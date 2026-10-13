from __future__ import annotations

from .workflow_policy import agentic_loop_enabled

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from types import SimpleNamespace
from typing import Callable, Mapping

from ccbd.api_models import AcceptedJobReceipt, DeliveryScope, JobStatus, MessageEnvelope, SubmitReceipt
from storage.atomic import atomic_write_json, ensure_durable_directory
from storage.locks import file_lock


_ACTIVATION_TASK_RE = re.compile(r'^act-frontdesk-([A-Za-z0-9][A-Za-z0-9_-]{0,79})$')
_REQUEST_ID_RE = re.compile(r'(?mi)^\s*CCB_REQ_ID\s*:\s*`?([^`\n]+?)`?\s*$')
_TRANSACTION_SCHEMA = 'ccb.frontdesk.direct_handoff_admission_transaction.v1'


@dataclass(frozen=True)
class _DirectHandoff:
    context: object
    activation_id: str
    activation_path: Path
    activation: dict[str, object]
    intake_sha256: str
    transaction_path: Path
    transaction: dict[str, object]


def is_frontdesk_submission(request: MessageEnvelope) -> bool:
    return str(request.from_actor or '').strip().lower() == 'frontdesk'


def submit_frontdesk_direct_handoff(
    dispatcher,
    request: MessageEnvelope,
    *,
    accepted_at: str,
    submit: Callable[[], SubmitReceipt],
) -> SubmitReceipt:
    """Admit the one Frontdesk effect and attach mechanical loop authority.

    Frontdesk owns the Planner message.  This function validates and records
    that message without rewriting it, then wakes the existing loop runner.
    """

    _validate_shape(dispatcher, request)
    lock_path = _direct_activation_path(dispatcher, str(request.task_id)).with_suffix('.lock')
    _validate_authority_path(dispatcher, lock_path, label='activation lock')
    ensure_durable_directory(_transaction_path(dispatcher, str(request.task_id)).parent)
    with file_lock(lock_path):
        handoff = _prepare(dispatcher, request)
        if str(handoff.transaction.get('status') or '') != 'committed':
            raise dispatcher._dispatch_error('frontdesk direct handoff admission is not committed')
        existing = _existing_planner_job(dispatcher, request)
        if existing is not None:
            _finalize(dispatcher, handoff, existing)
            return _existing_receipt(existing, accepted_at=accepted_at)

        receipt = submit()
        if len(receipt.jobs) != 1:
            raise dispatcher._dispatch_error('frontdesk planner handoff must create exactly one job')
        job = dispatcher.get(receipt.jobs[0].job_id)
        if job is None:
            raise dispatcher._dispatch_error('frontdesk planner handoff job was not persisted')
        _finalize(dispatcher, handoff, job)
        return receipt


def recover_frontdesk_direct_handoffs(
    dispatcher,
    *,
    authority_check=None,
) -> tuple[str, ...]:
    if not agentic_loop_enabled(dispatcher):
        return ()
    recovered: list[str] = []
    root = _direct_activation_path(dispatcher, 'placeholder').parent
    _validate_authority_path(dispatcher, root, label='activation directory')
    if not root.is_dir():
        return ()
    for path in sorted(root.glob('*.direct-handoff.transaction.json')):
        if callable(authority_check):
            authority_check()
        try:
            transaction = _read_json(path)
            request_record = transaction.get('request')
            if not isinstance(request_record, Mapping):
                raise ValueError('frontdesk direct handoff journal is missing request authority')
            request = _message_envelope(request_record)
            _validate_shape(dispatcher, request)
            expected_path = _transaction_path(dispatcher, str(request.task_id))
            if path != expected_path:
                raise ValueError('frontdesk direct handoff journal path is not canonical')
            lock_path = _direct_activation_path(dispatcher, str(request.task_id)).with_suffix('.lock')
            _validate_authority_path(dispatcher, lock_path, label='activation lock')
            ensure_durable_directory(expected_path.parent)
            with file_lock(lock_path):
                handoff = _prepare(dispatcher, request)
            receipt = dispatcher.submit(request)
            if len(receipt.jobs) != 1:
                raise ValueError('frontdesk direct handoff recovery did not resolve one Planner job')
            recovered.append(receipt.jobs[0].job_id)
        except Exception as exc:
            atomic_write_json(
                path.with_name(path.name.removesuffix('.json') + '.recovery-error.json'),
                {
                    'schema': 'ccb.frontdesk.direct_handoff_admission_recovery_error.v1',
                    'record_type': 'ccb_frontdesk_direct_handoff_admission_recovery_error',
                    'transaction_path': str(path),
                    'error': f'{type(exc).__name__}: {exc}',
                },
            )
    return tuple(recovered)


def _prepare(dispatcher, request: MessageEnvelope) -> _DirectHandoff:
    from cli.services.frontdesk_intake import (
        _load_existing_activation,
        _new_activation,
        _resolve_plan_slug,
    )

    _validate_shape(dispatcher, request)
    context = _context(dispatcher)
    activation_id = str(request.task_id)
    activation_path = _direct_activation_path(dispatcher, activation_id)
    transaction_path = _transaction_path(dispatcher, activation_id)
    _validate_authority_path(dispatcher, activation_path, label='activation')
    _validate_authority_path(dispatcher, transaction_path, label='transaction journal')
    plan = _resolve_plan_slug(context, SimpleNamespace(plan_slug=None))
    if str(plan.get('status') or '') != 'ok':
        raise dispatcher._dispatch_error(str(plan.get('reason') or 'frontdesk handoff plan resolution failed'))
    plan_slug = str(plan['plan_slug'])
    request_id = activation_id.removeprefix('act-frontdesk-')
    intake_sha256 = hashlib.sha256(request.body.encode('utf-8')).hexdigest()
    intake_bytes = len(request.body.encode('utf-8'))
    expected_activation = _new_activation(
        context,
        activation_id=activation_id,
        plan_slug=plan_slug,
        request_id=request_id,
        intake_text=request.body,
        intake_sha256=intake_sha256,
        source_request={
            'status': 'ok',
            'source_job_id': request_id,
            'agent_name': 'frontdesk',
            'project_id': request.project_id,
            'to_agent': 'planner',
            'from_actor': 'frontdesk',
            'message_type': 'ask',
            'text': request.body,
            'bytes': intake_bytes,
            'sha256': intake_sha256,
        },
    )
    expected_activation['source'] = 'frontdesk_direct_silence_ask'
    expected_activation['status'] = 'direct_ask_pending'
    expected_activation['direct_ask'] = {
        'from_actor': 'frontdesk',
        'target': 'planner',
        'silence': True,
        'task_id': activation_id,
        'body_sha256': intake_sha256,
        'controller_rewrote_body': False,
    }
    expected_source_task_id = request_id if expected_activation.get('planner_contract') == 'task_set' else None
    if expected_source_task_id is not None:
        expected_activation['source_task_id'] = expected_source_task_id
    transaction = _read_json_optional(transaction_path)
    if transaction is None:
        activation = expected_activation
        activation_digest = _activation_digest(expected_activation)
        authority = {
            'project_id': request.project_id,
            'activation_id': activation_id,
            'request_id': request_id,
            'plan_slug': plan_slug,
            'request': request.to_record(),
            'body_bytes': intake_bytes,
            'body_sha256': intake_sha256,
            'planner_contract': expected_activation.get('planner_contract'),
            'source_task_id': expected_source_task_id,
            'activation_digest': activation_digest,
        }
        transaction = {
            'schema': _TRANSACTION_SCHEMA,
            'record_type': 'ccb_frontdesk_direct_handoff_admission_transaction',
            'status': 'prepared',
            **authority,
            'transaction_digest': _canonical_digest(authority),
            'activation_record': activation,
            'created_at': dispatcher._clock(),
        }
        ensure_durable_directory(transaction_path.parent)
        atomic_write_json(transaction_path, transaction)
    else:
        if str(transaction.get('status') or '') == 'failed':
            raise dispatcher._dispatch_error('frontdesk direct handoff journal is failed')
        try:
            _validate_transaction(
                transaction,
                request=request,
                plan_slug=plan_slug,
                request_id=request_id,
                intake_sha256=intake_sha256,
                intake_bytes=intake_bytes,
                expected_activation=expected_activation,
            )
        except Exception as exc:
            _mark_failed(transaction_path, transaction, exc, dispatcher=dispatcher)
            raise dispatcher._dispatch_error(str(exc)) from exc
        stored_activation = transaction.get('activation_record')
        if not isinstance(stored_activation, dict):
            exc = ValueError('frontdesk direct handoff journal is missing activation authority')
            _mark_failed(transaction_path, transaction, exc, dispatcher=dispatcher)
            raise dispatcher._dispatch_error(str(exc))
        activation = dict(stored_activation)

    source_task_id = transaction.get('source_task_id')
    if source_task_id is not None:
        try:
            _ensure_source_task(context, plan_slug=plan_slug, request_id=request_id)
        except Exception as exc:
            _mark_failed(transaction_path, transaction, exc, dispatcher=dispatcher)
            raise

    existing_activation = _load_existing_activation(activation_path)
    if existing_activation is None:
        atomic_write_json(activation_path, activation)
    else:
        try:
            _validate_activation(existing_activation, transaction=transaction)
        except Exception as exc:
            _mark_failed(transaction_path, transaction, exc, dispatcher=dispatcher)
            raise dispatcher._dispatch_error(str(exc)) from exc
        activation = existing_activation
    observed_activation = _load_existing_activation(activation_path)
    if observed_activation is None:
        raise dispatcher._dispatch_error('frontdesk activation was not persisted')
    _validate_activation(observed_activation, transaction=transaction)
    if str(transaction.get('status') or '') == 'prepared':
        transaction = dict(transaction)
        transaction['status'] = 'committed'
        transaction['committed_at'] = dispatcher._clock()
        atomic_write_json(transaction_path, transaction)
    return _DirectHandoff(
        context=context,
        activation_id=activation_id,
        activation_path=activation_path,
        activation=activation,
        intake_sha256=intake_sha256,
        transaction_path=transaction_path,
        transaction=transaction,
    )


def _ensure_source_task(context, *, plan_slug: str, request_id: str) -> str:
    from cli.services.plan_tasks import plan_task

    try:
        shown = plan_task(context, SimpleNamespace(action='task-show', task_id=request_id))
    except ValueError as exc:
        if str(exc) != f'plan task not found: {request_id}':
            raise
        plan_task(
            context,
            SimpleNamespace(
                action='task-create',
                plan_slug=plan_slug,
                title=f'Frontdesk intake {request_id}',
                task_id=request_id,
            ),
        )
        return request_id
    task = shown.get('task') if isinstance(shown.get('task'), dict) else {}
    if str(task.get('task_id') or '') != request_id:
        raise ValueError('frontdesk source task request authority conflict')
    if str(task.get('plan_slug') or '') != plan_slug:
        raise ValueError('frontdesk source task plan authority conflict')
    if str(task.get('status') or '') != 'draft':
        raise ValueError('frontdesk source task must remain draft')
    return request_id


def _validate_shape(dispatcher, request: MessageEnvelope) -> None:
    from cli.services.role_output_import import frontdesk_intake_missing_fields

    if not _looks_like_direct_handoff(request):
        raise dispatcher._dispatch_error(
            'frontdesk may only submit one direct ask --silence to planner with task id act-frontdesk-<request-id>'
        )
    if request.body_artifact:
        raise dispatcher._dispatch_error('frontdesk planner handoff must keep intake evidence inline')
    if request.reply_to:
        raise dispatcher._dispatch_error('frontdesk planner handoff cannot set reply_to')
    if dict(request.route_options or {}):
        raise dispatcher._dispatch_error('frontdesk planner handoff cannot use chain or route options')
    missing = frontdesk_intake_missing_fields(request.body)
    if missing:
        raise dispatcher._dispatch_error(
            f'frontdesk planner handoff is missing required intake fields: {", ".join(missing)}'
        )
    match = _ACTIVATION_TASK_RE.fullmatch(str(request.task_id or ''))
    assert match is not None
    request_id_match = _REQUEST_ID_RE.search(request.body)
    if request_id_match is None:
        raise dispatcher._dispatch_error('frontdesk planner handoff requires CCB_REQ_ID in intake evidence')
    request_id = request_id_match.group(1).strip()
    if request_id != match.group(1):
        raise dispatcher._dispatch_error('frontdesk task id must match the CCB_REQ_ID intake field')


def _looks_like_direct_handoff(request: MessageEnvelope) -> bool:
    return bool(
        is_frontdesk_submission(request)
        and str(request.to_agent or '').strip().lower() == 'planner'
        and str(request.message_type or '').strip().lower() == 'ask'
        and request.delivery_scope is DeliveryScope.SINGLE
        and bool(request.silence_on_success)
        and _ACTIVATION_TASK_RE.fullmatch(str(request.task_id or ''))
    )


def _existing_planner_job(dispatcher, request: MessageEnvelope):
    matches = {}
    for job in dispatcher._job_store.list_agent('planner'):
        if str(job.request.task_id or '') != str(request.task_id or ''):
            continue
        matches[job.job_id] = job
    if not matches:
        return None
    exact = [job for job in matches.values() if job.request.to_record() == request.to_record()]
    if len(exact) != 1 or len(matches) != 1:
        raise dispatcher._dispatch_error('frontdesk activation request id conflict')
    return exact[0]


def _finalize(dispatcher, handoff: _DirectHandoff, job) -> None:
    from cli.services.frontdesk_intake import _start_auto_runner

    activation = dict(handoff.activation)
    ask = activation.get('ask') if isinstance(activation.get('ask'), dict) else {}
    prior_job_id = str(ask.get('job_id') or '').strip()
    if prior_job_id and prior_job_id != job.job_id:
        raise dispatcher._dispatch_error('frontdesk activation already references another planner job')
    activation['ask'] = {
        'target': 'planner',
        'job_id': job.job_id,
        'status': job.status.value,
        'sender': 'frontdesk',
        'silence': True,
    }
    activation['status'] = 'planner_submitted'
    atomic_write_json(handoff.activation_path, activation)
    if _job_import_settled(handoff.context, job.job_id):
        return
    try:
        activation['auto_runner'] = _start_auto_runner(
            handoff.context,
            activation_id=handoff.activation_id,
            wait_job_id=job.job_id,
        )
    except Exception as exc:
        activation['status'] = 'planner_submitted_runner_start_failed'
        activation['runner_start_error'] = f'{type(exc).__name__}: {exc}'
        atomic_write_json(handoff.activation_path, activation)
        raise
    activation['status'] = 'planner_submitted'
    activation.pop('runner_start_error', None)
    atomic_write_json(handoff.activation_path, activation)


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
        observed_job_id = str(source.get('job_id') or record.get('job_id') or '')
        if observed_job_id != job_id:
            continue
        if str(record.get('status') or '') == 'ok':
            return True
        if (
            str(record.get('action') or '') == 'role_output_import_blocked'
            and str(record.get('reason') or '') == 'terminal_job_not_completed'
        ):
            return True
    return False


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
            source='ccbd-frontdesk-direct-ask',
        ),
    )


def _direct_activation_path(dispatcher, activation_id: str) -> Path:
    return (
        Path(dispatcher._layout.project_root)
        / '.ccb'
        / 'runtime'
        / 'loops'
        / 'activations'
        / f'{activation_id}.json'
    )


def _transaction_path(dispatcher, activation_id: str) -> Path:
    activation_path = _direct_activation_path(dispatcher, activation_id)
    return activation_path.with_name(f'{activation_id}.direct-handoff.transaction.json')


def _activation_digest(activation: Mapping[str, object]) -> str:
    source_job = activation.get('source_job') if isinstance(activation.get('source_job'), Mapping) else {}
    source_request = (
        activation.get('source_request') if isinstance(activation.get('source_request'), Mapping) else {}
    )
    mechanical = {
        key: activation.get(key)
        for key in (
            'schema_version',
            'record_type',
            'activation_id',
            'project_id',
            'project_root',
            'action',
            'source',
            'plan_slug',
            'request_id',
            'intake_sha256',
            'source_intake',
            'planner_contract',
            'required_next_output',
            'script_write_rules',
            'expected_task_ids',
            'source_task_id',
            'direct_ask',
        )
        if key in activation
    }
    mechanical['source_job'] = {
        key: source_job.get(key)
        for key in ('job_id', 'agent_name', 'reply_sha256')
        if key in source_job
    }
    mechanical['source_request'] = {
        key: source_request.get(key)
        for key in (
            'source_job_id',
            'agent_name',
            'project_id',
            'to_agent',
            'from_actor',
            'message_type',
            'text',
            'bytes',
            'sha256',
        )
        if key in source_request
    }
    return _canonical_digest(mechanical)


def _validate_activation(activation: Mapping[str, object], *, transaction: Mapping[str, object]) -> None:
    if _activation_digest(activation) != transaction.get('activation_digest'):
        raise ValueError('frontdesk activation identity conflict')


def _validate_transaction(
    transaction: Mapping[str, object],
    *,
    request: MessageEnvelope,
    plan_slug: str,
    request_id: str,
    intake_sha256: str,
    intake_bytes: int,
    expected_activation: Mapping[str, object],
) -> None:
    if transaction.get('schema') != _TRANSACTION_SCHEMA:
        raise ValueError('frontdesk direct handoff journal schema conflict')
    expected_activation_digest = _activation_digest(expected_activation)
    expected_source_task_id = (
        request_id if expected_activation.get('planner_contract') == 'task_set' else None
    )
    expected = {
        'project_id': request.project_id,
        'activation_id': str(request.task_id),
        'request_id': request_id,
        'plan_slug': plan_slug,
        'request': request.to_record(),
        'body_bytes': intake_bytes,
        'body_sha256': intake_sha256,
        'planner_contract': expected_activation.get('planner_contract'),
        'source_task_id': expected_source_task_id,
        'activation_digest': expected_activation_digest,
    }
    for key, value in expected.items():
        if transaction.get(key) != value:
            raise ValueError(f'frontdesk direct handoff journal {key} conflict')
    if transaction.get('transaction_digest') != _canonical_digest(expected):
        raise ValueError('frontdesk direct handoff journal digest conflict')
    activation_record = transaction.get('activation_record')
    if not isinstance(activation_record, Mapping):
        raise ValueError('frontdesk direct handoff journal is missing activation authority')
    if _activation_digest(activation_record) != expected_activation_digest:
        raise ValueError('frontdesk direct handoff journal activation_record conflict')


def _mark_failed(path: Path, transaction: Mapping[str, object], exc: Exception, *, dispatcher) -> None:
    if str(transaction.get('status') or '') == 'failed':
        return
    failed = dict(transaction)
    failed['status'] = 'failed'
    failed['failed_at'] = dispatcher._clock()
    failed['failure'] = f'{type(exc).__name__}: {exc}'
    atomic_write_json(path, failed)


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


def _validate_authority_path(dispatcher, path: Path, *, label: str) -> None:
    project_root = Path(dispatcher._layout.project_root).absolute()
    candidate = Path(path).absolute()
    try:
        relative = candidate.relative_to(project_root)
    except ValueError as exc:
        raise ValueError(f'frontdesk direct handoff {label} escapes project root') from exc
    current = project_root
    for part in relative.parts:
        if current.is_symlink():
            raise ValueError(f'frontdesk direct handoff {label} cannot use symlink path components')
        current = current / part
    if current.is_symlink():
        raise ValueError(f'frontdesk direct handoff {label} cannot be a symlink')


__all__ = [
    'is_frontdesk_submission',
    'recover_frontdesk_direct_handoffs',
    'submit_frontdesk_direct_handoff',
]
