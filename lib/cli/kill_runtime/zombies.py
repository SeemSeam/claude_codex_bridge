from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import Callable

_ZOMBIE_SESSION_PATTERN = re.compile(r"^(codex|gemini|opencode|claude|droid|agy|kimi|deepseek)-(\d+)-")


def find_all_zombie_sessions(
    *,
    is_pid_alive: Callable[[int], bool],
    list_tmux_sessions_fn: Callable[[], list[str]] | None = None,
) -> list[dict]:
    if list_tmux_sessions_fn is None:
        list_tmux_sessions_fn = _list_tmux_sessions
    session_names = list_tmux_sessions_fn()
    if not session_names:
        return []
    zombies: list[dict] = []
    for session in session_names:
        zombie = _parse_zombie_session(session, is_pid_alive=is_pid_alive)
        if zombie is not None:
            zombies.append(zombie)
    return zombies


def _list_tmux_sessions() -> list[str]:
    if os.name == "nt" or not shutil.which("tmux"):
        return []
    try:
        result = subprocess.run(
            ["tmux", "list-sessions", "-F", "#{session_name}"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return []
    if result.returncode != 0:
        return []
    return [session for session in result.stdout.strip().split("\n") if session]


def _parse_zombie_session(session: str, *, is_pid_alive: Callable[[int], bool]) -> dict | None:
    match = _ZOMBIE_SESSION_PATTERN.match(session)
    if match is None:
        return None
    provider, parent_pid_text = match.groups()
    try:
        parent_pid = int(parent_pid_text)
    except ValueError:
        return None
    if is_pid_alive(parent_pid):
        return None
    return {
        "session": session,
        "provider": provider,
        "parent_pid": parent_pid,
    }


def kill_global_zombies(
    *,
    yes: bool,
    is_pid_alive: Callable[[int], bool],
    find_all_zombie_sessions_fn: Callable[..., list[dict]],
    input_fn: Callable[[str], str] = input,
    kill_tmux_session_fn: Callable[[str], bool] | None = None,
) -> int:
    if kill_tmux_session_fn is None:
        kill_tmux_session_fn = _kill_tmux_session
    zombies = find_all_zombie_sessions_fn(is_pid_alive=is_pid_alive)
    if not zombies:
        print("✅ No zombie sessions found")
        return 0

    _print_zombie_sessions(zombies)

    if not _confirm_cleanup(yes=yes, input_fn=input_fn):
        return 1

    killed, failed = _cleanup_zombie_sessions(
        zombies,
        kill_tmux_session_fn=kill_tmux_session_fn,
    )
    _print_cleanup_result(killed=killed, failed=failed)
    return 0


def _print_zombie_sessions(zombies: list[dict]) -> None:
    print(f"Found {len(zombies)} zombie session(s):")
    for zombie in zombies:
        print(f"  - {zombie['session']} (parent PID {zombie['parent_pid']} exited)")


def _confirm_cleanup(*, yes: bool, input_fn: Callable[[str], str]) -> bool:
    if yes:
        return True
    try:
        reply = input_fn("\nClean up these sessions? [y/N] ")
    except (EOFError, KeyboardInterrupt):
        print("\n❌ Cancelled")
        return False
    if reply.lower() == "y":
        return True
    print("❌ Cancelled")
    return False


def _cleanup_zombie_sessions(
    zombies: list[dict],
    *,
    kill_tmux_session_fn: Callable[[str], bool],
) -> tuple[int, int]:
    killed = 0
    failed = 0
    for zombie in zombies:
        if kill_tmux_session_fn(zombie["session"]):
            killed += 1
        else:
            failed += 1
    return killed, failed


def _kill_tmux_session(session_name: str) -> bool:
    try:
        result = subprocess.run(
            ["tmux", "kill-session", "-t", session_name],
            capture_output=True,
            timeout=5,
        )
    except Exception:
        return False
    return result.returncode == 0


def _print_cleanup_result(*, killed: int, failed: int) -> None:
    if failed > 0:
        print(f"✅ Cleaned up {killed} zombie session(s), {failed} failed")
        return
    print(f"✅ Cleaned up {killed} zombie session(s)")


__all__ = ["find_all_zombie_sessions", "kill_global_zombies"]
