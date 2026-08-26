from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib

import pytest

from agents.models import (
    AgentSpec,
    PermissionMode,
    ProviderProfileSpec,
    QueuePolicy,
    RestoreMode,
    RuntimeMode,
    SkillOverlaySpec,
    WorkspaceMode,
)
from cli.services.role_command_policy import RoleCommandPolicy
import provider_backends.claude.launcher_runtime.home as claude_home_runtime
from provider_backends.claude.launcher_runtime.home import materialize_claude_home_config
import provider_backends.droid.home as droid_home_runtime
from provider_backends.droid.home import materialize_droid_home_config
import provider_backends.gemini.launcher_runtime.home as gemini_home_runtime
from provider_backends.gemini.launcher_runtime.home import materialize_gemini_home_config
from provider_backends.qwen.home import materialize_qwen_home_config
import provider_core.projected_assets as projected_assets
import provider_core.projected_settings as projected_settings
import provider_profiles.codex_home_config as codex_home_config
from provider_profiles.codex_home_config import (
    codex_provider_authority_fingerprint,
    materialize_codex_home_config,
)
from provider_profiles import materialize_provider_profile, validate_provider_runtime_home_uniqueness
from provider_core.pathing import session_filename_for_agent
from storage.paths import PathLayout


def _spec(name: str, provider: str = "codex", *, provider_profile: ProviderProfileSpec | None = None, model: str | None = None) -> AgentSpec:
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
        provider_profile=provider_profile or ProviderProfileSpec(),
        model=model,
    )


def _write_project_memory(project_root: Path, text: str) -> None:
    path = project_root / '.ccb' / 'ccb_memory.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def _latest_agent_event(layout: PathLayout, agent_name: str, event_type: str) -> dict:
    events_path = layout.agent_events_path(agent_name)
    events = [
        json.loads(line)
        for line in events_path.read_text(encoding='utf-8').splitlines()
        if line.strip()
    ]
    for event in reversed(events):
        if event.get('event_type') == event_type:
            return event
    raise AssertionError(f'{event_type} event not found: {events}')


def test_source_test_codex_shell_path_prefers_project_command_shims(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / 'repo'
    command_dir = project_root / '.ccb' / 'bin'
    command_dir.mkdir(parents=True)
    for name in ('ask', 'ccb', 'codex-reconnect'):
        (command_dir / name).write_text('#!/bin/sh\n', encoding='utf-8')
    source_home = tmp_path / 'source-home'
    source_home.mkdir()
    (source_home / 'config.toml').write_text(
        '[shell_environment_policy.set]\nKEEP = "yes"\nPATH = "/user/bin:/usr/bin"\n',
        encoding='utf-8',
    )
    target_home = tmp_path / 'target-home'
    monkeypatch.setenv('CCB_TEST_ENTRYPOINT', '1')

    codex_home_config.materialize_codex_home_config(
        target_home,
        source_home=source_home,
        project_root=project_root,
        agent_name='agent1',
        workspace_path=project_root,
    )

    config = tomllib.loads((target_home / 'config.toml').read_text(encoding='utf-8'))
    configured = config['shell_environment_policy']['set']
    assert configured['KEEP'] == 'yes'
    assert configured['PATH'].split(os.pathsep) == [
        str(command_dir.resolve()),
        '/user/bin',
        '/usr/bin',
    ]


def test_source_test_codex_shell_path_supports_missing_user_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / 'repo'
    command_dir = project_root / '.ccb' / 'bin'
    command_dir.mkdir(parents=True)
    for name in ('ask', 'ccb', 'codex-reconnect'):
        (command_dir / name).write_text('#!/bin/sh\n', encoding='utf-8')
    source_home = tmp_path / 'source-home'
    source_home.mkdir()
    (source_home / 'config.toml').write_text('model = "gpt-test"\n', encoding='utf-8')
    target_home = tmp_path / 'target-home'
    monkeypatch.setenv('CCB_TEST_ENTRYPOINT', '1')
    monkeypatch.setenv('PATH', '/user/bin:/usr/bin')

    codex_home_config.materialize_codex_home_config(
        target_home,
        source_home=source_home,
        project_root=project_root,
        agent_name='agent1',
        workspace_path=project_root,
    )

    config = tomllib.loads((target_home / 'config.toml').read_text(encoding='utf-8'))
    assert config['shell_environment_policy']['set']['PATH'].split(os.pathsep) == [
        str(command_dir.resolve()),
        '/user/bin',
        '/usr/bin',
    ]


def test_release_codex_shell_path_preserves_user_policy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / 'repo'
    command_dir = project_root / '.ccb' / 'bin'
    command_dir.mkdir(parents=True)
    for name in ('ask', 'ccb'):
        (command_dir / name).write_text('#!/bin/sh\n', encoding='utf-8')
    source_home = tmp_path / 'source-home'
    source_home.mkdir()
    (source_home / 'config.toml').write_text(
        '[shell_environment_policy.set]\nPATH = "/user/bin:/usr/bin"\n',
        encoding='utf-8',
    )
    target_home = tmp_path / 'target-home'
    monkeypatch.delenv('CCB_TEST_ENTRYPOINT', raising=False)

    codex_home_config.materialize_codex_home_config(
        target_home,
        source_home=source_home,
        project_root=project_root,
        agent_name='agent1',
        workspace_path=project_root,
    )

    config = tomllib.loads((target_home / 'config.toml').read_text(encoding='utf-8'))
    assert config['shell_environment_policy']['set']['PATH'] == '/user/bin:/usr/bin'


def _write_codex_plugin_source(
    home: Path,
    *,
    plugin_name: str = 'demo-plugin',
    sha: str | None = 'plugins-sha-v1',
    marketplace_name: str = 'openai-curated',
    skill_body: str = 'plugin skill v1\n',
) -> None:
    plugin_root = home / '.tmp' / 'plugins'
    (plugin_root / '.agents' / 'plugins').mkdir(parents=True, exist_ok=True)
    (plugin_root / '.agents' / 'skills' / 'plugin-creator').mkdir(parents=True, exist_ok=True)
    (plugin_root / 'plugins' / plugin_name / '.codex-plugin').mkdir(parents=True, exist_ok=True)
    (plugin_root / 'plugins' / plugin_name / 'skills' / plugin_name).mkdir(parents=True, exist_ok=True)
    (home / '.tmp').mkdir(parents=True, exist_ok=True)
    if sha is None:
        (home / '.tmp' / 'plugins.sha').unlink(missing_ok=True)
    else:
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


def _write_droid_plugin_source(home: Path) -> None:
    plugin_root = home / 'plugins'
    marketplace_root = plugin_root / 'marketplaces' / 'test-marketplace'
    cache_root = plugin_root / 'cache' / 'test-marketplace' / 'test-plugin' / '1.0.0'
    (marketplace_root / '.factory-plugin').mkdir(parents=True, exist_ok=True)
    (cache_root / '.factory-plugin').mkdir(parents=True, exist_ok=True)
    (marketplace_root / '.factory-plugin' / 'marketplace.json').write_text(
        json.dumps({'name': 'test-marketplace', 'plugins': []}),
        encoding='utf-8',
    )
    (cache_root / '.factory-plugin' / 'plugin.json').write_text(
        json.dumps({'name': 'test-plugin', 'version': '1.0.0'}),
        encoding='utf-8',
    )
    (plugin_root / 'known_marketplaces.json').write_text(
        json.dumps(
            {
                'test-marketplace': {
                    'source': {'source': 'directory', 'path': str(marketplace_root)},
                    'installLocation': str(marketplace_root),
                }
            }
        ),
        encoding='utf-8',
    )
    (plugin_root / 'installed_plugins.json').write_text(
        json.dumps(
            {
                'schemaVersion': 1,
                'plugins': {
                    'test-plugin@test-marketplace': [
                        {
                            'scope': 'user',
                            'installPath': str(cache_root),
                            'version': '1.0.0',
                        }
                    ]
                },
            }
        ),
        encoding='utf-8',
    )
    (home / 'settings.json').write_text(
        json.dumps({'enabledPlugins': {'test-plugin@test-marketplace': True}}),
        encoding='utf-8',
    )


def _hard_role_policy(tmp_path: Path) -> RoleCommandPolicy:
    return RoleCommandPolicy(
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


def test_refresh_codex_auth_projection_updates_only_auth_files(tmp_path: Path) -> None:
    source_home = tmp_path / 'source'
    target_home = tmp_path / 'target'
    source_home.mkdir()
    target_home.mkdir()
    (source_home / 'auth.json').write_text('{"tokens":{"refresh_token":"fresh"}}\n', encoding='utf-8')
    (source_home / 'config.toml').write_text(
        'token_file = "$CODEX_HOME/company-extra-token"\n',
        encoding='utf-8',
    )
    (source_home / 'company-extra-token').write_text('fresh-token\n', encoding='utf-8')
    (target_home / 'auth.json').write_text('{"tokens":{"refresh_token":"revoked"}}\n', encoding='utf-8')
    (target_home / 'config.toml').write_text('model = "managed"\n', encoding='utf-8')
    (target_home / 'company-extra-token').write_text('old-token\n', encoding='utf-8')

    result = codex_home_config.refresh_codex_auth_projection(
        target_home,
        source_home=source_home,
    )

    assert result.refreshed is True
    assert result.changed_files == ('auth.json', 'company-extra-token')
    assert (target_home / 'auth.json').read_text(encoding='utf-8') == '{"tokens":{"refresh_token":"fresh"}}\n'
    assert (target_home / 'company-extra-token').read_text(encoding='utf-8') == 'fresh-token\n'
    assert (target_home / 'config.toml').read_text(encoding='utf-8') == 'model = "managed"\n'
    manifest = json.loads((target_home / '.ccb-auth-projection.json').read_text(encoding='utf-8'))
    assert manifest['status'] == 'inherited_auth_recovered'


@pytest.mark.parametrize('source_text', [None, 'not-json\n', '{}\n'])
def test_refresh_codex_auth_projection_preserves_target_when_source_is_invalid(
    tmp_path: Path,
    source_text: str | None,
) -> None:
    source_home = tmp_path / 'source'
    target_home = tmp_path / 'target'
    source_home.mkdir()
    target_home.mkdir()
    target_auth = '{"tokens":{"refresh_token":"local"}}\n'
    (target_home / 'auth.json').write_text(target_auth, encoding='utf-8')
    if source_text is not None:
        (source_home / 'auth.json').write_text(source_text, encoding='utf-8')

    result = codex_home_config.refresh_codex_auth_projection(
        target_home,
        source_home=source_home,
    )

    assert result.refreshed is False
    assert 'missing or invalid' in result.detail
    assert (target_home / 'auth.json').read_text(encoding='utf-8') == target_auth


def test_refresh_codex_auth_projection_rejects_symlink_source(tmp_path: Path) -> None:
    source_home = tmp_path / 'source'
    target_home = tmp_path / 'target'
    external_home = tmp_path / 'external'
    source_home.mkdir()
    target_home.mkdir()
    external_home.mkdir()
    external_auth = external_home / 'auth.json'
    external_auth.write_text('{"tokens":{"refresh_token":"external"}}\n', encoding='utf-8')
    (source_home / 'auth.json').symlink_to(external_auth)
    target_auth = '{"tokens":{"refresh_token":"agent-local"}}\n'
    (target_home / 'auth.json').write_text(target_auth, encoding='utf-8')

    result = codex_home_config.refresh_codex_auth_projection(
        target_home,
        source_home=source_home,
    )

    assert result.refreshed is False
    assert 'missing or invalid' in result.detail
    assert (target_home / 'auth.json').read_text(encoding='utf-8') == target_auth
    assert external_auth.read_text(encoding='utf-8') == '{"tokens":{"refresh_token":"external"}}\n'


def test_materialize_codex_profile_copies_inherited_assets(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo'
    source_home = tmp_path / 'system-codex-home'
    (source_home / 'skills').mkdir(parents=True, exist_ok=True)
    (source_home / 'commands').mkdir(parents=True, exist_ok=True)
    (source_home / 'config.toml').write_text(
        '\n'.join(
            [
                'model = "gpt-5"',
                'env_key = "${CODEX_HOME:-$HOME/.codex}/company-codex-api-key"',
                'token_file = "$CODEX_HOME/company-extra-token"',
                '',
            ]
        ),
        encoding='utf-8',
    )
    (source_home / 'auth.json').write_text('{"OPENAI_API_KEY":"system-key"}', encoding='utf-8')
    (source_home / 'company-codex-api-key').write_text('company-key\n', encoding='utf-8')
    (source_home / 'company-codex.config.toml').write_text('profile = "company"\n', encoding='utf-8')
    (source_home / 'company-extra-token').write_text('extra-token\n', encoding='utf-8')
    (source_home / 'skills' / 'demo').mkdir(parents=True, exist_ok=True)
    (source_home / 'skills' / 'demo' / 'SKILL.md').write_text('demo skill\n', encoding='utf-8')
    (source_home / 'commands' / 'demo.md').write_text('demo command\n', encoding='utf-8')
    _write_codex_plugin_source(source_home)
    monkeypatch.setenv('CODEX_HOME', str(source_home))
    layout = PathLayout(project_root)

    profile = materialize_provider_profile(
        layout=layout,
        spec=_spec(
            'agent1',
            provider_profile=ProviderProfileSpec(
                mode='isolated',
                inherit_api=False,
                inherit_auth=True,
                inherit_config=True,
                inherit_skills=True,
                inherit_commands=True,
            ),
        ),
        workspace_path=project_root,
    )

    runtime_home = Path(profile.runtime_home or '')
    assert runtime_home == layout.agent_provider_state_dir('agent1', 'codex') / 'home'
    assert profile.profile_root is None
    assert not (layout.provider_profiles_dir / 'agent1' / 'codex').exists()
    assert runtime_home.is_dir()
    assert (runtime_home / 'config.toml').is_file()
    assert (runtime_home / 'auth.json').is_file()
    assert (runtime_home / 'company-codex-api-key').read_text(encoding='utf-8') == 'company-key\n'
    assert (runtime_home / 'company-codex.config.toml').read_text(encoding='utf-8') == 'profile = "company"\n'
    assert (runtime_home / 'company-extra-token').read_text(encoding='utf-8') == 'extra-token\n'
    auth_manifest = json.loads((runtime_home / '.ccb-auth-projection.json').read_text(encoding='utf-8'))
    assert auth_manifest['record_type'] == 'ccb_codex_auth_projection'
    assert auth_manifest['status'] == 'inherited_auth'
    assert auth_manifest['projected_sidecars'] == [
        'company-codex-api-key',
        'company-codex.config.toml',
        'company-extra-token',
    ]
    assert any(
        item['name'] == 'company-codex-api-key' and item['source_exists'] and item['target_exists']
        for item in auth_manifest['files']
    )
    (source_home / 'company-codex-api-key').write_text('company-key-v2\n', encoding='utf-8')
    materialize_provider_profile(
        layout=layout,
        spec=_spec(
            'agent1',
            provider_profile=ProviderProfileSpec(
                mode='isolated',
                inherit_api=False,
                inherit_auth=True,
                inherit_config=True,
                inherit_skills=True,
                inherit_commands=True,
            ),
        ),
        workspace_path=project_root,
    )
    assert (runtime_home / 'company-codex-api-key').read_text(encoding='utf-8') == 'company-key-v2\n'
    assert (runtime_home / 'skills' / 'demo' / 'SKILL.md').is_file()
    assert (runtime_home / 'skills' / 'demo.ccb-projection.json').is_file()
    assert not (runtime_home / 'skills').is_symlink()
    assert (runtime_home / 'commands' / 'demo.md').is_file()
    assert (runtime_home / 'commands').is_symlink()
    assert (runtime_home / '.tmp' / 'plugins.sha').read_text(encoding='utf-8') == 'plugins-sha-v1\n'
    assert (runtime_home / '.tmp' / 'plugins').is_symlink()
    assert (runtime_home / '.tmp' / 'plugins').resolve() == (
        project_root / '.ccb' / 'shared-cache' / 'codex' / 'plugin-bundles' / 'plugins-sha-v1'
    ).resolve()
    assert (runtime_home / '.tmp' / 'plugins' / '.agents' / 'plugins' / 'marketplace.json').is_file()
    assert (runtime_home / '.tmp' / 'plugins' / 'plugins' / 'demo-plugin' / '.codex-plugin' / 'plugin.json').is_file()
    assert (runtime_home / 'sessions').is_dir()


def test_materialize_codex_profile_disables_external_migration_prompt(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo'
    source_home = tmp_path / 'system-codex-home'
    source_home.mkdir(parents=True, exist_ok=True)
    (source_home / 'company-codex-api-key').write_text('system-key\n', encoding='utf-8')
    (source_home / 'company-codex.config.toml').write_text('profile = "system"\n', encoding='utf-8')
    (source_home / 'config.toml').write_text(
        '\n'.join(
            [
                'model = "gpt-5.5"',
                'check_for_update_on_startup = true',
                '',
                '[features]',
                'external_migration = true',
                'memories = true',
                '',
            ]
        ),
        encoding='utf-8',
    )
    monkeypatch.setenv('CODEX_HOME', str(source_home))

    profile = materialize_provider_profile(
        layout=PathLayout(project_root),
        spec=_spec('agent1', provider_profile=ProviderProfileSpec(mode='isolated')),
        workspace_path=project_root,
    )

    config_text = (Path(profile.runtime_home or '') / 'config.toml').read_text(encoding='utf-8')
    assert 'model = "gpt-5.5"' in config_text
    assert '[features]' in config_text
    assert 'memories = true' in config_text
    assert 'external_migration = false' in config_text
    assert 'external_migration = true' not in config_text
    config = tomllib.loads(config_text)
    assert config['check_for_update_on_startup'] is False


def test_materialize_codex_profile_marks_project_and_workspace_trusted(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo'
    workspace_path = tmp_path / 'repo-worktree'
    source_home = tmp_path / 'system-codex-home'
    source_home.mkdir(parents=True, exist_ok=True)
    workspace_path.mkdir(parents=True, exist_ok=True)
    (source_home / 'config.toml').write_text('model = "gpt-5.5"\n', encoding='utf-8')
    monkeypatch.setenv('CODEX_HOME', str(source_home))

    profile = materialize_provider_profile(
        layout=PathLayout(project_root),
        spec=_spec('agent1', provider_profile=ProviderProfileSpec(mode='isolated')),
        workspace_path=workspace_path,
    )

    config = tomllib.loads((Path(profile.runtime_home or '') / 'config.toml').read_text(encoding='utf-8'))
    assert config['projects'][str(project_root.resolve())]['trust_level'] == 'trusted'
    assert config['projects'][str(workspace_path.resolve())]['trust_level'] == 'trusted'


def test_materialize_codex_profile_preserves_inline_table_arrays(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    source_home = tmp_path / 'system-codex-home'
    source_home.mkdir(parents=True, exist_ok=True)
    (source_home / 'config.toml').write_text(
        '\n'.join(
            [
                'model = "gpt-5.5"',
                'mcp_servers = [{ name = "puppeteer", enabled = true, args = ["-y", "pkg"] }]',
                '',
                '[features]',
                'external_migration = true',
                '',
            ]
        ),
        encoding='utf-8',
    )
    monkeypatch.setenv('CODEX_HOME', str(source_home))

    profile = materialize_provider_profile(
        layout=PathLayout(project_root),
        spec=_spec('agent1', provider_profile=ProviderProfileSpec(mode='isolated')),
        workspace_path=project_root,
    )

    config_text = (Path(profile.runtime_home or '') / 'config.toml').read_text(encoding='utf-8')
    assert 'mcp_servers = [{ name = "puppeteer", enabled = true, args = ["-y", "pkg"] }]' in config_text
    assert 'external_migration = false' in config_text


def test_materialize_codex_profile_merges_agent_mcp_server_overrides(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    source_home = tmp_path / 'system-codex-home'
    source_home.mkdir(parents=True, exist_ok=True)
    (source_home / 'config.toml').write_text(
        '\n'.join(
            [
                'model = "gpt-5.5"',
                '',
                '[mcp_servers.shared]',
                'command = "old-shared"',
                '',
                '[mcp_servers.keep]',
                'command = "keep-cmd"',
                '',
            ]
        ),
        encoding='utf-8',
    )
    monkeypatch.setenv('CODEX_HOME', str(source_home))

    profile = materialize_provider_profile(
        layout=PathLayout(project_root),
        spec=_spec(
            'agent1',
            provider_profile=ProviderProfileSpec(
                mode='isolated',
                mcp_servers={
                    'codegraph': {'command': '/usr/local/bin/codegraph', 'args': ['serve', '--mcp']},
                    'shared': {'command': 'new-shared', 'env': {'MODE': 'agent'}},
                },
            ),
        ),
        workspace_path=project_root,
    )

    config = tomllib.loads((Path(profile.runtime_home or '') / 'config.toml').read_text(encoding='utf-8'))

    assert config['mcp_servers']['keep']['command'] == 'keep-cmd'
    assert config['mcp_servers']['shared']['command'] == 'new-shared'
    assert config['mcp_servers']['shared']['env'] == {'MODE': 'agent'}
    assert config['mcp_servers']['codegraph']['command'] == '/usr/local/bin/codegraph'
    assert config['mcp_servers']['codegraph']['args'] == ['serve', '--mcp']
    assert config['features']['external_migration'] is False


def test_materialize_codex_profile_merges_agent_plugin_overrides(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    source_home = tmp_path / 'system-codex-home'
    source_home.mkdir(parents=True, exist_ok=True)
    (source_home / 'config.toml').write_text(
        '\n'.join(
            [
                'model = "gpt-5.5"',
                '',
                '[plugins."github@openai-curated"]',
                'enabled = true',
                '',
                '[plugins."superpowers@openai-curated"]',
                'enabled = true',
                '',
            ]
        ),
        encoding='utf-8',
    )
    monkeypatch.setenv('CODEX_HOME', str(source_home))

    profile = materialize_provider_profile(
        layout=PathLayout(project_root),
        spec=_spec(
            'agent1',
            provider_profile=ProviderProfileSpec(
                mode='isolated',
                plugins={
                    'github@openai-curated': {'enabled': False},
                    'agentmemory@agentmemory': {'enabled': True},
                },
            ),
        ),
        workspace_path=project_root,
    )

    config = tomllib.loads((Path(profile.runtime_home or '') / 'config.toml').read_text(encoding='utf-8'))

    assert config['plugins']['github@openai-curated']['enabled'] is False
    assert config['plugins']['superpowers@openai-curated']['enabled'] is True
    assert config['plugins']['agentmemory@agentmemory']['enabled'] is True
    assert config['features']['external_migration'] is False


def test_materialize_codex_profile_merges_plugin_overrides_from_env(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    source_home = tmp_path / 'system-codex-home'
    source_home.mkdir(parents=True, exist_ok=True)
    (source_home / 'config.toml').write_text(
        '\n'.join(
            [
                'model = "gpt-5.5"',
                '',
                '[plugins."github@openai-curated"]',
                'enabled = true',
                '',
            ]
        ),
        encoding='utf-8',
    )
    monkeypatch.setenv('CODEX_HOME', str(source_home))

    profile = materialize_provider_profile(
        layout=PathLayout(project_root),
        spec=_spec(
            'agent1',
            provider_profile=ProviderProfileSpec(
                mode='isolated',
                env={
                    'CCB_CODEX_PLUGIN_OVERRIDES_JSON': json.dumps(
                        {
                            'github@openai-curated': {'enabled': False},
                            'agentmemory@agentmemory': {'enabled': True},
                        }
                    )
                },
            ),
        ),
        workspace_path=project_root,
    )

    config = tomllib.loads((Path(profile.runtime_home or '') / 'config.toml').read_text(encoding='utf-8'))

    assert config['plugins']['github@openai-curated']['enabled'] is False
    assert config['plugins']['agentmemory@agentmemory']['enabled'] is True


def test_materialize_codex_profile_env_plugin_overrides_agent_profile(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    source_home = tmp_path / 'system-codex-home'
    source_home.mkdir(parents=True, exist_ok=True)
    (source_home / 'config.toml').write_text(
        '\n'.join(
            [
                'model = "gpt-5.5"',
                '',
                '[plugins."github@openai-curated"]',
                'enabled = true',
                'mode = "source"',
                '',
            ]
        ),
        encoding='utf-8',
    )
    monkeypatch.setenv('CODEX_HOME', str(source_home))

    profile = materialize_provider_profile(
        layout=PathLayout(project_root),
        spec=_spec(
            'agent1',
            provider_profile=ProviderProfileSpec(
                mode='isolated',
                plugins={'github@openai-curated': {'enabled': False, 'mode': 'profile'}},
                env={
                    'CCB_CODEX_PLUGIN_OVERRIDES_JSON': json.dumps(
                        {'github@openai-curated': {'enabled': True, 'mode': 'env'}}
                    )
                },
            ),
        ),
        workspace_path=project_root,
    )

    config = tomllib.loads((Path(profile.runtime_home or '') / 'config.toml').read_text(encoding='utf-8'))

    assert config['plugins']['github@openai-curated']['enabled'] is True
    assert config['plugins']['github@openai-curated']['mode'] == 'env'


def test_materialize_codex_profile_writes_plugin_overrides_without_source_config(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    source_home = tmp_path / 'system-codex-home'
    source_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv('CODEX_HOME', str(source_home))

    profile = materialize_provider_profile(
        layout=PathLayout(project_root),
        spec=_spec(
            'agent1',
            provider_profile=ProviderProfileSpec(
                mode='isolated',
                plugins={'agentmemory@agentmemory': {'enabled': True}},
            ),
        ),
        workspace_path=project_root,
    )

    config = tomllib.loads((Path(profile.runtime_home or '') / 'config.toml').read_text(encoding='utf-8'))

    assert config['plugins']['agentmemory@agentmemory']['enabled'] is True


def test_materialize_codex_profile_preserves_nested_inline_table_arrays(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    source_home = tmp_path / 'system-codex-home'
    source_home.mkdir(parents=True, exist_ok=True)
    (source_home / 'config.toml').write_text(
        '\n'.join(
            [
                'model = "gpt-5.5"',
                'tools = [{ name = "demo", config = { retries = 2, enabled = true } }]',
                '',
                '[features]',
                'external_migration = true',
                '',
            ]
        ),
        encoding='utf-8',
    )
    monkeypatch.setenv('CODEX_HOME', str(source_home))

    profile = materialize_provider_profile(
        layout=PathLayout(project_root),
        spec=_spec('agent1', provider_profile=ProviderProfileSpec(mode='isolated')),
        workspace_path=project_root,
    )

    config_text = (Path(profile.runtime_home or '') / 'config.toml').read_text(encoding='utf-8')
    assert 'tools = [{ name = "demo", config = { retries = 2, enabled = true } }]' in config_text
    assert 'external_migration = false' in config_text


def test_materialize_codex_profile_disables_external_migration_without_toml_reader(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    source_home = tmp_path / 'system-codex-home'
    source_home.mkdir(parents=True, exist_ok=True)
    (source_home / 'config.toml').write_text(
        '\n'.join(
            [
                'model = "gpt-5.5"',
                'check_for_update_on_startup = true',
                '',
                '[features]',
                'external_migration = true',
                'memories = true',
                '',
                '[projects."/tmp/demo"]',
                'trust_level = "trusted"',
                '',
            ]
        ),
        encoding='utf-8',
    )
    monkeypatch.setenv('CODEX_HOME', str(source_home))
    monkeypatch.setattr(codex_home_config, '_import_optional_toml_reader', lambda: None)

    profile = materialize_provider_profile(
        layout=PathLayout(project_root),
        spec=_spec('agent1', provider_profile=ProviderProfileSpec(mode='isolated')),
        workspace_path=project_root,
    )

    config_text = (Path(profile.runtime_home or '') / 'config.toml').read_text(encoding='utf-8')
    assert config_text.count('[features]') == 1
    assert 'model = "gpt-5.5"' in config_text
    assert 'memories = true' in config_text
    assert 'external_migration = false' in config_text
    assert 'external_migration = true' not in config_text
    config = tomllib.loads(config_text)
    assert config['check_for_update_on_startup'] is False
    assert '[projects."/tmp/demo"]' in config_text
    assert 'trust_level = "trusted"' in config_text
    assert f'[projects."{project_root.resolve()}"]' in config_text


def test_materialize_codex_profile_merges_final_features_without_toml_reader(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    source_home = tmp_path / 'system-codex-home'
    source_home.mkdir(parents=True, exist_ok=True)
    (source_home / 'config.toml').write_text(
        '\n'.join(
            [
                'model = "gpt-5.5"',
                '',
                '[features]',
                'external_migration = true',
                'memories = true',
                '',
            ]
        ),
        encoding='utf-8',
    )
    monkeypatch.setenv('CODEX_HOME', str(source_home))
    monkeypatch.setattr(codex_home_config, '_import_optional_toml_reader', lambda: None)

    profile = materialize_provider_profile(
        layout=PathLayout(project_root),
        spec=_spec('agent1', provider_profile=ProviderProfileSpec(mode='isolated')),
        workspace_path=project_root,
    )

    config_text = (Path(profile.runtime_home or '') / 'config.toml').read_text(encoding='utf-8')
    assert config_text.count('[features]') == 1
    assert 'memories = true' in config_text
    assert 'external_migration = false' in config_text
    assert 'external_migration = true' not in config_text


def test_materialize_codex_profile_stops_feature_merge_at_array_table_without_toml_reader(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    source_home = tmp_path / 'system-codex-home'
    source_home.mkdir(parents=True, exist_ok=True)
    (source_home / 'config.toml').write_text(
        '\n'.join(
            [
                'model = "gpt-5.5"',
                '',
                '[features]',
                'external_migration = true',
                'memories = true',
                '',
                '[[tools]]',
                'external_migration = true',
                'name = "not-a-feature"',
                '',
            ]
        ),
        encoding='utf-8',
    )
    monkeypatch.setenv('CODEX_HOME', str(source_home))
    monkeypatch.setattr(codex_home_config, '_import_optional_toml_reader', lambda: None)

    profile = materialize_provider_profile(
        layout=PathLayout(project_root),
        spec=_spec('agent1', provider_profile=ProviderProfileSpec(mode='isolated')),
        workspace_path=project_root,
    )

    config_text = (Path(profile.runtime_home or '') / 'config.toml').read_text(encoding='utf-8')
    assert config_text.count('[features]') == 1
    assert config_text.count('external_migration = true') == 1
    assert '[[tools]]' in config_text
    assert 'name = "not-a-feature"' in config_text


def test_materialize_codex_home_config_falls_back_to_marked_copy_when_symlink_fails(tmp_path: Path, monkeypatch) -> None:
    source_home = tmp_path / 'system-codex-home'
    target_home = tmp_path / 'managed-codex-home'
    (source_home / 'skills' / 'demo').mkdir(parents=True, exist_ok=True)
    (source_home / 'skills' / 'demo' / 'SKILL.md').write_text('demo skill\n', encoding='utf-8')

    def fail_symlink(self, target, target_is_directory=False):
        raise OSError('symlink disabled')

    monkeypatch.setattr(Path, 'symlink_to', fail_symlink)

    codex_home_config.materialize_codex_home_config(
        target_home,
        profile=ProviderProfileSpec(inherit_commands=False, inherit_memory=False),
        source_home=source_home,
    )

    assert not (target_home / 'skills').is_symlink()
    assert (target_home / 'skills' / 'demo' / 'SKILL.md').read_text(encoding='utf-8') == 'demo skill\n'
    assert not (target_home / 'skills' / 'demo').is_symlink()
    assert (target_home / 'skills' / 'demo.ccb-projection.json').is_file()
    assert not (target_home / 'skills.ccb-projection.json').exists()


def test_materialize_codex_home_config_filters_inherited_skills(tmp_path: Path) -> None:
    source_home = tmp_path / 'system-codex-home'
    target_home = tmp_path / 'managed-codex-home'
    for skill_name in ('ask', 'trellis-check', 'trellis-start'):
        (source_home / 'skills' / skill_name).mkdir(parents=True, exist_ok=True)
        (source_home / 'skills' / skill_name / 'SKILL.md').write_text(f'{skill_name}\n', encoding='utf-8')

    codex_home_config.materialize_codex_home_config(
        target_home,
        profile=ProviderProfileSpec(
            inherit_commands=False,
            inherit_memory=False,
            inherited_skill_exclude=('trellis-*',),
        ),
        source_home=source_home,
    )

    assert 'name: ask' in (target_home / 'skills' / 'ask' / 'SKILL.md').read_text(encoding='utf-8')
    assert (target_home / 'skills' / 'ask.ccb-projection.json').is_file()
    assert not (target_home / 'skills' / 'trellis-check').exists()
    assert not (target_home / 'skills' / 'trellis-start').exists()
    assert not (target_home / 'skills.ccb-projection.json').exists()


def test_materialize_codex_home_config_keeps_required_skills_when_optional_tree_has_broken_symlink(
    tmp_path: Path,
) -> None:
    source_home = tmp_path / 'system-codex-home'
    target_home = tmp_path / 'managed-codex-home'
    source_skills = source_home / 'skills'
    (source_skills / 'custom').mkdir(parents=True)
    (source_skills / 'custom' / 'SKILL.md').write_text('custom\n', encoding='utf-8')
    try:
        (source_skills / 'broken-role-skill').symlink_to(
            tmp_path / 'missing-role-skill',
            target_is_directory=True,
        )
    except OSError:
        pytest.skip('symlink creation is not available in this test environment')

    codex_home_config.materialize_codex_home_config(
        target_home,
        profile=ProviderProfileSpec(inherit_commands=False, inherit_memory=False),
        source_home=source_home,
    )

    assert not (target_home / 'skills.ccb-projection.json').exists()
    for skill_name in ('ask', 'ccb-clear', 'ccb-compact', 'ccb-diagnose', 'reconnect'):
        assert (target_home / 'skills' / skill_name / 'SKILL.md').is_file()
        assert (target_home / 'skills' / f'{skill_name}.ccb-projection.json').is_file()
    assert (source_skills / 'broken-role-skill').is_symlink()


def test_materialize_codex_home_config_keeps_required_skills_when_inheritance_is_disabled(
    tmp_path: Path,
) -> None:
    source_home = tmp_path / 'system-codex-home'
    target_home = tmp_path / 'managed-codex-home'
    (source_home / 'skills' / 'optional').mkdir(parents=True)
    (source_home / 'skills' / 'optional' / 'SKILL.md').write_text('optional\n', encoding='utf-8')

    codex_home_config.materialize_codex_home_config(
        target_home,
        profile=ProviderProfileSpec(
            inherit_skills=False,
            inherit_commands=False,
            inherit_memory=False,
        ),
        source_home=source_home,
    )

    assert not (target_home / 'skills' / 'optional').exists()
    for skill_name in ('ask', 'ccb-clear', 'ccb-compact', 'ccb-diagnose', 'reconnect'):
        assert (target_home / 'skills' / skill_name / 'SKILL.md').is_file()


def test_materialize_codex_home_config_projects_system_skill_collection_as_one_entry(
    tmp_path: Path,
) -> None:
    source_home = tmp_path / 'system-codex-home'
    target_home = tmp_path / 'managed-codex-home'
    system_skill = source_home / 'skills' / '.system' / 'skill-creator'
    system_skill.mkdir(parents=True)
    (system_skill / 'SKILL.md').write_text('system skill\n', encoding='utf-8')

    codex_home_config.materialize_codex_home_config(
        target_home,
        profile=ProviderProfileSpec(inherit_commands=False, inherit_memory=False),
        source_home=source_home,
    )

    target_system = target_home / 'skills' / '.system'
    assert target_system.is_symlink()
    assert (target_system / 'skill-creator' / 'SKILL.md').read_text(encoding='utf-8') == 'system skill\n'
    assert (target_home / 'skills' / '.system.ccb-projection.json').is_file()


def test_materialize_codex_home_config_restores_full_inherited_skills_after_filter(tmp_path: Path) -> None:
    source_home = tmp_path / 'system-codex-home'
    target_home = tmp_path / 'managed-codex-home'
    for skill_name in ('ask', 'trellis-check', 'trellis-start'):
        (source_home / 'skills' / skill_name).mkdir(parents=True, exist_ok=True)
        (source_home / 'skills' / skill_name / 'SKILL.md').write_text(f'{skill_name}\n', encoding='utf-8')

    codex_home_config.materialize_codex_home_config(
        target_home,
        profile=ProviderProfileSpec(
            inherit_commands=False,
            inherit_memory=False,
            inherited_skill_exclude=('trellis-*',),
        ),
        source_home=source_home,
    )
    codex_home_config.materialize_codex_home_config(
        target_home,
        profile=ProviderProfileSpec(inherit_commands=False, inherit_memory=False),
        source_home=source_home,
    )

    assert 'name: ask' in (target_home / 'skills' / 'ask' / 'SKILL.md').read_text(encoding='utf-8')
    assert (target_home / 'skills' / 'trellis-check' / 'SKILL.md').read_text(encoding='utf-8') == 'trellis-check\n'
    assert (target_home / 'skills' / 'trellis-start' / 'SKILL.md').read_text(encoding='utf-8') == 'trellis-start\n'
    assert (target_home / 'skills' / 'ask.ccb-projection.json').is_file()
    assert (target_home / 'skills' / 'trellis-check.ccb-projection.json').is_file()
    assert (target_home / 'skills' / 'trellis-start.ccb-projection.json').is_file()
    assert not (target_home / 'skills.ccb-projection.json').exists()


def test_materialize_codex_home_config_projects_skill_overlays(tmp_path: Path) -> None:
    source_home = tmp_path / 'system-codex-home'
    overlay_home = tmp_path / 'overlay-codex-home'
    target_home = tmp_path / 'managed-codex-home'
    (source_home / 'skills' / 'ask').mkdir(parents=True, exist_ok=True)
    (source_home / 'skills' / 'ask' / 'SKILL.md').write_text('ask\n', encoding='utf-8')
    for skill_name in ('trellis-check', 'trellis-start', 'unrelated'):
        (overlay_home / skill_name).mkdir(parents=True, exist_ok=True)
        (overlay_home / skill_name / 'SKILL.md').write_text(f'{skill_name}\n', encoding='utf-8')

    codex_home_config.materialize_codex_home_config(
        target_home,
        profile=ProviderProfileSpec(
            inherit_commands=False,
            inherit_memory=False,
            skill_overlays={
                'n14_trellis': SkillOverlaySpec(
                    source=str(overlay_home),
                    include=('trellis-*',),
                ),
            },
        ),
        source_home=source_home,
    )

    assert 'name: ask' in (target_home / 'skills' / 'ask' / 'SKILL.md').read_text(encoding='utf-8')
    assert (target_home / 'skills' / 'trellis-check' / 'SKILL.md').read_text(encoding='utf-8') == 'trellis-check\n'
    assert (target_home / 'skills' / 'trellis-start' / 'SKILL.md').read_text(encoding='utf-8') == 'trellis-start\n'
    assert (target_home / 'skills' / 'trellis-check.ccb-projection.json').is_file()
    assert (target_home / 'skills' / 'trellis-start.ccb-projection.json').is_file()
    assert not (target_home / 'skills' / 'unrelated').exists()


def test_materialize_codex_home_config_does_not_replace_user_asset_dir(tmp_path: Path) -> None:
    source_home = tmp_path / 'system-codex-home'
    target_home = tmp_path / 'managed-codex-home'
    (source_home / 'skills' / 'demo').mkdir(parents=True, exist_ok=True)
    (source_home / 'skills' / 'demo' / 'SKILL.md').write_text('source skill\n', encoding='utf-8')
    (target_home / 'skills').mkdir(parents=True, exist_ok=True)
    (target_home / 'skills' / 'custom.md').write_text('user skill\n', encoding='utf-8')

    codex_home_config.materialize_codex_home_config(
        target_home,
        profile=ProviderProfileSpec(inherit_commands=False, inherit_memory=False),
        source_home=source_home,
    )

    assert not (target_home / 'skills').is_symlink()
    assert (target_home / 'skills' / 'custom.md').read_text(encoding='utf-8') == 'user skill\n'
    assert (target_home / 'skills' / 'demo').is_symlink()
    assert (target_home / 'skills' / 'demo' / 'SKILL.md').read_text(encoding='utf-8') == 'source skill\n'
    assert (target_home / 'skills' / 'demo.ccb-projection.json').is_file()
    assert not (target_home / 'skills.ccb-projection.json').exists()


def test_materialize_codex_home_config_repairs_owned_skills_in_user_asset_dir(tmp_path: Path) -> None:
    source_home = tmp_path / 'system-codex-home'
    target_home = tmp_path / 'managed-codex-home'
    (source_home / 'skills' / 'ask').mkdir(parents=True, exist_ok=True)
    (source_home / 'skills' / 'ask' / 'SKILL.md').write_text('name: ask\n', encoding='utf-8')
    (source_home / 'skills' / 'ccb-clear').mkdir(parents=True, exist_ok=True)
    (source_home / 'skills' / 'ccb-clear' / 'SKILL.md').write_text('name: ccb-clear\n', encoding='utf-8')
    (source_home / 'skills' / 'reconnect').mkdir(parents=True, exist_ok=True)
    (source_home / 'skills' / 'reconnect' / 'SKILL.md').write_text('name: reconnect\n', encoding='utf-8')
    (target_home / 'skills').mkdir(parents=True, exist_ok=True)
    (target_home / 'skills' / 'custom.md').write_text('user skill\n', encoding='utf-8')
    (target_home / 'skills' / 'ccb_config').mkdir(parents=True, exist_ok=True)
    (target_home / 'skills' / 'ccb_config' / 'SKILL.md').write_text('name: ccb_config\n', encoding='utf-8')
    stale_target = target_home / 'skills' / 'ccb-config'
    stale_target.mkdir(parents=True, exist_ok=True)
    stale_source = tmp_path / 'stale-skill.md'
    stale_source.write_text('name: ccb-config-stale\n', encoding='utf-8')
    try:
        (stale_target / 'SKILL.md').symlink_to(stale_source)
    except OSError:
        pytest.skip('symlink creation is not available in this test environment')

    codex_home_config.materialize_codex_home_config(
        target_home,
        profile=ProviderProfileSpec(inherit_commands=False, inherit_memory=False),
        source_home=source_home,
    )

    assert (target_home / 'skills' / 'custom.md').read_text(encoding='utf-8') == 'user skill\n'
    assert 'name: ask' in (target_home / 'skills' / 'ask' / 'SKILL.md').read_text(encoding='utf-8')
    assert 'name: ccb-clear' in (target_home / 'skills' / 'ccb-clear' / 'SKILL.md').read_text(encoding='utf-8')
    assert 'name: reconnect' in (target_home / 'skills' / 'reconnect' / 'SKILL.md').read_text(encoding='utf-8')
    assert not (target_home / 'skills' / 'ccb_config').exists()
    assert not (target_home / 'skills' / 'ccb-config').exists()
    assert not (target_home / 'skills.ccb-projection.json').exists()


def test_materialize_codex_home_config_detaches_user_asset_symlink_without_mutating_source(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_home = tmp_path / 'system-codex-home'
    target_home = tmp_path / 'managed-codex-home'
    user_assets = tmp_path / 'user-skills'
    (source_home / 'skills').mkdir(parents=True, exist_ok=True)
    (source_home / 'skills' / 'demo.md').write_text('source skill\n', encoding='utf-8')
    user_assets.mkdir(parents=True, exist_ok=True)
    (user_assets / 'custom.md').write_text('user skill\n', encoding='utf-8')
    target_home.mkdir(parents=True, exist_ok=True)
    try:
        (target_home / 'skills').symlink_to(user_assets, target_is_directory=True)
    except OSError:
        pytest.skip('symlink creation is not available in this test environment')

    def fail_symlink(*args, **kwargs):
        raise OSError('symlink disabled')

    monkeypatch.setattr(Path, 'symlink_to', fail_symlink)

    codex_home_config.materialize_codex_home_config(
        target_home,
        profile=ProviderProfileSpec(inherit_commands=False, inherit_memory=False),
        source_home=source_home,
    )

    assert not (target_home / 'skills').is_symlink()
    assert (target_home / 'skills' / 'custom.md').read_text(encoding='utf-8') == 'user skill\n'
    assert (user_assets / 'custom.md').read_text(encoding='utf-8') == 'user skill\n'
    assert not (user_assets / 'ask').exists()
    assert (target_home / 'skills' / 'ask' / 'SKILL.md').is_file()
    assert (target_home / 'skills' / 'ccb-clear' / 'SKILL.md').is_file()
    assert (target_home / 'skills' / 'reconnect' / 'SKILL.md').is_file()
    assert not (target_home / 'skills.ccb-projection.json').exists()


def test_materialize_codex_home_config_migrates_matching_legacy_asset_copy(tmp_path: Path) -> None:
    source_home = tmp_path / 'system-codex-home'
    target_home = tmp_path / 'managed-codex-home'
    source_skill = source_home / 'skills' / 'demo'
    target_skill = target_home / 'skills' / 'demo'
    source_skill.mkdir(parents=True, exist_ok=True)
    target_skill.mkdir(parents=True, exist_ok=True)
    (source_skill / 'SKILL.md').write_text('source skill\n', encoding='utf-8')
    (target_skill / 'SKILL.md').write_text('source skill\n', encoding='utf-8')
    (target_home / 'skills.ccb-projection.json').write_text(
        json.dumps(
            {
                'schema_version': 1,
                'record_type': 'ccb_projected_asset',
                'label': 'codex-inherited-skills',
                'source': str(source_home / 'skills'),
                'mode': 'copy',
            }
        )
        + '\n',
        encoding='utf-8',
    )

    codex_home_config.materialize_codex_home_config(
        target_home,
        profile=ProviderProfileSpec(inherit_commands=False, inherit_memory=False),
        source_home=source_home,
    )

    assert not (target_home / 'skills').is_symlink()
    assert (target_home / 'skills' / 'demo').is_symlink()
    assert (target_home / 'skills' / 'demo' / 'SKILL.md').read_text(encoding='utf-8') == 'source skill\n'
    assert (target_home / 'skills' / 'demo.ccb-projection.json').is_file()
    assert not (target_home / 'skills.ccb-projection.json').exists()


def test_materialize_codex_home_config_rejects_source_home_as_writable_target(tmp_path: Path) -> None:
    source_home = tmp_path / 'system-codex-home'
    (source_home / 'skills').mkdir(parents=True, exist_ok=True)
    (source_home / 'skills' / 'demo.md').write_text('source skill\n', encoding='utf-8')

    with pytest.raises(ValueError, match='inheritance target must differ from source'):
        codex_home_config.materialize_codex_home_config(
            source_home,
            profile=ProviderProfileSpec(inherit_commands=False, inherit_memory=False),
            source_home=source_home,
        )

    assert not (source_home / 'skills').is_symlink()
    assert (source_home / 'skills' / 'demo.md').read_text(encoding='utf-8') == 'source skill\n'
    assert not (source_home / 'skills.ccb-projection.json').exists()


def test_materialize_codex_home_config_routes_plugins_through_shared_bundle(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    source_home = tmp_path / 'system-codex-home'
    _write_codex_plugin_source(source_home, sha='shared-plugin-sha')
    first_home = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-state' / 'codex' / 'home'
    second_home = project_root / '.ccb' / 'agents' / 'agent2' / 'provider-state' / 'codex' / 'home'

    codex_home_config.materialize_codex_home_config(
        first_home,
        profile=ProviderProfileSpec(inherit_skills=False, inherit_commands=False, inherit_memory=False),
        source_home=source_home,
        project_root=project_root,
    )
    codex_home_config.materialize_codex_home_config(
        second_home,
        profile=ProviderProfileSpec(inherit_skills=False, inherit_commands=False, inherit_memory=False),
        source_home=source_home,
        project_root=project_root,
    )

    bundle = project_root / '.ccb' / 'shared-cache' / 'codex' / 'plugin-bundles' / 'shared-plugin-sha'
    assert (bundle / '.agents' / 'plugins' / 'marketplace.json').is_file()
    assert (first_home / '.tmp' / 'plugins').is_symlink()
    assert (second_home / '.tmp' / 'plugins').is_symlink()
    assert (first_home / '.tmp' / 'plugins').resolve() == bundle.resolve()
    assert (second_home / '.tmp' / 'plugins').resolve() == bundle.resolve()
    assert (first_home / '.tmp' / 'plugins.sha').read_text(encoding='utf-8') == 'shared-plugin-sha\n'
    assert (second_home / '.tmp' / 'plugins.sha').read_text(encoding='utf-8') == 'shared-plugin-sha\n'


def test_materialize_codex_home_config_migrates_current_legacy_plugin_copy_to_shared_bundle(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    source_home = tmp_path / 'system-codex-home'
    target_home = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-state' / 'codex' / 'home'
    _write_codex_plugin_source(source_home, sha='shared-plugin-sha')
    shutil.copytree(source_home / '.tmp' / 'plugins', target_home / '.tmp' / 'plugins')
    (target_home / '.tmp' / 'plugins' / 'plugins-clone-residue').mkdir(parents=True, exist_ok=True)
    (target_home / '.tmp' / 'plugins.sha').write_text('shared-plugin-sha\n', encoding='utf-8')
    projected_assets.write_projected_marker(
        target_home / '.tmp' / 'plugins',
        label='codex-plugin-bundle',
        mode='copy',
        source=source_home / '.tmp' / 'plugins',
    )

    codex_home_config.materialize_codex_home_config(
        target_home,
        profile=ProviderProfileSpec(inherit_skills=False, inherit_commands=False, inherit_memory=False),
        source_home=source_home,
        project_root=project_root,
    )

    bundle = project_root / '.ccb' / 'shared-cache' / 'codex' / 'plugin-bundles' / 'shared-plugin-sha'
    assert (target_home / '.tmp' / 'plugins').is_symlink()
    assert (target_home / '.tmp' / 'plugins').resolve() == bundle.resolve()
    assert (target_home / '.tmp' / 'plugins.ccb-projection.json').is_file()
    assert (target_home / '.tmp' / 'plugins.sha').read_text(encoding='utf-8') == 'shared-plugin-sha\n'


def test_materialize_codex_profile_routes_plugins_through_shared_bundle(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo'
    source_home = tmp_path / 'system-codex-home'
    source_home.mkdir(parents=True, exist_ok=True)
    (source_home / 'config.toml').write_text('model = "gpt-5"\n', encoding='utf-8')
    _write_codex_plugin_source(source_home, sha='profile-plugin-sha')
    monkeypatch.setenv('CODEX_HOME', str(source_home))

    profile = materialize_provider_profile(
        layout=PathLayout(project_root),
        spec=_spec('agent1', provider_profile=ProviderProfileSpec(mode='isolated')),
        workspace_path=project_root,
    )

    runtime_home = Path(profile.runtime_home or '')
    bundle = project_root / '.ccb' / 'shared-cache' / 'codex' / 'plugin-bundles' / 'profile-plugin-sha'
    assert (bundle / '.agents' / 'plugins' / 'marketplace.json').is_file()
    assert (runtime_home / '.tmp' / 'plugins').is_symlink()
    assert (runtime_home / '.tmp' / 'plugins').resolve() == bundle.resolve()
    assert (runtime_home / '.tmp' / 'plugins.sha').read_text(encoding='utf-8') == 'profile-plugin-sha\n'


def test_materialize_codex_profile_preserves_explicit_runtime_home(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo'
    explicit_home = tmp_path / 'explicit-codex-home'
    source_home = tmp_path / 'system-codex-home'
    source_home.mkdir(parents=True, exist_ok=True)
    (source_home / 'config.toml').write_text('model = "gpt-5"\n', encoding='utf-8')
    monkeypatch.setenv('CODEX_HOME', str(source_home))

    profile = materialize_provider_profile(
        layout=PathLayout(project_root),
        spec=_spec(
            'agent1',
            provider_profile=ProviderProfileSpec(
                mode='isolated',
                home=str(explicit_home),
            ),
        ),
        workspace_path=project_root,
    )

    assert Path(profile.runtime_home or '') == explicit_home.resolve()
    assert Path(profile.profile_root or '') == explicit_home.resolve()
    assert (explicit_home / 'config.toml').is_file()
    assert (explicit_home / 'sessions').is_dir()


def test_materialize_codex_profile_migrates_legacy_profile_runtime_home(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo'
    layout = PathLayout(project_root)
    source_home = tmp_path / 'system-codex-home'
    source_home.mkdir(parents=True, exist_ok=True)
    (source_home / 'auth.json').write_text('{"OPENAI_API_KEY":"source-key"}\n', encoding='utf-8')
    _write_codex_plugin_source(source_home, plugin_name='source-plugin', sha='source-sha', skill_body='source skill\n')
    monkeypatch.setenv('CODEX_HOME', str(source_home))
    legacy_home = layout.provider_profiles_dir / 'agent1' / 'codex'
    legacy_session = legacy_home / 'sessions' / '2026' / '05' / '10' / 'legacy.jsonl'
    legacy_session.parent.mkdir(parents=True, exist_ok=True)
    legacy_session.write_text('{"type":"session"}\n', encoding='utf-8')
    (legacy_home / 'auth.json').write_text('{"OPENAI_API_KEY":"legacy-key"}\n', encoding='utf-8')
    _write_codex_plugin_source(legacy_home, plugin_name='legacy-plugin', sha='source-sha')
    session_file = layout.ccb_dir / session_filename_for_agent('codex', 'agent1')
    session_file.parent.mkdir(parents=True, exist_ok=True)
    session_file.write_text(
        json.dumps(
            {
                'codex_home': str(legacy_home),
                'codex_session_root': str(legacy_home / 'sessions'),
                'codex_session_path': str(legacy_session),
                'start_cmd': (
                    f'CODEX_HOME={legacy_home} '
                    f'CODEX_SESSION_ROOT={legacy_home / "sessions"} '
                    f'UNCHANGED={legacy_home}-suffix '
                    f'codex resume old'
                ),
                'codex_start_cmd': f'CODEX_HOME={legacy_home} CODEX_SESSION_ROOT={legacy_home / "sessions"} codex resume old',
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    profile = materialize_provider_profile(
        layout=layout,
        spec=_spec('agent1', provider_profile=ProviderProfileSpec(mode='isolated')),
        workspace_path=project_root,
    )

    runtime_home = Path(profile.runtime_home or '')
    migrated_session = runtime_home / 'sessions' / '2026' / '05' / '10' / 'legacy.jsonl'
    assert runtime_home == layout.agent_provider_state_dir('agent1', 'codex') / 'home'
    assert profile.profile_root is None
    assert migrated_session.read_text(encoding='utf-8') == '{"type":"session"}\n'
    assert (runtime_home / 'auth.json').read_text(encoding='utf-8') == '{"OPENAI_API_KEY":"source-key"}\n'
    assert (runtime_home / '.tmp' / 'plugins.sha').read_text(encoding='utf-8') == 'source-sha\n'
    assert (runtime_home / '.tmp' / 'plugins' / 'plugins' / 'source-plugin' / 'skills' / 'source-plugin' / 'SKILL.md').read_text(encoding='utf-8') == 'source skill\n'
    assert not (runtime_home / '.tmp' / 'plugins' / 'plugins' / 'legacy-plugin').exists()
    assert not (legacy_home / 'sessions').exists()
    payload = json.loads(session_file.read_text(encoding='utf-8'))
    assert payload['codex_home'] == str(runtime_home)
    assert payload['codex_session_root'] == str(runtime_home / 'sessions')
    assert payload['codex_session_path'] == str(migrated_session)
    assert f'CODEX_HOME={runtime_home}' in payload['start_cmd']
    assert f'CODEX_SESSION_ROOT={runtime_home / "sessions"}' in payload['start_cmd']
    assert str(legacy_home) not in payload['codex_start_cmd']
    assert f'UNCHANGED={legacy_home}-suffix' in payload['start_cmd']
    event = _latest_agent_event(layout, 'agent1', 'codex_profile_migration')
    assert event['status'] == 'migrated'
    assert event['reason'] == 'legacy_profile_runtime_home_migrated'


def test_materialize_codex_profile_migration_respects_inherit_auth_false(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo'
    layout = PathLayout(project_root)
    source_home = tmp_path / 'system-codex-home'
    source_home.mkdir(parents=True, exist_ok=True)
    (source_home / 'auth.json').write_text('{"OPENAI_API_KEY":"source-key"}\n', encoding='utf-8')
    monkeypatch.setenv('CODEX_HOME', str(source_home))
    legacy_home = layout.provider_profiles_dir / 'agent1' / 'codex'
    legacy_session = legacy_home / 'sessions' / '2026' / '05' / '10' / 'legacy.jsonl'
    legacy_session.parent.mkdir(parents=True, exist_ok=True)
    legacy_session.write_text('{"type":"session"}\n', encoding='utf-8')
    (legacy_home / 'auth.json').write_text('{"OPENAI_API_KEY":"legacy-key"}\n', encoding='utf-8')
    session_file = layout.ccb_dir / session_filename_for_agent('codex', 'agent1')
    session_file.parent.mkdir(parents=True, exist_ok=True)
    session_file.write_text(
        json.dumps(
            {
                'codex_home': str(legacy_home),
                'codex_session_root': str(legacy_home / 'sessions'),
                'codex_session_path': str(legacy_session),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    profile = materialize_provider_profile(
        layout=layout,
        spec=_spec(
            'agent1',
            provider_profile=ProviderProfileSpec(mode='isolated', inherit_auth=False),
        ),
        workspace_path=project_root,
    )

    runtime_home = Path(profile.runtime_home or '')
    assert (runtime_home / 'sessions' / '2026' / '05' / '10' / 'legacy.jsonl').is_file()
    assert (runtime_home / 'auth.json').read_text(encoding='utf-8') == '{"OPENAI_API_KEY":"legacy-key"}\n'
    assert not (legacy_home / 'sessions').exists()


def test_materialize_codex_profile_preserves_agent_local_auth_when_inherit_auth_false(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    layout = PathLayout(project_root)
    source_home = tmp_path / 'system-codex-home'
    source_home.mkdir(parents=True, exist_ok=True)
    (source_home / 'auth.json').write_text('{"auth_mode":"chatgpt","source":"global"}\n', encoding='utf-8')
    monkeypatch.setenv('CODEX_HOME', str(source_home))
    runtime_home = layout.agent_provider_state_dir('agent1', 'codex') / 'home'
    runtime_home.mkdir(parents=True, exist_ok=True)
    (runtime_home / 'auth.json').write_text('{"auth_mode":"chatgpt","source":"agent-local"}\n', encoding='utf-8')

    profile = materialize_provider_profile(
        layout=layout,
        spec=_spec(
            'agent1',
            provider_profile=ProviderProfileSpec(mode='isolated', inherit_auth=False),
        ),
        workspace_path=project_root,
    )

    runtime_home = Path(profile.runtime_home or '')
    assert (runtime_home / 'auth.json').read_text(encoding='utf-8') == (
        '{"auth_mode":"chatgpt","source":"agent-local"}\n'
    )


def test_materialize_codex_profile_does_not_migrate_when_session_authority_is_malformed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    layout = PathLayout(project_root)
    source_home = tmp_path / 'system-codex-home'
    source_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv('CODEX_HOME', str(source_home))
    legacy_home = layout.provider_profiles_dir / 'agent1' / 'codex'
    legacy_session = legacy_home / 'sessions' / '2026' / '05' / '10' / 'legacy.jsonl'
    legacy_session.parent.mkdir(parents=True, exist_ok=True)
    legacy_session.write_text('{"type":"session"}\n', encoding='utf-8')
    session_file = layout.ccb_dir / session_filename_for_agent('codex', 'agent1')
    session_file.parent.mkdir(parents=True, exist_ok=True)
    session_file.write_text('{not json}\n', encoding='utf-8')

    profile = materialize_provider_profile(
        layout=layout,
        spec=_spec('agent1', provider_profile=ProviderProfileSpec(mode='isolated')),
        workspace_path=project_root,
    )

    runtime_home = Path(profile.runtime_home or '')
    assert legacy_session.is_file()
    assert not (runtime_home / 'sessions' / '2026' / '05' / '10' / 'legacy.jsonl').exists()
    event = _latest_agent_event(layout, 'agent1', 'codex_profile_migration')
    assert event['status'] == 'skipped'
    assert event['reason'] == 'session_authority_preflight_failed'
    assert session_file.read_text(encoding='utf-8') == '{not json}\n'


def test_materialize_codex_profile_migrates_legacy_sessions_with_unrelated_tmp_symlink(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    layout = PathLayout(project_root)
    source_home = tmp_path / 'system-codex-home'
    source_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv('CODEX_HOME', str(source_home))
    legacy_home = layout.provider_profiles_dir / 'agent1' / 'codex'
    legacy_session = legacy_home / 'sessions' / '2026' / '05' / '10' / 'legacy.jsonl'
    legacy_session.parent.mkdir(parents=True, exist_ok=True)
    legacy_session.write_text('{"type":"session"}\n', encoding='utf-8')
    outside = tmp_path / 'outside'
    outside.mkdir(parents=True, exist_ok=True)
    tmp_dir = legacy_home / 'tmp' / 'arg0'
    tmp_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(outside, tmp_dir / 'linked-outside')
    except OSError:
        pytest.skip('symlink creation is not available in this test environment')
    session_file = layout.ccb_dir / session_filename_for_agent('codex', 'agent1')
    session_file.parent.mkdir(parents=True, exist_ok=True)
    session_file.write_text(
        json.dumps(
            {
                'codex_home': str(legacy_home),
                'codex_session_root': str(legacy_home / 'sessions'),
                'codex_session_path': str(legacy_session),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    profile = materialize_provider_profile(
        layout=layout,
        spec=_spec('agent1', provider_profile=ProviderProfileSpec(mode='isolated')),
        workspace_path=project_root,
    )

    runtime_home = Path(profile.runtime_home or '')
    assert not legacy_session.exists()
    assert (runtime_home / 'sessions' / '2026' / '05' / '10' / 'legacy.jsonl').is_file()
    assert (tmp_dir / 'linked-outside').is_symlink()
    event = _latest_agent_event(layout, 'agent1', 'codex_profile_migration')
    assert event['status'] == 'migrated'
    assert event['reason'] == 'legacy_profile_runtime_home_migrated'


def test_materialize_codex_profile_does_not_migrate_session_material_with_symlink(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    layout = PathLayout(project_root)
    source_home = tmp_path / 'system-codex-home'
    source_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv('CODEX_HOME', str(source_home))
    legacy_home = layout.provider_profiles_dir / 'agent1' / 'codex'
    legacy_session_root = legacy_home / 'sessions'
    legacy_session = legacy_session_root / '2026' / '05' / '10' / 'legacy.jsonl'
    legacy_session.parent.mkdir(parents=True, exist_ok=True)
    legacy_session.write_text('{"type":"session"}\n', encoding='utf-8')
    outside = tmp_path / 'outside'
    outside.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(outside, legacy_session_root / 'linked-outside')
    except OSError:
        pytest.skip('symlink creation is not available in this test environment')
    session_file = layout.ccb_dir / session_filename_for_agent('codex', 'agent1')
    session_file.parent.mkdir(parents=True, exist_ok=True)
    session_file.write_text(
        json.dumps(
            {
                'codex_home': str(legacy_home),
                'codex_session_root': str(legacy_session_root),
                'codex_session_path': str(legacy_session),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    profile = materialize_provider_profile(
        layout=layout,
        spec=_spec('agent1', provider_profile=ProviderProfileSpec(mode='isolated')),
        workspace_path=project_root,
    )

    runtime_home = Path(profile.runtime_home or '')
    assert legacy_session.is_file()
    assert not (runtime_home / 'sessions' / '2026' / '05' / '10' / 'legacy.jsonl').exists()
    event = _latest_agent_event(layout, 'agent1', 'codex_profile_migration')
    assert event['status'] == 'skipped'
    assert event['reason'] == 'legacy_home_contains_symlink'


def test_materialize_codex_profile_does_not_migrate_when_agent_runtime_is_active(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    layout = PathLayout(project_root)
    source_home = tmp_path / 'system-codex-home'
    source_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv('CODEX_HOME', str(source_home))
    legacy_home = layout.provider_profiles_dir / 'agent1' / 'codex'
    legacy_session = legacy_home / 'sessions' / '2026' / '05' / '10' / 'legacy.jsonl'
    legacy_session.parent.mkdir(parents=True, exist_ok=True)
    legacy_session.write_text('{"type":"session"}\n', encoding='utf-8')
    runtime_path = layout.agent_runtime_path('agent1')
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_path.write_text(
        json.dumps({'state': 'idle', 'pid': os.getpid()}, ensure_ascii=False) + '\n',
        encoding='utf-8',
    )
    session_file = layout.ccb_dir / session_filename_for_agent('codex', 'agent1')
    session_file.parent.mkdir(parents=True, exist_ok=True)
    session_file.write_text(
        json.dumps(
            {
                'codex_home': str(legacy_home),
                'codex_session_root': str(legacy_home / 'sessions'),
                'codex_session_path': str(legacy_session),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    profile = materialize_provider_profile(
        layout=layout,
        spec=_spec('agent1', provider_profile=ProviderProfileSpec(mode='isolated')),
        workspace_path=project_root,
    )

    runtime_home = Path(profile.runtime_home or '')
    assert legacy_session.is_file()
    assert not (runtime_home / 'sessions' / '2026' / '05' / '10' / 'legacy.jsonl').exists()
    event = _latest_agent_event(layout, 'agent1', 'codex_profile_migration')
    assert event['status'] == 'skipped'
    assert event['reason'] == 'agent_runtime_active'


def test_materialize_codex_profile_migrates_with_stale_idle_runtime_record(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    layout = PathLayout(project_root)
    source_home = tmp_path / 'system-codex-home'
    source_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv('CODEX_HOME', str(source_home))
    legacy_home = layout.provider_profiles_dir / 'agent1' / 'codex'
    legacy_session = legacy_home / 'sessions' / '2026' / '05' / '10' / 'legacy.jsonl'
    legacy_session.parent.mkdir(parents=True, exist_ok=True)
    legacy_session.write_text('{"type":"session"}\n', encoding='utf-8')
    runtime_path = layout.agent_runtime_path('agent1')
    runtime_path.parent.mkdir(parents=True, exist_ok=True)
    runtime_path.write_text('{"state":"idle","pid":0}\n', encoding='utf-8')
    session_file = layout.ccb_dir / session_filename_for_agent('codex', 'agent1')
    session_file.parent.mkdir(parents=True, exist_ok=True)
    session_file.write_text(
        json.dumps(
            {
                'codex_home': str(legacy_home),
                'codex_session_root': str(legacy_home / 'sessions'),
                'codex_session_path': str(legacy_session),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    profile = materialize_provider_profile(
        layout=layout,
        spec=_spec('agent1', provider_profile=ProviderProfileSpec(mode='isolated')),
        workspace_path=project_root,
    )

    runtime_home = Path(profile.runtime_home or '')
    assert (runtime_home / 'sessions' / '2026' / '05' / '10' / 'legacy.jsonl').is_file()
    assert not legacy_session.exists()


def test_materialize_codex_profile_writes_agent_local_provider_config_for_explicit_api(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    source_home = tmp_path / 'system-codex-home'
    source_home.mkdir(parents=True, exist_ok=True)
    (source_home / 'config.toml').write_text(
        '\n'.join(
            [
                'model_provider = "stale"',
                'model = "gpt-5.4-openai-compact"',
                'model_catalog_json = "model.json"',
                'model_reasoning_effort = "xhigh"',
                'disable_response_storage = true',
                '',
                '[projects."/tmp/demo-project"]',
                'trust_level = "trusted"',
                '',
                '[model_providers.stale]',
                'name = "stale"',
                'base_url = "https://stale.example.test/v1"',
                'wire_api = "responses"',
                'requires_openai_auth = true',
                '',
            ]
        ),
        encoding='utf-8',
    )
    (source_home / 'model.json').write_text('{"deepseek-v4-flash":{"context_window":128000}}\n', encoding='utf-8')
    monkeypatch.setenv('CODEX_HOME', str(source_home))
    _write_codex_plugin_source(
        source_home,
        plugin_name='weatherpromise',
        marketplace_name='codex-official',
        skill_body='plugin skill explicit\n',
    )

    profile = materialize_provider_profile(
        layout=PathLayout(project_root),
        spec=_spec(
            'agent2',
            provider_profile=ProviderProfileSpec(
                mode='isolated',
                env={
                    'OPENAI_API_KEY': 'profile-key',
                    'OPENAI_BASE_URL': 'https://api.rootflowai.com',
                },
                inherit_api=False,
                inherit_auth=False,
                inherit_config=False,
            ),
        ),
        workspace_path=project_root,
    )

    runtime_home = Path(profile.runtime_home or '')
    config_text = (runtime_home / 'config.toml').read_text(encoding='utf-8')
    assert 'model_provider = "custom"' in config_text
    assert 'model = "gpt-5.4-openai-compact"' in config_text
    assert 'model_catalog_json = "model.json"' in config_text
    assert 'model_reasoning_effort = "xhigh"' in config_text
    assert 'disable_response_storage = true' in config_text
    assert '[projects."/tmp/demo-project"]' in config_text
    assert '[model_providers.custom]' in config_text
    assert 'base_url = "https://api.rootflowai.com"' in config_text
    assert 'wire_api = "responses"' in config_text
    assert 'requires_openai_auth = false' in config_text
    assert 'external_migration = false' in config_text
    assert 'https://stale.example.test/v1' not in config_text
    assert 'env_key' not in config_text
    assert codex_provider_authority_fingerprint(profile)
    auth_payload = json.loads((runtime_home / 'auth.json').read_text(encoding='utf-8'))
    assert auth_payload == {'OPENAI_API_KEY': 'profile-key'}
    assert not (runtime_home / 'company-codex-api-key').exists()
    assert not (runtime_home / 'company-codex.config.toml').exists()
    auth_manifest = json.loads((runtime_home / '.ccb-auth-projection.json').read_text(encoding='utf-8'))
    assert auth_manifest['status'] == 'explicit_api_authority'
    assert auth_manifest['projected_sidecars'] == []
    assert (runtime_home / 'model.json').read_text(encoding='utf-8') == '{"deepseek-v4-flash":{"context_window":128000}}\n'
    assert (runtime_home / '.tmp' / 'plugins.sha').read_text(encoding='utf-8') == 'plugins-sha-v1\n'
    assert (runtime_home / '.tmp' / 'plugins' / '.agents' / 'plugins' / 'marketplace.json').is_file()
    assert (runtime_home / '.tmp' / 'plugins' / 'plugins' / 'weatherpromise' / 'skills' / 'weatherpromise' / 'SKILL.md').read_text(encoding='utf-8') == 'plugin skill explicit\n'


def test_materialize_codex_profile_projects_explicit_model_catalog_without_api_base(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / 'repo'
    source_home = tmp_path / 'system-codex-home'
    source_home.mkdir(parents=True, exist_ok=True)
    (source_home / 'config.toml').write_text('model = "deepseek-v4-flash"\n', encoding='utf-8')
    (source_home / 'model.json').write_text('{"deepseek-v4-flash":{"context_window":128000}}\n', encoding='utf-8')

    profile = ProviderProfileSpec(
        mode='isolated',
        env={'model_catalog_json': 'model.json'},
    )

    runtime_home = tmp_path / 'managed-home'
    materialize_codex_home_config(
        runtime_home,
        profile=profile,
        source_home=source_home,
        project_root=project_root,
    )

    config = tomllib.loads((runtime_home / 'config.toml').read_text(encoding='utf-8'))
    assert config['model_catalog_json'] == 'model.json'
    assert (runtime_home / 'model.json').read_text(encoding='utf-8') == (
        '{"deepseek-v4-flash":{"context_window":128000}}\n'
    )


def test_materialize_codex_profile_requires_model_catalog_file_in_managed_home(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / 'repo'
    source_home = tmp_path / 'system-codex-home'
    source_home.mkdir(parents=True, exist_ok=True)
    (source_home / 'config.toml').write_text('model = "deepseek-v4-flash"\n', encoding='utf-8')
    runtime_home = tmp_path / 'managed-home'
    runtime_home.mkdir(parents=True, exist_ok=True)

    with pytest.raises(RuntimeError, match='Codex model catalog file is missing'):
        materialize_codex_home_config(
            runtime_home,
            profile=ProviderProfileSpec(
                mode='isolated',
                env={'model_catalog_json': 'model.json'},
            ),
            source_home=source_home,
            project_root=project_root,
        )


def test_materialize_codex_profile_accepts_existing_managed_model_catalog_file(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / 'repo'
    source_home = tmp_path / 'system-codex-home'
    source_home.mkdir(parents=True, exist_ok=True)
    (source_home / 'config.toml').write_text('model = "deepseek-v4-flash"\n', encoding='utf-8')
    runtime_home = tmp_path / 'managed-home'
    runtime_home.mkdir(parents=True, exist_ok=True)
    (runtime_home / 'model.json').write_text('{"deepseek-v4-flash":{"source":"managed"}}\n', encoding='utf-8')

    materialize_codex_home_config(
        runtime_home,
        profile=ProviderProfileSpec(
            mode='isolated',
            env={'model_catalog_json': 'model.json'},
        ),
        source_home=source_home,
        project_root=project_root,
    )

    config = tomllib.loads((runtime_home / 'config.toml').read_text(encoding='utf-8'))
    assert config['model_catalog_json'] == 'model.json'
    assert (runtime_home / 'model.json').read_text(encoding='utf-8') == (
        '{"deepseek-v4-flash":{"source":"managed"}}\n'
    )


def test_materialize_codex_home_config_agent_model_overrides_inherited_global_model(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    source_home = tmp_path / 'system-codex-home'
    source_home.mkdir(parents=True, exist_ok=True)
    (source_home / 'config.toml').write_text('model = "gpt-global"\n', encoding='utf-8')

    # Without an explicit agent model, the inherited global model is preserved.
    plain_home = tmp_path / 'managed-plain'
    materialize_codex_home_config(
        plain_home,
        profile=ProviderProfileSpec(),
        source_home=source_home,
        project_root=project_root,
    )
    assert tomllib.loads((plain_home / 'config.toml').read_text(encoding='utf-8'))['model'] == 'gpt-global'

    # The agent's model field overrides the inherited global model.
    agent_home = tmp_path / 'managed-agent'
    materialize_codex_home_config(
        agent_home,
        profile=ProviderProfileSpec(),
        source_home=source_home,
        project_root=project_root,
        model='deepseek-v4-pro',
    )
    config = tomllib.loads((agent_home / 'config.toml').read_text(encoding='utf-8'))
    assert config['model'] == 'deepseek-v4-pro'


def test_materialize_codex_home_config_explicit_model_catalog_wins_over_stale_profile_env(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    source_home = tmp_path / 'system-codex-home'
    source_home.mkdir(parents=True, exist_ok=True)
    (source_home / 'config.toml').write_text('model = "deepseek-v4-flash"\n', encoding='utf-8')
    (source_home / 'models.json').write_text('{"deepseek-v4-pro":{"context_window":128000}}\n', encoding='utf-8')
    runtime_home = tmp_path / 'managed-home'

    # The resolved profile env is stale and does not carry model_catalog_json;
    # the explicit argument (plumbed from the live spec) must still project it.
    materialize_codex_home_config(
        runtime_home,
        profile=ProviderProfileSpec(
            mode='isolated',
            env={'OPENAI_BASE_URL': 'https://aspai.example/v1'},
        ),
        source_home=source_home,
        project_root=project_root,
        model_catalog_json='models.json',
    )

    config = tomllib.loads((runtime_home / 'config.toml').read_text(encoding='utf-8'))
    assert config['model_catalog_json'] == 'models.json'
    assert (runtime_home / 'models.json').read_text(encoding='utf-8') == (
        '{"deepseek-v4-pro":{"context_window":128000}}\n'
    )


def test_materialize_codex_profile_writes_agent_model_and_catalog_over_inherited_global(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    source_home = tmp_path / 'system-codex-home'
    source_home.mkdir(parents=True, exist_ok=True)
    (source_home / 'config.toml').write_text(
        'model = "gpt-global"\nmodel_reasoning_effort = "high"\n',
        encoding='utf-8',
    )
    (source_home / 'models.json').write_text('{"deepseek-v4-pro":{"context_window":128000}}\n', encoding='utf-8')
    monkeypatch.setenv('CODEX_HOME', str(source_home))

    profile = materialize_provider_profile(
        layout=PathLayout(project_root),
        spec=_spec(
            'agent1',
            provider_profile=ProviderProfileSpec(
                mode='isolated',
                env={
                    'OPENAI_BASE_URL': 'https://aspai.example/v1',
                    'model_catalog_json': 'models.json',
                },
            ),
            model='deepseek-v4-pro',
        ),
        workspace_path=project_root,
    )

    runtime_home = Path(profile.runtime_home or '')
    config = tomllib.loads((runtime_home / 'config.toml').read_text(encoding='utf-8'))
    assert config['model'] == 'deepseek-v4-pro'
    assert config['model_catalog_json'] == 'models.json'
    assert (runtime_home / 'models.json').read_text(encoding='utf-8') == (
        '{"deepseek-v4-pro":{"context_window":128000}}\n'
    )


def test_materialize_codex_profile_refreshes_plugin_projection_when_source_changes(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo'
    source_home = tmp_path / 'system-codex-home'
    source_home.mkdir(parents=True, exist_ok=True)
    (source_home / 'config.toml').write_text('model = "gpt-5"\n', encoding='utf-8')
    monkeypatch.setenv('CODEX_HOME', str(source_home))
    _write_codex_plugin_source(
        source_home,
        plugin_name='weatherpromise',
        sha='plugins-sha-v1',
        marketplace_name='market-v1',
        skill_body='plugin skill v1\n',
    )

    profile = materialize_provider_profile(
        layout=PathLayout(project_root),
        spec=_spec('agent1', provider_profile=ProviderProfileSpec(mode='isolated')),
        workspace_path=project_root,
    )

    runtime_home = Path(profile.runtime_home or '')
    marketplace_path = runtime_home / '.tmp' / 'plugins' / '.agents' / 'plugins' / 'marketplace.json'
    skill_path = runtime_home / '.tmp' / 'plugins' / 'plugins' / 'weatherpromise' / 'skills' / 'weatherpromise' / 'SKILL.md'
    assert skill_path.read_text(encoding='utf-8') == 'plugin skill v1\n'

    _write_codex_plugin_source(
        source_home,
        plugin_name='weatherpromise',
        sha='plugins-sha-v2',
        marketplace_name='market-v2',
        skill_body='plugin skill v2\n',
    )

    materialize_provider_profile(
        layout=PathLayout(project_root),
        spec=_spec('agent1', provider_profile=ProviderProfileSpec(mode='isolated')),
        workspace_path=project_root,
    )

    marketplace_payload = json.loads(marketplace_path.read_text(encoding='utf-8'))
    assert marketplace_payload['name'] == 'market-v2'
    assert skill_path.read_text(encoding='utf-8') == 'plugin skill v2\n'
    assert (runtime_home / '.tmp' / 'plugins.sha').read_text(encoding='utf-8') == 'plugins-sha-v2\n'

    plugin_source_root = source_home / '.tmp' / 'plugins'
    plugin_sha_path = source_home / '.tmp' / 'plugins.sha'
    shutil.rmtree(plugin_source_root)
    plugin_sha_path.unlink(missing_ok=True)

    materialize_provider_profile(
        layout=PathLayout(project_root),
        spec=_spec('agent1', provider_profile=ProviderProfileSpec(mode='isolated')),
        workspace_path=project_root,
    )

    assert not (runtime_home / '.tmp' / 'plugins').exists()
    assert not (runtime_home / '.tmp' / 'plugins.sha').exists()


def test_materialize_codex_home_seeds_writable_current_plugin_layout(tmp_path: Path) -> None:
    source_home = tmp_path / 'source'
    first_target_home = tmp_path / 'target-agent1'
    second_target_home = tmp_path / 'target-agent2'
    marketplace_root = source_home / '.tmp' / 'marketplaces'
    plugin_cache_root = source_home / 'plugins' / 'cache'
    (marketplace_root / 'demo' / '.agents' / 'plugins').mkdir(parents=True)
    (plugin_cache_root / 'demo' / 'demo' / '1.0.0' / '.codex-plugin').mkdir(parents=True)
    (marketplace_root / 'demo' / '.agents' / 'plugins' / 'marketplace.json').write_text('{}\n', encoding='utf-8')
    (plugin_cache_root / 'demo' / 'demo' / '1.0.0' / '.codex-plugin' / 'plugin.json').write_text(
        '{}\n',
        encoding='utf-8',
    )

    codex_home_config.materialize_codex_home_config(first_target_home, source_home=source_home)
    codex_home_config.materialize_codex_home_config(second_target_home, source_home=source_home)

    first_marketplaces = first_target_home / '.tmp' / 'marketplaces'
    first_cache = first_target_home / 'plugins' / 'cache'
    second_marketplaces = second_target_home / '.tmp' / 'marketplaces'
    second_cache = second_target_home / 'plugins' / 'cache'
    for target in (first_marketplaces, first_cache, second_marketplaces, second_cache):
        assert target.is_dir()
        assert not target.is_symlink()

    assert first_marketplaces.resolve() != marketplace_root.resolve()
    assert first_cache.resolve() != plugin_cache_root.resolve()
    assert first_marketplaces.resolve() != second_marketplaces.resolve()
    assert first_cache.resolve() != second_cache.resolve()
    assert (first_marketplaces / 'demo' / '.agents' / 'plugins' / 'marketplace.json').is_file()
    assert (first_cache / 'demo' / 'demo' / '1.0.0' / '.codex-plugin' / 'plugin.json').is_file()

    (first_cache / 'agent1-runtime-state.json').write_text('{}\n', encoding='utf-8')
    assert not (plugin_cache_root / 'agent1-runtime-state.json').exists()
    assert not (second_cache / 'agent1-runtime-state.json').exists()


def test_materialize_codex_home_migrates_marked_current_plugin_symlink_to_local_seed(tmp_path: Path) -> None:
    source_home = tmp_path / 'source'
    target_home = tmp_path / 'target'
    source_cache = source_home / 'plugins' / 'cache'
    (source_cache / 'demo').mkdir(parents=True)
    (source_cache / 'demo' / 'plugin.json').write_text('{}\n', encoding='utf-8')
    target_cache = target_home / 'plugins' / 'cache'
    target_cache.parent.mkdir(parents=True)
    target_cache.symlink_to(source_cache, target_is_directory=True)
    projected_assets.write_projected_marker(
        target_cache,
        label='codex-plugin-bundle',
        mode='symlink',
        source=source_cache,
    )

    codex_home_config.materialize_codex_home_config(target_home, source_home=source_home)

    assert target_cache.is_dir()
    assert not target_cache.is_symlink()
    assert (target_cache / 'demo' / 'plugin.json').is_file()
    (target_cache / 'local-runtime.json').write_text('{}\n', encoding='utf-8')
    assert not (source_cache / 'local-runtime.json').exists()
    marker = json.loads(Path(f'{target_cache}.ccb-projection.json').read_text(encoding='utf-8'))
    assert marker['mode'] == 'copy-seed'
    assert marker['source_fingerprint']


def test_materialize_codex_home_preserves_unmarked_current_plugin_targets(tmp_path: Path) -> None:
    source_home = tmp_path / 'source'
    target_home = tmp_path / 'target'
    source_cache = source_home / 'plugins' / 'cache'
    target_cache = target_home / 'plugins' / 'cache'
    (source_cache / 'source-plugin').mkdir(parents=True)
    (source_cache / 'source-plugin' / 'plugin.json').write_text('{"source":true}\n', encoding='utf-8')
    (target_cache / 'user-plugin').mkdir(parents=True)
    (target_cache / 'user-plugin' / 'plugin.json').write_text('{"user":true}\n', encoding='utf-8')

    codex_home_config.materialize_codex_home_config(target_home, source_home=source_home)

    assert (target_cache / 'user-plugin' / 'plugin.json').is_file()
    assert not (target_cache / 'source-plugin').exists()
    assert not Path(f'{target_cache}.ccb-projection.json').exists()


def test_materialize_codex_home_refreshes_only_marked_current_plugin_seed(tmp_path: Path) -> None:
    source_home = tmp_path / 'source'
    target_home = tmp_path / 'target'
    source_cache = source_home / 'plugins' / 'cache'
    source_file = source_cache / 'demo' / 'plugin.json'
    source_file.parent.mkdir(parents=True)
    source_file.write_text('{"version":1}\n', encoding='utf-8')

    codex_home_config.materialize_codex_home_config(target_home, source_home=source_home)
    target_cache = target_home / 'plugins' / 'cache'
    target_file = target_cache / 'demo' / 'plugin.json'
    (target_cache / 'provider-local.json').write_text('{}\n', encoding='utf-8')
    source_file.write_text('{"version":22}\n', encoding='utf-8')

    codex_home_config.materialize_codex_home_config(target_home, source_home=source_home)

    assert target_file.read_text(encoding='utf-8') == '{"version":22}\n'
    assert not (target_cache / 'provider-local.json').exists()

    shutil.rmtree(source_cache)
    codex_home_config.materialize_codex_home_config(target_home, source_home=source_home)

    assert target_file.read_text(encoding='utf-8') == '{"version":22}\n'


def test_materialize_codex_home_preserves_unmarked_legacy_plugin_bundle(tmp_path: Path) -> None:
    source_home = tmp_path / 'source'
    target_home = tmp_path / 'target'
    _write_codex_plugin_source(source_home, sha='source-sha')
    target_tree = target_home / '.tmp' / 'plugins'
    target_tree.mkdir(parents=True)
    (target_tree / 'user-owned.txt').write_text('keep\n', encoding='utf-8')
    target_sha = target_home / '.tmp' / 'plugins.sha'
    target_sha.write_text('user-sha\n', encoding='utf-8')

    codex_home_config.materialize_codex_home_config(target_home, source_home=source_home)

    assert (target_tree / 'user-owned.txt').read_text(encoding='utf-8') == 'keep\n'
    assert target_sha.read_text(encoding='utf-8') == 'user-sha\n'
    assert not Path(f'{target_tree}.ccb-projection.json').exists()


def test_seed_projected_tree_preserves_last_seed_when_source_disappears(tmp_path: Path) -> None:
    source = tmp_path / 'source'
    target = tmp_path / 'target'
    source.mkdir()
    (source / 'state.json').write_text('{"version":1}\n', encoding='utf-8')
    assert projected_assets.seed_projected_tree(source, target, label='test-seed')

    shutil.rmtree(source)

    assert not projected_assets.seed_projected_tree(source, target, label='test-seed')
    assert (target / 'state.json').read_text(encoding='utf-8') == '{"version":1}\n'


def test_seed_projected_tree_rolls_back_when_marker_update_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / 'source'
    target = tmp_path / 'target'
    source.mkdir()
    source_file = source / 'state.json'
    source_file.write_text('{"version":1}\n', encoding='utf-8')
    assert projected_assets.seed_projected_tree(source, target, label='test-seed')
    source_file.write_text('{"version":22}\n', encoding='utf-8')

    monkeypatch.setattr(projected_assets, '_write_projection_marker', lambda *args, **kwargs: False)

    assert not projected_assets.seed_projected_tree(source, target, label='test-seed')
    assert (target / 'state.json').read_text(encoding='utf-8') == '{"version":1}\n'


def test_project_json_mapping_fields_tracks_only_owned_entries(tmp_path: Path) -> None:
    source = tmp_path / 'source-settings.json'
    target = tmp_path / 'target-settings.json'
    marker = tmp_path / 'plugin-settings-marker.json'
    source.write_text(json.dumps({'enabledPlugins': {'source-plugin': True}}), encoding='utf-8')
    target.write_text(
        json.dumps({'theme': 'dark', 'enabledPlugins': {'local-plugin': True}}),
        encoding='utf-8',
    )

    assert projected_settings.project_json_mapping_fields(
        source,
        target,
        fields=('enabledPlugins',),
        label='test-plugin-settings',
        marker_path=marker,
    )
    payload = json.loads(target.read_text(encoding='utf-8'))
    assert payload['enabledPlugins'] == {'local-plugin': True, 'source-plugin': True}

    source.unlink()
    preserved_text = target.read_text(encoding='utf-8')
    assert not projected_settings.project_json_mapping_fields(
        source,
        target,
        fields=('enabledPlugins',),
        label='test-plugin-settings',
        marker_path=marker,
    )
    assert target.read_text(encoding='utf-8') == preserved_text
    assert marker.is_file()

    source.write_text('{malformed', encoding='utf-8')
    assert not projected_settings.project_json_mapping_fields(
        source,
        target,
        fields=('enabledPlugins',),
        label='test-plugin-settings',
        marker_path=marker,
    )
    assert target.read_text(encoding='utf-8') == preserved_text
    assert marker.is_file()

    source.write_text(
        json.dumps({'enabledPlugins': {'source-plugin': False, 'second-source-plugin': True}}),
        encoding='utf-8',
    )
    assert projected_settings.project_json_mapping_fields(
        source,
        target,
        fields=('enabledPlugins',),
        label='test-plugin-settings',
        marker_path=marker,
    )
    payload = json.loads(target.read_text(encoding='utf-8'))
    assert payload['enabledPlugins']['source-plugin'] is False
    payload['enabledPlugins']['source-plugin'] = True
    target.write_text(json.dumps(payload), encoding='utf-8')

    source.write_text(
        json.dumps({'enabledPlugins': {'source-plugin': True, 'second-source-plugin': False}}),
        encoding='utf-8',
    )
    assert projected_settings.project_json_mapping_fields(
        source,
        target,
        fields=('enabledPlugins',),
        label='test-plugin-settings',
        marker_path=marker,
    )
    payload = json.loads(target.read_text(encoding='utf-8'))
    assert payload['enabledPlugins'] == {
        'local-plugin': True,
        'source-plugin': True,
        'second-source-plugin': False,
    }

    assert not projected_settings.project_json_mapping_fields(
        source,
        target,
        fields=('enabledPlugins',),
        enabled=False,
        label='test-plugin-settings',
        marker_path=marker,
    )
    payload = json.loads(target.read_text(encoding='utf-8'))
    assert payload['theme'] == 'dark'
    assert payload['enabledPlugins'] == {'local-plugin': True, 'source-plugin': True}
    assert not marker.exists()


def test_project_json_mapping_fields_preserves_deleted_owned_null_entry(tmp_path: Path) -> None:
    source = tmp_path / 'source-settings.json'
    target = tmp_path / 'target-settings.json'
    marker = tmp_path / 'plugin-settings-marker.json'
    source.write_text(json.dumps({'enabledPlugins': {'source-plugin': None}}), encoding='utf-8')

    assert projected_settings.project_json_mapping_fields(
        source,
        target,
        fields=('enabledPlugins',),
        label='test-plugin-settings',
        marker_path=marker,
    )
    payload = json.loads(target.read_text(encoding='utf-8'))
    payload['enabledPlugins'].pop('source-plugin')
    target.write_text(json.dumps(payload), encoding='utf-8')
    source.write_text(json.dumps({'enabledPlugins': {'source-plugin': True}}), encoding='utf-8')

    assert not projected_settings.project_json_mapping_fields(
        source,
        target,
        fields=('enabledPlugins',),
        label='test-plugin-settings',
        marker_path=marker,
    )
    assert json.loads(target.read_text(encoding='utf-8')) == {}
    assert not marker.exists()


def test_project_json_mapping_fields_rolls_back_when_marker_write_fails(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / 'source-settings.json'
    target = tmp_path / 'target-settings.json'
    marker = tmp_path / 'plugin-settings-marker.json'
    source.write_text(json.dumps({'enabledPlugins': {'source-plugin': True}}), encoding='utf-8')
    original = '{"theme":"dark"}\n'
    target.write_text(original, encoding='utf-8')
    real_atomic_write = projected_settings.atomic_write_text

    def fail_marker(path: Path, text: str, *, encoding: str = 'utf-8') -> None:
        if Path(path) == marker:
            raise OSError('marker write failed')
        real_atomic_write(path, text, encoding=encoding)

    monkeypatch.setattr(projected_settings, 'atomic_write_text', fail_marker)

    assert not projected_settings.project_json_mapping_fields(
        source,
        target,
        fields=('enabledPlugins',),
        label='test-plugin-settings',
        marker_path=marker,
    )
    assert target.read_text(encoding='utf-8') == original
    assert not marker.exists()


def test_project_json_mapping_fields_restores_existing_marker_after_failed_update(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / 'source-settings.json'
    target = tmp_path / 'target-settings.json'
    marker = tmp_path / 'plugin-settings-marker.json'
    source.write_text(json.dumps({'enabledPlugins': {'source-plugin': True}}), encoding='utf-8')
    assert projected_settings.project_json_mapping_fields(
        source,
        target,
        fields=('enabledPlugins',),
        label='test-plugin-settings',
        marker_path=marker,
    )
    original_target = target.read_text(encoding='utf-8')
    original_marker = marker.read_text(encoding='utf-8')
    source.write_text(json.dumps({'enabledPlugins': {'source-plugin': False}}), encoding='utf-8')
    real_atomic_write = projected_settings.atomic_write_text

    def fail_after_marker_write(path: Path, text: str, *, encoding: str = 'utf-8') -> None:
        real_atomic_write(path, text, encoding=encoding)
        if Path(path) == marker:
            raise OSError('marker update failed after write')

    monkeypatch.setattr(projected_settings, 'atomic_write_text', fail_after_marker_write)

    assert not projected_settings.project_json_mapping_fields(
        source,
        target,
        fields=('enabledPlugins',),
        label='test-plugin-settings',
        marker_path=marker,
    )
    assert target.read_text(encoding='utf-8') == original_target
    assert marker.read_text(encoding='utf-8') == original_marker


def test_project_json_mapping_fields_preserves_foreign_marker_and_target(tmp_path: Path) -> None:
    source = tmp_path / 'source-settings.json'
    target = tmp_path / 'target-settings.json'
    marker = tmp_path / 'plugin-settings-marker.json'
    source.write_text(json.dumps({'enabledPlugins': {'source-plugin': True}}), encoding='utf-8')
    original = '{"enabledPlugins":{"local-plugin":true}}\n'
    target.write_text(original, encoding='utf-8')
    marker.write_text(
        json.dumps({'record_type': 'user-owned', 'label': 'test-plugin-settings'}),
        encoding='utf-8',
    )

    assert not projected_settings.project_json_mapping_fields(
        source,
        target,
        fields=('enabledPlugins',),
        label='test-plugin-settings',
        marker_path=marker,
    )
    assert target.read_text(encoding='utf-8') == original
    assert json.loads(marker.read_text(encoding='utf-8'))['record_type'] == 'user-owned'


def test_rebase_json_path_fields_rolls_back_all_files_after_write_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_root = tmp_path / 'source-plugins'
    target_root = tmp_path / 'target-plugins'
    first = target_root / 'installed_plugins.json'
    second = target_root / 'known_marketplaces.json'
    first.parent.mkdir(parents=True)
    original_first = json.dumps({'installPath': str(source_root / 'cache' / 'plugin')})
    original_second = json.dumps({'installLocation': str(source_root / 'marketplaces' / 'market')})
    first.write_text(original_first, encoding='utf-8')
    second.write_text(original_second, encoding='utf-8')
    real_atomic_write = projected_settings.atomic_write_text

    def fail_after_second_write(path: Path, text: str, *, encoding: str = 'utf-8') -> None:
        real_atomic_write(path, text, encoding=encoding)
        if Path(path) == second:
            raise OSError('second registry write failed')

    monkeypatch.setattr(projected_settings, 'atomic_write_text', fail_after_second_write)

    assert not projected_settings.rebase_json_path_fields(
        (first, second),
        source_root=source_root,
        target_root=target_root,
        fields=('installPath', 'installLocation'),
    )
    assert first.read_text(encoding='utf-8') == original_first
    assert second.read_text(encoding='utf-8') == original_second


def test_materialize_codex_profile_refreshes_plugin_projection_without_sha_marker(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo'
    source_home = tmp_path / 'system-codex-home'
    source_home.mkdir(parents=True, exist_ok=True)
    (source_home / 'config.toml').write_text('model = "gpt-5"\n', encoding='utf-8')
    monkeypatch.setenv('CODEX_HOME', str(source_home))
    _write_codex_plugin_source(
        source_home,
        plugin_name='weatherpromise',
        sha=None,
        marketplace_name='market-no-sha-v1',
        skill_body='plugin skill no sha v1\n',
    )

    profile = materialize_provider_profile(
        layout=PathLayout(project_root),
        spec=_spec('agent1', provider_profile=ProviderProfileSpec(mode='isolated')),
        workspace_path=project_root,
    )

    runtime_home = Path(profile.runtime_home or '')
    marketplace_path = runtime_home / '.tmp' / 'plugins' / '.agents' / 'plugins' / 'marketplace.json'
    skill_path = runtime_home / '.tmp' / 'plugins' / 'plugins' / 'weatherpromise' / 'skills' / 'weatherpromise' / 'SKILL.md'
    first_marker = (runtime_home / '.tmp' / 'plugins.sha').read_text(encoding='utf-8').strip()
    assert first_marker
    assert (project_root / '.ccb' / 'shared-cache' / 'codex' / 'plugin-bundles' / first_marker).is_dir()
    assert skill_path.read_text(encoding='utf-8') == 'plugin skill no sha v1\n'

    _write_codex_plugin_source(
        source_home,
        plugin_name='weatherpromise',
        sha=None,
        marketplace_name='market-no-sha-v2',
        skill_body='plugin skill no sha v2 updated\n',
    )

    materialize_provider_profile(
        layout=PathLayout(project_root),
        spec=_spec('agent1', provider_profile=ProviderProfileSpec(mode='isolated')),
        workspace_path=project_root,
    )

    marketplace_payload = json.loads(marketplace_path.read_text(encoding='utf-8'))
    assert marketplace_payload['name'] == 'market-no-sha-v2'
    assert skill_path.read_text(encoding='utf-8') == 'plugin skill no sha v2 updated\n'
    second_marker = (runtime_home / '.tmp' / 'plugins.sha').read_text(encoding='utf-8').strip()
    assert second_marker
    assert second_marker != first_marker
    assert (project_root / '.ccb' / 'shared-cache' / 'codex' / 'plugin-bundles' / second_marker).is_dir()


def test_materialize_codex_profile_skips_plugin_recopy_when_sha_is_unchanged(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo'
    source_home = tmp_path / 'system-codex-home'
    source_home.mkdir(parents=True, exist_ok=True)
    (source_home / 'config.toml').write_text('model = "gpt-5"\n', encoding='utf-8')
    monkeypatch.setenv('CODEX_HOME', str(source_home))
    _write_codex_plugin_source(
        source_home,
        plugin_name='weatherpromise',
        sha='stable-plugin-sha',
        marketplace_name='market-stable',
        skill_body='plugin skill stable\n',
    )

    copied_sources: list[Path] = []
    real_copytree = projected_assets.shutil.copytree

    def tracking_copytree(src, dst, *args, **kwargs):
        src_path = Path(src)
        if src_path == source_home / '.tmp' / 'plugins':
            copied_sources.append(src_path)
        return real_copytree(src, dst, *args, **kwargs)

    monkeypatch.setattr(projected_assets.shutil, 'copytree', tracking_copytree)

    materialize_provider_profile(
        layout=PathLayout(project_root),
        spec=_spec('agent1', provider_profile=ProviderProfileSpec(mode='isolated')),
        workspace_path=project_root,
    )
    materialize_provider_profile(
        layout=PathLayout(project_root),
        spec=_spec('agent1', provider_profile=ProviderProfileSpec(mode='isolated')),
        workspace_path=project_root,
    )

    assert copied_sources == [source_home / '.tmp' / 'plugins']


def test_materialize_codex_profile_repairs_incomplete_plugin_projection_even_when_sha_matches(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    source_home = tmp_path / 'system-codex-home'
    source_home.mkdir(parents=True, exist_ok=True)
    (source_home / 'config.toml').write_text('model = "gpt-5"\n', encoding='utf-8')
    monkeypatch.setenv('CODEX_HOME', str(source_home))
    _write_codex_plugin_source(
        source_home,
        plugin_name='weatherpromise',
        sha='repairable-plugin-sha',
        marketplace_name='market-repair',
        skill_body='plugin skill repair\n',
    )

    profile = materialize_provider_profile(
        layout=PathLayout(project_root),
        spec=_spec('agent1', provider_profile=ProviderProfileSpec(mode='isolated')),
        workspace_path=project_root,
    )

    runtime_home = Path(profile.runtime_home or '')
    marketplace_path = runtime_home / '.tmp' / 'plugins' / '.agents' / 'plugins' / 'marketplace.json'
    marketplace_path.unlink()
    assert not marketplace_path.exists()

    materialize_provider_profile(
        layout=PathLayout(project_root),
        spec=_spec('agent1', provider_profile=ProviderProfileSpec(mode='isolated')),
        workspace_path=project_root,
    )

    marketplace_payload = json.loads(marketplace_path.read_text(encoding='utf-8'))
    assert marketplace_payload['name'] == 'market-repair'


def test_materialize_claude_profile_keeps_runtime_home_managed_by_agent_state(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'

    profile = materialize_provider_profile(
        layout=PathLayout(project_root),
        spec=_spec(
            'agent1',
            provider='claude',
            provider_profile=ProviderProfileSpec(
                mode='isolated',
                inherit_api=False,
            ),
        ),
        workspace_path=project_root,
    )

    assert profile.runtime_home is None


def test_materialize_claude_home_config_projects_system_settings_into_managed_home(tmp_path: Path) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_settings = source_home / '.claude' / 'settings.json'
    source_settings.parent.mkdir(parents=True, exist_ok=True)
    source_settings.write_text(
        json.dumps(
            {
                'env': {
                    'ANTHROPIC_AUTH_TOKEN': 'system-token',
                    'ANTHROPIC_BASE_URL': 'https://claude.example.test',
                },
                'theme': 'light',
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    layout = materialize_claude_home_config(target_home, source_home=source_home)

    payload = json.loads(layout.settings_path.read_text(encoding='utf-8'))
    assert payload['env']['ANTHROPIC_AUTH_TOKEN'] == 'system-token'
    assert 'ANTHROPIC_API_KEY' not in payload['env']
    assert payload['env']['ANTHROPIC_BASE_URL'] == 'https://claude.example.test'
    assert payload['theme'] == 'light'
    trust = json.loads(layout.trust_path.read_text(encoding='utf-8'))
    assert 'customApiKeyResponses' not in trust


def test_materialize_claude_home_config_preserves_explicit_api_key_kind(tmp_path: Path) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_settings = source_home / '.claude' / 'settings.json'
    source_settings.parent.mkdir(parents=True, exist_ok=True)
    source_settings.write_text(
        json.dumps(
            {
                'env': {
                    'ANTHROPIC_API_KEY': 'system-api-key',
                    'ANTHROPIC_BASE_URL': 'https://claude.example.test',
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    layout = materialize_claude_home_config(target_home, source_home=source_home)

    payload = json.loads(layout.settings_path.read_text(encoding='utf-8'))
    assert payload['env']['ANTHROPIC_API_KEY'] == 'system-api-key'
    assert 'ANTHROPIC_AUTH_TOKEN' not in payload['env']
    trust = json.loads(layout.trust_path.read_text(encoding='utf-8'))
    assert trust['customApiKeyResponses']['approved'] == ['system-api-key']


def test_materialize_claude_home_config_drops_inherited_base_url_when_profile_sets_its_own(
    tmp_path: Path,
) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_settings = source_home / '.claude' / 'settings.json'
    source_settings.parent.mkdir(parents=True, exist_ok=True)
    source_settings.write_text(
        json.dumps(
            {
                'env': {
                    'ANTHROPIC_AUTH_TOKEN': 'system-token',
                    'ANTHROPIC_BASE_URL': 'https://system.example.test',
                },
                'theme': 'light',
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    profile = ProviderProfileSpec(
        env={
            'ANTHROPIC_AUTH_TOKEN': 'agent-token',
            'ANTHROPIC_BASE_URL': 'https://agent.example.test',
        },
    )

    layout = materialize_claude_home_config(target_home, source_home=source_home, profile=profile)

    payload = json.loads(layout.settings_path.read_text(encoding='utf-8'))
    assert 'ANTHROPIC_BASE_URL' not in payload.get('env', {})
    assert 'ANTHROPIC_AUTH_TOKEN' not in payload.get('env', {})
    assert payload.get('theme') == 'light'


def test_materialize_claude_home_config_drops_inherited_env_when_agent_sets_its_own(
    tmp_path: Path,
) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_settings = source_home / '.claude' / 'settings.json'
    source_settings.parent.mkdir(parents=True, exist_ok=True)
    source_settings.write_text(
        json.dumps(
            {
                'env': {
                    'ANTHROPIC_MODEL': 'deepseek-v4-pro[1M]',
                    'ANTHROPIC_DEFAULT_OPUS_MODEL': 'deepseek-v4-pro[1M]',
                    'MCP_TIMEOUT': '30000',
                },
                'theme': 'light',
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    extra_env = {
        'ANTHROPIC_MODEL': 'claude-opus-4-8[1M]',
        'ANTHROPIC_DEFAULT_OPUS_MODEL': 'claude-opus-4-8[1M]',
    }

    layout = materialize_claude_home_config(target_home, source_home=source_home, extra_env=extra_env)

    payload = json.loads(layout.settings_path.read_text(encoding='utf-8'))
    assert 'ANTHROPIC_MODEL' not in payload.get('env', {})
    assert 'ANTHROPIC_DEFAULT_OPUS_MODEL' not in payload.get('env', {})
    assert payload.get('env', {}).get('MCP_TIMEOUT') == '30000'
    assert payload.get('theme') == 'light'


def test_materialize_claude_home_config_projects_official_login_auth_into_managed_home(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(claude_home_runtime.platform, 'system', lambda: 'Linux')
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_credentials = source_home / '.claude' / '.credentials.json'
    source_legacy_auth = source_home / '.config' / 'claude-code' / 'auth.json'
    source_credentials.parent.mkdir(parents=True, exist_ok=True)
    source_legacy_auth.parent.mkdir(parents=True, exist_ok=True)
    source_credentials.write_text(
        json.dumps({'claudeAiOauth': {'refreshToken': 'system-refresh-token'}}, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    source_legacy_auth.write_text(
        json.dumps({'refresh_token': 'legacy-system-refresh-token'}, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )

    layout = materialize_claude_home_config(target_home, source_home=source_home)

    assert json.loads(layout.credentials_path.read_text(encoding='utf-8'))['claudeAiOauth']['refreshToken'] == 'system-refresh-token'
    assert json.loads(layout.auth_path.read_text(encoding='utf-8'))['refresh_token'] == 'legacy-system-refresh-token'


def test_materialize_claude_home_config_refreshes_login_metadata_without_replacing_trust(
    tmp_path: Path,
) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_trust = source_home / '.claude.json'
    target_trust = target_home / '.claude' / '.claude.json'
    source_trust.parent.mkdir(parents=True, exist_ok=True)
    target_trust.parent.mkdir(parents=True, exist_ok=True)
    source_trust.write_text(
        json.dumps(
            {
                'oauthAccount': {
                    'emailAddress': 'user@example.test',
                    'organizationUuid': 'org-source',
                },
                'hasCompletedOnboarding': True,
                'lastOnboardingVersion': '2.1.97',
                '/source/workspace': {'hasTrustDialogAccepted': True},
                'primaryApiKey': 'must-not-project',
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    target_trust.write_text(
        json.dumps(
            {
                'oauthAccount': {'emailAddress': 'stale@example.test'},
                'primaryApiKey': 'stale-key',
                '/managed/workspace': {'hasTrustDialogAccepted': True},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    layout = materialize_claude_home_config(target_home, source_home=source_home)

    payload = json.loads(layout.trust_path.read_text(encoding='utf-8'))
    assert payload['oauthAccount']['emailAddress'] == 'user@example.test'
    assert payload['oauthAccount']['organizationUuid'] == 'org-source'
    assert payload['hasCompletedOnboarding'] is True
    assert payload['lastOnboardingVersion'] == '2.1.97'
    assert payload['/managed/workspace']['hasTrustDialogAccepted'] is True
    assert '/source/workspace' not in payload
    assert 'primaryApiKey' not in payload


def test_materialize_claude_home_config_migrates_843_legacy_trust_state(
    tmp_path: Path,
) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_trust = source_home / '.claude.json'
    legacy_trust = target_home / '.claude.json'
    active_trust = target_home / '.claude' / '.claude.json'
    source_trust.parent.mkdir(parents=True, exist_ok=True)
    legacy_trust.parent.mkdir(parents=True, exist_ok=True)
    active_trust.parent.mkdir(parents=True, exist_ok=True)
    source_trust.write_text(
        json.dumps(
            {
                'oauthAccount': {'emailAddress': 'source@example.test'},
                'hasCompletedOnboarding': True,
            }
        ),
        encoding='utf-8',
    )
    legacy_trust.write_text(
        json.dumps(
            {
                'projects': {
                    '/legacy/workspace': {
                        'hasTrustDialogAccepted': True,
                        'allowedTools': ['Bash'],
                    },
                },
                'legacyOnly': True,
            }
        ),
        encoding='utf-8',
    )
    active_trust.write_text(
        json.dumps(
            {
                'oauthAccount': {'emailAddress': 'partial@example.test'},
                'projects': {
                    '/active/workspace': {'hasTrustDialogAccepted': True},
                },
                'activeOnly': True,
            }
        ),
        encoding='utf-8',
    )

    layout = materialize_claude_home_config(target_home, source_home=source_home)

    payload = json.loads(layout.trust_path.read_text(encoding='utf-8'))
    assert layout.trust_path == active_trust
    assert layout.legacy_trust_path == legacy_trust
    assert not legacy_trust.exists()
    assert payload['oauthAccount']['emailAddress'] == 'source@example.test'
    assert payload['hasCompletedOnboarding'] is True
    assert payload['legacyOnly'] is True
    assert payload['activeOnly'] is True
    assert payload['projects']['/legacy/workspace']['allowedTools'] == ['Bash']
    assert payload['projects']['/active/workspace']['hasTrustDialogAccepted'] is True


def test_materialize_claude_home_config_projects_mcp_config_into_managed_workspace(
    tmp_path: Path,
) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    project_root = tmp_path / 'repo'
    workspace = project_root / '.ccb' / 'workspaces' / 'clauder'
    source_trust = source_home / '.claude.json'
    target_trust = target_home / '.claude' / '.claude.json'
    source_trust.parent.mkdir(parents=True, exist_ok=True)
    target_trust.parent.mkdir(parents=True, exist_ok=True)
    project_root.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    source_project_key = str(project_root.resolve())
    target_project_key = str(workspace.resolve())
    source_trust.write_text(
        json.dumps(
            {
                'mcpServers': {
                    'global-tool': {
                        'command': 'global-mcp',
                        'args': ['serve'],
                        'env': {'GLOBAL_TOKEN': 'secret-value'},
                    },
                },
                'projects': {
                    source_project_key: {
                        'mcpServers': {
                            'project-tool': {
                                'command': 'project-mcp',
                                'args': ['--stdio'],
                            },
                        },
                        'enabledMcpjsonServers': ['project-tool'],
                        'disabledMcpjsonServers': ['disabled-json-tool'],
                        'disabledMcpServers': ['disabled-native-tool'],
                        'mcpContextUris': ['mcp://project-context'],
                        'allowedTools': ['must-not-project'],
                    },
                    '/unrelated/workspace': {
                        'mcpServers': {
                            'unrelated-tool': {'command': 'must-not-project'},
                        },
                    },
                },
                'primaryApiKey': 'must-not-project',
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    target_trust.write_text(
        json.dumps(
            {
                target_project_key: {
                    'hasTrustDialogAccepted': True,
                    'mcpServers': {'stale-tool': {'command': 'stale'}},
                },
                'projects': {
                    target_project_key: {
                        'hasTrustDialogAccepted': True,
                        'mcpServers': {'stale-tool': {'command': 'stale'}},
                    },
                },
                'primaryApiKey': 'stale-key',
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    layout = materialize_claude_home_config(
        target_home,
        source_home=source_home,
        project_root=project_root,
        workspace_path=workspace,
    )

    payload = json.loads(layout.trust_path.read_text(encoding='utf-8'))
    assert payload['mcpServers']['global-tool']['command'] == 'global-mcp'
    assert payload['mcpServers']['global-tool']['env'] == {'GLOBAL_TOKEN': 'secret-value'}
    assert payload['projects'][target_project_key]['hasTrustDialogAccepted'] is True
    assert payload['projects'][target_project_key]['mcpServers']['project-tool']['command'] == 'project-mcp'
    assert payload['projects'][target_project_key]['enabledMcpjsonServers'] == ['project-tool']
    assert payload['projects'][target_project_key]['disabledMcpjsonServers'] == ['disabled-json-tool']
    assert payload['projects'][target_project_key]['disabledMcpServers'] == ['disabled-native-tool']
    assert payload['projects'][target_project_key]['mcpContextUris'] == ['mcp://project-context']
    assert payload[target_project_key]['mcpServers']['project-tool']['command'] == 'project-mcp'
    assert 'allowedTools' not in payload['projects'][target_project_key]
    assert 'stale-tool' not in payload['projects'][target_project_key]['mcpServers']
    assert source_project_key not in payload['projects']
    assert '/unrelated/workspace' not in payload['projects']
    assert 'primaryApiKey' not in payload


def test_materialize_claude_home_config_strips_mcp_config_when_config_not_inherited(
    tmp_path: Path,
) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    workspace = tmp_path / 'repo' / '.ccb' / 'workspaces' / 'clauder'
    source_trust = source_home / '.claude.json'
    target_trust = target_home / '.claude' / '.claude.json'
    source_trust.parent.mkdir(parents=True, exist_ok=True)
    target_trust.parent.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)
    target_project_key = str(workspace.resolve())
    source_trust.write_text(
        json.dumps(
            {
                'mcpServers': {'global-tool': {'command': 'global-mcp'}},
                'projects': {
                    target_project_key: {
                        'mcpServers': {'project-tool': {'command': 'project-mcp'}},
                    },
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    target_trust.write_text(
        json.dumps(
            {
                'mcpServers': {'stale-global': {'command': 'stale'}},
                target_project_key: {
                    'hasTrustDialogAccepted': True,
                    'mcpServers': {'stale-project': {'command': 'stale'}},
                },
                'projects': {
                    target_project_key: {
                        'hasTrustDialogAccepted': True,
                        'mcpServers': {'stale-project': {'command': 'stale'}},
                    },
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    layout = materialize_claude_home_config(
        target_home,
        profile=ProviderProfileSpec(inherit_config=False),
        source_home=source_home,
        workspace_path=workspace,
    )

    payload = json.loads(layout.trust_path.read_text(encoding='utf-8'))
    assert 'mcpServers' not in payload
    assert payload[target_project_key]['hasTrustDialogAccepted'] is True
    assert 'mcpServers' not in payload[target_project_key]
    assert payload['projects'][target_project_key]['hasTrustDialogAccepted'] is True
    assert 'mcpServers' not in payload['projects'][target_project_key]


def test_materialize_claude_home_config_strips_login_metadata_when_auth_not_inherited(
    tmp_path: Path,
) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    target_trust = target_home / '.claude' / '.claude.json'
    target_trust.parent.mkdir(parents=True, exist_ok=True)
    target_trust.write_text(
        json.dumps(
            {
                'oauthAccount': {'emailAddress': 'stale@example.test'},
                'primaryApiKey': 'stale-key',
                '/managed/workspace': {'hasTrustDialogAccepted': True},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    (source_home / '.claude.json').parent.mkdir(parents=True, exist_ok=True)
    (source_home / '.claude.json').write_text('{"oauthAccount":{"emailAddress":"source@example.test"}}\n', encoding='utf-8')

    layout = materialize_claude_home_config(
        target_home,
        profile=ProviderProfileSpec(inherit_auth=False, inherit_api=False),
        source_home=source_home,
    )

    payload = json.loads(layout.trust_path.read_text(encoding='utf-8'))
    assert 'oauthAccount' not in payload
    assert 'primaryApiKey' not in payload
    assert payload['/managed/workspace']['hasTrustDialogAccepted'] is True


def test_materialize_claude_home_config_projects_macos_keychain_login_auth(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_home.mkdir(parents=True, exist_ok=True)
    calls: list[list[str]] = []

    class Result:
        def __init__(self, returncode: int, stdout: str = '') -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ''

    def fake_run(argv, **kwargs):
        calls.append([str(part) for part in argv])
        assert kwargs['capture_output'] is True
        assert kwargs['text'] is True
        command = str(argv[1])
        service = str(argv[argv.index('-s') + 1])
        if command == 'find-generic-password' and service == 'Claude Code-credentials':
            return Result(
                0,
                json.dumps({'claudeAiOauth': {'refreshToken': 'keychain-refresh-token'}}),
            )
        if command == 'add-generic-password':
            return Result(0)
        return Result(44)

    monkeypatch.setattr(claude_home_runtime.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(claude_home_runtime.shutil, 'which', lambda name: '/usr/bin/security')
    monkeypatch.setattr(claude_home_runtime.subprocess, 'run', fake_run)
    monkeypatch.setenv('USER', 'mac-user')

    layout = materialize_claude_home_config(target_home, source_home=source_home)

    payload = json.loads(layout.credentials_path.read_text(encoding='utf-8'))
    assert payload['claudeAiOauth']['refreshToken'] == 'keychain-refresh-token'
    assert calls[0] == [
        '/usr/bin/security',
        'find-generic-password',
        '-a',
        'mac-user',
        '-s',
        'Claude Code-credentials',
        '-w',
    ]
    managed_service = claude_home_runtime._managed_macos_keychain_service(layout)
    assert managed_service != 'Claude Code-credentials'
    assert any(
        call[1] == 'add-generic-password'
        and call[call.index('-s') + 1] == managed_service
        for call in calls
    )
    assert not any(
        call[1] in {'add-generic-password', 'delete-generic-password'}
        and call[call.index('-s') + 1] == 'Claude Code-credentials'
        for call in calls
    )


def test_materialize_claude_home_config_refreshes_existing_macos_keychain_after_source_relogin(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_home.mkdir(parents=True)
    source_refresh = 'source-refresh-1'
    managed_refresh: str | None = None
    calls: list[list[str]] = []

    class Result:
        def __init__(self, returncode: int, stdout: str = '') -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ''

    def credential_payload(refresh_token: str) -> dict[str, object]:
        return {'claudeAiOauth': {'refreshToken': refresh_token}}

    def fake_run(argv, **_kwargs):
        nonlocal managed_refresh
        call = [str(part) for part in argv]
        calls.append(call)
        command = call[1]
        service = call[call.index('-s') + 1]
        if command == 'find-generic-password' and service == 'Claude Code-credentials':
            return Result(0, json.dumps(credential_payload(source_refresh)))
        if command == 'find-generic-password':
            if managed_refresh is None:
                return Result(44)
            return Result(0, json.dumps(credential_payload(managed_refresh)))
        if command == 'add-generic-password':
            stored = json.loads(call[call.index('-w') + 1])
            managed_refresh = stored['claudeAiOauth']['refreshToken']
            return Result(0)
        return Result(44)

    monkeypatch.setattr(claude_home_runtime.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(claude_home_runtime.shutil, 'which', lambda name: '/usr/bin/security')
    monkeypatch.setattr(claude_home_runtime.subprocess, 'run', fake_run)
    monkeypatch.setenv('USER', 'mac-user')

    layout = materialize_claude_home_config(target_home, source_home=source_home)
    managed_service = claude_home_runtime._managed_macos_keychain_service(layout)
    assert managed_refresh == 'source-refresh-1'

    calls.clear()
    source_refresh = 'source-refresh-2'
    materialize_claude_home_config(target_home, source_home=source_home)

    payload = json.loads(layout.credentials_path.read_text(encoding='utf-8'))
    assert payload['claudeAiOauth']['refreshToken'] == 'source-refresh-2'
    assert managed_refresh == 'source-refresh-2'
    updates = [
        call
        for call in calls
        if call[1] == 'add-generic-password'
    ]
    assert len(updates) == 1
    assert '-U' in updates[0]
    assert updates[0][updates[0].index('-s') + 1] == managed_service
    assert not any(
        call[1] in {'add-generic-password', 'delete-generic-password'}
        and call[call.index('-s') + 1] == 'Claude Code-credentials'
        for call in calls
    )


def test_materialize_claude_home_config_preserves_private_macos_keychain_refresh_when_source_is_unchanged(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_home.mkdir(parents=True)
    source_refresh = 'source-refresh'
    managed_refresh: str | None = None
    calls: list[list[str]] = []

    class Result:
        def __init__(self, returncode: int, stdout: str = '') -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ''

    def credential_payload(refresh_token: str) -> dict[str, object]:
        return {'claudeAiOauth': {'refreshToken': refresh_token}}

    def fake_run(argv, **_kwargs):
        nonlocal managed_refresh
        call = [str(part) for part in argv]
        calls.append(call)
        command = call[1]
        service = call[call.index('-s') + 1]
        if command == 'find-generic-password' and service == 'Claude Code-credentials':
            return Result(0, json.dumps(credential_payload(source_refresh)))
        if command == 'find-generic-password':
            if managed_refresh is None:
                return Result(44)
            return Result(0, json.dumps(credential_payload(managed_refresh)))
        if command == 'add-generic-password':
            stored = json.loads(call[call.index('-w') + 1])
            managed_refresh = stored['claudeAiOauth']['refreshToken']
            return Result(0)
        return Result(44)

    monkeypatch.setattr(claude_home_runtime.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(claude_home_runtime.shutil, 'which', lambda name: '/usr/bin/security')
    monkeypatch.setattr(claude_home_runtime.subprocess, 'run', fake_run)
    monkeypatch.setenv('USER', 'mac-user')

    layout = materialize_claude_home_config(target_home, source_home=source_home)
    assert managed_refresh == source_refresh

    managed_refresh = 'managed-refresh'
    calls.clear()
    materialize_claude_home_config(target_home, source_home=source_home)

    payload = json.loads(layout.credentials_path.read_text(encoding='utf-8'))
    assert payload['claudeAiOauth']['refreshToken'] == source_refresh
    assert managed_refresh == 'managed-refresh'
    assert not any(call[1] == 'add-generic-password' for call in calls)


def test_materialize_claude_home_config_does_not_follow_owned_credentials_symlink_during_keychain_refresh(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    external_credentials = tmp_path / 'external-credentials.json'
    source_home.mkdir(parents=True)
    target_credentials = target_home / '.claude' / '.credentials.json'
    target_credentials.parent.mkdir(parents=True)
    external_credentials.write_text(
        '{"claudeAiOauth":{"refreshToken":"external-untouched"}}\n',
        encoding='utf-8',
    )
    target_credentials.symlink_to(external_credentials)
    (target_home / '.ccb-auth-projection.json').write_text(
        json.dumps(
            {
                'schema_version': 1,
                'record_type': 'ccb_claude_auth_projection',
                'status': 'inherited_auth',
                'source_home': str(source_home),
                'projected_files': ['.claude/.credentials.json'],
                'projected_env_keys': [],
            }
        ),
        encoding='utf-8',
    )
    calls: list[list[str]] = []

    class Result:
        def __init__(self, returncode: int, stdout: str = '') -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ''

    def fake_run(argv, **_kwargs):
        call = [str(part) for part in argv]
        calls.append(call)
        command = call[1]
        service = call[call.index('-s') + 1]
        if command == 'find-generic-password' and service == 'Claude Code-credentials':
            return Result(
                0,
                json.dumps({'claudeAiOauth': {'refreshToken': 'source-refresh'}}),
            )
        if command == 'find-generic-password':
            return Result(
                0,
                json.dumps({'claudeAiOauth': {'refreshToken': 'managed-stale'}}),
            )
        if command == 'add-generic-password':
            return Result(0)
        return Result(44)

    monkeypatch.setattr(claude_home_runtime.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(claude_home_runtime.shutil, 'which', lambda name: '/usr/bin/security')
    monkeypatch.setattr(claude_home_runtime.subprocess, 'run', fake_run)
    monkeypatch.setenv('USER', 'mac-user')

    layout = materialize_claude_home_config(target_home, source_home=source_home)

    assert layout.credentials_path.is_file()
    assert not layout.credentials_path.is_symlink()
    assert json.loads(layout.credentials_path.read_text(encoding='utf-8')) == {
        'claudeAiOauth': {'refreshToken': 'source-refresh'}
    }
    assert json.loads(external_credentials.read_text(encoding='utf-8')) == {
        'claudeAiOauth': {'refreshToken': 'external-untouched'}
    }
    assert any(call[1] == 'add-generic-password' and '-U' in call for call in calls)


def test_materialize_claude_home_config_observes_macos_keychain_logout(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_home.mkdir(parents=True)
    calls: list[list[str]] = []
    external_logged_in = True

    class Result:
        def __init__(self, returncode: int, stdout: str = '') -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ''

    def fake_run(argv, **_kwargs):
        nonlocal external_logged_in
        call = [str(part) for part in argv]
        calls.append(call)
        command = call[1]
        service = call[call.index('-s') + 1]
        if (
            command == 'find-generic-password'
            and service == 'Claude Code-credentials'
            and external_logged_in
        ):
            return Result(
                0,
                json.dumps({'claudeAiOauth': {'refreshToken': 'external-refresh'}}),
            )
        if command in {'add-generic-password', 'delete-generic-password'}:
            return Result(0)
        return Result(44)

    monkeypatch.setattr(claude_home_runtime.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(claude_home_runtime.shutil, 'which', lambda name: '/usr/bin/security')
    monkeypatch.setattr(claude_home_runtime.subprocess, 'run', fake_run)
    monkeypatch.setenv('USER', 'mac-user')

    layout = materialize_claude_home_config(target_home, source_home=source_home)
    managed_service = claude_home_runtime._managed_macos_keychain_service(layout)
    assert layout.credentials_path.is_file()

    external_logged_in = False
    materialize_claude_home_config(target_home, source_home=source_home)

    assert not layout.credentials_path.exists()
    manifest = json.loads(
        (target_home / '.ccb-auth-projection.json').read_text(encoding='utf-8')
    )
    assert manifest['status'] == 'source_auth_absent'
    assert manifest['projected_files'] == []
    assert any(
        call[1] == 'delete-generic-password'
        and call[call.index('-s') + 1] == managed_service
        for call in calls
    )
    assert not any(
        call[1] in {'add-generic-password', 'delete-generic-password'}
        and call[call.index('-s') + 1] in claude_home_runtime._macos_keychain_services()
        for call in calls
    )


def test_materialize_claude_home_config_keychain_error_preserves_projection(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_home.mkdir(parents=True)
    external_state = 'present'

    class Result:
        def __init__(self, returncode: int, stdout: str = '') -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ''

    def fake_run(argv, **_kwargs):
        command = str(argv[1])
        service = str(argv[argv.index('-s') + 1])
        if command == 'find-generic-password' and service == 'Claude Code-credentials':
            if external_state == 'present':
                return Result(
                    0,
                    json.dumps({'claudeAiOauth': {'refreshToken': 'external-refresh'}}),
                )
            return Result(36)
        if command == 'add-generic-password':
            return Result(0)
        return Result(44)

    monkeypatch.setattr(claude_home_runtime.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(claude_home_runtime.shutil, 'which', lambda name: '/usr/bin/security')
    monkeypatch.setattr(claude_home_runtime.subprocess, 'run', fake_run)
    monkeypatch.setenv('USER', 'mac-user')

    layout = materialize_claude_home_config(target_home, source_home=source_home)
    projected = layout.credentials_path.read_bytes()
    external_state = 'error'

    with pytest.raises(RuntimeError, match='cannot determine external Claude Keychain login state'):
        materialize_claude_home_config(target_home, source_home=source_home)

    assert layout.credentials_path.read_bytes() == projected


def test_materialize_claude_home_config_private_keychain_inspection_error_fails_closed(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_home.mkdir(parents=True)
    calls: list[list[str]] = []

    class Result:
        def __init__(self, returncode: int, stdout: str = '') -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ''

    def fake_run(argv, **_kwargs):
        call = [str(part) for part in argv]
        calls.append(call)
        command = call[1]
        service = call[call.index('-s') + 1]
        if command == 'find-generic-password' and service == 'Claude Code-credentials':
            return Result(
                0,
                json.dumps({'claudeAiOauth': {'refreshToken': 'source-refresh'}}),
            )
        if command == 'find-generic-password':
            return Result(36)
        return Result(0)

    monkeypatch.setattr(claude_home_runtime.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(claude_home_runtime.shutil, 'which', lambda name: '/usr/bin/security')
    monkeypatch.setattr(claude_home_runtime.subprocess, 'run', fake_run)
    monkeypatch.setenv('USER', 'mac-user')

    with pytest.raises(RuntimeError, match='cannot inspect agent-private Claude Keychain login'):
        materialize_claude_home_config(target_home, source_home=source_home)

    assert not any(call[1] == 'add-generic-password' for call in calls)


def test_materialize_claude_home_config_does_not_project_macos_keychain_preferences(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_plist = source_home / 'Library' / 'Preferences' / 'com.apple.security.plist'
    source_plist.parent.mkdir(parents=True, exist_ok=True)
    source_plist.write_text(
        '<plist><dict><key>DefaultKeychain</key><array/></dict></plist>\n',
        encoding='utf-8',
    )

    monkeypatch.setattr(claude_home_runtime.platform, 'system', lambda: 'Darwin')

    materialize_claude_home_config(target_home, source_home=source_home)

    target_plist = target_home / 'Library' / 'Preferences' / 'com.apple.security.plist'
    assert not target_plist.exists()
    assert source_plist.is_file()


def test_materialize_claude_home_config_never_links_macos_keychains_when_preferences_absent(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_keychains = source_home / 'Library' / 'Keychains'
    source_keychains.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(claude_home_runtime.platform, 'system', lambda: 'Darwin')

    materialize_claude_home_config(target_home, source_home=source_home)

    target_keychains = target_home / 'Library' / 'Keychains'
    assert not target_keychains.exists()
    assert not target_keychains.is_symlink()


def test_materialize_claude_home_config_detaches_legacy_macos_keychains_link(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_keychains = source_home / 'Library' / 'Keychains'
    target_keychains = target_home / 'Library' / 'Keychains'
    source_keychains.mkdir(parents=True, exist_ok=True)
    target_keychains.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(source_keychains, target_keychains)

    monkeypatch.setattr(claude_home_runtime.platform, 'system', lambda: 'Darwin')

    materialize_claude_home_config(target_home, source_home=source_home)

    assert not target_keychains.exists()
    assert not target_keychains.is_symlink()
    assert source_keychains.is_dir()


def test_materialize_claude_home_config_does_not_copy_keychain_preferences_on_non_darwin(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_plist = source_home / 'Library' / 'Preferences' / 'com.apple.security.plist'
    source_plist.parent.mkdir(parents=True, exist_ok=True)
    source_plist.write_text('<plist/>\n', encoding='utf-8')

    monkeypatch.setattr(claude_home_runtime.platform, 'system', lambda: 'Linux')

    materialize_claude_home_config(target_home, source_home=source_home)

    target_plist = target_home / 'Library' / 'Preferences' / 'com.apple.security.plist'
    assert not target_plist.exists()


def test_materialize_claude_home_config_removes_keychain_preferences_when_auth_not_inherited(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_plist = source_home / 'Library' / 'Preferences' / 'com.apple.security.plist'
    target_plist = target_home / 'Library' / 'Preferences' / 'com.apple.security.plist'
    source_plist.parent.mkdir(parents=True, exist_ok=True)
    target_plist.parent.mkdir(parents=True, exist_ok=True)
    source_plist.write_text('<plist><dict><key>DefaultKeychain</key><array/></dict></plist>\n', encoding='utf-8')
    target_plist.write_text('<plist><dict><key>OldKeychain</key><array/></dict></plist>\n', encoding='utf-8')

    monkeypatch.setattr(claude_home_runtime.platform, 'system', lambda: 'Darwin')

    materialize_claude_home_config(
        target_home,
        profile=ProviderProfileSpec(inherit_auth=False, inherit_api=False),
        source_home=source_home,
    )

    assert not target_plist.exists()


def test_materialize_claude_home_config_removes_macos_keychains_when_auth_not_inherited(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_keychains = source_home / 'Library' / 'Keychains'
    target_keychains = target_home / 'Library' / 'Keychains'
    source_keychains.mkdir(parents=True, exist_ok=True)
    target_keychains.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(source_keychains, target_keychains)

    monkeypatch.setattr(claude_home_runtime.platform, 'system', lambda: 'Darwin')

    materialize_claude_home_config(
        target_home,
        profile=ProviderProfileSpec(inherit_auth=False, inherit_api=False),
        source_home=source_home,
    )

    assert not target_keychains.exists()
    assert not target_keychains.is_symlink()


def test_materialize_claude_home_config_falls_back_to_legacy_macos_keychain_service(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_home.mkdir(parents=True, exist_ok=True)
    calls: list[list[str]] = []

    class Result:
        def __init__(self, returncode: int, stdout: str = '') -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ''

    def fake_run(argv, **kwargs):
        calls.append([str(part) for part in argv])
        service = calls[-1][calls[-1].index('-s') + 1]
        command = str(argv[1])
        if command == 'find-generic-password' and service == 'Claude Code':
            return Result(0, json.dumps({'claudeAiOauth': {'refreshToken': 'legacy-refresh-token'}}))
        if command == 'add-generic-password':
            return Result(0)
        return Result(44)

    monkeypatch.setattr(claude_home_runtime.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(claude_home_runtime.shutil, 'which', lambda name: '/usr/bin/security')
    monkeypatch.setattr(claude_home_runtime.subprocess, 'run', fake_run)
    monkeypatch.setenv('USER', 'mac-user')

    layout = materialize_claude_home_config(target_home, source_home=source_home)

    payload = json.loads(layout.credentials_path.read_text(encoding='utf-8'))
    assert payload['claudeAiOauth']['refreshToken'] == 'legacy-refresh-token'
    queried_services = [
        call[call.index('-s') + 1]
        for call in calls
        if call[1] == 'find-generic-password'
    ]
    assert queried_services[:3] == ['Claude Code-credentials', 'Claude Code-custom-oauth', 'Claude Code']
    assert queried_services[3] == claude_home_runtime._managed_macos_keychain_service(layout)
    assert all('-a' in call for call in calls)


def test_macos_keychain_services_keep_current_credentials_first_when_custom_oauth_enabled(monkeypatch) -> None:
    monkeypatch.setenv('CLAUDE_CODE_CUSTOM_OAUTH_URL', 'https://oauth.example.test')

    assert claude_home_runtime._macos_keychain_services() == (
        'Claude Code-credentials',
        'Claude Code-custom-oauth',
        'Claude Code',
    )


def test_macos_keychain_services_prioritize_explicit_override(monkeypatch) -> None:
    monkeypatch.setenv('CCB_KEYCHAIN_SERVICE_OVERRIDE', 'Claude Code-credentials-account-a')
    monkeypatch.setenv('CLAUDE_CODE_CUSTOM_OAUTH_URL', 'https://oauth.example.test')

    assert claude_home_runtime._macos_keychain_services() == (
        'Claude Code-credentials-account-a',
        'Claude Code-credentials',
        'Claude Code-custom-oauth',
        'Claude Code',
    )


def test_materialize_claude_home_config_reads_explicit_macos_keychain_override(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_home.mkdir(parents=True, exist_ok=True)
    calls: list[list[str]] = []

    class Result:
        def __init__(self, returncode: int, stdout: str = '') -> None:
            self.returncode = returncode
            self.stdout = stdout
            self.stderr = ''

    def fake_run(argv, **_kwargs):
        calls.append([str(part) for part in argv])
        service = calls[-1][calls[-1].index('-s') + 1]
        command = str(argv[1])
        if command == 'find-generic-password' and service == 'Claude Code-credentials-account-a':
            return Result(0, json.dumps({'claudeAiOauth': {'refreshToken': 'override-refresh-token'}}))
        if command == 'add-generic-password':
            return Result(0)
        return Result(44)

    monkeypatch.setattr(claude_home_runtime.platform, 'system', lambda: 'Darwin')
    monkeypatch.setattr(claude_home_runtime.shutil, 'which', lambda name: '/usr/bin/security')
    monkeypatch.setattr(claude_home_runtime.subprocess, 'run', fake_run)
    monkeypatch.setenv('USER', 'mac-user')
    monkeypatch.setenv('CCB_KEYCHAIN_SERVICE_OVERRIDE', 'Claude Code-credentials-account-a')

    layout = materialize_claude_home_config(target_home, source_home=source_home)

    payload = json.loads(layout.credentials_path.read_text(encoding='utf-8'))
    assert payload['claudeAiOauth']['refreshToken'] == 'override-refresh-token'
    assert calls[0][calls[0].index('-s') + 1] == 'Claude Code-credentials-account-a'
    assert any(
        call[1] == 'add-generic-password'
        and call[call.index('-s') + 1]
        == claude_home_runtime._managed_macos_keychain_service(layout)
        for call in calls
    )


def test_materialize_claude_home_config_preserves_runtime_hooks_and_permissions(tmp_path: Path) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_settings = source_home / '.claude' / 'settings.json'
    source_settings.parent.mkdir(parents=True, exist_ok=True)
    source_settings.write_text(
        json.dumps(
            {
                'env': {'ANTHROPIC_AUTH_TOKEN': 'system-token'},
                'theme': 'dark',
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    target_settings = target_home / '.claude' / 'settings.json'
    target_settings.parent.mkdir(parents=True, exist_ok=True)
    target_settings.write_text(
        json.dumps(
            {
                'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'echo hook'}]}]},
                'permissions': {'allow': ['Bash(ls)']},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    layout = materialize_claude_home_config(target_home, source_home=source_home)

    payload = json.loads(layout.settings_path.read_text(encoding='utf-8'))
    assert payload['env']['ANTHROPIC_AUTH_TOKEN'] == 'system-token'
    assert payload['theme'] == 'dark'
    assert payload['hooks']['Stop'][0]['hooks'][0]['command'] == 'echo hook'
    assert payload['permissions']['allow'] == ['Bash(ls)']


def test_materialize_claude_home_config_merges_source_and_managed_hooks(tmp_path: Path) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_settings = source_home / '.claude' / 'settings.json'
    source_settings.parent.mkdir(parents=True, exist_ok=True)
    source_settings.write_text(
        json.dumps(
            {
                'hooks': {
                    'Stop': [
                        {'hooks': [{'type': 'command', 'command': 'echo source-stop'}]},
                    ],
                    'UserPromptSubmit': [
                        {'hooks': [{'type': 'command', 'command': 'echo source-prompt'}]},
                    ],
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    target_settings = target_home / '.claude' / 'settings.json'
    target_settings.parent.mkdir(parents=True, exist_ok=True)
    target_settings.write_text(
        json.dumps(
            {
                'hooks': {
                    'Stop': [
                        {'hooks': [{'type': 'command', 'command': 'echo source-stop'}]},
                        {'hooks': [{'type': 'command', 'command': 'echo managed-stop'}]},
                    ],
                    'PostToolUse': [
                        {'hooks': [{'type': 'command', 'command': 'echo managed-post'}]},
                    ],
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    layout = materialize_claude_home_config(target_home, source_home=source_home)

    payload = json.loads(layout.settings_path.read_text(encoding='utf-8'))
    stop_commands = [
        hook['command']
        for group in payload['hooks']['Stop']
        for hook in group.get('hooks', [])
        if isinstance(hook, dict)
    ]
    assert stop_commands == ['echo source-stop', 'echo managed-stop']
    assert payload['hooks']['UserPromptSubmit'][0]['hooks'][0]['command'] == 'echo source-prompt'
    assert payload['hooks']['PostToolUse'][0]['hooks'][0]['command'] == 'echo managed-post'


def test_materialize_claude_home_config_refreshes_ccb_only_permissions_for_auto_permission(tmp_path: Path) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_settings = source_home / '.claude' / 'settings.json'
    source_settings.parent.mkdir(parents=True, exist_ok=True)
    source_settings.write_text(
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
    target_settings = target_home / '.claude' / 'settings.json'
    target_settings.parent.mkdir(parents=True, exist_ok=True)
    target_settings.write_text(
        json.dumps(
            {
                'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'echo hook'}]}]},
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

    layout = materialize_claude_home_config(target_home, source_home=source_home, auto_permission=True)

    payload = json.loads(layout.settings_path.read_text(encoding='utf-8'))
    assert payload['hooks']['Stop'][0]['hooks'][0]['command'] == 'echo hook'
    assert payload['permissions']['allow'] == ['Read', 'Write', 'Edit', 'Bash(git:*)', 'Bash(ccb ask *)']


def test_materialize_claude_home_config_preserves_custom_permissions_for_auto_permission(tmp_path: Path) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_settings = source_home / '.claude' / 'settings.json'
    source_settings.parent.mkdir(parents=True, exist_ok=True)
    source_settings.write_text(
        json.dumps({'permissions': {'allow': ['Read', 'Write'], 'deny': []}}, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    target_settings = target_home / '.claude' / 'settings.json'
    target_settings.parent.mkdir(parents=True, exist_ok=True)
    target_settings.write_text(
        json.dumps({'permissions': {'allow': ['Bash(ls)'], 'deny': []}}, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )

    layout = materialize_claude_home_config(target_home, source_home=source_home, auto_permission=True)

    payload = json.loads(layout.settings_path.read_text(encoding='utf-8'))
    assert payload['permissions']['allow'] == ['Bash(ls)']


def test_materialize_claude_home_config_refreshes_inherited_skill_assets(tmp_path: Path) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_claude_dir = source_home / '.claude'
    (source_claude_dir / 'skills' / 'review').mkdir(parents=True, exist_ok=True)
    (source_claude_dir / 'commands').mkdir(parents=True, exist_ok=True)
    (source_claude_dir / 'skills' / 'review' / 'SKILL.md').write_text('skill-v1\n', encoding='utf-8')
    (source_claude_dir / 'commands' / 'check.md').write_text('command-v1\n', encoding='utf-8')
    (source_claude_dir / 'CLAUDE.md').write_text('claude-md-v1\n', encoding='utf-8')

    layout = materialize_claude_home_config(target_home, source_home=source_home)

    assert (layout.claude_dir / 'skills' / 'review' / 'SKILL.md').read_text(encoding='utf-8') == 'skill-v1\n'
    assert (layout.claude_dir / 'commands' / 'check.md').read_text(encoding='utf-8') == 'command-v1\n'
    assert not (layout.claude_dir / 'CLAUDE.md').exists()

    (source_claude_dir / 'skills' / 'review' / 'SKILL.md').write_text('skill-v2\n', encoding='utf-8')
    (source_claude_dir / 'commands' / 'check.md').write_text('command-v2\n', encoding='utf-8')
    (source_claude_dir / 'CLAUDE.md').write_text('claude-md-v2\n', encoding='utf-8')

    materialize_claude_home_config(target_home, source_home=source_home)

    assert (layout.claude_dir / 'skills' / 'review' / 'SKILL.md').read_text(encoding='utf-8') == 'skill-v2\n'
    assert (layout.claude_dir / 'commands' / 'check.md').read_text(encoding='utf-8') == 'command-v2\n'
    assert not (layout.claude_dir / 'CLAUDE.md').exists()


def test_materialize_claude_home_config_writes_project_memory_bundle(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    source_home = tmp_path / 'system-home'
    target_home = project_root / '.ccb' / 'agents' / 'reviewer' / 'provider-state' / 'claude' / 'home'
    source_claude_dir = source_home / '.claude'
    source_claude_dir.mkdir(parents=True, exist_ok=True)
    (source_claude_dir / 'CLAUDE.md').write_text(
        'user claude memory\n'
        '<!-- CCB_CONFIG_START -->\n'
        'old installed claude ccb config\n'
        '<!-- CCB_CONFIG_END -->\n',
        encoding='utf-8',
    )
    project_root.mkdir(parents=True, exist_ok=True)
    _write_project_memory(project_root, 'shared ask memory\n')
    (project_root / 'CLAUDE.md').write_text('project claude memory\n', encoding='utf-8')
    private_memory = project_root / '.ccb' / 'agents' / 'reviewer' / 'memory.md'
    private_memory.parent.mkdir(parents=True, exist_ok=True)
    private_memory.write_text('reviewer private memory\n', encoding='utf-8')

    layout = materialize_claude_home_config(
        target_home,
        source_home=source_home,
        project_root=project_root,
        agent_name='reviewer',
        workspace_path=tmp_path / 'worktree',
    )

    text = (layout.claude_dir / 'CLAUDE.md').read_text(encoding='utf-8')
    assert '# CCB Managed Agent Memory' in text
    assert '## Provider User Memory' in text
    assert 'user claude memory' in text
    assert 'old installed claude ccb config' not in text
    assert '## CCB Shared Project Memory' in text
    assert 'shared ask memory' in text
    assert '## Provider-Native Project Memory' not in text
    assert 'project claude memory' not in text
    assert '## Agent Private Memory' in text
    assert 'reviewer private memory' in text


def test_materialize_claude_home_config_respects_inherit_memory_flag(tmp_path: Path) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_claude_dir = source_home / '.claude'
    source_claude_dir.mkdir(parents=True, exist_ok=True)
    (source_claude_dir / 'skills' / 'review').mkdir(parents=True, exist_ok=True)
    (source_claude_dir / 'skills' / 'review' / 'SKILL.md').write_text('skill\n', encoding='utf-8')
    (source_claude_dir / 'CLAUDE.md').write_text('claude-md\n', encoding='utf-8')

    layout = materialize_claude_home_config(
        target_home,
        profile=ProviderProfileSpec(inherit_skills=True, inherit_memory=False),
        source_home=source_home,
    )

    assert (layout.claude_dir / 'skills' / 'review' / 'SKILL.md').read_text(encoding='utf-8') == 'skill\n'
    assert not (layout.claude_dir / 'CLAUDE.md').exists()


def test_materialize_claude_home_config_projects_inherited_skills_and_commands(tmp_path: Path) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_claude_dir = source_home / '.claude'
    (source_claude_dir / 'skills' / 'ask').mkdir(parents=True, exist_ok=True)
    (source_claude_dir / 'commands').mkdir(parents=True, exist_ok=True)
    (source_claude_dir / 'skills' / 'ask' / 'SKILL.md').write_text('ask skill\n', encoding='utf-8')
    (source_claude_dir / 'commands' / 'ask.md').write_text('ask command\n', encoding='utf-8')

    layout = materialize_claude_home_config(
        target_home,
        profile=ProviderProfileSpec(inherit_memory=False),
        source_home=source_home,
    )

    assert 'name: ask' in (layout.claude_dir / 'skills' / 'ask' / 'SKILL.md').read_text(encoding='utf-8')
    assert (layout.claude_dir / 'skills' / 'ccb-clear' / 'SKILL.md').is_file()
    assert (layout.claude_dir / 'skills' / 'ccb-compact' / 'SKILL.md').is_file()
    assert (layout.claude_dir / 'commands' / 'ask.md').read_text(encoding='utf-8') == 'ask command\n'
    assert (layout.claude_dir / 'skills' / 'ask.ccb-projection.json').is_file()
    assert (layout.claude_dir / 'commands.ccb-projection.json').is_file()


def test_materialize_claude_home_config_preserves_unmarked_skills_and_commands(tmp_path: Path) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_claude_dir = source_home / '.claude'
    target_claude_dir = target_home / '.claude'
    (source_claude_dir / 'skills' / 'source-skill').mkdir(parents=True)
    (source_claude_dir / 'commands').mkdir(parents=True)
    (source_claude_dir / 'skills' / 'source-skill' / 'SKILL.md').write_text('source\n', encoding='utf-8')
    (source_claude_dir / 'commands' / 'source.md').write_text('source\n', encoding='utf-8')
    (target_claude_dir / 'skills' / 'user-skill').mkdir(parents=True)
    (target_claude_dir / 'commands').mkdir(parents=True)
    (target_claude_dir / 'skills' / 'user-skill' / 'SKILL.md').write_text('user\n', encoding='utf-8')
    (target_claude_dir / 'commands' / 'user.md').write_text('user\n', encoding='utf-8')

    layout = materialize_claude_home_config(
        target_home,
        profile=ProviderProfileSpec(inherit_memory=False),
        source_home=source_home,
    )
    materialize_claude_home_config(
        target_home,
        profile=ProviderProfileSpec(inherit_memory=False),
        source_home=source_home,
    )

    assert (layout.claude_dir / 'skills' / 'user-skill' / 'SKILL.md').read_text(encoding='utf-8') == 'user\n'
    assert (layout.claude_dir / 'commands' / 'user.md').read_text(encoding='utf-8') == 'user\n'
    assert (layout.claude_dir / 'skills' / 'source-skill').is_symlink()
    assert (layout.claude_dir / 'skills' / 'source-skill.ccb-projection.json').is_file()
    assert not (layout.claude_dir / 'commands' / 'source.md').exists()
    assert not (layout.claude_dir / 'skills.ccb-projection.json').exists()
    assert not (layout.claude_dir / 'commands.ccb-projection.json').exists()


def test_materialize_claude_home_config_merges_profile_mcp_server_overrides(tmp_path: Path) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_home.mkdir(parents=True, exist_ok=True)
    (source_home / '.claude.json').write_text(
        json.dumps(
            {
                'mcpServers': {
                    'agentmemory': {'command': 'agentmemory-old'},
                    'browser-use': {'command': 'browser-use'},
                    'playwright': {'command': 'playwright'},
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    layout = materialize_claude_home_config(
        target_home,
        profile=ProviderProfileSpec(
            inherit_memory=False,
            mcp_servers={
                'agentmemory': {'command': 'agentmemory-new', 'env': {'AGENTMEMORY_URL': 'http://localhost:3111'}},
                'browser-use': {'enabled': False},
                'codegraph': {'command': 'codegraph-mcp'},
                'playwright': {'enabled': False},
            },
        ),
        source_home=source_home,
    )

    payload = json.loads(layout.trust_path.read_text(encoding='utf-8'))
    servers = payload['mcpServers']
    assert sorted(servers) == ['agentmemory', 'codegraph']
    assert servers['agentmemory'] == {
        'command': 'agentmemory-new',
        'env': {'AGENTMEMORY_URL': 'http://localhost:3111'},
    }
    assert servers['codegraph'] == {'command': 'codegraph-mcp'}


def test_materialize_claude_home_config_writes_profile_mcp_without_source_trust(tmp_path: Path) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_home.mkdir(parents=True, exist_ok=True)

    layout = materialize_claude_home_config(
        target_home,
        profile=ProviderProfileSpec(
            inherit_memory=False,
            mcp_servers={'codegraph': {'command': 'codegraph-mcp', 'args': ['serve']}},
        ),
        source_home=source_home,
    )

    payload = json.loads(layout.trust_path.read_text(encoding='utf-8'))
    assert payload['mcpServers']['codegraph'] == {'command': 'codegraph-mcp', 'args': ['serve']}


def test_materialize_claude_home_config_removes_stale_profile_disabled_mcp_without_source_trust(
    tmp_path: Path,
) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_home.mkdir(parents=True, exist_ok=True)
    target_home.mkdir(parents=True, exist_ok=True)
    (target_home / '.claude' / '.claude.json').parent.mkdir(parents=True, exist_ok=True)
    (target_home / '.claude' / '.claude.json').write_text(
        json.dumps(
            {
                'mcpServers': {
                    'browser-use': {'command': 'browser-use'},
                    'keep': {'command': 'keep-mcp'},
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    layout = materialize_claude_home_config(
        target_home,
        profile=ProviderProfileSpec(
            inherit_memory=False,
            mcp_servers={
                'browser-use': {'enabled': False},
                'codegraph': {'command': 'codegraph-mcp'},
            },
        ),
        source_home=source_home,
    )

    payload = json.loads(layout.trust_path.read_text(encoding='utf-8'))
    assert sorted(payload['mcpServers']) == ['codegraph']
    assert payload['mcpServers']['codegraph'] == {'command': 'codegraph-mcp'}


def test_materialize_claude_home_config_skips_memory_without_project_context(tmp_path: Path) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_claude_dir = source_home / '.claude'
    source_claude_dir.mkdir(parents=True, exist_ok=True)
    (source_claude_dir / 'CLAUDE.md').write_text('source-only memory\n', encoding='utf-8')

    layout = materialize_claude_home_config(target_home, source_home=source_home)

    assert not (layout.claude_dir / 'CLAUDE.md').exists()


def test_materialize_droid_home_config_projects_inherited_skills(tmp_path: Path) -> None:
    source_home = tmp_path / 'system-factory-home'
    target_home = tmp_path / 'managed-factory-home'
    (source_home / 'skills' / 'ask').mkdir(parents=True, exist_ok=True)
    (source_home / 'skills' / 'ask' / 'SKILL.md').write_text('ask skill\n', encoding='utf-8')

    materialize_droid_home_config(target_home, source_home=source_home)

    assert (target_home / 'sessions').is_dir()
    assert 'name: ask' in (target_home / 'skills' / 'ask' / 'SKILL.md').read_text(encoding='utf-8')
    assert (target_home / 'skills' / 'ccb-clear' / 'SKILL.md').is_file()
    assert (target_home / 'skills' / 'ccb-compact' / 'SKILL.md').is_file()
    assert (target_home / 'skills' / 'ask.ccb-projection.json').is_file()


def test_materialize_droid_home_config_preserves_unmarked_inherited_skills(tmp_path: Path) -> None:
    source_home = tmp_path / 'system-factory-home'
    target_home = tmp_path / 'managed-factory-home'
    (source_home / 'skills' / 'source-skill').mkdir(parents=True)
    (source_home / 'skills' / 'source-skill' / 'SKILL.md').write_text('source\n', encoding='utf-8')
    (target_home / 'skills' / 'user-skill').mkdir(parents=True)
    (target_home / 'skills' / 'user-skill' / 'SKILL.md').write_text('user\n', encoding='utf-8')

    materialize_droid_home_config(target_home, source_home=source_home)
    materialize_droid_home_config(target_home, source_home=source_home)

    assert (target_home / 'skills' / 'user-skill' / 'SKILL.md').read_text(encoding='utf-8') == 'user\n'
    assert (target_home / 'skills' / 'source-skill').is_symlink()
    assert (target_home / 'skills' / 'source-skill.ccb-projection.json').is_file()
    assert not (target_home / 'skills.ccb-projection.json').exists()


def test_materialize_droid_home_config_copies_auth_without_linking_source(tmp_path: Path) -> None:
    source_home = tmp_path / 'source-factory'
    target_home = tmp_path / 'managed-factory'
    source_home.mkdir(parents=True)
    (source_home / 'auth.encrypted').write_text('source-auth\n', encoding='utf-8')
    (source_home / 'auth.v2.file').write_text('source-v2-auth\n', encoding='utf-8')
    (source_home / 'auth.v2.key').write_text('source-v2-key\n', encoding='utf-8')

    materialize_droid_home_config(target_home, source_home=source_home)

    target_auth = target_home / 'auth.encrypted'
    assert target_auth.read_text(encoding='utf-8') == 'source-auth\n'
    target_auth.write_text('managed-auth\n', encoding='utf-8')
    assert (source_home / 'auth.encrypted').read_text(encoding='utf-8') == 'source-auth\n'
    assert (target_home / 'auth.v2.file').read_text(encoding='utf-8') == 'source-v2-auth\n'
    assert (target_home / 'auth.v2.key').read_text(encoding='utf-8') == 'source-v2-key\n'


def test_materialize_droid_home_config_reads_current_factory_home_override(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source_os_home = tmp_path / 'source-os-home'
    source_factory_home = source_os_home / '.factory'
    target_factory_home = tmp_path / 'managed-os-home' / '.factory'
    source_factory_home.mkdir(parents=True)
    (source_factory_home / 'auth.encrypted').write_text(
        'source-auth\n',
        encoding='utf-8',
    )
    monkeypatch.delenv('CCB_SOURCE_HOME', raising=False)
    monkeypatch.delenv('FACTORY_HOME', raising=False)
    monkeypatch.delenv('FACTORY_ROOT', raising=False)
    monkeypatch.setenv('FACTORY_HOME_OVERRIDE', str(source_os_home))

    materialize_droid_home_config(target_factory_home)

    assert (target_factory_home / 'auth.encrypted').read_text(encoding='utf-8') == (
        'source-auth\n'
    )


def test_materialize_droid_home_config_converts_read_only_keyring_auth_to_private_files(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source_home = tmp_path / 'source-factory'
    target_home = tmp_path / 'managed-factory'
    source_ciphertext = source_home / 'auth.v2.keyring'
    source_ciphertext.parent.mkdir(parents=True)
    source_ciphertext.write_text('source-keyring-ciphertext\n', encoding='utf-8')
    key_text = base64.b64encode(b'k' * 32).decode('ascii')
    keyring_calls: list[tuple[str, str]] = []

    monkeypatch.delenv('CCB_SOURCE_HOME', raising=False)
    monkeypatch.setattr(droid_home_runtime, '_system_factory_home', lambda: source_home)

    def fake_read(service: str, account: str, **_kwargs) -> str:
        keyring_calls.append((service, account))
        return key_text

    monkeypatch.setattr(droid_home_runtime, 'read_keyring_password', fake_read)

    materialize_droid_home_config(target_home)

    assert keyring_calls == [('Factory CLI', 'auth-encryption-key')]
    assert (target_home / 'auth.v2.file').read_text(encoding='utf-8') == (
        'source-keyring-ciphertext\n'
    )
    assert (target_home / 'auth.v2.key').read_text(encoding='utf-8').strip() == key_text
    assert source_ciphertext.read_text(encoding='utf-8') == 'source-keyring-ciphertext\n'
    (target_home / 'auth.v2.file').unlink()
    assert source_ciphertext.is_file()


def test_materialize_droid_home_config_rejects_invalid_keyring_key(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source_home = tmp_path / 'source-factory'
    target_home = tmp_path / 'managed-factory'
    source_home.mkdir(parents=True)
    (source_home / 'auth.v2.keyring').write_text('ciphertext\n', encoding='utf-8')

    monkeypatch.delenv('CCB_SOURCE_HOME', raising=False)
    monkeypatch.setattr(droid_home_runtime, '_system_factory_home', lambda: source_home)
    monkeypatch.setattr(
        droid_home_runtime,
        'read_keyring_password',
        lambda *_args, **_kwargs: 'not-a-factory-key',
    )

    materialize_droid_home_config(target_home)

    assert not (target_home / 'auth.v2.file').exists()
    assert not (target_home / 'auth.v2.key').exists()


def test_materialize_droid_home_config_seeds_agent_local_plugins_and_rebases_registry_paths(
    tmp_path: Path,
) -> None:
    source_home = tmp_path / 'system-factory-home'
    first_home = tmp_path / 'managed-factory-home-1'
    second_home = tmp_path / 'managed-factory-home-2'
    _write_droid_plugin_source(source_home)
    first_settings = first_home / 'settings.json'
    first_settings.parent.mkdir(parents=True)
    first_settings.write_text(
        json.dumps({'theme': 'dark', 'enabledPlugins': {'local-plugin': True}}),
        encoding='utf-8',
    )

    materialize_droid_home_config(first_home, source_home=source_home)
    materialize_droid_home_config(second_home, source_home=source_home)

    for target_home in (first_home, second_home):
        target_plugins = target_home / 'plugins'
        assert target_plugins.is_dir()
        assert not target_plugins.is_symlink()
        installed = json.loads((target_plugins / 'installed_plugins.json').read_text(encoding='utf-8'))
        install_path = installed['plugins']['test-plugin@test-marketplace'][0]['installPath']
        assert install_path == str(
            target_plugins / 'cache' / 'test-marketplace' / 'test-plugin' / '1.0.0'
        )
        marketplaces = json.loads(
            (target_plugins / 'known_marketplaces.json').read_text(encoding='utf-8')
        )
        assert marketplaces['test-marketplace']['installLocation'] == str(
            target_plugins / 'marketplaces' / 'test-marketplace'
        )
        settings = json.loads((target_home / 'settings.json').read_text(encoding='utf-8'))
        assert settings['enabledPlugins']['test-plugin@test-marketplace'] is True

    first_registry = first_home / 'plugins' / 'installed_plugins.json'
    first_registry.write_text('{"agent":"one"}\n', encoding='utf-8')
    assert 'agent' not in json.loads(
        (second_home / 'plugins' / 'installed_plugins.json').read_text(encoding='utf-8')
    )
    source_installed = json.loads(
        (source_home / 'plugins' / 'installed_plugins.json').read_text(encoding='utf-8')
    )
    assert source_installed['plugins']['test-plugin@test-marketplace'][0]['installPath'].startswith(
        str(source_home)
    )

    materialize_droid_home_config(
        second_home,
        profile=ProviderProfileSpec(inherit_config=False),
        source_home=source_home,
    )
    assert not (second_home / 'plugins').exists()
    second_settings = json.loads((second_home / 'settings.json').read_text(encoding='utf-8'))
    assert 'test-plugin@test-marketplace' not in second_settings.get('enabledPlugins', {})

    materialize_droid_home_config(
        first_home,
        profile=ProviderProfileSpec(inherit_config=False),
        source_home=source_home,
    )
    disabled_settings = json.loads(first_settings.read_text(encoding='utf-8'))
    assert disabled_settings['theme'] == 'dark'
    assert disabled_settings['enabledPlugins'] == {'local-plugin': True}


def test_materialize_droid_home_config_removes_owned_projection_for_malformed_registry(
    tmp_path: Path,
) -> None:
    source_home = tmp_path / 'system-factory-home'
    target_home = tmp_path / 'managed-factory-home'
    _write_droid_plugin_source(source_home)
    materialize_droid_home_config(target_home, source_home=source_home)
    source_registry = source_home / 'plugins' / 'installed_plugins.json'
    source_registry.write_text('{malformed', encoding='utf-8')

    materialize_droid_home_config(target_home, source_home=source_home)

    assert not (target_home / 'plugins').exists()
    assert not (target_home / 'plugins.ccb-projection.json').exists()
    assert json.loads((target_home / 'settings.json').read_text(encoding='utf-8')) == {}
    assert source_registry.read_text(encoding='utf-8') == '{malformed'


def test_materialize_gemini_home_config_seeds_isolated_extensions(tmp_path: Path) -> None:
    source_home = tmp_path / 'system-home'
    source_extension = source_home / '.gemini' / 'extensions' / 'test-extension'
    source_extension.mkdir(parents=True)
    (source_extension / 'gemini-extension.json').write_text('{"name":"test-extension"}\n', encoding='utf-8')
    first_home = tmp_path / 'managed-home-1'
    second_home = tmp_path / 'managed-home-2'

    first = materialize_gemini_home_config(first_home, source_home=source_home)
    second = materialize_gemini_home_config(second_home, source_home=source_home)

    first_extensions = first.gemini_dir / 'extensions'
    second_extensions = second.gemini_dir / 'extensions'
    assert first_extensions.is_dir() and not first_extensions.is_symlink()
    assert second_extensions.is_dir() and not second_extensions.is_symlink()
    (first_extensions / 'test-extension' / 'agent-local.json').write_text('{}\n', encoding='utf-8')
    assert not (second_extensions / 'test-extension' / 'agent-local.json').exists()
    assert not (source_extension / 'agent-local.json').exists()

    materialize_gemini_home_config(
        second_home,
        profile=ProviderProfileSpec(inherit_config=False),
        source_home=source_home,
    )
    assert not second_extensions.exists()


def test_materialize_qwen_home_config_seeds_isolated_extensions(tmp_path: Path) -> None:
    source_home = tmp_path / 'system-qwen-home'
    source_extension = source_home / 'extensions' / 'test-extension'
    source_extension.mkdir(parents=True)
    (source_extension / 'qwen-extension.json').write_text('{"name":"test-extension"}\n', encoding='utf-8')
    first_home = tmp_path / 'managed-qwen-home-1'
    second_home = tmp_path / 'managed-qwen-home-2'

    materialize_qwen_home_config(first_home, source_home=source_home)
    materialize_qwen_home_config(second_home, source_home=source_home)

    first_extensions = first_home / 'extensions'
    second_extensions = second_home / 'extensions'
    assert first_extensions.is_dir() and not first_extensions.is_symlink()
    assert second_extensions.is_dir() and not second_extensions.is_symlink()
    (first_extensions / 'test-extension' / 'agent-local.json').write_text('{}\n', encoding='utf-8')
    assert not (second_extensions / 'test-extension' / 'agent-local.json').exists()
    assert not (source_extension / 'agent-local.json').exists()

    materialize_qwen_home_config(
        second_home,
        profile=ProviderProfileSpec(inherit_config=False),
        source_home=source_home,
    )
    assert not second_extensions.exists()


def test_materialize_qwen_home_config_copies_login_state_one_way(tmp_path: Path) -> None:
    source_home = tmp_path / 'source-qwen'
    target_home = tmp_path / 'managed-qwen'
    source_home.mkdir(parents=True)
    (source_home / 'oauth_creds.json').write_text(
        '{"refresh_token":"source"}\n',
        encoding='utf-8',
    )
    (source_home / 'settings.json').write_text('{"authType":"oauth"}\n', encoding='utf-8')

    materialize_qwen_home_config(target_home, source_home=source_home)

    target_auth = target_home / 'oauth_creds.json'
    target_auth.unlink()
    assert (source_home / 'oauth_creds.json').is_file()
    assert not target_home.is_symlink()


def test_provider_extension_projections_remove_owned_state_for_hard_role_policy(
    tmp_path: Path,
) -> None:
    policy = _hard_role_policy(tmp_path)

    droid_source = tmp_path / 'system-factory-home'
    droid_target = tmp_path / 'managed-factory-home'
    _write_droid_plugin_source(droid_source)
    materialize_droid_home_config(droid_target, source_home=droid_source)
    materialize_droid_home_config(
        droid_target,
        source_home=droid_source,
        command_policy=policy,
    )
    assert not (droid_target / 'plugins').exists()
    assert json.loads((droid_target / 'settings.json').read_text(encoding='utf-8')) == {}

    gemini_source = tmp_path / 'system-gemini-home'
    gemini_extension = gemini_source / '.gemini' / 'extensions' / 'test-extension'
    gemini_extension.mkdir(parents=True)
    (gemini_extension / 'gemini-extension.json').write_text('{}\n', encoding='utf-8')
    gemini_target = tmp_path / 'managed-gemini-home'
    materialize_gemini_home_config(gemini_target, source_home=gemini_source)
    gemini_layout = materialize_gemini_home_config(
        gemini_target,
        source_home=gemini_source,
        command_policy=policy,
    )
    assert not (gemini_layout.gemini_dir / 'extensions').exists()

    qwen_source = tmp_path / 'system-qwen-home'
    qwen_extension = qwen_source / 'extensions' / 'test-extension'
    qwen_extension.mkdir(parents=True)
    (qwen_extension / 'qwen-extension.json').write_text('{}\n', encoding='utf-8')
    qwen_target = tmp_path / 'managed-qwen-home'
    materialize_qwen_home_config(qwen_target, source_home=qwen_source)
    materialize_qwen_home_config(
        qwen_target,
        source_home=qwen_source,
        command_policy=policy,
    )
    assert not (qwen_target / 'extensions').exists()


def test_materialize_codex_home_config_writes_project_memory_bundle(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    source_home = tmp_path / 'system-codex-home'
    target_home = project_root / '.ccb' / 'agents' / 'agent1' / 'provider-state' / 'codex' / 'home'
    source_home.mkdir(parents=True, exist_ok=True)
    (source_home / 'AGENTS.md').write_text(
        'user codex memory\n'
        '<!-- CCB_ROLES_START -->\n'
        'old installed codex ccb roles\n'
        '<!-- CCB_ROLES_END -->\n',
        encoding='utf-8',
    )
    project_root.mkdir(parents=True, exist_ok=True)
    _write_project_memory(project_root, 'shared ask memory\n')
    (project_root / 'AGENTS.md').write_text('project codex memory\n', encoding='utf-8')
    private_memory = project_root / '.ccb' / 'agents' / 'agent1' / 'memory.md'
    private_memory.parent.mkdir(parents=True, exist_ok=True)
    private_memory.write_text('agent1 private memory\n', encoding='utf-8')

    codex_home_config.materialize_codex_home_config(
        target_home,
        source_home=source_home,
        project_root=project_root,
        agent_name='agent1',
        workspace_path=tmp_path / 'worktree',
    )

    text = (target_home / 'AGENTS.md').read_text(encoding='utf-8')
    assert '# CCB Managed Agent Memory' in text
    assert 'provider: codex' in text
    assert '## CCB Runtime Coordination Rules' in text
    assert 'CCB `ask` is submit-only' in text
    assert 'Do not wait, poll, or run `pend`/`watch`/`ping`' in text
    assert text.index('## CCB Runtime Coordination Rules') < text.index('## CCB Shared Project Memory')
    assert '## Provider User Memory' in text
    assert 'user codex memory' in text
    assert 'old installed codex ccb roles' not in text
    assert '## CCB Shared Project Memory' in text
    assert 'shared ask memory' in text
    assert '## Provider-Native Project Memory' not in text
    assert 'project codex memory' not in text
    assert '## Agent Private Memory' in text
    assert 'agent1 private memory' in text


def test_materialize_codex_provider_profile_writes_project_memory_bundle(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo'
    source_home = tmp_path / 'system-codex-home'
    source_home.mkdir(parents=True, exist_ok=True)
    (source_home / 'AGENTS.md').write_text('user codex memory\n', encoding='utf-8')
    monkeypatch.setenv('CODEX_HOME', str(source_home))
    _write_project_memory(project_root, 'shared profile memory\n')

    layout = PathLayout(project_root)
    profile = materialize_provider_profile(
        layout=layout,
        spec=_spec('agent1'),
        workspace_path=project_root,
    )

    target_home = Path(str(profile.runtime_home))
    text = (target_home / 'AGENTS.md').read_text(encoding='utf-8')
    assert 'agent: agent1' in text
    assert 'user codex memory' in text
    assert 'shared profile memory' in text
    marker = json.loads(
        (layout.agent_provider_runtime_dir('agent1', 'codex') / 'codex-memory-projection.json').read_text(
            encoding='utf-8'
        )
    )
    assert marker['status'] == 'ok'
    assert marker['reason'] == 'written'
    assert marker['sha256']


def test_materialize_codex_home_config_respects_inherit_memory_flag(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    source_home = tmp_path / 'system-codex-home'
    target_home = tmp_path / 'managed-codex-home'
    target_home.mkdir(parents=True, exist_ok=True)
    (target_home / 'AGENTS.md').write_text('stale managed memory\n', encoding='utf-8')

    codex_home_config.materialize_codex_home_config(
        target_home,
        profile=ProviderProfileSpec(inherit_memory=False),
        source_home=source_home,
        project_root=project_root,
        agent_name='agent1',
    )

    assert not (target_home / 'AGENTS.md').exists()


def test_materialize_codex_home_config_skips_memory_without_project_context(tmp_path: Path) -> None:
    source_home = tmp_path / 'system-codex-home'
    target_home = tmp_path / 'managed-codex-home'
    source_home.mkdir(parents=True, exist_ok=True)
    target_home.mkdir(parents=True, exist_ok=True)
    (source_home / 'AGENTS.md').write_text('source-only memory\n', encoding='utf-8')
    (target_home / 'AGENTS.md').write_text('existing managed memory\n', encoding='utf-8')

    codex_home_config.materialize_codex_home_config(target_home, source_home=source_home)

    assert (target_home / 'AGENTS.md').read_text(encoding='utf-8') == 'existing managed memory\n'


def test_materialize_claude_home_config_projects_referenced_home_hook_assets(tmp_path: Path) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_settings = source_home / '.claude' / 'settings.json'
    source_hook = source_home / '.codeisland' / 'codeisland-hook.sh'
    source_settings.parent.mkdir(parents=True, exist_ok=True)
    source_hook.parent.mkdir(parents=True, exist_ok=True)
    source_settings.write_text(
        json.dumps(
            {
                'hooks': {
                    'Stop': [
                        {
                            'hooks': [
                                {
                                    'type': 'command',
                                    'command': '$HOME/.codeisland/codeisland-hook.sh',
                                }
                            ]
                        }
                    ]
                }
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    source_hook.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')

    layout = materialize_claude_home_config(target_home, source_home=source_home)

    assert (layout.home_root / '.codeisland' / 'codeisland-hook.sh').read_text(encoding='utf-8') == '#!/bin/sh\nexit 0\n'


def test_materialize_claude_home_config_does_not_project_home_hook_assets_without_config_inheritance(
    tmp_path: Path,
) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_settings = source_home / '.claude' / 'settings.json'
    source_hook = source_home / '.codeisland' / 'codeisland-hook.sh'
    source_settings.parent.mkdir(parents=True, exist_ok=True)
    source_hook.parent.mkdir(parents=True, exist_ok=True)
    source_settings.write_text(
        json.dumps(
            {'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': '${HOME}/.codeisland/codeisland-hook.sh'}]}]}},
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    source_hook.write_text('#!/bin/sh\nexit 0\n', encoding='utf-8')

    layout = materialize_claude_home_config(
        target_home,
        source_home=source_home,
        profile=ProviderProfileSpec(inherit_config=False),
    )

    assert not (layout.home_root / '.codeisland').exists()


def test_materialize_claude_home_config_respects_inherit_skills_without_disabling_memory(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    source_home = tmp_path / 'system-home'
    target_home = project_root / '.ccb' / 'agents' / 'reviewer' / 'provider-state' / 'claude' / 'home'
    source_claude_dir = source_home / '.claude'
    (source_claude_dir / 'skills' / 'review').mkdir(parents=True, exist_ok=True)
    (source_claude_dir / 'commands').mkdir(parents=True, exist_ok=True)
    (source_claude_dir / 'skills' / 'review' / 'SKILL.md').write_text('skill\n', encoding='utf-8')
    (source_claude_dir / 'commands' / 'check.md').write_text('command\n', encoding='utf-8')
    (source_claude_dir / 'CLAUDE.md').write_text('claude-md\n', encoding='utf-8')
    project_root.mkdir(parents=True, exist_ok=True)
    _write_project_memory(project_root, 'shared memory\n')

    layout = materialize_claude_home_config(
        target_home,
        profile=ProviderProfileSpec(inherit_skills=False, inherit_commands=True),
        source_home=source_home,
        project_root=project_root,
        agent_name='reviewer',
    )

    assert not (layout.claude_dir / 'skills' / 'review').exists()
    assert (layout.claude_dir / 'skills' / 'ask' / 'SKILL.md').is_file()
    assert (layout.claude_dir / 'skills' / 'ccb-clear' / 'SKILL.md').is_file()
    assert (layout.claude_dir / 'skills' / 'ccb-compact' / 'SKILL.md').is_file()
    memory_text = (layout.claude_dir / 'CLAUDE.md').read_text(encoding='utf-8')
    assert '# CCB Managed Agent Memory' in memory_text
    assert 'claude-md' in memory_text
    assert 'shared memory' in memory_text
    assert (layout.claude_dir / 'commands' / 'check.md').read_text(encoding='utf-8') == 'command\n'


@pytest.mark.parametrize(
    ('managed_api_key', 'expected_api_key'),
    [
        pytest.param('managed-token', None, id='legacy-token-alias'),
        pytest.param('independent-api-key', 'independent-api-key', id='distinct-credentials'),
    ],
)
def test_materialize_claude_home_config_preserves_managed_auth_when_source_is_logged_out(
    tmp_path: Path,
    managed_api_key: str,
    expected_api_key: str | None,
) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_settings = source_home / '.claude' / 'settings.json'
    source_settings.parent.mkdir(parents=True, exist_ok=True)
    source_settings.write_text(
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
    target_settings = target_home / '.claude' / 'settings.json'
    target_settings.parent.mkdir(parents=True, exist_ok=True)
    target_settings.write_text(
        json.dumps(
            {
                'env': {
                    'ANTHROPIC_AUTH_TOKEN': 'managed-token',
                    'ANTHROPIC_API_KEY': managed_api_key,
                    'ANTHROPIC_BASE_URL': 'https://managed.example.test',
                },
                'theme': 'stale-theme',
                'hooks': {'Stop': [{'hooks': [{'type': 'command', 'command': 'echo hook'}]}]},
                'permissions': {'allow': ['Bash(ls)']},
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    layout = materialize_claude_home_config(target_home, source_home=source_home)

    payload = json.loads(layout.settings_path.read_text(encoding='utf-8'))
    assert payload['env']['ANTHROPIC_AUTH_TOKEN'] == 'managed-token'
    if expected_api_key is None:
        assert 'ANTHROPIC_API_KEY' not in payload['env']
    else:
        assert payload['env']['ANTHROPIC_API_KEY'] == expected_api_key
    assert payload['env']['ANTHROPIC_BASE_URL'] == 'https://claude.example.test'
    assert payload['theme'] == 'light'
    assert payload['hooks']['Stop'][0]['hooks'][0]['command'] == 'echo hook'
    assert payload['permissions']['allow'] == ['Bash(ls)']


def test_materialize_claude_home_config_preserves_existing_enabled_plugins(tmp_path: Path) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_settings = source_home / '.claude' / 'settings.json'
    source_settings.parent.mkdir(parents=True, exist_ok=True)
    source_settings.write_text(
        json.dumps(
            {
                'enabledPlugins': {
                    'source-plugin@marketplace': True,
                    'typescript-lsp@claude-plugins-official': False,
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    target_settings = target_home / '.claude' / 'settings.json'
    target_settings.parent.mkdir(parents=True, exist_ok=True)
    target_settings.write_text(
        json.dumps(
            {
                'enabledPlugins': {
                    'local-only@marketplace': True,
                    'typescript-lsp@claude-plugins-official': True,
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    layout = materialize_claude_home_config(
        target_home,
        profile=ProviderProfileSpec(inherit_memory=False),
        source_home=source_home,
    )

    payload = json.loads(layout.settings_path.read_text(encoding='utf-8'))
    assert payload['enabledPlugins'] == {
        'local-only@marketplace': True,
        'source-plugin@marketplace': True,
        'typescript-lsp@claude-plugins-official': False,
    }


def test_materialize_claude_home_config_refreshes_source_auth_over_managed_auth(tmp_path: Path) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_settings = source_home / '.claude' / 'settings.json'
    source_settings.parent.mkdir(parents=True, exist_ok=True)
    source_settings.write_text(
        json.dumps(
            {
                'env': {
                    'ANTHROPIC_AUTH_TOKEN': 'system-token',
                    'ANTHROPIC_BASE_URL': 'https://claude.example.test',
                },
                'theme': 'light',
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    target_settings = target_home / '.claude' / 'settings.json'
    target_settings.parent.mkdir(parents=True, exist_ok=True)
    target_settings.write_text(
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

    layout = materialize_claude_home_config(target_home, source_home=source_home)

    payload = json.loads(layout.settings_path.read_text(encoding='utf-8'))
    assert payload['env']['ANTHROPIC_AUTH_TOKEN'] == 'system-token'
    assert payload['env']['ANTHROPIC_BASE_URL'] == 'https://claude.example.test'
    assert payload['theme'] == 'light'
    assert payload['hooks']['Stop'][0]['hooks'][0]['command'] == 'echo hook'


def test_materialize_claude_home_config_removes_only_source_owned_auth_after_logout(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(claude_home_runtime.platform, 'system', lambda: 'Linux')
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_settings = source_home / '.claude' / 'settings.json'
    source_credentials = source_home / '.claude' / '.credentials.json'
    source_auth = source_home / '.config' / 'claude-code' / 'auth.json'
    source_settings.parent.mkdir(parents=True, exist_ok=True)
    source_auth.parent.mkdir(parents=True, exist_ok=True)
    source_settings.write_text(
        json.dumps(
            {
                'env': {
                    'ANTHROPIC_AUTH_TOKEN': 'source-token',
                    'ANTHROPIC_API_KEY': 'source-api-key',
                    'ANTHROPIC_BASE_URL': 'https://source.example.test',
                }
            }
        ),
        encoding='utf-8',
    )
    source_credentials.write_text(
        '{"claudeAiOauth":{"refreshToken":"source-refresh"}}\n',
        encoding='utf-8',
    )
    source_auth.write_text('{"accessToken":"source-access"}\n', encoding='utf-8')
    for index, path in enumerate((source_settings, source_credentials, source_auth), start=1):
        path.chmod(0o600 + index)
        timestamp_ns = 1_700_000_000_000_000_000 + index
        os.utime(path, ns=(timestamp_ns, timestamp_ns))
    source_snapshot = {
        path: (
            path.read_bytes(),
            path.stat().st_mode & 0o777,
            path.stat().st_mtime_ns,
        )
        for path in (source_settings, source_credentials, source_auth)
    }

    layout = materialize_claude_home_config(target_home, source_home=source_home)

    assert layout.credentials_path.is_file()
    assert layout.auth_path.is_file()
    manifest_path = target_home / '.ccb-auth-projection.json'
    first_manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    assert manifest_path.stat().st_mode & 0o777 == 0o600
    assert first_manifest['status'] == 'inherited_auth'
    assert first_manifest['projected_files'] == [
        '.claude/.credentials.json',
        '.config/claude-code/auth.json',
    ]
    assert first_manifest['projected_env_keys'] == [
        'ANTHROPIC_API_KEY',
        'ANTHROPIC_AUTH_TOKEN',
    ]
    assert all(
        (
            path.read_bytes(),
            path.stat().st_mode & 0o777,
            path.stat().st_mtime_ns,
        )
        == snapshot
        for path, snapshot in source_snapshot.items()
    )

    source_credentials.unlink()
    source_auth.unlink()
    source_settings.write_text('{}\n', encoding='utf-8')
    materialize_claude_home_config(target_home, source_home=source_home)

    settings = json.loads(layout.settings_path.read_text(encoding='utf-8'))
    assert 'env' not in settings
    assert not layout.credentials_path.exists()
    assert not layout.auth_path.exists()
    second_manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    assert second_manifest['status'] == 'source_auth_absent'
    assert second_manifest['projected_files'] == []
    assert second_manifest['projected_env_keys'] == []
    assert source_settings.read_text(encoding='utf-8') == '{}\n'
    assert source_snapshot[source_credentials][0] == (
        b'{"claudeAiOauth":{"refreshToken":"source-refresh"}}\n'
    )
    assert source_snapshot[source_auth][0] == b'{"accessToken":"source-access"}\n'


def test_materialize_claude_home_config_preserves_unmarked_auth_with_malformed_manifest(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(claude_home_runtime.platform, 'system', lambda: 'Linux')
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_home.mkdir(parents=True)
    target_credentials = target_home / '.claude' / '.credentials.json'
    target_credentials.parent.mkdir(parents=True)
    target_credentials.write_text(
        '{"claudeAiOauth":{"refreshToken":"agent-private"}}\n',
        encoding='utf-8',
    )
    (target_home / '.ccb-auth-projection.json').write_text(
        '{malformed',
        encoding='utf-8',
    )

    layout = materialize_claude_home_config(target_home, source_home=source_home)

    assert json.loads(layout.credentials_path.read_text(encoding='utf-8')) == {
        'claudeAiOauth': {'refreshToken': 'agent-private'}
    }
    manifest = json.loads(
        (target_home / '.ccb-auth-projection.json').read_text(encoding='utf-8')
    )
    assert manifest['status'] == 'agent_private_or_unmanaged'
    assert manifest['projected_files'] == []


def test_materialize_claude_home_config_source_read_error_preserves_projection(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(claude_home_runtime.platform, 'system', lambda: 'Linux')
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_credentials = source_home / '.claude' / '.credentials.json'
    source_credentials.parent.mkdir(parents=True)
    source_credentials.write_text(
        '{"claudeAiOauth":{"refreshToken":"source-refresh"}}\n',
        encoding='utf-8',
    )
    layout = materialize_claude_home_config(target_home, source_home=source_home)
    projected = layout.credentials_path.read_bytes()

    original_lstat = Path.lstat

    def fail_source_lstat(path: Path):
        if path == source_credentials:
            raise PermissionError('simulated source read failure')
        return original_lstat(path)

    monkeypatch.setattr(Path, 'lstat', fail_source_lstat)

    with pytest.raises(RuntimeError, match='cannot inspect inherited Claude auth source'):
        materialize_claude_home_config(target_home, source_home=source_home)

    assert layout.credentials_path.read_bytes() == projected


def test_materialize_claude_home_config_preserves_unmarked_private_auth_when_auth_is_not_inherited(
    tmp_path: Path,
) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    target_settings = target_home / '.claude' / 'settings.json'
    target_auth = target_home / '.config' / 'claude-code' / 'auth.json'
    target_credentials = target_home / '.claude' / '.credentials.json'
    target_settings.parent.mkdir(parents=True, exist_ok=True)
    target_settings.write_text(
        json.dumps(
            {
                'env': {'ANTHROPIC_AUTH_TOKEN': 'managed-token'},
                'theme': 'stale-theme',
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    target_auth.parent.mkdir(parents=True, exist_ok=True)
    target_auth.write_text('{"refresh_token":"stale-token"}\n', encoding='utf-8')
    target_credentials.write_text('{"claudeAiOauth":{"refreshToken":"stale-token"}}\n', encoding='utf-8')

    layout = materialize_claude_home_config(
        target_home,
        profile=ProviderProfileSpec(inherit_auth=False, inherit_api=False, inherit_config=True),
        source_home=source_home,
    )

    payload = json.loads(layout.settings_path.read_text(encoding='utf-8'))
    assert payload == {
        'allowedTools': [],
        'env': {'ANTHROPIC_AUTH_TOKEN': 'managed-token'},
    }
    assert json.loads(layout.auth_path.read_text(encoding='utf-8')) == {
        'refresh_token': 'stale-token'
    }
    assert json.loads(layout.credentials_path.read_text(encoding='utf-8')) == {
        'claudeAiOauth': {'refreshToken': 'stale-token'}
    }


def test_materialize_claude_home_config_preserves_managed_official_login_when_source_is_logged_out(
    monkeypatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(claude_home_runtime.platform, 'system', lambda: 'Linux')
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_settings = source_home / '.claude' / 'settings.json'
    source_settings.parent.mkdir(parents=True, exist_ok=True)
    source_settings.write_text(
        json.dumps(
            {
                'theme': 'light',
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    target_credentials = target_home / '.claude' / '.credentials.json'
    target_credentials.parent.mkdir(parents=True, exist_ok=True)
    target_credentials.write_text('{"claudeAiOauth":{"refreshToken":"managed-refresh-token"}}\n', encoding='utf-8')

    layout = materialize_claude_home_config(target_home, source_home=source_home)

    assert json.loads(layout.credentials_path.read_text(encoding='utf-8'))['claudeAiOauth']['refreshToken'] == 'managed-refresh-token'


def test_materialize_gemini_profile_keeps_runtime_home_unset_without_explicit_override(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'

    profile = materialize_provider_profile(
        layout=PathLayout(project_root),
        spec=_spec(
            'agent1',
            provider='gemini',
            provider_profile=ProviderProfileSpec(
                mode='isolated',
                inherit_api=False,
            ),
        ),
        workspace_path=project_root,
    )

    assert profile.runtime_home is None


def test_materialize_gemini_profile_rejects_explicit_home_override(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    explicit_home = tmp_path / 'gemini-home'

    with pytest.raises(ValueError, match='provider_profile.home is supported only for codex'):
        materialize_provider_profile(
            layout=PathLayout(project_root),
            spec=_spec(
                'agent1',
                provider='gemini',
                provider_profile=ProviderProfileSpec(
                    mode='isolated',
                    home=str(explicit_home),
                    inherit_api=False,
                ),
            ),
            workspace_path=project_root,
        )


def test_validate_provider_runtime_home_uniqueness_rejects_duplicate_codex_home(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    shared_home = tmp_path / 'shared-codex-home'

    with pytest.raises(ValueError, match='duplicate effective codex_home'):
        validate_provider_runtime_home_uniqueness(
            layout=PathLayout(project_root),
            specs=(
                _spec(
                    'agent1',
                    provider='codex',
                    provider_profile=ProviderProfileSpec(mode='isolated', home=str(shared_home)),
                ),
                _spec(
                    'agent2',
                    provider='codex',
                    provider_profile=ProviderProfileSpec(mode='isolated', home=str(shared_home)),
                ),
            ),
        )


def test_materialize_gemini_home_config_projects_system_settings_into_managed_home(tmp_path: Path) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_settings = source_home / '.gemini' / 'settings.json'
    source_settings.parent.mkdir(parents=True, exist_ok=True)
    source_settings.write_text(
        json.dumps(
            {
                'env': {
                    'GEMINI_API_KEY': 'system-gemini-key',
                    'GEMINI_MODEL': 'gemini-3.1-pro-preview',
                    'GOOGLE_API_KEY': 'system-google-key',
                    'GOOGLE_GEMINI_BASE_URL': 'https://chatapi.onechats.ai',
                },
                'theme': 'Default',
                'general': {
                    'enableAutoUpdate': True,
                    'enableAutoUpdateNotification': True,
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    layout = materialize_gemini_home_config(target_home, source_home=source_home)

    payload = json.loads(layout.settings_path.read_text(encoding='utf-8'))
    assert payload['env']['GEMINI_API_KEY'] == 'system-gemini-key'
    assert payload['env']['GEMINI_MODEL'] == 'gemini-3.1-pro-preview'
    assert payload['env']['GOOGLE_API_KEY'] == 'system-google-key'
    assert payload['env']['GOOGLE_GEMINI_BASE_URL'] == 'https://chatapi.onechats.ai'
    assert payload['theme'] == 'Default'
    assert payload['general']['enableAutoUpdate'] is False
    assert payload['general']['enableAutoUpdateNotification'] is False


def test_materialize_gemini_home_config_disables_autoupdate_without_source_settings(
    tmp_path: Path,
) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'

    layout = materialize_gemini_home_config(target_home, source_home=source_home)

    payload = json.loads(layout.settings_path.read_text(encoding='utf-8'))
    assert payload == {
        'general': {
            'enableAutoUpdate': False,
            'enableAutoUpdateNotification': False,
        }
    }


def test_materialize_gemini_home_config_projects_dotenv_api_auth_into_managed_home(tmp_path: Path) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_gemini = source_home / '.gemini'
    source_gemini.mkdir(parents=True, exist_ok=True)
    (source_gemini / 'settings.json').write_text(
        json.dumps(
            {
                'security': {
                    'auth': {
                        'selectedType': 'gemini-api-key',
                    }
                }
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    (source_gemini / '.env').write_text(
        '\n'.join(
            [
                'GEMINI_API_KEY=system-gemini-key',
                'GOOGLE_GEMINI_BASE_URL=https://gemini.example.test',
                'GOOGLE_GENAI_USE_GCA=true',
                'GOOGLE_CLOUD_PROJECT=demo-project',
                'OTHER_SECRET=must-not-copy',
            ]
        )
        + '\n',
        encoding='utf-8',
    )

    layout = materialize_gemini_home_config(target_home, source_home=source_home)

    payload = json.loads(layout.settings_path.read_text(encoding='utf-8'))
    dotenv = (layout.gemini_dir / '.env').read_text(encoding='utf-8')
    assert payload['security']['auth']['selectedType'] == 'gemini-api-key'
    assert 'GEMINI_API_KEY="system-gemini-key"' in dotenv
    assert 'GOOGLE_GEMINI_BASE_URL="https://gemini.example.test"' in dotenv
    assert 'GOOGLE_GENAI_USE_GCA="true"' in dotenv
    assert 'GOOGLE_CLOUD_PROJECT="demo-project"' in dotenv
    assert 'OTHER_SECRET' not in dotenv


def test_materialize_gemini_home_config_projects_oauth_credentials_for_login_auth(tmp_path: Path) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_settings = source_home / '.gemini' / 'settings.json'
    source_oauth = source_home / '.gemini' / 'oauth_creds.json'
    source_accounts = source_home / '.gemini' / 'google_accounts.json'
    source_settings.parent.mkdir(parents=True, exist_ok=True)
    source_settings.write_text(
        json.dumps(
            {
                'security': {
                    'auth': {
                        'selectedType': 'oauth-personal',
                    }
                },
                'theme': 'Default',
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    source_oauth.write_text(
        json.dumps({'refresh_token': 'system-refresh-token'}, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    source_accounts.write_text(
        json.dumps({'active': 'user@example.test'}, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )

    layout = materialize_gemini_home_config(target_home, source_home=source_home)

    payload = json.loads(layout.settings_path.read_text(encoding='utf-8'))
    assert payload['security']['auth']['selectedType'] == 'oauth-personal'
    assert json.loads((layout.gemini_dir / 'oauth_creds.json').read_text(encoding='utf-8'))['refresh_token'] == 'system-refresh-token'
    assert json.loads((layout.gemini_dir / 'google_accounts.json').read_text(encoding='utf-8'))['active'] == 'user@example.test'


def test_materialize_gemini_home_config_removes_only_source_owned_auth_after_logout(
    tmp_path: Path,
) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_settings = source_home / '.gemini' / 'settings.json'
    source_oauth = source_home / '.gemini' / 'oauth_creds.json'
    source_accounts = source_home / '.gemini' / 'google_accounts.json'
    source_settings.parent.mkdir(parents=True)
    source_settings.write_text(
        json.dumps({'security': {'auth': {'selectedType': 'oauth-personal'}}}) + '\n',
        encoding='utf-8',
    )
    source_oauth.write_text('{"refresh_token":"source-refresh"}\n', encoding='utf-8')
    source_accounts.write_text('{"active":"source@example.test"}\n', encoding='utf-8')
    source_paths = (source_settings, source_oauth, source_accounts)
    for index, path in enumerate(source_paths, start=1):
        path.chmod(0o600 + index)
        timestamp_ns = 1_700_000_100_000_000_000 + index
        os.utime(path, ns=(timestamp_ns, timestamp_ns))
    source_snapshot = {
        path: (
            path.read_bytes(),
            path.stat().st_mode & 0o777,
            path.stat().st_mtime_ns,
        )
        for path in source_paths
    }

    layout = materialize_gemini_home_config(target_home, source_home=source_home)

    manifest_path = target_home / '.ccb-auth-projection.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    assert manifest_path.stat().st_mode & 0o777 == 0o600
    assert manifest['status'] == 'inherited_auth'
    assert manifest['projected_files'] == ['google_accounts.json', 'oauth_creds.json']
    assert manifest['projected_selected_type'] == 'oauth-personal'
    assert all(
        (
            path.read_bytes(),
            path.stat().st_mode & 0o777,
            path.stat().st_mtime_ns,
        )
        == source_snapshot[path]
        for path in source_paths
    )

    source_oauth.unlink()
    source_accounts.unlink()
    source_settings.write_text('{}\n', encoding='utf-8')
    materialize_gemini_home_config(target_home, source_home=source_home)

    settings = json.loads(layout.settings_path.read_text(encoding='utf-8'))
    assert settings.get('security', {}).get('auth', {}).get('selectedType') is None
    assert not (layout.gemini_dir / 'oauth_creds.json').exists()
    assert not (layout.gemini_dir / 'google_accounts.json').exists()
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    assert manifest['status'] == 'source_auth_absent'
    assert manifest['projected_files'] == []
    assert manifest['projected_selected_type'] is None


def test_materialize_gemini_home_config_preserves_unmarked_auth_with_malformed_manifest(
    tmp_path: Path,
) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_home.mkdir(parents=True)
    target_settings = target_home / '.gemini' / 'settings.json'
    target_oauth = target_home / '.gemini' / 'oauth_creds.json'
    target_settings.parent.mkdir(parents=True)
    target_settings.write_text(
        '{"security":{"auth":{"selectedType":"oauth-personal"}}}\n',
        encoding='utf-8',
    )
    target_oauth.write_text('{"refresh_token":"agent-private"}\n', encoding='utf-8')
    (target_home / '.ccb-auth-projection.json').write_text(
        '{malformed',
        encoding='utf-8',
    )

    layout = materialize_gemini_home_config(target_home, source_home=source_home)

    assert json.loads(target_oauth.read_text(encoding='utf-8')) == {
        'refresh_token': 'agent-private'
    }
    settings = json.loads(layout.settings_path.read_text(encoding='utf-8'))
    assert settings['security']['auth']['selectedType'] == 'oauth-personal'
    manifest = json.loads(
        (target_home / '.ccb-auth-projection.json').read_text(encoding='utf-8')
    )
    assert manifest['status'] == 'agent_private_or_unmanaged'
    assert manifest['projected_files'] == []


def test_materialize_gemini_home_config_source_read_error_preserves_projection(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_settings = source_home / '.gemini' / 'settings.json'
    source_oauth = source_home / '.gemini' / 'oauth_creds.json'
    source_settings.parent.mkdir(parents=True)
    source_settings.write_text(
        '{"security":{"auth":{"selectedType":"oauth-personal"}}}\n',
        encoding='utf-8',
    )
    source_oauth.write_text('{"refresh_token":"source-refresh"}\n', encoding='utf-8')
    layout = materialize_gemini_home_config(target_home, source_home=source_home)
    projected = (layout.gemini_dir / 'oauth_creds.json').read_bytes()
    manifest_path = target_home / '.ccb-auth-projection.json'
    manifest = manifest_path.read_bytes()
    original_lstat = Path.lstat

    def fail_source_lstat(path: Path):
        if path == source_oauth:
            raise PermissionError('simulated source read failure')
        return original_lstat(path)

    monkeypatch.setattr(Path, 'lstat', fail_source_lstat)

    with pytest.raises(RuntimeError, match='cannot inspect inherited Gemini auth source'):
        materialize_gemini_home_config(target_home, source_home=source_home)

    assert (layout.gemini_dir / 'oauth_creds.json').read_bytes() == projected
    assert manifest_path.read_bytes() == manifest


def test_materialize_gemini_home_config_explicit_credential_suppresses_external_auth(
    tmp_path: Path,
) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_gemini = source_home / '.gemini'
    source_gemini.mkdir(parents=True)
    (source_gemini / 'settings.json').write_text(
        json.dumps(
            {
                'env': {
                    'GOOGLE_API_KEY': 'source-key',
                    'GOOGLE_GEMINI_BASE_URL': 'https://source.example.test',
                    'GOOGLE_CLOUD_PROJECT': 'source-project',
                },
                'security': {'auth': {'selectedType': 'oauth-personal'}},
            }
        )
        + '\n',
        encoding='utf-8',
    )
    (source_gemini / '.env').write_text(
        '\n'.join(
            (
                'GOOGLE_API_KEY=source-key',
                'GOOGLE_GEMINI_BASE_URL=https://source.example.test',
                'GOOGLE_CLOUD_PROJECT=source-project',
            )
        )
        + '\n',
        encoding='utf-8',
    )
    (source_gemini / 'oauth_creds.json').write_text(
        '{"refresh_token":"source-refresh"}\n',
        encoding='utf-8',
    )
    profile = ProviderProfileSpec(
        env={
            'GEMINI_API_KEY': 'explicit-key',
            'GOOGLE_GEMINI_BASE_URL': 'https://explicit.example.test',
        }
    )

    layout = materialize_gemini_home_config(
        target_home,
        profile=profile,
        source_home=source_home,
    )

    settings = json.loads(layout.settings_path.read_text(encoding='utf-8'))
    assert settings['security']['auth']['selectedType'] == 'gemini-api-key'
    assert settings['env'] == {'GOOGLE_CLOUD_PROJECT': 'source-project'}
    dotenv = (layout.gemini_dir / '.env').read_text(encoding='utf-8')
    assert 'GOOGLE_CLOUD_PROJECT="source-project"' in dotenv
    assert 'GOOGLE_API_KEY' not in dotenv
    assert 'GOOGLE_GEMINI_BASE_URL' not in dotenv
    assert not (layout.gemini_dir / 'oauth_creds.json').exists()
    manifest = json.loads(
        (target_home / '.ccb-auth-projection.json').read_text(encoding='utf-8')
    )
    assert manifest['status'] == 'explicit_api_authority'
    assert manifest['projected_files'] == []


def test_materialize_gemini_home_config_imports_system_keyring_oauth_one_way(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_settings = source_home / '.gemini' / 'settings.json'
    source_settings.parent.mkdir(parents=True)
    source_settings.write_text(
        json.dumps(
            {'security': {'auth': {'selectedType': 'oauth-personal'}}},
            ensure_ascii=False,
        ),
        encoding='utf-8',
    )
    stored = json.dumps(
        {
            'serverName': 'main-account',
            'token': {
                'accessToken': 'source-access-token',
                'refreshToken': 'source-refresh-token',
                'tokenType': 'Bearer',
                'scope': 'scope-a scope-b',
                'expiresAt': 1234567890,
            },
        }
    )
    keyring_calls: list[tuple[str, str]] = []

    monkeypatch.delenv('CCB_SOURCE_HOME', raising=False)
    monkeypatch.setattr(gemini_home_runtime, '_system_home_root', lambda: source_home)

    def fake_read(service: str, account: str, **_kwargs):
        keyring_calls.append((service, account))
        return gemini_home_runtime.KeyringReadResult('present', value=stored)

    monkeypatch.setattr(gemini_home_runtime, 'read_keyring_password_state', fake_read)

    layout = materialize_gemini_home_config(target_home)

    assert keyring_calls == [('gemini-cli-oauth', 'main-account')]
    projected = json.loads(
        (layout.gemini_dir / 'oauth_creds.json').read_text(encoding='utf-8')
    )
    assert projected == {
        'access_token': 'source-access-token',
        'refresh_token': 'source-refresh-token',
        'token_type': 'Bearer',
        'scope': 'scope-a scope-b',
        'expiry_date': 1234567890,
    }
    (layout.gemini_dir / 'oauth_creds.json').unlink()
    assert source_settings.is_file()


def test_materialize_gemini_home_config_keyring_error_preserves_projection(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_settings = source_home / '.gemini' / 'settings.json'
    source_settings.parent.mkdir(parents=True)
    source_settings.write_text(
        '{"security":{"auth":{"selectedType":"oauth-personal"}}}\n',
        encoding='utf-8',
    )
    stored = json.dumps(
        {
            'token': {
                'accessToken': 'source-access',
                'refreshToken': 'source-refresh',
            }
        }
    )
    external_state = 'present'

    def fake_read(*_args, **_kwargs):
        if external_state == 'present':
            return gemini_home_runtime.KeyringReadResult('present', value=stored)
        return gemini_home_runtime.KeyringReadResult('error', detail='simulated keyring failure')

    monkeypatch.delenv('CCB_SOURCE_HOME', raising=False)
    monkeypatch.setattr(gemini_home_runtime, '_system_home_root', lambda: source_home)
    monkeypatch.setattr(gemini_home_runtime, 'read_keyring_password_state', fake_read)

    layout = materialize_gemini_home_config(target_home)
    projected = (layout.gemini_dir / 'oauth_creds.json').read_bytes()
    external_state = 'error'

    with pytest.raises(RuntimeError, match='cannot determine external Gemini keyring login state'):
        materialize_gemini_home_config(target_home)

    assert (layout.gemini_dir / 'oauth_creds.json').read_bytes() == projected


def test_materialize_gemini_home_config_does_not_query_keyring_for_fixture_source(
    monkeypatch,
    tmp_path: Path,
) -> None:
    source_home = tmp_path / 'fixture-home'
    target_home = tmp_path / 'managed-home'
    settings = source_home / '.gemini' / 'settings.json'
    settings.parent.mkdir(parents=True)
    settings.write_text(
        '{"security":{"auth":{"selectedType":"oauth-personal"}}}\n',
        encoding='utf-8',
    )
    monkeypatch.setattr(
        gemini_home_runtime,
        'read_keyring_password_state',
        lambda *_args, **_kwargs: pytest.fail('explicit fixture must not query OS keyring'),
    )

    layout = materialize_gemini_home_config(target_home, source_home=source_home)

    assert not (layout.gemini_dir / 'oauth_creds.json').exists()


def test_materialize_gemini_home_config_strips_oauth_selection_and_credentials_when_auth_not_inherited(tmp_path: Path) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_settings = source_home / '.gemini' / 'settings.json'
    source_oauth = source_home / '.gemini' / 'oauth_creds.json'
    source_accounts = source_home / '.gemini' / 'google_accounts.json'
    source_settings.parent.mkdir(parents=True, exist_ok=True)
    source_settings.write_text(
        json.dumps(
            {
                'security': {
                    'auth': {
                        'selectedType': 'oauth-personal',
                    }
                },
                'theme': 'Default',
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    source_oauth.write_text(
        json.dumps({'refresh_token': 'system-refresh-token'}, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    source_accounts.write_text('{"active":"source@example.test"}\n', encoding='utf-8')
    materialize_gemini_home_config(target_home, source_home=source_home)

    layout = materialize_gemini_home_config(
        target_home,
        profile=ProviderProfileSpec(inherit_auth=False, inherit_config=True),
        source_home=source_home,
    )

    payload = json.loads(layout.settings_path.read_text(encoding='utf-8'))
    assert payload['theme'] == 'Default'
    assert payload.get('security', {}).get('auth', {}).get('selectedType') is None
    assert not (layout.gemini_dir / 'oauth_creds.json').exists()
    assert not (layout.gemini_dir / 'google_accounts.json').exists()


def test_materialize_gemini_home_config_strips_api_auth_selection_when_api_not_inherited(tmp_path: Path) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_settings = source_home / '.gemini' / 'settings.json'
    source_settings.parent.mkdir(parents=True, exist_ok=True)
    (source_home / '.gemini' / '.env').write_text('GEMINI_API_KEY=system-gemini-key\n', encoding='utf-8')
    source_settings.write_text(
        json.dumps(
            {
                'env': {'GEMINI_API_KEY': 'system-gemini-key'},
                'security': {
                    'auth': {
                        'selectedType': 'gemini-api-key',
                    }
                },
                'theme': 'Default',
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    layout = materialize_gemini_home_config(
        target_home,
        profile=ProviderProfileSpec(inherit_api=False, inherit_config=True),
        source_home=source_home,
    )

    payload = json.loads(layout.settings_path.read_text(encoding='utf-8'))
    assert payload['theme'] == 'Default'
    assert payload.get('env') is None
    assert payload.get('security', {}).get('auth', {}).get('selectedType') is None
    assert not (layout.gemini_dir / '.env').exists()


def test_materialize_gemini_env_refresh_preserves_agent_private_values(tmp_path: Path) -> None:
    source_home = tmp_path / 'system-home-env-refresh'
    target_home = tmp_path / 'managed-home-env-refresh'
    source_env = source_home / '.gemini' / '.env'
    source_env.parent.mkdir(parents=True)
    source_env.write_text('GEMINI_API_KEY=source-a\n', encoding='utf-8')

    layout = materialize_gemini_home_config(target_home, source_home=source_home)
    target_env = layout.gemini_dir / '.env'
    target_env.write_text(
        target_env.read_text(encoding='utf-8') + 'AGENT_PRIVATE_VALUE="keep-me"\n',
        encoding='utf-8',
    )
    source_env.write_text('GEMINI_API_KEY=source-b\n', encoding='utf-8')

    materialize_gemini_home_config(target_home, source_home=source_home)

    payload = gemini_home_runtime._read_env_file(target_env)
    manifest = json.loads((target_home / '.ccb-auth-projection.json').read_text(encoding='utf-8'))
    assert payload == {
        'AGENT_PRIVATE_VALUE': 'keep-me',
        'GEMINI_API_KEY': 'source-b',
    }
    assert manifest['projected_env_keys'] == ['GEMINI_API_KEY']


def test_materialize_gemini_env_removes_only_manifest_owned_values(tmp_path: Path) -> None:
    source_home = tmp_path / 'system-home-env-logout'
    target_home = tmp_path / 'managed-home-env-logout'
    source_env = source_home / '.gemini' / '.env'
    source_env.parent.mkdir(parents=True)
    source_env.write_text('GOOGLE_API_KEY=source-key\n', encoding='utf-8')
    layout = materialize_gemini_home_config(target_home, source_home=source_home)
    target_env = layout.gemini_dir / '.env'
    target_env.write_text(
        target_env.read_text(encoding='utf-8') + 'AGENT_PRIVATE_VALUE="keep-me"\n',
        encoding='utf-8',
    )
    source_env.unlink()

    materialize_gemini_home_config(target_home, source_home=source_home)

    assert gemini_home_runtime._read_env_file(target_env) == {
        'AGENT_PRIVATE_VALUE': 'keep-me',
    }


def test_materialize_gemini_home_config_preserves_runtime_hooks(tmp_path: Path) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_settings = source_home / '.gemini' / 'settings.json'
    source_settings.parent.mkdir(parents=True, exist_ok=True)
    source_settings.write_text(
        json.dumps(
            {
                'env': {'GEMINI_API_KEY': 'system-gemini-key'},
                'theme': 'Atom One',
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )
    target_settings = target_home / '.gemini' / 'settings.json'
    target_settings.parent.mkdir(parents=True, exist_ok=True)
    target_settings.write_text(
        json.dumps(
            {
                'hooks': {
                    'AfterAgent': [
                        {'matcher': '*', 'hooks': [{'type': 'command', 'command': 'echo hook'}]},
                    ]
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding='utf-8',
    )

    layout = materialize_gemini_home_config(target_home, source_home=source_home)

    payload = json.loads(layout.settings_path.read_text(encoding='utf-8'))
    assert payload['env']['GEMINI_API_KEY'] == 'system-gemini-key'
    assert payload['theme'] == 'Atom One'
    assert payload['hooks']['AfterAgent'][0]['hooks'][0]['command'] == 'echo hook'


def test_materialize_gemini_home_config_merges_trusted_folders(tmp_path: Path) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_trust = source_home / '.gemini' / 'trustedFolders.json'
    source_trust.parent.mkdir(parents=True, exist_ok=True)
    source_trust.write_text(
        json.dumps({'/system/project': 'TRUST_FOLDER'}, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )
    target_trust = target_home / '.gemini' / 'trustedFolders.json'
    target_trust.parent.mkdir(parents=True, exist_ok=True)
    target_trust.write_text(
        json.dumps({'/managed/project': 'TRUST_FOLDER'}, ensure_ascii=False, indent=2),
        encoding='utf-8',
    )

    layout = materialize_gemini_home_config(target_home, source_home=source_home)

    payload = json.loads(layout.trusted_folders_path.read_text(encoding='utf-8'))
    assert payload['/system/project'] == 'TRUST_FOLDER'
    assert payload['/managed/project'] == 'TRUST_FOLDER'


def test_materialize_gemini_home_config_writes_project_memory_bundle(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_gemini = source_home / '.gemini'
    source_gemini.mkdir(parents=True, exist_ok=True)
    (source_gemini / 'GEMINI.md').write_text('user gemini memory\n', encoding='utf-8')
    project_root.mkdir(parents=True, exist_ok=True)
    _write_project_memory(project_root, 'shared ask memory\n')
    (project_root / 'GEMINI.md').write_text('project gemini memory\n', encoding='utf-8')
    private_memory = project_root / '.ccb' / 'agents' / 'reviewer' / 'memory.md'
    private_memory.parent.mkdir(parents=True, exist_ok=True)
    private_memory.write_text('reviewer private memory\n', encoding='utf-8')

    layout = materialize_gemini_home_config(
        target_home,
        source_home=source_home,
        project_root=project_root,
        agent_name='reviewer',
        workspace_path=tmp_path / 'worktree',
    )

    text = (layout.gemini_dir / 'GEMINI.md').read_text(encoding='utf-8')
    settings = json.loads(layout.settings_path.read_text(encoding='utf-8'))
    assert '# CCB Managed Agent Memory' in text
    assert '## Provider User Memory' in text
    assert 'user gemini memory' in text
    assert '## CCB Shared Project Memory' in text
    assert 'shared ask memory' in text
    assert '## Provider-Native Project Memory' in text
    assert 'project gemini memory' in text
    assert '## Agent Private Memory' in text
    assert 'reviewer private memory' in text
    assert settings['contextFileName'] == 'GEMINI.md'


def test_materialize_gemini_home_config_respects_inherit_memory_flag(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    target_gemini = target_home / '.gemini'
    target_gemini.mkdir(parents=True, exist_ok=True)
    (target_gemini / 'GEMINI.md').write_text('stale managed memory\n', encoding='utf-8')
    (target_gemini / 'settings.json').write_text('{"contextFileName":"GEMINI.md"}\n', encoding='utf-8')

    layout = materialize_gemini_home_config(
        target_home,
        profile=ProviderProfileSpec(inherit_memory=False),
        source_home=source_home,
        project_root=project_root,
        agent_name='reviewer',
    )

    settings = json.loads(layout.settings_path.read_text(encoding='utf-8'))
    assert not (layout.gemini_dir / 'GEMINI.md').exists()
    assert 'contextFileName' not in settings


def test_materialize_gemini_home_config_skips_memory_without_project_context(tmp_path: Path) -> None:
    source_home = tmp_path / 'system-home'
    target_home = tmp_path / 'managed-home'
    source_gemini = source_home / '.gemini'
    target_gemini = target_home / '.gemini'
    source_gemini.mkdir(parents=True, exist_ok=True)
    target_gemini.mkdir(parents=True, exist_ok=True)
    (source_gemini / 'GEMINI.md').write_text('source-only memory\n', encoding='utf-8')

    layout = materialize_gemini_home_config(target_home, source_home=source_home)

    assert not (layout.gemini_dir / 'GEMINI.md').exists()


def test_render_toml_value_handles_dict_inline_table() -> None:
    from provider_profiles.codex_home_config import _render_toml_value
    result = _render_toml_value({'name': 'test', 'enabled': False})
    assert result == '{ name = "test", enabled = false }'


def test_render_toml_value_handles_empty_dict() -> None:
    from provider_profiles.codex_home_config import _render_toml_value
    result = _render_toml_value({})
    assert result == '{}'


def test_render_toml_value_handles_dict_in_mixed_list() -> None:
    from provider_profiles.codex_home_config import _render_toml_value
    result = _render_toml_value(['literal', {'name': 'test'}])
    assert result == '["literal", { name = "test" }]'


def test_render_toml_sections_handles_inline_table_arrays() -> None:
    from provider_profiles.codex_home_config import _render_toml_document
    payload = {
        'skills': {
            'config': [
                {'path': '/a/skill.md', 'enabled': False},
                {'name': 'plugin:skill', 'enabled': True},
            ]
        }
    }
    rendered = _render_toml_document(payload)
    assert 'config = [{ path = "/a/skill.md", enabled = false }, { name = "plugin:skill", enabled = true }]' in rendered
    assert tomllib.loads(rendered) == payload


def test_render_toml_sections_handles_array_of_tables_with_only_child_tables() -> None:
    from provider_profiles.codex_home_config import _render_toml_document
    payload = {
        'items': [
            {'child': {'x': 1}},
            {'child': {'x': 2}},
        ]
    }
    rendered = _render_toml_document(payload)
    assert tomllib.loads(rendered) == {
        'items': [
            {'child': {'x': 1}},
            {'child': {'x': 2}},
        ]
    }


def test_materialize_codex_home_config_with_skills_config_array(tmp_path: Path) -> None:
    source_home = tmp_path / 'codex-home'
    target_home = tmp_path / 'target-home'
    source_home.mkdir(parents=True, exist_ok=True)
    (source_home / 'config.toml').write_text(
        'model = "gpt-5.5"\n'
        '\n'
        '[[skills.config]]\n'
        'path = "/a/skill.md"\n'
        'enabled = false\n'
        '\n'
        '[[skills.config]]\n'
        'name = "plugin:other"\n'
        'enabled = true\n'
        '\n'
        '[features]\n'
        'unified_exec = true\n',
        encoding='utf-8',
    )

    codex_home_config.materialize_codex_home_config(
        target_home,
        source_home=source_home,
    )

    text = (target_home / 'config.toml').read_text(encoding='utf-8')
    assert 'config = [{ path = "/a/skill.md", enabled = false }, { name = "plugin:other", enabled = true }]' in text
    parsed = tomllib.loads(text)
    assert parsed['skills']['config'] == [
        {'path': '/a/skill.md', 'enabled': False},
        {'name': 'plugin:other', 'enabled': True},
    ]
