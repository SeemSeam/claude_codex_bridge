from __future__ import annotations

from typing import Any
import time

from opencode_runtime.storage import OpenCodeObservationUnavailable

from opencode_runtime.replies import extract_text as _extract_text

from ..polling import conversations_for_session as _conversations_for_session_runtime
from ..polling import latest_conversations as _latest_conversations_runtime
from ..polling import latest_message as _latest_message_runtime
from ..polling import read_since as _read_since_runtime
from ..reader_support import detect_project_id_for_workdir as _detect_project_id_for_workdir
from ..state_capture import capture_state as _capture_state
from ..storage_reader import get_latest_session as _get_latest_session
from ..storage_reader import get_latest_session_from_db as _get_latest_session_from_db
from ..storage_reader import get_latest_session_from_files as _get_latest_session_from_files
from ..storage_reader import read_messages as _read_messages_runtime
from ..storage_reader import read_parts as _read_parts_runtime


class OpenCodeTimelineMixin:
    def _detect_project_id_for_workdir(self) -> str | None:
        return _detect_project_id_for_workdir(
            storage_root=self.root,
            work_dir=self.work_dir,
            load_json_fn=self._load_json,
            allow_parent_match=self._allow_parent_match,
        )

    def _get_latest_session(self) -> dict | None:
        return _get_latest_session(self)

    def _get_latest_session_from_db(self) -> dict | None:
        return _get_latest_session_from_db(self)

    def _get_latest_session_from_files(self) -> dict | None:
        return _get_latest_session_from_files(self)

    def _read_messages(self, session_id: str) -> list[dict]:
        return _read_messages_runtime(self, session_id)

    def _read_parts(self, message_id: str) -> list[dict]:
        return _read_parts_runtime(self, message_id)

    @staticmethod
    def _extract_text(parts: list[dict], allow_reasoning_fallback: bool = True) -> str:
        return _extract_text(parts, allow_reasoning_fallback=allow_reasoning_fallback)

    def capture_state(self) -> dict[str, Any]:
        with self._storage.observation():
            return _capture_state(self)

    def _read_since(self, state: dict[str, Any], timeout: float, block: bool) -> tuple[str | None, dict[str, Any]]:
        now = time.monotonic()
        if now < getattr(self, '_observation_retry_at', 0):
            return None, state
        try:
            signature = self._storage.database_signature() if self._native is None else None
            if (not block and signature is not None and not state.get('observation_error')
                    and signature == getattr(self, '_last_database_signature', None)
                    and now - getattr(self, '_last_observation_at', float('-inf')) < self._force_read_interval):
                return None, state
            reply, updated = _read_since_runtime(self, state, timeout, block)
        except OpenCodeObservationUnavailable as exc:
            self._observation_retry_at = time.monotonic() + self._force_read_interval
            # Preserve the last successful cursor and emitted assistant baseline.
            return None, {**state, 'observation_error': exc.reason}
        # Retain only metadata, not a stale snapshot. Force revalidation at least
        # once per interval even if a filesystem coalesces timestamp updates.
        self._last_database_signature = signature
        self._last_observation_at = now
        updated = dict(updated)
        updated.pop('observation_error', None)
        return reply, updated

    def wait_for_message(self, state: dict[str, Any], timeout: float) -> tuple[str | None, dict[str, Any]]:
        return self._read_since(state, timeout, block=True)

    def try_get_message(self, state: dict[str, Any]) -> tuple[str | None, dict[str, Any]]:
        return self._read_since(state, timeout=0.0, block=False)

    def latest_message(self) -> str | None:
        return _latest_message_runtime(self)

    def conversations_for_session(self, session_id: str, n: int = 1) -> list[tuple[str, str]]:
        return _conversations_for_session_runtime(self, session_id, n=n)

    def latest_conversations(self, n: int = 1) -> list[tuple[str, str]]:
        return _latest_conversations_runtime(self, n=n)


__all__ = ['OpenCodeTimelineMixin']
