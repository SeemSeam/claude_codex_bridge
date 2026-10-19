from __future__ import annotations

from datetime import datetime, timezone

from mailbox_kernel import InboundEventStatus, LeaseState
from message_bureau.reply_payloads import delivery_job_id_from_payload

from ..records import get_job
from .common import head_reply_event, head_reply_id
from .decisions import reply_delivery_failed_decision
from .head import rewrite_reply_head
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

    if current.status.value in {'accepted', 'queued'}:
        # Recover a proven orphaned lease without replacing the pending job.
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
    if dispatcher._state.active_job_for(current.target_kind, current.target_name) is not None:
        return None
    control = dispatcher._message_bureau_control
    lease = control._lease_store.load(agent_name)
    if lease is None or lease.lease_state is not LeaseState.ACQUIRED:
        return None
    if not _delivery_never_reached_provider(dispatcher, current):
        return None
    if lease.inbound_event_id != head.inbound_event_id:
        leased_event = control._inbound_store.get_latest(agent_name, lease.inbound_event_id)
        if leased_event is not None and leased_event.status not in {
            InboundEventStatus.CONSUMED, InboundEventStatus.SUPERSEDED, InboundEventStatus.ABANDONED,
        }:
            return None
        # The kernel clears only matching leases when rewriting the head.
        # expire_lease clears missing/terminal events without abandoning work.
        control._mailbox_kernel.expire_lease(agent_name, finished_at=dispatcher._clock())
    rewrite_reply_head(
        dispatcher,
        head,
        reply_id=reply_id,
        delivery_job_id=current.job_id,
        status=InboundEventStatus.QUEUED,
        updated_at=dispatcher._clock(),
        clear_progress=True,
    )
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
            'released_lease_inbound_event_id': lease.inbound_event_id,
        },
        timestamp=dispatcher._clock(),
    )
    return head_reply_event(dispatcher, agent_name)


def _pending_delivery_is_stalled(dispatcher, current) -> bool:
    age = _seconds_since(dispatcher, current.updated_at or current.created_at)
    return age is not None and age >= REPLY_DELIVERY_PENDING_STALL_SECONDS


def _delivery_never_reached_provider(dispatcher, current) -> bool:
    """Guard against resurrecting a delivery whose prompt already entered a turn.

    A pending (accepted/queued) delivery should never have started a provider
    turn, but execution evidence wins over status: if a prior attempt reached
    the provider, releasing its lease could duplicate a delivered reply.
    """
    if _execution_job_is_live(dispatcher, current.job_id):
        return False
    execution = getattr(dispatcher, '_execution_service', None)
    state_store = getattr(execution, '_state_store', None)
    if state_store is not None:
        try:
            if state_store.load(current.job_id) is not None:
                return False
        except Exception:
            return False
    events = _delivery_events(dispatcher, current)
    if events is None:
        return False
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
        return None
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
