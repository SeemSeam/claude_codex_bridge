from __future__ import annotations

from datetime import datetime, timezone

from message_bureau.reply_payloads import delivery_job_id_from_payload

from ..records import get_job
from .common import head_reply_event, head_reply_id
from .decisions import reply_delivery_failed_decision
from .preparation_head import reset_stale_reply_head

REPLY_DELIVERY_PENDING_STALL_SECONDS = 300.0


def repair_reply_delivery_heads(dispatcher):
    repaired = []
    for agent_name in dispatcher._config.agents:
        result = repair_agent_reply_delivery_head(dispatcher, agent_name)
        if result is not None:
            repaired.append(result)
    return tuple(repaired)


def repair_agent_reply_delivery_head(dispatcher, agent_name: str):
    head = head_reply_event(dispatcher, agent_name)
    if head is None:
        return None
    reply_id = head_reply_id(head)
    if not reply_id:
        return None

    delivery_job_id = delivery_job_id_from_payload(head.payload_ref)
    if not delivery_job_id:
        return _repair_orphaned_lease(dispatcher, head, reply_id=reply_id, agent_name=agent_name)

    current = get_job(dispatcher, delivery_job_id)
    if current is None:
        return reset_stale_reply_head(
            dispatcher,
            head,
            reply_id=reply_id,
            agent_name=agent_name,
        )

    if current.status.value == 'accepted' or current.status.value == 'queued':
        # A delivery job can stay pending forever when its mailbox claim keeps
        # failing (conflicting lease, head race). The stale-running repair only
        # covers 'running'; release the pending head so the next tick re-prepares
        # and re-claims the delivery while the asker sits idle.
        return _repair_pending_stall(dispatcher, current, head, reply_id=reply_id, agent_name=agent_name)

    if current.status.value != 'running':
        return None

    persisted = _load_persisted_execution_state(dispatcher, current.job_id)
    if persisted is not None and persisted.pending_decision is not None and persisted.pending_decision.terminal:
        return dispatcher.complete(current.job_id, persisted.pending_decision)
    if _execution_job_is_live(dispatcher, current.job_id):
        return None

    return dispatcher.complete(
        current.job_id,
        reply_delivery_failed_decision(
            current,
            finished_at=dispatcher._clock(),
            reason='reply_delivery_stale_running_repaired',
            diagnostics={
                'repair_kind': 'stale_running_delivery_job',
                'head_inbound_event_id': head.inbound_event_id,
                'reply_id': reply_id,
            },
        ),
    )


def _repair_pending_stall(dispatcher, current, head, *, reply_id: str, agent_name: str):
    stalled = _pending_delivery_is_stalled(dispatcher, current)
    if not stalled:
        return None
    if not _delivery_never_reached_provider(dispatcher, current, head):
        return None
    reset = reset_stale_reply_head(
        dispatcher,
        head,
        reply_id=reply_id,
        agent_name=agent_name,
    )
    if reset is None:
        return None
    from ..records import append_event

    append_event(
        dispatcher,
        current,
        'reply_delivery_pending_stall_repaired',
        {
            'repair_kind': 'pending_delivery_job',
            'head_inbound_event_id': head.inbound_event_id,
            'reply_id': reply_id,
            'stalled_since': current.updated_at,
        },
        timestamp=dispatcher._clock(),
    )
    return reset


def _pending_delivery_is_stalled(dispatcher, current) -> bool:
    age = _seconds_since(dispatcher, current.updated_at or current.created_at)
    return age is not None and age >= REPLY_DELIVERY_PENDING_STALL_SECONDS


def _delivery_never_reached_provider(dispatcher, current, head) -> bool:
    """Guard against resurrecting a delivery whose prompt already entered a turn.

    A pending (accepted/queued) delivery should never have started a provider
    turn, but completion evidence wins over status: if a prior attempt reached
    the provider, consuming the head here could duplicate a delivered reply.
    """
    if current.status.value == 'queued':
        return True
    lease = dispatcher._message_bureau_control._lease_store.load(current.agent_name)
    if lease is not None and lease.lease_state is not None and str(getattr(lease.lease_state, 'value', lease.lease_state)) == 'acquired' and lease.inbound_event_id != head.inbound_event_id:
        # A live lease on a different event keeps the claim conflicting; the
        # delivery itself has not been sent for this head.
        return True
    events = _delivery_events(dispatcher, current)
    for record in events:
        if record.type in {'job_started', 'provider_start_failed', 'reply_delivery_consumed', 'reply_delivery_terminal_after_delivery'}:
            return False
    return True


def _delivery_events(dispatcher, job):
    try:
        _line, events = dispatcher._event_store.read_since_target(
            job.target_kind,
            job.target_name,
            0,
        )
    except Exception:
        return ()
    return tuple(record for record in events if getattr(record, 'job_id', None) == job.job_id)


def _seconds_since(dispatcher, timestamp: str | None) -> float | None:
    base = _parse_timestamp(timestamp)
    now = _parse_timestamp(dispatcher._clock())
    if base is None or now is None:
        return None
    return max(0.0, (now - base).total_seconds())


def _parse_timestamp(value: str | None) -> datetime | None:
    text = str(value or '').strip()
    if not text:
        return None
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _repair_orphaned_lease(dispatcher, head, *, reply_id: str, agent_name: str):
    lease = dispatcher._message_bureau_control._lease_store.load(agent_name)
    if lease is None:
        return None
    if lease.inbound_event_id != head.inbound_event_id:
        return None
    return reset_stale_reply_head(
        dispatcher,
        head,
        reply_id=reply_id,
        agent_name=agent_name,
    )


def _load_persisted_execution_state(dispatcher, job_id: str):
    execution = getattr(dispatcher, '_execution_service', None)
    if execution is None:
        return None
    state_store = getattr(execution, '_state_store', None)
    if state_store is None:
        return None
    try:
        return state_store.load(job_id)
    except Exception:
        return None


def _execution_job_is_live(dispatcher, job_id: str) -> bool:
    execution = getattr(dispatcher, '_execution_service', None)
    if execution is None:
        return False
    try:
        active = getattr(execution, '_active', None)
        if isinstance(active, dict) and job_id in active:
            return True
    except Exception:
        return False
    try:
        pending_replays = getattr(execution, '_pending_replays', None)
        if isinstance(pending_replays, dict) and job_id in pending_replays:
            return True
    except Exception:
        return False
    return False


__all__ = ['repair_reply_delivery_heads']
