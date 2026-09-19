from __future__ import annotations

from dataclasses import replace
from datetime import datetime
import json
import os
from pathlib import Path

from ccbd.api_models import JobRecord
from completion.models import (
    CompletionConfidence,
    CompletionCursor,
    CompletionDecision,
    CompletionItemKind,
    CompletionSourceKind,
    CompletionStatus,
)
from provider_core.protocol import request_anchor_for_job
from provider_execution.active import ensure_active_pane_alive, prepare_active_start
from provider_execution.base import ProviderPollResult, ProviderRuntimeContext, ProviderSubmission
from provider_execution.common import (
    build_item,
    error_submission,
    interrupt_and_clear_runtime_target,
    no_wrap_requested,
    send_prompt_to_runtime_target,
)
from terminal_runtime import get_backend_for_session

from provider_backends.native_cli_support.prompt import clean_native_reply, wrap_native_prompt

from .session import load_project_session


_MODE = 'grok_pane'
_DEFAULT_TIMEOUT_S = 900.0


class GrokPaneExecutionAdapter:
    provider = 'grok'

    def restore_diagnostics(self) -> dict[str, object]:
        return {
            'resume_supported': False,
            'restore_mode': 'resubmit_required',
            'restore_reason': 'provider_resume_unsupported',
            'restore_detail': (
                'Grok jobs are bound to the managed visible pane and native session events; '
                'interrupted in-flight jobs should be resubmitted'
            ),
        }

    def start(
        self,
        job: JobRecord,
        *,
        context: ProviderRuntimeContext | None,
        now: str,
    ) -> ProviderSubmission:
        prepared = prepare_active_start(
            job,
            context=context,
            provider=self.provider,
            source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
            now=now,
            missing_session_reason='missing_grok_session',
            load_session_fn=_load_session,
            backend_for_session_fn=get_backend_for_session,
        )
        if isinstance(prepared, ProviderSubmission):
            return prepared

        events_root = _events_root(prepared.session.data)
        if events_root is None:
            return error_submission(
                job,
                provider=self.provider,
                now=now,
                source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
                reason='runtime_unavailable',
                error='grok_events_root_missing',
            )

        request_anchor = request_anchor_for_job(job.job_id)
        no_wrap = no_wrap_requested(getattr(job, 'provider_options', None))
        prompt = _pane_prompt(job.request.body or '', request_anchor=request_anchor, no_wrap=no_wrap)
        try:
            offsets = capture_grok_event_offsets(events_root)
            send_prompt_to_runtime_target(prepared.backend, prepared.pane_id, prompt)
        except Exception as exc:
            return error_submission(
                job,
                provider=self.provider,
                now=now,
                source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
                reason='grok_pane_send_failed',
                error=f'{type(exc).__name__}: {exc}',
            )

        reply_delivery = str(job.request.message_type or '').strip().lower() == 'reply_delivery'
        return ProviderSubmission(
            job_id=job.job_id,
            agent_name=job.agent_name,
            provider=self.provider,
            accepted_at=now,
            ready_at=now,
            source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
            reply='',
            diagnostics={
                'provider': self.provider,
                'mode': _MODE,
                'workspace_path': str(prepared.work_dir),
                'pane_id': prepared.pane_id,
            },
            runtime_state={
                'mode': _MODE,
                'backend': prepared.backend,
                'pane_id': prepared.pane_id,
                'request_anchor': request_anchor,
                'events_root': str(events_root),
                'event_offsets': offsets,
                'matched_event_path': '',
                'provider_session_id': '',
                'provider_prompt_id': '',
                'reply_buffer': '',
                'next_seq': 1,
                'anchor_seen': no_wrap,
                'no_wrap': no_wrap,
                'started_at': now,
                'run_timeout_s': _effective_timeout_s(),
                'prompt_sent': True,
                'reply_delivery_complete_on_dispatch': reply_delivery,
            },
        )

    def poll(self, submission: ProviderSubmission, *, now: str) -> ProviderPollResult | None:
        if str(submission.runtime_state.get('mode') or '') != _MODE:
            return None
        state = dict(submission.runtime_state)
        backend = state.get('backend')
        pane_id = str(state.get('pane_id') or '')
        if backend is None or not pane_id:
            return _runtime_error(submission, state, now=now, reason='runtime_state_corrupt')
        pane_dead = ensure_active_pane_alive(submission, backend=backend, pane_id=pane_id, now=now)
        if pane_dead is not None:
            return pane_dead

        # Reply deliveries no longer complete at dispatch: the anchored event
        # stream below owns the delivery turn and holds the slot until the
        # anchored turn terminalizes.

        events_root = Path(str(state.get('events_root') or ''))
        events, offsets = read_new_grok_events(events_root, dict(state.get('event_offsets') or {}))
        state['event_offsets'] = offsets
        items = []
        matched_path = str(state.get('matched_event_path') or '')
        prompt_id = str(state.get('provider_prompt_id') or '')
        terminal_reason = ''

        for event_path, event in events:
            kind, text, event_prompt_id, session_id, stop_reason = _event_fields(event)
            if not matched_path:
                if kind != 'user_message_chunk' or str(state.get('request_anchor') or '') not in text:
                    continue
                matched_path = event_path
                state['matched_event_path'] = matched_path
                state['provider_session_id'] = session_id
                if not bool(state.get('anchor_seen')):
                    items.append(
                        build_item(
                            submission,
                            kind=CompletionItemKind.ANCHOR_SEEN,
                            timestamp=now,
                            seq=_next_seq(state),
                            payload={
                                'turn_id': str(state.get('request_anchor') or submission.job_id),
                                'source': 'grok_visible_session_user_message',
                                'provider_session_id': session_id,
                            },
                        )
                    )
                    state['anchor_seen'] = True
                continue
            if event_path != matched_path:
                continue

            if kind == 'agent_message_chunk' and text:
                if event_prompt_id:
                    if prompt_id and event_prompt_id != prompt_id:
                        continue
                    prompt_id = event_prompt_id
                    state['provider_prompt_id'] = prompt_id
                state['reply_buffer'] = str(state.get('reply_buffer') or '') + text
                continue

            if kind == 'turn_completed':
                if event_prompt_id and prompt_id and event_prompt_id != prompt_id:
                    continue
                if event_prompt_id:
                    prompt_id = event_prompt_id
                    state['provider_prompt_id'] = prompt_id
                terminal_reason = _normalize_reason(stop_reason)
                break

        reply = clean_native_reply(
            str(state.get('reply_buffer') or ''),
            str(state.get('request_anchor') or submission.job_id),
        )
        if reply and reply != submission.reply:
            items.append(
                build_item(
                    submission,
                    kind=CompletionItemKind.ASSISTANT_FINAL,
                    timestamp=now,
                    seq=_next_seq(state),
                    payload={
                        'text': reply,
                        'reply': reply,
                        'final_answer': reply,
                        'turn_id': str(state.get('request_anchor') or submission.job_id),
                        'provider_turn_ref': prompt_id or str(state.get('provider_session_id') or ''),
                        'finish_reason': terminal_reason,
                    },
                )
            )

        updated = replace(submission, reply=reply, runtime_state=state)
        if terminal_reason:
            items.append(
                build_item(
                    updated,
                    kind=CompletionItemKind.TURN_BOUNDARY,
                    timestamp=now,
                    seq=_next_seq(state),
                    payload={
                        'turn_id': str(state.get('request_anchor') or submission.job_id),
                        'provider_turn_ref': prompt_id or str(state.get('provider_session_id') or ''),
                        'finish_reason': terminal_reason,
                        'reason': 'grok_run_stop' if terminal_reason == 'end_turn' else f'grok_run_finished:{terminal_reason}',
                    },
                )
            )
            updated = replace(updated, runtime_state=state)
            return ProviderPollResult(
                submission=updated,
                items=tuple(items),
                decision=_terminal_decision(updated, state, reply=reply, finish_reason=terminal_reason, now=now),
            )

        if _timeout_elapsed(str(state.get('started_at') or ''), now, float(state.get('run_timeout_s') or 0.0)):
            return ProviderPollResult(
                submission=updated,
                items=tuple(items),
                decision=CompletionDecision(
                    terminal=True,
                    status=CompletionStatus.INCOMPLETE,
                    reason='grok_run_timeout',
                    confidence=CompletionConfidence.DEGRADED,
                    reply=reply,
                    anchor_seen=bool(state.get('anchor_seen')),
                    reply_started=bool(reply),
                    reply_stable=False,
                    provider_turn_ref=prompt_id or str(state.get('provider_session_id') or submission.job_id),
                    source_cursor=None,
                    finished_at=now,
                    diagnostics={'mode': _MODE, 'run_timeout_s': state.get('run_timeout_s')},
                ),
            )
        if items or updated != submission:
            return ProviderPollResult(submission=updated, items=tuple(items), decision=None)
        return None

    def cancel(self, submission: ProviderSubmission) -> None:
        backend = submission.runtime_state.get('backend')
        pane_id = str(submission.runtime_state.get('pane_id') or '')
        if backend is not None and pane_id:
            interrupt_and_clear_runtime_target(backend, pane_id)


def capture_grok_event_offsets(events_root: Path) -> dict[str, int]:
    offsets: dict[str, int] = {}
    if not events_root.is_dir():
        return offsets
    for path in events_root.rglob('updates.jsonl'):
        try:
            offsets[str(path)] = path.stat().st_size
        except OSError:
            continue
    return offsets


def read_new_grok_events(
    events_root: Path,
    offsets: dict[str, int],
) -> tuple[list[tuple[str, dict]], dict[str, int]]:
    next_offsets = dict(offsets)
    events: list[tuple[str, dict]] = []
    if not events_root.is_dir():
        return events, next_offsets
    paths = sorted(events_root.rglob('updates.jsonl'), key=_safe_mtime)
    for path in paths:
        key = str(path)
        offset = max(0, int(next_offsets.get(key, 0)))
        try:
            with path.open('rb') as handle:
                handle.seek(offset)
                chunk = handle.read()
        except OSError:
            continue
        complete_end = chunk.rfind(b'\n') + 1
        if complete_end <= 0:
            continue
        next_offsets[key] = offset + complete_end
        for raw_line in chunk[:complete_end].splitlines():
            try:
                payload = json.loads(raw_line.decode('utf-8', errors='replace'))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if isinstance(payload, dict):
                events.append((key, payload))
    return events, next_offsets


def _load_session(work_dir: Path, *, agent_name: str):
    return load_project_session(work_dir, instance=agent_name)


def _pane_prompt(body: str, *, request_anchor: str, no_wrap: bool) -> str:
    if no_wrap:
        return body
    return wrap_native_prompt(body, request_anchor)


def _events_root(session_data: dict) -> Path | None:
    raw = str(session_data.get('grok_home') or '').strip()
    return Path(raw).expanduser() / '.grok' / 'sessions' if raw else None


def _event_fields(event: dict) -> tuple[str, str, str, str, str]:
    params = event.get('params')
    if not isinstance(params, dict):
        return '', '', '', '', ''
    update = params.get('update')
    if not isinstance(update, dict):
        return '', '', '', '', ''
    kind = str(update.get('sessionUpdate') or '').strip().lower().replace('-', '_')
    content = update.get('content')
    text = _content_text(content)
    meta = event.get('_meta')
    if not isinstance(meta, dict):
        meta = params.get('_meta') if isinstance(params.get('_meta'), dict) else {}
    prompt_id = str(update.get('prompt_id') or meta.get('promptId') or '').strip()
    session_id = str(params.get('sessionId') or '').strip()
    stop_reason = str(update.get('stop_reason') or '').strip()
    return kind, text, prompt_id, session_id, stop_reason


def _content_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return ''.join(_content_text(item) for item in value)
    if not isinstance(value, dict):
        return ''
    if isinstance(value.get('text'), str):
        return str(value['text'])
    return _content_text(value.get('content'))



def _terminal_decision(
    submission: ProviderSubmission,
    state: dict[str, object],
    *,
    reply: str,
    finish_reason: str,
    now: str,
) -> CompletionDecision:
    if finish_reason == 'end_turn' and reply:
        status = CompletionStatus.COMPLETED
        reason = 'grok_run_stop'
        confidence = CompletionConfidence.OBSERVED
    elif finish_reason == 'end_turn':
        status = CompletionStatus.INCOMPLETE
        reason = 'grok_empty_reply'
        confidence = CompletionConfidence.DEGRADED
    else:
        status = CompletionStatus.INCOMPLETE
        reason = f'grok_run_finished:{finish_reason or "unknown"}'
        confidence = CompletionConfidence.OBSERVED
    return CompletionDecision(
        terminal=True,
        status=status,
        reason=reason,
        confidence=confidence,
        reply=reply,
        anchor_seen=bool(state.get('anchor_seen')),
        reply_started=bool(reply),
        reply_stable=True,
        provider_turn_ref=str(
            state.get('provider_prompt_id')
            or state.get('provider_session_id')
            or submission.job_id
        ),
        source_cursor=CompletionCursor(
            source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
            event_seq=max(0, int(state.get('next_seq') or 1) - 1),
            updated_at=now,
        ),
        finished_at=now,
        diagnostics={
            'mode': _MODE,
            'finish_reason': finish_reason,
            'events_path': str(state.get('matched_event_path') or ''),
            'provider_session_id': str(state.get('provider_session_id') or ''),
        },
    )


def _runtime_error(
    submission: ProviderSubmission,
    state: dict[str, object],
    *,
    now: str,
    reason: str,
) -> ProviderPollResult:
    return ProviderPollResult(
        submission=replace(submission, runtime_state=state),
        decision=CompletionDecision(
            terminal=True,
            status=CompletionStatus.INCOMPLETE,
            reason=reason,
            confidence=CompletionConfidence.DEGRADED,
            reply=submission.reply,
            anchor_seen=bool(state.get('anchor_seen')),
            reply_started=bool(submission.reply),
            reply_stable=False,
            provider_turn_ref=submission.job_id,
            source_cursor=None,
            finished_at=now,
            diagnostics={'mode': _MODE},
        ),
    )


def _next_seq(state: dict[str, object]) -> int:
    seq = max(1, int(state.get('next_seq') or 1))
    state['next_seq'] = seq + 1
    return seq


def _safe_mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def _normalize_reason(value: object) -> str:
    return str(value or '').strip().lower().replace('-', '_')


def _effective_timeout_s() -> float:
    raw = str(os.environ.get('CCB_GROK_RUN_TIMEOUT_S') or '').strip()
    if raw:
        try:
            return max(0.0, float(raw))
        except ValueError:
            pass
    return _DEFAULT_TIMEOUT_S


def _timeout_elapsed(started_at: str, now: str, timeout_s: float) -> bool:
    if timeout_s <= 0 or not started_at or not now:
        return False
    try:
        start = datetime.fromisoformat(started_at.replace('Z', '+00:00'))
        current = datetime.fromisoformat(now.replace('Z', '+00:00'))
    except ValueError:
        return False
    return (current - start).total_seconds() >= timeout_s


__all__ = [
    'GrokPaneExecutionAdapter',
    'capture_grok_event_offsets',
    'read_new_grok_events',
]
