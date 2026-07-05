from __future__ import annotations

from io import StringIO
from pathlib import Path
from types import SimpleNamespace

import pytest

from cli.parser import CliParser, CliUsageError


def _fake_context(tmp_path: Path):
    return SimpleNamespace(
        project=SimpleNamespace(project_root=tmp_path, project_id='proj-1'),
        paths=SimpleNamespace(ccb_dir=tmp_path / '.ccb'),
    )


def test_parse_identity_command() -> None:
    command = CliParser().parse(['identity'])
    assert command.kind == 'identity'
    assert command.json_output is False


def test_parse_identity_json_flag() -> None:
    command = CliParser().parse(['identity', '--json'])
    assert command.kind == 'identity'
    assert command.json_output is True


def test_parse_probe_command() -> None:
    command = CliParser().parse(['probe'])
    assert command.kind == 'probe'
    assert command.json_output is False


def test_parse_probe_json_flag() -> None:
    command = CliParser().parse(['probe', '--json'])
    assert command.kind == 'probe'
    assert command.json_output is True


def test_parse_doctor_identity_flag() -> None:
    command = CliParser().parse(['doctor', '--identity'])
    assert command.kind == 'doctor'
    assert command.identity is True
    assert command.deep is False


def test_parse_doctor_deep_flag() -> None:
    command = CliParser().parse(['doctor', '--deep'])
    assert command.kind == 'doctor'
    assert command.identity is False
    assert command.deep is True


def test_parse_doctor_identity_and_json_flags() -> None:
    command = CliParser().parse(['doctor', '--identity', '--json'])
    assert command.kind == 'doctor'
    assert command.identity is True
    assert command.json_output is True


def test_identity_command_renders_summary(monkeypatch, tmp_path: Path) -> None:
    from cli import phase2 as phase2_module

    fake_context = _fake_context(tmp_path)
    monkeypatch.setattr(phase2_module, '_build_context', lambda command, cwd, out: fake_context)
    monkeypatch.setattr(phase2_module, 'ensure_bootstrap_project_config', lambda project_root: None)
    monkeypatch.setattr(
        phase2_module,
        'identity_summary',
        lambda context: {
            'user_id': 1000,
            'user_name': 'demo',
            'home': '/home/demo',
            'root_runtime': False,
            'install_root_owned': False,
            'install_user_id': 1000,
            'install_user_name': 'demo',
            'sudo_user': None,
            'project_owner': '1000:demo',
            'ccb_dir_owner': '1000:demo',
            'install_owner': '1000:demo',
            'warnings': (),
        },
    )

    stdout = StringIO()
    stderr = StringIO()
    code = phase2_module.maybe_handle_phase2(
        ['identity'],
        cwd=tmp_path,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    output = stdout.getvalue()
    assert 'user_id: 1000' in output
    assert 'user_name: demo' in output
    assert stderr.getvalue() == ''


def test_identity_command_json_output(monkeypatch, tmp_path: Path) -> None:
    from cli import phase2 as phase2_module

    fake_context = _fake_context(tmp_path)
    monkeypatch.setattr(phase2_module, '_build_context', lambda command, cwd, out: fake_context)
    monkeypatch.setattr(phase2_module, 'ensure_bootstrap_project_config', lambda project_root: None)
    monkeypatch.setattr(
        phase2_module,
        'identity_summary',
        lambda context: {'user_id': 1000, 'user_name': 'demo'},
    )

    stdout = StringIO()
    stderr = StringIO()
    code = phase2_module.maybe_handle_phase2(
        ['identity', '--json'],
        cwd=tmp_path,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    assert '"user_id": 1000' in stdout.getvalue()
    assert '"user_name": "demo"' in stdout.getvalue()


def test_probe_command_renders_summary(monkeypatch, tmp_path: Path) -> None:
    from cli import phase2 as phase2_module

    fake_context = _fake_context(tmp_path)
    monkeypatch.setattr(phase2_module, '_build_context', lambda command, cwd, out: fake_context)
    monkeypatch.setattr(phase2_module, 'ensure_bootstrap_project_config', lambda project_root: None)
    monkeypatch.setattr(
        phase2_module,
        'probe_summary',
        lambda context: {
            'project_id': 'proj-1',
            'ccbd': {'state': 'mounted', 'health': 'ok'},
            'agents': [{'agent_name': 'worker', 'status': {'state': 'running', 'health': 'ok'}}],
            'requirements': {'provider_commands': ()},
        },
    )

    stdout = StringIO()
    stderr = StringIO()
    code = phase2_module.maybe_handle_phase2(
        ['probe'],
        cwd=tmp_path,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    output = stdout.getvalue()
    assert 'probe_project_id: proj-1' in output
    assert 'probe_agent: name=worker' in output


def test_doctor_identity_flag_renders_identity(monkeypatch, tmp_path: Path) -> None:
    from cli import phase2 as phase2_module

    fake_context = _fake_context(tmp_path)
    monkeypatch.setattr(phase2_module, '_build_context', lambda command, cwd, out: fake_context)
    monkeypatch.setattr(phase2_module, 'ensure_bootstrap_project_config', lambda project_root: None)
    monkeypatch.setattr(
        phase2_module,
        'identity_summary',
        lambda context: {'user_id': 1000, 'user_name': 'demo'},
    )

    stdout = StringIO()
    stderr = StringIO()
    code = phase2_module.maybe_handle_phase2(
        ['doctor', '--identity'],
        cwd=tmp_path,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    output = stdout.getvalue()
    assert 'user_id: 1000' in output
    assert 'user_name: demo' in output


def test_doctor_deep_flag_renders_doctor_and_probe(monkeypatch, tmp_path: Path) -> None:
    from cli import phase2 as phase2_module

    fake_context = _fake_context(tmp_path)
    monkeypatch.setattr(phase2_module, '_build_context', lambda command, cwd, out: fake_context)
    monkeypatch.setattr(phase2_module, 'ensure_bootstrap_project_config', lambda project_root: None)
    monkeypatch.setattr(
        phase2_module,
        'doctor_summary',
        lambda context: {
            'project': str(tmp_path),
            'project_id': 'proj-1',
            'installation': {},
            'entrypoint': {},
            'runtime': {},
            'requirements': {},
            'ccbd': {
                'state': 'mounted',
                'health': 'ok',
                'generation': 1,
                'pid_alive': True,
                'socket_connectable': True,
                'heartbeat_fresh': True,
                'takeover_allowed': True,
                'reason': 'ok',
                'last_heartbeat_at': None,
                'active_execution_count': 0,
                'recoverable_execution_count': 0,
                'nonrecoverable_execution_count': 0,
                'pending_items_count': 0,
                'terminal_pending_count': 0,
                'recoverable_execution_providers': [],
                'nonrecoverable_execution_providers': [],
                'diagnostic_errors': (),
            },
            'agents': [],
        },
    )
    monkeypatch.setattr(
        phase2_module,
        'probe_summary',
        lambda context: {
            'project_id': 'proj-1',
            'ccbd': {'state': 'mounted', 'health': 'ok'},
            'agents': [{'agent_name': 'worker', 'status': {'state': 'running', 'health': 'ok'}}],
            'requirements': {'provider_commands': ()},
        },
    )

    stdout = StringIO()
    stderr = StringIO()
    code = phase2_module.maybe_handle_phase2(
        ['doctor', '--deep'],
        cwd=tmp_path,
        stdout=stdout,
        stderr=stderr,
    )

    assert code == 0
    output = stdout.getvalue()
    assert 'ccbd_state: mounted' in output
    assert 'probe_project_id: proj-1' in output
    assert 'probe_agent: name=worker' in output
