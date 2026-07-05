from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderPaneAssessment:
    binding: object | None
    session: object | None
    terminal: str | None
    pane_state: str | None
    health: str
    pane_signal_state: str | None = None
    pane_signal_reason: str | None = None
    retry_after: str | None = None
    pane_tail: str | None = None
