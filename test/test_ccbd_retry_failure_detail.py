from __future__ import annotations

from types import SimpleNamespace

from completion.models import CompletionStatus
from ccbd.services.dispatcher_runtime.finalization_retry_runtime.policy import is_retryable_failure
from ccbd.services.dispatcher_runtime.finalization_retry_runtime.details import retry_failure_detail


def test_retry_failure_detail_collects_reason_and_diagnostics() -> None:
    decision = SimpleNamespace(
        reason="api_error",
        diagnostics={
            "error_type": "timeout",
            "error_code": "408",
            "error_message": "request timed out",
            "fault_rule_id": "rule-1",
        },
    )

    detail = retry_failure_detail(decision)

    assert detail == (
        "reason=api_error, error_type=timeout, error_code=408, "
        "error_message=request timed out, fault_rule_id=rule-1"
    )


def test_retry_failure_detail_falls_back_to_default_reason() -> None:
    decision = SimpleNamespace(reason="", diagnostics={})

    assert retry_failure_detail(decision) == "reason=api_error"


def test_retry_policy_honors_delivery_retryable_diagnostic() -> None:
    decision = SimpleNamespace(
        status=CompletionStatus.FAILED,
        reason="codex_prompt_delivery_failed",
        diagnostics={"delivery_retryable": True},
    )

    assert is_retryable_failure(
        decision,
        retry_policy={
            "retryable_reasons": ["api_error", "transport_error"],
            "retryable_runtime_reasons": ["pane_dead", "pane_unavailable"],
        },
        provider_supports_resume_value=True,
    )


def test_retry_policy_does_not_retry_empty_provider_reply_incomplete() -> None:
    decision = SimpleNamespace(
        status=CompletionStatus.INCOMPLETE,
        reason="task_complete_empty_reply",
        diagnostics={"error_type": "empty_provider_reply"},
    )

    assert not is_retryable_failure(
        decision,
        retry_policy={
            "retryable_reasons": ["api_error", "transport_error"],
            "retryable_runtime_reasons": ["pane_dead", "pane_unavailable"],
        },
        provider_supports_resume_value=True,
    )


def test_retry_policy_does_not_retry_empty_hook_reply_incomplete() -> None:
    decision = SimpleNamespace(
        status=CompletionStatus.INCOMPLETE,
        reason="hook_stop_empty_reply",
        diagnostics={"error_type": "empty_provider_reply"},
    )

    assert not is_retryable_failure(
        decision,
        retry_policy={
            "retryable_reasons": ["api_error", "transport_error"],
            "retryable_runtime_reasons": ["pane_dead", "pane_unavailable"],
        },
        provider_supports_resume_value=True,
    )


def test_retry_policy_honors_delivery_retryable_incomplete_diagnostic() -> None:
    decision = SimpleNamespace(
        status=CompletionStatus.INCOMPLETE,
        reason="codex_session_file_missing",
        diagnostics={
            "delivery_failure_kind": "delivery_session_missing",
            "delivery_retryable": True,
        },
    )

    assert is_retryable_failure(
        decision,
        retry_policy={
            "retryable_reasons": ["api_error", "transport_error"],
            "retryable_runtime_reasons": ["pane_dead", "pane_unavailable"],
        },
        provider_supports_resume_value=True,
    )


def test_retry_policy_does_not_override_nonretryable_api_failure() -> None:
    decision = SimpleNamespace(
        status=CompletionStatus.FAILED,
        reason="unauthorized",
        diagnostics={"delivery_retryable": True},
    )

    assert not is_retryable_failure(
        decision,
        retry_policy={
            "retryable_reasons": ["api_error", "transport_error"],
            "retryable_runtime_reasons": ["pane_dead", "pane_unavailable"],
        },
        provider_supports_resume_value=True,
    )
