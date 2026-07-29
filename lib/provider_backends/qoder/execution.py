from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ccbd.api_models import JobRecord
from completion.models import CompletionSourceKind
from provider_core.protocol import request_anchor_for_job
from provider_core.runtime_shared import provider_start_parts
from provider_execution.active import PreparedActiveStart, prepare_active_start
from provider_execution.base import (
    ProviderPollResult,
    ProviderRuntimeContext,
    ProviderSubmission,
)
from provider_execution.common import (
    interrupt_and_clear_runtime_target,
    send_prompt_to_runtime_target,
)
from terminal_runtime import get_backend_for_session

from provider_backends.native_cli_support import (
    NativeCliExecutionConfig,
    NativeCliExecutionRequest,
    NativeCliObservation,
    NativeCliSubprocessAdapter,
)
from provider_backends.pane_quiet_support import (
    PaneSnapshotReader,
    wrap_pane_quiet_prompt,
)
from provider_backends.pane_quiet_support import (
    poll_submission as poll_pane_submission,
)

from .session import load_project_session

_NORMAL_STOP_REASONS = {"completed", "end_turn", "stop", "stop_sequence", "success"}
_PERMISSION_OPTIONS = {"--dangerously-skip-permissions", "--permission-mode", "--yolo"}


class QoderPaneExecutionAdapter:
    restart_resume_supported = False

    def __init__(
        self,
        *,
        provider: str,
        load_project_session_fn: Callable,
        backend_for_session_fn: Callable[[dict], object | None] = get_backend_for_session,
    ) -> None:
        self.provider = str(provider or "").strip().lower()
        self._load_project_session = load_project_session_fn
        self._backend_for_session = backend_for_session_fn

    def restore_diagnostics(self) -> dict[str, object]:
        return {
            "resume_supported": False,
            "restore_mode": "resubmit_required",
            "restore_reason": "provider_resume_unsupported",
            "restore_detail": (
                f"{self.provider} asks run in the managed visible pane; interrupted "
                "in-flight jobs should be resubmitted after daemon restart"
            ),
        }

    def start(
        self,
        job: JobRecord,
        *,
        context: ProviderRuntimeContext | None,
        now: str,
    ) -> ProviderSubmission:
        prepared = prepare_active_start(
            job,
            context=context,
            provider=self.provider,
            source_kind=CompletionSourceKind.TERMINAL_TEXT,
            now=now,
            missing_session_reason=f"missing_{self.provider}_session",
            load_session_fn=self._load_session,
            backend_for_session_fn=self._backend_for_session,
        )
        if not isinstance(prepared, PreparedActiveStart):
            return prepared

        request_anchor = request_anchor_for_job(job.job_id)
        prompt = wrap_pane_quiet_prompt(job.request.body or "", request_anchor)
        reader = PaneSnapshotReader(
            backend=prepared.backend,
            pane_id=prepared.pane_id,
            lines=2000,
        )
        try:
            send_prompt_to_runtime_target(prepared.backend, prepared.pane_id, prompt)
        except Exception as exc:  # noqa: BLE001 - terminal backends expose provider-specific failures
            send_error = f"send_text_failed:{exc!r}"
        else:
            send_error = None

        return ProviderSubmission(
            job_id=job.job_id,
            agent_name=job.agent_name,
            provider=self.provider,
            accepted_at=now,
            ready_at=now,
            source_kind=CompletionSourceKind.TERMINAL_TEXT,
            reply="",
            diagnostics={
                "provider": self.provider,
                "mode": "visible_pane",
                "workspace_path": str(prepared.work_dir),
                "pane_id": prepared.pane_id,
                **({"send_error": send_error} if send_error else {}),
            },
            runtime_state={
                "mode": "pane_quiet",
                "provider": self.provider,
                "reader": reader,
                "backend": prepared.backend,
                "pane_id": prepared.pane_id,
                "req_id": request_anchor,
                "request_anchor": request_anchor,
                "started_at": now,
                "last_change_at": now,
                "last_poll_at": now,
                "last_hash": None,
                "prompt_sent": send_error is None,
                "pending_prompt": prompt,
                "send_error": send_error,
                "snapshot_errors": 0,
                "next_seq": 1,
            },
        )

    def poll(self, submission: ProviderSubmission, *, now: str) -> ProviderPollResult | None:
        return poll_pane_submission(submission, now=now)

    def cancel(self, submission: ProviderSubmission) -> None:
        backend = submission.runtime_state.get("backend")
        pane_id = str(submission.runtime_state.get("pane_id") or "").strip()
        if backend is not None and pane_id:
            interrupt_and_clear_runtime_target(backend, pane_id)

    def _load_session(self, work_dir: Path, *, agent_name: str):
        instance = str(agent_name or "").strip().lower()
        if not instance:
            return None
        return self._load_project_session(work_dir, instance=instance)


def build_qoder_pane_execution_adapter(
    *,
    provider: str,
    load_project_session_fn: Callable,
    backend_for_session_fn: Callable[[dict], object | None] = get_backend_for_session,
) -> QoderPaneExecutionAdapter:
    return QoderPaneExecutionAdapter(
        provider=provider,
        load_project_session_fn=load_project_session_fn,
        backend_for_session_fn=backend_for_session_fn,
    )


def build_execution_adapter() -> QoderPaneExecutionAdapter:
    return build_qoder_pane_execution_adapter(
        provider="qoder",
        load_project_session_fn=load_project_session,
    )


def build_headless_execution_adapter() -> NativeCliSubprocessAdapter:
    return NativeCliSubprocessAdapter(
        NativeCliExecutionConfig(
            provider="qoder",
            session_filename=".qoder-session",
            command_builder=_build_command,
            observer=observe_qoder_output,
            output_kind="jsonl",
            mode="qoder_run",
            start_failed_reason="qoder_run_start_failed",
            failed_reason="qoder_run_failed",
            empty_reason="qoder_empty_reply",
            run_error_reason="qoder_run_error",
            complete_reason="qoder_run_stop",
            process_exit_complete_reason="qoder_run_exit",
            missing_terminal_reason="qoder_native_terminal_missing",
            timeout_reason="qoder_run_timeout",
            terminal_on_process_exit=False,
        )
    )


def _build_command(request: NativeCliExecutionRequest) -> list[str]:
    return _build_qoder_command(request, provider="qoder")


def _build_qoder_command(
    request: NativeCliExecutionRequest,
    *,
    provider: str,
) -> list[str]:
    base = provider_start_parts(provider)
    command = [*base]
    if not _has_option(base, "--config-dir"):
        command.extend(["--config-dir", str(_qoder_config_dir(request, provider=provider))])
    if not any(_has_option(base, option) for option in _PERMISSION_OPTIONS):
        permission_mode = str(
            request.session_data.get(f"{provider}_headless_permission_mode") or "dont_ask"
        ).strip()
        if permission_mode not in {
            "accept_edits",
            "auto",
            "bypass_permissions",
            "default",
            "dont_ask",
            "plan",
        }:
            permission_mode = "dont_ask"
        command.extend(["--permission-mode", permission_mode])
    command.extend(
        [
            "-w",
            str(request.work_dir),
            "-p",
            "--output-format",
            "stream-json",
            "--session-id",
            _qoder_session_id_for_job(request.job.job_id, provider=provider),
            request.prompt,
        ]
    )
    return command


def _qoder_config_dir(
    request: NativeCliExecutionRequest,
    *,
    provider: str = "qoder",
) -> Path:
    raw = str(
        request.session_data.get(f"{provider}_config_dir")
        or request.session_data.get(f"{provider}_home")
        or ""
    ).strip()
    if raw:
        path = Path(raw).expanduser()
    else:
        state_dir = Path(
            str(
                request.session_data.get(f"{provider}_state_dir")
                or request.work_dir / ".ccb" / provider
            )
        ).expanduser()
        path = state_dir / "home"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _qoder_session_id_for_job(job_id: str, *, provider: str = "qoder") -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"ccb:{provider}:{job_id}"))


def observe_qoder_output(path: Path) -> NativeCliObservation:
    return _observe_qoder_output(
        path,
        result_error="qoder_result_error",
        assistant_error_terminal=True,
    )


def _observe_qoder_output(
    path: Path,
    *,
    result_error: str,
    assistant_error_terminal: bool = False,
    require_explicit_success: bool = False,
    require_stop_reason: bool = False,
) -> NativeCliObservation:
    if not path or not path.is_file():
        return NativeCliObservation()
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError as exc:
        return NativeCliObservation(error=f"read_stdout_failed:{exc}")

    assistant_text = ""
    result_text = ""
    finished = False
    finish_reason = ""
    turn_ref: str | None = None
    error = ""
    assistant_error = ""
    intermediate = False

    for line in lines:
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("type") or "").strip().lower()
        turn_ref = turn_ref or _text_value(event.get("session_id"))

        if event_type == "system":
            intermediate = True
            continue
        if event_type == "assistant":
            event_error = _text_value(event.get("error"))
            text = _message_text(event.get("message"))
            if event_error:
                assistant_error = text or event_error
                if assistant_error_terminal:
                    error = assistant_error
                elif text:
                    assistant_text = text
                continue
            if text:
                assistant_text = text
            continue
        if event_type != "result":
            continue

        finished = True
        native_reason = _text_value(event.get("stop_reason")) or _text_value(
            event.get("subtype")
        )
        is_error = event.get("is_error")
        if bool(is_error):
            error = (
                _text_value(event.get("result"))
                or assistant_error
                or native_reason
                or result_error
            )
            result_text = ""
            assistant_text = ""
            continue
        if require_explicit_success and is_error is not False:
            finish_reason = "missing_result_status"
            continue
        result_text = _text_value(event.get("result"))
        normalized_reason = native_reason.strip().lower().replace("-", "_")
        finish_reason = (
            "completed" if normalized_reason in _NORMAL_STOP_REASONS else normalized_reason
        )
        if not finish_reason and require_stop_reason:
            finish_reason = "missing_stop_reason"
        elif not finish_reason:
            finish_reason = "completed"

    return NativeCliObservation(
        text=result_text or assistant_text,
        finished=finished,
        finish_reason=finish_reason,
        turn_ref=turn_ref,
        error=error,
        intermediate=intermediate,
    )


def _message_text(value: object) -> str:
    if not isinstance(value, dict):
        return ""
    return _content_text(value.get("content"))


def _content_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_content_text(item) for item in value)
    if not isinstance(value, dict):
        return ""
    if str(value.get("type") or "").strip().lower() in {"text", "output_text"}:
        return _text_value(value.get("text"))
    for key in ("text", "content", "message"):
        text = _content_text(value.get(key))
        if text:
            return text
    return ""


def _text_value(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _has_option(parts: list[str], option: str) -> bool:
    return any(part == option or part.startswith(f"{option}=") for part in parts)


__all__ = [
    "QoderPaneExecutionAdapter",
    "build_execution_adapter",
    "build_headless_execution_adapter",
    "build_qoder_pane_execution_adapter",
    "observe_qoder_output",
]
