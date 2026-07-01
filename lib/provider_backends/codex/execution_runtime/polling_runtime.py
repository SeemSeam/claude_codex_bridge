from __future__ import annotations

from provider_execution.active import prepare_active_poll
from provider_execution.base import ProviderPollResult, ProviderSubmission

from .accelerator import poll_with_accelerator
from .event_reading import read_entries
from .start import state_session_path
from .state_machine import (
    apply_session_rotation,
    build_poll_state,
    finalize_poll_result,
    handle_assistant_entry,
    handle_terminal_entry,
    handle_user_entry,
    update_binding_refs,
)


def poll_submission(submission: ProviderSubmission, *, now: str) -> ProviderPollResult | None:
    prepared = prepare_active_poll(submission, now=now)
    if prepared is None or isinstance(prepared, ProviderPollResult):
        return prepared

    accelerated = poll_with_accelerator(submission, now=now)
    if accelerated is not None:
        return accelerated.result

    state = submission.runtime_state.get("state") or {}
    poll = build_poll_state(submission)
    state = poll_entry_batches(submission, poll, prepared.reader, state, now=now)
    return finalize_poll_result(submission, poll, state=state, now=now)


def poll_entry_batches(submission, poll, reader, state, *, now: str):
    current_state = state
    while True:
        entries, current_state = read_entries(reader, current_state)
        apply_session_state(submission, poll, current_state, now=now)
        if not entries:
            break
        process_entry_batch(submission, poll, entries, now=now)
        if poll.reached_terminal:
            break
    return current_state


def apply_session_state(submission, poll, state, *, now: str) -> None:
    apply_session_rotation(
        submission,
        poll,
        new_session_path=state_session_path(state),
        now=now,
    )


def process_entry_batch(submission, poll, entries, *, now: str) -> None:
    for entry in entries:
        process_entry(submission, poll, entry, now=now)
        if poll.reached_terminal:
            break


def _is_api_error_entry(entry: dict[str, object]) -> bool:
    if str(entry.get("type") or "").strip().lower() != "event_msg":
        return False
    payload = entry.get("payload") or {}
    if not isinstance(payload, dict):
        return False
    return str(payload.get("type") or "").strip().lower() == "api_error"


def process_entry(submission, poll, entry, *, now: str) -> None:
    update_binding_refs(poll, entry)
    if _is_api_error_entry(entry):
        poll.api_error_seen = True
        return
    role = str(entry.get("role") or "").strip().lower()
    if role == "user":
        handle_user_entry(submission, poll, text=str(entry.get("text") or ""), now=now)
        return
    if not poll.anchor_seen:
        return
    if role == "assistant":
        handle_assistant_entry(submission, poll, entry, now=now)
        return
    handle_terminal_entry(submission, poll, entry, now=now)


__all__ = ["poll_submission"]
