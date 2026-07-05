from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from completion.models import (
    CompletionConfidence,
    CompletionCursor,
    CompletionDecision,
    CompletionItem,
    CompletionItemKind,
    CompletionSourceKind,
    CompletionStatus,
)
from provider_execution.base import ProviderPollResult, ProviderRuntimeContext, ProviderSubmission
from provider_execution.registry import build_default_execution_registry
from provider_execution.reliability import CompletionReliabilityPolicy, adapter_reliability_policy
from provider_execution.service import ExecutionService
from provider_execution.service_runtime.persistence import persist_submission
from provider_execution.service_runtime.polling import poll_updates
from provider_execution.service_runtime.restore import restore_submission
from provider_execution.state_models import PersistedExecutionState


def _submission(job_id: str = "job_1", provider: str = "fake") -> ProviderSubmission:
    return ProviderSubmission(
        job_id=job_id,
        agent_name="agent1",
        provider=provider,
        accepted_at="2026-04-06T00:00:00Z",
        ready_at="2026-04-06T00:00:00Z",
        source_kind=CompletionSourceKind.PROTOCOL_EVENT_STREAM,
        reply="",
    )


def _decision(*, reply: str = "done") -> CompletionDecision:
    return CompletionDecision(
        terminal=True,
        status=CompletionStatus.COMPLETED,
        reason="result_message",
        confidence=CompletionConfidence.EXACT,
        reply=reply,
        anchor_seen=True,
        reply_started=True,
        reply_stable=True,
        provider_turn_ref=None,
        source_cursor=None,
        finished_at="2026-04-06T00:00:01Z",
        diagnostics={},
    )


def _item(seq: int = 1, *, kind: CompletionItemKind = CompletionItemKind.RESULT) -> CompletionItem:
    return CompletionItem(
        kind=kind,
        timestamp="2026-04-06T00:00:01Z",
        cursor=CompletionCursor(source_kind=CompletionSourceKind.PROTOCOL_EVENT_STREAM, event_seq=seq),
        provider="fake",
        agent_name="agent1",
        req_id="job_1",
        payload={"text": "done"},
    )


def _runtime_context() -> ProviderRuntimeContext:
    return ProviderRuntimeContext(
        agent_name="agent1",
        workspace_path="/tmp/demo",
        backend_type="pane-backed",
        runtime_ref="ref",
        session_ref="session",
    )


def test_codex_defaults_to_bounded_no_terminal_timeout() -> None:
    registry = build_default_execution_registry(include_optional=False, include_test_doubles=False)

    codex = registry.get("codex")
    assert codex is not None
    codex_policy = adapter_reliability_policy(codex)
    assert codex_policy is not None
    assert codex_policy.primary_authority == "protocol_log"
    assert codex_policy.no_terminal_timeout_s == 900.0

    for provider in ("claude", "gemini"):
        adapter = registry.get(provider)
        assert adapter is not None
        policy = adapter_reliability_policy(adapter)
        assert policy is not None
        assert policy.no_terminal_timeout_s == 0.0


def test_poll_updates_processes_terminal_result_and_cleans_active_state(monkeypatch) -> None:
    submission = _submission()
    result = SimpleNamespace(submission=submission, items=(_item(),), decision=_decision())
    service = SimpleNamespace(
        _clock=lambda: "2026-04-06T00:00:01Z",
        _pending_replays={},
        _active={"job_1": submission},
        _runtime_contexts={"job_1": _runtime_context()},
        _registry={"fake": SimpleNamespace(poll=lambda current, now: result)},
    )
    persisted: list[tuple[str, object, tuple]] = []
    monkeypatch.setattr(
        "provider_execution.service_runtime.polling.persist_submission",
        lambda service, job_id, pending_decision=None, pending_items=(): persisted.append((job_id, pending_decision, pending_items)),
    )

    updates = poll_updates(service)

    assert len(updates) == 1
    assert updates[0].job_id == "job_1"
    assert updates[0].decision is result.decision
    assert service._active == {}
    assert service._runtime_contexts == {}
    assert persisted == [("job_1", result.decision, result.items)]


def test_poll_updates_keeps_terminal_pending_replay_until_acknowledged() -> None:
    decision = _decision()
    service = SimpleNamespace(
        _clock=lambda: "2026-04-06T00:00:01Z",
        _pending_replays={"job_1": ((), decision)},
        _active={},
        _runtime_contexts={},
        _registry={},
    )

    updates = poll_updates(service)

    assert len(updates) == 1
    assert updates[0].job_id == "job_1"
    assert updates[0].decision is decision
    assert "job_1" in service._pending_replays


def test_poll_updates_terminalizes_reliability_timeout(monkeypatch) -> None:
    submission = _submission(provider="codex")
    adapter = SimpleNamespace(
        poll=lambda current, now: None,
        completion_reliability_policy=CompletionReliabilityPolicy(
            provider="codex",
            primary_authority="protocol_log",
            no_terminal_timeout_s=900.0,
        ),
    )
    service = SimpleNamespace(
        _clock=lambda: "2026-04-06T00:15:01Z",
        _pending_replays={},
        _active={"job_1": submission},
        _runtime_contexts={"job_1": _runtime_context()},
        _registry={"codex": adapter},
    )
    captured: dict[str, object] = {}

    def _persist(service, job_id, pending_decision=None, pending_items=()):
        captured["job_id"] = job_id
        captured["decision"] = pending_decision
        captured["items"] = pending_items
        captured["submission"] = service._active.get(job_id)

    monkeypatch.setattr(
        "provider_execution.service_runtime.polling.persist_submission",
        _persist,
    )

    updates = poll_updates(service)

    assert len(updates) == 1
    update = updates[0]
    assert update.job_id == "job_1"
    assert update.items == ()
    assert update.decision is not None
    assert update.decision.status is CompletionStatus.INCOMPLETE
    assert update.decision.reason == "completion_timeout"
    assert update.decision.confidence is CompletionConfidence.DEGRADED
    assert update.decision.diagnostics["completion_primary_authority"] == "protocol_log"
    assert update.decision.diagnostics["completion_timeout_class"] == "anchor_missing_no_reply"
    assert update.decision.diagnostics["no_reply_reason"] == "completion_detection_gap"
    assert update.decision.diagnostics["no_reply_detail"]["completion_timeout_class"] == "anchor_missing_no_reply"
    assert captured["job_id"] == "job_1"
    assert captured["decision"] is update.decision
    assert captured["items"] == ()
    assert captured["submission"].diagnostics["completion_fallback_source"] == "execution_reliability_monitor"
    assert service._active == {}
    assert service._runtime_contexts == {}


def test_poll_updates_timeout_classifies_anchor_seen_without_reply(monkeypatch) -> None:
    submission = replace(
        _submission(provider="codex"),
        runtime_state={"anchor_seen": True, "bound_turn_id": "turn-1"},
    )
    adapter = SimpleNamespace(
        poll=lambda current, now: None,
        completion_reliability_policy=CompletionReliabilityPolicy(
            provider="codex",
            primary_authority="protocol_log",
            no_terminal_timeout_s=900.0,
        ),
    )
    service = SimpleNamespace(
        _clock=lambda: "2026-04-06T00:15:01Z",
        _pending_replays={},
        _active={"job_1": submission},
        _runtime_contexts={"job_1": _runtime_context()},
        _registry={"codex": adapter},
    )
    monkeypatch.setattr(
        "provider_execution.service_runtime.polling.persist_submission",
        lambda service, job_id, pending_decision=None, pending_items=(): None,
    )

    updates = poll_updates(service)

    assert len(updates) == 1
    decision = updates[0].decision
    assert decision is not None
    assert decision.diagnostics["completion_timeout_class"] == "anchor_seen_no_reply"
    assert decision.diagnostics["no_reply_detail"]["anchor_seen"] is True
    assert decision.diagnostics["no_reply_detail"]["bound_turn_id"] == "turn-1"


def test_poll_updates_timeout_harvests_captured_reply_without_terminal(monkeypatch) -> None:
    submission = replace(
        _submission(provider="codex"),
        runtime_state={
            "anchor_seen": True,
            "bound_turn_id": "turn-1",
            "last_final_answer": "done from runtime state",
            "session_path": "/tmp/codex-session.jsonl",
        },
    )
    adapter = SimpleNamespace(
        poll=lambda current, now: None,
        completion_reliability_policy=CompletionReliabilityPolicy(
            provider="codex",
            primary_authority="protocol_log",
            no_terminal_timeout_s=900.0,
        ),
    )
    service = SimpleNamespace(
        _clock=lambda: "2026-04-06T00:15:01Z",
        _pending_replays={},
        _active={"job_1": submission},
        _runtime_contexts={"job_1": _runtime_context()},
        _registry={"codex": adapter},
    )
    captured: dict[str, object] = {}

    def _persist(service, job_id, pending_decision=None, pending_items=()):
        captured["submission"] = service._active.get(job_id)
        captured["decision"] = pending_decision

    monkeypatch.setattr(
        "provider_execution.service_runtime.polling.persist_submission",
        _persist,
    )

    updates = poll_updates(service)

    assert len(updates) == 1
    decision = updates[0].decision
    assert decision is not None
    assert decision.reply == "done from runtime state"
    assert decision.reply_started is True
    assert decision.diagnostics["completion_timeout_class"] == "reply_captured_no_terminal"
    assert decision.diagnostics["completion_reply_source"] == "last_final_answer"
    assert decision.diagnostics["no_reply_detail"]["recovery_action"] == "deliver_degraded_reply_then_repair_completion_log"
    assert captured["submission"].reply == "done from runtime state"


def test_poll_updates_terminalizes_timeout_when_poll_only_advances_cursor(monkeypatch) -> None:
    submission = _submission(provider="codex")

    def _poll(current, now):
        del now
        updated = replace(
            current,
            runtime_state={
                **current.runtime_state,
                "state": {"offset": 42, "last_rescan": 123.0},
            },
        )
        return ProviderPollResult(submission=updated)

    adapter = SimpleNamespace(
        poll=_poll,
        completion_reliability_policy=CompletionReliabilityPolicy(
            provider="codex",
            primary_authority="protocol_log",
            no_terminal_timeout_s=900.0,
        ),
    )
    service = SimpleNamespace(
        _clock=lambda: "2026-04-06T00:15:01Z",
        _pending_replays={},
        _active={"job_1": submission},
        _runtime_contexts={"job_1": _runtime_context()},
        _registry={"codex": adapter},
    )
    captured: dict[str, object] = {}

    def _persist(service, job_id, pending_decision=None, pending_items=()):
        captured["job_id"] = job_id
        captured["decision"] = pending_decision
        captured["items"] = pending_items
        captured["submission"] = service._active.get(job_id)

    monkeypatch.setattr(
        "provider_execution.service_runtime.polling.persist_submission",
        _persist,
    )

    updates = poll_updates(service)

    assert len(updates) == 1
    assert updates[0].decision is not None
    assert updates[0].decision.reason == "completion_timeout"
    assert updates[0].decision.anchor_seen is False
    assert captured["submission"].runtime_state["state"] == {"offset": 42, "last_rescan": 123.0}
    assert service._active == {}
    assert service._runtime_contexts == {}


def test_poll_updates_terminalizes_timeout_when_poll_only_emits_session_bookkeeping(monkeypatch) -> None:
    submission = _submission(provider="codex")

    def _poll(current, now):
        del now
        updated = replace(
            current,
            runtime_state={
                **current.runtime_state,
                "state": {"offset": 42, "last_rescan": 123.0},
            },
        )
        return ProviderPollResult(
            submission=updated,
            items=(_item(kind=CompletionItemKind.SESSION_ROTATE),),
        )

    adapter = SimpleNamespace(
        poll=_poll,
        completion_reliability_policy=CompletionReliabilityPolicy(
            provider="codex",
            primary_authority="protocol_log",
            no_terminal_timeout_s=900.0,
        ),
    )
    service = SimpleNamespace(
        _clock=lambda: "2026-04-06T00:15:01Z",
        _pending_replays={},
        _active={"job_1": submission},
        _runtime_contexts={"job_1": _runtime_context()},
        _registry={"codex": adapter},
    )
    captured: dict[str, object] = {}

    def _persist(service, job_id, pending_decision=None, pending_items=()):
        captured["job_id"] = job_id
        captured["decision"] = pending_decision
        captured["items"] = pending_items

    monkeypatch.setattr(
        "provider_execution.service_runtime.polling.persist_submission",
        _persist,
    )

    updates = poll_updates(service)

    assert len(updates) == 1
    assert updates[0].decision is not None
    assert updates[0].decision.reason == "completion_timeout"
    assert captured["decision"] is updates[0].decision
    assert captured["items"] == ()
    assert service._active == {}
    assert service._runtime_contexts == {}


def test_reliability_progress_ignores_cursor_noise_but_tracks_anchor(monkeypatch) -> None:
    submission = _submission(provider="codex")
    calls = iter(
        [
            ProviderPollResult(
                submission=replace(
                    submission,
                    runtime_state={
                        **submission.runtime_state,
                        "state": {"offset": 42, "last_rescan": 123.0},
                    },
                )
            ),
            ProviderPollResult(
                submission=replace(
                    submission,
                    runtime_state={
                        **submission.runtime_state,
                        "anchor_seen": True,
                        "state": {"offset": 84, "last_rescan": 456.0},
                    },
                )
            ),
        ]
    )
    adapter = SimpleNamespace(
        poll=lambda current, now: next(calls),
        completion_reliability_policy=CompletionReliabilityPolicy(
            provider="codex",
            primary_authority="protocol_log",
            no_terminal_timeout_s=900.0,
        ),
    )
    service = SimpleNamespace(
        _clock=lambda: "2026-04-06T00:00:10Z",
        _pending_replays={},
        _active={"job_1": submission},
        _runtime_contexts={"job_1": _runtime_context()},
        _registry={"codex": adapter},
    )
    monkeypatch.setattr(
        "provider_execution.service_runtime.polling.persist_submission",
        lambda service, job_id, pending_decision=None, pending_items=(): None,
    )

    assert poll_updates(service) == ()
    assert "reliability_last_progress_at" not in service._active["job_1"].runtime_state

    assert poll_updates(service) == ()
    assert service._active["job_1"].runtime_state["reliability_last_progress_at"] == "2026-04-06T00:00:10Z"


def test_persist_submission_preserves_reliability_progress_state() -> None:
    saved: list[PersistedExecutionState] = []
    submission = replace(
        _submission(provider="codex"),
        runtime_state={
            "mode": "active",
            "state": {"offset": 42},
            "reliability_last_progress_at": "2026-04-06T00:02:00Z",
            "reliability_timeout_deadline_at": "2026-04-06T00:17:00Z",
        },
    )
    service = SimpleNamespace(
        _clock=lambda: "2026-04-06T00:03:00Z",
        _active={"job_1": submission},
        _runtime_contexts={"job_1": _runtime_context()},
        _state_store=SimpleNamespace(save=lambda state: saved.append(state)),
        _registry={
            "codex": SimpleNamespace(
                export_runtime_state=lambda current: {
                    "mode": current.runtime_state.get("mode"),
                    "state": current.runtime_state.get("state"),
                }
            )
        },
    )

    persist_submission(service, "job_1")

    assert len(saved) == 1
    runtime_state = saved[0].submission.runtime_state
    assert runtime_state["mode"] == "active"
    assert runtime_state["state"] == {"offset": 42}
    assert runtime_state["reliability_last_progress_at"] == "2026-04-06T00:02:00Z"
    assert runtime_state["reliability_timeout_deadline_at"] == "2026-04-06T00:17:00Z"


def test_active_runtime_snapshots_expose_bounded_safe_state() -> None:
    adapter = SimpleNamespace(
        completion_reliability_policy=CompletionReliabilityPolicy(
            provider='codex',
            primary_authority='protocol_log',
            no_terminal_timeout_s=900.0,
        )
    )
    service = ExecutionService(
        SimpleNamespace(get=lambda provider: adapter if provider == 'codex' else None),
        clock=lambda: '2026-04-06T00:00:45Z',
    )
    service._active = {
        'job_1': replace(
            _submission(provider='codex'),
            runtime_state={
                'backend': object(),
                'reader': object(),
                'prompt_text': 'large private prompt',
                'reply_buffer': 'partial private reply',
                'request_anchor': 'CCB_REQ_ID: job_1',
                'anchor_seen': False,
                'session_path': '/tmp/codex/session.jsonl',
                'delivery_state': 'pending_anchor',
                'delivery_started_at': '2026-04-06T00:00:00Z',
                'delivery_timeout_s': 120.0,
                'delivery_target_pane_id': '%1',
            },
        )
    }
    service._runtime_contexts = {'job_1': _runtime_context()}

    snapshots = service.active_runtime_snapshots()

    assert len(snapshots) == 1
    snapshot = snapshots[0]
    runtime_state = snapshot['runtime_state']
    assert snapshot['job_id'] == 'job_1'
    assert snapshot['provider'] == 'codex'
    assert snapshot['source_kind'] == 'protocol_event_stream'
    assert snapshot['primary_authority'] == 'protocol_log'
    assert snapshot['no_terminal_timeout_s'] == 900.0
    assert snapshot['no_terminal_deadline_at'] == '2026-04-06T00:15:00Z'
    assert runtime_state['request_anchor'] == 'CCB_REQ_ID: job_1'
    assert runtime_state['delivery_state'] == 'pending_anchor'
    assert runtime_state['delivery_timeout_deadline_at'] == '2026-04-06T00:02:00Z'
    assert 'backend' not in runtime_state
    assert 'reader' not in runtime_state
    assert 'prompt_text' not in runtime_state
    assert 'reply_buffer' not in runtime_state


def test_restore_submission_returns_terminal_pending_without_resume() -> None:
    persisted = PersistedExecutionState(
        submission=_submission(provider="fake"),
        runtime_context=_runtime_context(),
        resume_capable=True,
        persisted_at="2026-04-06T00:00:00Z",
        pending_decision=_decision(reply="already finished"),
        pending_items=(),
        applied_event_seqs=(),
    )
    state_store = SimpleNamespace(load=lambda job_id: persisted, remove=lambda job_id: None)
    service = SimpleNamespace(
        _active={},
        _state_store=state_store,
        _registry={"fake": SimpleNamespace()},
        _pending_replays={},
        _runtime_contexts={},
        _clock=lambda: "2026-04-06T00:00:01Z",
    )
    job = SimpleNamespace(job_id="job_1", agent_name="agent1", provider="fake")

    result = restore_submission(service, job)

    assert result.status == "terminal_pending"
    assert result.reason == "terminal_decision_recovered"
    assert result.decision is persisted.pending_decision


def test_restore_submission_abandons_when_adapter_missing() -> None:
    removed: list[str] = []
    persisted = PersistedExecutionState(
        submission=_submission(provider="fake"),
        runtime_context=None,
        resume_capable=False,
        persisted_at="2026-04-06T00:00:00Z",
    )
    state_store = SimpleNamespace(load=lambda job_id: persisted, remove=lambda job_id: removed.append(job_id))
    service = SimpleNamespace(
        _active={},
        _state_store=state_store,
        _registry={},
        _pending_replays={},
        _runtime_contexts={},
        _clock=lambda: "2026-04-06T00:00:01Z",
    )
    job = SimpleNamespace(job_id="job_1", agent_name="agent1", provider="fake")

    result = restore_submission(service, job)

    assert result.status == "abandoned"
    assert result.reason == "adapter_missing"
    assert removed == ["job_1"]
