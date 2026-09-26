from __future__ import annotations

from uuid import uuid4

from agents.models import AgentState, AgentValidationError
from completion.tracker import CompletionTrackerView
from execution_phase import derive_execution_phase, execution_phase_evidence_from_records
from message_bureau.reply_payloads import reply_id_from_payload

from .completion import apply_tracker_view, merge_terminal_decision
from .lifecycle import resubmit_message, retry_attempt
from .records import append_event, append_job, rebuild_dispatcher_state
from .routing import build_watch_payload, resolve_targets, resolve_watch_target, validate_sender, validate_targets_available
from .runtime_state import sync_runtime
from .active_followups import followups_for_trace, trace_active_followups


class DispatcherFacadeMixin:
    def watch(self, target: str, *, start_line: int = 0) -> dict:
        return build_watch_payload(self, target, start_line=start_line)

    def queue(self, target: str = 'all', *, detail: bool | None = None) -> dict:
        payload = self._message_bureau_control.queue_summary(target, detail=detail)
        if payload.get('target') == 'all':
            payload = dict(payload)
            payload['agents'] = [self._queue_agent_with_runtime(agent) for agent in payload.get('agents') or ()]
            return payload
        payload = dict(payload)
        agent = payload.get('agent')
        if isinstance(agent, dict):
            payload['agent'] = self._queue_agent_with_runtime(agent)
        return payload

    def trace(self, target: str) -> dict:
        direct = trace_active_followups(self, target)
        if direct is not None:
            return direct
        result = dict(self._message_bureau_control.trace(target))
        followups = followups_for_trace(self, result)
        if followups:
            result['active_followups'] = followups
        return result

    def resubmit(self, message_id: str) -> dict:
        return resubmit_message(self, message_id)

    def retry(self, target: str) -> dict:
        return retry_attempt(self, target)

    def inbox(self, agent_name: str, *, detail: bool | None = None) -> dict:
        return self._message_bureau_control.inbox(agent_name, detail=detail)

    def mailbox_head(self, agent_name: str) -> dict:
        return self._message_bureau_control.mailbox_head(agent_name)

    def ack_reply(self, agent_name: str, inbound_event_id: str | None = None) -> dict:
        return self._message_bureau_control.ack_reply(agent_name, inbound_event_id)

    def _resolve_targets(self, request) -> tuple[str, ...]:
        return resolve_targets(self, request)

    def _validate_targets_available(self, targets) -> None:
        validate_targets_available(self, targets)

    def _validate_sender(self, sender: str) -> None:
        validate_sender(self, sender)

    def _resolve_watch_target(self, target: str) -> tuple[str, str]:
        return resolve_watch_target(self, target)

    def _profile_family(self, agent_name: str):
        spec = self._registry.spec_for(agent_name)
        manifest = self._provider_catalog.resolve_completion_manifest(spec.provider, spec.runtime_mode)
        return manifest.completion_family

    def _profile_family_for_job(self, job):
        return self._profile_family(job.agent_name)

    def _has_outstanding_work(self, agent_name: str) -> bool:
        return self._state.has_outstanding(agent_name)

    def _sync_runtime(self, agent_name: str, *, state: AgentState | None = None) -> None:
        sync_runtime(self, agent_name, state=state)

    def reconcile_runtime_views(self) -> None:
        for agent_name in self._config.agents:
            self._sync_runtime(agent_name)

    def _append_job(self, record) -> None:
        append_job(self, record)

    def _append_event(self, record, event_type: str, payload: dict[str, object], *, timestamp: str) -> None:
        append_event(self, record, event_type, payload, timestamp=timestamp)

    def _new_id(self, prefix: str) -> str:
        return f'{prefix}_{uuid4().hex[:12]}'

    def _rebuild_state(self) -> None:
        rebuild_dispatcher_state(self)

    def _apply_tracker_view(
        self,
        current,
        tracked: CompletionTrackerView,
        *,
        updated_at: str | None = None,
    ) -> bool:
        timestamp = apply_tracker_view(
            current,
            tracked,
            snapshot_writer=self._snapshot_writer,
            profile_family=self._profile_family_for_job(current),
            clock=self._clock,
            updated_at=updated_at,
        )
        if timestamp is None:
            return False
        self._append_event(current, 'completion_state_updated', tracked.state.to_record(), timestamp=timestamp)
        return True

    def _merge_terminal_decision(self, job_id: str, decision, *, prior_snapshot):
        return merge_terminal_decision(
            job_id,
            decision,
            completion_tracker=self._completion_tracker,
            prior_snapshot=prior_snapshot,
        )

    def _queue_agent_with_runtime(self, payload: dict) -> dict:
        agent = dict(payload)
        try:
            runtime = self._registry.get(agent.get('agent_name', ''))
        except (AgentValidationError, KeyError):
            runtime = None
        agent['runtime_state'] = runtime.state.value if runtime is not None else 'stopped'
        agent['runtime_health'] = runtime.health if runtime is not None else 'stopped'
        agent.update(self._queue_execution_phase(agent))
        # Pre-claim waits have no ProviderSubmission, so active snapshots alone
        # cannot explain a healthy idle agent with a blocked mailbox.
        queued = self._state.queued_items_for('agent', agent.get('agent_name', ''))
        snapshot = getattr(self._execution_service, 'draft_wait_snapshot', None)
        if queued and callable(snapshot):
            wait = snapshot(agent.get('agent_name', ''), queued[0])
            if wait:
                agent['delivery_wait'] = wait
        return agent

    def _queue_execution_phase(self, agent: dict) -> dict[str, object]:
        control = self._message_bureau_control
        agent_name = str(agent.get('agent_name') or '').strip()
        mailbox = _safe_store_call(getattr(control, '_mailbox_store', None), 'load', agent_name)
        event_id = str(
            getattr(mailbox, 'active_inbound_event_id', None)
            or getattr(mailbox, 'head_inbound_event_id', None)
            or ''
        ).strip()
        inbound = _safe_store_call(
            getattr(control, '_inbound_store', None),
            'get_latest',
            agent_name,
            event_id,
        )
        attempt = _safe_store_call(
            getattr(control, '_attempt_store', None),
            'get_latest',
            getattr(inbound, 'attempt_id', None),
        )
        job_id = str(getattr(attempt, 'job_id', '') or '').strip()
        job = self.get(job_id) if job_id else None
        if job is None:
            return {}
        if _enum_value(getattr(inbound, 'event_type', None)) == 'task_reply':
            reply_id = reply_id_from_payload(getattr(inbound, 'payload_ref', None))
            reply = _safe_store_call(
                getattr(control, '_reply_store', None),
                'get_latest',
                reply_id,
            )
            source_attempt = _safe_store_call(
                getattr(control, '_attempt_store', None),
                'get_latest',
                getattr(reply, 'attempt_id', None),
            )
            source_job_id = str(getattr(source_attempt, 'job_id', '') or '').strip()
            source_job = self.get(source_job_id) if source_job_id else None
            if source_job is None:
                return {}
            active_job_id = self._state.active_job(agent_name)
            active_job = self.get(active_job_id) if active_job_id else None
            is_reply_delivery = (
                getattr(getattr(active_job, 'request', None), 'message_type', None)
                == 'reply_delivery'
            )
            delivery_job = active_job if is_reply_delivery else None
            evidence = execution_phase_evidence_from_records(
                job=source_job,
                reply_expected=True,
                reply_delivery=delivery_job,
                reply_delivery_source_job_id=source_job_id if delivery_job is not None else None,
            )
            return derive_execution_phase(evidence).to_record()
        lease = _safe_store_call(getattr(control, '_lease_store', None), 'load', agent_name)
        completion = _safe_store_call(self._snapshot_writer, 'load', job_id)
        evidence = execution_phase_evidence_from_records(
            job=job,
            attempt=attempt,
            inbound=inbound,
            mailbox=mailbox,
            lease=lease,
            completion=completion,
            provider_state=None,
            provider_identity_current=False,
            reply_expected=_queue_reply_expected(self, job),
        )
        return derive_execution_phase(evidence).to_record()


def _safe_store_call(store, method_name: str, *args):
    method = getattr(store, method_name, None)
    if not callable(method) or any(arg is None or str(arg).strip() == '' for arg in args):
        return None
    try:
        return method(*args)
    except Exception:
        return None


def _enum_value(value) -> str:
    return str(getattr(value, 'value', value) or '').strip().lower()


def _queue_reply_expected(dispatcher, job) -> bool:
    sender = str(getattr(getattr(job, 'request', None), 'from_actor', '') or '').strip()
    target = str(getattr(job, 'target_name', '') or '').strip()
    configured = getattr(getattr(dispatcher, '_config', None), 'agents', {})
    return sender in configured and sender != target


__all__ = ['DispatcherFacadeMixin']
