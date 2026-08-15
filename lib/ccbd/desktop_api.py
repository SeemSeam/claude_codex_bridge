"""CCB Desktop API V1 adapter.

This module deliberately sits beside, rather than inside, the legacy v2 RPC
models.  The adapter owns the desktop envelope, cursor and action semantics;
the existing dispatcher and project-view service remain the runtime authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import struct
import time
from typing import Any, Callable

from ccbd.services.mount import MountManager
from project.identity_store import load_project_identity
from storage.paths import PathLayout

DESKTOP_PROTOCOL_VERSION = "desktop.v1"
DISCOVERY_SCHEMA_VERSION = "ccb.desktop-discovery.v1"
MAX_DESKTOP_FRAME_BYTES = 1024 * 1024
MAX_EVENT_RETENTION = 256
EVENT_POLL_INTERVAL_S = 0.05

_IDENTIFIER_LIMIT = 200
_EVENT_TYPES = frozenset(
    {
        "project.state_changed",
        "agent.updated",
        "job.accepted",
        "job.updated",
        "activity.appended",
        "terminal.state_changed",
        "health.changed",
        "config.changed",
        "diagnostic.created",
    }
)
_TOP_LEVEL_FIELDS = frozenset(
    {
        "protocol_version",
        "request_id",
        "project_id",
        "method",
        "params",
        # ActionEvidence is intentionally top-level in the V1 schema.  M0
        # accepts these fields there and also tolerates the early fixture form
        # that placed them in params.
        "action_id",
        "server_generation",
        "target_id",
        "target_kind",
        "target_evidence",
    }
)
_ACTION_METHODS = frozenset(
    {
        "project.open",
        "job.submit",
        "job.cancel",
        "job.retry",
        "terminal.open",
        "terminal.input",
        "terminal.resize",
        "terminal.close",
        "config.validate",
        "config.reload",
    }
)


class DesktopApiError(ValueError):
    """A stable, safe-to-display Desktop API error."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.code = str(code)
        self.message = str(message)
        self.retryable = bool(retryable)
        self.details = dict(details or {})
        super().__init__(self.message)


@dataclass(frozen=True)
class PeerCredentials:
    uid: int
    source: str


class DesktopEventStream:
    """A socket-owned stream backed by an explicit CCB event authority."""

    def __init__(self, adapter, request: dict[str, Any], *, after_seq: int, generation: int, stop_event=None) -> None:
        self._adapter = adapter
        self._request = dict(request)
        self._after_seq = int(after_seq)
        self._generation = int(generation)
        self._stop_event = stop_event

    def run(self, conn) -> None:
        request_id = _safe_identifier(self._request.get("request_id"), fallback="req_invalid")
        try:
            conn.settimeout(1.0)
            cursor = self._adapter._authority_cursor()
            self._write(
                conn,
                self._adapter._success(
                    request_id,
                    {
                        "events": [],
                        "last_event_seq": cursor["last_event_seq"],
                        "server_generation": cursor["server_generation"],
                        "snapshot_required": False,
                        "stream": True,
                    },
                ),
            )
            expected = self._after_seq + 1
            while self._stop_event is None or not self._stop_event.is_set():
                cursor = self._adapter._authority_cursor()
                if cursor["server_generation"] != self._generation:
                    raise DesktopApiError(
                        "CCBDSK_GENERATION_MISMATCH",
                        "Event stream generation changed; recover from a snapshot",
                        retryable=True,
                        details={"expected_generation": self._generation, "recovery": "handshake_snapshot_subscribe"},
                    )
                events = self._adapter._authority_events_since(self._after_seq)
                sent = False
                for event in events:
                    normalized = self._adapter._normalize_authoritative_event(event, cursor)
                    seq = normalized["seq"]
                    if seq <= self._after_seq:
                        continue
                    if seq != expected:
                        raise DesktopApiError(
                            "CCBDSK_EVENT_GAP",
                            "Event authority returned a non-contiguous sequence",
                            retryable=True,
                            details={"recovery": "snapshot", "last_event_seq": cursor["last_event_seq"]},
                        )
                    self._write(conn, normalized)
                    self._after_seq = seq
                    expected = seq + 1
                    sent = True
                if not sent:
                    time.sleep(EVENT_POLL_INTERVAL_S)
        except DesktopApiError as exc:
            try:
                self._write(
                    conn,
                    self._adapter._error(
                        request_id,
                        self._adapter.project_id,
                        exc,
                    ),
                )
            except OSError:
                pass
        except (OSError, TimeoutError):
            pass
        finally:
            try:
                conn.close()
            except OSError:
                pass

    @staticmethod
    def _write(conn, payload: dict[str, Any]) -> None:
        conn.sendall((json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))


def current_peer_credentials(conn) -> PeerCredentials:
    """Read kernel supplied Unix peer credentials.

    macOS exposes ``getpeereid`` while Linux exposes ``SO_PEERCRED``.  There is
    intentionally no path-based or caller-supplied fallback: inability to
    obtain credentials is a fail-closed condition for Desktop connections.
    """

    getter = getattr(conn, "getpeereid", None)
    if callable(getter):
        try:
            uid, _gid = getter()
            return PeerCredentials(uid=int(uid), source="getpeereid")
        except (OSError, TypeError, ValueError) as exc:
            raise DesktopApiError(
                "CCBDSK_PEER_UID_UNVERIFIABLE",
                "Unix peer UID could not be verified",
                details={"evidence": "getpeereid_failed"},
            ) from exc

    if hasattr(socket, "SO_PEERCRED"):
        try:
            raw = conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
            _pid, uid, _gid = struct.unpack("3i", raw[: struct.calcsize("3i")])
            return PeerCredentials(uid=int(uid), source="so_peercred")
        except (AttributeError, OSError, struct.error, TypeError, ValueError) as exc:
            raise DesktopApiError(
                "CCBDSK_PEER_UID_UNVERIFIABLE",
                "Unix peer UID could not be verified",
                details={"evidence": "so_peercred_failed"},
            ) from exc

    if hasattr(socket, "LOCAL_PEERCRED"):
        try:
            # Python on macOS exposes LOCAL_PEERCRED but not SOL_LOCAL; the
            # Darwin socket level is zero in that build.
            local_level = getattr(socket, "SOL_LOCAL", 0)
            raw = conn.getsockopt(local_level, socket.LOCAL_PEERCRED, 256)
            # Darwin's xucred starts with a version and uid.  Keep parsing
            # narrow and reject short/unknown records.
            if len(raw) < 8:
                raise ValueError("short xucred")
            _version, uid = struct.unpack("2i", raw[:8])
            return PeerCredentials(uid=int(uid), source="local_peercred")
        except (AttributeError, OSError, struct.error, TypeError, ValueError) as exc:
            raise DesktopApiError(
                "CCBDSK_PEER_UID_UNVERIFIABLE",
                "Unix peer UID could not be verified",
                details={"evidence": "local_peercred_failed"},
            ) from exc

    raise DesktopApiError(
        "CCBDSK_PEER_UID_UNVERIFIABLE",
        "Unix peer UID verification is unavailable",
        details={"evidence": "platform_api_unavailable"},
    )


def validate_unix_endpoint_descriptor(
    descriptor: dict[str, Any],
    *,
    project_id: str | None = None,
    expected_uid: int | None = None,
    require_socket: bool = True,
) -> dict[str, Any]:
    """Validate a descriptor before it is handed to Desktop.

    The checks are evidence based and never infer permissions from a claimed
    field in the descriptor.  TCP, relative paths and writable parents are
    rejected even when the record otherwise looks well formed.
    """

    if not isinstance(descriptor, dict):
        raise DesktopApiError("CCBDSK_RUNTIME_UNAVAILABLE", "Endpoint descriptor is invalid")
    kind = str(descriptor.get("kind") or "").strip()
    if kind != "unix_socket":
        raise DesktopApiError(
            "CCBDSK_RUNTIME_UNAVAILABLE",
            "Desktop V1 requires a Unix socket endpoint",
            details={"evidence": "non_unix_endpoint"},
        )
    descriptor_project = str(descriptor.get("project_id") or project_id or "").strip()
    if project_id and descriptor_project != str(project_id):
        raise DesktopApiError(
            "CCBDSK_PROJECT_ID_MISMATCH",
            "Endpoint project identity does not match the requested project",
            details={"evidence": "descriptor_project_mismatch"},
        )
    raw_path = descriptor.get("socket_path") or descriptor.get("address") or descriptor.get("legacy_socket_path")
    path = Path(str(raw_path or ""))
    if not path.is_absolute() or ".." in path.parts:
        raise DesktopApiError(
            "CCBDSK_RUNTIME_UNAVAILABLE",
            "Endpoint socket path must be absolute and canonical",
            details={"evidence": "non_canonical_socket_path"},
        )
    try:
        canonical = path.resolve(strict=require_socket)
    except OSError as exc:
        raise DesktopApiError(
            "CCBDSK_RUNTIME_UNAVAILABLE",
            "Endpoint socket cannot be verified",
            retryable=True,
            details={"evidence": "socket_missing"},
        ) from exc
    if canonical != path.absolute():
        raise DesktopApiError(
            "CCBDSK_RUNTIME_UNAVAILABLE",
            "Endpoint socket path is not canonical",
            details={"evidence": "socket_symlink"},
        )
    try:
        record = os.stat(canonical)
        parent = os.stat(canonical.parent)
    except OSError as exc:
        raise DesktopApiError(
            "CCBDSK_RUNTIME_UNAVAILABLE",
            "Endpoint descriptor permissions cannot be verified",
            retryable=True,
            details={"evidence": "descriptor_stat_failed"},
        ) from exc
    if require_socket and not stat.S_ISSOCK(record.st_mode):
        raise DesktopApiError(
            "CCBDSK_RUNTIME_UNAVAILABLE",
            "Endpoint descriptor does not point to a Unix socket",
            details={"evidence": "not_socket"},
        )
    actual_uid = int(record.st_uid)
    process_uid = int(os.getuid())
    if expected_uid is not None and int(expected_uid) != process_uid:
        raise DesktopApiError(
            "CCBDSK_RUNTIME_UNAVAILABLE",
            "Endpoint owner UID evidence cannot be supplied by the caller",
            details={"evidence": "caller_uid_untrusted"},
        )
    expected = process_uid
    claimed_uid = descriptor.get("owner_uid")
    if claimed_uid is None:
        raise DesktopApiError(
            "CCBDSK_RUNTIME_UNAVAILABLE",
            "Endpoint owner UID evidence is missing",
            details={"evidence": "descriptor_owner_uid_missing"},
        )
    if claimed_uid is not None and _strict_nonnegative_int(claimed_uid) != actual_uid:
        raise DesktopApiError(
            "CCBDSK_RUNTIME_UNAVAILABLE",
            "Endpoint owner UID claim does not match filesystem evidence",
            details={"evidence": "descriptor_claimed_owner_uid"},
        )
    if actual_uid != expected or int(parent.st_uid) != actual_uid:
        raise DesktopApiError(
            "CCBDSK_RUNTIME_UNAVAILABLE",
            "Endpoint owner UID is not the current user",
            details={"evidence": "descriptor_owner_uid"},
        )
    if (record.st_mode | parent.st_mode) & (stat.S_IWGRP | stat.S_IWOTH):
        raise DesktopApiError(
            "CCBDSK_RUNTIME_UNAVAILABLE",
            "Endpoint socket or parent directory is writable by another user",
            details={"evidence": "descriptor_permissions"},
        )
    return {
        "kind": "unix_socket",
        "socket_path": str(canonical),
        "project_id": descriptor_project,
        "protocol_version": DESKTOP_PROTOCOL_VERSION,
        "owner_uid": actual_uid,
        "permissions": oct(stat.S_IMODE(record.st_mode)),
        "parent_permissions": oct(stat.S_IMODE(parent.st_mode)),
        "server_generation": _strict_nonnegative_int(descriptor.get("server_generation")),
        "created_at": str(descriptor.get("created_at") or ""),
    }


class DesktopApiAdapter:
    """Serve desktop.v1 requests over an already authenticated Unix stream."""

    def __init__(
        self,
        app=None,
        *,
        project_id: str | None = None,
        project_root: str | Path | None = None,
        generation_getter: Callable[[], int | None] | None = None,
        snapshot_getter: Callable[[], dict[str, Any]] | None = None,
        dispatcher=None,
        clock: Callable[[], str] | None = None,
        ccb_version: str | None = None,
        endpoint_descriptor: dict[str, Any] | None = None,
        event_retention: int = MAX_EVENT_RETENTION,
        event_authority=None,
        readiness_getter: Callable[[], Any] | None = None,
    ) -> None:
        self._app = app
        self.project_id = str(project_id or getattr(app, "project_id", "")).strip()
        self.project_root = Path(project_root or getattr(app, "project_root", "")).expanduser()
        self._generation_getter = generation_getter or self._app_generation
        self._snapshot_getter = snapshot_getter or self._app_snapshot
        self._dispatcher = dispatcher
        self._clock = clock or getattr(app, "clock", None) or _utc_now
        self.ccb_version = str(ccb_version or "").strip() or None
        self.endpoint_descriptor = dict(endpoint_descriptor or {})
        self._event_retention = max(1, int(event_retention))
        self._event_authority = event_authority or getattr(app, "desktop_event_authority", None)
        self._readiness_getter = readiness_getter
        self._actions: dict[str, tuple[str, int, dict[str, Any]]] = {}

    @property
    def server_generation(self) -> int:
        return self._read_generation()

    @property
    def last_event_seq(self) -> int:
        return self._authority_cursor()["last_event_seq"]

    def handle(self, request: dict[str, Any], *, peer=None) -> dict[str, Any]:
        request_id = _safe_identifier(request.get("request_id"), fallback="req_invalid") if isinstance(request, dict) else "req_invalid"
        requested_project = str(request.get("project_id") or "") if isinstance(request, dict) else ""
        try:
            envelope = self._validate_envelope(request)
            if peer is not None:
                self._verify_peer(peer)
            method = envelope["method"]
            params = envelope["params"]
            self._validate_method_params(method, params)
            if method == "handshake":
                payload = self._handshake()
            elif method == "snapshot":
                payload = self._snapshot()
            elif method == "events.subscribe":
                raise DesktopApiError(
                    "CCBDSK_STREAM_REQUIRED",
                    "events.subscribe requires a live JSONL socket stream",
                    retryable=True,
                    details={"recovery": "handshake_snapshot_subscribe"},
                )
            elif method in _ACTION_METHODS:
                payload = self._action(
                    method,
                    params,
                    action_id=request.get("action_id"),
                    server_generation=request.get("server_generation"),
                    target_id=request.get("target_id"),
                    target_kind=request.get("target_kind"),
                    target_evidence=request.get("target_evidence"),
                )
            else:
                raise DesktopApiError(
                    "CCBDSK_METHOD_UNSUPPORTED",
                    "Desktop method is not available in this M0 slice",
                    details={"evidence": "unknown_method"},
                )
            return self._success(envelope["request_id"], payload)
        except DesktopApiError as exc:
            return self._error(
                request_id,
                requested_project or self.project_id,
                exc,
            )
        except Exception:
            return self._error(
                request_id,
                requested_project or self.project_id,
                DesktopApiError(
                    "CCBDSK_RUNTIME_UNAVAILABLE",
                    "Desktop API runtime is unavailable",
                    retryable=True,
                    details={"evidence": "adapter_failure"},
                ),
            )

    def publish_event(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        revision: int | None = None,
        server_generation: int | None = None,
    ) -> dict[str, Any]:
        event_type = str(event_type or "").strip()
        if event_type not in _EVENT_TYPES:
            raise DesktopApiError(
                "CCBDSK_EVENT_TYPE_UNSUPPORTED",
                "Unknown Desktop event type cannot be published",
                details={"evidence": "unknown_event_type"},
            )
        authority = self._event_authority
        publisher = getattr(authority, "publish", None) if authority is not None else None
        if not callable(publisher):
            raise DesktopApiError(
                "CCBDSK_EVENT_AUTHORITY_UNAVAILABLE",
                "CCB Core event authority is unavailable",
                retryable=True,
                details={"evidence": "event_authority_missing", "recovery": "snapshot"},
            )
        generation = self.server_generation if server_generation is None else int(server_generation)
        if generation != self.server_generation:
            raise DesktopApiError(
                "CCBDSK_GENERATION_MISMATCH",
                "Event generation is stale",
                details={"expected_generation": self.server_generation},
            )
        event = publisher(
            {
                "type": event_type,
                "project_id": self.project_id,
                "server_generation": generation,
                "revision": revision,
                "payload": dict(payload or {}),
            }
        )
        cursor = self._authority_cursor()
        return self._normalize_authoritative_event(event, cursor)

    def open_event_stream(self, request: dict[str, Any], *, peer=None, stop_event=None):
        """Validate a subscription and return a socket-owned stream runner."""
        request_id = _safe_identifier(request.get("request_id"), fallback="req_invalid") if isinstance(request, dict) else "req_invalid"
        project_id = str(request.get("project_id") or self.project_id) if isinstance(request, dict) else self.project_id
        try:
            envelope = self._validate_envelope(request)
            if peer is not None:
                self._verify_peer(peer)
            if envelope["method"] != "events.subscribe":
                raise DesktopApiError("CCBDSK_INVALID_REQUEST", "stream method must be events.subscribe")
            self._validate_method_params(envelope["method"], envelope["params"])
            after_seq, generation = self._prepare_subscription(envelope["params"])
            return DesktopEventStream(
                self,
                request,
                after_seq=after_seq,
                generation=generation,
                stop_event=stop_event,
            )
        except DesktopApiError as exc:
            return self._error(request_id, project_id, exc)

    def _read_generation(self) -> int:
        try:
            value = self._generation_getter()
        except Exception as exc:
            raise DesktopApiError(
                "CCBDSK_AUTHORITY_UNAVAILABLE",
                "CCB server generation authority is unavailable",
                retryable=True,
                details={"evidence": "generation_read_failed", "recovery": "handshake_snapshot_subscribe"},
            ) from exc
        generation = _strict_nonnegative_int(value)
        if generation is None:
            raise DesktopApiError(
                "CCBDSK_AUTHORITY_UNAVAILABLE",
                "CCB server generation authority is unavailable",
                retryable=True,
                details={"evidence": "generation_missing", "recovery": "handshake_snapshot_subscribe"},
            )
        return generation

    def _authority_cursor(self) -> dict[str, int]:
        authority = self._event_authority
        reader = getattr(authority, "cursor", None) if authority is not None else None
        if not callable(reader):
            raise DesktopApiError(
                "CCBDSK_EVENT_AUTHORITY_UNAVAILABLE",
                "CCB Core event authority is unavailable",
                retryable=True,
                details={"evidence": "event_cursor_missing", "recovery": "snapshot"},
            )
        try:
            raw = reader()
        except Exception as exc:
            raise DesktopApiError(
                "CCBDSK_EVENT_AUTHORITY_UNAVAILABLE",
                "CCB Core event authority could not be read",
                retryable=True,
                details={"evidence": "event_cursor_read_failed", "recovery": "snapshot"},
            ) from exc
        if not isinstance(raw, dict):
            raw = {}
        generation = _strict_nonnegative_int(raw.get("server_generation"))
        revision = _strict_nonnegative_int(raw.get("snapshot_revision"))
        last_seq = _strict_nonnegative_int(raw.get("last_event_seq"))
        first_seq = _strict_positive_int(raw.get("first_event_seq"))
        if last_seq == 0 and first_seq is None:
            first_seq = 1
        if generation is None or revision is None or last_seq is None or first_seq is None:
            raise DesktopApiError(
                "CCBDSK_AUTHORITY_INCONSISTENT",
                "CCB event authority cursor is incomplete",
                retryable=True,
                details={"evidence": "event_cursor_fields_missing", "recovery": "snapshot"},
            )
        current_generation = self._read_generation()
        if generation != current_generation or first_seq > last_seq + 1:
            raise DesktopApiError(
                "CCBDSK_AUTHORITY_INCONSISTENT",
                "CCB event authority disagrees with the active server generation",
                retryable=True,
                details={"evidence": "event_cursor_mismatch", "recovery": "handshake_snapshot_subscribe"},
            )
        return {
            "server_generation": generation,
            "snapshot_revision": revision,
            "last_event_seq": last_seq,
            "first_event_seq": first_seq,
        }

    def _authority_events_since(self, after_seq: int) -> list[dict[str, Any]]:
        reader = getattr(self._event_authority, "read_since", None) if self._event_authority is not None else None
        if not callable(reader):
            raise DesktopApiError(
                "CCBDSK_EVENT_AUTHORITY_UNAVAILABLE",
                "CCB Core event stream authority is unavailable",
                retryable=True,
                details={"evidence": "event_reader_missing", "recovery": "snapshot"},
            )
        try:
            events = reader(int(after_seq))
        except Exception as exc:
            raise DesktopApiError(
                "CCBDSK_EVENT_AUTHORITY_UNAVAILABLE",
                "CCB Core event stream could not be read",
                retryable=True,
                details={"evidence": "event_reader_failed", "recovery": "snapshot"},
            ) from exc
        if not isinstance(events, (list, tuple)) or any(not isinstance(event, dict) for event in events):
            raise DesktopApiError(
                "CCBDSK_AUTHORITY_INCONSISTENT",
                "CCB event authority returned an invalid sequence",
                retryable=True,
                details={"evidence": "event_reader_invalid", "recovery": "snapshot"},
            )
        return [dict(event) for event in events]

    def _normalize_authoritative_event(self, event: dict[str, Any], cursor: dict[str, int]) -> dict[str, Any]:
        if not isinstance(event, dict):
            raise DesktopApiError("CCBDSK_AUTHORITY_INCONSISTENT", "CCB event authority returned an invalid event", retryable=True, details={"recovery": "snapshot"})
        event_type = str(event.get("type") or "").strip()
        seq = _strict_positive_int(event.get("seq"))
        revision = _strict_nonnegative_int(event.get("revision"))
        generation = _strict_nonnegative_int(event.get("server_generation"))
        project_id = str(event.get("project_id") or "").strip()
        payload = event.get("payload")
        if event_type not in _EVENT_TYPES:
            raise DesktopApiError("CCBDSK_EVENT_TYPE_UNSUPPORTED", "Unknown Desktop event type cannot be published", retryable=True, details={"evidence": "unknown_event_type", "recovery": "snapshot"})
        if seq is None or revision is None or generation is None or seq > cursor["last_event_seq"] or project_id != self.project_id or generation != cursor["server_generation"] or not isinstance(payload, dict):
            raise DesktopApiError("CCBDSK_AUTHORITY_INCONSISTENT", "CCB event authority returned an invalid event", retryable=True, details={"evidence": "event_fields_invalid", "recovery": "snapshot"})
        try:
            json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        except (TypeError, ValueError) as exc:
            raise DesktopApiError("CCBDSK_AUTHORITY_INCONSISTENT", "CCB event authority returned a non-JSON payload", retryable=True, details={"evidence": "event_payload_invalid", "recovery": "snapshot"}) from exc
        return {
            "seq": seq,
            "revision": revision,
            "type": event_type,
            "project_id": self.project_id,
            "server_generation": generation,
            "payload": dict(payload),
        }

    def _validate_envelope(self, request: Any) -> dict[str, Any]:
        if not isinstance(request, dict):
            raise DesktopApiError("CCBDSK_INVALID_REQUEST", "Desktop request must be a JSON object")
        unknown = sorted(set(request) - _TOP_LEVEL_FIELDS)
        if unknown:
            raise DesktopApiError(
                "CCBDSK_INVALID_REQUEST",
                "Desktop request contains unsupported fields",
                details={"evidence": "unknown_fields"},
            )
        if request.get("protocol_version") != DESKTOP_PROTOCOL_VERSION:
            raise DesktopApiError(
                "CCBDSK_PROTOCOL_UNSUPPORTED",
                "Desktop protocol version is unsupported",
                retryable=False,
                details={"supported_protocols": [DESKTOP_PROTOCOL_VERSION]},
            )
        request_id = _safe_identifier(request.get("request_id"), fallback="")
        if not request_id:
            raise DesktopApiError("CCBDSK_INVALID_REQUEST", "request_id is required")
        if str(request.get("project_id") or "").strip() != self.project_id:
            raise DesktopApiError(
                "CCBDSK_PROJECT_ID_MISMATCH",
                "Request project identity does not match the connected project",
                details={"evidence": "request_project_mismatch"},
            )
        method = str(request.get("method") or "").strip()
        if not method:
            raise DesktopApiError("CCBDSK_INVALID_REQUEST", "method is required")
        params = request.get("params")
        if not isinstance(params, dict):
            raise DesktopApiError("CCBDSK_INVALID_REQUEST", "params must be an object")
        return {"request_id": request_id, "method": method, "params": params}

    def _verify_peer(self, peer) -> None:
        if os.name == "nt" or not hasattr(socket, "AF_UNIX"):
            raise DesktopApiError(
                "CCBDSK_RUNTIME_UNAVAILABLE",
                "Desktop V1 is unavailable on this transport",
                details={"evidence": "unix_transport_required"},
            )
        credentials = current_peer_credentials(peer)
        if int(credentials.uid) != int(os.getuid()):
            raise DesktopApiError(
                "CCBDSK_PEER_UID_MISMATCH",
                "Unix peer UID does not match the current user",
                details={"evidence": "peer_uid_mismatch"},
            )

    def _handshake(self) -> dict[str, Any]:
        cursor = self._authority_cursor()
        payload: dict[str, Any] = {
            "project": {
                "project_id": self.project_id,
                "project_root_display": redacted_display_root(self.project_root),
                "project_name": "<project>",
            },
            "server_generation": cursor["server_generation"],
            "snapshot_revision": cursor["snapshot_revision"],
            "last_event_seq": cursor["last_event_seq"],
            "capabilities": self.capabilities(cursor=cursor),
        }
        if self.ccb_version:
            payload["ccb_version"] = self.ccb_version
        return payload

    def _snapshot(self) -> dict[str, Any]:
        cursor = self._authority_cursor()
        try:
            source = self._snapshot_getter()
        except Exception as exc:
            raise DesktopApiError(
                "CCBDSK_SNAPSHOT_UNAVAILABLE",
                "CCB project snapshot authority is unavailable",
                retryable=True,
                details={"evidence": "snapshot_read_failed", "recovery": "handshake_snapshot_subscribe"},
            ) from exc
        if not isinstance(source, dict):
            raise DesktopApiError(
                "CCBDSK_SNAPSHOT_UNAVAILABLE",
                "CCB project snapshot authority is unavailable",
                retryable=True,
                details={"evidence": "snapshot_missing", "recovery": "handshake_snapshot_subscribe"},
            )
        view = source.get("view") if isinstance(source.get("view"), dict) else source
        cache = source.get("cache") if isinstance(source.get("cache"), dict) else None
        revision = _strict_nonnegative_int(cache.get("sequence") if cache else None)
        project = view.get("project") if isinstance(view.get("project"), dict) else None
        captured_at = str((cache or {}).get("generated_at") or view.get("generated_at") or "").strip()
        if revision is None or project is None or captured_at == "":
            raise DesktopApiError(
                "CCBDSK_SNAPSHOT_UNAVAILABLE",
                "CCB project snapshot authority is incomplete",
                retryable=True,
                details={"evidence": "snapshot_authority_incomplete", "recovery": "handshake_snapshot_subscribe"},
            )
        source_project_id = str(project.get("id") or project.get("project_id") or "").strip()
        if source_project_id != self.project_id or revision != cursor["snapshot_revision"]:
            raise DesktopApiError(
                "CCBDSK_AUTHORITY_INCONSISTENT",
                "CCB snapshot authority disagrees with the event cursor",
                retryable=True,
                details={"evidence": "snapshot_cursor_mismatch", "recovery": "handshake_snapshot_subscribe"},
            )
        payload = {
            "revision": revision,
            "captured_at": captured_at,
            "project": {
                "project_id": self.project_id,
                "project_root_display": redacted_display_root(self.project_root),
                "project_name": "<project>",
            },
            "agents": list(view.get("agents") or []),
            "jobs": list(view.get("jobs") or _jobs_from_view(view)),
            "activities": list(view.get("activities") or []),
            "health": self._health(view),
            "capabilities": self.capabilities(cursor=cursor),
            "last_event_seq": cursor["last_event_seq"],
        }
        return payload

    def _health(self, view: dict[str, Any]) -> dict[str, Any]:
        lease = getattr(self._app, "lease", None)
        return {
            "runtime": {
                "state": "running" if lease is not None else "unavailable",
                "server_generation": self.server_generation,
            },
            "keeper": {"state": "unknown", "diagnostic": "CCBDSK_KEEPER_STATUS_UNIMPLEMENTED"},
            "provider": {"state": "unknown", "diagnostic": "CCBDSK_PROVIDER_STATUS_UNIMPLEMENTED"},
            "tmux": {"state": "unknown", "diagnostic": "CCBDSK_TMUX_STATUS_UNIMPLEMENTED"},
            "adapter": {"state": "ready", "diagnostics": []},
        }

    def _subscribe(self, params: dict[str, Any]) -> dict[str, Any]:
        del params
        raise DesktopApiError(
            "CCBDSK_STREAM_REQUIRED",
            "events.subscribe requires a live JSONL socket stream",
            retryable=True,
            details={"recovery": "handshake_snapshot_subscribe"},
        )

    def _prepare_subscription(self, params: dict[str, Any]) -> tuple[int, int]:
        cursor = self._authority_cursor()
        after_seq = _nonnegative_int(params.get("after_seq"), default=-1)
        if after_seq < 0:
            raise DesktopApiError("CCBDSK_INVALID_REQUEST", "after_seq must be a non-negative integer")
        expected_generation = _nonnegative_int(params.get("server_generation"), default=-1)
        if expected_generation < 0:
            raise DesktopApiError("CCBDSK_INVALID_REQUEST", "server_generation is required")
        if expected_generation != cursor["server_generation"]:
            raise DesktopApiError(
                "CCBDSK_GENERATION_MISMATCH",
                "Event cursor belongs to a different server generation",
                retryable=True,
                details={"expected_generation": cursor["server_generation"], "recovery": "handshake_snapshot_subscribe"},
            )
        first_seq = cursor["first_event_seq"]
        if after_seq < first_seq - 1:
            raise DesktopApiError(
                "CCBDSK_EVENT_GAP",
                "Requested event cursor is outside the retained window",
                retryable=True,
                details={"recovery": "snapshot", "last_event_seq": cursor["last_event_seq"]},
            )
        if after_seq > cursor["last_event_seq"]:
            raise DesktopApiError(
                "CCBDSK_EVENT_CURSOR_AHEAD",
                "Requested event cursor is ahead of the server",
                retryable=True,
                details={"last_event_seq": cursor["last_event_seq"]},
            )
        return after_seq, cursor["server_generation"]

    def _action(
        self,
        method: str,
        params: dict[str, Any],
        *,
        action_id: Any = None,
        server_generation: Any = None,
        target_id: Any = None,
        target_kind: Any = None,
        target_evidence: Any = None,
    ) -> dict[str, Any]:
        action_id = _safe_identifier(action_id or params.get("action_id"), fallback="")
        if not action_id:
            raise DesktopApiError("CCBDSK_INVALID_REQUEST", "action_id is required")
        generation = _nonnegative_int(server_generation if server_generation is not None else params.get("server_generation"), default=-1)
        if generation != self.server_generation:
            raise DesktopApiError(
                "CCBDSK_GENERATION_MISMATCH",
                "Action generation is stale",
                retryable=True,
                details={"expected_generation": self.server_generation},
            )
        self._validate_action_params(method, params)
        normalized = {
            "method": method,
            "params": _without_action_transport_fields(params),
            "target_id": target_id,
            "target_kind": target_kind,
            "target_evidence": target_evidence,
        }
        fingerprint = _stable_digest(normalized)
        prior = self._actions.get(action_id)
        if prior is not None:
            prior_fingerprint, prior_generation, prior_receipt = prior
            if prior_generation != self.server_generation:
                raise DesktopApiError(
                    "CCBDSK_ACTION_ID_CONFLICT",
                    "action_id belongs to a different server generation",
                    details={"evidence": "action_id_generation_conflict"},
                )
            if prior_fingerprint != fingerprint:
                raise DesktopApiError(
                    "CCBDSK_ACTION_ID_CONFLICT",
                    "action_id was already used with different input",
                    details={"evidence": "action_id_conflict"},
                )
            return dict(prior_receipt)
        if method != "job.submit":
            raise DesktopApiError(
                "CCBDSK_CAPABILITY_DISABLED",
                "This Desktop action is disabled in the M0 slice",
                details={"method": method, "reason_code": "M0_NOT_IMPLEMENTED"},
            )
        ready, reason = self._job_submit_readiness()
        if not ready:
            raise DesktopApiError(
                "CCBDSK_CAPABILITY_DISABLED",
                "job.submit is unavailable because the dispatcher is not ready",
                details={"reason_code": reason},
            )
        dispatcher = self._resolved_dispatcher()
        agent_id = _safe_identifier(params.get("agent_id"), fallback="")
        message = str(params.get("message") or "")
        parent_job_id = params.get("parent_job_id")
        if parent_job_id is not None and not _safe_identifier(parent_job_id, fallback=""):
            raise DesktopApiError("CCBDSK_INVALID_REQUEST", "parent_job_id is invalid")
        if not agent_id or not message.strip() or len(message) > 100000:
            raise DesktopApiError("CCBDSK_INVALID_REQUEST", "job.submit requires agent_id and message")
        try:
            from ccbd.api_models import DeliveryScope, MessageEnvelope

            receipt = dispatcher.submit(
                MessageEnvelope(
                    project_id=self.project_id,
                    to_agent=agent_id,
                    from_actor="desktop",
                    body=message,
                    task_id=params.get("parent_job_id"),
                    reply_to=None,
                    message_type="ask",
                    delivery_scope=DeliveryScope.SINGLE,
                )
            )
        except TimeoutError:
            receipt = {
                "action_id": action_id,
                "state": "unknown",
                "accepted_at": self._clock(),
                "details": {"reason_code": "TRANSPORT_TIMEOUT", "replay": "forbidden"},
            }
            self._actions[action_id] = (fingerprint, self.server_generation, receipt)
            return dict(receipt)
        except Exception as exc:
            del exc
            raise DesktopApiError(
                "CCBDSK_ACTION_REJECTED",
                "job.submit was rejected by the CCB dispatcher",
                details={"method": method},
            )
        record = receipt.to_record() if hasattr(receipt, "to_record") else dict(receipt or {})
        result = {
            "action_id": action_id,
            "state": "accepted",
            "accepted_at": str(record.get("accepted_at") or self._clock()),
            "details": {"job": record},
        }
        self._actions[action_id] = (fingerprint, self.server_generation, result)
        return dict(result)

    def _validate_action_params(self, method: str, params: dict[str, Any]) -> None:
        allowed = {
            "job.submit": {"agent_id", "message", "parent_job_id", "action_id", "server_generation"},
            "job.cancel": {"job_id", "action_id", "server_generation"},
            "job.retry": {"job_id", "action_id", "server_generation"},
            "project.open": {"action_id", "server_generation", "target_evidence"},
            "terminal.open": {"agent_id", "window_id", "pane_id", "namespace_epoch", "action_id", "server_generation"},
            "terminal.input": {"terminal_id", "bytes_base64", "action_id", "server_generation"},
            "terminal.resize": {"terminal_id", "columns", "rows", "action_id", "server_generation"},
            "terminal.close": {"terminal_id", "action_id", "server_generation"},
            "config.validate": {"config_revision", "action_id", "server_generation"},
            "config.reload": {"config_revision", "action_id", "server_generation"},
        }.get(method, set())
        if set(params) - allowed:
            raise DesktopApiError(
                "CCBDSK_INVALID_REQUEST",
                "Action params contain unsupported fields",
                details={"evidence": "unknown_action_fields"},
            )

    def _validate_method_params(self, method: str, params: dict[str, Any]) -> None:
        if method in {"handshake", "snapshot"} and params:
            raise DesktopApiError(
                "CCBDSK_INVALID_REQUEST",
                "This Desktop query does not accept params",
                details={"evidence": "unknown_query_fields"},
            )
        if method == "events.subscribe":
            allowed = {"after_seq", "server_generation"}
            if set(params) - allowed:
                raise DesktopApiError(
                    "CCBDSK_INVALID_REQUEST",
                    "events.subscribe params contain unsupported fields",
                    details={"evidence": "unknown_event_fields"},
                )

    def capabilities(self, *, cursor: dict[str, int] | None = None) -> list[dict[str, Any]]:
        authority_ready = cursor is not None
        if cursor is None:
            try:
                cursor = self._authority_cursor()
            except DesktopApiError:
                cursor = None
        snapshot_ready = authority_ready and callable(self._snapshot_getter)
        events_ready = authority_ready and callable(getattr(self._event_authority, "read_since", None))
        job_ready, job_reason = self._job_submit_readiness()
        values = [
            {"name": "snapshot", "enabled": snapshot_ready, **({} if snapshot_ready else {"reason_code": "CCBDSK_SNAPSHOT_UNAVAILABLE"})},
            {"name": "events", "enabled": events_ready, **({"limits": {"retention": self._event_retention}} if events_ready else {"reason_code": "CCBDSK_EVENT_AUTHORITY_UNAVAILABLE"})},
            {"name": "job.submit", "enabled": job_ready, **({} if job_ready else {"reason_code": job_reason})},
        ]
        for name in ("project.open", "job.cancel", "job.retry", "terminal", "files", "git", "health", "config"):
            values.append({"name": name, "enabled": False, "reason_code": "M0_NOT_IMPLEMENTED"})
        return values

    def _resolved_dispatcher(self):
        dispatcher = self._dispatcher or getattr(self._app, "dispatcher", None)
        try:
            graph = self._app.current_service_graph()
            dispatcher = getattr(graph, "dispatcher", dispatcher)
        except Exception:
            pass
        return dispatcher

    def _job_submit_readiness(self) -> tuple[bool, str]:
        if callable(self._readiness_getter):
            try:
                result = self._readiness_getter()
                if isinstance(result, tuple):
                    return bool(result[0]), str(result[1] or "CCBDSK_RUNTIME_UNAVAILABLE")
                if isinstance(result, dict):
                    return bool(result.get("ready")), str(result.get("reason_code") or "CCBDSK_RUNTIME_UNAVAILABLE")
                return bool(result), "CCBDSK_RUNTIME_UNAVAILABLE"
            except Exception:
                return False, "CCBDSK_RUNTIME_UNAVAILABLE"
        app = self._app
        dispatcher = self._resolved_dispatcher()
        lease = getattr(app, "lease", None)
        mount_state = str(getattr(getattr(lease, "mount_state", None), "value", getattr(lease, "mount_state", "")) or "").lower()
        if lease is None or mount_state != "mounted":
            return False, "CCBDSK_RUNTIME_NOT_READY"
        if dispatcher is None or not callable(getattr(dispatcher, "submit", None)):
            return False, "CCBDSK_DISPATCHER_UNAVAILABLE"
        registry = getattr(dispatcher, "_registry", None)
        provider_catalog = getattr(dispatcher, "_provider_catalog", None)
        config = getattr(dispatcher, "_config", None)
        runtime_service = getattr(dispatcher, "_runtime_service", None)
        if registry is None or provider_catalog is None or runtime_service is None or config is None:
            return False, "CCBDSK_AUTHORITY_UNAVAILABLE"
        generation = self._read_generation()
        try:
            agent_names = tuple(getattr(config, "agents", ()))
        except Exception:
            return False, "CCBDSK_PROVIDER_NOT_READY"
        for agent_name in agent_names:
            try:
                runtime = registry.get(agent_name)
                spec = registry.spec_for(agent_name)
                provider_catalog.resolve_completion_manifest(spec.provider, spec.runtime_mode)
            except Exception:
                return False, "CCBDSK_PROVIDER_NOT_READY"
            state = str(getattr(getattr(runtime, "state", None), "value", getattr(runtime, "state", "")) or "").lower()
            health = str(getattr(runtime, "health", "") or "").lower()
            if runtime is None or state in {"", "stopped", "failed", "degraded"} or health not in {"healthy", "restored"}:
                return False, "CCBDSK_RUNTIME_NOT_READY"
            runtime_generation = _strict_nonnegative_int(getattr(runtime, "daemon_generation", None))
            if runtime_generation is None or runtime_generation != generation:
                return False, "CCBDSK_RUNTIME_NOT_READY"
        return True, ""

    def _success(self, request_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "ok": True,
            "protocol_version": DESKTOP_PROTOCOL_VERSION,
            "request_id": request_id,
            "project_id": self.project_id,
            "server_generation": self.server_generation,
            "payload": dict(payload),
        }

    def _error(self, request_id: str, project_id: str, exc: DesktopApiError) -> dict[str, Any]:
        return {
            "ok": False,
            "protocol_version": DESKTOP_PROTOCOL_VERSION,
            "request_id": _safe_identifier(request_id, fallback="req_invalid"),
            "project_id": _safe_identifier(project_id, fallback=self.project_id or "unknown"),
            "error_code": exc.code,
            "message": exc.message,
            "retryable": exc.retryable,
            "details": dict(exc.details),
        }

    def _app_generation(self) -> int:
        lease = getattr(self._app, "lease", None)
        if lease is not None:
            generation = _strict_nonnegative_int(getattr(lease, "generation", None))
            if generation is not None:
                return generation
        lifecycle_store = getattr(self._app, "lifecycle_store", None)
        try:
            lifecycle = lifecycle_store.load() if lifecycle_store is not None else None
            generation = _strict_nonnegative_int(getattr(lifecycle, "generation", None))
            if generation is not None:
                return generation
        except Exception as exc:
            raise RuntimeError("generation authority unavailable") from exc
        raise RuntimeError("generation authority unavailable")

    def _app_snapshot(self) -> dict[str, Any]:
        graph = self._app.current_service_graph()
        service = getattr(graph, "project_view_service", None)
        if service is None:
            raise RuntimeError("project snapshot authority unavailable")
        return service.build_response(schema_version=1)


def build_discovery(
    project_root: str | Path,
    *,
    ccb_version: str | None = None,
    current_uid: int | None = None,
    require_canonical: bool = True,
) -> dict[str, Any]:
    """Build side-effect-free ``ccb.desktop-discovery.v1`` JSON data."""

    raw = str(project_root or "").strip()
    diagnostics: list[dict[str, str]] = []
    # A caller-supplied UID is a claim, never authority for discovery.
    uid = int(os.getuid())
    if not raw or not Path(raw).is_absolute():
        return _discovery_failure("CCBDSK_PROJECT_INVALID", diagnostics, ccb_version=ccb_version)
    try:
        root = Path(raw).resolve(strict=True)
    except OSError:
        return _discovery_failure("CCBDSK_PROJECT_INVALID", diagnostics, ccb_version=ccb_version)
    if require_canonical and Path(raw) != root:
        return _discovery_failure("CCBDSK_PROJECT_INVALID", diagnostics, ccb_version=ccb_version)
    if not root.is_dir() or not (root / ".ccb").is_dir():
        return _discovery_failure("CCBDSK_PROJECT_INVALID", diagnostics, ccb_version=ccb_version)
    try:
        identity = load_project_identity(root)
    except Exception:
        identity = None
    if identity is None or identity.bound_root != str(root):
        return _discovery_failure("CCBDSK_PROJECT_INVALID", diagnostics, ccb_version=ccb_version)
    project_id = identity.project_id
    layout = PathLayout(root)
    lease = None
    try:
        lease = MountManager(layout).load_state()
    except Exception:
        diagnostics.append({"code": "CCBDSK_RUNTIME_UNAVAILABLE", "category": "lease_unreadable"})
    if lease is None:
        diagnostics.append({"code": "CCBDSK_RUNTIME_UNAVAILABLE", "category": "lease_missing"})
    generation = _strict_nonnegative_int(getattr(lease, "generation", None)) if lease is not None else None
    if lease is not None and generation is None:
        diagnostics.append({"code": "CCBDSK_AUTHORITY_UNAVAILABLE", "category": "generation_missing"})
    endpoint: dict[str, Any] | None = None
    if lease is not None:
        try:
            endpoint = validate_unix_endpoint_descriptor(
                {
                    **(getattr(lease, "control_plane_endpoint", None) or {}),
                    "project_id": project_id,
                    "server_generation": generation,
                    "created_at": getattr(lease, "started_at", ""),
                    "owner_uid": getattr(lease, "owner_uid", None),
                },
                project_id=project_id,
                expected_uid=uid,
                require_socket=True,
            )
            peer_uid, peer_source = _probe_endpoint_peer_uid(endpoint["socket_path"], timeout_s=0.2)
            if peer_uid != uid:
                raise DesktopApiError(
                    "CCBDSK_PEER_UID_MISMATCH",
                    "Endpoint peer UID does not match the current user",
                    details={"evidence": "peer_uid_mismatch"},
                )
            endpoint["peer_uid"] = peer_uid
            endpoint["peer_uid_source"] = peer_source
            endpoint["peer_uid_verified"] = True
        except DesktopApiError as exc:
            diagnostics.append({"code": exc.code, "category": str(exc.details.get("evidence") or "endpoint_invalid")})
    public_endpoint = _redacted_endpoint_descriptor(endpoint) if endpoint is not None else None
    state = "running" if lease is not None and endpoint is not None and generation is not None else "unavailable"
    capabilities = [
        {"name": "snapshot", "enabled": False, "reason_code": "CCBDSK_SNAPSHOT_AUTHORITY_UNVERIFIED"},
        {"name": "events", "enabled": False, "reason_code": "CCBDSK_EVENT_AUTHORITY_UNVERIFIED"},
        {"name": "job.submit", "enabled": False, "reason_code": "CCBDSK_CAPABILITY_NOT_VERIFIED"},
    ]
    return {
        "schema_version": DISCOVERY_SCHEMA_VERSION,
        "ccb_version": str(ccb_version or "").strip() or None,
        "project_id": project_id,
        "project_root_display": redacted_display_root(root),
        "desktop_protocols": [DESKTOP_PROTOCOL_VERSION],
        "runtime": {"state": state, "server_generation": generation},
        "endpoint": public_endpoint,
        "endpoint_descriptor": public_endpoint,
        "capabilities": capabilities,
        "diagnostics": diagnostics,
    }


def redacted_display_root(root: str | Path) -> str:
    del root
    return "<project>"


def _discovery_failure(code: str, diagnostics: list[dict[str, str]], *, ccb_version: str | None) -> dict[str, Any]:
    diagnostics = [*diagnostics, {"code": code, "category": "project_validation"}]
    return {
        "schema_version": DISCOVERY_SCHEMA_VERSION,
        "ccb_version": str(ccb_version or "").strip() or None,
        "project_id": None,
        "project_root_display": "<redacted>",
        "desktop_protocols": [DESKTOP_PROTOCOL_VERSION],
        "runtime": {"state": "unavailable", "server_generation": None},
        "endpoint": None,
        "endpoint_descriptor": None,
        "capabilities": [],
        "diagnostics": diagnostics,
    }


def _jobs_from_view(view: dict[str, Any]) -> list[Any]:
    comms = view.get("comms")
    return list(comms.get("jobs") or []) if isinstance(comms, dict) else []


def _redacted_endpoint_descriptor(endpoint: dict[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(endpoint, dict):
        return None
    # Discovery is safe to print and log.  The actual locator remains inside
    # the trusted CCB connection boundary and is never exposed as a path.
    return {
        key: value
        for key, value in endpoint.items()
        if key not in {"socket_path", "legacy_socket_path", "address"}
    } | {"socket_locator": "<redacted-unix-socket>"}


def _probe_endpoint_peer_uid(path: str, *, timeout_s: float) -> tuple[int, str]:
    if os.name == "nt" or not hasattr(socket, "AF_UNIX"):
        raise DesktopApiError(
            "CCBDSK_RUNTIME_UNAVAILABLE",
            "Desktop V1 requires a Unix peer credential API",
            details={"evidence": "unix_transport_required"},
        )
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        client.settimeout(max(0.01, float(timeout_s)))
        client.connect(path)
        credentials = current_peer_credentials(client)
        return credentials.uid, credentials.source
    except DesktopApiError:
        raise
    except (OSError, TimeoutError) as exc:
        raise DesktopApiError(
            "CCBDSK_RUNTIME_UNAVAILABLE",
            "Endpoint peer UID could not be verified",
            retryable=True,
            details={"evidence": "peer_probe_failed"},
        ) from exc
    finally:
        client.close()


def _without_action_transport_fields(params: dict[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in params.items() if key not in {"action_id", "server_generation"}}


def _safe_identifier(value: Any, *, fallback: str) -> str:
    if not isinstance(value, str):
        return fallback
    text = str(value or "").strip()
    if not text or len(text) > _IDENTIFIER_LIMIT or any(char not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789._:-" for char in text):
        return fallback
    return text


def _nonnegative_int(value: Any, *, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return number if number >= 0 else default


def _strict_nonnegative_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    number = int(value)
    return number if number >= 0 else None


def _strict_positive_int(value: Any) -> int | None:
    number = _strict_nonnegative_int(value)
    return number if number is not None and number > 0 else None


def _stable_digest(value: Any) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


__all__ = [
    "DESKTOP_PROTOCOL_VERSION",
    "DISCOVERY_SCHEMA_VERSION",
    "DesktopApiAdapter",
    "DesktopApiError",
    "PeerCredentials",
    "build_discovery",
    "current_peer_credentials",
    "redacted_display_root",
    "validate_unix_endpoint_descriptor",
]
