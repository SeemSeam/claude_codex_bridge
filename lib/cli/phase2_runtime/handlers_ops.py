from __future__ import annotations

import json


def handle_agent(context, command, out, services) -> int:
    payload = services.agent_lifecycle(context, command)
    if bool(getattr(command, 'json_output', False)):
        out.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        out.write('\n')
        return 0 if str(payload.get('agent_lifecycle_status') or '') in {'ok', 'active', 'removed'} else 1
    services.write_lines(out, services.render_agent_lifecycle(payload))
    return 0 if str(payload.get('agent_lifecycle_status') or '') in {'ok', 'active', 'removed'} else 1


def handle_kill(context, command, out, services) -> int:
    summary = services.kill_project(context, command)
    services.write_lines(out, services.render_kill(summary))
    return 0


def handle_cleanup(context, command, out, services) -> int:
    summary = services.cleanup_project_storage(context, command)
    services.write_lines(out, services.render_cleanup(summary))
    return 0


def handle_clear(context, command, out, services) -> int:
    summary = services.clear_agent_context(context, command)
    services.write_lines(out, services.render_clear(summary))
    return 0


def handle_logs(context, command, out, services) -> int:
    summary = services.agent_logs(context, command)
    services.write_lines(out, services.render_logs(summary))
    return 0


def handle_loop_capacity(context, command, out, services) -> int:
    payload = services.loop_capacity(context, command)
    if bool(getattr(command, 'json_output', False)):
        out.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        out.write('\n')
        return 0
    services.write_lines(out, services.render_loop_capacity(payload))
    return 0


def handle_loop_topology(context, command, out, services) -> int:
    payload = services.loop_topology(context, command)
    status = str(payload.get('loop_topology_status') or '')
    exit_code = 0 if status not in {'failed', 'invalid'} else 1
    if bool(getattr(command, 'json_output', False)):
        out.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        out.write('\n')
        return exit_code
    services.write_lines(out, services.render_loop_topology(payload))
    return exit_code


def handle_loop_run_once(context, command, out, services) -> int:
    payload = services.loop_run_once(context, command, services)
    exit_code = 0 if str(payload.get('loop_run_status') or '') == 'ok' else 1
    if bool(getattr(command, 'json_output', False)):
        out.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        out.write('\n')
        return exit_code
    services.write_lines(out, services.render_loop_run_once(payload))
    return exit_code


def handle_loop_runner(context, command, out, services) -> int:
    payload = services.loop_runner_once(context, command, services)
    exit_code = 0 if str(payload.get('loop_runner_status') or '') in {'ok', 'idle', 'paused', 'blocked', 'terminal'} else 1
    if bool(getattr(command, 'json_output', False)):
        out.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        out.write('\n')
        return exit_code
    services.write_lines(out, services.render_loop_runner(payload))
    return exit_code


def handle_layout(context, command, out, services) -> int:
    payload = services.layout_command(context, command)
    exit_code = 0 if str(payload.get('layout_status') or '') in {'planned', 'ok'} else 1
    if bool(getattr(command, 'json_output', False)):
        out.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        out.write('\n')
        return exit_code
    services.write_lines(out, services.render_layout(payload))
    return exit_code


def handle_plan_task(context, command, out, services) -> int:
    payload = services.plan_task(context, command)
    if bool(getattr(command, 'json_output', False)):
        out.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        out.write('\n')
        return 0
    if str(getattr(command, 'action', '') or '') == 'breadcrumb':
        out.write(str(payload.get('breadcrumb') or ''))
        out.write('\n')
        return 0
    services.write_lines(out, services.render_plan_task(payload))
    return 0


def handle_question(context, command, out, services) -> int:
    payload = services.question_command(context, command)
    if bool(getattr(command, 'json_output', False)):
        out.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        out.write('\n')
        return 0
    services.write_lines(out, services.render_mapping(payload))
    return 0


def handle_maintenance(context, command, out, services) -> int:
    payload = services.maintenance_status(context, command)
    services.write_lines(out, services.render_maintenance(payload))
    return 0 if str(payload.get('maintenance_status') or '') in {'ok', 'degraded'} else 2


def handle_mobile(context, command, out, services) -> int:
    if command.action == 'devices':
        payload = services.mobile_devices_status(context, command)
        services.write_lines(out, services.render_mobile_serve(payload))
        return 0
    if command.action == 'revoke':
        payload = services.revoke_mobile_device(context, command)
        services.write_lines(out, services.render_mobile_serve(payload))
        return 0
    handle = services.prepare_mobile_gateway(context, command)
    services.write_lines(out, services.render_mobile_serve(handle.summary))
    flush = getattr(out, 'flush', None)
    if callable(flush):
        flush()
    try:
        handle.serve_forever()
    except KeyboardInterrupt:
        return 0
    finally:
        close = getattr(handle, 'close', None)
        if callable(close):
            close()
    return 0


def handle_ps(context, command, out, services) -> int:
    payload = services.ps_summary(context, command)
    services.write_lines(out, services.render_ps(payload))
    return 0


def handle_doctor(context, command, out, services) -> int:
    if command.bundle:
        summary = services.export_diagnostic_bundle(context, command)
        services.write_lines(out, services.render_doctor_bundle(summary))
        return 0
    if getattr(command, 'storage', False):
        json_output = getattr(command, 'json_output', False)
        payload = services.doctor_storage_summary(context, compact=not json_output)
        if json_output:
            out.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            out.write('\n')
            return 0
        services.write_lines(out, services.render_doctor_storage(payload))
        return 0
    if getattr(command, 'identity', False):
        payload = services.identity_summary(context)
        if command.json_output:
            out.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
            out.write('\n')
            return 0
        services.write_lines(out, services.render_identity(payload))
        return 0
    payload = services.doctor_summary(context)
    if getattr(command, 'deep', False):
        probe_payload = services.probe_summary(context)
        services.write_lines(out, services.render_doctor(payload))
        services.write_lines(out, services.render_probe(probe_payload))
        return 0
    services.write_lines(out, services.render_doctor(payload))
    return 0


def handle_identity(context, command, out, services) -> int:
    payload = services.identity_summary(context)
    if command.json_output:
        out.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        out.write('\n')
        return 0
    services.write_lines(out, services.render_identity(payload))
    return 0


def handle_probe(context, command, out, services) -> int:
    payload = services.probe_summary(context)
    if command.json_output:
        out.write(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
        out.write('\n')
        return 0
    services.write_lines(out, services.render_probe(payload))
    return 0


def handle_fault_list(context, command, out, services) -> int:
    summary = services.list_fault_rules(context)
    services.write_lines(out, services.render_fault_list(summary))
    return 0


def handle_fault_arm(context, command, out, services) -> int:
    summary = services.arm_fault_rule(context, command)
    services.write_lines(out, services.render_fault_arm(summary))
    return 0


def handle_fault_clear(context, command, out, services) -> int:
    summary = services.clear_fault_rule(context, command)
    services.write_lines(out, services.render_fault_clear(summary))
    return 0


def handle_reload(context, command, out, services) -> int:
    payload = services.reload_config(context, command)
    services.write_lines(out, services.render_reload(payload))
    return 0 if str(payload.get('status') or '') in {'ok', 'published', 'noop'} else 1


def handle_restart(context, command, out, services) -> int:
    payload = services.restart_agent(context, command)
    services.write_lines(out, services.render_restart(payload))
    return 0 if str(payload.get('restart_status') or payload.get('status') or '') == 'ok' else 1


__all__ = [
    'handle_agent',
    'handle_cleanup',
    'handle_clear',
    'handle_doctor',
    'handle_fault_arm',
    'handle_fault_clear',
    'handle_fault_list',
    'handle_kill',
    'handle_layout',
    'handle_logs',
    'handle_loop_capacity',
    'handle_loop_topology',
    'handle_loop_run_once',
    'handle_loop_runner',
    'handle_maintenance',
    'handle_mobile',
    'handle_plan_task',
    'handle_ps',
    'handle_question',
    'handle_reload',
    'handle_restart',
]
