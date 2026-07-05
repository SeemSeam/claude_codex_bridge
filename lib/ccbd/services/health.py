from __future__ import annotations

from ccbd.system import process_exists, utc_now
from provider_core.registry import build_default_session_binding_map

from .health_runtime import assess_provider_pane
from .health_runtime_state import HealthMonitorRuntimeState, HealthMonitorRuntimeStateMixin
from .health_monitor_runtime import (
    check_all as check_all_impl,
    collect_orphans as collect_orphans_impl,
    daemon_health as daemon_health_impl,
    mark_degraded as mark_degraded_impl,
    pane_health as pane_health_impl,
    provider_pane_health as provider_pane_health_impl,
    provider_runtime_facts as provider_runtime_facts_impl,
    rebind_runtime as rebind_runtime_impl,
    runtime_health as runtime_health_impl,
)


class HealthMonitor(HealthMonitorRuntimeStateMixin):
    def __init__(
        self,
        registry,
        ownership_guard,
        *,
        project_id: str | None = None,
        lifecycle_store=None,
        runtime_service=None,
        clock=utc_now,
        pid_exists=process_exists,
        session_bindings=None,
        namespace_state_store=None,
        dispatcher=None,
    ) -> None:
        self._runtime_state = HealthMonitorRuntimeState(
            registry=registry,
            ownership_guard=ownership_guard,
            project_id=project_id,
            lifecycle_store=lifecycle_store,
            runtime_service=runtime_service,
            clock=clock,
            pid_exists=pid_exists,
            session_bindings=session_bindings or build_default_session_binding_map(include_optional=True),
            namespace_state_store=namespace_state_store,
            assess_provider_pane=assess_provider_pane,
            dispatcher=dispatcher,
        )

    def daemon_health(self):
        return daemon_health_impl(self)

    def local_daemon_health(self):
        return daemon_health_impl(self, assume_socket_connectable=True)

    def check_all(self) -> dict[str, str]:
        return check_all_impl(self)

    def collect_orphans(self) -> tuple[str, ...]:
        return collect_orphans_impl(self)

    def _runtime_health(self, runtime) -> str:
        return runtime_health_impl(self, runtime)

    def _pane_health(self, runtime) -> str | None:
        return pane_health_impl(self, runtime)

    def _provider_pane_health(self, runtime) -> str | None:
        return provider_pane_health_impl(self, runtime)

    def _rebind_runtime(
        self,
        runtime,
        session,
        binding,
        *,
        pane_id_override: str | None = None,
        force_session_ref_update: bool = False,
    ):
        return rebind_runtime_impl(
            self,
            runtime,
            session,
            binding,
            pane_id_override=pane_id_override,
            force_session_ref_update=force_session_ref_update,
        )

    def _mark_degraded(self, runtime, *, health: str, session=None, binding=None):
        return mark_degraded_impl(self, runtime, health=health, session=session, binding=binding)

    def _provider_runtime_facts(self, runtime, session, binding, *, pane_id_override: str | None = None):
        return provider_runtime_facts_impl(self, runtime, session, binding, pane_id_override=pane_id_override)


__all__ = ['HealthMonitor']
