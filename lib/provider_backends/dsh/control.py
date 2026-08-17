from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import uuid
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from provider_sessions.files import safe_write_session


def load_dsh_host_endpoint(
    state_path: Path,
    *,
    expected_instance_id: str | None = None,
) -> str:
    path = Path(state_path).expanduser()
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        raise RuntimeError(f'dsh host state unavailable: {path}') from exc
    if not isinstance(payload, dict) or payload.get('record_type') != 'dsh_host_state':
        raise RuntimeError('dsh host state is invalid')
    expected = str(expected_instance_id or '').strip()
    if expected and str(payload.get('host_instance_id') or '') != expected:
        raise RuntimeError('dsh host state belongs to another launch instance')
    if str(payload.get('status') or '') != 'ready':
        detail = str(payload.get('detail') or payload.get('status') or 'not ready')
        raise RuntimeError(f'dsh host is not ready: {detail}')
    endpoint = _validated_loopback_endpoint(str(payload.get('endpoint') or ''))
    return endpoint


def dsh_rpc(
    endpoint: str,
    method: str,
    payload: dict[str, object],
    *,
    rpc_id: str | None = None,
    timeout: float = 10.0,
) -> dict[str, object]:
    base = _validated_loopback_endpoint(endpoint)
    request_id = str(rpc_id or f'ccb-dsh-{uuid.uuid4()}')
    envelope = {
        'type': 'client-request',
        'rpcId': request_id,
        'method': method,
        'payload': payload,
    }
    request = Request(
        f'{base}/api/{method}',
        data=json.dumps(envelope, ensure_ascii=False).encode('utf-8'),
        headers={'content-type': 'application/json'},
        method='POST',
    )
    try:
        # DSH is loopback-only.  Explicitly bypass proxy environment variables
        # so credentials and prompts cannot leave the host through a proxy.
        with build_opener(ProxyHandler({})).open(request, timeout=max(0.1, timeout)) as response:
            body = response.read().decode('utf-8', 'replace')
    except Exception as exc:
        raise RuntimeError(f'dsh RPC {method} failed: {type(exc).__name__}: {exc}') from exc
    try:
        result = json.loads(body)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f'dsh RPC {method} returned invalid JSON') from exc
    if not isinstance(result, dict) or result.get('type') != 'server-response':
        raise RuntimeError(f'dsh RPC {method} returned an invalid envelope')
    if str(result.get('rpcId') or '') != request_id:
        raise RuntimeError(f'dsh RPC {method} correlation mismatch')
    outcome = result.get('result')
    if not isinstance(outcome, dict):
        raise RuntimeError(f'dsh RPC {method} omitted its result')
    if outcome.get('ok') is not True:
        error = outcome.get('error')
        if isinstance(error, dict):
            code = str(error.get('code') or 'unknown')
            message = str(error.get('message') or code)
            raise RuntimeError(f'dsh RPC {method} rejected: {code}: {message}')
        raise RuntimeError(f'dsh RPC {method} was rejected')
    value = outcome.get('value')
    return dict(value) if isinstance(value, dict) else {}


def compact_dsh_session(session_file: Path, *, timeout: float = 20.0) -> dict[str, object]:
    path, record = _load_session_record(session_file)
    session_id = _required_text(record, 'dsh_session_id')
    host_instance_id = _required_text(record, 'dsh_host_instance_id')
    endpoint_state = Path(_required_text(record, 'dsh_endpoint_state_path')).expanduser()
    endpoint = load_dsh_host_endpoint(
        endpoint_state,
        expected_instance_id=host_instance_id,
    )
    cwd = str(record.get('work_dir') or record.get('workspace_path') or path.parent)
    dsh_rpc(
        endpoint,
        'session.create',
        {'sessionId': session_id, 'cwd': cwd},
        rpc_id=f'ccb-compact-create-{uuid.uuid4()}',
        timeout=timeout,
    )
    # DSH commands are a typed Remote surface, not model prompts.  Calling the
    # command endpoint keeps /compact out of the model turn and returns the
    # native command outcome paired with command/run + command/done events.
    value = dsh_rpc(
        endpoint,
        'commands/execute',
        {'args': {'agentId': session_id, 'line': '/compact'}},
        rpc_id=f'ccb-compact-{uuid.uuid4()}',
        timeout=timeout,
    )
    command_id = str(value.get('commandId') or '').strip()
    result = value.get('result')
    if not command_id or not isinstance(result, dict):
        raise RuntimeError('dsh /compact was not admitted by the native command registry')
    if result.get('kind') != 'success':
        detail = str(result.get('text') or 'native command failed').strip()
        raise RuntimeError(f'dsh /compact failed: {detail}')
    return {
        'session_id': session_id,
        'command': '/compact',
        'command_id': command_id,
        'detail': str(result.get('text') or '').strip() or None,
    }


def rotate_dsh_session(session_file: Path) -> dict[str, object]:
    path, record = _load_session_record(session_file)
    old_session_id = _required_text(record, 'dsh_session_id')
    new_session_id = f'session-{uuid.uuid4()}'
    try:
        generation = max(0, int(record.get('dsh_context_generation') or 0)) + 1
    except (TypeError, ValueError):
        generation = 1
    updated = dict(record)
    updated.update(
        {
            'dsh_session_id': new_session_id,
            'dsh_context_generation': generation,
            'dsh_context_cleared_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            'dsh_resume_status': 'context_rotated',
        }
    )
    ok, error = safe_write_session(path, json.dumps(updated, ensure_ascii=False, indent=2) + '\n')
    if not ok:
        raise RuntimeError(f'dsh session rotation failed: {error or "write_failed"}')
    return {
        'old_session_id': old_session_id,
        'session_id': new_session_id,
        'context_generation': generation,
    }


def _load_session_record(session_file: Path) -> tuple[Path, dict[str, object]]:
    path = Path(session_file).expanduser()
    try:
        payload = json.loads(path.read_text(encoding='utf-8-sig'))
    except Exception as exc:
        raise RuntimeError(f'dsh session binding unavailable: {path}') from exc
    if not isinstance(payload, dict) or str(payload.get('provider') or '') != 'dsh':
        raise RuntimeError('dsh session binding is invalid')
    return path, payload


def _required_text(record: dict[str, object], key: str) -> str:
    value = str(record.get(key) or '').strip()
    if not value:
        raise RuntimeError(f'dsh session binding omitted {key}')
    return value


def _validated_loopback_endpoint(value: str) -> str:
    endpoint = str(value or '').strip().rstrip('/')
    try:
        parsed = urlsplit(endpoint)
    except Exception as exc:
        raise RuntimeError('dsh host endpoint is invalid') from exc
    if parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost', '::1'}:
        raise RuntimeError('dsh host endpoint must be loopback HTTP')
    if parsed.username or parsed.password or parsed.query or parsed.fragment or parsed.path not in {'', '/'}:
        raise RuntimeError('dsh host endpoint contains unsupported components')
    try:
        port = parsed.port
    except ValueError as exc:
        raise RuntimeError('dsh host endpoint port is invalid') from exc
    if port is None or not (1 <= port <= 65535):
        raise RuntimeError('dsh host endpoint port is missing')
    host = '127.0.0.1' if parsed.hostname in {'127.0.0.1', 'localhost'} else '[::1]'
    return f'http://{host}:{port}'


__all__ = [
    'compact_dsh_session',
    'dsh_rpc',
    'load_dsh_host_endpoint',
    'rotate_dsh_session',
]
