from __future__ import annotations

from .common import head_reply_id, project_id_for_agent
from .preparation_head import resolve_existing_delivery_job
from .preparation_message import build_reply_delivery_job
from .repair import repair_reply_delivery_heads
from .stalled import mark_stalled_reply_deliveries


def prepare_reply_deliveries(dispatcher):
    control = getattr(dispatcher, '_message_bureau_control', None)
    bureau = getattr(dispatcher, '_message_bureau', None)
    if control is None or bureau is None:
        return ()

    repair_reply_delivery_heads(dispatcher)
    mark_stalled_reply_deliveries(dispatcher)
    created = []
    for agent_name in dispatcher._config.agents:
        job = prepare_agent_reply_delivery(dispatcher, agent_name)
        if job is not None:
            created.append(job)
    return tuple(created)


def prepare_agent_reply_delivery(dispatcher, agent_name: str):
    from .common import head_reply_event

    head = head_reply_event(dispatcher, agent_name)
    if head is None:
        return None
    reply_id = head_reply_id(head)
    if not reply_id:
        return None

    head = resolve_existing_delivery_job(
        dispatcher,
        agent_name,
        head,
        reply_id=reply_id,
    )
    if head is None or head is False:
        return None

    reply = dispatcher._message_bureau_control._reply_store.get_latest(reply_id)
    if reply is None:
        return None

    accepted_at = dispatcher._clock()
    project_id = project_id_for_agent(dispatcher, agent_name)
    if not project_id:
        return None
    return build_reply_delivery_job(
        dispatcher,
        agent_name=agent_name,
        head=head,
        reply=reply,
        accepted_at=accepted_at,
        project_id=project_id,
    )


__all__ = ['prepare_reply_deliveries']
