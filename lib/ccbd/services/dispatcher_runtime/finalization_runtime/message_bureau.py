from __future__ import annotations

from dataclasses import replace

from ccbd.api_models import JobStatus
from completion.models import CompletionDecision
from message_bureau import CallbackEdgeState

from ..callbacks import (
    callback_child_edge,
    delegated_parent_edge,
    deliver_orphaned_chain_result,
    mark_callback_done,
    mark_parent_message_waiting,
    persist_delegated_terminal_job,
    submit_callback_continuation,
    terminalize_cancelled_parent_edge,
)
from ..reply_delivery import is_reply_delivery_job
from .artifacts import spill_terminal_reply_if_needed
from .message_bureau_persistence import persist_reply_decision
from .message_bureau_retry import reply_decision_without_automatic_retry, schedule_automatic_retry


def record_message_bureau_completion(
    dispatcher,
    current,
    terminal,
    decision: CompletionDecision,
    *,
    finished_at: str,
    prior_snapshot,
) -> tuple[object, CompletionDecision, bool]:
    reply_decision = decision
    if dispatcher._message_bureau is None:
        return terminal, reply_decision, False

    dispatcher._message_bureau.record_attempt_terminal(terminal, decision, finished_at=finished_at)
    if is_reply_delivery_job(current):
        return terminal, reply_decision, False
    parent_edge = delegated_parent_edge(dispatcher, terminal)
    if parent_edge is not None and parent_edge.state not in {
        CallbackEdgeState.FAILED,
        CallbackEdgeState.TIMED_OUT,
        CallbackEdgeState.DONE,
    }:
        terminal = persist_delegated_terminal_job(dispatcher, terminal, parent_edge, finished_at=finished_at)
        mark_parent_message_waiting(dispatcher, parent_edge, updated_at=finished_at)
        return terminal, reply_decision, False
    reply_decision, retry_scheduled = schedule_automatic_retry(
        dispatcher,
        current,
        terminal,
        decision,
        finished_at=finished_at,
    )
    if retry_scheduled:
        return terminal, decision, True
    if reply_decision is decision:
        reply_decision = reply_decision_without_automatic_retry(
            dispatcher,
            current,
            terminal,
            decision,
            finished_at=finished_at,
        )
    if reply_decision is not decision:
        reply_decision = spill_terminal_reply_if_needed(
            dispatcher,
            current,
            reply_decision,
            finished_at=finished_at,
        )
        terminal = persist_reply_decision(
            dispatcher,
            current,
            terminal,
            reply_decision,
            prior_snapshot=prior_snapshot,
            finished_at=finished_at,
        )
    _record_terminal_result(
        dispatcher,
        terminal,
        reply_decision,
        finished_at=finished_at,
        deliver_to_caller=_should_deliver_to_caller(terminal),
    )
    return terminal, reply_decision, False


def record_message_bureau_cancellation(
    dispatcher,
    terminal,
    decision: CompletionDecision,
    *,
    finished_at: str,
    record_reply: bool,
) -> None:
    if dispatcher._message_bureau is None:
        return
    dispatcher._message_bureau.record_attempt_terminal(
        terminal,
        decision,
        finished_at=finished_at,
    )
    if not record_reply:
        return
    parent_edge = delegated_parent_edge(dispatcher, terminal)
    if parent_edge is not None:
        terminalize_cancelled_parent_edge(
            dispatcher,
            parent_edge,
            parent_job=terminal,
            updated_at=finished_at,
        )
    _record_terminal_result(
        dispatcher,
        terminal,
        decision,
        finished_at=finished_at,
        deliver_to_caller=True,
    )


def _record_terminal_result(
    dispatcher,
    terminal,
    decision: CompletionDecision,
    *,
    finished_at: str,
    deliver_to_caller: bool,
) -> None:
    child_edge = callback_child_edge(dispatcher, terminal)
    if child_edge is not None:
        reply_id = dispatcher._message_bureau.record_reply(
            _job_with_unsilenced_reply(terminal),
            decision,
            finished_at=finished_at,
            deliver_to_caller=False,
        )
        if child_edge.state in {CallbackEdgeState.FAILED, CallbackEdgeState.TIMED_OUT}:
            deliver_orphaned_chain_result(
                dispatcher,
                child_edge,
                child_job=terminal,
                decision=decision,
                finished_at=finished_at,
            )
        else:
            submit_callback_continuation(
                dispatcher,
                child_edge,
                child_job=terminal,
                child_reply_id=reply_id,
                decision=decision,
                finished_at=finished_at,
            )
        mark_callback_done(dispatcher, terminal, finished_at=finished_at)
        return
    dispatcher._message_bureau.record_reply(
        terminal,
        decision,
        finished_at=finished_at,
        deliver_to_caller=deliver_to_caller,
    )
    mark_callback_done(dispatcher, terminal, finished_at=finished_at)


def _job_with_unsilenced_reply(job):
    if not bool(getattr(job.request, 'silence_on_success', False)):
        return job
    return replace(job, request=replace(job.request, silence_on_success=False))


def _should_deliver_to_caller(job) -> bool:
    return not (
        job.status is JobStatus.COMPLETED
        and bool(getattr(job.request, 'silence_on_success', False))
    )


__all__ = [
    'record_message_bureau_cancellation',
    'record_message_bureau_completion',
]
