from __future__ import annotations

from typing import Any

from .entries import extract_message


def structured_event(entry: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None
    entry_type = str(entry.get("type") or "").strip().lower()
    subtype = _optional_text(entry.get("subtype"))
    uuid = _optional_text(entry.get("uuid"), lowercase=False)
    parent_uuid = _optional_text(entry.get("parentUuid"), lowercase=False)

    user_msg = extract_message(entry, "user")
    if user_msg:
        return _event_record(
            role="user",
            text=user_msg,
            entry_type=entry_type,
            subtype=subtype,
            uuid=uuid,
            parent_uuid=parent_uuid,
            stop_reason=None,
            entry=entry,
        )

    assistant_msg = extract_message(entry, "assistant")
    if assistant_msg:
        return _event_record(
            role="assistant",
            text=assistant_msg,
            entry_type=entry_type,
            subtype=subtype,
            uuid=uuid,
            parent_uuid=parent_uuid,
            stop_reason=_assistant_stop_reason(entry),
            entry=entry,
        )

    if _is_assistant_non_text_entry(entry):
        return _event_record(
            role="assistant",
            text="",
            entry_type=entry_type,
            subtype=subtype,
            uuid=uuid,
            parent_uuid=parent_uuid,
            stop_reason=_assistant_stop_reason(entry),
            entry=entry,
        )

    if entry_type == "system":
        return _event_record(
            role="system",
            text="",
            entry_type=entry_type,
            subtype=subtype,
            uuid=uuid,
            parent_uuid=parent_uuid,
            stop_reason=None,
            entry=entry,
        )

    prompt_lifecycle = _prompt_lifecycle_event(
        entry,
        entry_type=entry_type,
        subtype=subtype,
        uuid=uuid,
        parent_uuid=parent_uuid,
    )
    if prompt_lifecycle is not None:
        return prompt_lifecycle
    return None


def _assistant_stop_reason(entry: dict[str, Any]) -> str | None:
    message = entry.get("message")
    if not isinstance(message, dict):
        return None
    return _optional_text(message.get("stop_reason"), lowercase=False)


def _is_assistant_non_text_entry(entry: dict[str, Any]) -> bool:
    if str(entry.get("type") or "").strip().lower() != "assistant":
        return False
    message = entry.get("message")
    if not isinstance(message, dict):
        return False
    if str(message.get("role") or "").strip().lower() != "assistant":
        return False
    content = message.get("content")
    if not isinstance(content, list) or not content:
        return False
    allowed = {"thinking", "thinking_delta", "tool_use"}
    return all(
        isinstance(item, dict) and str(item.get("type") or "").strip().lower() in allowed
        for item in content
    )


def _prompt_lifecycle_event(
    entry: dict[str, Any],
    *,
    entry_type: str,
    subtype: str | None,
    uuid: str | None,
    parent_uuid: str | None,
) -> dict[str, Any] | None:
    if entry_type == "queue-operation":
        operation = _optional_text(entry.get("operation"))
        if operation not in {"enqueue", "dequeue"}:
            return None
        text = str(entry.get("content") or "").strip() if operation == "enqueue" else ""
        event = _event_record(
            role="prompt_lifecycle",
            text=text,
            entry_type=entry_type,
            subtype=subtype,
            uuid=uuid,
            parent_uuid=parent_uuid,
            stop_reason=None,
            entry=entry,
        )
        event["prompt_phase"] = "enqueued" if operation == "enqueue" else "dequeued"
        return event

    if entry_type != "attachment":
        return None
    attachment = entry.get("attachment")
    if not isinstance(attachment, dict):
        return None
    if str(attachment.get("type") or "").strip().lower() != "queued_command":
        return None
    event = _event_record(
        role="prompt_lifecycle",
        text=str(attachment.get("prompt") or "").strip(),
        entry_type=entry_type,
        subtype=subtype,
        uuid=uuid,
        parent_uuid=parent_uuid,
        stop_reason=None,
        entry=entry,
    )
    event["prompt_phase"] = "activated"
    event["source_uuid"] = _optional_text(attachment.get("source_uuid"), lowercase=False)
    return event


def _event_record(
    *,
    role: str,
    text: str,
    entry_type: str,
    subtype: str | None,
    uuid: str | None,
    parent_uuid: str | None,
    stop_reason: str | None,
    entry: dict[str, Any],
) -> dict[str, Any]:
    event = {
        "role": role,
        "text": text,
        "entry_type": entry_type,
        "subtype": subtype,
        "uuid": uuid,
        "parent_uuid": parent_uuid,
        "stop_reason": stop_reason,
        "entry": entry,
    }
    subagent_id = _optional_text(
        entry.get("subagent_id") or entry.get("agentId") or entry.get("agent_id"),
        lowercase=False,
    )
    # NOTE: entry.slug is the Claude session-name slug (written on every entry
    # of a named session since Claude ~2.1.x), not a subagent marker; treating
    # it as one mislabels every main-chain event as subagent activity, which
    # hides the request anchor and strands completion tracking.
    subagent_name = _optional_text(
        entry.get("subagent_name") or entry.get("agentName"),
        lowercase=False,
    )
    if subagent_id:
        event["subagent_id"] = subagent_id
    if subagent_name:
        event["subagent_name"] = subagent_name
    if bool(entry.get("isSidechain")):
        event["is_sidechain"] = True
    return event


def _optional_text(value: object, *, lowercase: bool = True) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    return text.lower() if lowercase else text


__all__ = ["structured_event"]
