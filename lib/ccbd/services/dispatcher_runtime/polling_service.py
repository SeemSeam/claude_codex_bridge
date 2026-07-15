from __future__ import annotations

from dataclasses import replace

from completion.models import CompletionConfidence, CompletionStatus
from completion.tracker import CompletionTrackerView

from .execution_cleanup import finish_stale_execution_update
from .records import append_event, get_job


def poll_completion_updates(dispatcher) -> tuple:
    if dispatcher._execution_service is None:
        return ()
    completed: list = []
    completed_ids: set[str] = set()
    for update in dispatcher._execution_service.poll():
        current = get_job(dispatcher, update.job_id)
        if _skip_update(dispatcher, current, update.job_id):
            continue
        tracked = _ingest_update_items(dispatcher, current, update)
        decision = _resolve_update_decision(dispatcher, update, tracked)
        if decision is not None:
            completed.append(dispatcher.complete(update.job_id, decision))
            completed_ids.add(update.job_id)
        else:
            dispatcher._execution_service.acknowledge(update.job_id)
    _tick_tracker(dispatcher, completed, completed_ids)
    return tuple(completed)


def _skip_update(dispatcher, current, job_id: str) -> bool:
    if current is not None and current.status not in dispatcher._terminal_event_by_status:
        return False
    finish_stale_execution_update(dispatcher, job_id)
    return True


def _ingest_update_items(dispatcher, current, update) -> CompletionTrackerView | None:
    tracker = dispatcher._completion_tracker
    if tracker is not None and tracker.current(update.job_id) is None:
        tracker.start(current, started_at=current.updated_at)
    tracked: CompletionTrackerView | None = None
    for item in update.items:
        append_event(dispatcher, current, 'completion_item', item.to_record(), timestamp=item.timestamp)
        dispatcher._execution_service.acknowledge_item(update.job_id, event_seq=item.cursor.event_seq)
        if tracker is not None:
            tracked = tracker.ingest(update.job_id, item)
            dispatcher._apply_tracker_view(current, tracked, updated_at=item.timestamp)
    if tracked is None and tracker is not None:
        return tracker.current(update.job_id)
    return tracked


def _resolve_update_decision(dispatcher, update, tracked):
    decision = update.decision
    if decision is None and tracked is not None and tracked.decision.terminal:
        decision = tracked.decision
    return _validate_provider_completion_decision(getattr(update, 'submission', None), decision)


def _validate_provider_completion_decision(submission, decision):
    if decision is None:
        return None
    if not decision.terminal or decision.status is not CompletionStatus.COMPLETED:
        return decision
    if not _requires_codex_active_acceptance_gate(submission):
        return decision
    if _is_confirmed_reply_delivery_completion(submission, decision):
        return decision
    if not str(decision.reply or '').strip():
        return _incomplete_provider_completion(
            decision,
            reason='task_complete_empty_reply',
            gate='non_empty_reply',
            diagnostics={'empty_reply': True, 'error_type': 'empty_provider_reply'},
        )
    runtime_state = dict(getattr(submission, 'runtime_state', {}) or {})
    if bool(runtime_state.get('no_wrap')):
        return decision
    delivery_state = str(runtime_state.get('delivery_state') or '').strip().lower()
    anchor_seen = bool(runtime_state.get('anchor_seen') or decision.anchor_seen)
    if delivery_state == 'accepted' and anchor_seen:
        return decision
    reason = (
        'terminal_after_session_rotate_without_anchor'
        if delivery_state == 'accepted'
        else 'terminal_before_provider_acceptance'
    )
    return _incomplete_provider_completion(
        decision,
        reason=reason,
        gate='provider_acceptance',
        diagnostics={
            'delivery_state': delivery_state or '',
            'anchor_seen': anchor_seen,
        },
    )


def _requires_codex_active_acceptance_gate(submission) -> bool:
    if submission is None:
        return False
    if str(getattr(submission, 'provider', '') or '').strip().lower() != 'codex':
        return False
    runtime_state = dict(getattr(submission, 'runtime_state', {}) or {})
    return str(runtime_state.get('mode') or '').strip().lower() == 'active'


def _is_confirmed_reply_delivery_completion(submission, decision) -> bool:
    """Allow an empty Codex transport acknowledgement only with full delivery proof."""
    runtime_state = dict(getattr(submission, 'runtime_state', {}) or {})
    diagnostics = dict(decision.diagnostics or {})
    return (
        bool(runtime_state.get('reply_delivery_complete_on_dispatch'))
        and str(runtime_state.get('delivery_state') or '').strip().lower() == 'accepted'
        and bool(runtime_state.get('anchor_seen') or decision.anchor_seen)
        and str(decision.reason or '').strip().lower() == 'reply_delivery_sent'
        and diagnostics.get('reply_delivery') is True
        and str(diagnostics.get('delivery_status') or '').strip().lower() == 'accepted'
    )


def _incomplete_provider_completion(decision, *, reason: str, gate: str, diagnostics: dict | None = None):
    merged_diagnostics = {
        **dict(decision.diagnostics or {}),
        **dict(diagnostics or {}),
        'completion_gate': gate,
        'original_status': decision.status.value,
        'original_reason': decision.reason or '',
        'suppress_completion_state_merge': True,
    }
    return replace(
        decision,
        status=CompletionStatus.INCOMPLETE,
        reason=reason,
        confidence=CompletionConfidence.DEGRADED,
        reply='',
        anchor_seen=False,
        reply_started=False,
        reply_stable=False,
        diagnostics=merged_diagnostics,
    )


def _tick_tracker(dispatcher, completed: list, completed_ids: set[str]) -> None:
    tracker = dispatcher._completion_tracker
    if tracker is None:
        return
    for tracked in tracker.tick_all(now=dispatcher._clock()):
        current = get_job(dispatcher, tracked.job_id)
        if _skip_tracked_completion(dispatcher, current, tracked.job_id, completed_ids):
            continue
        dispatcher._apply_tracker_view(current, tracked)
        if tracked.decision.terminal:
            submission = _active_submission(dispatcher, tracked.job_id)
            completed.append(
                dispatcher.complete(
                    tracked.job_id,
                    _validate_provider_completion_decision(submission, tracked.decision),
                )
            )
            completed_ids.add(tracked.job_id)


def _active_submission(dispatcher, job_id: str):
    active = getattr(dispatcher._execution_service, '_active', None)
    if not isinstance(active, dict):
        return None
    return active.get(job_id)


def _skip_tracked_completion(dispatcher, current, job_id: str, completed_ids: set[str]) -> bool:
    return current is None or current.status in dispatcher._terminal_event_by_status or job_id in completed_ids


__all__ = ['poll_completion_updates']
