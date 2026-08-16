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
    DesktopEventGapError,
    DesktopEventAuthority,
    build_discovery,
    redacted_display_root,
    validate_unix_endpoint_descriptor,
)
from ccbd.api_models import TargetKind
from ccbd.services.mount import MountManager
from ccbd.services.dispatcher_runtime.records import append_event
from project.identity_store import ensure_project_identity
from storage.paths import PathLayout
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
        self.assertEqual(
            handshake["payload"]["event_coverage"],
            {"scope": "scoped", "complete": False, "event_types": ["job.accepted", "job.updated"]},
        )
        snapshot = adapter.handle(_request("snapshot", request_id="req_snapshot"))
        self.assertTrue(snapshot["ok"])
        self.assertEqual(snapshot["payload"]["revision"], 1)
        self.assertEqual(snapshot["payload"]["event_coverage"]["scope"], "scoped")
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
            authority.publish({"type": "job.accepted", "project_id": "project_fixture_1", "server_generation": 1, "payload": {"job_id": "job-stream"}})
            client.settimeout(2.0)
            event = json.loads(_readline(client))
            self.assertEqual(event["seq"], 1)
            self.assertEqual(event["type"], "job.accepted")
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

    def test_stream_exits_promptly_when_client_closes(self):
        adapter = self._adapter()
        stream = adapter.open_event_stream(
            _request("events.subscribe", params={"after_seq": 0, "server_generation": 1}),
        )
        left, right = socket.socketpair()
        thread = threading.Thread(target=stream.run, args=(left,), daemon=True)
        try:
            thread.start()
            self.assertTrue(json.loads(_readline(right))["ok"])
            right.close()
            thread.join(1.0)
            self.assertFalse(thread.is_alive())
        finally:
            try:
                right.close()
            except OSError:
                pass
            try:
                left.close()
            except OSError:
                pass

    def test_durable_event_authority_survives_reopen_and_resets_generation(self):
        with tempfile.TemporaryDirectory(prefix="ccb-authority-", dir="/tmp") as temp_dir:
            root = Path(temp_dir) / "repo"
            (root / ".ccb").mkdir(parents=True)
            layout = PathLayout(root)
            layout.ensure_runtime_state_root()
            generation = [7]
            revision = [3]
            authority = DesktopEventAuthority(
                layout,
                project_id=layout.project_id,
                generation_getter=lambda: generation[0],
                revision_getter=lambda: revision[0],
                retention=4,
            )
            self.assertEqual(authority.cursor()["last_event_seq"], 0)
            event = authority.publish({
                "type": "job.accepted",
                "project_id": layout.project_id,
                "payload": {"job_id": "job-1"},
            })
            self.assertEqual(event["seq"], 1)
            reopened = DesktopEventAuthority(
                layout,
                project_id=layout.project_id,
                generation_getter=lambda: generation[0],
                revision_getter=lambda: revision[0],
                retention=4,
            )
            self.assertEqual(reopened.cursor()["last_event_seq"], 1)
            self.assertEqual(reopened.read_since(0)[0]["type"], "job.accepted")
            generation[0] = 8
            revision[0] = 1
            self.assertEqual(reopened.cursor()["last_event_seq"], 0)
            self.assertEqual(reopened.read_since(0), [])

    def test_historical_event_revision_can_lag_current_snapshot_revision(self):
        with tempfile.TemporaryDirectory(prefix="ccb-authority-revision-", dir="/tmp") as temp_dir:
            root = Path(temp_dir) / "repo"
            (root / ".ccb").mkdir(parents=True)
            layout = PathLayout(root)
            layout.ensure_runtime_state_root()
            generation = [3]
            revision = [10]
            authority = DesktopEventAuthority(
                layout,
                project_id=layout.project_id,
                generation_getter=lambda: generation[0],
                revision_getter=lambda: revision[0],
            )
            authority.publish({"type": "job.accepted", "project_id": layout.project_id, "payload": {}})
            revision[0] = 11
            authority.cursor()
            self.assertEqual(authority.read_since(0)[0]["revision"], 10)
            self.assertEqual(authority.cursor()["snapshot_revision"], 11)

    def test_corrupt_sequence_fails_closed_and_persists_for_same_generation(self):
        with tempfile.TemporaryDirectory(prefix="ccb-authority-corrupt-", dir="/tmp") as temp_dir:
            root = Path(temp_dir) / "repo"
            (root / ".ccb").mkdir(parents=True)
            layout = PathLayout(root)
            layout.ensure_runtime_state_root()
            generation = [4]
            revision = [1]
            authority = DesktopEventAuthority(
                layout,
                project_id=layout.project_id,
                generation_getter=lambda: generation[0],
                revision_getter=lambda: revision[0],
            )
            authority.publish({"type": "job.updated", "project_id": layout.project_id, "payload": {}})
            rows = json.loads(layout.ccbd_desktop_events_path.read_text(encoding="utf-8").splitlines()[0])
            rows["seq"] = 3
            layout.ccbd_desktop_events_path.write_text(json.dumps(rows) + "\n", encoding="utf-8")
            with self.assertRaises(DesktopApiError) as raised:
                authority.cursor()
            self.assertEqual(raised.exception.code, "CCBDSK_AUTHORITY_INCONSISTENT")
            reopened = DesktopEventAuthority(
                layout,
                project_id=layout.project_id,
                generation_getter=lambda: generation[0],
                revision_getter=lambda: revision[0],
            )
            with self.assertRaises(DesktopApiError) as reopened_error:
                reopened.read_since(0)
            self.assertEqual(reopened_error.exception.code, "CCBDSK_EVENT_AUTHORITY_UNAVAILABLE")
            generation[0] = 5
            self.assertTrue(reopened.events_capability_enabled)
            self.assertEqual(reopened.cursor()["last_event_seq"], 0)

    def test_event_revision_rollback_fails_closed_without_partial_rows(self):
        with tempfile.TemporaryDirectory(prefix="ccb-authority-rollback-", dir="/tmp") as temp_dir:
            root = Path(temp_dir) / "repo"
            (root / ".ccb").mkdir(parents=True)
            layout = PathLayout(root)
            layout.ensure_runtime_state_root()
            generation = [6]
            revision = [20]
            authority = DesktopEventAuthority(
                layout,
                project_id=layout.project_id,
                generation_getter=lambda: generation[0],
                revision_getter=lambda: revision[0],
            )
            authority.publish({"type": "job.accepted", "project_id": layout.project_id, "payload": {}})
            revision[0] = 21
            authority.publish({"type": "job.updated", "project_id": layout.project_id, "payload": {}})
            rows = [json.loads(line) for line in layout.ccbd_desktop_events_path.read_text(encoding="utf-8").splitlines()]
            rows[1]["revision"] = 19
            layout.ccbd_desktop_events_path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            with self.assertRaises(DesktopApiError) as raised:
                authority.read_since(0)
            self.assertEqual(raised.exception.code, "CCBDSK_AUTHORITY_INCONSISTENT")

    def test_future_event_revision_fails_cursor_read_and_handshake_closed(self):
        with tempfile.TemporaryDirectory(prefix="ccb-authority-future-", dir="/tmp") as temp_dir:
            root = Path(temp_dir) / "repo"
            (root / ".ccb").mkdir(parents=True)
            layout = PathLayout(root)
            layout.ensure_runtime_state_root()
            generation = [10]
            revision = [30]
            authority = DesktopEventAuthority(
                layout,
                project_id=layout.project_id,
                generation_getter=lambda: generation[0],
                revision_getter=lambda: revision[0],
            )
            authority.publish({"type": "job.accepted", "project_id": layout.project_id, "payload": {}})
            row = json.loads(layout.ccbd_desktop_events_path.read_text(encoding="utf-8").splitlines()[0])
            row["revision"] = 31
            layout.ccbd_desktop_events_path.write_text(json.dumps(row) + "\n", encoding="utf-8")
            with self.assertRaises(DesktopApiError) as raised:
                authority.cursor()
            self.assertEqual(raised.exception.code, "CCBDSK_AUTHORITY_INCONSISTENT")
            reopened = DesktopEventAuthority(
                layout,
                project_id=layout.project_id,
                generation_getter=lambda: generation[0],
                revision_getter=lambda: revision[0],
            )
            with self.assertRaises(DesktopApiError) as read_error:
                reopened.read_since(0)
            self.assertEqual(read_error.exception.code, "CCBDSK_EVENT_AUTHORITY_UNAVAILABLE")
            adapter = self._adapter(authority=reopened, app=_App(generation=generation[0]))
            handshake = adapter.handle(_request("handshake"))
            self.assertFalse(handshake["ok"])
            self.assertEqual(handshake["error_code"], "CCBDSK_EVENT_AUTHORITY_UNAVAILABLE")

    def test_retention_gap_is_recoverable_and_stream_closes_with_gap_error(self):
        with tempfile.TemporaryDirectory(prefix="ccb-authority-gap-", dir="/tmp") as temp_dir:
            root = Path(temp_dir) / "repo"
            (root / ".ccb").mkdir(parents=True)
            layout = PathLayout(root)
            layout.ensure_runtime_state_root()
            generation = [11]
            revision = [40]
            authority = DesktopEventAuthority(
                layout,
                project_id=layout.project_id,
                generation_getter=lambda: generation[0],
                revision_getter=lambda: revision[0],
                retention=1,
            )
            authority.publish({"type": "job.accepted", "project_id": layout.project_id, "payload": {"seq": 1}})
            revision[0] = 41
            authority.publish({"type": "job.updated", "project_id": layout.project_id, "payload": {"seq": 2}})
            with self.assertRaises(DesktopEventGapError) as gap:
                authority.read_since(0)
            self.assertEqual(gap.exception.details["recovery"], "snapshot")
            self.assertEqual(gap.exception.details["first_event_seq"], 2)
            self.assertEqual(gap.exception.details["last_event_seq"], 2)
            self.assertIsNone(authority.failure_code)

            adapter = self._adapter(authority=authority, app=_App(generation=generation[0]))
            stream = adapter.open_event_stream(
                _request("events.subscribe", params={"after_seq": 1, "server_generation": generation[0]}),
            )
            left, right = socket.socketpair()
            try:
                thread = threading.Thread(target=stream.run, args=(left,), daemon=True)
                thread.start()
                self.assertTrue(json.loads(_readline(right))["ok"])
                revision[0] = 42
                authority.publish({"type": "job.updated", "project_id": layout.project_id, "payload": {"seq": 3}})
                error = json.loads(_readline(right))
                self.assertFalse(error["ok"])
                self.assertEqual(error["error_code"], "CCBDSK_EVENT_GAP")
                self.assertTrue(error["retryable"])
                self.assertEqual(error["details"]["recovery"], "snapshot")
                self.assertEqual(error["details"]["first_event_seq"], 3)
                self.assertEqual(error["details"]["last_event_seq"], 3)
                thread.join(2)
                self.assertFalse(thread.is_alive())
            finally:
                right.close()

    def test_same_generation_failure_marker_blocks_reopen_until_generation_changes(self):
        with tempfile.TemporaryDirectory(prefix="ccb-authority-failure-", dir="/tmp") as temp_dir:
            root = Path(temp_dir) / "repo"
            (root / ".ccb").mkdir(parents=True)
            layout = PathLayout(root)
            layout.ensure_runtime_state_root()
            generation = [8]
            revision = [1]
            authority = DesktopEventAuthority(
                layout,
                project_id=layout.project_id,
                generation_getter=lambda: generation[0],
                revision_getter=lambda: revision[0],
            )
            authority.record_failure(RuntimeError("publish failed"))
            self.assertFalse(authority.events_capability_enabled)
            authority.record_recovery()
            self.assertFalse(authority.events_capability_enabled)
            reopened = DesktopEventAuthority(
                layout,
                project_id=layout.project_id,
                generation_getter=lambda: generation[0],
                revision_getter=lambda: revision[0],
            )
            with self.assertRaises(DesktopApiError) as raised:
                reopened.cursor()
            self.assertEqual(raised.exception.code, "CCBDSK_EVENT_AUTHORITY_UNAVAILABLE")
            generation[0] = 9
            self.assertEqual(reopened.cursor()["server_generation"], 9)

    def test_unsupported_event_type_never_enters_desktop_authority(self):
        with tempfile.TemporaryDirectory(prefix="ccb-authority-type-", dir="/tmp") as temp_dir:
            root = Path(temp_dir) / "repo"
            (root / ".ccb").mkdir(parents=True)
            layout = PathLayout(root)
            layout.ensure_runtime_state_root()
            authority = DesktopEventAuthority(
                layout,
                project_id=layout.project_id,
                generation_getter=lambda: 1,
                revision_getter=lambda: 1,
            )
            with self.assertRaises(DesktopApiError) as raised:
                authority.publish({"type": "agent.updated", "project_id": layout.project_id, "payload": {}})
            self.assertEqual(raised.exception.code, "CCBDSK_EVENT_TYPE_UNSUPPORTED")
            self.assertEqual(authority.read_since(0), [])

    def test_dispatcher_core_transitions_publish_only_reviewed_desktop_events(self):
        published = []

        class Authority:
            def publish(self, event):
                published.append(event)

        class EventStore:
            def append(self, event):
                return None

        class Dispatcher:
            _event_store = EventStore()
            _desktop_event_authority = Authority()
            _layout = type("Layout", (), {"project_id": "project_fixture_1"})()

            def mark_project_view_dirty(self):
                return None

            def _new_id(self, prefix):
                return f"{prefix}-1"

        record = type(
            "Record",
            (),
            {
                "job_id": "job-1",
                "agent_name": "agent1",
                "target_kind": TargetKind.AGENT,
                "target_name": "agent1",
            },
        )()
        append_event(Dispatcher(), record, "job_accepted", {"status": "accepted"}, timestamp="now")
        append_event(Dispatcher(), record, "completion_item", {"status": "unknown"}, timestamp="now")
        self.assertEqual(len(published), 1)
        self.assertEqual(published[0]["type"], "job.accepted")
        self.assertEqual(published[0]["payload"]["job_id"], "job-1")

    def test_publish_failure_records_diagnostic_and_disables_events_without_losing_core_event(self):
        class FailingAuthority(_EventAuthority):
            def __init__(self):
                super().__init__()
                self.failure_code = None
                self.events_capability_enabled = True

            def publish(self, event):
                del event
                raise RuntimeError("projection unavailable")

            def record_failure(self, error):
                self.failure_code = "CCBDSK_EVENT_AUTHORITY_UNAVAILABLE"
                self.events_capability_enabled = False

            def record_recovery(self):
                self.failure_code = None
                self.events_capability_enabled = True

        class EventStore:
            def __init__(self):
                self.events = []

            def append(self, event):
                self.events.append(event)

        class Dispatcher:
            _layout = type("Layout", (), {"project_id": "project_fixture_1"})()

            def mark_project_view_dirty(self):
                return None

            def _new_id(self, prefix):
                return f"{prefix}-failure"

        authority = FailingAuthority()
        dispatcher = Dispatcher()
        dispatcher._event_store = EventStore()
        dispatcher._desktop_event_authority = authority
        record = type(
            "Record",
            (),
            {
                "job_id": "job-failure",
                "agent_name": "agent1",
                "target_kind": TargetKind.AGENT,
                "target_name": "agent1",
            },
        )()
        append_event(dispatcher, record, "job_accepted", {"status": "accepted"}, timestamp="now")
        self.assertEqual(len(dispatcher._event_store.events), 1)
        self.assertEqual(authority.failure_code, "CCBDSK_EVENT_AUTHORITY_UNAVAILABLE")
        adapter = self._adapter(authority=authority)
        handshake = adapter.handle(_request("handshake"))
        events = next(item for item in handshake["payload"]["capabilities"] if item["name"] == "events")
        self.assertFalse(events["enabled"])
        self.assertEqual(events["event_types"], ["job.accepted", "job.updated"])
        self.assertEqual(events["reason_code"], "CCBDSK_EVENT_AUTHORITY_UNAVAILABLE")
        authority.record_recovery()
        recovered = adapter.handle(_request("handshake", request_id="recovered"))
        recovered_events = next(item for item in recovered["payload"]["capabilities"] if item["name"] == "events")
        self.assertTrue(recovered_events["enabled"])

    def test_discovery_returns_connectable_locator_without_root_in_display_or_diagnostics(self):
        with tempfile.TemporaryDirectory(prefix="ccb-discovery-", dir="/tmp") as temp_dir:
            base = Path(temp_dir)
            root = base / "repo"
            (root / ".ccb").mkdir(parents=True)
            (root / ".ccb" / "ccb.config").write_text("agent1:codex\n", encoding="utf-8")
            ensure_project_identity(root)
            layout = PathLayout(root)
            layout.ensure_runtime_state_root()
            socket_path = Path(layout.ccbd_socket_path).resolve()
            socket_path.parent.mkdir(parents=True, exist_ok=True)
            socket_path.parent.chmod(0o700)
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            try:
                listener.bind(str(socket_path))
            except PermissionError as exc:
                listener.close()
                self.skipTest(f"AF_UNIX listener unavailable in sandbox: {exc}")
            listener.listen(2)
            try:
                MountManager(layout).mark_mounted(
                    project_id=layout.project_id,
                    pid=os.getpid(),
                    socket_path=socket_path,
                    generation=4,
                )
                payload = build_discovery(str(root.resolve()))
                descriptor = payload["endpoint_descriptor"]
                self.assertIsNotNone(descriptor)
                self.assertEqual(descriptor["socket_path"], str(socket_path.resolve()))
                self.assertEqual(payload["project_root_display"], "<project>")
                self.assertNotIn(str(root), payload["project_root_display"])
                self.assertNotIn(str(root), json.dumps(payload["diagnostics"]))
                self.assertEqual(payload["runtime"]["server_generation"], 4)
            finally:
                listener.close()

    def test_production_ccbd_app_wires_one_authority_and_missing_authority_fails_closed(self):
        try:
            from ccbd.app import CcbdApp
        except ModuleNotFoundError as exc:
            self.skipTest(f"CcbdApp dependency unavailable: {exc}")
        with tempfile.TemporaryDirectory(prefix="ccb-app-", dir="/tmp") as temp_dir:
            root = Path(temp_dir) / "repo"
            (root / ".ccb").mkdir(parents=True)
            (root / ".ccb" / "ccb.config").write_text("agent1:codex\n", encoding="utf-8")
            app = CcbdApp(root, clock=lambda: "2026-08-16T00:00:00Z", pid=os.getpid())
            app.lease = type("Lease", (), {"generation": 9})()
            adapter = app.socket_server._desktop_adapter
            self.assertIs(adapter._event_authority, app.desktop_event_authority)
            self.assertIs(app.dispatcher._desktop_event_authority, app.desktop_event_authority)
            request = _request("handshake", project_id=app.project_id)
            self.assertTrue(adapter.handle(request)["ok"])
            missing_app = SimpleNamespace(
                project_id=app.project_id,
                project_root=app.project_root,
                lease=app.lease,
                clock=app.clock,
                desktop_event_authority=None,
            )
            missing = DesktopApiAdapter(
                missing_app,
                project_id=app.project_id,
                generation_getter=lambda: 9,
                snapshot_getter=lambda: {},
            )
            self.assertEqual(missing.handle(request)["error_code"], "CCBDSK_EVENT_AUTHORITY_UNAVAILABLE")

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
        authority.events.append({"seq": 2, "revision": 1, "type": "job.updated", "project_id": "project_fixture_1", "server_generation": 1, "payload": {}})
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
