from __future__ import annotations

from pathlib import Path

from agents.models import (
    AgentRuntime,
    AgentSpec,
    AgentState,
    PermissionMode,
    ProjectConfig,
    QueuePolicy,
    RestoreMode,
    RuntimeMode,
    WorkspaceMode,
)
from ccbd.api_models import DeliveryScope, JobStatus, MessageEnvelope
from ccbd.services.dispatcher import JobDispatcher
from ccbd.services.dispatcher_runtime.polling_service import _validate_provider_completion_decision
from ccbd.services.dispatcher_runtime.reply_delivery import prepare_reply_deliveries
from ccbd.services.registry import AgentRegistry
from completion.models import (
    CompletionConfidence,
    CompletionDecision,
    CompletionSourceKind,
    CompletionStatus,
)
from project.ids import compute_project_id
from project.resolver import ProjectContext
from provider_execution.base import ProviderSubmission
from provider_execution.service_runtime.models import ExecutionUpdate
from storage.paths import PathLayout


def _bootstrap_test_project(project_root: Path) -> ProjectContext:
    project_root.mkdir()
    config_dir = project_root / '.ccb'
    config_dir.mkdir(exist_ok=True)
    (config_dir / 'ccb.config').write_text('cmd; demo:fake\n', encoding='utf-8')
    return ProjectContext(
        cwd=project_root,
        project_root=project_root,
        config_dir=config_dir,
        project_id=compute_project_id(project_root),
        source='test',
    )


def _provider_config(*providers: str) -> ProjectConfig:
    agents: dict[str, AgentSpec] = {}
    for provider in providers:
        agents[provider] = AgentSpec(
            name=provider,
            provider=provider,
            target='.',
            workspace_mode=WorkspaceMode.GIT_WORKTREE,
            workspace_root=None,
            runtime_mode=RuntimeMode.PANE_BACKED,
            restore_default=RestoreMode.AUTO,
            permission_default=PermissionMode.MANUAL,
            queue_policy=QueuePolicy.SERIAL_PER_AGENT,
        )
    return ProjectConfig(version=2, default_agents=tuple(providers), agents=agents, cmd_enabled=True)


def _runtime(agent_name: str, *, project_id: str, layout: PathLayout, pid: int) -> AgentRuntime:
    return AgentRuntime(
        agent_name=agent_name,
        state=AgentState.IDLE,
        pid=pid,
        started_at='2026-03-30T00:00:00Z',
        last_seen_at='2026-03-30T00:00:00Z',
        runtime_ref=f'{agent_name}-runtime',
        session_ref=f'{agent_name}-session',
        workspace_path=str(layout.workspace_path(agent_name)),
        project_id=project_id,
        backend_type='tmux',
        queue_depth=0,
        socket_path=None,
        health='healthy',
    )


def _ask(to_agent: str, from_actor: str, body: str, *, project_id: str, task_id: str) -> MessageEnvelope:
    return MessageEnvelope(
        project_id=project_id,
        to_agent=to_agent,
        from_actor=from_actor,
        body=body,
        task_id=task_id,
        reply_to=None,
        message_type='ask',
        delivery_scope=DeliveryScope.SINGLE,
    )


def _turn_end_decision(*, reply: str = 'done', turn_ref: str = 'turn-1') -> CompletionDecision:
    return CompletionDecision(
        terminal=True,
        status=CompletionStatus.COMPLETED,
        reason='task_complete',
        confidence=CompletionConfidence.EXACT,
        reply=reply,
        anchor_seen=True,
        reply_started=True,
        reply_stable=True,
        provider_turn_ref=turn_ref,
        source_cursor=None,
        finished_at='2026-03-30T00:00:10Z',
        diagnostics={},
    )


def _empty_turn_end_decision(*, turn_ref: str = 'turn-empty') -> CompletionDecision:
    return CompletionDecision(
        terminal=True,
        status=CompletionStatus.INCOMPLETE,
        reason='task_complete_empty_reply',
        confidence=CompletionConfidence.EXACT,
        reply='',
        anchor_seen=True,
        reply_started=False,
        reply_stable=True,
        provider_turn_ref=turn_ref,
        source_cursor=None,
        finished_at='2026-03-30T00:00:10Z',
        diagnostics={'empty_reply': True, 'error_type': 'empty_provider_reply'},
    )


class HoldingExecutionService:
    """Execution double that holds every submission until a turn end is armed.

    Models the unified-FIFO contract: transport acceptance never releases the
    target slot; only the armed provider turn-end decision completes a job.
    Delivery completion rides on job identity plus provider turn-end evidence;
    the legacy runtime flag is optional (see ``delivery_runtime_flag``).
    """

    def __init__(self, *, provider: str = 'codex', delivery_runtime_flag: bool = True) -> None:
        self.provider = provider
        # The legacy runtime flag is optional evidence: deliveries hold and
        # complete through job identity and provider turn-end decisions.
        self.delivery_runtime_flag = delivery_runtime_flag
        self.started: list[str] = []
        self.finished: list[str] = []
        self._active: dict[str, ProviderSubmission] = {}
        self._armed: dict[str, CompletionDecision] = {}


    def arm_turn_end(self, job_id: str, decision: CompletionDecision) -> None:
        self._armed[job_id] = decision

    def start(self, job, *, runtime_context=None):
        del runtime_context
        self.started.append(job.job_id)
        is_delivery = str(job.request.message_type or '') == 'reply_delivery'
        runtime_state = {
            'mode': 'active',
            'request_anchor': job.job_id,
            'pane_id': '%1',
            'prompt_sent': True,
        }
        if self.provider == 'codex':
            runtime_state.update(
                {
                    'delivery_state': 'accepted' if is_delivery else 'accepted',
                    'anchor_seen': True,
                }
            )
        if is_delivery and self.delivery_runtime_flag:
            runtime_state['reply_delivery_complete_on_dispatch'] = True
        submission = ProviderSubmission(
            job_id=job.job_id,
            agent_name=job.agent_name,
            provider=self.provider,
            accepted_at='2026-03-30T00:00:00Z',
            ready_at='2026-03-30T00:00:00Z',
            source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
            reply='',
            diagnostics={'provider': self.provider, 'mode': 'active'},
            runtime_state=runtime_state,
        )
        self._active[job.job_id] = submission
        return submission

    def cancel(self, job_id: str) -> None:
        self._active.pop(job_id, None)
        self._armed.pop(job_id, None)

    def finish(self, job_id: str) -> None:
        self.finished.append(job_id)
        self._active.pop(job_id, None)
        self._armed.pop(job_id, None)

    def poll(self):
        updates = []
        for job_id, decision in list(self._armed.items()):
            submission = self._active.get(job_id)
            if submission is None:
                continue
            updates.append(
                ExecutionUpdate(job_id=job_id, items=(), decision=decision, submission=submission)
            )
        return tuple(updates)


def _build_dispatcher(project_root: Path, *, execution, clock) -> tuple[ProjectContext, JobDispatcher]:
    ctx = _bootstrap_test_project(project_root)
    layout = PathLayout(project_root)
    config = _provider_config('codex', 'claude', 'gemini')
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('codex', project_id=ctx.project_id, layout=layout, pid=101))
    registry.upsert(_runtime('claude', project_id=ctx.project_id, layout=layout, pid=102))
    registry.upsert(_runtime('gemini', project_id=ctx.project_id, layout=layout, pid=103))
    dispatcher = JobDispatcher(
        layout,
        config,
        registry,
        execution_service=execution,
        auto_reply_delivery_on_complete=True,
        clock=clock,
    )
    return ctx, dispatcher


def _active_delivery_job(dispatcher, agent_name):
    job_id = dispatcher._state.active_job_for('agent', agent_name) if False else None
    # TargetKind.AGENT slots use TargetKind enum; use the public active view.
    for _kind, name, active_job_id in dispatcher._state.active_items():
        if name == agent_name:
            job_id = active_job_id
    return dispatcher.get(job_id) if job_id else None


def test_request_admitted_first_runs_before_later_reply_delivery(tmp_path: Path) -> None:
    """No message-class priority: a queued ask outranks a reply admitted later."""
    execution = HoldingExecutionService()
    ctx, dispatcher = _build_dispatcher(tmp_path / 'repo-fifo-request-first', execution=execution, clock=lambda: '2026-03-30T00:00:00Z')

    # codex commissions work from gemini before anything else runs.
    reply_receipt = dispatcher.submit(_ask('gemini', 'codex', 'produce reply', project_id=ctx.project_id, task_id='task-r'))
    reply_job = reply_receipt.jobs[0].job_id
    # X request admitted to codex while gemini is still working.
    x_receipt = dispatcher.submit(_ask('codex', 'claude', 'task X', project_id=ctx.project_id, task_id='task-x'))
    x_job = x_receipt.jobs[0].job_id

    dispatcher.tick()
    assert execution.started == [x_job, reply_job]
    assert _active_delivery_job(dispatcher, 'codex').job_id == x_job

    # gemini's result is admitted after X's request event.
    dispatcher.complete(reply_job, _turn_end_decision(reply='result body', turn_ref='turn-r'))
    for _ in range(3):
        dispatcher.tick()

    # X's turn still holds codex's slot: neither the reply nor new work starts.
    assert execution.started == [x_job, reply_job]

    execution.arm_turn_end(x_job, _turn_end_decision(turn_ref='turn-x'))
    dispatcher.poll_completions()
    dispatcher.tick()

    # X ended; the reply delivery (admitted after X) starts next.
    delivery_job = _active_delivery_job(dispatcher, 'codex')
    assert delivery_job is not None
    assert delivery_job.job_id not in {x_job, reply_job}
    assert delivery_job.request.message_type == 'reply_delivery'


def test_held_reply_delivery_orders_request_then_reply_strictly(tmp_path: Path) -> None:
    """A is a processing result; then request X and result B: A -> X -> B."""
    execution = HoldingExecutionService()
    ctx, dispatcher = _build_dispatcher(tmp_path / 'repo-fifo-a-x-b', execution=execution, clock=lambda: '2026-03-30T00:00:00Z')

    # Produce result A owed to codex.
    a_source = dispatcher.submit(_ask('claude', 'codex', 'produce A', project_id=ctx.project_id, task_id='task-a'))
    a_source_job = a_source.jobs[0].job_id
    dispatcher.tick()
    dispatcher.complete(a_source_job, _turn_end_decision(reply='result A', turn_ref='turn-a'))

    # A becomes codex's head reply and gets a held delivery job.
    dispatcher.tick()
    delivery_a = _active_delivery_job(dispatcher, 'codex')
    assert delivery_a is not None
    assert delivery_a.request.message_type == 'reply_delivery'
    a_delivery_id = delivery_a.job_id

    # X request admitted, then result B queued, both while A's turn runs.
    x_receipt = dispatcher.submit(_ask('codex', 'claude', 'task X', project_id=ctx.project_id, task_id='task-x'))
    x_job = x_receipt.jobs[0].job_id
    b_source = dispatcher.submit(_ask('claude', 'codex', 'produce B', project_id=ctx.project_id, task_id='task-b'))
    b_source_job = b_source.jobs[0].job_id
    dispatcher.tick()
    dispatcher.complete(b_source_job, _turn_end_decision(reply='result B', turn_ref='turn-b'))
    for _ in range(3):
        dispatcher.tick()

    # Only claude's own B-producing turn may start; codex still processes A.
    assert execution.started == [a_source_job, a_delivery_id, b_source_job]

    # A's turn ends with empty output: still a completed delivery.
    execution.arm_turn_end(a_delivery_id, _empty_turn_end_decision(turn_ref='turn-a-delivery'))
    completed = dispatcher.poll_completions()
    assert [job.job_id for job in completed] == [a_delivery_id]
    assert completed[0].status is JobStatus.COMPLETED

    dispatcher.tick()
    assert execution.started[-1] == x_job

    execution.arm_turn_end(x_job, _turn_end_decision(turn_ref='turn-x'))
    dispatcher.poll_completions()
    dispatcher.tick()
    delivery_b = _active_delivery_job(dispatcher, 'codex')
    assert delivery_b is not None
    assert delivery_b.request.message_type == 'reply_delivery'

    # X's completion also owes claude a result: its delivery starts on claude.
    assert execution.started[:5] == [a_source_job, a_delivery_id, b_source_job, x_job, delivery_b.job_id]
    assert len(execution.started) == 6
    assert execution.started[-1] not in {a_source_job, a_delivery_id, b_source_job, x_job, delivery_b.job_id}


def test_two_scheduler_passes_claim_one_job_per_target(tmp_path: Path) -> None:
    execution = HoldingExecutionService()
    ctx, dispatcher = _build_dispatcher(tmp_path / 'repo-fifo-single-claim', execution=execution, clock=lambda: '2026-03-30T00:00:00Z')

    dispatcher.submit(_ask('codex', 'claude', 'first', project_id=ctx.project_id, task_id='task-1'))
    dispatcher.submit(_ask('codex', 'claude', 'second', project_id=ctx.project_id, task_id='task-2'))
    dispatcher.tick()
    dispatcher.tick()
    dispatcher.tick()

    assert len(execution.started) == 1
    assert len(dispatcher._state.queued_items_for('agent', 'codex')) == 1


def test_same_timestamp_submissions_keep_submission_order(tmp_path: Path) -> None:
    execution = HoldingExecutionService()
    ctx, dispatcher = _build_dispatcher(tmp_path / 'repo-fifo-same-clock', execution=execution, clock=lambda: '2026-03-30T00:00:00Z')

    first = dispatcher.submit(_ask('codex', 'claude', 'first', project_id=ctx.project_id, task_id='task-1'))
    second = dispatcher.submit(_ask('codex', 'claude', 'second', project_id=ctx.project_id, task_id='task-2'))
    dispatcher.tick()

    assert execution.started == [first.jobs[0].job_id]
    queued = dispatcher._state.queued_items_for('agent', 'codex')
    assert queued == (second.jobs[0].job_id,)


def test_restart_rebuilds_queue_from_mailbox_admission_order(tmp_path: Path) -> None:
    """Delayed delivery-job materialization must not reorder after restart."""
    execution = HoldingExecutionService()
    ctx, dispatcher = _build_dispatcher(tmp_path / 'repo-fifo-restart-order', execution=execution, clock=lambda: '2026-03-30T00:00:00Z')

    # Reply A admitted to codex's mailbox first...
    a_source = dispatcher.submit(_ask('claude', 'codex', 'produce A', project_id=ctx.project_id, task_id='task-a'))
    a_source_job = a_source.jobs[0].job_id
    dispatcher.tick()
    dispatcher.complete(a_source_job, _turn_end_decision(reply='result A', turn_ref='turn-a'))
    # ...request X admitted second...
    x_receipt = dispatcher.submit(_ask('codex', 'claude', 'task X', project_id=ctx.project_id, task_id='task-x'))
    x_job = x_receipt.jobs[0].job_id
    # ...delivery job for A materialized last (after X's job exists).
    prepare_reply_deliveries(dispatcher)

    # JobStore order is X before A's delivery job; mailbox order is A before X.
    layout = PathLayout(tmp_path / 'repo-fifo-restart-order')
    config = _provider_config('codex', 'claude')
    registry = AgentRegistry(layout, config)
    restarted = JobDispatcher(
        layout,
        config,
        registry,
        execution_service=HoldingExecutionService(),
        auto_reply_delivery_on_complete=True,
        clock=lambda: '2026-03-30T00:01:00Z',
    )
    queued = restarted._state.queued_items_for('agent', 'codex')
    assert queued[0] != x_job
    assert queued[-1] == x_job
    head_job = restarted.get(queued[0])
    assert head_job is not None
    assert head_job.request.message_type == 'reply_delivery'


def test_empty_turn_end_delivery_consumes_head_without_requeue(tmp_path: Path) -> None:
    """An anchored turn that ends without text is a completed delivery."""
    execution = HoldingExecutionService()
    ctx, dispatcher = _build_dispatcher(tmp_path / 'repo-fifo-empty-turn-end', execution=execution, clock=lambda: '2026-03-30T00:00:00Z')

    a_source = dispatcher.submit(_ask('claude', 'codex', 'produce A', project_id=ctx.project_id, task_id='task-a'))
    a_source_job = a_source.jobs[0].job_id
    dispatcher.tick()
    dispatcher.complete(a_source_job, _turn_end_decision(reply='result A', turn_ref='turn-a'))
    dispatcher.tick()

    delivery = _active_delivery_job(dispatcher, 'codex')
    assert delivery is not None
    execution.arm_turn_end(delivery.job_id, _empty_turn_end_decision(turn_ref='turn-a-delivery'))
    completed = dispatcher.poll_completions()

    assert [job.job_id for job in completed] == [delivery.job_id]
    assert completed[0].status is JobStatus.COMPLETED

    from mailbox_kernel import InboundEventStore

    latest_by_event: dict[str, object] = {}
    for record in InboundEventStore(PathLayout(tmp_path / 'repo-fifo-empty-turn-end')).list_agent('codex'):
        if record.event_type.value == 'task_reply':
            latest_by_event[record.inbound_event_id] = record
    assert latest_by_event
    assert all(event.status.value == 'consumed' for event in latest_by_event.values())


def test_empty_turn_end_delivery_without_legacy_flag_completes_from_job_marker(tmp_path: Path) -> None:
    """Job identity, not the legacy runtime flag, marks the delivery turn."""
    execution = HoldingExecutionService(delivery_runtime_flag=False)
    ctx, dispatcher = _build_dispatcher(
        tmp_path / 'repo-fifo-empty-turn-end-no-flag',
        execution=execution,
        clock=lambda: '2026-03-30T00:00:00Z',
    )

    a_source = dispatcher.submit(_ask('claude', 'codex', 'produce A', project_id=ctx.project_id, task_id='task-a'))
    a_source_job = a_source.jobs[0].job_id
    dispatcher.tick()
    dispatcher.complete(a_source_job, _turn_end_decision(reply='result A', turn_ref='turn-a'))
    dispatcher.tick()

    delivery = _active_delivery_job(dispatcher, 'codex')
    assert delivery is not None
    assert 'reply_delivery_complete_on_dispatch' not in execution._active[delivery.job_id].runtime_state
    source_inbox_before = dispatcher.inbox('claude')['item_count']

    execution.arm_turn_end(delivery.job_id, _empty_turn_end_decision(turn_ref='turn-a-delivery'))
    completed = dispatcher.poll_completions()

    assert [job.job_id for job in completed] == [delivery.job_id]
    assert completed[0].status is JobStatus.COMPLETED
    assert completed[0].terminal_decision['reason'] == 'reply_delivery_turn_complete'
    # An empty back turn owes no reply and must not notify anyone recursively.
    assert dispatcher.inbox('claude')['item_count'] == source_inbox_before

    from mailbox_kernel import InboundEventStore

    latest_by_event: dict[str, object] = {}
    for record in InboundEventStore(PathLayout(tmp_path / 'repo-fifo-empty-turn-end-no-flag')).list_agent('codex'):
        if record.event_type.value == 'task_reply':
            latest_by_event[record.inbound_event_id] = record
    assert latest_by_event
    assert all(event.status.value == 'consumed' for event in latest_by_event.values())


class NoDeliveryFlagExecutionService:
    """Provider execution with ordinary acceptance evidence and no delivery flag."""

    def __init__(self) -> None:
        self.started: list[str] = []
        self.finished: list[str] = []
        self._active: dict[str, ProviderSubmission] = {}

    def start(self, job, *, runtime_context=None):
        del runtime_context
        self.started.append(job.job_id)
        submission = ProviderSubmission(
            job_id=job.job_id,
            agent_name=job.agent_name,
            provider=job.provider,
            accepted_at='2026-03-30T00:00:00Z',
            ready_at='2026-03-30T00:00:00Z',
            source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
            reply='',
            diagnostics={'provider': job.provider, 'mode': 'active'},
            runtime_state={
                'mode': 'active',
                'request_anchor': job.job_id,
                'delivery_state': 'accepted',
                'anchor_seen': True,
            },
        )
        self._active[job.job_id] = submission
        return submission

    def cancel(self, job_id: str) -> None:
        self._active.pop(job_id, None)

    def finish(self, job_id: str) -> None:
        self.finished.append(job_id)
        self._active.pop(job_id, None)

    def poll(self):
        return ()
def test_cancelled_delivery_is_not_resurrected_and_queue_advances(tmp_path: Path) -> None:
    """A user cancel of an accepted delivery ends it once; no redelivery."""
    execution = HoldingExecutionService()
    ctx, dispatcher = _build_dispatcher(tmp_path / 'repo-fifo-cancel-delivery', execution=execution, clock=lambda: '2026-03-30T00:00:00Z')

    a_source = dispatcher.submit(_ask('claude', 'codex', 'produce A', project_id=ctx.project_id, task_id='task-a'))
    a_source_job = a_source.jobs[0].job_id
    dispatcher.tick()
    dispatcher.complete(a_source_job, _turn_end_decision(reply='result A', turn_ref='turn-a'))
    dispatcher.tick()

    delivery = _active_delivery_job(dispatcher, 'codex')
    assert delivery is not None

    x_receipt = dispatcher.submit(_ask('codex', 'claude', 'task X', project_id=ctx.project_id, task_id='task-x'))
    x_job = x_receipt.jobs[0].job_id

    cancelled = dispatcher.cancel(delivery.job_id)
    assert cancelled.job_id == delivery.job_id

    # The cancelled delivery consumed the reply head; it is never requeued.
    inbox = dispatcher.inbox('codex')
    assert inbox['item_count'] == 1
    assert inbox['head']['event_type'] == 'task_request'
    for _ in range(3):
        dispatcher.tick()
    delivery_starts = [
        started_id
        for started_id in execution.started
        if (record := dispatcher.get(started_id)) is not None
        and record.request.message_type == 'reply_delivery'
    ]
    assert delivery_starts == [delivery.job_id]

    # The queued request proceeds normally after the cancel.
    started_after = [job for job in execution.started if job != a_source_job and job != delivery.job_id]
    assert started_after == [x_job]

    # Daemon restart keeps the consumed disposition; no delivery resurrection.
    layout = PathLayout(tmp_path / 'repo-fifo-cancel-delivery')
    config = _provider_config('codex', 'claude', 'gemini')
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('codex', project_id=ctx.project_id, layout=layout, pid=101))
    registry.upsert(_runtime('claude', project_id=ctx.project_id, layout=layout, pid=102))
    registry.upsert(_runtime('gemini', project_id=ctx.project_id, layout=layout, pid=103))
    restarted = JobDispatcher(
        layout,
        config,
        registry,
        execution_service=HoldingExecutionService(),
        auto_reply_delivery_on_complete=True,
        clock=lambda: '2026-03-30T00:01:00Z',
    )
    restarted.tick()
    assert restarted.inbox('codex')['item_count'] == 1
    assert restarted.inbox('codex')['head']['event_type'] == 'task_request'


def test_provider_completed_delivery_stays_consumed_after_restart(tmp_path: Path) -> None:
    """A provider turn end consumes the delivery once, including after restart."""
    execution = NoDeliveryFlagExecutionService()
    project_root = tmp_path / 'repo-fifo-turn-end-restart'
    ctx, dispatcher = _build_dispatcher(project_root, execution=execution, clock=lambda: '2026-03-30T00:00:00Z')

    a_source = dispatcher.submit(_ask('claude', 'codex', 'produce A', project_id=ctx.project_id, task_id='task-a'))
    a_source_job = a_source.jobs[0].job_id
    dispatcher.tick()
    dispatcher.complete(a_source_job, _turn_end_decision(reply='result A', turn_ref='turn-a'))
    dispatcher.tick()
    delivery = _active_delivery_job(dispatcher, 'codex')
    assert delivery is not None

    # A missing delivery flag does not bypass the normal completion path.
    update = ExecutionUpdate(
        job_id=delivery.job_id,
        items=(),
        decision=_turn_end_decision(reply='processed A', turn_ref=delivery.job_id),
        submission=execution._active[delivery.job_id],
    )
    execution.poll = lambda: (update,)
    terminalized = dispatcher.poll_completions()
    assert [job.job_id for job in terminalized] == [delivery.job_id]
    assert terminalized[0].status is JobStatus.COMPLETED
    assert dispatcher.inbox('codex')['item_count'] == 0

    layout = PathLayout(project_root)
    config = _provider_config('codex', 'claude', 'gemini')
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('codex', project_id=ctx.project_id, layout=layout, pid=101))
    registry.upsert(_runtime('claude', project_id=ctx.project_id, layout=layout, pid=102))
    registry.upsert(_runtime('gemini', project_id=ctx.project_id, layout=layout, pid=103))
    restarted = JobDispatcher(
        layout,
        config,
        registry,
        execution_service=HoldingExecutionService(),
        auto_reply_delivery_on_complete=True,
        clock=lambda: '2026-03-30T00:32:00Z',
    )
    restarted.tick()
    assert restarted.inbox('codex')['item_count'] == 0
    active = _active_delivery_job(restarted, 'codex')
    assert active is None or active.request.message_type != 'reply_delivery'

def test_validate_decision_normalizes_proven_empty_delivery_turn_ends() -> None:
    codex_submission = ProviderSubmission(
        job_id='job_d1',
        agent_name='codex',
        provider='codex',
        accepted_at='2026-03-30T00:00:00Z',
        ready_at='2026-03-30T00:00:00Z',
        source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
        reply='',
        runtime_state={
            'mode': 'active',
            'reply_delivery_complete_on_dispatch': True,
            'delivery_state': 'accepted',
            'anchor_seen': True,
        },
    )
    validated = _validate_provider_completion_decision(codex_submission, _empty_turn_end_decision())
    assert validated.status is CompletionStatus.COMPLETED
    assert validated.reason == 'reply_delivery_turn_complete'
    assert validated.diagnostics['reply_delivery_turn_end'] is True
    assert validated.diagnostics['original_reason'] == 'task_complete_empty_reply'


def test_validate_decision_keeps_unproven_delivery_failures_closed() -> None:
    codex_submission = ProviderSubmission(
        job_id='job_d2',
        agent_name='codex',
        provider='codex',
        accepted_at='2026-03-30T00:00:00Z',
        ready_at='2026-03-30T00:00:00Z',
        source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
        reply='',
        runtime_state={
            'mode': 'active',
            'reply_delivery_complete_on_dispatch': True,
            'delivery_state': 'pending_anchor',
            'anchor_seen': False,
        },
    )
    validated = _validate_provider_completion_decision(
        codex_submission,
        _turn_end_decision(reply=''),
    )
    # Ask and delivery completions share the codex acceptance isolation
    # gate: without anchor acceptance the delivery fails closed no matter
    # how the provider worded its terminal decision.
    assert validated.status is CompletionStatus.INCOMPLETE
    assert validated.reason == 'terminal_before_provider_acceptance'
    assert validated.diagnostics['completion_gate'] == 'provider_acceptance'

    normalized = _validate_provider_completion_decision(
        codex_submission,
        _empty_turn_end_decision(turn_ref='turn-unproven-empty'),
    )
    # The empty turn end is delivery-progress evidence and is recorded, but
    # it cannot complete the delivery without proven acceptance.
    assert normalized.status is CompletionStatus.INCOMPLETE
    assert normalized.reason == 'terminal_before_provider_acceptance'
    assert normalized.diagnostics['reply_delivery_turn_end'] is True


def test_validate_decision_normalizes_claude_empty_delivery_turn_end() -> None:
    claude_submission = ProviderSubmission(
        job_id='job_d3',
        agent_name='claude',
        provider='claude',
        accepted_at='2026-03-30T00:00:00Z',
        ready_at='2026-03-30T00:00:00Z',
        source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
        reply='',
        runtime_state={
            'mode': 'active',
            'reply_delivery_complete_on_dispatch': True,
            'prompt_sent': True,
        },
    )
    empty_hook = CompletionDecision(
        terminal=True,
        status=CompletionStatus.INCOMPLETE,
        reason='hook_stop_empty_reply',
        confidence=CompletionConfidence.EXACT,
        reply='',
        anchor_seen=True,
        reply_started=False,
        reply_stable=True,
        provider_turn_ref='turn-hook',
        source_cursor=None,
        finished_at='2026-03-30T00:00:10Z',
        diagnostics={'empty_reply': True, 'error_type': 'empty_provider_reply'},
    )
    validated = _validate_provider_completion_decision(claude_submission, empty_hook)
    assert validated.status is CompletionStatus.COMPLETED
    assert validated.reason == 'reply_delivery_turn_complete'


def test_validate_decision_keeps_claude_delivery_failure_attributable() -> None:
    claude_submission = ProviderSubmission(
        job_id='job_d4',
        agent_name='claude',
        provider='claude',
        accepted_at='2026-03-30T00:00:00Z',
        ready_at='2026-03-30T00:00:00Z',
        source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
        reply='',
        runtime_state={
            'mode': 'active',
            'reply_delivery_complete_on_dispatch': True,
            'prompt_sent': True,
        },
    )
    failed = CompletionDecision(
        terminal=True,
        status=CompletionStatus.FAILED,
        reason='pane_dead',
        confidence=CompletionConfidence.DEGRADED,
        reply='',
        anchor_seen=False,
        reply_started=False,
        reply_stable=False,
        provider_turn_ref='turn-dead',
        source_cursor=None,
        finished_at='2026-03-30T00:00:10Z',
        diagnostics={'error_type': 'pane_dead'},
    )
    validated = _validate_provider_completion_decision(claude_submission, failed)
    assert validated.status is CompletionStatus.FAILED
    assert validated.reason == 'pane_dead'


def test_validate_decision_normalizes_omp_empty_delivery_turn_end() -> None:
    omp_submission = ProviderSubmission(
        job_id='job_d5',
        agent_name='demo',
        provider='omp',
        accepted_at='2026-03-30T00:00:00Z',
        ready_at='2026-03-30T00:00:00Z',
        source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
        reply='',
        runtime_state={
            'mode': 'omp_pane',
            'reply_delivery_complete_on_dispatch': True,
            'prompt_sent': True,
            'anchor_seen': True,
        },
    )
    empty_settled = CompletionDecision(
        terminal=True,
        status=CompletionStatus.INCOMPLETE,
        reason='omp_empty_reply',
        confidence=CompletionConfidence.DEGRADED,
        reply='',
        anchor_seen=True,
        reply_started=False,
        reply_stable=True,
        provider_turn_ref='turn-omp-settled',
        source_cursor=None,
        finished_at='2026-03-30T00:00:10Z',
        diagnostics={'empty_reply': True},
    )
    validated = _validate_provider_completion_decision(omp_submission, empty_settled)
    assert validated.status is CompletionStatus.COMPLETED
    assert validated.reason == 'reply_delivery_turn_complete'


def test_validate_decision_keeps_omp_superseded_delivery_incomplete() -> None:
    omp_submission = ProviderSubmission(
        job_id='job_d6',
        agent_name='demo',
        provider='omp',
        accepted_at='2026-03-30T00:00:00Z',
        ready_at='2026-03-30T00:00:00Z',
        source_kind=CompletionSourceKind.SESSION_EVENT_LOG,
        reply='',
        runtime_state={
            'mode': 'omp_pane',
            'reply_delivery_complete_on_dispatch': True,
            'prompt_sent': True,
            'anchor_seen': True,
        },
    )
    superseded = CompletionDecision(
        terminal=True,
        status=CompletionStatus.INCOMPLETE,
        reason='omp_request_superseded',
        confidence=CompletionConfidence.DEGRADED,
        reply='',
        anchor_seen=True,
        reply_started=False,
        reply_stable=False,
        provider_turn_ref='turn-omp-superseded',
        source_cursor=None,
        finished_at='2026-03-30T00:00:10Z',
        diagnostics={'superseded_by': 'unmanaged_input'},
    )
    validated = _validate_provider_completion_decision(omp_submission, superseded)
    assert validated.status is CompletionStatus.INCOMPLETE
    assert validated.reason == 'omp_request_superseded'
