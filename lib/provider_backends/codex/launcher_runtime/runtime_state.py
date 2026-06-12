from __future__ import annotations

import stat
from pathlib import Path
import os

from provider_backends.codex.runtime_artifacts import ensure_runtime_artifact_layout


def prepare_runtime(runtime_dir: Path) -> dict[str, object]:
    artifacts = ensure_runtime_artifact_layout(runtime_dir)
    ensure_fifo(artifacts.input_fifo, 0o600)
    ensure_fifo(artifacts.output_fifo, 0o644)
    return {
        'input_fifo': artifacts.input_fifo,
        'output_fifo': artifacts.output_fifo,
        'completion_dir': artifacts.completion_dir,
        'bridge_log': artifacts.bridge_log,
    }


def ensure_fifo(path: Path, mode: int) -> None:
    if not hasattr(os, 'mkfifo'):
        # Windows has no FIFOs; the transport layer falls back to an inbox
        # directory next to the would-be FIFO (provider_core.transport.
        # SpoolDirTransport), so nothing must exist at the FIFO path itself.
        path.parent.joinpath('inbox').mkdir(parents=True, exist_ok=True)
        return
    if path.exists():
        if stat.S_ISFIFO(path.stat().st_mode):
            return
        raise RuntimeError(f'expected fifo at {path}')
    os.mkfifo(path, mode)


__all__ = ['prepare_runtime']
