from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from provider_core.runtime_shared import provider_start_env_vars
from provider_core.source_home import current_provider_source_home

from runtime_env.user_session import USER_SESSION_TRANSPORT_ENV_KEYS

_CONTROL_PLANE_ALLOWLIST = {
    'ANTHROPIC_API_KEY',
    'ANTHROPIC_AUTH_TOKEN',
    'ANTHROPIC_BASE_URL',
    'AGENT_ROLES_STORE',
    'APPDATA',
    'CCB_BACKEND_ENV',
    'CCB_CCBD_FAULTHANDLER',
    'CCB_CCBD_MIN_POLL_INTERVAL_S',
    'CCB_CLAUDE_NO_TERMINAL_TIMEOUT_S',
    'CCB_CODEX_NO_TERMINAL_TIMEOUT_S',
    'CCB_DEBUG',
    'CCB_GEMINI_NO_TERMINAL_TIMEOUT_S',
    'CCB_HERDR_CAPABILITY_REPORT',
    'CCB_HERDR_EXE',
    'CCB_HERDR_SESSION',
    'CCB_HERDR_SOCKET_REF',
    'CCB_KEEPER_PID',
    'CCB_KEYCHAIN_SERVICE_OVERRIDE',
    'CCB_LANG',
    'CCB_MOBILE_HOST_STATE_HOME',
    'CCB_NO_ATTACH',
    'CCB_PI_EXECUTION_MODE',
    'CCB_PI_EXTENSION_READY_TIMEOUT_S',
    'CCB_PI_NO_TERMINAL_TIMEOUT_S',
    'CCB_PYTHON',
    'CCB_REPLY_LANG',
    'CCB_RUNTIME_STATE_HOME',
    'CCB_STDIN_ENCODING',
    'CCB_SOURCE_ALLOWED_ROOTS',
    'CCB_SOURCE_HOME',
    'CCB_SOURCE_ROOT',
    'CCB_SH_EXECUTABLE',
    'CCB_TEST_ENTRYPOINT',
    'CCB_TEST_ROOTS',
    'CCB_VERSION',
    'CCB_WORKBENCH_FORCE_RICH',
    'CCB_WORKBENCH_PROFILE',
    'CCB_WORKBENCH_ROOT',
    'CCB_WORKBENCH_TERMINAL_PROGRAM',
    'CCB_WORKBENCH_TERMINAL_PROGRAM_VERSION',
    'CCB_WORKBENCH_YAZI_RICH_CONFIG',
    'CCB_WORKBENCH_YAZI_SAFE_CONFIG',
    'DBUS_SESSION_BUS_ADDRESS',
    'DESKTOP_SESSION',
    'DISPLAY',
    'DEEPSEEK_API_KEY',
    'DEEPSEEK_BASE_URL',
    'GEMINI_API_KEY',
    'GEMINI_MODEL',
    'GOOGLE_API_BASE',
    'GOOGLE_API_KEY',
    'GOOGLE_GEMINI_BASE_URL',
    'GOOGLE_GENAI_USE_VERTEXAI',
    'HOME',
    'LANG',
    'LC_ALL',
    'LC_MESSAGES',
    'LOCALAPPDATA',
    'OPENAI_API_BASE',
    'OPENAI_API_KEY',
    'OPENAI_BASE_URL',
    'OPENAI_ORG_ID',
    'OPENAI_ORGANIZATION',
    'PATH',
    'PROCESSOR_ARCHITECTURE',
    'PROCESSOR_ARCHITEW6432',
    'PYTHONUNBUFFERED',
    'SHELL',
    'SSH_AUTH_SOCK',
    'SYSTEMROOT',
    'TERM',
    'TERM_PROGRAM',
    'TERM_PROGRAM_VERSION',
    'TMP',
    'TEMP',
    'TMPDIR',
    'USER',
    'USERPROFILE',
    'XDG_CACHE_HOME',
    'XDG_CONFIG_HOME',
    'XDG_CURRENT_DESKTOP',
    'XDG_DATA_HOME',
    'XDG_RUNTIME_DIR',
    'XDG_SESSION_DESKTOP',
    'XDG_SESSION_TYPE',
    'XDG_STATE_HOME',
    'XAUTHORITY',
    'WAYLAND_DISPLAY',
    'WEZTERM_EXECUTABLE',
    'WEZTERM_PANE',
    'WEZTERM_UNIX_SOCKET',
    'KITTY_WINDOW_ID',
}
_CONTROL_PLANE_ALLOWLIST.update(provider_start_env_vars())

_CONTROL_PLANE_BLOCKED_PREFIXES = (
    'CODEX_',
    'CLAUDE_',
    'GEMINI_',
    'OPENCODE_',
    'DROID_',
    'DSH_',
    'CCB_CALLER_',
)

_CONTROL_PLANE_BLOCKED_EXACT = {
    'CCB_SESSION_FILE',
    'CCB_SESSION_ID',
    'CCB_TMUX_SOCKET',
    'CCB_TMUX_SOCKET_PATH',
    'PYTHONPATH',
    'TMUX',
    'TMUX_PANE',
}

_OPTIONAL_PROVIDER_RUNTIME_EXACT = {
    'AGENT_CLI_CREDENTIAL_STORE',
    'AGY_HOME',
    'COPILOT_CACHE_HOME',
    'COPILOT_DISABLE_KEYTAR',
    'COPILOT_HOME',
    'DEEPCODE_HOME',
    'DEEPSEEK_HOME',
    'DROID_ALLOW_ANY_PROJECT_SCAN',
    'DROID_POLL_INTERVAL',
    'DROID_SESSIONS_ROOT',
    'DROID_SESSION_SCAN_LIMIT',
    'DROID_SYNC_TIMEOUT',
    'DSH_AGENTS_HOME',
    'DSH_HOME',
    'FACTORY_DISABLE_KEYRING',
    'FACTORY_HOME',
    'FACTORY_HOME_OVERRIDE',
    'FACTORY_KEYTAR_PATH',
    'FACTORY_ROOT',
    'FACTORY_SESSIONS_ROOT',
    'KIMI_CODE_HOME',
    'KIMI_HOME',
    'KIMI_SHARE_DIR',
    'KIRO_HOME',
    'MIMOCODE_CONFIG',
    'MIMOCODE_DB',
    'MIMOCODE_HOME',
    'MIMOCODE_PROJECT_ID',
    'OPENCODE_CONFIG',
    'OPENCODE_LOG_ROOT',
    'OPENCODE_PROJECT_ID',
    'OPENCODE_RUNTIME_DIR',
    'OPENCODE_STORAGE_ROOT',
    'OPENCODE_TERMINAL',
    'OPENCODE_TMUX_SESSION',
    'PI_CODING_AGENT_DIR',
    'PI_CODING_AGENT_SESSION_DIR',
    'QWEN_CODE_FORCE_ENCRYPTED_FILE_STORAGE',
    'QWEN_CODE_FORCE_FILE_STORAGE',
    'QWEN_HOME',
}

_PROVIDER_API_ENV_KEYS = {
    'ANTHROPIC_API_KEY',
    'ANTHROPIC_AUTH_TOKEN',
    'ANTHROPIC_BASE_URL',
    'DEEPCODE_API_KEY',
    'DEEPCODE_BASE_URL',
    'DEEPSEEK_API_KEY',
    'DEEPSEEK_BASE_URL',
    'GEMINI_API_KEY',
    'GEMINI_MODEL',
    'GOOGLE_API_BASE',
    'GOOGLE_API_KEY',
    'GOOGLE_GEMINI_BASE_URL',
    'GOOGLE_GENAI_USE_VERTEXAI',
    'OPENAI_API_BASE',
    'OPENAI_API_KEY',
    'OPENAI_BASE_URL',
    'OPENAI_ORG_ID',
    'OPENAI_ORGANIZATION',
}

_MANAGED_CALLER_MARKERS = {
    'CCB_CALLER_ACTOR',
    'CCB_CALLER_RUNTIME_DIR',
    'CCB_SESSION_FILE',
    'CCB_SESSION_ID',
}


def control_plane_env(
    *,
    extra: dict[str, str] | None = None,
    environ: Mapping[str, object] | None = None,
) -> dict[str, str]:
    source = scrub_managed_provider_runtime_env(environ)
    env: dict[str, str] = {}
    for key, value in source.items():
        if key in _CONTROL_PLANE_BLOCKED_EXACT:
            continue
        if key in _CONTROL_PLANE_ALLOWLIST or key in USER_SESSION_TRANSPORT_ENV_KEYS:
            env[key] = value
            continue
        if any(key.startswith(prefix) for prefix in _CONTROL_PLANE_BLOCKED_PREFIXES):
            continue
        if key == 'PYTHONPATH':
            continue
        if key.startswith(('PYTHON', 'VIRTUAL_ENV', 'CONDA')):
            env[key] = value
    if extra:
        for key, value in extra.items():
            if value is None:
                env.pop(key, None)
                continue
            env[key] = str(value)
    return env


def scrub_managed_provider_runtime_env(
    environ: Mapping[str, object] | None = None,
) -> dict[str, str]:
    source = {
        str(key): str(value)
        for key, value in (os.environ if environ is None else environ).items()
        if value is not None
    }
    managed_caller = _is_managed_provider_caller(source)
    scrubbed: dict[str, str] = {}
    for key, value in source.items():
        if key in _CONTROL_PLANE_BLOCKED_EXACT:
            continue
        if key in _OPTIONAL_PROVIDER_RUNTIME_EXACT:
            continue
        if (
            any(key.startswith(prefix) for prefix in _CONTROL_PLANE_BLOCKED_PREFIXES)
            and key not in _CONTROL_PLANE_ALLOWLIST
            and key not in USER_SESSION_TRANSPORT_ENV_KEYS
        ):
            continue
        if managed_caller and key in _PROVIDER_API_ENV_KEYS:
            continue
        scrubbed[key] = value
    if managed_caller:
        _restore_user_home_roots(scrubbed, source)
    return scrubbed


def _is_managed_provider_caller(source: Mapping[str, str]) -> bool:
    if any(str(source.get(key) or '').strip() for key in _MANAGED_CALLER_MARKERS):
        return True
    return any(
        _looks_like_ccb_provider_home(source.get(key))
        for key in ('HOME', 'USERPROFILE')
    )


def _restore_user_home_roots(
    env: dict[str, str],
    source: Mapping[str, str],
) -> None:
    source_home = _source_home_for_environment(source)
    if not source_home:
        return
    home_text = str(source_home)
    managed_home = _path_or_none(source.get('HOME'))
    env['HOME'] = home_text
    if source.get('USERPROFILE'):
        env['USERPROFILE'] = home_text
    defaults = {
        'XDG_CONFIG_HOME': source_home / '.config',
        'XDG_DATA_HOME': source_home / '.local' / 'share',
        'XDG_STATE_HOME': source_home / '.local' / 'state',
        'XDG_CACHE_HOME': source_home / '.cache',
    }
    for key, fallback in defaults.items():
        current = _path_or_none(source.get(key))
        if current is None:
            continue
        if (
            _looks_like_ccb_provider_home(current)
            or _is_within(current, managed_home)
            or _looks_like_ccb_provider_cache(current)
        ):
            env[key] = str(fallback)


def _source_home_for_environment(source: Mapping[str, str]) -> Path:
    explicit = _path_or_none(source.get('CCB_SOURCE_HOME'))
    if explicit is not None:
        return explicit
    ambient_home = _path_or_none(source.get('HOME'))
    if (
        ambient_home is not None
        and not _is_managed_provider_caller(source)
        and not _looks_like_ccb_provider_home(ambient_home)
    ):
        return ambient_home
    return current_provider_source_home()


def _looks_like_ccb_provider_home(value: object) -> bool:
    path = _path_or_none(value)
    if path is None:
        return False
    parts = path.parts
    for index in range(0, max(len(parts) - 4, 0)):
        if parts[index] != 'agents':
            continue
        if parts[index + 2] == 'provider-state' and parts[index + 4] == 'home':
            return True
    return False


def _looks_like_ccb_provider_cache(path: Path) -> bool:
    parts = tuple(part.lower() for part in path.parts)
    for index in range(0, max(len(parts) - 2, 0)):
        if parts[index:index + 3] == ('ccb', 'provider-cache', 'gemini'):
            return True
    return False


def _is_within(path: Path, root: Path | None) -> bool:
    if root is None:
        return False
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except Exception:
        return False


def _path_or_none(value: object) -> Path | None:
    raw = str(value or '').strip()
    if not raw:
        return None
    try:
        return Path(raw).expanduser()
    except Exception:
        return None


__all__ = ['control_plane_env', 'scrub_managed_provider_runtime_env']
