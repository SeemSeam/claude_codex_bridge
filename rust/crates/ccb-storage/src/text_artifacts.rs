use camino::{Utf8Path, Utf8PathBuf};
use serde::{Deserialize, Serialize};
use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::PathBuf;
use uuid::Uuid;

use crate::atomic::atomic_write_text;
use crate::paths::PathLayout;

pub const TEXT_ARTIFACT_SPILL_BYTES: usize = 4 * 1024;
pub const TEXT_ARTIFACT_PREVIEW_CHARS: usize = 1200;
pub const TEXT_ARTIFACT_TTL_S: i64 = 24 * 60 * 60;

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct TextArtifact {
    pub schema_version: i32,
    pub kind: String,
    pub artifact_id: String,
    pub path: String,
    pub bytes: usize,
    pub sha256: String,
    pub encoding: String,
    pub preview: String,
    pub created_at: String,
    pub expires_at: String,
}

impl TextArtifact {
    pub fn to_record(&self) -> serde_json::Map<String, serde_json::Value> {
        serde_json::to_value(self)
            .unwrap_or_default()
            .as_object()
            .cloned()
            .unwrap_or_default()
    }
}

pub fn utf8_size(text: &str) -> usize {
    text.len()
}

pub fn should_spill_text(text: &str, threshold_bytes: usize) -> bool {
    utf8_size(text) > threshold_bytes
}

#[allow(clippy::too_many_arguments)]
pub fn maybe_spill_text(
    layout: &PathLayout,
    text: &str,
    kind: &str,
    owner_id: &str,
    prefix: &str,
    threshold_bytes: Option<usize>,
    ttl_seconds: Option<i64>,
    now: Option<&str>,
) -> crate::Result<(String, Option<TextArtifact>)> {
    let body = text;
    if !should_spill_text(body, threshold_bytes.unwrap_or(TEXT_ARTIFACT_SPILL_BYTES)) {
        return Ok((body.to_string(), None));
    }
    let artifact = write_text_artifact(layout, body, kind, owner_id, ttl_seconds, now)?;
    Ok((artifact_stub(prefix, &artifact, true), Some(artifact)))
}

pub fn write_text_artifact(
    layout: &PathLayout,
    text: &str,
    kind: &str,
    owner_id: &str,
    ttl_seconds: Option<i64>,
    now: Option<&str>,
) -> crate::Result<TextArtifact> {
    let body = text;
    let data = body.as_bytes();
    let digest = sha256_hex(data);
    let timestamp = now.map(|s| s.to_string()).unwrap_or_else(utc_now);
    layout.ensure_runtime_state_root(Some(&timestamp))?;
    let artifact_id = format!("art_{}", &Uuid::new_v4().as_simple().to_string()[..16]);
    let safe_kind = safe_segment(kind, "text");
    let safe_owner = safe_segment(owner_id, "unknown");
    let directory = layout.ccbd_text_artifacts_dir().join(&safe_kind);
    fs::create_dir_all(&directory)?;
    let path = directory.join(format!("{}-{}.txt", safe_owner, artifact_id));
    atomic_write_text(&path, body)?;
    if let Err(_e) = fs::set_permissions(&path, std::fs::Permissions::from_mode(0o600)) {
        // Ignore permission errors, mirroring Python.
    }
    Ok(TextArtifact {
        schema_version: 1,
        kind: safe_kind,
        artifact_id,
        path: path.to_string(),
        bytes: data.len(),
        sha256: digest,
        encoding: "utf-8".into(),
        preview: preview_text(body, TEXT_ARTIFACT_PREVIEW_CHARS),
        created_at: timestamp.clone(),
        expires_at: expires_at(&timestamp, ttl_seconds.unwrap_or(TEXT_ARTIFACT_TTL_S)),
    })
}

pub fn artifact_stub(prefix: &str, artifact: &TextArtifact, include_preview: bool) -> String {
    let preview = artifact.preview.trim_end();
    let show_preview = include_preview && !preview.is_empty();
    let mut lines = vec![
        prefix.trim_end().to_string(),
        format!("Full text: {}", artifact.path),
        format!("Bytes: {}", artifact.bytes),
        format!("SHA256: {}", artifact.sha256),
    ];
    if show_preview {
        lines.push(String::new());
        lines.push("Preview:".into());
        lines.push(preview.to_string());
    }
    let instruction = if show_preview {
        "Instruction: read the full text file above before acting when the preview is insufficient."
    } else {
        "Instruction: read the full text file above before acting."
    };
    lines.push(String::new());
    lines.push(instruction.into());
    lines.join("\n").trim_end().to_string()
}

pub fn preview_text(text: &str, max_chars: usize) -> String {
    let body = text.trim();
    if body.len() <= max_chars {
        return body.to_string();
    }
    format!("{}\n...[truncated]", body[..max_chars].trim_end())
}

pub fn validate_text_artifact_ref(
    layout: &PathLayout,
    artifact: Option<&TextArtifact>,
) -> crate::Result<Option<TextArtifact>> {
    let artifact = match artifact {
        Some(a) => a,
        None => return Ok(None),
    };
    let mut ref_record = artifact.to_record();
    let path = validated_artifact_path(layout, &artifact.path)?;
    let data = fs::read(&path)?;
    if let Some(expected_size) = ref_record.get("bytes").and_then(|v| v.as_u64()) {
        if expected_size as usize != data.len() {
            return Err(crate::StorageError::Corrupt(
                "text artifact byte size mismatch".into(),
            ));
        }
    }
    let expected_sha = ref_record
        .get("sha256")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim();
    let actual_sha = sha256_hex(&data);
    if !expected_sha.is_empty() && expected_sha != actual_sha {
        return Err(crate::StorageError::Corrupt(
            "text artifact sha256 mismatch".into(),
        ));
    }
    ref_record.insert("path".into(), path.to_string_lossy().to_string().into());
    ref_record.insert("bytes".into(), data.len().into());
    ref_record.insert("sha256".into(), actual_sha.into());
    ref_record
        .entry("encoding")
        .or_insert_with(|| "utf-8".into());
    let preview = preview_text(
        &String::from_utf8_lossy(&data).replace('\u{FFFD}', "?"),
        TEXT_ARTIFACT_PREVIEW_CHARS,
    );
    ref_record
        .entry("preview")
        .or_insert_with(|| preview.into());
    let value = serde_json::Value::Object(ref_record);
    let artifact: TextArtifact = serde_json::from_value(value)?;
    Ok(Some(artifact))
}

pub fn read_text_artifact(layout: &PathLayout, artifact: &TextArtifact) -> crate::Result<String> {
    let ref_record =
        validate_text_artifact_ref(layout, Some(artifact))?.unwrap_or_else(|| artifact.clone());
    let encoding = if ref_record.encoding == "utf-8" {
        None
    } else {
        Some(ref_record.encoding.clone())
    };
    let path = PathBuf::from(&ref_record.path);
    let data = fs::read(&path)?;
    if encoding.as_deref() == Some("utf-8") || encoding.is_none() {
        Ok(String::from_utf8_lossy(&data).to_string())
    } else {
        // Only utf-8 is supported in practice; fall back to lossy utf-8.
        Ok(String::from_utf8_lossy(&data).to_string())
    }
}

pub fn sweep_expired_text_artifacts(
    layout: &PathLayout,
    now: Option<&str>,
) -> crate::Result<Vec<Utf8PathBuf>> {
    let root = layout.ccbd_text_artifacts_dir();
    if !root.exists() {
        return Ok(Vec::new());
    }
    let current = parse_utc(now.unwrap_or(&utc_now()));
    let mut removed = Vec::new();
    for entry in walk_txt_files(&root)? {
        let path = entry?;
        let metadata = match fs::metadata(&path) {
            Ok(m) => m,
            Err(_) => continue,
        };
        let mtime = metadata.modified().ok();
        let age_s = mtime
            .map(|t| {
                current
                    .signed_duration_since(chrono::DateTime::<chrono::Utc>::from(t))
                    .num_seconds()
            })
            .unwrap_or(0)
            .max(0);
        if age_s < TEXT_ARTIFACT_TTL_S {
            continue;
        }
        match fs::remove_file(&path) {
            Ok(()) => removed.push(path),
            Err(_) => continue,
        }
    }
    Ok(removed)
}

fn walk_txt_files(root: &Utf8Path) -> std::io::Result<Vec<std::io::Result<Utf8PathBuf>>> {
    let mut results = Vec::new();
    let mut stack = vec![root.to_path_buf()];
    while let Some(dir) = stack.pop() {
        for entry in fs::read_dir(&dir)? {
            let entry = entry?;
            let path = entry.path();
            let metadata = entry.metadata()?;
            if metadata.is_dir() {
                if let Ok(p) = Utf8PathBuf::from_path_buf(path.clone()) {
                    stack.push(p);
                }
            } else if metadata.is_file() {
                if let Some(name) = path.file_name().and_then(|s| s.to_str()) {
                    if name.ends_with(".txt") {
                        if let Ok(p) = Utf8PathBuf::from_path_buf(path) {
                            results.push(Ok(p));
                        }
                    }
                }
            }
        }
    }
    Ok(results)
}

fn validated_artifact_path(layout: &PathLayout, value: &str) -> crate::Result<PathBuf> {
    let root = layout.ccbd_text_artifacts_dir();
    let root = std::fs::canonicalize(&root).unwrap_or_else(|_| PathBuf::from(root.as_str()));
    let path = PathBuf::from(expand_user_path(value));
    let resolved = std::fs::canonicalize(&path)
        .map_err(|_| crate::StorageError::Corrupt("text artifact path does not exist".into()))?;
    if !resolved.starts_with(&root) {
        return Err(crate::StorageError::Corrupt(
            "text artifact path escapes CCB artifact directory".into(),
        ));
    }
    Ok(resolved)
}

fn safe_segment(value: &str, fallback: &str) -> String {
    let mut out = String::new();
    for ch in value.trim().chars() {
        if ch.is_alphanumeric() || ch == '-' || ch == '_' {
            out.push(ch);
        } else {
            out.push('-');
        }
    }
    let out = out.trim_matches(&['-', '_'][..]).to_lowercase();
    if out.is_empty() {
        fallback.to_string()
    } else {
        out
    }
}

fn sha256_hex(input: impl AsRef<[u8]>) -> String {
    use sha2::{Digest, Sha256};
    let mut hasher = Sha256::new();
    hasher.update(input);
    hex::encode(hasher.finalize())
}

fn utc_now() -> String {
    chrono::Utc::now()
        .to_rfc3339_opts(chrono::SecondsFormat::Secs, true)
        .replace("+00:00", "Z")
}

fn expires_at(created_at: &str, ttl_seconds: i64) -> String {
    let base = parse_utc(created_at);
    (base + chrono::TimeDelta::seconds(ttl_seconds.max(0)))
        .to_rfc3339_opts(chrono::SecondsFormat::Secs, true)
        .replace("+00:00", "Z")
}

fn parse_utc(value: &str) -> chrono::DateTime<chrono::Utc> {
    let text = value.trim();
    let text = if let Some(rest) = text.strip_suffix('Z') {
        format!("{}+00:00", rest)
    } else {
        text.to_string()
    };
    chrono::DateTime::parse_from_rfc3339(&text)
        .map(|dt| dt.with_timezone(&chrono::Utc))
        .unwrap_or_else(|_| chrono::Utc::now())
}

fn expand_user_path(raw: &str) -> String {
    if let Some(rest) = raw.strip_prefix('~') {
        if let Ok(home) = std::env::var("HOME") {
            return home + rest;
        }
    }
    raw.to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_should_spill_text() {
        assert!(!should_spill_text("short", TEXT_ARTIFACT_SPILL_BYTES));
        assert!(should_spill_text(
            &"x".repeat(5000),
            TEXT_ARTIFACT_SPILL_BYTES
        ));
    }

    #[test]
    fn test_preview_text() {
        assert_eq!(preview_text("hello", 10), "hello");
        assert!(preview_text(&"x".repeat(2000), 10).ends_with("...[truncated]"));
    }

    #[test]
    fn test_write_and_read_text_artifact() {
        let layout = PathLayout::new("/tmp/ccb-test-artifact-repo");
        let text = "x".repeat(5000);
        let artifact = write_text_artifact(
            &layout,
            &text,
            "ask-request",
            "agent1",
            None,
            Some("2026-05-22T00:00:00Z"),
        )
        .unwrap();
        assert!(artifact.path.ends_with(".txt"));
        assert_eq!(read_text_artifact(&layout, &artifact).unwrap(), text);
    }
}
