from __future__ import annotations

from terminal_runtime.backend_selection import make_terminal_backend


class TerminalCodexSession:
    """Inject commands to Codex CLI via tmux-backed session."""

    def __init__(self, pane_id: str):
        self.pane_id = pane_id
        self.backend = make_terminal_backend()

    def send(self, text: str) -> None:
        command = text.replace('\r', ' ').replace('\n', ' ').strip()
        if command:
            strict_send = getattr(self.backend, 'send_text_to_pane', None)
            if callable(strict_send):
                strict_send(self.pane_id, command)
            else:
                self.backend.send_text(self.pane_id, command)


__all__ = ['TerminalCodexSession']
