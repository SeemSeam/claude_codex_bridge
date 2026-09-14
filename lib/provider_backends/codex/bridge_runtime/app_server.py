from __future__ import annotations

import json
import os
import socket as socket_module
from pathlib import Path
import subprocess
import time
import uuid

from provider_backends.codex.runtime_artifacts import GenerationLock, codex_runtime_artifact_layout


class ManagedCodexAppServer:
    """Supervises one generation of the managed Codex app-server.

    Artifact cleanup is fenced to the owning generation: socket, PID and
    marker files are shared pathnames across generations, so a supervisor
    only removes them when the recorded owner is itself (or a dead previous
    owner). Startup readiness additionally requires the socket node to accept
    a real connection, so a leftover node from a previous generation can
    never be mistaken for the new endpoint (#345).
    """

    def __init__(self, runtime_dir: Path) -> None:
        self._runtime_dir = Path(runtime_dir)
        self._process: subprocess.Popen | None = None
        self._generation = uuid.uuid4().hex

    def start(self) -> bool:
        command = _command_from_env()
        socket_path = _socket_from_env()
        artifacts = codex_runtime_artifact_layout(self._runtime_dir)
        if (
            not command
            or socket_path is None
            or not _same_path(socket_path, artifacts.app_server_socket)
        ):
            return False
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        # Serialize the whole claim protocol (wait/cleanup/spawn/readiness/
        # registry claim) across concurrent generations on this runtime dir.
        # Without the lock, two overlapping first starts could both observe
        # a fresh socket and a live child and claim the same endpoint (#345
        # review).
        lock = GenerationLock(self._runtime_dir)
        if not lock.acquire():
            return False
        try:
            return self._start_locked(artifacts, socket_path, command)
        finally:
            lock.release()

    def _start_locked(self, artifacts, socket_path, command) -> bool:
        # A live foreign generation still owns the endpoint during a restart
        # overlap. Wait for it to exit (bounded) instead of destroying its
        # socket or spawning a child that can only fail to bind (#345).
        if not self._wait_for_foreign_owner(artifacts):
            return False
        # Preserve the current PID registry until this generation proves
        # it owns the endpoint, so a failed start cannot clobber a live
        # foreign generation's claim (#345).
        previous_pid_record = _read_pid_file(artifacts.app_server_pid)
        self._cleanup_unowned_artifacts(artifacts)
        # After cleanup, a leftover node can only belong to a foreign
        # generation we deliberately preserved; anything our own child
        # binds now is fresh and must be trusted even if the filesystem
        # reuses the previous inode number.
        inherited_inode = _socket_inode(socket_path)
        try:
            with artifacts.app_server_stdout_log.open('ab') as stdout_log, artifacts.app_server_stderr_log.open('ab') as stderr_log:
                self._process = subprocess.Popen(
                    command,
                    env=os.environ.copy(),
                    stdout=stdout_log,
                    stderr=stderr_log,
                )
        except (OSError, ValueError):
            self._process = None
            return False
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            if _socket_is_ready(socket_path, inherited_inode=inherited_inode):
                if self._process is not None and self._process.poll() is None:
                    # The endpoint answers with a fresh inode AND our own
                    # child is alive, so the connection cannot belong to a
                    # foreign generation that still holds the pathname.
                    # Only now may this generation claim the artifacts.
                    self._claim_registry(artifacts)
                    return True
            if self._process is None or self._process.poll() is not None:
                self._restore_pid_record(artifacts.app_server_pid, previous_pid_record)
                return False
            time.sleep(0.05)
        if (
            _socket_is_ready(socket_path, inherited_inode=inherited_inode)
            and self._process is not None
            and self._process.poll() is None
        ):
            self._claim_registry(artifacts)
            return True
        self._stop_process()
        with GenerationLock(self._runtime_dir) as locked:
            if locked:
                self._cleanup_owned_artifacts(artifacts)
                self._restore_pid_record(artifacts.app_server_pid, previous_pid_record)
        return False

    def stop(self) -> None:
        self._stop_process()
        artifacts = codex_runtime_artifact_layout(self._runtime_dir)
        lock = GenerationLock(self._runtime_dir)
        if not lock.acquire():
            return
        try:
            self._cleanup_owned_artifacts(artifacts)
        finally:
            lock.release()

    def _stop_process(self) -> None:
        process = self._process
        self._process = None
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1.0)

    def _claim_registry(self, artifacts) -> None:
        if self._process is None:
            return
        artifacts.app_server_pid.write_text(
            json.dumps({'pid': self._process.pid, 'generation': self._generation}) + '\n',
            encoding='utf-8',
        )

    @staticmethod
    def _restore_pid_record(pid_path: Path, previous: str | None) -> None:
        """Restore the pre-start PID registry on a failed start.

        A failed generation must not leave its own (dead) PID behind, nor
        delete a foreign live generation's record. A record that changed to a
        different live owner since our snapshot is a newer generation's
        claim and must survive untouched (#345 review).
        """
        try:
            if previous is None:
                return
            current = _read_pid_file(pid_path)
            if current == previous:
                return
            current_owner = _read_pid_owner(pid_path)
            if current_owner is not None and _pid_alive(current_owner.pid):
                # A live foreign generation claimed the registry after our
                # snapshot; never overwrite it.
                return
            pid_path.write_text(previous, encoding='utf-8')
        except OSError:
            return

    def _wait_for_foreign_owner(self, artifacts, *, timeout_s: float = 4.0) -> bool:
        """Bounded wait for a live foreign generation to release the socket.

        During a restart overlap the previous supervisor may still be
        winding down. Waiting briefly lets the new generation bind cleanly
        once the old endpoint goes away, instead of killing the old socket
        or failing forever. The wait is bounded; a foreign owner that never
        exits still yields a False start (local fallback), not an endless
        loop (#345).
        """
        deadline = time.monotonic() + max(0.0, timeout_s)
        while True:
            owner = _read_pid_owner(artifacts.app_server_pid)
            if owner is None or not _pid_alive(owner.pid):
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)

    def _cleanup_owned_artifacts(self, artifacts) -> None:
        """Remove artifacts only when this generation (or a dead owner) owns them."""
        if not self._owns_artifacts(artifacts, allow_dead_owner=True):
            return
        _remove_stale_socket(artifacts.app_server_socket)
        _remove_runtime_file(artifacts.app_server_pid)
        _remove_runtime_file(artifacts.app_server_remote_marker)

    def _cleanup_unowned_artifacts(self, artifacts) -> None:
        """Pre-start cleanup: drop stale artifacts, keep a live owner's socket."""
        owner = _read_pid_owner(artifacts.app_server_pid)
        if owner is not None and _pid_alive(owner.pid):
            # Another generation is still running; keep its endpoint intact.
            return
        _remove_stale_socket(artifacts.app_server_socket)
        _remove_runtime_file(artifacts.app_server_remote_marker)
        _remove_runtime_file(artifacts.app_server_pid)

    def _owns_artifacts(self, artifacts, *, allow_dead_owner: bool) -> bool:
        owner = _read_pid_owner(artifacts.app_server_pid)
        if owner is None:
            # No recorded owner: only treat a dead socket node as ours.
            try:
                if not artifacts.app_server_socket.is_socket():
                    return True
                probe = socket_module.socket(socket_module.AF_UNIX)
                probe.settimeout(0.2)
                try:
                    probe.connect(str(artifacts.app_server_socket))
                finally:
                    probe.close()
                return False
            except OSError:
                return True
        if owner.generation == self._generation:
            return True
        if _pid_alive(owner.pid):
            return False
        return allow_dead_owner


def _read_pid_file(path: Path) -> str | None:
    try:
        return path.read_text(encoding='utf-8')
    except OSError:
        return None


def _read_pid_owner(path: Path) -> '_PidOwner | None':
    raw = ''
    try:
        raw = path.read_text(encoding='utf-8').strip()
    except (FileNotFoundError, NotADirectoryError, PermissionError, OSError):
        return None
    if not raw:
        return None
    pid: int | None = None
    generation = ''
    # Current format: JSON {"pid": ..., "generation": ...}
    if raw.startswith('{'):
        try:
            decoded = json.loads(raw)
            if isinstance(decoded, dict):
                pid = int(decoded.get('pid') or 0) or None
                generation = str(decoded.get('generation') or '')
        except (ValueError, TypeError):
            return None
    else:
        # Legacy format: bare pid line from an older release.
        try:
            pid = int(raw.splitlines()[0].strip() or 0) or None
        except (ValueError, IndexError):
            return None
    if pid is None:
        return None
    return _PidOwner(pid=pid, generation=generation)


class _PidOwner:
    def __init__(self, *, pid: int, generation: str) -> None:
        self.pid = pid
        self.generation = generation


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
def _socket_inode(path: Path) -> int | None:
    """Inode of a Unix socket node at [path], or None when absent/other type."""
    try:
        if path.is_socket():
            return path.stat().st_ino
    except OSError:
        return None
    return None


def _socket_is_ready(path: Path, *, inherited_inode: int | None) -> bool:
    """Readiness requires a live endpoint created after [inherited_inode].

    A leftover node from a dead generation exists but refuses connections,
    and a node owned by a live foreign generation keeps its inode because
    pre-start cleanup never unlinks it (#345).
    """
    try:
        if not path.is_socket():
            return False
        inode = path.stat().st_ino
        if inherited_inode is not None and inode == inherited_inode:
            return False
        probe = socket_module.socket(socket_module.AF_UNIX)
        probe.settimeout(0.2)
        try:
            probe.connect(str(path))
        finally:
            probe.close()
        return True
    except OSError:
        return False


def _command_from_env() -> list[str]:
    raw = str(os.environ.get('CCB_CODEX_APP_SERVER_COMMAND_JSON') or '').strip()
    if not raw:
        return []
    try:
        payload = json.loads(raw)
    except ValueError:
        return []
    if not isinstance(payload, list):
        return []
    return [str(item) for item in payload if str(item).strip()]


def _socket_from_env() -> Path | None:
    raw = str(os.environ.get('CCB_CODEX_APP_SERVER_SOCKET') or '').strip()
    return Path(raw) if raw else None



def _remove_stale_socket(path: Path) -> None:
    try:
        if path.is_socket():
            path.unlink()
            return
    except OSError:
        return


def _remove_runtime_file(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        return
    except OSError:
        return


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return False


__all__ = ['ManagedCodexAppServer']
