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

from provider_core.protocol import request_anchor_for_job
from .hindsight import recall_hindsight_memories, retain_hindsight_turn
from .session import load_project_session
from .native_log import KimiTurnObservation, observe_kimi_turn


PANE_LINES_DEFAULT = 2000
MAX_WAIT_SECS = 300.0
KIMI_NATIVE_TURN_TIMEOUT_ENV = "CCB_KIMI_NATIVE_TURN_TIMEOUT_S"
ANCHOR_WAIT_SECS = 120.0
READY_WAIT_SECS = 60.0
PANE_FALLBACK_STABLE_SECS = 10.0


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
    prompt = wrap_native_prompt(prompt_body, req_id)
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

    if not bool(state.get("prompt_sent")):
        return _poll_deferred_prompt(submission, state, now=now, backend=backend, pane_id=pane_id)

    state["last_poll_at"] = now
    state["next_seq"] = _state_int(state, "next_seq", 1)
    started_at = _state_str(state, "started_at") or submission.accepted_at or now
    total_secs = _seconds_between(started_at, now)
    max_wait_secs = _native_turn_timeout_secs()
    state["total_secs"] = total_secs
    state["max_wait_secs"] = max_wait_secs

    observation = observe_kimi_turn(Path(work_dir), req_id)
    pane_observation = _observe_kimi_pane_turn(backend, pane_id, req_id)
    if pane_observation is not None:
        pane_observation = _stabilize_pane_observation(state, pane_observation, now)
    if pane_observation is not None and observation is None:
        # Pane scraping is a rescue path only: once the native wire log has
        # recorded the req_id anchor, wait for the native turn to complete
        # instead of letting a prematurely-stable pane snapshot (input box
        # visible during answer generation) truncate the reply.
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
    if observation.completed and not reply:
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
                },
                cursor_kwargs={"session_path": session_path or None},
            )
        )

    boundary_ref = str(observation.provider_turn_ref or observation.session_id or session_path or req_id)
    if observation.completed and boundary_ref != _state_str(state, "turn_boundary_ref"):
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
                    "reason": "kimi_turn_end",
                    "last_agent_message": reply,
                    "turn_id": req_id,
                    "session_path": session_path or None,
                    "provider_session_id": observation.session_id,
                    "provider_turn_ref": observation.provider_turn_ref,
                    "native_completed_at": observation.native_completed_at,
                },
                cursor_kwargs={"session_path": session_path or None},
            )
        )
        state["turn_boundary_ref"] = boundary_ref

    if total_secs >= max_wait_secs and not observation.completed:
        return _terminal(
            submission,
            state,
            now,
            status=CompletionStatus.FAILED,
            reason="kimi_native_turn_timeout",
            reply=str(state.get("reply_buffer") or ""),
            confidence=CompletionConfidence.DEGRADED,
            diagnostics_extra={"max_wait_secs": max_wait_secs},
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
    prompt_ready = "│ >" in text and "context:" in text
    return legacy_ready or prompt_ready


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
        return observation

    signature = _hash_text(reply)
    if signature != _state_str(state, "pane_fallback_candidate_signature"):
        state["pane_fallback_candidate_signature"] = signature
        state["pane_fallback_candidate_since"] = now
        return replace(observation, completed=False)

    stable_since = _state_str(state, "pane_fallback_candidate_since") or now
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


def _looks_like_kimi_input_box_line(stripped: str) -> bool:
    if not stripped:
        return False
    if stripped.startswith("╭") or stripped.startswith("╰"):
        return True
    if stripped.startswith("│ >"):
        return True
    return "context:" in stripped


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


__all__ = ["KimiProviderAdapter", "build_execution_adapter"]
