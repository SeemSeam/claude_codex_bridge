"""Cross-platform message transport (phase 3.1).

POSIX systems use a FIFO (FifoTransport). Windows has no os.mkfifo, so it
uses an inbox directory of atomically-renamed one-message files consumed in
filename order (SpoolDirTransport). ``create_transport`` is the single place
in the codebase allowed to make that platform decision.

Both transports share the same contract:
- ``send_line`` delivers one JSON line or raises CommDeliveryError
- ``read_line`` waits up to a timeout for the next line, returning None on idle
"""

from __future__ import annotations

import itertools
import os
import time
from pathlib import Path

from .fifo_delivery import CommDeliveryError, write_fifo_line


class MessageTransport:
    """One-directional line transport between a sender and a single reader."""

    def send_line(self, line: str) -> None:
        raise NotImplementedError

    def read_line(self, timeout: float) -> str | None:
        raise NotImplementedError

    def close(self) -> None:  # reader-side resources only
        pass


class FifoTransport(MessageTransport):
    """POSIX named-pipe transport; reader holds the FIFO open persistently."""

    def __init__(self, fifo_path: Path):
        self._path = Path(fifo_path)
        self._reader = None  # lazy: only the receiving process needs it

    def send_line(self, line: str) -> None:
        write_fifo_line(self._path, line)

    def read_line(self, timeout: float) -> str | None:
        if self._reader is None:
            from provider_backends.codex.bridge_runtime.runtime_io import PersistentFifoReader

            self._reader = PersistentFifoReader(self._path)
        return self._reader.read_line(timeout)

    def close(self) -> None:
        if self._reader is not None:
            self._reader.close()
            self._reader = None


_msg_counter = itertools.count()


class SpoolDirTransport(MessageTransport):
    """Inbox-directory transport for platforms without FIFOs.

    Each message is one file, written to a .tmp name and atomically renamed
    into the inbox. Names sort by (monotonic-ish wall clock ns, pid, counter),
    so concurrent writers never collide and the reader consumes in order.
    """

    def __init__(self, inbox_dir: Path):
        self._inbox = Path(inbox_dir)

    def send_line(self, line: str) -> None:
        name = f"{time.time_ns():020d}-{os.getpid()}-{next(_msg_counter):06d}.msg"
        try:
            self._inbox.mkdir(parents=True, exist_ok=True)
            tmp = self._inbox / (name + ".tmp")
            tmp.write_text(line, encoding="utf-8")
            os.replace(tmp, self._inbox / name)
        except OSError as exc:
            raise CommDeliveryError(f"cannot write to inbox {self._inbox}: {exc}") from exc

    def read_line(self, timeout: float) -> str | None:
        deadline = time.monotonic() + timeout
        while True:
            entry = self._next_entry()
            if entry is not None:
                try:
                    line = entry.read_text(encoding="utf-8")
                except OSError:
                    line = None
                try:
                    entry.unlink()
                except OSError:
                    pass
                if line is not None:
                    return line
                continue
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            time.sleep(min(0.05, remaining))

    def _next_entry(self) -> Path | None:
        try:
            names = sorted(p for p in self._inbox.iterdir() if p.name.endswith(".msg"))
        except OSError:
            return None
        return names[0] if names else None


def endpoint_for_fifo_path(fifo_path: Path) -> Path:
    """Map a configured FIFO path to this platform's actual endpoint.

    POSIX: the FIFO itself. Windows: an ``inbox`` directory next to it.
    """
    fifo_path = Path(fifo_path)
    if hasattr(os, "mkfifo"):
        return fifo_path
    return fifo_path.parent / "inbox"


def create_transport(endpoint: Path) -> MessageTransport:
    """Sole platform-decision point: FIFO on POSIX, inbox dir on Windows."""
    endpoint = Path(endpoint)
    if hasattr(os, "mkfifo") and not endpoint.is_dir():
        return FifoTransport(endpoint)
    return SpoolDirTransport(endpoint)


__all__ = [
    "FifoTransport",
    "MessageTransport",
    "SpoolDirTransport",
    "create_transport",
    "endpoint_for_fifo_path",
]
