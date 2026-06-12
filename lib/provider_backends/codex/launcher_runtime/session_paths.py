from __future__ import annotations

import json
from pathlib import Path

from provider_core.pathing import session_filename_for_agent
from provider_backends.codex.session_authority import resume_authority_matches
from storage.path_helpers import runtime_project_anchor_from_path

from ..start_cmd import extract_resume_session_id


def load_resume_session_id(
    spec,
    runtime_dir: Path,
    profile=None,
    *,
    current_fingerprint: str | None = None,
    current_memory_fingerprint: str | None = None,
) -> str | None:
    session_path = preferred_session_path(spec, runtime_dir)
    if session_path is None:
        return None
    data = read_session_payload(session_path)
    if data is None:
        return None
    if not _provider_authority_matches(
        data,
        profile=profile,
        current_fingerprint=current_fingerprint,
        current_memory_fingerprint=current_memory_fingerprint,
    ):
        return None
    if not _resume_session_binding_is_usable(data):
        return None
    return payload_resume_session_id(data)


def agent_session_path(spec, runtime_dir: Path) -> Path | None:
    ccb_dir = find_project_ccb_dir(runtime_dir)
    if ccb_dir is None:
        return None
    return ccb_dir / session_filename_for_agent('codex', spec.name)


def find_project_ccb_dir(runtime_dir: Path) -> Path | None:
    current = Path(runtime_dir)
    for parent in (current, *current.parents):
        if parent.name == '.ccb':
            return parent
    return runtime_project_anchor_from_path(current)


def session_file_for_runtime_dir(runtime_dir: Path) -> Path | None:
    ccb_dir = find_project_ccb_dir(runtime_dir)
    if ccb_dir is None:
        return None
    try:
        agent_name = runtime_dir.parents[1].name
    except Exception:
        return None
    agent_name = str(agent_name or '').strip()
    if not agent_name:
        return None
    return ccb_dir / session_filename_for_agent('codex', agent_name)


def state_dir_for_runtime_dir(runtime_dir: Path) -> Path | None:
    current = Path(runtime_dir)
    normalized_provider = str(current.name or '').strip().lower()
    if not normalized_provider:
        return None
    parent = current.parent
    if parent.name != 'provider-runtime':
        return None
    agent_dir = parent.parent
    if not agent_dir.name:
        return None
    return agent_dir / 'provider-state' / normalized_provider


def preferred_session_path(spec, runtime_dir: Path) -> Path | None:
    candidates = (agent_session_path(spec, runtime_dir),)
    for session_path in candidates:
        if session_path is not None and session_path.is_file():
            return session_path
    return None


def read_session_payload(session_path: Path) -> dict | None:
    try:
        data = json.loads(session_path.read_text(encoding='utf-8'))
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def payload_resume_session_id(data: dict) -> str | None:
    session_id = str(data.get('codex_session_id') or '').strip()
    if session_id:
        return session_id
    start_cmd = str(data.get('codex_start_cmd') or data.get('start_cmd') or '').strip()
    if not start_cmd:
        return None
    return extract_resume_session_id(start_cmd)


def _provider_authority_matches(
    data: dict,
    *,
    profile,
    current_fingerprint: str | None,
    current_memory_fingerprint: str | None,
) -> bool:
    return resume_authority_matches(
        data,
        profile=profile,
        current_fingerprint=current_fingerprint,
        current_memory_fingerprint=current_memory_fingerprint,
    )


def _resume_session_binding_is_usable(data: dict) -> bool:
    session_path = _path_or_none(data.get('codex_session_path'))
    if session_path is None:
        return True
    if not session_path.is_file():
        return False
    session_root = _path_or_none(data.get('codex_session_root'))
    if session_root is not None and not _is_within(session_path, session_root):
        return False
    return True


def _path_or_none(value: object) -> Path | None:
    raw = str(value or '').strip()
    if not raw:
        return None
    if raw.startswith('\\\\wsl.localhost\\') or raw.startswith('\\\\wsl$\\'):
        parts = raw.split('\\')
        if len(parts) >= 4:
            raw = '/' + '/'.join(parts[4:])
    try:
        return Path(raw).expanduser()
    except Exception:
        return None


def _is_within(path: Path, root: Path) -> bool:
    try:
        Path(path).expanduser().resolve().relative_to(Path(root).expanduser().resolve())
        return True
    except Exception:
        return False


__all__ = ['load_resume_session_id', 'session_file_for_runtime_dir', 'state_dir_for_runtime_dir']
