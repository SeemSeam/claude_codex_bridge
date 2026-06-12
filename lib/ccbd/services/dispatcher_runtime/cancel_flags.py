"""Filesystem cancel flags (phase 2.1).

Marking a job CANCELLED in the stores does not reach an agent that is already
mid-task in its pane. A flag file gives agents a cheap, transport-independent
way to notice cancellation between work steps: the dispatch prompt tells them
to check it and stop if present.
"""

from __future__ import annotations

import os
import time
from pathlib import Path


def cancel_flag_path(layout, agent_name: str, job_id: str) -> Path:
    return layout.agent_dir(agent_name) / "cancel_flags" / f"{job_id}.cancel"


def write_cancel_flag(layout, agent_name: str, job_id: str) -> Path | None:
    """Atomically drop the flag; best-effort, never raises."""
    try:
        target = cancel_flag_path(layout, agent_name, job_id)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".cancel.tmp")
        tmp.write_text(str(time.time()), encoding="utf-8")
        os.replace(tmp, target)
        return target
    except OSError:
        return None


def cleanup_cancel_flags(layout, agent_name: str, *, max_age_seconds: float = 86_400.0) -> None:
    """Drop stale flags so the directory does not grow; never raises."""
    try:
        flag_dir = layout.agent_dir(agent_name) / "cancel_flags"
        cutoff = time.time() - max_age_seconds
        for entry in flag_dir.glob("*.cancel"):
            try:
                if entry.stat().st_mtime < cutoff:
                    entry.unlink()
            except OSError:
                continue
    except OSError:
        pass


__all__ = ["cancel_flag_path", "cleanup_cancel_flags", "write_cancel_flag"]
