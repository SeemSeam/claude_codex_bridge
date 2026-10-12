from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

PI_COMPLETION_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class PiEventBatch:
    events: tuple[dict[str, Any], ...] = ()
    next_offset: int = 0
    trailing_partial: bool = False
    protocol_error: str = ""


@dataclass(frozen=True)
class PiRuntimeObservation:
    ready: bool = False
    busy: bool = False
    runtime_instance_id: str = ""
    active_req_id: str = ""
    native_session_id: str = ""
    native_session_path: str = ""
    native_session_observed_at: str = ""
    next_offset: int = 0
    trailing_partial: bool = False
    protocol_error: str = ""


@dataclass(frozen=True)
class PiAssistantSnapshot:
    text: str = ""
    stop_reason: str = ""
    error: str = ""
    response_id: str = ""
    timestamp: object | None = None


def read_pi_events(path: Path, offset: int = 0) -> PiEventBatch:
    start = max(0, int(offset))
    if not path.is_file():
        return PiEventBatch(next_offset=start)
    try:
        size = path.stat().st_size
    except OSError as exc:
        return PiEventBatch(
            next_offset=start,
            protocol_error=f"event_log_stat_failed:{type(exc).__name__}:{exc}",
        )
    if size < start:
        return PiEventBatch(
            next_offset=start,
            protocol_error=f"event_log_truncated:{size}<{start}",
        )
    try:
        with path.open("rb") as stream:
            stream.seek(start)
            chunk = stream.read()
    except OSError as exc:
        return PiEventBatch(
            next_offset=start,
            protocol_error=f"event_log_read_failed:{type(exc).__name__}:{exc}",
        )
    if not chunk:
        return PiEventBatch(next_offset=start)

    complete_end = chunk.rfind(b"\n") + 1
    trailing_partial = complete_end < len(chunk)
    if complete_end <= 0:
        return PiEventBatch(
            next_offset=start,
            trailing_partial=True,
        )

    events: list[dict[str, Any]] = []
    for index, raw_line in enumerate(chunk[:complete_end].splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            line = raw_line.decode("utf-8", errors="strict")
        except UnicodeDecodeError as exc:
            return PiEventBatch(
                next_offset=start,
                trailing_partial=trailing_partial,
                protocol_error=f"invalid_utf8_record:{index}:{exc}",
            )
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            return PiEventBatch(
                next_offset=start,
                trailing_partial=trailing_partial,
                protocol_error=f"invalid_jsonl_record:{index}:{exc.msg}",
            )
        if not isinstance(event, dict):
            return PiEventBatch(
                next_offset=start,
                trailing_partial=trailing_partial,
                protocol_error=f"non_object_jsonl_record:{index}",
            )
        try:
            schema_version = int(event.get("schema_version"))
        except (TypeError, ValueError):
            schema_version = -1
        if schema_version != PI_COMPLETION_SCHEMA_VERSION:
            return PiEventBatch(
                next_offset=start,
                trailing_partial=trailing_partial,
                protocol_error=(
                    f"unsupported_schema_version:{index}:{schema_version}"
                ),
            )
        if not _text(event.get("type")):
            return PiEventBatch(
                next_offset=start,
                trailing_partial=trailing_partial,
                protocol_error=f"missing_event_type:{index}",
            )
        events.append(event)

    return PiEventBatch(
        events=tuple(events),
        next_offset=start + complete_end,
        trailing_partial=trailing_partial,
    )


def inspect_pi_runtime(
    path: Path,
    *,
    actor: str,
    launch_session_id: str,
) -> PiRuntimeObservation:
    batch = read_pi_events(path)
    if batch.protocol_error:
        return PiRuntimeObservation(
            next_offset=batch.next_offset,
            trailing_partial=batch.trailing_partial,
            protocol_error=batch.protocol_error,
        )

    ready = False
    busy = False
    runtime_instance_id = ""
    active_req_id = ""
    native_session_id = ""
    native_session_path = ""
    native_session_observed_at = ""
    expected_actor = _text(actor)
    expected_session = _text(launch_session_id)
    for event in batch.events:
        if not event_matches_runtime(
            event,
            actor=expected_actor,
            launch_session_id=expected_session,
        ):
            continue
        event_type = normalized_event_type(event)
        event_instance = _text(event.get("runtime_instance_id"))
        if event_type == "extension_ready":
            ready = True
            busy = False
            active_req_id = ""
            runtime_instance_id = event_instance
            native_session_id = _text(event.get("pi_session_id"))
            native_session_path = _text(event.get("pi_session_path"))
            native_session_observed_at = _text(event.get("timestamp"))
            continue
        if not ready or event_instance != runtime_instance_id:
            continue
        if event_type == "agent_start":
            busy = True
            active_req_id = _text(event.get("req_id"))
        elif event_type == "request_superseded" and _text(event.get("req_id")) == active_req_id:
            active_req_id = ""
        elif event_type == "agent_settled":
            busy = False
            active_req_id = ""

    return PiRuntimeObservation(
        ready=ready,
        busy=busy,
        runtime_instance_id=runtime_instance_id,
        active_req_id=active_req_id,
        native_session_id=native_session_id,
        native_session_path=native_session_path,
        native_session_observed_at=native_session_observed_at,
        next_offset=batch.next_offset,
        trailing_partial=batch.trailing_partial,
    )


def event_matches_runtime(
    event: dict[str, Any],
    *,
    actor: str,
    launch_session_id: str,
    runtime_instance_id: str | None = None,
) -> bool:
    if _text(event.get("actor")) != _text(actor):
        return False
    if _text(event.get("launch_session_id")) != _text(launch_session_id):
        return False
    if runtime_instance_id is not None:
        return _text(event.get("runtime_instance_id")) == _text(
            runtime_instance_id
        )
    return True


def normalized_event_type(event: dict[str, Any]) -> str:
    return _text(event.get("type")).lower().replace("-", "_")


def assistant_snapshot(event: dict[str, Any]) -> PiAssistantSnapshot | None:
    assistant = event.get("assistant")
    if not isinstance(assistant, dict):
        return None
    return PiAssistantSnapshot(
        text=_text(assistant.get("text")),
        stop_reason=_normalize_stop_reason(assistant.get("stop_reason")),
        error=_text(assistant.get("error")),
        response_id=_text(assistant.get("response_id")),
        timestamp=assistant.get("timestamp"),
    )


def _normalize_stop_reason(value: object) -> str:
    normalized = _text(value).lower().replace("-", "_")
    if normalized == "tooluse":
        return "tool_use"
    return normalized


def _text(value: object) -> str:
    return str(value or "").strip()


__all__ = [
    "PI_COMPLETION_SCHEMA_VERSION",
    "PiAssistantSnapshot",
    "PiEventBatch",
    "PiRuntimeObservation",
    "assistant_snapshot",
    "event_matches_runtime",
    "inspect_pi_runtime",
    "normalized_event_type",
    "read_pi_events",
]
