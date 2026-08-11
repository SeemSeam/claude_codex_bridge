from __future__ import annotations

import json
from pathlib import Path


def cursor_transcript_root(cursor_home: Path) -> Path:
    return Path(cursor_home).expanduser() / ".cursor" / "projects"


def iter_top_level_cursor_transcripts(cursor_home: Path) -> tuple[Path, ...]:
    root = cursor_transcript_root(cursor_home)
    if not root.is_dir():
        return ()
    paths = root.glob("*/agent-transcripts/*/*.jsonl")
    return tuple(sorted((path for path in paths if path.is_file()), key=_path_order))


def capture_cursor_transcript_offsets(cursor_home: Path) -> dict[str, int]:
    offsets: dict[str, int] = {}
    for path in iter_top_level_cursor_transcripts(cursor_home):
        try:
            offsets[str(path)] = path.stat().st_size
        except OSError:
            continue
    return offsets


def read_new_cursor_transcript_records(
    cursor_home: Path,
    offsets: dict[str, int],
) -> tuple[list[tuple[str, dict]], dict[str, int]]:
    next_offsets = dict(offsets)
    records: list[tuple[str, dict]] = []
    for path in iter_top_level_cursor_transcripts(cursor_home):
        key = str(path)
        offset = max(0, int(next_offsets.get(key, 0)))
        try:
            with path.open("rb") as handle:
                handle.seek(offset)
                chunk = handle.read()
        except OSError:
            continue
        complete_end = chunk.rfind(b"\n") + 1
        if complete_end <= 0:
            continue
        next_offsets[key] = offset + complete_end
        for raw_line in chunk[:complete_end].splitlines():
            try:
                payload = json.loads(raw_line.decode("utf-8", errors="strict"))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if isinstance(payload, dict):
                records.append((key, payload))
    return records, next_offsets


def cursor_record_text(record: dict) -> str:
    message = record.get("message")
    if not isinstance(message, dict):
        return ""
    return _content_text(message.get("content"))


def _content_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return "".join(_content_text(item) for item in value)
    if not isinstance(value, dict):
        return ""
    if str(value.get("type") or "").strip().lower() not in ("", "text"):
        return ""
    text = value.get("text")
    return str(text) if isinstance(text, str) else ""


def _path_order(path: Path) -> tuple[int, str]:
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        mtime_ns = 0
    return mtime_ns, str(path)


__all__ = [
    "capture_cursor_transcript_offsets",
    "cursor_record_text",
    "cursor_transcript_root",
    "iter_top_level_cursor_transcripts",
    "read_new_cursor_transcript_records",
]
