from __future__ import annotations

import re
from dataclasses import replace

from completion.models import CompletionConfidence, CompletionDecision, CompletionStatus
from ccbd.system import parse_utc_timestamp
from provider_execution.active import ensure_active_pane_alive, prepare_active_poll_without_liveness
from provider_execution.base import ProviderPollResult, ProviderSubmission

from .event_reading import is_turn_boundary_event, read_events, terminal_api_error_payload
from .hook_results import poll_exact_hook
from .hook_results_runtime import hook_poll_context
from provider_hooks.artifacts import load_event
from .state_machine import (
    apply_session_rotation,
    build_poll_state,
    finalize_poll_result,
    handle_assistant_event,
    handle_prompt_lifecycle_event,
    handle_system_event,
    handle_user_event,
    is_top_level_user_prompt,
)
from .start import looks_ready, send_prompt, state_session_path


def poll_submission(
    adapter,
    submission: ProviderSubmission,
    *,
    now: str,
) -> ProviderPollResult | None:
    del adapter
    prepared = _prepare_submission_poll(submission, now=now)
    if prepared is None or isinstance(prepared, ProviderPollResult):
        return prepared
    prompt_dispatch = _dispatch_deferred_prompt(
        submission,
        prepared=prepared,
        now=now,
    )
    if isinstance(prompt_dispatch, ProviderPollResult):
        return prompt_dispatch
    dispatch_items = ()
    if isinstance(prompt_dispatch, ProviderSubmission):
        submission = prompt_dispatch
    reply_delivery_terminal = _reply_delivery_terminal_if_dispatched(submission, now=now)
    if reply_delivery_terminal is not None:
        return _merge_poll_result_items(reply_delivery_terminal, prefix_items=dispatch_items)
    hook_result = poll_exact_hook(submission, now=now) if _prompt_completion_is_eligible(submission) else None
    if hook_result is None:
        hook_result = _orphaned_exact_hook(submission, prepared=prepared, now=now)
    if hook_result is not None:
        return _merge_poll_result_items(hook_result, prefix_items=dispatch_items)
    pane_dead_result = _ensure_prepared_pane_alive(submission, prepared=prepared, now=now)
    if pane_dead_result is not None:
        return _merge_poll_result_items(pane_dead_result, prefix_items=dispatch_items)
    state = submission.runtime_state.get("state") or {}
    poll = build_poll_state(submission)
    state = _poll_event_batches(submission, prepared.reader, poll, state=state, now=now)
    if isinstance(state, ProviderPollResult):
        return _merge_poll_result_items(state, prefix_items=dispatch_items)
    pane_terminal = _idle_pane_round_result_terminal(
        submission,
        prepared=prepared,
        poll=poll,
        state=state,
        now=now,
    )
    if pane_terminal is not None:
        return _merge_poll_result_items(pane_terminal, prefix_items=dispatch_items)
    return _merge_poll_result_items(
        finalize_poll_result(submission, poll, state=state),
        prefix_items=dispatch_items,
    )


_ROUND_RESULT_RE = re.compile(
    r"(?:^|\n)\s*[●•⏺]\s*round\s+result\s*:\s*"
    r"(pass|partial|replan_required|blocked)\b",
    re.IGNORECASE,
)


def _idle_pane_round_result_terminal(
    submission: ProviderSubmission,
    *,
    prepared,
    poll,
    state: dict[str, object],
    now: str,
) -> ProviderPollResult | None:
    """Recover a parser-enforced round result omitted from Claude's event log.

    Some Claude-compatible endpoints render the final answer and return to the
    input box without persisting a final assistant text event or firing Stop.
    The request anchor, result, and idle prompt must all be visible in order in
    the same pane snapshot; no elapsed-time inference is used.
    """
    if submission.agent_name != "ccb_round_reviewer":
        return None
    if poll.reached_turn_boundary or not poll.anchor_seen or not poll.request_anchor:
        return None
    get_pane_content = getattr(prepared.backend, "get_pane_content", None)
    if not callable(get_pane_content):
        return None
    try:
        pane_text = str(get_pane_content(prepared.pane_id, lines=2000) or "")
    except Exception:
        return None
    anchored = _pane_text_after_latest_anchor(pane_text, poll.request_anchor)
    if anchored is None:
        return None
    matches = tuple(_ROUND_RESULT_RE.finditer(anchored))
    if not matches:
        return None
    match = matches[-1]
    after_result = anchored[match.end() :]
    if not _has_idle_input_box(after_result):
        return None

    round_result = match.group(1).lower()
    reply = f"round result: {round_result}"
    updated = replace(
        submission,
        reply=reply,
        runtime_state={
            **submission.runtime_state,
            "state": state,
            "next_seq": poll.next_seq,
            "anchor_seen": poll.anchor_seen,
            "reply_buffer": reply,
            "raw_buffer": poll.raw_buffer,
            "session_path": poll.session_path,
            "last_assistant_uuid": poll.last_assistant_uuid,
            "prompt_enqueued": poll.prompt_enqueued,
            "queue_dequeue_observed": poll.queue_dequeue_observed,
            "prompt_activated": poll.prompt_activated,
            "prompt_enqueue_uuid": poll.prompt_enqueue_uuid,
            "prompt_activation_uuid": poll.prompt_activation_uuid,
        },
    )
    decision = CompletionDecision(
        terminal=True,
        status=CompletionStatus.COMPLETED,
        reason="claude_idle_pane_round_result",
        confidence=CompletionConfidence.OBSERVED,
        reply=reply,
        anchor_seen=True,
        reply_started=True,
        reply_stable=True,
        provider_turn_ref=poll.request_anchor,
        source_cursor=None,
        finished_at=now,
        diagnostics={
            "completion_source": "idle_pane_round_result",
            "pane_id": prepared.pane_id,
            "round_result": round_result,
            "session_event_final_text_missing": True,
        },
    )
    return ProviderPollResult(submission=updated, items=tuple(poll.items), decision=decision)


def _pane_text_after_latest_anchor(text: str, request_anchor: str) -> str | None:
    index = text.rfind(request_anchor)
    if index < 0:
        return None
    return text[index + len(request_anchor) :]


def _has_idle_input_box(text: str) -> bool:
    if "esc to interrupt" in text.lower():
        return False
    for line in text.splitlines():
        normalized = line.replace("\xa0", " ").strip()
        if normalized.startswith("❯") and not normalized[1:].strip():
            return True
        if re.fullmatch(r"[│|]\s*[>❯]\s*[│|]", normalized):
            return True
    return False


def _prepare_submission_poll(
    submission: ProviderSubmission,
    *,
    now: str,
):
    prepared = prepare_active_poll_without_liveness(submission, now=now)
    return prepared


def _dispatch_deferred_prompt(
    submission: ProviderSubmission,
    *,
    prepared,
    now: str,
) -> ProviderPollResult | ProviderSubmission | None:
    if bool(submission.runtime_state.get("prompt_sent", True)):
        return None
    if not _prompt_delivery_due(submission, backend=prepared.backend, pane_id=prepared.pane_id, now=now):
        if bool(submission.runtime_state.get("prompt_deferred_for_ready", False)):
            return None
        return replace(
            submission,
            runtime_state={
                **submission.runtime_state,
                "prompt_deferred_for_ready": True,
            },
        )
    prompt = str(submission.runtime_state.get("prompt_text") or "")
    send_prompt(prepared.backend, prepared.pane_id, prompt)
    anchor_seen = bool(submission.runtime_state.get("anchor_seen", False))
    updated = replace(
        submission,
        runtime_state={
            **submission.runtime_state,
            "prompt_sent": True,
            "prompt_sent_at": now,
            "anchor_seen": anchor_seen,
            "prompt_activated": bool(submission.runtime_state.get("prompt_activated", False)),
            "prompt_deferred_for_ready": False,
            "prompt_anchor_emitted_at": "",
        },
    )
    return updated


def _prompt_completion_is_eligible(submission: ProviderSubmission) -> bool:
    state = submission.runtime_state
    if bool(state.get("no_wrap", False)):
        return True
    if "prompt_activated" in state:
        return bool(state.get("prompt_activated", False) and state.get("anchor_seen", False))
    if state.get("prompt_anchor_emitted_at"):
        return False
    return bool(state.get("anchor_seen", False))


_ORPHANED_HOOK_GRACE_S = 180.0


def _orphaned_exact_hook(submission: ProviderSubmission, *, prepared, now: str) -> ProviderPollResult | None:
    """Recover a terminal Stop-hook artifact when session-log anchor tracking failed.

    The primary hook path is gated on the session event log observing prompt
    activation and the request anchor. If log parsing misses that window
    (parser regression, rotated offsets, already-consumed bytes), the job
    would zombie-run forever with its terminal reply stranded on disk and its
    active-task flag never cleared. Accept the artifact only with independent
    proof: it names this job's own request anchor (load_event enforces the
    req_id match), it was written after submission, a grace period has elapsed
    since it was written, the hook session matches the tracked session, and
    the target pane is observably idle.
    """
    state = submission.runtime_state
    if bool(state.get("no_wrap", False)):
        return None
    if not bool(state.get("prompt_sent", False)):
        return None
    context = hook_poll_context(submission)
    if context is None:
        return None
    event = load_event(context.completion_dir, context.request_anchor)
    if not event:
        return None
    finished_at = _parse_utc(str(event.get("timestamp") or ""))
    now_dt = _parse_utc(now)
    if finished_at is None or now_dt is None:
        return None
    accepted_at = _parse_utc(str(getattr(submission, "accepted_at", "") or ""))
    if accepted_at is not None and finished_at < accepted_at:
        return None
    if (now_dt - finished_at).total_seconds() < _ORPHANED_HOOK_GRACE_S:
        return None
    if not _orphaned_hook_session_matches(submission, event):
        return None
    if not _pane_observably_idle(prepared):
        return None
    return poll_exact_hook(submission, now=now)


def _parse_utc(value: str):
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = parse_utc_timestamp(text)
    except Exception:
        return None
    if parsed.tzinfo is None:
        from datetime import timezone

        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _orphaned_hook_session_matches(submission: ProviderSubmission, event: dict[str, object]) -> bool:
    hook_session = str(event.get("session_id") or "").strip()
    tracked = str(submission.runtime_state.get("session_path") or "").strip()
    if not hook_session or not tracked:
        return True
    tracked_stem = tracked.rsplit("/", 1)[-1].removesuffix(".jsonl")
    return hook_session == tracked_stem


def _pane_observably_idle(prepared) -> bool:
    backend = getattr(prepared, "backend", None)
    get_pane_content = getattr(backend, "get_pane_content", None)
    if not callable(get_pane_content):
        return False
    try:
        text = str(get_pane_content(getattr(prepared, "pane_id", None), lines=80) or "")
    except Exception:
        return False
    if "esc to interrupt" in text.lower():
        return False
    return _has_idle_input_box(text)


def _merge_poll_result_items(result: ProviderPollResult, *, prefix_items: tuple) -> ProviderPollResult:
    if not prefix_items:
        return result
    return ProviderPollResult(
        submission=result.submission,
        items=tuple(prefix_items) + tuple(result.items),
        decision=result.decision,
    )


def _prompt_delivery_due(
    submission: ProviderSubmission,
    *,
    backend: object,
    pane_id: str,
    now: str,
) -> bool:
    get_pane_content = getattr(backend, "get_pane_content", None)
    if not callable(get_pane_content):
        return True
    try:
        text = str(get_pane_content(pane_id, lines=120) or "")
    except Exception:
        return True
    if looks_ready(text):
        return True
    # Reply delivery prefers an observed ready prompt, but it must not deadlock
    # a serial mailbox queue forever when the prompt detector never converges.
    return _ready_wait_timed_out(submission, now=now)


def _reply_delivery_terminal_if_dispatched(
    submission: ProviderSubmission,
    *,
    now: str,
) -> ProviderPollResult | None:
    if not bool(submission.runtime_state.get("reply_delivery_complete_on_dispatch", False)):
        return None
    if not bool(submission.runtime_state.get("prompt_sent", False)):
        return None
    provider_turn_ref = str(
        submission.runtime_state.get("request_anchor")
        or submission.runtime_state.get("pane_id")
        or submission.job_id
    ).strip()
    decision = CompletionDecision(
        terminal=True,
        status=CompletionStatus.COMPLETED,
        reason="reply_delivery_sent",
        confidence=CompletionConfidence.OBSERVED,
        reply="",
        anchor_seen=True,
        reply_started=False,
        reply_stable=True,
        provider_turn_ref=provider_turn_ref or submission.job_id,
        source_cursor=None,
        finished_at=now,
        diagnostics={
            "reply_delivery": True,
            "delivery_status": "sent",
            "provider": submission.provider,
            "submission_mode": "active",
        },
    )
    return ProviderPollResult(submission=submission, decision=decision)


def _ready_wait_timed_out(submission: ProviderSubmission, *, now: str) -> bool:
    started_at = str(submission.runtime_state.get("ready_wait_started_at") or "").strip()
    if not started_at:
        return True
    try:
        timeout_s = float(submission.runtime_state.get("ready_timeout_s", 8.0))
    except Exception:
        timeout_s = 8.0
    try:
        elapsed = (parse_utc_timestamp(now) - parse_utc_timestamp(started_at)).total_seconds()
    except Exception:
        return True
    return elapsed >= max(0.0, timeout_s)


def _ensure_prepared_pane_alive(submission: ProviderSubmission, *, prepared, now: str):
    pane_dead_result = ensure_active_pane_alive(
        submission,
        backend=prepared.backend,
        pane_id=prepared.pane_id,
        now=now,
    )
    if pane_dead_result is not None:
        return pane_dead_result
    return None


def _poll_event_batches(
    submission: ProviderSubmission,
    reader,
    poll,
    *,
    state: dict,
    now: str,
):
    while True:
        batch = _read_event_batch(submission, reader, poll, state=state, now=now)
        if isinstance(batch, ProviderPollResult):
            return batch
        state, has_events = batch
        if not has_events or poll.reached_turn_boundary:
            return state


def _read_event_batch(
    submission: ProviderSubmission,
    reader,
    poll,
    *,
    state: dict,
    now: str,
):
    events, state = read_events(reader, state)
    apply_session_rotation(
        submission,
        poll,
        new_session_path=state_session_path(state),
        now=now,
    )
    if not events:
        return state, False
    event_result = _process_events(submission, poll, events, state=state, now=now)
    if event_result is not None:
        return event_result
    return state, True


def _process_events(
    submission: ProviderSubmission,
    poll,
    events: list[dict],
    *,
    state: dict,
    now: str,
) -> ProviderPollResult | None:
    for event in events:
        result = _process_event(submission, poll, event, state=state, now=now)
        if result is not None:
            return result
        if poll.reached_turn_boundary:
            break
    return None


def _process_event(
    submission: ProviderSubmission,
    poll,
    event: dict,
    *,
    state: dict,
    now: str,
) -> ProviderPollResult | None:
    role = str(event.get("role") or "")
    if role == "prompt_lifecycle":
        handle_prompt_lifecycle_event(submission, poll, event, now=now)
        return None
    if role == "user":
        if is_top_level_user_prompt(event):
            handle_user_event(submission, poll, text=str(event.get("text") or ""), now=now)
        return None
    if role == "system" and poll.anchor_seen:
        return handle_system_event(submission, poll, event, now=now, state=state)
    if role == "assistant" and poll.anchor_seen:
        handle_assistant_event(submission, poll, event, now=now)
    return None


__all__ = [
    "is_turn_boundary_event",
    "poll_exact_hook",
    "poll_submission",
    "read_events",
    "terminal_api_error_payload",
]
