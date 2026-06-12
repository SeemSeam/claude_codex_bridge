"""Behavior-parity tests for FifoTransport vs SpoolDirTransport (phase 3.1).

The same send/receive scenarios run against both transports so Windows (inbox
directory) and POSIX (FIFO) behave identically at the contract level.
"""

from __future__ import annotations

import json
import os
import threading

import pytest

from provider_core.fifo_delivery import CommDeliveryError
from provider_core.transport import (
    FifoTransport,
    SpoolDirTransport,
    create_transport,
    endpoint_for_fifo_path,
)

HAS_FIFO = hasattr(os, "mkfifo")

TRANSPORTS = ["spool"] + (["fifo"] if HAS_FIFO else [])


@pytest.fixture(params=TRANSPORTS)
def transport(request, tmp_path):
    if request.param == "fifo":
        path = tmp_path / "input.fifo"
        os.mkfifo(path, 0o600)
        t = FifoTransport(path)
        t.read_line(0.01)  # hold the FIFO read end open before any sender
    else:
        t = SpoolDirTransport(tmp_path / "inbox")
    yield t
    t.close()


def test_single_message_roundtrip(transport):
    transport.send_line('{"marker": "a", "content": "hi"}')
    line = transport.read_line(2.0)
    assert json.loads(line)["marker"] == "a"


def test_200_rapid_sends_no_loss_in_order(transport):
    total = 200
    received: list[str] = []

    def consume():
        misses = 0
        while len(received) < total and misses < 50:
            line = transport.read_line(1.0)
            if line is None:
                misses += 1
                continue
            misses = 0
            received.append(line)

    consumer = threading.Thread(target=consume)
    consumer.start()
    for i in range(total):
        transport.send_line(json.dumps({"marker": f"m-{i}"}))
    consumer.join(timeout=30)
    assert not consumer.is_alive()
    assert [json.loads(l)["marker"] for l in received] == [f"m-{i}" for i in range(total)]


def test_idle_read_times_out_quickly(transport):
    import time

    start = time.monotonic()
    assert transport.read_line(0.2) is None
    assert time.monotonic() - start < 1.0


def test_fifo_send_without_reader_raises():
    if not HAS_FIFO:
        pytest.skip("requires POSIX FIFOs")
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory() as d:
        path = Path(d) / "input.fifo"
        os.mkfifo(path, 0o600)
        with pytest.raises(CommDeliveryError):
            FifoTransport(path).send_line('{"marker": "lost"}')


def test_spool_send_never_needs_reader(tmp_path):
    t = SpoolDirTransport(tmp_path / "inbox")
    t.send_line('{"marker": "parked"}')
    assert json.loads(t.read_line(1.0))["marker"] == "parked"


def test_endpoint_mapping(tmp_path):
    fifo = tmp_path / "input.fifo"
    endpoint = endpoint_for_fifo_path(fifo)
    if HAS_FIFO:
        assert endpoint == fifo
    else:
        assert endpoint == tmp_path / "inbox"


def test_create_transport_picks_spool_for_directory(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    assert isinstance(create_transport(inbox), SpoolDirTransport)
