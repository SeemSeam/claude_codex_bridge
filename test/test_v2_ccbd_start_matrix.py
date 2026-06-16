from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from cli.phase2 import maybe_handle_phase2
import cli.phase2 as phase2_module


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _run_ccb(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    return subprocess.run(
        [sys.executable, str(_repo_root() / 'ccb.py'), *args],
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _run_phase2_local(args: list[str], *, cwd: Path) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    code = maybe_handle_phase2(args, cwd=cwd, stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


def _wait_for_pid_exit(pid: int, *, timeout_s: float = 3.0) -> None:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.05)
    raise AssertionError(f'pid {pid} did not exit within {timeout_s}s')


def _wait_for_lifecycle(path: Path, *, desired_state: str, phase: str, timeout_s: float = 3.0) -> dict[str, object]:
    deadline = time.time() + timeout_s
    last: dict[str, object] | None = None
    while time.time() < deadline:
        try:
            last = json.loads(path.read_text(encoding='utf-8'))
        except Exception:
            last = None
        if last is not None and last.get('desired_state') == desired_state and last.get('phase') == phase:
            return last
        time.sleep(0.05)
    raise AssertionError(f'expected lifecycle desired={desired_state!r} phase={phase!r}; last={last!r}')


def test_phase2_start_initializes_empty_existing_anchor(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-empty-anchor'
    (project_root / '.ccb').mkdir(parents=True)
    seen: dict[str, object] = {}

    def _fake_start(context, command):
        del command
        seen['source'] = context.project.source
        seen['project_root'] = context.project.project_root
        return SimpleNamespace(
            project_root=str(context.project.project_root),
            project_id=context.project.project_id,
            started=('agent1', 'agent2', 'agent3'),
            daemon_started=False,
            socket_path=str(context.paths.ccbd_socket_path),
        )

    monkeypatch.setattr(phase2_module, 'start_agents', _fake_start)

    code, stdout, stderr = _run_phase2_local([], cwd=project_root)

    assert code == 0, stderr
    assert seen['source'] == 'anchor'
    assert seen['project_root'] == project_root.resolve()
    assert (project_root / '.ccb' / 'ccb.config').exists() is False
    assert 'start_status: ok' in stdout
    assert 'agents: agent1, agent2, agent3' in stdout


def test_phase2_start_uses_builtin_default_when_anchor_has_persisted_state_without_config(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / 'repo-missing-config-with-state'
    runtime_path = project_root / '.ccb' / 'agents' / 'demo' / 'runtime.json'
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_path.write_text('{"agent_name":"demo","provider":"fake"}\n', encoding='utf-8')
    seen: dict[str, object] = {}

    def _fake_start(context, command):
        del command
        seen['source'] = context.project.source
        seen['project_root'] = context.project.project_root
        return SimpleNamespace(
            project_root=str(context.project.project_root),
            project_id=context.project.project_id,
            started=('agent1', 'agent2', 'agent3'),
            daemon_started=False,
            socket_path=str(context.paths.ccbd_socket_path),
        )

    monkeypatch.setattr(phase2_module, 'start_agents', _fake_start)

    code, stdout, stderr = _run_phase2_local([], cwd=project_root)

    assert code == 0, stderr
    assert seen['source'] == 'anchor'
    assert seen['project_root'] == project_root.resolve()
    assert (project_root / '.ccb' / 'ccb.config').exists() is False
    assert 'start_status: ok' in stdout
    assert 'agents: agent1, agent2, agent3' in stdout


@pytest.mark.ccb_lifecycle_smoke
def test_ccb_start_restarts_dead_daemon_on_subsequent_start(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-start-dead-daemon'
    config_path = project_root / '.ccb' / 'ccb.config'
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text('demo:fake\n', encoding='utf-8')

    start_1 = _run_ccb([], cwd=project_root)
    assert start_1.returncode == 0, start_1.stderr
    assert 'ccbd_started: true' in start_1.stdout
    assert 'agents: demo' in start_1.stdout

    lease_path = project_root / '.ccb' / 'ccbd' / 'lease.json'
    lifecycle_path = project_root / '.ccb' / 'ccbd' / 'lifecycle.json'
    lease_before = json.loads(lease_path.read_text(encoding='utf-8'))
    lifecycle_before = json.loads(lifecycle_path.read_text(encoding='utf-8'))
    stale_pid = int(lease_before['ccbd_pid'])
    os.kill(stale_pid, signal.SIGTERM)
    _wait_for_pid_exit(stale_pid)

    socket_path = Path(str(lease_before['socket_path']))
    try:
        socket_path.unlink()
    except FileNotFoundError:
        pass

    start_2 = _run_ccb([], cwd=project_root)
    assert start_2.returncode == 0, start_2.stderr
    assert 'start_status: ok' in start_2.stdout
    assert 'ccbd_started:' in start_2.stdout
    assert 'agents: demo' in start_2.stdout

    lease_after = json.loads(lease_path.read_text(encoding='utf-8'))
    lifecycle_after = json.loads(lifecycle_path.read_text(encoding='utf-8'))
    assert int(lease_after['ccbd_pid']) != stale_pid
    assert int(lease_after['generation']) == int(lease_before['generation']) + 1
    assert lifecycle_before['desired_state'] == 'running'
    assert lifecycle_before['phase'] == 'mounted'
    assert lifecycle_after['desired_state'] == 'running'
    assert lifecycle_after['phase'] == 'mounted'
    assert int(lifecycle_after['generation']) == int(lifecycle_before['generation']) + 1

    ps = _run_ccb(['ps'], cwd=project_root)
    assert ps.returncode == 0, ps.stderr
    assert 'ccbd_state: mounted' in ps.stdout
    assert 'agent: name=demo state=idle provider=fake queue=0' in ps.stdout

    kill = _run_ccb(['kill', '-f'], cwd=project_root)
    assert kill.returncode == 0, kill.stderr
    lifecycle_killed = _wait_for_lifecycle(lifecycle_path, desired_state='stopped', phase='unmounted')
    assert lifecycle_killed['desired_state'] == 'stopped'
    assert lifecycle_killed['phase'] == 'unmounted'
