from __future__ import annotations

import json
from pathlib import Path
import shlex
import subprocess
import pytest
try:  # pragma: no cover - version shim
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib

from agents.models import (
    AgentSpec,
    PermissionMode,
    QueuePolicy,
    RestoreMode,
    RuntimeMode,
    WorkspaceMode,
)
from cli.context import CliContext
from cli.models import ParsedStartCommand
from cli.services.provider_binding import AgentBinding
import cli.services.runtime_launch as runtime_launch
from cli.services.runtime_launch import ensure_agent_runtime
from provider_backends.claude import launcher as claude_launcher
from provider_backends.codex import launcher as codex_launcher
from provider_backends.droid import launcher as droid_launcher
from provider_backends.gemini import launcher as gemini_launcher
from provider_backends.opencode import launcher as opencode_launcher
from provider_backends.runtime_restore import ProviderRestoreTarget
from provider_backends.codex.launcher_runtime.command import prepare_codex_home_overrides as prepare_codex_home_overrides_for_test
import provider_profiles.codex_home_config as codex_home_config
from provider_profiles import load_resolved_provider_profile
from provider_profiles.models import ResolvedProviderProfile
from project.ids import compute_project_id
from project.resolver import ProjectContext
from storage.paths import PathLayout
from terminal_runtime.tmux_identity import pane_visual
from workspace.planner import WorkspacePlanner


def _spec(name: str, provider: str = 'codex') -> AgentSpec:
    return AgentSpec(
        name=name,
        provider=provider,
        target='.',
        workspace_mode=WorkspaceMode.GIT_WORKTREE,
        workspace_root=None,
        runtime_mode=RuntimeMode.PANE_BACKED,
        restore_default=RestoreMode.AUTO,
        permission_default=PermissionMode.MANUAL,
        queue_policy=QueuePolicy.SERIAL_PER_AGENT,
    )


def _context(project_root: Path, command: ParsedStartCommand) -> CliContext:
    project_root = project_root.resolve()
    config_dir = project_root / '.ccb'
    config_dir.mkdir(parents=True, exist_ok=True)
    project = ProjectContext(
        cwd=project_root,
        project_root=project_root,
        config_dir=config_dir,
        project_id=compute_project_id(project_root),
        source='test',
    )
    return CliContext(command=command, cwd=project_root, project=project, paths=PathLayout(project_root))


def _write_provider_profile(runtime_dir: Path, profile: ResolvedProviderProfile) -> None:
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / 'provider-profile.json').write_text(
        json.dumps(profile.to_record(), ensure_ascii=False, indent=2),
        encoding='utf-8',
    )


def _project_memory_path(project_root: Path) -> Path:
    return project_root / '.ccb' / 'ccb_memory.md'


def _write_project_memory(project_root: Path, text: str) -> None:
    path = _project_memory_path(project_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def _clipboard_bind_call(key: str) -> tuple[str, tuple[str, ...]]:
    return (
        'bind-key',
        (
            'bind-key',
            '-T',
            'copy-mode-vi',
            key,
            'send-keys',
            '-X',
            'copy-pipe-and-cancel',
            _clipboard_pipe_command_for_test(),
        ),
    )


def _clipboard_pipe_command_for_test() -> str:
    return (
        "sh -lc '"
        "tmp=$(mktemp \"${TMPDIR:-/tmp}/ccb-clipboard.XXXXXX\") || exit 0; "
        "cat >\"$tmp\"; "
        "if command -v wl-copy >/dev/null 2>&1 && [ -n \"${WAYLAND_DISPLAY:-}\" ]; then (wl-copy <\"$tmp\"; rm -f \"$tmp\") >/dev/null 2>&1 & "
        "elif command -v xclip >/dev/null 2>&1 && [ -n \"${DISPLAY:-}\" ]; then (xclip -selection clipboard <\"$tmp\"; rm -f \"$tmp\") >/dev/null 2>&1 & "
        "elif command -v xsel >/dev/null 2>&1 && [ -n \"${DISPLAY:-}\" ]; then (xsel --clipboard --input <\"$tmp\"; rm -f \"$tmp\") >/dev/null 2>&1 & "
        "elif command -v pbcopy >/dev/null 2>&1; then pbcopy <\"$tmp\"; rm -f \"$tmp\"; "
        "elif command -v powershell.exe >/dev/null 2>&1; then powershell.exe -NoProfile -Command \"[Console]::InputEncoding=[System.Text.UTF8Encoding]::new(); Set-Clipboard -Value ([Console]::In.ReadToEnd())\" <\"$tmp\"; rm -f \"$tmp\"; "
        "elif command -v pwsh >/dev/null 2>&1; then pwsh -NoLogo -NoProfile -Command \"[Console]::InputEncoding=[System.Text.UTF8Encoding]::new(); Set-Clipboard -Value ([Console]::In.ReadToEnd())\" <\"$tmp\"; rm -f \"$tmp\"; "
        "else rm -f \"$tmp\"; fi'"
    )


def _claude_prepared_state(runtime_dir: Path) -> dict[str, object]:
    return {'project_root': _launch_project_root(runtime_dir)}


def _prepare_claude_home_for_test(
    spec: AgentSpec,
    runtime_dir: Path,
    *,
    workspace_path: Path | None = None,
) -> dict[str, object]:
    project_root = _launch_project_root(runtime_dir)
    workspace = workspace_path or project_root
    from cli.services.provider_hooks import prepare_provider_workspace

    prepare_provider_workspace(
        layout=PathLayout(project_root),
        spec=spec,
        workspace_path=workspace,
        completion_dir=Path(runtime_dir) / 'completion',
        agent_name=spec.name,
        refresh_profile=False,
    )
    return _claude_prepared_state(runtime_dir)


def _codex_prepared_state(runtime_dir: Path, *, agent_name: str = 'agent1') -> dict[str, object]:
    project_root = _launch_project_root(runtime_dir)
    return {
        'agent_name': agent_name,
        'project_root': project_root,
        'workspace_path': project_root,
        'agent_events_path': project_root / '.ccb' / 'agents' / agent_name / 'events.jsonl',
    }


def _launch_project_root(runtime_dir: Path) -> Path:
    runtime = Path(runtime_dir)
    project_root = runtime.parent
    for parent in runtime.parents:
        if parent.name == '.ccb':
            project_root = parent.parent
            break
    return project_root


def _prepare_codex_home_for_test(spec: AgentSpec, runtime_dir: Path) -> dict[str, object]:
    prepared = _codex_prepared_state(runtime_dir, agent_name=spec.name)
    profile = load_resolved_provider_profile(runtime_dir)
    prepare_codex_home_overrides_for_test(
        runtime_dir,
        profile,
        refresh_home=True,
        project_root=prepared['project_root'],
        agent_name=spec.name,
        workspace_path=prepared['workspace_path'],
        memory_projection_event_path=prepared['agent_events_path'],
        memory_projection_marker_path=Path(runtime_dir) / 'codex-memory-projection.json',
    )
    return prepared


def _codex_start_cmd(command, spec: AgentSpec, runtime_dir: Path, launch_session_id: str) -> str:
    prepared = _prepare_codex_home_for_test(spec, runtime_dir)
    return codex_launcher.build_start_cmd(
        command,
        spec,
        runtime_dir,
        launch_session_id,
        prepared_state=prepared,
    )


def _opencode_prepared_state(runtime_dir: Path, *, agent_name: str = 'agent1') -> dict[str, object]:
    project_root = _launch_project_root(runtime_dir)
    return {
        'agent_name': agent_name,
        'project_root': project_root,
        'workspace_path': project_root,
        'agent_events_path': project_root / '.ccb' / 'agents' / agent_name / 'events.jsonl',
        'opencode_config_path': project_root / '.ccb' / 'agents' / agent_name / 'provider-state' / 'opencode' / 'opencode.json',
    }


def _prepare_opencode_workspace_for_test(spec: AgentSpec, runtime_dir: Path, *, workspace_path: Path | None = None) -> dict[str, object]:
    project_root = _launch_project_root(runtime_dir)
    workspace = workspace_path or project_root
    from cli.services.provider_hooks import prepare_provider_workspace

    prepare_provider_workspace(
        layout=PathLayout(project_root),
        spec=spec,
        workspace_path=workspace,
        completion_dir=Path(runtime_dir) / 'completion',
        agent_name=spec.name,
        refresh_profile=False,
    )
    return _opencode_prepared_state(runtime_dir, agent_name=spec.name)


def _assert_caller_env_exports(start_cmd: str, *, actor: str, runtime_dir: Path, session_id: str) -> None:
    assert f'CCB_CALLER_ACTOR={shlex.quote(actor)}' in start_cmd
    assert f'CCB_CALLER_RUNTIME_DIR={shlex.quote(str(runtime_dir))}' in start_cmd
    assert f'CCB_SESSION_ID={shlex.quote(session_id)}' in start_cmd


def _write_codex_plugin_source(
    home: Path,
    *,
    plugin_name: str = 'demo-plugin',
    sha: str = 'plugins-sha-v1',
    marketplace_name: str = 'openai-curated',
    skill_body: str = 'plugin skill v1\n',
) -> None:
    plugin_root = home / '.tmp' / 'plugins'
    (plugin_root / '.agents' / 'plugins').mkdir(parents=True, exist_ok=True)
    (plugin_root / '.agents' / 'skills' / 'plugin-creator').mkdir(parents=True, exist_ok=True)
    (plugin_root / 'plugins' / plugin_name / '.codex-plugin').mkdir(parents=True, exist_ok=True)
    (plugin_root / 'plugins' / plugin_name / 'skills' / plugin_name).mkdir(parents=True, exist_ok=True)
    (home / '.tmp').mkdir(parents=True, exist_ok=True)
    (home / '.tmp' / 'plugins.sha').write_text(f'{sha}\n', encoding='utf-8')
    (plugin_root / '.agents' / 'plugins' / 'marketplace.json').write_text(
        json.dumps(
            {
                'name': marketplace_name,
                'plugins': [
                    {
                        'name': plugin_name,
                        'source': {'source': 'local', 'path': f'./plugins/{plugin_name}'},
                    }
                ],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    (plugin_root / 'plugins' / plugin_name / '.codex-plugin' / 'plugin.json').write_text(
        json.dumps({'name': plugin_name}, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    (plugin_root / 'plugins' / plugin_name / 'skills' / plugin_name / 'SKILL.md').write_text(
        skill_body,
        encoding='utf-8',
    )


def test_ensure_agent_runtime_configures_claude_managed_home_without_touching_workspace(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-claude-hooks'
    (project_root / '.ccb').mkdir(parents=True)

    ctx = _context(project_root, ParsedStartCommand(project=None, agent_names=('agent3',), restore=False, auto_permission=False))
    spec = _spec('agent3', provider='claude')
    plan = WorkspacePlanner().plan(spec, ctx.project)
    workspace_settings = plan.workspace_path / '.claude' / 'settings.json'
    workspace_settings.parent.mkdir(parents=True, exist_ok=True)
    workspace_settings.write_text(
        json.dumps(
            {
                'env': {
                    'ANTHROPIC_AUTH_TOKEN': 'token-stale',
                    'ANTHROPIC_BASE_URL': 'https://api.stale.invalid',
                }
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    observed: dict[str, object] = {}
    managed_settings = ctx.paths.agent_provider_state_dir('agent3', 'claude') / 'home' / '.claude' / 'settings.json'

    def fake_ensure_impl(*args, **kwargs):
        del args, kwargs
        observed['workspace_settings_exists'] = workspace_settings.exists()
        observed['managed_settings_exists'] = managed_settings.exists()
        return runtime_launch.RuntimeLaunchResult(launched=False, binding=None)

    monkeypatch.setattr(runtime_launch, '_ensure_agent_runtime_impl', fake_ensure_impl)

    result = ensure_agent_runtime(ctx, ctx.command, spec, plan, None)

    assert result == runtime_launch.RuntimeLaunchResult(launched=False, binding=None)
    assert observed['workspace_settings_exists'] is True
    assert observed['managed_settings_exists'] is True
    managed_payload = json.loads(managed_settings.read_text(encoding='utf-8'))
    assert managed_payload['hooks']['Stop'][0]['hooks'][0]['command']


def test_ensure_agent_runtime_launches_named_codex_session(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    (project_root / '.ccb').mkdir(parents=True)
    ctx = _context(project_root, ParsedStartCommand(project=None, agent_names=('agent1',), restore=False, auto_permission=False))
    spec = _spec('agent1')
    plan = WorkspacePlanner().plan(spec, ctx.project)
    plan.workspace_path.mkdir(parents=True, exist_ok=True)

    tmux_state: dict[str, object] = {}

    class FakeTmuxBackend:
        _socket_name = 'sock-agent'
        _socket_path = '/tmp/ccb-agent.sock'

        def create_pane(self, cmd: str, cwd: str, direction: str = 'right', percent: int = 50, parent_pane: str | None = None) -> str:
            self.cmd = cmd
            self.cwd = cwd
            tmux_state['cwd'] = cwd
            tmux_state['cmd'] = cmd
            return '%42'

        def set_pane_title(self, pane_id: str, title: str) -> None:
            self.title = (pane_id, title)
            tmux_state['title'] = self.title

        def set_pane_user_option(self, pane_id: str, name: str, value: str) -> None:
            self.user_option = (pane_id, name, value)
            tmux_state['user_option'] = self.user_option

        def _tmux_run(self, args, capture=False, timeout=None):
            return subprocess.CompletedProcess(args=args, returncode=0, stdout='4242\n', stderr='')

    spawned: dict[str, object] = {}

    class FakePopen:
        def __init__(self, args, **kwargs):
            env = kwargs.get('env') or {}
            session_file = Path(str(env.get('CCB_SESSION_FILE') or ''))
            assert session_file.is_file()
            spawned.setdefault('calls', []).append((args, kwargs))
            spawned.setdefault('args', args)
            spawned.setdefault('kwargs', kwargs)
            spawned.setdefault('session_file', str(session_file))
            self.pid = 9911

    monkeypatch.setattr('cli.services.runtime_launch._inside_tmux', lambda: True)
    monkeypatch.setattr('cli.services.runtime_launch.shutil.which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr('cli.services.runtime_launch.TmuxBackend', FakeTmuxBackend)
    monkeypatch.setattr('cli.services.runtime_launch.subprocess.Popen', FakePopen)

    result = ensure_agent_runtime(ctx, ctx.command, spec, plan, None)

    assert result.launched is True
    assert result.binding is not None
    assert result.binding.runtime_ref == 'tmux:%42'
    expected_session = project_root / '.ccb' / '.codex-agent1-session'
    assert result.binding.session_ref == str(expected_session)
    payload = json.loads(expected_session.read_text(encoding='utf-8'))
    expected_codex_home = ctx.paths.agent_provider_state_dir('agent1', 'codex') / 'home'
    expected_session_root = expected_codex_home / 'sessions'
    assert payload['pane_id'] == '%42'
    assert payload['agent_name'] == 'agent1'
    assert payload['ccb_project_id'] == ctx.project.project_id
    assert payload['completion_artifact_dir'] == str(ctx.paths.agent_dir('agent1') / 'provider-runtime' / 'codex' / 'completion')
    assert payload['bridge_log'] == str(ctx.paths.agent_dir('agent1') / 'provider-runtime' / 'codex' / 'bridge.log')
    assert payload['codex_home'] == str(expected_codex_home)
    assert payload['codex_session_root'] == str(expected_session_root)
    assert payload['pane_title_marker'].startswith('CCB-agent1-')
    assert payload['tmux_socket_name'] == 'sock-agent'
    assert payload['tmux_socket_path'] == '/tmp/ccb-agent.sock'
    assert payload['work_dir'] == str(plan.workspace_path)
    assert payload['work_dir_norm']
    assert payload['tmux_log'] == payload['bridge_log']
    assert payload['codex_start_cmd'].startswith('export ')
    assert 'disable_paste_burst=true' in payload['codex_start_cmd']
    assert spawned['kwargs']['env']['CCB_SESSION_FILE'] == str(expected_session)
    assert spawned['kwargs']['env']['CODEX_TMUX_LOG'] == payload['bridge_log']
    assert spawned['kwargs']['env']['CODEX_HOME'] == str(expected_codex_home)
    assert spawned['kwargs']['env']['CODEX_SESSION_ROOT'] == str(expected_session_root)
    expected_lib_root = str((Path(codex_launcher.__file__).resolve().parents[2]))
    assert expected_lib_root in str(spawned['kwargs']['env']['PYTHONPATH'])
    assert Path(spawned['kwargs']['stdout'].name) == ctx.paths.agent_dir('agent1') / 'provider-runtime' / 'codex' / 'bridge.stdout.log'
    assert Path(spawned['kwargs']['stderr'].name) == ctx.paths.agent_dir('agent1') / 'provider-runtime' / 'codex' / 'bridge.stderr.log'
    assert tmux_state['title'] == ('%42', 'agent1')
    assert tmux_state['user_option'] == ('%42', '@ccb_project_id', ctx.project.project_id)
    assert (ctx.paths.agent_dir('agent1') / 'provider-runtime' / 'codex' / 'bridge.pid').read_text(encoding='utf-8').strip() == '9911'
    assert (ctx.paths.agent_dir('agent1') / 'provider-runtime' / 'codex' / 'codex.pid').read_text(encoding='utf-8').strip() == '4242'
    assert (ctx.paths.agent_dir('agent1') / 'provider-runtime' / 'codex' / 'completion').is_dir() is True
    assert (ctx.paths.agent_dir('agent1') / 'provider-runtime' / 'codex' / 'bridge.log').is_file() is True
    assert spawned['args'][0] == __import__('sys').executable
    assert spawned['args'][1:4] == ['-m', 'provider_backends.codex.bridge', '--runtime-dir']


def test_ensure_agent_runtime_relaunches_provider_identity_mismatch(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-identity-mismatch'
    (project_root / '.ccb').mkdir(parents=True)
    ctx = _context(project_root, ParsedStartCommand(project=None, agent_names=('agent1',), restore=True, auto_permission=False))
    spec = _spec('agent1')
    plan = WorkspacePlanner().plan(spec, ctx.project)
    plan.workspace_path.mkdir(parents=True, exist_ok=True)

    class FakeTmuxBackend:
        _socket_name = 'sock-agent'
        _socket_path = '/tmp/ccb-agent.sock'

        def is_tmux_pane_alive(self, pane_id: str) -> bool:
            assert pane_id == '%41'
            return True

        def kill_tmux_pane(self, pane_id: str) -> None:
            assert pane_id == '%41'

        def create_pane(self, cmd: str, cwd: str, direction: str = 'right', percent: int = 50, parent_pane: str | None = None) -> str:
            return '%42'

        def set_pane_title(self, pane_id: str, title: str) -> None:
            pass

        def set_pane_user_option(self, pane_id: str, name: str, value: str) -> None:
            pass

        def _tmux_run(self, args, capture=False, timeout=None):
            return subprocess.CompletedProcess(args=args, returncode=0, stdout='4242\n', stderr='')

    launched: list[tuple[str, str, str]] = []

    def _fake_launch(context, command, spec_arg, plan, launcher, *, assigned_pane_id=None, style_index=0, tmux_socket_path=None):
        del context, command, plan, launcher, assigned_pane_id, style_index, tmux_socket_path
        launched.append(('launched', spec_arg.name, spec_arg.provider))

    refreshed = AgentBinding(
        runtime_ref='tmux:%42',
        session_ref='bound-session',
        provider='codex',
        runtime_root=str(ctx.paths.agent_provider_runtime_dir('agent1', 'codex')),
        runtime_pid=4242,
        session_file=str(project_root / '.ccb' / '.codex-agent1-session'),
        session_id='bound-session',
        tmux_socket_name='sock-agent',
        tmux_socket_path='/tmp/ccb-agent.sock',
        terminal='tmux',
        pane_id='%42',
        active_pane_id='%42',
        pane_title_marker='CCB-agent1',
        pane_state='alive',
        provider_identity_state='match',
    )

    def _resolve_agent_binding(**kwargs):
        del kwargs
        return refreshed

    stale = AgentBinding(
        runtime_ref='tmux:%41',
        session_ref='bound-session',
        provider='codex',
        runtime_root=str(ctx.paths.agent_provider_runtime_dir('agent1', 'codex')),
        runtime_pid=4141,
        session_file=str(project_root / '.ccb' / '.codex-agent1-session'),
        session_id='bound-session',
        tmux_socket_name='sock-agent',
        tmux_socket_path='/tmp/ccb-agent.sock',
        terminal='tmux',
        pane_id='%41',
        active_pane_id='%41',
        pane_title_marker='CCB-agent1',
        pane_state='alive',
        provider_identity_state='mismatch',
        provider_identity_reason='live_codex_process_not_running_bound_resume_session',
    )

    monkeypatch.setattr('cli.services.runtime_launch.TmuxBackend', FakeTmuxBackend)
    monkeypatch.setattr('cli.services.runtime_launch.shutil.which', lambda name: f'/usr/bin/{name}')
    result = runtime_launch._ensure_agent_runtime_impl(
        ctx,
        ctx.command,
        spec,
        plan,
        stale,
        runtime_launch_result_cls=runtime_launch.RuntimeLaunchResult,
        binding_runtime_alive_fn=runtime_launch._binding_runtime_alive,
        provider_executable_fn=runtime_launch._provider_executable,
        cleanup_stale_tmux_binding_fn=runtime_launch._cleanup_stale_tmux_binding,
        launch_tmux_runtime_fn=_fake_launch,
        resolve_agent_binding_fn=_resolve_agent_binding,
    )

    assert result.launched is True
    assert result.binding is refreshed
    assert launched == [('launched', 'agent1', 'codex')]


def test_ensure_agent_runtime_uses_agent_scoped_session_name_for_codex_agent(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    (project_root / '.ccb').mkdir(parents=True)
    ctx = _context(project_root, ParsedStartCommand(project=None, agent_names=('codex',), restore=False, auto_permission=False))
    spec = _spec('codex')
    plan = WorkspacePlanner().plan(spec, ctx.project)
    plan.workspace_path.mkdir(parents=True, exist_ok=True)

    class FakeTmuxBackend:
        def create_pane(self, cmd: str, cwd: str, direction: str = 'right', percent: int = 50, parent_pane: str | None = None) -> str:
            return '%7'

        def set_pane_title(self, pane_id: str, title: str) -> None:
            pass

        def set_pane_user_option(self, pane_id: str, name: str, value: str) -> None:
            pass

        def _tmux_run(self, args, capture=False, timeout=None):
            return subprocess.CompletedProcess(args=args, returncode=0, stdout='7000\n', stderr='')

    class FakePopen:
        def __init__(self, args, **kwargs):
            self.pid = 1234

    monkeypatch.setattr('cli.services.runtime_launch._inside_tmux', lambda: True)
    monkeypatch.setattr('cli.services.runtime_launch.shutil.which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr('cli.services.runtime_launch.TmuxBackend', FakeTmuxBackend)
    monkeypatch.setattr('cli.services.runtime_launch.subprocess.Popen', FakePopen)

    result = ensure_agent_runtime(ctx, ctx.command, spec, plan, None)

    assert result.binding is not None
    assert result.binding.session_ref == str(project_root / '.ccb' / '.codex-codex-session')


def test_ensure_agent_runtime_passes_profile_codex_home_to_bridge(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-profile-bridge'
    (project_root / '.ccb').mkdir(parents=True)
    ctx = _context(project_root, ParsedStartCommand(project=None, agent_names=('agent1',), restore=False, auto_permission=False))
    spec = _spec('agent1')
    plan = WorkspacePlanner().plan(spec, ctx.project)
    plan.workspace_path.mkdir(parents=True, exist_ok=True)
    runtime_dir = ctx.paths.agent_dir('agent1') / 'provider-runtime' / 'codex'
    profile_home = tmp_path / 'profile-home'
    _write_provider_profile(
        runtime_dir,
        ResolvedProviderProfile(
            provider='codex',
            agent_name='agent1',
            mode='isolated',
            profile_root=str(tmp_path / 'profile-root'),
            runtime_home=str(profile_home),
            env={},
            inherit_api=True,
        ),
    )

    spawned: dict[str, object] = {}

    class FakeTmuxBackend:
        def create_pane(self, cmd: str, cwd: str, direction: str = 'right', percent: int = 50, parent_pane: str | None = None) -> str:
            return '%64'

        def set_pane_title(self, pane_id: str, title: str) -> None:
            pass

        def set_pane_user_option(self, pane_id: str, name: str, value: str) -> None:
            pass

        def _tmux_run(self, args, capture=False, timeout=None):
            return subprocess.CompletedProcess(args=args, returncode=0, stdout='6464\n', stderr='')

    class FakePopen:
        def __init__(self, args, **kwargs):
            env = kwargs.get('env') or {}
            if env.get('CCB_SESSION_FILE'):
                spawned['env'] = env
            self.pid = 8844

    monkeypatch.setattr('cli.services.runtime_launch._inside_tmux', lambda: True)
    monkeypatch.setattr('cli.services.runtime_launch.shutil.which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr('cli.services.runtime_launch.TmuxBackend', FakeTmuxBackend)
    monkeypatch.setattr('cli.services.runtime_launch.subprocess.Popen', FakePopen)

    ensure_agent_runtime(ctx, ctx.command, spec, plan, None)

    assert spawned['env']['CODEX_HOME'] == str(profile_home)
    assert spawned['env']['CODEX_SESSION_ROOT'] == str(profile_home / 'sessions')


def test_ensure_agent_runtime_rewrites_session_file_without_losing_existing_codex_binding(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / 'repo-rewrite-preserve'
    (project_root / '.ccb').mkdir(parents=True)
    existing_home = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-state' / 'codex' / 'home'
    existing_root = existing_home / 'sessions'
    existing_log = existing_root / '2026' / '04' / '19' / 'rollout-existing-session.jsonl'
    existing_log.parent.mkdir(parents=True, exist_ok=True)
    existing_log.write_text('', encoding='utf-8')
    existing_session = project_root / '.ccb' / '.codex-agent1-session'
    existing_session.write_text(
        json.dumps(
            {
                'codex_home': str(existing_home),
                'codex_session_root': str(existing_root),
                'codex_session_id': 'existing-session-id',
                'codex_session_path': str(existing_log),
                'start_cmd': 'codex resume existing-session-id',
                'codex_start_cmd': 'codex resume existing-session-id',
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    ctx = _context(project_root, ParsedStartCommand(project=None, agent_names=('agent1',), restore=True, auto_permission=False))
    spec = _spec('agent1')
    plan = WorkspacePlanner().plan(spec, ctx.project)
    plan.workspace_path.mkdir(parents=True, exist_ok=True)

    class FakeTmuxBackend:
        _socket_name = 'sock-agent'
        _socket_path = '/tmp/ccb-agent.sock'

        def create_pane(self, cmd: str, cwd: str, direction: str = 'right', percent: int = 50, parent_pane: str | None = None) -> str:
            self.cmd = cmd
            return '%90'

        def set_pane_title(self, pane_id: str, title: str) -> None:
            pass

        def set_pane_user_option(self, pane_id: str, name: str, value: str) -> None:
            pass

        def _tmux_run(self, args, capture=False, timeout=None):
            return subprocess.CompletedProcess(args=args, returncode=0, stdout='9090\n', stderr='')

    class FakePopen:
        def __init__(self, args, **kwargs):
            self.pid = 7777

    monkeypatch.setattr('cli.services.runtime_launch._inside_tmux', lambda: True)
    monkeypatch.setattr('cli.services.runtime_launch.shutil.which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr('cli.services.runtime_launch.TmuxBackend', FakeTmuxBackend)
    monkeypatch.setattr('cli.services.runtime_launch.subprocess.Popen', FakePopen)

    ensure_agent_runtime(ctx, ctx.command, spec, plan, None)

    payload = json.loads(existing_session.read_text(encoding='utf-8'))
    assert payload['codex_home'] == str(existing_home)
    assert payload['codex_session_root'] == str(existing_root)
    assert payload['codex_session_id'] == 'existing-session-id'
    assert payload['codex_session_path'] == str(existing_log)
    assert payload['codex_start_cmd'].endswith('resume existing-session-id')


def test_binding_runtime_alive_uses_tmux_socket_and_active_pane(monkeypatch) -> None:
    calls: list[tuple[str | None, str]] = []

    class FakeTmuxBackend:
        def __init__(self, *, socket_name: str | None = None, socket_path: str | None = None):
            self.socket_name = socket_name
            self.socket_path = socket_path

        def is_tmux_pane_alive(self, pane_id: str) -> bool:
            calls.append((self.socket_name, pane_id))
            return self.socket_name == 'sock-agent' and pane_id == '%77'

    monkeypatch.setattr('cli.services.runtime_launch.TmuxBackend', FakeTmuxBackend)

    binding = AgentBinding(
        runtime_ref='tmux:%41',
        session_ref='session-1',
        tmux_socket_name='sock-agent',
        pane_id='%41',
        active_pane_id='%77',
    )

    assert runtime_launch._binding_runtime_alive(binding) is True
    assert calls == [('sock-agent', '%77')]


def test_binding_runtime_alive_rejects_title_based_runtime_ref(monkeypatch) -> None:
    calls: list[str] = []

    class FakeTmuxBackend:
        def __init__(self, *, socket_name: str | None = None, socket_path: str | None = None):
            self.socket_name = socket_name
            self.socket_path = socket_path

        def is_tmux_pane_alive(self, pane_id: str) -> bool:
            calls.append(pane_id)
            return True

    monkeypatch.setattr('cli.services.runtime_launch.TmuxBackend', FakeTmuxBackend)

    binding = AgentBinding(
        runtime_ref='tmux:title:CCB-agent1-demo',
        session_ref='session-1',
        tmux_socket_name='sock-agent',
        pane_title_marker='CCB-agent1-demo',
    )

    assert runtime_launch._binding_runtime_alive(binding) is False
    assert calls == []


def test_ensure_agent_runtime_resumes_named_codex_session_by_agent_name(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-codex-resume'
    ccb_dir = project_root / '.ccb'
    ccb_dir.mkdir(parents=True)
    (ccb_dir / '.codex-agent1-session').write_text(
        json.dumps({'codex_session_id': 'agent1-session-id'}, ensure_ascii=False),
        encoding='utf-8',
    )
    (ccb_dir / '.codex-agent2-session').write_text(
        json.dumps({'codex_session_id': 'agent2-session-id'}, ensure_ascii=False),
        encoding='utf-8',
    )
    ctx = _context(project_root, ParsedStartCommand(project=None, agent_names=('agent1',), restore=True, auto_permission=False))
    spec = _spec('agent1')
    plan = WorkspacePlanner().plan(spec, ctx.project)
    plan.workspace_path.mkdir(parents=True, exist_ok=True)

    tmux_state: dict[str, object] = {}

    class FakeTmuxBackend:
        def create_pane(self, cmd: str, cwd: str, direction: str = 'right', percent: int = 50, parent_pane: str | None = None) -> str:
            tmux_state['cmd'] = cmd
            tmux_state['cwd'] = cwd
            return '%52'

        def set_pane_title(self, pane_id: str, title: str) -> None:
            tmux_state['title'] = (pane_id, title)

        def set_pane_user_option(self, pane_id: str, name: str, value: str) -> None:
            tmux_state['user_option'] = (pane_id, name, value)

        def _tmux_run(self, args, capture=False, timeout=None):
            return subprocess.CompletedProcess(args=args, returncode=0, stdout='5252\n', stderr='')

    class FakePopen:
        def __init__(self, args, **kwargs):
            self.pid = 9912

    monkeypatch.setattr('cli.services.runtime_launch._inside_tmux', lambda: True)
    monkeypatch.setattr('cli.services.runtime_launch.shutil.which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr('cli.services.runtime_launch.TmuxBackend', FakeTmuxBackend)
    monkeypatch.setattr('cli.services.runtime_launch.subprocess.Popen', FakePopen)

    result = ensure_agent_runtime(ctx, ctx.command, spec, plan, None)

    assert result.launched is True
    assert result.binding is not None
    assert result.binding.runtime_ref == 'tmux:%52'
    assert str(tmux_state['cmd']).endswith('resume agent1-session-id')
    assert 'agent2-session-id' not in str(tmux_state['cmd'])
    payload = json.loads((project_root / '.ccb' / '.codex-agent1-session').read_text(encoding='utf-8'))
    assert payload['codex_start_cmd'].endswith('resume agent1-session-id')


def test_ensure_agent_runtime_launches_named_gemini_session(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-gemini'
    (project_root / '.ccb').mkdir(parents=True)
    ctx = _context(project_root, ParsedStartCommand(project=None, agent_names=('reviewer',), restore=True, auto_permission=True))
    spec = _spec('reviewer', provider='gemini')
    plan = WorkspacePlanner().plan(spec, ctx.project)
    plan.workspace_path.mkdir(parents=True, exist_ok=True)

    tmux_state: dict[str, object] = {}

    class FakeTmuxBackend:
        def create_pane(self, cmd: str, cwd: str, direction: str = 'right', percent: int = 50, parent_pane: str | None = None) -> str:
            tmux_state['cmd'] = cmd
            tmux_state['cwd'] = cwd
            return '%55'

        def set_pane_title(self, pane_id: str, title: str) -> None:
            tmux_state['title'] = (pane_id, title)

        def set_pane_user_option(self, pane_id: str, name: str, value: str) -> None:
            tmux_state['user_option'] = (pane_id, name, value)

    resume_dir = tmp_path / 'gemini-resume'
    resume_dir.mkdir()

    monkeypatch.setattr('cli.services.runtime_launch._inside_tmux', lambda: True)
    monkeypatch.setattr('cli.services.runtime_launch.shutil.which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr('cli.services.runtime_launch.TmuxBackend', FakeTmuxBackend)
    monkeypatch.setattr(
        gemini_launcher,
        '_resolve_gemini_restore_target',
        lambda **kwargs: ProviderRestoreTarget(run_cwd=resume_dir, has_history=True),
    )

    result = ensure_agent_runtime(ctx, ctx.command, spec, plan, None)

    assert result.launched is True
    assert result.binding is not None
    assert result.binding.runtime_ref == 'tmux:%55'
    expected_session = project_root / '.ccb' / '.gemini-reviewer-session'
    assert result.binding.session_ref == str(expected_session)
    payload = json.loads(expected_session.read_text(encoding='utf-8'))
    assert payload['agent_name'] == 'reviewer'
    assert payload['ccb_project_id'] == ctx.project.project_id
    assert payload['completion_artifact_dir'] == str(ctx.paths.agent_dir('reviewer') / 'provider-runtime' / 'gemini' / 'completion')
    assert payload['pane_title_marker'].startswith('CCB-reviewer-')
    assert payload['pane_id'] == '%55'
    assert payload['work_dir'] == str(resume_dir)
    _assert_caller_env_exports(
        payload['start_cmd'],
        actor='reviewer',
        runtime_dir=ctx.paths.agent_dir('reviewer') / 'provider-runtime' / 'gemini',
        session_id=payload['ccb_session_id'],
    )
    assert payload['start_cmd'].endswith('gemini --yolo --resume latest')
    assert tmux_state['cwd'] == str(resume_dir)
    assert tmux_state['title'] == ('%55', 'reviewer')
    assert tmux_state['user_option'] == ('%55', '@ccb_project_id', ctx.project.project_id)


def test_ensure_agent_runtime_launches_named_claude_session(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-claude'
    (project_root / '.ccb').mkdir(parents=True)
    ctx = _context(project_root, ParsedStartCommand(project=None, agent_names=('reviewer',), restore=True, auto_permission=True))
    spec = _spec('reviewer', provider='claude')
    plan = WorkspacePlanner().plan(spec, ctx.project)
    plan.workspace_path.mkdir(parents=True, exist_ok=True)

    tmux_state: dict[str, object] = {}

    class FakeTmuxBackend:
        def create_pane(self, cmd: str, cwd: str, direction: str = 'right', percent: int = 50, parent_pane: str | None = None) -> str:
            tmux_state['cmd'] = cmd
            tmux_state['cwd'] = cwd
            return '%44'

        def set_pane_title(self, pane_id: str, title: str) -> None:
            tmux_state['title'] = (pane_id, title)

        def set_pane_user_option(self, pane_id: str, name: str, value: str) -> None:
            tmux_state['user_option'] = (pane_id, name, value)

    resume_dir = tmp_path / 'claude-resume'
    resume_dir.mkdir()

    monkeypatch.setattr('cli.services.runtime_launch._inside_tmux', lambda: True)
    monkeypatch.setattr('cli.services.runtime_launch.shutil.which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr('cli.services.runtime_launch.TmuxBackend', FakeTmuxBackend)
    monkeypatch.setattr(
        claude_launcher,
        '_resolve_claude_restore_target',
        lambda **kwargs: ProviderRestoreTarget(run_cwd=resume_dir, has_history=True),
    )
    monkeypatch.setattr(
        'provider_backends.claude.launcher.write_claude_settings_overlay',
        lambda runtime_dir, profile=None: runtime_dir / 'claude-settings.json',
    )
    monkeypatch.setattr(
        'provider_backends.claude.launcher.build_claude_env_prefix',
        lambda profile=None, extra_env=None: 'unset ANTHROPIC_BASE_URL',
    )

    result = ensure_agent_runtime(ctx, ctx.command, spec, plan, None)

    assert result.launched is True
    assert result.binding is not None
    assert result.binding.runtime_ref == 'tmux:%44'
    expected_session = project_root / '.ccb' / '.claude-reviewer-session'
    assert result.binding.session_ref == str(expected_session)
    payload = json.loads(expected_session.read_text(encoding='utf-8'))
    expected_claude_home = ctx.paths.agent_provider_state_dir('reviewer', 'claude') / 'home'
    assert payload['agent_name'] == 'reviewer'
    assert payload['ccb_project_id'] == ctx.project.project_id
    assert payload['completion_artifact_dir'] == str(ctx.paths.agent_dir('reviewer') / 'provider-runtime' / 'claude' / 'completion')
    assert payload['claude_home'] == str(expected_claude_home)
    assert payload['claude_projects_root'] == str(expected_claude_home / '.claude' / 'projects')
    assert payload['claude_session_env_root'] == str(expected_claude_home / '.claude' / 'session-env')
    assert payload['pane_title_marker'].startswith('CCB-reviewer-')
    assert payload['pane_id'] == '%44'
    assert payload['work_dir'] == str(resume_dir)
    assert payload['ccb_session_id'].startswith('ccb-reviewer-')
    assert tmux_state['cwd'] == str(resume_dir)
    managed_memory = expected_claude_home / '.claude' / 'CLAUDE.md'
    assert f'workspace_path: {resume_dir.resolve()}' in managed_memory.read_text(encoding='utf-8')
    assert payload['start_cmd'].startswith('unset ANTHROPIC_BASE_URL; ')
    assert f'HOME={shlex.quote(str(expected_claude_home))}' in payload['start_cmd']
    assert f'CLAUDE_PROJECTS_ROOT={shlex.quote(str(expected_claude_home / ".claude" / "projects"))}' in payload['start_cmd']
    _assert_caller_env_exports(
        payload['start_cmd'],
        actor='reviewer',
        runtime_dir=ctx.paths.agent_dir('reviewer') / 'provider-runtime' / 'claude',
        session_id=payload['ccb_session_id'],
    )
    assert payload['start_cmd'].endswith(
        f'claude --setting-sources user,project,local --settings '
        f'{shlex.quote(str(ctx.paths.agent_dir("reviewer") / "provider-runtime" / "claude" / "claude-settings.json"))} '
        '--permission-mode bypassPermissions --continue'
    )
    settings_payload = json.loads(
        (ctx.paths.agent_dir('reviewer') / 'provider-runtime' / 'claude' / 'claude-settings.json').read_text(
            encoding='utf-8'
        )
    )
    assert settings_payload['skipDangerousModePermissionPrompt'] is True
    assert tmux_state['title'] == ('%44', 'reviewer')
    assert tmux_state['user_option'] == ('%44', '@ccb_project_id', ctx.project.project_id)


def test_ensure_agent_runtime_launches_named_opencode_session(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-opencode'
    (project_root / '.ccb').mkdir(parents=True)
    ctx = _context(project_root, ParsedStartCommand(project=None, agent_names=('builder',), restore=True, auto_permission=False))
    spec = _spec('builder', provider='opencode')
    plan = WorkspacePlanner().plan(spec, ctx.project)
    plan.workspace_path.mkdir(parents=True, exist_ok=True)

    class FakeTmuxBackend:
        def create_pane(self, cmd: str, cwd: str, direction: str = 'right', percent: int = 50, parent_pane: str | None = None) -> str:
            self.cmd = cmd
            self.cwd = cwd
            return '%66'

        def set_pane_title(self, pane_id: str, title: str) -> None:
            self.title = (pane_id, title)

        def set_pane_user_option(self, pane_id: str, name: str, value: str) -> None:
            self.user_option = (pane_id, name, value)

    monkeypatch.setattr('cli.services.runtime_launch._inside_tmux', lambda: True)
    monkeypatch.setattr('cli.services.runtime_launch.shutil.which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr('cli.services.runtime_launch.TmuxBackend', FakeTmuxBackend)
    monkeypatch.setenv('OPENCODE_START_CMD', 'opencode')

    result = ensure_agent_runtime(ctx, ctx.command, spec, plan, None)

    assert result.launched is True
    assert result.binding is not None
    expected_session = project_root / '.ccb' / '.opencode-builder-session'
    assert result.binding.session_ref == str(expected_session)
    payload = json.loads(expected_session.read_text(encoding='utf-8'))
    assert payload['pane_title_marker'].startswith('CCB-builder-')
    config_path = ctx.paths.agent_provider_state_dir('builder', 'opencode') / 'opencode.json'
    assert f'OPENCODE_CONFIG={shlex.quote(str(config_path))}' in payload['start_cmd']
    _assert_caller_env_exports(
        payload['start_cmd'],
        actor='builder',
        runtime_dir=ctx.paths.agent_dir('builder') / 'provider-runtime' / 'opencode',
        session_id=payload['ccb_session_id'],
    )
    assert payload['start_cmd'].endswith('opencode --continue')
    assert payload['ccb_session_id'].startswith('ccb-builder-')
    assert config_path.is_file()


def test_ensure_agent_runtime_uses_assigned_tmux_pane(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-assigned'
    (project_root / '.ccb').mkdir(parents=True)
    ctx = _context(project_root, ParsedStartCommand(project=None, agent_names=('agent1',), restore=False, auto_permission=False))
    spec = _spec('agent1')
    plan = WorkspacePlanner().plan(spec, ctx.project)
    plan.workspace_path.mkdir(parents=True, exist_ok=True)

    tmux_state: dict[str, object] = {'options': [], 'styles': []}

    class FakeTmuxBackend:
        def respawn_pane(self, pane_id: str, *, cmd: str, cwd: str | None = None, remain_on_exit: bool = True) -> None:
            tmux_state['respawn'] = (pane_id, cmd, cwd, remain_on_exit)

        def set_pane_title(self, pane_id: str, title: str) -> None:
            tmux_state['title'] = (pane_id, title)

        def set_pane_user_option(self, pane_id: str, name: str, value: str) -> None:
            tmux_state['options'].append((pane_id, name, value))

        def set_pane_style(
            self,
            pane_id: str,
            *,
            border_style: str | None = None,
            active_border_style: str | None = None,
        ) -> None:
            tmux_state['styles'].append((pane_id, border_style, active_border_style))

        def _tmux_run(self, args, capture=False, timeout=None):
            return subprocess.CompletedProcess(args=args, returncode=0, stdout='4343\n', stderr='')

    spawned: dict[str, object] = {}

    class FakePopen:
        def __init__(self, args, **kwargs):
            spawned['args'] = args
            self.pid = 9913

    monkeypatch.setattr('cli.services.runtime_launch._inside_tmux', lambda: True)
    monkeypatch.setattr('cli.services.runtime_launch.shutil.which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr('cli.services.runtime_launch.TmuxBackend', FakeTmuxBackend)
    monkeypatch.setattr('cli.services.runtime_launch.subprocess.Popen', FakePopen)

    result = ensure_agent_runtime(ctx, ctx.command, spec, plan, None, assigned_pane_id='%43', style_index=2)

    assert result.launched is True
    assert result.binding is not None
    assert result.binding.runtime_ref == 'tmux:%43'
    assert tmux_state['respawn'][0] == '%43'
    assert tmux_state['respawn'][2] == str(plan.workspace_path)
    visual = pane_visual(project_id=ctx.project.project_id, slot_key='agent1', order_index=0)
    assert ('%43', '@ccb_label_style', visual.label_style) in tmux_state['options']
    assert ('%43', '@ccb_agent', 'agent1') in tmux_state['options']
    assert ('%43', '@ccb_project_id', ctx.project.project_id) in tmux_state['options']
    session_option = next(value for pane, name, value in tmux_state['options'] if pane == '%43' and name == '@ccb_session_id')
    assert session_option.startswith('ccb-agent1-')
    assert ('%43', visual.border_style, visual.active_border_style) in tmux_state['styles']


def test_ensure_agent_runtime_launches_named_droid_session(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-droid'
    (project_root / '.ccb').mkdir(parents=True)
    ctx = _context(project_root, ParsedStartCommand(project=None, agent_names=('mobile',), restore=True, auto_permission=False))
    spec = _spec('mobile', provider='droid')
    plan = WorkspacePlanner().plan(spec, ctx.project)
    plan.workspace_path.mkdir(parents=True, exist_ok=True)

    class FakeTmuxBackend:
        def create_pane(self, cmd: str, cwd: str, direction: str = 'right', percent: int = 50, parent_pane: str | None = None) -> str:
            self.cmd = cmd
            self.cwd = cwd
            return '%77'

        def set_pane_title(self, pane_id: str, title: str) -> None:
            self.title = (pane_id, title)

        def set_pane_user_option(self, pane_id: str, name: str, value: str) -> None:
            self.user_option = (pane_id, name, value)

    monkeypatch.setattr('cli.services.runtime_launch._inside_tmux', lambda: True)
    monkeypatch.setattr('cli.services.runtime_launch.shutil.which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr('cli.services.runtime_launch.TmuxBackend', FakeTmuxBackend)
    monkeypatch.setenv('DROID_START_CMD', 'droid')

    result = ensure_agent_runtime(ctx, ctx.command, spec, plan, None)

    assert result.launched is True
    assert result.binding is not None
    expected_session = project_root / '.ccb' / '.droid-mobile-session'
    assert result.binding.session_ref == str(expected_session)
    payload = json.loads(expected_session.read_text(encoding='utf-8'))
    assert payload['pane_title_marker'].startswith('CCB-mobile-')
    expected_home = ctx.paths.agent_provider_state_dir('mobile', 'droid') / 'home'
    assert payload['droid_home'] == str(expected_home)
    assert payload['factory_home'] == str(expected_home)
    assert payload['droid_sessions_root'] == str(expected_home / 'sessions')
    assert (expected_home / 'sessions').is_dir()
    assert f'FACTORY_HOME={shlex.quote(str(expected_home))}' in payload['start_cmd']
    assert f'DROID_SESSIONS_ROOT={shlex.quote(str(expected_home / "sessions"))}' in payload['start_cmd']
    _assert_caller_env_exports(
        payload['start_cmd'],
        actor='mobile',
        runtime_dir=ctx.paths.agent_dir('mobile') / 'provider-runtime' / 'droid',
        session_id=payload['ccb_session_id'],
    )
    assert payload['start_cmd'].endswith('droid -r')
    assert payload['ccb_session_id'].startswith('ccb-mobile-')


def test_ensure_agent_runtime_falls_back_to_detached_tmux_session(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    (project_root / '.ccb').mkdir(parents=True)
    ctx = _context(project_root, ParsedStartCommand(project=None, agent_names=('agent1',), restore=False, auto_permission=False))
    spec = _spec('agent1')
    plan = WorkspacePlanner().plan(spec, ctx.project)
    plan.workspace_path.mkdir(parents=True, exist_ok=True)

    calls: list[tuple[str, tuple[object, ...]]] = []

    class FakeTmuxBackend:
        def create_pane(self, cmd: str, cwd: str, direction: str = 'right', percent: int = 50, parent_pane: str | None = None) -> str:
            raise RuntimeError('tmux split-window failed (exit 1): no space for new pane')

        def set_pane_title(self, pane_id: str, title: str) -> None:
            calls.append(('title', (pane_id, title)))

        def set_pane_user_option(self, pane_id: str, name: str, value: str) -> None:
            calls.append(('option', (pane_id, name, value)))

        def respawn_pane(self, pane_id: str, *, cmd: str, cwd: str | None = None, remain_on_exit: bool = True) -> None:
            calls.append(('respawn', (pane_id, cmd, cwd, remain_on_exit)))

        def _tmux_run(self, args, capture=False, timeout=None, check=False):
            if args == ['start-server']:
                calls.append(('start-server', tuple(args)))
                return subprocess.CompletedProcess(args=args, returncode=0, stdout='', stderr='')
            if args[:2] == ['set-option', '-g']:
                calls.append(('set-option', tuple(args)))
                return subprocess.CompletedProcess(args=args, returncode=0, stdout='', stderr='')
            if args[:2] == ['set-window-option', '-g']:
                calls.append(('set-window-option', tuple(args)))
                return subprocess.CompletedProcess(args=args, returncode=0, stdout='', stderr='')
            if args[:1] == ['bind-key']:
                calls.append(('bind-key', tuple(args)))
                return subprocess.CompletedProcess(args=args, returncode=0, stdout='', stderr='')
            if args[:2] == ['new-session', '-d']:
                calls.append(('new-session', tuple(args)))
                return subprocess.CompletedProcess(args=args, returncode=0, stdout='', stderr='')
            if args[:2] == ['list-panes', '-t']:
                return subprocess.CompletedProcess(args=args, returncode=0, stdout='%88\n', stderr='')
            if args[:2] == ['display-message', '-p']:
                return subprocess.CompletedProcess(args=args, returncode=0, stdout='8800\n', stderr='')
            raise AssertionError(args)

    class FakePopen:
        def __init__(self, args, **kwargs):
            self.pid = 2222

    monkeypatch.setattr('cli.services.runtime_launch._inside_tmux', lambda: True)
    monkeypatch.setattr('cli.services.runtime_launch.shutil.which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr('cli.services.runtime_launch.TmuxBackend', FakeTmuxBackend)
    monkeypatch.setattr('cli.services.runtime_launch.subprocess.Popen', FakePopen)
    monkeypatch.setattr('cli.services.runtime_launch._pane_meets_minimum_size', lambda backend, pane_id: True)

    result = ensure_agent_runtime(ctx, ctx.command, spec, plan, None)

    assert result.binding is not None
    assert result.binding.runtime_ref == 'tmux:%88'
    assert any(name == 'start-server' for name, _ in calls)
    assert any(name == 'set-option' for name, _ in calls)
    assert ('set-option', ('set-option', '-g', 'mouse', 'on')) in calls
    assert ('set-option', ('set-option', '-g', 'set-clipboard', 'on')) in calls
    assert ('set-window-option', ('set-window-option', '-g', 'mode-keys', 'vi')) in calls
    assert ('bind-key', ('bind-key', '-T', 'copy-mode-vi', 'v', 'send-keys', '-X', 'begin-selection')) in calls
    assert _clipboard_bind_call('y') in calls
    assert _clipboard_bind_call('MouseDragEnd1Pane') in calls
    assert ('bind-key', ('bind-key', 'h', 'select-pane', '-L')) in calls
    assert any(name == 'new-session' for name, _ in calls)
    assert any(name == 'respawn' for name, _ in calls)


def test_ensure_agent_runtime_refuses_detached_fallback_inside_project_namespace(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-namespace-no-detached'
    (project_root / '.ccb').mkdir(parents=True)
    ctx = _context(project_root, ParsedStartCommand(project=None, agent_names=('agent1',), restore=False, auto_permission=False))
    spec = _spec('agent1')
    plan = WorkspacePlanner().plan(spec, ctx.project)
    plan.workspace_path.mkdir(parents=True, exist_ok=True)

    class FakeTmuxBackend:
        def __init__(self, *, socket_name: str | None = None, socket_path: str | None = None) -> None:
            self.socket_name = socket_name
            self.socket_path = socket_path

    monkeypatch.setattr('cli.services.runtime_launch._inside_tmux', lambda: True)
    monkeypatch.setattr('cli.services.runtime_launch.shutil.which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr('cli.services.runtime_launch.TmuxBackend', FakeTmuxBackend)

    with pytest.raises(RuntimeError, match='project namespace launch requires assigned tmux pane'):
        ensure_agent_runtime(
            ctx,
            ctx.command,
            spec,
            plan,
            None,
            tmux_socket_path=str(project_root / '.ccb' / 'ccbd' / 'tmux.sock'),
        )


def test_ensure_agent_runtime_relaunches_when_existing_binding_pane_is_dead(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-dead-binding'
    (project_root / '.ccb').mkdir(parents=True)
    ctx = _context(project_root, ParsedStartCommand(project=None, agent_names=('reviewer',), restore=False, auto_permission=False))
    spec = _spec('reviewer', provider='gemini')
    plan = WorkspacePlanner().plan(spec, ctx.project)
    plan.workspace_path.mkdir(parents=True, exist_ok=True)
    tmux_state: dict[str, object] = {'killed': []}

    class FakeTmuxBackend:
        def __init__(self, *, socket_name: str | None = None, socket_path: str | None = None) -> None:
            self.socket_name = socket_name
            self.socket_path = socket_path

        def is_tmux_pane_alive(self, pane_id: str) -> bool:
            return False

        def kill_tmux_pane(self, pane_id: str) -> None:
            tmux_state['killed'].append((self.socket_name, pane_id))

        def create_pane(self, cmd: str, cwd: str, direction: str = 'right', percent: int = 50, parent_pane: str | None = None) -> str:
            self.cmd = cmd
            self.cwd = cwd
            return '%91'

        def set_pane_title(self, pane_id: str, title: str) -> None:
            self.title = (pane_id, title)

        def set_pane_user_option(self, pane_id: str, name: str, value: str) -> None:
            self.user_option = (pane_id, name, value)

    monkeypatch.setattr('cli.services.runtime_launch._inside_tmux', lambda: True)
    monkeypatch.setattr('cli.services.runtime_launch.shutil.which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr('cli.services.runtime_launch.TmuxBackend', FakeTmuxBackend)

    result = ensure_agent_runtime(
        ctx,
        ctx.command,
        spec,
        plan,
        AgentBinding(
            runtime_ref='tmux:%44',
            session_ref=str(project_root / '.ccb' / '.gemini-reviewer-session'),
            tmux_socket_name='sock-dead',
            pane_id='%44',
            pane_state='dead',
        ),
    )

    assert result.launched is True
    assert result.binding is not None
    assert result.binding.runtime_ref == 'tmux:%91'
    assert tmux_state['killed'] == [('sock-dead', '%44')]


def test_ensure_agent_runtime_outside_tmux_relaunches_stale_binding_via_detached_session(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-outside-tmux-stale'
    (project_root / '.ccb').mkdir(parents=True)
    ctx = _context(project_root, ParsedStartCommand(project=None, agent_names=('agent1',), restore=False, auto_permission=False))
    spec = _spec('agent1')
    plan = WorkspacePlanner().plan(spec, ctx.project)
    plan.workspace_path.mkdir(parents=True, exist_ok=True)

    calls: list[tuple[str, tuple[object, ...]]] = []

    class FakeTmuxBackend:
        def __init__(self, *, socket_name: str | None = None, socket_path: str | None = None) -> None:
            self.socket_name = socket_name
            self.socket_path = socket_path

        def is_tmux_pane_alive(self, pane_id: str) -> bool:
            return False

        def kill_tmux_pane(self, pane_id: str) -> None:
            calls.append(('kill', (self.socket_name, pane_id)))

        def create_pane(self, cmd: str, cwd: str, direction: str = 'right', percent: int = 50, parent_pane: str | None = None) -> str:
            raise RuntimeError('tmux split-window failed (exit 1): no space for new pane')

        def set_pane_title(self, pane_id: str, title: str) -> None:
            calls.append(('title', (pane_id, title)))

        def set_pane_user_option(self, pane_id: str, name: str, value: str) -> None:
            calls.append(('option', (pane_id, name, value)))

        def respawn_pane(self, pane_id: str, *, cmd: str, cwd: str | None = None, remain_on_exit: bool = True) -> None:
            calls.append(('respawn', (pane_id, cmd, cwd, remain_on_exit)))

        def _tmux_run(self, args, capture=False, timeout=None, check=False):
            if args == ['start-server']:
                calls.append(('start-server', tuple(args)))
                return subprocess.CompletedProcess(args=args, returncode=0, stdout='', stderr='')
            if args[:2] == ['set-option', '-g']:
                calls.append(('set-option', tuple(args)))
                return subprocess.CompletedProcess(args=args, returncode=0, stdout='', stderr='')
            if args[:2] == ['set-window-option', '-g']:
                calls.append(('set-window-option', tuple(args)))
                return subprocess.CompletedProcess(args=args, returncode=0, stdout='', stderr='')
            if args[:1] == ['bind-key']:
                calls.append(('bind-key', tuple(args)))
                return subprocess.CompletedProcess(args=args, returncode=0, stdout='', stderr='')
            if args[:2] == ['new-session', '-d']:
                calls.append(('new-session', tuple(args)))
                return subprocess.CompletedProcess(args=args, returncode=0, stdout='', stderr='')
            if args[:2] == ['list-panes', '-t']:
                return subprocess.CompletedProcess(args=args, returncode=0, stdout='%88\n', stderr='')
            if args[:2] == ['display-message', '-p']:
                return subprocess.CompletedProcess(args=args, returncode=0, stdout='8800\n', stderr='')
            raise AssertionError(args)

    class FakePopen:
        def __init__(self, args, **kwargs):
            self.pid = 2222

    monkeypatch.setattr('cli.services.runtime_launch._inside_tmux', lambda: False)
    monkeypatch.setattr('cli.services.runtime_launch.shutil.which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr('cli.services.runtime_launch.TmuxBackend', FakeTmuxBackend)
    monkeypatch.setattr('cli.services.runtime_launch.subprocess.Popen', FakePopen)
    monkeypatch.setattr('cli.services.runtime_launch._pane_meets_minimum_size', lambda backend, pane_id: True)

    result = ensure_agent_runtime(
        ctx,
        ctx.command,
        spec,
        plan,
        AgentBinding(
            runtime_ref='tmux:%44',
            session_ref=str(project_root / '.ccb' / '.codex-agent1-session'),
            tmux_socket_name='sock-dead',
            pane_id='%44',
            pane_state='dead',
        ),
    )

    assert result.launched is True
    assert result.binding is not None
    assert result.binding.runtime_ref == 'tmux:%88'
    assert ('kill', ('sock-dead', '%44')) in calls
    assert any(name == 'start-server' for name, _ in calls)
    assert ('set-option', ('set-option', '-g', 'mouse', 'on')) in calls
    assert ('set-option', ('set-option', '-g', 'set-clipboard', 'on')) in calls
    assert ('set-window-option', ('set-window-option', '-g', 'mode-keys', 'vi')) in calls
    assert ('bind-key', ('bind-key', '-T', 'copy-mode-vi', 'y', 'send-keys', '-X', 'copy-selection-and-cancel')) not in calls
    assert _clipboard_bind_call('y') in calls
    assert _clipboard_bind_call('Enter') in calls
    assert ('bind-key', ('bind-key', '-r', 'L', 'resize-pane', '-R', '5')) in calls
    assert any(name == 'new-session' for name, _ in calls)
    assert any(name == 'respawn' for name, _ in calls)


def test_ensure_agent_runtime_relaunches_live_foreign_binding_without_killing_foreign_pane(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-foreign-binding'
    (project_root / '.ccb').mkdir(parents=True)
    ctx = _context(project_root, ParsedStartCommand(project=None, agent_names=('agent1',), restore=False, auto_permission=False))
    spec = _spec('agent1')
    plan = WorkspacePlanner().plan(spec, ctx.project)
    plan.workspace_path.mkdir(parents=True, exist_ok=True)

    calls: list[tuple[str, tuple[object, ...]]] = []

    class FakeTmuxBackend:
        def __init__(self, *, socket_name: str | None = None, socket_path: str | None = None) -> None:
            self.socket_name = socket_name
            self.socket_path = socket_path

        def is_tmux_pane_alive(self, pane_id: str) -> bool:
            return True

        def kill_tmux_pane(self, pane_id: str) -> None:
            calls.append(('kill', (self.socket_name, pane_id)))

        def create_pane(self, cmd: str, cwd: str, direction: str = 'right', percent: int = 50, parent_pane: str | None = None) -> str:
            calls.append(('create', (cmd, cwd, direction, percent, parent_pane)))
            return '%91'

        def set_pane_title(self, pane_id: str, title: str) -> None:
            calls.append(('title', (pane_id, title)))

        def set_pane_user_option(self, pane_id: str, name: str, value: str) -> None:
            calls.append(('option', (pane_id, name, value)))

    class FakePopen:
        def __init__(self, args, **kwargs):
            self.pid = 1234

    monkeypatch.setattr('cli.services.runtime_launch._inside_tmux', lambda: True)
    monkeypatch.setattr('cli.services.runtime_launch.shutil.which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr('cli.services.runtime_launch.TmuxBackend', FakeTmuxBackend)
    monkeypatch.setattr('cli.services.runtime_launch.subprocess.Popen', FakePopen)

    result = ensure_agent_runtime(
        ctx,
        ctx.command,
        spec,
        plan,
        AgentBinding(
            runtime_ref='tmux:%44',
            session_ref=str(project_root / '.ccb' / '.codex-agent1-session'),
            tmux_socket_name='sock-foreign',
            pane_id='%44',
            pane_state='foreign',
        ),
    )

    assert result.launched is True
    assert result.binding is not None
    assert result.binding.runtime_ref == 'tmux:%91'
    assert not any(name == 'kill' for name, _ in calls)
    assert any(name == 'create' for name, _ in calls)


def test_ensure_agent_runtime_raises_when_launch_does_not_produce_usable_binding(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-missing-binding'
    (project_root / '.ccb').mkdir(parents=True)
    ctx = _context(project_root, ParsedStartCommand(project=None, agent_names=('agent1',), restore=False, auto_permission=False))
    spec = _spec('agent1')
    plan = WorkspacePlanner().plan(spec, ctx.project)
    plan.workspace_path.mkdir(parents=True, exist_ok=True)

    class FakeTmuxBackend:
        def create_pane(self, cmd: str, cwd: str, direction: str = 'right', percent: int = 50, parent_pane: str | None = None) -> str:
            return '%42'

        def set_pane_title(self, pane_id: str, title: str) -> None:
            pass

        def set_pane_user_option(self, pane_id: str, name: str, value: str) -> None:
            pass

    class FakePopen:
        def __init__(self, args, **kwargs):
            self.pid = 9911

    monkeypatch.setattr('cli.services.runtime_launch._inside_tmux', lambda: True)
    monkeypatch.setattr('cli.services.runtime_launch.shutil.which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr('cli.services.runtime_launch.TmuxBackend', FakeTmuxBackend)
    monkeypatch.setattr('cli.services.runtime_launch.subprocess.Popen', FakePopen)
    monkeypatch.setattr('cli.services.runtime_launch.resolve_agent_binding', lambda **kwargs: None)

    with pytest.raises(RuntimeError, match='failed to resolve usable binding'):
        ensure_agent_runtime(ctx, ctx.command, spec, plan, None)


def test_codex_post_launch_requires_declared_runtime_artifacts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    runtime_dir = tmp_path / 'codex-runtime'
    codex_launcher.prepare_runtime(runtime_dir)

    class FakeTmuxBackend:
        def _tmux_run(self, args, capture=False, timeout=None):
            return subprocess.CompletedProcess(args=args, returncode=0, stdout='4242\n', stderr='')

    monkeypatch.setattr('provider_backends.codex.launcher_runtime.bridge.spawn_codex_bridge', lambda **kwargs: None)

    with pytest.raises(RuntimeError, match='bridge.pid'):
        codex_launcher.post_launch(FakeTmuxBackend(), '%42', runtime_dir, 'ccb-agent1-test', {})


def test_inside_tmux_detects_tmux_session_without_extra_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('TMUX', '/tmp/tmux-1000/default,1,0')
    monkeypatch.delenv('TMUX_PANE', raising=False)

    assert runtime_launch._inside_tmux() is True


def test_inside_tmux_detects_tmux_pane_without_extra_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('TMUX', raising=False)
    monkeypatch.setenv('TMUX_PANE', '%7')

    assert runtime_launch._inside_tmux() is True


def test_provider_start_parts_respect_env_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('GEMINI_START_CMD', '/tmp/stub-gemini --flag')
    monkeypatch.setenv('CLAUDE_START_CMD', '/tmp/stub-claude')
    monkeypatch.setenv('CODEX_START_CMD', '/tmp/stub-codex --profile test')
    monkeypatch.setenv('AGY_START_CMD', '/tmp/stub-agy --sandbox')

    assert runtime_launch._provider_start_parts('gemini') == ['/tmp/stub-gemini', '--flag']
    assert runtime_launch._provider_start_parts('claude') == ['/tmp/stub-claude']
    assert runtime_launch._provider_start_parts('codex') == ['/tmp/stub-codex', '--profile', 'test']
    assert runtime_launch._provider_start_parts('agy') == ['/tmp/stub-agy', '--sandbox']
    assert runtime_launch._provider_executable('codex') == '/tmp/stub-codex'


def test_provider_start_parts_fall_back_to_default_binary(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv('GEMINI_START_CMD', raising=False)
    monkeypatch.delenv('CLAUDE_START_CMD', raising=False)
    monkeypatch.delenv('CODEX_START_CMD', raising=False)
    monkeypatch.delenv('AGY_START_CMD', raising=False)

    assert runtime_launch._provider_start_parts('gemini') == ['gemini']
    assert runtime_launch._provider_start_parts('claude') == ['claude']
    assert runtime_launch._provider_start_parts('codex') == ['codex']
    assert runtime_launch._provider_start_parts('agy') == ['agy']


def test_agy_start_cmd_defaults_to_yolo_permission(tmp_path: Path) -> None:
    from provider_backends.agy import launcher as agy_launcher

    spec = _spec('debugger', provider='agy')
    cmd = agy_launcher.build_start_cmd(
        ParsedStartCommand(project=None, agent_names=('debugger',), restore=False, auto_permission=True),
        spec,
        tmp_path / 'runtime',
        'ccb-debugger-test',
    )

    assert 'agy --dangerously-skip-permissions' in cmd


def test_agy_start_cmd_uses_continue_for_restore(tmp_path: Path) -> None:
    from provider_backends.agy import launcher as agy_launcher

    spec = _spec('debugger', provider='agy')
    cmd = agy_launcher.build_start_cmd(
        ParsedStartCommand(project=None, agent_names=('debugger',), restore=True, auto_permission=False),
        spec,
        tmp_path / 'runtime',
        'ccb-debugger-test',
    )

    assert 'agy --continue' in cmd


def test_ensure_agent_runtime_falls_back_when_created_pane_is_too_small(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    (project_root / '.ccb').mkdir(parents=True)
    ctx = _context(project_root, ParsedStartCommand(project=None, agent_names=('agent1',), restore=False, auto_permission=False))
    spec = _spec('agent1')
    plan = WorkspacePlanner().plan(spec, ctx.project)
    plan.workspace_path.mkdir(parents=True, exist_ok=True)

    calls: list[tuple[str, tuple[object, ...]]] = []

    class FakeTmuxBackend:
        def create_pane(self, cmd: str, cwd: str, direction: str = 'right', percent: int = 50, parent_pane: str | None = None) -> str:
            calls.append(('create', (cmd, cwd)))
            return '%42'

        def kill_tmux_pane(self, pane_id: str) -> None:
            calls.append(('kill', (pane_id,)))

        def set_pane_title(self, pane_id: str, title: str) -> None:
            calls.append(('title', (pane_id, title)))

        def set_pane_user_option(self, pane_id: str, name: str, value: str) -> None:
            calls.append(('option', (pane_id, name, value)))

        def respawn_pane(self, pane_id: str, *, cmd: str, cwd: str | None = None, remain_on_exit: bool = True) -> None:
            calls.append(('respawn', (pane_id, cmd, cwd, remain_on_exit)))

        def _tmux_run(self, args, capture=False, timeout=None, check=False):
            if args == ['start-server']:
                calls.append(('start-server', tuple(args)))
                return subprocess.CompletedProcess(args=args, returncode=0, stdout='', stderr='')
            if args[:2] == ['set-option', '-g']:
                calls.append(('set-option', tuple(args)))
                return subprocess.CompletedProcess(args=args, returncode=0, stdout='', stderr='')
            if args[:2] == ['set-window-option', '-g']:
                calls.append(('set-window-option', tuple(args)))
                return subprocess.CompletedProcess(args=args, returncode=0, stdout='', stderr='')
            if args[:1] == ['bind-key']:
                calls.append(('bind-key', tuple(args)))
                return subprocess.CompletedProcess(args=args, returncode=0, stdout='', stderr='')
            if args[:4] == ['new-session', '-d', '-x', '160']:
                calls.append(('new-session', tuple(args)))
                return subprocess.CompletedProcess(args=args, returncode=0, stdout='', stderr='')
            if args[:2] == ['list-panes', '-t']:
                return subprocess.CompletedProcess(args=args, returncode=0, stdout='%88\n', stderr='')
            if args[:2] == ['display-message', '-p']:
                return subprocess.CompletedProcess(args=args, returncode=0, stdout='8800\n', stderr='')
            raise AssertionError(args)

    class FakePopen:
        def __init__(self, args, **kwargs):
            self.pid = 2222

    monkeypatch.setattr('cli.services.runtime_launch._inside_tmux', lambda: True)
    monkeypatch.setattr('cli.services.runtime_launch.shutil.which', lambda name: f'/usr/bin/{name}')
    monkeypatch.setattr('cli.services.runtime_launch.TmuxBackend', FakeTmuxBackend)
    monkeypatch.setattr('cli.services.runtime_launch.subprocess.Popen', FakePopen)
    monkeypatch.setattr('cli.services.runtime_launch._pane_meets_minimum_size', lambda backend, pane_id: False)

    result = ensure_agent_runtime(ctx, ctx.command, spec, plan, None)

    assert result.binding is not None
    assert result.binding.runtime_ref == 'tmux:%88'
    assert ('kill', ('%42',)) in calls
    assert any(name == 'start-server' for name, _ in calls)
    assert any(name == 'set-option' for name, _ in calls)
    assert ('set-option', ('set-option', '-g', 'mouse', 'on')) in calls
    assert ('set-option', ('set-option', '-g', 'set-clipboard', 'on')) in calls
    assert ('set-window-option', ('set-window-option', '-g', 'mode-keys', 'vi')) in calls
    assert _clipboard_bind_call('MouseDragEnd1Pane') in calls
    assert ('bind-key', ('bind-key', 'l', 'select-pane', '-R')) in calls
    assert any(name == 'new-session' for name, _ in calls)
    assert any(name == 'respawn' for name, _ in calls)


def test_codex_launcher_build_start_cmd_isolates_invalid_global_codex_config(monkeypatch, tmp_path: Path) -> None:
    runtime_dir = tmp_path / 'runtime'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    source_home = tmp_path / 'source-home'
    source_home.mkdir(parents=True, exist_ok=True)
    (source_home / 'config.toml').write_text('[mcp_servers.puppeteer]\nfoo=1\n[mcp_servers.puppeteer]\nbar=2\n', encoding='utf-8')
    (source_home / 'auth.json').write_text('{"OPENAI_API_KEY":"test-key"}', encoding='utf-8')
    monkeypatch.setenv('CODEX_HOME', str(source_home))

    spec = _spec('agent1')
    command = ParsedStartCommand(project=None, agent_names=('agent1',), restore=False, auto_permission=False)

    cmd = _codex_start_cmd(command, spec, runtime_dir, 'sess-1')

    isolated_home = runtime_dir / 'codex-state' / 'home'
    assert f'CODEX_HOME={shlex.quote(str(isolated_home))}' in cmd
    assert f'CODEX_SESSION_ROOT={shlex.quote(str(isolated_home / "sessions"))}' in cmd
    assert (isolated_home / 'auth.json').is_file()
    assert (isolated_home / 'config.toml').is_file()


def test_codex_launcher_build_start_cmd_does_not_require_toml_parser_for_config_sync(
    monkeypatch,
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / 'runtime-no-toml'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    source_home = tmp_path / 'source-home-no-toml'
    source_home.mkdir(parents=True, exist_ok=True)
    (source_home / 'config.toml').write_text('model = "gpt-5"\n', encoding='utf-8')
    (source_home / 'skills' / 'demo').mkdir(parents=True, exist_ok=True)
    (source_home / 'skills' / 'demo' / 'SKILL.md').write_text('skill\n', encoding='utf-8')
    monkeypatch.setenv('CODEX_HOME', str(source_home))
    monkeypatch.setattr(codex_home_config, '_import_optional_toml_reader', lambda: None)

    spec = _spec('agent1')
    command = ParsedStartCommand(project=None, agent_names=('agent1',), restore=False, auto_permission=False)

    cmd = _codex_start_cmd(command, spec, runtime_dir, 'sess-no-toml')

    isolated_home = runtime_dir / 'codex-state' / 'home'
    assert f'CODEX_HOME={shlex.quote(str(isolated_home))}' in cmd
    config_text = (isolated_home / 'config.toml').read_text(encoding='utf-8')
    assert 'model = "gpt-5"' in config_text
    assert 'external_migration = false' in config_text
    assert (isolated_home / 'skills' / 'demo' / 'SKILL.md').read_text(encoding='utf-8') == 'skill\n'


def test_codex_launcher_build_start_cmd_uses_agent_scoped_session_root_by_default(monkeypatch, tmp_path: Path) -> None:
    runtime_dir = tmp_path / 'repo' / '.ccb' / 'agents' / 'agent1' / 'provider-runtime' / 'codex'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    source_home = tmp_path / 'source-home'
    source_home.mkdir(parents=True, exist_ok=True)
    (source_home / 'config.toml').write_text('[model]\nname="gpt-5"\n', encoding='utf-8')
    monkeypatch.setenv('CODEX_HOME', str(source_home))

    spec = _spec('agent1')
    command = ParsedStartCommand(project=None, agent_names=('agent1',), restore=False, auto_permission=False)

    cmd = _codex_start_cmd(command, spec, runtime_dir, 'sess-default')

    codex_home = runtime_dir.parents[1] / 'provider-state' / 'codex' / 'home'
    session_root = codex_home / 'sessions'
    assert f'CODEX_HOME={shlex.quote(str(codex_home))}' in cmd
    assert f'CODEX_SESSION_ROOT={shlex.quote(str(session_root))}' in cmd
    assert session_root.is_dir()
    assert codex_home.is_dir()


def test_codex_launcher_build_start_cmd_includes_agent_model_shortcut(monkeypatch, tmp_path: Path) -> None:
    runtime_dir = tmp_path / 'runtime-codex-model'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.delenv('CODEX_HOME', raising=False)

    spec = AgentSpec(
        name='agent1',
        provider='codex',
        target='.',
        workspace_mode=WorkspaceMode.GIT_WORKTREE,
        workspace_root=None,
        runtime_mode=RuntimeMode.PANE_BACKED,
        restore_default=RestoreMode.AUTO,
        permission_default=PermissionMode.MANUAL,
        queue_policy=QueuePolicy.SERIAL_PER_AGENT,
        model='gpt-5',
        startup_args=('--search',),
    )
    command = ParsedStartCommand(project=None, agent_names=('agent1',), restore=False, auto_permission=False)

    cmd = _codex_start_cmd(command, spec, runtime_dir, 'sess-model')

    assert 'codex -c disable_paste_burst=true -m gpt-5 --search' in cmd


def test_codex_launcher_build_start_cmd_uses_native_auto_permission_flags(monkeypatch, tmp_path: Path) -> None:
    runtime_dir = tmp_path / 'runtime-codex-auto-permission'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.delenv('CODEX_HOME', raising=False)

    spec = _spec('agent1')
    command = ParsedStartCommand(project=None, agent_names=('agent1',), restore=False, auto_permission=True)

    cmd = _codex_start_cmd(command, spec, runtime_dir, 'sess-auto-permission')

    assert '--ask-for-approval never' in cmd
    assert '--sandbox danger-full-access' in cmd
    assert '--dangerously-bypass-hook-trust' in cmd
    assert 'trust_level=' not in cmd
    assert 'approval_policy=' not in cmd
    assert 'sandbox_mode=' not in cmd


def test_codex_launcher_build_start_cmd_skips_hook_trust_bypass_in_safe_mode(monkeypatch, tmp_path: Path) -> None:
    runtime_dir = tmp_path / 'runtime-codex-safe-permission'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.delenv('CODEX_HOME', raising=False)

    spec = _spec('agent1')
    command = ParsedStartCommand(project=None, agent_names=('agent1',), restore=False, auto_permission=False)

    cmd = _codex_start_cmd(command, spec, runtime_dir, 'sess-safe-permission')

    assert '--ask-for-approval never' not in cmd
    assert '--sandbox danger-full-access' not in cmd
    assert '--dangerously-bypass-hook-trust' not in cmd


def test_codex_launcher_repairs_activity_hook_trust_for_existing_home(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-codex-existing-hooks'
    runtime_dir = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-runtime' / 'codex'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    codex_home = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-state' / 'codex' / 'home'
    (codex_home / 'sessions').mkdir(parents=True, exist_ok=True)
    (codex_home / 'config.toml').write_text('model = "gpt-test"\n', encoding='utf-8')
    (project_root / '.ccb' / '.codex-agent1-session').write_text(
        json.dumps(
            {
                'codex_home': str(codex_home),
                'codex_session_root': str(codex_home / 'sessions'),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    monkeypatch.delenv('CODEX_HOME', raising=False)

    spec = _spec('agent1')
    command = ParsedStartCommand(project=None, agent_names=('agent1',), restore=False, auto_permission=True)

    cmd = codex_launcher.build_start_cmd(
        command,
        spec,
        runtime_dir,
        'sess-existing-hooks',
        prepared_state=_codex_prepared_state(runtime_dir),
    )

    assert f'CODEX_HOME={shlex.quote(str(codex_home))}' in cmd
    config = tomllib.loads((codex_home / 'config.toml').read_text(encoding='utf-8'))
    state = config['hooks']['state']
    assert len(state) == 6
    user_prompt_key = f'{codex_home / "hooks.json"}:user_prompt_submit:0:0'
    assert state[user_prompt_key]['enabled'] is True
    assert str(state[user_prompt_key]['trusted_hash']).startswith('sha256:')


def test_codex_launcher_build_start_cmd_uses_agent_scoped_resume_session(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-codex-resume'
    runtime_dir = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-runtime' / 'codex'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    ccb_dir = project_root / '.ccb'
    ccb_dir.mkdir(parents=True, exist_ok=True)
    spec = _spec('agent1')
    command = ParsedStartCommand(project=None, agent_names=('agent1',), restore=True, auto_permission=False)

    monkeypatch.delenv('CODEX_HOME', raising=False)
    _write_project_memory(project_root, 'shared memory\n')
    prepared = _prepare_codex_home_for_test(spec, runtime_dir)
    marker = json.loads((runtime_dir / 'codex-memory-projection.json').read_text(encoding='utf-8'))
    (ccb_dir / '.codex-agent1-session').write_text(
        json.dumps(
            {
                'codex_session_id': 'agent1-session-id',
                'codex_memory_projection_sha256': marker['sha256'],
                'codex_start_cmd': 'codex resume agent1-session-id',
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    (ccb_dir / '.codex-agent2-session').write_text(
        json.dumps(
            {
                'codex_session_id': 'agent2-session-id',
                'codex_memory_projection_sha256': marker['sha256'],
                'codex_start_cmd': 'codex resume agent2-session-id',
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    cmd = codex_launcher.build_start_cmd(command, spec, runtime_dir, 'sess-restore', prepared_state=prepared)

    assert cmd.endswith('resume agent1-session-id')
    assert 'agent2-session-id' not in cmd


def test_codex_launcher_build_start_cmd_respects_agent_restore_fresh(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-codex-fresh'
    runtime_dir = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-runtime' / 'codex'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    spec = AgentSpec(
        name='agent1',
        provider='codex',
        target='.',
        workspace_mode=WorkspaceMode.GIT_WORKTREE,
        workspace_root=None,
        runtime_mode=RuntimeMode.PANE_BACKED,
        restore_default=RestoreMode.FRESH,
        permission_default=PermissionMode.MANUAL,
        queue_policy=QueuePolicy.SERIAL_PER_AGENT,
    )
    command = ParsedStartCommand(project=None, agent_names=('agent1',), restore=True, auto_permission=False)

    monkeypatch.setattr(
        'provider_backends.codex.launcher_runtime.command.load_resume_session_id',
        lambda *args, **kwargs: pytest.fail('fresh restore must not inspect Codex resume sessions'),
    )

    cmd = _codex_start_cmd(command, spec, runtime_dir, 'sess-fresh')

    assert ' resume ' not in f' {cmd} '


def test_codex_launcher_build_start_cmd_reads_resume_cmd_from_agent_scoped_session_file(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-codex-agent'
    runtime_dir = project_root / '.ccb' / 'agents' / 'codex' / 'provider-runtime' / 'codex'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    ccb_dir = project_root / '.ccb'
    ccb_dir.mkdir(parents=True, exist_ok=True)
    demo_home = tmp_path / 'demo-codex-home'
    (ccb_dir / '.codex-codex-session').write_text(
        json.dumps(
            {
                'codex_start_cmd': f'export CODEX_HOME={shlex.quote(str(demo_home))}; codex -c disable_paste_burst=true resume codex-session-id',
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    spec = _spec('codex')
    command = ParsedStartCommand(project=None, agent_names=('codex',), restore=True, auto_permission=False)

    cmd = _codex_start_cmd(command, spec, runtime_dir, 'sess-restore')

    assert cmd.endswith('resume codex-session-id')


def test_claude_launcher_build_start_cmd_uses_overlay_and_drops_dead_local_user_proxy(monkeypatch, tmp_path: Path) -> None:
    runtime_dir = tmp_path / 'runtime'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    home_dir = tmp_path / 'home'
    claude_dir = home_dir / '.claude'
    claude_dir.mkdir(parents=True, exist_ok=True)
    (claude_dir / 'settings.json').write_text(
        json.dumps(
            {
                'env': {
                    'ANTHROPIC_BASE_URL': 'http://127.0.0.1:15722',
                    'ANTHROPIC_AUTH_TOKEN': 'secret',
                },
                'model': 'opus',
                'skipDangerousModePermissionPrompt': True,
            },
            ensure_ascii=False,
        ),
        encoding='utf-8',
    )
    spec = _spec('reviewer', provider='claude')
    command = ParsedStartCommand(project=None, agent_names=('reviewer',), restore=True, auto_permission=True)

    monkeypatch.setattr('provider_backends.claude.launcher.Path.home', lambda: home_dir)
    monkeypatch.setattr('provider_backends.claude.launcher.local_tcp_listener_available', lambda host, port: False)
    monkeypatch.setattr(
        claude_launcher,
        '_resolve_claude_restore_target',
        lambda **kwargs: ProviderRestoreTarget(run_cwd=runtime_dir, has_history=True),
    )

    start_cmd = claude_launcher.build_start_cmd(
        command,
        spec,
        runtime_dir,
        'claude-sess-1',
        prepared_state=_claude_prepared_state(runtime_dir),
    )

    assert start_cmd.startswith('unset ANTHROPIC_BASE_URL; ')
    assert 'HOME=' in start_cmd
    assert 'CLAUDE_PROJECTS_ROOT=' in start_cmd
    _assert_caller_env_exports(
        start_cmd,
        actor='reviewer',
        runtime_dir=runtime_dir,
        session_id='claude-sess-1',
    )
    assert start_cmd.endswith(
        f'claude --setting-sources user,project,local --settings {shlex.quote(str(runtime_dir / "claude-settings.json"))} '
        '--permission-mode bypassPermissions --continue'
    )
    settings_payload = json.loads((runtime_dir / 'claude-settings.json').read_text(encoding='utf-8'))
    assert settings_payload['skipDangerousModePermissionPrompt'] is True


def test_claude_launcher_build_start_cmd_respects_agent_restore_fresh(monkeypatch, tmp_path: Path) -> None:
    runtime_dir = tmp_path / 'runtime-claude-fresh'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    spec = AgentSpec(
        name='reviewer',
        provider='claude',
        target='.',
        workspace_mode=WorkspaceMode.GIT_WORKTREE,
        workspace_root=None,
        runtime_mode=RuntimeMode.PANE_BACKED,
        restore_default=RestoreMode.FRESH,
        permission_default=PermissionMode.MANUAL,
        queue_policy=QueuePolicy.SERIAL_PER_AGENT,
    )
    command = ParsedStartCommand(project=None, agent_names=('reviewer',), restore=True, auto_permission=True)
    observed_restore_flags: list[bool] = []

    def fake_restore_target(**kwargs):
        observed_restore_flags.append(bool(kwargs['restore']))
        return ProviderRestoreTarget(run_cwd=runtime_dir, has_history=bool(kwargs['restore']))

    monkeypatch.setattr(claude_launcher, '_resolve_claude_restore_target', fake_restore_target)
    monkeypatch.setattr(
        'provider_backends.claude.launcher.write_claude_settings_overlay',
        lambda runtime_dir, profile=None: runtime_dir / 'claude-settings.json',
    )

    start_cmd = claude_launcher.build_start_cmd(
        command,
        spec,
        runtime_dir,
        'claude-sess-fresh',
        prepared_state=_claude_prepared_state(runtime_dir),
    )

    assert observed_restore_flags == [False]
    assert '--permission-mode bypassPermissions' in start_cmd
    assert '--continue' not in start_cmd


def test_claude_launcher_build_start_cmd_includes_agent_model_shortcut(tmp_path: Path) -> None:
    runtime_dir = tmp_path / 'runtime-claude-model'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    spec = AgentSpec(
        name='reviewer',
        provider='claude',
        target='.',
        workspace_mode=WorkspaceMode.GIT_WORKTREE,
        workspace_root=None,
        runtime_mode=RuntimeMode.PANE_BACKED,
        restore_default=RestoreMode.AUTO,
        permission_default=PermissionMode.MANUAL,
        queue_policy=QueuePolicy.SERIAL_PER_AGENT,
        model='opus',
    )
    command = ParsedStartCommand(project=None, agent_names=('reviewer',), restore=False, auto_permission=False)

    start_cmd = claude_launcher.build_start_cmd(
        command,
        spec,
        runtime_dir,
        'claude-sess-model',
        prepared_state=_claude_prepared_state(runtime_dir),
    )

    assert start_cmd.endswith('claude --setting-sources user,project,local --model opus')


def test_claude_launcher_build_start_cmd_requires_launch_context(tmp_path: Path) -> None:
    runtime_dir = tmp_path / 'runtime-claude-missing-context'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    spec = _spec('reviewer', provider='claude')
    command = ParsedStartCommand(project=None, agent_names=('reviewer',), restore=False, auto_permission=False)

    with pytest.raises(RuntimeError, match='prepare_launch_context'):
        claude_launcher.build_start_cmd(command, spec, runtime_dir, 'claude-sess-missing-context')


@pytest.mark.parametrize(
    ('provider', 'launcher', 'session_id'),
    (
        ('droid', droid_launcher, 'droid-sess-prepared'),
    ),
)
def test_non_claude_build_start_cmd_accepts_prepared_state_keyword(
    provider: str,
    launcher,
    session_id: str,
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / provider / 'runtime'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    spec = _spec('agent1', provider=provider)
    command = ParsedStartCommand(project=None, agent_names=('agent1',), restore=False, auto_permission=False)

    launcher.build_start_cmd(command, spec, runtime_dir, session_id, prepared_state={})
    launcher.build_start_cmd(command, spec, runtime_dir, session_id, prepared_state=None)


def test_codex_launcher_build_start_cmd_requires_launch_context(tmp_path: Path) -> None:
    runtime_dir = tmp_path / 'runtime-codex-missing-context'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    spec = _spec('agent1', provider='codex')
    command = ParsedStartCommand(project=None, agent_names=('agent1',), restore=False, auto_permission=False)

    with pytest.raises(RuntimeError, match='prepare_launch_context'):
        codex_launcher.build_start_cmd(command, spec, runtime_dir, 'codex-sess-missing-context')


def test_opencode_launcher_build_start_cmd_requires_launch_context(tmp_path: Path) -> None:
    runtime_dir = tmp_path / 'runtime-opencode-missing-context'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    spec = _spec('agent1', provider='opencode')
    command = ParsedStartCommand(project=None, agent_names=('agent1',), restore=False, auto_permission=False)

    with pytest.raises(RuntimeError, match='prepare_launch_context'):
        opencode_launcher.build_start_cmd(command, spec, runtime_dir, 'opencode-sess-missing-context')


def test_gemini_launcher_build_start_cmd_requires_launch_context(tmp_path: Path) -> None:
    runtime_dir = tmp_path / 'runtime-gemini-missing-context'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    spec = _spec('agent1', provider='gemini')
    command = ParsedStartCommand(project=None, agent_names=('agent1',), restore=False, auto_permission=False)

    with pytest.raises(RuntimeError, match='prepare_launch_context'):
        gemini_launcher.build_start_cmd(command, spec, runtime_dir, 'gemini-sess-missing-context')


def test_opencode_workspace_preparation_writes_memory_config(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-opencode-memory'
    runtime_dir = project_root / '.ccb' / 'agents' / 'builder' / 'provider-runtime' / 'opencode'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    project_root.mkdir(parents=True, exist_ok=True)
    _write_project_memory(project_root, 'shared ask memory\n')
    (project_root / 'AGENTS.md').write_text('project opencode memory\n', encoding='utf-8')
    (project_root / 'opencode.json').write_text(
        json.dumps({'provider': 'anthropic', 'instructions': ['AGENTS.md']}, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    monkeypatch.setenv('OPENCODE_START_CMD', 'opencode')
    spec = _spec('builder', provider='opencode')
    command = ParsedStartCommand(project=None, agent_names=('builder',), restore=True, auto_permission=False)
    prepared = _prepare_opencode_workspace_for_test(spec, runtime_dir)

    cmd = opencode_launcher.build_start_cmd(
        command,
        spec,
        runtime_dir,
        'opencode-sess-memory',
        prepared_state=prepared,
    )

    config_path = project_root / '.ccb' / 'agents' / 'builder' / 'provider-state' / 'opencode' / 'opencode.json'
    bundle_path = project_root / '.ccb' / 'runtime' / 'memory' / 'builder.md'
    config = json.loads(config_path.read_text(encoding='utf-8'))
    assert f'OPENCODE_CONFIG={shlex.quote(str(config_path))}' in cmd
    assert cmd.endswith('opencode --continue')
    assert config['provider'] == 'anthropic'
    assert config['instructions'] == ['AGENTS.md', '.ccb/runtime/memory/builder.md']
    bundle_text = bundle_path.read_text(encoding='utf-8')
    assert 'shared ask memory' in bundle_text
    assert 'project opencode memory' in bundle_text


def test_opencode_workspace_preparation_records_memory_projection_once(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-opencode-events'
    runtime_dir = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-runtime' / 'opencode'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    project_root.mkdir(parents=True, exist_ok=True)
    _write_project_memory(project_root, 'shared ask memory\n')
    monkeypatch.setenv('OPENCODE_START_CMD', 'opencode')
    spec = _spec('agent1', provider='opencode')

    _prepare_opencode_workspace_for_test(spec, runtime_dir)
    _prepare_opencode_workspace_for_test(spec, runtime_dir)

    events = [
        json.loads(line)
        for line in (project_root / '.ccb' / 'agents' / 'agent1' / 'events.jsonl').read_text(encoding='utf-8').splitlines()
        if line.strip()
    ]
    memory_events = [event for event in events if str(event.get('event_type', '')).startswith('opencode_memory_projection_')]
    assert len(memory_events) == 1
    assert memory_events[0]['event_type'] == 'opencode_memory_projection_ok'
    assert memory_events[0]['projection_path'].endswith('/.ccb/runtime/memory/agent1.md')
    assert memory_events[0]['config_path'].endswith('/.ccb/agents/agent1/provider-state/opencode/opencode.json')
    assert memory_events[0]['bundle_path'].endswith('/.ccb/runtime/memory/agent1.md')
    assert memory_events[0]['sha256']


def test_opencode_workspace_preparation_respects_inherit_memory_flag(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-opencode-inherit-memory'
    runtime_dir = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-runtime' / 'opencode'
    config_path = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-state' / 'opencode' / 'opencode.json'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text('{"instructions":["stale.md"]}\n', encoding='utf-8')
    _write_provider_profile(
        runtime_dir,
        ResolvedProviderProfile(
            provider='opencode',
            agent_name='agent1',
            inherit_memory=False,
        ),
    )
    monkeypatch.setenv('OPENCODE_START_CMD', 'opencode')
    spec = _spec('agent1', provider='opencode')
    command = ParsedStartCommand(project=None, agent_names=('agent1',), restore=False, auto_permission=False)
    prepared = _prepare_opencode_workspace_for_test(spec, runtime_dir)

    cmd = opencode_launcher.build_start_cmd(
        command,
        spec,
        runtime_dir,
        'opencode-sess-inherit-memory',
        prepared_state=prepared,
    )

    assert 'OPENCODE_CONFIG=' not in cmd
    assert not config_path.exists()



def test_codex_launcher_build_start_cmd_uses_materialized_profile_home(monkeypatch, tmp_path: Path) -> None:
    runtime_dir = tmp_path / 'runtime'
    profile_home = tmp_path / 'codex-profile-home'
    _write_provider_profile(
        runtime_dir,
        ResolvedProviderProfile(
            provider='codex',
            agent_name='agent1',
            mode='isolated',
            profile_root=str(profile_home),
            runtime_home=str(profile_home),
            env={'OPENAI_API_KEY': 'profile-key'},
            inherit_api=False,
        ),
    )

    spec = _spec('agent1')
    command = ParsedStartCommand(project=None, agent_names=('agent1',), restore=False, auto_permission=False)

    cmd = _codex_start_cmd(command, spec, runtime_dir, 'sess-profile')

    assert 'unset OPENAI_API_KEY' in cmd
    assert f'CODEX_HOME={shlex.quote(str(profile_home))}' in cmd
    assert f'CODEX_SESSION_ROOT={shlex.quote(str(profile_home / "sessions"))}' in cmd
    assert f'OPENAI_API_KEY={shlex.quote("profile-key")}' in cmd
    assert (profile_home / 'sessions').is_dir()


def test_codex_launcher_build_start_cmd_api_override_clears_global_route_config(monkeypatch, tmp_path: Path) -> None:
    runtime_dir = tmp_path / 'runtime-codex-api-override'
    profile_home = tmp_path / 'codex-profile-home'
    source_home = tmp_path / 'source-home'
    source_home.mkdir(parents=True, exist_ok=True)
    (source_home / 'config.toml').write_text(
        '\n'.join(
            [
                'model_provider = "stale"',
                'model = "gpt-5.4-openai-compact"',
                'model_reasoning_effort = "xhigh"',
                'disable_response_storage = true',
                '',
                '[projects."/tmp/demo-project"]',
                'trust_level = "trusted"',
                '',
                '[model_providers.stale]',
                'name = "stale"',
                'base_url = "https://api.ikuncode.cc/v1"',
                'wire_api = "responses"',
                'requires_openai_auth = true',
                '',
            ]
        ),
        encoding='utf-8',
    )
    (source_home / 'auth.json').write_text('{"OPENAI_API_KEY":"system-key"}\n', encoding='utf-8')
    monkeypatch.setenv('CODEX_HOME', str(source_home))
    _write_provider_profile(
        runtime_dir,
        ResolvedProviderProfile(
            provider='codex',
            agent_name='agent1',
            mode='isolated',
            profile_root=str(profile_home),
            runtime_home=str(profile_home),
            env={
                'OPENAI_API_KEY': 'profile-key',
                'OPENAI_BASE_URL': 'https://api.rootflowai.com',
            },
            inherit_api=False,
            inherit_auth=False,
            inherit_config=False,
        ),
    )
    profile_home.mkdir(parents=True, exist_ok=True)
    (profile_home / 'config.toml').write_text('model_provider = "stale"\n', encoding='utf-8')
    (profile_home / 'auth.json').write_text('{"OPENAI_API_KEY":"stale-key"}\n', encoding='utf-8')

    spec = _spec('agent1')
    command = ParsedStartCommand(project=None, agent_names=('agent1',), restore=False, auto_permission=False)

    cmd = _codex_start_cmd(command, spec, runtime_dir, 'sess-profile-override')

    assert 'unset OPENAI_API_KEY' in cmd
    assert 'unset OPENAI_BASE_URL' in cmd
    assert f'OPENAI_API_KEY={shlex.quote("profile-key")}' in cmd
    assert f'OPENAI_BASE_URL={shlex.quote("https://api.rootflowai.com")}' not in cmd
    config_text = (profile_home / 'config.toml').read_text(encoding='utf-8')
    assert 'model_provider = "custom"' in config_text
    assert 'model = "gpt-5.4-openai-compact"' in config_text
    assert 'model_reasoning_effort = "xhigh"' in config_text
    assert 'disable_response_storage = true' in config_text
    assert '[projects."/tmp/demo-project"]' in config_text
    assert '[model_providers.custom]' in config_text
    assert 'base_url = "https://api.rootflowai.com"' in config_text
    assert 'wire_api = "responses"' in config_text
    assert 'requires_openai_auth = false' in config_text
    assert 'external_migration = false' in config_text
    assert 'https://api.ikuncode.cc/v1' not in config_text
    assert 'env_key' not in config_text
    assert (profile_home / 'auth.json').read_text(encoding='utf-8') == '{"OPENAI_API_KEY":"profile-key"}\n'


def test_codex_launcher_build_start_cmd_skips_resume_when_explicit_api_authority_changed(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-codex-authority-change'
    runtime_dir = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-runtime' / 'codex'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    profile_home = project_root / '.ccb' / 'provider-profiles' / 'agent1' / 'codex'
    _write_provider_profile(
        runtime_dir,
        ResolvedProviderProfile(
            provider='codex',
            agent_name='agent1',
            mode='isolated',
            profile_root=str(profile_home),
            runtime_home=str(profile_home),
            env={
                'OPENAI_API_KEY': 'profile-key',
                'OPENAI_BASE_URL': 'https://api.rootflowai.com',
            },
            inherit_api=False,
            inherit_auth=False,
            inherit_config=False,
        ),
    )
    ccb_dir = project_root / '.ccb'
    ccb_dir.mkdir(parents=True, exist_ok=True)
    (ccb_dir / '.codex-agent1-session').write_text(
        json.dumps({'codex_session_id': 'legacy-session-id'}, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )

    spec = _spec('agent1')
    command = ParsedStartCommand(project=None, agent_names=('agent1',), restore=True, auto_permission=False)

    cmd = _codex_start_cmd(command, spec, runtime_dir, 'sess-authority-change')

    assert 'resume legacy-session-id' not in cmd


def test_codex_launcher_build_start_cmd_resumes_when_memory_projection_changed(
    monkeypatch, tmp_path: Path
) -> None:
    project_root = tmp_path / 'repo-codex-memory-authority'
    runtime_dir = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-runtime' / 'codex'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    source_home = tmp_path / 'source-home'
    source_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv('CODEX_HOME', str(source_home))
    _write_project_memory(project_root, 'new shared memory\n')

    codex_home = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-state' / 'codex' / 'home'
    session_root = codex_home / 'sessions'
    old_log = session_root / '2026' / '05' / '01' / 'legacy-session.jsonl'
    old_log.parent.mkdir(parents=True, exist_ok=True)
    old_log.write_text('', encoding='utf-8')
    (codex_home / '.ccb-session-namespace.json').write_text(
        json.dumps(
            {
                'provider': 'codex',
                'provider_authority_fingerprint': '',
                'memory_projection_sha256': 'old-memory-sha',
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    ccb_dir = project_root / '.ccb'
    ccb_dir.mkdir(parents=True, exist_ok=True)
    session_file = ccb_dir / '.codex-agent1-session'
    resume_cmd = (
        f'export CODEX_HOME={shlex.quote(str(codex_home))} '
        f'CODEX_SESSION_ROOT={shlex.quote(str(session_root))}; '
        'codex -c disable_paste_burst=true resume legacy-session-id'
    )
    session_file.write_text(
        json.dumps(
            {
                'codex_home': str(codex_home),
                'codex_session_root': str(session_root),
                'codex_session_id': 'legacy-session-id',
                'codex_session_path': str(old_log),
                'codex_memory_projection_sha256': 'old-memory-sha',
                'start_cmd': resume_cmd,
                'codex_start_cmd': resume_cmd,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    spec = _spec('agent1')
    command = ParsedStartCommand(project=None, agent_names=('agent1',), restore=True, auto_permission=False)

    cmd = _codex_start_cmd(command, spec, runtime_dir, 'sess-memory-change')

    assert cmd.endswith('resume legacy-session-id')
    data = json.loads(session_file.read_text(encoding='utf-8'))
    assert data['codex_session_id'] == 'legacy-session-id'
    assert data['codex_session_path'] == str(old_log)
    assert old_log.is_file()
    assert not (codex_home / 'archived-sessions').exists()
    marker = json.loads((codex_home / '.ccb-session-namespace.json').read_text(encoding='utf-8'))
    assert marker['memory_projection_sha256']
    assert marker['memory_projection_sha256'] != 'old-memory-sha'


def test_codex_launcher_build_start_cmd_skips_resume_when_explicit_api_binding_proof_missing(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-codex-binding-proof-missing'
    runtime_dir = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-runtime' / 'codex'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    profile_home = project_root / '.ccb' / 'provider-profiles' / 'agent1' / 'codex'
    profile = ResolvedProviderProfile(
        provider='codex',
        agent_name='agent1',
        mode='isolated',
        profile_root=str(profile_home),
        runtime_home=str(profile_home),
        env={
            'OPENAI_API_KEY': 'profile-key',
            'OPENAI_BASE_URL': 'https://api.rootflowai.com',
        },
        inherit_api=False,
        inherit_auth=False,
        inherit_config=False,
    )
    _write_provider_profile(
        runtime_dir,
        profile,
    )
    ccb_dir = project_root / '.ccb'
    ccb_dir.mkdir(parents=True, exist_ok=True)
    fingerprint = codex_home_config.codex_provider_authority_fingerprint(profile)
    (ccb_dir / '.codex-agent1-session').write_text(
        json.dumps(
            {
                'codex_session_id': 'legacy-session-id',
                'codex_provider_authority_fingerprint': fingerprint,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    spec = _spec('agent1')
    command = ParsedStartCommand(project=None, agent_names=('agent1',), restore=True, auto_permission=False)

    cmd = _codex_start_cmd(command, spec, runtime_dir, 'sess-binding-proof-missing')

    assert 'resume legacy-session-id' not in cmd


def test_codex_launcher_build_start_cmd_rotates_legacy_explicit_session_namespace(
    monkeypatch, tmp_path: Path
) -> None:
    project_root = tmp_path / 'repo-codex-legacy-explicit-namespace'
    runtime_dir = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-runtime' / 'codex'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    profile_home = project_root / '.ccb' / 'provider-profiles' / 'agent1' / 'codex'
    source_home = tmp_path / 'source-home'
    source_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv('CODEX_HOME', str(source_home))

    profile = ResolvedProviderProfile(
        provider='codex',
        agent_name='agent1',
        mode='isolated',
        profile_root=str(profile_home),
        runtime_home=str(profile_home),
        env={
            'OPENAI_API_KEY': 'profile-key',
            'OPENAI_BASE_URL': 'https://api.rootflowai.com',
        },
        inherit_api=False,
        inherit_auth=False,
        inherit_config=False,
    )
    _write_provider_profile(runtime_dir, profile)

    session_root = profile_home / 'sessions'
    old_log = session_root / '2026' / '04' / '26' / 'legacy-session.jsonl'
    old_log.parent.mkdir(parents=True, exist_ok=True)
    old_log.write_text('', encoding='utf-8')
    ccb_dir = project_root / '.ccb'
    ccb_dir.mkdir(parents=True, exist_ok=True)
    fingerprint = codex_home_config.codex_provider_authority_fingerprint(profile)
    resume_cmd = (
        f'export CODEX_HOME={shlex.quote(str(profile_home))} '
        f'CODEX_SESSION_ROOT={shlex.quote(str(session_root))}; '
        'codex -m gpt-image-2-count resume legacy-session-id'
    )
    session_file = ccb_dir / '.codex-agent1-session'
    session_file.write_text(
        json.dumps(
            {
                'codex_home': str(profile_home),
                'codex_session_root': str(session_root),
                'codex_session_id': 'legacy-session-id',
                'codex_session_path': str(old_log),
                'codex_provider_authority_fingerprint': fingerprint,
                'codex_session_authority_fingerprint': fingerprint,
                'start_cmd': resume_cmd,
                'codex_start_cmd': resume_cmd,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    spec = _spec('agent1')
    command = ParsedStartCommand(project=None, agent_names=('agent1',), restore=True, auto_permission=False)

    cmd = _codex_start_cmd(command, spec, runtime_dir, 'sess-legacy-explicit-namespace')

    assert 'resume legacy-session-id' not in cmd
    assert session_root.is_dir()
    assert not any(session_root.iterdir())
    archive_root = profile_home / 'archived-sessions'
    assert archive_root.is_dir()
    assert any(archive_root.rglob('legacy-session.jsonl'))
    marker = json.loads((profile_home / '.ccb-session-namespace.json').read_text(encoding='utf-8'))
    assert marker['provider_authority_fingerprint'] == fingerprint
    data = json.loads(session_file.read_text(encoding='utf-8'))
    assert 'codex_session_id' not in data
    assert 'codex_session_path' not in data
    assert 'codex_session_authority_fingerprint' not in data
    assert data['old_codex_session_id'] == 'legacy-session-id'
    assert data['old_codex_session_path'] == str(old_log)
    assert 'resume legacy-session-id' not in data['start_cmd']
    assert 'resume legacy-session-id' not in data['codex_start_cmd']


def test_codex_launcher_build_start_cmd_exports_inherited_api_env(monkeypatch, tmp_path: Path) -> None:
    runtime_dir = tmp_path / 'runtime'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv('OPENAI_API_KEY', 'env-key')
    monkeypatch.setenv('OPENAI_BASE_URL', 'https://api.example.test/v1')

    spec = _spec('agent1')
    command = ParsedStartCommand(project=None, agent_names=('agent1',), restore=False, auto_permission=False)

    cmd = _codex_start_cmd(command, spec, runtime_dir, 'sess-inherit-api')

    assert f'OPENAI_API_KEY={shlex.quote("env-key")}' in cmd
    assert f'OPENAI_BASE_URL={shlex.quote("https://api.example.test/v1")}' in cmd


def test_codex_launcher_build_start_cmd_exports_user_session_transport_without_runtime_leaks(
    monkeypatch,
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / 'runtime'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    ambient_runtime = tmp_path / 'ambient-codex-runtime'
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:7890')
    monkeypatch.setenv('NO_PROXY', 'localhost,127.0.0.1')
    monkeypatch.setenv('CODEX_CA_CERTIFICATE', '/tmp/codex-ca.pem')
    monkeypatch.setenv('WSL_INTEROP', '/run/WSL/1234_interop')
    monkeypatch.setenv('CODEX_RUNTIME_DIR', str(ambient_runtime))
    monkeypatch.setenv('CCB_CALLER_ACTOR', 'stale-agent')

    spec = _spec('agent1')
    command = ParsedStartCommand(project=None, agent_names=('agent1',), restore=False, auto_permission=False)

    cmd = _codex_start_cmd(command, spec, runtime_dir, 'sess-transport')

    assert f'HTTPS_PROXY={shlex.quote("http://127.0.0.1:7890")}' in cmd
    assert f'NO_PROXY={shlex.quote("localhost,127.0.0.1")}' in cmd
    assert f'CODEX_CA_CERTIFICATE={shlex.quote("/tmp/codex-ca.pem")}' in cmd
    assert f'WSL_INTEROP={shlex.quote("/run/WSL/1234_interop")}' in cmd
    assert f'CODEX_RUNTIME_DIR={shlex.quote(str(runtime_dir))}' in cmd
    assert str(ambient_runtime) not in cmd
    assert 'CCB_CALLER_ACTOR=stale-agent' not in cmd


def test_codex_launcher_build_start_cmd_refreshes_managed_home_projection(monkeypatch, tmp_path: Path) -> None:
    runtime_dir = tmp_path / 'runtime'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    source_home = tmp_path / 'source-home'
    source_home.mkdir(parents=True, exist_ok=True)
    (source_home / 'config.toml').write_text('model = "gpt-5"\n', encoding='utf-8')
    (source_home / 'auth.json').write_text('{"OPENAI_API_KEY":"old-key"}\n', encoding='utf-8')
    _write_codex_plugin_source(
        source_home,
        plugin_name='weatherpromise',
        sha='plugins-sha-v1',
        marketplace_name='market-v1',
        skill_body='plugin skill v1\n',
    )
    monkeypatch.setenv('CODEX_HOME', str(source_home))

    spec = _spec('agent1')
    command = ParsedStartCommand(project=None, agent_names=('agent1',), restore=False, auto_permission=False)

    _codex_start_cmd(command, spec, runtime_dir, 'sess-refresh-1')

    isolated_home = runtime_dir / 'codex-state' / 'home'
    config_text = (isolated_home / 'config.toml').read_text(encoding='utf-8')
    assert 'model = "gpt-5"' in config_text
    assert 'external_migration = false' in config_text
    assert (isolated_home / 'auth.json').read_text(encoding='utf-8') == '{"OPENAI_API_KEY":"old-key"}\n'
    assert (isolated_home / '.tmp' / 'plugins.sha').read_text(encoding='utf-8') == 'plugins-sha-v1\n'
    assert (
        isolated_home / '.tmp' / 'plugins' / 'plugins' / 'weatherpromise' / 'skills' / 'weatherpromise' / 'SKILL.md'
    ).read_text(encoding='utf-8') == 'plugin skill v1\n'

    (source_home / 'config.toml').write_text('model = "gpt-5.1"\n', encoding='utf-8')
    (source_home / 'auth.json').write_text('{"OPENAI_API_KEY":"new-key"}\n', encoding='utf-8')
    _write_codex_plugin_source(
        source_home,
        plugin_name='weatherpromise',
        sha='plugins-sha-v2',
        marketplace_name='market-v2',
        skill_body='plugin skill v2\n',
    )

    _codex_start_cmd(command, spec, runtime_dir, 'sess-refresh-2')

    config_text = (isolated_home / 'config.toml').read_text(encoding='utf-8')
    assert 'model = "gpt-5.1"' in config_text
    assert 'external_migration = false' in config_text
    assert (isolated_home / 'auth.json').read_text(encoding='utf-8') == '{"OPENAI_API_KEY":"new-key"}\n'
    assert (isolated_home / '.tmp' / 'plugins.sha').read_text(encoding='utf-8') == 'plugins-sha-v2\n'
    marketplace_payload = json.loads(
        (isolated_home / '.tmp' / 'plugins' / '.agents' / 'plugins' / 'marketplace.json').read_text(encoding='utf-8')
    )
    assert marketplace_payload['name'] == 'market-v2'
    assert (
        isolated_home / '.tmp' / 'plugins' / 'plugins' / 'weatherpromise' / 'skills' / 'weatherpromise' / 'SKILL.md'
    ).read_text(encoding='utf-8') == 'plugin skill v2\n'


def test_codex_launcher_build_start_cmd_reuses_legacy_codex_home_from_persisted_start_cmd(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-codex-legacy-home'
    runtime_dir = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-runtime' / 'codex'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    legacy_home = tmp_path / 'legacy-codex-home'
    (legacy_home / 'sessions').mkdir(parents=True, exist_ok=True)
    ccb_dir = project_root / '.ccb'
    ccb_dir.mkdir(parents=True, exist_ok=True)
    (ccb_dir / '.codex-agent1-session').write_text(
        json.dumps(
            {
                'codex_start_cmd': f'export CODEX_HOME={legacy_home}; codex',
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    spec = _spec('agent1')
    command = ParsedStartCommand(project=None, agent_names=('agent1',), restore=False, auto_permission=False)

    cmd = _codex_start_cmd(command, spec, runtime_dir, 'sess-legacy-home')

    assert f'CODEX_HOME={shlex.quote(str(legacy_home))}' in cmd
    assert f'CODEX_SESSION_ROOT={shlex.quote(str(legacy_home / "sessions"))}' in cmd


def test_codex_launcher_build_start_cmd_reuses_legacy_session_root_from_persisted_log_path(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-codex-legacy-root'
    runtime_dir = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-runtime' / 'codex'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    legacy_root = tmp_path / 'legacy-codex-home' / 'sessions'
    legacy_log = legacy_root / '2026' / '04' / '19' / 'rollout-legacy-session.jsonl'
    legacy_log.parent.mkdir(parents=True, exist_ok=True)
    legacy_log.write_text('', encoding='utf-8')
    ccb_dir = project_root / '.ccb'
    ccb_dir.mkdir(parents=True, exist_ok=True)
    (ccb_dir / '.codex-agent1-session').write_text(
        json.dumps(
            {
                'codex_session_path': str(legacy_log),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    spec = _spec('agent1')
    command = ParsedStartCommand(project=None, agent_names=('agent1',), restore=False, auto_permission=False)

    cmd = _codex_start_cmd(command, spec, runtime_dir, 'sess-legacy-root')

    migrated_home = legacy_root.parent / 'home'
    migrated_root = migrated_home / 'sessions'
    assert f'CODEX_HOME={shlex.quote(str(migrated_home))}' in cmd
    assert f'CODEX_SESSION_ROOT={shlex.quote(str(migrated_root))}' in cmd
    assert migrated_root.is_dir()
    assert (migrated_root / '2026' / '04' / '19' / 'rollout-legacy-session.jsonl').is_file()


def test_claude_launcher_build_start_cmd_uses_isolated_profile_api_env(monkeypatch, tmp_path: Path) -> None:
    runtime_dir = tmp_path / 'runtime'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    home_dir = tmp_path / 'home'
    claude_dir = home_dir / '.claude'
    claude_dir.mkdir(parents=True, exist_ok=True)
    (claude_dir / 'settings.json').write_text(
        json.dumps(
            {
                'env': {
                    'ANTHROPIC_BASE_URL': 'https://example.invalid/claude',
                    'ANTHROPIC_AUTH_TOKEN': 'secret',
                },
                'model': 'opus',
            },
            ensure_ascii=False,
        ),
        encoding='utf-8',
    )
    _write_provider_profile(
        runtime_dir,
        ResolvedProviderProfile(
            provider='claude',
            agent_name='reviewer',
            mode='isolated',
            profile_root=str(tmp_path / 'profile'),
            runtime_home=None,
            env={'ANTHROPIC_AUTH_TOKEN': 'profile-token'},
            inherit_api=False,
        ),
    )
    spec = _spec('reviewer', provider='claude')
    command = ParsedStartCommand(project=None, agent_names=('reviewer',), restore=True, auto_permission=True)

    monkeypatch.setattr('provider_backends.claude.launcher.Path.home', lambda: home_dir)
    monkeypatch.setattr(
        claude_launcher,
        '_resolve_claude_restore_target',
        lambda **kwargs: ProviderRestoreTarget(run_cwd=runtime_dir, has_history=True),
    )

    start_cmd = claude_launcher.build_start_cmd(
        command,
        spec,
        runtime_dir,
        'claude-sess-iso',
        prepared_state=_claude_prepared_state(runtime_dir),
    )

    assert 'unset ANTHROPIC_AUTH_TOKEN' in start_cmd
    assert f'ANTHROPIC_AUTH_TOKEN={shlex.quote("profile-token")}' in start_cmd
    assert 'https://example.invalid/claude' not in start_cmd
    assert f'--settings {shlex.quote(str(runtime_dir / "claude-settings.json"))}' in start_cmd
    assert '--permission-mode bypassPermissions' in start_cmd
    settings_payload = json.loads((runtime_dir / 'claude-settings.json').read_text(encoding='utf-8'))
    assert settings_payload['skipDangerousModePermissionPrompt'] is True


def test_claude_launcher_build_start_cmd_uses_agent_settings_overlay_when_present(monkeypatch, tmp_path: Path) -> None:
    runtime_dir = tmp_path / 'runtime'
    profile_root = tmp_path / 'profile'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    profile_root.mkdir(parents=True, exist_ok=True)
    (profile_root / 'settings.json').write_text(
        json.dumps(
            {
                'env': {'ANTHROPIC_AUTH_TOKEN': 'secret'},
                'model': 'opus',
            },
            ensure_ascii=False,
        ),
        encoding='utf-8',
    )
    _write_provider_profile(
        runtime_dir,
        ResolvedProviderProfile(
            provider='claude',
            agent_name='reviewer',
            mode='inherit',
            profile_root=str(profile_root),
            runtime_home=None,
            env={},
            inherit_api=True,
        ),
    )
    spec = _spec('reviewer', provider='claude')
    command = ParsedStartCommand(project=None, agent_names=('reviewer',), restore=True, auto_permission=False)

    monkeypatch.setattr('provider_backends.claude.launcher.Path.home', lambda: tmp_path / 'home')
    monkeypatch.setattr(
        claude_launcher,
        '_resolve_claude_restore_target',
        lambda **kwargs: ProviderRestoreTarget(run_cwd=runtime_dir, has_history=False),
    )

    start_cmd = claude_launcher.build_start_cmd(
        command,
        spec,
        runtime_dir,
        'claude-sess-local',
        prepared_state=_claude_prepared_state(runtime_dir),
    )

    settings_path = runtime_dir / 'claude-settings.json'
    _assert_caller_env_exports(
        start_cmd,
        actor='reviewer',
        runtime_dir=runtime_dir,
        session_id='claude-sess-local',
    )
    assert start_cmd.endswith(
        f'claude --setting-sources user,project,local --settings {shlex.quote(str(settings_path))}'
    )
    assert json.loads(settings_path.read_text(encoding='utf-8')) == {'model': 'opus'}


def test_claude_launcher_build_start_cmd_ignores_profile_runtime_home(monkeypatch, tmp_path: Path) -> None:
    runtime_dir = tmp_path / 'runtime'
    profile_home = tmp_path / 'claude-profile-home'
    managed_home = runtime_dir / 'claude-home'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    _write_provider_profile(
        runtime_dir,
        ResolvedProviderProfile(
            provider='claude',
            agent_name='reviewer',
            mode='isolated',
            profile_root=str(profile_home),
            runtime_home=str(profile_home),
            env={'ANTHROPIC_AUTH_TOKEN': 'profile-token'},
            inherit_api=False,
        ),
    )
    monkeypatch.setattr(
        claude_launcher,
        '_resolve_claude_restore_target',
        lambda **kwargs: ProviderRestoreTarget(run_cwd=runtime_dir, has_history=False),
    )

    start_cmd = claude_launcher.build_start_cmd(
        ParsedStartCommand(project=None, agent_names=('reviewer',), restore=False, auto_permission=False),
        _spec('reviewer', provider='claude'),
        runtime_dir,
        'claude-sess-home',
        prepared_state=_claude_prepared_state(runtime_dir),
    )

    assert f'HOME={shlex.quote(str(managed_home))}' in start_cmd
    assert f'CLAUDE_PROJECTS_ROOT={shlex.quote(str(managed_home / ".claude" / "projects"))}' in start_cmd
    assert str(profile_home) not in start_cmd


def test_claude_launcher_build_start_cmd_exports_user_session_transport_without_runtime_leaks(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / 'repo-claude-transport'
    runtime_dir = project_root / '.ccb' / 'agents' / 'reviewer' / 'provider-runtime' / 'claude'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    source_home = tmp_path / 'source-home'
    (source_home / '.claude').mkdir(parents=True, exist_ok=True)
    ambient_projects = tmp_path / 'ambient-claude-projects'
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:7890')
    monkeypatch.setenv('NODE_EXTRA_CA_CERTS', '/tmp/node-ca.pem')
    monkeypatch.setenv('WSL_INTEROP', '/run/WSL/1234_interop')
    monkeypatch.setenv('CLAUDE_PROJECTS_ROOT', str(ambient_projects))
    monkeypatch.setenv('CCB_CALLER_ACTOR', 'stale-agent')
    monkeypatch.setattr('provider_backends.claude.launcher.Path.home', lambda: source_home)
    monkeypatch.setattr('provider_backends.claude.launcher_runtime.home.Path.home', lambda: source_home)
    monkeypatch.setattr(
        claude_launcher,
        '_resolve_claude_restore_target',
        lambda **kwargs: ProviderRestoreTarget(run_cwd=runtime_dir, has_history=False),
    )
    spec = _spec('reviewer', provider='claude')
    command = ParsedStartCommand(project=None, agent_names=('reviewer',), restore=False, auto_permission=False)

    start_cmd = claude_launcher.build_start_cmd(
        command,
        spec,
        runtime_dir,
        'claude-sess-transport',
        prepared_state=_claude_prepared_state(runtime_dir),
    )

    managed_projects = project_root / '.ccb' / 'agents' / 'reviewer' / 'provider-state' / 'claude' / 'home' / '.claude' / 'projects'
    assert f'HTTPS_PROXY={shlex.quote("http://127.0.0.1:7890")}' in start_cmd
    assert f'NODE_EXTRA_CA_CERTS={shlex.quote("/tmp/node-ca.pem")}' in start_cmd
    assert f'WSL_INTEROP={shlex.quote("/run/WSL/1234_interop")}' in start_cmd
    assert f'CLAUDE_PROJECTS_ROOT={shlex.quote(str(managed_projects))}' in start_cmd
    assert str(ambient_projects) not in start_cmd
    assert 'CCB_CALLER_ACTOR=stale-agent' not in start_cmd


def test_claude_workspace_preparation_refreshes_managed_home_projection(monkeypatch, tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-claude-refresh'
    runtime_dir = project_root / '.ccb' / 'agents' / 'reviewer' / 'provider-runtime' / 'claude'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    home_dir = tmp_path / 'home'
    source_claude_dir = home_dir / '.claude'
    (source_claude_dir / 'skills' / 'review').mkdir(parents=True, exist_ok=True)
    (source_claude_dir / 'commands').mkdir(parents=True, exist_ok=True)
    (source_claude_dir / 'skills' / 'review' / 'SKILL.md').write_text('skill-v1\n', encoding='utf-8')
    (source_claude_dir / 'commands' / 'check.md').write_text('command-v1\n', encoding='utf-8')
    (source_claude_dir / 'CLAUDE.md').write_text('claude-md-v1\n', encoding='utf-8')

    spec = _spec('reviewer', provider='claude')
    command = ParsedStartCommand(project=None, agent_names=('reviewer',), restore=False, auto_permission=False)
    context = _context(project_root, command)
    plan = WorkspacePlanner().plan(spec, context.project)

    monkeypatch.setattr('provider_backends.claude.launcher.Path.home', lambda: home_dir)
    monkeypatch.setattr('provider_backends.claude.launcher_runtime.home.Path.home', lambda: home_dir)
    monkeypatch.setenv('CCB_SOURCE_HOME', str(home_dir))
    monkeypatch.setattr(
        claude_launcher,
        '_resolve_claude_restore_target',
        lambda **kwargs: ProviderRestoreTarget(run_cwd=runtime_dir, has_history=False),
    )

    _prepare_claude_home_for_test(spec, runtime_dir, workspace_path=runtime_dir)
    prepared = claude_launcher.prepare_launch_context(context, spec, plan, runtime_dir, {})
    claude_launcher.build_start_cmd(
        command,
        spec,
        runtime_dir,
        'claude-sess-refresh-1',
        prepared_state=prepared,
    )

    managed_claude_dir = project_root / '.ccb' / 'agents' / 'reviewer' / 'provider-state' / 'claude' / 'home' / '.claude'
    assert (managed_claude_dir / 'skills' / 'review' / 'SKILL.md').read_text(encoding='utf-8') == 'skill-v1\n'
    assert (managed_claude_dir / 'commands' / 'check.md').read_text(encoding='utf-8') == 'command-v1\n'
    claude_memory_v1 = (managed_claude_dir / 'CLAUDE.md').read_text(encoding='utf-8')
    assert '# CCB Managed Agent Memory' in claude_memory_v1
    assert 'claude-md-v1' in claude_memory_v1
    assert 'This project uses CCB for visible multi-agent collaboration.' in _project_memory_path(
        project_root
    ).read_text(encoding='utf-8')

    (source_claude_dir / 'skills' / 'review' / 'SKILL.md').write_text('skill-v2\n', encoding='utf-8')
    (source_claude_dir / 'commands' / 'check.md').write_text('command-v2\n', encoding='utf-8')
    (source_claude_dir / 'CLAUDE.md').write_text('claude-md-v2\n', encoding='utf-8')

    _prepare_claude_home_for_test(spec, runtime_dir, workspace_path=runtime_dir)
    prepared = claude_launcher.prepare_launch_context(context, spec, plan, runtime_dir, {})
    claude_launcher.build_start_cmd(
        command,
        spec,
        runtime_dir,
        'claude-sess-refresh-2',
        prepared_state=prepared,
    )

    assert (managed_claude_dir / 'skills' / 'review' / 'SKILL.md').read_text(encoding='utf-8') == 'skill-v2\n'
    assert (managed_claude_dir / 'commands' / 'check.md').read_text(encoding='utf-8') == 'command-v2\n'
    claude_memory_v2 = (managed_claude_dir / 'CLAUDE.md').read_text(encoding='utf-8')
    assert '# CCB Managed Agent Memory' in claude_memory_v2
    assert 'claude-md-v2' in claude_memory_v2


def test_claude_launcher_project_memory_survives_relocated_runtime_state(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / 'repo-relocated'
    runtime_root = tmp_path / 'external-runtime'
    command = ParsedStartCommand(project=None, agent_names=('reviewer',), restore=False, auto_permission=False)
    context = _context(project_root, command)
    object.__setattr__(context.paths, '_state_root', runtime_root)
    spec = _spec('reviewer', provider='claude')
    plan = WorkspacePlanner().plan(spec, context.project)
    runtime_dir = context.paths.agent_provider_runtime_dir('reviewer', 'claude')
    runtime_dir.mkdir(parents=True, exist_ok=True)
    home_dir = tmp_path / 'home'
    source_claude_dir = home_dir / '.claude'
    source_claude_dir.mkdir(parents=True, exist_ok=True)
    (source_claude_dir / 'CLAUDE.md').write_text('claude relocated source memory\n', encoding='utf-8')
    _write_project_memory(project_root, 'relocated shared memory\n')
    monkeypatch.setattr('provider_backends.claude.launcher.Path.home', lambda: home_dir)
    monkeypatch.setattr('provider_backends.claude.launcher_runtime.home.Path.home', lambda: home_dir)
    monkeypatch.setenv('CCB_SOURCE_HOME', str(home_dir))
    monkeypatch.setattr(
        claude_launcher,
        '_resolve_claude_restore_target',
        lambda **kwargs: ProviderRestoreTarget(run_cwd=plan.workspace_path, has_history=False),
    )

    from cli.services.provider_hooks import prepare_provider_workspace

    prepare_provider_workspace(
        layout=context.paths,
        spec=spec,
        workspace_path=plan.workspace_path,
        completion_dir=runtime_dir / 'completion',
        agent_name=spec.name,
        refresh_profile=False,
    )
    prepared = claude_launcher.prepare_launch_context(context, spec, plan, runtime_dir, {})
    claude_launcher.build_start_cmd(
        command,
        spec,
        runtime_dir,
        'claude-sess-relocated',
        prepared_state=prepared,
    )

    managed_memory = runtime_root / 'agents' / 'reviewer' / 'provider-state' / 'claude' / 'home' / '.claude' / 'CLAUDE.md'
    text = managed_memory.read_text(encoding='utf-8')
    assert text.startswith('# CCB Managed Agent Memory')
    assert 'relocated shared memory' in text
    assert 'claude relocated source memory' in text


def test_claude_launcher_build_start_cmd_preserves_managed_auth_when_system_home_logged_out(
    monkeypatch, tmp_path: Path
) -> None:
    project_root = tmp_path / 'repo-claude-auth-refresh'
    runtime_dir = project_root / '.ccb' / 'agents' / 'reviewer' / 'provider-runtime' / 'claude'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    home_dir = tmp_path / 'home'
    source_claude_dir = home_dir / '.claude'
    source_claude_dir.mkdir(parents=True, exist_ok=True)
    (source_claude_dir / 'settings.json').write_text(
        json.dumps(
            {
                'env': {
                    'ANTHROPIC_BASE_URL': 'https://claude.example.test',
                },
                'theme': 'light',
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    managed_settings = project_root / '.ccb' / 'agents' / 'reviewer' / 'provider-state' / 'claude' / 'home' / '.claude' / 'settings.json'
    managed_settings.parent.mkdir(parents=True, exist_ok=True)
    managed_settings.write_text(
        json.dumps(
            {
                'env': {
                    'ANTHROPIC_AUTH_TOKEN': 'managed-token',
                    'ANTHROPIC_BASE_URL': 'https://managed.example.test',
                },
                'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'echo hook'}]}]},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    spec = _spec('reviewer', provider='claude')
    command = ParsedStartCommand(project=None, agent_names=('reviewer',), restore=False, auto_permission=False)

    monkeypatch.setattr('provider_backends.claude.launcher.Path.home', lambda: home_dir)
    monkeypatch.setattr('provider_backends.claude.launcher_runtime.home.Path.home', lambda: home_dir)
    monkeypatch.setenv('CCB_SOURCE_HOME', str(home_dir))
    monkeypatch.setattr(
        claude_launcher,
        '_resolve_claude_restore_target',
        lambda **kwargs: ProviderRestoreTarget(run_cwd=runtime_dir, has_history=False),
    )

    prepared = _prepare_claude_home_for_test(spec, runtime_dir, workspace_path=runtime_dir)
    start_cmd = claude_launcher.build_start_cmd(
        command,
        spec,
        runtime_dir,
        'claude-sess-auth-refresh',
        prepared_state=prepared,
    )

    payload = json.loads(managed_settings.read_text(encoding='utf-8'))
    assert payload['env']['ANTHROPIC_AUTH_TOKEN'] == 'managed-token'
    assert payload['env']['ANTHROPIC_BASE_URL'] == 'https://claude.example.test'
    assert payload['theme'] == 'light'
    assert payload['hooks']['Stop'][0]['hooks'][0]['command'] == 'echo hook'
    assert f'HOME={shlex.quote(str(project_root / ".ccb" / "agents" / "reviewer" / "provider-state" / "claude" / "home"))}' in start_cmd


def test_claude_launcher_build_start_cmd_projects_official_login_auth_into_managed_home(
    monkeypatch, tmp_path: Path
) -> None:
    project_root = tmp_path / 'repo-claude-login-auth'
    runtime_dir = project_root / '.ccb' / 'agents' / 'reviewer' / 'provider-runtime' / 'claude'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    home_dir = tmp_path / 'home'
    source_credentials = home_dir / '.claude' / '.credentials.json'
    source_credentials.parent.mkdir(parents=True, exist_ok=True)
    source_credentials.write_text(
        json.dumps({'claudeAiOauth': {'refreshToken': 'system-refresh-token'}}, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )

    spec = _spec('reviewer', provider='claude')
    command = ParsedStartCommand(project=None, agent_names=('reviewer',), restore=False, auto_permission=False)

    monkeypatch.setattr('provider_backends.claude.launcher.Path.home', lambda: home_dir)
    monkeypatch.setattr('provider_backends.claude.launcher_runtime.home.Path.home', lambda: home_dir)
    monkeypatch.setenv('CCB_SOURCE_HOME', str(home_dir))
    monkeypatch.setattr(
        claude_launcher,
        '_resolve_claude_restore_target',
        lambda **kwargs: ProviderRestoreTarget(run_cwd=runtime_dir, has_history=False),
    )

    prepared = _prepare_claude_home_for_test(spec, runtime_dir, workspace_path=runtime_dir)
    claude_launcher.build_start_cmd(
        command,
        spec,
        runtime_dir,
        'claude-sess-login-auth',
        prepared_state=prepared,
    )

    managed_auth = (
        project_root
        / '.ccb'
        / 'agents'
        / 'reviewer'
        / 'provider-state'
        / 'claude'
        / 'home'
        / '.claude'
        / '.credentials.json'
    )
    assert json.loads(managed_auth.read_text(encoding='utf-8'))['claudeAiOauth']['refreshToken'] == 'system-refresh-token'


def test_gemini_launcher_build_start_cmd_uses_isolated_profile_api_env(tmp_path: Path) -> None:
    runtime_dir = tmp_path / 'runtime'
    _write_provider_profile(
        runtime_dir,
        ResolvedProviderProfile(
            provider='gemini',
            agent_name='reviewer',
            mode='isolated',
            profile_root=str(tmp_path / 'profile'),
            runtime_home=None,
            env={'GEMINI_API_KEY': 'gemini-key'},
            inherit_api=False,
        ),
    )
    spec = _spec('reviewer', provider='gemini')
    command = ParsedStartCommand(project=None, agent_names=('reviewer',), restore=False, auto_permission=False)

    start_cmd = gemini_launcher.build_start_cmd(
        command,
        spec,
        runtime_dir,
        'gemini-sess-iso',
        prepared_state={'project_root': tmp_path},
    )

    assert 'unset GEMINI_API_KEY' in start_cmd
    assert f'GEMINI_API_KEY={shlex.quote("gemini-key")}' in start_cmd
    assert start_cmd.endswith('gemini')


def test_gemini_launcher_build_start_cmd_exports_user_session_transport_without_runtime_leaks(
    monkeypatch,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / 'repo-gemini-transport'
    runtime_dir = project_root / '.ccb' / 'agents' / 'reviewer' / 'provider-runtime' / 'gemini'
    runtime_dir.mkdir(parents=True, exist_ok=True)
    ambient_root = tmp_path / 'ambient-gemini-root'
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:7890')
    monkeypatch.setenv('REQUESTS_CA_BUNDLE', '/tmp/requests-ca.pem')
    monkeypatch.setenv('WSL_INTEROP', '/run/WSL/1234_interop')
    monkeypatch.setenv('GEMINI_ROOT', str(ambient_root))
    monkeypatch.setenv('CCB_CALLER_ACTOR', 'stale-agent')
    spec = _spec('reviewer', provider='gemini')
    command = ParsedStartCommand(project=None, agent_names=('reviewer',), restore=False, auto_permission=False)

    start_cmd = gemini_launcher.build_start_cmd(
        command,
        spec,
        runtime_dir,
        'gemini-sess-transport',
        prepared_state={'project_root': project_root},
    )

    managed_home = project_root / '.ccb' / 'agents' / 'reviewer' / 'provider-state' / 'gemini' / 'home'
    managed_root = managed_home / '.gemini' / 'tmp'
    assert f'HTTPS_PROXY={shlex.quote("http://127.0.0.1:7890")}' in start_cmd
    assert f'REQUESTS_CA_BUNDLE={shlex.quote("/tmp/requests-ca.pem")}' in start_cmd
    assert f'WSL_INTEROP={shlex.quote("/run/WSL/1234_interop")}' in start_cmd
    assert f'GEMINI_ROOT={shlex.quote(str(managed_root))}' in start_cmd
    assert str(ambient_root) not in start_cmd
    assert 'CCB_CALLER_ACTOR=stale-agent' not in start_cmd
