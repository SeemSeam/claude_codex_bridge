use camino::{Utf8Path, Utf8PathBuf};
use serde::Deserialize;

use crate::path_utils::{expand_user_path, resolve_utf8_path};

pub const CCB_DIRNAME: &str = ".ccb";
pub const WORKSPACE_BINDING_FILENAME: &str = ".ccb-workspace.json";

#[derive(Debug, thiserror::Error)]
pub enum ProjectDiscoveryError {
    #[error("cannot read workspace binding {path}: {source}")]
    WorkspaceBindingRead {
        path: String,
        source: std::io::Error,
    },
    #[error("cannot parse workspace binding {path}: {source}")]
    WorkspaceBindingParse {
        path: String,
        source: serde_json::Error,
    },
    #[error("workspace binding {0} must contain an object")]
    InvalidWorkspaceBinding(String),
    #[error("workspace binding {0} is missing target_project")]
    MissingTargetProject(String),
    #[error("invalid project anchor: {0}")]
    InvalidAnchor(String),
    #[error("cannot auto-create .ccb in {0}")]
    NestedAnchor(String),
    #[error("cannot resolve project for {0}; no .ccb anchor or workspace binding found")]
    Unresolved(String),
    #[error("refusing to auto-create .ccb in {0}; set CCB_INIT_PROJECT_DANGEROUS=1 to override")]
    DangerousRoot(String),
    #[error("{0}")]
    AnchorNotFound(String),
    #[error("{0}")]
    WorkspaceAnchorMissing(String),
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
}

#[derive(Debug, Clone, Deserialize)]
pub struct WorkspaceBinding {
    pub target_project: String,
}

/// Return the project-local `.ccb` directory.
pub fn project_ccb_dir(project_root: impl AsRef<Utf8Path>) -> Utf8PathBuf {
    resolve_utf8_path(project_root.as_ref()).join(CCB_DIRNAME)
}

/// Return the global `~/.ccb` directory.
pub fn global_ccb_dir() -> Utf8PathBuf {
    let home = std::env::var("HOME").unwrap_or_else(|_| "/tmp".into());
    Utf8PathBuf::from(expand_user_path(&home)).join(CCB_DIRNAME)
}

/// Return `start_dir` if it directly contains a `.ccb` anchor.
pub fn find_current_project_anchor(start_dir: impl AsRef<Utf8Path>) -> Option<Utf8PathBuf> {
    let current = resolved_dir(start_dir.as_ref());
    if project_anchor_dir(&current).is_some() {
        Some(current)
    } else {
        None
    }
}

/// Find the nearest `.ccb` anchor starting at `start_dir` and walking upward.
pub fn find_nearest_project_anchor(start_dir: impl AsRef<Utf8Path>) -> Option<Utf8PathBuf> {
    let current = resolved_dir(start_dir.as_ref());
    for root in search_roots(&current) {
        if project_anchor_dir(&root).is_none() {
            continue;
        }
        let (dangerous, _) = is_dangerous_project_root(&root);
        if root != current && dangerous {
            continue;
        }
        return Some(root);
    }
    None
}

/// Find a parent project's `.ccb` directory, skipping dangerous roots.
pub fn find_parent_project_anchor_dir(start_dir: impl AsRef<Utf8Path>) -> Option<Utf8PathBuf> {
    let current = resolved_dir(start_dir.as_ref());
    for root in current.ancestors().skip(1) {
        let candidate = project_anchor_dir(root);
        if candidate.is_none() {
            continue;
        }
        let (dangerous, _) = is_dangerous_project_root(root);
        if dangerous {
            continue;
        }
        return candidate;
    }
    None
}

/// Return whether `start_dir` is a root where auto-creating `.ccb` would be
/// dangerous (home, temp root, filesystem root).
pub fn is_dangerous_project_root(start_dir: impl AsRef<Utf8Path>) -> (bool, String) {
    let current = resolved_dir(start_dir.as_ref());
    if let Some(home) = resolved_home_dir() {
        if home == current {
            return (true, "$HOME".into());
        }
    }
    if let Some(temp) = resolved_temp_dir() {
        if temp == current {
            return (true, "temporary directory root".into());
        }
    }
    if let Some(anchor) = filesystem_anchor(&current) {
        if anchor == current {
            return (true, "filesystem root".into());
        }
    }
    (false, String::new())
}

fn project_anchor_dir(root: &Utf8Path) -> Option<Utf8PathBuf> {
    let primary = root.join(CCB_DIRNAME);
    if primary.as_std_path().is_dir() {
        Some(primary)
    } else {
        None
    }
}

/// Find the nearest `.ccb-workspace.json` binding file.
pub fn find_workspace_binding(start_dir: impl AsRef<Utf8Path>) -> Option<Utf8PathBuf> {
    let current = resolved_dir(start_dir.as_ref());
    for root in search_roots(&current) {
        let candidate = root.join(WORKSPACE_BINDING_FILENAME);
        if candidate.as_std_path().is_file() {
            return Some(candidate);
        }
    }
    None
}

/// Load and validate a workspace binding file.
pub fn load_workspace_binding(
    path: impl AsRef<Utf8Path>,
) -> Result<WorkspaceBinding, ProjectDiscoveryError> {
    let path = path.as_ref();
    let path_str = path.as_str().to_string();
    let text = std::fs::read_to_string(path.as_std_path()).map_err(|e| {
        ProjectDiscoveryError::WorkspaceBindingRead {
            path: path_str.clone(),
            source: e,
        }
    })?;
    let data: serde_json::Value =
        serde_json::from_str(&text).map_err(|e| ProjectDiscoveryError::WorkspaceBindingParse {
            path: path_str.clone(),
            source: e,
        })?;
    let obj = data
        .as_object()
        .ok_or_else(|| ProjectDiscoveryError::InvalidWorkspaceBinding(path_str.clone()))?;
    let target_project = obj
        .get("target_project")
        .and_then(|v| v.as_str())
        .map(|s| s.trim())
        .filter(|s| !s.is_empty())
        .ok_or_else(|| ProjectDiscoveryError::MissingTargetProject(path_str.clone()))?;
    Ok(WorkspaceBinding {
        target_project: target_project.to_string(),
    })
}

fn resolved_dir(path: &Utf8Path) -> Utf8PathBuf {
    resolve_utf8_path(path)
}

fn resolved_home_dir() -> Option<Utf8PathBuf> {
    let home = std::env::var("HOME").ok()?;
    if home.trim().is_empty() {
        return None;
    }
    Some(resolve_utf8_path(Utf8Path::new(&expand_user_path(&home))))
}

fn filesystem_anchor(current: &Utf8Path) -> Option<Utf8PathBuf> {
    let mut components = current.as_std_path().components();
    let first = components.next()?;
    let raw = first.as_os_str().to_string_lossy().to_string();
    if raw.is_empty() {
        None
    } else {
        Some(Utf8PathBuf::from(raw))
    }
}

fn resolved_temp_dir() -> Option<Utf8PathBuf> {
    let path = std::env::temp_dir();
    if let Ok(resolved) = std::fs::canonicalize(&path) {
        if let Ok(utf8) = Utf8PathBuf::from_path_buf(resolved) {
            return Some(utf8);
        }
    }
    Utf8PathBuf::from_path_buf(crate::path_utils::absolute_path(&path)).ok()
}

fn search_roots(current: &Utf8Path) -> Vec<Utf8PathBuf> {
    std::iter::once(current.to_path_buf())
        .chain(current.ancestors().skip(1).map(|p| p.to_path_buf()))
        .collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use camino::Utf8Path;
    use std::io::Write;

    fn tmpdir() -> tempfile::TempDir {
        tempfile::tempdir().unwrap()
    }

    fn utf8(path: &std::path::Path) -> Utf8PathBuf {
        Utf8PathBuf::from_path_buf(path.to_path_buf()).unwrap()
    }

    #[test]
    fn test_project_ccb_dir_appends_ccb() {
        let dir = project_ccb_dir(Utf8Path::new("/home/user/project"));
        assert!(dir.as_str().ends_with("/.ccb"));
    }

    #[test]
    fn test_find_current_project_anchor() {
        let tmp = tmpdir();
        let root = utf8(tmp.path());
        std::fs::create_dir(root.join(CCB_DIRNAME)).unwrap();
        assert_eq!(find_current_project_anchor(&root), Some(root.clone()));
        assert!(find_current_project_anchor(Utf8Path::new("/nonexistent-no-anchor")).is_none());
    }

    #[test]
    fn test_find_nearest_project_anchor_walks_up() {
        let tmp = tmpdir();
        let root = utf8(tmp.path());
        std::fs::create_dir(root.join(CCB_DIRNAME)).unwrap();
        let sub = root.join("src/nested");
        std::fs::create_dir_all(&sub).unwrap();
        assert_eq!(find_nearest_project_anchor(&sub), Some(root));
    }

    #[test]
    fn test_find_parent_project_anchor_dir() {
        let tmp = tmpdir();
        let root = utf8(tmp.path());
        std::fs::create_dir(root.join(CCB_DIRNAME)).unwrap();
        let child = root.join("child");
        std::fs::create_dir_all(&child).unwrap();
        assert_eq!(
            find_parent_project_anchor_dir(&child),
            Some(root.join(CCB_DIRNAME))
        );
    }

    #[test]
    fn test_is_dangerous_project_root() {
        let (yes, reason) = is_dangerous_project_root(Utf8Path::new("/"));
        assert!(yes);
        assert_eq!(reason, "filesystem root");

        let (yes, reason) = is_dangerous_project_root(Utf8Path::new("/tmp"));
        assert!(yes);
        assert_eq!(reason, "temporary directory root");

        let tmp = tmpdir();
        let home = utf8(tmp.path());
        let old_home = std::env::var("HOME").ok();
        std::env::set_var("HOME", home.as_str());
        let (yes, reason) = is_dangerous_project_root(&home);
        assert!(yes);
        assert_eq!(reason, "$HOME");
        if let Some(h) = old_home {
            std::env::set_var("HOME", h);
        } else {
            std::env::remove_var("HOME");
        }
    }

    #[test]
    fn test_find_workspace_binding() {
        let tmp = tmpdir();
        let root = utf8(tmp.path());
        let sub = root.join("sub");
        std::fs::create_dir_all(&sub).unwrap();
        let binding = root.join(WORKSPACE_BINDING_FILENAME);
        std::fs::write(&binding, r#"{"target_project": "/other/project"}"#).unwrap();
        assert_eq!(find_workspace_binding(&sub), Some(binding));
    }

    #[test]
    fn test_load_workspace_binding_ok() {
        let tmp = tmpdir();
        let path = tmp.path().join(WORKSPACE_BINDING_FILENAME);
        let mut f = std::fs::File::create(&path).unwrap();
        f.write_all(r#"{"target_project": "/target"}"#.as_bytes())
            .unwrap();
        let binding =
            load_workspace_binding(Utf8PathBuf::from_path_buf(path.to_path_buf()).unwrap())
                .unwrap();
        assert_eq!(binding.target_project, "/target");
    }

    #[test]
    fn test_load_workspace_binding_rejects_missing_target() {
        let tmp = tmpdir();
        let path = tmp.path().join(WORKSPACE_BINDING_FILENAME);
        std::fs::write(&path, r#"{"other": "x"}"#).unwrap();
        let err = load_workspace_binding(Utf8PathBuf::from_path_buf(path.to_path_buf()).unwrap())
            .unwrap_err();
        assert!(err.to_string().contains("missing target_project"));
    }
}
