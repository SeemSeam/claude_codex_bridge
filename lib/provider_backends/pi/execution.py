from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from provider_core.runtime_shared import provider_start_parts

from provider_backends.native_cli_support import (
    NativeCliExecutionConfig,
    NativeCliExecutionRequest,
    NativeCliObservation,
    NativeCliSubprocessAdapter,
)


PI_EXECUTION_MODE_ENV = "CCB_PI_EXECUTION_MODE"


def build_execution_adapter():
    """Pane-driven by default; set CCB_PI_EXECUTION_MODE=headless to roll back."""
    if (os.environ.get(PI_EXECUTION_MODE_ENV) or "").strip().lower() == "headless":
        return build_headless_execution_adapter()
    from .pane_execution import PiProviderAdapter

    return PiProviderAdapter()


def build_headless_execution_adapter() -> NativeCliSubprocessAdapter:
    return NativeCliSubprocessAdapter(
        NativeCliExecutionConfig(
            provider="pi",
            session_filename=".pi-session",
            command_builder=_build_command,
            env_builder=_build_env,
            private_path_env_names=(
                "PI_CODING_AGENT_DIR",
                "PI_CODING_AGENT_SESSION_DIR",
            ),
            private_raw_env_names=("PI_SKIP_VERSION_CHECK", "PI_TELEMETRY"),
            observer=observe_pi_json_output,
            output_kind="jsonl",
            mode="pi_run",
            start_failed_reason="pi_run_start_failed",
            failed_reason="pi_run_failed",
            empty_reason="pi_empty_reply",
            run_error_reason="pi_run_error",
            complete_reason="pi_run_stop",
            process_exit_complete_reason="pi_run_exit",
            missing_terminal_reason="pi_native_terminal_missing",
            timeout_reason="pi_run_timeout",
            invalid_protocol_reason="pi_native_protocol_invalid",
            missing_outcome_reason="pi_native_outcome_missing",
            terminal_on_process_exit=False,
            terminal_requires_process_exit=True,
            require_outcome_reason=True,
        )
    )


def _build_command(request: NativeCliExecutionRequest) -> list[str]:
    session_dir = _state_path(request, "pi_session_dir", fallback="sessions")
    session_dir.mkdir(parents=True, exist_ok=True)
    return [
        *provider_start_parts("pi"),
        "--mode",
        "json",
        "--session-dir",
        str(session_dir),
        "--no-approve",
        "--name",
        request.job.job_id,
        request.prompt,
    ]


def _build_env(request: NativeCliExecutionRequest) -> dict[str, str]:
    pi_home = _state_path(request, "pi_home", fallback="home")
    session_dir = _state_path(request, "pi_session_dir", fallback="sessions")
    pi_home.mkdir(parents=True, exist_ok=True)
    session_dir.mkdir(parents=True, exist_ok=True)
    return {
        "PI_CODING_AGENT_DIR": str(pi_home),
        "PI_CODING_AGENT_SESSION_DIR": str(session_dir),
        "PI_SKIP_VERSION_CHECK": "1",
        "PI_TELEMETRY": "0",
    }


def observe_pi_json_output(path: Path) -> NativeCliObservation:
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
            turn_ref = None
            completed_at = None
            continue
        if "tool" in event_type:
            intermediate = True
            continue
        message = event.get("message")
        if isinstance(message, dict) and _pi_message_role(message) == "assistant":
            latest_message_text, outcome_reason, outcome_error = _update_pi_assistant(
                message,
                latest_text=latest_message_text,
                outcome_reason=outcome_reason,
                outcome_error=outcome_error,
            )
            turn_ref = _pi_ref(message) or turn_ref
            completed_at = _pi_time(event) or completed_at
            if event_type in {"message_start", "message_end"}:
                current_delta_chunks = []
        assistant_event = event.get("assistantMessageEvent")
        if isinstance(assistant_event, dict):
            delta = str(assistant_event.get("delta") or "")
            if delta:
                current_delta_chunks.append(delta)
        if event_type == "turn_end":
            intermediate = True
            turn_ref = _pi_ref(event) or turn_ref
            completed_at = _pi_time(event) or completed_at
        elif event_type == "agent_end":
            intermediate = True
            assistant_message = _last_assistant_message(event.get("messages"))
            if assistant_message is not None:
                latest_message_text, outcome_reason, outcome_error = (
                    _update_pi_assistant(
                        assistant_message,
                        latest_text=latest_message_text,
                        outcome_reason=outcome_reason,
                        outcome_error=outcome_error,
                    )
                )
                turn_ref = _pi_ref(assistant_message) or turn_ref
            completed_at = _pi_time(event) or completed_at
        elif event_type == "agent_settled":
            finished = True
            finish_reason = "agent_settled"
            completed_at = _pi_time(event) or completed_at
        elif event_type in {
            "auto_retry_start",
            "auto_retry_end",
            "compaction_start",
            "compaction_end",
            "summarization_retry_scheduled",
            "summarization_retry_attempt_start",
            "summarization_retry_finished",
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
            request.session_data.get("pi_state_dir") or request.work_dir / ".ccb" / "pi"
        )
    ).expanduser()
    return state_dir / fallback


def _pi_message_role(message: dict[str, Any]) -> str:
    return (
        str(message.get("role") or message.get("sender") or message.get("author") or "")
        .strip()
        .lower()
    )


def _pi_message_text(message: dict[str, Any]) -> str:
    return _pi_text(message.get("content"))


def _last_assistant_message(messages: object) -> dict[str, Any] | None:
    if not isinstance(messages, list):
        return None
    for message in reversed(messages):
        if isinstance(message, dict) and _pi_message_role(message) == "assistant":
            return message
    return None


def _update_pi_assistant(
    message: dict[str, Any],
    *,
    latest_text: str,
    outcome_reason: str,
    outcome_error: str,
) -> tuple[str, str, str]:
    message_text = _pi_message_text(message)
    stop_reason = _pi_stop_reason(message)
    error_message = _pi_error_message(message)
    return (
        message_text if message_text or stop_reason else latest_text,
        stop_reason or outcome_reason,
        error_message
        or ("" if stop_reason and stop_reason != "error" else outcome_error),
    )


def _pi_stop_reason(message: dict[str, Any]) -> str:
    for key in ("stopReason", "stop_reason", "finishReason", "finish_reason"):
        raw = message.get(key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        normalized = raw.strip().lower().replace("-", "_")
        if normalized == "tooluse":
            return "tool_use"
        return normalized
    return ""


def _pi_error_message(message: dict[str, Any]) -> str:
    for key in ("errorMessage", "error_message", "error"):
        raw = message.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    return ""


def _pi_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_pi_text(item) for item in value)
    if not isinstance(value, dict):
        return ""
    for key in ("text", "delta", "content", "message", "payload", "data", "part"):
        nested = value.get(key)
        text = _pi_text(nested)
        if text:
            return text
    return ""


def _pi_ref(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    for key in ("id", "message_id", "session_id", "turn_id", "request_id"):
        raw = value.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw.strip()
    for key in ("message", "payload", "data"):
        ref = _pi_ref(value.get(key))
        if ref:
            return ref
    return None


def _pi_time(value: Any) -> object | None:
    if not isinstance(value, dict):
        return None
    for key in ("completed_at", "timestamp", "time", "created_at", "updated_at"):
        raw = value.get(key)
        if raw:
            return raw
    for key in ("message", "payload", "data"):
        found = _pi_time(value.get(key))
        if found:
            return found
    return None


__all__ = [
    "build_execution_adapter",
    "build_headless_execution_adapter",
    "observe_pi_json_output",
]
