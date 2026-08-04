from __future__ import annotations

from dataclasses import replace
import os
import time

from agents.config_loader import load_project_config
from ccbd.services.project_namespace import ProjectNamespaceController
from ccbd.services.project_namespace_runtime import build_namespace_topology_plan
from ccbd.services.project_namespace_runtime.materialize_topology import refresh_topology_sidebar_helpers
from terminal_runtime.backend_selection import make_terminal_backend

from .daemon import ensure_daemon_started
from .daemon_runtime.policy import STARTUP_TRANSACTION_TIMEOUT_S
from .config_restart_intent import clear_applied_config_restart_intent
from .layout_status import layout_status
from .maintenance import startup_ensure_maintenance_heartbeat
from .start_runtime import StartSummary, start_agents as _start_agents_impl
from ..startup_process_trace import consume_process_bootstrap_trace
from .tmux_project_cleanup import ProjectTmuxCleanupSummary
from workspace.reconcile import format_workspace_blockers, reconcile_start_workspaces


def start_agents(
    context,
    command,
    *,
    terminal_size: tuple[int, int] | None = None,
) -> StartSummary:
    cli_started_ns = time.perf_counter_ns()
    process_trace_id, process_timings, readiness_origin_ns = consume_process_bootstrap_trace(
        cli_started_ns
    )
    summary = _start_agents_impl(
        context,
        command,
        terminal_size=terminal_size,
        process_trace_id=process_trace_id,
        readiness_origin_ns=readiness_origin_ns,
        readiness_attach_mode=(
            'no_attach' if os.environ.get('CCB_NO_ATTACH') == '1' else 'interactive'
        ),
        ensure_daemon_started_fn=ensure_daemon_started,
        cleanup_summary_cls=ProjectTmuxCleanupSummary,
        before_client_start_fn=_reconcile_start_workspaces,
        enrich_summary_fn=_merge_workspace_guard_summary,
        start_rpc_timeout_s=STARTUP_TRANSACTION_TIMEOUT_S,
    )
    clear_applied_config_restart_intent(context)
    post_rpc_started_ns = time.perf_counter_ns()
    stage_started_ns = time.perf_counter_ns()
    sidebar_helper_refresh = _refresh_running_sidebar_helpers(context)
    sidebar_helper_refresh_ms = _elapsed_ms(stage_started_ns)
    summary = replace(summary, sidebar_helper_refresh=sidebar_helper_refresh)
    stage_started_ns = time.perf_counter_ns()
    summary = _attach_start_layout_summary(context, summary)
    layout_status_ms = _elapsed_ms(stage_started_ns)
    stage_started_ns = time.perf_counter_ns()
    heartbeat_summary = startup_ensure_maintenance_heartbeat(context)
    maintenance_heartbeat_ms = _elapsed_ms(stage_started_ns)
    if heartbeat_summary is not None:
        summary = replace(summary, maintenance_heartbeat=heartbeat_summary)
    timings = dict(summary.cli_timings_ms or {})
    timings.update(
        {
            'sidebar_helper_refresh': sidebar_helper_refresh_ms,
            'layout_status': layout_status_ms,
            'maintenance_heartbeat': maintenance_heartbeat_ms,
            'cli_post_rpc': float(timings.get('cli_post_rpc', 0.0)) + _elapsed_ms(post_rpc_started_ns),
            'cli_total': _elapsed_ms(cli_started_ns),
        }
    )
    return replace(
        summary,
        cli_timings_ms=timings,
        process_bootstrap_trace_id=process_trace_id,
        process_bootstrap_timings_ms=process_timings,
    )


def _reconcile_start_workspaces(context):
    summary = reconcile_start_workspaces(
        context.project.project_root,
        load_project_config(context.project.project_root).config,
    )
    if summary.blockers:
        raise RuntimeError(format_workspace_blockers('ccb start', summary.blockers))
    return summary


def _merge_workspace_guard_summary(context, summary: StartSummary, guard_summary) -> StartSummary:
    del context
    if guard_summary is None:
        return summary
    return replace(
        summary,
        worktree_warnings=tuple(getattr(guard_summary, 'warnings', ()) or ()),
        worktree_retired=tuple(getattr(guard_summary, 'retired', ()) or ()),
    )


def _refresh_running_sidebar_helpers(context) -> dict[str, object]:
    try:
        controller = ProjectNamespaceController(context.paths, context.project.project_id)
        namespace = controller.load()
        if namespace is None:
            return {'status': 'not_mounted'}
        topology_plan = build_namespace_topology_plan(
            load_project_config(context.project.project_root).config
        )
        backend = make_terminal_backend(socket_path=namespace.tmux_socket_path)
        refreshed = refresh_topology_sidebar_helpers(
            controller,
            backend,
            topology_plan=topology_plan,
            tmux_session_name=namespace.tmux_session_name,
            namespace_epoch=namespace.namespace_epoch,
        )
    except Exception as exc:
        return {
            'status': 'failed',
            'error_type': type(exc).__name__,
            'error': str(exc),
        }
    return {
        'status': 'refreshed' if refreshed else 'current',
        'panes': refreshed,
    }


def _attach_start_layout_summary(context, summary: StartSummary) -> StartSummary:
    try:
        payload = layout_status(context)
    except Exception as exc:
        payload = {
            'layout_summary_status': 'unavailable',
            'layout_status': 'unavailable',
            'error_type': type(exc).__name__,
            'error': str(exc),
        }
    else:
        payload = _compact_start_layout_summary(payload)
    return replace(summary, layout_summary=payload)


def _compact_start_layout_summary(payload: dict[str, object]) -> dict[str, object]:
    observed = payload.get('observed') if isinstance(payload.get('observed'), dict) else {}
    windows = [
        _compact_start_window(window)
        for window in tuple(payload.get('windows') or ())
        if isinstance(window, dict)
    ]
    return {
        'layout_summary_status': str(payload.get('layout_status') or 'unknown'),
        'layout_status': payload.get('layout_status'),
        'ccbd_state': payload.get('ccbd_state'),
        'windows_explicit': bool(payload.get('windows_explicit')),
        'entry_window': payload.get('entry_window'),
        'window_count': payload.get('window_count'),
        'pane_count': payload.get('pane_count'),
        'dynamic_agent_count': payload.get('dynamic_agent_count'),
        'loop_agent_count': payload.get('loop_agent_count'),
        'runtime_agent_count': payload.get('runtime_agent_count'),
        'observe_status': observed.get('observe_status'),
        'observe_reason': observed.get('reason'),
        'observed_pane_count': observed.get('observed_pane_count'),
        'windows': windows,
    }


def _compact_start_window(window: dict[str, object]) -> dict[str, object]:
    agents = [
        _compact_start_agent(agent)
        for agent in tuple(window.get('agents') or ())
        if isinstance(agent, dict)
    ]
    return {
        'name': window.get('name'),
        'index': window.get('index'),
        'pane_count': window.get('pane_count'),
        'runtime_pane_count': window.get('runtime_pane_count'),
        'agent_names': list(window.get('agent_names') or ()),
        'agents': agents,
    }


def _compact_start_agent(agent: dict[str, object]) -> dict[str, object]:
    return {
        'agent': agent.get('agent'),
        'source': agent.get('source'),
        'agent_kind': agent.get('agent_kind'),
        'ownership_class': agent.get('ownership_class'),
        'dispatch_state': agent.get('dispatch_state'),
        'window_name': agent.get('window_name'),
        'pane_id': agent.get('pane_id'),
        'pane_identity_source': agent.get('pane_identity_source'),
        'runtime_state': agent.get('runtime_state'),
        'apply_status': agent.get('apply_status'),
        'failed_apply': agent.get('failed_apply'),
    }


def _elapsed_ms(started_ns: int) -> float:
    return (time.perf_counter_ns() - started_ns) / 1_000_000


__all__ = ['StartSummary', 'start_agents']
