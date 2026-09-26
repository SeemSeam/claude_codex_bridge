from __future__ import annotations

from collections.abc import Mapping

from ..common import render_observer_notice


def render_queue(payload: Mapping[str, object]) -> tuple[str, ...]:
    status = 'ok'
    if payload.get('target') != 'all':
        agent = payload.get('agent') or {}
        if agent.get('summary_status') != 'ok':
            status = 'degraded'
    lines: list[str] = [
        f'queue_status: {status}',
        *render_observer_notice(view='queue', terminal=False),
        f'target: {payload.get("target")}',
    ]
    if payload.get('target') == 'all':
        lines.extend(
            [
                f'agent_count: {payload.get("agent_count")}',
                f'queued_agent_count: {payload.get("queued_agent_count")}',
                f'active_agent_count: {payload.get("active_agent_count")}',
                f'total_queue_depth: {payload.get("total_queue_depth")}',
                f'total_pending_reply_count: {payload.get("total_pending_reply_count")}',
            ]
        )
        agents = payload.get('agents') or ()
        for agent in agents:
            lines.append(
                'queue_agent: '
                f'name={agent["agent_name"]} runtime_state={agent.get("runtime_state")} '
                f'runtime_health={agent.get("runtime_health")} '
                f'phase={agent.get("execution_phase") or agent.get("mailbox_state")} '
                f'mailbox_state={agent["mailbox_state"]} '
                f'depth={agent["queue_depth"]} pending_replies={agent["pending_reply_count"]} '
                f'summary_status={agent.get("summary_status")} '
                f'delivery_wait={(agent.get("delivery_wait") or {}).get("reason")}'
            )
        return tuple(lines)

    agent = payload.get('agent') or {}
    lines.extend(
        [
            f'agent_name: {agent.get("agent_name")}',
            f'mailbox_id: {agent.get("mailbox_id")}',
            f'summary_status: {agent.get("summary_status")}',
            f'execution_phase: {agent.get("execution_phase") or agent.get("mailbox_state")}',
            f'execution_phase_reason: {agent.get("execution_phase_reason")}',
            f'mailbox_state: {agent.get("mailbox_state")}',
            f'runtime_state: {agent.get("runtime_state")}',
            f'runtime_health: {agent.get("runtime_health")}',
            f'lease_version: {agent.get("lease_version")}',
            f'queue_depth: {agent.get("queue_depth")}',
            f'pending_reply_count: {agent.get("pending_reply_count")}',
            f'active_inbound_event_id: {agent.get("active_inbound_event_id")}',
            f'last_inbound_started_at: {agent.get("last_inbound_started_at")}',
            f'last_inbound_finished_at: {agent.get("last_inbound_finished_at")}',
        ]
    )
    wait = agent.get('delivery_wait')
    if isinstance(wait, Mapping):
        lines.append(f'delivery_wait: job={wait.get("job_id")} reason={wait.get("reason")} '
                     f'elapsed_seconds={wait.get("elapsed_seconds")} '
                     f'wait_seconds={wait.get("wait_seconds")} clear_attempted={wait.get("clear_attempted")}')
        lines.append('delivery_wait_notice: 180s applies only to observed nonempty idle input; '
                     'unknown/busy does not authorize clearing or sending')
    if agent.get('summary_error') is not None:
        lines.append(f'summary_error: {agent.get("summary_error")}')
    if agent.get('summary_status') == 'missing':
        lines.append(
            'summary_notice: persisted mailbox summary is missing; routine observer view is degraded; use `ccb doctor` or wait for maintenance refresh'
        )
    elif agent.get('summary_status') == 'error':
        lines.append(
            'summary_notice: persisted mailbox summary is unreadable; routine observer view is degraded; use `ccb doctor` for diagnostics'
        )
    active = agent.get('active')
    if isinstance(active, Mapping):
        lines.append(
            'queue_active: '
            f'event={active.get("inbound_event_id")} type={active.get("event_type")} status={active.get("status")} '
            f'message={active.get("message_id")} attempt={active.get("attempt_id")} job={active.get("job_id")}'
        )
    queued_events = agent.get('queued_events')
    if queued_events is None:
        lines.append('queue_details: omitted; rerun with `ccb pend --queue --detail <agent>` or `ccb queue --detail <agent>` for queued-event detail')
        return tuple(lines)
    for event in queued_events or ():
        lines.append(
            'queue_event: '
            f'pos={event["position"]} event={event["inbound_event_id"]} type={event["event_type"]} '
            f'status={event["status"]} priority={event["priority"]} '
            f'message={event["message_id"]} attempt={event["attempt_id"]} job={event["job_id"]}'
        )
    return tuple(lines)


__all__ = ['render_queue']
