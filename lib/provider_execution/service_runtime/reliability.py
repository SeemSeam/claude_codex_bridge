from __future__ import annotations

from dataclasses import replace
from datetime import timedelta

from ccbd.system import parse_utc_timestamp
from completion.models import CompletionDecision, CompletionItemKind
from provider_execution.base import ProviderPollResult, ProviderSubmission
from provider_execution.common import request_anchor_from_runtime_state
from provider_execution.reliability import adapter_reliability_policy

_SEMANTIC_PROGRESS_ITEM_KINDS = frozenset(
    {
        CompletionItemKind.ANCHOR_SEEN,
        CompletionItemKind.ASSISTANT_CHUNK,
        CompletionItemKind.ASSISTANT_FINAL,
        CompletionItemKind.TOOL_CALL,
        CompletionItemKind.TOOL_RESULT,
        CompletionItemKind.RESULT,
        CompletionItemKind.TURN_BOUNDARY,
        CompletionItemKind.TURN_ABORTED,
        CompletionItemKind.CANCEL_INFO,
        CompletionItemKind.ERROR,
        CompletionItemKind.PANE_DEAD,
    }
)

_TIMEOUT_RECOVERY_ACTION_BY_CLASS = {
    'anchor_missing_no_reply': 'resend_after_delivery_or_session_binding_check',
    'reply_captured_anchor_missing': 'deliver_degraded_reply_then_check_anchor_binding',
    'anchor_seen_no_reply': 'inspect_provider_pane_or_waiting_user',
    'reply_captured_no_terminal': 'deliver_degraded_reply_then_repair_completion_log',
}


def apply_reliability_progress(
    result: ProviderPollResult,
    *,
    previous_submission: ProviderSubmission,
    now: str,
) -> ProviderPollResult:
    if not has_reliability_progress(result, previous_submission=previous_submission):
        return result
    updated_submission = with_last_progress_at(result.submission, at=now)
    if updated_submission == result.submission:
        return result
    return ProviderPollResult(
        submission=updated_submission,
        items=result.items,
        decision=result.decision,
    )


def has_reliability_progress(
    result: ProviderPollResult,
    *,
    previous_submission: ProviderSubmission,
) -> bool:
    return bool(
        has_semantic_progress_item(result)
        or result.decision is not None
        or semantic_progress_marker(result.submission)
        != semantic_progress_marker(previous_submission)
    )


def has_semantic_progress_item(result: ProviderPollResult) -> bool:
    return any(item.kind in _SEMANTIC_PROGRESS_ITEM_KINDS for item in result.items)


def semantic_progress_marker(submission: ProviderSubmission) -> tuple[object, ...]:
    runtime_state = dict(submission.runtime_state)
    return (
        submission.reply,
        submission.status,
        submission.reason,
        submission.confidence,
        bool(runtime_state.get('anchor_seen') or runtime_state.get('anchor_emitted')),
        str(runtime_state.get('bound_turn_id') or ''),
        str(runtime_state.get('bound_task_id') or ''),
        str(runtime_state.get('reply_buffer') or ''),
        str(runtime_state.get('last_agent_message') or ''),
        str(runtime_state.get('last_final_answer') or ''),
        str(runtime_state.get('last_assistant_message') or ''),
        str(runtime_state.get('last_assistant_signature') or ''),
        str(runtime_state.get('session_path') or ''),
    )


def with_last_progress_at(submission: ProviderSubmission, *, at: str) -> ProviderSubmission:
    runtime_state = dict(submission.runtime_state)
    if str(runtime_state.get('reliability_last_progress_at') or '').strip() == at:
        return submission
    runtime_state['reliability_last_progress_at'] = at
    return replace(submission, runtime_state=runtime_state)


def timeout_poll_result(
    service,
    *,
    job_id: str,
    submission: ProviderSubmission,
    adapter,
    now: str,
) -> ProviderPollResult | None:
    policy = timeout_policy_for(service, job_id=job_id, adapter=adapter)
    if policy is None:
        return None
    timeout_s = policy.effective_no_terminal_timeout_s()
    if timeout_s <= 0:
        return None
    last_progress_at = last_progress_timestamp(submission)
    if not timeout_elapsed(last_progress_at, now=now, timeout_s=timeout_s):
        return None
    return build_timeout_result(
        submission,
        now=now,
        timeout_s=timeout_s,
        last_progress_at=last_progress_at,
        policy=policy,
    )


def timeout_policy_for(service, *, job_id: str, adapter) -> object | None:
    policy = adapter_reliability_policy(adapter)
    if policy is None:
        return None
    runtime_context = service._runtime_contexts.get(job_id)
    backend_type = str(getattr(runtime_context, 'backend_type', '') or '').strip().lower() or None
    if policy.backend_type and backend_type != policy.backend_type:
        return None
    return policy


def last_progress_timestamp(submission: ProviderSubmission) -> str:
    runtime_state = dict(submission.runtime_state)
    for key in ('reliability_last_progress_at',):
        value = str(runtime_state.get(key) or '').strip()
        if value:
            return value
    return str(submission.ready_at or submission.accepted_at or '').strip()


def timeout_elapsed(started_at: str, *, now: str, timeout_s: float) -> bool:
    if not started_at:
        return False
    try:
        elapsed = (parse_utc_timestamp(now) - parse_utc_timestamp(started_at)).total_seconds()
    except Exception:
        return False
    return elapsed >= max(0.0, timeout_s)


def build_timeout_result(
    submission: ProviderSubmission,
    *,
    now: str,
    timeout_s: float,
    last_progress_at: str,
    policy,
) -> ProviderPollResult:
    reply, reply_source = timeout_reply(submission)
    request_anchor = request_anchor_from_runtime_state(submission.runtime_state, fallback=submission.job_id)
    timeout_detail = timeout_no_reply_detail(
        submission,
        reply=reply,
        reply_source=reply_source,
        last_progress_at=last_progress_at,
        timeout_s=timeout_s,
    )
    diagnostics = {
        **dict(submission.diagnostics or {}),
        'completion_primary_authority': policy.primary_authority,
        'completion_last_progress_at': last_progress_at,
        'completion_timeout_s': timeout_s,
        'completion_timeout_deadline_at': deadline_at(last_progress_at, timeout_s=timeout_s),
        'completion_reliability_reason': policy.timeout_reason,
        'completion_fallback_source': 'execution_reliability_monitor',
        'completion_timeout_class': timeout_detail['completion_timeout_class'],
        'completion_recovery_action': timeout_detail['recovery_action'],
        'no_reply_reason': 'completion_detection_gap',
        'no_reply_detail': timeout_detail,
    }
    if reply_source:
        diagnostics['completion_reply_source'] = reply_source
    runtime_state = {
        **dict(submission.runtime_state),
        'reliability_last_progress_at': last_progress_at,
        'reliability_timeout_s': timeout_s,
        'reliability_timeout_deadline_at': diagnostics['completion_timeout_deadline_at'] or '',
        'reliability_terminalized_at': now,
        'reliability_timeout_class': timeout_detail['completion_timeout_class'],
        'reliability_recovery_action': timeout_detail['recovery_action'],
    }
    updated_submission = replace(
        submission,
        reply=reply,
        status=policy.timeout_status,
        reason=policy.timeout_reason,
        confidence=policy.timeout_confidence,
        diagnostics=diagnostics,
        runtime_state=runtime_state,
    )
    decision = CompletionDecision(
        terminal=True,
        status=policy.timeout_status,
        reason=policy.timeout_reason,
        confidence=policy.timeout_confidence,
        reply=reply,
        anchor_seen=bool(
            submission.runtime_state.get('anchor_seen')
            or submission.runtime_state.get('anchor_emitted')
        ),
        reply_started=bool(reply),
        reply_stable=bool(reply),
        provider_turn_ref=request_anchor or submission.job_id,
        source_cursor=None,
        finished_at=now,
        diagnostics=diagnostics,
    )
    return ProviderPollResult(submission=updated_submission, decision=decision)


def timeout_reply(submission: ProviderSubmission) -> tuple[str, str]:
    reply = str(submission.reply or '')
    if reply:
        return reply, 'submission_reply'
    runtime_state = dict(submission.runtime_state)
    for key in ('last_agent_message', 'last_final_answer', 'last_assistant_message', 'reply_buffer'):
        value = str(runtime_state.get(key) or '').strip()
        if value:
            return value, key
    return '', ''


def timeout_no_reply_detail(
    submission: ProviderSubmission,
    *,
    reply: str,
    reply_source: str,
    last_progress_at: str,
    timeout_s: float,
) -> dict[str, object]:
    runtime_state = dict(submission.runtime_state)
    anchor_seen = bool(runtime_state.get('anchor_seen') or runtime_state.get('anchor_emitted'))
    reply_started = bool(reply)
    if anchor_seen and reply_started:
        timeout_class = 'reply_captured_no_terminal'
    elif anchor_seen:
        timeout_class = 'anchor_seen_no_reply'
    elif reply_started:
        timeout_class = 'reply_captured_anchor_missing'
    else:
        timeout_class = 'anchor_missing_no_reply'

    detail: dict[str, object] = {
        'source': 'completion_timeout',
        'completion_timeout_class': timeout_class,
        'anchor_seen': anchor_seen,
        'anchor_not_required': bool(runtime_state.get('no_wrap')),
        'reply_started': reply_started,
        'reply_chars': len(reply),
        'reply_source': reply_source,
        'last_progress_at': last_progress_at,
        'timeout_s': timeout_s,
        'recovery_action': _TIMEOUT_RECOVERY_ACTION_BY_CLASS[timeout_class],
    }
    for key in ('bound_turn_id', 'bound_task_id', 'session_path'):
        value = str(runtime_state.get(key) or '').strip()
        if value:
            detail[key] = value
    return detail


def deadline_at(started_at: str, *, timeout_s: float) -> str | None:
    if not started_at:
        return None
    try:
        deadline = parse_utc_timestamp(started_at) + timedelta(seconds=max(0.0, timeout_s))
    except Exception:
        return None
    return deadline.isoformat().replace('+00:00', 'Z')


__all__ = [
    'apply_reliability_progress',
    'build_timeout_result',
    'deadline_at',
    'has_reliability_progress',
    'has_semantic_progress_item',
    'last_progress_timestamp',
    'semantic_progress_marker',
    'timeout_elapsed',
    'timeout_no_reply_detail',
    'timeout_poll_result',
    'timeout_reply',
    'timeout_policy_for',
    'with_last_progress_at',
]
