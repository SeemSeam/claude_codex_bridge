from __future__ import annotations

from dataclasses import replace

from agents.models import AgentState
from ccbd.api_models import CancelReceipt, JobStatus, TargetKind
from completion.models import CompletionConfidence, CompletionDecision, CompletionStatus

from .cancel_flags import cleanup_cancel_flags, write_cancel_flag
from .completion import build_terminal_state
from .finalization_runtime.artifacts import spill_terminal_reply_if_needed
from .reply_delivery import resolve_reply_delivery_terminal


def cancel_job(dispatcher, job_id: str, *, record_reply: bool = True) -> CancelReceipt:
    current = dispatcher.get(job_id)
    if current is None:
        raise dispatcher._dispatch_error(f'unknown job: {job_id}')
    if current.status is JobStatus.CANCELLED:
        return CancelReceipt(
            job_id=current.job_id,
            agent_name=current.agent_name,
            target_kind=current.target_kind,
            target_name=current.target_name,
            provider_instance=current.provider_instance,
            status=JobStatus.CANCELLED,
            cancelled_at=current.updated_at,
        )
    if current.status in {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.INCOMPLETE}:
        raise dispatcher._dispatch_error(f'job is already terminal: {current.status.value}')

    cancelled_at = dispatcher._clock()
    marked = replace(current, cancel_requested_at=cancelled_at, updated_at=cancelled_at)
    dispatcher._append_job(marked)
    dispatcher._append_event(marked, 'job_cancel_requested', {'status': current.status.value}, timestamp=cancelled_at)
    if current.target_kind is TargetKind.AGENT and current.agent_name:
        # Visible to the agent itself: the dispatch prompt instructs agents to
        # check this flag between steps and stop if it exists.
        write_cancel_flag(dispatcher._layout, current.agent_name, job_id)
        cleanup_cancel_flags(dispatcher._layout, current.agent_name)
    dispatcher._state.remove_queued_for(current.target_kind, current.target_name, job_id)
    dispatcher._state.clear_active_for(current.target_kind, current.target_name, job_id=job_id)
    if dispatcher._execution_service is not None:
        dispatcher._execution_service.cancel(job_id)

    snapshot = dispatcher._snapshot_writer.load(job_id)
    reply = snapshot.latest_decision.reply if snapshot is not None else ''
    return cancel_with_decision(dispatcher, marked, cancelled_at, reply, snapshot, record_reply=record_reply)


def cancel_with_decision(dispatcher, current, cancelled_at: str, reply: str, snapshot, *, record_reply: bool = True) -> CancelReceipt:
    decision = CompletionDecision(
        terminal=True,
        status=CompletionStatus.CANCELLED,
        reason='cancel_info',
        confidence=CompletionConfidence.DEGRADED,
        reply=reply,
        anchor_seen=snapshot.state.anchor_seen if snapshot else False,
        reply_started=snapshot.state.reply_started if snapshot else False,
        reply_stable=snapshot.state.reply_stable if snapshot else False,
        provider_turn_ref=snapshot.state.provider_turn_ref if snapshot else None,
        source_cursor=snapshot.state.latest_cursor if snapshot else None,
        finished_at=cancelled_at,
        diagnostics={'cancel_requested': True},
    )
    decision = spill_terminal_reply_if_needed(
        dispatcher,
        current,
        decision,
        finished_at=cancelled_at,
    )
    if dispatcher._completion_tracker is not None:
        dispatcher._completion_tracker.finish(current.job_id)
    dispatcher._snapshot_writer.write_completion(
        job_id=current.job_id,
        agent_name=current.agent_name,
        profile_family=dispatcher._profile_family_for_job(current),
        state=build_terminal_state(decision, snapshot.state if snapshot else None),
        decision=decision,
        updated_at=cancelled_at,
    )
    dispatcher._append_event(current, 'completion_terminal', decision.to_record(), timestamp=cancelled_at)
    terminal = replace(
        current,
        status=JobStatus.CANCELLED,
        terminal_decision=decision.to_record(),
        updated_at=cancelled_at,
        cancel_requested_at=cancelled_at,
    )
    dispatcher._append_job(terminal)
    dispatcher._append_event(terminal, 'job_cancelled', {'status': JobStatus.CANCELLED.value}, timestamp=cancelled_at)
    if current.target_kind is TargetKind.AGENT:
        dispatcher._sync_runtime(current.agent_name, state=AgentState.IDLE)
    if dispatcher._message_bureau is not None:
        dispatcher._message_bureau.record_terminal(terminal, decision, finished_at=cancelled_at, record_reply=record_reply)
    resolve_reply_delivery_terminal(dispatcher, terminal, finished_at=cancelled_at)
    return CancelReceipt(
        job_id=terminal.job_id,
        agent_name=terminal.agent_name,
        target_kind=terminal.target_kind,
        target_name=terminal.target_name,
        provider_instance=terminal.provider_instance,
        status=JobStatus.CANCELLED,
        cancelled_at=cancelled_at,
    )


__all__ = ['cancel_job', 'cancel_with_decision']
