from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
from typing import Any

CCB_DIRNAME = '.ccb'
WORKSPACE_BINDING_FILENAME = '.ccb-workspace.json'
CCB_PROJECT_DIR_ENV = 'CCB_PROJECT_DIR'


class ProjectDiscoveryError(ValueError):
    pass


def project_ccb_dir(project_root: Path) -> Path:
    return Path(project_root).expanduser().resolve() / CCB_DIRNAME


def global_ccb_dir() -> Path:
    return Path.home() / CCB_DIRNAME


def find_current_project_anchor(start_dir: Path) -> Path | None:
    env_anchor = _env_project_anchor()
    if env_anchor is not None:
        return env_anchor
    current = _resolved_dir(start_dir)
    if _project_anchor_dir(current) is None:
        return None
    return current


def find_nearest_project_anchor(start_dir: Path) -> Path | None:
    env_anchor = _env_project_anchor()
    if env_anchor is not None:
        return env_anchor
    current = _resolved_dir(start_dir)
    for root in _search_roots(current):
        if _project_anchor_dir(root) is None:
            continue
        is_dangerous, _reason = is_dangerous_project_root(root)
        if root != current and is_dangerous:
            continue
        return root
    return None


def _env_project_anchor() -> Path | None:
    raw = os.environ.get(CCB_PROJECT_DIR_ENV)
    if not raw:
        return None
    try:
        candidate = _resolved_dir(Path(raw))
    except Exception:
        return None
    if _project_anchor_dir(candidate) is None:
        return None
    return candidate


def find_parent_project_anchor_dir(start_dir: Path) -> Path | None:
    current = _resolved_dir(start_dir)
    for root in current.parents:
        candidate = _project_anchor_dir(root)
        if candidate is None:
            continue
        is_dangerous, _reason = is_dangerous_project_root(root)
        if is_dangerous:
            continue
        return candidate
    return None


def is_dangerous_project_root(start_dir: Path) -> tuple[bool, str]:
    current = _resolved_dir(start_dir)
    home = _resolved_home_dir()
    if home is not None and current == home:
        return True, '$HOME'

    temp_root = _resolved_temp_dir()
    if temp_root is not None and current == temp_root:
        return True, 'temporary directory root'

    anchor = _filesystem_anchor(current)
    if anchor is not None and current == anchor:
        return True, 'filesystem root'
    return False, ''


def _project_anchor_dir(root: Path) -> Path | None:
    primary = root / CCB_DIRNAME
    return primary if primary.is_dir() else None


def find_workspace_binding(start_dir: Path) -> Path | None:
    current = _resolved_dir(start_dir)
    for root in _search_roots(current):
        candidate = root / WORKSPACE_BINDING_FILENAME
        if candidate.is_file():
            return candidate
    return None


def load_workspace_binding(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
    except Exception as exc:
        raise ProjectDiscoveryError(f'cannot read workspace binding {path}: {exc}') from exc
    if not isinstance(data, dict):
        raise ProjectDiscoveryError(f'workspace binding {path} must contain an object')
    target_project = data.get('target_project')
    if not isinstance(target_project, str) or not target_project.strip():
        raise ProjectDiscoveryError(f'workspace binding {path} is missing target_project')
    return data


def _resolved_dir(path: Path) -> Path:
    current = Path(path).expanduser()
    try:
        return current.resolve()
    except Exception:
        return current.absolute()


def _search_roots(current: Path):
    return (current, *current.parents)


def _resolved_home_dir() -> Path | None:
    try:
        return Path.home().resolve()
    except Exception:
        try:
            return Path.home().absolute()
        except Exception:
            return None


def _filesystem_anchor(current: Path) -> Path | None:
    try:
        return Path(current.anchor) if current.anchor else None
    except Exception:
        return None


def _resolved_temp_dir() -> Path | None:
    try:
        return Path(tempfile.gettempdir()).expanduser().resolve()
    except Exception:
        try:
            return Path(tempfile.gettempdir()).expanduser().absolute()
        except Exception:
            return None
