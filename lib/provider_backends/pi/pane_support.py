from __future__ import annotations

import os
from dataclasses import replace
from datetime import datetime
from pathlib import Path

from ccbd.api_models import JobRecord
from completion.models import (
    CompletionConfidence,
    CompletionCursor,
    CompletionDecision,
    CompletionStatus,
)
from provider_execution.base import ProviderPollResult, ProviderRuntimeContext, ProviderSubmission
from provider_execution.common import send_prompt_to_runtime_target


PI_NATIVE_TURN_TIMEOUT_ENV = "CCB_PI_NATIVE_TURN_TIMEOUT_S"
PANE_LINES_DEFAULT = 2000
MAX_WAIT_SECS = 300.0


def _pane_terminal(
    submission: ProviderSubmission,
    state: dict[str, object],
    now: str,
    *,
    status: CompletionStatus,
    reason: str,
    reply: str,
    confidence: CompletionConfidence,
    diagnostics_extra: dict[str, object] | None = None,
) -> ProviderPollResult:
    cleaned_reply = reply or ""
    progress = replace(
        submission,
        runtime_state=state,
        status=status,
        reason=reason,
        reply=cleaned_reply,
        confidence=confidence,
    )
    cursor = CompletionCursor(
        source_kind=submission.source_kind,
        event_seq=_state_int(state, "next_seq", 1),
        updated_at=now,
    )
    diagnostics: dict[str, object] = {
        "mode": "pane_native_log",
        "total_secs": float(state.get("total_secs") or state.get("ready_wait_secs") or 0.0),
        "anchor_seen": bool(state.get("anchor_seen")),
        "reply_chars": len(cleaned_reply),
    }
    diagnostics.update(diagnostics_extra or {})
    if reason == "pi_native_turn_timeout" and not cleaned_reply:
        diagnostics.update(
            {
                "no_captured_reply": True,
                "provider_no_reply": True,
                "receipt_valid": False,
                "receipt_class": "no_captured_reply",
                "error_type": "empty_provider_reply",
                "diagnosis": (
                    "pi pane turn polling timed out after observing the submitted CCB_REQ_ID, "
                    "but no assistant reply text was captured."
                ),
            }
        )
    decision = CompletionDecision(
        terminal=True,
        status=status,
        reason=reason,
        confidence=confidence,
        reply=cleaned_reply,
        anchor_seen=bool(state.get("anchor_seen")),
        reply_started=bool(cleaned_reply),
        reply_stable=bool(cleaned_reply) and status is CompletionStatus.COMPLETED,
        provider_turn_ref=_state_str(state, "req_id") or submission.job_id,
        source_cursor=cursor,
        finished_at=now,
        diagnostics=diagnostics,
    )
    return ProviderPollResult(submission=progress, items=(), decision=decision)


def _pane_session_dir(session: object) -> Path | None:
    data = getattr(session, "data", None)
    if not isinstance(data, dict):
        return None
    raw = str(data.get("pi_session_dir") or "").strip()
    if raw:
        return Path(raw).expanduser()
    state_dir = str(data.get("pi_state_dir") or "").strip()
    if state_dir:
        return Path(state_dir).expanduser() / "sessions"
    return None


def _pane_snapshot(backend: object, pane_id: str) -> str:
    getter = getattr(backend, "get_pane_content", None)
    if not callable(getter):
        getter = getattr(backend, "get_text", None)
    if not callable(getter):
        return ""
    try:
        return str(getter(pane_id, lines=PANE_LINES_DEFAULT) or "")
    except Exception:
        return ""


def _pane_ready_for_input(content: str) -> bool:
    """True once pi's TUI chrome is rendered, meaning the input box accepts a paste."""
    text = content or ""
    if "─" * 24 not in text:
        return False
    return "LSP" in text or "(auto)" in text


def _send_prompt(backend: object, pane_id: str, prompt: str) -> str | None:
    try:
        send_prompt_to_runtime_target(backend, pane_id, prompt)
    except Exception as exc:
        return f"send_text_failed:{exc!r}"
    return None


def _resolve_work_dir(job: JobRecord, context: ProviderRuntimeContext | None) -> Path | None:
    candidate = (context.workspace_path if context else None) or job.workspace_path
    if not candidate:
        return None
    try:
        return Path(candidate).expanduser()
    except Exception:
        return None


def _native_turn_timeout_secs() -> float:
    raw = os.environ.get(PI_NATIVE_TURN_TIMEOUT_ENV)
    if raw is None or not raw.strip():
        return MAX_WAIT_SECS
    try:
        value = float(raw)
    except ValueError:
        return MAX_WAIT_SECS
    return value if value > 0 else MAX_WAIT_SECS


def _parse_now(now: str) -> datetime | None:
    if not now:
        return None
    try:
        return datetime.fromisoformat(now.replace("Z", "+00:00"))
    except Exception:
        return None


def _seconds_between(start: str, end: str) -> float:
    start_dt = _parse_now(start)
    end_dt = _parse_now(end)
    if start_dt is None or end_dt is None:
        return 0.0
    return max(0.0, (end_dt - start_dt).total_seconds())


def _state_int(state: dict[str, object], key: str, default: int) -> int:
    value = state.get(key)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _state_str(state: dict[str, object], key: str, default: str = "") -> str:
    value = state.get(key)
    if value is None:
        return default
    return str(value)


__all__ = [
    "MAX_WAIT_SECS",
    "PANE_LINES_DEFAULT",
    "PI_NATIVE_TURN_TIMEOUT_ENV",
]
