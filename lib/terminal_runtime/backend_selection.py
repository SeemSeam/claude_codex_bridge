from __future__ import annotations

import os
import time
from dataclasses import dataclass
from typing import Callable

from terminal_runtime.layouts import LayoutResult, create_tmux_auto_layout
from terminal_runtime.tmux_backend import TmuxBackend


def make_terminal_backend(
    terminal_type=None,
    *,
    socket_name=None,
    socket_path=None,
):
    if terminal_type in (None, 'tmux'):
        return TmuxBackend(socket_name=socket_name, socket_path=socket_path)
    if terminal_type == 'herdr':
        raise NotImplementedError('herdr terminal backend lands in PR #2')
    raise ValueError(f'unsupported terminal backend: {terminal_type}')


@dataclass
class TerminalBackendSelection:
    detect_terminal_fn: Callable[[], object | None]
    tmux_backend_factory: Callable[[], object]
    cached_backend: object | None = None

    def get_backend(self, terminal_type: str | None = None) -> object | None:
        if self.cached_backend is not None:
            return self.cached_backend
        selected = terminal_type or self.detect_terminal_fn()
        if selected == 'tmux':
            self.cached_backend = self.tmux_backend_factory()
        elif selected == 'herdr':
            raise NotImplementedError('herdr terminal backend lands in PR #2')
        return self.cached_backend

    def get_backend_for_session(self, session_data: dict) -> object:
        socket_name = str(session_data.get('tmux_socket_name') or '').strip() or None
        socket_path = str(session_data.get('tmux_socket_path') or '').strip() or None
        try:
            return self.tmux_backend_factory(socket_name=socket_name, socket_path=socket_path)
        except TypeError:
            return self.tmux_backend_factory()

    @staticmethod
    def get_pane_id_from_session(session_data: dict) -> str | None:
        return session_data.get('pane_id') or session_data.get('tmux_session')


@dataclass
class TerminalLayoutService:
    tmux_backend_factory: Callable[[], object]
    detached_session_name_fn: Callable[..., str]
    os_getpid_fn: Callable[[], int] = os.getpid
    time_fn: Callable[[], float] = time.time
    env: dict[str, str] | None = None

    def create_auto_layout(
        self,
        providers: list[str],
        *,
        cwd: str,
        root_pane_id: str | None = None,
        tmux_session_name: str | None = None,
        percent: int = 50,
        set_markers: bool = True,
        marker_prefix: str = 'CCB',
        terminal_type: str | None = None,
    ) -> LayoutResult:
        selected = terminal_type or 'tmux'
        if selected == 'herdr':
            raise NotImplementedError('herdr terminal backend lands in PR #2')
        if selected != 'tmux':
            raise ValueError(f'unsupported terminal backend: {selected}')
        env = self.env if self.env is not None else os.environ
        return create_tmux_auto_layout(
            providers,
            cwd=cwd,
            backend=self.tmux_backend_factory(),
            root_pane_id=root_pane_id,
            tmux_session_name=tmux_session_name,
            percent=percent,
            set_markers=set_markers,
            marker_prefix=marker_prefix,
            detached_session_name=self.detached_session_name_fn(
                cwd=cwd,
                pid=self.os_getpid_fn(),
                now_ts=self.time_fn(),
            ),
            inside_tmux=bool((env.get('TMUX') or '').strip()),
        )
