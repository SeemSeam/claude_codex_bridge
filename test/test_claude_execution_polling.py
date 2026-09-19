from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pytest
from completion.models import CompletionConfidence, CompletionDecision, CompletionSourceKind, CompletionStatus
from provider_backends.claude.execution import ClaudeProviderAdapter
from provider_backends.claude.execution_runtime.polling import (
    _orphaned_exact_hook,
    poll_submission,
)
from provider_backends.claude.execution_runtime.start import looks_ready
from provider_execution.base import ProviderPollResult, ProviderSubmission


def _submission() -> ProviderSubmission:
    return ProviderSubmission(
        job_id="job_1",
        agent_name="agent1",
        provider="claude",
        accepted_at="2026-04-06T00:00:00Z",
        ready_at="2026-04-06T00:00:00Z",
        source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
        reply="",
        runtime_state={"state": {}, "mode": "active"},
    )


def _orphaned_submission(**runtime_overrides: object) -> ProviderSubmission:
    runtime_state: dict[str, object] = {
        "state": {},
        "mode": "active",
        "completion_dir": "/tmp/completion",
        "request_anchor": "job_1",
        "next_seq": 3,
        "anchor_seen": False,
        "prompt_activated": False,
        "prompt_sent": True,
        "no_wrap": False,
        "session_path": r"C:\Users\demo\.claude\projects\session-1.jsonl",
    }
    runtime_state.update(runtime_overrides)
    return replace(
        _submission(),
        diagnostics={"workspace_path": "C:/work/demo"},
        runtime_state=runtime_state,
    )


def _orphaned_event(**overrides: object) -> dict[str, object]:
    event: dict[str, object] = {
        "schema_version": 1,
        "record_type": "provider_completion_hook",
        "provider": "claude",
        "agent_name": "agent1",
        "workspace_path": r"C:\work\demo",
        "req_id": "job_1",
        "reply": "completed reply",
        "timestamp": "2026-04-06T00:01:00Z",
        "status": "completed",
        "hook_event_name": "Stop",
        "session_id": "session-1",
    }
    event.update(overrides)
    return event


class _PaneBackend:
    def __init__(self, text: str) -> None:
        self.text = text

    def get_pane_content(self, pane_id: str, lines: int = 120) -> str:
        assert pane_id == "%1"
        assert lines == 80
        return self.text


def test_orphaned_exact_hook_recovers_after_grace_with_independent_proof(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.hook_results_runtime.load_event",
        lambda completion_dir, request_anchor: _orphaned_event(),
    )
    prepared = SimpleNamespace(
        backend=_PaneBackend("completed\n❯\n"),
        pane_id="%1",
    )

    result = _orphaned_exact_hook(
        _orphaned_submission(),
        prepared=prepared,
        now="2026-04-06T00:04:00Z",
    )

    assert result is not None
    assert result.decision is not None
    assert result.decision.reply == "completed reply"
    assert result.decision.diagnostics["completion_fallback_source"] == "orphaned_exact_hook"
    assert result.decision.diagnostics["request_anchor_observation_missed"] is True
    assert result.decision.diagnostics["orphaned_hook_age_s"] == 180.0


@pytest.mark.parametrize(
    ("event_overrides", "runtime_overrides", "pane_text", "now"),
    [
        ({}, {}, "completed\n❯\n", "2026-04-06T00:03:59Z"),
        ({"timestamp": "2026-04-05T23:59:59Z"}, {}, "completed\n❯\n", "2026-04-06T00:04:00Z"),
        ({"session_id": None}, {}, "completed\n❯\n", "2026-04-06T00:04:00Z"),
        ({}, {"session_path": ""}, "completed\n❯\n", "2026-04-06T00:04:00Z"),
        ({"session_id": "other"}, {}, "completed\n❯\n", "2026-04-06T00:04:00Z"),
        ({}, {"prompt_sent": False}, "completed\n❯\n", "2026-04-06T00:04:00Z"),
        ({}, {}, "working\nesc to interrupt", "2026-04-06T00:04:00Z"),
        ({}, {}, "no input prompt", "2026-04-06T00:04:00Z"),
    ],
)
def test_orphaned_exact_hook_rejects_incomplete_or_unsafe_proof(
    monkeypatch,
    event_overrides,
    runtime_overrides,
    pane_text,
    now,
) -> None:
    event = _orphaned_event(**event_overrides)
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.hook_results_runtime.load_event",
        lambda completion_dir, request_anchor: event,
    )

    result = _orphaned_exact_hook(
        _orphaned_submission(**runtime_overrides),
        prepared=SimpleNamespace(backend=_PaneBackend(pane_text), pane_id="%1"),
        now=now,
    )

    assert result is None


def test_poll_submission_returns_hook_result_before_pane_liveness(monkeypatch) -> None:
    submission = replace(
        _submission(),
        runtime_state={
            "state": {},
            "mode": "active",
            "anchor_seen": True,
            "prompt_activated": True,
        },
    )
    prepared = SimpleNamespace(reader=object(), backend=object(), pane_id="%1")
    hook_result = ProviderPollResult(submission=submission)
    liveness_calls: list[str] = []

    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.prepare_active_poll_without_liveness",
        lambda submission, now: prepared,
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.poll_exact_hook",
        lambda submission, now: hook_result,
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.ensure_active_pane_alive",
        lambda submission, backend, pane_id, now: liveness_calls.append(pane_id) or None,
    )

    result = poll_submission(None, submission, now="2026-04-06T00:00:01Z")

    assert result is hook_result
    assert liveness_calls == []


def test_poll_submission_processes_events_until_turn_boundary(monkeypatch) -> None:
    submission = _submission()
    prepared = SimpleNamespace(reader=object(), backend=object(), pane_id="%1")
    poll = SimpleNamespace(anchor_seen=True, reached_turn_boundary=False)
    calls: list[tuple[str, object]] = []
    batches = iter(
        [
            (
                [
                    {"role": "user", "text": "hello"},
                    {"role": "assistant", "text": "done"},
                ],
                {"cursor": 1},
            ),
            ([], {"cursor": 2}),
        ]
    )

    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.prepare_active_poll_without_liveness",
        lambda submission, now: prepared,
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.poll_exact_hook",
        lambda submission, now: None,
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.ensure_active_pane_alive",
        lambda submission, backend, pane_id, now: None,
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.build_poll_state",
        lambda submission: poll,
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.read_events",
        lambda reader, state: next(batches),
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.state_session_path",
        lambda state: f"path-{state['cursor']}",
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.apply_session_rotation",
        lambda submission, poll, new_session_path, now: calls.append(("rotate", new_session_path)),
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.handle_user_event",
        lambda submission, poll, text, now: calls.append(("user", text)),
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.handle_assistant_event",
        lambda submission, poll, event, now: calls.append(("assistant", event["text"]))
        or setattr(poll, "reached_turn_boundary", True),
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.handle_system_event",
        lambda submission, poll, event, now, state: None,
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.finalize_poll_result",
        lambda submission, poll, state: {"state": state, "calls": list(calls)},
    )

    result = poll_submission(None, submission, now="2026-04-06T00:00:01Z")

    assert result["state"] == {"cursor": 1}
    assert result["calls"] == [
        ("rotate", "path-1"),
        ("user", "hello"),
        ("assistant", "done"),
    ]


def test_poll_submission_recovers_anchored_round_result_from_idle_pane(monkeypatch) -> None:
    submission = replace(_submission(), agent_name="ccb_round_reviewer")
    batches = iter(
        [
            ([{"role": "user", "text": "CCB_REQ_ID: job_1"}], {"cursor": 1}),
            ([], {"cursor": 2}),
        ]
    )

    class Backend:
        def get_pane_content(self, pane_id: str, lines: int = 120) -> str:
            assert pane_id == "%1"
            assert lines == 2000
            return (
                "CCB_REQ_ID:\n  job_old\n● round result: blocked\n"
                "CCB_REQ_ID:\n  job_1\nround result: blocked\n"
                "Thinking...\n● round result: pass\n"
                "╭──────────╮\n│ >        │\n╰──────────╯\n? for shortcuts\n"
            )

    prepared = SimpleNamespace(reader=object(), backend=Backend(), pane_id="%1")
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.prepare_active_poll_without_liveness",
        lambda submission, now: prepared,
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.poll_exact_hook",
        lambda submission, now: None,
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.ensure_active_pane_alive",
        lambda submission, backend, pane_id, now: None,
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.read_events",
        lambda reader, state: next(batches),
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.state_session_path",
        lambda state: "",
    )

    result = poll_submission(None, submission, now="2026-04-06T00:00:01Z")

    assert isinstance(result, ProviderPollResult)
    assert result.decision is not None
    assert result.decision.status.value == "completed"
    assert result.decision.reason == "claude_idle_pane_round_result"
    assert result.decision.reply == "round result: pass"
    assert result.decision.diagnostics["completion_source"] == "idle_pane_round_result"
    assert result.decision.diagnostics["completion_fallback_source"] == "terminal_capture"
    assert result.decision.diagnostics["terminal_capture_role"] == "provider_declared_fallback"


def test_poll_submission_does_not_use_round_result_while_pane_is_busy(monkeypatch) -> None:
    submission = replace(_submission(), agent_name="ccb_round_reviewer")
    batches = iter(
        [
            ([{"role": "user", "text": "CCB_REQ_ID: job_1"}], {"cursor": 1}),
            ([], {"cursor": 2}),
        ]
    )

    class Backend:
        def get_pane_content(self, pane_id: str, lines: int = 120) -> str:
            assert lines == 2000
            return "CCB_REQ_ID: job_1\n● round result: pass\nesc to interrupt"

    prepared = SimpleNamespace(reader=object(), backend=Backend(), pane_id="%1")
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.prepare_active_poll_without_liveness",
        lambda submission, now: prepared,
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.poll_exact_hook",
        lambda submission, now: None,
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.ensure_active_pane_alive",
        lambda submission, backend, pane_id, now: None,
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.read_events",
        lambda reader, state: next(batches),
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.state_session_path",
        lambda state: "",
    )

    result = poll_submission(None, submission, now="2026-04-06T00:00:01Z")

    assert isinstance(result, ProviderPollResult)
    assert result.decision is None


def test_poll_submission_does_not_treat_unmarked_prompt_result_as_assistant_output(monkeypatch) -> None:
    submission = replace(_submission(), agent_name="ccb_round_reviewer")
    batches = iter(
        [
            ([{"role": "user", "text": "CCB_REQ_ID: job_1"}], {"cursor": 1}),
            ([], {"cursor": 2}),
        ]
    )

    class Backend:
        def get_pane_content(self, pane_id: str, lines: int = 120) -> str:
            assert lines == 2000
            return (
                "CCB_REQ_ID: job_1\nround result: blocked\n"
                "╭──────────╮\n│ >        │\n╰──────────╯\n? for shortcuts\n"
            )

    prepared = SimpleNamespace(reader=object(), backend=Backend(), pane_id="%1")
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.prepare_active_poll_without_liveness",
        lambda submission, now: prepared,
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.poll_exact_hook",
        lambda submission, now: None,
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.ensure_active_pane_alive",
        lambda submission, backend, pane_id, now: None,
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.read_events",
        lambda reader, state: next(batches),
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.state_session_path",
        lambda state: "",
    )

    result = poll_submission(None, submission, now="2026-04-06T00:00:01Z")

    assert isinstance(result, ProviderPollResult)
    assert result.decision is None


def test_poll_submission_returns_system_terminal_result(monkeypatch) -> None:
    submission = _submission()
    prepared = SimpleNamespace(reader=object(), backend=object(), pane_id="%1")
    poll = SimpleNamespace(anchor_seen=True, reached_turn_boundary=False)
    terminal_result = ProviderPollResult(submission=submission)

    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.prepare_active_poll_without_liveness",
        lambda submission, now: prepared,
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.poll_exact_hook",
        lambda submission, now: None,
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.ensure_active_pane_alive",
        lambda submission, backend, pane_id, now: None,
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.build_poll_state",
        lambda submission: poll,
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.read_events",
        lambda reader, state: ([{"role": "system", "kind": "error"}], {"cursor": 1}),
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.state_session_path",
        lambda state: None,
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.apply_session_rotation",
        lambda submission, poll, new_session_path, now: None,
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.handle_system_event",
        lambda submission, poll, event, now, state: terminal_result,
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.finalize_poll_result",
        lambda submission, poll, state: (_ for _ in ()).throw(AssertionError("finalize should not run")),
    )

    result = poll_submission(None, submission, now="2026-04-06T00:00:01Z")

    assert result is terminal_result


def test_poll_submission_reply_delivery_defers_before_ready_timeout(monkeypatch) -> None:
    submission = ProviderSubmission(
        job_id="job_reply",
        agent_name="agent1",
        provider="claude",
        accepted_at="2026-04-06T00:00:00Z",
        ready_at="2026-04-06T00:00:00Z",
        source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
        reply="",
        runtime_state={
            "state": {},
            "mode": "active",
            "pane_id": "%1",
            "prompt_text": "CCB_REPLY from=agent2 reply=rep_1",
            "prompt_sent": False,
            "reply_delivery_complete_on_dispatch": True,
            "reply_delivery_require_ready": True,
            "request_anchor": "job_reply",
            "ready_wait_started_at": "2026-04-06T00:00:00Z",
            "ready_timeout_s": 30.0,
        },
    )
    sent: list[tuple[str, str]] = []

    class BusyBackend:
        def get_pane_content(self, pane_id: str, lines: int = 120) -> str:
            assert pane_id == "%1"
            assert lines == 120
            return "Claude is still busy"

        def send_text(self, pane_id: str, text: str) -> None:
            sent.append((pane_id, text))

    prepared = SimpleNamespace(reader=object(), backend=BusyBackend(), pane_id="%1")

    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.prepare_active_poll_without_liveness",
        lambda submission, now: prepared,
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.poll_exact_hook",
        lambda submission, now: None,
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.ensure_active_pane_alive",
        lambda submission, backend, pane_id, now: None,
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.build_poll_state",
        lambda submission: SimpleNamespace(
            anchor_seen=False,
            reached_turn_boundary=False,
            items=[],
            next_seq=1,
            request_anchor="job_reply",
            reply_buffer="",
            raw_buffer="",
            session_path="",
            last_assistant_uuid="",
        ),
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.read_events",
        lambda reader, state: ([], state),
    )

    result = poll_submission(None, submission, now="2026-04-06T00:00:01Z")

    assert isinstance(result, ProviderPollResult)
    assert result.decision is None
    assert result.submission.runtime_state["prompt_sent"] is False
    assert sent == []


def test_poll_submission_reply_delivery_dispatches_after_ready_timeout_and_holds(monkeypatch) -> None:
    submission = ProviderSubmission(
        job_id="job_reply",
        agent_name="agent1",
        provider="claude",
        accepted_at="2026-04-06T00:00:00Z",
        ready_at="2026-04-06T00:00:00Z",
        source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
        reply="",
        runtime_state={
            "state": {},
            "mode": "active",
            "pane_id": "%1",
            "prompt_text": "CCB_REPLY from=agent2 reply=rep_1",
            "prompt_sent": False,
            "reply_delivery_complete_on_dispatch": True,
            "reply_delivery_require_ready": True,
            "request_anchor": "job_reply",
            "ready_wait_started_at": "2026-04-06T00:00:00Z",
            "ready_timeout_s": 0.0,
        },
    )
    sent: list[tuple[str, str]] = []

    class BusyBackend:
        def get_pane_content(self, pane_id: str, lines: int = 120) -> str:
            assert pane_id == "%1"
            assert lines == 120
            return "Claude is still busy"

        def send_text(self, pane_id: str, text: str) -> None:
            sent.append((pane_id, text))

    prepared = SimpleNamespace(reader=object(), backend=BusyBackend(), pane_id="%1")

    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.prepare_active_poll_without_liveness",
        lambda submission, now: prepared,
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.poll_exact_hook",
        lambda submission, now: None,
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling._orphaned_exact_hook",
        lambda submission, prepared, now: None,
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.ensure_active_pane_alive",
        lambda submission, backend, pane_id, now: None,
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.read_events",
        lambda reader, state: ([], state),
    )

    result = poll_submission(None, submission, now="2026-04-06T00:00:01Z")

    # The bounded ready-wait send is transport only: the delivery stays held
    # (no terminal decision) until the attributed turn-end evidence arrives.
    assert isinstance(result, ProviderPollResult)
    assert result.decision is None
    assert result.submission.runtime_state["prompt_sent"] is True
    assert sent == [("%1", "CCB_REPLY from=agent2 reply=rep_1")]


def test_poll_submission_reply_delivery_completes_at_attributed_turn_end(monkeypatch) -> None:
    submission = ProviderSubmission(
        job_id="job_reply",
        agent_name="agent1",
        provider="claude",
        accepted_at="2026-04-06T00:00:00Z",
        ready_at="2026-04-06T00:00:00Z",
        source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
        reply="",
        runtime_state={
            "state": {},
            "mode": "active",
            "pane_id": "%1",
            "prompt_text": "CCB_REPLY from=agent2 reply=rep_1",
            "prompt_sent": True,
            "prompt_sent_at": "2026-04-06T00:00:01Z",
            "anchor_seen": True,
            "reply_delivery_complete_on_dispatch": True,
            "reply_delivery_require_ready": True,
            "request_anchor": "job_reply",
            "ready_wait_started_at": "2026-04-06T00:00:00Z",
            "ready_timeout_s": 30.0,
        },
    )
    sent: list[tuple[str, str]] = []

    class ReadyBackend:
        def get_pane_content(self, pane_id: str, lines: int = 120) -> str:
            assert pane_id == "%1"
            assert lines == 120
            return "❯\n  ? for shortcuts"

        def send_text(self, pane_id: str, text: str) -> None:
            sent.append((pane_id, text))

    prepared = SimpleNamespace(reader=object(), backend=ReadyBackend(), pane_id="%1")
    hook_decision = CompletionDecision(
        terminal=True,
        status=CompletionStatus.COMPLETED,
        reason="hook_stop",
        confidence=CompletionConfidence.EXACT,
        reply="",
        anchor_seen=True,
        reply_started=False,
        reply_stable=True,
        provider_turn_ref="job_reply",
        source_cursor=None,
        finished_at="2026-04-06T00:00:05Z",
        diagnostics={"reply_delivery": True},
    )

    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.prepare_active_poll_without_liveness",
        lambda submission, now: prepared,
    )
    monkeypatch.setattr(
        "provider_backends.claude.execution_runtime.polling.poll_exact_hook",
        lambda submission, now: ProviderPollResult(
            submission=submission,
            items=(),
            decision=hook_decision,
        ),
    )

    result = poll_submission(None, submission, now="2026-04-06T00:00:05Z")

    # Sending never completes the delivery by itself; the attributed Stop
    # hook for this delivery's anchor is the turn-end evidence.
    assert isinstance(result, ProviderPollResult)
    assert result.decision is not None
    assert result.decision.terminal is True
    assert result.decision.reason == "hook_stop"
    assert sent == []


def test_looks_ready_accepts_nbsp_prompt_line() -> None:
    text = (
        "────────────────────────────────────────────────────\n"
        "❯\xa0\n"
        "────────────────────────────────────────────────────\n"
        "  ⏵⏵ bypass permissions on (shift+tab to cycle)\n"
    )

    assert looks_ready(text) is True


def test_looks_ready_prefers_prompt_line_over_welcome_banner() -> None:
    text = (
        "Welcome back!\n"
        "Some older banner text\n"
        "────────────────────────────────────────────────────\n"
        "❯\xa0\n"
        "────────────────────────────────────────────────────\n"
        "  ⏵⏵ bypass permissions on (shift+tab to cycle)\n"
    )

    assert looks_ready(text) is True


def test_claude_export_runtime_state_preserves_reply_delivery_flags() -> None:
    adapter = ClaudeProviderAdapter()
    submission = ProviderSubmission(
        job_id="job_reply",
        agent_name="agent1",
        provider="claude",
        accepted_at="2026-04-06T00:00:00Z",
        ready_at="2026-04-06T00:00:00Z",
        source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
        reply="",
        runtime_state={
            "mode": "active",
            "state": {},
            "pane_id": "%1",
            "request_anchor": "job_reply",
            "next_seq": 1,
            "anchor_seen": True,
            "no_wrap": True,
            "reply_buffer": "",
            "raw_buffer": "",
            "session_path": "/tmp/session.jsonl",
            "last_assistant_uuid": "",
            "prompt_enqueued": True,
            "queue_dequeue_observed": True,
            "prompt_activated": True,
            "prompt_enqueue_uuid": "queue-1",
            "prompt_activation_uuid": "queue-1",
            "completion_dir": "/tmp/completion",
            "prompt_text": "CCB_REPLY from=agent2 reply=rep_1",
            "prompt_sent": False,
            "prompt_sent_at": None,
            "reply_delivery_complete_on_dispatch": True,
            "reply_delivery_require_ready": True,
            "ready_wait_started_at": "2026-04-06T00:00:00Z",
            "ready_timeout_s": 8.0,
        },
    )

    exported = adapter.export_runtime_state(submission)

    assert exported["reply_delivery_complete_on_dispatch"] is True
    assert exported["reply_delivery_require_ready"] is True
    assert exported["prompt_enqueued"] is True
    assert exported["queue_dequeue_observed"] is True
    assert exported["prompt_activated"] is True
    assert exported["prompt_enqueue_uuid"] == "queue-1"
    assert exported["prompt_activation_uuid"] == "queue-1"
