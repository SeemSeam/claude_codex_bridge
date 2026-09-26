from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from completion.models import CompletionSourceKind
from provider_execution.base import ProviderSubmission
from provider_execution.draft_guard import DraftGuard, guarded_send, allow_submission_send
from provider_execution.draft_observation import Observation, inspect_screen

CAPTURES = json.loads((Path(__file__).parent/'fixtures/composer/native-20260920.json').read_text())
INSTALLED_CAPTURES = json.loads((Path(__file__).parent/'fixtures/composer/installed-20260920.json').read_text())


@pytest.mark.parametrize('capture', INSTALLED_CAPTURES, ids=lambda x: x['provider']+'-'+x['case'])
def test_installed_native_status_does_not_confuse_history_or_draft(capture):
    assert inspect_screen(capture['provider'], capture, binding='pane').state == capture['expected']


def test_claude_queued_hint_blocks_even_between_native_turns():
    capture = dict(next(x for x in INSTALLED_CAPTURES if x['provider']=='claude' and x['case']=='human_busy_5'))
    capture['text'] = '\n'.join('' if 'Fiddle-faddling' in line else line for line in capture['text'].split('\n'))
    result = inspect_screen('claude', capture, binding='pane')
    assert result.state == 'unknown'
    assert result.reason == 'provider_native_queue_pending'


@pytest.mark.parametrize('capture', CAPTURES, ids=lambda x: x['provider']+'-'+x['case'])
def test_native_composer_captures(capture):
    empty = {'initial', 'small_empty', 'normal_initial', 'ghost_after_new_turn', 'accepted_cleared',
             'light_ghost', 'ansi_dark_ghost'}
    unknown = {'model_picker', 'busy_connection_probe'}
    # On this narrow Claude screen the top border has scrolled out of view.
    if capture['provider'] == 'claude' and capture['case'] == 'small_multiline':
        expected = 'unknown'
    elif capture['case'] in unknown:
        expected = 'unknown'
    else:
        expected = 'empty' if capture['case'] in empty else 'nonempty'
    result = inspect_screen(capture['provider'], capture, binding='pane-1')
    assert result.state == expected


def test_codex_empty_placeholder_with_terminal_right_padding():
    # Both rust-v0.146.0 and rust-v0.156.1 upstream empty.snap render
    # right padding. Tmux can retain it on styled/background-filled rows.
    screen = {'text': '\n› Ask Codex to do anything' + ' ' * 74 + '\n\n  ? for shortcuts    100% context left  ',
              'cursor_x': 2, 'cursor_y': 1}
    assert inspect_screen('codex', screen, binding='pane').state == 'empty'
    screen['text'] = screen['text'].replace('anything', 'anything plus user draft')
    assert inspect_screen('codex', screen, binding='pane').state == 'nonempty'


class Target:
    def __init__(self, *, clears=True):
        self.state = 'nonempty'
        self.binding = 'pane-1'
        self.clear_count = 0
        self.clears = clears

    def observe(self):
        return Observation(self.state, self.binding, self.state)

    def clear(self, observation):
        self.clear_count += 1
        if self.clears:
            self.state = 'empty'


def test_fixed_180_seconds_no_edit_reset_and_early_empty():
    now = [0.0]
    guard = DraftGuard(clock=lambda: now[0])
    target = Target()
    assert not guard.allows(target)
    for instant in (1, 100, 179.999):
        now[0] = instant
        assert not guard.allows(target)
        assert target.clear_count == 0
    now[0] = 180
    assert guard.allows(target)
    assert target.clear_count == 1
    target.state = 'nonempty'
    assert not guard.allows(target)
    now[0] = 181
    target.state = 'empty'
    assert guard.allows(target)
    assert target.clear_count == 1


@pytest.mark.parametrize('interruption', ['unknown', 'new-binding'])
def test_unknown_or_binding_change_starts_fresh_wait(interruption):
    now = [0.0]
    guard = DraftGuard(clock=lambda: now[0])
    target = Target()
    assert not guard.allows(target)
    now[0] = 179
    if interruption == 'unknown':
        target.state = 'unknown'
    else:
        target.binding = 'pane-2'
    assert not guard.allows(target)
    target.state = 'nonempty'
    now[0] = 200
    assert not guard.allows(target)
    assert target.clear_count == 0


def test_failed_clear_is_not_repeated_and_manual_clear_releases():
    now = [0.0]
    guard = DraftGuard(clock=lambda: now[0])
    target = Target(clears=False)
    assert not guard.allows(target)
    now[0] = 180
    assert not guard.allows(target)
    target.state = 'unknown'
    assert not guard.allows(target)
    target.state = 'nonempty'
    now[0] = 10000
    assert not guard.allows(target)
    assert target.clear_count == 1
    target.state = 'empty'
    assert guard.allows(target)


def test_change_at_clear_boundary_never_sends_clear():
    guard = DraftGuard(clock=lambda: 180, binding='pane-1', since=0)
    target = Target()
    observations = iter([Observation('nonempty', 'pane-1', ''), Observation('unknown', 'pane-1', 'busy')])
    target.observe = lambda: next(observations)
    assert not guard.allows(target)
    assert target.clear_count == 0


def test_send_error_is_not_an_unsent_prompt():
    state = {'prompt_sent': False}
    def failed_send():
        raise OSError('terminal accepted an unknown amount of data')
    guarded_send(state, failed_send)
    assert state['prompt_sent'] is True
    assert state['draft_guard_send_unknown'] is True


def _submission(provider, state):
    return ProviderSubmission('job-test', 'agent-test', provider, '2026-09-20T00:00:00Z',
                              '2026-09-20T00:00:00Z', CompletionSourceKind.SESSION_EVENT_LOG, '',
                              runtime_state=state)


class Backend:
    def __init__(self, provider):
        self.screen = dict(next(x for x in CAPTURES if x['provider']==provider and x['case']=='single'))
        self.sent = []
        self.keys = []

    def capture_composer(self, pane):
        return {**self.screen, 'binding': pane, 'blocked': False}

    def send_text_to_pane(self, pane, text):
        self.sent.append(text)

    def send_key(self, pane, key):
        self.keys.append(key)


@pytest.mark.parametrize('provider', ['codex', 'claude'])
def test_final_sender_defers_and_then_sends_once_without_blind_clear(provider):
    backend = Backend(provider)
    reader = SimpleNamespace(capture_state=lambda: {'cursor': 'fresh'})
    submission = _submission(provider, {'mode': 'active', 'backend': backend, 'pane_id': '%1',
                                       'reader': reader, 'draft_guard_enabled': True,
                                       'prompt_sent': False, 'pending_prompt': 'CCB_TASK', 'prompt_text': 'CCB_TASK'})
    if provider == 'codex':
        from provider_backends.codex.execution_runtime.start import dispatch_guarded_prompt
        dispatch = lambda s: dispatch_guarded_prompt(s, now=s.ready_at)
    else:
        from provider_backends.claude.execution_runtime.polling import _dispatch_deferred_prompt
        prepared = SimpleNamespace(backend=backend, pane_id='%1', reader=reader)
        dispatch = lambda s: _dispatch_deferred_prompt(s, prepared=prepared, now=s.ready_at)
    waiting = dispatch(submission)
    assert not backend.sent and not backend.keys
    assert waiting.runtime_state['prompt_sent'] is False
    assert waiting.runtime_state['draft_guard_pending'] is True
    backend.screen = dict(next(x for x in CAPTURES if x['provider']==provider and x['case']=='initial'))
    sent = dispatch(waiting)
    assert sent.runtime_state['prompt_sent'] is True
    assert sent.runtime_state['state'] == {'cursor': 'fresh'}
    dispatch(sent)
    assert backend.sent == ['CCB_TASK']
    assert backend.keys == []


def test_guarded_claude_does_not_retry_enter_on_folded_paste():
    from provider_backends.claude.execution_runtime.polling import _maybe_resend_activation_enter
    backend = Backend('claude')
    submission = _submission('claude', {'draft_guard_enabled': True, 'prompt_sent': True})
    assert _maybe_resend_activation_enter(submission, prepared=SimpleNamespace(backend=backend),
                                         poll=None, now=submission.ready_at) is None
    assert not backend.keys


def test_cancel_unsent_job_does_not_touch_user_draft():
    from provider_execution.service import _cancel_submission
    backend = Backend('codex')
    calls = []
    adapter = SimpleNamespace(cancel=lambda s: calls.append('cancel'))
    _cancel_submission(adapter, _submission('codex', {'draft_guard_enabled': True, 'prompt_sent': False,
                                                    'backend': backend, 'pane_id': '%1'}))
    assert not calls and not backend.keys


@pytest.mark.parametrize('provider', ['codex', 'claude', 'omp'])
def test_persistence_drops_timer_and_preserves_unsent_guard(tmp_path, provider):
    from provider_execution.service_runtime.persistence import persist_submission
    from provider_execution.state_store import ExecutionStateStore
    from provider_execution.registry import ProviderExecutionRegistry
    from provider_backends.codex.execution import CodexProviderAdapter
    from provider_backends.claude.execution import ClaudeProviderAdapter
    from provider_backends.omp.pane_execution import OmpPaneExecutionAdapter
    from storage.paths import PathLayout
    adapters = {'codex': CodexProviderAdapter, 'claude': ClaudeProviderAdapter,
                'omp': OmpPaneExecutionAdapter}
    adapter = adapters[provider]()
    state = {'mode': 'active', 'draft_guard_enabled': True, 'prompt_sent': False,
             'pending_prompt': 'CCB_TASK', 'prompt_text': 'CCB_TASK',
             '_draft_guard': DraftGuard(since=0, clear_attempted=True)}
    submission = _submission(provider, state)
    store = ExecutionStateStore(PathLayout(tmp_path))
    service = SimpleNamespace(_state_store=store, _active={submission.job_id: submission},
                              _registry=ProviderExecutionRegistry([adapter]),
                              _runtime_contexts={}, _clock=lambda: submission.ready_at)
    persist_submission(service, submission.job_id)
    restored = store.load(submission.job_id).submission.runtime_state
    assert '_draft_guard' not in restored
    assert restored['draft_guard_enabled'] and restored['prompt_sent'] is False
    if provider == 'codex':
        assert restored['pending_prompt'] == 'CCB_TASK'
    if provider in {'codex', 'claude'}:
        from test_provider_execution_active_resume import _job, _context
        from importlib import import_module
        resume = import_module(f'provider_backends.{provider}.execution_runtime.start').resume_submission
        backend = Backend(provider)
        reader = SimpleNamespace(set_preferred_session=lambda path: None)
        session = SimpleNamespace(data={}, ensure_pane=lambda: (True, '%1'))
        result = resume(_job(), replace(submission, runtime_state=restored), context=_context(tmp_path),
                        load_session_fn=lambda *args, **kwargs: session, backend_for_session_fn=lambda data: backend,
                        reader_factory=lambda *args: reader)
        assert result is not None
        assert not allow_submission_send(provider, result.runtime_state)
        assert result.runtime_state['_draft_guard'].since is not None
        assert not result.runtime_state['_draft_guard'].clear_attempted
        assert not backend.sent and not backend.keys


def test_draft_wait_is_not_execution_timeout_and_send_restarts_progress():
    from provider_execution.base import ProviderPollResult
    from provider_execution.service_runtime.reliability import timeout_poll_result, apply_reliability_progress
    waiting = _submission('codex', {'draft_guard_enabled': True, 'prompt_sent': False,
                                    'reliability_last_progress_at': '2026-09-01T00:00:00Z'})
    now = '2026-09-20T00:00:00Z'
    assert timeout_poll_result(None, job_id=waiting.job_id, submission=waiting,
                               adapter=None, now=now) is None
    sent = replace(waiting, runtime_state={**waiting.runtime_state, 'prompt_sent': True})
    result = apply_reliability_progress(ProviderPollResult(submission=sent), previous_submission=waiting, now=now)
    assert result.submission.runtime_state['reliability_last_progress_at'] == now


def test_tracker_does_not_count_draft_wait_as_provider_time():
    from ccbd.services.dispatcher_runtime.polling_service import _ingest_update_items
    events = []
    tracker = SimpleNamespace(finish=lambda job: events.append(('finish', job)),
                              current=lambda job: None,
                              start=lambda job, started_at: events.append(('start', started_at)))
    dispatcher = SimpleNamespace(_completion_tracker=tracker)
    state = {'draft_guard_enabled': True, 'prompt_sent': False}
    update = SimpleNamespace(job_id='job-test', submission=_submission('codex', state), items=())
    current = SimpleNamespace(updated_at='old-time')
    assert _ingest_update_items(dispatcher, current, update) is None
    state.update(prompt_sent=True, prompt_sent_at='actual-send-time')
    assert _ingest_update_items(dispatcher, current, update) is None
    assert events == [('finish', 'job-test'), ('start', 'actual-send-time')]


def test_omp_final_guard_restore_and_send_failure_do_not_duplicate(tmp_path, monkeypatch):
    from test_omp_pane_execution import (_runtime, _event, _append, _FakeBackend, _FakeSession,
                                         _job, _context, NOW)
    from provider_backends.omp.pane_execution import OmpPaneExecutionAdapter
    from provider_backends.pi import pane_execution
    from provider_execution.draft_guard import DraftTarget
    data, events, dispatch = _runtime(tmp_path)
    data['omp_draft_guard_version'] = 1
    _append(events, _event('extension_ready'))
    backend = _FakeBackend(fail_send=True)
    backend.capture_composer = lambda pane: {}
    target = Target()
    monkeypatch.setattr(DraftTarget, 'observe', lambda self: target.observe())
    monkeypatch.setattr(pane_execution, 'get_backend_for_session', lambda data: backend)
    adapter = OmpPaneExecutionAdapter()
    adapter.session_loader = lambda *args, **kwargs: _FakeSession(data)
    waiting = adapter.start(_job(), context=_context(tmp_path), now=NOW)
    assert waiting.runtime_state['prompt_sent'] is False
    assert dispatch.read_text() == ''
    persisted = replace(waiting, runtime_state=adapter.export_runtime_state(waiting))
    restored = adapter.resume(_job(), persisted, context=_context(tmp_path), persisted_state=None, now=NOW)
    assert restored is not None and '_draft_guard' not in restored.runtime_state
    polled = adapter.poll(restored, now=NOW)
    assert not polled.submission.runtime_state['prompt_sent']
    assert dispatch.read_text() == ''
    target.state = 'empty'
    failed = adapter.poll(polled.submission, now=NOW)
    assert failed.decision.reason == 'draft_guard_send_unknown'
    assert failed.submission.runtime_state['prompt_sent'] is True
    assert len(dispatch.read_text().splitlines()) == 1


def test_omp_picker_never_queries_or_clears_underlying_editor():
    from provider_execution.draft_guard import DraftTarget
    backend = SimpleNamespace(capture_composer=lambda pane: {
        'text': 'Select model\n╰─', 'cursor_y': 0, 'binding': 'pane'})
    target = DraftTarget('omp', backend, '%1')
    target._editor_request = lambda *args, **kwargs: pytest.fail('modal must veto native editor access')
    assert target.observe().reason == 'omp_editor_not_focused'


@pytest.mark.parametrize('provider', ['codex', 'claude', 'omp'])
def test_preclaim_binding_resolution_is_read_only(tmp_path, monkeypatch, provider):
    from importlib import import_module
    from provider_execution.draft_guard import resolve_job_target
    session_module = import_module(f'provider_backends.{provider}.session')
    session_file = tmp_path/'session.json'
    session_file.write_text(json.dumps({'ccb_session_id': 'launch', 'omp_draft_guard_version': 1}))
    before = session_file.read_bytes()
    monkeypatch.setattr(session_module, 'find_project_session_file', lambda *args, **kwargs: session_file)
    monkeypatch.setattr(session_module, 'load_project_session', lambda *args, **kwargs: pytest.fail('mutating loader'))
    monkeypatch.setattr('terminal_runtime.get_backend_for_session', lambda data: Backend('codex'))
    monkeypatch.setattr('terminal_runtime.get_pane_id_from_session', lambda data: '%1')
    job = SimpleNamespace(provider=provider, agent_name='agent1', provider_instance=None)
    context = SimpleNamespace(workspace_path=str(tmp_path), backend_type='tmux')
    target = resolve_job_target(job, context)
    assert target.provider == provider and target.pane_id == '%1'
    assert session_file.read_bytes() == before
    context.backend_type = 'headless'
    assert resolve_job_target(job, context) is None
    monkeypatch.setattr(session_module, 'find_project_session_file', lambda *args, **kwargs: None)
    context.backend_type = 'tmux'
    with pytest.raises(ValueError, match='missing managed binding'):
        resolve_job_target(job, context)
