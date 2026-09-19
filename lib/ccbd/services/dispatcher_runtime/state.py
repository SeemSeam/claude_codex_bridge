from __future__ import annotations

from typing import TYPE_CHECKING, Iterable

from ccbd.api_models import JobRecord, JobStatus, TargetKind

if TYPE_CHECKING:
    from jobs.store import JobStore

from .state_active import DispatcherStateActiveMixin
from .state_agents import DispatcherStateAgentMixin
from .state_common import TargetQueue, TargetSlot, _PENDING_STATES
from .state_index import DispatcherStateIndexMixin
from .state_queue import DispatcherStateQueueMixin


class DispatcherState(
    DispatcherStateAgentMixin,
    DispatcherStateActiveMixin,
    DispatcherStateIndexMixin,
    DispatcherStateQueueMixin,
):
    def __init__(self, agent_names: Iterable[str]) -> None:
        self._queues: dict[TargetSlot, TargetQueue] = {}
        for name in agent_names:
            self._ensure_queue((TargetKind.AGENT, str(name)))
        self._job_index: dict[str, TargetSlot] = {}
        self._active_jobs: dict[TargetSlot, str] = {}

    def _ensure_queue(self, slot: TargetSlot) -> TargetQueue:
        queue = self._queues.get(slot)
        if queue is None:
            queue = TargetQueue()
            self._queues[slot] = queue
        return queue

    def _normalize_slot(self, target_kind: TargetKind | str, target_name: str) -> TargetSlot:
        return TargetKind(target_kind), str(target_name)

    def rebuild(self, job_store: JobStore, *, agent_names: Iterable[str], mailbox_order=None) -> None:
        mailbox_order_by_agent = dict(mailbox_order or {})
        self._job_index.clear()
        self._active_jobs.clear()
        for queue in self._queues.values():
            queue.clear()
        for agent_name in agent_names:
            latest_by_job: dict[str, JobRecord] = {}
            order: list[str] = []
            for record in job_store.list_agent(agent_name):
                if record.job_id not in latest_by_job:
                    order.append(record.job_id)
                latest_by_job[record.job_id] = record
                self._job_index[record.job_id] = self._normalize_slot(record.target_kind, record.target_name)
                self._ensure_queue((record.target_kind, record.target_name))
            running: list[str] = []
            pending: list[str] = []
            for job_id in order:
                latest = latest_by_job[job_id]
                if latest.status is JobStatus.RUNNING:
                    running.append(job_id)
                elif latest.status in _PENDING_STATES:
                    pending.append(job_id)
            for job_id in _admission_ordered(pending, mailbox_order_by_agent.get(agent_name) or ()):
                slot = self._normalize_slot(
                    latest_by_job[job_id].target_kind,
                    latest_by_job[job_id].target_name,
                )
                self._ensure_queue(slot).push(job_id)
            for job_id in running:
                slot = self._normalize_slot(
                    latest_by_job[job_id].target_kind,
                    latest_by_job[job_id].target_name,
                )
                self._active_jobs[slot] = job_id


def _admission_ordered(pending: list[str], mailbox_job_ids) -> list[str]:
    """Order pending jobs by durable mailbox admission, then JobStore order.

    The mailbox inbound history is the single FIFO that spans task requests
    and result deliveries. JobStore order is only a fallback for pending jobs
    without a matching pending mailbox event (legacy state), so delayed
    delivery-job materialization or a stale-head repair can never move an
    already admitted entry behind a newer one after restart.
    """
    pending_set = set(pending)
    ordered: list[str] = []
    seen: set[str] = set()
    for job_id in mailbox_job_ids:
        if job_id in pending_set and job_id not in seen:
            ordered.append(job_id)
            seen.add(job_id)
    for job_id in pending:
        if job_id not in seen:
            ordered.append(job_id)
            seen.add(job_id)
    return ordered
