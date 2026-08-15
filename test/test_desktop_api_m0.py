from __future__ import annotations

import json
from pathlib import Path
import shutil
import socket
import stat
import tempfile

import pytest

from ccbd.desktop_api import (
    DESKTOP_PROTOCOL_VERSION,
    DesktopApiAdapter,
    DesktopApiError,
    build_discovery,
    validate_unix_endpoint_descriptor,
)
from project.identity_store import ensure_project_identity


class _FakeApp:
    project_id = "project_fixture_1"
    project_root = Path("/tmp/desktop-project")
    lease = None
    clock = staticmethod(lambda: "2026-08-16T00:00:00Z")


class _FakeDispatcher:
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


def _request(method, project_id="project_fixture_1", **kwargs):
    return {
        "protocol_version": DESKTOP_PROTOCOL_VERSION,
        "request_id": kwargs.pop("request_id", "req_fixture"),
        "project_id": project_id,
        "method": method,
        "params": kwargs.pop("params", {}),
        **kwargs,
    }


def test_handshake_and_snapshot_have_stable_envelopes():
    adapter = DesktopApiAdapter(_FakeApp())

    handshake = adapter.handle(_request("handshake"))
    assert handshake["ok"] is True
    assert handshake["protocol_version"] == "desktop.v1"
    assert handshake["payload"]["project"]["project_id"] == "project_fixture_1"
    assert handshake["payload"]["capabilities"]

    snapshot = adapter.handle(_request("snapshot", request_id="req_snapshot"))
    assert snapshot["ok"] is True
    payload = snapshot["payload"]
    assert set(("revision", "captured_at", "project", "agents", "jobs", "activities", "health", "capabilities", "last_event_seq")) <= set(payload)
    assert payload["project"]["project_root_display"].startswith("<project>")


def test_unix_peer_uid_is_verified_from_kernel_credentials():
    adapter = DesktopApiAdapter(_FakeApp())
    left, right = socket.socketpair()
    try:
        request = _request("handshake")
        response = adapter.handle(request, peer=left)
        assert response["ok"] is True
    finally:
        left.close()
        right.close()


def test_generation_and_project_mismatch_are_fail_closed():
    adapter = DesktopApiAdapter(_FakeApp(), generation_getter=lambda: 7)

    mismatch = adapter.handle(_request("handshake", project_id="foreign_project"))
    assert mismatch["error_code"] == "CCBDSK_PROJECT_ID_MISMATCH"

    stale = adapter.handle(
        _request(
            "events.subscribe",
            request_id="req_stale",
            params={"after_seq": 0, "server_generation": 6},
        )
    )
    assert stale["error_code"] == "CCBDSK_GENERATION_MISMATCH"

    action_stale = adapter.handle(
        _request(
            "job.submit",
            request_id="req_action_stale",
            action_id="action_stale",
            server_generation=6,
            params={"agent_id": "agent1", "message": "hello"},
        )
    )
    assert action_stale["error_code"] == "CCBDSK_GENERATION_MISMATCH"


def test_protocol_peer_and_cursor_errors_are_stable():
    adapter = DesktopApiAdapter(_FakeApp())
    unsupported = adapter.handle(
        {
            "protocol_version": "desktop.v2",
            "request_id": "req_protocol",
            "project_id": "project_fixture_1",
            "method": "handshake",
            "params": {},
        }
    )
    assert unsupported["error_code"] == "CCBDSK_PROTOCOL_UNSUPPORTED"

    class ForeignPeer:
        def getpeereid(self):
            return (os.getuid() + 1, os.getgid())

    import os

    foreign = adapter.handle(_request("handshake"), peer=ForeignPeer())
    assert foreign["error_code"] == "CCBDSK_PEER_UID_MISMATCH"

    ahead = adapter.handle(
        _request(
            "events.subscribe",
            request_id="req_ahead",
            params={"after_seq": 1, "server_generation": 0},
        )
    )
    assert ahead["error_code"] == "CCBDSK_EVENT_CURSOR_AHEAD"


def test_unknown_event_and_noncontiguous_cursor_require_recovery():
    adapter = DesktopApiAdapter(_FakeApp())
    with pytest.raises(DesktopApiError) as unknown:
        adapter.publish_event("event.not_in_v1", {})
    assert unknown.value.code == "CCBDSK_EVENT_TYPE_UNSUPPORTED"
    adapter.publish_event("diagnostic.created", {"code": "A"})
    adapter._events[0]["seq"] = 3
    response = adapter.handle(
        _request("events.subscribe", params={"after_seq": 0, "server_generation": 0})
    )
    assert response["error_code"] == "CCBDSK_EVENT_GAP"


def test_events_are_ordered_and_gap_requires_snapshot():
    adapter = DesktopApiAdapter(_FakeApp(), event_retention=2)
    adapter.publish_event("diagnostic.created", {"code": "A"})
    adapter.publish_event("health.changed", {"state": "degraded"})
    first = adapter.handle(
        _request("events.subscribe", params={"after_seq": 0, "server_generation": 0})
    )
    assert [item["seq"] for item in first["payload"]["events"]] == [1, 2]
    repeat = adapter.handle(
        _request("events.subscribe", request_id="req_repeat", params={"after_seq": 0, "server_generation": 0})
    )
    assert [item["seq"] for item in repeat["payload"]["events"]] == [1, 2]
    adapter.publish_event("config.changed", {"revision": 3})
    gap = adapter.handle(
        _request("events.subscribe", request_id="req_gap", params={"after_seq": 0, "server_generation": 0})
    )
    assert gap["error_code"] == "CCBDSK_EVENT_GAP"
    assert gap["details"]["recovery"] == "snapshot"


def test_action_id_is_idempotent_and_conflicts_are_rejected():
    dispatcher = _FakeDispatcher()
    adapter = DesktopApiAdapter(_FakeApp(), dispatcher=dispatcher)
    request = _request(
        "job.submit",
        action_id="action_1",
        server_generation=0,
        params={"agent_id": "agent1", "message": "hello"},
    )
    accepted = adapter.handle(request)
    repeated = adapter.handle({**request, "request_id": "req_repeated"})
    assert accepted["payload"] == repeated["payload"]
    assert dispatcher.calls == 1

    conflict = adapter.handle(
        {
            **request,
            "request_id": "req_conflict",
            "params": {"agent_id": "agent1", "message": "different"},
        }
    )
    assert conflict["error_code"] == "CCBDSK_ACTION_ID_CONFLICT"


def test_action_timeout_is_unknown_without_automatic_replay():
    dispatcher = _FakeDispatcher(timeout=True)
    adapter = DesktopApiAdapter(_FakeApp(), dispatcher=dispatcher)
    request = _request(
        "job.submit",
        action_id="action_timeout",
        server_generation=0,
        params={"agent_id": "agent1", "message": "hello"},
    )
    result = adapter.handle(request)
    repeated = adapter.handle({**request, "request_id": "req_timeout_repeat"})
    assert result["payload"]["state"] == "unknown"
    assert repeated["payload"] == result["payload"]
    assert dispatcher.calls == 1


def test_descriptor_rejects_tcp_and_writable_parent(tmp_path):
    with pytest.raises(DesktopApiError) as tcp:
        validate_unix_endpoint_descriptor(
            {"kind": "tcp_loopback", "address": "127.0.0.1:1"},
            require_socket=False,
        )
    assert tcp.value.code == "CCBDSK_RUNTIME_UNAVAILABLE"

    with pytest.raises(DesktopApiError) as relative:
        validate_unix_endpoint_descriptor(
            {"kind": "unix_socket", "socket_path": "relative.sock"},
            require_socket=False,
        )
    assert relative.value.code == "CCBDSK_RUNTIME_UNAVAILABLE"

    regular = tmp_path / "regular"
    regular.write_text("not a socket")
    symlink = tmp_path / "socket-link"
    symlink.symlink_to(regular)
    with pytest.raises(DesktopApiError) as link:
        validate_unix_endpoint_descriptor(
            {"kind": "unix_socket", "socket_path": str(symlink)},
            require_socket=True,
        )
    assert link.value.details["evidence"] == "socket_symlink"

    # Darwin limits AF_UNIX paths; keep the socket under /tmp even when the
    # pytest temporary root is a long /private/var/... path.
    short_dir = Path(tempfile.mkdtemp(prefix="ccb-dapi-", dir="/tmp"))
    socket_path = short_dir / "desktop.sock"
    regular_endpoint = short_dir / "regular-endpoint"
    regular_endpoint.write_text("not a socket")
    short_dir.chmod(stat.S_IRWXU | stat.S_IWGRP)
    with pytest.raises(DesktopApiError) as permissions:
        validate_unix_endpoint_descriptor(
            {"kind": "unix_socket", "socket_path": str(regular_endpoint.resolve())},
            require_socket=False,
        )
    assert permissions.value.details["evidence"] == "descriptor_permissions"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        try:
            listener.bind(str(socket_path.resolve()))
        except PermissionError:
            # Some hermetic runners disallow AF_UNIX bind even under /tmp; the
            # adapter's peer credential path is covered by socketpair tests.
            return
        socket_path.parent.chmod(stat.S_IRWXU)
        with pytest.raises(DesktopApiError) as permissions:
            validate_unix_endpoint_descriptor(
                {"kind": "unix_socket", "socket_path": str(socket_path)},
                require_socket=True,
            )
        assert permissions.value.code == "CCBDSK_RUNTIME_UNAVAILABLE"
    finally:
        listener.close()
        shutil.rmtree(short_dir, ignore_errors=True)


def test_discovery_rejects_relative_root_and_validates_identity(tmp_path):
    relative = build_discovery("relative/project", ccb_version="8.6.2")
    assert relative["diagnostics"][0]["code"] == "CCBDSK_PROJECT_INVALID"
    assert relative["project_id"] is None

    root = tmp_path / "project"
    (root / ".ccb").mkdir(parents=True)
    ensure_project_identity(root)
    payload = build_discovery(str(root.resolve()), ccb_version="8.6.2")
    assert payload["schema_version"] == "ccb.desktop-discovery.v1"
    assert payload["project_id"]
    assert payload["runtime"]["state"] == "unavailable"
    assert payload["endpoint"] is None


def test_compatibility_fixture_does_not_claim_baseline_release():
    fixture = json.loads(Path(__file__).parent.joinpath("fixtures/desktop_v1_compatibility.json").read_text())
    assert fixture["minimum_ccb_version"] is None
    assert fixture["validated_ccb_version"] is None
    assert fixture["windows_tcp_compatible"] is False

    invalid = json.loads(Path(__file__).parent.joinpath("fixtures/desktop_v1_invalid_requests.json").read_text())
    assert {item["expected_error_code"] for item in invalid} == {
        "CCBDSK_INVALID_REQUEST",
        "CCBDSK_PROJECT_ID_MISMATCH",
    }
