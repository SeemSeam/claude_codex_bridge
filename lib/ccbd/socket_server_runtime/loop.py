from __future__ import annotations

import queue
import socket
import threading
import time

_ACCEPT_POLL_TIMEOUT_S = 0.2
_WORKER_JOIN_TIMEOUT_S = 2.0
_MAX_QUEUED_CONNECTION_AGE_S = 1.0


def serve_forever(
    server,
    *,
    poll_interval: float = 0.2,
    on_tick=None,
    on_serving=None,
) -> None:
    server.listen()
    interval = max(0.0, float(poll_interval))
    start_worker(server, interval=interval, on_tick=on_tick)
    if callable(on_serving):
        server.queue_after_response_action(on_serving)
    start_maintenance_worker(server, interval=interval, on_tick=on_tick)
    try:
        while not server._stop_event.is_set():
            runtime_socket = server._server
            if runtime_socket is None:
                break
            runtime_socket.settimeout(_ACCEPT_POLL_TIMEOUT_S)
            try:
                conn, _ = runtime_socket.accept()
            except (socket.timeout, TimeoutError):
                if server._stop_event.is_set() or server._server is not runtime_socket:
                    break
                continue
            except OSError:
                break
            enqueue_connection(server, conn)
    finally:
        stop_worker(server)
        stop_maintenance_worker(server)
    worker_error = server._take_worker_error()
    if worker_error is not None:
        raise worker_error


def start_worker(server, *, interval: float, on_tick) -> None:
    worker = getattr(server, '_worker_thread', None)
    if worker is not None and worker.is_alive():
        return
    worker = threading.Thread(
        target=worker_loop,
        args=(server,),
        kwargs={'interval': interval, 'on_tick': on_tick},
        name='ccbd-socket-worker',
        daemon=True,
    )
    server._worker_thread = worker
    worker.start()


def start_maintenance_worker(server, *, interval: float, on_tick) -> None:
    worker = getattr(server, '_maintenance_thread', None)
    if worker is not None and worker.is_alive():
        return
    worker = threading.Thread(
        target=maintenance_worker_loop,
        args=(server,),
        kwargs={'interval': interval, 'on_tick': on_tick},
        name='ccbd-maintenance-worker',
        daemon=True,
    )
    server._maintenance_thread = worker
    worker.start()


def stop_worker(server) -> None:
    close_streams = getattr(server, 'close_desktop_streams', None)
    if callable(close_streams):
        close_streams()
    worker = getattr(server, '_worker_thread', None)
    if worker is None:
        return
    close_pending_connections(server)
    try:
        server._connection_queue.put_nowait(server._worker_sentinel)
    except queue.Full:
        close_oldest_queued_connection(server)
        try:
            server._connection_queue.put_nowait(server._worker_sentinel)
        except Exception:
            pass
    except Exception:
        pass
    if worker is not threading.current_thread():
        worker.join(timeout=_WORKER_JOIN_TIMEOUT_S)
    if not worker.is_alive():
        server._worker_thread = None


def stop_maintenance_worker(server) -> None:
    worker = getattr(server, '_maintenance_thread', None)
    if worker is not None:
        server._maintenance_pending_event.set()
        if worker is not threading.current_thread():
            worker.join(timeout=_WORKER_JOIN_TIMEOUT_S)
        if not worker.is_alive():
            server._maintenance_thread = None
    # The request worker is stopped before this function during serve-loop
    # teardown, so every response finalizer has now been queued.  Drain any
    # finalizer that arrived after the maintenance lane observed stop_event;
    # stop-all must not lose namespace destruction to that shutdown race.
    run_after_response_actions(
        server,
        shutdown_only=server._stop_event.is_set(),
    )


def enqueue_connection(server, conn) -> None:
    if server._stop_event.is_set():
        close_connection(conn)
        return
    item = (conn, time.monotonic())
    try:
        server._connection_queue.put_nowait(item)
    except queue.Full:
        close_oldest_queued_connection(server)
        try:
            server._connection_queue.put_nowait(item)
        except queue.Full:
            close_connection(conn)


def close_pending_connections(server) -> None:
    while True:
        try:
            item = server._connection_queue.get_nowait()
        except queue.Empty:
            return
        if item is server._worker_sentinel:
            continue
        conn = item[0] if isinstance(item, tuple) else item
        close_connection(conn)


def close_oldest_queued_connection(server) -> None:
    try:
        item = server._connection_queue.get_nowait()
    except queue.Empty:
        return
    if item is server._worker_sentinel:
        try:
            server._connection_queue.put_nowait(item)
        except queue.Full:
            pass
        return
    conn = item[0] if isinstance(item, tuple) else item
    close_connection(conn)


def close_connection(conn) -> None:
    try:
        conn.close()
    except OSError:
        pass


def worker_loop(server, *, interval: float, on_tick) -> None:
    try:
        while True:
            try:
                item = server._connection_queue.get(timeout=_ACCEPT_POLL_TIMEOUT_S)
            except queue.Empty:
                if server._stop_event.is_set():
                    break
                continue
            if item is server._worker_sentinel:
                break
            if isinstance(item, tuple):
                conn, enqueued_at = item
            else:
                conn, enqueued_at = item, None
            if queued_connection_expired(enqueued_at):
                close_connection(conn)
                continue
            handled_op = handle_worker_connection(server, conn, enqueued_at=enqueued_at)
            if server._stop_event.is_set():
                continue
            server.queue_post_request_maintenance(handled_op)
    except Exception as exc:
        server._record_worker_error(exc)
        server._stop_event.set()


def maintenance_worker_loop(server, *, interval: float, on_tick) -> None:
    next_tick_at = time.monotonic() + interval
    try:
        while True:
            timeout = next_timeout(next_tick_at=next_tick_at, on_tick=on_tick)
            woke_for_pending = server._maintenance_pending_event.wait(timeout=timeout)
            # A shutdown signal can race the request worker between preparing
            # a stop-all response and queueing its destructive finalizer.  Any
            # shutdown-required action already queued remains mandatory after
            # stop_event; ordinary bootstrap actions must still fail closed.
            if server._stop_event.is_set():
                run_after_response_actions(server, shutdown_only=True)
                break
            run_after_response_actions(server)
            if server._stop_event.is_set():
                break
            if woke_for_pending:
                next_tick_at = post_request_tick(
                    server=server,
                    on_tick=on_tick,
                    next_tick_at=next_tick_at,
                    interval=interval,
                )
                continue
            next_tick_at = run_tick_if_needed(
                server=server,
                on_tick=on_tick,
                next_tick_at=next_tick_at,
                interval=interval,
            )
    except Exception as exc:
        server._record_worker_error(exc)
        server._stop_event.set()


def handle_worker_connection(server, conn, *, enqueued_at=None) -> str | None:
    try:
        with conn:
            if enqueued_at is not None:
                callback = getattr(server, '_record_request_queue_wait', None)
                if callable(callback):
                    try:
                        callback(max(0.0, time.monotonic() - float(enqueued_at)))
                    except Exception:
                        pass
            return server._handle_connection(conn)
    except Exception:
        return None


def queued_connection_expired(enqueued_at) -> bool:
    if enqueued_at is None:
        return False
    try:
        return time.monotonic() - float(enqueued_at) > _MAX_QUEUED_CONNECTION_AGE_S
    except Exception:
        return False


def next_timeout(*, next_tick_at: float, on_tick) -> float | None:
    if on_tick is None:
        return _ACCEPT_POLL_TIMEOUT_S
    return max(0.0, next_tick_at - time.monotonic())


def next_worker_timeout(*, server, next_tick_at: float, on_tick) -> float | None:
    if on_tick is not None and server.maintenance_pending():
        return 0.0
    return next_timeout(next_tick_at=next_tick_at, on_tick=on_tick)


def run_tick_if_needed(*, server, on_tick, next_tick_at: float, interval: float) -> float:
    if on_tick is None:
        return next_tick_at
    if request_backlog_pending(server):
        return time.monotonic() + interval
    server.queue_periodic_maintenance_tick()
    return post_request_tick(
        server=server,
        on_tick=on_tick,
        next_tick_at=next_tick_at,
        interval=interval,
    )


def post_request_tick(
    *,
    server,
    on_tick,
    next_tick_at: float,
    interval: float,
) -> float:
    tick_count = server.take_pending_maintenance_ticks()
    if tick_count == 0 and on_tick is not None and time.monotonic() >= next_tick_at:
        server.queue_periodic_maintenance_tick()
        tick_count = server.take_pending_maintenance_ticks()
    if on_tick is not None:
        if tick_count > 0:
            run_queued_maintenance_ticks(on_tick=on_tick, tick_count=tick_count)
            return time.monotonic() + interval
    return next_tick_at


def run_queued_maintenance_ticks(*, on_tick, tick_count: int) -> None:
    if tick_count <= 0:
        return
    on_tick()
    for _ in range(tick_count - 1):
        on_tick()


def run_after_response_actions(server, *, shutdown_only: bool = False) -> None:
    for action in server.pop_after_response_actions(shutdown_only=shutdown_only):
        try:
            action()
        except Exception as exc:
            server._record_worker_error(exc)
            server._stop_event.set()
            break


def request_backlog_pending(server) -> bool:
    try:
        return not server._connection_queue.empty()
    except Exception:
        return False


__all__ = [
    'close_pending_connections',
    'close_connection',
    'close_oldest_queued_connection',
    'enqueue_connection',
    'handle_worker_connection',
    'maintenance_worker_loop',
    'next_timeout',
    'next_worker_timeout',
    'post_request_tick',
    'queued_connection_expired',
    'request_backlog_pending',
    'run_after_response_actions',
    'run_queued_maintenance_ticks',
    'run_tick_if_needed',
    'serve_forever',
    'start_maintenance_worker',
    'start_worker',
    'stop_maintenance_worker',
    'stop_worker',
    'worker_loop',
]
