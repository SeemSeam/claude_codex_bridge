from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import unittest
from types import SimpleNamespace

from ccbd.desktop_api import (
    DESKTOP_PROTOCOL_VERSION,
    DesktopApiAdapter,
    DesktopApiError,
    build_discovery,
    redacted_display_root,
    validate_unix_endpoint_descriptor,
)
from ccbd.socket_server_runtime.loop import start_worker
from ccbd.socket_server_runtime.server import CcbdSocketServer


class _EventAuthority:
    def __init__(self, *, generation: int = 1, revision: int = 1):
        self.generation = generation
        self.revision = revision
        self.events: list[dict] = []

    def cursor(self):
        first = self.events[0]["seq"] if self.events else 1
        return {
            "server_generation": self.generation,
            "snapshot_revision": self.revision,
            "last_event_seq": self.events[-1]["seq"] if self.events else 0,
            "first_event_seq": first,
        }

    def read_since(self, after_seq: int):
        return [event for event in self.events if event["seq"] > after_seq]

    def publish(self, payload):
        event = {
            "seq": self.events[-1]["seq"] + 1 if self.events else 1,
            "revision": self.revision,
            "type": payload["type"],
            "project_id": payload["project_id"],
            "server_generation": payload["server_generation"],
            "payload": dict(payload["payload"]),
        }
        self.events.append(event)
        return event


class _App:
    project_id = "project_fixture_1"
    project_root = Path("/tmp/desktop-project")
    clock = staticmethod(lambda: "2026-08-16T00:00:00Z")

    def __init__(self, *, generation: int = 1, mounted: bool = True):
        self.lease = SimpleNamespace(
            generation=generation,
            mount_state=SimpleNamespace(value="mounted" if mounted else "unmounted"),
        )


class _Dispatcher:
    def __init__(self, *, timeout: bool = False):
        self.calls = 0
        self.timeout = timeout

    def submit(self, envelope):
        self.calls += 1
        if self.timeout:
            raise TimeoutError("transport timeout")

        class Receipt:
            def to_record(self):
                return {
                    "job_id": "job_fixture_1",
                    "agent_name": envelope.to_agent,
                    "status": "accepted",
                    "accepted_at": "2026-08-16T00:00:00Z",
                }

        return Receipt()


def _snapshot(project_id: str = "project_fixture_1", revision: int = 1):
    return {
        "view": {
            "generated_at": "2026-08-16T00:00:00Z",
            "project": {"id": project_id, "display_name": "private-name"},
            "agents": [],
            "comms": {"jobs": []},
        },
        "cache": {"generated_at": "2026-08-16T00:00:00Z", "sequence": revision},
    }


def _request(method, *, params=None, request_id="req_fixture", project_id="project_fixture_1", **kwargs):
    return {
        "protocol_version": DESKTOP_PROTOCOL_VERSION,
        "request_id": request_id,
        "project_id": project_id,
        "method": method,
        "params": dict(params or {}),
        **kwargs,
    }


class DesktopApiM0ContractTests(unittest.TestCase):
    def _adapter(self, *, authority=None, app=None, dispatcher=None, readiness_getter=None):
        app = app or _App()
        authority = authority or _EventAuthority(generation=app.lease.generation)
        return DesktopApiAdapter(
            app,
            generation_getter=lambda: app.lease.generation,
            snapshot_getter=lambda: _snapshot(revision=authority.revision),
            event_authority=authority,
            dispatcher=dispatcher,
            readiness_getter=readiness_getter,
        )

    def test_handshake_snapshot_use_authoritative_cursor_and_redact_root(self):
        adapter = self._adapter()
        handshake = adapter.handle(_request("handshake"))
        self.assertTrue(handshake["ok"])
        self.assertEqual(handshake["payload"]["snapshot_revision"], 1)
        snapshot = adapter.handle(_request("snapshot", request_id="req_snapshot"))
        self.assertTrue(snapshot["ok"])
        self.assertEqual(snapshot["payload"]["revision"], 1)
        self.assertEqual(snapshot["payload"]["project"]["project_root_display"], "<project>")
        self.assertNotIn(str(adapter.project_root), json.dumps(snapshot))

    def test_authority_missing_never_returns_empty_snapshot_or_zero_generation(self):
        app = _App()
        adapter = DesktopApiAdapter(app, generation_getter=lambda: None, snapshot_getter=lambda: {})
        handshake = adapter.handle(_request("handshake"))
        snapshot = adapter.handle(_request("snapshot"))
        self.assertEqual(handshake["error_code"], "CCBDSK_EVENT_AUTHORITY_UNAVAILABLE")
        self.assertEqual(snapshot["error_code"], "CCBDSK_EVENT_AUTHORITY_UNAVAILABLE")
        self.assertNotEqual(handshake.get("server_generation"), 0)

    def test_socket_peer_uid_is_kernel_verified(self):
        adapter = self._adapter()
        left, right = socket.socketpair()
        try:
            response = adapter.handle(_request("handshake"), peer=left)
            self.assertTrue(response["ok"])

            class ForeignPeer:
                def getpeereid(self):
                    return os.getuid() + 1, os.getgid()

            foreign = adapter.handle(_request("handshake", request_id="foreign"), peer=ForeignPeer())
            self.assertEqual(foreign["error_code"], "CCBDSK_PEER_UID_MISMATCH")
        finally:
            left.close()
            right.close()

    def test_generation_mismatch_and_stream_required_are_recoverable(self):
        adapter = self._adapter()
        mismatch = adapter.open_event_stream(
            _request("events.subscribe", params={"after_seq": 0, "server_generation": 0}),
        )
        self.assertEqual(mismatch["error_code"], "CCBDSK_GENERATION_MISMATCH")
        direct = adapter.handle(_request("events.subscribe", params={"after_seq": 0, "server_generation": 1}))
        self.assertEqual(direct["error_code"], "CCBDSK_STREAM_REQUIRED")

    def test_real_jsonl_socket_keeps_subscription_open_and_pushes_ordered_event(self):
        authority = _EventAuthority()
        adapter = self._adapter(authority=authority)
        server = CcbdSocketServer("/tmp/unused-desktop.sock")
        server.set_desktop_adapter(adapter)

        # A real AF_UNIX socketpair exercises JSONL framing without requiring
        # a privileged listener bind in hermetic test runners.
        left, client = socket.socketpair()
        try:
            client.settimeout(2.0)
            left_thread = threading.Thread(target=lambda: server._handle_connection(left), daemon=True)
            left_thread.start()
            client.sendall((json.dumps(_request("handshake")) + "\n").encode())
            handshake = json.loads(_readline(client))
            self.assertTrue(handshake["ok"])
            left_thread.join(2)
        finally:
            client.close()
            left.close()

        left, client = socket.socketpair()
        try:
            client.settimeout(2.0)
            left_thread = threading.Thread(target=lambda: server._handle_connection(left), daemon=True)
            left_thread.start()
            client.sendall((json.dumps(_request("events.subscribe", request_id="sub", params={"after_seq": 0, "server_generation": 1})) + "\n").encode())
            subscribed = json.loads(_readline(client))
            self.assertTrue(subscribed["ok"])
            self.assertTrue(subscribed["payload"]["stream"])
            client.settimeout(0.1)
            with self.assertRaises((socket.timeout, TimeoutError)):
                client.recv(1)
            authority.publish({"type": "diagnostic.created", "project_id": "project_fixture_1", "server_generation": 1, "payload": {"code": "A"}})
            client.settimeout(2.0)
            event = json.loads(_readline(client))
            self.assertEqual(event["seq"], 1)
            self.assertEqual(event["type"], "diagnostic.created")
        finally:
            client.close()
            server.close_desktop_streams()
            left.close()
            deadline = time.time() + 1.0
            while time.time() < deadline:
                with server._desktop_stream_lock:
                    if not server._desktop_streams:
                        break
                time.sleep(0.01)

    def test_unknown_event_and_gap_close_stream_with_snapshot_recovery(self):
        authority = _EventAuthority()
        adapter = self._adapter(authority=authority)
        authority.events.append({"seq": 1, "revision": 1, "type": "unknown.event", "project_id": "project_fixture_1", "server_generation": 1, "payload": {}})
        stream = adapter.open_event_stream(_request("events.subscribe", params={"after_seq": 0, "server_generation": 1}))
        left, right = socket.socketpair()
        try:
            thread = threading.Thread(target=stream.run, args=(left,), daemon=True)
            thread.start()
            response = json.loads(_readline(right))
            self.assertTrue(response["ok"])
            error = json.loads(_readline(right))
            self.assertEqual(error["error_code"], "CCBDSK_EVENT_TYPE_UNSUPPORTED")
            self.assertEqual(error["details"]["recovery"], "snapshot")
            thread.join(2)
        finally:
            right.close()

    def test_sequence_gap_is_not_repaired_or_guessed(self):
        authority = _EventAuthority()
        authority.events.append({"seq": 2, "revision": 1, "type": "diagnostic.created", "project_id": "project_fixture_1", "server_generation": 1, "payload": {}})
        authority.cursor = lambda: {
            "server_generation": 1,
            "snapshot_revision": 1,
            "last_event_seq": 2,
            "first_event_seq": 1,
        }
        stream = self._adapter(authority=authority).open_event_stream(
            _request("events.subscribe", params={"after_seq": 0, "server_generation": 1}),
        )
        left, right = socket.socketpair()
        try:
            thread = threading.Thread(target=stream.run, args=(left,), daemon=True)
            thread.start()
            self.assertTrue(json.loads(_readline(right))["ok"])
            error = json.loads(_readline(right))
            self.assertEqual(error["error_code"], "CCBDSK_EVENT_GAP")
            self.assertEqual(error["details"]["recovery"], "snapshot")
            thread.join(2)
        finally:
            right.close()

    def test_stream_generation_change_requires_recovery(self):
        authority = _EventAuthority()
        app = _App()
        adapter = self._adapter(authority=authority, app=app)
        stream = adapter.open_event_stream(
            _request("events.subscribe", params={"after_seq": 0, "server_generation": 1}),
        )
        left, right = socket.socketpair()
        try:
            thread = threading.Thread(target=stream.run, args=(left,), daemon=True)
            thread.start()
            self.assertTrue(json.loads(_readline(right))["ok"])
            app.lease.generation = 2
            authority.generation = 2
            error = json.loads(_readline(right))
            self.assertEqual(error["error_code"], "CCBDSK_GENERATION_MISMATCH")
            self.assertEqual(error["details"]["recovery"], "handshake_snapshot_subscribe")
            thread.join(2)
        finally:
            right.close()

    def test_job_submit_disabled_until_runtime_provider_dispatcher_authority_ready(self):
        authority = _EventAuthority()
        dispatcher = _Dispatcher()
        adapter = self._adapter(authority=authority, dispatcher=dispatcher)
        request = _request("job.submit", action_id="a1", server_generation=1, params={"agent_id": "agent1", "message": "hello"})
        result = adapter.handle(request)
        self.assertEqual(result["error_code"], "CCBDSK_CAPABILITY_DISABLED")
        self.assertEqual(result["details"]["reason_code"], "CCBDSK_AUTHORITY_UNAVAILABLE")
        self.assertEqual(dispatcher.calls, 0)

        ready = self._adapter(
            authority=authority,
            dispatcher=dispatcher,
            readiness_getter=lambda: (True, ""),
        )
        accepted = ready.handle(request)
        repeated = ready.handle({**request, "request_id": "repeat"})
        self.assertEqual(accepted["payload"], repeated["payload"])
        self.assertEqual(dispatcher.calls, 1)

    def test_descriptor_owner_uid_uses_stat_and_claim_mismatch_fails_closed(self):
        with tempfile.TemporaryDirectory(prefix="ccb-descriptor-", dir="/tmp") as temp_dir:
            path = (Path(temp_dir) / "endpoint").resolve()
            path.write_text("not socket", encoding="utf-8")
            with self.assertRaises(DesktopApiError) as raised:
                validate_unix_endpoint_descriptor(
                    {"kind": "unix_socket", "socket_path": str(path), "owner_uid": os.getuid() + 1},
                    expected_uid=os.getuid(),
                    require_socket=False,
                )
            self.assertEqual(raised.exception.details["evidence"], "descriptor_claimed_owner_uid")

    def test_discovery_is_side_effect_free_and_does_not_emit_root_or_socket_path(self):
        with tempfile.TemporaryDirectory(prefix="ccb-project-", dir="/tmp") as temp_dir:
            root = Path(temp_dir)
            (root / ".ccb").mkdir()
            # An invalid identity is enough to exercise the public redaction path.
            payload = build_discovery(str(root.resolve()))
            encoded = json.dumps(payload)
            self.assertNotIn(str(root), encoded)
            self.assertEqual(payload["project_root_display"], "<redacted>")
            self.assertIsNone(payload["runtime"]["server_generation"])
            self.assertEqual(redacted_display_root(root), "<project>")

    def test_compatibility_fixture_does_not_claim_baseline_release(self):
        fixture = json.loads(Path(__file__).parent.joinpath("fixtures/desktop_v1_compatibility.json").read_text())
        self.assertIsNone(fixture["minimum_ccb_version"])
        self.assertIsNone(fixture["validated_ccb_version"])
        self.assertFalse(fixture["windows_tcp_compatible"])


_FRAME_BUFFERS: dict[int, bytes] = {}


def _readline(sock: socket.socket) -> bytes:
    data = bytearray(_FRAME_BUFFERS.pop(sock.fileno(), b""))
    while not data.endswith(b"\n"):
        chunk = sock.recv(65536)
        if not chunk:
            raise EOFError("socket closed before JSONL frame")
        data.extend(chunk)
    line, separator, tail = bytes(data).partition(b"\n")
    if separator and tail:
        _FRAME_BUFFERS[sock.fileno()] = tail
    return line


if __name__ == "__main__":
    unittest.main()
