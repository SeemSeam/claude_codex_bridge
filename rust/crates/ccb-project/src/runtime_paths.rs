use camino::{Utf8Path, Utf8PathBuf};

use crate::discovery::project_ccb_dir;
use crate::identity::try_resolve_project_root;

/// Return the `.ccb` directory for the project containing `work_dir`.
pub fn project_anchor_dir(work_dir: impl AsRef<Utf8Path>) -> Utf8PathBuf {
    project_ccb_dir(crate::identity::resolve_project_root(
        work_dir.as_ref().as_str(),
    ))
}

/// Return whether a project anchor exists for `work_dir`.
pub fn project_anchor_exists(work_dir: impl AsRef<Utf8Path>) -> bool {
    match try_resolve_project_root(work_dir.as_ref().as_str()) {
        Ok(root) => project_ccb_dir(&root).as_std_path().is_dir(),
        Err(_) => false,
    }
}

/// Return the `ccbd` runtime directory for the project containing `work_dir`.
pub fn project_ccbd_dir(work_dir: impl AsRef<Utf8Path>) -> Utf8PathBuf {
    project_anchor_dir(work_dir).join("ccbd")
}

/// Return the registry directory for the project containing `work_dir`.
pub fn project_registry_dir(work_dir: impl AsRef<Utf8Path>) -> Utf8PathBuf {
    project_ccbd_dir(work_dir).join("registry")
}

/// Return the lock directory for the project containing `work_dir`.
pub fn project_lock_dir(work_dir: impl AsRef<Utf8Path>) -> Utf8PathBuf {
    project_ccbd_dir(work_dir).join("locks")
}

#[cfg(test)]
mod tests {
    use super::*;

    fn tmpdir() -> tempfile::TempDir {
        tempfile::tempdir().unwrap()
    }

    fn utf8(path: &std::path::Path) -> Utf8PathBuf {
        Utf8PathBuf::from_path_buf(path.to_path_buf()).unwrap()
    }

    #[test]
    fn test_project_anchor_dir_and_exists() {
        let tmp = tmpdir();
        let root = utf8(tmp.path());
        std::fs::create_dir(root.join(crate::discovery::CCB_DIRNAME)).unwrap();
        let anchor = project_anchor_dir(&root);
        assert!(anchor.as_str().ends_with("/.ccb"));
        assert!(project_anchor_exists(&root));
    }

    #[test]
    fn test_project_ccbd_registry_lock_dirs() {
        let tmp = tmpdir();
        let root = utf8(tmp.path());
        std::fs::create_dir_all(root.join(crate::discovery::CCB_DIRNAME)).unwrap();
        assert!(project_ccbd_dir(&root).as_str().ends_with("/.ccb/ccbd"));
        assert!(project_registry_dir(&root)
            .as_str()
            .ends_with("/.ccb/ccbd/registry"));
        assert!(project_lock_dir(&root)
            .as_str()
            .ends_with("/.ccb/ccbd/locks"));
    }

    #[test]
    fn test_project_anchor_exists_false_for_missing_anchor() {
        let tmp = tmpdir();
        let root = utf8(tmp.path());
        assert!(!project_anchor_exists(&root));
    }
}
