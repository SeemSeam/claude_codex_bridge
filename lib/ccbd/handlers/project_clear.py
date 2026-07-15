from __future__ import annotations

import subprocess
import time

from agents.models import normalize_agent_name
from terminal_runtime import TmuxBackend

OPENCODE_CLEAR_SUBMIT_DELAY_S = 0.3


def build_project_clear_context_handler(app):
    def handle(payload: dict) -> dict:
        agent_names = _requested_agent_names(app, payload)
        namespace = app.project_namespace.load()
        if namespace is None:
            raise RuntimeError('project namespace is not mounted')
        backend = TmuxBackend(socket_path=namespace.tmux_socket_path)
        results = tuple(_clear_agent_context(app, backend=backend, agent_name=name) for name in agent_names)
        statuses = {str(item.get('status') or '') for item in results}
        return {
            'status': 'blocked' if 'blocked' in statuses else ('failed' if 'failed' in statuses else 'ok'),
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
            raise ValueError('clear target "all" cannot be combined with agent names')
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


def _clear_agent_context(app, *, backend, agent_name: str) -> dict[str, object]:
    busy = _clear_busy_gate(app, agent_name=agent_name)
    if busy is not None:
        return {
            'agent': agent_name,
            'status': 'blocked',
            'reason': 'agent_has_outstanding_work',
            **busy,
        }
    runtime = app.registry.get(agent_name)
    if runtime is None:
        return {'agent': agent_name, 'status': 'skipped', 'reason': 'runtime_missing'}
    pane_id = _runtime_pane_id(runtime)
    if pane_id is None:
        return {'agent': agent_name, 'status': 'skipped', 'reason': 'pane_missing'}
    try:
        if not backend.pane_exists(pane_id):
            return {'agent': agent_name, 'status': 'skipped', 'reason': 'pane_missing', 'pane_id': pane_id}
        _send_clear_sequence(backend, pane_id=pane_id, provider=_agent_provider(app, agent_name))
    except subprocess.CalledProcessError as exc:
        return {
            'agent': agent_name,
            'status': 'failed',
            'reason': str(exc.stderr or exc)[:200],
            'pane_id': pane_id,
        }
    except Exception as exc:
        return {
            'agent': agent_name,
            'status': 'failed',
            'reason': str(exc)[:200],
            'pane_id': pane_id,
        }
    return {'agent': agent_name, 'status': 'cleared', 'pane_id': pane_id, 'command': '/clear'}


def _clear_busy_gate(app, *, agent_name: str) -> dict[str, object] | None:
    dispatcher = getattr(app, 'dispatcher', None)
    has_outstanding = getattr(dispatcher, '_has_outstanding_work', None)
    if not callable(has_outstanding):
        return None
    try:
        if not has_outstanding(agent_name):
            return None
    except Exception:
        # A failed authority check must not mutate a live provider context.
        return {'active_job_id': None, 'queue_depth': None, 'authority_check_failed': True}

    state = getattr(dispatcher, '_state', None)
    active_job = getattr(state, 'active_job', None)
    queue_depth = getattr(state, 'queue_depth', None)
    try:
        active_job_id = active_job(agent_name) if callable(active_job) else None
    except Exception:
        active_job_id = None
    try:
        depth = queue_depth(agent_name) if callable(queue_depth) else None
    except Exception:
        depth = None
    return {
        'active_job_id': str(active_job_id or '').strip() or None,
        'queue_depth': depth,
    }


def _runtime_pane_id(runtime) -> str | None:
    for candidate in (
        getattr(runtime, 'active_pane_id', None),
        getattr(runtime, 'pane_id', None),
    ):
        text = str(candidate or '').strip()
        if text.startswith('%'):
            return text
    return None


def _agent_provider(app, agent_name: str) -> str:
    spec = app.config.agents.get(agent_name)
    return str(getattr(spec, 'provider', '') or '').strip().lower()


def _send_clear_sequence(backend, *, pane_id: str, provider: str = '') -> None:
    try:
        backend._ensure_not_in_copy_mode(pane_id)
    except Exception:
        pass
    backend._tmux_run(['send-keys', '-t', pane_id, 'C-u'], check=True, capture=True)
    backend._tmux_run(['send-keys', '-t', pane_id, '-l', '/clear'], check=True, capture=True)
    if provider == 'opencode':
        # OpenCode can drop an immediate submit after restoring an old session.
        time.sleep(OPENCODE_CLEAR_SUBMIT_DELAY_S)
    backend._tmux_run(['send-keys', '-t', pane_id, 'Enter'], check=True, capture=True)


__all__ = ['build_project_clear_context_handler']
