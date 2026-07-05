from __future__ import annotations

from .dispatcher_runtime import (
    DispatcherState,
    build_last_restore_report,
    cancel_job,
    cancel_with_decision,
    cleanup_stale_execution_states,
    comms_recover,
    complete_job,
    get_job,
    latest_for_agent,
    poll_completion_updates,
    prepare_reply_deliveries,
    repair_callback_edges,
    restore_running_jobs,
    submit_jobs,
    terminate_nonterminal_jobs,
    tick_jobs,
)
from .dispatcher_runtime.facade import DispatcherFacadeMixin
from .dispatcher_runtime.facade_state import DispatcherRuntimeState, DispatcherRuntimeStateMixin
from ccbd.api_models import (
    CancelReceipt,
    JobRecord,
    JobStatus,
    MessageEnvelope,
    SubmitReceipt,
)
from ccbd.models import CcbdRestoreEntry, CcbdRestoreReport
from ccbd.system import utc_now
from completion.models import CompletionDecision
from completion.tracker import CompletionTrackerService
from jobs.store import JobEventStore, JobStore, SubmissionStore
from message_bureau import MessageBureauControlService, MessageBureauFacade
from provider_core.catalog import ProviderCatalog, build_default_provider_catalog
from storage.paths import PathLayout

from .dispatcher_runtime.artifact_maintenance import sweep_text_artifacts_if_due
from .dispatcher_runtime.quota_buckets import QuotaBuckets
from .registry import AgentRegistry
from .snapshot_writer import SnapshotWriter

_TERMINAL_EVENT_BY_STATUS = {
    JobStatus.COMPLETED: 'job_completed',
    JobStatus.CANCELLED: 'job_cancelled',
    JobStatus.FAILED: 'job_failed',
    JobStatus.INCOMPLETE: 'job_incomplete',
}


class DispatchError(RuntimeError):
    pass


class DispatchRejectedError(DispatchError):
    pass


class JobDispatcher(DispatcherRuntimeStateMixin, DispatcherFacadeMixin):
    def __init__(
        self,
        layout: PathLayout,
        config,
        registry: AgentRegistry,
        *,
        runtime_service=None,
        execution_service=None,
        auto_reply_delivery_on_complete: bool = False,
        require_actionable_runtime_binding_for_execution: bool = False,
        completion_tracker: CompletionTrackerService | None = None,
        provider_catalog: ProviderCatalog | None = None,
        job_store: JobStore | None = None,
        event_store: JobEventStore | None = None,
        submission_store: SubmissionStore | None = None,
        message_bureau: MessageBureauFacade | None = None,
        message_bureau_control: MessageBureauControlService | None = None,
        snapshot_writer: SnapshotWriter | None = None,
        timing_sink=None,
        clock=utc_now,
    ) -> None:
        self._runtime_state = DispatcherRuntimeState(
            layout=layout,
            config=config,
            registry=registry,
            runtime_service=runtime_service,
            execution_service=execution_service,
            auto_reply_delivery_on_complete=bool(auto_reply_delivery_on_complete),
            require_actionable_runtime_binding_for_execution=bool(
                require_actionable_runtime_binding_for_execution
            ),
            provider_catalog=provider_catalog or build_default_provider_catalog(),
            completion_tracker=completion_tracker,
            job_store=job_store or JobStore(layout),
            event_store=event_store or JobEventStore(layout),
            submission_store=submission_store or SubmissionStore(layout),
            message_bureau=message_bureau or MessageBureauFacade(layout, config=config, clock=clock),
            message_bureau_control=message_bureau_control or MessageBureauControlService(layout, config, clock=clock),
            snapshot_writer=snapshot_writer or SnapshotWriter(layout),
            clock=clock,
            state=DispatcherState(config.agents),
            dispatch_error=DispatchError,
            dispatch_rejected_error=DispatchRejectedError,
            terminal_event_by_status=_TERMINAL_EVENT_BY_STATUS,
            running_status=JobStatus.RUNNING,
            timing_sink=timing_sink,
            last_restore_entries=(),
            last_restore_generated_at=None,
            quota_buckets=QuotaBuckets(clock=clock),
        )
        self._rebuild_state()
        cleanup_stale_execution_states(self)

    def submit(self, request: MessageEnvelope) -> SubmitReceipt:
        return submit_jobs(self, request)

    def tick(self) -> tuple[JobRecord, ...]:
        sweep_text_artifacts_if_due(self)
        repair_callback_edges(self)
        prepare_reply_deliveries(self)
        return tick_jobs(self)

    def disable_auto_reply_delivery(self) -> None:
        self._runtime_state.auto_reply_delivery_on_complete = False

    def complete(self, job_id: str, decision: CompletionDecision) -> JobRecord:
        return complete_job(self, job_id, decision)

    def cancel(self, job_id: str, *, record_reply: bool = True) -> CancelReceipt:
        return cancel_job(self, job_id, record_reply=record_reply)

    def _cancel_with_decision(
        self,
        current: JobRecord,
        cancelled_at: str,
        reply: str,
        snapshot,
        *,
        record_reply: bool = True,
    ) -> CancelReceipt:
        return cancel_with_decision(self, current, cancelled_at, reply, snapshot, record_reply=record_reply)

    def comms_recover(self, payload: dict | str) -> dict[str, object]:
        return comms_recover(self, payload)

    def get(self, job_id: str) -> JobRecord | None:
        return get_job(self, job_id)

    def get_snapshot(self, job_id: str):
        return self._snapshot_writer.load(job_id)

    def latest_for_agent(self, agent_name: str) -> JobRecord | None:
        return latest_for_agent(self, agent_name)

    def poll_completions(self) -> tuple[JobRecord, ...]:
        return poll_completion_updates(self)

    def restore_running_jobs(self) -> tuple[JobRecord, ...]:
        return restore_running_jobs(self)

    def terminate_nonterminal_jobs(self, *, shutdown_reason: str, forced: bool) -> tuple[JobRecord, ...]:
        return terminate_nonterminal_jobs(self, shutdown_reason=shutdown_reason, forced=forced)

    def last_restore_report(self, *, project_id: str) -> CcbdRestoreReport:
        return build_last_restore_report(self, project_id=project_id)


__all__ = ['JobDispatcher']
