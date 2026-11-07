from __future__ import annotations

from dataclasses import replace

from completion.models import (CompletionItemKind, CompletionDecision, CompletionStatus,
                               CompletionConfidence, seconds_between)
from provider_execution.active import prepare_active_poll, prepare_active_poll_without_liveness
from provider_execution.base import ProviderPollResult, ProviderSubmission
from provider_execution.common import build_item, request_anchor_from_runtime_state


def poll_submission(
    submission: ProviderSubmission,
    *,
    now: str,
    state_session_path_fn,
) -> ProviderPollResult | None:
    prepare = prepare_active_poll_without_liveness if submission.runtime_state.get("native_v2") else prepare_active_poll
    prepared = prepare(submission, now=now)
    if prepared is None or isinstance(prepared, ProviderPollResult):
        return prepared

    if submission.runtime_state.get('native_v2'):
        from .native_v2 import poll
        return poll(submission, prepared, now=now)

    previous_submission = submission
    state = submission.runtime_state.get("state") or {}
    runtime = _poll_runtime_state(submission)
    items = []

    reply, state = prepared.reader.try_get_message(state)
    if state.get('observation_error'):
        failed_state = {**submission.runtime_state, 'state': state}
        since = failed_state.setdefault('observation_failure_since', now)
        updated = replace(submission, runtime_state=failed_state, diagnostics={
            **(submission.diagnostics or {}), 'observation_error': state['observation_error'],
            'observation_failure_since': since, 'automatic_replay': False})
        decision = None
        if seconds_between(since, now) >= 30:
            decision = CompletionDecision(
                terminal=True, status=CompletionStatus.INCOMPLETE,
                reason='opencode_observation_unavailable', confidence=CompletionConfidence.DEGRADED,
                reply=submission.reply, anchor_seen=bool(runtime['anchor_emitted']),
                reply_started=bool(submission.reply), reply_stable=False, finished_at=now,
                provider_turn_ref=state.get('last_assistant_id'), source_cursor=None,
                diagnostics={**updated.diagnostics, 'provider_may_still_be_running': True})
        return ProviderPollResult(submission=updated, decision=decision)
    if 'observation_failure_since' in submission.runtime_state:
        recovered = dict(submission.runtime_state)
        recovered.pop('observation_failure_since', None)
        diagnostics = dict(submission.diagnostics or {})
        diagnostics.pop('observation_error', None)
        diagnostics.pop('observation_failure_since', None)
        submission = replace(submission, runtime_state=recovered, diagnostics=diagnostics)
    _apply_session_rotation(
        submission,
        runtime,
        items,
        new_session_path=state_session_path_fn(state),
        provider_session_id=state.get("session_id"),
        now=now,
    )
    _emit_anchor(submission, runtime, items, now=now)

    if reply:
        _append_reply_item(
            submission,
            runtime,
            items,
            reply=reply,
            state=state,
            provider_session_id=state.get("session_id"),
            now=now,
        )

    updated = replace(
        submission,
        reply=runtime["reply_buffer"],
        runtime_state={
            **submission.runtime_state,
            "state": state,
            "next_seq": runtime["next_seq"],
            "anchor_emitted": runtime["anchor_emitted"],
            "reply_buffer": runtime["reply_buffer"],
            "session_path": runtime["session_path"],
        },
    )
    if (not items and updated.reply == previous_submission.reply
            and updated.runtime_state == previous_submission.runtime_state
            and updated.diagnostics == previous_submission.diagnostics):
        return None
    return ProviderPollResult(submission=updated, items=tuple(items))


def _poll_runtime_state(submission: ProviderSubmission) -> dict[str, object]:
    runtime_state = submission.runtime_state
    return {
        "request_anchor": request_anchor_from_runtime_state(
            runtime_state,
            fallback=submission.job_id,
        ),
        "next_seq": int(runtime_state.get("next_seq", 1)),
        "anchor_emitted": bool(runtime_state.get("anchor_emitted", False)),
        "no_wrap": bool(runtime_state.get("no_wrap", False)),
        "reply_buffer": str(runtime_state.get("reply_buffer") or ""),
        "session_path": str(runtime_state.get("session_path") or ""),
    }


def _apply_session_rotation(
    submission: ProviderSubmission,
    runtime: dict[str, object],
    items: list,
    *,
    new_session_path: str | None,
    provider_session_id,
    now: str,
) -> None:
    session_path = str(runtime["session_path"])
    if not new_session_path or new_session_path == session_path:
        return
    items.append(
        build_item(
            submission,
            kind=CompletionItemKind.SESSION_ROTATE,
            timestamp=now,
            seq=int(runtime["next_seq"]),
            payload={
                "session_path": new_session_path,
                "provider_session_id": provider_session_id,
            },
            cursor_kwargs={"session_path": new_session_path},
        )
    )
    runtime["next_seq"] = int(runtime["next_seq"]) + 1
    runtime["session_path"] = new_session_path
    runtime["anchor_emitted"] = bool(runtime["no_wrap"])
    runtime["reply_buffer"] = ""


def _emit_anchor(
    submission: ProviderSubmission,
    runtime: dict[str, object],
    items: list,
    *,
    now: str,
) -> None:
    if runtime["anchor_emitted"]:
        return
    session_path = str(runtime["session_path"] or "") or None
    items.append(
        build_item(
            submission,
            kind=CompletionItemKind.ANCHOR_SEEN,
            timestamp=now,
            seq=int(runtime["next_seq"]),
            payload={"turn_id": runtime["request_anchor"]},
            cursor_kwargs={"session_path": session_path},
        )
    )
    runtime["next_seq"] = int(runtime["next_seq"]) + 1
    runtime["anchor_emitted"] = True


def _append_reply_item(
    submission: ProviderSubmission,
    runtime: dict[str, object],
    items: list,
    *,
    reply,
    state: dict[str, object],
    provider_session_id,
    now: str,
) -> None:
    if not _reply_matches_request(runtime, state):
        return
    request_anchor = str(runtime["request_anchor"] or "") or None
    cleaned = _clean_reply(reply)
    if not cleaned:
        return
    runtime["reply_buffer"] = cleaned
    session_path = str(runtime["session_path"] or "") or None
    message_id = _coerce_str(state.get("last_assistant_id"))
    parent_id = _coerce_str(state.get("last_assistant_parent_id"))
    items.append(
        build_item(
            submission,
            kind=CompletionItemKind.ASSISTANT_FINAL,
            timestamp=now,
            seq=int(runtime["next_seq"]),
            payload={
                "text": cleaned,
                "reply": cleaned,
                "final_answer": cleaned,
                "turn_id": request_anchor,
                "session_path": session_path,
                "provider_session_id": provider_session_id,
                "provider_turn_ref": message_id,
                "message_id": message_id,
                "parent_message_id": parent_id,
                "completed_at": state.get("last_assistant_completed"),
            },
            cursor_kwargs={"session_path": session_path},
        )
    )
    runtime["next_seq"] = int(runtime["next_seq"]) + 1
    if not _reply_completed(state):
        return
    items.append(
        build_item(
            submission,
            kind=CompletionItemKind.TURN_BOUNDARY,
            timestamp=now,
            seq=int(runtime["next_seq"]),
            payload={
                "reason": "assistant_completed",
                "last_agent_message": cleaned,
                "turn_id": request_anchor,
                "session_path": session_path,
                "provider_turn_ref": message_id,
                "message_id": message_id,
                "parent_message_id": parent_id,
                "provider_session_id": provider_session_id,
                "completed_at": state.get("last_assistant_completed"),
            },
            cursor_kwargs={"session_path": session_path},
        )
    )
    runtime["next_seq"] = int(runtime["next_seq"]) + 1


def _reply_matches_request(runtime: dict[str, object], state: dict[str, object]) -> bool:
    if bool(runtime.get("no_wrap", False)):
        return True
    request_anchor = str(runtime.get("request_anchor") or "").strip().lower()
    observed_req_id = str(state.get("last_assistant_req_id") or "").strip().lower()
    return bool(request_anchor and observed_req_id and observed_req_id == request_anchor)


def _reply_completed(state: dict[str, object]) -> bool:
    completed = state.get("last_assistant_completed")
    try:
        return completed is not None
    except Exception:
        return False


def _coerce_str(value: object) -> str | None:
    text = str(value or "").strip()
    return text or None


def _clean_reply(reply) -> str:
    return str(reply).strip()


__all__ = ["poll_submission"]
