from __future__ import annotations

from agents.models import AgentState
from agents.config_identity import project_config_identity_payload
from provider_execution.capabilities import execution_restore_capability
from storage.path_helpers import socket_placement_payload


def build_agent_payload(
    *,
    project_id: str,
    agent_name: str,
    registry,
    inspection,
    execution_registry,
    provider_pane_assessment=None,
) -> dict:
    spec = registry.spec_for(agent_name)
    runtime = registry.get(agent_name)
    adapter = execution_registry.get(spec.provider) if execution_registry is not None else None
    capability = execution_restore_capability(adapter, provider=spec.provider)
    diagnostics = {
        'ccbd_generation': inspection.generation,
        'last_heartbeat_at': inspection.lease.last_heartbeat_at if inspection.lease else None,
        'desired_state': _inspection_desired_state(inspection),
        **capability,
    }
    provider_health = _provider_health_payload(provider_pane_assessment)
    if provider_health is not None:
        diagnostics['provider_health'] = provider_health
    return {
        'project_id': project_id,
        'agent_name': spec.name,
        'provider': spec.provider,
        'mount_state': _agent_mount_state(runtime, inspection=inspection),
        'runtime_state': runtime.state.value if runtime is not None else 'stopped',
        'health': runtime.health if runtime is not None else inspection.health.value,
        'diagnostics': diagnostics,
    }


def build_ccbd_payload(
    *,
    project_id: str,
    config,
    paths,
    inspection,
    execution_summary: dict,
    restore_summary: dict,
    namespace_summary: dict,
    namespace_event_summary: dict,
    start_policy_summary: dict,
    control_plane_metrics=None,
) -> dict:
    identity = project_config_identity_payload(config)
    socket_path = inspection.socket_path if hasattr(inspection, 'socket_path') else None
    if socket_path is None and inspection.lease is not None:
        socket_path = inspection.lease.socket_path
    process_metrics = _process_metrics(control_plane_metrics)
    return {
        'project_id': project_id,
        'mount_state': _inspection_phase(inspection),
        'desired_state': _inspection_desired_state(inspection),
        'health': inspection.health.value,
        'generation': inspection.generation,
        'socket_path': socket_path,
        'tmux_socket_path': str(paths.ccbd_tmux_socket_placement.effective_path),
        **(paths.runtime_state_payload() if hasattr(paths, 'runtime_state_payload') else {}),
        **socket_placement_payload(paths.ccbd_socket_placement),
        **socket_placement_payload(paths.ccbd_tmux_socket_placement, prefix='tmux'),
        'known_agents': list(identity['known_agents']),
        'config_signature': identity['config_signature'],
        **namespace_summary,
        **namespace_event_summary,
        **start_policy_summary,
        'diagnostics': {
            'pid_alive': inspection.pid_alive,
            'socket_connectable': inspection.socket_connectable,
            'heartbeat_fresh': inspection.heartbeat_fresh,
            'takeover_allowed': inspection.takeover_allowed,
            'reason': inspection.reason,
            'startup_id': str(getattr(inspection, 'startup_id', '') or '').strip() or None,
            'startup_stage': str(getattr(inspection, 'startup_stage', '') or '').strip() or None,
            'last_progress_at': str(getattr(inspection, 'last_progress_at', '') or '').strip() or None,
            'startup_deadline_at': str(getattr(inspection, 'startup_deadline_at', '') or '').strip() or None,
            'last_failure_reason': str(getattr(inspection, 'last_failure_reason', '') or '').strip() or None,
            'shutdown_intent': str(getattr(inspection, 'shutdown_intent', '') or '').strip() or None,
            'last_request_queue_wait_s': getattr(control_plane_metrics, 'last_request_queue_wait_s', None),
            'last_submit_duration_s': getattr(control_plane_metrics, 'last_submit_duration_s', None),
            'last_ping_duration_s': getattr(control_plane_metrics, 'last_ping_duration_s', None),
            'last_handler_latency_s_by_op': dict(
                getattr(control_plane_metrics, 'last_handler_latency_s_by_op', {}) or {}
            ),
            'last_maintenance_duration_s': getattr(control_plane_metrics, 'last_maintenance_duration_s', None),
            'last_heartbeat_duration_s': getattr(control_plane_metrics, 'last_heartbeat_duration_s', None),
            'heartbeat_step_duration_s': dict(
                getattr(control_plane_metrics, 'heartbeat_step_duration_s', {}) or {}
            ),
            'last_heartbeat_agents_inspected': getattr(
                control_plane_metrics,
                'last_heartbeat_agents_inspected',
                None,
            ),
            'last_heartbeat_runtime_store_writes': getattr(
                control_plane_metrics,
                'last_heartbeat_runtime_store_writes',
                None,
            ),
            'pending_maintenance_ticks': getattr(control_plane_metrics, 'pending_maintenance_ticks', None),
            'last_project_view_response_duration_s': getattr(
                control_plane_metrics,
                'last_project_view_response_duration_s',
                None,
            ),
            'last_project_view_build_duration_s': getattr(
                control_plane_metrics,
                'last_project_view_build_duration_s',
                None,
            ),
            'project_view_cache_hits': getattr(control_plane_metrics, 'project_view_cache_hits', None),
            'project_view_cache_misses': getattr(control_plane_metrics, 'project_view_cache_misses', None),
            'last_project_view_tmux_command_count': getattr(
                control_plane_metrics,
                'last_project_view_tmux_command_count',
                None,
            ),
            'last_project_view_capture_pane_count': getattr(
                control_plane_metrics,
                'last_project_view_capture_pane_count',
                None,
            ),
            'last_project_view_store_scan_count': getattr(
                control_plane_metrics,
                'last_project_view_store_scan_count',
                None,
            ),
            'rss_bytes': process_metrics.get('rss_bytes'),
            'virtual_memory_bytes': process_metrics.get('virtual_memory_bytes'),
            'fd_count': process_metrics.get('fd_count'),
            'thread_count': process_metrics.get('thread_count'),
            'service_graph_version': getattr(control_plane_metrics, 'service_graph_version', None),
            'service_graph_created_at': getattr(control_plane_metrics, 'service_graph_created_at', None),
            'service_graph_retained_count': getattr(control_plane_metrics, 'service_graph_retained_count', None),
            'service_graph_retained_count_scope': getattr(
                control_plane_metrics,
                'service_graph_retained_count_scope',
                None,
            ),
            'last_reload_duration_s': getattr(control_plane_metrics, 'last_reload_duration_s', None),
            'last_reload_plan_class': getattr(control_plane_metrics, 'last_reload_plan_class', None),
            'last_reload_error': getattr(control_plane_metrics, 'last_reload_error', None),
            **execution_summary,
            **restore_summary,
        },
    }


def _process_metrics(control_plane_metrics) -> dict[str, int | None]:
    snapshot = getattr(control_plane_metrics, 'process_snapshot', None)
    if not callable(snapshot):
        return {}
    try:
        value = snapshot()
    except Exception:
        return {}
    return dict(value or {}) if isinstance(value, dict) else {}


def _inspection_phase(inspection) -> str:
    phase = str(getattr(inspection, 'phase', '') or '').strip()
    if phase:
        return phase
    lease = getattr(inspection, 'lease', None)
    return str(getattr(getattr(lease, 'mount_state', None), 'value', '') or 'unmounted')


def _inspection_desired_state(inspection) -> str | None:
    desired_state = str(getattr(inspection, 'desired_state', '') or '').strip()
    return desired_state or None


def _agent_mount_state(runtime, *, inspection) -> str:
    if runtime is None:
        return _inspection_phase(inspection)
    if runtime.state is AgentState.STARTING:
        return 'starting'
    if runtime.state is AgentState.FAILED:
        return 'failed'
    if runtime.state is AgentState.STOPPED:
        return 'unmounted'
    return 'mounted'


def _provider_health_payload(assessment) -> dict | None:
    """Project a ProviderPaneAssessment into the ping diagnostics payload.

    Returns None when there is no assessment or no pane signal to report, so
    the diagnostics dict stays unchanged for agents without pane visibility
    (e.g. non-tmux runtimes, missing sessions). None-valued fields are omitted
    to keep the payload compact.
    """
    if assessment is None:
        return None
    # Only surface when there is meaningful content: a signal state, a non-
    # healthy pane label, or a retry_after. A bare healthy/alive assessment
    # adds noise without diagnostic value.
    signal_state = getattr(assessment, 'pane_signal_state', None)
    retry_after = getattr(assessment, 'retry_after', None)
    health = str(getattr(assessment, 'health', '') or '').strip()
    has_signal = bool(signal_state) or bool(retry_after)
    has_content_health = health not in {'', 'healthy'} and health not in {
        'pane-missing',
        'session-missing',
        'pane-foreign',
        'pane-dead',
    }
    if not has_signal and not has_content_health:
        return None
    payload: dict[str, object] = {'state': health or 'unknown'}
    if signal_state:
        payload['signal_state'] = signal_state
    signal_reason = getattr(assessment, 'pane_signal_reason', None)
    if signal_reason:
        payload['signal_reason'] = signal_reason
    if retry_after:
        payload['retry_after'] = retry_after
    return payload


__all__ = ['build_agent_payload', 'build_ccbd_payload']
