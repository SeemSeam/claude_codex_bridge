from __future__ import annotations

import json
from time import monotonic

from ccbd.api_models import RpcRequest, RpcResponse

_REQUEST_READ_TIMEOUT_S = 0.5
_MAX_REQUEST_BYTES = 1024 * 1024


def handle_connection(server, conn) -> str | None:
    request = None
    after_response_action = None
    try:
        conn.settimeout(_REQUEST_READ_TIMEOUT_S)
        raw = _recv_request_line(conn)
        if not raw:
            return None
        message = json.loads(raw.split(b'\n', 1)[0].decode('utf-8'))
        desktop_adapter = getattr(server, '_desktop_adapter', None)
        if desktop_adapter is not None and isinstance(message, dict) and 'protocol_version' in message:
            # Keep desktop.v1 envelope validation independent from api_version=2.
            response = desktop_adapter.handle(message, peer=conn)
            conn.sendall((json.dumps(response, ensure_ascii=False) + '\n').encode('utf-8'))
            return f"desktop.v1:{str(message.get('method') or '').strip()}"
        request = RpcRequest.from_record(message)
        # Final lifecycle publication and opening the runtime-bootstrap gate are
        # one request-dispatch boundary.  Keep the gate through handler start so
        # shutdown cannot clear bootstrap flags between the decision and an
        # authority-mutating handler.  The request worker is already serial, so
        # this does not reduce request concurrency; handlers follow the same
        # gate -> authority-lock order as final mounted publication.
        with server._bootstrap_gate_lock:
            if server._stop_event.is_set() and request.op != 'shutdown':
                response = RpcResponse.failure('ccbd request serving is stopping')
            else:
                bootstrap_restricted = bool(
                    getattr(server, '_bootstrap_probe_active', False)
                    or getattr(server, '_runtime_bootstrap_active', False)
                )
                handler = server._handlers.get(request.op)
                if bootstrap_restricted and request.op != 'ping':
                    response = RpcResponse.failure('ccbd bootstrap accepts ping only')
                elif handler is None:
                    response = RpcResponse.failure(f'unknown op: {request.op}')
                else:
                    guard = getattr(server, '_request_guard', None)
                    rejection = guard(request.op) if guard is not None else None
                    if rejection:
                        response = RpcResponse.failure(rejection)
                    else:
                        started = monotonic()
                        try:
                            payload = handler(request.request)
                        finally:
                            _record_handler_latency(server, request.op, max(0.0, monotonic() - started))
                        if isinstance(payload, tuple) and len(payload) == 2:
                            payload, after_response_action = payload
                        response = RpcResponse.success(payload)
    except Exception as exc:
        response = RpcResponse.failure(str(exc))
    try:
        conn.sendall((json.dumps(response.to_record(), ensure_ascii=False) + '\n').encode('utf-8'))
    except OSError:
        _queue_after_response_action(
            server,
            after_response_action,
            request_op=getattr(request, 'op', None),
        )
        return getattr(request, 'op', None)
    _queue_after_response_action(
        server,
        after_response_action,
        request_op=getattr(request, 'op', None),
    )
    return getattr(request, 'op', None)


def _recv_request_line(conn) -> bytes:
    raw = b''
    while b'\n' not in raw:
        chunk = conn.recv(65536)
        if not chunk:
            break
        raw += chunk
        if len(raw) > _MAX_REQUEST_BYTES:
            raise ValueError('ccbd request exceeds maximum size')
    return raw


def _queue_after_response_action(server, action, *, request_op: str | None) -> None:
    if action is None:
        return
    try:
        server.queue_after_response_action(
            action,
            run_during_shutdown=request_op in {'stop-all', 'shutdown'},
        )
    except Exception:
        pass


def _record_handler_latency(server, op: str, duration: float) -> None:
    callback = getattr(server, '_record_handler_latency', None)
    if not callable(callback):
        return
    try:
        callback(op, duration)
    except Exception:
        pass


__all__ = ['handle_connection']
