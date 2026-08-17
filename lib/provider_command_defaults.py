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
    'kimi': 'KIMI_START_CMD',
    'deepseek': 'DEEPSEEK_START_CMD',
    'dsh': 'DSH_START_CMD',
    'mimo': 'MIMO_START_CMD',
    'qwen': 'QWEN_START_CMD',
    'qoder': 'QODER_START_CMD',
    'qoderclicn': 'QODERCLICN_START_CMD',
    'cursor': 'CURSOR_START_CMD',
    'copilot': 'COPILOT_START_CMD',
    'crush': 'CRUSH_START_CMD',
    'grok': 'GROK_START_CMD',
    'kiro': 'KIRO_START_CMD',
    'pi': 'PI_START_CMD',
    'omp': 'OMP_START_CMD',
    'zai': 'ZAI_START_CMD',
}

_PROVIDER_DEFAULT_EXECUTABLES = {
    'codex': 'codex',
    'claude': 'claude',
    'gemini': 'gemini',
    'opencode': 'opencode',
    'droid': 'droid',
    'agy': 'agy',
    'kimi': 'kimi',
    'deepseek': 'deepcode',
    'dsh': 'dsh',
    'mimo': 'mimo',
    'qwen': 'qwen',
    'qoder': 'qodercli',
    'qoderclicn': 'qoderclicn',
    'cursor': 'agent',
    'copilot': 'copilot',
    'crush': 'crush',
    'grok': 'grok',
    'kiro': 'kiro-cli',
    'pi': 'pi',
    'omp': 'omp',
    'zai': 'zai',
}

SUPPORTED_PROVIDER_NAMES = tuple(_PROVIDER_DEFAULT_EXECUTABLES)


def provider_start_env_vars() -> tuple[str, ...]:
    return tuple(_PROVIDER_START_ENV_VARS.values())


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


__all__ = [
    'SUPPORTED_PROVIDER_NAMES',
    'provider_executable',
    'provider_start_env_vars',
    'provider_start_parts',
]
