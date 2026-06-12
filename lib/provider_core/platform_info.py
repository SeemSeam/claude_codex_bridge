"""Single home for platform predicates used on the comm path (phase 3.2)."""

from __future__ import annotations

import functools
import os
import sys


def is_windows() -> bool:
    return os.name == "nt"


def is_macos() -> bool:
    return sys.platform == "darwin"


@functools.cache
def is_wsl() -> bool:
    if "WSL_DISTRO_NAME" in os.environ:
        return True
    try:
        with open("/proc/version", encoding="utf-8") as handle:
            return "microsoft" in handle.read().lower()
    except OSError:
        return False


__all__ = ["is_macos", "is_windows", "is_wsl"]
