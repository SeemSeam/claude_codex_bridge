from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from provider_core.runtime_shared import provider_start_parts
from provider_execution.active_runtime.polling_runtime.result import (
    runtime_error_result,
)
from provider_execution.base import (
    ProviderPollResult,
    ProviderRuntimeContext,
    ProviderSubmission,
)
from provider_execution.reliability import CompletionReliabilityPolicy

from provider_backends.native_cli_support import (
    NativeCliExecutionConfig,
    NativeCliExecutionRequest,
    NativeCliObservation,
    NativeCliSubprocessAdapter,
)

OMP_EXECUTION_MODE_ENV = "CCB_OMP_EXECUTION_MODE"
OMP_HEADLESS_MODE = "omp_run"


class OmpExecutionAdapter:
    provider = "omp"
    restart_resume_supported = True
    completion_reliability_policy = CompletionReliabilityPolicy(
        provider="omp",
        primary_authority="omp_extension_agent_end_final",
        no_terminal_timeout_s=0.0,
    )

    def __init__(self) -> None:
        from .pane_execution import OmpPaneExecutionAdapter

        self.pane = OmpPaneExecutionAdapter()
        self.headless = build_headless_execution_adapter()

    def restore_diagnostics(self) -> dict[str, object]:
        return {
            "resume_supported": True,
            "restore_mode": "persisted_mode_dispatch",
            "restore_reason": "omp_execution_mode_aware_restore",
            "restore_detail": (
                "Visible-pane jobs rebind to the exact live OMP extension "
                "instance; persisted omp_run jobs retain the headless "
                "resubmit contract"
            ),
        }

    def start(
        self,
        job,
        *,
        context: ProviderRuntimeContext | None,
        now: str,
    ) -> ProviderSubmission:
        adapter = (
            self.headless
            if _configured_execution_mode() == "headless"
            else self.pane
        )
        return adapter.start(job, context=context, now=now)

    def poll(
        self,
        submission: ProviderSubmission,
        *,
        now: str,
    ) -> ProviderPollResult | None:
        mode = str(submission.runtime_state.get("mode") or "")
        if mode in {"passive", "error"}:
            return runtime_error_result(
                submission,
                now=now,
                reason=str(
                    submission.runtime_state.get("reason")
                    or "runtime_unavailable"
                ),
                error=str(submission.runtime_state.get("error") or ""),
            )
        adapter = self._adapter_for_mode(mode)
        if adapter is None:
            return runtime_error_result(
                submission,
                now=now,
                reason="runtime_state_corrupt",
                error=f"unsupported_omp_execution_mode:{mode or 'missing'}",
            )
        return adapter.poll(submission, now=now)

    def cancel(self, submission: ProviderSubmission) -> None:
        adapter = self._adapter_for_mode(
            str(submission.runtime_state.get("mode") or "")
        )
        cancel = getattr(adapter, "cancel", None)
        if callable(cancel):
            cancel(submission)

    def export_runtime_state(
        self,
        submission: ProviderSubmission,
    ) -> dict[str, object]:
        adapter = self._adapter_for_mode(
            str(submission.runtime_state.get("mode") or "")
        )
        export = getattr(adapter, "export_runtime_state", None)
        if callable(export):
            return dict(export(submission))
        state = dict(submission.runtime_state)
        state.pop("backend", None)
        return state

    def resume(
        self,
        job,
        submission: ProviderSubmission,
        *,
        context: ProviderRuntimeContext | None,
        persisted_state,
        now: str,
    ) -> ProviderSubmission | None:
        mode = str(submission.runtime_state.get("mode") or "")
        if isinstance(persisted_state, dict):
            mode = str(persisted_state.get("mode") or mode)
        adapter = self._adapter_for_mode(mode)
        resume = getattr(adapter, "resume", None)
        if not callable(resume):
            return None
        return resume(
            job,
            submission,
            context=context,
            persisted_state=persisted_state,
            now=now,
        )

    def _adapter_for_mode(self, mode: str):
        from .pane_execution import OMP_PANE_MODE

        if mode == OMP_PANE_MODE:
            return self.pane
        if mode == OMP_HEADLESS_MODE:
            return self.headless
        return None


def build_execution_adapter() -> OmpExecutionAdapter:
    return OmpExecutionAdapter()


def build_headless_execution_adapter() -> NativeCliSubprocessAdapter:
    return NativeCliSubprocessAdapter(
        NativeCliExecutionConfig(
            provider="omp",
            session_filename=".omp-session",
            command_builder=_build_command,
            env_builder=_build_env,
            private_path_env_names=(
                "PI_CODING_AGENT_DIR",
                "PI_CODING_AGENT_SESSION_DIR",
            ),
            observer=observe_omp_json_output,
            output_kind="jsonl",
            mode="omp_run",
            start_failed_reason="omp_run_start_failed",
            failed_reason="omp_run_failed",
            empty_reason="omp_empty_reply",
            run_error_reason="omp_run_error",
            complete_reason="omp_run_stop",
            process_exit_complete_reason="omp_run_exit",
            missing_terminal_reason="omp_native_terminal_missing",
            timeout_reason="omp_run_timeout",
            invalid_protocol_reason="omp_native_protocol_invalid",
            missing_outcome_reason="omp_native_outcome_missing",
            terminal_on_process_exit=False,
            terminal_requires_process_exit=True,
            require_outcome_reason=True,
        )
    )


def _configured_execution_mode() -> str:
    raw = str(os.environ.get(OMP_EXECUTION_MODE_ENV) or "").strip().lower()
    return "headless" if raw == "headless" else "pane"


def _build_command(request: NativeCliExecutionRequest) -> list[str]:
    session_dir = _state_path(request, "omp_session_dir", fallback="sessions")
    session_dir.mkdir(parents=True, exist_ok=True)
    return [
        *provider_start_parts("omp"),
        "--mode",
        "json",
        "--session-dir",
        str(session_dir),
        "--approval-mode",
        "yolo",
        "--print",
        request.prompt,
    ]


def _build_env(request: NativeCliExecutionRequest) -> dict[str, str]:
    session_dir = _state_path(request, "omp_session_dir", fallback="sessions")
    agent_dir = _omp_agent_dir(request)
    session_dir.mkdir(parents=True, exist_ok=True)
    agent_dir.mkdir(parents=True, exist_ok=True)
    return {
        "PI_CODING_AGENT_DIR": str(agent_dir),
        "PI_CODING_AGENT_SESSION_DIR": str(session_dir),
    }


def observe_omp_json_output(path: Path) -> NativeCliObservation:
    if not path or not path.is_file():
        return NativeCliObservation()
    finished = False
    finish_reason = ""
    turn_ref: str | None = None
    completed_at: object | None = None
    outcome_reason = ""
    outcome_error = ""
    protocol_error = ""
    intermediate = False
    current_delta_chunks: list[str] = []
    latest_message_text = ""
    terminal_yield_text = ""
    terminal_yield_status = ""
    try:
        raw_output = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return NativeCliObservation(error=f"read_stdout_failed:{exc}")

    lines = raw_output.splitlines(keepends=True)
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped:
            continue
        if index == len(lines) - 1 and not line.endswith(("\n", "\r")):
            protocol_error = f"unterminated_jsonl_record:{index + 1}"
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            protocol_error = protocol_error or f"invalid_jsonl_record:{index + 1}"
            continue
        if not isinstance(event, dict):
            protocol_error = protocol_error or f"non_object_jsonl_record:{index + 1}"
            continue

        event_type = str(event.get("type") or "").strip().lower().replace("-", "_")
        if event_type == "agent_start":
            if finished:
                intermediate = True
            finished = False
            finish_reason = ""
            outcome_reason = ""
            outcome_error = ""
            latest_message_text = ""
            current_delta_chunks = []
            terminal_yield_text = ""
            terminal_yield_status = ""
            turn_ref = None
            completed_at = None
            continue

        if "tool" in event_type:
            intermediate = True
            if (
                event_type == "tool_execution_end"
                and str(event.get("toolName") or "") == "yield"
            ):
                yield_result = _omp_terminal_yield_result(event)
                if yield_result is not None:
                    terminal_yield_status, terminal_yield_text, outcome_error = (
                        yield_result
                    )
            continue

        message = event.get("message")
        if isinstance(message, dict) and _omp_message_role(message) == "assistant":
            latest_message_text, outcome_reason, outcome_error = _update_omp_assistant(
                message,
                latest_text=latest_message_text,
                outcome_reason=outcome_reason,
                outcome_error=outcome_error,
            )
            turn_ref = _omp_ref(message) or turn_ref
            completed_at = _omp_time(event) or completed_at
            if event_type in {"message_start", "message_end"}:
                current_delta_chunks = []

        assistant_event = event.get("assistantMessageEvent")
        if isinstance(assistant_event, dict):
            delta = str(assistant_event.get("delta") or "")
            if delta:
                current_delta_chunks.append(delta)

        if event_type == "turn_end":
            intermediate = True
            turn_ref = _omp_ref(event) or turn_ref
            completed_at = _omp_time(event) or completed_at
        elif event_type == "agent_end":
            assistant_message = _last_omp_assistant_message(event.get("messages"))
            if assistant_message is not None:
                latest_message_text, outcome_reason, outcome_error = (
                    _update_omp_assistant(
                        assistant_message,
                        latest_text=latest_message_text,
                        outcome_reason=outcome_reason,
                        outcome_error=outcome_error,
                    )
                )
                turn_ref = _omp_ref(assistant_message) or turn_ref
            if terminal_yield_status == "success":
                outcome_reason = "yield"
                latest_message_text = terminal_yield_text or latest_message_text
                outcome_error = ""
            elif terminal_yield_status == "aborted":
                outcome_reason = "error"
            completed_at = _omp_time(event) or completed_at
            if event.get("isTerminal") is True:
                finished = True
                finish_reason = "agent_end_terminal"
            else:
                finished = False
                finish_reason = ""
                intermediate = True
        elif event_type in {
            "auto_retry_start",
            "auto_retry_end",
            "auto_compaction_start",
            "auto_compaction_end",
            "retry_fallback_applied",
            "retry_fallback_succeeded",
            "queue_update",
            "extension_error",
        }:
            intermediate = True

    text = latest_message_text or "".join(current_delta_chunks)
    error = outcome_error if finished and outcome_reason == "error" else ""
    return NativeCliObservation(
        text=text,
        finished=finished,
        finish_reason=finish_reason,
        turn_ref=turn_ref,
        completed_at=completed_at,
        error=error,
        intermediate=intermediate,
        outcome_reason=outcome_reason,
        protocol_error=protocol_error,
    )


def _state_path(request: NativeCliExecutionRequest, key: str, *, fallback: str) -> Path:
    raw = str(request.session_data.get(key) or "").strip()
    if raw:
        return Path(raw).expanduser()
    state_dir = Path(
        str(
            request.session_data.get("omp_state_dir")
            or request.work_dir / ".ccb" / "omp"
        )
    ).expanduser()
    return state_dir / fallback


def _omp_agent_dir(request: NativeCliExecutionRequest) -> Path:
    raw_home = str(request.session_data.get("omp_home") or "").strip()
    if raw_home:
        home_dir = Path(raw_home).expanduser()
    else:
        raw_state = str(request.session_data.get("omp_state_dir") or "").strip()
        state_dir = (
            Path(raw_state).expanduser()
            if raw_state
            else request.work_dir / ".ccb" / "omp"
        )
        home_dir = state_dir / "home"
    return home_dir / ".omp" / "agent"


def _omp_message_role(message: dict[str, Any]) -> str:
    return (
        str(message.get("role") or message.get("sender") or message.get("author") or "")
        .strip()
        .lower()
    )


def _omp_message_text(message: dict[str, Any]) -> str:
    return _omp_text(message.get("content"))


def _last_omp_assistant_message(messages: object) -> dict[str, Any] | None:
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
        if isinstance(message, dict) and _omp_message_role(message) == "assistant":
            return message
    return None


def _update_omp_assistant(
    message: dict[str, Any],
    *,
    latest_text: str,
    outcome_reason: str,
    outcome_error: str,
) -> tuple[str, str, str]:
    message_text = _omp_message_text(message)
    stop_reason = _omp_stop_reason(message)
    error_message = _omp_error_message(message)
    return (
        message_text if message_text or stop_reason else latest_text,
        stop_reason or outcome_reason,
        error_message
        or ("" if stop_reason and stop_reason != "error" else outcome_error),
    )


def _omp_stop_reason(message: dict[str, Any]) -> str:
    for key in ("stopReason", "stop_reason", "finishReason", "finish_reason"):
        raw = message.get(key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        normalized = raw.strip().lower().replace("-", "_")
        if normalized == "tooluse":
            return "tool_use"
        return normalized
    return ""


def _omp_error_message(message: dict[str, Any]) -> str:
    for key in ("errorMessage", "error_message", "error"):
        raw = message.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return ""


def _omp_terminal_yield_result(event: dict[str, Any]) -> tuple[str, str, str] | None:
    if bool(event.get("isError")):
        return None
    result = event.get("result")
    details = result.get("details") if isinstance(result, dict) else None
    if isinstance(details, dict):
        status = str(details.get("status") or "").strip().lower()
        yield_type = details.get("type")
        if (
            status == "success"
            and isinstance(yield_type, list)
            and yield_type
            and all(isinstance(item, str) for item in yield_type)
        ):
            return None
        if status == "aborted":
            return "aborted", "", str(details.get("error") or "yield_aborted")
        if status == "success":
            return "success", _omp_value_text(details.get("data")), ""
    return "success", "", ""


def _omp_value_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if value is None:
        return ""
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def _omp_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_omp_text(item) for item in value)
    if not isinstance(value, dict):
        return ""
    for key in ("text", "delta", "content", "message", "payload", "data", "part"):
        nested = value.get(key)
        text = _omp_text(nested)
        if text:
            return text
    return ""


def _omp_ref(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in ("id", "message_id", "session_id", "turn_id", "request_id"):
        raw = value.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    for key in ("message", "payload", "data"):
        ref = _omp_ref(value.get(key))
        if ref:
            return ref
    return None


def _omp_time(value: Any) -> object | None:
    if not isinstance(value, dict):
        return None
    for key in ("completed_at", "timestamp", "time", "created_at", "updated_at"):
        raw = value.get(key)
        if raw:
            return raw
    for key in ("message", "payload", "data"):
        found = _omp_time(value.get(key))
        if found:
            return found
    return None


__all__ = [
    "OMP_EXECUTION_MODE_ENV",
    "OMP_HEADLESS_MODE",
    "OmpExecutionAdapter",
    "build_execution_adapter",
    "build_headless_execution_adapter",
    "observe_omp_json_output",
]
