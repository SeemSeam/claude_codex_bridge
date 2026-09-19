from dataclasses import replace
from types import SimpleNamespace

import pytest

from completion.models import CompletionDecision, CompletionStatus, CompletionConfidence
from ccbd.api_models import JobStatus
from ccbd.services.dispatcher_runtime.finalization_runtime import message_bureau
from ccbd.services.dispatcher_runtime.finalization_retry_runtime.policy import is_retryable_failure


def decision(status=CompletionStatus.FAILED, reply='provider error', reason='api_error'):
    return CompletionDecision(True, status, reason, CompletionConfidence.DEGRADED, reply,
                              True, False, False, 'turn-1', None, '2026-09-19T00:00:00Z', {})


@pytest.mark.parametrize('status,reply', [(CompletionStatus.FAILED, ''), (CompletionStatus.FAILED, 'quota detail'),
    (CompletionStatus.INCOMPLETE, 'partial output'), (CompletionStatus.INCOMPLETE, '')])
def test_abnormal_guidance_preserves_facts_and_is_idempotent(monkeypatch, status, reply):
    monkeypatch.setattr(message_bureau, 'callback_child_edge', lambda *a: None)
    original = decision(status, reply)
    job = SimpleNamespace(job_id='job-1', agent_name='demo', status=JobStatus(status.value))
    result = message_bureau._empty_result_notice_decision(None, job, original, deliver_to_caller=True)
    assert result.status == status and result.reason == original.reason
    assert reply in result.reply
    assert 'ccb screen demo' in result.reply and 'ccb trace job-1' in result.reply
    again = message_bureau._empty_result_notice_decision(None, job, result, deliver_to_caller=True)
    assert again is result


@pytest.mark.parametrize('status,silenced,chain', [(CompletionStatus.CANCELLED, False, False),
    (CompletionStatus.COMPLETED, False, False), (CompletionStatus.COMPLETED, True, False),
    (CompletionStatus.FAILED, False, True)])
def test_guidance_exclusions(monkeypatch, status, silenced, chain):
    monkeypatch.setattr(message_bureau, 'callback_child_edge', lambda *a: object() if chain else None)
    original = decision(status, '' if silenced else 'existing reply')
    job = SimpleNamespace(job_id='job-1', agent_name='demo', status=JobStatus(status.value))
    assert message_bureau._empty_result_notice_decision(None, job, original, deliver_to_caller=not silenced) is original


@pytest.mark.parametrize('reason', ['omp_empty_reply', 'pi_empty_reply', 'cursor_empty_reply', 'grok_empty_reply'])
def test_provider_empty_cannot_bypass_no_retry_policy(reason):
    outcome = replace(decision(CompletionStatus.INCOMPLETE, '', reason), diagnostics={'delivery_retryable': True})
    assert not is_retryable_failure(outcome, retry_policy={'retryable_reasons': [reason]}, provider_supports_resume_value=True)
