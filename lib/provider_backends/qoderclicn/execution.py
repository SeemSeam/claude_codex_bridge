from __future__ import annotations

from pathlib import Path

from provider_backends.native_cli_support import (
    NativeCliExecutionConfig,
    NativeCliExecutionRequest,
    NativeCliObservation,
    NativeCliSubprocessAdapter,
)
from provider_backends.qoder.execution import (
    QoderPaneExecutionAdapter,
    _build_qoder_command,
    _observe_qoder_output,
    _qoder_session_id_for_job,
    build_qoder_pane_execution_adapter,
)
from provider_backends.qoderclicn.session import load_project_session


def build_execution_adapter() -> QoderPaneExecutionAdapter:
    return build_qoder_pane_execution_adapter(
        provider="qoderclicn",
        load_project_session_fn=load_project_session,
    )


def build_headless_execution_adapter() -> NativeCliSubprocessAdapter:
    return NativeCliSubprocessAdapter(
        NativeCliExecutionConfig(
            provider="qoderclicn",
            session_filename=".qoderclicn-session",
            command_builder=_build_command,
            observer=observe_qoderclicn_output,
            output_kind="jsonl",
            mode="qoderclicn_run",
            start_failed_reason="qoderclicn_run_start_failed",
            failed_reason="qoderclicn_run_failed",
            empty_reason="qoderclicn_empty_reply",
            run_error_reason="qoderclicn_run_error",
            complete_reason="qoderclicn_run_stop",
            process_exit_complete_reason="qoderclicn_run_exit",
            missing_terminal_reason="qoderclicn_native_terminal_missing",
            timeout_reason="qoderclicn_run_timeout",
            terminal_on_process_exit=False,
        )
    )


def _build_command(request: NativeCliExecutionRequest) -> list[str]:
    return _build_qoder_command(request, provider="qoderclicn")


def observe_qoderclicn_output(path: Path) -> NativeCliObservation:
    return _observe_qoder_output(
        path,
        result_error="qoderclicn_result_error",
        require_explicit_success=True,
        require_stop_reason=True,
    )


def _qoderclicn_session_id_for_job(job_id: str) -> str:
    return _qoder_session_id_for_job(job_id, provider="qoderclicn")


__all__ = [
    "build_execution_adapter",
    "build_headless_execution_adapter",
    "observe_qoderclicn_output",
]
