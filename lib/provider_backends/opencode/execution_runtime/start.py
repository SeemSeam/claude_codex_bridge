from __future__ import annotations

from pathlib import Path

from ccbd.api_models import JobRecord
from completion.models import CompletionSourceKind
from provider_execution.active import PreparedActiveStart, prepare_active_start
from provider_execution.base import ProviderRuntimeContext, ProviderSubmission
from provider_execution.common import no_wrap_requested, send_prompt_to_runtime_target


def start_submission(
    job: JobRecord,
    *,
    context: ProviderRuntimeContext | None,
    now: str,
    provider: str,
    load_session_fn,
    backend_for_session_fn,
    reader_cls,
    request_anchor_fn,
    wrap_prompt_fn,
) -> ProviderSubmission:
    prepared = prepare_active_start(
        job,
        context=context,
        provider=provider,
        source_kind=CompletionSourceKind.SESSION_SNAPSHOT,
        now=now,
        missing_session_reason="missing_opencode_session",
        load_session_fn=load_session_fn,
        backend_for_session_fn=backend_for_session_fn,
    )
    if not isinstance(prepared, PreparedActiveStart):
        return prepared

    reader_kwargs = {
        "work_dir": Path(prepared.session.work_dir),
        "project_id": _provider_session_project_id(prepared.session, provider) or "global",
        "session_id_filter": _provider_session_filter(prepared.session, provider),
    }
    storage_root = _provider_storage_root(prepared.session, provider)
    if storage_root:
        reader_kwargs["root"] = storage_root
    api_url = getattr(prepared.session, 'data', {}).get('opencode_api_url')
    if api_url:
        reader_kwargs['api_url'] = str(api_url)
    reader = reader_cls(**reader_kwargs)
    state = reader.capture_state()
    request_anchor = request_anchor_fn(job.job_id)
    no_wrap = no_wrap_requested(job)
    prompt = job.request.body if no_wrap else wrap_prompt_fn(job.request.body, request_anchor)
    send_prompt_to_runtime_target(prepared.backend, prepared.pane_id, prompt)

    return ProviderSubmission(
        job_id=job.job_id,
        agent_name=job.agent_name,
        provider=provider,
        accepted_at=now,
        ready_at=now,
        source_kind=CompletionSourceKind.SESSION_SNAPSHOT,
        reply="",
        diagnostics={"provider": provider, "mode": "active", "workspace_path": str(prepared.work_dir)},
        runtime_state={
            "mode": "active",
            "reader": reader,
            "state": state,
            "backend": prepared.backend,
            "pane_id": prepared.pane_id,
            "request_anchor": request_anchor,
            "next_seq": 1,
            "anchor_emitted": no_wrap,
            "reply_buffer": "",
            "session_path": state_session_path(state),
            "no_wrap": no_wrap,
        },
    )


def state_session_path(state: dict[str, object]) -> str:
    from .helpers import state_session_path as _state_session_path

    return _state_session_path(state)


def _provider_session_project_id(session, provider: str) -> str:
    value = getattr(session, f"{provider}_project_id", "")
    return str(value or "").strip()


def _provider_session_filter(session, provider: str) -> str | None:
    value = getattr(session, f"{provider}_session_id_filter", None)
    text = str(value or "").strip()
    return text or None


def _provider_storage_root(session, provider: str):
    value = getattr(session, f"{provider}_storage_root", None)
    text = str(value or "").strip()
    return text or None


__all__ = ["start_submission"]
