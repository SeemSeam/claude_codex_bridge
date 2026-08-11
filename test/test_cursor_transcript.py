from __future__ import annotations

import json
from pathlib import Path

from provider_backends.cursor.transcript import (
    capture_cursor_transcript_offsets,
    iter_top_level_cursor_transcripts,
    read_new_cursor_transcript_records,
)


def _transcript(home: Path, session_id: str, *, workspace: str = "repo") -> Path:
    return (
        home
        / ".cursor"
        / "projects"
        / workspace
        / "agent-transcripts"
        / session_id
        / f"{session_id}.jsonl"
    )


def _append(path: Path, *records: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=True) + "\n")


def test_cursor_transcript_discovery_excludes_subagents(tmp_path: Path) -> None:
    home = tmp_path / "managed-home"
    parent = _transcript(home, "parent-session")
    subagent = parent.parent / "subagents" / "child-session.jsonl"
    _append(parent, {"role": "user", "message": {"content": []}})
    _append(subagent, {"role": "assistant", "message": {"content": []}})

    paths = iter_top_level_cursor_transcripts(home)
    offsets = capture_cursor_transcript_offsets(home)

    assert paths == (parent,)
    assert offsets == {str(parent): parent.stat().st_size}


def test_cursor_transcript_reader_finds_appends_and_new_top_level_files(tmp_path: Path) -> None:
    home = tmp_path / "managed-home"
    first = _transcript(home, "first-session")
    _append(first, {"role": "user", "message": {"content": [{"type": "text", "text": "old"}]}})
    offsets = capture_cursor_transcript_offsets(home)

    _append(first, {"type": "turn_ended", "status": "success"})
    second = _transcript(home, "second-session")
    new_user = {"role": "user", "message": {"content": [{"type": "text", "text": "new"}]}}
    _append(second, new_user)

    records, next_offsets = read_new_cursor_transcript_records(home, offsets)

    assert records == [
        (str(first), {"type": "turn_ended", "status": "success"}),
        (str(second), new_user),
    ]
    assert next_offsets[str(first)] == first.stat().st_size
    assert next_offsets[str(second)] == second.stat().st_size


def test_cursor_transcript_reader_does_not_advance_past_partial_line(tmp_path: Path) -> None:
    home = tmp_path / "managed-home"
    path = _transcript(home, "partial-session")
    path.parent.mkdir(parents=True, exist_ok=True)
    complete = json.dumps({"role": "assistant", "message": {"content": []}}) + "\n"
    path.write_bytes(complete.encode("utf-8") + b'{"type":"turn_')

    records, offsets = read_new_cursor_transcript_records(home, {})

    assert records == [
        (str(path), {"role": "assistant", "message": {"content": []}}),
    ]
    assert offsets[str(path)] == len(complete.encode("utf-8"))

    with path.open("ab") as handle:
        handle.write(b'ended","status":"success"}\n')
    records, offsets = read_new_cursor_transcript_records(home, offsets)

    assert records == [(str(path), {"type": "turn_ended", "status": "success"})]
    assert offsets[str(path)] == path.stat().st_size


def test_cursor_transcript_reader_skips_malformed_complete_records(tmp_path: Path) -> None:
    home = tmp_path / "managed-home"
    path = _transcript(home, "malformed-session")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "not-json\n" + json.dumps({"type": "turn_ended", "status": "error"}) + "\n",
        encoding="utf-8",
    )

    records, offsets = read_new_cursor_transcript_records(home, {})

    assert records == [(str(path), {"type": "turn_ended", "status": "error"})]
    assert offsets[str(path)] == path.stat().st_size
