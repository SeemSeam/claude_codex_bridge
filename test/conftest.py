from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import time
from pathlib import Path

import pytest

repo_root = Path(__file__).resolve().parents[1]
lib_dir = repo_root / "lib"
if str(lib_dir) not in sys.path:
    sys.path.insert(0, str(lib_dir))

import project.resolver as project_resolver_module
from storage.paths import PathLayout


def pytest_configure() -> None:
    if str(lib_dir) not in sys.path:
        sys.path.insert(0, str(lib_dir))


def _write_provider_stub_launchers(bin_dir: Path) -> None:
    stub_path = (repo_root / "test" / "stubs" / "provider_stub.py").resolve()
    python_exe = sys.executable
    providers = (
        "codex", "gemini", "claude", "opencode", "droid", "agy", "kimi", "deepcode", "grok", "qodercli", "qoderclicn",
    )
    for provider in providers:
        posix_launcher = bin_dir / provider
        posix_launcher.write_text(
            "\n".join(
                [
                    "#!/bin/sh",
                    f'exec "{python_exe}" "{stub_path}" --provider {_stub_provider_name(provider)} "$@"',
                    "",
                ]
            ),
            encoding="utf-8",
        )
        posix_launcher.chmod(posix_launcher.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

        windows_launcher = bin_dir / f"{provider}.cmd"
        windows_launcher.write_text(
            f'@"{python_exe}" "{stub_path}" --provider {_stub_provider_name(provider)} %*\r\n',
            encoding="utf-8",
        )


def _stub_provider_name(provider: str) -> str:
    if provider == "deepcode":
        return "deepseek"
    if provider == "qodercli":
        return "qoder"
    if provider == "qoderclicn":
        return "qodercn"
    return provider


@pytest.fixture(autouse=True)
def _ignore_host_level_tmp_anchor(monkeypatch, tmp_path_factory) -> None:
    original = project_resolver_module.find_parent_project_anchor_dir
    pytest_tmp_root = tmp_path_factory.getbasetemp().resolve()

    def _patched(path: Path):
        result = original(path)
        if result is None:
            return None
        anchor_root = result.parent.resolve()
        if pytest_tmp_root.is_relative_to(anchor_root) and not anchor_root.is_relative_to(pytest_tmp_root):
            return None
        return result

    monkeypatch.setattr(project_resolver_module, 'find_parent_project_anchor_dir', _patched)


@pytest.fixture(autouse=True)
def _install_provider_stubs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    home_dir = tmp_path / ".home"
    bin_dir = tmp_path / ".stub-bin"
    home_dir.mkdir(parents=True, exist_ok=True)
    bin_dir.mkdir(parents=True, exist_ok=True)
    _write_provider_stub_launchers(bin_dir)

    for name in (
        "CCB_CALLER_ACTOR",
        "CCB_CALLER_PROJECT_ID",
        "CCB_CALLER_PROJECT_ROOT",
        "CCB_CALLER_RUNTIME_DIR",
        "CCB_SESSION_ID",
        "CODEX_RUNTIME_DIR",
    ):
        monkeypatch.delenv(name, raising=False)

    path_entries = [str(bin_dir)]
    existing_path = os.environ.get("PATH")
    if existing_path:
        path_entries.append(existing_path)

    monkeypatch.setenv("HOME", str(home_dir))
    monkeypatch.setenv("USERPROFILE", str(home_dir))
    monkeypatch.setenv("PATH", os.pathsep.join(path_entries))
    monkeypatch.setenv("STUB_DELAY", "1.5")
    monkeypatch.setenv("CCB_REPLY_LANG", "en")
    monkeypatch.setenv("CCB_CLAUDE_SKILLS", "0")
    monkeypatch.delenv("CCB_KEEPER_PID", raising=False)


class _Phase2RuntimeOwner:
    def __init__(self, tmp_root: Path) -> None:
        self.tmp_root = tmp_root.resolve()
        self.project_roots: dict[Path, bool] = {}

    def track(self, project_root: Path, *, in_process: bool = False) -> None:
        root = project_root.resolve()
        root.relative_to(self.tmp_root)
        self.project_roots[root] = self.project_roots.get(root, False) or in_process

    def cleanup(self) -> None:
        failures = []
        for project_root, in_process in reversed(tuple(self.project_roots.items())):
            if in_process:
                _clear_unmounted_in_process_authority(project_root)
            result = _kill_test_project(project_root)
            if result.returncode != 0:
                failures.append(
                    f'{project_root}: rc={result.returncode} stdout={result.stdout!r} stderr={result.stderr!r}'
                )
        residue = [
            *_wait_for_process_residue(self.tmp_root, timeout=3.0),
            *_wait_for_socket_residue(self.tmp_root, timeout=3.0),
        ]
        if failures or residue:
            raise AssertionError(
                'phase2 test-owned runtime cleanup failed:\n' + '\n'.join([*failures, *residue])
            )


@pytest.fixture
def phase2_runtime_owner(tmp_path: Path, _install_provider_stubs):
    del _install_provider_stubs
    owner = _Phase2RuntimeOwner(tmp_path)
    yield owner
    owner.cleanup()


def _kill_test_project(project_root: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    for name in tuple(env):
        if name in {'CCB_KEEPER_PID', 'CCB_SESSION_FILE', 'CCB_SESSION_ID'}:
            env.pop(name, None)
            continue
        if name.startswith(('CCB_CALLER_', 'CODEX_', 'CLAUDE_', 'GEMINI_', 'OPENCODE_', 'DROID_')):
            env.pop(name, None)
    return subprocess.run(
        [sys.executable, str(repo_root / 'ccb.py'), 'kill', '-f'],
        cwd=str(project_root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=30.0,
    )


def _wait_for_process_residue(tmp_root: Path, *, timeout: float) -> list[str]:
    deadline = time.time() + timeout
    residue: list[str] = []
    while time.time() < deadline:
        residue = _process_lines_containing(tmp_root)
        if not residue:
            return []
        time.sleep(0.05)
    return residue


def _process_lines_containing(path: Path) -> list[str]:
    result = subprocess.run(
        ['ps', '-eo', 'pid=,ppid=,args='],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(f'failed to inspect test-owned processes: {result.stderr}')
    root = path.resolve()
    marker = str(root)
    current_pid = os.getpid()
    residue: list[str] = []
    for line in result.stdout.splitlines():
        parts = line.strip().split(None, 2)
        if not parts or parts[0] == str(current_pid):
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        cwd = _process_cwd(pid)
        if marker in line or (cwd is not None and (cwd == root or root in cwd.parents)):
            residue.append(f'{line.strip()} cwd={cwd or "unavailable"}')
    return residue


def _wait_for_socket_residue(tmp_root: Path, *, timeout: float) -> list[str]:
    deadline = time.time() + timeout
    residue: list[str] = []
    while time.time() < deadline:
        residue = _listening_socket_lines_containing(tmp_root)
        if not residue:
            return []
        time.sleep(0.05)
    return residue


def _listening_socket_lines_containing(path: Path) -> list[str]:
    if shutil.which('ss') is None:
        return []
    result = subprocess.run(
        ['ss', '-xlpn'],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(f'failed to inspect test-owned listening sockets: {result.stderr}')
    marker = str(path.resolve())
    return [
        f'listening_socket: {line.strip()}'
        for line in result.stdout.splitlines()
        if marker in line
    ]


def _process_cwd(pid: int) -> Path | None:
    try:
        return Path(os.readlink(f'/proc/{pid}/cwd')).resolve()
    except Exception:
        return None


def _clear_unmounted_in_process_authority(project_root: Path) -> None:
    paths = PathLayout(project_root)
    try:
        lease = json.loads(paths.ccbd_lease_path.read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError):
        return
    if not isinstance(lease, dict) or lease.get('mount_state') != 'unmounted':
        raise AssertionError(f'in-process test daemon is not unmounted: {paths.ccbd_lease_path}')
    for path in (paths.ccbd_lease_path, paths.ccbd_keeper_path, paths.ccbd_lifecycle_path):
        path.unlink(missing_ok=True)
