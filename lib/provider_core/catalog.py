from __future__ import annotations

from agents.models import RuntimeMode

from .manifests import ProviderManifest
from .registry import (
    CORE_PROVIDER_NAMES,
    OPTIONAL_PROVIDER_NAMES,
    TEST_DOUBLE_PROVIDER_NAMES,
    build_default_provider_manifests,
)


class ProviderCatalog:
    def __init__(self, manifests: list[ProviderManifest] | None = None) -> None:
        self._manifests: dict[str, ProviderManifest] = {}
        for manifest in manifests or []:
            self.register(manifest)

    def register(self, manifest: ProviderManifest) -> None:
        if manifest.provider in self._manifests:
            raise ValueError(f'duplicate provider manifest: {manifest.provider}')
        self._manifests[manifest.provider] = manifest

    def get(self, provider: str) -> ProviderManifest:
        key = provider.strip().lower()
        try:
            return self._manifests[key]
        except KeyError as exc:
            raise KeyError(f'unknown provider: {provider}') from exc

    def resolve_completion_manifest(self, provider: str, runtime_mode: RuntimeMode):
        manifest = self.get(provider)
        if not manifest.supports_runtime_mode(runtime_mode):
            raise ValueError(
                f'provider {manifest.provider!r} does not support runtime_mode {runtime_mode.value!r}'
            )
        return manifest.completion_manifest_for(runtime_mode)

    def providers(self) -> tuple[str, ...]:
        return tuple(sorted(self._manifests))


def build_default_provider_catalog(
    *,
    include_optional: bool = True,
    include_test_doubles: bool = True,
    providers: set[str] | None = None,
) -> ProviderCatalog:
    return ProviderCatalog(
        manifests=build_default_provider_manifests(
            include_optional=include_optional,
            include_test_doubles=include_test_doubles,
            providers=providers,
        )
    )


__all__ = [
    'CORE_PROVIDER_NAMES',
    'OPTIONAL_PROVIDER_NAMES',
    'ProviderCatalog',
    'TEST_DOUBLE_PROVIDER_NAMES',
    'build_default_provider_catalog',
]
