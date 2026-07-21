from __future__ import annotations

from mailbox_kernel import InboundEventStatus, InboundEventType
from message_bureau.models import AttemptState
from message_bureau.reply_metadata import (
    reply_heartbeat_silence_seconds,
    reply_last_progress_at,
    reply_notice,
    reply_notice_kind,
)
from message_bureau.reply_payloads import delivery_job_id_from_payload

from .common import require_mailbox_target
from .events import pending_event_records, reply_for_event
from .views import agent_queue

TERMINAL_ATTEMPT_STATES = frozenset(
    {
        AttemptState.COMPLETED,
        AttemptState.INCOMPLETE,
        AttemptState.FAILED,
        AttemptState.CANCELLED,
        AttemptState.SUPERSEDED,
        AttemptState.DEAD_LETTER,
    }
)


def ack_reply(service, agent_name: str, inbound_event_id: str | None = None) -> dict[str, object]:
    normalized = require_mailbox_target(service, agent_name)
    head = _head_event(service, normalized, inbound_event_id=inbound_event_id)
    if head.event_type is InboundEventType.TASK_REQUEST:
        return _ack_terminal_task_request(service, normalized, head)

    reply = reply_for_event(service, head)
    if reply is None:
        raise ValueError(f'reply record missing for inbound event: {head.inbound_event_id}')
    attempt = _reply_attempt(service, head)

    timestamp = service._clock()
    consumed = service._mailbox_kernel.ack_reply(
        normalized,
        head.inbound_event_id,
        started_at=timestamp,
        finished_at=timestamp,
    )
    if consumed is None:
        raise RuntimeError(f'failed to ack reply event: {head.inbound_event_id}')

    mailbox_payload = agent_queue(service, normalized)
    next_records = pending_event_records(service, normalized)
    next_head = service._mailbox_kernel.head_pending_event(normalized)
    if next_head is None or (
        next_records and all(record.inbound_event_id != next_head.inbound_event_id for record in next_records)
    ):
        next_head = next_records[0] if next_records else None
    return _ack_payload(
        normalized,
        consumed=consumed,
        attempt=attempt,
        reply=reply,
        next_head=next_head,
        mailbox_payload=mailbox_payload,
    )


def _head_event(service, agent_name: str, *, inbound_event_id: str | None):
    direct_head = service._mailbox_kernel.head_pending_event(agent_name)
    requested_event_id = str(inbound_event_id or '').strip() or (
        direct_head.inbound_event_id if direct_head is not None else ''
    )
    if direct_head is not None and direct_head.inbound_event_id == requested_event_id:
        if direct_head.event_type is InboundEventType.TASK_REPLY:
            return _validate_reply_head(service, direct_head)
        if direct_head.event_type is InboundEventType.TASK_REQUEST:
            return direct_head

    records = pending_event_records(service, agent_name)
    head = records[0] if records else None
    if head is None:
        raise ValueError(f'inbox is empty for agent: {agent_name}')
    requested_event_id = requested_event_id or head.inbound_event_id
    if head.inbound_event_id != requested_event_id:
        raise ValueError(f'ack requires head event: {head.inbound_event_id}')
    return _validate_reply_head(service, head)


def _validate_reply_head(service, head):
    if head.event_type is not InboundEventType.TASK_REPLY:
        raise ValueError(
            f'ack only supports task_reply or terminal task_request head events; found: {head.event_type.value}'
        )
    delivery_job_id = delivery_job_id_from_payload(head.payload_ref)
    if delivery_job_id:
        attempt = _reply_attempt(service, head)
        source_job_id = getattr(attempt, 'job_id', None) or 'unknown'
        raise ValueError(
            f'ack is not allowed for task_reply event {head.inbound_event_id}: '
            f'automatic reply delivery has been scheduled as job {delivery_job_id} '
            f'for source job {source_job_id}; wait for automatic delivery or inspect the delivery job'
        )
    return head


def _ack_terminal_task_request(service, agent_name: str, head) -> dict[str, object]:
    attempt = service._attempt_store.get_latest(head.attempt_id) if head.attempt_id else None
    if attempt is not None and attempt.attempt_state not in TERMINAL_ATTEMPT_STATES:
        raise ValueError(
            'ack only supports terminal task_request head events; '
            f'found attempt_state={attempt.attempt_state.value}'
        )

    timestamp = service._clock()
    if attempt is not None and attempt.attempt_state is AttemptState.CANCELLED and head.status in {
        InboundEventStatus.CREATED,
        InboundEventStatus.QUEUED,
    }:
        consumed = service._mailbox_kernel.abandon(
            agent_name,
            head.inbound_event_id,
            finished_at=timestamp,
        )
    else:
        consumed = service._mailbox_kernel.consume(
            agent_name,
            head.inbound_event_id,
            finished_at=timestamp,
        )
    if consumed is None:
        raise RuntimeError(f'failed to ack task_request event: {head.inbound_event_id}')

    mailbox_payload = agent_queue(service, agent_name)
    next_records = pending_event_records(service, agent_name)
    next_head = service._mailbox_kernel.head_pending_event(agent_name)
    if next_head is None or (
        next_records and all(record.inbound_event_id != next_head.inbound_event_id for record in next_records)
    ):
        next_head = next_records[0] if next_records else None
    return {
        'target': agent_name,
        'agent_name': agent_name,
        'acknowledged_inbound_event_id': consumed.inbound_event_id,
        'acknowledged_event_type': consumed.event_type.value,
        'message_id': consumed.message_id,
        'attempt_id': consumed.attempt_id,
        'job_id': attempt.job_id if attempt is not None else None,
        'attempt_state': attempt.attempt_state.value if attempt is not None else None,
        'next_inbound_event_id': next_head.inbound_event_id if next_head is not None else None,
        'next_event_type': next_head.event_type.value if next_head is not None else None,
        'mailbox': mailbox_payload,
        'reply': '',
    }


def _reply_attempt(service, head):
    if not head.attempt_id:
        return None
    return service._attempt_store.get_latest(head.attempt_id)


def _ack_payload(
    agent_name: str,
    *,
    consumed,
    attempt,
    reply,
    next_head,
    mailbox_payload: dict[str, object],
) -> dict[str, object]:
    return {
        'target': agent_name,
        'agent_name': agent_name,
        'acknowledged_inbound_event_id': consumed.inbound_event_id,
        'message_id': consumed.message_id,
        'attempt_id': consumed.attempt_id,
        'job_id': attempt.job_id if attempt is not None else None,
        'reply_id': reply.reply_id,
        'reply_from_agent': reply.agent_name,
        'reply_terminal_status': reply.terminal_status.value,
        'reply_finished_at': reply.finished_at,
        'reply_notice': reply_notice(reply),
        'reply_notice_kind': reply_notice_kind(reply),
        'reply_last_progress_at': reply_last_progress_at(reply),
        'reply_heartbeat_silence_seconds': reply_heartbeat_silence_seconds(reply),
        'next_inbound_event_id': next_head.inbound_event_id if next_head is not None else None,
        'next_event_type': next_head.event_type.value if next_head is not None else None,
        'mailbox': mailbox_payload,
        'reply': reply.reply,
    }


__all__ = ['ack_reply']
