from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path

from ccbd.system import parse_utc_timestamp
from ccbd.api_models import JobRecord
from completion.models import CompletionConfidence, CompletionDecision, CompletionItemKind, CompletionStatus
from provider_backends.codex.comm_runtime.binding import extract_cwd_from_log_file, extract_session_id
from provider_backends.codex.comm_runtime.pathing import normalize_work_dir
from provider_core.protocol import REQ_ID_PREFIX, request_anchor_for_job, wrap_codex_turn_prompt
from provider_execution.base import ProviderPollResult, ProviderRuntimeContext, ProviderSubmission
from provider_execution.common import build_item, is_runtime_target_alive, request_anchor_from_runtime_state
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


CODEX_NO_TERMINAL_TIMEOUT_SECS = 900.0


class CodexProviderAdapter:
    provider = 'codex'
    completion_reliability_policy = CompletionReliabilityPolicy(
        provider='codex',
        primary_authority='protocol_log',
        no_terminal_timeout_s=CODEX_NO_TERMINAL_TIMEOUT_SECS,
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
        submission = _refresh_reader_for_current_session_binding(submission)
        crashed = _codex_runtime_crashed_result(submission, now=now)
        if crashed is not None:
            return crashed
        provider_signal = _codex_provider_signal_terminal_result(submission, now=now)
        if provider_signal is not None:
            return provider_signal
        delivery_failure = _delivery_acceptance_guard(submission, now=now)
        if delivery_failure is not None:
            return delivery_failure
        return _poll_submission(submission, now=now)

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
            'delivery_timeout_s': submission.runtime_state.get('delivery_timeout_s'),
            'delivery_target_pane_id': submission.runtime_state.get('delivery_target_pane_id'),
            'delivery_target_session_path': submission.runtime_state.get('delivery_target_session_path'),
            'delivery_confirmed_at': submission.runtime_state.get('delivery_confirmed_at'),
            'delivery_failure_kind': submission.runtime_state.get('delivery_failure_kind'),
            'delivery_failed_at': submission.runtime_state.get('delivery_failed_at'),
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
    kwargs: dict[str, object] = {
        "log_path": preferred_log if preferred_log is not None else default_log,
        "session_id_filter": session.codex_session_id or None,
        "work_dir": work_dir,
        "follow_workspace_sessions": should_follow_workspace_sessions(
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

    current_log_str = _normalized_path_string(current_log)
    poll_state = dict(state.get('state') or {})
    poll_state_log_str = _normalized_path_string(poll_state.get('log_path'))
    reader = state.get('reader')
    reader_log_str = _normalized_path_string(getattr(reader, '_preferred_log', None))
    reader_filter = str(getattr(reader, '_session_id_filter', '') or '').strip()
    current_filter = str(getattr(session, 'codex_session_id', '') or '').strip()

    fallback_log = _active_anchor_fallback_log(state)
    if fallback_log is not None:
        updated = _submission_with_locked_reader(
            submission,
            state=state,
            poll_state=poll_state,
            session=session,
            work_dir=work_dir,
            log_path=fallback_log,
            fallback=True,
        )
        if updated is not None:
            return updated

    anchor_fallback = _anchor_fallback_log(
        submission,
        state=state,
        poll_state=poll_state,
        session=session,
        work_dir=work_dir,
        current_log=current_log,
    )
    if anchor_fallback is not None:
        updated = _submission_with_locked_reader(
            submission,
            state=state,
            poll_state=poll_state,
            session=session,
            work_dir=work_dir,
            log_path=anchor_fallback,
            fallback=True,
        )
        if updated is not None:
            return updated

    if (
        current_log_str == poll_state_log_str
        and current_log_str == reader_log_str
        and (not current_filter or current_filter == reader_filter)
    ):
        return submission

    updated_state = {
        **state,
        'reader': _reader_factory(session, current_log),
        'workspace_path': str(work_dir),
    }
    if current_log_str != poll_state_log_str:
        updated_state['state'] = {
            **poll_state,
            'log_path': current_log,
            'offset': 0,
            'last_rescan': 0.0,
        }
    return replace(submission, runtime_state=updated_state)


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

    failure_kind = _delivery_failure_kind(state, submission=submission, now=now)
    if not failure_kind:
        return None

    work_dir = _submission_work_dir(submission, state)
    if work_dir is None:
        return None
    session = _load_session(work_dir, submission.agent_name)
    if session is None:
        return None
    current_log = _current_session_log(session)
    if current_log is None or not current_log.exists():
        return None
    checked_root = codex_session_root_path(getattr(session, 'data', None))

    poll_state = dict(state.get('state') or {})
    if not _current_log_is_drained(current_log, poll_state.get('offset')):
        return None
    if _active_anchor_fallback_log(state) is not None:
        return None
    if _anchor_fallback_log(
        submission,
        state=state,
        poll_state=poll_state,
        session=session,
        work_dir=work_dir,
        current_log=current_log,
    ) is not None:
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


# Maps a parsed codex pane signal state to an attributable error_kind for
# delivery/attempt diagnostics. None means "no content error worth tagging".
_PANE_SIGNAL_ERROR_KIND = {
    'usage_limit': 'provider_usage_limit',
    'api_error': 'provider_api_error',
    'auth_failed': 'provider_auth_failed',
    'config_error': 'provider_config_error',
    'failed': 'provider_error',
    'auth_required': 'provider_auth_required',
}


def _delivery_pane_signal(state: dict[str, object]) -> dict[str, object] | None:
    """Capture and parse pane content during a delivery failure.

    Returns a diagnostics fragment ({error_kind, pane_tail, retry_after?}) when
    the pane shows a classified provider error, or None otherwise. Keeps the
    delivery path's existing failure classification untouched.
    """
    backend = state.get('backend')
    pane_id = str(state.get('pane_id') or state.get('delivery_target_pane_id') or '').strip()
    get_pane_content = getattr(backend, 'get_pane_content', None)
    if not pane_id or not callable(get_pane_content):
        return None
    try:
        content = str(get_pane_content(pane_id, lines=120) or '')
    except Exception:
        return None
    if not content.strip():
        return None
    try:
        from provider_pane_status.codex_pane import parse_codex_pane_status
    except Exception:
        return None
    parsed = parse_codex_pane_status(content)
    error_kind = _PANE_SIGNAL_ERROR_KIND.get(parsed.state)
    if error_kind is None:
        return None
    tail_lines = [line.rstrip() for line in content.splitlines() if line.strip()]
    pane_tail = '\n'.join(tail_lines[-20:]) if tail_lines else None
    fragment: dict[str, object] = {
        'error_kind': error_kind,
        'pane_signal_state': parsed.state,
        'pane_signal_reason': parsed.reason,
    }
    if pane_tail:
        fragment['pane_tail'] = pane_tail
    if parsed.retry_after:
        fragment['retry_after'] = parsed.retry_after
    return fragment


def _delivery_timeout_elapsed(state: dict[str, object], *, submission: ProviderSubmission, now: str) -> bool:
    timeout_s = _delivery_timeout_s(state)
    if timeout_s <= 0:
        return False
    started_at = str(state.get('delivery_started_at') or submission.ready_at or submission.accepted_at or '').strip()
    if not started_at:
        return False
    try:
        elapsed = (parse_utc_timestamp(now) - parse_utc_timestamp(started_at)).total_seconds()
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
        'delivery_timeout_s': _delivery_timeout_s(state),
        'delivery_checked_session_root': str(checked_root or current_log.parent),
        'delivery_current_log_path': str(current_log),
        'delivery_workspace_path': str(work_dir),
        'delivery_anchor_seen': False,
    }
    # When the pane content shows a provider error banner (usage-limit / auth /
    # api), surface it on the delivery diagnostics so the failure is
    # attributable instead of looking like a generic delivery timeout.
    pane_signal = _delivery_pane_signal(state)
    if pane_signal is not None:
        diagnostics.update(pane_signal)
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


# Pane signal states that should terminalize the job on the normal poll path.
# The delivery path continues to use _PANE_SIGNAL_ERROR_KIND for diagnostics,
# but these specific provider-side failures are attributable early.
_NORMAL_POLL_PANE_ERROR_KINDS = {
    'provider_usage_limit',
    'provider_auth_failed',
    'provider_api_error',
    'provider_config_error',
    'provider_waiting_for_user',
}

_NORMAL_POLL_PANE_STATE_TO_REASON = {
    'usage_limit': 'provider_usage_limit',
    'auth_failed': 'provider_auth_failed',
    'api_error': 'provider_api_error',
    'config_error': 'provider_config_error',
    'auth_required': 'provider_waiting_for_user',
}


def _codex_runtime_crashed_result(
    submission: ProviderSubmission,
    *,
    now: str,
) -> ProviderPollResult | None:
    """Terminalize with provider_crashed if the codex pane or runtime_pid is gone.

    Reuses the shared ``pane_dead_result`` so the emitted item keeps the
    ``CompletionItemKind.PANE_DEAD`` kind that downstream detectors and tests
    rely on, while carrying a codex-specific ``no_reply_reason`` of
    ``provider_crashed`` (the codex pane dying mid-turn is a provider crash,
    distinct from a generic unreachable agent).
    """
    from provider_execution.active_runtime.polling_runtime import pane_dead_result

    state = dict(submission.runtime_state)
    if str(state.get('mode') or '').strip().lower() != 'active':
        return None
    backend = state.get('backend')
    pane_id = str(state.get('pane_id') or '').strip()
    if not backend or not pane_id:
        return None
    try:
        pane_alive = is_runtime_target_alive(backend, pane_id)
    except Exception:
        pane_alive = False
    runtime_pid = _coerce_pid(state.get('runtime_pid'))
    pid_alive = _pid_alive(runtime_pid) if runtime_pid else True
    if pane_alive and pid_alive:
        return None
    return pane_dead_result(
        submission,
        now=now,
        reason='pane_dead',
        no_reply_reason='provider_crashed',
        no_reply_detail={
            'pane_alive': pane_alive,
            'runtime_pid': runtime_pid,
            'pid_alive': pid_alive,
        },
    )


def _codex_provider_signal_terminal_result(
    submission: ProviderSubmission,
    *,
    now: str,
) -> ProviderPollResult | None:
    """Terminalize early when the codex pane shows a HIGH-CONFIDENCE provider error.

    Uses the strict/high-confidence marker tier only: a healthy agent whose
    output merely *discusses* usage limits / quotas / api errors must NEVER be
    terminalized here. Broad marker matches stay available to the diagnostics
    path (_delivery_pane_signal) for attributing an already-failed delivery.
    """
    state = dict(submission.runtime_state)
    if str(state.get('mode') or '').strip().lower() != 'active':
        return None
    backend = state.get('backend')
    pane_id = str(state.get('pane_id') or '').strip()
    if not backend or not pane_id:
        return None
    signal = _read_codex_pane_signal(backend, pane_id, strict=True)
    if signal is None:
        return None
    error_kind = str(signal.get('error_kind') or '').strip()
    if error_kind not in _NORMAL_POLL_PANE_ERROR_KINDS:
        return None
    reason = _NORMAL_POLL_PANE_STATE_TO_REASON.get(
        str(signal.get('pane_signal_state') or '').strip(), error_kind
    )
    extra_diagnostics: dict[str, object] = {
        'pane_signal_state': signal.get('pane_signal_state'),
        'pane_signal_reason': signal.get('pane_signal_reason'),
    }
    pane_tail = signal.get('pane_tail')
    if pane_tail:
        extra_diagnostics['pane_tail'] = pane_tail
    retry_after = signal.get('retry_after')
    if retry_after:
        extra_diagnostics['retry_after'] = retry_after
    return _codex_terminal_result(
        submission,
        now=now,
        reason=reason,
        error_kind=error_kind,
        status=CompletionStatus.FAILED,
        extra_diagnostics=extra_diagnostics,
    )


def _codex_terminal_result(
    submission: ProviderSubmission,
    *,
    now: str,
    reason: str,
    error_kind: str,
    status: CompletionStatus,
    extra_diagnostics: dict[str, object],
) -> ProviderPollResult:
    state = dict(submission.runtime_state)
    seq = int(state.get('next_seq', 1))
    request_anchor = request_anchor_from_runtime_state(state, fallback=submission.job_id)
    diagnostics = {
        **dict(submission.diagnostics or {}),
        'reason': reason,
        'error_kind': error_kind,
    }
    diagnostics.update(extra_diagnostics)
    item = build_item(
        submission,
        kind=CompletionItemKind.ERROR,
        timestamp=now,
        seq=seq,
        payload={'reason': reason, 'error_kind': error_kind},
    )
    updated_state = {
        **state,
        'mode': 'passive',
        'next_seq': item.cursor.event_seq + 1,
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
            status=status,
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


def _read_codex_pane_signal(
    backend: object,
    pane_id: str,
    *,
    strict: bool = False,
) -> dict[str, object] | None:
    """Parse pane content into a provider-error signal fragment.

    ``strict`` selects the codex_pane marker tier:

    * ``strict=False`` (default): broad markers. Used by the DIAGNOSTICS path
      (_delivery_pane_signal) where a delivery failure is already established.
    * ``strict=True``: high-confidence markers only. Used by the AUTO-
      TERMINALIZATION poll path (_codex_provider_signal_terminal_result) so a
      healthy agent discussing usage limits / quotas / api errors is not killed.
    """
    get_pane_content = getattr(backend, 'get_pane_content', None)
    if not callable(get_pane_content):
        return None
    try:
        content = str(get_pane_content(pane_id, lines=120) or '')
    except Exception:
        return None
    if not content.strip():
        return None
    try:
        from provider_pane_status.codex_pane import parse_codex_pane_status
    except Exception:
        return None
    parsed = parse_codex_pane_status(content, strict=strict)
    error_kind = _PANE_SIGNAL_ERROR_KIND.get(parsed.state)
    if error_kind is None:
        return None
    if parsed.state == 'auth_required':
        error_kind = 'provider_waiting_for_user'
    tail_lines = [line.rstrip() for line in content.splitlines() if line.strip()]
    pane_tail = '\n'.join(tail_lines[-20:]) if tail_lines else None
    fragment: dict[str, object] = {
        'error_kind': error_kind,
        'pane_signal_state': parsed.state,
        'pane_signal_reason': parsed.reason,
    }
    if pane_tail:
        fragment['pane_tail'] = pane_tail
    if parsed.retry_after:
        fragment['retry_after'] = parsed.retry_after
    return fragment


def _coerce_pid(value: object) -> int | None:
    try:
        pid = int(value or 0)
    except (TypeError, ValueError):
        return None
    return pid if pid > 0 else None


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


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


def _active_anchor_fallback_log(state: dict[str, object]) -> Path | None:
    raw = str(state.get('codex_anchor_fallback_log') or '').strip()
    if not raw:
        return None
    try:
        path = Path(raw).expanduser()
    except Exception:
        return None
    return path if path.is_file() else None


def _anchor_fallback_log(
    submission: ProviderSubmission,
    *,
    state: dict[str, object],
    poll_state: dict[str, object],
    session,
    work_dir: Path,
    current_log: Path,
) -> Path | None:
    if bool(state.get('anchor_seen') or state.get('no_wrap')):
        return None
    if _current_log_has_unread_data(current_log, poll_state.get('offset')):
        return None
    request_anchor = request_anchor_from_runtime_state(state, fallback=submission.job_id)
    if not request_anchor:
        return None
    root = codex_session_root_path(getattr(session, 'data', None))
    if root is None or not root.is_dir():
        return None
    target_work_dir = normalize_work_dir(work_dir)
    if not target_work_dir:
        return None

    current_path = _normalized_resolved_path(current_log)
    matches: list[Path] = []
    try:
        candidates = sorted(root.glob('**/*.jsonl'))
    except OSError:
        return None
    for candidate in candidates:
        if not candidate.is_file():
            continue
        if _normalized_resolved_path(candidate) == current_path:
            continue
        if not _log_matches_work_dir(candidate, target_work_dir):
            continue
        if not extract_session_id(candidate):
            continue
        if _log_contains_request_anchor(candidate, request_anchor):
            matches.append(candidate)
            if len(matches) > 1:
                return None
    if len(matches) != 1:
        return None
    return matches[0]


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


def _log_matches_work_dir(log_path: Path, target_work_dir: str) -> bool:
    raw = extract_cwd_from_log_file(log_path)
    if not raw:
        return False
    try:
        return normalize_work_dir(Path(raw).expanduser()) == target_work_dir
    except Exception:
        return False


def _log_contains_request_anchor(log_path: Path, request_anchor: str) -> bool:
    needle = f'{REQ_ID_PREFIX} {request_anchor}'
    try:
        with log_path.open('r', encoding='utf-8-sig', errors='ignore') as handle:
            for line in handle:
                if needle in line:
                    return True
    except OSError:
        return False
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
