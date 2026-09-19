from __future__ import annotations

from ccbd.api_models import JobEvent, JobRecord, TargetKind


def get_job(dispatcher, job_id: str) -> JobRecord | None:
    target = dispatcher._state.target_for_job(job_id)
    if target is not None:
        return dispatcher._job_store.get_latest_target(target[0], target[1], job_id)
    for candidate in dispatcher._config.agents:
        record = dispatcher._job_store.get_latest(candidate, job_id)
        if record is not None:
            dispatcher._state.remember_job(job_id, TargetKind.AGENT, candidate)
            return record
    return None


def latest_for_agent(dispatcher, agent_name: str) -> JobRecord | None:
    records = dispatcher._job_store.list_agent(agent_name)
    if not records:
        return None
    return records[-1]


def append_job(dispatcher, record: JobRecord) -> None:
    dispatcher._job_store.append(record)
    dispatcher._state.record(record)
    _mark_project_view_dirty(dispatcher)


def append_event(
    dispatcher,
    record: JobRecord,
    event_type: str,
    payload: dict[str, object],
    *,
    timestamp: str,
) -> None:
    dispatcher._event_store.append(
        JobEvent(
            event_id=dispatcher._new_id('evt'),
            job_id=record.job_id,
            agent_name=record.agent_name,
            target_kind=record.target_kind,
            target_name=record.target_name,
            type=event_type,
            payload=dict(payload),
            timestamp=timestamp,
        )
    )
    _mark_project_view_dirty(dispatcher)


def _mark_project_view_dirty(dispatcher) -> None:
    marker = getattr(dispatcher, 'mark_project_view_dirty', None)
    if callable(marker):
        marker()


def rebuild_dispatcher_state(dispatcher) -> None:
    from .fifo_order import mailbox_pending_job_order

    dispatcher._state.rebuild(
        dispatcher._job_store,
        agent_names=dispatcher._config.agents,
        mailbox_order=mailbox_pending_job_order(dispatcher, dispatcher._config.agents),
    )


__all__ = [
    'append_event',
    'append_job',
    'get_job',
    'latest_for_agent',
    'rebuild_dispatcher_state',
]
