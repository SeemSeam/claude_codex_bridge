from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from ccbd.api_models import DeliveryScope, JobRecord, JobStatus, MessageEnvelope, TargetKind
from completion.models import CompletionDecision
from mailbox_runtime.targets import NON_AGENT_ACTORS
from message_bureau import CallbackEdgeRecord, CallbackEdgeState, MessageState, ReplyTerminalStatus
from storage.text_artifacts import maybe_spill_text, preview_text

from .records import append_event, append_job, get_job
from .runtime_state import sync_runtime

CALLBACK_ROUTE_MODE = 'chain'
CALLBACK_CONTINUATION_MESSAGE_TYPE = 'chain_continuation'
DEFAULT_MAX_CALLBACK_DEPTH = 5
_TERMINAL_CALLBACK_STATES = frozenset(
    {
        CallbackEdgeState.CONTINUATION_SUBMITTED,
        CallbackEdgeState.DONE,
        CallbackEdgeState.FAILED,
        CallbackEdgeState.TIMED_OUT,
    }
)


def request_callback_route(request: MessageEnvelope) -> bool:
    options = dict(getattr(request, 'route_options', None) or {})
    return str(options.get('mode') or '').strip().lower() == CALLBACK_ROUTE_MODE


def validate_nested_ask_request(dispatcher, request: MessageEnvelope) -> None:
    if not _is_plain_ask(request):
        return
    if bool(getattr(request, 'silence_on_success', False)):
        return
    parent = _active_parent_job(dispatcher, request.from_actor)
    if parent is None:
        return
    raise dispatcher._dispatch_error(
        'plain ask from an active CCB task requires --chain when the child result is needed, '
        'or --silence for independent fire-and-forget work'
    )


def register_callback_edge(dispatcher, *, request: MessageEnvelope, jobs: tuple, message_id: str, accepted_at: str) -> None:
    validate_callback_request(dispatcher, request)
    if not request_callback_route(request):
        return
    if len(jobs) != 1:
        raise dispatcher._dispatch_error('ask --chain supports exactly one target agent')
    parent = _active_parent_job(dispatcher, request.from_actor)
    parent_message = _message_for_job(dispatcher, parent)
    child = jobs[0]
    edge = CallbackEdgeRecord(
        edge_id=dispatcher._new_id('cb'),
        parent_job_id=parent.job_id,
        parent_message_id=parent_message.message_id,
        parent_agent=parent.agent_name,
        child_job_id=child.job_id,
        child_message_id=message_id,
        callback_target_agent=parent.agent_name,
        original_caller=parent.request.from_actor,
        original_task_id=parent.request.task_id or parent_message.message_id,
        state=CallbackEdgeState.PENDING,
        timeout_at=None,
        created_at=accepted_at,
        updated_at=accepted_at,
        diagnostics={
            'route_mode': CALLBACK_ROUTE_MODE,
            'child_agent': child.agent_name,
            'parent_body': _callback_body_summary(parent.request),
            'child_body': _callback_body_summary(child.request),
            'parent_body_artifact': dict(parent.request.body_artifact) if parent.request.body_artifact else None,
            'child_body_artifact': dict(child.request.body_artifact) if child.request.body_artifact else None,
            'allowed_chain_targets': list(_allowed_chain_targets(parent)),
            'bind_chain_workspace_tree': bool(request.route_options.get('bind_chain_workspace_tree')),
            'review_workspace_path': request.route_options.get('review_workspace_path'),
            'review_tree_digest': request.route_options.get('review_tree_digest'),
        },
    )
    dispatcher._message_bureau.record_callback_edge(edge)
    append_event(
        dispatcher,
        child,
        'callback_edge_created',
        {
            'edge_id': edge.edge_id,
            'parent_job_id': edge.parent_job_id,
            'parent_message_id': edge.parent_message_id,
            'callback_target_agent': edge.callback_target_agent,
            'original_caller': edge.original_caller,
        },
        timestamp=accepted_at,
    )


def validate_callback_request(dispatcher, request: MessageEnvelope) -> None:
    parent = _active_parent_job(dispatcher, request.from_actor)
    allowed_targets = _allowed_chain_targets(parent)
    if not request_callback_route(request):
        if allowed_targets:
            raise dispatcher._dispatch_error(
                'restricted workflow job may only use ask --chain to its assigned target'
            )
        validate_nested_ask_request(dispatcher, request)
        return
    if dispatcher._message_bureau is None:
        raise dispatcher._dispatch_error('ask --chain requires message bureau support')
    if request.delivery_scope is not DeliveryScope.SINGLE:
        raise dispatcher._dispatch_error('ask --chain supports exactly one target agent')
    if parent is None:
        raise dispatcher._dispatch_error('ask --chain requires an active parent job for the sender')
    if _message_for_job(dispatcher, parent) is None:
        raise dispatcher._dispatch_error('ask --chain could not resolve parent message')
    _validate_callback_continuation_target(dispatcher, parent=parent, child_agent=request.to_agent)
    if allowed_targets and str(request.to_agent or '').strip().lower() not in allowed_targets:
        raise dispatcher._dispatch_error(
            'ask --chain target is not the assigned reviewer for this workflow job'
        )
    parent_options = dict(getattr(parent.request, 'route_options', None) or {})
    if bool(parent_options.get('bind_chain_workspace_tree')):
        request.route_options['bind_chain_workspace_tree'] = True
        _bind_callback_workspace_tree(dispatcher, request=request, parent=parent)
    if dispatcher._message_bureau.callback_edge_for_parent_job(parent.job_id) is not None:
        raise dispatcher._dispatch_error('ask --chain allows one outstanding chain edge per parent job')
    _validate_callback_chain(dispatcher, parent=parent, child_agent=request.to_agent)


def _is_plain_ask(request: MessageEnvelope) -> bool:
    if str(request.message_type or '').strip().lower() != 'ask':
        return False
    return not request_callback_route(request)


def delegated_parent_edge(dispatcher, job) -> CallbackEdgeRecord | None:
    if dispatcher._message_bureau is None:
        return None
    return dispatcher._message_bureau.callback_edge_for_parent_job(job.job_id)


def callback_child_edge(dispatcher, job) -> CallbackEdgeRecord | None:
    if dispatcher._message_bureau is None:
        return None
    edge = dispatcher._message_bureau.callback_edge_for_child_job(job.job_id)
    if edge is not None:
        return edge
    message = _message_for_job(dispatcher, job)
    if message is None:
        return None
    return dispatcher._message_bureau.callback_edge_for_child_message(message.message_id)


def submit_callback_continuation(
    dispatcher,
    edge: CallbackEdgeRecord,
    *,
    child_job,
    child_reply_id: str | None,
    decision: CompletionDecision,
    finished_at: str,
) -> CallbackEdgeRecord:
    latest = dispatcher._message_bureau.callback_edge_for_child_job(child_job.job_id) or edge
    if latest.state in _TERMINAL_CALLBACK_STATES:
        return latest
    existing_continuation = _existing_continuation_job(dispatcher, latest)
    if existing_continuation is not None:
        _repair_pending_continuation_attempt(dispatcher, latest, existing_continuation)
        persisted_reply = _latest_child_reply(dispatcher, latest)
        state = (
            CallbackEdgeState.DONE
            if existing_continuation.status in dispatcher._terminal_event_by_status
            else CallbackEdgeState.CONTINUATION_SUBMITTED
        )
        return dispatcher._message_bureau.update_callback_edge(
            latest,
            state=state,
            child_reply_id=child_reply_id or latest.child_reply_id or (persisted_reply.reply_id if persisted_reply else None),
            child_status=child_job.status.value,
            continuation_job_id=existing_continuation.job_id,
            continuation_message_id=latest.parent_message_id,
            updated_at=finished_at,
        )
    updated = dispatcher._message_bureau.update_callback_edge(
        latest,
        state=CallbackEdgeState.CHILD_COMPLETED,
        child_reply_id=child_reply_id,
        child_status=child_job.status.value,
        updated_at=finished_at,
    )
    try:
        continuation_job_id, continuation_message_id = _submit_continuation_job(
            dispatcher,
            request=_continuation_request(dispatcher, edge=updated, child_job=child_job, decision=decision),
            parent_message_id=updated.parent_message_id,
            accepted_at=finished_at,
        )
    except Exception as exc:
        return fail_callback_edge(
            dispatcher,
            updated,
            reason='chain_continuation_submit_failed',
            detail=str(exc),
            updated_at=finished_at,
        )
    final = dispatcher._message_bureau.update_callback_edge(
        updated,
        state=CallbackEdgeState.CONTINUATION_SUBMITTED,
        continuation_job_id=continuation_job_id,
        continuation_message_id=continuation_message_id,
        updated_at=finished_at,
    )
    append_event(
        dispatcher,
        child_job,
        'chain_continuation_submitted',
        {
            'edge_id': final.edge_id,
            'continuation_job_id': continuation_job_id,
            'continuation_message_id': continuation_message_id,
            'callback_target_agent': final.callback_target_agent,
        },
        timestamp=finished_at,
    )
    return final


def repair_callback_edges(dispatcher) -> tuple[CallbackEdgeRecord, ...]:
    if dispatcher._message_bureau is None:
        return ()
    with dispatcher._chain_transition_lock:
        return _repair_callback_edges_locked(dispatcher)


def _repair_callback_edges_locked(dispatcher) -> tuple[CallbackEdgeRecord, ...]:
    repaired: list[CallbackEdgeRecord] = []
    latest_edges = _latest_callback_edges(dispatcher)
    repair_candidates = tuple(
        edge
        for edge in latest_edges
        if edge.state in {CallbackEdgeState.PENDING, CallbackEdgeState.CHILD_COMPLETED}
        and not edge.continuation_job_id
        and not _pending_edge_waits_on_child(dispatcher, edge)
    )
    unlinked_submitted = tuple(
        edge
        for edge in latest_edges
        if edge.state is CallbackEdgeState.CONTINUATION_SUBMITTED
        and not edge.continuation_job_id
    )
    existing_continuations = _existing_continuation_jobs_by_edge(
        dispatcher,
        (*repair_candidates, *unlinked_submitted),
    )
    repair_candidate_ids = {edge.edge_id for edge in repair_candidates}
    for latest in latest_edges:
        if latest.state in {
            CallbackEdgeState.DONE,
            CallbackEdgeState.FAILED,
            CallbackEdgeState.TIMED_OUT,
        }:
            continue
        if latest.state is CallbackEdgeState.CONTINUATION_SUBMITTED:
            existing_continuation = (
                _queued_continuation_job(dispatcher, latest)
                if latest.continuation_job_id
                else existing_continuations.get(latest.edge_id)
            )
            if existing_continuation is not None and _repair_pending_continuation_attempt(
                dispatcher,
                latest,
                existing_continuation,
            ):
                repaired.append(latest)
            continue
        if latest.state in _TERMINAL_CALLBACK_STATES:
            continue
        if latest.edge_id not in repair_candidate_ids:
            continue
        existing_continuation = existing_continuations.get(latest.edge_id)
        if existing_continuation is not None:
            _repair_pending_continuation_attempt(dispatcher, latest, existing_continuation)
            reply = _latest_child_reply(dispatcher, latest)
            reply_job = _job_for_reply(dispatcher, reply)
            repaired.append(
                dispatcher._message_bureau.update_callback_edge(
                    latest,
                    state=CallbackEdgeState.CONTINUATION_SUBMITTED,
                    child_reply_id=latest.child_reply_id or (reply.reply_id if reply else None),
                    child_status=latest.child_status or (reply_job.status.value if reply_job else None),
                    continuation_job_id=existing_continuation.job_id,
                    continuation_message_id=latest.parent_message_id,
                    updated_at=latest.updated_at,
                )
            )
            continue
        reply = _latest_child_reply(dispatcher, latest)
        child_job = _job_for_reply(dispatcher, reply) or get_job(dispatcher, latest.child_job_id)
        if (
            reply is None
            and child_job is not None
            and getattr(dispatcher._config.agents.get(child_job.agent_name), 'remote_workspace', None)
        ):
            from remote_workspace.callback_recovery import restore_missing_reply

            reply = restore_missing_reply(dispatcher, child_job)
        decision = _decision_from_reply(reply, child_job=child_job, fallback_finished_at=latest.updated_at)
        if reply is None or child_job is None or decision is None:
            continue
        repaired.append(
            submit_callback_continuation(
                dispatcher,
                latest,
                child_job=child_job,
                child_reply_id=reply.reply_id,
                decision=decision,
                finished_at=latest.updated_at or reply.finished_at,
            )
        )
    return tuple(repaired)


def _latest_callback_edges(dispatcher) -> tuple[CallbackEdgeRecord, ...]:
    return dispatcher._message_bureau._callback_edge_store.list_latest()


def _existing_continuation_jobs_by_edge(
    dispatcher,
    edges: tuple[CallbackEdgeRecord, ...],
) -> dict[str, JobRecord]:
    targets = {edge.callback_target_agent for edge in edges}
    existing: dict[str, JobRecord] = {}
    for target in targets:
        for job in dispatcher._job_store.list_agent(target):
            options = dict(getattr(job.request, 'route_options', None) or {})
            edge_id = str(options.get('chain_edge_id') or '').strip()
            if edge_id:
                existing[edge_id] = job
    return existing


def _queued_continuation_job(dispatcher, edge: CallbackEdgeRecord):
    job_id = str(edge.continuation_job_id or '').strip()
    if not job_id:
        return None
    target = dispatcher._state.target_for_job(job_id)
    if target is None:
        return None
    if job_id not in dispatcher._state.queued_items_for(target[0], target[1]):
        return None
    return dispatcher._job_store.get_latest_target(target[0], target[1], job_id)


def _pending_edge_waits_on_child(dispatcher, edge: CallbackEdgeRecord) -> bool:
    if edge.state is not CallbackEdgeState.PENDING:
        return False
    return _job_is_outstanding(dispatcher, edge.child_job_id)


def _job_is_outstanding(dispatcher, job_id: str) -> bool:
    target = dispatcher._state.target_for_job(job_id)
    if target is None:
        return False
    if dispatcher._state.active_job_for(target[0], target[1]) == job_id:
        return True
    return job_id in dispatcher._state.queued_items_for(target[0], target[1])


def _repair_pending_continuation_attempt(dispatcher, edge: CallbackEdgeRecord, job) -> bool:
    if job.status not in {JobStatus.ACCEPTED, JobStatus.QUEUED}:
        return False
    bureau = dispatcher._message_bureau
    existing_attempt = bureau._attempt_store.get_latest_by_job_id(job.job_id)
    existing_inbound = None
    if existing_attempt is not None:
        existing_inbound = bureau._inbound_store.get_latest_for_attempt(
            job.agent_name,
            existing_attempt.attempt_id,
        )
    if existing_attempt is not None and existing_inbound is not None:
        return False
    bureau.record_retry_attempt(
        edge.parent_message_id,
        job,
        accepted_at=job.created_at or edge.updated_at,
    )
    return existing_attempt is None or existing_inbound is None


def sweep_callback_timeouts(dispatcher) -> tuple[CallbackEdgeRecord, ...]:
    return ()


def fail_callback_edge(
    dispatcher,
    edge: CallbackEdgeRecord,
    *,
    reason: str,
    detail: str,
    updated_at: str,
    state: CallbackEdgeState = CallbackEdgeState.FAILED,
) -> CallbackEdgeRecord:
    latest = dispatcher._message_bureau.callback_edge(edge.edge_id) or edge
    if latest.state in _TERMINAL_CALLBACK_STATES:
        return latest
    failed = dispatcher._message_bureau.update_callback_edge(
        latest,
        state=state,
        child_status=latest.child_status or state.value,
        updated_at=updated_at,
        diagnostics={
            **dict(latest.diagnostics or {}),
            'failure_reason': reason,
            'failure_detail': detail,
        },
    )
    _record_callback_failure_notice(
        dispatcher,
        failed,
        reason=reason,
        detail=detail,
        updated_at=updated_at,
    )
    return failed


def mark_callback_done(dispatcher, job, *, finished_at: str) -> None:
    options = dict(getattr(job.request, 'route_options', None) or {})
    edge_id = str(options.get('chain_edge_id') or '').strip()
    if not edge_id or dispatcher._message_bureau is None:
        return
    edge = dispatcher._message_bureau.callback_edge(edge_id)
    if edge is None or edge.state in {CallbackEdgeState.DONE, CallbackEdgeState.FAILED, CallbackEdgeState.TIMED_OUT}:
        return
    done = dispatcher._message_bureau.update_callback_edge(
        edge,
        state=CallbackEdgeState.DONE,
        updated_at=finished_at,
    )
    _mark_prior_continuation_edges_done(dispatcher, done, updated_at=finished_at)


def _mark_prior_continuation_edges_done(dispatcher, completed_edge: CallbackEdgeRecord, *, updated_at: str) -> None:
    for edge in dispatcher._message_bureau._callback_edge_store.list_latest():
        if edge.edge_id == completed_edge.edge_id:
            continue
        if edge.parent_message_id != completed_edge.parent_message_id:
            continue
        if edge.state is not CallbackEdgeState.CONTINUATION_SUBMITTED:
            continue
        dispatcher._message_bureau.update_callback_edge(
            edge,
            state=CallbackEdgeState.DONE,
            updated_at=updated_at,
        )


def mark_parent_message_waiting(dispatcher, edge: CallbackEdgeRecord, *, updated_at: str) -> None:
    if edge.state in {CallbackEdgeState.FAILED, CallbackEdgeState.TIMED_OUT, CallbackEdgeState.DONE}:
        return
    dispatcher._message_bureau.set_message_state(edge.parent_message_id, MessageState.RUNNING, updated_at=updated_at)


def delegated_terminal_job(job, edge: CallbackEdgeRecord):
    return replace(
        job,
        terminal_decision={
            **dict(job.terminal_decision or {}),
            'delegated': True,
            'suppress_reply': True,
            'chain_edge_id': edge.edge_id,
            'chain_child_job_id': edge.child_job_id,
        },
    )


def terminalize_cancelled_parent_edge(
    dispatcher,
    edge: CallbackEdgeRecord,
    *,
    parent_job,
    updated_at: str,
) -> CallbackEdgeRecord:
    latest = dispatcher._message_bureau.callback_edge(edge.edge_id) or edge
    if latest.state in _TERMINAL_CALLBACK_STATES:
        return latest
    return dispatcher._message_bureau.update_callback_edge(
        latest,
        state=CallbackEdgeState.FAILED,
        updated_at=updated_at,
        diagnostics={
            **dict(latest.diagnostics or {}),
            'failure_reason': 'chain_parent_cancelled',
            'parent_cancelled': True,
            'cancelled_parent_job_id': parent_job.job_id,
        },
    )


def persist_delegated_terminal_job(dispatcher, job, edge: CallbackEdgeRecord, *, finished_at: str):
    delegated = delegated_terminal_job(job, edge)
    append_job(dispatcher, delegated)
    append_event(
        dispatcher,
        delegated,
        'job_delegated_chain',
        {
            'edge_id': edge.edge_id,
            'chain_child_job_id': edge.child_job_id,
            'chain_target_agent': edge.callback_target_agent,
        },
        timestamp=finished_at,
    )
    return delegated


def _submit_continuation_job(dispatcher, *, request: MessageEnvelope, parent_message_id: str, accepted_at: str) -> tuple[str, str]:
    dispatcher._registry.spec_for(request.to_agent)
    dispatcher._validate_targets_available((request.to_agent,))
    spec = dispatcher._registry.spec_for(request.to_agent)
    parent_job_id = str(request.route_options.get('chain_parent_job_id') or '').strip()
    parent_job = get_job(dispatcher, parent_job_id) if parent_job_id else None
    parent_runtime = dispatcher._registry.get(request.to_agent)
    job_id = dispatcher._new_id('job')
    status = JobStatus.QUEUED if dispatcher._state.has_outstanding_for(TargetKind.AGENT, request.to_agent) else JobStatus.ACCEPTED
    job = JobRecord(
        job_id=job_id,
        submission_id=None,
        agent_name=request.to_agent,
        provider=spec.provider,
        provider_instance=None,
        provider_options={},
        workspace_path=(
            getattr(parent_job, 'workspace_path', None)
            or getattr(parent_runtime, 'workspace_path', None)
        ),
        target_kind=TargetKind.AGENT,
        target_name=request.to_agent,
        request=request,
        status=status,
        terminal_decision=None,
        cancel_requested_at=None,
        created_at=accepted_at,
        updated_at=accepted_at,
    )
    append_job(dispatcher, job)
    append_event(
        dispatcher,
        job,
        'job_accepted' if status is JobStatus.ACCEPTED else 'job_queued',
        {'status': status.value, 'chain_continuation': True},
        timestamp=accepted_at,
    )
    dispatcher._state.enqueue_for(job.target_kind, job.target_name, job.job_id)
    sync_runtime(dispatcher, job.agent_name)
    dispatcher._message_bureau.record_retry_attempt(parent_message_id, job, accepted_at=accepted_at)
    parent_message = dispatcher._message_bureau._message_store.get_latest(parent_message_id)
    if parent_message is not None:
        _append_submission_job(dispatcher, parent_message.submission_id, job_id=job.job_id, updated_at=accepted_at)
    return job.job_id, parent_message_id


def _append_submission_job(dispatcher, submission_id: str | None, *, job_id: str, updated_at: str) -> None:
    if not submission_id:
        return
    current = dispatcher._submission_store.get_latest(submission_id)
    if current is None:
        return
    job_ids = list(current.job_ids)
    if job_id not in job_ids:
        job_ids.append(job_id)
    dispatcher._submission_store.append(
        replace(
            current,
            job_ids=job_ids,
            updated_at=updated_at,
        )
    )


def _existing_continuation_job(dispatcher, edge: CallbackEdgeRecord):
    return _existing_continuation_jobs_by_edge(dispatcher, (edge,)).get(edge.edge_id)


def _latest_child_reply(dispatcher, edge: CallbackEdgeRecord):
    if edge.child_reply_id:
        reply = dispatcher._message_bureau._reply_store.get_latest(edge.child_reply_id)
        if reply is not None:
            return reply
    for reply in reversed(dispatcher._message_bureau._reply_store.list_message(edge.child_message_id)):
        if not bool(reply.diagnostics.get('notice')):
            return reply
    return None


def _job_for_reply(dispatcher, reply):
    if reply is None:
        return None
    attempt = dispatcher._message_bureau._attempt_store.get_latest(reply.attempt_id)
    if attempt is None:
        return None
    return get_job(dispatcher, attempt.job_id)


def _active_parent_job(dispatcher, actor: str):
    normalized = str(actor or '').strip().lower()
    if not normalized or normalized in NON_AGENT_ACTORS:
        return None
    if normalized not in dispatcher._config.agents:
        return None
    parent_job_id = dispatcher._state.active_job(normalized)
    if not parent_job_id:
        return None
    parent = get_job(dispatcher, parent_job_id)
    if parent is None:
        return None
    # Reply delivery preparation imports callback submission, so keep this lazy.
    from .reply_delivery import is_reply_delivery_job

    if is_reply_delivery_job(parent):
        return None
    return parent


def _message_for_job(dispatcher, job):
    attempt = dispatcher._message_bureau._attempt_store.get_latest_by_job_id(job.job_id)
    if attempt is None:
        return None
    return dispatcher._message_bureau._message_store.get_latest(attempt.message_id)


def _validate_callback_chain(dispatcher, *, parent, child_agent: str) -> None:
    parent_message = _message_for_job(dispatcher, parent)
    if parent_message is None:
        raise dispatcher._dispatch_error('ask --chain could not resolve parent message')
    chain = _callback_chain_for_parent(dispatcher, parent_message.message_id)
    max_depth = _max_callback_depth(dispatcher)
    next_depth = len(chain) + 1
    if next_depth > max_depth:
        raise dispatcher._dispatch_error(f'ask --chain exceeds max chain depth {max_depth}')
    actors = {str(edge.parent_agent or '').strip().lower() for edge in chain}
    actors.update(_callback_child_agent(edge) for edge in chain)
    actors.add(parent.agent_name)
    target = str(child_agent or '').strip().lower()
    if target in actors:
        raise dispatcher._dispatch_error('ask --chain cycle detected')


def _validate_callback_continuation_target(dispatcher, *, parent, child_agent: str) -> None:
    request = getattr(parent, 'request', None)
    options = dict(getattr(request, 'route_options', None) or {})
    message_type = str(getattr(request, 'message_type', '') or '').strip().lower()
    mode = str(options.get('mode') or '').strip().lower()
    if message_type != CALLBACK_CONTINUATION_MESSAGE_TYPE and mode != 'chain_continuation':
        return
    edge_id = str(options.get('chain_edge_id') or '').strip()
    if not edge_id:
        raise dispatcher._dispatch_error(
            f'ask --chain from result-chain continuation job {parent.job_id} cannot resolve chain edge; '
            'finish the current continuation directly or report the metadata error'
        )
    edge = dispatcher._message_bureau.callback_edge(edge_id)
    if edge is None:
        raise dispatcher._dispatch_error(
            f'ask --chain from result-chain continuation job {parent.job_id} could not resolve chain edge {edge_id}; '
            'finish the current continuation directly or report the metadata error'
        )
    target = str(child_agent or '').strip().lower()
    upstream = str(edge.original_caller or '').strip().lower()
    if target and upstream and target == upstream:
        raise dispatcher._dispatch_error(
            f'ask --chain from result-chain continuation job {parent.job_id} to original caller {edge.original_caller} '
            f'is not allowed for chain edge {edge.edge_id}; finish the current response and CCB will deliver it upstream'
        )


def _callback_chain_for_parent(dispatcher, parent_message_id: str) -> tuple[CallbackEdgeRecord, ...]:
    chain: list[CallbackEdgeRecord] = []
    seen: set[str] = set()
    message_id = parent_message_id
    while message_id and message_id not in seen:
        seen.add(message_id)
        edge = dispatcher._message_bureau.callback_edge_for_child_message(message_id)
        if edge is None:
            break
        chain.append(edge)
        message_id = edge.parent_message_id
    return tuple(chain)


def _record_callback_failure_notice(
    dispatcher,
    edge: CallbackEdgeRecord,
    *,
    reason: str,
    detail: str,
    updated_at: str,
) -> None:
    parent_job = get_job(dispatcher, edge.parent_job_id)
    if parent_job is None:
        dispatcher._message_bureau.set_message_state(edge.parent_message_id, MessageState.FAILED, updated_at=updated_at)
        return
    dispatcher._message_bureau.record_notice(
        parent_job,
        reply=_callback_failure_reply(edge=edge, reason=reason, detail=detail),
        diagnostics={
            'notice': True,
            'chain_edge_id': edge.edge_id,
            'chain_failure': True,
            'reason': reason,
            'detail': detail,
        },
        finished_at=updated_at,
        terminal_status=ReplyTerminalStatus.FAILED,
        deliver_to_actor=edge.original_caller,
    )


def _callback_failure_reply(*, edge: CallbackEdgeRecord, reason: str, detail: str) -> str:
    suffix = f': {detail}' if detail else ''
    return (
        f'CCB chain failed for delegated task {edge.child_job_id} '
        f'while continuing parent job {edge.parent_job_id}. Reason: {reason}{suffix}'
    )


def _max_callback_depth(dispatcher) -> int:
    value = getattr(dispatcher._config, 'max_callback_depth', DEFAULT_MAX_CALLBACK_DEPTH)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return DEFAULT_MAX_CALLBACK_DEPTH


def _callback_child_agent(edge: CallbackEdgeRecord) -> str:
    return str(edge.diagnostics.get('child_agent') or '').strip().lower()


def _allowed_chain_targets(job) -> tuple[str, ...]:
    if job is None:
        return ()
    request = getattr(job, 'request', None)
    options = dict(getattr(request, 'route_options', None) or {})
    values = options.get('allowed_chain_targets')
    if not isinstance(values, (list, tuple)):
        return ()
    return tuple(
        dict.fromkeys(
            str(value or '').strip().lower()
            for value in values
            if str(value or '').strip()
        )
    )


def _bind_callback_workspace_tree(dispatcher, *, request: MessageEnvelope, parent) -> None:
    parent_runtime = dispatcher._registry.get(parent.agent_name)
    workspace = str(
        getattr(parent, 'workspace_path', None)
        or getattr(parent_runtime, 'workspace_path', None)
        or ''
    ).strip()
    if not workspace:
        raise dispatcher._dispatch_error(
            'restricted review chain requires a bound parent workspace'
        )
    try:
        from cli.services.workgroup_integration.git_ops import GitOperations

        path = Path(workspace).expanduser().resolve()
        digest = GitOperations(path).current_tree_digest(
            path,
            ignore_controller_state=True,
        )
    except Exception as exc:
        raise dispatcher._dispatch_error(
            f'restricted review chain could not bind parent workspace tree: {exc}'
        ) from exc
    request.route_options['review_workspace_path'] = str(path)
    request.route_options['review_tree_digest'] = digest


def _continuation_request(dispatcher, *, edge: CallbackEdgeRecord, child_job, decision: CompletionDecision) -> MessageEnvelope:
    body = _continuation_body(edge=edge, child_job=child_job, decision=decision)
    body, body_artifact = maybe_spill_text(
        dispatcher._layout,
        text=body,
        kind='result-chain-continuation',
        owner_id=edge.edge_id,
        prefix=f'CCB result-chain continuation {edge.edge_id} is larger than 4 KiB and was stored as an artifact.',
    )
    return MessageEnvelope(
        project_id=child_job.request.project_id,
        to_agent=edge.callback_target_agent,
        from_actor=edge.original_caller,
        body=body,
        task_id=edge.original_task_id,
        reply_to=edge.parent_message_id,
        message_type=CALLBACK_CONTINUATION_MESSAGE_TYPE,
        delivery_scope=DeliveryScope.SINGLE,
        silence_on_success=False,
        route_options={
            'mode': 'chain_continuation',
            'chain_edge_id': edge.edge_id,
            'chain_parent_job_id': edge.parent_job_id,
            'chain_child_job_id': edge.child_job_id,
            'chain_child_message_id': edge.child_message_id,
            'allowed_chain_targets': list(edge.diagnostics.get('allowed_chain_targets') or []),
            'bind_chain_workspace_tree': bool(edge.diagnostics.get('bind_chain_workspace_tree')),
        },
        body_artifact=body_artifact,
    )


def _continuation_body(*, edge: CallbackEdgeRecord, child_job, decision: CompletionDecision) -> str:
    original = str(edge.diagnostics.get('parent_body') or '').rstrip()
    child_task = str(edge.diagnostics.get('child_body') or '').rstrip()
    child_reply = _reply_summary(decision)
    parts = [
        'CCB result-chain continuation.',
        '',
        f'Original caller: {edge.original_caller}',
        f'Parent job: {edge.parent_job_id}',
        f'Child job: {child_job.job_id}',
        f'Child agent: {child_job.agent_name}',
        f'Child status: {child_job.status.value}',
    ]
    if original:
        parts.extend(['', 'Original task context:', original])
    if child_task:
        parts.extend(['', 'Delegated child task:', child_task])
    parts.extend(
        [
            '',
            'Child result:',
            child_reply or '(no reply body)',
            '',
            'Continue the original task using the child result.',
            'Finish this current response with the final result.',
            'Do not call ask, --chain, or --silence to the original caller; CCB will deliver this chain result upstream.',
        ]
    )
    return '\n'.join(parts)


def _decision_from_reply(reply, *, child_job, fallback_finished_at: str | None):
    if reply is None or child_job is None or child_job.terminal_decision is None:
        return None
    from completion.models import CompletionConfidence, CompletionStatus

    terminal = dict(child_job.terminal_decision or {})
    status = CompletionStatus(terminal.get('status') or child_job.status.value)
    confidence = CompletionConfidence(terminal.get('confidence') or 'degraded')
    diagnostics = dict(terminal.get('diagnostics') or {})
    if getattr(reply, 'reply_artifact', None) and 'reply_artifact' not in diagnostics:
        diagnostics['reply_artifact'] = dict(reply.reply_artifact)
    return CompletionDecision(
        terminal=True,
        status=status,
        reason=terminal.get('reason') or child_job.status.value,
        confidence=confidence,
        reply=reply.reply,
        anchor_seen=bool(terminal.get('anchor_seen')),
        reply_started=bool(terminal.get('reply_started')),
        reply_stable=bool(terminal.get('reply_stable')),
        provider_turn_ref=terminal.get('provider_turn_ref'),
        source_cursor=None,
        finished_at=terminal.get('finished_at') or reply.finished_at or fallback_finished_at,
        diagnostics=diagnostics,
    )


def _callback_body_summary(request: MessageEnvelope) -> str:
    body = _strip_ccb_guidance(request.body)
    if not request.body_artifact:
        return body
    artifact = dict(request.body_artifact)
    path = str(artifact.get('path') or '').strip()
    size = str(artifact.get('bytes') or '').strip()
    digest = str(artifact.get('sha256') or '').strip()
    lines = [
        preview_text(body, max_chars=800),
        '',
        'Full original request artifact:',
        f'path: {path}',
    ]
    if size:
        lines.append(f'bytes: {size}')
    if digest:
        lines.append(f'sha256: {digest}')
    return '\n'.join(line for line in lines if line is not None).rstrip()


def _reply_summary(decision: CompletionDecision) -> str:
    artifact = dict(decision.diagnostics or {}).get('reply_artifact')
    if not isinstance(artifact, dict):
        return decision.reply or ''
    path = str(artifact.get('path') or '').strip()
    size = str(artifact.get('bytes') or '').strip()
    digest = str(artifact.get('sha256') or '').strip()
    lines = [
        decision.reply or '(reply body stored as artifact)',
        '',
        'Full child reply artifact:',
        f'path: {path}',
    ]
    if size:
        lines.append(f'bytes: {size}')
    if digest:
        lines.append(f'sha256: {digest}')
    return '\n'.join(lines).rstrip()


def _strip_ccb_guidance(body: str) -> str:
    indexes = [
        index
        for marker in ('\n\nCCB_REPLY_MODE:', '\n\nCCB reply guidance:')
        if (index := body.find(marker)) >= 0
    ]
    return body[: min(indexes)].rstrip() if indexes else body


__all__ = [
    'CALLBACK_CONTINUATION_MESSAGE_TYPE',
    'CALLBACK_ROUTE_MODE',
    'callback_child_edge',
    'delegated_parent_edge',
    'delegated_terminal_job',
    'mark_parent_message_waiting',
    'mark_callback_done',
    'persist_delegated_terminal_job',
    'repair_callback_edges',
    'register_callback_edge',
    'request_callback_route',
    'sweep_callback_timeouts',
    'submit_callback_continuation',
    'terminalize_cancelled_parent_edge',
    'validate_nested_ask_request',
    'validate_callback_request',
]
