"""Codex TUI ready detection probe (Q3 Stage 1a).

Implements provider-specific InitGateProbe for Codex CLI cold-start
detection — checks for banner dissipation and input prompt readiness.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from provider_core.init_gate import InitGateProbe


# Banner/welcome screen strings that indicate TUI is NOT yet ready.
# Captured from Codex v0.124.x cold-start welcome screen.
# If Codex CLI upgrades change these, patch this constant.
CODEX_INIT_BANNERS: tuple[str, ...] = (
    "OpenAI Codex",
    "Sign in with ChatGPT",
    "Trust this workspace",
    "Choose a model",
    "✦ Welcome to",
)


class CodexInitProbe:
    """Probe Codex TUI for 'input box ready' state.

    Implements InitGateProbe protocol for use with InitGate.

    Ready criteria (AND — all must be true):
        S1: Welcome banner strings NOT present in visible screen
        S2: Last non-empty line starts with "› " (idle input prompt)

    Uses visible-screen-only capture (no scrollback) to avoid
    false negatives from historical banner in scrollback.
    """

    def __init__(
        self,
        *,
        pane_id: str,
        tmux_run_fn: Callable[[list[str]], str],
    ) -> None:
        """Initialize probe.

        Args:
            pane_id: Tmux pane identifier (e.g., "%3")
            tmux_run_fn: Callable that runs tmux command and returns stdout.
                         Expected signature: (args: list[str]) -> str
        """
        self._pane_id = pane_id
        self._tmux_run = tmux_run_fn

    def detect(self) -> bool:
        """Return True if Codex TUI is ready for input.

        Conservative: any error or ambiguity returns False.
        """
        try:
            capture = self._capture_visible()
        except Exception:
            # tmux failure or capture error — conservative fail
            return False

        return self._banner_gone(capture) and self._prompt_on_last_line(capture)

    def _capture_visible(self) -> str:
        """Capture visible screen (no scrollback) from pane.

        Uses `capture-pane -p -t <pane>` without `-S` parameter,
        so only currently visible lines are returned.
        """
        # Note: no -S flag — we want visible screen only, not scrollback
        args = ["capture-pane", "-p", "-t", self._pane_id]
        return self._tmux_run(args)

    def _banner_gone(self, capture: str) -> bool:
        """S1: Check that welcome banner strings are NOT present."""
        capture_lower = capture.lower()
        for banner in CODEX_INIT_BANNERS:
            if banner.lower() in capture_lower:
                return False
        return True

    def _prompt_on_last_line(self, capture: str) -> bool:
        """S2: Check that last non-empty line is idle input prompt.

        Codex idle prompt: line starting with "› " (caret + space).
        Tolerates placeholder hint after the caret (e.g.,
        "› Improve documentation in @filename") — this is normal idle.
        """
        # Filter to non-empty lines only
        lines = [ln for ln in capture.splitlines() if ln.strip()]
        if not lines:
            return False

        last = lines[-1]
        # Strip leading whitespace (pane may have indent), check prefix
        return last.lstrip().startswith("› ")

    def capture_visible_for_diagnostics(self) -> str:
        """Expose visible capture for InitGate diagnostics.

        Returns empty string on any error (conservative).
        """
        try:
            return self._capture_visible()
        except Exception:
            return ""
