from __future__ import annotations

from pathlib import Path

from .models import StorageClass, StorageEntry


_SECRET_FILENAMES = {
    '.ccb-auth-projection.json',
    '.credentials.json',
    '.credentials.yaml',
    '.env',
    'a2a-oauth-tokens.json',
    'auth.encrypted',
    'auth.json',
    'auth.v2.file',
    'auth.v2.key',
    'auth.v2.keyring',
    'auth.v2.loginkeychain',
    'company-codex-api-key',
    'company-codex.config.toml',
    'credentials.json',
    'extension-secrets-v1.json',
    'gemini-credentials.json',
    'google_accounts.json',
    'mcp-oauth-tokens-v2.json',
    'mcp-oauth-tokens.json',
    'mcp-oauth.v2.file',
    'mcp-oauth.v2.key',
    'oauth_creds.json',
}
_CLAUDE_PROJECTED_NAMES = {'settings.json', 'CLAUDE.md'}
_GEMINI_PROJECTED_NAMES = {'settings.json', 'trustedFolders.json'}
_CODEX_PROJECTED_NAMES = {'config.toml'}
_OPENCODE_PROJECTED_NAMES = {'opencode.json'}
_MIMO_PROJECTED_NAMES = {'mimocode.json'}
_COPILOT_PROJECTION_MARKER_NAME = '.ccb-installed-plugins-projection.json'
_NATIVE_CLI_PROVIDERS = {'qwen', 'qoder', 'qoderclicn', 'cursor', 'copilot', 'crush', 'dsh', 'grok', 'kiro', 'pi', 'omp', 'zai'}
_NATIVE_CLI_PROJECTED_ROOTS = {'inherited-skills', 'role-skills', 'overlay-skills'}
_NATIVE_CLI_CACHE_ROOTS = {'.cache', '.npm', '.tmp', 'cache', 'node_modules', 'tmp'}
_NATIVE_CLI_SESSION_ROOTS = {
    '.config',
    '.crush',
    '.cursor',
    '.kiro',
    '.local',
    '.pi',
    '.qoder',
    '.qwen',
    'data',
    'logs',
    'session',
    'sessions',
    'state',
}
_PROVIDER_MIXED_SECRET_NAMES = {
    'deepseek': {'settings.json'},
    'kimi': {'config.toml', 'device_id', 'kimi.json'},
    'kiro': {'data.sqlite3'},
    'mimo': {'token.json'},
    'opencode': {'account.json'},
    'omp': {
        'agent.db',
        'agent.db-shm',
        'agent.db-wal',
        'config.yaml',
        'config.yml',
        'models.json',
        'models.yaml',
        'models.yml',
        'oauth.json',
        'settings.json',
    },
    'crush': {'hyper.json', 'providers.json'},
    'zai': {'user-settings.json'},
}
_CODEX_SESSION_NAMES = {
    '.ccb-session-namespace.json',
    'history.jsonl',
    'logs_2.sqlite',
    'logs_2.sqlite-shm',
    'logs_2.sqlite-wal',
    'state_5.sqlite',
    'state_5.sqlite-shm',
    'state_5.sqlite-wal',
}


def classify_provider_home(
    path: Path,
    relative_path: str,
    provider: str,
    agent: str,
    remainder: tuple[str, ...],
    *,
    size: int,
    root_kind: str,
) -> StorageEntry:
    provider = str(provider or '').strip().lower() or None
    if not remainder:
        return _entry(path, relative_path, StorageClass.PROJECTED_CONFIG, size, provider=provider, agent=agent, root_kind=root_kind)
    name = remainder[-1]
    if name in _SECRET_FILENAMES:
        return _entry(path, relative_path, StorageClass.SECRET, size, provider=provider, agent=agent, reason='provider_secret', root_kind=root_kind)
    if name in _PROVIDER_MIXED_SECRET_NAMES.get(provider, set()):
        return _entry(
            path,
            relative_path,
            StorageClass.SECRET,
            size,
            provider=provider,
            agent=agent,
            reason='provider_mixed_auth_state',
            root_kind=root_kind,
        )
    if name.endswith('.ccb-projection.json'):
        return _entry(
            path,
            relative_path,
            StorageClass.PROJECTED_CONFIG,
            size,
            provider=provider,
            agent=agent,
            reason='projected_asset_marker',
            root_kind=root_kind,
        )
    if provider == 'codex':
        return _classify_codex_home(path, relative_path, remainder, size=size, provider=provider, agent=agent, root_kind=root_kind)
    if provider == 'claude':
        return _classify_claude_home(path, relative_path, remainder, size=size, provider=provider, agent=agent, root_kind=root_kind)
    if provider == 'gemini':
        return _classify_gemini_home(path, relative_path, remainder, size=size, provider=provider, agent=agent, root_kind=root_kind)
    if provider == 'opencode':
        return _classify_opencode_home(path, relative_path, remainder, size=size, provider=provider, agent=agent, root_kind=root_kind)
    if provider == 'kimi':
        return _classify_kimi_home(path, relative_path, remainder, size=size, provider=provider, agent=agent, root_kind=root_kind)
    if provider == 'mimo':
        return _classify_mimo_home(path, relative_path, remainder, size=size, provider=provider, agent=agent, root_kind=root_kind)
    if provider == 'droid':
        return _classify_droid_home(path, relative_path, remainder, size=size, provider=provider, agent=agent, root_kind=root_kind)
    if provider == 'copilot':
        return _classify_copilot_home(path, relative_path, remainder, size=size, provider=provider, agent=agent, root_kind=root_kind)
    if provider in {'qoder', 'qoderclicn'} and remainder[0] == '.auth':
        return _entry(
            path,
            relative_path,
            StorageClass.SECRET,
            size,
            provider=provider,
            agent=agent,
            reason=f'{provider}_auth_state',
            root_kind=root_kind,
        )
    if provider in _NATIVE_CLI_PROVIDERS:
        return _classify_native_cli_home(path, relative_path, remainder, size=size, provider=provider, agent=agent, root_kind=root_kind)
    return _entry(path, relative_path, StorageClass.UNKNOWN, size, provider=provider, agent=agent, root_kind=root_kind)


def _classify_codex_home(
    path: Path,
    relative_path: str,
    remainder: tuple[str, ...],
    *,
    size: int,
    provider: str,
    agent: str,
    root_kind: str,
) -> StorageEntry:
    name = remainder[-1]
    if remainder[0] == 'sessions' or name in _CODEX_SESSION_NAMES or remainder[0] in {'log', 'logs', 'shell_snapshots'}:
        return _entry(path, relative_path, StorageClass.SESSION, size, provider=provider, agent=agent, root_kind=root_kind)
    if remainder[0] == '.tmp' and len(remainder) >= 2 and remainder[1] == 'plugins':
        return _entry(
            path,
            relative_path,
            StorageClass.STARTUP_AUTHORITY_BUNDLE,
            size,
            provider=provider,
            agent=agent,
            reclaimable=False,
            reason='codex_plugin_bundle',
            root_kind=root_kind,
        )
    if name == 'plugins.sha' and remainder[:1] == ('.tmp',):
        return _entry(
            path,
            relative_path,
            StorageClass.STARTUP_AUTHORITY_BUNDLE,
            size,
            provider=provider,
            agent=agent,
            reclaimable=False,
            reason='codex_plugin_bundle_manifest',
            root_kind=root_kind,
        )
    if name in _CODEX_PROJECTED_NAMES or remainder[0] in {'skills', 'commands'}:
        return _entry(path, relative_path, StorageClass.PROJECTED_CONFIG, size, provider=provider, agent=agent, root_kind=root_kind)
    if remainder[0] in {'.tmp', '.cache'}:
        return _entry(path, relative_path, StorageClass.REBUILDABLE_CACHE, size, provider=provider, agent=agent, root_kind=root_kind)
    return _entry(path, relative_path, StorageClass.UNKNOWN, size, provider=provider, agent=agent, root_kind=root_kind)


def _classify_claude_home(
    path: Path,
    relative_path: str,
    remainder: tuple[str, ...],
    *,
    size: int,
    provider: str,
    agent: str,
    root_kind: str,
) -> StorageEntry:
    name = remainder[-1]
    if name == '.claude.json':
        return _entry(
            path,
            relative_path,
            StorageClass.SECRET,
            size,
            provider=provider,
            agent=agent,
            reason='claude_trust_mcp_authority',
            root_kind=root_kind,
        )
    if remainder[:2] == ('Library', 'Keychains'):
        return _entry(path, relative_path, StorageClass.SECRET, size, provider=provider, agent=agent, reason='macos_keychain_link', root_kind=root_kind)
    if remainder[:3] == ('.local', 'share', 'claude') and len(remainder) >= 4 and remainder[3] == 'versions':
        is_active_version = _claude_version_active(path, remainder)
        return _entry(
            path,
            relative_path,
            StorageClass.REBUILDABLE_CACHE,
            size,
            provider=provider,
            agent=agent,
            active=False,
            is_active_version=is_active_version,
            reachable_from_current_symlink=is_active_version,
            reclaimable=False if is_active_version else None,
            reason='active_claude_version_cache' if is_active_version else 'claude_version_cache',
            root_kind=root_kind,
        )
    if remainder[:2] == ('.local', 'bin') and name == 'claude':
        return _entry(
            path,
            relative_path,
            StorageClass.REBUILDABLE_CACHE,
            size,
            provider=provider,
            agent=agent,
            active=True,
            is_active_version=False,
            reachable_from_current_symlink=True,
            reclaimable=False,
            reason='claude_current_binary_link',
            root_kind=root_kind,
        )
    if remainder[0] == '.claude' and len(remainder) >= 2 and remainder[1] in {'projects', 'session-env', 'tasks'}:
        return _entry(path, relative_path, StorageClass.SESSION, size, provider=provider, agent=agent, root_kind=root_kind)
    if remainder[0] == '.claude' and (name in _CLAUDE_PROJECTED_NAMES or (len(remainder) >= 2 and remainder[1] in {'skills', 'commands'})):
        return _entry(path, relative_path, StorageClass.PROJECTED_CONFIG, size, provider=provider, agent=agent, root_kind=root_kind)
    if remainder[0] in {'.cache', '.npm'}:
        return _entry(path, relative_path, StorageClass.REBUILDABLE_CACHE, size, provider=provider, agent=agent, root_kind=root_kind)
    return _entry(path, relative_path, StorageClass.UNKNOWN, size, provider=provider, agent=agent, root_kind=root_kind)


def _classify_gemini_home(
    path: Path,
    relative_path: str,
    remainder: tuple[str, ...],
    *,
    size: int,
    provider: str,
    agent: str,
    root_kind: str,
) -> StorageEntry:
    name = remainder[-1]
    if remainder[0] == '.gemini' and len(remainder) >= 2 and remainder[1] == 'tmp':
        return _entry(path, relative_path, StorageClass.SESSION, size, provider=provider, agent=agent, root_kind=root_kind)
    if remainder[0] == '.gemini' and name in _GEMINI_PROJECTED_NAMES:
        return _entry(path, relative_path, StorageClass.PROJECTED_CONFIG, size, provider=provider, agent=agent, root_kind=root_kind)
    if remainder[0] == '.npm':
        return _entry(path, relative_path, StorageClass.REBUILDABLE_CACHE, size, provider=provider, agent=agent, reason='npm_cache', root_kind=root_kind)
    if remainder[:2] == ('.cache', 'node-gyp') or remainder[:2] == ('.cache', 'vscode-ripgrep'):
        return _entry(path, relative_path, StorageClass.REBUILDABLE_CACHE, size, provider=provider, agent=agent, reason='tool_cache', root_kind=root_kind)
    if remainder[0] == '.gemini':
        return _entry(path, relative_path, StorageClass.SESSION, size, provider=provider, agent=agent, root_kind=root_kind)
    return _entry(path, relative_path, StorageClass.UNKNOWN, size, provider=provider, agent=agent, root_kind=root_kind)


def _classify_opencode_home(
    path: Path,
    relative_path: str,
    remainder: tuple[str, ...],
    *,
    size: int,
    provider: str,
    agent: str,
    root_kind: str,
) -> StorageEntry:
    name = remainder[-1]
    if name in _OPENCODE_PROJECTED_NAMES:
        return _entry(path, relative_path, StorageClass.PROJECTED_CONFIG, size, provider=provider, agent=agent, root_kind=root_kind)
    if remainder[0] in {'.cache', '.tmp'}:
        return _entry(path, relative_path, StorageClass.REBUILDABLE_CACHE, size, provider=provider, agent=agent, root_kind=root_kind)
    return _entry(path, relative_path, StorageClass.UNKNOWN, size, provider=provider, agent=agent, root_kind=root_kind)


def _classify_kimi_home(
    path: Path,
    relative_path: str,
    remainder: tuple[str, ...],
    *,
    size: int,
    provider: str,
    agent: str,
    root_kind: str,
) -> StorageEntry:
    if remainder[0] in {'inherited-skills', 'role-skills', 'overlay-skills'}:
        return _entry(path, relative_path, StorageClass.PROJECTED_CONFIG, size, provider=provider, agent=agent, root_kind=root_kind)
    return _entry(path, relative_path, StorageClass.UNKNOWN, size, provider=provider, agent=agent, root_kind=root_kind)


def _classify_mimo_home(
    path: Path,
    relative_path: str,
    remainder: tuple[str, ...],
    *,
    size: int,
    provider: str,
    agent: str,
    root_kind: str,
) -> StorageEntry:
    name = remainder[-1]
    if name in _MIMO_PROJECTED_NAMES:
        return _entry(path, relative_path, StorageClass.PROJECTED_CONFIG, size, provider=provider, agent=agent, root_kind=root_kind)
    if remainder[0] in {'data', 'state'}:
        return _entry(path, relative_path, StorageClass.SESSION, size, provider=provider, agent=agent, root_kind=root_kind)
    if remainder[0] == 'cache':
        return _entry(path, relative_path, StorageClass.REBUILDABLE_CACHE, size, provider=provider, agent=agent, root_kind=root_kind)
    return _entry(path, relative_path, StorageClass.UNKNOWN, size, provider=provider, agent=agent, root_kind=root_kind)


def _classify_droid_home(
    path: Path,
    relative_path: str,
    remainder: tuple[str, ...],
    *,
    size: int,
    provider: str,
    agent: str,
    root_kind: str,
) -> StorageEntry:
    if remainder[0] == '.factory' and len(remainder) > 1:
        remainder = remainder[1:]
    if remainder[0] == 'sessions':
        return _entry(path, relative_path, StorageClass.SESSION, size, provider=provider, agent=agent, root_kind=root_kind)
    if remainder[0] in {'skills', 'plugins'}:
        return _entry(path, relative_path, StorageClass.PROJECTED_CONFIG, size, provider=provider, agent=agent, root_kind=root_kind)
    return _entry(path, relative_path, StorageClass.UNKNOWN, size, provider=provider, agent=agent, root_kind=root_kind)


def _classify_copilot_home(
    path: Path,
    relative_path: str,
    remainder: tuple[str, ...],
    *,
    size: int,
    provider: str,
    agent: str,
    root_kind: str,
) -> StorageEntry:
    name = remainder[-1]
    if name == 'config.json':
        return _entry(
            path,
            relative_path,
            StorageClass.SECRET,
            size,
            provider=provider,
            agent=agent,
            reason='copilot_mixed_auth_application_state',
            root_kind=root_kind,
        )
    if name == _COPILOT_PROJECTION_MARKER_NAME or remainder[0] == 'installed-plugins':
        return _entry(
            path,
            relative_path,
            StorageClass.PROJECTED_CONFIG,
            size,
            provider=provider,
            agent=agent,
            reason='copilot_installed_plugin_projection',
            root_kind=root_kind,
        )
    if remainder[0] in {'mcp-oauth-config', 'mcp-secrets'}:
        return _entry(
            path,
            relative_path,
            StorageClass.SECRET,
            size,
            provider=provider,
            agent=agent,
            reason='copilot_secret_state',
            root_kind=root_kind,
        )
    if len(remainder) >= 2 and remainder[:2] == ('data', 'cache'):
        return _entry(
            path,
            relative_path,
            StorageClass.REBUILDABLE_CACHE,
            size,
            provider=provider,
            agent=agent,
            reason='copilot_agent_local_cache',
            root_kind=root_kind,
        )
    return _classify_native_cli_home(
        path,
        relative_path,
        remainder,
        size=size,
        provider=provider,
        agent=agent,
        root_kind=root_kind,
    )


def _classify_native_cli_home(
    path: Path,
    relative_path: str,
    remainder: tuple[str, ...],
    *,
    size: int,
    provider: str,
    agent: str,
    root_kind: str,
) -> StorageEntry:
    name = remainder[-1]
    if provider == 'dsh' and (
        remainder[0] == 'skills'
        or name in {'AGENTS.md', 'settings.yaml'}
    ):
        return _entry(
            path,
            relative_path,
            StorageClass.PROJECTED_CONFIG,
            size,
            provider=provider,
            agent=agent,
            reason='dsh_managed_projection',
            root_kind=root_kind,
        )
    if (
        provider == 'grok'
        and len(remainder) >= 3
        and remainder[:2] == ('.grok', 'skills')
        and remainder[2] in {'ask', 'ccb-clear', 'ccb-compact', 'ccb-diagnose'}
    ):
        return _entry(path, relative_path, StorageClass.PROJECTED_CONFIG, size, provider=provider, agent=agent, root_kind=root_kind)
    if provider in {'qoder', 'qoderclicn'} and remainder[0] == 'skills':
        return _entry(
            path,
            relative_path,
            StorageClass.PROJECTED_CONFIG,
            size,
            provider=provider,
            agent=agent,
            reason='qoder_skill_projection',
            root_kind=root_kind,
        )
    if remainder[0] in _NATIVE_CLI_PROJECTED_ROOTS:
        return _entry(path, relative_path, StorageClass.PROJECTED_CONFIG, size, provider=provider, agent=agent, root_kind=root_kind)
    if remainder[0] in _NATIVE_CLI_CACHE_ROOTS:
        return _entry(path, relative_path, StorageClass.REBUILDABLE_CACHE, size, provider=provider, agent=agent, root_kind=root_kind)
    if (
        remainder[0] in _NATIVE_CLI_SESSION_ROOTS
        or name.endswith(('.db', '.jsonl', '.log', '.sqlite', '.sqlite-shm', '.sqlite-wal'))
    ):
        return _entry(
            path,
            relative_path,
            StorageClass.SESSION,
            size,
            provider=provider,
            agent=agent,
            reason='native_cli_provider_state',
            root_kind=root_kind,
        )
    return _entry(
        path,
        relative_path,
        StorageClass.SESSION,
        size,
        provider=provider,
        agent=agent,
        reason='native_cli_provider_owned_state',
        root_kind=root_kind,
    )


def _entry(
    path: Path,
    relative_path: str,
    storage_class: StorageClass,
    size: int,
    *,
    provider: str | None = None,
    agent: str | None = None,
    active: bool | None = None,
    is_active_version: bool | None = None,
    reachable_from_current_symlink: bool | None = None,
    reclaimable: bool | None = None,
    reason: str | None = None,
    root_kind: str,
) -> StorageEntry:
    return StorageEntry(
        path=path,
        relative_path=relative_path,
        storage_class=storage_class,
        size_bytes=size,
        provider=provider,
        agent=agent,
        active=active,
        is_active_version=is_active_version,
        reachable_from_current_symlink=reachable_from_current_symlink,
        reclaimable=reclaimable,
        reason=reason,
        root_kind=root_kind,
    )


def _claude_version_active(path: Path, remainder: tuple[str, ...]) -> bool:
    if len(remainder) < 6:
        return False
    version = remainder[4]
    home = _provider_home_from_remainder(path, remainder)
    if home is None:
        return False
    link = home / '.local' / 'bin' / 'claude'
    try:
        target = link.resolve(strict=True)
    except Exception:
        return False
    try:
        return target.relative_to(home / '.local' / 'share' / 'claude' / 'versions' / version) is not None
    except Exception:
        return False


def _provider_home_from_remainder(path: Path, remainder: tuple[str, ...]) -> Path | None:
    try:
        current = path
        for _part in remainder:
            current = current.parent
        return current
    except Exception:
        return None


__all__ = ['classify_provider_home']
