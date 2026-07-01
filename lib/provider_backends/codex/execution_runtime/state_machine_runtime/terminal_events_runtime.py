from __future__ import annotations

from completion.models import CompletionItemKind
from provider_execution.base import ProviderSubmission
from provider_execution.common import build_item

from ..reply_logic import abort_status, clean_codex_reply_text, select_reply
from .models import CodexPollState


def handle_terminal_entry(
    submission: ProviderSubmission,
    poll: CodexPollState,
    entry: dict[str, object],
    *,
    now: str,
) -> None:
    payload_type = terminal_payload_type(entry)
    if payload_type == "task_complete":
        append_task_complete_item(submission, poll, entry=entry, now=now)
        return
    if payload_type == "turn_aborted":
        append_turn_aborted_item(submission, poll, entry=entry, now=now)


def terminal_payload_type(entry: dict[str, object]) -> str:
    return str(entry.get("payload_type") or entry.get("entry_type") or "").strip().lower()


def append_task_complete_item(
    submission: ProviderSubmission,
    poll: CodexPollState,
    *,
    entry: dict[str, object],
    now: str,
) -> None:
    terminal_text = str(entry.get("last_agent_message") or "").strip()
    if terminal_text:
        poll.last_agent_message = clean_codex_reply_text(terminal_text, poll.request_anchor).strip()
    poll.items.append(
        build_item(
            submission,
            kind=CompletionItemKind.TURN_BOUNDARY,
            timestamp=now,
            seq=poll.next_seq,
            payload=task_complete_payload(poll),
        )
    )
    poll.next_seq += 1
    poll.reached_terminal = True


def append_turn_aborted_item(
    submission: ProviderSubmission,
    poll: CodexPollState,
    *,
    entry: dict[str, object],
    now: str,
) -> None:
    reason = str(entry.get("reason") or "turn_aborted").strip() or "turn_aborted"
    error_text = str(entry.get("text") or "").strip()
    poll.items.append(
        build_item(
            submission,
            kind=CompletionItemKind.TURN_ABORTED,
            timestamp=now,
            seq=poll.next_seq,
            payload=turn_aborted_payload(poll, reason=reason, error_text=error_text),
        )
    )
    poll.next_seq += 1
    poll.reached_terminal = True


def task_complete_payload(poll: CodexPollState) -> dict[str, object]:
    payload: dict[str, object] = {
        "reason": "task_complete",
        "last_agent_message": selected_reply(poll),
        "api_error_seen": poll.api_error_seen,
    }
    add_binding_payload(payload, poll)
    return payload


def turn_aborted_payload(poll: CodexPollState, *, reason: str, error_text: str) -> dict[str, object]:
    payload: dict[str, object] = {
        "reason": reason,
        "status": abort_status(reason),
        "last_agent_message": selected_reply(poll),
    }
    if error_text:
        payload["text"] = error_text
        payload["error_message"] = error_text
    add_binding_payload(payload, poll)
    return payload


def selected_reply(poll: CodexPollState) -> str:
    return select_reply(
        last_agent_message=poll.last_agent_message,
        last_final_answer=poll.last_final_answer,
        last_assistant_message=poll.last_assistant_message,
        reply_buffer=poll.reply_buffer,
    )


def add_binding_payload(payload: dict[str, object], poll: CodexPollState) -> None:
    if poll.bound_turn_id or poll.request_anchor:
        payload["turn_id"] = poll.bound_turn_id or poll.request_anchor
    if poll.bound_task_id:
        payload["task_id"] = poll.bound_task_id
    if poll.session_path:
        payload["session_path"] = poll.session_path


__all__ = ["handle_terminal_entry"]
