from __future__ import annotations

import fnmatch
import json
from pathlib import Path
import shutil

from provider_core.projected_assets import (
    projected_path_is_owned,
    remove_projected_path,
    route_projected_tree,
)


_REQUIRED_CONTROL_SKILLS = {
    'claude': ('ask', 'ccb-clear', 'ccb-compact', 'ccb-diagnose'),
    'codex': ('ask', 'ccb-clear', 'ccb-compact', 'ccb-diagnose', 'reconnect'),
    'droid': ('ask', 'ccb-clear', 'ccb-compact', 'ccb-diagnose'),
    'dsh': ('ask', 'ccb-clear', 'ccb-compact', 'ccb-diagnose'),
    'gemini': ('ask', 'ccb-clear', 'ccb-compact', 'ccb-diagnose'),
    'grok': ('ask', 'ccb-clear', 'ccb-compact', 'ccb-diagnose'),
    'kimi': ('ask', 'ccb-clear', 'ccb-compact', 'ccb-diagnose'),
    'qoder': ('ask', 'ccb-clear', 'ccb-compact', 'ccb-diagnose'),
    'qoderclicn': ('ask', 'ccb-clear', 'ccb-compact', 'ccb-diagnose'),
}
_PACKAGED_SKILL_PROVIDER_ALIASES = {
    # Both released Qoder provider keys consume the same CCB control contract.
    # Keep qoderclicn stable rather than adopting PR #280's incompatible rename.
    'qoderclicn': 'qoder',
}
_REQUIRED_SKILL_LABEL_PREFIX = 'ccb-required-skill:'


def packaged_inherited_skills_dir(provider: str) -> Path:
    normalized = str(provider or '').strip().lower()
    packaged = _PACKAGED_SKILL_PROVIDER_ALIASES.get(normalized, normalized)
    return _repo_root() / 'inherit_skills' / f'{packaged}_skills'


def packaged_inherited_skill_file(provider: str, relative_path: str) -> Path:
    return packaged_inherited_skills_dir(provider) / relative_path


def route_packaged_inherited_skills_dir(
    *,
    provider: str,
    target_dir: Path,
    enabled: bool,
    label: str,
) -> bool:
    return route_projected_tree(
        packaged_inherited_skills_dir(provider),
        Path(target_dir),
        enabled=enabled,
        label=label,
    )


def inherits_skills(profile) -> bool:
    return True if profile is None else bool(getattr(profile, 'inherit_skills', True))


def required_control_skill_names(provider: str) -> tuple[str, ...]:
    normalized = str(provider or '').strip().lower()
    return _REQUIRED_CONTROL_SKILLS.get(normalized, ())


def materialize_required_control_skills(
    *,
    provider: str,
    target_dir: Path,
) -> tuple[str, ...]:
    """Project CCB control skills independently from optional user inheritance."""
    normalized = str(provider or '').strip().lower()
    required = required_control_skill_names(normalized)
    if not required:
        return ()
    source_root = packaged_inherited_skills_dir(normalized)
    target_root = Path(target_dir).expanduser()
    _detach_skill_root_symlink(target_root, reserved_names=frozenset(required))
    target_root.mkdir(parents=True, exist_ok=True)

    active: list[str] = []
    for skill_name in required:
        source = source_root / skill_name
        if not (source / 'SKILL.md').is_file():
            raise RuntimeError(
                f'packaged required CCB skill is missing: {normalized}/{skill_name}'
            )
        target = target_root / skill_name
        label = _required_skill_label(normalized, skill_name)
        if not _required_projection_matches(target, source=source, label=label):
            _remove_exact_skill_entry(target)
            Path(f'{target}.ccb-projection.json').unlink(missing_ok=True)
        projected = route_projected_tree(
            source,
            target,
            enabled=True,
            label=label,
            allow_unmarked_replace=False,
        )
        if not projected or not (target / 'SKILL.md').is_file():
            raise RuntimeError(
                f'failed to project required CCB skill: {normalized}/{skill_name}'
            )
        active.append(skill_name)
    return tuple(active)


def required_control_skills_ready(*, provider: str, target_dir: Path) -> bool:
    normalized = str(provider or '').strip().lower()
    required = required_control_skill_names(normalized)
    source_root = packaged_inherited_skills_dir(normalized)
    target_root = Path(target_dir).expanduser()
    return bool(required) and all(
        _required_projection_matches(
            target_root / skill_name,
            source=source_root / skill_name,
            label=_required_skill_label(normalized, skill_name),
        )
        for skill_name in required
    )


def route_inherited_skill_entries(
    source_dir: Path,
    target_dir: Path,
    *,
    enabled: bool,
    label: str,
    exclude: tuple[str, ...] = (),
    include_patterns: tuple[str, ...] = (),
    exclude_patterns: tuple[str, ...] = (),
    special_entries: tuple[str, ...] = (),
) -> tuple[str, ...]:
    """Route optional skills per entry so one broken source cannot drop all skills."""
    source_root = Path(source_dir).expanduser()
    target_root = Path(target_dir).expanduser()
    excluded = frozenset(str(name or '').strip() for name in exclude)
    special = frozenset(str(name or '').strip() for name in special_entries)

    # Migrate the old whole-tree projection before creating entry projections.
    accepts_projection = _skill_root_accepts_entry_projection(target_root, label=label)
    remove_projected_path(target_root, label=label)
    if not accepts_projection:
        return ()
    _detach_skill_root_symlink(target_root, reserved_names=excluded)

    desired_labels: set[str] = set()
    active: list[str] = []
    if enabled and source_root.is_dir():
        target_root.mkdir(parents=True, exist_ok=True)
        for source in sorted(source_root.iterdir(), key=lambda item: item.name):
            skill_name = source.name
            if (
                (skill_name.startswith('.') and skill_name not in special)
                or skill_name in excluded
                or not source.is_dir()
                or (skill_name not in special and not (source / 'SKILL.md').is_file())
                or (
                    include_patterns
                    and not any(fnmatch.fnmatchcase(skill_name, pattern) for pattern in include_patterns)
                )
                or any(fnmatch.fnmatchcase(skill_name, pattern) for pattern in exclude_patterns)
            ):
                continue
            entry_label = f'{label}:{skill_name}'
            desired_labels.add(entry_label)
            if route_projected_tree(
                source,
                target_root / skill_name,
                enabled=True,
                label=entry_label,
                allow_unmarked_replace=False,
            ):
                active.append(skill_name)
    _remove_stale_entry_projections(
        target_root,
        label_prefix=f'{label}:',
        desired_labels=desired_labels,
    )
    _remove_empty_directory(target_root)
    return tuple(active)


def _required_skill_label(provider: str, skill_name: str) -> str:
    return f'{_REQUIRED_SKILL_LABEL_PREFIX}{provider}:{skill_name}'


def _required_projection_matches(target: Path, *, source: Path, label: str) -> bool:
    if not (target / 'SKILL.md').is_file():
        return False
    marker = Path(f'{target}.ccb-projection.json')
    try:
        payload = json.loads(marker.read_text(encoding='utf-8'))
    except Exception:
        return False
    if not isinstance(payload, dict):
        return False
    if payload.get('record_type') != 'ccb_projected_asset':
        return False
    if str(payload.get('label') or '') != label:
        return False
    try:
        return Path(str(payload.get('source') or '')).expanduser().resolve() == source.resolve()
    except Exception:
        return str(payload.get('source') or '') == str(source)


def _detach_skill_root_symlink(target_root: Path, *, reserved_names: frozenset[str]) -> None:
    if not target_root.is_symlink():
        return
    try:
        source_root = target_root.resolve()
        entries = tuple(source_root.iterdir()) if source_root.is_dir() else ()
    except Exception:
        entries = ()
    target_root.unlink(missing_ok=True)
    _remove_ccb_projection_marker(Path(f'{target_root}.ccb-projection.json'))
    target_root.mkdir(parents=True, exist_ok=True)
    for source in entries:
        if source.name in reserved_names:
            continue
        target = target_root / source.name
        try:
            target.symlink_to(source, target_is_directory=source.is_dir())
        except OSError:
            try:
                if source.is_dir():
                    shutil.copytree(source, target, symlinks=True)
                elif source.is_file():
                    shutil.copy2(source, target)
            except OSError:
                continue


def _remove_stale_entry_projections(
    target_root: Path,
    *,
    label_prefix: str,
    desired_labels: set[str],
) -> None:
    if not target_root.is_dir() or target_root.is_symlink():
        return
    for marker in sorted(target_root.glob('*.ccb-projection.json')):
        try:
            payload = json.loads(marker.read_text(encoding='utf-8'))
        except Exception:
            continue
        if not isinstance(payload, dict) or payload.get('record_type') != 'ccb_projected_asset':
            continue
        entry_label = str(payload.get('label') or '')
        if not entry_label.startswith(label_prefix) or entry_label in desired_labels:
            continue
        skill_name = marker.name.removesuffix('.ccb-projection.json')
        remove_projected_path(
            target_root / skill_name,
            label=entry_label,
            marker_path=marker,
        )


def _remove_ccb_projection_marker(marker: Path) -> None:
    try:
        payload = json.loads(marker.read_text(encoding='utf-8'))
    except Exception:
        return
    if isinstance(payload, dict) and payload.get('record_type') == 'ccb_projected_asset':
        marker.unlink(missing_ok=True)


def _skill_root_accepts_entry_projection(target_root: Path, *, label: str) -> bool:
    if not target_root.exists() and not target_root.is_symlink():
        return True
    if projected_path_is_owned(target_root, label=label):
        return True
    marker = Path(f'{target_root}.ccb-projection.json')
    if marker.exists() or marker.is_symlink():
        return False
    # A local skills directory may contain provider-created or user-created
    # entries.  Per-entry routing is safe there because unmarked conflicts are
    # never replaced and stale cleanup only removes CCB-owned markers.
    return target_root.is_dir()


def _remove_exact_skill_entry(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        shutil.rmtree(path)


def _remove_empty_directory(path: Path) -> None:
    try:
        path.rmdir()
    except OSError:
        pass


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


__all__ = [
    'inherits_skills',
    'materialize_required_control_skills',
    'packaged_inherited_skill_file',
    'packaged_inherited_skills_dir',
    'required_control_skill_names',
    'required_control_skills_ready',
    'route_inherited_skill_entries',
    'route_packaged_inherited_skills_dir',
]
