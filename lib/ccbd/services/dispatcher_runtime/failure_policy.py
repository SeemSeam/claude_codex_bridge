from __future__ import annotations

from completion.models import CompletionDecision

_NON_RETRYABLE_API_AUTH_CODES = frozenset(
    {
        'unauthorized',
        'authenticationerror',
        'authenticationfailed',
        'invalidapikey',
        'invalidauthtoken',
        'invalidtoken',
        'notauthenticated',
        'notloggedin',
        'loginrequired',
        'missingapikey',
    }
)
_NON_RETRYABLE_API_PERMISSION_CODES = frozenset(
    {
        'permissiondenied',
        'accessdenied',
        'forbidden',
        'notauthorized',
    }
)
_NON_RETRYABLE_API_BILLING_CODES = frozenset(
    {
        'insufficientquota',
        'quotaexceeded',
        'paymentrequired',
        'billinghardlimitreached',
        'billingnotactive',
        'insufficientbalance',
        'balanceexhausted',
        'creditbalancetoolow',
    }
)
_NON_RETRYABLE_API_AUTH_MESSAGE_MARKERS = (
    'unauthorized',
    'not logged in',
    'login required',
    'authentication failed',
    'invalid api key',
    'invalid auth',
    'invalid token',
    'run codex login',
    'run login',
)

# Wave 2: explicit provider error_kind values (surfaced by Wave 1 from pane
# content / finish-hook diagnostics) that mark a turn non-retryable regardless
# of message text. Mapped to the legacy classification categories so existing
# reply rendering and event recording keep working unchanged.
_NON_RETRYABLE_ERROR_KIND_TO_CATEGORY = {
    'provider_usage_limit': 'billing',
    'provider_auth_failed': 'authentication',
    'provider_auth_required': 'authentication',
    'provider_config_error': 'authentication',
}
_NON_RETRYABLE_API_PERMISSION_MESSAGE_MARKERS = (
    'permission denied',
    'access denied',
    'forbidden',
    'not authorized',
)
_NON_RETRYABLE_API_BILLING_MESSAGE_MARKERS = (
    'insufficient quota',
    'quota exceeded',
    'payment required',
    'insufficient balance',
    'billing',
    'credit balance too low',
)


def normalized_error_token(value: object) -> str:
    lowered = str(value or '').strip().lower()
    if not lowered:
        return ''
    return ''.join(ch for ch in lowered if ch.isalnum())


def failure_message_text(decision: CompletionDecision) -> str:
    return ' '.join(
        str(value or '').strip().lower()
        for value in (
            decision.diagnostics.get('error_message'),
            decision.diagnostics.get('fault_message'),
            decision.diagnostics.get('error'),
            decision.diagnostics.get('message'),
            decision.diagnostics.get('text'),
        )
        if str(value or '').strip()
    )


def nonretryable_api_failure_kind(decision: CompletionDecision) -> str | None:
    if decision.status.value not in {'failed', 'incomplete'}:
        return None
    # Wave 2: an explicit provider error_kind (from pane content / finish-hook)
    # is the most authoritative signal; honor it before falling back to the
    # legacy token/marker heuristics. Classify both FAILED and INCOMPLETE
    # terminals so usage-limit empty-output turns fast-fail instead of retry.
    error_kind = nonretryable_provider_error_kind_from_diagnostics(decision.diagnostics)
    if error_kind is not None:
        return _NON_RETRYABLE_ERROR_KIND_TO_CATEGORY[error_kind]
    tokens = {
        normalized_error_token(decision.reason),
        normalized_error_token(decision.diagnostics.get('error_type')),
        normalized_error_token(decision.diagnostics.get('error_code')),
    }
    tokens.discard('')
    if tokens & _NON_RETRYABLE_API_AUTH_CODES:
        return 'authentication'
    if tokens & _NON_RETRYABLE_API_PERMISSION_CODES:
        return 'permission'
    if tokens & _NON_RETRYABLE_API_BILLING_CODES:
        return 'billing'

    message_text = failure_message_text(decision)
    if any(marker in message_text for marker in _NON_RETRYABLE_API_AUTH_MESSAGE_MARKERS):
        return 'authentication'
    if any(marker in message_text for marker in _NON_RETRYABLE_API_PERMISSION_MESSAGE_MARKERS):
        return 'permission'
    if any(marker in message_text for marker in _NON_RETRYABLE_API_BILLING_MESSAGE_MARKERS):
        return 'billing'
    return None


def is_nonretryable_api_failure(decision: CompletionDecision) -> bool:
    return nonretryable_api_failure_kind(decision) is not None


# Wave 2: the set of explicit provider error_kind values that should block
# retry. Exposed for the manual retry path (which reads attempt/reply
# diagnostics directly rather than a CompletionDecision).
NON_RETRYABLE_PROVIDER_ERROR_KINDS = frozenset(_NON_RETRYABLE_ERROR_KIND_TO_CATEGORY)


def nonretryable_provider_error_kind_from_diagnostics(diagnostics: dict | None) -> str | None:
    """Return the explicit non-retryable provider error_kind from raw diagnostics.

    Used at the manual retry entry where only the attempt/reply diagnostics are
    available (no CompletionDecision). Returns the error_kind string itself
    (e.g. ``provider_usage_limit``) when present, or ``None`` when the turn is
    retryable / has no explicit error_kind.
    """
    if not diagnostics:
        return None
    error_kind = str(diagnostics.get('error_kind') or '').strip().lower()
    if error_kind and error_kind in NON_RETRYABLE_PROVIDER_ERROR_KINDS:
        return error_kind
    return None


__all__ = [
    'NON_RETRYABLE_PROVIDER_ERROR_KINDS',
    'failure_message_text',
    'is_nonretryable_api_failure',
    'nonretryable_api_failure_kind',
    'nonretryable_provider_error_kind_from_diagnostics',
    'normalized_error_token',
]
