use camino::{Utf8Path, Utf8PathBuf};
use serde::{Deserialize, Serialize};
use std::env;
use std::fs;
use std::path::PathBuf;

pub const RUNTIME_ROOT_MARKER_FILENAME: &str = "runtime-root.json";
pub const RUNTIME_ROOT_REF_FILENAME: &str = "runtime-root-ref.json";
pub const RUNTIME_ROOT_RECORD_TYPE: &str = "ccb_runtime_root";
pub const RUNTIME_ROOT_REF_RECORD_TYPE: &str = "ccb_runtime_root_ref";
pub const UNIX_SOCKET_SAFE_BYTES: usize = 100;
pub const TARGET_SEGMENT_PATTERN: &str = r"[^a-z0-9._-]+";

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum FilesystemHint {
    WslDrvfs,
    Normal,
}

impl FilesystemHint {
    pub fn as_str(&self) -> Option<&'static str> {
        match self {
            FilesystemHint::WslDrvfs => Some("wsl_drvfs"),
            FilesystemHint::Normal => None,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum RootKind {
    Project,
    Runtime,
    Relocated,
}

impl RootKind {
    pub fn as_str(&self) -> &'static str {
        match self {
            RootKind::Project => "project",
            RootKind::Runtime => "runtime",
            RootKind::Relocated => "relocated",
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct SocketPlacement {
    pub preferred_path: Utf8PathBuf,
    pub effective_path: Utf8PathBuf,
    pub root_kind: RootKind,
    pub fallback_reason: Option<String>,
    pub filesystem_hint: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct RuntimeStatePlacement {
    pub anchor_path: Utf8PathBuf,
    pub effective_path: Utf8PathBuf,
    pub root_kind: RootKind,
    pub relocation_reason: Option<String>,
    pub filesystem_hint: Option<String>,
}

impl RuntimeStatePlacement {
    pub fn is_project_scoped(&self) -> bool {
        matches!(self.root_kind, RootKind::Project)
    }
}

/// Sanitize a path segment to `[a-z0-9._-]+`, collapsing runs and trimming edges.
/// Mirrors Python `storage.path_helpers.normalized_segment`.
pub fn normalized_segment(value: &str, label: &str) -> crate::Result<String> {
    let mut out = String::new();
    let mut prev_dash = true; // treat leading as edge
    for ch in value.trim().to_lowercase().chars() {
        if ch.is_ascii_lowercase() || ch.is_ascii_digit() || ch == '.' || ch == '_' || ch == '-' {
            out.push(ch);
            prev_dash = false;
        } else if !prev_dash {
            out.push('-');
            prev_dash = true;
        }
    }
    // trim trailing dashes/dots
    let trimmed = out.trim_matches(&['-', '.'][..]);
    if trimmed.is_empty() {
        return Err(crate::StorageError::Corrupt(format!(
            "{} cannot be empty",
            label
        )));
    }
    Ok(trimmed.to_string())
}

const RESERVED_AGENT_NAMES: &[&str] = &[
    "all", "from", "user", "system", "ask", "cancel", "clear", "pend", "ping", "watch", "kill",
    "ps", "logs", "doctor", "config", "cmd", "version", "update", "help",
];

/// Normalize an agent name for use in path segments.
/// Mirrors Python `agents.models_runtime.names.normalize_agent_name`.
pub fn normalize_agent_name(name: &str) -> crate::Result<String> {
    let trimmed = name.trim();
    if trimmed.is_empty() {
        return Err(crate::StorageError::Corrupt(
            "agent name cannot be empty".into(),
        ));
    }
    if trimmed.len() > 32 {
        return Err(crate::StorageError::Corrupt(
            "agent name must be 32 characters or fewer".into(),
        ));
    }
    let mut chars = trimmed.chars();
    let first = chars.next().unwrap();
    if !first.is_ascii_alphabetic() {
        return Err(crate::StorageError::Corrupt(
            "agent name must start with a letter".into(),
        ));
    }
    if !chars.all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-') {
        return Err(crate::StorageError::Corrupt(
            "agent name must contain only letters, digits, underscores, or hyphens".into(),
        ));
    }
    let normalized = trimmed.to_lowercase();
    if RESERVED_AGENT_NAMES.contains(&normalized.as_str()) {
        return Err(crate::StorageError::Corrupt(format!(
            "agent name {:?} is reserved",
            normalized
        )));
    }
    Ok(normalized)
}

/// Normalize a mailbox owner name for use in path segments.
pub fn normalize_mailbox_owner_name(name: &str) -> crate::Result<String> {
    normalize_agent_name(name)
}

/// Return the target segment for a given target kind/name.
/// Mirrors Python `storage.path_helpers.target_segment`.
pub fn target_segment(target_kind: &str, target_name: &str) -> crate::Result<String> {
    let kind = target_kind.trim().to_lowercase();
    let raw_name = target_name.trim();
    if kind == "agent" {
        normalize_agent_name(raw_name)
    } else {
        normalized_segment(raw_name, "target_name")
    }
}

pub fn unix_socket_path_is_safe(path: &Utf8Path) -> bool {
    path.as_str().len() <= UNIX_SOCKET_SAFE_BYTES
}

pub fn is_wsl() -> bool {
    if env::var("WSL_INTEROP").is_ok() || env::var("WSL_DISTRO_NAME").is_ok() {
        return true;
    }
    if let Ok(contents) = fs::read_to_string("/proc/version") {
        return contents.to_lowercase().contains("microsoft");
    }
    false
}

pub fn socket_filesystem_hint(path: &Utf8Path) -> FilesystemHint {
    let normalized = path.as_str().replace('\\', "/");
    if is_wsl() && normalized.starts_with("/mnt/") {
        FilesystemHint::WslDrvfs
    } else {
        FilesystemHint::Normal
    }
}

pub fn pathname_unix_socket_supported(path: &Utf8Path) -> bool {
    !matches!(socket_filesystem_hint(path), FilesystemHint::WslDrvfs)
}

pub fn pathname_runtime_state_supported(path: &Utf8Path) -> bool {
    !matches!(socket_filesystem_hint(path), FilesystemHint::WslDrvfs)
}

pub fn runtime_socket_root_candidates() -> Vec<Utf8PathBuf> {
    let mut candidates: Vec<Utf8PathBuf> = Vec::new();
    if let Ok(xdg) = env::var("XDG_RUNTIME_DIR") {
        let trimmed = xdg.trim();
        if !trimmed.is_empty() {
            candidates.push(Utf8PathBuf::from(format!("{}/ccb-runtime", trimmed)));
        }
    }
    candidates.push(Utf8PathBuf::from("/tmp/ccb-runtime"));
    if let Ok(tmp) = env::var("TMPDIR") {
        if !tmp.trim().is_empty() {
            candidates.push(Utf8PathBuf::from(format!("{}/ccb-runtime", tmp.trim())));
        }
    }
    candidates.push(Utf8PathBuf::from(format!(
        "{}/ccb-runtime",
        std::env::temp_dir().display()
    )));

    let mut unique: Vec<Utf8PathBuf> = Vec::new();
    for candidate in candidates {
        if !unique.contains(&candidate) {
            unique.push(candidate);
        }
    }
    unique
}

pub fn runtime_socket_root() -> Utf8PathBuf {
    for candidate in runtime_socket_root_candidates() {
        if pathname_unix_socket_supported(&candidate) {
            return candidate;
        }
    }
    Utf8PathBuf::from("/tmp/ccb-runtime")
}

fn absolute_path_from_env(env_name: &str) -> Option<Utf8PathBuf> {
    absolute_path_from_value(env::var(env_name).unwrap_or_default().as_str())
}

fn absolute_path_from_value(raw: &str) -> Option<Utf8PathBuf> {
    let text = raw.trim();
    if text.is_empty() {
        return None;
    }
    let path = PathBuf::from(expand_user_path(text));
    if !path.is_absolute() {
        return None;
    }
    Utf8PathBuf::from_path_buf(path).ok()
}

fn expand_user_path(raw: &str) -> String {
    if let Some(rest) = raw.strip_prefix('~') {
        if let Ok(home) = env::var("HOME") {
            return home + rest;
        }
    }
    raw.to_string()
}

fn account_home_dir() -> Utf8PathBuf {
    env::var("HOME")
        .ok()
        .and_then(|h| Utf8PathBuf::from_path_buf(PathBuf::from(expand_user_path(&h))).ok())
        .unwrap_or_else(|| Utf8PathBuf::from("/tmp"))
}

pub fn runtime_state_root_candidates() -> Vec<Utf8PathBuf> {
    let mut candidates: Vec<Utf8PathBuf> = Vec::new();
    if let Some(path) = absolute_path_from_env("CCB_RUNTIME_STATE_HOME") {
        candidates.push(path);
    }
    if let Some(xdg_state) =
        absolute_path_from_value(&env::var("XDG_STATE_HOME").unwrap_or_default())
    {
        candidates.push(xdg_state.join("ccb/projects"));
    }
    candidates.push(account_home_dir().join(".local/state/ccb/projects"));

    let mut unique: Vec<Utf8PathBuf> = Vec::new();
    for candidate in candidates {
        if !unique.contains(&candidate) {
            unique.push(candidate);
        }
    }
    unique
}

pub fn runtime_state_base_root() -> Utf8PathBuf {
    for candidate in runtime_state_root_candidates() {
        if pathname_runtime_state_supported(&candidate) {
            return candidate;
        }
    }
    account_home_dir().join(".local/state/ccb/projects")
}

pub fn runtime_state_root_for_project(project_id: &str) -> Utf8PathBuf {
    let normalized = project_id.trim();
    if normalized.is_empty() {
        panic!("project_id cannot be empty");
    }
    runtime_state_base_root().join(normalized)
}

pub fn choose_runtime_state_placement(
    _project_root: &Utf8Path,
    project_id: &str,
    anchor_path: &Utf8Path,
) -> RuntimeStatePlacement {
    let anchor = Utf8PathBuf::from(expand_user_path(anchor_path.as_str()));
    let hint = socket_filesystem_hint(&anchor);
    if let Some(ref_root) = runtime_state_root_from_anchor_ref(&anchor, Some(project_id)) {
        return RuntimeStatePlacement {
            anchor_path: anchor.clone(),
            effective_path: ref_root,
            root_kind: RootKind::Relocated,
            relocation_reason: Some("runtime_root_ref".into()),
            filesystem_hint: hint.as_str().map(|s| s.to_string()),
        };
    }
    if matches!(hint, FilesystemHint::WslDrvfs) {
        return RuntimeStatePlacement {
            anchor_path: anchor.clone(),
            effective_path: runtime_state_root_for_project(project_id),
            root_kind: RootKind::Relocated,
            relocation_reason: Some("wsl_drvfs".into()),
            filesystem_hint: hint.as_str().map(|s| s.to_string()),
        };
    }
    RuntimeStatePlacement {
        anchor_path: anchor.clone(),
        effective_path: anchor.clone(),
        root_kind: RootKind::Project,
        relocation_reason: None,
        filesystem_hint: hint.as_str().map(|s| s.to_string()),
    }
}

fn runtime_socket_placement(
    preferred_path: &Utf8Path,
    project_socket_key: &str,
    fallback_reason: &str,
    filesystem_hint: Option<String>,
) -> SocketPlacement {
    let stem = preferred_path.file_stem().unwrap_or("sock");
    let effective_root = runtime_socket_root();
    SocketPlacement {
        preferred_path: preferred_path.to_path_buf(),
        effective_path: effective_root.join(format!("{}-{}.sock", stem, project_socket_key)),
        root_kind: RootKind::Runtime,
        fallback_reason: Some(fallback_reason.into()),
        filesystem_hint,
    }
}

pub fn choose_socket_placement(
    preferred_path: &Utf8Path,
    project_socket_key: &str,
    preferred_root_kind: RootKind,
) -> SocketPlacement {
    let preferred = Utf8PathBuf::from(expand_user_path(preferred_path.as_str()));
    if !unix_socket_path_is_safe(&preferred) {
        return runtime_socket_placement(
            &preferred,
            project_socket_key,
            "path_too_long",
            socket_filesystem_hint(&preferred)
                .as_str()
                .map(|s| s.to_string()),
        );
    }
    let hint = socket_filesystem_hint(&preferred);
    if !pathname_unix_socket_supported(&preferred) {
        return runtime_socket_placement(
            &preferred,
            project_socket_key,
            "unsupported_filesystem",
            hint.as_str().map(|s| s.to_string()),
        );
    }
    SocketPlacement {
        preferred_path: preferred.clone(),
        effective_path: preferred,
        root_kind: preferred_root_kind,
        fallback_reason: None,
        filesystem_hint: hint.as_str().map(|s| s.to_string()),
    }
}

pub fn socket_placement_payload(
    placement: &SocketPlacement,
    prefix: &str,
) -> serde_json::Map<String, serde_json::Value> {
    let mut map = serde_json::Map::new();
    let p = |key: &str| format!("{}{}", prefix, key);
    map.insert(
        p("preferred_socket_path"),
        placement.preferred_path.as_str().into(),
    );
    map.insert(
        p("effective_socket_path"),
        placement.effective_path.as_str().into(),
    );
    map.insert(p("socket_root_kind"), placement.root_kind.as_str().into());
    map.insert(
        p("socket_fallback_reason"),
        placement.fallback_reason.clone().into(),
    );
    map.insert(
        p("socket_filesystem_hint"),
        placement.filesystem_hint.clone().into(),
    );
    map
}

pub fn runtime_state_placement_payload(
    placement: &RuntimeStatePlacement,
) -> serde_json::Map<String, serde_json::Value> {
    let mut map = serde_json::Map::new();
    map.insert(
        "project_anchor_path".into(),
        placement.anchor_path.as_str().into(),
    );
    map.insert(
        "runtime_state_root".into(),
        placement.effective_path.as_str().into(),
    );
    map.insert(
        "runtime_root_kind".into(),
        placement.root_kind.as_str().into(),
    );
    map.insert(
        "runtime_relocation_reason".into(),
        placement.relocation_reason.clone().into(),
    );
    map.insert(
        "runtime_filesystem_hint".into(),
        placement.filesystem_hint.clone().into(),
    );
    map
}

pub fn runtime_root_marker_path(runtime_state_root: &Utf8Path) -> Utf8PathBuf {
    Utf8PathBuf::from(expand_user_path(runtime_state_root.as_str()))
        .join(RUNTIME_ROOT_MARKER_FILENAME)
}

pub fn runtime_root_ref_path(anchor_path: &Utf8Path) -> Utf8PathBuf {
    Utf8PathBuf::from(expand_user_path(anchor_path.as_str())).join(RUNTIME_ROOT_REF_FILENAME)
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct RuntimeRootRefPayload {
    pub schema_version: Option<i64>,
    pub record_type: Option<String>,
    pub project_id: Option<String>,
    pub runtime_state_root: Option<String>,
    pub created_at: Option<String>,
}

#[derive(Debug, Clone, Deserialize, Serialize)]
pub struct RuntimeRootMarkerPayload {
    pub schema_version: Option<i64>,
    pub record_type: Option<String>,
    pub project_id: Option<String>,
    pub project_root: Option<String>,
    pub anchor_path: Option<String>,
    pub runtime_root_path: Option<String>,
    pub created_at: Option<String>,
}

pub fn read_runtime_root_ref_payload(
    anchor_path: &Utf8Path,
    project_id: Option<&str>,
) -> Option<serde_json::Map<String, serde_json::Value>> {
    let path = runtime_root_ref_path(anchor_path);
    let mut payload = read_json_object(&path)?;
    if payload.get("record_type").and_then(|v| v.as_str()) != Some(RUNTIME_ROOT_REF_RECORD_TYPE) {
        return None;
    }
    let recorded_project_id = payload
        .get("project_id")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim();
    if recorded_project_id.is_empty() {
        return None;
    }
    if let Some(expected) = project_id {
        if recorded_project_id != expected.trim() {
            return None;
        }
    }
    let root = payload
        .get("runtime_state_root")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim();
    absolute_path_from_value(root)?;
    let recorded_project_id = recorded_project_id.to_string();
    let root = root.to_string();
    payload.insert("project_id".into(), recorded_project_id.into());
    payload.insert("runtime_state_root".into(), root.into());
    Some(payload)
}

pub fn runtime_state_root_from_anchor_ref(
    anchor_path: &Utf8Path,
    project_id: Option<&str>,
) -> Option<Utf8PathBuf> {
    let payload = read_runtime_root_ref_payload(anchor_path, project_id)?;
    let root = payload.get("runtime_state_root").and_then(|v| v.as_str())?;
    Some(Utf8PathBuf::from(expand_user_path(root)))
}

/// Return the runtime state root for an anchor, falling back to the anchor itself.
/// Mirrors Python `storage.path_helpers.runtime_state_root_from_anchor`.
pub fn runtime_state_root_from_anchor(
    anchor_path: &Utf8Path,
    project_id: Option<&str>,
) -> Utf8PathBuf {
    runtime_state_root_from_anchor_ref(anchor_path, project_id)
        .unwrap_or_else(|| Utf8PathBuf::from(expand_user_path(anchor_path.as_str())))
}

pub fn find_runtime_root_marker_path(path: &Utf8Path) -> Option<Utf8PathBuf> {
    let current = Utf8PathBuf::from(expand_user_path(path.as_str()));
    let mut candidates: Vec<Utf8PathBuf> = vec![current.clone()];
    let mut parent = current.parent().map(|p| p.to_path_buf());
    while let Some(p) = parent {
        candidates.push(p.clone());
        parent = p.parent().map(|p| p.to_path_buf());
    }
    for candidate in candidates {
        let marker = candidate.join(RUNTIME_ROOT_MARKER_FILENAME);
        if marker.is_file() {
            return Some(marker);
        }
    }
    None
}

pub fn read_runtime_root_marker_payload(
    marker_path: &Utf8Path,
) -> Option<serde_json::Map<String, serde_json::Value>> {
    let path = Utf8PathBuf::from(expand_user_path(marker_path.as_str()));
    let mut payload = read_json_object(&path)?;
    if payload.get("record_type").and_then(|v| v.as_str()) != Some(RUNTIME_ROOT_RECORD_TYPE) {
        return None;
    }
    let project_id = payload
        .get("project_id")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .trim();
    if project_id.is_empty() {
        return None;
    }
    let runtime_root = absolute_path_from_value(
        payload
            .get("runtime_root_path")
            .and_then(|v| v.as_str())
            .unwrap_or(""),
    )?;
    if runtime_root != path.parent()? {
        return None;
    }
    let project_root = absolute_path_from_value(
        payload
            .get("project_root")
            .and_then(|v| v.as_str())
            .unwrap_or(""),
    )?;
    let anchor_path = absolute_path_from_value(
        payload
            .get("anchor_path")
            .and_then(|v| v.as_str())
            .unwrap_or(""),
    )?;
    if anchor_path.file_name() != Some(".ccb") {
        return None;
    }
    if anchor_path != project_root.join(".ccb") {
        return None;
    }
    payload.insert("project_id".into(), project_id.into());
    payload.insert("project_root".into(), project_root.as_str().into());
    payload.insert("anchor_path".into(), anchor_path.as_str().into());
    payload.insert("runtime_root_path".into(), runtime_root.as_str().into());
    Some(payload)
}

pub fn runtime_project_anchor_from_path(path: &Utf8Path) -> Option<Utf8PathBuf> {
    let marker_path = find_runtime_root_marker_path(path)?;
    let payload = read_runtime_root_marker_payload(&marker_path)?;
    let anchor = payload.get("anchor_path").and_then(|v| v.as_str())?;
    Some(Utf8PathBuf::from(expand_user_path(anchor)))
}

pub fn runtime_project_root_from_path(path: &Utf8Path) -> Option<Utf8PathBuf> {
    let marker_path = find_runtime_root_marker_path(path)?;
    let payload = read_runtime_root_marker_payload(&marker_path)?;
    let project_root = payload.get("project_root").and_then(|v| v.as_str())?;
    Some(Utf8PathBuf::from(expand_user_path(project_root)))
}

fn read_json_object(path: &Utf8Path) -> Option<serde_json::Map<String, serde_json::Value>> {
    let data = fs::read_to_string(path).ok()?;
    let value: serde_json::Value = serde_json::from_str(&data).ok()?;
    value.as_object().cloned()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_normalized_segment_basic() {
        assert_eq!(
            normalized_segment("Hello World!", "label").unwrap(),
            "hello-world"
        );
        assert_eq!(
            normalized_segment("--foo..bar__", "label").unwrap(),
            "foo..bar__"
        );
    }

    #[test]
    fn test_target_segment_agent() {
        assert_eq!(target_segment("agent", "Agent1").unwrap(), "agent1");
    }

    #[test]
    fn test_runtime_socket_root_candidates_not_empty() {
        assert!(!runtime_socket_root_candidates().is_empty());
    }

    #[test]
    fn test_target_segment_pattern_constant() {
        assert!(!TARGET_SEGMENT_PATTERN.is_empty());
    }

    #[test]
    fn test_runtime_state_root_from_anchor_falls_back() {
        let anchor = Utf8Path::new("/tmp/repo/.ccb");
        assert_eq!(
            runtime_state_root_from_anchor(anchor, Some("proj-1")),
            Utf8PathBuf::from("/tmp/repo/.ccb")
        );
    }
}
