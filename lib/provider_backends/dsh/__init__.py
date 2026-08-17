from provider_core.contracts import ProviderBackend

from .execution import build_execution_adapter
from .launcher import build_runtime_launcher
from .manifest import build_manifest
from .session import build_session_binding


def build_backend() -> ProviderBackend:
    return ProviderBackend(
        manifest=build_manifest(),
        execution_adapter=build_execution_adapter(),
        session_binding=build_session_binding(),
        runtime_launcher=build_runtime_launcher(),
    )


__all__ = ['build_backend']
