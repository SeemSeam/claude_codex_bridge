from __future__ import annotations

import os
import re
from dataclasses import replace

from ccbd.system import parse_utc_timestamp
from completion.models import CompletionConfidence, CompletionDecision, CompletionStatus
from provider_execution.active import (
    ensure_active_pane_alive,
    prepare_active_poll_without_liveness,
)
from provider_execution.base import ProviderPollResult, ProviderSubmission
from provider_execution.draft_guard import allow_submission_send, guarded_send, send_unknown_result
from provider_execution.common import send_prompt_to_runtime_target

from .event_reading import (
    is_turn_boundary_event,
    read_events,
    terminal_api_error_payload,
)
from .hook_results import poll_exact_hook
from .hook_results_runtime import (
    load_strict_exact_hook_evidence,
    poll_hook_event,
)
from .start import looks_ready, send_prompt, state_session_path
from .state_machine import (
    apply_session_rotation,
    build_poll_state,
    finalize_poll_result,
    handle_assistant_event,
    handle_prompt_lifecycle_event,
    handle_system_event,
    handle_user_event,
    is_top_level_user_prompt,
)


def poll_submission(
    adapter,
    submission: ProviderSubmission,
    *,
    now: str,
) -> ProviderPollResult | None:
    del adapter
    prepared = _prepare_submission_poll(submission, now=now)
    if prepared is None or isinstance(prepared, ProviderPollResult):
        return prepared
    prompt_dispatch = _dispatch_deferred_prompt(
        submission,
        prepared=prepared,
        now=now,
    )
    if isinstance(prompt_dispatch, ProviderPollResult):
        return prompt_dispatch
    dispatch_items = ()
    if isinstance(prompt_dispatch, ProviderSubmission):
        submission = prompt_dispatch
    unknown = send_unknown_result(submission, now=now)
    if unknown is not None:
        return unknown
    if submission.runtime_state.get('draft_guard_enabled') and not submission.runtime_state.get('prompt_sent', True):
        return ProviderPollResult(submission=submission)
    # Reply deliveries no longer complete when the prompt is sent: the
    # attributed Stop hook / event-stream turn boundary below completes them,
    # holding the target's execution slot until the exact turn end.
    hook_result = poll_exact_hook(submission, now=now) if _prompt_completion_is_eligible(submission) else None
    if hook_result is None:
        hook_result = _orphaned_exact_hook(submission, prepared=prepared, now=now)
    if hook_result is not None:
        return _merge_poll_result_items(hook_result, prefix_items=dispatch_items)
    pane_dead_result = _ensure_prepared_pane_alive(submission, prepared=prepared, now=now)
    if pane_dead_result is not None:
        return _merge_poll_result_items(pane_dead_result, prefix_items=dispatch_items)
    state = submission.runtime_state.get("state") or {}
    poll = build_poll_state(submission)
    state = _poll_event_batches(submission, prepared.reader, poll, state=state, now=now)
    if isinstance(state, ProviderPollResult):
        return _merge_poll_result_items(state, prefix_items=dispatch_items)
    pane_terminal = _idle_pane_round_result_terminal(
        submission,
        prepared=prepared,
        poll=poll,
        state=state,
        now=now,
    )
    if pane_terminal is not None:
        return _merge_poll_result_items(pane_terminal, prefix_items=dispatch_items)
    # Bounded one-time activation retry: re-send Enter once for a prompt that was
    # pasted but never activated (see _maybe_resend_activation_enter).
    activation_retry = _maybe_resend_activation_enter(
        submission,
        prepared=prepared,
        poll=poll,
        now=now,
    )
    if activation_retry is not None:
        submission = activation_retry
    return _merge_poll_result_items(
        finalize_poll_result(submission, poll, state=state),
        prefix_items=dispatch_items,
    )


_ROUND_RESULT_RE = re.compile(
    r"(?:^|\n)\s*[●•⏺]\s*round\s+result\s*:\s*"
    r"(pass|partial|replan_required|blocked)\b",
    re.IGNORECASE,
)


def _idle_pane_round_result_terminal(
    submission: ProviderSubmission,
    *,
    prepared,
    poll,
    state: dict[str, object],
    now: str,
) -> ProviderPollResult | None:
    """Recover a parser-enforced round result omitted from Claude's event log.

    Some Claude-compatible endpoints render the final answer and return to the
    input box without persisting a final assistant text event or firing Stop.
    The request anchor, result, and idle prompt must all be visible in order in
    the same pane snapshot; no elapsed-time inference is used.
    """
    if submission.agent_name != "ccb_round_reviewer":
        return None
    if poll.reached_turn_boundary or not poll.anchor_seen or not poll.request_anchor:
        return None
    get_pane_content = getattr(prepared.backend, "get_pane_content", None)
    if not callable(get_pane_content):
        return None
    try:
        pane_text = str(get_pane_content(prepared.pane_id, lines=2000) or "")
    except Exception:
        return None
    anchored = _pane_text_after_latest_anchor(pane_text, poll.request_anchor)
    if anchored is None:
        return None
    matches = tuple(_ROUND_RESULT_RE.finditer(anchored))
    if not matches:
        return None
    match = matches[-1]
    after_result = anchored[match.end() :]
    if not _has_idle_input_box(after_result):
        return None

    round_result = match.group(1).lower()
    reply = f"round result: {round_result}"
    updated = replace(
        submission,
        reply=reply,
        runtime_state={
            **submission.runtime_state,
            "state": state,
            "next_seq": poll.next_seq,
            "anchor_seen": poll.anchor_seen,
            "reply_buffer": reply,
            "raw_buffer": poll.raw_buffer,
            "session_path": poll.session_path,
            "last_assistant_uuid": poll.last_assistant_uuid,
            "active_assistant_message_id": poll.active_assistant_message_id,
            "active_assistant_text": poll.active_assistant_text,
            "active_assistant_stop_reason": poll.active_assistant_stop_reason,
            "active_assistant_has_tool_use": poll.active_assistant_has_tool_use,
            "terminal_reply": reply,
            "prompt_enqueued": poll.prompt_enqueued,
            "queue_dequeue_observed": poll.queue_dequeue_observed,
            "prompt_activated": poll.prompt_activated,
            "prompt_enqueue_uuid": poll.prompt_enqueue_uuid,
            "prompt_activation_uuid": poll.prompt_activation_uuid,
        },
    )
    decision = CompletionDecision(
        terminal=True,
        status=CompletionStatus.COMPLETED,
        reason="claude_idle_pane_round_result",
        confidence=CompletionConfidence.OBSERVED,
        reply=reply,
        anchor_seen=True,
        reply_started=True,
        reply_stable=True,
        provider_turn_ref=poll.request_anchor,
        source_cursor=None,
        finished_at=now,
        diagnostics={
            "completion_source": "idle_pane_round_result",
            "completion_fallback_source": "terminal_capture",
            "completion_fallback_kind": "provider_declared",
            "terminal_capture_role": "provider_declared_fallback",
            "pane_id": prepared.pane_id,
            "round_result": round_result,
            "session_event_final_text_missing": True,
        },
    )
    return ProviderPollResult(submission=updated, items=tuple(poll.items), decision=decision)


def _pane_text_after_latest_anchor(text: str, request_anchor: str) -> str | None:
    index = text.rfind(request_anchor)
    if index < 0:
        return None
    return text[index + len(request_anchor) :]


def _has_idle_input_box(text: str) -> bool:
    if "esc to interrupt" in text.lower():
        return False
    for line in text.splitlines():
        normalized = line.replace("\xa0", " ").strip()
        if normalized.startswith("❯") and not normalized[1:].strip():
            return True
        if re.fullmatch(r"[│|]\s*[>❯]\s*[│|]", normalized):
            return True
    return False


def _prepare_submission_poll(
    submission: ProviderSubmission,
    *,
    now: str,
):
    prepared = prepare_active_poll_without_liveness(submission, now=now)
    return prepared


def _dispatch_deferred_prompt(
    submission: ProviderSubmission,
    *,
    prepared,
    now: str,
) -> ProviderPollResult | ProviderSubmission | None:
    if bool(submission.runtime_state.get("prompt_sent", True)):
        return None
    if submission.runtime_state.get('draft_guard_enabled'):
        state = dict(submission.runtime_state)
        if allow_submission_send('claude', state):
            state['state'] = prepared.reader.capture_state()
            guarded_send(state, lambda: send_prompt_to_runtime_target(
                prepared.backend, prepared.pane_id, str(state.get('prompt_text') or '')))
            state['prompt_sent_at'] = now
            state['prompt_deferred_for_ready'] = False
        return replace(submission, runtime_state=state)
    if not _prompt_delivery_due(submission, backend=prepared.backend, pane_id=prepared.pane_id, now=now):
        if bool(submission.runtime_state.get("prompt_deferred_for_ready", False)):
            return None
        return replace(
            submission,
            runtime_state={
                **submission.runtime_state,
                "prompt_deferred_for_ready": True,
            },
        )
    prompt = str(submission.runtime_state.get("prompt_text") or "")
    send_prompt(prepared.backend, prepared.pane_id, prompt)
    anchor_seen = bool(submission.runtime_state.get("anchor_seen", False))
    updated = replace(
        submission,
        runtime_state={
            **submission.runtime_state,
            "prompt_sent": True,
            "prompt_sent_at": now,
            "anchor_seen": anchor_seen,
            "prompt_activated": bool(submission.runtime_state.get("prompt_activated", False)),
            "prompt_deferred_for_ready": False,
            "prompt_anchor_emitted_at": "",
        },
    )
    return updated


def _prompt_completion_is_eligible(submission: ProviderSubmission) -> bool:
    state = submission.runtime_state
    if bool(state.get("no_wrap", False)):
        return True
    if "prompt_activated" in state:
        return bool(state.get("prompt_activated", False) and state.get("anchor_seen", False))
    if state.get("prompt_anchor_emitted_at"):
        return False
    return bool(state.get("anchor_seen", False))


_ORPHANED_HOOK_GRACE_S = 180.0


def _orphaned_exact_hook(
    submission: ProviderSubmission,
    *,
    prepared,
    now: str,
) -> ProviderPollResult | None:
    """Recover a completed turn whose transcript anchor was missed.

    This bypasses prompt-activation gating only after independent artifact,
    session, time, and idle-pane proof. Missing proof always keeps the normal
    event-log path authoritative.
    """
    if bool(submission.runtime_state.get("no_wrap", False)):
        return None
    evidence = load_strict_exact_hook_evidence(submission, now=now)
    if evidence is None:
        return None
    try:
        age_s = (parse_utc_timestamp(now) - evidence.event_at).total_seconds()
    except Exception:
        return None
    if age_s < _ORPHANED_HOOK_GRACE_S:
        return None
    if not _pane_observably_idle(prepared):
        return None
    return poll_hook_event(
        submission,
        context=evidence.context,
        event=evidence.event,
        now=now,
        extra_diagnostics={
            "completion_fallback_source": "orphaned_exact_hook",
            "request_anchor_observation_missed": True,
            "orphaned_hook_grace_s": _ORPHANED_HOOK_GRACE_S,
            "orphaned_hook_age_s": age_s,
        },
    )


def _pane_observably_idle(prepared) -> bool:
    backend = getattr(prepared, "backend", None)
    get_pane_content = getattr(backend, "get_pane_content", None)
    if not callable(get_pane_content):
        return False
    try:
        text = str(get_pane_content(getattr(prepared, "pane_id", None), lines=80) or "")
    except Exception:
        return False
    if "esc to interrupt" in text.lower():
        return False
    return _has_idle_input_box(text)


def _activation_grace_s() -> float:
    """Seconds to wait after dispatch before a lost Enter may be retried.

    A retry is never sent before this grace: the initial paste may still be
    inserting, so an earlier Enter would be eaten again (or become a stray
    newline). Default 6s.
    """
    try:
        return max(0.0, float(os.environ.get("CCB_CLAUDE_ACTIVATION_GRACE_S", 6.0)))
    except Exception:
        return 6.0


def _activation_max_wait_s() -> float:
    """Generous give-up bound replacing the old tight ``[grace, 2*grace)`` window.

    The old default 6–12s window could permanently miss a stuck prompt: a
    multi-KB paste can keep rendering past 12s, and the polling cadence may never
    land inside such a narrow slice. Because the retry is at-most-once and gated
    on current-composer evidence for this job plus a non-busy pane, a wide bound
    is still safe — it only decides how long a genuinely abandoned job remains
    auto-recoverable before an operator must intervene. Default 600s (10 min).
    """
    try:
        return max(0.0, float(os.environ.get("CCB_CLAUDE_ACTIVATION_MAX_WAIT_S", 600.0)))
    except Exception:
        return 600.0


def _elapsed_since(from_at: str, now: str) -> float | None:
    try:
        return (parse_utc_timestamp(now) - parse_utc_timestamp(from_at)).total_seconds()
    except Exception:
        return None


# Claude folds a long bracketed paste in the composer into this placeholder:
# ``❯ [Pasted text #4 +11 lines]``. The raw prompt (and therefore the request
# anchor) is no longer visible in the pane, so the placeholder on the *current*
# composer row is the only evidence that this job's prompt is still pending.
_PASTED_PLACEHOLDER_RE = re.compile(r"\[Pasted text #\d+\s+\+\s*\d+\s+lines?\]")

# --- Prompt-tail fingerprint (third activation evidence) ---------------------
# A long pasted prompt whose head (and request anchor) scrolled out of the pane
# capture leaves only its *tail* visible in the expanded multi-line composer.
# The tail of the submitted prompt — not the anchor and not the folded
# placeholder — is then the only remaining proof that *this* job's input is
# still pending. Generic control lines never form the fingerprint, and only the
# current composer block is inspected, so a matching tail in scrollback/history
# can never trigger.
_GENERIC_CONTROL_LINE_RE = re.compile(
    r"\b(?:CCB_REQ_ID|CCB_REPLY_MODE|CCB_BEGIN|CCB_END)\b",
    re.IGNORECASE,
)
_TAIL_FRAGMENT_CHARS = 48        # tail fragment contributed per business line
_TAIL_FINGERPRINT_MAX_LINES = 3  # last up-to-3 business lines
_TAIL_MIN_LINE_CHARS = 10        # a business line must be this long to count as "long enough"
_TAIL_MIN_TOTAL_CHARS = 28       # combined tail specificity floor
_TAIL_MIN_MATCH = 2              # at least this many fragments must land, in order

# Composer block boundaries: hint/status rows are never part of the input.
_STATUS_HINT_RE = re.compile(
    r"for shortcuts|esc to interrupt|type your message|for commands",
    re.IGNORECASE,
)
_ARROW_ROW_RE = re.compile(r"^\s*[❯>]\s?(.*)$")
_BOXED_ROW_RE = re.compile(r"^\s*[│|]\s*[❯>]?\s?(.*?)\s*[│|]\s*$")
_TRANSCRIPT_ROLE_RE = re.compile(r"^\s*(?:user|assistant|system)\b", re.IGNORECASE)


def _current_composer_content(text: str) -> str | None:
    """Content of the current Claude composer input row, decorations removed.

    The composer is the bottom-most input row in the visible pane tail (arrow
    ``❯ ...`` or boxed ``│ ... │``); hint/status lines rendered below
    it (``? for shortcuts``, ``esc to interrupt``) are skipped. Returns ``""``
    for an empty idle box, or ``None`` when no composer row is found. Only the
    current input row is inspected, so a marker or placeholder in
    scrollback/history can never match.
    """
    tail = text.splitlines()[-32:]
    for raw in reversed(tail):
        line = raw.replace("\xa0", " ").rstrip()
        if not line.strip():
            continue
        arrow = re.match(r"^\s*[❯>]\s?(.*)$", line)
        if arrow:
            return arrow.group(1)
        boxed = re.match(r"^\s*[│|]\s*[❯>]?\s?(.*?)\s*[│|]\s*$", line)
        if boxed:
            return boxed.group(1)
    return None


def _current_composer_holds_pasted_placeholder(text: str) -> bool:
    """True only when the *current* composer row shows a collapsed paste.

    A placeholder that merely appears in the transcript history (a previous
    turn's folded paste) never matches, because only the bottom-most input row
    is considered.
    """
    content = _current_composer_content(text)
    if content is None:
        return False
    return bool(_PASTED_PLACEHOLDER_RE.search(content))


def _pane_holds_current_job_marker(text: str, submission: ProviderSubmission) -> bool:
    """True when the current composer still shows a marker of *this* job.

    The wrapped prompt is ``CCB_REQ_ID: <request_anchor>``; ``no_wrap`` prompts
    carry the anchor (job id) directly. The marker must be inside the current
    composer block, never merely in transcript history. An empty composer or a
    different job's text must not match, so a retry Enter cannot submit stale
    history as if it were pending input.
    """
    composer = _current_composer_block(str(text or ""))
    if not composer:
        return False
    anchor = str(
        submission.runtime_state.get("request_anchor")
        or submission.job_id
        or ""
    ).strip()
    if not anchor:
        return False
    if f"CCB_REQ_ID: {anchor}" in composer:
        return True
    if "CCB_BEGIN" in composer and anchor in composer:
        return True
    return anchor in composer


def _collapse_whitespace(text: str) -> str:
    """Collapse any whitespace run (incl. ``\\xa0``) to a single space."""
    return " ".join(str(text).replace("\xa0", " ").split())


def _strip_all_whitespace(text: str) -> str:
    """Remove *all* whitespace — used for wrap-tolerant fingerprint matching.

    A TUI-wrapped prompt line renders as several visual rows; stripping every
    whitespace char on both sides lets a single logical line still match across
    the wrap boundary (and across composer indentation).
    """
    return "".join(str(text).replace("\xa0", " ").split())


def _prompt_tail_fingerprint(prompt_text: str) -> tuple[str, ...] | None:
    """Stable, non-generic tail fingerprint of a submitted prompt.

    Returns the tail fragments of the last 2-3 non-blank, non-control business
    lines (``CCB_REQ_ID:`` / ``CCB_REPLY_MODE:`` / ``CCB_BEGIN`` / ``CCB_END``
    are excluded). A line longer than ``_TAIL_FRAGMENT_CHARS`` contributes only
    its trailing fragment, because an expanded composer shows the *tail* of a
    long pasted prompt. Returns ``None`` when fewer than two usable lines remain
    or the combined tail is too short to be specific.
    """
    business: list[str] = []
    for raw in (prompt_text or "").splitlines():
        line = _collapse_whitespace(raw)
        if not line:
            continue
        if _GENERIC_CONTROL_LINE_RE.search(line):
            continue
        business.append(line)
    if len(business) < 2:
        return None
    # Prefer the last up-to-3 business lines that are each long enough to be
    # specific; otherwise fall back to the last business lines so a short final
    # line can still anchor the tail.
    long_lines = [line for line in business if len(line) >= _TAIL_MIN_LINE_CHARS]
    tail = (
        long_lines[-_TAIL_FINGERPRINT_MAX_LINES:]
        if len(long_lines) >= 2
        else business[-_TAIL_FINGERPRINT_MAX_LINES:]
    )
    fragments = tuple(
        line[-_TAIL_FRAGMENT_CHARS:] if len(line) > _TAIL_FRAGMENT_CHARS else line
        for line in tail
    )
    if len("".join(fragments)) < _TAIL_MIN_TOTAL_CHARS:
        return None
    return fragments


def _composer_row_content(line: str) -> str:
    """Content of a composer row with arrow/box decoration removed."""
    arrow = _ARROW_ROW_RE.match(line)
    if arrow:
        return arrow.group(1)
    boxed = _BOXED_ROW_RE.match(line)
    if boxed:
        return boxed.group(1)
    return line


def _is_composer_anchor_row(line: str) -> bool:
    return bool(_ARROW_ROW_RE.match(line)) or bool(_BOXED_ROW_RE.match(line))


def _is_input_block_boundary(line: str) -> bool:
    """True when a row above/below the anchor is *not* part of this composer.

    Boxed rows (``│ … │``) are always input; indented arrow-style continuation
    rows are input; blank lines, hint/status rows, transcript-turn rows, and
    col-0 (non-boxed) history lines are boundaries.
    """
    if not line.strip():
        return True
    if _is_composer_anchor_row(line):
        return True  # a different (empty) composer row
    if _BOXED_ROW_RE.match(line):
        return False  # boxed rows are input, never a boundary
    if _STATUS_HINT_RE.search(line):
        return True
    if _TRANSCRIPT_ROLE_RE.match(line):
        return True
    if not line[0].isspace():
        return True  # col-0 non-boxed line = transcript/history boundary
    return False


def _current_composer_block(text: str) -> str | None:
    """Full text of the *current* composer input block, decorations removed.

    Supports the expanded multi-line composer and TUI-wrapped rows: the block is
    the bottom-most composer anchor (``❯ ...`` / boxed ``│ ... │``) plus every
    contiguous input row above and below it, stopping at the first boundary
    (blank line, hint/status row, a transcript-turn row, or another composer
    anchor). Returns ``None`` when no composer input is visible. Only the
    current input region is inspected, so a marker, placeholder, or prompt tail
    in scrollback/history can never match.
    """
    tail = text.splitlines()[-64:]
    anchor = None
    for i in range(len(tail) - 1, -1, -1):
        line = tail[i].replace("\xa0", " ").rstrip()
        if _is_composer_anchor_row(line):
            anchor = i
            break
    if anchor is None:
        return None
    rows: list[str] = []
    j = anchor - 1
    while j >= 0:
        line = tail[j].replace("\xa0", " ").rstrip()
        if _is_input_block_boundary(line):
            break
        rows.insert(0, _composer_row_content(line))
        j -= 1
    rows.append(_composer_row_content(tail[anchor]))
    k = anchor + 1
    while k < len(tail):
        line = tail[k].replace("\xa0", " ").rstrip()
        if _is_input_block_boundary(line):
            break
        rows.append(_composer_row_content(line))
        k += 1
    return "\n".join(rows)


def _wrap_tolerant_match(block: str, fingerprint: tuple[str, ...]) -> bool:
    """Ordered, wrap-tolerant containment of the fingerprint in a composer block.

    All whitespace is removed on both sides so a TUI-wrap newline or indentation
    cannot break a fragment. At least ``_TAIL_MIN_MATCH`` fragments must appear
    in prompt order (a single short line is not enough).
    """
    block_key = _strip_all_whitespace(block)
    pos = 0
    matched = 0
    for frag in fingerprint:
        key = _strip_all_whitespace(frag)
        if not key:
            continue
        idx = block_key.find(key, pos)
        if idx < 0:
            continue
        pos = idx + len(key)
        matched += 1
        if matched >= _TAIL_MIN_MATCH:
            return True
    return matched >= _TAIL_MIN_MATCH


def _composer_holds_prompt_tail(text: str, prompt_text: str) -> bool:
    """True when the *current* composer block holds this job's prompt tail.

    The prompt-tail fingerprint must hit the current composer block; a tail that
    only appears in scrollback/history can never match, and generic control lines
    never form the fingerprint.
    """
    block = _current_composer_block(text)
    if block is None:
        return False
    fingerprint = _prompt_tail_fingerprint(prompt_text)
    if not fingerprint:
        return False
    return _wrap_tolerant_match(block, fingerprint)


def _maybe_resend_activation_enter(
    submission: ProviderSubmission,
    *,
    prepared,
    poll,
    now: str,
) -> ProviderSubmission | None:
    """Bounded one-time activation Enter re-send for a sent-but-stuck prompt.

    Root cause: tmux ``paste-buffer`` returns once bytes hit the pty, but the
    Claude TUI consumes/renders a bracketed-paste stream asynchronously. For a
    long (often multi-KB Unicode) prompt the initial Enter, sent
    ``CCB_TMUX_ENTER_DELAY`` later, can land while the composer is still
    inserting and be swallowed — the prompt stays in the composer, no request
    anchor ever appears, and the job hangs in ``delivering`` until an operator
    presses Enter manually.

    There is no paste ACK in the TUI, so the request anchor is the only real
    activation proof. This monitor re-sends Enter **at most once**, and only
    while every one of these holds:

    * the prompt was already dispatched (``prompt_sent``) with a timestamp;
    * the current job is not yet activated (no ``prompt_activated``/``anchor_seen``);
    * the elapsed time since dispatch is at least the grace start and below the
      generous give-up cap (``CCB_CLAUDE_ACTIVATION_MAX_WAIT_S``, default 600s);
    * the pane is not busy (no ``esc to interrupt``) and the current composer
      still holds *this* job's pending input — any one of three pieces of
      evidence:
        * the recognizable prompt/anchor text;
        * when the long paste has been folded by the TUI, the collapsed
          placeholder ``[Pasted text #N +M lines]`` on the current composer row;
        * when the prompt's head (and anchor) has scrolled out of the capture,
          a wrap-tolerant match of the submitted prompt's tail fingerprint
          against the current composer block (expanded multi-line / TUI-wrapped
          rows) — generic control lines never form the fingerprint, and a tail
          only present in scrollback/history never matches;
      (an empty composer, a different job's text, or a placeholder / tail that
      only appears in scrollback never matches);
    * this job has not already re-sent Enter (``activation_enter_count < 1``).

    The old ``[grace, 2*grace)`` window (default 6–12s) is removed: a folded
    long paste renders asynchronously for longer than that, so the retry could
    permanently miss the whole slice. The wide give-up cap plus at-most-once
    plus current-composer evidence is the safe replacement: it can never send
    more than one Enter, and that one Enter only ever submits the prompt that is
    still sitting in this job's composer. On a successful ``send_key`` the
    runtime state bumps ``activation_enter_count``, records the evidence kind
    (``anchor_marker`` | ``pasted_placeholder`` | ``prompt_tail``) in
    ``activation_enter_evidence``, and stamps ``activation_enter_at`` so later
    polls never re-send.
    """
    state = submission.runtime_state
    if state.get('draft_guard_enabled'):
        # Folded-paste/anchor evidence does not prove that the user has not
        # appended a draft. Guarded v1 deliberately has no second Enter.
        return None
    if not bool(state.get("prompt_sent", False)):
        return None
    prompt_sent_at = str(state.get("prompt_sent_at") or "").strip()
    if not prompt_sent_at:
        return None
    if int(state.get("activation_enter_count", 0) or 0) >= 1:
        return None
    if poll is not None and (
        bool(getattr(poll, "anchor_seen", False))
        or bool(getattr(poll, "prompt_activated", False))
    ):
        return None
    elapsed = _elapsed_since(prompt_sent_at, now)
    if elapsed is None:
        return None
    if elapsed < _activation_grace_s():
        return None
    if elapsed >= _activation_max_wait_s():
        return None
    pane_id = getattr(prepared, "pane_id", None)
    backend = getattr(prepared, "backend", None)
    get_pane_content = getattr(backend, "get_pane_content", None)
    send_key = getattr(backend, "send_key", None)
    if not pane_id or not callable(get_pane_content) or not callable(send_key):
        return None
    try:
        pane_text = str(get_pane_content(pane_id, lines=200) or "")
    except Exception:
        return None
    if "esc to interrupt" in pane_text.lower():
        return None
    has_placeholder = _current_composer_holds_pasted_placeholder(pane_text)
    has_anchor = _pane_holds_current_job_marker(pane_text, submission)
    has_tail = _composer_holds_prompt_tail(pane_text, str(state.get("prompt_text") or ""))
    if not (has_placeholder or has_anchor or has_tail):
        return None
    if has_placeholder:
        evidence = "pasted_placeholder"
    elif has_anchor:
        evidence = "anchor_marker"
    else:
        evidence = "prompt_tail"
    try:
        sent = send_key(pane_id, "Enter")
    except Exception:
        return None
    if not sent:
        return None
    return replace(
        submission,
        runtime_state={
            **state,
            "activation_enter_count": int(state.get("activation_enter_count", 0) or 0) + 1,
            "activation_enter_at": now,
            "activation_enter_evidence": evidence,
        },
    )


def _merge_poll_result_items(result: ProviderPollResult, *, prefix_items: tuple) -> ProviderPollResult:
    if not prefix_items:
        return result
    return ProviderPollResult(
        submission=result.submission,
        items=tuple(prefix_items) + tuple(result.items),
        decision=result.decision,
    )


def _prompt_delivery_due(
    submission: ProviderSubmission,
    *,
    backend: object,
    pane_id: str,
    now: str,
) -> bool:
    get_pane_content = getattr(backend, "get_pane_content", None)
    if not callable(get_pane_content):
        return True
    try:
        text = str(get_pane_content(pane_id, lines=120) or "")
    except Exception:
        return True
    if looks_ready(text):
        return True
    # Reply delivery prefers an observed ready prompt, but it must not deadlock
    # a serial mailbox queue forever when the prompt detector never converges.
    return _ready_wait_timed_out(submission, now=now)



def _ready_wait_timed_out(submission: ProviderSubmission, *, now: str) -> bool:
    started_at = str(submission.runtime_state.get("ready_wait_started_at") or "").strip()
    if not started_at:
        return True
    try:
        timeout_s = float(submission.runtime_state.get("ready_timeout_s", 8.0))
    except Exception:
        timeout_s = 8.0
    try:
        elapsed = (parse_utc_timestamp(now) - parse_utc_timestamp(started_at)).total_seconds()
    except Exception:
        return True
    return elapsed >= max(0.0, timeout_s)


def _ensure_prepared_pane_alive(submission: ProviderSubmission, *, prepared, now: str):
    pane_dead_result = ensure_active_pane_alive(
        submission,
        backend=prepared.backend,
        pane_id=prepared.pane_id,
        now=now,
    )
    if pane_dead_result is not None:
        return pane_dead_result
    return None


def _poll_event_batches(
    submission: ProviderSubmission,
    reader,
    poll,
    *,
    state: dict,
    now: str,
):
    while True:
        batch = _read_event_batch(submission, reader, poll, state=state, now=now)
        if isinstance(batch, ProviderPollResult):
            return batch
        state, has_events = batch
        if not has_events or poll.reached_turn_boundary:
            return state


def _read_event_batch(
    submission: ProviderSubmission,
    reader,
    poll,
    *,
    state: dict,
    now: str,
):
    events, state = read_events(reader, state)
    apply_session_rotation(
        submission,
        poll,
        new_session_path=state_session_path(state),
        now=now,
    )
    if not events:
        return state, False
    event_result = _process_events(submission, poll, events, state=state, now=now)
    if event_result is not None:
        return event_result
    return state, True


def _process_events(
    submission: ProviderSubmission,
    poll,
    events: list[dict],
    *,
    state: dict,
    now: str,
) -> ProviderPollResult | None:
    for event in events:
        result = _process_event(submission, poll, event, state=state, now=now)
        if result is not None:
            return result
        if poll.reached_turn_boundary:
            break
    return None


def _process_event(
    submission: ProviderSubmission,
    poll,
    event: dict,
    *,
    state: dict,
    now: str,
) -> ProviderPollResult | None:
    role = str(event.get("role") or "")
    if role == "prompt_lifecycle":
        handle_prompt_lifecycle_event(submission, poll, event, now=now)
        return None
    if role == "user":
        if is_top_level_user_prompt(event):
            handle_user_event(submission, poll, text=str(event.get("text") or ""), now=now)
        return None
    if role == "system" and poll.anchor_seen:
        return handle_system_event(submission, poll, event, now=now, state=state)
    if role == "assistant" and poll.anchor_seen:
        handle_assistant_event(submission, poll, event, now=now)
    return None


__all__ = [
    "is_turn_boundary_event",
    "poll_exact_hook",
    "poll_submission",
    "read_events",
    "terminal_api_error_payload",
]
