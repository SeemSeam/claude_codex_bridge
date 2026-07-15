from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from completion.models import CompletionSourceKind, CompletionStatus
from provider_backends.codex.comm import CodexLogReader
from provider_execution.active import PreparedActiveStart
from provider_execution.base import ProviderSubmission


def test_codex_reply_delivery_forces_anchor_wrapping_and_waits_for_acceptance(monkeypatch, tmp_path: Path) -> None:
    from provider_backends.codex.execution_runtime import start as start_module

    sent: list[tuple[str, str]] = []
    backend = SimpleNamespace(send_text_to_pane=lambda pane_id, text: sent.append((pane_id, text)))
    session = SimpleNamespace()
    reader = SimpleNamespace(capture_state=lambda: {"log_path": tmp_path / "old.jsonl", "offset": 0})
    prepared = PreparedActiveStart(work_dir=tmp_path, session=session, pane_id="%7", backend=backend)
    monkeypatch.setattr(start_module, "prepare_active_start", lambda *args, **kwargs: prepared)
    monkeypatch.setattr(start_module, "wait_for_runtime_ready", lambda *args, **kwargs: True)
    monkeypatch.setattr(start_module, "no_wrap_requested", lambda job: True)

    job = SimpleNamespace(
        job_id="job_reply",
        agent_name="talk2",
        request=SimpleNamespace(body="CCB_REPLY payload", message_type="reply_delivery"),
    )
    submission = start_module.start_active_submission(
        SimpleNamespace(provider="codex"),
        job,
        context=None,
        now="2026-07-15T00:00:00Z",
        load_session_fn=lambda *args, **kwargs: None,
        backend_for_session_fn=lambda data: None,
        reader_factory=lambda current_session, preferred: reader,
        request_anchor_fn=lambda job_id: job_id,
        wrap_prompt_fn=lambda body, anchor: f"CCB_REQ_ID: {anchor}\n\n{body}",
    )

    assert sent == [("%7", "CCB_REQ_ID: job_reply\n\nCCB_REPLY payload")]
    assert submission.runtime_state["no_wrap"] is False
    assert submission.runtime_state["anchor_seen"] is False
    assert submission.runtime_state["delivery_state"] == "pending_anchor"
    assert submission.runtime_state["reply_delivery_complete_on_dispatch"] is True


def test_codex_reply_delivery_completes_only_after_request_anchor_is_observed(monkeypatch, tmp_path: Path) -> None:
    from provider_execution import codex as codex_module

    work_dir = tmp_path / "repo"
    work_dir.mkdir()
    root = tmp_path / "sessions"
    session_id = "22222222-2222-2222-2222-222222222222"
    log_path = root / "2026" / "07" / "15" / f"rollout-{session_id}.jsonl"
    log_path.parent.mkdir(parents=True)
    log_path.write_text(
        "\n".join(
            (
                json.dumps({"type": "session_meta", "payload": {"cwd": str(work_dir)}}),
                json.dumps(
                    {
                        "timestamp": "2026-07-15T00:00:01Z",
                        "type": "response_item",
                        "payload": {
                            "type": "message",
                            "role": "user",
                            "content": [{"type": "input_text", "text": "CCB_REQ_ID: job_reply\n\npayload"}],
                        },
                    }
                ),
            )
        )
        + "\n",
        encoding="utf-8",
    )
    session = SimpleNamespace(
        work_dir=str(work_dir),
        codex_session_path=str(log_path),
        codex_session_id=session_id,
        data={
            "work_dir": str(work_dir),
            "codex_session_root": str(root),
            "codex_session_path": str(log_path),
            "codex_session_id": session_id,
        },
    )
    monkeypatch.setattr(codex_module, "_load_session", lambda *args, **kwargs: session)
    reader = CodexLogReader(root=root, log_path=log_path, session_id_filter=session_id, work_dir=work_dir)
    submission = ProviderSubmission(
        job_id="job_reply",
        agent_name="talk2",
        provider="codex",
        accepted_at="2026-07-15T00:00:00Z",
        ready_at="2026-07-15T00:00:00Z",
        source_kind=CompletionSourceKind.PROTOCOL_EVENT_STREAM,
        reply="",
        diagnostics={"provider": "codex", "mode": "active", "workspace_path": str(work_dir)},
        runtime_state={
            "mode": "active",
            "reader": reader,
            "state": {"log_path": log_path, "offset": 0},
            "backend": SimpleNamespace(is_alive=lambda pane_id: True),
            "pane_id": "%7",
            "request_anchor": "job_reply",
            "next_seq": 1,
            "anchor_seen": False,
            "no_wrap": False,
            "reply_buffer": "",
            "session_path": str(log_path),
            "workspace_path": str(work_dir),
            "delivery_state": "pending_anchor",
            "delivery_started_at": "2026-07-15T00:00:00Z",
            "delivery_last_progress_at": "2026-07-15T00:00:00Z",
            "delivery_timeout_s": 120.0,
            "delivery_target_pane_id": "%7",
            "delivery_target_session_path": str(log_path),
            "reply_delivery_complete_on_dispatch": True,
        },
    )

    result = codex_module.CodexProviderAdapter().poll(submission, now="2026-07-15T00:00:02Z")

    assert result is not None
    assert result.decision is not None
    assert result.decision.status is CompletionStatus.COMPLETED
    assert result.decision.reason == "reply_delivery_sent"
    assert result.decision.anchor_seen is True
    assert result.decision.diagnostics["delivery_status"] == "accepted"
    assert result.submission.runtime_state["delivery_state"] == "accepted"
