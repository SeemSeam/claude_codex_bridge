from __future__ import annotations

from completion.models import CompletionDecision

from provider_execution.base import ProviderSubmission
from provider_execution.capabilities import execution_restore_capability
from provider_execution.state_models import PersistedExecutionState


def acknowledge(service, job_id: str) -> None:
    service._pending_replays.pop(job_id, None)
    if service._state_store is None:
        return
    persisted = service._state_store.load(job_id)
    if persisted is None:
        return
    service._state_store.save(
        PersistedExecutionState(
            submission=persisted.submission,
            runtime_context=persisted.runtime_context,
            resume_capable=persisted.resume_capable,
            persisted_at=service._clock(),
            pending_decision=persisted.pending_decision,
            pending_items=(),
            applied_event_seqs=(),
        )
    )


def acknowledge_item(service, job_id: str, *, event_seq: int | None) -> None:
    if event_seq is None or service._state_store is None:
        return
    persisted = service._state_store.load(job_id)
    if persisted is None or not persisted.pending_items:
        return
    applied_event_seqs = tuple(sorted({*persisted.applied_event_seqs, int(event_seq)}))
    service._state_store.save(
        PersistedExecutionState(
            submission=persisted.submission,
            runtime_context=persisted.runtime_context,
            resume_capable=persisted.resume_capable,
            persisted_at=service._clock(),
            pending_decision=persisted.pending_decision,
            pending_items=persisted.pending_items,
            applied_event_seqs=applied_event_seqs,
        )
    )


def persist_submission(
    service,
    job_id: str,
    *,
    pending_decision: CompletionDecision | None = None,
    pending_items: tuple = (),
    applied_event_seqs: tuple[int, ...] = (),
) -> None:
    if service._state_store is None:
        return
    submission = service._active.get(job_id)
    if submission is None:
        return
    adapter = service._registry.get(submission.provider)
    capability = execution_restore_capability(adapter, provider=submission.provider)
    runtime_state: dict[str, object] = {}
    resume_capable = False
    if adapter is not None:
        exporter = getattr(adapter, "export_runtime_state", None)
        if callable(exporter):
            exported = exporter(submission)
            if exported is not None:
                runtime_state = dict(exported)
                resume_capable = callable(getattr(adapter, "resume", None))
    runtime_state = with_reliability_state(runtime_state, submission.runtime_state)
    if submission.runtime_state.get('draft_guard_enabled'):
        runtime_state.update({key: value for key, value in submission.runtime_state.items()
                              if key.startswith('draft_guard_') or key == 'prompt_sent'})
        if submission.provider == 'codex' and submission.runtime_state.get('prompt_sent') is False:
            runtime_state['pending_prompt'] = submission.runtime_state.get('pending_prompt', '')
    persisted = PersistedExecutionState(
        submission=ProviderSubmission(
            job_id=submission.job_id,
            agent_name=submission.agent_name,
            provider=submission.provider,
            accepted_at=submission.accepted_at,
            ready_at=submission.ready_at,
            source_kind=submission.source_kind,
            reply=submission.reply,
            status=submission.status,
            reason=submission.reason,
            confidence=submission.confidence,
            diagnostics={**dict(submission.diagnostics or {}), **capability},
            runtime_state=runtime_state,
        ),
        runtime_context=service._runtime_contexts.get(job_id),
        resume_capable=resume_capable,
        persisted_at=service._clock(),
        pending_decision=pending_decision,
        pending_items=tuple(pending_items),
        applied_event_seqs=tuple(sorted({int(value) for value in applied_event_seqs})),
    )
    service._state_store.save(persisted)


def with_reliability_state(runtime_state: dict[str, object], source_state: dict[str, object]) -> dict[str, object]:
    reliability_state = {
        key: value
        for key, value in dict(source_state).items()
        if str(key).startswith('reliability_')
    }
    if not reliability_state:
        return runtime_state
    return {**runtime_state, **reliability_state}


def filter_pending_items(persisted: PersistedExecutionState) -> tuple:
    if not persisted.pending_items or not persisted.applied_event_seqs:
        return tuple(persisted.pending_items)
    applied = set(persisted.applied_event_seqs)
    return tuple(item for item in persisted.pending_items if (item.cursor.event_seq or -1) not in applied)


__all__ = [
    "acknowledge",
    "acknowledge_item",
    "filter_pending_items",
    "persist_submission",
    "with_reliability_state",
]
