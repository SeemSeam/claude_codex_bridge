from __future__ import annotations


def format_reply_delivery_body(dispatcher, reply) -> str:
    source_job = _source_job_for_reply(dispatcher, reply)
    if _is_heartbeat_notice(reply):
        return format_heartbeat_delivery_body(reply, source_job=source_job)
    header = _reply_header(reply, source_job=source_job)
    body = reply.reply or '(empty reply)'
    if str(reply.terminal_status.value or '').lower() != 'completed':
        no_reply_reason = _no_reply_reason_for_reply(reply, source_job=source_job)
        if no_reply_reason:
            body = f'[no_reply_reason={no_reply_reason}]\n{body}'
    return '\n'.join((' '.join(header), '', body)).rstrip()


def format_heartbeat_delivery_body(reply, *, source_job) -> str:
    lines = [_heartbeat_header(reply, source_job=source_job)]
    body = str(reply.reply or '').strip()
    if body:
        lines.extend(['', body])
    else:
        lines.extend(['', '(empty notice)'])
    return '\n'.join(lines).rstrip()


def format_silence_seconds(value) -> str:
    try:
        seconds = int(round(float(value)))
    except Exception:
        return str(value)
    return f'{seconds}s'


def _source_job_for_reply(dispatcher, reply):
    attempt = dispatcher._message_bureau_control._attempt_store.get_latest(reply.attempt_id)
    if attempt is None:
        return None
    source_job = dispatcher.get_job(attempt.job_id) if hasattr(dispatcher, 'get_job') else None
    if source_job is not None:
        return source_job
    from ..records import get_job

    return get_job(dispatcher, attempt.job_id)


def _is_heartbeat_notice(reply) -> bool:
    return str(reply.diagnostics.get('notice_kind') or '').strip().lower() == 'heartbeat'


def _reply_header(reply, *, source_job) -> list[str]:
    header = [
        'CCB_REPLY',
        f'from={reply.agent_name}',
        f'reply={reply.reply_id}',
        f'status={reply.terminal_status.value}',
    ]
    if source_job is None:
        return header
    header.append(f'job={source_job.job_id}')
    task_id = str(source_job.request.task_id or '').strip()
    if task_id:
        header.append(f'task={task_id}')
    return header


def _heartbeat_header(reply, *, source_job) -> str:
    diagnostics = dict(reply.diagnostics or {})
    header = (
        'CCB_NOTICE '
        f'kind=heartbeat '
        f'from={reply.agent_name} '
        f'reply={reply.reply_id}'
    )
    job_id = _heartbeat_job_id(diagnostics, source_job=source_job)
    if job_id:
        header = f'{header} job={job_id}'
    task_id = _heartbeat_task_id(source_job)
    if task_id:
        header = f'{header} task={task_id}'
    last_progress_at = str(diagnostics.get('last_progress_at') or '').strip()
    if last_progress_at:
        header = f'{header} last_progress={last_progress_at}'
    silence_seconds = diagnostics.get('heartbeat_silence_seconds')
    if silence_seconds is not None:
        header = f'{header} silent_for={format_silence_seconds(silence_seconds)}'
    return header


def _heartbeat_job_id(diagnostics: dict, *, source_job) -> str | None:
    job_id = str(diagnostics.get('job_id') or '').strip()
    if job_id:
        return job_id
    if source_job is not None:
        return source_job.job_id
    return None


def _no_reply_reason_for_reply(reply, *, source_job) -> str | None:
    diagnostics = dict(reply.diagnostics or {})
    decision_diagnostics = diagnostics.get('decision_diagnostics')
    if isinstance(decision_diagnostics, dict):
        reason = str(decision_diagnostics.get('no_reply_reason') or '').strip()
        if reason:
            return reason
    if source_job is not None:
        terminal = getattr(source_job, 'terminal_decision', None)
        if isinstance(terminal, dict):
            terminal_diagnostics = terminal.get('diagnostics') or {}
            reason = str(terminal_diagnostics.get('no_reply_reason') or '').strip()
            if reason:
                return reason
    return None


def _heartbeat_task_id(source_job) -> str | None:
    if source_job is None:
        return None
    task_id = str(source_job.request.task_id or '').strip()
    return task_id or None


__all__ = ['format_reply_delivery_body']
