from __future__ import annotations

import hashlib
from datetime import datetime
from pathlib import Path

from ccbd.api_models import JobRecord
from provider_execution.base import ProviderRuntimeContext


def resolve_work_dir(job: JobRecord, context: ProviderRuntimeContext | None) -> Path | None:
    candidate = (context.workspace_path if context else None) or job.workspace_path
    if not candidate:
        return None
    try:
        return Path(candidate).expanduser()
    except Exception:
        return None


def hash_text(text: str) -> str:
    return hashlib.sha1((text or '').encode('utf-8', 'replace')).hexdigest()


def parse_now(now: str) -> datetime | None:
    if not now:
        return None
    try:
        return datetime.fromisoformat(now.replace('Z', '+00:00'))
    except Exception:
        return None


def seconds_between(start: str, end: str) -> float:
    start_dt = parse_now(start)
    end_dt = parse_now(end)
    if start_dt is None or end_dt is None:
        return 0.0
    return max(0.0, (end_dt - start_dt).total_seconds())


def state_int(state: dict[str, object], key: str, default: int) -> int:
    value = state.get(key)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def state_str(state: dict[str, object], key: str, default: str = '') -> str:
    value = state.get(key)
    if value is None:
        return default
    return str(value)


__all__ = [
    'hash_text',
    'parse_now',
    'resolve_work_dir',
    'seconds_between',
    'state_int',
    'state_str',
]
