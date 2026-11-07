from __future__ import annotations

import time
from typing import Any, Callable
from contextlib import nullcontext


def resolve_active_session(
    state: dict[str, Any],
    *,
    session_id: str | None,
    session_entry: dict[str, Any] | None,
    reset_state_for_session_fn: Callable,
) -> tuple[str | None, str | None, dict[str, Any]]:
    if not session_entry:
        return session_id, None, state
    current_session_id = payload_session_id(session_entry)
    session_id, state = advance_session_state(
        state,
        session_id=session_id,
        current_session_id=current_session_id,
        reset_state_for_session_fn=reset_state_for_session_fn,
    )
    return session_id, current_session_id, state


def session_updated_value(session_entry: dict[str, Any], *, session_updated_fn: Callable) -> int:
    return session_updated_fn(session_entry.get("payload") or {})


def should_finish_read_cycle(*, block: bool, reader, deadline: float) -> bool:
    if not block:
        return True
    return sleep_or_timeout(reader, deadline)


def read_since(
    reader,
    state: dict[str, Any],
    timeout: float,
    block: bool,
    *,
    get_latest_session_fn: Callable,
    find_reply_fn: Callable,
    merge_reply_state_fn: Callable,
    refresh_observed_state_fn: Callable,
    reset_state_for_session_fn: Callable,
    session_updated_fn: Callable,
) -> tuple[str | None, dict[str, Any]]:
    deadline = time.monotonic() + timeout
    session_id = state_session_id(state)
    while True:
        storage = getattr(reader, '_storage', None)
        scope = storage.observation() if storage is not None else nullcontext()
        with scope:
            session_entry = get_latest_session_fn(reader)
            session_id, current_session_id, candidate = resolve_active_session(
                state, session_id=session_id, session_entry=session_entry,
                reset_state_for_session_fn=reset_state_for_session_fn)
            if current_session_id:
                updated_i = session_updated_value(session_entry, session_updated_fn=session_updated_fn)
                previous = int(candidate.get('session_updated') or -1)
                last_scan = getattr(reader, '_last_reply_scan', float('-inf'))
                if should_scan_session(block=block, updated_i=updated_i, prev_updated=previous,
                                       last_forced_read=last_scan, force_read_interval=reader._force_read_interval):
                    scanned_at, reply, candidate = scan_current_session(
                        reader, candidate, current_session_id=current_session_id,
                        session_entry=session_entry, updated_i=updated_i, prev_updated=previous,
                        block=block, last_forced_read=last_scan, find_reply_fn=find_reply_fn,
                        merge_reply_state_fn=merge_reply_state_fn,
                        refresh_observed_state_fn=refresh_observed_state_fn)
                    reader._last_reply_scan = scanned_at
                    state = candidate
                    if reply:
                        return reply, state
        if should_finish_read_cycle(block=block, reader=reader, deadline=deadline):
            return None, state


def state_session_id(state: dict[str, Any]) -> str | None:
    session_id = state.get("session_id")
    if isinstance(session_id, str) and session_id:
        return session_id
    return None


def payload_session_id(session_entry: dict[str, Any]) -> str | None:
    payload = session_entry.get("payload") or {}
    session_id = payload.get("id")
    return session_id if isinstance(session_id, str) else None


def advance_session_state(
    state: dict[str, Any],
    *,
    session_id: str | None,
    current_session_id: str | None,
    reset_state_for_session_fn: Callable,
) -> tuple[str | None, dict[str, Any]]:
    if session_id and current_session_id and current_session_id != session_id:
        return current_session_id, reset_state_for_session_fn(state, current_session_id)
    if not session_id:
        return current_session_id, state
    return session_id, state


def should_scan_session(
    *,
    block: bool,
    updated_i: int,
    prev_updated: int,
    last_forced_read: float,
    force_read_interval: float,
) -> bool:
    if updated_i != prev_updated:
        return True
    return (time.monotonic() - last_forced_read) >= force_read_interval


def scan_current_session(
    reader,
    state: dict[str, Any],
    *,
    current_session_id: str,
    session_entry: dict[str, Any],
    updated_i: int,
    prev_updated: int,
    block: bool,
    last_forced_read: float,
    find_reply_fn: Callable,
    merge_reply_state_fn: Callable,
    refresh_observed_state_fn: Callable,
) -> tuple[float, str | None, dict[str, Any]]:
    # Do not perform a second refresh: it can consume a just-completed reply
    # into the baseline without ever delivering it. One observation, one merge.
    scanned_at = time.monotonic()
    reply, reply_state = find_reply_fn(reader, current_session_id, state)
    merged = merge_reply_state_fn(
        state, session_entry=session_entry, current_session_id=current_session_id,
        updated_i=updated_i, reply_state=reply_state)
    return scanned_at, reply, merged


def sleep_or_timeout(reader, deadline: float) -> bool:
    time.sleep(reader._poll_interval)
    return time.monotonic() >= deadline


__all__ = [
    "advance_session_state",
    "payload_session_id",
    "read_since",
    "resolve_active_session",
    "scan_current_session",
    "session_updated_value",
    "should_scan_session",
    "should_finish_read_cycle",
    "sleep_or_timeout",
    "state_session_id",
]
