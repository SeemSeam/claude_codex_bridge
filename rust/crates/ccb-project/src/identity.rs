use camino::{Utf8Path, Utf8PathBuf};

use crate::path_utils::{
    expand_user_path, is_win_drive_path, normalize_mnt_drive_mapping, normalize_msys_drive_mapping,
    normalize_posix_path, resolve_utf8_path,
};

pub use crate::ids::{compute_project_id, normalize_project_path, project_slug};

fn sha256_hex(input: impl AsRef<[u8]>) -> String {
    use sha2::{Digest, Sha256};
    let mut hasher = Sha256::new();
    hasher.update(input);
    hex::encode(hasher.finalize())
}

/// True if `raw` looks like an absolute path on any supported platform.
fn is_absolute_preview(raw: &str) -> bool {
    let preview = raw.replace('\\', "/");
    preview.starts_with('/')
        || preview.starts_with("//")
        || preview.starts_with("\\\\")
        || is_win_drive_path(raw)
}

/// Make a relative path absolute against the current working directory.
fn absolutize_relative_path(raw: &str) -> String {
    if is_absolute_preview(raw) {
        return raw.to_string();
    }
    let cwd = std::env::current_dir().unwrap_or_else(|_| std::path::PathBuf::from("."));
    cwd.join(raw).to_string_lossy().to_string()
}

fn normalize_path_slashes(raw: &str) -> String {
    raw.replace('\\', "/")
}

fn normalize_platform_drive_mapping(value: &str) -> String {
    normalize_mnt_drive_mapping(value)
        .or_else(|| normalize_msys_drive_mapping(value))
        .unwrap_or_else(|| value.to_string())
}

fn normalize_drive_letter_case(value: &str) -> String {
    if is_win_drive_path(value) {
        value[0..1].to_ascii_lowercase() + &value[1..]
    } else {
        value.to_string()
    }
}

/// Normalize a work_dir into a stable string for hashing and matching.
/// Mirrors Python `project.identity.normalize_work_dir`.
pub fn normalize_work_dir(value: impl AsRef<str>) -> String {
    let mut raw = value.as_ref().trim().to_string();
    if raw.is_empty() {
        return String::new();
    }
    raw = expand_user_path(&raw);
    raw = absolutize_relative_path(&raw);
    let mut normalized = normalize_path_slashes(&raw);
    normalized = normalize_platform_drive_mapping(&normalized);
    normalized = normalize_posix_path(&normalized);
    normalize_drive_letter_case(&normalized)
}

/// Compute a stable worktree/workspace scope id (first 12 hex chars of SHA256).
/// Mirrors Python `project.identity.compute_worktree_scope_id`.
pub fn compute_worktree_scope_id(work_dir: impl AsRef<str>) -> String {
    let norm = normalize_work_dir(work_dir);
    if norm.is_empty() {
        return String::new();
    }
    sha256_hex(norm.as_bytes())[..12].to_string()
}

fn _resolved_path(path: &str) -> Utf8PathBuf {
    let expanded = expand_user_path(path);
    resolve_utf8_path(Utf8Path::new(&expanded))
}

/// Try to resolve the project root from a work_dir.
pub(crate) fn try_resolve_project_root(
    work_dir: impl AsRef<str>,
) -> Result<Utf8PathBuf, crate::discovery::ProjectDiscoveryError> {
    let current = _resolved_path(work_dir.as_ref());
    if let Some(binding_path) = crate::discovery::find_workspace_binding(&current) {
        let binding = crate::discovery::load_workspace_binding(&binding_path)?;
        return Ok(_resolved_path(&expand_user_path(&binding.target_project)));
    }
    if let Some(anchor) = crate::discovery::find_nearest_project_anchor(&current) {
        return Ok(anchor);
    }
    Ok(current)
}

/// Resolve the project root from a work_dir, considering workspace bindings and anchors.
/// Mirrors Python `project.identity.resolve_project_root`.
pub fn resolve_project_root(work_dir: impl AsRef<str>) -> Utf8PathBuf {
    try_resolve_project_root(work_dir).expect("failed to resolve project root")
}

/// Compatibility wrapper for the v2 project id.
/// Mirrors Python `project.identity.compute_ccb_project_id`.
pub fn compute_ccb_project_id(work_dir: impl AsRef<str>) -> String {
    compute_project_id(resolve_project_root(work_dir).as_str())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_project_id_deterministic_and_64_chars() {
        let a = compute_project_id("/home/user/project");
        let b = compute_project_id("/home/user/project");
        assert_eq!(a, b);
        assert_eq!(a.len(), 64);
    }

    #[test]
    fn test_project_slug_contains_digest() {
        let slug = project_slug("/home/user/project");
        let digest = &compute_project_id("/home/user/project")[..8];
        assert!(slug.ends_with(digest));
    }

    #[test]
    fn test_normalize_work_dir_mnt_drive() {
        let normalized = normalize_work_dir("/mnt/C/Users/demo/repo");
        assert_eq!(normalized, "c:/Users/demo/repo");
    }

    #[test]
    fn test_compute_worktree_scope_id_12_chars() {
        let id = compute_worktree_scope_id("/home/user/project");
        assert_eq!(id.len(), 12);
    }

    #[test]
    fn test_resolve_project_root_prefers_binding() {
        let tmp = tempfile::tempdir().unwrap();
        let root = Utf8PathBuf::from_path_buf(tmp.path().to_path_buf()).unwrap();
        let target = root.join("target");
        std::fs::create_dir(&target).unwrap();
        let binding = root.join(crate::discovery::WORKSPACE_BINDING_FILENAME);
        std::fs::write(
            &binding,
            format!(r#"{{"target_project": "{}"}}"#, target.as_str()),
        )
        .unwrap();
        let resolved = resolve_project_root(root.as_str());
        assert_eq!(resolved, target);
    }

    #[test]
    fn test_resolve_project_root_falls_back_to_anchor() {
        let tmp = tempfile::tempdir().unwrap();
        let root = Utf8PathBuf::from_path_buf(tmp.path().to_path_buf()).unwrap();
        std::fs::create_dir(root.join(crate::discovery::CCB_DIRNAME)).unwrap();
        let resolved = resolve_project_root(root.as_str());
        assert_eq!(resolved, root);
    }
}
