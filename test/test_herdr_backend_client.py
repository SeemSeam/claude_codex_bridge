from __future__ import annotations

import json
import subprocess
import shlex
import sys

import pytest

import terminal_runtime.api as terminal_api
import platforms.windows.herdr.runtime.cli as herdr_cli
from ccbd.services.project_namespace_pane import inspect_project_namespace_pane
from ccbd.services.project_namespace_runtime import controller as namespace_controller
from terminal_runtime.backend_selection import TerminalBackendSelection
from platforms.windows.herdr.backend import HerdrBackend
from platforms.windows.herdr.runtime.capabilities import HerdrCapabilityGate
from platforms.windows.herdr.runtime.cli import HerdrCliRequestAdapter
from platforms.windows.herdr.runtime.client import HerdrSocketClient
from terminal_runtime.mux_backend_contract import MuxCommandErrorV2


def test_herdr_capability_gate_blocks_missing_spike_evidence() -> None:
    gate = HerdrCapabilityGate.from_spike_evidence(
        None,
        capability_report_ref="evidence/herdr-contract-spike-evidence.json",
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        gate.require_supported("create_session")

    assert exc_info.value.category == "unsupported"
    assert exc_info.value.backend_impl == "herdr"
    assert exc_info.value.evidence["failure_reason"] == "herdr-capability-missing"


@pytest.mark.parametrize(
    "spike_evidence",
    [
        {"adapter_recommendation": "stop"},
        {"adapter_recommendation": "needs-upstream-issue"},
        {"verdict": "blocked"},
        {"verdict": "failed"},
        {"failure_class": "windows-beta-gap"},
        {"capability_projection": {"blocking_gaps": ["server_restart_output_history"]}},
        {"capability_projection": {"blocking_gaps": "server_restart_output_history"}},
        {"capability_projection": {"blocking_gaps": [123]}},
        {"capability_projection": {"windows_beta_gaps": "server_restart_output_history"}},
        {"capability_projection": {"windows_beta_gaps": [False]}},
        {"capability_projection": {"command_status": {"send_input": "unknown"}}},
        {"capability_projection": {"command_status": {"send_input": "surprising-new-status"}}},
    ],
)
def test_herdr_capability_gate_fails_closed_for_blocking_spike_projection(
    spike_evidence: dict[str, object],
) -> None:
    gate = HerdrCapabilityGate.from_spike_evidence(
        spike_evidence,
        capability_report_ref="evidence/herdr-contract-spike-evidence.json",
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        gate.require_supported("send_text")

    assert exc_info.value.category == "unsupported"
    assert exc_info.value.evidence["failure_reason"] == "unsupported-capability"


def test_herdr_capability_gate_allows_supported_spike_projection() -> None:
    gate = HerdrCapabilityGate.from_spike_evidence(
        {
            "adapter_recommendation": "continue",
            "verdict": "pass",
            "failure_class": "none",
            "capability_projection": {
                "command_status": {
                    "session_attach": "supported",
                    "pane_spawn": "supported",
                    "send_input": "supported",
                    "read_output": "supported",
                    "kill_pane": "supported",
                },
                "semantic_status": {
                    "session_attach": "supported",
                    "pane_spawn": "supported",
                    "send_input": "supported",
                    "read_output": "supported",
                    "kill_pane": "supported",
                },
                "windows_beta_gaps": [],
                "blocking_gaps": [],
            },
        },
        capability_report_ref="evidence/herdr-contract-spike-evidence.json",
    )

    capabilities = gate.require_supported("send_text")

    assert capabilities["backend_impl"] == "herdr"
    assert capabilities["blocking_gaps"] == []
    assert capabilities["source_ref"] == "evidence/herdr-contract-spike-evidence.json"


def test_herdr_capability_gate_allows_session_and_release_operations() -> None:
    gate = _supported_gate()

    assert gate.require_supported("report_pane_agent_session")["backend_impl"] == "herdr"
    assert gate.require_supported("release_pane_agent")["backend_impl"] == "herdr"


def test_herdr_capability_gate_allows_nonrequired_windows_beta_gaps() -> None:
    gate = HerdrCapabilityGate.from_spike_evidence(
        {
            "adapter_recommendation": "continue-with-gaps",
            "verdict": "partial",
            "failure_class": "windows-beta-gap",
            "capability_projection": {
                "command_status": {
                    "session_attach": "supported",
                    "pane_spawn": "supported",
                    "send_input": "supported",
                    "read_output": "supported",
                    "kill_pane": "supported",
                    "server_restart_process_continuity": "unsupported",
                },
                "semantic_status": {
                    "session_attach": "supported",
                    "pane_spawn": "supported",
                    "send_input": "supported",
                    "read_output": "supported",
                    "kill_pane": "supported",
                    "server_restart_process_continuity": "unsupported",
                },
                "windows_beta_gaps": [],
                "blocking_gaps": ["server_restart_process_continuity"],
            },
        },
        capability_report_ref="evidence/herdr-contract-spike-evidence.json",
    )

    capabilities = gate.require_supported("prepare_server")

    assert capabilities["backend_impl"] == "herdr"
    assert capabilities["blocking_gaps"] == ["server_restart_process_continuity"]


@pytest.mark.parametrize(
    "spike_evidence",
    [
        {
            "adapter_recommendation": "continue",
            "verdict": "unknown",
            "failure_class": "none",
            "capability_projection": {
                "command_status": {
                    "session_attach": "supported",
                    "pane_spawn": "supported",
                    "send_input": "supported",
                    "read_output": "supported",
                    "kill_pane": "supported",
                },
                "semantic_status": {
                    "session_attach": "supported",
                    "pane_spawn": "supported",
                    "send_input": "supported",
                    "read_output": "supported",
                    "kill_pane": "supported",
                },
                "windows_beta_gaps": [],
                "blocking_gaps": [],
            },
        },
        {
            "adapter_recommendation": "surprising",
            "verdict": "pass",
            "failure_class": "none",
            "capability_projection": {
                "command_status": {
                    "session_attach": "supported",
                    "pane_spawn": "supported",
                    "send_input": "supported",
                    "read_output": "supported",
                    "kill_pane": "supported",
                },
                "semantic_status": {
                    "session_attach": "supported",
                    "pane_spawn": "supported",
                    "send_input": "supported",
                    "read_output": "supported",
                    "kill_pane": "supported",
                },
                "windows_beta_gaps": [],
                "blocking_gaps": [],
            },
        },
    ],
)
def test_herdr_capability_gate_requires_known_continue_and_pass_verdict(
    spike_evidence: dict[str, object],
) -> None:
    gate = HerdrCapabilityGate.from_spike_evidence(
        spike_evidence,
        capability_report_ref="evidence/herdr-contract-spike-evidence.json",
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        gate.require_supported("send_text")

    assert exc_info.value.category == "unsupported"


def test_herdr_capability_gate_does_not_surface_extra_upstream_capability_names() -> None:
    gate = HerdrCapabilityGate.from_spike_evidence(
        {
            "adapter_recommendation": "continue",
            "verdict": "pass",
            "failure_class": "none",
            "capability_projection": {
                "command_status": {
                    "session_attach": "supported",
                    "pane_spawn": "supported",
                    "send_input": "supported",
                    "read_output": "supported",
                    "kill_pane": "supported",
                    "schema": "supported",
                },
                "semantic_status": {
                    "session_attach": "supported",
                    "pane_spawn": "supported",
                    "send_input": "supported",
                    "read_output": "supported",
                    "kill_pane": "supported",
                    "schema": "supported",
                },
                "windows_beta_gaps": [],
                "blocking_gaps": [],
            },
        },
        capability_report_ref="evidence/herdr-contract-spike-evidence.json",
    )

    capabilities = gate.require_supported("capabilities")

    assert "schema" not in capabilities["command_status"]
    assert "schema" not in capabilities["semantic_status"]


def test_herdr_socket_client_schema_gate_passes_and_records_server_info() -> None:
    client = HerdrSocketClient(
        request_fn=_fake_herdr_request(),
        socket_ref="herdr://local",
    )

    server_info = client.server_info()

    assert server_info["api_schema"] == "Herdr API"
    assert server_info["platform"] == "windows"
    assert server_info["arch"] == "x64"
    assert server_info["socket_ref"] == "herdr://local"


def test_herdr_socket_client_schema_mismatch_is_structured_error() -> None:
    client = HerdrSocketClient(
        request_fn=_fake_herdr_request(server_info={"api_schema": "Unexpected API"}),
        socket_ref="herdr://local",
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        client.server_info()

    assert exc_info.value.category == "schema-mismatch"
    assert exc_info.value.backend_impl == "herdr"
    assert exc_info.value.operation == "server_info"
    assert "expected Herdr contract" in exc_info.value.detail
    assert exc_info.value.evidence["expected_api_schema"] == "Herdr API"
    assert exc_info.value.evidence["actual_api_schema"] == "Unexpected API"


@pytest.mark.parametrize(
    "server_info",
    [
        {"version": ""},
        {"platform": "linux"},
        {"arch": "arm64"},
    ],
)
def test_herdr_socket_client_server_info_gate_rejects_wrong_version_platform_or_arch(
    server_info: dict[str, object],
) -> None:
    client = HerdrSocketClient(
        request_fn=_fake_herdr_request(server_info=server_info),
        socket_ref="herdr://local",
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        client.server_info()

    assert exc_info.value.category == "schema-mismatch"
    assert exc_info.value.evidence["expected_platform"] == "windows"
    assert exc_info.value.evidence["expected_arch"] == "x64"


def test_herdr_socket_client_rejects_scalar_result_as_structured_error() -> None:
    client = HerdrSocketClient(
        request_fn=lambda operation, payload: {"result": "ok"},
        socket_ref="herdr://local",
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        client.server_info()

    assert exc_info.value.category == "command-failed"
    assert exc_info.value.operation == "server_info"


def test_herdr_socket_client_maps_failed_envelope_to_structured_error() -> None:
    client = HerdrSocketClient(
        request_fn=lambda operation, payload: {
            "status": "failed",
            "detail": "workspace create failed",
        },
        socket_ref="herdr://local",
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        client.create_session(project_id="demo", cwd="D:/demo", title="ccb-demo")

    assert exc_info.value.category == "command-failed"
    assert exc_info.value.operation == "create_session"
    assert "workspace create failed" in exc_info.value.detail


def test_herdr_socket_client_checks_outer_failed_status_before_result_unwrap() -> None:
    client = HerdrSocketClient(
        request_fn=lambda operation, payload: {
            "status": "failed",
            "detail": "outer failure",
            "result": {"namespace_id": "workspace-1", "session_name": "ccb-demo"},
        },
        socket_ref="herdr://local",
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        client.create_session(project_id="demo", cwd="D:/demo", title="ccb-demo")

    assert exc_info.value.category == "command-failed"
    assert "outer failure" in exc_info.value.detail


def test_herdr_socket_client_rejects_create_pane_session_mismatch() -> None:
    client = HerdrSocketClient(
        request_fn=lambda operation, payload: {
            "result": {"pane_id": "pane-1", "session_name": "other-session"}
        },
        socket_ref="herdr://local",
    )
    namespace = {
        "backend_family": "herdr-native",
        "backend_impl": "herdr",
        "namespace_id": "workspace-1",
        "session_name": "ccb-demo",
        "ipc_kind": "herdr_socket",
        "ipc_ref": "herdr://local",
        "restore_token": "ccb-demo::workspace-1",
    }

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        client.create_pane(namespace, command=[], cwd="D:/demo", env={}, title="demo")

    assert exc_info.value.category == "command-failed"
    assert exc_info.value.evidence["expected_session_name"] == "ccb-demo"
    assert exc_info.value.evidence["actual_session_name"] == "other-session"


def test_herdr_socket_client_preserves_outer_detail_for_nested_failure() -> None:
    client = HerdrSocketClient(
        request_fn=lambda operation, payload: {
            "status": "ok",
            "detail": "outer diagnostic",
            "result": {"status": "failed", "pane_id": payload["pane_id"]},
        },
        socket_ref="herdr://local",
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        client.kill_pane(
            {
                "backend_impl": "herdr",
                "pane_id": "pane-1",
                "session_name": "ccb-demo",
                "window_name": None,
                "agent_slug": None,
            }
        )

    assert exc_info.value.category == "command-failed"
    assert exc_info.value.detail == "outer diagnostic"


def test_herdr_socket_client_rejects_unknown_status() -> None:
    client = HerdrSocketClient(
        request_fn=lambda operation, payload: {"status": "error", "detail": "unknown status"},
        socket_ref="herdr://local",
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        client.kill_pane(
            {
                "backend_impl": "herdr",
                "pane_id": "pane-1",
                "session_name": "ccb-demo",
                "window_name": None,
                "agent_slug": None,
            }
        )

    assert exc_info.value.category == "command-failed"
    assert exc_info.value.evidence["status"] == "error"


@pytest.mark.parametrize("status", ["unsupported", "transient-unavailable", "not-found"])
def test_herdr_socket_client_preserves_recognized_error_status_categories(status: str) -> None:
    client = HerdrSocketClient(
        request_fn=lambda operation, payload: {"status": status, "detail": "structured failure"},
        socket_ref="herdr://local",
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        client.kill_pane(
            {
                "backend_impl": "herdr",
                "pane_id": "pane-1",
                "session_name": "ccb-demo",
                "window_name": None,
                "agent_slug": None,
            }
        )

    assert exc_info.value.category == status


def test_herdr_socket_client_requires_operation_status() -> None:
    client = HerdrSocketClient(
        request_fn=lambda operation, payload: {"pane_id": "pane-1"},
        socket_ref="herdr://local",
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        client.kill_pane(
            {
                "backend_impl": "herdr",
                "pane_id": "pane-1",
                "session_name": "ccb-demo",
                "window_name": None,
                "agent_slug": None,
            }
        )

    assert exc_info.value.category == "command-failed"
    assert exc_info.value.evidence["status"] == ""


def test_herdr_socket_client_wraps_missing_ref_fields_as_structured_error() -> None:
    client = HerdrSocketClient(
        request_fn=lambda operation, payload: {"session_name": "ccb-demo"},
        socket_ref="herdr://local",
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        client.create_session(project_id="demo", cwd="D:/demo", title="ccb-demo")

    assert exc_info.value.category == "command-failed"
    assert exc_info.value.operation == "create_session"


def test_herdr_socket_client_normalizes_disallowed_session_scoped_namespace_ipc_ref() -> None:
    client = HerdrSocketClient(
        request_fn=lambda operation, payload: {
            "result": {
                "namespace_id": "workspace-1",
                "session_name": "ccb-demo",
                "ipc_ref": "herdr://ccb-demo",
            }
        },
        socket_ref="herdr://override",
    )

    namespace = client.create_session(project_id="demo", cwd="D:/demo", title="ccb-demo")

    assert namespace["ipc_ref"] == "herdr://override"


def test_herdr_socket_client_preserves_allowed_session_scoped_namespace_ipc_ref() -> None:
    client = HerdrSocketClient(
        request_fn=lambda operation, payload: {
            "result": {
                "namespace_id": "workspace-1",
                "session_name": "ccb-demo",
                "ipc_ref": "herdr://ccb-demo",
            }
        },
        socket_ref="herdr://default",
        allow_session_scoped_ipc_refs=True,
    )

    namespace = client.create_session(project_id="demo", cwd="D:/demo", title="ccb-demo")

    assert namespace["ipc_ref"] == "herdr://ccb-demo"


def test_herdr_socket_client_normalizes_foreign_session_scoped_namespace_ipc_ref() -> None:
    client = HerdrSocketClient(
        request_fn=lambda operation, payload: {
            "result": {
                "namespace_id": "workspace-1",
                "session_name": "ccb-demo",
                "ipc_ref": "herdr://foreign",
            }
        },
        socket_ref="herdr://default",
        allow_session_scoped_ipc_refs=True,
    )

    namespace = client.create_session(project_id="demo", cwd="D:/demo", title="ccb-demo")

    assert namespace["ipc_ref"] == "herdr://default"


def test_herdr_socket_client_rejects_restore_response_namespace_mismatch() -> None:
    client = HerdrSocketClient(
        request_fn=lambda operation, payload: {
            "result": {
                "namespace_id": "workspace-2",
                "session_name": "ccb-demo",
                "restore_token": payload["restore_token"],
            }
        },
        socket_ref="herdr://local",
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        client.restore_session(restore_token="ccb-demo::workspace-1")

    assert exc_info.value.category == "command-failed"
    assert exc_info.value.evidence["expected_namespace_id"] == "workspace-1"
    assert exc_info.value.evidence["actual_namespace_id"] == "workspace-2"


def test_herdr_socket_client_rejects_restore_response_session_mismatch() -> None:
    client = HerdrSocketClient(
        request_fn=lambda operation, payload: {
            "result": {
                "namespace_id": "workspace-1",
                "session_name": "other-session",
                "restore_token": payload["restore_token"],
            }
        },
        socket_ref="herdr://local",
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        client.restore_session(restore_token="expected-session::workspace-1")

    assert exc_info.value.category == "command-failed"
    assert exc_info.value.evidence["expected_session_name"] == "expected-session"
    assert exc_info.value.evidence["actual_session_name"] == "other-session"


def test_herdr_socket_client_rejects_restore_response_token_mismatch() -> None:
    client = HerdrSocketClient(
        request_fn=lambda operation, payload: {
            "result": {
                "namespace_id": "workspace-1",
                "session_name": "expected-session",
                "restore_token": "expected-session::workspace-2",
            }
        },
        socket_ref="herdr://local",
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        client.restore_session(restore_token="expected-session::workspace-1")

    assert exc_info.value.category == "command-failed"
    assert exc_info.value.evidence["expected_restore_token"] == "expected-session::workspace-1"
    assert exc_info.value.evidence["actual_restore_token"] == "expected-session::workspace-2"


@pytest.mark.parametrize(
    "response",
    [
        {"session_name": "expected-session", "restore_token": "expected-session::workspace-1"},
        {"namespace_id": "workspace-1", "restore_token": "expected-session::workspace-1"},
        {"namespace_id": "workspace-1", "session_name": "expected-session"},
    ],
)
def test_herdr_socket_client_requires_restore_response_identity_fields(
    response: dict[str, object],
) -> None:
    client = HerdrSocketClient(
        request_fn=lambda operation, payload: {"result": dict(response)},
        socket_ref="herdr://local",
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        client.restore_session(restore_token="expected-session::workspace-1")

    assert exc_info.value.category == "command-failed"
    assert exc_info.value.operation == "restore_session"


@pytest.mark.parametrize("restore_token", ["workspace-1", "::workspace-1", "ccb-demo::", "ccb-demo::workspace-1::extra"])
def test_herdr_socket_client_rejects_restore_token_without_session_scope(restore_token: str) -> None:
    client = HerdrSocketClient(
        request_fn=_fake_herdr_request(),
        socket_ref="herdr://local",
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        client.restore_session(restore_token=restore_token)

    assert exc_info.value.category == "command-failed"
    assert exc_info.value.operation == "restore_session"


@pytest.mark.parametrize(
    ("operation", "invoke"),
    [
        ("server_info", lambda client: client.server_info()),
        (
            "create_session",
            lambda client: client.create_session(project_id="demo", cwd="D:/demo", title="ccb-demo"),
        ),
        ("restore_session", lambda client: client.restore_session(restore_token="ccb-demo::workspace-1")),
        (
            "create_pane",
            lambda client: client.create_pane(
                {
                    "backend_family": "herdr-native",
                    "backend_impl": "herdr",
                    "namespace_id": "workspace-1",
                    "session_name": "ccb-demo",
                    "ipc_kind": "herdr_socket",
                    "ipc_ref": "herdr://local",
                    "restore_token": None,
                },
                command=[],
                cwd="D:/demo",
                env={},
                title="workspace",
            ),
        ),
    ],
)
def test_herdr_socket_client_rejects_wrapped_success_without_result(
    operation: str,
    invoke,
) -> None:
    client = HerdrSocketClient(
        request_fn=lambda requested_operation, payload: {"status": "ok"},
        socket_ref="herdr://local",
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        invoke(client)

    assert exc_info.value.category == "command-failed"
    assert exc_info.value.operation == operation


def test_herdr_backend_facade_returns_refs_and_operation_evidence() -> None:
    backend = HerdrBackend(
        client=HerdrSocketClient(request_fn=_fake_herdr_request(), socket_ref="herdr://local"),
        capability_gate=_supported_gate(),
    )

    namespace = backend.create_session(project_id="demo", cwd="D:/demo", title="ccb-demo")
    restored = backend.restore_session(restore_token=namespace["restore_token"] or "")
    pane = backend.create_pane(
        namespace,
        command=["python", "-V"],
        cwd="D:/demo",
        env={"SECRET_TOKEN": "must-not-leak"},
        title="workspace",
    )
    send = backend.send_text(pane, "secret typed text")
    captured, capture = backend.capture_pane(pane, lines=20)
    killed = backend.kill_pane(pane)

    assert namespace["backend_family"] == "herdr-native"
    assert namespace["backend_impl"] == "herdr"
    assert namespace["ipc_kind"] == "herdr_socket"
    assert namespace["restore_token"] == "ccb-demo::workspace-1"
    assert restored["namespace_id"] == namespace["namespace_id"]
    assert pane["backend_impl"] == "herdr"
    assert pane["pane_id"] == "pane-1"
    assert send["operation"] == "send_text"
    assert send["status"] == "ok"
    assert "secret typed text" not in str(send)
    assert "python ready" in captured
    assert capture["operation"] == "capture_pane"
    assert killed["operation"] == "kill_pane"


def test_herdr_backend_reports_sessions_and_releases_pane_agent() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def request(operation: str, payload: dict[str, object]) -> dict[str, object]:
        calls.append((operation, dict(payload)))
        if operation in {"report_pane_agent", "report_pane_agent_session", "release_pane_agent"}:
            return {"status": "ok", "pane_id": str(payload["pane_id"])}
        return _fake_herdr_request()(operation, payload)

    backend = HerdrBackend(
        client=HerdrSocketClient(request_fn=request, socket_ref="herdr://local"),
        capability_gate=_supported_gate(),
    )
    namespace = backend.create_session(project_id="demo", cwd="D:/demo", title="ccb-demo")
    pane = backend.create_pane(namespace, command=[], cwd="D:/demo", env={}, title="workspace")

    backend.report_pane_agent(
        pane,
        provider_kind="codex",
        state="working",
        seq=11,
        session_id="ccb-session",
        session_path="D:/demo/.ccb/session",
    )
    backend.report_pane_agent_session(
        pane,
        provider_kind="codex",
        seq=12,
        session_id="ccb-session",
        session_path="D:/demo/.ccb/session",
    )
    backend.release_pane_agent(pane, provider_kind="codex", seq=13)

    reported = [call for call in calls if call[0] == "report_pane_agent"]
    sessions = [call for call in calls if call[0] == "report_pane_agent_session"]
    releases = [call for call in calls if call[0] == "release_pane_agent"]
    assert reported[-1][1] == {
        "pane_id": "pane-1",
        "session_name": "ccb-demo",
        "provider_kind": "codex",
        "state": "working",
        "seq": 11,
        "session_id": "ccb-session",
        "session_path": "D:/demo/.ccb/session",
    }
    assert sessions[-1][1] == {
        "pane_id": "pane-1",
        "session_name": "ccb-demo",
        "provider_kind": "codex",
        "seq": 12,
        "session_id": "ccb-session",
        "session_path": "D:/demo/.ccb/session",
    }
    assert releases[-1][1] == {
        "pane_id": "pane-1",
        "session_name": "ccb-demo",
        "provider_kind": "codex",
        "seq": 13,
    }


def test_herdr_backend_updates_liveness_after_kill() -> None:
    backend = HerdrBackend(
        client=HerdrSocketClient(request_fn=_fake_herdr_request(), socket_ref="herdr://local"),
        capability_gate=_supported_gate(),
    )
    namespace = backend.create_session(project_id="demo", cwd="D:/demo", title="ccb-demo")
    pane = backend.create_pane(namespace, command=[], cwd="D:/demo", env={}, title="workspace")

    assert backend.is_alive("pane-1") is True
    backend.kill_pane(pane)

    assert backend.is_alive("pane-1") is False


def test_herdr_backend_attach_namespace_uses_v2_namespace_ref_without_restore_token_leak() -> None:
    payloads: list[dict[str, object]] = []

    def request(operation: str, payload: dict[str, object]) -> dict[str, object]:
        if operation == "attach_namespace":
            payloads.append(dict(payload))
            return {"status": "ok", "namespace_id": payload["namespace_id"]}
        return _fake_herdr_request()(operation, payload)

    backend = HerdrBackend(
        client=HerdrSocketClient(request_fn=request, socket_ref="herdr://local"),
        capability_gate=_supported_gate(),
    )
    namespace = backend.create_session(project_id="demo", cwd="D:/demo", title="ccb-demo")

    evidence = backend.attach_namespace(namespace, window_name="workspace")

    assert evidence["operation"] == "attach_namespace"
    assert evidence["status"] == "ok"
    assert payloads == [
        {
            "namespace_id": "workspace-1",
            "session_name": "ccb-demo",
            "ipc_ref": "herdr://local",
            "window_name": "workspace",
        }
    ]
    assert "restore_token" not in str(payloads)


def test_herdr_backend_liveness_probe_clears_stale_pane() -> None:
    def request(operation: str, payload: dict[str, object]) -> dict[str, object]:
        if operation == "capture_pane":
            raise MuxCommandErrorV2(
                category="not-found",
                backend_impl="herdr",
                operation="capture_pane",
                detail="pane not found",
            )
        return _fake_herdr_request()(operation, payload)

    backend = HerdrBackend(
        client=HerdrSocketClient(request_fn=request, socket_ref="herdr://local"),
        capability_gate=_supported_gate(),
    )
    namespace = backend.create_session(project_id="demo", cwd="D:/demo", title="ccb-demo")
    pane = backend.create_pane(namespace, command=[], cwd="D:/demo", env={}, title="workspace")

    assert backend.is_alive(pane["pane_id"]) is False


def test_herdr_backend_legacy_create_pane_uses_lazy_session_and_returns_pane_id() -> None:
    payloads: list[dict[str, object]] = []

    def request(operation: str, payload: dict[str, object]) -> dict[str, object]:
        if operation == "create_pane":
            payloads.append(payload)
        return _fake_herdr_request()(operation, payload)

    backend = HerdrBackend(
        client=HerdrSocketClient(request_fn=request, socket_ref="herdr://local"),
        capability_gate=_supported_gate(),
    )

    pane_id = backend.create_pane("python -V", "D:/demo")

    assert pane_id == "pane-1"
    assert backend.is_alive(pane_id) is True
    assert payloads[0]["command"] == ["python -V"]


def test_herdr_backend_legacy_create_pane_preserves_explicit_command_kwarg() -> None:
    payloads: list[dict[str, object]] = []

    def request(operation: str, payload: dict[str, object]) -> dict[str, object]:
        if operation == "create_pane":
            payloads.append(payload)
        return _fake_herdr_request()(operation, payload)

    backend = HerdrBackend(
        client=HerdrSocketClient(request_fn=request, socket_ref="herdr://local"),
        capability_gate=_supported_gate(),
    )

    pane_id = backend.create_pane("python", "D:/demo", command=["python", "-V"])

    assert pane_id == "pane-1"
    assert payloads[0]["command"] == ["python", "-V"]


def test_herdr_backend_legacy_namespaces_are_keyed_by_cwd() -> None:
    create_session_payloads: list[dict[str, object]] = []

    def request(operation: str, payload: dict[str, object]) -> dict[str, object]:
        if operation == "create_session":
            create_session_payloads.append(payload)
        return _fake_herdr_request()(operation, payload)

    backend = HerdrBackend(
        client=HerdrSocketClient(request_fn=request, socket_ref="herdr://local"),
        capability_gate=_supported_gate(),
    )

    backend.create_pane("python -V", "D:/one")
    backend.create_pane("python -V", "D:/two")
    backend.create_pane("python -V", "D:/one")

    assert [payload["cwd"] for payload in create_session_payloads] == ["D:/one", "D:/two"]


def test_herdr_backend_legacy_parent_pane_must_be_known() -> None:
    backend = HerdrBackend(
        client=HerdrSocketClient(request_fn=_fake_herdr_request(), socket_ref="herdr://local"),
        capability_gate=_supported_gate(),
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        backend.create_pane("python -V", "D:/demo", parent_pane="missing-pane")

    assert exc_info.value.category == "not-found"
    assert exc_info.value.operation == "create_pane"


def test_herdr_backend_threads_split_geometry_to_client() -> None:
    create_pane_payloads: list[dict[str, object]] = []

    def request(operation: str, payload: dict[str, object]) -> dict[str, object]:
        if operation == "create_pane":
            create_pane_payloads.append(payload)
        return _fake_herdr_request()(operation, payload)

    backend = HerdrBackend(
        client=HerdrSocketClient(request_fn=request, socket_ref="herdr://local"),
        capability_gate=_supported_gate(),
    )
    namespace = backend.create_session(project_id="demo", cwd="D:/demo", title="ccb-demo")
    pane = backend.create_pane(namespace, command=[], cwd="D:/demo", env={}, title="root")

    backend.split_pane(pane, direction="down", percent=25, command=[], cwd="D:/demo", env={}, title="child")

    assert create_pane_payloads[1]["direction"] == "down"
    assert create_pane_payloads[1]["percent"] == 25
    assert create_pane_payloads[1]["parent_pane"] == pane["pane_id"]


def test_herdr_backend_namespace_create_pane_preserves_parent_pane() -> None:
    create_pane_payloads: list[dict[str, object]] = []

    def request(operation: str, payload: dict[str, object]) -> dict[str, object]:
        if operation == "create_pane":
            create_pane_payloads.append(payload)
        return _fake_herdr_request()(operation, payload)

    backend = HerdrBackend(
        client=HerdrSocketClient(request_fn=request, socket_ref="herdr://local"),
        capability_gate=_supported_gate(),
    )
    namespace = backend.create_session(project_id="demo", cwd="D:/demo", title="ccb-demo")

    backend.create_pane(
        namespace,
        parent_pane="pane-root",
        direction="down",
        percent=25,
        command=[],
        cwd="D:/demo",
        env={},
        title="child",
    )

    assert create_pane_payloads[0]["parent_pane"] == "pane-root"
    assert create_pane_payloads[0]["direction"] == "down"
    assert create_pane_payloads[0]["percent"] == 25


def test_herdr_backend_rejects_invalid_namespace_ref_dict() -> None:
    backend = HerdrBackend(
        client=HerdrSocketClient(request_fn=_fake_herdr_request(), socket_ref="herdr://local"),
        capability_gate=_supported_gate(),
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        backend.create_pane({"namespace_id": "workspace-1"}, cwd="D:/demo")

    assert exc_info.value.category == "command-failed"
    assert exc_info.value.operation == "create_pane"

    with pytest.raises(MuxCommandErrorV2):
        backend.create_pane(
            {
                "backend_impl": "herdr",
                "namespace_id": "workspace-1",
                "session_name": "ccb-demo",
                "ipc_kind": "socket_path",
                "ipc_ref": "C:/tmp/herdr.sock",
                "restore_token": None,
            },
            cwd="D:/demo",
        )


def test_herdr_backend_explicit_socket_override_rejects_session_derived_namespace_ref() -> None:
    backend = HerdrBackend(
        client=HerdrSocketClient(request_fn=_fake_herdr_request(), socket_ref="herdr://override"),
        capability_gate=_supported_gate(),
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        backend.create_pane(
            {
                "backend_family": "herdr-native",
                "backend_impl": "herdr",
                "namespace_id": "workspace-1",
                "session_name": "ccb-demo",
                "ipc_kind": "herdr_socket",
                "ipc_ref": "herdr://ccb-demo",
                "restore_token": None,
            },
            cwd="D:/demo",
        )

    assert exc_info.value.category == "command-failed"


def test_herdr_backend_allows_session_scoped_namespace_ref_when_client_declares_it() -> None:
    payloads: list[dict[str, object]] = []

    def request(operation: str, payload: dict[str, object]) -> dict[str, object]:
        if operation == "create_pane":
            payloads.append(payload)
        return _fake_herdr_request()(operation, payload)

    backend = HerdrBackend(
        client=HerdrSocketClient(
            request_fn=request,
            socket_ref="herdr://ccb-demo",
            allow_session_scoped_ipc_refs=True,
        ),
        capability_gate=_supported_gate(),
    )

    backend.create_pane(
        {
            "backend_family": "herdr-native",
            "backend_impl": "herdr",
            "namespace_id": "workspace-1",
            "session_name": "restored-session",
            "ipc_kind": "herdr_socket",
            "ipc_ref": "herdr://restored-session",
            "restore_token": "restored-session::workspace-1",
        },
        cwd="D:/demo",
    )

    assert payloads[0]["session_name"] == "restored-session"
    assert payloads[0]["ipc_ref"] == "herdr://ccb-demo"
    with pytest.raises(MuxCommandErrorV2):
        backend.create_pane(
            {
                "backend_impl": "herdr",
                "namespace_id": "workspace-1",
                "session_name": "ccb-demo",
                "ipc_kind": "herdr_socket",
                "ipc_ref": "herdr://foreign",
                "restore_token": None,
            },
            cwd="D:/demo",
        )


def test_herdr_backend_capture_rejects_foreign_pane_ref() -> None:
    backend = HerdrBackend(
        client=HerdrSocketClient(request_fn=_fake_herdr_request(), socket_ref="herdr://local"),
        capability_gate=_supported_gate(),
    )
    namespace = backend.create_session(project_id="demo", cwd="D:/demo", title="ccb-demo")
    backend.create_pane(namespace, command=[], cwd="D:/demo", env={}, title="workspace")

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        backend.capture_pane(
            {
                "backend_impl": "herdr",
                "pane_id": "pane-1",
                "session_name": "foreign-session",
                "window_name": None,
                "agent_slug": None,
            },
            lines=10,
        )

    assert exc_info.value.category == "not-found"
    assert exc_info.value.operation == "capture_pane"


def test_herdr_backend_accepts_uncached_v2_pane_ref() -> None:
    captured_payloads: list[dict[str, object]] = []

    def request(operation: str, payload: dict[str, object]) -> dict[str, object]:
        if operation == "capture_pane":
            captured_payloads.append(payload)
            return {"status": "ok", "pane_id": payload["pane_id"], "text": "restored ready"}
        return _fake_herdr_request()(operation, payload)

    backend = HerdrBackend(
        client=HerdrSocketClient(request_fn=request, socket_ref="herdr://local"),
        capability_gate=_supported_gate(),
    )
    backend.namespace_ref("restored-session", "workspace-1")

    text, evidence = backend.capture_pane(
        {
            "backend_impl": "herdr",
            "pane_id": "restored-pane",
            "session_name": "restored-session",
            "window_name": None,
            "agent_slug": None,
        },
        lines=10,
    )

    assert text == "restored ready"
    assert evidence["status"] == "ok"
    assert captured_payloads[0]["session_name"] == "restored-session"


def test_herdr_backend_rejects_uncached_v2_pane_ref_without_known_namespace() -> None:
    backend = HerdrBackend(
        client=HerdrSocketClient(request_fn=_fake_herdr_request(), socket_ref="herdr://local"),
        capability_gate=_supported_gate(),
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        backend.capture_pane(
            {
                "backend_impl": "herdr",
                "pane_id": "foreign-pane",
                "session_name": "foreign-session",
                "window_name": None,
                "agent_slug": None,
            },
            lines=10,
        )

    assert exc_info.value.category == "not-found"


def test_herdr_backend_io_operations_run_schema_gate_for_uncached_v2_ref() -> None:
    def request(operation: str, payload: dict[str, object]) -> dict[str, object]:
        if operation == "server_info":
            return {
                "version": "herdr 0.7.5-preview",
                "api_schema": "Unexpected API",
                "platform": "windows",
                "arch": "x64",
            }
        raise AssertionError(f"unexpected operation after schema mismatch: {operation}")

    backend = HerdrBackend(
        client=HerdrSocketClient(request_fn=request, socket_ref="herdr://local"),
        capability_gate=_supported_gate(),
    )
    backend.namespace_ref("restored-session", "workspace-1")

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        backend.capture_pane(
            {
                "backend_impl": "herdr",
                "pane_id": "restored-pane",
                "session_name": "restored-session",
                "window_name": None,
                "agent_slug": None,
            },
            lines=10,
        )

    assert exc_info.value.category == "schema-mismatch"


def test_herdr_backend_liveness_transient_failure_does_not_evict_pane() -> None:
    def request(operation: str, payload: dict[str, object]) -> dict[str, object]:
        if operation == "capture_pane":
            raise MuxCommandErrorV2(
                category="transient-unavailable",
                backend_impl="herdr",
                operation="capture_pane",
                detail="temporary failure",
            )
        return _fake_herdr_request()(operation, payload)

    backend = HerdrBackend(
        client=HerdrSocketClient(request_fn=request, socket_ref="herdr://local"),
        capability_gate=_supported_gate(),
    )
    namespace = backend.create_session(project_id="demo", cwd="D:/demo", title="ccb-demo")
    pane = backend.create_pane(namespace, command=[], cwd="D:/demo", env={}, title="workspace")

    assert backend.is_alive(pane["pane_id"]) is True
    assert backend._panes[pane["pane_id"]] == pane


def test_herdr_backend_liveness_schema_mismatch_fails_closed() -> None:
    def request(operation: str, payload: dict[str, object]) -> dict[str, object]:
        if operation == "server_info":
            return {
                "version": "herdr 0.7.5-preview",
                "api_schema": "Unexpected API",
                "platform": "windows",
                "arch": "x64",
            }
        return _fake_herdr_request()(operation, payload)

    backend = HerdrBackend(
        client=HerdrSocketClient(request_fn=request, socket_ref="herdr://local"),
        capability_gate=_supported_gate(),
    )
    backend._panes["pane-1"] = {
        "backend_impl": "herdr",
        "pane_id": "pane-1",
        "session_name": "ccb-demo",
        "window_name": None,
        "agent_slug": None,
    }

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        backend.is_alive("pane-1")

    assert exc_info.value.category == "schema-mismatch"


def test_herdr_backend_is_alive_accepts_v2_pane_ref_with_known_namespace() -> None:
    captured_payloads: list[dict[str, object]] = []

    def request(operation: str, payload: dict[str, object]) -> dict[str, object]:
        if operation == "capture_pane":
            captured_payloads.append(payload)
            return {"status": "ok", "pane_id": payload["pane_id"], "text": "ready"}
        return _fake_herdr_request()(operation, payload)

    backend = HerdrBackend(
        client=HerdrSocketClient(request_fn=request, socket_ref="herdr://local"),
        capability_gate=_supported_gate(),
    )
    backend.namespace_ref("restored-session", "workspace-1")

    assert backend.is_alive(
        {
            "backend_impl": "herdr",
            "pane_id": "restored-pane",
            "session_name": "restored-session",
            "window_name": None,
            "agent_slug": None,
        }
    ) is True
    assert captured_payloads[0]["pane_id"] == "restored-pane"


def test_herdr_backend_is_alive_rejects_v2_pane_ref_without_known_namespace() -> None:
    backend = HerdrBackend(
        client=HerdrSocketClient(request_fn=_fake_herdr_request(), socket_ref="herdr://local"),
        capability_gate=_supported_gate(),
    )

    assert backend.is_alive(
        {
            "backend_impl": "herdr",
            "pane_id": "foreign-pane",
            "session_name": "foreign-session",
            "window_name": None,
            "agent_slug": None,
        }
    ) is False


def test_herdr_backend_namespace_ref_is_local_builder() -> None:
    calls: list[str] = []

    def request(operation: str, payload: dict[str, object]) -> dict[str, object]:
        calls.append(operation)
        return _fake_herdr_request()(operation, payload)

    backend = HerdrBackend(
        client=HerdrSocketClient(request_fn=request, socket_ref="herdr://local"),
        capability_gate=_supported_gate(),
    )

    namespace = backend.namespace_ref("ccb-demo", "workspace-1")

    assert namespace["namespace_id"] == "workspace-1"
    assert namespace["ipc_kind"] == "herdr_socket"
    assert calls == []


def test_herdr_backend_split_unknown_pane_is_structured_not_found() -> None:
    backend = HerdrBackend(
        client=HerdrSocketClient(request_fn=_fake_herdr_request(), socket_ref="herdr://local"),
        capability_gate=_supported_gate(),
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        backend.split_pane(
            {
                "backend_impl": "herdr",
                "pane_id": "restored-pane",
                "session_name": "ccb-demo",
                "window_name": None,
                "agent_slug": None,
            }
        )

    assert exc_info.value.category == "not-found"
    assert exc_info.value.operation == "split_pane"


def test_herdr_backend_split_accepts_v2_pane_ref_with_known_namespace() -> None:
    create_pane_payloads: list[dict[str, object]] = []

    def request(operation: str, payload: dict[str, object]) -> dict[str, object]:
        if operation == "create_pane":
            create_pane_payloads.append(payload)
        return _fake_herdr_request()(operation, payload)

    backend = HerdrBackend(
        client=HerdrSocketClient(request_fn=request, socket_ref="herdr://local"),
        capability_gate=_supported_gate(),
    )
    backend.namespace_ref("restored-session", "workspace-1")

    pane = backend.split_pane(
        {
            "backend_impl": "herdr",
            "pane_id": "restored-pane",
            "session_name": "restored-session",
            "window_name": None,
            "agent_slug": None,
        },
        direction="down",
        percent=25,
        command=[],
        cwd="D:/demo",
        env={},
        title="child",
    )

    assert pane["pane_id"] == "pane-1"
    assert create_pane_payloads[0]["parent_pane"] == "restored-pane"
    assert create_pane_payloads[0]["session_name"] == "restored-session"


def test_herdr_backend_split_accepts_project_namespace_ref_without_known_namespace_cache() -> None:
    create_pane_payloads: list[dict[str, object]] = []

    def request(operation: str, payload: dict[str, object]) -> dict[str, object]:
        if operation == "create_pane":
            create_pane_payloads.append(payload)
        return _fake_herdr_request()(operation, payload)

    backend = HerdrBackend(
        client=HerdrSocketClient(request_fn=request, socket_ref="herdr://local"),
        capability_gate=_supported_gate(),
    )
    backend._ccb_project_namespace_ref = {  # type: ignore[attr-defined]
        "backend_impl": "herdr",
        "backend_family": "herdr-native",
        "namespace_id": "workspace-1",
        "session_name": "restored-session",
        "ipc_kind": "herdr_socket",
        "ipc_ref": "herdr://local",
        "restore_token": "restored-session::workspace-1",
    }

    pane = backend.split_pane(
        {
            "backend_impl": "herdr",
            "pane_id": "restored-pane",
            "session_name": "restored-session",
            "window_name": None,
            "agent_slug": None,
        },
        direction="down",
        percent=25,
        command=[],
        cwd="D:/demo",
        env={},
        title="child",
    )

    assert pane["pane_id"] == "pane-1"
    assert create_pane_payloads[0]["namespace_id"] == "workspace-1"
    assert create_pane_payloads[0]["parent_pane"] == "restored-pane"


def test_herdr_backend_split_rejects_ambiguous_known_namespace() -> None:
    create_pane_payloads: list[dict[str, object]] = []

    def request(operation: str, payload: dict[str, object]) -> dict[str, object]:
        if operation == "create_pane":
            create_pane_payloads.append(payload)
        return _fake_herdr_request()(operation, payload)

    backend = HerdrBackend(
        client=HerdrSocketClient(request_fn=request, socket_ref="herdr://local"),
        capability_gate=_supported_gate(),
    )
    backend.namespace_ref("restored-session", "workspace-1")
    backend.namespace_ref("restored-session", "workspace-2")

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        backend.split_pane(
            {
                "backend_impl": "herdr",
                "pane_id": "restored-pane",
                "session_name": "restored-session",
                "window_name": None,
                "agent_slug": None,
            },
            command=[],
            cwd="D:/demo",
            env={},
        )

    assert exc_info.value.category == "not-found"
    assert "ambiguous" in exc_info.value.detail
    assert create_pane_payloads == []


def test_herdr_backend_send_and_kill_unknown_pane_are_structured_not_found() -> None:
    backend = HerdrBackend(
        client=HerdrSocketClient(request_fn=_fake_herdr_request(), socket_ref="herdr://local"),
        capability_gate=_supported_gate(),
    )

    with pytest.raises(MuxCommandErrorV2) as send_exc:
        backend.send_text("missing-pane", "hello")
    with pytest.raises(MuxCommandErrorV2) as kill_exc:
        backend.kill_pane("missing-pane")

    assert send_exc.value.category == "not-found"
    assert send_exc.value.operation == "send_text"
    assert kill_exc.value.category == "not-found"
    assert kill_exc.value.operation == "kill_pane"


def test_herdr_backend_activate_is_structured_unsupported() -> None:
    backend = HerdrBackend(
        client=HerdrSocketClient(request_fn=_fake_herdr_request(), socket_ref="herdr://local"),
        capability_gate=_supported_gate(),
    )
    namespace = backend.create_session(project_id="demo", cwd="D:/demo", title="ccb-demo")
    pane = backend.create_pane(namespace, command=[], cwd="D:/demo", env={}, title="workspace")

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        backend.activate(pane["pane_id"])

    assert exc_info.value.category == "unsupported"
    assert exc_info.value.operation == "activate"


def test_herdr_backend_rejects_foreign_pane_ref() -> None:
    backend = HerdrBackend(
        client=HerdrSocketClient(request_fn=_fake_herdr_request(), socket_ref="herdr://local"),
        capability_gate=_supported_gate(),
    )
    namespace = backend.create_session(project_id="demo", cwd="D:/demo", title="ccb-demo")
    backend.create_pane(namespace, command=[], cwd="D:/demo", env={}, title="workspace")

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        backend.send_text(
            {
                "backend_impl": "herdr",
                "pane_id": "pane-1",
                "session_name": "foreign-session",
                "window_name": None,
                "agent_slug": None,
            },
            "hello",
        )

    assert exc_info.value.category == "not-found"
    assert exc_info.value.operation == "send_text"


def test_terminal_backend_selection_creates_herdr_only_after_gates_pass() -> None:
    created: list[str] = []
    selection = TerminalBackendSelection(
        detect_terminal_fn=lambda: "herdr",
        tmux_backend_factory=lambda: "tmux",
        herdr_backend_factory=lambda: created.append("herdr") or "herdr-backend",
        platform_gate_fn=_windows_x64_platform_gate,
        herdr_capability_report_fn=lambda: _supported_gate().capabilities,
        herdr_capability_report_ref_fn=lambda: "evidence/herdr-capabilities.json",
    )

    assert selection.get_backend("herdr") == "herdr-backend"
    assert created == ["herdr"]


def test_terminal_backend_selection_fails_closed_without_herdr_capability_evidence() -> None:
    selection = TerminalBackendSelection(
        detect_terminal_fn=lambda: "herdr",
        tmux_backend_factory=lambda: "tmux",
        herdr_backend_factory=lambda: "herdr-backend",
        platform_gate_fn=_windows_x64_platform_gate,
        herdr_capability_report_fn=lambda: None,
        herdr_capability_report_ref_fn=lambda: None,
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        selection.get_backend("herdr")

    assert exc_info.value.category == "unsupported"
    assert exc_info.value.operation == "select_backend"
    assert exc_info.value.evidence["selection"]["failure_reason"] == "herdr-capability-missing"


def test_terminal_backend_selection_does_not_return_stale_tmux_for_explicit_herdr() -> None:
    selection = TerminalBackendSelection(
        detect_terminal_fn=lambda: "tmux",
        tmux_backend_factory=lambda: "tmux",
        herdr_backend_factory=lambda: "herdr-backend",
        platform_gate_fn=_windows_x64_platform_gate,
        herdr_capability_report_fn=lambda: _supported_gate().capabilities,
        herdr_capability_report_ref_fn=lambda: "evidence/herdr-capabilities.json",
    )

    assert selection.get_backend("tmux") == "tmux"
    assert selection.get_backend("herdr") == "herdr-backend"


def test_terminal_backend_selection_does_not_return_stale_herdr_for_explicit_tmux() -> None:
    selection = TerminalBackendSelection(
        detect_terminal_fn=lambda: "herdr",
        tmux_backend_factory=lambda: "tmux",
        herdr_backend_factory=lambda: "herdr-backend",
        platform_gate_fn=_windows_x64_platform_gate,
        herdr_capability_report_fn=lambda: _supported_gate().capabilities,
        herdr_capability_report_ref_fn=lambda: "evidence/herdr-capabilities.json",
    )

    assert selection.get_backend("herdr") == "herdr-backend"
    assert selection.get_backend("tmux") == "tmux"


def test_terminal_backend_selection_preserves_non_windows_auto_legacy_fallback() -> None:
    selection = TerminalBackendSelection(
        detect_terminal_fn=lambda: "auto",
        tmux_backend_factory=lambda: "tmux",
        herdr_backend_factory=lambda: "herdr-backend",
        platform_gate_fn=lambda: {"supported": False, "os_platform": "linux", "cpu_arch": "x64"},
        herdr_capability_report_fn=lambda: None,
        herdr_capability_report_ref_fn=lambda: None,
    )

    assert selection.get_backend("auto") == "tmux"


def test_terminal_backend_selection_auto_blocks_when_herdr_schema_gate_fails() -> None:
    backend = HerdrBackend(
        client=HerdrSocketClient(
            request_fn=_fake_herdr_request(server_info={"api_schema": "Unexpected API"}),
            socket_ref="herdr://local",
        ),
        capability_gate=_supported_gate(),
    )
    selection = TerminalBackendSelection(
        detect_terminal_fn=lambda: "auto",
        tmux_backend_factory=lambda: "tmux",
        herdr_backend_factory=lambda: backend,
        platform_gate_fn=_windows_x64_platform_gate,
        herdr_capability_report_fn=lambda: _supported_gate().capabilities,
        herdr_capability_report_ref_fn=lambda: "evidence/herdr-capabilities.json",
    )

    assert selection.get_backend("auto") is None


def test_terminal_backend_selection_preserves_explicit_herdr_prepare_failure() -> None:
    backend = _PrepareFailsBackend()
    selection = TerminalBackendSelection(
        detect_terminal_fn=lambda: "herdr",
        tmux_backend_factory=lambda: "tmux",
        herdr_backend_factory=lambda: backend,
        platform_gate_fn=_windows_x64_platform_gate,
        herdr_capability_report_fn=lambda: _supported_gate().capabilities,
        herdr_capability_report_ref_fn=lambda: "evidence/herdr-capabilities.json",
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        selection.get_backend("herdr")

    assert exc_info.value.category == "schema-mismatch"
    assert exc_info.value.operation == "server_info"
    assert backend.prepare_calls == 1


def test_terminal_backend_selection_auto_returns_none_on_herdr_prepare_failure() -> None:
    backend = _PrepareFailsBackend()
    selection = TerminalBackendSelection(
        detect_terminal_fn=lambda: "auto",
        tmux_backend_factory=lambda: "tmux",
        herdr_backend_factory=lambda: backend,
        platform_gate_fn=_windows_x64_platform_gate,
        herdr_capability_report_fn=lambda: _supported_gate().capabilities,
        herdr_capability_report_ref_fn=lambda: "evidence/herdr-capabilities.json",
    )

    assert selection.get_backend("auto") is None
    assert backend.prepare_calls == 1


def test_terminal_backend_selection_auto_returns_none_on_herdr_factory_failure() -> None:
    selection = TerminalBackendSelection(
        detect_terminal_fn=lambda: "auto",
        tmux_backend_factory=lambda: "tmux",
        herdr_backend_factory=lambda: (_ for _ in ()).throw(RuntimeError("factory failed")),
        platform_gate_fn=_windows_x64_platform_gate,
        herdr_capability_report_fn=lambda: _supported_gate().capabilities,
        herdr_capability_report_ref_fn=lambda: "evidence/herdr-capabilities.json",
    )

    assert selection.get_backend("auto") is None


def test_terminal_backend_selection_explicit_herdr_wraps_factory_failure() -> None:
    selection = TerminalBackendSelection(
        detect_terminal_fn=lambda: "auto",
        tmux_backend_factory=lambda: "tmux",
        herdr_backend_factory=lambda: (_ for _ in ()).throw(RuntimeError("factory failed")),
        platform_gate_fn=_windows_x64_platform_gate,
        herdr_capability_report_fn=lambda: _supported_gate().capabilities,
        herdr_capability_report_ref_fn=lambda: "evidence/herdr-capabilities.json",
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        selection.get_backend("herdr")

    assert exc_info.value.category == "transient-unavailable"
    assert exc_info.value.operation == "select_backend"


def test_terminal_backend_selection_platform_default_routes_herdr_when_detect_is_empty() -> None:
    backend = _PreparedBackend()
    selection = TerminalBackendSelection(
        detect_terminal_fn=lambda: None,
        tmux_backend_factory=lambda: "tmux",
        herdr_backend_factory=lambda: backend,
        platform_gate_fn=_windows_x64_platform_gate,
        herdr_capability_report_fn=lambda: _supported_gate().capabilities,
        herdr_capability_report_ref_fn=lambda: "evidence/herdr-capabilities.json",
    )

    assert selection.get_backend() is backend
    assert backend.prepare_calls == 1


def test_terminal_backend_selection_platform_default_prepare_failure_returns_none() -> None:
    backend = _PrepareFailsBackend()
    selection = TerminalBackendSelection(
        detect_terminal_fn=lambda: None,
        tmux_backend_factory=lambda: "tmux",
        herdr_backend_factory=lambda: backend,
        platform_gate_fn=_windows_x64_platform_gate,
        herdr_capability_report_fn=lambda: _supported_gate().capabilities,
        herdr_capability_report_ref_fn=lambda: "evidence/herdr-capabilities.json",
    )

    assert selection.get_backend() is None
    assert backend.prepare_calls == 1


def test_terminal_backend_selection_implicit_failure_does_not_cache_none() -> None:
    failing_backend = _PrepareFailsBackend()
    prepared_backend = _PreparedBackend()
    factories = [lambda: failing_backend, lambda: prepared_backend]
    selection = TerminalBackendSelection(
        detect_terminal_fn=lambda: None,
        tmux_backend_factory=lambda: "tmux",
        herdr_backend_factory=lambda: factories.pop(0)(),
        platform_gate_fn=_windows_x64_platform_gate,
        herdr_capability_report_fn=lambda: _supported_gate().capabilities,
        herdr_capability_report_ref_fn=lambda: "evidence/herdr-capabilities.json",
    )

    assert selection.get_backend() is None
    assert selection.get_backend() is prepared_backend
    assert prepared_backend.prepare_calls == 1


def test_terminal_backend_selection_rechecks_dynamic_herdr_inputs_after_tmux_cache() -> None:
    detected = ["tmux", None]
    selection = TerminalBackendSelection(
        detect_terminal_fn=lambda: detected.pop(0),
        tmux_backend_factory=lambda: "tmux",
        herdr_backend_factory=lambda: "herdr",
        platform_gate_fn=_windows_x64_platform_gate,
        herdr_capability_report_fn=lambda: _supported_gate().capabilities,
        herdr_capability_report_ref_fn=lambda: "evidence/herdr-capabilities.json",
    )

    assert selection.get_backend() == "tmux"
    assert selection.get_backend() == "herdr"


def test_terminal_backend_selection_explicit_request_does_not_pollute_cache() -> None:
    selection = TerminalBackendSelection(
        detect_terminal_fn=lambda: "tmux",
        tmux_backend_factory=lambda: "tmux",
        herdr_backend_factory=lambda: "herdr",
        platform_gate_fn=_windows_x64_platform_gate,
        herdr_capability_report_fn=lambda: _supported_gate().capabilities,
        herdr_capability_report_ref_fn=lambda: "evidence/herdr-capabilities.json",
    )

    assert selection.get_backend("herdr") == "herdr"
    assert selection.cached_backend is None
    assert selection.get_backend() == "tmux"
    assert selection.cached_backend == "tmux"


def test_terminal_backend_selection_reuses_explicit_backend_without_implicit_cache_pollution() -> None:
    created: list[str] = []
    selection = TerminalBackendSelection(
        detect_terminal_fn=lambda: "tmux",
        tmux_backend_factory=lambda: "tmux",
        herdr_backend_factory=lambda: created.append("herdr") or _PreparedBackend(),
        platform_gate_fn=_windows_x64_platform_gate,
        herdr_capability_report_fn=lambda: _supported_gate().capabilities,
        herdr_capability_report_ref_fn=lambda: "evidence/herdr-capabilities.json",
    )

    first = selection.get_backend("herdr")
    second = selection.get_backend("herdr")

    assert first is second
    assert created == ["herdr"]
    assert selection.cached_backend is None


def test_terminal_backend_selection_non_windows_platform_default_falls_back_to_tmux() -> None:
    selection = TerminalBackendSelection(
        detect_terminal_fn=lambda: None,
        tmux_backend_factory=lambda: "tmux",
        herdr_backend_factory=lambda: "herdr",
        platform_gate_fn=lambda: {"supported": False, "os_platform": "linux", "cpu_arch": "x64"},
        herdr_capability_report_fn=lambda: None,
        herdr_capability_report_ref_fn=lambda: None,
    )

    assert selection.get_backend() == "tmux"


def test_terminal_api_get_backend_threads_production_herdr_wiring(monkeypatch) -> None:
    monkeypatch.setattr(terminal_api, "_backend_cache", None)
    monkeypatch.setattr(terminal_api, "_herdr_platform_gate", _windows_x64_platform_gate)
    monkeypatch.setattr(terminal_api, "_herdr_capability_report", lambda: _supported_gate().capabilities)
    monkeypatch.setattr(terminal_api, "_herdr_capability_report_ref", lambda: "evidence/herdr-capabilities.json")
    monkeypatch.setattr(terminal_api, "_herdr_request_adapter", lambda: _FakeRequestAdapter())

    backend = terminal_api.get_backend("herdr")

    assert isinstance(backend, HerdrBackend)


def test_terminal_api_get_backend_for_session_reattaches_persisted_herdr_pane_without_env(
    monkeypatch,
) -> None:
    monkeypatch.delenv("CCB_HERDR_CAPABILITY_REPORT", raising=False)
    monkeypatch.setattr(terminal_api, "_herdr_request_adapter", lambda: _FakeRequestAdapter())

    backend = terminal_api.get_backend_for_session(
        {
            "terminal": "mux",
            "backend_impl": "herdr",
            "namespace_ref": {
                "backend_family": "herdr-native",
                "backend_impl": "herdr",
                "namespace_id": "wC",
                "session_name": "ccb-demo",
                "ipc_kind": "herdr_socket",
                "ipc_ref": "herdr://local",
            },
            "pane_id": "wC:p1",
        }
    )

    assert isinstance(backend, HerdrBackend)
    assert backend.is_alive("wC:p1") is True
    backend.send_text("wC:p1", "secret typed text")
    assert getattr(backend, "_ccb_project_namespace_ref")["namespace_id"] == "wC"
    backend._capability_gate.require_supported("capture_pane")
    backend._capability_gate.require_supported("send_text")


def test_terminal_api_get_backend_for_session_preserves_stricter_live_capability_report(
    monkeypatch,
) -> None:
    capabilities = dict(_supported_gate().capabilities or {})
    capabilities["command_status"] = dict(capabilities["command_status"])
    capabilities["semantic_status"] = dict(capabilities["semantic_status"])
    capabilities["command_status"]["send_input"] = "unsupported"
    capabilities["semantic_status"]["send_input"] = "unsupported"
    monkeypatch.setattr(terminal_api, "_herdr_capability_report", lambda: capabilities)
    monkeypatch.setattr(terminal_api, "_herdr_request_adapter", lambda: _FakeRequestAdapter())

    backend = terminal_api.get_backend_for_session(
        {
            "terminal": "mux",
            "backend_impl": "herdr",
            "namespace_ref": {
                "backend_family": "herdr-native",
                "backend_impl": "herdr",
                "namespace_id": "wC",
                "session_name": "ccb-demo",
                "ipc_kind": "herdr_socket",
                "ipc_ref": "herdr://local",
            },
            "pane_id": "wC:p1",
        }
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        backend.send_text("wC:p1", "secret typed text")

    assert exc_info.value.category == "unsupported"


def test_terminal_api_get_backend_herdr_defaults_fail_closed(monkeypatch) -> None:
    monkeypatch.setattr(terminal_api, "_backend_cache", None)
    monkeypatch.delenv("CCB_HERDR_CAPABILITY_REPORT", raising=False)
    monkeypatch.delenv("CCB_HERDR_SOCKET_REF", raising=False)

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        terminal_api.get_backend("herdr")

    assert exc_info.value.operation == "select_backend"


def test_terminal_api_get_backend_auto_preserves_non_windows_tmux(monkeypatch) -> None:
    monkeypatch.setattr(terminal_api, "_backend_cache", None)
    monkeypatch.setattr(terminal_api, "_backend_cache_key", None)
    monkeypatch.setattr(
        terminal_api,
        "_herdr_platform_gate",
        lambda: {"supported": False, "os_platform": "linux", "cpu_arch": "x64"},
    )
    monkeypatch.setattr(terminal_api, "_herdr_capability_report", lambda: None)
    monkeypatch.setattr(terminal_api, "TmuxBackend", lambda: "tmux")

    assert terminal_api.get_backend("auto") == "tmux"


def test_terminal_api_get_backend_rechecks_auto_after_tmux_cache(monkeypatch) -> None:
    detected = ["tmux", None]
    monkeypatch.delenv("CCB_RUNTIME_MUX_BACKEND", raising=False)
    monkeypatch.setattr(terminal_api, "_backend_cache", None)
    monkeypatch.setattr(terminal_api, "_backend_cache_key", None)
    monkeypatch.setattr(terminal_api, "_backend_config_preference", None)
    monkeypatch.setattr(terminal_api, "detect_terminal", lambda: detected.pop(0))
    monkeypatch.setattr(terminal_api, "TmuxBackend", lambda: "tmux")
    monkeypatch.setattr(terminal_api, "_herdr_platform_gate", _windows_x64_platform_gate)
    monkeypatch.setattr(terminal_api, "_herdr_capability_report", lambda: _supported_gate().capabilities)
    monkeypatch.setattr(terminal_api, "_herdr_capability_report_ref", lambda: "evidence/herdr-capabilities.json")
    monkeypatch.setattr(terminal_api, "_herdr_request_adapter", lambda: _FakeRequestAdapter())

    assert terminal_api.get_backend() == "tmux"
    assert isinstance(terminal_api.get_backend(), HerdrBackend)


def test_terminal_api_explicit_backend_request_bypasses_module_cache(monkeypatch) -> None:
    stale_backend = "tmux"
    monkeypatch.setattr(terminal_api, "_backend_cache", stale_backend)
    monkeypatch.setattr(terminal_api, "_backend_cache_key", "tmux")
    monkeypatch.setattr(terminal_api, "_herdr_platform_gate", _windows_x64_platform_gate)
    monkeypatch.setattr(terminal_api, "_herdr_capability_report", lambda: _supported_gate().capabilities)
    monkeypatch.setattr(terminal_api, "_herdr_capability_report_ref", lambda: "evidence/herdr-capabilities.json")
    monkeypatch.setattr(terminal_api, "_herdr_request_adapter", lambda: _FakeRequestAdapter())

    backend = terminal_api.get_backend("herdr")

    assert isinstance(backend, HerdrBackend)
    assert terminal_api._backend_cache is stale_backend


def test_terminal_api_herdr_runtime_env_bypasses_implicit_cache(monkeypatch) -> None:
    monkeypatch.delenv("CCB_RUNTIME_MUX_BACKEND", raising=False)
    monkeypatch.setattr(terminal_api, "_backend_cache", "stale")
    monkeypatch.setattr(terminal_api, "_backend_cache_key", "tmux")
    monkeypatch.setattr(terminal_api, "_backend_config_preference", None)
    monkeypatch.setattr(terminal_api, "detect_terminal", lambda: "tmux")
    monkeypatch.setattr(terminal_api, "TmuxBackend", lambda: "fresh")
    monkeypatch.setenv("CCB_HERDR_SESSION", "runtime-session")

    assert terminal_api.get_backend() == "fresh"
    assert terminal_api._backend_cache == "stale"


def test_terminal_api_platform_gate_uses_live_wsl_runtime(monkeypatch) -> None:
    monkeypatch.setattr(terminal_api, "is_windows", lambda: True)
    monkeypatch.setattr(terminal_api, "_is_wsl_impl", lambda: True)
    monkeypatch.setattr(terminal_api.platform, "machine", lambda: "AMD64")
    monkeypatch.setattr(terminal_api.platform, "architecture", lambda: ("64bit", "WindowsPE"))

    gate = terminal_api._herdr_platform_gate()

    assert gate["supported"] is False
    assert gate["is_wsl"] is True
    assert gate["platform_gate_ref"] == "runtime"


def test_terminal_api_platform_gate_uses_windows_arch_env_when_machine_empty(monkeypatch) -> None:
    monkeypatch.setattr(terminal_api, "is_windows", lambda: True)
    monkeypatch.setattr(terminal_api, "_is_wsl_impl", lambda: False)
    monkeypatch.setattr(terminal_api.platform, "machine", lambda: "")
    monkeypatch.setattr(terminal_api.platform, "architecture", lambda: ("64bit", "WindowsPE"))
    monkeypatch.setenv("PROCESSOR_ARCHITECTURE", "AMD64")

    gate = terminal_api._herdr_platform_gate()

    assert gate["supported"] is True
    assert gate["cpu_arch"] == "x64"


def test_terminal_api_capability_report_can_use_runtime_override(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(terminal_api, "_ROOT_DIR", tmp_path)
    report_path = tmp_path / "evidence" / "herdr-capabilities.json"
    report_path.parent.mkdir()
    report_path.write_text(
        '{"backend_impl":"herdr","command_status":{"session_attach":"supported","pane_spawn":"supported","send_input":"supported","read_output":"supported","kill_pane":"supported"},"semantic_status":{"session_attach":"supported","pane_spawn":"supported","send_input":"supported","read_output":"supported","kill_pane":"supported"},"windows_beta_gaps":[],"blocking_gaps":[],"source_ref":"runtime"}',
        encoding="utf-8",
    )
    monkeypatch.setenv("CCB_HERDR_CAPABILITY_REPORT", str(report_path))

    assert terminal_api._herdr_capability_report()["source_ref"] == "evidence/herdr-capabilities.json"
    assert terminal_api._herdr_capability_report_ref() == "evidence/herdr-capabilities.json"


def test_terminal_api_capability_report_normalizes_spike_projection(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(terminal_api, "_ROOT_DIR", tmp_path)
    report_path = tmp_path / "evidence" / "herdr-spike.json"
    report_path.parent.mkdir()
    report_path.write_text(
        '{"adapter_recommendation":"continue-with-gaps","verdict":"partial","failure_class":"windows-beta-gap","capability_projection":{"command_status":{"session_attach":"supported","pane_spawn":"supported","send_input":"supported","read_output":"supported","kill_pane":"supported","server_restart_process_continuity":"unsupported"},"semantic_status":{"session_attach":"supported","pane_spawn":"supported","send_input":"supported","read_output":"supported","kill_pane":"supported","server_restart_process_continuity":"unsupported"},"windows_beta_gaps":[],"blocking_gaps":["server_restart_process_continuity"]}}',
        encoding="utf-8",
    )
    monkeypatch.setenv("CCB_HERDR_CAPABILITY_REPORT", str(report_path))

    report = terminal_api._herdr_capability_report()

    assert report["backend_impl"] == "herdr"
    assert report["command_status"]["session_attach"] == "supported"
    assert report["command_status"]["workspace_create"] == "supported"
    assert report["command_status"]["pane_split"] == "supported"
    assert report["semantic_status"]["workspace_focus"] == "supported"
    assert report["semantic_status"]["pane_metadata"] == "supported"
    assert report["blocking_gaps"] == ["server_restart_process_continuity"]
    assert report["source_ref"] == "evidence/herdr-spike.json"


def test_terminal_api_malformed_capability_report_is_invalid_request(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(terminal_api, "_ROOT_DIR", tmp_path)
    report_path = tmp_path / "evidence" / "herdr-capabilities.json"
    report_path.parent.mkdir()
    report_path.write_text("{not-json", encoding="utf-8")
    monkeypatch.setenv("CCB_HERDR_CAPABILITY_REPORT", str(report_path))

    report = terminal_api._herdr_capability_report()

    assert report is not None
    assert report["blocked"] is True
    assert report["failure_reason"] == "invalid-request"
    assert terminal_api._herdr_capability_report_ref() == "evidence/herdr-capabilities.json"


def test_terminal_api_missing_configured_capability_report_is_invalid_request(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(terminal_api, "_ROOT_DIR", tmp_path)
    report_path = tmp_path / "evidence" / "missing.json"
    monkeypatch.setenv("CCB_HERDR_CAPABILITY_REPORT", str(report_path))

    report = terminal_api._herdr_capability_report()

    assert report is not None
    assert report["blocked"] is True
    assert report["failure_reason"] == "invalid-request"


def test_terminal_api_capability_report_uses_external_override_without_leaking_absolute_ref(
    monkeypatch,
    tmp_path,
) -> None:
    monkeypatch.setattr(terminal_api, "_ROOT_DIR", tmp_path / "repo")
    report_path = tmp_path / "outside" / "herdr-capabilities.json"
    report_path.parent.mkdir()
    report_path.write_text('{"source_ref":"C:/Users/Administrator/secret/herdr-capabilities.json"}', encoding="utf-8")
    monkeypatch.setenv("CCB_HERDR_CAPABILITY_REPORT", str(report_path))

    assert terminal_api._herdr_capability_report()["source_ref"] == "herdr-capabilities.json"
    assert terminal_api._herdr_capability_report_ref() == "herdr-capabilities.json"


def test_terminal_api_capability_gate_rejects_malformed_supported_report() -> None:
    gate = terminal_api._herdr_capability_gate({"backend_impl": "herdr"})

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        gate.require_supported("prepare_server")

    assert exc_info.value.evidence["failure_reason"] == "invalid-request"


def test_terminal_api_capability_gate_rejects_contradictory_report_metadata() -> None:
    capabilities = dict(_supported_gate().capabilities or {})
    capabilities.update(
        {
            "adapter_recommendation": "stop",
            "verdict": "failed",
            "failure_class": "windows-beta-gap",
        }
    )
    gate = terminal_api._herdr_capability_gate(capabilities)

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        gate.require_supported("prepare_server")

    assert exc_info.value.evidence["failure_reason"] == "invalid-request"


def test_terminal_api_herdr_request_adapter_uses_socket_ref_override(monkeypatch) -> None:
    monkeypatch.setenv("CCB_HERDR_SOCKET_REF", "herdr://override")

    adapter = terminal_api._herdr_request_adapter()

    assert adapter.socket_ref == "herdr://override"


def test_terminal_api_herdr_request_adapter_uses_windowless_run_wrapper(monkeypatch) -> None:
    def run_fn(*_args, **_kwargs):
        raise AssertionError("run_fn should not be executed by adapter construction")

    monkeypatch.setattr(terminal_api, "_run", run_fn)

    adapter = terminal_api._herdr_request_adapter()

    assert adapter._run_fn is run_fn


def test_herdr_cli_request_adapter_maps_server_info_and_core_operations() -> None:
    commands: list[list[str]] = []

    def run_fn(command, **kwargs):
        commands.append(command)
        assert command[1:3] == ["--session", "ccb-demo"]
        joined = " ".join(command)
        if "status --json" in joined:
            return _completed('{"client":{"version":"0.7.5-preview"},"server":{"socket":"C:/tmp/herdr.sock"}}')
        if "--version" in joined:
            return _completed("herdr 0.7.5-preview\n")
        if "api schema --json" in joined:
            return _completed('{"title":"Herdr API"}')
        if "report-metadata" in joined:
            return _completed("")
        if "workspace create" in joined:
            return _completed(
                '{"result":{"workspace":{"workspace_id":"w1"},"root_pane":{"pane_id":"w1:p1","workspace_id":"w1"}}}'
            )
        if "workspace list" in joined:
            return _completed('{"result":{"workspaces":[{"workspace_id":"w1","label":"demo"}]}}')
        if "pane list" in joined:
            return _completed(
                '{"result":{"panes":[{"pane_id":"w1:p1","workspace_id":"w1"},{"pane_id":"w1:p2","workspace_id":"w1"}]}}'
            )
        if "pane split" in joined:
            return _completed('{"result":{"pane":{"pane_id":"w1:p2","workspace_id":"w1"}}}')
        if "pane run" in joined:
            return _completed("")
        if "pane read" in joined:
            return _completed("ready")
        if "pane close" in joined:
            return _completed('{"result":{"type":"ok"}}')
        raise AssertionError(joined)

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
    )

    assert adapter("server_info", {})["api_schema"] == "Herdr API"
    namespace = adapter("create_session", {"project_id": "demo", "cwd": "D:/demo", "title": "demo"})
    restored = adapter("restore_session", {"restore_token": namespace["restore_token"]})
    pane = adapter("create_pane", {"namespace_id": namespace["namespace_id"], "cwd": "D:/demo"})
    sent = adapter("send_text", {"pane_id": pane["pane_id"], "text": "hello"})
    captured = adapter("capture_pane", {"pane_id": pane["pane_id"], "lines": 10})
    killed = adapter("kill_pane", {"pane_id": pane["pane_id"]})

    assert namespace["namespace_id"] == "w1"
    assert namespace["restore_token"] == "ccb-demo::w1"
    assert namespace["session_name"] == "ccb-demo"
    assert restored["restore_token"] == "ccb-demo::w1"
    assert restored["session_name"] == "ccb-demo"
    assert pane["pane_id"] == "w1:p2"
    assert pane["session_name"] == "ccb-demo"
    assert sent["status"] == "ok"
    assert captured["text"] == "ready"
    assert killed["status"] == "ok"
    assert commands
    joined_commands = [" ".join(command) for command in commands]
    assert any("workspace list" in command for command in joined_commands)
    assert any("pane list" in command for command in joined_commands)
    assert not any("workspace list --json" in command for command in joined_commands)
    assert not any("pane list --json" in command for command in joined_commands)


def test_herdr_cli_request_adapter_falls_back_to_api_snapshot_when_list_output_is_not_json() -> None:
    commands: list[list[str]] = []

    def run_fn(command, **kwargs):
        commands.append(command)
        joined = " ".join(command)
        if "workspace list" in joined or "pane list" in joined:
            assert "--json" not in command
            return _completed("not-json")
        if "api snapshot" in joined:
            return _completed(
                json.dumps(
                    {
                        "result": {
                            "snapshot": {
                                "workspaces": [{"workspace_id": "w1", "label": "demo"}],
                                "panes": [
                                    {"pane_id": "w1:p1", "workspace_id": "w1"},
                                    {"pane_id": "w2:p1", "workspace_id": "w2"},
                                ],
                            }
                        }
                    }
                )
            )
        raise AssertionError(joined)

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
    )

    restored = adapter("restore_session", {"restore_token": "ccb-demo::w1"})
    listed = adapter("list_panes", {"namespace_id": "w1", "session_name": "ccb-demo"})

    assert restored["namespace_id"] == "w1"
    assert [pane["pane_id"] for pane in listed["panes"]] == ["w1:p1"]
    joined_commands = [" ".join(command) for command in commands]
    assert any("workspace list" in command for command in joined_commands)
    assert any("pane list" in command for command in joined_commands)
    assert not any("workspace list --json" in command for command in joined_commands)
    assert not any("pane list --json" in command for command in joined_commands)
    assert sum("api snapshot" in command for command in joined_commands) == 2


def test_herdr_cli_request_adapter_does_not_fallback_to_snapshot_for_list_command_failure() -> None:
    commands: list[list[str]] = []

    def run_fn(command, **kwargs):
        commands.append(command)
        joined = " ".join(command)
        if "workspace list" in joined:
            raise _called_process_error(command, stderr="permission denied")
        if "api snapshot" in joined:
            raise AssertionError("snapshot fallback must not mask real command failures")
        raise AssertionError(joined)

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        adapter("restore_session", {"restore_token": "ccb-demo::w1"})

    assert "permission denied" in exc_info.value.detail
    assert any("workspace list" in " ".join(command) for command in commands)
    assert not any("api snapshot" in " ".join(command) for command in commands)


def test_herdr_cli_request_adapter_starts_server_and_retries_server_backed_command() -> None:
    commands: list[list[str]] = []
    popen_commands: list[list[str]] = []
    workspace_create_calls = 0

    def run_fn(command, **kwargs):
        nonlocal workspace_create_calls
        commands.append(command)
        joined = " ".join(command)
        if "report-metadata" in joined:
            return _completed("")
        if "status server --json" in joined:
            return _completed('{"status":"running","running":true}')
        if "workspace create" in joined:
            workspace_create_calls += 1
            if workspace_create_calls == 1:
                raise subprocess.CalledProcessError(
                    1,
                    command,
                    stderr="Error: Os { code: 2, kind: NotFound, message: \"No such file or directory\" }",
                )
            return _completed(
                '{"result":{"workspace":{"workspace_id":"w1"},"root_pane":{"pane_id":"w1:p1","workspace_id":"w1"}}}'
            )
        if "workspace list" in joined:
            return _completed('{"result":{"workspaces":[{"workspace_id":"w1"}]}}')
        raise AssertionError(joined)

    class _RunningProcess:
        def poll(self):
            return None

    def popen_fn(command, **kwargs):
        popen_commands.append(command)
        return _RunningProcess()

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        popen_fn=popen_fn,
        which_fn=lambda name: "herdr",
        sleep_fn=lambda seconds: None,
    )

    namespace = adapter("create_session", {"project_id": "demo", "cwd": "D:/demo", "title": "demo"})

    assert namespace["namespace_id"] == "w1"
    assert workspace_create_calls == 2
    assert popen_commands == [["herdr", "--session", "ccb-demo", "server"]]
    workspace_commands = [command for command in commands if "workspace create" in " ".join(command)]
    assert workspace_commands[0] == workspace_commands[1]


def test_herdr_cli_request_adapter_fails_closed_when_server_exits_immediately() -> None:
    commands: list[list[str]] = []
    popen_commands: list[list[str]] = []
    workspace_create_calls = 0

    class _ExitedProcess:
        def poll(self) -> int:
            return 1

    def run_fn(command, **kwargs):
        nonlocal workspace_create_calls
        commands.append(command)
        joined = " ".join(command)
        if "report-metadata" in joined:
            return _completed("")
        if "workspace create" in joined:
            workspace_create_calls += 1
            if workspace_create_calls in {1, 3}:
                raise subprocess.CalledProcessError(
                    1,
                    command,
                    stderr="Error: Os { code: 2, kind: NotFound, message: \"No such file or directory\" }",
                )
            return _completed(
                '{"result":{"workspace":{"workspace_id":"w1"},"root_pane":{"pane_id":"w1:p1","workspace_id":"w1"}}}'
            )
        if "workspace list" in joined:
            return _completed('{"result":{"workspaces":[{"workspace_id":"w1"}]}}')
        raise AssertionError(joined)

    def popen_fn(command, **kwargs):
        popen_commands.append(command)
        return _ExitedProcess()

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        popen_fn=popen_fn,
        which_fn=lambda name: "herdr",
        sleep_fn=lambda seconds: None,
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        adapter("create_session", {"project_id": "demo", "cwd": "D:/demo", "title": "demo"})

    assert exc_info.value.operation == "start_server"
    assert exc_info.value.category == "transient-unavailable"
    assert "exited immediately" in exc_info.value.detail
    assert workspace_create_calls == 1
    assert popen_commands == [["herdr", "--session", "ccb-demo", "server"]]


def test_herdr_cli_request_adapter_fails_closed_when_started_server_is_not_running() -> None:
    popen_commands: list[list[str]] = []
    workspace_create_calls = 0

    class _RunningProcess:
        def poll(self):
            return None

    def run_fn(command, **kwargs):
        nonlocal workspace_create_calls
        joined = " ".join(command)
        if "workspace create" in joined:
            workspace_create_calls += 1
            raise subprocess.CalledProcessError(
                1,
                command,
                stderr="Error: Os { code: 2, kind: NotFound, message: \"No such file or directory\" }",
            )
        if "status server --json" in joined:
            return _completed('{"status":"not_running","running":false}')
        raise AssertionError(joined)

    def popen_fn(command, **kwargs):
        popen_commands.append(command)
        return _RunningProcess()

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        popen_fn=popen_fn,
        which_fn=lambda name: "herdr",
        sleep_fn=lambda seconds: None,
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        adapter("create_session", {"project_id": "demo", "cwd": "D:/demo", "title": "demo"})

    assert exc_info.value.operation == "start_server"
    assert exc_info.value.category == "transient-unavailable"
    assert "did not become ready" in exc_info.value.detail
    assert workspace_create_calls == 1
    assert popen_commands == [["herdr", "--session", "ccb-demo", "server"]]


def test_herdr_cli_request_adapter_accepts_nested_server_status() -> None:
    commands: list[list[str]] = []

    def run_fn(command, **kwargs):
        del kwargs
        commands.append(command)
        return _completed('{"server":{"status":"running","running":true}}')

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
    )

    assert adapter._server_status_running("herdr", session_name="ccb-demo") is True
    assert commands == [["herdr", "--session", "ccb-demo", "status", "server", "--json"]]


def test_herdr_cli_request_adapter_fails_when_created_workspace_is_not_listed() -> None:
    def run_fn(command, **kwargs):
        del kwargs
        joined = " ".join(command)
        if "status --json" in joined:
            return _completed('{"client":{"version":"0.7.5"}}')
        if "api schema --json" in joined:
            return _completed('{"title":"Herdr API"}')
        if "--version" in joined:
            return _completed("herdr 0.7.5")
        if "workspace create" in joined:
            return _completed(
                '{"result":{"workspace":{"workspace_id":"w1"},"root_pane":{"pane_id":"w1:p1","workspace_id":"w1"}}}'
            )
        if "workspace list" in joined:
            return _completed('{"result":{"workspaces":[]}}')
        raise AssertionError(joined)

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        adapter("create_session", {"project_id": "demo", "cwd": "D:/demo", "title": "demo"})

    assert exc_info.value.operation == "create_session"
    assert exc_info.value.category == "command-failed"
    assert "was not found after creation" in exc_info.value.detail


def test_herdr_cli_request_adapter_does_not_start_server_for_server_info() -> None:
    popen_commands: list[list[str]] = []

    def run_fn(command, **kwargs):
        joined = " ".join(command)
        if "status --json" in joined:
            raise subprocess.CalledProcessError(
                1,
                command,
                stderr="Error: Os { code: 2, kind: NotFound, message: \"No such file or directory\" }",
            )
        raise AssertionError(joined)

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        popen_fn=lambda command, **kwargs: popen_commands.append(command) or object(),
        which_fn=lambda name: "herdr",
        sleep_fn=lambda seconds: None,
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        adapter("server_info", {})

    assert exc_info.value.operation == "server_info"
    assert popen_commands == []


def test_herdr_backend_uses_cli_adapter_envelope_contract_for_core_operations(monkeypatch) -> None:
    monkeypatch.setattr("platforms.windows.herdr.runtime.cli._runtime_platform", lambda: "windows")
    monkeypatch.setattr("platforms.windows.herdr.runtime.cli._runtime_arch", lambda: "x64")
    commands: list[list[str]] = []

    def run_fn(command, **kwargs):
        commands.append(command)
        joined = " ".join(command)
        if "status --json" in joined:
            return _completed('{"client":{"version":"0.7.5-preview"}}')
        if "--version" in joined:
            return _completed("herdr 0.7.5-preview\n")
        if "api schema --json" in joined:
            return _completed('{"title":"Herdr API"}')
        if "report-metadata" in joined:
            return _completed("")
        if "workspace create" in joined:
            return _completed(
                '{"result":{"workspace":{"workspace_id":"w1"},"root_pane":{"pane_id":"w1:p1","workspace_id":"w1"}}}'
            )
        if "workspace list" in joined:
            return _completed('{"result":{"workspaces":[{"workspace_id":"w1"}]}}')
        if "pane list" in joined:
            return _completed(
                '{"result":{"panes":[{"pane_id":"w1:p1","workspace_id":"w1"},{"pane_id":"w1:p2","workspace_id":"w1"}]}}'
            )
        if "pane split" in joined:
            return _completed('{"result":{"pane":{"pane_id":"w1:p2","workspace_id":"w1"}}}')
        if "pane run" in joined:
            return _completed("")
        if "pane read" in joined:
            return _completed("ready")
        if "pane close" in joined:
            return _completed('{"result":{"type":"ok"}}')
        raise AssertionError(joined)

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
    )
    backend = HerdrBackend(
        client=HerdrSocketClient(
            request_fn=adapter,
            socket_ref=adapter.socket_ref,
            allow_session_scoped_ipc_refs=adapter.allow_session_scoped_ipc_refs,
        ),
        capability_gate=_supported_gate(),
    )

    namespace = backend.create_session(project_id="demo", cwd="D:/demo", title="demo")
    pane = backend.create_pane(namespace, command=["python", "-V"], cwd="D:/demo", env={}, title="root")
    split = backend.split_pane(pane, command=[], cwd="D:/demo", env={}, title="child")
    send = backend.send_text(split, "hello")
    captured, capture = backend.capture_pane(split, lines=5)
    killed = backend.kill_pane(split)

    assert pane["pane_id"] == "w1:p2"
    assert split["pane_id"] == "w1:p2"
    assert send["status"] == "ok"
    assert captured == "ready"
    assert capture["status"] == "ok"
    assert killed["status"] == "ok"
    assert any("pane run" in " ".join(command) for command in commands)


def test_herdr_backend_logical_window_facade_restores_from_root_pane_metadata(monkeypatch) -> None:
    monkeypatch.setattr("platforms.windows.herdr.runtime.cli._runtime_platform", lambda: "windows")
    monkeypatch.setattr("platforms.windows.herdr.runtime.cli._runtime_arch", lambda: "x64")
    state: dict[str, object] = {
        "workspaces": {},
        "panes": {},
        "focused_workspaces": [],
        "closed_workspaces": [],
    }

    def tokens_from_command(command: list[str]) -> dict[str, str]:
        tokens: dict[str, str] = {}
        for index, value in enumerate(command):
            if value == "--token" and index + 1 < len(command):
                name, token_value = command[index + 1].split("=", 1)
                tokens[name] = token_value
        return tokens

    def run_fn(command, **kwargs):
        joined = " ".join(command)
        workspaces: dict[str, dict[str, object]] = state["workspaces"]  # type: ignore[assignment]
        panes: dict[str, dict[str, object]] = state["panes"]  # type: ignore[assignment]
        if "status --json" in joined:
            return _completed('{"client":{"version":"0.7.5-preview"}}')
        if "--version" in joined:
            return _completed("herdr 0.7.5-preview\n")
        if "api schema --json" in joined:
            return _completed('{"title":"Herdr API"}')
        if "status server --json" in joined:
            return _completed('{"status":"running","running":true}')
        if "workspace create" in joined:
            workspace_id = f"w{len(workspaces) + 1}"
            pane_id = f"{workspace_id}:p1"
            workspaces[workspace_id] = {
                "workspace_id": workspace_id,
                "label": command[command.index("--label") + 1],
                "focused": "--focus" in command,
                "tokens": {},
            }
            panes[pane_id] = {
                "pane_id": pane_id,
                "workspace_id": workspace_id,
                "tokens": {},
            }
            return _completed(
                json.dumps(
                    {
                        "result": {
                            "workspace": {"workspace_id": workspace_id},
                            "root_pane": {
                                "pane_id": pane_id,
                                "workspace_id": workspace_id,
                            },
                        }
                    }
                )
            )
        if "workspace report-metadata" in joined:
            workspace_id = command[command.index("report-metadata") + 1]
            workspaces[workspace_id]["tokens"].update(tokens_from_command(command))
            return _completed("")
        if "pane report-metadata" in joined:
            pane_id = command[command.index("report-metadata") + 1]
            panes[pane_id]["tokens"].update(tokens_from_command(command))
            return _completed("")
        if "workspace list" in joined:
            return _completed(json.dumps({"result": {"workspaces": list(workspaces.values())}}))
        if "pane list" in joined:
            selected = list(panes.values())
            if "--workspace" in command:
                workspace_id = command[command.index("--workspace") + 1]
                selected = [pane for pane in selected if pane["workspace_id"] == workspace_id]
            return _completed(json.dumps({"result": {"panes": selected}}))
        if "pane split" in joined:
            parent_id = command[command.index("split") + 1]
            workspace_id = str(panes[parent_id]["workspace_id"])
            pane_id = f"{workspace_id}:p{sum(pane['workspace_id'] == workspace_id for pane in panes.values()) + 1}"
            panes[pane_id] = {
                "pane_id": pane_id,
                "workspace_id": workspace_id,
                "tokens": {},
            }
            return _completed(
                json.dumps({"result": {"pane": {"pane_id": pane_id, "workspace_id": workspace_id}}})
            )
        if "pane report-metadata" in joined:
            pane_id = command[command.index("report-metadata") + 1]
            tokens = tokens_from_command(command)
            if pane_id in panes:
                existing = panes[pane_id]
                existing["tokens"] = {**existing.get("tokens", {}), **tokens}
            return _completed("")
        if "workspace focus" in joined:
            state["focused_workspaces"].append(command[-1])  # type: ignore[union-attr]
            return _completed("")
        if "workspace report-metadata" in joined:
            workspace_id = command[command.index("report-metadata") + 1]
            tokens = tokens_from_command(command)
            if workspace_id in workspaces:
                existing = workspaces[workspace_id]
                existing["tokens"] = {**existing.get("tokens", {}), **tokens}
            return _completed("")
        if "workspace close" in joined:
            workspace_id = command[-1]
            state["closed_workspaces"].append(workspace_id)  # type: ignore[union-attr]
            workspaces.pop(workspace_id)
            for pane_id in [pane_id for pane_id, pane in panes.items() if pane["workspace_id"] == workspace_id]:
                panes.pop(pane_id)
            return _completed("")
        raise AssertionError(joined)

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
    )
    backend = HerdrBackend(
        client=HerdrSocketClient(
            request_fn=adapter,
            socket_ref=adapter.socket_ref,
            allow_session_scoped_ipc_refs=adapter.allow_session_scoped_ipc_refs,
        ),
        capability_gate=_supported_gate(),
    )

    namespace = backend.create_session(project_id="demo", cwd="D:/demo", title="ccb-demo")
    control = backend.ensure_window(
        namespace,
        window_name="__ccb_ctl",
        cwd="D:/demo",
        select=False,
    )
    workspace = backend.ensure_window(
        namespace,
        window_name="ccb",
        cwd="D:/demo",
        select=True,
    )
    root = backend.window_root_pane(namespace, window_name="ccb")
    backend.set_pane_identity(
        root,
        title="cmd",
        agent_label="cmd",
        project_id="demo",
        is_cmd=True,
        slot_key="cmd",
        window_name="ccb",
        managed_by="ccbd",
    )

    assert control["window_id"] == "w1"
    assert workspace["window_id"] == "w2"
    assert root["pane_id"] == "w2:p1"
    assert state["panes"]["w2:p1"]["tokens"]["ccb_root_pane"] == "1"  # type: ignore[index]
    assert state["panes"]["w2:p1"]["tokens"]["ccb_window"] == "ccb"  # type: ignore[index]

    for workspace_record in state["workspaces"].values():  # type: ignore[union-attr]
        workspace_record["tokens"] = {}

    restored_adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
    )
    restored_backend = HerdrBackend(
        client=HerdrSocketClient(
            request_fn=restored_adapter,
            socket_ref=restored_adapter.socket_ref,
            allow_session_scoped_ipc_refs=restored_adapter.allow_session_scoped_ipc_refs,
        ),
        capability_gate=_supported_gate(),
    )
    restored_namespace = restored_backend.namespace_ref("ccb-demo", "w1")

    restored_workspace = restored_backend.ensure_window(
        restored_namespace,
        window_name="ccb",
        cwd="D:/demo",
        select=False,
    )
    assert restored_workspace["window_id"] == "w2"
    assert len(state["workspaces"]) == 2  # type: ignore[arg-type]
    restored_windows = restored_backend.list_windows(restored_namespace)
    restored_root = restored_backend.window_root_pane(restored_namespace, window_name="ccb")
    child = restored_backend.split_pane(
        restored_root,
        direction="right",
        percent=50,
        command=[],
        cwd="D:/demo",
        env={},
        title="agent",
    )
    restored_backend.select_window(
        restored_namespace,
        window_id="ccb",
        target="ccb-demo:ccb",
    )
    restored_backend.rename_window(
        restored_namespace,
        window_id="w2",
        target="ccb-demo:w2",
        new_name="ccb-renamed",
    )
    restored_backend.select_window(
        restored_namespace,
        window_id="ccb-renamed",
        target="ccb-demo:ccb-renamed",
    )
    restored_backend.kill_window(
        restored_namespace,
        window_id="__ccb_ctl",
        target="ccb-demo:__ccb_ctl",
    )

    assert {(item["window_id"], item["window_name"]) for item in restored_windows} == {
        ("w1", "__ccb_ctl"),
        ("w2", "ccb"),
    }
    assert restored_root["pane_id"] == "w2:p1"
    assert child["pane_id"] == "w2:p2"
    assert state["panes"]["w2:p1"]["tokens"]["ccb_window"] == "ccb-renamed"  # type: ignore[index]
    assert state["focused_workspaces"][-1] == "w2"  # type: ignore[index]
    assert state["closed_workspaces"] == ["w1"]
    assert [item["window_name"] for item in restored_backend.list_windows(restored_namespace)] == ["ccb-renamed"]
    assert not callable(getattr(restored_backend, "_tmux_run", None))


def test_herdr_cli_logical_windows_accept_workspace_ids_and_isolate_namespace_groups() -> None:
    state: dict[str, object] = {
        "workspaces": {
            "w1": {"workspace_id": "w1", "focused": False},
            "w2": {"workspace_id": "w2", "focused": False},
            "w3": {"workspace_id": "w3", "focused": False},
            "w4": {"workspace_id": "w4", "focused": False},
        },
        "panes": {
            "w1:p1": {
                "pane_id": "w1:p1",
                "workspace_id": "w1",
                "tokens": {
                    "ccb_namespace_id": "w1",
                    "ccb_root_pane": "1",
                    "ccb_window": "__ccb_ctl",
                },
            },
            "w2:p1": {
                "pane_id": "w2:p1",
                "workspace_id": "w2",
                "tokens": {
                    "ccb_namespace_id": "w1",
                    "ccb_root_pane": "1",
                    "ccb_window": "ccb",
                },
            },
            "w3:p1": {
                "pane_id": "w3:p1",
                "workspace_id": "w3",
                "tokens": {
                    "ccb_namespace_id": "w3",
                    "ccb_root_pane": "1",
                    "ccb_window": "ccb",
                },
            },
            "w4:p1": {
                "pane_id": "w4:p1",
                "workspace_id": "w4",
                "tokens": {"ccb_namespace_id": "w1", "ccb_window": "broken"},
            },
            "w4:p2": {
                "pane_id": "w4:p2",
                "workspace_id": "w4",
                "tokens": {"ccb_namespace_id": "w1", "ccb_window": "broken"},
            },
        },
        "focused": [],
        "closed": [],
    }

    def run_fn(command, **kwargs):
        joined = " ".join(command)
        workspaces: dict[str, dict[str, object]] = state["workspaces"]  # type: ignore[assignment]
        panes: dict[str, dict[str, object]] = state["panes"]  # type: ignore[assignment]
        if "workspace list" in joined:
            return _completed(json.dumps({"result": {"workspaces": list(workspaces.values())}}))
        if "pane list" in joined:
            selected = list(panes.values())
            if "--workspace" in command:
                workspace_id = command[command.index("--workspace") + 1]
                selected = [pane for pane in selected if pane["workspace_id"] == workspace_id]
            return _completed(json.dumps({"result": {"panes": selected}}))
        if "workspace focus" in joined:
            state["focused"].append(command[-1])  # type: ignore[union-attr]
            return _completed("")
        if "workspace close" in joined:
            workspace_id = command[-1]
            state["closed"].append(workspace_id)  # type: ignore[union-attr]
            workspaces.pop(workspace_id)
            for pane_id in [
                pane_id
                for pane_id, pane in panes.items()
                if pane["workspace_id"] == workspace_id
            ]:
                panes.pop(pane_id)
            return _completed("")
        raise AssertionError(joined)

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
    )
    payload = {"namespace_id": "w1", "session_name": "ccb-demo"}

    root = adapter("window_root_pane", {**payload, "window_name": "w2"})
    adapter("select_window", {**payload, "window_id": "w2"})

    assert root["pane_id"] == "w2:p1"
    assert state["focused"] == ["w2"]

    for operation, request in (
        ("window_root_pane", {**payload, "window_name": "w3"}),
        ("select_window", {**payload, "window_id": "w3"}),
        ("kill_window", {**payload, "window_id": "w3"}),
        (
            "create_pane",
            {
                **payload,
                "parent_pane": "w3:p1",
                "command": [],
                "cwd": "D:/demo",
            },
        ),
        ("window_root_pane", {**payload, "window_name": "w4"}),
    ):
        with pytest.raises(MuxCommandErrorV2):
            adapter(operation, request)

    adapter("kill_window", {**payload, "window_id": "w2"})

    assert state["closed"] == ["w2"]
    adapter("destroy_namespace", payload)

    assert state["closed"] == ["w2", "w1"]
    assert set(state["workspaces"]) == {"w3", "w4"}


def test_herdr_cli_destroy_namespace_ignores_missing_workspace_during_close() -> None:
    calls: list[list[str]] = []

    def run_fn(command, **kwargs):
        del kwargs
        calls.append(list(command))
        joined = " ".join(command)
        if "workspace list" in joined:
            return _completed(
                json.dumps(
                    {
                        "result": {
                            "workspaces": [
                                {"workspace_id": "w1", "focused": False},
                            ],
                        },
                    }
                )
            )
        if "pane list" in joined:
            return _completed(
                json.dumps(
                    {
                        "result": {
                            "panes": [
                                {
                                    "pane_id": "w1:p1",
                                    "workspace_id": "w1",
                                    "tokens": {
                                        "ccb_namespace_id": "w1",
                                        "ccb_root_pane": "1",
                                        "ccb_window": "ccb",
                                    },
                                },
                            ],
                        },
                    }
                )
            )
        if "workspace close" in joined:
            raise subprocess.CalledProcessError(
                1,
                command,
                stderr='{"error":{"code":"workspace_not_found","message":"workspace w1 not found"}}',
            )
        raise AssertionError(joined)

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
    )

    result = adapter("destroy_namespace", {"namespace_id": "w1", "session_name": "ccb-demo"})

    assert result == {"status": "ok", "namespace_id": "w1", "closed_workspace_ids": []}
    assert any("workspace close w1" in " ".join(command) for command in calls)


def test_herdr_backend_destroy_namespace_and_kill_server_delegate_and_drop_namespace_refs() -> None:
    operations: list[tuple[str, dict[str, object]]] = []

    def request(operation: str, payload: dict[str, object]) -> dict[str, object]:
        operations.append((operation, dict(payload)))
        if operation == "server_info":
            return {
                "status": "ok",
                "result": {
                    "api_schema": "Herdr API",
                    "version": "0.7.5-preview",
                    "platform": "windows",
                    "arch": "x64",
                },
            }
        if operation == "close_workspace":
            return {"status": "ok"}
        if operation in {"destroy_namespace", "kill_server"}:
            return {"status": "ok", "closed_workspace_ids": ["w1"]}
        raise AssertionError(operation)

    backend = HerdrBackend(
        client=HerdrSocketClient(request_fn=request, socket_ref="herdr://local"),
        capability_gate=_supported_gate(),
    )
    namespace = backend.namespace_ref("ccb-demo", "w1")
    backend._logical_windows[("ccb-demo", "w1", "ccb")] = {  # type: ignore[attr-defined]
        "window_id": "w1",
        "window_name": "ccb",
        "root_pane_id": "w1:p1",
    }
    backend._panes["w1:p1"] = {  # type: ignore[attr-defined]
        "pane_id": "w1:p1",
        "session_name": "ccb-demo",
        "window_name": "ccb",
    }
    backend._pane_namespaces["w1:p1"] = namespace  # type: ignore[attr-defined]

    destroy = backend.destroy_namespace(namespace)
    namespace = backend.namespace_ref("ccb-demo", "w1")
    kill = backend.kill_server(namespace)

    assert destroy["status"] == "ok"
    assert kill["status"] == "ok"
    assert [operation for operation, _payload in operations] == [
        "server_info",
        "destroy_namespace",
        "close_workspace",
        "server_info",
        "kill_server",
    ]
    assert operations[1][1]["namespace_id"] == "w1"
    assert backend._logical_windows == {}  # type: ignore[attr-defined]
    assert backend._panes == {}  # type: ignore[attr-defined]
    assert backend._pane_namespaces == {}  # type: ignore[attr-defined]


def test_herdr_backend_list_panes_by_user_options_uses_current_namespace_ref() -> None:
    requests: list[tuple[str, dict[str, object]]] = []

    def request(operation: str, payload: dict[str, object]):
        requests.append((operation, payload))
        if operation == "server_info":
            return {
                "version": "0.7.5-preview",
                "api_schema": "Herdr API",
                "platform": "windows",
                "arch": "x64",
            }
        if operation == "list_panes":
            return {
                "status": "ok",
                "panes": [
                    {
                        "pane_id": "w1:p1",
                        "session_name": "ccb-herdr",
                        "workspace_id": "w1",
                        "tokens": {"ccb_slot": "agent1"},
                    }
                ]
            }
        raise AssertionError(operation)

    backend = HerdrBackend(
        client=HerdrSocketClient(request_fn=request, socket_ref="herdr://local"),
        capability_gate=_supported_gate(),
    )
    namespace = backend.namespace_ref("ccb-herdr", "w1")
    backend._ccb_project_namespace_ref = namespace  # type: ignore[attr-defined]

    assert backend.list_panes_by_user_options({"@ccb_slot": "agent1"}) == ["w1:p1"]
    assert requests[-1] == (
        "list_panes",
        {
            "namespace_id": "w1",
            "session_name": "ccb-herdr",
            "ipc_ref": "herdr://local",
        },
    )


def test_herdr_backend_describe_pane_uses_current_namespace_ref_for_topology_inspection() -> None:
    requests: list[tuple[str, dict[str, object]]] = []

    def request(operation: str, payload: dict[str, object]):
        requests.append((operation, payload))
        if operation == "server_info":
            return {
                "version": "0.7.5-preview",
                "api_schema": "Herdr API",
                "platform": "windows",
                "arch": "x64",
            }
        if operation == "list_panes":
            if not payload.get("namespace_id"):
                return {"status": "ok", "panes": []}
            return {
                "status": "ok",
                "panes": [
                    {
                        "pane_id": "w1:p1",
                        "workspace_id": "w1",
                        "tokens": {
                            "ccb_project_id": "project-1",
                            "ccb_role": "agent",
                            "ccb_slot": "agent1",
                            "ccb_window": "main",
                            "ccb_managed_by": "ccbd",
                            "ccb_namespace_epoch": "1",
                        },
                    }
                ],
            }
        raise AssertionError(operation)

    backend = HerdrBackend(
        client=HerdrSocketClient(request_fn=request, socket_ref="herdr://local"),
        capability_gate=_supported_gate(),
    )
    namespace = backend.namespace_ref("ccb-herdr", "w1")
    backend._ccb_project_namespace_ref = namespace  # type: ignore[attr-defined]

    record = inspect_project_namespace_pane(backend, "w1:p1")

    assert record is not None
    assert record.session_name == "ccb-herdr"
    assert record.project_id == "project-1"
    assert record.role == "agent"
    assert record.slot_key == "agent1"
    assert record.matches_authoritative_topology(
        tmux_session_name="ccb-herdr",
        project_id="project-1",
        role="agent",
        slot_key="agent1",
        window_name="main",
        managed_by="ccbd",
        namespace_epoch=1,
    )
    assert requests[-1] == (
        "list_panes",
        {
            "namespace_id": "w1",
            "session_name": "ccb-herdr",
            "ipc_ref": "herdr://local",
        },
    )


def test_herdr_backend_set_pane_identity_rehydrates_pane_namespace_from_known_namespace() -> None:
    requests: list[tuple[str, dict[str, object]]] = []

    def request(operation: str, payload: dict[str, object]):
        requests.append((operation, payload))
        if operation == "server_info":
            return {
                "version": "0.7.5-preview",
                "api_schema": "Herdr API",
                "platform": "windows",
                "arch": "x64",
            }
        if operation == "set_pane_identity":
            return {"status": "ok", "pane_id": payload["pane_id"]}
        raise AssertionError(operation)

    backend = HerdrBackend(
        client=HerdrSocketClient(request_fn=request, socket_ref="herdr://local"),
        capability_gate=_supported_gate(),
    )
    namespace = backend.namespace_ref("ccb-herdr", "w1")
    pane = {
        "backend_impl": "herdr",
        "pane_id": "w1:p2",
        "session_name": "ccb-herdr",
    }

    backend.set_pane_identity(
        pane,
        title="agent2",
        agent_label="agent2",
        project_id="project-1",
        role="agent",
        slot_key="agent2",
        window_name="main",
        namespace_epoch=2,
        managed_by="ccbd",
    )

    assert backend._pane_namespaces["w1:p2"] == namespace  # type: ignore[attr-defined]
    assert requests[-1] == (
        "set_pane_identity",
        {
            "pane_id": "w1:p2",
            "session_name": "ccb-herdr",
            "title": "agent2",
            "agent_label": "agent2",
            "tokens": {
                "ccb_project_id": "project-1",
                "ccb_order": "",
                "ccb_is_cmd": "0",
                "ccb_role": "agent",
                "ccb_slot": "agent2",
                "ccb_window": "main",
                "ccb_sidebar_instance": "",
                "ccb_session_id": "",
                "ccb_namespace_epoch": "2",
                "ccb_managed_by": "ccbd",
            },
        },
    )
def test_herdr_backend_kill_window_drops_only_current_namespace_cache() -> None:
    def request(operation: str, payload: dict[str, object]) -> dict[str, object]:
        if operation == "server_info":
            return {
                "status": "ok",
                "result": {
                    "api_schema": "Herdr API",
                    "version": "0.7.5-preview",
                    "platform": "windows",
                    "arch": "x64",
                },
            }
        if operation == "kill_window":
            return {"status": "ok"}
        raise AssertionError(operation)

    backend = HerdrBackend(
        client=HerdrSocketClient(request_fn=request, socket_ref="herdr://local"),
        capability_gate=_supported_gate(),
    )
    first = backend.namespace_ref("ccb-demo", "w1")
    second = backend.namespace_ref("ccb-demo", "w2")
    backend._logical_windows[("ccb-demo", "w1", "ccb")] = {  # type: ignore[attr-defined]
        "window_id": "w1",
        "window_name": "ccb",
        "root_pane_id": "w1:p1",
    }
    backend._logical_windows[("ccb-demo", "w2", "ccb")] = {  # type: ignore[attr-defined]
        "window_id": "w2",
        "window_name": "ccb",
        "root_pane_id": "w2:p1",
    }
    backend._panes["w1:p1"] = {"pane_id": "w1:p1"}  # type: ignore[attr-defined]
    backend._panes["w2:p1"] = {"pane_id": "w2:p1"}  # type: ignore[attr-defined]
    backend._pane_namespaces["w1:p1"] = first  # type: ignore[attr-defined]
    backend._pane_namespaces["w2:p1"] = second  # type: ignore[attr-defined]

    backend.kill_window(first, window_id="w1", target="ccb-demo:ccb")

    assert ("ccb-demo", "w1", "ccb") not in backend._logical_windows  # type: ignore[attr-defined]
    assert ("ccb-demo", "w2", "ccb") in backend._logical_windows  # type: ignore[attr-defined]
    assert "w1:p1" not in backend._panes  # type: ignore[attr-defined]
    assert "w2:p1" in backend._panes  # type: ignore[attr-defined]


def test_herdr_backend_identity_update_clears_removed_tokens_and_preserves_root_group(monkeypatch) -> None:
    monkeypatch.setattr("platforms.windows.herdr.runtime.cli._runtime_platform", lambda: "windows")
    monkeypatch.setattr("platforms.windows.herdr.runtime.cli._runtime_arch", lambda: "x64")
    state: dict[str, object] = {
        "workspaces": [{"workspace_id": "w1", "focused": False}],
        "panes": {
            "w1:p1": {
                "pane_id": "w1:p1",
                "workspace_id": "w1",
                "tokens": {
                    "ccb_namespace_id": "w1",
                    "ccb_root_pane": "1",
                    "ccb_window": "ccb",
                },
            }
        },
    }

    def tokens_from_command(command: list[str]) -> dict[str, str]:
        tokens: dict[str, str] = {}
        for index, value in enumerate(command):
            if value == "--token" and index + 1 < len(command):
                name, token_value = command[index + 1].split("=", 1)
                tokens[name] = token_value
        return tokens

    def run_fn(command, **kwargs):
        joined = " ".join(command)
        panes: dict[str, dict[str, object]] = state["panes"]  # type: ignore[assignment]
        if "status --json" in joined:
            return _completed('{"client":{"version":"0.7.5-preview"}}')
        if "--version" in joined:
            return _completed("herdr 0.7.5-preview\n")
        if "api schema --json" in joined:
            return _completed('{"title":"Herdr API"}')
        if "workspace list" in joined:
            return _completed(json.dumps({"result": {"workspaces": state["workspaces"]}}))
        if "pane list" in joined:
            return _completed(json.dumps({"result": {"panes": list(panes.values())}}))
        if "pane report-metadata" in joined:
            pane_id = command[command.index("report-metadata") + 1]
            panes[pane_id]["tokens"].update(tokens_from_command(command))
            return _completed("")
        raise AssertionError(joined)

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
    )
    backend = HerdrBackend(
        client=HerdrSocketClient(
            request_fn=adapter,
            socket_ref=adapter.socket_ref,
            allow_session_scoped_ipc_refs=adapter.allow_session_scoped_ipc_refs,
        ),
        capability_gate=_supported_gate(),
    )
    namespace = backend.namespace_ref("ccb-demo", "w1")
    root = backend.window_root_pane(namespace, window_name="ccb")

    backend.set_pane_identity(
        root,
        title="agent",
        agent_label="agent",
        project_id="demo",
        role="agent",
        slot_key="worker",
        window_name="ccb",
        sidebar_instance="sidebar",
        managed_by="ccbd",
    )
    assert backend.list_panes_by_user_options({"@ccb_role": "agent"}) == ["w1:p1"]

    backend.set_pane_identity(
        root,
        title="agent",
        agent_label="",
        project_id="demo",
        role=None,
        slot_key=None,
        window_name="ccb",
        sidebar_instance=None,
        managed_by=None,
    )

    tokens = state["panes"]["w1:p1"]["tokens"]  # type: ignore[index]
    assert backend.list_panes_by_user_options({"@ccb_role": "agent"}) == []
    assert tokens["ccb_namespace_id"] == "w1"
    assert tokens["ccb_root_pane"] == "1"
    assert tokens["ccb_role"] == ""
    assert tokens["ccb_slot"] == ""
    assert tokens["ccb_sidebar_instance"] == ""


def test_herdr_backend_window_root_pane_fallback_rejects_foreign_namespace_root() -> None:
    def request(operation: str, payload: dict[str, object]) -> dict[str, object]:
        if operation == "server_info":
            return {
                "result": {
                    "version": "0.7.5-preview",
                    "api_schema": "Herdr API",
                    "platform": "windows",
                    "arch": "x64",
                }
            }
        if operation == "window_root_pane":
            return {"status": "not-found", "detail": "missing root"}
        if operation == "list_panes":
            return {
                "status": "ok",
                "panes": [
                    {
                        "pane_id": "w2:p1",
                        "workspace_id": "w2",
                        "tokens": {
                            "ccb_namespace_id": "w2",
                            "ccb_root_pane": "1",
                            "ccb_window": "ccb",
                        },
                    }
                ],
            }
        raise AssertionError(operation)

    backend = HerdrBackend(
        client=HerdrSocketClient(request_fn=request, socket_ref="herdr://local"),
        capability_gate=_supported_gate(),
    )
    namespace = backend.namespace_ref("ccb-demo", "w1")

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        backend.window_root_pane(namespace, window_name="ccb")

    assert exc_info.value.category == "not-found"


def test_herdr_capability_gate_requires_facade_specific_primitives() -> None:
    supported = dict((_supported_gate().capabilities or {})["command_status"])
    supported.pop("workspace_focus")
    gate = HerdrCapabilityGate.from_spike_evidence(
        {
            "adapter_recommendation": "continue",
            "verdict": "pass",
            "failure_class": "none",
            "capability_projection": {
                "command_status": supported,
                "semantic_status": supported,
                "windows_beta_gaps": [],
                "blocking_gaps": [],
            },
        },
        capability_report_ref="evidence/herdr-capabilities.json",
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        gate.require_supported("attach_namespace")

    assert exc_info.value.category == "unsupported"
    assert "workspace_focus" in exc_info.value.evidence["unsupported_capabilities"]


def test_default_project_namespace_backend_uses_auto_selection(monkeypatch) -> None:
    monkeypatch.delenv("CCB_HERDR_CAPABILITY_REPORT", raising=False)
    monkeypatch.delenv("CCB_HERDR_SOCKET_REF", raising=False)
    calls: list[object] = []

    def resolve(terminal_type=None):
        calls.append(terminal_type)
        return "auto-backend"

    monkeypatch.setattr(namespace_controller, "resolve_terminal_backend", resolve)

    assert namespace_controller.default_project_namespace_backend() == "auto-backend"
    assert calls == [None]


def test_default_project_namespace_backend_retries_explicit_herdr_when_auto_returns_none(monkeypatch) -> None:
    monkeypatch.delenv("CCB_HERDR_CAPABILITY_REPORT", raising=False)
    monkeypatch.delenv("CCB_HERDR_SOCKET_REF", raising=False)
    calls: list[object] = []

    def resolve(terminal_type=None):
        calls.append(terminal_type)
        return "herdr-backend" if terminal_type == "herdr" else None

    monkeypatch.setattr(namespace_controller, "resolve_terminal_backend", resolve)

    assert namespace_controller.default_project_namespace_backend() == "herdr-backend"
    assert calls == [None, "herdr"]


def test_default_project_namespace_backend_uses_explicit_herdr_when_runtime_configured(monkeypatch) -> None:
    calls: list[object] = []

    class LegacyBackend:
        backend_impl = "tmux"

    def resolve(terminal_type=None):
        calls.append(terminal_type)
        return "herdr-backend" if terminal_type == "herdr" else LegacyBackend()

    monkeypatch.setenv("CCB_HERDR_CAPABILITY_REPORT", "evidence/herdr.json")
    monkeypatch.setattr(namespace_controller, "resolve_terminal_backend", resolve)

    assert namespace_controller.default_project_namespace_backend() == "herdr-backend"
    assert calls == ["herdr"]


def test_get_backend_for_namespace_teardown_reattaches_without_selection_gate(monkeypatch) -> None:
    monkeypatch.delenv("CCB_HERDR_CAPABILITY_REPORT", raising=False)
    monkeypatch.delenv("CCB_HERDR_SOCKET_REF", raising=False)
    monkeypatch.delenv("CCB_HERDR_SESSION", raising=False)
    monkeypatch.delenv("CCB_HERDR_EXE", raising=False)

    backend = terminal_api.get_backend_for_namespace_teardown(
        {
            "backend_family": "herdr-native",
            "backend_impl": "herdr",
            "namespace_id": "w-anchor",
            "session_name": "ccb-herdr",
            "ipc_kind": "herdr_socket",
            "ipc_ref": "herdr://local",
            "restore_token": "restore-token",
        }
    )

    assert isinstance(backend, HerdrBackend)
    assert getattr(backend, "_ccb_project_namespace_ref")["namespace_id"] == "w-anchor"
    # Teardown operations must be permitted even without ambient capability evidence.
    backend.capabilities()
    backend._capability_gate.require_supported("destroy_namespace")
    backend._capability_gate.require_supported("kill_server")
    backend._capability_gate.require_supported("namespace_alive")
    # The client socket ref is derived from the persisted ref, so the persisted
    # ref passes the backend's namespace-ref validation before the destroy call.
    assert backend._client.socket_ref == "herdr://local"
    validated = backend._namespace_ref_from_mapping(
        {
            "backend_impl": "herdr",
            "namespace_id": "w-anchor",
            "session_name": "ccb-herdr",
            "ipc_kind": "herdr_socket",
            "ipc_ref": "herdr://local",
            "restore_token": "restore-token",
        },
        operation="destroy_namespace",
    )
    assert validated["namespace_id"] == "w-anchor"


def test_herdr_cli_resolves_common_windows_install_when_not_on_path(monkeypatch) -> None:
    monkeypatch.setattr(herdr_cli, "_runtime_platform", lambda: "windows")
    monkeypatch.setattr(herdr_cli, "_runtime_arch", lambda: "x64")
    monkeypatch.delenv("CCB_HERDR_EXE", raising=False)
    monkeypatch.setattr(
        herdr_cli.os.path,
        "isfile",
        lambda path: str(path).replace("\\", "/") == "C:/Users/me/AppData/Local/Programs/Herdr/herdr.exe",
    )
    monkeypatch.setenv("LOCALAPPDATA", "C:/Users/me/AppData/Local")
    commands: list[list[str]] = []

    def run_fn(command, **kwargs):
        commands.append(list(command))
        if command[-2:] == ["status", "--json"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout='{"status":"ok","client":{"version":"0.8.0"}}',
                stderr="",
            )
        if command[-3:] == ["api", "schema", "--json"]:
            return subprocess.CompletedProcess(
                command,
                0,
                stdout='{"status":"ok","title":"herdr-api"}',
                stderr="",
            )
        if command[-1:] == ["--version"]:
            return subprocess.CompletedProcess(command, 0, stdout="herdr 0.8.0\n", stderr="")
        raise AssertionError(f"unexpected command: {command}")

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-test",
        run_fn=run_fn,
        which_fn=lambda name: None,
    )

    result = adapter("server_info", {})

    assert result["version"] == "0.8.0"
    assert commands
    assert all(
        command[0].replace("\\", "/") == "C:/Users/me/AppData/Local/Programs/Herdr/herdr.exe"
        for command in commands
    )


def test_herdr_socket_client_rejects_window_root_pane_session_mismatch() -> None:
    client = HerdrSocketClient(
        request_fn=lambda operation, payload: {
            "status": "ok",
            "result": {"pane_id": "pane-1", "session_name": "other-session"},
        },
        socket_ref="herdr://local",
    )
    namespace = {
        "backend_family": "herdr-native",
        "backend_impl": "herdr",
        "namespace_id": "workspace-1",
        "session_name": "ccb-demo",
        "ipc_kind": "herdr_socket",
        "ipc_ref": "herdr://local",
        "restore_token": "ccb-demo::workspace-1",
    }

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        client.window_root_pane(namespace, window_name="ccb")

    assert exc_info.value.category == "command-failed"
    assert exc_info.value.evidence["expected_session_name"] == "ccb-demo"
    assert exc_info.value.evidence["actual_session_name"] == "other-session"


def test_herdr_cli_request_adapter_rejects_exit_zero_failed_json_status() -> None:
    def run_fn(command, **kwargs):
        joined = " ".join(command)
        if "workspace create" in joined:
            return _completed(
                '{"status":"failed","detail":"workspace failed","result":{"workspace":{"workspace_id":"w1"}}}'
            )
        raise AssertionError(joined)

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        adapter("create_session", {"project_id": "demo", "cwd": "D:/demo", "title": "demo"})

    assert exc_info.value.category == "command-failed"
    assert exc_info.value.detail == "workspace failed"


def test_herdr_cli_request_adapter_rejects_create_session_without_workspace_id() -> None:
    def run_fn(command, **kwargs):
        joined = " ".join(command)
        if "workspace create" in joined:
            return _completed('{"result":{"workspace":{},"root_pane":{}}}')
        raise AssertionError(joined)

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        adapter("create_session", {"project_id": "demo", "cwd": "D:/demo", "title": "demo"})

    assert exc_info.value.category == "command-failed"
    assert "workspace_id" in exc_info.value.detail


def test_herdr_cli_request_adapter_rejects_create_pane_without_pane_id() -> None:
    def run_fn(command, **kwargs):
        joined = " ".join(command)
        if "pane list" in joined:
            return _completed('{"result":{"panes":[{"pane_id":"w1:p1","workspace_id":"w1"}]}}')
        if "pane split" in joined:
            return _completed('{"result":{"pane":{"workspace_id":"w1"}}}')
        if "pane run" in joined:
            raise AssertionError("pane run must not execute without pane_id")
        raise AssertionError(joined)

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        adapter("create_pane", {"namespace_id": "w1", "session_name": "ccb-demo"})

    assert exc_info.value.category == "command-failed"
    assert "pane_id" in exc_info.value.detail


def test_herdr_cli_request_adapter_rejects_non_list_create_pane_command() -> None:
    def run_fn(command, **kwargs):
        raise AssertionError("command validation should happen before Herdr command execution")

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        adapter("create_pane", {"namespace_id": "w1", "session_name": "ccb-demo", "command": "python -V"})

    assert exc_info.value.category == "command-failed"
    assert "list of argv parts" in exc_info.value.detail


def test_herdr_cli_request_adapter_kill_pane_accepts_empty_success_output() -> None:
    def run_fn(command, **kwargs):
        joined = " ".join(command)
        if "pane close" in joined:
            return _completed("")
        raise AssertionError(joined)

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
    )

    killed = adapter("kill_pane", {"pane_id": "w1:p1", "session_name": "ccb-demo"})

    assert killed["status"] == "ok"
    assert killed["pane_id"] == "w1:p1"


def test_herdr_cli_request_adapter_rejects_nested_failed_json_status() -> None:
    def run_fn(command, **kwargs):
        joined = " ".join(command)
        if "pane list" in joined:
            return _completed('{"result":{"panes":[{"pane_id":"w1:p1","workspace_id":"w1"}]}}')
        if "pane split" in joined:
            return _completed(
                '{"status":"ok","detail":"outer detail","result":{"status":"failed","message":"split failed","pane":{"pane_id":"w1:p2","workspace_id":"w1"}}}'
            )
        raise AssertionError(joined)

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        adapter("create_pane", {"namespace_id": "w1", "session_name": "ccb-demo"})

    assert exc_info.value.category == "command-failed"
    assert exc_info.value.detail == "split failed"


def test_herdr_cli_request_adapter_runs_command_after_create_pane_split() -> None:
    commands: list[list[str]] = []

    def run_fn(command, **kwargs):
        commands.append(command)
        joined = " ".join(command)
        if "pane list" in joined:
            return _completed('{"result":{"panes":[{"pane_id":"w1:p1","workspace_id":"w1"}]}}')
        if "pane split" in joined:
            return _completed('{"result":{"pane":{"pane_id":"w1:p2","workspace_id":"w1"}}}')
        if "pane run" in joined:
            assert command[1:3] == ["--session", "ccb-demo"]
            expected_command = (
                subprocess.list2cmdline(["python", "-c", "print('a b')"])
                if sys.platform.startswith("win")
                else shlex.join(["python", "-c", "print('a b')"])
            )
            assert command[-2] == "w1:p2"
            assert command[-1] == expected_command
            assert command[1:3] == ["--session", "ccb-demo"]
            return _completed("")
        raise AssertionError(joined)

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
    )

    pane = adapter(
        "create_pane",
        {"namespace_id": "w1", "session_name": "ccb-demo", "command": ["python", "-c", "print('a b')"]},
    )

    assert pane["pane_id"] == "w1:p2"
    assert any("pane run" in " ".join(command) for command in commands)


def test_herdr_cli_request_adapter_waits_for_pane_before_metadata_report() -> None:
    commands: list[list[str]] = []
    pane_list_calls = 0

    def run_fn(command, **kwargs):
        nonlocal pane_list_calls
        commands.append(command)
        joined = " ".join(command)
        if "pane list" in joined:
            pane_list_calls += 1
            if pane_list_calls == 1:
                return _completed('{"result":{"panes":[{"pane_id":"w1:p1","workspace_id":"w1"}]}}')
            return _completed(
                '{"result":{"panes":[{"pane_id":"w1:p1","workspace_id":"w1"},'
                '{"pane_id":"w1:p2","workspace_id":"w1"}]}}'
            )
        if "pane report-metadata" in joined:
            assert pane_list_calls >= 2
            return _completed("")
        if "status --json" in joined:
            return _completed('{"client":{"version":"0.7.5-preview"}}')
        if "--version" in joined:
            return _completed("herdr 0.7.5-preview\n")
        if "api schema --json" in joined:
            return _completed('{"title":"Herdr API"}')
        raise AssertionError(joined)

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
        sleep_fn=lambda _seconds: None,
    )

    result = adapter(
        "set_pane_identity",
        {
            "pane_id": "w1:p2",
            "session_name": "ccb-demo",
            "title": "agent2",
            "agent_label": "agent2",
            "tokens": {"ccb_role": "agent"},
        },
    )

    assert result == {"status": "ok", "pane_id": "w1:p2"}
    assert pane_list_calls == 2


def test_herdr_cli_request_adapter_splits_from_requested_parent_when_parent_is_non_root() -> None:
    split_commands: list[list[str]] = []

    def run_fn(command, **kwargs):
        joined = " ".join(command)
        if "pane list" in joined:
            return _completed(
                '{"result":{"panes":['
                '{"pane_id":"w1:p1","workspace_id":"w1","tokens":{"ccb_root_pane":"1"}},'
                '{"pane_id":"w1:p2","workspace_id":"w1","tokens":{"ccb_role":"agent"}}'
                ']}}'
            )
        if "pane split" in joined:
            split_commands.append(command)
            return _completed('{"result":{"pane":{"pane_id":"w1:p3","workspace_id":"w1"}}}')
        raise AssertionError(joined)

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
    )

    pane = adapter(
        "create_pane",
        {
            "namespace_id": "w1",
            "session_name": "ccb-demo",
            "parent_pane": "w1:p2",
        },
    )

    assert pane["pane_id"] == "w1:p3"
    assert split_commands[0][split_commands[0].index("split") + 1] == "w1:p2"


def test_herdr_cli_request_adapter_focuses_workspace_for_attach_namespace() -> None:
    commands: list[list[str]] = []

    def run_fn(command, **kwargs):
        commands.append(command)
        joined = " ".join(command)
        if "workspace list" in joined:
            return _completed(
                '{"result":{"workspaces":[{"workspace_id":"w1"},{"workspace_id":"w2"}]}}'
            )
        if "pane list" in joined:
            return _completed(
                '{"result":{"panes":['
                '{"pane_id":"w1:p1","workspace_id":"w1","tokens":{"ccb_namespace_id":"w1","ccb_root_pane":"1","ccb_window":"__ccb_ctl"}},'
                '{"pane_id":"w2:p1","workspace_id":"w2","tokens":{"ccb_namespace_id":"w1","ccb_root_pane":"1","ccb_window":"ccb"}}'
                ']}}'
            )
        return _completed("")

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
    )

    attached = adapter(
        "attach_namespace",
        {
            "namespace_id": "w1",
            "session_name": "restored-session",
            "window_name": "ccb",
            "restore_token": "secret",
        },
    )

    assert attached["status"] == "ok"
    assert attached["namespace_id"] == "w1"
    assert commands[-2] == ["herdr", "--session", "restored-session", "workspace", "focus", "w2"]
    assert commands[-1] == ["herdr", "session", "attach", "restored-session"]
    assert "secret" not in str(commands)


def test_herdr_cli_request_adapter_rejects_create_pane_env_override() -> None:
    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=lambda command, **kwargs: (_ for _ in ()).throw(AssertionError(command)),
        which_fn=lambda name: "herdr",
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        adapter("create_pane", {"namespace_id": "w1", "env": {"SECRET": "value"}})

    assert exc_info.value.category == "command-failed"
    assert "environment overrides" in exc_info.value.detail


def test_herdr_cli_request_adapter_threads_session_scope_and_split_geometry() -> None:
    split_commands: list[list[str]] = []

    def run_fn(command, **kwargs):
        joined = " ".join(command)
        if "pane list" in joined:
            assert command[1:3] == ["--session", "restored-session"]
            return _completed('{"result":{"panes":[{"pane_id":"w1:p1","workspace_id":"w1"}]}}')
        if "pane split" in joined:
            split_commands.append(command)
            assert command[1:3] == ["--session", "restored-session"]
            return _completed('{"result":{"pane":{"pane_id":"w1:p2","workspace_id":"w1"}}}')
        raise AssertionError(joined)

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
    )

    pane = adapter(
        "create_pane",
        {
            "namespace_id": "w1",
            "ipc_ref": "herdr://restored-session",
            "session_name": "restored-session",
            "cwd": "D:/demo",
            "direction": "down",
            "percent": 25,
            "parent_pane": "w1:p1",
        },
    )

    assert pane["pane_id"] == "w1:p2"
    assert pane["session_name"] == "restored-session"
    assert "pane list" not in " ".join(" ".join(command) for command in split_commands)
    assert "w1:p1" in split_commands[0]
    assert "--direction" in split_commands[0]
    assert split_commands[0][split_commands[0].index("--direction") + 1] == "down"
    assert split_commands[0][split_commands[0].index("--ratio") + 1] == "0.25"


def test_herdr_cli_request_adapter_maps_ccbd_bottom_direction_to_herdr_down() -> None:
    split_commands: list[list[str]] = []

    def run_fn(command, **kwargs):
        joined = " ".join(command)
        if "pane list" in joined:
            return _completed('{"result":{"panes":[{"pane_id":"w1:p1","workspace_id":"w1"}]}}')
        if "pane split" in joined:
            split_commands.append(command)
            return _completed('{"result":{"pane":{"pane_id":"w1:p2","workspace_id":"w1"}}}')
        raise AssertionError(joined)

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
    )

    adapter(
        "create_pane",
        {
            "namespace_id": "w1",
            "session_name": "ccb-demo",
            "direction": "bottom",
            "parent_pane": "w1:p1",
        },
    )

    assert split_commands[0][split_commands[0].index("--direction") + 1] == "down"


def test_herdr_cli_request_adapter_honors_non_root_parent_pane() -> None:
    split_commands: list[list[str]] = []

    def run_fn(command, **kwargs):
        joined = " ".join(command)
        if "pane list" in joined:
            return _completed(
                json.dumps(
                    {
                        "result": {
                            "panes": [
                                {
                                    "pane_id": "w1:p1",
                                    "workspace_id": "w1",
                                    "tokens": {"ccb_root_pane": "1"},
                                },
                                {
                                    "pane_id": "w1:p2",
                                    "workspace_id": "w1",
                                    "tokens": {"ccb_role": "agent"},
                                },
                            ]
                        }
                    }
                )
            )
        if "pane split" in joined:
            split_commands.append(command)
            return _completed('{"result":{"pane":{"pane_id":"w1:p3","workspace_id":"w1"}}}')
        raise AssertionError(joined)

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
    )

    adapter(
        "create_pane",
        {
            "namespace_id": "w1",
            "session_name": "ccb-demo",
            "direction": "bottom",
            "parent_pane": "w1:p2",
        },
    )

    assert "w1:p2" in split_commands[0]


@pytest.mark.parametrize("direction", ["left", "up", "sideways"])
def test_herdr_cli_request_adapter_rejects_unrepresentable_split_direction(direction: str) -> None:
    def run_fn(command, **kwargs):
        raise AssertionError(f"Herdr command must not execute for {direction}: {' '.join(command)}")

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        adapter(
            "create_pane",
            {
                "namespace_id": "w1",
                "session_name": "ccb-demo",
                "direction": direction,
                "parent_pane": "w1:p1",
            },
        )

    assert exc_info.value.category == "command-failed"
    assert "unsupported Herdr split direction" in exc_info.value.detail


def test_herdr_cli_request_adapter_rejects_parent_pane_outside_namespace() -> None:
    def run_fn(command, **kwargs):
        joined = " ".join(command)
        if "pane list" in joined:
            return _completed('{"result":{"panes":[{"pane_id":"other:p1","workspace_id":"other"}]}}')
        if "workspace list" in joined:
            return _completed('{"result":{"workspaces":[]}}')
        raise AssertionError(joined)

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        adapter(
            "create_pane",
            {
                "namespace_id": "w1",
                "session_name": "ccb-demo",
                "parent_pane": "other:p1",
            },
        )

    assert exc_info.value.category == "not-found"
    assert "unknown Herdr parent pane" in exc_info.value.detail


def test_herdr_cli_request_adapter_preserves_socket_ref_override_in_namespace() -> None:
    def run_fn(command, **kwargs):
        joined = " ".join(command)
        if "report-metadata" in joined:
            return _completed("")
        if "workspace create" in joined:
            return _completed(
                '{"result":{"workspace":{"workspace_id":"w1"},"root_pane":{"pane_id":"w1:p1","workspace_id":"w1"}}}'
            )
        if "workspace list" in joined:
            return _completed('{"result":{"workspaces":[{"workspace_id":"w1","label":"demo"}]}}')
        raise AssertionError(joined)

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
        socket_ref="herdr://override",
    )

    namespace = adapter("create_session", {"project_id": "demo", "cwd": "D:/demo", "title": "demo"})
    restored = adapter("restore_session", {"restore_token": namespace["restore_token"]})

    assert namespace["session_name"] == "ccb-demo"
    assert namespace["ipc_ref"] == "herdr://override"
    assert restored["session_name"] == "ccb-demo"
    assert restored["ipc_ref"] == "herdr://override"


def test_herdr_cli_request_adapter_create_session_uses_project_namespace_title_as_session_scope() -> None:
    commands: list[list[str]] = []

    def run_fn(command, **kwargs):
        commands.append(command)
        assert command[1:3] == ["--session", "ccb-project-12345678"]
        joined = " ".join(command)
        if "status server --json" in joined:
            return _completed('{"status":"running","running":true}')
        if "report-metadata" in joined:
            return _completed("")
        if "workspace create" in joined:
            return _completed(
                '{"result":{"workspace":{"workspace_id":"w1"},"root_pane":{"pane_id":"w1:p1","workspace_id":"w1"}}}'
            )
        if "workspace list" in joined:
            return _completed('{"result":{"workspaces":[{"workspace_id":"w1"}]}}')
        raise AssertionError(joined)

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-bootstrap",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
    )

    namespace = adapter(
        "create_session",
        {"project_id": "demo", "cwd": "D:/demo", "title": "ccb-project-12345678"},
    )

    assert namespace["session_name"] == "ccb-project-12345678"
    assert namespace["restore_token"] == "ccb-project-12345678::w1"
    assert namespace["ipc_ref"] == "herdr://ccb-project-12345678"
    assert len(commands) == 6


def test_herdr_cli_request_adapter_restore_uses_restored_session_ipc_ref() -> None:
    commands: list[list[str]] = []

    def run_fn(command, **kwargs):
        commands.append(command)
        joined = " ".join(command)
        if "workspace list" in joined:
            assert command[1:3] == ["--session", "restored-session"]
            return _completed('{"result":{"workspaces":[{"workspace_id":"w1","label":"demo"}]}}')
        raise AssertionError(joined)

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
    )

    restored = adapter("restore_session", {"restore_token": "restored-session::w1"})

    assert restored["session_name"] == "restored-session"
    assert restored["ipc_ref"] == "herdr://restored-session"
    assert commands


def test_herdr_cli_request_adapter_restore_failure_uses_restored_session_ipc_ref() -> None:
    def run_fn(command, **kwargs):
        joined = " ".join(command)
        if "workspace list" in joined:
            assert command[1:3] == ["--session", "restored-session"]
            return _completed('{"result":{"workspaces":[]}}')
        raise AssertionError(joined)

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        adapter("restore_session", {"restore_token": "restored-session::w1"})

    assert exc_info.value.ipc_ref == "herdr://restored-session"


@pytest.mark.parametrize("restore_token", ["w1", "::w1", "ccb-demo::", "ccb-demo::w1::extra"])
def test_herdr_cli_request_adapter_rejects_restore_token_without_session_scope(
    restore_token: str,
) -> None:
    def run_fn(command, **kwargs):
        raise AssertionError("restore token validation should happen before Herdr command execution")

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        adapter("restore_session", {"restore_token": restore_token})

    assert exc_info.value.category == "command-failed"
    assert exc_info.value.operation == "restore_session"


def test_herdr_cli_request_adapter_normalizes_capture_line_count() -> None:
    commands: list[list[str]] = []

    def run_fn(command, **kwargs):
        commands.append(command)
        return _completed("ready")

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
    )

    captured = adapter("capture_pane", {"pane_id": "p1", "session_name": "ccb-demo", "lines": -5})

    assert captured["text"] == "ready"
    assert commands[0][commands[0].index("--lines") + 1] == "1"


def test_herdr_cli_request_adapter_supports_is_alive_probe() -> None:
    commands: list[list[str]] = []

    def run_fn(command, **kwargs):
        commands.append(command)
        return _completed("ready")

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
    )

    result = adapter("is_alive", {"pane_id": "p1", "session_name": "ccb-demo"})

    assert result["status"] == "ok"
    assert result["alive"] is True
    assert "pane read" in " ".join(commands[0])


def test_herdr_cli_request_adapter_is_alive_maps_not_found_to_false() -> None:
    def request_fn(operation: str, payload: dict[str, object]) -> dict[str, object]:
        if operation == "capture_pane":
            raise MuxCommandErrorV2(
                category="not-found",
                backend_impl="herdr",
                operation="capture_pane",
                detail="pane not found",
            )
        raise AssertionError(operation)

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=lambda command, **kwargs: _completed(""),
        which_fn=lambda name: "herdr",
    )
    adapter._capture_pane = lambda payload: request_fn("capture_pane", dict(payload))  # type: ignore[method-assign]

    result = adapter("is_alive", {"pane_id": "p1", "session_name": "ccb-demo"})

    assert result["status"] == "ok"
    assert result["alive"] is False


def test_herdr_cli_request_adapter_reports_pane_agent() -> None:
    commands: list[list[str]] = []

    def run_fn(command, **kwargs):
        commands.append(list(command))
        return _completed("")

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
    )

    result = adapter(
        "report_pane_agent",
        {
            "pane_id": "w1:p2",
            "session_name": "ccb-demo",
            "provider_kind": "claude",
            "state": "unknown",
            "session_id": "ccb-agent-session",
        },
    )

    assert result == {
        "status": "ok",
        "pane_id": "w1:p2",
        "provider_kind": "claude",
        "state": "unknown",
    }
    assert commands == [
        [
            "herdr",
            "--session",
            "ccb-demo",
            "pane",
            "report-agent",
            "w1:p2",
            "--source",
            "ccb",
            "--agent",
            "claude",
            "--state",
            "unknown",
            "--agent-session-id",
            "ccb-agent-session",
        ]
    ]


def test_herdr_cli_request_adapter_reports_pane_agent_with_seq() -> None:
    commands: list[list[str]] = []

    def run_fn(command, **kwargs):
        commands.append(list(command))
        return _completed("")

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
    )

    result = adapter(
        "report_pane_agent",
        {
            "pane_id": "w1:p2",
            "session_name": "ccb-demo",
            "provider_kind": "codex",
            "state": "working",
            "seq": 7,
            "session_id": "ccb-agent-session",
            "session_path": "D:/demo/.ccb/session",
        },
    )

    assert result["status"] == "ok"
    assert commands[0][-8:] == [
        "--state",
        "working",
        "--seq",
        "7",
        "--agent-session-id",
        "ccb-agent-session",
        "--agent-session-path",
        "D:/demo/.ccb/session",
    ]


def test_herdr_cli_request_adapter_reports_pane_agent_session() -> None:
    commands: list[list[str]] = []

    def run_fn(command, **kwargs):
        commands.append(list(command))
        return _completed("")

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
    )

    result = adapter(
        "report_pane_agent_session",
        {
            "pane_id": "w1:p2",
            "session_name": "ccb-demo",
            "provider_kind": "codex",
            "seq": 3,
            "session_id": "ccb-agent-session",
            "session_path": "D:/demo/.ccb/session",
        },
    )

    assert result["status"] == "ok"
    assert commands == [
        [
            "herdr",
            "--session",
            "ccb-demo",
            "pane",
            "report-agent-session",
            "w1:p2",
            "--source",
            "ccb",
            "--agent",
            "codex",
            "--seq",
            "3",
            "--agent-session-id",
            "ccb-agent-session",
            "--agent-session-path",
            "D:/demo/.ccb/session",
        ]
    ]


def test_herdr_cli_request_adapter_releases_pane_agent() -> None:
    commands: list[list[str]] = []

    def run_fn(command, **kwargs):
        commands.append(list(command))
        return _completed("")

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
    )

    result = adapter(
        "release_pane_agent",
        {
            "pane_id": "w1:p2",
            "session_name": "ccb-demo",
            "provider_kind": "codex",
            "seq": 9,
        },
    )

    assert result["status"] == "ok"
    assert commands == [
        [
            "herdr",
            "--session",
            "ccb-demo",
            "pane",
            "release-agent",
            "w1:p2",
            "--source",
            "ccb",
            "--agent",
            "codex",
            "--seq",
            "9",
        ]
    ]


def test_herdr_cli_request_adapter_rejects_negative_seq() -> None:
    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=lambda command, **kwargs: _completed(""),
        which_fn=lambda name: "herdr",
    )

    with pytest.raises(MuxCommandErrorV2):
        adapter(
            "release_pane_agent",
            {
                "pane_id": "w1:p2",
                "session_name": "ccb-demo",
                "provider_kind": "codex",
                "seq": -1,
            },
        )


def test_herdr_cli_request_adapter_is_alive_maps_command_not_found_to_false() -> None:
    def run_fn(command, **kwargs):
        raise _called_process_error(command, stderr="pane not found")

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
    )

    result = adapter("is_alive", {"pane_id": "p1", "session_name": "ccb-demo"})

    assert result["status"] == "ok"
    assert result["alive"] is False


def test_herdr_cli_request_adapter_redacts_send_text_failure_evidence() -> None:
    secret = "TOKEN=super-secret-value"

    def run_fn(command, **kwargs):
        raise _called_process_error(command, stderr="send failed")

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        adapter("send_text", {"pane_id": "p1", "text": secret})

    assert secret not in str(exc_info.value.evidence)
    redacted = [v for v in exc_info.value.evidence.get("argv", []) if v == "<redacted>"]
    assert len(redacted) >= 1
    assert exc_info.value.evidence["operation"] == "send_text"


def test_herdr_cli_request_adapter_command_failure_uses_effective_session_ipc_ref() -> None:
    def run_fn(command, **kwargs):
        assert command[1:3] == ["--session", "restored-session"]
        raise _called_process_error(command, stderr="send failed")

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
    )

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        adapter("send_text", {"pane_id": "p1", "session_name": "restored-session", "text": "hello"})

    assert exc_info.value.ipc_ref == "herdr://restored-session"


def test_herdr_cli_request_adapter_server_info_rejects_non_windows_runtime(monkeypatch) -> None:
    def run_fn(command, **kwargs):
        joined = " ".join(command)
        if "status --json" in joined:
            return _completed('{"client":{"version":"0.7.5-preview"}}')
        if "--version" in joined:
            return _completed("herdr 0.7.5-preview\n")
        if "api schema --json" in joined:
            return _completed('{"title":"Herdr API"}')
        raise AssertionError(joined)

    monkeypatch.setattr(herdr_cli.sys, "platform", "linux")
    monkeypatch.setattr(herdr_cli.platform, "machine", lambda: "aarch64")
    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
    )
    client = HerdrSocketClient(request_fn=adapter, socket_ref=adapter.socket_ref)

    with pytest.raises(MuxCommandErrorV2) as exc_info:
        client.server_info()

    assert exc_info.value.category == "schema-mismatch"
    assert exc_info.value.evidence["actual_platform"] == "linux"
    assert exc_info.value.evidence["actual_arch"] == "arm64"


def test_herdr_cli_request_adapter_omits_empty_cwd_arguments() -> None:
    commands: list[list[str]] = []

    def run_fn(command, **kwargs):
        commands.append(command)
        joined = " ".join(command)
        if "report-metadata" in joined:
            return _completed("")
        if "workspace create" in joined:
            assert "--cwd" not in command
            return _completed(
                '{"result":{"workspace":{"workspace_id":"w1"},"root_pane":{"pane_id":"w1:p1","workspace_id":"w1"}}}'
            )
        if "workspace list" in joined:
            return _completed('{"result":{"workspaces":[{"workspace_id":"w1"}]}}')
        if "pane list" in joined:
            return _completed('{"result":{"panes":[{"pane_id":"w1:p1","workspace_id":"w1"}]}}')
        if "pane split" in joined:
            assert "--cwd" not in command
            return _completed('{"result":{"pane":{"pane_id":"w1:p2","workspace_id":"w1"}}}')
        raise AssertionError(joined)

    adapter = HerdrCliRequestAdapter(
        session_name="ccb-demo",
        herdr_executable="herdr",
        run_fn=run_fn,
        which_fn=lambda name: "herdr",
    )

    namespace = adapter("create_session", {"project_id": "demo", "cwd": "", "title": "demo"})
    pane = adapter("create_pane", {"namespace_id": namespace["namespace_id"], "cwd": ""})

    assert namespace["namespace_id"] == "w1"
    assert pane["pane_id"] == "w1:p2"
    assert commands


def _supported_gate() -> HerdrCapabilityGate:
    supported = {
        "session_attach": "supported",
        "pane_spawn": "supported",
        "send_input": "supported",
        "read_output": "supported",
        "kill_pane": "supported",
        "workspace_create": "supported",
        "workspace_list": "supported",
        "workspace_focus": "supported",
        "workspace_close": "supported",
        "workspace_metadata": "supported",
        "pane_metadata": "supported",
        "pane_list": "supported",
        "pane_split": "supported",
        "pane_run": "supported",
    }
    return HerdrCapabilityGate.from_spike_evidence(
        {
            "adapter_recommendation": "continue",
            "verdict": "pass",
            "failure_class": "none",
            "capability_projection": {
                "command_status": supported,
                "semantic_status": supported,
                "windows_beta_gaps": [],
                "blocking_gaps": [],
            },
        },
        capability_report_ref="evidence/herdr-capabilities.json",
    )


def _windows_x64_platform_gate() -> dict[str, object]:
    return {
        "supported": True,
        "os_platform": "win32",
        "cpu_arch": "x64",
        "python_bitness": "64bit",
        "is_wsl": False,
    }


def _fake_herdr_request(
    *,
    server_info: dict[str, object] | None = None,
):
    state = {
        "server_info": {
            "version": "herdr 0.7.5-preview",
            "api_schema": "Herdr API",
            "platform": "windows",
            "arch": "x64",
        },
        "namespace": {
            "namespace_id": "workspace-1",
            "session_name": "ccb-demo",
            "restore_token": "ccb-demo::workspace-1",
        },
        "pane": {
            "pane_id": "pane-1",
            "session_name": "ccb-demo",
            "output": "python ready",
        },
    }
    if server_info is not None:
        state["server_info"].update(server_info)

    def request(operation: str, payload: dict[str, object]) -> dict[str, object]:
        if operation == "server_info":
            return dict(state["server_info"])
        if operation == "create_session":
            return dict(state["namespace"])
        if operation == "restore_session":
            if payload["restore_token"] != "ccb-demo::workspace-1":
                raise AssertionError(f"unexpected restore_token: {payload['restore_token']!r}")
            return dict(state["namespace"])
        if operation == "create_pane":
            pane = dict(state["pane"])
            pane["session_name"] = payload.get("session_name") or pane["session_name"]
            return pane
        if operation == "send_text":
            if payload["text"] != "secret typed text":
                raise AssertionError(f"unexpected send_text payload: {payload['text']!r}")
            return {"status": "ok", "pane_id": payload["pane_id"]}
        if operation == "capture_pane":
            return {"status": "ok", "pane_id": payload["pane_id"], "text": state["pane"]["output"]}
        if operation == "kill_pane":
            return {"status": "ok", "pane_id": payload["pane_id"]}
        if operation in {"report_pane_agent", "report_pane_agent_session", "release_pane_agent"}:
            return {"status": "ok", "pane_id": payload["pane_id"]}
        raise AssertionError(f"unexpected fake Herdr operation {operation}")

    return request


class _FakeRequestAdapter:
    socket_ref = "herdr://local"

    def __call__(self, operation: str, payload: dict[str, object]) -> dict[str, object]:
        return _fake_herdr_request()(operation, payload)


class _PrepareFailsBackend:
    def __init__(self) -> None:
        self.prepare_calls = 0

    def prepare_server(self) -> None:
        self.prepare_calls += 1
        raise MuxCommandErrorV2(
            category="schema-mismatch",
            backend_impl="herdr",
            operation="server_info",
            detail="schema mismatch",
            ipc_ref="herdr://local",
        )


class _PreparedBackend:
    def __init__(self) -> None:
        self.prepare_calls = 0

    def prepare_server(self) -> None:
        self.prepare_calls += 1


def _completed(stdout: str):
    class _Result:
        def __init__(self, stdout: str) -> None:
            self.stdout = stdout
            self.stderr = ""

    return _Result(stdout)


def _called_process_error(command: list[str], *, stderr: str = "") -> Exception:
    return subprocess.CalledProcessError(1, command, stderr=stderr)


def test_herdr_adapter_pane_process_info_parses_foreground_pid(monkeypatch) -> None:
    """方向 A：pane process-info 解析前台进程 pid（供 runtime_pid 回填）。"""
    adapter = HerdrCliRequestAdapter(session_name="ccb-test")
    result = _completed(
        json.dumps(
            {
                "id": "cli:pane:process_info",
                "result": {
                    "process_info": {
                        "foreground_processes": [{"name": "codex.exe", "pid": 4321}]
                    }
                },
                "type": "pane_process_info",
            }
        )
    )
    monkeypatch.setattr(adapter, "_command", lambda *args, **kwargs: result)
    payload = adapter._pane_process_info({"pane_id": "wX:p1", "session_name": "ccb-test"})
    assert payload["status"] == "ok"
    assert payload["foreground_pid"] == 4321
    assert payload["pane_id"] == "wX:p1"


def test_herdr_adapter_pane_process_info_missing_pane_id_raises() -> None:
    adapter = HerdrCliRequestAdapter(session_name="ccb-test")
    with pytest.raises(MuxCommandErrorV2):
        adapter._pane_process_info({"session_name": "ccb-test"})
