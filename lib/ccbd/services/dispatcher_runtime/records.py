from __future__ import annotations

from ccbd.api_models import JobEvent, JobRecord, TargetKind


_DESKTOP_EVENT_TYPES = {
    'job_accepted': 'job.accepted',
    'job_queued': 'job.accepted',
    'job_completed': 'job.updated',
    'job_cancelled': 'job.updated',
    'job_failed': 'job.updated',
    'job_incomplete': 'job.updated',
}


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
    _publish_desktop_event(dispatcher, record, event_type, payload)


def _mark_project_view_dirty(dispatcher) -> None:
    marker = getattr(dispatcher, 'mark_project_view_dirty', None)
    if callable(marker):
        marker()


def _publish_desktop_event(dispatcher, record: JobRecord, event_type: str, payload: dict[str, object]) -> None:
    """Project Core transition to Desktop event mapping.

    Only transitions with an explicit, reviewed Desktop meaning are mapped;
    all other Core event names remain Core-only and are never guessed.
    """
    desktop_type = _DESKTOP_EVENT_TYPES.get(str(event_type))
    authority = getattr(dispatcher, '_desktop_event_authority', None)
    publisher = getattr(authority, 'publish', None) if authority is not None else None
    if desktop_type is None or not callable(publisher):
        return
    event_payload = dict(payload or {})
    event_payload.update({
        'job_id': record.job_id,
        'agent_id': record.agent_name,
        'target_kind': str(record.target_kind.value if hasattr(record.target_kind, 'value') else record.target_kind),
        'target_id': record.target_name,
        'core_event_type': str(event_type),
    })
    try:
        publisher({
            'type': desktop_type,
            'project_id': str(getattr(dispatcher._layout, 'project_id', '') or ''),
            'payload': event_payload,
        })
    except Exception:
        # Legacy dispatcher writes remain authoritative even if the optional
        # Desktop projection is temporarily unavailable; Desktop then fails
        # closed on its durable cursor rather than fabricating a sequence.
        return


def rebuild_dispatcher_state(dispatcher) -> None:
    dispatcher._state.rebuild(dispatcher._job_store, agent_names=dispatcher._config.agents)


__all__ = [
    'append_event',
    'append_job',
    'get_job',
    'latest_for_agent',
    'rebuild_dispatcher_state',
]
