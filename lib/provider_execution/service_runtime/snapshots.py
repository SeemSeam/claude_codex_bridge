from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from provider_runtime.session_payload import completion_source_for_kind

from .reliability import deadline_at, last_progress_timestamp, timeout_policy_for

_RUNTIME_STATE_KEYS = (
    'mode',
    'pane_id',
    'pane_ref',
    'request_anchor',
    'next_seq',
    'anchor_seen',
    'anchor_emitted',
    'no_wrap',
    'bound_turn_id',
    'bound_task_id',
    'codex_app_server_enabled',
    'codex_app_server_socket',
    'codex_app_server_remote_marker',
    'active_followup_ids',
    'last_assistant_uuid',
    'active_assistant_message_id',
    'active_assistant_stop_reason',
    'active_assistant_has_tool_use',
    'session_path',
    'completion_dir',
    'prompt_sent',
    'prompt_sent_at',
    'draft_guard_enabled',
    'draft_guard_pending',
    'draft_guard_reason',
    'draft_guard_send_unknown',
    'ready_wait_started_at',
    'ready_timeout_s',
    'delivery_state',
    'delivery_started_at',
    'delivery_last_progress_at',
    'delivery_progress_kind',
    'delivery_session_missing_since',
    'delivery_timeout_s',
    'delivery_no_progress_deadline_at',
    'delivery_target_pane_id',
    'delivery_target_session_path',
    'delivery_confirmed_at',
    'delivery_failure_kind',
    'delivery_failed_at',
    'reliability_last_progress_at',
    'reliability_timeout_s',
    'reliability_timeout_deadline_at',
)
_PANE_REF_PUBLIC_KEYS = frozenset(
    {
        'backend_impl',
        'pane_id',
        'session_name',
        'window_name',
        'agent_slug',
    }
)


def active_runtime_snapshots(service) -> tuple[dict[str, object], ...]:
    snapshots: list[dict[str, object]] = []
    now = service._clock()
    for job_id, submission in sorted(service._active.items()):
        adapter = service._registry.get(submission.provider)
        policy = timeout_policy_for(service, job_id=job_id, adapter=adapter) if adapter is not None else None
        runtime_state = _safe_runtime_state(submission.runtime_state)
        delivery_timeout_s = _optional_float(runtime_state.get('delivery_timeout_s'))
        delivery_started_at = str(runtime_state.get('delivery_started_at') or '').strip()
        delivery_last_progress_at = str(runtime_state.get('delivery_last_progress_at') or '').strip()
        delivery_deadline_base = delivery_last_progress_at or delivery_started_at
        if delivery_deadline_base and delivery_timeout_s is not None:
            runtime_state['delivery_timeout_deadline_at'] = deadline_at(
                delivery_deadline_base,
                timeout_s=delivery_timeout_s,
            )
            runtime_state['delivery_no_progress_deadline_at'] = runtime_state['delivery_timeout_deadline_at']

        last_progress_at = last_progress_timestamp(submission)
        no_terminal_timeout_s = policy.effective_no_terminal_timeout_s() if policy is not None else None
        no_terminal_deadline_at = (
            deadline_at(last_progress_at, timeout_s=no_terminal_timeout_s)
            if last_progress_at and no_terminal_timeout_s is not None
            else None
        )
        source_kind = getattr(submission.source_kind, 'value', submission.source_kind)
        status = getattr(submission.status, 'value', submission.status)
        confidence = getattr(submission.confidence, 'value', submission.confidence)
        snapshots.append(
            {
                'job_id': submission.job_id,
                'agent_name': submission.agent_name,
                'provider': submission.provider,
                'source_kind': source_kind,
                'completion_source': completion_source_for_kind(source_kind),
                'completion_source_kind': source_kind,
                'status': status,
                'reason': submission.reason,
                'confidence': confidence,
                'accepted_at': submission.accepted_at,
                'ready_at': submission.ready_at,
                'primary_authority': getattr(policy, 'primary_authority', None) if policy is not None else None,
                'last_progress_at': last_progress_at or None,
                'no_terminal_timeout_s': no_terminal_timeout_s,
                'no_terminal_deadline_at': no_terminal_deadline_at,
                'runtime_state': runtime_state,
            }
        )
    return tuple(snapshots)


def _safe_runtime_state(runtime_state: Mapping[str, object] | None) -> dict[str, object]:
    if not isinstance(runtime_state, Mapping):
        return {}
    result: dict[str, object] = {}
    for key in _RUNTIME_STATE_KEYS:
        value = runtime_state.get(key)
        safe = _safe_pane_ref(value) if key == 'pane_ref' else _safe_value(value)
        if safe is not _UNSAFE:
            result[key] = safe
    return result


class _Unsafe:
    pass


_UNSAFE = _Unsafe()


def _safe_value(value: object) -> object:
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return _UNSAFE


def _safe_pane_ref(value: object) -> object:
    if not isinstance(value, Mapping):
        return _UNSAFE
    result: dict[str, object] = {}
    for key in _PANE_REF_PUBLIC_KEYS:
        item = value.get(key)
        safe = _safe_value(item)
        if safe is not _UNSAFE:
            result[key] = safe
    return result if result.get('pane_id') else _UNSAFE


def _optional_float(value: object) -> float | None:
    if value is None or value == '':
        return None
    try:
        return float(value)
    except Exception:
        return None


__all__ = ['active_runtime_snapshots']
