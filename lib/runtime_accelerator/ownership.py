from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from cli.kill_runtime.processes import is_pid_alive, terminate_pid_tree
from project.ids import compute_project_id
from runtime_pid_cleanup.procfs import list_process_cmdlines, read_proc_cmdline, read_proc_path
from storage.atomic import atomic_write_json
from storage.paths import PathLayout

_OWNER_SCHEMA_VERSION = 1
_OWNER_RECORD_TYPE = "ccb_runtime_accelerator_owner"
_OWNER_FILE_NAME = "runtime-accelerator.json"
_LEGACY_MARKER_NAME = "runtime-accelerator.legacy"


class RuntimeAcceleratorOwnershipError(RuntimeError):
    pass


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    argv: tuple[str, ...]
    cwd: Path | None
    executable: Path | None
    start_token: str


@dataclass(frozen=True)
class RuntimeAcceleratorOwner:
    project_id: str
    project_root: Path
    pid: int
    socket_path: Path
    executable: Path
    start_token: str


@dataclass(frozen=True)
class CorruptOwnerRecovery:
    status: str
    reclaimed_pids: tuple[int, ...] = ()
    warning: str = ""


def owner_manifest_path(project_root: str | Path) -> Path:
    return PathLayout(Path(project_root)).ccbd_dir / _OWNER_FILE_NAME


def legacy_marker_path(project_root: str | Path) -> Path:
    return PathLayout(Path(project_root)).ccbd_dir / _LEGACY_MARKER_NAME


def load_runtime_accelerator_owner(project_root: str | Path) -> RuntimeAcceleratorOwner | None:
    root = _resolved_path(project_root)
    path = owner_manifest_path(root)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except Exception as exc:
        raise RuntimeAcceleratorOwnershipError(f"runtime_accelerator_owner_invalid:{path}:{exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeAcceleratorOwnershipError(f"runtime_accelerator_owner_invalid:{path}:not_object")
    expected = {
        "schema_version": _OWNER_SCHEMA_VERSION,
        "record_type": _OWNER_RECORD_TYPE,
        "project_id": compute_project_id(root),
        "project_root": str(root),
    }
    for key, value in expected.items():
        if payload.get(key) != value:
            raise RuntimeAcceleratorOwnershipError(
                f"runtime_accelerator_owner_invalid:{path}:{key}={payload.get(key)!r} expected={value!r}"
            )
    try:
        pid = int(payload["pid"])
        socket_path = _resolved_path(payload["socket_path"])
        executable = _normalized_executable_path(payload["executable"])
        start_token = str(payload["process_start_token"]).strip()
    except Exception as exc:
        raise RuntimeAcceleratorOwnershipError(f"runtime_accelerator_owner_invalid:{path}:{exc}") from exc
    if pid <= 0 or not start_token or not _is_accelerator_executable(executable):
        raise RuntimeAcceleratorOwnershipError(f"runtime_accelerator_owner_invalid:{path}:identity")
    return RuntimeAcceleratorOwner(
        project_id=expected["project_id"],
        project_root=root,
        pid=pid,
        socket_path=socket_path,
        executable=executable,
        start_token=start_token,
    )


def record_runtime_accelerator_owner(
    project_root: str | Path,
    *,
    socket_path: str | Path,
    pid: int,
) -> RuntimeAcceleratorOwner:
    root = _resolved_path(project_root)
    socket = _resolved_path(socket_path)
    identity = _wait_for_process_identity(pid)
    if identity is None:
        raise RuntimeAcceleratorOwnershipError(f"runtime_accelerator_identity_unavailable:pid={pid}")
    identity = _wait_for_accelerator_identity(
        pid,
        initial=identity,
        project_root=root,
        socket_path=socket,
    )
    if not _matches_accelerator_process(identity, project_root=root, socket_path=socket):
        raise RuntimeAcceleratorOwnershipError(
            "runtime_accelerator_identity_mismatch:"
            f"pid={pid}:argv={identity.argv!r}:cwd={identity.cwd}:"
            f"executable={identity.executable}:start_token={identity.start_token!r}"
        )
    assert identity.executable is not None
    owner = RuntimeAcceleratorOwner(
        project_id=compute_project_id(root),
        project_root=root,
        pid=pid,
        socket_path=socket,
        executable=_normalized_executable_path(identity.executable),
        start_token=identity.start_token,
    )
    atomic_write_json(
        owner_manifest_path(root),
        {
            "schema_version": _OWNER_SCHEMA_VERSION,
            "record_type": _OWNER_RECORD_TYPE,
            "project_id": owner.project_id,
            "project_root": str(owner.project_root),
            "pid": owner.pid,
            "socket_path": str(owner.socket_path),
            "executable": str(owner.executable),
            "process_start_token": owner.start_token,
        },
    )
    return owner


def reclaim_runtime_accelerator(project_root: str | Path, *, socket_path: str | Path) -> tuple[int, ...]:
    root = _resolved_path(project_root)
    socket = _resolved_path(socket_path)
    reclaimed: list[int] = []
    excluded_legacy_pids: set[int] = set()
    owner = load_runtime_accelerator_owner(root)
    if owner is not None:
        owner_result = _reclaim_recorded_owner(owner)
        if owner_result == "reclaimed":
            reclaimed.append(owner.pid)
        elif owner_result == "pid_reused":
            excluded_legacy_pids.add(owner.pid)

    for pid in legacy_runtime_accelerator_pids(root, socket_path=socket):
        if pid in reclaimed or pid in excluded_legacy_pids:
            continue
        if not terminate_pid_tree(pid, timeout_s=1.0, is_pid_alive_fn=is_pid_alive):
            raise RuntimeAcceleratorOwnershipError(f"runtime_accelerator_takeover_failed:pid={pid}")
        reclaimed.append(pid)

    if socket.exists() and _socket_is_connectable(socket):
        raise RuntimeAcceleratorOwnershipError(f"runtime_accelerator_unowned_socket_active:{socket}")
    socket.unlink(missing_ok=True)
    if owner is not None:
        owner_manifest_path(root).unlink(missing_ok=True)
    return tuple(reclaimed)


def runtime_accelerator_pid_matches_owner(
    pid: int,
    *,
    project_root: str | Path,
    manifest_path: str | Path,
) -> bool:
    if Path(manifest_path).name != _OWNER_FILE_NAME:
        return False
    try:
        owner = load_runtime_accelerator_owner(project_root)
    except RuntimeAcceleratorOwnershipError:
        return False
    if owner is None or owner.pid != int(pid):
        return False
    identity = inspect_process_identity(pid)
    return identity is not None and _owner_matches_identity(owner, identity)


def recover_corrupt_runtime_accelerator_owner(
    project_root: str | Path,
    *,
    socket_path: str | Path,
    force: bool,
) -> CorruptOwnerRecovery:
    root = _resolved_path(project_root)
    socket = _resolved_path(socket_path)
    manifest_path = owner_manifest_path(root)
    if not manifest_path.exists():
        return CorruptOwnerRecovery(status="absent")
    try:
        owner = load_runtime_accelerator_owner(root)
    except RuntimeAcceleratorOwnershipError as exc:
        invalid_reason = str(exc)
    else:
        return CorruptOwnerRecovery(status="valid" if owner is not None else "absent")
    if not force:
        return CorruptOwnerRecovery(
            status="blocked",
            warning=f"runtime_accelerator_corrupt_owner_preserved:force_required:{invalid_reason}",
        )

    verified_pids = legacy_runtime_accelerator_pids(root, socket_path=socket)
    if not verified_pids:
        return CorruptOwnerRecovery(
            status="blocked",
            warning="runtime_accelerator_corrupt_owner_preserved:exact_legacy_identity_not_found",
        )
    reclaimed: list[int] = []
    for pid in verified_pids:
        if not terminate_pid_tree(pid, timeout_s=1.0, is_pid_alive_fn=is_pid_alive):
            return CorruptOwnerRecovery(
                status="blocked",
                reclaimed_pids=tuple(reclaimed),
                warning=f"runtime_accelerator_corrupt_owner_preserved:terminate_failed:pid={pid}",
            )
        reclaimed.append(pid)
    remaining = legacy_runtime_accelerator_pids(root, socket_path=socket)
    if remaining:
        return CorruptOwnerRecovery(
            status="blocked",
            reclaimed_pids=tuple(reclaimed),
            warning=(
                "runtime_accelerator_corrupt_owner_preserved:verified_processes_remain:"
                + ",".join(str(pid) for pid in remaining)
            ),
        )
    if socket.exists() and _socket_is_connectable(socket):
        return CorruptOwnerRecovery(
            status="blocked",
            reclaimed_pids=tuple(reclaimed),
            warning="runtime_accelerator_corrupt_owner_preserved:socket_still_connectable",
        )
    try:
        socket.unlink(missing_ok=True)
        if socket.exists():
            raise OSError("socket still exists")
        manifest_path.unlink()
    except OSError as exc:
        return CorruptOwnerRecovery(
            status="blocked",
            reclaimed_pids=tuple(reclaimed),
            warning=f"runtime_accelerator_corrupt_owner_preserved:cleanup_failed:{exc}",
        )
    return CorruptOwnerRecovery(status="recovered", reclaimed_pids=tuple(reclaimed))


def runtime_accelerator_pid_matches_legacy(pid: int, *, project_root: str | Path) -> bool:
    root = _resolved_path(project_root)
    identity = inspect_process_identity(pid)
    if identity is None:
        return False
    from .config import accelerator_socket_path

    socket = accelerator_socket_path(root)
    return socket is not None and _matches_accelerator_process(
        identity,
        project_root=root,
        socket_path=_resolved_path(socket),
    )


def legacy_runtime_accelerator_pids(
    project_root: str | Path,
    *,
    socket_path: str | Path,
    process_cmdlines: dict[int, str] | None = None,
) -> tuple[int, ...]:
    root = _resolved_path(project_root)
    socket = _resolved_path(socket_path)
    process_cmdlines = process_cmdlines if process_cmdlines is not None else list_process_cmdlines()
    matches: list[int] = []
    for raw_pid, cmdline in process_cmdlines.items():
        try:
            pid = int(raw_pid)
        except (TypeError, ValueError):
            continue
        argv = _split_cmdline(str(cmdline or ""))
        if not _argv_matches_accelerator(argv, socket_path=socket):
            continue
        identity = inspect_process_identity(pid)
        if identity is not None and _matches_accelerator_process(
            identity,
            project_root=root,
            socket_path=socket,
        ):
            matches.append(pid)
    return tuple(sorted(matches))


def remove_runtime_accelerator_owner(project_root: str | Path, *, pid: int) -> None:
    root = _resolved_path(project_root)
    try:
        owner = load_runtime_accelerator_owner(root)
    except RuntimeAcceleratorOwnershipError:
        return
    if owner is not None and owner.pid == int(pid):
        owner_manifest_path(root).unlink(missing_ok=True)


def runtime_accelerator_socket_is_connectable(socket_path: str | Path) -> bool:
    return _socket_is_connectable(_resolved_path(socket_path))


def inspect_process_identity(pid: int) -> ProcessIdentity | None:
    if int(pid) <= 0 or not is_pid_alive(int(pid)):
        return None
    argv = _read_proc_argv(pid)
    if not argv:
        argv = _split_cmdline(read_proc_cmdline(pid))
    cwd = read_proc_path(pid, "cwd") or _read_process_cwd_via_lsof(pid)
    executable = read_proc_path(pid, "exe") or _read_process_executable_via_lsof(pid)
    if executable is None and argv:
        executable = _resolve_executable(argv[0], cwd=cwd)
    start_token = _read_process_start_token(pid)
    if not argv or cwd is None or executable is None or not start_token:
        return None
    return ProcessIdentity(
        pid=int(pid),
        argv=argv,
        cwd=_resolved_path(cwd),
        executable=_resolved_path(executable),
        start_token=start_token,
    )


def _reclaim_recorded_owner(owner: RuntimeAcceleratorOwner) -> str:
    if not is_pid_alive(owner.pid):
        return "stale"
    identity = inspect_process_identity(owner.pid)
    if identity is None:
        raise RuntimeAcceleratorOwnershipError(
            f"runtime_accelerator_owner_identity_unavailable:pid={owner.pid}"
        )
    if identity.start_token != owner.start_token:
        return "pid_reused"
    if not _owner_matches_identity(owner, identity):
        raise RuntimeAcceleratorOwnershipError(f"runtime_accelerator_owner_identity_mismatch:pid={owner.pid}")
    if not terminate_pid_tree(owner.pid, timeout_s=1.0, is_pid_alive_fn=is_pid_alive):
        raise RuntimeAcceleratorOwnershipError(f"runtime_accelerator_takeover_failed:pid={owner.pid}")
    return "reclaimed"


def _owner_matches_identity(owner: RuntimeAcceleratorOwner, identity: ProcessIdentity) -> bool:
    observed_executable = (
        _normalized_executable_path(identity.executable) if identity.executable is not None else None
    )
    return (
        identity.pid == owner.pid
        and identity.start_token == owner.start_token
        and observed_executable == owner.executable
        and _matches_accelerator_process(
            identity,
            project_root=owner.project_root,
            socket_path=owner.socket_path,
        )
    )


def _matches_accelerator_process(
    identity: ProcessIdentity,
    *,
    project_root: Path,
    socket_path: Path,
) -> bool:
    executable = _normalized_executable_path(identity.executable) if identity.executable is not None else None
    return (
        identity.cwd == project_root
        and executable is not None
        and _is_accelerator_executable(executable)
        and _argv_matches_accelerator(identity.argv, socket_path=socket_path)
    )


def _argv_matches_accelerator(argv: tuple[str, ...], *, socket_path: Path) -> bool:
    if len(argv) != 4 or not _is_accelerator_executable(Path(argv[0])):
        return False
    return argv[1:3] == ("serve", "--socket") and _resolved_path(argv[3]) == socket_path


def _is_accelerator_executable(path: Path) -> bool:
    return path.name in {"ccb-runtime-accelerator", "ccb-runtime-accelerator.exe"}


def _normalized_executable_path(value: str | Path) -> Path:
    text = str(value)
    if text.endswith(" (deleted)"):
        text = text[: -len(" (deleted)")]
    return _resolved_path(text)


def _wait_for_process_identity(pid: int, *, timeout_s: float = 0.25) -> ProcessIdentity | None:
    deadline = time.monotonic() + max(0.0, timeout_s)
    while True:
        identity = inspect_process_identity(pid)
        if identity is not None:
            return identity
        if time.monotonic() >= deadline:
            return None
        time.sleep(0.01)


def _wait_for_accelerator_identity(
    pid: int,
    *,
    initial: ProcessIdentity,
    project_root: Path,
    socket_path: Path,
    timeout_s: float = 0.25,
) -> ProcessIdentity:
    """Wait through the fork-to-exec window without accepting a lookalike."""

    identity = initial
    deadline = time.monotonic() + max(0.0, timeout_s)
    while not _matches_accelerator_process(
        identity,
        project_root=project_root,
        socket_path=socket_path,
    ):
        if time.monotonic() >= deadline:
            return identity
        time.sleep(0.01)
        observed = inspect_process_identity(pid)
        if observed is not None:
            identity = observed
    return identity


def _read_proc_argv(pid: int) -> tuple[str, ...]:
    try:
        raw = Path(f"/proc/{int(pid)}/cmdline").read_bytes()
    except Exception:
        return ()
    return tuple(part.decode("utf-8", errors="surrogateescape") for part in raw.split(b"\0") if part)


def _read_process_start_token(pid: int) -> str:
    try:
        text = Path(f"/proc/{int(pid)}/stat").read_text(encoding="utf-8")
        fields = text.rsplit(") ", 1)[1].split()
        if len(fields) > 19:
            return f"proc:{fields[19]}"
    except Exception:
        pass
    try:
        result = subprocess.run(
            ["ps", "-p", str(int(pid)), "-o", "lstart="],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:
        return ""
    value = str(result.stdout or "").strip()
    return f"ps:{value}" if result.returncode == 0 and value else ""


def _read_process_cwd_via_lsof(pid: int) -> Path | None:
    try:
        result = subprocess.run(
            ["lsof", "-a", "-p", str(int(pid)), "-d", "cwd", "-Fn"],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    for line in str(result.stdout or "").splitlines():
        if line.startswith("n") and len(line) > 1:
            return Path(line[1:])
    return None


def _read_process_executable_via_lsof(pid: int) -> Path | None:
    try:
        result = subprocess.run(
            ["lsof", "-a", "-p", str(int(pid)), "-d", "txt", "-Fn"],
            check=False,
            capture_output=True,
            text=True,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    for line in str(result.stdout or "").splitlines():
        if not line.startswith("n") or len(line) <= 1:
            continue
        candidate = Path(line[1:])
        if _is_accelerator_executable(candidate):
            return candidate
    return None


def _resolve_executable(value: str, *, cwd: Path | None) -> Path | None:
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate
    if "/" in value and cwd is not None:
        return cwd / candidate
    found = shutil.which(value)
    return Path(found) if found else None


def _split_cmdline(value: str) -> tuple[str, ...]:
    try:
        return tuple(shlex.split(value, posix=os.name != "nt"))
    except ValueError:
        return ()


def _socket_is_connectable(path: Path) -> bool:
    import socket

    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(0.1)
            client.connect(str(path))
        return True
    except OSError:
        return False


def _resolved_path(value: str | Path) -> Path:
    path = Path(value).expanduser()
    try:
        return path.resolve()
    except Exception:
        return path.absolute()


__all__ = [
    "CorruptOwnerRecovery",
    "ProcessIdentity",
    "RuntimeAcceleratorOwner",
    "RuntimeAcceleratorOwnershipError",
    "inspect_process_identity",
    "legacy_marker_path",
    "legacy_runtime_accelerator_pids",
    "load_runtime_accelerator_owner",
    "owner_manifest_path",
    "recover_corrupt_runtime_accelerator_owner",
    "reclaim_runtime_accelerator",
    "record_runtime_accelerator_owner",
    "remove_runtime_accelerator_owner",
    "runtime_accelerator_pid_matches_legacy",
    "runtime_accelerator_pid_matches_owner",
    "runtime_accelerator_socket_is_connectable",
]
