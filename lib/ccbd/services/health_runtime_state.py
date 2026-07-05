from __future__ import annotations

from dataclasses import dataclass


@dataclass
class HealthMonitorRuntimeState:
    registry: object
    ownership_guard: object
    project_id: str | None
    lifecycle_store: object | None
    runtime_service: object | None
    clock: object
    pid_exists: object
    session_bindings: object
    namespace_state_store: object
    assess_provider_pane: object
    dispatcher: object | None = None


class HealthMonitorRuntimeStateMixin:
    @property
    def _registry(self):
        return self._runtime_state.registry

    @property
    def _ownership_guard(self):
        return self._runtime_state.ownership_guard

    @property
    def _project_id(self):
        return self._runtime_state.project_id

    @property
    def _lifecycle_store(self):
        return self._runtime_state.lifecycle_store

    @property
    def _clock(self):
        return self._runtime_state.clock

    @property
    def _runtime_service(self):
        return self._runtime_state.runtime_service

    @property
    def _pid_exists(self):
        return self._runtime_state.pid_exists

    @property
    def _session_bindings(self):
        return self._runtime_state.session_bindings

    @_session_bindings.setter
    def _session_bindings(self, value) -> None:
        self._runtime_state.session_bindings = value

    @property
    def _namespace_state_store(self):
        return self._runtime_state.namespace_state_store

    @property
    def _assess_provider_pane(self):
        return self._runtime_state.assess_provider_pane

    @property
    def _dispatcher(self):
        return self._runtime_state.dispatcher


__all__ = [
    'HealthMonitorRuntimeState',
    'HealthMonitorRuntimeStateMixin',
]
