from __future__ import annotations

import os
import shlex


_PROVIDER_START_ENV_VARS = {
    'codex': 'CODEX_START_CMD',
    'claude': 'CLAUDE_START_CMD',
    'gemini': 'GEMINI_START_CMD',
    'opencode': 'OPENCODE_START_CMD',
    'droid': 'DROID_START_CMD',
    'agy': 'AGY_START_CMD',
}

_PROVIDER_DEFAULT_EXECUTABLES = {
    'codex': 'codex',
    'claude': 'claude',
    'gemini': 'gemini',
    'opencode': 'opencode',
    'droid': 'droid',
    'agy': 'agy',
}


def provider_start_parts(provider: str) -> list[str]:
    normalized = str(provider or '').strip().lower()
    env_name = _PROVIDER_START_ENV_VARS.get(normalized)
    raw = str(os.environ.get(env_name or '') or '').strip() if env_name else ''
    if raw:
        try:
            parts = shlex.split(raw)
        except ValueError:
            parts = [raw]
        if parts:
            return [str(part) for part in parts]
    default = _PROVIDER_DEFAULT_EXECUTABLES.get(normalized, normalized)
    return [default]


def provider_executable(provider: str) -> str:
    parts = provider_start_parts(provider)
    return str(parts[0] or provider)


def pane_title_marker(*, project_id: str, agent_name: str) -> str:
    suffix = str(project_id or '').strip()[:8]
    if suffix:
        return f'CCB-{agent_name}-{suffix}'
    return f'CCB-{agent_name}'


__all__ = ['pane_title_marker', 'provider_executable', 'provider_start_parts']
