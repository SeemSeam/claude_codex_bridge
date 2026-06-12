from __future__ import annotations

import time
from pathlib import Path

from provider_core.comm_logging import get_comm_logger, log_comm_event
from provider_core.fifo_delivery import wait_for_ack
from ui_text import t

from .common import ensure_session_health, remember_log_hint

_logger = get_comm_logger('codex.comm')


def ask_sync(comm, question: str, timeout: int | None = None) -> str | None:
    try:
        ensure_session_health(comm)
        print(f"🔔 {t('sending_to', provider='Codex')}", flush=True)
        marker, state = comm._send_message(question)
        input_fifo = getattr(comm, 'input_fifo', None)
        if input_fifo:
            ack_dir = Path(input_fifo).parent / "acks"
            if wait_for_ack(ack_dir, marker):
                print("✅ Delivery confirmed by bridge", flush=True)
            else:
                log_comm_event(
                    _logger,
                    provider='codex',
                    direction='send',
                    endpoint=str(input_fifo),
                    event='ack_timeout_sync',
                )
                print("⚠️ Delivery unconfirmed — still waiting for a reply, but the bridge may not have received the question", flush=True)

        wait_timeout = comm.timeout if timeout is None else int(timeout)
        if wait_timeout == 0:
            print(f"⏳ {t('waiting_for_reply', provider='Codex')}", flush=True)
            return _wait_indefinitely(comm, state)

        print(f"⏳ Waiting for Codex reply (timeout {wait_timeout}s)...")
        return _wait_once(comm, state, float(wait_timeout))
    except Exception as exc:
        log_comm_event(
            _logger,
            provider='codex',
            direction='send',
            endpoint=str(getattr(comm, 'input_fifo', '?')),
            event='ask_sync_failed',
            error=exc,
        )
        print(f"❌ Sync ask failed: {exc}")
        return None


def _wait_indefinitely(comm, state) -> str | None:
    start_time = time.time()
    last_hint = 0
    while True:
        message, state = _wait_for_message(comm, state, timeout=30.0)
        if message:
            return _display_reply(message)
        elapsed = int(time.time() - start_time)
        if elapsed >= last_hint + 30:
            last_hint = elapsed
            print(f"⏳ Still waiting... ({elapsed}s)")


def _wait_once(comm, state, timeout: float) -> str | None:
    message, _ = _wait_for_message(comm, state, timeout=timeout)
    if message:
        return _display_reply(message)
    print(f"⏰ {t('timeout_no_reply', provider='Codex')}")
    return None


def _wait_for_message(comm, state, *, timeout: float):
    message, new_state = comm.log_reader.wait_for_message(state, timeout)
    next_state = new_state or state
    remember_log_hint(comm, new_state if isinstance(new_state, dict) else next_state)
    return message, next_state


def _display_reply(message: str) -> str:
    print(f"🤖 {t('reply_from', provider='Codex')}")
    print(message)
    return message


__all__ = ["ask_sync"]
