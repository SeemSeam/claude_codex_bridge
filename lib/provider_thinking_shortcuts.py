from __future__ import annotations


_PROVIDER_THINKING_LEVELS = {
    # The installed Codex model catalog remains the model-specific authority.
    # This superset permits current and legacy Codex reasoning presets.
    'codex': ('none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'),
    # Claude Code 2.1.220 exposes these values through --effort.
    'claude': ('low', 'medium', 'high', 'xhigh', 'max'),
    'deepseek': ('off', 'high', 'max'),
    # DSH validates these on session.selectModel rather than at process start.
    'dsh': ('off', 'high', 'max'),
}

_PROVIDER_THINKING_RUNTIME_ENV = {
    'deepseek': frozenset({'DEEPCODE_THINKING_ENABLED', 'DEEPCODE_REASONING_EFFORT'}),
}


def supported_provider_thinking_shortcuts() -> tuple[str, ...]:
    return tuple(sorted(_PROVIDER_THINKING_LEVELS))


def provider_thinking_levels(provider: str) -> tuple[str, ...]:
    return _PROVIDER_THINKING_LEVELS.get(str(provider or '').strip().lower(), ())


def normalize_provider_thinking(provider: str, thinking: object) -> str:
    normalized_provider = str(provider or '').strip().lower()
    levels = provider_thinking_levels(normalized_provider)
    if not levels:
        supported = ', '.join(supported_provider_thinking_shortcuts())
        raise ValueError(f'thinking shortcut is supported only for providers: {supported}')
    value = str(thinking or '').strip().lower()
    if value not in levels:
        allowed = ', '.join(levels)
        raise ValueError(f'thinking for provider {normalized_provider} must be one of: {allowed}')
    return value


def provider_thinking_startup_args(provider: str, *, thinking: str | None) -> tuple[str, ...]:
    if thinking is None:
        return ()
    normalized_provider = str(provider or '').strip().lower()
    value = normalize_provider_thinking(normalized_provider, thinking)
    if normalized_provider == 'codex':
        return ('-c', f'model_reasoning_effort="{value}"')
    if normalized_provider == 'claude':
        return ('--effort', value)
    return ()


def provider_thinking_runtime_env(provider: str, *, thinking: str | None) -> dict[str, str]:
    if thinking is None:
        return {}
    normalized_provider = str(provider or '').strip().lower()
    value = normalize_provider_thinking(normalized_provider, thinking)
    if normalized_provider != 'deepseek':
        return {}
    if value == 'off':
        return {'DEEPCODE_THINKING_ENABLED': 'false'}
    return {
        'DEEPCODE_THINKING_ENABLED': 'true',
        'DEEPCODE_REASONING_EFFORT': value,
    }


def provider_thinking_runtime_env_keys(provider: str) -> set[str]:
    return set(_PROVIDER_THINKING_RUNTIME_ENV.get(str(provider or '').strip().lower(), ()))


def startup_args_contain_thinking_flag(
    provider: str,
    startup_args: tuple[str, ...] | list[str],
) -> bool:
    normalized_provider = str(provider or '').strip().lower()
    args = tuple(str(item) for item in startup_args)
    if normalized_provider == 'claude':
        return any(
            arg == '--effort' or arg.startswith('--effort=')
            for arg in args
        )
    if normalized_provider != 'codex':
        return False
    for index, arg in enumerate(args):
        if arg in {'-c', '--config'} and index + 1 < len(args):
            if args[index + 1].lstrip().startswith('model_reasoning_effort='):
                return True
        if arg.startswith('--config=') and arg.removeprefix('--config=').lstrip().startswith(
            'model_reasoning_effort='
        ):
            return True
    return False


def strip_provider_thinking_startup_args(
    provider: str,
    startup_args: tuple[str, ...] | list[str],
    *,
    thinking: str,
) -> tuple[str, ...]:
    compiled_prefix = provider_thinking_startup_args(provider, thinking=thinking)
    normalized_args = tuple(str(arg) for arg in startup_args)
    if compiled_prefix and normalized_args[: len(compiled_prefix)] == compiled_prefix:
        return normalized_args[len(compiled_prefix) :]
    return normalized_args


__all__ = [
    'normalize_provider_thinking',
    'provider_thinking_levels',
    'provider_thinking_runtime_env',
    'provider_thinking_runtime_env_keys',
    'provider_thinking_startup_args',
    'startup_args_contain_thinking_flag',
    'strip_provider_thinking_startup_args',
    'supported_provider_thinking_shortcuts',
]
