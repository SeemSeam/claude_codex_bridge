from __future__ import annotations

from dataclasses import replace

from completion.models import CompletionItemKind
from completion.models import CompletionConfidence, CompletionDecision, CompletionStatus
from ccbd.system import parse_utc_timestamp
from provider_execution.active import ensure_active_pane_alive, prepare_active_poll_without_liveness
from provider_execution.base import ProviderPollResult, ProviderSubmission
from provider_execution.common import build_item, request_anchor_from_runtime_state

from .event_reading import is_turn_boundary_event, read_events, terminal_api_error_payload
from .hook_results import poll_exact_hook
from .state_machine import (
    apply_session_rotation,
    build_poll_state,
    finalize_poll_result,
    handle_assistant_event,
    handle_system_event,
    handle_user_event,
)
from .start import looks_ready, send_prompt, state_session_path

# Pane content banners that indicate a terminal provider-side failure for
# claude.  Claude has no dedicated pane parser in lib/provider_pane_status
# today, so these are tail-content keyword patterns mirroring codex_pane's
# markers.
#
# Two tiers (mirrors codex_pane's strict/broad split):
#
# _CLAUDE_*_MARKERS         : broad markers, DIAGNOSTICS-only (never drive
#                             terminalization).
# _CLAUDE_HIGH_CONFIDENCE_* : specific multi-word banners. The ONLY tier that
#                             may drive AUTO-TERMINALIZATION via
#                             _claude_provider_signal_terminal_result. Generic
#                             words ("usage limit", "quota", "rate limit",
#                             "api error", "overloaded", "unauthorized",
#                             "x-api-key", ...) are dropped here because a
#                             healthy agent researching those topics
#                             legitimately prints them in its output.
_CLAUDE_USAGE_LIMIT_MARKERS = (
    "usage limit",
    "hit your usage limit",
    "out of credits",
    "purchase more credits",
    "quota exceeded",
    "plan limit",
    "rate spend limit",
)
_CLAUDE_AUTH_FAILED_MARKERS = (
    "invalid api key",
    "incorrect api key",
    "authentication failed",
    "401 unauthorized",
    "unauthorized",
    "api key is invalid",
    "x-api-key",
)
_CLAUDE_API_ERROR_MARKERS = (
    "rate limit",
    "rate_limit",
    "too many requests",
    "overloaded",
    "api error",
    "internal server error",
    "model unavailable",
    "service unavailable",
    "bad gateway",
    "connection reset",
    "connection timed out",
    "request timed out",
)
_CLAUDE_CONFIG_ERROR_MARKERS = (
    "invalid config",
    "invalid configuration",
    "failed to parse config",
    "unknown model provider",
    "configuration error",
)

# High-confidence banners: only these may terminalize a claude job.
_CLAUDE_HIGH_CONFIDENCE_USAGE_LIMIT_MARKERS = (
    "hit your usage limit",
    "out of credits",
    "purchase more credits",
    "quota exceeded",
    "rate spend limit",
)
_CLAUDE_HIGH_CONFIDENCE_AUTH_FAILED_MARKERS = (
    "invalid api key",
    "incorrect api key",
    "authentication failed",
    "401 unauthorized",
    "api key is invalid",
)
_CLAUDE_HIGH_CONFIDENCE_API_ERROR_MARKERS = (
    "internal server error",
    "bad gateway",
    "service unavailable",
    "connection reset",
    "connection timed out",
    "request timed out",
)
_CLAUDE_HIGH_CONFIDENCE_CONFIG_ERROR_MARKERS = (
    "failed to parse config",
    "invalid configuration",
    "unknown model provider",
)

# broad -> high-confidence mapping.
_CLAUDE_HIGH_CONFIDENCE_MARKERS = {
    _CLAUDE_USAGE_LIMIT_MARKERS: _CLAUDE_HIGH_CONFIDENCE_USAGE_LIMIT_MARKERS,
    _CLAUDE_AUTH_FAILED_MARKERS: _CLAUDE_HIGH_CONFIDENCE_AUTH_FAILED_MARKERS,
    _CLAUDE_API_ERROR_MARKERS: _CLAUDE_HIGH_CONFIDENCE_API_ERROR_MARKERS,
    _CLAUDE_CONFIG_ERROR_MARKERS: _CLAUDE_HIGH_CONFIDENCE_CONFIG_ERROR_MARKERS,
}

_CLAUDE_PANE_SIGNAL_TO_REASON = {
    "usage_limit": "provider_usage_limit",
    "auth_failed": "provider_auth_failed",
    "api_error": "provider_api_error",
    "config_error": "provider_config_error",
}


def poll_submission(
    adapter,
    submission: ProviderSubmission,
    *,
    now: str,
) -> ProviderPollResult | None:
    del adapter
    # Terminalize early when the claude pane shows a classified provider-side
    # error banner (usage-limit / auth / api / config).  Mirrors codex's
    # _codex_provider_signal_terminal_result; checked before any delivery or
    # event-read work so the job fails fast with an attributable no_reply_reason
    # instead of draining the ready/delivery timeouts.
    provider_signal = _claude_provider_signal_terminal_result(submission, now=now)
    if provider_signal is not None:
        return provider_signal
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
        dispatch_items = _prompt_dispatch_items(submission, now=now)
    reply_delivery_terminal = _reply_delivery_terminal_if_dispatched(submission, now=now)
    if reply_delivery_terminal is not None:
        return _merge_poll_result_items(reply_delivery_terminal, prefix_items=dispatch_items)
    hook_result = poll_exact_hook(submission, now=now)
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
    return _merge_poll_result_items(
        finalize_poll_result(submission, poll, state=state),
        prefix_items=dispatch_items,
    )


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
    next_seq = int(submission.runtime_state.get("next_seq", 1))
    anchor_seen = bool(submission.runtime_state.get("anchor_seen", False))
    deferred_for_ready = bool(submission.runtime_state.get("prompt_deferred_for_ready", False))
    anchor_emitted = deferred_for_ready and not anchor_seen
    updated = replace(
        submission,
        runtime_state={
            **submission.runtime_state,
            "prompt_sent": True,
            "prompt_sent_at": now,
            "anchor_seen": anchor_seen or anchor_emitted,
            "next_seq": next_seq + (1 if anchor_emitted else 0),
            "prompt_deferred_for_ready": False,
            "prompt_anchor_emitted_at": now if anchor_emitted else "",
        },
    )
    return updated


def _prompt_dispatch_items(submission: ProviderSubmission, *, now: str) -> tuple:
    if not bool(submission.runtime_state.get("prompt_sent", False)):
        return ()
    emitted_at = str(submission.runtime_state.get("prompt_anchor_emitted_at") or "").strip()
    if emitted_at != now:
        return ()
    prior_seq = int(submission.runtime_state.get("next_seq", 1)) - 1
    if prior_seq < 1:
        prior_seq = 1
    request_anchor = request_anchor_from_runtime_state(submission.runtime_state, fallback=submission.job_id)
    session_path = str(submission.runtime_state.get("session_path") or "").strip() or None
    return (
        build_item(
            submission,
            kind=CompletionItemKind.ANCHOR_SEEN,
            timestamp=now,
            seq=prior_seq,
            payload={"turn_id": request_anchor, "session_path": session_path},
            cursor_kwargs={"session_path": session_path},
        ),
    )


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
    if role == "user":
        handle_user_event(submission, poll, text=str(event.get("text") or ""), now=now)
        return None
    if role == "system":
        return handle_system_event(submission, poll, event, now=now, state=state)
    if role == "assistant" and poll.anchor_seen:
        handle_assistant_event(submission, poll, event, now=now)
    return None


def _claude_provider_signal_terminal_result(
    submission: ProviderSubmission,
    *,
    now: str,
) -> ProviderPollResult | None:
    """Terminalize early when the claude pane shows a HIGH-CONFIDENCE provider error.

    Mirrors codex's _codex_provider_signal_terminal_result and uses the strict
    / high-confidence marker tier only: a healthy agent whose output merely
    *discusses* usage limits / quotas / api errors must NEVER be terminalized.
    Broad markers stay available for diagnostics.
    """
    runtime_state = submission.runtime_state or {}
    backend = runtime_state.get("backend")
    pane_id = str(runtime_state.get("pane_id") or "").strip()
    if not backend or not pane_id:
        return None
    signal = _read_claude_pane_signal(backend, pane_id, strict=True)
    if signal is None:
        return None
    no_reply_reason = _CLAUDE_PANE_SIGNAL_TO_REASON.get(str(signal.get("pane_signal_state") or ""))
    if no_reply_reason is None:
        return None
    request_anchor = request_anchor_from_runtime_state(runtime_state, fallback=submission.job_id)
    diagnostics = {
        **dict(submission.diagnostics or {}),
        "reason": no_reply_reason,
        "pane_signal_state": signal.get("pane_signal_state"),
        "pane_signal_reason": signal.get("pane_signal_reason"),
        "no_reply_reason": no_reply_reason,
        "no_reply_detail": {
            "pane_signal_state": signal.get("pane_signal_state"),
            "matched_markers": signal.get("matched_markers"),
        },
    }
    if signal.get("pane_tail"):
        diagnostics["pane_tail"] = signal.get("pane_tail")
    item = build_item(
        submission,
        kind=CompletionItemKind.ERROR,
        timestamp=now,
        seq=int(runtime_state.get("next_seq", 1)),
        payload={"reason": no_reply_reason, "error_kind": no_reply_reason},
    )
    updated_state = {
        **runtime_state,
        "mode": "passive",
        "next_seq": item.cursor.event_seq + 1,
    }
    updated = replace(
        submission,
        runtime_state=updated_state,
        diagnostics=diagnostics,
    )
    return ProviderPollResult(
        submission=updated,
        items=(item,),
        decision=CompletionDecision(
            terminal=True,
            status=CompletionStatus.FAILED,
            reason=no_reply_reason,
            confidence=CompletionConfidence.DEGRADED,
            reply="",
            anchor_seen=False,
            reply_started=False,
            reply_stable=False,
            provider_turn_ref=request_anchor or submission.job_id,
            source_cursor=item.cursor,
            finished_at=now,
            diagnostics=diagnostics,
        ),
    )


def _read_claude_pane_signal(
    backend: object,
    pane_id: str,
    *,
    strict: bool = False,
) -> dict[str, object] | None:
    """Scan pane tail content for claude provider-error banners.

    Returns a fragment ({pane_signal_state, pane_signal_reason, pane_tail,
    matched_markers}) when a classified banner is present, else None.  Order
    matches codex_pane: usage_limit > auth_failed > config_error > api_error so
    the most specific terminal signal wins on overlapping text.

    ``strict`` selects the marker tier (mirrors codex_pane strict mode):
    ``strict=False`` uses broad markers (diagnostics), ``strict=True`` uses
    high-confidence markers only (auto-terminalization).
    """
    get_pane_content = getattr(backend, "get_pane_content", None)
    if not callable(get_pane_content):
        return None
    try:
        content = str(get_pane_content(pane_id, lines=120) or "")
    except Exception:
        return None
    if not content.strip():
        return None
    normalized = content.replace("\r\n", "\n").replace("\r", "\n")
    recent_lines = [line.rstrip() for line in normalized.splitlines() if line.strip()]
    if not recent_lines:
        return None
    recent = " ".join(line.strip() for line in recent_lines[-20:]).lower()
    tail = "\n".join(recent_lines[-20:])

    for state_key, markers in (
        ("usage_limit", _CLAUDE_USAGE_LIMIT_MARKERS),
        ("auth_failed", _CLAUDE_AUTH_FAILED_MARKERS),
        ("config_error", _CLAUDE_CONFIG_ERROR_MARKERS),
        ("api_error", _CLAUDE_API_ERROR_MARKERS),
    ):
        if strict:
            markers = _CLAUDE_HIGH_CONFIDENCE_MARKERS.get(markers, markers)
        matched = tuple(marker for marker in markers if marker in recent)
        if matched:
            return {
                "pane_signal_state": state_key,
                "pane_signal_reason": _CLAUDE_PANE_SIGNAL_TO_REASON.get(state_key, state_key),
                "pane_tail": tail,
                "matched_markers": matched,
            }
    return None


__all__ = [
    "is_turn_boundary_event",
    "poll_exact_hook",
    "poll_submission",
    "read_events",
    "terminal_api_error_payload",
]
