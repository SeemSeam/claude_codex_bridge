from __future__ import annotations

import re
from dataclasses import dataclass


_ANSI_RE = re.compile(r'\x1b\[[0-9;?]*[ -/]*[@-~]')


@dataclass
class AgyPaneReader:
    """Thin wrapper around a terminal backend that snapshots an agy tmux pane.

    The underlying TmuxBackend.get_pane_content already strips ANSI, but other
    backend types may not, so we strip defensively and tolerate failures by
    returning an empty string instead of raising.
    """

    backend: object
    pane_id: str
    lines: int = 200

    def snapshot(self) -> str:
        getter = getattr(self.backend, 'get_pane_content', None)
        if not callable(getter):
            getter = getattr(self.backend, 'get_text', None)
        if not callable(getter):
            return ''
        try:
            content = getter(self.pane_id, lines=self.lines)
        except Exception:
            return ''
        if not content:
            return ''
        return _ANSI_RE.sub('', content)


__all__ = ['AgyPaneReader']
