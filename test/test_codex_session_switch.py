from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from provider_backends.codex.bridge_runtime.binding_runtime import CodexBindingTracker
from provider_backends.codex.session import CodexProjectSession
from provider_backends.codex.session_runtime.binding_runtime import SessionWriteError
from provider_backends.codex.session_switch import resolve_switch_decision


OLD_ID = "11111111-1111-1111-1111-111111111111"
NEW_ID = "22222222-2222-2222-2222-222222222222"
ALT_ID = "33333333-3333-3333-3333-333333333333"


def test_bridge_tracker_auto_rebinds_unique_managed_candidate(tmp_path: Path, monkeypatch) -> None:
    work_dir, session_file, runtime_dir, old_log = _project(tmp_path)
    new_log = _log(tmp_path, session_id=NEW_ID, work_dir=work_dir, mtime=200)

    monkeypatch.setenv("CCB_SESSION_FILE", str(session_file))
    tracker = CodexBindingTracker(runtime_dir)

    assert tracker.refresh_once() is True

    data = json.loads(session_file.read_text(encoding="utf-8"))
    assert data["codex_session_id"] == NEW_ID
    assert data["codex_session_path"] == str(new_log)
    assert data["old_codex_session_id"] == OLD_ID
    assert data["old_codex_session_path"] == str(old_log)
    assert data["start_cmd"].endswith(f"resume {NEW_ID}")

    switch = json.loads((runtime_dir / "session-switch.json").read_text(encoding="utf-8"))
    assert switch["state"] == "auto_rebound"
    assert switch["committed"] is True


def test_bridge_tracker_rejects_ambiguous_managed_candidates(tmp_path: Path, monkeypatch) -> None:
    work_dir, session_file, runtime_dir, old_log = _project(tmp_path)
    _log(tmp_path, session_id=NEW_ID, work_dir=work_dir, mtime=200)
    _log(tmp_path, session_id=ALT_ID, work_dir=work_dir, mtime=201)

    monkeypatch.setenv("CCB_SESSION_FILE", str(session_file))
    tracker = CodexBindingTracker(runtime_dir)

    assert tracker.refresh_once() is False

    data = json.loads(session_file.read_text(encoding="utf-8"))
    assert data["codex_session_id"] == OLD_ID
    assert data["codex_session_path"] == str(old_log)

    switch = json.loads((runtime_dir / "session-switch.json").read_text(encoding="utf-8"))
    assert switch["state"] == "switched_unbound"
    assert switch["reason"] == "ambiguous_session_candidates"
    assert switch["committed"] is False





def test_bridge_tracker_skips_repeated_bound_scan_until_files_change(tmp_path: Path, monkeypatch) -> None:
    _work_dir, session_file, runtime_dir, _old_log = _project(tmp_path)

    monkeypatch.setenv("CCB_SESSION_FILE", str(session_file))
    tracker = CodexBindingTracker(runtime_dir)

    assert tracker.refresh_once() is False

    def fail_resolve(*_args, **_kwargs):
        raise AssertionError("unchanged bound sessions should not be rescanned")

    monkeypatch.setattr("provider_backends.codex.bridge_runtime.binding_runtime.resolve_switch_decision", fail_resolve)

    assert tracker.refresh_once() is False

    _log(tmp_path, session_id=NEW_ID, work_dir=_work_dir, mtime=200)
    calls = []

    def record_resolve(*_args, **_kwargs):
        calls.append(True)
        from provider_backends.codex.session_switch.resolver import resolve_switch_decision

        return resolve_switch_decision(*_args, **_kwargs)

    monkeypatch.setattr("provider_backends.codex.bridge_runtime.binding_runtime.resolve_switch_decision", record_resolve)

    assert tracker.refresh_once() is True
    assert calls

def test_bridge_tracker_reuses_bound_log_without_workspace_rescan(tmp_path: Path, monkeypatch) -> None:
    _work_dir, session_file, runtime_dir, _old_log = _project(tmp_path)

    monkeypatch.setenv("CCB_SESSION_FILE", str(session_file))
    tracker = CodexBindingTracker(runtime_dir)

    def fail_current_log_path(*_args, **_kwargs):
        raise AssertionError("bound session should not rescan workspace logs")

    monkeypatch.setattr("provider_backends.codex.bridge_runtime.binding_runtime.current_log_path", fail_current_log_path)

    assert tracker.refresh_once() is False

def test_bridge_tracker_skips_repeated_ambiguous_scan_until_files_change(tmp_path: Path, monkeypatch) -> None:
    work_dir, session_file, runtime_dir, _old_log = _project(tmp_path)
    _log(tmp_path, session_id=NEW_ID, work_dir=work_dir, mtime=200)
    _log(tmp_path, session_id=ALT_ID, work_dir=work_dir, mtime=201)

    monkeypatch.setenv("CCB_SESSION_FILE", str(session_file))
    tracker = CodexBindingTracker(runtime_dir)

    assert tracker.refresh_once() is False

    def fail_resolve(*_args, **_kwargs):
        raise AssertionError("unchanged ambiguous sessions should not be rescanned")

    monkeypatch.setattr("provider_backends.codex.bridge_runtime.binding_runtime.resolve_switch_decision", fail_resolve)

    assert tracker.refresh_once() is False

    new_log = _log(tmp_path, session_id="44444444-4444-4444-4444-444444444444", work_dir=work_dir, mtime=300)
    calls = []

    def record_resolve(*_args, **_kwargs):
        calls.append(True)
        from provider_backends.codex.session_switch.resolver import resolve_switch_decision

        return resolve_switch_decision(*_args, **_kwargs)

    monkeypatch.setattr("provider_backends.codex.bridge_runtime.binding_runtime.resolve_switch_decision", record_resolve)

    assert tracker.refresh_once() is False
    assert calls
    assert new_log.exists()

def test_bridge_tracker_requires_running_job_anchor_before_rebind(tmp_path: Path, monkeypatch) -> None:
    work_dir, session_file, runtime_dir, old_log = _project(tmp_path)
    jobs_path = tmp_path / "repo" / ".ccb" / "agents" / "agent1" / "jobs.jsonl"
    jobs_path.parent.mkdir(parents=True, exist_ok=True)
    jobs_path.write_text(
        json.dumps({"schema_version": 2, "record_type": "job_record", "job_id": "job_live", "status": "running"})
        + "\n",
        encoding="utf-8",
    )
    new_log = _log(tmp_path, session_id=NEW_ID, work_dir=work_dir, mtime=200)

    monkeypatch.setenv("CCB_SESSION_FILE", str(session_file))
    tracker = CodexBindingTracker(runtime_dir)

    assert tracker.refresh_once() is False
    data = json.loads(session_file.read_text(encoding="utf-8"))
    assert data["codex_session_id"] == OLD_ID
    switch = json.loads((runtime_dir / "session-switch.json").read_text(encoding="utf-8"))
    assert switch["reason"] == "running_job_anchor_not_seen"

    with new_log.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "message", "payload": {"text": "CCB_REQ_ID: job_live"}}) + "\n")
    os.utime(new_log, (300, 300))

    assert tracker.refresh_once() is True
    data = json.loads(session_file.read_text(encoding="utf-8"))
    assert data["codex_session_id"] == NEW_ID
    assert data["codex_session_path"] == str(new_log)
    assert data["old_codex_session_path"] == str(old_log)


def test_bridge_tracker_rebinds_exact_running_anchor_despite_changed_cwd(tmp_path: Path, monkeypatch) -> None:
    work_dir, session_file, runtime_dir, old_log = _project(tmp_path)
    _running_job(tmp_path, "job_live")
    fresh_workspace = tmp_path / "groups" / "talk2_workers"
    new_log = _log(tmp_path, session_id=NEW_ID, work_dir=fresh_workspace, mtime=200)
    _append_anchor(new_log, "job_live")
    os.utime(new_log, (300, 300))

    monkeypatch.setenv("CCB_SESSION_FILE", str(session_file))

    assert CodexBindingTracker(runtime_dir).refresh_once() is True
    data = json.loads(session_file.read_text(encoding="utf-8"))
    assert data["codex_session_path"] == str(new_log)
    assert data["codex_session_id"] == NEW_ID
    assert data["old_codex_session_path"] == str(old_log)


def test_running_anchor_rejects_partial_duplicate_and_subagent_logs(tmp_path: Path) -> None:
    work_dir, session_file, runtime_dir, _old_log = _project(tmp_path)
    _running_job(tmp_path, "job_live")
    partial = _log(tmp_path, session_id=NEW_ID, work_dir=tmp_path / "fresh", mtime=200)
    _append_anchor(partial, "job_live_extra")
    assert resolve_switch_decision(
        json.loads(session_file.read_text(encoding="utf-8")), session_file=session_file, runtime_dir=runtime_dir
    ).state == "bound"

    _append_anchor(partial, "job_live")
    duplicate = _log(tmp_path, session_id=ALT_ID, work_dir=tmp_path / "fresh", mtime=201)
    _append_anchor(duplicate, "job_live")
    assert resolve_switch_decision(
        json.loads(session_file.read_text(encoding="utf-8")), session_file=session_file, runtime_dir=runtime_dir
    ).state == "switched_unbound"

    partial.unlink()
    _mark_subagent(duplicate)
    assert resolve_switch_decision(
        json.loads(session_file.read_text(encoding="utf-8")), session_file=session_file, runtime_dir=runtime_dir
    ).state == "bound"


def test_running_anchor_rejects_log_officially_bound_to_another_agent(tmp_path: Path) -> None:
    work_dir, session_file, runtime_dir, _old_log = _project(tmp_path)
    _running_job(tmp_path, "job_live")
    candidate = _log(tmp_path, session_id=NEW_ID, work_dir=tmp_path / "fresh", mtime=200)
    _append_anchor(candidate, "job_live")
    (session_file.parent / ".codex-agent2-session").write_text(
        json.dumps({"codex_session_path": str(candidate), "codex_session_id": NEW_ID}) + "\n",
        encoding="utf-8",
    )

    decision = resolve_switch_decision(
        json.loads(session_file.read_text(encoding="utf-8")), session_file=session_file, runtime_dir=runtime_dir
    )

    assert decision.state == "bound"
    assert decision.candidate is None


def test_two_agents_rebind_only_inside_their_own_managed_roots(tmp_path: Path, monkeypatch) -> None:
    first_work, first_session, first_runtime, _ = _project(tmp_path / "one")
    second_work, second_session, second_runtime, _ = _project(tmp_path / "two")
    _running_job(tmp_path / "one", "job_one")
    _running_job(tmp_path / "two", "job_two")
    first_log = _log(tmp_path / "one", session_id=NEW_ID, work_dir=tmp_path / "new-one", mtime=200)
    second_log = _log(tmp_path / "two", session_id=ALT_ID, work_dir=tmp_path / "new-two", mtime=200)
    _append_anchor(first_log, "job_one")
    _append_anchor(second_log, "job_two")
    os.utime(first_log, (300, 300))
    os.utime(second_log, (300, 300))

    monkeypatch.setenv("CCB_SESSION_FILE", str(first_session))
    assert CodexBindingTracker(first_runtime).refresh_once() is True
    monkeypatch.setenv("CCB_SESSION_FILE", str(second_session))
    assert CodexBindingTracker(second_runtime).refresh_once() is True

    assert json.loads(first_session.read_text(encoding="utf-8"))["codex_session_path"] == str(first_log)
    assert json.loads(second_session.read_text(encoding="utf-8"))["codex_session_path"] == str(second_log)
    assert str(first_log.parent.parent.parent) not in str(second_log)


def test_codex_project_session_false_write_keeps_old_binding_and_skips_transfer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work_dir, session_file, _runtime_dir, _old_log = _project(tmp_path)
    new_log = _log(tmp_path, session_id=NEW_ID, work_dir=work_dir, mtime=200)
    original = json.loads(session_file.read_text(encoding="utf-8"))
    transfers: list[dict[str, object]] = []

    monkeypatch.setattr(
        "provider_backends.codex.session_runtime.binding_runtime.safe_write_session",
        lambda path, payload: (False, "write_failed"),
    )
    monkeypatch.setattr("memory.transfer_runtime.maybe_auto_transfer", lambda **kwargs: transfers.append(kwargs))

    session = CodexProjectSession(session_file=session_file, data=dict(original))

    with pytest.raises(SessionWriteError):
        session.update_codex_log_binding(log_path=str(new_log), session_id=NEW_ID)

    assert session.data == original
    assert json.loads(session_file.read_text(encoding="utf-8")) == original
    assert transfers == []


def test_codex_project_session_persists_before_transfer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work_dir, session_file, _runtime_dir, old_log = _project(tmp_path)
    new_log = _log(tmp_path, session_id=NEW_ID, work_dir=work_dir, mtime=200)
    order: list[str] = []

    def write_session(path, payload):
        order.append("write")
        Path(path).write_text(payload, encoding="utf-8")
        return True, None

    def transfer(**kwargs):
        order.append("transfer")
        data = json.loads(session_file.read_text(encoding="utf-8"))
        assert data["codex_session_id"] == NEW_ID
        assert data["codex_session_path"] == str(new_log)
        assert kwargs["session_id"] == OLD_ID

    monkeypatch.setattr("provider_backends.codex.session_runtime.binding_runtime.safe_write_session", write_session)
    monkeypatch.setattr("memory.transfer_runtime.maybe_auto_transfer", transfer)

    session = CodexProjectSession(
        session_file=session_file,
        data=json.loads(session_file.read_text(encoding="utf-8")),
    )

    assert session.update_codex_log_binding(log_path=str(new_log), session_id=NEW_ID) is True

    assert order == ["write", "transfer"]
    assert session.data["codex_session_id"] == NEW_ID
    assert json.loads(session_file.read_text(encoding="utf-8"))["old_codex_session_path"] == str(old_log)


def test_codex_project_session_rejects_stale_concurrent_binding_writer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    work_dir, session_file, _runtime_dir, _old_log = _project(tmp_path)
    first_log = _log(tmp_path, session_id=NEW_ID, work_dir=work_dir, mtime=200)
    second_id = "33333333-3333-3333-3333-333333333333"
    second_log = _log(tmp_path, session_id=second_id, work_dir=work_dir, mtime=300)
    original = json.loads(session_file.read_text(encoding="utf-8"))
    first = CodexProjectSession(session_file=session_file, data=dict(original))
    stale = CodexProjectSession(session_file=session_file, data=dict(original))
    monkeypatch.setattr("memory.transfer_runtime.maybe_auto_transfer", lambda **kwargs: None)

    assert first.update_codex_log_binding(log_path=str(first_log), session_id=NEW_ID) is True
    with pytest.raises(SessionWriteError, match="changed concurrently"):
        stale.update_codex_log_binding(log_path=str(second_log), session_id=second_id)

    persisted = json.loads(session_file.read_text(encoding="utf-8"))
    assert persisted["codex_session_id"] == NEW_ID
    assert persisted["codex_session_path"] == str(first_log)
    assert stale.data == original


def _project(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    work_dir = tmp_path / "repo"
    ccb_dir = work_dir / ".ccb"
    runtime_dir = ccb_dir / "agents" / "agent1" / "provider-runtime" / "codex"
    codex_home = ccb_dir / "agents" / "agent1" / "provider-state" / "codex" / "home"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    (codex_home / "sessions").mkdir(parents=True, exist_ok=True)
    old_log = _log(tmp_path, session_id=OLD_ID, work_dir=work_dir, mtime=100)
    session_file = ccb_dir / ".codex-agent1-session"
    session_file.write_text(
        json.dumps(
            {
                "active": True,
                "agent_name": "agent1",
                "work_dir": str(work_dir),
                "runtime_dir": str(runtime_dir),
                "codex_home": str(codex_home),
                "codex_session_path": str(old_log),
                "codex_session_id": OLD_ID,
                "codex_provider_authority_fingerprint": "fp-1",
                "codex_session_authority_fingerprint": "fp-1",
                "start_cmd": "codex resume " + OLD_ID,
                "codex_start_cmd": "codex resume " + OLD_ID,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return work_dir, session_file, runtime_dir, old_log


def _log(tmp_path: Path, *, session_id: str, work_dir: Path, mtime: int) -> Path:
    path = (
        tmp_path
        / "repo"
        / ".ccb"
        / "agents"
        / "agent1"
        / "provider-state"
        / "codex"
        / "home"
        / "sessions"
        / "2026"
        / "04"
        / "29"
        / f"rollout-{session_id}.jsonl"
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"type": "session_meta", "payload": {"cwd": str(work_dir)}}) + "\n",
        encoding="utf-8",
    )
    os.utime(path, (mtime, mtime))
    return path


def _running_job(tmp_path: Path, job_id: str) -> None:
    jobs_path = tmp_path / "repo" / ".ccb" / "agents" / "agent1" / "jobs.jsonl"
    jobs_path.parent.mkdir(parents=True, exist_ok=True)
    jobs_path.write_text(json.dumps({"job_id": job_id, "status": "running"}) + "\n", encoding="utf-8")


def _append_anchor(path: Path, job_id: str) -> None:
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps({"type": "message", "payload": {"text": f"CCB_REQ_ID: {job_id}"}}) + "\n")


def _mark_subagent(path: Path) -> None:
    lines = path.read_text(encoding="utf-8").splitlines()
    lines[0] = json.dumps({"type": "session_meta", "payload": {"thread_source": "subagent"}})
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
