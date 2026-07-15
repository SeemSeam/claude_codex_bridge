from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from typing import Callable

from ccbd.api_models import JobRecord
from completion.models import CompletionSourceKind
from provider_core.instance_resolution import named_agent_instance
from provider_execution.active import PreparedActiveStart, prepare_active_start
from provider_execution.base import ProviderRuntimeContext, ProviderSubmission
from provider_execution.common import no_wrap_requested, normalize_session_path, send_prompt_to_runtime_target

from .readiness import wait_for_runtime_ready


def start_active_submission(
    adapter,
    job: JobRecord,
    *,
    context: ProviderRuntimeContext | None,
    now: str,
    load_session_fn: Callable[[Path, str], object | None],
    backend_for_session_fn: Callable[[dict], object | None],
    reader_factory: Callable[[object, Path | None], object],
    request_anchor_fn: Callable[[str | None], str],
    wrap_prompt_fn: Callable[[str, str], str],
) -> ProviderSubmission:
    prepared = prepare_active_start(
        job,
        context=context,
        provider=adapter.provider,
        source_kind=CompletionSourceKind.PROTOCOL_EVENT_STREAM,
        now=now,
        missing_session_reason='missing_codex_session',
        load_session_fn=load_session_fn,
        backend_for_session_fn=backend_for_session_fn,
    )
    if not isinstance(prepared, PreparedActiveStart):
        return prepared

    reader = reader_factory(prepared.session, None)
    state = reader.capture_state()
    request_anchor = request_anchor_fn(job.job_id)
    reply_delivery = str(job.request.message_type or '').strip().lower() == 'reply_delivery'
    # Reply delivery is transport work, but Codex still needs a durable request
    # anchor so ccbd can distinguish "sent to pane" from "accepted by Codex".
    no_wrap = no_wrap_requested(job) and not reply_delivery
    prompt = job.request.body if no_wrap else wrap_prompt_fn(job.request.body, request_anchor)
    session_path = state_session_path(state)
    if not wait_for_runtime_ready(prepared.backend, prepared.pane_id):
        return _runtime_not_ready_submission(
            job,
            provider=adapter.provider,
            now=now,
            source_kind=CompletionSourceKind.PROTOCOL_EVENT_STREAM,
            work_dir=prepared.work_dir,
            pane_id=prepared.pane_id,
        )
    send_prompt_to_runtime_target(prepared.backend, prepared.pane_id, prompt)

    return ProviderSubmission(
        job_id=job.job_id,
        agent_name=job.agent_name,
        provider=adapter.provider,
        accepted_at=now,
        ready_at=now,
        source_kind=CompletionSourceKind.PROTOCOL_EVENT_STREAM,
        reply='',
        diagnostics={'provider': adapter.provider, 'mode': 'active', 'workspace_path': str(prepared.work_dir)},
        runtime_state={
            'mode': 'active',
            'reader': reader,
            'state': state,
            'backend': prepared.backend,
            'pane_id': prepared.pane_id,
            'request_anchor': request_anchor,
            'next_seq': 1,
            'anchor_seen': no_wrap,
            'bound_turn_id': '',
            'bound_task_id': '',
            'reply_buffer': '',
            'last_agent_message': '',
            'last_final_answer': '',
            'last_assistant_message': '',
            'last_assistant_signature': '',
            'session_path': session_path,
            'workspace_path': str(prepared.work_dir),
            'no_wrap': no_wrap,
            'delivery_state': 'not_required' if no_wrap else 'pending_anchor',
            'delivery_started_at': '' if no_wrap else now,
            'delivery_last_progress_at': '' if no_wrap else now,
            'delivery_timeout_s': 0.0 if no_wrap else resolved_delivery_timeout_s(),
            'delivery_target_pane_id': prepared.pane_id,
            'delivery_target_session_path': session_path,
            'delivery_confirmed_at': '',
            'reply_delivery_complete_on_dispatch': reply_delivery,
        },
    )


def resume_submission(
    job: JobRecord,
    submission: ProviderSubmission,
    *,
    context: ProviderRuntimeContext | None,
    load_session_fn: Callable[[Path, str], object | None],
    backend_for_session_fn: Callable[[dict], object | None],
    reader_factory: Callable[[object, Path | None], object],
) -> ProviderSubmission | None:
    if context is None or not context.workspace_path:
        return None
    state = dict(submission.runtime_state)
    if str(state.get('mode') or 'passive') != 'active':
        return None
    work_dir = Path(context.workspace_path).expanduser()
    session = load_session_fn(work_dir, job.agent_name)
    if session is None:
        return None
    ok, pane_or_err = session.ensure_pane()
    if not ok:
        return None
    backend = backend_for_session_fn(session.data)
    if backend is None:
        return None
    preferred_log = preferred_log_path(state)
    reader = reader_factory(session, preferred_log)
    return replace(
        submission,
        runtime_state={
            **state,
            'reader': reader,
            'backend': backend,
            'pane_id': str(pane_or_err),
            'mode': 'active',
            'session_path': state.get('session_path') or (str(preferred_log) if preferred_log else ''),
            'workspace_path': str(work_dir),
        },
    )


def load_session(load_project_session_fn, work_dir: Path, *, agent_name: str):
    instance = named_agent_instance(agent_name, primary_agent='codex')
    if instance is not None:
        session = load_project_session_fn(work_dir, instance)
        if session is not None:
            return session
        return None
    return load_project_session_fn(work_dir)


def preferred_log_path(state: dict[str, object]) -> Path | None:
    raw = state.get('session_path') or state_session_path(state.get('state') or {})
    session_path = normalize_session_path(raw)
    if not session_path:
        return None
    try:
        return Path(session_path).expanduser()
    except Exception:
        return None


def state_session_path(state: dict[str, object]) -> str:
    return normalize_session_path(state.get('log_path'))


def resolved_delivery_timeout_s(default: float = 120.0) -> float:
    try:
        return max(0.0, float(os.environ.get('CCB_CODEX_DELIVERY_TIMEOUT_S', default)))
    except Exception:
        return max(0.0, default)


def _runtime_not_ready_submission(
    job: JobRecord,
    *,
    provider: str,
    now: str,
    source_kind: CompletionSourceKind,
    work_dir: Path,
    pane_id: str,
) -> ProviderSubmission:
    diagnostics = {
        'provider': provider,
        'mode': 'error',
        'reason': 'runtime_unavailable',
        'error': 'codex_runtime_not_ready',
        'error_type': 'codex_runtime_not_ready',
        'delivery_retryable': True,
        'delivery_failure_kind': 'runtime_not_ready',
        'delivery_workspace_path': str(work_dir),
        'delivery_target_pane_id': str(pane_id),
    }
    return ProviderSubmission(
        job_id=job.job_id,
        agent_name=job.agent_name,
        provider=provider,
        accepted_at=now,
        ready_at=now,
        source_kind=source_kind,
        reply='',
        diagnostics=diagnostics,
        runtime_state={
            'mode': 'error',
            'reason': 'runtime_unavailable',
            'error': 'codex_runtime_not_ready',
            'diagnostics': diagnostics,
            'next_seq': 1,
        },
    )


__all__ = [
    'load_session',
    'preferred_log_path',
    'resume_submission',
    'resolved_delivery_timeout_s',
    'start_active_submission',
    'state_session_path',
]
