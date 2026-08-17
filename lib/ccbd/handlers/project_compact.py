from __future__ import annotations

import subprocess

from agents.models import normalize_agent_name
from terminal_runtime import TmuxBackend

from .project_clear import (
    _agent_provider,
    _clear_busy_gate,
    _context_terminal_backend,
    _runtime_pane_id,
    _runtime_session_file,
)
from .project_context import COMPACT_COMMANDS, send_context_command


def build_project_compact_context_handler(app):
    def handle(payload: dict) -> dict:
        agent_names = _requested_agent_names(app, payload)
        backend = _context_terminal_backend(app, agent_names, backend_factory=TmuxBackend)
        results = tuple(_compact_agent_context(app, backend=backend, agent_name=name) for name in agent_names)
        statuses = {str(item.get('status') or '') for item in results}
        if 'blocked' in statuses:
            status = 'blocked'
        elif 'failed' in statuses:
            status = 'failed'
        elif 'unsupported' in statuses:
            status = 'unsupported'
        else:
            status = 'ok'
        return {
            'status': status,
            'agent_names': list(agent_names),
            'results': list(results),
        }

    return handle


def _requested_agent_names(app, payload: dict) -> tuple[str, ...]:
    raw_names = tuple(str(item).strip() for item in (payload.get('agent_names') or ()) if str(item).strip())
    if not raw_names:
        return tuple(app.config.agents)
    lowered = {item.lower() for item in raw_names}
    if 'all' in lowered:
        if len(raw_names) > 1:
            raise ValueError('compact target "all" cannot be combined with agent names')
        return tuple(app.config.agents)
    names: list[str] = []
    known = set(app.config.agents)
    for raw in raw_names:
        name = normalize_agent_name(raw)
        if name not in known:
            raise ValueError(f'unknown agent: {name}')
        if name not in names:
            names.append(name)
    return tuple(names)


def _compact_agent_context(app, *, backend, agent_name: str) -> dict[str, object]:
    busy = _clear_busy_gate(app, agent_name=agent_name)
    if busy is not None:
        return {
            'agent': agent_name,
            'status': 'blocked',
            'reason': 'agent_has_outstanding_work',
            **busy,
        }
    provider = _agent_provider(app, agent_name)
    command = COMPACT_COMMANDS.get(provider)
    runtime = app.registry.get(agent_name)
    if runtime is None:
        return {'agent': agent_name, 'status': 'skipped', 'reason': 'runtime_missing', 'provider': provider}
    if provider == 'dsh':
        session_file = _runtime_session_file(runtime)
        if session_file is None:
            return {
                'agent': agent_name,
                'status': 'skipped',
                'reason': 'session_binding_missing',
                'provider': provider,
            }
        try:
            from provider_backends.dsh.control import compact_dsh_session

            compacted = compact_dsh_session(session_file)
        except Exception as exc:
            return {
                'agent': agent_name,
                'status': 'failed',
                'reason': str(exc)[:200],
                'provider': provider,
                'command': command,
            }
        return {
            'agent': agent_name,
            'status': 'compacted',
            'provider': provider,
            'command': str(compacted.get('command') or command or '/compact'),
            **(
                {'detail': compacted['detail']}
                if compacted.get('detail')
                else {}
            ),
        }
    pane_id = _runtime_pane_id(runtime)
    if pane_id is None:
        return {'agent': agent_name, 'status': 'skipped', 'reason': 'pane_missing', 'provider': provider}
    if command is None:
        return {
            'agent': agent_name,
            'status': 'unsupported',
            'reason': 'provider_native_compact_unverified',
            'provider': provider,
            'pane_id': pane_id,
        }
    try:
        if not backend.pane_exists(pane_id):
            return {
                'agent': agent_name,
                'status': 'skipped',
                'reason': 'pane_missing',
                'provider': provider,
                'pane_id': pane_id,
            }
        send_context_command(backend, pane_id=pane_id, command=command, provider=provider)
    except subprocess.CalledProcessError as exc:
        return {
            'agent': agent_name,
            'status': 'failed',
            'reason': str(exc.stderr or exc)[:200],
            'provider': provider,
            'pane_id': pane_id,
            'command': command,
        }
    except Exception as exc:
        return {
            'agent': agent_name,
            'status': 'failed',
            'reason': str(exc)[:200],
            'provider': provider,
            'pane_id': pane_id,
            'command': command,
        }
    return {
        'agent': agent_name,
        'status': 'compacted',
        'provider': provider,
        'pane_id': pane_id,
        'command': command,
    }


__all__ = ['build_project_compact_context_handler']
