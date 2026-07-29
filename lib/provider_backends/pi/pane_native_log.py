from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from provider_backends.native_cli_support.prompt import clean_native_reply
from provider_core.protocol import REQ_ID_PREFIX


# The pane pi keeps one JSONL per interactive session and headless `pi run`
# jobs drop their own files in the same directory. Scanning the newest few by
# mtime finds the live pane session without having to track which file it owns.
MAX_SESSION_FILES = 12
MAX_CHAIN_STEPS = 2000

# Assistant messages that stop to call a tool are not a finished turn.
_PENDING_STOP_REASONS = frozenset({"tool_use", "tool_calls", "toolcall", "max_tokens"})


@dataclass(frozen=True)
class PiTurnObservation:
    request_seen: bool = False
    completed: bool = False
    reply: str = ""
    session_id: str = ""
    session_path: str = ""
    provider_turn_ref: str = ""
    stop_reason: str = ""
    native_started_at: str | None = None
    native_completed_at: str | None = None


def observe_pi_pane_turn(session_dir: Path, req_id: str) -> PiTurnObservation | None:
    """Locate the pane turn carrying ``req_id`` in pi's own session log.

    The pane pi appends a structured JSONL log under its ``--session-dir``:
    ``thinking`` and ``text`` arrive as separately typed content blocks and
    every message carries ``parentId``, so the reply and the turn boundary are
    both exact. Pane text is never parsed, which is what keeps this free of the
    reasoning-vs-answer heuristics screen scraping would need.

    Returns ``None`` until the request anchor shows up in some session file.
    """
    if not req_id:
        return None
    for path in _candidate_session_files(session_dir):
        observation = _observe_session_file(path, req_id)
        if observation is not None:
            return observation
    return None


def _candidate_session_files(session_dir: Path) -> list[Path]:
    try:
        files = [entry for entry in session_dir.glob("*.jsonl") if entry.is_file()]
    except OSError:
        return []
    files.sort(key=_safe_mtime, reverse=True)
    return files[:MAX_SESSION_FILES]


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _observe_session_file(path: Path, req_id: str) -> PiTurnObservation | None:
    records = _load_records(path)
    if not records:
        return None

    anchor = f"{REQ_ID_PREFIX} {req_id}"
    request_record = None
    for record in records:
        if _record_role(record) != "user":
            continue
        if anchor in _record_text(record):
            request_record = record
    if request_record is None:
        return None

    session_id = _session_id(records) or path.stem
    request_id = str(request_record.get("id") or "")
    last_assistant = _walk_to_last_assistant(records, request_id)

    if last_assistant is None:
        return PiTurnObservation(
            request_seen=True,
            session_id=session_id,
            session_path=str(path),
            provider_turn_ref=f"{session_id}:{req_id}",
            native_started_at=_timestamp(request_record),
        )

    message = last_assistant.get("message") or {}
    stop_reason = str(message.get("stopReason") or message.get("stop_reason") or "").strip()
    reply = clean_native_reply(_assistant_text(last_assistant), req_id)
    completed = bool(stop_reason) and stop_reason.lower() not in _PENDING_STOP_REASONS

    return PiTurnObservation(
        request_seen=True,
        completed=completed and bool(reply),
        reply=reply,
        session_id=session_id,
        session_path=str(path),
        provider_turn_ref=f"{session_id}:{req_id}",
        stop_reason=stop_reason,
        native_started_at=_timestamp(request_record),
        native_completed_at=_timestamp(last_assistant) if completed else None,
    )


def _load_records(path: Path) -> list[dict[str, Any]]:
    try:
        raw = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    records: list[dict[str, Any]] = []
    for line in raw.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError:
            # A partially flushed trailing record is normal while pi is writing.
            continue
        if isinstance(record, dict):
            records.append(record)
    return records


def _walk_to_last_assistant(records: list[dict[str, Any]], start_id: str) -> dict[str, Any] | None:
    """Follow the parentId chain forward and return the turn's final assistant message.

    Tool rounds extend the chain (assistant -> tool result -> assistant), so the
    last assistant node reached before the next anchored user turn is the answer.
    """
    if not start_id:
        return None
    children: dict[str, list[dict[str, Any]]] = {}
    for record in records:
        parent = str(record.get("parentId") or "")
        if parent:
            children.setdefault(parent, []).append(record)

    current = start_id
    last_assistant: dict[str, Any] | None = None
    for _ in range(MAX_CHAIN_STEPS):
        kids = children.get(current) or []
        if not kids:
            break
        node = kids[-1]
        role = _record_role(node)
        if role == "user" and REQ_ID_PREFIX in _record_text(node):
            break
        if role == "assistant":
            last_assistant = node
        node_id = str(node.get("id") or "")
        if not node_id or node_id == current:
            break
        current = node_id
    return last_assistant


def _record_role(record: dict[str, Any]) -> str:
    if record.get("type") != "message":
        return ""
    message = record.get("message")
    if not isinstance(message, dict):
        return ""
    return str(message.get("role") or "").strip().lower()


def _record_text(record: dict[str, Any]) -> str:
    message = record.get("message")
    if not isinstance(message, dict):
        return ""
    return _content_text(message.get("content"), include_thinking=True)


def _assistant_text(record: dict[str, Any]) -> str:
    message = record.get("message")
    if not isinstance(message, dict):
        return ""
    return _content_text(message.get("content"), include_thinking=False).strip()


def _content_text(content: Any, *, include_thinking: bool) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        kind = str(block.get("type") or "").strip().lower()
        if kind == "text":
            parts.append(str(block.get("text") or ""))
        elif kind == "thinking" and include_thinking:
            parts.append(str(block.get("thinking") or ""))
    return "".join(parts)


def _session_id(records: list[dict[str, Any]]) -> str:
    for record in records:
        if record.get("type") == "session":
            return str(record.get("id") or "")
    return ""


def _timestamp(record: dict[str, Any]) -> str | None:
    raw = record.get("timestamp")
    return str(raw) if raw else None


__all__ = ["PiTurnObservation", "observe_pi_pane_turn"]
