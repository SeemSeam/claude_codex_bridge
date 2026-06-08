"""Claude ↔ Codex bridge main process (Q3 Stage 1a — Init Gate integrated)."""
from __future__ import annotations

import os
import signal
import time
from pathlib import Path
from typing import Any

from .env import env_float
from .runtime_io import process_request, read_request
from .runtime_state import build_bridge_runtime_state


class DualBridge:
    """Claude ↔ Codex bridge main process."""

    def __init__(self, runtime_dir: Path):
        pane_id = os.environ.get('CODEX_TMUX_SESSION')
        if not pane_id:
            raise RuntimeError('Missing CODEX_TMUX_SESSION environment variable')

        # Import here to avoid circular imports
        from terminal_runtime.tmux_backend import TmuxBackend

        # Create tmux backend for init probe capture (no-arg constructor;
        # pane id is bound via tmux_run_fn closure inside build_bridge_runtime_state)
        tmux_backend = TmuxBackend()

        self._runtime = build_bridge_runtime_state(
            runtime_dir,
            pane_id=pane_id,
            tmux_backend=tmux_backend,
        )
        self._running = True

        # Q3-S1a.3: Open FIFO holder before Init Gate starts
        # This allows upstream writer to proceed without blocking on open(O_WRONLY)
        try:
            self._runtime.fifo_holder_fd = os.open(
                str(self._runtime.paths.input_fifo),
                os.O_RDONLY | os.O_NONBLOCK,
            )
        except FileNotFoundError:
            self._log_console("input.fifo not found at init; proceeding without holder")
            self._runtime.fifo_holder_fd = None

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
        """Handle termination signals."""
        self._running = False
        self._log_console(f'Received signal {signum}, exiting...')
        self._teardown()

    def _teardown(self) -> None:
        """Cleanup holder fd + stop binding tracker (idempotent)."""
        # Close FIFO holder if open
        fd = self._runtime.fifo_holder_fd
        if fd is not None:
            try:
                os.close(fd)
            except OSError as exc:
                self._log_console(f"fifo holder close failed: {exc}")
            self._runtime.fifo_holder_fd = None

        # Stop binding tracker (may not have been started)
        try:
            self.binding_tracker.stop()
        except Exception as exc:
            self._log_console(f"binding tracker stop failed: {exc}")

    def run(self) -> int:
        """Run the bridge main loop.

        Returns:
            0 on normal exit, 3 on INIT_FAIL.
        """
        self._log_console('Codex bridge started, waiting for Claude commands...')

        # Q3-S1a.3: Init Gate — wait for TUI to be ready before entering main loop
        if not self._runtime.init_gate.wait_until_ready():
            self._log_console(
                f"[InitGate] INIT_FAIL: {self._runtime.init_gate.last_reason}"
            )
            self._teardown()
            return 3

        # Init Gate passed — enter normal main loop
        self.binding_tracker.start()

        idle_sleep = env_float('CCB_BRIDGE_IDLE_SLEEP', 0.05)
        error_backoff_min = env_float('CCB_BRIDGE_ERROR_BACKOFF_MIN', 0.05)
        error_backoff_max = env_float('CCB_BRIDGE_ERROR_BACKOFF_MAX', 0.2)
        error_backoff = max(0.0, min(error_backoff_min, error_backoff_max))

        try:
            while self._running:
                try:
                    payload = self._read_request()
                    if payload is None:
                        if idle_sleep:
                            time.sleep(idle_sleep)
                        continue
                    self._process_request(payload)
                    error_backoff = max(
                        0.0, min(error_backoff_min, error_backoff_max)
                    )
                except KeyboardInterrupt:
                    self._running = False
                except Exception as exc:
                    self._log_console(f'Failed to process message: {exc}')
                    self._log_bridge(f'error: {exc}')
                    if error_backoff:
                        time.sleep(error_backoff)
                    if error_backoff_max:
                        error_backoff = min(
                            error_backoff_max,
                            max(error_backoff_min, error_backoff * 2)
                        )
        finally:
            self._teardown()

        self._log_console('Codex bridge exited')
        return 0

    def _read_request(self):
        return read_request(self._runtime)

    def _process_request(self, payload) -> None:
        process_request(self._runtime, payload, log_console_fn=self._log_console)

    def _log_bridge(self, message: str) -> None:
        from .runtime_io import log_bridge

        log_bridge(self._runtime, message)

    @staticmethod
    def _log_console(message: str) -> None:
        print(message, flush=True)


__all__ = ['DualBridge']
