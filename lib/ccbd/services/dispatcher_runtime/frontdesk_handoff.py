from __future__ import annotations

from .workflow_policy import agentic_loop_enabled

import hashlib
import json
from pathlib import Path
import re

from ccbd.api_models import JobRecord
from completion.models import CompletionConfidence, CompletionDecision, CompletionStatus
from storage.atomic import atomic_write_json

from .records import append_event

_PLAN_SLUG_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*$')
_IMPLEMENTATION_VERB_RE = re.compile(
    r'\b('
    r'add|build|change|create|delete|edit|fix|generate|implement|modify|patch|'
    r'run|test|update|verify|write'
    r')\b',
    re.IGNORECASE,
)
_PROJECT_ARTIFACT_RE = re.compile(
    r'(?i)'
    r'('
    r'\b(?:artifact|build|cli|code|config|doc|docs|documentation|file|files|'
    r'implementation|module|package|script|source|test|tests)\b'
    r'|(?:^|[\s`"\'])[\w./-]+/[\w./-]+\.[A-Za-z0-9]{1,12}\b'
    r'|\b(?:README|AGENTS|pyproject\.toml|package\.json|setup\.py)\b'
    r')'
)


def enforce_frontdesk_boundary(
    dispatcher,
    current: JobRecord,
    decision: CompletionDecision,
    *,
    finished_at: str,
) -> CompletionDecision:
    if not agentic_loop_enabled(dispatcher):
        return decision
    if current.agent_name != 'frontdesk':
        return decision
    if str(getattr(current.request, 'message_type', '') or '') != 'ask':
        return decision
    if not decision.terminal or decision.status is not CompletionStatus.COMPLETED:
        return decision
    if _has_persisted_direct_handoff(dispatcher, current):
        return decision
    reply = decision.reply or ''
    if not _frontdesk_intake_missing_fields(reply):
        return decision
    request_body = str(getattr(current.request, 'body', '') or '')
    if not _frontdesk_request_requires_planner_handoff(request_body):
        return decision
    reason = 'frontdesk_direct_implementation_boundary_violation'
    marker_path = _boundary_marker_path(dispatcher, current.job_id)
    payload = {
        'schema_version': 1,
        'record_type': 'ccb_frontdesk_boundary_violation',
        'status': 'blocked',
        'job_id': current.job_id,
        'agent_name': current.agent_name,
        'project_root': str(dispatcher._layout.project_root),
        'reason': reason,
        'required_action': 'return_intake_evidence_for_planner_handoff',
        'request_preview': request_body.strip()[:500],
        'reply_preview': reply.strip()[:500],
        'missing_intake_fields': _frontdesk_intake_missing_fields(reply),
        'recorded_at': finished_at,
    }
    atomic_write_json(marker_path, payload)
    append_event(
        dispatcher,
        current,
        'frontdesk_boundary_violation',
        {**payload, 'marker_path': str(marker_path)},
        timestamp=finished_at,
    )
    diagnostics = dict(decision.diagnostics)
    diagnostics.update(
        {
            'frontdesk_boundary_violation': True,
            'boundary_marker_path': str(marker_path),
            'required_action': payload['required_action'],
            'original_status': decision.status.value,
            'missing_intake_fields': payload['missing_intake_fields'],
        }
    )
    return CompletionDecision(
        terminal=True,
        status=CompletionStatus.FAILED,
        reason=reason,
        confidence=CompletionConfidence.EXACT,
        reply=(
            'Frontdesk boundary violation: implementation-like user requests must '
            'be returned as Intake Evidence or Blocked Evidence for controlled '
            'planner handoff; frontdesk must not directly implement project artifacts.'
        ),
        anchor_seen=decision.anchor_seen,
        reply_started=decision.reply_started,
        reply_stable=decision.reply_stable,
        provider_turn_ref=decision.provider_turn_ref,
        source_cursor=decision.source_cursor,
        finished_at=finished_at,
        diagnostics=diagnostics,
    )


def _has_persisted_direct_handoff(dispatcher, current: JobRecord) -> bool:
    activation_id = f'act-frontdesk-{current.job_id}'
    path = (
        Path(dispatcher._layout.project_root)
        / '.ccb'
        / 'runtime'
        / 'loops'
        / 'activations'
        / f'{activation_id}.json'
    )
    try:
        activation = json.loads(path.read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return False
    if not isinstance(activation, dict):
        return False
    direct = activation.get('direct_ask') if isinstance(activation.get('direct_ask'), dict) else {}
    ask = activation.get('ask') if isinstance(activation.get('ask'), dict) else {}
    planner_job_id = str(ask.get('job_id') or '').strip()
    if not planner_job_id:
        return False
    planner_job = dispatcher.get(planner_job_id)
    if planner_job is None:
        return False
    planner_request = planner_job.request
    intake_sha256 = hashlib.sha256(str(planner_request.body or '').encode('utf-8')).hexdigest()
    return bool(
        activation.get('record_type') == 'ccb_loop_frontdesk_planner_activation'
        and activation.get('source') == 'frontdesk_direct_silence_ask'
        and activation.get('status') == 'planner_submitted'
        and str(activation.get('project_id') or '') == str(current.request.project_id)
        and str(activation.get('request_id') or '') == current.job_id
        and str(activation.get('activation_id') or '') == activation_id
        and str(direct.get('from_actor') or '') == 'frontdesk'
        and str(direct.get('target') or '') == 'planner'
        and bool(direct.get('silence'))
        and str(direct.get('task_id') or '') == activation_id
        and direct.get('controller_rewrote_body') is False
        and str(direct.get('body_sha256') or '') == intake_sha256
        and str(activation.get('intake_sha256') or '') == intake_sha256
        and str(ask.get('target') or '') == 'planner'
        and str(ask.get('sender') or '') == 'frontdesk'
        and bool(ask.get('silence'))
        and str(planner_request.from_actor or '') == 'frontdesk'
        and str(planner_request.to_agent or '') == 'planner'
        and str(planner_request.task_id or '') == activation_id
        and bool(planner_request.silence_on_success)
    )


def _frontdesk_request_requires_planner_handoff(text: str) -> bool:
    return bool(_IMPLEMENTATION_VERB_RE.search(text or '') and _PROJECT_ARTIFACT_RE.search(text or ''))


def _boundary_marker_path(dispatcher, job_id: str) -> Path:
    path = Path(dispatcher._layout.project_root) / '.ccb' / 'runtime' / 'frontdesk-boundary' / f'{job_id}.json'
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _has_heading(text: str, heading: str) -> bool:
    escaped = re.escape(heading)
    return bool(re.search(rf'(?mi)^\s*(?:#+\s*)?(?:\*\*)?\s*{escaped}\s*(?:\*\*)?\s*$', text))


def _has_label(text: str, label: str) -> bool:
    escaped = re.escape(label)
    return bool(re.search(rf'(?mi)^\s*(?:[-*]\s*)?(?:\*\*)?\s*{escaped}\s*(?:\*\*)?\s*:', text))


def _has_structured_blocked_evidence(reply: str) -> bool:
    return (
        _has_heading(reply, 'Blocked Evidence')
        and _has_label(reply, 'Requested validation')
        and _has_label(reply, 'Blocker')
        and _has_label(reply, 'Routing recommendation')
        and (
            _has_label(reply, 'Prohibited actions')
            or _has_label(reply, 'Constraints')
            or _has_label(reply, 'Required behavior')
        )
    )


def _frontdesk_intake_missing_fields(reply: str) -> list[str]:
    has_blocked_evidence = _has_structured_blocked_evidence(reply)
    has_request_detail = any(
        (
            _has_heading(reply, 'Macro Task Request'),
            _has_heading(reply, 'User Request'),
            _has_heading(reply, 'User Intent'),
            _has_label(reply, 'Requested validation'),
            _has_label(reply, 'Macro request'),
            _has_label(reply, 'User request'),
            _has_label(reply, 'User intent'),
        )
    )
    has_legacy_contract = _has_heading(reply, 'Execution Contract') or _has_heading(reply, 'Acceptance Criteria')
    has_intake_contract = _has_label(reply, 'Required behavior') and (
        _has_label(reply, 'Scope') or _has_label(reply, 'Constraints')
    )
    has_request_anchor = any(
        (
            _has_heading(reply, 'Macro Task Request'),
            _has_heading(reply, 'User Request'),
            _has_heading(reply, 'Intake Evidence'),
            has_blocked_evidence,
            has_request_detail and (has_legacy_contract or has_intake_contract),
        )
    )
    missing = []
    if not has_request_anchor:
        missing.append('Macro Task Request, User Request, or Intake Evidence')
    if not has_request_detail:
        missing.append('Macro request or User request detail')
    if not has_legacy_contract and not has_intake_contract and not has_blocked_evidence:
        missing.append('Execution Contract, Acceptance Criteria, or Required behavior with Scope/Constraints')
    return missing


__all__ = ['enforce_frontdesk_boundary']
