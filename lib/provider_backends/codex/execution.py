from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from ccbd.system import parse_utc_timestamp
from ccbd.api_models import JobRecord
from completion.models import CompletionConfidence, CompletionDecision, CompletionItemKind, CompletionStatus
from provider_backends.codex.comm_runtime.binding import extract_session_id, is_codex_subagent_log
from provider_backends.codex.session_switch import SwitchCandidate, select_exact_anchor_candidate, write_rebound
from provider_core.protocol import request_anchor_for_job, wrap_codex_turn_prompt
from provider_execution.base import ProviderPollResult, ProviderRuntimeContext, ProviderSubmission
from provider_execution.common import build_item, request_anchor_from_runtime_state
from provider_execution.reliability import CompletionReliabilityPolicy
from terminal_runtime import get_backend_for_session

from .comm import CodexLogReader
from .execution_runtime import poll_submission as _poll_submission
from .execution_runtime import resume_submission as _resume_submission
from .execution_runtime import start_active_submission as _start_active_submission
from .execution_runtime.readiness import looks_unusable
from .execution_runtime.start import resolved_delivery_timeout_s
from .session import load_project_session
from .session_runtime.follow_policy import codex_session_root_path, should_follow_workspace_sessions


class CodexProviderAdapter:
    provider = 'codex'
    restart_resume_supported = True
    completion_reliability_policy = CompletionReliabilityPolicy(
        provider='codex',
        primary_authority='protocol_log',
        no_terminal_timeout_s=0.0,
    )

    def start(self, job: JobRecord, *, context: ProviderRuntimeContext | None, now: str) -> ProviderSubmission:
        return _start_active_submission(
            self,
            job,
            context=context,
            now=now,
            load_session_fn=_load_session,
            backend_for_session_fn=get_backend_for_session,
            reader_factory=_reader_factory,
            request_anchor_fn=request_anchor_for_job,
            wrap_prompt_fn=wrap_codex_turn_prompt,
        )

    def poll(self, submission: ProviderSubmission, *, now: str) -> ProviderPollResult | None:
        original_submission = submission
        submission = _refresh_reader_for_current_session_binding(submission)
        submission = _record_delivery_progress(submission, now=now)
        delivery_failure = _delivery_acceptance_guard(submission, now=now)
        if delivery_failure is not None:
            return delivery_failure
        if _quarantined_anchor_fallback_pending(submission.runtime_state):
            return ProviderPollResult(submission=submission)
        result = _poll_submission(submission, now=now)
        if result is not None:
            updated_submission = _record_delivery_progress(result.submission, now=now)
            if updated_submission is not result.submission:
                result = ProviderPollResult(
                    submission=updated_submission,
                    items=result.items,
                    decision=result.decision,
                )
            reply_delivery_result = _reply_delivery_accepted_result(result, now=now)
            if reply_delivery_result is not None:
                return reply_delivery_result
        if result is None and submission is not original_submission:
            return ProviderPollResult(submission=submission)
        return result

    def export_runtime_state(self, submission: ProviderSubmission) -> dict[str, object]:
        return {
            'mode': submission.runtime_state.get('mode'),
            'state': submission.runtime_state.get('state') or {},
            'pane_id': submission.runtime_state.get('pane_id'),
            'request_anchor': request_anchor_from_runtime_state(submission.runtime_state, fallback=submission.job_id),
            'next_seq': submission.runtime_state.get('next_seq'),
            'anchor_seen': submission.runtime_state.get('anchor_seen'),
            'no_wrap': submission.runtime_state.get('no_wrap'),
            'bound_turn_id': submission.runtime_state.get('bound_turn_id'),
            'bound_task_id': submission.runtime_state.get('bound_task_id'),
            'reply_buffer': submission.runtime_state.get('reply_buffer'),
            'last_agent_message': submission.runtime_state.get('last_agent_message'),
            'last_final_answer': submission.runtime_state.get('last_final_answer'),
            'last_assistant_message': submission.runtime_state.get('last_assistant_message'),
            'last_assistant_signature': submission.runtime_state.get('last_assistant_signature'),
            'session_path': submission.runtime_state.get('session_path'),
            'workspace_path': submission.runtime_state.get('workspace_path'),
            'delivery_state': submission.runtime_state.get('delivery_state'),
            'delivery_started_at': submission.runtime_state.get('delivery_started_at'),
            'delivery_last_progress_at': submission.runtime_state.get('delivery_last_progress_at'),
            'delivery_progress_kind': submission.runtime_state.get('delivery_progress_kind'),
            'delivery_timeout_s': submission.runtime_state.get('delivery_timeout_s'),
            'delivery_target_pane_id': submission.runtime_state.get('delivery_target_pane_id'),
            'delivery_target_session_path': submission.runtime_state.get('delivery_target_session_path'),
            'delivery_confirmed_at': submission.runtime_state.get('delivery_confirmed_at'),
            'delivery_failure_kind': submission.runtime_state.get('delivery_failure_kind'),
            'delivery_failed_at': submission.runtime_state.get('delivery_failed_at'),
            'reply_delivery_complete_on_dispatch': submission.runtime_state.get(
                'reply_delivery_complete_on_dispatch'
            ),
        }

    def resume(
        self,
        job: JobRecord,
        submission: ProviderSubmission,
        *,
        context: ProviderRuntimeContext | None,
        persisted_state,
        now: str,
    ) -> ProviderSubmission | None:
        del persisted_state, now
        return _resume_submission(
            job,
            submission,
            context=context,
            load_session_fn=_load_session,
            backend_for_session_fn=get_backend_for_session,
            reader_factory=_reader_factory,
        )


def _reader_factory(session, preferred_log: Path | None):
    work_dir = Path(session.work_dir)
    default_log = Path(session.codex_session_path).expanduser() if session.codex_session_path else None
    selected_log = preferred_log if preferred_log is not None else default_log
    invalid_subagent_binding = selected_log is not None and is_codex_subagent_log(selected_log)
    kwargs: dict[str, object] = {
        "log_path": None if invalid_subagent_binding else selected_log,
        "session_id_filter": None if invalid_subagent_binding else (session.codex_session_id or None),
        "work_dir": work_dir,
        "follow_workspace_sessions": invalid_subagent_binding
        or should_follow_workspace_sessions(
            work_dir=work_dir,
            session_file=getattr(session, "session_file", None),
            session_data=getattr(session, "data", None),
        ),
    }
    session_root = codex_session_root_path(getattr(session, "data", None))
    if session_root is not None:
        kwargs["root"] = session_root
    return CodexLogReader(**kwargs)


def _locked_reader_for_log(session, log_path: Path, *, work_dir: Path) -> CodexLogReader | None:
    try:
        if not log_path.is_file():
            return None
    except OSError:
        return None
    session_id = extract_session_id(log_path)
    if not session_id:
        return None
    kwargs: dict[str, object] = {
        "log_path": log_path,
        "session_id_filter": session_id,
        "work_dir": work_dir,
        "follow_workspace_sessions": False,
    }
    session_root = codex_session_root_path(getattr(session, "data", None))
    if session_root is not None:
        kwargs["root"] = session_root
    return CodexLogReader(**kwargs)


def _refresh_reader_for_current_session_binding(submission: ProviderSubmission) -> ProviderSubmission:
    state = dict(submission.runtime_state)
    if str(state.get('mode') or '').strip().lower() != 'active':
        return submission
    work_dir = _submission_work_dir(submission, state)
    if work_dir is None:
        return submission
    session = _load_session(work_dir, submission.agent_name)
    if session is None:
        return submission
    current_log = _current_session_log(session)
    if current_log is None or not current_log.exists():
        return submission
    if is_codex_subagent_log(current_log):
        return submission

    current_log_str = _normalized_path_string(current_log)
    poll_state = dict(state.get('state') or {})
    poll_state_log_str = _normalized_path_string(poll_state.get('log_path'))
    reader = state.get('reader')
    reader_log_str = _normalized_path_string(getattr(reader, '_preferred_log', None))
    reader_filter = str(getattr(reader, '_session_id_filter', '') or '').strip()
    current_filter = str(getattr(session, 'codex_session_id', '') or '').strip()

    if (
        current_log_str == poll_state_log_str
        and current_log_str == reader_log_str
        and (not current_filter or current_filter == reader_filter)
    ):
        fallback_log = _active_anchor_fallback_log(state)
        if fallback_log is not None and _normalized_resolved_path(fallback_log) != _normalized_resolved_path(current_log):
            return _submission_with_anchor_fallback_diagnostics(
                submission,
                state=state,
                log_path=fallback_log,
                session_id=str(state.get('codex_anchor_fallback_session_id') or ''),
            )

        anchor_fallback = _anchor_fallback_candidate(
            submission,
            state=state,
            poll_state=poll_state,
            session=session,
            work_dir=work_dir,
            current_log=current_log,
        )
        if anchor_fallback is not None:
            if (
                _locked_reader_for_log(session, anchor_fallback.path, work_dir=work_dir) is not None
                and _commit_exact_anchor_fallback_rebind(session, anchor_fallback)
            ):
                rebound = _submission_with_locked_reader(
                    submission,
                    state=state,
                    poll_state=poll_state,
                    session=session,
                    work_dir=work_dir,
                    log_path=anchor_fallback.path,
                    fallback=False,
                )
                if rebound is not None:
                    return rebound
            return _submission_with_anchor_fallback_diagnostics(
                submission,
                state=state,
                log_path=anchor_fallback.path,
                session_id=anchor_fallback.session_id,
            )
        return submission

    updated_state = {
        **state,
        'reader': _reader_factory(session, current_log),
        'workspace_path': str(work_dir),
    }
    _clear_anchor_fallback_diagnostics(updated_state)
    if current_log_str == poll_state_log_str:
        updated_state['session_path'] = current_log_str
    if current_log_str != poll_state_log_str:
        updated_state['state'] = {
            **poll_state,
            'log_path': current_log,
            'offset': 0,
            'last_rescan': 0.0,
        }
    return replace(submission, runtime_state=updated_state)


def _record_delivery_progress(submission: ProviderSubmission, *, now: str) -> ProviderSubmission:
    state = dict(submission.runtime_state)
    if not _delivery_progress_tracking_required(state):
        return submission
    if _quarantined_anchor_fallback_pending(state):
        return submission

    marker, progress_kind = _delivery_progress_marker(submission, state)
    if marker is None:
        return submission
    previous_marker = state.get('delivery_progress_marker')
    if previous_marker == marker:
        return submission

    updated_state = {
        **state,
        'delivery_progress_marker': marker,
        'delivery_progress_kind': progress_kind,
    }
    previous_progress = str(
        state.get('delivery_last_progress_at')
        or state.get('delivery_started_at')
        or submission.ready_at
        or submission.accepted_at
        or now
    )
    if previous_marker is not None or progress_kind in {'session_binding', 'session_unread'}:
        updated_state['delivery_last_progress_at'] = now
    else:
        updated_state['delivery_last_progress_at'] = previous_progress
    if progress_kind == 'session_missing':
        updated_state.setdefault('delivery_session_missing_since', updated_state['delivery_last_progress_at'])
    else:
        updated_state.pop('delivery_session_missing_since', None)
    return replace(submission, runtime_state=updated_state)


def _reply_delivery_accepted_result(
    result: ProviderPollResult,
    *,
    now: str,
) -> ProviderPollResult | None:
    state = dict(result.submission.runtime_state)
    if not bool(state.get('reply_delivery_complete_on_dispatch')):
        return None
    if not bool(state.get('anchor_seen')):
        return None

    request_anchor = request_anchor_from_runtime_state(state, fallback=result.submission.job_id)
    updated = replace(
        result.submission,
        runtime_state={
            **state,
            'delivery_state': 'accepted',
            'delivery_confirmed_at': str(state.get('delivery_confirmed_at') or now),
        },
    )
    source_cursor = result.items[-1].cursor if result.items else None
    decision = CompletionDecision(
        terminal=True,
        status=CompletionStatus.COMPLETED,
        reason='reply_delivery_sent',
        confidence=CompletionConfidence.OBSERVED,
        reply='',
        anchor_seen=True,
        reply_started=False,
        reply_stable=True,
        provider_turn_ref=request_anchor or result.submission.job_id,
        source_cursor=source_cursor,
        finished_at=now,
        diagnostics={
            'reply_delivery': True,
            'delivery_status': 'accepted',
            'provider': result.submission.provider,
            'submission_mode': str(state.get('mode') or 'active'),
        },
    )
    return ProviderPollResult(submission=updated, items=result.items, decision=decision)


def _delivery_progress_tracking_required(state: dict[str, object]) -> bool:
    if str(state.get('mode') or '').strip().lower() != 'active':
        return False
    if bool(state.get('anchor_seen') or state.get('no_wrap')):
        return False
    return str(state.get('delivery_state') or '').strip() == 'pending_anchor'


def _delivery_progress_marker(
    submission: ProviderSubmission,
    state: dict[str, object],
) -> tuple[dict[str, object] | None, str]:
    work_dir = _submission_work_dir(submission, state)
    if work_dir is None:
        return None, ''

    session = _load_session(work_dir, submission.agent_name)
    current_log = _current_session_log(session) if session is not None else None
    current_log_str = _normalized_path_string(current_log)
    current_session_id = str(getattr(session, 'codex_session_id', '') or '').strip() if session is not None else ''
    stat_marker = _delivery_log_stat_marker(current_log)
    poll_state = dict(state.get('state') or {})
    poll_log_str = _normalized_path_string(poll_state.get('log_path'))
    poll_offset = poll_state.get('offset')
    offset = poll_offset if isinstance(poll_offset, int) and poll_offset >= 0 else -1

    marker: dict[str, object] = {
        'current_session_id': current_session_id,
        'current_log_path': current_log_str,
        'current_log_exists': bool(stat_marker.get('exists')),
        'current_log_size': stat_marker.get('size', -1),
        'current_log_mtime_ns': stat_marker.get('mtime_ns', -1),
        'poll_log_path': poll_log_str,
        'poll_offset': offset,
        'fallback_log_path': str(state.get('codex_anchor_fallback_log') or '').strip(),
        'fallback_session_id': str(state.get('codex_anchor_fallback_session_id') or '').strip(),
        'fallback_quarantined': bool(state.get('codex_anchor_fallback_quarantined')),
    }

    if not current_log_str or not bool(stat_marker.get('exists')):
        return marker, 'session_missing'
    if current_log_str != poll_log_str:
        return marker, 'session_binding'
    if isinstance(poll_offset, int) and int(stat_marker.get('size', -1)) > poll_offset:
        return marker, 'session_unread'
    return marker, 'session_present'


def _delivery_log_stat_marker(log_path: Path | None) -> dict[str, object]:
    if log_path is None:
        return {'exists': False}
    try:
        stat = log_path.stat()
    except OSError:
        return {'exists': False}
    return {
        'exists': True,
        'size': int(stat.st_size),
        'mtime_ns': int(stat.st_mtime_ns),
    }


def _delivery_acceptance_guard(submission: ProviderSubmission, *, now: str) -> ProviderPollResult | None:
    state = dict(submission.runtime_state)
    if str(state.get('mode') or '').strip().lower() != 'active':
        return None
    if bool(state.get('anchor_seen') or state.get('no_wrap')):
        return None
    if str(state.get('delivery_state') or '').strip() != 'pending_anchor':
        return None
    if not str(state.get('delivery_target_pane_id') or '').strip():
        return None

    # The shared selector has already proved an exact active-job anchor in a
    # newer managed-root log.  Let protocol polling consume that authority
    # instead of terminalizing the stale official binding at its old timeout.
    fallback_quarantined = _quarantined_anchor_fallback_pending(state)
    if _active_anchor_fallback_log(state) is not None and not fallback_quarantined:
        return None

    failure_kind = _delivery_failure_kind(state, submission=submission, now=now)
    if not failure_kind:
        return None

    work_dir = _submission_work_dir(submission, state)
    if work_dir is None:
        return None
    session = _load_session(work_dir, submission.agent_name)
    if session is None:
        return _delivery_session_missing_result(
            submission,
            now=now,
            failure_kind=_delivery_missing_failure_kind(failure_kind),
            current_log=None,
            current_session_id='',
            checked_root=None,
            work_dir=work_dir,
        )
    current_log = _current_session_log(session)
    if current_log is None or not current_log.exists():
        return _delivery_session_missing_result(
            submission,
            now=now,
            failure_kind=_delivery_missing_failure_kind(failure_kind),
            current_log=current_log,
            current_session_id=str(getattr(session, 'codex_session_id', '') or '').strip(),
            checked_root=codex_session_root_path(getattr(session, 'data', None)),
            work_dir=work_dir,
        )
    checked_root = codex_session_root_path(getattr(session, 'data', None))

    poll_state = dict(state.get('state') or {})
    if not fallback_quarantined and not _current_log_is_drained(current_log, poll_state.get('offset')):
        return None

    return _delivery_failure_result(
        submission,
        now=now,
        failure_kind=failure_kind,
        current_log=current_log,
        checked_root=checked_root,
        work_dir=work_dir,
    )


def _delivery_failure_kind(state: dict[str, object], *, submission: ProviderSubmission, now: str) -> str | None:
    if _delivery_pane_looks_unusable(state):
        return 'delivery_shutdown'
    if _delivery_timeout_elapsed(state, submission=submission, now=now):
        return 'delivery_anchor_missing'
    return None


def _delivery_missing_failure_kind(failure_kind: str) -> str:
    if failure_kind == 'delivery_shutdown':
        return failure_kind
    return 'delivery_session_missing'


def _delivery_pane_looks_unusable(state: dict[str, object]) -> bool:
    backend = state.get('backend')
    pane_id = str(state.get('pane_id') or state.get('delivery_target_pane_id') or '').strip()
    get_pane_content = getattr(backend, 'get_pane_content', None)
    if not pane_id or not callable(get_pane_content):
        return False
    try:
        return looks_unusable(str(get_pane_content(pane_id, lines=120) or ''))
    except Exception:
        return False


def _delivery_timeout_elapsed(state: dict[str, object], *, submission: ProviderSubmission, now: str) -> bool:
    timeout_s = _delivery_timeout_s(state)
    if timeout_s <= 0:
        return False
    progress_at = str(
        state.get('delivery_last_progress_at')
        or state.get('delivery_started_at')
        or submission.ready_at
        or submission.accepted_at
        or ''
    ).strip()
    if not progress_at:
        return False
    try:
        elapsed = (parse_utc_timestamp(now) - parse_utc_timestamp(progress_at)).total_seconds()
    except Exception:
        return False
    return elapsed >= timeout_s


def _delivery_timeout_s(state: dict[str, object]) -> float:
    try:
        raw = state.get('delivery_timeout_s')
        if raw is not None:
            return max(0.0, float(raw))
    except Exception:
        pass
    return resolved_delivery_timeout_s()


def _delivery_failure_result(
    submission: ProviderSubmission,
    *,
    now: str,
    failure_kind: str,
    current_log: Path,
    checked_root: Path | None,
    work_dir: Path,
) -> ProviderPollResult:
    reason = 'codex_prompt_delivery_failed'
    state = dict(submission.runtime_state)
    seq = int(state.get('next_seq', 1))
    request_anchor = request_anchor_from_runtime_state(state, fallback=submission.job_id)
    diagnostics = {
        **dict(submission.diagnostics or {}),
        'reason': reason,
        'delivery_failure_kind': failure_kind,
        'delivery_retryable': True,
        'delivery_state': 'failed',
        'delivery_started_at': str(state.get('delivery_started_at') or ''),
        'delivery_last_progress_at': str(state.get('delivery_last_progress_at') or ''),
        'delivery_timeout_s': _delivery_timeout_s(state),
        'delivery_checked_session_root': str(checked_root or current_log.parent),
        'delivery_current_log_path': str(current_log),
        'delivery_workspace_path': str(work_dir),
        'delivery_anchor_seen': False,
    }
    fallback_log = str(state.get('codex_anchor_fallback_log') or '').strip()
    if fallback_log:
        diagnostics['delivery_anchor_fallback_log'] = fallback_log
        diagnostics['delivery_anchor_fallback_session_id'] = str(
            state.get('codex_anchor_fallback_session_id') or ''
        ).strip()
        diagnostics['delivery_anchor_fallback_quarantined'] = bool(
            state.get('codex_anchor_fallback_quarantined')
        )
    item = build_item(
        submission,
        kind=CompletionItemKind.ERROR,
        timestamp=now,
        seq=seq,
        payload={
            'reason': reason,
            'delivery_failure_kind': failure_kind,
            'delivery_retryable': True,
        },
    )
    updated_state = {
        **state,
        'mode': 'passive',
        'next_seq': item.cursor.event_seq + 1,
        'delivery_state': 'failed',
        'delivery_failure_kind': failure_kind,
        'delivery_failed_at': now,
    }
    updated = replace(
        submission,
        runtime_state=updated_state,
        diagnostics=diagnostics,
    )
    return ProviderPollResult(
        submission=updated,
        items=(item,),
        decision=CompletionDecision(
            terminal=True,
            status=CompletionStatus.FAILED,
            reason=reason,
            confidence=CompletionConfidence.DEGRADED,
            reply='',
            anchor_seen=False,
            reply_started=False,
            reply_stable=False,
            provider_turn_ref=request_anchor or submission.job_id,
            source_cursor=item.cursor,
            finished_at=now,
            diagnostics=diagnostics,
        ),
    )


def _delivery_session_missing_result(
    submission: ProviderSubmission,
    *,
    now: str,
    failure_kind: str,
    current_log: Path | None,
    current_session_id: str,
    checked_root: Path | None,
    work_dir: Path,
) -> ProviderPollResult:
    shutdown = failure_kind == 'delivery_shutdown'
    reason = 'codex_prompt_delivery_failed' if shutdown else 'codex_session_file_missing'
    no_reply_reason = 'provider_crashed' if shutdown else 'completion_detection_gap'
    state = dict(submission.runtime_state)
    seq = int(state.get('next_seq', 1))
    request_anchor = request_anchor_from_runtime_state(state, fallback=submission.job_id)
    current_log_path = str(current_log or '').strip()
    diagnostics = {
        **dict(submission.diagnostics or {}),
        'reason': reason,
        'delivery_failure_kind': failure_kind,
        'delivery_retryable': True,
        'delivery_state': 'failed',
        'delivery_started_at': str(state.get('delivery_started_at') or ''),
        'delivery_last_progress_at': str(state.get('delivery_last_progress_at') or ''),
        'delivery_timeout_s': _delivery_timeout_s(state),
        'delivery_session_missing_since': str(
            state.get('delivery_session_missing_since')
            or state.get('delivery_last_progress_at')
            or state.get('delivery_started_at')
            or ''
        ),
        'delivery_checked_session_root': str(checked_root or ''),
        'delivery_current_log_path': current_log_path,
        'delivery_current_session_id': str(current_session_id or ''),
        'delivery_workspace_path': str(work_dir),
        'delivery_anchor_seen': False,
        'no_reply_reason': no_reply_reason,
    }
    item = build_item(
        submission,
        kind=CompletionItemKind.ERROR,
        timestamp=now,
        seq=seq,
        payload={
            'reason': reason,
            'delivery_failure_kind': failure_kind,
            'delivery_retryable': True,
            'no_reply_reason': no_reply_reason,
        },
    )
    updated_state = {
        **state,
        'mode': 'passive',
        'next_seq': item.cursor.event_seq + 1,
        'delivery_state': 'failed',
        'delivery_failure_kind': failure_kind,
        'delivery_failed_at': now,
    }
    updated = replace(
        submission,
        runtime_state=updated_state,
        diagnostics=diagnostics,
    )
    return ProviderPollResult(
        submission=updated,
        items=(item,),
        decision=CompletionDecision(
            terminal=True,
            status=CompletionStatus.FAILED if shutdown else CompletionStatus.INCOMPLETE,
            reason=reason,
            confidence=CompletionConfidence.DEGRADED,
            reply='',
            anchor_seen=False,
            reply_started=False,
            reply_stable=False,
            provider_turn_ref=request_anchor or submission.job_id,
            source_cursor=item.cursor,
            finished_at=now,
            diagnostics=diagnostics,
        ),
    )


def _submission_with_locked_reader(
    submission: ProviderSubmission,
    *,
    state: dict[str, object],
    poll_state: dict[str, object],
    session,
    work_dir: Path,
    log_path: Path,
    fallback: bool,
) -> ProviderSubmission | None:
    reader = _locked_reader_for_log(session, log_path, work_dir=work_dir)
    if reader is None:
        return None
    log_str = _normalized_path_string(log_path)
    poll_state_log_str = _normalized_path_string(poll_state.get('log_path'))
    current_reader = state.get('reader')
    reader_log_str = _normalized_path_string(getattr(current_reader, '_preferred_log', None))
    reader_filter = str(getattr(current_reader, '_session_id_filter', '') or '').strip()
    target_filter = str(getattr(reader, '_session_id_filter', '') or '').strip()

    if log_str == poll_state_log_str and log_str == reader_log_str and target_filter == reader_filter:
        return submission

    updated_state = {
        **state,
        'reader': reader,
        'workspace_path': str(work_dir),
    }
    if fallback:
        updated_state['codex_anchor_fallback_log'] = str(log_path)
        updated_state['codex_anchor_fallback_session_id'] = target_filter
    if log_str != poll_state_log_str:
        updated_state['state'] = {
            **poll_state,
            'log_path': log_path,
            'offset': 0,
            'last_rescan': 0.0,
        }
    return replace(submission, runtime_state=updated_state)


def _submission_with_anchor_fallback_diagnostics(
    submission: ProviderSubmission,
    *,
    state: dict[str, object],
    log_path: Path,
    session_id: str,
) -> ProviderSubmission:
    log_str = _normalized_path_string(log_path)
    normalized_session_id = str(session_id or '').strip()
    if (
        str(state.get('codex_anchor_fallback_log') or '').strip() == log_str
        and str(state.get('codex_anchor_fallback_session_id') or '').strip() == normalized_session_id
        and bool(state.get('codex_anchor_fallback_quarantined'))
    ):
        return submission
    updated_state = {
        **state,
        'codex_anchor_fallback_log': log_str,
        'codex_anchor_fallback_session_id': normalized_session_id,
        'codex_anchor_fallback_quarantined': True,
    }
    return replace(submission, runtime_state=updated_state)


def _clear_anchor_fallback_diagnostics(state: dict[str, object]) -> None:
    for key in (
        'codex_anchor_fallback_log',
        'codex_anchor_fallback_session_id',
        'codex_anchor_fallback_quarantined',
    ):
        state.pop(key, None)


def _active_anchor_fallback_log(state: dict[str, object]) -> Path | None:
    raw = str(state.get('codex_anchor_fallback_log') or '').strip()
    if not raw:
        return None
    try:
        path = Path(raw).expanduser()
    except Exception:
        return None
    return path if path.is_file() else None


def _quarantined_anchor_fallback_pending(state: dict[str, object]) -> bool:
    if str(state.get('mode') or '').strip().lower() != 'active':
        return False
    if bool(state.get('anchor_seen') or state.get('no_wrap')):
        return False
    if str(state.get('delivery_state') or '').strip() != 'pending_anchor':
        return False
    return bool(state.get('codex_anchor_fallback_quarantined')) and bool(
        str(state.get('codex_anchor_fallback_log') or '').strip()
    )


def _anchor_fallback_candidate(
    submission: ProviderSubmission,
    *,
    state: dict[str, object],
    poll_state: dict[str, object],
    session,
    work_dir: Path,
    current_log: Path,
) -> SwitchCandidate | None:
    if bool(state.get('anchor_seen') or state.get('no_wrap')):
        return None
    if _current_log_has_unread_data(current_log, poll_state.get('offset')):
        return None
    request_anchor = request_anchor_from_runtime_state(state, fallback=submission.job_id)
    if not request_anchor:
        return None
    data = dict(getattr(session, 'data', None) or {})
    data.setdefault('work_dir', str(work_dir))
    data.setdefault('codex_session_path', str(current_log))
    return select_exact_anchor_candidate(
        data,
        session_file=getattr(session, 'session_file', None),
        request_anchor=request_anchor,
    )


def _commit_exact_anchor_fallback_rebind(session, candidate: SwitchCandidate) -> bool:
    update = getattr(session, 'update_codex_log_binding', None)
    if not callable(update):
        return False
    data_before = _session_data_copy(session)
    before = _session_binding_snapshot(getattr(session, 'data', None))
    old_session_id, old_session_path = before
    try:
        update(
            log_path=str(candidate.path),
            session_id=candidate.session_id,
            post_write_validate=lambda: candidate.path.is_file(),
        )
    except Exception:
        _restore_session_data(session, data_before)
        return False
    after = _session_binding_snapshot(getattr(session, 'data', None))
    if after == before:
        return False
    write_rebound(
        _session_runtime_dir(session),
        candidate=candidate,
        old_session_id=old_session_id,
        old_session_path=old_session_path,
        reason='exact_request_anchor_fallback',
    )
    return True


def _session_data_copy(session) -> dict[str, object] | None:
    data = getattr(session, 'data', None)
    return dict(data) if isinstance(data, dict) else None


def _restore_session_data(session, data_before: dict[str, object] | None) -> None:
    data = getattr(session, 'data', None)
    if data_before is None or not isinstance(data, dict):
        return
    data.clear()
    data.update(data_before)


def _session_binding_snapshot(data: object) -> tuple[str, str]:
    source = data if isinstance(data, dict) else {}
    return (
        str(source.get('codex_session_id') or '').strip(),
        str(source.get('codex_session_path') or '').strip(),
    )


def _session_runtime_dir(session) -> Path | None:
    data = getattr(session, 'data', None)
    raw = data.get('runtime_dir') if isinstance(data, dict) else None
    if not raw:
        return None
    try:
        return Path(str(raw)).expanduser()
    except Exception:
        return None


def _current_log_has_unread_data(log_path: Path, offset: object) -> bool:
    if not isinstance(offset, int) or offset < 0:
        return False
    try:
        return log_path.stat().st_size > offset
    except OSError:
        return False


def _current_log_is_drained(log_path: Path, offset: object) -> bool:
    if not isinstance(offset, int) or offset < 0:
        return False
    try:
        return log_path.stat().st_size <= offset
    except OSError:
        return False


def _normalized_resolved_path(value: object) -> str:
    try:
        return str(Path(value).expanduser().resolve())
    except Exception:
        return _normalized_path_string(value)


def _submission_work_dir(submission: ProviderSubmission, state: dict[str, object]) -> Path | None:
    diagnostics = submission.diagnostics if isinstance(submission.diagnostics, dict) else {}
    raw = state.get('workspace_path') or diagnostics.get('workspace_path')
    if not raw:
        return None
    try:
        return Path(str(raw)).expanduser()
    except Exception:
        return None


def _current_session_log(session) -> Path | None:
    raw = str(getattr(session, 'codex_session_path', '') or '').strip()
    if not raw:
        return None
    try:
        return Path(raw).expanduser()
    except Exception:
        return None


def _normalized_path_string(value: object) -> str:
    if value is None:
        return ''
    try:
        return str(Path(value).expanduser())
    except Exception:
        return str(value or '').strip()


def _load_session(work_dir: Path, agent_name: str):
    from .execution_runtime.start import load_session as _runtime_load_session

    return _runtime_load_session(load_project_session, work_dir, agent_name=agent_name)


def build_execution_adapter() -> CodexProviderAdapter:
    return CodexProviderAdapter()


__all__ = ['CodexProviderAdapter', 'build_execution_adapter']
