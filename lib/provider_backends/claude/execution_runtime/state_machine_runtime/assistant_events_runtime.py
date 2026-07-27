from __future__ import annotations

from completion.models import CompletionItemKind
from provider_execution.base import ProviderSubmission
from provider_execution.common import build_item

from ...protocol import extract_reply_for_req, is_done_text, strip_done_text
from .models import ClaudePollState


def handle_assistant_event(
    submission: ProviderSubmission,
    poll: ClaudePollState,
    event: dict[str, object],
    *,
    now: str,
) -> None:
    text = assistant_text(event)
    subagent_id, subagent_name, is_subagent = assistant_identity(event)
    event_assistant_uuid = assistant_uuid(event)
    if event_assistant_uuid and not is_subagent:
        poll.last_assistant_uuid = event_assistant_uuid
    poll.raw_buffer = append_buffer(poll.raw_buffer, text)

    cleaned = cleaned_assistant_text(text, request_anchor=poll.request_anchor)
    if has_visible_text(cleaned):
        append_chunk_item(
            submission,
            poll,
            event=event,
            cleaned=cleaned,
            subagent_id=subagent_id,
            subagent_name=subagent_name,
            is_subagent=is_subagent,
            event_assistant_uuid=event_assistant_uuid,
            now=now,
        )

    maybe_append_turn_boundary(submission, poll, now=now)
    maybe_append_assistant_end_turn_boundary(
        submission,
        poll,
        event=event,
        cleaned=cleaned,
        is_subagent=is_subagent,
        now=now,
    )


def assistant_text(event: dict[str, object]) -> str:
    return str(event.get("text") or "")


def assistant_identity(event: dict[str, object]) -> tuple[str, str, bool]:
    subagent_id = str(event.get("subagent_id") or "").strip()
    subagent_name = str(event.get("subagent_name") or "").strip()
    is_subagent = bool(subagent_id or subagent_name or event.get("is_sidechain"))
    return subagent_id, subagent_name, is_subagent


def assistant_uuid(event: dict[str, object]) -> str:
    return str(event.get("uuid") or "").strip()


def assistant_stop_reason(event: dict[str, object]) -> str:
    return str(event.get("stop_reason") or "").strip().lower()


def append_buffer(buffer: str, text: str) -> str:
    return f"{buffer}\n{text}".strip() if buffer else text


def cleaned_assistant_text(text: str, *, request_anchor: str) -> str:
    if not request_anchor:
        return text
    return strip_done_text(text, request_anchor)


def has_visible_text(text: str) -> bool:
    return bool(text.strip())


def append_chunk_item(
    submission: ProviderSubmission,
    poll: ClaudePollState,
    *,
    event: dict[str, object],
    cleaned: str,
    subagent_id: str,
    subagent_name: str,
    is_subagent: bool,
    event_assistant_uuid: str,
    now: str,
) -> None:
    poll.reply_buffer = append_buffer(poll.reply_buffer, cleaned)
    if not is_subagent:
        poll.last_assistant_uuid = str(event_assistant_uuid or poll.last_assistant_uuid or "")
    current_assistant_uuid = event_assistant_uuid or poll.last_assistant_uuid or None
    poll.items.append(
        build_item(
            submission,
            kind=CompletionItemKind.ASSISTANT_CHUNK,
            timestamp=now,
            seq=poll.next_seq,
            payload=chunk_payload(
                poll=poll,
                event=event,
                cleaned=cleaned,
                assistant_uuid=current_assistant_uuid,
                subagent_id=subagent_id,
                subagent_name=subagent_name,
            ),
        )
    )
    poll.next_seq += 1


def chunk_payload(
    *,
    poll: ClaudePollState,
    event: dict[str, object],
    cleaned: str,
    assistant_uuid: str | None,
    subagent_id: str,
    subagent_name: str,
) -> dict[str, object]:
    return {
        "text": cleaned,
        "merged_text": poll.reply_buffer,
        "turn_id": poll.request_anchor,
        "session_path": poll.session_path or None,
        "assistant_uuid": assistant_uuid,
        "subagent_id": subagent_id or None,
        "subagent_name": subagent_name or None,
        "stop_reason": event.get("stop_reason"),
    }


def maybe_append_turn_boundary(
    submission: ProviderSubmission,
    poll: ClaudePollState,
    *,
    now: str,
) -> None:
    if poll.reached_turn_boundary:
        return
    if not poll.request_anchor or not is_done_text(poll.raw_buffer, poll.request_anchor):
        return
    reply = extract_reply_for_req(poll.raw_buffer, poll.request_anchor) or poll.reply_buffer
    poll.items.append(
        build_item(
            submission,
            kind=CompletionItemKind.TURN_BOUNDARY,
            timestamp=now,
            seq=poll.next_seq,
            payload=turn_boundary_payload(poll=poll, reply=reply),
        )
    )
    poll.next_seq += 1
    poll.reached_turn_boundary = True


def maybe_append_assistant_end_turn_boundary(
    submission: ProviderSubmission,
    poll: ClaudePollState,
    *,
    event: dict[str, object],
    cleaned: str,
    is_subagent: bool,
    now: str,
) -> None:
    if poll.reached_turn_boundary:
        return
    if is_subagent or not poll.anchor_seen or not poll.request_anchor:
        return
    stop_reason = assistant_stop_reason(event)
    inferred_text_turn = stop_reason == "" and assistant_has_final_text_without_tool_use(event)
    if stop_reason != "end_turn" and not inferred_text_turn:
        return
    reply = poll.reply_buffer if has_visible_text(poll.reply_buffer) else cleaned
    if not has_visible_text(reply):
        return
    event_assistant_uuid = assistant_uuid(event)
    if event_assistant_uuid:
        poll.last_assistant_uuid = event_assistant_uuid
    poll.items.append(
        build_item(
            submission,
            kind=CompletionItemKind.TURN_BOUNDARY,
            timestamp=now,
            seq=poll.next_seq,
            payload=turn_boundary_payload(
                poll=poll,
                reply=reply,
                reason="assistant_text_message" if inferred_text_turn else "assistant_end_turn",
                stop_reason=stop_reason or None,
            ),
        )
    )
    poll.next_seq += 1
    poll.reached_turn_boundary = True


def assistant_has_final_text_without_tool_use(event: dict[str, object]) -> bool:
    content = assistant_message_content(event)
    if content is None:
        return False
    return content_has_text(content) and not content_has_type(content, "tool_use")


def assistant_message_content(event: dict[str, object]) -> object | None:
    entry = event.get("entry")
    if not isinstance(entry, dict):
        return None
    message = entry.get("message")
    if isinstance(message, dict):
        role = str(message.get("role") or entry.get("type") or "").strip().lower()
        if role == "assistant":
            return message.get("content")
    payload = entry.get("payload")
    if isinstance(payload, dict):
        payload_type = str(payload.get("type") or "").strip().lower()
        payload_role = str(payload.get("role") or "").strip().lower()
        if payload_type == "message" and payload_role == "assistant":
            return payload.get("content")
    entry_type = str(entry.get("type") or "").strip().lower()
    if entry_type == "assistant":
        return entry.get("content")
    return None


def content_has_text(content: object) -> bool:
    if isinstance(content, str):
        return bool(content.strip())
    if not isinstance(content, list):
        return False
    for item in content:
        if isinstance(item, str) and item.strip():
            return True
        if not isinstance(item, dict):
            continue
        item_type = str(item.get("type") or "").strip().lower()
        if item_type in {"thinking", "thinking_delta"}:
            continue
        text = item.get("text")
        if not text and item_type == "text":
            text = item.get("content")
        if isinstance(text, str) and text.strip():
            return True
    return False


def content_has_type(content: object, expected: str) -> bool:
    expected_type = expected.strip().lower()
    if not expected_type:
        return False
    if isinstance(content, dict):
        return str(content.get("type") or "").strip().lower() == expected_type
    if not isinstance(content, list):
        return False
    for item in content:
        if isinstance(item, dict) and str(item.get("type") or "").strip().lower() == expected_type:
            return True
    return False


def turn_boundary_payload(
    *,
    poll: ClaudePollState,
    reply: str,
    reason: str = "task_complete",
    stop_reason: str | None = None,
) -> dict[str, object]:
    payload = {
        "reason": reason,
        "last_agent_message": reply,
        "turn_id": poll.request_anchor,
        "session_path": poll.session_path or None,
        "assistant_uuid": poll.last_assistant_uuid or None,
    }
    if stop_reason:
        payload["stop_reason"] = stop_reason
    return payload


__all__ = ["handle_assistant_event"]
