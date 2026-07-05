from __future__ import annotations

from types import SimpleNamespace

from ccbd.services.health_assessment.provider_pane import (
    assess_provider_pane,
    health_from_pane_signal,
)


def _runtime(**overrides):
    values = {
        'runtime_ref': 'tmux:%1',
        'agent_name': 'agent1',
        'workspace_path': '/tmp/workspace',
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _registry(provider: str = 'codex'):
    return SimpleNamespace(spec_for=lambda agent_name: SimpleNamespace(provider=provider))


def _binding():
    return SimpleNamespace(load_session=lambda workspace_path, agent_name: None)


def test_assess_provider_pane_reports_missing_session(monkeypatch) -> None:
    binding = _binding()
    monkeypatch.setattr(
        'ccbd.services.health_assessment.provider_pane.load_provider_session',
        lambda binding, workspace_path, agent_name: None,
    )

    assessment = assess_provider_pane(
        runtime=_runtime(),
        registry=_registry(),
        session_bindings={'codex': binding},
        namespace_state_store=object(),
    )

    assert assessment is not None
    assert assessment.binding is binding
    assert assessment.session is None
    assert assessment.pane_state == 'missing'
    assert assessment.health == 'session-missing'


def test_assess_provider_pane_marks_foreign_tmux_pane(monkeypatch) -> None:
    binding = _binding()
    session = SimpleNamespace(pane_id='%9')
    monkeypatch.setattr(
        'ccbd.services.health_assessment.provider_pane.load_provider_session',
        lambda binding, workspace_path, agent_name: session,
    )
    monkeypatch.setattr(
        'ccbd.services.health_assessment.provider_pane.session_terminal',
        lambda session: 'tmux',
    )
    monkeypatch.setattr(
        'ccbd.services.health_assessment.provider_pane.session_backend',
        lambda session: 'backend',
    )
    monkeypatch.setattr(
        'ccbd.services.health_assessment.provider_pane.tmux_pane_state',
        lambda session, backend, pane_id: 'alive',
    )
    monkeypatch.setattr(
        'ccbd.services.health_assessment.provider_pane.pane_outside_project_namespace',
        lambda **kwargs: True,
    )

    assessment = assess_provider_pane(
        runtime=_runtime(),
        registry=_registry(),
        session_bindings={'codex': binding},
        namespace_state_store=object(),
    )

    assert assessment is not None
    assert assessment.session is session
    assert assessment.terminal == 'tmux'
    assert assessment.pane_state == 'foreign'
    assert assessment.health == 'pane-foreign'


# --- content-aware health: usage-limit / signal mapping ------------------


def test_health_from_pane_signal_maps_known_signals_and_returns_none_otherwise() -> None:
    assert health_from_pane_signal('usage_limit') == 'usage-limited'
    assert health_from_pane_signal('api_error') == 'api-error'
    assert health_from_pane_signal('auth_failed') == 'auth-failed'
    assert health_from_pane_signal('config_error') == 'config-error'
    assert health_from_pane_signal('failed') == 'provider-error'
    # Unknown / benign signals do not change health.
    assert health_from_pane_signal('unknown') is None
    assert health_from_pane_signal('working') is None
    assert health_from_pane_signal(None) is None
    assert health_from_pane_signal('') is None


def test_assess_provider_pane_surfaces_usage_limit_signal_when_pane_alive(monkeypatch) -> None:
    binding = _binding()
    session = SimpleNamespace(pane_id='%9')
    banner = (
        "You've hit your usage limit. Visit https://chatgpt.com to purchase "
        "more credits or try again at Jul 2nd, 2026 10:21 AM."
    )
    monkeypatch.setattr(
        'ccbd.services.health_assessment.provider_pane.load_provider_session',
        lambda binding, workspace_path, agent_name: session,
    )
    monkeypatch.setattr(
        'ccbd.services.health_assessment.provider_pane.session_terminal',
        lambda session: 'tmux',
    )
    monkeypatch.setattr(
        'ccbd.services.health_assessment.provider_pane.session_backend',
        lambda session: 'backend',
    )
    monkeypatch.setattr(
        'ccbd.services.health_assessment.provider_pane.tmux_pane_state',
        lambda session, backend, pane_id: 'alive',
    )
    monkeypatch.setattr(
        'ccbd.services.health_assessment.provider_pane.pane_outside_project_namespace',
        lambda **kwargs: False,
    )
    monkeypatch.setattr(
        'ccbd.services.health_assessment.provider_pane._capture_pane_content',
        lambda sess, pane_id: banner,
    )

    assessment = assess_provider_pane(
        runtime=_runtime(),
        registry=_registry(),
        session_bindings={'codex': binding},
        namespace_state_store=object(),
    )

    assert assessment is not None
    assert assessment.pane_state == 'alive'
    assert assessment.pane_signal_state == 'usage_limit'
    assert assessment.pane_signal_reason == 'provider_usage_limit'
    assert assessment.retry_after == '2026-07-02T10:21:00'
    assert assessment.health == 'usage-limited'
    assert assessment.pane_tail is not None
    assert 'usage limit' in assessment.pane_tail.lower()


def test_assess_provider_pane_leaves_non_codex_provider_content_none(monkeypatch) -> None:
    binding = _binding()
    session = SimpleNamespace(pane_id='%9')
    monkeypatch.setattr(
        'ccbd.services.health_assessment.provider_pane.load_provider_session',
        lambda binding, workspace_path, agent_name: session,
    )
    monkeypatch.setattr(
        'ccbd.services.health_assessment.provider_pane.session_terminal',
        lambda session: 'tmux',
    )
    monkeypatch.setattr(
        'ccbd.services.health_assessment.provider_pane.session_backend',
        lambda session: 'backend',
    )
    monkeypatch.setattr(
        'ccbd.services.health_assessment.provider_pane.tmux_pane_state',
        lambda session, backend, pane_id: 'alive',
    )
    monkeypatch.setattr(
        'ccbd.services.health_assessment.provider_pane.pane_outside_project_namespace',
        lambda **kwargs: False,
    )
    # A non-codex provider: no parser exists yet, so content fields stay None
    # and health stays healthy (no crash).
    monkeypatch.setattr(
        'ccbd.services.health_assessment.provider_pane._capture_pane_content',
        lambda sess, pane_id: 'some opaque pane content',
    )

    assessment = assess_provider_pane(
        runtime=_runtime(),
        registry=_registry(provider='claude'),
        session_bindings={'claude': binding},
        namespace_state_store=object(),
    )

    assert assessment is not None
    assert assessment.pane_state == 'alive'
    assert assessment.pane_signal_state is None
    assert assessment.pane_signal_reason is None
    assert assessment.retry_after is None
    assert assessment.health == 'healthy'

