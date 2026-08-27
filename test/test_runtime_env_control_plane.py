from __future__ import annotations

from provider_core.runtime_shared import provider_start_env_vars
from runtime_env.control_plane import control_plane_env
from terminal_runtime.env import isolated_tmux_env


def test_control_plane_env_keeps_provider_api_env(monkeypatch) -> None:
    monkeypatch.setenv('OPENAI_API_KEY', 'openai-key')
    monkeypatch.setenv('OPENAI_BASE_URL', 'https://api.example.test/v1')
    monkeypatch.setenv('ANTHROPIC_API_KEY', 'anthropic-key')
    monkeypatch.setenv('GEMINI_API_KEY', 'gemini-key')
    monkeypatch.setenv('GEMINI_MODEL', 'gemini-3.1-pro-preview')
    monkeypatch.setenv('GOOGLE_GEMINI_BASE_URL', 'https://chatapi.onechats.ai')

    env = control_plane_env()

    assert env['OPENAI_API_KEY'] == 'openai-key'
    assert env['OPENAI_BASE_URL'] == 'https://api.example.test/v1'
    assert env['ANTHROPIC_API_KEY'] == 'anthropic-key'
    assert env['GEMINI_API_KEY'] == 'gemini-key'
    assert env['GEMINI_MODEL'] == 'gemini-3.1-pro-preview'
    assert env['GOOGLE_GEMINI_BASE_URL'] == 'https://chatapi.onechats.ai'


def test_control_plane_env_keeps_provider_start_overrides(monkeypatch) -> None:
    for env_name in provider_start_env_vars():
        monkeypatch.setenv(env_name, f'/tmp/{env_name.lower()} --stub')
    monkeypatch.setenv('CODEX_HOME', '/tmp/global-codex-home')
    monkeypatch.setenv('QWEN_HOME', '/tmp/global-qwen-home')
    monkeypatch.setenv('CCB_SESSION_ID', 'stale-session')

    env = control_plane_env()

    for env_name in provider_start_env_vars():
        assert env[env_name] == f'/tmp/{env_name.lower()} --stub'
    assert 'CODEX_HOME' not in env
    assert 'QWEN_HOME' not in env
    assert 'CCB_SESSION_ID' not in env


def test_control_plane_env_keeps_claude_keychain_override(monkeypatch) -> None:
    monkeypatch.setenv('CCB_KEYCHAIN_SERVICE_OVERRIDE', 'Claude Code-credentials-account-a')

    env = control_plane_env()

    assert env['CCB_KEYCHAIN_SERVICE_OVERRIDE'] == 'Claude Code-credentials-account-a'


def test_control_plane_env_keeps_tmux_config_override(monkeypatch) -> None:
    monkeypatch.setenv('CCB_TMUX_CONFIG', '/home/demo/.config/ccb/tmux.conf')

    env = control_plane_env()

    assert env['CCB_TMUX_CONFIG'] == '/home/demo/.config/ccb/tmux.conf'


def test_control_plane_env_does_not_inject_tmux_config_override(monkeypatch) -> None:
    monkeypatch.delenv('CCB_TMUX_CONFIG', raising=False)

    env = control_plane_env()

    assert 'CCB_TMUX_CONFIG' not in env


def test_control_plane_env_keeps_agent_roles_store_pin(monkeypatch) -> None:
    monkeypatch.setenv('AGENT_ROLES_STORE', '/home/demo/.roles')

    env = control_plane_env()

    assert env['AGENT_ROLES_STORE'] == '/home/demo/.roles'


def test_control_plane_env_keeps_windows_profile_roots(monkeypatch) -> None:
    monkeypatch.setenv('APPDATA', 'C:/Users/demo/AppData/Roaming')
    monkeypatch.setenv('LOCALAPPDATA', 'C:/Users/demo/AppData/Local')
    monkeypatch.setenv('USERPROFILE', 'C:/Users/demo')

    env = control_plane_env()

    assert env['APPDATA'] == 'C:/Users/demo/AppData/Roaming'
    assert env['LOCALAPPDATA'] == 'C:/Users/demo/AppData/Local'
    assert env['USERPROFILE'] == 'C:/Users/demo'


def test_control_plane_env_keeps_source_test_wrapper_signals(monkeypatch) -> None:
    monkeypatch.setenv('CCB_TEST_ENTRYPOINT', '1')
    monkeypatch.setenv('CCB_RUNTIME_STATE_HOME', '/tmp/source-dev-state/projects')
    monkeypatch.setenv('CCB_SOURCE_ALLOWED_ROOTS', '/tmp/source-test-root')
    monkeypatch.setenv('CCB_SOURCE_ROOT', '/tmp/ccb-source')
    monkeypatch.setenv('CCB_TEST_ROOTS', '/tmp/extra-test-root')
    monkeypatch.setenv('CCB_CALLER_ACTOR', 'stale-agent')

    env = control_plane_env()

    assert env['CCB_TEST_ENTRYPOINT'] == '1'
    assert env['CCB_RUNTIME_STATE_HOME'] == '/tmp/source-dev-state/projects'
    assert env['CCB_SOURCE_ALLOWED_ROOTS'] == '/tmp/source-test-root'
    assert env['CCB_SOURCE_ROOT'] == '/tmp/ccb-source'
    assert env['CCB_TEST_ROOTS'] == '/tmp/extra-test-root'
    assert 'CCB_CALLER_ACTOR' not in env


def test_control_plane_env_keeps_pi_execution_policy(monkeypatch) -> None:
    monkeypatch.setenv('CCB_PI_EXECUTION_MODE', 'headless')
    monkeypatch.setenv('CCB_PI_EXTENSION_READY_TIMEOUT_S', '45')
    monkeypatch.setenv('CCB_PI_NO_TERMINAL_TIMEOUT_S', '1800')

    env = control_plane_env()

    assert env['CCB_PI_EXECUTION_MODE'] == 'headless'
    assert env['CCB_PI_EXTENSION_READY_TIMEOUT_S'] == '45'
    assert env['CCB_PI_NO_TERMINAL_TIMEOUT_S'] == '1800'


def test_control_plane_env_keeps_managed_provider_no_terminal_timeouts(monkeypatch) -> None:
    monkeypatch.setenv('CCB_CLAUDE_NO_TERMINAL_TIMEOUT_S', '1800')
    monkeypatch.setenv('CCB_CODEX_NO_TERMINAL_TIMEOUT_S', '1800')
    monkeypatch.setenv('CCB_GEMINI_NO_TERMINAL_TIMEOUT_S', '1800')

    env = control_plane_env()

    assert env['CCB_CLAUDE_NO_TERMINAL_TIMEOUT_S'] == '1800'
    assert env['CCB_CODEX_NO_TERMINAL_TIMEOUT_S'] == '1800'
    assert env['CCB_GEMINI_NO_TERMINAL_TIMEOUT_S'] == '1800'


def test_control_plane_env_keeps_mobile_host_state_override(monkeypatch) -> None:
    monkeypatch.setenv('CCB_MOBILE_HOST_STATE_HOME', '/tmp/ccb-mobile-state')

    env = control_plane_env()

    assert env['CCB_MOBILE_HOST_STATE_HOME'] == '/tmp/ccb-mobile-state'


def test_control_plane_env_keeps_herdr_runtime_selection(monkeypatch) -> None:
    monkeypatch.setenv('CCB_HERDR_EXE', '/opt/herdr/herdr')
    monkeypatch.setenv('CCB_HERDR_SOCKET_REF', 'herdr://cmd-013-local')
    monkeypatch.setenv('CCB_HERDR_CAPABILITY_REPORT', '/tmp/herdr-capabilities.json')
    monkeypatch.setenv('CCB_HERDR_SESSION', 'cmd-013-session')

    env = control_plane_env()

    assert env['CCB_HERDR_EXE'] == '/opt/herdr/herdr'
    assert env['CCB_HERDR_SOCKET_REF'] == 'herdr://cmd-013-local'
    assert env['CCB_HERDR_CAPABILITY_REPORT'] == '/tmp/herdr-capabilities.json'
    assert env['CCB_HERDR_SESSION'] == 'cmd-013-session'


def test_control_plane_env_keeps_windows_processor_arch_for_platform_gate(monkeypatch) -> None:
    monkeypatch.setenv('PROCESSOR_ARCHITECTURE', 'AMD64')
    monkeypatch.setenv('PROCESSOR_ARCHITEW6432', 'ARM64')
    monkeypatch.setenv('CCB_CALLER_ACTOR', 'stale-agent')

    env = control_plane_env()

    assert env['PROCESSOR_ARCHITECTURE'] == 'AMD64'
    assert env['PROCESSOR_ARCHITEW6432'] == 'ARM64'
    assert 'CCB_CALLER_ACTOR' not in env


def test_control_plane_env_keeps_user_session_transport_for_cmd_shell(monkeypatch) -> None:
    monkeypatch.setenv('DISPLAY', ':0')
    monkeypatch.setenv('WAYLAND_DISPLAY', 'wayland-0')
    monkeypatch.setenv('DBUS_SESSION_BUS_ADDRESS', 'unix:path=/run/user/1000/bus')
    monkeypatch.setenv('XAUTHORITY', '/tmp/.Xauthority')
    monkeypatch.setenv('SSH_AUTH_SOCK', '/tmp/ssh-agent.sock')

    env = control_plane_env()

    assert env['DISPLAY'] == ':0'
    assert env['WAYLAND_DISPLAY'] == 'wayland-0'
    assert env['DBUS_SESSION_BUS_ADDRESS'] == 'unix:path=/run/user/1000/bus'
    assert env['XAUTHORITY'] == '/tmp/.Xauthority'
    assert env['SSH_AUTH_SOCK'] == '/tmp/ssh-agent.sock'


def test_control_plane_env_keeps_rich_terminal_workbench_signals(monkeypatch) -> None:
    monkeypatch.setenv('TERM_PROGRAM', 'WezTerm')
    monkeypatch.setenv('TERM_PROGRAM_VERSION', '20260615')
    monkeypatch.setenv('WEZTERM_EXECUTABLE', '/usr/bin/wezterm')
    monkeypatch.setenv('WEZTERM_PANE', '7')
    monkeypatch.setenv('WEZTERM_UNIX_SOCKET', '/tmp/wezterm.sock')
    monkeypatch.setenv('KITTY_WINDOW_ID', '42')
    monkeypatch.setenv('CCB_WORKBENCH_PROFILE', 'rich')
    monkeypatch.setenv('CCB_WORKBENCH_FORCE_RICH', '1')
    monkeypatch.setenv('CCB_WORKBENCH_ROOT', '/tmp/workbench')
    monkeypatch.setenv('CCB_WORKBENCH_TERMINAL_PROGRAM', 'WezTerm')
    monkeypatch.setenv('CCB_WORKBENCH_TERMINAL_PROGRAM_VERSION', '20260615')
    monkeypatch.setenv('CCB_WORKBENCH_YAZI_SAFE_CONFIG', '/tmp/workbench/yazi-safe')
    monkeypatch.setenv('CCB_WORKBENCH_YAZI_RICH_CONFIG', '/tmp/workbench/yazi-rich')

    env = control_plane_env()

    assert env['TERM_PROGRAM'] == 'WezTerm'
    assert env['TERM_PROGRAM_VERSION'] == '20260615'
    assert env['WEZTERM_EXECUTABLE'] == '/usr/bin/wezterm'
    assert env['WEZTERM_PANE'] == '7'
    assert env['WEZTERM_UNIX_SOCKET'] == '/tmp/wezterm.sock'
    assert env['KITTY_WINDOW_ID'] == '42'
    assert env['CCB_WORKBENCH_PROFILE'] == 'rich'
    assert env['CCB_WORKBENCH_FORCE_RICH'] == '1'
    assert env['CCB_WORKBENCH_ROOT'] == '/tmp/workbench'
    assert env['CCB_WORKBENCH_TERMINAL_PROGRAM'] == 'WezTerm'
    assert env['CCB_WORKBENCH_TERMINAL_PROGRAM_VERSION'] == '20260615'
    assert env['CCB_WORKBENCH_YAZI_SAFE_CONFIG'] == '/tmp/workbench/yazi-safe'
    assert env['CCB_WORKBENCH_YAZI_RICH_CONFIG'] == '/tmp/workbench/yazi-rich'


def test_control_plane_env_keeps_network_transport_without_provider_authority(monkeypatch) -> None:
    monkeypatch.setenv('HTTPS_PROXY', 'http://127.0.0.1:7890')
    monkeypatch.setenv('NO_PROXY', 'localhost,127.0.0.1')
    monkeypatch.setenv('CODEX_CA_CERTIFICATE', '/tmp/codex-ca.pem')
    monkeypatch.setenv('SSL_CERT_FILE', '/tmp/ca.pem')
    monkeypatch.setenv('WSL_INTEROP', '/run/WSL/1234_interop')
    monkeypatch.setenv('WSL_DISTRO_NAME', 'Ubuntu-22.04')
    monkeypatch.setenv('CODEX_HOME', '/tmp/global-codex-home')
    monkeypatch.setenv('CODEX_SESSION_ROOT', '/tmp/global-codex-sessions')
    monkeypatch.setenv('GEMINI_ROOT', '/tmp/global-gemini-root')
    monkeypatch.setenv('CLAUDE_PROJECTS_ROOT', '/tmp/global-claude-projects')
    monkeypatch.setenv('CCB_SESSION_ID', 'stale-session')
    monkeypatch.setenv('CCB_CALLER_ACTOR', 'stale-agent')

    env = control_plane_env()

    assert env['HTTPS_PROXY'] == 'http://127.0.0.1:7890'
    assert env['NO_PROXY'] == 'localhost,127.0.0.1'
    assert env['CODEX_CA_CERTIFICATE'] == '/tmp/codex-ca.pem'
    assert env['SSL_CERT_FILE'] == '/tmp/ca.pem'
    assert env['WSL_INTEROP'] == '/run/WSL/1234_interop'
    assert env['WSL_DISTRO_NAME'] == 'Ubuntu-22.04'
    assert 'CODEX_HOME' not in env
    assert 'CODEX_SESSION_ROOT' not in env
    assert 'GEMINI_ROOT' not in env
    assert 'CLAUDE_PROJECTS_ROOT' not in env
    assert 'CCB_SESSION_ID' not in env
    assert 'CCB_CALLER_ACTOR' not in env


def test_control_plane_env_drops_outer_tmux_authority(monkeypatch) -> None:
    monkeypatch.setenv('TMUX', '/tmp/tmux-1000/default,123,0')
    monkeypatch.setenv('TMUX_PANE', '%77')
    monkeypatch.setenv('CCB_TMUX_SOCKET', 'outer')
    monkeypatch.setenv('CCB_TMUX_SOCKET_PATH', '/tmp/outer.sock')

    env = control_plane_env()

    assert 'TMUX' not in env
    assert 'TMUX_PANE' not in env
    assert 'CCB_TMUX_SOCKET' not in env
    assert 'CCB_TMUX_SOCKET_PATH' not in env


def test_control_plane_env_drops_outer_pythonpath(monkeypatch) -> None:
    monkeypatch.setenv('PYTHONPATH', '/stable/ccb/lib:/other')
    monkeypatch.setenv('PYTHONUNBUFFERED', '1')

    env = control_plane_env()

    assert 'PYTHONPATH' not in env
    assert env['PYTHONUNBUFFERED'] == '1'


def test_managed_provider_caller_does_not_flow_back_into_control_plane(
    monkeypatch,
    tmp_path,
) -> None:
    source_home = tmp_path / 'source-home'
    managed_home = (
        tmp_path
        / 'repo'
        / '.ccb'
        / 'agents'
        / 'codex1'
        / 'provider-state'
        / 'codex'
        / 'home'
    )
    monkeypatch.setenv('CCB_SOURCE_HOME', str(source_home))
    monkeypatch.setenv('CCB_SESSION_FILE', str(tmp_path / 'repo' / '.ccb' / '.codex-codex1-session'))
    monkeypatch.setenv('CCB_CALLER_ACTOR', 'codex1')
    monkeypatch.setenv('HOME', str(managed_home))
    monkeypatch.setenv('USERPROFILE', str(managed_home))
    monkeypatch.setenv('XDG_CONFIG_HOME', str(managed_home / '.config'))
    monkeypatch.setenv('XDG_DATA_HOME', str(managed_home / '.local' / 'share'))
    monkeypatch.setenv('XDG_STATE_HOME', str(managed_home / '.local' / 'state'))
    monkeypatch.setenv(
        'XDG_CACHE_HOME',
        str(source_home / '.cache' / 'ccb' / 'provider-cache' / 'gemini' / 'xdg'),
    )
    monkeypatch.setenv('CODEX_HOME', str(managed_home))
    monkeypatch.setenv('OPENAI_API_KEY', 'managed-agent-key')

    env = control_plane_env()

    assert env['HOME'] == str(source_home)
    assert env['USERPROFILE'] == str(source_home)
    assert env['XDG_CONFIG_HOME'] == str(source_home / '.config')
    assert env['XDG_DATA_HOME'] == str(source_home / '.local' / 'share')
    assert env['XDG_STATE_HOME'] == str(source_home / '.local' / 'state')
    assert env['XDG_CACHE_HOME'] == str(source_home / '.cache')
    assert env['CCB_SOURCE_HOME'] == str(source_home)
    assert 'CCB_SESSION_FILE' not in env
    assert 'CCB_CALLER_ACTOR' not in env
    assert 'CODEX_HOME' not in env
    assert 'OPENAI_API_KEY' not in env


def test_tmux_control_env_scrubs_provider_runtime_but_keeps_safe_shell_state(
    tmp_path,
) -> None:
    source_home = tmp_path / 'source-home'
    managed_home = (
        tmp_path
        / '.ccb'
        / 'agents'
        / 'cursor1'
        / 'provider-state'
        / 'cursor'
        / 'home'
    )
    env = isolated_tmux_env(
        {
            'CCB_SOURCE_HOME': str(source_home),
            'CCB_SESSION_FILE': str(tmp_path / '.ccb' / '.cursor-cursor1-session'),
            'CCB_CALLER_ACTOR': 'cursor1',
            'HOME': str(managed_home),
            'PATH': '/usr/bin',
            'SAFE_CUSTOM_ENV': 'keep-me',
            'AGENT_CLI_CREDENTIAL_STORE': 'file',
            'OPENAI_API_KEY': 'managed-agent-key',
            'TMUX': '/tmp/outer,1,0',
            'TMUX_PANE': '%9',
        }
    )

    assert env['HOME'] == str(source_home)
    assert env['PATH'] == '/usr/bin'
    assert env['SAFE_CUSTOM_ENV'] == 'keep-me'
    assert 'CCB_SESSION_FILE' not in env
    assert 'CCB_CALLER_ACTOR' not in env
    assert 'AGENT_CLI_CREDENTIAL_STORE' not in env
    assert 'OPENAI_API_KEY' not in env
    assert 'TMUX' not in env
    assert 'TMUX_PANE' not in env


def test_control_plane_restores_source_home_for_nonstandard_managed_home(
    monkeypatch,
    tmp_path,
) -> None:
    account_home = tmp_path / 'account-home'
    managed_home = tmp_path / '.ccb_agy_homes' / 'runtime-id'
    monkeypatch.setenv('HOME', str(managed_home))
    monkeypatch.setenv('CCB_CALLER_ACTOR', 'agy1')
    monkeypatch.delenv('CCB_SOURCE_HOME', raising=False)
    import provider_core.source_home as source_home_module

    if source_home_module.pwd is not None:
        monkeypatch.setattr(
            source_home_module.pwd,
            'getpwuid',
            lambda _uid: type('Account', (), {'pw_dir': str(account_home)})(),
        )

    env = control_plane_env()

    assert env['HOME'] == str(account_home)
    assert 'CCB_CALLER_ACTOR' not in env
