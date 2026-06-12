from __future__ import annotations

import os
import signal
import time
from pathlib import Path
from typing import Any

from provider_core.fifo_delivery import cleanup_acks

from .env import env_float
from .runtime_io import process_request, read_request
from .runtime_state import build_bridge_runtime_state


class DualBridge:
    """Claude ↔ Codex bridge main process"""

    def __init__(self, runtime_dir: Path):
        pane_id = os.environ.get('CODEX_TMUX_SESSION')
        if not pane_id:
            raise RuntimeError('Missing CODEX_TMUX_SESSION environment variable')

        self._runtime = build_bridge_runtime_state(runtime_dir, pane_id=pane_id)
        self._running = True
        signal.signal(signal.SIGTERM, self._handle_signal)
        signal.signal(signal.SIGINT, self._handle_signal)

    @property
    def runtime_dir(self) -> Path:
        return self._runtime.paths.runtime_dir

    @property
    def input_fifo(self) -> Path:
        return self._runtime.paths.input_fifo

    @property
    def history_dir(self) -> Path:
        return self._runtime.paths.history_dir

    @property
    def history_file(self) -> Path:
        return self._runtime.paths.history_file

    @property
    def bridge_log(self) -> Path:
        return self._runtime.paths.bridge_log

    @property
    def binding_tracker(self):
        return self._runtime.binding_tracker

    @property
    def codex_session(self):
        return self._runtime.codex_session

    def _handle_signal(self, signum: int, _: Any) -> None:
        self._running = False
        self.binding_tracker.stop()
        self._log_console(f'Received signal {signum}, exiting...')

    def run(self) -> int:
        self._log_console('Codex bridge started, waiting for Claude commands...')
        cleanup_acks(self._runtime.paths.runtime_dir / 'acks')
        self.binding_tracker.start()
        idle_sleep = env_float('CCB_BRIDGE_IDLE_SLEEP', 0.05)
        error_backoff_min = env_float('CCB_BRIDGE_ERROR_BACKOFF_MIN', 0.05)
        error_backoff_max = env_float('CCB_BRIDGE_ERROR_BACKOFF_MAX', 0.2)
        error_backoff = max(0.0, min(error_backoff_min, error_backoff_max))
        poll_timeout = idle_sleep if idle_sleep else 0.05
        try:
            while self._running:
                try:
                    payload = self._read_request(timeout=poll_timeout)
                    if payload is None:
                        continue
                    self._process_request(payload)
                    error_backoff = max(0.0, min(error_backoff_min, error_backoff_max))
                except KeyboardInterrupt:
                    self._running = False
                except Exception as exc:
                    self._log_console(f'Failed to process message: {exc}')
                    self._log_bridge(f'error: {exc}')
                    if error_backoff:
                        time.sleep(error_backoff)
                    if error_backoff_max:
                        error_backoff = min(error_backoff_max, max(error_backoff_min, error_backoff * 2))
        finally:
            self.binding_tracker.stop()
            if self._runtime.fifo_reader is not None:
                self._runtime.fifo_reader.close()

        self._log_console('Codex bridge exited')
        return 0

    def _read_request(self, *, timeout: float = 0.0):
        return read_request(self._runtime, timeout=timeout)

    def _process_request(self, payload) -> None:
        process_request(self._runtime, payload, log_console_fn=self._log_console)

    def _log_bridge(self, message: str) -> None:
        from .runtime_io import log_bridge

        log_bridge(self._runtime, message)

    @staticmethod
    def _log_console(message: str) -> None:
        print(message, flush=True)


__all__ = ['DualBridge']
