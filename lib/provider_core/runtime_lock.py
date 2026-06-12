"""
Per-provider, per-scope file lock to serialize runtime request-response cycles.
"""
from __future__ import annotations

import hashlib
import os
import time
import tempfile
from pathlib import Path
from typing import Optional

from .platform_info import is_windows

from project.runtime_paths import project_anchor_exists, project_lock_dir


def _is_pid_alive(pid: int) -> bool:
    if is_windows():
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32
            synchronize = 0x00100000
            handle = kernel32.OpenProcess(synchronize, False, pid)
            if handle:
                kernel32.CloseHandle(handle)
                return True
            return False
        except Exception:
            return True
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class ProviderLock:
    """Per-provider, per-scope file lock."""

    def __init__(self, provider: str, timeout: float = 60.0, cwd: str | None = None):
        self.provider = provider
        self.timeout = timeout
        scope = cwd if cwd is not None else os.getcwd()
        self.lock_dir = _lock_dir_for_scope(scope)

        cwd_hash = hashlib.md5(scope.encode()).hexdigest()[:8]
        self.lock_file = self.lock_dir / f"{provider}-{cwd_hash}.lock"
        self._fd: Optional[int] = None
        self._acquired = False

    def _try_acquire_once(self) -> bool:
        try:
            if is_windows():
                import msvcrt

                try:
                    st = os.fstat(self._fd)
                    if getattr(st, "st_size", 0) < 1:
                        os.lseek(self._fd, 0, os.SEEK_SET)
                        os.write(self._fd, b"\0")
                except Exception:
                    pass
                msvcrt.locking(self._fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)

            pid_bytes = f"{os.getpid()}\n".encode()
            os.lseek(self._fd, 0, os.SEEK_SET)
            os.write(self._fd, pid_bytes)
            if is_windows():
                try:
                    os.ftruncate(self._fd, max(1, len(pid_bytes)))
                except Exception:
                    pass
            else:
                os.ftruncate(self._fd, len(pid_bytes))
            self._acquired = True
            return True
        except (OSError, IOError):
            return False

    def _check_stale_lock(self) -> bool:
        try:
            with open(self.lock_file, "r", encoding="utf-8") as f:
                content = f.read().strip()
                if content:
                    pid = int(content)
                    if not _is_pid_alive(pid):
                        try:
                            self.lock_file.unlink()
                        except OSError:
                            pass
                        return True
        except (OSError, ValueError):
            pass
        return False

    def try_acquire(self) -> bool:
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(str(self.lock_file), os.O_CREAT | os.O_RDWR)

        if self._try_acquire_once():
            return True

        if self._check_stale_lock():
            os.close(self._fd)
            self._fd = os.open(str(self.lock_file), os.O_CREAT | os.O_RDWR)
            if self._try_acquire_once():
                return True

        os.close(self._fd)
        self._fd = None
        return False

    def acquire(self) -> bool:
        self.lock_dir.mkdir(parents=True, exist_ok=True)
        self._fd = os.open(str(self.lock_file), os.O_CREAT | os.O_RDWR)

        deadline = time.time() + self.timeout
        stale_checked = False

        while time.time() < deadline:
            if self._try_acquire_once():
                return True

            if not stale_checked:
                stale_checked = True
                if self._check_stale_lock():
                    os.close(self._fd)
                    self._fd = os.open(str(self.lock_file), os.O_CREAT | os.O_RDWR)
                    if self._try_acquire_once():
                        return True

            time.sleep(0.1)

        if self._fd is not None:
            os.close(self._fd)
            self._fd = None
        return False

    def release(self) -> None:
        if self._fd is not None:
            try:
                if self._acquired:
                    if is_windows():
                        import msvcrt

                        try:
                            msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
                        except OSError:
                            pass
                    else:
                        import fcntl

                        try:
                            fcntl.flock(self._fd, fcntl.LOCK_UN)
                        except OSError:
                            pass
            finally:
                try:
                    os.close(self._fd)
                except OSError:
                    pass
                self._fd = None
                self._acquired = False

    def __enter__(self) -> "ProviderLock":
        if not self.acquire():
            raise TimeoutError(f"Failed to acquire {self.provider} lock after {self.timeout}s")
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        self.release()


def _lock_dir_for_scope(scope: str) -> Path:
    if project_anchor_exists(scope):
        return project_lock_dir(scope)
    runtime_root = os.environ.get('XDG_RUNTIME_DIR') or tempfile.gettempdir()
    return Path(runtime_root).expanduser() / 'ccb-runtime' / 'locks'
