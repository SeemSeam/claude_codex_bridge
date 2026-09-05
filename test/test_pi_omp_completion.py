from __future__ import annotations

import json
import sys
import time
from pathlib import Path

import pytest
from ccbd.api_models import DeliveryScope, JobRecord, JobStatus, MessageEnvelope
from completion.models import CompletionSourceKind, CompletionStatus
from provider_backends.omp.execution import (
    build_headless_execution_adapter as build_omp_headless_execution_adapter,
    observe_omp_json_output,
)
from provider_backends.pi.execution import (
    build_headless_execution_adapter,
    observe_pi_json_output,
)
from provider_core.pathing import session_filename_for_agent
from provider_execution.base import ProviderRuntimeContext, ProviderSubmission


def _write_jsonl(path: Path, events: list[dict], *, final_newline: bool = True) -> None:
    text = "\n".join(json.dumps(event, ensure_ascii=True) for event in events)
    if final_newline:
        text += "\n"
    path.write_text(text, encoding="utf-8")


def _assistant(text: str, *, stop_reason: str = "stop", error: str = "") -> dict:
    message = {
        "id": f"msg-{abs(hash((text, stop_reason)))}",
        "role": "assistant",
        "stopReason": stop_reason,
        "content": [{"type": "text", "text": text}] if text else [],
    }
    if error:
        message["errorMessage"] = error
    return message


def _pi_run_events(
    text: str, *, stop_reason: str = "stop", settled: bool = True
) -> list[dict]:
    message = _assistant(text, stop_reason=stop_reason)
    events = [
        {"type": "session", "version": 3, "id": "ses-pi"},
        {"type": "agent_start"},
        {"type": "turn_end", "message": message, "toolResults": []},
        {"type": "agent_end", "messages": [message], "willRetry": False},
    ]
    if settled:
        events.append({"type": "agent_settled"})
    return events


def _omp_run_events(
    text: str,
    *,
    stop_reason: str = "stop",
    is_terminal: bool | None = True,
) -> list[dict]:
    message = _assistant(text, stop_reason=stop_reason)
    agent_end = {"type": "agent_end", "messages": [message]}
    if is_terminal is not None:
        agent_end["isTerminal"] = is_terminal
    return [
        {"type": "session", "version": 3, "id": "ses-omp"},
        {"type": "agent_start"},
        {"type": "turn_end", "message": message, "toolResults": []},
        agent_end,
    ]


def _observer(provider: str):
    return observe_pi_json_output if provider == "pi" else observe_omp_json_output


def _adapter(provider: str):
    if provider == "pi":
        return build_headless_execution_adapter()
    return build_omp_headless_execution_adapter()


def _job(provider: str, work_dir: Path) -> JobRecord:
    agent_name = f"{provider}1"
    return JobRecord(
        job_id=f"job_{provider}_latest_completion",
        submission_id=f"sub_{provider}_latest_completion",
        agent_name=agent_name,
        provider=provider,
        request=MessageEnvelope(
            project_id="project",
            to_agent=agent_name,
            from_actor="main",
            body=f"Reply exactly from {provider}",
            task_id=None,
            reply_to=None,
            message_type="ask",
            delivery_scope=DeliveryScope.SINGLE,
        ),
        status=JobStatus.RUNNING,
        terminal_decision=None,
        cancel_requested_at=None,
        created_at="2026-07-28T00:00:00Z",
        updated_at="2026-07-28T00:00:00Z",
        workspace_path=str(work_dir),
    )


def _runtime_context(provider: str, work_dir: Path) -> ProviderRuntimeContext:
    agent_name = f"{provider}1"
    return ProviderRuntimeContext(
        agent_name=agent_name,
        workspace_path=str(work_dir),
        backend_type="pane-backed",
        runtime_ref="%1",
        session_ref=str(
            work_dir / ".ccb" / session_filename_for_agent(provider, agent_name)
        ),
    )


def _write_session(provider: str, work_dir: Path) -> None:
    agent_name = f"{provider}1"
    runtime_dir = (
        work_dir / ".ccb" / "agents" / agent_name / "provider-runtime" / provider
    )
    state_dir = work_dir / ".ccb" / "agents" / agent_name / "provider-state" / provider
    session_path = work_dir / ".ccb" / session_filename_for_agent(provider, agent_name)
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text(
        json.dumps(
            {
                "active": True,
                "agent_name": agent_name,
                "runtime_dir": str(runtime_dir),
                "completion_artifact_dir": str(runtime_dir / "completion"),
                "work_dir": str(work_dir),
                "pane_id": "%1",
                f"{provider}_state_dir": str(state_dir),
                f"{provider}_home": str(state_dir / "home"),
                f"{provider}_data_dir": str(state_dir / "data"),
            },
            ensure_ascii=True,
        ),
        encoding="utf-8",
    )


def _install_stub(
    monkeypatch: pytest.MonkeyPatch, provider: str, *, mode: str = ""
) -> None:
    stub = Path("test/stubs/provider_stub.py").resolve()
    monkeypatch.setenv(
        f"{provider.upper()}_START_CMD",
        f"{sys.executable} {stub} --provider {provider}",
    )
    if mode:
        monkeypatch.setenv(f"{provider.upper()}_STUB_MODE", mode)
    else:
        monkeypatch.delenv(f"{provider.upper()}_STUB_MODE", raising=False)


def _run_to_terminal(adapter, submission: ProviderSubmission):
    current = submission
    for index in range(250):
        result = adapter.poll(
            current,
            now=f"2026-07-28T00:{index // 60:02d}:{index % 60:02d}Z",
        )
        if result is not None:
            current = result.submission
            if result.decision is not None:
                return result
        time.sleep(0.01)
    raise AssertionError("provider adapter did not terminalize")


def _completed_submission(provider: str, stdout: Path) -> ProviderSubmission:
    return ProviderSubmission(
        job_id=f"job_{provider}_fixture",
        agent_name=f"{provider}1",
        provider=provider,
        accepted_at="2026-07-28T00:00:00Z",
        ready_at="2026-07-28T00:00:00Z",
        source_kind=CompletionSourceKind.STRUCTURED_RESULT_STREAM,
        reply="",
        runtime_state={
            "mode": f"{provider}_run",
            "provider": provider,
            "job_id": f"job_{provider}_fixture",
            "request_anchor": f"job_{provider}_fixture",
            "stdout_path": str(stdout),
            "stderr_path": str(stdout.with_suffix(".stderr")),
            "started_at": "2026-07-28T00:00:00Z",
            "next_seq": 1,
            "anchor_emitted": True,
            "reply_buffer": "",
            "returncode": 0,
            "run_timeout_s": 900.0,
        },
    )


def test_pi_turn_end_and_agent_end_are_progress_only(tmp_path: Path) -> None:
    output = tmp_path / "pi.jsonl"
    _write_jsonl(output, _pi_run_events("intermediate", settled=False))

    observed = observe_pi_json_output(output)

    assert observed.finished is False
    assert observed.finish_reason == ""
    assert observed.outcome_reason == "stop"
    assert observed.text == "intermediate"
    assert observed.intermediate is True


def test_pi_agent_settled_uses_reply_from_last_retry_run(tmp_path: Path) -> None:
    output = tmp_path / "pi-retry.jsonl"
    first = _assistant("first attempt", stop_reason="error", error="temporary")
    final = _assistant("final answer")
    _write_jsonl(
        output,
        [
            {"type": "session", "version": 3, "id": "ses-pi"},
            {"type": "agent_start"},
            {"type": "turn_end", "message": first, "toolResults": []},
            {"type": "agent_end", "messages": [first], "willRetry": True},
            {"type": "auto_retry_start", "attempt": 1, "maxAttempts": 3},
            {"type": "agent_start"},
            {"type": "turn_end", "message": final, "toolResults": []},
            {"type": "agent_end", "messages": [final], "willRetry": False},
            {"type": "agent_settled"},
        ],
    )

    observed = observe_pi_json_output(output)

    assert observed.finished is True
    assert observed.finish_reason == "agent_settled"
    assert observed.outcome_reason == "stop"
    assert observed.error == ""
    assert observed.text == "final answer"


def test_pi_new_agent_run_invalidates_earlier_settled_signal(tmp_path: Path) -> None:
    output = tmp_path / "pi-reopened.jsonl"
    _write_jsonl(
        output,
        [
            *_pi_run_events("first answer"),
            {"type": "agent_start"},
        ],
    )

    observed = observe_pi_json_output(output)

    assert observed.finished is False
    assert observed.finish_reason == ""
    assert observed.text == ""


def test_omp_requires_explicit_true_terminal_flag_and_uses_final_run(
    tmp_path: Path,
) -> None:
    output = tmp_path / "omp-continuation.jsonl"
    first = _assistant("first answer")
    final = _assistant("final answer")
    _write_jsonl(
        output,
        [
            {"type": "session", "version": 3, "id": "ses-omp"},
            {"type": "agent_start"},
            {"type": "turn_end", "message": first, "toolResults": []},
            {"type": "agent_end", "messages": [first], "isTerminal": False},
            {"type": "agent_start"},
            {"type": "turn_end", "message": final, "toolResults": []},
            {"type": "agent_end", "messages": [final], "isTerminal": True},
        ],
    )

    observed = observe_omp_json_output(output)

    assert observed.finished is True
    assert observed.finish_reason == "agent_end_terminal"
    assert observed.outcome_reason == "stop"
    assert observed.text == "final answer"


def test_omp_missing_terminal_flag_fails_closed_for_latest_protocol(
    tmp_path: Path,
) -> None:
    output = tmp_path / "omp-missing-terminal-flag.jsonl"
    _write_jsonl(output, _omp_run_events("answer", is_terminal=None))

    observed = observe_omp_json_output(output)

    assert observed.finished is False
    assert observed.finish_reason == ""
    assert observed.outcome_reason == "stop"


def test_omp_terminal_yield_is_a_successful_final_result(tmp_path: Path) -> None:
    output = tmp_path / "omp-yield.jsonl"
    tool_message = {
        "id": "msg-yield",
        "role": "assistant",
        "stopReason": "toolUse",
        "content": [
            {
                "type": "toolCall",
                "id": "yield-1",
                "name": "yield",
                "arguments": {"result": {"data": {"answer": 42}}},
            }
        ],
    }
    _write_jsonl(
        output,
        [
            {"type": "session", "version": 3, "id": "ses-omp"},
            {"type": "agent_start"},
            {
                "type": "tool_execution_end",
                "toolCallId": "yield-1",
                "toolName": "yield",
                "isError": False,
                "result": {
                    "content": [{"type": "text", "text": "Result submitted."}],
                    "details": {
                        "status": "success",
                        "data": {"answer": 42},
                    },
                },
            },
            {
                "type": "agent_end",
                "messages": [tool_message],
                "isTerminal": True,
            },
        ],
    )

    observed = observe_omp_json_output(output)

    assert observed.finished is True
    assert observed.outcome_reason == "yield"
    assert observed.text == '{"answer": 42}'
    assert observed.error == ""


@pytest.mark.parametrize("provider", ("pi", "omp"))
def test_latest_semantic_terminal_waits_for_process_exit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    provider: str,
) -> None:
    work_dir = tmp_path / f"repo-{provider}-exit-fence"
    work_dir.mkdir()
    _write_session(provider, work_dir)
    _install_stub(monkeypatch, provider)
    barrier = tmp_path / f"{provider}-release"
    monkeypatch.setenv("STUB_POST_TERMINAL_BARRIER", str(barrier))

    adapter = _adapter(provider)
    submission = adapter.start(
        _job(provider, work_dir),
        context=_runtime_context(provider, work_dir),
        now="2026-07-28T00:00:00Z",
    )
    stdout = Path(str(submission.runtime_state["stdout_path"]))
    try:
        for _ in range(300):
            if _observer(provider)(stdout).finished:
                break
            time.sleep(0.01)
        else:
            raise AssertionError("latest semantic terminal event was not emitted")

        active = adapter.poll(submission, now="2026-07-28T00:00:01Z")

        assert active is not None
        assert active.decision is None
        assert active.submission.runtime_state["returncode"] is None

        barrier.touch()
        terminal = _run_to_terminal(adapter, active.submission)
        assert terminal.decision is not None
        assert terminal.decision.status is CompletionStatus.COMPLETED
        assert terminal.decision.reason == f"{provider}_run_stop"
        assert terminal.decision.diagnostics["returncode"] == 0
    finally:
        barrier.touch(exist_ok=True)
        adapter.cancel(submission)


@pytest.mark.parametrize("provider", ("pi", "omp"))
def test_clean_process_exit_without_latest_terminal_is_incomplete(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    provider: str,
) -> None:
    work_dir = tmp_path / f"repo-{provider}-missing-terminal"
    work_dir.mkdir()
    _write_session(provider, work_dir)
    _install_stub(monkeypatch, provider, mode="no_terminal")

    adapter = _adapter(provider)
    submission = adapter.start(
        _job(provider, work_dir),
        context=_runtime_context(provider, work_dir),
        now="2026-07-28T00:00:00Z",
    )
    terminal = _run_to_terminal(adapter, submission)

    assert terminal.decision is not None
    assert terminal.decision.status is CompletionStatus.INCOMPLETE
    assert terminal.decision.reason == f"{provider}_native_terminal_missing"
    assert terminal.decision.diagnostics["returncode"] == 0


@pytest.mark.parametrize("provider", ("pi", "omp"))
def test_nonzero_exit_wins_over_semantic_terminal(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    provider: str,
) -> None:
    work_dir = tmp_path / f"repo-{provider}-nonzero-after-terminal"
    work_dir.mkdir()
    _write_session(provider, work_dir)
    _install_stub(monkeypatch, provider)
    monkeypatch.setenv("STUB_POST_TERMINAL_EXIT_CODE", "17")

    adapter = _adapter(provider)
    submission = adapter.start(
        _job(provider, work_dir),
        context=_runtime_context(provider, work_dir),
        now="2026-07-28T00:00:00Z",
    )
    terminal = _run_to_terminal(adapter, submission)

    assert terminal.decision is not None
    assert terminal.decision.status is CompletionStatus.FAILED
    assert terminal.decision.reason == f"{provider}_run_failed"
    assert terminal.decision.diagnostics["returncode"] == 17


@pytest.mark.parametrize("provider", ("pi", "omp"))
def test_semantic_terminal_that_never_closes_hits_run_timeout(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    provider: str,
) -> None:
    work_dir = tmp_path / f"repo-{provider}-terminal-timeout"
    work_dir.mkdir()
    _write_session(provider, work_dir)
    _install_stub(monkeypatch, provider)
    barrier = tmp_path / f"{provider}-timeout-release"
    monkeypatch.setenv("STUB_POST_TERMINAL_BARRIER", str(barrier))
    monkeypatch.setenv(f"CCB_{provider.upper()}_RUN_TIMEOUT_S", "0.1")

    adapter = _adapter(provider)
    submission = adapter.start(
        _job(provider, work_dir),
        context=_runtime_context(provider, work_dir),
        now="2026-07-28T00:00:00Z",
    )
    stdout = Path(str(submission.runtime_state["stdout_path"]))
    try:
        for _ in range(300):
            if _observer(provider)(stdout).finished:
                break
            time.sleep(0.01)
        else:
            raise AssertionError("latest semantic terminal event was not emitted")

        terminal = adapter.poll(submission, now="2026-07-28T00:00:01Z")

        assert terminal is not None
        assert terminal.decision is not None
        assert terminal.decision.status is CompletionStatus.INCOMPLETE
        assert terminal.decision.reason == f"{provider}_run_timeout"
        assert terminal.decision.diagnostics["run_timeout_s"] == 0.1
    finally:
        barrier.touch(exist_ok=True)
        adapter.cancel(submission)


@pytest.mark.parametrize("provider", ("pi", "omp"))
@pytest.mark.parametrize(
    ("stop_reason", "expected_status", "expected_reason"),
    (
        ("error", CompletionStatus.FAILED, "run_error"),
        ("aborted", CompletionStatus.INCOMPLETE, "run_finished:aborted"),
        ("length", CompletionStatus.INCOMPLETE, "run_finished:length"),
    ),
)
def test_latest_final_assistant_outcome_is_validated(
    tmp_path: Path,
    provider: str,
    stop_reason: str,
    expected_status: CompletionStatus,
    expected_reason: str,
) -> None:
    output = tmp_path / f"{provider}-{stop_reason}.jsonl"
    events = (
        _pi_run_events("partial answer", stop_reason=stop_reason)
        if provider == "pi"
        else _omp_run_events("partial answer", stop_reason=stop_reason)
    )
    if stop_reason == "error":
        for event in events:
            if event.get("type") in {"turn_end", "agent_end"}:
                messages = event.get("messages") or [event.get("message")]
                for message in messages:
                    if isinstance(message, dict):
                        message["errorMessage"] = "provider request failed"
    _write_jsonl(output, events)

    result = _adapter(provider).poll(
        _completed_submission(provider, output),
        now="2026-07-28T00:00:01Z",
    )

    assert result is not None
    assert result.decision is not None
    assert result.decision.status is expected_status
    assert result.decision.reason == f"{provider}_{expected_reason}"
    assert result.decision.diagnostics["outcome_reason"] == stop_reason


@pytest.mark.parametrize("provider", ("pi", "omp"))
@pytest.mark.parametrize(
    ("invalid_tail", "protocol_error_prefix"),
    (
        ('{"type":"message_update"', "unterminated_jsonl_record:"),
        ("not-json\n", "invalid_jsonl_record:"),
    ),
)
def test_invalid_latest_stream_is_not_completed(
    tmp_path: Path,
    provider: str,
    invalid_tail: str,
    protocol_error_prefix: str,
) -> None:
    output = tmp_path / f"{provider}-invalid.jsonl"
    events = _pi_run_events("answer") if provider == "pi" else _omp_run_events("answer")
    _write_jsonl(output, events)
    with output.open("a", encoding="utf-8") as stream:
        stream.write(invalid_tail)

    observed = _observer(provider)(output)
    assert observed.finished is True
    assert observed.protocol_error.startswith(protocol_error_prefix)

    result = _adapter(provider).poll(
        _completed_submission(provider, output),
        now="2026-07-28T00:00:01Z",
    )

    assert result is not None
    assert result.decision is not None
    assert result.decision.status is CompletionStatus.INCOMPLETE
    assert result.decision.reason == f"{provider}_native_protocol_invalid"


@pytest.mark.parametrize("provider", ("pi", "omp"))
def test_latest_terminal_without_final_outcome_is_not_completed(
    tmp_path: Path,
    provider: str,
) -> None:
    output = tmp_path / f"{provider}-missing-outcome.jsonl"
    events = _pi_run_events("answer") if provider == "pi" else _omp_run_events("answer")
    for event in events:
        message = event.get("message")
        if isinstance(message, dict):
            message.pop("stopReason", None)
        messages = event.get("messages")
        if isinstance(messages, list):
            for item in messages:
                if isinstance(item, dict):
                    item.pop("stopReason", None)
    _write_jsonl(output, events)

    result = _adapter(provider).poll(
        _completed_submission(provider, output),
        now="2026-07-28T00:00:01Z",
    )

    assert result is not None
    assert result.decision is not None
    assert result.decision.status is CompletionStatus.INCOMPLETE
    assert result.decision.reason == f"{provider}_native_outcome_missing"
