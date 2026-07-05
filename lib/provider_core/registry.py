from __future__ import annotations

from provider_execution.base import ProviderExecutionAdapter

from .contracts import ProviderBackend, ProviderRuntimeLauncher, ProviderSessionBinding
from .manifests import ProviderManifest
from .registry_runtime import (
    CORE_PROVIDER_NAMES,
    OPTIONAL_PROVIDER_NAMES,
    TEST_DOUBLE_PROVIDER_NAMES,
    build_builtin_backends,
    build_test_double_backends,
)


class ProviderBackendRegistry:
    def __init__(self, backends: list[ProviderBackend] | None = None) -> None:
        self._backends: dict[str, ProviderBackend] = {}
        for backend in backends or []:
            self.register(backend)

    def register(self, backend: ProviderBackend) -> None:
        provider = backend.provider
        if provider in self._backends:
            raise ValueError(f"duplicate provider backend: {provider}")
        self._backends[provider] = backend

    def get(self, provider: str) -> ProviderBackend | None:
        return self._backends.get(str(provider or "").strip().lower())

    def manifests(self) -> list[ProviderManifest]:
        return [backend.manifest for backend in self._backends.values()]

    def execution_adapters(self) -> list[ProviderExecutionAdapter]:
        return [backend.execution_adapter for backend in self._backends.values() if backend.execution_adapter is not None]

    def session_bindings(self) -> dict[str, ProviderSessionBinding]:
        return {
            backend.provider: backend.session_binding
            for backend in self._backends.values()
            if backend.session_binding is not None
        }

    def runtime_launchers(self) -> dict[str, ProviderRuntimeLauncher]:
        return {
            backend.provider: backend.runtime_launcher
            for backend in self._backends.values()
            if backend.runtime_launcher is not None
        }


def build_default_backend_registry(
    *,
    include_optional: bool = True,
    include_test_doubles: bool = True,
    providers: set[str] | None = None,
) -> ProviderBackendRegistry:
    backends: list[ProviderBackend] = []
    if include_test_doubles:
        backends.extend(build_test_double_backends())
    backends.extend(
        build_builtin_backends(
            include_optional=include_optional,
            providers=providers,
        )
    )
    return ProviderBackendRegistry(backends)


def build_default_provider_manifests(
    *,
    include_optional: bool = True,
    include_test_doubles: bool = True,
    providers: set[str] | None = None,
) -> list[ProviderManifest]:
    return build_default_backend_registry(
        include_optional=include_optional,
        include_test_doubles=include_test_doubles,
        providers=providers,
    ).manifests()


def build_default_execution_adapters(
    *,
    include_optional: bool = True,
    include_test_doubles: bool = True,
    providers: set[str] | None = None,
) -> list[ProviderExecutionAdapter]:
    return build_default_backend_registry(
        include_optional=include_optional,
        include_test_doubles=include_test_doubles,
        providers=providers,
    ).execution_adapters()


def build_default_session_binding_map(
    *, include_optional: bool = True, providers: set[str] | None = None
) -> dict[str, ProviderSessionBinding]:
    return build_default_backend_registry(
        include_optional=include_optional,
        include_test_doubles=False,
        providers=providers,
    ).session_bindings()


def build_default_runtime_launcher_map(
    *, include_optional: bool = True, providers: set[str] | None = None
) -> dict[str, ProviderRuntimeLauncher]:
    return build_default_backend_registry(
        include_optional=include_optional,
        include_test_doubles=False,
        providers=providers,
    ).runtime_launchers()


__all__ = [
    "CORE_PROVIDER_NAMES",
    "OPTIONAL_PROVIDER_NAMES",
    "ProviderBackendRegistry",
    "TEST_DOUBLE_PROVIDER_NAMES",
    "build_default_backend_registry",
    "build_default_execution_adapters",
    "build_default_provider_manifests",
    "build_default_runtime_launcher_map",
    "build_default_session_binding_map",
]
