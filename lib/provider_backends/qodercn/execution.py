from __future__ import annotations

from pathlib import Path

from provider_backends.native_cli_support import (
    NativeCliExecutionConfig,
    NativeCliExecutionRequest,
    NativeCliObservation,
    NativeCliSubprocessAdapter,
)
from provider_backends.qoder.execution import (
    _build_qoder_command,
    _observe_qoder_output,
    _qoder_session_id_for_job,
)


def build_execution_adapter() -> NativeCliSubprocessAdapter:
    return NativeCliSubprocessAdapter(
        NativeCliExecutionConfig(
            provider="qodercn",
            session_filename=".qodercn-session",
            command_builder=_build_command,
            observer=observe_qodercn_output,
            output_kind="jsonl",
            mode="qodercn_run",
            start_failed_reason="qodercn_run_start_failed",
            failed_reason="qodercn_run_failed",
            empty_reason="qodercn_empty_reply",
            run_error_reason="qodercn_run_error",
            complete_reason="qodercn_run_stop",
            process_exit_complete_reason="qodercn_run_exit",
            missing_terminal_reason="qodercn_native_terminal_missing",
            timeout_reason="qodercn_run_timeout",
            terminal_on_process_exit=False,
        )
    )


def _build_command(request: NativeCliExecutionRequest) -> list[str]:
    return _build_qoder_command(request, provider="qodercn")


def observe_qodercn_output(path: Path) -> NativeCliObservation:
    return _observe_qoder_output(
        path,
        result_error="qodercn_result_error",
        require_explicit_success=True,
        require_stop_reason=True,
    )


def _qodercn_session_id_for_job(job_id: str) -> str:
    return _qoder_session_id_for_job(job_id, provider="qodercn")


__all__ = ["build_execution_adapter", "observe_qodercn_output"]
