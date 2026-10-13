from __future__ import annotations

from .workflow_policy import agentic_loop_enabled

from ccbd.api_models import MessageEnvelope, SubmitReceipt
from message_bureau import AttemptState, MessageStore

from .lifecycle_start import tick_jobs
from .frontdesk_direct_handoff import is_frontdesk_submission, submit_frontdesk_direct_handoff
from .detailer_replan_handoff import is_task_detailer_submission, submit_detailer_replan_handoff
from .submission import (
    _append_submission_job,
    _build_job_record,
    _enqueue_submitted_job,
    _ensure_agent_target_ready,
    _JobDraft,
    _latest_attempts_by_agent,
    _plan_agent_submission,
    _plan_message_resubmission,
    _resolve_retry_attempt,
    _submit_plan,
)

_TERMINAL_ATTEMPT_STATES = frozenset(
    {
        AttemptState.COMPLETED,
        AttemptState.INCOMPLETE,
        AttemptState.FAILED,
        AttemptState.CANCELLED,
        AttemptState.SUPERSEDED,
        AttemptState.DEAD_LETTER,
    }
)
_RETRY_CONTINUE_BODY = 'continue'
_RETRY_DELIVERY_MODE_OPTION = 'retry_delivery_mode'
_RETRY_SOURCE_JOB_ID_OPTION = 'retry_source_job_id'


def submit_jobs(dispatcher, request: MessageEnvelope) -> SubmitReceipt:
    accepted_at = dispatcher._clock()
    def submit(message: MessageEnvelope = request) -> SubmitReceipt:
        receipt, _ = _submit_plan(
            dispatcher,
            _plan_agent_submission(dispatcher, message),
            accepted_at=accepted_at,
        )
        return receipt

    if agentic_loop_enabled(dispatcher) and is_frontdesk_submission(request):
        return submit_frontdesk_direct_handoff(
            dispatcher,
            request,
            accepted_at=accepted_at,
            submit=submit,
        )
    if agentic_loop_enabled(dispatcher) and is_task_detailer_submission(request):
        return submit_detailer_replan_handoff(
            dispatcher,
            request,
            accepted_at=accepted_at,
            submit=submit,
        )
    return submit()


def resubmit_message(dispatcher, message_id: str) -> dict[str, object]:
    accepted_at = dispatcher._clock()
    plan = _plan_message_resubmission(dispatcher, message_id)
    receipt, new_message_id = _submit_plan(dispatcher, plan, accepted_at=accepted_at)
    return {
        'accepted_at': accepted_at,
        'original_message_id': message_id,
        'message_id': new_message_id,
        'submission_id': receipt.submission_id,
        'jobs': [job.to_record() for job in receipt.jobs],
    }


def retry_attempt(dispatcher, target: str) -> dict[str, object]:
    if dispatcher._message_bureau is None:
        raise dispatcher._dispatch_error('retry requires message bureau support')
    accepted_at = dispatcher._clock()
    original_attempt = _resolve_retry_attempt(dispatcher, target)
    if original_attempt.attempt_state not in _TERMINAL_ATTEMPT_STATES:
        raise dispatcher._dispatch_error(f'attempt is still active: {original_attempt.attempt_id}')
    if original_attempt.attempt_state is AttemptState.COMPLETED:
        raise dispatcher._dispatch_error(f'retry is not allowed for completed attempts: {original_attempt.attempt_id}')

    latest_attempts = _latest_attempts_by_agent(dispatcher, original_attempt.message_id)
    latest_attempt = latest_attempts.get(original_attempt.agent_name)
    if latest_attempt is None:
        raise dispatcher._dispatch_error(
            f'message is missing attempt lineage for agent: {original_attempt.agent_name}'
        )
    if latest_attempt.attempt_id != original_attempt.attempt_id:
        raise dispatcher._dispatch_error(
            f'retry requires latest attempt for agent: {original_attempt.agent_name}'
        )

    message = MessageStore(dispatcher._layout).get_latest(original_attempt.message_id)
    if message is None:
        raise dispatcher._dispatch_error(f'message not found for attempt: {original_attempt.attempt_id}')

    _ensure_agent_target_ready(dispatcher, original_attempt.agent_name)
    dispatcher._validate_targets_available((original_attempt.agent_name,))

    current = dispatcher._job_store.get_latest(original_attempt.agent_name, original_attempt.job_id)
    if current is None:
        raise dispatcher._dispatch_error(f'job not found for attempt: {original_attempt.attempt_id}')

    retry_request = _retry_request_for_job(current)
    retry_provider_options = _retry_provider_options_for_job(current)
    job_id = dispatcher._new_id('job')
    job, status = _build_job_record(
        dispatcher,
        _JobDraft(
            agent_name=current.agent_name,
            provider=current.provider,
            request=retry_request,
            target_kind=current.target_kind,
            target_name=current.target_name,
            provider_instance=current.provider_instance,
            provider_options=retry_provider_options,
            workspace_path=current.workspace_path,
        ),
        job_id=job_id,
        submission_id=message.submission_id,
        accepted_at=accepted_at,
    )
    receipt = _enqueue_submitted_job(dispatcher, job, status=status, accepted_at=accepted_at)
    attempt_id = dispatcher._message_bureau.record_retry_attempt(
        original_attempt.message_id,
        job,
        accepted_at=accepted_at,
    )
    _append_submission_job(
        dispatcher,
        message.submission_id,
        job_id=job.job_id,
        updated_at=accepted_at,
    )
    return {
        'accepted_at': accepted_at,
        'target': target,
        'message_id': original_attempt.message_id,
        'original_attempt_id': original_attempt.attempt_id,
        'attempt_id': attempt_id,
        'job_id': receipt.job_id,
        'agent_name': receipt.agent_name,
        'status': receipt.status.value,
    }


def _retry_request_for_job(job) -> MessageEnvelope:
    if not _should_retry_with_continue(job):
        return job.request
    return MessageEnvelope(
        project_id=job.request.project_id,
        to_agent=job.request.to_agent,
        from_actor=job.request.from_actor,
        body=_RETRY_CONTINUE_BODY,
        task_id=job.request.task_id,
        reply_to=job.request.reply_to,
        message_type=job.request.message_type,
        delivery_scope=job.request.delivery_scope,
        silence_on_success=job.request.silence_on_success,
        route_options=dict(job.request.route_options or {}),
        body_artifact=dict(job.request.body_artifact) if job.request.body_artifact else None,
    )


def _retry_provider_options_for_job(job) -> dict[str, object] | None:
    options = dict(job.provider_options or {})
    if _should_retry_with_continue(job):
        options[_RETRY_DELIVERY_MODE_OPTION] = 'continue'
    else:
        options.pop(_RETRY_DELIVERY_MODE_OPTION, None)
    options[_RETRY_SOURCE_JOB_ID_OPTION] = job.job_id
    return options


def _should_retry_with_continue(job) -> bool:
    if str(job.request.message_type or '').strip().lower() != 'ask':
        return False
    options = dict(job.provider_options or {})
    if str(options.get(_RETRY_DELIVERY_MODE_OPTION) or '').strip().lower() == 'continue':
        return True
    terminal = dict(job.terminal_decision or {})
    return bool(terminal.get('anchor_seen') or terminal.get('reply_started'))


__all__ = ['resubmit_message', 'retry_attempt', 'submit_jobs', 'tick_jobs']
