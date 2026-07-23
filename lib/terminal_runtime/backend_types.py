from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional


class TerminalBackend(ABC):
    @abstractmethod
    def create_pane(
        self,
        cmd: str,
        cwd: str,
        direction: str = 'right',
        percent: int = 50,
        parent_pane: Optional[str] = None,
    ) -> str: ...

    @abstractmethod
    def split_pane(
        self,
        parent_pane_id: str,
        direction: str,
        percent: int,
        cmd: str | None = None,
        cwd: str | None = None,
    ) -> str: ...

    @abstractmethod
    def respawn_pane(
        self,
        pane_id: str,
        *,
        cmd: str,
        cwd: str | None = None,
        stderr_log_path: str | None = None,
        remain_on_exit: bool = True,
    ) -> None: ...

    @abstractmethod
    def send_text(self, pane_id: str, text: str) -> None: ...

    @abstractmethod
    def send_key(self, pane_id: str, key: str) -> bool: ...

    @abstractmethod
    def activate(self, pane_id: str) -> None: ...

    @abstractmethod
    def kill_pane(self, pane_id: str) -> None: ...

    @abstractmethod
    def is_alive(self, pane_id: str) -> bool: ...

    @abstractmethod
    def pane_exists(self, pane_id: str) -> bool: ...

    @abstractmethod
    def get_current_pane_id(self) -> str: ...

    @abstractmethod
    def find_pane_by_title_marker(self, marker: str) -> Optional[str]: ...

    @abstractmethod
    def find_pane_by_user_options(self, expected: dict[str, str]) -> Optional[str]: ...

    @abstractmethod
    def list_panes_by_user_options(self, expected: dict[str, str]) -> list[str]: ...

    @abstractmethod
    def describe_pane(
        self,
        pane_id: str,
        *,
        user_options: tuple[str, ...] = (),
    ) -> dict[str, str] | None: ...

    @abstractmethod
    def set_pane_title(self, pane_id: str, title: str) -> None: ...

    @abstractmethod
    def set_pane_user_option(self, pane_id: str, name: str, value: str) -> None: ...

    def set_pane_style(
        self,
        pane_id: str,
        *,
        border_style: str | None = None,
        active_border_style: str | None = None,
    ) -> None:
        pass

    @abstractmethod
    def set_pane_identity(
        self,
        pane_id: str,
        *,
        title: str,
        user_options: dict[str, str],
        border_style: str | None = None,
        active_border_style: str | None = None,
    ) -> None: ...

    @abstractmethod
    def get_pane_content(self, pane_id: str, lines: int = 20) -> Optional[str]: ...

    @abstractmethod
    def get_text(self, pane_id: str, lines: int = 20) -> Optional[str]: ...

    @abstractmethod
    def pane_log_path(self, pane_id: str) -> Optional[Path]: ...

    @abstractmethod
    def ensure_pane_log(self, pane_id: str) -> Optional[Path]: ...

    def refresh_pane_logs(self) -> None:
        pass

    def save_crash_log(
        self,
        pane_id: str,
        crash_log_path: str,
        *,
        lines: int = 1000,
    ) -> None:
        pass
