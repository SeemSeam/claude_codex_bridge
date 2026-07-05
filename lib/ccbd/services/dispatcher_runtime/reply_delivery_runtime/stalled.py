from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from mailbox_kernel import InboundEventType
from message_bureau.facade_recording_common import mailbox_actor
from message_bureau.reply_payloads import reply_id_from_payload

# A terminal job whose reply has been sitting unconsumed in the sender's
# mailbox for this long is flagged reply_delivery_stalled (observability only
# -- no retry).  120s avoids false positives while a sender is mid-turn
# (senders routinely take 30-60s between mailbox polls), and still surfaces a
# genuinely-missed delivery well before a human would notice.
_REPLY_DELIVERY_STALLED_SECS = 120.0


def mark_stalled_reply_deliveries(dispatcher):
    """Tag terminal jobs whose reply has not been consumed by the sender."""
    control = getattr(dispatcher, '_message_bureau_control', None)
    if control is None:
        return ()
    kernel = getattr(control, '_mailbox_kernel', None)
    if kernel is None:
        return ()
    reply_store = getattr(control, '_reply_store', None)
    if reply_store is None:
        return ()
    attempt_store = getattr(control, '_attempt_store', None)
    if attempt_store is None:
        return ()

    candidates = []
    for agent_name in dispatcher._config.agents:
        sender_mailbox = mailbox_actor(control, agent_name)
        if sender_mailbox is None:
            continue
        for event in kernel.pending_events(sender_mailbox, event_type=InboundEventType.TASK_REPLY):
            reply_id = reply_id_from_payload(event.payload_ref)
            if not reply_id:
                continue
            reply = reply_store.get_latest(reply_id)
            if reply is None:
                continue
            if not str(reply.reply or '').strip():
                continue
            if not reply.attempt_id:
                continue
            attempt = attempt_store.get_latest(reply.attempt_id)
            if attempt is None:
                continue
            job = dispatcher.get(attempt.job_id)
            if job is None:
                continue
            if job.status in dispatcher._terminal_event_by_status:
                terminal = dict(job.terminal_decision or {})
                diagnostics = dict(terminal.get('diagnostics') or {})
                if diagnostics.get('no_reply_reason') == 'reply_delivery_stalled':
                    continue
                created_at = str(event.created_at or reply.finished_at or '').strip()
                if not created_at:
                    continue
                candidates.append((job, terminal, diagnostics, reply_id, event, sender_mailbox, created_at))

    if not candidates:
        return ()

    marked = []
    now = dispatcher._clock()
    for job, terminal, diagnostics, reply_id, event, sender_mailbox, created_at in candidates:
        elapsed = _seconds_between(created_at, now)
        if elapsed < _REPLY_DELIVERY_STALLED_SECS:
            continue
        diagnostics['no_reply_reason'] = 'reply_delivery_stalled'
        diagnostics['no_reply_detail'] = {
            'reply_id': reply_id,
            'inbound_event_id': event.inbound_event_id,
            'sender_mailbox': sender_mailbox,
            'created_at': created_at,
            'elapsed_secs': elapsed,
            'stalled_threshold_secs': _REPLY_DELIVERY_STALLED_SECS,
        }
        terminal['diagnostics'] = diagnostics
        updated = replace(job, terminal_decision=terminal, updated_at=now)
        dispatcher._append_job(updated)
        marked.append(updated)
    return tuple(marked)


def _seconds_between(start: str, end: str) -> float:
    start_dt = _parse_iso(start)
    end_dt = _parse_iso(end)
    if start_dt is None or end_dt is None:
        return 0.0
    return max(0.0, (end_dt - start_dt).total_seconds())


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        text = str(value).strip()
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        return datetime.fromisoformat(text)
    except Exception:
        return None


__all__ = ['mark_stalled_reply_deliveries']
