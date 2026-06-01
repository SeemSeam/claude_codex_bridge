from __future__ import annotations

from provider_execution.registry import (
    CORE_EXECUTION_PROVIDERS,
    OPTIONAL_EXECUTION_PROVIDERS,
    build_default_execution_registry,
)


def test_execution_registry_can_build_core_only_registry() -> None:
    registry = build_default_execution_registry(include_optional=False, include_test_doubles=False)
    assert set(CORE_EXECUTION_PROVIDERS) == {'codex', 'claude', 'gemini'}
    assert set(OPTIONAL_EXECUTION_PROVIDERS) == {'opencode', 'droid', 'agy'}
    assert registry.get('codex') is not None
    assert registry.get('claude') is not None
    assert registry.get('gemini') is not None
    assert registry.get('opencode') is None
    assert registry.get('droid') is None
    assert registry.get('agy') is None
    assert registry.get('fake') is None
