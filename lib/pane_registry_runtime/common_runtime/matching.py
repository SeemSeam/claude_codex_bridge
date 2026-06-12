from __future__ import annotations

import os
from pathlib import Path

from provider_core.platform_info import is_windows


def normalize_path_for_match(value: str | Path | None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    try:
        path = Path(raw).expanduser()
        try:
            raw = str(path.resolve())
        except Exception:
            raw = str(path.absolute())
    except Exception:
        pass
    normalized = raw.replace("\\", "/").rstrip("/")
    if is_windows():
        normalized = normalized.lower()
    return normalized


def path_is_same_or_parent(parent: str | Path | None, child: str | Path | None) -> bool:
    normalized_parent = normalize_path_for_match(parent)
    normalized_child = normalize_path_for_match(child)
    if not normalized_parent or not normalized_child:
        return False
    if normalized_parent == normalized_child:
        return True
    if not normalized_child.startswith(normalized_parent):
        return False
    return normalized_child[len(normalized_parent):].startswith("/")


__all__ = ["normalize_path_for_match", "path_is_same_or_parent"]
