"""Exact native v2 admission and explicit idle terminalization."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import uuid

from completion.models import (CompletionConfidence, CompletionDecision, CompletionItemKind,
                               CompletionSourceKind, CompletionStatus, seconds_between)
from opencode_runtime.native import NativeStateError, NativeTransportError, NativeRequestRejected
from opencode_runtime.native_v2 import OpenCodeV2State
from provider_execution.base import ProviderPollResult, ProviderSubmission
from provider_execution.common import build_item


def start(job, prepared, *, api_url, session_id, prompt, now, password_env=None):
    if not session_id:
        raise NativeStateError('OpenCode v2 task transport requires an exact native session binding')
    reader = OpenCodeV2State(api_url, str(prepared.session.work_dir), password_env=password_env)
    reader.session(session_id)  # Fail before submission on unsupported/mismatched services.
    input_id = 'msg_ccb_' + uuid.uuid4().hex
    state = dict(mode='active', reader=reader, backend=prepared.backend, pane_id=prepared.pane_id,
                 native_v2=True, native_session_id=session_id, native_input_id=input_id,
                 native_prompt_hash=hashlib.sha256(prompt.encode()).hexdigest(),
                 admission='unknown_no_replay', native_started_at=now, anchor_seen=False, next_seq=1)
    submission = ProviderSubmission(job_id=job.job_id, agent_name=job.agent_name, provider='opencode',
                                    accepted_at=now, ready_at=now,
                                    source_kind=CompletionSourceKind.SESSION_SNAPSHOT, reply='',
                                    diagnostics={'transport': 'opencode_v2_native', 'native_input_id': input_id},
                                    runtime_state=state)
    # One POST only. Neither timeout nor malformed/lost receipt permits replay.
    try:
        answer = reader.submit(session_id, input_id=input_id, job_id=job.job_id, prompt=prompt)
        receipt = answer.get('data') if isinstance(answer, dict) else None
        if (isinstance(receipt, dict) and receipt.get('id') == input_id
                and receipt.get('sessionID') == session_id and receipt.get('type') == 'user'):
            state['admission'] = 'accepted'
    except NativeRequestRejected:
        state['admission'] = 'rejected'
    except NativeStateError:
        pass  # Native ID observation, not the HTTP response, owns acceptance.
    return submission


def poll(submission, prepared, *, now):
    state = dict(submission.runtime_state)
    if state.get('admission') == 'rejected':
        return terminal(submission, state, now=now, status=CompletionStatus.FAILED,
                        reason='opencode_v2_native_admission_rejected')
    try:
        observed = prepared.reader.observe(state['native_session_id'], input_id=state['native_input_id'],
                                           job_id=submission.job_id, prompt_hash=state['native_prompt_hash'])
    except NativeTransportError:
        state.setdefault('native_transport_since', now)
        if seconds_between(state['native_transport_since'], now) >= 30:
            return terminal(submission, state, now=now, status=CompletionStatus.INCOMPLETE,
                            reason='opencode_v2_transport_unknown_no_replay')
        state['native_wait'] = 'transport_unavailable_no_replay'
        return ProviderPollResult(submission=replace(submission, runtime_state=state))
    except NativeStateError as exc:
        state['native_protocol_error'] = str(exc)
        # Avoid logging raw native messages, errors or private Provider payloads.
        return terminal(submission, state, now=now, status=CompletionStatus.INCOMPLETE,
                        reason='opencode_v2_binding_or_protocol_invalid')
    state.pop('native_transport_since', None)
    if not observed['anchor_seen'] and not observed.get('queued') and seconds_between(state['native_started_at'], now) >= 30:
        return terminal(submission, state, now=now, status=CompletionStatus.INCOMPLETE,
                        reason='opencode_v2_admission_unknown_no_replay')
    items = []
    if observed['anchor_seen'] and not state.get('anchor_seen'):
        state['anchor_seen'] = True
        items.append(build_item(submission, kind=CompletionItemKind.ANCHOR_SEEN, timestamp=now,
                                seq=state['next_seq'], payload={'turn_id': submission.job_id,
                                                              'message_id': state['native_input_id']}))
        state['next_seq'] += 1
    reply = observed['reply']
    if reply and reply != submission.reply:
        items.append(build_item(submission, kind=CompletionItemKind.ASSISTANT_FINAL, timestamp=now,
                                seq=state['next_seq'], payload={'text': reply, 'reply': reply,
                                                              'turn_id': submission.job_id,
                                                              'message_id': observed['assistant_id']}))
        state['next_seq'] += 1
    state.update(native_wait='', admission='projected' if observed['anchor_seen'] else state['admission'])
    updated = replace(submission, reply=reply, runtime_state=state)
    status = {'succeeded': CompletionStatus.COMPLETED, 'failed': CompletionStatus.FAILED,
              'interrupted': CompletionStatus.CANCELLED, 'incomplete': CompletionStatus.INCOMPLETE}.get(observed['state'])
    if status is not None:
        return terminal(updated, state, now=now, status=status,
                        reason='opencode_v2_native_' + observed['state'], items=items,
                        idle_id=observed['idle_id'])
    return ProviderPollResult(submission=updated, items=tuple(items))


def terminal(submission, state, *, now, status, reason, items=(), idle_id=None):
    return ProviderPollResult(submission=replace(submission, runtime_state=state), items=tuple(items),
        decision=CompletionDecision(terminal=True, status=status, reason=reason,
            confidence=CompletionConfidence.EXACT if idle_id else CompletionConfidence.DEGRADED,
            reply=submission.reply, anchor_seen=bool(state.get('anchor_seen')), reply_started=bool(submission.reply),
            reply_stable=bool(idle_id), provider_turn_ref=state['native_input_id'], source_cursor=None,
            finished_at=now, diagnostics={'terminal_authority': 'opencode_v2_native_idle',
                'native_input_id': state['native_input_id'], 'native_session_id': state['native_session_id'],
                'native_idle_id': idle_id, 'native_protocol_error': state.get('native_protocol_error'),
                'automatic_replay': False}))


def cancel(submission):
    """Cancel only the exact queued input; active interruption has no exact-ID fence."""
    state = submission.runtime_state
    reader = state['reader']
    from urllib.parse import quote
    session_id, input_id = state['native_session_id'], state['native_input_id']
    reader.session(session_id)
    inbox = reader.get('/api/session/' + quote(session_id, safe='') + '/inbox')
    entries = inbox.get('data') if isinstance(inbox, dict) else None
    if not isinstance(entries, list):
        raise NativeStateError('OpenCode v2 cancellation inbox unavailable')
    matches = [entry for entry in entries if isinstance(entry, dict) and entry.get('id') == input_id]
    if len(matches) == 1 and matches[0].get('sessionID') == session_id:
        # DELETE is idempotent and also returns 204 for a consumed input.
        # Detect already-projected work; never escalate to session interruption.
        reader.request('DELETE', '/api/session/' + quote(session_id, safe='') + '/inbox/' + quote(input_id, safe=''))
        if reader.timeline(session_id, input_id):
            raise NativeStateError('OpenCode v2 queued cancellation raced with delivery; native work may still be running')
        return
    # No native API can interrupt exactly one expected active input atomically.
    # Report this limit instead of sending Esc/C-c into a possibly foreign turn.
    raise NativeStateError('OpenCode v2 exact active-turn cancellation unsupported; native work may still be running')
