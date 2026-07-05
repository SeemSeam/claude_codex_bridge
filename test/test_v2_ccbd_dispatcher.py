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
from ccbd.reload_drain import DrainIntent, DrainQueueStore
from ccbd.services.dispatcher import JobDispatcher
from ccbd.services.runtime import RuntimeService
from ccbd.services.registry import AgentRegistry
from completion.tracker import CompletionTrackerService
from completion.models import CompletionConfidence, CompletionCursor, CompletionDecision, CompletionSourceKind, CompletionState, CompletionStatus
from completion.tracker import CompletionTrackerView
from project.ids import compute_project_id
from project.resolver import ProjectContext
from provider_core.contracts import ProviderSessionBinding
from provider_core.catalog import build_default_provider_catalog
from provider_execution.registry import build_default_execution_registry
from provider_execution.service import ExecutionRestoreResult, ExecutionService
from provider_execution.service_runtime.models import ExecutionUpdate
from provider_execution.state_store import ExecutionStateStore
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


def _runtime(agent_name: str, *, project_id: str, layout: PathLayout, pid: int) -> AgentRuntime:
    return AgentRuntime(
        agent_name=agent_name,
        state=AgentState.IDLE,
        pid=pid,
        started_at='2026-03-18T00:00:00Z',
        last_seen_at='2026-03-18T00:00:00Z',
        runtime_ref=f'{agent_name}-runtime',
        session_ref=f'{agent_name}-session',
        workspace_path=str(layout.workspace_path(agent_name)),
        project_id=project_id,
        backend_type='tmux',
        queue_depth=0,
        socket_path=None,
        health='healthy',
    )


class StepClock:
    def __init__(self, *values: str) -> None:
        self._values = list(values)
        self._index = 0
        self._last = values[-1] if values else '2026-03-18T00:00:00Z'

    def __call__(self) -> str:
        if self._index < len(self._values):
            self._last = self._values[self._index]
            self._index += 1
        return self._last


def _fake_config(*, provider: str = 'fake') -> ProjectConfig:
    spec = AgentSpec(
        name='demo',
        provider=provider,
        target='.',
        workspace_mode=WorkspaceMode.GIT_WORKTREE,
        workspace_root=None,
        runtime_mode=RuntimeMode.PANE_BACKED,
        restore_default=RestoreMode.AUTO,
        permission_default=PermissionMode.MANUAL,
        queue_policy=QueuePolicy.SERIAL_PER_AGENT,
    )
    return ProjectConfig(version=2, default_agents=('demo',), agents={'demo': spec})


def _provider_config(*providers: str, dispatch_disabled: set[str] | None = None) -> ProjectConfig:
    agents: dict[str, AgentSpec] = {}
    disabled = set(dispatch_disabled or set())
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
            dispatch_disabled=provider in disabled,
        )
    return ProjectConfig(version=2, default_agents=tuple(providers), agents=agents)


class RecoveringBindingSession:
    def __init__(
        self,
        *,
        pane_id: str,
        fake_session_id: str,
        recovered_pane_id: str,
        recovered_session_id: str,
        recover_ok: bool = True,
    ) -> None:
        self.pane_id = pane_id
        self.terminal = 'tmux'
        self.fake_session_id = fake_session_id
        self.fake_session_path = None
        self._recovered_pane_id = recovered_pane_id
        self._recovered_session_id = recovered_session_id
        self._recover_ok = recover_ok
        self.ensure_calls = 0

    def ensure_pane(self):
        self.ensure_calls += 1
        if not self._recover_ok:
            return False, 'pane_dead'
        self.pane_id = self._recovered_pane_id
        self.fake_session_id = self._recovered_session_id
        return True, self.pane_id


def _binding_map(provider: str, session: RecoveringBindingSession) -> dict[str, ProviderSessionBinding]:
    return {
        provider: ProviderSessionBinding(
            provider=provider,
            load_session=lambda root, instance, provider=provider, session=session: (
                session if instance in {None, provider} else None
            ),
            session_id_attr='fake_session_id',
            session_path_attr='fake_session_path',
        )
    }


class RecordingExecutionService:
    def __init__(self) -> None:
        self.calls: list[tuple[object, object]] = []

    def start(self, job, *, runtime_context=None) -> None:
        self.calls.append((job, runtime_context))

    def cancel(self, job_id: str) -> None:
        del job_id

    def finish(self, job_id: str) -> None:
        del job_id

    def poll(self):
        return ()


class FailingRestoreExecutionService(RecordingExecutionService):
    def restore(self, job, *, runtime_context=None):
        del runtime_context
        return ExecutionRestoreResult(
            job_id=job.job_id,
            agent_name=job.agent_name,
            provider=job.provider,
            status='abandoned',
            reason='provider_resume_unsupported',
            resume_capable=False,
        )


class LateUpdateExecutionService:
    def __init__(self, state_store: ExecutionStateStore, *updates: ExecutionUpdate) -> None:
        self._state_store = state_store
        self._updates = list(updates)
        self.finished: list[str] = []

    def poll(self):
        return tuple(self._updates)

    def finish(self, job_id: str) -> None:
        self.finished.append(job_id)
        self._state_store.remove(job_id)


@pytest.mark.parametrize('provider', ['codex', 'claude', 'gemini'])
def test_runtime_service_refresh_provider_binding_recovers_tmux_binding(provider: str, tmp_path: Path) -> None:
    project_root = tmp_path / f'repo-refresh-{provider}'
    ctx = _bootstrap_test_project(project_root)
    layout = PathLayout(project_root)
    config = _provider_config(provider)
    registry = AgentRegistry(layout, config)
    session = RecoveringBindingSession(
        pane_id='%41',
        fake_session_id=f'{provider}-session-old',
        recovered_pane_id='%88',
        recovered_session_id=f'{provider}-session-new',
    )
    runtime_service = RuntimeService(
        layout,
        registry,
        ctx.project_id,
        session_bindings=_binding_map(provider, session),
        clock=lambda: '2026-03-18T00:00:00Z',
    )
    runtime_service.attach(
        agent_name=provider,
        workspace_path=str(layout.workspace_path(provider)),
        backend_type='tmux',
        pid=101,
        runtime_ref='tmux:%41',
        session_ref=f'{provider}-session-old',
        health='pane-dead',
    )

    refreshed = runtime_service.refresh_provider_binding(provider, recover=True)

    assert refreshed is not None
    assert refreshed.state is AgentState.IDLE
    assert refreshed.health == 'healthy'
    assert refreshed.runtime_ref == 'tmux:%88'
    assert refreshed.session_ref == f'{provider}-session-new'
    assert session.ensure_calls == 1
    persisted = registry.get(provider)
    assert persisted is not None
    assert persisted.runtime_ref == 'tmux:%88'
    assert persisted.session_ref == f'{provider}-session-new'


def test_runtime_service_attach_reconciles_helper_ownership_via_registry_upsert(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-helper-attach'
    ctx = _bootstrap_test_project(project_root)
    layout = PathLayout(project_root)
    config = _provider_config('codex')
    registry = AgentRegistry(layout, config)
    runtime_service = RuntimeService(layout, registry, ctx.project_id, clock=lambda: '2026-03-18T00:00:00Z')
    seen: list[tuple[str, str]] = []

    monkeypatch.setattr(
        'ccbd.services.registry.cleanup_stale_runtime_helper',
        lambda layout, runtime: seen.append(('cleanup', runtime.agent_name)) or False,
    )
    monkeypatch.setattr(
        'ccbd.services.registry.sync_runtime_helper_manifest',
        lambda layout, runtime: seen.append(('sync', runtime.agent_name)) or None,
    )

    runtime_service.attach(
        agent_name='codex',
        workspace_path=str(layout.workspace_path('codex')),
        backend_type='tmux',
        pid=101,
        runtime_ref='tmux:%41',
        session_ref='codex-session-old',
        health='healthy',
        provider='codex',
    )

    assert seen == [('cleanup', 'codex'), ('sync', 'codex')]


def test_dispatcher_submit_tick_complete_roundtrip(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    ctx = _bootstrap_test_project(project_root)
    layout = PathLayout(project_root)
    config = _provider_config('codex')
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('codex', project_id=ctx.project_id, layout=layout, pid=101))
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: '2026-03-18T00:00:00Z')

    receipt = dispatcher.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent='codex',
            from_actor='user',
            body='hello',
            task_id='task-1',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    job_id = receipt.jobs[0].job_id
    runtime = registry.get('codex')
    assert runtime is not None and runtime.queue_depth == 1
    binding_generation = runtime.binding_generation
    runtime_generation = runtime.runtime_generation
    daemon_generation = runtime.daemon_generation

    started = dispatcher.tick()
    assert len(started) == 1
    assert started[0].job_id == job_id
    runtime = registry.get('codex')
    assert runtime is not None and runtime.state is AgentState.BUSY
    assert runtime.binding_generation == binding_generation
    assert runtime.runtime_generation == runtime_generation
    assert runtime.daemon_generation == daemon_generation

    terminal = dispatcher.complete(
        job_id,
        CompletionDecision(
            terminal=True,
            status=CompletionStatus.COMPLETED,
            reason='task_complete',
            confidence=CompletionConfidence.EXACT,
            reply='done',
            anchor_seen=True,
            reply_started=True,
            reply_stable=True,
            provider_turn_ref='turn-1',
            source_cursor=None,
            finished_at='2026-03-18T00:00:10Z',
            diagnostics={},
        ),
    )
    assert terminal.status.value == 'completed'
    snapshot = dispatcher.get_snapshot(job_id)
    assert snapshot is not None
    assert snapshot.latest_decision.reply == 'done'
    runtime = registry.get('codex')
    assert runtime is not None and runtime.state is AgentState.IDLE and runtime.queue_depth == 0
    assert runtime.binding_generation == binding_generation
    assert runtime.runtime_generation == runtime_generation
    assert runtime.daemon_generation == daemon_generation


def test_dispatcher_allows_non_mailbox_email_sender(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-allow-email-sender'
    ctx = _bootstrap_test_project(project_root)
    layout = PathLayout(project_root)
    config = _provider_config('codex')
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('codex', project_id=ctx.project_id, layout=layout, pid=101))
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: '2026-03-18T00:00:00Z')

    receipt = dispatcher.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent='codex',
            from_actor='email',
            body='hello',
            task_id='email-req-1',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )

    assert len(receipt.jobs) == 1
    current = dispatcher.get(receipt.jobs[0].job_id)
    assert current is not None
    assert current.request.from_actor == 'email'


def test_dispatcher_does_not_overwrite_terminal_snapshot_with_non_terminal_tracker_view(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-terminal-guard'
    ctx = _bootstrap_test_project(project_root)
    layout = PathLayout(project_root)
    config = _provider_config('codex')
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('codex', project_id=ctx.project_id, layout=layout, pid=101))
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: '2026-03-18T00:00:00Z')

    receipt = dispatcher.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent='codex',
            from_actor='user',
            body='hello',
            task_id='task-1',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    job_id = receipt.jobs[0].job_id
    dispatcher.tick()
    dispatcher.complete(
        job_id,
        CompletionDecision(
            terminal=True,
            status=CompletionStatus.COMPLETED,
            reason='task_complete',
            confidence=CompletionConfidence.EXACT,
            reply='done',
            anchor_seen=True,
            reply_started=True,
            reply_stable=True,
            provider_turn_ref='turn-1',
            source_cursor=CompletionCursor(
                source_kind=CompletionSourceKind.PROTOCOL_EVENT_STREAM,
                event_seq=3,
                updated_at='2026-03-18T00:00:10Z',
            ),
            finished_at='2026-03-18T00:00:10Z',
            diagnostics={},
        ),
    )
    terminal_snapshot = dispatcher.get_snapshot(job_id)
    assert terminal_snapshot is not None
    assert terminal_snapshot.state.terminal is True
    assert terminal_snapshot.latest_decision.reply == 'done'

    changed = dispatcher._apply_tracker_view(
        dispatcher.get(job_id),
        CompletionTrackerView(
            job_id=job_id,
            agent_name='codex',
            state=CompletionState(
                anchor_seen=False,
                reply_started=False,
                reply_stable=False,
                terminal=False,
                latest_cursor=CompletionCursor(
                    source_kind=CompletionSourceKind.PROTOCOL_EVENT_STREAM,
                    event_seq=1,
                    updated_at='2026-03-18T00:00:05Z',
                ),
            ),
            decision=CompletionDecision.pending(
                cursor=CompletionCursor(
                    source_kind=CompletionSourceKind.PROTOCOL_EVENT_STREAM,
                    event_seq=1,
                    updated_at='2026-03-18T00:00:05Z',
                )
            ),
        ),
    )
    assert changed is False
    stable_snapshot = dispatcher.get_snapshot(job_id)
    assert stable_snapshot is not None
    assert stable_snapshot.state.terminal is True
    assert stable_snapshot.latest_decision.reply == 'done'


def test_dispatcher_broadcast_excludes_sender_and_only_targets_alive_agents(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    ctx = _bootstrap_test_project(project_root)
    layout = PathLayout(project_root)
    config = _provider_config('codex', 'claude')
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('codex', project_id=ctx.project_id, layout=layout, pid=101))
    registry.upsert(_runtime('claude', project_id=ctx.project_id, layout=layout, pid=102))
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: '2026-03-18T00:00:00Z')

    receipt = dispatcher.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent='all',
            from_actor='codex',
            body='broadcast',
            task_id='task-1',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.BROADCAST,
        )
    )

    assert receipt.submission_id is not None
    assert [job.agent_name for job in receipt.jobs] == ['claude']


def test_dispatcher_rejects_dispatch_disabled_single_target(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-dispatch-disabled-single'
    ctx = _bootstrap_test_project(project_root)
    layout = PathLayout(project_root)
    config = _provider_config('codex', dispatch_disabled={'codex'})
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('codex', project_id=ctx.project_id, layout=layout, pid=101))
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: '2026-03-18T00:00:00Z')

    with pytest.raises(Exception, match='agent codex is dispatch-disabled'):
        dispatcher.submit(
            MessageEnvelope(
                project_id=ctx.project_id,
                to_agent='codex',
                from_actor='user',
                body='hello',
                task_id='task-1',
                reply_to=None,
                message_type='ask',
                delivery_scope=DeliveryScope.SINGLE,
            )
        )


def test_dispatcher_broadcast_skips_dispatch_disabled_agents(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-dispatch-disabled-broadcast'
    ctx = _bootstrap_test_project(project_root)
    layout = PathLayout(project_root)
    config = _provider_config('codex', 'claude', dispatch_disabled={'claude'})
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('codex', project_id=ctx.project_id, layout=layout, pid=101))
    registry.upsert(_runtime('claude', project_id=ctx.project_id, layout=layout, pid=102))
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: '2026-03-18T00:00:00Z')

    receipt = dispatcher.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent='all',
            from_actor='user',
            body='broadcast',
            task_id='task-1',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.BROADCAST,
        )
    )

    assert receipt.submission_id is not None
    assert [job.agent_name for job in receipt.jobs] == ['codex']


def test_dispatcher_rejects_targets_with_active_reload_drain(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-draining-target'
    ctx = _bootstrap_test_project(project_root)
    layout = PathLayout(project_root)
    config = _provider_config('codex', 'claude')
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('codex', project_id=ctx.project_id, layout=layout, pid=101))
    registry.upsert(_runtime('claude', project_id=ctx.project_id, layout=layout, pid=102))
    store = DrainQueueStore(layout)
    result = store.load().enqueue(
        DrainIntent(
            intent_id='drain-claude',
            intent_kind='unload',
            agent_name='claude',
            created_at_s=1.0,
            reason='test drain',
        ),
        now_s=1.0,
    )
    store.save(result.queue)
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: '2026-03-18T00:00:00Z')

    with pytest.raises(Exception, match='agent claude is draining and rejects new work'):
        dispatcher.submit(
            MessageEnvelope(
                project_id=ctx.project_id,
                to_agent='claude',
                from_actor='user',
                body='hello',
                task_id='task-1',
                reply_to=None,
                message_type='ask',
                delivery_scope=DeliveryScope.SINGLE,
            )
        )

    receipt = dispatcher.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent='all',
            from_actor='user',
            body='broadcast',
            task_id='task-2',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.BROADCAST,
        )
    )
    assert [job.agent_name for job in receipt.jobs] == ['codex']


def test_dispatcher_rejects_unknown_sender_agent(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-unknown-sender'
    ctx = _bootstrap_test_project(project_root)
    layout = PathLayout(project_root)
    config = _provider_config('codex')
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('codex', project_id=ctx.project_id, layout=layout, pid=101))
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: '2026-03-18T00:00:00Z')

    try:
        dispatcher.submit(
            MessageEnvelope(
                project_id=ctx.project_id,
                to_agent='codex',
                from_actor='agent9',
                body='hello',
                task_id=None,
                reply_to=None,
                message_type='ask',
                delivery_scope=DeliveryScope.SINGLE,
            )
        )
    except Exception as exc:
        assert str(exc) == 'unknown sender agent: agent9'
    else:
        raise AssertionError('expected dispatcher.submit to reject unknown sender agent')


def test_dispatcher_cancel_queued_job(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    ctx = _bootstrap_test_project(project_root)
    layout = PathLayout(project_root)
    config = _provider_config('codex')
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('codex', project_id=ctx.project_id, layout=layout, pid=101))
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: '2026-03-18T00:00:00Z')

    first = dispatcher.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent='codex',
            from_actor='user',
            body='one',
            task_id=None,
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    second = dispatcher.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent='codex',
            from_actor='user',
            body='two',
            task_id=None,
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    assert dispatcher.get(first.jobs[0].job_id).status.value == 'accepted'
    assert dispatcher.get(second.jobs[0].job_id).status.value == 'queued'

    receipt = dispatcher.cancel(second.jobs[0].job_id)
    assert receipt.status.value == 'cancelled'
    assert dispatcher.get(second.jobs[0].job_id).status.value == 'cancelled'


def test_dispatcher_watch_resolves_latest_job_and_terminal_events(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    ctx = _bootstrap_test_project(project_root)
    layout = PathLayout(project_root)
    config = _provider_config('codex')
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('codex', project_id=ctx.project_id, layout=layout, pid=101))
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: '2026-03-18T00:00:00Z')

    receipt = dispatcher.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent='codex',
            from_actor='user',
            body='hello',
            task_id=None,
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    job_id = receipt.jobs[0].job_id
    dispatcher.tick()
    dispatcher.complete(
        job_id,
        CompletionDecision(
            terminal=True,
            status=CompletionStatus.COMPLETED,
            reason='task_complete',
            confidence=CompletionConfidence.EXACT,
            reply='done',
            anchor_seen=True,
            reply_started=True,
            reply_stable=True,
            provider_turn_ref='turn-1',
            source_cursor=None,
            finished_at='2026-03-18T00:00:10Z',
            diagnostics={},
        ),
    )

    payload = dispatcher.watch('codex', start_line=0)
    assert payload['job_id'] == job_id
    assert payload['terminal'] is True
    event_types = [event['type'] for event in payload['events']]
    assert event_types == ['job_accepted', 'job_started', 'completion_terminal', 'job_completed']


def test_dispatcher_passes_runtime_context_to_execution_service(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    ctx = _bootstrap_test_project(project_root)
    layout = PathLayout(project_root)
    config = _provider_config('codex')
    registry = AgentRegistry(layout, config)
    runtime = _runtime('codex', project_id=ctx.project_id, layout=layout, pid=101)
    registry.upsert(runtime)
    execution_service = RecordingExecutionService()
    dispatcher = JobDispatcher(layout, config, registry, execution_service=execution_service, clock=lambda: '2026-03-18T00:00:00Z')

    dispatcher.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent='codex',
            from_actor='user',
            body='hello',
            task_id=None,
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    dispatcher.tick()

    assert len(execution_service.calls) == 1
    _, runtime_context = execution_service.calls[0]
    assert runtime_context is not None
    assert runtime_context.agent_name == 'codex'
    assert runtime_context.workspace_path == str(layout.workspace_path('codex'))
    assert runtime_context.runtime_ref == 'codex-runtime'
    assert runtime_context.session_ref == 'codex-session'
    assert runtime_context.runtime_pid == 101


def test_dispatcher_uses_latest_attached_binding_refs(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-runtime-binding'
    ctx = _bootstrap_test_project(project_root)
    layout = PathLayout(project_root)
    config = _provider_config('codex')
    registry = AgentRegistry(layout, config)
    runtime_service = RuntimeService(layout, registry, ctx.project_id, clock=lambda: '2026-03-18T00:00:00Z')
    runtime_service.attach(
        agent_name='codex',
        workspace_path=str(layout.workspace_path('codex')),
        backend_type='pane-backed',
    )
    runtime_service.attach(
        agent_name='codex',
        workspace_path=str(layout.workspace_path('codex')),
        backend_type='pane-backed',
        runtime_ref='tmux:%88',
        session_ref='codex-session-new',
        health='restored',
    )
    execution_service = RecordingExecutionService()
    dispatcher = JobDispatcher(
        layout,
        config,
        registry,
        runtime_service=runtime_service,
        execution_service=execution_service,
        clock=lambda: '2026-03-18T00:00:00Z',
    )

    dispatcher.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent='codex',
            from_actor='user',
            body='hello',
            task_id=None,
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    dispatcher.tick()

    assert len(execution_service.calls) == 1
    _, runtime_context = execution_service.calls[0]
    assert runtime_context is not None
    assert runtime_context.runtime_ref == 'tmux:%88'
    assert runtime_context.session_ref == 'codex-session-new'


@pytest.mark.parametrize('provider', ['codex', 'claude', 'gemini'])
def test_dispatcher_tick_refreshes_recoverable_runtime_binding_before_start(
    provider: str,
    tmp_path: Path,
) -> None:
    project_root = tmp_path / f'repo-dispatch-refresh-{provider}'
    ctx = _bootstrap_test_project(project_root)
    layout = PathLayout(project_root)
    config = _provider_config(provider)
    registry = AgentRegistry(layout, config)
    session = RecoveringBindingSession(
        pane_id='%41',
        fake_session_id=f'{provider}-session-old',
        recovered_pane_id='%77',
        recovered_session_id=f'{provider}-session-new',
    )
    runtime_service = RuntimeService(
        layout,
        registry,
        ctx.project_id,
        session_bindings=_binding_map(provider, session),
        clock=lambda: '2026-03-18T00:00:00Z',
    )
    runtime_service.attach(
        agent_name=provider,
        workspace_path=str(layout.workspace_path(provider)),
        backend_type='tmux',
        pid=101,
        runtime_ref='tmux:%41',
        session_ref=f'{provider}-session-old',
        health='pane-dead',
    )
    execution_service = RecordingExecutionService()
    dispatcher = JobDispatcher(
        layout,
        config,
        registry,
        runtime_service=runtime_service,
        execution_service=execution_service,
        clock=lambda: '2026-03-18T00:00:00Z',
    )

    receipt = dispatcher.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent=provider,
            from_actor='user',
            body='recover binding before start',
            task_id=None,
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )

    started = dispatcher.tick()

    assert len(started) == 1
    assert started[0].job_id == receipt.jobs[0].job_id
    assert len(execution_service.calls) == 1
    _, runtime_context = execution_service.calls[0]
    assert runtime_context is not None
    assert runtime_context.runtime_ref == 'tmux:%77'
    assert runtime_context.session_ref == f'{provider}-session-new'
    assert runtime_context.runtime_health == 'healthy'
    assert session.ensure_calls == 1
    runtime = registry.get(provider)
    assert runtime is not None
    assert runtime.state is AgentState.BUSY
    assert runtime.runtime_ref == 'tmux:%77'
    assert runtime.session_ref == f'{provider}-session-new'


def test_dispatcher_tick_keeps_job_queued_when_runtime_recovery_fails(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-dispatch-refresh-fail'
    ctx = _bootstrap_test_project(project_root)
    layout = PathLayout(project_root)
    config = _provider_config('codex')
    registry = AgentRegistry(layout, config)
    session = RecoveringBindingSession(
        pane_id='%41',
        fake_session_id='codex-session-old',
        recovered_pane_id='%77',
        recovered_session_id='codex-session-new',
        recover_ok=False,
    )
    runtime_service = RuntimeService(
        layout,
        registry,
        ctx.project_id,
        session_bindings=_binding_map('codex', session),
        clock=lambda: '2026-03-18T00:00:00Z',
    )
    runtime_service.attach(
        agent_name='codex',
        workspace_path=str(layout.workspace_path('codex')),
        backend_type='tmux',
        pid=101,
        runtime_ref='tmux:%41',
        session_ref='codex-session-old',
        health='pane-dead',
    )
    execution_service = RecordingExecutionService()
    dispatcher = JobDispatcher(
        layout,
        config,
        registry,
        runtime_service=runtime_service,
        execution_service=execution_service,
        clock=lambda: '2026-03-18T00:00:00Z',
    )

    receipt = dispatcher.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent='codex',
            from_actor='user',
            body='stay queued on failed recover',
            task_id=None,
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )

    assert dispatcher.tick() == ()
    current = dispatcher.get(receipt.jobs[0].job_id)
    assert current is not None
    assert current.status is JobStatus.ACCEPTED
    assert execution_service.calls == []
    runtime = registry.get('codex')
    assert runtime is not None
    assert runtime.state is AgentState.DEGRADED
    assert runtime.health == 'pane-dead'
    assert session.ensure_calls == 1


def test_dispatcher_tick_runs_jobs_in_parallel_across_agents_but_serializes_per_agent(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-dispatch-multi-agent'
    ctx = _bootstrap_test_project(project_root)
    layout = PathLayout(project_root)
    config = _provider_config('codex', 'claude')
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('codex', project_id=ctx.project_id, layout=layout, pid=101))
    registry.upsert(_runtime('claude', project_id=ctx.project_id, layout=layout, pid=102))
    execution_service = RecordingExecutionService()
    dispatcher = JobDispatcher(
        layout,
        config,
        registry,
        execution_service=execution_service,
        clock=lambda: '2026-03-18T00:00:00Z',
    )

    first_codex = dispatcher.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent='codex',
            from_actor='user',
            body='codex one',
            task_id=None,
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    second_codex = dispatcher.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent='codex',
            from_actor='user',
            body='codex two',
            task_id=None,
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    claude = dispatcher.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent='claude',
            from_actor='user',
            body='claude one',
            task_id=None,
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )

    started = dispatcher.tick()

    started_ids = {job.job_id for job in started}
    assert started_ids == {first_codex.jobs[0].job_id, claude.jobs[0].job_id}
    assert len(execution_service.calls) == 2
    assert {job.job_id for job, _runtime in execution_service.calls} == started_ids

    first_state = dispatcher.get(first_codex.jobs[0].job_id)
    second_state = dispatcher.get(second_codex.jobs[0].job_id)
    claude_state = dispatcher.get(claude.jobs[0].job_id)
    assert first_state is not None and first_state.status is JobStatus.RUNNING
    assert second_state is not None and second_state.status is JobStatus.QUEUED
    assert claude_state is not None and claude_state.status is JobStatus.RUNNING


def test_dispatcher_tick_starts_healthy_agent_when_other_agent_binding_recovery_fails(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-dispatch-isolated-recovery'
    ctx = _bootstrap_test_project(project_root)
    layout = PathLayout(project_root)
    config = _provider_config('codex', 'claude')
    registry = AgentRegistry(layout, config)
    session = RecoveringBindingSession(
        pane_id='%41',
        fake_session_id='codex-session-old',
        recovered_pane_id='%77',
        recovered_session_id='codex-session-new',
        recover_ok=False,
    )
    runtime_service = RuntimeService(
        layout,
        registry,
        ctx.project_id,
        session_bindings=_binding_map('codex', session),
        clock=lambda: '2026-03-18T00:00:00Z',
    )
    runtime_service.attach(
        agent_name='codex',
        workspace_path=str(layout.workspace_path('codex')),
        backend_type='tmux',
        pid=101,
        runtime_ref='tmux:%41',
        session_ref='codex-session-old',
        health='pane-dead',
    )
    runtime_service.attach(
        agent_name='claude',
        workspace_path=str(layout.workspace_path('claude')),
        backend_type='tmux',
        pid=102,
        runtime_ref='tmux:%42',
        session_ref='claude-session',
        health='healthy',
    )
    execution_service = RecordingExecutionService()
    dispatcher = JobDispatcher(
        layout,
        config,
        registry,
        runtime_service=runtime_service,
        execution_service=execution_service,
        clock=lambda: '2026-03-18T00:00:00Z',
    )

    codex = dispatcher.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent='codex',
            from_actor='user',
            body='recover codex before start',
            task_id=None,
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    claude = dispatcher.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent='claude',
            from_actor='user',
            body='claude should still start',
            task_id=None,
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )

    started = dispatcher.tick()

    assert [job.job_id for job in started] == [claude.jobs[0].job_id]
    assert [job.job_id for job, _runtime in execution_service.calls] == [claude.jobs[0].job_id]
    codex_state = dispatcher.get(codex.jobs[0].job_id)
    claude_state = dispatcher.get(claude.jobs[0].job_id)
    assert codex_state is not None and codex_state.status is JobStatus.ACCEPTED
    assert claude_state is not None and claude_state.status is JobStatus.RUNNING

    codex_runtime = registry.get('codex')
    claude_runtime = registry.get('claude')
    assert codex_runtime is not None and codex_runtime.state is AgentState.DEGRADED
    assert claude_runtime is not None and claude_runtime.state is AgentState.BUSY
    assert session.ensure_calls == 1


def test_dispatcher_persists_completion_items_and_state_updates_for_fake_provider(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-fake'
    ctx = _bootstrap_test_project(project_root)
    layout = PathLayout(project_root)
    config = _fake_config()
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('demo', project_id=ctx.project_id, layout=layout, pid=201))
    provider_catalog = build_default_provider_catalog()
    clock = StepClock(
        *(['2026-03-18T00:00:00Z'] * 5),
        '2026-03-18T00:00:00.100000Z',
        '2026-03-18T00:00:00.200000Z',
        '2026-03-18T00:00:00.400000Z',
        '2026-03-18T00:00:00.400000Z',
    )
    execution_state_store = ExecutionStateStore(layout)
    dispatcher = JobDispatcher(
        layout,
        config,
        registry,
        execution_service=ExecutionService(build_default_execution_registry(), clock=clock, state_store=execution_state_store),
        completion_tracker=CompletionTrackerService(config, provider_catalog),
        provider_catalog=provider_catalog,
        clock=clock,
    )

    receipt = dispatcher.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent='demo',
            from_actor='user',
            body='hello fake',
            task_id='fake;latency_ms=400',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    job_id = receipt.jobs[0].job_id
    dispatcher.tick()

    completed = dispatcher.poll_completions()
    assert completed == ()
    running = dispatcher.get(job_id)
    assert running is not None
    assert running.status is JobStatus.RUNNING
    snapshot = dispatcher.get_snapshot(job_id)
    assert snapshot is not None
    assert snapshot.latest_decision.reply == 'FAKE[demo] hello fake'
    assert snapshot.state.anchor_seen is True
    assert snapshot.state.reply_started is True
    persisted = execution_state_store.load(job_id)
    assert persisted is not None
    assert persisted.pending_items == ()

    watch_running = dispatcher.watch(job_id, start_line=0)
    running_event_types = [event['type'] for event in watch_running['events']]
    assert running_event_types[:2] == ['job_accepted', 'job_started']
    assert running_event_types.count('completion_item') == 2
    assert running_event_types.count('completion_state_updated') >= 2
    assert watch_running['terminal'] is False

    completed = dispatcher.poll_completions()
    assert len(completed) == 1
    assert completed[0].job_id == job_id
    watch_terminal = dispatcher.watch(job_id, start_line=0)
    terminal_event_types = [event['type'] for event in watch_terminal['events']]
    assert terminal_event_types.count('completion_item') == 3
    assert 'completion_terminal' in terminal_event_types
    assert terminal_event_types[-1] == 'job_completed'
    assert watch_terminal['terminal'] is True


def test_fake_provider_can_emit_deterministic_local_markdown_reply(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-fake-md'
    ctx = _bootstrap_test_project(project_root)
    layout = PathLayout(project_root)
    config = _fake_config()
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('demo', project_id=ctx.project_id, layout=layout, pid=201))
    provider_catalog = build_default_provider_catalog()
    dispatcher = JobDispatcher(
        layout,
        config,
        registry,
        execution_service=ExecutionService(
            build_default_execution_registry(),
            clock=StepClock(
                *(['2026-03-18T00:00:00Z'] * 5),
                '2026-03-18T00:00:00.100000Z',
                '2026-03-18T00:00:00.200000Z',
            ),
            state_store=ExecutionStateStore(layout),
        ),
        completion_tracker=CompletionTrackerService(config, provider_catalog),
        provider_catalog=provider_catalog,
        clock=StepClock(
            *(['2026-03-18T00:00:00Z'] * 5),
            '2026-03-18T00:00:00.100000Z',
            '2026-03-18T00:00:00.200000Z',
        ),
    )

    receipt = dispatcher.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent='demo',
            from_actor='user',
            body='ccb-local-md:matrix',
            task_id=None,
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )

    job_id = receipt.jobs[0].job_id
    dispatcher.tick()
    dispatcher.poll_completions()
    completed = dispatcher.poll_completions()

    assert len(completed) == 1
    snapshot = dispatcher.get_snapshot(job_id)
    assert snapshot is not None
    reply = snapshot.latest_decision.reply
    assert reply is not None
    assert reply.startswith('# CCB Local Markdown matrix')
    assert '`ccb-local-reply:matrix`' in reply
    assert '```text' in reply
    assert '[blocked local link]' in reply


def test_dispatcher_single_target_submit_keeps_stopped_agent_queued_until_tick(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    ctx = _bootstrap_test_project(project_root)
    layout = PathLayout(project_root)
    config = _provider_config('codex')
    registry = AgentRegistry(layout, config)
    runtime = _runtime('codex', project_id=ctx.project_id, layout=layout, pid=101)
    runtime.state = AgentState.STOPPED
    runtime.health = 'stopped'
    registry.upsert(runtime)

    from agents.models import AgentRestoreState, RestoreMode, RestoreStatus
    from agents.store import AgentRestoreStore

    restore_store = AgentRestoreStore(layout)
    restore_store.save(
        'codex',
        AgentRestoreState(
            restore_mode=RestoreMode.AUTO,
            last_checkpoint='checkpoint-1',
            conversation_summary='resume me',
            open_tasks=['continue'],
            files_touched=['README.md'],
            last_restore_status=RestoreStatus.CHECKPOINT,
        ),
    )
    runtime_service = RuntimeService(layout, registry, ctx.project_id, restore_store, clock=lambda: '2026-03-18T00:00:00Z')
    dispatcher = JobDispatcher(layout, config, registry, runtime_service=runtime_service, clock=lambda: '2026-03-18T00:00:00Z')

    receipt = dispatcher.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent='codex',
            from_actor='user',
            body='hello',
            task_id=None,
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    assert receipt.jobs[0].agent_name == 'codex'
    runtime = registry.get('codex')
    assert runtime is not None
    assert runtime.state is AgentState.STOPPED
    assert runtime.health == 'stopped'

    started = dispatcher.tick()
    assert len(started) == 1
    runtime = registry.get('codex')
    assert runtime is not None
    assert runtime.state is AgentState.BUSY
    assert runtime.health == 'restored'


def test_dispatcher_single_target_submit_keeps_failed_agent_queued_until_tick(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-failed'
    ctx = _bootstrap_test_project(project_root)
    layout = PathLayout(project_root)
    config = _provider_config('codex')
    registry = AgentRegistry(layout, config)
    runtime = _runtime('codex', project_id=ctx.project_id, layout=layout, pid=101)
    runtime.state = AgentState.FAILED
    runtime.health = 'failed'
    registry.upsert(runtime)

    from agents.models import AgentRestoreState, RestoreMode, RestoreStatus
    from agents.store import AgentRestoreStore

    restore_store = AgentRestoreStore(layout)
    restore_store.save(
        'codex',
        AgentRestoreState(
            restore_mode=RestoreMode.AUTO,
            last_checkpoint='checkpoint-1',
            conversation_summary='resume me',
            open_tasks=['continue'],
            files_touched=['README.md'],
            last_restore_status=RestoreStatus.CHECKPOINT,
        ),
    )
    runtime_service = RuntimeService(layout, registry, ctx.project_id, restore_store, clock=lambda: '2026-03-18T00:00:00Z')
    dispatcher = JobDispatcher(layout, config, registry, runtime_service=runtime_service, clock=lambda: '2026-03-18T00:00:00Z')

    receipt = dispatcher.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent='codex',
            from_actor='user',
            body='hello',
            task_id=None,
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    assert receipt.jobs[0].agent_name == 'codex'
    runtime = registry.get('codex')
    assert runtime is not None
    assert runtime.state is AgentState.FAILED
    assert runtime.health == 'failed'

    started = dispatcher.tick()
    assert len(started) == 1
    runtime = registry.get('codex')
    assert runtime is not None
    assert runtime.state is AgentState.BUSY
    assert runtime.health == 'restored'


def test_dispatcher_single_target_submit_with_missing_runtime_and_restore_state_starts_via_tick_handoff(
    tmp_path: Path,
) -> None:
    project_root = tmp_path / 'repo-missing-runtime'
    ctx = _bootstrap_test_project(project_root)
    layout = PathLayout(project_root)
    config = _provider_config('codex')
    registry = AgentRegistry(layout, config)

    from agents.models import AgentRestoreState, RestoreMode, RestoreStatus
    from agents.store import AgentRestoreStore

    restore_store = AgentRestoreStore(layout)
    restore_store.save(
        'codex',
        AgentRestoreState(
            restore_mode=RestoreMode.AUTO,
            last_checkpoint='checkpoint-1',
            conversation_summary='resume me',
            open_tasks=['continue'],
            files_touched=['README.md'],
            last_restore_status=RestoreStatus.CHECKPOINT,
        ),
    )
    runtime_service = RuntimeService(layout, registry, ctx.project_id, restore_store, clock=lambda: '2026-03-18T00:00:00Z')
    dispatcher = JobDispatcher(layout, config, registry, runtime_service=runtime_service, clock=lambda: '2026-03-18T00:00:00Z')

    receipt = dispatcher.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent='codex',
            from_actor='user',
            body='hello',
            task_id=None,
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )

    assert receipt.jobs[0].agent_name == 'codex'
    assert registry.get('codex') is None

    started = dispatcher.tick()
    assert len(started) == 1
    runtime = registry.get('codex')
    assert runtime is not None
    assert runtime.state is AgentState.BUSY
    assert runtime.health == 'restored'


def test_dispatcher_restore_running_jobs_marks_unrecoverable_execution_incomplete(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-restore-fail'
    ctx = _bootstrap_test_project(project_root)
    layout = PathLayout(project_root)
    config = _fake_config()
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('demo', project_id=ctx.project_id, layout=layout, pid=201))
    clock = StepClock(
        '2026-03-18T00:00:00Z',
        '2026-03-18T00:00:00Z',
        '2026-03-18T00:00:00Z',
        '2026-03-18T00:00:00Z',
        '2026-03-18T00:00:00Z',
        '2026-03-18T00:00:01Z',
    )
    execution_service = ExecutionService(
        build_default_execution_registry(),
        clock=clock,
        state_store=ExecutionStateStore(layout),
    )
    dispatcher = JobDispatcher(layout, config, registry, execution_service=execution_service, clock=clock)

    receipt = dispatcher.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent='demo',
            from_actor='user',
            body='restore me',
            task_id='fake;latency_ms=1500',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    job_id = receipt.jobs[0].job_id
    dispatcher.tick()
    running = dispatcher.get(job_id)
    assert running is not None
    assert running.status is JobStatus.RUNNING
    assert layout.execution_state_path(job_id).exists()

    restarted = JobDispatcher(
        layout,
        config,
        registry,
        execution_service=FailingRestoreExecutionService(),
        clock=lambda: '2026-03-18T00:00:05Z',
    )
    completed = restarted.restore_running_jobs()
    assert len(completed) == 1
    terminal = restarted.get(job_id)
    assert terminal is not None
    assert terminal.status is JobStatus.INCOMPLETE
    assert terminal.terminal_decision is not None
    assert terminal.terminal_decision['reason'] == 'ccbd_restart_requires_resubmit'

    watched = restarted.watch(job_id, start_line=0)
    event_types = [event['type'] for event in watched['events']]
    assert 'execution_restore_failed' in event_types
    assert watched['terminal'] is True


def test_dispatcher_terminate_nonterminal_jobs_prevents_retry_and_restart_restore(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-stop-terminal'
    ctx = _bootstrap_test_project(project_root)
    layout = PathLayout(project_root)
    config = _fake_config()
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('demo', project_id=ctx.project_id, layout=layout, pid=301))
    clock = StepClock(
        '2026-03-18T00:00:00Z',
        '2026-03-18T00:00:00Z',
        '2026-03-18T00:00:00Z',
        '2026-03-18T00:00:00Z',
        '2026-03-18T00:00:00Z',
        '2026-03-18T00:00:01Z',
        '2026-03-18T00:00:02Z',
    )
    execution_service = ExecutionService(
        build_default_execution_registry(),
        clock=clock,
        state_store=ExecutionStateStore(layout),
    )
    dispatcher = JobDispatcher(layout, config, registry, execution_service=execution_service, clock=clock)

    receipt = dispatcher.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent='demo',
            from_actor='user',
            body='stop me',
            task_id='fake;latency_ms=1500',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    job_id = receipt.jobs[0].job_id
    dispatcher.tick()
    running = dispatcher.get(job_id)
    assert running is not None
    assert running.status is JobStatus.RUNNING
    assert layout.execution_state_path(job_id).exists()

    terminated = dispatcher.terminate_nonterminal_jobs(shutdown_reason='kill', forced=True)
    assert len(terminated) == 1

    terminal = dispatcher.get(job_id)
    assert terminal is not None
    assert terminal.status is JobStatus.INCOMPLETE
    assert terminal.terminal_decision is not None
    assert terminal.terminal_decision['reason'] == 'project_shutdown'
    assert terminal.terminal_decision['diagnostics']['shutdown_reason'] == 'kill'
    assert terminal.terminal_decision['diagnostics']['forced'] is True
    assert not layout.execution_state_path(job_id).exists()

    watched = dispatcher.watch(job_id, start_line=0)
    event_types = [event['type'] for event in watched['events']]
    assert 'job_retry_scheduled' not in event_types
    assert watched['terminal'] is True

    restarted = JobDispatcher(
        layout,
        config,
        registry,
        execution_service=ExecutionService(
            build_default_execution_registry(),
            clock=lambda: '2026-03-18T00:00:05Z',
            state_store=ExecutionStateStore(layout),
        ),
        clock=lambda: '2026-03-18T00:00:05Z',
    )
    assert restarted.restore_running_jobs() == ()


def test_dispatcher_startup_cleans_execution_state_for_terminal_jobs(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-stale-execution-terminal'
    ctx = _bootstrap_test_project(project_root)
    layout = PathLayout(project_root)
    config = _fake_config()
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('demo', project_id=ctx.project_id, layout=layout, pid=301))
    state_store = ExecutionStateStore(layout)
    execution_service = ExecutionService(
        build_default_execution_registry(),
        clock=lambda: '2026-03-18T00:00:00Z',
        state_store=state_store,
    )
    dispatcher = JobDispatcher(layout, config, registry, execution_service=execution_service)

    receipt = dispatcher.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent='demo',
            from_actor='user',
            body='stale terminal execution',
            task_id='fake;latency_ms=1500',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    job_id = receipt.jobs[0].job_id
    dispatcher.tick()
    assert state_store.load(job_id) is not None

    dispatcher.cancel(job_id)
    assert state_store.load(job_id) is None

    execution_service.start(dispatcher.get(job_id))
    assert state_store.load(job_id) is not None

    JobDispatcher(
        layout,
        config,
        registry,
        execution_service=execution_service,
        clock=lambda: '2026-03-18T00:00:01Z',
    )

    assert state_store.load(job_id) is None
    assert state_store.summary()['active_execution_count'] == 0


def test_dispatcher_poll_finishes_late_update_for_terminal_job(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-late-terminal-update'
    ctx = _bootstrap_test_project(project_root)
    layout = PathLayout(project_root)
    config = _fake_config()
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('demo', project_id=ctx.project_id, layout=layout, pid=401))
    state_store = ExecutionStateStore(layout)
    execution_service = ExecutionService(
        build_default_execution_registry(),
        clock=lambda: '2026-03-18T00:00:00Z',
        state_store=state_store,
    )
    dispatcher = JobDispatcher(layout, config, registry, execution_service=execution_service)

    receipt = dispatcher.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent='demo',
            from_actor='user',
            body='late provider update',
            task_id='fake;latency_ms=1500',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    job_id = receipt.jobs[0].job_id
    dispatcher.tick()
    dispatcher.cancel(job_id)

    execution_service.start(dispatcher.get(job_id))
    assert state_store.load(job_id) is not None
    late_service = LateUpdateExecutionService(state_store, ExecutionUpdate(job_id=job_id, items=(), decision=None))
    dispatcher._execution_service = late_service

    assert dispatcher.poll_completions() == ()
    assert late_service.finished == [job_id]
    assert state_store.load(job_id) is None


def test_dispatcher_broadcast_does_not_lazy_restore_offline_agents(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    ctx = _bootstrap_test_project(project_root)
    layout = PathLayout(project_root)
    config = _provider_config('codex', 'claude')
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('codex', project_id=ctx.project_id, layout=layout, pid=101))
    offline = _runtime('claude', project_id=ctx.project_id, layout=layout, pid=102)
    offline.state = AgentState.STOPPED
    offline.health = 'stopped'
    registry.upsert(offline)
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: '2026-03-18T00:00:00Z')

    receipt = dispatcher.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent='all',
            from_actor='system',
            body='broadcast',
            task_id=None,
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.BROADCAST,
        )
    )
    assert [job.agent_name for job in receipt.jobs] == ['codex']


def test_dispatcher_single_target_submit_without_restore_state_starts_via_tick_handoff(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-no-restore-state'
    ctx = _bootstrap_test_project(project_root)
    layout = PathLayout(project_root)
    config = _provider_config('codex')
    registry = AgentRegistry(layout, config)
    runtime = _runtime('codex', project_id=ctx.project_id, layout=layout, pid=101)
    runtime.state = AgentState.STOPPED
    runtime.health = 'stopped'
    registry.upsert(runtime)

    runtime_service = RuntimeService(layout, registry, ctx.project_id, clock=lambda: '2026-03-18T00:00:00Z')
    execution_service = RecordingExecutionService()
    dispatcher = JobDispatcher(
        layout,
        config,
        registry,
        runtime_service=runtime_service,
        execution_service=execution_service,
        clock=lambda: '2026-03-18T00:00:00Z',
    )

    receipt = dispatcher.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent='codex',
            from_actor='user',
            body='hello',
            task_id=None,
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )

    assert receipt.jobs[0].agent_name == 'codex'
    queued = dispatcher.get(receipt.jobs[0].job_id)
    assert queued is not None
    assert queued.status is JobStatus.ACCEPTED

    started = dispatcher.tick()
    assert len(started) == 1
    assert started[0].job_id == receipt.jobs[0].job_id
    assert len(execution_service.calls) == 1
    runtime = registry.get('codex')
    assert runtime is not None
    assert runtime.state is AgentState.BUSY
    assert runtime.health == 'healthy'


def test_auto_retry_skips_non_retryable_provider_error_kind(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-auto-retry-nonretryable'
    ctx = _bootstrap_test_project(project_root)
    layout = PathLayout(project_root)
    config = _fake_config(provider='fake')
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('demo', project_id=ctx.project_id, layout=layout, pid=101))
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: '2026-03-18T00:00:00Z')

    receipt = dispatcher.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent='demo',
            from_actor='user',
            body='hello',
            task_id='task-1',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    job_id = receipt.jobs[0].job_id
    dispatcher.tick()

    decision = CompletionDecision(
        terminal=True,
        status=CompletionStatus.FAILED,
        reason='api_error',
        reply='',
        confidence=CompletionConfidence.EXACT,
        anchor_seen=False,
        reply_started=False,
        reply_stable=False,
        provider_turn_ref=None,
        source_cursor=CompletionCursor(source_kind=CompletionSourceKind.PROTOCOL_EVENT_STREAM),
        diagnostics={'error_kind': 'provider_usage_limit', 'retry_after': '2026-07-01T01:00:00Z'},
        finished_at='2026-03-18T00:00:01Z',
    )
    dispatcher.complete(job_id, decision)

    # No automatic retry should be scheduled; the job remains the only one.
    latest = dispatcher.latest_for_agent('demo')
    assert latest is not None
    assert latest.job_id == job_id
    assert latest.status is JobStatus.FAILED


def test_auto_retry_skips_non_retryable_provider_error_kind_on_incomplete(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-auto-retry-nonretryable-incomplete'
    ctx = _bootstrap_test_project(project_root)
    layout = PathLayout(project_root)
    config = _fake_config(provider='fake')
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('demo', project_id=ctx.project_id, layout=layout, pid=101))
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: '2026-03-18T00:00:00Z')

    receipt = dispatcher.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent='demo',
            from_actor='user',
            body='hello',
            task_id='task-1',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    job_id = receipt.jobs[0].job_id
    dispatcher.tick()

    decision = CompletionDecision(
        terminal=True,
        status=CompletionStatus.INCOMPLETE,
        reason='model_empty_output',
        reply='',
        confidence=CompletionConfidence.EXACT,
        anchor_seen=False,
        reply_started=False,
        reply_stable=False,
        provider_turn_ref=None,
        source_cursor=CompletionCursor(source_kind=CompletionSourceKind.PROTOCOL_EVENT_STREAM),
        diagnostics={'error_kind': 'provider_usage_limit', 'retry_after': '2026-07-01T01:00:00Z'},
        finished_at='2026-03-18T00:00:01Z',
    )
    dispatcher.complete(job_id, decision)

    # No automatic retry should be scheduled; the INCOMPLETE job remains.
    latest = dispatcher.latest_for_agent('demo')
    assert latest is not None
    assert latest.job_id == job_id
    assert latest.status is JobStatus.INCOMPLETE
    # The usage-limit signal should degrade the provider bucket immediately.
    assert dispatcher._quota_buckets.is_degraded('fake') is True
