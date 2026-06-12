from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import time

from provider_backends.codex.session_authority import (
    current_memory_projection_fingerprint,
    current_provider_authority_fingerprint,
    stored_provider_authority_fingerprint,
    stored_session_authority_fingerprint,
)
from provider_backends.codex.start_cmd import strip_resume_start_cmd
from provider_sessions.files import safe_write_session
from provider_profiles.codex_home_config import materialize_codex_home_config, repair_codex_activity_hooks

from ..session_paths import read_session_payload, session_file_for_runtime_dir, state_dir_for_runtime_dir


_ENV_ASSIGNMENT_RE = re.compile(
    r"(?:(?:^|[;\s])export\s+|(?:^|[;\s]))(?P<name>[A-Z0-9_]+)=(?P<value>'[^']*'|\"[^\"]*\"|[^;\s]+)"
)
_SESSION_NAMESPACE_MARKER = '.ccb-session-namespace.json'


@dataclass(frozen=True)
class CodexHomeLayout:
    codex_home: Path
    session_root: Path


def resolve_codex_home_layout(runtime_dir: Path, profile) -> CodexHomeLayout:
    explicit_runtime_home = _profile_runtime_home(profile)
    if explicit_runtime_home is not None:
        return CodexHomeLayout(
            codex_home=explicit_runtime_home,
            session_root=explicit_runtime_home / 'sessions',
        )

    existing = _existing_layout(runtime_dir)
    if existing is not None:
        return existing

    isolated_home = _managed_isolated_home(runtime_dir)
    return CodexHomeLayout(
        codex_home=isolated_home,
        session_root=isolated_home / 'sessions',
    )


def prepare_codex_home_overrides(
    runtime_dir: Path,
    profile,
    *,
    refresh_home: bool = False,
    project_root: Path | None = None,
    agent_name: str | None = None,
    workspace_path: Path | None = None,
    memory_projection_event_path: Path | None = None,
    memory_projection_marker_path: Path | None = None,
) -> dict[str, str]:
    layout = resolve_codex_home_layout(runtime_dir, profile)
    layout.codex_home.mkdir(parents=True, exist_ok=True)
    layout.session_root.mkdir(parents=True, exist_ok=True)
    marker_ready = _session_namespace_marker_exists(layout.codex_home)
    if refresh_home:
        _prepare_managed_home(
            _system_codex_home(),
            layout.codex_home,
            profile=profile,
            runtime_dir=runtime_dir,
            project_root=project_root,
            agent_name=agent_name,
            workspace_path=workspace_path,
            memory_projection_event_path=memory_projection_event_path,
            memory_projection_marker_path=memory_projection_marker_path,
        )
        _ensure_session_namespace_authority(runtime_dir, layout.codex_home, layout.session_root, profile=profile)
    elif not marker_ready and not any(layout.session_root.iterdir()):
        _write_session_namespace_marker(layout.codex_home / _SESSION_NAMESPACE_MARKER, current_provider_authority_fingerprint(profile))
    if not refresh_home:
        repair_codex_activity_hooks(
            layout.codex_home,
            project_root=project_root,
            agent_name=agent_name,
            runtime_dir=runtime_dir,
            workspace_path=workspace_path,
        )

    overrides = {
        'CODEX_HOME': str(layout.codex_home),
        'CODEX_SESSION_ROOT': str(layout.session_root),
    }

    if "WSL_DISTRO_NAME" in os.environ:
        # We are running inside WSL. The target executable might be a Windows binary (via interop).
        # Set USERPROFILE to the same isolated path and instruct WSLENV to automatically translate paths.
        overrides['USERPROFILE'] = str(layout.codex_home)
        wslenv_additions = "CODEX_HOME/p:CODEX_SESSION_ROOT/p:USERPROFILE/p"
        existing_wslenv = os.environ.get("WSLENV", "")
        if existing_wslenv:
            overrides['WSLENV'] = f"{wslenv_additions}:{existing_wslenv}"
        else:
            overrides['WSLENV'] = wslenv_additions

    return overrides


def _profile_runtime_home(profile) -> Path | None:
    runtime_home = getattr(profile, 'runtime_home', None) if profile is not None else None
    if not runtime_home:
        return None
    return Path(runtime_home).expanduser()


def _existing_layout(runtime_dir: Path) -> CodexHomeLayout | None:
    session_file = session_file_for_runtime_dir(runtime_dir)
    if session_file is None or not session_file.is_file():
        return None
    data = read_session_payload(session_file)
    if not isinstance(data, dict):
        return None
    return _layout_from_payload(data)


def _layout_from_payload(data: dict[str, object]) -> CodexHomeLayout | None:
    codex_home = _path_or_none(data.get('codex_home'))
    session_root = _path_or_none(data.get('codex_session_root'))
    if session_root is None:
        session_root = _session_root_from_commands(data)
    if session_root is None and codex_home is not None:
        session_root = codex_home / 'sessions'
    if session_root is None:
        session_root = _session_root_from_log_path(data.get('codex_session_path'))
    if session_root is None:
        return None
    if codex_home is None:
        codex_home = _codex_home_from_commands(data)
    if codex_home is None:
        codex_home = _legacy_root_to_home(session_root)
    _migrate_legacy_session_root(session_root, codex_home / 'sessions')
    return CodexHomeLayout(codex_home=codex_home, session_root=codex_home / 'sessions')


def _session_root_from_commands(data: dict[str, object]) -> Path | None:
    commands = (
        str(data.get('codex_start_cmd') or '').strip(),
        str(data.get('start_cmd') or '').strip(),
    )
    for command in commands:
        session_root = _extract_command_path(command, 'CODEX_SESSION_ROOT')
        if session_root is not None:
            return session_root
        codex_home = _extract_command_path(command, 'CODEX_HOME')
        if codex_home is not None:
            return codex_home / 'sessions'
    return None


def _codex_home_from_commands(data: dict[str, object]) -> Path | None:
    commands = (
        str(data.get('codex_start_cmd') or '').strip(),
        str(data.get('start_cmd') or '').strip(),
    )
    for command in commands:
        codex_home = _extract_command_path(command, 'CODEX_HOME')
        if codex_home is not None:
            return codex_home
    return None


def _extract_command_path(command: str, env_name: str) -> Path | None:
    if not command:
        return None
    for match in _ENV_ASSIGNMENT_RE.finditer(command):
        if match.group('name') != env_name:
            continue
        return _path_or_none(_unquote_env_value(match.group('value')))
    return None


def _unquote_env_value(value: str) -> str:
    text = str(value or '').strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in ("'", '"'):
        return text[1:-1]
    return text


def _session_root_from_log_path(value: object) -> Path | None:
    log_path = _path_or_none(value)
    if log_path is None:
        return None
    for parent in (log_path.parent, *log_path.parents):
        if parent.name == 'sessions':
            return parent
    return None


def _path_or_none(value: object) -> Path | None:
    raw = str(value or '').strip()
    if not raw:
        return None
    try:
        return Path(raw).expanduser()
    except Exception:
        return None


def _managed_state_dir(runtime_dir: Path) -> Path:
    derived = state_dir_for_runtime_dir(runtime_dir)
    if derived is not None:
        return derived
    return Path(runtime_dir).expanduser() / 'codex-state'


def _managed_isolated_home(runtime_dir: Path) -> Path:
    return _managed_state_dir(runtime_dir) / 'home'
def _legacy_root_to_home(session_root: Path) -> Path:
    normalized_root = Path(session_root).expanduser()
    if normalized_root.name == 'sessions':
        parent = normalized_root.parent
        if parent.name == 'home':
            return parent
        return parent / 'home'
    return normalized_root / 'home'


def _migrate_legacy_session_root(source_root: Path, target_root: Path) -> None:
    normalized_source = Path(source_root).expanduser()
    normalized_target = Path(target_root).expanduser()
    if normalized_source == normalized_target:
        normalized_target.mkdir(parents=True, exist_ok=True)
        return
    if normalized_source.name != 'sessions':
        normalized_target.mkdir(parents=True, exist_ok=True)
        return
    if normalized_target.exists():
        return
    normalized_target.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.move(str(normalized_source), str(normalized_target))
    except Exception:
        normalized_target.mkdir(parents=True, exist_ok=True)


def _system_codex_home() -> Path:
    return Path(os.environ.get('CODEX_HOME') or (Path.home() / '.codex')).expanduser()


def _prepare_managed_home(
    source_home: Path,
    target_home: Path,
    *,
    profile,
    runtime_dir: Path,
    project_root: Path | None,
    agent_name: str | None,
    workspace_path: Path | None,
    memory_projection_event_path: Path | None,
    memory_projection_marker_path: Path | None,
) -> None:
    materialize_codex_home_config(
        target_home,
        profile=profile,
        source_home=source_home,
        project_root=project_root,
        agent_name=agent_name,
        runtime_dir=runtime_dir,
        workspace_path=workspace_path,
        memory_projection_event_path=memory_projection_event_path,
        memory_projection_marker_path=memory_projection_marker_path,
    )


def _ensure_session_namespace_authority(runtime_dir: Path, codex_home: Path, session_root: Path, *, profile) -> None:
    current_fingerprint = current_provider_authority_fingerprint(profile)
    memory_fingerprint = current_memory_projection_fingerprint(runtime_dir)
    marker_path = codex_home / _SESSION_NAMESPACE_MARKER
    stored_marker = _read_session_namespace_marker(marker_path)
    session_file = session_file_for_runtime_dir(runtime_dir)
    session_data = read_session_payload(session_file) if session_file is not None and session_file.is_file() else {}
    if _session_namespace_requires_reset(
        stored_marker=stored_marker,
        current_fingerprint=current_fingerprint,
        current_memory_fingerprint=memory_fingerprint,
        session_data=session_data,
    ):
        _archive_session_root(codex_home, session_root, label=_marker_label(stored_marker) or stored_provider_authority_fingerprint(session_data))
        _scrub_project_session_binding(session_file)
    _write_session_namespace_marker(marker_path, current_fingerprint, memory_fingerprint=memory_fingerprint)


def _session_namespace_marker_exists(codex_home: Path) -> bool:
    return (Path(codex_home) / _SESSION_NAMESPACE_MARKER).is_file()


def _read_session_namespace_marker(marker_path: Path) -> dict[str, str] | None:
    try:
        data = json.loads(marker_path.read_text(encoding='utf-8'))
    except Exception:
        return None
    if not isinstance(data, dict):
        return None
    return {
        'provider_authority_fingerprint': str(data.get('provider_authority_fingerprint') or '').strip(),
        'memory_projection_sha256': str(data.get('memory_projection_sha256') or '').strip(),
    }


def _session_namespace_requires_reset(
    *,
    stored_marker: dict[str, str] | None,
    current_fingerprint: str,
    current_memory_fingerprint: str,
    session_data: dict[str, object],
) -> bool:
    del current_memory_fingerprint
    stored_session_fingerprint = stored_provider_authority_fingerprint(session_data)
    stored_binding_fingerprint = stored_session_authority_fingerprint(session_data)
    if stored_marker is not None:
        return str(stored_marker.get('provider_authority_fingerprint') or '').strip() != current_fingerprint
    if current_fingerprint:
        return True
    return bool(stored_session_fingerprint or stored_binding_fingerprint)


def _marker_label(stored_marker: dict[str, str] | None) -> str:
    if not stored_marker:
        return ''
    return (
        str(stored_marker.get('provider_authority_fingerprint') or '').strip()
        or str(stored_marker.get('memory_projection_sha256') or '').strip()
    )


def _archive_session_root(codex_home: Path, session_root: Path, *, label: str) -> None:
    normalized_root = Path(session_root).expanduser()
    if not normalized_root.exists():
        normalized_root.mkdir(parents=True, exist_ok=True)
        return
    try:
        has_entries = next(normalized_root.iterdir(), None) is not None
    except Exception:
        has_entries = False
    if not has_entries:
        normalized_root.mkdir(parents=True, exist_ok=True)
        return
    archive_parent = codex_home / 'archived-sessions'
    archive_parent.mkdir(parents=True, exist_ok=True)
    archive_name = f"{time.strftime('%Y%m%d-%H%M%S')}-{_archive_label(label)}"
    archive_path = archive_parent / archive_name
    try:
        shutil.move(str(normalized_root), str(archive_path))
    except Exception:
        pass
    normalized_root.mkdir(parents=True, exist_ok=True)


def _archive_label(label: str) -> str:
    text = str(label or '').strip().lower()
    if not text:
        return 'global'
    return re.sub(r'[^a-z0-9._-]+', '-', text)[:32] or 'global'


def _scrub_project_session_binding(session_file: Path | None) -> None:
    if session_file is None or not session_file.is_file():
        return
    data = read_session_payload(session_file)
    if not isinstance(data, dict):
        return
    old_id = str(data.get('codex_session_id') or '').strip()
    old_path = str(data.get('codex_session_path') or '').strip()
    changed = False
    if old_id and data.get('old_codex_session_id') != old_id:
        data['old_codex_session_id'] = old_id
        changed = True
    if old_path and data.get('old_codex_session_path') != old_path:
        data['old_codex_session_path'] = old_path
        changed = True
    if old_id or old_path:
        data['old_updated_at'] = time.strftime('%Y-%m-%d %H:%M:%S')
        changed = True
    for key in ('codex_session_id', 'codex_session_path', 'codex_session_authority_fingerprint'):
        if key in data:
            data.pop(key, None)
            changed = True
    for key in ('start_cmd', 'codex_start_cmd'):
        stripped = strip_resume_start_cmd(data.get(key))
        current = str(data.get(key) or '').strip()
        if stripped and stripped != current:
            data[key] = stripped
            changed = True
    if not changed:
        return
    ok, error = safe_write_session(session_file, json.dumps(data, ensure_ascii=False, indent=2))
    if not ok:
        raise RuntimeError(error or f'failed to rewrite session file: {session_file}')


def _write_session_namespace_marker(marker_path: Path, fingerprint: str, *, memory_fingerprint: str = '') -> None:
    payload = {
        'provider': 'codex',
        'provider_authority_fingerprint': str(fingerprint or '').strip(),
        'memory_projection_sha256': str(memory_fingerprint or '').strip(),
        'updated_at': time.strftime('%Y-%m-%d %H:%M:%S'),
        'version': 1,
    }
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    marker_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')


__all__ = ['CodexHomeLayout', 'prepare_codex_home_overrides', 'resolve_codex_home_layout']
