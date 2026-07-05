from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import Any

from cli.context import CliContextBuilder
from cli.models import ParsedAskCommand, ParsedPendCommand, ParsedPingCommand, ParsedPsCommand
from cli.services.ask import submit_ask, watch_ask_job
from cli.services.pend import pend_target
from cli.services.ping import ping_target
from cli.services.ps import ps_summary

from server_runtime_io import tool_error, tool_ok


def parse_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def parse_timeout(value: Any, default: float = 120.0) -> float:
    try:
        parsed = float(value)
    except Exception:
        return default
    return parsed if parsed > 0 else default


def build_context_for(work_dir: str | None):
    cwd = Path(work_dir).expanduser() if work_dir else Path.cwd()
    command = ParsedPingCommand(project=None, target='ccbd')
    return CliContextBuilder().build(command, cwd=cwd, bootstrap_if_missing=True)


def _required_text(args: dict[str, Any], field: str) -> str | None:
    value = str(args.get(field) or "").strip()
    return value or None


def _optional_text(args: dict[str, Any], field: str) -> str | None:
    value = str(args.get(field) or "").strip()
    return value or None


def _submit_response(payload, *, agent_name: str) -> dict[str, Any]:
    if not payload.jobs:
        raise RuntimeError('ask submission returned no jobs')
    first_job = payload.jobs[0]
    return {
        "project_id": payload.project_id,
        "submission_id": payload.submission_id,
        "job_id": first_job["job_id"],
        "agent_name": first_job.get("agent_name") or agent_name,
        "target_kind": first_job.get("target_kind"),
        "target_name": first_job.get("target_name") or agent_name,
        "status": first_job.get("status"),
    }


def _terminal_submit_response(context, job_id: str, *, timeout_s: float, response: dict[str, Any]) -> dict[str, Any]:
    terminal = watch_ask_job(
        context,
        job_id,
        StringIO(),
        timeout=timeout_s,
        emit_output=False,
    )
    response.update(
        {
            "terminal": True,
            "status": terminal.status,
            "reply": terminal.reply or "",
        }
    )
    return response


def _async_submit_response(job_id: str, response: dict[str, Any]) -> dict[str, Any]:
    response.update(
        {
            "terminal": False,
            "reply_mode": "async",
        }
    )
    return response


def _pend_response(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "job_id": payload.get("job_id"),
        "agent_name": payload.get("agent_name"),
        "target_kind": payload.get("target_kind"),
        "target_name": payload.get("target_name"),
        "status": payload.get("status"),
        "terminal": bool(payload.get("terminal")),
        "reply": payload.get("reply") or "",
        "cursor": payload.get("cursor"),
    }


def submit_task(args: dict[str, Any], *, caller: str) -> dict[str, Any]:
    agent_name = (_required_text(args, "agent_name") or "").lower()
    message = _required_text(args, "message") or ""
    if not agent_name:
        return tool_error("agent_name is required")
    if not message:
        return tool_error("message is required")

    wait = parse_bool(args.get("wait"), default=False)
    timeout_s = parse_timeout(args.get("timeout_s"), default=120.0)
    work_dir = _optional_text(args, "work_dir")
    task_id = _optional_text(args, "task_id")
    reply_to = _optional_text(args, "reply_to")

    try:
        context = build_context_for(work_dir)
        payload = submit_ask(
            context,
            ParsedAskCommand(
                project=None,
                target=agent_name,
                sender=caller,
                message=message,
                task_id=task_id,
                reply_to=reply_to,
            ),
        )
        response = _submit_response(payload, agent_name=agent_name)
        job_id = response["job_id"]
        if wait:
            response = _terminal_submit_response(
                context,
                job_id,
                timeout_s=timeout_s,
                response=response,
            )
        else:
            response = _async_submit_response(job_id, response)
        return tool_ok(response)
    except Exception as exc:
        return tool_error(str(exc))


def pend_task(args: dict[str, Any]) -> dict[str, Any]:
    target = (_required_text(args, "target") or "").lower()
    if not target:
        return tool_error("target is required")

    work_dir = _optional_text(args, "work_dir")
    try:
        context = build_context_for(work_dir)
        payload = pend_target(context, ParsedPendCommand(project=None, target=target))
        return tool_ok(_pend_response(payload))
    except Exception as exc:
        return tool_error(str(exc))


def ping_agent(args: dict[str, Any]) -> dict[str, Any]:
    target = (_optional_text(args, "target") or "ccbd").lower()
    work_dir = _optional_text(args, "work_dir")
    try:
        context = build_context_for(work_dir)
        payload = ping_target(context, ParsedPingCommand(project=None, target=target))
        return tool_ok(payload)
    except Exception as exc:
        return tool_error(str(exc))


def roster(args: dict[str, Any]) -> dict[str, Any]:
    work_dir = _optional_text(args, "work_dir")
    alive_only = parse_bool(args.get("alive_only"), default=False)
    try:
        context = build_context_for(work_dir)
        payload = ps_summary(context, ParsedPsCommand(project=None, alive_only=alive_only))
        return tool_ok(payload)
    except Exception as exc:
        return tool_error(str(exc))


def peer_status(args: dict[str, Any]) -> dict[str, Any]:
    agent_name = (_optional_text(args, "agent_name") or "ccbd").lower()
    work_dir = _optional_text(args, "work_dir")
    try:
        context = build_context_for(work_dir)
        payload = ping_target(context, ParsedPingCommand(project=None, target=agent_name))
        return tool_ok(payload)
    except Exception as exc:
        return tool_error(str(exc))


_TOOL_HANDLERS = {
    "ccb_ask_agent": lambda args, caller: submit_task(args, caller=caller),
    "ccb_pend_agent": lambda args, caller: pend_task(args),
    "ccb_ping_agent": lambda args, caller: ping_agent(args),
    "ccb_roster": lambda args, caller: roster(args),
    "ccb_peer_status": lambda args, caller: peer_status(args),
}


def handle_tool_call(name: str, args: dict[str, Any], *, caller: str) -> dict[str, Any]:
    handler = _TOOL_HANDLERS.get(name)
    if handler is None:
        return tool_error(f"unknown tool: {name}")
    return handler(args, caller)


__all__ = ['handle_tool_call']
