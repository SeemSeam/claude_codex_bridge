from __future__ import annotations

import json
from pathlib import Path
import shutil

from agents.config_loader import load_project_config
from agents.store import AgentRuntimeStore
from ccbd.services.project_namespace_state import ProjectNamespaceStateStore
from terminal_runtime.backend_selection import make_terminal_backend

from .agent_status_diagnostics import (
    agent_kind,
    dispatch_state,
    failed_apply,
    normalize_apply_payload,
    ownership_class,
    pane_identity_source,
)
from .daemon import ping_local_state


def layout_status(context) -> dict[str, object]:
    loaded = load_project_config(context.project.project_root, include_loop_overlays=True)
    config = loaded.config
    local = ping_local_state(context)
    namespace = _namespace_record(context, local)
    observed = _observe_project_namespace(namespace)
    dynamic_by_agent = _dynamic_records_by_agent(context)
    loop_by_agent = _loop_records_by_agent(context)
    runtime_store = AgentRuntimeStore(context.paths)
    windows = [
        _window_status(
            window=window,
            agents_by_name=dict(config.agents),
            dynamic_by_agent=dynamic_by_agent,
            loop_by_agent=loop_by_agent,
            runtime_store=runtime_store,
            observed_by_agent=dict(observed.get('agent_panes') or {}),
            observed_by_window=dict(observed.get('windows') or {}),
        )
        for window in tuple(config.windows or ())
    ]
    return {
        'layout_status': 'ok',
        'action': 'status',
        'project_id': context.project.project_id,
        'project_root': str(context.project.project_root),
        'config_source': str(getattr(loaded, 'source_path', None) or context.paths.config_path),
        'ccbd_state': getattr(local, 'mount_state', None),
        'windows_explicit': bool(getattr(config, 'windows_explicit', False)),
        'entry_window': getattr(config, 'entry_window', None),
        'topology_signature': getattr(config, 'topology_signature', None),
        'pane_count': sum(len(tuple(window.get('agent_names') or ())) for window in windows),
        'window_count': len(windows),
        'dynamic_agent_count': sum(
            1
            for window in windows
            for agent in tuple(window.get('agents') or ())
            if agent.get('source') == 'dynamic'
        ),
        'loop_agent_count': sum(
            1
            for window in windows
            for agent in tuple(window.get('agents') or ())
            if agent.get('source') == 'loop'
        ),
        'runtime_agent_count': sum(
            1
            for window in windows
            for agent in tuple(window.get('agents') or ())
            if agent.get('runtime_state') != 'missing'
        ),
        'namespace': namespace,
        'observed': {
            key: value
            for key, value in observed.items()
            if key not in {'agent_panes', 'windows'}
        },
        'windows': windows,
    }


def _window_status(
    *,
    window,
    agents_by_name: dict[str, object],
    dynamic_by_agent: dict[str, dict[str, object]],
    loop_by_agent: dict[str, dict[str, object]],
    runtime_store: AgentRuntimeStore,
    observed_by_agent: dict[str, dict[str, object]],
    observed_by_window: dict[str, dict[str, object]],
) -> dict[str, object]:
    agent_names = tuple(window.agent_names or ())
    agents = [
        _agent_status(
            agent_name=agent_name,
            window_name=str(window.name),
            spec=agents_by_name.get(agent_name),
            dynamic=dynamic_by_agent.get(agent_name),
            loop=loop_by_agent.get(agent_name),
            runtime=runtime_store.load_best_effort(agent_name),
            observed=observed_by_agent.get(agent_name),
        )
        for agent_name in agent_names
    ]
    observed_window = observed_by_window.get(str(window.name)) or {}
    return {
        'name': str(window.name),
        'index': int(window.order),
        'order': int(window.order),
        'layout_spec': str(window.layout_spec),
        'agent_names': list(agent_names),
        'tool_names': list(getattr(window, 'tool_names', ()) or ()),
        'pane_count': len(agent_names),
        'runtime_pane_count': len(tuple(observed_window.get('panes') or ())),
        'observed': observed_window or None,
        'agents': agents,
    }


def _agent_status(*, agent_name: str, window_name: str, spec, dynamic, loop, runtime, observed) -> dict[str, object]:
    dynamic_payload = dict(dynamic or {})
    loop_payload = dict(loop or {})
    placement_source = dynamic_payload if dynamic is not None else loop_payload
    placement = placement_source.get('placement') if isinstance(placement_source.get('placement'), dict) else {}
    placement = dict(placement)
    pane_id = (
        _runtime_attr(runtime, 'active_pane_id')
        or _runtime_attr(runtime, 'pane_id')
        or _optional_text(dynamic_payload.get('pane_id'))
        or _optional_text(loop_payload.get('pane_id'))
        or _optional_text(placement.get('pane_id'))
    )
    observed_payload = dict(observed or {})
    source = 'dynamic' if dynamic is not None else ('loop' if loop is not None else 'configured')
    dispatch_disabled = bool(
        getattr(spec, 'dispatch_disabled', False)
        or dynamic_payload.get('dispatch_disabled')
        or loop_payload.get('dispatch_disabled')
    )
    runtime_pane_id = _runtime_attr(runtime, 'active_pane_id') or _runtime_attr(runtime, 'pane_id')
    record_pane_id = (
        _optional_text(dynamic_payload.get('pane_id'))
        or _optional_text(loop_payload.get('pane_id'))
        or _optional_text(placement.get('pane_id'))
    )
    apply_payload = normalize_apply_payload(dynamic_payload, loop_payload)
    return {
        'agent': agent_name,
        'source': source,
        'agent_kind': agent_kind(source),
        'ownership_class': ownership_class(source=source, lifetime=dynamic_payload.get('lifetime')),
        'provider': getattr(spec, 'provider', None),
        'role': (
            dynamic_payload.get('role')
            if dynamic is not None
            else (loop_payload.get('role') if loop is not None else getattr(spec, 'role', None))
        ),
        'profile': dynamic_payload.get('profile') if dynamic is not None else loop_payload.get('profile'),
        'role_class': dynamic_payload.get('role_class'),
        'loop_id': loop_payload.get('loop_id'),
        'node_id': loop_payload.get('node_id'),
        'lifecycle_state': (
            dynamic_payload.get('lifecycle_state')
            if dynamic is not None
            else (loop_payload.get('state') if loop is not None else 'configured')
        ),
        'visibility_state': (
            dynamic_payload.get('visibility_state')
            if dynamic is not None
            else ('loop' if loop is not None else 'visible')
        ),
        'dispatch_disabled': dispatch_disabled,
        'dispatch_state': dispatch_state(dispatch_disabled),
        'window_name': _runtime_attr(runtime, 'tmux_window_name') or _optional_text(placement.get('window_name')) or window_name,
        'pane_id': pane_id,
        'pane_identity_source': pane_identity_source(
            observed=observed_payload.get('pane_id'),
            runtime=runtime_pane_id,
            record=record_pane_id,
        ),
        'runtime_state': _runtime_state_value(runtime),
        'queue_depth': _runtime_attr(runtime, 'queue_depth', 0) if runtime is not None else 0,
        'pane_state': _runtime_attr(runtime, 'pane_state') or observed_payload.get('pane_state'),
        'apply_status': apply_payload.get('apply_status'),
        'apply_plan_class': apply_payload.get('plan_class'),
        'apply_stage': apply_payload.get('stage'),
        'failed_apply': failed_apply(apply_payload),
        'retained_busy': bool(dynamic_payload.get('retained_busy')),
        'observed': observed_payload or None,
        'ask_target': dynamic_payload.get('ask_target') or loop_payload.get('ask_target') or agent_name,
        'state_path': dynamic_payload.get('state_path') or loop_payload.get('state_path'),
    }


def _namespace_record(context, local) -> dict[str, object]:
    status = str(getattr(local, 'mount_state', '') or 'unknown')
    payload: dict[str, object] = {
        'status': status,
        'project_id': context.project.project_id,
        'socket_connectable': bool(getattr(local, 'socket_connectable', False)),
        'tmux_socket_path': getattr(local, 'tmux_socket_path', None),
        'state_path': str(context.paths.ccbd_state_path),
    }
    try:
        state = ProjectNamespaceStateStore(context.paths).load()
    except Exception as exc:
        payload.update(
            {
                'state_load_status': 'failed',
                'error_type': type(exc).__name__,
                'error': str(exc),
            }
        )
        return payload
    if state is None:
        payload['state_load_status'] = 'missing'
        return payload
    payload.update(
        {
            'state_load_status': 'ok',
            'namespace_epoch': state.namespace_epoch,
            'tmux_socket_path': state.tmux_socket_path,
            'tmux_session_name': state.tmux_session_name,
            'layout_version': state.layout_version,
            'layout_signature': state.layout_signature,
            'workspace_window_name': state.workspace_window_name,
            'workspace_window_id': state.workspace_window_id,
            'ui_attachable': state.ui_attachable,
        }
    )
    return payload


def _observe_project_namespace(namespace: dict[str, object]) -> dict[str, object]:
    if str(namespace.get('state_load_status') or '') != 'ok':
        return {'observe_status': 'skipped', 'reason': 'namespace_state_missing'}
    if str(namespace.get('status') or '') != 'mounted':
        return {'observe_status': 'skipped', 'reason': 'namespace_unmounted'}
    if not bool(namespace.get('socket_connectable')):
        return {'observe_status': 'skipped', 'reason': 'namespace_socket_unreachable'}
    if not bool(namespace.get('ui_attachable', True)):
        return {'observe_status': 'skipped', 'reason': 'namespace_not_attachable'}
    if shutil.which('tmux') is None:
        return {'observe_status': 'skipped', 'reason': 'tmux_not_found'}
    socket_path = _optional_text(namespace.get('tmux_socket_path'))
    session_name = _optional_text(namespace.get('tmux_session_name'))
    if socket_path is None or session_name is None:
        return {'observe_status': 'skipped', 'reason': 'namespace_tmux_scope_missing'}
    backend = make_terminal_backend(socket_path=socket_path)
    try:
        result = backend._tmux_run(
            [
                'list-panes',
                '-a',
                '-t',
                session_name,
                '-F',
                '#{window_name}\t#{window_id}\t#{pane_id}\t#{pane_title}\t#{@ccb_agent}\t#{@ccb_slot}\t#{@ccb_window}\t#{@ccb_project_id}\t#{@ccb_managed_by}\t#{pane_active}\t#{pane_dead}\t#{pane_index}\t#{pane_left}\t#{pane_top}\t#{pane_width}\t#{pane_height}',
            ],
            check=True,
            capture=True,
            timeout=1.0,
        )
    except Exception as exc:
        return {
            'observe_status': 'failed',
            'error_type': type(exc).__name__,
            'error': str(exc),
        }
    windows: dict[str, dict[str, object]] = {}
    agent_panes: dict[str, dict[str, object]] = {}
    project_id = _optional_text(namespace.get('project_id'))
    for line in (result.stdout or '').splitlines():
        record = _observed_pane_record(line)
        if record is None:
            continue
        ccb_window = _optional_text(record.get('ccb_window')) or str(record.get('window_name') or '')
        window = windows.setdefault(ccb_window, {'name': ccb_window, 'panes': []})
        window['panes'].append(record)
        agent = _optional_text(record.get('ccb_agent')) or _optional_text(record.get('ccb_slot'))
        if agent is not None and (project_id is None or record.get('ccb_project_id') == project_id):
            agent_panes[agent] = record
    return {
        'observe_status': 'ok',
        'observed_pane_count': sum(len(tuple(window.get('panes') or ())) for window in windows.values()),
        'windows': windows,
        'agent_panes': agent_panes,
    }


def _observed_pane_record(line: str) -> dict[str, object] | None:
    parts = line.split('\t')
    if len(parts) < 11:
        return None
    return {
        'window_name': parts[0],
        'window_id': parts[1],
        'pane_id': parts[2],
        'pane_title': parts[3],
        'ccb_agent': parts[4],
        'ccb_slot': parts[5],
        'ccb_window': parts[6],
        'ccb_project_id': parts[7],
        'ccb_managed_by': parts[8],
        'pane_active': parts[9] == '1',
        'pane_state': 'dead' if parts[10] == '1' else 'alive',
        'pane_index': _optional_int(parts[11]) if len(parts) > 11 else None,
        'pane_left': _optional_int(parts[12]) if len(parts) > 12 else None,
        'pane_top': _optional_int(parts[13]) if len(parts) > 13 else None,
        'pane_width': _optional_int(parts[14]) if len(parts) > 14 else None,
        'pane_height': _optional_int(parts[15]) if len(parts) > 15 else None,
    }


def _dynamic_records_by_agent(context) -> dict[str, dict[str, object]]:
    records = {}
    root = Path(context.paths.runtime_state_root) / 'runtime' / 'agents'
    if not root.is_dir():
        return records
    for path in sorted(root.glob('*/lifecycle.json')):
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        if str(payload.get('lifecycle_state') or '') not in {'visible', 'hidden', 'parked'}:
            continue
        agent = _optional_text(payload.get('agent')) or path.parent.name
        payload = dict(payload)
        payload.setdefault('state_path', str(path))
        records[agent] = payload
    return records


def _loop_records_by_agent(context) -> dict[str, dict[str, object]]:
    records = {}
    root = Path(context.paths.runtime_state_root) / 'runtime' / 'loops'
    if not root.is_dir():
        return records
    for path in sorted(root.glob('*/capacity.json')):
        try:
            payload = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        if str(payload.get('loop_capacity_status') or '') != 'ensured':
            continue
        loop_state_path = str(path)
        for raw_agent in tuple(payload.get('agents') or ()):
            if not isinstance(raw_agent, dict):
                continue
            if str(raw_agent.get('state') or '') == 'released':
                continue
            agent = _optional_text(raw_agent.get('name'))
            if agent is None:
                continue
            record = dict(raw_agent)
            record.setdefault('loop_id', payload.get('loop_id'))
            record.setdefault('state_path', loop_state_path)
            records[agent] = record
    return records


def _runtime_attr(runtime, name: str, default=None):
    if runtime is None:
        return default
    return getattr(runtime, name, default)


def _runtime_state_value(runtime) -> str:
    if runtime is None:
        return 'missing'
    value = getattr(runtime, 'state', None)
    return str(getattr(value, 'value', value) or 'unknown')


def _optional_text(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_int(value: object | None) -> int | None:
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


__all__ = ['layout_status']
