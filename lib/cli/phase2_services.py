from __future__ import annotations

from types import SimpleNamespace

from cli.render import (
    render_ack,
    render_agent_lifecycle,
    render_ask,
    render_cancel,
    render_followup,
    render_clear,
    render_compact,
    render_cleanup,
    render_config_validate,
    render_doctor,
    render_doctor_bundle,
    render_doctor_storage,
    render_fault_arm,
    render_fault_clear,
    render_fault_list,
    render_inbox,
    render_kill,
    render_layout,
    render_logs,
    render_loop_capacity,
    render_loop_run_once,
    render_loop_runner,
    render_loop_topology,
    render_maintenance,
    render_mobile_serve,
    render_plan_task,
    render_mapping,
    render_observer_notice,
    render_pend,
    render_ps,
    render_relay_operator,
    render_reload,
    render_restart,
    render_queue,
    render_resubmit,
    render_retry,
    render_start,
    render_trace,
    render_wait,
    render_watch_batch,
    write_lines,
)
from cli.services.ack import ack_reply
from cli.services.agent_lifecycle import agent_lifecycle
from cli.services.ask import exit_code_for_ask_status, submit_ask, watch_ask_job, write_ask_output
from cli.services.cancel import cancel_job
from cli.services.followup import active_job_followup
from cli.services.clear import clear_agent_context
from cli.services.compact import compact_agent_context
from cli.services.cleanup import cleanup_project_storage
from cli.services.config_ui import open_config_ui_url, prepare_config_ui
from cli.services.config_validate import effective_config_context, migrate_config_context, validate_config_context
from cli.services.project_command_approval import approve_project_commands_context, inspect_project_commands
from cli.services.doctor import doctor_summary
from cli.services.doctor_storage import doctor_storage_summary
from cli.services.diagnostics import export_diagnostic_bundle
from cli.services.fault import arm_fault_rule, clear_fault_rule, list_fault_rules
from cli.services.frontdesk_intake_command import frontdesk_intake_command
from cli.services.inbox import inbox_target
from cli.services.kill import kill_project
from cli.services.layout import layout_command
from cli.services.logs import agent_logs
from cli.services.screen import agent_screen
from cli.services.loop_capacity import loop_capacity
from cli.services.loop_run_once import loop_run_once
from cli.services.loop_runner import loop_runner_auto, loop_runner_once
from cli.services.loop_topology import loop_topology
from cli.services.maintenance import maintenance_status
from cli.services.mobile import mobile_devices_status, prepare_mobile_gateway, revoke_mobile_device
from cli.services.plan_tasks import plan_task
from cli.services.questions import question_command
from cli.services.pend import pend_target
from cli.services.ping import ping_target
from cli.services.ps import ps_summary
from cli.services.queue import queue_target
from cli.services.relay_operator import relay_operator_command
from cli.services.reload import reload_config
from cli.services.restart import restart_agent
from cli.services.resubmit import resubmit_message
from cli.services.retry import retry_attempt
from cli.services.start import start_agents
from cli.services.trace import trace_target
from cli.services.wait import wait_for_replies
from cli.services.watch import watch_target


def build_phase2_dispatch_services(**overrides):
    payload = dict(
        ack_reply=ack_reply,
        approve_project_commands_context=approve_project_commands_context,
        agent_lifecycle=agent_lifecycle,
        agent_logs=agent_logs,
        agent_screen=agent_screen,
        arm_fault_rule=arm_fault_rule,
        cancel_job=cancel_job,
        active_job_followup=active_job_followup,
        clear_agent_context=clear_agent_context,
        compact_agent_context=compact_agent_context,
        cleanup_project_storage=cleanup_project_storage,
        open_config_ui_url=open_config_ui_url,
        clear_fault_rule=clear_fault_rule,
        doctor_summary=doctor_summary,
        doctor_storage_summary=doctor_storage_summary,
        effective_config_context=effective_config_context,
        exit_code_for_ask_status=exit_code_for_ask_status,
        export_diagnostic_bundle=export_diagnostic_bundle,
        frontdesk_intake_command=frontdesk_intake_command,
        inbox_target=inbox_target,
        inspect_project_commands=inspect_project_commands,
        kill_project=kill_project,
        layout_command=layout_command,
        list_fault_rules=list_fault_rules,
        loop_capacity=loop_capacity,
        loop_run_once=loop_run_once,
        loop_runner_auto=loop_runner_auto,
        loop_runner_once=loop_runner_once,
        loop_topology=loop_topology,
        maintenance_status=maintenance_status,
        migrate_config_context=migrate_config_context,
        mobile_devices_status=mobile_devices_status,
        plan_task=plan_task,
        prepare_config_ui=prepare_config_ui,
        prepare_mobile_gateway=prepare_mobile_gateway,
        pend_target=pend_target,
        ping_target=ping_target,
        ps_summary=ps_summary,
        question_command=question_command,
        queue_target=queue_target,
        relay_operator_command=relay_operator_command,
        reload_config=reload_config,
        restart_agent=restart_agent,
        revoke_mobile_device=revoke_mobile_device,
        render_ack=render_ack,
        render_agent_lifecycle=render_agent_lifecycle,
        render_ask=render_ask,
        render_cancel=render_cancel,
        render_followup=render_followup,
        render_clear=render_clear,
        render_compact=render_compact,
        render_cleanup=render_cleanup,
        render_config_validate=render_config_validate,
        render_doctor=render_doctor,
        render_doctor_bundle=render_doctor_bundle,
        render_doctor_storage=render_doctor_storage,
        render_fault_arm=render_fault_arm,
        render_fault_clear=render_fault_clear,
        render_fault_list=render_fault_list,
        render_inbox=render_inbox,
        render_kill=render_kill,
        render_layout=render_layout,
        render_logs=render_logs,
        render_loop_capacity=render_loop_capacity,
        render_loop_run_once=render_loop_run_once,
        render_loop_runner=render_loop_runner,
        render_loop_topology=render_loop_topology,
        render_maintenance=render_maintenance,
        render_mobile_serve=render_mobile_serve,
        render_plan_task=render_plan_task,
        render_mapping=render_mapping,
        render_observer_notice=render_observer_notice,
        render_pend=render_pend,
        render_ps=render_ps,
        render_relay_operator=render_relay_operator,
        render_reload=render_reload,
        render_restart=render_restart,
        render_queue=render_queue,
        render_resubmit=render_resubmit,
        render_retry=render_retry,
        render_start=render_start,
        render_trace=render_trace,
        render_wait=render_wait,
        render_watch_batch=render_watch_batch,
        resubmit_message=resubmit_message,
        retry_attempt=retry_attempt,
        start_agents=start_agents,
        submit_ask=submit_ask,
        trace_target=trace_target,
        validate_config_context=validate_config_context,
        wait_for_replies=wait_for_replies,
        watch_ask_job=watch_ask_job,
        watch_target=watch_target,
        write_ask_output=write_ask_output,
        write_lines=write_lines,
    )
    payload.update(overrides)
    return SimpleNamespace(**payload)


__all__ = ['build_phase2_dispatch_services']
