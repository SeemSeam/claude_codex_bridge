from __future__ import annotations

from typing import Any


def session_updated(payload: dict[str, Any]) -> int:
    updated = (payload.get('time') or {}).get('updated')
    try:
        return int(updated)
    except Exception:
        return -1


def reset_state_for_session(state: dict[str, Any], session_id: str) -> dict[str, Any]:
    new_state = dict(state)
    new_state['session_id'] = session_id
    new_state['session_updated'] = -1
    new_state['assistant_count'] = 0
    new_state['last_assistant_id'] = None
    new_state['last_assistant_parent_id'] = None
    new_state['last_assistant_completed'] = None
    new_state['last_assistant_req_id'] = None
    new_state['last_assistant_text_hash'] = None
    new_state['last_assistant_aborted'] = False
    return new_state


def merge_reply_state(
    state: dict[str, Any],
    *,
    session_entry: dict[str, Any],
    current_session_id: str,
    updated_i: int,
    reply_state: dict[str, Any] | None,
) -> dict[str, Any]:
    new_state = dict(state)
    new_state['session_id'] = current_session_id
    new_state['session_updated'] = updated_i
    if (session_entry.get('payload') or {}).get('id') == current_session_id:
        new_state['session_path'] = session_entry.get('path')
    if reply_state:
        new_state.update(reply_state)
    return new_state


def refresh_observed_state(reader, state: dict[str, Any], session_id: str, *, updated_i: int) -> dict[str, Any]:
    new_state = dict(state)
    new_state['session_id'] = session_id
    new_state['session_updated'] = updated_i
    # Compatibility helper: never absorb an undelivered assistant here.
    return new_state


__all__ = ['merge_reply_state', 'refresh_observed_state', 'reset_state_for_session', 'session_updated']
