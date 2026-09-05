from __future__ import annotations

import pytest

from provider_thinking_shortcuts import (
    normalize_provider_thinking,
    provider_thinking_levels,
    provider_thinking_startup_args,
    startup_args_contain_thinking_flag,
    strip_provider_thinking_startup_args,
)


def test_claude_effort_compiles_to_current_cli_contract() -> None:
    assert provider_thinking_levels('claude') == (
        'low',
        'medium',
        'high',
        'xhigh',
        'max',
    )
    assert provider_thinking_startup_args('claude', thinking='xhigh') == (
        '--effort',
        'xhigh',
    )
    assert startup_args_contain_thinking_flag('claude', ['--effort', 'high'])
    assert startup_args_contain_thinking_flag('claude', ['--effort=max'])
    assert strip_provider_thinking_startup_args(
        'claude',
        ['--effort', 'xhigh', '--permission-mode', 'manual'],
        thinking='xhigh',
    ) == ('--permission-mode', 'manual')


@pytest.mark.parametrize('level', ['low', 'medium', 'high', 'xhigh', 'max'])
def test_pi_thinking_compiles_and_round_trips(level: str) -> None:
    assert provider_thinking_startup_args('pi', thinking=level) == ('--thinking', level)
    assert strip_provider_thinking_startup_args(
        'pi', ['--thinking', level, '--offline'], thinking=level,
    ) == ('--offline',)


@pytest.mark.parametrize('args', [['--thinking', 'high'], ['--thinking=max']])
def test_pi_thinking_detects_explicit_flag(args: list[str]) -> None:
    assert startup_args_contain_thinking_flag('pi', args)


def test_pi_thinking_preserves_provider_specific_levels() -> None:
    assert provider_thinking_levels('pi') == ('off', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max')
    with pytest.raises(ValueError, match='must be one of'):
        normalize_provider_thinking('pi', 'ultra')
    assert provider_thinking_startup_args('codex', thinking='ultra') == (
        '-c', 'model_reasoning_effort="ultra"',
    )
    assert provider_thinking_startup_args('pi', thinking=None) == ()
