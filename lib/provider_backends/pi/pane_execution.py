from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from ccbd.api_models import JobRecord
from completion.models import CompletionConfidence, CompletionSourceKind, CompletionStatus
from provider_core.protocol import request_anchor_for_job
from provider_execution.base import ProviderPollResult, ProviderRuntimeContext, ProviderSubmission
from provider_execution.common import error_submission, send_prompt_to_runtime_target

from provider_backends.native_cli_support import wrap_native_prompt

from .pane_native_log import observe_pi_pane_turn
from .pane_support import (
    _native_turn_timeout_secs,
    _pane_ready_for_input,
    _pane_session_dir,
    _pane_snapshot,
    _pane_terminal,
    _resolve_work_dir,
    _seconds_between,
    _send_prompt,
    _state_str,
)
from .session import load_project_session


ANCHOR_WAIT_SECS = 120.0
READY_WAIT_SECS = 60.0


class PiProviderAdapter:
    """Drive the visible pi pane, read the reply from pi's own session log.

    The prompt is typed into the pane so the seat's work is visible the way
    claude and kimi seats are. Completion and reply text come from the JSONL
    session log pi writes under its ``--session-dir``, which carries typed
    content blocks and an explicit ``stopReason`` -- so pane text never has to
    be parsed.
    """

    provider = "pi"

    def restore_diagnostics(self) -> dict[str, object]:
        return {
            "resume_supported": False,
            "restore_mode": "resubmit_required",
            "restore_reason": "provider_resume_unsupported",
            "restore_detail": (
                "pi pane turns are observed through pi's own session JSONL; completed turns stay "
                "readable after restart, but an interrupted in-flight job must be resubmitted"
            ),
        }

    def start(
        self,
        job: JobRecord,
        *,
        context: ProviderRuntimeContext | None,
        now: str,
    ) -> ProviderSubmission:
        return _pane_start_submission(job, context=context, now=now, provider=self.provider)

    def poll(self, submission: ProviderSubmission, *, now: str) -> ProviderPollResult | None:
        return _pane_poll_submission(submission, now=now)

    def resume(
        self,
        job: JobRecord,
        submission: ProviderSubmission,
        *,
        context: ProviderRuntimeContext | None,
        persisted_state,
        now: str,
    ) -> ProviderSubmission | None:
        del job, submission, context, persisted_state, now
        return None


def _pane_start_submission(
    job: JobRecord,
    *,
    context: ProviderRuntimeContext | None,
    now: str,
    provider: str,
) -> ProviderSubmission:
    work_dir = _resolve_work_dir(job, context)
    if work_dir is None:
        return error_submission(
            job,
            provider=provider,
            now=now,
            source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
            reason="runtime_unavailable",
            error="work_dir_missing",
        )

    session = None
    load_error: str | None = None
    instance = (job.agent_name or "").strip().lower() or None
    try:
        if instance is not None:
            session = load_project_session(work_dir, instance=instance)
        if session is None:
            session = load_project_session(work_dir)
    except Exception as exc:
        load_error = f"load_session_failed:{exc!r}"

    if session is None:
        return error_submission(
            job,
            provider=provider,
            now=now,
            source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
            reason="runtime_unavailable",
            error=load_error or "pi_session_file_missing",
        )

    pane_id = str(getattr(session, "pane_id", "") or "").strip()
    if not pane_id:
        return error_submission(
            job,
            provider=provider,
            now=now,
            source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
            reason="pane_unavailable",
            error="pane_id_missing_in_session",
        )

    try:
        backend = session.backend()
    except Exception as exc:
        backend = None
        backend_error = f"backend_resolve_failed:{exc!r}"
    else:
        backend_error = None

    if backend is None:
        return error_submission(
            job,
            provider=provider,
            now=now,
            source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
            reason="backend_unavailable",
            error=backend_error or "terminal_backend_unavailable",
        )

    session_dir = _pane_session_dir(session)
    if session_dir is None:
        return error_submission(
            job,
            provider=provider,
            now=now,
            source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
            reason="runtime_unavailable",
            error="pi_session_dir_missing",
        )

    req_id = request_anchor_for_job(job.job_id)
    prompt = wrap_native_prompt(job.request.body or "", req_id)

    initial_content = _pane_snapshot(backend, pane_id)
    prompt_deferred_until_ready = not _pane_ready_for_input(initial_content)
    send_error: str | None = None
    prompt_sent = False
    if not prompt_deferred_until_ready:
        send_error = _send_prompt(backend, pane_id, prompt)
        prompt_sent = send_error is None

    diagnostics: dict[str, object] = {
        "provider": provider,
        "mode": "pane_native_log",
        "pane_id": pane_id,
        "req_id": req_id,
        "task_id": job.request.task_id,
        "workspace_path": str(work_dir),
        "session_dir": str(session_dir),
    }
    if send_error:
        diagnostics["send_error"] = send_error
    if prompt_deferred_until_ready:
        diagnostics["prompt_deferred_until_ready"] = True

    return ProviderSubmission(
        job_id=job.job_id,
        agent_name=job.agent_name,
        provider=provider,
        accepted_at=now,
        ready_at=now,
        source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
        reply="",
        diagnostics=diagnostics,
        runtime_state={
            "mode": "pane_native_log",
            "provider": provider,
            "backend": backend,
            "pane_id": pane_id,
            "req_id": req_id,
            "session_dir": str(session_dir),
            "work_dir": str(work_dir),
            "started_at": now,
            "last_poll_at": now,
            "prompt_sent": prompt_sent,
            "pending_prompt": prompt,
            "prompt_deferred_until_ready": prompt_deferred_until_ready,
            "send_error": send_error,
            "next_seq": 1,
            "reply_buffer": "",
        },
    )


def _pane_poll_submission(submission: ProviderSubmission, *, now: str) -> ProviderPollResult | None:
    state = dict(submission.runtime_state)

    send_error = state.get("send_error")
    if send_error:
        return _pane_terminal(
            submission,
            state,
            now,
            status=CompletionStatus.FAILED,
            reason=f"send_failed:{send_error}",
            reply="",
            confidence=CompletionConfidence.DEGRADED,
        )

    pane_id = _state_str(state, "pane_id")
    req_id = _state_str(state, "req_id")
    session_dir = _state_str(state, "session_dir")
    backend = state.get("backend")
    if not pane_id or not req_id or not session_dir:
        return _pane_terminal(
            submission,
            state,
            now,
            status=CompletionStatus.FAILED,
            reason="runtime_state_invalid",
            reply="",
            confidence=CompletionConfidence.DEGRADED,
        )
    if backend is None:
        return _pane_terminal(
            submission,
            state,
            now,
            status=CompletionStatus.FAILED,
            reason="runtime_handle_lost",
            reply="",
            confidence=CompletionConfidence.DEGRADED,
        )

    if not bool(state.get("prompt_sent")):
        return _pane_poll_deferred_prompt(submission, state, now=now, backend=backend, pane_id=pane_id)

    state["last_poll_at"] = now
    started_at = _state_str(state, "started_at") or submission.accepted_at or now
    total_secs = _seconds_between(started_at, now)
    max_wait_secs = _native_turn_timeout_secs()
    state["total_secs"] = total_secs
    state["max_wait_secs"] = max_wait_secs

    observation = observe_pi_pane_turn(Path(session_dir), req_id)
    if observation is None:
        if total_secs >= ANCHOR_WAIT_SECS:
            return _pane_terminal(
                submission,
                state,
                now,
                status=CompletionStatus.INCOMPLETE,
                reason="pi_native_anchor_missing",
                reply="",
                confidence=CompletionConfidence.DEGRADED,
                diagnostics_extra={
                    "anchor_seen": False,
                    "total_secs": total_secs,
                    "diagnosis": "pi session log did not record the submitted CCB_REQ_ID.",
                },
            )
        return None

    state["anchor_seen"] = True
    state["session_path"] = observation.session_path
    state["stop_reason"] = observation.stop_reason
    if observation.reply:
        state["reply_buffer"] = observation.reply

    if observation.completed:
        return _pane_terminal(
            submission,
            state,
            now,
            status=CompletionStatus.COMPLETED,
            reason="pi_pane_turn_end",
            reply=observation.reply,
            confidence=CompletionConfidence.OBSERVED,
            diagnostics_extra={
                "session_path": observation.session_path,
                "provider_session_id": observation.session_id,
                "provider_turn_ref": observation.provider_turn_ref,
                "native_completed_at": observation.native_completed_at,
            },
        )

    if total_secs >= max_wait_secs:
        return _pane_terminal(
            submission,
            state,
            now,
            status=CompletionStatus.FAILED,
            reason="pi_native_turn_timeout",
            reply=_state_str(state, "reply_buffer"),
            confidence=CompletionConfidence.DEGRADED,
            diagnostics_extra={"max_wait_secs": max_wait_secs, "stop_reason": observation.stop_reason},
        )

    updated = replace(submission, reply=_state_str(state, "reply_buffer"), runtime_state=state)
    if updated != submission:
        return ProviderPollResult(submission=updated, items=())
    return None


def _pane_poll_deferred_prompt(
    submission: ProviderSubmission,
    state: dict[str, object],
    *,
    now: str,
    backend: object,
    pane_id: str,
) -> ProviderPollResult:
    started_at = _state_str(state, "started_at") or submission.accepted_at or now
    ready_wait_secs = _seconds_between(started_at, now)
    state["ready_wait_secs"] = ready_wait_secs

    if _pane_ready_for_input(_pane_snapshot(backend, pane_id)):
        pending_prompt = _state_str(state, "pending_prompt")
        if not pending_prompt:
            return _pane_terminal(
                submission,
                state,
                now,
                status=CompletionStatus.FAILED,
                reason="runtime_state_invalid",
                reply="",
                confidence=CompletionConfidence.DEGRADED,
                diagnostics_extra={"missing_pending_prompt": True},
            )
        send_error = _send_prompt(backend, pane_id, pending_prompt)
        if send_error:
            state["send_error"] = send_error
            return _pane_terminal(
                submission,
                state,
                now,
                status=CompletionStatus.FAILED,
                reason=f"send_failed:{send_error}",
                reply="",
                confidence=CompletionConfidence.DEGRADED,
            )
        state["prompt_sent"] = True
        state["prompt_sent_at"] = now
        state["prompt_deferred_until_ready"] = False
        state["started_at"] = now
        state["last_poll_at"] = now
        return ProviderPollResult(submission=replace(submission, runtime_state=state), items=())

    if ready_wait_secs >= READY_WAIT_SECS:
        return _pane_terminal(
            submission,
            state,
            now,
            status=CompletionStatus.INCOMPLETE,
            reason="pi_input_not_ready",
            reply="",
            confidence=CompletionConfidence.DEGRADED,
            diagnostics_extra={
                "input_not_ready": True,
                "ready_wait_secs": ready_wait_secs,
                "diagnosis": "pi pane did not reach an input-ready state before prompt delivery.",
            },
        )

    state["last_poll_at"] = now
    return ProviderPollResult(submission=replace(submission, runtime_state=state), items=())


__all__ = ["PiProviderAdapter"]
