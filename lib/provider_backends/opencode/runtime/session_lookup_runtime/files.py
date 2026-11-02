from __future__ import annotations

from pathlib import Path

from .common import coerce_updated, directories_match
from .db import get_latest_session_from_db


def get_latest_session(reader) -> dict | None:
    native = getattr(reader, '_native', None)
    if native is not None:
        return native.session(reader._session_id_filter)
    session = get_latest_session_from_db(reader)
    if session is not None:
        return session
    return get_latest_session_from_files(reader)


def get_latest_session_from_files(reader) -> dict | None:
    sessions_dir = reader._session_dir()
    if not sessions_dir.exists():
        return None

    files = _session_files(sessions_dir)
    filtered_match, filtered_updated = _filtered_match(reader, files)
    best_match, best_updated, best_any = _scan_file_candidates(reader, files)

    if filtered_match is not None:
        if best_match is not None and best_updated > filtered_updated:
            return best_match
        return filtered_match
    if best_match is not None:
        return best_match
    if reader._allow_any_session:
        return best_any
    return None


def _session_files(sessions_dir: Path) -> list[Path]:
    try:
        return [path for path in sessions_dir.glob("ses_*.json") if path.is_file()]
    except Exception:
        return []


def _filtered_match(reader, files: list[Path]) -> tuple[dict | None, int]:
    if not reader._session_id_filter:
        return None, -1
    try:
        for path in files:
            payload = reader._load_json(path)
            sid = payload.get("id")
            if isinstance(sid, str) and sid == reader._session_id_filter:
                return {"path": path, "payload": payload}, coerce_updated((payload.get("time") or {}).get("updated"))
    except Exception:
        return None, -1
    return None, -1


def _scan_file_candidates(reader, files: list[Path]) -> tuple[dict | None, int, dict | None]:
    best_match: dict | None = None
    best_updated = -1
    best_mtime = -1.0
    best_any: dict | None = None
    best_any_updated = -1
    best_any_mtime = -1.0

    for path in files:
        payload = reader._load_json(path)
        entry = _file_entry(path, payload)
        if entry is None:
            continue
        updated = entry["updated"]
        mtime = entry["mtime"]
        candidate = entry["entry"]

        if updated > best_any_updated or (updated == best_any_updated and mtime >= best_any_mtime):
            best_any = candidate
            best_any_updated = updated
            best_any_mtime = mtime

        directory = candidate["payload"].get("directory")
        if not directories_match(reader, directory):
            continue
        if updated > best_updated or (updated == best_updated and mtime >= best_mtime):
            best_match = candidate
            best_updated = updated
            best_mtime = mtime

    return best_match, best_updated, best_any


def _file_entry(path: Path, payload: dict) -> dict | None:
    sid = payload.get("id")
    if not isinstance(sid, str) or not sid:
        return None
    updated = coerce_updated((payload.get("time") or {}).get("updated"))
    try:
        mtime = path.stat().st_mtime
    except Exception:
        mtime = 0.0
    return {
        "entry": {"path": path, "payload": payload},
        "updated": updated,
        "mtime": mtime,
    }


__all__ = ["get_latest_session", "get_latest_session_from_files"]
