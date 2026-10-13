from __future__ import annotations

from .workflow_policy import agentic_loop_enabled

from dataclasses import dataclass
import hashlib
import inspect
import json
from pathlib import Path
import re
from types import SimpleNamespace
from typing import Callable, Mapping

from ccbd.api_models import (
    AcceptedJobReceipt,
    DeliveryScope,
    JobStatus,
    MessageEnvelope,
    SubmitReceipt,
)
from cli.services.plan_tasks import plan_task
from cli.services.planner_feedback_apply import plan_revision_authority
from storage.atomic import atomic_write_json
from storage.locks import file_lock


REPLAN_REQUEST_SCHEMA = 'ccb.detailer.replan_request.v1'
_DIGEST_RE = re.compile(r'^sha256:[0-9a-f]{64}$')
_TASK_ID_RE = re.compile(r'^detailer-replan-([0-9a-f]{32})$')
_SOURCE_ROLES = frozenset({'task_detailer', 'ccb_task_detailer'})
_ACTIVE_SOURCE_STATUSES = frozenset({JobStatus.ACCEPTED, JobStatus.QUEUED, JobStatus.RUNNING})
_REQUEST_FIELDS = frozenset(
    {
        'schema',
        'request_identity',
        'task_id',
        'task_revision',
        'source_detailer_job_id',
        'source_role',
        'target_role',
        'silence',
        'detail',
        'detail_digest',
        'macro_impact',
        'macro_impact_digest',
    }
)
_DETAIL_FIELDS = frozenset({'summary', 'artifact_refs', 'clarification_refs'})
_MACRO_FIELDS = frozenset(
    {
        'categories',
        'summary',
        'preserved_facts',
        'proposed_changes',
        'acceptance_impacts',
        'dependency_impacts',
        'roadmap_impacts',
    }
)
_MACRO_CATEGORIES = frozenset(
    {'scope', 'public_interface', 'dependencies', 'ordering', 'acceptance', 'risk', 'roadmap'}
)


@dataclass(frozen=True)
class _PreparedHandoff:
    request_payload: dict[str, object]
    intent_path: Path
    intent: dict[str, object]
    activation_path: Path
    activation: dict[str, object]
    accepted_task_revision: int


def is_task_detailer_submission(request: MessageEnvelope) -> bool:
    return str(request.from_actor or '').strip().lower() in _SOURCE_ROLES


def submit_detailer_replan_handoff(
    dispatcher,
    request: MessageEnvelope,
    *,
    accepted_at: str,
    submit: Callable[[MessageEnvelope], SubmitReceipt],
) -> SubmitReceipt:
    payload = _validate_request(
        dispatcher,
        request,
        allow_settled_source=_request_has_existing_intent(dispatcher, request),
    )
    intent_path = _intent_path(dispatcher, str(payload['request_identity']))
    with file_lock(intent_path.with_suffix('.lock')):
        prepared = _prepare(dispatcher, request, payload=payload, intent_path=intent_path)
        existing = _existing_planner_job(dispatcher, payload=payload, request=request)
        if existing is not None:
            _finalize(dispatcher, prepared, existing)
            return _existing_receipt(existing, accepted_at=accepted_at)
        # Legacy test/in-process submit hooks may be zero-argument; the runtime
        # hook receives the constructed Planner envelope.
        receipt = (
            submit()
            if len(inspect.signature(submit).parameters) == 0
            else submit(_planner_request(request, prepared.activation))
        )
        if len(receipt.jobs) != 1:
            raise dispatcher._dispatch_error('Detailer replan handoff must create exactly one Planner job')
        job = dispatcher.get(receipt.jobs[0].job_id)
        if job is None:
            raise dispatcher._dispatch_error('Detailer replan Planner job was not persisted')
        _finalize(dispatcher, prepared, job)
        return receipt


def recover_detailer_replan_handoffs(
    dispatcher,
    *,
    authority_check=None,
) -> tuple[str, ...]:
    if not agentic_loop_enabled(dispatcher):
        return ()
    recovered: list[str] = []
    root = _intent_root(dispatcher)
    if not root.is_dir():
        return ()
    for path in sorted(root.glob('*.json')):
        if callable(authority_check):
            authority_check()
        try:
            intent = _read_json(path)
            request_record = intent.get('request')
            if not isinstance(request_record, Mapping):
                raise ValueError('Detailer replan intent is missing request authority')
            request = _message_envelope(request_record)
            _validate_request(dispatcher, request, allow_settled_source=True)
            receipt = dispatcher.submit(request)
            if len(receipt.jobs) != 1:
                raise ValueError('Detailer replan recovery did not resolve one Planner job')
            recovered.append(receipt.jobs[0].job_id)
        except Exception as exc:
            atomic_write_json(
                path.with_suffix('.recovery-error'),
                {
                    'schema': 'ccb.detailer.replan_recovery_error.v1',
                    'record_type': 'ccb_detailer_replan_recovery_error',
                    'intent_path': str(path),
                    'error': f'{type(exc).__name__}: {exc}',
                },
            )
    return tuple(dict.fromkeys(recovered))


def _prepare(
    dispatcher,
    request: MessageEnvelope,
    *,
    payload: dict[str, object],
    intent_path: Path,
) -> _PreparedHandoff:
    existing = _read_json_optional(intent_path)
    body_sha256 = hashlib.sha256(request.body.encode('utf-8')).hexdigest()
    if existing is not None:
        _validate_existing_intent(dispatcher, existing, payload=payload, request=request)
        intent = existing
    else:
        intent = {
            'schema': 'ccb.detailer.replan_intent.v1',
            'record_type': 'ccb_detailer_replan_intent',
            'status': 'prepared',
            'request_identity': payload['request_identity'],
            'task_id': payload['task_id'],
            'source_task_revision': payload['task_revision'],
            'detail_digest': payload['detail_digest'],
            'macro_impact_digest': payload['macro_impact_digest'],
            'source_detailer_job_id': payload['source_detailer_job_id'],
            'request_body_sha256': body_sha256,
            'request': request.to_record(),
            'created_at': dispatcher._clock(),
        }
        intent_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(intent_path, intent)

    context = _context(dispatcher)
    accepted = plan_task(
        context,
        SimpleNamespace(
            action='task-accept-detailer-replan',
            task_id=payload['task_id'],
            request_identity=payload['request_identity'],
            detail_digest=payload['detail_digest'],
            macro_impact_digest=payload['macro_impact_digest'],
            source_task_revision=payload['task_revision'],
            source_detailer_job_id=payload['source_detailer_job_id'],
            planner_job_id=None,
        ),
    )
    task = accepted.get('task') if isinstance(accepted.get('task'), dict) else {}
    accepted_revision = int(task.get('task_revision') or 0)
    if accepted_revision != int(payload['task_revision']) + 1:
        raise dispatcher._dispatch_error('Detailer replan authority did not establish the expected revision fence')
    activation_id = _activation_id(str(payload['request_identity']))
    activation_path = _activation_path(dispatcher, activation_id)
    activation = _read_json_optional(activation_path)
    if activation is None:
        detail = payload['detail'] if isinstance(payload.get('detail'), Mapping) else {}
        macro = payload['macro_impact'] if isinstance(payload.get('macro_impact'), Mapping) else {}
        evidence_refs = tuple(dict.fromkeys([
            *[str(value) for value in detail.get('artifact_refs', ())],
            *[str(value) for value in detail.get('clarification_refs', ())],
        ]))
        closure_digest = _canonical_digest({
            'request_identity': payload['request_identity'], 'detail_digest': payload['detail_digest'],
            'macro_impact_digest': payload['macro_impact_digest'], 'evidence_refs': evidence_refs,
        })
        plan_slug = str(task.get('plan_slug') or '')
        if not plan_slug:
            raise dispatcher._dispatch_error('Detailer replan accepted task is missing plan_slug')
        expected_plan_revision = plan_revision_authority(context, plan_slug)['digest']
        activation = {
            'schema_version': 1,
            'record_type': 'ccb_loop_detailer_planner_replan_activation',
            'activation_id': activation_id,
            'status': 'planner_submit_pending',
            'target': 'planner',
            'task_id': payload['task_id'],
            'task_revision': accepted_revision,
            'source_task_revision': payload['task_revision'],
            'plan_slug': plan_slug,
            'planner_contract': 'detailer_replan',
            'reason_for_activation': 'planner_replan_required_from_task_detailer',
            'source_job': {
                'job_id': payload['source_detailer_job_id'],
                'agent_name': request.from_actor,
            },
            'source_replan_request': {
                'schema': REPLAN_REQUEST_SCHEMA,
                'request_identity': payload['request_identity'],
                'source_detailer_job_id': payload['source_detailer_job_id'],
                'detail_digest': payload['detail_digest'],
                'macro_impact_digest': payload['macro_impact_digest'],
                'intent_path': str(intent_path),
                'source_request_version': 1,
                'source_request_body': request.body,
                'source_request_body_sha256': body_sha256,
            },
            'planner_authority': {
                'task_id': payload['task_id'], 'task_revision': accepted_revision,
                'plan_slug': plan_slug, 'expected_plan_revision': expected_plan_revision,
                'closure_evidence_digest': closure_digest, 'evidence_refs': list(evidence_refs),
                'request_identity': payload['request_identity'], 'detail_digest': payload['detail_digest'],
                'macro_impact_digest': payload['macro_impact_digest'],
            },
        }
        activation_path.parent.mkdir(parents=True, exist_ok=True)
        atomic_write_json(activation_path, activation)
    else:
        _validate_existing_activation(dispatcher, activation, payload=payload, accepted_revision=accepted_revision, request=request)
    intent = dict(intent)
    intent['status'] = 'authority_accepted'
    intent['accepted_task_revision'] = accepted_revision
    intent['activation_id'] = activation_id
    intent['activation_path'] = str(activation_path)
    intent['updated_at'] = dispatcher._clock()
    atomic_write_json(intent_path, intent)
    return _PreparedHandoff(
        request_payload=payload,
        intent_path=intent_path,
        intent=intent,
        activation_path=activation_path,
        activation=activation,
        accepted_task_revision=accepted_revision,
    )


def _finalize(dispatcher, prepared: _PreparedHandoff, job) -> None:
    from cli.services.frontdesk_intake import _start_auto_runner

    payload = prepared.request_payload
    activation = dict(prepared.activation)
    ask = activation.get('ask') if isinstance(activation.get('ask'), dict) else {}
    prior_job_id = str(ask.get('job_id') or '').strip()
    if prior_job_id and prior_job_id != job.job_id:
        raise dispatcher._dispatch_error('Detailer replan activation already references another Planner job')
    activation['ask'] = {
        'target': 'planner',
        'job_id': job.job_id,
        'status': job.status.value,
        'sender': str(job.request.from_actor),
        'silence': True,
    }
    activation['status'] = 'planner_submitted'
    atomic_write_json(prepared.activation_path, activation)
    accepted = plan_task(
        _context(dispatcher),
        SimpleNamespace(
            action='task-accept-detailer-replan',
            task_id=payload['task_id'],
            request_identity=payload['request_identity'],
            detail_digest=payload['detail_digest'],
            macro_impact_digest=payload['macro_impact_digest'],
            source_task_revision=payload['task_revision'],
            source_detailer_job_id=payload['source_detailer_job_id'],
            planner_job_id=job.job_id,
        ),
    )
    if not bool(accepted.get('idempotent')):
        raise dispatcher._dispatch_error('Detailer replan Planner job binding lost task authority')
    intent = dict(prepared.intent)
    intent['status'] = 'planner_submitted'
    intent['planner_job_id'] = job.job_id
    intent['planner_job_status'] = job.status.value
    intent['updated_at'] = dispatcher._clock()
    atomic_write_json(prepared.intent_path, intent)
    if _job_import_settled(_context(dispatcher), job.job_id):
        return
    try:
        activation['auto_runner'] = _start_auto_runner(
            _context(dispatcher),
            activation_id=str(activation['activation_id']),
            wait_job_id=job.job_id,
        )
    except Exception as exc:
        activation['status'] = 'planner_submitted_runner_start_failed'
        activation['runner_start_error'] = f'{type(exc).__name__}: {exc}'
        atomic_write_json(prepared.activation_path, activation)
        intent['status'] = 'planner_submitted_runner_start_failed'
        intent['runner_start_error'] = activation['runner_start_error']
        atomic_write_json(prepared.intent_path, intent)
        raise
    activation['status'] = 'planner_submitted'
    activation.pop('runner_start_error', None)
    atomic_write_json(prepared.activation_path, activation)


def _validate_request(
    dispatcher,
    request: MessageEnvelope,
    *,
    allow_settled_source: bool,
) -> dict[str, object]:
    if not _looks_like_handoff(request):
        raise dispatcher._dispatch_error(
            'Task Detailer may only submit one direct silent inline ask to resident Planner '
            'using ccb.detailer.replan_request.v1'
        )
    if request.body_artifact:
        raise dispatcher._dispatch_error('Detailer replan request must remain inline')
    if request.reply_to:
        raise dispatcher._dispatch_error('Detailer replan request cannot set reply_to')
    if dict(request.route_options or {}):
        raise dispatcher._dispatch_error('Detailer replan request cannot use chain or route options')
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError as exc:
        raise dispatcher._dispatch_error(f'Detailer replan request must be valid JSON: {exc}') from exc
    if not isinstance(payload, dict):
        raise dispatcher._dispatch_error('Detailer replan request must be a JSON object')
    if set(payload) != _REQUEST_FIELDS:
        raise dispatcher._dispatch_error('Detailer replan request fields do not match the versioned schema')
    if payload.get('schema') != REPLAN_REQUEST_SCHEMA:
        raise dispatcher._dispatch_error(f'Detailer replan request schema must be {REPLAN_REQUEST_SCHEMA}')
    if payload.get('source_role') != 'task_detailer' or payload.get('target_role') != 'planner':
        raise dispatcher._dispatch_error('Detailer replan request role boundary is invalid')
    if payload.get('silence') is not True:
        raise dispatcher._dispatch_error('Detailer replan request must declare silence=true')
    task_id = str(payload.get('task_id') or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', task_id):
        raise dispatcher._dispatch_error('Detailer replan request task_id is invalid')
    revision = payload.get('task_revision')
    if isinstance(revision, bool) or not isinstance(revision, int) or revision <= 0:
        raise dispatcher._dispatch_error('Detailer replan request task_revision must be a positive integer')
    detail = _strict_object(dispatcher, payload.get('detail'), fields=_DETAIL_FIELDS, label='detail')
    macro = _strict_object(dispatcher, payload.get('macro_impact'), fields=_MACRO_FIELDS, label='macro_impact')
    _required_text(dispatcher, detail.get('summary'), label='detail.summary')
    _string_list(dispatcher, detail.get('artifact_refs'), label='detail.artifact_refs')
    _string_list(dispatcher, detail.get('clarification_refs'), label='detail.clarification_refs')
    categories = _string_list(dispatcher, macro.get('categories'), label='macro_impact.categories', nonempty=True)
    unknown_categories = sorted(set(categories) - _MACRO_CATEGORIES)
    if unknown_categories:
        raise dispatcher._dispatch_error(f'unsupported Detailer macro impact category: {unknown_categories[0]}')
    _required_text(dispatcher, macro.get('summary'), label='macro_impact.summary')
    for key in (
        'preserved_facts',
        'proposed_changes',
        'acceptance_impacts',
        'dependency_impacts',
        'roadmap_impacts',
    ):
        _string_list(dispatcher, macro.get(key), label=f'macro_impact.{key}')
    if not any(macro.get(key) for key in ('proposed_changes', 'acceptance_impacts', 'dependency_impacts', 'roadmap_impacts')):
        raise dispatcher._dispatch_error('Detailer macro impact must contain at least one proposed change or impact')
    detail_digest = _digest(dispatcher, payload.get('detail_digest'), label='detail_digest')
    macro_digest = _digest(dispatcher, payload.get('macro_impact_digest'), label='macro_impact_digest')
    if detail_digest != _canonical_digest(detail):
        raise dispatcher._dispatch_error('Detailer replan detail_digest does not match detail evidence')
    if macro_digest != _canonical_digest(macro):
        raise dispatcher._dispatch_error('Detailer replan macro_impact_digest does not match macro evidence')
    identity = _digest(dispatcher, payload.get('request_identity'), label='request_identity')
    expected_identity = _canonical_digest(
        {'task_id': task_id, 'task_revision': revision, 'detail_digest': detail_digest}
    )
    if identity != expected_identity:
        raise dispatcher._dispatch_error('Detailer replan request_identity does not match task revision and detail digest')
    expected_task_id = f'detailer-replan-{identity.removeprefix("sha256:")[:32]}'
    if request.task_id != expected_task_id:
        raise dispatcher._dispatch_error('Detailer replan ask task id does not match request_identity')
    _validate_source_job(
        dispatcher,
        request,
        payload=payload,
        allow_settled_source=allow_settled_source,
    )
    return payload


def _validate_source_job(
    dispatcher,
    request: MessageEnvelope,
    *,
    payload: Mapping[str, object],
    allow_settled_source: bool,
) -> None:
    source_job_id = str(payload.get('source_detailer_job_id') or '').strip()
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', source_job_id):
        raise dispatcher._dispatch_error('Detailer replan source_detailer_job_id is invalid')
    source = dispatcher.get(source_job_id)
    if source is None or source.agent_name != request.from_actor:
        raise dispatcher._dispatch_error('Detailer replan source Detailer job is missing or belongs to another role')
    spec = dispatcher._config.agents.get(source.agent_name)
    role = str(getattr(spec, 'role', '') or '').strip()
    if role and role != 'agentroles.ccb_task_detailer':
        raise dispatcher._dispatch_error('Detailer replan source Detailer job role is invalid')
    if not allow_settled_source and source.status not in _ACTIVE_SOURCE_STATUSES:
        raise dispatcher._dispatch_error('Detailer replan source Detailer job is not active')
    activation_id = str(source.request.task_id or '').strip()
    if not activation_id.startswith('act-'):
        raise dispatcher._dispatch_error('Detailer replan source Detailer job is not activation-scoped')
    activation = _read_json_optional(_activation_path(dispatcher, activation_id))
    ask = activation.get('ask') if isinstance(activation, dict) and isinstance(activation.get('ask'), dict) else {}
    if isinstance(activation, dict) and activation.get('task_revision') != payload.get('task_revision'):
        raise dispatcher._dispatch_error(
            'stale detailer replan task revision: '
            f'activation={activation.get("task_revision")}, request={payload.get("task_revision")}'
        )
    if (
        not isinstance(activation, dict)
        or str(activation.get('target') or '') != source.agent_name
        or str(activation.get('task_id') or '') != str(payload.get('task_id') or '')
        or str(ask.get('job_id') or '') != source_job_id
    ):
        raise dispatcher._dispatch_error('Detailer replan source Detailer job activation identity is invalid')


def _looks_like_handoff(request: MessageEnvelope) -> bool:
    return bool(
        is_task_detailer_submission(request)
        and str(request.to_agent or '').strip().lower() == 'planner'
        and str(request.message_type or '').strip().lower() == 'ask'
        and request.delivery_scope is DeliveryScope.SINGLE
        and bool(request.silence_on_success)
        and _TASK_ID_RE.fullmatch(str(request.task_id or ''))
    )


def _existing_planner_job(dispatcher, *, payload: Mapping[str, object], request: MessageEnvelope):
    matches = {
        job.job_id: job
        for job in dispatcher._job_store.list_agent('planner')
        if str(job.request.task_id or '') == str(request.task_id or '')
    }
    if not matches:
        return None
    exact = []
    for job in matches.values():
        try:
            existing = json.loads(job.request.body)
        except json.JSONDecodeError:
            continue
        if not isinstance(existing, dict):
            continue
        existing = existing.get('source_request') if isinstance(existing.get('source_request'), dict) else existing
        if all(
            existing.get(key) == payload.get(key)
            for key in (
                'request_identity',
                'detail_digest',
                'macro_impact_digest',
                'source_detailer_job_id',
            )
        ):
            exact.append(job)
    if len(exact) != 1 or len(matches) != 1:
        raise dispatcher._dispatch_error('Detailer replan request identity conflict')
    return exact[0]


def _planner_request(request: MessageEnvelope, activation: Mapping[str, object]) -> MessageEnvelope:
    """Add mechanical controller authority without changing Detailer's JSON bytes."""
    authority = activation.get('planner_authority')
    source = activation.get('source_replan_request')
    if not isinstance(authority, Mapping) or not isinstance(source, Mapping):
        raise ValueError('Detailer replan planner authority is missing')
    body = json.dumps({
        'schema': 'ccb.detailer.planner_activation.v1',
        'mode': 'detailer_replan',
        'authority': dict(authority),
        'source_request': json.loads(request.body),
        'source_request_body': str(source['source_request_body']),
        'source_request_body_sha256': str(source['source_request_body_sha256']),
    }, ensure_ascii=True, sort_keys=True, separators=(',', ':'))
    return MessageEnvelope(
        project_id=request.project_id, to_agent=request.to_agent, from_actor=request.from_actor,
        body=body, task_id=request.task_id, reply_to=request.reply_to, message_type=request.message_type,
        delivery_scope=request.delivery_scope, silence_on_success=request.silence_on_success,
        route_options=dict(request.route_options or {}), body_artifact=request.body_artifact,
    )


def _validate_existing_intent(dispatcher, intent: Mapping[str, object], *, payload: Mapping[str, object], request: MessageEnvelope) -> None:
    for key in (
        'request_identity',
        'task_id',
        'detail_digest',
        'macro_impact_digest',
        'source_detailer_job_id',
    ):
        if intent.get(key) != payload.get(key):
            raise dispatcher._dispatch_error('Detailer replan request identity conflict')
    if intent.get('source_task_revision') != payload.get('task_revision'):
        raise dispatcher._dispatch_error('Detailer replan request identity conflict')
    request_record = intent.get('request') if isinstance(intent.get('request'), Mapping) else {}
    if (
        request_record.get('body') != request.body
        or intent.get('request_body_sha256') != hashlib.sha256(request.body.encode('utf-8')).hexdigest()
    ):
        raise dispatcher._dispatch_error('Detailer replan raw request identity conflict')


def _validate_existing_activation(
    dispatcher,
    activation: Mapping[str, object],
    *,
    payload: Mapping[str, object],
    accepted_revision: int,
    request: MessageEnvelope,
) -> None:
    source = activation.get('source_replan_request')
    if not isinstance(source, Mapping):
        raise dispatcher._dispatch_error('Detailer replan activation is missing source authority')
    if (
        str(activation.get('task_id') or '') != str(payload['task_id'])
        or activation.get('task_revision') != accepted_revision
        or source.get('request_identity') != payload['request_identity']
        or source.get('source_detailer_job_id') != payload['source_detailer_job_id']
        or source.get('detail_digest') != payload['detail_digest']
        or source.get('macro_impact_digest') != payload['macro_impact_digest']
        or source.get('source_request_body') != request.body
        or source.get('source_request_body_sha256') != hashlib.sha256(request.body.encode('utf-8')).hexdigest()
    ):
        raise dispatcher._dispatch_error('Detailer replan activation identity conflict')


def _existing_receipt(job, *, accepted_at: str) -> SubmitReceipt:
    status = job.status
    if status not in {JobStatus.ACCEPTED, JobStatus.QUEUED, JobStatus.RUNNING}:
        status = JobStatus.RUNNING
    return SubmitReceipt(
        accepted_at=accepted_at,
        jobs=(
            AcceptedJobReceipt(
                job_id=job.job_id,
                agent_name=job.agent_name,
                target_kind=job.target_kind,
                target_name=job.target_name,
                provider_instance=job.provider_instance,
                status=status,
                accepted_at=accepted_at,
            ),
        ),
    )


def _context(dispatcher):
    layout = dispatcher._layout
    return SimpleNamespace(
        cwd=layout.project_root,
        paths=layout,
        project=SimpleNamespace(
            cwd=layout.project_root,
            project_root=layout.project_root,
            config_dir=layout.ccb_dir,
            project_id=layout.project_id,
            source='ccbd-detailer-replan-direct-ask',
        ),
    )


def _intent_root(dispatcher) -> Path:
    return Path(dispatcher._layout.project_root) / '.ccb' / 'runtime' / 'detailer-replan'


def _intent_path(dispatcher, request_identity: str) -> Path:
    return _intent_root(dispatcher) / f'{request_identity.removeprefix("sha256:")}.json'


def _request_has_existing_intent(dispatcher, request: MessageEnvelope) -> bool:
    try:
        payload = json.loads(request.body)
    except json.JSONDecodeError:
        return False
    if not isinstance(payload, dict):
        return False
    identity = str(payload.get('request_identity') or '').strip().lower()
    return bool(_DIGEST_RE.fullmatch(identity) and _intent_path(dispatcher, identity).is_file())


def _activation_id(request_identity: str) -> str:
    return f'act-detailer-replan-{request_identity.removeprefix("sha256:")[:32]}'


def _activation_path(dispatcher, activation_id: str) -> Path:
    return (
        Path(dispatcher._layout.project_root)
        / '.ccb'
        / 'runtime'
        / 'loops'
        / 'activations'
        / f'{activation_id}.json'
    )


def _strict_object(dispatcher, value: object, *, fields: frozenset[str], label: str) -> dict[str, object]:
    if not isinstance(value, dict) or set(value) != fields:
        raise dispatcher._dispatch_error(f'Detailer replan {label} fields do not match the versioned schema')
    return dict(value)


def _required_text(dispatcher, value: object, *, label: str) -> str:
    text = str(value or '').strip()
    if not text:
        raise dispatcher._dispatch_error(f'Detailer replan {label} must be non-empty')
    return text


def _string_list(dispatcher, value: object, *, label: str, nonempty: bool = False) -> tuple[str, ...]:
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise dispatcher._dispatch_error(f'Detailer replan {label} must be a string list')
    if nonempty and not value:
        raise dispatcher._dispatch_error(f'Detailer replan {label} must not be empty')
    return tuple(item.strip() for item in value)


def _digest(dispatcher, value: object, *, label: str) -> str:
    text = str(value or '').strip().lower()
    if not _DIGEST_RE.fullmatch(text):
        raise dispatcher._dispatch_error(f'Detailer replan {label} must use sha256:<64 lowercase hex>')
    return text


def _canonical_digest(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':')).encode('utf-8')
    return 'sha256:' + hashlib.sha256(encoded).hexdigest()


def _message_envelope(record: Mapping[str, object]) -> MessageEnvelope:
    return MessageEnvelope(
        project_id=str(record.get('project_id') or ''),
        to_agent=str(record.get('to_agent') or ''),
        from_actor=str(record.get('from_actor') or ''),
        body=str(record.get('body') or ''),
        task_id=str(record.get('task_id') or '') or None,
        reply_to=str(record.get('reply_to') or '') or None,
        message_type=str(record.get('message_type') or ''),
        delivery_scope=DeliveryScope(str(record.get('delivery_scope') or 'single')),
        silence_on_success=bool(record.get('silence_on_success')),
        route_options=dict(record.get('route_options') or {}),
        body_artifact=dict(record['body_artifact']) if isinstance(record.get('body_artifact'), dict) else None,
    )


def _read_json(path: Path) -> dict[str, object]:
    payload = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(payload, dict):
        raise ValueError(f'expected JSON object: {path}')
    return payload


def _read_json_optional(path: Path) -> dict[str, object] | None:
    try:
        return _read_json(path)
    except FileNotFoundError:
        return None


def _job_import_settled(context, job_id: str) -> bool:
    path = Path(context.project.project_root) / '.ccb' / 'runtime' / 'role-output-imports.jsonl'
    try:
        lines = path.read_text(encoding='utf-8').splitlines()
    except FileNotFoundError:
        return False
    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        source = record.get('source_job') if isinstance(record.get('source_job'), dict) else {}
        if str(source.get('job_id') or record.get('job_id') or '') != job_id:
            continue
        if str(record.get('status') or '') == 'ok':
            return True
    return False


__all__ = [
    'REPLAN_REQUEST_SCHEMA',
    'is_task_detailer_submission',
    'recover_detailer_replan_handoffs',
    'submit_detailer_replan_handoff',
]
