from __future__ import annotations

from ccbd.api_models import JobStatus

from ..failure_policy import is_nonretryable_api_failure
from ..finalization_runtime.empty_result_notice import is_pure_empty_provider_outcome

DEFAULT_RETRYABLE_REASONS = frozenset({'api_error', 'transport_error'})
DEFAULT_RETRYABLE_ERROR_TYPES = frozenset({'api_error', 'transport_error', 'provider_api_error'})
DEFAULT_RETRYABLE_RUNTIME_REASONS = frozenset({'pane_dead', 'pane_unavailable', 'runtime_unavailable', 'backend_unavailable'})
# Empty terminal results are never automatically retried: the daemon emits a
# one-time caller inspection notice instead (see the empty-result
# caller-notice plan topic). Only attributable transport/runtime diagnostics
# remain retryable for INCOMPLETE outcomes.
DEFAULT_RETRYABLE_INCOMPLETE_REASONS: frozenset[str] = frozenset()
DEFAULT_RETRYABLE_INCOMPLETE_ERROR_TYPES: frozenset[str] = frozenset()
TIMEOUT_INSPECTION_REASONS = frozenset({'timeout'})


def safe_int(value: object, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def retryable_reasons(retry_policy: dict[str, object]) -> frozenset[str]:
    configured = {
        str(item or '').strip().lower()
        for item in (retry_policy.get('retryable_reasons') or ())
        if str(item or '').strip()
    }
    return frozenset(configured or DEFAULT_RETRYABLE_REASONS)


def retryable_runtime_reasons(retry_policy: dict[str, object]) -> frozenset[str]:
    configured = {
        str(item or '').strip().lower()
        for item in (retry_policy.get('retryable_runtime_reasons') or ())
        if str(item or '').strip()
    }
    return frozenset(configured or DEFAULT_RETRYABLE_RUNTIME_REASONS)


def policy_bool(value: object, default: bool) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    lowered = str(value).strip().lower()
    if lowered in {'1', 'true', 'yes', 'on'}:
        return True
    if lowered in {'0', 'false', 'no', 'off'}:
        return False
    return default


def has_retryable_diagnostic(decision) -> bool:
    diagnostics = getattr(decision, 'diagnostics', {}) or {}
    return policy_bool(diagnostics.get('delivery_retryable'), False)


def provider_supports_resume(dispatcher, provider: str) -> bool:
    try:
        manifest = dispatcher._provider_catalog.get(provider)
    except Exception:
        return False
    return bool(manifest.supports_resume)


def is_retryable_failure(
    decision,
    *,
    retry_policy: dict[str, object],
    provider_supports_resume_value: bool,
) -> bool:
    status = decision.status.value
    if status == JobStatus.INCOMPLETE.value:
        if is_pure_empty_provider_outcome(decision):
            # Pure empty provider results are never automatically retried or
            # reactivated, even when a generic delivery_retryable diagnostic
            # is attached: the caller inspection notice owns recovery.
            return False
        if has_retryable_diagnostic(decision):
            return True
        reason = str(decision.reason or '').strip().lower()
        error_type = str(decision.diagnostics.get('error_type') or '').strip().lower()
        return reason in DEFAULT_RETRYABLE_INCOMPLETE_REASONS or error_type in DEFAULT_RETRYABLE_INCOMPLETE_ERROR_TYPES
    if status != JobStatus.FAILED.value:
        return False
    if is_nonretryable_api_failure(decision):
        return False
    if has_retryable_diagnostic(decision):
        return True
    reason = str(decision.reason or '').strip().lower()
    if reason in retryable_reasons(retry_policy):
        return True
    if provider_supports_resume_value and policy_bool(retry_policy.get('retry_runtime_when_resume_supported'), True):
        if reason in retryable_runtime_reasons(retry_policy):
            return True
    error_type = str(decision.diagnostics.get('error_type') or '').strip().lower()
    return error_type in DEFAULT_RETRYABLE_ERROR_TYPES


__all__ = [
    'DEFAULT_RETRYABLE_INCOMPLETE_ERROR_TYPES',
    'DEFAULT_RETRYABLE_INCOMPLETE_REASONS',
    'DEFAULT_RETRYABLE_RUNTIME_REASONS',
    'has_retryable_diagnostic',
    'TIMEOUT_INSPECTION_REASONS',
    'is_retryable_failure',
    'policy_bool',
    'provider_supports_resume',
    'retryable_reasons',
    'retryable_runtime_reasons',
    'safe_int',
]
