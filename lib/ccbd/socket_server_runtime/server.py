from __future__ import annotations

from pathlib import Path
import os
import queue
import threading

from ccbd.control_plane_transport import endpoint_from_legacy_socket_path
from ccbd.control_plane_transport.factory import transport_for_legacy_socket_path
from platforms.windows.control_plane.tcp import WindowsTcpControlPlaneTransport

from .bootstrap_probe import bootstrap_readiness_probe
from .lifecycle import listen_server, shutdown_server
from .loop import serve_forever as serve_forever_impl, stop_maintenance_worker, stop_worker
from .protocol import handle_connection


_CONNECTION_QUEUE_MAXSIZE = 128


def _is_windows() -> bool:
    return os.name == 'nt'


class CcbdSocketServer:
    _MUTATING_OPS = frozenset({
        'submit',
        'cancel',
        'attach',
        'start',
        'restore',
        'ack',
        'resubmit',
        'retry',
        'comms_recover',
        'project_restart_agent',
        'project_restart_panes',
        'project_clear_context',
        'project_compact_context',
        'stop-all',
        'frontdesk_forward_planner',
    })

    def __init__(self, socket_path: str | Path, *, control_plane_transport=None) -> None:
        self._socket_path = Path(socket_path)
        if control_plane_transport is None:
            transport = transport_for_legacy_socket_path(
                self._socket_path,
                prefer_windows=_is_windows(),
            )
            if _is_windows() and not isinstance(transport, WindowsTcpControlPlaneTransport):
                transport = WindowsTcpControlPlaneTransport(None, legacy_socket_path=self._socket_path)
            self._control_plane_transport = transport
        else:
            self._control_plane_transport = control_plane_transport
        endpoint = getattr(self._control_plane_transport, 'endpoint', None)
        self._control_plane_endpoint = (
            dict(endpoint)
            if isinstance(endpoint, dict)
            else endpoint_from_legacy_socket_path(self._socket_path)
        )
        self._handlers: dict[str, callable] = {}
        self._desktop_adapter = None
        self._request_guard = None
        self._server = None
        self._connection_queue = queue.Queue(maxsize=_CONNECTION_QUEUE_MAXSIZE)
        self._worker_sentinel = object()
        self._worker_thread: threading.Thread | None = None
        self._maintenance_thread: threading.Thread | None = None
        self._worker_error: BaseException | None = None
        self._worker_error_lock = threading.Lock()
        self._bootstrap_gate_lock = threading.RLock()
        self._bootstrap_probe_active = False
        self._runtime_bootstrap_active = False
        self._bound_socket_stat: tuple[int, int] | None = None
        self._maintenance_state_lock = threading.Lock()
        self._after_response_actions: list[tuple[callable, bool]] = []
        self._pending_maintenance_ticks = 0
        self._maintenance_pending_event = threading.Event()
        self._stop_event = threading.Event()

    @property
    def socket_path(self) -> Path:
        return self._socket_path

    @property
    def control_plane_endpoint(self) -> dict:
        return dict(self._control_plane_endpoint)

    def register_handler(self, op: str, handler) -> None:
        if op in self._handlers:
            raise ValueError(f'duplicate handler for op {op!r}')
        self._handlers[op] = handler

    def set_desktop_adapter(self, adapter) -> None:
        """Install the independent desktop.v1 JSONL adapter."""
        if adapter is None or not callable(getattr(adapter, 'handle', None)):
            raise TypeError('desktop adapter must provide handle(request, peer=...)')
        self._desktop_adapter = adapter

    def set_request_guard(self, guard) -> None:
        self._request_guard = guard

    def listen(self) -> None:
        listen_server(self)

    def serve_forever(
        self,
        *,
        poll_interval: float = 0.2,
        on_tick=None,
        on_serving=None,
    ) -> None:
        serve_forever_impl(
            self,
            poll_interval=poll_interval,
            on_tick=on_tick,
            on_serving=on_serving,
        )

    def bootstrap_readiness_probe(self, *, timeout_s: float):
        return bootstrap_readiness_probe(self, timeout_s=timeout_s)

    def begin_runtime_bootstrap(self) -> None:
        with self._bootstrap_gate_lock:
            if self._server is None:
                raise RuntimeError('ccbd runtime bootstrap requires a listening socket')
            if self._stop_event.is_set():
                raise RuntimeError('ccbd runtime bootstrap cannot begin while stopping')
            if self._bootstrap_probe_active or self._runtime_bootstrap_active:
                raise RuntimeError('ccbd runtime bootstrap is already active')
            self._runtime_bootstrap_active = True

    def finish_runtime_bootstrap(self, publish_ready) -> None:
        if not callable(publish_ready):
            raise TypeError('ccbd runtime bootstrap completion requires a publication callback')
        with self._bootstrap_gate_lock:
            try:
                self._assert_runtime_bootstrap_can_finish_locked()
                publish_ready()
                self._assert_runtime_bootstrap_can_finish_locked()
            except BaseException:
                # Once final publication fails, even ping must fail closed.  In
                # particular, an atomic replace followed by directory-fsync
                # failure can leave a mounted record visible on disk; no RPC
                # may treat that record as a successfully opened control plane.
                self._stop_event.set()
                raise
            self._runtime_bootstrap_active = False

    def _assert_runtime_bootstrap_can_finish_locked(self) -> None:
        if not self._runtime_bootstrap_active:
            raise RuntimeError('ccbd runtime bootstrap is not active')
        if self._bootstrap_probe_active:
            raise RuntimeError('ccbd bootstrap self-probe is still active')
        if self._server is None:
            raise RuntimeError('ccbd listening socket is unavailable')
        if self._stop_event.is_set():
            raise RuntimeError('ccbd request serving stopped during runtime bootstrap')
        worker_error = self._peek_worker_error()
        if worker_error is not None:
            raise RuntimeError(f'ccbd request serving failed during runtime bootstrap: {worker_error}')
        worker = self._worker_thread
        if worker is None or not worker.is_alive():
            raise RuntimeError('ccbd request worker is not running during runtime bootstrap')

    def request_shutdown(self) -> None:
        shutdown_server(self)

    def shutdown(self) -> None:
        self.request_shutdown()
        stop_worker(self)
        stop_maintenance_worker(self)

    def request_maintenance_ticks(self, handled_op: str | None) -> int:
        if str(handled_op or '').startswith('desktop.v1:'):
            method = str(handled_op).split(':', 1)[1]
            return 1 if method in {'job.submit', 'job.cancel', 'job.retry'} else 0
        if handled_op not in self._MUTATING_OPS:
            return 0
        return 1

    def queue_maintenance_ticks(self, count: int) -> None:
        if count > 0:
            with self._maintenance_state_lock:
                # Post-request maintenance is a dirty signal, not a counted work queue.
                self._pending_maintenance_ticks = 1
                self._maintenance_pending_event.set()
                pending_ticks = self._pending_maintenance_ticks
            self._record_pending_maintenance_ticks_value(pending_ticks)

    def queue_periodic_maintenance_tick(self) -> None:
        self.queue_maintenance_ticks(1)

    def take_queued_maintenance_ticks(self) -> int:
        with self._maintenance_state_lock:
            count = self._pending_maintenance_ticks
            self._pending_maintenance_ticks = 0
            if not self._after_response_actions:
                self._maintenance_pending_event.clear()
            pending_ticks = self._pending_maintenance_ticks
        self._record_pending_maintenance_ticks_value(pending_ticks)
        return count

    def queue_post_request_maintenance(self, handled_op: str | None) -> None:
        self.queue_maintenance_ticks(self.request_maintenance_ticks(handled_op))

    def take_pending_maintenance_ticks(self) -> int:
        return max(0, int(self.take_queued_maintenance_ticks()))

    def maintenance_pending(self) -> bool:
        with self._maintenance_state_lock:
            return self._pending_maintenance_ticks > 0 or bool(self._after_response_actions)

    def queue_after_response_action(
        self,
        action,
        *,
        run_during_shutdown: bool = False,
    ) -> None:
        if callable(action):
            with self._maintenance_state_lock:
                self._after_response_actions.append((action, bool(run_during_shutdown)))
                self._maintenance_pending_event.set()

    def pop_after_response_actions(
        self,
        *,
        shutdown_only: bool = False,
    ) -> tuple[callable, ...]:
        with self._maintenance_state_lock:
            queued = tuple(self._after_response_actions)
            self._after_response_actions.clear()
            if self._pending_maintenance_ticks <= 0:
                self._maintenance_pending_event.clear()
        return tuple(
            action
            for action, run_during_shutdown in queued
            if not shutdown_only or run_during_shutdown
        )

    def _record_pending_maintenance_ticks_value(self, value: int) -> None:
        callback = getattr(self, '_record_pending_maintenance_ticks', None)
        if callable(callback):
            try:
                callback(value)
            except Exception:
                pass

    def _handle_connection(self, conn) -> str | None:
        return handle_connection(self, conn)

    def _reset_worker_error(self) -> None:
        with self._worker_error_lock:
            self._worker_error = None

    def _record_worker_error(self, error: BaseException) -> None:
        with self._worker_error_lock:
            if self._worker_error is None:
                self._worker_error = error

    def _peek_worker_error(self) -> BaseException | None:
        with self._worker_error_lock:
            return self._worker_error

    def _take_worker_error(self) -> BaseException | None:
        with self._worker_error_lock:
            error = self._worker_error
            self._worker_error = None
            return error


__all__ = ['CcbdSocketServer']
