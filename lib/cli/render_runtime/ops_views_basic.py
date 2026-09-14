from __future__ import annotations

from collections.abc import Mapping
import json

from .common import cleanup_csv, render_tmux_cleanup_summaries
from .ops_views_common import binding_line, herdr_surface_lines


def render_agent_lifecycle(summary) -> tuple[str, ...]:
    payload = summary if isinstance(summary, Mapping) else {}
    lines = [
        f'agent_lifecycle_status: {payload.get("agent_lifecycle_status", "unknown")}',
        f'action: {payload.get("action", "")}',
        f'project_id: {payload.get("project_id", "")}',
    ]
    agent = payload.get('agent')
    if agent is not None:
        lines.append(f'agent: {agent}')
        lines.append(f'role: {payload.get("role", "")}')
        lines.append(f'provider: {payload.get("provider", "")}')
        lines.append(f'lifecycle_state: {payload.get("lifecycle_state", "")}')
        lines.append(f'visibility_state: {payload.get("visibility_state", "")}')
        if payload.get('resolved_window_name') is not None:
            lines.append(f'resolved_window_name: {payload.get("resolved_window_name", "")}')
        if payload.get('requested_policy') is not None:
            lines.append(f'requested_policy: {payload.get("requested_policy", "")}')
        if payload.get('resolved_policy') is not None:
            lines.append(f'resolved_policy: {payload.get("resolved_policy", "")}')
        if payload.get('state_path') is not None:
            lines.append(f'state_path: {payload.get("state_path", "")}')
        return tuple(lines)
    lines.append(f'agent_count: {payload.get("agent_count", 0)}')
    for record in tuple(payload.get('agents') or ()):
        if not isinstance(record, Mapping):
            continue
        lines.append(
            'agent: '
            f'name={record.get("agent", "")} '
            f'source={record.get("source", "")} '
            f'role={record.get("role", "")} '
            f'provider={record.get("provider", "")} '
            f'lifecycle_state={record.get("lifecycle_state", "")} '
            f'window={record.get("resolved_window_name", "")}'
        )
    return tuple(lines)


def render_config_validate(summary) -> tuple[str, ...]:
    if int(getattr(summary, 'config_version', 2)) == 3:
        workflow = summary.workflow or {}
        capacity = summary.effective_workgroup_capacity or {}
        return (
            'config_status: valid',
            f'project: {summary.project_root}',
            f'project_id: {summary.project_id}',
            f'config_source_kind: {summary.source_kind}',
            f'config_source: {summary.source or "<builtin>"}',
            'config_version: 3',
            f'workflow_mode: {workflow.get("mode", "")}',
            f'workflow_profile: {workflow.get("profile", "")}',
            f'entry_role: {workflow.get("entry_role", "")}',
            'resident_roles: ' + ', '.join(str(item.get('slot')) for item in summary.resident_roles),
            'dynamic_profiles: ' + ', '.join(str(item.get('profile')) for item in summary.dynamic_profiles),
            'effective_workgroup_capacity: '
            f'max_workgroups={capacity.get("max_workgroups")} '
            f'max_parallel_workgroups={capacity.get("max_parallel_workgroups")} '
            f'max_active_dynamic_agents={capacity.get("max_active_dynamic_agents")}',
            f'capacity_digest: {summary.capacity_digest}',
        )
    lines = [
        'config_status: valid',
        f'project: {summary.project_root}',
        f'project_id: {summary.project_id}',
        f'config_source_kind: {summary.source_kind}',
        f'config_source: {summary.source or "<builtin>"}',
        f'used_builtin_default: {str(summary.used_builtin_default).lower()}',
        f'default_agents: {", ".join(summary.default_agents)}',
        f'agents: {", ".join(summary.agent_names)}',
        f'cmd_enabled: {str(summary.cmd_enabled).lower()}',
        f'layout: {summary.layout_spec}',
    ]
    lines.extend(f'config_warning: {warning}' for warning in getattr(summary, 'style_warnings', ()) or ())
    return tuple(lines)


def render_start(summary) -> tuple[str, ...]:
    lines = [
        'start_status: ok',
        f'project: {summary.project_root}',
        f'project_id: {summary.project_id}',
        f'ccbd_started: {str(summary.daemon_started).lower()}',
        f'socket_path: {summary.socket_path}',
        f'agents: {", ".join(summary.started)}',
    ]
    startup_run_id = str(getattr(summary, 'startup_run_id', None) or '').strip()
    if startup_run_id:
        lines.append(f'startup_run_id: {startup_run_id}')
    cli_timings_ms = getattr(summary, 'cli_timings_ms', None)
    if isinstance(cli_timings_ms, Mapping):
        lines.append(
            'startup_cli_timings_ms: '
            + json.dumps(dict(cli_timings_ms), ensure_ascii=True, sort_keys=True, separators=(',', ':'))
        )
    process_trace_id = str(getattr(summary, 'process_bootstrap_trace_id', None) or '').strip()
    process_timings_ms = getattr(summary, 'process_bootstrap_timings_ms', None)
    if process_trace_id and isinstance(process_timings_ms, Mapping):
        lines.append(f'startup_process_trace_id: {process_trace_id}')
        lines.append(
            'startup_process_bootstrap_timings_ms: '
            + json.dumps(
                dict(process_timings_ms),
                ensure_ascii=True,
                sort_keys=True,
                separators=(',', ':'),
            )
        )
    heartbeat = getattr(summary, 'maintenance_heartbeat', None)
    if isinstance(heartbeat, Mapping):
        details = [
            f'status={heartbeat.get("maintenance_status")}',
            f'action={heartbeat.get("action")}',
        ]
        if heartbeat.get('runner_status') is not None:
            details.append(f'runner_status={heartbeat.get("runner_status")}')
        if heartbeat.get('tick_status') is not None:
            details.append(f'tick_status={heartbeat.get("tick_status")}')
        lines.append(
            'maintenance_heartbeat: '
            + ' '.join(details)
        )
        reason = str(heartbeat.get('reason') or '').strip()
        if reason:
            lines.append(f'maintenance_heartbeat_reason: {reason}')
    layout_summary = getattr(summary, 'layout_summary', None)
    if isinstance(layout_summary, Mapping):
        lines.extend(_render_start_layout_summary(layout_summary))
    sidebar_refresh = getattr(summary, 'sidebar_helper_refresh', None)
    if isinstance(sidebar_refresh, Mapping):
        status = str(sidebar_refresh.get('status') or '').strip()
        if status == 'refreshed':
            panes = ','.join(str(item) for item in sidebar_refresh.get('panes') or ()) or '-'
            lines.append(f'sidebar_helper_refresh: refreshed panes={panes}')
        elif status == 'failed':
            error_type = str(sidebar_refresh.get('error_type') or 'Error').strip()
            error = str(sidebar_refresh.get('error') or 'unknown error').strip()
            lines.append(f'sidebar_helper_refresh: failed {error_type}: {error}')
    lines.extend(render_tmux_cleanup_summaries(getattr(summary, 'cleanup_summaries', ()) or ()))
    return tuple(lines)


def _render_start_layout_summary(payload: Mapping[str, object]) -> list[str]:
    status = str(payload.get('layout_summary_status') or payload.get('layout_status') or 'unknown')
    lines = [f'layout_summary_status: {status}']
    if status == 'unavailable':
        error_type = str(payload.get('error_type') or '').strip()
        error = str(payload.get('error') or '').strip()
        if error_type:
            lines.append(f'layout_summary_error_type: {error_type}')
        if error:
            lines.append(f'layout_summary_error: {error}')
        return lines
    details = [
        f'windows={_text(payload.get("window_count"), default="0")}',
        f'panes={_text(payload.get("pane_count"), default="0")}',
        f'runtime_panes={_text(payload.get("observed_pane_count"), default="0")}',
        f'dynamic={_text(payload.get("dynamic_agent_count"), default="0")}',
        f'loop={_text(payload.get("loop_agent_count"), default="0")}',
        f'runtime={_text(payload.get("runtime_agent_count"), default="0")}',
        f'explicit={str(bool(payload.get("windows_explicit"))).lower()}',
        f'entry_window={_text(payload.get("entry_window"), default="-")}',
        f'ccbd_state={_text(payload.get("ccbd_state"), default="unknown")}',
        f'observe_status={_text(payload.get("observe_status"), default="unknown")}',
    ]
    observe_reason = str(payload.get('observe_reason') or '').strip()
    if observe_reason:
        details.append(f'observe_reason={observe_reason}')
    lines.append('layout: ' + ' '.join(details))
    for window in tuple(payload.get('windows') or ()):
        if not isinstance(window, Mapping):
            continue
        lines.append(
            'layout_window: '
            f'name={_text(window.get("name"), default="-")} '
            f'index={_text(window.get("index"), default="-")} '
            f'panes={_text(window.get("pane_count"), default="0")} '
            f'runtime_panes={_text(window.get("runtime_pane_count"), default="0")} '
            f'agents={cleanup_csv(tuple(window.get("agent_names") or ()))}'
        )
        for agent in tuple(window.get('agents') or ()):
            if isinstance(agent, Mapping):
                lines.append(_render_start_layout_agent(agent))
    return lines


def _render_start_layout_agent(agent: Mapping[str, object]) -> str:
    return (
        'layout_agent: '
        f'name={_text(agent.get("agent"), default="-")} '
        f'kind={_text(agent.get("agent_kind"), default="-")} '
        f'source={_text(agent.get("source"), default="-")} '
        f'ownership={_text(agent.get("ownership_class"), default="-")} '
        f'dispatch={_text(agent.get("dispatch_state"), default="-")} '
        f'window={_text(agent.get("window_name"), default="-")} '
        f'pane={_text(agent.get("pane_id"), default="-")} '
        f'pane_identity={_text(agent.get("pane_identity_source"), default="-")} '
        f'runtime_state={_text(agent.get("runtime_state"), default="-")} '
        f'apply_status={_text(agent.get("apply_status"), default="-")} '
        f'failed_apply={str(bool(agent.get("failed_apply"))).lower()}'
    )


def _text(value: object, *, default: str) -> str:
    text = str(value or '').strip()
    return text or default


def render_logs(summary) -> tuple[str, ...]:
    lines = [
        'logs_status: ok',
        f'project_id: {summary.project_id}',
        f'agent_name: {summary.agent_name}',
        f'provider: {summary.provider}',
        f'runtime_ref: {summary.runtime_ref}',
        f'session_ref: {summary.session_ref}',
        f'log_count: {len(summary.entries)}',
    ]
    if not summary.entries:
        lines.append('log: <none>')
        return tuple(lines)
    for entry in summary.entries:
        lines.append(f'log: {entry.source} {entry.path}')
        for line in entry.lines:
            lines.append(f'log_line: {line}')
    return tuple(lines)


def render_loop_capacity(summary) -> tuple[str, ...]:
    payload = summary if isinstance(summary, Mapping) else {}
    status = str(payload.get('loop_capacity_status') or 'unknown')
    lines = [
        f'loop_capacity_status: {status}',
        f'loop_id: {payload.get("loop_id", "")}',
        f'project_id: {payload.get("project_id", "")}',
        f'agent_count: {payload.get("agent_count", 0)}',
        f'state_path: {payload.get("state_path", "")}',
        f'events_path: {payload.get("events_path", "")}',
    ]
    released_count = payload.get('released_count')
    if released_count is not None:
        lines.append(f'released_count: {released_count}')
    for request in tuple(payload.get('requests') or ()):
        if isinstance(request, Mapping):
            lines.append(f'loop_capacity_request: profile={request.get("profile", "")} count={request.get("count", 0)}')
    for agent in tuple(payload.get('agents') or ()):
        if not isinstance(agent, Mapping):
            continue
        lines.append(
            'loop_capacity_agent: '
            f'name={agent.get("name", "")} '
            f'profile={agent.get("profile", "")} '
            f'role={agent.get("role", "")} '
            f'provider={agent.get("provider", "")} '
            f'state={agent.get("state", "")} '
            f'node={agent.get("node_id", "")} '
            f'window={agent.get("window_name", "")}'
        )
    return tuple(lines)


def render_loop_topology(summary) -> tuple[str, ...]:
    payload = summary if isinstance(summary, Mapping) else {}
    status = str(payload.get('loop_topology_status') or 'unknown')
    lines = [
        f'loop_topology_status: {status}',
        f'loop_id: {payload.get("loop_id", "")}',
    ]
    for key in ('proposal_id', 'revision', 'desired_revision', 'agent_count', 'retained_count', 'released_count'):
        if key in payload:
            lines.append(f'{key}: {payload.get(key)}')
    for key in ('proposal_path', 'desired_path', 'observed_path'):
        value = str(payload.get(key) or '').strip()
        if value:
            lines.append(f'{key}: {value}')
    validation = payload.get('validation')
    if isinstance(validation, Mapping):
        lines.append(f'topology_validation_status: {validation.get("topology_validation_status", "")}')
        lines.append(f'present_agent_count: {validation.get("present_agent_count", 0)}')
        profile_counts = validation.get('profile_counts')
        if isinstance(profile_counts, Mapping):
            for profile, count in sorted(profile_counts.items()):
                lines.append(f'topology_profile: profile={profile} count={count}')
    observed = payload.get('observed')
    if isinstance(observed, Mapping):
        drift = observed.get('drift')
        if isinstance(drift, Mapping):
            mismatched = tuple(drift.get('mismatched_agents') or ())
            lines.append(f'drift_mismatch_count: {len(mismatched)}')
        for agent in tuple(observed.get('agents') or ()):
            if not isinstance(agent, Mapping):
                continue
            lines.append(
                'topology_agent: '
                f'id={agent.get("id", "")} '
                f'profile={agent.get("profile", "")} '
                f'desired={agent.get("desired_state", "")} '
                f'observed={agent.get("observed_state", "")} '
                f'node={agent.get("node_id", "")} '
                f'window={agent.get("window_name", "")}'
            )
    actions = tuple(payload.get('actions') or ())
    if actions:
        lines.append(f'action_count: {len(actions)}')
        for action in actions:
            if isinstance(action, Mapping):
                lines.append(
                    'topology_action: '
                    f'action={action.get("action", "")} '
                    f'agent={action.get("agent", "")} '
                    f'window={action.get("window_name", "") or action.get("target_window", "")} '
                    f'status={action.get("status", "")}'
                )
    return tuple(lines)


def render_loop_run_once(summary) -> tuple[str, ...]:
    payload = summary if isinstance(summary, Mapping) else {}
    agents = payload.get('agents') if isinstance(payload.get('agents'), Mapping) else {}
    paths = payload.get('paths') if isinstance(payload.get('paths'), Mapping) else {}
    capacity = payload.get('capacity') if isinstance(payload.get('capacity'), Mapping) else {}
    release = capacity.get('release') if isinstance(capacity.get('release'), Mapping) else {}
    lines = [
        f'loop_run_status: {payload.get("loop_run_status", "unknown")}',
        f'loop_id: {payload.get("loop_id", "")}',
        f'project_id: {payload.get("project_id", "")}',
        f'worker_agent: {agents.get("worker", "")}',
        f'reviewer_agent: {agents.get("reviewer", "")}',
        f'orchestrator: {agents.get("orchestrator", "")}',
        f'round_checker: {agents.get("round_checker", "")}',
    ]
    for key, label in (
        ('worker', 'worker_job'),
        ('reviewer', 'reviewer_job'),
        ('aggregation', 'aggregate_job'),
        ('round_checker', 'round_checker_job'),
    ):
        item = payload.get(key)
        if isinstance(item, Mapping):
            lines.append(
                f'{label}: {item.get("job_id", "")} '
                f'target={item.get("target", "")} '
                f'status={item.get("status", "")}'
            )
    lines.extend(
        [
            f'release_status: {release.get("loop_capacity_status", "")}',
            f'retained_count: {release.get("retained_count", 0)}',
            f'round_path: {paths.get("round", "")}',
            f'asks_path: {paths.get("asks", "")}',
            f'events_path: {paths.get("events", "")}',
            f'breadcrumb_path: {paths.get("breadcrumb", "")}',
        ]
    )
    return tuple(lines)


def render_loop_runner(summary) -> tuple[str, ...]:
    payload = summary if isinstance(summary, Mapping) else {}
    lines = [
        f'loop_runner_status: {payload.get("loop_runner_status", "unknown")}',
        f'action: {payload.get("action", "")}',
        f'project_id: {payload.get("project_id", "")}',
    ]
    task_id = str(payload.get('task_id') or '').strip()
    if task_id:
        lines.extend(
            [
                f'task_id: {task_id}',
                f'loop_id: {payload.get("loop_id", "")}',
                f'round_result: {payload.get("round_result", "")}',
                f'round_result_source: {payload.get("round_result_source", "")}',
                f'task_status: {payload.get("task_status", "")}',
                f'next_activation: {payload.get("next_activation", "")}',
            ]
        )
    reason = str(payload.get('reason') or '').strip()
    if reason:
        lines.append(f'reason: {reason}')
    return tuple(lines)


def render_layout(summary) -> tuple[str, ...]:
    payload = summary if isinstance(summary, Mapping) else {}
    lines = [
        f'layout_status: {payload.get("layout_status", "unknown")}',
        f'action: {payload.get("action", "")}',
        f'project_id: {payload.get("project_id", "")}',
        f'pane_count: {payload.get("pane_count", 0)}',
        f'window_count: {payload.get("window_count", 0)}',
    ]
    if str(payload.get('action') or '') == 'resolve':
        lines.extend(
            [
                f'agent: {payload.get("agent", "")}',
                f'placement_mode: {payload.get("placement_mode", "")}',
                f'resolved_window_name: {payload.get("resolved_window_name", "")}',
                f'target_surface: {payload.get("target_surface", "")}',
                f'target_window_exists: {payload.get("target_window_exists", "")}',
                f'will_create_window: {payload.get("will_create_window", "")}',
                f'target_window_pane_count: {payload.get("target_window_pane_count", 0)}',
                f'addable: {payload.get("addable", "")}',
            ]
        )
    if str(payload.get('action') or '') == 'move-plan':
        lines.extend(
            [
                f'move_plan_status: {payload.get("move_plan_status", "")}',
                f'agent: {payload.get("agent", "")}',
                f'agent_source: {payload.get("agent_source", "")}',
                f'placement_mode: {payload.get("placement_mode", "")}',
                f'source_window_name: {payload.get("source_window_name", "")}',
                f'target_window_name: {payload.get("target_window_name", "")}',
                f'same_window: {payload.get("same_window", "")}',
                f'will_create_window: {payload.get("will_create_window", "")}',
                f'read_only: {payload.get("read_only", "")}',
                f'mutation_performed: {payload.get("mutation_performed", "")}',
                f'apply_command_supported: {payload.get("apply_command_supported", "")}',
            ]
        )
    if str(payload.get('action') or '') == 'arrange':
        lines.extend(
            [
                f'arrange_status: {payload.get("arrange_status", "")}',
                f'window_name: {payload.get("window_name", "")}',
                f'reflowed_windows: {", ".join(str(item) for item in tuple(payload.get("reflowed_windows") or ()))}',
            ]
        )
        reflow_errors = payload.get('reflow_errors') if isinstance(payload.get('reflow_errors'), Mapping) else {}
        for window_name, error in reflow_errors.items():
            lines.append(f'reflow_error: window={window_name} error={error}')
    if str(payload.get('action') or '') == 'status':
        lines.extend(
            [
                f'ccbd_state: {payload.get("ccbd_state", "")}',
                f'windows_explicit: {payload.get("windows_explicit", "")}',
                f'entry_window: {payload.get("entry_window", "")}',
                f'dynamic_agent_count: {payload.get("dynamic_agent_count", 0)}',
                f'loop_agent_count: {payload.get("loop_agent_count", 0)}',
                f'runtime_agent_count: {payload.get("runtime_agent_count", 0)}',
            ]
        )
        namespace = payload.get('namespace') if isinstance(payload.get('namespace'), Mapping) else {}
        if namespace:
            lines.append(
                'namespace: '
                f'status={namespace.get("status", "")} '
                f'state={namespace.get("state_load_status", "")} '
                f'session={namespace.get("tmux_session_name", "")} '
                f'workspace={namespace.get("workspace_window_name", "")}'
            )
            lines.extend(herdr_surface_lines(namespace.get('herdr_surface_projection')))
    for window in tuple(payload.get('windows') or ()):
        if not isinstance(window, Mapping):
            continue
        lines.append(
            'layout_window: '
            f'name={window.get("name", "")} '
            f'index={window.get("index", "")} '
            f'panes={len(tuple(window.get("agent_names") or ()))} '
            f'layout={window.get("layout_spec", "")}'
        )
        if str(payload.get('action') or '') == 'status':
            for agent in tuple(window.get('agents') or ()):
                if not isinstance(agent, Mapping):
                    continue
                lines.append(
                    'layout_agent: '
                    f'window={window.get("name", "")} '
                    f'agent={agent.get("agent", "")} '
                    f'source={agent.get("source", "")} '
                    f'state={agent.get("runtime_state", "")} '
                    f'lifecycle={agent.get("lifecycle_state", "")} '
                    f'pane={agent.get("pane_id", "")}'
                )
    smoke_status = str(payload.get('smoke_status') or '').strip()
    if smoke_status:
        lines.extend(
            [
                f'smoke_status: {smoke_status}',
                f'dynamic_status: {payload.get("dynamic_status", "")}',
                f'event_count: {payload.get("event_count", "")}',
                f'session_name: {payload.get("session_name", "")}',
                f'socket_path: {payload.get("socket_path", "")}',
                f'cleanup_status: {payload.get("cleanup_status", "")}',
            ]
        )
    for observed in tuple(payload.get('observed_windows') or ()):
        if not isinstance(observed, Mapping):
            continue
        lines.append(
            'observed_window: '
            f'name={observed.get("name", "")} '
            f'pane_count={observed.get("pane_count", 0)}'
        )
    for event in tuple(payload.get('dynamic_events') or ())[:20]:
        if not isinstance(event, Mapping):
            continue
        lines.append(
            'dynamic_event: '
            f'phase={event.get("phase", "")} '
            f'operation={event.get("operation", "")} '
            f'agent={event.get("agent", "")} '
            f'target_count={event.get("target_count", 0)} '
            f'window_count={event.get("window_count", 0)} '
            f'all_retained_alive={_render_value(event.get("all_retained_alive"))}'
        )
    reason = str(payload.get('reason') or '').strip()
    if reason:
        lines.append(f'reason: {reason}')
    error = str(payload.get('error') or '').strip()
    if error:
        lines.append(f'error: {error}')
    return tuple(lines)


def render_plan_task(summary) -> tuple[str, ...]:
    payload = summary if isinstance(summary, Mapping) else {}
    task = payload.get('task') if isinstance(payload.get('task'), Mapping) else {}
    lines = [
        f'plan_task_status: {payload.get("plan_task_status", "unknown")}',
        f'action: {payload.get("action", "")}',
        f'task_id: {payload.get("task_id", "")}',
        f'status: {payload.get("status", "")}',
        f'next_owner: {task.get("next_owner", "")}',
        f'activation_reason: {task.get("activation_reason", "")}',
        f'plan_slug: {payload.get("plan_slug", "")}',
        f'task_root: {payload.get("task_root", "")}',
        f'readme_path: {payload.get("readme_path", "")}',
    ]
    title = str(task.get('title') or '').strip()
    if title:
        lines.append(f'title: {title}')
    artifact = payload.get('artifact')
    if isinstance(artifact, Mapping):
        lines.append(
            'artifact: '
            f'kind={artifact.get("kind", "")} '
            f'path={artifact.get("path", "")} '
            f'sha256={artifact.get("sha256", "")}'
        )
    tasks = payload.get('tasks')
    if isinstance(tasks, tuple) or isinstance(tasks, list):
        lines.append(f'task_count: {len(tasks)}')
        for item in tasks:
            if not isinstance(item, Mapping):
                continue
            lines.append(
                'task: '
                f'id={item.get("task_id", "")} '
                f'status={item.get("status", "")} '
                f'title={item.get("title", "")}'
            )
    return tuple(lines)


def render_doctor_bundle(summary) -> tuple[str, ...]:
    return (
        'doctor_bundle_status: ok',
        f'project: {summary.project_root}',
        f'project_id: {summary.project_id}',
        f'bundle_id: {summary.bundle_id}',
        f'bundle_path: {summary.bundle_path}',
        f'file_count: {summary.file_count}',
        f'included_count: {summary.included_count}',
        f'missing_count: {summary.missing_count}',
        f'truncated_count: {summary.truncated_count}',
        f'doctor_error: {summary.doctor_error}',
    )


def render_cleanup(summary) -> tuple[str, ...]:
    lines = [
        f'cleanup_status: {summary.status}',
        f'project_root: {summary.project_root}',
        f'project_id: {summary.project_id}',
        f'cleanup_deleted_bytes: {summary.deleted_bytes}',
        f'cleanup_deleted_count: {summary.deleted_count}',
        f'cleanup_skipped_count: {summary.skipped_count}',
    ]
    for action in getattr(summary, 'actions', ()) or ():
        lines.append(
            'cleanup_action: '
            f'provider={action.provider} '
            f'kind={action.kind} '
            f'bytes={action.bytes_removed} '
            f'reason={action.reason} '
            f'path={action.path}'
        )
    for skipped in getattr(summary, 'skipped', ()) or ():
        lines.append(
            'cleanup_skipped: '
            f'provider={skipped.provider} '
            f'reason={skipped.reason} '
            f'path={skipped.path}'
        )
    return tuple(lines)


def render_clear(summary) -> tuple[str, ...]:
    results = tuple(summary.get('results', ()) or ()) if isinstance(summary, Mapping) else ()
    cleared_count = sum(1 for item in results if item.get('status') == 'cleared')
    skipped_count = sum(1 for item in results if item.get('status') == 'skipped')
    failed_count = sum(1 for item in results if item.get('status') == 'failed')
    lines = [
        f'clear_status: {summary.get("status", "unknown") if isinstance(summary, Mapping) else "unknown"}',
        f'cleared_count: {cleared_count}',
        f'skipped_count: {skipped_count}',
        f'failed_count: {failed_count}',
    ]
    for item in results:
        agent = str(item.get('agent') or '')
        status = str(item.get('status') or '')
        pane_id = str(item.get('pane_id') or '')
        reason = str(item.get('reason') or '')
        confirmed = str(item.get('confirmed') or '')
        detail = f'agent={agent} status={status}'
        if pane_id:
            detail += f' pane_id={pane_id}'
        if reason:
            detail += f' reason={reason}'
        if confirmed:
            # Surface the limited confirmation explicitly: input_delivered
            # means the clear keys reached a live pane, not a provider-native
            # confirmed context reset.
            detail += f' confirmed={confirmed}'
        lines.append(f'clear_agent: {detail}')
    return tuple(lines)


def render_compact(summary) -> tuple[str, ...]:
    results = tuple(summary.get('results', ()) or ()) if isinstance(summary, Mapping) else ()
    compacted_count = sum(1 for item in results if item.get('status') == 'compacted')
    skipped_count = sum(1 for item in results if item.get('status') == 'skipped')
    blocked_count = sum(1 for item in results if item.get('status') == 'blocked')
    unsupported_count = sum(1 for item in results if item.get('status') == 'unsupported')
    failed_count = sum(1 for item in results if item.get('status') == 'failed')
    lines = [
        f'compact_status: {summary.get("status", "unknown") if isinstance(summary, Mapping) else "unknown"}',
        f'compacted_count: {compacted_count}',
        f'skipped_count: {skipped_count}',
        f'blocked_count: {blocked_count}',
        f'unsupported_count: {unsupported_count}',
        f'failed_count: {failed_count}',
    ]
    for item in results:
        agent = str(item.get('agent') or '')
        status = str(item.get('status') or '')
        provider = str(item.get('provider') or '')
        pane_id = str(item.get('pane_id') or '')
        reason = str(item.get('reason') or '')
        command = str(item.get('command') or '')
        detail = f'compact_agent: agent={agent} status={status}'
        for key, value in (('provider', provider), ('pane_id', pane_id), ('command', command), ('reason', reason)):
            if value:
                detail += f' {key}={value}'
        lines.append(detail)
    return tuple(lines)


def render_restart(summary) -> tuple[str, ...]:
    payload = summary if isinstance(summary, Mapping) else {}
    status = str(payload.get('restart_status') or payload.get('status') or 'unknown')
    lines = [
        f'restart_status: {status}',
        f'agent_name: {payload.get("agent_name", "")}',
    ]
    restartable = tuple(str(item) for item in (payload.get('restartable_agents') or ()) if str(item))
    if restartable:
        lines.append(f'restartable_agents: {", ".join(restartable)}')
    reason = str(payload.get('reason') or '').strip()
    if reason:
        lines.append(f'reason: {reason}')
    busy_gate = payload.get('busy_gate')
    if isinstance(busy_gate, Mapping):
        lines.append(_restart_busy_gate_line(busy_gate))
    blockers = tuple(payload.get('blockers') or ())
    for blocker in blockers:
        if isinstance(blocker, Mapping):
            reason_text = str(blocker.get('reason') or '').strip()
            detail = str(blocker.get('detail') or '').strip()
            line = f'blocker: reason={reason_text}'
            if detail:
                line += f' detail={detail}'
            lines.append(line)
        else:
            lines.append(f'blocker: {blocker}')
    old_runtime = payload.get('old_runtime')
    if isinstance(old_runtime, Mapping):
        lines.append(f'old_runtime: {_runtime_evidence_text(old_runtime)}')
    new_runtime = payload.get('new_runtime')
    if isinstance(new_runtime, Mapping):
        lines.append(f'new_runtime: {_runtime_evidence_text(new_runtime)}')
    result = payload.get('result')
    if isinstance(result, Mapping):
        lines.append(f'restart_result: {_flat_mapping_text(result)}')
    error = str(payload.get('error') or '').strip()
    if error:
        lines.append(f'error: {error}')
    return tuple(lines)


def render_maintenance(payload) -> tuple[str, ...]:
    data = payload if isinstance(payload, Mapping) else {}
    status = str(data.get('maintenance_status') or 'unknown')
    lines = [f'maintenance_status: {status}']
    action = str(data.get('action') or '').strip()
    if action:
        lines.append(f'action: {action}')
    reason = str(data.get('reason') or '').strip()
    if reason:
        lines.append(f'reason: {reason}')
    if status == 'not_implemented':
        return tuple(lines)
    runner_status = str(data.get('runner_status') or '').strip()
    if runner_status:
        lines.extend(
            [
                f'runner_status: {runner_status}',
                f'runner_started: {_render_optional(data.get("runner_started"))}',
                f'runner_id: {_render_optional(data.get("runner_id"))}',
                f'runner_pid: {_render_optional(data.get("runner_pid"))}',
                f'runner_exit_reason: {_render_optional(data.get("runner_exit_reason"))}',
                f'runner_iterations: {_render_optional(data.get("runner_iterations"))}',
            ]
        )
    tick_status = str(data.get('tick_status') or '').strip()
    if tick_status:
        lines.extend(
            [
                f'tick_status: {tick_status}',
                f'tick_source_kind: {data.get("tick_source_kind")}',
                f'tick_recommended_action: {data.get("tick_recommended_action")}',
                f'tick_needs_user: {_render_value(data.get("tick_needs_user"))}',
                f'tick_next_heartbeat_after_s: {_render_optional(data.get("tick_next_heartbeat_after_s"))}',
                f'status_written: {_render_value(data.get("status_written"))}',
                f'schedule_written: {_render_value(data.get("schedule_written"))}',
                f'activation_written: {_render_value(data.get("activation_written"))}',
                f'tick_activation_status: {_render_optional(data.get("tick_activation_status"))}',
                f'tick_activation_id: {_render_optional(data.get("tick_activation_id"))}',
                f'tick_activation_job_id: {_render_optional(data.get("tick_activation_job_id"))}',
            ]
        )
        summary = data.get('tick_summary')
        if isinstance(summary, Mapping):
            lines.extend(_maintenance_summary_lines('tick_summary', summary))
        evidence = data.get('tick_evidence')
        if isinstance(evidence, (list, tuple)):
            lines.append(f'tick_evidence_count: {len(evidence)}')
            for item in evidence[:5]:
                if isinstance(item, Mapping):
                    lines.append(_maintenance_evidence_line('tick_evidence', item))

    lines.extend(
        [
            f'project: {data.get("project")}',
            f'project_id: {data.get("project_id")}',
            f'config_source_kind: {data.get("config_source_kind")}',
            f'config_source: {data.get("config_source") or "<builtin>"}',
            f'heartbeat_enabled: {_render_value(data.get("enabled"))}',
            f'heartbeat_assessor: {data.get("assessor")}',
            f'heartbeat_assessor_present: {_render_value(data.get("assessor_present"))}',
            f'heartbeat_interval_s: {data.get("interval_s")}',
            f'heartbeat_min_interval_s: {data.get("min_interval_s")}',
            f'heartbeat_unknown_streak_cap: {data.get("unknown_streak_cap")}',
            f'heartbeat_escalation_policy: {data.get("escalation_policy")}',
            f'heartbeat_startup_ensure: {_render_value(data.get("startup_ensure"))}',
        ]
    )
    schedule = data.get('schedule')
    if isinstance(schedule, Mapping):
        lines.extend(_maintenance_record_lines('schedule', schedule))
    last_status = data.get('last_status')
    if isinstance(last_status, Mapping):
        lines.extend(_maintenance_record_lines('last_status', last_status))
    runner = data.get('runner')
    if isinstance(runner, Mapping):
        lines.extend(_maintenance_record_lines('runner', runner))
    last_activation = data.get('last_activation')
    if isinstance(last_activation, Mapping):
        lines.extend(_maintenance_record_lines('last_activation', last_activation))
    return tuple(lines)


def render_mobile_serve(summary) -> tuple[str, ...]:
    payload = summary if isinstance(summary, Mapping) else {}
    status = str(payload.get('mobile_status') or 'unknown')
    if status == 'devices':
        lines = [
            'mobile_status: devices',
            f'project_id: {payload.get("project_id", "")}',
            f'project_root: {payload.get("project_root", "")}',
            f'mobile_state_dir: {payload.get("mobile_state_dir", "")}',
        ]
        devices = payload.get('devices')
        if not isinstance(devices, (list, tuple)) or not devices:
            lines.append('devices: none')
            return tuple(lines)
        for device in devices:
            if not isinstance(device, Mapping):
                continue
            scopes = device.get('scopes')
            scope_text = ','.join(str(item) for item in scopes) if isinstance(scopes, (list, tuple)) else ''
            lines.append(
                'device: '
                f'id={device.get("device_id", "")} '
                f'name={device.get("name", "")} '
                f'revoked={str(bool(device.get("revoked"))).lower()} '
                f'route_provider={device.get("route_provider", "")} '
                f'scopes={scope_text} '
                f'last_seen_at={device.get("last_seen_at", "")}'
            )
        return tuple(lines)
    if status == 'revoked':
        device = payload.get('device') if isinstance(payload.get('device'), Mapping) else {}
        return (
            'mobile_status: revoked',
            f'project_id: {payload.get("project_id", "")}',
            f'project_root: {payload.get("project_root", "")}',
            f'mobile_state_dir: {payload.get("mobile_state_dir", "")}',
            f'device_id: {device.get("device_id", "")}',
            f'device_revoked: {str(bool(device.get("revoked"))).lower()}',
            f'revoked_at: {device.get("revoked_at", "")}',
            f'revoked_terminal_count: {payload.get("revoked_terminal_count", 0)}',
        )
    lines = [
        f'mobile_status: {status}',
        f'listen: {payload.get("listen", "")}',
        f'gateway_url: {payload.get("gateway_url", "")}',
        f'route_provider: {payload.get("route_provider", "")}',
        f'project_id: {payload.get("project_id", "")}',
        f'project_root: {payload.get("project_root", "")}',
        f'mode: {payload.get("mode", "")}',
    ]
    if payload.get('host_id'):
        lines.insert(4, f'host_id: {payload.get("host_id", "")}')
    if payload.get('mobile_state_dir'):
        lines.append(f'mobile_state_dir: {payload.get("mobile_state_dir", "")}')
    if 'project_count' in payload:
        lines.append(f'project_count: {payload.get("project_count", 0)}')
    projects = payload.get('projects')
    if isinstance(projects, (list, tuple)):
        for project in projects:
            if not isinstance(project, Mapping):
                continue
            lines.append(
                'project: '
                f'id={project.get("id", "")} '
                f'name={project.get("display_name", "")} '
                f'health={project.get("health", "")} '
                f'root={project.get("root", "")}'
            )
    endpoints = payload.get('endpoints')
    if isinstance(endpoints, (list, tuple)):
        lines.append(f'endpoints: {", ".join(str(item) for item in endpoints)}')
    push_sender = payload.get('push_sender')
    if isinstance(push_sender, Mapping):
        details = [
            f'provider={push_sender.get("provider", "")}',
            f'configured={str(bool(push_sender.get("configured"))).lower()}',
        ]
        if 'ready' in push_sender:
            details.append(f'ready={str(bool(push_sender.get("ready"))).lower()}')
        if push_sender.get('credential_source'):
            details.append(f'credential_source={push_sender.get("credential_source", "")}')
        if push_sender.get('reason'):
            details.append(f'reason={push_sender.get("reason", "")}')
        if push_sender.get('timeout_seconds') is not None:
            details.append(f'timeout_seconds={push_sender.get("timeout_seconds")}')
        if push_sender.get('max_workers') is not None:
            details.append(f'max_workers={push_sender.get("max_workers")}')
        lines.append('push_sender: ' + ' '.join(details))
    pairing = payload.get('pairing')
    if isinstance(pairing, Mapping):
        lines.extend(
            [
                f'pairing_code: {pairing.get("pairing_code", "")}',
                f'pairing_expires_at: {pairing.get("expires_at", "")}',
                f'pairing_claim_endpoint: {pairing.get("claim_endpoint", "")}',
            ]
        )
    relay_outbound = payload.get('relay_outbound')
    if isinstance(relay_outbound, Mapping):
        lines.extend(
            [
                f'relay_outbound_status: {relay_outbound.get("status", "")}',
                f'relay_outbound_mode: {relay_outbound.get("mode", "")}',
                f'relay_outbound_host_id: {relay_outbound.get("host_id", "")}',
            ]
        )
    return tuple(lines)


def render_relay_operator(summary) -> tuple[str, ...]:
    payload = summary if isinstance(summary, Mapping) else {}
    status = str(payload.get('relay_status') or 'unknown')
    lines = [f'relay_status: {status}']
    if payload.get('db_path') not in (None, ''):
        lines.append(f'db_path: {payload.get("db_path", "")}')
    if status == 'host_activated':
        lines.extend(
            f'{key}: {payload.get(key, "")}'
            for key in (
                'relay_origin',
                'host_id',
                'invitation_id',
                'host_fingerprint',
                'credential_path',
                'activated_at',
            )
        )
        return tuple(lines)
    if status == 'invite_issued':
        lines.extend(
            [
                f'invite_id: {payload.get("invite_id", "")}',
                f'invitation: {payload.get("invitation", "")}',
                f'state: {payload.get("state", "")}',
                f'expires_at: {payload.get("expires_at", "")}',
            ]
        )
        quota = payload.get('quota')
        if isinstance(quota, Mapping):
            lines.append(_relay_quota_line(quota))
        return tuple(lines)
    if status in {'invite_status', 'host_status'}:
        lines.extend(_relay_record_lines(payload))
        return tuple(lines)
    if status == 'invite_list':
        items = payload.get('invitations')
        if not isinstance(items, (list, tuple)) or not items:
            lines.append('invitations: none')
            return tuple(lines)
        for item in items:
            if isinstance(item, Mapping):
                lines.append(_relay_invite_summary_line(item))
        return tuple(lines)
    if status == 'host_list':
        items = payload.get('hosts')
        if not isinstance(items, (list, tuple)) or not items:
            lines.append('hosts: none')
            return tuple(lines)
        for item in items:
            if isinstance(item, Mapping):
                lines.append(_relay_host_summary_line(item))
        return tuple(lines)
    lines.extend(_relay_record_lines(payload))
    return tuple(lines)


def _relay_record_lines(payload: Mapping[str, object]) -> list[str]:
    keys = (
        'invite_id',
        'host_id',
        'invitation_id',
        'state',
        'issued_at',
        'expires_at',
        'consumed_at',
        'revoked_at',
        'host_public_key_sha256',
    )
    lines = [f'{key}: {payload.get(key, "")}' for key in keys if payload.get(key) not in (None, '')]
    quota = payload.get('quota')
    if isinstance(quota, Mapping):
        lines.append(_relay_quota_line(quota))
    return lines


def _relay_invite_summary_line(payload: Mapping[str, object]) -> str:
    return (
        'invite: '
        f'id={payload.get("invite_id", "")} '
        f'state={payload.get("state", "")} '
        f'expires_at={payload.get("expires_at", "")} '
        f'host_id={payload.get("host_id", "")}'
    )


def _relay_host_summary_line(payload: Mapping[str, object]) -> str:
    return (
        'host: '
        f'id={payload.get("host_id", "")} '
        f'state={payload.get("state", "")} '
        f'invitation_id={payload.get("invitation_id", "")} '
        f'pubkey={payload.get("host_public_key_sha256", "")}'
    )


def _relay_quota_line(quota: Mapping[str, object]) -> str:
    return (
        'quota: '
        f'max_sessions={quota.get("max_sessions", "")} '
        f'max_bytes_per_day={quota.get("max_bytes_per_day", "")}'
    )


def _maintenance_record_lines(prefix: str, payload: Mapping[str, object]) -> list[str]:
    lines = [
        f'{prefix}_state: {payload.get("state")}',
        f'{prefix}_path: {payload.get("path")}',
    ]
    error = str(payload.get('error') or '').strip()
    if error:
        lines.append(f'{prefix}_error: {error}')
    record = payload.get('record')
    if isinstance(record, Mapping):
        for key in (
            'next_run_at',
            'reason',
            'updated_at',
            'updated_by',
            'last_tick_status',
            'last_tick_at',
            'last_ok_at',
            'last_error',
            'unknown_streak',
            'source_kind',
            'recommended_action',
            'next_heartbeat_after_s',
            'needs_user',
            'last_activation_status',
            'last_activation_id',
            'last_activation_job_id',
            'last_activation_target',
            'last_activation_dedup_key',
            'runner_id',
            'pid',
            'state',
            'started_at',
            'last_seen_at',
            'last_wake_at',
            'last_tick_at',
            'last_tick_status',
            'observed_next_run_at',
            'sleep_until',
            'exit_reason',
            'activation_id',
            'status',
            'condition_kind',
            'trigger_kind',
            'source',
            'observed_at',
            'target_agent',
            'delivery_mode',
            'payload_kind',
            'dedup_key',
            'job_id',
            'submitted_at',
            'suppressed_reason',
            'repeat_count',
        ):
            if key in record:
                lines.append(f'{prefix}_{key}: {_render_value(record.get(key))}')
        summary = record.get('summary')
        if isinstance(summary, Mapping):
            lines.extend(_maintenance_summary_lines(f'{prefix}_summary', summary))
        evidence = record.get('evidence')
        if isinstance(evidence, (list, tuple)):
            lines.append(f'{prefix}_evidence_count: {len(evidence)}')
    return lines


def _maintenance_summary_lines(prefix: str, payload: Mapping[str, object]) -> list[str]:
    lines: list[str] = []
    for key in (
        'source_kind',
        'ccbd_state',
        'agent_count',
        'active_agent_count',
        'pending_agent_count',
        'idle_agent_count',
        'offline_agent_count',
        'failed_agent_count',
        'concern_agent_count',
        'unknown_agent_count',
        'comms_count',
        'active_comms_count',
        'concern_comms_count',
        'failing_comms_count',
        'suspicion_count',
        'fallback_error',
    ):
        if key in payload:
            lines.append(f'{prefix}_{key}: {_render_value(payload.get(key))}')
    return lines


def _maintenance_evidence_line(prefix: str, payload: Mapping[str, object]) -> str:
    parts = [f'{prefix}:']
    for key in (
        'health',
        'kind',
        'condition_kind',
        'agent',
        'job_id',
        'target',
        'reason',
        'source',
        'status',
        'ccbd_state',
        'confidence',
    ):
        value = payload.get(key)
        if value is not None and value != '':
            parts.append(f'{key}={value}')
    return ' '.join(parts)


def _render_optional(value: object) -> str:
    if value is None:
        return '<none>'
    return _render_value(value)


def _restart_busy_gate_line(gate: Mapping[str, object]) -> str:
    fields = {
        'passed': str(bool(gate.get('passed'))).lower(),
        'runtime_state': gate.get('runtime_state'),
        'runtime_queue_depth': gate.get('runtime_queue_depth'),
        'queue_depth': gate.get('queue_depth'),
        'pending_reply_count': gate.get('pending_reply_count'),
        'active_job_id': gate.get('active_job_id'),
        'active_inbound_event_id': gate.get('active_inbound_event_id'),
        'pending_callback_count': gate.get('pending_callback_count'),
    }
    return 'restart_busy_gate: ' + _flat_mapping_text(fields)


def _runtime_evidence_text(evidence: Mapping[str, object]) -> str:
    fields = {
        'state': evidence.get('state'),
        'health': evidence.get('health'),
        'pane_id': evidence.get('pane_id'),
        'active_pane_id': evidence.get('active_pane_id'),
        'runtime_ref': evidence.get('runtime_ref'),
        'session_ref': evidence.get('session_ref'),
        'runtime_pid': evidence.get('runtime_pid'),
        'restart_count': evidence.get('restart_count'),
    }
    return _flat_mapping_text(fields)


def _flat_mapping_text(payload: Mapping[str, object]) -> str:
    return ' '.join(f'{key}={_render_value(value)}' for key, value in payload.items())


def _render_value(value: object) -> str:
    if value is None:
        return 'None'
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (list, tuple)):
        return ','.join(str(item) for item in value)
    return str(value).replace('\n', '\\n')


def render_kill(summary) -> tuple[str, ...]:
    lines = [
        'kill_status: ok',
        f'project_id: {summary.project_id}',
        f'state: {summary.state}',
        f'socket_path: {summary.socket_path}',
        f'forced: {str(summary.forced).lower()}',
    ]
    lines.extend(f'kill_action: {action}' for action in getattr(summary, 'runtime_actions', ()) or ())
    lines.extend(f'kill_warning: {warning}' for warning in getattr(summary, 'runtime_warnings', ()) or ())
    lines.extend(render_tmux_cleanup_summaries(getattr(summary, 'cleanup_summaries', ()) or ()))
    return tuple(lines)


def render_ps(payload: Mapping[str, object]) -> tuple[str, ...]:
    lines = [
        f'project_id: {payload["project_id"]}',
        f'ccbd_state: {payload["ccbd_state"]}',
    ]
    lines.extend(herdr_surface_lines(payload.get('herdr_surface_projection')))
    for agent in payload['agents']:
        lines.append(
            f'agent: name={agent["agent_name"]} state={agent["state"]} provider={agent["provider"]} queue={agent["queue_depth"]}'
        )
        lines.append(binding_line(agent))
    return tuple(lines)


__all__ = [
    'render_clear',
    'render_compact',
    'render_cleanup',
    'render_config_validate',
    'render_doctor_bundle',
    'render_kill',
    'render_logs',
    'render_layout',
    'render_loop_capacity',
    'render_loop_topology',
    'render_loop_run_once',
    'render_loop_runner',
    'render_maintenance',
    'render_mobile_serve',
    'render_relay_operator',
    'render_plan_task',
    'render_ps',
    'render_restart',
    'render_start',
]
