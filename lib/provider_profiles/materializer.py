from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
import shutil
from typing import TYPE_CHECKING
from datetime import datetime, timezone

if TYPE_CHECKING:
    from agents.models import AgentSpec
from provider_profiles.models import ProviderProfileSpec, ResolvedProviderProfile
from provider_profiles.codex_home_config import materialize_codex_home_config
from provider_core.pathing import session_filename_for_agent
from storage.atomic import atomic_write_json, atomic_write_json_if_changed
from storage.paths import PathLayout


_API_ENV_KEYS = {
    'codex': {
        'OPENAI_API_KEY',
        'OPENAI_BASE_URL',
        'OPENAI_API_BASE',
        'OPENAI_ORG_ID',
        'OPENAI_ORGANIZATION',
    },
    'claude': {'ANTHROPIC_API_KEY', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_BASE_URL'},
    'gemini': {
        'GEMINI_API_KEY',
        'GEMINI_MODEL',
        'GOOGLE_API_KEY',
        'GOOGLE_API_BASE',
        'GOOGLE_GEMINI_BASE_URL',
        'GOOGLE_VERTEX_BASE_URL',
        'GOOGLE_GENAI_USE_VERTEXAI',
        'GOOGLE_GENAI_USE_GCA',
        'GOOGLE_CLOUD_PROJECT',
        'GOOGLE_CLOUD_LOCATION',
        'GOOGLE_APPLICATION_CREDENTIALS',
    },
    'deepseek': {
        'DEEPCODE_API_KEY',
        'DEEPCODE_BASE_URL',
    },
    'dsh': {
        'DEEPSEEK_API_KEY',
        'DEEPSEEK_BASE_URL',
    },
}


def materialize_provider_profile(
    *,
    layout: PathLayout,
    spec: 'AgentSpec',
    workspace_path: Path,
    materialize_home: bool = True,
) -> ResolvedProviderProfile:
    validate_provider_runtime_home_policy(spec)
    runtime_dir = layout.agent_provider_runtime_dir(spec.name, spec.provider)
    runtime_dir.mkdir(parents=True, exist_ok=True)
    profile_spec = _profile_spec_with_agent_api_env(spec, spec.provider_profile)
    profile_root = _resolve_profile_root(layout, spec, profile_spec)

    if spec.provider == 'codex':
        profile = _materialize_codex_profile(
            layout=layout,
            spec=spec,
            profile_spec=profile_spec,
            profile_root=profile_root,
            workspace_path=workspace_path,
            materialize_home=materialize_home,
        )
    elif spec.provider == 'claude':
        profile = _materialize_claude_profile(
            spec=spec,
            profile_spec=profile_spec,
            profile_root=profile_root,
        )
    elif spec.provider == 'gemini':
        profile = _materialize_api_profile(
            spec=spec,
            profile_spec=profile_spec,
            profile_root=profile_root,
        )
    else:
        profile = ResolvedProviderProfile(
            provider=spec.provider,
            agent_name=spec.name,
            mode=profile_spec.mode,
            profile_root=str(profile_root) if profile_root is not None else None,
            runtime_home=None,
            env=dict(profile_spec.env),
            mcp_servers=dict(profile_spec.mcp_servers),
            plugins=dict(profile_spec.plugins),
            **_skill_profile_fields(profile_spec),
            inherit_api=profile_spec.inherit_api,
            inherit_auth=profile_spec.inherit_auth,
            inherit_config=profile_spec.inherit_config,
            inherit_skills=profile_spec.inherit_skills,
            inherit_commands=profile_spec.inherit_commands,
            inherit_memory=profile_spec.inherit_memory,
        )

    _write_profile_record(runtime_dir, profile)
    return profile


def _profile_spec_with_agent_api_env(
    spec: 'AgentSpec',
    profile_spec: ProviderProfileSpec,
) -> ProviderProfileSpec:
    """Make every CCB-configured Provider API value visible to home projection.

    Launchers already give ``agents.<name>.env`` precedence over
    ``provider_profile.env``.  Home projection and authority fingerprints must
    resolve the same values, otherwise an explicit API credential can compete
    with an inherited native login before the process even starts.
    """
    api_keys = provider_api_env_keys(spec.provider)
    configured = {
        str(key): str(value)
        for key, value in dict(getattr(spec, 'env', {}) or {}).items()
        if str(key) in api_keys and str(value).strip()
    }
    if not configured:
        return profile_spec
    return replace(
        profile_spec,
        env={**dict(profile_spec.env), **configured},
    )


def load_resolved_provider_profile(runtime_dir: Path) -> ResolvedProviderProfile | None:
    path = Path(runtime_dir) / 'provider-profile.json'
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    try:
        return ResolvedProviderProfile.from_record(data)
    except Exception:
        return None


def provider_api_env_keys(provider: str) -> set[str]:
    return set(_API_ENV_KEYS.get(str(provider or '').strip().lower(), set()))


def validate_provider_runtime_home_policy(spec: 'AgentSpec') -> None:
    provider = str(spec.provider or '').strip().lower()
    profile_spec = spec.provider_profile
    if provider == 'codex' or profile_spec.home is None:
        return
    raise ValueError(f'{spec.name}: provider_profile.home is supported only for codex runtime_home overrides')


def validate_provider_runtime_home_uniqueness(*, layout: PathLayout, specs) -> None:
    seen: dict[tuple[str, str], str] = {}
    for spec in specs:
        validate_provider_runtime_home_policy(spec)
        provider = str(spec.provider or '').strip().lower()
        home = _effective_provider_runtime_home(layout=layout, spec=spec)
        key = (provider, _normalize_runtime_home(home))
        prior = seen.get(key)
        if prior is not None:
            raise ValueError(f'duplicate effective {provider}_home for agents {prior} and {spec.name}: {key[1]}')
        seen[key] = spec.name


def _effective_provider_runtime_home(*, layout: PathLayout, spec: 'AgentSpec') -> Path:
    provider = str(spec.provider or '').strip().lower()
    profile_spec = spec.provider_profile
    if provider == 'codex' and _codex_profile_uses_explicit_runtime_home(profile_spec):
        return _resolve_profile_root(layout, spec, profile_spec)
    return layout.agent_provider_state_dir(spec.name, provider) / 'home'


def _normalize_runtime_home(path: Path) -> str:
    try:
        return str(Path(path).expanduser().resolve())
    except Exception:
        return str(Path(path).expanduser().absolute())


def _materialize_codex_profile(
    *,
    layout: PathLayout,
    spec: 'AgentSpec',
    profile_spec: ProviderProfileSpec,
    profile_root: Path,
    workspace_path: Path,
    materialize_home: bool,
) -> ResolvedProviderProfile:
    runtime_dir = layout.agent_provider_runtime_dir(spec.name, spec.provider)
    runtime_home = _effective_provider_runtime_home(layout=layout, spec=spec)
    if not _codex_profile_uses_explicit_runtime_home(profile_spec):
        migrated_legacy_home = _migrate_legacy_codex_profile_runtime_home(
            layout=layout,
            spec=spec,
            source_home=profile_root,
            target_home=runtime_home,
        )
        if migrated_legacy_home:
            _discard_migrated_codex_projection(runtime_home)
    if materialize_home:
        materialize_codex_home_config(
            runtime_home,
            profile=profile_spec,
            project_root=layout.project_root,
            agent_name=spec.name,
            runtime_dir=runtime_dir,
            workspace_path=workspace_path,
            shared_cache_root=layout.shared_cache_dir,
            memory_projection_event_path=layout.agent_events_path(spec.name),
            memory_projection_marker_path=runtime_dir / 'codex-memory-projection.json',
            model=spec.model,
            model_catalog_json=profile_spec.env.get('model_catalog_json'),
        )

    return ResolvedProviderProfile(
        provider=spec.provider,
        agent_name=spec.name,
        mode=profile_spec.mode,
        profile_root=str(profile_root) if _codex_profile_uses_explicit_runtime_home(profile_spec) else None,
        runtime_home=str(runtime_home) if runtime_home is not None else None,
        env=dict(profile_spec.env),
        mcp_servers=dict(profile_spec.mcp_servers),
        plugins=dict(profile_spec.plugins),
        **_skill_profile_fields(profile_spec),
        inherit_api=profile_spec.inherit_api,
        inherit_auth=profile_spec.inherit_auth,
        inherit_config=profile_spec.inherit_config,
        inherit_skills=profile_spec.inherit_skills,
        inherit_commands=profile_spec.inherit_commands,
        inherit_memory=profile_spec.inherit_memory,
    )


def _materialize_api_profile(
    *,
    spec: 'AgentSpec',
    profile_spec: ProviderProfileSpec,
    profile_root: Path,
) -> ResolvedProviderProfile:
    api_keys = provider_api_env_keys(spec.provider)
    env = {key: value for key, value in profile_spec.env.items() if key in api_keys or profile_spec.mode != 'inherit'}
    return ResolvedProviderProfile(
        provider=spec.provider,
        agent_name=spec.name,
        mode=profile_spec.mode,
        profile_root=str(profile_root),
        runtime_home=None,
        env=env,
        mcp_servers=dict(profile_spec.mcp_servers),
        plugins=dict(profile_spec.plugins),
        **_skill_profile_fields(profile_spec),
        inherit_api=profile_spec.inherit_api,
        inherit_auth=profile_spec.inherit_auth,
        inherit_config=profile_spec.inherit_config,
        inherit_skills=profile_spec.inherit_skills,
        inherit_commands=profile_spec.inherit_commands,
        inherit_memory=profile_spec.inherit_memory,
    )


def _materialize_claude_profile(
    *,
    spec: 'AgentSpec',
    profile_spec: ProviderProfileSpec,
    profile_root: Path,
) -> ResolvedProviderProfile:
    env = {
        key: value
        for key, value in profile_spec.env.items()
        if key in provider_api_env_keys('claude') or profile_spec.mode != 'inherit'
    }
    return ResolvedProviderProfile(
        provider=spec.provider,
        agent_name=spec.name,
        mode=profile_spec.mode,
        profile_root=str(profile_root),
        runtime_home=None,
        env=env,
        mcp_servers=dict(profile_spec.mcp_servers),
        plugins=dict(profile_spec.plugins),
        **_skill_profile_fields(profile_spec),
        inherit_api=profile_spec.inherit_api,
        inherit_auth=profile_spec.inherit_auth,
        inherit_config=profile_spec.inherit_config,
        inherit_skills=profile_spec.inherit_skills,
        inherit_commands=profile_spec.inherit_commands,
        inherit_memory=profile_spec.inherit_memory,
    )


def _skill_profile_fields(profile_spec: ProviderProfileSpec) -> dict[str, object]:
    return {
        'inherited_skill_include': tuple(profile_spec.inherited_skill_include),
        'inherited_skill_exclude': tuple(profile_spec.inherited_skill_exclude),
        'skill_overlays': dict(profile_spec.skill_overlays),
    }


def _resolve_profile_root(layout: PathLayout, spec: AgentSpec, profile_spec: ProviderProfileSpec) -> Path:
    if profile_spec.home:
        raw = Path(profile_spec.home).expanduser()
        if not raw.is_absolute():
            raw = layout.project_root / raw
        return raw.resolve()
    return (layout.provider_profiles_dir / spec.name / spec.provider).resolve()


def _codex_profile_uses_explicit_runtime_home(profile_spec: ProviderProfileSpec) -> bool:
    return profile_spec.home is not None


_CODEX_RUNTIME_HOME_SENTINELS = (
    Path('sessions'),
    Path('archived-sessions'),
    Path('auth.json'),
    Path('history.jsonl'),
    Path('logs_2.sqlite'),
    Path('state_5.sqlite'),
    Path('.ccb-session-namespace.json'),
    Path('log'),
    Path('logs'),
    Path('shell_snapshots'),
    Path('.tmp') / 'plugins',
    Path('.tmp') / 'plugins.sha',
)
_CODEX_SESSION_MIGRATION_SENTINELS = (
    Path('sessions'),
    Path('archived-sessions'),
    Path('auth.json'),
    Path('history.jsonl'),
    Path('logs_2.sqlite'),
    Path('state_5.sqlite'),
    Path('.ccb-session-namespace.json'),
    Path('log'),
    Path('logs'),
    Path('shell_snapshots'),
    Path('.tmp') / 'plugins',
    Path('.tmp') / 'plugins.sha',
)
_MIGRATION_ABORT = object()


def _migrate_legacy_codex_profile_runtime_home(
    *,
    layout: PathLayout,
    spec: 'AgentSpec',
    source_home: Path,
    target_home: Path,
) -> bool:
    source = Path(source_home).expanduser()
    target = Path(target_home).expanduser()
    if source == target or not _looks_like_legacy_codex_runtime_home(source):
        return False
    if source.is_symlink() or not _is_within(source, layout.provider_profiles_dir):
        _record_codex_profile_migration_event(
            layout,
            spec,
            status='skipped',
            reason='legacy_home_out_of_bounds_or_symlink',
            source_home=source,
            target_home=target,
        )
        return False
    if not _is_within(target, layout.agent_provider_state_dir(spec.name, spec.provider)):
        _record_codex_profile_migration_event(
            layout,
            spec,
            status='skipped',
            reason='target_home_out_of_bounds',
            source_home=source,
            target_home=target,
        )
        return False
    if _agent_runtime_blocks_legacy_migration(layout, spec):
        _record_codex_profile_migration_event(
            layout,
            spec,
            status='skipped',
            reason='agent_runtime_active',
            source_home=source,
            target_home=target,
        )
        return False
    if _session_migration_material_contains_symlink(source):
        _record_codex_profile_migration_event(
            layout,
            spec,
            status='skipped',
            reason='legacy_home_contains_symlink',
            source_home=source,
            target_home=target,
        )
        return False
    prepared_session_authority = _prepare_legacy_codex_session_authority(
        layout=layout,
        spec=spec,
        source_home=source,
        target_home=target,
    )
    if prepared_session_authority is _MIGRATION_ABORT:
        _record_codex_profile_migration_event(
            layout,
            spec,
            status='skipped',
            reason='session_authority_preflight_failed',
            source_home=source,
            target_home=target,
        )
        return False
    target.mkdir(parents=True, exist_ok=True)
    _merge_legacy_codex_session_material(source, target)
    if prepared_session_authority is not None:
        session_file, payload = prepared_session_authority
        atomic_write_json(session_file, payload)
    _remove_empty_parents(source, stop_at=layout.provider_profiles_dir)
    _record_codex_profile_migration_event(
        layout,
        spec,
        status='migrated',
        reason='legacy_profile_runtime_home_migrated',
        source_home=source,
        target_home=target,
    )
    return True


def _discard_migrated_codex_projection(runtime_home: Path) -> None:
    _remove_tree_if_exists(runtime_home / '.tmp' / 'plugins')
    (runtime_home / '.tmp' / 'plugins.sha').unlink(missing_ok=True)
    (runtime_home / 'AGENTS.md').unlink(missing_ok=True)


def _remove_tree_if_exists(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
        return
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)


def _looks_like_legacy_codex_runtime_home(path: Path) -> bool:
    if not path.exists() or not path.is_dir() or path.is_symlink():
        return False
    return any(
        (path / relative).exists() or (path / relative).is_symlink()
        for relative in _CODEX_RUNTIME_HOME_SENTINELS
    )


def _merge_tree(source: Path, target: Path) -> None:
    if not source.is_dir() or source.is_symlink():
        return
    target.mkdir(parents=True, exist_ok=True)
    for child in sorted(source.iterdir(), key=lambda item: item.name):
        destination = target / child.name
        if child.is_symlink():
            continue
        if not destination.exists() and not destination.is_symlink():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(child), str(destination))
            continue
        if child.is_dir() and destination.is_dir() and not destination.is_symlink():
            _merge_tree(child, destination)
    _remove_empty_dir(source)


def _merge_legacy_codex_session_material(source: Path, target: Path) -> None:
    target.mkdir(parents=True, exist_ok=True)
    for relative in _CODEX_SESSION_MIGRATION_SENTINELS:
        child = source / relative
        if not child.exists() or child.is_symlink():
            continue
        destination = target / relative
        if child.is_dir():
            _merge_tree(child, destination)
            continue
        if not destination.exists() and not destination.is_symlink():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(child), str(destination))
    _remove_empty_parents(source, stop_at=source)


def _prepare_legacy_codex_session_authority(
    *,
    layout: PathLayout,
    spec: 'AgentSpec',
    source_home: Path,
    target_home: Path,
) -> tuple[Path, dict[str, object]] | object | None:
    if not _has_legacy_codex_session_material(source_home):
        return None
    session_file = layout.ccb_dir / session_filename_for_agent('codex', spec.name)
    if not session_file.is_file():
        return _MIGRATION_ABORT
    try:
        payload = json.loads(session_file.read_text(encoding='utf-8'))
    except Exception:
        return _MIGRATION_ABORT
    if not isinstance(payload, dict):
        return _MIGRATION_ABORT
    source_sessions = source_home / 'sessions'
    target_sessions = target_home / 'sessions'
    changed = False
    changed |= _rewrite_path_field(payload, 'codex_home', source_home, target_home)
    changed |= _rewrite_path_field(payload, 'codex_session_root', source_sessions, target_sessions)
    changed |= _rewrite_nested_path_field(payload, 'codex_session_path', source_sessions, target_sessions)
    for key in ('start_cmd', 'codex_start_cmd'):
        changed |= _rewrite_command_field(payload, key, source_home, target_home)
    if changed:
        return session_file, payload
    return _MIGRATION_ABORT


def _has_legacy_codex_session_material(source_home: Path) -> bool:
    return any((source_home / relative).exists() for relative in _CODEX_SESSION_MIGRATION_SENTINELS)


def _session_migration_material_contains_symlink(source_home: Path) -> bool:
    for relative in _CODEX_SESSION_MIGRATION_SENTINELS:
        root = source_home / relative
        if not root.exists() and not root.is_symlink():
            continue
        if _tree_contains_symlink(root):
            return True
    return False


def _tree_contains_symlink(root: Path) -> bool:
    if root.is_symlink():
        return True
    try:
        for current, dirnames, filenames in os.walk(root, followlinks=False):
            current_path = Path(current)
            for name in [*dirnames, *filenames]:
                if (current_path / name).is_symlink():
                    return True
    except OSError:
        return True
    return False


def _agent_runtime_blocks_legacy_migration(layout: PathLayout, spec: 'AgentSpec') -> bool:
    runtime_path = layout.agent_runtime_path(spec.name)
    if not runtime_path.is_file():
        return False
    try:
        payload = json.loads(runtime_path.read_text(encoding='utf-8'))
    except Exception:
        return True
    if not isinstance(payload, dict):
        return True
    state = str(payload.get('state') or '').strip().lower()
    if state in {'', 'stopped', 'failed'}:
        return False
    if any(_pid_alive(_coerce_pid(payload.get(key))) for key in ('pid', 'runtime_pid')):
        return True
    return state in {'starting', 'busy', 'stopping'}


def _coerce_pid(value: object) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _record_codex_profile_migration_event(
    layout: PathLayout,
    spec: 'AgentSpec',
    *,
    status: str,
    reason: str,
    source_home: Path,
    target_home: Path,
) -> None:
    path = layout.agent_events_path(spec.name)
    payload = {
        'record_type': 'agent_event',
        'event_type': 'codex_profile_migration',
        'provider': 'codex',
        'agent_name': spec.name,
        'status': status,
        'reason': reason,
        'source_home': str(source_home),
        'target_home': str(target_home),
        'created_at': datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('a', encoding='utf-8') as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + '\n')
    except OSError:
        return


def _rewrite_path_field(payload: dict[str, object], key: str, source: Path, target: Path) -> bool:
    current = _path_or_none(payload.get(key))
    if current is None or not _same_path(current, source):
        return False
    payload[key] = str(target)
    return True


def _rewrite_nested_path_field(payload: dict[str, object], key: str, source: Path, target: Path) -> bool:
    current = _path_or_none(payload.get(key))
    if current is None:
        return False
    replacement = _replace_path_prefix(current, source, target)
    if replacement is None:
        return False
    payload[key] = str(replacement)
    return True


def _rewrite_command_field(payload: dict[str, object], key: str, source_home: Path, target_home: Path) -> bool:
    current = str(payload.get(key) or '')
    if not current:
        return False
    source_sessions = source_home / 'sessions'
    target_sessions = target_home / 'sessions'
    updated = _replace_path_text(current, source_sessions, target_sessions)
    updated = _replace_path_text(updated, source_home, target_home)
    if updated == current:
        return False
    payload[key] = updated
    return True


def _replace_path_text(text: str, source: Path, target: Path) -> str:
    source_text = str(source)
    if not source_text:
        return text
    target_text = str(target)
    result: list[str] = []
    index = 0
    while True:
        match = text.find(source_text, index)
        if match < 0:
            result.append(text[index:])
            return ''.join(result)
        end = match + len(source_text)
        if _path_text_match_has_boundary(text, match, end):
            result.append(text[index:match])
            result.append(target_text)
            index = end
            continue
        result.append(text[index:end])
        index = end


def _path_text_match_has_boundary(text: str, start: int, end: int) -> bool:
    before = text[start - 1] if start > 0 else ''
    after = text[end] if end < len(text) else ''
    return _path_text_left_boundary(before) and _path_text_right_boundary(after)


def _path_text_left_boundary(char: str) -> bool:
    return not char or char.isspace() or char in {'=', ':', ';', ',', '"', "'", '(', '[', '{'}


def _path_text_right_boundary(char: str) -> bool:
    return not char or char.isspace() or char == '/' or char in {':', ';', ',', '"', "'", ')', ']', '}'}


def _replace_path_prefix(path: Path, source: Path, target: Path) -> Path | None:
    try:
        relative = path.expanduser().resolve(strict=False).relative_to(source.expanduser().resolve(strict=False))
    except Exception:
        return None
    return target / relative


def _path_or_none(value: object) -> Path | None:
    raw = str(value or '').strip()
    if not raw:
        return None
    try:
        return Path(raw).expanduser()
    except Exception:
        return None


def _same_path(left: Path, right: Path) -> bool:
    try:
        return left.expanduser().resolve(strict=False) == right.expanduser().resolve(strict=False)
    except Exception:
        return left.expanduser() == right.expanduser()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.expanduser().resolve(strict=False).relative_to(root.expanduser().resolve(strict=False))
        return True
    except Exception:
        return False


def _remove_empty_parents(path: Path, *, stop_at: Path) -> None:
    stop = stop_at.expanduser().resolve(strict=False)
    current = path.expanduser()
    while True:
        if _same_path(current, stop):
            return
        if not _remove_empty_dir(current):
            return
        current = current.parent


def _remove_empty_dir(path: Path) -> bool:
    try:
        path.rmdir()
        return True
    except OSError:
        return False


def _write_profile_record(runtime_dir: Path, profile: ResolvedProviderProfile) -> Path:
    path = Path(runtime_dir) / 'provider-profile.json'
    atomic_write_json_if_changed(path, profile.to_record())
    return path


__all__ = [
    'load_resolved_provider_profile',
    'materialize_provider_profile',
    'provider_api_env_keys',
    'validate_provider_runtime_home_policy',
    'validate_provider_runtime_home_uniqueness',
]
