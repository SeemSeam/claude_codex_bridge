from __future__ import annotations

from datetime import datetime, timezone

from terminal_runtime.tmux_backend import TmuxBackend
from .daemon import connect_current_mounted_daemon


def _binding(context, view, agent_name):
    if view.get('project', {}).get('id') != context.project.project_id:
        raise ValueError('project identity mismatch')
    if view.get('ccbd', {}).get('state') != 'mounted':
        raise ValueError('project ccbd is not mounted')
    namespace = view.get('namespace') or {}
    if namespace.get('namespace_backend_impl') != 'tmux':
        raise ValueError('screen requires a tmux project namespace')
    socket = namespace.get('socket_path')
    epoch = namespace.get('epoch')
    if not socket or epoch is None:
        raise ValueError('tmux namespace identity is unavailable')
    agents = [a for a in view.get('agents', []) if a.get('name') == agent_name]
    if len(agents) != 1:
        raise ValueError(f'unknown or ambiguous agent: {agent_name}')
    pane = agents[0].get('pane_id')
    if not pane:
        raise ValueError(f'agent has no bound pane: {agent_name}')
    return str(socket), str(epoch), str(pane)


def agent_screen(context, command) -> dict:
    """Read only the bound live pane. Never start a daemon or fall back to logs."""
    try:
        handle = connect_current_mounted_daemon(context)
        binding = _binding(context, handle.client.project_view()['view'], command.agent_name)
        socket, epoch, pane = binding
        backend = TmuxBackend(socket_path=socket)
        expected = {
            '@ccb_project_id': context.project.project_id,
            '@ccb_agent': command.agent_name,
            '@ccb_namespace_epoch': epoch,
            '@ccb_managed_by': 'ccbd',
            '@ccb_role': 'agent',
        }
        options = (*expected, '@ccb_session_id')

        def inspect():
            info = backend.describe_pane(pane, user_options=options)
            if (not info or info.get('pane_id') != pane or info.get('pane_dead') != '0'
                    or any(info.get(key) != value for key, value in expected.items())):
                raise ValueError('pane is missing, dead, or no longer owned by this project/agent')
            return tuple(info.get(key) for key in options)

        before = inspect()
        text = backend.capture_screen(pane, history_lines=command.lines)
        if text is None:
            raise ValueError('tmux screen capture failed')
        if inspect() != before or _binding(
            context, handle.client.project_view()['view'], command.agent_name
        ) != binding:
            raise ValueError('pane binding changed during capture; query again')
        return {
            'status': 'ok', 'agent': command.agent_name,
            'project_id': context.project.project_id, 'pane': pane,
            'captured_at': datetime.now(timezone.utc).isoformat(),
            'history_lines': command.lines, 'text': text,
        }
    except (RuntimeError, ValueError, OSError) as exc:
        return {'status': 'failed', 'agent': command.agent_name, 'error': str(exc)}
