from __future__ import annotations

import re


_TMUX_SAFE_NAME_RE = re.compile(r'[^A-Za-z0-9_-]+')


def _tmux_safe_name(value: object, *, fallback: str) -> str:
    text = str(value or '').strip()
    sanitized = _TMUX_SAFE_NAME_RE.sub('_', text).strip('_-')
    return sanitized or fallback


class ProjectAnchorPathMixin:
    @property
    def project_anchor_dir(self):
        return self.ccb_dir

    @property
    def ccb_dir(self):
        return self.project_root / '.ccb'

    @property
    def config_path(self):
        return self.ccb_dir / 'ccb.config'

    @property
    def ccbd_dir(self):
        return self.runtime_state_root / 'ccbd'


class CcbdMailboxPathMixin:
    @property
    def ccbd_submissions_path(self):
        return self.ccbd_dir / 'submissions.jsonl'

    @property
    def ccbd_mailboxes_dir(self):
        return self.ccbd_dir / 'mailboxes'

    @property
    def ccbd_messages_dir(self):
        return self.ccbd_dir / 'messages'

    @property
    def ccbd_messages_path(self):
        return self.ccbd_messages_dir / 'messages.jsonl'

    @property
    def ccbd_attempts_dir(self):
        return self.ccbd_dir / 'attempts'

    @property
    def ccbd_attempts_path(self):
        return self.ccbd_attempts_dir / 'attempts.jsonl'

    @property
    def ccbd_replies_dir(self):
        return self.ccbd_dir / 'replies'

    @property
    def ccbd_replies_path(self):
        return self.ccbd_replies_dir / 'replies.jsonl'

    @property
    def ccbd_callback_edges_path(self):
        return self.ccbd_dir / 'callbacks' / 'edges.jsonl'

    @property
    def ccbd_leases_dir(self):
        return self.ccbd_dir / 'leases'

    @property
    def ccbd_dead_letters_dir(self):
        return self.ccbd_dir / 'dead-letters'

    @property
    def ccbd_dead_letters_path(self):
        return self.ccbd_dead_letters_dir / 'dead_letters.jsonl'

    @property
    def ccbd_provider_health_dir(self):
        return self.ccbd_dir / 'provider-health'


class CcbdMountPathMixin:
    @property
    def ccbd_socket_placement(self):
        return self._project_socket_placement('ccbd')

    @property
    def ccbd_lifecycle_path(self):
        return self.ccbd_dir / 'lifecycle.json'

    @property
    def ccbd_lease_path(self):
        return self.ccbd_dir / 'lease.json'

    @property
    def ccbd_socket_path(self):
        return self.ccbd_socket_placement.effective_path

    @property
    def ccbd_state_path(self):
        return self.ccbd_dir / 'state.json'

    @property
    def ccbd_project_view_state_path(self):
        return self.ccbd_dir / 'project-view-state.json'

    @property
    def ccbd_config_restart_intent_path(self):
        return self.ccbd_dir / 'config-restart-intent.json'

    @property
    def ccbd_mobile_dir(self):
        return self.ccbd_dir / 'mobile'

    @property
    def ccbd_mobile_gateway_path(self):
        return self.ccbd_mobile_dir / 'gateway.json'

    @property
    def ccbd_mobile_devices_path(self):
        return self.ccbd_mobile_dir / 'devices.json'

    @property
    def ccbd_mobile_pairing_tokens_path(self):
        return self.ccbd_mobile_dir / 'pairing-tokens.jsonl'

    @property
    def ccbd_mobile_terminal_tokens_path(self):
        return self.ccbd_mobile_dir / 'terminal-tokens.jsonl'

    @property
    def ccbd_mobile_audit_path(self):
        return self.ccbd_mobile_dir / 'audit.jsonl'

    @property
    def ccbd_start_policy_path(self):
        return self.ccbd_dir / 'start-policy.json'

    @property
    def ccbd_restore_report_path(self):
        return self.ccbd_dir / 'restore-report.json'

    @property
    def ccbd_startup_report_path(self):
        return self.ccbd_dir / 'startup-report.json'

    @property
    def ccbd_shutdown_report_path(self):
        return self.ccbd_dir / 'shutdown-report.json'

    @property
    def ccbd_tmux_socket_placement(self):
        return self._project_socket_placement('tmux')

    @property
    def ccbd_tmux_socket_path(self):
        return self.ccbd_tmux_socket_placement.effective_path

    @property
    def ccbd_tmux_session_name(self) -> str:
        return f'ccb-{_tmux_safe_name(self.project_slug, fallback="project")}'

    @property
    def ccbd_tmux_control_window_name(self) -> str:
        return '__ccb_ctl'

    @property
    def ccbd_tmux_workspace_window_name(self) -> str:
        return 'ccb'


class CcbdOpsPathMixin:
    @property
    def ccbd_desktop_events_path(self):
        """Durable Desktop V1 event log under the protected CCB runtime root."""
        return self.ccbd_dir / 'desktop-events.jsonl'

    @property
    def ccbd_desktop_cursor_path(self):
        """Durable Desktop V1 cursor metadata under the protected CCB runtime root."""
        return self.ccbd_dir / 'desktop-cursor.json'

    @property
    def ccbd_desktop_lock_path(self):
        return self.ccbd_dir / 'desktop-events.lock'

    @property
    def ccbd_supervision_path(self):
        return self.ccbd_dir / 'supervision.jsonl'

    @property
    def ccbd_lifecycle_log_path(self):
        return self.ccbd_dir / 'lifecycle.jsonl'

    @property
    def ccbd_keeper_path(self):
        return self.ccbd_dir / 'keeper.json'

    @property
    def ccbd_shutdown_intent_path(self):
        return self.ccbd_dir / 'shutdown-intent.json'

    @property
    def ccbd_tmux_cleanup_history_path(self):
        return self.ccbd_dir / 'tmux-cleanup-history.jsonl'

    @property
    def ccbd_maintenance_heartbeat_dir(self):
        return self.ccbd_dir / 'maintenance-heartbeat'

    @property
    def ccbd_maintenance_heartbeat_schedule_path(self):
        return self.ccbd_maintenance_heartbeat_dir / 'schedule.json'

    @property
    def ccbd_maintenance_heartbeat_status_path(self):
        return self.ccbd_maintenance_heartbeat_dir / 'status.json'

    @property
    def ccbd_maintenance_heartbeat_runner_path(self):
        return self.ccbd_maintenance_heartbeat_dir / 'runner.json'

    @property
    def ccbd_maintenance_heartbeat_lock_path(self):
        return self.ccbd_maintenance_heartbeat_dir / 'lock.json'

    @property
    def ccbd_maintenance_heartbeat_activations_path(self):
        return self.ccbd_maintenance_heartbeat_dir / 'activations.jsonl'

    @property
    def ccbd_fault_injection_path(self):
        return self.ccbd_dir / 'fault-injection.json'

    @property
    def ccbd_reload_drain_path(self):
        return self.ccbd_dir / 'reload-drain.json'

    @property
    def ccbd_reload_handoff_path(self):
        return self.ccbd_dir / 'reload-handoff.json'

    @property
    def ccbd_active_followups_path(self):
        return self.ccbd_dir / 'active-followups.jsonl'


class CcbdArtifactsPathMixin:
    @property
    def ccbd_artifacts_dir(self):
        return self.ccbd_dir / 'artifacts'

    @property
    def ccbd_text_artifacts_dir(self):
        return self.ccbd_artifacts_dir / 'text'

    @property
    def ccbd_support_dir(self):
        return self.ccbd_dir / 'support'

    @property
    def ccbd_executions_dir(self):
        return self.ccbd_dir / 'executions'

    @property
    def ccbd_snapshots_dir(self):
        return self.ccbd_dir / 'snapshots'

    @property
    def ccbd_cursors_dir(self):
        return self.ccbd_dir / 'cursors'

    @property
    def ccbd_heartbeats_dir(self):
        return self.ccbd_dir / 'heartbeats'


__all__ = [
    'CcbdArtifactsPathMixin',
    'CcbdMailboxPathMixin',
    'CcbdMountPathMixin',
    'CcbdOpsPathMixin',
    'ProjectAnchorPathMixin',
]
