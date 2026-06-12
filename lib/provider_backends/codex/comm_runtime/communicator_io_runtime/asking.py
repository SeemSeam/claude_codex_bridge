from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from provider_core.comm_logging import get_comm_logger, log_comm_event

from .common import ensure_session_health, remember_log_hint

_logger = get_comm_logger('codex.comm')


def send_message(comm, content: str) -> tuple[str, dict[str, Any]]:
    marker = comm._generate_marker()
    message = {
        "content": content,
        "timestamp": datetime.now().isoformat(),
        "marker": marker,
    }

    state = comm.log_reader.capture_state()
    with open(comm.input_fifo, "w", encoding="utf-8") as fifo:
        fifo.write(json.dumps(message, ensure_ascii=False) + "\n")
        fifo.flush()
    return marker, state


def ask_async(comm, question: str) -> bool:
    try:
        ensure_session_health(comm)
        marker, state = comm._send_message(question)
        remember_log_hint(comm, state)
        print(f"📤 Written to Codex, delivery unconfirmed (marker: {marker[:12]}...)")
        print("Hint: `ccb pend <agent|job_id>` is only a supplementary observer view, not an authoritative completion path")
        return True
    except Exception as exc:
        log_comm_event(
            _logger,
            provider='codex',
            direction='send',
            endpoint=str(getattr(comm, 'input_fifo', '?')),
            event='ask_async_failed',
            error=exc,
        )
        print(f"❌ Send failed: {exc}")
        return False


__all__ = ["ask_async", "send_message"]
