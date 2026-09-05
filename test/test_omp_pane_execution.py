from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from completion.models import CompletionSourceKind, CompletionStatus
from provider_backends.omp.execution import (
    OMP_EXECUTION_MODE_ENV,
    OmpExecutionAdapter,
)
from provider_backends.omp.launcher import _omp_completion_extension_source
from provider_backends.omp.pane_execution import (
    OMP_PANE_MODE,
    OmpPaneExecutionAdapter,
)
from provider_backends.pi import pane_execution
from provider_execution.base import ProviderRuntimeContext, ProviderSubmission

ACTOR = "omp1"
LAUNCH_ID = "launch-omp-1"
INSTANCE_ID = "runtime-omp-1"
NOW = "2026-09-05T00:00:00Z"


class _FakeSession:
    def __init__(self, data: dict[str, object]) -> None:
        self.data = data
        self.session_file = Path(str(data.get("session_file") or ""))

    def ensure_pane(self) -> tuple[bool, str]:
        return True, "%8"


class _FakeBackend:
    def __init__(self, *, fail_send: bool = False) -> None:
        self.sent: list[tuple[str, str]] = []
        self.keys: list[tuple[str, str]] = []
        self.alive = True
        self.fail_send = fail_send

    def send_text_to_pane(self, pane_id: str, text: str) -> None:
        if self.fail_send:
            raise RuntimeError("send failed")
        self.sent.append((pane_id, text))

    def is_tmux_pane_alive(self, pane_id: str) -> bool:
        return self.alive and pane_id == "%8"

    def send_key(self, pane_id: str, key: str) -> None:
        self.keys.append((pane_id, key))


def _job(*, job_id: str = "job_omp_visible_1", body: str = "Reply OMP_OK"):
    return SimpleNamespace(
        job_id=job_id,
        agent_name=ACTOR,
        provider="omp",
        provider_instance=None,
        provider_options={},
        workspace_path=None,
        request=SimpleNamespace(
            body=body,
            message_type="ask",
            task_id=None,
        ),
    )


def _context(tmp_path: Path) -> ProviderRuntimeContext:
    return ProviderRuntimeContext(
        agent_name=ACTOR,
        workspace_path=str(tmp_path),
        backend_type="pane-backed",
        runtime_ref="%8",
        session_ref=str(tmp_path / ".ccb" / ".omp-omp1-session"),
    )


def _runtime(tmp_path: Path) -> tuple[dict[str, object], Path, Path]:
    runtime_dir = tmp_path / ".ccb" / "agents" / ACTOR / "provider-runtime" / "omp"
    completion_dir = runtime_dir / "completion"
    completion_dir.mkdir(parents=True)
    events = completion_dir / "omp-pane.events.jsonl"
    dispatch = completion_dir / "omp-pane.dispatch.jsonl"
    events.touch()
    dispatch.touch()
    return (
        {
            "agent_name": ACTOR,
            "runtime_dir": str(runtime_dir),
            "ccb_session_id": LAUNCH_ID,
            "omp_session_id": LAUNCH_ID,
            "omp_session_dir": str(tmp_path / "omp-sessions"),
            "omp_completion_event_log": str(events),
            "omp_dispatch_event_log": str(dispatch),
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
        "timestamp": "2026-09-05T00:00:01Z",
        "req_id": req_id,
        **extra,
    }


def _assistant(text: str, *, response_id: str = "response-final") -> dict:
    return {
        "text": text,
        "stop_reason": "stop",
        "error": "",
        "response_id": response_id,
        "timestamp": 123,
    }


def _tool_use_assistant(*, response_id: str = "response-tool") -> dict:
    return {
        "text": "",
        "stop_reason": "tool_use",
        "error": "",
        "response_id": response_id,
        "timestamp": 123,
    }


def _append(path: Path, *events: dict) -> None:
    with path.open("a", encoding="utf-8") as stream:
        for event in events:
            stream.write(json.dumps(event, ensure_ascii=True) + "\n")


def _start_ready(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    backend: _FakeBackend | None = None,
) -> tuple[OmpPaneExecutionAdapter, ProviderSubmission, _FakeBackend, Path, Path]:
    data, events, dispatch = _runtime(tmp_path)
    _append(events, _event("extension_ready"))
    active_backend = backend or _FakeBackend()
    monkeypatch.setattr(
        pane_execution,
        "get_backend_for_session",
        lambda data: active_backend,
    )
    adapter = OmpPaneExecutionAdapter()
    adapter.session_loader = lambda work_dir, instance=None: _FakeSession(data)
    submission = adapter.start(_job(), context=_context(tmp_path), now=NOW)
    return adapter, submission, active_backend, events, dispatch


def test_omp_visible_pane_dispatches_exact_prompt_and_waits_for_final_agent_end(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    adapter, submission, backend, events, dispatch = _start_ready(
        monkeypatch,
        tmp_path,
    )
    req_id = submission.job_id

    assert submission.source_kind is CompletionSourceKind.SESSION_EVENT_LOG
    assert submission.runtime_state["mode"] == OMP_PANE_MODE
    assert backend.sent[0][0] == "%8"
    assert backend.sent[0][1].startswith(f"CCB_REQ_ID: {req_id}\n")
    dispatch_record = json.loads(dispatch.read_text(encoding="utf-8"))
    assert dispatch_record["req_id"] == req_id
    assert dispatch_record["actor"] == ACTOR
    assert dispatch_record["launch_session_id"] == LAUNCH_ID
    assert dispatch_record["runtime_instance_id"] == INSTANCE_ID

    _append(
        events,
        _event("request_start", req_id=req_id, dispatch_matched=True),
        _event("agent_end", req_id=req_id, assistant=_assistant("retry result")),
    )
    progress = adapter.poll(submission, now="2026-09-05T00:00:02Z")
    assert progress is not None and progress.decision is None

    _append(
        events,
        _event("agent_start", req_id=req_id),
        _event("assistant_message", req_id=req_id, assistant=_assistant("OMP_OK")),
        _event("agent_end", req_id=req_id, assistant=_assistant("OMP_OK")),
        _event("agent_settled", req_id=req_id, assistant=_assistant("OMP_OK")),
    )
    result = adapter.poll(progress.submission, now="2026-09-05T00:00:03Z")

    assert result is not None and result.decision is not None
    assert result.decision.status is CompletionStatus.COMPLETED
    assert result.decision.reason == "omp_run_stop"
    assert result.decision.reply == "OMP_OK"
    assert result.decision.diagnostics["terminal_authority"] == (
        "omp_extension_agent_end_final"
    )


def test_omp_tool_use_settle_is_progress_until_final_stop(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    adapter, submission, _, events, _ = _start_ready(monkeypatch, tmp_path)
    req_id = submission.job_id
    _append(
        events,
        _event("request_start", req_id=req_id, dispatch_matched=True),
        _event("tool_start", req_id=req_id, tool_name="bash", tool_call_id="call-1"),
        _event("tool_end", req_id=req_id, tool_name="bash", tool_call_id="call-1"),
        _event(
            "agent_end",
            req_id=req_id,
            will_continue=False,
            assistant=_tool_use_assistant(),
        ),
        _event("agent_settled", req_id=req_id, assistant=_tool_use_assistant()),
    )

    progress = adapter.poll(submission, now="2026-09-05T00:00:02Z")

    assert progress is not None
    assert progress.decision is None
    assert progress.submission.runtime_state["anchor_seen"] is True
    _append(
        events,
        _event("assistant_message", req_id=req_id, assistant=_assistant("OMP_FINAL")),
        _event("agent_end", req_id=req_id, assistant=_assistant("OMP_FINAL")),
        _event("agent_settled", req_id=req_id, assistant=_assistant("OMP_FINAL")),
    )

    result = adapter.poll(progress.submission, now="2026-09-05T00:00:03Z")

    assert result is not None and result.decision is not None
    assert result.decision.status is CompletionStatus.COMPLETED
    assert result.decision.reply == "OMP_FINAL"


def test_omp_extension_keeps_tool_use_agent_end_bound_to_active_request() -> None:
    source = _omp_completion_extension_source()

    assert (
        'event?.willContinue === true || latestAssistant?.stop_reason === "tool_use"'
        in source
    )


def test_omp_foreign_runtime_events_cannot_complete_job(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    adapter, submission, _, events, _ = _start_ready(monkeypatch, tmp_path)
    req_id = submission.job_id
    _append(
        events,
        _event("request_start", req_id=req_id, actor="other", dispatch_matched=True),
        _event("agent_settled", req_id=req_id, actor="other", assistant=_assistant("BAD")),
        _event(
            "agent_settled",
            req_id=req_id,
            runtime_instance_id="runtime-other",
            assistant=_assistant("BAD"),
        ),
    )

    pending = adapter.poll(submission, now="2026-09-05T00:00:02Z")

    assert pending is not None and pending.decision is None


def test_omp_malformed_sidecar_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    adapter, submission, _, events, _ = _start_ready(monkeypatch, tmp_path)
    with events.open("a", encoding="utf-8") as stream:
        stream.write("not-json\n")

    result = adapter.poll(submission, now="2026-09-05T00:00:02Z")

    assert result is not None and result.decision is not None
    assert result.decision.status is CompletionStatus.INCOMPLETE
    assert result.decision.reason == "omp_native_protocol_invalid"


def test_omp_send_failure_and_cancel_are_scoped_to_managed_pane(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    adapter, failed, _, _, _ = _start_ready(
        monkeypatch,
        tmp_path / "failed",
        backend=_FakeBackend(fail_send=True),
    )
    assert failed.runtime_state["mode"] == "error"
    assert failed.runtime_state["reason"] == "omp_pane_send_failed"

    adapter, submission, backend, _, _ = _start_ready(
        monkeypatch,
        tmp_path / "cancel",
    )
    adapter.cancel(submission)
    assert backend.keys == [
        ("%8", "C-c"),
        ("%8", "Escape"),
        ("%8", "C-u"),
    ]
    assert backend.alive is True


class _RoutingAdapter:
    def __init__(self, mode: str) -> None:
        self.mode = mode
        self.calls: list[str] = []

    def start(self, job, *, context, now):
        self.calls.append("start")
        return ProviderSubmission(
            job_id=job.job_id,
            agent_name=job.agent_name,
            provider="omp",
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
        return replace(submission)


def test_omp_composite_defaults_to_pane_and_preserves_headless_jobs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    composite = OmpExecutionAdapter()
    pane = _RoutingAdapter(OMP_PANE_MODE)
    headless = _RoutingAdapter("omp_run")
    composite.pane = pane
    composite.headless = headless
    job = _job()

    monkeypatch.delenv(OMP_EXECUTION_MODE_ENV, raising=False)
    assert composite.start(job, context=None, now=NOW).runtime_state["mode"] == (
        OMP_PANE_MODE
    )

    monkeypatch.setenv(OMP_EXECUTION_MODE_ENV, "headless")
    headless_submission = composite.start(job, context=None, now=NOW)
    assert headless_submission.runtime_state["mode"] == "omp_run"

    monkeypatch.delenv(OMP_EXECUTION_MODE_ENV, raising=False)
    composite.poll(headless_submission, now=NOW)
    composite.cancel(headless_submission)
    composite.export_runtime_state(headless_submission)
    composite.resume(
        job,
        headless_submission,
        context=None,
        persisted_state={"mode": "omp_run"},
        now=NOW,
    )
    assert headless.calls == ["start", "poll", "cancel", "export", "resume"]
    assert pane.calls == ["start"]
