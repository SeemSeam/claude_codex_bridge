from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

from provider_core.fifo_delivery import DeliveryResult
from runtime_env import env_float
from terminal_runtime import get_backend_for_session, get_pane_id_from_session

from . import (
    ask_async as _ask_async_impl,
    ask_sync as _ask_sync_impl,
    check_session_health as _check_session_health_impl,
    check_tmux_runtime_health,
    consume_pending as _consume_pending_impl,
    ensure_log_reader as _ensure_log_reader_impl,
    find_codex_session_file,
    get_status as _get_status_impl,
    initialize_state as _initialize_state,
    load_codex_session_info,
    pane_alive as _pane_alive_impl,
    prime_log_binding as _prime_log_binding_impl,
    remember_codex_session as _remember_codex_session_impl,
    send_message as _send_message_impl,
    update_project_session_binding,
)

def _publish_registry_binding_proxy(**kwargs) -> None:
    from .. import comm as codex_comm_module

    codex_comm_module.publish_registry_binding(**kwargs)


def _codex_log_reader_cls():
    from .. import comm as codex_comm_module

    return codex_comm_module.CodexLogReader


class CodexCommunicator:
    """Communicates with Codex bridge via FIFO and reads replies from logs."""

    def __init__(self, lazy_init: bool = False):
        _initialize_state(
            self,
            get_pane_id_from_session_fn=get_pane_id_from_session,
            get_backend_for_session_fn=get_backend_for_session,
            pane_health_ttl=env_float("CCB_CODEX_PANE_HEALTH_TTL", 1.0),
        )

        if not lazy_init:
            self._ensure_log_reader()
            healthy, msg = self._check_session_health()
            if not healthy:
                raise RuntimeError(
                    f"❌ Session unhealthy: {msg}\nTip: Run 'ccb codex' (or add codex to ccb.config) to start a new session"
                )

    @property
    def log_reader(self):
        if self._log_reader is None:
            self._ensure_log_reader()
        return self._log_reader

    def _ensure_log_reader(self) -> None:
        _ensure_log_reader_impl(self, log_reader_cls=_codex_log_reader_cls())

    def _find_session_file(self) -> Path | None:
        return find_codex_session_file()

    def _load_session_info(self):
        return load_codex_session_info(session_finder=self._find_session_file)

    def _prime_log_binding(self) -> None:
        _prime_log_binding_impl(self)

    def _check_session_health(self):
        return self._check_session_health_impl(probe_terminal=True)

    def _check_session_health_impl(self, probe_terminal: bool):
        return _check_session_health_impl(
            self,
            probe_terminal=probe_terminal,
            tmux_health_checker=check_tmux_runtime_health,
        )

    def _invalidate_pane_health_cache(self) -> None:
        self._pane_health_cache = None

    def _pane_alive(self, *, force: bool) -> bool:
        return _pane_alive_impl(self, force=force)

    def _send_via_terminal(self, content: str) -> None:
        if not self.backend or not self.pane_id:
            raise RuntimeError("Terminal session not configured")
        self.backend.send_text(self.pane_id, content)

    def _send_message(self, content: str) -> tuple[str, dict[str, Any]]:
        return _send_message_impl(self, content)

    def _generate_marker(self) -> str:
        return f"{self.marker_prefix}-{int(time.time())}-{os.getpid()}"

    def ask_async(self, question: str) -> "DeliveryResult":
        # DeliveryResult is bool-compatible: FAILED is falsy, others truthy.
        return _ask_async_impl(self, question)

    def ask_sync(self, question: str, timeout: int | None = None) -> str | None:
        return _ask_sync_impl(self, question, timeout=timeout)

    def consume_pending(self, display: bool = True, n: int = 1):
        return _consume_pending_impl(self, display=display, n=n)

    def ping(self, display: bool = True) -> tuple[bool, str]:
        healthy, status = self._check_session_health()
        msg = f"✅ Codex connection OK ({status})" if healthy else f"❌ Codex connection error: {status}"
        if display:
            print(msg)
        return healthy, msg

    def get_status(self) -> dict[str, Any]:
        return _get_status_impl(self)

    def _remember_codex_session(self, log_path: Path | None) -> None:
        _remember_codex_session_impl(
            self,
            log_path,
            update_project_session_binding_fn=update_project_session_binding,
            publish_registry_binding_fn=_publish_registry_binding_proxy,
            debug_enabled=os.environ.get("CCB_DEBUG") in ("1", "true", "yes"),
        )

    @staticmethod
    def _extract_session_id(log_path: Path) -> str | None:
        from .. import comm as codex_comm_module

        return codex_comm_module._extract_session_id(log_path)


__all__ = ["CodexCommunicator"]
