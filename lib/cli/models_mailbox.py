from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedAskCommand:
    project: str | None
    target: str
    sender: str | None
    message: str
    task_id: str | None = None
    reply_to: str | None = None
    mode: str | None = None
    compact: bool = False
    silence: bool = False
    callback: bool = False
    artifact_request: bool = False
    artifact_reply: bool = False
    kind: str = 'ask'


@dataclass(frozen=True)
class ParsedCancelCommand:
    project: str | None
    job_id: str
    kind: str = 'cancel'


@dataclass(frozen=True)
class ParsedPendCommand:
    project: str | None
    target: str
    count: int | None = None
    observer_mode: str = 'snapshot'
    detail: bool = False
    kind: str = 'pend'


@dataclass(frozen=True)
class ParsedQueueCommand:
    project: str | None
    target: str
    detail: bool = False
    kind: str = 'queue'


@dataclass(frozen=True)
class ParsedTraceCommand:
    project: str | None
    target: str
    kind: str = 'trace'


@dataclass(frozen=True)
class ParsedResubmitCommand:
    project: str | None
    message_id: str
    kind: str = 'resubmit'


@dataclass(frozen=True)
class ParsedRetryCommand:
    project: str | None
    target: str
    kind: str = 'retry'


@dataclass(frozen=True)
class ParsedWaitCommand:
    project: str | None
    mode: str
    target: str
    quorum: int | None = None
    timeout_s: float | None = None
    kind: str = 'wait'


@dataclass(frozen=True)
class ParsedWatchCommand:
    project: str | None
    target: str
    kind: str = 'watch'


@dataclass(frozen=True)
class ParsedInboxCommand:
    project: str | None
    agent_name: str
    detail: bool = False
    kind: str = 'inbox'


@dataclass(frozen=True)
class ParsedAckCommand:
    project: str | None
    agent_name: str
    inbound_event_id: str | None = None
    kind: str = 'ack'


@dataclass(frozen=True)
class ParsedWhyCommand:
    project: str | None
    job_id: str
    kind: str = 'why'


__all__ = [
    'ParsedAckCommand',
    'ParsedAskCommand',
    'ParsedCancelCommand',
    'ParsedInboxCommand',
    'ParsedPendCommand',
    'ParsedQueueCommand',
    'ParsedResubmitCommand',
    'ParsedRetryCommand',
    'ParsedTraceCommand',
    'ParsedWaitCommand',
    'ParsedWatchCommand',
    'ParsedWhyCommand',
]
