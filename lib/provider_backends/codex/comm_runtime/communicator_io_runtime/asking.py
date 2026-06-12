from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from pathlib import Path

from provider_core.comm_logging import get_comm_logger, log_comm_event
from provider_core.fifo_delivery import (
    PIPE_ATOMIC_LIMIT,
    DeliveryResult,
    spool_payload,
    wait_for_ack,
    write_fifo_line,
)

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
    fifo_path = Path(comm.input_fifo)
    line = json.dumps(message, ensure_ascii=False)
    if len(line.encode("utf-8")) + 1 > PIPE_ATOMIC_LIMIT:
        # Oversized payloads can't be written atomically to a FIFO; park the
        # body in a spool file and send a small pointer line instead.
        spool_file = spool_payload(fifo_path.parent / "spool", marker, line)
        line = json.dumps({"marker": marker, "spool": str(spool_file)}, ensure_ascii=False)
    write_fifo_line(fifo_path, line)
    return marker, state


def ask_async(comm, question: str) -> DeliveryResult:
    try:
        ensure_session_health(comm)
        marker, state = comm._send_message(question)
        remember_log_hint(comm, state)
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
        return DeliveryResult.FAILED

    input_fifo = getattr(comm, "input_fifo", None)
    if not input_fifo:
        print(f"📤 Written to Codex, delivery unconfirmed (marker: {marker[:12]}...)")
        return DeliveryResult.UNCONFIRMED
    ack_dir = Path(input_fifo).parent / "acks"
    if wait_for_ack(ack_dir, marker):
        print(f"✅ Delivered to Codex (marker: {marker[:12]}...)")
        result = DeliveryResult.DELIVERED
    else:
        log_comm_event(
            _logger,
            provider='codex',
            direction='send',
            endpoint=str(comm.input_fifo),
            event='ack_timeout',
        )
        print(f"⚠️ Written but unconfirmed — receiver may be busy (marker: {marker[:12]}...)")
        result = DeliveryResult.UNCONFIRMED
    print("Hint: `ccb pend <agent|job_id>` is only a supplementary observer view, not an authoritative completion path")
    return result


__all__ = ["ask_async", "send_message"]
