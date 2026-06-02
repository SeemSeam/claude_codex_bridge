from __future__ import annotations

from pathlib import Path
from typing import Callable, Mapping

from .kill_runtime.daemons import terminate_provider_daemon as _terminate_provider_daemon_impl
from .kill_runtime.processes import kill_pid as _kill_pid_impl
from .kill_runtime.sessions import terminate_provider_session as _terminate_provider_session_impl
from .kill_runtime.zombies import (
    find_all_zombie_sessions as _find_all_zombie_sessions_impl,
    kill_global_zombies as _kill_global_zombies_impl,
)


def find_all_zombie_sessions(*, is_pid_alive: Callable[[int], bool]) -> list[dict]:
    return _find_all_zombie_sessions_impl(is_pid_alive=is_pid_alive)


def kill_global_zombies(*, yes: bool, is_pid_alive: Callable[[int], bool]) -> int:
    return _kill_global_zombies_impl(
        yes=yes,
        is_pid_alive=is_pid_alive,
        find_all_zombie_sessions_fn=find_all_zombie_sessions,
    )


def kill_pid(pid: int, *, force: bool = False) -> bool:
    return _kill_pid_impl(pid, force=force)


def cmd_kill(
    args,
    *,
    parse_providers: Callable[[list[str]], list[str]],
    cwd: Path,
    session_finder: Callable[[Path, str], Path | None],
    tmux_backend_factory: Callable[[], object],
    safe_write_session: Callable[[Path, str], tuple[bool, str | None]],
    state_file_path_fn: Callable[[str], Path],
    shutdown_daemon_fn: Callable[[str, float, Path], bool],
    read_state_fn: Callable[[Path], dict | None],
    specs_by_provider: Mapping[str, object],
    is_pid_alive: Callable[[int], bool],
) -> int:
    force = getattr(args, "force", False)
    if force:
        yes = getattr(args, "yes", False)
        return kill_global_zombies(yes=yes, is_pid_alive=is_pid_alive)

    providers = parse_providers(list(args.providers or ["codex", "gemini", "opencode", "claude", "droid", "agy"]))
    if not providers:
        return 2

    for provider in providers:
        _terminate_provider_session(
            provider,
            cwd=cwd,
            session_finder=session_finder,
            tmux_backend_factory=tmux_backend_factory,
            safe_write_session=safe_write_session,
        )
        _terminate_provider_daemon(
            provider,
            specs_by_provider=specs_by_provider,
            state_file_path_fn=state_file_path_fn,
            shutdown_daemon_fn=shutdown_daemon_fn,
            read_state_fn=read_state_fn,
        )
    return 0


def _terminate_provider_session(
    provider: str,
    *,
    cwd: Path,
    session_finder: Callable[[Path, str], Path | None],
    tmux_backend_factory: Callable[[], object],
    safe_write_session: Callable[[Path, str], tuple[bool, str | None]],
) -> None:
    _terminate_provider_session_impl(
        provider,
        cwd=cwd,
        session_finder=session_finder,
        tmux_backend_factory=tmux_backend_factory,
        safe_write_session=safe_write_session,
    )


def _terminate_provider_daemon(
    provider: str,
    *,
    specs_by_provider: Mapping[str, object],
    state_file_path_fn: Callable[[str], Path],
    shutdown_daemon_fn: Callable[[str, float, Path], bool],
    read_state_fn: Callable[[Path], dict | None],
) -> None:
    _terminate_provider_daemon_impl(
        provider,
        specs_by_provider=specs_by_provider,
        state_file_path_fn=state_file_path_fn,
        shutdown_daemon_fn=shutdown_daemon_fn,
        read_state_fn=read_state_fn,
        kill_pid_fn=kill_pid,
    )


__all__ = [
    "cmd_kill",
    "find_all_zombie_sessions",
    "kill_global_zombies",
    "kill_pid",
]
