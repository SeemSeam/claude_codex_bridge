from __future__ import annotations

from completion.models import CompletionConfidence, CompletionDecision, CompletionStatus
from ccbd.no_reply_reason import NoReplyReason, resolve_no_reply_reason


def test_explicit_no_reply_reason_preserves_provider_detail() -> None:
    decision = CompletionDecision(
        terminal=True,
        status=CompletionStatus.FAILED,
        reason="provider_usage_limit",
        confidence=CompletionConfidence.DEGRADED,
        reply="",
        anchor_seen=True,
        reply_started=False,
        reply_stable=False,
        provider_turn_ref="job_1",
        source_cursor=None,
        finished_at="2026-04-06T00:00:01Z",
        diagnostics={
            "no_reply_reason": "provider_usage_limit",
            "no_reply_detail": {
                "pane_signal_state": "usage_limit",
                "retry_after": "2026-04-06T01:00:00Z",
            },
        },
    )

    resolved = resolve_no_reply_reason(decision)

    assert resolved is not None
    reason, detail = resolved
    assert reason is NoReplyReason.provider_usage_limit
    assert detail == {
        "source": "explicit",
        "no_reply_reason": "provider_usage_limit",
        "pane_signal_state": "usage_limit",
        "retry_after": "2026-04-06T01:00:00Z",
    }
