from __future__ import annotations

import sys
from pathlib import Path
from typing import Sequence, TextIO

from agents.config_loader import ensure_bootstrap_project_config
from cli.context import CliContextBuilder
from cli.phase2_errors import handle_phase2_exception, parse_phase2_command
from cli.phase2_runtime import (
    build_context as _build_context_impl,
    confirm_project_reset as _confirm_project_reset_impl,
    dispatch as _dispatch_impl,
    looks_like_config_validate as _looks_like_config_validate_impl,
    resolve_requested_project_root as _resolve_requested_project_root_impl,
    stream_is_tty as _stream_is_tty_impl,
)
from cli.phase2_services import build_phase2_dispatch_services
from cli.render import render_kill, write_lines
from cli.services.ack import ack_reply
from cli.services.ask import exit_code_for_ask_status, submit_ask, watch_ask_job, write_ask_output
from cli.services.cancel import cancel_job
from cli.services.clear import clear_agent_context
from cli.services.cleanup import cleanup_project_storage
from cli.services.config_validate import validate_config_context
from cli.services.doctor import doctor_summary
from cli.services.doctor_storage import doctor_storage_summary
from cli.services.diagnostics import export_diagnostic_bundle
from cli.services.identity import identity_summary
from cli.services.probe import probe_summary
from cli.services.fault import arm_fault_rule, clear_fault_rule, list_fault_rules
from cli.services.inbox import inbox_target
from cli.services.daemon import KillSummary
from cli.services.kill import kill_project
from cli.services.logs import agent_logs
from cli.services.loop_capacity import loop_capacity
from cli.services.loop_run_once import loop_run_once
from cli.services.loop_runner import loop_runner_once
from cli.services.loop_topology import loop_topology
from cli.services.maintenance import maintenance_status
from cli.services.mobile import mobile_devices_status, prepare_mobile_gateway, revoke_mobile_device
from cli.services.plan_tasks import plan_task
from cli.services.questions import question_command
from cli.services.pend import pend_target
from cli.services.ping import ping_target
from cli.services.ps import ps_summary
from cli.services.queue import queue_target
from cli.services.reload import reload_config
from cli.services.restart import restart_agent
from cli.services.reset_project import reset_project_state
from cli.services.resubmit import resubmit_message
from cli.services.retry import retry_attempt
from cli.services.start import start_agents
from cli.services.trace import trace_target
from cli.services.wait import wait_for_replies
from cli.services.watch import watch_target
from project.discovery import ProjectDiscoveryError
from project.ids import compute_project_id
from storage.paths import PathLayout


def maybe_handle_phase2(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    config_command = _looks_like_config_validate(argv)
    out = stdout or sys.stdout
    err = stderr or sys.stderr
    command = parse_phase2_command(argv, config_command=config_command, err=err)
    if command is None:
        return 2

    try:
        try:
            context = _build_context(command, cwd=cwd, out=out)
        except ProjectDiscoveryError:
            if command.kind == 'kill':
                return _render_kill_without_anchor(command, cwd=cwd, out=out)
            raise
        if _command_requires_bootstrap_config(command):
            ensure_bootstrap_project_config(context.project.project_root)
        return _dispatch(context, command, out)
    except Exception as exc:
        return handle_phase2_exception(err, command_kind=command.kind, exc=exc)


def _command_requires_bootstrap_config(command) -> bool:
    kind = getattr(command, 'kind', None)
    return kind not in {
        'cleanup',
        'config-validate',
        'kill',
        'loop-capacity',
        'loop-run-once',
        'loop-runner',
        'loop-topology',
        'plan-task',
        'question',
        'reload',
    }


def _render_kill_without_anchor(command, *, cwd: Path | None, out: TextIO) -> int:
    current = Path(cwd or Path.cwd()).expanduser()
    try:
        current = current.resolve()
    except Exception:
        current = current.absolute()
    project_root = _resolve_requested_project_root_impl(
        command,
        cwd=current,
        project_discovery_error_cls=ProjectDiscoveryError,
    )
    summary = KillSummary(
        project_id=compute_project_id(project_root),
        state='unmounted',
        socket_path=str(PathLayout(project_root).ccbd_socket_path),
        forced=bool(getattr(command, 'force', False)),
    )
    write_lines(out, render_kill(summary))
    return 0


def _build_context(command, *, cwd: Path | None, out: TextIO):
    return _build_context_impl(
        command,
        cwd=cwd,
        out=out,
        builder_cls=CliContextBuilder,
        reset_project_state_fn=reset_project_state,
        project_discovery_error_cls=ProjectDiscoveryError,
        confirm_project_reset_fn=_confirm_project_reset,
    )


def _confirm_project_reset(project_root: Path, *, out: TextIO) -> None:
    _confirm_project_reset_impl(
        project_root,
        out=out,
        stdin=sys.stdin,
        stream_is_tty_fn=_stream_is_tty,
    )


def _dispatch(context, command, out: TextIO) -> int:
    return _dispatch_impl(context, command, out, _dispatch_services())


def _dispatch_services():
    return build_phase2_dispatch_services(
        ack_reply=ack_reply,
        agent_logs=agent_logs,
        arm_fault_rule=arm_fault_rule,
        cancel_job=cancel_job,
        clear_agent_context=clear_agent_context,
        cleanup_project_storage=cleanup_project_storage,
        clear_fault_rule=clear_fault_rule,
        doctor_summary=doctor_summary,
        doctor_storage_summary=doctor_storage_summary,
        exit_code_for_ask_status=exit_code_for_ask_status,
        identity_summary=identity_summary,
        probe_summary=probe_summary,
        export_diagnostic_bundle=export_diagnostic_bundle,
        inbox_target=inbox_target,
        kill_project=kill_project,
        list_fault_rules=list_fault_rules,
        loop_capacity=loop_capacity,
        loop_run_once=loop_run_once,
        loop_runner_once=loop_runner_once,
        loop_topology=loop_topology,
        maintenance_status=maintenance_status,
        mobile_devices_status=mobile_devices_status,
        prepare_mobile_gateway=prepare_mobile_gateway,
        pend_target=pend_target,
        ping_target=ping_target,
        plan_task=plan_task,
        ps_summary=ps_summary,
        question_command=question_command,
        queue_target=queue_target,
        reload_config=reload_config,
        restart_agent=restart_agent,
        revoke_mobile_device=revoke_mobile_device,
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
    )


def _looks_like_config_validate(argv: Sequence[str]) -> bool:
    return _looks_like_config_validate_impl(argv)

def _stream_is_tty(stream: object) -> bool:
    return _stream_is_tty_impl(stream)
