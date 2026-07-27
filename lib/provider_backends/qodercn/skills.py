from __future__ import annotations

from pathlib import Path

from provider_core.inherited_skills import inherits_skills, packaged_inherited_skills_dir
from provider_core.projected_assets import route_projected_tree


QODERCN_CCB_SKILL_NAMES = ('ask', 'ccb-clear')
_QODERCN_SKILL_LABEL_PREFIX = 'qodercn-ccb-skill:'


def materialize_qodercn_skills(target_home: Path, *, profile=None) -> tuple[str, ...]:
    source_root = packaged_inherited_skills_dir('qodercn')
    target_root = Path(target_home).expanduser() / 'skills'
    enabled = inherits_skills(profile)
    active: list[str] = []
    for skill_name in QODERCN_CCB_SKILL_NAMES:
        if route_projected_tree(
            source_root / skill_name,
            target_root / skill_name,
            enabled=enabled,
            label=_skill_label(skill_name),
            allow_unmarked_replace=False,
        ):
            active.append(skill_name)
    return tuple(active)


def _skill_label(skill_name: str) -> str:
    return f'{_QODERCN_SKILL_LABEL_PREFIX}{skill_name}'


__all__ = [
    'QODERCN_CCB_SKILL_NAMES',
    'materialize_qodercn_skills',
]
