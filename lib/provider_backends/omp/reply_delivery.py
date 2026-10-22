"""OMP receipt transport: completion means native message persistence, not business completion."""
from __future__ import annotations

import hashlib
import json
import socket
from dataclasses import replace

from completion.models import CompletionConfidence, CompletionDecision, CompletionStatus
from provider_execution.base import ProviderPollResult


def request(socket_path: str, actor: str, launch_id: str, operation: str, **fields) -> dict:
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(1.0)
        client.connect(socket_path)
        client.sendall((json.dumps(dict(operation=operation, actor=actor, launch_session_id=launch_id, **fields), ensure_ascii=False) + '\n').encode())
        data = bytearray()
        while b'\n' not in data:
            chunk = client.recv(4096)
            if not chunk or len(data) + len(chunk) > 16384:
                raise ValueError('Invalid OMP receipt response')
            data.extend(chunk)
        response = json.loads(data.split(b'\n', 1)[0])
        if not isinstance(response, dict):
            raise ValueError('Invalid OMP receipt response')
        return response


def capable(socket_path: str, actor: str, launch_id: str) -> bool:
    response = request(socket_path, actor, launch_id, 'reply_capabilities')
    return bool(response.get('reply_ingress_version') == 1 and response.get('runtime_instance_id') and response.get('session_id'))


def poll_receipt(submission, state: dict, *, now: str):
    """Only the initial call sends. Ambiguous sends are queried, never replayed."""
    path = str(state.get('draft_guard_socket') or '')
    actor = str(state.get('actor') or '')
    launch = str(state.get('launch_session_id') or '')
    if not state.get('reply_receipt_transport'):
        try:
            capability = request(path, actor, launch, 'reply_capabilities')
        except (OSError, ValueError):
            return replace(submission, runtime_state=state)
        if capability.get('reply_ingress_version') != 1:
            state['reply_receipt_wait'] = 'restart_idle_agent_for_reply_ingress'
            return replace(submission, runtime_state=state)
        runtime = str(capability.get('runtime_instance_id') or '')
        session = str(capability.get('session_id') or '')
        if not runtime or not session:
            return replace(submission, runtime_state=state)
        body = str(state.get('pending_prompt') or '')
        if not body:
            return replace(submission, runtime_state=state)
        state.update(reply_receipt_transport=True, runtime_instance_id=runtime,
                     reply_receipt_session_id=session, prompt_sent=True, prompt_sent_at=now,
                     reply_body_sha256=hashlib.sha256(body.encode()).hexdigest())
        state.pop('pending_prompt', None)
        operation, fields = 'reply_deliver', {'body': body}
    else:
        operation, fields = 'reply_status', {}
    try:
        answer = request(path, actor, launch, operation,
                         delivery_id=submission.job_id,
                         runtime_instance_id=state['runtime_instance_id'],
                         session_id=state['reply_receipt_session_id'],
                         body_sha256=state['reply_body_sha256'], **fields)
    except (OSError, ValueError):
        state['reply_receipt_wait'] = 'receipt_unknown_no_replay'
        return replace(submission, runtime_state=state)
    if (answer.get('runtime_instance_id') != state['runtime_instance_id'] or
            answer.get('session_id') != state['reply_receipt_session_id']):
        state['reply_receipt_wait'] = 'native_session_changed_no_replay'
        return replace(submission, runtime_state=state)
    if (answer.get('state') != 'received' or answer.get('delivery_id') != submission.job_id or
            answer.get('body_sha256') != state['reply_body_sha256']):
        state['reply_receipt_wait'] = str(answer.get('state') or 'receipt_unknown_no_replay')
        return replace(submission, runtime_state=state)
    if operation == 'reply_deliver':
        return replace(submission, runtime_state=state)
    state.update(anchor_seen=True, delivery_state='accepted', reply_receipt_wait='')
    updated = replace(submission, runtime_state=state)
    return ProviderPollResult(submission=updated, decision=CompletionDecision(
        terminal=True, status=CompletionStatus.COMPLETED, reason='omp_reply_received',
        confidence=CompletionConfidence.EXACT, reply='', anchor_seen=True,
        reply_started=False, reply_stable=True, provider_turn_ref=submission.job_id,
        source_cursor=None, finished_at=now,
        diagnostics={'reply_delivery': True, 'delivery_status': 'accepted',
                     'terminal_authority': 'omp_native_persisted_custom_message',
                     'business_processing_complete': False,
                     'runtime_instance_id': state['runtime_instance_id'],
                     'native_session_id': state['reply_receipt_session_id']},
    ))
