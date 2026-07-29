from __future__ import annotations

import json
from pathlib import Path

from provider_backends.pi.pane_native_log import observe_pi_pane_turn


REQ = "job_pi_pane_001"
ANCHOR = f"CCB_REQ_ID: {REQ}"


def _write_session(path: Path, records: list[dict]) -> None:
    path.write_text("".join(json.dumps(record) + "\n" for record in records), encoding="utf-8")


def _session_header(session_id: str = "sess-1") -> dict:
    return {"type": "session", "version": 3, "id": session_id, "cwd": "/work"}


def _user(msg_id: str, parent: str, text: str) -> dict:
    return {
        "type": "message",
        "id": msg_id,
        "parentId": parent,
        "timestamp": "2026-07-29T03:00:00.000Z",
        "message": {"role": "user", "content": [{"type": "text", "text": text}]},
    }


def _assistant(msg_id: str, parent: str, *, thinking: str = "", text: str = "", stop: str = "stop") -> dict:
    content: list[dict] = []
    if thinking:
        content.append({"type": "thinking", "thinking": thinking, "thinkingSignature": "sig"})
    if text:
        content.append({"type": "text", "text": text})
    return {
        "type": "message",
        "id": msg_id,
        "parentId": parent,
        "timestamp": "2026-07-29T03:00:05.000Z",
        "message": {"role": "assistant", "content": content, "stopReason": stop, "model": "k3"},
    }


def test_reply_excludes_thinking_block(tmp_path: Path) -> None:
    _write_session(
        tmp_path / "s.jsonl",
        [
            _session_header(),
            _user("m1", "root", f"{ANCHOR}\n\nwhere are you running?"),
            _assistant("m2", "m1", thinking="The user asks about cwd. Let me answer.", text="I run in /work."),
        ],
    )

    observation = observe_pi_pane_turn(tmp_path, REQ)

    assert observation is not None
    assert observation.request_seen is True
    assert observation.completed is True
    assert observation.stop_reason == "stop"
    assert observation.reply == "I run in /work."


def test_pending_tool_stop_reason_is_not_complete(tmp_path: Path) -> None:
    _write_session(
        tmp_path / "s.jsonl",
        [
            _session_header(),
            _user("m1", "root", f"{ANCHOR}\n\ncount the lines"),
            _assistant("m2", "m1", text="reading the file", stop="tool_use"),
        ],
    )

    observation = observe_pi_pane_turn(tmp_path, REQ)

    assert observation is not None
    assert observation.request_seen is True
    assert observation.completed is False


def test_tool_round_returns_final_assistant_message(tmp_path: Path) -> None:
    _write_session(
        tmp_path / "s.jsonl",
        [
            _session_header(),
            _user("m1", "root", f"{ANCHOR}\n\ncount the lines"),
            _assistant("m2", "m1", text="calling wc", stop="tool_use"),
            _user("m3", "m2", "tool result: 54"),
            _assistant("m4", "m3", thinking="wc said 54.", text="LINES=54"),
        ],
    )

    observation = observe_pi_pane_turn(tmp_path, REQ)

    assert observation is not None
    assert observation.completed is True
    assert observation.reply == "LINES=54"


def test_walk_stops_before_the_next_anchored_turn(tmp_path: Path) -> None:
    _write_session(
        tmp_path / "s.jsonl",
        [
            _session_header(),
            _user("m1", "root", f"{ANCHOR}\n\nfirst question"),
            _assistant("m2", "m1", text="first answer"),
            _user("m3", "m2", "CCB_REQ_ID: job_pi_pane_002\n\nsecond question"),
            _assistant("m4", "m3", text="second answer"),
        ],
    )

    observation = observe_pi_pane_turn(tmp_path, REQ)

    assert observation is not None
    assert observation.reply == "first answer"


def test_anchor_seen_before_assistant_replies(tmp_path: Path) -> None:
    _write_session(
        tmp_path / "s.jsonl",
        [
            _session_header(),
            _user("m1", "root", f"{ANCHOR}\n\nstill thinking"),
        ],
    )

    observation = observe_pi_pane_turn(tmp_path, REQ)

    assert observation is not None
    assert observation.request_seen is True
    assert observation.completed is False
    assert observation.reply == ""


def test_unknown_request_returns_none(tmp_path: Path) -> None:
    _write_session(
        tmp_path / "s.jsonl",
        [
            _session_header(),
            _user("m1", "root", "CCB_REQ_ID: some_other_job\n\nhello"),
            _assistant("m2", "m1", text="hi"),
        ],
    )

    assert observe_pi_pane_turn(tmp_path, REQ) is None
    assert observe_pi_pane_turn(tmp_path, "") is None


def test_turn_is_found_across_sibling_session_files(tmp_path: Path) -> None:
    _write_session(
        tmp_path / "other.jsonl",
        [_session_header("sess-other"), _user("a1", "root", "CCB_REQ_ID: unrelated\n\nhi")],
    )
    _write_session(
        tmp_path / "target.jsonl",
        [
            _session_header("sess-target"),
            _user("m1", "root", f"{ANCHOR}\n\nquestion"),
            _assistant("m2", "m1", text="answer"),
        ],
    )

    observation = observe_pi_pane_turn(tmp_path, REQ)

    assert observation is not None
    assert observation.session_id == "sess-target"
    assert observation.reply == "answer"


def test_partial_trailing_record_is_tolerated(tmp_path: Path) -> None:
    path = tmp_path / "s.jsonl"
    _write_session(
        path,
        [
            _session_header(),
            _user("m1", "root", f"{ANCHOR}\n\nquestion"),
            _assistant("m2", "m1", text="answer"),
        ],
    )
    with path.open("a", encoding="utf-8") as handle:
        handle.write('{"type":"message","id":"m3","parentI')

    observation = observe_pi_pane_turn(tmp_path, REQ)

    assert observation is not None
    assert observation.completed is True
    assert observation.reply == "answer"


def test_missing_session_dir_returns_none(tmp_path: Path) -> None:
    assert observe_pi_pane_turn(tmp_path / "absent", REQ) is None
