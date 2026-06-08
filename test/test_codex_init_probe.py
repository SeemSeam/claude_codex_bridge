"""Unit tests for CodexInitProbe (Q3 Stage 1a).

Tests provider-specific TUI ready detection for Codex CLI.
"""
from __future__ import annotations

import pytest
from typing import Callable

from provider_backends.codex.bridge_runtime.init_probe import (
    CODEX_INIT_BANNERS,
    CodexInitProbe,
)


class TestBannerDetection:
    """Test S1: welcome banner detection."""

    def test_banner_present_returns_false(self):
        """Capture with "OpenAI Codex" → detect() returns False."""
        def tmux_run(args: list[str]) -> str:
            return "OpenAI Codex v0.124.x\nSign in with ChatGPT\n"

        probe = CodexInitProbe(pane_id="%3", tmux_run_fn=tmux_run)
        assert probe.detect() is False

    def test_banner_gone_prompt_ready_returns_true(self):
        """Capture with only idle prompt on last line → detect() returns True."""
        def tmux_run(args: list[str]) -> str:
            return "\n› Improve documentation in @filename\n"

        probe = CodexInitProbe(pane_id="%3", tmux_run_fn=tmux_run)
        assert probe.detect() is True


class TestPromptPosition:
    """Test S2: prompt position detection."""

    def test_prompt_not_on_last_line_returns_false(self):
        """› appears but followed by other non-empty lines → False."""
        def tmux_run(args: list[str]) -> str:
            return "› Some input here\nChoose a model\n[Y/n]\n"

        probe = CodexInitProbe(pane_id="%3", tmux_run_fn=tmux_run)
        assert probe.detect() is False


class TestBannerVariants:
    """Test all CODEX_INIT_BANNERS are detected."""

    def test_all_banner_variants_detected(self):
        """Each banner string causes detect() to return False."""
        for banner in CODEX_INIT_BANNERS:

            def make_tmux_run(b: str):
                def tmux_run(args: list[str]) -> str:
                    return f"Some line\n{b}\n› prompt\n"
                return tmux_run

            probe = CodexInitProbe(pane_id="%3", tmux_run_fn=make_tmux_run(banner))
            result = probe.detect()
            assert result is False, f"Banner '{banner}' should cause False, got {result}"


class TestCaptureBehavior:
    """Test capture-pane command structure."""

    def test_capture_uses_visible_only(self):
        """Mock tmux_run_fn verifies args are ['capture-pane', '-p', '-t', pane_id] (no -S)."""
        captured_args: list[str] = []

        def tmux_run(args: list[str]) -> str:
            captured_args.extend(args)
            return "› Ready\n"

        probe = CodexInitProbe(pane_id="%5", tmux_run_fn=tmux_run)
        probe.detect()

        assert captured_args == ["capture-pane", "-p", "-t", "%5"]
        assert "-S" not in captured_args  # No scrollback flag


class TestEdgeCases:
    """Test edge cases and error handling."""

    def test_empty_capture_returns_false(self):
        """Tmux returns empty string → detect() returns False (conservative)."""
        def tmux_run(args: list[str]) -> str:
            return ""

        probe = CodexInitProbe(pane_id="%3", tmux_run_fn=tmux_run)
        assert probe.detect() is False

    def test_tmux_run_exception_returns_false(self):
        """Tmux raises RuntimeError → detect() returns False, no exception propagated."""
        def tmux_run(args: list[str]) -> str:
            raise RuntimeError("tmux failed")

        probe = CodexInitProbe(pane_id="%3", tmux_run_fn=tmux_run)
        assert probe.detect() is False  # Conservative failure, no raise
