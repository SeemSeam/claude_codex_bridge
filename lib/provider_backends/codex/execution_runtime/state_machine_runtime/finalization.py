from __future__ import annotations

from dataclasses import replace

from provider_execution.base import ProviderPollResult, ProviderSubmission

from ..reply_logic import select_reply
from .models import CodexPollState


def finalize_poll_result(
    submission: ProviderSubmission,
    poll: CodexPollState,
    *,
    state: dict[str, object],
    now: str | None = None,
) -> ProviderPollResult | None:
    prior_state = submission.runtime_state.get("state") or {}
    prior_session_path = str(submission.runtime_state.get("session_path") or "")
    runtime_state = {
        **submission.runtime_state,
        "state": state,
        "next_seq": poll.next_seq,
        "anchor_seen": poll.anchor_seen,
        "bound_turn_id": poll.bound_turn_id,
        "bound_task_id": poll.bound_task_id,
        "reply_buffer": poll.reply_buffer,
        "last_agent_message": poll.last_agent_message,
        "last_final_answer": poll.last_final_answer,
        "last_assistant_message": poll.last_assistant_message,
        "last_assistant_signature": poll.last_assistant_signature,
        "session_path": poll.session_path,
        "api_error_seen": poll.api_error_seen,
    }
    if poll.anchor_seen and str(runtime_state.get("delivery_state") or "") == "pending_anchor":
        runtime_state["delivery_state"] = "accepted"
        runtime_state["delivery_confirmed_at"] = now or str(runtime_state.get("delivery_confirmed_at") or "")
    updated_submission = replace(
        submission,
        reply=(
            select_reply(
                last_agent_message=poll.last_agent_message,
                last_final_answer=poll.last_final_answer,
                last_assistant_message=poll.last_assistant_message,
                reply_buffer=poll.reply_buffer,
            )
            if poll.items
            else submission.reply
        ),
        runtime_state=runtime_state,
    )
    if not poll.items:
        if prior_state != state or prior_session_path != poll.session_path:
            return ProviderPollResult(submission=updated_submission)
        return None
    return ProviderPollResult(submission=updated_submission, items=tuple(poll.items))


__all__ = ["finalize_poll_result"]
