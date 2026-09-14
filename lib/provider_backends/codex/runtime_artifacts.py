from __future__ import annotations

import json
import os
from dataclasses import dataclass
import hashlib
from pathlib import Path

from provider_core.runtime_lock import ProviderLock
from storage.path_helpers import SocketPlacement, choose_socket_placement


@dataclass(frozen=True)
class CodexRuntimeArtifacts:
    runtime_dir: Path
    input_fifo: Path
    output_fifo: Path
    completion_dir: Path
    history_dir: Path
    history_file: Path
    bridge_log: Path
    bridge_stdout_log: Path
    bridge_stderr_log: Path
    bridge_pid: Path
    codex_pid: Path
    app_server_socket_placement: SocketPlacement
    app_server_socket: Path
    app_server_pid: Path
    app_server_stdout_log: Path
    app_server_stderr_log: Path
    app_server_remote_marker: Path


def codex_runtime_artifact_layout(runtime_dir: Path) -> CodexRuntimeArtifacts:
    runtime_dir = Path(runtime_dir)
    socket_placement = codex_app_server_socket_placement(runtime_dir)
    return CodexRuntimeArtifacts(
        runtime_dir=runtime_dir,
        input_fifo=runtime_dir / 'input.fifo',
        output_fifo=runtime_dir / 'output.fifo',
        completion_dir=runtime_dir / 'completion',
        history_dir=runtime_dir / 'history',
        history_file=runtime_dir / 'history' / 'session.jsonl',
        bridge_log=runtime_dir / 'bridge.log',
        bridge_stdout_log=runtime_dir / 'bridge.stdout.log',
        bridge_stderr_log=runtime_dir / 'bridge.stderr.log',
        bridge_pid=runtime_dir / 'bridge.pid',
        codex_pid=runtime_dir / 'codex.pid',
        app_server_socket_placement=socket_placement,
        app_server_socket=socket_placement.effective_path,
        app_server_pid=runtime_dir / 'app-server.pid',
        app_server_stdout_log=runtime_dir / 'app-server.stdout.log',
        app_server_stderr_log=runtime_dir / 'app-server.stderr.log',
        app_server_remote_marker=runtime_dir / 'app-server.remote',
    )


def codex_app_server_socket_placement(runtime_dir: Path) -> SocketPlacement:
    runtime_path = Path(runtime_dir).expanduser()
    socket_key = hashlib.sha256(str(runtime_path.absolute()).encode('utf-8')).hexdigest()[:16]
    return choose_socket_placement(
        preferred_path=runtime_path / 'app-server.sock',
        project_socket_key=socket_key,
        preferred_root_kind='runtime',
    )


def ensure_runtime_artifact_layout(runtime_dir: Path) -> CodexRuntimeArtifacts:
    artifacts = codex_runtime_artifact_layout(runtime_dir)
    artifacts.runtime_dir.mkdir(parents=True, exist_ok=True)
    artifacts.completion_dir.mkdir(parents=True, exist_ok=True)
    artifacts.history_dir.mkdir(parents=True, exist_ok=True)
    _touch_file(artifacts.bridge_log)
    return artifacts

class GenerationLock:
    """Cross-process mutex serializing app-server generation transitions.

    Held across the whole start claim protocol and across stop/shutdown
    cleanup so overlapping generations cannot race check-then-act on the
    shared socket/PID/marker set. Acquisition is bounded: a holder that
    never releases makes later attempts fail fast (local fallback) rather
    than block forever.

    The primitive itself is the shared cross-platform ProviderLock, whose
    per-platform lock semantics are already review-proven, so this module
    keeps no platform-specific imports of its own.
    """

    def __init__(self, runtime_dir: Path, *, timeout_s: float = 5.0) -> None:
        self._runtime_dir = Path(runtime_dir)
        self._timeout_s = max(0.0, timeout_s)
        self._lock: ProviderLock | None = None

    def acquire(self) -> bool:
        lock = ProviderLock(
            'codex-app-server-generation',
            timeout=self._timeout_s,
            cwd=str(self._runtime_dir),
        )
        if not lock.acquire():
            self._lock = None
            return False
        self._lock = lock
        return True

    def release(self) -> None:
        lock = self._lock
        self._lock = None
        if lock is None:
            return
        lock.release()

    def __enter__(self) -> bool:
        return self.acquire()

    def __exit__(self, *exc_info) -> None:
        self.release()


def cleanup_codex_app_server_shutdown_artifacts(runtime_dir: Path) -> tuple[Path, ...]:
    """Remove exact app-server authority artifacts after provider processes stop.

    The artifacts form one generation-owned set (socket, PID registry, remote
    marker). A live successor that re-registered while this stop flow ran
    owns the whole set: neither its PID record nor its marker may be deleted
    while leaving its socket bound. The removal runs under the same
    generation lock as supervisor start/stop so concurrent transitions are
    serialized (#345).
    """
    lock = GenerationLock(runtime_dir)
    if not lock.acquire():
        # A concurrent generation transition holds the lock; its owner
        # records are authoritative for this moment. Skip removal rather
        # than racing it.
        return ()
    try:
        artifacts = codex_runtime_artifact_layout(runtime_dir)
        removed: list[Path] = []
        owner_pid = _read_recorded_app_server_pid(artifacts.app_server_pid)
        if owner_pid is not None and _pid_alive(owner_pid):
            # A live generation owns the entire artifact set; keep all of it.
            return tuple(removed)
        for path in (artifacts.app_server_pid, artifacts.app_server_remote_marker):
            try:
                path.unlink()
            except FileNotFoundError:
                continue
            except OSError:
                continue
            removed.append(path)
        try:
            if artifacts.app_server_socket.is_socket():
                artifacts.app_server_socket.unlink()
                removed.append(artifacts.app_server_socket)
        except (FileNotFoundError, OSError):
            pass
        return tuple(removed)
    finally:
        lock.release()


def _read_recorded_app_server_pid(pid_path: Path) -> int | None:
    raw = ''
    try:
        raw = pid_path.read_text(encoding='utf-8').strip()
    except OSError:
        return None
    if not raw:
        return None
    if raw.startswith('{'):
        try:
            decoded = json.loads(raw)
            if isinstance(decoded, dict):
                return int(decoded.get('pid') or 0) or None
        except (ValueError, TypeError):
            return None
        return None
    try:
        return int(raw.splitlines()[0].strip() or 0) or None
    except (ValueError, IndexError):
        return None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _touch_file(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a', encoding='utf-8'):
        pass


__all__ = [
    'CodexRuntimeArtifacts',
    'GenerationLock',
    'cleanup_codex_app_server_shutdown_artifacts',
    'codex_app_server_socket_placement',
    'codex_runtime_artifact_layout',
    'ensure_runtime_artifact_layout',
]
