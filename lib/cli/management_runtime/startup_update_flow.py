from __future__ import annotations

import os
import platform
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import TextIO

from cli.models_start import ParsedStartCommand
from cli.parser import CliParser, CliUsageError

from .commands_runtime import cmd_update
from .install import find_install_dir
from .startup_update_refresh import schedule_background_update_refresh
from .startup_update_state import (
    defer_update_prompt,
    load_update_check_state,
    should_prompt_for_update,
    silence_update_version,
    startup_release_update_supported,
    update_check_state_is_stale,
)
from .versioning import get_version_info


def maybe_handle_startup_release_update(
    tokens: list[str],
    *,
    script_root: Path,
    cwd: Path,
    stdout: TextIO,
    stderr: TextIO,
    stdin,
    env: dict[str, str] | None = None,
    schedule_refresh_fn=None,
    update_fn=None,
    relaunch_fn=None,
) -> int | None:
    del stderr
    context = _startup_update_context(tokens, script_root=script_root, stdin=stdin, stdout=stdout)
    if context is None:
        return None
    install_dir, local_info = context
    state = load_update_check_state(install_dir)
    now = time.time()
    if _cache_needs_refresh(state, now=now):
        _schedule_refresh(schedule_refresh_fn, script_root=script_root, install_dir=install_dir)
        return None
    if not should_prompt_for_update(state, local_info=local_info, now=now):
        return None
    return _handle_prompted_update(
        state,
        local_info=local_info,
        install_dir=install_dir,
        tokens=tokens,
        script_root=script_root,
        cwd=cwd,
        stdout=stdout,
        stdin=stdin,
        env=env,
        update_fn=update_fn,
        relaunch_fn=relaunch_fn,
        now=now,
    )


def prompt_for_startup_update(
    state: dict[str, object],
    *,
    local_info: dict[str, object],
    stdout: TextIO,
    stdin,
) -> str:
    latest = str(state.get("latest_version") or "").strip()
    current = str(local_info.get("version") or "").strip()
    print(f"📦 Release update available: v{latest} (current v{current})", file=stdout)
    print("   [y] upgrade now  [Enter/n] continue  [s] silence this version", file=stdout)
    stdout.write("Upgrade now? [y/N/s]: ")
    stdout.flush()
    try:
        reply = str(stdin.readline() or "")
    except Exception:
        reply = ""
    answer = reply.strip().lower()
    if answer in {"y", "n", "s"}:
        return answer
    return ""


def relaunch_after_update(tokens: list[str], *, script_root: Path, cwd: Path, env: dict[str, str]) -> int:
    child_env = dict(env)
    child_env["CCB_SKIP_STARTUP_UPDATE_CHECK"] = "1"
    command = [sys.executable, str(Path(script_root) / "ccb.py"), *list(tokens)]
    return subprocess.run(command, cwd=str(cwd), env=child_env).returncode


def stream_is_tty(stream: object) -> bool:
    isatty = getattr(stream, "isatty", None)
    if not callable(isatty):
        return False
    try:
        return bool(isatty())
    except Exception:
        return False


def is_start_command(tokens: list[str]) -> bool:
    try:
        command = CliParser().parse(list(tokens))
    except CliUsageError:
        return False
    return isinstance(command, ParsedStartCommand)


def _startup_update_context(
    tokens: list[str], *, script_root: Path, stdin, stdout: TextIO
) -> tuple[Path, dict[str, object]] | None:
    if os.environ.get("CCB_SKIP_STARTUP_UPDATE_CHECK"):
        return None
    if not stream_is_tty(stdin) or not stream_is_tty(stdout):
        return None
    if not is_start_command(tokens):
        return None
    install_dir = find_install_dir(script_root)
    local_info = get_version_info(install_dir)
    if not startup_release_update_supported(local_info, platform_name=platform.system()):
        return None
    return install_dir, local_info


def _cache_needs_refresh(state: dict[str, object] | None, *, now: float) -> bool:
    return state is None or update_check_state_is_stale(state, now=now)


def _schedule_refresh(schedule_refresh_fn, *, script_root: Path, install_dir: Path) -> None:
    schedule_refresh = schedule_refresh_fn or schedule_background_update_refresh
    try:
        schedule_refresh(script_root=script_root, install_dir=install_dir)
    except Exception:
        pass


def _handle_prompted_update(
    state: dict[str, object],
    *,
    local_info: dict[str, object],
    install_dir: Path,
    tokens: list[str],
    script_root: Path,
    cwd: Path,
    stdout: TextIO,
    stdin,
    env: dict[str, str] | None,
    update_fn,
    relaunch_fn,
    now: float,
) -> int | None:
    choice = prompt_for_startup_update(state, local_info=local_info, stdout=stdout, stdin=stdin)
    if choice == "s":
        silence_update_version(install_dir, state)
        return None
    if choice != "y":
        defer_update_prompt(install_dir, state, now=now)
        return None
    return _update_and_relaunch(
        state,
        tokens=tokens,
        script_root=script_root,
        cwd=cwd,
        stdout=stdout,
        env=env,
        update_fn=update_fn,
        relaunch_fn=relaunch_fn,
    )


def _update_and_relaunch(
    state: dict[str, object],
    *,
    tokens: list[str],
    script_root: Path,
    cwd: Path,
    stdout: TextIO,
    env: dict[str, str] | None,
    update_fn,
    relaunch_fn,
) -> int | None:
    run_update = update_fn or cmd_update
    relaunch = relaunch_fn or relaunch_after_update
    print(f"🔄 Updating to v{state.get('latest_version')} before startup...", file=stdout)
    code = int(run_update(SimpleNamespace(target=None), script_root=script_root) or 0)
    if code != 0:
        print("⚠️  Update failed; continuing with current version.", file=stdout)
        return None
    return relaunch(tokens, script_root=script_root, cwd=cwd, env=dict(env or os.environ))


__all__ = [
    "is_start_command",
    "maybe_handle_startup_release_update",
    "prompt_for_startup_update",
    "relaunch_after_update",
    "stream_is_tty",
]
