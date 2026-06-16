from __future__ import annotations

import hashlib
import importlib
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, time
from pathlib import Path
import re
import shlex
import shutil
import sys

from provider_core.memory_projection import (
    materialize_provider_memory_file,
    memory_projection_result,
    record_memory_projection_event,
)
from provider_core.projected_assets import (
    copy_projected_tree_to_cache,
    remove_projected_path,
    route_projected_tree,
    tree_content_fingerprint,
    write_projected_marker,
)
from provider_core.source_home import current_provider_source_home
from rolepacks.projection import project_role_skills_to_home
from storage.atomic import atomic_write_text
from storage.paths import PathLayout


_CODEX_CUSTOM_PROVIDER_ID = 'custom'
_BARE_TOML_KEY_RE = re.compile(r'^[A-Za-z0-9_-]+$')
_CODEX_PLUGIN_TREE_RELATIVE = Path('.tmp') / 'plugins'
_CODEX_PLUGIN_SHA_RELATIVE = Path('.tmp') / 'plugins.sha'
_CODEX_SKILLS_PROJECTION_LABEL = 'codex-inherited-skills'
_CODEX_COMMANDS_PROJECTION_LABEL = 'codex-inherited-commands'
_CODEX_PLUGIN_PROJECTION_LABEL = 'codex-plugin-bundle'
_CODEX_OWNED_SKILL_NAMES = ('ask',)
_CODEX_LEGACY_OWNED_SKILL_NAMES = ('ccb_config', 'ccb-config')
_CODEX_PLUGIN_REQUIRED_RELATIVE_PATHS = (
    Path('.agents') / 'plugins' / 'marketplace.json',
    Path('.agents') / 'skills',
    Path('plugins'),
)
_MANAGED_CODEX_DISABLED_FEATURES = ('external_migration',)
_CODEX_ACTIVITY_HOOK_EVENTS = (
    'SessionStart',
    'UserPromptSubmit',
    'PreToolUse',
    'PermissionRequest',
    'PostToolUse',
    'Stop',
)
_CODEX_ACTIVITY_HOOK_TIMEOUT_S = 5
_CODEX_INHERITED_HOOK_EVENTS = frozenset({'SessionStart', 'UserPromptSubmit', 'Stop'})
_CODEX_INHERITED_HOOK_COMMAND_MARKERS = ('.hindsight/codex/scripts/',)
_TOML_TABLE_HEADER_RE = re.compile(r'^\s*\[{1,2}[^\]]+\]{1,2}\s*(?:#.*)?$')


@dataclass(frozen=True)
class CodexApiAuthority:
    provider_id: str
    base_url: str
    wire_api: str = 'responses'
    requires_openai_auth: bool = False


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
    memory_projection_event_path: Path | None = None,
    memory_projection_marker_path: Path | None = None,
) -> Path:
    target_home = Path(target_home).expanduser()
    source_home = Path(source_home).expanduser() if source_home is not None else _system_codex_home()
    target_home.mkdir(parents=True, exist_ok=True)
    (target_home / 'sessions').mkdir(parents=True, exist_ok=True)

    target_config = target_home / 'config.toml'
    source_config = source_home / 'config.toml'
    authority = codex_api_authority(profile)

    if authority is not None:
        _write_codex_api_authority_config(
            target_config,
            authority,
            source_config=source_config,
            project_root=project_root,
            workspace_path=workspace_path,
        )
    elif _inherits_config(profile) and _inherits_api(profile) and _source_config_valid(source_config):
        if source_config.is_file():
            payload = _read_source_config_payload(source_config)
            if payload:
                _write_managed_codex_config(
                    target_config,
                    payload,
                    project_root=project_root,
                    workspace_path=workspace_path,
                )
            else:
                _sync_file(source_config, target_config)
                _append_managed_codex_feature_overrides(target_config)
                _append_managed_codex_project_trust(target_config, project_root=project_root, workspace_path=workspace_path)
        else:
            _write_managed_config_stub(target_config, project_root=project_root, workspace_path=workspace_path)
    else:
        _write_managed_config_stub(target_config, project_root=project_root, workspace_path=workspace_path)

    _materialize_auth_file(
        source_home / 'auth.json',
        target_home / 'auth.json',
        profile=profile,
        authority=authority,
    )
    _copy_inherited_tree(
        source_home / 'skills',
        target_home / 'skills',
        enabled=_inherits_skills(profile),
        label=_CODEX_SKILLS_PROJECTION_LABEL,
    )
    project_role_skills_to_home(
        project_root=project_root,
        agent_name=agent_name,
        provider='codex',
        target_skills_dir=target_home / 'skills',
    )
    _route_inherited_tree(
        source_home / 'commands',
        target_home / 'commands',
        enabled=_inherits_commands(profile),
        label=_CODEX_COMMANDS_PROJECTION_LABEL,
    )
    _sync_codex_plugin_projection(
        source_home,
        target_home,
        project_root=project_root,
        shared_cache_root=shared_cache_root,
    )
    memory_result = _materialize_codex_memory(
        source_home,
        target_home,
        profile=profile,
        project_root=project_root,
        agent_name=agent_name,
        workspace_path=workspace_path,
    )
    _install_codex_activity_hooks(
        target_home,
        target_config,
        source_home=source_home,
        project_root=project_root,
        agent_name=agent_name,
        runtime_dir=runtime_dir,
        workspace_path=workspace_path,
    )
    record_memory_projection_event(
        memory_result,
        provider='codex',
        event_path=memory_projection_event_path,
        marker_path=memory_projection_marker_path,
        agent_name=agent_name,
    )
    return target_config


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
    _install_codex_activity_hooks(
        target_home,
        target_home / 'config.toml',
        source_home=source_home,
        project_root=project_root,
        agent_name=agent_name,
        runtime_dir=runtime_dir,
        workspace_path=workspace_path,
    )


def codex_api_authority(profile) -> CodexApiAuthority | None:
    if profile is None or _inherits_api(profile):
        return None
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


def _inherits_api(profile) -> bool:
    return True if profile is None else bool(getattr(profile, 'inherit_api', True))


def _inherits_auth(profile) -> bool:
    return True if profile is None else bool(getattr(profile, 'inherit_auth', True))


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


def _write_codex_api_authority_config(
    target: Path,
    authority: CodexApiAuthority,
    *,
    source_config: Path,
    project_root: Path | None,
    workspace_path: Path | None,
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = _managed_codex_config_payload(source_config, authority=authority)
    _trust_managed_codex_project_paths(payload, project_root=project_root, workspace_path=workspace_path)
    target.write_text(_render_toml_document(payload), encoding='utf-8')


def _write_managed_config_stub(target: Path, *, project_root: Path | None, workspace_path: Path | None) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    payload: dict[str, object] = {}
    _trust_managed_codex_project_paths(payload, project_root=project_root, workspace_path=workspace_path)
    rendered = _render_toml_document(payload) if payload else '# ccb agent-local codex config\n'
    target.write_text(rendered, encoding='utf-8')


def _write_managed_codex_config(
    target: Path,
    payload: dict[str, object],
    *,
    project_root: Path | None,
    workspace_path: Path | None,
) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    sanitized = _disable_interactive_migration_features(payload)
    _strip_unmanaged_hook_config(sanitized)
    _trust_managed_codex_project_paths(sanitized, project_root=project_root, workspace_path=workspace_path)
    target.write_text(_render_toml_document(sanitized), encoding='utf-8')


def _append_managed_codex_feature_overrides(target: Path) -> None:
    if not target.is_file():
        return
    try:
        text = target.read_text(encoding='utf-8')
    except Exception:
        return
    target.write_text(_merge_managed_codex_feature_overrides(text), encoding='utf-8')


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


def _materialize_auth_file(source: Path, target: Path, *, profile, authority: CodexApiAuthority | None) -> None:
    if authority is not None:
        explicit_key = _explicit_api_key(profile)
        if explicit_key:
            _write_auth_file(target, explicit_key)
        else:
            target.unlink(missing_ok=True)
        return
    _sync_auth_file(source, target, profile=profile)


def _sync_auth_file(source: Path, target: Path, *, profile) -> None:
    if not _inherits_auth(profile):
        return
    if not source.is_file():
        target.unlink(missing_ok=True)
        return
    _sync_file(source, target)


def _write_auth_file(target: Path, api_key: str) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps({'OPENAI_API_KEY': api_key}, ensure_ascii=False, separators=(',', ':'))
    target.write_text(f'{payload}\n', encoding='utf-8')


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


def _copy_inherited_tree(source: Path, target: Path, *, enabled: bool, label: str) -> None:
    source = Path(source).expanduser()
    target = Path(target).expanduser()
    if not enabled:
        remove_projected_path(target, label=label)
        return
    if not source.is_dir():
        remove_projected_path(target, label=label)
        return
    if _same_path(source, target):
        return
    marker = Path(f'{target}.ccb-projection.json')
    if (target.exists() or target.is_symlink()) and not marker.is_file():
        if target.is_symlink():
            _repair_owned_codex_skill_entries(source, target)
            return
        if not target.is_dir() or tree_content_fingerprint(target) != tree_content_fingerprint(source):
            _repair_owned_codex_skill_entries(source, target)
            return
    if copy_projected_tree_to_cache(source, target, label=label):
        return
    remove_projected_path(target, label=label)


def _repair_owned_codex_skill_entries(source: Path, target: Path) -> None:
    if not target.is_dir() or target.is_symlink():
        return
    for legacy_name in _CODEX_LEGACY_OWNED_SKILL_NAMES:
        _remove_path(target / legacy_name)
    for skill_name in _CODEX_OWNED_SKILL_NAMES:
        source_skill = source / skill_name
        if not source_skill.is_dir():
            continue
        target_skill = target / skill_name
        _remove_path(target_skill)
        try:
            shutil.copytree(source_skill, target_skill)
        except Exception:
            _remove_path(target_skill)


def _sync_codex_plugin_projection(
    source_home: Path,
    target_home: Path,
    *,
    project_root: Path | None,
    shared_cache_root: Path | None,
) -> None:
    source_tree = source_home / _CODEX_PLUGIN_TREE_RELATIVE
    source_sha = source_home / _CODEX_PLUGIN_SHA_RELATIVE
    target_tree = target_home / _CODEX_PLUGIN_TREE_RELATIVE
    target_sha = target_home / _CODEX_PLUGIN_SHA_RELATIVE
    if not source_tree.is_dir():
        remove_projected_path(target_tree, label=_CODEX_PLUGIN_PROJECTION_LABEL)
        _remove_path(target_sha)
        return
    if _same_path(source_tree, target_tree):
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
    if source_sha.is_file() and _plugin_projection_is_current(
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
        remove_projected_path(target_tree, label=_CODEX_PLUGIN_PROJECTION_LABEL, source=source_tree)
        target_tree.parent.mkdir(parents=True, exist_ok=True)
        try:
            target_tree.symlink_to(bundle_tree, target_is_directory=True)
            write_projected_marker(
                target_tree,
                label=_CODEX_PLUGIN_PROJECTION_LABEL,
                mode='symlink',
                source=bundle_tree,
            )
            projected = True
        except Exception:
            projected = route_projected_tree(
                bundle_tree,
                target_tree,
                label=_CODEX_PLUGIN_PROJECTION_LABEL,
                allow_unmarked_replace=True,
            )
    else:
        projected = route_projected_tree(
            source_tree,
            target_tree,
            label=_CODEX_PLUGIN_PROJECTION_LABEL,
            allow_unmarked_replace=True,
        )
    if not projected or not _plugin_required_paths_available(source_tree, target_tree):
        return
    _remove_path(target_sha)
    if source_sha.is_file():
        _sync_file(source_sha, target_sha)
    else:
        target_sha.parent.mkdir(parents=True, exist_ok=True)
        target_sha.write_text(f'{bundle_sha}\n', encoding='utf-8')


def _install_codex_activity_hooks(
    target_home: Path,
    target_config: Path,
    *,
    source_home: Path | None = None,
    project_root: Path | None,
    agent_name: str | None,
    runtime_dir: Path | None,
    workspace_path: Path | None,
) -> None:
    if project_root is None or agent_name is None or runtime_dir is None or workspace_path is None:
        return
    project_id = _project_id_for_path(project_root)
    if not project_id:
        return
    hooks_path = Path(target_home).expanduser() / 'hooks.json'
    command = _codex_activity_hook_command(
        project_id=project_id,
        agent_name=str(agent_name),
        runtime_dir=Path(runtime_dir).expanduser(),
        workspace_path=Path(workspace_path).expanduser(),
    )
    event_groups = _codex_activity_hook_events(command)
    event_groups = _merge_codex_hook_groups(
        event_groups,
        _allowed_inherited_codex_hooks(source_home),
    )
    hooks_payload = {'hooks': event_groups}
    hooks_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        hooks_path,
        json.dumps(hooks_payload, ensure_ascii=False, indent=2) + '\n',
    )
    _merge_codex_activity_hook_state(
        target_config,
        hooks_path=hooks_path,
        event_groups=event_groups,
    )


def _project_id_for_path(project_root: Path) -> str:
    try:
        return PathLayout(Path(project_root).expanduser()).project_id
    except Exception:
        return ''


def _codex_activity_hook_command(
    *,
    project_id: str,
    agent_name: str,
    runtime_dir: Path,
    workspace_path: Path,
) -> str:
    script_path = Path(__file__).resolve().parents[2] / 'bin' / 'ccb-provider-activity-hook.py'
    parts = [
        sys.executable,
        str(script_path),
        '--provider',
        'codex',
        '--project-id',
        project_id,
        '--agent-name',
        agent_name,
        '--runtime-dir',
        str(runtime_dir),
        '--workspace',
        str(workspace_path),
    ]
    return ' '.join(shlex.quote(str(part)) for part in parts)


def _codex_activity_hook_events(command: str) -> dict[str, list[dict[str, object]]]:
    return {
        event_name: [
            {
                'hooks': [
                    {
                        'type': 'command',
                        'command': command,
                        'timeout': _CODEX_ACTIVITY_HOOK_TIMEOUT_S,
                    }
                ]
            }
        ]
        for event_name in _CODEX_ACTIVITY_HOOK_EVENTS
    }


def _allowed_inherited_codex_hooks(source_home: Path | None) -> dict[str, list[dict[str, object]]]:
    if source_home is None:
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
        if event not in _CODEX_INHERITED_HOOK_EVENTS or not isinstance(raw_groups, list):
            continue
        groups: list[dict[str, object]] = []
        for raw_group in raw_groups:
            group = _allowed_inherited_codex_hook_group(raw_group)
            if group is not None:
                groups.append(group)
        if groups:
            selected[event] = groups
    return selected


def _allowed_inherited_codex_hook_group(raw_group: object) -> dict[str, object] | None:
    if not isinstance(raw_group, dict):
        return None
    raw_handlers = raw_group.get('hooks')
    if not isinstance(raw_handlers, list):
        return None
    handlers: list[dict[str, object]] = []
    for raw_handler in raw_handlers:
        handler = _allowed_inherited_codex_hook_handler(raw_handler)
        if handler is None:
            return None
        handlers.append(handler)
    if not handlers:
        return None
    group: dict[str, object] = {'hooks': handlers}
    if raw_group.get('matcher') is not None:
        group['matcher'] = str(raw_group.get('matcher') or '')
    return group


def _allowed_inherited_codex_hook_handler(raw_handler: object) -> dict[str, object] | None:
    if not isinstance(raw_handler, dict):
        return None
    if str(raw_handler.get('type') or '') != 'command':
        return None
    command = str(raw_handler.get('command') or '')
    normalized_command = command.replace('\\', '/')
    if not any(marker in normalized_command for marker in _CODEX_INHERITED_HOOK_COMMAND_MARKERS):
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
        _codex_hook_group_command_identity(group)
        for groups in merged.values()
        for group in groups
    }
    for event, groups in inherited.items():
        event_groups = merged.setdefault(event, [])
        for group in groups:
            identity = _codex_hook_group_command_identity(group)
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


def _merge_codex_activity_hook_state(
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
            _replace_managed_codex_activity_state_block(existing_text, state_table),
            encoding='utf-8',
        )
        return
    hooks_payload = payload.get('hooks')
    hooks_table = hooks_payload if isinstance(hooks_payload, dict) else {}
    if hooks_table is not hooks_payload:
        payload['hooks'] = hooks_table
    hooks_table['state'] = state_table
    target_config.write_text(_render_toml_document(payload), encoding='utf-8')


def _replace_managed_codex_activity_state_block(text: str, state_table: dict[str, object]) -> str:
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
        'timeout': int(handler.get('timeout') or _CODEX_ACTIVITY_HOOK_TIMEOUT_S),
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
    'codex_api_authority',
    'codex_provider_authority_fingerprint',
    'materialize_codex_home_config',
    'repair_codex_activity_hooks',
]
