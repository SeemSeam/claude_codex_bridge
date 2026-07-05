from __future__ import annotations

from dataclasses import replace

from completion.models import CompletionConfidence, CompletionDecision, CompletionItemKind, CompletionStatus

from provider_execution.base import ProviderPollResult, ProviderSubmission
from provider_execution.common import build_item


def pane_dead_result(
    submission: ProviderSubmission,
    *,
    now: str,
    reason: str = "pane_dead",
    no_reply_reason: str = "agent_unreachable_dead",
    no_reply_detail: dict[str, object] | None = None,
) -> ProviderPollResult:
    item = build_item(
        submission,
        kind=CompletionItemKind.PANE_DEAD,
        timestamp=now,
        seq=int(submission.runtime_state.get("next_seq", 1)),
        payload={"reason": reason},
    )
    updated = replace(
        submission,
        runtime_state={**submission.runtime_state, "mode": "passive", "next_seq": item.cursor.event_seq + 1},
    )
    diagnostics = {
        "reason": reason,
        "no_reply_reason": no_reply_reason,
        "no_reply_detail": dict(no_reply_detail or {}),
    }
    return ProviderPollResult(
        submission=updated,
        items=(item,),
        decision=CompletionDecision(
            terminal=True,
            status=CompletionStatus.FAILED,
            reason=reason,
            confidence=CompletionConfidence.DEGRADED,
            reply="",
            anchor_seen=False,
            reply_started=False,
            reply_stable=False,
            provider_turn_ref=None,
            source_cursor=item.cursor,
            finished_at=now,
            diagnostics=diagnostics,
        ),
    )


def runtime_error_result(
    submission: ProviderSubmission,
    *,
    now: str,
    reason: str,
    error: str = "",
    no_reply_reason: str | None = None,
    no_reply_detail: dict[str, object] | None = None,
) -> ProviderPollResult:
    error_reason = reason or "transport_error"
    item = build_item(
        submission,
        kind=CompletionItemKind.ERROR,
        timestamp=now,
        seq=int(submission.runtime_state.get("next_seq", 1)),
        payload={"reason": error_reason, "error": error or ""},
    )
    updated = replace(
        submission,
        runtime_state={**submission.runtime_state, "mode": "passive", "next_seq": item.cursor.event_seq + 1},
    )
    diagnostics: dict[str, object] = {"reason": error_reason}
    if error:
        diagnostics["error"] = error
        diagnostics["error_message"] = error
    diagnostics["no_reply_reason"] = no_reply_reason or _runtime_error_no_reply_reason(error_reason)
    diagnostics["no_reply_detail"] = dict(no_reply_detail or {})
    return ProviderPollResult(
        submission=updated,
        items=(item,),
        decision=CompletionDecision(
            terminal=True,
            status=CompletionStatus.FAILED,
            reason=error_reason,
            confidence=CompletionConfidence.DEGRADED,
            reply="",
            anchor_seen=False,
            reply_started=False,
            reply_stable=False,
            provider_turn_ref=None,
            source_cursor=item.cursor,
            finished_at=now,
            diagnostics=diagnostics,
        ),
    )


def _runtime_error_no_reply_reason(reason: str) -> str:
    lowered = str(reason or "").lower()
    if "unavailable" in lowered or "missing" in lowered:
        return "agent_unreachable_dead"
    if "auth" in lowered:
        return "provider_auth_failed"
    if "config" in lowered:
        return "provider_config_error"
    return "provider_crashed"


__all__ = ["pane_dead_result", "runtime_error_result"]
