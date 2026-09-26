"""Fixed, nonblocking input-draft guard. It never decides provider turn completion."""
from __future__ import annotations

import json
import socket
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .draft_observation import Observation, inspect_screen

WAIT_SECONDS = 180.0


@dataclass
class DraftGuard:
    clock: Callable[[], float] = time.monotonic
    binding: str = ''
    since: float | None = None
    clear_attempted: bool = False
    reason: str = 'unobserved'

    def reset(self, reason: str) -> None:
        self.since = None
        self.reason = reason

    def allows(self, target: 'DraftTarget') -> bool:
        observation = target.observe()
        if observation.state not in {'empty', 'nonempty'}:
            self.reset(observation.reason)
            return False
        if self.binding != observation.binding:
            self.binding = observation.binding
            self.since = None
            self.clear_attempted = False
        self.reason = observation.reason
        if observation.state == 'empty':
            self.since = None
            self.clear_attempted = False
            return True
        if observation.state != 'nonempty':
            self.reset(observation.reason)
            return False
        if self.clear_attempted:
            self.reason = 'clear_unconfirmed'
            return False
        if self.since is None:
            self.since = self.clock()
        if self.clock() - self.since < WAIT_SECONDS:
            self.reason = 'waiting_for_draft'
            return False
        # Recheck at the destructive boundary, including pane generation/busy.
        fresh = target.observe()
        if fresh.binding != self.binding or fresh.state != 'nonempty':
            self.reset('draft_changed_before_clear')
            return False
        self.clear_attempted = True
        try:
            target.clear(fresh)
        except Exception:
            self.reason = 'clear_failed'
            return False
        # Rendering is asynchronous. Subsequent normal polls may confirm empty;
        # never send another clear just because the first readback is delayed.
        after = target.observe()
        if after.binding == self.binding and after.state == 'empty':
            self.since = None
            self.clear_attempted = False
            self.reason = 'cleared'
            return True
        self.reason = 'clear_unconfirmed'
        return False


@dataclass
class DraftTarget:
    provider: str
    backend: object
    pane_id: str
    generation: str = ''
    editor_socket: str = ''
    actor: str = ''
    launch_id: str = ''
    _native: dict = field(default_factory=dict, repr=False)

    def observe(self) -> Observation:
        try:
            screen = self.backend.capture_composer(self.pane_id)
            binding = f'{self.generation}:{screen["binding"]}'
            if screen.get('blocked'):
                return Observation('unknown', binding, 'pane_unavailable_or_in_mode')
            if self.provider != 'omp':
                return inspect_screen(self.provider, screen, binding=binding)
            # The native editor API reads the underlying buffer even when a
            # modal owns keyboard focus. v1 qualifies the default Status Band
            # surface; alternate layouts remain unknown rather than sending
            # keys into a picker. Text emptiness itself still comes from OMP.
            lines = re.sub(r'\x1b\[[0-?]*[ -/]*[@-~]', '', screen['text']).splitlines()
            editors = [i for i, line in enumerate(lines) if re.match(r'^╰─(?: |$)', line)]
            if not editors or screen['cursor_y'] < editors[-1]:
                return Observation('unknown', binding, 'omp_editor_not_focused')
            data = self._editor_request('inspect')
            self._native = data
            binding += ':' + str(data.get('runtime_instance_id', '')) + ':' + str(data.get('session_id', ''))
            state = data.get('state')
            if state not in {'empty', 'nonempty'}:
                state = 'unknown'
            return Observation(state, binding, str(data.get('reason') or state))
        except (ConnectionRefusedError, FileNotFoundError):
            return Observation('unknown', self.generation, 'composer_bridge_unavailable')
        except Exception:
            return Observation('unknown', self.generation, 'composer_unavailable')

    def _editor_request(self, operation: str, **extra) -> dict:
        if not self.editor_socket:
            raise ValueError('OMP editor bridge unavailable')
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(0.5)
            client.connect(self.editor_socket)
            client.sendall((json.dumps({'operation': operation, 'actor': self.actor,
                                      'launch_session_id': self.launch_id, **extra})+'\n').encode())
            response = b''
            while b'\n' not in response and len(response) < 4096:
                part = client.recv(4096)
                if not part:
                    break
                response += part
            return json.loads(response)

    def clear(self, observation: Observation) -> None:
        if self.provider == 'omp':
            self._editor_request('clear', runtime_instance_id=self._native.get('runtime_instance_id'),
                                 session_id=self._native.get('session_id'))
        elif self.backend.send_key(self.pane_id, 'C-c') is False:
            raise RuntimeError('clear key failed')


def guarded_backend(backend: object) -> bool:
    return callable(getattr(backend, 'capture_composer', None))


def target_for_state(provider: str, state: dict) -> DraftTarget:
    return DraftTarget(provider, state.get('backend'), str(state.get('pane_id') or ''),
                       str(state.get('draft_guard_generation') or state.get('launch_session_id') or ''),
                       str(state.get('draft_guard_socket') or ''), str(state.get('actor') or ''),
                       str(state.get('launch_session_id') or ''))


def allow_submission_send(provider: str, state: dict) -> bool:
    if not state.get('draft_guard_enabled'):
        return True
    guard = state.get('_draft_guard')
    if not isinstance(guard, DraftGuard):
        guard = DraftGuard()
        state['_draft_guard'] = guard
    allowed = guard.allows(target_for_state(provider, state))
    state['draft_guard_pending'] = not allowed
    state['draft_guard_reason'] = guard.reason
    return allowed


def guarded_send(state: dict, sender) -> None:
    """An ambiguous terminal write is never retried as an unsent prompt."""
    state['prompt_sent'] = True
    state['draft_guard_pending'] = False
    try:
        sender()
    except Exception as exc:
        state['draft_guard_send_unknown'] = True
        state['draft_guard_send_error'] = type(exc).__name__


def send_unknown_result(submission, *, now):
    from completion.models import CompletionDecision, CompletionStatus, CompletionConfidence
    from .base import ProviderPollResult
    if not submission.runtime_state.get('draft_guard_send_unknown'):
        return None
    return ProviderPollResult(submission=submission, decision=CompletionDecision(
        terminal=True, status=CompletionStatus.FAILED, reason='draft_guard_send_unknown',
        confidence=CompletionConfidence.DEGRADED, reply='', anchor_seen=False,
        reply_started=False, reply_stable=False, provider_turn_ref=None,
        source_cursor=None, finished_at=now,
        diagnostics={'error_type': 'terminal_send_unknown', 'automatic_retry': False},
    ))


def initial_guard_state(provider: str, backend: object, data: dict) -> dict:
    enabled = guarded_backend(backend)
    # An older OMP launcher has no bridge. Do not silently infer native support.
    if provider == 'omp':
        enabled = enabled and data.get('omp_draft_guard_version') == 1
    return {'draft_guard_enabled': enabled,
            'draft_guard_generation': str(data.get('ccb_session_id') or ''),
            'draft_guard_socket': str(data.get('omp_draft_guard_socket') or ''),
            'draft_guard_reason': 'pending' if enabled else 'unprotected_transport'}


def resolve_job_target(job, context) -> DraftTarget | None:
    """Resolve a current managed binding without activating/restarting a pane."""
    if job.provider not in {'codex', 'claude', 'omp'} or context is None:
        return None
    if context.backend_type in {'headless', 'pty-backed'}:
        return None
    if not context.workspace_path:
        raise ValueError('missing workspace')
    from importlib import import_module
    from provider_core.instance_resolution import named_agent_instance
    from terminal_runtime import get_backend_for_session, get_pane_id_from_session

    finder = import_module(f'provider_backends.{job.provider}.session').find_project_session_file
    name = str(getattr(job, 'provider_instance', None) or job.agent_name or job.provider)
    session_file = finder(Path(context.workspace_path), instance=named_agent_instance(name, primary_agent=job.provider))
    if session_file is None:
        raise ValueError('missing managed binding')
    # Session loaders may migrate layouts or write bindings. Pre-claim polling
    # must remain read-only, including while a human is editing the composer.
    data = json.loads(session_file.read_text(encoding='utf-8-sig'))
    if not isinstance(data, dict):
        raise ValueError('invalid managed binding')
    backend = get_backend_for_session(data)
    config = initial_guard_state(job.provider, backend, data)
    if not config['draft_guard_enabled']:
        return None
    pane = get_pane_id_from_session(data)
    if not pane:
        raise ValueError('missing pane')
    return DraftTarget(job.provider, backend, pane, config['draft_guard_generation'],
                       config['draft_guard_socket'], job.agent_name,
                       str(data.get('ccb_session_id') or ''))
