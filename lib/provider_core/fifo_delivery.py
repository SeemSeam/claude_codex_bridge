"""Reliable single-line writes to a FIFO (phase 1.2).

The previous sender used a blocking ``open(fifo, "w")`` with no timeout: it
could hang forever when no reader was listening, and a transient absence of
the reader lost the message outright. This module opens the FIFO
non-blocking, retries with backoff while the reader is briefly away, and
raises ``CommDeliveryError`` instead of failing silently.
"""

from __future__ import annotations

import errno
import os
import time
from pathlib import Path

# Writes up to PIPE_BUF bytes are atomic on a FIFO; POSIX guarantees >= 512,
# Linux/macOS use 4096+. Lines longer than this must go through a spool file.
PIPE_ATOMIC_LIMIT = 4096

_RETRY_BACKOFFS = (0.1, 0.3, 0.9)


class CommDeliveryError(RuntimeError):
    """The message could not be handed to the receiving end."""


def write_fifo_line(
    fifo_path: Path,
    line: str,
    *,
    backoffs: tuple[float, ...] = _RETRY_BACKOFFS,
) -> None:
    """Write one newline-terminated line to the FIFO, or raise CommDeliveryError.

    Retries while the FIFO has no reader (ENXIO); never blocks indefinitely.
    """
    data = line.encode("utf-8")
    if not data.endswith(b"\n"):
        data += b"\n"
    if len(data) > PIPE_ATOMIC_LIMIT:
        raise CommDeliveryError(
            f"line of {len(data)} bytes exceeds atomic FIFO write limit "
            f"({PIPE_ATOMIC_LIMIT}); spool the payload and send a pointer instead"
        )

    last_error: OSError | None = None
    attempts = len(backoffs) + 1
    for attempt in range(attempts):
        try:
            fd = os.open(str(fifo_path), os.O_WRONLY | os.O_NONBLOCK)
        except OSError as exc:
            if exc.errno == errno.ENXIO:  # no reader currently listening
                last_error = exc
                if attempt < len(backoffs):
                    time.sleep(backoffs[attempt])
                continue
            raise CommDeliveryError(f"cannot open {fifo_path}: {exc}") from exc
        try:
            written = os.write(fd, data)
            if written != len(data):
                raise CommDeliveryError(
                    f"partial FIFO write to {fifo_path}: {written}/{len(data)} bytes"
                )
            return
        except BlockingIOError as exc:
            last_error = exc
            if attempt < len(backoffs):
                time.sleep(backoffs[attempt])
            continue
        except OSError as exc:
            raise CommDeliveryError(f"write to {fifo_path} failed: {exc}") from exc
        finally:
            os.close(fd)
    raise CommDeliveryError(
        f"receiver not listening on {fifo_path} after {attempts} attempts"
    ) from last_error


def spool_payload(spool_dir: Path, marker: str, payload_json: str) -> Path:
    """Atomically persist an oversized payload; returns the spool file path."""
    spool_dir.mkdir(parents=True, exist_ok=True)
    target = spool_dir / f"{marker}.json"
    tmp = spool_dir / f"{marker}.json.tmp"
    tmp.write_text(payload_json, encoding="utf-8")
    os.replace(tmp, target)
    return target


__all__ = ["CommDeliveryError", "PIPE_ATOMIC_LIMIT", "spool_payload", "write_fifo_line"]
