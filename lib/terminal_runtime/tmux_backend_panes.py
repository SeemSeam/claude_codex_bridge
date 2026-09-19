from __future__ import annotations

from typing import Optional

from terminal_runtime.tmux import looks_like_pane_id as _looks_like_pane_id_impl
from terminal_runtime.tmux import looks_like_tmux_target as _looks_like_tmux_target_impl
from terminal_runtime.tmux_panes import TmuxPaneService
from terminal_runtime.tmux_backend_runtime import (
    activate_tmux_pane as _activate_tmux_pane_impl,
    create_pane as _create_pane_impl,
    ensure_not_in_copy_mode as _ensure_not_in_copy_mode_impl,
)


class TmuxBackendPaneQueryMixin:
    @staticmethod
    def _looks_like_pane_id(value: str) -> bool:
        return _looks_like_pane_id_impl(value)

    @staticmethod
    def _looks_like_tmux_target(value: str) -> bool:
        return _looks_like_tmux_target_impl(value)

    def _require_pane_id(self, pane_id: str, *, action: str) -> str:
        pane_id = (pane_id or '').strip()
        if not self._looks_like_pane_id(pane_id):
            raise ValueError(f'{action} requires tmux pane id, got {pane_id!r}')
        return pane_id

    def _pane_service(self) -> TmuxPaneService:
        return self._services.pane_service

    def pane_exists(self, pane_id: str) -> bool:
        return self._services.pane_service.pane_exists(pane_id)

    def get_current_pane_id(self) -> str:
        return self._services.pane_service.get_current_pane_id(
            env_pane=self._env_tmux_pane(),
        )

    def find_pane_by_title_marker(self, marker: str) -> Optional[str]:
        return self._services.pane_service.find_pane_by_title_marker(marker)

    def find_pane_by_user_options(self, expected: dict[str, str]) -> Optional[str]:
        return self._services.pane_service.find_pane_by_user_options(expected)

    def list_panes_by_user_options(self, expected: dict[str, str]) -> list[str]:
        return self._services.pane_service.list_panes_by_user_options(expected)

    def describe_pane(
        self,
        pane_id: str,
        *,
        user_options: tuple[str, ...] = (),
    ) -> dict[str, str] | None:
        return self._services.pane_service.describe_pane(
            pane_id,
            user_options=user_options,
        )

    def get_pane_content(self, pane_id: str, lines: int = 20) -> Optional[str]:
        return self._services.pane_service.get_pane_content(pane_id, lines=lines)

    def get_text(self, pane_id: str, lines: int = 20) -> Optional[str]:
        return self.get_pane_content(pane_id, lines=lines)

    def capture_screen(self, pane_id: str, *, history_lines: int = 0) -> Optional[str]:
        from .tmux_panes_runtime.queries_runtime.service import capture_screen

        return capture_screen(self._pane_service(), pane_id, history_lines=history_lines)

    def is_pane_alive(self, pane_id: str) -> bool:
        return self._services.pane_service.is_pane_alive(pane_id)


class TmuxBackendPaneMutationMixin:
    def split_pane(
        self,
        parent_pane_id: str,
        direction: str,
        percent: int,
        cmd: str | None = None,
        cwd: str | None = None,
    ) -> str:
        return self._services.pane_service.split_pane(
            parent_pane_id,
            direction=direction,
            percent=percent,
            cmd=cmd,
            cwd=cwd,
        )

    def set_pane_title(self, pane_id: str, title: str) -> None:
        self._services.pane_service.set_pane_title(pane_id, title)

    def set_pane_user_option(self, pane_id: str, name: str, value: str) -> None:
        self._services.pane_service.set_pane_user_option(pane_id, name, value)

    def set_pane_style(
        self,
        pane_id: str,
        *,
        border_style: str | None = None,
        active_border_style: str | None = None,
    ) -> None:
        self._services.pane_service.set_pane_style(
            pane_id,
            border_style=border_style,
            active_border_style=active_border_style,
        )

    def set_pane_identity(
        self,
        pane_id: str,
        *,
        title: str,
        user_options: dict[str, str],
        border_style: str | None = None,
        active_border_style: str | None = None,
    ) -> None:
        self._services.pane_service.set_pane_identity(
            pane_id,
            title=title,
            user_options=user_options,
            border_style=border_style,
            active_border_style=active_border_style,
        )

    def activate_tmux_pane(self, pane_id: str) -> None:
        _activate_tmux_pane_impl(self, pane_id)

    def _ensure_not_in_copy_mode(self, pane_id: str) -> None:
        _ensure_not_in_copy_mode_impl(self, pane_id)

    def create_pane(
        self,
        cmd: str,
        cwd: str,
        direction: str = 'right',
        percent: int = 50,
        parent_pane: Optional[str] = None,
    ) -> str:
        return _create_pane_impl(
            self,
            cmd=cmd,
            cwd=cwd,
            direction=direction,
            percent=percent,
            parent_pane=parent_pane,
        )


__all__ = [
    'TmuxBackendPaneMutationMixin',
    'TmuxBackendPaneQueryMixin',
]
