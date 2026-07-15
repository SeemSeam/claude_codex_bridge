from __future__ import annotations

import json
import os
from pathlib import Path

from completion.models import CompletionItemKind, CompletionSourceKind, CompletionStatus
from provider_backends.codex.comm import CodexLogReader
from provider_execution.base import ProviderSubmission


def test_codex_log_reader_keeps_bound_session(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    work_dir = tmp_path / "repo"
    work_dir.mkdir()
    preferred = root / "2026" / "abc-session.jsonl"
    newer = root / "2026" / "other-session.jsonl"
    preferred.parent.mkdir(parents=True)

    meta = json.dumps({"type": "session_meta", "payload": {"cwd": str(work_dir)}}) + "\n"
    preferred.write_text(meta, encoding="utf-8")
    preferred_mtime = preferred.stat().st_mtime
    os.utime(preferred, (preferred_mtime - 30.0, preferred_mtime - 30.0))
    newer.write_text(meta, encoding="utf-8")
    preferred_mtime = preferred.stat().st_mtime
    newer_mtime = newer.stat().st_mtime
    os.utime(preferred, (preferred_mtime - 30.0, preferred_mtime - 30.0))
    os.utime(newer, (newer_mtime, newer_mtime))

    reader = CodexLogReader(
        root=root,
        log_path=preferred,
        session_id_filter="abc",
        work_dir=work_dir,
    )

    assert reader.current_log_path() == preferred


def test_codex_log_reader_follows_newer_workspace_session_when_enabled(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    work_dir = tmp_path / "repo"
    work_dir.mkdir()
    preferred = root / "2026" / "abc-session.jsonl"
    preferred.parent.mkdir(parents=True, exist_ok=True)

    meta = json.dumps({"type": "session_meta", "payload": {"cwd": str(work_dir)}}) + "\n"
    preferred.write_text(meta, encoding="utf-8")

    reader = CodexLogReader(
        root=root,
        log_path=preferred,
        session_id_filter="abc",
        work_dir=work_dir,
        follow_workspace_sessions=True,
    )
    state = reader.capture_state()
    assert state["log_path"] == preferred

    rotated = root / "2026" / "rotated-session.jsonl"
    rotated.write_text(
        meta
        + json.dumps(
            {
                "timestamp": "2026-04-04T10:39:14.000Z",
                "type": "response_item",
                "payload": {
                    "type": "message",
                    "role": "user",
                    "content": [{"type": "input_text", "text": "CCB_REQ_ID: req-rotate\n\nhello"}],
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    rotated_mtime = rotated.stat().st_mtime
    os.utime(rotated, (rotated_mtime + 30.0, rotated_mtime + 30.0))
    state["last_rescan"] = 0.0

    entries, next_state = reader.try_get_entries(state)

    assert entries == []
    assert next_state["log_path"] == rotated
    assert reader.current_log_path() == rotated

    entries, _final_state = reader.try_get_entries(next_state)
    assert len(entries) == 1
    assert entries[0]["role"] == "user"
    assert "CCB_REQ_ID: req-rotate" in entries[0]["text"]


def test_codex_log_reader_replays_first_entries_when_log_appears_after_capture(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    work_dir = tmp_path / "repo"
    work_dir.mkdir()

    reader = CodexLogReader(root=root, work_dir=work_dir)
    state = reader.capture_state()
    assert state["log_path"] is None
    assert state["offset"] == -1

    log_path = root / "2026" / "ccb-codex-session.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        "\n".join(
            [
                json.dumps({"type": "session_meta", "payload": {"cwd": str(work_dir)}}),
                json.dumps(
                    {
                        "timestamp": "2026-03-24T00:00:00.000Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "CCB_REQ_ID: req-1\n\nhello"}],
                        },
                    }
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    entries, next_state = reader.try_get_entries(state)

    assert len(entries) == 1
    assert entries[0]["role"] == "user"
    assert "CCB_REQ_ID: req-1" in entries[0]["text"]
    assert next_state["log_path"] == log_path


def test_codex_execution_reader_factory_uses_bound_root_and_disables_workspace_follow(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from provider_execution import codex as codex_adapter_module

    captured: dict[str, object] = {}
    session_root = tmp_path / ".codex" / "sessions"

    class _Reader:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    class _Session:
        codex_session_path = str(tmp_path / "session.jsonl")
        codex_session_id = "session-old"
        codex_session_root = str(session_root)
        work_dir = str(tmp_path / "repo")
        data = {"codex_session_root": str(session_root), "codex_session_id": "session-old"}

    monkeypatch.setattr(codex_adapter_module, "CodexLogReader", _Reader)

    codex_adapter_module._reader_factory(_Session(), None)

    assert captured["root"] == session_root
    assert captured["log_path"] == tmp_path / "session.jsonl"
    assert captured["session_id_filter"] == "session-old"
    assert captured["work_dir"] == tmp_path / "repo"
    assert captured["follow_workspace_sessions"] is False


def test_codex_execution_reader_factory_disables_workspace_follow_for_ambiguous_inplace_agents(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from provider_execution import codex as codex_adapter_module

    captured: dict[str, object] = {}
    work_dir = tmp_path / "repo"
    work_dir.mkdir(parents=True, exist_ok=True)
    session_dir = work_dir / ".ccb"
    session_dir.mkdir(parents=True, exist_ok=True)
    session_file = session_dir / ".codex-agent1-session"
    session_file.write_text(json.dumps({"work_dir": str(work_dir)}), encoding="utf-8")
    (session_dir / ".codex-agent2-session").write_text(json.dumps({"work_dir": str(work_dir)}), encoding="utf-8")

    class _Reader:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    class _Session:
        codex_session_path = str(tmp_path / "session.jsonl")
        codex_session_id = "session-old"
        data = {"codex_session_id": "session-old"}

    _Session.work_dir = str(work_dir)
    _Session.session_file = session_file

    monkeypatch.setattr(codex_adapter_module, "CodexLogReader", _Reader)

    codex_adapter_module._reader_factory(_Session(), None)

    assert captured["follow_workspace_sessions"] is False


def test_codex_execution_reader_factory_enables_workspace_follow_for_unbound_session(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from provider_execution import codex as codex_adapter_module

    captured: dict[str, object] = {}
    session_root = tmp_path / ".codex" / "sessions"

    class _Reader:
        def __init__(self, **kwargs) -> None:
            captured.update(kwargs)

    class _Session:
        codex_session_path = ""
        codex_session_id = ""
        codex_session_root = str(session_root)
        work_dir = str(tmp_path / "repo")
        data = {"codex_session_root": str(session_root)}

    monkeypatch.setattr(codex_adapter_module, "CodexLogReader", _Reader)

    codex_adapter_module._reader_factory(_Session(), None)

    assert captured["root"] == session_root
    assert captured["follow_workspace_sessions"] is True


def test_codex_execution_quarantines_matching_fallback_until_official_binding_moves(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from provider_execution import codex as codex_adapter_module

    work_dir = tmp_path / "repo"
    root = tmp_path / "sessions"
    old_id = "11111111-1111-1111-1111-111111111111"
    new_id = "22222222-2222-2222-2222-222222222222"
    old_log = _codex_log(root, old_id, work_dir, entries=())
    new_log = _codex_log(
        root,
        new_id,
        work_dir,
        entries=(
            _codex_user_entry("job_1", "hello"),
            _codex_assistant_entry("done"),
            _codex_task_complete_entry("done"),
        ),
    )
    os.utime(old_log, (100, 100))
    os.utime(new_log, (200, 200))

    monkeypatch.setattr(
        codex_adapter_module,
        "_load_session",
        lambda work_dir_arg, agent_name: _CodexSession(
            work_dir=work_dir_arg,
            root=root,
            log_path=old_log,
            session_id=old_id,
        ),
    )

    submission = _codex_submission(
        reader=CodexLogReader(root=root, log_path=old_log, session_id_filter=old_id, work_dir=work_dir),
        state={"log_path": old_log, "offset": old_log.stat().st_size},
        work_dir=work_dir,
    )

    result = codex_adapter_module.CodexProviderAdapter().poll(submission, now="2026-04-04T10:00:00Z")

    assert result is not None
    assert result.submission.runtime_state["session_path"] == str(old_log)
    assert result.submission.runtime_state["state"]["log_path"] == old_log
    assert result.submission.runtime_state["codex_anchor_fallback_log"] == str(new_log)
    assert result.submission.runtime_state["codex_anchor_fallback_session_id"] == new_id
    assert result.submission.runtime_state["codex_anchor_fallback_quarantined"] is True
    assert result.submission.runtime_state["anchor_seen"] is False
    assert result.submission.reply == ""
    assert result.items == ()
    assert result.decision is None


def test_codex_execution_rebinds_exact_anchor_fallback_after_clear_stale_binding(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from provider_execution import codex as codex_adapter_module

    work_dir = tmp_path / "repo"
    runtime_dir = work_dir / ".ccb" / "agents" / "agent1" / "provider-runtime" / "codex"
    root = tmp_path / "sessions"
    old_id = "11111111-1111-1111-1111-111111111111"
    new_id = "22222222-2222-2222-2222-222222222222"
    old_log = _codex_log(root, old_id, work_dir, entries=())
    new_log = _codex_log(
        root,
        new_id,
        tmp_path / "fresh-rollout-workspace",
        entries=(
            _codex_user_entry("job_1", "hello after clear"),
            _codex_assistant_entry("completed after clear"),
            _codex_task_complete_entry("completed after clear"),
        ),
    )
    os.utime(old_log, (100, 100))
    os.utime(new_log, (200, 200))

    session_file = work_dir / ".ccb" / ".codex-agent1-session"
    session = _WritableCodexSession(
        work_dir=work_dir,
        root=root,
        log_path=old_log,
        session_id=old_id,
        runtime_dir=runtime_dir,
        session_file=session_file,
    )
    session._write_back()
    monkeypatch.setattr(codex_adapter_module, "_load_session", lambda work_dir_arg, agent_name: session)

    submission = _codex_submission(
        reader=CodexLogReader(root=root, log_path=old_log, session_id_filter=old_id, work_dir=work_dir),
        state={"log_path": old_log, "offset": old_log.stat().st_size},
        work_dir=work_dir,
        delivery=True,
    )

    result = codex_adapter_module.CodexProviderAdapter().poll(submission, now="2026-04-04T10:00:05Z")

    assert result is not None
    assert result.decision is None
    assert result.submission.reply == "completed after clear"
    assert result.submission.runtime_state["anchor_seen"] is True
    assert result.submission.runtime_state["session_path"] == str(new_log)
    assert result.submission.runtime_state["state"]["log_path"] == new_log
    assert result.submission.runtime_state["delivery_state"] == "accepted"
    assert "codex_anchor_fallback_quarantined" not in result.submission.runtime_state

    persisted = json.loads(session_file.read_text(encoding="utf-8"))
    assert persisted["codex_session_id"] == new_id
    assert persisted["codex_session_path"] == str(new_log)
    assert persisted["old_codex_session_id"] == old_id
    assert persisted["old_codex_session_path"] == str(old_log)

    switch = json.loads((runtime_dir / "session-switch.json").read_text(encoding="utf-8"))
    assert switch["state"] == "auto_rebound"
    assert switch["reason"] == "exact_request_anchor_fallback"
    assert switch["candidate"]["session_id"] == new_id
    assert switch["candidate"]["session_path"] == str(new_log)


def test_codex_delivery_timeout_polls_exact_changed_cwd_anchor_before_failure(monkeypatch, tmp_path: Path) -> None:
    from provider_execution import codex as codex_adapter_module

    work_dir = tmp_path / "repo"
    root = tmp_path / "sessions"
    old_id = "11111111-1111-1111-1111-111111111111"
    new_id = "22222222-2222-2222-2222-222222222222"
    old_log = _codex_log(root, old_id, work_dir, entries=())
    new_log = _codex_log(
        root,
        new_id,
        tmp_path / "groups" / "talk2_workers",
        entries=(_codex_user_entry("job_1", "delivered"),),
    )
    os.utime(old_log, (100, 100))
    os.utime(new_log, (200, 200))
    session = _WritableCodexSession(
        work_dir=work_dir,
        root=root,
        log_path=old_log,
        session_id=old_id,
        runtime_dir=work_dir / ".ccb" / "agents" / "agent1" / "provider-runtime" / "codex",
        session_file=work_dir / ".ccb" / ".codex-agent1-session",
    )
    session._write_back()
    monkeypatch.setattr(codex_adapter_module, "_load_session", lambda work_dir_arg, agent_name: session)
    submission = _codex_submission(
        reader=CodexLogReader(root=root, log_path=old_log, session_id_filter=old_id, work_dir=work_dir),
        state={"log_path": old_log, "offset": old_log.stat().st_size},
        work_dir=work_dir,
        delivery=True,
    )

    result = codex_adapter_module.CodexProviderAdapter().poll(submission, now="2026-04-04T10:03:00Z")

    assert result is not None
    assert result.decision is None
    assert result.submission.runtime_state["session_path"] == str(new_log)
    assert result.submission.runtime_state["anchor_seen"] is True
    assert result.submission.runtime_state["delivery_state"] == "accepted"
    assert "codex_anchor_fallback_log" not in result.submission.runtime_state
    assert result.submission.runtime_state.get("delivery_failure_kind") is None


def test_codex_execution_does_not_switch_without_current_anchor(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from provider_execution import codex as codex_adapter_module

    work_dir = tmp_path / "repo"
    root = tmp_path / "sessions"
    old_id = "11111111-1111-1111-1111-111111111111"
    new_id = "22222222-2222-2222-2222-222222222222"
    old_log = _codex_log(root, old_id, work_dir, entries=())
    new_log = _codex_log(root, new_id, work_dir, entries=(_codex_user_entry("job_other", "hello"),))
    os.utime(old_log, (100, 100))
    os.utime(new_log, (200, 200))

    monkeypatch.setattr(
        codex_adapter_module,
        "_load_session",
        lambda work_dir_arg, agent_name: _CodexSession(
            work_dir=work_dir_arg,
            root=root,
            log_path=old_log,
            session_id=old_id,
        ),
    )

    submission = _codex_submission(
        reader=CodexLogReader(root=root, log_path=old_log, session_id_filter=old_id, work_dir=work_dir),
        state={"log_path": old_log, "offset": old_log.stat().st_size},
        work_dir=work_dir,
    )

    refreshed = codex_adapter_module._refresh_reader_for_current_session_binding(submission)

    assert refreshed.runtime_state["state"]["log_path"] == old_log
    assert "codex_anchor_fallback_log" not in refreshed.runtime_state


def test_codex_delivery_guard_fails_on_shutdown_text_without_anchor(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from provider_execution import codex as codex_adapter_module

    work_dir = tmp_path / "repo"
    root = tmp_path / "sessions"
    session_id = "11111111-1111-1111-1111-111111111111"
    log_path = _codex_log(root, session_id, work_dir, entries=())

    monkeypatch.setattr(
        codex_adapter_module,
        "_load_session",
        lambda work_dir_arg, agent_name: _CodexSession(
            work_dir=work_dir_arg,
            root=root,
            log_path=log_path,
            session_id=session_id,
        ),
    )

    submission = _codex_submission(
        reader=CodexLogReader(root=root, log_path=log_path, session_id_filter=session_id, work_dir=work_dir),
        state={"log_path": log_path, "offset": log_path.stat().st_size},
        work_dir=work_dir,
        backend=_Backend(pane_content=">_ OpenAI Codex\nShutting down...\nPane is dead"),
        delivery=True,
    )

    result = codex_adapter_module.CodexProviderAdapter().poll(submission, now="2026-04-04T10:00:10Z")

    assert result is not None
    assert result.decision is not None
    assert result.decision.status is CompletionStatus.FAILED
    assert result.decision.reason == "codex_prompt_delivery_failed"
    assert result.decision.diagnostics["delivery_failure_kind"] == "delivery_shutdown"
    assert result.items[0].kind is CompletionItemKind.ERROR
    assert result.items[0].payload["delivery_failure_kind"] == "delivery_shutdown"


def test_codex_delivery_guard_fails_on_shutdown_text_with_missing_session_file(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from provider_execution import codex as codex_adapter_module

    work_dir = tmp_path / "repo"
    root = tmp_path / "sessions"
    session_id = "11111111-1111-1111-1111-111111111111"
    missing_log = root / "2026" / "04" / "04" / f"rollout-{session_id}.jsonl"

    monkeypatch.setattr(
        codex_adapter_module,
        "_load_session",
        lambda work_dir_arg, agent_name: _CodexSession(
            work_dir=work_dir_arg,
            root=root,
            log_path=missing_log,
            session_id=session_id,
        ),
    )

    submission = _codex_submission(
        reader=CodexLogReader(root=root, log_path=missing_log, session_id_filter=session_id, work_dir=work_dir),
        state={"log_path": missing_log, "offset": 0},
        work_dir=work_dir,
        backend=_Backend(pane_content=">_ OpenAI Codex\nShutting down...\nPane is dead"),
        delivery=True,
    )

    result = codex_adapter_module.CodexProviderAdapter().poll(submission, now="2026-04-04T10:00:10Z")

    assert result is not None
    assert result.decision is not None
    assert result.decision.status is CompletionStatus.FAILED
    assert result.decision.reason == "codex_prompt_delivery_failed"
    assert result.decision.diagnostics["delivery_failure_kind"] == "delivery_shutdown"
    assert result.decision.diagnostics["no_reply_reason"] == "provider_crashed"
    assert result.items[0].kind is CompletionItemKind.ERROR
    assert result.items[0].payload["no_reply_reason"] == "provider_crashed"


def test_codex_delivery_guard_waits_for_slow_anchor_without_evidence(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from provider_execution import codex as codex_adapter_module

    work_dir = tmp_path / "repo"
    root = tmp_path / "sessions"
    session_id = "11111111-1111-1111-1111-111111111111"
    log_path = _codex_log(root, session_id, work_dir, entries=())

    monkeypatch.setattr(
        codex_adapter_module,
        "_load_session",
        lambda work_dir_arg, agent_name: _CodexSession(
            work_dir=work_dir_arg,
            root=root,
            log_path=log_path,
            session_id=session_id,
        ),
    )

    submission = _codex_submission(
        reader=CodexLogReader(root=root, log_path=log_path, session_id_filter=session_id, work_dir=work_dir),
        state={"log_path": log_path, "offset": log_path.stat().st_size},
        work_dir=work_dir,
        backend=_Backend(pane_content=">_ OpenAI Codex\nmodel: gpt-5.5 /model to change\n› Implement {feature}"),
        delivery=True,
    )

    result = codex_adapter_module.CodexProviderAdapter().poll(submission, now="2026-04-04T10:00:30Z")

    assert result is None or (result.decision is None and result.items == ())
    if result is not None:
        assert result.submission.runtime_state["delivery_state"] == "pending_anchor"


def test_codex_delivery_guard_times_out_after_anchor_never_appears(
    monkeypatch,
    tmp_path: Path,
) -> None:
    from provider_execution import codex as codex_adapter_module

    work_dir = tmp_path / "repo"
    root = tmp_path / "sessions"
    session_id = "11111111-1111-1111-1111-111111111111"
    log_path = _codex_log(root, session_id, work_dir, entries=())

    monkeypatch.setattr(
        codex_adapter_module,
        "_load_session",
        lambda work_dir_arg, agent_name: _CodexSession(
            work_dir=work_dir_arg,
            root=root,
            log_path=log_path,
            session_id=session_id,
        ),
    )

    submission = _codex_submission(
        reader=CodexLogReader(root=root, log_path=log_path, session_id_filter=session_id, work_dir=work_dir),
        state={"log_path": log_path, "offset": log_path.stat().st_size},
        work_dir=work_dir,
        backend=_Backend(pane_content=">_ OpenAI Codex\nmodel: gpt-5.5 /model to change\n› Implement {feature}"),
        delivery=True,
    )

    result = codex_adapter_module.CodexProviderAdapter().poll(submission, now="2026-04-04T10:02:01Z")

    assert result is not None
    assert result.decision is not None
    assert result.decision.status is CompletionStatus.FAILED
    assert result.decision.reason == "codex_prompt_delivery_failed"
    assert result.decision.diagnostics["delivery_failure_kind"] == "delivery_anchor_missing"
    assert result.decision.diagnostics["delivery_retryable"] is True


def test_codex_log_reader_matches_wsl_and_windows_style_workdirs(tmp_path: Path) -> None:
    root = tmp_path / "sessions"
    log_path = root / "2026" / "wsl-session.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text(
        json.dumps({"type": "session_meta", "payload": {"cwd": "/mnt/C/Users/alice/repo"}}) + "\n",
        encoding="utf-8",
    )

    reader = CodexLogReader(root=root, work_dir=Path("c:/Users/alice/repo"))

    assert reader.current_log_path() == log_path


class _Backend:
    def __init__(self, *, alive: bool = True, pane_content: str = "") -> None:
        self.alive = alive
        self.pane_content = pane_content

    def is_alive(self, pane_id: str) -> bool:
        del pane_id
        return self.alive

    def get_pane_content(self, pane_id: str, *, lines: int = 120) -> str:
        del pane_id, lines
        return self.pane_content


class _CodexSession:
    def __init__(self, *, work_dir: Path, root: Path, log_path: Path, session_id: str) -> None:
        self.work_dir = str(work_dir)
        self.codex_session_path = str(log_path)
        self.codex_session_id = session_id
        self.data = {
            "work_dir": str(work_dir),
            "codex_session_root": str(root),
            "codex_session_path": str(log_path),
            "codex_session_id": session_id,
        }


class _WritableCodexSession(_CodexSession):
    def __init__(
        self,
        *,
        work_dir: Path,
        root: Path,
        log_path: Path,
        session_id: str,
        runtime_dir: Path,
        session_file: Path,
    ) -> None:
        super().__init__(work_dir=work_dir, root=root, log_path=log_path, session_id=session_id)
        self.session_file = session_file
        self.data.update(
            {
                "agent_name": "agent1",
                "runtime_dir": str(runtime_dir),
                "start_cmd": "codex resume " + session_id,
                "codex_start_cmd": "codex resume " + session_id,
            }
        )

    def update_codex_log_binding(
        self,
        *,
        log_path: str | None,
        session_id: str | None,
        post_write_validate=None,
    ) -> bool:
        from provider_backends.codex.session_runtime.binding import update_codex_log_binding

        return update_codex_log_binding(
            self,
            log_path=log_path,
            session_id=session_id,
            post_write_validate=post_write_validate,
        )

    def _write_back(self) -> None:
        self.session_file.parent.mkdir(parents=True, exist_ok=True)
        self.session_file.write_text(json.dumps(self.data, indent=2) + "\n", encoding="utf-8")


def _codex_submission(
    *,
    reader: CodexLogReader,
    state: dict[str, object],
    work_dir: Path,
    backend: _Backend | None = None,
    delivery: bool = False,
) -> ProviderSubmission:
    runtime_state = {
        "mode": "active",
        "reader": reader,
        "state": state,
        "backend": backend or _Backend(),
        "pane_id": "%1",
        "request_anchor": "job_1",
        "next_seq": 1,
        "anchor_seen": False,
        "bound_turn_id": "",
        "bound_task_id": "",
        "reply_buffer": "",
        "last_agent_message": "",
        "last_final_answer": "",
        "last_assistant_message": "",
        "last_assistant_signature": "",
        "session_path": str(state.get("log_path") or ""),
        "workspace_path": str(work_dir),
        "no_wrap": False,
    }
    if delivery:
        runtime_state.update(
            {
                "delivery_state": "pending_anchor",
                "delivery_started_at": "2026-04-04T10:00:00Z",
                "delivery_timeout_s": 120.0,
                "delivery_target_pane_id": "%1",
                "delivery_target_session_path": str(state.get("log_path") or ""),
                "delivery_confirmed_at": "",
            }
        )
    return ProviderSubmission(
        job_id="job_1",
        agent_name="agent1",
        provider="codex",
        accepted_at="2026-04-04T10:00:00Z",
        ready_at="2026-04-04T10:00:00Z",
        source_kind=CompletionSourceKind.PROTOCOL_EVENT_STREAM,
        reply="",
        diagnostics={"provider": "codex", "mode": "active", "workspace_path": str(work_dir)},
        runtime_state=runtime_state,
    )


def _codex_log(root: Path, session_id: str, work_dir: Path, *, entries: tuple[dict[str, object], ...]) -> Path:
    path = root / "2026" / "04" / "04" / f"rollout-{session_id}.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps({"type": "session_meta", "payload": {"cwd": str(work_dir)}})]
    lines.extend(json.dumps(entry) for entry in entries)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _codex_user_entry(job_id: str, text: str) -> dict[str, object]:
    return {
        "timestamp": "2026-04-04T10:00:01.000Z",
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "user",
            "content": [{"type": "input_text", "text": f"CCB_REQ_ID: {job_id}\n\n{text}"}],
        },
    }


def _codex_assistant_entry(text: str) -> dict[str, object]:
    return {
        "timestamp": "2026-04-04T10:00:02.000Z",
        "type": "response_item",
        "payload": {
            "type": "message",
            "role": "assistant",
            "phase": "final_answer",
            "content": [{"type": "output_text", "text": text}],
        },
    }


def _codex_task_complete_entry(text: str) -> dict[str, object]:
    return {
        "timestamp": "2026-04-04T10:00:03.000Z",
        "type": "event_msg",
        "payload": {"type": "task_complete", "last_agent_message": text},
    }


def test_resolve_unique_codex_session_target_skips_ambiguous_instances(tmp_path: Path) -> None:
    from provider_backends.codex.comm import _resolve_unique_codex_session_target

    work_dir = tmp_path / "repo"
    config_dir = work_dir / ".ccb"
    config_dir.mkdir(parents=True)
    (config_dir / ".codex-auth-session").write_text("{}", encoding="utf-8")
    (config_dir / ".codex-payment-session").write_text("{}", encoding="utf-8")

    session_file, instance = _resolve_unique_codex_session_target(work_dir)

    assert session_file is None
    assert instance is None


def test_resolve_unique_codex_session_target_accepts_single_instance(tmp_path: Path) -> None:
    from provider_backends.codex.comm import _resolve_unique_codex_session_target

    work_dir = tmp_path / "repo"
    config_dir = work_dir / ".ccb"
    config_dir.mkdir(parents=True)
    target = config_dir / ".codex-auth-session"
    target.write_text("{}", encoding="utf-8")

    session_file, instance = _resolve_unique_codex_session_target(work_dir)

    assert session_file == target
    assert instance == "auth"


def test_resolve_unique_codex_session_target_filters_candidates_by_log_path(tmp_path: Path) -> None:
    from provider_backends.codex.comm import _resolve_unique_codex_session_target

    work_dir = tmp_path / "repo"
    config_dir = work_dir / ".ccb"
    config_dir.mkdir(parents=True)
    session_root_a = tmp_path / "agent-a" / "sessions"
    session_root_b = tmp_path / "agent-b" / "sessions"
    log_path = session_root_a / "2026" / "04" / "19" / "rollout-a-session.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("", encoding="utf-8")
    (config_dir / ".codex-auth-session").write_text(
        json.dumps({"codex_session_root": str(session_root_a)}),
        encoding="utf-8",
    )
    (config_dir / ".codex-payment-session").write_text(
        json.dumps({"codex_session_root": str(session_root_b)}),
        encoding="utf-8",
    )

    session_file, instance = _resolve_unique_codex_session_target(work_dir, log_path=log_path)

    assert session_file == config_dir / ".codex-auth-session"
    assert instance == "auth"
