use camino::Utf8PathBuf;

use crate::path_helpers::{choose_socket_placement, RootKind, SocketPlacement};
use crate::paths::PathLayout;

impl PathLayout {
    // --- Project anchor paths ---

    pub fn project_anchor_dir(&self) -> Utf8PathBuf {
        self.ccb_dir()
    }

    pub fn ccb_dir(&self) -> Utf8PathBuf {
        self.project_root.join(".ccb")
    }

    pub fn config_path(&self) -> Utf8PathBuf {
        self.ccb_dir().join("ccb.config")
    }

    // --- CCBD paths ---

    pub fn ccbd_dir(&self) -> Utf8PathBuf {
        self.runtime_state_root.join("ccbd")
    }

    pub fn ccbd_submissions_path(&self) -> Utf8PathBuf {
        self.ccbd_dir().join("submissions.jsonl")
    }

    pub fn ccbd_mailboxes_dir(&self) -> Utf8PathBuf {
        self.ccbd_dir().join("mailboxes")
    }

    pub fn ccbd_messages_dir(&self) -> Utf8PathBuf {
        self.ccbd_dir().join("messages")
    }

    pub fn ccbd_messages_path(&self) -> Utf8PathBuf {
        self.ccbd_messages_dir().join("messages.jsonl")
    }

    pub fn ccbd_attempts_dir(&self) -> Utf8PathBuf {
        self.ccbd_dir().join("attempts")
    }

    pub fn ccbd_attempts_path(&self) -> Utf8PathBuf {
        self.ccbd_attempts_dir().join("attempts.jsonl")
    }

    pub fn ccbd_replies_dir(&self) -> Utf8PathBuf {
        self.ccbd_dir().join("replies")
    }

    pub fn ccbd_replies_path(&self) -> Utf8PathBuf {
        self.ccbd_replies_dir().join("replies.jsonl")
    }

    pub fn ccbd_callback_edges_path(&self) -> Utf8PathBuf {
        self.ccbd_dir().join("callbacks/edges.jsonl")
    }

    pub fn ccbd_leases_dir(&self) -> Utf8PathBuf {
        self.ccbd_dir().join("leases")
    }

    pub fn ccbd_dead_letters_dir(&self) -> Utf8PathBuf {
        self.ccbd_dir().join("dead-letters")
    }

    pub fn ccbd_dead_letters_path(&self) -> Utf8PathBuf {
        self.ccbd_dead_letters_dir().join("dead_letters.jsonl")
    }

    pub fn ccbd_provider_health_dir(&self) -> Utf8PathBuf {
        self.ccbd_dir().join("provider-health")
    }

    // --- Message bureau paths (legacy aliases used by other crates) ---

    pub fn message_bureau_dir(&self) -> Utf8PathBuf {
        self.ccbd_messages_dir()
    }

    pub fn message_store_path(&self) -> Utf8PathBuf {
        self.ccbd_messages_path()
    }

    pub fn attempt_store_path(&self) -> Utf8PathBuf {
        self.ccbd_attempts_path()
    }

    pub fn reply_store_path(&self) -> Utf8PathBuf {
        self.ccbd_replies_path()
    }

    // --- CCBD mount / lifecycle paths ---

    fn project_socket_placement(&self, stem: &str) -> SocketPlacement {
        let preferred_root_kind =
            if matches!(self.runtime_state_placement.root_kind, RootKind::Relocated) {
                RootKind::Runtime
            } else {
                RootKind::Project
            };
        choose_socket_placement(
            &self.ccbd_dir().join(format!("{}.sock", stem)),
            &self.project_socket_key(),
            preferred_root_kind,
        )
    }

    pub fn ccbd_socket_placement(&self) -> SocketPlacement {
        self.project_socket_placement("ccbd")
    }

    pub fn ccbd_socket_path(&self) -> Utf8PathBuf {
        self.ccbd_socket_placement().effective_path
    }

    pub fn ccbd_pid_path(&self) -> Utf8PathBuf {
        self.ccbd_dir().join("ccbd.pid")
    }

    pub fn ccbd_lifecycle_path(&self) -> Utf8PathBuf {
        self.ccbd_dir().join("lifecycle.json")
    }

    pub fn ccbd_lease_path(&self) -> Utf8PathBuf {
        self.ccbd_dir().join("lease.json")
    }

    pub fn ccbd_state_path(&self) -> Utf8PathBuf {
        self.ccbd_dir().join("state.json")
    }

    pub fn ccbd_project_view_state_path(&self) -> Utf8PathBuf {
        self.ccbd_dir().join("project-view-state.json")
    }

    pub fn ccbd_start_policy_path(&self) -> Utf8PathBuf {
        self.ccbd_dir().join("start-policy.json")
    }

    pub fn ccbd_restore_report_path(&self) -> Utf8PathBuf {
        self.ccbd_dir().join("restore-report.json")
    }

    pub fn ccbd_startup_report_path(&self) -> Utf8PathBuf {
        self.ccbd_dir().join("startup-report.json")
    }

    pub fn ccbd_shutdown_report_path(&self) -> Utf8PathBuf {
        self.ccbd_dir().join("shutdown-report.json")
    }

    pub fn ccbd_tmux_socket_placement(&self) -> SocketPlacement {
        self.project_socket_placement("tmux")
    }

    pub fn ccbd_tmux_socket_path(&self) -> Utf8PathBuf {
        self.ccbd_tmux_socket_placement().effective_path
    }

    pub fn ccbd_tmux_session_name(&self) -> String {
        let safe = crate::paths::tmux_safe_name(&self.project_slug(), "project");
        format!("ccb-{}", safe)
    }

    pub fn ccbd_tmux_control_window_name(&self) -> &'static str {
        "__ccb_ctl"
    }

    pub fn ccbd_tmux_workspace_window_name(&self) -> &'static str {
        "ccb"
    }

    // --- CCBD ops paths ---

    pub fn ccbd_supervision_path(&self) -> Utf8PathBuf {
        self.ccbd_dir().join("supervision.jsonl")
    }

    pub fn ccbd_lifecycle_log_path(&self) -> Utf8PathBuf {
        self.ccbd_dir().join("lifecycle.jsonl")
    }

    pub fn ccbd_keeper_path(&self) -> Utf8PathBuf {
        self.ccbd_dir().join("keeper.json")
    }

    pub fn ccbd_shutdown_intent_path(&self) -> Utf8PathBuf {
        self.ccbd_dir().join("shutdown-intent.json")
    }

    pub fn ccbd_tmux_cleanup_history_path(&self) -> Utf8PathBuf {
        self.ccbd_dir().join("tmux-cleanup-history.jsonl")
    }

    pub fn ccbd_maintenance_heartbeat_dir(&self) -> Utf8PathBuf {
        self.ccbd_dir().join("maintenance-heartbeat")
    }

    pub fn ccbd_maintenance_heartbeat_schedule_path(&self) -> Utf8PathBuf {
        self.ccbd_maintenance_heartbeat_dir().join("schedule.json")
    }

    pub fn ccbd_maintenance_heartbeat_status_path(&self) -> Utf8PathBuf {
        self.ccbd_maintenance_heartbeat_dir().join("status.json")
    }

    pub fn ccbd_maintenance_heartbeat_runner_path(&self) -> Utf8PathBuf {
        self.ccbd_maintenance_heartbeat_dir().join("runner.json")
    }

    pub fn ccbd_maintenance_heartbeat_lock_path(&self) -> Utf8PathBuf {
        self.ccbd_maintenance_heartbeat_dir().join("lock.json")
    }

    pub fn ccbd_maintenance_heartbeat_activations_path(&self) -> Utf8PathBuf {
        self.ccbd_maintenance_heartbeat_dir()
            .join("activations.jsonl")
    }

    pub fn ccbd_fault_injection_path(&self) -> Utf8PathBuf {
        self.ccbd_dir().join("fault-injection.json")
    }

    pub fn ccbd_reload_drain_path(&self) -> Utf8PathBuf {
        self.ccbd_dir().join("reload-drain.json")
    }

    pub fn ccbd_reload_handoff_path(&self) -> Utf8PathBuf {
        self.ccbd_dir().join("reload-handoff.json")
    }

    // --- CCBD artifact paths ---

    pub fn ccbd_artifacts_dir(&self) -> Utf8PathBuf {
        self.ccbd_dir().join("artifacts")
    }

    pub fn ccbd_text_artifacts_dir(&self) -> Utf8PathBuf {
        self.ccbd_artifacts_dir().join("text")
    }

    pub fn ccbd_support_dir(&self) -> Utf8PathBuf {
        self.ccbd_dir().join("support")
    }

    pub fn ccbd_executions_dir(&self) -> Utf8PathBuf {
        self.ccbd_dir().join("executions")
    }

    pub fn ccbd_snapshots_dir(&self) -> Utf8PathBuf {
        self.ccbd_dir().join("snapshots")
    }

    pub fn ccbd_cursors_dir(&self) -> Utf8PathBuf {
        self.ccbd_dir().join("cursors")
    }

    pub fn ccbd_heartbeats_dir(&self) -> Utf8PathBuf {
        self.ccbd_dir().join("heartbeats")
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn test_ccb_dir_structure() {
        let layout = PathLayout::new("/project");
        assert_eq!(layout.ccb_dir(), Utf8PathBuf::from("/project/.ccb"));
        assert_eq!(
            layout.ccbd_dir(),
            Utf8PathBuf::from("/project/.ccb/ccbd")
        );
    }
}
