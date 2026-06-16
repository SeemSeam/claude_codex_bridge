from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


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


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def test_ccb_config_validate_success(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    _write(project_root / '.ccb' / 'ccb.config', 'cmd; agent1:codex\n')

    proc = _run_ccb(['config', 'validate'], cwd=project_root)
    assert proc.returncode == 0
    assert 'config_status: valid' in proc.stdout
    assert 'config_source_kind: project_config' in proc.stdout
    assert 'used_builtin_default: false' in proc.stdout
    assert 'default_agents: agent1' in proc.stdout
    assert 'agents: agent1' in proc.stdout
    assert 'cmd_enabled: true' in proc.stdout
    assert 'layout: cmd; agent1:codex' in proc.stdout


def test_ccb_config_validate_rejects_provider_only_list(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    _write(project_root / '.ccb' / 'ccb.config', 'codex,gemini,claude,cmd\n')

    proc = _run_ccb(['config', 'validate'], cwd=project_root)

    assert proc.returncode == 1
    assert 'config_status: invalid' in proc.stderr
    assert 'expected' in proc.stderr


def test_ccb_config_validate_reports_user_config_source_kind(tmp_path: Path) -> None:
    home = tmp_path / 'home'
    project_root = tmp_path / 'repo'
    (project_root / '.ccb').mkdir(parents=True)
    _write(home / '.ccb' / 'ccb.config', 'cmd; userdefault:claude\n')

    env = dict(os.environ)
    env['HOME'] = str(home)
    proc = subprocess.run(
        [sys.executable, str(_repo_root() / 'ccb.py'), 'config', 'validate'],
        cwd=str(project_root),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )

    assert proc.returncode == 0
    assert 'config_source_kind: user_config' in proc.stdout
    assert f'config_source: {home / ".ccb" / "ccb.config"}' in proc.stdout
    assert 'used_builtin_default: false' in proc.stdout
    assert 'default_agents: userdefault' in proc.stdout


def test_ccb_config_validate_accepts_named_simple_agent_map(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    _write(project_root / '.ccb' / 'ccb.config', 'cmd, agent1:codex; agent2:codex, agent3:claude\n')

    proc = _run_ccb(['config', 'validate'], cwd=project_root)

    assert proc.returncode == 0
    assert 'config_status: valid' in proc.stdout
    assert 'default_agents: agent1, agent2, agent3' in proc.stdout
    assert 'agents: agent1, agent2, agent3' in proc.stdout
    assert 'cmd_enabled: true' in proc.stdout
    assert 'layout: cmd, agent1:codex; agent2:codex, agent3:claude' in proc.stdout


def test_ccb_config_validate_reports_invalid_token(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    _write(project_root / '.ccb' / 'ccb.config', 'agent1=codex\n')

    proc = _run_ccb(['config', 'validate'], cwd=project_root)
    assert proc.returncode == 1
    assert 'config_status: invalid' in proc.stderr
    assert 'invalid TOML config' in proc.stderr or 'rich TOML config requires Python 3.11+' in proc.stderr


def test_ccb_config_validate_rejects_cmd_agent_name(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    _write(project_root / '.ccb' / 'ccb.config', 'cmd:codex\n')

    proc = _run_ccb(['config', 'validate'], cwd=project_root)
    assert proc.returncode == 1
    assert 'config_status: invalid' in proc.stderr
    assert 'reserved token' in proc.stderr


def test_ccb_config_validate_requires_project_resolution(tmp_path: Path) -> None:
    proc = _run_ccb(['config', 'validate'], cwd=tmp_path)
    assert proc.returncode == 2
    assert 'cannot resolve project' in proc.stderr


def test_ccb_config_validate_supports_explicit_project(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    _write(project_root / '.ccb' / 'ccb.config', 'cmd; agent1:claude\n')

    proc = _run_ccb(['--project', str(project_root), 'config', 'validate'], cwd=elsewhere)
    assert proc.returncode == 0
    assert f'project: {project_root.resolve()}' in proc.stdout
