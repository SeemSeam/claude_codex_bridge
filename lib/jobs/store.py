from __future__ import annotations

from dataclasses import dataclass, field
import os
from typing import Any

from ccbd.api_models import DeliveryScope, JobEvent, JobRecord, JobStatus, MessageEnvelope, SubmissionRecord, TargetKind
from storage.jsonl_store import JsonlStore
from storage.paths import PathLayout

SCHEMA_VERSION = 2
PROJECT_VIEW_RECENT_JOB_STATUS_VALUES = frozenset(
    {
        JobStatus.COMPLETED.value,
        JobStatus.CANCELLED.value,
        JobStatus.FAILED.value,
        JobStatus.INCOMPLETE.value,
    }
)


@dataclass(frozen=True)
class ProjectViewMessageSummary:
    project_id: str = ''
    to_agent: str = ''
    from_actor: str = ''
    body: str = ''
    task_id: str | None = None
    reply_to: str | None = None
    message_type: str = ''
    delivery_scope: str = ''
    silence_on_success: bool = False
    route_options: dict[str, Any] = field(default_factory=dict)
    body_artifact: dict[str, Any] | None = None


@dataclass(frozen=True)
class ProjectViewJobSummary:
    job_id: str
    agent_name: str
    provider: str
    request: ProjectViewMessageSummary
    status: JobStatus
    terminal_decision: dict[str, Any] | None
    created_at: str
    updated_at: str
    target_kind: TargetKind = TargetKind.AGENT
    target_name: str = ''
    provider_options: dict[str, Any] = field(default_factory=dict)


class JobStore:
    def __init__(self, layout: PathLayout, store: JsonlStore | None = None) -> None:
        self._layout = layout
        self._store = store or JsonlStore()

    def append(self, record: JobRecord) -> None:
        self._store.append(
            self._layout.target_jobs_path(record.target_kind, record.target_name),
            record,
            serializer=lambda value: value.to_record(),
        )

    def list_agent(self, agent_name: str) -> list[JobRecord]:
        return self.list_target(TargetKind.AGENT, agent_name)

    def list_target(self, target_kind: TargetKind | str, target_name: str) -> list[JobRecord]:
        return self._store.read_all(
            self._layout.target_jobs_path(target_kind, target_name),
            loader=_job_record_from_record,
        )

    def list_agent_tail(self, agent_name: str, *, limit: int) -> list[JobRecord]:
        return self.list_target_tail(TargetKind.AGENT, agent_name, limit=limit)

    def list_agent_tails_batch(self, agent_names: tuple[str, ...] | list[str], *, limit: int) -> dict[str, list[JobRecord]]:
        normalized = [str(agent_name) for agent_name in agent_names]
        if _strict_jsonl_helper_required():
            from rust_helpers_jsonl import read_jsonl_tail_strict_batch_required

            result = read_jsonl_tail_strict_batch_required(
                [
                    {
                        'id': agent_name,
                        'path': str(self._layout.target_jobs_path(TargetKind.AGENT, agent_name)),
                        'n': limit,
                    }
                    for agent_name in normalized
                ],
            )
            rows_by_agent: dict[str, list[JobRecord]] = {agent_name: [] for agent_name in normalized}
            for item in result.value.get('requests', []):
                if not isinstance(item, dict):
                    continue
                agent_name = str(item.get('id') or '')
                rows = item.get('rows')
                if agent_name not in rows_by_agent or not isinstance(rows, list):
                    continue
                rows_by_agent[agent_name] = [_job_record_from_record(row) for row in rows if isinstance(row, dict)]
            return rows_by_agent
        return {agent_name: self.list_agent_tail(agent_name, limit=limit) for agent_name in normalized}

    def list_agent_tail_summaries_batch(
        self,
        agent_names: tuple[str, ...] | list[str],
        *,
        limit: int,
    ) -> dict[str, list[ProjectViewJobSummary]]:
        normalized = [str(agent_name) for agent_name in agent_names]
        if _job_summary_tail_helper_required():
            from rust_helpers_jsonl import read_job_tail_summaries_required

            result = read_job_tail_summaries_required(
                [
                    {
                        'id': agent_name,
                        'path': str(self._layout.target_jobs_path(TargetKind.AGENT, agent_name)),
                        'n': limit,
                    }
                    for agent_name in normalized
                ],
            )
            summaries_by_agent: dict[str, list[ProjectViewJobSummary]] = {agent_name: [] for agent_name in normalized}
            for item in result.value.get('requests', []):
                if not isinstance(item, dict):
                    continue
                agent_name = str(item.get('id') or '')
                jobs = item.get('jobs')
                if agent_name not in summaries_by_agent or not isinstance(jobs, list):
                    continue
                summaries_by_agent[agent_name] = [
                    _project_view_job_summary_from_record(job) for job in jobs if isinstance(job, dict)
                ]
            return summaries_by_agent
        return {
            agent_name: [
                _project_view_job_summary_from_job(record)
                for record in self.list_agent_tail(agent_name, limit=limit)
            ]
            for agent_name in normalized
        }

    def list_project_view_recent_jobs(
        self,
        agent_names: tuple[str, ...] | list[str],
        *,
        per_agent_limit: int,
        result_limit: int,
        statuses: tuple[str, ...] | list[str] | None = None,
        per_agent_initial_limit: int | None = None,
    ) -> tuple[ProjectViewJobSummary, ...]:
        if per_agent_limit < 0:
            raise ValueError('per_agent_limit cannot be negative')
        if result_limit < 0:
            raise ValueError('result_limit cannot be negative')
        if per_agent_initial_limit is not None and per_agent_initial_limit < 0:
            raise ValueError('per_agent_initial_limit cannot be negative')
        normalized = [str(agent_name) for agent_name in agent_names]
        if not normalized or per_agent_limit <= 0 or result_limit <= 0:
            return ()
        status_values = frozenset(str(status) for status in (statuses or tuple(PROJECT_VIEW_RECENT_JOB_STATUS_VALUES)))
        initial_limit = per_agent_limit if per_agent_initial_limit is None else min(per_agent_initial_limit, per_agent_limit)
        if initial_limit <= 0:
            initial_limit = min(per_agent_limit, 1)
        if _project_view_recent_jobs_helper_required():
            if initial_limit < per_agent_limit:
                from rust_helpers_project_view import read_jobs_query_recent_required

                result = read_jobs_query_recent_required(
                    [
                        {
                            'id': agent_name,
                            'path': str(self._layout.target_jobs_path(TargetKind.AGENT, agent_name)),
                        }
                        for agent_name in normalized
                    ],
                    statuses=tuple(status_values),
                    result_limit=result_limit,
                    per_agent_initial=initial_limit,
                    per_agent_max=per_agent_limit,
                )
                return tuple(
                    _project_view_job_summary_from_record(row)
                    for row in result.value.get('jobs', [])
                    if isinstance(row, dict)
                )

            return self._list_project_view_recent_jobs_with_helper(
                normalized,
                per_agent_limit=per_agent_limit,
                result_limit=result_limit,
                status_values=status_values,
            )

        current_limit = initial_limit
        while True:
            jobs = self._list_project_view_recent_jobs_python(
                normalized,
                per_agent_limit=current_limit,
                result_limit=result_limit,
                status_values=status_values,
            )
            if len(jobs) >= result_limit or current_limit >= per_agent_limit:
                return jobs
            current_limit = min(per_agent_limit, max(current_limit + 1, current_limit * 2))

    def _list_project_view_recent_jobs_with_helper(
        self,
        agent_names: list[str],
        *,
        per_agent_limit: int,
        result_limit: int,
        status_values: frozenset[str],
    ) -> tuple[ProjectViewJobSummary, ...]:
        from rust_helpers_project_view import read_project_view_recent_jobs_required

        result = read_project_view_recent_jobs_required(
            [
                {
                    'id': agent_name,
                    'path': str(self._layout.target_jobs_path(TargetKind.AGENT, agent_name)),
                    'n': per_agent_limit,
                }
                for agent_name in agent_names
            ],
            statuses=tuple(status_values),
            result_limit=result_limit,
        )
        return tuple(
            _project_view_job_summary_from_record(row)
            for row in result.value.get('jobs', [])
            if isinstance(row, dict)
        )

    def _list_project_view_recent_jobs_python(
        self,
        agent_names: list[str],
        *,
        per_agent_limit: int,
        result_limit: int,
        status_values: frozenset[str],
    ) -> tuple[ProjectViewJobSummary, ...]:
        jobs: list[ProjectViewJobSummary] = []
        for agent_name in agent_names:
            latest_by_job: dict[str, JobRecord] = {}
            for record in self.list_agent_tail(agent_name, limit=per_agent_limit):
                latest_by_job[record.job_id] = record
            jobs.extend(
                _project_view_job_summary_from_job(record)
                for record in latest_by_job.values()
                if record.status.value in status_values
            )
        jobs.sort(key=lambda item: item.updated_at, reverse=True)
        return tuple(jobs[:result_limit])

    def list_target_tail(self, target_kind: TargetKind | str, target_name: str, *, limit: int) -> list[JobRecord]:
        return self._store.read_tail(
            self._layout.target_jobs_path(target_kind, target_name),
            limit,
            loader=_job_record_from_record,
        )

    def get_latest(self, agent_name: str, job_id: str) -> JobRecord | None:
        return self.get_latest_target(TargetKind.AGENT, agent_name, job_id)

    def get_latest_target(self, target_kind: TargetKind | str, target_name: str, job_id: str) -> JobRecord | None:
        return self._store.find_last(
            self._layout.target_jobs_path(target_kind, target_name),
            predicate=lambda payload: str(payload.get('job_id') or '') == job_id,
            loader=_job_record_from_record,
        )


def _strict_jsonl_helper_required() -> bool:
    return str(os.environ.get('CCB_RUST_JSONL_STORE') or '').strip().lower() in {
        '1',
        'true',
        'yes',
        'on',
        'required',
    }


def _project_view_recent_jobs_helper_required() -> bool:
    return str(os.environ.get('CCB_RUST_PROJECT_VIEW_RECENT_JOBS') or '').strip().lower() in {
        '1',
        'true',
        'yes',
        'on',
        'required',
    }


def _job_summary_tail_helper_required() -> bool:
    return str(os.environ.get('CCB_RUST_JOB_SUMMARY_TAIL') or '').strip().lower() in {
        '1',
        'true',
        'yes',
        'on',
        'required',
    }


class JobEventStore:
    def __init__(self, layout: PathLayout, store: JsonlStore | None = None) -> None:
        self._layout = layout
        self._store = store or JsonlStore()

    def append(self, event: JobEvent) -> None:
        self._store.append(
            self._layout.target_events_path(event.target_kind, event.target_name),
            event,
            serializer=lambda value: value.to_record(),
        )

    def read_since(self, agent_name: str, start_line: int = 0) -> tuple[int, list[JobEvent]]:
        return self.read_since_target(TargetKind.AGENT, agent_name, start_line)

    def read_since_target(
        self,
        target_kind: TargetKind | str,
        target_name: str,
        start_line: int = 0,
    ) -> tuple[int, list[JobEvent]]:
        line_no, rows = self._store.read_since(
            self._layout.target_events_path(target_kind, target_name),
            start_line,
        )
        events: list[JobEvent] = []
        for row in rows:
            if row.get('record_type') != 'job_event':
                continue
            events.append(_job_event_from_record(row))
        return line_no, events


class SubmissionStore:
    def __init__(self, layout: PathLayout, store: JsonlStore | None = None) -> None:
        self._layout = layout
        self._store = store or JsonlStore()

    def append(self, record: SubmissionRecord) -> None:
        self._store.append(self._layout.ccbd_submissions_path, record, serializer=lambda value: value.to_record())

    def list_all(self) -> list[SubmissionRecord]:
        return self._store.read_all(self._layout.ccbd_submissions_path, loader=_submission_record_from_record)

    def get_latest(self, submission_id: str) -> SubmissionRecord | None:
        return self._store.find_last(
            self._layout.ccbd_submissions_path,
            predicate=lambda payload: str(payload.get('submission_id') or '') == submission_id,
            loader=_submission_record_from_record,
        )


def _validate_record(record: dict, expected_type: str) -> None:
    if record.get('schema_version') != SCHEMA_VERSION:
        raise ValueError(f'schema_version must be {SCHEMA_VERSION}')
    if record.get('record_type') != expected_type:
        raise ValueError(f'record_type must be {expected_type!r}')


def _message_envelope_from_record(record: dict) -> MessageEnvelope:
    return MessageEnvelope(
        project_id=record['project_id'],
        to_agent=record['to_agent'],
        from_actor=record['from_actor'],
        body=record['body'],
        task_id=record.get('task_id'),
        reply_to=record.get('reply_to'),
        message_type=record['message_type'],
        delivery_scope=DeliveryScope(record['delivery_scope']),
        silence_on_success=bool(record.get('silence_on_success', False)),
        route_options=dict(record.get('route_options') or {}),
        body_artifact=record.get('body_artifact') if isinstance(record.get('body_artifact'), dict) else None,
    )


def _job_record_from_record(record: dict) -> JobRecord:
    _validate_record(record, 'job_record')
    return JobRecord(
        job_id=record['job_id'],
        submission_id=record.get('submission_id'),
        agent_name=record.get('agent_name', ''),
        provider=record['provider'],
        request=_message_envelope_from_record(record['request']),
        status=JobStatus(record['status']),
        terminal_decision=record.get('terminal_decision'),
        cancel_requested_at=record.get('cancel_requested_at'),
        created_at=record['created_at'],
        updated_at=record['updated_at'],
        workspace_path=record.get('workspace_path'),
        target_kind=record.get('target_kind', TargetKind.AGENT.value),
        target_name=record.get('target_name', record.get('agent_name', '')),
        provider_instance=record.get('provider_instance'),
        provider_options=dict(record.get('provider_options') or {}),
        no_reply_reason=record.get('no_reply_reason'),
        no_reply_detail=dict(record.get('no_reply_detail') or {}),
    )


def _project_view_job_summary_from_job(record: JobRecord) -> ProjectViewJobSummary:
    return ProjectViewJobSummary(
        job_id=record.job_id,
        agent_name=record.agent_name,
        provider=record.provider,
        request=ProjectViewMessageSummary(
            project_id=record.request.project_id,
            to_agent=record.request.to_agent,
            from_actor=record.request.from_actor,
            body=record.request.body,
            task_id=record.request.task_id,
            reply_to=record.request.reply_to,
            message_type=record.request.message_type,
            delivery_scope=record.request.delivery_scope.value,
            silence_on_success=record.request.silence_on_success,
            route_options=dict(record.request.route_options),
            body_artifact=dict(record.request.body_artifact) if isinstance(record.request.body_artifact, dict) else None,
        ),
        status=record.status,
        terminal_decision=dict(record.terminal_decision) if isinstance(record.terminal_decision, dict) else None,
        created_at=record.created_at,
        updated_at=record.updated_at,
        target_kind=record.target_kind,
        target_name=record.target_name,
        provider_options=dict(record.provider_options or {}),
    )


def _project_view_job_summary_from_record(record: dict) -> ProjectViewJobSummary:
    request = record.get('request') if isinstance(record.get('request'), dict) else {}
    return ProjectViewJobSummary(
        job_id=str(record.get('job_id') or ''),
        agent_name=str(record.get('agent_name') or ''),
        provider=str(record.get('provider') or ''),
        request=ProjectViewMessageSummary(
            project_id=str(request.get('project_id') or ''),
            to_agent=str(request.get('to_agent') or ''),
            from_actor=str(request.get('from_actor') or ''),
            body=str(request.get('body') or ''),
            task_id=str(request.get('task_id')) if request.get('task_id') is not None else None,
            reply_to=str(request.get('reply_to')) if request.get('reply_to') is not None else None,
            message_type=str(request.get('message_type') or ''),
            delivery_scope=str(request.get('delivery_scope') or ''),
            silence_on_success=bool(request.get('silence_on_success', False)),
            route_options=dict(request.get('route_options') or {}),
            body_artifact=dict(request.get('body_artifact')) if isinstance(request.get('body_artifact'), dict) else None,
        ),
        status=JobStatus(str(record.get('status') or '')),
        terminal_decision=dict(record.get('terminal_decision')) if isinstance(record.get('terminal_decision'), dict) else None,
        created_at=str(record.get('created_at') or ''),
        updated_at=str(record.get('updated_at') or ''),
        target_kind=TargetKind(str(record.get('target_kind') or TargetKind.AGENT.value)),
        target_name=str(record.get('target_name') or record.get('agent_name') or ''),
        provider_options=dict(record.get('provider_options') or {}),
    )


def _submission_record_from_record(record: dict) -> SubmissionRecord:
    _validate_record(record, 'submission_record')
    return SubmissionRecord(
        submission_id=record['submission_id'],
        project_id=record['project_id'],
        from_actor=record['from_actor'],
        target_scope=record['target_scope'],
        task_id=record.get('task_id'),
        job_ids=list(record.get('job_ids', [])),
        created_at=record.get('created_at', ''),
        updated_at=record.get('updated_at', ''),
    )


def _job_event_from_record(record: dict) -> JobEvent:
    _validate_record(record, 'job_event')
    return JobEvent(
        event_id=record['event_id'],
        job_id=record['job_id'],
        agent_name=record.get('agent_name', ''),
        target_kind=record.get('target_kind', TargetKind.AGENT.value),
        target_name=record.get('target_name', record.get('agent_name', '')),
        type=record['type'],
        payload=dict(record.get('payload', {})),
        timestamp=record['timestamp'],
    )
