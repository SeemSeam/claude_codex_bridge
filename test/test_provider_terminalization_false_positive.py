"""Regression tests: AUTO-TERMINALIZATION (B.1 poll + B.2 health) must require
HIGH-CONFIDENCE provider banners and must NOT fire on broad keyword matches
that appear in normal agent output (e.g. an agent researching usage limits).
"""
from __future__ import annotations

from types import SimpleNamespace

from provider_backends.claude.execution_runtime.polling import _read_claude_pane_signal
from provider_backends.codex.execution import _read_codex_pane_signal
from provider_backends.kimi.execution import _read_kimi_pane_signal
from ccbd.services.health_assessment.provider_pane import assess_provider_pane
from ccbd.services.health_monitor_runtime.provider import (
    _maybe_terminalize_for_provider_health,
)


# Text a healthy agent emits while RESEARCHING the usage-limit classification.
RESEARCH_TEXT = (
    "› Classify the usage-limited codex agent.\n"
    "\n"
    "The quota classification maps provider_usage_limit to a terminal health\n"
    "state. We also handle rate limit and generic api error banners separately.\n"
    "\n"
    "› next step\n"
)

REAL_CODEX_BANNER = (
    "■ You've hit your usage limit. Visit "
    "https://chatgpt.com/codex/settings/usage to purchase more credits or "
    "try again at Jul 2nd, 2026 10:21 AM"
)


class _PaneBackend:
    """Fake terminal backend returning canned pane content."""

    def __init__(self, content: str) -> None:
        self._content = content

    def get_pane_content(self, pane_id: str, lines: int = 120) -> str:
        return self._content


# --- B.1 codex poll path ---------------------------------------------------


def test_codex_poll_signal_strict_returns_none_for_research_text() -> None:
    # TEST A: broad-only research text must NOT produce a terminal signal.
    backend = _PaneBackend(RESEARCH_TEXT)
    assert _read_codex_pane_signal(backend, "%1", strict=True) is None


def test_codex_poll_signal_strict_returns_terminal_for_real_banner() -> None:
    # TEST B: the real codex banner still produces provider_usage_limit.
    backend = _PaneBackend(REAL_CODEX_BANNER)
    signal = _read_codex_pane_signal(backend, "%1", strict=True)
    assert signal is not None
    assert signal["pane_signal_state"] == "usage_limit"
    assert signal["error_kind"] == "provider_usage_limit"


def test_codex_poll_signal_broad_still_attributes_research_for_diagnostics() -> None:
    # Diagnostics path (strict=False) still attributes broad matches.
    backend = _PaneBackend(RESEARCH_TEXT)
    signal = _read_codex_pane_signal(backend, "%1", strict=False)
    assert signal is not None
    assert signal["pane_signal_state"] == "usage_limit"


# --- B.1 kimi / claude detectors ------------------------------------------


def test_kimi_detector_strict_returns_none_for_research_text() -> None:
    backend = _PaneBackend(RESEARCH_TEXT)
    assert _read_kimi_pane_signal(backend, "%1", strict=True) is None


def test_kimi_detector_strict_returns_terminal_for_real_banner() -> None:
    backend = _PaneBackend(REAL_CODEX_BANNER)
    signal = _read_kimi_pane_signal(backend, "%1", strict=True)
    assert signal is not None
    assert signal["pane_signal_state"] == "usage_limit"
    assert signal["pane_signal_reason"] == "provider_usage_limit"


def test_claude_detector_strict_returns_none_for_research_text() -> None:
    backend = _PaneBackend(RESEARCH_TEXT)
    assert _read_claude_pane_signal(backend, "%1", strict=True) is None


def test_claude_detector_strict_returns_terminal_for_real_banner() -> None:
    backend = _PaneBackend(REAL_CODEX_BANNER)
    signal = _read_claude_pane_signal(backend, "%1", strict=True)
    assert signal is not None
    assert signal["pane_signal_state"] == "usage_limit"
    assert signal["pane_signal_reason"] == "provider_usage_limit"


# --- B.2 health bridge -----------------------------------------------------


def _wire_codex_assessment(monkeypatch, content: str):
    """Wire assess_provider_pane for a tmux codex pane returning `content`."""
    session = SimpleNamespace(pane_id="%9")
    binding = SimpleNamespace(load_session=lambda workspace_path, agent_name: session)
    monkeypatch.setattr(
        "ccbd.services.health_assessment.provider_pane.load_provider_session",
        lambda binding, workspace_path, agent_name: session,
    )
    monkeypatch.setattr(
        "ccbd.services.health_assessment.provider_pane.session_terminal",
        lambda session: "tmux",
    )
    monkeypatch.setattr(
        "ccbd.services.health_assessment.provider_pane.session_backend",
        lambda session: "backend",
    )
    monkeypatch.setattr(
        "ccbd.services.health_assessment.provider_pane.tmux_pane_state",
        lambda session, backend, pane_id: "alive",
    )
    monkeypatch.setattr(
        "ccbd.services.health_assessment.provider_pane.pane_outside_project_namespace",
        lambda **kwargs: False,
    )
    monkeypatch.setattr(
        "ccbd.services.health_assessment.provider_pane._capture_pane_content",
        lambda sess, pane_id: content,
    )
    return binding


def _runtime():
    return SimpleNamespace(
        runtime_ref="tmux:%1",
        agent_name="agent1",
        workspace_path="/tmp/workspace",
    )


def _registry(provider: str = "codex"):
    return SimpleNamespace(spec_for=lambda agent_name: SimpleNamespace(provider=provider))


def test_health_assessment_does_not_flip_on_research_text(monkeypatch) -> None:
    # TEST A (B.2): health stays healthy on broad-only research text.
    binding = _wire_codex_assessment(monkeypatch, RESEARCH_TEXT)
    assessment = assess_provider_pane(
        runtime=_runtime(),
        registry=_registry(),
        session_bindings={"codex": binding},
        namespace_state_store=object(),
    )
    assert assessment is not None
    assert assessment.pane_state == "alive"
    # No terminal provider-failure signal (usage_limit / auth_failed / api_error
    # / config_error) may be surfaced from broad-only research text.
    assert assessment.pane_signal_state not in {
        "usage_limit",
        "auth_failed",
        "api_error",
        "config_error",
        "failed",
    }
    assert assessment.health == "healthy"


def test_health_assessment_flips_on_real_banner(monkeypatch) -> None:
    # TEST B (B.2): real banner flips health to usage-limited.
    binding = _wire_codex_assessment(monkeypatch, REAL_CODEX_BANNER)
    assessment = assess_provider_pane(
        runtime=_runtime(),
        registry=_registry(),
        session_bindings={"codex": binding},
        namespace_state_store=object(),
    )
    assert assessment is not None
    assert assessment.pane_signal_state == "usage_limit"
    assert assessment.health == "usage-limited"


def test_health_bridge_does_not_terminalize_without_pane_signal() -> None:
    # Defensive: even if health somehow reads usage-limited, a missing
    # high-confidence pane_signal_state must block terminalization.
    completed = []
    dispatcher = SimpleNamespace(
        _state=SimpleNamespace(active_job=lambda agent: None),
        get=lambda job_id: None,
        _terminal_event_by_status=set(),
        complete=lambda job_id, decision: completed.append((job_id, decision)),
    )
    monitor = SimpleNamespace(_dispatcher=dispatcher, _clock=lambda: "2026-07-02T00:00:00Z")
    assessment = SimpleNamespace(
        pane_signal_state=None,
        pane_signal_reason=None,
        retry_after=None,
        pane_tail=None,
    )
    _maybe_terminalize_for_provider_health(
        monitor,
        agent_name="archi",
        prior_health="healthy",
        current_health="usage-limited",
        assessment=assessment,
    )
    assert completed == []


def test_health_bridge_terminalizes_with_high_confidence_pane_signal() -> None:
    completed = []
    dispatcher = SimpleNamespace(
        _state=SimpleNamespace(active_job=lambda agent: "job-1"),
        get=lambda job_id: SimpleNamespace(status="running"),
        _terminal_event_by_status={"completed", "failed"},
        complete=lambda job_id, decision: completed.append((job_id, decision)),
    )
    monitor = SimpleNamespace(_dispatcher=dispatcher, _clock=lambda: "2026-07-02T00:00:00Z")
    assessment = SimpleNamespace(
        pane_signal_state="usage_limit",
        pane_signal_reason="provider_usage_limit",
        retry_after="2026-07-02T10:21:00",
        pane_tail="hit your usage limit",
    )
    _maybe_terminalize_for_provider_health(
        monitor,
        agent_name="archi",
        prior_health="healthy",
        current_health="usage-limited",
        assessment=assessment,
    )
    assert len(completed) == 1
    job_id, decision = completed[0]
    assert job_id == "job-1"
    assert decision.terminal is True
    assert decision.diagnostics["no_reply_reason"] == "provider_usage_limit"
