from __future__ import annotations

from types import SimpleNamespace

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

    assert validated.status is CompletionStatus.COMPLETED
    assert validated.reason == 'reply_delivery_sent'
    assert validated.diagnostics['completion_source_kind'] == 'protocol_event_stream'


def test_reply_delivery_without_full_acceptance_proof_still_fails_closed() -> None:
    validated = _validate_provider_completion_decision(
        _submission(reply_delivery=True, delivery_state='pending_anchor', anchor_seen=True),
        _empty_completed_decision(reply_delivery=True),
    )

    # Ask and delivery completions share the codex acceptance isolation gate;
    # a delivery additionally owes no reply body, so the missing acceptance
    # evidence, not the empty body, is the attributable failure.
    assert validated.status is CompletionStatus.INCOMPLETE
    assert validated.reason == 'terminal_before_provider_acceptance'
    assert validated.diagnostics['completion_gate'] == 'provider_acceptance'
    assert validated.diagnostics['original_reason'] == 'reply_delivery_sent'


def _job(*, message_type: str = 'ask'):
    return SimpleNamespace(
        job_id='job_delivery',
        request=SimpleNamespace(message_type=message_type),
        provider_options={'reply_delivery': message_type == 'reply_delivery'},
    )


def test_job_marker_identifies_delivery_without_legacy_runtime_flag() -> None:
    submission = _submission(reply_delivery=False, delivery_state='accepted', anchor_seen=True)

    validated = _validate_provider_completion_decision(
        submission,
        _empty_turn_end_decision(),
        _job(message_type='reply_delivery'),
    )

    # The durable job marker, not the legacy runtime flag, routes the empty
    # provider turn end through the delivery policy.
    assert validated.status is CompletionStatus.COMPLETED
    assert validated.reason == 'reply_delivery_turn_complete'
    assert validated.diagnostics['reply_delivery_turn_end'] is True
    assert validated.diagnostics['original_reason'] == 'task_complete_empty_reply'


def test_job_marker_ask_keeps_empty_turn_end_for_caller_notice() -> None:
    submission = _submission(reply_delivery=False, delivery_state='accepted', anchor_seen=True)

    validated = _validate_provider_completion_decision(
        submission,
        _empty_turn_end_decision(),
        _job(message_type='ask'),
    )

    # Ordinary asks keep the empty-result caller notice policy: an empty
    # turn end is never a successful ask, flag or no flag.
    assert validated.status is CompletionStatus.INCOMPLETE
    assert validated.reason == 'task_complete_empty_reply'
    assert validated.diagnostics['empty_reply'] is True


def test_cancelled_delivery_terminal_is_not_disguised_as_success() -> None:
    cancelled = CompletionDecision(
        terminal=True,
        status=CompletionStatus.CANCELLED,
        reason='cancelled_by_user',
        confidence=CompletionConfidence.EXACT,
        reply='',
        anchor_seen=True,
        reply_started=False,
        reply_stable=True,
        provider_turn_ref='turn-cancel',
        source_cursor=None,
        finished_at='2026-07-15T00:00:10Z',
        diagnostics={},
    )

    validated = _validate_provider_completion_decision(
        _submission(reply_delivery=True, delivery_state='accepted', anchor_seen=True),
        cancelled,
        _job(message_type='reply_delivery'),
    )

    assert validated.status is CompletionStatus.CANCELLED
    assert validated.reason == 'cancelled_by_user'


def test_durable_ask_identity_overrides_legacy_delivery_flag() -> None:
    validated = _validate_provider_completion_decision(
        _submission(reply_delivery=True, delivery_state='accepted', anchor_seen=True),
        _empty_turn_end_decision(),
        _job(message_type='ask'),
    )

    assert validated.status is CompletionStatus.INCOMPLETE
    assert validated.reason == 'task_complete_empty_reply'
    assert not validated.diagnostics.get('reply_delivery_turn_end')


def _empty_turn_end_decision() -> CompletionDecision:
    return CompletionDecision(
        terminal=True,
        status=CompletionStatus.INCOMPLETE,
        reason='task_complete_empty_reply',
        confidence=CompletionConfidence.EXACT,
        reply='',
        anchor_seen=True,
        reply_started=False,
        reply_stable=True,
        provider_turn_ref='turn-empty',
        source_cursor=None,
        finished_at='2026-07-15T00:00:10Z',
        diagnostics={'empty_reply': True, 'error_type': 'empty_provider_reply'},
    )


def test_ordinary_codex_empty_completion_still_fails_closed() -> None:
    validated = _validate_provider_completion_decision(
        _submission(reply_delivery=False, delivery_state='accepted', anchor_seen=True),
        _empty_completed_decision(reply_delivery=False),
    )

    assert validated.status is CompletionStatus.INCOMPLETE
    assert validated.reason == 'task_complete_empty_reply'
    assert validated.diagnostics['empty_reply'] is True


def test_declared_herdr_agent_state_completion_fails_closed_as_diagnostics_only() -> None:
    decision = CompletionDecision(
        terminal=True,
        status=CompletionStatus.COMPLETED,
        reason='task_complete',
        confidence=CompletionConfidence.OBSERVED,
        reply='done',
        anchor_seen=True,
        reply_started=True,
        reply_stable=True,
        provider_turn_ref='job_delivery',
        source_cursor=None,
        finished_at='2026-07-15T00:00:01Z',
        diagnostics={
            'completion_source': 'herdr_agent_state',
            'completion_source_kind': 'herdr_agent_state',
            'herdr_agent_state_ref': 'namespace/pane/state',
        },
    )

    validated = _validate_provider_completion_decision(
        _submission(reply_delivery=False, delivery_state='accepted', anchor_seen=True),
        decision,
    )

    assert validated.status is CompletionStatus.INCOMPLETE
    assert validated.reason == 'herdr_agent_state_not_completion_authority'
    assert validated.diagnostics['herdr_agent_state_role'] == 'diagnostics_only'
