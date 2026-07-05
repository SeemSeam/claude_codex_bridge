from __future__ import annotations

from completion.models import CompletionDecision

from ccbd.no_reply_reason import NoReplyReason, resolve_no_reply_reason

from ..quota_buckets import USAGE_LIMIT_ERROR_KIND, bucket_key
from ..records import get_job
from ..reply_delivery import prepare_reply_deliveries, resolve_reply_delivery_terminal
from .message_bureau import record_message_bureau_completion
from .persistence import persist_terminal_completion


def complete_job(dispatcher, job_id: str, decision: CompletionDecision):
    if not decision.terminal:
        raise dispatcher._dispatch_error('complete requires a terminal completion decision')
    current = get_job(dispatcher, job_id)
    if current is None:
        raise dispatcher._dispatch_error(f'unknown job: {job_id}')
    if current.status in dispatcher._terminal_event_by_status:
        return current

    no_reply_reason: str | None = None
    no_reply_detail: dict | None = None
    resolved = resolve_no_reply_reason(decision)
    if resolved is not None:
        reason_value, detail = resolved
        no_reply_reason = reason_value.value
        no_reply_detail = dict(detail)
        decision = _decision_with_no_reply(decision, no_reply_reason, no_reply_detail)

    finished_at = decision.finished_at or dispatcher._clock()
    terminal, terminal_decision, prior_snapshot = persist_terminal_completion(
        dispatcher,
        current,
        decision,
        finished_at=finished_at,
        no_reply_reason=no_reply_reason,
        no_reply_detail=no_reply_detail,
    )
    _maybe_mark_quota_bucket_degraded(dispatcher, current, terminal_decision)
    terminal, _reply_decision, retry_scheduled = record_message_bureau_completion(
        dispatcher,
        current,
        terminal,
        terminal_decision,
        finished_at=finished_at,
        prior_snapshot=prior_snapshot,
    )
    resolve_reply_delivery_terminal(dispatcher, terminal, finished_at=finished_at)
    if retry_scheduled:
        return terminal
    if bool(getattr(dispatcher, '_auto_reply_delivery_on_complete', False)):
        prepare_reply_deliveries(dispatcher)
    return terminal


def _maybe_mark_quota_bucket_degraded(dispatcher, job, decision: CompletionDecision) -> None:
    """If a job terminalizes with provider_usage_limit + retry_after, degrade its bucket."""
    if job.target_kind.value != 'agent':
        return
    diagnostics = dict(decision.diagnostics or {})
    error_kind = str(diagnostics.get('error_kind') or '').strip().lower()
    if error_kind != USAGE_LIMIT_ERROR_KIND:
        return
    retry_after = diagnostics.get('retry_after')
    if not retry_after:
        return
    quota_buckets = dispatcher._quota_buckets
    if quota_buckets is None:
        return
    try:
        spec = dispatcher._registry.spec_for(job.agent_name)
    except Exception:
        return
    if spec is None:
        return
    key = bucket_key(spec.provider, spec.model, getattr(spec, 'account', None))
    quota_buckets.mark_degraded(key, str(retry_after), reason=error_kind)


def _decision_with_no_reply(
    decision: CompletionDecision,
    no_reply_reason: str,
    no_reply_detail: dict,
) -> CompletionDecision:
    diagnostics = dict(decision.diagnostics or {})
    diagnostics['no_reply_reason'] = no_reply_reason
    diagnostics['no_reply_detail'] = dict(no_reply_detail)
    return decision.__class__(
        terminal=decision.terminal,
        status=decision.status,
        reason=decision.reason,
        confidence=decision.confidence,
        reply=decision.reply,
        anchor_seen=decision.anchor_seen,
        reply_started=decision.reply_started,
        reply_stable=decision.reply_stable,
        provider_turn_ref=decision.provider_turn_ref,
        source_cursor=decision.source_cursor,
        finished_at=decision.finished_at,
        diagnostics=diagnostics,
    )


__all__ = ['complete_job']
