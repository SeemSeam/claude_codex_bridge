from __future__ import annotations

from dataclasses import replace

from completion.models import (
    CompletionConfidence,
    CompletionCursor,
    CompletionDecision,
    CompletionStatus,
)
from provider_execution.base import ProviderPollResult, ProviderSubmission

from ..comm import AgyPaneReader
from ..protocol import extract_reply_for_req
from .helpers import hash_text, seconds_between, state_int, state_str


QUIET_SECS = 4.0
MAX_WAIT_SECS = 300.0
MIN_OBSERVED_SECS = 2.0


def poll_submission(submission: ProviderSubmission, *, now: str) -> ProviderPollResult | None:
    state = dict(submission.runtime_state)

    send_error = state.get('send_error')
    if send_error:
        return _terminal(
            submission,
            state,
            now,
            status=CompletionStatus.FAILED,
            reason=f'send_failed:{send_error}',
            reply='',
            confidence=CompletionConfidence.DEGRADED,
        )

    pane_id = state_str(state, 'pane_id')
    req_id = state_str(state, 'req_id')
    if not pane_id or not req_id:
        return _terminal(
            submission,
            state,
            now,
            status=CompletionStatus.FAILED,
            reason='runtime_state_invalid',
            reply='',
            confidence=CompletionConfidence.DEGRADED,
        )

    reader = _ensure_reader(state)
    if reader is None:
        return _terminal(
            submission,
            state,
            now,
            status=CompletionStatus.FAILED,
            reason='runtime_handle_lost',
            reply='',
            confidence=CompletionConfidence.DEGRADED,
        )

    content = reader.snapshot()
    if not content:
        state['snapshot_errors'] = state_int(state, 'snapshot_errors', 0) + 1

    current_hash = hash_text(content) if content else state_str(state, 'last_hash')
    last_hash = state.get('last_hash')
    started_at = state_str(state, 'started_at') or submission.accepted_at or now
    last_change_at = state_str(state, 'last_change_at') or started_at

    if content and current_hash != last_hash:
        state['last_hash'] = current_hash
        state['last_change_at'] = now
        last_change_at = now

    state['last_poll_at'] = now
    state['next_seq'] = state_int(state, 'next_seq', 1) + 1

    quiet_secs = seconds_between(last_change_at, now)
    total_secs = seconds_between(started_at, now)

    state['quiet_secs'] = quiet_secs
    state['total_secs'] = total_secs

    reply, done_seen = extract_reply_for_req(content, req_id)
    state['done_seen'] = done_seen
    state['reply_chars'] = len(reply)

    if done_seen and reply:
        return _terminal(
            submission,
            state,
            now,
            status=CompletionStatus.COMPLETED,
            reason='pane_done_marker',
            reply=reply,
            confidence=CompletionConfidence.OBSERVED,
        )

    if total_secs >= MAX_WAIT_SECS:
        return _terminal(
            submission,
            state,
            now,
            status=CompletionStatus.FAILED,
            reason='pane_quiet_timeout',
            reply=reply,
            confidence=CompletionConfidence.DEGRADED,
        )

    if (
        reply
        and total_secs >= MIN_OBSERVED_SECS
        and quiet_secs >= QUIET_SECS
    ):
        return _terminal(
            submission,
            state,
            now,
            status=CompletionStatus.COMPLETED,
            reason='pane_text_quiet',
            reply=reply,
            confidence=CompletionConfidence.DEGRADED,
        )

    progress = replace(submission, runtime_state=state)
    return ProviderPollResult(submission=progress, items=(), decision=None)


def _ensure_reader(state: dict[str, object]) -> AgyPaneReader | None:
    reader = state.get('reader')
    if isinstance(reader, AgyPaneReader):
        return reader
    backend = state.get('backend')
    pane_id = state_str(state, 'pane_id')
    lines = state_int(state, 'pane_lines', 200)
    if backend is None or not pane_id:
        return None
    rebuilt = AgyPaneReader(backend=backend, pane_id=pane_id, lines=lines)
    state['reader'] = rebuilt
    return rebuilt


def _terminal(
    submission: ProviderSubmission,
    state: dict[str, object],
    now: str,
    *,
    status: CompletionStatus,
    reason: str,
    reply: str,
    confidence: CompletionConfidence,
) -> ProviderPollResult:
    cleaned_reply = reply or ''
    progress = replace(
        submission,
        runtime_state=state,
        status=status,
        reason=reason,
        reply=cleaned_reply,
        confidence=confidence,
    )
    cursor = CompletionCursor(
        source_kind=submission.source_kind,
        event_seq=state_int(state, 'next_seq', 1),
        updated_at=now,
    )
    decision = CompletionDecision(
        terminal=True,
        status=status,
        reason=reason,
        confidence=confidence,
        reply=cleaned_reply,
        anchor_seen=bool(state.get('done_seen')) or bool(cleaned_reply),
        reply_started=bool(cleaned_reply),
        reply_stable=bool(cleaned_reply) and status is CompletionStatus.COMPLETED,
        provider_turn_ref=state_str(state, 'req_id') or None,
        source_cursor=cursor,
        finished_at=now,
        diagnostics={
            'mode': 'pane_quiet',
            'quiet_secs': float(state.get('quiet_secs') or 0.0),
            'total_secs': float(state.get('total_secs') or 0.0),
            'done_seen': bool(state.get('done_seen')),
            'snapshot_errors': state_int(state, 'snapshot_errors', 0),
            'reply_chars': state_int(state, 'reply_chars', 0),
        },
    )
    return ProviderPollResult(submission=progress, items=(), decision=decision)


__all__ = ['poll_submission']
