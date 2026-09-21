from __future__ import annotations

from ccbd.api_models import TargetKind
from mailbox_kernel import InboundEventType

from ..records import get_job
from ..context import build_job_runtime_context
from ..reply_delivery import claim_reply_delivery_start, claimable_reply_delivery_job_ids
from .models import QueuedTargetSlot
from .recovery import refresh_slot_runtime_for_start
from .start import start_running_job


def start_next_queued_job(dispatcher, slot: QueuedTargetSlot):
    slot = refresh_slot_runtime_for_start(dispatcher, slot)
    if slot is None:
        return None
    if dispatcher._message_bureau is not None and slot.target_kind is TargetKind.AGENT:
        return _start_agent_mailbox_job(dispatcher, slot)
    queued = dispatcher._state.queued_items_for(slot.target_kind, slot.target_name)
    job_id = next(iter(queued), None)
    if job_id is None:
        return None
    current = get_job(dispatcher, job_id)
    if current is None:
        return None
    if not _draft_allows_start(dispatcher, current, slot):
        return None
    dispatcher._state.remove_queued_for(slot.target_kind, slot.target_name, job_id)
    return start_running_job(dispatcher, current, slot=slot)


def _start_agent_mailbox_job(dispatcher, slot: QueuedTargetSlot):
    queued_ids = set(dispatcher._state.queued_items_for(slot.target_kind, slot.target_name))
    head = _mailbox_head_event(dispatcher, slot.target_name)
    if head is None:
        return None
    if head.event_type is InboundEventType.TASK_REPLY:
        return _claim_reply_delivery(dispatcher, slot, queued_ids)
    if head.event_type is InboundEventType.TASK_REQUEST:
        return _claim_request_job(dispatcher, slot, queued_ids)
    return None


def _mailbox_head_event(dispatcher, agent_name: str):
    control = getattr(dispatcher, '_message_bureau_control', None)
    kernel = getattr(control, '_mailbox_kernel', None)
    if kernel is None:
        return None
    return kernel.head_pending_event(agent_name)


def _claim_reply_delivery(dispatcher, slot: QueuedTargetSlot, queued_ids: set[str]):
    for candidate in claimable_reply_delivery_job_ids(dispatcher, slot.target_name):
        if candidate not in queued_ids:
            continue
        current = get_job(dispatcher, candidate)
        if current is None:
            dispatcher._state.remove_queued_for(slot.target_kind, slot.target_name, candidate)
            continue
        started_at = dispatcher._clock()
        if not _draft_allows_start(dispatcher, current, slot):
            return None
        if not claim_reply_delivery_start(dispatcher, current, started_at=started_at):
            continue
        dispatcher._state.remove_queued_for(slot.target_kind, slot.target_name, candidate)
        return start_running_job(dispatcher, current, slot=slot, started_at=started_at)
    return None


def _claim_request_job(dispatcher, slot: QueuedTargetSlot, queued_ids: set[str]):
    for candidate in dispatcher._message_bureau.claimable_request_job_ids(slot.target_name):
        if candidate not in queued_ids:
            continue
        current = get_job(dispatcher, candidate)
        if current is None:
            dispatcher._state.remove_queued_for(slot.target_kind, slot.target_name, candidate)
            continue
        if not _draft_allows_start(dispatcher, current, slot):
            return None
        dispatcher._state.remove_queued_for(slot.target_kind, slot.target_name, candidate)
        return start_running_job(dispatcher, current, slot=slot)
    return None


def _draft_allows_start(dispatcher, job, slot) -> bool:
    check = getattr(dispatcher._execution_service, 'draft_allows_start', None)
    if callable(check) and not check(job, runtime_context=build_job_runtime_context(job, slot.runtime)):
        return False
    # Terminal inspection runs outside the dispatcher lock. Recheck cancellation
    # and ownership after I/O, before taking the queue head.
    current = get_job(dispatcher, job.job_id)
    return (current is not None and current.status == job.status
            and job.job_id in dispatcher._state.queued_items_for(slot.target_kind, slot.target_name)
            and dispatcher._state.active_job_for(slot.target_kind, slot.target_name) is None)


__all__ = ['start_next_queued_job']
