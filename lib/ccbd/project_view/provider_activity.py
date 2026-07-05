from __future__ import annotations

from pathlib import Path

from storage.paths import PathLayout


def provider_activity_evidence(
    *,
    project_root: Path,
    project_id: str,
    paths: object | None,
    agent_name: str,
    provider: str,
    runtime: object | None,
    now: str,
):
    from provider_hooks.activity import ProviderActivityEvidence, read_activity_evidence

    runtime_dir = _provider_runtime_dir(
        project_root=project_root,
        paths=paths,
        agent_name=agent_name,
        provider=provider,
    )
    if runtime_dir is None:
        return None
    return read_activity_evidence(
        runtime_dir,
        project_id=project_id,
        agent_name=agent_name,
        provider=provider,
        ccb_session_id=_runtime_ccb_session_id(runtime),
        provider_session_id=_runtime_provider_session_id(runtime),
        pane_id=_runtime_pane_id(runtime),
        workspace_path=getattr(runtime, 'workspace_path', None) if runtime is not None else None,
        now=now,
    )


def record_provider_activity_failure(
    *,
    project_root: Path,
    project_id: str,
    paths: object | None,
    agent_name: str,
    provider: str,
    runtime: object | None,
    reason: str,
    updated_at: str | None = None,
) -> None:
    from provider_hooks.activity import write_activity

    runtime_dir = _provider_runtime_dir(
        project_root=project_root,
        paths=paths,
        agent_name=agent_name,
        provider=provider,
    )
    if runtime_dir is None:
        return
    try:
        write_activity(
            provider=provider,
            project_id=project_id,
            agent_name=agent_name,
            runtime_dir=runtime_dir,
            state='failed',
            source='provider_pane',
            event_name='ProviderPaneError',
            ccb_session_id=_runtime_ccb_session_id(runtime),
            pane_id=_runtime_pane_id(runtime),
            workspace_path=getattr(runtime, 'workspace_path', None) if runtime is not None else None,
            provider_session_id=_runtime_provider_session_id(runtime),
            diagnostics={'reason': reason},
            updated_at=updated_at,
        )
    except Exception:
        return


def _provider_runtime_dir(*, project_root: Path, paths: object | None, agent_name: str, provider: str) -> Path | None:
    layout = paths or PathLayout(project_root)
    resolver = getattr(layout, 'agent_provider_runtime_dir', None)
    if not callable(resolver):
        return None
    try:
        return Path(resolver(agent_name, provider))
    except Exception:
        return None


def _runtime_ccb_session_id(runtime: object | None) -> str | None:
    if runtime is None:
        return None
    text = str(getattr(runtime, 'ccb_session_id', '') or '').strip()
    if text:
        return text
    return None


def _runtime_provider_session_id(runtime: object | None) -> str | None:
    if runtime is None:
        return None
    text = str(getattr(runtime, 'session_id', '') or '').strip()
    if text:
        return text
    return None


def _runtime_pane_id(runtime: object | None) -> str | None:
    if runtime is None:
        return None
    for field_name in ('active_pane_id', 'pane_id'):
        text = str(getattr(runtime, field_name, '') or '').strip()
        if text.startswith('%'):
            return text
    return None


__all__ = ['provider_activity_evidence', 'record_provider_activity_failure']
