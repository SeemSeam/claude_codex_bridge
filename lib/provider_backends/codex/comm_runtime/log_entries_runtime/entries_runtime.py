from __future__ import annotations

from typing import Any

from .messages import extract_message, extract_user_message


def extract_entry(entry: dict) -> dict[str, Any] | None:
    if is_native_subagent_entry(entry):
        return None
    base, payload = base_entry(entry)
    direct = direct_entry(base, entry, payload=payload)
    if direct is not None:
        return direct
    return fallback_entry(base, entry, payload=payload)


def extract_event(entry: dict) -> tuple[str, str] | None:
    normalized = extract_entry(entry)
    if normalized is None:
        return None
    role = str(normalized.get("role") or "").strip().lower()
    text = str(normalized.get("text") or "").strip()
    if role in {"user", "assistant"} and text:
        return role, text
    return None


def base_entry(entry: dict) -> tuple[dict[str, Any], dict[str, Any]]:
    entry_type = str(entry.get("type") or "").strip()
    payload = normalized_payload(entry.get("payload"))
    return (
        {
            "entry_type": entry_type,
            "payload_type": str(payload.get("type") or "").strip(),
            "timestamp": entry.get("timestamp"),
            "phase": payload.get("phase"),
            "turn_id": payload.get("turn_id"),
            "task_id": payload.get("task_id"),
            "reason": payload.get("reason"),
            "last_agent_message": payload.get("last_agent_message"),
            "error": normalized_error(payload.get("error")),
            "entry": entry,
        },
        payload,
    )


def normalized_error(value: object) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    return value


def normalized_payload(payload: object) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    return {}


def is_native_subagent_entry(entry: dict) -> bool:
    entry_type = str(entry.get("type") or "").strip().lower()
    payload = normalized_payload(entry.get("payload"))
    payload_type = str(payload.get("type") or "").strip().lower()
    if entry_type == "event_msg" and payload_type == "sub_agent_activity":
        return True
    return entry_type == "response_item" and payload_type == "agent_message"


def direct_entry(base: dict[str, Any], entry: dict, *, payload: dict[str, Any]) -> dict[str, Any] | None:
    entry_type = str(base["entry_type"])
    payload_type = str(base["payload_type"])
    if entry_type == "response_item" and payload_type == "message":
        return response_message_entry(base, entry, role=payload_role(payload))
    if entry_type != "event_msg":
        return None
    return event_message_entry(base, entry, payload=payload, payload_type=payload_type)


def response_message_entry(base: dict[str, Any], entry: dict, *, role: str) -> dict[str, Any] | None:
    if role == "user":
        return entry_with_text(base, role="user", text=extract_user_message(entry) or "")
    if role == "assistant":
        return entry_with_text(base, role="assistant", text=extract_message(entry) or "")
    return None


def event_message_entry(
    base: dict[str, Any],
    entry: dict,
    *,
    payload: dict[str, Any],
    payload_type: str,
) -> dict[str, Any] | None:
    if payload_type == "user_message":
        return entry_with_text(base, role="user", text=extract_user_message(entry) or "")
    if payload_type == "task_started":
        return system_entry(base, text="", reason="task_started")
    if payload_type in {"agent_message", "assistant_message", "assistant", "assistant_response", "message"}:
        role = payload_role(payload)
        if role == "user":
            return entry_with_text(base, role="user", text=extract_user_message(entry) or "")
        return entry_with_text(base, role="assistant", text=extract_message(entry) or "")
    if payload_type == "task_complete":
        return system_entry(base, text=str(payload.get("last_agent_message") or "").strip(), reason="task_complete")
    if payload_type == "turn_aborted":
        return system_entry(
            base,
            text=str(payload.get("message") or "").strip(),
            reason=payload.get("reason") or "turn_aborted",
        )
    return None


def fallback_entry(base: dict[str, Any], entry: dict, *, payload: dict[str, Any]) -> dict[str, Any] | None:
    user_text = stripped_text(extract_user_message(entry))
    if user_text:
        return entry_with_text(base, role="user", text=user_text)
    assistant_text = stripped_text(extract_message(entry))
    if assistant_text:
        role = payload_role(payload) or "assistant"
        return entry_with_text(base, role=role, text=assistant_text)
    return None


def payload_role(payload: dict[str, Any]) -> str:
    return str(payload.get("role") or "").strip().lower()


def stripped_text(value: object) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def entry_with_text(base: dict[str, Any], *, role: str, text: str) -> dict[str, Any]:
    return {**base, "role": role, "text": text}


def system_entry(base: dict[str, Any], *, text: str, reason: object) -> dict[str, Any]:
    return {**base, "role": "system", "text": text, "reason": reason}


__all__ = ["extract_entry", "extract_event", "is_native_subagent_entry"]
