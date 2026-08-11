from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import math
import os
from pathlib import Path

from ccbd.api_models import JobRecord
from completion.models import (
    CompletionConfidence,
    CompletionCursor,
    CompletionDecision,
    CompletionItemKind,
    CompletionSourceKind,
    CompletionStatus,
)
from provider_backends.native_cli_support.prompt import clean_native_reply, wrap_native_prompt
from provider_core.protocol import request_anchor_for_job
from provider_execution.active import ensure_active_pane_alive, prepare_active_start
from provider_execution.base import ProviderPollResult, ProviderRuntimeContext, ProviderSubmission
from provider_execution.common import (
    build_item,
    error_submission,
    interrupt_and_clear_runtime_target,
    no_wrap_requested,
    send_prompt_to_runtime_target,
)
from terminal_runtime import get_backend_for_session

from .session import load_project_session
from .transcript import (
    capture_cursor_transcript_offsets,
    cursor_pane_turn_state,
    cursor_record_text,
    read_new_cursor_transcript_records,
)


_MODE = "cursor_pane"
_IDLE_CONFIRM_S = 2.0
_DEFAULT_READY_TIMEOUT_S = 300.0
_DEFAULT_RUN_TIMEOUT_S = 900.0


class CursorPaneExecutionAdapter:
    provider = "cursor"

    def restore_diagnostics(self) -> dict[str, object]:
        return {
            "resume_supported": False,
            "restore_mode": "resubmit_required",
            "restore_reason": "provider_resume_unsupported",
            "restore_detail": (
                "Cursor jobs are bound to the managed visible pane and transcript; "
                "interrupted in-flight jobs should be resubmitted"
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
            source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
            now=now,
            missing_session_reason="missing_cursor_session",
            load_session_fn=_load_session,
            backend_for_session_fn=get_backend_for_session,
        )
        if isinstance(prepared, ProviderSubmission):
            return prepared

        cursor_home = _cursor_home(prepared.session.data)
        if cursor_home is None:
            return error_submission(
                job,
                provider=self.provider,
                now=now,
                source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
                reason="runtime_unavailable",
                error="cursor_home_missing",
            )

        request_anchor = request_anchor_for_job(job.job_id)
        no_wrap = no_wrap_requested(getattr(job, "provider_options", None))
        prompt = _pane_prompt(job.request.body or "", request_anchor=request_anchor, no_wrap=no_wrap)
        session_started_mtime_ns = _session_started_mtime_ns(prepared.session)
        turn_state = cursor_pane_turn_state(
            cursor_home,
            session_started_mtime_ns=session_started_mtime_ns,
        )
        pane_status = _cursor_pane_status(prepared.backend, prepared.pane_id)
        pane_busy = pane_status != "idle"
        readiness_offsets = capture_cursor_transcript_offsets(cursor_home)
        existing_records, _ = read_new_cursor_transcript_records(cursor_home, {})
        excluded_anchor_paths = tuple(
            sorted(
                {
                    record_path
                    for record_path, record in existing_records
                    if str(record.get("role") or "").strip().lower() == "user"
                    and request_anchor in cursor_record_text(record)
                }
            )
        )
        offsets: dict[str, int] = {}
        prompt_sent = False
        if not turn_state.busy and not pane_busy:
            try:
                offsets = readiness_offsets
                send_prompt_to_runtime_target(prepared.backend, prepared.pane_id, prompt)
                prompt_sent = True
            except Exception as exc:
                return error_submission(
                    job,
                    provider=self.provider,
                    now=now,
                    source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
                    reason="cursor_pane_send_failed",
                    error=f"{type(exc).__name__}: {exc}",
                )

        reply_delivery = str(job.request.message_type or "").strip().lower() == "reply_delivery"
        return ProviderSubmission(
            job_id=job.job_id,
            agent_name=job.agent_name,
            provider=self.provider,
            accepted_at=now,
            ready_at=now,
            source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
            reply="",
            diagnostics={
                "provider": self.provider,
                "mode": _MODE,
                "workspace_path": str(prepared.work_dir),
                "pane_id": prepared.pane_id,
                "prompt_deferred_until_ready": not prompt_sent,
            },
            runtime_state={
                "mode": _MODE,
                "backend": prepared.backend,
                "pane_id": prepared.pane_id,
                "request_anchor": request_anchor,
                "cursor_home": str(cursor_home),
                "session_started_mtime_ns": session_started_mtime_ns,
                "transcript_offsets": offsets,
                "readiness_transcript_offsets": readiness_offsets,
                "matched_transcript_path": "",
                "excluded_anchor_paths": excluded_anchor_paths,
                "provider_session_id": "",
                "reply_buffer": "",
                "next_seq": 1,
                "anchor_seen": no_wrap,
                "no_wrap": no_wrap,
                "accepted_at": now,
                "started_at": now if prompt_sent else "",
                "idle_observed_at": "",
                "ready_timeout_s": _effective_ready_timeout_s(),
                "run_timeout_s": _effective_run_timeout_s(),
                "prompt_sent": prompt_sent,
                "pane_busy": pane_busy,
                "pane_status": pane_status,
                "deferred_requires_terminal": (
                    not prompt_sent and (turn_state.busy or pane_status == "active")
                ),
                "deferred_terminal_seen": False,
                "pending_prompt": prompt,
                "reply_delivery_complete_on_dispatch": reply_delivery,
            },
        )

    def poll(self, submission: ProviderSubmission, *, now: str) -> ProviderPollResult | None:
        if str(submission.runtime_state.get("mode") or "") != _MODE:
            return None
        state = dict(submission.runtime_state)
        backend = state.get("backend")
        pane_id = str(state.get("pane_id") or "")
        if backend is None or not pane_id:
            return _runtime_error(submission, state, now=now, reason="runtime_state_corrupt")
        pane_dead = ensure_active_pane_alive(submission, backend=backend, pane_id=pane_id, now=now)
        if pane_dead is not None:
            return pane_dead

        if not bool(state.get("prompt_sent")):
            cursor_home = Path(str(state.get("cursor_home") or ""))
            readiness_records, readiness_offsets = read_new_cursor_transcript_records(
                cursor_home,
                dict(state.get("readiness_transcript_offsets") or {}),
            )
            state["readiness_transcript_offsets"] = readiness_offsets
            if any(
                str(record.get("type") or "").strip().lower() == "turn_ended"
                for _, record in readiness_records
            ):
                state["deferred_terminal_seen"] = True
            turn_state = cursor_pane_turn_state(
                cursor_home,
                session_started_mtime_ns=max(0, int(state.get("session_started_mtime_ns") or 0)),
            )
            pane_status = _cursor_pane_status(backend, pane_id)
            pane_busy = pane_status != "idle"
            terminal_required = bool(state.get("deferred_requires_terminal"))
            terminal_seen = bool(state.get("deferred_terminal_seen"))
            accepted_at = str(state.get("accepted_at") or submission.accepted_at or "")
            ready_timeout_s = float(state.get("ready_timeout_s") or _DEFAULT_READY_TIMEOUT_S)
            ready_wait_s = _elapsed_seconds(accepted_at, now)
            state["ready_wait_s"] = ready_wait_s
            state["busy_transcript_path"] = turn_state.transcript_path
            state["pane_busy"] = pane_busy
            state["pane_status"] = pane_status
            state["waiting_for_deferred_terminal"] = terminal_required and not terminal_seen
            if turn_state.busy or pane_busy or (terminal_required and not terminal_seen):
                state["idle_observed_at"] = ""
                if ready_wait_s >= ready_timeout_s:
                    updated = replace(submission, runtime_state=state)
                    return ProviderPollResult(
                        submission=updated,
                        decision=_timeout_decision(
                            updated,
                            state,
                            now=now,
                            reason="cursor_input_not_ready",
                            timeout_s=ready_timeout_s,
                        ),
                    )
                return ProviderPollResult(
                    submission=replace(submission, runtime_state=state),
                    items=(),
                    decision=None,
                )

            idle_observed_at = str(state.get("idle_observed_at") or "")
            if not idle_observed_at:
                state["idle_observed_at"] = now
                return ProviderPollResult(
                    submission=replace(submission, runtime_state=state),
                    items=(),
                    decision=None,
                )
            if _elapsed_seconds(idle_observed_at, now) < _IDLE_CONFIRM_S:
                return ProviderPollResult(
                    submission=replace(submission, runtime_state=state),
                    items=(),
                    decision=None,
                )

            pending_prompt = str(state.get("pending_prompt") or "")
            if not pending_prompt:
                return _runtime_error(submission, state, now=now, reason="runtime_state_corrupt")
            try:
                state["transcript_offsets"] = capture_cursor_transcript_offsets(cursor_home)
                send_prompt_to_runtime_target(backend, pane_id, pending_prompt)
            except Exception:
                return _runtime_error(submission, state, now=now, reason="cursor_pane_send_failed")
            state["prompt_sent"] = True
            state["prompt_sent_at"] = now
            state["started_at"] = now
            state["prompt_deferred_until_ready"] = False
            updated = replace(submission, runtime_state=state)
            if bool(state.get("reply_delivery_complete_on_dispatch")):
                return _reply_delivery_result(updated, state, now=now)
            return ProviderPollResult(submission=updated, items=(), decision=None)
        if bool(state.get("reply_delivery_complete_on_dispatch")):
            return _reply_delivery_result(submission, state, now=now)

        cursor_home = Path(str(state.get("cursor_home") or ""))
        matched_path = str(state.get("matched_transcript_path") or "")
        scan_from_start = not matched_path
        records, offsets = read_new_cursor_transcript_records(
            cursor_home,
            {} if scan_from_start else dict(state.get("transcript_offsets") or {}),
        )
        if not scan_from_start:
            state["transcript_offsets"] = offsets
        items = []
        request_anchor = str(state.get("request_anchor") or submission.job_id)
        terminal_status = ""
        excluded_anchor_paths = {
            str(path) for path in (state.get("excluded_anchor_paths") or ())
        }

        for record_path, record in records:
            role = str(record.get("role") or "").strip().lower()
            if not matched_path:
                if record_path in excluded_anchor_paths:
                    continue
                if role != "user" or request_anchor not in cursor_record_text(record):
                    continue
                matched_path = record_path
                state["matched_transcript_path"] = matched_path
                state["transcript_offsets"] = offsets
                state["provider_session_id"] = Path(matched_path).parent.name
                if not bool(state.get("anchor_seen")):
                    items.append(
                        build_item(
                            submission,
                            kind=CompletionItemKind.ANCHOR_SEEN,
                            timestamp=now,
                            seq=_next_seq(state),
                            payload={
                                "turn_id": request_anchor,
                                "source": "cursor_visible_session_user_message",
                                "provider_session_id": str(state.get("provider_session_id") or ""),
                            },
                        )
                    )
                    state["anchor_seen"] = True
                continue
            if record_path != matched_path:
                continue
            if role == "assistant":
                text = cursor_record_text(record)
                if text:
                    state["reply_buffer"] = str(state.get("reply_buffer") or "") + text
                continue
            if str(record.get("type") or "").strip().lower() == "turn_ended":
                terminal_status = str(record.get("status") or "").strip().lower() or "unknown"
                break

        reply = clean_native_reply(
            str(state.get("reply_buffer") or ""),
            request_anchor,
        )
        if reply and reply != submission.reply:
            items.append(
                build_item(
                    submission,
                    kind=CompletionItemKind.ASSISTANT_FINAL,
                    timestamp=now,
                    seq=_next_seq(state),
                    payload={
                        "text": reply,
                        "reply": reply,
                        "final_answer": reply,
                        "turn_id": request_anchor,
                        "provider_turn_ref": str(state.get("provider_session_id") or ""),
                        "finish_reason": terminal_status,
                    },
                )
            )

        updated = replace(submission, reply=reply, runtime_state=state)
        if terminal_status:
            items.append(
                build_item(
                    updated,
                    kind=CompletionItemKind.TURN_BOUNDARY,
                    timestamp=now,
                    seq=_next_seq(state),
                    payload={
                        "turn_id": request_anchor,
                        "provider_turn_ref": str(state.get("provider_session_id") or ""),
                        "finish_reason": terminal_status,
                        "reason": (
                            "cursor_run_stop"
                            if terminal_status == "success"
                            else f"cursor_run_finished:{terminal_status}"
                        ),
                    },
                )
            )
            updated = replace(updated, runtime_state=state)
            return ProviderPollResult(
                submission=updated,
                items=tuple(items),
                decision=_terminal_decision(
                    updated,
                    state,
                    reply=reply,
                    terminal_status=terminal_status,
                    now=now,
                ),
            )

        run_timeout_s = float(state.get("run_timeout_s") or _DEFAULT_RUN_TIMEOUT_S)
        if _timeout_elapsed(str(state.get("started_at") or ""), now, run_timeout_s):
            return ProviderPollResult(
                submission=updated,
                items=tuple(items),
                decision=_timeout_decision(
                    updated,
                    state,
                    now=now,
                    reason="cursor_run_timeout",
                    timeout_s=run_timeout_s,
                ),
            )

        if items or updated != submission:
            return ProviderPollResult(submission=updated, items=tuple(items), decision=None)
        return None

    def cancel(self, submission: ProviderSubmission) -> None:
        if not bool(submission.runtime_state.get("prompt_sent")):
            return
        backend = submission.runtime_state.get("backend")
        pane_id = str(submission.runtime_state.get("pane_id") or "")
        if backend is not None and pane_id:
            interrupt_and_clear_runtime_target(backend, pane_id)


def _load_session(work_dir: Path, *, agent_name: str):
    return load_project_session(work_dir, instance=agent_name)


def _cursor_home(session_data: dict) -> Path | None:
    raw = str(session_data.get("cursor_home") or "").strip()
    return Path(raw).expanduser() if raw else None


def _session_started_mtime_ns(session: object) -> int:
    session_file = getattr(session, "session_file", None)
    if session_file is None:
        return 0
    try:
        return Path(session_file).stat().st_mtime_ns
    except OSError:
        return 0


def _pane_prompt(body: str, *, request_anchor: str, no_wrap: bool) -> str:
    if no_wrap:
        return body
    return wrap_native_prompt(body, request_anchor)


def _cursor_pane_status(backend: object, pane_id: str) -> str:
    get_content = getattr(backend, "get_pane_content", None)
    if not callable(get_content):
        return "idle"
    try:
        content = str(get_content(pane_id, lines=120) or "")
    except Exception:
        return "idle"
    normalized = "\n".join(content.splitlines()[-12:]).lower()
    if any(
        marker in normalized
        for marker in ("ctrl+c to stop", " working", "┌─ follow-ups")
    ):
        return "active"
    if any(
        marker in normalized
        for marker in (
            "do you trust the contents of this directory?",
            "trusting workspace...",
        )
    ):
        return "blocked"
    return "idle"


def _cursor_pane_busy(backend: object, pane_id: str) -> bool:
    return _cursor_pane_status(backend, pane_id) != "idle"


def _reply_delivery_result(
    submission: ProviderSubmission,
    state: dict[str, object],
    *,
    now: str,
) -> ProviderPollResult:
    decision = CompletionDecision(
        terminal=True,
        status=CompletionStatus.COMPLETED,
        reason="reply_delivery_sent",
        confidence=CompletionConfidence.OBSERVED,
        reply="",
        anchor_seen=True,
        reply_started=False,
        reply_stable=True,
        provider_turn_ref=str(state.get("pane_id") or submission.job_id),
        source_cursor=None,
        finished_at=now,
        diagnostics={
            "reply_delivery": True,
            "delivery_status": "sent",
            "submission_mode": _MODE,
            "provider": submission.provider,
        },
    )
    return ProviderPollResult(submission=submission, decision=decision)


def _terminal_decision(
    submission: ProviderSubmission,
    state: dict[str, object],
    *,
    reply: str,
    terminal_status: str,
    now: str,
) -> CompletionDecision:
    if terminal_status == "success" and reply:
        status = CompletionStatus.COMPLETED
        reason = "cursor_run_stop"
        confidence = CompletionConfidence.OBSERVED
    elif terminal_status == "success":
        status = CompletionStatus.INCOMPLETE
        reason = "cursor_empty_reply"
        confidence = CompletionConfidence.DEGRADED
    else:
        status = CompletionStatus.INCOMPLETE
        reason = f"cursor_run_finished:{terminal_status or 'unknown'}"
        confidence = CompletionConfidence.DEGRADED
    return CompletionDecision(
        terminal=True,
        status=status,
        reason=reason,
        confidence=confidence,
        reply=reply,
        anchor_seen=bool(state.get("anchor_seen")),
        reply_started=bool(reply),
        reply_stable=True,
        provider_turn_ref=str(state.get("provider_session_id") or submission.job_id),
        source_cursor=CompletionCursor(
            source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
            event_seq=max(0, int(state.get("next_seq") or 1) - 1),
            updated_at=now,
        ),
        finished_at=now,
        diagnostics={
            "mode": _MODE,
            "finish_reason": terminal_status,
            "transcript_path": str(state.get("matched_transcript_path") or ""),
            "provider_session_id": str(state.get("provider_session_id") or ""),
        },
    )


def _runtime_error(
    submission: ProviderSubmission,
    state: dict[str, object],
    *,
    now: str,
    reason: str,
) -> ProviderPollResult:
    return ProviderPollResult(
        submission=replace(submission, runtime_state=state),
        decision=CompletionDecision(
            terminal=True,
            status=CompletionStatus.INCOMPLETE,
            reason=reason,
            confidence=CompletionConfidence.DEGRADED,
            reply=submission.reply,
            anchor_seen=bool(state.get("anchor_seen")),
            reply_started=bool(submission.reply),
            reply_stable=False,
            provider_turn_ref=submission.job_id,
            source_cursor=None,
            finished_at=now,
            diagnostics={"mode": _MODE},
        ),
    )


def _timeout_decision(
    submission: ProviderSubmission,
    state: dict[str, object],
    *,
    now: str,
    reason: str,
    timeout_s: float,
) -> CompletionDecision:
    return CompletionDecision(
        terminal=True,
        status=CompletionStatus.INCOMPLETE,
        reason=reason,
        confidence=CompletionConfidence.DEGRADED,
        reply=submission.reply,
        anchor_seen=bool(state.get("anchor_seen")),
        reply_started=bool(submission.reply),
        reply_stable=False,
        provider_turn_ref=str(state.get("provider_session_id") or submission.job_id),
        source_cursor=None,
        finished_at=now,
        diagnostics={
            "mode": _MODE,
            "timeout_s": timeout_s,
            "prompt_sent": bool(state.get("prompt_sent")),
            "transcript_path": str(state.get("matched_transcript_path") or ""),
        },
    )


def _effective_ready_timeout_s() -> float:
    return _positive_finite_env("CCB_CURSOR_READY_TIMEOUT_S", _DEFAULT_READY_TIMEOUT_S)


def _effective_run_timeout_s() -> float:
    return _positive_finite_env("CCB_CURSOR_RUN_TIMEOUT_S", _DEFAULT_RUN_TIMEOUT_S)


def _positive_finite_env(name: str, default: float) -> float:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if value > 0.0 and math.isfinite(value) else default


def _elapsed_seconds(started_at: str, now: str) -> float:
    if not started_at or not now:
        return 0.0
    try:
        started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
        current = datetime.fromisoformat(now.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    return max(0.0, (current - started).total_seconds())


def _timeout_elapsed(started_at: str, now: str, timeout_s: float) -> bool:
    return timeout_s > 0.0 and _elapsed_seconds(started_at, now) >= timeout_s


def _next_seq(state: dict[str, object]) -> int:
    seq = max(1, int(state.get("next_seq") or 1))
    state["next_seq"] = seq + 1
    return seq


__all__ = ["CursorPaneExecutionAdapter"]
