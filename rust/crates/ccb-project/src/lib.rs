//! CCB project discovery, identity, resolver, and runtime paths.
//!
//! Mirrors the corresponding Python v7.5.2 package. This crate is the canonical
//! home for project id/slug computation and worktree scope resolution.

pub mod discovery;
pub mod identity;
pub mod ids;
pub mod resolver;
pub mod runtime_paths;

mod path_utils;

// Re-export the most commonly used helpers at crate root, matching __init__.py.
pub use discovery::{
    find_current_project_anchor, find_nearest_project_anchor, find_parent_project_anchor_dir,
    find_workspace_binding, global_ccb_dir, is_dangerous_project_root, load_workspace_binding,
    project_ccb_dir, ProjectDiscoveryError, WorkspaceBinding,
};
pub use identity::{
    compute_ccb_project_id, compute_project_id, compute_worktree_scope_id, normalize_project_path,
    normalize_work_dir, project_slug, resolve_project_root,
};
pub use resolver::{bootstrap_project, ProjectContext, ProjectResolver};
pub use runtime_paths::{
    project_anchor_dir, project_anchor_exists, project_ccbd_dir, project_lock_dir,
    project_registry_dir,
};

/// Crate version.
pub fn version() -> &'static str {
    env!("CARGO_PKG_VERSION")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn crate_smoke() {
        assert_eq!(version(), env!("CARGO_PKG_VERSION"));
    }

    #[test]
    fn project_id_is_stable() {
        let id1 = compute_project_id("/home/user/my-project");
        let id2 = compute_project_id("/home/user/my-project");
        assert_eq!(id1, id2);
        assert_eq!(id1.len(), 64);
    }
}
