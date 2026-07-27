from __future__ import annotations

import re
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


def handle_user_event(
    submission: ProviderSubmission,
    poll: ClaudePollState,
    *,
    text: str,
    now: str,
) -> None:
    if not has_outer_request_anchor(text, request_anchor=poll.request_anchor):
        return
    poll.prompt_activated = True
    if not poll.anchor_seen:
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


def handle_prompt_lifecycle_event(
    submission: ProviderSubmission,
    poll: ClaudePollState,
    event: dict[str, object],
    *,
    now: str,
) -> None:
    phase = str(event.get("prompt_phase") or "").strip().lower()
    text = str(event.get("text") or "")
    if phase == "enqueued":
        if has_outer_request_anchor(text, request_anchor=poll.request_anchor):
            poll.prompt_enqueued = True
            poll.prompt_enqueue_uuid = str(event.get("uuid") or "").strip()
        return
    if phase == "dequeued":
        if poll.prompt_enqueued and not poll.prompt_activated:
            poll.queue_dequeue_observed = True
        return
    if phase != "activated" or not has_outer_request_anchor(text, request_anchor=poll.request_anchor):
        return
    source_uuid = str(event.get("source_uuid") or "").strip()
    poll.prompt_activation_uuid = source_uuid or str(event.get("uuid") or "").strip()
    handle_user_event(submission, poll, text=text, now=now)


def has_outer_request_anchor(text: str, *, request_anchor: str) -> bool:
    if not request_anchor:
        return False
    from ...protocol import REQ_ID_PREFIX

    pattern = rf"^\s*{re.escape(REQ_ID_PREFIX)}\s*{re.escape(request_anchor)}(?=\s|$)"
    return re.search(pattern, str(text or ""), flags=re.IGNORECASE) is not None


def is_top_level_user_prompt(event: dict[str, object] | None) -> bool:
    if event is None:
        return True
    if event.get("subagent_id") or event.get("subagent_name"):
        return False
    if bool(event.get("is_sidechain")):
        return False
    entry = event.get("entry")
    if not isinstance(entry, dict):
        return True
    if entry.get("toolUseResult") is not None or bool(entry.get("isMeta", False)):
        return False
    if bool(entry.get("isSidechain")):
        return False
    message = entry.get("message")
    if not isinstance(message, dict):
        return True
    content = message.get("content")
    if not isinstance(content, list):
        return True
    return not any(
        isinstance(item, dict) and str(item.get("type") or "").strip().lower() == "tool_result"
        for item in content
    )


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
                "prompt_enqueued": poll.prompt_enqueued,
                "queue_dequeue_observed": poll.queue_dequeue_observed,
                "prompt_activated": poll.prompt_activated,
                "prompt_enqueue_uuid": poll.prompt_enqueue_uuid,
                "prompt_activation_uuid": poll.prompt_activation_uuid,
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


__all__ = [
    "handle_prompt_lifecycle_event",
    "handle_system_event",
    "handle_user_event",
    "is_top_level_user_prompt",
]
