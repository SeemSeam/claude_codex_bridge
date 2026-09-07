from __future__ import annotations

import json
from pathlib import Path

import pytest

from provider_backends.claude.comm import ClaudeCommunicator
from provider_backends.claude.registry import ClaudeSessionRegistry
from provider_backends.claude.resolver import resolve_claude_session
from provider_backends.claude.session import ClaudeProjectSession
from project.identity import normalize_work_dir


def test_claude_session_start_cmd_rehydrates_proxy_auth_for_pane_recovery(tmp_path: Path) -> None:
    runtime_dir = tmp_path / 'runtime'
    runtime_dir.mkdir()
    (runtime_dir / 'provider-profile.json').write_text(
        json.dumps(
            {
                'provider': 'claude',
                'agent_name': 'advisor',
                'mode': 'inherit',
                'env': {
                    'ANTHROPIC_AUTH_TOKEN': 'proxy-token',
                    'ANTHROPIC_BASE_URL': 'https://proxy.example.test',
                },
                'inherit_api': True,
                'inherit_auth': True,
                'inherit_config': True,
                'inherit_skills': True,
                'inherit_commands': True,
                'inherit_memory': True,
            }
        ),
        encoding='utf-8',
    )
    session = ClaudeProjectSession(
        session_file=tmp_path / '.claude-session',
        data={
            'runtime_dir': str(runtime_dir),
            'work_dir': str(tmp_path),
            # Persisted recovery command keeps the proxy URL but drops auth.
            'start_cmd': (
                'export ANTHROPIC_BASE_URL=https://proxy.example.test; '
                'claude --continue'
            ),
        },
    )

    start_cmd = session.start_cmd

    assert 'ANTHROPIC_AUTH_TOKEN=proxy-token' in start_cmd
    assert 'ANTHROPIC_BASE_URL=https://proxy.example.test' in start_cmd
    # Disk payload stays redacted; only the respawn view is rehydrated.
    assert 'ANTHROPIC_AUTH_TOKEN=' not in session.data['start_cmd']


def test_claude_missing_session_recovery_drops_only_managed_continue_binding(tmp_path: Path) -> None:
    session_file = tmp_path / '.claude-session'
    session = ClaudeProjectSession(
        session_file=session_file,
        data={
            'work_dir': str(tmp_path),
            'start_cmd': (
                'export ANTHROPIC_BASE_URL=https://example.invalid; '
                'claude --continue --setting-sources user,project,local'
            ),
            'claude_start_cmd': (
                'export ANTHROPIC_BASE_URL=https://example.invalid; '
                'claude --continue --setting-sources user,project,local'
            ),
            'claude_session_id': 'missing-session',
            'claude_session_path': str(tmp_path / 'missing-session.jsonl'),
        },
    )

    result = session.prepare_crash_recovery('provider_session_missing')

    assert result is not None and result[0] is True
    persisted = json.loads(session_file.read_text(encoding='utf-8'))
    assert '--continue' not in persisted['start_cmd']
    assert 'ANTHROPIC_BASE_URL=https://example.invalid' in persisted['start_cmd']
    assert persisted['claude_session_id'] == ''
    assert persisted['claude_session_path'] == ''


def test_claude_missing_session_recovery_fails_closed_without_managed_continue(tmp_path: Path) -> None:
    session = ClaudeProjectSession(
        session_file=tmp_path / '.claude-session',
        data={'work_dir': str(tmp_path), 'start_cmd': 'claude --verbose'},
    )

    result = session.prepare_crash_recovery('provider_session_missing')

    assert result is not None and result[0] is False
    assert 'no CCB-owned --continue' in result[1]


def test_claude_session_update_backfills_work_dir_fields(tmp_path: Path) -> None:
    cfg = tmp_path / ".ccb"
    cfg.mkdir(parents=True, exist_ok=True)
    session_file = cfg / ".claude-session"
    session_file.write_text("{}", encoding="utf-8")

    session = ClaudeProjectSession(
        session_file=session_file,
        data={
            "claude_session_id": "old-id",
            "claude_session_path": str(tmp_path / "old.jsonl"),
        },
    )
    session.update_claude_binding(session_path=tmp_path / "new-id.jsonl", session_id="new-id")

    data = json.loads(session_file.read_text(encoding="utf-8"))
    assert data["work_dir"] == str(tmp_path)
    assert data["work_dir_norm"] == normalize_work_dir(str(tmp_path))


def test_claude_session_update_binds_new_fork_to_current_authority(tmp_path: Path) -> None:
    session_file = tmp_path / '.ccb' / '.claude-session'
    session_file.parent.mkdir(parents=True)
    session_file.write_text('{}', encoding='utf-8')
    session = ClaudeProjectSession(
        session_file=session_file,
        data={
            'claude_provider_authority_fingerprint': 'authority-b',
            'ccb_resume_compatibility': 'linked_continuation',
            'ccb_continuation_launch_mode': 'fork',
        },
    )
    session_path = tmp_path / 'managed-home' / '.claude' / 'projects' / 'workspace' / 'native-b.jsonl'
    session_path.parent.mkdir(parents=True)
    session_path.write_text('{}\n', encoding='utf-8')

    session.update_claude_binding(session_path=session_path, session_id='native-b')

    data = json.loads(session_file.read_text(encoding='utf-8'))
    assert data['claude_session_authority_fingerprint'] == 'authority-b'
    assert data['ccb_resume_compatibility'] == 'native_fork_continuation'


def test_registry_direct_update_backfills_work_dir_fields(tmp_path: Path) -> None:
    cfg = tmp_path / ".ccb"
    cfg.mkdir(parents=True, exist_ok=True)
    session_file = cfg / ".claude-session"
    session_file.write_text(json.dumps({"active": True}), encoding="utf-8")

    log_path = tmp_path / "new-id.jsonl"
    log_path.write_text("", encoding="utf-8")

    registry = ClaudeSessionRegistry()
    registry._update_session_file_direct(session_file, log_path, "new-id")

    data = json.loads(session_file.read_text(encoding="utf-8"))
    assert data["claude_session_id"] == "new-id"
    assert data["claude_session_path"] == str(log_path)
    assert data["work_dir"] == str(tmp_path)
    assert data["work_dir_norm"] == normalize_work_dir(str(tmp_path))


def test_claude_comm_remember_backfills_work_dir_fields(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = tmp_path / ".ccb"
    cfg.mkdir(parents=True, exist_ok=True)
    session_file = cfg / ".claude-session"
    session_file.write_text("{}", encoding="utf-8")

    log_path = tmp_path / "new-id.jsonl"
    log_path.write_text("", encoding="utf-8")

    comm = ClaudeCommunicator.__new__(ClaudeCommunicator)
    comm.project_session_file = str(session_file)
    comm.session_info = {"work_dir": str(tmp_path)}
    comm.session_id = "ccb-session-id"
    comm.terminal = "tmux"
    monkeypatch.setattr(ClaudeCommunicator, "_publish_registry", lambda self: None)

    comm._remember_claude_session(log_path)

    data = json.loads(session_file.read_text(encoding="utf-8"))
    assert data["claude_session_path"] == str(log_path)
    assert data["claude_session_id"] == "new-id"
    assert data["work_dir"] == str(tmp_path)
    assert data["work_dir_norm"] == normalize_work_dir(str(tmp_path))


def test_load_project_session_migrates_legacy_setting_sources(tmp_path: Path) -> None:
    cfg = tmp_path / ".ccb"
    cfg.mkdir(parents=True, exist_ok=True)
    session_file = cfg / ".claude-session"
    session_file.write_text(
        json.dumps(
            {
                "active": True,
                "work_dir": str(tmp_path),
                "start_cmd": (
                    f"export ANTHROPIC_BASE_URL=https://example.invalid; claude --setting-sources "
                    f"project,local --settings {tmp_path / 'overlay.json'}"
                ),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    from provider_backends.claude.session import load_project_session

    loaded = load_project_session(tmp_path)

    assert loaded is not None
    assert "--setting-sources user,project,local" in loaded.data["start_cmd"]

    persisted = json.loads(session_file.read_text(encoding="utf-8"))
    assert "--setting-sources user,project,local" in persisted["start_cmd"]


def test_load_project_session_instance_migrates_legacy_setting_sources(tmp_path: Path) -> None:
    cfg = tmp_path / ".ccb"
    cfg.mkdir(parents=True, exist_ok=True)
    session_file = cfg / ".claude-agent3-session"
    session_file.write_text(
        json.dumps(
            {
                "active": True,
                "work_dir": str(tmp_path),
                "start_cmd": "claude --setting-sources project,local --settings /tmp/overlay.json",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    from provider_backends.claude.session import load_project_session

    loaded = load_project_session(tmp_path, "agent3")

    assert loaded is not None
    assert "--setting-sources user,project,local" in loaded.data["start_cmd"]

    persisted = json.loads(session_file.read_text(encoding="utf-8"))
    assert "--setting-sources user,project,local" in persisted["start_cmd"]



def test_load_project_session_instance_does_not_fallback_to_primary_session(tmp_path: Path) -> None:
    cfg = tmp_path / '.ccb'
    cfg.mkdir(parents=True, exist_ok=True)
    (cfg / '.claude-session').write_text(
        json.dumps(
            {
                'active': True,
                'work_dir': str(tmp_path),
                'start_cmd': 'claude --continue',
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    from provider_backends.claude.session import load_project_session

    loaded = load_project_session(tmp_path, 'agent3')

    assert loaded is None


def test_load_project_session_exposes_claude_home_fields(tmp_path: Path) -> None:
    cfg = tmp_path / '.ccb'
    cfg.mkdir(parents=True, exist_ok=True)
    claude_home = tmp_path / '.ccb' / 'agents' / 'agent1' / 'provider-state' / 'claude' / 'home'
    session_file = cfg / '.claude-agent1-session'
    session_file.write_text(
        json.dumps(
            {
                'active': True,
                'work_dir': str(tmp_path),
                'claude_home': str(claude_home),
                'claude_projects_root': str(claude_home / '.claude' / 'projects'),
                'claude_session_env_root': str(claude_home / '.claude' / 'session-env'),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    from provider_backends.claude.session import load_project_session

    loaded = load_project_session(tmp_path, 'agent1')

    assert loaded is not None
    assert loaded.claude_home == str(claude_home)
    assert loaded.claude_projects_root == str(claude_home / '.claude' / 'projects')


def test_resolve_claude_session_uses_explicit_ccb_session_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    project_root = tmp_path / "project"
    project_ccb = project_root / ".ccb"
    project_ccb.mkdir(parents=True, exist_ok=True)
    session_file = project_ccb / ".claude-session"
    log_path = tmp_path / "logs" / "claude-session.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")
    session_file.write_text(
        json.dumps(
            {
                "active": True,
                "claude_session_id": "claude-session-id",
                "claude_session_path": str(log_path),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CCB_SESSION_FILE", str(session_file))

    outside = tmp_path / "outside"
    outside.mkdir()
    resolution = resolve_claude_session(outside)

    assert resolution is not None
    assert resolution.session_file == session_file
    assert resolution.source == "session_file"
    assert resolution.registry is None
    assert resolution.data["work_dir"] == str(project_root)


def test_resolve_claude_session_accepts_named_ccb_session_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / ".ccb-workspace.json").write_text(
        json.dumps(
            {
                "schema_version": 2,
                "record_type": "workspace_binding",
                "target_project": str(tmp_path / "project"),
                "project_id": "demo-project",
                "agent_name": "agent3",
                "workspace_mode": "linked",
                "workspace_path": str(workspace),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    project_ccb = tmp_path / "project" / ".ccb"
    project_ccb.mkdir(parents=True, exist_ok=True)
    session_file = project_ccb / ".claude-agent3-session"
    session_file.write_text(
        json.dumps(
            {
                "active": True,
                "work_dir": str(workspace),
                "claude_session_id": "claude-agent3-id",
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    monkeypatch.chdir(workspace)

    resolution = resolve_claude_session(workspace)

    assert resolution is not None
    assert resolution.session_file == session_file
    assert resolution.data["work_dir"] == str(workspace)


def test_resolve_claude_session_without_project_binding_returns_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("CCB_SESSION_ID", "legacy-ccb-session")
    monkeypatch.setenv("TMUX_PANE", "%99")

    outside = tmp_path / "outside"
    outside.mkdir()

    assert resolve_claude_session(outside) is None
