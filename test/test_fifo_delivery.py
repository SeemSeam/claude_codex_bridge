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
    ack_file_path,
    cleanup_acks,
    spool_payload,
    wait_for_ack,
    write_ack,
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


def test_ack_roundtrip(tmp_path):
    ack_dir = tmp_path / "acks"
    write_ack(ack_dir, "m-1")
    assert wait_for_ack(ack_dir, "m-1", timeout=1.0)
    # consumed: a second wait for the same marker must time out
    assert not wait_for_ack(ack_dir, "m-1", timeout=0.2)


def test_wait_for_ack_times_out_when_never_written(tmp_path):
    start = time.monotonic()
    assert not wait_for_ack(tmp_path / "acks", "m-none", timeout=0.3)
    assert time.monotonic() - start < 1.0


def test_ack_appearing_mid_wait_is_seen(tmp_path):
    import threading

    ack_dir = tmp_path / "acks"

    def late_ack():
        time.sleep(0.2)
        write_ack(ack_dir, "m-late")

    threading.Thread(target=late_ack).start()
    assert wait_for_ack(ack_dir, "m-late", timeout=3.0)


def test_cleanup_acks_removes_only_stale_files(tmp_path):
    ack_dir = tmp_path / "acks"
    write_ack(ack_dir, "m-old")
    write_ack(ack_dir, "m-new")
    old = ack_file_path(ack_dir, "m-old")
    os.utime(old, (time.time() - 100_000, time.time() - 100_000))
    cleanup_acks(ack_dir)
    assert not old.exists()
    assert ack_file_path(ack_dir, "m-new").exists()


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
