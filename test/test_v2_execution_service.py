from __future__ import annotations

import json
from pathlib import Path

import pytest

from ccbd.api_models import DeliveryScope, JobRecord, JobStatus, MessageEnvelope
from completion.models import CompletionConfidence, CompletionItemKind, CompletionSourceKind, CompletionStatus
from provider_execution.base import ProviderRuntimeContext
from provider_execution.base import ProviderSubmission
from provider_execution.reliability import CompletionReliabilityPolicy
from provider_execution.registry import ProviderExecutionRegistry, build_default_execution_registry
from provider_execution.service import ExecutionService
from provider_execution.state_store import ExecutionStateStore
from storage.paths import PathLayout


def _job(*, job_id: str = 'job_1', task_id: str | None = None, body: str = 'hello') -> JobRecord:
    return JobRecord(
        job_id=job_id,
        submission_id=None,
        agent_name='agent1',
        provider='fake',
        request=MessageEnvelope(
            project_id='proj',
            to_agent='agent1',
            from_actor='user',
            body=body,
            task_id=task_id,
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        ),
        status=JobStatus.RUNNING,
        terminal_decision=None,
        cancel_requested_at=None,
        created_at='2026-03-18T00:00:00Z',
        updated_at='2026-03-18T00:00:00Z',
    )


def _job_for_provider(provider: str, *, job_id: str = 'job_1', task_id: str | None = None, body: str = 'hello') -> JobRecord:
    job = _job(job_id=job_id, task_id=task_id, body=body)
    job.provider = provider
    job.agent_name = 'agent1'
    job.request = MessageEnvelope(
        project_id=job.request.project_id,
        to_agent=job.request.to_agent,
        from_actor=job.request.from_actor,
        body=job.request.body,
        task_id=job.request.task_id,
        reply_to=job.request.reply_to,
        message_type=job.request.message_type,
        delivery_scope=job.request.delivery_scope,
    )
    return job


def _anchored_job_for_provider(
    provider: str,
    request_anchor: str,
    *,
    task_id: str | None = None,
    body: str = 'hello',
) -> JobRecord:
    return _job_for_provider(provider, job_id=request_anchor, task_id=task_id, body=body)


def _runtime_context(tmp_path: Path) -> ProviderRuntimeContext:
    return ProviderRuntimeContext(
        agent_name='agent1',
        workspace_path=str(tmp_path),
        backend_type='pane-backed',
        runtime_ref='codex:agent1:attached',
        session_ref='session:agent1',
        runtime_pid=123,
        runtime_health='healthy',
    )


def test_execution_service_completes_fake_provider_jobs() -> None:
    ticks = iter([
        '2026-03-18T00:00:00Z',
        '2026-03-18T00:00:00.100000Z',
        '2026-03-18T00:00:00.300000Z',
    ])
    service = ExecutionService(build_default_execution_registry(), clock=lambda: next(ticks))
    service.start(_job())
    first = service.poll()
    assert len(first) == 1
    assert first[0].job_id == 'job_1'
    assert first[0].decision is None
    assert [item.kind for item in first[0].items] == [
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.ASSISTANT_CHUNK,
    ]
    assert [item.cursor.event_seq for item in first[0].items] == [1, 2]

    completed = service.poll()
    assert len(completed) == 1
    update = completed[0]
    assert update.job_id == 'job_1'
    assert [item.kind for item in update.items] == [CompletionItemKind.RESULT]
    assert update.items[0].cursor.event_seq == 3
    assert update.decision is not None
    assert update.decision.status is CompletionStatus.COMPLETED
    assert update.decision.reply == 'FAKE[agent1] hello'
    assert update.decision.reason == 'result_message'
    assert update.decision.confidence is CompletionConfidence.EXACT


@pytest.mark.parametrize(
    ('task_id', 'expected_status', 'expected_reason', 'expected_confidence'),
    [
        ('fake;status=failed;reason=api_error;confidence=exact;latency_ms=0', CompletionStatus.FAILED, 'api_error', CompletionConfidence.EXACT),
        ('fake;status=cancelled;reason=cancel_info;confidence=exact;latency_ms=0', CompletionStatus.CANCELLED, 'cancel_info', CompletionConfidence.EXACT),
        ('fake;status=incomplete;reason=timeout;confidence=degraded;latency_ms=0', CompletionStatus.INCOMPLETE, 'timeout', CompletionConfidence.DEGRADED),
        ('fake;status=completed;reason=result_message;confidence=observed;latency_ms=0', CompletionStatus.COMPLETED, 'result_message', CompletionConfidence.OBSERVED),
    ],
)
def test_execution_service_supports_scripted_fake_terminal_states(
    task_id: str,
    expected_status: CompletionStatus,
    expected_reason: str,
    expected_confidence: CompletionConfidence,
) -> None:
    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:01Z')
    service.start(_job(task_id=task_id, body='scenario'))
    completed = service.poll()
    assert len(completed) == 1
    update = completed[0]
    assert update.job_id == 'job_1'
    assert len(update.items) >= 1
    decision = update.decision
    assert decision is not None
    assert decision.status is expected_status
    assert decision.reason == expected_reason
    assert decision.confidence is expected_confidence
    assert decision.reply == 'FAKE[agent1] scenario'


def test_execution_service_supports_json_scripted_fake_events() -> None:
    task_id = (
        'fake;script='
        '[{"t":0,"type":"anchor_seen"},'
        '{"t":10,"type":"assistant_chunk","text":"hello"},'
        '{"t":20,"type":"assistant_chunk","text":" world"},'
        '{"t":30,"type":"result","reply":"hello world"}]'
    )
    ticks = iter(['2026-03-18T00:00:00Z', '2026-03-18T00:00:01Z'])
    service = ExecutionService(build_default_execution_registry(), clock=lambda: next(ticks))
    service.start(_job(task_id=task_id, body='ignored'))

    update = service.poll()[0]
    assert [item.kind for item in update.items] == [
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.ASSISTANT_CHUNK,
        CompletionItemKind.ASSISTANT_CHUNK,
        CompletionItemKind.RESULT,
    ]
    assert [item.cursor.event_seq for item in update.items] == [1, 2, 3, 4]
    assert update.items[1].payload['merged_text'] == 'hello'
    assert update.items[2].payload['merged_text'] == 'hello world'
    assert update.decision is not None
    assert update.decision.reply == 'hello world'


def test_execution_service_supports_fake_codex_protocol_turn_defaults() -> None:
    ticks = iter(['2026-03-18T00:00:00Z', '2026-03-18T00:00:01Z'])
    service = ExecutionService(build_default_execution_registry(), clock=lambda: next(ticks))
    service.start(_job_for_provider('fake-codex', body='codex path'))
    update = service.poll()[0]
    assert [item.kind for item in update.items] == [
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.ASSISTANT_CHUNK,
        CompletionItemKind.TURN_BOUNDARY,
    ]
    assert update.decision is not None
    assert update.decision.status is CompletionStatus.COMPLETED


def test_execution_service_supports_fake_gemini_observed_defaults_without_terminal_decision() -> None:
    ticks = iter(['2026-03-18T00:00:00Z', '2026-03-18T00:00:01Z'])
    service = ExecutionService(build_default_execution_registry(), clock=lambda: next(ticks))
    service.start(_job_for_provider('fake-gemini', body='gemini path'))
    update = service.poll()[0]
    assert [item.kind for item in update.items] == [
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.SESSION_SNAPSHOT,
    ]
    assert update.decision is None


def test_execution_service_claude_adapter_fails_without_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from provider_execution import claude as claude_adapter_module

    monkeypatch.setattr(claude_adapter_module, 'load_project_session', lambda work_dir, instance=None: None)
    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z')
    service.start(_job_for_provider('claude', body='real claude'), runtime_context=_runtime_context(tmp_path))
    update = service.poll()[0]
    assert [item.kind for item in update.items] == [CompletionItemKind.ERROR]
    assert update.items[0].payload['reason'] == 'runtime_unavailable'
    assert 'missing_claude_session' in update.items[0].payload['error']
    assert update.decision is not None
    assert update.decision.status is CompletionStatus.FAILED
    assert update.decision.reason == 'runtime_unavailable'


def test_execution_state_summary_lists_recoverable_and_nonrecoverable_providers(tmp_path: Path) -> None:
    layout = PathLayout(tmp_path)
    state_store = ExecutionStateStore(layout)
    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z', state_store=state_store)

    service.start(_job_for_provider('fake', job_id='job_fake', body='recoverable'), runtime_context=_runtime_context(tmp_path))
    service.start(_job_for_provider('opencode', job_id='job_opencode', body='nonrecoverable'), runtime_context=_runtime_context(tmp_path))

    summary = state_store.summary()
    assert summary['active_execution_count'] == 2
    assert summary['recoverable_execution_providers'] == ['fake']
    assert summary['nonrecoverable_execution_providers'] == ['opencode']

    persisted = state_store.load('job_opencode')
    assert persisted is not None
    assert persisted.resume_capable is False
    assert persisted.submission.diagnostics['resume_supported'] is False
    assert persisted.submission.diagnostics['restore_mode'] == 'resubmit_required'
    assert persisted.submission.diagnostics['restore_reason'] == 'provider_resume_unsupported'


def test_execution_service_claude_adapter_emits_session_boundary_items_from_log(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from provider_execution import claude as claude_adapter_module

    fixed_req_id = 'job_1'
    sent: list[tuple[str, str]] = []

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            sent.append((pane_id, text))

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%2'

    class FakeSession:
        data = {}
        claude_session_path = str(tmp_path / 'claude-session.jsonl')
        work_dir = str(tmp_path)

        def ensure_pane(self):
            return True, '%2'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._calls = 0
            self._events = [
                ('user', f'CCB_REQ_ID: {fixed_req_id}\n\nprompt'),
                ('assistant', 'partial'),
                ('assistant', f'final\nCCB_DONE: {fixed_req_id}'),
            ]

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': str(tmp_path / 'claude-session.jsonl'), 'offset': 0}

        def try_get_events(self, state):
            index = int(state.get('index', 0))
            if index >= len(self._events):
                return [], state
            return [self._events[index]], {**state, 'index': index + 1}

    monkeypatch.setattr(claude_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(claude_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(claude_adapter_module, 'ClaudeLogReader', FakeReader)

    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z')
    service.start(_job_for_provider('claude', body='real claude'), runtime_context=_runtime_context(tmp_path))
    update = service.poll()[0]

    assert sent and sent[0][0] == '%2'
    assert fixed_req_id in sent[0][1]
    assert 'real claude' in sent[0][1]
    assert 'Async Ask' not in sent[0][1]
    assert 'command ask' not in sent[0][1]
    assert 'SKILL.md' not in sent[0][1]
    assert [item.kind for item in update.items] == [
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.ASSISTANT_CHUNK,
        CompletionItemKind.ASSISTANT_CHUNK,
        CompletionItemKind.TURN_BOUNDARY,
    ]
    assert update.items[-1].payload['last_agent_message'] == 'partial\nfinal'
    assert update.decision is None


def test_execution_service_claude_adapter_respects_no_wrap_provider_option(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from provider_execution import claude as claude_adapter_module

    sent: list[tuple[str, str]] = []

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            sent.append((pane_id, text))

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%2'

    class FakeSession:
        data = {}
        claude_session_path = str(tmp_path / 'claude-session.jsonl')
        work_dir = str(tmp_path)

        def ensure_pane(self):
            return True, '%2'

    class EmptyReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': str(tmp_path / 'claude-session.jsonl'), 'offset': 0}

        def try_get_events(self, state):
            return [], state

    monkeypatch.setattr(claude_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(claude_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(claude_adapter_module, 'ClaudeLogReader', EmptyReader)

    job = _job_for_provider('claude', body='raw claude prompt')
    job.provider_options = {'no_wrap': True}

    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z')
    service.start(job, runtime_context=_runtime_context(tmp_path))

    assert sent == []
    assert service.poll() == ()
    assert sent == [('%2', 'raw claude prompt')]
    assert service._active[job.job_id].runtime_state['anchor_seen'] is True
    assert service._active[job.job_id].runtime_state['no_wrap'] is True


def test_execution_service_claude_adapter_completes_on_turn_duration_without_done_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from provider_execution import claude as claude_adapter_module

    fixed_req_id = '20260318-000000-000-3-2'
    sent: list[tuple[str, str]] = []

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            sent.append((pane_id, text))

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%2'

    class FakeSession:
        data = {}
        claude_session_path = str(tmp_path / 'claude-session.jsonl')
        work_dir = str(tmp_path)

        def ensure_pane(self):
            return True, '%2'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._events = [
                {'role': 'user', 'text': f'CCB_REQ_ID: {fixed_req_id}\n\nprompt', 'entry_type': 'user'},
                {'role': 'assistant', 'text': 'final without done', 'entry_type': 'assistant', 'uuid': 'assistant-1'},
                {'role': 'system', 'text': '', 'entry_type': 'system', 'subtype': 'turn_duration', 'parent_uuid': 'assistant-1'},
            ]

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': str(tmp_path / 'claude-session.jsonl'), 'offset': 0}

        def try_get_entries(self, state):
            index = int(state.get('index', 0))
            if index >= len(self._events):
                return [], state
            return [self._events[index]], {**state, 'index': index + 1}

    monkeypatch.setattr(claude_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(claude_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(claude_adapter_module, 'ClaudeLogReader', FakeReader)

    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z')
    service.start(_anchored_job_for_provider('claude', fixed_req_id, body='real claude'), runtime_context=_runtime_context(tmp_path))
    update = service.poll()[0]

    assert sent and sent[0][0] == '%2'
    assert fixed_req_id in sent[0][1]
    assert [item.kind for item in update.items] == [
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.ASSISTANT_CHUNK,
        CompletionItemKind.TURN_BOUNDARY,
    ]
    assert update.items[-1].payload['reason'] == 'turn_duration'
    assert update.items[-1].payload['last_agent_message'] == 'final without done'


def test_execution_service_claude_adapter_emits_boundary_on_end_turn_without_done_marker(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from provider_execution import claude as claude_adapter_module

    fixed_req_id = '20260318-000000-000-3-end-turn'

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            del pane_id, text

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%2'

    class FakeSession:
        data = {}
        claude_session_path = str(tmp_path / 'claude-session.jsonl')
        work_dir = str(tmp_path)

        def ensure_pane(self):
            return True, '%2'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._events = [
                {'role': 'user', 'text': f'CCB_REQ_ID: {fixed_req_id}\n\nprompt', 'entry_type': 'user'},
                {
                    'role': 'assistant',
                    'text': 'final via end turn',
                    'entry_type': 'assistant',
                    'uuid': 'assistant-1',
                    'stop_reason': 'end_turn',
                },
            ]

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': str(tmp_path / 'claude-session.jsonl'), 'offset': 0}

        def try_get_entries(self, state):
            index = int(state.get('index', 0))
            if index >= len(self._events):
                return [], state
            return [self._events[index]], {**state, 'index': index + 1}

    monkeypatch.setattr(claude_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(claude_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(claude_adapter_module, 'ClaudeLogReader', FakeReader)

    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z')
    service.start(_anchored_job_for_provider('claude', fixed_req_id, body='real claude'), runtime_context=_runtime_context(tmp_path))
    update = service.poll()[0]

    assert [item.kind for item in update.items] == [
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.ASSISTANT_CHUNK,
        CompletionItemKind.TURN_BOUNDARY,
    ]
    assert update.items[-1].payload['reason'] == 'assistant_end_turn'
    assert update.items[-1].payload['stop_reason'] == 'end_turn'
    assert update.items[-1].payload['assistant_uuid'] == 'assistant-1'
    assert update.items[-1].payload['last_agent_message'] == 'final via end turn'


def test_execution_service_claude_adapter_prefers_exact_hook_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from provider_execution import claude as claude_adapter_module
    from provider_hooks.artifacts import write_event

    fixed_req_id = '20260318-000000-000-3-hook'
    completion_dir = tmp_path / 'completion'
    sent: list[tuple[str, str]] = []

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            sent.append((pane_id, text))

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%2'

    class FakeSession:
        data = {
            'completion_artifact_dir': str(completion_dir),
        }
        claude_session_path = str(tmp_path / 'claude-session.jsonl')
        work_dir = str(tmp_path)

        def ensure_pane(self):
            return True, '%2'

    class EmptyReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': str(tmp_path / 'claude-session.jsonl'), 'offset': 0}

        def try_get_entries(self, state):
            return [], state

    write_event(
        provider='claude',
        completion_dir=completion_dir,
        agent_name='agent1',
        workspace_path=str(tmp_path),
        req_id=fixed_req_id,
        status='completed',
        reply='exact hook reply',
        session_id='claude-session-id',
        hook_event_name='Stop',
    )

    monkeypatch.setattr(claude_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(claude_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(claude_adapter_module, 'ClaudeLogReader', EmptyReader)

    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z')
    service.start(_anchored_job_for_provider('claude', fixed_req_id, body='real claude'), runtime_context=_runtime_context(tmp_path))
    update = service.poll()[0]

    assert [item.kind for item in update.items] == [CompletionItemKind.ASSISTANT_FINAL]
    assert update.items[0].payload['reply'] == 'exact hook reply'
    assert update.decision is not None
    assert update.decision.status is CompletionStatus.COMPLETED
    assert update.decision.confidence is CompletionConfidence.EXACT
    assert update.decision.reason == 'hook_stop'
    assert sent and sent[0][0] == '%2'
    assert fixed_req_id in sent[0][1]
    assert 'CCB_DONE:' not in sent[0][1]


def test_execution_service_claude_exact_hook_submission_uses_strict_tmux_sender(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from provider_execution import claude as claude_adapter_module
    from provider_hooks.artifacts import write_event

    fixed_req_id = '20260318-000000-000-3-strict'
    completion_dir = tmp_path / 'completion'
    strict_sent: list[tuple[str, str]] = []

    class FakeBackend:
        def send_text_to_pane(self, pane_id: str, text: str) -> None:
            strict_sent.append((pane_id, text))

        def send_text(self, pane_id: str, text: str) -> None:
            raise AssertionError(f'legacy sender should not be used: {pane_id} {text}')

    class FakeSession:
        data = {
            'completion_artifact_dir': str(completion_dir),
        }
        claude_session_path = str(tmp_path / 'claude-session.jsonl')
        work_dir = str(tmp_path)

        def ensure_pane(self):
            return True, '%2'

    class EmptyReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': str(tmp_path / 'claude-session.jsonl'), 'offset': 0}

        def try_get_entries(self, state):
            return [], state

    write_event(
        provider='claude',
        completion_dir=completion_dir,
        agent_name='agent1',
        workspace_path=str(tmp_path),
        req_id=fixed_req_id,
        status='completed',
        reply='exact hook reply',
        session_id='claude-session-id',
        hook_event_name='Stop',
    )

    monkeypatch.setattr(claude_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(claude_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(claude_adapter_module, 'ClaudeLogReader', EmptyReader)

    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z')
    service.start(_anchored_job_for_provider('claude', fixed_req_id, body='real claude'), runtime_context=_runtime_context(tmp_path))
    update = service.poll()[0]

    assert update.decision is not None
    assert len(strict_sent) == 1
    assert strict_sent[0][0] == '%2'
    assert fixed_req_id in strict_sent[0][1]


def test_execution_service_claude_adapter_ignores_subagent_turn_boundary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from provider_execution import claude as claude_adapter_module

    fixed_req_id = '20260318-000000-000-3-subagent'

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            del pane_id, text

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%2'

    class FakeSession:
        data = {}
        claude_session_path = str(tmp_path / 'claude-session.jsonl')
        work_dir = str(tmp_path)

        def ensure_pane(self):
            return True, '%2'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._events = [
                {'role': 'user', 'text': f'CCB_REQ_ID: {fixed_req_id}\n\nprompt', 'entry_type': 'user'},
                {'role': 'assistant', 'text': 'main partial', 'entry_type': 'assistant', 'uuid': 'assistant-main'},
                {
                    'role': 'assistant',
                    'text': 'child tool work',
                    'entry_type': 'assistant',
                    'uuid': 'assistant-child',
                    'subagent_id': 'child-1',
                    'stop_reason': 'end_turn',
                },
                {'role': 'system', 'text': '', 'entry_type': 'system', 'subtype': 'turn_duration', 'parent_uuid': 'assistant-child'},
            ]

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': str(tmp_path / 'claude-session.jsonl'), 'offset': 0}

        def try_get_entries(self, state):
            index = int(state.get('index', 0))
            if index >= len(self._events):
                return [], state
            return [self._events[index]], {**state, 'index': index + 1}

    monkeypatch.setattr(claude_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(claude_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(claude_adapter_module, 'ClaudeLogReader', FakeReader)

    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z')
    service.start(_anchored_job_for_provider('claude', fixed_req_id, body='real claude'), runtime_context=_runtime_context(tmp_path))
    update = service.poll()[0]

    assert [item.kind for item in update.items] == [
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.ASSISTANT_CHUNK,
        CompletionItemKind.ASSISTANT_CHUNK,
    ]
    assert update.items[-1].payload['subagent_id'] == 'child-1'
    assert update.decision is None


def test_execution_service_claude_adapter_fails_on_terminal_api_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from provider_execution import claude as claude_adapter_module

    fixed_req_id = '20260318-000000-000-3-api-error'

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            del pane_id, text

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%2'

    class FakeSession:
        data = {}
        claude_session_path = str(tmp_path / 'claude-session.jsonl')
        work_dir = str(tmp_path)

        def ensure_pane(self):
            return True, '%2'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._events = [
                {'role': 'user', 'text': f'CCB_REQ_ID: {fixed_req_id}\n\nprompt', 'entry_type': 'user'},
                {
                    'role': 'system',
                    'text': '',
                    'entry_type': 'system',
                    'subtype': 'api_error',
                    'entry': {
                        'type': 'system',
                        'subtype': 'api_error',
                        'timestamp': '2026-03-18T00:00:02Z',
                        'retryAttempt': 10,
                        'maxRetries': 10,
                        'cause': {
                            'code': 'ConnectionRefused',
                            'path': 'http://127.0.0.1:15722/v1/messages?beta=true',
                        },
                    },
                },
            ]

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': str(tmp_path / 'claude-session.jsonl'), 'offset': 0}

        def try_get_entries(self, state):
            index = int(state.get('index', 0))
            if index >= len(self._events):
                return [], state
            return [self._events[index]], {**state, 'index': index + 1}

    monkeypatch.setattr(claude_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(claude_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(claude_adapter_module, 'ClaudeLogReader', FakeReader)

    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z')
    service.start(_anchored_job_for_provider('claude', fixed_req_id, body='real claude'), runtime_context=_runtime_context(tmp_path))
    update = service.poll()[0]

    assert [item.kind for item in update.items] == [
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.ERROR,
    ]
    assert update.items[-1].payload['reason'] == 'api_error'
    assert update.items[-1].payload['error_code'] == 'ConnectionRefused'
    assert update.decision is not None
    assert update.decision.status is CompletionStatus.FAILED
    assert update.decision.reason == 'api_error'
    assert update.decision.confidence is CompletionConfidence.OBSERVED


def test_execution_service_claude_adapter_fails_on_pre_anchor_terminal_api_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from provider_execution import claude as claude_adapter_module

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            del pane_id, text

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%2'

    class FakeSession:
        data = {}
        claude_session_path = str(tmp_path / 'claude-session.jsonl')
        work_dir = str(tmp_path)

        def ensure_pane(self):
            return True, '%2'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._events = [
                {
                    'role': 'system',
                    'text': '',
                    'entry_type': 'system',
                    'subtype': 'api_error',
                    'entry': {
                        'type': 'system',
                        'subtype': 'api_error',
                        'timestamp': '2026-03-18T00:00:02Z',
                        'retryAttempt': 3,
                        'maxRetries': 3,
                        'cause': {
                            'code': 'Unauthorized',
                            'path': 'https://api.anthropic.com/v1/messages',
                        },
                    },
                },
            ]

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': str(tmp_path / 'claude-session.jsonl'), 'offset': 0}

        def try_get_entries(self, state):
            index = int(state.get('index', 0))
            if index >= len(self._events):
                return [], state
            return [self._events[index]], {**state, 'index': index + 1}

    monkeypatch.setattr(claude_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(claude_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(claude_adapter_module, 'ClaudeLogReader', FakeReader)
    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z')
    service.start(_job_for_provider('claude', body='real claude'), runtime_context=_runtime_context(tmp_path))
    update = service.poll()[0]

    assert [item.kind for item in update.items] == [CompletionItemKind.ERROR]
    assert update.items[-1].payload['reason'] == 'api_error'
    assert update.items[-1].payload['error_code'] == 'Unauthorized'
    assert update.decision is not None
    assert update.decision.status is CompletionStatus.FAILED
    assert update.decision.reason == 'api_error'
    assert update.decision.anchor_seen is False


def test_execution_service_claude_adapter_advances_state_across_nonterminal_api_errors(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from provider_execution import claude as claude_adapter_module

    fixed_req_id = '20260318-000000-000-3-api-retry'

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            del pane_id, text

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%2'

    class FakeSession:
        data = {}
        claude_session_path = str(tmp_path / 'claude-session.jsonl')
        work_dir = str(tmp_path)

        def ensure_pane(self):
            return True, '%2'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': str(tmp_path / 'claude-session.jsonl'), 'phase': 0, 'ready': True}

        def try_get_entries(self, state):
            if not state.get('ready', True):
                return [], {**state, 'ready': True}
            phase = int(state.get('phase', 0))
            if phase == 0:
                return [
                    {'role': 'user', 'text': f'CCB_REQ_ID: {fixed_req_id}\n\nprompt', 'entry_type': 'user'},
                ], {**state, 'phase': 1, 'ready': False}
            if phase == 1:
                return [
                    {
                        'role': 'system',
                        'text': '',
                        'entry_type': 'system',
                        'subtype': 'api_error',
                        'entry': {
                            'type': 'system',
                            'subtype': 'api_error',
                            'timestamp': '2026-03-18T00:00:02Z',
                            'retryAttempt': 1,
                            'maxRetries': 2,
                            'cause': {'code': 'ConnectionRefused', 'path': 'http://127.0.0.1:15722/v1/messages?beta=true'},
                        },
                    },
                ], {**state, 'phase': 2, 'ready': False}
            if phase == 2:
                return [
                    {
                        'role': 'system',
                        'text': '',
                        'entry_type': 'system',
                        'subtype': 'api_error',
                        'entry': {
                            'type': 'system',
                            'subtype': 'api_error',
                            'timestamp': '2026-03-18T00:00:03Z',
                            'retryAttempt': 2,
                            'maxRetries': 2,
                            'cause': {'code': 'ConnectionRefused', 'path': 'http://127.0.0.1:15722/v1/messages?beta=true'},
                        },
                    },
                ], {**state, 'phase': 3, 'ready': False}
            return [], state

    monkeypatch.setattr(claude_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(claude_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(claude_adapter_module, 'ClaudeLogReader', FakeReader)
    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z')
    service.start(_anchored_job_for_provider('claude', fixed_req_id, body='real claude'), runtime_context=_runtime_context(tmp_path))

    first = service.poll()[0]
    assert [item.kind for item in first.items] == [CompletionItemKind.ANCHOR_SEEN]

    second = service.poll()
    assert second == ()

    third = service.poll()[0]
    assert [item.kind for item in third.items] == [CompletionItemKind.ERROR]
    assert third.decision is not None
    assert third.decision.status is CompletionStatus.FAILED
    assert third.decision.reason == 'api_error'


def test_execution_service_claude_adapter_reports_pane_dead(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from provider_execution import claude as claude_adapter_module

    class DeadBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            del pane_id, text

        def is_alive(self, pane_id: str) -> bool:
            del pane_id
            return False

    class FakeSession:
        data = {}
        claude_session_path = str(tmp_path / 'claude-session.jsonl')
        work_dir = str(tmp_path)

        def ensure_pane(self):
            return True, '%2'

    class EmptyReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': str(tmp_path / 'claude-session.jsonl'), 'offset': 0}

        def try_get_events(self, state):
            return [], state

    monkeypatch.setattr(claude_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(claude_adapter_module, 'get_backend_for_session', lambda data: DeadBackend())
    monkeypatch.setattr(claude_adapter_module, 'ClaudeLogReader', EmptyReader)

    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z')
    service.start(_job_for_provider('claude'), runtime_context=_runtime_context(tmp_path))
    update = service.poll()[0]
    assert [item.kind for item in update.items] == [CompletionItemKind.PANE_DEAD]


def test_execution_service_claude_adapter_reanchors_after_session_rotate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from provider_execution import claude as claude_adapter_module

    fixed_req_id = '20260318-000000-000-3-rotate'

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            del pane_id, text

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%2'

    class FakeSession:
        data = {}
        claude_session_path = str(tmp_path / 'claude-session-old.jsonl')
        work_dir = str(tmp_path)

        def ensure_pane(self):
            return True, '%2'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._calls = 0

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': str(tmp_path / 'claude-session-old.jsonl'), 'offset': 0, 'phase': 0, 'ready': True}

        def try_get_entries(self, state):
            if not state.get('ready', True):
                return [], {**state, 'ready': True}
            phase = int(state.get('phase', 0))
            if phase == 0:
                return [
                    {'role': 'user', 'text': f'CCB_REQ_ID: {fixed_req_id}\n\nprompt', 'entry_type': 'user'},
                    {'role': 'assistant', 'text': 'old partial', 'entry_type': 'assistant', 'uuid': 'assistant-1'},
                ], {**state, 'offset': 1, 'phase': 1, 'ready': False}
            if phase == 1:
                return [
                    {'role': 'user', 'text': f'CCB_REQ_ID: {fixed_req_id}\n\nprompt again', 'entry_type': 'user'},
                    {'role': 'assistant', 'text': 'new partial', 'entry_type': 'assistant', 'uuid': 'assistant-2'},
                ], {**state, 'session_path': str(tmp_path / 'claude-session-new.jsonl'), 'offset': 2, 'phase': 2, 'ready': False}
            return [], state

    monkeypatch.setattr(claude_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(claude_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(claude_adapter_module, 'ClaudeLogReader', FakeReader)
    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z')
    service.start(_anchored_job_for_provider('claude', fixed_req_id, body='real claude'), runtime_context=_runtime_context(tmp_path))

    first = service.poll()[0]
    assert [item.kind for item in first.items] == [
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.ASSISTANT_CHUNK,
    ]

    second = service.poll()[0]
    assert [item.kind for item in second.items] == [
        CompletionItemKind.SESSION_ROTATE,
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.ASSISTANT_CHUNK,
    ]
    assert second.items[0].payload['session_path'] == str(tmp_path / 'claude-session-new.jsonl')


def test_execution_service_claude_adapter_after_rotate_only_new_main_boundary_completes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from provider_execution import claude as claude_adapter_module

    fixed_req_id = '20260318-000000-000-3-rotate-subagent'

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            del pane_id, text

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%2'

    class FakeSession:
        data = {}
        claude_session_path = str(tmp_path / 'claude-session-old.jsonl')
        work_dir = str(tmp_path)

        def ensure_pane(self):
            return True, '%2'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': str(tmp_path / 'claude-session-old.jsonl'), 'offset': 0, 'phase': 0, 'ready': True}

        def try_get_entries(self, state):
            if not state.get('ready', True):
                return [], {**state, 'ready': True}
            phase = int(state.get('phase', 0))
            if phase == 0:
                return [
                    {'role': 'user', 'text': f'CCB_REQ_ID: {fixed_req_id}\n\nprompt', 'entry_type': 'user'},
                    {'role': 'assistant', 'text': 'old partial', 'entry_type': 'assistant', 'uuid': 'assistant-old'},
                    {
                        'role': 'assistant',
                        'text': 'old child work',
                        'entry_type': 'assistant',
                        'uuid': 'assistant-child-old',
                        'subagent_id': 'child-old',
                    },
                    {
                        'role': 'system',
                        'text': '',
                        'entry_type': 'system',
                        'subtype': 'turn_duration',
                        'parent_uuid': 'assistant-child-old',
                    },
                ], {**state, 'offset': 1, 'phase': 1, 'ready': False}
            if phase == 1:
                return [
                    {'role': 'user', 'text': f'CCB_REQ_ID: {fixed_req_id}\n\nprompt again', 'entry_type': 'user'},
                    {
                        'role': 'system',
                        'text': '',
                        'entry_type': 'system',
                        'subtype': 'turn_duration',
                        'parent_uuid': 'assistant-old',
                    },
                    {'role': 'assistant', 'text': 'new partial', 'entry_type': 'assistant', 'uuid': 'assistant-new'},
                    {
                        'role': 'assistant',
                        'text': 'new child work',
                        'entry_type': 'assistant',
                        'uuid': 'assistant-child-new',
                        'subagent_id': 'child-new',
                    },
                    {
                        'role': 'system',
                        'text': '',
                        'entry_type': 'system',
                        'subtype': 'turn_duration',
                        'parent_uuid': 'assistant-child-new',
                    },
                    {
                        'role': 'system',
                        'text': '',
                        'entry_type': 'system',
                        'subtype': 'turn_duration',
                        'parent_uuid': 'assistant-new',
                    },
                ], {
                    **state,
                    'session_path': str(tmp_path / 'claude-session-new.jsonl'),
                    'offset': 2,
                    'phase': 2,
                    'ready': False,
                }
            return [], state

    monkeypatch.setattr(claude_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(claude_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(claude_adapter_module, 'ClaudeLogReader', FakeReader)
    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z')
    service.start(_anchored_job_for_provider('claude', fixed_req_id, body='real claude'), runtime_context=_runtime_context(tmp_path))

    first = service.poll()[0]
    assert [item.kind for item in first.items] == [
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.ASSISTANT_CHUNK,
        CompletionItemKind.ASSISTANT_CHUNK,
    ]
    assert first.items[-1].payload['subagent_id'] == 'child-old'

    second = service.poll()[0]
    assert [item.kind for item in second.items] == [
        CompletionItemKind.SESSION_ROTATE,
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.ASSISTANT_CHUNK,
        CompletionItemKind.ASSISTANT_CHUNK,
        CompletionItemKind.TURN_BOUNDARY,
    ]
    assert second.items[0].payload['session_path'] == str(tmp_path / 'claude-session-new.jsonl')
    assert second.items[2].payload['assistant_uuid'] == 'assistant-new'
    assert second.items[3].payload['subagent_id'] == 'child-new'
    assert second.items[4].payload['assistant_uuid'] == 'assistant-new'
    assert second.items[4].payload['reason'] == 'turn_duration'
    assert second.decision is None


def test_execution_service_claude_adapter_can_resume_after_restart(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from provider_execution import claude as claude_adapter_module

    fixed_req_id = '20260318-000000-000-3-resume'

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            del pane_id, text

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%2'

    class FakeSession:
        data = {}
        claude_session_path = str(tmp_path / 'claude-session.jsonl')
        work_dir = str(tmp_path)

        def ensure_pane(self):
            return True, '%2'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._events = [
                {'role': 'user', 'text': f'CCB_REQ_ID: {fixed_req_id}\n\nprompt', 'entry_type': 'user'},
                {'role': 'assistant', 'text': 'resumed final', 'entry_type': 'assistant', 'uuid': 'assistant-2'},
                {'role': 'system', 'text': '', 'entry_type': 'system', 'subtype': 'turn_duration', 'parent_uuid': 'assistant-2'},
            ]

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': tmp_path / 'claude-session.jsonl', 'offset': 0, 'carry': b''}

        def try_get_entries(self, state):
            index = int(state.get('index', 0))
            if index >= len(self._events):
                return [], state
            return [self._events[index]], {**state, 'index': index + 1}

    monkeypatch.setattr(claude_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(claude_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(claude_adapter_module, 'ClaudeLogReader', FakeReader)

    layout = PathLayout(tmp_path / 'claude-resume')
    state_store = ExecutionStateStore(layout)
    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z', state_store=state_store)
    job = _anchored_job_for_provider('claude', fixed_req_id, body='resume claude')
    service.start(job, runtime_context=_runtime_context(tmp_path))

    persisted = state_store.load(job.job_id)
    assert persisted is not None
    assert persisted.resume_capable is True
    assert persisted.submission.runtime_state['state']['carry'] == b''

    restarted = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:05Z', state_store=state_store)
    restored = restarted.restore(job, runtime_context=_runtime_context(tmp_path))
    assert restored.restored is True

    update = restarted.poll()[0]
    assert [item.kind for item in update.items] == [
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.ASSISTANT_CHUNK,
        CompletionItemKind.TURN_BOUNDARY,
    ]
    assert update.decision is None


def test_execution_service_claude_persists_before_ready_wait_and_resumes_prompt_dispatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from provider_execution import claude as claude_adapter_module

    fixed_req_id = '20260318-000000-000-3-ready'
    sent: list[tuple[str, str]] = []
    pane_reads: list[tuple[str, int]] = []
    pane_text = {'value': 'Starting Claude...'}

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            sent.append((pane_id, text))

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%2'

        def get_pane_content(self, pane_id: str, lines: int = 120) -> str:
            pane_reads.append((pane_id, lines))
            return pane_text['value']

    class FakeSession:
        data = {}
        claude_session_path = str(tmp_path / 'claude-session.jsonl')
        work_dir = str(tmp_path)

        def ensure_pane(self):
            return True, '%2'

    class EmptyReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': tmp_path / 'claude-session.jsonl', 'offset': 0, 'carry': b''}

        def try_get_entries(self, state):
            return [], state

    backend = FakeBackend()
    monkeypatch.setattr(claude_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(claude_adapter_module, 'get_backend_for_session', lambda data: backend)
    monkeypatch.setattr(claude_adapter_module, 'ClaudeLogReader', EmptyReader)

    layout = PathLayout(tmp_path / 'claude-ready-resume')
    state_store = ExecutionStateStore(layout)
    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z', state_store=state_store)
    job = _anchored_job_for_provider('claude', fixed_req_id, body='resume after ready wait')
    service.start(job, runtime_context=_runtime_context(tmp_path))

    persisted = state_store.load(job.job_id)
    assert persisted is not None
    assert persisted.resume_capable is True
    assert persisted.submission.runtime_state['prompt_sent'] is False
    assert sent == []
    assert pane_reads == []

    restarted = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:01Z', state_store=state_store)
    restored = restarted.restore(job, runtime_context=_runtime_context(tmp_path))
    assert restored.restored is True

    assert restarted.poll() == ()
    assert sent == []
    # Two reads on the first active poll: one for the high-confidence
    # provider-signal terminalization check (usage-limit false-positive fix)
    # and one for ready-detection.
    assert pane_reads == [('%2', 120), ('%2', 120)]

    pane_text['value'] = """
───────────────────────────────────────────
❯
───────────────────────────────────────────
  ? for shortcuts
"""
    update = restarted.poll()[0]
    assert [item.kind for item in update.items] == [CompletionItemKind.ANCHOR_SEEN]
    assert len(sent) == 1
    assert sent[0][0] == '%2'
    assert fixed_req_id in sent[0][1]
    assert pane_reads[-1] == ('%2', 120)

    persisted_after_send = state_store.load(job.job_id)
    assert persisted_after_send is not None
    assert persisted_after_send.submission.runtime_state['prompt_sent'] is True


def test_execution_service_codex_adapter_fails_without_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from provider_execution import codex as codex_adapter_module

    monkeypatch.setattr(codex_adapter_module, 'load_project_session', lambda work_dir, instance=None: None)
    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z')
    service.start(_job_for_provider('codex', body='real codex'), runtime_context=_runtime_context(tmp_path))
    update = service.poll()[0]
    assert [item.kind for item in update.items] == [CompletionItemKind.ERROR]
    assert update.items[0].payload['reason'] == 'runtime_unavailable'
    assert 'missing_codex_session' in update.items[0].payload['error']
    assert update.decision is not None
    assert update.decision.status is CompletionStatus.FAILED
    assert update.decision.reason == 'runtime_unavailable'


def test_execution_service_codex_adapter_emits_protocol_items_from_log(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from provider_execution import codex as codex_adapter_module

    fixed_req_id = 'job_1'
    sent: list[tuple[str, str]] = []

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            sent.append((pane_id, text))

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%1'

    class FakeSession:
        data = {}
        codex_session_path = ''
        codex_session_id = ''
        work_dir = str(tmp_path)

        def ensure_pane(self):
            return True, '%1'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._events = [
                {
                    'role': 'user',
                    'text': f'CCB_REQ_ID: {fixed_req_id}\n\nprompt',
                    'entry_type': 'response_item',
                    'payload_type': 'message',
                    'timestamp': '2026-03-18T00:00:00Z',
                },
                {
                    'role': 'assistant',
                    'text': 'partial',
                    'entry_type': 'event_msg',
                    'payload_type': 'agent_message',
                    'timestamp': '2026-03-18T00:00:01Z',
                },
                {
                    'role': 'assistant',
                    'text': 'final without done',
                    'entry_type': 'event_msg',
                    'payload_type': 'agent_message',
                    'phase': 'final_answer',
                    'timestamp': '2026-03-18T00:00:02Z',
                },
                {
                    'role': 'assistant',
                    'text': 'final without done',
                    'entry_type': 'response_item',
                    'payload_type': 'message',
                    'phase': 'final_answer',
                    'timestamp': '2026-03-18T00:00:02Z',
                },
                {
                    'role': 'system',
                    'text': 'partial\nfinal without done',
                    'entry_type': 'event_msg',
                    'payload_type': 'task_complete',
                    'turn_id': 'turn-codex-1',
                    'last_agent_message': 'partial\nfinal without done',
                    'timestamp': '2026-03-18T00:00:03Z',
                },
            ]

        def capture_state(self):
            return {'index': 0}

        def try_get_entries(self, state):
            index = int(state.get('index', 0))
            if index >= len(self._events):
                return [], state
            return [self._events[index]], {'index': index + 1}

    monkeypatch.setattr(codex_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(codex_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(codex_adapter_module, 'CodexLogReader', FakeReader)
    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z')
    service.start(_anchored_job_for_provider('codex', fixed_req_id, body='real codex'), runtime_context=_runtime_context(tmp_path))
    update = service.poll()[0]

    assert sent and sent[0][0] == '%1'
    assert fixed_req_id in sent[0][1]
    assert [item.kind for item in update.items] == [
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.ASSISTANT_CHUNK,
        CompletionItemKind.ASSISTANT_CHUNK,
        CompletionItemKind.TURN_BOUNDARY,
    ]
    assert update.items[2].payload['phase'] == 'final_answer'
    assert update.items[-1].payload['last_agent_message'] == 'partial\nfinal without done'
    assert update.items[-1].payload['turn_id'] == 'turn-codex-1'
    assert update.decision is None


def test_execution_service_codex_adapter_emits_empty_task_complete_boundary(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from provider_execution import codex as codex_adapter_module

    fixed_req_id = '20260318-000000-000-1-empty-codex'

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            del pane_id, text

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%1'

    class FakeSession:
        data = {}
        codex_session_path = ''
        codex_session_id = ''
        work_dir = str(tmp_path)

        def ensure_pane(self):
            return True, '%1'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._events = [
                {
                    'role': 'user',
                    'text': f'CCB_REQ_ID: {fixed_req_id}\n\nprompt',
                    'entry_type': 'response_item',
                    'payload_type': 'message',
                    'timestamp': '2026-03-18T00:00:00Z',
                },
                {
                    'role': 'system',
                    'text': '',
                    'entry_type': 'event_msg',
                    'payload_type': 'task_complete',
                    'turn_id': 'turn-codex-empty',
                    'last_agent_message': '',
                    'timestamp': '2026-03-18T00:00:01Z',
                },
            ]

        def capture_state(self):
            return {'index': 0}

        def try_get_entries(self, state):
            index = int(state.get('index', 0))
            if index >= len(self._events):
                return [], state
            return [self._events[index]], {'index': index + 1}

    monkeypatch.setattr(codex_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(codex_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(codex_adapter_module, 'CodexLogReader', FakeReader)
    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z')
    service.start(_anchored_job_for_provider('codex', fixed_req_id, body='real codex'), runtime_context=_runtime_context(tmp_path))
    update = service.poll()[0]

    assert [item.kind for item in update.items] == [
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.TURN_BOUNDARY,
    ]
    assert update.items[-1].payload['reason'] == 'task_complete'
    assert update.items[-1].payload['last_agent_message'] == ''
    assert update.items[-1].payload['turn_id'] == 'turn-codex-empty'
    assert update.decision is None


def test_execution_service_codex_adapter_respects_no_wrap_provider_option(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from provider_execution import codex as codex_adapter_module

    fixed_req_id = '20260318-000000-000-1-nowrap'
    sent: list[tuple[str, str]] = []

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            sent.append((pane_id, text))

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%1'

    class FakeSession:
        data = {}
        codex_session_path = ''
        codex_session_id = ''
        work_dir = str(tmp_path)

        def ensure_pane(self):
            return True, '%1'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._events = [
                {
                    'role': 'assistant',
                    'text': 'reply body',
                    'entry_type': 'event_msg',
                    'payload_type': 'agent_message',
                    'timestamp': '2026-03-18T00:00:01Z',
                },
                {
                    'role': 'system',
                    'text': 'reply body',
                    'entry_type': 'event_msg',
                    'payload_type': 'task_complete',
                    'turn_id': 'turn-codex-nowrap',
                    'last_agent_message': 'reply body',
                    'timestamp': '2026-03-18T00:00:02Z',
                },
            ]

        def capture_state(self):
            return {'index': 0}

        def try_get_entries(self, state):
            index = int(state.get('index', 0))
            if index >= len(self._events):
                return [], state
            return [self._events[index]], {'index': index + 1}

    monkeypatch.setattr(codex_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(codex_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(codex_adapter_module, 'CodexLogReader', FakeReader)

    job = _anchored_job_for_provider('codex', fixed_req_id, body='raw codex prompt')
    job.provider_options = {'no_wrap': True}
    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z')
    service.start(job, runtime_context=_runtime_context(tmp_path))
    update = service.poll()[0]

    assert sent == [('%1', 'raw codex prompt')]
    assert [item.kind for item in update.items] == [
        CompletionItemKind.ASSISTANT_CHUNK,
        CompletionItemKind.TURN_BOUNDARY,
    ]
    assert update.items[-1].payload['last_agent_message'] == 'reply body'
    assert update.decision is None


@pytest.mark.parametrize(
    ('abort_reason', 'expected_status'),
    [
        ('interrupted', CompletionStatus.CANCELLED),
        ('api_error', CompletionStatus.FAILED),
    ],
)
def test_execution_service_codex_adapter_maps_turn_aborted_terminal_states(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    abort_reason: str,
    expected_status: CompletionStatus,
) -> None:
    from provider_execution import codex as codex_adapter_module

    fixed_req_id = f'20260318-000000-000-1-{abort_reason}'

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            del pane_id, text

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%1'

    class FakeSession:
        data = {}
        codex_session_path = ''
        codex_session_id = ''
        work_dir = str(tmp_path)

        def ensure_pane(self):
            return True, '%1'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._events = [
                {
                    'role': 'user',
                    'text': f'CCB_REQ_ID: {fixed_req_id}\n\nprompt',
                    'entry_type': 'response_item',
                    'payload_type': 'message',
                    'timestamp': '2026-03-18T00:00:00Z',
                },
                {
                    'role': 'assistant',
                    'text': 'partial before abort',
                    'entry_type': 'event_msg',
                    'payload_type': 'agent_message',
                    'timestamp': '2026-03-18T00:00:01Z',
                },
                {
                    'role': 'system',
                    'text': '',
                    'entry_type': 'event_msg',
                    'payload_type': 'turn_aborted',
                    'turn_id': 'turn-codex-abort',
                    'reason': abort_reason,
                    'timestamp': '2026-03-18T00:00:02Z',
                },
            ]

        def capture_state(self):
            return {'index': 0}

        def try_get_entries(self, state):
            index = int(state.get('index', 0))
            if index >= len(self._events):
                return [], state
            return [self._events[index]], {'index': index + 1}

    monkeypatch.setattr(codex_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(codex_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(codex_adapter_module, 'CodexLogReader', FakeReader)
    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z')
    service.start(_anchored_job_for_provider('codex', fixed_req_id, body='real codex'), runtime_context=_runtime_context(tmp_path))
    update = service.poll()[0]

    assert [item.kind for item in update.items] == [
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.ASSISTANT_CHUNK,
        CompletionItemKind.TURN_ABORTED,
    ]
    assert update.items[-1].payload['reason'] == abort_reason
    assert update.items[-1].payload['status'] == expected_status.value
    assert update.decision is None


def test_execution_service_codex_adapter_reports_pane_dead(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from provider_execution import codex as codex_adapter_module

    class DeadBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            del pane_id, text

        def is_alive(self, pane_id: str) -> bool:
            del pane_id
            return False

    class FakeSession:
        data = {}
        codex_session_path = ''
        codex_session_id = ''
        work_dir = str(tmp_path)

        def ensure_pane(self):
            return True, '%1'

    class EmptyReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def capture_state(self):
            return {'index': 0}

        def try_get_entries(self, state):
            return [], state

    monkeypatch.setattr(codex_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(codex_adapter_module, 'get_backend_for_session', lambda data: DeadBackend())
    monkeypatch.setattr(codex_adapter_module, 'CodexLogReader', EmptyReader)

    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z')
    service.start(_job_for_provider('codex'), runtime_context=_runtime_context(tmp_path))
    update = service.poll()[0]
    assert [item.kind for item in update.items] == [CompletionItemKind.PANE_DEAD]


def test_execution_service_codex_adapter_prefers_strict_tmux_target_helpers(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from provider_execution import codex as codex_adapter_module

    fixed_req_id = '20260318-000000-000-1-strict'
    calls: list[tuple[str, str]] = []

    class StrictBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            raise AssertionError(f'legacy send_text should not be used: {pane_id} {text}')

        def is_alive(self, pane_id: str) -> bool:
            raise AssertionError(f'legacy is_alive should not be used: {pane_id}')

        def send_text_to_pane(self, pane_id: str, text: str) -> None:
            calls.append(('send', pane_id))

        def is_tmux_pane_alive(self, pane_id: str) -> bool:
            calls.append(('alive', pane_id))
            return False

    class FakeSession:
        data = {'terminal': 'tmux'}
        codex_session_path = ''
        codex_session_id = ''
        work_dir = str(tmp_path)

        def ensure_pane(self):
            return True, '%11'

    class EmptyReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def capture_state(self):
            return {'index': 0}

        def try_get_entries(self, state):
            return [], state

    monkeypatch.setattr(codex_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(codex_adapter_module, 'get_backend_for_session', lambda data: StrictBackend())
    monkeypatch.setattr(codex_adapter_module, 'CodexLogReader', EmptyReader)

    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z')
    service.start(_anchored_job_for_provider('codex', fixed_req_id, body='strict path'), runtime_context=_runtime_context(tmp_path))
    update = service.poll()[0]

    assert calls == [('send', '%11'), ('alive', '%11')]
    assert [item.kind for item in update.items] == [CompletionItemKind.PANE_DEAD]


def test_execution_service_codex_adapter_can_resume_after_restart(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from provider_execution import codex as codex_adapter_module

    fixed_req_id = '20260318-000000-000-1-resume'

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            del pane_id, text

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%1'

    class FakeSession:
        data = {}
        codex_session_path = str(tmp_path / 'codex-session.jsonl')
        codex_session_id = 'codex-session-id'
        work_dir = str(tmp_path)

        def ensure_pane(self):
            return True, '%1'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._events = [
                {
                    'role': 'user',
                    'text': f'CCB_REQ_ID: {fixed_req_id}\n\nprompt',
                    'entry_type': 'response_item',
                    'payload_type': 'message',
                    'timestamp': '2026-03-18T00:00:00Z',
                },
                {
                    'role': 'assistant',
                    'text': 'resume partial',
                    'entry_type': 'event_msg',
                    'payload_type': 'agent_message',
                    'timestamp': '2026-03-18T00:00:01Z',
                },
                {
                    'role': 'system',
                    'text': 'resume partial',
                    'entry_type': 'event_msg',
                    'payload_type': 'task_complete',
                    'turn_id': 'turn-codex-resume',
                    'last_agent_message': 'resume partial',
                    'timestamp': '2026-03-18T00:00:02Z',
                },
            ]

        def capture_state(self):
            return {'index': 0, 'log_path': tmp_path / 'codex-session.jsonl'}

        def try_get_entries(self, state):
            index = int(state.get('index', 0))
            if index >= len(self._events):
                return [], state
            return [self._events[index]], {**state, 'index': index + 1}

    monkeypatch.setattr(codex_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(codex_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(codex_adapter_module, 'CodexLogReader', FakeReader)

    layout = PathLayout(tmp_path / 'codex-resume')
    state_store = ExecutionStateStore(layout)
    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z', state_store=state_store)
    job = _anchored_job_for_provider('codex', fixed_req_id, body='resume codex')
    service.start(job, runtime_context=_runtime_context(tmp_path))

    persisted = state_store.load(job.job_id)
    assert persisted is not None
    assert persisted.resume_capable is True
    assert str(persisted.submission.runtime_state['state']['log_path']).endswith('codex-session.jsonl')

    restarted = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:05Z', state_store=state_store)
    restored = restarted.restore(job, runtime_context=_runtime_context(tmp_path))
    assert restored.restored is True

    update = restarted.poll()[0]
    assert [item.kind for item in update.items] == [
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.ASSISTANT_CHUNK,
        CompletionItemKind.TURN_BOUNDARY,
    ]
    assert update.decision is None


def test_execution_service_codex_adapter_persists_log_switch_without_immediate_entries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from provider_execution import codex as codex_adapter_module

    fixed_req_id = '20260318-000000-000-1-logswitch'

    class FakeBackend:
        def send_text_to_pane(self, pane_id: str, text: str) -> None:
            assert pane_id == '%22'
            assert fixed_req_id in text

        def is_tmux_pane_alive(self, pane_id: str) -> bool:
            return pane_id == '%22'

    class FakeSession:
        data = {'terminal': 'tmux'}
        codex_session_path = ''
        codex_session_id = ''
        work_dir = str(tmp_path / 'agent2')

        def ensure_pane(self):
            return True, '%22'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._new_log = tmp_path / 'new.jsonl'
            self._old_log = tmp_path / 'old.jsonl'

        def capture_state(self):
            return {'index': 0, 'log_path': self._old_log, 'last_rescan': 0.0}

        def try_get_entries(self, state):
            index = int(state.get('index', 0))
            current_log = state.get('log_path') or self._old_log
            if index == 0:
                return [], {'index': 1, 'log_path': current_log, 'last_rescan': 100.0}
            if index == 1:
                return [], {'index': 2, 'log_path': self._new_log, 'last_rescan': 200.0}
            if index == 2:
                return [
                    {
                        'role': 'user',
                        'text': f'CCB_REQ_ID: {fixed_req_id}\n\n2+3=?',
                        'entry_type': 'response_item',
                        'payload_type': 'message',
                        'timestamp': '2026-03-18T00:00:01Z',
                    }
                ], {'index': 3, 'log_path': current_log, 'last_rescan': 200.0}
            if index == 3:
                return [
                    {
                        'role': 'assistant',
                        'text': '5',
                        'entry_type': 'event_msg',
                        'payload_type': 'agent_message',
                        'timestamp': '2026-03-18T00:00:02Z',
                    }
                ], {'index': 4, 'log_path': current_log, 'last_rescan': 200.0}
            if index == 4:
                return [
                    {
                        'role': 'system',
                        'text': '5',
                        'entry_type': 'event_msg',
                        'payload_type': 'task_complete',
                        'turn_id': 'turn-codex-logswitch',
                        'last_agent_message': '5',
                        'timestamp': '2026-03-18T00:00:03Z',
                    }
                ], {'index': 5, 'log_path': current_log, 'last_rescan': 200.0}
            return [], state

    monkeypatch.setattr(codex_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(codex_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(codex_adapter_module, 'CodexLogReader', FakeReader)

    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z')
    job = _anchored_job_for_provider('codex', fixed_req_id, body='2+3=?')
    job.agent_name = 'agent2'
    job.request = MessageEnvelope(
        project_id=job.request.project_id,
        to_agent='agent2',
        from_actor=job.request.from_actor,
        body='2+3=?',
        task_id=job.request.task_id,
        reply_to=job.request.reply_to,
        message_type=job.request.message_type,
        delivery_scope=job.request.delivery_scope,
    )
    service.start(
        job,
        runtime_context=ProviderRuntimeContext(
            agent_name='agent2',
            workspace_path=str(tmp_path / 'agent2'),
            backend_type='pane-backed',
            runtime_ref='tmux:%22',
            session_ref='session:agent2',
            runtime_pid=456,
            runtime_health='healthy',
        ),
    )

    assert service.poll() == ()
    active = service._active[job.job_id]
    assert active.runtime_state['state']['log_path'] == tmp_path / 'old.jsonl'
    assert active.runtime_state['state']['last_rescan'] == 100.0

    rotate = service.poll()[0]
    assert [item.kind for item in rotate.items] == [CompletionItemKind.SESSION_ROTATE]
    assert rotate.decision is None

    update = service.poll()[0]
    assert [item.kind for item in update.items] == [
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.ASSISTANT_CHUNK,
        CompletionItemKind.TURN_BOUNDARY,
    ]
    assert update.decision is None


def test_execution_service_codex_adapter_follows_rebound_session_binding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from provider_execution import codex as codex_adapter_module

    fixed_req_id = '20260318-000000-000-1-rebound'
    work_dir = tmp_path / 'repo'
    old_log = tmp_path / 'home' / 'sessions' / 'old-session' / 'old-session.jsonl'
    new_log = tmp_path / 'home' / 'sessions' / 'new-session' / 'new-session.jsonl'
    for path in (old_log, new_log):
        path.parent.mkdir(parents=True, exist_ok=True)
    old_log.write_text(
        json.dumps({"type": "session_meta", "payload": {"cwd": str(work_dir)}}) + "\n",
        encoding='utf-8',
    )
    new_log.write_text(
        "\n".join(
            [
                json.dumps({"type": "session_meta", "payload": {"cwd": str(work_dir)}}),
                json.dumps(
                    {
                        "timestamp": "2026-03-18T00:00:01Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "turn_id": f"turn-{fixed_req_id}",
                            "task_id": f"task-{fixed_req_id}",
                            "content": [{"type": "input_text", "text": f"CCB_REQ_ID: {fixed_req_id}\n\nprompt"}],
                        },
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-03-18T00:00:02Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "agent_message",
                            "role": "assistant",
                            "turn_id": f"turn-{fixed_req_id}",
                            "task_id": f"task-{fixed_req_id}",
                            "phase": "final_answer",
                            "message": "rebound reply",
                        },
                    }
                ),
                json.dumps(
                    {
                        "timestamp": "2026-03-18T00:00:03Z",
                        "type": "event_msg",
                        "payload": {
                            "type": "task_complete",
                            "turn_id": f"turn-{fixed_req_id}",
                            "task_id": f"task-{fixed_req_id}",
                            "reason": "task_complete",
                            "last_agent_message": "rebound reply",
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding='utf-8',
    )
    work_dir_str = str(work_dir)

    class FakeBackend:
        def send_text_to_pane(self, pane_id: str, text: str) -> None:
            assert pane_id == '%33'
            assert fixed_req_id in text

        def is_tmux_pane_alive(self, pane_id: str) -> bool:
            return pane_id == '%33'

    class FakeSession:
        data = {
            'terminal': 'tmux',
            'codex_session_root': str(tmp_path / 'home' / 'sessions'),
            'codex_session_path': str(old_log),
            'codex_session_id': 'old-session',
        }
        codex_session_path = str(old_log)
        codex_session_id = 'old-session'
        work_dir = work_dir_str

        def ensure_pane(self):
            return True, '%33'

    session = FakeSession()

    def load_session(work_dir_arg, instance=None):
        del work_dir_arg, instance
        return session

    monkeypatch.setattr(codex_adapter_module, 'load_project_session', load_session)
    monkeypatch.setattr(codex_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())

    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z')
    job = _anchored_job_for_provider('codex', fixed_req_id, body='prompt')
    service.start(job, runtime_context=_runtime_context(work_dir))

    session.codex_session_path = str(new_log)
    session.codex_session_id = 'new-session'
    session.data = {**session.data, 'codex_session_path': str(new_log), 'codex_session_id': 'new-session'}

    update = service.poll()[0]

    assert [item.kind for item in update.items] == [
        CompletionItemKind.SESSION_ROTATE,
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.ASSISTANT_CHUNK,
        CompletionItemKind.TURN_BOUNDARY,
    ]
    assert update.items[-1].payload['last_agent_message'] == 'rebound reply'
    assert update.decision is None


def test_execution_service_gemini_adapter_fails_without_session(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from provider_execution import gemini as gemini_adapter_module

    monkeypatch.setattr(gemini_adapter_module, 'load_project_session', lambda work_dir, instance=None: None)
    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z')
    service.start(_job_for_provider('gemini', body='real gemini'), runtime_context=_runtime_context(tmp_path))
    update = service.poll()[0]
    assert [item.kind for item in update.items] == [CompletionItemKind.ERROR]
    assert update.items[0].payload['reason'] == 'runtime_unavailable'
    assert 'missing_gemini_session' in update.items[0].payload['error']
    assert update.decision is not None
    assert update.decision.status is CompletionStatus.FAILED
    assert update.decision.reason == 'runtime_unavailable'


class _NoResumeAdapter:
    provider = 'noresume'

    def start(self, job: JobRecord, *, context, now: str) -> ProviderSubmission:
        del context
        return ProviderSubmission(
            job_id=job.job_id,
            agent_name=job.agent_name,
            provider=self.provider,
            accepted_at=now,
            ready_at=now,
            source_kind=CompletionSourceKind.TERMINAL_TEXT,
            reply='pending',
            diagnostics={'provider': self.provider},
            runtime_state={'opaque': 'value'},
        )

    def poll(self, submission: ProviderSubmission, *, now: str):
        del submission, now
        return None


class _ReliabilityTimeoutAdapter:
    provider = 'timed'
    completion_reliability_policy = CompletionReliabilityPolicy(
        provider='timed',
        primary_authority='session_snapshot',
        no_terminal_timeout_s=1.0,
    )

    def start(self, job: JobRecord, *, context, now: str) -> ProviderSubmission:
        del context
        return ProviderSubmission(
            job_id=job.job_id,
            agent_name=job.agent_name,
            provider=self.provider,
            accepted_at=now,
            ready_at=now,
            source_kind=CompletionSourceKind.SESSION_SNAPSHOT,
            reply='',
            diagnostics={'provider': self.provider},
            runtime_state={'request_anchor': job.job_id},
        )

    def poll(self, submission: ProviderSubmission, *, now: str):
        del submission, now
        return None

    def export_runtime_state(self, submission: ProviderSubmission) -> dict[str, object]:
        return dict(submission.runtime_state)

    def resume(
        self,
        job: JobRecord,
        submission: ProviderSubmission,
        *,
        context,
        persisted_state,
        now: str,
    ) -> ProviderSubmission:
        del job, context, persisted_state, now
        return submission


def test_execution_service_persists_and_restores_fake_submission_across_restart(tmp_path: Path) -> None:
    layout = PathLayout(tmp_path / 'repo-resume')
    state_store = ExecutionStateStore(layout)
    start_clock = iter([
        '2026-03-18T00:00:00Z',
        '2026-03-18T00:00:00Z',
        '2026-03-18T00:00:00.150000Z',
        '2026-03-18T00:00:00.150000Z',
    ])
    service = ExecutionService(
        build_default_execution_registry(),
        clock=lambda: next(start_clock),
        state_store=state_store,
    )
    job = _job(task_id='fake;latency_ms=300', body='resume flow')
    service.start(job)

    first = service.poll()
    assert len(first) == 1
    assert [item.kind for item in first[0].items] == [
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.ASSISTANT_CHUNK,
    ]
    persisted = state_store.load(job.job_id)
    assert persisted is not None
    assert persisted.resume_capable is True
    assert persisted.submission.runtime_state['next_index'] == 2
    assert persisted.submission.runtime_state['next_seq'] == 3

    restarted = ExecutionService(
        build_default_execution_registry(),
        clock=lambda: '2026-03-18T00:00:00.400000Z',
        state_store=state_store,
    )
    restored = restarted.restore(job)
    assert restored.restored is True

    replayed = restarted.poll()
    assert len(replayed) == 1
    assert [item.kind for item in replayed[0].items] == [
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.ASSISTANT_CHUNK,
    ]
    restarted.acknowledge(job.job_id)

    completed = restarted.poll()
    assert len(completed) == 1
    assert [item.kind for item in completed[0].items] == [CompletionItemKind.RESULT]
    assert completed[0].decision is not None
    assert completed[0].decision.reply == 'FAKE[agent1] resume flow'
    pending = state_store.load(job.job_id)
    assert pending is not None
    assert pending.pending_decision is not None
    assert pending.pending_decision.reply == 'FAKE[agent1] resume flow'

    restarted.finish(job.job_id)
    assert state_store.load(job.job_id) is None


def test_execution_service_replays_pending_items_after_restart_until_ack(tmp_path: Path) -> None:
    layout = PathLayout(tmp_path / 'repo-pending-replay')
    state_store = ExecutionStateStore(layout)
    start_clock = iter([
        '2026-03-18T00:00:00Z',
        '2026-03-18T00:00:00Z',
        '2026-03-18T00:00:00.150000Z',
        '2026-03-18T00:00:00.150000Z',
    ])
    service = ExecutionService(
        build_default_execution_registry(),
        clock=lambda: next(start_clock),
        state_store=state_store,
    )
    job = _job(task_id='fake;latency_ms=300', body='pending replay')
    service.start(job)
    first = service.poll()
    assert len(first) == 1

    persisted = state_store.load(job.job_id)
    assert persisted is not None
    assert [item.kind for item in persisted.pending_items] == [
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.ASSISTANT_CHUNK,
    ]

    restarted = ExecutionService(
        build_default_execution_registry(),
        clock=lambda: '2026-03-18T00:00:00.400000Z',
        state_store=state_store,
    )
    restored = restarted.restore(job)
    assert restored.status == 'replay_pending'

    replayed = restarted.poll()
    assert len(replayed) == 1
    assert [item.kind for item in replayed[0].items] == [
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.ASSISTANT_CHUNK,
    ]
    restarted.acknowledge(job.job_id)
    persisted_after_ack = state_store.load(job.job_id)
    assert persisted_after_ack is not None
    assert persisted_after_ack.pending_items == ()

    completed = restarted.poll()
    assert len(completed) == 1
    assert [item.kind for item in completed[0].items] == [CompletionItemKind.RESULT]


def test_execution_service_defaults_nonterminal_submission_state_until_decision(tmp_path: Path) -> None:
    layout = PathLayout(tmp_path / 'repo-default-submission-status')
    state_store = ExecutionStateStore(layout)
    registry = ProviderExecutionRegistry()
    registry.register(_NoResumeAdapter())
    service = ExecutionService(
        registry,
        clock=lambda: '2026-03-18T00:00:00Z',
        state_store=state_store,
    )

    job = _job_for_provider('noresume', job_id='job_default_state', body='pending')
    service.start(job, runtime_context=_runtime_context(tmp_path))

    persisted = state_store.load(job.job_id)
    assert persisted is not None
    assert persisted.submission.status is CompletionStatus.INCOMPLETE
    assert persisted.submission.reason == 'in_progress'
    assert persisted.submission.confidence is CompletionConfidence.OBSERVED
    assert persisted.pending_decision is None


def test_execution_service_terminalizes_reliability_timeout_after_restore(tmp_path: Path) -> None:
    layout = PathLayout(tmp_path / 'repo-reliability-timeout')
    state_store = ExecutionStateStore(layout)
    registry = ProviderExecutionRegistry([_ReliabilityTimeoutAdapter()])
    start_clock = iter([
        '2026-03-18T00:00:00Z',
        '2026-03-18T00:00:00Z',
    ])
    service = ExecutionService(
        registry,
        clock=lambda: next(start_clock),
        state_store=state_store,
    )

    job = _job_for_provider('timed', job_id='job_timeout', body='stall forever')
    service.start(job, runtime_context=_runtime_context(tmp_path))

    persisted = state_store.load(job.job_id)
    assert persisted is not None
    assert persisted.resume_capable is True
    assert persisted.pending_decision is None

    restarted = ExecutionService(
        registry,
        clock=lambda: '2026-03-18T00:00:02Z',
        state_store=state_store,
    )
    restored = restarted.restore(job, runtime_context=_runtime_context(tmp_path))
    assert restored.restored is True

    updates = restarted.poll()
    assert len(updates) == 1
    update = updates[0]
    assert update.job_id == job.job_id
    assert update.items == ()
    assert update.decision is not None
    assert update.decision.status is CompletionStatus.INCOMPLETE
    assert update.decision.reason == 'completion_timeout'
    assert update.decision.confidence is CompletionConfidence.DEGRADED
    assert update.decision.diagnostics['completion_primary_authority'] == 'session_snapshot'

    persisted_after_timeout = state_store.load(job.job_id)
    assert persisted_after_timeout is not None
    assert persisted_after_timeout.pending_decision is not None
    assert persisted_after_timeout.pending_decision.reason == 'completion_timeout'
    assert persisted_after_timeout.submission.diagnostics['completion_fallback_source'] == 'execution_reliability_monitor'


def test_execution_service_acknowledge_item_tracks_exact_apply_markers(tmp_path: Path) -> None:
    layout = PathLayout(tmp_path / 'repo-pending-marker')
    state_store = ExecutionStateStore(layout)
    clock = iter([
        '2026-03-18T00:00:00Z',
        '2026-03-18T00:00:00.150000Z',
    ])
    service = ExecutionService(
        build_default_execution_registry(),
        clock=lambda: next(clock, '2026-03-18T00:00:00.250000Z'),
        state_store=state_store,
    )
    job = _job(task_id='fake;latency_ms=300', body='trim prefix')
    service.start(job)
    update = service.poll()[0]
    assert [item.cursor.event_seq for item in update.items] == [1, 2]

    service.acknowledge_item(job.job_id, event_seq=2)
    persisted = state_store.load(job.job_id)
    assert persisted is not None
    assert [item.cursor.event_seq for item in persisted.pending_items] == [1, 2]
    assert persisted.applied_event_seqs == (2,)

    restarted = ExecutionService(
        build_default_execution_registry(),
        clock=lambda: '2026-03-18T00:00:00.250000Z',
        state_store=state_store,
    )
    restored = restarted.restore(job)
    assert restored.status == 'replay_pending'
    assert restored.pending_items_count == 1

    replayed = restarted.poll()
    assert len(replayed) == 1
    assert [item.cursor.event_seq for item in replayed[0].items] == [1]

    restarted.acknowledge(job.job_id)
    persisted = state_store.load(job.job_id)
    assert persisted is not None
    assert persisted.pending_items == ()
    assert persisted.applied_event_seqs == ()


def test_execution_service_restore_uses_apply_markers_to_recover_terminal_pending(tmp_path: Path) -> None:
    layout = PathLayout(tmp_path / 'repo-terminal-marker')
    state_store = ExecutionStateStore(layout)
    service = ExecutionService(
        build_default_execution_registry(),
        clock=lambda: '2026-03-18T00:00:00Z',
        state_store=state_store,
    )
    job = _job(task_id='fake;latency_ms=0', body='terminal marker')
    service.start(job)

    updates = service.poll()
    assert len(updates) == 1
    update = updates[0]
    assert update.decision is not None
    assert [item.cursor.event_seq for item in update.items] == [1, 2, 3]

    for event_seq in (1, 2, 3):
        service.acknowledge_item(job.job_id, event_seq=event_seq)

    persisted = state_store.load(job.job_id)
    assert persisted is not None
    assert persisted.pending_decision is not None
    assert persisted.applied_event_seqs == (1, 2, 3)

    restarted = ExecutionService(
        build_default_execution_registry(),
        clock=lambda: '2026-03-18T00:00:01Z',
        state_store=state_store,
    )
    restored = restarted.restore(job)
    assert restored.status == 'terminal_pending'
    assert restored.decision is not None
    assert restored.decision.reply == 'FAKE[agent1] terminal marker'


def test_execution_service_restore_recovers_terminal_pending_decision(tmp_path: Path) -> None:
    layout = PathLayout(tmp_path / 'repo-terminal-pending')
    state_store = ExecutionStateStore(layout)
    service = ExecutionService(
        build_default_execution_registry(),
        clock=lambda: '2026-03-18T00:00:00Z',
        state_store=state_store,
    )
    job = _job(task_id='fake;latency_ms=0', body='terminal pending')
    service.start(job)

    updates = service.poll()
    assert len(updates) == 1
    assert updates[0].decision is not None

    restarted = ExecutionService(
        build_default_execution_registry(),
        clock=lambda: '2026-03-18T00:00:01Z',
        state_store=state_store,
    )
    restored = restarted.restore(job)
    assert restored.status == 'replay_pending'
    replayed = restarted.poll()
    assert len(replayed) == 1
    assert replayed[0].decision is not None
    assert replayed[0].decision.reply == 'FAKE[agent1] terminal pending'


def test_execution_service_restore_abandons_non_resumable_submission(tmp_path: Path) -> None:
    layout = PathLayout(tmp_path / 'repo-noresume')
    state_store = ExecutionStateStore(layout)
    registry = ProviderExecutionRegistry([_NoResumeAdapter()])
    start_clock = iter(['2026-03-18T00:00:00Z', '2026-03-18T00:00:00Z'])
    service = ExecutionService(registry, clock=lambda: next(start_clock), state_store=state_store)
    job = _job_for_provider('noresume', body='cannot resume')
    service.start(job)

    persisted = state_store.load(job.job_id)
    assert persisted is not None
    assert persisted.resume_capable is False

    restarted = ExecutionService(registry, clock=lambda: '2026-03-18T00:00:10Z', state_store=state_store)
    restored = restarted.restore(job)
    assert restored.restored is False
    assert restored.status == 'abandoned'
    assert restored.reason == 'provider_resume_unsupported'
    assert state_store.load(job.job_id) is None


def test_execution_service_gemini_adapter_emits_session_snapshot_items(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from provider_execution import gemini as gemini_adapter_module

    fixed_req_id = 'job_1'
    sent: list[tuple[str, str]] = []

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            sent.append((pane_id, text))

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%3'

    class FakeSession:
        data = {}
        gemini_session_path = str(tmp_path / 'gemini-session.json')
        gemini_session_id = 'gemini-session-id'
        work_dir = str(tmp_path)

        def ensure_pane(self):
            return True, '%3'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._calls = 0

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': str(tmp_path / 'gemini-session.json'), 'msg_count': 0}

        def try_get_message(self, state):
            return (
                'stable reply',
                {
                    **state,
                    'msg_count': 2,
                    'last_gemini_id': 'msg-2',
                    'mtime_ns': 123456789,
                },
            )

    monkeypatch.setattr(gemini_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(gemini_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(gemini_adapter_module, 'GeminiLogReader', FakeReader)

    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z')
    service.start(_anchored_job_for_provider('gemini', fixed_req_id, body='real gemini'), runtime_context=_runtime_context(tmp_path))
    update = service.poll()[0]

    assert sent and sent[0][0] == '%3'
    assert fixed_req_id in sent[0][1]
    assert [item.kind for item in update.items] == [
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.SESSION_SNAPSHOT,
    ]
    assert update.items[-1].payload['reply'] == 'stable reply'
    assert update.decision is None


def test_execution_service_gemini_adapter_respects_no_wrap_provider_option(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from provider_execution import gemini as gemini_adapter_module

    fixed_req_id = '20260318-000000-000-5-nowrap'
    sent: list[tuple[str, str]] = []

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            sent.append((pane_id, text))

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%3'

    class FakeSession:
        data = {}
        gemini_session_path = str(tmp_path / 'gemini-session.json')
        gemini_session_id = 'gemini-session-id'
        work_dir = str(tmp_path)

        def ensure_pane(self):
            return True, '%3'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': str(tmp_path / 'gemini-session.json'), 'msg_count': 0}

        def try_get_message(self, state):
            return (
                'stable reply',
                {
                    **state,
                    'msg_count': 2,
                    'last_gemini_id': 'msg-2',
                    'mtime_ns': 123456789,
                },
            )

    monkeypatch.setattr(gemini_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(gemini_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(gemini_adapter_module, 'GeminiLogReader', FakeReader)

    job = _anchored_job_for_provider('gemini', fixed_req_id, body='raw gemini prompt')
    job.provider_options = {'no_wrap': True}
    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z')
    service.start(job, runtime_context=_runtime_context(tmp_path))
    update = service.poll()[0]

    assert sent == [('%3', 'raw gemini prompt')]
    assert [item.kind for item in update.items] == [CompletionItemKind.SESSION_SNAPSHOT]
    assert update.items[0].payload['reply'] == 'stable reply'
    assert update.decision is None


def test_execution_service_gemini_adapter_reanchors_after_session_rotate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from provider_execution import gemini as gemini_adapter_module

    fixed_req_id = '20260318-000000-000-5-rotate'

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            del pane_id, text

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%3'

    class FakeSession:
        data = {}
        gemini_session_path = str(tmp_path / 'gemini-session-old.json')
        gemini_session_id = 'gemini-session-id'
        work_dir = str(tmp_path)

        def ensure_pane(self):
            return True, '%3'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._calls = 0

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': str(tmp_path / 'gemini-session-old.json'), 'msg_count': 0}

        def try_get_message(self, state):
            self._calls += 1
            if self._calls == 1:
                return None, state
            return (
                'rotated reply',
                {
                    **state,
                    'session_path': str(tmp_path / 'gemini-session-new.json'),
                    'msg_count': 3,
                    'last_gemini_id': 'msg-3',
                    'mtime_ns': 222222222,
                },
            )

    monkeypatch.setattr(gemini_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(gemini_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(gemini_adapter_module, 'GeminiLogReader', FakeReader)

    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z')
    service.start(_anchored_job_for_provider('gemini', fixed_req_id, body='real gemini'), runtime_context=_runtime_context(tmp_path))

    first = service.poll()[0]
    assert [item.kind for item in first.items] == [CompletionItemKind.ANCHOR_SEEN]
    assert first.items[0].payload['session_path'] == str(tmp_path / 'gemini-session-old.json')

    second = service.poll()[0]
    assert [item.kind for item in second.items] == [
        CompletionItemKind.SESSION_ROTATE,
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.SESSION_SNAPSHOT,
    ]
    assert second.items[0].payload['session_path'] == str(tmp_path / 'gemini-session-new.json')
    assert second.items[1].payload['session_path'] == str(tmp_path / 'gemini-session-new.json')
    assert second.items[2].payload['reply'] == 'rotated reply'
    assert second.decision is None


def test_execution_service_gemini_adapter_reports_pane_dead(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from provider_execution import gemini as gemini_adapter_module

    class DeadBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            del pane_id, text

        def is_alive(self, pane_id: str) -> bool:
            del pane_id
            return False

    class FakeSession:
        data = {}
        gemini_session_path = str(tmp_path / 'gemini-session.json')
        gemini_session_id = 'gemini-session-id'
        work_dir = str(tmp_path)

        def ensure_pane(self):
            return True, '%3'

    class EmptyReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': str(tmp_path / 'gemini-session.json'), 'msg_count': 0}

        def try_get_message(self, state):
            return None, state

    monkeypatch.setattr(gemini_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(gemini_adapter_module, 'get_backend_for_session', lambda data: DeadBackend())
    monkeypatch.setattr(gemini_adapter_module, 'GeminiLogReader', EmptyReader)

    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z')
    service.start(_job_for_provider('gemini'), runtime_context=_runtime_context(tmp_path))
    update = service.poll()[0]
    assert [item.kind for item in update.items] == [CompletionItemKind.PANE_DEAD]


def test_execution_service_gemini_adapter_prefers_exact_hook_artifact(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from provider_execution import gemini as gemini_adapter_module
    from provider_hooks.artifacts import write_event

    fixed_req_id = '20260318-000000-000-5-hook'
    completion_dir = tmp_path / 'completion'
    sent: list[tuple[str, str]] = []

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            sent.append((pane_id, text))

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%3'

    class FakeSession:
        data = {
            'completion_artifact_dir': str(completion_dir),
        }
        gemini_session_path = str(tmp_path / 'gemini-session.json')
        gemini_session_id = 'gemini-session-id'
        work_dir = str(tmp_path)

        def ensure_pane(self):
            return True, '%3'

    class EmptyReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': str(tmp_path / 'gemini-session.json'), 'msg_count': 0}

        def try_get_message(self, state):
            return None, state

    write_event(
        provider='gemini',
        completion_dir=completion_dir,
        agent_name='agent1',
        workspace_path=str(tmp_path),
        req_id=fixed_req_id,
        status='completed',
        reply='gemini exact reply',
        session_id='gemini-session-id',
        hook_event_name='AfterAgent',
    )

    monkeypatch.setattr(gemini_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(gemini_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(gemini_adapter_module, 'GeminiLogReader', EmptyReader)

    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z')
    service.start(_anchored_job_for_provider('gemini', fixed_req_id, body='real gemini'), runtime_context=_runtime_context(tmp_path))
    update = service.poll()[0]

    assert [item.kind for item in update.items] == [CompletionItemKind.ASSISTANT_FINAL]
    assert update.items[0].payload['reply'] == 'gemini exact reply'
    assert update.decision is not None
    assert update.decision.status is CompletionStatus.COMPLETED
    assert update.decision.confidence is CompletionConfidence.EXACT
    assert update.decision.reason == 'hook_after_agent'
    assert sent and sent[0][0] == '%3'
    assert fixed_req_id in sent[0][1]
    assert 'CCB_DONE:' not in sent[0][1]


def test_execution_service_gemini_adapter_maps_exact_hook_failures_to_api_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from provider_execution import gemini as gemini_adapter_module
    from provider_hooks.artifacts import write_event

    fixed_req_id = '20260318-000000-000-5-hook-failed'
    completion_dir = tmp_path / 'completion'
    failure_text = (
        'Code Assist login required.\n'
        'Attempting to open authentication page in your browser.'
    )

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            del pane_id, text

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%3'

    class FakeSession:
        data = {
            'completion_artifact_dir': str(completion_dir),
        }
        gemini_session_path = str(tmp_path / 'gemini-session.json')
        gemini_session_id = 'gemini-session-id'
        work_dir = str(tmp_path)

        def ensure_pane(self):
            return True, '%3'

    class EmptyReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': str(tmp_path / 'gemini-session.json'), 'msg_count': 0}

        def try_get_message(self, state):
            return None, state

    write_event(
        provider='gemini',
        completion_dir=completion_dir,
        agent_name='agent1',
        workspace_path=str(tmp_path),
        req_id=fixed_req_id,
        status='failed',
        reply=failure_text,
        session_id='gemini-session-id',
        hook_event_name='AfterAgent',
        diagnostics={
            'error_type': 'provider_api_error',
            'error_code': 'LoginRequired',
            'error_message': failure_text,
            'reason': 'api_error',
        },
    )

    monkeypatch.setattr(gemini_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(gemini_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(gemini_adapter_module, 'GeminiLogReader', EmptyReader)

    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z')
    service.start(_anchored_job_for_provider('gemini', fixed_req_id, body='real gemini'), runtime_context=_runtime_context(tmp_path))
    update = service.poll()[0]

    assert [item.kind for item in update.items] == [CompletionItemKind.ASSISTANT_FINAL]
    assert update.items[0].payload['status'] == 'failed'
    assert update.items[0].payload['error_code'] == 'LoginRequired'
    assert update.items[0].payload['error_message'] == failure_text
    assert update.decision is not None
    assert update.decision.status is CompletionStatus.FAILED
    assert update.decision.reason == 'api_error'
    assert update.decision.reply == failure_text
    assert update.decision.diagnostics['completion_source'] == 'hook_artifact'
    assert update.decision.diagnostics['hook_event_name'] == 'AfterAgent'
    assert update.decision.diagnostics['error_type'] == 'provider_api_error'
    assert update.decision.diagnostics['error_code'] == 'LoginRequired'
    assert update.decision.diagnostics['error_message'] == failure_text


def test_execution_service_gemini_adapter_marks_empty_completed_hook_artifact_incomplete(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from provider_execution import gemini as gemini_adapter_module
    from provider_hooks.artifacts import write_event

    fixed_req_id = '20260318-000000-000-5-empty-completed'
    completion_dir = tmp_path / 'completion'

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            del pane_id, text

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%3'

    class FakeSession:
        data = {
            'completion_artifact_dir': str(completion_dir),
        }
        gemini_session_path = str(tmp_path / 'gemini-session.json')
        gemini_session_id = 'gemini-session-id'
        work_dir = str(tmp_path)

        def ensure_pane(self):
            return True, '%3'

    class EmptyReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': str(tmp_path / 'gemini-session.json'), 'msg_count': 0}

        def try_get_message(self, state):
            return None, state

    write_event(
        provider='gemini',
        completion_dir=completion_dir,
        agent_name='agent1',
        workspace_path=str(tmp_path),
        req_id=fixed_req_id,
        status='completed',
        reply='',
        session_id='gemini-session-id',
        hook_event_name='AfterAgent',
    )

    monkeypatch.setattr(gemini_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(gemini_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(gemini_adapter_module, 'GeminiLogReader', EmptyReader)

    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z')
    service.start(_anchored_job_for_provider('gemini', fixed_req_id, body='real gemini'), runtime_context=_runtime_context(tmp_path))
    update = service.poll()[0]

    assert update.decision is not None
    assert update.decision.status is CompletionStatus.INCOMPLETE
    assert update.decision.reason == 'hook_after_agent_incomplete'
    assert update.decision.diagnostics['empty_reply'] is True
    assert update.decision.diagnostics['error_type'] == 'empty_provider_reply'
    assert update.items[0].kind is CompletionItemKind.ASSISTANT_FINAL
    assert update.items[0].payload['status'] == 'incomplete'
    assert 'without assistant reply text' in update.items[0].payload['text']


def test_execution_service_gemini_exact_hook_submission_uses_strict_tmux_sender(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from provider_execution import gemini as gemini_adapter_module
    from provider_hooks.artifacts import write_event

    fixed_req_id = '20260318-000000-000-5-strict'
    completion_dir = tmp_path / 'completion'
    strict_sent: list[tuple[str, str]] = []

    class FakeBackend:
        def send_text_to_pane(self, pane_id: str, text: str) -> None:
            strict_sent.append((pane_id, text))

        def send_text(self, pane_id: str, text: str) -> None:
            raise AssertionError(f'legacy sender should not be used: {pane_id} {text}')

    class FakeSession:
        data = {
            'completion_artifact_dir': str(completion_dir),
        }
        gemini_session_path = str(tmp_path / 'gemini-session.json')
        gemini_session_id = 'gemini-session-id'
        work_dir = str(tmp_path)

        def ensure_pane(self):
            return True, '%3'

    class EmptyReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': str(tmp_path / 'gemini-session.json'), 'msg_count': 0}

        def try_get_message(self, state):
            return None, state

    write_event(
        provider='gemini',
        completion_dir=completion_dir,
        agent_name='agent1',
        workspace_path=str(tmp_path),
        req_id=fixed_req_id,
        status='completed',
        reply='gemini exact reply',
        session_id='gemini-session-id',
        hook_event_name='AfterAgent',
    )

    monkeypatch.setattr(gemini_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(gemini_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(gemini_adapter_module, 'GeminiLogReader', EmptyReader)

    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z')
    service.start(_anchored_job_for_provider('gemini', fixed_req_id, body='real gemini'), runtime_context=_runtime_context(tmp_path))
    update = service.poll()[0]

    assert update.decision is not None
    assert len(strict_sent) == 1
    assert strict_sent[0][0] == '%3'
    assert fixed_req_id in strict_sent[0][1]


def test_execution_service_gemini_adapter_can_resume_after_restart(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from provider_execution import gemini as gemini_adapter_module

    fixed_req_id = '20260318-000000-000-2-resume'

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            del pane_id, text

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%7'

    class FakeSession:
        data = {}
        gemini_session_path = str(tmp_path / 'gemini-session.json')
        work_dir = str(tmp_path)

        def ensure_pane(self):
            return True, '%7'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': tmp_path / 'gemini-session.json', 'msg_count': 0, 'mtime': 0.0, 'mtime_ns': 0, 'size': 0}

        def try_get_message(self, state):
            if state.get('done'):
                return None, state
            return f'resumed gemini\nCCB_DONE: {fixed_req_id}', {**state, 'done': True, 'msg_count': 1, 'last_gemini_id': 'gemini-1'}

    monkeypatch.setattr(gemini_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(gemini_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(gemini_adapter_module, 'GeminiLogReader', FakeReader)

    layout = PathLayout(tmp_path / 'gemini-resume')
    state_store = ExecutionStateStore(layout)
    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z', state_store=state_store)
    job = _anchored_job_for_provider('gemini', fixed_req_id, body='resume gemini')
    service.start(job, runtime_context=_runtime_context(tmp_path))

    persisted = state_store.load(job.job_id)
    assert persisted is not None
    assert persisted.resume_capable is True
    assert str(persisted.submission.runtime_state['state']['session_path']).endswith('gemini-session.json')

    restarted = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:05Z', state_store=state_store)
    restored = restarted.restore(job, runtime_context=_runtime_context(tmp_path))
    assert restored.restored is True

    update = restarted.poll()[0]
    assert [item.kind for item in update.items] == [
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.SESSION_SNAPSHOT,
    ]
    assert update.decision is None


def test_execution_service_gemini_adapter_defers_prompt_until_ready_and_persists_wait_state(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    from provider_execution import gemini as gemini_adapter_module

    fixed_req_id = '20260318-000000-000-2-gemini-ready'
    sent: list[tuple[str, str]] = []
    pane_reads: list[tuple[str, int]] = []
    pane_text = {'value': 'Gemini is starting'}

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            sent.append((pane_id, text))

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%2'

        def get_pane_content(self, pane_id: str, *, lines: int = 120) -> str:
            pane_reads.append((pane_id, lines))
            return pane_text['value']

    class FakeSession:
        data = {}
        gemini_session_path = str(tmp_path / 'gemini-session.json')
        work_dir = str(tmp_path)

        def ensure_pane(self):
            return True, '%2'

    class EmptyReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': tmp_path / 'gemini-session.json', 'msg_count': 0}

        def try_get_message(self, state):
            return None, state

    backend = FakeBackend()
    monkeypatch.setattr(gemini_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(gemini_adapter_module, 'get_backend_for_session', lambda data: backend)
    monkeypatch.setattr(gemini_adapter_module, 'GeminiLogReader', EmptyReader)

    layout = PathLayout(tmp_path / 'gemini-ready-resume')
    state_store = ExecutionStateStore(layout)
    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z', state_store=state_store)
    job = _anchored_job_for_provider('gemini', fixed_req_id, body='resume after ready wait')
    service.start(job, runtime_context=_runtime_context(tmp_path))

    persisted = state_store.load(job.job_id)
    assert persisted is not None
    assert persisted.resume_capable is True
    assert persisted.submission.runtime_state['prompt_sent'] is False
    assert sent == []
    assert pane_reads == []

    restarted_clock = iter(
        [
            '2026-03-18T00:00:01Z',
            '2026-03-18T00:00:01Z',
            '2026-03-18T00:00:01Z',
            '2026-03-18T00:00:02Z',
            '2026-03-18T00:00:02Z',
            '2026-03-18T00:00:04Z',
            '2026-03-18T00:00:04Z',
        ]
    )
    restarted = ExecutionService(
        build_default_execution_registry(),
        clock=lambda: next(restarted_clock),
        state_store=state_store,
    )
    restored = restarted.restore(job, runtime_context=_runtime_context(tmp_path))
    assert restored.restored is True

    assert restarted.poll() == ()
    assert sent == []
    assert pane_reads == [('%2', 120)]

    pane_text['value'] = 'Type your message'
    assert restarted.poll() == ()
    assert sent == []
    persisted_ready = state_store.load(job.job_id)
    assert persisted_ready is not None
    assert persisted_ready.submission.runtime_state['prompt_sent'] is False
    assert persisted_ready.submission.runtime_state['ready_prompt_fingerprint'] == 'Type your message'
    assert persisted_ready.submission.runtime_state['ready_prompt_seen_at'] == '2026-03-18T00:00:02Z'

    update = restarted.poll()[0]
    assert [item.kind for item in update.items] == [CompletionItemKind.ANCHOR_SEEN]
    assert len(sent) == 1
    assert sent[0][0] == '%2'
    assert fixed_req_id in sent[0][1]

    persisted_after_send = state_store.load(job.job_id)
    assert persisted_after_send is not None
    assert persisted_after_send.submission.runtime_state['prompt_sent'] is True
    assert persisted_after_send.submission.runtime_state['prompt_sent_at'] == '2026-03-18T00:00:04Z'


def test_execution_service_opencode_adapter_emits_boundary_for_completed_reply(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from provider_execution import opencode as opencode_adapter_module

    fixed_req_id = '20260318-000000-000-6-1'
    sent: list[tuple[str, str]] = []

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            sent.append((pane_id, text))

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%4'

    class FakeSession:
        data = {}
        opencode_session_id_filter = 'ses-demo'
        opencode_project_id = 'proj-demo'
        work_dir = str(tmp_path)

        def ensure_pane(self):
            return True, '%4'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def capture_state(self):
            return {'session_path': str(tmp_path / 'opencode-session.json'), 'session_id': 'ses-demo'}

        def try_get_message(self, state):
            return (
                'final answer',
                {
                    **state,
                    'last_assistant_id': 'msg-final',
                    'last_assistant_parent_id': 'msg-user',
                    'last_assistant_req_id': fixed_req_id,
                    'last_assistant_completed': 1234,
                },
            )

    monkeypatch.setattr(opencode_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(opencode_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(opencode_adapter_module, 'OpenCodeLogReader', FakeReader)

    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z')
    service.start(_anchored_job_for_provider('opencode', fixed_req_id, body='real opencode'), runtime_context=_runtime_context(tmp_path))
    update = service.poll()[0]

    assert sent and sent[0][0] == '%4'
    assert fixed_req_id in sent[0][1]
    assert [item.kind for item in update.items] == [
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.ASSISTANT_FINAL,
        CompletionItemKind.TURN_BOUNDARY,
    ]
    assert update.items[1].payload['reply'] == 'final answer'
    assert update.items[2].payload['reason'] == 'assistant_completed'


def test_execution_service_opencode_adapter_respects_no_wrap_provider_option(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from provider_execution import opencode as opencode_adapter_module

    fixed_req_id = '20260318-000000-000-6-nowrap'
    sent: list[tuple[str, str]] = []

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            sent.append((pane_id, text))

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%4'

    class FakeSession:
        data = {}
        opencode_session_id_filter = 'ses-demo'
        opencode_project_id = 'proj-demo'
        work_dir = str(tmp_path)

        def ensure_pane(self):
            return True, '%4'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def capture_state(self):
            return {'session_path': str(tmp_path / 'opencode-session.json'), 'session_id': 'ses-demo'}

        def try_get_message(self, state):
            return (
                'final answer',
                {
                    **state,
                    'last_assistant_id': 'msg-final',
                    'last_assistant_parent_id': 'msg-user',
                    'last_assistant_completed': None,
                },
            )

    monkeypatch.setattr(opencode_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(opencode_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(opencode_adapter_module, 'OpenCodeLogReader', FakeReader)

    job = _anchored_job_for_provider('opencode', fixed_req_id, body='raw opencode prompt')
    job.provider_options = {'no_wrap': True}
    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z')
    service.start(job, runtime_context=_runtime_context(tmp_path))
    update = service.poll()[0]

    assert sent == [('%4', 'raw opencode prompt')]
    assert [item.kind for item in update.items] == [CompletionItemKind.ASSISTANT_FINAL]
    assert update.items[0].payload['reply'] == 'final answer'


def test_execution_service_opencode_adapter_reanchors_after_session_rotate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from provider_execution import opencode as opencode_adapter_module

    fixed_req_id = '20260318-000000-000-6-rotate'

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            del pane_id, text

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%4'

    class FakeSession:
        data = {}
        opencode_session_id_filter = 'ses-demo'
        opencode_project_id = 'proj-demo'
        work_dir = str(tmp_path)

        def ensure_pane(self):
            return True, '%4'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._calls = 0

        def capture_state(self):
            return {'session_path': str(tmp_path / 'opencode-old.json'), 'session_id': 'ses-old'}

        def try_get_message(self, state):
            self._calls += 1
            if self._calls == 1:
                return None, state
            return (
                'new final',
                {
                    **state,
                    'session_path': str(tmp_path / 'opencode-new.json'),
                    'session_id': 'ses-new',
                    'last_assistant_id': 'msg-new',
                    'last_assistant_parent_id': 'msg-user-new',
                    'last_assistant_req_id': fixed_req_id,
                    'last_assistant_completed': 2222,
                },
            )

    monkeypatch.setattr(opencode_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(opencode_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(opencode_adapter_module, 'OpenCodeLogReader', FakeReader)

    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z')
    service.start(_anchored_job_for_provider('opencode', fixed_req_id, body='real opencode'), runtime_context=_runtime_context(tmp_path))

    first = service.poll()[0]
    assert [item.kind for item in first.items] == [CompletionItemKind.ANCHOR_SEEN]

    second = service.poll()[0]
    assert [item.kind for item in second.items] == [
        CompletionItemKind.SESSION_ROTATE,
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.ASSISTANT_FINAL,
        CompletionItemKind.TURN_BOUNDARY,
    ]
    assert second.items[0].payload['provider_session_id'] == 'ses-new'


def test_execution_service_droid_adapter_emits_legacy_items_from_events(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from provider_execution import droid as droid_adapter_module

    fixed_req_id = '20260318-000000-000-7-1'
    sent: list[tuple[str, str]] = []
    reader_inits: list[dict[str, object]] = []

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            sent.append((pane_id, text))

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%5'

    class FakeSession:
        data = {'droid_sessions_root': str(tmp_path / 'factory-home' / 'sessions')}
        droid_session_path = str(tmp_path / 'droid-session.jsonl')
        droid_session_id = 'droid-session-id'
        work_dir = str(tmp_path)

        def ensure_pane(self):
            return True, '%5'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args
            reader_inits.append(dict(kwargs))
            self._events = [
                ('user', f'CCB_REQ_ID: {fixed_req_id}\n\nprompt'),
                ('assistant', 'partial'),
                ('assistant', f'final\nCCB_DONE: {fixed_req_id}'),
            ]

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def set_session_id_hint(self, session_id) -> None:
            del session_id

        def capture_state(self):
            return {'session_path': str(tmp_path / 'droid-session.jsonl'), 'offset': 0}

        def try_get_events(self, state):
            index = int(state.get('index', 0))
            if index >= len(self._events):
                return [], state
            return [self._events[index]], {**state, 'index': index + 1}

    monkeypatch.setattr(droid_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(droid_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(droid_adapter_module, 'DroidLogReader', FakeReader)

    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z')
    service.start(_anchored_job_for_provider('droid', fixed_req_id, body='real droid'), runtime_context=_runtime_context(tmp_path))
    update = service.poll()[0]

    assert reader_inits[0]['root'] == tmp_path / 'factory-home' / 'sessions'
    assert sent and sent[0][0] == '%5'
    assert fixed_req_id in sent[0][1]
    assert [item.kind for item in update.items] == [
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.ASSISTANT_CHUNK,
        CompletionItemKind.ASSISTANT_FINAL,
    ]
    assert update.items[-1].payload['reply'] == 'partial\nfinal'
    assert update.items[-1].payload['done_marker'] is True


def test_execution_service_droid_adapter_respects_no_wrap_provider_option(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from provider_execution import droid as droid_adapter_module

    fixed_req_id = '20260318-000000-000-7-nowrap'
    sent: list[tuple[str, str]] = []

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            sent.append((pane_id, text))

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%5'

    class FakeSession:
        data = {}
        droid_session_path = str(tmp_path / 'droid-session.jsonl')
        droid_session_id = 'droid-session-id'
        work_dir = str(tmp_path)

        def ensure_pane(self):
            return True, '%5'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._events = [
                ('assistant', f'reply body\nCCB_DONE: {fixed_req_id}'),
            ]

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def set_session_id_hint(self, session_id) -> None:
            del session_id

        def capture_state(self):
            return {'session_path': str(tmp_path / 'droid-session.jsonl'), 'offset': 0}

        def try_get_events(self, state):
            index = int(state.get('index', 0))
            if index >= len(self._events):
                return [], state
            return [self._events[index]], {**state, 'index': index + 1}

    monkeypatch.setattr(droid_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(droid_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(droid_adapter_module, 'DroidLogReader', FakeReader)

    job = _anchored_job_for_provider('droid', fixed_req_id, body='raw droid prompt')
    job.provider_options = {'no_wrap': True}
    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z')
    service.start(job, runtime_context=_runtime_context(tmp_path))
    update = service.poll()[0]

    assert sent == [('%5', 'raw droid prompt')]
    assert [item.kind for item in update.items] == [CompletionItemKind.ASSISTANT_FINAL]
    assert update.items[0].payload['reply'] == 'reply body'


def test_execution_service_droid_adapter_reanchors_after_session_rotate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from provider_execution import droid as droid_adapter_module

    fixed_req_id = '20260318-000000-000-7-rotate'

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            del pane_id, text

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%5'

    class FakeSession:
        data = {}
        droid_session_path = str(tmp_path / 'droid-old.jsonl')
        droid_session_id = 'droid-session-id'
        work_dir = str(tmp_path)

        def ensure_pane(self):
            return True, '%5'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._calls = 0

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def set_session_id_hint(self, session_id) -> None:
            del session_id

        def capture_state(self):
            return {'session_path': str(tmp_path / 'droid-old.jsonl'), 'offset': 0, 'phase': 0, 'ready': True}

        def try_get_events(self, state):
            if not state.get('ready', True):
                return [], {**state, 'ready': True}
            phase = int(state.get('phase', 0))
            if phase == 0:
                return [
                    ('user', f'CCB_REQ_ID: {fixed_req_id}\n\nprompt'),
                    ('assistant', 'old partial'),
                ], {**state, 'offset': 1, 'phase': 1, 'ready': False}
            if phase == 1:
                return [
                    ('user', f'CCB_REQ_ID: {fixed_req_id}\n\nprompt new'),
                    ('assistant', 'new final'),
                    ('assistant', f'new final\nCCB_DONE: {fixed_req_id}'),
                ], {**state, 'session_path': str(tmp_path / 'droid-new.jsonl'), 'offset': 2, 'phase': 2, 'ready': False}
            return [], state

    monkeypatch.setattr(droid_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(droid_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(droid_adapter_module, 'DroidLogReader', FakeReader)

    service = ExecutionService(build_default_execution_registry(), clock=lambda: '2026-03-18T00:00:00Z')
    service.start(_anchored_job_for_provider('droid', fixed_req_id, body='real droid'), runtime_context=_runtime_context(tmp_path))

    first = service.poll()[0]
    assert [item.kind for item in first.items] == [
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.ASSISTANT_CHUNK,
    ]

    second = service.poll()[0]
    assert [item.kind for item in second.items] == [
        CompletionItemKind.SESSION_ROTATE,
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.ASSISTANT_CHUNK,
        CompletionItemKind.ASSISTANT_FINAL,
    ]
    assert second.items[0].payload['session_path'] == str(tmp_path / 'droid-new.jsonl')
