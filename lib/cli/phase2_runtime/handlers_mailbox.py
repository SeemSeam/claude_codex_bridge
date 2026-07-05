from __future__ import annotations

from types import SimpleNamespace


def handle_ping(context, command, out, services) -> int:
    payload = services.ping_target(context, command)
    services.write_lines(out, services.render_mapping(payload))
    return 0


def handle_pend(context, command, out, services) -> int:
    if getattr(command, 'observer_mode', 'snapshot') == 'watch':
        services.write_lines(out, services.render_observer_notice(view='watch', terminal=False))
        watch_command = SimpleNamespace(target=command.target)
        for batch in services.watch_target(context, watch_command):
            services.write_lines(out, services.render_watch_batch(batch))
        return 0
    if getattr(command, 'observer_mode', 'snapshot') == 'inbox':
        inbox_command = SimpleNamespace(agent_name=command.target, detail=bool(command.detail))
        payload = services.inbox_target(context, inbox_command)
        services.write_lines(out, services.render_inbox(payload))
        return 0
    if getattr(command, 'observer_mode', 'snapshot') == 'queue':
        queue_command = SimpleNamespace(target=command.target, detail=bool(command.detail))
        payload = services.queue_target(context, queue_command)
        services.write_lines(out, services.render_queue(payload))
        return 0
    payload = services.pend_target(context, command)
    services.write_lines(out, services.render_pend(payload))
    return 0


def handle_queue(context, command, out, services) -> int:
    payload = services.queue_target(context, command)
    services.write_lines(out, services.render_queue(payload))
    return 0


def handle_trace(context, command, out, services) -> int:
    payload = services.trace_target(context, command)
    services.write_lines(out, services.render_trace(payload))
    return 0


def handle_resubmit(context, command, out, services) -> int:
    summary = services.resubmit_message(context, command)
    services.write_lines(out, services.render_resubmit(summary))
    return 0


def handle_retry(context, command, out, services) -> int:
    summary = services.retry_attempt(context, command)
    services.write_lines(out, services.render_retry(summary))
    return 0


def handle_wait(context, command, out, services) -> int:
    summary = services.wait_for_replies(context, command)
    services.write_lines(out, services.render_wait(summary))
    return 0


def handle_inbox(context, command, out, services) -> int:
    payload = services.inbox_target(context, command)
    services.write_lines(out, services.render_inbox(payload))
    return 0


def handle_ack(context, command, out, services) -> int:
    payload = services.ack_reply(context, command)
    services.write_lines(out, services.render_ack(payload))
    return 0


def handle_watch(context, command, out, services) -> int:
    services.write_lines(out, services.render_observer_notice(view='watch', terminal=False))
    for batch in services.watch_target(context, command):
        services.write_lines(out, services.render_watch_batch(batch))
    return 0


def handle_cancel(context, command, out, services) -> int:
    payload = services.cancel_job(context, command)
    services.write_lines(out, services.render_cancel(payload))
    return 0


def handle_why(context, command, out, services) -> int:
    payload = services.why_target(context, command)
    services.write_lines(out, services.render_why(payload))
    return 0


__all__ = [
    'handle_ack',
    'handle_cancel',
    'handle_inbox',
    'handle_pend',
    'handle_ping',
    'handle_queue',
    'handle_resubmit',
    'handle_retry',
    'handle_trace',
    'handle_wait',
    'handle_watch',
    'handle_why',
]
