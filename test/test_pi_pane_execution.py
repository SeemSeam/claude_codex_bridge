from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from completion.models import (
    CompletionItemKind,
    CompletionSourceKind,
    CompletionStatus,
)
from provider_backends.pi import pane_execution
from provider_backends.pi.execution import (
    PI_EXECUTION_MODE_ENV,
    PiExecutionAdapter,
)
from provider_backends.pi.pane_events import (
    inspect_pi_runtime,
    read_pi_events,
)
from provider_backends.pi.pane_execution import (
    PI_PANE_MODE,
    PiPaneExecutionAdapter,
)
from provider_execution.base import ProviderRuntimeContext, ProviderSubmission

ACTOR = "pi1"
LAUNCH_ID = "launch-pi-1"
INSTANCE_ID = "runtime-pi-1"
NOW = "2026-07-29T00:00:00Z"


class _FakeSession:
    def __init__(self, data: dict[str, object]) -> None:
        self.data = data
        session_file = str(data.get("session_file") or "").strip()
        self.session_file = Path(session_file) if session_file else ""

    def ensure_pane(self) -> tuple[bool, str]:
        return True, "%9"


class _FakeBackend:
    def __init__(self) -> None:
        self.sent: list[tuple[str, str]] = []
        self.keys: list[tuple[str, str]] = []
        self.alive = True

    def send_text_to_pane(self, pane_id: str, text: str) -> None:
        self.sent.append((pane_id, text))

    def is_tmux_pane_alive(self, pane_id: str) -> bool:
        return self.alive and pane_id == "%9"

    def send_key(self, pane_id: str, key: str) -> None:
        self.keys.append((pane_id, key))


def _job(
    *,
    job_id: str = "job_pi_visible_1",
    body: str = "Inspect this",
    message_type: str = "ask",
    no_wrap: bool = False,
):
    return SimpleNamespace(
        job_id=job_id,
        agent_name=ACTOR,
        provider="pi",
        provider_instance=None,
        provider_options={"no_wrap": True} if no_wrap else {},
        workspace_path=None,
        request=SimpleNamespace(
            body=body,
            message_type=message_type,
            task_id=None,
        ),
    )


def _context(tmp_path: Path) -> ProviderRuntimeContext:
    return ProviderRuntimeContext(
        agent_name=ACTOR,
        workspace_path=str(tmp_path),
        backend_type="pane-backed",
        runtime_ref="%9",
        session_ref=str(tmp_path / ".ccb" / ".pi-pi1-session"),
    )


def _runtime(tmp_path: Path) -> tuple[dict[str, object], Path, Path]:
    runtime_dir = tmp_path / ".ccb" / "agents" / ACTOR / "provider-runtime" / "pi"
    completion_dir = runtime_dir / "completion"
    completion_dir.mkdir(parents=True)
    events = completion_dir / "pi-pane.events.jsonl"
    dispatch = completion_dir / "pi-pane.dispatch.jsonl"
    events.touch()
    dispatch.touch()
    return (
        {
            "agent_name": ACTOR,
            "runtime_dir": str(runtime_dir),
            "ccb_session_id": LAUNCH_ID,
            "pi_session_id": LAUNCH_ID,
            "pi_completion_event_log": str(events),
            "pi_dispatch_event_log": str(dispatch),
        },
        events,
        dispatch,
    )


def _event(
    event_type: str,
    *,
    req_id: str = "",
    actor: str = ACTOR,
    launch_session_id: str = LAUNCH_ID,
    runtime_instance_id: str = INSTANCE_ID,
    **extra,
) -> dict:
    return {
        "schema_version": 1,
        "type": event_type,
        "actor": actor,
        "launch_session_id": launch_session_id,
        "runtime_instance_id": runtime_instance_id,
        "timestamp": "2026-07-29T00:00:01Z",
        "req_id": req_id,
        **extra,
    }


def _assistant(
    text: str,
    *,
    stop_reason: str = "stop",
    error: str = "",
    response_id: str = "response-final",
) -> dict:
    return {
        "text": text,
        "stop_reason": stop_reason,
        "error": error,
        "response_id": response_id,
        "timestamp": 123,
    }


def _append(path: Path, *events: dict) -> None:
    with path.open("a", encoding="utf-8") as stream:
        for event in events:
            stream.write(json.dumps(event, ensure_ascii=True) + "\n")


def _bind(
    monkeypatch: pytest.MonkeyPatch,
    *,
    session: _FakeSession,
    backend: _FakeBackend,
) -> None:
    monkeypatch.setattr(
        pane_execution,
        "_load_session",
        lambda work_dir, agent_name: session,
    )
    monkeypatch.setattr(
        pane_execution,
        "get_backend_for_session",
        lambda data: backend,
    )


def _start_ready(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    job=None,
) -> tuple[PiPaneExecutionAdapter, ProviderSubmission, _FakeBackend, Path, Path]:
    data, events, dispatch = _runtime(tmp_path)
    _append(events, _event("extension_ready"))
    backend = _FakeBackend()
    _bind(monkeypatch, session=_FakeSession(data), backend=backend)
    adapter = PiPaneExecutionAdapter()
    submission = adapter.start(
        job or _job(),
        context=_context(tmp_path),
        now=NOW,
    )
    return adapter, submission, backend, events, dispatch


def test_pi_visible_pane_uses_final_settled_reply_after_tool_process_text(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    adapter, submission, backend, events, dispatch = _start_ready(
        monkeypatch,
        tmp_path,
    )
    req_id = submission.job_id

    assert submission.source_kind is CompletionSourceKind.SESSION_EVENT_LOG
    assert submission.runtime_state["mode"] == PI_PANE_MODE
    assert submission.runtime_state["prompt_sent"] is True
    assert backend.sent[0][0] == "%9"
    assert f"CCB_REQ_ID: {req_id}" in backend.sent[0][1]
    dispatch_record = json.loads(dispatch.read_text(encoding="utf-8"))
    assert dispatch_record["req_id"] == req_id
    assert len(dispatch_record["dispatch_id"]) == 32
    assert dispatch_record["actor"] == ACTOR
    assert dispatch_record["launch_session_id"] == LAUNCH_ID

    _append(
        events,
        _event(
            "request_start",
            req_id=req_id,
            dispatch_matched=True,
            anchor_req_id=req_id,
        ),
        _event("agent_start", req_id=req_id),
        _event(
            "assistant_message",
            req_id=req_id,
            assistant=_assistant(
                "Let me inspect...",
                stop_reason="tool_use",
                response_id="response-process",
            ),
        ),
        _event(
            "tool_start",
            req_id=req_id,
            tool_call_id="tool-1",
            tool_name="read",
        ),
        _event(
            "tool_end",
            req_id=req_id,
            tool_call_id="tool-1",
            tool_name="read",
            is_error=False,
        ),
        _event(
            "turn_end",
            req_id=req_id,
            assistant=_assistant(
                "Let me inspect...",
                stop_reason="tool_use",
                response_id="response-process",
            ),
        ),
        _event(
            "agent_end",
            req_id=req_id,
            assistant=_assistant(
                "Let me inspect...",
                stop_reason="tool_use",
                response_id="response-process",
            ),
        ),
        _event("agent_start", req_id=req_id),
        _event(
            "assistant_message",
            req_id=req_id,
            assistant=_assistant("FINAL_OK"),
        ),
        _event("agent_end", req_id=req_id, assistant=_assistant("FINAL_OK")),
        _event(
            "agent_settled",
            req_id=req_id,
            assistant=_assistant("FINAL_OK"),
        ),
    )

    result = adapter.poll(submission, now="2026-07-29T00:00:05Z")

    assert result is not None and result.decision is not None
    assert result.decision.status is CompletionStatus.COMPLETED
    assert result.decision.reason == "pi_run_stop"
    assert result.decision.reply == "FINAL_OK"
    assert "Let me inspect" not in result.decision.reply
    assert [item.kind for item in result.items] == [
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.TOOL_CALL,
        CompletionItemKind.TOOL_RESULT,
        CompletionItemKind.ASSISTANT_FINAL,
        CompletionItemKind.TURN_BOUNDARY,
    ]


def test_pi_stop_message_is_progress_until_agent_settled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    adapter, submission, _, events, _ = _start_ready(monkeypatch, tmp_path)
    req_id = submission.job_id
    _append(
        events,
        _event("request_start", req_id=req_id, dispatch_matched=True),
        _event(
            "assistant_message",
            req_id=req_id,
            assistant=_assistant("not terminal yet"),
        ),
        _event("agent_end", req_id=req_id, assistant=_assistant("not terminal yet")),
    )

    progress = adapter.poll(submission, now="2026-07-29T00:00:02Z")

    assert progress is not None
    assert progress.decision is None
    assert progress.submission.reply == ""
    assert progress.submission.runtime_state["last_assistant_message"] == (
        "not terminal yet"
    )

    _append(
        events,
        _event(
            "agent_settled",
            req_id=req_id,
            assistant=_assistant("true final"),
        ),
    )
    terminal = adapter.poll(
        progress.submission,
        now="2026-07-29T00:00:03Z",
    )
    assert terminal is not None and terminal.decision is not None
    assert terminal.decision.reply == "true final"


@pytest.mark.parametrize(
    ("text", "stop_reason", "error", "status", "reason"),
    (
        ("OK", "stop", "", CompletionStatus.COMPLETED, "pi_run_stop"),
        (
            "partial",
            "error",
            "stalled mid-stream",
            CompletionStatus.FAILED,
            "pi_run_error",
        ),
        (
            "partial",
            "aborted",
            "",
            CompletionStatus.INCOMPLETE,
            "pi_run_finished:aborted",
        ),
        (
            "partial",
            "length",
            "",
            CompletionStatus.INCOMPLETE,
            "pi_run_finished:length",
        ),
        (
            "partial",
            "",
            "",
            CompletionStatus.INCOMPLETE,
            "pi_native_outcome_missing",
        ),
        ("", "stop", "", CompletionStatus.INCOMPLETE, "pi_empty_reply"),
    ),
)
def test_pi_settled_outcome_mapping(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    text: str,
    stop_reason: str,
    error: str,
    status: CompletionStatus,
    reason: str,
) -> None:
    adapter, submission, _, events, _ = _start_ready(monkeypatch, tmp_path)
    snapshot = _assistant(text, stop_reason=stop_reason, error=error)
    _append(
        events,
        _event(
            "request_start",
            req_id=submission.job_id,
            dispatch_matched=True,
        ),
        _event(
            "agent_settled",
            req_id=submission.job_id,
            assistant=snapshot,
        ),
    )

    result = adapter.poll(submission, now="2026-07-29T00:00:02Z")

    assert result is not None and result.decision is not None
    assert result.decision.status is status
    assert result.decision.reason == reason
    assert result.decision.diagnostics["terminal_authority"] == (
        "pi_extension_agent_settled"
    )


def test_pi_partial_trailing_event_waits_then_completes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    adapter, submission, _, events, _ = _start_ready(monkeypatch, tmp_path)
    req_id = submission.job_id
    complete_event = json.dumps(
        _event(
            "request_start",
            req_id=req_id,
            dispatch_matched=True,
        )
    )
    with events.open("a", encoding="utf-8") as stream:
        stream.write(complete_event[:25])

    pending = adapter.poll(submission, now="2026-07-29T00:00:01Z")

    assert pending is not None
    assert pending.decision is None
    assert pending.submission.runtime_state["event_trailing_partial"] is True

    with events.open("a", encoding="utf-8") as stream:
        stream.write(complete_event[25:] + "\n")
    _append(
        events,
        _event(
            "agent_settled",
            req_id=req_id,
            assistant=_assistant("SHORT"),
        ),
    )
    terminal = adapter.poll(
        pending.submission,
        now="2026-07-29T00:00:02Z",
    )
    assert terminal is not None and terminal.decision is not None
    assert terminal.decision.reply == "SHORT"


def test_pi_malformed_complete_event_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    adapter, submission, _, events, _ = _start_ready(monkeypatch, tmp_path)
    with events.open("a", encoding="utf-8") as stream:
        stream.write("not-json\n")

    result = adapter.poll(submission, now="2026-07-29T00:00:01Z")

    assert result is not None and result.decision is not None
    assert result.decision.status is CompletionStatus.INCOMPLETE
    assert result.decision.reason == "pi_native_protocol_invalid"
    assert result.decision.diagnostics["protocol_error"].startswith(
        "invalid_jsonl_record:"
    )


@pytest.mark.parametrize(
    "event",
    (
        _event(
            "binding_error",
            req_id="job_pi_visible_1",
            dispatch_req_id="job_pi_visible_1",
            anchor_req_id="job_other",
        ),
        _event(
            "request_start",
            req_id="",
            dispatch_matched=False,
            anchor_req_id="job_pi_visible_1",
        ),
        _event(
            "request_start",
            req_id="job_other",
            dispatch_matched=True,
            anchor_req_id="job_pi_visible_1",
        ),
    ),
)
def test_pi_request_binding_failures_terminalize_incomplete(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    event: dict,
) -> None:
    adapter, submission, _, events, _ = _start_ready(monkeypatch, tmp_path)
    _append(events, event)

    result = adapter.poll(submission, now="2026-07-29T00:00:01Z")

    assert result is not None and result.decision is not None
    assert result.decision.status is CompletionStatus.INCOMPLETE
    assert result.decision.reason == "pi_request_binding_invalid"


def test_pi_old_and_foreign_completions_cannot_satisfy_new_job(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data, events, _ = _runtime(tmp_path)
    _append(
        events,
        _event("extension_ready"),
        _event(
            "request_start",
            req_id="job_old",
            dispatch_matched=True,
        ),
        _event(
            "agent_settled",
            req_id="job_old",
            assistant=_assistant("OLD"),
        ),
    )
    backend = _FakeBackend()
    _bind(monkeypatch, session=_FakeSession(data), backend=backend)
    adapter = PiPaneExecutionAdapter()
    submission = adapter.start(
        _job(job_id="job_new"),
        context=_context(tmp_path),
        now=NOW,
    )
    _append(
        events,
        _event(
            "request_start",
            req_id="job_new",
            actor="other",
            dispatch_matched=True,
        ),
        _event(
            "agent_settled",
            req_id="job_new",
            actor="other",
            assistant=_assistant("FOREIGN_ACTOR"),
        ),
        _event(
            "request_start",
            req_id="job_new",
            launch_session_id="other-launch",
            dispatch_matched=True,
        ),
        _event(
            "agent_settled",
            req_id="job_new",
            launch_session_id="other-launch",
            assistant=_assistant("FOREIGN_SESSION"),
        ),
    )

    ignored = adapter.poll(submission, now="2026-07-29T00:00:01Z")

    assert ignored is not None
    assert ignored.decision is None
    assert ignored.submission.reply == ""

    _append(
        events,
        _event("request_start", req_id="job_new", dispatch_matched=True),
        _event(
            "agent_settled",
            req_id="job_new",
            assistant=_assistant("NEW"),
        ),
    )
    terminal = adapter.poll(
        ignored.submission,
        now="2026-07-29T00:00:02Z",
    )
    assert terminal is not None and terminal.decision is not None
    assert terminal.decision.reply == "NEW"


def test_pi_runtime_instance_change_does_not_rebind_inflight_job(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    adapter, submission, _, events, _ = _start_ready(monkeypatch, tmp_path)
    _append(
        events,
        _event("extension_ready", runtime_instance_id="runtime-pi-2"),
    )

    result = adapter.poll(submission, now="2026-07-29T00:00:01Z")

    assert result is not None and result.decision is not None
    assert result.decision.status is CompletionStatus.INCOMPLETE
    assert result.decision.reason == "pi_runtime_restarted"


def test_pi_unmanaged_input_supersedes_inflight_job(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    adapter, submission, _, events, _ = _start_ready(monkeypatch, tmp_path)
    req_id = submission.job_id
    _append(
        events,
        _event("request_start", req_id=req_id, dispatch_matched=True),
        _event(
            "request_superseded",
            req_id=req_id,
            superseded_by="unmanaged_input",
            input_source="interactive",
        ),
        _event(
            "agent_settled",
            req_id=req_id,
            assistant=_assistant("MUST_NOT_BE_RETURNED"),
        ),
    )

    result = adapter.poll(submission, now="2026-07-29T00:00:01Z")

    assert result is not None and result.decision is not None
    assert result.decision.status is CompletionStatus.INCOMPLETE
    assert result.decision.reason == "pi_request_superseded"
    assert result.decision.reply == ""
    assert result.decision.diagnostics["superseded_by"] == "unmanaged_input"


def test_pi_busy_pane_defers_without_sending_then_dispatches_when_idle(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data, events, dispatch = _runtime(tmp_path)
    _append(
        events,
        _event("extension_ready"),
        _event("agent_start"),
    )
    backend = _FakeBackend()
    _bind(monkeypatch, session=_FakeSession(data), backend=backend)
    adapter = PiPaneExecutionAdapter()

    submission = adapter.start(
        _job(),
        context=_context(tmp_path),
        now=NOW,
    )

    assert submission.runtime_state["prompt_sent"] is False
    assert backend.sent == []
    assert dispatch.read_text(encoding="utf-8") == ""

    _append(events, _event("agent_settled"))
    dispatched = adapter.poll(submission, now="2026-07-29T00:00:05Z")

    assert dispatched is not None
    assert dispatched.decision is None
    assert dispatched.submission.runtime_state["prompt_sent"] is True
    assert len(backend.sent) == 1


def test_pi_extension_readiness_timeout_never_sends_prompt(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data, _, dispatch = _runtime(tmp_path)
    backend = _FakeBackend()
    _bind(monkeypatch, session=_FakeSession(data), backend=backend)
    adapter = PiPaneExecutionAdapter()
    submission = adapter.start(
        _job(),
        context=_context(tmp_path),
        now=NOW,
    )

    result = adapter.poll(submission, now="2026-07-29T00:00:31Z")

    assert result is not None and result.decision is not None
    assert result.decision.status is CompletionStatus.INCOMPLETE
    assert result.decision.reason == "pi_completion_extension_not_ready"
    assert result.decision.diagnostics["prompt_sent"] is False
    assert backend.sent == []
    assert dispatch.read_text(encoding="utf-8") == ""


def test_pi_reply_delivery_dispatches_and_holds_until_agent_settled(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    job = _job(
        body="raw delivery",
        message_type="reply_delivery",
    )
    adapter, submission, backend, events, _ = _start_ready(
        monkeypatch,
        tmp_path,
        job=job,
    )
    req_id = submission.job_id

    held = adapter.poll(submission, now="2026-07-29T00:00:01Z")

    assert backend.sent[0][0] == "%9"
    assert f"CCB_REQ_ID: {req_id}" in backend.sent[0][1]
    # Sending is transport only: the delivery holds until the anchored
    # agent_settled turn end.
    assert held is None or held.decision is None

    _append(
        events,
        _event(
            "request_start",
            req_id=req_id,
            dispatch_matched=True,
            anchor_req_id=req_id,
        ),
        _event(
            "agent_settled",
            req_id=req_id,
            assistant=_assistant("DELIVERED_ACK"),
        ),
    )

    settled = adapter.poll(
        held.submission if held is not None else submission,
        now="2026-07-29T00:00:05Z",
    )

    assert settled is not None and settled.decision is not None
    assert settled.decision.status is CompletionStatus.COMPLETED
    assert settled.decision.reason == "pi_run_stop"


def test_pi_visible_prompt_preserves_compact_reply_mode_without_static_guidance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    job = _job(
        body=(
            "Inspect this.\n\n"
            "CCB_REPLY_MODE: compact"
        )
    )
    _, _, backend, _, _ = _start_ready(
        monkeypatch,
        tmp_path,
        job=job,
    )

    assert backend.sent[0][1].count("CCB_REPLY_MODE: compact") == 1
    assert "CCB reply guidance:" not in backend.sent[0][1]
    assert backend.sent[0][1].startswith(
        "CCB_REQ_ID: job_pi_visible_1\n\n"
    )


def test_pi_cancel_interrupts_but_does_not_kill_managed_pane(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    adapter, submission, backend, _, _ = _start_ready(monkeypatch, tmp_path)

    adapter.cancel(submission)

    assert backend.keys == [
        ("%9", "C-c"),
        ("%9", "Escape"),
        ("%9", "C-u"),
    ]
    assert backend.alive is True


def test_pi_export_and_resume_rebind_exact_live_extension_instance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    adapter, submission, backend, events, _ = _start_ready(monkeypatch, tmp_path)
    exported = adapter.export_runtime_state(submission)
    persisted = replace(submission, runtime_state=exported)

    assert "backend" not in exported
    assert "pending_prompt" not in exported

    resumed = adapter.resume(
        _job(),
        persisted,
        context=_context(tmp_path),
        persisted_state=exported,
        now="2026-07-29T00:00:01Z",
    )

    assert resumed is not None
    assert resumed.runtime_state["backend"] is backend
    assert resumed.runtime_state["pane_id"] == "%9"

    _append(
        events,
        _event("extension_ready", runtime_instance_id="runtime-pi-new"),
    )
    stale = adapter.resume(
        _job(),
        persisted,
        context=_context(tmp_path),
        persisted_state=exported,
        now="2026-07-29T00:00:02Z",
    )
    assert stale is None


def test_pi_event_reader_keeps_partial_tail_at_previous_offset(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    _append(path, _event("extension_ready"))
    first = read_pi_events(path)
    with path.open("a", encoding="utf-8") as stream:
        stream.write('{"schema_version":1')

    second = read_pi_events(path, first.next_offset)

    assert second.events == ()
    assert second.next_offset == first.next_offset
    assert second.trailing_partial is True
    assert second.protocol_error == ""


def test_pi_runtime_observation_tracks_foreign_busy_state(
    tmp_path: Path,
) -> None:
    path = tmp_path / "events.jsonl"
    _append(path, _event("extension_ready"), _event("agent_start"))

    busy = inspect_pi_runtime(path, actor=ACTOR, launch_session_id=LAUNCH_ID)

    assert busy.ready is True
    assert busy.busy is True
    assert busy.runtime_instance_id == INSTANCE_ID

    _append(path, _event("agent_settled"))
    idle = inspect_pi_runtime(path, actor=ACTOR, launch_session_id=LAUNCH_ID)
    assert idle.busy is False


def test_pi_initial_ready_observation_persists_native_session_binding(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    data, events, _ = _runtime(tmp_path)
    session_dir = tmp_path / "state" / "sessions"
    native_id = "019fdd2b-8362-7958-9e85-d0a5eed17084"
    native_path = session_dir / f"2026-07-29T00-00-00-000Z_{native_id}.jsonl"
    session_dir.mkdir(parents=True)
    native_path.write_text(
        json.dumps({"type": "session", "id": native_id, "cwd": str(tmp_path)}) + "\n",
        encoding="utf-8",
    )
    session_file = tmp_path / ".ccb" / ".pi-pi1-session"
    session_file.parent.mkdir(parents=True, exist_ok=True)
    session_file.write_text(
        json.dumps(
            {
                "ccb_session_id": LAUNCH_ID,
                "agent_name": ACTOR,
                "ccb_project_id": "project-1",
                "work_dir": str(tmp_path),
            }
        ),
        encoding="utf-8",
    )
    data.update(
        {
            "session_file": str(session_file),
            "work_dir": str(tmp_path),
            "pi_session_dir": str(session_dir),
        }
    )
    _append(
        events,
        _event(
            "extension_ready",
            pi_session_id=native_id,
            pi_session_path=str(native_path),
        ),
    )
    backend = _FakeBackend()
    _bind(monkeypatch, session=_FakeSession(data), backend=backend)

    submission = PiPaneExecutionAdapter().start(
        _job(),
        context=_context(tmp_path),
        now=NOW,
    )

    assert submission.runtime_state["native_session_id"] == native_id
    assert json.loads(session_file.read_text(encoding="utf-8"))["pi_session_id"] == native_id


class _RoutingAdapter:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.calls: list[str] = []

    def start(self, job, *, context, now):
        self.calls.append("start")
        return ProviderSubmission(
            job_id=job.job_id,
            agent_name=job.agent_name,
            provider="pi",
            accepted_at=now,
            ready_at=now,
            source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
            reply="",
            runtime_state={"mode": self.mode},
        )

    def poll(self, submission, *, now):
        self.calls.append("poll")

    def cancel(self, submission):
        self.calls.append("cancel")

    def export_runtime_state(self, submission):
        self.calls.append("export")
        return dict(submission.runtime_state)

    def resume(self, job, submission, *, context, persisted_state, now):
        self.calls.append("resume")
        return submission


def test_pi_composite_routes_new_mode_and_persisted_legacy_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    composite = PiExecutionAdapter()
    pane = _RoutingAdapter(PI_PANE_MODE)
    headless = _RoutingAdapter("pi_run")
    composite.pane = pane
    composite.headless = headless
    job = _job()

    monkeypatch.delenv(PI_EXECUTION_MODE_ENV, raising=False)
    pane_submission = composite.start(job, context=None, now=NOW)
    assert pane_submission.runtime_state["mode"] == PI_PANE_MODE
    assert pane.calls == ["start"]

    monkeypatch.setenv(PI_EXECUTION_MODE_ENV, "headless")
    headless_submission = composite.start(job, context=None, now=NOW)
    assert headless_submission.runtime_state["mode"] == "pi_run"
    assert headless.calls == ["start"]

    monkeypatch.delenv(PI_EXECUTION_MODE_ENV, raising=False)
    composite.poll(headless_submission, now=NOW)
    composite.cancel(headless_submission)
    composite.export_runtime_state(headless_submission)
    composite.resume(
        job,
        headless_submission,
        context=None,
        persisted_state={"mode": "pi_run"},
        now=NOW,
    )
    assert headless.calls == ["start", "poll", "cancel", "export", "resume"]
    assert pane.calls == ["start"]
