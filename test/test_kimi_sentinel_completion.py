"""Acceptance tests for kimi CCB_DONE sentinel completion detection.

T1-T5 cover the authoritative sentinel path (kimi ends its final reply with
`CCB_DONE:<anchor>`) introduced alongside the existing pane-idle fallback.
These mirror the droid sentinel contract: the marker is authoritative terminal
completion, the persisted reply is the text BEFORE the marker (never the
marker itself), and a stray marker with the wrong anchor never matches.
"""

from __future__ import annotations

from pathlib import Path

from ccbd.api_models import DeliveryScope, JobRecord, JobStatus, MessageEnvelope
from completion.models import CompletionItemKind, CompletionSourceKind, CompletionStatus
from provider_backends.kimi.execution import KimiProviderAdapter, wrap_kimi_prompt
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
        job_id="job_kimi_sentinel",
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


_REQ_ID = "job_kimi_sentinel"


def _submission(
    work_dir: Path,
    *,
    pane_text: str = "",
    extra_state: dict[str, object] | None = None,
) -> ProviderSubmission:
    return ProviderSubmission(
        job_id=_REQ_ID,
        agent_name="kimi1",
        provider="kimi",
        accepted_at="2026-06-13T00:00:00Z",
        ready_at="2026-06-13T00:00:00Z",
        source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
        reply="",
        runtime_state={
            "mode": "native_turn_log",
            "backend": _Backend(pane_text),
            "pane_id": "%9",
            "req_id": _REQ_ID,
            "request_anchor": _REQ_ID,
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


def _final_item(result):
    for item in result.items:
        if item.kind is CompletionItemKind.ASSISTANT_FINAL:
            return item
    return None


# --------------------------------------------------------------------------- #
# T1: sentinel hit -> authoritative terminal completion, fast (no 45s wait).
# --------------------------------------------------------------------------- #
def test_t1_sentinel_hit_completes_fast_with_done_marker(monkeypatch, tmp_path: Path) -> None:
    work_dir = tmp_path / "project"
    work_dir.mkdir()

    real_answer = "The fix is applied to src/auth.py."
    sentinel_reply = f"{real_answer}\nCCB_DONE:{_REQ_ID}"

    monkeypatch.setattr(
        "provider_backends.kimi.execution.observe_kimi_turn",
        lambda *args, **kwargs: _native_observation(completed=True, reply=sentinel_reply),
    )
    # Pane still "working" (not idle-stable). Without the sentinel this would
    # withhold completion. The sentinel must win and complete immediately.
    monkeypatch.setattr(
        "provider_backends.kimi.execution._observe_kimi_pane_turn",
        lambda *args, **kwargs: _pane_observation(completed=False, reply=""),
    )

    result = KimiProviderAdapter().poll(_submission(work_dir), now="2026-06-13T00:00:10Z")

    assert result is not None
    boundary = _boundary_item(result)
    assert boundary is not None, "sentinel hit must emit a TURN_BOUNDARY"
    assert boundary.payload.get("reason") == "kimi_sentinel_complete"
    assert boundary.payload.get("done_marker") is True
    assert boundary.payload.get("ccb_done") is True
    assert boundary.payload.get("completion_source") == "sentinel"
    # Persisted reply is the text BEFORE the marker only.
    assert result.submission.reply == real_answer
    assert "CCB_DONE" not in result.submission.reply
    final = _final_item(result)
    assert final is not None
    assert final.payload.get("done_marker") is True


# --------------------------------------------------------------------------- #
# T2: no sentinel, pane idle-stable past PANE_FALLBACK_STABLE_SECS -> fallback
#     completion, reason stays kimi_pane_idle_complete.
# --------------------------------------------------------------------------- #
def test_t2_no_sentinel_idle_stable_uses_fallback_reason(monkeypatch, tmp_path: Path) -> None:
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
    assert _boundary_item(first) is None  # stabilizing

    second = KimiProviderAdapter().poll(first.submission, now="2026-06-13T00:00:30Z")
    assert _boundary_item(second) is None  # still below 45s threshold

    third = KimiProviderAdapter().poll(second.submission, now="2026-06-13T00:00:56Z")
    boundary = _boundary_item(third)
    assert boundary is not None
    # No sentinel in the reply -> fallback reason must be used.
    assert boundary.payload.get("reason") == "kimi_pane_idle_complete"
    assert boundary.payload.get("done_marker") is False
    assert boundary.payload.get("completion_source") == "pane_idle_fallback"


# --------------------------------------------------------------------------- #
# T3: no sentinel, pane never idle-stable, output keeps changing -> not
#     completed until bounded by native_turn_timeout.
# --------------------------------------------------------------------------- #
def test_t3_no_sentinel_no_idle_eventually_times_out(monkeypatch, tmp_path: Path) -> None:
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
    # No premature close: no TURN_BOUNDARY emitted.
    assert _boundary_item(result) is None


# --------------------------------------------------------------------------- #
# T4: a CCB_DONE line with a DIFFERENT anchor must NOT match.
# --------------------------------------------------------------------------- #
def test_t4_wrong_anchor_sentinel_does_not_complete(monkeypatch, tmp_path: Path) -> None:
    work_dir = tmp_path / "project"
    work_dir.mkdir()

    # Stray marker for a different job id.
    stray_reply = f"working...\nCCB_DONE:job_other_agent"

    monkeypatch.setattr(
        "provider_backends.kimi.execution.observe_kimi_turn",
        lambda *args, **kwargs: _native_observation(completed=True, reply=stray_reply),
    )
    monkeypatch.setattr(
        "provider_backends.kimi.execution._observe_kimi_pane_turn",
        lambda *args, **kwargs: _pane_observation(completed=False, reply=""),
    )

    result = KimiProviderAdapter().poll(_submission(work_dir), now="2026-06-13T00:00:10Z")

    assert result is not None
    # Wrong-anchor marker must not fire authoritative completion.
    assert _boundary_item(result) is None
    assert result.decision is None


# --------------------------------------------------------------------------- #
# T5: marker is the terminal line, optionally followed only by blank lines
#     (which is_trailing_noise_line treats as noise) -> reply = content before
#     the marker only. This mirrors the shared droid is_done_text contract: the
#     CCB_DONE line must be the last non-noise line of the reply.
# --------------------------------------------------------------------------- #
def test_t5_sentinel_terminal_reply_is_before_marker(monkeypatch, tmp_path: Path) -> None:
    work_dir = tmp_path / "project"
    work_dir.mkdir()

    before_marker = "Implementation Receipt\n- changed src/a.py\n- ran tests"
    # Realistic kimi reply: marker is the final non-blank line; trailing blank
    # lines are noise and are skipped by is_done_text / strip_done_text.
    reply_with_blank_tail = f"{before_marker}\nCCB_DONE:{_REQ_ID}\n\n"

    monkeypatch.setattr(
        "provider_backends.kimi.execution.observe_kimi_turn",
        lambda *args, **kwargs: _native_observation(completed=True, reply=reply_with_blank_tail),
    )
    monkeypatch.setattr(
        "provider_backends.kimi.execution._observe_kimi_pane_turn",
        lambda *args, **kwargs: _pane_observation(completed=False, reply=""),
    )

    result = KimiProviderAdapter().poll(_submission(work_dir), now="2026-06-13T00:00:10Z")

    assert result is not None
    boundary = _boundary_item(result)
    assert boundary is not None
    assert boundary.payload.get("reason") == "kimi_sentinel_complete"
    # Reply is strictly the content before the marker; trailing blanks dropped.
    assert result.submission.reply == before_marker
    assert "CCB_DONE" not in result.submission.reply


def test_t5b_marker_followed_by_real_text_is_not_done(monkeypatch, tmp_path: Path) -> None:
    """A marker with real (non-noise) text after it is NOT terminal.

    This matches the shared droid contract (is_done_text): the CCB_DONE line
    must be the last non-noise line. Genuine trailing content means kimi kept
    talking after the marker, so we must not close. This prevents a malformed
    mid-reply marker from truncating a still-running answer.
    """
    work_dir = tmp_path / "project"
    work_dir.mkdir()

    before_marker = "partial answer"
    reply_with_real_text = f"{before_marker}\nCCB_DONE:{_REQ_ID}\nactually more to say"

    monkeypatch.setattr(
        "provider_backends.kimi.execution.observe_kimi_turn",
        lambda *args, **kwargs: _native_observation(completed=True, reply=reply_with_real_text),
    )
    monkeypatch.setattr(
        "provider_backends.kimi.execution._observe_kimi_pane_turn",
        lambda *args, **kwargs: _pane_observation(completed=False, reply=""),
    )

    result = KimiProviderAdapter().poll(_submission(work_dir), now="2026-06-13T00:00:10Z")

    assert result is not None
    # Non-noise text after the marker => not terminal => no boundary.
    assert _boundary_item(result) is None
    assert result.decision is None


# --------------------------------------------------------------------------- #
# Bonus: the prompt wrapper itself must carry both the request anchor and the
# sentinel instruction line.
# --------------------------------------------------------------------------- #
def test_wrap_kimi_prompt_includes_anchor_and_sentinel_instruction() -> None:
    prompt = wrap_kimi_prompt("do work", _REQ_ID)
    assert "CCB_REQ_ID: job_kimi_sentinel" in prompt
    assert "CCB_DONE: job_kimi_sentinel" in prompt
    assert "IMPORTANT COMPLETION SIGNAL" in prompt


def test_wrap_kimi_prompt_does_not_mutate_shared_native_wrapper() -> None:
    # The shared wrapper must remain marker-free (test_native_cli_completion
    # guards this); wrap_kimi_prompt layers the sentinel on top only.
    from provider_backends.native_cli_support import wrap_native_prompt

    assert "CCB_DONE" not in wrap_native_prompt("answer", _REQ_ID)


# =========================================================================== #
# REGRESSION R1-R5: prompt-echo isolation (mirrors the REAL prod pane).
#
# The real kimi pane ALWAYS contains the marker literal in the PROMPT region,
# because ``wrap_kimi_prompt`` shows kimi the format with an inline example
# ``CCB_DONE: <anchor>`` line.  The pane also always shows the idle input box
# (``╭``/``│ >``/``╰``) BELOW the assistant reply.  The original sentinel
# buffer joined the RAW pane snapshot, so (a) ``is_done_text`` matched the
# input-box line (the last non-noise line) instead of the emitted marker, and
# (b) the prompt's example marker risked a false fire.  These tests use the
# real pane shape to lock the prompt/reply separation.
# =========================================================================== #


def _real_prompt_block(req_id: str) -> str:
    """Mirror the prompt region ``wrap_kimi_prompt`` produces (echoed in pane)."""
    return (
        f"CCB_REQ_ID: {req_id}\n"
        "\n"
        "do work\n"
        "\n"
        "IMPORTANT COMPLETION SIGNAL:\n"
        "- When the requested task is FULLY complete, end your final reply with "
        "this exact final line on its own line, verbatim, with nothing else on "
        "that line and nothing after it:\n"
        f"   CCB_DONE: {req_id}\n"
        "- Emit this marker ONLY when the whole task is done, never during "
        "inter-step thinking or tool-use pauses.\n"
    )


def _real_input_box() -> str:
    return (
        "╭──────────────────────────────────────────╮\n"
        "│ > K2.7 Code  context: 120k tokens        │\n"
        "╰──────────────────────────────────────────╯"
    )


def _real_pane(*, answer: str, emit_marker: bool, trailing_blank: bool = False) -> str:
    """Build a pane that mirrors the real prod layout.

    prompt block (carries the EXAMPLE marker) -> assistant bullet reply ->
    optional emitted marker -> optional blank line -> idle input box.
    """
    parts = [_real_prompt_block(_REQ_ID), f"● {answer}"]
    if emit_marker:
        parts.append(f"   CCB_DONE: {_REQ_ID}")
    if trailing_blank:
        parts.append("")
    parts.append(_real_input_box())
    return "\n".join(parts)


def test_r1_prompt_echo_with_emitted_marker_completes_via_sentinel(
    monkeypatch, tmp_path: Path
) -> None:
    """R1: real pane (prompt echo + answer + emitted marker + box) -> sentinel.

    The EMITTED marker (in the assistant region) must fire; the prompt's EXAMPLE
    marker must NOT.  Reply = answer only, marker stripped, prompt excluded.
    Completion reason = kimi_sentinel_complete with done_marker=True.
    """
    work_dir = tmp_path / "project"
    work_dir.mkdir()

    answer = "NoReplyReason 共有 17 个值。 ... submit_failed、provider_api_error、sender_mailbox_missed。"
    pane_text = _real_pane(answer=answer, emit_marker=True)

    # Native TurnEnd not yet seen (marker printed before TurnEnd flushes); pane
    # not idle-stable.  Without the pane-region sentinel scan this would NOT
    # complete; the fix must complete it via the emitted marker.
    monkeypatch.setattr(
        "provider_backends.kimi.execution.observe_kimi_turn",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "provider_backends.kimi.execution._observe_kimi_pane_turn",
        lambda *args, **kwargs: _pane_observation(completed=False, reply=""),
    )

    result = KimiProviderAdapter().poll(_submission(work_dir, pane_text=pane_text), now="2026-06-13T00:00:10Z")

    assert result is not None
    boundary = _boundary_item(result)
    assert boundary is not None, "emitted marker must fire TURN_BOUNDARY even with prompt echo"
    assert boundary.payload.get("reason") == "kimi_sentinel_complete"
    assert boundary.payload.get("done_marker") is True
    assert boundary.payload.get("completion_source") == "sentinel"
    # Reply is the assistant answer only: no marker, no prompt echo, no bullet.
    assert result.submission.reply == answer
    assert "CCB_DONE" not in result.submission.reply
    assert "IMPORTANT COMPLETION SIGNAL" not in result.submission.reply
    assert "CCB_REQ_ID" not in result.submission.reply


def test_r2_prompt_echo_without_emitted_marker_does_not_false_fire(
    monkeypatch, tmp_path: Path
) -> None:
    """R2: prompt carries the EXAMPLE marker but kimi hasn't emitted yet.

    Must NOT complete (no false fire on the prompt example).  The pane shows a
    partial assistant reply but no emitted marker line.
    """
    work_dir = tmp_path / "project"
    work_dir.mkdir()

    pane_text = _real_pane(answer="partial answer, kimi still working", emit_marker=False)

    monkeypatch.setattr(
        "provider_backends.kimi.execution.observe_kimi_turn",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "provider_backends.kimi.execution._observe_kimi_pane_turn",
        lambda *args, **kwargs: _pane_observation(completed=False, reply=""),
    )

    result = KimiProviderAdapter().poll(_submission(work_dir, pane_text=pane_text), now="2026-06-13T00:00:10Z")

    assert result is not None
    # Prompt example marker must NOT fire sentinel completion.
    assert _boundary_item(result) is None
    assert result.decision is None


def test_r3_emitted_marker_completes_fast_no_45s_wait(
    monkeypatch, tmp_path: Path
) -> None:
    """R3: assistant emits marker as last non-noise line -> fast completion.

    The sentinel path bypasses PANE_FALLBACK_STABLE_SECS.  We verify by polling
    once at t=10s (well under the 45s threshold) and asserting the boundary
    fires immediately with the sentinel reason.
    """
    work_dir = tmp_path / "project"
    work_dir.mkdir()

    answer = "The fix is applied to src/auth.py."
    pane_text = _real_pane(answer=answer, emit_marker=True)

    monkeypatch.setattr(
        "provider_backends.kimi.execution.observe_kimi_turn",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "provider_backends.kimi.execution._observe_kimi_pane_turn",
        lambda *args, **kwargs: _pane_observation(completed=False, reply=""),
    )

    result = KimiProviderAdapter().poll(_submission(work_dir, pane_text=pane_text), now="2026-06-13T00:00:10Z")

    boundary = _boundary_item(result)
    assert boundary is not None
    assert boundary.payload.get("reason") == "kimi_sentinel_complete"
    # Fast: completed at t=10s, no PANE_FALLBACK_STABLE_SECS wait.


def test_r4_no_marker_idle_stable_uses_fallback_reason(monkeypatch, tmp_path: Path) -> None:
    """R4: no marker at all, idle-stable past 45s -> fallback reason."""
    work_dir = tmp_path / "project"
    work_dir.mkdir()

    # Pane idle-stable, assistant reply present, but NO emitted marker anywhere
    # (not even the prompt example, to isolate the fallback path).
    pane_text = (
        f"CCB_REQ_ID: {_REQ_ID}\n\ndo work\n\n● Done with the task.\n"
        + _real_input_box()
    )

    monkeypatch.setattr(
        "provider_backends.kimi.execution.observe_kimi_turn",
        lambda *args, **kwargs: _native_observation(completed=True, reply="Done with the task."),
    )
    monkeypatch.setattr(
        "provider_backends.kimi.execution._observe_kimi_pane_turn",
        lambda *args, **kwargs: _pane_observation(completed=True, reply="Done with the task."),
    )

    sub = _submission(work_dir, pane_text=pane_text)
    first = KimiProviderAdapter().poll(sub, now="2026-06-13T00:00:10Z")
    assert _boundary_item(first) is None  # stabilizing

    second = KimiProviderAdapter().poll(first.submission, now="2026-06-13T00:00:30Z")
    assert _boundary_item(second) is None  # below 45s

    third = KimiProviderAdapter().poll(second.submission, now="2026-06-13T00:00:56Z")
    boundary = _boundary_item(third)
    assert boundary is not None
    assert boundary.payload.get("reason") == "kimi_pane_idle_complete"
    assert boundary.payload.get("done_marker") is False
    assert boundary.payload.get("completion_source") == "pane_idle_fallback"


def test_r5_marker_after_blank_line_reply_excludes_marker_and_noise(
    monkeypatch, tmp_path: Path
) -> None:
    """R5: answer spans blank lines, marker follows a blank line.

    Reply extraction must keep the answer (including its internal blank lines),
    strip the emitted marker, and exclude the trailing input box / noise.
    """
    work_dir = tmp_path / "project"
    work_dir.mkdir()

    answer = "Implementation Receipt\n\n- changed src/a.py\n- ran tests"
    # Marker follows a blank line after the answer; then the input box.
    pane_text = (
        _real_prompt_block(_REQ_ID)
        + f"● {answer}\n\n   CCB_DONE: {_REQ_ID}\n"
        + _real_input_box()
    )

    monkeypatch.setattr(
        "provider_backends.kimi.execution.observe_kimi_turn",
        lambda *args, **kwargs: None,
    )
    monkeypatch.setattr(
        "provider_backends.kimi.execution._observe_kimi_pane_turn",
        lambda *args, **kwargs: _pane_observation(completed=False, reply=""),
    )

    result = KimiProviderAdapter().poll(_submission(work_dir, pane_text=pane_text), now="2026-06-13T00:00:10Z")

    assert result is not None
    boundary = _boundary_item(result)
    assert boundary is not None
    assert boundary.payload.get("reason") == "kimi_sentinel_complete"
    # Internal blank line preserved; marker + box stripped.
    assert result.submission.reply == answer
    assert "CCB_DONE" not in result.submission.reply
    assert "K2.7 Code" not in result.submission.reply


def test_r6_unit_extract_reply_region_excludes_prompt_echo_and_box() -> None:
    """Unit guard: _extract_kimi_reply_region isolates the assistant region.

    Locks the textual contract that the prompt block (with its EXAMPLE marker)
    and the idle input box are both excluded, leaving only answer + emitted
    marker.  This is the boundary the R1-R5 integration tests rely on.
    """
    from provider_backends.kimi.execution import _extract_kimi_reply_region

    answer = "the real answer text"
    pane = _real_pane(answer=answer, emit_marker=True)

    region = _extract_kimi_reply_region(pane, _REQ_ID)

    # Only ONE CCB_DONE occurrence (the emitted one); prompt example excluded.
    assert region.count("CCB_DONE") == 1
    assert f"CCB_DONE: {_REQ_ID}" in region
    # Prompt region fully excluded.
    assert "CCB_REQ_ID" not in region
    assert "IMPORTANT COMPLETION SIGNAL" not in region
    # Input box excluded.
    assert "K2.7 Code" not in region
    assert "╭" not in region
    # Assistant answer present.
    assert answer in region
