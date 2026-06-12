from __future__ import annotations

import os
import platform
import shutil
import subprocess
from pathlib import Path
import re

from provider_core.platform_info import is_windows as _platform_is_windows


def env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return max(0.0, value)


def env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def sanitize_filename(value: str) -> str:
    text = (value or "").strip()
    if not text:
        return ""
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("_")


def is_windows() -> bool:
    return _platform_is_windows()


def subprocess_kwargs() -> dict:
    if is_windows():
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        return {"creationflags": flags}
    return {}


def isolated_tmux_env(env: dict[str, str] | None = None) -> dict[str, str]:
    isolated = tmux_compatible_env(env)
    for key in (
        "TMUX",
        "TMUX_PANE",
        "CCB_TMUX_SOCKET",
        "CCB_TMUX_SOCKET_PATH",
    ):
        isolated.pop(key, None)
    return isolated


def tmux_compatible_env(env: dict[str, str] | None = None) -> dict[str, str]:
    compatible = dict(os.environ if env is None else env)
    term = str(compatible.get("TERM") or "").strip().lower()
    if term == "xterm-ghostty":
        compatible["TERM"] = "xterm-256color"
    return compatible


def is_wsl() -> bool:
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except Exception:
        return False

def default_shell(*, is_wsl_fn, is_windows_fn) -> tuple[str, str]:
    if is_wsl_fn():
        return "bash", "-c"
    if is_windows_fn():
        for shell in ["pwsh", "powershell"]:
            if shutil.which(shell):
                return shell, "-Command"
        return "powershell", "-Command"
    return "bash", "-c"
