from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from completion.models import CompletionItemKind, CompletionSourceKind, CompletionStatus
from provider_backends.cursor import execution
from provider_backends.cursor import pane_execution
from provider_backends.cursor.pane_execution import CursorPaneExecutionAdapter
from provider_backends.native_cli_support import NativeCliSubprocessAdapter
from provider_execution.base import ProviderRuntimeContext


def test_cursor_execution_adapter_defaults_to_visible_pane(monkeypatch) -> None:
    monkeypatch.delenv("CCB_CURSOR_EXECUTION_MODE", raising=False)

    adapter = execution.build_execution_adapter()

    assert type(adapter).__name__ == "CursorPaneExecutionAdapter"
    assert getattr(adapter, "provider", "") == "cursor"


def test_cursor_execution_adapter_supports_explicit_headless_rollback(monkeypatch) -> None:
    monkeypatch.setenv("CCB_CURSOR_EXECUTION_MODE", "headless")

    adapter = execution.build_execution_adapter()

    assert isinstance(adapter, NativeCliSubprocessAdapter)


def test_cursor_execution_adapter_rejects_unknown_mode(monkeypatch) -> None:
    monkeypatch.setenv("CCB_CURSOR_EXECUTION_MODE", "mirror")

    try:
        execution.build_execution_adapter()
    except ValueError as exc:
        assert "CCB_CURSOR_EXECUTION_MODE" in str(exc)
        assert "mirror" in str(exc)
    else:
        raise AssertionError("unknown Cursor execution mode must fail closed")


def test_cursor_headless_command_and_env_builders_remain_available() -> None:
    assert callable(execution.build_headless_execution_adapter)
    assert callable(execution._build_command)
    assert callable(execution._build_env)


class _FakeCursorSession:
    def __init__(self, home: Path, session_file: Path) -> None:
        self.data = {"cursor_home": str(home)}
        self.session_file = session_file
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        self.session_file.write_text("{}", encoding="utf-8")

    def ensure_pane(self) -> tuple[bool, str]:
        return True, "%9"


class _FakeCursorBackend:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.keys: list[tuple[str, str]] = []
        self.alive = True
        self.pane_text = ""

    def send_text_to_pane(self, pane_id: str, text: str) -> None:
        self.sent.append((pane_id, text))

    def is_tmux_pane_alive(self, pane_id: str) -> bool:
        return self.alive and pane_id == "%9"

    def send_key(self, pane_id: str, key: str) -> None:
        self.keys.append((pane_id, key))

    def get_pane_content(self, pane_id: str, lines: int = 20) -> str:
        assert pane_id == "%9"
        assert lines >= 80
        return self.pane_text


def _pane_job(*, message_type: str = "ask", no_wrap: bool = False):
    return SimpleNamespace(
        job_id="job_cursor_pane_1",
        agent_name="cursor1",
        provider="cursor",
        provider_instance=None,
        provider_options={"no_wrap": True} if no_wrap else {},
        request=SimpleNamespace(body="visible request", message_type=message_type),
    )


def _pane_context(tmp_path: Path) -> ProviderRuntimeContext:
    return ProviderRuntimeContext(
        agent_name="cursor1",
        workspace_path=str(tmp_path),
        backend_type="pane-backed",
        runtime_ref="%9",
        session_ref=str(tmp_path / ".ccb" / ".cursor-cursor1-session"),
    )


def _cursor_transcript(home: Path, session_id: str = "session-visible") -> Path:
    return (
        home
        / ".cursor"
        / "projects"
        / "repo"
        / "agent-transcripts"
        / session_id
        / f"{session_id}.jsonl"
    )


def _append_cursor_records(path: Path, *records: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=True) + "\n")


def _bind_cursor(
    monkeypatch,
    tmp_path: Path,
) -> tuple[Path, _FakeCursorBackend, _FakeCursorSession]:
    home = tmp_path / "managed-home"
    session = _FakeCursorSession(home, tmp_path / ".ccb" / ".cursor-cursor1-session")
    backend = _FakeCursorBackend()
    monkeypatch.setattr(pane_execution, "_load_session", lambda work_dir, agent_name: session)
    monkeypatch.setattr(pane_execution, "get_backend_for_session", lambda data: backend)
    return home, backend, session


def test_cursor_pane_adapter_sends_once_and_finishes_from_exact_anchored_transcript(
    monkeypatch,
    tmp_path: Path,
) -> None:
    home, backend, _ = _bind_cursor(monkeypatch, tmp_path)
    stale = _cursor_transcript(home, "stale-session")
    _append_cursor_records(
        stale,
        {"role": "user", "message": {"content": [{"type": "text", "text": "CCB_REQ_ID: job_cursor_pane_1"}]}},
        {"role": "assistant", "message": {"content": [{"type": "text", "text": "stale reply"}]}},
        {"type": "turn_ended", "status": "success"},
    )
    adapter = CursorPaneExecutionAdapter()

    submission = adapter.start(
        _pane_job(),
        context=_pane_context(tmp_path),
        now="2026-08-11T00:00:00Z",
    )

    assert submission.source_kind is CompletionSourceKind.SESSION_EVENT_LOG
    assert submission.runtime_state["mode"] == "cursor_pane"
    assert submission.runtime_state["prompt_sent"] is True
    assert backend.sent[0][0] == "%9"
    assert "CCB_REQ_ID: job_cursor_pane_1" in backend.sent[0][1]
    assert "CCB_DONE" not in backend.sent[0][1]

    subagent = _cursor_transcript(home).parent / "subagents" / "child.jsonl"
    _append_cursor_records(
        subagent,
        {"role": "user", "message": {"content": [{"type": "text", "text": backend.sent[0][1]}]}},
        {"role": "assistant", "message": {"content": [{"type": "text", "text": "child reply"}]}},
        {"type": "turn_ended", "status": "success"},
    )
    transcript = _cursor_transcript(home)
    _append_cursor_records(
        transcript,
        {"role": "user", "message": {"content": [{"type": "text", "text": backend.sent[0][1]}]}},
        {"role": "assistant", "message": {"content": [{"type": "text", "text": "visible "}]}},
        {"role": "assistant", "message": {"content": [{"type": "text", "text": "reply"}]}},
        {"type": "turn_ended", "status": "success"},
    )

    result = adapter.poll(submission, now="2026-08-11T00:00:05Z")

    assert len(backend.sent) == 1
    assert result is not None and result.decision is not None
    assert result.decision.status is CompletionStatus.COMPLETED
    assert result.decision.reason == "cursor_run_stop"
    assert result.decision.reply == "visible reply"
    assert result.decision.diagnostics["transcript_path"] == str(transcript)
    assert [item.kind for item in result.items] == [
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.ASSISTANT_FINAL,
        CompletionItemKind.TURN_BOUNDARY,
    ]


def test_cursor_pane_adapter_assistant_text_does_not_complete_without_turn_ended(
    monkeypatch,
    tmp_path: Path,
) -> None:
    home, backend, _ = _bind_cursor(monkeypatch, tmp_path)
    adapter = CursorPaneExecutionAdapter()
    submission = adapter.start(_pane_job(), context=_pane_context(tmp_path), now="2026-08-11T00:00:00Z")
    transcript = _cursor_transcript(home)
    _append_cursor_records(
        transcript,
        {"role": "user", "message": {"content": [{"type": "text", "text": backend.sent[0][1]}]}},
        {"role": "assistant", "message": {"content": [{"type": "text", "text": "still running"}]}},
    )

    active = adapter.poll(submission, now="2026-08-11T00:00:02Z")

    assert active is not None
    assert active.decision is None
    assert active.submission.reply == "still running"

    _append_cursor_records(transcript, {"type": "turn_ended", "status": "success"})
    finished = adapter.poll(active.submission, now="2026-08-11T00:00:03Z")

    assert finished is not None and finished.decision is not None
    assert finished.decision.status is CompletionStatus.COMPLETED
    assert finished.decision.reply == "still running"


def test_cursor_pane_adapter_finds_anchor_when_cursor_replaces_previous_terminal_record(
    monkeypatch,
    tmp_path: Path,
) -> None:
    home, backend, _ = _bind_cursor(monkeypatch, tmp_path)
    transcript = _cursor_transcript(home)
    manual_records = [
        {"role": "user", "message": {"content": [{"type": "text", "text": "manual"}]}},
        {"role": "assistant", "message": {"content": [{"type": "text", "text": "manual reply"}]}},
        {"type": "turn_ended", "status": "success"},
    ]
    _append_cursor_records(transcript, *manual_records)
    adapter = CursorPaneExecutionAdapter()
    submission = adapter.start(_pane_job(), context=_pane_context(tmp_path), now="2026-08-11T00:00:00Z")

    transcript.write_text(
        "".join(json.dumps(record, ensure_ascii=True) + "\n" for record in manual_records[:-1]),
        encoding="utf-8",
    )
    _append_cursor_records(
        transcript,
        {"role": "user", "message": {"content": [{"type": "text", "text": backend.sent[0][1]}]}},
        {"role": "assistant", "message": {"content": [{"type": "text", "text": "rewritten reply"}]}},
        {"type": "turn_ended", "status": "success"},
    )

    result = adapter.poll(submission, now="2026-08-11T00:00:03Z")

    assert result is not None and result.decision is not None
    assert result.decision.status is CompletionStatus.COMPLETED
    assert result.decision.reply == "rewritten reply"


def test_cursor_pane_adapter_error_turn_fails_closed(monkeypatch, tmp_path: Path) -> None:
    home, backend, _ = _bind_cursor(monkeypatch, tmp_path)
    adapter = CursorPaneExecutionAdapter()
    submission = adapter.start(_pane_job(), context=_pane_context(tmp_path), now="2026-08-11T00:00:00Z")
    transcript = _cursor_transcript(home)
    _append_cursor_records(
        transcript,
        {"role": "user", "message": {"content": [{"type": "text", "text": backend.sent[0][1]}]}},
        {"role": "assistant", "message": {"content": [{"type": "text", "text": "partial reply"}]}},
        {"type": "turn_ended", "status": "error"},
    )

    result = adapter.poll(submission, now="2026-08-11T00:00:04Z")

    assert result is not None and result.decision is not None
    assert result.decision.status is CompletionStatus.INCOMPLETE
    assert result.decision.reason == "cursor_run_finished:error"
    assert result.decision.reply == "partial reply"


def test_cursor_reply_delivery_sends_raw_body_and_completes_on_dispatch(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _, backend, _ = _bind_cursor(monkeypatch, tmp_path)
    adapter = CursorPaneExecutionAdapter()

    submission = adapter.start(
        _pane_job(message_type="reply_delivery", no_wrap=True),
        context=_pane_context(tmp_path),
        now="2026-08-11T00:00:00Z",
    )
    result = adapter.poll(submission, now="2026-08-11T00:00:01Z")

    assert backend.sent == [("%9", "visible request")]
    assert result is not None and result.decision is not None
    assert result.decision.status is CompletionStatus.COMPLETED
    assert result.decision.reason == "reply_delivery_sent"


def test_cursor_pane_restore_requires_resubmission() -> None:
    diagnostics = CursorPaneExecutionAdapter().restore_diagnostics()

    assert diagnostics["resume_supported"] is False
    assert diagnostics["restore_mode"] == "resubmit_required"


def test_cursor_busy_pane_defers_then_dispatches_exactly_once_when_idle(
    monkeypatch,
    tmp_path: Path,
) -> None:
    home, backend, _ = _bind_cursor(monkeypatch, tmp_path)
    manual = _cursor_transcript(home, "manual-session")
    _append_cursor_records(
        manual,
        {"role": "user", "message": {"content": [{"type": "text", "text": "manual work"}]}},
    )
    adapter = CursorPaneExecutionAdapter()

    submission = adapter.start(
        _pane_job(),
        context=_pane_context(tmp_path),
        now="2026-08-11T00:00:00Z",
    )

    assert submission.runtime_state["prompt_sent"] is False
    assert submission.runtime_state["started_at"] == ""
    assert backend.sent == []

    waiting = adapter.poll(submission, now="2026-08-11T00:00:05Z")

    assert waiting is not None and waiting.decision is None
    assert waiting.submission.runtime_state["prompt_sent"] is False
    assert backend.sent == []

    _append_cursor_records(manual, {"type": "turn_ended", "status": "success"})
    idle_observed = adapter.poll(waiting.submission, now="2026-08-11T00:00:06Z")

    assert idle_observed is not None and idle_observed.decision is None
    assert idle_observed.submission.runtime_state["prompt_sent"] is False
    assert backend.sent == []

    dispatched = adapter.poll(idle_observed.submission, now="2026-08-11T00:00:09Z")

    assert dispatched is not None and dispatched.decision is None
    assert dispatched.submission.runtime_state["prompt_sent"] is True
    assert dispatched.submission.runtime_state["started_at"] == "2026-08-11T00:00:09Z"
    assert len(backend.sent) == 1

    assert adapter.poll(dispatched.submission, now="2026-08-11T00:00:10Z") is None
    assert len(backend.sent) == 1


def test_cursor_working_pane_defers_even_before_user_transcript_is_flushed(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _, backend, _ = _bind_cursor(monkeypatch, tmp_path)
    backend.pane_text = "⠘⠤ Working\n→ Add a follow-up                    ctrl+c to stop"
    adapter = CursorPaneExecutionAdapter()

    submission = adapter.start(
        _pane_job(),
        context=_pane_context(tmp_path),
        now="2026-08-11T00:00:00Z",
    )

    assert submission.runtime_state["prompt_sent"] is False
    assert submission.runtime_state["pane_busy"] is True
    assert backend.sent == []

    waiting = adapter.poll(submission, now="2026-08-11T00:00:01Z")
    assert waiting is not None and waiting.decision is None
    assert waiting.submission.runtime_state["prompt_sent"] is False
    assert backend.sent == []

    backend.pane_text = "→ Add a follow-up\nGPT-5.6 Sol 1M"
    transient_idle = adapter.poll(waiting.submission, now="2026-08-11T00:00:02Z")

    assert transient_idle is not None and transient_idle.decision is None
    assert transient_idle.submission.runtime_state["prompt_sent"] is False
    assert backend.sent == []

    still_waiting = adapter.poll(transient_idle.submission, now="2026-08-11T00:00:07Z")
    assert still_waiting is not None and still_waiting.decision is None
    assert still_waiting.submission.runtime_state["prompt_sent"] is False
    assert still_waiting.submission.runtime_state["deferred_terminal_seen"] is False
    assert backend.sent == []

    manual = _cursor_transcript(tmp_path / "managed-home", "manual-session")
    _append_cursor_records(
        manual,
        {"role": "user", "message": {"content": [{"type": "text", "text": "manual work"}]}},
        {"role": "assistant", "message": {"content": [{"type": "text", "text": "manual done"}]}},
        {"type": "turn_ended", "status": "success"},
    )
    terminal_observed = adapter.poll(still_waiting.submission, now="2026-08-11T00:00:08Z")
    assert terminal_observed is not None and terminal_observed.decision is None
    assert terminal_observed.submission.runtime_state["deferred_terminal_seen"] is True
    assert terminal_observed.submission.runtime_state["prompt_sent"] is False

    dispatched = adapter.poll(terminal_observed.submission, now="2026-08-11T00:00:11Z")

    assert dispatched is not None and dispatched.decision is None
    assert dispatched.submission.runtime_state["prompt_sent"] is True
    assert dispatched.submission.runtime_state["pane_busy"] is False
    assert len(backend.sent) == 1


def test_cursor_pane_status_ignores_busy_markers_left_only_in_scrollback(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _, backend, _ = _bind_cursor(monkeypatch, tmp_path)
    backend.pane_text = "ctrl+c to stop\n" + "\n".join(
        f"idle screen line {index}" for index in range(20)
    )

    submission = CursorPaneExecutionAdapter().start(
        _pane_job(),
        context=_pane_context(tmp_path),
        now="2026-08-11T00:00:00Z",
    )

    assert submission.runtime_state["pane_status"] == "idle"
    assert submission.runtime_state["prompt_sent"] is True
    assert len(backend.sent) == 1


def test_cursor_busy_pane_ready_timeout_never_sends(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CCB_CURSOR_READY_TIMEOUT_S", "2")
    home, backend, _ = _bind_cursor(monkeypatch, tmp_path)
    manual = _cursor_transcript(home, "manual-session")
    _append_cursor_records(
        manual,
        {"role": "user", "message": {"content": [{"type": "text", "text": "manual work"}]}},
    )
    adapter = CursorPaneExecutionAdapter()
    submission = adapter.start(_pane_job(), context=_pane_context(tmp_path), now="2026-08-11T00:00:00Z")

    result = adapter.poll(submission, now="2026-08-11T00:00:03Z")

    assert result is not None and result.decision is not None
    assert result.decision.status is CompletionStatus.INCOMPLETE
    assert result.decision.reason == "cursor_input_not_ready"
    assert result.decision.diagnostics["prompt_sent"] is False
    assert backend.sent == []


def test_cursor_run_timeout_preserves_observed_reply_without_resending(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("CCB_CURSOR_RUN_TIMEOUT_S", "2")
    home, backend, _ = _bind_cursor(monkeypatch, tmp_path)
    adapter = CursorPaneExecutionAdapter()
    submission = adapter.start(_pane_job(), context=_pane_context(tmp_path), now="2026-08-11T00:00:00Z")
    transcript = _cursor_transcript(home)
    _append_cursor_records(
        transcript,
        {"role": "user", "message": {"content": [{"type": "text", "text": backend.sent[0][1]}]}},
        {"role": "assistant", "message": {"content": [{"type": "text", "text": "partial reply"}]}},
    )
    active = adapter.poll(submission, now="2026-08-11T00:00:01Z")
    assert active is not None and active.decision is None

    result = adapter.poll(active.submission, now="2026-08-11T00:00:03Z")

    assert result is not None and result.decision is not None
    assert result.decision.status is CompletionStatus.INCOMPLETE
    assert result.decision.reason == "cursor_run_timeout"
    assert result.decision.reply == "partial reply"
    assert result.decision.anchor_seen is True
    assert len(backend.sent) == 1


def test_cursor_timeout_configuration_requires_positive_finite_values(monkeypatch) -> None:
    monkeypatch.setenv("CCB_CURSOR_READY_TIMEOUT_S", "nan")
    monkeypatch.setenv("CCB_CURSOR_RUN_TIMEOUT_S", "inf")

    assert pane_execution._effective_ready_timeout_s() == pane_execution._DEFAULT_READY_TIMEOUT_S
    assert pane_execution._effective_run_timeout_s() == pane_execution._DEFAULT_RUN_TIMEOUT_S

    monkeypatch.setenv("CCB_CURSOR_READY_TIMEOUT_S", "2.5")
    monkeypatch.setenv("CCB_CURSOR_RUN_TIMEOUT_S", "7")

    assert pane_execution._effective_ready_timeout_s() == 2.5
    assert pane_execution._effective_run_timeout_s() == 7.0


def test_cursor_dead_pane_fails_promptly(monkeypatch, tmp_path: Path) -> None:
    _, backend, _ = _bind_cursor(monkeypatch, tmp_path)
    adapter = CursorPaneExecutionAdapter()
    submission = adapter.start(_pane_job(), context=_pane_context(tmp_path), now="2026-08-11T00:00:00Z")
    backend.alive = False

    result = adapter.poll(submission, now="2026-08-11T00:00:01Z")

    assert result is not None and result.decision is not None
    assert result.decision.status is CompletionStatus.FAILED
    assert result.decision.reason == "pane_dead"


def test_cursor_cancel_interrupts_only_after_prompt_delivery(monkeypatch, tmp_path: Path) -> None:
    home, backend, _ = _bind_cursor(monkeypatch, tmp_path)
    manual = _cursor_transcript(home, "manual-session")
    _append_cursor_records(
        manual,
        {"role": "user", "message": {"content": [{"type": "text", "text": "manual work"}]}},
    )
    adapter = CursorPaneExecutionAdapter()
    deferred = adapter.start(_pane_job(), context=_pane_context(tmp_path), now="2026-08-11T00:00:00Z")

    adapter.cancel(deferred)

    assert backend.keys == []

    _append_cursor_records(manual, {"type": "turn_ended", "status": "success"})
    idle_observed = adapter.poll(deferred, now="2026-08-11T00:00:01Z")
    assert idle_observed is not None
    dispatched = adapter.poll(idle_observed.submission, now="2026-08-11T00:00:04Z")
    assert dispatched is not None and dispatched.submission.runtime_state["prompt_sent"] is True

    adapter.cancel(dispatched.submission)

    assert backend.keys == [("%9", "C-c"), ("%9", "Escape"), ("%9", "C-u")]
