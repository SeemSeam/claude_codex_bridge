from __future__ import annotations

from pathlib import Path

from agents.models import (
    AgentApiSpec,
    AgentRuntime,
    AgentState,
    AgentRestoreState,
    AgentSpec,
    PermissionMode,
    ProviderProfileSpec,
    QueuePolicy,
    RestoreMode,
    RestoreStatus,
    RuntimeBindingSource,
    RuntimeMode,
    WorkspaceMode,
)
from storage.json_store import JsonStore
from storage.paths import PathLayout

SCHEMA_VERSION = 2


class AgentSpecStore:
    def __init__(self, layout: PathLayout, store: JsonStore | None = None) -> None:
        self._layout = layout
        self._store = store or JsonStore()

    def load(self, agent_name: str) -> AgentSpec | None:
        path = self._layout.agent_spec_path(agent_name)
        if not path.exists():
            return None
        return self._store.load(path, loader=_agent_spec_from_record)

    def save(self, spec: AgentSpec) -> Path:
        path = self._layout.agent_spec_path(spec.name)
        self._store.save_if_changed(path, spec, serializer=lambda value: value.to_record())
        return path


class AgentRuntimeStore:
    def __init__(self, layout: PathLayout, store: JsonStore | None = None) -> None:
        self._layout = layout
        self._store = store or JsonStore()
        self.save_count = 0

    def load(self, agent_name: str) -> AgentRuntime | None:
        path = self._layout.agent_runtime_path(agent_name)
        if not path.exists():
            return None
        return self._store.load(path, loader=_agent_runtime_from_record)

    def save(self, runtime: AgentRuntime) -> Path:
        path = self._layout.agent_runtime_path(runtime.agent_name)
        changed = self._store.save_if_changed(path, runtime, serializer=lambda value: value.to_record())
        if changed:
            self.save_count += 1
        return path

    def load_best_effort(self, agent_name: str) -> AgentRuntime | None:
        try:
            return self.load(agent_name)
        except Exception:
            return None


class AgentRestoreStore:
    def __init__(self, layout: PathLayout, store: JsonStore | None = None) -> None:
        self._layout = layout
        self._store = store or JsonStore()

    def load(self, agent_name: str) -> AgentRestoreState | None:
        path = self._layout.agent_restore_path(agent_name)
        if not path.exists():
            return None
        return self._store.load(path, loader=_agent_restore_from_record)

    def save(self, agent_name: str, restore_state: AgentRestoreState) -> Path:
        path = self._layout.agent_restore_path(agent_name)
        self._store.save(path, restore_state, serializer=lambda value: value.to_record())
        return path


def _validate_record(record: dict, expected_type: str) -> None:
    if record.get('schema_version') != SCHEMA_VERSION:
        raise ValueError(f'schema_version must be {SCHEMA_VERSION}')
    if record.get('record_type') != expected_type:
        raise ValueError(f'record_type must be {expected_type!r}')


def _agent_spec_from_record(record: dict) -> AgentSpec:
    _validate_record(record, 'agent_spec')
    return AgentSpec(
        name=record['name'],
        provider=record['provider'],
        target=record['target'],
        workspace_mode=WorkspaceMode(record['workspace_mode']),
        workspace_root=record.get('workspace_root'),
        workspace_path=record.get('workspace_path'),
        workspace_group=record.get('workspace_group'),
        provider_command_template=record.get('provider_command_template'),
        remote_workspace=record.get('remote_workspace'),
        runtime_mode=RuntimeMode(record['runtime_mode']),
        restore_default=RestoreMode(record['restore_default']),
        permission_default=PermissionMode(record['permission_default']),
        queue_policy=QueuePolicy(record['queue_policy']),
        model=record.get('model'),
        thinking=record.get('thinking'),
        startup_args=tuple(record.get('startup_args', [])),
        env=dict(record.get('env', {})),
        api=AgentApiSpec(**dict(record.get('api') or {})),
        provider_profile=ProviderProfileSpec(**dict(record.get('provider_profile') or {})),
        branch_template=record.get('branch_template'),
        labels=tuple(record.get('labels', [])),
        description=record.get('description'),
        role=record.get('role'),
        watch_paths=tuple(record.get('watch_paths', [])),
        dispatch_disabled=bool(record.get('dispatch_disabled', False)),
    )


def _agent_runtime_from_record(record: dict) -> AgentRuntime:
    _validate_record(record, 'agent_runtime')
    return AgentRuntime(
        agent_name=record['agent_name'],
        state=AgentState(record['state']),
        pid=record.get('pid'),
        started_at=record.get('started_at'),
        last_seen_at=record.get('last_seen_at'),
        runtime_ref=record.get('runtime_ref'),
        session_ref=record.get('session_ref'),
        workspace_path=record.get('workspace_path'),
        project_id=record['project_id'],
        backend_type=record['backend_type'],
        queue_depth=record['queue_depth'],
        socket_path=record.get('socket_path'),
        health=record['health'],
        provider=record.get('provider'),
        runtime_root=record.get('runtime_root'),
        runtime_pid=record.get('runtime_pid'),
        terminal_backend=record.get('terminal_backend'),
        provider_runtime_backend_ref=(
            dict(record['provider_runtime_backend_ref'])
            if isinstance(record.get('provider_runtime_backend_ref'), dict)
            else None
        ),
        namespace_ref=dict(record['namespace_ref']) if isinstance(record.get('namespace_ref'), dict) else None,
        pane_ref=dict(record['pane_ref']) if isinstance(record.get('pane_ref'), dict) else None,
        namespace_restore_token_present=bool(record.get('namespace_restore_token_present', False)),
        herdr_auto_restore_mode=record.get('herdr_auto_restore_mode'),
        herdr_agent_state_ref=record.get('herdr_agent_state_ref'),
        pane_id=record.get('pane_id'),
        active_pane_id=record.get('active_pane_id'),
        pane_title_marker=record.get('pane_title_marker'),
        pane_state=record.get('pane_state'),
        tmux_socket_name=record.get('tmux_socket_name'),
        tmux_socket_path=record.get('tmux_socket_path'),
        tmux_window_name=record.get('tmux_window_name'),
        tmux_window_id=record.get('tmux_window_id'),
        session_file=record.get('session_file'),
        session_id=record.get('session_id'),
        slot_key=record.get('slot_key'),
        window_id=record.get('window_id'),
        workspace_epoch=(int(record['workspace_epoch']) if record.get('workspace_epoch') is not None else None),
        lifecycle_state=record.get('lifecycle_state'),
        binding_generation=int(record.get('binding_generation', 1)),
        managed_by=record.get('managed_by', 'ccbd'),
        binding_source=RuntimeBindingSource(record.get('binding_source', RuntimeBindingSource.PROVIDER_SESSION.value)),
        daemon_generation=(int(record['daemon_generation']) if record.get('daemon_generation') is not None else None),
        runtime_generation=(int(record['runtime_generation']) if record.get('runtime_generation') is not None else None),
        desired_state=record.get('desired_state'),
        reconcile_state=record.get('reconcile_state'),
        restart_count=int(record.get('restart_count', 0)),
        recovery_failure_count=int(record.get('recovery_failure_count', 0)),
        last_reconcile_at=record.get('last_reconcile_at'),
        last_failure_reason=record.get('last_failure_reason'),
        mount_attempt_id=record.get('mount_attempt_id'),
    )


def _agent_restore_from_record(record: dict) -> AgentRestoreState:
    _validate_record(record, 'agent_restore_state')
    return AgentRestoreState(
        restore_mode=RestoreMode(record['restore_mode']),
        last_checkpoint=record.get('last_checkpoint'),
        conversation_summary=record.get('conversation_summary', ''),
        open_tasks=list(record.get('open_tasks', [])),
        files_touched=list(record.get('files_touched', [])),
        base_commit=record.get('base_commit'),
        head_commit=record.get('head_commit'),
        last_restore_status=(
            RestoreStatus(record['last_restore_status']) if record.get('last_restore_status') is not None else None
        ),
    )
