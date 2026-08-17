from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from project.ids import compute_project_id
from runtime_accelerator.ownership import (
    ProcessIdentity,
    RuntimeAcceleratorOwnershipError,
    inspect_process_identity,
    legacy_runtime_accelerator_pids,
    load_runtime_accelerator_owner,
    owner_manifest_path,
    reclaim_runtime_accelerator,
    record_runtime_accelerator_owner,
    recover_corrupt_runtime_accelerator_owner,
    runtime_accelerator_pid_matches_owner,
)


def _identity(
    project_root: Path,
    socket_path: Path,
    *,
    pid: int = 321,
    start_token: str = "proc:100",
    executable: Path = Path("/opt/ccb/bin/ccb-runtime-accelerator"),
    argv: tuple[str, ...] | None = None,
    cwd: Path | None = None,
) -> ProcessIdentity:
    return ProcessIdentity(
        pid=pid,
        argv=argv
        or (
            "/opt/ccb/bin/ccb-runtime-accelerator",
            "serve",
            "--socket",
            str(socket_path),
        ),
        cwd=project_root if cwd is None else cwd,
        executable=executable,
        start_token=start_token,
    )


def _write_owner(project_root: Path, socket_path: Path, *, pid: int = 321, start_token: str = "proc:100") -> Path:
    path = owner_manifest_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "record_type": "ccb_runtime_accelerator_owner",
                "project_id": compute_project_id(project_root.resolve()),
                "project_root": str(project_root.resolve()),
                "pid": pid,
                "socket_path": str(socket_path.resolve()),
                "executable": "/opt/ccb/bin/ccb-runtime-accelerator",
                "process_start_token": start_token,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return path


def test_recorded_owner_binds_project_socket_process_and_start_token(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    socket_path = tmp_path / "runtime" / "accelerator.sock"
    identity = _identity(project_root.resolve(), socket_path.resolve())
    monkeypatch.setattr("runtime_accelerator.ownership._wait_for_process_identity", lambda pid: identity)

    owner = record_runtime_accelerator_owner(project_root, socket_path=socket_path, pid=321)

    assert owner == load_runtime_accelerator_owner(project_root)
    assert owner.project_root == project_root.resolve()
    assert owner.socket_path == socket_path.resolve()
    assert owner.start_token == "proc:100"


def test_inspect_process_identity_uses_lsof_executable_without_procfs(monkeypatch, tmp_path: Path) -> None:
    project_root = (tmp_path / "project").resolve()
    project_root.mkdir()
    executable = (tmp_path / "bin" / "ccb-runtime-accelerator").resolve()
    socket_path = (tmp_path / "accelerator.sock").resolve()
    monkeypatch.setattr("runtime_accelerator.ownership.is_pid_alive", lambda pid: True)
    monkeypatch.setattr(
        "runtime_accelerator.ownership._read_proc_argv",
        lambda pid: ("ccb-runtime-accelerator", "serve", "--socket", str(socket_path)),
    )
    monkeypatch.setattr("runtime_accelerator.ownership.read_proc_path", lambda pid, entry: None)
    monkeypatch.setattr(
        "runtime_accelerator.ownership._read_process_cwd_via_lsof",
        lambda pid: project_root,
    )
    monkeypatch.setattr(
        "runtime_accelerator.ownership._read_process_executable_via_lsof",
        lambda pid: executable,
    )
    monkeypatch.setattr(
        "runtime_accelerator.ownership._read_process_start_token",
        lambda pid: "ps:Tue Jul 14 16:00:00 2026",
    )

    assert inspect_process_identity(321) == ProcessIdentity(
        pid=321,
        argv=("ccb-runtime-accelerator", "serve", "--socket", str(socket_path)),
        cwd=project_root,
        executable=executable,
        start_token="ps:Tue Jul 14 16:00:00 2026",
    )


def test_lsof_executable_reader_ignores_non_accelerator_text_mappings(monkeypatch) -> None:
    monkeypatch.setattr(
        "runtime_accelerator.ownership.subprocess.run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=0,
            stdout=(
                "p321\n"
                "ftxt\n"
                "n/usr/lib/dyld\n"
                "ftxt\n"
                "n/opt/ccb/bin/ccb-runtime-accelerator\n"
            ),
        ),
    )

    from runtime_accelerator import ownership

    assert ownership._read_process_executable_via_lsof(321) == Path(
        "/opt/ccb/bin/ccb-runtime-accelerator"
    )


def test_recorded_owner_waits_through_pre_exec_identity(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    socket_path = tmp_path / "runtime" / "accelerator.sock"
    pre_exec = ProcessIdentity(
        pid=321,
        argv=("/usr/bin/python", "-m", "pytest"),
        cwd=project_root.resolve(),
        executable=Path("/usr/bin/python"),
        start_token="proc:100",
    )
    accelerated = _identity(project_root.resolve(), socket_path.resolve())
    observed = iter((pre_exec, accelerated))
    monkeypatch.setattr("runtime_accelerator.ownership._wait_for_process_identity", lambda pid: pre_exec)
    monkeypatch.setattr(
        "runtime_accelerator.ownership.inspect_process_identity",
        lambda pid: next(observed),
    )

    owner = record_runtime_accelerator_owner(project_root, socket_path=socket_path, pid=321)

    assert owner == load_runtime_accelerator_owner(project_root)
    assert owner.executable == Path("/opt/ccb/bin/ccb-runtime-accelerator")


def test_recorded_owner_accepts_canonical_socket_alias_in_process_argv(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    runtime_root = tmp_path / "private-tmp"
    runtime_root.mkdir()
    socket_alias_root = tmp_path / "tmp"
    socket_alias_root.symlink_to(runtime_root, target_is_directory=True)
    socket_argument = socket_alias_root / "accelerator.sock"
    socket_path = socket_argument.resolve()
    identity = _identity(
        project_root.resolve(),
        socket_path,
        argv=(
            "/opt/ccb/bin/ccb-runtime-accelerator",
            "serve",
            "--socket",
            str(socket_argument),
        ),
    )
    monkeypatch.setattr("runtime_accelerator.ownership._wait_for_process_identity", lambda pid: identity)

    owner = record_runtime_accelerator_owner(project_root, socket_path=socket_argument, pid=321)

    assert owner.socket_path == socket_path
    assert owner == load_runtime_accelerator_owner(project_root)


def test_recorded_owner_persistent_lookalike_still_fails_closed(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    socket_path = tmp_path / "runtime" / "accelerator.sock"
    lookalike = ProcessIdentity(
        pid=321,
        argv=("/usr/bin/python", "-m", "pytest"),
        cwd=project_root.resolve(),
        executable=Path("/usr/bin/python"),
        start_token="proc:100",
    )
    monkeypatch.setattr("runtime_accelerator.ownership._wait_for_process_identity", lambda pid: lookalike)
    monkeypatch.setattr("runtime_accelerator.ownership.inspect_process_identity", lambda pid: lookalike)
    monkeypatch.setattr("runtime_accelerator.ownership.time.sleep", lambda seconds: None)

    with pytest.raises(RuntimeAcceleratorOwnershipError, match="identity_mismatch"):
        record_runtime_accelerator_owner(project_root, socket_path=socket_path, pid=321)

    assert not owner_manifest_path(project_root).exists()


def test_takeover_reaps_exact_owner_before_removing_socket(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    socket_path = tmp_path / "runtime" / "accelerator.sock"
    socket_path.parent.mkdir()
    socket_path.write_text("stale", encoding="utf-8")
    owner_path = _write_owner(project_root, socket_path)
    events: list[str] = []
    monkeypatch.setattr("runtime_accelerator.ownership.is_pid_alive", lambda pid: True)
    monkeypatch.setattr(
        "runtime_accelerator.ownership.inspect_process_identity",
        lambda pid: _identity(project_root.resolve(), socket_path.resolve(), pid=pid),
    )
    monkeypatch.setattr(
        "runtime_accelerator.ownership.terminate_pid_tree",
        lambda pid, **kwargs: events.append(f"terminate:{pid}") or True,
    )
    monkeypatch.setattr("runtime_accelerator.ownership.legacy_runtime_accelerator_pids", lambda *args, **kwargs: ())
    monkeypatch.setattr(
        "runtime_accelerator.ownership._socket_is_connectable",
        lambda path: events.append(f"socket-check:{path.exists()}") or False,
    )

    reclaimed = reclaim_runtime_accelerator(project_root, socket_path=socket_path)

    assert reclaimed == (321,)
    assert events == ["terminate:321", "socket-check:True"]
    assert not owner_path.exists()
    assert not socket_path.exists()


def test_takeover_does_not_kill_pid_reuse(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    socket_path = tmp_path / "accelerator.sock"
    _write_owner(project_root, socket_path, start_token="proc:old")
    killed: list[int] = []
    monkeypatch.setattr("runtime_accelerator.ownership.is_pid_alive", lambda pid: True)
    monkeypatch.setattr(
        "runtime_accelerator.ownership.inspect_process_identity",
        lambda pid: _identity(project_root.resolve(), socket_path.resolve(), pid=pid, start_token="proc:new"),
    )
    monkeypatch.setattr(
        "runtime_accelerator.ownership.terminate_pid_tree",
        lambda pid, **kwargs: killed.append(pid) or True,
    )
    monkeypatch.setattr("runtime_accelerator.ownership.legacy_runtime_accelerator_pids", lambda *args, **kwargs: (321,))

    assert reclaim_runtime_accelerator(project_root, socket_path=socket_path) == ()
    assert killed == []
    assert not owner_manifest_path(project_root).exists()


def test_pid_reuse_with_active_socket_blocks_and_preserves_owner_evidence(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    socket_path = tmp_path / "accelerator.sock"
    socket_path.write_text("active", encoding="utf-8")
    owner_path = _write_owner(project_root, socket_path, start_token="proc:old")
    monkeypatch.setattr("runtime_accelerator.ownership.is_pid_alive", lambda pid: True)
    monkeypatch.setattr(
        "runtime_accelerator.ownership.inspect_process_identity",
        lambda pid: _identity(project_root.resolve(), socket_path.resolve(), pid=pid, start_token="proc:new"),
    )
    monkeypatch.setattr("runtime_accelerator.ownership.legacy_runtime_accelerator_pids", lambda *args, **kwargs: (321,))
    monkeypatch.setattr("runtime_accelerator.ownership._socket_is_connectable", lambda path: True)
    monkeypatch.setattr(
        "runtime_accelerator.ownership.terminate_pid_tree",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("reused pid must not be killed")),
    )

    with pytest.raises(RuntimeAcceleratorOwnershipError, match="unowned_socket_active"):
        reclaim_runtime_accelerator(project_root, socket_path=socket_path)

    assert owner_path.exists()
    assert socket_path.exists()


@pytest.mark.parametrize(
    "identity",
    (
        lambda root, socket: _identity(root, socket, cwd=root.parent / "other"),
        lambda root, socket: _identity(
            root,
            socket,
            executable=Path("/bin/sh"),
        ),
        lambda root, socket: _identity(
            root,
            socket,
            argv=("/opt/ccb/bin/ccb-runtime-accelerator", "serve", "--socket", str(socket) + ".other"),
        ),
    ),
)
def test_recorded_owner_rejects_lookalike_process(monkeypatch, tmp_path: Path, identity) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    socket_path = tmp_path / "accelerator.sock"
    owner_path = _write_owner(project_root, socket_path)
    monkeypatch.setattr("runtime_accelerator.ownership.is_pid_alive", lambda pid: True)
    monkeypatch.setattr(
        "runtime_accelerator.ownership.inspect_process_identity",
        lambda pid: identity(project_root.resolve(), socket_path.resolve()),
    )
    monkeypatch.setattr(
        "runtime_accelerator.ownership.terminate_pid_tree",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("lookalike must not be killed")),
    )

    with pytest.raises(RuntimeAcceleratorOwnershipError, match="owner_identity_mismatch"):
        reclaim_runtime_accelerator(project_root, socket_path=socket_path)

    assert owner_path.exists()


def test_stale_owner_identity_unavailable_remains_fail_closed(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    socket_path = tmp_path / "accelerator.sock"
    owner_path = _write_owner(project_root, socket_path)
    monkeypatch.setattr("runtime_accelerator.ownership.is_pid_alive", lambda pid: True)
    monkeypatch.setattr("runtime_accelerator.ownership.inspect_process_identity", lambda pid: None)
    monkeypatch.setattr(
        "runtime_accelerator.ownership.terminate_pid_tree",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("unknown owner must not be killed")),
    )

    with pytest.raises(RuntimeAcceleratorOwnershipError, match="owner_identity_unavailable"):
        reclaim_runtime_accelerator(project_root, socket_path=socket_path)

    assert owner_path.exists()


def test_deleted_executable_suffix_is_normalized_for_exact_owner_reclaim(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    socket_path = tmp_path / "accelerator.sock"
    _write_owner(project_root, socket_path)
    terminated: list[int] = []
    monkeypatch.setattr("runtime_accelerator.ownership.is_pid_alive", lambda pid: True)
    monkeypatch.setattr(
        "runtime_accelerator.ownership.inspect_process_identity",
        lambda pid: _identity(
            project_root.resolve(),
            socket_path.resolve(),
            pid=pid,
            executable=Path("/opt/ccb/bin/ccb-runtime-accelerator (deleted)"),
        ),
    )
    monkeypatch.setattr(
        "runtime_accelerator.ownership.terminate_pid_tree",
        lambda pid, **kwargs: terminated.append(pid) or True,
    )
    monkeypatch.setattr("runtime_accelerator.ownership.legacy_runtime_accelerator_pids", lambda *args, **kwargs: ())

    assert reclaim_runtime_accelerator(project_root, socket_path=socket_path) == (321,)
    assert terminated == [321]


@pytest.mark.parametrize(
    "executable",
    (
        Path("/bin/sh (deleted)"),
        Path("/other/ccb-runtime-accelerator (deleted)"),
    ),
)
def test_deleted_suffix_does_not_relax_executable_identity(monkeypatch, tmp_path: Path, executable: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    socket_path = tmp_path / "accelerator.sock"
    owner_path = _write_owner(project_root, socket_path)
    monkeypatch.setattr("runtime_accelerator.ownership.is_pid_alive", lambda pid: True)
    monkeypatch.setattr(
        "runtime_accelerator.ownership.inspect_process_identity",
        lambda pid: _identity(project_root.resolve(), socket_path.resolve(), pid=pid, executable=executable),
    )

    with pytest.raises(RuntimeAcceleratorOwnershipError, match="owner_identity_mismatch"):
        reclaim_runtime_accelerator(project_root, socket_path=socket_path)

    assert owner_path.exists()


def test_owner_pid_match_revalidates_exact_manifest_identity(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    socket_path = tmp_path / "accelerator.sock"
    owner_path = _write_owner(project_root, socket_path)
    monkeypatch.setattr(
        "runtime_accelerator.ownership.inspect_process_identity",
        lambda pid: _identity(project_root.resolve(), socket_path.resolve(), pid=pid),
    )

    assert runtime_accelerator_pid_matches_owner(
        321,
        project_root=project_root,
        manifest_path=owner_path,
    )

    monkeypatch.setattr(
        "runtime_accelerator.ownership.inspect_process_identity",
        lambda pid: _identity(project_root.resolve(), socket_path.resolve(), pid=pid, start_token="proc:reused"),
    )
    assert not runtime_accelerator_pid_matches_owner(
        321,
        project_root=project_root,
        manifest_path=owner_path,
    )


def test_legacy_scan_requires_exact_accelerator_argv_executable_and_cwd(monkeypatch, tmp_path: Path) -> None:
    project_root = (tmp_path / "project").resolve()
    other_root = (tmp_path / "other").resolve()
    project_root.mkdir()
    other_root.mkdir()
    socket_path = (tmp_path / "accelerator.sock").resolve()
    cmdline = f"/opt/ccb/bin/ccb-runtime-accelerator serve --socket {socket_path}"
    identities = {
        101: _identity(project_root, socket_path, pid=101),
        202: _identity(project_root, socket_path, pid=202, executable=Path("/bin/sh")),
        303: _identity(other_root, socket_path, pid=303),
    }
    monkeypatch.setattr("runtime_accelerator.ownership.inspect_process_identity", identities.get)

    assert legacy_runtime_accelerator_pids(
        project_root,
        socket_path=socket_path,
        process_cmdlines={101: cmdline, 202: cmdline, 303: cmdline, 404: "sh editor.py"},
    ) == (101,)


def test_takeover_does_not_touch_other_project_accelerator(monkeypatch, tmp_path: Path) -> None:
    project_root = (tmp_path / "project").resolve()
    other_root = (tmp_path / "other").resolve()
    project_root.mkdir()
    other_root.mkdir()
    socket_path = (tmp_path / "project.sock").resolve()
    other_socket = (tmp_path / "other.sock").resolve()
    other_cmdline = f"/opt/ccb/bin/ccb-runtime-accelerator serve --socket {other_socket}"
    monkeypatch.setattr(
        "runtime_accelerator.ownership.list_process_cmdlines",
        lambda: {707: other_cmdline},
    )
    monkeypatch.setattr(
        "runtime_accelerator.ownership.inspect_process_identity",
        lambda pid: _identity(other_root, other_socket, pid=pid),
    )
    monkeypatch.setattr(
        "runtime_accelerator.ownership.terminate_pid_tree",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("other project must not be killed")),
    )

    assert reclaim_runtime_accelerator(project_root, socket_path=socket_path) == ()


def test_takeover_reaps_all_legacy_accelerators_for_project_socket(monkeypatch, tmp_path: Path) -> None:
    project_root = (tmp_path / "project").resolve()
    project_root.mkdir()
    socket_path = (tmp_path / "accelerator.sock").resolve()
    cmdline = f"/opt/ccb/bin/ccb-runtime-accelerator serve --socket {socket_path}"
    monkeypatch.setattr(
        "runtime_accelerator.ownership.list_process_cmdlines",
        lambda: {808: cmdline, 909: cmdline},
    )
    monkeypatch.setattr(
        "runtime_accelerator.ownership.inspect_process_identity",
        lambda pid: _identity(project_root, socket_path, pid=pid),
    )
    terminated: list[int] = []
    monkeypatch.setattr(
        "runtime_accelerator.ownership.terminate_pid_tree",
        lambda pid, **kwargs: terminated.append(pid) or True,
    )

    assert reclaim_runtime_accelerator(project_root, socket_path=socket_path) == (808, 909)
    assert terminated == [808, 909]


def test_normal_recovery_preserves_corrupt_owner_with_warning(tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    socket_path = tmp_path / "accelerator.sock"
    manifest_path = owner_manifest_path(project_root)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text("{invalid\n", encoding="utf-8")

    result = recover_corrupt_runtime_accelerator_owner(
        project_root,
        socket_path=socket_path,
        force=False,
    )

    assert result.status == "blocked"
    assert "force_required" in result.warning
    assert manifest_path.exists()


def test_force_recovery_removes_corrupt_owner_only_after_exact_legacy_reap(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    socket_path = tmp_path / "accelerator.sock"
    socket_path.write_text("stale", encoding="utf-8")
    manifest_path = owner_manifest_path(project_root)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text("{invalid\n", encoding="utf-8")
    scans = iter(((321,), ()))
    events: list[str] = []
    monkeypatch.setattr(
        "runtime_accelerator.ownership.legacy_runtime_accelerator_pids",
        lambda *args, **kwargs: next(scans),
    )
    monkeypatch.setattr(
        "runtime_accelerator.ownership.terminate_pid_tree",
        lambda pid, **kwargs: events.append(f"terminate:{pid}") or True,
    )
    monkeypatch.setattr(
        "runtime_accelerator.ownership._socket_is_connectable",
        lambda path: events.append("socket-check") or False,
    )

    result = recover_corrupt_runtime_accelerator_owner(
        project_root,
        socket_path=socket_path,
        force=True,
    )

    assert result.status == "recovered"
    assert result.reclaimed_pids == (321,)
    assert events == ["terminate:321", "socket-check"]
    assert not manifest_path.exists()
    assert not socket_path.exists()


def test_force_recovery_without_exact_legacy_identity_preserves_corrupt_owner(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    socket_path = tmp_path / "accelerator.sock"
    manifest_path = owner_manifest_path(project_root)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text("{invalid\n", encoding="utf-8")
    monkeypatch.setattr(
        "runtime_accelerator.ownership.legacy_runtime_accelerator_pids",
        lambda *args, **kwargs: (),
    )

    result = recover_corrupt_runtime_accelerator_owner(
        project_root,
        socket_path=socket_path,
        force=True,
    )

    assert result.status == "blocked"
    assert "exact_legacy_identity_not_found" in result.warning
    assert manifest_path.exists()


def test_force_recovery_preserves_corrupt_owner_when_socket_remains_active(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_root.mkdir()
    socket_path = tmp_path / "accelerator.sock"
    socket_path.write_text("active", encoding="utf-8")
    manifest_path = owner_manifest_path(project_root)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text("{invalid\n", encoding="utf-8")
    scans = iter(((321,), ()))
    monkeypatch.setattr(
        "runtime_accelerator.ownership.legacy_runtime_accelerator_pids",
        lambda *args, **kwargs: next(scans),
    )
    monkeypatch.setattr("runtime_accelerator.ownership.terminate_pid_tree", lambda *args, **kwargs: True)
    monkeypatch.setattr("runtime_accelerator.ownership._socket_is_connectable", lambda path: True)

    result = recover_corrupt_runtime_accelerator_owner(
        project_root,
        socket_path=socket_path,
        force=True,
    )

    assert result.status == "blocked"
    assert "socket_still_connectable" in result.warning
    assert manifest_path.exists()
