from __future__ import annotations

import json
import os
from pathlib import Path

from provider_core.inherited_skills import (
    inherits_skills,
    materialize_required_control_skills,
    required_control_skill_names,
    route_inherited_skill_entries,
)
from provider_core.memory_projection import (
    materialize_provider_memory_file,
    record_memory_projection_event,
)
from provider_core.one_way_inheritance import (
    copy_regular_file,
    ensure_private_descendant_directory,
    ensure_private_inheritance_directory,
)
from provider_core.source_home import current_provider_source_home
from rolepacks.projection import project_role_skills_to_home


_AUTH_FILES = (Path('.credentials.yaml'), Path('.env'))
_CONFIG_FILES = (Path('settings.yaml'),)


def source_dsh_home(source_home: Path | None = None) -> Path:
    if source_home is not None:
        root = Path(source_home).expanduser()
    else:
        explicit = str(os.environ.get('DSH_HOME') or '').strip()
        root = Path(explicit).expanduser() if explicit else current_provider_source_home() / '.dsh'
    return root


def materialize_dsh_home(
    target_home: Path,
    *,
    profile=None,
    source_home: Path | None = None,
    project_root: Path | None = None,
    workspace_path: Path | None = None,
    agent_name: str | None = None,
    runtime_dir: Path | None = None,
    event_path: Path | None = None,
) -> Path:
    """Materialize the allowlisted DSH account/config/context projection.

    DSH's native sessions and caches intentionally stay outside this copy
    list.  Every mounted agent owns its own target ``DSH_HOME``.
    """

    source = source_dsh_home(source_home)
    target = ensure_private_inheritance_directory(Path(target_home).expanduser(), source)
    if _inherits_auth(profile):
        _copy_files(source, target, _AUTH_FILES)
    if _inherits_config(profile):
        _copy_files(source, target, _CONFIG_FILES)

    target_skills = target / 'skills'
    route_inherited_skill_entries(
        source / 'skills',
        target_skills,
        enabled=inherits_skills(profile),
        label='dsh-inherited-skills',
        exclude=required_control_skill_names('dsh'),
    )
    project_role_skills_to_home(
        project_root=project_root,
        agent_name=agent_name,
        provider='dsh',
        target_skills_dir=target_skills,
    )
    materialize_required_control_skills(provider='dsh', target_dir=target_skills)

    if (
        _inherits_memory(profile)
        and project_root is not None
        and agent_name
    ):
        result = materialize_provider_memory_file(
            project_root=Path(project_root),
            agent_name=agent_name,
            provider='dsh',
            target=target / 'AGENTS.md',
            provider_memory_path=source / 'AGENTS.md',
            provider_memory_title='DSH User Instructions',
            workspace_path=workspace_path,
        )
        record_memory_projection_event(
            result,
            provider='dsh',
            event_path=event_path,
            marker_path=(Path(runtime_dir) / 'dsh-memory-projection.json') if runtime_dir else None,
            agent_name=agent_name,
        )
    elif runtime_dir is not None:
        _remove_owned_memory_projection(
            target / 'AGENTS.md',
            Path(runtime_dir) / 'dsh-memory-projection.json',
        )
    return target


def _copy_files(source: Path, target: Path, names: tuple[Path, ...]) -> None:
    for relative in names:
        ensure_private_descendant_directory(target, relative.parent)
        copy_regular_file(source / relative, target / relative)


def _remove_owned_memory_projection(target: Path, marker: Path) -> None:
    try:
        payload = json.loads(marker.read_text(encoding='utf-8'))
    except Exception:
        return
    if not isinstance(payload, dict):
        return
    digest = str(payload.get('sha256') or '').strip()
    if not digest:
        return
    try:
        from project_memory.hashing import sha256_text

        if target.is_file() and sha256_text(target.read_text(encoding='utf-8')) == digest:
            target.unlink()
        marker.unlink(missing_ok=True)
    except OSError:
        return


def _inherits_auth(profile) -> bool:
    return True if profile is None else bool(getattr(profile, 'inherit_auth', True))


def _inherits_config(profile) -> bool:
    return True if profile is None else bool(getattr(profile, 'inherit_config', True))


def _inherits_memory(profile) -> bool:
    return True if profile is None else bool(getattr(profile, 'inherit_memory', True))


__all__ = ['materialize_dsh_home', 'source_dsh_home']
