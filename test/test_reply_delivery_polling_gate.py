from __future__ import annotations

from ccbd.services.dispatcher_runtime.polling_service import _validate_provider_completion_decision
from completion.models import (
    CompletionConfidence,
    CompletionDecision,
    CompletionSourceKind,
    CompletionStatus,
)
from provider_execution.base import ProviderSubmission


def _submission(*, reply_delivery: bool, delivery_state: str, anchor_seen: bool) -> ProviderSubmission:
    return ProviderSubmission(
        job_id='job_delivery',
        agent_name='talk2',
        provider='codex',
        accepted_at='2026-07-15T00:00:00Z',
        ready_at='2026-07-15T00:00:00Z',
        source_kind=CompletionSourceKind.PROTOCOL_EVENT_STREAM,
        reply='',
        runtime_state={
            'mode': 'active',
            'reply_delivery_complete_on_dispatch': reply_delivery,
            'delivery_state': delivery_state,
            'anchor_seen': anchor_seen,
        },
    )


def _empty_completed_decision(*, reply_delivery: bool, delivery_status: str = 'accepted') -> CompletionDecision:
    return CompletionDecision(
        terminal=True,
        status=CompletionStatus.COMPLETED,
        reason='reply_delivery_sent' if reply_delivery else 'task_complete',
        confidence=CompletionConfidence.OBSERVED,
        reply='',
        anchor_seen=True,
        reply_started=False,
        reply_stable=True,
        provider_turn_ref='job_delivery',
        source_cursor=None,
        finished_at='2026-07-15T00:00:01Z',
        diagnostics={
            'reply_delivery': reply_delivery,
            'delivery_status': delivery_status,
        },
    )


def test_confirmed_reply_delivery_empty_transport_ack_remains_completed() -> None:
    decision = _empty_completed_decision(reply_delivery=True)

    validated = _validate_provider_completion_decision(
        _submission(reply_delivery=True, delivery_state='accepted', anchor_seen=True),
        decision,
    )

    assert validated is decision
    assert validated.status is CompletionStatus.COMPLETED
    assert validated.reason == 'reply_delivery_sent'


def test_reply_delivery_without_full_acceptance_proof_still_fails_closed() -> None:
    validated = _validate_provider_completion_decision(
        _submission(reply_delivery=True, delivery_state='pending_anchor', anchor_seen=True),
        _empty_completed_decision(reply_delivery=True),
    )

    assert validated.status is CompletionStatus.INCOMPLETE
    assert validated.reason == 'task_complete_empty_reply'
    assert validated.diagnostics['original_reason'] == 'reply_delivery_sent'


def test_ordinary_codex_empty_completion_still_fails_closed() -> None:
    validated = _validate_provider_completion_decision(
        _submission(reply_delivery=False, delivery_state='accepted', anchor_seen=True),
        _empty_completed_decision(reply_delivery=False),
    )

    assert validated.status is CompletionStatus.INCOMPLETE
    assert validated.reason == 'task_complete_empty_reply'
    assert validated.diagnostics['empty_reply'] is True
