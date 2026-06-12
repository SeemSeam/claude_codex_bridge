from __future__ import annotations

from pathlib import Path

from provider_core.comm_logging import get_comm_logger, log_comm_event
from provider_core.protocol import is_done_text, make_req_id, strip_done_text
from terminal_runtime import get_backend_for_session, get_pane_id_from_session

from ..protocol import wrap_claude_prompt
from ..resolver import resolve_claude_session
from . import (
    ask_async as _ask_async_impl,
    ask_sync as _ask_sync_impl,
    check_session_health as _check_session_health_impl,
    ensure_log_reader as _ensure_log_reader_impl,
    initialize_state as _initialize_comm_state,
    ping as _ping_impl,
    prime_log_binding as _prime_log_binding_impl,
    publish_claude_registry,
    publish_registry as _publish_registry_impl,
    remember_claude_session as _remember_claude_session_impl,
    remember_claude_session_binding,
)


_comm_logger = get_comm_logger("claude.comm")


def _claude_log_reader_cls():
    from .. import comm as claude_comm_module

    return claude_comm_module.ClaudeLogReader


class ClaudeCommunicator:
    """Communicate with Claude via terminal and read replies from session logs."""

    def __init__(self, lazy_init: bool = False):
        _initialize_comm_state(
            self,
            get_backend_for_session_fn=get_backend_for_session,
            get_pane_id_from_session_fn=get_pane_id_from_session,
        )

        self._publish_registry()

        if not lazy_init:
            self._ensure_log_reader()
            healthy, msg = self._check_session_health()
            if not healthy:
                raise RuntimeError(
                    "❌ Session unhealthy: "
                    f"{msg}\nHint: run ccb claude (or add claude to ccb.config) to start a new session"
                )

    @property
    def log_reader(self):
        if self._log_reader is None:
            self._ensure_log_reader()
        return self._log_reader

    def _ensure_log_reader(self) -> None:
        _ensure_log_reader_impl(self, log_reader_cls=_claude_log_reader_cls())

    def _load_session_info(self) -> dict | None:
        work_dir = Path.cwd()
        resolution = resolve_claude_session(work_dir)
        if not resolution:
            return None
        data = dict(resolution.data or {})
        if not data:
            return None
        if data.get("active") is False:
            return None
        session_file = resolution.session_file
        if session_file:
            data["_session_file"] = str(session_file)
        data["work_dir"] = str(Path(data.get("work_dir") or work_dir))
        return data

    def _prime_log_binding(self) -> None:
        _prime_log_binding_impl(self)

    def _check_session_health(self) -> tuple[bool, str]:
        return self._check_session_health_impl(probe_terminal=True)

    def _check_session_health_impl(self, probe_terminal: bool) -> tuple[bool, str]:
        return _check_session_health_impl(self, probe_terminal=probe_terminal)

    def _send_via_terminal(self, content: str) -> bool:
        if not self.backend or not self.pane_id:
            raise RuntimeError("Terminal session not configured")
        try:
            self.backend.send_text(self.pane_id, content)
        except Exception as exc:
            log_comm_event(
                _comm_logger,
                provider="claude",
                direction="send",
                endpoint=str(self.pane_id),
                event="send_via_terminal_failed",
                error=exc,
            )
            raise
        return True

    def _remember_claude_session(self, session_path: Path) -> None:
        _remember_claude_session_impl(
            self,
            session_path,
            remember_claude_session_binding_fn=remember_claude_session_binding,
        )

    def _publish_registry(self) -> None:
        _publish_registry_impl(
            self,
            publish_claude_registry_fn=publish_claude_registry,
        )

    def ask_async(self, question: str) -> bool:
        return _ask_async_impl(self, question)

    def ask_sync(self, question: str, timeout: int | None = None) -> str | None:
        return _ask_sync_impl(
            self,
            question,
            timeout=timeout,
            req_id_factory=make_req_id,
            wrap_prompt_fn=wrap_claude_prompt,
            is_done_text_fn=is_done_text,
            strip_done_text_fn=strip_done_text,
        )

    def ping(self, display: bool = True) -> tuple[bool, str]:
        return _ping_impl(self, display=display)


__all__ = ["ClaudeCommunicator"]
