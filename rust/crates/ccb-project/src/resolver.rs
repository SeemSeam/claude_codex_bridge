use camino::{Utf8Path, Utf8PathBuf};

use crate::discovery::{
    find_current_project_anchor, find_nearest_project_anchor, find_parent_project_anchor_dir,
    find_workspace_binding, is_dangerous_project_root, load_workspace_binding, project_ccb_dir,
    ProjectDiscoveryError,
};
use crate::ids::compute_project_id;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProjectContext {
    pub cwd: Utf8PathBuf,
    pub project_root: Utf8PathBuf,
    pub config_dir: Utf8PathBuf,
    pub project_id: String,
    pub source: String,
}

#[derive(Debug, Default, Clone)]
pub struct ProjectResolver;

impl ProjectResolver {
    pub fn new() -> Self {
        Self
    }

    pub fn resolve(
        &self,
        cwd: impl AsRef<Utf8Path>,
        explicit_project: Option<&Utf8Path>,
        allow_ancestor_anchor: bool,
    ) -> Result<ProjectContext, ProjectDiscoveryError> {
        let current = _resolved_path(cwd.as_ref());

        if let Some(explicit) = explicit_project {
            return _explicit_project_context(&current, explicit);
        }

        if let Some(binding_path) = find_workspace_binding(&current) {
            return _workspace_binding_context(&current, &binding_path);
        }

        let anchor = if allow_ancestor_anchor {
            find_nearest_project_anchor(&current)
        } else {
            find_current_project_anchor(&current)
        };
        if let Some(anchor) = anchor {
            return _project_context(&current, &anchor, "anchor");
        }

        Err(ProjectDiscoveryError::Unresolved(current.to_string()))
    }
}

/// Bootstrap a new project anchor at `project_root`.
pub fn bootstrap_project(
    project_root: impl AsRef<Utf8Path>,
) -> Result<ProjectContext, ProjectDiscoveryError> {
    let root = _resolved_path(project_root.as_ref());
    let config_dir = project_ccb_dir(&root);
    if config_dir.as_std_path().exists() && !config_dir.as_std_path().is_dir() {
        return Err(ProjectDiscoveryError::InvalidAnchor(format!(
            "{} exists but is not a directory",
            config_dir
        )));
    }
    if let Some(parent_anchor) = find_parent_project_anchor_dir(&root) {
        return Err(ProjectDiscoveryError::NestedAnchor(
            _nested_anchor_bootstrap_error(&root, parent_anchor.parent().unwrap_or(root.as_ref())),
        ));
    }
    let (is_dangerous, danger_reason) = is_dangerous_project_root(&root);
    if is_dangerous && !env_truthy("CCB_INIT_PROJECT_DANGEROUS") {
        return Err(ProjectDiscoveryError::DangerousRoot(format!(
            "{danger_reason}; set CCB_INIT_PROJECT_DANGEROUS=1 to override"
        )));
    }
    std::fs::create_dir_all(&config_dir)?;
    ensure_bootstrap_project_config(&root)?;
    _project_context(&root, &root, "bootstrapped")
}

fn ensure_bootstrap_project_config(
    project_root: &Utf8Path,
) -> Result<Utf8PathBuf, ProjectDiscoveryError> {
    let config_dir = project_ccb_dir(project_root);
    std::fs::create_dir_all(&config_dir)?;
    Ok(config_dir.join("ccb.config"))
}

fn env_truthy(name: &str) -> bool {
    let value = std::env::var(name).unwrap_or_default();
    matches!(
        value.trim().to_lowercase().as_str(),
        "1" | "true" | "yes" | "on"
    )
}

fn _resolved_path(path: &Utf8Path) -> Utf8PathBuf {
    crate::path_utils::resolve_utf8_path(path)
}

fn _project_context(
    cwd: &Utf8Path,
    root: &Utf8Path,
    source: &str,
) -> Result<ProjectContext, ProjectDiscoveryError> {
    Ok(ProjectContext {
        cwd: cwd.to_path_buf(),
        project_root: root.to_path_buf(),
        config_dir: project_ccb_dir(root),
        project_id: compute_project_id(root.as_str()),
        source: source.to_string(),
    })
}

fn _explicit_project_context(
    cwd: &Utf8Path,
    explicit_project: &Utf8Path,
) -> Result<ProjectContext, ProjectDiscoveryError> {
    let root = _resolved_path(explicit_project);
    _require_anchor_dir(&root, "project anchor not found")?;
    _project_context(cwd, &root, "explicit")
}

fn _workspace_binding_context(
    cwd: &Utf8Path,
    binding_path: &Utf8Path,
) -> Result<ProjectContext, ProjectDiscoveryError> {
    let binding = load_workspace_binding(binding_path)?;
    let root = _resolved_path(Utf8Path::new(&crate::path_utils::expand_user_path(
        &binding.target_project,
    )));
    _require_anchor_dir(&root, "workspace binding points to missing project anchor")?;
    _project_context(cwd, &root, "workspace-binding")
}

fn _require_anchor_dir(root: &Utf8Path, reason: &str) -> Result<(), ProjectDiscoveryError> {
    let config_dir = project_ccb_dir(root);
    if !config_dir.as_std_path().is_dir() {
        return Err(ProjectDiscoveryError::AnchorNotFound(format!(
            "{reason}: {config_dir}"
        )));
    }
    Ok(())
}

fn _nested_anchor_bootstrap_error(project_root: &Utf8Path, parent_root: &Utf8Path) -> String {
    format!(
        "cannot auto-create .ccb in {project_root}: \
         parent project anchor already exists at {parent_anchor}; \
         .ccb is the unique project anchor for a project tree. \
         If you intentionally want {project_root} to be a separate project, \
         create {new_anchor} manually and rerun",
        parent_anchor = project_ccb_dir(parent_root),
        new_anchor = project_ccb_dir(project_root)
    )
}

#[cfg(test)]
mod tests {
    use super::*;
    use camino::Utf8Path;

    fn tmpdir() -> tempfile::TempDir {
        tempfile::tempdir().unwrap()
    }

    fn utf8(path: &std::path::Path) -> Utf8PathBuf {
        Utf8PathBuf::from_path_buf(path.to_path_buf()).unwrap()
    }

    #[test]
    fn test_resolve_explicit_project() {
        let tmp = tmpdir();
        let root = utf8(tmp.path());
        std::fs::create_dir(root.join(crate::discovery::CCB_DIRNAME)).unwrap();
        let resolver = ProjectResolver::new();
        let ctx = resolver
            .resolve(Utf8Path::new("/cwd"), Some(root.as_ref()), true)
            .unwrap();
        assert_eq!(ctx.project_root, root);
        assert_eq!(ctx.source, "explicit");
    }

    #[test]
    fn test_resolve_workspace_binding() {
        let tmp = tmpdir();
        let root = utf8(tmp.path());
        let target = root.join("target");
        std::fs::create_dir(&target).unwrap();
        std::fs::create_dir(target.join(crate::discovery::CCB_DIRNAME)).unwrap();
        let binding = root.join(crate::discovery::WORKSPACE_BINDING_FILENAME);
        std::fs::write(
            &binding,
            format!(r#"{{"target_project": "{}"}}"#, target.as_str()),
        )
        .unwrap();
        let resolver = ProjectResolver::new();
        let ctx = resolver.resolve(root.as_path(), None, true).unwrap();
        assert_eq!(ctx.project_root, target);
        assert_eq!(ctx.source, "workspace-binding");
    }

    #[test]
    fn test_resolve_anchor_ancestor() {
        let tmp = tmpdir();
        let root = utf8(tmp.path());
        std::fs::create_dir(root.join(crate::discovery::CCB_DIRNAME)).unwrap();
        let sub = root.join("sub");
        std::fs::create_dir(&sub).unwrap();
        let resolver = ProjectResolver::new();
        let ctx = resolver.resolve(sub.as_path(), None, true).unwrap();
        assert_eq!(ctx.project_root, root);
        assert_eq!(ctx.source, "anchor");
    }

    #[test]
    fn test_resolve_no_anchor_fails() {
        let tmp = tmpdir();
        let root = utf8(tmp.path());
        let resolver = ProjectResolver::new();
        let err = resolver.resolve(root.as_path(), None, true).unwrap_err();
        assert!(err
            .to_string()
            .contains("no .ccb anchor or workspace binding found"));
    }

    #[test]
    fn test_bootstrap_project_creates_anchor() {
        let tmp = tmpdir();
        let root = utf8(tmp.path()).join("new-project");
        std::fs::create_dir(&root).unwrap();
        let ctx = bootstrap_project(&root).unwrap();
        assert_eq!(ctx.project_root, root);
        assert_eq!(ctx.source, "bootstrapped");
        assert!(root
            .join(crate::discovery::CCB_DIRNAME)
            .as_std_path()
            .is_dir());
    }

    #[test]
    fn test_bootstrap_rejects_nested_anchor() {
        let tmp = tmpdir();
        let root = utf8(tmp.path());
        std::fs::create_dir(root.join(crate::discovery::CCB_DIRNAME)).unwrap();
        let child = root.join("child");
        std::fs::create_dir(&child).unwrap();
        let err = bootstrap_project(&child).unwrap_err();
        assert!(err
            .to_string()
            .contains("parent project anchor already exists"));
    }
}
