from __future__ import annotations

from .handlers_ask import handle_ask
from .handlers_mailbox import (
    handle_ack,
    handle_cancel,
    handle_inbox,
    handle_pend,
    handle_ping,
    handle_queue,
    handle_resubmit,
    handle_retry,
    handle_trace,
    handle_wait,
    handle_watch,
    handle_why,
)
from .handlers_ops import (
    handle_agent,
    handle_cleanup,
    handle_clear,
    handle_doctor,
    handle_fault_arm,
    handle_fault_clear,
    handle_fault_list,
    handle_identity,
    handle_kill,
    handle_layout,
    handle_logs,
    handle_loop_capacity,
    handle_loop_run_once,
    handle_loop_runner,
    handle_loop_topology,
    handle_maintenance,
    handle_mobile,
    handle_plan_task,
    handle_probe,
    handle_ps,
    handle_question,
    handle_reload,
    handle_restart,
)
from .handlers_start import handle_config_validate, handle_start


_HANDLERS = {
    'ack': handle_ack,
    'agent': handle_agent,
    'ask': handle_ask,
    'cancel': handle_cancel,
    'clear': handle_clear,
    'cleanup': handle_cleanup,
    'config-validate': handle_config_validate,
    'doctor': handle_doctor,
    'fault-arm': handle_fault_arm,
    'fault-clear': handle_fault_clear,
    'fault-list': handle_fault_list,
    'identity': handle_identity,
    'inbox': handle_inbox,
    'kill': handle_kill,
    'layout': handle_layout,
    'logs': handle_logs,
    'loop-capacity': handle_loop_capacity,
    'loop-run-once': handle_loop_run_once,
    'loop-runner': handle_loop_runner,
    'loop-topology': handle_loop_topology,
    'maintenance': handle_maintenance,
    'mobile': handle_mobile,
    'pend': handle_pend,
    'plan-task': handle_plan_task,
    'ping': handle_ping,
    'probe': handle_probe,
    'ps': handle_ps,
    'question': handle_question,
    'queue': handle_queue,
    'reload': handle_reload,
    'restart': handle_restart,
    'resubmit': handle_resubmit,
    'retry': handle_retry,
    'start': handle_start,
    'trace': handle_trace,
    'wait': handle_wait,
    'watch': handle_watch,
    'why': handle_why,
}


def dispatch(context, command, out, services) -> int:
    handler = _HANDLERS.get(command.kind)
    if handler is None:
        print(f'command_status: unsupported\nerror: unsupported v2 command: {command.kind}', file=out)
        return 2
    return handler(context, command, out, services)


__all__ = ['dispatch']
