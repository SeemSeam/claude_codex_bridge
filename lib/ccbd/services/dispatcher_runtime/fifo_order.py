from __future__ import annotations

from mailbox_kernel import InboundEventType
from message_bureau.facade_recording_common import job_id_from_payload_ref
from message_bureau.reply_payloads import delivery_job_id_from_payload


def mailbox_pending_job_order(dispatcher, agent_names) -> dict[str, tuple[str, ...]]:
    """Resolve the durable mailbox admission order into pending job ids.

    The per-agent inbound history is the single chronological FIFO spanning
    task requests and result deliveries. Request events carry ``job:<id>``
    payloads; reply events carry ``reply:<id>`` plus, once materialized, the
    ``delivery:<job_id>`` token written by the reply-head rewrite. Events
    without a materialized delivery job resolve to no job id here; the
    delivery job is created on demand by reply preparation and keeps the
    event's original place.
    """
    kernel = _mailbox_kernel(dispatcher)
    if kernel is None:
        return {}
    order: dict[str, tuple[str, ...]] = {}
    for agent_name in agent_names:
        job_ids: list[str] = []
        for event in kernel.pending_events(agent_name):
            job_id = _job_id_for_event(event)
            if job_id:
                job_ids.append(job_id)
        order[str(agent_name)] = tuple(job_ids)
    return order


def _mailbox_kernel(dispatcher):
    control = getattr(dispatcher, '_message_bureau_control', None)
    kernel = getattr(control, '_mailbox_kernel', None)
    if kernel is None or not hasattr(kernel, 'pending_events'):
        return None
    return kernel


def _job_id_for_event(event) -> str | None:
    if event.event_type is InboundEventType.TASK_REQUEST:
        return job_id_from_payload_ref(event.payload_ref)
    if event.event_type is InboundEventType.TASK_REPLY:
        return delivery_job_id_from_payload(event.payload_ref)
    return None


__all__ = ['mailbox_pending_job_order']
