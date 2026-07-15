from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from provider_backends.pane_log_support.session import PaneLogProjectSessionBase
from provider_profiles import load_resolved_provider_profile
from provider_profiles.codex_home_config import refresh_codex_auth_projection

from ..start_cmd import effective_start_cmd
from .binding import update_codex_log_binding as _update_codex_log_binding_impl


@dataclass
class CodexProjectSession(PaneLogProjectSessionBase):
    @property
    def codex_home(self) -> str:
        return str(self.data.get("codex_home") or "").strip()

    @property
    def codex_session_root(self) -> str:
        return str(self.data.get("codex_session_root") or "").strip()

    @property
    def codex_session_path(self) -> str:
        return str(self.data.get("codex_session_path") or "").strip()

    @property
    def codex_session_id(self) -> str:
        return str(self.data.get("codex_session_id") or "").strip()

    @property
    def start_cmd(self) -> str:
        return effective_start_cmd(self.data)

    def prepare_crash_recovery(self, reason: str) -> tuple[bool, str] | None:
        if reason != 'provider_auth_revoked':
            return None
        target_home = str(self.codex_home or '').strip()
        if not target_home:
            return False, 'Managed Codex home is unavailable; authentication recovery was blocked'
        result = refresh_codex_auth_projection(
            target_home,
            profile=load_resolved_provider_profile(self.runtime_dir),
        )
        return result.refreshed, result.detail

    def backend(self):
        from provider_backends.codex import session as session_module

        return session_module.get_backend_for_session(self.data)

    def update_codex_log_binding(
        self,
        *,
        log_path: str | None,
        session_id: str | None,
        post_write_validate: Callable[[], bool] | None = None,
    ) -> bool:
        return _update_codex_log_binding_impl(
            self,
            log_path=log_path,
            session_id=session_id,
            post_write_validate=post_write_validate,
        )


__all__ = ["CodexProjectSession"]
