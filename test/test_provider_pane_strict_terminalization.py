"""Regression tests for the false-positive usage-limit terminalization fix.

Background: broad pane-text markers (e.g. "usage limit", "quota", "rate limit",
"api error") were wired into AUTO-TERMINALIZATION (B.1 poll path + B.2 health
bridge). A healthy agent whose output merely *discusses* those topics got
matched and had its job killed as provider_usage_limit.

Fix: terminalization uses ONLY the HIGH-CONFIDENCE / strict marker tier
(specific multi-word provider banners). Broad markers stay for diagnostics.

These tests pin both halves of the contract:
  * TEST A (no false positive): research-style pane text -> NO terminal.
  * TEST B (real quota banner still classified): real codex banner -> terminal.
"""
from __future__ import annotations

from provider_pane_status.codex_pane import (
    HIGH_CONFIDENCE_USAGE_LIMIT_MARKERS,
    USAGE_LIMIT_MARKERS,
    high_confidence_signal,
    parse_codex_pane_status,
)


# Text a healthy agent emits while RESEARCHING the usage-limit classification.
# Contains the broad words ("usage limit", "quota", "rate limit", "api error")
# but NO high-confidence banner.
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


# --- codex_pane strict tier ------------------------------------------------


def test_high_confidence_usage_markers_exclude_generic_words() -> None:
    # The dangerous broad words must NOT be in the high-confidence tier.
    assert "usage limit" not in HIGH_CONFIDENCE_USAGE_LIMIT_MARKERS
    assert "quota" not in HIGH_CONFIDENCE_USAGE_LIMIT_MARKERS
    assert "try again at" not in HIGH_CONFIDENCE_USAGE_LIMIT_MARKERS
    assert "plan limit" not in HIGH_CONFIDENCE_USAGE_LIMIT_MARKERS
    # Specific banners are kept.
    assert "hit your usage limit" in HIGH_CONFIDENCE_USAGE_LIMIT_MARKERS
    assert "purchase more credits" in HIGH_CONFIDENCE_USAGE_LIMIT_MARKERS
    assert "out of credits" in HIGH_CONFIDENCE_USAGE_LIMIT_MARKERS
    # Broad set still contains them (diagnostics path).
    assert "usage limit" in USAGE_LIMIT_MARKERS
    assert "quota" in USAGE_LIMIT_MARKERS


def test_strict_mode_does_not_classify_research_text_as_usage_limit() -> None:
    # TEST A (codex_pane strict): research output must NOT be usage_limit.
    parsed = parse_codex_pane_status(RESEARCH_TEXT, strict=True)
    assert parsed.state != "usage_limit"
    assert parsed.reason != "provider_usage_limit"
    assert parsed.terminal_outcome != "failed"


def test_high_confidence_signal_returns_none_for_research_text() -> None:
    # TEST A (terminalization gate): None == do NOT terminalize.
    assert high_confidence_signal(RESEARCH_TEXT) is None


def test_strict_mode_classifies_real_codex_banner_as_usage_limit() -> None:
    # TEST B: the real banner still classifies.
    parsed = parse_codex_pane_status(REAL_CODEX_BANNER, strict=True)
    assert parsed.state == "usage_limit"
    assert parsed.reason == "provider_usage_limit"
    assert parsed.terminal_outcome == "failed"
    assert parsed.retry_after == "2026-07-02T10:21:00"


def test_high_confidence_signal_returns_terminal_for_real_banner() -> None:
    signal = high_confidence_signal(REAL_CODEX_BANNER)
    assert signal is not None
    assert signal.state == "usage_limit"
    assert signal.reason == "provider_usage_limit"


def test_broad_mode_still_classifies_research_text_for_diagnostics() -> None:
    # The diagnostics path (broad) still attributes the research text; this is
    # fine because diagnostics only run after a delivery has already failed.
    parsed = parse_codex_pane_status(RESEARCH_TEXT, strict=False)
    assert parsed.state == "usage_limit"
