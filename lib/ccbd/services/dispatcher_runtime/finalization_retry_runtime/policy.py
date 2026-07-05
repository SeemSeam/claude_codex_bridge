from __future__ import annotations

from ccbd.api_models import JobStatus

from ..failure_policy import is_nonretryable_api_failure

DEFAULT_RETRYABLE_REASONS = frozenset({'api_error', 'transport_error'})
DEFAULT_RETRYABLE_ERROR_TYPES = frozenset({'api_error', 'transport_error', 'provider_api_error'})
DEFAULT_RETRYABLE_RUNTIME_REASONS = frozenset({'pane_dead', 'pane_unavailable', 'runtime_unavailable', 'backend_unavailable'})
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
    # Wave 2 must-fix: explicit non-retryable provider error kinds
    # (provider_usage_limit / auth_failed / auth_required / config_error) block
    # automatic retry even when the terminal status is INCOMPLETE, e.g. an empty
    # reply caused by a usage-limit banner. The status guard still rejects other
    # non-failed terminals.
    if is_nonretryable_api_failure(decision):
        return False
    if decision.status.value != JobStatus.FAILED.value:
        return False
    reason = str(decision.reason or '').strip().lower()
    if reason in retryable_reasons(retry_policy):
        return True
    if provider_supports_resume_value and policy_bool(retry_policy.get('retry_runtime_when_resume_supported'), True):
        if reason in retryable_runtime_reasons(retry_policy):
            return True
    error_type = str(decision.diagnostics.get('error_type') or '').strip().lower()
    return error_type in DEFAULT_RETRYABLE_ERROR_TYPES


__all__ = [
    'DEFAULT_RETRYABLE_RUNTIME_REASONS',
    'TIMEOUT_INSPECTION_REASONS',
    'is_retryable_failure',
    'policy_bool',
    'provider_supports_resume',
    'retryable_reasons',
    'retryable_runtime_reasons',
    'safe_int',
]
