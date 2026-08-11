from __future__ import annotations

import json
import sys
import time
import uuid
from pathlib import Path

import pytest
from ccbd.api_models import DeliveryScope, JobRecord, JobStatus, MessageEnvelope
from completion.models import CompletionItemKind, CompletionSourceKind, CompletionStatus
from provider_backends.copilot.execution import _build_env as build_copilot_env
from provider_backends.cursor.execution import (
    build_headless_execution_adapter as build_cursor_headless_execution_adapter,
)
from provider_backends.grok import home as grok_home
from provider_backends.grok.execution import (
    _grok_session_id_for_job,
    observe_grok_output,
)
from provider_backends.grok.execution import (
    build_headless_execution_adapter as build_grok_headless_execution_adapter,
)
from provider_backends.native_cli_support import (
    NativeCliExecutionConfig,
    NativeCliExecutionRequest,
)
from provider_backends.native_cli_support.execution import _native_cli_env
from provider_backends.pi.execution import (
    build_headless_execution_adapter as build_pi_headless_execution_adapter,
)
from provider_backends.qoder.execution import (
    _build_command as build_qoder_command,
)
from provider_backends.qoder.execution import (
    _qoder_session_id_for_job,
    observe_qoder_output,
)
from provider_backends.qoderclicn.execution import (
    _build_command as build_qoderclicn_command,
)
from provider_backends.qoderclicn.execution import (
    _qoderclicn_session_id_for_job,
    observe_qoderclicn_output,
)
from provider_backends.zai.execution import observe_zai_output
from provider_core.pathing import session_filename_for_agent
from provider_core.registry import build_default_backend_registry
from provider_execution.base import ProviderRuntimeContext, ProviderSubmission

PROVIDERS = ("qwen", "qoder", "qoderclicn", "cursor", "copilot", "crush", "kiro", "pi", "omp", "zai")
STRUCTURED_PROVIDERS = ("qwen", "cursor", "copilot", "pi", "omp")


def _job(provider: str, work_dir: Path) -> JobRecord:
    agent_name = f"{provider}1"
    return JobRecord(
        job_id=f"job_{provider}_run123",
        submission_id=f"sub_{provider}",
        agent_name=agent_name,
        provider=provider,
        request=MessageEnvelope(
            project_id="proj",
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
        created_at="2026-06-13T00:00:00Z",
        updated_at="2026-06-13T00:00:00Z",
        workspace_path=str(work_dir),
    )


def _runtime_context(provider: str, work_dir: Path) -> ProviderRuntimeContext:
    agent_name = f"{provider}1"
    return ProviderRuntimeContext(
        agent_name=agent_name,
        workspace_path=str(work_dir),
        backend_type="pane-backed",
        runtime_ref="%1",
        session_ref=str(work_dir / ".ccb" / session_filename_for_agent(provider, agent_name)),
    )


def _write_session(provider: str, work_dir: Path) -> None:
    agent_name = f"{provider}1"
    runtime_dir = work_dir / ".ccb" / "agents" / agent_name / "provider-runtime" / provider
    state_dir = work_dir / ".ccb" / "agents" / agent_name / "provider-state" / provider
    session = {
        "active": True,
        "agent_name": agent_name,
        "runtime_dir": str(runtime_dir),
        "completion_artifact_dir": str(runtime_dir / "completion"),
        "work_dir": str(work_dir),
        "pane_id": "%1",
        f"{provider}_state_dir": str(state_dir),
        f"{provider}_home": str(state_dir / "home"),
        f"{provider}_data_dir": str(state_dir / "data"),
    }
    session_path = work_dir / ".ccb" / session_filename_for_agent(provider, agent_name)
    session_path.parent.mkdir(parents=True, exist_ok=True)
    session_path.write_text(json.dumps(session, ensure_ascii=True), encoding="utf-8")


def _adapter(provider: str):
    if provider == "cursor":
        return build_cursor_headless_execution_adapter()
    if provider == "pi":
        return build_pi_headless_execution_adapter()
    backend = build_default_backend_registry(include_optional=True, include_test_doubles=False).get(provider)
    assert backend is not None
    assert backend.execution_adapter is not None
    return backend.execution_adapter


def test_qoderclicn_headless_command_uses_qoder_contract_and_uuid(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.delenv("QODERCLICN_START_CMD", raising=False)
    provider = "qoderclicn"
    work_dir = tmp_path / "repo-qoderclicn-command"
    work_dir.mkdir()
    config_dir = work_dir / ".ccb" / "agents" / "qoderclicn1" / "provider-state" / provider / "home"
    request = NativeCliExecutionRequest(
        provider=provider,
        job=_job(provider, work_dir),
        work_dir=work_dir,
        session_data={f"{provider}_config_dir": str(config_dir)},
        prompt="Reply exactly once.",
        request_anchor="CCB_REQ_ID: job",
    )

    command = build_qoderclicn_command(request)
    session_id = command[command.index("--session-id") + 1]

    assert command == [
        "qoderclicn",
        "--config-dir",
        str(config_dir),
        "--permission-mode",
        "dont_ask",
        "-w",
        str(work_dir),
        "-p",
        "--output-format",
        "stream-json",
        "--session-id",
        session_id,
        "Reply exactly once.",
    ]
    assert str(uuid.UUID(session_id)) == session_id
    assert session_id == _qoderclicn_session_id_for_job(request.job.job_id)
    assert session_id != request.job.job_id
    assert config_dir.is_dir()


def test_qoderclicn_headless_command_does_not_repeat_explicit_options(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    explicit_config = tmp_path / "explicit-qoderclicn"
    monkeypatch.setenv(
        "QODERCLICN_START_CMD",
        f"qoderclicn --config-dir {explicit_config} --yolo",
    )
    work_dir = tmp_path / "repo-qoderclicn-explicit"
    work_dir.mkdir()
    request = NativeCliExecutionRequest(
        provider="qoderclicn",
        job=_job("qoderclicn", work_dir),
        work_dir=work_dir,
        session_data={"qoderclicn_headless_permission_mode": "auto"},
        prompt="test prompt",
        request_anchor="anchor",
    )

    command = build_qoderclicn_command(request)

    assert command.count("--config-dir") == 1
    assert command.count("--yolo") == 1
    assert "--permission-mode" not in command


def _install_stub(monkeypatch, provider: str, *, mode: str = "") -> None:
    if provider == "kiro":
        # Kiro's managed storage contract intentionally fails closed on
        # macOS. These generic adapter tests exercise the supported private
        # file-store path; the macOS rejection has dedicated coverage.
        monkeypatch.setattr(
            "provider_backends.native_cli_support.home.platform.system",
            lambda: "Linux",
        )
    stub = Path("test/stubs/provider_stub.py").resolve()
    monkeypatch.setenv(f"{provider.upper()}_START_CMD", f"{sys.executable} {stub} --provider {provider}")
    if mode:
        monkeypatch.setenv(f"{provider.upper()}_STUB_MODE", mode)
    else:
        monkeypatch.delenv(f"{provider.upper()}_STUB_MODE", raising=False)


def _run_to_terminal(adapter, submission: ProviderSubmission):
    current = submission
    emitted: list[CompletionItemKind] = []
    for index in range(150):
        result = adapter.poll(current, now=f"2026-06-13T00:00:{index % 60:02d}Z")
        if result is not None:
            current = result.submission
            emitted.extend(item.kind for item in result.items)
            if result.decision is not None:
                return result, emitted
        time.sleep(0.02)
    raise AssertionError("provider adapter did not terminalize")


def test_copilot_headless_execution_uses_agent_local_home_and_cache(tmp_path: Path) -> None:
    work_dir = tmp_path / 'repo-copilot-env'
    state_dir = work_dir / '.ccb' / 'agents' / 'copilot1' / 'provider-state' / 'copilot'
    request = NativeCliExecutionRequest(
        provider='copilot',
        job=_job('copilot', work_dir),
        work_dir=work_dir,
        session_data={
            'copilot_state_dir': str(state_dir),
            'copilot_home': str(state_dir / 'home'),
            'copilot_data_dir': str(state_dir / 'data'),
        },
        prompt='test prompt',
        request_anchor='anchor',
    )

    env = build_copilot_env(request)

    assert env == {
        'COPILOT_HOME': str(state_dir / 'home'),
        'COPILOT_CACHE_HOME': str(state_dir / 'data' / 'cache'),
        'COPILOT_DISABLE_KEYTAR': '1',
    }
    assert (state_dir / 'home').is_dir()
    assert (state_dir / 'data' / 'cache').is_dir()


def test_native_headless_env_overrides_global_home_and_detaches_legacy_link(
    tmp_path: Path,
) -> None:
    work_dir = tmp_path / "repo"
    work_dir.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    managed_home = tmp_path / "managed-home"
    managed_home.symlink_to(outside, target_is_directory=True)
    request = NativeCliExecutionRequest(
        provider="qwen",
        job=_job("qwen", work_dir),
        work_dir=work_dir,
        session_data={"qwen_home": str(managed_home)},
        prompt="test prompt",
        request_anchor="anchor",
    )
    config = NativeCliExecutionConfig(
        provider="qwen",
        session_filename=".qwen-session",
        command_builder=lambda _request: ["qwen"],
        env_builder=lambda _request: {"HOME": str(tmp_path / "global-home")},
    )

    env = _native_cli_env(config, request)

    assert env["HOME"] == str(managed_home)
    assert env["XDG_CONFIG_HOME"] == str(managed_home / ".config")
    assert env["XDG_DATA_HOME"] == str(managed_home.parent / "data")
    assert env["XDG_STATE_HOME"] == str(managed_home / ".local" / "state")
    assert env["XDG_CACHE_HOME"] == str(managed_home / ".cache")
    assert managed_home.is_dir() and not managed_home.is_symlink()
    assert not any(outside.iterdir())


def test_cursor_headless_disables_os_credential_store(tmp_path: Path) -> None:
    from provider_backends.cursor.execution import _build_env as build_cursor_env

    work_dir = tmp_path / "repo-cursor"
    work_dir.mkdir()
    managed_home = tmp_path / "managed-cursor"
    request = NativeCliExecutionRequest(
        provider="cursor",
        job=_job("cursor", work_dir),
        work_dir=work_dir,
        session_data={"cursor_home": str(managed_home)},
        prompt="test prompt",
        request_anchor="anchor",
    )

    env = build_cursor_env(request)

    assert env["HOME"] == str(managed_home)
    assert env["AGENT_CLI_CREDENTIAL_STORE"] == "file"


def test_qoder_headless_command_uses_documented_print_mode_and_uuid(tmp_path: Path) -> None:
    work_dir = tmp_path / "repo-qoder-command"
    config_dir = work_dir / ".ccb" / "agents" / "qoder1" / "provider-state" / "qoder" / "home"
    request = NativeCliExecutionRequest(
        provider="qoder",
        job=_job("qoder", work_dir),
        work_dir=work_dir,
        session_data={"qoder_config_dir": str(config_dir)},
        prompt="test prompt",
        request_anchor="anchor",
    )

    command = build_qoder_command(request)
    session_id = command[command.index("--session-id") + 1]

    assert "--bare" not in command
    assert "-p" in command
    assert command[command.index("--config-dir") + 1] == str(config_dir)
    assert command[command.index("--permission-mode") + 1] == "dont_ask"
    assert str(uuid.UUID(session_id)) == session_id
    assert session_id == _qoder_session_id_for_job(request.job.job_id)
    assert session_id != request.job.job_id
    assert config_dir.is_dir()


def test_qoder_observer_uses_result_without_duplicate_assistant_text(tmp_path: Path) -> None:
    output = tmp_path / "qoder.jsonl"
    session_id = "11111111-1111-5111-8111-111111111111"
    output.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                {"type": "system", "subtype": "init", "session_id": session_id},
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "QODER_OK"}],
                    },
                    "session_id": session_id,
                },
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": "QODER_OK",
                    "stop_reason": "end_turn",
                    "session_id": session_id,
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )

    observed = observe_qoder_output(output)

    assert observed.text == "QODER_OK"
    assert observed.finished is True
    assert observed.finish_reason == "completed"
    assert observed.turn_ref == session_id
    assert observed.error == ""


def test_qoder_observer_fails_closed_on_stream_result_error(tmp_path: Path) -> None:
    output = tmp_path / "qoder-error.jsonl"
    output.write_text(
        "\n".join(
            (
                json.dumps(
                    {
                        "type": "assistant",
                        "message": {
                            "role": "assistant",
                            "content": [{"type": "text", "text": "Not logged in"}],
                        },
                        "error": "authentication_failed",
                    }
                ),
                json.dumps(
                    {
                        "type": "result",
                        "subtype": "success",
                        "is_error": True,
                        "result": "Not logged in",
                        "stop_reason": "stop_sequence",
                    }
                ),
            )
        )
        + "\n",
        encoding="utf-8",
    )

    observed = observe_qoder_output(output)

    assert observed.finished is True
    assert observed.text == ""
    assert observed.error == "Not logged in"


def test_qoderclicn_observer_uses_result_without_duplicate_assistant_text(
    tmp_path: Path,
) -> None:
    output = tmp_path / "qoderclicn.jsonl"
    session_id = "22222222-2222-5222-8222-222222222222"
    output.write_text(
        "\n".join(
            json.dumps(row)
            for row in (
                {"type": "system", "subtype": "init", "session_id": session_id},
                {
                    "type": "assistant",
                    "message": {
                        "role": "assistant",
                        "content": [{"type": "text", "text": "QODER_CN_OK"}],
                    },
                    "session_id": session_id,
                },
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": False,
                    "result": "QODER_CN_OK",
                    "stop_reason": "end_turn",
                    "session_id": session_id,
                },
            )
        )
        + "\n",
        encoding="utf-8",
    )

    observed = observe_qoderclicn_output(output)

    assert observed.text == "QODER_CN_OK"
    assert observed.finished is True
    assert observed.finish_reason == "completed"
    assert observed.turn_ref == session_id
    assert observed.error == ""


def test_qoderclicn_observer_waits_for_error_result_envelope(tmp_path: Path) -> None:
    output = tmp_path / "qoderclicn-error.jsonl"
    assistant_error = {
        "type": "assistant",
        "message": {
            "role": "assistant",
            "content": [{"type": "text", "text": "Not logged in"}],
        },
        "error": "authentication_failed",
    }
    output.write_text(json.dumps(assistant_error) + "\n", encoding="utf-8")

    assistant_only = observe_qoderclicn_output(output)

    assert assistant_only.finished is False
    assert assistant_only.text == "Not logged in"
    assert assistant_only.error == ""

    with output.open("a", encoding="utf-8") as stream:
        stream.write(
            json.dumps(
                {
                    "type": "result",
                    "subtype": "success",
                    "is_error": True,
                    "result": "Not logged in",
                    "stop_reason": "stop_sequence",
                }
            )
            + "\n"
        )

    observed = observe_qoderclicn_output(output)

    assert observed.finished is True
    assert observed.text == ""
    assert observed.error == "Not logged in"


@pytest.mark.parametrize("provider", ("qoder", "qoderclicn"))
def test_qoder_provider_requires_native_result_envelope(
    monkeypatch,
    tmp_path: Path,
    provider: str,
) -> None:
    work_dir = tmp_path / f"repo-{provider}-no-result"
    work_dir.mkdir()
    _write_session(provider, work_dir)
    _install_stub(monkeypatch, provider, mode="no_terminal")

    adapter = _adapter(provider)
    submission = adapter.start(
        _job(provider, work_dir),
        context=_runtime_context(provider, work_dir),
        now="2026-06-13T00:00:00Z",
    )
    terminal, emitted = _run_to_terminal(adapter, submission)

    assert terminal.decision is not None
    assert terminal.decision.status is CompletionStatus.INCOMPLETE
    assert terminal.decision.reason == f"{provider}_native_terminal_missing"
    assert terminal.decision.reply == f"stub reply for job_{provider}_run123"
    assert CompletionItemKind.TURN_BOUNDARY not in emitted


@pytest.mark.parametrize("provider", PROVIDERS)
def test_native_cli_provider_adapter_completes_from_native_process(monkeypatch, tmp_path: Path, provider: str) -> None:
    work_dir = tmp_path / f"repo-{provider}"
    work_dir.mkdir()
    _write_session(provider, work_dir)
    _install_stub(monkeypatch, provider)

    adapter = _adapter(provider)
    submission = adapter.start(_job(provider, work_dir), context=_runtime_context(provider, work_dir), now="2026-06-13T00:00:00Z")

    assert submission.source_kind is CompletionSourceKind.STRUCTURED_RESULT_STREAM
    assert submission.runtime_state["mode"] == f"{provider}_run"

    terminal, emitted = _run_to_terminal(adapter, submission)

    assert terminal.decision is not None
    assert terminal.decision.status is CompletionStatus.COMPLETED
    assert terminal.decision.reply == f"stub reply for job_{provider}_run123"
    assert CompletionItemKind.ANCHOR_SEEN in emitted
    assert CompletionItemKind.ASSISTANT_FINAL in emitted
    assert CompletionItemKind.TURN_BOUNDARY in emitted


def test_grok_provider_adapter_projects_system_login_and_uses_uuid_session(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source_home = tmp_path / "source-home"
    source_grok = source_home / ".grok"
    source_grok.mkdir(parents=True)
    (source_grok / "auth.json").write_text('{"token":"system-login"}\n', encoding="utf-8")
    (source_grok / "config.toml").write_text('model = "grok-test"\n', encoding="utf-8")
    monkeypatch.setattr(grok_home, "current_provider_source_home", lambda: source_home)

    work_dir = tmp_path / "repo-grok-login"
    work_dir.mkdir()
    _write_session("grok", work_dir)
    _install_stub(monkeypatch, "grok")

    adapter = build_grok_headless_execution_adapter()
    job = _job("grok", work_dir)
    submission = adapter.start(job, context=_runtime_context("grok", work_dir), now="2026-06-13T00:00:00Z")

    managed_home = work_dir / ".ccb" / "agents" / "grok1" / "provider-state" / "grok" / "home"
    assert (managed_home / ".grok" / "auth.json").read_text(encoding="utf-8") == '{"token":"system-login"}\n'
    assert (managed_home / ".grok" / "config.toml").read_text(encoding="utf-8") == 'model = "grok-test"\n'
    (managed_home / ".grok" / "auth.json").write_text(
        '{"token":"managed-login"}\n',
        encoding="utf-8",
    )
    assert (source_grok / "auth.json").read_text(encoding="utf-8") == (
        '{"token":"system-login"}\n'
    )
    grok_session_id = _grok_session_id_for_job(job.job_id)
    assert grok_session_id != job.job_id
    assert str(uuid.UUID(grok_session_id)) == grok_session_id
    assert submission.diagnostics["pid"]

    terminal, _emitted = _run_to_terminal(adapter, submission)
    assert terminal.decision is not None
    assert terminal.decision.status is CompletionStatus.COMPLETED
    assert terminal.decision.reason == "grok_run_stop"
    assert terminal.decision.diagnostics["finish_reason"] == "end_turn"


def test_grok_provider_adapter_requires_native_terminal_event(monkeypatch, tmp_path: Path) -> None:
    work_dir = tmp_path / "repo-grok-missing-native-terminal"
    work_dir.mkdir()
    _write_session("grok", work_dir)
    _install_stub(monkeypatch, "grok", mode="no_terminal")

    adapter = build_grok_headless_execution_adapter()
    submission = adapter.start(
        _job("grok", work_dir),
        context=_runtime_context("grok", work_dir),
        now="2026-06-13T00:00:00Z",
    )
    terminal, emitted = _run_to_terminal(adapter, submission)

    assert terminal.decision is not None
    assert terminal.decision.status is CompletionStatus.INCOMPLETE
    assert terminal.decision.reason == "grok_native_terminal_missing"
    assert terminal.decision.reply == "stub reply for job_grok_run123"
    assert terminal.decision.diagnostics["returncode"] == 0
    assert CompletionItemKind.TURN_BOUNDARY not in emitted


def test_grok_provider_adapter_reports_native_cancelled_end(monkeypatch, tmp_path: Path) -> None:
    work_dir = tmp_path / "repo-grok-native-cancelled"
    work_dir.mkdir()
    _write_session("grok", work_dir)
    _install_stub(monkeypatch, "grok", mode="cancelled")

    adapter = build_grok_headless_execution_adapter()
    submission = adapter.start(
        _job("grok", work_dir),
        context=_runtime_context("grok", work_dir),
        now="2026-06-13T00:00:00Z",
    )
    terminal, emitted = _run_to_terminal(adapter, submission)

    assert terminal.decision is not None
    assert terminal.decision.status is CompletionStatus.INCOMPLETE
    assert terminal.decision.reason == "grok_run_finished:cancelled"
    assert terminal.decision.reply == ""
    assert CompletionItemKind.TURN_BOUNDARY in emitted


@pytest.mark.parametrize("provider", PROVIDERS)
def test_native_cli_provider_adapter_reports_empty_reply(monkeypatch, tmp_path: Path, provider: str) -> None:
    work_dir = tmp_path / f"repo-empty-{provider}"
    work_dir.mkdir()
    _write_session(provider, work_dir)
    _install_stub(monkeypatch, provider, mode="empty")

    adapter = _adapter(provider)
    submission = adapter.start(_job(provider, work_dir), context=_runtime_context(provider, work_dir), now="2026-06-13T00:00:00Z")
    terminal, _emitted = _run_to_terminal(adapter, submission)

    assert terminal.decision is not None
    assert terminal.decision.status is CompletionStatus.INCOMPLETE
    assert terminal.decision.reason == f"{provider}_empty_reply"


@pytest.mark.parametrize("provider", PROVIDERS)
def test_native_cli_provider_adapter_reports_nonzero_exit(monkeypatch, tmp_path: Path, provider: str) -> None:
    work_dir = tmp_path / f"repo-failed-{provider}"
    work_dir.mkdir()
    _write_session(provider, work_dir)
    _install_stub(monkeypatch, provider, mode="permission")

    adapter = _adapter(provider)
    submission = adapter.start(_job(provider, work_dir), context=_runtime_context(provider, work_dir), now="2026-06-13T00:00:00Z")
    terminal, _emitted = _run_to_terminal(adapter, submission)

    assert terminal.decision is not None
    assert terminal.decision.status is CompletionStatus.FAILED
    assert terminal.decision.reason == f"{provider}_run_failed"
    assert "permission denied" in str(terminal.decision.diagnostics.get("stderr_tail") or "")


@pytest.mark.parametrize("provider", PROVIDERS)
def test_native_cli_provider_adapter_reports_run_timeout(monkeypatch, tmp_path: Path, provider: str) -> None:
    work_dir = tmp_path / f"repo-timeout-{provider}"
    work_dir.mkdir()
    _write_session(provider, work_dir)
    _install_stub(monkeypatch, provider, mode="timeout")
    monkeypatch.setenv("STUB_TIMEOUT_SLEEP", "5")
    monkeypatch.setenv(f"CCB_{provider.upper()}_RUN_TIMEOUT_S", "0.1")

    adapter = _adapter(provider)
    submission = adapter.start(_job(provider, work_dir), context=_runtime_context(provider, work_dir), now="2026-06-13T00:00:00Z")
    terminal, emitted = _run_to_terminal(adapter, submission)

    assert terminal.decision is not None
    assert terminal.decision.status is CompletionStatus.INCOMPLETE
    assert terminal.decision.reason == f"{provider}_run_timeout"
    assert terminal.decision.diagnostics["run_timeout_s"] == 0.1
    assert CompletionItemKind.ANCHOR_SEEN in emitted


def test_zai_observer_extracts_assistant_and_drops_progress(tmp_path: Path) -> None:
    stdout = tmp_path / "zai.out"
    stdout.write_text(
        "\n".join(
            [
                json.dumps({"role": "user", "content": "CCB_REQ_ID: job_zai\nread file"}, ensure_ascii=True),
                json.dumps({"role": "assistant", "content": "Using tools to help you..."}, ensure_ascii=True),
                json.dumps({"role": "assistant", "content": "alpha beta gamma"}, ensure_ascii=True),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    observation = observe_zai_output(stdout)

    assert observation.error == ""
    assert observation.text == "alpha beta gamma"


def test_grok_observer_extracts_session_update_chunks(tmp_path: Path) -> None:
    stdout = tmp_path / "grok.jsonl"
    stdout.write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": "ses-grok",
                            "update": {
                                "sessionUpdate": "agent_message_chunk",
                                "content": {"type": "text", "text": "alpha "},
                            },
                        },
                    },
                    ensure_ascii=True,
                ),
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": "ses-grok",
                            "update": {
                                "sessionUpdate": "agent_message_chunk",
                                "content": {"type": "text", "text": "beta"},
                            },
                        },
                    },
                    ensure_ascii=True,
                ),
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "method": "session/update",
                        "params": {
                            "sessionId": "ses-grok",
                            "update": {"sessionUpdate": "turn_end", "stopReason": "end_turn"},
                        },
                    },
                    ensure_ascii=True,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    observation = observe_grok_output(stdout)

    assert observation.error == ""
    assert observation.text == "alpha beta"
    assert observation.finished is True
    assert observation.finish_reason == "end_turn"
    assert observation.turn_ref == "ses-grok"


@pytest.mark.parametrize("provider", STRUCTURED_PROVIDERS)
def test_native_cli_structured_tool_event_does_not_terminalize_before_final(tmp_path: Path, provider: str) -> None:
    stdout = tmp_path / f"{provider}.jsonl"
    stdout.write_text(
        json.dumps(
            {
                "type": "tool_call",
                "role": "assistant",
                "status": "tool_calls",
                "name": "demo",
            },
            ensure_ascii=True,
        )
        + "\n",
        encoding="utf-8",
    )
    submission = ProviderSubmission(
        job_id=f"job_tool_{provider}",
        agent_name=f"{provider}1",
        provider=provider,
        accepted_at="2026-06-13T00:00:00Z",
        ready_at="2026-06-13T00:00:00Z",
        source_kind=CompletionSourceKind.STRUCTURED_RESULT_STREAM,
        reply="",
        runtime_state={
            "mode": f"{provider}_run",
            "provider": provider,
            "job_id": f"job_tool_{provider}",
            "request_anchor": f"job_tool_{provider}",
            "stdout_path": str(stdout),
            "stderr_path": str(tmp_path / "stderr.log"),
            "next_seq": 1,
            "anchor_emitted": False,
            "returncode": None,
        },
    )

    active = _adapter(provider).poll(submission, now="2026-06-13T00:00:01Z")

    assert active is not None
    assert active.decision is None

    active.submission.runtime_state["returncode"] = 0
    terminal = _adapter(provider).poll(active.submission, now="2026-06-13T00:00:02Z")

    assert terminal is not None
    assert terminal.decision is not None
    assert terminal.decision.status is CompletionStatus.INCOMPLETE
    expected_reason = (
        f"{provider}_native_terminal_missing"
        if provider in {"grok", "pi", "omp"}
        else f"{provider}_run_finished:tool_calls"
    )
    assert terminal.decision.reason == expected_reason
