from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from completion.models import CompletionItem, CompletionItemKind
from provider_execution.base import ProviderSubmission
from provider_execution.common import build_item


@dataclass
class CodexPollState:
    request_anchor: str
    next_seq: int
    anchor_seen: bool
    bound_turn_id: str
    bound_task_id: str
    reply_buffer: str
    last_agent_message: str
    last_final_answer: str
    last_assistant_message: str
    last_assistant_signature: str
    session_path: str
    api_error_seen: bool = False
    items: list[CompletionItem] = field(default_factory=list)
    reached_terminal: bool = False


def build_poll_state(submission: ProviderSubmission) -> CodexPollState:
    from provider_execution.common import request_anchor_from_runtime_state

    return CodexPollState(
        request_anchor=request_anchor_from_runtime_state(submission.runtime_state, fallback=submission.job_id),
        next_seq=int(submission.runtime_state.get("next_seq", 1)),
        anchor_seen=bool(submission.runtime_state.get("anchor_seen", False)),
        bound_turn_id=str(submission.runtime_state.get("bound_turn_id") or ""),
        bound_task_id=str(submission.runtime_state.get("bound_task_id") or ""),
        reply_buffer=str(submission.runtime_state.get("reply_buffer") or ""),
        last_agent_message=str(submission.runtime_state.get("last_agent_message") or ""),
        last_final_answer=str(submission.runtime_state.get("last_final_answer") or ""),
        last_assistant_message=str(submission.runtime_state.get("last_assistant_message") or ""),
        last_assistant_signature=str(submission.runtime_state.get("last_assistant_signature") or ""),
        session_path=str(submission.runtime_state.get("session_path") or ""),
        api_error_seen=bool(submission.runtime_state.get("api_error_seen", False)),
    )


def apply_session_rotation(
    submission: ProviderSubmission,
    poll: CodexPollState,
    *,
    new_session_path: str,
    now: str,
) -> None:
    if not new_session_path or new_session_path == poll.session_path:
        return
    poll.items.append(
        build_item(
            submission,
            kind=CompletionItemKind.SESSION_ROTATE,
            timestamp=now,
            seq=poll.next_seq,
            payload={
                "session_path": new_session_path,
                "provider_session_id": Path(new_session_path).stem,
            },
        )
    )
    poll.next_seq += 1
    poll.session_path = new_session_path
    poll.anchor_seen = bool(submission.runtime_state.get("no_wrap", False))
    poll.bound_turn_id = ""
    poll.bound_task_id = ""
    poll.reply_buffer = ""
    poll.last_agent_message = ""
    poll.last_final_answer = ""
    poll.last_assistant_message = ""
    poll.last_assistant_signature = ""
    poll.api_error_seen = False


__all__ = [
    "CodexPollState",
    "apply_session_rotation",
    "build_poll_state",
]
