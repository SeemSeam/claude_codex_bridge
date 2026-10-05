from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from ccbd.api_models import JobRecord
from completion.models import (
    CompletionConfidence,
    CompletionDecision,
    CompletionItem,
    CompletionSourceKind,
    CompletionStatus,
)


@dataclass(frozen=True)
class ProviderRuntimeContext:
    agent_name: str
    workspace_path: str | None
    backend_type: str | None
    runtime_ref: str | None
    session_ref: str | None
    runtime_pid: int | None = None
    runtime_health: str | None = None
    runtime_binding_source: str | None = None
    remote_session_path: str | None = None


@dataclass(frozen=True)
class ProviderSubmission:
    job_id: str
    agent_name: str
    provider: str
    accepted_at: str
    ready_at: str
    source_kind: CompletionSourceKind
    reply: str
    status: CompletionStatus = CompletionStatus.INCOMPLETE
    reason: str = 'in_progress'
    confidence: CompletionConfidence = CompletionConfidence.OBSERVED
    diagnostics: dict | None = None
    runtime_state: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderPollResult:
    submission: ProviderSubmission
    items: tuple[CompletionItem, ...] = ()
    decision: CompletionDecision | None = None

    def __post_init__(self) -> None:
        if self.decision is not None and not self.decision.terminal:
            raise ValueError('provider poll decisions must be terminal')


class ProviderExecutionAdapter(Protocol):
    provider: str

    def start(self, job: JobRecord, *, context: ProviderRuntimeContext | None, now: str) -> ProviderSubmission:
        ...

    def poll(self, submission: ProviderSubmission, *, now: str) -> ProviderPollResult | None:
        ...
