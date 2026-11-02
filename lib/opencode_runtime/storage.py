from __future__ import annotations

import json
import os
import sqlite3
import shutil
import tempfile
from pathlib import Path
from typing import Any


class UnsupportedOpenCodeSchema(RuntimeError):
    """Provider storage changed; never interpret it as an empty conversation."""


class OpenCodeStorageAccessor:
    def __init__(
        self,
        root: Path,
        *,
        db_env_var: str = "OPENCODE_DB_PATH",
        db_filenames: tuple[str, ...] = ("opencode.db",),
    ) -> None:
        self.root = Path(root).expanduser()
        self._db_path_hint: Path | None = None
        self.db_env_var = str(db_env_var or "").strip()
        self.db_filenames = tuple(str(item).strip() for item in db_filenames if str(item).strip()) or ("opencode.db",)

    def session_dir(self, project_id: str) -> Path:
        return self.root / "session" / project_id

    def message_dir(self, session_id: str) -> Path:
        nested = self.root / "message" / session_id
        if nested.exists():
            return nested
        return self.root / "message"

    def part_dir(self, message_id: str) -> Path:
        nested = self.root / "part" / message_id
        if nested.exists():
            return nested
        return self.root / "part"

    def load_json(self, path: Path) -> dict:
        try:
            raw = path.read_text(encoding="utf-8")
            data = json.loads(raw)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def load_json_blob(self, raw: Any) -> dict:
        if isinstance(raw, dict):
            return raw
        if not isinstance(raw, str) or not raw:
            return {}
        try:
            payload = json.loads(raw)
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def opencode_db_candidates(self) -> list[Path]:
        candidates: list[Path] = []
        env = (os.environ.get(self.db_env_var) or "").strip() if self.db_env_var else ""
        if env:
            candidates.append(Path(env).expanduser())

        for filename in self.db_filenames:
            candidates.append(self.root.parent / filename)
            candidates.append(self.root / filename)

        out: list[Path] = []
        seen: set[str] = set()
        for candidate in candidates:
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            out.append(candidate)
        return out

    def resolve_opencode_db_path(self) -> Path | None:
        cached = self._cached_db_path()
        if cached is not None:
            return cached
        resolved = self._existing_db_candidate()
        self._db_path_hint = resolved
        return resolved

    def fetch_opencode_db_rows(self, query: str, params: tuple[object, ...]) -> list[sqlite3.Row]:
        db_path = self.resolve_opencode_db_path()
        if not db_path:
            return []
        conn = _open_readonly_connection(db_path)
        if conn is None:
            return []
        try:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA busy_timeout = 200")
            return _row_results(conn.execute(query, params).fetchall())
        except sqlite3.OperationalError as exc:
            if "no such table" in str(exc) or "no such column" in str(exc):
                raise UnsupportedOpenCodeSchema(
                    "Unsupported OpenCode storage schema; use a compatible native state adapter "
                    "before submitting tasks (issues #401/#402)."
                ) from exc
            return []
        except Exception:
            return []
        finally:
            _close_connection(conn)

    @staticmethod
    def message_sort_key(message: dict) -> tuple[int, float, str]:
        created = (message.get("time") or {}).get("created")
        try:
            created_i = int(created)
        except Exception:
            created_i = -1
        try:
            mtime = Path(message.get("_path", "")).stat().st_mtime if message.get("_path") else 0.0
        except Exception:
            mtime = 0.0
        message_id = message.get("id") if isinstance(message.get("id"), str) else ""
        return created_i, mtime, message_id

    @staticmethod
    def part_sort_key(part: dict) -> tuple[int, float, str]:
        started = (part.get("time") or {}).get("start")
        try:
            started_i = int(started)
        except Exception:
            started_i = -1
        try:
            mtime = Path(part.get("_path", "")).stat().st_mtime if part.get("_path") else 0.0
        except Exception:
            mtime = 0.0
        part_id = part.get("id") if isinstance(part.get("id"), str) else ""
        return started_i, mtime, part_id

    def _cached_db_path(self) -> Path | None:
        candidate = self._db_path_hint
        if candidate is None:
            return None
        try:
            if candidate.exists():
                return candidate
        except Exception:
            return None
        return None

    def _existing_db_candidate(self) -> Path | None:
        for candidate in self.opencode_db_candidates():
            try:
                if candidate.exists() and candidate.is_file():
                    return candidate
            except Exception:
                continue
        return None


class _SnapshotConnection(sqlite3.Connection):
    snapshot_dir: tempfile.TemporaryDirectory | None = None

    def close(self) -> None:
        try:
            super().close()
        finally:
            if self.snapshot_dir is not None:
                self.snapshot_dir.cleanup()
                self.snapshot_dir = None


def _open_readonly_connection(db_path: Path) -> sqlite3.Connection | None:
    # mode=ro can still truncate the owner's WAL shared memory (issue #401).
    # Never open the foreign database with SQLite, even as a fallback. Recover
    # a private copy of main+WAL instead; SQLite checks WAL frame checksums.
    snapshot = tempfile.TemporaryDirectory(prefix="ccb-opencode-read-")
    try:
        target = Path(snapshot.name) / "opencode.db"
        journal = Path(str(db_path) + "-journal")
        if journal.exists() and journal.stat().st_size:
            snapshot.cleanup()
            return None
        wal = Path(str(db_path) + "-wal")
        before = (db_path.stat(), wal.stat() if wal.exists() else None)
        shutil.copyfile(db_path, target)
        if before[1] is not None:
            shutil.copyfile(wal, Path(str(target) + "-wal"))
        after = (db_path.stat(), wal.stat() if wal.exists() else None)
        # Checkpoint/truncate/replacement while copying is not a coherent read.
        def signature(stats):
            return tuple(None if st is None else (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns) for st in stats)
        if signature(before) != signature(after):
            snapshot.cleanup()
            return None
        conn = sqlite3.connect(str(target), timeout=0.2, factory=_SnapshotConnection)
        conn.snapshot_dir = snapshot
        conn.execute("PRAGMA query_only=ON")
        return conn
    except Exception:
        snapshot.cleanup()
        return None


def _row_results(rows: list[object]) -> list[sqlite3.Row]:
    return [row for row in rows if isinstance(row, sqlite3.Row)]


def _close_connection(conn: sqlite3.Connection | None) -> None:
    try:
        if conn is not None:
            conn.close()
    except Exception:
        pass
