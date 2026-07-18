from __future__ import annotations

from ccbd.api_models import JobRecord, JobStatus
from completion.models import CompletionDecision
from message_bureau.reply_payloads import compose_reply_payload
from mailbox_kernel import InboundEventRecord, InboundEventStatus, InboundEventType

from .facade_recording_common import (
    delivered_reply_text,
    mailbox_actor,
    new_id,
    reply_status_for_job,
)
from .facade_recording_terminal_attempts import record_attempt_terminal
from .facade_state import rebuild_mailbox_summary, refresh_message_state
from .models import ReplyRecord, ReplyTerminalStatus


def record_reply(
    service,
    job: JobRecord,
    decision: CompletionDecision,
    *,
    finished_at: str,
    deliver_to_caller: bool = True,
) -> str | None:
    attempt = service._attempt_store.get_latest_by_job_id(job.job_id)
    if attempt is None:
        return None

    reply_text = delivered_reply_text(job, decision)
    reply_artifact = _reply_artifact_from_decision(decision)
    empty_cancel_notice = (
        job.status is JobStatus.CANCELLED
        and not reply_text.strip()
        and reply_artifact is None
    )
    reply_id = new_id('rep')
    diagnostics = {
        'reason': decision.reason,
        'status': job.status.value,
        'provider_turn_ref': decision.provider_turn_ref,
        'decision_diagnostics': dict(decision.diagnostics or {}),
        'silence_on_success': bool(job.request.silence_on_success),
    }
    if empty_cancel_notice:
        diagnostics.update(
            {
                'notice': True,
                'notice_kind': 'cancelled',
                'delivery_mode': 'auto_consumed_control_notice',
            }
        )
    service._reply_store.append(
        ReplyRecord(
            reply_id=reply_id,
            message_id=attempt.message_id,
            attempt_id=attempt.attempt_id,
            agent_name=job.agent_name,
            terminal_status=reply_status_for_job(job.status),
            reply=reply_text,
            reply_artifact=reply_artifact,
            diagnostics=diagnostics,
            finished_at=finished_at,
        )
    )

    caller_mailbox = mailbox_actor(service, job.request.from_actor) if deliver_to_caller else None
    if caller_mailbox is not None:
        if empty_cancel_notice:
            _record_consumed_completion_notice(
                service,
                caller_mailbox=caller_mailbox,
                message_id=attempt.message_id,
                attempt_id=attempt.attempt_id,
                reply_id=reply_id,
                finished_at=finished_at,
            )
        else:
            queue_reply_delivery(
                service,
                caller_mailbox=caller_mailbox,
                message_id=attempt.message_id,
                attempt_id=attempt.attempt_id,
                reply_id=reply_id,
                finished_at=finished_at,
            )

    refresh_message_state(service, attempt.message_id, updated_at=finished_at)
    return reply_id


def record_notice(
    service,
    job: JobRecord,
    *,
    reply: str,
    diagnostics: dict[str, object] | None,
    finished_at: str,
    terminal_status: ReplyTerminalStatus = ReplyTerminalStatus.INCOMPLETE,
    deliver_to_actor: str | None = None,
) -> str | None:
    attempt = service._attempt_store.get_latest_by_job_id(job.job_id)
    if attempt is None:
        return None

    reply_id = new_id('rep')
    payload = dict(diagnostics or {})
    payload.setdefault('status', job.status.value)
    payload.setdefault('notice', True)
    reply_text, reply_artifact = _spill_notice_reply_if_needed(
        service,
        job=job,
        reply=reply or '',
        reply_id=reply_id,
        finished_at=finished_at,
    )
    service._reply_store.append(
        ReplyRecord(
            reply_id=reply_id,
            message_id=attempt.message_id,
            attempt_id=attempt.attempt_id,
            agent_name=job.agent_name,
            terminal_status=terminal_status,
            reply=reply_text,
            reply_artifact=reply_artifact,
            diagnostics=payload,
            finished_at=finished_at,
        )
    )

    target_actor = deliver_to_actor if deliver_to_actor is not None else job.request.from_actor
    caller_mailbox = mailbox_actor(service, target_actor)
    if caller_mailbox is not None:
        queue_reply_delivery(
            service,
            caller_mailbox=caller_mailbox,
            message_id=attempt.message_id,
            attempt_id=attempt.attempt_id,
            reply_id=reply_id,
            finished_at=finished_at,
        )

    refresh_message_state(service, attempt.message_id, updated_at=finished_at)
    return reply_id


def record_terminal(
    service,
    job: JobRecord,
    decision: CompletionDecision,
    *,
    finished_at: str,
    deliver_to_caller: bool = True,
    record_reply_enabled: bool = True,
) -> str | None:
    record_attempt_terminal(service, job, decision, finished_at=finished_at)
    if not record_reply_enabled:
        return None
    return record_reply(service, job, decision, finished_at=finished_at, deliver_to_caller=deliver_to_caller)


def queue_reply_delivery(
    service,
    *,
    caller_mailbox: str,
    message_id: str,
    attempt_id: str,
    reply_id: str,
    finished_at: str,
) -> None:
    service._inbound_store.append(
        InboundEventRecord(
            inbound_event_id=new_id('iev'),
            agent_name=caller_mailbox,
            event_type=InboundEventType.TASK_REPLY,
            message_id=message_id,
            attempt_id=attempt_id,
            payload_ref=compose_reply_payload(reply_id),
            priority=10,
            status=InboundEventStatus.QUEUED,
            created_at=finished_at,
        )
    )
    service._mailbox_kernel.apply_incremental_summary_update(
        caller_mailbox,
        queue_delta=1,
        pending_reply_delta=1,
        updated_at=finished_at,
    )


def _record_consumed_completion_notice(
    service,
    *,
    caller_mailbox: str,
    message_id: str,
    attempt_id: str,
    reply_id: str,
    finished_at: str,
) -> None:
    service._inbound_store.append(
        InboundEventRecord(
            inbound_event_id=new_id('iev'),
            agent_name=caller_mailbox,
            event_type=InboundEventType.COMPLETION_NOTICE,
            message_id=message_id,
            attempt_id=attempt_id,
            payload_ref=compose_reply_payload(reply_id),
            priority=10,
            status=InboundEventStatus.CONSUMED,
            created_at=finished_at,
            started_at=finished_at,
            finished_at=finished_at,
        )
    )


def _reply_artifact_from_decision(decision: CompletionDecision) -> dict[str, object] | None:
    artifact = dict(decision.diagnostics or {}).get('reply_artifact')
    return dict(artifact) if isinstance(artifact, dict) else None


def _spill_notice_reply_if_needed(service, *, job: JobRecord, reply: str, reply_id: str, finished_at: str):
    from storage.text_artifacts import maybe_spill_text

    return maybe_spill_text(
        service._layout,
        text=reply,
        kind='notice-reply',
        owner_id=f'{job.job_id}-{reply_id}',
        prefix=f'CCB notice reply for job {job.job_id} is larger than 4 KiB and was stored as an artifact.',
        now=finished_at,
    )


__all__ = [
    'record_notice',
    'record_reply',
    'record_terminal',
]
