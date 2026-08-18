from __future__ import annotations

import getpass
import hashlib
import json
import os
import platform
from pathlib import Path
import shutil
import stat
import subprocess
import unicodedata

from provider_core.memory_projection import (
    memory_projection_result,
    record_memory_projection_event,
    text_file_sha256,
)
from provider_core.one_way_inheritance import (
    copy_regular_file,
    ensure_private_descendant_directory,
    ensure_private_directory,
    ensure_private_inheritance_directory,
)
from provider_core.inherited_skills import (
    materialize_required_control_skills,
    required_control_skill_names,
    route_inherited_skill_entries,
)
from provider_core.projected_assets import (
    remove_projected_path,
    route_projected_tree,
    seed_projected_tree,
)
from provider_core.projected_settings import rebase_json_path_fields
from provider_core.source_home import current_provider_source_home
from provider_profiles import provider_api_env_keys
from rolepacks.projection import project_role_skills_to_home
from cli.services.role_command_policy import (
    claude_permission_allowlist,
    role_command_policy_disables_inherited_assets,
    role_command_policy_requires_enforcement,
)
from project_memory import (
    ensure_project_memory,
    load_memory_sources,
    read_memory_source,
    render_memory_bundle,
)
from project_memory.hashing import sha256_text
from storage.atomic import atomic_write_text

from ..home_layout import ClaudeHomeLayout, claude_layout_for_home, claude_layout_from_session_data
from .session_paths import read_session_payload, session_file_for_runtime_dir, state_dir_for_runtime_dir

_CLAUDE_RUNTIME_SETTINGS_KEYS = ('enabledPlugins', 'hooks', 'permissions')
_CLAUDE_CCB_PERMISSION_PREFIX = 'Bash(ccb '
_CLAUDE_AUTH_ENV_KEYS = ('ANTHROPIC_AUTH_TOKEN',)
_CLAUDE_API_AUTH_ENV_KEYS = ('ANTHROPIC_API_KEY',)
_CLAUDE_ROUTE_ENV_KEYS = ('ANTHROPIC_BASE_URL',)
_CLAUDE_HOME_HOOK_ASSET_DIRS = ('.codeisland',)
_CLAUDE_JSON_AUTH_METADATA_KEYS = ('oauthAccount',)
_CLAUDE_JSON_AUTH_SECRET_KEYS = ('primaryApiKey',)
_CLAUDE_JSON_AUTH_COMPANION_KEYS = (
    'hasCompletedOnboarding',
    'lastOnboardingVersion',
    'hasAvailableSubscription',
    'subscriptionNoticeCount',
)
_CLAUDE_JSON_MCP_ROOT_KEYS = ('mcpServers',)
_CLAUDE_JSON_MCP_PROJECT_KEYS = (
    'mcpServers',
    'enabledMcpjsonServers',
    'disabledMcpjsonServers',
    'disabledMcpServers',
    'mcpContextUris',
)
_MACOS_KEYCHAIN_CLAUDE_SERVICES = ('Claude Code-credentials', 'Claude Code-custom-oauth', 'Claude Code')
_CLAUDE_SKILLS_PROJECTION_LABEL = 'claude-inherited-skills'
_CLAUDE_COMMANDS_PROJECTION_LABEL = 'claude-inherited-commands'
_CLAUDE_PLUGIN_SEED_ENV = 'CLAUDE_CODE_PLUGIN_SEED_DIR'
_CLAUDE_PLUGIN_CACHE_ENV = 'CLAUDE_CODE_PLUGIN_CACHE_DIR'
_CLAUDE_PLUGIN_SETTINGS_KEYS = ('enabledPlugins', 'extraKnownMarketplaces')
_CLAUDE_PLUGIN_BOOTSTRAP_LABEL = 'claude-plugin-cache-bootstrap'
_CLAUDE_RESTRICTED_PLUGIN_ROOT = 'ccb-restricted-plugins'
_CLAUDE_EMPTY_PLUGIN_SEED = 'ccb-empty-plugin-seed'
_CLAUDE_EMPTY_PLUGIN_ROOT = 'ccb-empty-plugins'
_CLAUDE_PLUGIN_PATH_KEYS = ('installLocation', 'installPath')
_CLAUDE_AUTH_PROJECTION_MANIFEST = '.ccb-auth-projection.json'


def resolve_claude_home_layout(runtime_dir: Path, profile) -> ClaudeHomeLayout:
    explicit_runtime_home = _profile_runtime_home(profile)
    if explicit_runtime_home is not None:
        return claude_layout_for_home(explicit_runtime_home)

    managed_home = _managed_isolated_home(runtime_dir)
    existing = _existing_layout(runtime_dir, managed_home=managed_home)
    if existing is not None:
        return existing

    return claude_layout_for_home(managed_home)


def prepare_claude_home_overrides(
    runtime_dir: Path,
    profile,
    *,
    source_home: Path | None = None,
    refresh_home: bool = True,
    auto_permission: bool = False,
    project_root: Path | None = None,
    agent_name: str | None = None,
    workspace_path: Path | None = None,
    memory_projection_event_path: Path | None = None,
    memory_projection_marker_path: Path | None = None,
    command_policy=None,
) -> dict[str, str]:
    layout = resolve_claude_home_layout(runtime_dir, profile)
    source_root = (
        Path(source_home).expanduser()
        if source_home is not None
        else _system_home_root()
    )
    if refresh_home:
        materialize_claude_home_config(
            layout.home_root,
            profile=profile,
            source_home=source_root,
            project_root=project_root,
            agent_name=agent_name,
            workspace_path=workspace_path,
            auto_permission=auto_permission,
            command_policy=command_policy,
            memory_projection_event_path=memory_projection_event_path,
            memory_projection_marker_path=memory_projection_marker_path,
        )
    materialize_required_control_skills(
        provider='claude',
        target_dir=layout.claude_dir / 'skills',
    )
    overrides = {
        'HOME': str(layout.home_root),
        'CLAUDE_CONFIG_DIR': str(layout.claude_dir),
        # Claude's macOS secure-storage service name is scoped from this path.
        # Pinning it to the agent-private config root prevents refreshes from
        # updating the user's ordinary "Claude Code-credentials" entry.
        'CLAUDE_SECURESTORAGE_CONFIG_DIR': str(layout.claude_dir),
        'CLAUDE_PROJECTS_ROOT': str(layout.projects_root),
        'CLAUDE_PROJECT_ROOT': str(layout.projects_root),
        'CLAUDE_SESSION_ENV_ROOT': str(layout.session_env_root),
    }
    overrides.update(
        _claude_plugin_environment(
            source_root,
            layout,
            profile=profile,
            command_policy=command_policy,
        )
    )

    if "WSL_DISTRO_NAME" in os.environ:
        # We are running inside WSL. The target claude executable might be a Windows binary (via interop).
        # We must set USERPROFILE (which Windows Node.js uses as home) to the same isolated path.
        # WSLENV translates path variables with /p and forwards Claude API env names as raw values
        # when invoking a Windows executable. Linux executables will ignore WSLENV.
        overrides['USERPROFILE'] = str(layout.home_root)
        wslenv_additions = (
            "HOME/p:USERPROFILE/p:CLAUDE_CONFIG_DIR/p:"
            "CLAUDE_SECURESTORAGE_CONFIG_DIR/p:"
            "CLAUDE_PROJECTS_ROOT/p:CLAUDE_PROJECT_ROOT/p:CLAUDE_SESSION_ENV_ROOT/p:"
            "CLAUDE_CODE_PLUGIN_SEED_DIR/p:CLAUDE_CODE_PLUGIN_CACHE_DIR/p:"
            "ANTHROPIC_AUTH_TOKEN:ANTHROPIC_API_KEY:ANTHROPIC_BASE_URL:"
            "DISABLE_LOGIN_COMMAND:DISABLE_LOGOUT_COMMAND:DISABLE_AUTOUPDATER"
        )
        existing_wslenv = os.environ.get("WSLENV", "")
        if existing_wslenv:
            overrides['WSLENV'] = f"{wslenv_additions}:{existing_wslenv}"
        else:
            overrides['WSLENV'] = wslenv_additions

    return overrides


def _claude_plugin_environment(
    source_home: Path,
    target_layout: ClaudeHomeLayout,
    *,
    profile,
    command_policy,
) -> dict[str, str]:
    inherited_plugins_enabled = (
        _inherits_config(profile)
        and not role_command_policy_disables_inherited_assets(command_policy)
    )
    source_seed_root = Path(source_home).expanduser() / '.claude' / 'plugins'
    empty_seed_root = target_layout.claude_dir / _CLAUDE_EMPTY_PLUGIN_SEED
    source_seed_usable = inherited_plugins_enabled and _usable_claude_plugin_seed(source_seed_root)
    seed_root = source_seed_root if source_seed_usable else empty_seed_root
    normal_plugin_root = target_layout.claude_dir / 'plugins'
    if not inherited_plugins_enabled:
        plugin_root = target_layout.claude_dir / _CLAUDE_RESTRICTED_PLUGIN_ROOT
    elif source_seed_usable or normal_plugin_root.exists() or normal_plugin_root.is_symlink():
        plugin_root = normal_plugin_root
    else:
        plugin_root = target_layout.claude_dir / _CLAUDE_EMPTY_PLUGIN_ROOT
    try:
        if source_seed_usable and _claude_plugin_root_is_bootstrap_safe(plugin_root):
            if plugin_root.exists():
                shutil.rmtree(plugin_root)
            seeded = seed_projected_tree(
                source_seed_root,
                plugin_root,
                label=_CLAUDE_PLUGIN_BOOTSTRAP_LABEL,
            )
            if seeded and not rebase_json_path_fields(
                (
                    plugin_root / 'installed_plugins.json',
                    plugin_root / 'known_marketplaces.json',
                ),
                source_root=source_seed_root,
                target_root=plugin_root,
                fields=_CLAUDE_PLUGIN_PATH_KEYS,
            ):
                remove_projected_path(plugin_root, label=_CLAUDE_PLUGIN_BOOTSTRAP_LABEL)
        plugin_root.mkdir(parents=True, exist_ok=True)
        (plugin_root / 'cache').mkdir(parents=True, exist_ok=True)
        empty_seed_root.mkdir(parents=True, exist_ok=True)
    except Exception:
        return {}
    return {
        _CLAUDE_PLUGIN_SEED_ENV: str(seed_root),
        _CLAUDE_PLUGIN_CACHE_ENV: str(plugin_root),
    }


def _claude_plugin_root_is_bootstrap_safe(plugin_root: Path) -> bool:
    if plugin_root.is_symlink():
        return False
    if not plugin_root.exists():
        return True
    if not plugin_root.is_dir():
        return False
    try:
        return not any(entry.is_symlink() or not entry.is_dir() for entry in plugin_root.rglob('*'))
    except Exception:
        return False


def _usable_claude_plugin_seed(seed_root: Path) -> bool:
    return seed_root.is_dir() and any(
        (
            (seed_root / 'known_marketplaces.json').is_file(),
            (seed_root / 'marketplaces').is_dir(),
            (seed_root / 'cache').is_dir(),
        )
    )


def materialize_claude_home_config(
    target_home: Path,
    *,
    profile=None,
    source_home: Path | None = None,
    project_root: Path | None = None,
    agent_name: str | None = None,
    workspace_path: Path | None = None,
    auto_permission: bool = False,
    command_policy=None,
    extra_env: dict[str, str] | None = None,
    memory_projection_event_path: Path | None = None,
    memory_projection_marker_path: Path | None = None,
) -> ClaudeHomeLayout:
    layout = claude_layout_for_home(Path(target_home).expanduser())
    source_root = Path(source_home).expanduser() if source_home is not None else _system_home_root()
    memory_result = _prepare_managed_home(
        source_root,
        layout,
        profile=profile,
        project_root=project_root,
        agent_name=agent_name,
        workspace_path=workspace_path,
        auto_permission=auto_permission,
        command_policy=command_policy,
        extra_env=extra_env,
    )
    record_memory_projection_event(
        memory_result,
        provider='claude',
        event_path=memory_projection_event_path,
        marker_path=memory_projection_marker_path,
        agent_name=agent_name,
    )
    return layout


def _profile_runtime_home(profile) -> Path | None:
    del profile
    return None


def _existing_layout(runtime_dir: Path, *, managed_home: Path) -> ClaudeHomeLayout | None:
    session_file = session_file_for_runtime_dir(runtime_dir)
    if session_file is None or not session_file.is_file():
        return None
    data = read_session_payload(session_file)
    if not isinstance(data, dict):
        return None
    layout = claude_layout_from_session_data(data)
    if layout is None:
        return None
    return layout if _is_within_home_root(layout.home_root, managed_home) else None


def _managed_isolated_home(runtime_dir: Path) -> Path:
    state_dir = state_dir_for_runtime_dir(runtime_dir)
    if state_dir is not None:
        return state_dir / 'home'
    return Path(runtime_dir).expanduser() / 'claude-home'


def _is_within_home_root(candidate: Path, managed_home: Path) -> bool:
    normalized_candidate = _normalize_path(candidate)
    normalized_managed = _normalize_path(managed_home)
    if normalized_candidate is None or normalized_managed is None:
        return False
    try:
        normalized_candidate.relative_to(normalized_managed)
        return True
    except Exception:
        return False


def _normalize_path(value: object) -> Path | None:
    try:
        return Path(value).expanduser().resolve()
    except Exception:
        try:
            return Path(value).expanduser()
        except Exception:
            return None


def _prepare_managed_home(
    source_home: Path,
    target_layout: ClaudeHomeLayout,
    *,
    profile,
    project_root: Path | None,
    agent_name: str | None,
    workspace_path: Path | None,
    auto_permission: bool,
    command_policy,
    extra_env: dict[str, str] | None = None,
) -> dict[str, object]:
    ensure_private_inheritance_directory(target_layout.home_root, source_home)
    ensure_private_descendant_directory(target_layout.home_root, Path('.claude'))
    ensure_private_descendant_directory(target_layout.home_root, Path('.claude') / 'projects')
    ensure_private_descendant_directory(target_layout.home_root, Path('.claude') / 'session-env')
    ensure_private_descendant_directory(target_layout.home_root, Path('.config') / 'claude-code')

    _materialize_settings(
        source_home,
        target_layout,
        profile=profile,
        auto_permission=auto_permission,
        command_policy=command_policy,
        extra_env=extra_env,
    )
    _materialize_macos_keychain_preferences(source_home, target_layout, profile=profile)
    _materialize_auth(source_home, target_layout, profile=profile)
    _materialize_trust(
        source_home,
        target_layout,
        profile=profile,
        project_root=project_root,
        workspace_path=workspace_path,
        auto_permission=auto_permission,
    )
    return _materialize_inherited_assets(
        source_home,
        target_layout,
        profile=profile,
        project_root=project_root,
        agent_name=agent_name,
        workspace_path=workspace_path,
        command_policy=command_policy,
    )


def _materialize_inherited_assets(
    source_home: Path,
    target_layout: ClaudeHomeLayout,
    *,
    profile,
    project_root: Path | None,
    agent_name: str | None,
    workspace_path: Path | None,
    command_policy,
) -> dict[str, object]:
    inherited_assets_enabled = not role_command_policy_disables_inherited_assets(command_policy)
    _route_inherited_tree(
        source_home / '.claude' / 'commands',
        target_layout.claude_dir / 'commands',
        enabled=inherited_assets_enabled and _inherits_commands(profile),
        label=_CLAUDE_COMMANDS_PROJECTION_LABEL,
    )
    route_inherited_skill_entries(
        source_home / '.claude' / 'skills',
        target_layout.claude_dir / 'skills',
        enabled=inherited_assets_enabled and _inherits_skills(profile),
        label=_CLAUDE_SKILLS_PROJECTION_LABEL,
        exclude=required_control_skill_names('claude'),
    )
    project_role_skills_to_home(
        project_root=project_root,
        agent_name=agent_name,
        provider='claude',
        target_skills_dir=target_layout.claude_dir / 'skills',
    )
    materialize_required_control_skills(
        provider='claude',
        target_dir=target_layout.claude_dir / 'skills',
    )
    memory_result = _materialize_claude_memory(
        source_home,
        target_layout,
        profile=profile,
        project_root=project_root,
        agent_name=agent_name,
        workspace_path=workspace_path,
    )
    _materialize_home_hook_assets(source_home, target_layout, profile=profile)
    return memory_result


def _materialize_claude_memory(
    source_home: Path,
    target_layout: ClaudeHomeLayout,
    *,
    profile,
    project_root: Path | None,
    agent_name: str | None,
    workspace_path: Path | None,
) -> dict[str, object]:
    target = target_layout.claude_dir / 'CLAUDE.md'
    if not _inherits_memory(profile):
        _remove_file(target)
        return memory_projection_result(
            status='skipped',
            reason='inherit_memory_disabled',
            path=target,
        )
    if project_root is None or agent_name is None:
        return memory_projection_result(
            status='failed',
            reason='missing_project_context',
            path=target,
        )
    root = Path(project_root).expanduser()
    try:
        warnings: list[str] = []
        ensure_result = ensure_project_memory(root)
        if ensure_result.warning:
            warnings.append(ensure_result.warning)
        extra_sources = tuple(
            source
            for source in (
                read_memory_source(
                    kind='provider_user_memory',
                    title='Provider User Memory',
                    path=source_home / '.claude' / 'CLAUDE.md',
                    include_missing=False,
                ),
            )
            if source is not None
        )
        sources = load_memory_sources(
            root,
            agent_name=agent_name,
            provider='claude',
            extra_sources=extra_sources,
        )
        warnings.extend(source.warning for source in sources if source.warning)
        rendered = render_memory_bundle(
            project_root=root,
            agent_name=agent_name,
            provider='claude',
            sources=sources,
            workspace_path=workspace_path,
        )
        digest = sha256_text(rendered)
        if text_file_sha256(target) == digest:
            return memory_projection_result(
                status='skipped',
                reason='unchanged',
                path=target,
                sha256=digest,
                source_count=len(sources),
                warnings=warnings,
            )
        atomic_write_text(target, rendered)
        return memory_projection_result(
            status='ok',
            reason='written',
            path=target,
            sha256=digest,
            source_count=len(sources),
            warnings=warnings,
        )
    except Exception as exc:
        return memory_projection_result(
            status='failed',
            reason=type(exc).__name__,
            path=target,
            error_detail=str(exc),
        )

def _materialize_home_hook_assets(source_home: Path, target_layout: ClaudeHomeLayout, *, profile) -> None:
    if not _inherits_config(profile):
        return
    source_settings = _read_json_object(source_home / '.claude' / 'settings.json')
    hooks_payload = source_settings.get('hooks')
    if not isinstance(hooks_payload, dict):
        return
    for dirname in _CLAUDE_HOME_HOOK_ASSET_DIRS:
        if _payload_mentions_home_asset(hooks_payload, dirname):
            _sync_tree(source_home / dirname, target_layout.home_root / dirname)


def _materialize_settings(
    source_home: Path,
    target_layout: ClaudeHomeLayout,
    *,
    profile,
    auto_permission: bool = False,
    command_policy=None,
    extra_env: dict[str, str] | None = None,
) -> None:
    payload = _projected_settings_payload(
        source_home / '.claude' / 'settings.json',
        profile=profile,
        extra_env=extra_env,
    )
    existing = _read_json_object(target_layout.settings_path)
    previous_projection = _read_claude_auth_projection(target_layout)
    merged = _merge_settings_payload(
        payload,
        existing=existing,
        profile=profile,
        auto_permission=auto_permission,
        command_policy=command_policy,
        source_owned_auth_env_keys=_manifest_string_set(
            previous_projection,
            'projected_env_keys',
        ),
    )
    if merged is None:
        return
    _rewrite_tilde_paths(merged, source_home=source_home)
    atomic_write_text(
        target_layout.settings_path,
        json.dumps(merged, ensure_ascii=False, indent=2) + '\n',
    )


def _materialize_trust(
    source_home: Path,
    target_layout: ClaudeHomeLayout,
    *,
    profile,
    project_root: Path | None,
    workspace_path: Path | None,
    auto_permission: bool = False,
) -> None:
    source_trust = source_home / '.claude.json'
    profile_servers = _profile_mcp_servers(profile)
    custom_api_key = _claude_custom_api_key_from_settings(target_layout.settings_path)
    if (
        source_trust.is_file()
        or target_layout.trust_path.exists()
        or target_layout.legacy_trust_path.exists()
        or profile_servers
        or auto_permission
        or _env_value_present(custom_api_key)
    ):
        # CCB 8.4.3 exported CLAUDE_CONFIG_DIR but continued writing this state
        # at HOME/.claude.json.  Claude writes its own partial state at
        # CLAUDE_CONFIG_DIR/.claude.json, so merge both authorities during the
        # migration.  The active CLI state wins on conflicts while missing
        # onboarding, trust, and MCP fields survive from the legacy file.
        existing = _merge_json_objects(
            _read_json_object(target_layout.legacy_trust_path),
            _read_json_object(target_layout.trust_path),
        )
        merged = _projected_claude_json_payload(
            _read_json_object(source_trust) if source_trust.is_file() else {},
            existing=existing,
            profile=profile,
            project_root=project_root,
            workspace_path=workspace_path,
        )
        if auto_permission:
            merged['bypassPermissionsModeAccepted'] = True
            _ensure_project_permission_acceptance(
                merged,
                project_root=project_root,
                workspace_path=workspace_path,
            )
        _approve_claude_custom_api_key(merged, custom_api_key)
        _write_json_object(target_layout.trust_path, merged)
        _remove_file(target_layout.legacy_trust_path)
    _ensure_trust_file(target_layout.trust_path)


def _merge_json_objects(
    base: dict[str, object],
    overlay: dict[str, object],
) -> dict[str, object]:
    merged = {key: _clone_jsonish(value) for key, value in base.items()}
    for key, value in overlay.items():
        previous = merged.get(key)
        if isinstance(previous, dict) and isinstance(value, dict):
            merged[key] = _merge_json_objects(previous, value)
        else:
            merged[key] = _clone_jsonish(value)
    return merged


def _materialize_auth(source_home: Path, target_layout: ClaudeHomeLayout, *, profile) -> None:
    previous = _read_claude_auth_projection(target_layout)
    previous_files = _manifest_string_set(previous, 'projected_files')
    credentials_name = _relative_to_home(
        target_layout.credentials_path,
        target_layout.home_root,
    )
    previous_credentials_payload = (
        _read_owned_json_projection(target_layout.credentials_path)
        if credentials_name in previous_files
        else None
    )
    if not _inherits_external_auth(profile):
        for _source_auth, target_auth in _source_auth_paths(source_home, target_layout):
            name = _relative_to_home(target_auth, target_layout.home_root)
            if name in previous_files:
                _remove_file(target_auth)
        if credentials_name in previous_files:
            _remove_managed_macos_keychain_auth(target_layout)
        _write_claude_auth_projection(
            target_layout,
            source_home=source_home,
            projected_files=(),
            projected_env_keys=(),
            status=(
                'explicit_api_authority'
                if _profile_has_explicit_credential(profile)
                else 'inherit_auth_disabled'
            ),
        )
        return

    projected_files: set[str] = set()
    for source_auth, target_auth in _source_auth_paths(source_home, target_layout):
        name = _relative_to_home(target_auth, target_layout.home_root)
        copied = _copy_claude_auth_source(source_auth, target_auth)
        if copied:
            projected_files.add(name)
        elif name in previous_files and name != credentials_name:
            _remove_file(target_auth)

    keychain_projected = _materialize_macos_keychain_auth(
        target_layout,
        preserve_existing=(
            credentials_name not in previous_files
            or credentials_name in projected_files
        ),
        previous_projected_payload=previous_credentials_payload,
        previous_projection_owned=credentials_name in previous_files,
    )
    if keychain_projected:
        projected_files.add(credentials_name)
    elif credentials_name in previous_files and credentials_name not in projected_files:
        _remove_file(target_layout.credentials_path)

    projected_env_keys = _source_auth_env_keys(source_home, profile=profile)
    had_source_projection = bool(previous_files or _manifest_string_set(previous, 'projected_env_keys'))
    if projected_files or projected_env_keys:
        status = 'inherited_auth'
    elif had_source_projection:
        status = 'source_auth_absent'
    else:
        status = 'agent_private_or_unmanaged'
    _write_claude_auth_projection(
        target_layout,
        source_home=source_home,
        projected_files=tuple(sorted(projected_files)),
        projected_env_keys=tuple(sorted(projected_env_keys)),
        status=status,
    )


def _materialize_macos_keychain_preferences(source_home: Path, target_layout: ClaudeHomeLayout, *, profile) -> None:
    del source_home, profile
    target = target_layout.home_root / 'Library' / 'Preferences' / 'com.apple.security.plist'
    target_keychains = target_layout.home_root / 'Library' / 'Keychains'
    # Older CCB releases linked this path back to the user's real Keychains
    # directory.  That made a managed provider logout capable of mutating the
    # external login authority.  Credential inheritance is now copy-only, so
    # detach any legacy link before doing anything else.
    _remove_keychains_link(target_keychains)
    # A copied preference file can itself point Security.framework back to the
    # user's global keychain database, so remove legacy copies as well.
    _remove_file(target)


def _remove_keychains_link(path: Path) -> None:
    try:
        if path.is_symlink():
            path.unlink()
    except Exception:
        pass


def _projected_claude_json_payload(
    source_payload: dict[str, object],
    *,
    existing: dict[str, object],
    profile=None,
    project_root: Path | None = None,
    workspace_path: Path | None = None,
) -> dict[str, object]:
    merged = dict(existing or {})
    for key in _CLAUDE_JSON_AUTH_SECRET_KEYS:
        merged.pop(key, None)

    if _inherits_config(profile):
        _project_claude_mcp_config(
            source_payload,
            merged,
            project_root=project_root,
            workspace_path=workspace_path,
        )
    else:
        _strip_claude_mcp_config(
            merged,
            project_root=project_root,
            workspace_path=workspace_path,
        )

    _merge_profile_mcp_servers(merged, profile=profile)

    if not _inherits_auth(profile):
        for key in _CLAUDE_JSON_AUTH_METADATA_KEYS:
            merged.pop(key, None)
        return merged

    for key in (*_CLAUDE_JSON_AUTH_METADATA_KEYS, *_CLAUDE_JSON_AUTH_COMPANION_KEYS):
        if key in source_payload:
            merged[key] = _clone_jsonish(source_payload[key])
    return merged


def _project_claude_mcp_config(
    source_payload: dict[str, object],
    merged: dict[str, object],
    *,
    project_root: Path | None,
    workspace_path: Path | None,
) -> None:
    for key in _CLAUDE_JSON_MCP_ROOT_KEYS:
        value = source_payload.get(key)
        if isinstance(value, dict):
            merged[key] = _clone_jsonish(value)
        else:
            merged.pop(key, None)

    target_key = _claude_project_target_key(project_root=project_root, workspace_path=workspace_path)
    if not target_key:
        return

    selected = _selected_source_project_mcp_config(
        source_payload,
        project_root=project_root,
        workspace_path=workspace_path,
    )
    _refresh_project_mcp_record(merged, target_key=target_key, selected=selected)


def _merge_profile_mcp_servers(merged: dict[str, object], *, profile) -> None:
    profile_servers = _profile_mcp_servers(profile)
    if not profile_servers:
        return

    existing = merged.get('mcpServers')
    servers = dict(existing) if isinstance(existing, dict) else {}
    for raw_name, raw_config in profile_servers.items():
        name = str(raw_name or '').strip()
        if not name:
            continue
        if _mcp_server_disabled(raw_config):
            servers.pop(name, None)
            continue
        payload = _clone_jsonish(raw_config)
        if not isinstance(payload, dict):
            continue
        payload.pop('enabled', None)
        servers[name] = payload

    if servers:
        merged['mcpServers'] = servers
    else:
        merged.pop('mcpServers', None)


def _profile_mcp_servers(profile) -> dict[str, object]:
    if profile is None:
        return {}
    raw = getattr(profile, 'mcp_servers', None)
    return dict(raw) if isinstance(raw, dict) else {}


def _mcp_server_disabled(value: object) -> bool:
    return isinstance(value, dict) and value.get('enabled') is False


def _strip_claude_mcp_config(
    merged: dict[str, object],
    *,
    project_root: Path | None,
    workspace_path: Path | None,
) -> None:
    for key in _CLAUDE_JSON_MCP_ROOT_KEYS:
        merged.pop(key, None)
    target_key = _claude_project_target_key(project_root=project_root, workspace_path=workspace_path)
    if target_key:
        _refresh_project_mcp_record(merged, target_key=target_key, selected={})


def _selected_source_project_mcp_config(
    source_payload: dict[str, object],
    *,
    project_root: Path | None,
    workspace_path: Path | None,
) -> dict[str, object]:
    for key in _claude_project_source_keys(project_root=project_root, workspace_path=workspace_path):
        selected: dict[str, object] = {}
        for record in _source_project_records(source_payload, key):
            for mcp_key in _CLAUDE_JSON_MCP_PROJECT_KEYS:
                if mcp_key in record:
                    selected[mcp_key] = _clone_jsonish(record[mcp_key])
        if selected:
            return selected
    return {}


def _refresh_project_mcp_record(
    merged: dict[str, object],
    *,
    target_key: str,
    selected: dict[str, object],
) -> None:
    projects = merged.get('projects')
    if not isinstance(projects, dict):
        projects = {}
    else:
        projects = dict(projects)

    project_record = _project_record_copy(projects.get(target_key))
    top_record = _project_record_copy(merged.get(target_key))
    _strip_project_mcp_keys(project_record)
    _strip_project_mcp_keys(top_record)

    for key, value in selected.items():
        project_record[key] = _clone_jsonish(value)
        top_record[key] = _clone_jsonish(value)

    if project_record:
        projects[target_key] = project_record
    else:
        projects.pop(target_key, None)

    if projects:
        merged['projects'] = projects
    else:
        merged.pop('projects', None)

    if top_record:
        merged[target_key] = top_record
    else:
        merged.pop(target_key, None)


def _ensure_project_permission_acceptance(
    merged: dict[str, object],
    *,
    project_root: Path | None,
    workspace_path: Path | None,
) -> None:
    target_key = _claude_project_target_key(project_root=project_root, workspace_path=workspace_path)
    if not target_key:
        return

    projects = merged.get('projects')
    if not isinstance(projects, dict):
        projects = {}
    else:
        projects = dict(projects)

    project_record = _project_record_copy(projects.get(target_key))
    top_record = _project_record_copy(merged.get(target_key))
    for record in (project_record, top_record):
        record['hasTrustDialogAccepted'] = True
        if not isinstance(record.get('allowedTools'), list):
            record['allowedTools'] = []

    projects[target_key] = project_record
    merged['projects'] = projects
    merged[target_key] = top_record


def _project_record_copy(value: object) -> dict[str, object]:
    return dict(value) if isinstance(value, dict) else {}


def _strip_project_mcp_keys(record: dict[str, object]) -> None:
    for key in _CLAUDE_JSON_MCP_PROJECT_KEYS:
        record.pop(key, None)


def _source_project_records(source_payload: dict[str, object], key: str) -> tuple[dict[str, object], ...]:
    records: list[dict[str, object]] = []
    top_record = source_payload.get(key)
    if isinstance(top_record, dict):
        records.append(top_record)
    projects = source_payload.get('projects')
    if isinstance(projects, dict):
        project_record = projects.get(key)
        if isinstance(project_record, dict):
            records.append(project_record)
    return tuple(records)


def _claude_project_target_key(
    *,
    project_root: Path | None,
    workspace_path: Path | None,
) -> str | None:
    for candidate in (workspace_path, project_root):
        key = _claude_path_key(candidate)
        if key:
            return key
    return None


def _claude_project_source_keys(
    *,
    project_root: Path | None,
    workspace_path: Path | None,
) -> tuple[str, ...]:
    keys: list[str] = []
    for candidate in (workspace_path, project_root):
        for key in _claude_path_key_candidates(candidate):
            if key and key not in keys:
                keys.append(key)
    return tuple(keys)


def _claude_path_key_candidates(value: Path | None) -> tuple[str, ...]:
    if value is None:
        return ()
    keys: list[str] = []
    path = Path(value).expanduser()
    for candidate in (path, _normalize_path(path)):
        if candidate is None:
            continue
        key = str(candidate)
        if key and key not in keys:
            keys.append(key)
    return tuple(keys)


def _claude_path_key(value: Path | None) -> str | None:
    candidates = _claude_path_key_candidates(value)
    return candidates[-1] if candidates else None


def _clone_jsonish(value: object) -> object:
    try:
        return json.loads(json.dumps(value, ensure_ascii=False))
    except Exception:
        return value


def _materialize_macos_keychain_auth(
    target_layout: ClaudeHomeLayout,
    *,
    preserve_existing: bool = True,
    previous_projected_payload: dict[str, object] | None = None,
    previous_projection_owned: bool = False,
) -> bool:
    try:
        source_payload = _read_macos_keychain_claude_credentials()
    except RuntimeError:
        # A previously projected credential must not be mistaken for an
        # authoritative external logout when Keychain cannot be read.
        if not preserve_existing:
            raise
        return False
    payload = source_payload
    if source_payload:
        _write_json_object(target_layout.credentials_path, source_payload, mode=0o600)
    elif platform.system() == 'Darwin':
        if not preserve_existing:
            _remove_file(target_layout.credentials_path)
            _remove_managed_macos_keychain_auth(target_layout)
            return False
        payload = _read_json_object(target_layout.credentials_path)
    if not payload or not isinstance(payload.get('claudeAiOauth'), dict):
        return False
    refresh_existing = bool(
        previous_projection_owned
        and payload != previous_projected_payload
    )
    # Claude may refresh its private Keychain item after launch. Replace it
    # only when the CCB-owned source projection changed between launches.
    _sync_managed_macos_keychain_auth(
        target_layout,
        payload,
        refresh_existing=refresh_existing,
    )
    return source_payload is not None


def _read_macos_keychain_claude_credentials() -> dict[str, object] | None:
    if platform.system() != 'Darwin':
        return None
    security = shutil.which('security') or '/usr/bin/security'
    account = _macos_keychain_account()
    if not account:
        return None

    errors: list[str] = []
    for service in _macos_keychain_services():
        try:
            result = subprocess.run(
                [security, 'find-generic-password', '-a', account, '-s', service, '-w'],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception as exc:
            errors.append(f'{service}: {type(exc).__name__}')
            continue
        if result.returncode == 44:
            continue
        if result.returncode != 0:
            errors.append(f'{service}: exit {result.returncode}')
            continue
        payload = _json_object_from_text(result.stdout)
        if isinstance(payload.get('claudeAiOauth'), dict):
            return payload
        errors.append(f'{service}: invalid credential payload')
    if errors:
        raise RuntimeError(
            'cannot determine external Claude Keychain login state: '
            + '; '.join(errors)
        )
    return None


def _macos_keychain_account() -> str:
    account = str(os.environ.get('USER') or '').strip()
    if account:
        return account
    try:
        return str(getpass.getuser() or '').strip()
    except Exception:
        return ''


def _macos_keychain_services() -> tuple[str, ...]:
    services = list(_MACOS_KEYCHAIN_CLAUDE_SERVICES)
    custom_service = 'Claude Code-custom-oauth'
    if os.environ.get('CLAUDE_CODE_CUSTOM_OAUTH_URL') and custom_service in services:
        services.remove(custom_service)
        services.insert(1, custom_service)
    # Allow callers to bind a CCB stack to a specific keychain entry,
    # e.g. when isolating multiple Claude accounts on one machine.
    override = os.environ.get('CCB_KEYCHAIN_SERVICE_OVERRIDE')
    if override:
        services.insert(0, override)
    return tuple(services)


def _managed_macos_keychain_service(target_layout: ClaudeHomeLayout) -> str:
    storage_root = unicodedata.normalize('NFC', str(target_layout.claude_dir))
    suffix = hashlib.sha256(storage_root.encode('utf-8')).hexdigest()[:8]
    base = (
        'Claude Code-custom-oauth'
        if os.environ.get('CLAUDE_CODE_CUSTOM_OAUTH_URL')
        else 'Claude Code-credentials'
    )
    return f'{base}-{suffix}'


def _sync_managed_macos_keychain_auth(
    target_layout: ClaudeHomeLayout,
    payload: dict[str, object],
    *,
    refresh_existing: bool = False,
) -> None:
    if platform.system() != 'Darwin':
        return
    security = shutil.which('security') or '/usr/bin/security'
    account = _macos_keychain_account()
    if not account:
        raise RuntimeError('cannot isolate Claude login: macOS Keychain account is unavailable')
    service = _managed_macos_keychain_service(target_layout)
    if service in _macos_keychain_services():
        raise RuntimeError('refusing to overwrite the external Claude Keychain login')
    try:
        existing = subprocess.run(
            [security, 'find-generic-password', '-a', account, '-s', service, '-w'],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:
        raise RuntimeError(f'cannot inspect agent-private Claude Keychain login: {exc}') from exc
    if existing.returncode == 0:
        if not refresh_existing:
            return
        existing_payload = _json_object_from_text(existing.stdout)
        if existing_payload == payload:
            return
    elif existing.returncode != 44:
        raise RuntimeError('cannot inspect agent-private Claude Keychain login')
    credential_text = json.dumps(payload, ensure_ascii=False, separators=(',', ':'))
    try:
        result = subprocess.run(
            [
                security,
                'add-generic-password',
                '-U',
                '-a',
                account,
                '-s',
                service,
                '-w',
                credential_text,
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception as exc:
        raise RuntimeError(f'cannot seed agent-private Claude Keychain login: {exc}') from exc
    if result.returncode != 0:
        raise RuntimeError('cannot seed agent-private Claude Keychain login')


def _remove_managed_macos_keychain_auth(target_layout: ClaudeHomeLayout) -> None:
    if platform.system() != 'Darwin':
        return
    security = shutil.which('security') or '/usr/bin/security'
    account = _macos_keychain_account()
    if not account:
        return
    service = _managed_macos_keychain_service(target_layout)
    if service in _macos_keychain_services():
        return
    try:
        subprocess.run(
            [security, 'delete-generic-password', '-a', account, '-s', service],
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        pass


def _projected_settings_payload(
    source_settings_path: Path,
    *,
    profile,
    extra_env: dict[str, str] | None = None,
) -> dict[str, object] | None:
    source_payload = _read_source_json_object(source_settings_path, label='Claude settings')
    if not source_payload:
        return {} if _needs_settings_stub(profile) else None

    env_payload = dict(source_payload.get('env') or {}) if isinstance(source_payload.get('env'), dict) else {}
    # Explicit `agents.<name>.env` keys are exported separately through the
    # launcher shell prefix, so they must not be shadowed by the inherited
    # settings.json env block.
    for key in (extra_env or {}):
        if str(key).strip():
            env_payload.pop(str(key), None)
    if not _inherits_api(profile):
        for key in provider_api_env_keys('claude'):
            env_payload.pop(key, None)
    elif not _inherits_external_auth(profile):
        env_payload.pop('ANTHROPIC_AUTH_TOKEN', None)
        env_payload.pop('ANTHROPIC_API_KEY', None)
        # When the agent owns an explicit base URL, the inherited host route must
        # not win over it: the shell env already exports the agent's resolved
        # profile env, so drop the host's route key instead of shadowing it.
        profile_env = dict(getattr(profile, 'env', {}) or {})
        if _env_value_present(profile_env.get('ANTHROPIC_BASE_URL')):
            env_payload.pop('ANTHROPIC_BASE_URL', None)

    include_config = _inherits_config(profile)
    payload: dict[str, object] = {}
    if include_config:
        payload.update(source_payload)
    if env_payload:
        payload['env'] = env_payload
    else:
        payload.pop('env', None)
    if payload:
        return payload
    return {} if _needs_settings_stub(profile) else None


def _merge_settings_payload(
    projected: dict[str, object] | None,
    *,
    existing: dict[str, object],
    profile=None,
    auto_permission: bool = False,
    command_policy=None,
    source_owned_auth_env_keys: set[str] | None = None,
) -> dict[str, object] | None:
    existing_payload = dict(existing or {})
    projected_payload = dict(projected or {})
    merged = dict(projected_payload)
    _carry_forward_managed_auth_env(
        merged,
        existing_payload,
        profile=profile,
        source_owned_keys=source_owned_auth_env_keys or set(),
    )

    for key in _CLAUDE_RUNTIME_SETTINGS_KEYS:
        value = existing_payload.get(key)
        if value is not None:
            if key == 'enabledPlugins':
                enabled_plugins = _merge_enabled_plugins_payload(projected_payload.get('enabledPlugins'), value)
                if enabled_plugins:
                    merged[key] = enabled_plugins
                else:
                    merged.pop(key, None)
                continue
            if key == 'hooks':
                hooks = _merge_hooks_payload(projected_payload.get('hooks'), value)
                if hooks:
                    merged[key] = hooks
                else:
                    merged.pop(key, None)
                continue
            if key == 'permissions' and auto_permission and _is_ccb_only_permission_payload(value):
                continue
            merged[key] = value

    if role_command_policy_requires_enforcement(command_policy):
        allowlist = list(claude_permission_allowlist(command_policy))
        merged['permissions'] = {'allow': allowlist, 'deny': []}

    if (
        not _inherits_config(profile)
        or role_command_policy_disables_inherited_assets(command_policy)
    ):
        for key in _CLAUDE_PLUGIN_SETTINGS_KEYS:
            merged.pop(key, None)

    # Claude Code 1.0.43 still iterates the legacy top-level allowedTools
    # array even when the newer permissions.* schema is present.
    if not isinstance(merged.get('allowedTools'), list):
        merged['allowedTools'] = []

    if auto_permission:
        merged['skipDangerousModePermissionPrompt'] = True

    if merged:
        return merged
    if projected is not None:
        return {}
    return None


def _merge_enabled_plugins_payload(projected: object, existing: object) -> dict[str, object]:
    existing_plugins = _settings_mapping_copy(existing)
    projected_plugins = _settings_mapping_copy(projected)
    if not existing_plugins:
        return projected_plugins
    if not projected_plugins:
        return existing_plugins
    merged = dict(existing_plugins)
    merged.update(projected_plugins)
    return merged


def _merge_hooks_payload(projected: object, existing: object) -> dict[str, object]:
    projected_hooks = _settings_mapping_copy(projected)
    existing_hooks = _settings_mapping_copy(existing)
    if not projected_hooks:
        return existing_hooks
    if not existing_hooks:
        return projected_hooks

    merged = dict(projected_hooks)
    for event_name, existing_groups in existing_hooks.items():
        projected_groups = merged.get(event_name)
        if not isinstance(existing_groups, list):
            if event_name not in merged:
                merged[event_name] = _clone_jsonish(existing_groups)
            continue
        if not isinstance(projected_groups, list):
            if event_name in merged:
                continue
            merged[event_name] = [_clone_jsonish(group) for group in existing_groups]
            continue
        fingerprints = {_json_fingerprint(group) for group in projected_groups}
        groups = list(projected_groups)
        for group in existing_groups:
            fingerprint = _json_fingerprint(group)
            if fingerprint in fingerprints:
                continue
            groups.append(_clone_jsonish(group))
            fingerprints.add(fingerprint)
        merged[event_name] = groups
    return merged


def _settings_mapping_copy(value: object) -> dict[str, object]:
    return dict(_clone_jsonish(value)) if isinstance(value, dict) else {}


def _json_fingerprint(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    except Exception:
        return repr(value)


def _is_ccb_only_permission_payload(value: object) -> bool:
    if not isinstance(value, dict):
        return False
    allow = value.get('allow')
    if not isinstance(allow, list):
        return False
    normalized = tuple(str(item or '').strip() for item in allow if str(item or '').strip())
    if not normalized:
        return False
    if any(not item.startswith(_CLAUDE_CCB_PERMISSION_PREFIX) for item in normalized):
        return False
    deny = value.get('deny')
    return deny in (None, [])


def _carry_forward_managed_auth_env(
    merged_payload: dict[str, object],
    existing_payload: dict[str, object],
    *,
    profile=None,
    source_owned_keys: set[str] | None = None,
) -> None:
    if _profile_has_explicit_credential(profile):
        return
    existing_env = _read_env_payload(existing_payload)
    if not existing_env:
        return
    merged_env = _read_env_payload(merged_payload)
    if _has_projected_auth_authority(merged_env):
        return
    source_owned = set(source_owned_keys or ())

    preserved_any = False
    for key in _CLAUDE_AUTH_ENV_KEYS:
        if key in source_owned:
            continue
        value = existing_env.get(key)
        if _env_value_present(value):
            merged_env[key] = value
            preserved_any = True
    if _inherits_api(profile):
        for key in _CLAUDE_API_AUTH_ENV_KEYS:
            if key in source_owned:
                continue
            value = existing_env.get(key)
            if _env_value_present(value):
                merged_env[key] = value
                preserved_any = True
        if preserved_any:
            for key in _CLAUDE_ROUTE_ENV_KEYS:
                if _env_value_present(merged_env.get(key)):
                    continue
                value = existing_env.get(key)
                if _env_value_present(value):
                    merged_env[key] = value
        _drop_legacy_claude_api_key_alias(merged_env)

    if merged_env:
        merged_payload['env'] = merged_env
    else:
        merged_payload.pop('env', None)


def _read_env_payload(payload: dict[str, object]) -> dict[str, object]:
    env_payload = payload.get('env')
    return dict(env_payload) if isinstance(env_payload, dict) else {}


def _has_projected_auth_authority(env_payload: dict[str, object]) -> bool:
    return any(_env_value_present(env_payload.get(key)) for key in (*_CLAUDE_AUTH_ENV_KEYS, *_CLAUDE_API_AUTH_ENV_KEYS))


def _env_value_present(value: object) -> bool:
    if isinstance(value, str):
        return bool(value.strip())
    return value is not None


def _drop_legacy_claude_api_key_alias(env_payload: dict[str, object]) -> None:
    auth_token = env_payload.get('ANTHROPIC_AUTH_TOKEN')
    api_key = env_payload.get('ANTHROPIC_API_KEY')
    if _env_value_present(auth_token) and _env_value_present(api_key) and auth_token == api_key:
        env_payload.pop('ANTHROPIC_API_KEY', None)


def _claude_custom_api_key_from_settings(settings_path: Path) -> object:
    settings = _read_json_object(settings_path)
    env_payload = _read_env_payload(settings)
    return env_payload.get('ANTHROPIC_API_KEY')


def _approve_claude_custom_api_key(payload: dict[str, object], api_key: object) -> None:
    if not isinstance(api_key, str) or not api_key.strip():
        return
    key_suffix = api_key[-20:]
    responses = payload.get('customApiKeyResponses')
    if not isinstance(responses, dict):
        responses = {}
    approved = responses.get('approved')
    if not isinstance(approved, list):
        approved = []
    if key_suffix not in approved:
        approved = [*approved, key_suffix]
    responses['approved'] = approved
    payload['customApiKeyResponses'] = responses


def _needs_settings_stub(profile) -> bool:
    return bool(_inherits_api(profile) or _inherits_auth(profile) or _inherits_config(profile))


def _inherits_api(profile) -> bool:
    return True if profile is None else bool(getattr(profile, 'inherit_api', True))


def _inherits_auth(profile) -> bool:
    return True if profile is None else bool(getattr(profile, 'inherit_auth', True))


def _inherits_external_auth(profile) -> bool:
    return _inherits_auth(profile) and not _profile_has_explicit_credential(profile)


def _profile_has_explicit_credential(profile) -> bool:
    if profile is None:
        return False
    env = dict(getattr(profile, 'env', {}) or {})
    return any(
        _env_value_present(env.get(key))
        for key in (*_CLAUDE_AUTH_ENV_KEYS, *_CLAUDE_API_AUTH_ENV_KEYS)
    )


def _inherits_config(profile) -> bool:
    return True if profile is None else bool(getattr(profile, 'inherit_config', True))


def _inherits_skills(profile) -> bool:
    return True if profile is None else bool(getattr(profile, 'inherit_skills', True))


def _inherits_commands(profile) -> bool:
    return True if profile is None else bool(getattr(profile, 'inherit_commands', True))


def _inherits_memory(profile) -> bool:
    return True if profile is None else bool(getattr(profile, 'inherit_memory', True))


def _rewrite_tilde_paths(payload: dict[str, object], *, source_home: Path) -> None:
    """Replace ``~`` / ``~/`` prefixes in settings string values with *source_home*.

    In a managed pane ``HOME`` points to the agent's private home, so ``~`` in
    settings (e.g. ``statusLine.command`` or hook commands) would resolve to the
    wrong directory.  Rewriting to the real source home fixes the resolution
    without requiring the user to hard-code absolute paths.
    """
    source_root = str(Path(source_home).expanduser()).replace('\\', '/')
    _rewrite_tilde_in_place(payload, source_root)


def _rewrite_tilde_in_place(value: object, source_root: str) -> None:
    """Walk *value* (dict/list) and replace ``~``-prefixed strings in place."""
    if isinstance(value, dict):
        for key in list(value.keys()):
            child = value[key]
            if isinstance(child, str):
                value[key] = _replace_tilde_prefix(child, source_root)
            else:
                _rewrite_tilde_in_place(child, source_root)
    elif isinstance(value, list):
        for index in range(len(value)):
            child = value[index]
            if isinstance(child, str):
                value[index] = _replace_tilde_prefix(child, source_root)
            else:
                _rewrite_tilde_in_place(child, source_root)


def _replace_tilde_prefix(text: str, source_root: str) -> str:
    """Replace leading ``~`` or ``~/`` with *source_root*."""
    if not text or not source_root:
        return text
    if text == '~':
        return source_root
    if text.startswith('~/'):
        return f'{source_root}/{text[2:]}'
    return text


def _read_json_object(path: Path) -> dict[str, object]:
    try:
        data = _json_object_from_text(path.read_text(encoding='utf-8'))
    except Exception:
        return {}
    return data


def _read_owned_json_projection(path: Path) -> dict[str, object] | None:
    target = Path(path)
    try:
        metadata = target.lstat()
    except OSError:
        return None
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        return None
    try:
        payload = json.loads(target.read_text(encoding='utf-8'))
    except (OSError, UnicodeError, ValueError, TypeError):
        return None
    return payload if isinstance(payload, dict) else None


def _read_source_json_object(path: Path, *, label: str) -> dict[str, object]:
    source = Path(path).expanduser()
    try:
        metadata = source.lstat()
    except FileNotFoundError:
        return {}
    except OSError as exc:
        raise RuntimeError(f'cannot inspect inherited {label} source: {source}: {exc}') from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError(f'inherited {label} source must be a regular file: {source}')
    try:
        text = source.read_text(encoding='utf-8')
        data = json.loads(text)
    except (OSError, UnicodeError, ValueError, TypeError) as exc:
        raise RuntimeError(f'cannot read inherited {label} source: {source}: {exc}') from exc
    if not isinstance(data, dict):
        raise RuntimeError(f'inherited {label} source must contain an object: {source}')
    return data


def _json_object_from_text(value: str) -> dict[str, object]:
    try:
        data = json.loads(str(value or '').strip())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_json_object(path: Path, payload: dict[str, object], *, mode: int | None = None) -> None:
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=False, indent=2) + '\n',
    )
    if mode is not None:
        try:
            path.chmod(mode)
        except Exception:
            pass


def _payload_mentions_home_asset(value: object, dirname: str) -> bool:
    if isinstance(value, str):
        return any(
            marker in value
            for marker in (
                f'$HOME/{dirname}/',
                f'${{HOME}}/{dirname}/',
                f'~/{dirname}/',
            )
        )
    if isinstance(value, dict):
        return any(_payload_mentions_home_asset(child, dirname) for child in value.values())
    if isinstance(value, list):
        return any(_payload_mentions_home_asset(child, dirname) for child in value)
    return False


def _ensure_trust_file(path: Path) -> None:
    if path.exists() and not path.is_symlink():
        return
    atomic_write_text(path, '{}\n')


def _copy_if_missing(source: Path, target: Path) -> None:
    if target.exists() or not source.is_file():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(source, target)
    except Exception:
        pass


def _sync_file(source: Path, target: Path) -> None:
    if not source.is_file():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(source, target)
    except Exception:
        pass


def _remove_file(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass
    except Exception:
        pass


def _source_auth_paths(source_home: Path, target_layout: ClaudeHomeLayout) -> tuple[tuple[Path, Path], ...]:
    return (
        (source_home / '.claude' / '.credentials.json', target_layout.credentials_path),
        (source_home / '.config' / 'claude-code' / 'auth.json', target_layout.auth_path),
    )


def _copy_claude_auth_source(source: Path, target: Path) -> bool:
    """Copy a present auth file and distinguish absence from read failure."""
    source_path = Path(source).expanduser()
    try:
        metadata = source_path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise RuntimeError(
            f'cannot inspect inherited Claude auth source: {source_path}: {exc}'
        ) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError(
            f'inherited Claude auth source must be a regular file: {source_path}'
        )
    try:
        copied = copy_regular_file(source_path, target)
    except OSError as exc:
        raise RuntimeError(
            f'cannot copy inherited Claude auth source: {source_path}: {exc}'
        ) from exc
    if not copied:
        raise RuntimeError(f'cannot copy inherited Claude auth source: {source_path}')
    return True


def _source_auth_env_keys(source_home: Path, *, profile) -> set[str]:
    source_settings = _read_json_object(Path(source_home) / '.claude' / 'settings.json')
    env = _read_env_payload(source_settings)
    keys: set[str] = set()
    if _inherits_external_auth(profile):
        keys.update(
            key
            for key in _CLAUDE_AUTH_ENV_KEYS
            if _env_value_present(env.get(key))
        )
    if _inherits_api(profile):
        keys.update(
            key
            for key in _CLAUDE_API_AUTH_ENV_KEYS
            if _env_value_present(env.get(key))
        )
    return keys


def _read_claude_auth_projection(target_layout: ClaudeHomeLayout) -> dict[str, object]:
    path = target_layout.home_root / _CLAUDE_AUTH_PROJECTION_MANIFEST
    return _read_json_object(path)


def _manifest_string_set(payload: dict[str, object], key: str) -> set[str]:
    if not _valid_claude_auth_projection(payload):
        return set()
    raw = payload.get(key)
    if not isinstance(raw, list):
        return set()
    return {str(item).strip() for item in raw if str(item).strip()}


def _valid_claude_auth_projection(payload: dict[str, object]) -> bool:
    return bool(
        isinstance(payload, dict)
        and payload.get('schema_version') == 1
        and payload.get('record_type') == 'ccb_claude_auth_projection'
    )


def _relative_to_home(path: Path, home_root: Path) -> str:
    try:
        return str(Path(path).relative_to(Path(home_root)))
    except ValueError:
        return str(Path(path))


def _write_claude_auth_projection(
    target_layout: ClaudeHomeLayout,
    *,
    source_home: Path,
    projected_files: tuple[str, ...],
    projected_env_keys: tuple[str, ...],
    status: str,
) -> None:
    path = target_layout.home_root / _CLAUDE_AUTH_PROJECTION_MANIFEST
    payload = {
        'schema_version': 1,
        'record_type': 'ccb_claude_auth_projection',
        'status': str(status),
        'source_home': str(Path(source_home).expanduser()),
        'projected_files': list(projected_files),
        'projected_env_keys': list(projected_env_keys),
    }
    atomic_write_text(
        path,
        json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + '\n',
    )
    try:
        path.chmod(0o600)
    except OSError:
        pass


def _sync_tree(source: Path, target: Path) -> None:
    if not source.is_dir():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(source, target, dirs_exist_ok=True)
    except Exception:
        pass


def _route_inherited_tree(source: Path, target: Path, *, enabled: bool, label: str) -> None:
    route_projected_tree(source, target, enabled=enabled, label=label)


def _system_home_root() -> Path:
    return current_provider_source_home()


__all__ = ['materialize_claude_home_config', 'prepare_claude_home_overrides', 'resolve_claude_home_layout']
