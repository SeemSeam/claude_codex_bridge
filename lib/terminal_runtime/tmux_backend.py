from __future__ import annotations

import os
from pathlib import Path
import re
import subprocess

from terminal_runtime.backend_types import TerminalBackend
from terminal_runtime.env import isolated_tmux_env as _isolated_tmux_env_impl
from terminal_runtime.env import subprocess_kwargs as _subprocess_kwargs_impl
from terminal_runtime.tmux import tmux_base as _tmux_base_impl
from terminal_runtime.tmux_backend_control import TmuxBackendControlMixin
from terminal_runtime.tmux_backend_logs import TmuxBackendLogsMixin
from terminal_runtime.tmux_backend_panes import (
    TmuxBackendPaneMutationMixin,
    TmuxBackendPaneQueryMixin,
)
from terminal_runtime.tmux_backend_runtime import (
    TmuxBackendServices,
    build_backend_services as _build_backend_services_impl,
)
from runtime_observability import record_startup_operations


def _subprocess_kwargs() -> dict:
    return _subprocess_kwargs_impl()


def _isolated_tmux_env() -> dict[str, str]:
    return _isolated_tmux_env_impl()


def _run(*args, **kwargs):
    kwargs.update(_subprocess_kwargs())
    import subprocess as _sp

    return _sp.run(*args, **kwargs)


class TmuxBackend(
    TmuxBackendLogsMixin,
    TmuxBackendPaneQueryMixin,
    TmuxBackendPaneMutationMixin,
    TmuxBackendControlMixin,
    TerminalBackend,
):
    _ANSI_RE = re.compile(r'\x1b\[[0-?]*[ -/]*[@-~]')

    def __init__(self, *, socket_name: str | None = None, socket_path: str | None = None):
        self._socket_path = (
            socket_path or os.environ.get('CCB_TMUX_SOCKET_PATH') or ''
        ).strip() or None
        if self._socket_path:
            self._socket_path = str(Path(self._socket_path).expanduser())
        self._socket_name = (
            socket_name or os.environ.get('CCB_TMUX_SOCKET') or ''
        ).strip() or None
        self._pane_log_info: dict[str, float] = {}
        self._services: TmuxBackendServices = _build_backend_services_impl(self)

    def _tmux_base(self) -> list[str]:
        return _tmux_base_impl(self._socket_name, socket_path=self._socket_path)

    def capture_composer(self, pane_id: str) -> dict:
        """Visible screen with attributes and exact pane identity, not scrollback."""
        if not re.fullmatch(r'%\d+', pane_id):
            raise ValueError('composer capture requires an exact pane id')
        fmt = '#{pane_id}|#{pane_pid}|#{pane_dead}|#{pane_in_mode}|#{cursor_x}|#{cursor_y}|#{pane_width}|#{pane_height}'
        def metadata():
            value = self._tmux_run(['display-message', '-p', '-t', pane_id, fmt],
                                   check=True, capture=True, timeout=1).stdout.strip().split('|')
            if len(value) != 8 or value[0] != pane_id:
                raise ValueError('pane binding changed')
            return value
        before = metadata()
        text = self._tmux_run(['capture-pane', '-p', '-e', '-t', pane_id],
                              check=True, capture=True, timeout=1).stdout
        after = metadata()
        return {'text': text, 'cursor_x': int(after[4]), 'cursor_y': int(after[5]),
                'binding': f'{self._socket_path or self._socket_name}:{after[0]}:{after[1]}',
                'blocked': before != after or after[2] != '0' or after[3] != '0'}

    def _tmux_run(
        self,
        args: list[str],
        *,
        check: bool = False,
        capture: bool = False,
        input_bytes: bytes | None = None,
        timeout: float | None = None,
    ) -> subprocess.CompletedProcess:
        kwargs: dict = {}
        if capture:
            kwargs.update(
                {
                    'capture_output': True,
                    'text': True,
                    'encoding': 'utf-8',
                    'errors': 'replace',
                }
            )
        if input_bytes is not None:
            kwargs['input'] = input_bytes
        if timeout is not None:
            kwargs['timeout'] = timeout
        kwargs['env'] = _isolated_tmux_env()
        record_startup_operations(
            {
                'tmux_backend_command_attempt_count': 1,
                'tracked_startup_subprocess_spawn_attempt_count': 1,
            }
        )
        try:
            result = _run([*self._tmux_base(), *args], check=check, **kwargs)
        except subprocess.SubprocessError:
            # CalledProcessError and TimeoutExpired both prove that the child
            # process was created, even though the tmux command did not finish
            # successfully.
            _record_tmux_subprocess_started()
            raise
        _record_tmux_subprocess_started()
        return result

    @staticmethod
    def _env_tmux_pane() -> str:
        return os.environ.get('TMUX_PANE', '')


def _record_tmux_subprocess_started() -> None:
    record_startup_operations(
        {
            'tmux_backend_command_count': 1,
            'tmux_backend_subprocess_spawn_count': 1,
            'tracked_startup_subprocess_spawn_count': 1,
        }
    )


__all__ = ['TmuxBackend']
