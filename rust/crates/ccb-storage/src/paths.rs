use camino::{Utf8Path, Utf8PathBuf};
use serde::{Deserialize, Serialize};
use std::fs;
use std::path::PathBuf;

use crate::atomic::atomic_write_json;
use crate::path_helpers::{
    choose_runtime_state_placement, read_runtime_root_marker_payload,
    read_runtime_root_ref_payload, runtime_root_marker_path, runtime_root_ref_path,
    runtime_state_placement_payload, RootKind, RuntimeStatePlacement,
};
use crate::project_identity::{compute_project_id, project_slug};

const SHARED_CACHE_PROVIDERS: &[&str] = &["claude", "codex", "gemini"];
const EXTERNAL_CACHE_PROVIDERS: &[&str] = &["claude", "gemini"];

/// Project-level path layout for a CCB project.
/// Mirrors Python `storage.paths.PathLayout`.
#[derive(Debug, Clone)]
pub struct PathLayout {
    pub project_root: Utf8PathBuf,
    project_id: String,
    pub(crate) runtime_state_placement: RuntimeStatePlacement,
    pub(crate) runtime_state_root: Utf8PathBuf,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct RuntimeStatePayload {
    pub project_root: String,
    pub project_slug: String,
    pub project_id: String,
    pub created_at: String,
}

impl PathLayout {
    pub fn new(project_root: impl Into<Utf8PathBuf>) -> Self {
        let mut root = project_root.into();
        // Try to resolve; fall back to absolute like Python.
        root = if let Ok(resolved) = PathBuf::from(root.as_str()).canonicalize() {
            Utf8PathBuf::from_path_buf(resolved).unwrap_or(root)
        } else if let Ok(absolute) = std::path::absolute(PathBuf::from(root.as_str())) {
            Utf8PathBuf::from_path_buf(absolute).unwrap_or(root)
        } else {
            root
        };

        let project_id = compute_project_id(root.as_str());
        let placement = choose_runtime_state_placement(&root, &project_id, &root.join(".ccb"));
        let state_root = placement.effective_path.clone();

        Self {
            project_root: root,
            project_id,
            runtime_state_placement: placement,
            runtime_state_root: state_root,
        }
    }

    pub fn project_slug(&self) -> String {
        project_slug(self.project_root.as_str())
    }

    pub fn project_id(&self) -> &str {
        &self.project_id
    }

    pub fn project_socket_key(&self) -> String {
        self.project_id[..12].to_string()
    }

    pub fn runtime_state_placement(&self) -> &RuntimeStatePlacement {
        &self.runtime_state_placement
    }

    pub fn runtime_state_root(&self) -> &Utf8Path {
        &self.runtime_state_root
    }

    // --- Memory paths ---

    pub fn project_memory_path(&self) -> Utf8PathBuf {
        self.ccb_dir().join("ccb_memory.md")
    }

    pub fn memory_seed_path(&self) -> Utf8PathBuf {
        self.runtime_state_root.join("state/memory.seed.json")
    }

    pub fn runtime_memory_dir(&self) -> Utf8PathBuf {
        self.runtime_state_root.join("runtime/memory")
    }

    pub fn runtime_memory_bundle_path(&self, agent_name: &str) -> Utf8PathBuf {
        let normalized = crate::path_helpers::normalize_agent_name(agent_name)
            .unwrap_or_else(|_| agent_name.to_lowercase());
        self.runtime_memory_dir().join(format!("{}.md", normalized))
    }

    // --- Shared cache ---

    pub fn shared_cache_dir(&self) -> Utf8PathBuf {
        self.runtime_state_root.join("shared-cache")
    }

    pub fn provider_shared_cache_dir(&self, provider: &str) -> crate::Result<Utf8PathBuf> {
        let normalized = crate::path_helpers::normalized_segment(provider, "provider")?;
        let original = provider.trim().to_lowercase();
        if normalized != original || !SHARED_CACHE_PROVIDERS.contains(&normalized.as_str()) {
            return Err(crate::StorageError::Corrupt(format!(
                "provider must be one of: {}",
                SHARED_CACHE_PROVIDERS.join(", ")
            )));
        }
        Ok(self.shared_cache_dir().join(normalized))
    }

    pub fn ensure_provider_shared_cache_dir(
        &self,
        provider: &str,
        created_at: Option<&str>,
    ) -> crate::Result<Utf8PathBuf> {
        let placement = self.runtime_state_placement();
        if placement.filesystem_hint.as_deref() == Some("wsl_drvfs")
            && !matches!(placement.root_kind, RootKind::Relocated)
        {
            return Err(crate::StorageError::Corrupt(
                "shared cache requires relocated runtime state for WSL drvfs project anchors"
                    .into(),
            ));
        }
        let cache_dir = self.provider_shared_cache_dir(provider)?;
        let timestamp = created_at.map(|s| s.to_string()).unwrap_or_else(utc_now);
        self.ensure_runtime_state_root(Some(&timestamp))?;
        fs::create_dir_all(&cache_dir)?;
        let manifest_path = cache_dir.join("MANIFEST.json");
        if !manifest_path.exists() {
            atomic_write_json(
                &manifest_path,
                &serde_json::json!({
                    "schema_version": 1,
                    "record_type": "ccb_shared_cache_manifest",
                    "provider": cache_dir.file_name().unwrap_or("unknown"),
                    "project_id": self.project_id,
                    "runtime_state_root": self.runtime_state_root.as_str(),
                    "created_at": timestamp,
                    "entries": [],
                }),
            )?;
        }
        Ok(cache_dir)
    }

    pub fn external_provider_cache_root(&self) -> Utf8PathBuf {
        let root = user_cache_home();
        root.join("ccb/projects")
            .join(&self.project_id[..16])
            .join("provider-cache")
    }

    pub fn provider_external_cache_dir(&self, provider: &str) -> crate::Result<Utf8PathBuf> {
        let normalized = crate::path_helpers::normalized_segment(provider, "provider")?;
        let original = provider.trim().to_lowercase();
        if normalized != original || !EXTERNAL_CACHE_PROVIDERS.contains(&normalized.as_str()) {
            return Err(crate::StorageError::Corrupt(format!(
                "provider must be one of: {}",
                EXTERNAL_CACHE_PROVIDERS.join(", ")
            )));
        }
        Ok(self.external_provider_cache_root().join(normalized))
    }

    pub fn ensure_provider_external_cache_dir(
        &self,
        provider: &str,
        created_at: Option<&str>,
    ) -> crate::Result<Utf8PathBuf> {
        let cache_dir = self.provider_external_cache_dir(provider)?;
        let timestamp = created_at.map(|s| s.to_string()).unwrap_or_else(utc_now);
        fs::create_dir_all(&cache_dir)?;
        let manifest_path = cache_dir.join("MANIFEST.json");
        if !manifest_path.exists() {
            atomic_write_json(
                &manifest_path,
                &serde_json::json!({
                    "schema_version": 1,
                    "record_type": "ccb_external_provider_cache_manifest",
                    "provider": cache_dir.file_name().unwrap_or("unknown"),
                    "project_id": self.project_id,
                    "project_root": self.project_root.as_str(),
                    "created_at": timestamp,
                    "entries": [],
                }),
            )?;
        }
        Ok(cache_dir)
    }

    // --- Runtime root marker / ref ---

    pub fn runtime_root_marker_path(&self) -> Utf8PathBuf {
        runtime_root_marker_path(&self.runtime_state_root)
    }

    pub fn runtime_root_ref_path(&self) -> Utf8PathBuf {
        runtime_root_ref_path(&self.ccb_dir())
    }

    pub fn runtime_marker_status(&self) -> String {
        if self.runtime_state_placement.is_project_scoped() {
            return "not_required".into();
        }
        match self.validate_runtime_root_marker(false) {
            Ok(()) => match self.validate_runtime_root_ref(true) {
                Ok(()) => "ok".into(),
                Err(_) => "mismatch".into(),
            },
            Err(crate::StorageError::NotFound(_)) => "missing".into(),
            Err(_) => "mismatch".into(),
        }
    }

    pub fn ensure_runtime_state_root(&self, created_at: Option<&str>) -> std::io::Result<()> {
        if self.runtime_state_placement.is_project_scoped() {
            return Ok(());
        }
        fs::create_dir_all(self.ccb_dir())?;
        fs::create_dir_all(&self.runtime_state_root)?;
        let timestamp = created_at.map(|s| s.to_string()).unwrap_or_else(utc_now);
        if let Err(e) = self.validate_runtime_root_marker(true) {
            if !e.to_string().contains("No such file") {
                return Err(std::io::Error::other(e));
            }
        }
        if let Err(e) = self.validate_runtime_root_ref(true) {
            if !e.to_string().contains("No such file") {
                return Err(std::io::Error::other(e));
            }
        }
        atomic_write_json(
            &self.runtime_root_marker_path(),
            &self.runtime_root_marker_payload(&timestamp),
        )
        .map_err(std::io::Error::other)?;
        atomic_write_json(
            &self.runtime_root_ref_path(),
            &self.runtime_root_ref_payload(&timestamp),
        )
        .map_err(std::io::Error::other)?;
        Ok(())
    }

    pub fn runtime_state_payload(&self) -> serde_json::Map<String, serde_json::Value> {
        let mut payload = runtime_state_placement_payload(&self.runtime_state_placement);
        payload.insert(
            "runtime_marker_status".into(),
            self.runtime_marker_status().into(),
        );
        payload.insert(
            "runtime_root_marker_path".into(),
            self.runtime_root_marker_path().as_str().into(),
        );
        payload.insert(
            "runtime_root_ref_path".into(),
            self.runtime_root_ref_path().as_str().into(),
        );
        payload
    }

    fn runtime_root_marker_payload(&self, created_at: &str) -> serde_json::Value {
        serde_json::json!({
            "schema_version": 1,
            "record_type": "ccb_runtime_root",
            "project_id": self.project_id,
            "project_root": self.project_root.as_str(),
            "anchor_path": self.ccb_dir().as_str(),
            "runtime_root_path": self.runtime_state_root.as_str(),
            "created_at": created_at,
        })
    }

    fn runtime_root_ref_payload(&self, created_at: &str) -> serde_json::Value {
        serde_json::json!({
            "schema_version": 1,
            "record_type": "ccb_runtime_root_ref",
            "project_id": self.project_id,
            "runtime_state_root": self.runtime_state_root.as_str(),
            "created_at": created_at,
        })
    }

    fn validate_runtime_root_marker(&self, allow_missing: bool) -> crate::Result<()> {
        let payload = read_runtime_root_marker_payload(&self.runtime_root_marker_path());
        if payload.is_none() {
            if allow_missing && !self.runtime_root_marker_path().exists() {
                return Ok(());
            }
            if !self.runtime_root_marker_path().exists() {
                return Err(crate::StorageError::NotFound(
                    self.runtime_root_marker_path().to_string(),
                ));
            }
            return Err(crate::StorageError::Corrupt(format!(
                "{} is invalid",
                self.runtime_root_marker_path()
            )));
        }
        let payload = payload.unwrap();
        let ccb_dir = self.ccb_dir();
        let expected = [
            ("project_id", self.project_id.as_str()),
            ("project_root", self.project_root.as_str()),
            ("anchor_path", ccb_dir.as_str()),
            ("runtime_root_path", self.runtime_state_root.as_str()),
        ];
        for (key, value) in expected {
            let recorded = payload
                .get(key)
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .trim();
            if recorded != value {
                return Err(crate::StorageError::Corrupt(format!(
                    "{} field {} mismatch: expected {}, found {}",
                    self.runtime_root_marker_path(),
                    key,
                    value,
                    if recorded.is_empty() {
                        "<missing>"
                    } else {
                        recorded
                    }
                )));
            }
        }
        Ok(())
    }

    fn validate_runtime_root_ref(&self, allow_missing: bool) -> crate::Result<()> {
        let payload = read_runtime_root_ref_payload(&self.ccb_dir(), Some(&self.project_id));
        if payload.is_none() {
            if allow_missing && !self.runtime_root_ref_path().exists() {
                return Ok(());
            }
            if !self.runtime_root_ref_path().exists() {
                return Err(crate::StorageError::NotFound(
                    self.runtime_root_ref_path().to_string(),
                ));
            }
            return Err(crate::StorageError::Corrupt(format!(
                "{} is invalid",
                self.runtime_root_ref_path()
            )));
        }
        let payload = payload.unwrap();
        let expected = [
            ("project_id", self.project_id.as_str()),
            ("runtime_state_root", self.runtime_state_root.as_str()),
        ];
        for (key, value) in expected {
            let recorded = payload
                .get(key)
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .trim();
            if recorded != value {
                return Err(crate::StorageError::Corrupt(format!(
                    "{} field {} mismatch: expected {}, found {}",
                    self.runtime_root_ref_path(),
                    key,
                    value,
                    if recorded.is_empty() {
                        "<missing>"
                    } else {
                        recorded
                    }
                )));
            }
        }
        Ok(())
    }
}

pub(crate) fn tmux_safe_name(value: &str, fallback: &str) -> String {
    let sanitized: String = value
        .trim()
        .chars()
        .map(|ch| {
            if ch.is_ascii_alphanumeric() || ch == '_' || ch == '-' {
                ch
            } else {
                '_'
            }
        })
        .collect();
    let sanitized = sanitized.trim_matches(&['_', '-'][..]);
    if sanitized.is_empty() {
        fallback.to_string()
    } else {
        sanitized.to_string()
    }
}

pub(crate) fn user_cache_home() -> Utf8PathBuf {
    if let Ok(raw) = std::env::var("XDG_CACHE_HOME") {
        let trimmed = raw.trim();
        if !trimmed.is_empty() {
            if let Ok(path) = Utf8PathBuf::from_path_buf(PathBuf::from(expand_user_path(trimmed))) {
                return path;
            }
        }
    }
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".into());
    Utf8PathBuf::from(format!("{}/.cache", expand_user_path(&home)))
}

pub(crate) fn expand_user_path(raw: &str) -> String {
    if let Some(rest) = raw.strip_prefix('~') {
        if let Ok(home) = std::env::var("HOME") {
            return home + rest;
        }
    }
    raw.to_string()
}

pub(crate) fn utc_now() -> String {
    chrono::Utc::now()
        .to_rfc3339_opts(chrono::SecondsFormat::Secs, true)
        .replace("+00:00", "Z")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_project_slug() {
        let layout = PathLayout::new("/home/user/my-project");
        assert!(layout.project_slug().starts_with("my-project-"));
    }

    #[test]
    fn test_project_id_deterministic() {
        let a = PathLayout::new("/home/user/project");
        let b = PathLayout::new("/home/user/project");
        assert_eq!(a.project_id, b.project_id);
    }

    #[test]
    fn test_socket_key_length() {
        let layout = PathLayout::new("/home/user/project");
        assert_eq!(layout.project_socket_key().len(), 12);
    }

    #[test]
    fn test_provider_shared_cache_dir() {
        let layout = PathLayout::new("/project");
        assert_eq!(
            layout.provider_shared_cache_dir("claude").unwrap(),
            Utf8PathBuf::from("/project/.ccb/shared-cache/claude")
        );
    }

    #[test]
    fn test_rejects_noncanonical_shared_cache_provider() {
        let layout = PathLayout::new("/project");
        assert!(layout.provider_shared_cache_dir("Claude Code").is_err());
    }
}
