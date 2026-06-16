from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from io import StringIO
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest

from ccbd.app import CcbdApp
from ccbd.socket_client import CcbdClient, CcbdClientError
from ccbd.services.health import HealthMonitor
from cli.services.cleanup import CleanupAction, CleanupSummary
from cli.services.start_runtime import StartSummary
import cli.phase2 as phase2_module
from cli.phase2 import maybe_handle_phase2
from storage.paths import PathLayout


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _run_ccb(args: list[str], *, cwd: Path) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    for name in tuple(env):
        if name in {'CCB_SESSION_FILE', 'CCB_SESSION_ID'}:
            env.pop(name, None)
            continue
        if name.startswith(('CCB_CALLER_', 'CODEX_', 'CLAUDE_', 'GEMINI_', 'OPENCODE_', 'DROID_')):
            env.pop(name, None)
    return subprocess.run(
        [sys.executable, str(_repo_root() / 'ccb.py'), *args],
        cwd=str(cwd),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def _config_text() -> str:
    return 'codex:codex\n'


def _single_agent_config_text(provider: str) -> str:
    return f'demo:{provider}\n'


def _named_agent_config_text(agent_name: str, provider: str) -> str:
    return f'{agent_name}:{provider}\n'


def _dual_named_agent_config_text(agent1: str, provider1: str, agent2: str, provider2: str) -> str:
    return f'{agent1}:{provider1},{agent2}:{provider2}\n'


def _wait_for_status(cwd: Path, target: str, expected: str, *, timeout: float = 3.0) -> subprocess.CompletedProcess[str]:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = _run_ccb(['pend', target], cwd=cwd)
        if last.returncode == 0 and f'status: {expected}' in last.stdout:
            return last
        time.sleep(0.1)
    raise AssertionError(f'expected status {expected!r}; last stdout={last.stdout!r} stderr={last.stderr!r}')


def _wait_for_any_status(cwd: Path, target: str, expected: tuple[str, ...], *, timeout: float = 3.0) -> subprocess.CompletedProcess[str]:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = _run_ccb(['pend', target], cwd=cwd)
        if last.returncode == 0 and any(f'status: {item}' in last.stdout for item in expected):
            return last
        time.sleep(0.1)
    raise AssertionError(f'expected any status {expected!r}; last stdout={last.stdout!r} stderr={last.stderr!r}')


def _wait_for_ccbd_execution_summary(
    cwd: Path,
    *,
    active_execution_count: int,
    recoverable_execution_count: int,
    timeout: float = 3.0,
) -> subprocess.CompletedProcess[str]:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = _run_ccb(['ping', 'ccbd'], cwd=cwd)
        if last.returncode == 0:
            stdout = last.stdout
            if (
                f'active_execution_count: {active_execution_count}' in stdout
                and f'recoverable_execution_count: {recoverable_execution_count}' in stdout
            ):
                return last
        time.sleep(0.05)
    raise AssertionError(f'expected execution summary; last stdout={last.stdout!r} stderr={last.stderr!r}')


def _wait_for_doctor_line(cwd: Path, expected: str, *, timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = _run_ccb(['doctor'], cwd=cwd)
        if last.returncode == 0 and expected in last.stdout:
            return last
        time.sleep(0.1)
    raise AssertionError(f'expected doctor line {expected!r}; last stdout={last.stdout!r} stderr={last.stderr!r}')


def _wait_for_doctor_any_line(
    cwd: Path,
    expected: tuple[str, ...],
    *,
    timeout: float = 5.0,
) -> subprocess.CompletedProcess[str]:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = _run_ccb(['doctor'], cwd=cwd)
        if last.returncode == 0 and any(line in last.stdout for line in expected):
            return last
        time.sleep(0.1)
    raise AssertionError(f'expected any doctor line {expected!r}; last stdout={last.stdout!r} stderr={last.stderr!r}')


def _run_phase2_local(args: list[str], *, cwd: Path, start_app: CcbdApp | None = None) -> tuple[int, str, str]:
    stdout = StringIO()
    stderr = StringIO()
    if start_app is None or args:
        code = maybe_handle_phase2(args, cwd=cwd, stdout=stdout, stderr=stderr)
        return code, stdout.getvalue(), stderr.getvalue()
    original_start_agents = phase2_module.start_agents
    try:
        phase2_module.start_agents = _phase2_start_against_app(start_app)
        code = maybe_handle_phase2(args, cwd=cwd, stdout=stdout, stderr=stderr)
    finally:
        phase2_module.start_agents = original_start_agents
    return code, stdout.getvalue(), stderr.getvalue()


def _phase2_start_against_app(app: CcbdApp):
    # In-process app tests own the daemon lifecycle; starting keeper here races the fixture.
    def _start(context, command, *, terminal_size=None):
        assert context.project.project_root == app.project_root
        payload = CcbdClient(app.paths.ccbd_socket_path).start(
            agent_names=command.agent_names,
            restore=command.restore,
            auto_permission=command.auto_permission,
            terminal_size=terminal_size,
        )
        return StartSummary(
            project_root=str(payload.get("project_root") or context.project.project_root),
            project_id=str(payload.get("project_id") or context.project.project_id),
            started=tuple(str(item) for item in (payload.get("started") or ())),
            daemon_started=False,
            socket_path=str(payload.get("socket_path") or context.paths.ccbd_socket_path),
        )

    return _start


def _extract_accepted_job_id(stdout: str, *, target: str) -> str:
    patterns = (
        re.compile(rf'^accepted job=(job_[a-f0-9]+) target={re.escape(target)}$', re.MULTILINE),
        re.compile(rf'^job: (job_[a-f0-9]+) {re.escape(target)} accepted$', re.MULTILINE),
    )
    for pattern in patterns:
        match = pattern.search(stdout)
        if match is not None:
            return match.group(1)
    raise AssertionError(f'expected accepted receipt for target={target!r}; stdout={stdout!r}')


def _freeze_job_ids(app: CcbdApp, monkeypatch: pytest.MonkeyPatch, *job_ids: str) -> None:
    original_new_id = app.dispatcher._new_id
    remaining = iter(job_ids)

    def _new_id(kind: str) -> str:
        if kind == 'job':
            return next(remaining)
        return original_new_id(kind)

    monkeypatch.setattr(app.dispatcher, '_new_id', _new_id)


class _TtyStringIO(StringIO):
    def isatty(self) -> bool:  # pragma: no cover - trivial
        return True


class _TtyInput(StringIO):
    def isatty(self) -> bool:  # pragma: no cover - trivial
        return True


@pytest.fixture(autouse=True)
def _disable_health_monitor_in_phase2_blackbox_tests(monkeypatch) -> None:
    monkeypatch.setattr(HealthMonitor, 'check_all', lambda self: {})


def test_phase2_start_bootstraps_missing_project_without_writing_config(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-bootstrap'
    project_root.mkdir()
    seen: dict[str, object] = {}

    def _fake_start(context, command):
        del command
        seen['source'] = context.project.source
        seen['project_root'] = context.project.project_root
        return SimpleNamespace(
            project_root=str(context.project.project_root),
            project_id=context.project.project_id,
            started=('codex', 'claude'),
            daemon_started=False,
            socket_path=str(context.paths.ccbd_socket_path),
        )

    monkeypatch.setattr(phase2_module, 'start_agents', _fake_start)

    code, stdout, stderr = _run_phase2_local([], cwd=project_root)

    assert code == 0, stderr
    assert seen['source'] == 'bootstrapped'
    assert seen['project_root'] == project_root.resolve()
    assert (project_root / '.ccb').is_dir()
    assert (project_root / '.ccb' / 'ccb.config').exists() is False
    assert 'start_status: ok' in stdout
    assert 'agents: codex, claude' in stdout


def test_phase2_config_validate_rejects_duplicate_effective_runtime_home(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-config-validate'
    _write(
        project_root / '.ccb' / 'ccb.config',
        """version = 2
default_agents = ["agent1", "agent2"]

[agents.agent1]
provider = "codex"
target = "."
workspace_mode = "inplace"
restore = "auto"
permission = "manual"

[agents.agent1.provider_profile]
mode = "isolated"
home = ".ccb/provider-profiles/shared-codex"

[agents.agent2]
provider = "codex"
target = "."
workspace_mode = "inplace"
restore = "auto"
permission = "manual"

[agents.agent2.provider_profile]
mode = "isolated"
home = ".ccb/provider-profiles/shared-codex"
""",
    )

    code, stdout, stderr = _run_phase2_local(['config', 'validate'], cwd=project_root)

    assert code == 1
    assert stdout == ''
    assert 'config_status: invalid' in stderr
    assert 'duplicate effective codex_home' in stderr


def test_phase2_config_validate_reports_windows_style_warnings(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-config-style-warnings'
    _write(
        project_root / '.ccb' / 'ccb.config',
        """version = 2

[windows]
main = "agent1:codex(worktree)"

[agents.agent1]
provider = "codex"
workspace_mode = "git-worktree"

[agents.ghost]
provider = "codex"
""",
    )

    code, stdout, stderr = _run_phase2_local(['config', 'validate'], cwd=project_root)

    assert code == 0
    assert stderr == ''
    assert 'config_status: valid' in stdout
    assert (
        'config_warning: redundant_agent_provider: agents.agent1.provider repeats [windows] and should be removed'
        in stdout
    )
    assert (
        'config_warning: redundant_agent_workspace_mode: agents.agent1.workspace_mode repeats [windows] and should be removed'
        in stdout
    )
    assert (
        'config_warning: stale_agent_overlay: agents.ghost is ignored because it is not referenced by [windows]'
        in stdout
    )


def test_ccb_kill_without_anchor_is_noop_and_does_not_bootstrap(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-kill-no-anchor'
    project_root.mkdir()

    result = _run_ccb(['kill', '-f'], cwd=project_root)

    assert result.returncode == 0, result.stderr
    assert result.stderr == ''
    assert 'kill_status: ok' in result.stdout
    assert 'state: unmounted' in result.stdout
    assert 'forced: true' in result.stdout
    assert not (project_root / '.ccb').exists()


def test_phase2_missing_config_with_persisted_state_uses_builtin_default(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-missing-config-guidance'
    _write(project_root / '.ccb' / 'agents' / 'demo' / 'runtime.json', '{"agent_name":"demo"}\n')
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


def test_ccb_kill_succeeds_with_persisted_state_without_config(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-kill-missing-config'
    _write(project_root / '.ccb' / 'agents' / 'demo' / 'runtime.json', '{"agent_name":"demo"}\n')

    result = _run_ccb(['kill'], cwd=project_root)

    assert result.returncode == 0, result.stderr
    assert result.stderr == ''
    assert 'kill_status: ok' in result.stdout
    assert 'state: unmounted' in result.stdout


def test_phase2_interactive_start_attaches_namespace(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-attach'
    project_root.mkdir()
    events: list[str] = []

    def _fake_start(context, command):
        del context, command
        events.append('start')
        return SimpleNamespace(
            project_root=str(project_root),
            project_id='proj-1',
            started=('agent1',),
            daemon_started=True,
            socket_path=str(project_root / '.ccb' / 'ccbd' / 'ccbd.sock'),
        )

    def _fake_attach(context):
        del context
        events.append('attach')
        return SimpleNamespace(
            project_id='proj-1',
            tmux_socket_path=str(project_root / '.ccb' / 'ccbd' / 'tmux.sock'),
            tmux_session_name='ccb-repo-attach-proj1',
        )

    monkeypatch.setattr(phase2_module, 'start_agents', _fake_start)
    monkeypatch.setattr('cli.phase2_runtime.handlers_start.attach_started_project_namespace', _fake_attach)
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: True)

    stdout = _TtyStringIO()
    stderr = StringIO()
    code = maybe_handle_phase2([], cwd=project_root, stdout=stdout, stderr=stderr)

    assert code == 0, stderr.getvalue()
    assert events == ['start', 'attach']
    assert stdout.getvalue() == ''
    assert 'start_status: ok' not in stdout.getvalue()


def test_phase2_interactive_start_passes_terminal_size_to_start_service(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-attach-terminal-size'
    project_root.mkdir()
    seen: dict[str, object] = {}

    def _fake_start(context, command, *, terminal_size=None):
        del context, command
        seen['terminal_size'] = terminal_size
        return SimpleNamespace(
            project_root=str(project_root),
            project_id='proj-tty',
            started=('agent1',),
            daemon_started=True,
            socket_path=str(project_root / '.ccb' / 'ccbd' / 'ccbd.sock'),
        )

    monkeypatch.setattr(phase2_module, 'start_agents', _fake_start)
    monkeypatch.setattr(
        'cli.phase2_runtime.handlers_start.attach_started_project_namespace',
        lambda context: SimpleNamespace(project_id='proj-tty', tmux_socket_path='sock', tmux_session_name='sess'),
    )
    monkeypatch.setattr(
        'cli.phase2_runtime.handlers_start._terminal_size_for_streams',
        lambda *streams: (240, 72),
    )
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: True)

    stdout = _TtyStringIO()
    stderr = StringIO()
    code = maybe_handle_phase2([], cwd=project_root, stdout=stdout, stderr=stderr)

    assert code == 0, stderr.getvalue()
    assert seen['terminal_size'] == (240, 72)


def test_phase2_noninteractive_start_keeps_start_output(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-no-auto-open'
    project_root.mkdir()
    events: list[str] = []

    def _fake_start(context, command):
        del context, command
        events.append('start')
        return SimpleNamespace(
            project_root=str(project_root),
            project_id='proj-2',
            started=('agent1',),
            daemon_started=True,
            socket_path=str(project_root / '.ccb' / 'ccbd' / 'ccbd.sock'),
        )

    def _fake_attach(context):
        del context
        events.append('attach')
        raise AssertionError('attach should not be called in noninteractive mode')

    monkeypatch.setattr(phase2_module, 'start_agents', _fake_start)
    monkeypatch.setattr('cli.phase2_runtime.handlers_start.attach_started_project_namespace', _fake_attach)
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: False)

    stdout = StringIO()
    stderr = StringIO()
    code = maybe_handle_phase2([], cwd=project_root, stdout=stdout, stderr=stderr)

    assert code == 0, stderr.getvalue()
    assert events == ['start']
    assert 'start_status: ok' in stdout.getvalue()


def test_phase2_start_with_new_context_requires_interactive_confirmation(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-reset-noninteractive'
    project_root.mkdir()
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: False)

    code, stdout, stderr = _run_phase2_local(['-n'], cwd=project_root)

    assert code == 1
    assert stdout == ''
    assert 'requires interactive confirmation' in stderr
    assert not (project_root / '.ccb').exists()


def test_phase2_start_with_new_context_cancelled_does_not_bootstrap(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-reset-cancelled'
    project_root.mkdir()
    monkeypatch.setattr(sys, 'stdin', _TtyInput('n\n'))

    stdout = _TtyStringIO()
    stderr = StringIO()
    code = maybe_handle_phase2(['-n'], cwd=project_root, stdout=stdout, stderr=stderr)

    assert code == 1
    assert 'Refresh project memory/context under' in stdout.getvalue()
    assert 'project reset cancelled' in stderr.getvalue()
    assert not (project_root / '.ccb').exists()


def test_phase2_start_with_new_context_rebuilds_stale_anchor_before_bootstrap(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-reset-rebuild'
    stale_dir = project_root / '.ccb'
    (stale_dir / 'agents' / 'ghost').mkdir(parents=True)
    (stale_dir / 'agents' / 'ghost' / 'runtime.json').write_text('{}', encoding='utf-8')

    seen: dict[str, object] = {}

    def _fake_start(context, command):
        seen['config_exists'] = (context.project.project_root / '.ccb' / 'ccb.config').exists()
        seen['ghost_exists'] = (context.project.project_root / '.ccb' / 'agents' / 'ghost').exists()
        seen['restore'] = command.restore
        return SimpleNamespace(
            project_root=str(context.project.project_root),
            project_id=context.project.project_id,
            started=('agent1', 'agent2', 'agent3'),
            daemon_started=False,
            socket_path=str(context.paths.ccbd_socket_path),
        )

    monkeypatch.setattr(phase2_module, 'start_agents', _fake_start)
    monkeypatch.setattr(sys, 'stdin', _TtyInput('y\n'))

    stdout = StringIO()
    stderr = StringIO()
    code = maybe_handle_phase2(['-n'], cwd=project_root, stdout=stdout, stderr=stderr)

    assert code == 0, stderr.getvalue()
    assert seen['ghost_exists'] is False
    assert seen['config_exists'] is False
    assert seen['restore'] is False
    assert 'start_status: ok' in stdout.getvalue()


def test_phase2_start_with_new_context_rebuilds_after_kill_when_config_missing(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-reset-after-kill'
    _write(project_root / '.ccb' / 'agents' / 'demo' / 'runtime.json', '{"agent_name":"demo"}\n')

    kill = _run_ccb(['kill'], cwd=project_root)
    assert kill.returncode == 0, kill.stderr
    assert 'kill_status: ok' in kill.stdout

    seen: dict[str, object] = {}

    def _fake_start(context, command):
        seen['config_exists'] = (context.project.project_root / '.ccb' / 'ccb.config').exists()
        seen['demo_exists'] = (context.project.project_root / '.ccb' / 'agents' / 'demo').exists()
        seen['restore'] = command.restore
        return SimpleNamespace(
            project_root=str(context.project.project_root),
            project_id=context.project.project_id,
            started=('agent1', 'agent2', 'agent3'),
            daemon_started=False,
            socket_path=str(context.paths.ccbd_socket_path),
        )

    monkeypatch.setattr(phase2_module, 'start_agents', _fake_start)
    monkeypatch.setattr(sys, 'stdin', _TtyInput('y\n'))

    stdout = StringIO()
    stderr = StringIO()
    code = maybe_handle_phase2(['-n'], cwd=project_root, stdout=stdout, stderr=stderr)

    assert code == 0, stderr.getvalue()
    assert seen['restore'] is False
    assert seen['demo_exists'] is False
    assert seen['config_exists'] is False
    assert 'start_status: ok' in stdout.getvalue()


def test_phase2_start_with_new_context_reports_cleanup_guidance_on_stop_failure(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-reset-stop-failure'
    _write(project_root / '.ccb' / 'ccb.config', _config_text())
    monkeypatch.setattr(sys, 'stdin', _TtyInput('y\n'))

    def _fail_reset(project_root_arg: Path, *, context=None):
        del project_root_arg, context
        raise RuntimeError('failed to stop project runtime before rebuilding `.ccb`; run `ccb kill -f` and retry')

    monkeypatch.setattr(phase2_module, 'reset_project_state', _fail_reset)

    stdout = StringIO()
    stderr = StringIO()
    code = maybe_handle_phase2(['-n'], cwd=project_root, stdout=stdout, stderr=stderr)

    assert code == 1
    assert 'Refresh project memory/context under' in stdout.getvalue()
    assert 'command_status: failed' in stderr.getvalue()
    assert 'ccb kill -f' in stderr.getvalue()


def test_phase2_start_blocks_nested_directory_under_parent_anchor(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-parent'
    nested = project_root / 'nested'
    nested.mkdir(parents=True)
    (project_root / '.ccb').mkdir()

    code, stdout, stderr = _run_phase2_local([], cwd=nested)

    assert code == 1
    assert stdout == ''
    assert 'parent project anchor already exists' in stderr
    assert 'create' in stderr and '.ccb manually' in stderr


def test_phase2_reports_subprocess_failure_without_traceback(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-subprocess'
    _write(project_root / '.ccb' / 'ccb.config', 'agent1:codex\n')

    def _raise_subprocess_error(context):
        del context
        raise subprocess.CalledProcessError(1, ['tmux', 'attach-session'])

    monkeypatch.setattr('cli.phase2_runtime.handlers_start.attach_started_project_namespace', _raise_subprocess_error)
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: True)

    stdout = _TtyStringIO()
    stderr_io = StringIO()
    code = maybe_handle_phase2([], cwd=project_root, stdout=stdout, stderr=stderr_io)
    stderr = stderr_io.getvalue()

    assert code == 1
    assert stdout.getvalue() == ''
    assert 'command_status: failed' in stderr
    assert 'returned non-zero exit status 1' in stderr
    assert 'Traceback' not in stderr


def test_phase2_reports_exception_cause_without_traceback(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-cause'
    _write(project_root / '.ccb' / 'ccb.config', 'agent1:codex\n')

    def _raise_wrapped_error(context):
        del context
        cause = RuntimeError('tmux detail: socket path too long')
        raise RuntimeError('failed to prepare tmux server') from cause

    monkeypatch.setattr('cli.phase2_runtime.handlers_start.attach_started_project_namespace', _raise_wrapped_error)
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: True)

    stdout = _TtyStringIO()
    stderr_io = StringIO()
    code = maybe_handle_phase2([], cwd=project_root, stdout=stdout, stderr=stderr_io)
    stderr = stderr_io.getvalue()

    assert code == 1
    assert stdout.getvalue() == ''
    assert 'command_status: failed' in stderr
    assert 'error: failed to prepare tmux server' in stderr
    assert 'error_cause: tmux detail: socket path too long' in stderr
    assert 'Traceback' not in stderr


def test_phase2_removed_attach_command_reports_guidance(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-removed-attach'
    project_root.mkdir()

    removed_command = ''.join(('op', 'en'))
    result = _run_ccb([removed_command], cwd=project_root)

    assert result.returncode == 2
    assert result.stdout == ''
    assert 'has been removed' in result.stderr
    assert 'Use: ccb' in result.stderr
    assert not (project_root / '.ccb').exists()


def test_phase2_trace_renders_control_plane_payload(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-trace-render'
    project_root.mkdir()
    seen: dict[str, object] = {}

    def _fake_trace(context, command):
        seen['project_root'] = context.project.project_root
        seen['target'] = command.target
        return {
            'target': command.target,
            'resolved_kind': 'job',
            'submission_id': None,
            'message_id': 'msg_1',
            'attempt_id': 'att_1',
            'reply_id': 'rep_1',
            'job_id': command.target,
            'submission': None,
            'message_count': 1,
            'attempt_count': 1,
            'reply_count': 1,
            'event_count': 2,
            'job_count': 1,
            'messages': (
                {
                    'message_id': 'msg_1',
                    'origin_message_id': None,
                    'submission_id': None,
                    'from_actor': 'claude',
                    'target_scope': 'single',
                    'target_agents': ['codex'],
                    'message_class': 'task_request',
                    'message_state': 'completed',
                    'priority': 100,
                    'created_at': '2026-03-30T00:00:00Z',
                    'updated_at': '2026-03-30T00:00:10Z',
                },
            ),
            'attempts': (
                {
                    'attempt_id': 'att_1',
                    'message_id': 'msg_1',
                    'agent_name': 'codex',
                    'provider': 'codex',
                    'job_id': command.target,
                    'retry_index': 0,
                    'attempt_state': 'completed',
                    'started_at': '2026-03-30T00:00:01Z',
                    'updated_at': '2026-03-30T00:00:10Z',
                },
            ),
            'replies': (
                {
                    'reply_id': 'rep_1',
                    'message_id': 'msg_1',
                    'attempt_id': 'att_1',
                    'agent_name': 'codex',
                    'terminal_status': 'completed',
                    'reply_preview': 'done',
                    'reply_size': 4,
                    'reason': 'task_complete',
                    'status': 'completed',
                    'provider_turn_ref': 'turn-1',
                    'finished_at': '2026-03-30T00:00:10Z',
                },
            ),
            'events': (
                {
                    'inbound_event_id': 'iev_1',
                    'agent_name': 'codex',
                    'event_type': 'task_request',
                    'message_id': 'msg_1',
                    'attempt_id': 'att_1',
                    'payload_ref': 'message:msg_1',
                    'priority': 100,
                    'status': 'consumed',
                    'mailbox_state': 'idle',
                    'mailbox_active': False,
                    'created_at': '2026-03-30T00:00:00Z',
                    'started_at': '2026-03-30T00:00:01Z',
                    'finished_at': '2026-03-30T00:00:10Z',
                },
                {
                    'inbound_event_id': 'iev_2',
                    'agent_name': 'claude',
                    'event_type': 'task_reply',
                    'message_id': 'msg_1',
                    'attempt_id': 'att_1',
                    'payload_ref': 'reply:rep_1',
                    'priority': 10,
                    'status': 'queued',
                    'mailbox_state': 'blocked',
                    'mailbox_active': False,
                    'created_at': '2026-03-30T00:00:10Z',
                    'started_at': None,
                    'finished_at': None,
                },
            ),
            'jobs': (
                {
                    'job_id': command.target,
                    'agent_name': 'codex',
                    'provider': 'codex',
                    'status': 'completed',
                    'submission_id': None,
                    'created_at': '2026-03-30T00:00:00Z',
                    'updated_at': '2026-03-30T00:00:10Z',
                },
            ),
        }

    monkeypatch.setattr(phase2_module, 'trace_target', _fake_trace)

    code, stdout, stderr = _run_phase2_local(['trace', 'job_123'], cwd=project_root)

    assert code == 0, stderr
    assert seen['project_root'] == project_root.resolve()
    assert seen['target'] == 'job_123'
    assert 'trace_status: ok' in stdout
    assert 'resolved_kind: job' in stdout
    assert 'message_count: 1' in stdout
    assert 'attempt_count: 1' in stdout
    assert 'reply_count: 1' in stdout
    assert 'event_count: 2' in stdout
    assert 'job_count: 1' in stdout
    assert 'reply: id=rep_1 message=msg_1 attempt=att_1 agent=codex terminal=completed size=4 notice=false kind=None reason=task_complete finished=2026-03-30T00:00:10Z preview=done' in stdout


def test_phase2_doctor_bundle_renders_export_summary(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-doctor-bundle'
    project_root.mkdir()

    monkeypatch.setattr(
        phase2_module,
        'export_diagnostic_bundle',
        lambda context, command: SimpleNamespace(
            project_root=str(context.project.project_root),
            project_id=context.project.project_id,
            bundle_id='bundle-1',
            bundle_path=str(context.paths.ccbd_support_dir / 'bundle-1.tar.gz'),
            file_count=7,
            included_count=6,
            missing_count=1,
            truncated_count=2,
            doctor_error=None,
        ),
    )

    code, stdout, stderr = _run_phase2_local(['doctor', '--output'], cwd=project_root)

    assert code == 0, stderr
    assert 'doctor_bundle_status: ok' in stdout
    assert 'bundle_id: bundle-1' in stdout
    assert 'truncated_count: 2' in stdout


def test_phase2_doctor_storage_renders_storage_summary(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-doctor-storage'
    project_root.mkdir()

    monkeypatch.setattr(
        phase2_module,
        'doctor_storage_summary',
        lambda context: {
            'schema_version': 1,
            'project': str(context.project.project_root),
            'project_id': context.project.project_id,
            'runtime_root_kind': 'project',
            'runtime_state_root': str(context.paths.runtime_state_root),
            'shared_cache_root': str(context.paths.shared_cache_dir),
            'shared_cache_root_usable': False,
            'shared_cache_status': 'disabled',
            'shared_cache_reason': 'not_implemented',
            'total_bytes': 12,
            'total_count': 2,
            'by_class': {'secret': {'bytes': 4, 'count': 1}, 'session': {'bytes': 8, 'count': 1}},
            'by_provider': {'codex': {'bytes': 12, 'count': 2}},
            'by_agent': {'agent1': {'bytes': 12, 'count': 2}},
            'entries': [
                {
                    'relative_path': 'agents/agent1/provider-state/codex/home/auth.json',
                    'storage_class': 'secret',
                    'size_bytes': 4,
                    'provider': 'codex',
                    'agent': 'agent1',
                    'active': None,
                    'reclaimable': None,
                    'reason': 'provider_secret',
                }
            ],
        },
    )

    code, stdout, stderr = _run_phase2_local(['doctor', 'storage'], cwd=project_root)

    assert code == 0, stderr
    assert 'storage_status: ok' in stdout
    assert 'storage_shared_cache_status: disabled' in stdout
    assert f'storage_shared_cache_root: {PathLayout(project_root).shared_cache_dir}' in stdout
    assert 'storage_shared_cache_root_usable: False' in stdout
    assert 'storage_shared_cache_reason: not_implemented' in stdout
    assert 'storage_class: class=secret bytes=4 count=1' in stdout
    assert 'storage_provider: provider=codex bytes=12 count=2' in stdout
    assert 'storage_entry: class=secret provider=codex agent=agent1 bytes=4' in stdout


def test_phase2_doctor_storage_json_emits_full_payload(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-doctor-storage-json'
    project_root.mkdir()

    monkeypatch.setattr(
        phase2_module,
        'doctor_storage_summary',
        lambda context: {
            'schema_version': 1,
            'project': str(context.project.project_root),
            'project_id': context.project.project_id,
            'runtime_root_kind': 'project',
            'runtime_state_root': str(context.paths.runtime_state_root),
            'shared_cache_root': str(context.paths.shared_cache_dir),
            'shared_cache_root_usable': False,
            'shared_cache_status': 'disabled',
            'shared_cache_reason': 'not_implemented',
            'total_bytes': 0,
            'total_count': 0,
            'by_class': {},
            'by_provider': {},
            'by_agent': {},
            'entries': [],
        },
    )

    code, stdout, stderr = _run_phase2_local(['doctor', 'storage', '--json'], cwd=project_root)

    assert code == 0, stderr
    payload = json.loads(stdout)
    assert payload['schema_version'] == 1
    assert payload['shared_cache_status'] == 'disabled'
    assert payload['shared_cache_root_usable'] is False
    assert payload['entries'] == []


def test_phase2_cleanup_renders_cleanup_summary(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-cleanup'
    (project_root / '.ccb').mkdir(parents=True)

    monkeypatch.setattr(
        phase2_module,
        'cleanup_project_storage',
        lambda context, command: CleanupSummary(
            project_root=str(context.project.project_root),
            project_id=context.project.project_id,
            status='ok',
            deleted_bytes=12,
            deleted_count=1,
            skipped_count=0,
            actions=(
                CleanupAction(
                    provider='claude',
                    kind='version_cache',
                    path=str(context.paths.ccb_dir / 'agents/agent1/provider-state/claude/home/.local/share/claude/versions/old'),
                    bytes_removed=12,
                    reason='old_claude_version_cache',
                ),
            ),
        ),
    )

    code, stdout, stderr = _run_phase2_local(['cleanup'], cwd=project_root)

    assert code == 0, stderr
    assert 'cleanup_status: ok' in stdout
    assert 'cleanup_deleted_bytes: 12' in stdout
    assert 'cleanup_action: provider=claude kind=version_cache bytes=12 reason=old_claude_version_cache' in stdout


def test_phase2_resubmit_renders_new_message_chain(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-resubmit-render'
    project_root.mkdir()
    seen: dict[str, object] = {}

    def _fake_resubmit(context, command):
        seen['project_root'] = context.project.project_root
        seen['message_id'] = command.message_id
        return SimpleNamespace(
            project_id=context.project.project_id,
            original_message_id=command.message_id,
            message_id='msg_new',
            submission_id='sub_new',
            jobs=(
                {'job_id': 'job_1', 'agent_name': 'codex', 'target_name': 'codex', 'status': 'accepted'},
                {'job_id': 'job_2', 'agent_name': 'claude', 'target_name': 'claude', 'status': 'queued'},
            ),
        )

    monkeypatch.setattr(phase2_module, 'resubmit_message', _fake_resubmit)

    code, stdout, stderr = _run_phase2_local(['resubmit', 'msg_old'], cwd=project_root)

    assert code == 0, stderr
    assert seen['project_root'] == project_root.resolve()
    assert seen['message_id'] == 'msg_old'
    assert 'resubmit_status: accepted' in stdout
    assert 'original_message_id: msg_old' in stdout
    assert 'message_id: msg_new' in stdout
    assert 'submission_id: sub_new' in stdout
    assert 'job: job_1 codex accepted' in stdout
    assert 'job: job_2 claude queued' in stdout


def test_phase2_retry_renders_attempt_retry_chain(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-retry-render'
    project_root.mkdir()
    seen: dict[str, object] = {}

    def _fake_retry(context, command):
        seen['project_root'] = context.project.project_root
        seen['target'] = command.target
        return SimpleNamespace(
            project_id=context.project.project_id,
            target=command.target,
            message_id='msg_1',
            original_attempt_id='att_old',
            attempt_id='att_new',
            job_id='job_new',
            agent_name='codex',
            status='accepted',
        )

    monkeypatch.setattr(phase2_module, 'retry_attempt', _fake_retry)

    code, stdout, stderr = _run_phase2_local(['retry', 'att_old'], cwd=project_root)

    assert code == 0, stderr
    assert seen['project_root'] == project_root.resolve()
    assert seen['target'] == 'att_old'
    assert 'retry_status: accepted' in stdout
    assert 'target: att_old' in stdout
    assert 'message_id: msg_1' in stdout
    assert 'original_attempt_id: att_old' in stdout
    assert 'attempt_id: att_new' in stdout
    assert 'job_id: job_new' in stdout
    assert 'agent_name: codex' in stdout
    assert 'status: accepted' in stdout


def test_phase2_doctor_ps_uses_converged_diagnostics_entrypoint(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-doctor-ps'
    project_root.mkdir()
    seen: dict[str, object] = {}

    def _fake_ps(context, command):
        seen['project_root'] = context.project.project_root
        seen['kind'] = command.kind
        return {
            'project_id': context.project.project_id,
            'ccbd_state': 'mounted',
            'agents': (),
        }

    monkeypatch.setattr(phase2_module, 'ps_summary', _fake_ps)

    code, stdout, stderr = _run_phase2_local(['doctor', 'ps'], cwd=project_root)

    assert code == 0, stderr
    assert seen['project_root'] == project_root.resolve()
    assert seen['kind'] == 'ps'
    assert 'ccbd_state: mounted' in stdout


def test_phase2_doctor_logs_uses_converged_diagnostics_entrypoint(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-doctor-logs'
    project_root.mkdir()
    seen: dict[str, object] = {}

    def _fake_logs(context, command):
        seen['project_root'] = context.project.project_root
        seen['agent_name'] = command.agent_name
        return SimpleNamespace(
            project_id=context.project.project_id,
            agent_name=command.agent_name,
            provider='codex',
            runtime_ref='rt_1',
            session_ref='sess_1',
            entries=(),
        )

    monkeypatch.setattr(phase2_module, 'agent_logs', _fake_logs)

    code, stdout, stderr = _run_phase2_local(['doctor', 'logs', 'codex'], cwd=project_root)

    assert code == 0, stderr
    assert seen['project_root'] == project_root.resolve()
    assert seen['agent_name'] == 'codex'
    assert 'logs_status: ok' in stdout
    assert 'agent_name: codex' in stdout


def test_phase2_wait_renders_satisfied_reply_summary(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-wait-render'
    project_root.mkdir()
    seen: dict[str, object] = {}

    def _fake_wait(context, command):
        seen['project_root'] = context.project.project_root
        seen['target'] = command.target
        seen['mode'] = command.mode
        return SimpleNamespace(
            project_id=context.project.project_id,
            mode=command.mode,
            target=command.target,
            resolved_kind='message',
            expected_count=2,
            received_count=2,
            waited_s=0.125,
            replies=(
                {
                    'reply_id': 'rep_1',
                    'message_id': 'msg_1',
                    'attempt_id': 'att_1',
                    'agent_name': 'codex',
                    'terminal_status': 'completed',
                    'reason': 'task_complete',
                    'finished_at': '2026-03-30T00:00:10Z',
                    'reply': 'done',
                },
            ),
        )

    monkeypatch.setattr(phase2_module, 'wait_for_replies', _fake_wait)

    code, stdout, stderr = _run_phase2_local(['wait-all', 'msg_1'], cwd=project_root)

    assert code == 0, stderr
    assert seen['project_root'] == project_root.resolve()
    assert seen['target'] == 'msg_1'
    assert seen['mode'] == 'all'
    assert 'wait_status: satisfied' in stdout
    assert 'mode: all' in stdout
    assert 'target: msg_1' in stdout
    assert 'resolved_kind: message' in stdout
    assert 'expected_count: 2' in stdout
    assert 'received_count: 2' in stdout
    assert 'reply_text: done' in stdout


def test_phase2_inbox_and_ack_render_control_plane_payload(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-inbox-render'
    project_root.mkdir()
    seen: dict[str, object] = {}

    def _fake_inbox(context, command):
        seen['inbox_project_root'] = context.project.project_root
        seen['inbox_agent_name'] = command.agent_name
        return {
            'target': command.agent_name,
            'summary_status': 'ok',
            'agent': {
                'agent_name': command.agent_name,
                'mailbox_id': f'mbx_{command.agent_name}',
                'mailbox_state': 'blocked',
                'lease_version': 0,
                'queue_depth': 1,
                'pending_reply_count': 1,
                'active_inbound_event_id': None,
            },
            'item_count': 1,
            'head': {
                'inbound_event_id': 'iev_1',
                'event_type': 'task_reply',
                'status': 'queued',
                'reply_id': 'rep_1',
                'source_actor': 'codex',
                'reply_terminal_status': 'completed',
                'reply_finished_at': '2026-03-30T00:00:10Z',
                'reply': 'done',
            },
            'items': (
                {
                    'position': 1,
                    'inbound_event_id': 'iev_1',
                    'event_type': 'task_reply',
                    'status': 'queued',
                    'priority': 10,
                    'message_id': 'msg_1',
                    'attempt_id': 'att_1',
                    'job_id': 'job_123',
                    'source_actor': 'codex',
                    'reply_id': 'rep_1',
                    'reply_terminal_status': 'completed',
                    'reply_preview': 'done',
                },
            ),
        }

    def _fake_ack(context, command):
        seen['ack_project_root'] = context.project.project_root
        seen['ack_agent_name'] = command.agent_name
        seen['ack_event'] = command.inbound_event_id
        return {
            'target': command.agent_name,
            'agent_name': command.agent_name,
            'acknowledged_inbound_event_id': command.inbound_event_id,
            'message_id': 'msg_1',
            'attempt_id': 'att_1',
            'reply_id': 'rep_1',
            'reply_from_agent': 'codex',
            'reply_terminal_status': 'completed',
            'reply_finished_at': '2026-03-30T00:00:10Z',
            'next_inbound_event_id': None,
            'next_event_type': None,
            'mailbox': {
                'mailbox_state': 'idle',
                'queue_depth': 0,
                'pending_reply_count': 0,
            },
            'reply': 'done',
        }

    monkeypatch.setattr(phase2_module, 'inbox_target', _fake_inbox)
    monkeypatch.setattr(phase2_module, 'ack_reply', _fake_ack)

    inbox_code, inbox_stdout, inbox_stderr = _run_phase2_local(['inbox', 'claude'], cwd=project_root)
    ack_code, ack_stdout, ack_stderr = _run_phase2_local(['ack', 'claude', 'iev_1'], cwd=project_root)

    assert inbox_code == 0, inbox_stderr
    assert seen['inbox_project_root'] == project_root.resolve()
    assert seen['inbox_agent_name'] == 'claude'
    assert 'inbox_status: ok' in inbox_stdout
    assert 'head_reply_id: rep_1' in inbox_stdout
    assert 'reply: done' in inbox_stdout

    assert ack_code == 0, ack_stderr
    assert seen['ack_project_root'] == project_root.resolve()
    assert seen['ack_agent_name'] == 'claude'
    assert seen['ack_event'] == 'iev_1'
    assert 'ack_status: ok' in ack_stdout
    assert 'acknowledged_inbound_event_id: iev_1' in ack_stdout


def test_phase2_repair_ack_uses_converged_recovery_entrypoint(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-repair-ack'
    project_root.mkdir()
    seen: dict[str, object] = {}

    def _fake_ack(context, command):
        seen['project_root'] = context.project.project_root
        seen['agent_name'] = command.agent_name
        seen['event'] = command.inbound_event_id
        return {
            'target': command.agent_name,
            'agent_name': command.agent_name,
            'acknowledged_inbound_event_id': command.inbound_event_id,
            'message_id': 'msg_1',
            'attempt_id': 'att_1',
            'job_id': 'job_1',
            'reply_id': 'rep_1',
            'reply_from_agent': 'codex',
            'reply_terminal_status': 'completed',
            'reply_notice': False,
            'reply_notice_kind': None,
            'reply_finished_at': '2026-03-30T00:00:10Z',
            'next_inbound_event_id': None,
            'next_event_type': None,
            'mailbox': {'mailbox_state': 'idle', 'queue_depth': 0, 'pending_reply_count': 0},
            'reply': 'done',
        }

    monkeypatch.setattr(phase2_module, 'ack_reply', _fake_ack)

    code, stdout, stderr = _run_phase2_local(['repair', 'ack', 'claude', 'iev_1'], cwd=project_root)

    assert code == 0, stderr
    assert seen['project_root'] == project_root.resolve()
    assert seen['agent_name'] == 'claude'
    assert seen['event'] == 'iev_1'
    assert 'ack_status: ok' in stdout
    assert 'acknowledged_inbound_event_id: iev_1' in stdout


def test_phase2_pend_watch_uses_converged_observer_entrypoint(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-pend-watch'
    project_root.mkdir()
    seen: dict[str, object] = {}

    def _fake_watch(context, command):
        seen['project_root'] = context.project.project_root
        seen['target'] = command.target
        yield SimpleNamespace(
            events=(),
            terminal=True,
            job_id='job_123',
            agent_name='claude',
            target_name='claude',
            status='completed',
            reply='done',
        )

    monkeypatch.setattr(phase2_module, 'watch_target', _fake_watch)

    code, stdout, stderr = _run_phase2_local(['pend', '--watch', 'claude'], cwd=project_root)

    assert code == 0, stderr
    assert seen['project_root'] == project_root.resolve()
    assert seen['target'] == 'claude'
    assert 'observer_view: watch' in stdout
    assert 'watch_status: terminal' in stdout
    assert 'reply: done' in stdout


def test_phase2_pend_inbox_uses_converged_observer_entrypoint(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-pend-inbox'
    project_root.mkdir()
    seen: dict[str, object] = {}

    def _fake_inbox(context, command):
        seen['project_root'] = context.project.project_root
        seen['agent_name'] = command.agent_name
        seen['detail'] = command.detail
        return {
            'target': command.agent_name,
            'summary_status': 'ok',
            'agent': {
                'agent_name': command.agent_name,
                'mailbox_id': f'mbx_{command.agent_name}',
                'mailbox_state': 'blocked',
                'lease_version': 0,
                'queue_depth': 1,
                'pending_reply_count': 1,
                'active_inbound_event_id': None,
            },
            'item_count': 1,
            'head': {
                'inbound_event_id': 'iev_1',
                'event_type': 'task_reply',
                'status': 'queued',
                'reply_id': 'rep_1',
                'source_actor': 'codex',
                'reply_terminal_status': 'completed',
                'reply_finished_at': '2026-03-30T00:00:10Z',
                'reply': 'done',
            },
            'items': (),
        }

    monkeypatch.setattr(phase2_module, 'inbox_target', _fake_inbox)

    code, stdout, stderr = _run_phase2_local(['pend', '--inbox', '--detail', 'claude'], cwd=project_root)

    assert code == 0, stderr
    assert seen['project_root'] == project_root.resolve()
    assert seen['agent_name'] == 'claude'
    assert seen['detail'] is True
    assert 'inbox_status: ok' in stdout
    assert 'target: claude' in stdout
    assert 'reply: done' in stdout


def test_phase2_pend_queue_uses_converged_observer_entrypoint(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-pend-queue'
    project_root.mkdir()
    seen: dict[str, object] = {}

    def _fake_queue(context, command):
        seen['project_root'] = context.project.project_root
        seen['target'] = command.target
        seen['detail'] = command.detail
        return {
            'target': command.target,
            'agent': {
                'agent_name': 'claude',
                'mailbox_id': 'mbx_claude',
                'summary_status': 'ok',
                'mailbox_state': 'blocked',
                'runtime_state': 'busy',
                'runtime_health': 'restored',
                'lease_version': 1,
                'queue_depth': 1,
                'pending_reply_count': 0,
                'active_inbound_event_id': 'iev_1',
                'last_inbound_started_at': '2026-03-30T00:00:00Z',
                'last_inbound_finished_at': None,
                'queued_events': (),
            },
        }

    monkeypatch.setattr(phase2_module, 'queue_target', _fake_queue)

    code, stdout, stderr = _run_phase2_local(['pend', '--queue', '--detail', 'claude'], cwd=project_root)

    assert code == 0, stderr
    assert seen['project_root'] == project_root.resolve()
    assert seen['target'] == 'claude'
    assert seen['detail'] is True
    assert 'queue_status: ok' in stdout
    assert 'target: claude' in stdout
    assert 'queue_depth: 1' in stdout


def _wait_for_phase2_status(cwd: Path, target: str, expected: str, *, timeout: float = 5.0) -> str:
    deadline = time.time() + timeout
    last_stdout = ''
    last_stderr = ''
    while time.time() < deadline:
        code, stdout, stderr = _run_phase2_local(['pend', target], cwd=cwd)
        last_stdout, last_stderr = stdout, stderr
        if code == 0 and f'status: {expected}' in stdout:
            return stdout
        time.sleep(0.05)
    raise AssertionError(f'expected status {expected!r}; last stdout={last_stdout!r} stderr={last_stderr!r}')


DUAL_PROVIDER_STATUS_TIMEOUT = 20.0


def _wait_for_path(path: Path, timeout: float = 5.0) -> None:
    deadline = time.time() + timeout
    last_error: str | None = None
    while time.time() < deadline:
        if path.exists():
            if path.suffix != '.sock':
                return
            try:
                CcbdClient(path, timeout_s=0.2).ping('ccbd')
                return
            except CcbdClientError as exc:
                last_error = str(exc)
        time.sleep(0.02)
    suffix = f' last_error={last_error!r}' if last_error else ''
    raise AssertionError(f'timed out waiting for {path}{suffix}')


def _wait_for_ccbd_ping_payload(project_root: Path, *, timeout: float = 5.0) -> dict[str, object]:
    socket_path = PathLayout(project_root).ccbd_socket_path
    _wait_for_path(socket_path, timeout=timeout)
    deadline = time.time() + timeout
    last_error: str | None = None
    while time.time() < deadline:
        try:
            return CcbdClient(socket_path, timeout_s=0.2).ping('ccbd')
        except CcbdClientError as exc:
            last_error = str(exc)
        time.sleep(0.05)
    suffix = f' last_error={last_error!r}' if last_error else ''
    raise AssertionError(f'timed out waiting for ccbd ping payload{suffix}')


def _wait_for_ping_unmounted(cwd: Path, target: str, *, timeout: float = 5.0) -> subprocess.CompletedProcess[str]:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = _run_ccb(['ping', target], cwd=cwd)
        if last.returncode == 0 and 'mount_state: unmounted' in last.stdout:
            return last
        time.sleep(0.05)
    raise AssertionError(f'expected {target!r} to become unmounted; last stdout={last.stdout!r} stderr={last.stderr!r}')


def _tmux_cmd_pane_id(socket_path: str, session_name: str, *, timeout: float = 3.0) -> str:
    deadline = time.time() + timeout
    last_stdout = ''
    while time.time() < deadline:
        proc = subprocess.run(
            [
                'tmux',
                '-S',
                socket_path,
                'list-panes',
                '-t',
                session_name,
                '-F',
                '#{pane_id}\t#{@ccb_role}\t#{pane_current_command}',
            ],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        last_stdout = proc.stdout
        if proc.returncode == 0:
            for line in (proc.stdout or '').splitlines():
                pane_id, _sep, rest = line.partition('\t')
                _role, _sep2, _command = rest.partition('\t')
                if _role.strip() == 'cmd' and pane_id.strip().startswith('%'):
                    return pane_id.strip()
        time.sleep(0.05)
    raise AssertionError(f'failed to resolve cmd pane; last_stdout={last_stdout!r}')


def _tmux_wait_for_pane_text(socket_path: str, pane_id: str, marker: str, *, timeout: float = 3.0) -> str:
    deadline = time.time() + timeout
    last_text = ''
    while time.time() < deadline:
        proc = subprocess.run(
            ['tmux', '-S', socket_path, 'capture-pane', '-p', '-t', pane_id],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        if proc.returncode == 0:
            last_text = proc.stdout
            if marker in last_text:
                return last_text
        time.sleep(0.05)
    raise AssertionError(f'failed to observe marker {marker!r} in pane {pane_id}; last_text={last_text!r}')


def _wait_for_file_text(path: Path, marker: str, *, timeout: float = 3.0) -> str:
    deadline = time.time() + timeout
    last_text = ''
    while time.time() < deadline:
        try:
            last_text = path.read_text(encoding='utf-8')
        except Exception:
            time.sleep(0.05)
            continue
        if marker in last_text:
            return last_text
        time.sleep(0.05)
    raise AssertionError(f'failed to observe marker {marker!r} in file {path}; last_text={last_text!r}')


def _assert_phase2_app_shutdown_clean(project_root: Path, app: CcbdApp, thread: threading.Thread) -> None:
    app.request_shutdown()
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert not app.paths.ccbd_socket_path.exists()
    lease_path = app.paths.ccbd_lease_path
    assert lease_path.exists()
    lease = json.loads(lease_path.read_text(encoding='utf-8'))
    assert lease['mount_state'] == 'unmounted'
    runtime_root = PathLayout(project_root).agents_dir
    pid_files = sorted(runtime_root.glob('*/provider-runtime/**/*.pid'))
    assert pid_files == []


def _wait_for_pid_exit(pid: int, timeout: float = 2.0) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.02)
    raise AssertionError(f'timed out waiting for pid {pid} to exit')


@pytest.mark.ccb_lifecycle_smoke
def test_ccb_v2_project_lifecycle(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv('STUB_DELAY', '2.0')
    project_root = tmp_path / 'repo'
    _write(project_root / '.ccb' / 'ccb.config', _config_text())

    proc = _run_ccb([], cwd=project_root)
    assert proc.returncode == 0, proc.stderr
    assert 'start_status: ok' in proc.stdout
    assert 'agents: codex' in proc.stdout

    ping = _run_ccb(['ping', 'codex'], cwd=project_root)
    assert ping.returncode == 0, ping.stderr
    assert 'agent_name: codex' in ping.stdout
    assert 'provider: codex' in ping.stdout

    ps = _run_ccb(['ps'], cwd=project_root)
    assert ps.returncode == 0, ps.stderr
    assert 'ccbd_state: mounted' in ps.stdout
    assert 'agent: name=codex state=idle provider=codex queue=0' in ps.stdout
    assert f'workspace={project_root.resolve()}' in ps.stdout

    doctor = _wait_for_doctor_line(
        project_root,
        'agent: name=codex health=restored provider=codex completion=protocol_turn',
    )
    assert f'project: {project_root.resolve()}' in doctor.stdout
    assert 'ccbd_generation: 1' in doctor.stdout
    assert 'ccbd_reason: healthy' in doctor.stdout
    assert 'agent: name=codex health=restored provider=codex completion=protocol_turn' in doctor.stdout
    assert f'workspace={project_root.resolve()}' in doctor.stdout

    ask = _run_ccb(['ask', 'codex', 'from', 'user', 'hello from test'], cwd=project_root)
    assert ask.returncode == 0, ask.stderr
    job_id = _extract_accepted_job_id(ask.stdout, target='codex')

    observed = _wait_for_any_status(project_root, 'codex', ('running', 'completed'))
    assert f'job_id: {job_id}' in observed.stdout

    if 'status: running' in observed.stdout:
        queue = _run_ccb(['queue', 'codex'], cwd=project_root)
        assert queue.returncode == 0, queue.stderr
        assert 'queue_status: ok' in queue.stdout
        assert 'target: codex' in queue.stdout
        assert 'runtime_health: restored' in queue.stdout
        assert 'queue_details: omitted; rerun with `ccb pend --queue --detail <agent>` or `ccb queue --detail <agent>` for queued-event detail' in queue.stdout

        queue_detail = _run_ccb(['queue', '--detail', 'codex'], cwd=project_root)
        assert queue_detail.returncode == 0, queue_detail.stderr
        assert 'observer_view: queue' in queue_detail.stdout
        assert 'queue_details: omitted' not in queue_detail.stdout

        cancel = _run_ccb(['cancel', job_id], cwd=project_root)
        if cancel.returncode == 0:
            assert 'cancel_status: ok' in cancel.stdout
            assert 'status: cancelled' in cancel.stdout

            terminal = _wait_for_status(project_root, job_id, 'cancelled')
            assert 'completion_reason: cancel_info' in terminal.stdout

            watch = _run_ccb(['watch', job_id], cwd=project_root)
            assert watch.returncode == 0, watch.stderr
            assert 'event:' in watch.stdout
            assert 'watch_status: terminal' in watch.stdout
            assert f'job_id: {job_id}' in watch.stdout
            assert 'status: cancelled' in watch.stdout

            watch_agent = _run_ccb(['watch', 'codex'], cwd=project_root)
            assert watch_agent.returncode == 0, watch_agent.stderr
            assert 'watch_status: terminal' in watch_agent.stdout
            assert f'job_id: {job_id}' in watch_agent.stdout
        else:
            assert 'job is already terminal: completed' in cancel.stderr
            terminal = _wait_for_status(project_root, job_id, 'completed')
            assert 'completion_reason: task_complete' in terminal.stdout
    else:
        completed = _wait_for_status(project_root, job_id, 'completed')
        assert 'reply: stub reply for' in completed.stdout
        assert 'completion_reason: task_complete' in completed.stdout

        watch = _run_ccb(['watch', job_id], cwd=project_root)
        assert watch.returncode == 0, watch.stderr
        assert 'event:' in watch.stdout
        assert 'watch_status: terminal' in watch.stdout
        assert f'job_id: {job_id}' in watch.stdout
        assert 'status: completed' in watch.stdout

        watch_agent = _run_ccb(['watch', 'codex'], cwd=project_root)
        assert watch_agent.returncode == 0, watch_agent.stderr
        assert 'watch_status: terminal' in watch_agent.stdout
        assert f'job_id: {job_id}' in watch_agent.stdout

    kill = _run_ccb(['kill'], cwd=project_root)
    assert kill.returncode == 0, kill.stderr


def test_ccb_cmd_pane_blackbox_inherits_user_session_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if shutil.which('tmux') is None:
        pytest.skip('tmux is required for cmd pane blackbox env regression')

    project_root = tmp_path / 'repo-cmd-env-blackbox'
    _write(project_root / '.ccb' / 'ccb.config', 'cmd; demo:codex\n')

    shell_path = shutil.which('sh') or '/bin/sh'
    sentinel_display = 'ccb-test-display'
    sentinel_xauthority = '/tmp/ccb-test-xauthority'
    sentinel_dbus = 'unix:path=/tmp/ccb-test-bus'
    sentinel_wayland = 'ccb-test-wayland'
    monkeypatch.setenv('CCB_CMD_SHELL', shell_path)
    monkeypatch.setenv('SHELL', shell_path)
    monkeypatch.setenv('DISPLAY', sentinel_display)
    monkeypatch.setenv('XAUTHORITY', sentinel_xauthority)
    monkeypatch.setenv('DBUS_SESSION_BUS_ADDRESS', sentinel_dbus)
    monkeypatch.setenv('WAYLAND_DISPLAY', sentinel_wayland)

    start = _run_ccb([], cwd=project_root)
    assert start.returncode == 0, start.stderr
    assert 'start_status: ok' in start.stdout

    try:
        payload = _wait_for_ccbd_ping_payload(project_root)
        tmux_socket_path = str(payload.get('namespace_tmux_socket_path') or '').strip()
        tmux_session_name = str(payload.get('namespace_tmux_session_name') or '').strip()
        assert tmux_socket_path
        assert tmux_session_name

        cmd_pane_id = _tmux_cmd_pane_id(tmux_socket_path, tmux_session_name)
        marker = 'CCB_CMD_ENV_MARKER'
        env_dump_path = project_root / '.ccb' / 'cmd-env.txt'
        command = (
            f'printf "{marker} SHELL=%s DISPLAY=%s XAUTHORITY=%s DBUS=%s WAYLAND=%s\\n" '
            f'"$SHELL" "$DISPLAY" "$XAUTHORITY" "$DBUS_SESSION_BUS_ADDRESS" "$WAYLAND_DISPLAY" > {env_dump_path}'
        )
        send = subprocess.run(
            ['tmux', '-S', tmux_socket_path, 'send-keys', '-t', cmd_pane_id, command, 'C-m'],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert send.returncode == 0, send.stderr

        env_text = _wait_for_file_text(env_dump_path, marker)
        assert f'{marker} SHELL={shell_path}' in env_text
        assert f'DISPLAY={sentinel_display}' in env_text
        assert f'XAUTHORITY={sentinel_xauthority}' in env_text
        assert f'DBUS={sentinel_dbus}' in env_text
        assert f'WAYLAND={sentinel_wayland}' in env_text
    finally:
        kill = _run_ccb(['kill', '-f'], cwd=project_root)
        assert kill.returncode == 0, kill.stderr
    assert 'kill_status: ok' in kill.stdout
    assert 'state: stopping' in kill.stdout or 'state: unmounted' in kill.stdout

    ping_after = _wait_for_ping_unmounted(project_root, 'codex')
    assert 'mount_state: unmounted' in ping_after.stdout
    assert 'health: unmounted' in ping_after.stdout


def test_ccb_logs_reads_agent_runtime_logs(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-logs'
    _write(project_root / '.ccb' / 'ccb.config', _named_agent_config_text('demo', 'codex'))
    _write(
        project_root / '.ccb' / 'agents' / 'demo' / 'provider-runtime' / 'codex' / 'bridge.log',
        'first line\nsecond line\n',
    )

    proc = _run_ccb(['logs', 'demo'], cwd=project_root)

    assert proc.returncode == 0, proc.stderr
    assert 'logs_status: ok' in proc.stdout
    assert 'agent_name: demo' in proc.stdout
    assert 'log_count: 1' in proc.stdout
    assert 'log: runtime ' in proc.stdout
    assert 'log_line: first line' in proc.stdout
    assert 'log_line: second line' in proc.stdout


@pytest.mark.ccb_lifecycle_smoke
def test_ccb_ping_ccbd_recovers_from_stale_mount_and_bumps_generation(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-stale-ccbd'
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('fake'))

    start = _run_ccb([], cwd=project_root)
    assert start.returncode == 0, start.stderr

    lease_path = project_root / '.ccb' / 'ccbd' / 'lease.json'
    lease = json.loads(lease_path.read_text(encoding='utf-8'))
    stale_pid = int(lease['ccbd_pid'])
    os.kill(stale_pid, signal.SIGTERM)
    _wait_for_pid_exit(stale_pid)

    socket_path = Path(str(lease['socket_path']))
    try:
        socket_path.unlink()
    except FileNotFoundError:
        pass

    lease['last_heartbeat_at'] = '2026-03-01T00:00:00Z'
    lease_path.write_text(json.dumps(lease, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    ping = _run_ccb(['ping', 'ccbd'], cwd=project_root)
    assert ping.returncode == 0, ping.stderr
    assert 'mount_state: mounted' in ping.stdout
    assert 'health: healthy' in ping.stdout
    assert 'generation: 2' in ping.stdout
    assert 'pid_alive: True' in ping.stdout
    assert 'socket_connectable: True' in ping.stdout
    assert 'heartbeat_fresh: True' in ping.stdout
    assert 'takeover_allowed: False' in ping.stdout
    assert 'reason: healthy' in ping.stdout

    doctor = _run_ccb(['doctor'], cwd=project_root)
    assert doctor.returncode == 0, doctor.stderr
    assert 'ccbd_state: mounted' in doctor.stdout
    assert 'ccbd_health: healthy' in doctor.stdout
    assert 'ccbd_generation: 2' in doctor.stdout
    assert 'ccbd_reason: healthy' in doctor.stdout

    kill = _run_ccb(['kill'], cwd=project_root)
    assert kill.returncode == 0, kill.stderr


@pytest.mark.ccb_lifecycle_smoke
def test_ccb_long_running_job_keeps_heartbeat_and_doctor_healthy(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-heartbeat'
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('fake'))

    start = _run_ccb([], cwd=project_root)
    assert start.returncode == 0, start.stderr

    ask = _run_ccb(
        ['ask', '--task-id', 'fake;latency_ms=4000', 'demo', 'from', 'user', 'heartbeat probe'],
        cwd=project_root,
    )
    assert ask.returncode == 0, ask.stderr
    job_id = _extract_accepted_job_id(ask.stdout, target='demo')

    running = _wait_for_status(project_root, 'demo', 'running', timeout=2.0)
    assert f'job_id: {job_id}' in running.stdout

    lease_path = project_root / '.ccb' / 'ccbd' / 'lease.json'
    lease_before = json.loads(lease_path.read_text(encoding='utf-8'))
    doctor_1 = _run_ccb(['doctor'], cwd=project_root)
    assert doctor_1.returncode == 0, doctor_1.stderr
    assert 'ccbd_state: mounted' in doctor_1.stdout
    assert 'ccbd_health: healthy' in doctor_1.stdout
    assert 'ccbd_pid_alive: True' in doctor_1.stdout
    assert 'ccbd_socket_connectable: True' in doctor_1.stdout
    assert 'ccbd_heartbeat_fresh: True' in doctor_1.stdout
    assert 'ccbd_takeover_allowed: False' in doctor_1.stdout
    assert 'ccbd_reason: healthy' in doctor_1.stdout
    assert 'ccbd_active_execution_count: 1' in doctor_1.stdout
    assert 'ccbd_recoverable_execution_count: 1' in doctor_1.stdout
    assert 'ccbd_nonrecoverable_execution_count: 0' in doctor_1.stdout
    assert 'ccbd_pending_items_count:' in doctor_1.stdout
    assert 'ccbd_terminal_pending_count:' in doctor_1.stdout

    ping = _wait_for_ccbd_execution_summary(
        project_root,
        active_execution_count=1,
        recoverable_execution_count=1,
        timeout=2.0,
    )
    assert ping.returncode == 0, ping.stderr
    assert 'active_execution_count: 1' in ping.stdout
    assert 'recoverable_execution_count: 1' in ping.stdout
    assert 'nonrecoverable_execution_count: 0' in ping.stdout
    assert 'pending_items_count:' in ping.stdout
    assert 'terminal_pending_count:' in ping.stdout

    time.sleep(0.5)

    lease_after = json.loads(lease_path.read_text(encoding='utf-8'))
    assert lease_after['last_heartbeat_at'] != lease_before['last_heartbeat_at']

    doctor_2 = _run_ccb(['doctor'], cwd=project_root)
    assert doctor_2.returncode == 0, doctor_2.stderr
    assert f'ccbd_last_heartbeat_at: {lease_after["last_heartbeat_at"]}' in doctor_2.stdout or 'ccbd_last_heartbeat_at:' in doctor_2.stdout
    assert 'ccbd_health: healthy' in doctor_2.stdout

    completed = _wait_for_status(project_root, job_id, 'completed', timeout=5.0)
    assert 'reply: FAKE[demo] heartbeat probe' in completed.stdout

    kill = _run_ccb(['kill'], cwd=project_root)
    assert kill.returncode == 0, kill.stderr


@pytest.mark.ccb_lifecycle_smoke
def test_ccb_fake_provider_recovers_running_execution_after_ccbd_restart(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-resume-fake'
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('fake'))

    start = _run_ccb([], cwd=project_root)
    assert start.returncode == 0, start.stderr

    ask = _run_ccb(
        ['ask', '--task-id', 'fake;latency_ms=1500', 'demo', 'from', 'user', 'resume after restart'],
        cwd=project_root,
    )
    assert ask.returncode == 0, ask.stderr
    job_id = _extract_accepted_job_id(ask.stdout, target='demo')

    running = _wait_for_status(project_root, 'demo', 'running', timeout=2.0)
    assert f'job_id: {job_id}' in running.stdout
    execution_path = project_root / '.ccb' / 'ccbd' / 'executions' / f'{job_id}.json'
    _wait_for_path(execution_path)

    lease_path = project_root / '.ccb' / 'ccbd' / 'lease.json'
    lease = json.loads(lease_path.read_text(encoding='utf-8'))
    stale_pid = int(lease['ccbd_pid'])
    os.kill(stale_pid, signal.SIGTERM)
    _wait_for_pid_exit(stale_pid)

    ping = _run_ccb(['ping', 'ccbd'], cwd=project_root)
    assert ping.returncode == 0, ping.stderr
    assert 'mount_state: mounted' in ping.stdout
    assert 'health: healthy' in ping.stdout
    assert 'generation: 2' in ping.stdout
    assert 'last_restore_running_job_count: 1' in ping.stdout
    assert 'last_restore_restored_execution_count: 1' in ping.stdout
    assert 'last_restore_replay_pending_count: 0' in ping.stdout
    assert 'last_restore_abandoned_execution_count: 0' in ping.stdout
    assert 'last_restore_results_text: demo/fake:restored(provider_resumed)' in ping.stdout

    doctor = _run_ccb(['doctor'], cwd=project_root)
    assert doctor.returncode == 0, doctor.stderr
    assert 'ccbd_last_restore_running_job_count: 1' in doctor.stdout
    assert 'ccbd_last_restore_restored_execution_count: 1' in doctor.stdout
    assert 'ccbd_last_restore_results_text: demo/fake:restored(provider_resumed)' in doctor.stdout

    completed = _wait_for_status(project_root, job_id, 'completed', timeout=5.0)
    assert 'reply: FAKE[demo] resume after restart' in completed.stdout
    assert not execution_path.exists()

    watch = _run_ccb(['watch', job_id], cwd=project_root)
    assert watch.returncode == 0, watch.stderr
    assert 'watch_status: terminal' in watch.stdout
    assert f'job_id: {job_id}' in watch.stdout

    kill = _run_ccb(['kill'], cwd=project_root)
    assert kill.returncode == 0, kill.stderr


def test_ccb_doctor_and_ping_expose_opencode_restore_degradation(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-opencode-capability'
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('opencode'))

    start = _run_ccb([], cwd=project_root)
    assert start.returncode == 0, start.stderr

    doctor = _run_ccb(['doctor'], cwd=project_root)
    assert doctor.returncode == 0, doctor.stderr
    assert 'restore: supported=False mode=resubmit_required reason=provider_resume_unsupported' in doctor.stdout
    assert 'restore_detail: opencode live polling works, but restart-time execution resume is not implemented yet' in doctor.stdout

    ping = _run_ccb(['ping', 'demo'], cwd=project_root)
    assert ping.returncode == 0, ping.stderr
    assert "'resume_supported': False" in ping.stdout
    assert "'restore_mode': 'resubmit_required'" in ping.stdout
    assert "'restore_reason': 'provider_resume_unsupported'" in ping.stdout

    kill = _run_ccb(['kill'], cwd=project_root)
    assert kill.returncode == 0, kill.stderr


@pytest.mark.provider_blackbox
def test_ccb_opencode_real_adapter_blackbox_pane_dead_fails_degraded(monkeypatch, tmp_path: Path) -> None:
    from provider_execution import opencode as opencode_adapter_module

    fixed_req_id = 'job_0dea0d'
    project_root = tmp_path / 'repo-opencode-dead'
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('opencode'))

    class DeadBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            self.sent = (pane_id, text)

        def is_alive(self, pane_id: str) -> bool:
            del pane_id
            return False

    class FakeSession:
        data = {}
        opencode_project_id = 'proj-demo'
        opencode_session_id_filter = 'ses-demo'
        work_dir = str(project_root)

        def ensure_pane(self):
            return True, '%4'

    class EmptyReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def capture_state(self):
            return {'session_path': str(tmp_path / 'opencode-session.json'), 'session_id': 'ses-demo'}

        def try_get_message(self, state):
            return None, state

    monkeypatch.setattr(opencode_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(opencode_adapter_module, 'get_backend_for_session', lambda data: DeadBackend())
    monkeypatch.setattr(opencode_adapter_module, 'OpenCodeLogReader', EmptyReader)
    app = CcbdApp(project_root)
    _freeze_job_ids(app, monkeypatch, fixed_req_id)
    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for_path(app.paths.ccbd_socket_path)

    try:
        code, stdout, stderr = _run_phase2_local([], cwd=project_root, start_app=app)
        assert code == 0, stderr

        code, stdout, stderr = _run_phase2_local(['ask', 'demo', 'from', 'user', 'hello opencode dead'], cwd=project_root)
        assert code == 0, stderr
        job_id = _extract_accepted_job_id(stdout, target='demo')

        pend = _wait_for_phase2_status(project_root, job_id, 'failed', timeout=5.0)
        assert 'reply: \n' in pend or pend.rstrip().endswith('reply:')
        assert 'completion_reason: pane_dead' in pend
        assert 'completion_confidence: degraded' in pend

        code, stdout, stderr = _run_phase2_local(['watch', job_id], cwd=project_root)
        assert code == 0, stderr
        assert 'watch_status: terminal' in stdout
        assert f'job_id: {job_id}' in stdout
        assert 'status: failed' in stdout
    finally:
        app.shutdown()
        thread.join(timeout=2)
        assert not thread.is_alive()


@pytest.mark.provider_blackbox
def test_ccb_opencode_real_adapter_blackbox_completed_reply_without_done_marker(monkeypatch, tmp_path: Path) -> None:
    from provider_execution import opencode as opencode_adapter_module

    project_root = tmp_path / 'repo-opencode-legacy'
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('opencode'))
    anchor: dict[str, str | None] = {'req_id': None}

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            self.sent = (pane_id, text)
            match = re.search(r'^CCB_REQ_ID:\s*(\S+)\s*$', text, re.MULTILINE)
            anchor['req_id'] = match.group(1) if match else None

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%4'

    class FakeSession:
        data = {}
        opencode_project_id = 'proj-demo'
        opencode_session_id_filter = 'ses-demo'
        work_dir = str(project_root)

        def ensure_pane(self):
            return True, '%4'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def capture_state(self):
            return {'session_path': str(tmp_path / 'opencode-session.json'), 'session_id': 'ses-demo'}

        def try_get_message(self, state):
            return (
                'legacy final',
                {
                    **state,
                    'last_assistant_id': 'msg-final',
                    'last_assistant_parent_id': 'msg-user',
                    'last_assistant_req_id': anchor["req_id"],
                    'last_assistant_completed': 1234,
                },
            )

    monkeypatch.setattr(opencode_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(opencode_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(opencode_adapter_module, 'OpenCodeLogReader', FakeReader)

    app = CcbdApp(project_root)
    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for_path(app.paths.ccbd_socket_path)

    try:
        code, stdout, stderr = _run_phase2_local([], cwd=project_root, start_app=app)
        assert code == 0, stderr

        code, stdout, stderr = _run_phase2_local(['ask', 'demo', 'from', 'user', 'hello opencode'], cwd=project_root)
        assert code == 0, stderr
        job_id = _extract_accepted_job_id(stdout, target='demo')

        pend = _wait_for_phase2_status(project_root, job_id, 'completed', timeout=5.0)
        assert 'reply: legacy final' in pend
        assert 'completion_reason: assistant_completed' in pend
        assert 'completion_confidence: observed' in pend

        code, stdout, stderr = _run_phase2_local(['watch', job_id], cwd=project_root)
        assert code == 0, stderr
        assert 'watch_status: terminal' in stdout
        assert f'job_id: {job_id}' in stdout
        assert 'status: completed' in stdout
    finally:
        app.shutdown()
        thread.join(timeout=2)
        assert not thread.is_alive()


@pytest.mark.provider_blackbox
def test_ccb_opencode_real_adapter_blackbox_cancel_stops_legacy_completion(monkeypatch, tmp_path: Path) -> None:
    from provider_execution import opencode as opencode_adapter_module

    project_root = tmp_path / 'repo-opencode-cancel'
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('opencode'))
    anchor: dict[str, str | None] = {'req_id': None}

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            self.sent = (pane_id, text)
            match = re.search(r'^CCB_REQ_ID:\s*(\S+)\s*$', text, re.MULTILINE)
            anchor['req_id'] = match.group(1) if match else None

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%4'

    class FakeSession:
        data = {}
        opencode_project_id = 'proj-demo'
        opencode_session_id_filter = 'ses-demo'
        work_dir = str(project_root)

        def ensure_pane(self):
            return True, '%4'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._calls = 0

        def capture_state(self):
            return {'session_path': str(tmp_path / 'opencode-session.json'), 'session_id': 'ses-demo'}

        def try_get_message(self, state):
            self._calls += 1
            if self._calls == 1:
                return (
                    'partial before cancel',
                    {
                        **state,
                        'last_assistant_id': 'msg-partial',
                        'last_assistant_parent_id': 'msg-user',
                        'last_assistant_req_id': anchor["req_id"],
                        'last_assistant_completed': None,
                    },
                )
            if self._calls < 50:
                return None, state
            return (
                'final after cancel',
                {
                    **state,
                    'last_assistant_id': 'msg-final',
                    'last_assistant_parent_id': 'msg-user',
                    'last_assistant_req_id': anchor["req_id"],
                    'last_assistant_completed': 9999,
                },
            )

    monkeypatch.setattr(opencode_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(opencode_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(opencode_adapter_module, 'OpenCodeLogReader', FakeReader)

    app = CcbdApp(project_root)
    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for_path(app.paths.ccbd_socket_path)

    try:
        code, stdout, stderr = _run_phase2_local([], cwd=project_root, start_app=app)
        assert code == 0, stderr

        code, stdout, stderr = _run_phase2_local(['ask', 'demo', 'from', 'user', 'hello opencode cancel'], cwd=project_root)
        assert code == 0, stderr
        job_id = _extract_accepted_job_id(stdout, target='demo')

        running = _wait_for_phase2_status(project_root, 'demo', 'running', timeout=3.0)
        assert f'job_id: {job_id}' in running
        assert 'observer_notice: weak observer surface; non-terminal state may change; use ccb trace <id> for lineage when needed' in running

        code, stdout, stderr = _run_phase2_local(['cancel', job_id], cwd=project_root)
        assert code == 0, stderr
        assert 'cancel_status: ok' in stdout
        assert 'status: cancelled' in stdout

        pend = _wait_for_phase2_status(project_root, job_id, 'cancelled', timeout=5.0)
        assert 'completion_reason: cancel_info' in pend
        assert 'completion_confidence: degraded' in pend
        assert 'final after cancel' not in pend

        code, stdout, stderr = _run_phase2_local(['watch', job_id], cwd=project_root)
        assert code == 0, stderr
        assert 'watch_status: terminal' in stdout
        assert 'status: cancelled' in stdout
    finally:
        app.shutdown()
        thread.join(timeout=2)
        assert not thread.is_alive()


@pytest.mark.provider_blackbox
def test_ccb_droid_real_adapter_blackbox_pane_dead_fails_degraded(monkeypatch, tmp_path: Path) -> None:
    from provider_execution import droid as droid_adapter_module

    fixed_req_id = 'job_d00d10'
    project_root = tmp_path / 'repo-droid-dead'
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('droid'))
    _write(
        project_root / '.ccb' / '.droid-session',
        json.dumps(
            {
                'terminal': 'tmux',
                'pane_id': '%5',
                'work_dir': str(project_root),
                'droid_session_id': 'droid-session-id',
                'droid_session_path': str(tmp_path / 'droid-session.jsonl'),
            },
            ensure_ascii=False,
            indent=2,
        ) + '\n',
    )

    class DeadBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            self.sent = (pane_id, text)

        def is_alive(self, pane_id: str) -> bool:
            del pane_id
            return False

    class FakeSession:
        data = {}
        droid_session_path = str(tmp_path / 'droid-session.jsonl')
        droid_session_id = 'droid-session-id'
        work_dir = str(project_root)

        def ensure_pane(self):
            return True, '%5'

    class EmptyReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def set_session_id_hint(self, session_id) -> None:
            del session_id

        def capture_state(self):
            return {'session_path': str(tmp_path / 'droid-session.jsonl'), 'offset': 0}

        def try_get_events(self, state):
            return [], state

    monkeypatch.setattr(droid_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(droid_adapter_module, 'get_backend_for_session', lambda data: DeadBackend())
    monkeypatch.setattr(droid_adapter_module, 'DroidLogReader', EmptyReader)
    app = CcbdApp(project_root)
    _freeze_job_ids(app, monkeypatch, fixed_req_id)
    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for_path(app.paths.ccbd_socket_path)

    try:
        code, stdout, stderr = _run_phase2_local([], cwd=project_root, start_app=app)
        assert code == 0, stderr

        code, stdout, stderr = _run_phase2_local(['ask', 'demo', 'from', 'user', 'hello droid dead'], cwd=project_root)
        assert code == 0, stderr
        job_id = _extract_accepted_job_id(stdout, target='demo')

        pend = _wait_for_phase2_status(project_root, job_id, 'failed', timeout=5.0)
        assert 'reply: \n' in pend or pend.rstrip().endswith('reply:')
        assert 'completion_reason: pane_dead' in pend
        assert 'completion_confidence: degraded' in pend

        code, stdout, stderr = _run_phase2_local(['watch', job_id], cwd=project_root)
        assert code == 0, stderr
        assert 'watch_status: terminal' in stdout
        assert f'job_id: {job_id}' in stdout
        assert 'status: failed' in stdout
    finally:
        app.shutdown()
        thread.join(timeout=2)
        assert not thread.is_alive()


@pytest.mark.provider_blackbox
def test_ccb_droid_real_adapter_blackbox_terminal_done_marker_completion(monkeypatch, tmp_path: Path) -> None:
    from provider_execution import droid as droid_adapter_module

    project_root = tmp_path / 'repo-droid-legacy'
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('droid'))
    anchor: dict[str, str | None] = {'req_id': None}
    _write(
        project_root / '.ccb' / '.droid-session',
        json.dumps(
            {
                'terminal': 'tmux',
                'pane_id': '%5',
                'work_dir': str(project_root),
                'droid_session_id': 'droid-session-id',
                'droid_session_path': str(tmp_path / 'droid-session.jsonl'),
            },
            ensure_ascii=False,
            indent=2,
        ) + '\n',
    )

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            self.sent = (pane_id, text)
            match = re.search(r'^CCB_REQ_ID:\s*(\S+)\s*$', text, re.MULTILINE)
            anchor['req_id'] = match.group(1) if match else None

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%5'

    class FakeSession:
        data = {}
        droid_session_path = str(tmp_path / 'droid-session.jsonl')
        droid_session_id = 'droid-session-id'
        work_dir = str(project_root)

        def ensure_pane(self):
            return True, '%5'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def set_session_id_hint(self, session_id) -> None:
            del session_id

        def capture_state(self):
            return {'session_path': str(tmp_path / 'droid-session.jsonl'), 'offset': 0}

        def try_get_events(self, state):
            events = [
                ('user', f'CCB_REQ_ID: {anchor["req_id"]}\n\nprompt'),
                ('assistant', 'partial'),
                ('assistant', f'final\nCCB_DONE: {anchor["req_id"]}'),
            ]
            index = int(state.get('index', 0))
            if index >= len(events):
                return [], state
            return [events[index]], {**state, 'index': index + 1}

    monkeypatch.setattr(droid_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(droid_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(droid_adapter_module, 'DroidLogReader', FakeReader)

    app = CcbdApp(project_root)
    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for_path(app.paths.ccbd_socket_path)

    try:
        code, stdout, stderr = _run_phase2_local([], cwd=project_root, start_app=app)
        assert code == 0, stderr

        code, stdout, stderr = _run_phase2_local(['ask', 'demo', 'from', 'user', 'hello droid'], cwd=project_root)
        assert code == 0, stderr
        job_id = _extract_accepted_job_id(stdout, target='demo')

        pend = _wait_for_phase2_status(project_root, job_id, 'completed', timeout=5.0)
        assert 'reply: partial\nfinal' in pend
        assert 'completion_reason: terminal_done_marker' in pend
        assert 'completion_confidence: degraded' in pend

        code, stdout, stderr = _run_phase2_local(['watch', job_id], cwd=project_root)
        assert code == 0, stderr
        assert 'watch_status: terminal' in stdout
        assert f'job_id: {job_id}' in stdout
        assert 'status: completed' in stdout
    finally:
        app.shutdown()
        thread.join(timeout=2)
        assert not thread.is_alive()


@pytest.mark.provider_blackbox
def test_ccb_droid_real_adapter_blackbox_cancel_stops_legacy_completion(monkeypatch, tmp_path: Path) -> None:
    from provider_execution import droid as droid_adapter_module

    project_root = tmp_path / 'repo-droid-cancel'
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('droid'))
    anchor: dict[str, str | None] = {'req_id': None}
    _write(
        project_root / '.ccb' / '.droid-session',
        json.dumps(
            {
                'terminal': 'tmux',
                'pane_id': '%5',
                'work_dir': str(project_root),
                'droid_session_id': 'droid-session-id',
                'droid_session_path': str(tmp_path / 'droid-session.jsonl'),
            },
            ensure_ascii=False,
            indent=2,
        ) + '\n',
    )

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            self.sent = (pane_id, text)
            match = re.search(r'^CCB_REQ_ID:\s*(\S+)\s*$', text, re.MULTILINE)
            anchor['req_id'] = match.group(1) if match else None

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%5'

    class FakeSession:
        data = {}
        droid_session_path = str(tmp_path / 'droid-session.jsonl')
        droid_session_id = 'droid-session-id'
        work_dir = str(project_root)

        def ensure_pane(self):
            return True, '%5'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._calls = 0

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def set_session_id_hint(self, session_id) -> None:
            del session_id

        def capture_state(self):
            return {'session_path': str(tmp_path / 'droid-session.jsonl'), 'offset': 0}

        def try_get_events(self, state):
            events = [
                ('user', f'CCB_REQ_ID: {anchor["req_id"]}\n\nprompt'),
                ('assistant', 'partial before cancel'),
                ('assistant', f'final after cancel\nCCB_DONE: {anchor["req_id"]}'),
            ]
            self._calls += 1
            index = int(state.get('index', 0))
            if index >= len(events):
                return [], state
            if index >= 2 and self._calls < 50:
                return [], state
            return [events[index]], {**state, 'index': index + 1}

    monkeypatch.setattr(droid_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(droid_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(droid_adapter_module, 'DroidLogReader', FakeReader)

    app = CcbdApp(project_root)
    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for_path(app.paths.ccbd_socket_path)

    try:
        code, stdout, stderr = _run_phase2_local([], cwd=project_root, start_app=app)
        assert code == 0, stderr

        code, stdout, stderr = _run_phase2_local(['ask', 'demo', 'from', 'user', 'hello droid cancel'], cwd=project_root)
        assert code == 0, stderr
        job_id = _extract_accepted_job_id(stdout, target='demo')

        running = _wait_for_phase2_status(project_root, 'demo', 'running', timeout=3.0)
        assert f'job_id: {job_id}' in running
        assert 'observer_notice: weak observer surface; non-terminal state may change; use ccb trace <id> for lineage when needed' in running

        code, stdout, stderr = _run_phase2_local(['cancel', job_id], cwd=project_root)
        assert code == 0, stderr
        assert 'cancel_status: ok' in stdout
        assert 'status: cancelled' in stdout

        pend = _wait_for_phase2_status(project_root, job_id, 'cancelled', timeout=5.0)
        assert 'completion_reason: cancel_info' in pend
        assert 'completion_confidence: degraded' in pend
        assert 'final after cancel' not in pend

        code, stdout, stderr = _run_phase2_local(['watch', job_id], cwd=project_root)
        assert code == 0, stderr
        assert 'watch_status: terminal' in stdout
        assert 'status: cancelled' in stdout
    finally:
        app.shutdown()
        thread.join(timeout=2)
        assert not thread.is_alive()



def test_ccb_start_restore_preserves_existing_restore_state(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    _write(project_root / '.ccb' / 'ccb.config', _config_text())
    restore_path = project_root / '.ccb' / 'agents' / 'codex' / 'restore.json'
    restore_path.parent.mkdir(parents=True, exist_ok=True)
    restore_path.write_text(
        json.dumps(
            {
                'schema_version': 2,
                'record_type': 'agent_restore_state',
                'restore_mode': 'auto',
                'last_checkpoint': 'cp-1',
                'conversation_summary': 'keep this summary',
                'open_tasks': ['task-a'],
                'files_touched': ['README.md'],
                'base_commit': None,
                'head_commit': None,
                'last_restore_status': 'checkpoint',
            },
            ensure_ascii=False,
            indent=2,
        ) + '\n',
        encoding='utf-8',
    )

    proc = _run_ccb([], cwd=project_root)
    assert proc.returncode == 0, proc.stderr
    assert 'start_status: ok' in proc.stdout

    doctor = _wait_for_doctor_any_line(
        project_root,
        (
            'agent: name=codex health=restored provider=codex completion=protocol_turn',
            'agent: name=codex health=healthy provider=codex completion=protocol_turn',
        ),
    )

    payload = json.loads(restore_path.read_text(encoding='utf-8'))
    assert payload['conversation_summary'] == 'keep this summary'
    assert payload['last_checkpoint'] == 'cp-1'
    assert payload['last_restore_status'] == 'checkpoint'

    kill = _run_ccb(['kill'], cwd=project_root)
    assert kill.returncode == 0, kill.stderr



def test_ccb_start_prefers_instance_scoped_codex_binding(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-codex-binding'
    _write(project_root / '.ccb' / 'ccb.config', _named_agent_config_text('demo', 'codex'))
    _write(
        project_root / '.ccb' / '.codex-session',
        json.dumps(
            {
                'terminal': 'tmux',
                'pane_id': '%1',
                'work_dir': str(project_root),
                'codex_session_id': 'base-session-id',
                'codex_session_path': str(tmp_path / 'base-session.jsonl'),
            },
            ensure_ascii=False,
            indent=2,
        ) + '\n',
    )
    _write(
        project_root / '.ccb' / '.codex-demo-session',
        json.dumps(
            {
                'terminal': 'tmux',
                'pane_id': '%9',
                'work_dir': str(project_root),
                'codex_session_id': 'demo-session-id',
                'codex_session_path': str(tmp_path / 'demo-session.jsonl'),
            },
            ensure_ascii=False,
            indent=2,
        ) + '\n',
    )

    proc = _run_ccb([], cwd=project_root)
    assert proc.returncode == 0, proc.stderr
    assert 'agents: demo' in proc.stdout

    runtime_path = project_root / '.ccb' / 'agents' / 'demo' / 'runtime.json'
    runtime = json.loads(runtime_path.read_text(encoding='utf-8'))
    assert runtime['runtime_ref'] == 'tmux:%9'
    assert runtime['session_ref'] == 'demo-session-id'
    assert runtime['workspace_path'] == str(project_root)

    kill = _run_ccb(['kill'], cwd=project_root)
    assert kill.returncode == 0, kill.stderr


def test_ccb_start_loads_claude_binding_from_project_anchor(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-claude-binding'
    _write(project_root / '.ccb' / 'ccb.config', _named_agent_config_text('claude', 'claude'))
    _write(
        project_root / '.ccb' / '.claude-claude-session',
        json.dumps(
            {
                'terminal': 'tmux',
                'pane_id': '%2',
                'work_dir': str(project_root),
                'work_dir_norm': str(project_root),
                'claude_session_id': 'claude-session-id',
                'claude_session_path': str(tmp_path / 'claude-session.jsonl'),
            },
            ensure_ascii=False,
            indent=2,
        ) + '\n',
    )

    proc = _run_ccb([], cwd=project_root)
    assert proc.returncode == 0, proc.stderr
    assert 'agents: claude' in proc.stdout

    runtime_path = project_root / '.ccb' / 'agents' / 'claude' / 'runtime.json'
    runtime = json.loads(runtime_path.read_text(encoding='utf-8'))
    assert runtime['runtime_ref'] == 'tmux:%2'
    assert runtime['session_ref'] == 'claude-session-id'
    assert runtime['workspace_path'] == str(project_root)

    kill = _run_ccb(['kill'], cwd=project_root)
    assert kill.returncode == 0, kill.stderr


def test_ccb_start_restore_keeps_bound_runtime_refs(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-restore-binding'
    _write(project_root / '.ccb' / 'ccb.config', _named_agent_config_text('demo', 'codex'))
    _write(
        project_root / '.ccb' / '.codex-demo-session',
        json.dumps(
            {
                'terminal': 'tmux',
                'pane_id': '%9',
                'work_dir': str(project_root),
                'codex_session_id': 'demo-session-id',
                'codex_session_path': str(tmp_path / 'demo-session.jsonl'),
            },
            ensure_ascii=False,
            indent=2,
        ) + '\n',
    )

    proc = _run_ccb([], cwd=project_root)
    assert proc.returncode == 0, proc.stderr

    restore = _run_ccb([], cwd=project_root)
    assert restore.returncode == 0, restore.stderr

    runtime_path = project_root / '.ccb' / 'agents' / 'demo' / 'runtime.json'
    runtime = json.loads(runtime_path.read_text(encoding='utf-8'))
    assert runtime['runtime_ref'] == 'tmux:%9'
    assert runtime['session_ref'] == 'demo-session-id'

    doctor = _run_ccb(['doctor'], cwd=project_root)
    assert doctor.returncode == 0, doctor.stderr
    assert 'binding: status=bound runtime=tmux:%9 session=demo-session-id' in doctor.stdout

    kill = _run_ccb(['kill'], cwd=project_root)
    assert kill.returncode == 0, kill.stderr


def test_ccb_start_gemini_binding_does_not_fall_back_to_default_session(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-gemini-binding'
    _write(project_root / '.ccb' / 'ccb.config', _named_agent_config_text('demo', 'gemini'))
    _write(
        project_root / '.ccb' / '.gemini-session',
        json.dumps(
            {
                'terminal': 'tmux',
                'pane_id': '%7',
                'work_dir': str(project_root),
                'gemini_session_id': 'gemini-default-id',
                'gemini_session_path': str(tmp_path / 'gemini-session.jsonl'),
            },
            ensure_ascii=False,
            indent=2,
        ) + '\n',
    )

    proc = _run_ccb([], cwd=project_root)
    assert proc.returncode == 0, proc.stderr
    assert 'agents: demo' in proc.stdout

    runtime_path = project_root / '.ccb' / 'agents' / 'demo' / 'runtime.json'
    runtime = json.loads(runtime_path.read_text(encoding='utf-8'))
    assert runtime['runtime_ref'] != 'tmux:%7'
    assert runtime['session_ref'] != 'gemini-default-id'
    assert runtime['workspace_path'] == str(project_root)

    kill = _run_ccb(['kill'], cwd=project_root)
    assert kill.returncode == 0, kill.stderr


def test_ccb_fake_provider_auto_completes(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-fake'
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('fake'))

    start = _run_ccb([], cwd=project_root)
    assert start.returncode == 0, start.stderr
    assert 'agents: demo' in start.stdout

    ask = _run_ccb(['ask', 'demo', 'from', 'user', 'auto complete'], cwd=project_root)
    assert ask.returncode == 0, ask.stderr
    job_id = _extract_accepted_job_id(ask.stdout, target='demo')

    completed = _wait_for_status(project_root, job_id, 'completed', timeout=5.0)
    assert 'reply: FAKE[demo] auto complete' in completed.stdout
    assert 'completion_reason: result_message' in completed.stdout
    assert 'completion_confidence: exact' in completed.stdout

    watch = _run_ccb(['watch', job_id], cwd=project_root)
    assert watch.returncode == 0, watch.stderr
    assert 'watch_status: terminal' in watch.stdout
    assert 'status: completed' in watch.stdout
    assert 'reply: FAKE[demo] auto complete' in watch.stdout
    assert 'completion_item' in watch.stdout
    assert 'completion_state_updated' in watch.stdout

    kill = _run_ccb(['kill'], cwd=project_root)
    assert kill.returncode == 0, kill.stderr



def test_ccb_fake_codex_provider_blackbox_watch_chain(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-fake-codex'
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('fake-codex'))

    start = _run_ccb([], cwd=project_root)
    assert start.returncode == 0, start.stderr
    assert 'agents: demo' in start.stdout

    doctor = _run_ccb(['doctor'], cwd=project_root)
    assert doctor.returncode == 0, doctor.stderr
    assert 'agent: name=demo health=restored provider=fake-codex completion=protocol_turn' in doctor.stdout

    ask = _run_ccb(
        [
            'ask',
            '--task-id',
            'fake;latency_ms=400',
            'demo',
            'from',
            'user',
            'protocol flow',
        ],
        cwd=project_root,
    )
    assert ask.returncode == 0, ask.stderr
    job_id = _extract_accepted_job_id(ask.stdout, target='demo')

    observed = _wait_for_any_status(project_root, 'demo', ('running', 'completed'), timeout=3.0)
    assert f'job_id: {job_id}' in observed.stdout

    watch = _run_ccb(['watch', 'demo'], cwd=project_root)
    assert watch.returncode == 0, watch.stderr
    assert 'watch_status: terminal' in watch.stdout
    assert f'job_id: {job_id}' in watch.stdout
    assert 'agent_name: demo' in watch.stdout
    assert 'status: completed' in watch.stdout
    assert 'reply: FAKE[demo] protocol flow' in watch.stdout
    assert 'completion_item' in watch.stdout
    assert 'completion_terminal' in watch.stdout
    assert 'job_completed' in watch.stdout

    completed = _wait_for_status(project_root, job_id, 'completed', timeout=3.0)
    assert 'reply: FAKE[demo] protocol flow' in completed.stdout
    assert 'completion_reason: task_complete' in completed.stdout
    assert 'completion_confidence: exact' in completed.stdout

    kill = _run_ccb(['kill'], cwd=project_root)
    assert kill.returncode == 0, kill.stderr


@pytest.mark.provider_blackbox
def test_ccb_codex_real_adapter_blackbox_watch_chain_without_done_marker(monkeypatch, tmp_path: Path) -> None:
    from provider_execution import codex as codex_adapter_module

    fixed_req_id = 'job_c0de11'
    sent: list[tuple[str, str]] = []
    project_root = tmp_path / 'repo-codex-real'
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('codex'))
    _write(
        project_root / '.ccb' / '.codex-session',
        json.dumps(
            {
                'terminal': 'tmux',
                'pane_id': '%1',
                'work_dir': str(project_root),
                'codex_session_id': 'codex-session-id',
                'codex_session_path': str(tmp_path / 'codex-session.jsonl'),
            },
            ensure_ascii=False,
            indent=2,
        ) + '\n',
    )

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            sent.append((pane_id, text))

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%1'

    class FakeSession:
        data = {}
        codex_session_path = str(tmp_path / 'codex-session.jsonl')
        codex_session_id = 'codex-session-id'
        work_dir = str(project_root)

        def ensure_pane(self):
            return True, '%1'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._events = [
                {
                    'role': 'user',
                    'text': f'CCB_REQ_ID: {fixed_req_id}\n\nprompt',
                    'entry_type': 'response_item',
                    'payload_type': 'message',
                    'timestamp': '2026-03-18T00:00:00Z',
                },
                {
                    'role': 'assistant',
                    'text': 'partial',
                    'entry_type': 'event_msg',
                    'payload_type': 'agent_message',
                    'timestamp': '2026-03-18T00:00:01Z',
                },
                {
                    'role': 'assistant',
                    'text': 'final without done',
                    'entry_type': 'event_msg',
                    'payload_type': 'agent_message',
                    'phase': 'final_answer',
                    'timestamp': '2026-03-18T00:00:02Z',
                },
                {
                    'role': 'assistant',
                    'text': 'final without done',
                    'entry_type': 'response_item',
                    'payload_type': 'message',
                    'phase': 'final_answer',
                    'timestamp': '2026-03-18T00:00:02Z',
                },
                {
                    'role': 'system',
                    'text': 'partial\nfinal without done',
                    'entry_type': 'event_msg',
                    'payload_type': 'task_complete',
                    'turn_id': 'turn-codex-phase2',
                    'last_agent_message': 'partial\nfinal without done',
                    'timestamp': '2026-03-18T00:00:03Z',
                },
            ]

        def capture_state(self):
            return {'index': 0, 'log_path': str(tmp_path / 'codex-session.jsonl')}

        def try_get_entries(self, state):
            index = int(state.get('index', 0))
            if index >= len(self._events):
                return [], state
            return [self._events[index]], {**state, 'index': index + 1}

    monkeypatch.setattr(codex_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(codex_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(codex_adapter_module, 'CodexLogReader', FakeReader)
    app = CcbdApp(project_root)
    _freeze_job_ids(app, monkeypatch, fixed_req_id)
    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for_path(app.paths.ccbd_socket_path)

    try:
        code, stdout, stderr = _run_phase2_local([], cwd=project_root, start_app=app)
        assert code == 0, stderr
        assert 'start_status: ok' in stdout

        code, stdout, stderr = _run_phase2_local(['doctor'], cwd=project_root)
        assert code == 0, stderr
        assert 'agent: name=demo health=restored provider=codex completion=protocol_turn' in stdout

        code, stdout, stderr = _run_phase2_local(['ask', 'demo', 'from', 'user', 'hello codex'], cwd=project_root)
        assert code == 0, stderr
        job_id = _extract_accepted_job_id(stdout, target='demo')

        pend = _wait_for_phase2_status(project_root, 'demo', 'completed')
        assert f'job_id: {job_id}' in pend
        assert 'reply: partial\nfinal without done' in pend
        assert 'completion_reason: task_complete' in pend
        assert 'completion_confidence: exact' in pend
        assert sent and sent[0][0] == '%1'
        assert fixed_req_id in sent[0][1]
        assert 'CCB_DONE:' not in sent[0][1]

        code, stdout, stderr = _run_phase2_local(['watch', job_id], cwd=project_root)
        assert code == 0, stderr
        assert 'watch_status: terminal' in stdout
        assert f'job_id: {job_id}' in stdout
        assert 'status: completed' in stdout

        code, stdout, stderr = _run_phase2_local(['watch', 'demo'], cwd=project_root)
        assert code == 0, stderr
        assert 'watch_status: terminal' in stdout
        assert f'job_id: {job_id}' in stdout
    finally:
        app.shutdown()
        thread.join(timeout=2)
        assert not thread.is_alive()


@pytest.mark.provider_blackbox
def test_ccb_codex_real_adapter_recovers_after_ccbd_restart(monkeypatch, tmp_path: Path) -> None:
    from provider_execution import codex as codex_adapter_module

    fixed_req_id = 'job_c0de12'
    project_root = tmp_path / 'repo-codex-resume'
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('codex'))
    _write(
        project_root / '.ccb' / '.codex-session',
        json.dumps(
            {
                'terminal': 'tmux',
                'pane_id': '%1',
                'work_dir': str(project_root),
                'codex_session_id': 'codex-session-id',
                'codex_session_path': str(tmp_path / 'codex-session.jsonl'),
            },
            ensure_ascii=False,
            indent=2,
        ) + '\n',
    )

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            self.sent = (pane_id, text)

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%1'

    class FakeSession:
        data = {}
        codex_session_path = str(tmp_path / 'codex-session.jsonl')
        codex_session_id = 'codex-session-id'
        work_dir = str(project_root)

        def ensure_pane(self):
            return True, '%1'

    class FakeReader:
        instances = 0

        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            FakeReader.instances += 1
            self.instance_id = FakeReader.instances
            self._events = [
                {
                    'role': 'user',
                    'text': f'CCB_REQ_ID: {fixed_req_id}\n\nprompt',
                    'entry_type': 'response_item',
                    'payload_type': 'message',
                    'timestamp': '2026-03-18T00:00:00Z',
                },
                {
                    'role': 'assistant',
                    'text': 'partial before restart',
                    'entry_type': 'event_msg',
                    'payload_type': 'agent_message',
                    'timestamp': '2026-03-18T00:00:01Z',
                },
                {
                    'role': 'system',
                    'text': 'partial before restart',
                    'entry_type': 'event_msg',
                    'payload_type': 'task_complete',
                    'turn_id': 'turn-codex-resume',
                    'last_agent_message': 'partial before restart',
                    'timestamp': '2026-03-18T00:00:02Z',
                },
            ]

        def capture_state(self):
            return {'index': 0, 'log_path': str(tmp_path / 'codex-session.jsonl')}

        def try_get_entries(self, state):
            index = int(state.get('index', 0))
            limit = 2 if self.instance_id == 1 else len(self._events)
            if index >= limit:
                return [], state
            return [self._events[index]], {**state, 'index': index + 1}

    monkeypatch.setattr(codex_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(codex_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(codex_adapter_module, 'CodexLogReader', FakeReader)
    app1 = CcbdApp(project_root)
    _freeze_job_ids(app1, monkeypatch, fixed_req_id)
    thread1 = threading.Thread(target=app1.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread1.start()
    _wait_for_path(app1.paths.ccbd_socket_path)

    try:
        code, stdout, stderr = _run_phase2_local([], cwd=project_root, start_app=app1)
        assert code == 0, stderr

        code, stdout, stderr = _run_phase2_local(['ask', 'demo', 'from', 'user', 'resume codex'], cwd=project_root)
        assert code == 0, stderr
        job_id = _extract_accepted_job_id(stdout, target='demo')

        running = _wait_for_phase2_status(project_root, 'demo', 'running')
        assert f'job_id: {job_id}' in running

        deadline = time.time() + 2.0
        while time.time() < deadline:
            events_path = project_root / '.ccb' / 'agents' / 'demo' / 'events.jsonl'
            if events_path.exists() and 'assistant_chunk' in events_path.read_text(encoding='utf-8'):
                break
            time.sleep(0.05)
        else:
            raise AssertionError('expected assistant_chunk before restart')

        app1.request_shutdown()
        thread1.join(timeout=2)
        assert not thread1.is_alive()

        app2 = CcbdApp(project_root)
        thread2 = threading.Thread(target=app2.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        thread2.start()
        _wait_for_path(app2.paths.ccbd_socket_path)
        try:
            pend = _wait_for_phase2_status(project_root, job_id, 'completed')
            assert 'reply: partial before restart' in pend
            assert 'completion_reason: task_complete' in pend
        finally:
            app2.request_shutdown()
            thread2.join(timeout=2)
            assert not thread2.is_alive()
    finally:
        if thread1.is_alive():
            app1.request_shutdown()
            thread1.join(timeout=2)
            assert not thread1.is_alive()


def test_ccb_two_named_codex_agents_concurrent_ask_isolated(monkeypatch, tmp_path: Path) -> None:
    from jobs.store import JobEventStore, JobStore
    from provider_execution import codex as codex_adapter_module
    from storage.paths import PathLayout

    project_root = tmp_path / 'repo-dual-codex'
    _write(project_root / '.ccb' / 'ccb.config', _dual_named_agent_config_text('agent1', 'codex', 'agent2', 'codex'))
    _write(
        project_root / '.ccb' / '.codex-agent1-session',
        json.dumps(
            {
                'terminal': 'tmux',
                'pane_id': '%11',
                'work_dir': str(project_root / '.ccb' / 'workspaces' / 'agent1'),
                'work_dir_norm': str(project_root / '.ccb' / 'workspaces' / 'agent1'),
                'codex_session_id': 'agent1-session-id',
                'codex_session_path': str(tmp_path / 'agent1-session.jsonl'),
            },
            ensure_ascii=False,
            indent=2,
        ) + '\n',
    )
    _write(
        project_root / '.ccb' / '.codex-agent2-session',
        json.dumps(
            {
                'terminal': 'tmux',
                'pane_id': '%22',
                'work_dir': str(project_root / '.ccb' / 'workspaces' / 'agent2'),
                'work_dir_norm': str(project_root / '.ccb' / 'workspaces' / 'agent2'),
                'codex_session_id': 'agent2-session-id',
                'codex_session_path': str(tmp_path / 'agent2-session.jsonl'),
            },
            ensure_ascii=False,
            indent=2,
        ) + '\n',
    )

    request_ids = ('job_a11ce1', 'job_b0b001')
    sent: list[tuple[str, str]] = []

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            sent.append((pane_id, text))

        def is_alive(self, pane_id: str) -> bool:
            return pane_id in {'%11', '%22'}

    class FakeSession:
        def __init__(self, *, pane_id: str, session_id: str, log_path: Path, work_dir: Path) -> None:
            self.data = {'pane_id': pane_id}
            self.codex_session_path = str(log_path)
            self.codex_session_id = session_id
            self.work_dir = str(work_dir)

        def ensure_pane(self):
            return True, self.data['pane_id']

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            self.session_id = str(kwargs.get('session_id_filter') or '')
            self.log_path = str(kwargs.get('log_path') or '')
            if self.session_id == 'agent1-session-id':
                self._events = [
                    {
                        'role': 'user',
                        'text': f'CCB_REQ_ID: {request_ids[0]}\n\nprompt',
                        'entry_type': 'response_item',
                        'payload_type': 'message',
                        'timestamp': '2026-03-18T00:00:00Z',
                    },
                    {
                        'role': 'assistant',
                        'text': 'agent1 partial',
                        'entry_type': 'event_msg',
                        'payload_type': 'agent_message',
                        'timestamp': '2026-03-18T00:00:01Z',
                    },
                    {
                        'role': 'system',
                        'text': 'agent1 partial\nagent1 final',
                        'entry_type': 'event_msg',
                        'payload_type': 'task_complete',
                        'turn_id': 'turn-agent1',
                        'last_agent_message': 'agent1 partial\nagent1 final',
                        'timestamp': '2026-03-18T00:00:02Z',
                    },
                ]
            elif self.session_id == 'agent2-session-id':
                self._events = [
                    {
                        'role': 'user',
                        'text': f'CCB_REQ_ID: {request_ids[1]}\n\nprompt',
                        'entry_type': 'response_item',
                        'payload_type': 'message',
                        'timestamp': '2026-03-18T00:00:00Z',
                    },
                    {
                        'role': 'assistant',
                        'text': 'agent2 partial',
                        'entry_type': 'event_msg',
                        'payload_type': 'agent_message',
                        'timestamp': '2026-03-18T00:00:01Z',
                    },
                    {
                        'role': 'system',
                        'text': 'agent2 partial\nagent2 final',
                        'entry_type': 'event_msg',
                        'payload_type': 'task_complete',
                        'turn_id': 'turn-agent2',
                        'last_agent_message': 'agent2 partial\nagent2 final',
                        'timestamp': '2026-03-18T00:00:02Z',
                    },
                ]
            else:
                raise AssertionError(f'unexpected session: {self.session_id} {self.log_path}')

        def capture_state(self):
            return {'index': 0, 'log_path': self.log_path}

        def try_get_entries(self, state):
            index = int(state.get('index', 0))
            if index >= len(self._events):
                return [], state
            return [self._events[index]], {**state, 'index': index + 1}

    def _load_fake_session(work_dir, instance=None):
        work_dir = Path(work_dir)
        if instance is None:
            return None
        if instance == 'agent1':
            return FakeSession(
                pane_id='%11',
                session_id='agent1-session-id',
                log_path=tmp_path / 'agent1-session.jsonl',
                work_dir=work_dir,
            )
        if instance == 'agent2':
            return FakeSession(
                pane_id='%22',
                session_id='agent2-session-id',
                log_path=tmp_path / 'agent2-session.jsonl',
                work_dir=work_dir,
            )
        raise AssertionError(f'unexpected instance: {instance}')

    monkeypatch.setattr(codex_adapter_module, 'load_project_session', _load_fake_session)
    monkeypatch.setattr(codex_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(codex_adapter_module, 'CodexLogReader', FakeReader)
    app = CcbdApp(project_root)
    _freeze_job_ids(app, monkeypatch, *request_ids)
    monkeypatch.setattr(app.health_monitor, 'check_all', lambda: {})
    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for_path(app.paths.ccbd_socket_path)

    try:
        code, stdout, stderr = _run_phase2_local([], cwd=project_root, start_app=app)
        assert code == 0, stderr
        assert 'agents: agent1, agent2' in stdout

        runtime_agent1 = json.loads((project_root / '.ccb' / 'agents' / 'agent1' / 'runtime.json').read_text(encoding='utf-8'))
        runtime_agent2 = json.loads((project_root / '.ccb' / 'agents' / 'agent2' / 'runtime.json').read_text(encoding='utf-8'))
        assert runtime_agent1['session_ref'] == 'agent1-session-id'
        assert runtime_agent2['session_ref'] == 'agent2-session-id'
        assert runtime_agent1['workspace_path'] == str(project_root)
        assert runtime_agent2['workspace_path'] == str(project_root)

        code, stdout1, stderr = _run_phase2_local(['ask', 'agent1', 'from', 'user', 'hello agent1'], cwd=project_root)
        assert code == 0, stderr
        job1 = _extract_accepted_job_id(stdout1, target='agent1')

        code, stdout2, stderr = _run_phase2_local(['ask', 'agent2', 'from', 'user', 'hello agent2'], cwd=project_root)
        assert code == 0, stderr
        job2 = _extract_accepted_job_id(stdout2, target='agent2')

        pend1 = _wait_for_phase2_status(project_root, 'agent1', 'completed', timeout=DUAL_PROVIDER_STATUS_TIMEOUT)
        pend2 = _wait_for_phase2_status(project_root, 'agent2', 'completed', timeout=DUAL_PROVIDER_STATUS_TIMEOUT)
        assert f'job_id: {job1}' in pend1
        assert f'job_id: {job2}' in pend2
        assert 'reply: agent1 partial\nagent1 final' in pend1
        assert 'reply: agent2 partial\nagent2 final' in pend2
        assert 'agent2 final' not in pend1
        assert 'agent1 final' not in pend2

        code, watch1, stderr = _run_phase2_local(['watch', job1], cwd=project_root)
        assert code == 0, stderr
        code, watch2, stderr = _run_phase2_local(['watch', job2], cwd=project_root)
        assert code == 0, stderr
        assert 'agent_name: agent1' in watch1
        assert 'agent_name: agent2' in watch2
        assert f'job_id: {job1}' in watch1
        assert f'job_id: {job2}' in watch2
        assert 'agent2 final' not in watch1
        assert 'agent1 final' not in watch2

        layout = PathLayout(project_root)
        event_store = JobEventStore(layout)
        line1, events1 = event_store.read_since('agent1', 0)
        line2, events2 = event_store.read_since('agent2', 0)
        assert line1 > 0 and line2 > 0
        assert all(event.agent_name == 'agent1' for event in events1)
        assert all(event.agent_name == 'agent2' for event in events2)
        assert any(event.job_id == job1 for event in events1)
        assert any(event.job_id == job2 for event in events2)
        assert not any(event.job_id == job2 for event in events1)
        assert not any(event.job_id == job1 for event in events2)

        job_store = JobStore(layout)
        latest1 = job_store.get_latest('agent1', job1)
        latest2 = job_store.get_latest('agent2', job2)
        assert latest1 is not None and latest1.status.value == 'completed'
        assert latest2 is not None and latest2.status.value == 'completed'

        assert any(pane_id == '%11' and request_ids[0] in text for pane_id, text in sent)
        assert any(pane_id == '%22' and request_ids[1] in text for pane_id, text in sent)
        assert not any('CCB_DONE:' in text for _, text in sent)
    finally:
        _assert_phase2_app_shutdown_clean(project_root, app, thread)


def test_ccb_two_named_codex_agents_recover_after_ccbd_restart(monkeypatch, tmp_path: Path) -> None:
    from provider_execution import codex as codex_adapter_module

    project_root = tmp_path / 'repo-dual-codex-resume'
    _write(project_root / '.ccb' / 'ccb.config', _dual_named_agent_config_text('agent1', 'codex', 'agent2', 'codex'))
    _write(
        project_root / '.ccb' / '.codex-agent1-session',
        json.dumps(
            {
                'terminal': 'tmux',
                'pane_id': '%11',
                'work_dir': str(project_root / '.ccb' / 'workspaces' / 'agent1'),
                'work_dir_norm': str(project_root / '.ccb' / 'workspaces' / 'agent1'),
                'codex_session_id': 'agent1-session-id',
                'codex_session_path': str(tmp_path / 'agent1-session.jsonl'),
            },
            ensure_ascii=False,
            indent=2,
        ) + '\n',
    )
    _write(
        project_root / '.ccb' / '.codex-agent2-session',
        json.dumps(
            {
                'terminal': 'tmux',
                'pane_id': '%22',
                'work_dir': str(project_root / '.ccb' / 'workspaces' / 'agent2'),
                'work_dir_norm': str(project_root / '.ccb' / 'workspaces' / 'agent2'),
                'codex_session_id': 'agent2-session-id',
                'codex_session_path': str(tmp_path / 'agent2-session.jsonl'),
            },
            ensure_ascii=False,
            indent=2,
        ) + '\n',
    )

    request_ids = ('job_a11ce2', 'job_b0b002')
    reader_instances = {'agent1-session-id': 0, 'agent2-session-id': 0}
    sent: list[tuple[str, str]] = []

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            sent.append((pane_id, text))

        def is_alive(self, pane_id: str) -> bool:
            return pane_id in {'%11', '%22'}

    class FakeSession:
        def __init__(self, *, pane_id: str, session_id: str, log_path: Path, work_dir: Path) -> None:
            self.data = {'pane_id': pane_id}
            self.codex_session_path = str(log_path)
            self.codex_session_id = session_id
            self.work_dir = str(work_dir)

        def ensure_pane(self):
            return True, self.data['pane_id']

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            self.session_id = str(kwargs.get('session_id_filter') or '')
            self.log_path = str(kwargs.get('log_path') or '')
            reader_instances[self.session_id] = reader_instances.get(self.session_id, 0) + 1
            if self.session_id == 'agent1-session-id':
                self._events = [
                    {
                        'role': 'user',
                        'text': f'CCB_REQ_ID: {request_ids[0]}\n\nprompt',
                        'entry_type': 'response_item',
                        'payload_type': 'message',
                        'timestamp': '2026-03-18T00:00:00Z',
                    },
                    {
                        'role': 'assistant',
                        'text': 'agent1 before restart',
                        'entry_type': 'event_msg',
                        'payload_type': 'agent_message',
                        'timestamp': '2026-03-18T00:00:01Z',
                    },
                    {
                        'role': 'system',
                        'text': 'agent1 before restart\nagent1 after restart',
                        'entry_type': 'event_msg',
                        'payload_type': 'task_complete',
                        'turn_id': 'turn-agent1-restart',
                        'last_agent_message': 'agent1 before restart\nagent1 after restart',
                        'timestamp': '2026-03-18T00:00:02Z',
                    },
                ]
            elif self.session_id == 'agent2-session-id':
                self._events = [
                    {
                        'role': 'user',
                        'text': f'CCB_REQ_ID: {request_ids[1]}\n\nprompt',
                        'entry_type': 'response_item',
                        'payload_type': 'message',
                        'timestamp': '2026-03-18T00:00:00Z',
                    },
                    {
                        'role': 'assistant',
                        'text': 'agent2 before restart',
                        'entry_type': 'event_msg',
                        'payload_type': 'agent_message',
                        'timestamp': '2026-03-18T00:00:01Z',
                    },
                    {
                        'role': 'system',
                        'text': 'agent2 before restart\nagent2 after restart',
                        'entry_type': 'event_msg',
                        'payload_type': 'task_complete',
                        'turn_id': 'turn-agent2-restart',
                        'last_agent_message': 'agent2 before restart\nagent2 after restart',
                        'timestamp': '2026-03-18T00:00:02Z',
                    },
                ]
            else:
                raise AssertionError(f'unexpected session: {self.session_id} {self.log_path}')

        def capture_state(self):
            return {'index': 0, 'log_path': self.log_path}

        def try_get_entries(self, state):
            index = int(state.get('index', 0))
            limit = 2 if reader_instances[self.session_id] == 1 else len(self._events)
            if index >= limit:
                return [], state
            return [self._events[index]], {**state, 'index': index + 1}

    def _load_fake_session(work_dir, instance=None):
        work_dir = Path(work_dir)
        if instance is None:
            return None
        if instance == 'agent1':
            return FakeSession(
                pane_id='%11',
                session_id='agent1-session-id',
                log_path=tmp_path / 'agent1-session.jsonl',
                work_dir=work_dir,
            )
        if instance == 'agent2':
            return FakeSession(
                pane_id='%22',
                session_id='agent2-session-id',
                log_path=tmp_path / 'agent2-session.jsonl',
                work_dir=work_dir,
            )
        raise AssertionError(f'unexpected instance: {instance}')

    monkeypatch.setattr(codex_adapter_module, 'load_project_session', _load_fake_session)
    monkeypatch.setattr(codex_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(codex_adapter_module, 'CodexLogReader', FakeReader)
    app1 = CcbdApp(project_root)
    _freeze_job_ids(app1, monkeypatch, *request_ids)
    thread1 = threading.Thread(target=app1.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread1.start()
    _wait_for_path(app1.paths.ccbd_socket_path)

    try:
        code, stdout, stderr = _run_phase2_local([], cwd=project_root, start_app=app1)
        assert code == 0, stderr
        assert 'agents: agent1, agent2' in stdout

        code, stdout, stderr = _run_phase2_local(['ask', 'agent1', 'from', 'user', 'restart agent1'], cwd=project_root)
        assert code == 0, stderr
        job1 = _extract_accepted_job_id(stdout, target='agent1')

        code, stdout, stderr = _run_phase2_local(['ask', 'agent2', 'from', 'user', 'restart agent2'], cwd=project_root)
        assert code == 0, stderr
        job2 = _extract_accepted_job_id(stdout, target='agent2')

        running1 = _wait_for_phase2_status(project_root, 'agent1', 'running')
        running2 = _wait_for_phase2_status(project_root, 'agent2', 'running')
        assert f'job_id: {job1}' in running1
        assert f'job_id: {job2}' in running2

        deadline = time.time() + 2.0
        while time.time() < deadline:
            agent1_events = project_root / '.ccb' / 'agents' / 'agent1' / 'events.jsonl'
            agent2_events = project_root / '.ccb' / 'agents' / 'agent2' / 'events.jsonl'
            if (
                agent1_events.exists()
                and agent2_events.exists()
                and 'assistant_chunk' in agent1_events.read_text(encoding='utf-8')
                and 'assistant_chunk' in agent2_events.read_text(encoding='utf-8')
            ):
                break
            time.sleep(0.05)
        else:
            raise AssertionError('expected assistant_chunk for both agents before restart')

        _assert_phase2_app_shutdown_clean(project_root, app1, thread1)

        app2 = CcbdApp(project_root)
        thread2 = threading.Thread(target=app2.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        thread2.start()
        _wait_for_path(app2.paths.ccbd_socket_path)
        try:
            pend1 = _wait_for_phase2_status(project_root, 'agent1', 'completed', timeout=DUAL_PROVIDER_STATUS_TIMEOUT)
            pend2 = _wait_for_phase2_status(project_root, 'agent2', 'completed', timeout=DUAL_PROVIDER_STATUS_TIMEOUT)
            assert f'job_id: {job1}' in pend1
            assert f'job_id: {job2}' in pend2
            assert 'reply: agent1 before restart\nagent1 after restart' in pend1
            assert 'reply: agent2 before restart\nagent2 after restart' in pend2
            assert 'agent2 after restart' not in pend1
            assert 'agent1 after restart' not in pend2
            assert reader_instances['agent1-session-id'] >= 2
            assert reader_instances['agent2-session-id'] >= 2
            assert any(pane_id == '%11' and request_ids[0] in text for pane_id, text in sent)
            assert any(pane_id == '%22' and request_ids[1] in text for pane_id, text in sent)
        finally:
            _assert_phase2_app_shutdown_clean(project_root, app2, thread2)
    finally:
        if thread1.is_alive():
            _assert_phase2_app_shutdown_clean(project_root, app1, thread1)


def test_ccb_two_named_claude_agents_concurrent_ask_isolated(monkeypatch, tmp_path: Path) -> None:
    from jobs.store import JobEventStore, JobStore
    from provider_execution import claude as claude_adapter_module
    from storage.paths import PathLayout

    project_root = tmp_path / 'repo-dual-claude'
    _write(project_root / '.ccb' / 'ccb.config', _dual_named_agent_config_text('agent1', 'claude', 'agent2', 'claude'))
    _write(
        project_root / '.ccb' / '.claude-agent1-session',
        json.dumps(
            {
                'terminal': 'tmux',
                'pane_id': '%31',
                'work_dir': str(project_root / '.ccb' / 'workspaces' / 'agent1'),
                'claude_session_id': 'claude-agent1-session-id',
                'claude_session_path': str(tmp_path / 'claude-agent1.jsonl'),
            },
            ensure_ascii=False,
            indent=2,
        ) + '\n',
    )
    _write(
        project_root / '.ccb' / '.claude-agent2-session',
        json.dumps(
            {
                'terminal': 'tmux',
                'pane_id': '%32',
                'work_dir': str(project_root / '.ccb' / 'workspaces' / 'agent2'),
                'claude_session_id': 'claude-agent2-session-id',
                'claude_session_path': str(tmp_path / 'claude-agent2.jsonl'),
            },
            ensure_ascii=False,
            indent=2,
        ) + '\n',
    )

    request_ids = ('job_ca1de6', 'job_ca1de7')
    sent: list[tuple[str, str]] = []

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            sent.append((pane_id, text))

        def is_alive(self, pane_id: str) -> bool:
            return pane_id in {'%31', '%32'}

    class FakeSession:
        def __init__(self, *, pane_id: str, session_id: str, session_path: Path, work_dir: Path) -> None:
            self.data = {'pane_id': pane_id}
            self.claude_session_id = session_id
            self.claude_session_path = str(session_path)
            self.claude_projects_root = None
            self.work_dir = str(work_dir)

        def ensure_pane(self):
            return True, self.data['pane_id']

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            self.agent_name = ''
            self._events = []

        def set_preferred_session(self, session_path) -> None:
            session_name = Path(session_path).name
            if 'agent1' in session_name:
                self.agent_name = 'agent1'
                self._events = [
                    {'role': 'user', 'text': f'CCB_REQ_ID: {request_ids[0]}\n\nprompt', 'entry_type': 'user'},
                    {'role': 'assistant', 'text': 'claude agent1 partial', 'entry_type': 'assistant', 'uuid': 'claude-agent1-uuid'},
                    {
                        'role': 'system',
                        'text': '',
                        'entry_type': 'system',
                        'subtype': 'turn_duration',
                        'parent_uuid': 'claude-agent1-uuid',
                    },
                ]
                return
            if 'agent2' in session_name:
                self.agent_name = 'agent2'
                self._events = [
                    {'role': 'user', 'text': f'CCB_REQ_ID: {request_ids[1]}\n\nprompt', 'entry_type': 'user'},
                    {'role': 'assistant', 'text': 'claude agent2 partial', 'entry_type': 'assistant', 'uuid': 'claude-agent2-uuid'},
                    {
                        'role': 'system',
                        'text': '',
                        'entry_type': 'system',
                        'subtype': 'turn_duration',
                        'parent_uuid': 'claude-agent2-uuid',
                    },
                ]
                return
            raise AssertionError(f'unexpected claude session path: {session_path}')

        def capture_state(self):
            return {'index': 0, 'session_path': f'{self.agent_name}.jsonl'}

        def try_get_entries(self, state):
            index = int(state.get('index', 0))
            if index >= len(self._events):
                return [], state
            return [self._events[index]], {**state, 'index': index + 1}

    def _load_fake_session(work_dir, instance=None):
        work_dir = Path(work_dir)
        if instance is None:
            return None
        if instance == 'agent1':
            return FakeSession(
                pane_id='%31',
                session_id='claude-agent1-session-id',
                session_path=tmp_path / 'claude-agent1.jsonl',
                work_dir=work_dir,
            )
        if instance == 'agent2':
            return FakeSession(
                pane_id='%32',
                session_id='claude-agent2-session-id',
                session_path=tmp_path / 'claude-agent2.jsonl',
                work_dir=work_dir,
            )
        raise AssertionError(f'unexpected instance: {instance}')

    monkeypatch.setattr(claude_adapter_module, 'load_project_session', _load_fake_session)
    monkeypatch.setattr(claude_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(claude_adapter_module, 'ClaudeLogReader', FakeReader)
    app = CcbdApp(project_root)
    _freeze_job_ids(app, monkeypatch, *request_ids)
    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for_path(app.paths.ccbd_socket_path)

    try:
        code, stdout, stderr = _run_phase2_local([], cwd=project_root, start_app=app)
        assert code == 0, stderr
        assert 'agents: agent1, agent2' in stdout

        runtime_agent1 = json.loads((project_root / '.ccb' / 'agents' / 'agent1' / 'runtime.json').read_text(encoding='utf-8'))
        runtime_agent2 = json.loads((project_root / '.ccb' / 'agents' / 'agent2' / 'runtime.json').read_text(encoding='utf-8'))
        assert runtime_agent1['agent_name'] == 'agent1'
        assert runtime_agent2['agent_name'] == 'agent2'
        assert runtime_agent1['runtime_ref'] != runtime_agent2['runtime_ref']
        assert runtime_agent1['workspace_path'] == str(project_root)
        assert runtime_agent2['workspace_path'] == str(project_root)

        code, stdout1, stderr = _run_phase2_local(['ask', 'agent1', 'from', 'user', 'hello claude agent1'], cwd=project_root)
        assert code == 0, stderr
        job1 = _extract_accepted_job_id(stdout1, target='agent1')

        code, stdout2, stderr = _run_phase2_local(['ask', 'agent2', 'from', 'user', 'hello claude agent2'], cwd=project_root)
        assert code == 0, stderr
        job2 = _extract_accepted_job_id(stdout2, target='agent2')

        pend1 = _wait_for_phase2_status(project_root, 'agent1', 'completed', timeout=DUAL_PROVIDER_STATUS_TIMEOUT)
        pend2 = _wait_for_phase2_status(project_root, 'agent2', 'completed', timeout=DUAL_PROVIDER_STATUS_TIMEOUT)
        assert f'job_id: {job1}' in pend1
        assert f'job_id: {job2}' in pend2
        assert 'reply: claude agent1 partial' in pend1
        assert 'reply: claude agent2 partial' in pend2
        assert 'completion_reason: turn_duration' in pend1
        assert 'completion_reason: turn_duration' in pend2
        assert 'claude agent2 partial' not in pend1
        assert 'claude agent1 partial' not in pend2

        code, watch1, stderr = _run_phase2_local(['watch', job1], cwd=project_root)
        assert code == 0, stderr
        code, watch2, stderr = _run_phase2_local(['watch', job2], cwd=project_root)
        assert code == 0, stderr
        assert 'agent_name: agent1' in watch1
        assert 'agent_name: agent2' in watch2
        assert 'claude agent2 partial' not in watch1
        assert 'claude agent1 partial' not in watch2

        layout = PathLayout(project_root)
        event_store = JobEventStore(layout)
        _, events1 = event_store.read_since('agent1', 0)
        _, events2 = event_store.read_since('agent2', 0)
        assert all(event.agent_name == 'agent1' for event in events1)
        assert all(event.agent_name == 'agent2' for event in events2)
        assert any(event.job_id == job1 for event in events1)
        assert any(event.job_id == job2 for event in events2)
        assert not any(event.job_id == job2 for event in events1)
        assert not any(event.job_id == job1 for event in events2)

        job_store = JobStore(layout)
        latest1 = job_store.get_latest('agent1', job1)
        latest2 = job_store.get_latest('agent2', job2)
        assert latest1 is not None and latest1.status.value == 'completed'
        assert latest2 is not None and latest2.status.value == 'completed'

        assert any(pane_id == '%31' and request_ids[0] in text for pane_id, text in sent)
        assert any(pane_id == '%32' and request_ids[1] in text for pane_id, text in sent)
    finally:
        _assert_phase2_app_shutdown_clean(project_root, app, thread)


def test_ccb_two_named_gemini_agents_concurrent_ask_isolated(monkeypatch, tmp_path: Path) -> None:
    from jobs.store import JobEventStore, JobStore
    from provider_execution import gemini as gemini_adapter_module
    from storage.paths import PathLayout

    project_root = tmp_path / 'repo-dual-gemini'
    _write(project_root / '.ccb' / 'ccb.config', _dual_named_agent_config_text('agent1', 'gemini', 'agent2', 'gemini'))
    _write(
        project_root / '.ccb' / '.gemini-agent1-session',
        json.dumps(
            {
                'terminal': 'tmux',
                'pane_id': '%41',
                'work_dir': str(project_root / '.ccb' / 'workspaces' / 'agent1'),
                'gemini_session_id': 'gemini-agent1-session-id',
                'gemini_session_path': str(tmp_path / 'gemini-agent1.json'),
            },
            ensure_ascii=False,
            indent=2,
        ) + '\n',
    )
    _write(
        project_root / '.ccb' / '.gemini-agent2-session',
        json.dumps(
            {
                'terminal': 'tmux',
                'pane_id': '%42',
                'work_dir': str(project_root / '.ccb' / 'workspaces' / 'agent2'),
                'gemini_session_id': 'gemini-agent2-session-id',
                'gemini_session_path': str(tmp_path / 'gemini-agent2.json'),
            },
            ensure_ascii=False,
            indent=2,
        ) + '\n',
    )

    request_ids = ('job_6e11a1', 'job_6e11a2')
    sent: list[tuple[str, str]] = []

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            sent.append((pane_id, text))

        def is_alive(self, pane_id: str) -> bool:
            return pane_id in {'%41', '%42'}

    class FakeSession:
        def __init__(self, *, pane_id: str, session_id: str, session_path: Path, work_dir: Path) -> None:
            self.data = {'pane_id': pane_id}
            self.gemini_session_id = session_id
            self.gemini_session_path = str(session_path)
            self.work_dir = str(work_dir)

        def ensure_pane(self):
            return True, self.data['pane_id']

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            work_dir = Path(kwargs.get('work_dir') or '')
            self.agent_name = work_dir.name
            self._emitted = False
            if self.agent_name == 'agent1':
                self.reply = 'gemini agent1 stable reply'
                self.message_id = 'gemini-msg-1'
            elif self.agent_name == 'agent2':
                self.reply = 'gemini agent2 stable reply'
                self.message_id = 'gemini-msg-2'
            else:
                raise AssertionError(f'unexpected gemini work_dir: {work_dir}')

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': f'{self.agent_name}.json', 'msg_count': 0}

        def try_get_message(self, state):
            if self._emitted:
                return None, state
            self._emitted = True
            return (
                self.reply,
                {
                    **state,
                    'msg_count': 1,
                    'last_gemini_id': self.message_id,
                    'mtime_ns': 123456789,
                },
            )

    def _load_fake_session(work_dir, instance=None):
        work_dir = Path(work_dir)
        if instance is None:
            return None
        if instance == 'agent1':
            return FakeSession(
                pane_id='%41',
                session_id='gemini-agent1-session-id',
                session_path=tmp_path / 'gemini-agent1.json',
                work_dir=project_root / '.ccb' / 'workspaces' / 'agent1',
            )
        if instance == 'agent2':
            return FakeSession(
                pane_id='%42',
                session_id='gemini-agent2-session-id',
                session_path=tmp_path / 'gemini-agent2.json',
                work_dir=project_root / '.ccb' / 'workspaces' / 'agent2',
            )
        raise AssertionError(f'unexpected instance: {instance}')

    monkeypatch.setattr(gemini_adapter_module, 'load_project_session', _load_fake_session)
    monkeypatch.setattr(gemini_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(gemini_adapter_module, 'GeminiLogReader', FakeReader)
    app = CcbdApp(project_root)
    _freeze_job_ids(app, monkeypatch, *request_ids)
    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for_path(app.paths.ccbd_socket_path)

    try:
        code, stdout, stderr = _run_phase2_local([], cwd=project_root, start_app=app)
        assert code == 0, stderr
        assert 'agents: agent1, agent2' in stdout

        runtime_agent1 = json.loads((project_root / '.ccb' / 'agents' / 'agent1' / 'runtime.json').read_text(encoding='utf-8'))
        runtime_agent2 = json.loads((project_root / '.ccb' / 'agents' / 'agent2' / 'runtime.json').read_text(encoding='utf-8'))
        assert runtime_agent1['agent_name'] == 'agent1'
        assert runtime_agent2['agent_name'] == 'agent2'
        assert runtime_agent1['runtime_ref'] != runtime_agent2['runtime_ref']
        assert runtime_agent1['workspace_path'] == str(project_root)
        assert runtime_agent2['workspace_path'] == str(project_root)

        code, stdout1, stderr = _run_phase2_local(['ask', 'agent1', 'from', 'user', 'hello gemini agent1'], cwd=project_root)
        assert code == 0, stderr
        job1 = _extract_accepted_job_id(stdout1, target='agent1')

        code, stdout2, stderr = _run_phase2_local(['ask', 'agent2', 'from', 'user', 'hello gemini agent2'], cwd=project_root)
        assert code == 0, stderr
        job2 = _extract_accepted_job_id(stdout2, target='agent2')

        pend1 = _wait_for_phase2_status(project_root, 'agent1', 'completed', timeout=DUAL_PROVIDER_STATUS_TIMEOUT)
        pend2 = _wait_for_phase2_status(project_root, 'agent2', 'completed', timeout=DUAL_PROVIDER_STATUS_TIMEOUT)
        assert f'job_id: {job1}' in pend1
        assert f'job_id: {job2}' in pend2
        assert 'reply: gemini agent1 stable reply' in pend1
        assert 'reply: gemini agent2 stable reply' in pend2
        assert 'completion_reason: session_reply_stable' in pend1
        assert 'completion_reason: session_reply_stable' in pend2
        assert 'gemini agent2 stable reply' not in pend1
        assert 'gemini agent1 stable reply' not in pend2

        code, watch1, stderr = _run_phase2_local(['watch', job1], cwd=project_root)
        assert code == 0, stderr
        code, watch2, stderr = _run_phase2_local(['watch', job2], cwd=project_root)
        assert code == 0, stderr
        assert 'agent_name: agent1' in watch1
        assert 'agent_name: agent2' in watch2
        assert 'gemini agent2 stable reply' not in watch1
        assert 'gemini agent1 stable reply' not in watch2

        layout = PathLayout(project_root)
        event_store = JobEventStore(layout)
        _, events1 = event_store.read_since('agent1', 0)
        _, events2 = event_store.read_since('agent2', 0)
        assert all(event.agent_name == 'agent1' for event in events1)
        assert all(event.agent_name == 'agent2' for event in events2)
        assert any(event.job_id == job1 for event in events1)
        assert any(event.job_id == job2 for event in events2)
        assert not any(event.job_id == job2 for event in events1)
        assert not any(event.job_id == job1 for event in events2)

        job_store = JobStore(layout)
        latest1 = job_store.get_latest('agent1', job1)
        latest2 = job_store.get_latest('agent2', job2)
        assert latest1 is not None and latest1.status.value == 'completed'
        assert latest2 is not None and latest2.status.value == 'completed'

        assert any(pane_id == '%41' and request_ids[0] in text for pane_id, text in sent)
        assert any(pane_id == '%42' and request_ids[1] in text for pane_id, text in sent)
    finally:
        _assert_phase2_app_shutdown_clean(project_root, app, thread)


def test_ccb_two_named_opencode_agents_concurrent_ask_isolated(monkeypatch, tmp_path: Path) -> None:
    from jobs.store import JobEventStore, JobStore
    from provider_backends.opencode.session_runtime.model import OpenCodeProjectSession
    from provider_execution import opencode as opencode_adapter_module
    from storage.paths import PathLayout

    project_root = tmp_path / 'repo-dual-opencode'
    shared_work_dir = project_root / '.ccb' / 'workspaces' / 'shared-opencode'
    shared_work_dir.mkdir(parents=True, exist_ok=True)
    _write(project_root / '.ccb' / 'ccb.config', _dual_named_agent_config_text('agent1', 'opencode', 'agent2', 'opencode'))
    _write(
        project_root / '.ccb' / '.opencode-agent1-session',
        json.dumps(
            {
                'terminal': 'tmux',
                'pane_id': '%51',
                'work_dir': str(shared_work_dir),
                'opencode_project_id': 'proj-shared',
                'opencode_session_id': 'ses-agent1',
                'active': True,
            },
            ensure_ascii=False,
            indent=2,
        ) + '\n',
    )
    _write(
        project_root / '.ccb' / '.opencode-agent2-session',
        json.dumps(
            {
                'terminal': 'tmux',
                'pane_id': '%52',
                'work_dir': str(shared_work_dir),
                'opencode_project_id': 'proj-shared',
                'opencode_session_id': 'ses-agent2',
                'active': True,
            },
            ensure_ascii=False,
            indent=2,
        ) + '\n',
    )

    request_ids = ('job_6e11b1', 'job_6e11b2')
    sent: list[tuple[str, str]] = []
    anchors: dict[str, str | None] = {'ses-agent1': None, 'ses-agent2': None}

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            sent.append((pane_id, text))
            match = re.search(r'^CCB_REQ_ID:\s*(\S+)\s*$', text, re.MULTILINE)
            if pane_id == '%51':
                anchors['ses-agent1'] = match.group(1) if match else None
            elif pane_id == '%52':
                anchors['ses-agent2'] = match.group(1) if match else None

        def is_alive(self, pane_id: str) -> bool:
            return pane_id in {'%51', '%52'}

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            self.project_id = kwargs.get('project_id')
            self.session_id_filter = kwargs.get('session_id_filter')
            self.work_dir = Path(kwargs.get('work_dir') or '')
            self._emitted = False
            if self.session_id_filter == 'ses-agent1':
                self.reply = 'opencode agent1 final'
            elif self.session_id_filter == 'ses-agent2':
                self.reply = 'opencode agent2 final'
            else:
                raise AssertionError(f'unexpected opencode session filter: {self.session_id_filter!r}')

        def capture_state(self):
            return {
                'session_path': str(self.work_dir / f'{self.session_id_filter}.json'),
                'session_id': self.session_id_filter,
            }

        def try_get_message(self, state):
            if self._emitted:
                return None, state
            self._emitted = True
            req_id = anchors.get(str(self.session_id_filter)) or ''
            return (
                self.reply,
                {
                    **state,
                    'last_assistant_id': f'msg-{self.session_id_filter}',
                    'last_assistant_parent_id': f'usr-{self.session_id_filter}',
                    'last_assistant_req_id': req_id,
                    'last_assistant_completed': 1234,
                },
            )

    monkeypatch.setattr(OpenCodeProjectSession, 'ensure_pane', lambda self: (True, self.pane_id))
    monkeypatch.setattr(opencode_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(opencode_adapter_module, 'OpenCodeLogReader', FakeReader)
    app = CcbdApp(project_root)
    _freeze_job_ids(app, monkeypatch, *request_ids)
    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for_path(app.paths.ccbd_socket_path)

    try:
        code, stdout, stderr = _run_phase2_local([], cwd=project_root, start_app=app)
        assert code == 0, stderr
        assert 'agents: agent1, agent2' in stdout

        runtime_agent1 = json.loads((project_root / '.ccb' / 'agents' / 'agent1' / 'runtime.json').read_text(encoding='utf-8'))
        runtime_agent2 = json.loads((project_root / '.ccb' / 'agents' / 'agent2' / 'runtime.json').read_text(encoding='utf-8'))
        assert runtime_agent1['agent_name'] == 'agent1'
        assert runtime_agent2['agent_name'] == 'agent2'
        assert runtime_agent1['runtime_ref'] != runtime_agent2['runtime_ref']

        code, stdout1, stderr = _run_phase2_local(['ask', 'agent1', 'from', 'user', 'hello opencode agent1'], cwd=project_root)
        assert code == 0, stderr
        job1 = _extract_accepted_job_id(stdout1, target='agent1')

        code, stdout2, stderr = _run_phase2_local(['ask', 'agent2', 'from', 'user', 'hello opencode agent2'], cwd=project_root)
        assert code == 0, stderr
        job2 = _extract_accepted_job_id(stdout2, target='agent2')

        pend1 = _wait_for_phase2_status(project_root, 'agent1', 'completed', timeout=DUAL_PROVIDER_STATUS_TIMEOUT)
        pend2 = _wait_for_phase2_status(project_root, 'agent2', 'completed', timeout=DUAL_PROVIDER_STATUS_TIMEOUT)
        assert f'job_id: {job1}' in pend1
        assert f'job_id: {job2}' in pend2
        assert 'reply: opencode agent1 final' in pend1
        assert 'reply: opencode agent2 final' in pend2
        assert 'completion_reason: assistant_completed' in pend1
        assert 'completion_reason: assistant_completed' in pend2
        assert 'opencode agent2 final' not in pend1
        assert 'opencode agent1 final' not in pend2

        code, watch1, stderr = _run_phase2_local(['watch', job1], cwd=project_root)
        assert code == 0, stderr
        code, watch2, stderr = _run_phase2_local(['watch', job2], cwd=project_root)
        assert code == 0, stderr
        assert 'agent_name: agent1' in watch1
        assert 'agent_name: agent2' in watch2
        assert 'opencode agent2 final' not in watch1
        assert 'opencode agent1 final' not in watch2

        layout = PathLayout(project_root)
        event_store = JobEventStore(layout)
        _, events1 = event_store.read_since('agent1', 0)
        _, events2 = event_store.read_since('agent2', 0)
        assert all(event.agent_name == 'agent1' for event in events1)
        assert all(event.agent_name == 'agent2' for event in events2)
        assert any(event.job_id == job1 for event in events1)
        assert any(event.job_id == job2 for event in events2)
        assert not any(event.job_id == job2 for event in events1)
        assert not any(event.job_id == job1 for event in events2)

        job_store = JobStore(layout)
        latest1 = job_store.get_latest('agent1', job1)
        latest2 = job_store.get_latest('agent2', job2)
        assert latest1 is not None and latest1.status.value == 'completed'
        assert latest2 is not None and latest2.status.value == 'completed'

        assert any(pane_id == '%51' and request_ids[0] in text for pane_id, text in sent)
        assert any(pane_id == '%52' and request_ids[1] in text for pane_id, text in sent)
    finally:
        _assert_phase2_app_shutdown_clean(project_root, app, thread)


@pytest.mark.provider_blackbox
def test_ccb_gemini_real_adapter_recovers_after_ccbd_restart_and_rotate_clears_stale_preview(
    monkeypatch, tmp_path: Path
) -> None:
    from provider_execution import gemini as gemini_adapter_module

    fixed_req_id = 'job_6e1101'
    project_root = tmp_path / 'grr'
    old_session_path = str(tmp_path / 'gso.json')
    new_session_path = str(tmp_path / 'gsn.json')
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('gemini'))
    _write(
        project_root / '.ccb' / '.gemini-session',
        json.dumps(
            {
                'terminal': 'tmux',
                'pane_id': '%3',
                'work_dir': str(project_root),
                'gemini_session_id': 'gemini-session-id',
                'gemini_session_path': old_session_path,
            },
            ensure_ascii=False,
            indent=2,
        ) + '\n',
    )

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            self.sent = (pane_id, text)

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%3'

    class FakeSession:
        data = {}
        gemini_session_path = old_session_path
        gemini_session_id = 'gemini-session-id'
        work_dir = str(project_root)

        def ensure_pane(self):
            return True, '%3'

    class FakeReader:
        instances = 0

        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            FakeReader.instances += 1
            self.instance_id = FakeReader.instances
            self._calls = 0

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': old_session_path, 'msg_count': 0}

        def try_get_message(self, state):
            self._calls += 1
            if self.instance_id == 1:
                if self._calls == 1:
                    return (
                        'old preview reply',
                        {
                            **state,
                            'session_path': old_session_path,
                            'msg_count': 1,
                            'last_gemini_id': 'msg-old',
                            'mtime_ns': 111,
                        },
                    )
                return None, state
            if self._calls == 1:
                return (
                    None,
                    {
                        **state,
                        'session_path': new_session_path,
                        'msg_count': 0,
                        'last_gemini_id': None,
                        'mtime_ns': 222,
                    },
                )
            if self._calls == 2:
                return (
                    'rotated final stable reply',
                    {
                        **state,
                        'session_path': new_session_path,
                        'msg_count': 1,
                        'last_gemini_id': 'msg-new',
                        'mtime_ns': 333,
                    },
                )
            return None, state

    monkeypatch.setattr(gemini_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(gemini_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(gemini_adapter_module, 'GeminiLogReader', FakeReader)
    app1 = CcbdApp(project_root)
    _freeze_job_ids(app1, monkeypatch, fixed_req_id)
    thread1 = threading.Thread(target=app1.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread1.start()
    _wait_for_path(app1.paths.ccbd_socket_path)

    try:
        code, stdout, stderr = _run_phase2_local([], cwd=project_root, start_app=app1)
        assert code == 0, stderr

        code, stdout, stderr = _run_phase2_local(['ask', 'demo', 'from', 'user', 'resume gemini and rotate'], cwd=project_root)
        assert code == 0, stderr
        job_id = _extract_accepted_job_id(stdout, target='demo')

        deadline = time.time() + 3.0
        last_stdout = ''
        while time.time() < deadline:
            code, pend, stderr = _run_phase2_local(['pend', 'demo'], cwd=project_root)
            assert code == 0, stderr
            last_stdout = pend
            if f'job_id: {job_id}' in pend and 'status: running' in pend and 'reply: old preview reply' in pend:
                break
            time.sleep(0.05)
        else:
            raise AssertionError(f'expected stale preview before restart; last={last_stdout!r}')

        app1.request_shutdown()
        thread1.join(timeout=2)
        assert not thread1.is_alive()

        app2 = CcbdApp(project_root)
        thread2 = threading.Thread(target=app2.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        thread2.start()
        _wait_for_path(app2.paths.ccbd_socket_path)
        try:
            deadline = time.time() + 3.0
            last_stdout = ''
            while time.time() < deadline:
                code, pend, stderr = _run_phase2_local(['pend', 'demo'], cwd=project_root)
                assert code == 0, stderr
                last_stdout = pend
                if f'job_id: {job_id}' in pend and 'status: running' in pend and 'reply: old preview reply' not in pend:
                    break
                time.sleep(0.05)
            else:
                raise AssertionError(f'expected rotate to clear stale preview after restart; last={last_stdout!r}')

            pend = _wait_for_phase2_status(project_root, job_id, 'completed', timeout=5.0)
            assert 'reply: rotated final stable reply' in pend
            assert 'completion_reason: session_reply_stable' in pend
            assert 'completion_confidence: observed' in pend
        finally:
            app2.request_shutdown()
            thread2.join(timeout=2)
            assert not thread2.is_alive()
    finally:
        if thread1.is_alive():
            app1.request_shutdown()
            thread1.join(timeout=2)
            assert not thread1.is_alive()


def test_ccb_fake_provider_can_fail(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-fake-fail'
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('fake'))

    start = _run_ccb([], cwd=project_root)
    assert start.returncode == 0, start.stderr

    ask = _run_ccb(
        [
            'ask',
            '--task-id',
            'fake;status=failed;reason=api_error;confidence=exact;latency_ms=0',
            'demo',
            'from',
            'user',
            'explode',
        ],
        cwd=project_root,
    )
    assert ask.returncode == 0, ask.stderr
    job_id = _extract_accepted_job_id(ask.stdout, target='demo')

    failed = _wait_for_status(project_root, job_id, 'failed', timeout=5.0)
    assert 'reply: FAKE[demo] explode' in failed.stdout
    assert 'completion_reason: api_error' in failed.stdout
    assert 'completion_confidence: exact' in failed.stdout

    watch = _run_ccb(['watch', job_id], cwd=project_root)
    assert watch.returncode == 0, watch.stderr
    assert 'watch_status: terminal' in watch.stdout
    assert 'status: failed' in watch.stdout

    kill = _run_ccb(['kill'], cwd=project_root)
    assert kill.returncode == 0, kill.stderr


def test_ccb_fake_gemini_provider_observed_completion(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-fake-gemini'
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('fake-gemini'))

    start = _run_ccb([], cwd=project_root)
    assert start.returncode == 0, start.stderr

    ask = _run_ccb(
        [
            'ask',
            '--task-id',
            'fake;script=[{"t":0,"type":"anchor_seen"},{"t":10,"type":"session_snapshot","reply":"stable reply"}]',
            'demo',
            'from',
            'user',
            'observe',
        ],
        cwd=project_root,
    )
    assert ask.returncode == 0, ask.stderr
    job_id = _extract_accepted_job_id(ask.stdout, target='demo')

    completed = _wait_for_status(project_root, job_id, 'completed', timeout=6.0)
    assert 'reply: stable reply' in completed.stdout
    assert 'completion_reason: session_reply_stable' in completed.stdout
    assert 'completion_confidence: observed' in completed.stdout

    watch = _run_ccb(['watch', job_id], cwd=project_root)
    assert watch.returncode == 0, watch.stderr
    assert 'completion_item' in watch.stdout
    assert 'completion_state_updated' in watch.stdout
    assert 'completion_terminal' in watch.stdout

    kill = _run_ccb(['kill'], cwd=project_root)
    assert kill.returncode == 0, kill.stderr


def test_ccb_fake_legacy_provider_degraded_done_marker_completion(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-fake-legacy'
    _write(
        project_root / '.ccb' / 'ccb.config',
        _single_agent_config_text('fake-legacy'),
    )

    start = _run_ccb([], cwd=project_root)
    assert start.returncode == 0, start.stderr

    ask = _run_ccb(
        [
            'ask',
            '--task-id',
            'fake;script=[{"t":0,"type":"assistant_final","text":"legacy reply","done_marker":true}]',
            'demo',
            'from',
            'user',
            'legacy',
        ],
        cwd=project_root,
    )
    assert ask.returncode == 0, ask.stderr
    job_id = _extract_accepted_job_id(ask.stdout, target='demo')

    completed = _wait_for_status(project_root, job_id, 'completed', timeout=5.0)
    assert 'reply: legacy reply' in completed.stdout
    assert 'completion_reason: terminal_done_marker' in completed.stdout
    assert 'completion_confidence: degraded' in completed.stdout

    kill = _run_ccb(['kill'], cwd=project_root)
    assert kill.returncode == 0, kill.stderr


@pytest.mark.provider_blackbox
def test_ccb_claude_real_adapter_blackbox_watch_chain(monkeypatch, tmp_path: Path) -> None:
    from provider_execution import claude as claude_adapter_module

    fixed_req_id = 'job_ca1de1'
    project_root = tmp_path / 'repo-claude-blackbox'
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('claude'))
    _write(
        project_root / '.ccb' / '.claude-session',
        json.dumps(
            {
                'terminal': 'tmux',
                'pane_id': '%2',
                'work_dir': str(project_root),
                'claude_session_id': 'claude-session-id',
                'claude_session_path': str(tmp_path / 'claude-session.jsonl'),
            },
            ensure_ascii=False,
            indent=2,
        ) + '\n',
    )

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            self.sent = (pane_id, text)

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%2'

    class FakeSession:
        data = {}
        claude_session_path = str(tmp_path / 'claude-session.jsonl')
        claude_session_id = 'claude-session-id'
        claude_projects_root = None
        work_dir = str(project_root)

        def ensure_pane(self):
            return True, '%2'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._events = [
                ('user', f'CCB_REQ_ID: {fixed_req_id}\n\nprompt'),
                ('assistant', 'partial'),
                ('assistant', f'final\nCCB_DONE: {fixed_req_id}'),
            ]

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': str(tmp_path / 'claude-session.jsonl'), 'offset': 0}

        def try_get_events(self, state):
            index = int(state.get('index', 0))
            if index >= len(self._events):
                return [], state
            return [self._events[index]], {**state, 'index': index + 1}

    monkeypatch.setattr(claude_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(claude_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(claude_adapter_module, 'ClaudeLogReader', FakeReader)
    app = CcbdApp(project_root)
    _freeze_job_ids(app, monkeypatch, fixed_req_id)
    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for_path(app.paths.ccbd_socket_path)

    try:
        code, stdout, stderr = _run_phase2_local([], cwd=project_root, start_app=app)
        assert code == 0, stderr
        assert 'start_status: ok' in stdout

        code, stdout, stderr = _run_phase2_local(['doctor'], cwd=project_root)
        assert code == 0, stderr
        assert 'completion=session_boundary' in stdout

        code, stdout, stderr = _run_phase2_local(['ask', 'demo', 'from', 'user', 'hello claude'], cwd=project_root)
        assert code == 0, stderr
        job_id = _extract_accepted_job_id(stdout, target='demo')

        pend = _wait_for_phase2_status(project_root, 'demo', 'completed')
        assert f'job_id: {job_id}' in pend
        assert 'reply: partial\nfinal' in pend
        assert 'completion_reason: task_complete' in pend
        assert 'completion_confidence: observed' in pend

        code, stdout, stderr = _run_phase2_local(['watch', job_id], cwd=project_root)
        assert code == 0, stderr
        assert 'watch_status: terminal' in stdout
        assert f'job_id: {job_id}' in stdout
        assert 'status: completed' in stdout

        code, stdout, stderr = _run_phase2_local(['watch', 'demo'], cwd=project_root)
        assert code == 0, stderr
        assert 'watch_status: terminal' in stdout
        assert f'job_id: {job_id}' in stdout
    finally:
        app.shutdown()
        thread.join(timeout=2)
        assert not thread.is_alive()


@pytest.mark.provider_blackbox
def test_ccb_claude_real_adapter_blackbox_watch_chain_without_done_marker(monkeypatch, tmp_path: Path) -> None:
    from provider_execution import claude as claude_adapter_module

    fixed_req_id = 'job_ca1de2'
    project_root = tmp_path / 'repo-claude-td'
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('claude'))
    _write(
        project_root / '.ccb' / '.claude-session',
        json.dumps(
            {
                'terminal': 'tmux',
                'pane_id': '%2',
                'work_dir': str(project_root),
                'claude_session_id': 'claude-session-id',
                'claude_session_path': str(tmp_path / 'claude-session.jsonl'),
            },
            ensure_ascii=False,
            indent=2,
        ) + '\n',
    )

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            self.sent = (pane_id, text)

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%2'

    class FakeSession:
        data = {}
        claude_session_path = str(tmp_path / 'claude-session.jsonl')
        claude_session_id = 'claude-session-id'
        claude_projects_root = None
        work_dir = str(project_root)

        def ensure_pane(self):
            return True, '%2'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._events = [
                {'role': 'user', 'text': f'CCB_REQ_ID: {fixed_req_id}\n\nprompt', 'entry_type': 'user'},
                {'role': 'assistant', 'text': 'final without done', 'entry_type': 'assistant', 'uuid': 'assistant-1'},
                {'role': 'system', 'text': '', 'entry_type': 'system', 'subtype': 'turn_duration', 'parent_uuid': 'assistant-1'},
            ]

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': str(tmp_path / 'claude-session.jsonl'), 'offset': 0}

        def try_get_entries(self, state):
            index = int(state.get('index', 0))
            if index >= len(self._events):
                return [], state
            return [self._events[index]], {**state, 'index': index + 1}

    monkeypatch.setattr(claude_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(claude_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(claude_adapter_module, 'ClaudeLogReader', FakeReader)
    app = CcbdApp(project_root)
    _freeze_job_ids(app, monkeypatch, fixed_req_id)
    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for_path(app.paths.ccbd_socket_path)

    try:
        code, stdout, stderr = _run_phase2_local([], cwd=project_root, start_app=app)
        assert code == 0, stderr

        code, stdout, stderr = _run_phase2_local(['ask', 'demo', 'from', 'user', 'hello claude'], cwd=project_root)
        assert code == 0, stderr
        job_id = _extract_accepted_job_id(stdout, target='demo')

        pend = _wait_for_phase2_status(project_root, 'demo', 'completed')
        assert f'job_id: {job_id}' in pend
        assert 'reply: final without done' in pend
        assert 'completion_reason: turn_duration' in pend
        assert 'completion_confidence: observed' in pend

        code, stdout, stderr = _run_phase2_local(['watch', job_id], cwd=project_root)
        assert code == 0, stderr
        assert 'watch_status: terminal' in stdout
        assert 'status: completed' in stdout
    finally:
        app.shutdown()
        thread.join(timeout=2)
        assert not thread.is_alive()


@pytest.mark.provider_blackbox
def test_ccb_claude_real_adapter_recovers_after_ccbd_restart(monkeypatch, tmp_path: Path) -> None:
    from provider_execution import claude as claude_adapter_module

    fixed_req_id = 'job_ca1de3'
    project_root = tmp_path / 'repo-claude-resume'
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('claude'))
    _write(
        project_root / '.ccb' / '.claude-session',
        json.dumps(
            {
                'terminal': 'tmux',
                'pane_id': '%2',
                'work_dir': str(project_root),
                'claude_session_id': 'claude-session-id',
                'claude_session_path': str(tmp_path / 'claude-session.jsonl'),
            },
            ensure_ascii=False,
            indent=2,
        ) + '\n',
    )

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            self.sent = (pane_id, text)

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%2'

    class FakeSession:
        data = {}
        claude_session_path = str(tmp_path / 'claude-session.jsonl')
        claude_session_id = 'claude-session-id'
        claude_projects_root = None
        work_dir = str(project_root)

        def ensure_pane(self):
            return True, '%2'

    class FakeReader:
        instances = 0

        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            FakeReader.instances += 1
            self.instance_id = FakeReader.instances
            self._events = [
                {'role': 'user', 'text': f'CCB_REQ_ID: {fixed_req_id}\n\nprompt', 'entry_type': 'user'},
                {'role': 'assistant', 'text': 'partial before restart', 'entry_type': 'assistant', 'uuid': 'assistant-resume'},
                {'role': 'system', 'text': '', 'entry_type': 'system', 'subtype': 'turn_duration', 'parent_uuid': 'assistant-resume'},
            ]

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': str(tmp_path / 'claude-session.jsonl'), 'offset': 0, 'carry': b''}

        def try_get_entries(self, state):
            index = int(state.get('index', 0))
            limit = 2 if self.instance_id == 1 else len(self._events)
            if index >= limit:
                return [], state
            return [self._events[index]], {**state, 'index': index + 1}

    monkeypatch.setattr(claude_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(claude_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(claude_adapter_module, 'ClaudeLogReader', FakeReader)
    app1 = CcbdApp(project_root)
    _freeze_job_ids(app1, monkeypatch, fixed_req_id)
    thread1 = threading.Thread(target=app1.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread1.start()
    _wait_for_path(app1.paths.ccbd_socket_path)

    try:
        code, stdout, stderr = _run_phase2_local([], cwd=project_root, start_app=app1)
        assert code == 0, stderr

        code, stdout, stderr = _run_phase2_local(['ask', 'demo', 'from', 'user', 'resume claude'], cwd=project_root)
        assert code == 0, stderr
        job_id = _extract_accepted_job_id(stdout, target='demo')

        running = _wait_for_phase2_status(project_root, 'demo', 'running')
        assert f'job_id: {job_id}' in running

        deadline = time.time() + 2.0
        while time.time() < deadline:
            events_path = project_root / '.ccb' / 'agents' / 'demo' / 'events.jsonl'
            if events_path.exists() and 'assistant_chunk' in events_path.read_text(encoding='utf-8'):
                break
            time.sleep(0.05)
        else:
            raise AssertionError('expected assistant_chunk before restart')

        app1.request_shutdown()
        thread1.join(timeout=2)
        assert not thread1.is_alive()

        app2 = CcbdApp(project_root)
        thread2 = threading.Thread(target=app2.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        thread2.start()
        _wait_for_path(app2.paths.ccbd_socket_path)
        try:
            pend = _wait_for_phase2_status(project_root, job_id, 'completed')
            assert 'reply: partial before restart' in pend
            assert 'completion_reason: turn_duration' in pend
            assert 'completion_confidence: observed' in pend
        finally:
            app2.request_shutdown()
            thread2.join(timeout=2)
            assert not thread2.is_alive()
    finally:
        if thread1.is_alive():
            app1.request_shutdown()
            thread1.join(timeout=2)
            assert not thread1.is_alive()


@pytest.mark.provider_blackbox
def test_ccb_claude_real_adapter_blackbox_rotate_and_subagent_only_new_main_boundary_completes(
    monkeypatch, tmp_path: Path
) -> None:
    from provider_execution import claude as claude_adapter_module

    fixed_req_id = 'job_ca1de4'
    project_root = tmp_path / 'crs'
    old_session_path = str(tmp_path / 'cso.jsonl')
    new_session_path = str(tmp_path / 'csn.jsonl')
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('claude'))
    _write(
        project_root / '.ccb' / '.claude-session',
        json.dumps(
            {
                'terminal': 'tmux',
                'pane_id': '%2',
                'work_dir': str(project_root),
                'claude_session_id': 'claude-session-id',
                'claude_session_path': old_session_path,
            },
            ensure_ascii=False,
            indent=2,
        ) + '\n',
    )

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            self.sent = (pane_id, text)

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%2'

    class FakeSession:
        data = {}
        claude_session_path = old_session_path
        claude_session_id = 'claude-session-id'
        claude_projects_root = None
        work_dir = str(project_root)

        def ensure_pane(self):
            return True, '%2'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._calls = 0

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {
                'session_path': old_session_path,
                'offset': 0,
            }

        def try_get_entries(self, state):
            self._calls += 1
            if self._calls == 1:
                return [
                    {'role': 'user', 'text': f'CCB_REQ_ID: {fixed_req_id}\n\nprompt', 'entry_type': 'user'},
                    {'role': 'assistant', 'text': 'old partial', 'entry_type': 'assistant', 'uuid': 'assistant-old'},
                    {
                        'role': 'assistant',
                        'text': 'old child work',
                        'entry_type': 'assistant',
                        'uuid': 'assistant-child-old',
                        'subagent_id': 'child-old',
                    },
                    {
                        'role': 'system',
                        'text': '',
                        'entry_type': 'system',
                        'subtype': 'turn_duration',
                        'parent_uuid': 'assistant-child-old',
                    },
                ], {**state, 'offset': 1}

            if self._calls < 4:
                return [], state

            if self._calls == 4:
                return [
                    {'role': 'user', 'text': f'CCB_REQ_ID: {fixed_req_id}\n\nprompt again', 'entry_type': 'user'},
                    {
                        'role': 'system',
                        'text': '',
                        'entry_type': 'system',
                        'subtype': 'turn_duration',
                        'parent_uuid': 'assistant-old',
                    },
                    {'role': 'assistant', 'text': 'new partial', 'entry_type': 'assistant', 'uuid': 'assistant-new'},
                    {
                        'role': 'assistant',
                        'text': 'new child work',
                        'entry_type': 'assistant',
                        'uuid': 'assistant-child-new',
                        'subagent_id': 'child-new',
                    },
                    {
                        'role': 'system',
                        'text': '',
                        'entry_type': 'system',
                        'subtype': 'turn_duration',
                        'parent_uuid': 'assistant-child-new',
                    },
                    {
                        'role': 'system',
                        'text': '',
                        'entry_type': 'system',
                        'subtype': 'turn_duration',
                        'parent_uuid': 'assistant-new',
                    },
                ], {
                    **state,
                    'offset': 2,
                    'session_path': new_session_path,
                }

            return [], state

    monkeypatch.setattr(claude_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(claude_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(claude_adapter_module, 'ClaudeLogReader', FakeReader)
    app = CcbdApp(project_root)
    _freeze_job_ids(app, monkeypatch, fixed_req_id)
    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for_path(app.paths.ccbd_socket_path)

    try:
        code, stdout, stderr = _run_phase2_local([], cwd=project_root, start_app=app)
        assert code == 0, stderr

        code, stdout, stderr = _run_phase2_local(['ask', 'demo', 'from', 'user', 'hello claude rotate'], cwd=project_root)
        assert code == 0, stderr
        job_id = _extract_accepted_job_id(stdout, target='demo')

        deadline = time.time() + 3.0
        last_stdout = ''
        while time.time() < deadline:
            code, pend, stderr = _run_phase2_local(['pend', 'demo'], cwd=project_root)
            assert code == 0, stderr
            last_stdout = pend
            if f'job_id: {job_id}' in pend and 'status: running' in pend and 'reply: old partial' in pend:
                assert 'completion_reason: None' in pend
                break
            time.sleep(0.05)
        else:
            raise AssertionError(f'expected running old-session preview before rotate completion; last={last_stdout!r}')

        pend = _wait_for_phase2_status(project_root, job_id, 'completed', timeout=5.0)
        assert 'reply: old partial' not in pend
        assert 'reply: new partial\nnew child work' in pend
        assert 'completion_reason: turn_duration' in pend
        assert 'completion_confidence: observed' in pend

        code, stdout, stderr = _run_phase2_local(['watch', job_id], cwd=project_root)
        assert code == 0, stderr
        assert 'watch_status: terminal' in stdout
        assert f'job_id: {job_id}' in stdout
        assert 'status: completed' in stdout
    finally:
        app.shutdown()
        thread.join(timeout=2)
        assert not thread.is_alive()


@pytest.mark.provider_blackbox
def test_ccb_claude_real_adapter_recovers_after_ccbd_restart_rotate_and_subagent_only_new_main_boundary_completes(
    monkeypatch, tmp_path: Path
) -> None:
    from provider_execution import claude as claude_adapter_module

    fixed_req_id = 'job_ca1de5'
    project_root = tmp_path / 'crsr'
    old_session_path = str(tmp_path / 'rcso.jsonl')
    new_session_path = str(tmp_path / 'rcsn.jsonl')
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('claude'))
    _write(
        project_root / '.ccb' / '.claude-session',
        json.dumps(
            {
                'terminal': 'tmux',
                'pane_id': '%2',
                'work_dir': str(project_root),
                'claude_session_id': 'claude-session-id',
                'claude_session_path': old_session_path,
            },
            ensure_ascii=False,
            indent=2,
        ) + '\n',
    )

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            self.sent = (pane_id, text)

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%2'

    class FakeSession:
        data = {}
        claude_session_path = old_session_path
        claude_session_id = 'claude-session-id'
        claude_projects_root = None
        work_dir = str(project_root)

        def ensure_pane(self):
            return True, '%2'

    class FakeReader:
        instances = 0

        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            FakeReader.instances += 1
            self.instance_id = FakeReader.instances
            self._events = [
                {'role': 'user', 'text': f'CCB_REQ_ID: {fixed_req_id}\n\nprompt', 'entry_type': 'user'},
                {'role': 'assistant', 'text': 'old partial', 'entry_type': 'assistant', 'uuid': 'assistant-old'},
                {
                    'role': 'assistant',
                    'text': 'old child work',
                    'entry_type': 'assistant',
                    'uuid': 'assistant-child-old',
                    'subagent_id': 'child-old',
                },
                {
                    'role': 'system',
                    'text': '',
                    'entry_type': 'system',
                    'subtype': 'turn_duration',
                    'parent_uuid': 'assistant-child-old',
                },
                {'role': 'user', 'text': f'CCB_REQ_ID: {fixed_req_id}\n\nprompt again', 'entry_type': 'user'},
                {
                    'role': 'system',
                    'text': '',
                    'entry_type': 'system',
                    'subtype': 'turn_duration',
                    'parent_uuid': 'assistant-old',
                },
                {'role': 'assistant', 'text': 'new partial', 'entry_type': 'assistant', 'uuid': 'assistant-new'},
                {
                    'role': 'assistant',
                    'text': 'new child work',
                    'entry_type': 'assistant',
                    'uuid': 'assistant-child-new',
                    'subagent_id': 'child-new',
                },
                {
                    'role': 'system',
                    'text': '',
                    'entry_type': 'system',
                    'subtype': 'turn_duration',
                    'parent_uuid': 'assistant-child-new',
                },
                {
                    'role': 'system',
                    'text': '',
                    'entry_type': 'system',
                    'subtype': 'turn_duration',
                    'parent_uuid': 'assistant-new',
                },
            ]

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': old_session_path, 'offset': 0, 'carry': b''}

        def try_get_entries(self, state):
            index = int(state.get('index', 0))
            limit = 4 if self.instance_id == 1 else len(self._events)
            if index >= limit:
                return [], state
            next_state = {**state, 'index': index + 1}
            if self.instance_id != 1 and index >= 4:
                next_state['session_path'] = new_session_path
            return [self._events[index]], next_state

    monkeypatch.setattr(claude_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(claude_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(claude_adapter_module, 'ClaudeLogReader', FakeReader)
    app1 = CcbdApp(project_root)
    _freeze_job_ids(app1, monkeypatch, fixed_req_id)
    thread1 = threading.Thread(target=app1.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread1.start()
    _wait_for_path(app1.paths.ccbd_socket_path)

    try:
        code, stdout, stderr = _run_phase2_local([], cwd=project_root, start_app=app1)
        assert code == 0, stderr

        code, stdout, stderr = _run_phase2_local(['ask', 'demo', 'from', 'user', 'resume claude rotate'], cwd=project_root)
        assert code == 0, stderr
        job_id = _extract_accepted_job_id(stdout, target='demo')

        deadline = time.time() + 2.0
        while time.time() < deadline:
            events_path = project_root / '.ccb' / 'agents' / 'demo' / 'events.jsonl'
            if events_path.exists() and 'assistant_chunk' in events_path.read_text(encoding='utf-8'):
                break
            time.sleep(0.05)
        else:
            raise AssertionError('expected assistant_chunk before restart')

        deadline = time.time() + 5.0
        running = ''
        while time.time() < deadline:
            running = _wait_for_phase2_status(project_root, 'demo', 'running')
            if 'reply: old partial\nold child work' in running:
                break
            time.sleep(0.05)
        else:
            raise AssertionError(f'expected running partial reply before restart; last output={running!r}')
        assert f'job_id: {job_id}' in running
        assert 'completion_reason: None' in running

        app1.request_shutdown()
        thread1.join(timeout=2)
        assert not thread1.is_alive()

        app2 = CcbdApp(project_root)
        thread2 = threading.Thread(target=app2.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        thread2.start()
        _wait_for_path(app2.paths.ccbd_socket_path)
        try:
            pend = _wait_for_phase2_status(project_root, job_id, 'completed')
            assert 'reply: old partial' not in pend
            assert 'reply: new partial\nnew child work' in pend
            assert 'completion_reason: turn_duration' in pend
            assert 'completion_confidence: observed' in pend
        finally:
            app2.request_shutdown()
            thread2.join(timeout=2)
            assert not thread2.is_alive()
    finally:
        if thread1.is_alive():
            app1.request_shutdown()
            thread1.join(timeout=2)
            assert not thread1.is_alive()


@pytest.mark.provider_blackbox
def test_ccb_gemini_real_adapter_blackbox_watch_chain(monkeypatch, tmp_path: Path) -> None:
    from provider_execution import gemini as gemini_adapter_module

    fixed_req_id = 'job_6e1102'
    project_root = tmp_path / 'repo-gemini-blackbox'
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('gemini'))
    _write(
        project_root / '.ccb' / '.gemini-session',
        json.dumps(
            {
                'terminal': 'tmux',
                'pane_id': '%3',
                'work_dir': str(project_root),
                'gemini_session_id': 'gemini-session-id',
                'gemini_session_path': str(tmp_path / 'gemini-session.json'),
            },
            ensure_ascii=False,
            indent=2,
        ) + '\n',
    )

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            self.sent = (pane_id, text)

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%3'

    class FakeSession:
        data = {}
        gemini_session_path = str(tmp_path / 'gemini-session.json')
        gemini_session_id = 'gemini-session-id'
        work_dir = str(project_root)

        def ensure_pane(self):
            return True, '%3'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._emitted = False

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': str(tmp_path / 'gemini-session.json'), 'msg_count': 0}

        def try_get_message(self, state):
            if self._emitted:
                return None, state
            self._emitted = True
            return (
                'stable reply',
                {
                    **state,
                    'msg_count': 2,
                    'last_gemini_id': 'msg-2',
                    'mtime_ns': 123456789,
                },
            )

    monkeypatch.setattr(gemini_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(gemini_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(gemini_adapter_module, 'GeminiLogReader', FakeReader)
    app = CcbdApp(project_root)
    _freeze_job_ids(app, monkeypatch, fixed_req_id)
    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for_path(app.paths.ccbd_socket_path)

    try:
        code, stdout, stderr = _run_phase2_local([], cwd=project_root, start_app=app)
        assert code == 0, stderr
        assert 'start_status: ok' in stdout

        code, stdout, stderr = _run_phase2_local(['doctor'], cwd=project_root)
        assert code == 0, stderr
        assert 'completion=anchored_session_stability' in stdout

        code, stdout, stderr = _run_phase2_local(['ask', 'demo', 'from', 'user', 'hello gemini'], cwd=project_root)
        assert code == 0, stderr
        job_id = _extract_accepted_job_id(stdout, target='demo')

        pend = _wait_for_phase2_status(project_root, job_id, 'completed', timeout=5.0)
        assert 'reply: stable reply' in pend
        assert 'completion_reason: session_reply_stable' in pend
        assert 'completion_confidence: observed' in pend

        code, stdout, stderr = _run_phase2_local(['watch', job_id], cwd=project_root)
        assert code == 0, stderr
        assert 'watch_status: terminal' in stdout
        assert 'status: completed' in stdout

        code, stdout, stderr = _run_phase2_local(['watch', 'demo'], cwd=project_root)
        assert code == 0, stderr
        assert 'watch_status: terminal' in stdout
        assert f'job_id: {job_id}' in stdout
    finally:
        app.shutdown()
        thread.join(timeout=2)
        assert not thread.is_alive()


@pytest.mark.provider_blackbox
def test_ccb_gemini_real_adapter_blackbox_waits_for_last_snapshot_mutation_to_settle(
    monkeypatch, tmp_path: Path
) -> None:
    from provider_execution import gemini as gemini_adapter_module

    fixed_req_id = 'job_6e1103'
    project_root = tmp_path / 'gms'
    session_path = str(tmp_path / 'gms.json')
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('gemini'))
    _write(
        project_root / '.ccb' / '.gemini-session',
        json.dumps(
            {
                'terminal': 'tmux',
                'pane_id': '%3',
                'work_dir': str(project_root),
                'gemini_session_id': 'gemini-session-id',
                'gemini_session_path': session_path,
            },
            ensure_ascii=False,
            indent=2,
        ) + '\n',
    )

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            self.sent = (pane_id, text)

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%3'

    class FakeSession:
        data = {}
        gemini_session_path = session_path
        gemini_session_id = 'gemini-session-id'
        work_dir = str(project_root)

        def ensure_pane(self):
            return True, '%3'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._calls = 0

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': session_path, 'msg_count': 0}

        def try_get_message(self, state):
            self._calls += 1
            if self._calls == 1:
                return (
                    'draft 1',
                    {
                        **state,
                        'msg_count': 1,
                        'last_gemini_id': 'msg-1',
                        'mtime_ns': 100,
                    },
                )
            if self._calls == 2:
                return (
                    'draft 1 expanded',
                    {
                        **state,
                        'msg_count': 1,
                        'last_gemini_id': 'msg-1',
                        'mtime_ns': 200,
                    },
                )
            return (
                'final stable reply',
                {
                    **state,
                    'msg_count': 1,
                    'last_gemini_id': 'msg-1',
                    'mtime_ns': 300,
                },
            )

    monkeypatch.setattr(gemini_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(gemini_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(gemini_adapter_module, 'GeminiLogReader', FakeReader)
    app = CcbdApp(project_root)
    _freeze_job_ids(app, monkeypatch, fixed_req_id)
    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for_path(app.paths.ccbd_socket_path)

    try:
        code, stdout, stderr = _run_phase2_local([], cwd=project_root, start_app=app)
        assert code == 0, stderr

        code, stdout, stderr = _run_phase2_local(['ask', 'demo', 'from', 'user', 'hello gemini mutate'], cwd=project_root)
        assert code == 0, stderr
        job_id = _extract_accepted_job_id(stdout, target='demo')

        deadline = time.time() + 3.0
        last_stdout = ''
        while time.time() < deadline:
            code, pend, stderr = _run_phase2_local(['pend', 'demo'], cwd=project_root)
            assert code == 0, stderr
            last_stdout = pend
            if f'job_id: {job_id}' in pend and 'status: running' in pend and 'reply: final stable reply' in pend:
                assert 'completion_reason: None' in pend
                break
            time.sleep(0.05)
        else:
            raise AssertionError(f'expected running final preview before settle completion; last={last_stdout!r}')

        pend = _wait_for_phase2_status(project_root, job_id, 'completed', timeout=6.0)
        assert 'reply: final stable reply' in pend
        assert 'completion_reason: session_reply_stable' in pend
        assert 'completion_confidence: observed' in pend

        code, stdout, stderr = _run_phase2_local(['watch', job_id], cwd=project_root)
        assert code == 0, stderr
        assert 'watch_status: terminal' in stdout
        assert f'job_id: {job_id}' in stdout
        assert 'status: completed' in stdout
    finally:
        app.shutdown()
        thread.join(timeout=2)
        assert not thread.is_alive()


@pytest.mark.provider_blackbox
def test_ccb_gemini_real_adapter_blackbox_handles_long_silence_and_rotate(monkeypatch, tmp_path: Path) -> None:
    from provider_execution import gemini as gemini_adapter_module

    fixed_req_id = 'job_6e1104'
    project_root = tmp_path / 'repo-gemini-rotate'
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('gemini'))
    _write(
        project_root / '.ccb' / '.gemini-session',
        json.dumps(
            {
                'terminal': 'tmux',
                'pane_id': '%3',
                'work_dir': str(project_root),
                'gemini_session_id': 'gemini-session-id',
                'gemini_session_path': str(tmp_path / 'gemini-session-old.json'),
            },
            ensure_ascii=False,
            indent=2,
        ) + '\n',
    )

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            self.sent = (pane_id, text)

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%3'

    class FakeSession:
        data = {}
        gemini_session_path = str(tmp_path / 'gemini-session-old.json')
        gemini_session_id = 'gemini-session-id'
        work_dir = str(project_root)

        def ensure_pane(self):
            return True, '%3'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._calls = 0

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': str(tmp_path / 'gemini-session-old.json'), 'msg_count': 0}

        def try_get_message(self, state):
            self._calls += 1
            if self._calls < 4:
                return None, state
            return (
                'rotated stable reply',
                {
                    **state,
                    'session_path': str(tmp_path / 'gemini-session-new.json'),
                    'msg_count': 4,
                    'last_gemini_id': 'msg-4',
                    'mtime_ns': 987654321,
                },
            )

    monkeypatch.setattr(gemini_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(gemini_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(gemini_adapter_module, 'GeminiLogReader', FakeReader)
    app = CcbdApp(project_root)
    _freeze_job_ids(app, monkeypatch, fixed_req_id)
    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for_path(app.paths.ccbd_socket_path)

    try:
        code, stdout, stderr = _run_phase2_local([], cwd=project_root, start_app=app)
        assert code == 0, stderr

        code, stdout, stderr = _run_phase2_local(['ask', 'demo', 'from', 'user', 'hello gemini rotate'], cwd=project_root)
        assert code == 0, stderr
        job_id = _extract_accepted_job_id(stdout, target='demo')

        time.sleep(0.15)
        code, stdout, stderr = _run_phase2_local(['pend', 'demo'], cwd=project_root)
        assert code == 0, stderr
        assert f'job_id: {job_id}' in stdout
        assert 'status: running' in stdout

        pend = _wait_for_phase2_status(project_root, job_id, 'completed', timeout=5.0)
        assert 'reply: rotated stable reply' in pend
        assert 'completion_reason: session_reply_stable' in pend
        assert 'completion_confidence: observed' in pend

        code, stdout, stderr = _run_phase2_local(['watch', job_id], cwd=project_root)
        assert code == 0, stderr
        assert 'watch_status: terminal' in stdout
        assert 'status: completed' in stdout
    finally:
        app.shutdown()
        thread.join(timeout=2)
        assert not thread.is_alive()


@pytest.mark.provider_blackbox
def test_ccb_gemini_real_adapter_blackbox_clears_stale_reply_preview_after_rotate(monkeypatch, tmp_path: Path) -> None:
    from provider_execution import gemini as gemini_adapter_module

    fixed_req_id = 'job_6e1105'
    project_root = tmp_path / 'gpr'
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('gemini'))
    _write(
        project_root / '.ccb' / '.gemini-session',
        json.dumps(
            {
                'terminal': 'tmux',
                'pane_id': '%3',
                'work_dir': str(project_root),
                'gemini_session_id': 'gemini-session-id',
                'gemini_session_path': str(tmp_path / 'gemini-session-old.json'),
            },
            ensure_ascii=False,
            indent=2,
        ) + '\n',
    )

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            self.sent = (pane_id, text)

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%3'

    class FakeSession:
        data = {}
        gemini_session_path = str(tmp_path / 'gemini-session-old.json')
        gemini_session_id = 'gemini-session-id'
        work_dir = str(project_root)

        def ensure_pane(self):
            return True, '%3'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._calls = 0

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': str(tmp_path / 'gemini-session-old.json'), 'msg_count': 0}

        def try_get_message(self, state):
            self._calls += 1
            if self._calls == 1:
                return (
                    'old preview reply',
                    {
                        **state,
                        'msg_count': 1,
                        'last_gemini_id': 'msg-old',
                        'mtime_ns': 111,
                    },
                )
            if self._calls == 2:
                return (
                    None,
                    {
                        **state,
                        'session_path': str(tmp_path / 'gemini-session-new.json'),
                        'msg_count': 0,
                        'last_gemini_id': None,
                        'mtime_ns': 222,
                    },
                )
            return None, state

    monkeypatch.setattr(gemini_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(gemini_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(gemini_adapter_module, 'GeminiLogReader', FakeReader)
    app = CcbdApp(project_root)
    _freeze_job_ids(app, monkeypatch, fixed_req_id)
    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for_path(app.paths.ccbd_socket_path)

    try:
        code, stdout, stderr = _run_phase2_local([], cwd=project_root, start_app=app)
        assert code == 0, stderr

        code, stdout, stderr = _run_phase2_local(['ask', 'demo', 'from', 'user', 'hello gemini preview reset'], cwd=project_root)
        assert code == 0, stderr
        job_id = _extract_accepted_job_id(stdout, target='demo')

        deadline = time.time() + 3.0
        last_stdout = ''
        while time.time() < deadline:
            code, pend, stderr = _run_phase2_local(['pend', 'demo'], cwd=project_root)
            assert code == 0, stderr
            last_stdout = pend
            if f'job_id: {job_id}' in pend and 'status: running' in pend and 'reply: ' in pend:
                if 'reply: old preview reply' not in pend:
                    break
            time.sleep(0.05)
        else:
            raise AssertionError(f'expected running pend without stale preview; last={last_stdout!r}')
    finally:
        app.shutdown()
        thread.join(timeout=2)
        assert not thread.is_alive()


@pytest.mark.provider_blackbox
def test_ccb_gemini_real_adapter_recovers_after_ccbd_restart(monkeypatch, tmp_path: Path) -> None:
    from provider_execution import gemini as gemini_adapter_module

    fixed_req_id = 'job_6e1106'
    project_root = tmp_path / 'repo-gemini-resume'
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('gemini'))
    _write(
        project_root / '.ccb' / '.gemini-session',
        json.dumps(
            {
                'terminal': 'tmux',
                'pane_id': '%3',
                'work_dir': str(project_root),
                'gemini_session_id': 'gemini-session-id',
                'gemini_session_path': str(tmp_path / 'gemini-session.json'),
            },
            ensure_ascii=False,
            indent=2,
        ) + '\n',
    )

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            self.sent = (pane_id, text)

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%3'

    class FakeSession:
        data = {}
        gemini_session_path = str(tmp_path / 'gemini-session.json')
        gemini_session_id = 'gemini-session-id'
        work_dir = str(project_root)

        def ensure_pane(self):
            return True, '%3'

    class FakeReader:
        instances = 0

        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            FakeReader.instances += 1
            self.instance_id = FakeReader.instances

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': str(tmp_path / 'gemini-session.json'), 'msg_count': 0}

        def try_get_message(self, state):
            if self.instance_id == 1:
                if int(state.get('msg_count', 0) or 0) >= 1:
                    return None, state
                return (
                    'partial stable',
                    {
                        **state,
                        'msg_count': 1,
                        'last_gemini_id': 'msg-1',
                        'mtime_ns': 111,
                    },
                )
            if int(state.get('msg_count', 0) or 0) >= 2:
                return None, state
            return (
                'final stable reply',
                {
                    **state,
                    'msg_count': 2,
                    'last_gemini_id': 'msg-2',
                    'mtime_ns': 222,
                },
            )

    monkeypatch.setattr(gemini_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(gemini_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(gemini_adapter_module, 'GeminiLogReader', FakeReader)
    app1 = CcbdApp(project_root)
    _freeze_job_ids(app1, monkeypatch, fixed_req_id)
    thread1 = threading.Thread(target=app1.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread1.start()
    _wait_for_path(app1.paths.ccbd_socket_path)

    try:
        code, stdout, stderr = _run_phase2_local([], cwd=project_root, start_app=app1)
        assert code == 0, stderr

        code, stdout, stderr = _run_phase2_local(['ask', 'demo', 'from', 'user', 'resume gemini'], cwd=project_root)
        assert code == 0, stderr
        job_id = _extract_accepted_job_id(stdout, target='demo')

        running = _wait_for_phase2_status(project_root, 'demo', 'running')
        assert f'job_id: {job_id}' in running

        deadline = time.time() + 2.0
        while time.time() < deadline:
            events_path = project_root / '.ccb' / 'agents' / 'demo' / 'events.jsonl'
            if events_path.exists() and 'session_snapshot' in events_path.read_text(encoding='utf-8'):
                break
            time.sleep(0.05)
        else:
            raise AssertionError('expected session_snapshot before restart')

        app1.request_shutdown()
        thread1.join(timeout=2)
        assert not thread1.is_alive()

        app2 = CcbdApp(project_root)
        thread2 = threading.Thread(target=app2.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        thread2.start()
        _wait_for_path(app2.paths.ccbd_socket_path)
        try:
            pend = _wait_for_phase2_status(project_root, job_id, 'completed', timeout=5.0)
            assert 'reply: final stable reply' in pend
            assert 'completion_reason: session_reply_stable' in pend
            assert 'completion_confidence: observed' in pend
        finally:
            app2.request_shutdown()
            thread2.join(timeout=2)
            assert not thread2.is_alive()
    finally:
        if thread1.is_alive():
            app1.request_shutdown()
            thread1.join(timeout=2)
            assert not thread1.is_alive()


@pytest.mark.provider_blackbox
def test_ccb_gemini_real_adapter_recovers_after_ccbd_restart_and_waits_for_post_restart_mutation_settle(
    monkeypatch, tmp_path: Path
) -> None:
    from provider_execution import gemini as gemini_adapter_module

    fixed_req_id = 'job_6e1107'
    project_root = tmp_path / 'grm'
    session_path = str(tmp_path / 'grm.json')
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('gemini'))
    _write(
        project_root / '.ccb' / '.gemini-session',
        json.dumps(
            {
                'terminal': 'tmux',
                'pane_id': '%3',
                'work_dir': str(project_root),
                'gemini_session_id': 'gemini-session-id',
                'gemini_session_path': session_path,
            },
            ensure_ascii=False,
            indent=2,
        ) + '\n',
    )

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            self.sent = (pane_id, text)

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%3'

    class FakeSession:
        data = {}
        gemini_session_path = session_path
        gemini_session_id = 'gemini-session-id'
        work_dir = str(project_root)

        def ensure_pane(self):
            return True, '%3'

    class FakeReader:
        instances = 0

        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            FakeReader.instances += 1
            self.instance_id = FakeReader.instances
            self._calls = 0

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': session_path, 'msg_count': 0}

        def try_get_message(self, state):
            self._calls += 1
            if self.instance_id == 1:
                if int(state.get('msg_count', 0) or 0) >= 1:
                    return None, state
                return (
                    'partial stable',
                    {
                        **state,
                        'msg_count': 1,
                        'last_gemini_id': 'msg-1',
                        'mtime_ns': 111,
                    },
                )
            if self._calls == 1:
                return (
                    'partial stable expanded',
                    {
                        **state,
                        'msg_count': 1,
                        'last_gemini_id': 'msg-1',
                        'mtime_ns': 222,
                    },
                )
            return (
                'final stable reply',
                {
                    **state,
                    'msg_count': 1,
                    'last_gemini_id': 'msg-1',
                    'mtime_ns': 333,
                },
            )

    monkeypatch.setattr(gemini_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(gemini_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(gemini_adapter_module, 'GeminiLogReader', FakeReader)
    app1 = CcbdApp(project_root)
    _freeze_job_ids(app1, monkeypatch, fixed_req_id)
    thread1 = threading.Thread(target=app1.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread1.start()
    _wait_for_path(app1.paths.ccbd_socket_path)

    try:
        code, stdout, stderr = _run_phase2_local([], cwd=project_root, start_app=app1)
        assert code == 0, stderr

        code, stdout, stderr = _run_phase2_local(['ask', 'demo', 'from', 'user', 'resume gemini mutate'], cwd=project_root)
        assert code == 0, stderr
        job_id = _extract_accepted_job_id(stdout, target='demo')

        deadline = time.time() + 2.0
        while time.time() < deadline:
            events_path = project_root / '.ccb' / 'agents' / 'demo' / 'events.jsonl'
            if events_path.exists() and 'session_snapshot' in events_path.read_text(encoding='utf-8'):
                break
            time.sleep(0.05)
        else:
            raise AssertionError('expected session_snapshot before restart')

        running = _wait_for_phase2_status(project_root, 'demo', 'running')
        assert f'job_id: {job_id}' in running
        assert 'reply: partial stable' in running
        assert 'completion_reason: None' in running

        app1.request_shutdown()
        thread1.join(timeout=2)
        assert not thread1.is_alive()

        app2 = CcbdApp(project_root)
        thread2 = threading.Thread(target=app2.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        thread2.start()
        _wait_for_path(app2.paths.ccbd_socket_path)
        try:
            deadline = time.time() + 3.0
            last_stdout = ''
            while time.time() < deadline:
                code, pend, stderr = _run_phase2_local(['pend', 'demo'], cwd=project_root)
                assert code == 0, stderr
                last_stdout = pend
                if f'job_id: {job_id}' in pend and 'status: running' in pend and 'reply: final stable reply' in pend:
                    assert 'completion_reason: None' in pend
                    break
                time.sleep(0.05)
            else:
                raise AssertionError(f'expected running final preview after restart before settle completion; last={last_stdout!r}')

            pend = _wait_for_phase2_status(project_root, job_id, 'completed', timeout=6.0)
            assert 'reply: final stable reply' in pend
            assert 'completion_reason: session_reply_stable' in pend
            assert 'completion_confidence: observed' in pend
        finally:
            app2.request_shutdown()
            thread2.join(timeout=2)
            assert not thread2.is_alive()
    finally:
        if thread1.is_alive():
            app1.request_shutdown()
            thread1.join(timeout=2)
            assert not thread1.is_alive()


@pytest.mark.provider_blackbox
def test_ccb_gemini_real_adapter_recovers_after_restart_rotate_and_waits_for_new_session_mutation_settle(
    monkeypatch, tmp_path: Path
) -> None:
    from provider_execution import gemini as gemini_adapter_module

    fixed_req_id = 'job_6e1108'
    project_root = tmp_path / 'grrm'
    old_session_path = str(tmp_path / 'grrmo.json')
    new_session_path = str(tmp_path / 'grrmn.json')
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('gemini'))
    _write(
        project_root / '.ccb' / '.gemini-session',
        json.dumps(
            {
                'terminal': 'tmux',
                'pane_id': '%3',
                'work_dir': str(project_root),
                'gemini_session_id': 'gemini-session-id',
                'gemini_session_path': old_session_path,
            },
            ensure_ascii=False,
            indent=2,
        ) + '\n',
    )

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            self.sent = (pane_id, text)

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%3'

    class FakeSession:
        data = {}
        gemini_session_path = old_session_path
        gemini_session_id = 'gemini-session-id'
        work_dir = str(project_root)

        def ensure_pane(self):
            return True, '%3'

    class FakeReader:
        instances = 0

        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            FakeReader.instances += 1
            self.instance_id = FakeReader.instances
            self._calls = 0

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': old_session_path, 'msg_count': 0}

        def try_get_message(self, state):
            self._calls += 1
            if self.instance_id == 1:
                if int(state.get('msg_count', 0) or 0) >= 1:
                    return None, state
                return (
                    'old preview reply',
                    {
                        **state,
                        'session_path': old_session_path,
                        'msg_count': 1,
                        'last_gemini_id': 'msg-old',
                        'mtime_ns': 111,
                    },
                )
            if self._calls == 1:
                return (
                    None,
                    {
                        **state,
                        'session_path': new_session_path,
                        'msg_count': 0,
                        'last_gemini_id': None,
                        'mtime_ns': 222,
                    },
                )
            if self._calls == 2:
                return (
                    'new draft 1',
                    {
                        **state,
                        'session_path': new_session_path,
                        'msg_count': 1,
                        'last_gemini_id': 'msg-new',
                        'mtime_ns': 333,
                    },
                )
            if self._calls == 3:
                return (
                    'new draft 2',
                    {
                        **state,
                        'session_path': new_session_path,
                        'msg_count': 1,
                        'last_gemini_id': 'msg-new',
                        'mtime_ns': 444,
                    },
                )
            return (
                'new final stable reply',
                {
                    **state,
                    'session_path': new_session_path,
                    'msg_count': 1,
                    'last_gemini_id': 'msg-new',
                    'mtime_ns': 555,
                },
            )

    monkeypatch.setattr(gemini_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(gemini_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(gemini_adapter_module, 'GeminiLogReader', FakeReader)
    app1 = CcbdApp(project_root)
    _freeze_job_ids(app1, monkeypatch, fixed_req_id)
    thread1 = threading.Thread(target=app1.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread1.start()
    _wait_for_path(app1.paths.ccbd_socket_path)

    try:
        code, stdout, stderr = _run_phase2_local([], cwd=project_root, start_app=app1)
        assert code == 0, stderr

        code, stdout, stderr = _run_phase2_local(['ask', 'demo', 'from', 'user', 'resume gemini rotate mutate'], cwd=project_root)
        assert code == 0, stderr
        job_id = _extract_accepted_job_id(stdout, target='demo')

        deadline = time.time() + 2.0
        while time.time() < deadline:
            events_path = project_root / '.ccb' / 'agents' / 'demo' / 'events.jsonl'
            if events_path.exists() and 'session_snapshot' in events_path.read_text(encoding='utf-8'):
                break
            time.sleep(0.05)
        else:
            raise AssertionError('expected old-session snapshot before restart')

        running = _wait_for_phase2_status(project_root, 'demo', 'running')
        assert f'job_id: {job_id}' in running
        assert 'reply: old preview reply' in running
        assert 'completion_reason: None' in running

        app1.request_shutdown()
        thread1.join(timeout=2)
        assert not thread1.is_alive()

        app2 = CcbdApp(project_root)
        thread2 = threading.Thread(target=app2.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
        thread2.start()
        _wait_for_path(app2.paths.ccbd_socket_path)
        try:
            deadline = time.time() + 3.0
            last_stdout = ''
            while time.time() < deadline:
                code, pend, stderr = _run_phase2_local(['pend', 'demo'], cwd=project_root)
                assert code == 0, stderr
                last_stdout = pend
                if f'job_id: {job_id}' in pend and 'status: running' in pend and 'reply: old preview reply' not in pend:
                    break
                time.sleep(0.05)
            else:
                raise AssertionError(f'expected rotate to clear old preview after restart; last={last_stdout!r}')

            deadline = time.time() + 3.0
            last_stdout = ''
            while time.time() < deadline:
                code, pend, stderr = _run_phase2_local(['pend', 'demo'], cwd=project_root)
                assert code == 0, stderr
                last_stdout = pend
                if f'job_id: {job_id}' in pend and 'status: running' in pend and 'reply: new final stable reply' in pend:
                    assert 'completion_reason: None' in pend
                    break
                time.sleep(0.05)
            else:
                raise AssertionError(f'expected running new-session final preview before settle completion; last={last_stdout!r}')

            pend = _wait_for_phase2_status(project_root, job_id, 'completed', timeout=6.0)
            assert 'reply: old preview reply' not in pend
            assert 'reply: new final stable reply' in pend
            assert 'completion_reason: session_reply_stable' in pend
            assert 'completion_confidence: observed' in pend
        finally:
            app2.request_shutdown()
            thread2.join(timeout=2)
            assert not thread2.is_alive()
    finally:
        if thread1.is_alive():
            app1.request_shutdown()
            thread1.join(timeout=2)
            assert not thread1.is_alive()
