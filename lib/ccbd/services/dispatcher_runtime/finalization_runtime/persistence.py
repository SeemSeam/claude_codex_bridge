from __future__ import annotations

from dataclasses import replace

from agents.models import AgentState
from ccbd.api_models import JobRecord, JobStatus, TargetKind
from completion.models import CompletionDecision

from ..completion import build_terminal_state, merge_terminal_decision
from ..records import append_event, append_job
from ..runtime_state import sync_runtime
from .artifacts import spill_terminal_reply_if_needed


def persist_terminal_completion(
    dispatcher,
    current: JobRecord,
    decision: CompletionDecision,
    *,
    finished_at: str,
    no_reply_reason: str | None = None,
    no_reply_detail: dict[str, object] | None = None,
) -> tuple[JobRecord, CompletionDecision, object | None]:
    prior_snapshot = dispatcher._snapshot_writer.load(current.job_id)
    terminal_decision = merge_terminal_decision(
        current.job_id,
        decision,
        completion_tracker=dispatcher._completion_tracker,
        prior_snapshot=prior_snapshot,
    )
    terminal_decision = spill_terminal_reply_if_needed(
        dispatcher,
        current,
        terminal_decision,
        finished_at=finished_at,
    )
    if dispatcher._completion_tracker is not None:
        dispatcher._completion_tracker.finish(current.job_id)
    dispatcher._snapshot_writer.write_completion(
        job_id=current.job_id,
        agent_name=current.agent_name,
        profile_family=dispatcher._profile_family_for_job(current),
        state=build_terminal_state(terminal_decision, prior_snapshot.state if prior_snapshot else None),
        decision=terminal_decision,
        updated_at=finished_at,
    )
    append_event(dispatcher, current, 'completion_terminal', terminal_decision.to_record(), timestamp=finished_at)
    terminal = replace(
        current,
        status=JobStatus(terminal_decision.status.value),
        terminal_decision=terminal_decision.to_record(),
        updated_at=finished_at,
        no_reply_reason=no_reply_reason,
        no_reply_detail=dict(no_reply_detail or {}),
    )
    append_job(dispatcher, terminal)
    append_event(
        dispatcher,
        terminal,
        dispatcher._terminal_event_by_status[terminal.status],
        {'status': terminal.status.value},
        timestamp=finished_at,
    )
    dispatcher._state.clear_active_for(current.target_kind, current.target_name, job_id=current.job_id)
    if dispatcher._execution_service is not None:
        dispatcher._execution_service.finish(current.job_id)
    if current.target_kind is TargetKind.AGENT:
        sync_runtime(dispatcher, current.agent_name, state=AgentState.IDLE)
    return terminal, terminal_decision, prior_snapshot


__all__ = ['persist_terminal_completion']
