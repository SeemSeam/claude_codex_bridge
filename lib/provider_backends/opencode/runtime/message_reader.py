from __future__ import annotations


def read_messages(reader, session_id: str) -> list[dict]:
    native = getattr(reader, '_native', None)
    if native is not None:
        return native.messages(session_id)
    messages = read_messages_from_db(reader, session_id)
    if messages:
        messages.sort(key=reader._message_sort_key)
        return messages

    messages = read_messages_from_files(reader, session_id)
    messages.sort(key=reader._message_sort_key)
    return messages


def read_messages_from_files(reader, session_id: str) -> list[dict]:
    message_dir = reader._message_dir(session_id)
    if not message_dir.exists():
        return []
    messages: list[dict] = []
    try:
        paths = [path for path in message_dir.glob("msg_*.json") if path.is_file()]
    except Exception:
        paths = []
    for path in paths:
        payload = reader._load_json(path)
        if payload.get("sessionID") != session_id:
            continue
        payload["_path"] = str(path)
        messages.append(payload)
    return messages


def read_messages_from_db(reader, session_id: str) -> list[dict]:
    rows = reader._fetch_opencode_db_rows(
        """
        SELECT id, session_id, time_created, time_updated, data
        FROM message
        WHERE session_id = ?
        ORDER BY time_created ASC, time_updated ASC, id ASC
        """,
        (session_id,),
    )
    if not rows:
        return []

    messages: list[dict] = []
    for row in rows:
        payload = reader._load_json_blob(row["data"]) or {}
        payload.setdefault("id", row["id"])
        payload.setdefault("sessionID", row["session_id"])
        time_data = payload.get("time")
        if not isinstance(time_data, dict):
            time_data = {}
        if time_data.get("created") is None:
            time_data["created"] = row["time_created"]
        if time_data.get("updated") is None:
            time_data["updated"] = row["time_updated"]
        payload["time"] = time_data
        messages.append(payload)
    return messages


def read_parts(reader, message_id: str) -> list[dict]:
    native = getattr(reader, '_native', None)
    if native is not None:
        return native.parts.get(message_id, [])
    parts = read_parts_from_db(reader, message_id)
    if parts:
        parts.sort(key=reader._part_sort_key)
        return parts

    parts = read_parts_from_files(reader, message_id)
    parts.sort(key=reader._part_sort_key)
    return parts


def read_parts_from_files(reader, message_id: str) -> list[dict]:
    part_dir = reader._part_dir(message_id)
    if not part_dir.exists():
        return []
    parts: list[dict] = []
    try:
        paths = [path for path in part_dir.glob("prt_*.json") if path.is_file()]
    except Exception:
        paths = []
    for path in paths:
        payload = reader._load_json(path)
        if payload.get("messageID") != message_id:
            continue
        payload["_path"] = str(path)
        parts.append(payload)
    return parts


def read_parts_from_db(reader, message_id: str) -> list[dict]:
    rows = reader._fetch_opencode_db_rows(
        """
        SELECT id, message_id, session_id, time_created, time_updated, data
        FROM part
        WHERE message_id = ?
        ORDER BY time_created ASC, time_updated ASC, id ASC
        """,
        (message_id,),
    )
    if not rows:
        return []

    parts: list[dict] = []
    for row in rows:
        payload = reader._load_json_blob(row["data"]) or {}
        payload.setdefault("id", row["id"])
        payload.setdefault("messageID", row["message_id"])
        payload.setdefault("sessionID", row["session_id"])
        time_data = payload.get("time")
        if not isinstance(time_data, dict):
            time_data = {}
        if time_data.get("start") is None:
            time_data["start"] = row["time_created"]
        if time_data.get("updated") is None:
            time_data["updated"] = row["time_updated"]
        payload["time"] = time_data
        parts.append(payload)
    return parts


__all__ = [
    "read_messages",
    "read_messages_from_db",
    "read_messages_from_files",
    "read_parts",
    "read_parts_from_db",
    "read_parts_from_files",
]
