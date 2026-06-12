"""Regression tests for the persistent FIFO reader (phase 1.1).

The old bridge loop opened/read/closed the FIFO each iteration and slept
between reads, leaving windows with no reader where sender writes blocked or
failed silently. These tests assert the new reader never drops messages under
rapid sequential sends.
"""

from __future__ import annotations

import json
import os
import threading

import pytest

from provider_backends.codex.bridge_runtime.runtime_io import PersistentFifoReader

pytestmark = pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires POSIX FIFOs")


@pytest.fixture()
def fifo_path(tmp_path):
    path = tmp_path / "input.fifo"
    os.mkfifo(path, 0o600)
    return path


def _drain(reader: PersistentFifoReader, count: int, timeout_per_line: float = 2.0) -> list[str]:
    lines: list[str] = []
    misses = 0
    while len(lines) < count and misses < 50:
        line = reader.read_line(timeout_per_line)
        if line is None:
            misses += 1
            continue
        misses = 0
        lines.append(line)
    return lines


def test_rapid_sequential_sends_are_all_received_in_order(fifo_path):
    reader = PersistentFifoReader(fifo_path)
    total = 200
    received: list[str] = []

    def consume():
        received.extend(_drain(reader, total))

    consumer = threading.Thread(target=consume)
    consumer.start()
    try:
        # Mimic the sender: open/write/close per message, no pacing.
        for i in range(total):
            with open(fifo_path, "w", encoding="utf-8") as fifo:
                fifo.write(json.dumps({"marker": f"m-{i}", "content": f"msg {i}"}) + "\n")
    finally:
        consumer.join(timeout=30)
        reader.close()

    assert not consumer.is_alive(), "consumer thread hung"
    assert len(received) == total
    markers = [json.loads(line)["marker"] for line in received]
    assert markers == [f"m-{i}" for i in range(total)]


def test_multiple_messages_in_single_write_are_split(fifo_path):
    reader = PersistentFifoReader(fifo_path)
    try:
        reader.read_line(0.01)  # prime: hold the read end open before any writer
        with open(fifo_path, "w", encoding="utf-8") as fifo:
            fifo.write('{"marker": "a"}\n{"marker": "b"}\n')
        first = reader.read_line(2.0)
        second = reader.read_line(2.0)
        assert json.loads(first)["marker"] == "a"
        assert json.loads(second)["marker"] == "b"
    finally:
        reader.close()


def test_partial_line_is_buffered_until_newline(fifo_path):
    reader = PersistentFifoReader(fifo_path)
    try:
        reader.read_line(0.01)  # prime: hold the read end open before any writer
        fd = os.open(fifo_path, os.O_WRONLY)
        try:
            os.write(fd, b'{"marker": "par')
            assert reader.read_line(0.2) is None
            os.write(fd, b'tial"}\n')
            line = reader.read_line(2.0)
            assert json.loads(line)["marker"] == "partial"
        finally:
            os.close(fd)
    finally:
        reader.close()


def test_no_eof_storm_after_writer_disconnects(fifo_path):
    reader = PersistentFifoReader(fifo_path)
    try:
        reader.read_line(0.01)  # prime: hold the read end open before any writer
        with open(fifo_path, "w", encoding="utf-8") as fifo:
            fifo.write('{"marker": "x"}\n')
        assert json.loads(reader.read_line(2.0))["marker"] == "x"
        # Writer has disconnected; reader must idle quietly, not spin on EOF.
        assert reader.read_line(0.2) is None
        with open(fifo_path, "w", encoding="utf-8") as fifo:
            fifo.write('{"marker": "y"}\n')
        assert json.loads(reader.read_line(2.0))["marker"] == "y"
    finally:
        reader.close()


def test_missing_fifo_returns_none(tmp_path):
    reader = PersistentFifoReader(tmp_path / "absent.fifo")
    try:
        assert reader.read_line(0.05) is None
    finally:
        reader.close()
