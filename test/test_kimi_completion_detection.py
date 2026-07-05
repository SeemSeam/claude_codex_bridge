from __future__ import annotations

from pathlib import Path

import pytest

from ccbd.api_models import DeliveryScope, JobRecord, JobStatus, MessageEnvelope
from completion.models import CompletionItemKind, CompletionSourceKind, CompletionStatus
from provider_backends.kimi.execution import KimiProviderAdapter
from provider_backends.kimi.native_log import KimiTurnObservation
from provider_execution.base import ProviderSubmission


class _Backend:
    def __init__(self, text: str = "") -> None:
        self._text = text
        self.sent: list[str] = []

    def get_pane_content(self, pane_id: str, *, lines: int) -> str:
        del pane_id, lines
        return self._text

    def send_text(self, pane_id: str, text: str) -> None:
        del pane_id
        self.sent.append(text)

    def send_keys(self, pane_id: str, *keys: str) -> None:
        del pane_id
        self.sent.extend(keys)


def _job(workspace_path: Path, body: str = "do work") -> JobRecord:
    return JobRecord(
        job_id="job_kimi_completion",
        submission_id=None,
        agent_name="kimi1",
        provider="kimi",
        request=MessageEnvelope(
            project_id="proj",
            to_agent="kimi1",
            from_actor="user",
            body=body,
            task_id=None,
            reply_to=None,
            message_type="ask",
            delivery_scope=DeliveryScope.SINGLE,
        ),
        status=JobStatus.RUNNING,
        terminal_decision=None,
        cancel_requested_at=None,
        workspace_path=str(workspace_path),
        created_at="2026-06-13T00:00:00Z",
        updated_at="2026-06-13T00:00:00Z",
    )


def _submission(work_dir: Path, *, extra_state: dict[str, object] | None = None) -> ProviderSubmission:
    return ProviderSubmission(
        job_id="job_kimi_completion",
        agent_name="kimi1",
        provider="kimi",
        accepted_at="2026-06-13T00:00:00Z",
        ready_at="2026-06-13T00:00:00Z",
        source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
        reply="",
        runtime_state={
            "mode": "native_turn_log",
            "backend": _Backend(),
            "pane_id": "%9",
            "req_id": "job_kimi_completion",
            "request_anchor": "job_kimi_completion",
            "work_dir": str(work_dir),
            "started_at": "2026-06-13T00:00:00Z",
            "last_poll_at": "2026-06-13T00:00:00Z",
            "prompt_sent": True,
            "next_seq": 1,
            "anchor_emitted": False,
            "reply_buffer": "",
            "last_reply_signature": "",
            "turn_boundary_ref": "",
            "session_path": "",
            "hindsight_user_prompt": "do work",
            **(extra_state or {}),
        },
    )


def _native_observation(*, completed: bool, reply: str) -> KimiTurnObservation:
    return KimiTurnObservation(
        request_seen=True,
        completed=completed,
        reply=reply,
        session_id="session-1",
        session_path="session-path",
        provider_turn_ref="turn-1",
        line_count=4,
        native_started_at="2026-06-13T00:00:01Z",
        native_completed_at="2026-06-13T00:00:10Z",
    )


def _pane_observation(*, completed: bool, reply: str) -> KimiTurnObservation:
    return KimiTurnObservation(
        request_seen=True,
        completed=completed,
        reply=reply,
        session_id="pane-session",
        session_path="pane:session",
        provider_turn_ref="pane:turn-1",
        line_count=20,
        native_started_at=None,
        native_completed_at=None,
    )


def _boundary_item(result):
    for item in result.items:
        if item.kind is CompletionItemKind.TURN_BOUNDARY:
            return item
    return None


def test_kimi_first_turn_end_does_not_complete_while_pane_still_working(monkeypatch, tmp_path: Path) -> None:
    work_dir = tmp_path / "project"
    work_dir.mkdir()

    monkeypatch.setattr(
        "provider_backends.kimi.execution.observe_kimi_turn",
        lambda *args, **kwargs: _native_observation(completed=True, reply="Need explore... Let's read..."),
    )
    monkeypatch.setattr(
        "provider_backends.kimi.execution._observe_kimi_pane_turn",
        lambda *args, **kwargs: _pane_observation(completed=False, reply=""),
    )

    result = KimiProviderAdapter().poll(_submission(work_dir), now="2026-06-13T00:00:10Z")

    assert result is not None
    assert result.decision is None
    assert any(item.kind is CompletionItemKind.ASSISTANT_FINAL for item in result.items)
    assert _boundary_item(result) is None


def test_kimi_interim_reply_marked_partial_when_pane_not_idle(monkeypatch, tmp_path: Path) -> None:
    work_dir = tmp_path / "project"
    work_dir.mkdir()

    monkeypatch.setattr(
        "provider_backends.kimi.execution.observe_kimi_turn",
        lambda *args, **kwargs: _native_observation(completed=True, reply="Need explore... Let's read..."),
    )
    monkeypatch.setattr(
        "provider_backends.kimi.execution._observe_kimi_pane_turn",
        lambda *args, **kwargs: _pane_observation(completed=False, reply=""),
    )

    result = KimiProviderAdapter().poll(_submission(work_dir), now="2026-06-13T00:00:10Z")

    final_items = [item for item in result.items if item.kind is CompletionItemKind.ASSISTANT_FINAL]
    assert len(final_items) == 1
    assert final_items[0].payload.get("interim") is True
    assert final_items[0].payload.get("pane_completed") is False


def test_kimi_completes_only_when_pane_idle_and_stable(monkeypatch, tmp_path: Path) -> None:
    work_dir = tmp_path / "project"
    work_dir.mkdir()

    monkeypatch.setattr(
        "provider_backends.kimi.execution.observe_kimi_turn",
        lambda *args, **kwargs: _native_observation(completed=True, reply="Done."),
    )
    pane_obs = _pane_observation(completed=True, reply="Done.")
    monkeypatch.setattr(
        "provider_backends.kimi.execution._observe_kimi_pane_turn",
        lambda *args, **kwargs: pane_obs,
    )

    sub = _submission(work_dir)
    first = KimiProviderAdapter().poll(sub, now="2026-06-13T00:00:10Z")
    # First poll stabilizes the pane observation; boundary not yet emitted.
    assert first is not None
    assert _boundary_item(first) is None

    # 20s of stability is BELOW the PANE_FALLBACK_STABLE_SECS window (45s), so
    # the pane fallback must still withhold completion.
    second = KimiProviderAdapter().poll(first.submission, now="2026-06-13T00:00:30Z")
    assert _boundary_item(second) is None

    # At 46s of stable pane observation the threshold (45s) is exceeded and the
    # pane-idle TURN_BOUNDARY is emitted.
    third = KimiProviderAdapter().poll(second.submission, now="2026-06-13T00:00:56Z")
    boundary = _boundary_item(third)
    assert boundary is not None
    assert boundary.payload.get("reason") == "kimi_pane_idle_complete"


def test_kimi_timeout_while_pane_still_working_reports_low_confidence(monkeypatch, tmp_path: Path) -> None:
    work_dir = tmp_path / "project"
    work_dir.mkdir()

    monkeypatch.setattr(
        "provider_backends.kimi.execution.observe_kimi_turn",
        lambda *args, **kwargs: _native_observation(completed=True, reply="partial"),
    )
    monkeypatch.setattr(
        "provider_backends.kimi.execution._observe_kimi_pane_turn",
        lambda *args, **kwargs: _pane_observation(completed=False, reply=""),
    )
    monkeypatch.setenv("CCB_KIMI_NATIVE_TURN_TIMEOUT_S", "1")

    result = KimiProviderAdapter().poll(_submission(work_dir), now="2026-06-13T00:05:00Z")

    assert result is not None
    assert result.decision is not None
    assert result.decision.status is CompletionStatus.FAILED
    assert result.decision.reason == "kimi_native_turn_timeout"
    assert result.decision.diagnostics.get("pane_still_working") is True
    assert result.decision.diagnostics.get("reply_confidence") == "low"
    assert result.decision.confidence.value == "degraded"


def test_kimi_native_completed_no_pane_observation_falls_back_to_native(monkeypatch, tmp_path: Path) -> None:
    """When pane evidence is unavailable, native TurnEnd remains authoritative."""
    work_dir = tmp_path / "project"
    work_dir.mkdir()

    monkeypatch.setattr(
        "provider_backends.kimi.execution.observe_kimi_turn",
        lambda *args, **kwargs: _native_observation(completed=True, reply="Done."),
    )
    monkeypatch.setattr(
        "provider_backends.kimi.execution._observe_kimi_pane_turn",
        lambda *args, **kwargs: None,
    )

    result = KimiProviderAdapter().poll(_submission(work_dir), now="2026-06-13T00:00:10Z")

    assert _boundary_item(result) is not None
    assert result.decision is None
