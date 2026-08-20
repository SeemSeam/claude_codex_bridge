from __future__ import annotations

from pathlib import Path
try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - Python < 3.11
    import tomli as tomllib

import pytest

from agents.config_identity import project_config_identity_payload
import agents.config_loader_runtime.io_runtime.documents as config_documents
import agents.config_loader_runtime.defaults_runtime.project as default_project
from agents.config_loader import (
    CONFIG_SOURCE_BUILTIN_DEFAULT,
    CONFIG_SOURCE_PROJECT,
    CONFIG_SOURCE_USER,
    ConfigValidationError,
    build_default_project_config,
    ensure_bootstrap_project_config,
    ensure_default_project_config,
    load_project_config,
    render_project_config_text,
)
import runtime_env.source_home as source_home_module
from agents.models import AgentApiSpec, PermissionMode, QueuePolicy, RestoreMode, RuntimeMode, WorkspaceMode


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def _write_installed_role(store_root: Path, role_id: str, *, default_agent_name: str = 'mother') -> None:
    role_root = store_root / 'installed' / role_id / 'current'
    _write(
        role_root / 'role.toml',
        f'''id = "{role_id}"
version = "0.1.0"

[identity]
default_agent_name = "{default_agent_name}"
''',
    )


def test_load_valid_project_config(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(config_path, 'cmd; agent1:codex\n')

    result = load_project_config(project_root)
    spec = result.config.agents['agent1']
    assert result.source_path == config_path
    assert spec.workspace_mode is WorkspaceMode.INPLACE
    assert spec.runtime_mode is RuntimeMode.PANE_BACKED
    assert spec.restore_default is RestoreMode.AUTO
    assert spec.permission_default is PermissionMode.MANUAL
    assert spec.queue_policy is QueuePolicy.SERIAL_PER_AGENT
    assert result.config.layout_spec == 'cmd; agent1:codex'
    assert result.config.windows_explicit is False
    assert result.config.entry_window == 'main'
    assert [window.name for window in result.config.windows] == ['main']
    assert result.config.windows[0].layout_spec == 'cmd; agent1:codex'
    assert result.config.windows[0].agent_names == ('agent1',)
    assert result.config.maintenance_heartbeat.enabled is False
    assert result.config.maintenance_heartbeat.assessor == 'ccb_self'


def test_load_project_config_supports_agent_dispatch_disabled(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-dispatch-disabled'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """version = 2
default_agents = ["agent1"]
layout = "agent1:codex"

[agents.agent1]
provider = "codex"
target = "."
workspace_mode = "inplace"
restore = "auto"
permission = "manual"
dispatch_disabled = true
""",
    )

    result = load_project_config(project_root)

    assert result.config.agents['agent1'].dispatch_disabled is True
    rendered = render_project_config_text(result.config)
    assert 'dispatch_disabled = true' in rendered


def test_load_project_config_resolves_role_store_from_account_home_inside_provider_home(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / 'repo-provider-home-role-store'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        '''
version = 2
entry_window = "main"

[windows]
main = "agentroles.mother:codex"
''',
    )
    account_home = tmp_path / 'account-home'
    provider_home = (
        project_root
        / '.ccb'
        / 'agents'
        / 'agent1'
        / 'provider-state'
        / 'codex'
        / 'home'
    )
    _write_installed_role(account_home / '.roles', 'agentroles.mother', default_agent_name='mother')
    monkeypatch.setenv('HOME', str(provider_home))
    monkeypatch.delenv('AGENT_ROLES_STORE', raising=False)
    monkeypatch.delenv('CCB_SOURCE_HOME', raising=False)
    if source_home_module.pwd is not None:
        monkeypatch.setattr(
            source_home_module.pwd,
            'getpwuid',
            lambda _uid: type('PwdEntry', (), {'pw_dir': str(account_home)})(),
        )

    loaded = load_project_config(project_root).config

    assert loaded.agents['mother'].role == 'agentroles.mother'
    assert loaded.windows[0].layout_spec == 'mother:codex'


def test_load_project_config_prefers_project_local_role_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / 'repo-project-local-role-store'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        '''
version = 2
entry_window = "main"

[windows]
main = "agentroles.ccb_frontdesk:codex"

[loop.capacity]
enabled = true
max_nodes = 1

[loop.role_profiles.ccb_frontdesk]
role = "agentroles.ccb_frontdesk"
provider = "codex"
workspace_mode = "inplace"
max_instances = 1
''',
    )
    _write_installed_role(
        project_root / 'roles',
        'agentroles.ccb_frontdesk',
        default_agent_name='frontdesk',
    )
    monkeypatch.delenv('AGENT_ROLES_STORE', raising=False)

    loaded = load_project_config(project_root).config

    assert loaded.agents['frontdesk'].role == 'agentroles.ccb_frontdesk'
    assert loaded.windows[0].layout_spec == 'frontdesk:codex'
    assert loaded.loop_capacity.role_profiles['ccb_frontdesk'].role == 'agentroles.ccb_frontdesk'


def test_load_project_config_role_missing_reports_resolved_store(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / 'repo-provider-home-missing-role'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        '''
version = 2
entry_window = "main"

[windows]
main = "agentroles.mother:codex"
''',
    )
    account_home = tmp_path / 'account-home'
    provider_home = (
        project_root
        / '.ccb'
        / 'agents'
        / 'agent1'
        / 'provider-state'
        / 'codex'
        / 'home'
    )
    monkeypatch.setenv('HOME', str(provider_home))
    monkeypatch.delenv('AGENT_ROLES_STORE', raising=False)
    monkeypatch.delenv('CCB_SOURCE_HOME', raising=False)
    if source_home_module.pwd is not None:
        monkeypatch.setattr(
            source_home_module.pwd,
            'getpwuid',
            lambda _uid: type('PwdEntry', (), {'pw_dir': str(account_home)})(),
        )

    with pytest.raises(ConfigValidationError) as exc_info:
        load_project_config(project_root)

    message = str(exc_info.value)
    assert 'role agentroles.mother is not installed in role store' in message
    assert str(account_home / '.roles' / 'installed') in message


def test_load_project_config_allows_multiple_agents_bound_to_same_role(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-role-multi-instance'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        '''
version = 2
entry_window = "main"

[windows]
main = "archi_review:codex, archi_qa:claude"

[agents.archi_review]
role = "agentroles.archi"

[agents.archi_qa]
role = "agentroles.archi"
''',
    )

    result = load_project_config(project_root)

    assert result.config.windows[0].agent_names == ('archi_review', 'archi_qa')
    assert result.config.agents['archi_review'].role == 'agentroles.archi'
    assert result.config.agents['archi_qa'].role == 'agentroles.archi'
    assert result.config.agents['archi_review'].provider == 'codex'
    assert result.config.agents['archi_qa'].provider == 'claude'


def test_load_project_config_accepts_kimi_and_deepseek_providers(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-native-providers'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(config_path, 'cmd; kimi_agent:kimi, deep_agent:deepseek\n')

    result = load_project_config(project_root)

    assert result.config.agents['kimi_agent'].provider == 'kimi'
    assert result.config.agents['deep_agent'].provider == 'deepseek'
    assert result.config.layout_spec == 'cmd; kimi_agent:kimi, deep_agent:deepseek'


def test_load_project_config_supports_maintenance_heartbeat_table(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-maintenance'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """demo:codex

[maintenance.heartbeat]
enabled = true
assessor = "SelfAgent"
interval_s = 1200
min_interval_s = 120
unknown_streak_cap = 4
escalation_policy = "ask_user"
startup_ensure = false
""",
    )

    result = load_project_config(project_root)
    heartbeat = result.config.maintenance_heartbeat

    assert heartbeat.enabled is True
    assert heartbeat.assessor == 'selfagent'
    assert heartbeat.interval_s == 1200
    assert heartbeat.min_interval_s == 120
    assert heartbeat.unknown_streak_cap == 4
    assert heartbeat.escalation_policy == 'ask_user'
    assert heartbeat.startup_ensure is False


def test_load_project_config_rejects_invalid_maintenance_heartbeat_values(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-maintenance-invalid'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """demo:codex

[maintenance.heartbeat]
enabled = true
interval_s = 30
min_interval_s = 60
""",
    )

    with pytest.raises(ConfigValidationError, match='min_interval_s cannot exceed interval_s'):
        load_project_config(project_root)


def test_load_project_config_rejects_provider_only_list(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(config_path, 'codex,claude,cmd\n')

    with pytest.raises(ConfigValidationError, match='expected'):
        load_project_config(project_root)


def test_load_project_config_supports_named_simple_agent_map(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(config_path, 'cmd, agent1:codex; agent2:codex, agent3:claude\n')

    result = load_project_config(project_root)

    assert result.source_path == config_path
    assert result.config.default_agents == ('agent1', 'agent2', 'agent3')
    assert set(result.config.agents) == {'agent1', 'agent2', 'agent3'}
    assert result.config.agents['agent1'].provider == 'codex'
    assert result.config.agents['agent2'].provider == 'codex'
    assert result.config.agents['agent3'].provider == 'claude'
    assert result.config.cmd_enabled is True
    assert result.config.layout_spec == 'cmd, agent1:codex; agent2:codex, agent3:claude'
    assert result.config.windows_explicit is False
    assert [window.name for window in result.config.windows] == ['main']
    assert result.config.windows[0].agent_names == ('agent1', 'agent2', 'agent3')


def test_load_project_config_normalizes_mixed_case_compact_agent_names(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-mixed-case'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        'cmd, Alice:codex; Tomy:codex, Hanmeimei:claude; Lilei:gemini, Harry:gemini\n',
    )

    result = load_project_config(project_root)

    assert result.config.default_agents == ('alice', 'tomy', 'hanmeimei', 'lilei', 'harry')
    assert set(result.config.agents) == {'alice', 'tomy', 'hanmeimei', 'lilei', 'harry'}
    assert result.config.layout_spec == (
        'cmd, alice:codex; tomy:codex, hanmeimei:claude; lilei:gemini, harry:gemini'
    )


def test_load_project_config_normalizes_mixed_case_windows_agent_names(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-windows-mixed-case'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        '\n'.join(
            [
                'version = 2',
                'entry_window = "main"',
                '',
                '[windows]',
                'main = "Main_Claude:claude; Reviewer:codex"',
                '',
                '[agents.Main_Claude]',
                'role = "agentroles.archi"',
                '',
                '[agents.Reviewer]',
                'role = "agentroles.coder"',
                '',
            ]
        ),
    )

    result = load_project_config(project_root)

    assert result.config.windows[0].agent_names == ('main_claude', 'reviewer')
    assert tuple(result.config.agents) == ('main_claude', 'reviewer')
    assert result.config.agents['main_claude'].provider == 'claude'
    assert result.config.agents['reviewer'].provider == 'codex'


def test_load_project_config_rejects_case_insensitive_duplicates(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(config_path, 'Agent1:codex,agent1:claude\n')
    with pytest.raises(ConfigValidationError):
        load_project_config(project_root)


def test_load_project_config_uses_builtin_default_when_project_config_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / 'repo'
    monkeypatch.setenv('HOME', str(tmp_path / 'empty-home'))
    monkeypatch.setattr(
        default_project.shutil,
        'which',
        lambda command: '/usr/bin/claude' if command == 'claude' else None,
    )
    config = build_default_project_config()
    assert config.default_agents == ('demo',)
    assert config.cmd_enabled is False
    assert config.windows_explicit is True
    assert config.entry_window == 'main'
    assert [window.name for window in config.windows] == ['main']
    assert config.tool_windows == ()
    loaded = load_project_config(project_root)
    assert loaded.source_path is None
    assert loaded.source_kind == CONFIG_SOURCE_BUILTIN_DEFAULT
    assert loaded.used_default is True
    assert loaded.config.default_agents == ('demo',)
    assert loaded.config.cmd_enabled is False
    assert loaded.config.windows_explicit is True
    assert loaded.config.entry_window == 'main'
    assert [window.name for window in loaded.config.windows] == ['main']
    assert loaded.config.tool_windows == ()
    assert set(loaded.config.agents) == {'demo'}
    assert loaded.config.agents['demo'].provider == 'claude'
    assert loaded.config.agents['demo'].role is None
    assert loaded.config.agents['demo'].workspace_mode is WorkspaceMode.INPLACE
    assert loaded.config.agents['demo'].runtime_mode is RuntimeMode.PANE_BACKED


@pytest.mark.parametrize(
    ('available', 'expected'),
    [
        ({'codex', 'claude'}, 'codex'),
        ({'claude'}, 'claude'),
        ({'grok'}, 'grok'),
        (set(), 'codex'),
    ],
)
def test_builtin_default_selects_first_available_provider(
    available: set[str],
    expected: str,
) -> None:
    config = build_default_project_config(
        which_fn=lambda command: f'/usr/bin/{command}' if command in available else None,
    )

    assert config.default_agents == ('demo',)
    assert config.agents['demo'].provider == expected
    assert config.windows[0].layout_spec == f'demo:{expected}'


def test_builtin_default_provider_priority_tracks_runtime_registry() -> None:
    from provider_command_defaults import SUPPORTED_PROVIDER_NAMES
    from provider_core.registry import CORE_PROVIDER_NAMES, OPTIONAL_PROVIDER_NAMES

    assert SUPPORTED_PROVIDER_NAMES == tuple(CORE_PROVIDER_NAMES + OPTIONAL_PROVIDER_NAMES)


def test_builtin_default_provider_detection_honors_start_command_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[str] = []
    monkeypatch.setenv('CODEX_START_CMD', '/opt/ccb/codex-wrapper --profile demo')

    config = build_default_project_config(
        which_fn=lambda command: observed.append(command) or '/opt/ccb/codex-wrapper',
    )

    assert observed == ['/opt/ccb/codex-wrapper']
    assert config.agents['demo'].provider == 'codex'


def test_render_default_project_config_text_omits_optional_tool_windows(tmp_path: Path) -> None:
    from agents.config_loader import render_default_project_config_text

    rendered = render_default_project_config_text(provider='codex')

    assert '[windows]' in rendered
    assert 'main = "demo:codex"' in rendered
    assert '[agents.demo]' not in rendered
    assert 'ccb_self' not in rendered
    assert '[tool_windows.' not in rendered
    assert '[ui.sidebar]' in rendered
    assert '[ui.sidebar.view]' not in rendered
    assert 'agents_height = "50%"' in rendered
    assert 'comms_height = "15%"' in rendered
    assert 'tips_height = "35%"' in rendered
    assert 'position = "left"' not in rendered
    config_path = tmp_path / 'repo-render-default' / '.ccb' / 'ccb.config'
    _write(config_path, rendered)
    loaded = load_project_config(config_path.parents[1]).config
    assert loaded.tool_windows == ()
    assert loaded.default_agents == ('demo',)
    assert loaded.agents['demo'].provider == 'codex'


def test_load_project_config_normalizes_legacy_ccb_self_role_alias(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-legacy-ccb-self-role'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """
version = 2
entry_window = "main"

[windows]
main = "ccb_self:codex"

[agents.ccb_self]
role = "agentrole.ccb_self"
""",
    )

    loaded = load_project_config(project_root).config

    assert loaded.agents['ccb_self'].role == 'agentroles.ccb_self'


def test_ensure_default_project_config_creates_anchor_without_writing_config(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'

    config_path = ensure_default_project_config(project_root)

    assert config_path == project_root.resolve() / '.ccb' / 'ccb.config'
    assert config_path.parent.is_dir()
    assert config_path.exists() is False


def test_ensure_bootstrap_project_config_allows_empty_anchor(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-empty-anchor'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)

    config_path = ensure_bootstrap_project_config(project_root)

    assert config_path == project_root.resolve() / '.ccb' / 'ccb.config'
    assert config_path.exists() is False


def test_ensure_bootstrap_project_config_allows_persisted_state_without_writing_config(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-missing-config-with-state'
    runtime_path = project_root / '.ccb' / 'agents' / 'demo' / 'runtime.json'
    _write(runtime_path, '{"agent_name":"demo"}\n')

    config_path = ensure_bootstrap_project_config(project_root)

    assert config_path.exists() is False


def test_load_project_config_supports_explicit_worktree_suffix_in_compact_config(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-worktree-compact'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(config_path, 'cmd; agent1:codex(worktree), agent2:claude\n')

    result = load_project_config(project_root)

    assert result.config.agents['agent1'].workspace_mode is WorkspaceMode.GIT_WORKTREE
    assert result.config.agents['agent2'].workspace_mode is WorkspaceMode.INPLACE
    assert result.config.layout_spec == 'cmd; agent1:codex(worktree), agent2:claude'


def test_ensure_bootstrap_project_config_ignores_session_residue_without_writing_config(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-session-residue'
    _write(project_root / '.ccb' / '.codex-agent1-session', '{}\n')
    _write(project_root / '.ccb' / '.claude-agent3-session', '{}\n')

    config_path = ensure_bootstrap_project_config(project_root)

    assert config_path.exists() is False


def test_load_project_config_rejects_invalid_token(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(config_path, 'demo\n')

    with pytest.raises(ConfigValidationError, match='expected'):
        load_project_config(project_root)


def test_reserved_agent_name_is_rejected(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(config_path, 'clear:codex\n')
    with pytest.raises(ConfigValidationError):
        load_project_config(project_root)


def test_cmd_only_config_is_rejected(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(config_path, 'cmd\n')
    with pytest.raises(ConfigValidationError, match='at least one agent'):
        load_project_config(project_root)


def test_cmd_cannot_be_used_as_agent_name(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(config_path, 'cmd:codex\n')
    with pytest.raises(ConfigValidationError, match='reserved token'):
        load_project_config(project_root)


def test_load_project_config_uses_user_default_when_project_config_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / 'home'
    user_default_config = home / '.ccb' / 'ccb.config'
    project_root = tmp_path / 'repo'
    project_root.mkdir()
    monkeypatch.setenv('HOME', str(home))
    _write(user_default_config, 'cmd; builder:codex, reviewer:claude; qa:gemini\n')

    result = load_project_config(project_root)

    assert result.source_path == user_default_config
    assert result.source_kind == CONFIG_SOURCE_USER
    assert result.used_default is False
    assert result.config.default_agents == ('builder', 'reviewer', 'qa')
    assert result.config.agents['builder'].provider == 'codex'
    assert result.config.agents['reviewer'].provider == 'claude'
    assert result.config.agents['qa'].provider == 'gemini'
    assert result.config.cmd_enabled is True


def test_load_project_config_prefers_project_config_over_user_default(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / 'home'
    project_root = tmp_path / 'repo'
    user_default_config = home / '.ccb' / 'ccb.config'
    project_config = project_root / '.ccb' / 'ccb.config'
    monkeypatch.setenv('HOME', str(home))
    _write(user_default_config, 'cmd; userdefault:claude\n')
    _write(project_config, 'cmd; projectagent:codex\n')

    result = load_project_config(project_root)

    assert result.source_path == project_config
    assert result.source_kind == CONFIG_SOURCE_PROJECT
    assert result.used_default is False
    assert result.config.default_agents == ('projectagent',)
    assert result.config.agents['projectagent'].provider == 'codex'


def test_load_project_config_reports_invalid_user_default_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    home = tmp_path / 'home'
    project_root = tmp_path / 'repo'
    user_default_config = home / '.ccb' / 'ccb.config'
    monkeypatch.setenv('HOME', str(home))
    _write(user_default_config, 'cmd:codex\n')

    with pytest.raises(ConfigValidationError) as exc_info:
        load_project_config(project_root)

    assert str(user_default_config) in str(exc_info.value)


def test_load_project_config_supports_toml_provider_profile(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """version = 2
default_agents = ["agent1"]
layout = "cmd; agent1"
cmd_enabled = true

[agents.agent1]
provider = "codex"
target = "."
workspace_mode = "git-worktree"
restore = "auto"
permission = "manual"

[agents.agent1.provider_profile]
mode = "isolated"
home = ".ccb/provider-profiles/agent1/codex"
inherited_skill_include = ["*"]
inherited_skill_exclude = ["trellis-*"]
inherit_api = false
inherit_auth = true
inherit_config = true
inherit_skills = false
inherit_commands = false
inherit_memory = false

[agents.agent1.provider_profile.env]
OPENAI_API_KEY = "sk-test"

[agents.agent1.provider_profile.mcp_servers.codegraph]
command = "/usr/local/bin/codegraph"
args = ["serve", "--mcp"]

[agents.agent1.provider_profile.mcp_servers.hindsight.env]
HINDSIGHT_AGENT_NAME = "agent1"

[agents.agent1.provider_profile.plugins."github@openai-curated"]
enabled = false

[agents.agent1.provider_profile.plugins."agentmemory@agentmemory"]
enabled = true

[agents.agent1.provider_profile.skill_overlays.n14_trellis]
source = "/root/.codex/skills"
include = ["trellis-*"]
exclude = ["trellis-meta"]
""",
    )

    result = load_project_config(project_root)
    spec = result.config.agents['agent1']

    assert spec.provider_profile.mode == 'isolated'
    assert spec.provider_profile.home == '.ccb/provider-profiles/agent1/codex'
    assert spec.provider_profile.inherit_api is False
    assert spec.provider_profile.inherit_auth is True
    assert spec.provider_profile.inherit_skills is False
    assert spec.provider_profile.inherit_commands is False
    assert spec.provider_profile.inherit_memory is False
    assert spec.provider_profile.inherited_skill_include == ('*',)
    assert spec.provider_profile.inherited_skill_exclude == ('trellis-*',)
    assert spec.provider_profile.env == {'OPENAI_API_KEY': 'sk-test'}
    assert spec.provider_profile.mcp_servers == {
        'codegraph': {'command': '/usr/local/bin/codegraph', 'args': ['serve', '--mcp']},
        'hindsight': {'env': {'HINDSIGHT_AGENT_NAME': 'agent1'}},
    }
    assert spec.provider_profile.plugins == {
        'github@openai-curated': {'enabled': False},
        'agentmemory@agentmemory': {'enabled': True},
    }
    overlay = spec.provider_profile.skill_overlays['n14_trellis']
    assert overlay.source == '/root/.codex/skills'
    assert overlay.include == ('trellis-*',)
    assert overlay.exclude == ('trellis-meta',)


@pytest.mark.parametrize('location', ['agent', 'provider_profile_env'])
def test_load_project_config_supports_codex_model_catalog_json(tmp_path: Path, location: str) -> None:
    project_root = tmp_path / f'repo-{location}'
    config_path = project_root / '.ccb' / 'ccb.config'
    extra = (
        'model_catalog_json = "model.json"\n'
        if location == 'agent'
        else '[agents.agent1.provider_profile.env]\nmodel_catalog_json = "model.json"\n'
    )
    _write(
        config_path,
        f"""version = 2
default_agents = ["agent1"]
layout = "cmd; agent1"
cmd_enabled = true

[agents.agent1]
provider = "codex"
target = "."
workspace_mode = "git-worktree"
restore = "auto"
permission = "manual"
{extra}""",
    )

    spec = load_project_config(project_root).config.agents['agent1']

    assert spec.provider_profile.env['model_catalog_json'] == 'model.json'


def test_load_project_config_supports_workspace_path_and_group_fields(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    config_path = project_root / '.ccb' / 'ccb.config'
    external = tmp_path / 'manual-worktree'
    _write(
        config_path,
        f"""version = 2
default_agents = ["agent1", "agent2"]

[agents.agent1]
provider = "codex"
target = "."
workspace_mode = "git-worktree"
workspace_path = "{external}"
restore = "auto"
permission = "manual"

[agents.agent2]
provider = "claude"
target = "."
workspace_mode = "git-worktree"
workspace_group = "main"
restore = "auto"
permission = "manual"
""",
    )

    result = load_project_config(project_root)

    assert result.config.agents['agent1'].workspace_path == str(external)
    assert result.config.agents['agent1'].workspace_group is None
    assert result.config.agents['agent2'].workspace_path is None
    assert result.config.agents['agent2'].workspace_group == 'main'


def test_load_project_config_supports_provider_command_template(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """version = 2
default_agents = ["agent1"]

[agents.agent1]
provider = "codex"
target = "."
workspace_mode = "git-worktree"
restore = "auto"
permission = "manual"
provider_command_template = "sandbox=1 {command} omx --madmax"
""",
    )

    result = load_project_config(project_root)
    spec = result.config.agents['agent1']
    rendered = render_project_config_text(result.config)

    assert spec.provider_command_template == 'sandbox=1 {command} omx --madmax'
    assert 'provider_command_template = "sandbox=1 {command} omx --madmax"' in rendered


@pytest.mark.parametrize(
    'template',
    [
        'sandbox=1 codex omx --madmax',
        'sandbox=1 {command} {command}',
    ],
)
def test_load_project_config_rejects_invalid_provider_command_template(
    tmp_path: Path,
    template: str,
) -> None:
    project_root = tmp_path / 'repo'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        f"""version = 2
default_agents = ["agent1"]

[agents.agent1]
provider = "codex"
target = "."
workspace_mode = "git-worktree"
restore = "auto"
permission = "manual"
provider_command_template = "{template}"
""",
    )

    with pytest.raises(ConfigValidationError, match='provider_command_template must contain exactly one'):
        load_project_config(project_root)


def test_load_project_config_rejects_workspace_path_and_group_together(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """version = 2
default_agents = ["agent1"]

[agents.agent1]
provider = "codex"
target = "."
workspace_mode = "git-worktree"
workspace_path = "/tmp/manual"
workspace_group = "main"
restore = "auto"
permission = "manual"
""",
    )

    with pytest.raises(ConfigValidationError, match='workspace_path and workspace_group are mutually exclusive'):
        load_project_config(project_root)


def test_load_project_config_rejects_workspace_group_without_git_worktree_mode(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """version = 2
default_agents = ["agent1"]

[agents.agent1]
provider = "codex"
target = "."
workspace_mode = "inplace"
workspace_group = "main"
restore = "auto"
permission = "manual"
""",
    )

    with pytest.raises(ConfigValidationError, match='workspace_path and workspace_group require workspace_mode'):
        load_project_config(project_root)


@pytest.mark.parametrize('provider', ['claude', 'gemini'])
def test_load_project_config_rejects_non_codex_provider_profile_home(tmp_path: Path, provider: str) -> None:
    project_root = tmp_path / 'repo'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        f"""version = 2
default_agents = ["agent1"]

[agents.agent1]
provider = "{provider}"
target = "."
workspace_mode = "inplace"
restore = "auto"
permission = "manual"

[agents.agent1.provider_profile]
mode = "isolated"
home = ".ccb/provider-profiles/agent1/{provider}"
""",
    )

    with pytest.raises(ConfigValidationError, match='provider_profile\\.home is supported only for codex'):
        load_project_config(project_root)


@pytest.mark.parametrize(
    ('provider', 'api_block', 'expected_key', 'expected_url', 'expected_env', 'expected_inherit_config'),
    [
        (
            'codex',
            'key = "sk-test"\nurl = "https://openai.example.test/v1"\n',
            'sk-test',
            'https://openai.example.test/v1',
            {
                'OPENAI_API_KEY': 'sk-test',
                'OPENAI_BASE_URL': 'https://openai.example.test/v1',
            },
            False,
        ),
        (
            'claude',
            'key = "claude-key"\nurl = "https://claude.example.test"\n',
            'claude-key',
            'https://claude.example.test',
            {
                'ANTHROPIC_API_KEY': 'claude-key',
                'ANTHROPIC_BASE_URL': 'https://claude.example.test',
            },
            True,
        ),
        (
            'gemini',
            'key = "gemini-key"\nurl = "https://gemini.example.test"\n',
            'gemini-key',
            'https://gemini.example.test',
            {
                'GEMINI_API_KEY': 'gemini-key',
                'GOOGLE_GEMINI_BASE_URL': 'https://gemini.example.test',
            },
            True,
        ),
        (
            'deepseek',
            'key = "deepseek-key"\nurl = "https://api.deepseek.com"\n',
            'deepseek-key',
            'https://api.deepseek.com',
            {
                'DEEPCODE_API_KEY': 'deepseek-key',
                'DEEPCODE_BASE_URL': 'https://api.deepseek.com',
            },
            True,
        ),
        (
            'dsh',
            'key = "deepseek-key"\nurl = "https://api.deepseek.com"\n',
            'deepseek-key',
            'https://api.deepseek.com',
            {
                'DEEPSEEK_API_KEY': 'deepseek-key',
                'DEEPSEEK_BASE_URL': 'https://api.deepseek.com',
            },
            True,
        ),
    ],
)
def test_load_project_config_supports_toml_agent_api_shortcut(
    tmp_path: Path,
    provider: str,
    api_block: str,
    expected_key: str,
    expected_url: str,
    expected_env: dict[str, str],
    expected_inherit_config: bool,
) -> None:
    project_root = tmp_path / f'repo-{provider}-api'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        f"""version = 2
default_agents = ["agent1"]
layout = "cmd; agent1"
cmd_enabled = true

[agents.agent1]
provider = "{provider}"
target = "."
workspace_mode = "git-worktree"
restore = "auto"
permission = "manual"

{api_block}""",
    )

    result = load_project_config(project_root)
    spec = result.config.agents['agent1']

    assert spec.api.key == expected_key
    assert spec.api.url == expected_url
    assert spec.provider_profile.inherit_api is False
    assert spec.provider_profile.inherit_auth is False
    assert spec.provider_profile.inherit_config is expected_inherit_config
    assert spec.provider_profile.env == expected_env


def test_load_project_config_supports_legacy_nested_agent_api_shortcut(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-legacy-nested-api'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """version = 2
default_agents = ["agent1"]
layout = "cmd; agent1"
cmd_enabled = true

[agents.agent1]
provider = "codex"
target = "."
workspace_mode = "git-worktree"
restore = "auto"
permission = "manual"

[agents.agent1.api]
key = "sk-legacy"
url = "https://legacy.example.test/v1"
""",
    )

    result = load_project_config(project_root)
    spec = result.config.agents['agent1']

    assert spec.api.key == 'sk-legacy'
    assert spec.api.url == 'https://legacy.example.test/v1'
    assert spec.provider_profile.inherit_api is False
    assert spec.provider_profile.env == {
        'OPENAI_API_KEY': 'sk-legacy',
        'OPENAI_BASE_URL': 'https://legacy.example.test/v1',
    }
    assert spec.provider_profile.inherit_config is False
    assert spec.provider_profile.inherit_auth is False


def test_load_project_config_codex_api_shortcut_disables_conflicting_global_projection(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-codex-shortcut-flags'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """version = 2
default_agents = ["agent1"]
layout = "cmd; agent1"
cmd_enabled = true

[agents.agent1]
provider = "codex"
target = "."
workspace_mode = "git-worktree"
restore = "auto"
permission = "manual"
key = "sk-shortcut"
url = "https://api.example.test/v1"
""",
    )

    result = load_project_config(project_root)
    spec = result.config.agents['agent1']

    assert spec.provider_profile.inherit_api is False
    assert spec.provider_profile.inherit_config is False
    assert spec.provider_profile.inherit_auth is False


def test_load_project_config_supports_uppercase_agent_api_keys(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-uppercase-api'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """version = 2
default_agents = ["agent1"]
layout = "cmd; agent1"
cmd_enabled = true

[agents.agent1]
provider = "codex"
target = "."
workspace_mode = "git-worktree"
restore = "auto"
permission = "manual"

[agents.agent1.api]
KEY = "sk-upper"
URL = "https://upper.example.test/v1"
""",
    )

    result = load_project_config(project_root)
    spec = result.config.agents['agent1']

    assert spec.api.key == 'sk-upper'
    assert spec.api.url == 'https://upper.example.test/v1'
    assert spec.provider_profile.env == {
        'OPENAI_API_KEY': 'sk-upper',
        'OPENAI_BASE_URL': 'https://upper.example.test/v1',
    }


def test_load_project_config_normalizes_bare_codex_api_origin_to_v1_env(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-codex-origin-api'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """version = 2
default_agents = ["agent1"]
layout = "cmd; agent1"
cmd_enabled = true

[agents.agent1]
provider = "codex"
target = "."
workspace_mode = "git-worktree"
restore = "auto"
permission = "manual"
key = "sk-origin"
url = "https://api.example.test"
""",
    )

    result = load_project_config(project_root)
    spec = result.config.agents['agent1']

    assert spec.api.key == 'sk-origin'
    assert spec.api.url == 'https://api.example.test'
    assert spec.provider_profile.env == {
        'OPENAI_API_KEY': 'sk-origin',
        'OPENAI_BASE_URL': 'https://api.example.test/v1',
    }


def test_load_project_config_supports_compact_header_with_agent_api_overlay(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-hybrid-api'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """cmd, agent1:codex; agent2:claude

[agents.agent1]
key = "sk-hybrid"
url = "https://api.example.test/v1"
""",
    )

    result = load_project_config(project_root)

    assert result.config.layout_spec == 'cmd, agent1:codex; agent2:claude'
    assert result.config.default_agents == ('agent1', 'agent2')
    assert result.config.agents['agent1'].api == AgentApiSpec(
        key='sk-hybrid',
        url='https://api.example.test/v1',
    )
    assert result.config.agents['agent2'].provider == 'claude'


def test_load_project_config_rejects_mixed_flat_and_nested_agent_api_shortcuts(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-mixed-api-shortcuts'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """cmd; agent1:codex

[agents.agent1]
key = "sk-flat"

[agents.agent1.api]
url = "https://api.example.test/v1"
""",
    )

    with pytest.raises(ConfigValidationError, match='key/url cannot be combined with agents\\.agent1\\.api'):
        load_project_config(project_root)


@pytest.mark.parametrize(
    ('provider', 'model_name', 'expected_startup_args'),
    [
        ('codex', 'gpt-5', ('-m', 'gpt-5')),
        ('claude', 'opus', ('--model', 'opus')),
        ('gemini', 'gemini-2.5-pro', ('-m', 'gemini-2.5-pro')),
        ('opencode', 'openai/gpt-5', ('-m', 'openai/gpt-5')),
        ('dsh', 'deepseek-v4-flash', ()),
    ],
)
def test_load_project_config_supports_agent_model_shortcut(
    tmp_path: Path,
    provider: str,
    model_name: str,
    expected_startup_args: tuple[str, ...],
) -> None:
    project_root = tmp_path / f'repo-{provider}-model'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        f"""cmd; agent1:{provider}

[agents.agent1]
model = "{model_name}"
""",
    )

    result = load_project_config(project_root)
    spec = result.config.agents['agent1']

    assert spec.model == model_name
    assert spec.startup_args == expected_startup_args


def test_load_project_config_supports_agent_model_shortcut_with_extra_startup_args(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-model-extra-startup-args'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """cmd; agent1:codex

[agents.agent1]
model = "gpt-5"
startup_args = ["--search"]
""",
    )

    result = load_project_config(project_root)
    spec = result.config.agents['agent1']

    assert spec.model == 'gpt-5'
    assert spec.startup_args == ('-m', 'gpt-5', '--search')


@pytest.mark.parametrize(
    ('provider', 'model_name', 'thinking', 'expected_startup_args'),
    [
        (
            'codex',
            'gpt-5.5',
            'high',
            ('-m', 'gpt-5.5', '-c', 'model_reasoning_effort="high"'),
        ),
        ('deepseek', 'deepseek-v4-pro', 'max', ()),
        ('deepseek', 'deepseek-v4-flash', 'off', ()),
        ('dsh', 'deepseek-v4-flash', 'high', ()),
        ('dsh', 'deepseek-v4-pro', 'max', ()),
    ],
)
def test_load_project_config_supports_static_agent_thinking_shortcut(
    tmp_path: Path,
    provider: str,
    model_name: str,
    thinking: str,
    expected_startup_args: tuple[str, ...],
) -> None:
    project_root = tmp_path / f'repo-{provider}-thinking'
    _write(
        project_root / '.ccb' / 'ccb.config',
        f'''cmd; agent1:{provider}

[agents.agent1]
model = "{model_name}"
thinking = "{thinking}"
''',
    )

    spec = load_project_config(project_root).config.agents['agent1']

    assert spec.model == model_name
    assert spec.thinking == thinking
    assert spec.startup_args == expected_startup_args


def test_load_project_config_supports_static_thinking_with_inherited_model(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-thinking-without-model'
    _write(
        project_root / '.ccb' / 'ccb.config',
        '''cmd; agent1:codex

[agents.agent1]
thinking = "high"
''',
    )

    spec = load_project_config(project_root).config.agents['agent1']

    assert spec.model is None
    assert spec.thinking == 'high'
    assert spec.startup_args == ('-c', 'model_reasoning_effort="high"')


def test_load_project_config_rejects_static_thinking_startup_arg_conflict(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-thinking-startup-conflict'
    _write(
        project_root / '.ccb' / 'ccb.config',
        '''cmd; agent1:codex

[agents.agent1]
model = "gpt-5.5"
thinking = "high"
startup_args = ["-c", "model_reasoning_effort=low"]
''',
    )

    with pytest.raises(ConfigValidationError, match='thinking cannot be combined with startup_args'):
        load_project_config(project_root)


def test_load_project_config_rejects_deepseek_structured_env_conflict(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-deepseek-env-conflict'
    _write(
        project_root / '.ccb' / 'ccb.config',
        '''cmd; agent1:deepseek

[agents.agent1]
model = "deepseek-v4-pro"
thinking = "max"
env = { DEEPCODE_MODEL = "deepseek-v4-flash" }
''',
    )

    with pytest.raises(ConfigValidationError, match='model/thinking cannot be combined with env runtime overrides'):
        load_project_config(project_root)


def test_load_project_config_rejects_agent_model_shortcut_for_unsupported_provider(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-model-unsupported-provider'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """cmd; agent1:droid

[agents.agent1]
model = "droid-pro"
""",
    )

    with pytest.raises(ConfigValidationError, match='model shortcut is supported only for providers'):
        load_project_config(project_root)


def test_load_project_config_rejects_agent_model_shortcut_mixed_with_startup_arg_model_flag(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-model-startup-conflict'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """cmd; agent1:codex

[agents.agent1]
model = "gpt-5"
startup_args = ["--model", "gpt-4.1"]
""",
    )

    with pytest.raises(ConfigValidationError, match='model cannot be combined with startup_args model flags'):
        load_project_config(project_root)


def test_load_project_config_supports_loop_role_profiles(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / 'repo-loop-profiles'
    config_path = project_root / '.ccb' / 'ccb.config'
    role_store = tmp_path / 'roles'
    _write_installed_role(role_store, 'agentroles.coder', default_agent_name='coder')
    _write_installed_role(role_store, 'agentroles.code_reviewer', default_agent_name='code_reviewer')
    monkeypatch.setenv('AGENT_ROLES_STORE', str(role_store))
    _write(
        config_path,
        """cmd; orchestrator:codex

[loop.capacity]
enabled = true
max_nodes = 2
default_lifetime = "current_round"
name_template = "loop-{loop_id}-{profile}-{index}"
reuse = "prefer_idle"

[loop.role_profiles.worker]
role = "agentroles.coder"
provider = "codex"
model = "gpt-5"
thinking = "high"
workspace_mode = "git-worktree"
workspace_group = "worker_pool"
max_instances = 1
reuse = "prefer_idle"

[loop.role_profiles.code_reviewer]
role = "agentroles.code_reviewer"
provider = "codex"
model = "gpt-5"
thinking = "medium"
workspace_mode = "git-worktree"
max_instances = 1
""",
    )

    result = load_project_config(project_root)
    capacity = result.config.loop_capacity
    rendered = render_project_config_text(result.config)

    assert capacity.enabled is True
    assert capacity.max_nodes == 2
    assert capacity.default_lifetime == 'current_round'
    assert tuple(capacity.role_profiles) == ('worker', 'code_reviewer')
    worker = capacity.role_profiles['worker']
    assert worker.role == 'agentroles.coder'
    assert worker.provider == 'codex'
    assert worker.model == 'gpt-5'
    assert worker.thinking == 'high'
    assert worker.workspace_mode is WorkspaceMode.GIT_WORKTREE
    assert worker.workspace_group == 'worker_pool'
    assert worker.max_instances == 1
    assert '[loop.capacity]' in rendered
    assert '[loop.role_profiles.worker]' in rendered
    assert 'role = "agentroles.coder"' in rendered
    assert 'thinking = "high"' in rendered


def test_load_project_config_rejects_unknown_loop_profile_field(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / 'repo-loop-unknown-field'
    config_path = project_root / '.ccb' / 'ccb.config'
    role_store = tmp_path / 'roles'
    _write_installed_role(role_store, 'agentroles.coder', default_agent_name='coder')
    monkeypatch.setenv('AGENT_ROLES_STORE', str(role_store))
    _write(
        config_path,
        """cmd; orchestrator:codex

[loop.role_profiles.worker]
role = "agentroles.coder"
provider = "codex"
max_instances = 1
provider_model = "gpt-5"
""",
    )

    with pytest.raises(ConfigValidationError, match='loop\\.role_profiles\\.worker contains unknown fields: provider_model'):
        load_project_config(project_root)


def test_load_project_config_rejects_missing_loop_profile_role(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / 'repo-loop-missing-role'
    config_path = project_root / '.ccb' / 'ccb.config'
    role_store = tmp_path / 'roles'
    monkeypatch.setenv('AGENT_ROLES_STORE', str(role_store))
    _write(
        config_path,
        """cmd; orchestrator:codex

[loop.role_profiles.worker]
role = "agentroles.coder"
provider = "codex"
max_instances = 1
""",
    )

    with pytest.raises(ConfigValidationError, match='role agentroles\\.coder is not installed in role store'):
        load_project_config(project_root)


def test_load_project_config_rejects_loop_capacity_exceeding_profile_limits(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / 'repo-loop-max-nodes'
    config_path = project_root / '.ccb' / 'ccb.config'
    role_store = tmp_path / 'roles'
    _write_installed_role(role_store, 'agentroles.coder', default_agent_name='coder')
    monkeypatch.setenv('AGENT_ROLES_STORE', str(role_store))
    _write(
        config_path,
        """cmd; orchestrator:codex

[loop.capacity]
enabled = true
max_nodes = 2

[loop.role_profiles.worker]
role = "agentroles.coder"
provider = "codex"
max_instances = 1
""",
    )

    with pytest.raises(ConfigValidationError, match='max_nodes cannot exceed total loop\\.role_profiles max_instances'):
        load_project_config(project_root)


def test_load_project_config_rejects_loop_profile_model_startup_conflict(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / 'repo-loop-model-conflict'
    config_path = project_root / '.ccb' / 'ccb.config'
    role_store = tmp_path / 'roles'
    _write_installed_role(role_store, 'agentroles.coder', default_agent_name='coder')
    monkeypatch.setenv('AGENT_ROLES_STORE', str(role_store))
    _write(
        config_path,
        """cmd; orchestrator:codex

[loop.role_profiles.worker]
role = "agentroles.coder"
provider = "codex"
model = "gpt-5"
startup_args = ["--model", "gpt-4.1"]
max_instances = 1
""",
    )

    with pytest.raises(ConfigValidationError, match='model cannot be combined with startup_args model flags'):
        load_project_config(project_root)


def test_load_project_config_rejects_hybrid_overlay_redefining_compact_provider(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-hybrid-provider-conflict'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """cmd; agent1:codex

[agents.agent1]
provider = "claude"
""",
    )

    with pytest.raises(ConfigValidationError, match='cannot redefine compact-header fields'):
        load_project_config(project_root)


def test_load_project_config_rejects_hybrid_overlay_for_unknown_agent(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-hybrid-unknown-agent'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """cmd; agent1:codex

[agents.agent2]
key = "sk-extra"
""",
    )

    with pytest.raises(ConfigValidationError, match="cannot define agent 'agent2' outside the compact layout"):
        load_project_config(project_root)


def test_load_project_config_rejects_hybrid_overlay_top_level_fields(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-hybrid-top-level'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """cmd; agent1:codex

version = 2
""",
    )

    with pytest.raises(ConfigValidationError, match='hybrid overlay contains unsupported top-level fields: version'):
        load_project_config(project_root)


def test_load_project_config_rejects_agent_api_shortcut_mixed_with_agent_env(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-agent-api-env-conflict'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """version = 2
default_agents = ["agent1"]
layout = "cmd; agent1"
cmd_enabled = true

[agents.agent1]
provider = "codex"
target = "."
workspace_mode = "git-worktree"
restore = "auto"
permission = "manual"
key = "sk-shortcut"

[agents.agent1.env]
OPENAI_API_KEY = "sk-conflict"
""",
    )

    with pytest.raises(ConfigValidationError, match='key/url cannot be mixed with provider API env in agents\\.agent1\\.env'):
        load_project_config(project_root)


def test_load_project_config_rejects_agent_api_shortcut_mixed_with_provider_profile_env(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-provider-api-env-conflict'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """version = 2
default_agents = ["agent1"]
layout = "cmd; agent1"
cmd_enabled = true

[agents.agent1]
provider = "codex"
target = "."
workspace_mode = "git-worktree"
restore = "auto"
permission = "manual"
key = "sk-shortcut"

[agents.agent1.provider_profile.env]
OPENAI_API_KEY = "sk-conflict"
""",
    )

    with pytest.raises(
        ConfigValidationError,
        match='key/url cannot be mixed with provider API env in agents\\.agent1\\.provider_profile\\.env',
    ):
        load_project_config(project_root)


def test_load_project_config_rejects_agent_api_shortcut_with_explicit_inherit_api_true(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-provider-inherit-api-conflict'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """version = 2
default_agents = ["agent1"]
layout = "cmd; agent1"
cmd_enabled = true

[agents.agent1]
provider = "codex"
target = "."
workspace_mode = "git-worktree"
restore = "auto"
permission = "manual"
key = "sk-shortcut"

[agents.agent1.provider_profile]
inherit_api = true
""",
    )

    with pytest.raises(
        ConfigValidationError,
        match='key/url cannot be combined with agents\\.agent1\\.provider_profile\\.inherit_api = true',
    ):
        load_project_config(project_root)


def test_load_project_config_rejects_codex_api_shortcut_with_explicit_inherit_config_true(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-provider-inherit-config-conflict'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """version = 2
default_agents = ["agent1"]
layout = "cmd; agent1"
cmd_enabled = true

[agents.agent1]
provider = "codex"
target = "."
workspace_mode = "git-worktree"
restore = "auto"
permission = "manual"
url = "https://api.example.test/v1"

[agents.agent1.provider_profile]
inherit_config = true
""",
    )

    with pytest.raises(
        ConfigValidationError,
        match='key/url cannot be combined with agents\\.agent1\\.provider_profile\\.inherit_config = true for codex',
    ):
        load_project_config(project_root)


@pytest.mark.parametrize('provider,key_field', [('codex', 'sk-shortcut'), ('claude', 'claude-key'), ('gemini', 'gemini-key')])
def test_load_project_config_rejects_agent_api_shortcut_with_explicit_inherit_auth_true(
    tmp_path: Path,
    provider: str,
    key_field: str,
) -> None:
    project_root = tmp_path / f'repo-provider-inherit-auth-conflict-{provider}'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        f"""version = 2
default_agents = ["agent1"]
layout = "cmd; agent1"
cmd_enabled = true

[agents.agent1]
provider = "{provider}"
target = "."
workspace_mode = "git-worktree"
restore = "auto"
permission = "manual"
key = "{key_field}"

[agents.agent1.provider_profile]
inherit_auth = true
""",
    )

    with pytest.raises(
        ConfigValidationError,
        match='key/url cannot be combined with agents\\.agent1\\.provider_profile\\.inherit_auth = true',
    ):
        load_project_config(project_root)


def test_load_project_config_supports_windows_topology_without_default_agents(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-windows-topology'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """version = 2
entry_window = "main"

[windows]
main = "agent1:codex"
review = "agent2:codex, agent3:claude"

[ui.sidebar]
mode = "every_window"
width = "15%"
bottom_height = 20
""",
    )

    result = load_project_config(project_root)

    assert result.config.default_agents == ('agent1', 'agent2', 'agent3')
    assert result.config.entry_window == 'main'
    assert [window.name for window in result.config.windows] == ['main', 'review']
    assert result.config.windows[0].agent_names == ('agent1',)
    assert result.config.windows[1].agent_names == ('agent2', 'agent3')
    assert result.config.agents['agent1'].provider == 'codex'
    assert result.config.agents['agent2'].provider == 'codex'
    assert result.config.agents['agent3'].provider == 'claude'
    assert result.config.sidebar.mode == 'every_window'
    assert result.config.sidebar.width == '15%'
    assert result.config.sidebar.bottom_height == 20
    assert result.config.sidebar.position == 'left'
    assert result.config.sidebar_view.agents_height == '50%'
    assert result.config.sidebar_view.comms_height == '15%'
    assert result.config.sidebar_view.tips_height == '35%'
    assert result.config.sidebar_view.comms_limit == 5
    assert result.config.sidebar_view.tips[0] == 'C-b d  detach'
    assert 'C-b h/j/k/l pane' in result.config.sidebar_view.tips
    assert 'copy: y yank' in result.config.sidebar_view.tips


def test_load_project_config_supports_right_sidebar_position(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-right-sidebar'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """version = 2
entry_window = "main"

[windows]
main = "agent1:codex"

[ui.sidebar]
position = "right"
""",
    )

    loaded = load_project_config(project_root).config
    rendered = render_project_config_text(loaded)

    assert loaded.sidebar.position == 'right'
    assert 'position = "right"' in rendered
    assert '[ui.sidebar.view]' not in rendered
    assert 'mode = "every_window"' not in rendered
    assert 'width = "15%"' not in rendered
    assert 'bottom_height = 20' not in rendered


def test_load_project_config_rejects_invalid_sidebar_position(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-invalid-sidebar-position'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """version = 2
entry_window = "main"

[windows]
main = "agent1:codex"

[ui.sidebar]
position = "bottom"
""",
    )

    with pytest.raises(ConfigValidationError, match='ui\\.sidebar\\.position must be left or right'):
        load_project_config(project_root)


def test_load_project_config_supports_inline_sidebar_view_options(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-inline-sidebar-view'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """version = 2
entry_window = "main"

[windows]
main = "agent1:codex"

[ui.sidebar]
agents_height = "40%"
comms_height = "15%"
tips_height = "45%"
comms_limit = 4
comms_compact = true
tips_enabled = true
tips = ["C-b d detach", "C-b z zoom"]
""",
    )

    loaded = load_project_config(project_root).config
    rendered = render_project_config_text(loaded)

    assert loaded.sidebar_view.agents_height == '40%'
    assert loaded.sidebar_view.comms_height == '15%'
    assert loaded.sidebar_view.tips_height == '45%'
    assert loaded.sidebar_view.comms_limit == 4
    assert loaded.sidebar_view.comms_compact is True
    assert loaded.sidebar_view.tips_enabled is True
    assert loaded.sidebar_view.tips == ('C-b d detach', 'C-b z zoom')
    assert '[ui.sidebar]' in rendered
    assert '[ui.sidebar.view]' not in rendered
    assert 'agents_height = "40%"' in rendered
    assert 'comms_height = "15%"' in rendered
    assert 'tips_height = "45%"' in rendered


def test_load_project_config_supports_sidebar_view_options_without_topology_signature_drift(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-sidebar-view'
    config_path = project_root / '.ccb' / 'ccb.config'
    base = """version = 2
entry_window = "main"

[windows]
main = "agent1:codex"

[ui.sidebar]
mode = "every_window"
width = "15%"
bottom_height = 20
"""
    _write(
        config_path,
        base
        + """
[ui.sidebar.view]
agents_height = "40%"
comms_height = "15%"
tips_height = "45%"
comms_limit = 4
comms_compact = true
tips_enabled = true
tips = ["C-b d detach", "C-b z zoom"]
""",
    )

    configured = load_project_config(project_root).config

    assert configured.sidebar_view.agents_height == '40%'
    assert configured.sidebar_view.comms_height == '15%'
    assert configured.sidebar_view.tips_height == '45%'
    assert configured.sidebar_view.comms_limit == 4
    assert configured.sidebar_view.comms_compact is True
    assert configured.sidebar_view.tips_enabled is True
    assert configured.sidebar_view.tips == ('C-b d detach', 'C-b z zoom')

    configured_signature = configured.topology_signature
    configured_identity = project_config_identity_payload(configured)['config_signature']
    _write(
        config_path,
        base
        + """
[ui.sidebar.view]
agents_height = "35%"
comms_height = "10%"
tips_height = "55%"
comms_limit = 5
tips = ["C-b c new win"]
""",
    )
    changed_view = load_project_config(project_root).config

    assert changed_view.topology_signature == configured_signature
    assert project_config_identity_payload(changed_view)['config_signature'] == configured_identity


def test_load_project_config_supports_windows_topology_agent_overrides(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-windows-agent-overrides'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """version = 2

[windows]
main = "agent1:codex"

[agents.agent1]
provider = "codex"
target = "."
workspace_mode = "git-worktree"
restore = "fresh"
permission = "auto"
model = "gpt-5"
""",
    )

    result = load_project_config(project_root)
    spec = result.config.agents['agent1']

    assert result.config.default_agents == ('agent1',)
    assert spec.workspace_mode is WorkspaceMode.GIT_WORKTREE
    assert spec.restore_default is RestoreMode.FRESH
    assert spec.permission_default is PermissionMode.AUTO
    assert spec.model == 'gpt-5'


def test_load_project_config_supports_managed_tool_windows(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-tool-windows'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """version = 2
entry_window = "files"

[windows]
main = "agent1:codex"

[tool_windows.files]
command = "ccb-workbench files"
label = "files"
show_in_sidebar = true
""",
    )

    result = load_project_config(project_root)

    assert result.config.default_agents == ('agent1',)
    assert set(result.config.agents) == {'agent1'}
    assert result.config.entry_window == 'files'
    assert [window.name for window in result.config.windows] == ['main']
    assert len(result.config.tool_windows) == 1
    tool = result.config.tool_windows[0]
    assert tool.name == 'files'
    assert tool.order == 0
    assert tool.command == 'ccb-workbench files'
    assert tool.label == 'files'
    assert tool.show_in_sidebar is True
    record = result.config.to_record()
    assert record['tool_windows'] == [
        {
            'name': 'files',
            'order': 0,
            'command': 'ccb-workbench files',
            'label': 'files',
            'show_in_sidebar': True,
        }
    ]
    assert 'files' not in project_config_identity_payload(result.config)['known_agents']


@pytest.mark.parametrize(
    ('tool_name', 'command'),
    [
        ('neovim', 'ccb-workbench files'),
        ('nvim', 'ccb-workbench files'),
        ('files', 'ccb-nvim'),
    ],
)
def test_load_project_config_rejects_legacy_neovim_tool_windows(
    tmp_path: Path,
    tool_name: str,
    command: str,
) -> None:
    project_root = tmp_path / f'repo-legacy-tool-{tool_name}'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        f"""version = 2

[windows]
main = "agent1:codex"

[tool_windows.{tool_name}]
command = "{command}"
""",
    )

    with pytest.raises(ConfigValidationError, match='managed Neovim tool windows are no longer supported'):
        load_project_config(project_root)


def test_load_project_config_supports_rich_layout_alias_without_agent_runtime(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-rich-layout-alias'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """version = 2

[windows]
main = "agent1:codex, rich"
rich_page = "rich"
""",
    )

    result = load_project_config(project_root)

    assert set(result.config.agents) == {'agent1'}
    assert result.config.default_agents == ('agent1',)
    assert [window.name for window in result.config.windows] == ['main', 'rich_page']
    assert result.config.windows[0].agent_names == ('agent1',)
    assert result.config.windows[0].tool_names == ('rich',)
    assert result.config.windows[1].agent_names == ()
    assert result.config.windows[1].tool_names == ('rich',)
    assert 'rich' not in result.config.agents
    assert 'rich' not in project_config_identity_payload(result.config)['known_agents']
    assert result.config.topology_signature_payload['windows'][0]['tools'] == ['rich']


def test_load_project_config_rejects_rich_alias_with_provider(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-rich-layout-invalid'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """version = 2

[windows]
main = "agent1:codex, rich:codex"
""",
    )

    with pytest.raises(ConfigValidationError, match="tool alias 'rich' must not declare a provider"):
        load_project_config(project_root)


def test_load_project_config_supports_native_windows_shell_aliases(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        'agents.config_loader_runtime.parsing_runtime.topology.is_native_windows',
        lambda: True,
    )
    project_root = tmp_path / 'repo-native-shell-alias'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """version = 2

[windows]
main = "agent1:codex, pwsh"
shells = "powershell, bash, wincmd"
""",
    )

    result = load_project_config(project_root)

    assert set(result.config.agents) == {'agent1'}
    assert result.config.windows[0].agent_names == ('agent1',)
    assert result.config.windows[0].tool_names == ('pwsh',)
    assert result.config.windows[1].agent_names == ()
    assert result.config.windows[1].tool_names == ('powershell', 'bash', 'wincmd')
    assert not ({'pwsh', 'powershell', 'bash', 'wincmd'} & set(result.config.agents))


def test_native_windows_shell_aliases_launch_the_requested_shell() -> None:
    from agents.models import (
        is_layout_tool_alias,
        is_native_windows_only_layout_tool_alias,
        layout_tool_alias_command,
    )

    expected = {
        'pwsh': 'exec pwsh',
        'powershell': 'exec powershell',
        'bash': 'exec bash',
        'wincmd': 'exec cmd',
    }
    for alias, command in expected.items():
        assert is_layout_tool_alias(alias)
        assert is_native_windows_only_layout_tool_alias(alias)
        assert layout_tool_alias_command(alias) == command
    # `rich` stays platform-agnostic.
    assert is_layout_tool_alias('rich')
    assert not is_native_windows_only_layout_tool_alias('rich')


@pytest.mark.parametrize('alias', ['pwsh', 'powershell', 'bash', 'wincmd'])
def test_load_project_config_rejects_native_windows_shell_alias_off_native_windows(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    alias: str,
) -> None:
    monkeypatch.setattr(
        'agents.config_loader_runtime.parsing_runtime.topology.is_native_windows',
        lambda: False,
    )
    project_root = tmp_path / f'repo-native-shell-off-{alias}'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        f"""version = 2

[windows]
main = "agent1:codex, {alias}"
""",
    )

    with pytest.raises(
        ConfigValidationError,
        match=f"tool alias '{alias}' is only available on native Windows",
    ):
        load_project_config(project_root)


@pytest.mark.parametrize('alias', ['pwsh', 'powershell', 'bash', 'wincmd'])
def test_native_windows_shell_alias_with_provider_remains_an_agent_name(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    alias: str,
) -> None:
    monkeypatch.setattr(
        'agents.config_loader_runtime.parsing_runtime.topology.is_native_windows',
        lambda: False,
    )
    project_root = tmp_path / f'repo-native-shell-agent-{alias}'
    _write(
        project_root / '.ccb' / 'ccb.config',
        f'''version = 2

[windows]
main = "{alias}:codex"
''',
    )

    result = load_project_config(project_root)

    assert set(result.config.agents) == {alias}
    assert result.config.windows[0].agent_names == (alias,)
    assert result.config.windows[0].tool_names == ()


def test_load_project_config_tool_windows_affect_topology_identity(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-tool-window-identity'
    config_path = project_root / '.ccb' / 'ccb.config'
    base = """version = 2

[windows]
main = "agent1:codex"
"""
    _write(config_path, base)
    without_tool = load_project_config(project_root).config

    _write(
        config_path,
        base
        + """
[tool_windows.files]
command = "ccb-workbench files"
""",
    )
    with_tool = load_project_config(project_root).config

    assert with_tool.tool_windows[0].label == 'files'
    assert with_tool.topology_signature != without_tool.topology_signature
    assert project_config_identity_payload(with_tool)['config_signature'] != project_config_identity_payload(without_tool)['config_signature']


def test_load_project_config_tool_window_label_and_sidebar_visibility_are_view_only(
    tmp_path: Path,
) -> None:
    base = """version = 2

[windows]
main = "agent1:codex"

[tool_windows.files]
command = "ccb-workbench files"
label = "files"
show_in_sidebar = true
"""
    changed = base.replace('label = "files"', 'label = "editor"').replace(
        'show_in_sidebar = true',
        'show_in_sidebar = false',
    )
    first_root = tmp_path / 'repo-tool-view-1'
    second_root = tmp_path / 'repo-tool-view-2'
    _write(first_root / '.ccb' / 'ccb.config', base)
    _write(second_root / '.ccb' / 'ccb.config', changed)

    first = load_project_config(first_root).config
    second = load_project_config(second_root).config

    assert first.topology_signature == second.topology_signature
    assert (
        project_config_identity_payload(first)['config_signature']
        == project_config_identity_payload(second)['config_signature']
    )


def test_load_project_config_rejects_tool_window_name_conflict(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-tool-window-conflict'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """version = 2

[windows]
files = "agent1:codex"

[tool_windows.files]
command = "ccb-workbench files"
""",
    )

    with pytest.raises(ConfigValidationError, match='tool window conflicts with agent window'):
        load_project_config(project_root)


def test_load_project_config_rejects_tool_windows_without_windows_topology(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-tool-without-windows'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """version = 2
default_agents = ["agent1"]
layout = "agent1:codex"

[agents.agent1]
provider = "codex"

[tool_windows.files]
command = "ccb-workbench files"
""",
    )

    with pytest.raises(ConfigValidationError, match='tool_windows requires windows topology'):
        load_project_config(project_root)


def test_load_project_config_rejects_invalid_tool_window(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-tool-invalid'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """version = 2

[windows]
main = "agent1:codex"

[tool_windows.files]
command = ""
""",
    )

    with pytest.raises(ConfigValidationError, match='command cannot be empty'):
        load_project_config(project_root)


def test_load_project_config_supports_windows_topology_partial_agent_overlay(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-windows-partial-agent-overlay'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """version = 2

[windows]
main = "agent1:codex(worktree)"

[agents.agent1]
workspace_mode = "inplace"
restore = "fresh"
""",
    )

    result = load_project_config(project_root)
    spec = result.config.agents['agent1']

    assert result.config.default_agents == ('agent1',)
    assert spec.provider == 'codex'
    assert spec.target == '.'
    assert spec.workspace_mode is WorkspaceMode.INPLACE
    assert spec.restore_default is RestoreMode.FRESH
    assert spec.permission_default is PermissionMode.MANUAL


def test_load_project_config_ignores_windows_topology_stale_agent_overlays(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-windows-stale-agent-overlay'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """version = 2

[windows]
main = "agent2:codex"

[agents.agent1]
provider = "codex"
target = "."
workspace_mode = "git-worktree"
restore = "auto"
permission = "manual"
""",
    )

    result = load_project_config(project_root)

    assert result.config.default_agents == ('agent2',)
    assert set(result.config.agents) == {'agent2'}
    assert result.config.agents['agent2'].workspace_mode is WorkspaceMode.INPLACE


def test_load_project_config_rejects_windows_topology_referenced_provider_conflict(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-windows-provider-conflict'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """version = 2

[windows]
main = "agent1:codex"

[agents.agent1]
provider = "claude"
""",
    )

    with pytest.raises(ConfigValidationError, match='provider conflicts between windows and agents table'):
        load_project_config(project_root)


@pytest.mark.parametrize(
    ('extra', 'message'),
    [
        ('default_agents = ["agent1"]\n', 'default_agents is not supported with windows topology'),
        ('layout = "agent1:codex"\n', 'layout is not supported with windows topology'),
        ('cmd_enabled = true\n', 'cmd_enabled is not supported with windows topology'),
    ],
)
def test_load_project_config_rejects_windows_topology_mixed_legacy_fields(
    tmp_path: Path,
    extra: str,
    message: str,
) -> None:
    project_root = tmp_path / 'repo-windows-mixed'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        f"""version = 2
{extra}
[windows]
main = "agent1:codex"
""",
    )

    with pytest.raises(ConfigValidationError, match=message):
        load_project_config(project_root)


def test_load_project_config_rejects_topology_fields_without_windows(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-sidebar-without-windows'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """version = 2
default_agents = ["agent1"]
layout = "agent1:codex"

[agents.agent1]
provider = "codex"
target = "."
workspace_mode = "inplace"
restore = "auto"
permission = "manual"

[ui.sidebar]
mode = "every_window"
""",
    )

    with pytest.raises(ConfigValidationError, match='ui\\.sidebar requires windows topology'):
        load_project_config(project_root)


def test_render_project_config_text_round_trips_agent_api_shortcut(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-render-api'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """version = 2
default_agents = ["agent1"]
layout = "cmd; agent1"
cmd_enabled = true

[agents.agent1]
provider = "codex"
target = "."
workspace_mode = "git-worktree"
restore = "auto"
permission = "manual"
key = "sk-test"
url = "https://api.example.test/v1"

[agents.agent1.provider_profile]
mode = "isolated"
inherit_skills = false
""",
    )

    loaded = load_project_config(project_root)
    rendered = render_project_config_text(loaded.config)

    assert rendered.startswith('cmd; agent1:codex(worktree)\n')
    assert '[agents.agent1]' in rendered
    assert 'key = "sk-test"' in rendered
    assert 'url = "https://api.example.test/v1"' in rendered
    assert '[agents.agent1.api]' not in rendered
    assert 'OPENAI_API_KEY' not in rendered
    assert 'inherit_api = false' not in rendered

    rewritten_path = tmp_path / 'repo-render-api-roundtrip' / '.ccb' / 'ccb.config'
    _write(rewritten_path, rendered)

    round_tripped = load_project_config(rewritten_path.parents[1])
    spec = round_tripped.config.agents['agent1']

    assert spec.api == AgentApiSpec(key='sk-test', url='https://api.example.test/v1')
    assert spec.provider_profile.mode == 'isolated'
    assert spec.provider_profile.inherit_api is False
    assert spec.provider_profile.inherit_skills is False
    assert spec.provider_profile.env == {
        'OPENAI_API_KEY': 'sk-test',
        'OPENAI_BASE_URL': 'https://api.example.test/v1',
    }


def test_render_project_config_text_migrates_legacy_nested_agent_api_shortcut_to_flat_fields(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-render-legacy-api'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """version = 2
default_agents = ["agent1"]
layout = "cmd; agent1"
cmd_enabled = true

[agents.agent1]
provider = "codex"
target = "."
workspace_mode = "git-worktree"
restore = "auto"
permission = "manual"

[agents.agent1.api]
key = "sk-legacy"
url = "https://legacy.example.test/v1"
""",
    )

    loaded = load_project_config(project_root)
    rendered = render_project_config_text(loaded.config)

    assert '[agents.agent1.api]' not in rendered
    assert '[agents.agent1]' in rendered
    assert 'key = "sk-legacy"' in rendered
    assert 'url = "https://legacy.example.test/v1"' in rendered


def test_render_project_config_text_round_trips_agent_model_shortcut(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-render-model'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """version = 2
default_agents = ["agent1"]
layout = "cmd; agent1"
cmd_enabled = true

[agents.agent1]
provider = "codex"
target = "."
workspace_mode = "git-worktree"
restore = "auto"
permission = "manual"
model = "gpt-5"
startup_args = ["--search"]
key = "sk-test"
url = "https://api.example.test/v1"
""",
    )

    loaded = load_project_config(project_root)
    rendered = render_project_config_text(loaded.config)

    assert rendered.startswith('cmd; agent1:codex(worktree)\n')
    assert '[agents.agent1]' in rendered
    assert 'model = "gpt-5"' in rendered
    assert 'startup_args = ["--search"]' in rendered
    assert 'startup_args = ["-m", "gpt-5", "--search"]' not in rendered

    rewritten_path = tmp_path / 'repo-render-model-roundtrip' / '.ccb' / 'ccb.config'
    _write(rewritten_path, rendered)

    round_tripped = load_project_config(rewritten_path.parents[1])
    spec = round_tripped.config.agents['agent1']

    assert spec.model == 'gpt-5'
    assert spec.startup_args == ('-m', 'gpt-5', '--search')
    assert spec.api == AgentApiSpec(key='sk-test', url='https://api.example.test/v1')


@pytest.mark.parametrize(
    ('provider', 'model_name', 'thinking'),
    [
        ('codex', 'gpt-5.5', 'xhigh'),
        ('deepseek', 'deepseek-v4-flash', 'high'),
        ('dsh', 'deepseek-v4-pro', 'max'),
    ],
)
def test_render_project_config_text_round_trips_static_thinking(
    tmp_path: Path,
    provider: str,
    model_name: str,
    thinking: str,
) -> None:
    project_root = tmp_path / f'repo-render-{provider}-thinking'
    _write(
        project_root / '.ccb' / 'ccb.config',
        f'''cmd; agent1:{provider}

[agents.agent1]
model = "{model_name}"
thinking = "{thinking}"
startup_args = ["--demo"]
''',
    )

    rendered = render_project_config_text(load_project_config(project_root).config)

    assert f'model = "{model_name}"' in rendered
    assert f'thinking = "{thinking}"' in rendered
    assert 'model_reasoning_effort' not in rendered
    assert 'startup_args = ["--demo"]' in rendered
    rewritten = tmp_path / f'repo-render-{provider}-thinking-roundtrip'
    _write(rewritten / '.ccb' / 'ccb.config', rendered)
    spec = load_project_config(rewritten).config.agents['agent1']
    assert spec.model == model_name
    assert spec.thinking == thinking


def test_render_project_config_text_preserves_non_entry_window_agent_overlays(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / 'repo-render-multi-window-overlays'
    _write(
        project_root / '.ccb' / 'ccb.config',
        '''version = 2
entry_window = "main"

[windows]
main = "agent1:codex"
secondary = "agent2:codex(worktree)"

[agents.agent1]
model = "gpt-5.5"

[agents.agent2]
model = "gpt-5.6-sol"
thinking = "xhigh"
role = "agentroles.coder"
''',
    )

    rendered = render_project_config_text(load_project_config(project_root).config)

    assert '[agents.agent1]' in rendered
    assert '[agents.agent2]' in rendered
    assert 'model = "gpt-5.6-sol"' in rendered
    assert 'thinking = "xhigh"' in rendered
    assert 'role = "agentroles.coder"' in rendered
    rewritten = tmp_path / 'repo-render-multi-window-overlays-roundtrip'
    _write(rewritten / '.ccb' / 'ccb.config', rendered)
    round_tripped = load_project_config(rewritten).config
    assert round_tripped.agents['agent1'].model == 'gpt-5.5'
    assert round_tripped.agents['agent2'].model == 'gpt-5.6-sol'
    assert round_tripped.agents['agent2'].thinking == 'xhigh'
    assert round_tripped.agents['agent2'].role == 'agentroles.coder'


def test_render_project_config_text_round_trips_noncompact_provider_profile(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-render-provider-profile'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """version = 2
default_agents = ["agent1"]
layout = "cmd; agent1"
cmd_enabled = true

[agents.agent1]
provider = "claude"
target = "."
workspace_mode = "git-worktree"
restore = "auto"
permission = "manual"

[agents.agent1.provider_profile]
mode = "isolated"
inherited_skill_exclude = ["trellis-*"]
inherit_api = false
inherit_auth = false

[agents.agent1.provider_profile.skill_overlays.n14_trellis]
source = "/root/.codex/skills"
include = ["trellis-*"]

[agents.agent1.provider_profile.env]
ANTHROPIC_API_KEY = "claude-key"
ANTHROPIC_BASE_URL = "https://claude.example.test"
""",
    )

    loaded = load_project_config(project_root)
    rendered = render_project_config_text(loaded.config)

    assert rendered.startswith('cmd; agent1:claude(worktree)\n')
    assert '[agents.agent1.provider_profile]' in rendered
    assert '[agents.agent1.provider_profile.env]' in rendered
    assert '[agents.agent1.provider_profile.skill_overlays.n14_trellis]' in rendered
    assert 'inherited_skill_exclude = ["trellis-*"]' in rendered
    assert 'ANTHROPIC_API_KEY = "claude-key"' in rendered

    rewritten_path = tmp_path / 'repo-render-provider-profile-roundtrip' / '.ccb' / 'ccb.config'
    _write(rewritten_path, rendered)

    round_tripped = load_project_config(rewritten_path.parents[1])
    spec = round_tripped.config.agents['agent1']

    assert spec.provider_profile.mode == 'isolated'
    assert spec.provider_profile.inherit_api is False
    assert spec.provider_profile.inherit_auth is False
    assert spec.provider_profile.inherited_skill_exclude == ('trellis-*',)
    assert spec.provider_profile.skill_overlays['n14_trellis'].source == '/root/.codex/skills'
    assert spec.provider_profile.skill_overlays['n14_trellis'].include == ('trellis-*',)
    assert spec.provider_profile.env == {
        'ANTHROPIC_API_KEY': 'claude-key',
        'ANTHROPIC_BASE_URL': 'https://claude.example.test',
    }


def test_load_project_config_reads_project_ccb_config_path(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-layout-path'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(config_path, 'cmd; agent1:codex\n')

    result = load_project_config(project_root)

    assert result.source_path == config_path
    assert result.config.layout_spec == 'cmd; agent1:codex'


def test_load_project_config_compact_format_does_not_require_toml_reader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / 'repo-compact-no-toml'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(config_path, 'cmd; agent1:codex\n')

    def _unexpected_reader(path: Path):
        raise AssertionError(f'compact config unexpectedly requested TOML reader for {path}')

    monkeypatch.setattr(config_documents, '_load_toml_reader', _unexpected_reader)

    result = load_project_config(project_root)

    assert result.config.layout_spec == 'cmd; agent1:codex'


def test_load_project_config_reports_actionable_error_when_rich_toml_parser_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / 'repo-rich-no-toml'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        'version = 2\n'
        'default_agents = ["agent1"]\n'
        'layout = "agent1"\n'
        '\n'
        '[agents.agent1]\n'
        'provider = "codex"\n'
        'target = "."\n',
    )

    monkeypatch.setattr(config_documents, '_import_optional_toml_reader', lambda: None)

    with pytest.raises(ConfigValidationError, match='rich TOML config requires Python 3.11\\+'):
        load_project_config(project_root)


def test_load_project_config_reports_actionable_error_when_hybrid_overlay_parser_is_missing(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / 'repo-hybrid-no-toml'
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        'cmd; agent1:codex\n'
        '\n'
        '[agents.agent1]\n'
        'key = "sk-test"\n',
    )

    monkeypatch.setattr(config_documents, '_import_optional_toml_reader', lambda: None)

    with pytest.raises(ConfigValidationError, match='rich TOML config requires Python 3.11\\+'):
        load_project_config(project_root)


def test_render_toml_value_service_handles_dict_inline_table() -> None:
    from agents.config_loader_runtime.defaults_runtime.rendering_runtime.service import _render_toml_value
    result = _render_toml_value({'key': 'val', 'count': 3})
    assert result == '{ key = "val", count = 3 }'


def test_render_toml_value_service_handles_empty_dict() -> None:
    from agents.config_loader_runtime.defaults_runtime.rendering_runtime.service import _render_toml_value
    result = _render_toml_value({})
    assert result == '{}'


def test_render_toml_value_service_handles_dict_in_mixed_list() -> None:
    from agents.config_loader_runtime.defaults_runtime.rendering_runtime.service import _render_toml_value
    result = _render_toml_value(['literal', {'key': 'val'}])
    assert result == '["literal", { key = "val" }]'


def test_render_toml_mapping_handles_array_of_tables() -> None:
    from agents.config_loader_runtime.defaults_runtime.rendering_runtime.service import _render_toml_document
    payload = {
        'items': [
            {'name': 'first', 'value': 1},
            {'name': 'second', 'value': 2},
        ]
    }
    rendered = _render_toml_document(payload)
    assert '[[items]]' in rendered
    assert 'name = "first"' in rendered
    assert 'value = 1' in rendered
    assert 'name = "second"' in rendered
    assert 'value = 2' in rendered


def test_render_toml_mapping_handles_array_of_tables_with_only_child_tables() -> None:
    from agents.config_loader_runtime.defaults_runtime.rendering_runtime.service import _render_toml_document
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
