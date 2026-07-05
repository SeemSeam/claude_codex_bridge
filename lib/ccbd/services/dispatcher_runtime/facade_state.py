from __future__ import annotations

from dataclasses import dataclass


@dataclass
class DispatcherRuntimeState:
    layout: object
    config: object
    registry: object
    runtime_service: object
    execution_service: object
    auto_reply_delivery_on_complete: bool
    require_actionable_runtime_binding_for_execution: bool
    provider_catalog: object
    completion_tracker: object
    job_store: object
    event_store: object
    submission_store: object
    message_bureau: object
    message_bureau_control: object
    snapshot_writer: object
    clock: object
    state: object
    dispatch_error: object
    dispatch_rejected_error: object
    terminal_event_by_status: dict
    running_status: object
    timing_sink: object | None = None
    last_restore_entries: tuple = ()
    last_restore_generated_at: str | None = None
    last_text_artifact_sweep_at: float | None = None
    project_view_revision: int = 0
    quota_buckets: object | None = None


class DispatcherRuntimeStateMixin:
    @property
    def _layout(self):
        return self._runtime_state.layout

    @property
    def _config(self):
        return self._runtime_state.config

    @property
    def _registry(self):
        return self._runtime_state.registry

    @property
    def _runtime_service(self):
        return self._runtime_state.runtime_service

    @property
    def _execution_service(self):
        return self._runtime_state.execution_service

    @_execution_service.setter
    def _execution_service(self, value) -> None:
        self._runtime_state.execution_service = value

    @property
    def _auto_reply_delivery_on_complete(self):
        return self._runtime_state.auto_reply_delivery_on_complete

    @property
    def _require_actionable_runtime_binding_for_execution(self):
        return self._runtime_state.require_actionable_runtime_binding_for_execution

    @property
    def _provider_catalog(self):
        return self._runtime_state.provider_catalog

    @property
    def _completion_tracker(self):
        return self._runtime_state.completion_tracker

    @property
    def _job_store(self):
        return self._runtime_state.job_store

    @property
    def _event_store(self):
        return self._runtime_state.event_store

    @property
    def _submission_store(self):
        return self._runtime_state.submission_store

    @property
    def _message_bureau(self):
        return self._runtime_state.message_bureau

    @property
    def _message_bureau_control(self):
        return self._runtime_state.message_bureau_control

    @property
    def _snapshot_writer(self):
        return self._runtime_state.snapshot_writer

    @property
    def _clock(self):
        return self._runtime_state.clock

    @property
    def _state(self):
        return self._runtime_state.state

    @_state.setter
    def _state(self, value) -> None:
        self._runtime_state.state = value

    @property
    def _dispatch_error(self):
        return self._runtime_state.dispatch_error

    @property
    def _dispatch_rejected_error(self):
        return self._runtime_state.dispatch_rejected_error

    @property
    def _terminal_event_by_status(self):
        return self._runtime_state.terminal_event_by_status

    @property
    def _running_status(self):
        return self._runtime_state.running_status

    @property
    def _timing_sink(self):
        return self._runtime_state.timing_sink

    @property
    def _last_restore_entries(self):
        return self._runtime_state.last_restore_entries

    @_last_restore_entries.setter
    def _last_restore_entries(self, value) -> None:
        self._runtime_state.last_restore_entries = value

    @property
    def _last_restore_generated_at(self):
        return self._runtime_state.last_restore_generated_at

    @_last_restore_generated_at.setter
    def _last_restore_generated_at(self, value) -> None:
        self._runtime_state.last_restore_generated_at = value

    @property
    def _last_text_artifact_sweep_at(self):
        return self._runtime_state.last_text_artifact_sweep_at

    @_last_text_artifact_sweep_at.setter
    def _last_text_artifact_sweep_at(self, value) -> None:
        self._runtime_state.last_text_artifact_sweep_at = value

    @property
    def project_view_revision(self) -> int:
        return int(self._runtime_state.project_view_revision)

    def mark_project_view_dirty(self) -> None:
        self._runtime_state.project_view_revision += 1

    @property
    def _quota_buckets(self):
        return self._runtime_state.quota_buckets


__all__ = [
    'DispatcherRuntimeState',
    'DispatcherRuntimeStateMixin',
]
