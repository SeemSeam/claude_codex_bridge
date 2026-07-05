from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Iterable


USAGE_LIMIT_ERROR_KIND = 'provider_usage_limit'


def bucket_key(provider: str, model: str | None, account: str | None) -> str:
    """Return a stable quota-bucket key for a provider/model/account triple.

    When ``account`` is unset the bucket falls back to provider+model, so
    agents that share the same provider+model (e.g. two codex agents on
    ``gpt-5.5``) land in the same bucket by default.
    """
    provider = str(provider or '').strip().lower()
    model = str(model or '').strip().lower() or None
    account = str(account or '').strip() or None
    parts = [provider]
    if model is not None:
        parts.append(model)
    if account is not None:
        parts.append(account)
    return '|'.join(parts)


@dataclass
class QuotaBucketState:
    degraded_until: str | None = None
    degraded_reason: str | None = None


class QuotaBuckets:
    """Project-scoped provider+model+account quota-bucket state.

    A bucket becomes *degraded* when one of its agents reports a usage-limit
    failure with a provider ``retry_after`` time.  While degraded, dispatch
    should skip starting new jobs for any agent that maps to the same bucket.
    """

    def __init__(self, *, clock=None) -> None:
        self._clock = clock
        self._buckets: dict[str, QuotaBucketState] = {}

    def mark_degraded(
        self,
        key: str,
        retry_after_iso: str,
        *,
        reason: str | None = None,
    ) -> None:
        """Mark ``key`` degraded until ``retry_after_iso`` (ISO-8601)."""
        state = self._buckets.setdefault(key, QuotaBucketState())
        state.degraded_until = retry_after_iso
        state.degraded_reason = reason

    def is_degraded(self, key: str, now_iso: str | None = None) -> bool:
        """Return True if ``key`` is currently degraded."""
        state = self._buckets.get(key)
        if state is None or state.degraded_until is None:
            return False
        now = _parse_iso(now_iso or self._clock())
        until = _parse_iso(state.degraded_until)
        if now is None or until is None:
            return True
        return now < until

    def degraded_until(self, key: str) -> str | None:
        """Return the ISO-8601 time a bucket is degraded until, if any."""
        state = self._buckets.get(key)
        return state.degraded_until if state is not None else None

    def degraded_reason(self, key: str) -> str | None:
        """Return the reason the bucket was marked degraded, if any."""
        state = self._buckets.get(key)
        return state.degraded_reason if state is not None else None

    def clear(self, key: str) -> None:
        """Remove degradation for ``key`` (primarily for tests)."""
        self._buckets.pop(key, None)

    def keys(self) -> Iterable[str]:
        """Iterate over bucket keys that have been touched."""
        return tuple(self._buckets.keys())


def _parse_iso(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        text = str(value).strip()
        if text.endswith('Z'):
            text = text[:-1] + '+00:00'
        return datetime.fromisoformat(text)
    except Exception:
        return None


__all__ = [
    'USAGE_LIMIT_ERROR_KIND',
    'QuotaBucketState',
    'QuotaBuckets',
    'bucket_key',
]
