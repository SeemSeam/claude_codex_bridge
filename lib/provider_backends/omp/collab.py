"""Owner-bound, bounded local IPC for the managed OMP Collab guest.

The bridge, not this client, holds Collab capabilities and the durable FIFO.
Queued receipts confirm local admission only; upstream protocol v3 has no
request acknowledgement. Handed-off prompts are never automatically replayed.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import stat
import struct
import threading
from pathlib import Path

from provider_core.pathing import session_filename_for_agent
from .socket_paths import socket_temp_root

MAX_PROMPT_BYTES = 256 * 1024


class OmpCollabError(RuntimeError):
    def __init__(self, message: str, status_code: int = 503):
        super().__init__(message)
        self.status_code = status_code


class OmpCollabClient:
    def __init__(self, timeout: float = 2.0):
        self.timeout = timeout
        self._locks: dict[tuple[str, str], threading.Lock] = {}
        self._guard = threading.Lock()

    def close_all(self) -> None:
        # Requests use short-lived sockets; no background threads or connections.
        with self._guard:
            self._locks.clear()

    def _request(self, project_root: Path, agent: str, operation: str, **fields) -> dict:
        root = Path(project_root).resolve()
        if not re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", agent) or agent in {".", ".."}:
            raise OmpCollabError("Invalid OMP agent", 409)
        session = root / ".ccb" / session_filename_for_agent("omp", agent)
        try:
            metadata = session.lstat()
            if not stat.S_ISREG(metadata.st_mode) or metadata.st_uid != os.getuid():
                raise OmpCollabError("Unsafe OMP session binding")
            if metadata.st_size > 1024 * 1024:
                raise OmpCollabError("Invalid OMP session binding")
            data = json.loads(session.read_text())
            if (data.get("provider") != "omp" or data.get("agent_name") != agent
                    or data.get("active") is False
                    or Path(data.get("start_dir") or "").resolve() != root
                    or not data.get("ccb_session_id")):
                raise OmpCollabError("OMP session binding changed", 409)
            runtime = Path(data["runtime_dir"]).resolve()
            endpoint = Path(data["omp_collab_socket"])
            token = hashlib.sha256(f"{runtime}:{data['ccb_session_id']}".encode()).hexdigest()[:20]
            short_endpoint = socket_temp_root() / f"ccb-omp-{os.getuid()}-{token}" / "bridge.sock"
            if not endpoint.is_absolute() or (endpoint != short_endpoint and endpoint.parent.resolve() != runtime / "collab"):
                raise OmpCollabError("Unsafe OMP Collab endpoint")
            parent = endpoint.parent.lstat()
            info = endpoint.lstat()
            if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.getuid()
                    or parent.st_mode & 0o077 or not stat.S_ISSOCK(info.st_mode)
                    or info.st_uid != os.getuid() or info.st_mode & 0o077):
                raise OmpCollabError("Unsafe OMP Collab endpoint")
        except OmpCollabError:
            raise
        except (OSError, ValueError, TypeError, KeyError):
            raise OmpCollabError("Managed OMP Collab unavailable") from None
        key = (str(root), agent)
        with self._guard:
            if key not in self._locks:
                if len(self._locks) >= 256:
                    raise OmpCollabError("OMP Collab client capacity reached")
                self._locks[key] = threading.Lock()
            lock = self._locks[key]
        # Concurrent sends wait their turn locally; a running provider is not
        # an admission conflict. The runtime owns the durable bounded FIFO.
        lock.acquire()
        handed_off = False
        try:
            request = {"operation": operation, "actor": agent,
                       "launch_session_id": data["ccb_session_id"], **fields}
            encoded = (json.dumps(request) + "\n").encode()
            if len(encoded) > 1024 * 1024:
                raise OmpCollabError("OMP prompt too large", 409)
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
                client.settimeout(self.timeout)
                client.connect(str(endpoint))
                if hasattr(socket, "SO_PEERCRED"):
                    _, uid, _ = struct.unpack("3i", client.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                    if uid != os.getuid():
                        raise OmpCollabError("OMP Collab owner mismatch")
                handed_off = True
                client.sendall(encoded)
                chunks = bytearray()
                while b"\n" not in chunks:
                    chunk = client.recv(65536)
                    if not chunk:
                        raise OSError("bridge disconnected")
                    chunks.extend(chunk)
                    if len(chunks) > 64 * 1024 * 1024:
                        raise OSError("bridge response exceeds budget")
                response = json.loads(chunks.split(b"\n", 1)[0])
            if response.get("actor") != agent or response.get("launch_session_id") != data["ccb_session_id"]:
                raise OmpCollabError("OMP Collab response binding changed", 409)
            if response.get("error"):
                raise OmpCollabError(str(response["error"]), int(response.get("status_code", 503)))
            return response["result"]
        except OmpCollabError:
            raise
        except (OSError, ValueError, KeyError, TypeError):
            raise OmpCollabError(
                "OMP delivery unknown; do not retry" if handed_off and operation in {"submit", "set_model"} else "OMP Collab disconnected",
                502 if handed_off and operation in {"submit", "set_model"} else 503,
            ) from None
        finally:
            lock.release()

    def get_snapshot(self, project_root: Path, agent: str) -> dict:
        return self._request(project_root, agent, "snapshot")

    def submit(self, project_root: Path, agent: str, body: str, idempotency_key: str, *,
               expected_session_id: str | None = None, expected_generation: object = None) -> dict:
        if not isinstance(body, str) or not body.strip() or not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 256:
            raise OmpCollabError("Invalid OMP prompt", 409)
        if len(body.encode("utf-8")) > MAX_PROMPT_BYTES:
            raise OmpCollabError("OMP prompt too large", 409)
        if not isinstance(expected_session_id, str) or not expected_session_id or expected_generation is None:
            raise OmpCollabError("Refresh OMP conversation before submitting", 409)
        return self._request(project_root, agent, "submit", body=body, idempotency_key=idempotency_key,
                             expected_session_id=expected_session_id, expected_generation=expected_generation)

    def get_models(self, project_root: Path, agent: str) -> dict:
        return self._request(project_root, agent, "models")

    def set_model(self, project_root: Path, agent: str, model: dict, thinking: str | None, *,
                  expected_session_id: str, expected_generation: object,
                  expected_model: dict | None, expected_thinking: str | None,
                  idempotency_key: str, expected_model_revision: str | None = None) -> dict:
        if (not isinstance(model, dict) or not all(isinstance(model.get(key), str) and 0 < len(model[key]) <= 512 for key in ("provider", "id"))
                or not isinstance(expected_session_id, str) or not expected_session_id or expected_generation is None
                or not isinstance(expected_model_revision, str) or not 1 <= len(expected_model_revision) <= 2048
                or not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 256
                or (thinking is not None and (not isinstance(thinking, str) or thinking not in {"off", "minimal", "low", "medium", "high", "xhigh", "max"}))):
            raise OmpCollabError("Invalid OMP model request or missing conversation fence", 409)
        return self._request(project_root, agent, "set_model", model={"provider": model["provider"], "id": model["id"]},
                             thinking=thinking, expected_session_id=expected_session_id, expected_generation=expected_generation,
                             expected_model=expected_model, expected_thinking=expected_thinking or "off",
                             expected_model_revision=expected_model_revision, idempotency_key=idempotency_key)
