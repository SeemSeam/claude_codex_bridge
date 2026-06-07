from __future__ import annotations

from ccbd.api_models import JobRecord
from completion.models import CompletionSourceKind
from provider_core.protocol import request_anchor_for_job
from provider_execution.base import ProviderRuntimeContext, ProviderSubmission
from provider_execution.common import error_submission, send_prompt_to_runtime_target

from ..comm import AgyPaneReader
from ..protocol import wrap_agy_prompt
from ..session import load_project_session
from .helpers import resolve_work_dir


_PANE_LINES_DEFAULT = 200


def start_submission(
    job: JobRecord,
    *,
    context: ProviderRuntimeContext | None,
    now: str,
    provider: str,
) -> ProviderSubmission:
    work_dir = resolve_work_dir(job, context)
    if work_dir is None:
        return error_submission(
            job,
            provider=provider,
            now=now,
            source_kind=CompletionSourceKind.TERMINAL_TEXT,
            reason='runtime_unavailable',
            error='work_dir_missing',
        )

    instance = (job.agent_name or '').strip().lower() or None
    session = None
    load_error: str | None = None
    try:
        session = load_project_session(work_dir, instance=instance)
        if session is None and instance is not None:
            session = load_project_session(work_dir)
    except Exception as exc:
        load_error = f'load_session_failed:{exc!r}'

    if session is None:
        return error_submission(
            job,
            provider=provider,
            now=now,
            source_kind=CompletionSourceKind.TERMINAL_TEXT,
            reason='runtime_unavailable',
            error=load_error or 'agy_session_file_missing',
        )

    pane_id = session.pane_id
    if not pane_id:
        return error_submission(
            job,
            provider=provider,
            now=now,
            source_kind=CompletionSourceKind.TERMINAL_TEXT,
            reason='pane_unavailable',
            error='pane_id_missing_in_session',
        )

    try:
        backend = session.backend()
    except Exception as exc:
        backend = None
        backend_error = f'backend_resolve_failed:{exc!r}'
    else:
        backend_error = None

    if backend is None:
        return error_submission(
            job,
            provider=provider,
            now=now,
            source_kind=CompletionSourceKind.TERMINAL_TEXT,
            reason='backend_unavailable',
            error=backend_error or 'terminal_backend_unavailable',
        )

    req_id = request_anchor_for_job(job.job_id)
    prompt = wrap_agy_prompt(job.request.body or '', req_id)

    reader = AgyPaneReader(backend=backend, pane_id=pane_id, lines=_PANE_LINES_DEFAULT)

    send_error: str | None = None
    try:
        send_prompt_to_runtime_target(backend, pane_id, prompt)
    except Exception as exc:
        send_error = f'send_text_failed:{exc!r}'

    diagnostics: dict[str, object] = {
        'provider': provider,
        'mode': 'pane_quiet',
        'pane_id': pane_id,
        'req_id': req_id,
        'task_id': job.request.task_id,
        'workspace_path': str(work_dir),
    }
    if send_error:
        diagnostics['send_error'] = send_error

    return ProviderSubmission(
        job_id=job.job_id,
        agent_name=job.agent_name,
        provider=provider,
        accepted_at=now,
        ready_at=now,
        source_kind=CompletionSourceKind.TERMINAL_TEXT,
        reply='',
        diagnostics=diagnostics,
        runtime_state={
            'mode': 'pane_quiet',
            'reader': reader,
            'backend': backend,
            'pane_id': pane_id,
            'req_id': req_id,
            'pane_lines': _PANE_LINES_DEFAULT,
            'started_at': now,
            'last_hash': None,
            'last_change_at': now,
            'last_poll_at': now,
            'prompt_sent': send_error is None,
            'send_error': send_error,
            'snapshot_errors': 0,
            'next_seq': 1,
        },
    )


__all__ = ['start_submission']
