from dataclasses import replace

from completion.models import CompletionSourceKind, CompletionStatus
from provider_backends.omp import reply_delivery
from provider_execution.base import ProviderSubmission


def submission():
    return ProviderSubmission(job_id='delivery-a', agent_name='caller', provider='omp',
        accepted_at='now', ready_at='now', source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
        reply='', runtime_state={'provider': 'omp', 'actor': 'caller', 'launch_session_id': 'launch',
            'draft_guard_socket': '/unused', 'pending_prompt': 'CCB_REPLY result A'})


def test_busy_receipt_waits_for_native_persistence_without_replaying(monkeypatch):
    calls = []
    persisted = [False]
    def request(path, actor, launch, operation, **fields):
        calls.append(operation)
        base = {'runtime_instance_id': 'runtime', 'session_id': 'session'}
        if operation == 'reply_capabilities':
            return {**base, 'reply_ingress_version': 1}
        if operation == 'reply_deliver':
            raise TimeoutError('accepted but response lost')
        return {**base, 'state': 'received' if persisted[0] else 'pending',
            'delivery_id': 'delivery-a', 'body_sha256': reply_delivery.hashlib.sha256(b'CCB_REPLY result A').hexdigest()}
    monkeypatch.setattr(reply_delivery, 'request', request)
    original = submission()
    pending = reply_delivery.poll_receipt(original, dict(original.runtime_state), now='1')
    assert pending.runtime_state['reply_receipt_transport']
    pending = reply_delivery.poll_receipt(pending, dict(pending.runtime_state), now='2')
    assert pending.runtime_state['reply_receipt_wait'] == 'pending'
    persisted[0] = True
    result = reply_delivery.poll_receipt(pending, dict(pending.runtime_state), now='3')
    assert result.decision.status is CompletionStatus.COMPLETED
    assert result.decision.diagnostics['business_processing_complete'] is False
    assert calls == ['reply_capabilities', 'reply_deliver', 'reply_status', 'reply_status']


def test_receipt_from_different_session_or_body_cannot_release_head(monkeypatch):
    original = submission()
    state = {**original.runtime_state, 'reply_receipt_transport': True,
        'runtime_instance_id': 'runtime', 'reply_receipt_session_id': 'session',
        'reply_body_sha256': 'expected', 'prompt_sent': True}
    for change in [{'session_id': 'foreign'}, {'body_sha256': 'wrong'}]:
        def request(*args, **kwargs):
            return {'state': 'received', 'runtime_instance_id': 'runtime', 'session_id': 'session',
                'delivery_id': 'delivery-a', 'body_sha256': 'expected', **change}
        monkeypatch.setattr(reply_delivery, 'request', request)
        result = reply_delivery.poll_receipt(replace(original, runtime_state=state), dict(state), now='4')
        assert isinstance(result, ProviderSubmission)
        assert not result.runtime_state.get('anchor_seen')
