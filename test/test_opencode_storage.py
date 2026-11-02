from __future__ import annotations

import sqlite3
from pathlib import Path

from opencode_runtime.storage import OpenCodeStorageAccessor


def test_resolve_opencode_db_path_uses_existing_candidate_and_cache(tmp_path: Path) -> None:
    root = tmp_path / 'storage'
    root.mkdir()
    db_path = tmp_path / 'opencode.db'
    db_path.write_text('', encoding='utf-8')
    storage = OpenCodeStorageAccessor(root)

    first = storage.resolve_opencode_db_path()
    db_path.unlink()
    db_path.write_text('', encoding='utf-8')
    second = storage.resolve_opencode_db_path()

    assert first == db_path
    assert second == db_path


def test_fetch_opencode_db_rows_reads_rows_from_database(tmp_path: Path) -> None:
    root = tmp_path / 'storage'
    root.mkdir()
    db_path = tmp_path / 'opencode.db'
    conn = sqlite3.connect(str(db_path))
    conn.execute('CREATE TABLE session (id TEXT, time_updated INTEGER)')
    conn.execute('INSERT INTO session VALUES (?, ?)', ('sid-1', 7))
    conn.commit()
    conn.close()

    storage = OpenCodeStorageAccessor(root)
    rows = storage.fetch_opencode_db_rows(
        'SELECT id, time_updated FROM session ORDER BY time_updated DESC',
        (),
    )

    assert len(rows) == 1
    assert rows[0]['id'] == 'sid-1'


def test_wal_read_never_opens_or_changes_foreign_sidecars(tmp_path, monkeypatch):
    import opencode_runtime.storage as module
    root = tmp_path / 'storage'
    root.mkdir()
    db = tmp_path / 'opencode.db'
    writer = sqlite3.connect(db)
    try:
        writer.execute('PRAGMA journal_mode=WAL')
        writer.execute('CREATE TABLE session (id TEXT)')
        writer.execute("INSERT INTO session VALUES ('native-wal')")
        writer.commit()
        sidecars = [Path(str(db) + suffix) for suffix in ('', '-wal', '-shm')]
        before = [(p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_size) for p in sidecars]
        original_connect = sqlite3.connect
        opened = []

        def connect(path, **kwargs):
            opened.append(str(path))
            assert str(tmp_path) not in str(path), 'SQLite opened the provider database'
            return original_connect(path, **kwargs)

        monkeypatch.setattr(module.sqlite3, 'connect', connect)
        rows = OpenCodeStorageAccessor(root).fetch_opencode_db_rows('SELECT id FROM session', ())
        assert [row['id'] for row in rows] == ['native-wal']
        assert opened
        assert [(p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_size) for p in sidecars] == before
        assert writer.execute('SELECT count(*) FROM session').fetchone() == (1,)
    finally:
        writer.close()


def test_unknown_schema_is_explicit_instead_of_empty_reply(tmp_path):
    import pytest
    from opencode_runtime.storage import UnsupportedOpenCodeSchema
    db = tmp_path / 'opencode.db'
    with sqlite3.connect(db) as conn:
        conn.execute('CREATE TABLE session_v2 (id TEXT)')
    with pytest.raises(UnsupportedOpenCodeSchema, match='Unsupported OpenCode storage schema'):
        OpenCodeStorageAccessor(tmp_path / 'storage').fetch_opencode_db_rows('SELECT * FROM session', ())
