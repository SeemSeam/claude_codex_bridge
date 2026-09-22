from __future__ import annotations

from pathlib import Path

import pytest

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
from ccbd.api_models import DeliveryScope, JobStatus, MessageEnvelope, TargetKind
from ccbd.project_view import ProjectViewDependencies, ProjectViewService
from ccbd.services.dispatcher import JobDispatcher
from ccbd.services.mount import MountManager
from ccbd.services.project_namespace_state import ProjectNamespaceStateStore
from ccbd.services.registry import AgentRegistry
from completion.models import CompletionConfidence, CompletionDecision, CompletionStatus
from mailbox_kernel import InboundEventType
from project.ids import compute_project_id
from project.resolver import ProjectContext
from storage.paths import PathLayout

from ccbd.services.dispatcher_runtime.reply_delivery_runtime.repair import (
    REPLY_DELIVERY_PENDING_STALL_SECONDS,
    repair_agent_reply_delivery_head,
)

START = '2026-05-22T12:00:00Z'
LATER = '2026-05-22T12:10:00Z'


def _bootstrap(project_root: Path) -> ProjectContext:
    project_root.mkdir()
    config_dir = project_root / '.ccb'
    config_dir.mkdir()
    (config_dir / 'ccb.config').write_text('agent1:codex, agent2:claude\n', encoding='utf-8')
    return ProjectContext(
        cwd=project_root,
        project_root=project_root,
        config_dir=config_dir,
        project_id=compute_project_id(project_root),
        source='test',
    )


def _config() -> ProjectConfig:
    agents = {
        name: AgentSpec(
            name=name,
            provider=provider,
            target='.',
            workspace_mode=WorkspaceMode.INPLACE,
            workspace_root=None,
            runtime_mode=RuntimeMode.PANE_BACKED,
            restore_default=RestoreMode.AUTO,
            permission_default=PermissionMode.MANUAL,
            queue_policy=QueuePolicy.SERIAL_PER_AGENT,
        )
        for name, provider in (('agent1', 'codex'), ('agent2', 'claude'))
    }
    return ProjectConfig(version=2, default_agents=('agent1', 'agent2'), agents=agents, cmd_enabled=False)


def _runtime(agent_name: str, *, project_id: str, layout: PathLayout) -> AgentRuntime:
    return AgentRuntime(
        agent_name=agent_name,
        state=AgentState.IDLE,
        pid=100,
        started_at=START,
        last_seen_at=START,
        runtime_ref=f'tmux:%{agent_name[-1]}',
        session_ref=f'{agent_name}-session',
        workspace_path=str(layout.workspace_path(agent_name)),
        project_id=project_id,
        backend_type='tmux',
        queue_depth=0,
        socket_path=None,
        health='healthy',
        pane_id=f'%{agent_name[-1]}',
        pane_state='alive',
    )


def _decision(status: CompletionStatus, *, reply: str = '') -> CompletionDecision:
    return CompletionDecision(
        terminal=True,
        status=status,
        reason=status.value,
        confidence=CompletionConfidence.OBSERVED,
        reply=reply,
        anchor_seen=False,
        reply_started=False,
        reply_stable=False,
        provider_turn_ref=None,
        source_cursor=None,
        finished_at=START,
        diagnostics={},
    )


class _MutableClock:
    def __init__(self, value: str) -> None:
        self.value = value

    def __call__(self) -> str:
        return self.value


def _build_dispatcher(project_root: Path):
    ctx = _bootstrap(project_root)
    layout = PathLayout(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    for agent_name in config.agents:
        registry.upsert(_runtime(agent_name, project_id=ctx.project_id, layout=layout))
    clock = _MutableClock(START)
    dispatcher = JobDispatcher(layout, config, registry, clock=clock)
    return ctx, layout, config, registry, dispatcher, clock


def _submit(dispatcher: JobDispatcher, ctx: ProjectContext, *, sender: str, target: str) -> str:
    receipt = dispatcher.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent=target,
            from_actor=sender,
            body='work',
            task_id=None,
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    return receipt.jobs[0].job_id


def _delivery_job(dispatcher: JobDispatcher, agent_name: str):
    return next(
        job
        for job in dispatcher._job_store.list_agent(agent_name)
        if job.request.message_type == 'reply_delivery'
    )


def _conflict_lease_on_stale_event(dispatcher, source_agent: str) -> None:
    """Point the asker's delivery lease at a stale inbound event id.

    This is the production stall shape: the head reply event can never be
    claimed while an acquired lease pins a different (already consumed or
    bogus) inbound event, and no pre-existing repair covered the resulting
    pending delivery job.
    """
    lease_store = dispatcher._message_bureau_control._lease_store
    lease = lease_store.load(source_agent)
    from mailbox_kernel import DeliveryLease, LeaseState

    lease_store.save(
        DeliveryLease(
            agent_name=source_agent,
            inbound_event_id=(lease.inbound_event_id if lease else 'iev_stale_conflict') + '-stale',
            lease_version=(lease.lease_version if lease else 0) + 1,
            acquired_at=START,
            last_progress_at=START,
            expires_at=None,
            lease_state=LeaseState.ACQUIRED,
        )
    )


def test_stalled_pending_delivery_is_repaired_after_stall_threshold(tmp_path: Path) -> None:
    ctx, layout, config, registry, dispatcher, clock = _build_dispatcher(tmp_path / 'repo-stall-repair')
    source = _submit(dispatcher, ctx, sender='agent2', target='agent1')
    dispatcher.tick()
    dispatcher.complete(source, _decision(CompletionStatus.COMPLETED, reply='OK'))
    # Install the conflicting lease before the tick: the delivery job is then
    # created but can never be claimed, which is the production stall shape.
    _conflict_lease_on_stale_event(dispatcher, 'agent2')
    dispatcher.tick()
    delivery = _delivery_job(dispatcher, 'agent2')
    assert delivery.status in {JobStatus.ACCEPTED, JobStatus.QUEUED}

    # Before the stall threshold the pending delivery is left untouched.
    assert repair_agent_reply_delivery_head(dispatcher, 'agent2') is None
    untouched = dispatcher.get(delivery.job_id)
    assert untouched.status in {JobStatus.ACCEPTED, JobStatus.QUEUED}

    # Advance the clock past the stall threshold: the repair must release the
    # reply head so a fresh delivery job can be prepared and claimed.
    clock.value = '2026-05-22T12:20:00Z'
    assert REPLY_DELIVERY_PENDING_STALL_SECONDS > 0
    result = repair_agent_reply_delivery_head(dispatcher, 'agent2')
    head = dispatcher._message_bureau_control._mailbox_kernel.head_pending_event('agent2')
    assert head is not None
    assert head.event_type is InboundEventType.TASK_REPLY
    assert head.payload_ref is not None
    # The head no longer pins the stalled delivery job id.
    from message_bureau.reply_payloads import delivery_job_id_from_payload

    assert delivery_job_id_from_payload(head.payload_ref) != delivery.job_id
    events = dispatcher._event_store.read_since_target(TargetKind.AGENT, 'agent2', 0)[1]
    stall_events = [record for record in events if record.type == 'reply_delivery_pending_stall_repaired']
    assert stall_events, 'pending stall repair must be auditable'


def test_fresh_pending_delivery_is_not_repaired(tmp_path: Path) -> None:
    ctx, layout, config, registry, dispatcher, clock = _build_dispatcher(tmp_path / 'repo-stall-fresh')
    source = _submit(dispatcher, ctx, sender='agent2', target='agent1')
    dispatcher.tick()
    dispatcher.complete(source, _decision(CompletionStatus.COMPLETED, reply='OK'))
    _conflict_lease_on_stale_event(dispatcher, 'agent2')
    dispatcher.tick()
    delivery = _delivery_job(dispatcher, 'agent2')
    assert delivery.status in {JobStatus.ACCEPTED, JobStatus.QUEUED}

    result = repair_agent_reply_delivery_head(dispatcher, 'agent2')

    assert result is None
    current = dispatcher.get(delivery.job_id)
    assert current.status in {JobStatus.ACCEPTED, JobStatus.QUEUED}


def test_project_view_surfaces_pending_reply_delivery_row(tmp_path: Path) -> None:
    ctx, layout, config, registry, dispatcher, clock = _build_dispatcher(tmp_path / 'repo-comms-pending-delivery')
    source = _submit(dispatcher, ctx, sender='agent2', target='agent1')
    dispatcher.tick()
    dispatcher.complete(source, _decision(CompletionStatus.COMPLETED, reply='OK'))
    _conflict_lease_on_stale_event(dispatcher, 'agent2')
    dispatcher.tick()
    delivery = _delivery_job(dispatcher, 'agent2')
    assert delivery.status in {JobStatus.ACCEPTED, JobStatus.QUEUED}
    clock.value = LATER

    mount_manager = MountManager(layout, clock=lambda: LATER)
    mount_manager.mark_mounted(project_id=ctx.project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=LATER)
    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=ctx.project_root,
            project_id=ctx.project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            clock=lambda: LATER,
        )
    )
    comms = service.build_response()['view']['comms']
    delivery_rows = [
        row
        for row in comms
        if row.get('id') == delivery.job_id
    ]
    assert delivery_rows, 'pending reply delivery must appear as its own comms row'
    row = delivery_rows[0]
    assert row['business_status'] == 'delivering'
    assert row['status'] in {'accepted', 'queued'}
    assert row.get('reply_delivery_source_job_id') == source
