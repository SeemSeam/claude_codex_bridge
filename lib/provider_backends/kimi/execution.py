from __future__ import annotations

import hashlib
import os
from dataclasses import replace
from datetime import datetime
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
from provider_backends.native_cli_support import wrap_native_prompt
from provider_execution.base import ProviderPollResult, ProviderRuntimeContext, ProviderSubmission
from provider_execution.common import build_item, error_submission, send_prompt_to_runtime_target

from provider_core.protocol import (
    DONE_PREFIX,
    extract_reply_for_req,
    is_done_text,
    request_anchor_for_job,
    strip_done_text,
)
from .hindsight import recall_hindsight_memories, retain_hindsight_turn
from .session import load_project_session
from .native_log import KimiTurnObservation, observe_kimi_turn


PANE_LINES_DEFAULT = 2000
MAX_WAIT_SECS = 300.0
KIMI_NATIVE_TURN_TIMEOUT_ENV = "CCB_KIMI_NATIVE_TURN_TIMEOUT_S"
ANCHOR_WAIT_SECS = 120.0
READY_WAIT_SECS = 60.0
# How long the pane must show an UNCHANGED reply signature AND an unchanged
# pane line count before the pane-idle fallback treats kimi as complete.
#
# Kimi Code CLI is an autonomous loop: a native TurnEnd only marks one
# assistant message finished, not the whole task, so the pane is the ground
# truth for "really done".  Between agentic steps (Read/Grep/Todo/Edit) the
# pane shows the idle input prompt AND a stable (unchanging) last reply for
# many seconds while the agent is actually still working.  A short window
# (the original 10s, and even 30s) routinely fires `kimi_pane_idle_complete`
# mid-task and closes the worker prematurely.
#
# 45s rides out observed inter-step pauses (commonly 10-30s, occasionally
# longer for large Reads) while staying well under MAX_WAIT_SECS so a genuine
# completion is not delayed excessively.  The line-count stability guard in
# _stabilize_pane_observation already filters most noise; this pure time
# threshold is a HEURISTIC safety margin only.
#
# TODO(completion-detection): a stronger finish signal (kimi emitting an
# explicit task-complete / summary marker) should replace this time-based
# fallback; until then this value is the lever that trades latency vs
# premature-close risk.
PANE_FALLBACK_STABLE_SECS = 45.0

# Pane content banners that indicate a terminal provider-side failure for kimi.
# Kimi has no dedicated pane parser in lib/provider_pane_status today, so these
# are tail-content keyword patterns mirroring codex_pane's markers.
#
# Two tiers (mirrors codex_pane's strict/broad split):
#
# *_MARKERS            : broad markers, DIAGNOSTICS-only (never drive
#                        terminalization). Kept for future diagnostics paths.
# HIGH_CONFIDENCE_*    : specific multi-word banners. The ONLY tier that may
#                        drive AUTO-TERMINALIZATION via
#                        _kimi_provider_signal_terminal_result. Generic words
#                        ("usage limit", "quota", "rate limit", "api error",
#                        "overloaded", "unauthorized", ...) are dropped here
#                        because a healthy agent researching those topics
#                        legitimately prints them in its output.
#
# When a kimi pane parser lands, replace this with a call to
# parse_kimi_pane_status (mirroring codex's _read_codex_pane_signal).
_KIMI_USAGE_LIMIT_MARKERS = (
    "usage limit",
    "hit your usage limit",
    "out of credits",
    "purchase more credits",
    "quota exceeded",
    "plan limit",
)
_KIMI_AUTH_FAILED_MARKERS = (
    "invalid api key",
    "incorrect api key",
    "authentication failed",
    "401 unauthorized",
    "unauthorized",
    "no api key provided",
    "api key is invalid",
)
_KIMI_API_ERROR_MARKERS = (
    "rate limit",
    "rate_limit",
    "too many requests",
    "overloaded",
    "api error",
    "internal server error",
    "model unavailable",
    "model_not_found",
    "service unavailable",
    "bad gateway",
    "connection reset",
    "connection timed out",
    "request timed out",
)
_KIMI_CONFIG_ERROR_MARKERS = (
    "invalid config",
    "invalid configuration",
    "failed to parse config",
    "unknown model provider",
    "configuration error",
)

# High-confidence banners: only these may terminalize a kimi job.
_KIMI_HIGH_CONFIDENCE_USAGE_LIMIT_MARKERS = (
    "hit your usage limit",
    "out of credits",
    "purchase more credits",
    "quota exceeded",
)
_KIMI_HIGH_CONFIDENCE_AUTH_FAILED_MARKERS = (
    "invalid api key",
    "incorrect api key",
    "authentication failed",
    "401 unauthorized",
    "no api key provided",
    "api key is invalid",
)
_KIMI_HIGH_CONFIDENCE_API_ERROR_MARKERS = (
    "internal server error",
    "bad gateway",
    "service unavailable",
    "connection reset",
    "connection timed out",
    "request timed out",
)
_KIMI_HIGH_CONFIDENCE_CONFIG_ERROR_MARKERS = (
    "failed to parse config",
    "invalid configuration",
    "unknown model provider",
)

# broad -> high-confidence mapping.
_KIMI_HIGH_CONFIDENCE_MARKERS = {
    _KIMI_USAGE_LIMIT_MARKERS: _KIMI_HIGH_CONFIDENCE_USAGE_LIMIT_MARKERS,
    _KIMI_AUTH_FAILED_MARKERS: _KIMI_HIGH_CONFIDENCE_AUTH_FAILED_MARKERS,
    _KIMI_API_ERROR_MARKERS: _KIMI_HIGH_CONFIDENCE_API_ERROR_MARKERS,
    _KIMI_CONFIG_ERROR_MARKERS: _KIMI_HIGH_CONFIDENCE_CONFIG_ERROR_MARKERS,
}

# Parsed pane signal state -> no_reply_reason.  Mirrors codex's
# _NORMAL_POLL_PANE_STATE_TO_REASON so kimi terminalization is attributable the
# same way codex is.
_KIMI_PANE_SIGNAL_TO_REASON = {
    "usage_limit": "provider_usage_limit",
    "auth_failed": "provider_auth_failed",
    "api_error": "provider_api_error",
    "config_error": "provider_config_error",
}


class KimiProviderAdapter:
    provider = "kimi"

    def restore_diagnostics(self) -> dict[str, object]:
        return {
            "resume_supported": False,
            "restore_mode": "resubmit_required",
            "restore_reason": "provider_resume_unsupported",
            "restore_detail": "kimi native turn log polling cannot resume an interrupted in-flight job; resubmit after restart",
        }

    def start(
        self,
        job: JobRecord,
        *,
        context: ProviderRuntimeContext | None,
        now: str,
    ) -> ProviderSubmission:
        return _start_submission(job, context=context, now=now, provider=self.provider)

    def poll(self, submission: ProviderSubmission, *, now: str) -> ProviderPollResult | None:
        return _poll_submission(submission, now=now)

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


def _start_submission(
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
            error=load_error or "kimi_session_file_missing",
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

    req_id = request_anchor_for_job(job.job_id)
    original_prompt_body = job.request.body or ""
    prompt_body = _with_kimi_context_pointer(original_prompt_body, session)
    hindsight_recall = recall_hindsight_memories(
        original_prompt_body,
        session_id=str(getattr(session, "session_id", "") or req_id),
        agent_name=job.agent_name,
        workspace_path=str(work_dir),
    )
    if hindsight_recall.context:
        prompt_body = f"{hindsight_recall.context}\n\n{prompt_body}"
    prompt = wrap_kimi_prompt(prompt_body, req_id)
    initial_content = _pane_snapshot(backend, pane_id, lines=PANE_LINES_DEFAULT)
    prompt_deferred_until_ready = not _pane_ready_for_input(initial_content)
    send_error: str | None = None
    prompt_sent = False
    if not prompt_deferred_until_ready:
        send_error = _send_prompt(backend, pane_id, prompt)
        prompt_sent = send_error is None

    diagnostics: dict[str, object] = {
        "provider": provider,
        "mode": "native_turn_log",
        "pane_id": pane_id,
        "req_id": req_id,
        "task_id": job.request.task_id,
        "workspace_path": str(work_dir),
    }
    if send_error:
        diagnostics["send_error"] = send_error
    if prompt_deferred_until_ready:
        diagnostics["prompt_deferred_until_ready"] = True
    if hindsight_recall.diagnostics:
        diagnostics["hindsight_recall"] = hindsight_recall.diagnostics

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
            "mode": "native_turn_log",
            "provider": provider,
            "backend": backend,
            "pane_id": pane_id,
            "request_anchor": req_id,
            "req_id": req_id,
            "work_dir": str(work_dir),
            "hindsight_user_prompt": original_prompt_body,
            "hindsight_recall": hindsight_recall.diagnostics or {},
            "started_at": now,
            "last_poll_at": now,
            "prompt_sent": prompt_sent,
            "pending_prompt": prompt,
            "prompt_deferred_until_ready": prompt_deferred_until_ready,
            "send_error": send_error,
            "snapshot_errors": 0,
            "next_seq": 1,
            "anchor_emitted": False,
            "reply_buffer": "",
            "last_reply_signature": "",
            "turn_boundary_ref": "",
            "session_path": "",
        },
    )


def _poll_submission(submission: ProviderSubmission, *, now: str) -> ProviderPollResult | None:
    state = dict(submission.runtime_state)
    send_error = state.get("send_error")
    if send_error:
        return _terminal(
            submission,
            state,
            now,
            status=CompletionStatus.FAILED,
            reason=f"send_failed:{send_error}",
            reply="",
            confidence=CompletionConfidence.DEGRADED,
        )

    pane_id = _state_str(state, "pane_id")
    req_id = _state_str(state, "request_anchor") or _state_str(state, "req_id") or submission.job_id
    work_dir = _state_str(state, "work_dir")
    if not pane_id or not req_id or not work_dir:
        return _terminal(
            submission,
            state,
            now,
            status=CompletionStatus.FAILED,
            reason="runtime_state_invalid",
            reply="",
            confidence=CompletionConfidence.DEGRADED,
        )

    backend = state.get("backend")
    if backend is None:
        return _terminal(
            submission,
            state,
            now,
            status=CompletionStatus.FAILED,
            reason="runtime_handle_lost",
            reply="",
            confidence=CompletionConfidence.DEGRADED,
        )

    # Terminalize early when the kimi pane shows a classified provider-side
    # error banner (usage-limit / auth / api / config).  Mirrors codex's
    # _codex_provider_signal_terminal_result; the pane is the ground truth for
    # kimi and these banners are terminal until reset/reauth.
    provider_signal = _kimi_provider_signal_terminal_result(
        submission, state, backend=backend, pane_id=pane_id, now=now
    )
    if provider_signal is not None:
        return provider_signal

    if not bool(state.get("prompt_sent")):
        return _poll_deferred_prompt(submission, state, now=now, backend=backend, pane_id=pane_id)

    state["last_poll_at"] = now
    state["next_seq"] = _state_int(state, "next_seq", 1)
    started_at = _state_str(state, "started_at") or submission.accepted_at or now
    total_secs = _seconds_between(started_at, now)
    max_wait_secs = _native_turn_timeout_secs()
    state["total_secs"] = total_secs
    state["max_wait_secs"] = max_wait_secs

    native_observation = observe_kimi_turn(Path(work_dir), req_id)
    pane_observation = _observe_kimi_pane_turn(backend, pane_id, req_id)
    if pane_observation is not None:
        pane_observation = _stabilize_pane_observation(state, pane_observation, now)

    # AUTHORITATIVE completion: kimi emits no native task-complete signal, so we
    # instruct it (see wrap_kimi_prompt) to close its final reply with a single
    # `CCB_DONE:<anchor>` line.  A CCB_DONE line matching THIS job's anchor is
    # the authoritative terminal signal — it is fast (no PANE_FALLBACK_STABLE_SECS
    # wait) and unambiguous (cannot fire during inter-step pauses the way the
    # pane-idle heuristic does).  Scan the latest native TurnEnd content and the
    # live pane text; either source is sufficient.
    sentinel_buffer = _kimi_sentinel_buffer(native_observation, pane_observation, backend, pane_id, req_id)
    done_seen = bool(req_id and sentinel_buffer and is_done_text(sentinel_buffer, req_id))
    state["done_seen"] = done_seen

    # FALLBACK: pane-idle-stable.  Kimi Code CLI is an autonomous loop; a native
    # TurnEnd only marks one assistant message finished, not the whole task, so
    # the pane is the fallback ground truth for "really done".  Between agentic
    # steps the pane shows the idle input prompt AND a stable reply for many
    # seconds while still working, so this path is gated by
    # PANE_FALLBACK_STABLE_SECS and is distinguishable from the sentinel path in
    # telemetry (reason=kimi_pane_idle_complete vs kimi_sentinel_complete).
    native_completed = bool(native_observation is not None and native_observation.completed)
    pane_completed = bool(pane_observation is not None and pane_observation.completed)
    fallback_completed = pane_completed or (native_completed and pane_observation is None)

    # Sentinel wins and bypasses the fallback-stability wait.  When only the
    # fallback fires the existing PANE_FALLBACK_STABLE_SECS heuristic applies
    # (already enforced by _stabilize_pane_observation above).
    effective_completed = done_seen or fallback_completed

    observation = native_observation
    if pane_observation is not None and (
        observation is None or (pane_observation.completed and not observation.completed)
    ):
        observation = pane_observation
        state["pane_fallback_observed"] = True
    if observation is None:
        if total_secs >= ANCHOR_WAIT_SECS:
            return _terminal(
                submission,
                state,
                now,
                status=CompletionStatus.INCOMPLETE,
                reason="kimi_native_anchor_missing",
                reply="",
                confidence=CompletionConfidence.DEGRADED,
                diagnostics_extra={
                    "diagnosis": "Kimi native turn log did not record the submitted CCB_REQ_ID.",
                    "anchor_seen": False,
                    "total_secs": total_secs,
                },
            )
        return None

    items = []
    session_path = str(observation.session_path or "")
    if session_path and session_path != _state_str(state, "session_path"):
        items.append(
            build_item(
                submission,
                kind=CompletionItemKind.SESSION_ROTATE,
                timestamp=now,
                seq=_next_seq(state),
                payload={
                    "session_path": session_path,
                    "provider_session_id": observation.session_id,
                },
                cursor_kwargs={"session_path": session_path},
            )
        )
        state["session_path"] = session_path
        state["anchor_emitted"] = False
        state["reply_buffer"] = ""
        state["last_reply_signature"] = ""
        state["turn_boundary_ref"] = ""

    if observation.request_seen and not bool(state.get("anchor_emitted")):
        items.append(
            build_item(
                submission,
                kind=CompletionItemKind.ANCHOR_SEEN,
                timestamp=now,
                seq=_next_seq(state),
                payload={
                    "turn_id": req_id,
                    "session_path": session_path or None,
                    "provider_session_id": observation.session_id,
                    "native_started_at": observation.native_started_at,
                },
                cursor_kwargs={"session_path": session_path or None},
            )
        )
        state["anchor_emitted"] = True

    reply = observation.reply or ""
    # When the sentinel fired, the authoritative reply is the content BEFORE the
    # marker in the sentinel buffer (the full assistant region). ``observation.reply``
    # can be a STALE PARTIAL: it may have been populated at an earlier poll from
    # only the first assistant bullet before kimi finished — kimi emits multiple
    # ``●`` bullets (restatement, tool-use, reasoning, final answer), and the
    # pane observation only carries one chunk at a time.  So when the sentinel
    # fires, always re-extract from the sentinel buffer instead of trusting a
    # possibly-partial ``observation.reply``; keep the prior reply only as a
    # fallback when extraction yields nothing.
    if done_seen and req_id and sentinel_buffer:
        extracted = extract_reply_for_req(sentinel_buffer, req_id) or strip_done_text(sentinel_buffer, req_id)
        if extracted:
            reply = extracted
    # When the sentinel fired, strip the CCB_DONE marker line and anything
    # after it from the persisted reply so we keep the real answer only.  This
    # also guards against a marker that lands mid-text followed by trailing
    # noise.  Mirrors droid's clean_reply (extract_reply_for_req /
    # strip_done_text).  Kimi pane-rendered replies may carry a leading ``●``/
    # ``•`` bullet that is terminal decoration, not part of the answer; strip it
    # so the persisted reply matches the native-reply format.
    if done_seen and req_id and reply:
        cleaned_reply = extract_reply_for_req(reply, req_id)
        if cleaned_reply:
            reply = cleaned_reply
        else:
            reply = strip_done_text(reply, req_id)
        reply = _strip_kimi_reply_bullet(reply)
        observation = replace(observation, reply=reply)

    if effective_completed and not reply:
        return _terminal(
            submission,
            state,
            now,
            status=CompletionStatus.INCOMPLETE,
            reason="kimi_native_empty_reply",
            reply="",
            confidence=CompletionConfidence.OBSERVED,
            diagnostics_extra={
                "empty_reply": True,
                "error_type": "empty_provider_reply",
                "diagnosis": "Kimi recorded TurnEnd for the submitted CCB_REQ_ID but no assistant reply text was found.",
                "session_path": session_path or None,
                "provider_session_id": observation.session_id,
            },
        )

    interim_reply = bool(observation.completed and not effective_completed)
    reply_signature = _hash_text(reply)
    if reply and reply_signature != _state_str(state, "last_reply_signature"):
        state["reply_buffer"] = reply
        state["last_reply_signature"] = reply_signature
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
                    "turn_id": req_id,
                    "session_path": session_path or None,
                    "provider_session_id": observation.session_id,
                    "provider_turn_ref": observation.provider_turn_ref,
                    "native_completed": observation.completed,
                    "interim": interim_reply,
                    "pane_completed": effective_completed,
                    "done_marker": done_seen,
                    "ccb_done": done_seen,
                },
                cursor_kwargs={"session_path": session_path or None},
            )
        )

    boundary_ref = str(observation.provider_turn_ref or observation.session_id or session_path or req_id)
    if effective_completed and boundary_ref != _state_str(state, "turn_boundary_ref"):
        hindsight_prompt = _state_str(state, "hindsight_user_prompt")
        if reply and hindsight_prompt and not bool(state.get("hindsight_retained")):
            retain_result = retain_hindsight_turn(
                prompt=hindsight_prompt,
                reply=reply,
                session_id=_state_str(state, "request_anchor") or submission.job_id,
                job_id=submission.job_id,
                agent_name=submission.agent_name,
                workspace_path=work_dir,
            )
            state["hindsight_retained"] = bool(retain_result.retained)
            if retain_result.diagnostics:
                state["hindsight_retain"] = retain_result.diagnostics
        items.append(
            build_item(
                submission,
                kind=CompletionItemKind.TURN_BOUNDARY,
                timestamp=now,
                seq=_next_seq(state),
                payload={
                    "reason": "kimi_sentinel_complete" if done_seen else "kimi_pane_idle_complete",
                    "last_agent_message": reply,
                    "turn_id": req_id,
                    "session_path": session_path or None,
                    "provider_session_id": observation.session_id,
                    "provider_turn_ref": observation.provider_turn_ref,
                    "native_completed_at": observation.native_completed_at,
                    "native_completed": observation.completed,
                    "done_marker": done_seen,
                    "ccb_done": done_seen,
                    "completion_source": "sentinel" if done_seen else "pane_idle_fallback",
                },
                cursor_kwargs={"session_path": session_path or None},
            )
        )
        state["turn_boundary_ref"] = boundary_ref

    if total_secs >= max_wait_secs and not effective_completed:
        pane_still_working = bool(
            pane_observation is not None and not pane_observation.completed
        )
        return _terminal(
            submission,
            state,
            now,
            status=CompletionStatus.FAILED,
            reason="kimi_native_turn_timeout",
            reply=str(state.get("reply_buffer") or ""),
            confidence=CompletionConfidence.DEGRADED,
            diagnostics_extra={
                "max_wait_secs": max_wait_secs,
                "pane_still_working": pane_still_working,
                "reply_confidence": "low",
                "native_completed": bool(observation.completed),
                "diagnosis": "Kimi native turn ended but the pane never reached a stable idle input prompt; treating as incomplete rather than falsely completed.",
            },
        )

    updated = replace(submission, reply=str(state.get("reply_buffer") or ""), runtime_state=state)
    if items or updated != submission:
        return ProviderPollResult(submission=updated, items=tuple(items))
    return None


def _poll_deferred_prompt(
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
    content = _pane_snapshot(backend, pane_id, lines=PANE_LINES_DEFAULT)
    if _pane_ready_for_input(content):
        pending_prompt = _state_str(state, "pending_prompt")
        if not pending_prompt:
            return _terminal(
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
            return _terminal(
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
        _next_seq(state)
        return ProviderPollResult(submission=replace(submission, runtime_state=state), items=())

    if ready_wait_secs >= READY_WAIT_SECS:
        return _terminal(
            submission,
            state,
            now,
            status=CompletionStatus.INCOMPLETE,
            reason="kimi_input_not_ready",
            reply="",
            confidence=CompletionConfidence.DEGRADED,
            diagnostics_extra={
                "input_not_ready": True,
                "ready_wait_secs": ready_wait_secs,
                "diagnosis": "Kimi pane did not reach an input-ready state before prompt delivery.",
            },
        )
    state["last_poll_at"] = now
    _next_seq(state)
    return ProviderPollResult(submission=replace(submission, runtime_state=state), items=())


def _kimi_provider_signal_terminal_result(
    submission: ProviderSubmission,
    state: dict[str, object],
    *,
    backend: object,
    pane_id: str,
    now: str,
) -> ProviderPollResult | None:
    """Terminalize early when the kimi pane shows a HIGH-CONFIDENCE provider error.

    Mirrors codex's _codex_provider_signal_terminal_result and uses the strict
    / high-confidence marker tier only: a healthy agent whose output merely
    *discusses* usage limits / quotas / api errors must NEVER be terminalized.
    Broad markers stay available for diagnostics.
    """
    signal = _read_kimi_pane_signal(backend, pane_id, strict=True)
    if signal is None:
        return None
    no_reply_reason = _KIMI_PANE_SIGNAL_TO_REASON.get(str(signal.get("pane_signal_state") or ""))
    if no_reply_reason is None:
        return None
    return _terminal(
        submission,
        state,
        now,
        status=CompletionStatus.FAILED,
        reason=no_reply_reason,
        reply="",
        confidence=CompletionConfidence.DEGRADED,
        diagnostics_extra={
            "pane_signal_state": signal.get("pane_signal_state"),
            "pane_signal_reason": signal.get("pane_signal_reason"),
            "pane_tail": signal.get("pane_tail"),
            "matched_markers": signal.get("matched_markers"),
        },
        no_reply_reason=no_reply_reason,
        no_reply_detail={
            "pane_signal_state": signal.get("pane_signal_state"),
            "matched_markers": signal.get("matched_markers"),
        },
    )


def _read_kimi_pane_signal(
    backend: object,
    pane_id: str,
    *,
    strict: bool = False,
) -> dict[str, object] | None:
    """Scan pane tail content for kimi provider-error banners.

    Returns a fragment ({pane_signal_state, pane_signal_reason, pane_tail,
    matched_markers}) when a classified banner is present, else None.  Order
    matches codex_pane: usage_limit > auth_failed > config_error > api_error so
    the most specific terminal signal wins on overlapping text.

    ``strict`` selects the marker tier (mirrors codex_pane strict mode):
    ``strict=False`` uses broad markers (diagnostics), ``strict=True`` uses
    high-confidence markers only (auto-terminalization).
    """
    content = _pane_snapshot(backend, pane_id, lines=120)
    if not content or not content.strip():
        return None
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    recent_lines = [line.rstrip() for line in normalized.splitlines() if line.strip()]
    if not recent_lines:
        return None
    recent = " ".join(line.strip() for line in recent_lines[-20:]).lower()
    tail = "\n".join(recent_lines[-20:])

    for state_key, markers in (
        ("usage_limit", _KIMI_USAGE_LIMIT_MARKERS),
        ("auth_failed", _KIMI_AUTH_FAILED_MARKERS),
        ("config_error", _KIMI_CONFIG_ERROR_MARKERS),
        ("api_error", _KIMI_API_ERROR_MARKERS),
    ):
        if strict:
            markers = _KIMI_HIGH_CONFIDENCE_MARKERS.get(markers, markers)
        matched = tuple(marker for marker in markers if marker in recent)
        if matched:
            return {
                "pane_signal_state": state_key,
                "pane_signal_reason": _KIMI_PANE_SIGNAL_TO_REASON.get(state_key, state_key),
                "pane_tail": tail,
                "matched_markers": matched,
            }
    return None


def _terminal(
    submission: ProviderSubmission,
    state: dict[str, object],
    now: str,
    *,
    status: CompletionStatus,
    reason: str,
    reply: str,
    confidence: CompletionConfidence,
    diagnostics_extra: dict[str, object] | None = None,
    no_reply_reason: str | None = None,
    no_reply_detail: dict[str, object] | None = None,
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
    diagnostics = {
        "mode": "native_turn_log",
        "total_secs": float(state.get("total_secs") or state.get("ready_wait_secs") or 0.0),
        "anchor_seen": bool(state.get("anchor_emitted")),
        "reply_chars": len(cleaned_reply),
    }
    diagnostics.update(diagnostics_extra or {})
    if _is_no_captured_reply_timeout(reason=reason, reply=cleaned_reply):
        diagnostics.update(
            {
                "no_captured_reply": True,
                "provider_no_reply": True,
                "receipt_valid": False,
                "receipt_class": "no_captured_reply",
                "error_type": "empty_provider_reply",
                "diagnosis": (
                    "Kimi native turn polling timed out after observing the submitted CCB_REQ_ID, "
                    "but no assistant reply text was captured."
                ),
            }
        )
    diagnostics["no_reply_reason"] = no_reply_reason or _reason_to_no_reply_reason(reason, cleaned_reply)
    diagnostics["no_reply_detail"] = dict(no_reply_detail or {})
    decision = CompletionDecision(
        terminal=True,
        status=status,
        reason=reason,
        confidence=confidence,
        reply=cleaned_reply,
        anchor_seen=bool(state.get("anchor_emitted")),
        reply_started=bool(cleaned_reply),
        reply_stable=bool(cleaned_reply) and status is CompletionStatus.COMPLETED,
        provider_turn_ref=_state_str(state, "request_anchor") or submission.job_id,
        source_cursor=cursor,
        finished_at=now,
        diagnostics=diagnostics,
    )
    return ProviderPollResult(submission=progress, items=(), decision=decision)


def _is_no_captured_reply_timeout(*, reason: str, reply: str) -> bool:
    return reason == "kimi_native_turn_timeout" and not (reply or "")


def _reason_to_no_reply_reason(reason: str, reply: str) -> str:
    lowered = str(reason or "").lower()
    if "send_failed" in lowered or "send" in lowered:
        return "provider_config_error"
    if "runtime_state_invalid" in lowered or "runtime_unavailable" in lowered or "backend_unavailable" in lowered:
        return "agent_unreachable_dead"
    if "handle_lost" in lowered or "pane_unavailable" in lowered:
        return "agent_unreachable_dead"
    if "anchor_missing" in lowered:
        return "completion_detection_gap"
    if "empty_reply" in lowered:
        return "provider_empty_output"
    if "timeout" in lowered:
        return "completion_detection_gap" if reply else "provider_waiting_for_user"
    if "not_ready" in lowered:
        return "provider_config_error"
    return "provider_api_error"


def _pane_snapshot(backend: object, pane_id: str, *, lines: int) -> str:
    getter = getattr(backend, "get_pane_content", None)
    if not callable(getter):
        getter = getattr(backend, "get_text", None)
    if not callable(getter):
        return ""
    try:
        return str(getter(pane_id, lines=lines) or "")
    except Exception:
        return ""


def _pane_ready_for_input(content: str) -> bool:
    text = content or ""
    legacy_ready = "── input" in text and "agent (" in text
    k27_ready = "│ >" in text and "K2.7 Code" in text and "context:" in text
    return legacy_ready or k27_ready


def _kimi_sentinel_buffer(
    native_observation: KimiTurnObservation | None,
    pane_observation: KimiTurnObservation | None,
    backend: object,
    pane_id: str,
    req_id: str,
) -> str:
    """Return the text to scan for the CCB_DONE sentinel.

    A sentinel line is authoritative from EITHER source: the latest native
    TurnEnd reply OR the live pane content.  We deliberately re-snapshot the
    pane here (rather than reuse pane_observation.reply) because the pane reply
    is only populated when the pane is idle-stable, while the marker can appear
    in the raw pane content as soon as kimi prints it.

    The raw pane snapshot is reduced to the ASSISTANT REPLY REGION before
    scanning (see ``_extract_kimi_reply_region``).  This is the kimi analogue
    of droid scanning only ``assistant`` events: the pane always contains the
    PROMPT, and ``wrap_kimi_prompt`` shows kimi the marker format inline as an
    instruction example (``CCB_DONE: <anchor>``).  Scanning the raw pane would
    therefore (a) false-fire on the prompt's example marker, and (b) miss the
    real emitted marker because the idle input box (``╭``/``│ >``/``╰``) always
    sits BELOW it, so ``is_done_text`` (which scans from the last non-noise
    line) matches the box line instead of the marker.  Both are avoided by
    extracting only the reply region: everything after the LAST prompt/anchor
    echo, up to the input box.
    """
    chunks: list[str] = []
    if native_observation is not None and native_observation.reply:
        chunks.append(native_observation.reply)
    if pane_observation is not None and pane_observation.reply:
        chunks.append(pane_observation.reply)
    pane_text = _pane_snapshot(backend, pane_id, lines=PANE_LINES_DEFAULT)
    if pane_text:
        reply_region = _extract_kimi_reply_region(pane_text, req_id)
        if reply_region:
            chunks.append(reply_region)
    return "\n".join(chunk for chunk in chunks if chunk)


def _extract_kimi_reply_region(content: str, req_id: str) -> str:
    """Carve the assistant reply region out of the raw pane snapshot.

    The pane contains, in order: the echoed PROMPT (which includes the
    ``CCB_DONE: <anchor>`` instruction example, since ``wrap_kimi_prompt``
    shows kimi the marker format), then kimi's EMITTED assistant reply
    (answer + the real ``CCB_DONE: <anchor>`` line), then the idle input box.
    The sentinel must match only the emitted marker in the assistant region.

    This returns ONLY the assistant reply region, so:

    * the prompt's example marker is excluded (it lives in the prompt region,
      before the first assistant bullet line),
    * the trailing input-box lines are excluded (they would otherwise be the
      last non-noise line and defeat ``is_done_text``'s tail scan), and
    * the emitted marker is visible as soon as kimi prints it, WITHOUT waiting
      for pane idle-stable (the 45s fallback).

    Mirrors droid's prompt/reply separation (droid scans only ``assistant``
    events; kimi has no structured event stream for the pane, so we carve the
    region textually).  The assistant region is bounded by:
      start = first ``●``/``•`` bullet line AFTER the last ``CCB_REQ_ID`` prompt
              anchor (the bullet is kimi's assistant-message decoration, never
              part of the prompt echo),
      end   = the idle input box (``╭``/``│ >``/``╰``) or end of pane.
    """
    text = (content or "").replace("\r\n", "\n").replace("\r", "\n")
    if not text or not req_id:
        return ""

    # Bound the search window: everything after the last prompt anchor line.
    # ``CCB_REQ_ID: <anchor>`` is the canonical prompt anchor emitted by
    # ``wrap_kimi_prompt``; the example marker also carries the anchor but is
    # part of the prompt instruction block, so we anchor on CCB_REQ_ID.
    prompt_anchor = f"CCB_REQ_ID: {req_id}"
    cut_index = text.rfind(prompt_anchor)
    if cut_index != -1:
        search_from = cut_index + len(prompt_anchor)
    else:
        # Fallback when no CCB_REQ_ID line is present: search the whole pane.
        search_from = 0

    lines = text[search_from:].split("\n")

    # Locate the FIRST assistant bullet line.  The prompt instruction block
    # (including the example ``CCB_DONE: <anchor>``) never starts with a
    # ``●``/``•`` bullet, so the first bullet reliably marks the start of
    # kimi's emitted reply.
    start_index: int | None = None
    for index, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith(("●", "•")):
            start_index = index
            break
    if start_index is None:
        # No assistant bullet yet: kimi hasn't started (or hasn't finished
        # streaming the first line).  Nothing to scan.
        return ""

    region: list[str] = []
    for line in lines[start_index:]:
        if _looks_like_kimi_input_box_line(line.strip()):
            break
        region.append(line)

    # Drop trailing blank lines (gap before the input box).
    while region and not region[-1].strip():
        region.pop()
    return "\n".join(region).rstrip()


def _observe_kimi_pane_turn(backend: object, pane_id: str, req_id: str) -> KimiTurnObservation | None:
    content = _pane_snapshot(backend, pane_id, lines=PANE_LINES_DEFAULT)
    if not content or req_id not in content:
        return None
    completed = _pane_ready_for_input(content)
    reply = _extract_kimi_pane_reply(content, req_id) if completed else ""
    return KimiTurnObservation(
        request_seen=True,
        completed=bool(completed and reply),
        reply=reply,
        session_id=pane_id,
        session_path=f"pane:{pane_id}",
        provider_turn_ref=f"pane:{pane_id}:{req_id}",
        line_count=len(content.splitlines()),
        native_started_at=None,
        native_completed_at=None,
    )


def _stabilize_pane_observation(
    state: dict[str, object],
    observation: KimiTurnObservation,
    now: str,
) -> KimiTurnObservation:
    reply = observation.reply or ""
    if not reply:
        state.pop("pane_fallback_candidate_signature", None)
        state.pop("pane_fallback_candidate_since", None)
        state.pop("pane_fallback_pane_lines", None)
        state.pop("pane_fallback_pane_stable_since", None)
        return observation

    signature = _hash_text(reply)
    pane_lines = observation.line_count
    reply_stable = signature == _state_str(state, "pane_fallback_candidate_signature")
    pane_stable = pane_lines == _state_int(state, "pane_fallback_pane_lines", -1)

    if not reply_stable or not pane_stable:
        state["pane_fallback_candidate_signature"] = signature
        state["pane_fallback_candidate_since"] = now
        state["pane_fallback_pane_lines"] = pane_lines
        state["pane_fallback_pane_stable_since"] = now
        return replace(observation, completed=False)

    stable_since = _state_str(state, "pane_fallback_pane_stable_since") or now
    stable_secs = _seconds_between(stable_since, now)
    state["pane_fallback_stable_secs"] = stable_secs
    if stable_secs < PANE_FALLBACK_STABLE_SECS:
        return replace(observation, completed=False)
    return observation


def _extract_kimi_pane_reply(content: str, req_id: str) -> str:
    tail = content.split(req_id, 1)[-1]
    lines = tail.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    answer_start: int | None = None
    first_answer_line = ""
    for index, line in enumerate(lines):
        stripped = line.strip()
        if not stripped.startswith(("●", "•")):
            continue
        candidate = stripped.lstrip("●•").strip()
        if not candidate or _looks_like_kimi_non_answer(candidate):
            continue
        answer_start = index
        first_answer_line = candidate
        break
    if answer_start is None:
        return ""

    reply_lines = [first_answer_line]
    for line in lines[answer_start + 1 :]:
        stripped = line.strip()
        if _looks_like_kimi_input_box_line(stripped):
            break
        if stripped.startswith(("●", "•")) and _looks_like_kimi_non_answer(stripped.lstrip("●•").strip()):
            break
        reply_lines.append(line.rstrip())
    return _clean_kimi_pane_reply("\n".join(reply_lines), req_id)


def _clean_kimi_pane_reply(text: str, req_id: str) -> str:
    lines = [line.rstrip() for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    if lines and lines[0].strip().startswith(("●", "•")):
        lines[0] = lines[0].strip().lstrip("●•").strip()
    return "\n".join(lines).strip()


def _strip_kimi_reply_bullet(text: str) -> str:
    """Strip a leading kimi pane bullet (``●``/``•``) from the first reply line.

    The pane renders the first assistant line with a ``●``/``•`` decoration that
    is terminal chrome, not part of the answer.  The native TurnEnd reply path
    never carries it; strip it from pane-derived replies so both paths persist
    the same answer shape.
    """
    if not text:
        return text
    lines = text.split("\n")
    if not lines:
        return text
    stripped = lines[0].lstrip()
    if stripped[:1] in ("●", "•"):
        lines[0] = stripped.lstrip("●•").lstrip()
    return "\n".join(lines)


def _looks_like_kimi_input_box_line(stripped: str) -> bool:
    if not stripped:
        return False
    if stripped.startswith("╭") or stripped.startswith("╰"):
        return True
    if stripped.startswith("│ >"):
        return True
    return "K2.7 Code" in stripped and "context:" in stripped


def _looks_like_kimi_non_answer(text: str) -> bool:
    stripped = text.strip()
    lowered = stripped.lower()
    if not stripped or all(char in "🌑🌒🌓🌔🌕🌖🌗🌘⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏ .·" for char in stripped):
        return True
    return lowered.startswith(
        (
            "using ",
            "used ",
            "reading ",
            "read ",
            "run ",
            "running ",
            "todo",
            "thinking",
            "user wants",
            "user asks",
            "user says",
            "user requests",
            "user requested",
            "the user wants",
            "the user asks",
            "the user says",
            "the user requested",
            "they want",
            "they ask",
            "they request",
            "let me ",
            "let's ",
            "i'll ",
            "i will ",
            "i can ",
            "i am ",
            "i'm ",
            "i need",
            "i should",
            "should ",
            "we have",
            "we need",
            "need ",
            "now need",
            "good. ",
            "no docs lint script",
            "the task",
        )
    )


def _send_prompt(backend: object, pane_id: str, prompt: str) -> str | None:
    try:
        send_prompt_to_runtime_target(backend, pane_id, prompt)
    except Exception as exc:
        return f"send_text_failed:{exc!r}"
    return None


def wrap_kimi_prompt(message: str, req_id: str) -> str:
    """Wrap a kimi task prompt with the CCB request anchor + CCB_DONE sentinel.

    Mirrors ``wrap_droid_prompt``: the generic ``wrap_native_prompt`` already
    injects ``CCB_REQ_ID:<anchor>`` and reply guidance.  Kimi has NO native
    authoritative task-complete signal (only TurnBegin/ContentPart/TurnEnd),
    so we add an explicit instruction that kimi must close its final reply with
    a single ``CCB_DONE:<anchor>`` line.  The marker is the authoritative
    completion signal used by ``_kimi_sentinel_complete``.
    """
    body = wrap_native_prompt((message or "").rstrip(), req_id)
    return (
        f"{body}\n"
        "IMPORTANT COMPLETION SIGNAL:\n"
        "- When the requested task is FULLY complete, end your final reply with "
        "this exact final line on its own line, verbatim, with nothing else on "
        "that line and nothing after it:\n"
        f"{DONE_PREFIX} {req_id}\n"
        "- Emit this marker ONLY when the whole task is done, never during "
        "inter-step thinking or tool-use pauses.\n"
    )


def _with_kimi_context_pointer(message: str, session: object) -> str:
    data = getattr(session, "data", None)
    context_path = ""
    if isinstance(data, dict):
        context_path = str(data.get("kimi_context_path") or "").strip()
    if not context_path:
        return message
    context = "\n".join(
        [
            "CCB Kimi context:",
            f"- Read and follow: {context_path}",
            "- Kimi does not load local CCB skills directly; this context file is the scoped CCB memory/rules projection.",
            "- Implementation completed is not review/archive; keep lifecycle truth separate.",
            "",
        ]
    )
    return f"{context}{message or ''}"


def _resolve_work_dir(job: JobRecord, context: ProviderRuntimeContext | None) -> Path | None:
    candidate = (context.workspace_path if context else None) or job.workspace_path
    if not candidate:
        return None
    try:
        return Path(candidate).expanduser()
    except Exception:
        return None


def _hash_text(text: str) -> str:
    if not text:
        return ""
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()


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


def _native_turn_timeout_secs() -> float:
    raw = os.environ.get(KIMI_NATIVE_TURN_TIMEOUT_ENV)
    if raw is None or not raw.strip():
        return MAX_WAIT_SECS
    try:
        value = float(raw)
    except ValueError:
        return MAX_WAIT_SECS
    if value <= 0:
        return MAX_WAIT_SECS
    return value


def _next_seq(state: dict[str, object]) -> int:
    seq = _state_int(state, "next_seq", 1)
    state["next_seq"] = seq + 1
    return seq


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


def build_execution_adapter() -> KimiProviderAdapter:
    return KimiProviderAdapter()


__all__ = ["KimiProviderAdapter", "build_execution_adapter", "wrap_kimi_prompt"]
