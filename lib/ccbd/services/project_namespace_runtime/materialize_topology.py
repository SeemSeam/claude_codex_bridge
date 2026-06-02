from __future__ import annotations

import shlex
from typing import Any

from cli.services.tmux_ui import apply_project_tmux_ui
from agents.models import parse_layout_spec
from terminal_runtime.placeholders import pane_placeholder_cmd
from terminal_runtime.tmux_identity import apply_ccb_pane_identity

from .backend import (
    create_session,
    ensure_window,
    ensure_server_policy,
    prepare_server,
    rename_window,
    select_window,
    session_window_target,
    split_pane,
    window_root_pane,
)
from .sidebar_helper import sidebar_respawn_args


def refresh_topology_ui(context) -> None:
    apply_project_tmux_ui(
        tmux_socket_path=context.desired_socket_path,
        tmux_session_name=context.desired_session_name,
        backend=context.backend,
    )
    _sync_topology_sidebar_widths(None, context, topology_plan=getattr(context, 'topology_plan', None))


def refresh_topology_ui_for_project(
    controller,
    context,
    *,
    topology_plan,
    timeout_s: float | None = None,
) -> None:
    apply_project_tmux_ui(
        tmux_socket_path=context.desired_socket_path,
        ccbd_socket_path=str(controller._layout.ccbd_socket_path),
        tmux_session_name=context.desired_session_name,
        backend=context.backend,
    )
    _sync_topology_sidebar_widths(controller, context, topology_plan=topology_plan, timeout_s=timeout_s)


def materialize_topology(
    controller,
    context,
    *,
    topology_plan,
    epoch: int,
    terminal_size: tuple[int, int] | None = None,
    timeout_s: float | None = None,
) -> dict[str, str]:
    windows = tuple(getattr(topology_plan, 'windows', ()) or ())
    if not windows:
        return {}
    prepare_server(context.backend, timeout_s=timeout_s)
    first_window = windows[0]
    if not context.session_is_alive:
        create_session(
            context.backend,
            session_name=context.desired_session_name,
            project_root=controller._layout.project_root,
            window_name=first_window.name,
            terminal_size=terminal_size,
            timeout_s=timeout_s,
        )
    else:
        ensure_window(
            context.backend,
            session_name=context.desired_session_name,
            window_name=first_window.name,
            project_root=controller._layout.project_root,
            select=False,
            timeout_s=timeout_s,
        )
    ensure_server_policy(context.backend, timeout_s=timeout_s)
    apply_project_tmux_ui(
        tmux_socket_path=context.desired_socket_path,
        ccbd_socket_path=str(controller._layout.ccbd_socket_path),
        tmux_session_name=context.desired_session_name,
        backend=context.backend,
    )
    _rename_legacy_workspace_if_needed(controller, context, first_window_name=first_window.name, timeout_s=timeout_s)

    agent_panes: dict[str, str] = {}
    for index, window in enumerate(windows):
        ensure_window(
            context.backend,
            session_name=context.desired_session_name,
            window_name=window.name,
            project_root=controller._layout.project_root,
            select=index == 0,
            timeout_s=timeout_s,
        )
        target = session_window_target(context.desired_session_name, window.name)
        root_pane = window_root_pane(context.backend, target_window=target, timeout_s=timeout_s)
        user_root = _materialize_sidebar(
            controller,
            context,
            window=window,
            root_pane=root_pane,
            epoch=epoch,
            timeout_s=timeout_s,
        )
        agent_panes.update(
            _materialize_agent_layout(
                controller,
                context,
                window=window,
                user_root=user_root,
                epoch=epoch,
                timeout_s=timeout_s,
            )
        )
        _materialize_tool_window(
            controller,
            context,
            window=window,
            user_root=user_root,
            epoch=epoch,
        )

    refresh_topology_ui_for_project(
        controller,
        context,
        topology_plan=topology_plan,
        timeout_s=timeout_s,
    )
    select_window(
        context.backend,
        target=session_window_target(context.desired_session_name, topology_plan.entry_window),
    )
    return agent_panes


def existing_topology_agent_panes(controller, context, *, topology_plan) -> dict[str, str]:
    agent_panes: dict[str, str] = {}
    for window in tuple(getattr(topology_plan, 'windows', ()) or ()):
        for agent_name in tuple(getattr(window, 'agent_names', ()) or ()):
            matches = _list_panes_by_user_options(
                context.backend,
                {
                    '@ccb_project_id': controller._project_id,
                    '@ccb_role': 'agent',
                    '@ccb_slot': str(agent_name),
                    '@ccb_window': str(window.name),
                    '@ccb_managed_by': 'ccbd',
                },
            )
            if len(matches) == 1:
                agent_panes[str(agent_name)] = matches[0]
    return agent_panes


def topology_active_panes(controller, context, *, topology_plan) -> tuple[str, ...]:
    expected_windows = {str(window.name) for window in tuple(getattr(topology_plan, 'windows', ()) or ())}
    panes: list[str] = []
    for role in ('sidebar', 'agent', 'tool'):
        matches = _list_panes_by_user_options(
            context.backend,
            {
                '@ccb_project_id': controller._project_id,
                '@ccb_role': role,
                '@ccb_managed_by': 'ccbd',
            },
        )
        for pane_id in matches:
            window_name = _pane_option(context.backend, pane_id, '@ccb_window')
            sidebar_instance = _pane_option(context.backend, pane_id, '@ccb_sidebar_instance')
            if (window_name in expected_windows) or (sidebar_instance in expected_windows):
                panes.append(pane_id)
    return tuple(dict.fromkeys(panes))


def topology_recreate_reason(controller, context, *, topology_plan) -> str | None:
    if context.current is not None:
        current_workspace = str(getattr(context.current, 'workspace_window_name', '') or '').strip()
        if current_workspace and current_workspace != context.desired_workspace_window_name:
            return 'topology_workspace_changed'

    windows = tuple(getattr(topology_plan, 'windows', ()) or ())
    for window in windows:
        if _find_window(context, str(window.name)) is None:
            return f'topology_window_missing:{window.name}'

    expected_agents = {
        str(agent_name)
        for window in windows
        for agent_name in tuple(getattr(window, 'agent_names', ()) or ())
    }
    if set(existing_topology_agent_panes(controller, context, topology_plan=topology_plan)) != expected_agents:
        return 'topology_agent_panes_changed'

    if bool(getattr(topology_plan, 'sidebar_enabled', False)):
        for window in windows:
            matches = _list_panes_by_user_options(
                context.backend,
                {
                    '@ccb_project_id': controller._project_id,
                    '@ccb_role': 'sidebar',
                    '@ccb_sidebar_instance': str(window.name),
                    '@ccb_managed_by': 'ccbd',
                },
            )
            if len(matches) != 1:
                return 'topology_sidebar_panes_changed'
    expected_tools = {
        str(window.name)
        for window in windows
        if str(getattr(window, 'kind', '') or '') == 'tool'
    }
    for window_name in expected_tools:
        matches = _list_panes_by_user_options(
            context.backend,
            {
                '@ccb_project_id': controller._project_id,
                '@ccb_role': 'tool',
                '@ccb_slot': f'tool:{window_name}',
                '@ccb_window': window_name,
                '@ccb_managed_by': 'ccbd',
            },
        )
        if len(matches) != 1:
            return 'topology_tool_panes_changed'
    return None


def _rename_legacy_workspace_if_needed(controller, context, *, first_window_name: str, timeout_s: float | None) -> None:
    legacy_name = str(getattr(controller._layout, 'ccbd_tmux_workspace_window_name', '') or '').strip()
    if context.current is not None:
        legacy_name = str(getattr(context.current, 'workspace_window_name', '') or '').strip() or legacy_name
    first_name = str(first_window_name or '').strip()
    if not legacy_name or not first_name or legacy_name == first_name:
        return
    legacy = ensure_target = None
    try:
        from .backend import find_window

        legacy = find_window(
            context.backend,
            session_name=context.desired_session_name,
            window_name=legacy_name,
            timeout_s=timeout_s,
        )
        ensure_target = find_window(
            context.backend,
            session_name=context.desired_session_name,
            window_name=first_name,
            timeout_s=timeout_s,
        )
    except Exception:
        return
    if legacy is None or ensure_target is not None:
        return
    rename_window(
        context.backend,
        target=session_window_target(context.desired_session_name, legacy.window_id or legacy_name),
        new_name=first_name,
        timeout_s=timeout_s,
    )


def _materialize_sidebar(
    controller,
    context,
    *,
    window,
    root_pane: str,
    epoch: int,
    timeout_s: float | None,
) -> str:
    sidebar = getattr(window, 'sidebar', None)
    if sidebar is None:
        return root_pane
    user_root = split_pane(
        context.backend,
        target=root_pane,
        direction='right',
        percent=_user_pane_percent_for_sidebar(
            sidebar.width,
            pane_width=_pane_width_cells(context.backend, root_pane),
        ),
        project_root=controller._layout.project_root,
        timeout_s=timeout_s,
    )
    _respawn_sidebar(context.backend, root_pane, sidebar.launch_args, cwd=str(controller._layout.project_root))
    apply_ccb_pane_identity(
        context.backend,
        root_pane,
        title='sidebar',
        agent_label='sidebar',
        project_id=controller._project_id,
        role='sidebar',
        slot_key=f'sidebar:{window.name}',
        window_name=window.name,
        sidebar_instance=window.name,
        namespace_epoch=epoch,
        managed_by='ccbd',
    )
    return user_root


def _materialize_agent_layout(
    controller,
    context,
    *,
    window,
    user_root: str,
    epoch: int,
    timeout_s: float | None,
) -> dict[str, str]:
    if str(getattr(window, 'kind', '') or '') == 'tool':
        return {}
    layout = parse_layout_spec(window.user_layout)
    agent_names = tuple(str(name) for name in getattr(window, 'agent_names', ()) or ())
    style_index_by_agent = {name: index for index, name in enumerate(agent_names)}
    agent_panes: dict[str, str] = {}

    def assign_leaf(item: str, pane_id: str) -> None:
        if item == 'cmd':
            return
        agent_panes[item] = pane_id
        apply_ccb_pane_identity(
            context.backend,
            pane_id,
            title=item,
            agent_label=item,
            project_id=controller._project_id,
            order_index=style_index_by_agent.get(item),
            role='agent',
            slot_key=item,
            window_name=window.name,
            namespace_epoch=epoch,
            managed_by='ccbd',
        )

    _materialize_layout(
        controller,
        context,
        parent_pane_id=user_root,
        node=layout,
        assign_leaf=assign_leaf,
        timeout_s=timeout_s,
    )
    return agent_panes


def _materialize_tool_window(
    controller,
    context,
    *,
    window,
    user_root: str,
    epoch: int,
) -> None:
    if str(getattr(window, 'kind', '') or '') != 'tool':
        return
    command = str(getattr(window, 'command', '') or '').strip() or pane_placeholder_cmd()
    respawn = getattr(context.backend, 'respawn_pane', None)
    if callable(respawn):
        respawn(user_root, cmd=command, cwd=str(controller._layout.project_root), remain_on_exit=True)
    else:
        context.backend._tmux_run(['respawn-pane', '-k', '-t', user_root, 'sh', '-lc', command], check=False)
    apply_ccb_pane_identity(
        context.backend,
        user_root,
        title=str(getattr(window, 'label', None) or window.name),
        agent_label=str(getattr(window, 'label', None) or window.name),
        project_id=controller._project_id,
        order_index=int(getattr(window, 'order', 0) or 0),
        role='tool',
        slot_key=f'tool:{window.name}',
        window_name=window.name,
        namespace_epoch=epoch,
        managed_by='ccbd',
    )


def _get_specified_percent(node: Any) -> int | None:
    if node.kind == 'leaf':
        assert node.leaf is not None
        return node.leaf.percent
    for leaf in node.iter_leaves():
        if getattr(leaf, 'percent', None) is not None:
            return leaf.percent
    return None


def _materialize_layout(
    controller,
    context,
    *,
    parent_pane_id: str,
    node: Any,
    assign_leaf,
    timeout_s: float | None,
) -> None:
    if node.kind == 'leaf':
        assert node.leaf is not None
        assign_leaf(node.leaf.name, parent_pane_id)
        return

    assert node.left is not None
    assert node.right is not None

    right_pct = _get_specified_percent(node.right)
    left_pct = _get_specified_percent(node.left)
    if right_pct is not None:
        percent = max(1, min(99, right_pct))
    elif left_pct is not None:
        percent = max(1, min(99, 100 - left_pct))
    else:
        total = max(1, node.leaf_count)
        right_count = max(1, node.right.leaf_count)
        percent = max(1, min(99, round((right_count * 100) / total)))

    direction = 'right' if node.kind == 'horizontal' else 'bottom'
    new_pane_id = split_pane(
        context.backend,
        target=parent_pane_id,
        direction=direction,
        percent=percent,
        project_root=controller._layout.project_root,
        timeout_s=timeout_s,
    )
    _materialize_layout(
        controller,
        context,
        parent_pane_id=parent_pane_id,
        node=node.left,
        assign_leaf=assign_leaf,
        timeout_s=timeout_s,
    )
    _materialize_layout(
        controller,
        context,
        parent_pane_id=new_pane_id,
        node=node.right,
        assign_leaf=assign_leaf,
        timeout_s=timeout_s,
    )


def _find_window(context, window_name: str):
    try:
        from .backend import find_window

        return find_window(
            context.backend,
            session_name=context.desired_session_name,
            window_name=window_name,
            timeout_s=0.0,
        )
    except Exception:
        return None


def _sync_topology_sidebar_widths(
    controller,
    context,
    *,
    topology_plan,
    timeout_s: float | None = None,
) -> None:
    if topology_plan is None or not bool(getattr(topology_plan, 'sidebar_enabled', False)):
        return
    width_by_window = {
        str(window.name): getattr(window.sidebar, 'width', '15%')
        for window in tuple(getattr(topology_plan, 'windows', ()) or ())
        if getattr(window, 'sidebar', None) is not None
    }
    if not width_by_window:
        return
    project_id = (
        str(getattr(controller, '_project_id', '') or '').strip()
        if controller is not None
        else ''
    )
    width_override = _session_sidebar_width_override(context.backend, context.desired_session_name)
    _set_session_sidebar_sync_guard(context.backend, context.desired_session_name, enabled=True)
    try:
        for record in _list_sidebar_geometry_records(
            context.backend,
            session_name=context.desired_session_name,
            project_id=project_id,
        ):
            configured_width = width_override or width_by_window.get(record['sidebar_instance'])
            if configured_width is None:
                continue
            window_width = _positive_int(record.get('window_width'))
            if window_width <= 0:
                continue
            target_width = _sidebar_width_cells(configured_width, window_width)
            if target_width <= 0 or target_width == _positive_int(record.get('pane_width')):
                continue
            _resize_pane_width(context.backend, record['pane_id'], target_width, timeout_s=timeout_s)
    finally:
        _set_session_sidebar_sync_guard(context.backend, context.desired_session_name, enabled=False)


def _list_sidebar_geometry_records(
    backend,
    *,
    session_name: str,
    project_id: str = '',
) -> list[dict[str, str]]:
    runner = getattr(backend, '_tmux_run', None)
    if not callable(runner):
        return []
    fmt = '\t'.join(
        [
            '#{session_name}',
            '#{pane_id}',
            '#{window_width}',
            '#{pane_width}',
            '#{@ccb_project_id}',
            '#{@ccb_role}',
            '#{@ccb_sidebar_instance}',
            '#{@ccb_managed_by}',
        ]
    )
    try:
        cp = runner(['list-panes', '-a', '-F', fmt], capture=True, check=False, timeout=0.5)
    except Exception:
        return []
    if getattr(cp, 'returncode', 1) != 0:
        return []
    records: list[dict[str, str]] = []
    for line in (getattr(cp, 'stdout', '') or '').splitlines():
        parts = [part.strip() for part in line.split('\t')]
        if len(parts) != 8:
            continue
        (
            pane_session,
            pane_id,
            window_width,
            pane_width,
            pane_project_id,
            role,
            sidebar_instance,
            managed_by,
        ) = parts
        if pane_session != session_name or role != 'sidebar' or managed_by != 'ccbd':
            continue
        if project_id and pane_project_id != project_id:
            continue
        if not pane_id.startswith('%') or not sidebar_instance:
            continue
        records.append(
            {
                'pane_id': pane_id,
                'window_width': window_width,
                'pane_width': pane_width,
                'sidebar_instance': sidebar_instance,
            }
        )
    return records


def _resize_pane_width(backend, pane_id: str, width: int, *, timeout_s: float | None = None) -> None:
    runner = getattr(backend, '_tmux_run', None)
    if not callable(runner):
        return
    try:
        runner(
            ['resize-pane', '-t', pane_id, '-x', str(max(1, int(width)))],
            check=False,
            capture=True,
            timeout=timeout_s,
        )
    except Exception:
        return


def _sidebar_width_cells(width: object, window_width: int) -> int:
    usable_width = max(1, int(window_width or 0))
    target = _sidebar_width_target_cells(width, usable_width)
    min_user_width = 10 if usable_width > 20 else 1
    max_width = max(1, usable_width - min_user_width)
    return max(1, min(max_width, int(target)))


def _sidebar_width_target_cells(width: object, window_width: int) -> int:
    text = str(width or '').strip()
    if text.endswith('%'):
        return round(max(1, int(window_width or 0)) * (_sidebar_percent(text) / 100.0))
    try:
        return int(text)
    except Exception:
        return round(max(1, int(window_width or 0)) * 0.15)


def _pane_width_cells(backend, pane_id: str) -> int:
    runner = getattr(backend, '_tmux_run', None)
    if not callable(runner):
        return 0
    try:
        cp = runner(
            ['display-message', '-p', '-t', pane_id, '#{pane_width}'],
            capture=True,
            check=False,
            timeout=0.5,
        )
    except Exception:
        return 0
    if getattr(cp, 'returncode', 1) != 0:
        return 0
    return _positive_int(((getattr(cp, 'stdout', '') or '').splitlines() or [''])[0])


def _session_sidebar_width_override(backend, session_name: str) -> int:
    runner = getattr(backend, '_tmux_run', None)
    if not callable(runner):
        return 0
    try:
        cp = runner(
            ['show-option', '-qv', '-t', session_name, '@ccb_sidebar_width_cells'],
            capture=True,
            check=False,
            timeout=0.5,
        )
    except Exception:
        return 0
    if getattr(cp, 'returncode', 1) != 0:
        return 0
    return _positive_int(((getattr(cp, 'stdout', '') or '').splitlines() or [''])[0])


def _set_session_sidebar_sync_guard(backend, session_name: str, *, enabled: bool) -> None:
    runner = getattr(backend, '_tmux_run', None)
    if not callable(runner):
        return
    args = (
        ['set-option', '-t', session_name, '@ccb_sidebar_sync_guard', '1']
        if enabled
        else ['set-option', '-u', '-t', session_name, '@ccb_sidebar_sync_guard']
    )
    try:
        runner(args, capture=True, check=False, timeout=0.5)
    except Exception:
        return


def _positive_int(value: object) -> int:
    try:
        parsed = int(str(value or '').strip())
    except Exception:
        return 0
    return max(0, parsed)


def _pane_option(backend, pane_id: str, option_name: str) -> str:
    runner = getattr(backend, '_tmux_run', None)
    if not callable(runner):
        return ''
    try:
        cp = runner(
            ['display-message', '-p', '-t', pane_id, f'#{{{option_name}}}'],
            capture=True,
            check=False,
            timeout=0.5,
        )
    except Exception:
        return ''
    if getattr(cp, 'returncode', 1) != 0:
        return ''
    return ((getattr(cp, 'stdout', '') or '').splitlines() or [''])[0].strip()


def _list_panes_by_user_options(backend, expected: dict[str, str]) -> list[str]:
    lister = getattr(backend, 'list_panes_by_user_options', None)
    if callable(lister):
        try:
            return list(lister(expected))
        except Exception:
            return []
    runner = getattr(backend, '_tmux_run', None)
    if not callable(runner):
        return []
    options = list(expected)
    fmt = '\t'.join(['#{pane_id}', *(f'#{{{option}}}' for option in options)])
    try:
        cp = runner(['list-panes', '-a', '-F', fmt], capture=True, check=False, timeout=0.5)
    except Exception:
        return []
    if getattr(cp, 'returncode', 1) != 0:
        return []
    matches: list[str] = []
    for line in (getattr(cp, 'stdout', '') or '').splitlines():
        parts = line.split('\t')
        if len(parts) != len(options) + 1:
            continue
        pane_id = parts[0].strip()
        if not pane_id.startswith('%'):
            continue
        if all((parts[index + 1] or '').strip() == expected[option] for index, option in enumerate(options)):
            matches.append(pane_id)
    return matches


def _sidebar_percent(width: object) -> int:
    text = str(width or '').strip()
    if text.endswith('%'):
        text = text[:-1]
    try:
        value = int(text)
    except Exception:
        return 15
    return max(1, min(90, value))


def _user_pane_percent_for_sidebar(width: object, pane_width: int = 0) -> int:
    if pane_width > 0:
        sidebar_cells = _sidebar_width_cells(width, pane_width)
        user_cells = max(1, int(pane_width) - sidebar_cells)
        return max(1, min(99, round((user_cells * 100) / int(pane_width))))
    return max(10, min(99, 100 - _sidebar_percent(width)))


def _respawn_sidebar(backend, pane_id: str, launch_args: tuple[str, ...], *, cwd: str) -> None:
    args = sidebar_respawn_args(tuple(launch_args or ()))
    command = ' '.join(shlex.quote(str(part)) for part in args) if args else pane_placeholder_cmd()
    respawn = getattr(backend, 'respawn_pane', None)
    if callable(respawn):
        respawn(pane_id, cmd=command, cwd=cwd, remain_on_exit=True)
        return
    backend._tmux_run(['respawn-pane', '-k', '-t', pane_id, 'sh', '-lc', command], check=False)


__all__ = [
    'existing_topology_agent_panes',
    'materialize_topology',
    'refresh_topology_ui',
    'refresh_topology_ui_for_project',
    'topology_active_panes',
    'topology_recreate_reason',
]
