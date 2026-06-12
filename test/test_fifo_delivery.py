"""Tests for non-blocking FIFO delivery with retry and spool (phase 1.2)."""

from __future__ import annotations

import json
import os
import time

import pytest

from provider_backends.codex.bridge_runtime.runtime_io import PersistentFifoReader
from provider_core.fifo_delivery import (
    CommDeliveryError,
    PIPE_ATOMIC_LIMIT,
    spool_payload,
    write_fifo_line,
)

pytestmark = pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires POSIX FIFOs")


@pytest.fixture()
def fifo_path(tmp_path):
    path = tmp_path / "input.fifo"
    os.mkfifo(path, 0o600)
    return path


def test_send_without_reader_raises_after_bounded_retries(fifo_path):
    start = time.monotonic()
    with pytest.raises(CommDeliveryError, match="not listening"):
        write_fifo_line(fifo_path, '{"marker": "lost"}')
    elapsed = time.monotonic() - start
    assert elapsed < 2.5, f"retries took {elapsed:.1f}s; backoff schedule broken"


def test_send_with_reader_succeeds(fifo_path):
    reader = PersistentFifoReader(fifo_path)
    try:
        reader.read_line(0.01)  # open the read end
        write_fifo_line(fifo_path, '{"marker": "ok"}')
        line = reader.read_line(2.0)
        assert json.loads(line)["marker"] == "ok"
    finally:
        reader.close()


def test_oversized_line_is_rejected_by_writer(fifo_path):
    big = '{"content": "' + "x" * PIPE_ATOMIC_LIMIT + '"}'
    with pytest.raises(CommDeliveryError, match="spool"):
        write_fifo_line(fifo_path, big)


def test_spool_roundtrip(tmp_path):
    payload = json.dumps({"marker": "m-big", "content": "y" * 8192})
    spool_file = spool_payload(tmp_path / "spool", "m-big", payload)
    assert spool_file.exists()
    assert json.loads(spool_file.read_text(encoding="utf-8"))["marker"] == "m-big"


def test_large_message_via_spool_pointer_reaches_reader(fifo_path, tmp_path):
    reader = PersistentFifoReader(fifo_path)
    try:
        reader.read_line(0.01)
        body = json.dumps({"marker": "m-big", "content": "z" * 8192})
        spool_file = spool_payload(tmp_path / "spool", "m-big", body)
        write_fifo_line(fifo_path, json.dumps({"marker": "m-big", "spool": str(spool_file)}))
        line = reader.read_line(2.0)
        pointer = json.loads(line)
        assert pointer["spool"] == str(spool_file)
    finally:
        reader.close()
