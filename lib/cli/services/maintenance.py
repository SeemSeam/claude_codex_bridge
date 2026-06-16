from __future__ import annotations

import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import timedelta
from pathlib import Path

from agents.config_loader import load_project_config
from ccbd.api_models import DeliveryScope, MessageEnvelope
from ccbd.services.lifecycle import CcbdLifecycleStore
from ccbd.system import parse_utc_timestamp, utc_now
from cli.context import CliContext
from cli.kill_runtime.processes import is_pid_alive as _process_pid_alive
from cli.models import ParsedMaintenanceCommand, ParsedPsCommand
from mailbox_runtime import MAINTENANCE_HEARTBEAT_ACTOR
from maintenance_heartbeat import (
    MaintenanceHeartbeatActivation,
    MaintenanceHeartbeatEvaluation,
    MaintenanceHeartbeatLock,
    MaintenanceHeartbeatLockBusy,
    MaintenanceHeartbeatReadResult,
    MaintenanceHeartbeatRunner,
    MaintenanceHeartbeatSchedule,
    MaintenanceHeartbeatStatus,
    MaintenanceHeartbeatStore,
    evaluate_project_view,
    evaluate_ps_summary,
)
from runtime_env.control_plane import control_plane_env

from .daemon import connect_mounted_daemon, invoke_mounted_daemon
from .ps import ps_summary

_ACTIVATION_TAIL_LIMIT = 100
_MESSAGE_EVIDENCE_LIMIT = 5
_ACTIVE_ACTIVATION_BUSINESS_STATUSES = {'delivering', 'replying', 'sending'}
_ACTIVE_JOB_STATUSES = {'accepted', 'queued', 'running'}
_RUNNER_DEFAULT_SLEEP_CAP_S = 30.0
_RUNNER_STOP_WAIT_S = 1.0
_DEDUP_VOLATILE_KEYS = {
    'accepted_at',
    'ready_at',
    'last_progress_at',
    'no_terminal_deadline_at',
    'delivery_started_at',
    'delivery_timeout_deadline_at',
    'delivery_confirmed_at',
    'delivery_failed_at',
    'prompt_sent_at',
    'ready_wait_started_at',
    'reliability_last_progress_at',
    'reliability_timeout_deadline_at',
    'next_seq',
}


@dataclass(frozen=True)
class _RuntimeObservation:
    evaluation: MaintenanceHeartbeatEvaluation
    payload: Mapping[str, object]


def maintenance_status(context: CliContext, command: ParsedMaintenanceCommand) -> dict:
    action = str(command.action or 'status').strip().lower()
    if action == 'status':
        return _maintenance_status(context)
    if action == 'tick':
        return _maintenance_tick(context, command)
    if action == 'schedule':
        return _maintenance_schedule(context, command)
    if action == 'runner':
        return _maintenance_runner(context, command)
    if action in {'enable', 'disable'}:
        return {
            'maintenance_status': 'not_implemented',
            'action': action,
            'reason': 'heartbeat enablement is config-authority in v1; edit [maintenance.heartbeat].enabled',
        }
    return {
        'maintenance_status': 'not_implemented',
        'action': action,
        'reason': 'unsupported maintenance action',
    }


def _maintenance_status(context: CliContext) -> dict:
    loaded = load_project_config(context.project.project_root)
    heartbeat = loaded.config.maintenance_heartbeat
    store = MaintenanceHeartbeatStore(context.paths, project_id=context.project.project_id)
    schedule = store.load_schedule()
    last_status = store.load_status()
    runner = store.load_runner()
    last_activation = _load_last_activation(store, context)
    degraded = schedule.state == 'corrupt' or last_status.state == 'corrupt' or runner.state == 'corrupt'
    return {
        'maintenance_status': 'degraded' if degraded else 'ok',
        'project': str(context.project.project_root),
        'project_id': context.project.project_id,
        'config_source_kind': loaded.source_kind,
        'config_source': str(loaded.source_path) if loaded.source_path else None,
        'enabled': heartbeat.enabled,
        'assessor': heartbeat.assessor,
        'assessor_present': heartbeat.assessor in loaded.config.agents,
        'interval_s': heartbeat.interval_s,
        'min_interval_s': heartbeat.min_interval_s,
        'unknown_streak_cap': heartbeat.unknown_streak_cap,
        'escalation_policy': heartbeat.escalation_policy,
        'startup_ensure': heartbeat.startup_ensure,
        'schedule': schedule.to_record(),
        'last_status': last_status.to_record(),
        'runner': runner.to_record(),
        'last_activation': last_activation,
    }


def _maintenance_tick(context: CliContext, command: ParsedMaintenanceCommand) -> dict:
    loaded = load_project_config(context.project.project_root)
    heartbeat = loaded.config.maintenance_heartbeat
    store = MaintenanceHeartbeatStore(context.paths, project_id=context.project.project_id)
    try:
        tick_options = _parse_tick_args(command.args)
    except ValueError as exc:
        return {
            'maintenance_status': 'invalid',
            'action': 'tick',
            'reason': str(exc),
        }
    if not heartbeat.enabled:
        return {
            **_maintenance_status(context),
            'maintenance_status': 'ok',
            'action': 'tick',
            'tick_status': 'disabled',
            'tick_source_kind': 'disabled',
            'tick_recommended_action': 'none',
            'tick_needs_user': False,
            'tick_next_heartbeat_after_s': None,
            'status_written': False,
            'schedule_written': False,
            'activation_written': False,
            'tick_activation_status': None,
            'tick_activation_id': None,
            'tick_activation_job_id': None,
            'tick_summary': {'source_kind': 'disabled'},
            'tick_evidence': [],
            'reason': 'maintenance heartbeat is disabled by effective config',
        }

    observed_at = utc_now()
    try:
        with _heartbeat_lock(context, action='tick', observed_at=observed_at):
            if not tick_options['force']:
                current_schedule = store.load_schedule()
                if _schedule_is_future(current_schedule, observed_at):
                    return {
                        **_maintenance_status(context),
                        'maintenance_status': 'ok',
                        'action': 'tick',
                        'tick_status': 'too_early',
                        'tick_source_kind': 'schedule',
                        'tick_recommended_action': 'none',
                        'tick_needs_user': False,
                        'tick_next_heartbeat_after_s': None,
                        'status_written': False,
                        'schedule_written': False,
                        'activation_written': False,
                        'tick_activation_status': None,
                        'tick_activation_id': None,
                        'tick_activation_job_id': None,
                        'tick_summary': {'source_kind': 'schedule'},
                        'tick_evidence': [],
                        'reason': 'heartbeat schedule is not due; use `ccb maintenance tick --force` to run now',
                    }
            return _run_due_tick(
                context,
                loaded=loaded,
                heartbeat=heartbeat,
                store=store,
                observed_at=observed_at,
                dispatch=bool(tick_options['dispatch']),
            )
    except MaintenanceHeartbeatLockBusy:
        return {
            **_maintenance_status(context),
            'maintenance_status': 'ok',
            'action': 'tick',
            'tick_status': 'locked',
            'tick_source_kind': 'lock',
            'tick_recommended_action': 'none',
            'tick_needs_user': False,
            'tick_next_heartbeat_after_s': None,
            'status_written': False,
            'schedule_written': False,
            'activation_written': False,
            'tick_activation_status': None,
            'tick_activation_id': None,
            'tick_activation_job_id': None,
            'tick_summary': {'source_kind': 'lock'},
            'tick_evidence': [],
            'reason': 'another maintenance heartbeat tick is active',
        }


def _maintenance_schedule(context: CliContext, command: ParsedMaintenanceCommand) -> dict:
    loaded = load_project_config(context.project.project_root)
    heartbeat = loaded.config.maintenance_heartbeat
    store = MaintenanceHeartbeatStore(context.paths, project_id=context.project.project_id)
    try:
        schedule_options = _parse_schedule_args(command.args)
    except ValueError as exc:
        return {
            'maintenance_status': 'invalid',
            'action': 'schedule',
            'reason': str(exc),
        }
    if not heartbeat.enabled:
        return {
            **_maintenance_status(context),
            'maintenance_status': 'degraded',
            'action': 'schedule',
            'schedule_status': 'disabled',
            'schedule_written': False,
            'requested_after_s': schedule_options['after_s'],
            'scheduled_after_s': None,
            'reason': 'maintenance heartbeat is disabled by effective config',
        }
    observed_at = utc_now()
    delay_s = max(int(schedule_options['after_s']), int(heartbeat.min_interval_s))
    next_run_at = _after_seconds(observed_at, delay_s)
    try:
        with _heartbeat_lock(context, action='schedule', observed_at=observed_at):
            store.save_schedule(
                MaintenanceHeartbeatSchedule(
                    project_id=context.project.project_id,
                    next_run_at=next_run_at,
                    reason=schedule_options['reason'],
                    updated_at=observed_at,
                    updated_by='maintenance_schedule',
                )
            )
    except MaintenanceHeartbeatLockBusy:
        return {
            **_maintenance_status(context),
            'maintenance_status': 'ok',
            'action': 'schedule',
            'schedule_status': 'locked',
            'schedule_written': False,
            'requested_after_s': schedule_options['after_s'],
            'scheduled_after_s': None,
            'reason': 'another maintenance heartbeat operation is active',
        }
    return {
        **_maintenance_status(context),
        'maintenance_status': 'ok',
        'action': 'schedule',
        'schedule_status': 'scheduled',
        'schedule_written': True,
        'requested_after_s': schedule_options['after_s'],
        'scheduled_after_s': delay_s,
        'next_run_at': next_run_at,
    }


def _maintenance_runner(context: CliContext, command: ParsedMaintenanceCommand) -> dict:
    try:
        options = _parse_runner_args(command.args)
    except ValueError as exc:
        return {
            'maintenance_status': 'invalid',
            'action': 'runner',
            'reason': str(exc),
        }
    return _run_maintenance_runner(
        context,
        runner_id=str(options['runner_id'] or _runner_id()),
        source=str(options['source']),
        max_iterations=options['max_iterations'],
        sleep_cap_s=float(options['sleep_cap_s']),
        dispatch=bool(options['dispatch']),
    )


def _run_maintenance_runner(
    context: CliContext,
    *,
    runner_id: str,
    source: str,
    max_iterations: int | None,
    sleep_cap_s: float,
    dispatch: bool,
) -> dict:
    store = MaintenanceHeartbeatStore(context.paths, project_id=context.project.project_id)
    current_pid = os.getpid()
    existing = store.load_runner()
    if _runner_read_result_is_live(existing, exclude_pid=current_pid):
        return {
            **_maintenance_status(context),
            'maintenance_status': 'ok',
            'action': 'runner',
            'runner_status': 'already_running',
            'runner_pid': existing.value.pid if existing.value is not None else None,
            'runner_id': existing.value.runner_id if existing.value is not None else None,
        }

    stop_event = threading.Event()
    previous_handlers = _install_runner_signal_handlers(stop_event)
    started_at = utc_now()
    runner = MaintenanceHeartbeatRunner(
        project_id=context.project.project_id,
        runner_id=runner_id,
        pid=current_pid,
        state='running',
        source=source,
        started_at=started_at,
        last_seen_at=started_at,
    )
    store.save_runner(runner)
    iterations = 0
    exit_reason = 'max_iterations' if max_iterations == 0 else 'stopped'
    failure_error: str | None = None
    try:
        while not stop_event.is_set():
            observed_at = utc_now()
            loaded = load_project_config(context.project.project_root)
            heartbeat = loaded.config.maintenance_heartbeat
            if not heartbeat.enabled:
                exit_reason = 'disabled'
                break
            if heartbeat.assessor not in loaded.config.agents:
                exit_reason = f'assessor_missing:{heartbeat.assessor}'
                break
            if not _project_lifecycle_allows_runner(context):
                exit_reason = 'project_stopped'
                break

            schedule = store.load_schedule()
            if _schedule_is_future(schedule, observed_at):
                next_run_at = schedule.value.next_run_at if schedule.value is not None else None
                runner = _runner_update(
                    runner,
                    state='sleeping',
                    last_seen_at=observed_at,
                    observed_next_run_at=next_run_at,
                    sleep_until=next_run_at,
                    exit_reason=None,
                )
                store.save_runner(runner)
                iterations += 1
                if max_iterations is not None and iterations >= max_iterations:
                    exit_reason = 'max_iterations'
                    break
                delay_s = min(_seconds_until(observed_at, next_run_at), max(0.0, float(sleep_cap_s)))
                if delay_s > 0:
                    stop_event.wait(delay_s)
                continue

            runner = _runner_update(
                runner,
                state='running',
                last_seen_at=observed_at,
                last_wake_at=observed_at,
                observed_next_run_at=schedule.value.next_run_at if schedule.value is not None else None,
                sleep_until=None,
                exit_reason=None,
            )
            store.save_runner(runner)
            tick_args = () if dispatch else ('--no-dispatch',)
            tick_payload = _maintenance_tick(
                context,
                ParsedMaintenanceCommand(project=getattr(context.command, 'project', None), action='tick', args=tick_args),
            )
            status_record = tick_payload.get('last_status')
            tick_at = _nested_record_value(status_record, 'last_tick_at') or observed_at
            tick_status = str(tick_payload.get('tick_status') or '').strip() or None
            runner = _runner_update(
                runner,
                state='running',
                last_seen_at=utc_now(),
                last_tick_at=tick_at,
                last_tick_status=tick_status,
                observed_next_run_at=_nested_record_value(tick_payload.get('schedule'), 'next_run_at'),
                sleep_until=None,
                exit_reason=None,
            )
            store.save_runner(runner)
            iterations += 1
            if max_iterations is not None and iterations >= max_iterations:
                exit_reason = 'max_iterations'
                break

        if stop_event.is_set():
            exit_reason = 'signal'
    except Exception as exc:
        exit_reason = f'error:{exc}'
        failure_error = str(exc)
    finally:
        _restore_runner_signal_handlers(previous_handlers)
        stopped_at = utc_now()
        store.save_runner(
            _runner_update(
                runner,
                state='stopped',
                last_seen_at=stopped_at,
                sleep_until=None,
                exit_reason=exit_reason,
            )
        )
    return {
        **_maintenance_status(context),
        'maintenance_status': 'degraded' if failure_error else 'ok',
        'action': 'runner',
        'runner_status': 'stopped',
        'runner_id': runner_id,
        'runner_pid': current_pid,
        'runner_exit_reason': exit_reason,
        'runner_iterations': iterations,
        'reason': failure_error,
    }


def _run_due_tick(
    context: CliContext,
    *,
    loaded,
    heartbeat,
    store: MaintenanceHeartbeatStore,
    observed_at: str,
    dispatch: bool,
) -> dict:
    observation = _evaluate_runtime(context)
    evaluation = observation.evaluation
    previous = store.load_status()
    unknown_streak = _next_unknown_streak(evaluation.health, previous)
    next_after_s = _next_after_s(evaluation.health, heartbeat=heartbeat, unknown_streak=unknown_streak)
    unknown_cap_reached = evaluation.health == 'unknown' and unknown_streak >= int(heartbeat.unknown_streak_cap)
    activation = _maybe_activate_assessor(
        context,
        loaded=loaded,
        heartbeat=heartbeat,
        store=store,
        observation=observation,
        observed_at=observed_at,
        next_after_s=next_after_s,
        dispatch=dispatch,
    )
    needs_user = bool(evaluation.needs_user or unknown_cap_reached or _activation_needs_user(activation))
    status = MaintenanceHeartbeatStatus(
        project_id=context.project.project_id,
        last_tick_status=evaluation.health,
        last_tick_at=observed_at,
        last_ok_at=observed_at if evaluation.health == 'healthy' else _previous_last_ok(previous),
        last_error=_first_issue_reason(evaluation.evidence),
        unknown_streak=unknown_streak,
        updated_at=observed_at,
        source_kind=evaluation.source_kind,
        recommended_action=evaluation.recommended_action,
        next_heartbeat_after_s=next_after_s,
        needs_user=needs_user,
        summary=evaluation.summary,
        evidence=evaluation.evidence,
        last_activation_status=getattr(activation, 'status', None),
        last_activation_id=getattr(activation, 'activation_id', None),
        last_activation_job_id=getattr(activation, 'job_id', None),
        last_activation_target=getattr(activation, 'target_agent', None),
        last_activation_dedup_key=getattr(activation, 'dedup_key', None),
    )
    schedule = MaintenanceHeartbeatSchedule(
        project_id=context.project.project_id,
        next_run_at=_after_seconds(observed_at, next_after_s),
        reason=f'{evaluation.health}_tick',
        updated_at=observed_at,
        updated_by='maintenance_tick',
    )
    store.save_status(status)
    store.save_schedule(schedule)
    return {
        **_maintenance_status(context),
        'maintenance_status': 'degraded' if _activation_needs_user(activation) else 'ok',
        'action': 'tick',
        'tick_status': evaluation.health,
        'tick_source_kind': evaluation.source_kind,
        'tick_recommended_action': evaluation.recommended_action,
        'tick_needs_user': needs_user,
        'tick_next_heartbeat_after_s': next_after_s,
        'status_written': True,
        'schedule_written': True,
        'activation_written': activation is not None,
        'tick_activation_status': getattr(activation, 'status', None),
        'tick_activation_id': getattr(activation, 'activation_id', None),
        'tick_activation_job_id': getattr(activation, 'job_id', None),
        'tick_summary': evaluation.summary,
        'tick_evidence': list(evaluation.evidence),
    }


def _evaluate_runtime(context: CliContext) -> _RuntimeObservation:
    try:
        handle = connect_mounted_daemon(context, allow_restart_stale=False)
        assert handle.client is not None
        payload = handle.client.project_view(schema_version=1)
        return _RuntimeObservation(evaluation=evaluate_project_view(payload), payload=payload)
    except Exception as exc:
        fallback = ps_summary(context, ParsedPsCommand(project=getattr(context.command, 'project', None)))
        return _RuntimeObservation(evaluation=evaluate_ps_summary(fallback, error=str(exc)), payload=fallback)


def _next_after_s(health: str, *, heartbeat, unknown_streak: int = 0) -> int:
    if health == 'healthy':
        return int(heartbeat.interval_s)
    if health == 'unknown' and unknown_streak >= int(heartbeat.unknown_streak_cap):
        return int(heartbeat.interval_s)
    return int(heartbeat.min_interval_s)


def _after_seconds(timestamp: str, seconds: int) -> str:
    return (parse_utc_timestamp(timestamp) + timedelta(seconds=int(seconds))).isoformat().replace('+00:00', 'Z')


def _previous_last_ok(previous: MaintenanceHeartbeatReadResult[MaintenanceHeartbeatStatus]) -> str | None:
    if previous.value is None:
        return None
    return previous.value.last_ok_at


def _next_unknown_streak(health: str, previous: MaintenanceHeartbeatReadResult[MaintenanceHeartbeatStatus]) -> int:
    if health != 'unknown':
        return 0
    if previous.value is None:
        return 1
    return int(previous.value.unknown_streak or 0) + 1


def _first_issue_reason(evidence: tuple[dict, ...]) -> str | None:
    if not evidence:
        return None
    first = evidence[0]
    reason = str(first.get('reason') or '').strip()
    return reason or str(first.get('kind') or '').strip() or None


def _maybe_activate_assessor(
    context: CliContext,
    *,
    loaded,
    heartbeat,
    store: MaintenanceHeartbeatStore,
    observation: _RuntimeObservation,
    observed_at: str,
    next_after_s: int,
    dispatch: bool,
) -> MaintenanceHeartbeatActivation | None:
    evaluation = observation.evaluation
    if evaluation.health == 'healthy':
        return None
    target = str(heartbeat.assessor or '').strip()
    dedup_key = _diagnostic_dedup_key(context, evaluation)
    activation_id = _activation_id()
    common = {
        'project_id': context.project.project_id,
        'activation_id': activation_id,
        'condition_kind': 'heartbeat_state_check',
        'trigger_kind': 'state_check',
        'source': evaluation.source_kind,
        'observed_at': observed_at,
        'target_agent': target,
        'delivery_mode': 'ask_silence',
        'payload_kind': 'maintenance_diagnostic',
        'dedup_key': dedup_key,
        'reason': _first_issue_reason(evaluation.evidence) or evaluation.health,
        'repeat_count': _repeat_count(store, dedup_key),
        'payload_summary': _activation_summary(evaluation, next_after_s=next_after_s),
        'evidence': tuple(evaluation.evidence[:_MESSAGE_EVIDENCE_LIMIT]),
    }
    if target not in loaded.config.agents:
        activation = MaintenanceHeartbeatActivation(
            status='blocked',
            suppressed_reason='assessor_missing',
            error=f'configured assessor is not present: {target}',
            **common,
        )
        store.append_activation(activation)
        return activation
    if not dispatch:
        activation = MaintenanceHeartbeatActivation(
            status='suppressed',
            suppressed_reason='dispatch_disabled',
            **common,
        )
        store.append_activation(activation)
        return activation
    active_job = _active_maintenance_job(observation.payload, target_agent=target)
    if active_job:
        activation = MaintenanceHeartbeatActivation(
            status='suppressed',
            suppressed_reason=f'active_maintenance_job:{active_job}',
            **common,
        )
        store.append_activation(activation)
        return activation
    duplicate = _recent_duplicate(store, dedup_key=dedup_key, observed_at=observed_at, window_s=int(heartbeat.min_interval_s))
    if duplicate is not None:
        activation = MaintenanceHeartbeatActivation(
            status='suppressed',
            suppressed_reason=f'recent_duplicate:{duplicate.activation_id}',
            job_id=duplicate.job_id,
            **common,
        )
        store.append_activation(activation)
        return activation
    try:
        job_id = _dispatch_activation(
            context,
            target_agent=target,
            activation_id=activation_id,
            dedup_key=dedup_key,
            observed_at=observed_at,
            evaluation=evaluation,
            next_after_s=next_after_s,
        )
        activation = MaintenanceHeartbeatActivation(
            status='submitted',
            job_id=job_id,
            submitted_at=observed_at,
            **common,
        )
    except Exception as exc:
        activation = MaintenanceHeartbeatActivation(
            status='failed',
            error=str(exc),
            **common,
        )
    store.append_activation(activation)
    return activation


def _dispatch_activation(
    context: CliContext,
    *,
    target_agent: str,
    activation_id: str,
    dedup_key: str,
    observed_at: str,
    evaluation: MaintenanceHeartbeatEvaluation,
    next_after_s: int,
) -> str | None:
    request = MessageEnvelope(
        project_id=context.project.project_id,
        to_agent=target_agent,
        from_actor=MAINTENANCE_HEARTBEAT_ACTOR,
        body=_activation_message(
            context,
            activation_id=activation_id,
            dedup_key=dedup_key,
            observed_at=observed_at,
            evaluation=evaluation,
            next_after_s=next_after_s,
        ),
        task_id=f'maintenance-heartbeat:{dedup_key}',
        reply_to=None,
        message_type='ask',
        delivery_scope=DeliveryScope.SINGLE,
        silence_on_success=True,
        route_options={
            'maintenance_heartbeat': True,
            'activation_id': activation_id,
            'dedup_key': dedup_key,
        },
    )
    payload = invoke_mounted_daemon(
        context,
        allow_restart_stale=False,
        request_fn=lambda client: client.submit(request),
    )
    return _submitted_job_id(payload)


def _activation_message(
    context: CliContext,
    *,
    activation_id: str,
    dedup_key: str,
    observed_at: str,
    evaluation: MaintenanceHeartbeatEvaluation,
    next_after_s: int,
) -> str:
    package = {
        'schema_version': 1,
        'record_type': 'maintenance_heartbeat_diagnostic',
        'activation_id': activation_id,
        'project_id': context.project.project_id,
        'project': str(context.project.project_root),
        'observed_at': observed_at,
        'health': evaluation.health,
        'source_kind': evaluation.source_kind,
        'dedup_key': dedup_key,
        'recommended_action': evaluation.recommended_action,
        'next_heartbeat_after_s': next_after_s,
        'allowed_actions': _activation_allowed_actions(evaluation.evidence),
        'summary': _bounded_mapping(evaluation.summary),
        'evidence': list(evaluation.evidence[:_MESSAGE_EVIDENCE_LIMIT]),
    }
    diagnostic = json.dumps(package, ensure_ascii=False, indent=2, sort_keys=True)
    return (
        'CCB maintenance heartbeat detected a runtime condition that needs semantic supervision.\n\n'
        'Assess the diagnostic package from the ccb_self running-supervision perspective. '
        'Use only actions explicitly allowed by the diagnostic package evidence. Prefer read-only diagnosis; '
        'do not restart or repair unless the package allows it and no active business work would be duplicated. '
        'If a delayed follow-up is needed, request `ccb maintenance schedule --after <duration> --reason <reason>` '
        'through the CCB control plane.\n\n'
        'Diagnostic package:\n'
        '```json\n'
        f'{diagnostic}\n'
        '```\n\n'
        'CCB reply guidance:\n'
        '- Silent-on-success requested.\n'
        '- Reply only with blockers, risks, needed user action, or a schedule recommendation.\n'
        '- Do not include raw logs unless essential.'
    )


def _submitted_job_id(payload: dict) -> str | None:
    job_id = str(payload.get('job_id') or '').strip()
    if job_id:
        return job_id
    jobs = payload.get('jobs')
    if isinstance(jobs, (list, tuple)) and jobs:
        first = jobs[0]
        if isinstance(first, Mapping):
            return str(first.get('job_id') or '').strip() or None
    return None


def _activation_summary(evaluation: MaintenanceHeartbeatEvaluation, *, next_after_s: int) -> dict[str, object]:
    return {
        'health': evaluation.health,
        'source_kind': evaluation.source_kind,
        'recommended_action': evaluation.recommended_action,
        'next_heartbeat_after_s': next_after_s,
        **_bounded_mapping(evaluation.summary),
    }


def _activation_allowed_actions(evidence: tuple[dict, ...]) -> list[str]:
    actions: list[str] = []
    seen: set[str] = set()
    for item in evidence[:_MESSAGE_EVIDENCE_LIMIT]:
        raw_actions = item.get('allowed_actions')
        if not isinstance(raw_actions, (list, tuple)):
            continue
        for value in raw_actions:
            action = str(value or '').strip()
            if not action or action in seen:
                continue
            seen.add(action)
            actions.append(action)
    return actions


def _bounded_mapping(payload: Mapping[str, object]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in payload.items():
        if isinstance(value, (str, int, float, bool)) or value is None:
            result[str(key)] = value
    return result


def _diagnostic_dedup_key(context: CliContext, evaluation: MaintenanceHeartbeatEvaluation) -> str:
    payload = {
        'project_id': context.project.project_id,
        'health': evaluation.health,
        'source_kind': evaluation.source_kind,
        'summary': _bounded_mapping(evaluation.summary),
        'evidence': _dedup_stable_value(list(evaluation.evidence[:_MESSAGE_EVIDENCE_LIMIT])),
    }
    digest = hashlib.sha256(json.dumps(payload, ensure_ascii=False, sort_keys=True).encode('utf-8')).hexdigest()
    return f'maintenance:{digest[:20]}'


def _dedup_stable_value(value: object) -> object:
    if isinstance(value, Mapping):
        result: dict[str, object] = {}
        for key, item in value.items():
            text_key = str(key)
            if text_key in _DEDUP_VOLATILE_KEYS:
                continue
            result[text_key] = _dedup_stable_value(item)
        return result
    if isinstance(value, (list, tuple)):
        return [_dedup_stable_value(item) for item in value]
    return value


def _activation_id() -> str:
    return f'act_{uuid.uuid4().hex[:16]}'


def _active_maintenance_job(payload: Mapping[str, object], *, target_agent: str) -> str | None:
    view = payload.get('view') if isinstance(payload.get('view'), Mapping) else payload
    if not isinstance(view, Mapping):
        return None
    comms = view.get('comms')
    if not isinstance(comms, (list, tuple)):
        return None
    for item in comms:
        if not isinstance(item, Mapping):
            continue
        sender = str(item.get('sender') or '').strip()
        target = str(item.get('target') or '').strip()
        if sender != MAINTENANCE_HEARTBEAT_ACTOR or target != target_agent:
            continue
        business_status = str(item.get('business_status') or '').strip()
        status = str(item.get('status') or '').strip()
        if business_status in _ACTIVE_ACTIVATION_BUSINESS_STATUSES or status in _ACTIVE_JOB_STATUSES:
            return str(item.get('id') or '').strip() or '<unknown>'
    return None


def _repeat_count(store: MaintenanceHeartbeatStore, dedup_key: str) -> int:
    return 1 + sum(1 for item in _activation_tail(store) if item.dedup_key == dedup_key)


def _recent_duplicate(
    store: MaintenanceHeartbeatStore,
    *,
    dedup_key: str,
    observed_at: str,
    window_s: int,
) -> MaintenanceHeartbeatActivation | None:
    observed = parse_utc_timestamp(observed_at)
    for item in reversed(_activation_tail(store)):
        if item.dedup_key != dedup_key or item.status != 'submitted':
            continue
        submitted_at = item.submitted_at or item.observed_at
        try:
            submitted = parse_utc_timestamp(submitted_at)
        except Exception:
            return item
        if (observed - submitted).total_seconds() < int(window_s):
            return item
        return None
    return None


def _activation_tail(store: MaintenanceHeartbeatStore) -> tuple[MaintenanceHeartbeatActivation, ...]:
    try:
        return store.load_activation_tail(_ACTIVATION_TAIL_LIMIT)
    except Exception:
        return ()


def _activation_needs_user(activation: MaintenanceHeartbeatActivation | None) -> bool:
    return activation is not None and activation.status in {'blocked', 'failed'}


def _parse_tick_args(args: tuple[str, ...]) -> dict[str, bool]:
    force = False
    dispatch = True
    for token in args:
        if token == '--force':
            force = True
        elif token == '--no-dispatch':
            dispatch = False
        else:
            raise ValueError('tick supports only: --force, --no-dispatch')
    return {'force': force, 'dispatch': dispatch}


def _parse_schedule_args(args: tuple[str, ...]) -> dict[str, object]:
    after_s: int | None = None
    reason = 'manual_schedule'
    index = 0
    while index < len(args):
        token = args[index]
        if token == '--after':
            index += 1
            if index >= len(args):
                raise ValueError('schedule --after requires a duration')
            after_s = _duration_seconds(args[index])
        elif token == '--reason':
            index += 1
            if index >= len(args):
                raise ValueError('schedule --reason requires text')
            reason = str(args[index] or '').strip() or 'manual_schedule'
        else:
            raise ValueError('schedule supports only: --after <duration> [--reason <text>]')
        index += 1
    if after_s is None:
        raise ValueError('schedule requires --after <duration>')
    return {'after_s': after_s, 'reason': reason}


def _parse_runner_args(args: tuple[str, ...]) -> dict[str, object]:
    runner_id: str | None = None
    source = 'manual'
    max_iterations: int | None = None
    sleep_cap_s = _RUNNER_DEFAULT_SLEEP_CAP_S
    dispatch = True
    index = 0
    while index < len(args):
        token = args[index]
        if token == '--runner-id':
            index += 1
            if index >= len(args):
                raise ValueError('runner --runner-id requires text')
            runner_id = str(args[index] or '').strip() or None
        elif token == '--source':
            index += 1
            if index >= len(args):
                raise ValueError('runner --source requires text')
            source = str(args[index] or '').strip() or 'manual'
        elif token == '--max-iterations':
            index += 1
            if index >= len(args):
                raise ValueError('runner --max-iterations requires an integer')
            max_iterations = int(args[index])
            if max_iterations < 0:
                raise ValueError('runner --max-iterations cannot be negative')
        elif token == '--sleep-cap':
            index += 1
            if index >= len(args):
                raise ValueError('runner --sleep-cap requires a duration')
            sleep_cap_s = float(_duration_seconds(args[index]))
        elif token == '--no-dispatch':
            dispatch = False
        else:
            raise ValueError(
                'runner supports only: --runner-id <id>, --source <text>, '
                '--max-iterations <n>, --sleep-cap <duration>, --no-dispatch'
            )
        index += 1
    return {
        'runner_id': runner_id,
        'source': source,
        'max_iterations': max_iterations,
        'sleep_cap_s': sleep_cap_s,
        'dispatch': dispatch,
    }


def _duration_seconds(value: str) -> int:
    text = str(value or '').strip().lower()
    if not text:
        raise ValueError('duration cannot be empty')
    multiplier = 1
    number = text
    if text[-1:] in {'s', 'm', 'h', 'd'}:
        suffix = text[-1]
        number = text[:-1]
        multiplier = {'s': 1, 'm': 60, 'h': 3600, 'd': 86400}[suffix]
    try:
        amount = int(number)
    except ValueError as exc:
        raise ValueError(f'invalid duration: {value}') from exc
    seconds = amount * multiplier
    if seconds <= 0:
        raise ValueError('duration must be positive')
    return seconds


def _schedule_is_future(
    schedule: MaintenanceHeartbeatReadResult[MaintenanceHeartbeatSchedule],
    observed_at: str,
) -> bool:
    if schedule.value is None or not schedule.value.next_run_at:
        return False
    try:
        return parse_utc_timestamp(schedule.value.next_run_at) > parse_utc_timestamp(observed_at)
    except Exception:
        return False


def _seconds_until(observed_at: str, next_run_at: str | None) -> float:
    if not next_run_at:
        return 0.0
    try:
        return max(0.0, (parse_utc_timestamp(next_run_at) - parse_utc_timestamp(observed_at)).total_seconds())
    except Exception:
        return 0.0


def _heartbeat_lock(context: CliContext, *, action: str, observed_at: str) -> MaintenanceHeartbeatLock:
    return MaintenanceHeartbeatLock(
        context.paths.ccbd_maintenance_heartbeat_lock_path,
        payload={
            'schema_version': 1,
            'record_type': 'maintenance_heartbeat_lock',
            'project_id': context.project.project_id,
            'pid': os.getpid(),
            'action': action,
            'started_at': observed_at,
        },
    )


def _load_last_activation(store: MaintenanceHeartbeatStore, context: CliContext) -> dict[str, object]:
    path = context.paths.ccbd_maintenance_heartbeat_activations_path
    if not path.exists():
        return {'state': 'missing', 'path': str(path), 'error': None}
    try:
        tail = store.load_activation_tail(1)
    except Exception as exc:
        return {'state': 'corrupt', 'path': str(path), 'error': str(exc)}
    if not tail:
        return {'state': 'missing', 'path': str(path), 'error': None}
    return {'state': 'ok', 'path': str(path), 'error': None, 'record': tail[-1].to_record()}


def _runner_id() -> str:
    return f'runner_{uuid.uuid4().hex[:16]}'


def _runner_update(runner: MaintenanceHeartbeatRunner, **kwargs) -> MaintenanceHeartbeatRunner:
    return replace(runner, **kwargs)


def _nested_record_value(payload: object, key: str) -> str | None:
    if not isinstance(payload, Mapping):
        return None
    record = payload.get('record')
    if not isinstance(record, Mapping):
        return None
    value = str(record.get(key) or '').strip()
    return value or None


def _install_runner_signal_handlers(stop_event: threading.Event):
    if threading.current_thread() is not threading.main_thread():
        return {}
    previous = {}

    def _handler(signum, frame):
        del signum, frame
        stop_event.set()

    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, _handler)
        except Exception:
            continue
    return previous


def _restore_runner_signal_handlers(previous) -> None:
    for signum, handler in dict(previous or {}).items():
        try:
            signal.signal(signum, handler)
        except Exception:
            continue


def _runner_read_result_is_live(
    result: MaintenanceHeartbeatReadResult[MaintenanceHeartbeatRunner],
    *,
    exclude_pid: int | None = None,
) -> bool:
    runner = result.value
    if runner is None or runner.pid is None:
        return False
    if exclude_pid is not None and int(runner.pid) == int(exclude_pid):
        return False
    if runner.state in {'stopped', 'failed'}:
        return False
    return _pid_alive(int(runner.pid))


def _pid_alive(pid: int) -> bool:
    return _process_pid_alive(int(pid))


def _project_lifecycle_allows_runner(context: CliContext) -> bool:
    try:
        lifecycle = CcbdLifecycleStore(context.paths).load()
    except Exception:
        return True
    if lifecycle is None:
        return True
    desired = str(getattr(lifecycle, 'desired_state', '') or '').strip()
    phase = str(getattr(lifecycle, 'phase', '') or '').strip()
    if desired == 'stopped':
        return False
    if phase in {'stopping', 'unmounted'}:
        return False
    return True


def _script_root() -> Path:
    return Path(__file__).resolve().parents[3]


def _runner_env() -> dict[str, str]:
    extra = {
        'PYTHONUNBUFFERED': '1',
        'CCB_MAINTENANCE_HEARTBEAT_RUNNER': '1',
    }
    for key in (
        'CCB_SOURCE_ALLOWED_ROOTS',
        'CCB_TEST_ROOTS',
        'CCB_TEST_ENTRYPOINT',
        'CCB_SKIP_STARTUP_UPDATE_CHECK',
        'CCB_SOURCE_HOME',
    ):
        value = os.environ.get(key)
        if value:
            extra[key] = value
    env = control_plane_env(extra=extra)
    lib_root = str(_script_root() / 'lib')
    current_pythonpath = env.get('PYTHONPATH')
    env['PYTHONPATH'] = lib_root if not current_pythonpath else lib_root + os.pathsep + current_pythonpath
    path = env.get('PATH', '')
    script_root = str(_script_root())
    env['PATH'] = script_root + (os.pathsep + path if path else '')
    return env


def _spawn_maintenance_runner(context: CliContext, *, runner_id: str, source: str) -> subprocess.Popen:
    script = _script_root() / 'ccb.py'
    context.paths.ccbd_maintenance_heartbeat_dir.mkdir(parents=True, exist_ok=True)
    stdout_log = open(context.paths.ccbd_maintenance_heartbeat_dir / 'runner.stdout.log', 'ab')
    stderr_log = open(context.paths.ccbd_maintenance_heartbeat_dir / 'runner.stderr.log', 'ab')
    try:
        return subprocess.Popen(
            [
                sys.executable,
                str(script),
                '--project',
                str(context.project.project_root),
                'maintenance',
                'runner',
                '--runner-id',
                runner_id,
                '--source',
                source,
            ],
            cwd=str(context.project.project_root),
            env=_runner_env(),
            stdin=subprocess.DEVNULL,
            stdout=stdout_log,
            stderr=stderr_log,
            start_new_session=True,
        )
    finally:
        stdout_log.close()
        stderr_log.close()


def ensure_maintenance_heartbeat_runner(context: CliContext, *, source: str = 'startup_ensure') -> dict:
    loaded = load_project_config(context.project.project_root)
    heartbeat = loaded.config.maintenance_heartbeat
    store = MaintenanceHeartbeatStore(context.paths, project_id=context.project.project_id)
    if not heartbeat.enabled or not heartbeat.startup_ensure:
        return {
            **_maintenance_status(context),
            'maintenance_status': 'ok',
            'action': 'runner-ensure',
            'runner_status': 'disabled',
            'runner_started': False,
        }
    if heartbeat.assessor not in loaded.config.agents:
        return {
            **_maintenance_status(context),
            'maintenance_status': 'degraded',
            'action': 'runner-ensure',
            'runner_status': 'skipped',
            'runner_started': False,
            'reason': f'configured heartbeat assessor is not present: {heartbeat.assessor}',
        }
    existing = store.load_runner()
    if _runner_read_result_is_live(existing):
        return {
            **_maintenance_status(context),
            'maintenance_status': 'ok',
            'action': 'runner-ensure',
            'runner_status': 'already_running',
            'runner_started': False,
            'runner_pid': existing.value.pid if existing.value is not None else None,
            'runner_id': existing.value.runner_id if existing.value is not None else None,
        }
    observed_at = utc_now()
    runner_id = _runner_id()
    process = _spawn_maintenance_runner(context, runner_id=runner_id, source=source)
    store.save_runner(
        MaintenanceHeartbeatRunner(
            project_id=context.project.project_id,
            runner_id=runner_id,
            pid=int(process.pid),
            state='starting',
            source=source,
            started_at=observed_at,
            last_seen_at=observed_at,
        )
    )
    return {
        **_maintenance_status(context),
        'maintenance_status': 'ok',
        'action': 'runner-ensure',
        'runner_status': 'started',
        'runner_started': True,
        'runner_pid': int(process.pid),
        'runner_id': runner_id,
    }


def stop_maintenance_heartbeat_runner(context: CliContext, *, reason: str = 'shutdown') -> dict:
    store = MaintenanceHeartbeatStore(context.paths, project_id=context.project.project_id)
    result = store.load_runner()
    if result.value is None:
        return {'runner_stop_status': result.state, 'runner_stopped': False, 'reason': result.error}
    runner = result.value
    if runner.pid is None or not _pid_alive(int(runner.pid)):
        store.save_runner(
            _runner_update(
                runner,
                state='stopped',
                last_seen_at=utc_now(),
                sleep_until=None,
                exit_reason='stale_pid',
            )
        )
        return {'runner_stop_status': 'stale', 'runner_stopped': False, 'runner_pid': runner.pid}
    if int(runner.pid) == os.getpid():
        return {'runner_stop_status': 'self', 'runner_stopped': False, 'runner_pid': runner.pid}
    store.save_runner(
        _runner_update(
            runner,
            state='stopping',
            last_seen_at=utc_now(),
            sleep_until=None,
            exit_reason=reason,
        )
    )
    try:
        os.kill(int(runner.pid), signal.SIGTERM)
    except ProcessLookupError:
        return {'runner_stop_status': 'stale', 'runner_stopped': False, 'runner_pid': runner.pid}
    deadline = time.time() + _RUNNER_STOP_WAIT_S
    while time.time() < deadline:
        if not _pid_alive(int(runner.pid)):
            store.save_runner(
                _runner_update(
                    runner,
                    state='stopped',
                    last_seen_at=utc_now(),
                    sleep_until=None,
                    exit_reason=reason,
                )
            )
            return {'runner_stop_status': 'stopped', 'runner_stopped': True, 'runner_pid': runner.pid}
        time.sleep(0.05)
    return {'runner_stop_status': 'signalled', 'runner_stopped': False, 'runner_pid': runner.pid}


def startup_ensure_maintenance_heartbeat(context: CliContext) -> dict | None:
    try:
        loaded = load_project_config(context.project.project_root)
        heartbeat = loaded.config.maintenance_heartbeat
        if not heartbeat.enabled or not heartbeat.startup_ensure:
            return None
        if heartbeat.assessor not in loaded.config.agents:
            return {
                'maintenance_status': 'degraded',
                'action': 'startup_ensure',
                'runner_status': 'skipped',
                'reason': f'configured heartbeat assessor is not present: {heartbeat.assessor}',
            }
        try:
            return ensure_maintenance_heartbeat_runner(context, source='startup_ensure')
        except Exception as runner_exc:
            fallback = maintenance_status(
                context,
                ParsedMaintenanceCommand(project=getattr(context.command, 'project', None), action='tick'),
            )
            return {
                **fallback,
                'maintenance_status': 'degraded',
                'action': 'startup_ensure',
                'runner_status': 'failed',
                'reason': str(runner_exc),
            }
    except Exception as exc:
        return {
            'maintenance_status': 'degraded',
            'action': 'startup_ensure',
            'runner_status': 'failed',
            'reason': str(exc),
        }


__all__ = [
    'ensure_maintenance_heartbeat_runner',
    'maintenance_status',
    'startup_ensure_maintenance_heartbeat',
    'stop_maintenance_heartbeat_runner',
]
