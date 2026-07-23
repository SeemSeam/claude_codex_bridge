from __future__ import annotations

import os
from pathlib import Path
import shutil
import tempfile
from types import SimpleNamespace

from agents.config_loader import load_project_config
from agents.config_loader_runtime.dynamic_agent_overlays import (
    DEFAULT_MAX_PANES_PER_DYNAMIC_WINDOW,
    resolve_dynamic_placement_window,
)
from agents.models import AgentValidationError, build_pane_growth_windows, normalize_agent_name
from ccbd.services.project_namespace_state import ProjectNamespaceStateStore
from ccbd.services.project_namespace_runtime.additive_patch_windows import WindowPatchResult
from ccbd.services.project_namespace_runtime.backend import (
    build_backend,
    create_session,
    create_window,
    ensure_server_policy,
    kill_server,
    kill_window,
    session_alive,
    session_window_target,
    split_pane,
    window_root_pane,
)
from ccbd.services.project_namespace_runtime.remove_patch_agents import reflow_window_after_agent_change
from terminal_runtime import TmuxBackend
from terminal_runtime.backend_selection import make_terminal_backend
from terminal_runtime.tmux_identity import apply_ccb_pane_identity

from .daemon import ping_local_state
from .layout_status import layout_status


def layout_command(context, command) -> dict[str, object]:
    action = str(command.action or '').strip().lower()
    if action == 'status':
        return layout_status(context)
    if action == 'resolve':
        return _resolve_layout_placement(context, command)
    if action == 'move-plan':
        return _move_plan_layout_agent(context, command)
    if action == 'arrange':
        return _arrange_layout_window(context, command)
    names = tuple(f'p{index}' for index in range(1, int(command.panes) + 1))
    windows = build_pane_growth_windows(
        names,
        window_prefix=str(command.window_prefix or 'layout'),
    )
    payload: dict[str, object] = {
        'layout_status': 'planned',
        'action': command.action,
        'project_id': context.project.project_id,
        'pane_count': len(names),
        'window_count': len(windows),
        'windows': [window.to_record() for window in windows],
    }
    if command.action == 'dynamic-smoke':
        payload.update(_run_layout_dynamic_smoke(context, command, names))
        return payload
    if command.action != 'smoke':
        return payload
    smoke = _run_layout_smoke(context, command, windows)
    payload.update(smoke)
    return payload


def _arrange_layout_window(context, command) -> dict[str, object]:
    window_name = _optional_text(getattr(command, 'window_name', None))
    if window_name is None:
        raise ValueError('layout arrange requires --window')
    loaded = load_project_config(context.project.project_root, include_loop_overlays=True)
    config = loaded.config
    if _window_record(config, window_name) is None:
        return _arrange_blocked(
            context,
            window_name=window_name,
            reason='window_not_configured',
            message=f'window {window_name!r} is not present in the effective layout',
            loaded=loaded,
            config=config,
        )
    local = ping_local_state(context)
    if str(getattr(local, 'mount_state', '') or '') != 'mounted' or not bool(getattr(local, 'socket_connectable', False)):
        return _arrange_blocked(
            context,
            window_name=window_name,
            reason='namespace_not_mounted',
            message='layout arrange requires a mounted project namespace',
            loaded=loaded,
            config=config,
        )
    state = ProjectNamespaceStateStore(context.paths).load()
    if state is None:
        return _arrange_blocked(
            context,
            window_name=window_name,
            reason='namespace_state_missing',
            message='project namespace state is missing',
            loaded=loaded,
            config=config,
        )
    backend = build_backend(TmuxBackend, socket_path=state.tmux_socket_path)
    if not session_alive(backend, state.tmux_session_name, timeout_s=float(getattr(command, 'timeout_s', 5.0) or 5.0)):
        return _arrange_blocked(
            context,
            window_name=window_name,
            reason='session_unavailable',
            message='project namespace tmux session is not alive',
            loaded=loaded,
            config=config,
        )
    controller = SimpleNamespace(
        _project_id=context.project.project_id,
        _layout=SimpleNamespace(
            project_root=context.project.project_root,
            ccbd_socket_path=context.paths.ccbd_socket_path,
        ),
    )
    result = WindowPatchResult()
    timeout_s = float(getattr(command, 'timeout_s', 5.0) or 5.0)
    reflow_window_after_agent_change(
        controller,
        backend,
        current=state,
        topology_plan=config,
        window_name=window_name,
        result=result,
        timeout_s=timeout_s,
    )
    reflow_errors = dict(result.reflow_errors)
    reflowed_windows = list(result.reflowed_windows)
    after = layout_status(context)
    arrange_ok = window_name in reflowed_windows and window_name not in reflow_errors
    return {
        'layout_status': 'ok' if arrange_ok else 'failed',
        'arrange_status': 'ok' if arrange_ok else 'failed',
        'action': 'arrange',
        'project_id': context.project.project_id,
        'project_root': str(context.project.project_root),
        'config_source_kind': loaded.source_kind,
        'config_source': str(loaded.source_path or '<builtin>'),
        'window_name': window_name,
        'timeout_s': timeout_s,
        'reason': '' if arrange_ok else reflow_errors.get(window_name, 'window_not_reflowed'),
        'reflowed_windows': reflowed_windows,
        'reflow_errors': reflow_errors,
        'namespace': after.get('namespace'),
        'observed': after.get('observed'),
        'pane_count': after.get('pane_count', 0),
        'window_count': after.get('window_count', 0),
        'windows': after.get('windows', []),
    }


def _arrange_blocked(context, *, window_name: str, reason: str, message: str, loaded, config) -> dict[str, object]:
    return {
        'layout_status': 'failed',
        'arrange_status': 'blocked',
        'action': 'arrange',
        'project_id': context.project.project_id,
        'project_root': str(context.project.project_root),
        'config_source_kind': loaded.source_kind,
        'config_source': str(loaded.source_path or '<builtin>'),
        'window_name': window_name,
        'reason': reason,
        'message': message,
        'pane_count': sum(_window_counts(config).values()),
        'window_count': len(tuple(config.windows or ())),
    }


def _move_plan_layout_agent(context, command) -> dict[str, object]:
    loaded = load_project_config(context.project.project_root, include_loop_overlays=True)
    config = loaded.config
    agent_name = _normalize_agent_name(getattr(command, 'agent_name', None))
    source_window = _agent_window_record(config, agent_name)
    if source_window is None:
        return _move_plan_blocked(
            context,
            loaded=loaded,
            config=config,
            agent_name=agent_name,
            placement=_placement_request(command),
            reason='agent_not_in_effective_layout',
            message=f'agent {agent_name!r} is not present in the effective layout',
        )
    placement = _placement_request(command)
    if str(placement.get('mode') or '') == 'auto':
        return _move_plan_blocked(
            context,
            loaded=loaded,
            config=config,
            agent_name=agent_name,
            placement=placement,
            source_window=source_window,
            reason='target_required',
            message='layout move-plan requires a target window, window class, or execution-node placement',
        )
    window_counts = _window_counts(config)
    source_window_name = str(source_window.get('name') or '')
    if source_window_name:
        window_counts[source_window_name] = max(0, int(window_counts.get(source_window_name, 0)) - 1)
    state = {
        'agent': agent_name,
        'window_name': placement.get('window_name'),
        'window_class': placement.get('window_class'),
        'loop_id': placement.get('loop_id'),
        'node_id': placement.get('node_id'),
        'placement': placement,
    }
    target_window_name = resolve_dynamic_placement_window(config, state, window_counts=window_counts)
    if target_window_name is None:
        return _move_plan_blocked(
            context,
            loaded=loaded,
            config=config,
            agent_name=agent_name,
            placement=placement,
            source_window=source_window,
            reason='target_window_unresolved',
            message='target placement did not resolve to a concrete window',
        )
    target_window = _window_record(config, target_window_name)
    status = layout_status(context)
    agent_record = _layout_status_agent_record(status, agent_name)
    source = _agent_source(agent_record)
    same_window = source_window_name == target_window_name
    target_agent_names = _target_agent_names(target_window, agent_name=agent_name, same_window=same_window)
    base = _move_plan_payload(
        context,
        loaded=loaded,
        config=config,
        agent_name=agent_name,
        placement=placement,
        source_window=source_window,
        target_window_name=target_window_name,
        target_window=target_window,
        target_agent_names=target_agent_names,
        source=source,
        agent_record=agent_record,
    )
    if same_window:
        base.update(
            {
                'layout_status': 'planned',
                'move_plan_status': 'noop',
                'plan_class': 'noop',
                'reason': 'same_window',
                'message': 'agent is already in the resolved target window',
            }
        )
        return base
    if source != 'dynamic':
        base.update(
            {
                'layout_status': 'failed',
                'move_plan_status': 'blocked',
                'plan_class': 'blocked',
                'reason': f'{source}_agent_not_movable',
                'message': 'only dynamic session agents can be moved by a runtime move operation',
            }
        )
        return base
    base.update(
        {
            'layout_status': 'planned',
            'move_plan_status': 'planned',
            'plan_class': 'move_dynamic_agent',
            'reason': '',
            'message': 'read-only move plan; no runtime mutation was performed',
        }
    )
    return base


def _move_plan_payload(
    context,
    *,
    loaded,
    config,
    agent_name: str,
    placement: dict[str, object],
    source_window: dict[str, object],
    target_window_name: str,
    target_window: dict[str, object] | None,
    target_agent_names: tuple[str, ...],
    source: str,
    agent_record: dict[str, object],
) -> dict[str, object]:
    source_window_name = str(source_window.get('name') or '')
    source_agent_names = tuple(str(item) for item in tuple(source_window.get('agent_names') or ()))
    source_after = tuple(item for item in source_agent_names if item != agent_name)
    placement = dict(placement)
    placement['window_name'] = target_window_name
    target_pane_count = len(tuple(target_window.get('agent_names') or ())) if target_window else 0
    target_capacity = DEFAULT_MAX_PANES_PER_DYNAMIC_WINDOW if str(placement.get('mode') or '') == 'window_class' else None
    target_over_capacity = target_capacity is not None and len(target_agent_names) > target_capacity
    return {
        'layout_status': 'planned',
        'move_plan_status': 'planned',
        'action': 'move-plan',
        'project_id': context.project.project_id,
        'project_root': str(context.project.project_root),
        'config_source_kind': loaded.source_kind,
        'config_source': str(loaded.source_path or '<builtin>'),
        'agent': agent_name,
        'agent_source': source,
        'agent_kind': agent_record.get('agent_kind') or ('dynamic' if source == 'dynamic' else 'static'),
        'ownership_class': agent_record.get('ownership_class') or '',
        'lifecycle_state': agent_record.get('lifecycle_state') or '',
        'visibility_state': agent_record.get('visibility_state') or '',
        'placement': placement,
        'placement_mode': placement.get('mode'),
        'read_only': True,
        'mutation_performed': False,
        'apply_command_supported': False,
        'source_window_name': source_window_name,
        'target_window_name': target_window_name,
        'same_window': source_window_name == target_window_name,
        'target_window_exists': target_window is not None,
        'will_create_window': target_window is None,
        'source_window_agent_names': list(source_agent_names),
        'source_window_would_be_agent_names': list(source_after),
        'target_window_agent_names': list(tuple(target_window.get('agent_names') or ())) if target_window else [],
        'target_window_would_be_agent_names': list(target_agent_names),
        'target_window_pane_count': target_pane_count,
        'target_window_capacity': target_capacity,
        'target_window_over_capacity': target_over_capacity,
        'windows_explicit': bool(getattr(config, 'windows_explicit', False)),
        'entry_window': str(config.entry_window or 'main'),
        'pane_count': sum(_window_counts(config).values()),
        'window_count': len(tuple(config.windows or ())),
    }


def _move_plan_blocked(
    context,
    *,
    loaded,
    config,
    agent_name: str,
    placement: dict[str, object],
    reason: str,
    message: str,
    source_window: dict[str, object] | None = None,
) -> dict[str, object]:
    source_window_name = str(source_window.get('name') or '') if source_window else ''
    return {
        'layout_status': 'failed',
        'move_plan_status': 'blocked',
        'action': 'move-plan',
        'project_id': context.project.project_id,
        'project_root': str(context.project.project_root),
        'config_source_kind': loaded.source_kind,
        'config_source': str(loaded.source_path or '<builtin>'),
        'agent': agent_name,
        'placement': dict(placement),
        'placement_mode': placement.get('mode'),
        'read_only': True,
        'mutation_performed': False,
        'apply_command_supported': False,
        'source_window_name': source_window_name,
        'target_window_name': '',
        'same_window': False,
        'reason': reason,
        'message': message,
        'pane_count': sum(_window_counts(config).values()),
        'window_count': len(tuple(config.windows or ())),
    }


def _resolve_layout_placement(context, command) -> dict[str, object]:
    loaded = load_project_config(context.project.project_root, include_loop_overlays=True)
    config = loaded.config
    agent_name = _normalize_agent_name(getattr(command, 'agent_name', None))
    window_counts = _window_counts(config)
    placement = _placement_request(command)
    state = {
        'agent': agent_name,
        'window_name': placement.get('window_name'),
        'window_class': placement.get('window_class'),
        'loop_id': placement.get('loop_id'),
        'node_id': placement.get('node_id'),
        'placement': placement,
    }
    resolved_window = resolve_dynamic_placement_window(config, state, window_counts=window_counts)
    target_surface = 'window'
    if resolved_window is None:
        if getattr(config, 'windows_explicit', False):
            resolved_window = str(config.entry_window or 'main')
            target_surface = 'entry_window'
        else:
            target_surface = 'layout_spec'
    target_window = _window_record(config, resolved_window)
    target_pane_count = int(window_counts.get(resolved_window, 0)) if resolved_window is not None else 0
    agent_exists = agent_name in config.agents
    would_append = not agent_exists
    placement = dict(placement)
    if resolved_window is not None:
        placement['window_name'] = resolved_window
    target_agent_names = tuple(target_window.get('agent_names') or ()) if target_window else ()
    if would_append:
        target_agent_names = (*target_agent_names, agent_name)
    return {
        'layout_status': 'ok',
        'action': 'resolve',
        'project_id': context.project.project_id,
        'project_root': str(context.project.project_root),
        'config_source_kind': loaded.source_kind,
        'config_source': str(loaded.source_path or '<builtin>'),
        'agent': agent_name,
        'agent_exists': agent_exists,
        'addable': not agent_exists,
        'would_append_agent': would_append,
        'placement': placement,
        'placement_mode': placement.get('mode'),
        'target_surface': target_surface,
        'resolved_window_name': resolved_window,
        'target_window_exists': target_window is not None,
        'will_create_window': resolved_window is not None and target_window is None,
        'target_window_pane_count': target_pane_count,
        'target_window_capacity': (
            DEFAULT_MAX_PANES_PER_DYNAMIC_WINDOW
            if str(placement.get('mode') or '') == 'window_class'
            else None
        ),
        'target_window_agent_names': list(target_agent_names),
        'windows_explicit': bool(getattr(config, 'windows_explicit', False)),
        'entry_window': str(config.entry_window or 'main'),
        'pane_count': sum(window_counts.values()),
        'window_count': len(tuple(config.windows or ())),
    }


def _placement_request(command) -> dict[str, object]:
    window_name = _optional_text(getattr(command, 'window_name', None))
    window_class = _optional_text(getattr(command, 'window_class', None))
    loop_id = _optional_text(getattr(command, 'loop_id', None))
    node_id = _optional_text(getattr(command, 'node_id', None))
    mode = 'auto'
    if window_name is not None:
        mode = 'window'
    elif loop_id is not None or node_id is not None:
        mode = 'execution_node'
    elif window_class is not None:
        mode = 'window_class'
    return {
        'mode': mode,
        'window_name': window_name,
        'window_class': window_class,
        'loop_id': loop_id,
        'node_id': node_id,
        'layout_policy': 'append-or-create-window',
    }


def _normalize_agent_name(value: object) -> str:
    try:
        return normalize_agent_name(str(value or ''))
    except AgentValidationError as exc:
        raise ValueError(f'agent name is invalid: {exc}') from exc


def _optional_text(value: object | None) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _window_counts(config) -> dict[str, int]:
    return {
        str(window.name): len(tuple(window.agent_names or ()))
        for window in tuple(config.windows or ())
    }


def _agent_window_record(config, agent_name: str) -> dict[str, object] | None:
    for window in tuple(config.windows or ()):
        agent_names = tuple(window.agent_names or ())
        if agent_name in agent_names:
            return {
                'name': str(window.name),
                'agent_names': list(agent_names),
                'layout_spec': str(window.layout_spec or ''),
            }
    return None


def _window_record(config, name: str | None) -> dict[str, object] | None:
    if name is None:
        return None
    for window in tuple(config.windows or ()):
        if str(window.name) == name:
            return {
                'name': str(window.name),
                'agent_names': list(tuple(window.agent_names or ())),
                'layout_spec': str(window.layout_spec or ''),
            }
    return None


def _layout_status_agent_record(status: dict[str, object], agent_name: str) -> dict[str, object]:
    for window in tuple(status.get('windows') or ()):
        if not isinstance(window, dict):
            continue
        for agent in tuple(window.get('agents') or ()):
            if isinstance(agent, dict) and agent.get('agent') == agent_name:
                return dict(agent)
    return {}


def _agent_source(agent_record: dict[str, object]) -> str:
    source = _optional_text(agent_record.get('source'))
    return source or 'configured'


def _target_agent_names(target_window: dict[str, object] | None, *, agent_name: str, same_window: bool) -> tuple[str, ...]:
    current = tuple(str(item) for item in tuple(target_window.get('agent_names') or ())) if target_window else ()
    if same_window or agent_name in current:
        return current
    return (*current, agent_name)


def _run_layout_smoke(context, command, windows) -> dict[str, object]:
    if shutil.which('tmux') is None:
        return {
            'layout_status': 'failed',
            'smoke_status': 'failed',
            'reason': 'tmux_not_found',
        }
    context.paths.ensure_runtime_state_root()
    socket_path = _smoke_socket_path(context)
    session_name = _session_name(context, command)
    backend = make_terminal_backend(socket_path=str(socket_path))
    kill_server(backend)
    observed: list[dict[str, object]] = []
    try:
        for index, window in enumerate(windows):
            if index == 0:
                create_session(
                    backend,
                    session_name=session_name,
                    project_root=context.project.project_root,
                    window_name=window.name,
                    timeout_s=5.0,
                )
                ensure_server_policy(backend)
            else:
                create_window(
                    backend,
                    session_name=session_name,
                    window_name=window.name,
                    project_root=context.project.project_root,
                    select=False,
                    timeout_s=5.0,
                )
            target = session_window_target(session_name, window.name)
            root = window_root_pane(backend, target_window=target, timeout_s=5.0)
            _materialize_smoke_layout(
                context,
                backend,
                window=window,
                parent_pane_id=root,
            )
            observed.append(_observe_window(backend, session_name=session_name, window_name=window.name))
        return {
            'layout_status': 'ok',
            'smoke_status': 'ok',
            'socket_path': str(socket_path),
            'session_name': session_name,
            'observed_windows': observed,
            'cleanup_status': _cleanup(backend, enabled=bool(command.cleanup)),
        }
    except Exception as exc:
        return {
            'layout_status': 'failed',
            'smoke_status': 'failed',
            'socket_path': str(socket_path),
            'session_name': session_name,
            'error_type': type(exc).__name__,
            'error': str(exc),
            'cleanup_status': _cleanup(backend, enabled=True),
        }


def _run_layout_dynamic_smoke(context, command, names: tuple[str, ...]) -> dict[str, object]:
    if shutil.which('tmux') is None:
        return {
            'layout_status': 'failed',
            'smoke_status': 'failed',
            'dynamic_status': 'failed',
            'reason': 'tmux_not_found',
        }
    context.paths.ensure_runtime_state_root()
    socket_path = _smoke_socket_path(context)
    session_name = _session_name(context, command)
    backend = make_terminal_backend(socket_path=str(socket_path))
    kill_server(backend)
    events: list[dict[str, object]] = []
    pane_by_name: dict[str, str] = {}
    try:
        first_window = _current_windows(names[:1], window_prefix=command.window_prefix)[0]
        create_session(
            backend,
            session_name=session_name,
            project_root=context.project.project_root,
            window_name=first_window.name,
            timeout_s=5.0,
        )
        ensure_server_policy(backend)
        root = window_root_pane(
            backend,
            target_window=session_window_target(session_name, first_window.name),
            timeout_s=5.0,
        )
        pane_by_name[names[0]] = root
        _label_dynamic_pane(
            context,
            backend,
            pane_id=root,
            name=names[0],
            window_name=first_window.name,
            order_index=0,
        )
        _append_dynamic_event(
            context,
            backend,
            events,
            session_name=session_name,
            window_prefix=command.window_prefix,
            active_names=names[:1],
            phase='grow',
            operation='start',
            agent=names[0],
            pane_by_name=pane_by_name,
        )

        for index, name in enumerate(names[1:], start=2):
            _dynamic_add_pane(
                context,
                backend,
                session_name=session_name,
                window_prefix=command.window_prefix,
                pane_by_name=pane_by_name,
                name=name,
                index=index,
            )
            _append_dynamic_event(
                context,
                backend,
                events,
                session_name=session_name,
                window_prefix=command.window_prefix,
                active_names=names[:index],
                phase='grow',
                operation='add',
                agent=name,
                pane_by_name=pane_by_name,
            )

        active_names = list(names)
        for name in reversed(names[1:]):
            _dynamic_remove_pane(
                backend,
                session_name=session_name,
                window_prefix=command.window_prefix,
                pane_by_name=pane_by_name,
                name=name,
            )
            active_names.remove(name)
            _append_dynamic_event(
                context,
                backend,
                events,
                session_name=session_name,
                window_prefix=command.window_prefix,
                active_names=tuple(active_names),
                phase='shrink',
                operation='remove',
                agent=name,
                pane_by_name=pane_by_name,
            )

        return {
            'layout_status': 'ok',
            'smoke_status': 'ok',
            'dynamic_status': 'ok',
            'socket_path': str(socket_path),
            'session_name': session_name,
            'dynamic_events': events,
            'event_count': len(events),
            'cleanup_status': _cleanup(backend, enabled=bool(command.cleanup)),
        }
    except Exception as exc:
        return {
            'layout_status': 'failed',
            'smoke_status': 'failed',
            'dynamic_status': 'failed',
            'socket_path': str(socket_path),
            'session_name': session_name,
            'event_count': len(events),
            'dynamic_events': events,
            'error_type': type(exc).__name__,
            'error': str(exc),
            'cleanup_status': _cleanup(backend, enabled=True),
        }


def _materialize_smoke_layout(context, backend, *, window, parent_pane_id: str) -> None:
    style_index_by_agent = {name: index for index, name in enumerate(window.agent_names)}

    def assign_leaf(item: str, pane_id: str) -> None:
        apply_ccb_pane_identity(
            backend,
            pane_id,
            title=item,
            agent_label=item,
            project_id=context.project.project_id,
            order_index=style_index_by_agent.get(item),
            role='layout_smoke',
            slot_key=item,
            window_name=window.name,
            managed_by='layout-smoke',
        )

    _materialize_layout_node(
        context,
        backend,
        parent_pane_id=parent_pane_id,
        node=window.layout,
        assign_leaf=assign_leaf,
    )


def _materialize_layout_node(context, backend, *, parent_pane_id: str, node, assign_leaf) -> None:
    if node.kind == 'leaf':
        assert node.leaf is not None
        assign_leaf(node.leaf.name, parent_pane_id)
        return
    assert node.left is not None
    assert node.right is not None
    new_pane_id = split_pane(
        backend,
        target=parent_pane_id,
        direction='right' if node.kind == 'horizontal' else 'bottom',
        percent=_right_pane_percent(node),
        project_root=context.project.project_root,
        timeout_s=5.0,
    )
    _materialize_layout_node(
        context,
        backend,
        parent_pane_id=parent_pane_id,
        node=node.left,
        assign_leaf=assign_leaf,
    )
    _materialize_layout_node(
        context,
        backend,
        parent_pane_id=new_pane_id,
        node=node.right,
        assign_leaf=assign_leaf,
    )


def _dynamic_add_pane(
    context,
    backend,
    *,
    session_name: str,
    window_prefix: str,
    pane_by_name: dict[str, str],
    name: str,
    index: int,
) -> None:
    current_windows = _current_windows(tuple(f'p{item}' for item in range(1, index + 1)), window_prefix=window_prefix)
    target_window = current_windows[-1]
    local_index = ((index - 1) % 6) + 1
    if local_index == 1:
        create_window(
            backend,
            session_name=session_name,
            window_name=target_window.name,
            project_root=context.project.project_root,
            select=False,
            timeout_s=5.0,
        )
        pane_id = window_root_pane(
            backend,
            target_window=session_window_target(session_name, target_window.name),
            timeout_s=5.0,
        )
    else:
        if local_index == 2:
            parent_name = f'p{index - 1}'
            direction = 'right'
        else:
            parent_name = f'p{index - 2}'
            direction = 'bottom'
        pane_id = split_pane(
            backend,
            target=pane_by_name[parent_name],
            direction=direction,
            percent=50,
            project_root=context.project.project_root,
            timeout_s=5.0,
        )
    pane_by_name[name] = pane_id
    _label_dynamic_pane(
        context,
        backend,
        pane_id=pane_id,
        name=name,
        window_name=target_window.name,
        order_index=index - 1,
    )
    _even_window(backend, session_name=session_name, window_name=target_window.name)


def _dynamic_remove_pane(
    backend,
    *,
    session_name: str,
    window_prefix: str,
    pane_by_name: dict[str, str],
    name: str,
) -> None:
    index = int(name.removeprefix('p'))
    local_index = ((index - 1) % 6) + 1
    target_window = _current_windows(tuple(f'p{item}' for item in range(1, index + 1)), window_prefix=window_prefix)[-1]
    pane_id = pane_by_name.pop(name)
    if local_index == 1 and target_window.index > 1:
        kill_window(
            backend,
            target=session_window_target(session_name, target_window.name),
            timeout_s=5.0,
        )
        return
    backend.kill_pane(pane_id)
    _even_window(backend, session_name=session_name, window_name=target_window.name)


def _append_dynamic_event(
    context,
    backend,
    events: list[dict[str, object]],
    *,
    session_name: str,
    window_prefix: str,
    active_names: tuple[str, ...],
    phase: str,
    operation: str,
    agent: str,
    pane_by_name: dict[str, str],
) -> None:
    windows = _current_windows(active_names, window_prefix=window_prefix)
    observed = [
        _observe_window(backend, session_name=session_name, window_name=window.name)
        for window in windows
    ]
    alive = {
        name: backend.pane_exists(pane_id)
        for name, pane_id in sorted(pane_by_name.items())
    }
    events.append(
        {
            'phase': phase,
            'operation': operation,
            'agent': agent,
            'target_count': len(active_names),
            'window_count': len(windows),
            'windows': [window.to_record() for window in windows],
            'observed_windows': observed,
            'pane_ids': dict(sorted(pane_by_name.items())),
            'pane_alive': alive,
            'all_retained_alive': all(alive.values()),
            'project_id': context.project.project_id,
        }
    )


def _label_dynamic_pane(context, backend, *, pane_id: str, name: str, window_name: str, order_index: int) -> None:
    apply_ccb_pane_identity(
        backend,
        pane_id,
        title=name,
        agent_label=name,
        project_id=context.project.project_id,
        order_index=order_index,
        role='layout_smoke_agent',
        slot_key=name,
        window_name=window_name,
        managed_by='layout-dynamic-smoke',
    )


def _current_windows(names: tuple[str, ...], *, window_prefix: str) -> tuple[object, ...]:
    return build_pane_growth_windows(names, window_prefix=str(window_prefix or 'layout'))


def _even_window(backend, *, session_name: str, window_name: str) -> None:
    backend._tmux_run(
        ['select-layout', '-E', '-t', session_window_target(session_name, window_name)],
        check=False,
        capture=True,
    )


def _right_pane_percent(node) -> int:
    total = max(1, int(getattr(node, 'leaf_count', 1) or 1))
    right = max(1, int(getattr(node.right, 'leaf_count', 1) or 1))
    return max(1, min(99, round((right * 100) / total)))


def _observe_window(backend, *, session_name: str, window_name: str) -> dict[str, object]:
    target = session_window_target(session_name, window_name)
    result = backend._tmux_run(
        ['list-panes', '-t', target, '-F', '#{pane_id}\t#{pane_title}'],
        check=True,
        capture=True,
    )
    panes = []
    for line in (result.stdout or '').splitlines():
        pane_id, _sep, title = line.partition('\t')
        if pane_id.strip():
            panes.append({'pane_id': pane_id.strip(), 'title': title.strip()})
    return {
        'name': window_name,
        'pane_count': len(panes),
        'panes': panes,
    }


def _cleanup(backend, *, enabled: bool) -> str:
    if not enabled:
        return 'kept'
    return 'ok' if kill_server(backend) else 'failed'


def _session_name(context, command) -> str:
    explicit = str(command.session_name or '').strip()
    if explicit:
        return explicit
    return f'ccb-layout-smoke-{context.project.project_id[:12]}'


def _smoke_socket_path(context) -> Path:
    root = Path(tempfile.gettempdir()) / 'ccb-layout-smoke'
    root.mkdir(parents=True, exist_ok=True)
    return root / f'{context.project.project_id[:12]}-{os.getpid()}.sock'


__all__ = ['layout_command']
