from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from completion.models import CompletionConfidence, CompletionCursor, CompletionDecision, CompletionItem, CompletionItemKind, CompletionSourceKind, CompletionStatus

from .base import ProviderRuntimeContext, ProviderSubmission
from .common import deserialize_runtime_state, serialize_runtime_state

SCHEMA_VERSION = 3


@dataclass(frozen=True)
class PersistedExecutionState:
    submission: ProviderSubmission
    runtime_context: ProviderRuntimeContext | None
    resume_capable: bool
    persisted_at: str
    pending_decision: CompletionDecision | None = None
    pending_items: tuple[CompletionItem, ...] = ()
    applied_event_seqs: tuple[int, ...] = ()

    @property
    def job_id(self) -> str:
        return self.submission.job_id

    @property
    def provider(self) -> str:
        return self.submission.provider

    def to_record(self) -> dict[str, Any]:
        return {
            'schema_version': SCHEMA_VERSION,
            'record_type': 'execution_state',
            'submission': _submission_to_record(self.submission),
            'runtime_context': _runtime_context_to_record(self.runtime_context),
            'resume_capable': self.resume_capable,
            'persisted_at': self.persisted_at,
            'pending_decision': _decision_to_record(self.pending_decision),
            'pending_items': [_item_to_record(item) for item in self.pending_items],
            'applied_event_seqs': list(self.applied_event_seqs),
        }

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> 'PersistedExecutionState':
        if record.get('schema_version') != SCHEMA_VERSION:
            raise ValueError(f'schema_version must be {SCHEMA_VERSION}')
        if record.get('record_type') != 'execution_state':
            raise ValueError("record_type must be 'execution_state'")
        return cls(
            submission=_submission_from_record(dict(record['submission'])),
            runtime_context=_runtime_context_from_record(record.get('runtime_context')),
            resume_capable=bool(record.get('resume_capable', False)),
            persisted_at=str(record.get('persisted_at') or ''),
            pending_decision=_decision_from_record(record.get('pending_decision')),
            pending_items=tuple(_item_from_record(item) for item in record.get('pending_items', [])),
            applied_event_seqs=tuple(sorted({int(value) for value in record.get('applied_event_seqs', [])})),
        )


def _runtime_context_to_record(value: ProviderRuntimeContext | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return {
        'agent_name': value.agent_name,
        'workspace_path': value.workspace_path,
        'backend_type': value.backend_type,
        'runtime_ref': value.runtime_ref,
        'session_ref': value.session_ref,
        'runtime_pid': value.runtime_pid,
        'runtime_health': value.runtime_health,
        'remote_session_path': value.remote_session_path,
    }


def _runtime_context_from_record(record: dict[str, Any] | None) -> ProviderRuntimeContext | None:
    if record is None:
        return None
    return ProviderRuntimeContext(
        agent_name=str(record['agent_name']),
        workspace_path=record.get('workspace_path'),
        backend_type=record.get('backend_type'),
        runtime_ref=record.get('runtime_ref'),
        session_ref=record.get('session_ref'),
        runtime_pid=record.get('runtime_pid'),
        runtime_health=record.get('runtime_health'),
        remote_session_path=record.get('remote_session_path'),
    )


def _submission_to_record(value: ProviderSubmission) -> dict[str, Any]:
    return {
        'job_id': value.job_id,
        'agent_name': value.agent_name,
        'provider': value.provider,
        'accepted_at': value.accepted_at,
        'ready_at': value.ready_at,
        'source_kind': value.source_kind.value,
        'reply': value.reply,
        'status': value.status.value,
        'reason': value.reason,
        'confidence': value.confidence.value,
        'diagnostics': dict(value.diagnostics or {}),
        'runtime_state': serialize_runtime_state(dict(value.runtime_state)),
    }


def _submission_from_record(record: dict[str, Any]) -> ProviderSubmission:
    runtime_state = dict(deserialize_runtime_state(record.get('runtime_state') or {}))
    if 'request_anchor' not in runtime_state and runtime_state.get('req_id'):
        runtime_state['request_anchor'] = runtime_state.get('req_id')
    runtime_state.pop('req_id', None)
    return ProviderSubmission(
        job_id=str(record['job_id']),
        agent_name=str(record['agent_name']),
        provider=str(record['provider']),
        accepted_at=str(record['accepted_at']),
        ready_at=str(record['ready_at']),
        source_kind=CompletionSourceKind(str(record['source_kind'])),
        reply=str(record.get('reply') or ''),
        status=CompletionStatus(str(record.get('status') or CompletionStatus.INCOMPLETE.value)),
        reason=str(record.get('reason') or 'in_progress'),
        confidence=CompletionConfidence(str(record.get('confidence') or CompletionConfidence.OBSERVED.value)),
        diagnostics=dict(record.get('diagnostics') or {}),
        runtime_state=runtime_state,
    )


def _decision_to_record(value: CompletionDecision | None) -> dict[str, Any] | None:
    if value is None:
        return None
    return value.to_record()


def _decision_from_record(record: dict[str, Any] | None) -> CompletionDecision | None:
    if record is None:
        return None
    cursor_record = record.get('source_cursor')
    cursor = None if cursor_record is None else CompletionCursor(
        source_kind=CompletionSourceKind(str(cursor_record['source_kind'])),
        opaque_cursor=cursor_record.get('opaque_cursor'),
        session_path=cursor_record.get('session_path'),
        offset=cursor_record.get('offset'),
        line_no=cursor_record.get('line_no'),
        event_seq=cursor_record.get('event_seq'),
        updated_at=cursor_record.get('updated_at'),
    )
    confidence = record.get('confidence')
    return CompletionDecision(
        terminal=bool(record.get('terminal', False)),
        status=CompletionStatus(str(record.get('status') or CompletionStatus.INCOMPLETE.value)),
        reason=record.get('reason'),
        confidence=None if confidence is None else CompletionConfidence(str(confidence)),
        reply=str(record.get('reply') or ''),
        anchor_seen=bool(record.get('anchor_seen', False)),
        reply_started=bool(record.get('reply_started', False)),
        reply_stable=bool(record.get('reply_stable', False)),
        provider_turn_ref=record.get('provider_turn_ref'),
        source_cursor=cursor,
        finished_at=record.get('finished_at'),
        diagnostics=dict(record.get('diagnostics') or {}),
    )


def _item_to_record(value: CompletionItem) -> dict[str, Any]:
    return value.to_record()


def _item_from_record(record: dict[str, Any]) -> CompletionItem:
    cursor_record = dict(record['cursor'])
    cursor = CompletionCursor(
        source_kind=CompletionSourceKind(str(cursor_record['source_kind'])),
        opaque_cursor=cursor_record.get('opaque_cursor'),
        session_path=cursor_record.get('session_path'),
        offset=cursor_record.get('offset'),
        line_no=cursor_record.get('line_no'),
        event_seq=cursor_record.get('event_seq'),
        updated_at=cursor_record.get('updated_at'),
    )
    return CompletionItem(
        kind=CompletionItemKind(str(record['kind'])),
        timestamp=str(record['timestamp']),
        cursor=cursor,
        provider=str(record['provider']),
        agent_name=str(record['agent_name']),
        req_id=str(record['req_id']),
        payload=dict(record.get('payload') or {}),
    )
