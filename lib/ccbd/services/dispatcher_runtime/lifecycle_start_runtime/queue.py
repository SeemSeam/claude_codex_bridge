from __future__ import annotations

from dataclasses import replace

from agents.models import AgentState
from ccbd.api_models import TargetKind
from completion.models import CompletionConfidence, CompletionDecision, CompletionStatus

from ..quota_buckets import bucket_key
from ..records import append_job, get_job
from ..reply_delivery import claim_reply_delivery_start, claimable_reply_delivery_job_ids
from .models import QueuedTargetSlot
from .recovery import refresh_slot_runtime_for_start
from .start import start_running_job

_DEGRADED_SKIP_GRACE_SECS = 8.0
_DISPATCH_HANDOFF_FAILED_GRACE_SECS = 8.0
# A queued job is tagged agent_busy_queue_blocked when its agent is BUSY on a
# prior job whose last_seen_at is older than this.  30s distinguishes "agent is
# actively working the prior job" (heartbeat advances every few seconds) from
# "prior job is stuck and blocking the queue".  Observability only; the job is
# NOT terminalized -- the sender just needs to see why its ask is queued.
_AGENT_BUSY_QUEUE_BLOCKED_STALE_SECS = 30.0
_NO_REPLY_SKIPPED_SINCE = '_no_reply_skipped_since'
_NO_REPLY_HANDOFF_NONE_SINCE = '_no_reply_handoff_none_since'
_NO_REPLY_REASON = '_no_reply_reason'
_AGENT_BUSY_BLOCKED_TAGGED_AT = '_agent_busy_queue_blocked_tagged_at'


def start_next_queued_job(dispatcher, slot: QueuedTargetSlot):
    refreshed = refresh_slot_runtime_for_start(dispatcher, slot)
    if refreshed is None:
        return _maybe_terminalize_handoff_failed_for_missing_runtime(dispatcher, slot)
    slot = refreshed
    if dispatcher._message_bureau is not None and slot.target_kind is TargetKind.AGENT:
        return _start_agent_mailbox_job(dispatcher, slot)
    return _start_next_queued_job_from_state(dispatcher, slot)


def _start_next_queued_job_from_state(dispatcher, slot: QueuedTargetSlot):
    for job_id in dispatcher._state.queued_items_for(slot.target_kind, slot.target_name):
        current = get_job(dispatcher, job_id)
        if current is None:
            dispatcher._state.remove_queued_for(slot.target_kind, slot.target_name, job_id)
            continue
        degraded, bucket_key, bucket_reason = _bucket_degradation_info(dispatcher, current)
        if degraded:
            terminal = _maybe_terminalize_degraded_skip(
                dispatcher,
                current,
                slot,
                bucket_key=bucket_key,
                bucket_reason=bucket_reason,
            )
            if terminal is not None:
                return None
            continue
        _clear_no_reply_timestamp(dispatcher, current)
        dispatcher._state.remove_queued_for(slot.target_kind, slot.target_name, job_id)
        return start_running_job(dispatcher, current, slot=slot)
    _tag_queued_jobs_busy_blocked(dispatcher, slot)
    return None


def _start_agent_mailbox_job(dispatcher, slot: QueuedTargetSlot):
    queued_ids = set(dispatcher._state.queued_items_for(slot.target_kind, slot.target_name))
    reply_delivery = _claim_reply_delivery(dispatcher, slot, queued_ids)
    if reply_delivery is not None:
        return reply_delivery
    job_id = _claim_request_job_id(dispatcher, slot, queued_ids)
    if job_id is None:
        _tag_queued_jobs_busy_blocked(dispatcher, slot)
        return None
    current = get_job(dispatcher, job_id)
    if current is None:
        return None
    return start_running_job(dispatcher, current, slot=slot)


def _claim_reply_delivery(dispatcher, slot: QueuedTargetSlot, queued_ids: set[str]):
    for candidate in claimable_reply_delivery_job_ids(dispatcher, slot.target_name):
        if candidate not in queued_ids:
            continue
        current = get_job(dispatcher, candidate)
        if current is None:
            dispatcher._state.remove_queued_for(slot.target_kind, slot.target_name, candidate)
            continue
        started_at = dispatcher._clock()
        if not claim_reply_delivery_start(dispatcher, current, started_at=started_at):
            continue
        dispatcher._state.remove_queued_for(slot.target_kind, slot.target_name, candidate)
        return start_running_job(dispatcher, current, slot=slot, started_at=started_at)
    return None


def _claim_request_job_id(dispatcher, slot: QueuedTargetSlot, queued_ids: set[str]) -> str | None:
    for candidate in dispatcher._message_bureau.claimable_request_job_ids(slot.target_name):
        if candidate not in queued_ids:
            continue
        current = get_job(dispatcher, candidate)
        if current is None:
            dispatcher._state.remove_queued_for(slot.target_kind, slot.target_name, candidate)
            continue
        degraded, bucket_key, bucket_reason = _bucket_degradation_info(dispatcher, current)
        if degraded:
            terminal = _maybe_terminalize_degraded_skip(
                dispatcher,
                current,
                slot,
                bucket_key=bucket_key,
                bucket_reason=bucket_reason,
            )
            if terminal is not None:
                return None
            continue
        _clear_no_reply_timestamp(dispatcher, current)
        dispatcher._state.remove_queued_for(slot.target_kind, slot.target_name, candidate)
        return candidate
    return None



def _is_job_bucket_degraded(dispatcher, job) -> bool:
    degraded, _, _ = _bucket_degradation_info(dispatcher, job)
    return degraded


def _bucket_degradation_info(dispatcher, job) -> tuple[bool, str | None, str | None]:
    if job.target_kind is not TargetKind.AGENT:
        return False, None, None
    quota_buckets = dispatcher._quota_buckets
    if quota_buckets is None:
        return False, None, None
    try:
        spec = dispatcher._registry.spec_for(job.agent_name)
    except Exception:
        return False, None, None
    if spec is None:
        return False, None, None
    key = bucket_key(spec.provider, spec.model, getattr(spec, 'account', None))
    if not quota_buckets.is_degraded(key):
        return False, key, None
    return True, key, quota_buckets.degraded_reason(key)


def _maybe_terminalize_degraded_skip(
    dispatcher,
    current,
    slot: QueuedTargetSlot,
    *,
    bucket_key: str | None,
    bucket_reason: str | None,
):
    now = dispatcher._clock()
    skipped_since = _ensure_no_reply_timestamp(dispatcher, current, now)
    elapsed = _seconds_between(skipped_since, now)
    if elapsed < _DEGRADED_SKIP_GRACE_SECS:
        return None
    bucket_type = bucket_reason or 'provider_usage_limit'
    return _terminalize_queued_job(
        dispatcher,
        current,
        slot,
        status=CompletionStatus.INCOMPLETE,
        reason='dispatch_handoff_skipped_degraded',
        no_reply_reason='dispatch_handoff_skipped_degraded',
        no_reply_detail={
            'bucket_key': bucket_key,
            'bucket_type': bucket_type,
            'bucket_reason': bucket_reason,
            'grace_secs': _DEGRADED_SKIP_GRACE_SECS,
            'skipped_since': skipped_since,
            'elapsed_secs': elapsed,
        },
        finished_at=now,
    )


def _maybe_terminalize_handoff_failed_for_missing_runtime(dispatcher, slot: QueuedTargetSlot):
    """Terminalize the front queued job when the runtime cannot be readied.

    This catches the non-degraded handoff path where the dispatcher has a
    queued job but no runnable runtime binding (``delivery_state`` stays None
    because execution never receives a context).  A short grace period keeps
    transient runtime-recovery failures from burning the job immediately.
    """
    if slot.target_kind is not TargetKind.AGENT:
        return None
    for job_id in dispatcher._state.queued_items_for(slot.target_kind, slot.target_name):
        current = get_job(dispatcher, job_id)
        if current is None:
            dispatcher._state.remove_queued_for(slot.target_kind, slot.target_name, job_id)
            continue
        degraded, _, _ = _bucket_degradation_info(dispatcher, current)
        if degraded:
            return None
        now = dispatcher._clock()
        stalled_since = _ensure_handoff_none_timestamp(dispatcher, current, now)
        elapsed = _seconds_between(stalled_since, now)
        if elapsed < _DISPATCH_HANDOFF_FAILED_GRACE_SECS:
            return None
        return _terminalize_queued_job(
            dispatcher,
            current,
            slot,
            status=CompletionStatus.INCOMPLETE,
            reason='dispatch_handoff_failed',
            no_reply_reason='dispatch_handoff_failed',
            no_reply_detail={
                'grace_secs': _DISPATCH_HANDOFF_FAILED_GRACE_SECS,
                'stalled_since': stalled_since,
                'elapsed_secs': elapsed,
                'runtime_ref': str(getattr(slot, 'runtime', None) or ''),
            },
            finished_at=now,
        )
    return None


def _terminalize_queued_job(
    dispatcher,
    current,
    slot: QueuedTargetSlot,
    *,
    status: CompletionStatus,
    reason: str,
    no_reply_reason: str,
    no_reply_detail: dict[str, object] | None,
    finished_at: str,
):
    dispatcher._state.remove_queued_for(slot.target_kind, slot.target_name, current.job_id)
    decision = CompletionDecision(
        terminal=True,
        status=status,
        reason=reason,
        confidence=CompletionConfidence.DEGRADED,
        reply='',
        anchor_seen=False,
        reply_started=False,
        reply_stable=False,
        provider_turn_ref=current.job_id,
        source_cursor=None,
        finished_at=finished_at,
        diagnostics={
            'no_reply_reason': no_reply_reason,
            'no_reply_detail': dict(no_reply_detail or {}),
        },
    )
    dispatcher.complete(current.job_id, decision)
    return None


def _ensure_no_reply_timestamp(dispatcher, current, now: str) -> str:
    options = dict(current.provider_options or {})
    skipped_since = options.get(_NO_REPLY_SKIPPED_SINCE)
    if skipped_since:
        return str(skipped_since)
    options[_NO_REPLY_SKIPPED_SINCE] = now
    updated = replace(current, provider_options=options)
    append_job(dispatcher, updated)
    return now


def _ensure_handoff_none_timestamp(dispatcher, current, now: str) -> str:
    options = dict(current.provider_options or {})
    stalled_since = options.get(_NO_REPLY_HANDOFF_NONE_SINCE)
    if stalled_since:
        return str(stalled_since)
    options[_NO_REPLY_HANDOFF_NONE_SINCE] = now
    updated = replace(current, provider_options=options)
    append_job(dispatcher, updated)
    return now


def _clear_no_reply_timestamp(dispatcher, current) -> None:
    options = dict(current.provider_options or {})
    changed = False
    if _NO_REPLY_SKIPPED_SINCE in options:
        del options[_NO_REPLY_SKIPPED_SINCE]
        changed = True
    if _NO_REPLY_HANDOFF_NONE_SINCE in options:
        del options[_NO_REPLY_HANDOFF_NONE_SINCE]
        changed = True
    if _NO_REPLY_REASON in options:
        del options[_NO_REPLY_REASON]
        changed = True
    if _AGENT_BUSY_BLOCKED_TAGGED_AT in options:
        del options[_AGENT_BUSY_BLOCKED_TAGGED_AT]
        changed = True
    if changed:
        updated = replace(current, provider_options=options)
        append_job(dispatcher, updated)


def _slot_is_busy_stuck(slot: QueuedTargetSlot, *, now: str) -> bool:
    """An agent slot is 'busy-stuck' when it is BUSY with a stale heartbeat.

    This is the A3 condition: a new job cannot start because the agent is still
    marked BUSY on a prior job whose last_seen_at has not advanced.  We treat
    the queue depth from the slot runtime as the authoritative 'has prior
    work' signal.
    """
    runtime = slot.runtime
    if runtime is None or runtime.state is not AgentState.BUSY:
        return False
    if (runtime.queue_depth or 0) <= 0:
        return False
    last_seen = str(runtime.last_seen_at or '').strip()
    if not last_seen:
        return False
    return _seconds_between(last_seen, now) >= _AGENT_BUSY_QUEUE_BLOCKED_STALE_SECS


def _tag_queued_jobs_busy_blocked(dispatcher, slot: QueuedTargetSlot) -> None:
    """Observability tag: explain WHY the queued jobs are still queued.

    Tags each remaining queued job for this agent with
    no_reply_reason='agent_busy_queue_blocked' (via provider_options) so the
    sender can distinguish 'reached, accepted, but blocked behind a stuck
    prior job' from a silent no-reply.  Idempotent: re-tagging refreshes the
    tagged_at timestamp and detail only.  Does NOT terminalize.
    """
    if slot.target_kind is not TargetKind.AGENT:
        return
    now = dispatcher._clock()
    if not _slot_is_busy_stuck(slot, now=now):
        return
    runtime = slot.runtime
    for job_id in dispatcher._state.queued_items_for(slot.target_kind, slot.target_name):
        current = get_job(dispatcher, job_id)
        if current is None:
            continue
        options = dict(current.provider_options or {})
        prev_tagged_at = str(options.get(_AGENT_BUSY_BLOCKED_TAGGED_AT) or '').strip()
        options[_NO_REPLY_REASON] = 'agent_busy_queue_blocked'
        options[_AGENT_BUSY_BLOCKED_TAGGED_AT] = now
        options['_no_reply_detail'] = {
            'agent_name': slot.target_name,
            'runtime_state': str(runtime.state.value if hasattr(runtime.state, 'value') else runtime.state),
            'queue_depth': int(runtime.queue_depth or 0),
            'last_seen_at': str(runtime.last_seen_at or ''),
            'stale_threshold_secs': _AGENT_BUSY_QUEUE_BLOCKED_STALE_SECS,
            'tagged_at': now,
            'prior_tagged_at': prev_tagged_at or None,
        }
        updated = replace(current, provider_options=options)
        append_job(dispatcher, updated)


def _seconds_between(start: str, end: str) -> float:
    from datetime import datetime

    start_dt = _parse_iso(start)
    end_dt = _parse_iso(end)
    if start_dt is None or end_dt is None:
        return 0.0
    return max(0.0, (end_dt - start_dt).total_seconds())


def _parse_iso(value: str | None):
    from datetime import datetime

    if not value:
        return None
    try:
        text = str(value).strip()
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        return datetime.fromisoformat(text)
    except Exception:
        return None


__all__ = ['start_next_queued_job']
