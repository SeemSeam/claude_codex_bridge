from __future__ import annotations

from agents.models import (
    AgentSpec,
    PermissionMode,
    ProjectConfig,
    QueuePolicy,
    RestoreMode,
    RuntimeMode,
    WorkspaceMode,
)
from ccbd.api_models import DeliveryScope, JobRecord, JobStatus, MessageEnvelope
from completion.models import CompletionCursor, CompletionItem, CompletionItemKind, CompletionSourceKind, CompletionStatus
from completion.tracker import CompletionTrackerService
from provider_core.catalog import build_default_provider_catalog


def _job(*, provider: str = 'codex', agent_name: str = 'codex') -> JobRecord:
    return JobRecord(
        job_id='job_1',
        submission_id=None,
        agent_name=agent_name,
        provider=provider,
        request=MessageEnvelope(
            project_id='proj',
            to_agent=agent_name,
            from_actor='user',
            body='hello',
            task_id=None,
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


def _item(kind: CompletionItemKind, seq: int, ts: str, payload: dict | None = None) -> CompletionItem:
    return CompletionItem(
        kind=kind,
        timestamp=ts,
        cursor=CompletionCursor(source_kind=CompletionSourceKind.PROTOCOL_EVENT_STREAM, event_seq=seq, updated_at=ts),
        provider='codex',
        agent_name='codex',
        req_id='job_1',
        payload=payload or {},
    )


def _session_item(kind: CompletionItemKind, seq: int, ts: str, payload: dict | None = None) -> CompletionItem:
    return CompletionItem(
        kind=kind,
        timestamp=ts,
        cursor=CompletionCursor(source_kind=CompletionSourceKind.SESSION_SNAPSHOT, event_seq=seq, updated_at=ts),
        provider='gemini',
        agent_name='gemini',
        req_id='job_1',
        payload=payload or {},
    )


def _provider_config(*providers: str) -> ProjectConfig:
    agents: dict[str, AgentSpec] = {}
    for provider in providers:
        agents[provider] = AgentSpec(
            name=provider,
            provider=provider,
            target='.',
            workspace_mode=WorkspaceMode.GIT_WORKTREE,
            workspace_root=None,
            runtime_mode=RuntimeMode.PANE_BACKED,
            restore_default=RestoreMode.AUTO,
            permission_default=PermissionMode.MANUAL,
            queue_policy=QueuePolicy.SERIAL_PER_AGENT,
        )
    return ProjectConfig(version=2, default_agents=tuple(providers), agents=agents)


def test_completion_tracker_projects_reply_preview_and_terminal_reply() -> None:
    tracker = CompletionTrackerService(_provider_config('codex', 'claude', 'gemini'), build_default_provider_catalog())
    initial = tracker.start(_job(), started_at='2026-03-18T00:00:00Z')
    assert initial.decision.terminal is False
    assert initial.state.latest_cursor is not None

    running = tracker.ingest(
        'job_1',
        _item(CompletionItemKind.ASSISTANT_CHUNK, 1, '2026-03-18T00:00:01Z', {'text': 'partial'}),
    )
    assert running.decision.terminal is False
    assert running.decision.reply == 'partial'
    assert running.state.reply_started is True

    terminal = tracker.ingest(
        'job_1',
        _item(CompletionItemKind.TURN_BOUNDARY, 2, '2026-03-18T00:00:02Z', {'reason': 'task_complete'}),
    )
    assert terminal.decision.terminal is True
    assert terminal.decision.status is CompletionStatus.COMPLETED
    assert terminal.decision.reply == 'partial'


def test_completion_tracker_marks_empty_protocol_boundary_incomplete() -> None:
    tracker = CompletionTrackerService(_provider_config('codex', 'claude', 'gemini'), build_default_provider_catalog())
    tracker.start(_job(), started_at='2026-03-18T00:00:00Z')

    terminal = tracker.ingest(
        'job_1',
        _item(CompletionItemKind.TURN_BOUNDARY, 1, '2026-03-18T00:00:01Z', {'reason': 'task_complete'}),
    )

    assert terminal.decision.terminal is True
    assert terminal.decision.status is CompletionStatus.INCOMPLETE
    assert terminal.decision.reason == 'delivery_late_empty'
    assert terminal.decision.reply == ''
    assert terminal.decision.diagnostics['empty_reply'] is True
    assert terminal.decision.diagnostics['empty_reply_reason'] == 'delivery_late_empty'
    assert terminal.decision.diagnostics['error_type'] == 'empty_provider_reply'


def test_completion_tracker_clears_session_reply_preview_after_rotate() -> None:
    tracker = CompletionTrackerService(_provider_config('codex', 'claude', 'gemini'), build_default_provider_catalog())
    tracker.start(_job(provider='gemini', agent_name='gemini'), started_at='2026-03-18T00:00:00Z')

    first = tracker.ingest(
        'job_1',
        _session_item(
            CompletionItemKind.SESSION_SNAPSHOT,
            1,
            '2026-03-18T00:00:01Z',
            {'reply': 'stable reply', 'message_id': 'm1', 'message_count': 1, 'last_updated': '1'},
        ),
    )
    assert first.decision.reply == 'stable reply'
    assert first.state.reply_started is True

    rotated = tracker.ingest(
        'job_1',
        _session_item(
            CompletionItemKind.SESSION_ROTATE,
            2,
            '2026-03-18T00:00:02Z',
            {'session_path': '/tmp/new-session.json'},
        ),
    )
    assert rotated.decision.terminal is False
    assert rotated.decision.reply == ''


def test_completion_tracker_finalizes_timeout_after_request_deadline() -> None:
    tracker = CompletionTrackerService(
        _provider_config('codex', 'claude', 'gemini'),
        build_default_provider_catalog(),
        request_timeout_s=1.0,
    )
    tracker.start(_job(), started_at='2026-03-18T00:00:00Z')

    timed_out = tracker.tick('job_1', now='2026-03-18T00:00:02Z')
    assert timed_out.decision.terminal is True
    assert timed_out.decision.status is CompletionStatus.INCOMPLETE
    assert timed_out.decision.reason == 'timeout'


def test_completion_tracker_does_not_finalize_timeout_when_disabled() -> None:
    tracker = CompletionTrackerService(
        _provider_config('codex', 'claude', 'gemini'),
        build_default_provider_catalog(),
        request_timeout_s=0.0,
    )
    tracker.start(_job(), started_at='2026-03-18T00:00:00Z')

    running = tracker.tick('job_1', now='2026-03-18T02:00:00Z')
    assert running.decision.terminal is False
