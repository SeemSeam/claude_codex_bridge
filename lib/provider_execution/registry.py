from __future__ import annotations

from .base import ProviderExecutionAdapter
from provider_core.registry import (
    CORE_PROVIDER_NAMES,
    OPTIONAL_PROVIDER_NAMES,
    TEST_DOUBLE_PROVIDER_NAMES,
    build_default_execution_adapters,
)

CORE_EXECUTION_PROVIDERS = CORE_PROVIDER_NAMES
OPTIONAL_EXECUTION_PROVIDERS = OPTIONAL_PROVIDER_NAMES
TEST_DOUBLE_EXECUTION_PROVIDERS = TEST_DOUBLE_PROVIDER_NAMES


class ProviderExecutionRegistry:
    def __init__(self, adapters: list[ProviderExecutionAdapter] | None = None) -> None:
        self._adapters: dict[str, ProviderExecutionAdapter] = {}
        for adapter in adapters or []:
            self.register(adapter)

    def register(self, adapter: ProviderExecutionAdapter) -> None:
        provider = adapter.provider.strip().lower()
        if provider in self._adapters:
            raise ValueError(f'duplicate execution adapter: {provider}')
        self._adapters[provider] = adapter

    def get(self, provider: str) -> ProviderExecutionAdapter | None:
        return self._adapters.get(provider.strip().lower())


def build_default_execution_registry(
    *,
    include_optional: bool = True,
    include_test_doubles: bool = True,
    providers: set[str] | None = None,
) -> ProviderExecutionRegistry:
    return ProviderExecutionRegistry(
        build_default_execution_adapters(
            include_optional=include_optional,
            include_test_doubles=include_test_doubles,
            providers=providers,
        )
    )
