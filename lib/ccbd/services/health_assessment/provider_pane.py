from __future__ import annotations

from pathlib import Path

from provider_core.session_binding_evidence import session_terminal

from ..provider_runtime_facts import load_provider_session
from .models import ProviderPaneAssessment
from .tmux import pane_outside_project_namespace, session_backend, tmux_pane_state

_PROVIDER_SIGNAL_TO_HEALTH = {
    'usage_limit': 'usage-limited',
    'api_error': 'api-error',
    'auth_failed': 'auth-failed',
    'config_error': 'config-error',
    'failed': 'provider-error',
}


def health_from_pane_signal(signal_state: str | None) -> str | None:
    """Map a parsed pane content signal state to a health label.

    Returns None when the signal does not change health (unknown/working/etc.).
    """
    if not signal_state:
        return None
    return _PROVIDER_SIGNAL_TO_HEALTH.get(signal_state)


def assess_provider_pane(*, runtime, registry, session_bindings, namespace_state_store) -> ProviderPaneAssessment | None:
    if not _is_tmux_runtime(runtime):
        return None
    binding = _resolve_binding(runtime=runtime, registry=registry, session_bindings=session_bindings)
    if binding is None:
        return None
    workspace_path = _workspace_path(runtime)
    if not workspace_path:
        return None
    session = load_provider_session(binding, Path(workspace_path), runtime.agent_name)
    if session is None:
        return _build_assessment(binding=binding, health='session-missing', pane_state='missing')

    terminal = _session_terminal_name(session)
    if terminal != 'tmux':
        return _build_assessment(
            binding=binding,
            session=session,
            terminal=terminal,
            health='healthy',
        )

    pane_state = _tmux_pane_state(
        runtime=runtime,
        session=session,
        namespace_state_store=namespace_state_store,
    )

    signal_state: str | None = None
    signal_reason: str | None = None
    retry_after: str | None = None
    pane_tail: str | None = None
    health = health_from_pane_state(pane_state)

    # When the pane is alive, also inspect its CONTENT for provider-side error
    # banners (usage-limit / rate-limit / auth / api errors) that liveness
    # checks cannot see. Codex is parsed today; other providers stay content-
    # blind until their pane parsers exist (later wave) and simply keep None.
    if pane_state == 'alive':
        provider = _provider_name(registry, runtime)
        content = _capture_pane_content(session, pane_id=str(getattr(session, 'pane_id', '') or '').strip())
        if content:
            pane_tail = _tail_lines(content, lines=20)
            parsed = _parse_provider_pane_content(provider, content)
            if parsed is not None:
                signal_state = parsed.state
                signal_reason = parsed.reason
                retry_after = parsed.retry_after
                signal_health = health_from_pane_signal(signal_state)
                if signal_health is not None:
                    health = signal_health

    return _build_assessment(
        binding=binding,
        session=session,
        terminal=terminal,
        pane_state=pane_state,
        health=health,
        pane_signal_state=signal_state,
        pane_signal_reason=signal_reason,
        retry_after=retry_after,
        pane_tail=pane_tail,
    )


def health_from_pane_state(pane_state: str) -> str:
    return {
        'alive': 'healthy',
        'missing': 'pane-missing',
        'foreign': 'pane-foreign',
    }.get(pane_state, 'pane-dead')


def _is_tmux_runtime(runtime) -> bool:
    return str(runtime.runtime_ref or '').strip().startswith('tmux:')


def _resolve_binding(*, runtime, registry, session_bindings):
    spec = registry.spec_for(runtime.agent_name)
    return session_bindings.get(spec.provider)


def _provider_name(registry, runtime) -> str:
    spec = registry.spec_for(runtime.agent_name)
    return str(getattr(spec, 'provider', '') or '').strip().lower()


def _workspace_path(runtime) -> str:
    return str(runtime.workspace_path or '').strip()


def _session_terminal_name(session) -> str | None:
    return str(session_terminal(session) or '').strip().lower() or None


def _tmux_pane_state(*, runtime, session, namespace_state_store) -> str:
    pane_id = str(getattr(session, 'pane_id', '') or '').strip()
    backend = session_backend(session)
    pane_state = tmux_pane_state(session, backend, pane_id)
    if pane_state != 'alive':
        return pane_state
    if pane_outside_project_namespace(
        runtime=runtime,
        namespace_state_store=namespace_state_store,
        backend=backend,
        pane_id=pane_id,
    ):
        return 'foreign'
    return pane_state


def _capture_pane_content(session, *, pane_id: str) -> str | None:
    """Capture pane content via the same tmux backend used for liveness checks.

    The backend (from session_backend) exposes get_pane_content(pane_id,
    lines=N) when wired to the tmux pane queries service; if absent or it
    raises, we return None and content-aware health is skipped silently.
    """
    if not pane_id:
        return None
    backend = session_backend(session)
    if backend is None:
        return None
    getter = getattr(backend, 'get_pane_content', None)
    if not callable(getter):
        return None
    try:
        return str(getter(pane_id, lines=30) or '') or None
    except Exception:
        return None


def _tail_lines(text: str, *, lines: int = 20) -> str:
    cleaned = (text or '').strip()
    if not cleaned:
        return None
    parts = [line.rstrip() for line in cleaned.splitlines() if line.strip()]
    if not parts:
        return None
    return '\n'.join(parts[-lines:])


def _parse_provider_pane_content(provider: str, content: str):
    """Parse captured pane content for the provider. Returns a PaneStatus or None.

    Only codex has a content parser today. Unknown/non-codex providers return
    None so content fields stay None without crashing (claude/kimi parsers are
    a later wave).

    Uses ``strict=True`` (the HIGH-CONFIDENCE marker tier) so that health does
    NOT flip to usage-limited / auth-failed / api-error / config-error on a
    broad keyword match. A healthy agent whose pane output merely *discusses*
    usage limits / quotas / api errors (e.g. one researching the classification
    topic) must not be terminalized by the B.2 health bridge. Broad markers
    remain available to the diagnostics-only delivery path.
    """
    if provider != 'codex':
        return None
    # Imported lazily so the health-assessment module stays import-clean even
    # when provider_pane_status is not on sys.path in some unit contexts.
    from provider_pane_status.codex_pane import parse_codex_pane_status

    return parse_codex_pane_status(content, strict=True)


def _build_assessment(
    *,
    binding,
    health: str,
    session=None,
    terminal: str | None = None,
    pane_state: str | None = None,
    pane_signal_state: str | None = None,
    pane_signal_reason: str | None = None,
    retry_after: str | None = None,
    pane_tail: str | None = None,
) -> ProviderPaneAssessment:
    return ProviderPaneAssessment(
        binding=binding,
        session=session,
        terminal=terminal,
        pane_state=pane_state,
        health=health,
        pane_signal_state=pane_signal_state,
        pane_signal_reason=pane_signal_reason,
        retry_after=retry_after,
        pane_tail=pane_tail,
    )
