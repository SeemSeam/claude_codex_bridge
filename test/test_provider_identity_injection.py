from __future__ import annotations

from pathlib import Path

import pytest

from agents.models import AgentSpec, PermissionMode, QueuePolicy, RestoreMode, RuntimeMode, WorkspaceMode
from cli.models import ParsedStartCommand


def _spec(
    name: str,
    provider: str,
    *,
    restore_default: RestoreMode = RestoreMode.AUTO,
    role: str | None = None,
) -> AgentSpec:
    return AgentSpec(
        name=name,
        provider=provider,
        target='.',
        workspace_mode=WorkspaceMode.GIT_WORKTREE,
        workspace_root=None,
        runtime_mode=RuntimeMode.PANE_BACKED,
        restore_default=restore_default,
        permission_default=PermissionMode.MANUAL,
        queue_policy=QueuePolicy.SERIAL_PER_AGENT,
        role=role,
    )


def _prepared(runtime_dir: Path, *, workspace_path: Path | None = None) -> dict[str, object]:
    runtime = Path(runtime_dir)
    project_root = runtime.parent
    for parent in runtime.parents:
        if parent.name == '.ccb':
            project_root = parent.parent
            break
    payload: dict[str, object] = {'project_root': str(project_root)}
    if workspace_path is not None:
        payload['workspace_path'] = str(workspace_path)
    return payload


def test_claude_build_start_cmd_includes_identity_prompt(monkeypatch, tmp_path: Path) -> None:
    from provider_backends import claude as claude_backend

    project_root = tmp_path / 'repo'
    runtime_dir = project_root / '.ccb' / 'agents' / 'reviewer' / 'provider-runtime' / 'claude'
    runtime_dir.mkdir(parents=True)
    monkeypatch.setattr(claude_backend.launcher.Path, 'home', lambda: tmp_path / 'home')

    cmd = claude_backend.launcher.build_start_cmd(
        ParsedStartCommand(project=None, agent_names=('reviewer',), restore=False, auto_permission=False),
        _spec('reviewer', 'claude', role='ccb.reviewer'),
        runtime_dir,
        'launch-1',
        prepared_state=_prepared(runtime_dir),
    )

    assert '--append-system-prompt' in cmd
    assert 'You are agent reviewer' in cmd
    assert 'provider=claude' in cmd
    assert 'role=ccb.reviewer' in cmd
    assert 'window=reviewer' in cmd
    assert 'Do not claim to be any other agent' in cmd


def test_codex_build_start_cmd_materializes_identity_agents_md(tmp_path: Path) -> None:
    from provider_backends import codex as codex_backend
    from provider_backends.codex.launcher_runtime.session_paths import state_dir_for_runtime_dir

    project_root = tmp_path / 'repo'
    runtime_dir = project_root / '.ccb' / 'agents' / 'worker' / 'provider-runtime' / 'codex'
    runtime_dir.mkdir(parents=True)

    cmd = codex_backend.launcher.build_start_cmd(
        ParsedStartCommand(project=None, agent_names=('worker',), restore=False, auto_permission=False),
        _spec('worker', 'codex', role='ccb.worker'),
        runtime_dir,
        'launch-1',
        prepared_state=_prepared(runtime_dir),
    )

    # Codex CLI has no -c developer_instructions=... key; identity must live in
    # the isolated $CODEX_HOME/AGENTS.md file.
    assert 'developer_instructions=' not in cmd
    codex_home = state_dir_for_runtime_dir(runtime_dir) / 'home'
    agents_md = codex_home / 'AGENTS.md'
    assert agents_md.is_file()
    content = agents_md.read_text(encoding='utf-8')
    assert '# CCB injected identity' in content
    assert 'You are agent worker' in content
    assert 'provider=codex' in content
    assert 'role=ccb.worker' in content
    assert 'window=worker' in content
    assert 'Do not claim to be any other agent' in content


def test_kimi_build_start_cmd_materializes_identity_agents_md(tmp_path: Path) -> None:
    from provider_backends import kimi as kimi_backend

    workspace_path = tmp_path / 'ws'
    workspace_path.mkdir(parents=True)
    runtime_dir = tmp_path / 'runtime'
    runtime_dir.mkdir(parents=True)

    cmd = kimi_backend.launcher.build_start_cmd(
        ParsedStartCommand(project=None, agent_names=('worker',), restore=False, auto_permission=False),
        _spec('worker', 'kimi', role='ccb.worker'),
        runtime_dir,
        'launch-1',
        prepared_state=_prepared(runtime_dir, workspace_path=workspace_path),
    )

    # Kimi Code CLI has no CLI system-prompt flag; identity is delivered via the
    # workspace AGENTS.md discovered by ${KIMI_AGENTS_MD}.
    assert '--append-system-prompt' not in cmd
    assert 'developer_instructions=' not in cmd
    agents_md = workspace_path / 'AGENTS.md'
    assert agents_md.is_file()
    content = agents_md.read_text(encoding='utf-8')
    assert '# CCB injected identity' in content
    assert 'You are agent worker' in content
    assert 'provider=kimi' in content
    assert 'role=ccb.worker' in content
    assert 'window=worker' in content
    assert 'Do not claim to be any other agent' in content


def test_agy_build_start_cmd_injects_identity_for_claude_provider(monkeypatch, tmp_path: Path) -> None:
    from provider_backends import agy as agy_backend

    project_root = tmp_path / 'repo'
    runtime_dir = project_root / '.ccb' / 'agents' / 'spy' / 'provider-runtime' / 'agy'
    runtime_dir.mkdir(parents=True)
    monkeypatch.setattr(agy_backend.launcher, '_resolve_managed_home', lambda rd: Path(rd) / 'home')
    monkeypatch.setattr(agy_backend.launcher, '_resolve_credential_source_home', lambda: tmp_path / 'agy-src')
    monkeypatch.setenv('AGY_START_CMD', 'agy')

    cmd = agy_backend.launcher.build_start_cmd(
        ParsedStartCommand(project=None, agent_names=('spy',), restore=False, auto_permission=False),
        _spec('spy', 'claude', role='ccb.spy'),
        runtime_dir,
        'launch-1',
        prepared_state=_prepared(runtime_dir),
    )

    assert '--append-system-prompt' in cmd
    assert 'You are agent spy' in cmd
    assert 'provider=claude' in cmd
    assert 'role=ccb.spy' in cmd


def test_agy_build_start_cmd_omits_invalid_developer_instructions_for_codex_provider(monkeypatch, tmp_path: Path) -> None:
    from provider_backends import agy as agy_backend

    project_root = tmp_path / 'repo'
    runtime_dir = project_root / '.ccb' / 'agents' / 'spy' / 'provider-runtime' / 'agy'
    runtime_dir.mkdir(parents=True)
    monkeypatch.setattr(agy_backend.launcher, '_resolve_managed_home', lambda rd: Path(rd) / 'home')
    monkeypatch.setattr(agy_backend.launcher, '_resolve_credential_source_home', lambda: tmp_path / 'agy-src')
    monkeypatch.setenv('AGY_START_CMD', 'agy')

    cmd = agy_backend.launcher.build_start_cmd(
        ParsedStartCommand(project=None, agent_names=('spy',), restore=False, auto_permission=False),
        _spec('spy', 'codex', role='ccb.spy'),
        runtime_dir,
        'launch-1',
        prepared_state=_prepared(runtime_dir),
    )

    assert 'developer_instructions=' not in cmd
    assert '--append-system-prompt' not in cmd


def test_agy_build_start_cmd_omits_identity_for_native_agy_provider(monkeypatch, tmp_path: Path) -> None:
    from provider_backends import agy as agy_backend

    project_root = tmp_path / 'repo'
    runtime_dir = project_root / '.ccb' / 'agents' / 'native' / 'provider-runtime' / 'agy'
    runtime_dir.mkdir(parents=True)
    monkeypatch.setattr(agy_backend.launcher, '_resolve_managed_home', lambda rd: Path(rd) / 'home')
    monkeypatch.setattr(agy_backend.launcher, '_resolve_credential_source_home', lambda: tmp_path / 'agy-src')
    monkeypatch.setenv('AGY_START_CMD', 'agy')

    cmd = agy_backend.launcher.build_start_cmd(
        ParsedStartCommand(project=None, agent_names=('native',), restore=False, auto_permission=False),
        _spec('native', 'agy'),
        runtime_dir,
        'launch-1',
        prepared_state=_prepared(runtime_dir),
    )

    assert '--append-system-prompt' not in cmd
    assert 'developer_instructions=' not in cmd


@pytest.mark.parametrize(
    ('restore_default', 'expect_continue'),
    (
        (RestoreMode.FRESH, False),
        (RestoreMode.AUTO, True),
        (RestoreMode.PROVIDER, True),
    ),
)
def test_agy_build_start_cmd_restore_fresh_omits_continue(
    monkeypatch,
    tmp_path: Path,
    restore_default: RestoreMode,
    expect_continue: bool,
) -> None:
    from provider_backends import agy as agy_backend

    project_root = tmp_path / 'repo'
    runtime_dir = project_root / '.ccb' / 'agents' / 'native' / 'provider-runtime' / 'agy'
    runtime_dir.mkdir(parents=True)
    monkeypatch.setattr(agy_backend.launcher, '_resolve_managed_home', lambda rd: Path(rd) / 'home')
    monkeypatch.setattr(agy_backend.launcher, '_resolve_credential_source_home', lambda: tmp_path / 'agy-src')
    monkeypatch.setenv('AGY_START_CMD', 'agy')

    cmd = agy_backend.launcher.build_start_cmd(
        ParsedStartCommand(project=None, agent_names=('native',), restore=True, auto_permission=False),
        _spec('native', 'agy', restore_default=restore_default),
        runtime_dir,
        'launch-1',
        prepared_state=_prepared(runtime_dir),
    )

    has_continue = '--continue' in cmd or '--conversation' in cmd
    if expect_continue:
        assert has_continue, f'restore_default={restore_default.value} should produce --continue'
    else:
        assert not has_continue, f'restore_default={restore_default.value} must not produce --continue/--conversation'
