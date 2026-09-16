from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.config_loader import load_project_config
from agents.models import AgentRestoreState, RestoreMode, WorkspaceMode
from ccbd.services.project_namespace_pane import ProjectNamespacePaneRecord
from ccbd.start_preparation import prepare_start_agents
from cli.context import CliContextBuilder
from cli.models import ParsedStartCommand
from cli.services.role_command_policy import RoleCommandPolicy
from provider_backends.claude import launcher as claude_launcher
from provider_backends.runtime_restore import ProviderRestoreTarget
from project.resolver import bootstrap_project
from storage.paths import PathLayout


pytestmark = pytest.mark.usefixtures('stub_claude_private_keychain')


def _single_codex_project(tmp_path: Path, name: str):
    project_root = tmp_path / name
    (project_root / '.ccb').mkdir(parents=True)
    (project_root / '.ccb' / 'ccb.config').write_text('agent1:codex\n', encoding='utf-8')
    bootstrap_project(project_root)
    command = ParsedStartCommand(project=None, agent_names=('agent1',), restore=True, auto_permission=False)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    return project_root, context, load_project_config(project_root).config, PathLayout(project_root)


def test_prepare_start_agents_rejects_git_worktree_for_non_git_project(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-start-prep-non-git-worktree'
    (project_root / '.ccb').mkdir(parents=True)
    (project_root / 'README.md').write_text('not a git repo\n', encoding='utf-8')
    (project_root / '.ccb' / 'ccb.config').write_text('agent1:codex(worktree)\n', encoding='utf-8')
    bootstrap_project(project_root)
    command = ParsedStartCommand(project=None, agent_names=(), restore=True, auto_permission=False)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    config = load_project_config(project_root).config
    paths = PathLayout(project_root)

    with pytest.raises(RuntimeError, match='git-worktree workspace requires a git repository'):
        prepare_start_agents(
            targets=('agent1',),
            config=config,
            paths=paths,
            context=context,
            project_root=project_root,
            project_id=context.project.project_id,
            tmux_socket_path=None,
            tmux_session_name=None,
            workspace_window_id=None,
            resolve_agent_binding_fn=lambda **kwargs: None,
            project_binding_filter_fn=lambda binding, **kwargs: binding,
            restore_state_builder=lambda restore_mode: {'restore_mode': restore_mode},
        )

    assert paths.workspace_path('agent1').exists() is False


def test_prepare_start_agents_skips_provider_preparation_for_reused_binding(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root, context, config, paths = _single_codex_project(tmp_path, 'repo-start-prep-reuse')
    binding = SimpleNamespace(runtime_ref='tmux:%1')
    calls: list[str] = []
    monkeypatch.setattr(
        'ccbd.start_preparation.prepare_provider_workspace',
        lambda **kwargs: calls.append(kwargs['agent_name']),
    )
    monkeypatch.setattr(
        'ccbd.start_preparation.effective_start_command',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError('reused bindings must not resolve a launch command')
        ),
    )

    prepared = prepare_start_agents(
        targets=('agent1',),
        config=config,
        paths=paths,
        context=context,
        project_root=project_root,
        project_id=context.project.project_id,
        tmux_socket_path=None,
        tmux_session_name=None,
        workspace_window_id=None,
        resolve_agent_binding_fn=lambda **kwargs: binding,
        project_binding_filter_fn=lambda candidate, **kwargs: candidate,
        restore_state_builder=lambda restore_mode: AgentRestoreState(
            restore_mode=RestoreMode(restore_mode),
            last_checkpoint=None,
            conversation_summary='pending restore',
        ),
    )

    assert calls == []
    assert prepared[0].binding is binding
    assert prepared[0].provider_prepared is False
    assert prepared[0].effective_command is None


def test_prepare_start_agents_restarts_reused_binding_when_provider_profile_drifts(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / 'repo-start-prep-profile-drift'
    (project_root / '.ccb').mkdir(parents=True)
    (project_root / '.ccb' / 'ccb.config').write_text(
        '\n'.join(
            [
                'agent1:claude',
                '',
                '[agents.agent1.provider_profile.env]',
                'ANTHROPIC_AUTH_TOKEN = "token-new"',
                'ANTHROPIC_BASE_URL = "https://example.test"',
                '',
            ]
        ),
        encoding='utf-8',
    )
    bootstrap_project(project_root)
    command = ParsedStartCommand(project=None, agent_names=('agent1',), restore=True, auto_permission=False)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    config = load_project_config(project_root).config
    paths = PathLayout(project_root)
    runtime_dir = paths.agent_provider_runtime_dir('agent1', 'claude')
    runtime_dir.mkdir(parents=True)
    (runtime_dir / 'provider-profile.json').write_text(
        json.dumps(
            {
                'provider': 'claude',
                'agent_name': 'agent1',
                'mode': 'inherit',
                'profile_root': str(paths.provider_profiles_dir / 'agent1' / 'claude'),
                'runtime_home': None,
                'env': {},
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
    binding = SimpleNamespace(runtime_ref='mux:w1:p1')
    calls: list[dict[str, object]] = []
    monkeypatch.setattr(
        'ccbd.start_preparation.prepare_provider_workspace',
        lambda **kwargs: calls.append(kwargs),
    )

    prepared = prepare_start_agents(
        targets=('agent1',),
        config=config,
        paths=paths,
        context=context,
        project_root=project_root,
        project_id=context.project.project_id,
        tmux_socket_path=None,
        tmux_session_name=None,
        workspace_window_id=None,
        resolve_agent_binding_fn=lambda **kwargs: binding,
        project_binding_filter_fn=lambda candidate, **kwargs: candidate,
        restore_state_builder=lambda restore_mode: AgentRestoreState(
            restore_mode=RestoreMode(restore_mode),
            last_checkpoint=None,
            conversation_summary='pending restore',
        ),
    )

    assert [call['agent_name'] for call in calls] == ['agent1']
    assert calls[0]['refresh_profile'] is True
    assert prepared[0].binding is None
    assert prepared[0].raw_binding is binding
    assert prepared[0].stale_binding is True
    assert prepared[0].provider_prepared is True
    assert prepared[0].binding_reject_reason == 'provider_profile_changed'


def test_provider_profile_reject_reason_accepts_agent_env_api_credentials(
    tmp_path: Path,
) -> None:
    from ccbd.start_preparation import _provider_profile_reject_reason

    spec = SimpleNamespace(
        provider='claude',
        env={'ANTHROPIC_API_KEY': 'sk-agent-level'},
        provider_profile=SimpleNamespace(
            mode='inherit',
            home=None,
            env={},
            mcp_servers={},
            plugins={},
            inherit_api=True,
            inherit_auth=True,
            inherit_config=True,
            inherit_skills=True,
            inherit_commands=True,
            inherit_memory=True,
            inherited_skill_include=(),
            inherited_skill_exclude=(),
            skill_overlays={},
        ),
    )
    runtime_dir = tmp_path / 'runtime'
    runtime_dir.mkdir(parents=True)
    (runtime_dir / 'provider-profile.json').write_text(
        json.dumps(
            {
                'provider': 'claude',
                'agent_name': 'agent1',
                'mode': 'inherit',
                'profile_root': None,
                'runtime_home': None,
                'env': {'ANTHROPIC_API_KEY': 'sk-agent-level'},
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
    paths = SimpleNamespace(agent_provider_runtime_dir=lambda agent_name, provider: runtime_dir)

    assert _provider_profile_reject_reason(paths=paths, spec=spec, agent_name='agent1') is None


def test_provider_profile_reject_reason_normalizes_relative_runtime_home(
    tmp_path: Path,
) -> None:
    from ccbd.start_preparation import _provider_profile_reject_reason

    project_root = tmp_path / 'repo-relative-profile-home'
    paths = PathLayout(project_root)
    runtime_dir = paths.agent_provider_runtime_dir('agent1', 'codex')
    runtime_dir.mkdir(parents=True)
    resolved_home = (project_root / 'profiles' / 'agent1-codex').resolve()
    spec = SimpleNamespace(
        provider='codex',
        env={},
        provider_profile=SimpleNamespace(
            mode='isolated',
            home='profiles/agent1-codex',
            env={},
            mcp_servers={},
            plugins={},
            inherit_api=True,
            inherit_auth=True,
            inherit_config=True,
            inherit_skills=True,
            inherit_commands=True,
            inherit_memory=True,
            inherited_skill_include=(),
            inherited_skill_exclude=(),
            skill_overlays={},
        ),
    )
    (runtime_dir / 'provider-profile.json').write_text(
        json.dumps(
            {
                'provider': 'codex',
                'agent_name': 'agent1',
                'mode': 'isolated',
                'profile_root': str(resolved_home),
                'runtime_home': str(resolved_home),
                'env': {},
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

    assert _provider_profile_reject_reason(
        paths=paths,
        spec=spec,
        agent_name='agent1',
    ) is None


def test_prepare_start_agents_forced_restart_rebuilds_provider_state(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root, context, config, paths = _single_codex_project(tmp_path, 'repo-start-prep-forced-restart')
    binding = SimpleNamespace(runtime_ref='tmux:%1')
    calls: list[str] = []
    monkeypatch.setattr(
        'ccbd.start_preparation.prepare_provider_workspace',
        lambda **kwargs: calls.append(kwargs['agent_name']),
    )

    prepared = prepare_start_agents(
        targets=('agent1',),
        config=config,
        paths=paths,
        context=context,
        project_root=project_root,
        project_id=context.project.project_id,
        tmux_socket_path=None,
        tmux_session_name=None,
        workspace_window_id=None,
        resolve_agent_binding_fn=lambda **kwargs: binding,
        project_binding_filter_fn=lambda candidate, **kwargs: candidate,
        restore_state_builder=lambda restore_mode: AgentRestoreState(
            restore_mode=RestoreMode(restore_mode),
            last_checkpoint=None,
            conversation_summary='pending restore',
        ),
        force_restart_agents=('agent1',),
    )

    assert calls == ['agent1']
    assert prepared[0].binding is None
    assert prepared[0].raw_binding is binding
    assert prepared[0].stale_binding is True
    assert prepared[0].provider_prepared is True
    assert prepared[0].effective_command is not None
    assert prepared[0].binding_reject_reason == 'manual_restart'


def test_prepare_start_agents_treats_empty_tmux_socket_path_as_missing(monkeypatch, tmp_path: Path) -> None:
    project_root, context, config, paths = _single_codex_project(tmp_path, 'repo-start-prep-empty-tmux')
    binding = SimpleNamespace(runtime_ref='mux:w4M:p3')
    called: list[tuple[bool, str | None]] = []

    def resolve_agent_binding_fn(*, ensure_usable: bool, **kwargs):
        del kwargs
        called.append((ensure_usable, None))
        return binding

    monkeypatch.setattr(
        'ccbd.start_preparation.prepare_provider_workspace',
        lambda **kwargs: None,
    )

    prepared = prepare_start_agents(
        targets=('agent1',),
        config=config,
        paths=paths,
        context=context,
        project_root=project_root,
        project_id=context.project.project_id,
        tmux_socket_path='',
        tmux_session_name='',
        workspace_window_id=None,
        resolve_agent_binding_fn=resolve_agent_binding_fn,
        project_binding_filter_fn=lambda candidate, **kwargs: (_ for _ in ()).throw(
            AssertionError('tmux filter must not run for empty socket path')
        ),
        restore_state_builder=lambda restore_mode: AgentRestoreState(
            restore_mode=RestoreMode(restore_mode),
            last_checkpoint=None,
            conversation_summary='pending restore',
        ),
    )

    assert prepared[0].binding is binding
    assert prepared[0].binding_reject_reason is None
    assert called == [(False, None), (True, None)]


def test_prepare_start_agents_uses_assigned_pane_fallback_for_mux_binding_without_tmux_socket(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root, context, config, paths = _single_codex_project(tmp_path, 'repo-start-prep-mux-fallback')
    raw_binding = SimpleNamespace(
        runtime_ref='mux:w4M:p3',
        pane_id='w4M:p3',
        active_pane_id='w4M:p3',
        pane_state='unknown',
    )
    calls: list[bool] = []

    def resolve_agent_binding_fn(*, ensure_usable: bool, **kwargs):
        del kwargs
        calls.append(ensure_usable)
        return raw_binding if not ensure_usable else None

    monkeypatch.setattr(
        'ccbd.start_preparation.prepare_provider_workspace',
        lambda **kwargs: None,
    )

    prepared = prepare_start_agents(
        targets=('agent1',),
        config=config,
        paths=paths,
        context=context,
        project_root=project_root,
        project_id=context.project.project_id,
        tmux_socket_path='',
        tmux_session_name='',
        workspace_window_id=None,
        namespace_agent_panes={'agent1': 'w4M:p3'},
        resolve_agent_binding_fn=resolve_agent_binding_fn,
        project_binding_filter_fn=lambda candidate, **kwargs: (_ for _ in ()).throw(
            AssertionError('tmux filter must not run for empty socket path')
        ),
        restore_state_builder=lambda restore_mode: AgentRestoreState(
            restore_mode=RestoreMode(restore_mode),
            last_checkpoint=None,
            conversation_summary='pending restore',
        ),
    )

    assert prepared[0].binding is raw_binding
    assert prepared[0].binding_reject_reason is None
    assert calls == [False, True]


def test_prepare_start_agents_prepares_missing_binding_once(monkeypatch, tmp_path: Path) -> None:
    project_root, context, config, paths = _single_codex_project(tmp_path, 'repo-start-prep-launch')
    calls: list[str] = []
    monkeypatch.setattr(
        'ccbd.start_preparation.prepare_provider_workspace',
        lambda **kwargs: calls.append(kwargs['agent_name']),
    )

    prepared = prepare_start_agents(
        targets=('agent1',),
        config=config,
        paths=paths,
        context=context,
        project_root=project_root,
        project_id=context.project.project_id,
        tmux_socket_path=None,
        tmux_session_name=None,
        workspace_window_id=None,
        resolve_agent_binding_fn=lambda **kwargs: None,
        project_binding_filter_fn=lambda candidate, **kwargs: candidate,
        restore_state_builder=lambda restore_mode: AgentRestoreState(
            restore_mode=RestoreMode(restore_mode),
            last_checkpoint=None,
            conversation_summary='pending restore',
        ),
    )

    assert calls == ['agent1']
    assert prepared[0].provider_prepared is True
    assert prepared[0].binding_reject_reason == 'binding_missing'


def test_prepare_start_agents_reports_logical_window_reject_reason(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-start-prep-window-mismatch'
    (project_root / '.ccb').mkdir(parents=True)
    (project_root / '.ccb' / 'ccb.config').write_text(
        'version = 2\n\n[windows]\nreview = "agent1:codex"\n',
        encoding='utf-8',
    )
    bootstrap_project(project_root)
    command = ParsedStartCommand(project=None, agent_names=('agent1',), restore=False, auto_permission=False)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    config = load_project_config(project_root).config
    paths = PathLayout(project_root)
    raw_binding = SimpleNamespace(
        runtime_ref='tmux:%41',
        pane_id='%41',
        active_pane_id='%41',
        pane_state='alive',
        provider_identity_state='match',
    )
    record = ProjectNamespacePaneRecord(
        pane_id='%41',
        session_name='ccb-demo',
        window_id='@1',
        window_name='main',
        role='agent',
        slot_key='agent1',
        ccb_window='main',
        project_id=context.project.project_id,
        managed_by='ccbd',
        namespace_epoch=7,
        alive=True,
    )
    monkeypatch.setattr('ccbd.start_preparation.prepare_provider_workspace', lambda **kwargs: None)

    prepared = prepare_start_agents(
        targets=('agent1',),
        config=config,
        paths=paths,
        context=context,
        project_root=project_root,
        project_id=context.project.project_id,
        tmux_socket_path='/tmp/ccb.sock',
        tmux_session_name='ccb-demo',
        workspace_window_id='@0',
        namespace_epoch=7,
        namespace_pane_records={'%41': record},
        resolve_agent_binding_fn=lambda **kwargs: raw_binding,
        project_binding_filter_fn=lambda candidate, **kwargs: None,
        restore_state_builder=lambda restore_mode: AgentRestoreState(
            restore_mode=RestoreMode(restore_mode),
            last_checkpoint=None,
            conversation_summary='pending restore',
        ),
    )

    assert prepared[0].binding_reject_reason == 'logical_window_mismatch'


def test_prepare_start_agents_honors_windows_overlay_inplace_for_non_git_project(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-start-prep-windows-overlay-inplace'
    (project_root / '.ccb').mkdir(parents=True)
    (project_root / 'README.md').write_text('not a git repo\n', encoding='utf-8')
    (project_root / '.ccb' / 'ccb.config').write_text(
        """version = 2

[windows]
main = "agent1:codex(worktree)"

[agents.agent1]
workspace_mode = "inplace"
""",
        encoding='utf-8',
    )
    bootstrap_project(project_root)
    command = ParsedStartCommand(project=None, agent_names=(), restore=True, auto_permission=False)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    config = load_project_config(project_root).config
    paths = PathLayout(project_root)

    prepared = prepare_start_agents(
        targets=('agent1',),
        config=config,
        paths=paths,
        context=context,
        project_root=project_root,
        project_id=context.project.project_id,
        tmux_socket_path=None,
        tmux_session_name=None,
        workspace_window_id=None,
        resolve_agent_binding_fn=lambda **kwargs: None,
        project_binding_filter_fn=lambda binding, **kwargs: binding,
        restore_state_builder=lambda restore_mode: AgentRestoreState(
            restore_mode=RestoreMode(restore_mode),
            last_checkpoint=None,
            conversation_summary='pending restore',
        ),
    )

    assert prepared[0].plan.workspace_mode is WorkspaceMode.INPLACE
    assert prepared[0].plan.workspace_path == project_root.resolve()
    assert paths.workspace_path('agent1').exists() is False


def test_prepare_start_agents_rejects_duplicate_effective_provider_homes(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-start-prep-duplicate-provider-home'
    shared_home = tmp_path / 'shared-codex-home'
    shared_home_toml = str(shared_home).replace('\\', '\\\\')
    (project_root / '.ccb').mkdir(parents=True)
    (project_root / '.ccb' / 'ccb.config').write_text(
        f"""version = 2
default_agents = ["agent1", "agent2"]

[agents.agent1]
provider = "codex"
target = "."
workspace_mode = "inplace"
restore = "auto"
permission = "manual"

[agents.agent1.provider_profile]
mode = "isolated"
home = "{shared_home_toml}"

[agents.agent2]
provider = "codex"
target = "."
workspace_mode = "inplace"
restore = "auto"
permission = "manual"

[agents.agent2.provider_profile]
mode = "isolated"
home = "{shared_home_toml}"
""",
        encoding='utf-8',
    )
    bootstrap_project(project_root)
    command = ParsedStartCommand(project=None, agent_names=(), restore=True, auto_permission=False)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    config = load_project_config(project_root).config
    paths = PathLayout(project_root)

    with pytest.raises(RuntimeError, match='duplicate effective codex_home'):
        prepare_start_agents(
            targets=('agent1',),
            config=config,
            paths=paths,
            context=context,
            project_root=project_root,
            project_id=context.project.project_id,
            tmux_socket_path=None,
            tmux_session_name=None,
            workspace_window_id=None,
            resolve_agent_binding_fn=lambda **kwargs: None,
            project_binding_filter_fn=lambda binding, **kwargs: binding,
            restore_state_builder=lambda restore_mode: {'restore_mode': restore_mode},
        )


def test_prepare_start_agents_uses_provider_run_cwd_for_memory_projection(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-start-prep-claude-memory'
    resume_dir = tmp_path / 'claude-resume'
    resume_dir.mkdir()
    (project_root / '.ccb').mkdir(parents=True)
    (project_root / '.ccb' / 'ccb.config').write_text(
        """version = 2
default_agents = ["reviewer"]

[agents.reviewer]
provider = "claude"
target = "."
workspace_mode = "inplace"
restore = "auto"
permission = "manual"
""",
        encoding='utf-8',
    )
    bootstrap_project(project_root)
    command = ParsedStartCommand(project=None, agent_names=('reviewer',), restore=True, auto_permission=False)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    config = load_project_config(project_root).config
    paths = PathLayout(project_root)

    monkeypatch.setattr(
        claude_launcher,
        '_resolve_claude_restore_target',
        lambda **kwargs: ProviderRestoreTarget(run_cwd=resume_dir, has_history=True),
    )

    prepare_start_agents(
        targets=('reviewer',),
        config=config,
        paths=paths,
        context=context,
        project_root=project_root,
        project_id=context.project.project_id,
        tmux_socket_path=None,
        tmux_session_name=None,
        workspace_window_id=None,
        resolve_agent_binding_fn=lambda **kwargs: None,
        project_binding_filter_fn=lambda binding, **kwargs: binding,
        restore_state_builder=lambda restore_mode: AgentRestoreState(
            restore_mode=RestoreMode(restore_mode),
            last_checkpoint=None,
            conversation_summary='pending restore',
        ),
    )

    managed_memory = (
        paths.agent_provider_state_dir('reviewer', 'claude')
        / 'home'
        / '.claude'
        / 'CLAUDE.md'
    )
    assert f'workspace_path: {resume_dir.resolve()}' in managed_memory.read_text(encoding='utf-8')


def test_prepare_start_agents_refreshes_ccb_only_claude_permissions_when_auto_permission(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / 'repo-start-prep-claude-permissions'
    source_home = tmp_path / 'source-home'
    (source_home / '.claude').mkdir(parents=True)
    (source_home / '.claude' / 'settings.json').write_text(
        json.dumps(
            {
                'permissions': {
                    'allow': ['Read', 'Write', 'Edit', 'Bash(git:*)', 'Bash(ccb ask *)'],
                    'deny': [],
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    (project_root / '.ccb').mkdir(parents=True)
    (project_root / '.ccb' / 'ccb.config').write_text(
        """version = 2
default_agents = ["reviewer"]

[agents.reviewer]
provider = "claude"
target = "."
workspace_mode = "inplace"
restore = "auto"
permission = "manual"
""",
        encoding='utf-8',
    )
    bootstrap_project(project_root)
    command = ParsedStartCommand(project=None, agent_names=('reviewer',), restore=True, auto_permission=True)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    config = load_project_config(project_root).config
    paths = PathLayout(project_root)
    managed_settings = (
        paths.agent_provider_state_dir('reviewer', 'claude')
        / 'home'
        / '.claude'
        / 'settings.json'
    )
    managed_settings.parent.mkdir(parents=True)
    managed_settings.write_text(
        json.dumps(
            {
                'permissions': {
                    'allow': ['Bash(ccb ask *)', 'Bash(ccb ping *)', 'Bash(ccb pend *)'],
                    'deny': [],
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    monkeypatch.setattr(
        'cli.services.provider_hooks.current_provider_source_home',
        lambda: source_home,
    )
    monkeypatch.setattr(
        claude_launcher,
        '_resolve_claude_restore_target',
        lambda **kwargs: ProviderRestoreTarget(run_cwd=project_root, has_history=False),
    )

    prepared = prepare_start_agents(
        targets=('reviewer',),
        config=config,
        paths=paths,
        context=context,
        project_root=project_root,
        project_id=context.project.project_id,
        tmux_socket_path=None,
        tmux_session_name=None,
        workspace_window_id=None,
        resolve_agent_binding_fn=lambda **kwargs: None,
        project_binding_filter_fn=lambda binding, **kwargs: binding,
        restore_state_builder=lambda restore_mode: AgentRestoreState(
            restore_mode=RestoreMode(restore_mode),
            last_checkpoint=None,
            conversation_summary='pending restore',
        ),
    )

    payload = json.loads(managed_settings.read_text(encoding='utf-8'))
    assert payload['permissions']['allow'] == ['Read', 'Write', 'Edit', 'Bash(git:*)', 'Bash(ccb ask *)']
    assert prepared[0].effective_command is context.command
    assert prepared[0].effective_command.auto_permission is True


def test_prepare_start_agents_uses_hard_role_effective_command_for_claude_projection(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / 'repo-start-prep-hard-role'
    source_home = tmp_path / 'source-home'
    (source_home / '.claude').mkdir(parents=True)
    (source_home / '.claude' / 'settings.json').write_text(
        json.dumps({'theme': 'dark'}),
        encoding='utf-8',
    )
    (project_root / '.ccb').mkdir(parents=True)
    (project_root / '.ccb' / 'ccb.config').write_text(
        'version = 2\ndefault_agents = ["reviewer"]\n\n'
        '[agents.reviewer]\nprovider = "claude"\ntarget = "."\n'
        'workspace_mode = "inplace"\nrestore = "auto"\npermission = "manual"\n'
        'role = "test.hard"\n',
        encoding='utf-8',
    )
    bootstrap_project(project_root)
    command = ParsedStartCommand(
        project=None,
        agent_names=('reviewer',),
        restore=True,
        auto_permission=True,
    )
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    config = load_project_config(project_root).config
    paths = PathLayout(project_root)
    policy = RoleCommandPolicy(
        role_id='test.hard',
        path=tmp_path / 'command-surface.toml',
        mode='deny_all_except',
        enforcement='required',
        if_unsupported='fail_mount',
        generic_shell=False,
        generic_ccb=False,
        supported_providers=('claude',),
        provider_tools=(),
        allowed_effects=(),
        forbidden_effects=(),
        allowed=(),
    )
    monkeypatch.setattr(
        'cli.services.runtime_launch.role_command_policy_for_spec',
        lambda _spec: policy,
    )
    monkeypatch.setattr(
        'cli.services.role_command_policy.role_command_policy_for_spec',
        lambda _spec: policy,
    )
    monkeypatch.setattr(
        'cli.services.provider_hooks.current_provider_source_home',
        lambda: source_home,
    )
    monkeypatch.setattr(
        claude_launcher,
        '_resolve_claude_restore_target',
        lambda **kwargs: ProviderRestoreTarget(run_cwd=project_root, has_history=False),
    )
    from cli.services.provider_hooks import prepare_provider_workspace as real_prepare_provider_workspace

    observed_auto_permission: list[bool] = []

    def capture_prepare(**kwargs):
        observed_auto_permission.append(bool(kwargs['auto_permission']))
        return real_prepare_provider_workspace(**kwargs)

    monkeypatch.setattr('ccbd.start_preparation.prepare_provider_workspace', capture_prepare)

    prepared = prepare_start_agents(
        targets=('reviewer',),
        config=config,
        paths=paths,
        context=context,
        project_root=project_root,
        project_id=context.project.project_id,
        tmux_socket_path=None,
        tmux_session_name=None,
        workspace_window_id=None,
        resolve_agent_binding_fn=lambda **kwargs: None,
        project_binding_filter_fn=lambda binding, **kwargs: binding,
        restore_state_builder=lambda restore_mode: AgentRestoreState(
            restore_mode=RestoreMode(restore_mode),
            last_checkpoint=None,
            conversation_summary='pending restore',
        ),
    )

    assert context.command.auto_permission is True
    assert prepared[0].effective_command.auto_permission is False
    assert observed_auto_permission == [False]
    managed_home = paths.agent_provider_state_dir('reviewer', 'claude') / 'home'
    settings = json.loads((managed_home / '.claude' / 'settings.json').read_text(encoding='utf-8'))
    trust = json.loads((managed_home / '.claude' / '.claude.json').read_text(encoding='utf-8'))
    assert 'skipDangerousModePermissionPrompt' not in settings
    assert 'bypassPermissionsModeAccepted' not in trust
