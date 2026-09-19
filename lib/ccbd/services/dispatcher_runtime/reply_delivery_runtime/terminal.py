from __future__ import annotations

from mailbox_kernel import InboundEventStatus

from ..records import append_event
from .common import is_reply_delivery_job, reply_delivery_inbound_event_id, reply_delivery_reply_id
from .head import rewrite_reply_head

_MAX_PRE_DELIVERY_TRANSPORT_REQUEUES = 3


def resolve_reply_delivery_terminal(dispatcher, job, *, finished_at: str) -> None:
    if not is_reply_delivery_job(job):
        return

    inbound_event_id = reply_delivery_inbound_event_id(job)
    reply_id = reply_delivery_reply_id(job)
    if not inbound_event_id or not reply_id:
        return

    control = dispatcher._message_bureau_control
    current = control._inbound_store.get_latest(job.agent_name, inbound_event_id)
    if current is None:
        return

    if job.status.value == 'completed':
        control._mailbox_kernel.consume(job.agent_name, inbound_event_id, finished_at=finished_at)
        append_event(
            dispatcher,
            job,
            'reply_delivery_consumed',
            {
                'inbound_event_id': inbound_event_id,
                'reply_id': reply_id,
            },
            timestamp=finished_at,
        )
        return

    disposition = _terminal_disposition(dispatcher, job, inbound_event_id=inbound_event_id)
    if disposition == 'consumed':
        # The delivered prompt already reached the target (accepted into a
        # processing turn, or the delivery was cancelled after that point).
        # The turn's terminal outcome ends this delivery once; requeueing
        # would resurrect a cancelled or already-processed result.
        control._mailbox_kernel.consume(job.agent_name, inbound_event_id, finished_at=finished_at)
        append_event(
            dispatcher,
            job,
            'reply_delivery_terminal_after_delivery',
            {
                'inbound_event_id': inbound_event_id,
                'reply_id': reply_id,
                'terminal_status': job.status.value,
                'terminal_reason': _terminal_reason(job),
            },
            timestamp=finished_at,
        )
        return
    if disposition == 'abandoned':
        control._mailbox_kernel.consume(job.agent_name, inbound_event_id, finished_at=finished_at)
        append_event(
            dispatcher,
            job,
            'reply_delivery_abandoned',
            {
                'inbound_event_id': inbound_event_id,
                'reply_id': reply_id,
                'terminal_status': job.status.value,
                'terminal_reason': _terminal_reason(job),
                'transport_requeues': _transport_requeue_count(dispatcher, job, inbound_event_id=inbound_event_id),
            },
            timestamp=finished_at,
        )
        return

    rewrite_reply_head(
        dispatcher,
        current,
        reply_id=reply_id,
        delivery_job_id=None,
        status=InboundEventStatus.QUEUED,
        updated_at=finished_at,
        clear_progress=True,
    )
    append_event(
        dispatcher,
        job,
        'reply_delivery_requeued',
        {
            'inbound_event_id': inbound_event_id,
            'reply_id': reply_id,
            'terminal_status': job.status.value,
        },
        timestamp=finished_at,
    )


def _terminal_disposition(dispatcher, job, *, inbound_event_id: str) -> str:
    """Classify a non-completed delivery terminal: requeue, consume, or abandon.

    - Cancelled deliveries are never resurrected.
    - Terminals after provider acceptance (the prompt entered a processing
      turn) end the delivery once; failures there are not redelivered.
    - Pre-acceptance transport failures requeue so a transient sender/pane
      outage can deliver later, bounded by a requeue budget before the reply
      is abandoned with attributable diagnostics.
    """
    if job.status.value == 'cancelled':
        return 'consumed'
    if _delivery_reached_processing(job):
        return 'consumed'
    if _transport_requeue_count(dispatcher, job, inbound_event_id=inbound_event_id) >= _MAX_PRE_DELIVERY_TRANSPORT_REQUEUES:
        return 'abandoned'
    return 'requeued'


def _delivery_reached_processing(job) -> bool:
    terminal = getattr(job, 'terminal_decision', None)
    if not isinstance(terminal, dict):
        return False
    if bool(terminal.get('anchor_seen')):
        return True
    diagnostics = terminal.get('diagnostics')
    if isinstance(diagnostics, dict):
        delivery_state = str(diagnostics.get('delivery_state') or '').strip().lower()
        if delivery_state == 'accepted':
            return True
        if str(diagnostics.get('delivery_status') or '').strip().lower() in {'sent', 'accepted', 'held_without_turn_evidence'}:
            return True
        if diagnostics.get('reply_delivery_no_turn_evidence'):
            return True
    return False


def _terminal_reason(job) -> str:
    terminal = getattr(job, 'terminal_decision', None)
    if isinstance(terminal, dict):
        return str(terminal.get('reason') or '')
    return ''


def _transport_requeue_count(dispatcher, job, *, inbound_event_id: str) -> int:
    count = 0
    _line, events = dispatcher._event_store.read_since_target(
        job.target_kind,
        job.target_name,
        0,
    )
    for record in events:
        if record.type != 'reply_delivery_requeued':
            continue
        payload = record.payload if isinstance(record.payload, dict) else {}
        if str(payload.get('inbound_event_id') or '') == inbound_event_id:
            count += 1
    return count

__all__ = ['resolve_reply_delivery_terminal']
