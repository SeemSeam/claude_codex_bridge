from __future__ import annotations

from typing import Any


def ask_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "agent_name": {
                "type": "string",
                "description": "Target agent name from .ccb/ccb.config.",
            },
            "message": {
                "type": "string",
                "description": "Request text to send to the target agent.",
            },
            "work_dir": {
                "type": "string",
                "description": "Project work directory that contains .ccb/ccb.config.",
            },
            "task_id": {
                "type": "string",
                "description": "Optional logical task id for correlation.",
            },
            "reply_to": {
                "type": "string",
                "description": "Optional job id to use as reply_to correlation.",
            },
        },
        "required": ["agent_name", "message"],
    }


def pend_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": "A job_id or agent name to inspect.",
            },
            "work_dir": {
                "type": "string",
                "description": "Project work directory that contains .ccb/ccb.config.",
            },
        },
        "required": ["target"],
    }


def ping_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "target": {
                "type": "string",
                "description": "Agent name, all, or ccbd.",
                "default": "ccbd",
            },
            "work_dir": {
                "type": "string",
                "description": "Project work directory that contains .ccb/ccb.config.",
            },
        },
        "required": [],
    }


def roster_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "work_dir": {
                "type": "string",
                "description": "Project work directory that contains .ccb/ccb.config.",
            },
            "alive_only": {
                "type": "boolean",
                "description": "If true, only include agents that appear to be running.",
                "default": False,
            },
        },
        "required": [],
    }


def peer_status_schema() -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "agent_name": {
                "type": "string",
                "description": "Agent name to inspect. Defaults to ccbd.",
                "default": "ccbd",
            },
            "work_dir": {
                "type": "string",
                "description": "Project work directory that contains .ccb/ccb.config.",
            },
        },
        "required": [],
    }


TOOL_DEFS = [
    {
        "name": "ccb_ask_agent",
        "description": "Submit a request to a named CCB agent.",
        "inputSchema": ask_schema(),
    },
    {
        "name": "ccb_pend_agent",
        "description": "Inspect the latest state/reply for a named agent or job.",
        "inputSchema": pend_schema(),
    },
    {
        "name": "ccb_ping_agent",
        "description": "Check ccbd or mounted-agent health inside the current project.",
        "inputSchema": ping_schema(),
    },
    {
        "name": "ccb_roster",
        "description": "List configured agents in the current project with runtime summary.",
        "inputSchema": roster_schema(),
    },
    {
        "name": "ccb_peer_status",
        "description": "Get a live ping/status snapshot for one agent or the control plane.",
        "inputSchema": peer_status_schema(),
    },
]


__all__ = ['TOOL_DEFS']
