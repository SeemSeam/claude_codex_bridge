from __future__ import annotations

import getpass
import json
import os
import platform
from pathlib import Path
import shutil
import subprocess

from provider_core.memory_projection import (
    memory_projection_result,
    record_memory_projection_event,
    text_file_sha256,
)
from provider_core.projected_assets import route_projected_tree
from provider_core.source_home import current_provider_source_home
from provider_profiles import provider_api_env_keys
from rolepacks.projection import project_role_skills_to_home
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

_CLAUDE_RUNTIME_SETTINGS_KEYS = ('hooks', 'permissions')
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
_MACOS_KEYCHAIN_CLAUDE_SERVICES = ('Claude Code-credentials', 'Claude Code-custom-oauth', 'Claude Code')
_CLAUDE_SKILLS_PROJECTION_LABEL = 'claude-inherited-skills'
_CLAUDE_COMMANDS_PROJECTION_LABEL = 'claude-inherited-commands'


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
    refresh_home: bool = True,
    auto_permission: bool = False,
    project_root: Path | None = None,
    agent_name: str | None = None,
    workspace_path: Path | None = None,
    memory_projection_event_path: Path | None = None,
    memory_projection_marker_path: Path | None = None,
) -> dict[str, str]:
    layout = resolve_claude_home_layout(runtime_dir, profile)
    if refresh_home:
        materialize_claude_home_config(
            layout.home_root,
            profile=profile,
            project_root=project_root,
            agent_name=agent_name,
            workspace_path=workspace_path,
            auto_permission=auto_permission,
            memory_projection_event_path=memory_projection_event_path,
            memory_projection_marker_path=memory_projection_marker_path,
        )
    overrides = {
        'HOME': str(layout.home_root),
        'CLAUDE_PROJECTS_ROOT': str(layout.projects_root),
        'CLAUDE_PROJECT_ROOT': str(layout.projects_root),
    }

    if "WSL_DISTRO_NAME" in os.environ:
        # We are running inside WSL. The target claude executable might be a Windows binary (via interop).
        # We must set USERPROFILE (which Windows Node.js uses as home) to the same isolated path.
        # We also instruct WSLENV to automatically translate these paths to Windows format (/p)
        # when invoking a Windows executable. Linux executables will ignore WSLENV.
        overrides['USERPROFILE'] = str(layout.home_root)
        wslenv_additions = "HOME/p:USERPROFILE/p:CLAUDE_PROJECTS_ROOT/p:CLAUDE_PROJECT_ROOT/p"
        existing_wslenv = os.environ.get("WSLENV", "")
        if existing_wslenv:
            overrides['WSLENV'] = f"{wslenv_additions}:{existing_wslenv}"
        else:
            overrides['WSLENV'] = wslenv_additions

    return overrides


def materialize_claude_home_config(
    target_home: Path,
    *,
    profile=None,
    source_home: Path | None = None,
    project_root: Path | None = None,
    agent_name: str | None = None,
    workspace_path: Path | None = None,
    auto_permission: bool = False,
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
) -> dict[str, object]:
    target_layout.home_root.mkdir(parents=True, exist_ok=True)
    target_layout.claude_dir.mkdir(parents=True, exist_ok=True)
    target_layout.projects_root.mkdir(parents=True, exist_ok=True)
    target_layout.session_env_root.mkdir(parents=True, exist_ok=True)

    if target_layout.home_root == source_home.expanduser():
        _ensure_trust_file(target_layout.trust_path)
        return memory_projection_result(
            status='skipped',
            reason='source_home_is_target_home',
            path=target_layout.claude_dir / 'CLAUDE.md',
        )

    _materialize_settings(source_home, target_layout, profile=profile, auto_permission=auto_permission)
    _materialize_macos_keychain_preferences(source_home, target_layout, profile=profile)
    _materialize_auth(source_home, target_layout, profile=profile)
    _materialize_trust(source_home, target_layout, profile=profile)
    return _materialize_inherited_assets(
        source_home,
        target_layout,
        profile=profile,
        project_root=project_root,
        agent_name=agent_name,
        workspace_path=workspace_path,
    )


def _materialize_inherited_assets(
    source_home: Path,
    target_layout: ClaudeHomeLayout,
    *,
    profile,
    project_root: Path | None,
    agent_name: str | None,
    workspace_path: Path | None,
) -> dict[str, object]:
    _route_inherited_tree(
        source_home / '.claude' / 'commands',
        target_layout.claude_dir / 'commands',
        enabled=_inherits_commands(profile),
        label=_CLAUDE_COMMANDS_PROJECTION_LABEL,
    )
    _route_inherited_tree(
        source_home / '.claude' / 'skills',
        target_layout.claude_dir / 'skills',
        enabled=_inherits_skills(profile),
        label=_CLAUDE_SKILLS_PROJECTION_LABEL,
    )
    project_role_skills_to_home(
        project_root=project_root,
        agent_name=agent_name,
        provider='claude',
        target_skills_dir=target_layout.claude_dir / 'skills',
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
) -> None:
    payload = _projected_settings_payload(source_home / '.claude' / 'settings.json', profile=profile)
    existing = _read_json_object(target_layout.settings_path)
    merged = _merge_settings_payload(payload, existing=existing, profile=profile, auto_permission=auto_permission)
    if merged is None:
        return
    target_layout.settings_path.parent.mkdir(parents=True, exist_ok=True)
    target_layout.settings_path.write_text(
        json.dumps(merged, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )


def _materialize_trust(source_home: Path, target_layout: ClaudeHomeLayout, *, profile) -> None:
    source_trust = source_home / '.claude.json'
    if source_trust.is_file():
        merged = _projected_claude_json_payload(
            _read_json_object(source_trust),
            existing=_read_json_object(target_layout.trust_path),
            profile=profile,
        )
        _write_json_object(target_layout.trust_path, merged)
    _ensure_trust_file(target_layout.trust_path)


def _materialize_auth(source_home: Path, target_layout: ClaudeHomeLayout, *, profile) -> None:
    if not _inherits_auth(profile):
        _remove_file(target_layout.auth_path)
        _remove_file(target_layout.credentials_path)
        return

    for source_auth, target_auth in _source_auth_paths(source_home, target_layout):
        if source_auth.is_file():
            _sync_file(source_auth, target_auth)
    _materialize_macos_keychain_auth(target_layout)


def _materialize_macos_keychain_preferences(source_home: Path, target_layout: ClaudeHomeLayout, *, profile) -> None:
    target = target_layout.home_root / 'Library' / 'Preferences' / 'com.apple.security.plist'
    target_keychains = target_layout.home_root / 'Library' / 'Keychains'
    if not _inherits_auth(profile):
        _remove_file(target)
        _remove_keychains_link(target_keychains)
        return
    if platform.system() != 'Darwin':
        return
    source = source_home / 'Library' / 'Preferences' / 'com.apple.security.plist'
    if source.is_file():
        _sync_file(source, target)
        return
    _remove_file(target)
    _materialize_macos_keychains_link(source_home / 'Library' / 'Keychains', target_keychains)


def _materialize_macos_keychains_link(source: Path, target: Path) -> None:
    if not source.is_dir():
        _remove_keychains_link(target)
        return
    try:
        if target.is_symlink():
            if target.resolve() == source.resolve():
                return
            target.unlink()
        elif target.exists():
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        target.symlink_to(source, target_is_directory=True)
    except Exception:
        pass


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
) -> dict[str, object]:
    merged = dict(existing or {})
    for key in _CLAUDE_JSON_AUTH_SECRET_KEYS:
        merged.pop(key, None)
    if not _inherits_auth(profile):
        for key in _CLAUDE_JSON_AUTH_METADATA_KEYS:
            merged.pop(key, None)
        return merged

    for key in (*_CLAUDE_JSON_AUTH_METADATA_KEYS, *_CLAUDE_JSON_AUTH_COMPANION_KEYS):
        if key in source_payload:
            merged[key] = source_payload[key]
    return merged


def _materialize_macos_keychain_auth(target_layout: ClaudeHomeLayout) -> None:
    payload = _read_macos_keychain_claude_credentials()
    if not payload:
        return
    _write_json_object(target_layout.credentials_path, payload, mode=0o600)


def _read_macos_keychain_claude_credentials() -> dict[str, object] | None:
    if platform.system() != 'Darwin':
        return None
    security = shutil.which('security') or '/usr/bin/security'
    account = _macos_keychain_account()
    if not account:
        return None

    for service in _macos_keychain_services():
        try:
            result = subprocess.run(
                [security, 'find-generic-password', '-a', account, '-s', service, '-w'],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception:
            continue
        if result.returncode != 0:
            continue
        payload = _json_object_from_text(result.stdout)
        if isinstance(payload.get('claudeAiOauth'), dict):
            return payload
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


def _projected_settings_payload(source_settings_path: Path, *, profile) -> dict[str, object] | None:
    source_payload = _read_json_object(source_settings_path)
    if not source_payload:
        return {} if _needs_settings_stub(profile) else None

    env_payload = dict(source_payload.get('env') or {}) if isinstance(source_payload.get('env'), dict) else {}
    if not _inherits_api(profile):
        for key in provider_api_env_keys('claude'):
            env_payload.pop(key, None)
    elif not _inherits_auth(profile):
        env_payload.pop('ANTHROPIC_AUTH_TOKEN', None)
        env_payload.pop('ANTHROPIC_API_KEY', None)

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
) -> dict[str, object] | None:
    existing_payload = dict(existing or {})
    projected_payload = dict(projected or {})
    merged = dict(projected_payload)
    _carry_forward_managed_auth_env(merged, existing_payload, profile=profile)

    for key in _CLAUDE_RUNTIME_SETTINGS_KEYS:
        value = existing_payload.get(key)
        if value is not None:
            if key == 'permissions' and auto_permission and _is_ccb_only_permission_payload(value):
                continue
            merged[key] = value

    if merged:
        return merged
    if projected is not None:
        return {}
    return None


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
) -> None:
    if not _inherits_auth(profile):
        return
    existing_env = _read_env_payload(existing_payload)
    if not existing_env:
        return
    merged_env = _read_env_payload(merged_payload)
    if _has_projected_auth_authority(merged_env):
        return

    preserved_any = False
    for key in _CLAUDE_AUTH_ENV_KEYS:
        value = existing_env.get(key)
        if _env_value_present(value):
            merged_env[key] = value
            preserved_any = True
    if _inherits_api(profile):
        for key in _CLAUDE_API_AUTH_ENV_KEYS:
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


def _needs_settings_stub(profile) -> bool:
    return bool(_inherits_api(profile) or _inherits_auth(profile) or _inherits_config(profile))


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


def _read_json_object(path: Path) -> dict[str, object]:
    try:
        data = _json_object_from_text(path.read_text(encoding='utf-8'))
    except Exception:
        return {}
    return data


def _json_object_from_text(value: str) -> dict[str, object]:
    try:
        data = json.loads(str(value or '').strip())
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _write_json_object(path: Path, payload: dict[str, object], *, mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
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
    if path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('{}\n', encoding='utf-8')


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


def _sync_tree(source: Path, target: Path) -> None:
    if not source.is_dir():
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(source, target, dirs_exist_ok=True)
    except Exception:
        pass


def _route_inherited_tree(source: Path, target: Path, *, enabled: bool, label: str) -> None:
    route_projected_tree(source, target, enabled=enabled, label=label, allow_unmarked_replace=True)


def _system_home_root() -> Path:
    return current_provider_source_home()


__all__ = ['materialize_claude_home_config', 'prepare_claude_home_overrides', 'resolve_claude_home_layout']
