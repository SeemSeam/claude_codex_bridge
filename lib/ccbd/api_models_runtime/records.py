from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from agents.models import normalize_agent_name
from mailbox_runtime.targets import normalize_actor_name

from .common import SCHEMA_VERSION, JobStatus, TargetKind
from .messages import MessageEnvelope


@dataclass
class JobRecord:
    job_id: str
    submission_id: str | None
    agent_name: str
    provider: str
    request: MessageEnvelope
    status: JobStatus
    terminal_decision: dict[str, Any] | None
    cancel_requested_at: str | None
    created_at: str
    updated_at: str
    workspace_path: str | None = None
    target_kind: TargetKind = TargetKind.AGENT
    target_name: str = ""
    provider_instance: str | None = None
    provider_options: dict[str, Any] = field(default_factory=dict)
    no_reply_reason: str | None = None
    no_reply_detail: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.job_id:
            raise ValueError("job_id cannot be empty")
        self.target_kind = TargetKind(self.target_kind)
        normalized_agent = normalize_agent_name(self.target_name or self.agent_name)
        self.agent_name = normalized_agent
        self.target_name = normalized_agent
        self.provider_instance = None
        self.provider_options = dict(self.provider_options or {})
        if self.status in {
            JobStatus.COMPLETED,
            JobStatus.CANCELLED,
            JobStatus.FAILED,
            JobStatus.INCOMPLETE,
        } and self.terminal_decision is None:
            raise ValueError("terminal_decision is required for terminal job states")

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "record_type": "job_record",
            "job_id": self.job_id,
            "submission_id": self.submission_id,
            "agent_name": self.agent_name,
            "target_kind": self.target_kind.value,
            "target_name": self.target_name,
            "provider": self.provider,
            "provider_instance": self.provider_instance,
            "provider_options": dict(self.provider_options),
            "request": self.request.to_record(),
            "status": self.status.value,
            "terminal_decision": self.terminal_decision,
            "cancel_requested_at": self.cancel_requested_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "workspace_path": self.workspace_path,
            "no_reply_reason": self.no_reply_reason,
            "no_reply_detail": dict(self.no_reply_detail or {}),
        }


@dataclass
class SubmissionRecord:
    submission_id: str
    project_id: str
    from_actor: str
    target_scope: str
    task_id: str | None
    job_ids: list[str] = field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""

    def __post_init__(self) -> None:
        if not self.submission_id:
            raise ValueError("submission_id cannot be empty")
        if not self.project_id:
            raise ValueError("project_id cannot be empty")
        self.from_actor = normalize_actor_name(self.from_actor)

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "record_type": "submission_record",
            "submission_id": self.submission_id,
            "project_id": self.project_id,
            "from_actor": self.from_actor,
            "target_scope": self.target_scope,
            "task_id": self.task_id,
            "job_ids": list(self.job_ids),
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }


@dataclass
class JobEvent:
    event_id: str
    job_id: str
    agent_name: str
    type: str
    payload: dict[str, Any]
    timestamp: str
    target_kind: TargetKind = TargetKind.AGENT
    target_name: str = ""

    def __post_init__(self) -> None:
        if not self.event_id:
            raise ValueError("event_id cannot be empty")
        if not self.job_id:
            raise ValueError("job_id cannot be empty")
        if not self.type:
            raise ValueError("type cannot be empty")
        self.target_kind = TargetKind(self.target_kind)
        normalized_agent = normalize_agent_name(self.target_name or self.agent_name)
        self.agent_name = normalized_agent
        self.target_name = normalized_agent

    def to_record(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "record_type": "job_event",
            "event_id": self.event_id,
            "job_id": self.job_id,
            "agent_name": self.agent_name,
            "target_kind": self.target_kind.value,
            "target_name": self.target_name,
            "type": self.type,
            "payload": dict(self.payload),
            "timestamp": self.timestamp,
        }


__all__ = ["JobEvent", "JobRecord", "SubmissionRecord"]
