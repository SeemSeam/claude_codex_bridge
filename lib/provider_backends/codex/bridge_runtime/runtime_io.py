from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Any

from provider_core.comm_logging import get_comm_logger, log_comm_event
from provider_core.runtime_specs import provider_marker_prefix

from .runtime_state import BridgeRuntimeState

_logger = get_comm_logger('codex.bridge')


def read_request(state: BridgeRuntimeState) -> dict[str, Any] | None:
    if not state.paths.input_fifo.exists():
        return None
    try:
        with state.paths.input_fifo.open('r', encoding='utf-8') as fifo:
            line = fifo.readline()
            if not line:
                return None
            return json.loads(line)
    except (OSError, json.JSONDecodeError) as exc:
        log_comm_event(
            _logger,
            provider='codex',
            direction='recv',
            endpoint=str(state.paths.input_fifo),
            event='read_request_failed',
            error=exc,
        )
        return None


def process_request(
    state: BridgeRuntimeState,
    payload: dict[str, Any],
    *,
    log_console_fn,
) -> None:
    content = payload.get('content', '')
    marker = payload.get('marker') or generate_marker()
    timestamp = timestamp_now()
    log_bridge(
        state,
        json.dumps({'marker': marker, 'question': content, 'time': timestamp}, ensure_ascii=False),
    )
    append_history(state, 'claude', content, marker, log_console_fn=log_console_fn)

    try:
        state.codex_session.send(content)
    except Exception as exc:
        message = f'Failed to send to Codex: {exc}'
        log_comm_event(
            _logger,
            provider='codex',
            direction='send',
            endpoint='codex_session',
            event='forward_to_pane_failed',
            error=exc,
        )
        append_history(state, 'codex', message, marker, log_console_fn=log_console_fn)
        log_console_fn(message)


def append_history(
    state: BridgeRuntimeState,
    role: str,
    content: str,
    marker: str,
    *,
    log_console_fn,
) -> None:
    entry = {
        'timestamp': datetime.now(timezone.utc).isoformat(),
        'role': role,
        'marker': marker,
        'content': content,
    }
    try:
        with state.paths.history_file.open('a', encoding='utf-8') as handle:
            json.dump(entry, handle, ensure_ascii=False)
            handle.write('\n')
    except Exception as exc:
        log_console_fn(f'Failed to write history: {exc}')


def log_bridge(state: BridgeRuntimeState, message: str) -> None:
    try:
        with state.paths.bridge_log.open('a', encoding='utf-8') as handle:
            handle.write(f'{timestamp_now()} {message}\n')
    except Exception as exc:
        log_comm_event(
            _logger,
            provider='codex',
            direction='send',
            endpoint=str(state.paths.bridge_log),
            event='bridge_log_write_failed',
            error=exc,
        )


def timestamp_now() -> str:
    return datetime.now().strftime('%Y-%m-%d %H:%M:%S')


def generate_marker() -> str:
    return f"{provider_marker_prefix('codex')}-{int(time.time())}-{os.getpid()}"


__all__ = [
    'append_history',
    'generate_marker',
    'log_bridge',
    'process_request',
    'read_request',
    'timestamp_now',
]
