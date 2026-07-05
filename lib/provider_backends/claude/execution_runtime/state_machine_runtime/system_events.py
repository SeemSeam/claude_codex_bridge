from __future__ import annotations

from dataclasses import replace

from completion.models import (
    CompletionConfidence,
    CompletionCursor,
    CompletionDecision,
    CompletionItemKind,
    CompletionStatus,
)
from provider_execution.base import ProviderPollResult, ProviderSubmission
from provider_execution.common import build_item

from ..event_reading import is_turn_boundary_event, terminal_api_error_payload
from .models import ClaudePollState


def handle_user_event(submission: ProviderSubmission, poll: ClaudePollState, *, text: str, now: str) -> None:
    req_prefix = getattr(submission, "req_id_prefix", None)
    del req_prefix
    from ...protocol import REQ_ID_PREFIX

    if poll.request_anchor and f"{REQ_ID_PREFIX} {poll.request_anchor}" in text and not poll.anchor_seen:
        poll.items.append(
            build_item(
                submission,
                kind=CompletionItemKind.ANCHOR_SEEN,
                timestamp=now,
                seq=poll.next_seq,
                payload={"turn_id": poll.request_anchor},
            )
        )
        poll.next_seq += 1
        poll.anchor_seen = True


def handle_system_event(
    submission: ProviderSubmission,
    poll: ClaudePollState,
    event: dict[str, object],
    *,
    now: str,
    state: dict[str, object],
) -> ProviderPollResult | None:
    api_error = terminal_api_error_payload(event)
    if api_error is not None:
        timestamp = str(api_error.get("timestamp") or now)
        poll.items.append(
            build_item(
                submission,
                kind=CompletionItemKind.ERROR,
                timestamp=timestamp,
                seq=poll.next_seq,
                payload={
                    "reason": "api_error",
                    "turn_id": poll.request_anchor,
                    "session_path": poll.session_path or None,
                    **api_error,
                },
            )
        )
        decision = CompletionDecision(
            terminal=True,
            status=CompletionStatus.FAILED,
            reason="api_error",
            confidence=CompletionConfidence.OBSERVED,
            reply=poll.reply_buffer,
            anchor_seen=poll.anchor_seen,
            reply_started=bool(poll.reply_buffer),
            reply_stable=bool(poll.reply_buffer),
            provider_turn_ref=poll.request_anchor or poll.session_path or None,
            source_cursor=CompletionCursor(
                source_kind=submission.source_kind,
                session_path=poll.session_path or None,
                event_seq=poll.next_seq,
                updated_at=timestamp,
            ),
            finished_at=timestamp,
            diagnostics={
                "error_code": api_error.get("error_code"),
                "error_path": api_error.get("error_path"),
                "retry_attempt": api_error.get("retry_attempt"),
                "max_retries": api_error.get("max_retries"),
                "error_type": "provider_api_error",
                "no_reply_reason": "provider_api_error",
                "no_reply_detail": {
                    "event_reason": "api_error",
                    "error_code": api_error.get("error_code"),
                    "error_path": api_error.get("error_path"),
                },
            },
        )
        updated = replace(
            submission,
            reply=poll.reply_buffer,
            runtime_state={
                **submission.runtime_state,
                "mode": "passive",
                "state": state,
                "next_seq": poll.next_seq + 1,
                "anchor_seen": poll.anchor_seen,
                "reply_buffer": poll.reply_buffer,
                "raw_buffer": poll.raw_buffer,
                "session_path": poll.session_path,
                "last_assistant_uuid": poll.last_assistant_uuid,
            },
        )
        return ProviderPollResult(submission=updated, items=tuple(poll.items), decision=decision)

    if is_turn_boundary_event(event, last_assistant_uuid=poll.last_assistant_uuid):
        poll.items.append(
            build_item(
                submission,
                kind=CompletionItemKind.TURN_BOUNDARY,
                timestamp=now,
                seq=poll.next_seq,
                payload={
                    "reason": "turn_duration",
                    "last_agent_message": poll.reply_buffer,
                    "turn_id": poll.request_anchor,
                    "session_path": poll.session_path or None,
                    "assistant_uuid": poll.last_assistant_uuid or None,
                },
            )
        )
        poll.next_seq += 1
        poll.reached_turn_boundary = True
    return None


__all__ = ["handle_system_event", "handle_user_event"]
