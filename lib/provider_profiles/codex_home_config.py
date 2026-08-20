from __future__ import annotations

import fnmatch
import hashlib
import hmac
import importlib
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
import re
import shutil
import stat
import sys
import tempfile

from provider_core.memory_projection import (
    materialize_provider_memory_file,
    memory_projection_result,
    record_memory_projection_event,
)
from provider_core.one_way_inheritance import (
    copy_regular_file,
    ensure_private_directory,
    ensure_private_inheritance_directory,
)
from provider_core.inherited_skills import (
    materialize_required_control_skills,
    required_control_skill_names,
    route_inherited_skill_entries,
)
from provider_core.projected_assets import (
    copy_projected_tree_to_cache,
    projected_path_is_owned,
    remove_projected_path,
    route_projected_tree,
    seed_projected_tree,
    tree_content_fingerprint,
    write_projected_marker,
)
from provider_core.source_home import current_provider_source_home
from rolepacks.projection import project_role_skills_to_home
from project.ids import compute_project_id
from storage.atomic import atomic_write_text
from storage.paths import PathLayout


_CODEX_CUSTOM_PROVIDER_ID = 'custom'
_BARE_TOML_KEY_RE = re.compile(r'^[A-Za-z0-9_-]+$')
_CODEX_PLUGIN_TREE_RELATIVE = Path('.tmp') / 'plugins'
_CODEX_PLUGIN_SHA_RELATIVE = Path('.tmp') / 'plugins.sha'
_CODEX_SKILLS_PROJECTION_LABEL = 'codex-inherited-skills'
_CODEX_COMMANDS_PROJECTION_LABEL = 'codex-inherited-commands'
_CODEX_PLUGIN_PROJECTION_LABEL = 'codex-plugin-bundle'
_CODEX_AUTH_PROJECTION_MANIFEST = '.ccb-auth-projection.json'
_CODEX_AUTH_SIDECAR_FILENAMES = (
    'company-codex-api-key',
    'company-codex.config.toml',
)
_CODEX_MODEL_CATALOG_JSON_KEY = 'model_catalog_json'
_CODEX_AUTH_SIDECAR_REF_RE = re.compile(
    r'(?:'
    r'\$\{CODEX_HOME(?::-[^}]*)?\}'
    r'|\$CODEX_HOME'
    r'|\$\{HOME(?::-[^}]*)?\}/\.codex'
    r'|\$HOME/\.codex'
    r'|~/\.codex'
    r')/(?P<name>[A-Za-z0-9][A-Za-z0-9_.-]{0,127})'
)
_CODEX_LEGACY_OWNED_SKILL_NAMES = ('ccb_config', 'ccb-config')
_CODEX_PLUGIN_REQUIRED_RELATIVE_PATHS = (
    Path('.agents') / 'plugins' / 'marketplace.json',
    Path('.agents') / 'skills',
    Path('plugins'),
)
_MANAGED_CODEX_DISABLED_FEATURES = ('external_migration',)
_CODEX_AUTHORITY_ROUTE_KEYS = (
    'chatgpt_base_url',
    'oss_provider',
)
_CODEX_AUTHORITY_LOGIN_KEYS = (
    'preferred_auth_method',
    'forced_login_method',
    'forced_chatgpt_workspace_id',
)
_CODEX_DEFAULT_INHERITED_HOOK_EVENTS = frozenset(
    {
        'SessionStart',
        'UserPromptSubmit',
        'PreToolUse',
        'PostToolUse',
        'PreCompact',
        'PostCompact',
        'Stop',
    }
)
_CODEX_CONFIGURED_MARKER_DEFAULT_HOOK_EVENTS = frozenset({'SessionStart', 'UserPromptSubmit', 'Stop'})
_CODEX_DEFAULT_INHERITED_COMMAND_HOOK_MARKERS = (
    '.hindsight/codex/scripts/',
    'oh-my-codex/dist/scripts/codex-native-hook.js',
    'omx-native-hook-windows-shim.ps1',
)
_CODEX_INHERITED_HOOK_EVENTS_ENV = 'CCB_CODEX_INHERITED_HOOK_EVENTS'
_CODEX_INHERITED_COMMAND_HOOK_MARKERS_ENV = 'CCB_CODEX_INHERITED_COMMAND_HOOK_MARKERS'
_CODEX_COMMAND_HOOK_DEFAULT_TIMEOUT_S = 600
_MANAGED_CODEX_STARTUP_UPDATE_KEY = 'check_for_update_on_startup'
_TOML_TABLE_HEADER_RE = re.compile(r'^\s*\[{1,2}[^\]]+\]{1,2}\s*(?:#.*)?$')


@dataclass(frozen=True)
class CodexApiAuthority:
    provider_id: str
    base_url: str
    wire_api: str = 'responses'
    requires_openai_auth: bool = False


@dataclass(frozen=True)
class CodexAuthRefreshResult:
    refreshed: bool
    detail: str
    changed_files: tuple[str, ...] = ()


def materialize_codex_home_config(
    target_home: Path,
    *,
    profile=None,
    source_home: Path | None = None,
    project_root: Path | None = None,
    agent_name: str | None = None,
    runtime_dir: Path | None = None,
    workspace_path: Path | None = None,
    shared_cache_root: Path | None = None,
    command_policy=None,
    memory_projection_event_path: Path | None = None,
    memory_projection_marker_path: Path | None = None,
    model: str | None = None,
    model_catalog_json: str | None = None,
) -> Path:
    target_home = Path(target_home).expanduser()
    source_home = Path(source_home).expanduser() if source_home is not None else _system_codex_home()
    source_config = source_home / 'config.toml'
    _preflight_codex_auth_sources(source_home, source_config=source_config, profile=profile)
    target_home = ensure_private_inheritance_directory(target_home, source_home)
    ensure_private_directory(target_home / 'sessions')

    target_config = target_home / 'config.toml'
    if target_config.is_symlink():
        target_config.unlink()
    authority = codex_api_authority(profile)
    inherited_assets_enabled = not _role_command_policy_disables_inherited_assets(command_policy)

    if authority is not None:
        _write_codex_api_authority_config(
            target_config,
            authority,
            profile=profile,
            source_config=source_config,
            project_root=project_root,
            workspace_path=workspace_path,
        )
    elif _inherits_config(profile) and _inherits_api(profile) and _source_config_valid(source_config):
        if source_config.is_file():
            payload = _read_source_config_payload(source_config)
            if payload or _profile_mcp_servers(profile) or _profile_plugins(profile):
                _write_managed_codex_config(
                    target_config,
                    payload,
                    profile=profile,
                    project_root=project_root,
                    workspace_path=workspace_path,
                )
            else:
                _sync_file(source_config, target_config)
                _append_managed_codex_feature_overrides(target_config)
                _append_managed_codex_project_trust(target_config, project_root=project_root, workspace_path=workspace_path)
        else:
            _write_managed_config_stub(
                target_config,
                profile=profile,
                project_root=project_root,
                workspace_path=workspace_path,
            )
    else:
        _write_managed_config_stub(
            target_config,
            profile=profile,
            project_root=project_root,
            workspace_path=workspace_path,
        )

    _install_role_command_mcp_server(
        target_config,
        command_policy=command_policy,
        project_root=project_root,
        agent_name=agent_name,
        runtime_dir=runtime_dir,
    )
    configured_model_catalog = (
        model_catalog_json
        if model_catalog_json is not None
        else _profile_env(profile).get(_CODEX_MODEL_CATALOG_JSON_KEY)
    )
    model_catalog_name = _codex_model_catalog_sidecar_name(
        configured_model_catalog or _read_source_config_payload(target_config).get(_CODEX_MODEL_CATALOG_JSON_KEY)
    )
    if configured_model_catalog and model_catalog_name is None:
        raise RuntimeError(
            f'Codex {_CODEX_MODEL_CATALOG_JSON_KEY} must reference a safe .json file name'
        )
    if model_catalog_name is not None:
        _set_codex_model_catalog_json(target_config, model_catalog_name)
        _materialize_config_sidecars(
            source_home,
            target_home,
            required_names=(model_catalog_name,),
        )
    if model is not None:
        _set_codex_model(target_config, model)

    previous_auth_projection = _read_auth_projection_manifest(target_home)
    _materialize_auth_file(
        source_home / 'auth.json',
        target_home / 'auth.json',
        profile=profile,
        previous_projection=previous_auth_projection,
    )
    _materialize_auth_sidecars(
        source_home,
        target_home,
        source_config=source_config,
        profile=profile,
        authority=authority,
        previous_projection=previous_auth_projection,
    )
    _materialize_inherited_skills(
        source_home / 'skills',
        target_home / 'skills',
        profile=profile,
        enabled=inherited_assets_enabled,
    )
    if inherited_assets_enabled:
        _materialize_skill_overlays(
            target_home / 'skills',
            profile=profile,
            project_root=project_root,
        )
    project_role_skills_to_home(
        project_root=project_root,
        agent_name=agent_name,
        provider='codex',
        target_skills_dir=target_home / 'skills',
    )
    materialize_required_control_skills(
        provider='codex',
        target_dir=target_home / 'skills',
    )
    _route_inherited_tree(
        source_home / 'commands',
        target_home / 'commands',
        enabled=_inherits_commands(profile) and inherited_assets_enabled,
        label=_CODEX_COMMANDS_PROJECTION_LABEL,
    )
    _sync_codex_plugin_projection(
        source_home,
        target_home,
        enabled=inherited_assets_enabled,
        project_root=project_root,
        shared_cache_root=shared_cache_root,
    )
    for relative in (Path('.tmp') / 'marketplaces', Path('plugins') / 'cache'):
        seed_projected_tree(
            source_home / relative,
            target_home / relative,
            enabled=inherited_assets_enabled,
            label=_CODEX_PLUGIN_PROJECTION_LABEL,
        )
    memory_result = _materialize_codex_memory(
        source_home,
        target_home,
        profile=profile,
        project_root=project_root,
        agent_name=agent_name,
        workspace_path=workspace_path,
    )
    if inherited_assets_enabled:
        _install_codex_inherited_hooks(
            target_home,
            target_config,
            source_home=source_home,
        )
    _bind_source_test_command_path(target_config, project_root=project_root)
    record_memory_projection_event(
        memory_result,
        provider='codex',
        event_path=memory_projection_event_path,
        marker_path=memory_projection_marker_path,
        agent_name=agent_name,
    )
    return target_config


def _bind_source_test_command_path(
    target_config: Path,
    *,
    project_root: Path | None,
) -> None:
    if os.environ.get('CCB_TEST_ENTRYPOINT') != '1' or project_root is None:
        return
    command_dir = Path(project_root).expanduser().resolve() / '.ccb' / 'bin'
    if not all(
        (command_dir / name).is_file()
        for name in ('ask', 'ccb', 'codex-reconnect')
    ):
        return
    payload = _read_source_config_payload(target_config)
    raw_policy = payload.get('shell_environment_policy')
    policy = _clone_mapping(raw_policy) if isinstance(raw_policy, dict) else {}
    raw_configured = policy.get('set')
    configured = _clone_mapping(raw_configured) if isinstance(raw_configured, dict) else {}
    inherited_path = str(configured.get('PATH') or os.environ.get('PATH') or '')
    path_parts = [str(command_dir)]
    path_parts.extend(
        item
        for item in inherited_path.split(os.pathsep)
        if item and item != str(command_dir)
    )
    configured['PATH'] = os.pathsep.join(path_parts)
    policy['set'] = configured
    payload['shell_environment_policy'] = policy
    target_config.write_text(_render_toml_document(payload), encoding='utf-8')


def repair_codex_activity_hooks(
    target_home: Path,
    *,
    source_home: Path | None = None,
    project_root: Path | None,
    agent_name: str | None,
    runtime_dir: Path | None,
    workspace_path: Path | None,
) -> None:
    target_home = Path(target_home).expanduser()
    source_home = Path(source_home).expanduser() if source_home is not None else _system_codex_home()
    del project_root, agent_name, runtime_dir, workspace_path
    _install_codex_inherited_hooks(
        target_home,
        target_home / 'config.toml',
        source_home=source_home,
    )


def codex_api_authority(profile) -> CodexApiAuthority | None:
    env = _profile_env(profile)
    base_url = env.get('OPENAI_BASE_URL') or env.get('OPENAI_API_BASE') or ''
    if not base_url:
        return None
    return CodexApiAuthority(
        provider_id=_CODEX_CUSTOM_PROVIDER_ID,
        base_url=base_url,
    )


def codex_provider_authority_fingerprint(profile) -> str | None:
    authority = codex_api_authority(profile)
    if authority is None:
        return None
    payload = {
        'provider_id': authority.provider_id,
        'base_url': authority.base_url,
        'wire_api': authority.wire_api,
        'requires_openai_auth': authority.requires_openai_auth,
    }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return hashlib.sha256(encoded).hexdigest()[:16]


def codex_source_authority_config_payload(
    config_path: Path,
    *,
    include_route: bool = True,
    include_login: bool = True,
) -> dict[str, object]:
    """Project only Codex config dimensions that select an authority.

    Model, UI, hook, MCP, and feature edits must not rotate a conversation
    generation.  Route/provider selection and explicit account-login knobs do
    affect which remote authority a stopped restart will use.
    """
    source = Path(config_path).expanduser()
    if not _source_config_valid(source):
        raise RuntimeError(f'Codex authority config is invalid: {source}')
    payload = _read_source_config_payload(source)
    selected: dict[str, object] = {}
    if include_route:
        provider_id = str(payload.get('model_provider') or 'openai').strip() or 'openai'
        selected['model_provider'] = provider_id
        providers = payload.get('model_providers')
        if isinstance(providers, dict):
            provider_payload = providers.get(provider_id)
            if isinstance(provider_payload, dict):
                selected['model_provider_config'] = _clone_mapping(provider_payload)
        for key in _CODEX_AUTHORITY_ROUTE_KEYS:
            if key in payload:
                selected[key] = _clone_payload(payload[key])
    if include_login:
        for key in _CODEX_AUTHORITY_LOGIN_KEYS:
            if key in payload:
                selected[key] = _clone_payload(payload[key])
    return selected


def codex_auth_sidecar_names(source_home: Path, source_config: Path | None = None) -> tuple[str, ...]:
    """Return safe auth sidecars referenced by the source Codex config."""
    root = Path(source_home).expanduser()
    config = Path(source_config).expanduser() if source_config is not None else root / 'config.toml'
    return tuple(sorted(_codex_auth_sidecar_names(root, config)))


def refresh_codex_auth_projection(
    target_home: Path,
    *,
    profile=None,
    source_home: Path | None = None,
) -> CodexAuthRefreshResult:
    """Refresh only inherited Codex authentication after a revoked-token crash."""
    target_home = Path(target_home).expanduser()
    source_home = Path(source_home).expanduser() if source_home is not None else _system_codex_home()
    if codex_api_authority(profile) is not None:
        return CodexAuthRefreshResult(
            False,
            'Codex authentication uses explicit API authority; inherited auth was not changed',
        )
    if not _inherits_auth(profile):
        return CodexAuthRefreshResult(
            False,
            'Codex provider profile has inherit_auth=false; local auth was preserved',
        )
    if _same_path(source_home, target_home):
        return CodexAuthRefreshResult(False, 'Codex authentication source and managed home are the same path')

    source_auth = source_home / 'auth.json'
    if not _valid_codex_auth_file(source_auth):
        return CodexAuthRefreshResult(
            False,
            f'Inherited Codex authentication is missing or invalid: {source_auth}',
        )

    source_config = source_home / 'config.toml'
    sidecar_names = tuple(
        name
        for name in sorted(_codex_auth_sidecar_names(source_home, source_config))
        if (source_home / name).is_file()
    )
    projected_names = ('auth.json', *sidecar_names)
    changed_names = tuple(
        name
        for name in projected_names
        if _file_sha256(source_home / name) != _file_sha256(target_home / name)
    )
    if not changed_names:
        return CodexAuthRefreshResult(
            False,
            'Inherited Codex authentication is unchanged; run `codex login` in the source profile before remounting',
        )

    try:
        for name in changed_names:
            _atomic_sync_secret_file(source_home / name, target_home / name)
        _write_auth_projection_manifest(
            source_home,
            target_home,
            projected_files=projected_names,
            projected_sidecars=sidecar_names,
            profile=profile,
            status='inherited_auth_recovered',
        )
    except Exception as exc:
        return CodexAuthRefreshResult(False, f'Failed to refresh inherited Codex authentication: {exc}')
    return CodexAuthRefreshResult(
        True,
        f'Refreshed inherited Codex authentication files: {", ".join(changed_names)}',
        changed_files=changed_names,
    )


def _inherits_api(profile) -> bool:
    return True if profile is None else bool(getattr(profile, 'inherit_api', True))


def _inherits_auth(profile) -> bool:
    return True if profile is None else bool(getattr(profile, 'inherit_auth', True))


def _inherits_external_auth(profile) -> bool:
    return _inherits_auth(profile) and not bool(_explicit_api_key(profile))


def _inherits_config(profile) -> bool:
    return True if profile is None else bool(getattr(profile, 'inherit_config', True))


def _inherits_skills(profile) -> bool:
    return True if profile is None else bool(getattr(profile, 'inherit_skills', True))


def _inherits_commands(profile) -> bool:
    return True if profile is None else bool(getattr(profile, 'inherit_commands', True))


def _inherits_memory(profile) -> bool:
    return True if profile is None else bool(getattr(profile, 'inherit_memory', True))


def _profile_env(profile) -> dict[str, str]:
    if profile is None:
        return {}
    return {
        str(key): str(value).strip()
        for key, value in dict(getattr(profile, 'env', {}) or {}).items()
        if str(value).strip()
    }


def _explicit_api_key(profile) -> str:
    return _profile_env(profile).get('OPENAI_API_KEY', '')


def _preflight_codex_auth_sources(
    source_home: Path,
    *,
    source_config: Path,
    profile,
) -> None:
    reads_source_config = bool(
        _inherits_config(profile)
        or _inherits_api(profile)
        or _inherits_external_auth(profile)
    )
    if reads_source_config and _probe_regular_source_file(source_config):
        if not _source_config_valid(source_config):
            raise RuntimeError(
                f'cannot parse inherited Codex config source: {source_config}'
            )
    if not _inherits_external_auth(profile):
        return
    names = {'auth.json', *_codex_auth_sidecar_names(source_home, source_config)}
    for name in sorted(names):
        _probe_regular_source_file(Path(source_home) / name)


def _probe_regular_source_file(path: Path) -> bool:
    source = Path(path).expanduser()
    try:
        metadata = source.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise RuntimeError(
            f'cannot inspect inherited Codex source: {source}: {exc}'
        ) from exc
    if stat.S_ISLNK(metadata.st_mode) or not stat.S_ISREG(metadata.st_mode):
        raise RuntimeError(
            f'inherited Codex source must be a regular file: {source}'
        )
    try:
        source.read_bytes()
    except OSError as exc:
        raise RuntimeError(
            f'cannot read inherited Codex source: {source}: {exc}'
        ) from exc
    return True


def _write_codex_api_authority_config(
    target: Path,
    authority: CodexApiAuthority,
    *,
    profile,
    source_config: Path,
    project_root: Path | None,
    workspace_path: Path | None,
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = _managed_codex_config_payload(source_config, authority=authority)
    _merge_codex_plugin_overrides(payload, profile=profile)
    _merge_codex_mcp_server_overrides(payload, profile=profile)
    _trust_managed_codex_project_paths(payload, project_root=project_root, workspace_path=workspace_path)
    target.write_text(_render_toml_document(payload), encoding='utf-8')


def _write_managed_config_stub(
    target: Path,
    *,
    profile,
    project_root: Path | None,
    workspace_path: Path | None,
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = _disable_interactive_migration_features({})
    _merge_codex_plugin_overrides(payload, profile=profile)
    _merge_codex_mcp_server_overrides(payload, profile=profile)
    _trust_managed_codex_project_paths(payload, project_root=project_root, workspace_path=workspace_path)
    rendered = _render_toml_document(payload)
    target.write_text(rendered, encoding='utf-8')


def _write_managed_codex_config(
    target: Path,
    payload: dict[str, object],
    *,
    profile,
    project_root: Path | None,
    workspace_path: Path | None,
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    sanitized = _disable_interactive_migration_features(payload)
    _strip_unmanaged_hook_config(sanitized)
    _merge_codex_plugin_overrides(sanitized, profile=profile)
    _merge_codex_mcp_server_overrides(sanitized, profile=profile)
    _trust_managed_codex_project_paths(sanitized, project_root=project_root, workspace_path=workspace_path)
    target.write_text(_render_toml_document(sanitized), encoding='utf-8')


def _append_managed_codex_feature_overrides(target: Path) -> None:
    if not target.is_file():
        return
    try:
        text = target.read_text(encoding='utf-8')
    except Exception:
        return
    merged = _merge_managed_codex_startup_update_override(text)
    target.write_text(_merge_managed_codex_feature_overrides(merged), encoding='utf-8')


def _append_managed_codex_project_trust(target: Path, *, project_root: Path | None, workspace_path: Path | None) -> None:
    if not target.is_file():
        return
    try:
        text = target.read_text(encoding='utf-8')
    except Exception:
        return
    target.write_text(
        _merge_managed_codex_project_trust(text, project_root=project_root, workspace_path=workspace_path),
        encoding='utf-8',
    )


def _merge_managed_codex_feature_overrides(text: str) -> str:
    lines = text.splitlines()
    features_index = _find_toml_table_index(lines, 'features')
    override_lines = [f'{feature_name} = false' for feature_name in _MANAGED_CODEX_DISABLED_FEATURES]

    if features_index is None:
        merged = [text.rstrip(), '', '[features]', *override_lines]
        return '\n'.join(merged).lstrip('\n') + '\n'

    section_end = _toml_table_end(lines, features_index + 1)
    disabled = set(_MANAGED_CODEX_DISABLED_FEATURES)
    section_lines = [
        line
        for line in lines[features_index + 1 : section_end]
        if _toml_key_name(line) not in disabled
    ]
    insert_at = len(section_lines)
    while insert_at > 0 and not section_lines[insert_at - 1].strip():
        insert_at -= 1
    section_lines[insert_at:insert_at] = override_lines
    merged_lines = [*lines[: features_index + 1], *section_lines, *lines[section_end:]]
    return '\n'.join(merged_lines).rstrip() + '\n'


def _merge_managed_codex_startup_update_override(text: str) -> str:
    lines = text.splitlines()
    first_table = next(
        (index for index, line in enumerate(lines) if _TOML_TABLE_HEADER_RE.match(line)),
        len(lines),
    )
    prefix = [
        line
        for line in lines[:first_table]
        if _toml_key_name(line) != _MANAGED_CODEX_STARTUP_UPDATE_KEY
    ]
    insert_at = len(prefix)
    while insert_at > 0 and not prefix[insert_at - 1].strip():
        insert_at -= 1
    prefix[insert_at:insert_at] = [f'{_MANAGED_CODEX_STARTUP_UPDATE_KEY} = false']
    merged = [*prefix, *lines[first_table:]]
    return '\n'.join(merged).rstrip() + '\n'


def _trust_managed_codex_project_paths(
    payload: dict[str, object],
    *,
    project_root: Path | None,
    workspace_path: Path | None,
) -> None:
    paths = _managed_codex_trusted_paths(project_root=project_root, workspace_path=workspace_path)
    if not paths:
        return
    raw_projects = payload.get('projects')
    projects = raw_projects if isinstance(raw_projects, dict) else {}
    if projects is not raw_projects:
        payload['projects'] = projects
    for path in paths:
        raw_project = projects.get(path)
        project = raw_project if isinstance(raw_project, dict) else {}
        if project is not raw_project:
            projects[path] = project
        project['trust_level'] = 'trusted'


def _merge_managed_codex_project_trust(
    text: str,
    *,
    project_root: Path | None,
    workspace_path: Path | None,
) -> str:
    paths = _managed_codex_trusted_paths(project_root=project_root, workspace_path=workspace_path)
    if not paths:
        return text
    lines = text.splitlines()
    for path in paths:
        lines = _merge_managed_codex_single_project_trust(lines, path)
    return '\n'.join(lines).rstrip() + '\n'


def _merge_managed_codex_single_project_trust(lines: list[str], project_path: str) -> list[str]:
    header = f'[projects.{json.dumps(project_path)}]'
    project_index = _find_toml_table_index(lines, f'projects.{json.dumps(project_path)}')
    if project_index is None:
        return [*lines, '', header, 'trust_level = "trusted"']

    section_end = _toml_table_end(lines, project_index + 1)
    section_lines = [
        line
        for line in lines[project_index + 1 : section_end]
        if _toml_key_name(line) != 'trust_level'
    ]
    insert_at = len(section_lines)
    while insert_at > 0 and not section_lines[insert_at - 1].strip():
        insert_at -= 1
    section_lines[insert_at:insert_at] = ['trust_level = "trusted"']
    return [*lines[: project_index + 1], *section_lines, *lines[section_end:]]


def _managed_codex_trusted_paths(*, project_root: Path | None, workspace_path: Path | None) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for candidate in (project_root, workspace_path):
        if candidate is None:
            continue
        trusted = _trusted_project_path(candidate)
        if not trusted or trusted in seen:
            continue
        normalized.append(trusted)
        seen.add(trusted)
    return tuple(normalized)


def _trusted_project_path(path: Path) -> str:
    expanded = Path(path).expanduser()
    try:
        return str(expanded.resolve())
    except Exception:
        return str(expanded)


def _find_toml_table_index(lines: list[str], table_name: str) -> int | None:
    needle = f'[{table_name}]'
    for index, line in enumerate(lines):
        if line.split('#', 1)[0].strip() == needle:
            return index
    return None


def _toml_table_end(lines: list[str], start: int) -> int:
    for index in range(start, len(lines)):
        if _TOML_TABLE_HEADER_RE.match(lines[index]):
            return index
    return len(lines)


def _toml_key_name(line: str) -> str | None:
    candidate = line.split('#', 1)[0]
    if '=' not in candidate:
        return None
    raw_key = candidate.split('=', 1)[0].strip()
    return raw_key if _BARE_TOML_KEY_RE.match(raw_key) else None


def _managed_codex_config_payload(source_config: Path, *, authority: CodexApiAuthority) -> dict[str, object]:
    payload = {'model_provider': authority.provider_id}
    inherited_payload = _strip_route_authority(_read_source_config_payload(source_config))
    for key, value in inherited_payload.items():
        payload[key] = value
    payload['model_providers'] = {
        authority.provider_id: {
            'name': authority.provider_id,
            'wire_api': authority.wire_api,
            'requires_openai_auth': authority.requires_openai_auth,
            'base_url': authority.base_url,
        }
    }
    return _disable_interactive_migration_features(payload)


def _disable_interactive_migration_features(payload: dict[str, object]) -> dict[str, object]:
    sanitized = _clone_mapping(payload)
    sanitized[_MANAGED_CODEX_STARTUP_UPDATE_KEY] = False
    raw_features = sanitized.get('features')
    features = dict(raw_features) if isinstance(raw_features, dict) else {}
    for feature_name in _MANAGED_CODEX_DISABLED_FEATURES:
        features[feature_name] = False
    sanitized['features'] = features
    return sanitized


def _strip_unmanaged_hook_config(payload: dict[str, object]) -> None:
    # CCB installs its own per-agent managed hook declarations below. Inherited
    # user hooks would couple agent runtime behavior to the outer Codex home.
    payload.pop('hooks', None)


def _merge_codex_mcp_server_overrides(payload: dict[str, object], *, profile) -> None:
    overrides = _profile_mcp_servers(profile)
    if not overrides:
        return

    existing = _codex_mcp_servers_as_mapping(payload.get('mcp_servers'))
    for name, server in overrides.items():
        existing[name] = _clone_mapping(server)
    payload['mcp_servers'] = existing


def _install_role_command_mcp_server(
    target_config: Path,
    *,
    command_policy,
    project_root: Path | None,
    agent_name: str | None,
    runtime_dir: Path | None,
) -> None:
    provider_tools = dict(getattr(command_policy, 'provider_tools', ()) or ())
    tool_name = str(provider_tools.get('codex') or '').strip()
    actor = str(agent_name or '').strip().lower()
    allowed_tools = {
        'frontdesk': 'ccb_frontdesk_ask_planner',
        'task_detailer': 'ccb_task_detailer_replan_planner',
        'ccb_task_detailer': 'ccb_task_detailer_replan_planner',
    }
    if allowed_tools.get(actor) != tool_name:
        return
    if project_root is None or runtime_dir is None:
        raise RuntimeError('Codex role command capability requires project and runtime identity')
    resolved_project = Path(project_root).expanduser().resolve()
    server = Path(__file__).resolve().parents[2] / 'mcp' / 'ccb-role-command' / 'server.py'
    if not server.is_file():
        raise RuntimeError(f'Codex role command MCP server is missing: {server}')
    payload = _read_source_config_payload(target_config)
    payload['approval_policy'] = 'never'
    payload['sandbox_mode'] = 'read-only'
    features = _clone_mapping(payload.get('features')) if isinstance(payload.get('features'), dict) else {}
    for feature in (
        'apps',
        'browser_use',
        'browser_use_external',
        'computer_use',
        'image_generation',
        'multi_agent',
        'multi_agent_v2',
        'plugins',
        'remote_plugin',
        'shell_tool',
        'unified_exec',
    ):
        features[feature] = False
    payload['features'] = features
    server_env = {
        'CCB_CALLER_ACTOR': actor,
        'CCB_CALLER_PROJECT_ROOT': str(resolved_project),
        'CCB_CALLER_PROJECT_ID': compute_project_id(resolved_project),
        'CCB_CALLER_RUNTIME_DIR': str(Path(runtime_dir).expanduser().resolve()),
    }
    agent_roles_store = str(os.environ.get('AGENT_ROLES_STORE') or '').strip()
    if agent_roles_store:
        server_env['AGENT_ROLES_STORE'] = agent_roles_store
    payload['mcp_servers'] = {'ccb_role_command': {
        'command': sys.executable,
        'args': [str(server)],
        'required': True,
        'enabled_tools': [tool_name],
        'default_tools_approval_mode': 'prompt',
        'tools': {
            tool_name: {
                'approval_mode': 'approve',
            },
        },
        'env': server_env,
    }}
    target_config.write_text(_render_toml_document(payload), encoding='utf-8')


def _merge_codex_plugin_overrides(payload: dict[str, object], *, profile) -> None:
    overrides = _profile_plugins(profile)
    if not overrides:
        return

    raw_existing = payload.get('plugins')
    existing = _clone_mapping(raw_existing) if isinstance(raw_existing, dict) else {}
    for name, plugin in overrides.items():
        merged = _clone_mapping(existing.get(name)) if isinstance(existing.get(name), dict) else {}
        for key, value in plugin.items():
            merged[str(key)] = _clone_payload(value)
        existing[name] = merged
    payload['plugins'] = existing


def _profile_plugins(profile) -> dict[str, dict[str, object]]:
    raw = getattr(profile, 'plugins', {}) if profile is not None else {}
    if not isinstance(raw, dict):
        return _profile_plugin_overrides_from_env(profile)
    normalized: dict[str, dict[str, object]] = {}
    for raw_name, raw_plugin in raw.items():
        name = str(raw_name or '').strip()
        if not name:
            continue
        if isinstance(raw_plugin, dict):
            normalized[name] = _clone_mapping(raw_plugin)
        else:
            normalized[name] = {'enabled': bool(raw_plugin)}
    for name, plugin in _profile_plugin_overrides_from_env(profile).items():
        existing = normalized.get(name)
        merged = _clone_mapping(existing) if isinstance(existing, dict) else {}
        for key, value in plugin.items():
            merged[str(key)] = _clone_payload(value)
        normalized[name] = merged
    return normalized


def _profile_plugin_overrides_from_env(profile) -> dict[str, dict[str, object]]:
    env = _profile_env(profile)
    raw = env.get('CCB_CODEX_PLUGIN_OVERRIDES_JSON') or env.get('CCB_CODEX_PLUGIN_OVERRIDES') or ''
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except Exception:
        return {}
    if not isinstance(payload, dict):
        return {}
    normalized: dict[str, dict[str, object]] = {}
    for raw_name, raw_plugin in payload.items():
        name = str(raw_name or '').strip()
        if not name:
            continue
        if isinstance(raw_plugin, dict):
            normalized[name] = _clone_mapping(raw_plugin)
        else:
            normalized[name] = {'enabled': bool(raw_plugin)}
    return normalized


def _profile_mcp_servers(profile) -> dict[str, dict[str, object]]:
    raw = getattr(profile, 'mcp_servers', {}) if profile is not None else {}
    if not isinstance(raw, dict):
        return {}
    normalized: dict[str, dict[str, object]] = {}
    for raw_name, raw_server in raw.items():
        name = str(raw_name or '').strip()
        if not name or not isinstance(raw_server, dict):
            continue
        normalized[name] = _clone_mapping(raw_server)
    return normalized


def _codex_mcp_servers_as_mapping(value: object) -> dict[str, dict[str, object]]:
    if isinstance(value, dict):
        return {
            str(name): _clone_mapping(server)
            for name, server in value.items()
            if str(name).strip() and isinstance(server, dict)
        }
    if isinstance(value, list):
        servers: dict[str, dict[str, object]] = {}
        for item in value:
            if not isinstance(item, dict):
                continue
            name = str(item.get('name') or '').strip()
            if not name:
                continue
            server = _clone_mapping(item)
            server.pop('name', None)
            servers[name] = server
        return servers
    return {}


def _import_optional_toml_reader():
    for module_name in ('tomllib', 'tomli', 'toml'):
        try:
            return importlib.import_module(module_name)
        except ModuleNotFoundError:
            continue
    return None


def _read_source_config_payload(config_path: Path) -> dict[str, object]:
    try:
        if not config_path.is_file():
            return {}
        reader = _import_optional_toml_reader()
        if reader is None:
            return {}
        if getattr(reader, '__name__', '') == 'toml':
            payload = reader.loads(config_path.read_text(encoding='utf-8'))
        elif hasattr(reader, 'load'):
            with config_path.open('rb') as handle:
                payload = reader.load(handle)
        elif hasattr(reader, 'loads'):  # pragma: no cover - defensive fallback
            payload = reader.loads(config_path.read_text(encoding='utf-8'))
        else:  # pragma: no cover - unsupported parser shim
            return {}
    except Exception:
        return {}
    return _clone_mapping(payload) if isinstance(payload, dict) else {}


def _source_config_valid(config_path: Path) -> bool:
    try:
        if not config_path.is_file():
            return True
        reader = _import_optional_toml_reader()
        if reader is None:
            return True
        if getattr(reader, '__name__', '') == 'toml':
            reader.loads(config_path.read_text(encoding='utf-8'))
        elif hasattr(reader, 'load'):
            with config_path.open('rb') as handle:
                reader.load(handle)
        elif hasattr(reader, 'loads'):  # pragma: no cover - defensive fallback
            reader.loads(config_path.read_text(encoding='utf-8'))
        else:  # pragma: no cover - unsupported parser shim
            return True
        return True
    except Exception:
        return False


def _strip_route_authority(payload: dict[str, object]) -> dict[str, object]:
    cleaned: dict[str, object] = {}
    for raw_key, value in payload.items():
        key = str(raw_key)
        if key in {'model_provider', 'model_providers'}:
            continue
        cleaned[key] = _clone_payload(value)
    return cleaned


def _clone_mapping(payload: dict[str, object]) -> dict[str, object]:
    return {str(key): _clone_payload(value) for key, value in payload.items()}


def _clone_payload(value: object) -> object:
    if isinstance(value, dict):
        return _clone_mapping(value)
    if isinstance(value, list):
        return [_clone_payload(item) for item in value]
    return value


def _materialize_auth_file(
    source: Path,
    target: Path,
    *,
    profile,
    previous_projection: dict[str, object],
) -> None:
    explicit_key = _explicit_api_key(profile)
    if explicit_key:
        _write_auth_file(target, explicit_key)
        return
    _sync_auth_file(
        source,
        target,
        enabled=_inherits_external_auth(profile),
        source_owned='auth.json' in _manifest_projected_files(previous_projection),
    )


def _sync_auth_file(
    source: Path,
    target: Path,
    *,
    enabled: bool,
    source_owned: bool,
) -> None:
    if not enabled:
        if source_owned:
            target.unlink(missing_ok=True)
        return
    copied = _sync_secret_source_file(source, target)
    if not copied:
        if source_owned:
            target.unlink(missing_ok=True)
        return
    if copied:
        try:
            os.chmod(target, 0o600)
        except Exception:
            pass


def _write_auth_file(target: Path, api_key: str) -> None:
    ensure_private_directory(target.parent)
    payload = json.dumps({'OPENAI_API_KEY': api_key}, ensure_ascii=False, separators=(',', ':'))
    atomic_write_text(target, f'{payload}\n')
    try:
        os.chmod(target, 0o600)
    except Exception:
        pass


def _materialize_auth_sidecars(
    source_home: Path,
    target_home: Path,
    *,
    source_config: Path,
    profile,
    authority: CodexApiAuthority | None,
    previous_projection: dict[str, object],
) -> None:
    source_home = Path(source_home).expanduser()
    target_home = Path(target_home).expanduser()
    previous_sidecars = _manifest_sidecars(previous_projection)
    requested_sidecars = _codex_auth_sidecar_names(source_home, source_config)

    if not _inherits_external_auth(profile):
        _remove_projected_auth_sidecars(target_home, previous_sidecars)
        _write_auth_projection_manifest(
            source_home,
            target_home,
            projected_files=(),
            projected_sidecars=(),
            profile=profile,
            status=(
                'explicit_api_authority'
                if _explicit_api_key(profile) or authority is not None
                else 'inherit_auth_disabled'
            ),
        )
        return

    projected: list[str] = []
    for name in sorted(requested_sidecars):
        source = source_home / name
        target = target_home / name
        if _sync_secret_source_file(source, target):
            projected.append(name)
        elif name in previous_sidecars:
            target.unlink(missing_ok=True)

    for name in sorted(previous_sidecars - set(projected)):
        if name not in requested_sidecars:
            (target_home / name).unlink(missing_ok=True)

    _write_auth_projection_manifest(
        source_home,
        target_home,
        projected_files=tuple(
            name
            for name in ('auth.json', *projected)
            if (target_home / name).is_file()
        ),
        projected_sidecars=tuple(projected),
        profile=profile,
        status='inherited_auth',
    )


def _codex_auth_sidecar_names(source_home: Path, source_config: Path) -> set[str]:
    names = set(_CODEX_AUTH_SIDECAR_FILENAMES)
    for match in _CODEX_AUTH_SIDECAR_REF_RE.finditer(_safe_read_text(source_config)):
        name = str(match.group('name') or '').strip()
        if _is_safe_codex_auth_sidecar_name(name):
            names.add(name)
    return names


def _materialize_config_sidecars(
    source_home: Path,
    target_home: Path,
    *,
    required_names: tuple[str, ...],
) -> None:
    for name in sorted(set(required_names)):
        source = Path(source_home).expanduser() / name
        target = Path(target_home).expanduser() / name
        if _probe_regular_source_file(source):
            try:
                copied = copy_regular_file(source, target)
            except OSError as exc:
                raise RuntimeError(
                    f'cannot copy inherited Codex config sidecar: {source}: {exc}'
                ) from exc
            if not copied:
                raise RuntimeError(f'cannot copy inherited Codex config sidecar: {source}')
        if not target.is_file():
            raise RuntimeError(
                f'Codex model catalog file is missing: {source} '
                f'(expected managed file: {target})'
            )


def _set_codex_model_catalog_json(target_config: Path, name: str) -> None:
    payload = _read_source_config_payload(target_config)
    payload[_CODEX_MODEL_CATALOG_JSON_KEY] = name
    target_config.write_text(_render_toml_document(payload), encoding='utf-8')


def _set_codex_model(target_config: Path, model: str) -> None:
    normalized = str(model or '').strip()
    if not normalized:
        return
    payload = _read_source_config_payload(target_config)
    payload['model'] = normalized
    target_config.write_text(_render_toml_document(payload), encoding='utf-8')


def _codex_model_catalog_sidecar_name(value: object) -> str | None:
    raw = str(value or '').strip() if isinstance(value, str) else ''
    if not raw:
        return None
    normalized = raw.replace('\\', '/')
    if '/' in normalized:
        match = _CODEX_AUTH_SIDECAR_REF_RE.fullmatch(normalized)
        if not match:
            return None
        raw = str(match.group('name') or '').strip()
    if _is_safe_codex_model_catalog_sidecar_name(raw):
        return raw
    return None


def _is_safe_codex_model_catalog_sidecar_name(name: str) -> bool:
    if not name or '/' in name or '\\' in name or name in {'.', '..'} or name.startswith('.'):
        return False
    lower = name.lower()
    if lower in {'auth.json', 'config.toml'}:
        return False
    return lower.endswith('.json')


def _is_safe_codex_auth_sidecar_name(name: str) -> bool:
    if not name or '/' in name or '\\' in name or name in {'.', '..'} or name.startswith('.'):
        return False
    lower = name.lower()
    if lower in {'auth.json', 'config.toml'}:
        return False
    if lower in set(_CODEX_AUTH_SIDECAR_FILENAMES):
        return True
    return lower.endswith('.config.toml') or any(token in lower for token in ('auth', 'credential', 'key', 'token'))


def _sync_secret_source_file(source: Path, target: Path) -> bool:
    source = Path(source).expanduser()
    target = Path(target).expanduser()
    if not _probe_regular_source_file(source):
        return False
    try:
        copied = copy_regular_file(source, target)
    except OSError as exc:
        raise RuntimeError(
            f'cannot copy inherited Codex auth source: {source}: {exc}'
        ) from exc
    if not copied:
        raise RuntimeError(f'cannot copy inherited Codex auth source: {source}')
    try:
        os.chmod(target, 0o600)
    except Exception:
        pass
    return True


def _valid_codex_auth_file(path: Path) -> bool:
    if Path(path).is_symlink():
        return False
    try:
        payload = json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception:
        return False
    return isinstance(payload, dict) and bool(payload)


def _atomic_sync_secret_file(source: Path, target: Path) -> None:
    source = Path(source).expanduser()
    target = Path(target).expanduser()
    if Path(os.path.abspath(source)) == Path(os.path.abspath(target)):
        return
    if not source.is_file() or source.is_symlink():
        raise ValueError(f'Codex auth source must be a regular file: {source}')
    ensure_private_directory(target.parent)
    fd, tmp_name = tempfile.mkstemp(prefix=f'.{target.name}.ccb-auth-', dir=str(target.parent))
    os.close(fd)
    tmp_path = Path(tmp_name)
    try:
        shutil.copy2(source, tmp_path)
        os.chmod(tmp_path, 0o600)
        os.replace(tmp_path, target)
    finally:
        tmp_path.unlink(missing_ok=True)


def _read_auth_projection_manifest(target_home: Path) -> dict[str, object]:
    path = Path(target_home).expanduser() / _CODEX_AUTH_PROJECTION_MANIFEST
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _manifest_sidecars(payload: dict[str, object]) -> set[str]:
    if not _valid_auth_projection_manifest(payload):
        return set()
    raw = payload.get('projected_sidecars')
    if not isinstance(raw, list):
        return set()
    owned = _manifest_projected_files(payload)
    return {
        name
        for name in (str(item or '').strip() for item in raw)
        if _is_safe_codex_auth_sidecar_name(name) and name in owned
    }


def _valid_auth_projection_manifest(payload: dict[str, object]) -> bool:
    return bool(
        isinstance(payload, dict)
        and payload.get('schema_version') == 1
        and payload.get('record_type') == 'ccb_codex_auth_projection'
    )


def _manifest_projected_files(payload: dict[str, object]) -> set[str]:
    if not _valid_auth_projection_manifest(payload):
        return set()
    raw = payload.get('projected_files')
    if isinstance(raw, list):
        return {
            name
            for name in (str(item or '').strip() for item in raw)
            if name == 'auth.json' or _is_safe_codex_auth_sidecar_name(name)
        }

    # Migrate the pre-provenance manifest only when its recorded source and
    # target digests prove that CCB copied the exact file.
    if not str(payload.get('status') or '').startswith('inherited_auth'):
        return set()
    records = payload.get('files')
    if not isinstance(records, list):
        return set()
    owned: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        name = str(record.get('name') or '').strip()
        if name != 'auth.json' and not _is_safe_codex_auth_sidecar_name(name):
            continue
        source_sha = str(record.get('source_sha256') or '').strip()
        target_sha = str(record.get('target_sha256') or '').strip()
        if (
            record.get('source_exists') is True
            and record.get('target_exists') is True
            and source_sha
            and hmac.compare_digest(source_sha, target_sha)
        ):
            owned.add(name)
    return owned


def _remove_projected_auth_sidecars(target_home: Path, names: set[str]) -> None:
    for name in sorted(names):
        if _is_safe_codex_auth_sidecar_name(name):
            (Path(target_home).expanduser() / name).unlink(missing_ok=True)


def _write_auth_projection_manifest(
    source_home: Path,
    target_home: Path,
    *,
    projected_files: tuple[str, ...],
    projected_sidecars: tuple[str, ...],
    profile,
    status: str,
) -> None:
    target_home = Path(target_home).expanduser()
    names = ('auth.json', 'config.toml', *projected_sidecars)
    payload = {
        'schema_version': 1,
        'record_type': 'ccb_codex_auth_projection',
        'status': status,
        'source_home': str(Path(source_home).expanduser()),
        'inherit_auth': _inherits_auth(profile),
        'projected_files': sorted(projected_files),
        'projected_sidecars': sorted(projected_sidecars),
        'files': [
            _auth_projection_file_record(Path(source_home).expanduser() / name, target_home / name, name=name)
            for name in names
        ],
    }
    manifest = target_home / _CODEX_AUTH_PROJECTION_MANIFEST
    atomic_write_text(manifest, json.dumps(payload, ensure_ascii=True, sort_keys=True, indent=2) + '\n')
    try:
        os.chmod(manifest, 0o600)
    except Exception:
        pass


def _auth_projection_file_record(source: Path, target: Path, *, name: str) -> dict[str, object]:
    return {
        'name': name,
        'source_exists': source.is_file(),
        'source_sha256': _file_sha256(source),
        'target_exists': target.is_file(),
        'target_sha256': _file_sha256(target),
        'target_size_bytes': target.stat().st_size if target.is_file() else 0,
    }


def _file_sha256(path: Path) -> str:
    try:
        if not Path(path).expanduser().is_file():
            return ''
        digest = hashlib.sha256()
        with Path(path).expanduser().open('rb') as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b''):
                digest.update(chunk)
        return digest.hexdigest()
    except Exception:
        return ''


def _sync_file(source: Path, target: Path) -> None:
    if not source.is_file():
        target.unlink(missing_ok=True)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(source, target)
    except Exception:
        pass


def _sync_tree(source: Path, target: Path, *, enabled: bool) -> None:
    if not enabled:
        _remove_path(target)
        return
    if not source.is_dir():
        _remove_path(target)
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(source, target, dirs_exist_ok=True)
    except Exception:
        pass


def _route_inherited_tree(source: Path, target: Path, *, enabled: bool, label: str) -> None:
    if not enabled:
        remove_projected_path(target, label=label)
        return
    if not source.is_dir():
        remove_projected_path(target, label=label)
        return
    route_projected_tree(source, target, label=label)


def _materialize_inherited_skills(source: Path, target: Path, *, profile, enabled: bool = True) -> None:
    include = _profile_skill_patterns(profile, 'inherited_skill_include')
    exclude = _profile_skill_patterns(profile, 'inherited_skill_exclude')
    for legacy_name in _CODEX_LEGACY_OWNED_SKILL_NAMES:
        _remove_path(Path(target).expanduser() / legacy_name)
    route_inherited_skill_entries(
        source,
        target,
        enabled=_inherits_skills(profile) and enabled,
        label=_CODEX_SKILLS_PROJECTION_LABEL,
        exclude=required_control_skill_names('codex'),
        include_patterns=include,
        exclude_patterns=exclude,
        # Codex owns a nested system-skill collection without a top-level
        # SKILL.md.  Treat it as one immutable projection entry.
        special_entries=('.system',),
    )


def _role_command_policy_disables_inherited_assets(command_policy) -> bool:
    if command_policy is None:
        return False
    mode = str(getattr(command_policy, 'mode', '') or '').strip()
    enforcement = str(getattr(command_policy, 'enforcement', '') or '').strip()
    return mode == 'deny_all_except' and enforcement == 'required'


def _materialize_skill_overlays(target: Path, *, profile, project_root: Path | None) -> None:
    overlays = getattr(profile, 'skill_overlays', {}) if profile is not None else {}
    if not isinstance(overlays, dict):
        overlays = {}
    desired_labels: set[str] = set()
    target = Path(target).expanduser()
    for overlay_name, overlay in sorted(overlays.items(), key=lambda item: str(item[0])):
        source = _resolve_skill_overlay_source(getattr(overlay, 'source', ''), project_root=project_root)
        include = _profile_skill_patterns(overlay, 'include', default=('*',))
        exclude = _profile_skill_patterns(overlay, 'exclude')
        if not source.is_dir():
            continue
        target.mkdir(parents=True, exist_ok=True)
        for entry in sorted(source.iterdir(), key=lambda item: item.name):
            if not entry.is_dir():
                continue
            skill_name = entry.name
            if not _matches_skill_patterns(skill_name, include=include, exclude=exclude):
                continue
            label = f'codex-skill-overlay:{overlay_name}:{skill_name}'
            desired_labels.add(label)
            route_projected_tree(
                entry,
                target / skill_name,
                enabled=True,
                label=label,
                allow_unmarked_replace=False,
            )
    _remove_stale_skill_projection_markers(
        target,
        label_prefix='codex-skill-overlay:',
        desired_labels=desired_labels,
    )


def _resolve_skill_overlay_source(source: object, *, project_root: Path | None) -> Path:
    path = Path(str(source or '')).expanduser()
    if not path.is_absolute() and project_root is not None:
        path = Path(project_root).expanduser() / path
    return path


def _profile_skill_patterns(profile, attribute: str, *, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    raw = getattr(profile, attribute, default) if profile is not None else default
    if isinstance(raw, str):
        candidates = (raw,)
    else:
        try:
            candidates = tuple(raw)
        except TypeError:
            candidates = ()
    patterns = tuple(str(item or '').strip() for item in candidates if str(item or '').strip())
    return patterns or default


def _matches_skill_patterns(skill_name: str, *, include: tuple[str, ...], exclude: tuple[str, ...]) -> bool:
    if include and not any(fnmatch.fnmatchcase(skill_name, pattern) for pattern in include):
        return False
    if exclude and any(fnmatch.fnmatchcase(skill_name, pattern) for pattern in exclude):
        return False
    return True


def _remove_stale_skill_projection_markers(target: Path, *, label_prefix: str, desired_labels: set[str]) -> None:
    target = Path(target).expanduser()
    if not target.is_dir() or target.is_symlink():
        return
    for marker in sorted(target.glob('*.ccb-projection.json')):
        try:
            payload = json.loads(marker.read_text(encoding='utf-8'))
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        if payload.get('record_type') != 'ccb_projected_asset':
            continue
        label = str(payload.get('label') or '')
        if not label.startswith(label_prefix) or label in desired_labels:
            continue
        skill_name = marker.name.removesuffix('.ccb-projection.json')
        remove_projected_path(
            target / skill_name,
            label=label,
            marker_path=marker,
        )


def _sync_codex_plugin_projection(
    source_home: Path,
    target_home: Path,
    *,
    enabled: bool,
    project_root: Path | None,
    shared_cache_root: Path | None,
) -> None:
    source_tree = source_home / _CODEX_PLUGIN_TREE_RELATIVE
    source_sha = source_home / _CODEX_PLUGIN_SHA_RELATIVE
    target_tree = target_home / _CODEX_PLUGIN_TREE_RELATIVE
    target_sha = target_home / _CODEX_PLUGIN_SHA_RELATIVE
    target_marker = Path(f'{target_tree}.ccb-projection.json')
    target_owned = projected_path_is_owned(target_tree, label=_CODEX_PLUGIN_PROJECTION_LABEL)
    if not enabled or not source_tree.is_dir():
        if target_owned:
            remove_projected_path(target_tree, label=_CODEX_PLUGIN_PROJECTION_LABEL)
            _remove_path(target_sha)
        return
    if _same_path(source_tree, target_tree):
        return
    target_present = target_tree.exists() or target_tree.is_symlink()
    if (target_present or target_sha.exists() or target_marker.exists()) and not target_owned:
        return
    bundle_sha = _codex_plugin_bundle_sha(source_tree, source_sha)
    if not bundle_sha:
        return
    bundle_tree = _codex_plugin_shared_bundle_path(
        project_root,
        target_home,
        shared_cache_root=shared_cache_root,
        bundle_sha=bundle_sha,
    )
    if target_owned and source_sha.is_file() and _plugin_projection_is_current(
        source_tree=source_tree,
        source_sha=source_sha,
        target_tree=target_tree,
        target_sha=target_sha,
    ):
        if bundle_tree is None:
            return
        if _same_path(target_tree, bundle_tree):
            write_projected_marker(
                target_tree,
                label=_CODEX_PLUGIN_PROJECTION_LABEL,
                mode='symlink',
                source=bundle_tree,
            )
            return
    projected = False
    if bundle_tree is not None and copy_projected_tree_to_cache(source_tree, bundle_tree, label=_CODEX_PLUGIN_PROJECTION_LABEL):
        remove_projected_path(target_tree, label=_CODEX_PLUGIN_PROJECTION_LABEL)
        _remove_path(target_sha)
        if target_tree.exists() or target_tree.is_symlink():
            return
        target_tree.parent.mkdir(parents=True, exist_ok=True)
        try:
            target_tree.symlink_to(bundle_tree, target_is_directory=True)
            projected = write_projected_marker(
                target_tree,
                label=_CODEX_PLUGIN_PROJECTION_LABEL,
                mode='symlink',
                source=bundle_tree,
            )
            if not projected:
                _remove_path(target_tree)
        except Exception:
            projected = route_projected_tree(
                bundle_tree,
                target_tree,
                label=_CODEX_PLUGIN_PROJECTION_LABEL,
            )
    else:
        remove_projected_path(target_tree, label=_CODEX_PLUGIN_PROJECTION_LABEL)
        _remove_path(target_sha)
        if target_tree.exists() or target_tree.is_symlink():
            return
        projected = route_projected_tree(
            source_tree,
            target_tree,
            label=_CODEX_PLUGIN_PROJECTION_LABEL,
        )
    if not projected or not _plugin_required_paths_available(source_tree, target_tree):
        return
    _remove_path(target_sha)
    if source_sha.is_file():
        _sync_file(source_sha, target_sha)
    else:
        target_sha.parent.mkdir(parents=True, exist_ok=True)
        target_sha.write_text(f'{bundle_sha}\n', encoding='utf-8')


def _install_codex_inherited_hooks(
    target_home: Path,
    target_config: Path,
    *,
    source_home: Path | None = None,
) -> None:
    hooks_path = Path(target_home).expanduser() / 'hooks.json'
    event_groups = _allowed_inherited_codex_hooks(source_home)
    hooks_payload = {'hooks': event_groups}
    hooks_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        hooks_path,
        json.dumps(hooks_payload, ensure_ascii=False, indent=2) + '\n',
    )
    _merge_codex_hook_state(
        target_config,
        hooks_path=hooks_path,
        event_groups=event_groups,
    )


def _allowed_inherited_codex_hooks(source_home: Path | None) -> dict[str, list[dict[str, object]]]:
    if source_home is None:
        return {}
    configured_events = _split_codex_inherited_hook_env(os.environ.get(_CODEX_INHERITED_HOOK_EVENTS_ENV))
    default_marker_events = frozenset([*_CODEX_DEFAULT_INHERITED_HOOK_EVENTS, *configured_events])
    configured_marker_events = frozenset([*_CODEX_CONFIGURED_MARKER_DEFAULT_HOOK_EVENTS, *configured_events])
    policy_events = frozenset([*default_marker_events, *configured_marker_events])
    default_command_markers = _normalize_codex_command_hook_markers(_CODEX_DEFAULT_INHERITED_COMMAND_HOOK_MARKERS)
    configured_command_markers = _normalize_codex_command_hook_markers(
        _split_codex_inherited_hook_env(os.environ.get(_CODEX_INHERITED_COMMAND_HOOK_MARKERS_ENV))
    )
    if not policy_events or (not default_command_markers and not configured_command_markers):
        return {}
    hooks_path = Path(source_home).expanduser() / 'hooks.json'
    try:
        payload = json.loads(hooks_path.read_text(encoding='utf-8'))
    except Exception:
        return {}
    hooks = payload.get('hooks') if isinstance(payload, dict) else None
    if not isinstance(hooks, dict):
        return {}

    selected: dict[str, list[dict[str, object]]] = {}
    for event_name, raw_groups in hooks.items():
        event = str(event_name)
        if event not in policy_events or not isinstance(raw_groups, list):
            continue
        groups: list[dict[str, object]] = []
        for raw_group in raw_groups:
            group = _allowed_inherited_codex_hook_group(
                raw_group,
                event_name=event,
                default_marker_events=default_marker_events,
                configured_marker_events=configured_marker_events,
                default_command_markers=default_command_markers,
                configured_command_markers=configured_command_markers,
            )
            if group is not None:
                groups.append(group)
        if groups:
            selected[event] = groups
    return selected


def _normalize_codex_command_hook_markers(markers: object) -> tuple[str, ...]:
    normalized: list[str] = []
    seen: set[str] = set()
    for marker in markers:
        normalized_marker = str(marker or '').strip().replace('\\', '/')
        if not normalized_marker or normalized_marker in seen:
            continue
        normalized.append(normalized_marker)
        seen.add(normalized_marker)
    return tuple(normalized)


def _split_codex_inherited_hook_env(value: object) -> tuple[str, ...]:
    raw = str(value or '').strip()
    if not raw:
        return ()
    parts = re.split(f'[,{re.escape(os.pathsep)}]+', raw)
    return tuple(part.strip() for part in parts if part.strip())


def _allowed_inherited_codex_hook_group(
    raw_group: object,
    *,
    event_name: str,
    default_marker_events: frozenset[str],
    configured_marker_events: frozenset[str],
    default_command_markers: tuple[str, ...],
    configured_command_markers: tuple[str, ...],
) -> dict[str, object] | None:
    if not isinstance(raw_group, dict):
        return None
    raw_handlers = raw_group.get('hooks')
    if not isinstance(raw_handlers, list):
        return None
    handlers: list[dict[str, object]] = []
    for raw_handler in raw_handlers:
        handler = _allowed_inherited_codex_hook_handler(
            raw_handler,
            event_name=event_name,
            default_marker_events=default_marker_events,
            configured_marker_events=configured_marker_events,
            default_command_markers=default_command_markers,
            configured_command_markers=configured_command_markers,
        )
        if handler is None:
            return None
        handlers.append(handler)
    if not handlers:
        return None
    group: dict[str, object] = {'hooks': handlers}
    if raw_group.get('matcher') is not None:
        group['matcher'] = str(raw_group.get('matcher') or '')
    return group


def _allowed_inherited_codex_hook_handler(
    raw_handler: object,
    *,
    event_name: str,
    default_marker_events: frozenset[str],
    configured_marker_events: frozenset[str],
    default_command_markers: tuple[str, ...],
    configured_command_markers: tuple[str, ...],
) -> dict[str, object] | None:
    if not isinstance(raw_handler, dict):
        return None
    if str(raw_handler.get('type') or '') != 'command':
        return None
    command = str(raw_handler.get('command') or '')
    normalized_command = command.replace('\\', '/')
    matches_default_marker = event_name in default_marker_events and any(
        marker in normalized_command for marker in default_command_markers
    )
    matches_configured_marker = event_name in configured_marker_events and any(
        marker in normalized_command for marker in configured_command_markers
    )
    if not matches_default_marker and not matches_configured_marker:
        return None
    handler: dict[str, object] = {
        'type': 'command',
        'command': command,
    }
    if raw_handler.get('timeout') is not None:
        try:
            handler['timeout'] = int(raw_handler.get('timeout') or 0)
        except (TypeError, ValueError):
            return None
    if raw_handler.get('async') is not None:
        handler['async'] = bool(raw_handler.get('async'))
    if raw_handler.get('statusMessage') is not None:
        handler['statusMessage'] = str(raw_handler.get('statusMessage') or '')
    return handler


def _merge_codex_hook_groups(
    base: dict[str, list[dict[str, object]]],
    inherited: dict[str, list[dict[str, object]]],
) -> dict[str, list[dict[str, object]]]:
    merged: dict[str, list[dict[str, object]]] = {
        event: [_clone_mapping(group) for group in groups]
        for event, groups in base.items()
    }
    seen_commands = {
        (event, _codex_hook_group_command_identity(group))
        for event, groups in merged.items()
        for group in groups
    }
    for event, groups in inherited.items():
        event_groups = merged.setdefault(event, [])
        for group in groups:
            identity = (event, _codex_hook_group_command_identity(group))
            if identity in seen_commands:
                continue
            event_groups.append(_clone_mapping(group))
            seen_commands.add(identity)
    return merged


def _codex_hook_group_command_identity(group: dict[str, object]) -> tuple[str, ...]:
    handlers = group.get('hooks')
    if not isinstance(handlers, list):
        return ()
    return tuple(
        str(handler.get('command') or '')
        for handler in handlers
        if isinstance(handler, dict)
    )


def _merge_codex_hook_state(
    target_config: Path,
    *,
    hooks_path: Path,
    event_groups: dict[str, list[dict[str, object]]],
) -> None:
    state_table: dict[str, object] = {}
    source_path = str(Path(hooks_path).expanduser())
    for event_name, groups in event_groups.items():
        event_label = _codex_hook_event_label(event_name)
        for group_index, group in enumerate(groups):
            handlers = group.get('hooks') if isinstance(group, dict) else None
            if not isinstance(handlers, list):
                continue
            for handler_index, handler in enumerate(handlers):
                if not isinstance(handler, dict):
                    continue
                key = f'{source_path}:{event_label}:{group_index}:{handler_index}'
                state_table[key] = {
                    'enabled': True,
                    'trusted_hash': _codex_command_hook_hash(event_label, group, handler),
                }
    target_config.parent.mkdir(parents=True, exist_ok=True)
    existing_text = _safe_read_text(target_config)
    payload = _read_source_config_payload(target_config)
    if not payload and existing_text.strip():
        target_config.write_text(
            _replace_managed_codex_hook_state_block(existing_text, state_table),
            encoding='utf-8',
        )
        return
    hooks_payload = payload.get('hooks')
    hooks_table = hooks_payload if isinstance(hooks_payload, dict) else {}
    if hooks_table is not hooks_payload:
        payload['hooks'] = hooks_table
    hooks_table['state'] = state_table
    target_config.write_text(_render_toml_document(payload), encoding='utf-8')


def _replace_managed_codex_hook_state_block(text: str, state_table: dict[str, object]) -> str:
    begin = '# ccb managed codex activity hook state: begin'
    end = '# ccb managed codex activity hook state: end'
    lines = text.splitlines()
    cleaned: list[str] = []
    index = 0
    while index < len(lines):
        if lines[index].strip() == begin:
            index += 1
            while index < len(lines) and lines[index].strip() != end:
                index += 1
            if index < len(lines):
                index += 1
            continue
        cleaned.append(lines[index])
        index += 1
    block = _render_toml_document({'hooks': {'state': state_table}}).rstrip()
    return '\n'.join([*cleaned, '', begin, block, end]).strip() + '\n'


def _codex_hook_event_label(event_name: str) -> str:
    return ''.join(
        f'_{char.lower()}' if char.isupper() and index else char.lower()
        for index, char in enumerate(str(event_name or '').strip())
    )


def _codex_command_hook_hash(event_label: str, group: dict[str, object], handler: dict[str, object]) -> str:
    normalized_handler = {
        'type': 'command',
        'command': str(handler.get('command') or ''),
        'timeout': int(handler.get('timeout') or _CODEX_COMMAND_HOOK_DEFAULT_TIMEOUT_S),
        'async': bool(handler.get('async', False)),
    }
    if handler.get('statusMessage') is not None:
        normalized_handler['statusMessage'] = str(handler.get('statusMessage') or '')
    normalized_group: dict[str, object] = {}
    if group.get('matcher') is not None:
        normalized_group['matcher'] = str(group.get('matcher') or '')
    normalized_group['hooks'] = [normalized_handler]
    identity = {'event_name': event_label, **normalized_group}
    encoded = json.dumps(_canonical_json(identity), ensure_ascii=False, separators=(',', ':')).encode('utf-8')
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _canonical_json(value: object) -> object:
    if isinstance(value, dict):
        return {key: _canonical_json(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [_canonical_json(item) for item in value]
    return value


def _codex_plugin_bundle_sha(source_tree: Path, source_sha: Path) -> str:
    if source_sha.is_file():
        digest = _safe_read_text(source_sha).strip()
        if digest:
            return _safe_cache_segment(digest)
    return tree_content_fingerprint(source_tree)


def _safe_cache_segment(value: str) -> str:
    normalized = re.sub(r'[^A-Za-z0-9._-]+', '-', str(value or '').strip()).strip('.-')
    if normalized:
        return normalized[:160]
    return hashlib.sha256(str(value or '').encode('utf-8', errors='ignore')).hexdigest()


def _codex_plugin_shared_bundle_path(
    project_root: Path | None,
    target_home: Path,
    *,
    shared_cache_root: Path | None,
    bundle_sha: str,
) -> Path | None:
    cache_root = _shared_cache_root(project_root, target_home, shared_cache_root=shared_cache_root)
    if cache_root is None:
        return None
    return cache_root / 'codex' / 'plugin-bundles' / bundle_sha


def _shared_cache_root(
    project_root: Path | None,
    target_home: Path,
    *,
    shared_cache_root: Path | None,
) -> Path | None:
    if shared_cache_root is not None:
        return Path(shared_cache_root).expanduser()
    if project_root is not None:
        layout = PathLayout(Path(project_root).expanduser())
        try:
            layout.ensure_provider_shared_cache_dir('codex')
        except Exception:
            return None
        return layout.shared_cache_dir
    del target_home
    return None


def _materialize_codex_memory(
    source_home: Path,
    target_home: Path,
    *,
    profile,
    project_root: Path | None,
    agent_name: str | None,
    workspace_path: Path | None,
) -> dict[str, object]:
    normalized_source_home = Path(source_home).expanduser()
    normalized_target_home = Path(target_home).expanduser()
    target = normalized_target_home / 'AGENTS.md'
    if _same_path(normalized_source_home, normalized_target_home):
        return memory_projection_result(
            status='skipped',
            reason='source_home_is_target_home',
            path=target,
        )
    if not _inherits_memory(profile):
        _remove_path(target)
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
    return materialize_provider_memory_file(
        project_root=project_root,
        agent_name=agent_name,
        provider='codex',
        target=target,
        provider_memory_path=source_home / 'AGENTS.md',
        provider_memory_title='Provider User Memory',
        workspace_path=workspace_path,
    )

def _same_path(left: Path, right: Path) -> bool:
    try:
        return Path(left).expanduser().resolve() == Path(right).expanduser().resolve()
    except Exception:
        return Path(left).expanduser() == Path(right).expanduser()


def _plugin_projection_is_current(*, source_tree: Path, source_sha: Path, target_tree: Path, target_sha: Path) -> bool:
    if not target_tree.is_dir():
        return False
    if not _plugin_required_paths_available(source_tree, target_tree):
        return False
    if source_sha.is_file():
        return target_sha.is_file() and _safe_read_text(source_sha) == _safe_read_text(target_sha)
    # Metadata fingerprint is a cheap repair check for legacy projections.
    # Content-addressed bundle selection uses tree_content_fingerprint instead.
    source_fingerprint = _tree_metadata_fingerprint(source_tree)
    if not source_fingerprint:
        return False
    return source_fingerprint == _tree_metadata_fingerprint(target_tree)


def _plugin_required_paths_available(source_tree: Path, target_tree: Path) -> bool:
    for relative in _CODEX_PLUGIN_REQUIRED_RELATIVE_PATHS:
        if (source_tree / relative).exists() and not (target_tree / relative).exists():
            return False
    return True


def _safe_read_text(path: Path) -> str:
    try:
        return path.read_text(encoding='utf-8')
    except Exception:
        return ''


def _tree_metadata_fingerprint(root: Path) -> str:
    digest = hashlib.sha256()
    try:
        for entry in sorted(root.rglob('*')):
            relative = entry.relative_to(root)
            kind = 'd' if entry.is_dir() else 'f' if entry.is_file() else 'l' if entry.is_symlink() else 'o'
            digest.update(kind.encode('utf-8'))
            digest.update(b'\0')
            digest.update(str(relative).encode('utf-8', errors='ignore'))
            digest.update(b'\0')
            if entry.is_file():
                stat = entry.stat()
                digest.update(str(stat.st_size).encode('utf-8'))
                digest.update(b'\0')
                digest.update(str(stat.st_mtime_ns).encode('utf-8'))
                digest.update(b'\0')
    except Exception:
        return ''
    return digest.hexdigest()


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def _system_codex_home() -> Path:
    if os.environ.get('CCB_SOURCE_HOME'):
        return current_provider_source_home() / '.codex'
    raw = str(os.environ.get('CODEX_HOME') or '').strip()
    if raw:
        candidate = Path(raw).expanduser()
        if not _looks_like_ccb_provider_home(candidate):
            return candidate
    return current_provider_source_home() / '.codex'


def _looks_like_ccb_provider_home(path: Path) -> bool:
    parts = Path(path).expanduser().parts
    for index in range(0, max(len(parts) - 4, 0)):
        if parts[index] != 'agents':
            continue
        if parts[index + 2] == 'provider-state' and parts[index + 4] == 'home':
            return True
    return False


def _render_toml_document(payload: dict[str, object]) -> str:
    sections = _render_toml_sections(payload, path=())
    rendered = '\n\n'.join(section for section in sections if section.strip())
    return f'{rendered}\n' if rendered else ''


def _render_toml_sections(payload: dict[str, object], *, path: tuple[str, ...] = ()) -> list[str]:
    scalar_lines: list[str] = []
    child_sections: list[str] = []
    child_tables: list[tuple[str, dict[str, object]]] = []
    for raw_key, value in payload.items():
        key = str(raw_key)
        if value is None:
            continue
        if isinstance(value, dict):
            child_tables.append((key, value))
            continue
        scalar_lines.append(f'{_render_toml_key(key)} = {_render_toml_value(value)}')

    sections: list[str] = []
    if path:
        header = f'[{_render_toml_path(path)}]'
        if scalar_lines:
            sections.append('\n'.join([header, *scalar_lines]))
        elif not child_tables:
            sections.append(header)
    elif scalar_lines:
        sections.append('\n'.join(scalar_lines))

    for key, child in child_tables:
        child_sections.extend(_render_toml_sections(child, path=(*path, key)))
    sections.extend(child_sections)
    return sections


def _render_toml_path(path: tuple[str, ...]) -> str:
    return '.'.join(_render_toml_key_part(part) for part in path)


def _render_toml_key(key: str) -> str:
    return _render_toml_key_part(key)


def _render_toml_key_part(key: str) -> str:
    return key if _BARE_TOML_KEY_RE.fullmatch(key) else json.dumps(key)


def _render_toml_value(value: object) -> str:
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, (date, datetime, time)):
        return value.isoformat()
    if isinstance(value, (list, tuple)):
        return '[' + ', '.join(_render_toml_value(item) for item in value) + ']'
    if isinstance(value, dict):
        return _render_toml_inline_table(value)
    raise TypeError(f'unsupported TOML value type: {type(value).__name__}')


def _render_toml_inline_table(payload: dict[object, object]) -> str:
    items = [
        f'{_render_toml_key(str(key))} = {_render_toml_value(value)}'
        for key, value in payload.items()
        if value is not None
    ]
    if not items:
        return '{}'
    return '{ ' + ', '.join(items) + ' }'


__all__ = [
    'CodexApiAuthority',
    'CodexAuthRefreshResult',
    'codex_api_authority',
    'codex_provider_authority_fingerprint',
    'codex_source_authority_config_payload',
    'codex_auth_sidecar_names',
    'materialize_codex_home_config',
    'refresh_codex_auth_projection',
    'repair_codex_activity_hooks',
]
