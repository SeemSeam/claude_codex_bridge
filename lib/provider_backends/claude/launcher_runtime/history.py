from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


@dataclass
class ClaudeHistoryLocator:
    invocation_dir: Path
    project_root: Path
    env: Mapping[str, str]
    home_dir: Path

    def project_binding(self, work_dir: Path) -> tuple[Path, Path]:
        return project_binding_for_work_dir(work_dir=work_dir, env=self.env, home_dir=self.home_dir)

    def latest_session_id(self) -> tuple[str | None, bool, Path | None]:
        return latest_session_id_for_candidates(
            candidates=history_candidates(self.invocation_dir, self.project_root),
            home_dir=self.home_dir,
            project_binding_fn=self.project_binding,
        )


def history_candidates(invocation_dir: Path, project_root: Path) -> list[Path]:
    candidates: list[Path] = []
    for candidate in (invocation_dir, project_root):
        path = resolve_fallback_path(candidate)
        if path not in candidates:
            candidates.append(path)
    return candidates


def project_binding_for_work_dir(*, work_dir: Path, env: Mapping[str, str], home_dir: Path) -> tuple[Path, Path]:
    projects_root = home_dir / ".claude" / "projects"
    for candidate in project_dir_candidates(work_dir, env):
        normalized_candidate = resolve_fallback_path(candidate)
        project_dir = projects_root / project_key(candidate)
        if project_dir.exists():
            return project_dir, normalized_candidate
    fallback = resolve_fallback_path(work_dir)
    return projects_root / project_key(fallback), fallback


def project_dir_for_work_dir(*, work_dir: Path, env: Mapping[str, str], home_dir: Path) -> Path:
    project_dir, _matched_cwd = project_binding_for_work_dir(work_dir=work_dir, env=env, home_dir=home_dir)
    return project_dir


def project_dir_candidates(work_dir: Path, env: Mapping[str, str]) -> list[Path]:
    candidates: list[Path] = []
    env_pwd = env.get("PWD")
    if env_pwd:
        try:
            candidates.append(Path(env_pwd))
        except Exception:
            pass
    candidates.extend([work_dir])
    try:
        candidates.append(work_dir.resolve())
    except Exception:
        pass
    return candidates


def resolve_fallback_path(work_dir: Path) -> Path:
    try:
        return work_dir.resolve()
    except Exception:
        return work_dir


def project_key(work_dir: Path) -> str:
    return re.sub(r"[^A-Za-z0-9]", "-", str(work_dir))


def latest_session_id_for_candidates(*, candidates: list[Path], home_dir: Path, project_binding_fn) -> tuple[str | None, bool, Path | None]:
    session_env_root = home_dir / ".claude" / "session-env"
    best_uuid: Path | None = None
    best_any: Path | None = None
    has_any_history = False
    best_cwd: Path | None = None

    for work_dir in candidates:
        project_dir, matched_cwd = project_binding_fn(work_dir)
        if not project_dir.exists():
            continue
        session_files = list(project_dir.glob("*.jsonl"))
        if not session_files:
            continue
        has_any_history = True
        best_any, best_cwd = maybe_update_best_any(session_files, matched_cwd, best_any=best_any, best_cwd=best_cwd)
        best_uuid, best_cwd = update_best_uuid_session(
            session_files,
            matched_cwd,
            session_env_root=session_env_root,
            best_uuid=best_uuid,
            best_cwd=best_cwd,
        )

    if best_uuid is None and not has_any_history:
        # Fallback: if candidates didn't match (e.g. because a Windows executable generated
        # a slug using a Windows path instead of a Linux path), search all project directories.
        # Since this home_dir is already isolated per-agent, any history found here belongs to this agent.
        projects_root = home_dir / ".claude" / "projects"
        if projects_root.exists():
            fallback_cwd = candidates[0] if candidates else None
            for project_dir in projects_root.iterdir():
                if not project_dir.is_dir():
                    continue
                session_files = list(project_dir.glob("*.jsonl"))
                if not session_files:
                    continue
                has_any_history = True
                best_any, best_cwd = maybe_update_best_any(session_files, fallback_cwd, best_any=best_any, best_cwd=best_cwd)
                best_uuid, best_cwd = update_best_uuid_session(
                    session_files,
                    fallback_cwd,
                    session_env_root=session_env_root,
                    best_uuid=best_uuid,
                    best_cwd=best_cwd,
                )

    if best_uuid is not None:
        return best_uuid.stem, True, best_cwd
    if has_any_history:
        return None, True, best_cwd
    return None, False, None


def maybe_update_best_any(session_files: list[Path], work_dir: Path, *, best_any: Path | None, best_cwd: Path | None) -> tuple[Path | None, Path | None]:
    try:
        best_in_dir = max(session_files, key=lambda p: p.stat().st_mtime)
        if best_any is None or best_in_dir.stat().st_mtime > best_any.stat().st_mtime:
            return best_in_dir, work_dir
    except Exception:
        pass
    return best_any, best_cwd


def update_best_uuid_session(
    session_files: list[Path],
    work_dir: Path,
    *,
    session_env_root: Path,
    best_uuid: Path | None,
    best_cwd: Path | None,
) -> tuple[Path | None, Path | None]:
    for session_file in session_files:
        if not valid_uuid_session_file(session_file, session_env_root=session_env_root):
            continue
        if best_uuid is None:
            best_uuid = session_file
            best_cwd = work_dir
            continue
        try:
            if session_file.stat().st_mtime > best_uuid.stat().st_mtime:
                best_uuid = session_file
                best_cwd = work_dir
        except Exception:
            continue
    return best_uuid, best_cwd


def valid_uuid_session_file(session_file: Path, *, session_env_root: Path) -> bool:
    try:
        uuid.UUID(session_file.stem)
        if session_file.stat().st_size <= 0:
            return False
        if not (session_env_root / session_file.stem).exists():
            return False
    except Exception:
        return False
    return True


__all__ = ["ClaudeHistoryLocator", "history_candidates"]
