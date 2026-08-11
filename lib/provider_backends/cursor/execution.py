from __future__ import annotations

import os
from pathlib import Path

from provider_backends.native_cli_support import (
    NativeCliExecutionConfig,
    NativeCliExecutionRequest,
    NativeCliSubprocessAdapter,
)
from provider_core.runtime_shared import provider_start_parts


def build_execution_adapter():
    mode = str(os.environ.get("CCB_CURSOR_EXECUTION_MODE") or "").strip().lower()
    if mode in ("", "pane"):
        from .pane_execution import CursorPaneExecutionAdapter

        return CursorPaneExecutionAdapter()
    if mode == "headless":
        return build_headless_execution_adapter()
    raise ValueError(f"unsupported CCB_CURSOR_EXECUTION_MODE: {mode}")


def build_headless_execution_adapter() -> NativeCliSubprocessAdapter:
    return NativeCliSubprocessAdapter(
        NativeCliExecutionConfig(
            provider="cursor",
            session_filename=".cursor-session",
            command_builder=_build_command,
            env_builder=_build_env,
            private_raw_env_names=("AGENT_CLI_CREDENTIAL_STORE",),
            output_kind="jsonl",
            mode="cursor_run",
            start_failed_reason="cursor_run_start_failed",
            failed_reason="cursor_run_failed",
            empty_reason="cursor_empty_reply",
            run_error_reason="cursor_run_error",
            complete_reason="cursor_run_stop",
            process_exit_complete_reason="cursor_run_exit",
            timeout_reason="cursor_run_timeout",
        )
    )


def _build_command(request: NativeCliExecutionRequest) -> list[str]:
    return [
        *provider_start_parts("cursor"),
        "--print",
        "--output-format",
        "stream-json",
        "--workspace",
        str(request.work_dir),
        "--trust",
        request.prompt,
    ]


def _build_env(request: NativeCliExecutionRequest) -> dict[str, str]:
    cursor_home = _state_path(request, "cursor_home", fallback="home")
    cursor_home.mkdir(parents=True, exist_ok=True)
    return {
        "HOME": str(cursor_home),
        "AGENT_CLI_CREDENTIAL_STORE": "file",
    }


def _state_path(request: NativeCliExecutionRequest, key: str, *, fallback: str) -> Path:
    raw = str(request.session_data.get(key) or "").strip()
    if raw:
        return Path(raw).expanduser()
    state_dir = Path(str(request.session_data.get("cursor_state_dir") or request.work_dir / ".ccb" / "cursor")).expanduser()
    return state_dir / fallback


__all__ = ["build_execution_adapter", "build_headless_execution_adapter"]
