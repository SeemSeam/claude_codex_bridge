from __future__ import annotations

from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

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
    WindowSpec,
    ToolWindowSpec,
    WorkspaceMode,
)
from ccbd.api_models import DeliveryScope, JobRecord, JobStatus, MessageEnvelope, TargetKind
from ccbd.models import MountState
from ccbd.project_view import (
    AgentActivityFacts,
    ProjectViewDependencies,
    ProjectViewSequenceCache,
    ProjectViewService,
    ProjectViewStateStore,
    resolve_agent_activity,
)
import ccbd.project_view.service as project_view_service
from ccbd.reload_drain import DrainIntent, DrainQueueStore, plan_drain_transition
from ccbd.services.dispatcher import JobDispatcher
from ccbd.services.mount import MountManager
from ccbd.services.project_namespace import ProjectNamespaceController
from ccbd.services.project_namespace_state import ProjectNamespaceState, ProjectNamespaceStateStore
from ccbd.services.registry import AgentRegistry
from completion.models import CompletionConfidence, CompletionDecision, CompletionStatus
from message_bureau import CallbackEdgeStore, CallbackEdgeState
from message_bureau.models import AttemptRecord, AttemptState, ReplyRecord, ReplyTerminalStatus
from project.ids import compute_project_id
from provider_hooks.activity import load_activity, write_activity
from rust_helpers import RUST_HELPER_BIN_ENV
from rust_helpers_project_view import RUST_PROJECT_VIEW_ENV
from storage.paths import PathLayout


NOW = '2026-05-20T12:00:00Z'


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def _write_helper(path: Path, body: str) -> Path:
    path.write_text('#!/usr/bin/env python3\n' + body, encoding='utf-8')
    path.chmod(0o755)
    return path


def _spec(name: str, provider: str) -> AgentSpec:
    return AgentSpec(
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


def _runtime(agent_name: str, *, project_id: str, state: AgentState = AgentState.IDLE, health: str = 'healthy') -> AgentRuntime:
    return AgentRuntime(
        agent_name=agent_name,
        state=state,
        pid=100,
        started_at=NOW,
        last_seen_at=NOW,
        runtime_ref=f'tmux:%{agent_name}',
        session_ref=f'{agent_name}-session',
        workspace_path='/tmp/workspace',
        project_id=project_id,
        backend_type='tmux',
        queue_depth=0,
        socket_path=None,
        health=health,
        provider=None,
        pane_id=f'%{agent_name[-1]}',
        pane_state='alive',
        reconcile_state='steady',
    )


def _config() -> ProjectConfig:
    agents = {
        'agent1': _spec('agent1', 'codex'),
        'agent2': _spec('agent2', 'claude'),
        'agent3': _spec('agent3', 'codex'),
    }
    return ProjectConfig(
        version=2,
        default_agents=('agent1', 'agent2', 'agent3'),
        agents=agents,
        cmd_enabled=False,
        layout_spec='agent1:codex, agent2:claude',
        windows=(
            WindowSpec(name='main', order=0, layout_spec='agent1:codex, agent2:claude', agent_names=('agent1', 'agent2')),
            WindowSpec(name='ops', order=1, layout_spec='agent3:codex', agent_names=('agent3',)),
        ),
        entry_window='main',
    )


def _config_with_tool_window() -> ProjectConfig:
    base = _config()
    return ProjectConfig(
        version=2,
        default_agents=base.default_agents,
        agents=base.agents,
        cmd_enabled=False,
        layout_spec=base.layout_spec,
        windows=base.windows,
        tool_windows=(ToolWindowSpec(name='files', order=0, command='ccb-workbench files'),),
        entry_window='main',
    )


def _message(project_id: str, *, sender: str, target: str) -> MessageEnvelope:
    return MessageEnvelope(
        project_id=project_id,
        to_agent=target,
        from_actor=sender,
        body='work',
        task_id=None,
        reply_to='agent1' if sender != 'user' else None,
        message_type='ask',
        delivery_scope=DeliveryScope.SINGLE,
    )


def _reply_delivery_message(
    project_id: str,
    *,
    source_agent: str,
    target: str,
    source_job_id: str,
    reply_id: str = 'reply_1',
    body: str | None = None,
) -> MessageEnvelope:
    return MessageEnvelope(
        project_id=project_id,
        to_agent=target,
        from_actor='system',
        body=body if body is not None else f'CCB_REPLY from={source_agent} reply={reply_id} status=completed job={source_job_id}\n\nOK',
        task_id=f'reply:{reply_id}',
        reply_to=None,
        message_type='reply_delivery',
        delivery_scope=DeliveryScope.SINGLE,
    )


def _submit(dispatcher: JobDispatcher, project_id: str, *, sender: str, target: str, body: str = 'work') -> str:
    receipt = dispatcher.submit(
        MessageEnvelope(
            project_id=project_id,
            to_agent=target,
            from_actor=sender,
            body=body,
            task_id=None,
            reply_to='agent1' if sender != 'user' else None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    return receipt.jobs[0].job_id


def _job(
    project_id: str,
    *,
    job_id: str,
    sender: str,
    target: str,
    status: JobStatus,
    updated_at: str = NOW,
    terminal_reason: str | None = None,
    body: str = 'work',
    silence_on_success: bool = False,
    route_options: dict[str, object] | None = None,
) -> JobRecord:
    terminal_decision = None
    if status in {JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.INCOMPLETE}:
        terminal_decision = {'reason': terminal_reason or status.value}
    elif status is JobStatus.COMPLETED:
        terminal_decision = {'reason': terminal_reason or 'task_complete'}
    return JobRecord(
        job_id=job_id,
        submission_id=None,
        agent_name=target,
        provider='codex',
        request=replace(
            _message(project_id, sender=sender, target=target),
            body=body,
            silence_on_success=silence_on_success,
            route_options=dict(route_options or {}),
        ),
        status=status,
        terminal_decision=terminal_decision,
        cancel_requested_at=None,
        created_at='2026-05-20T11:59:00Z',
        updated_at=updated_at,
        target_kind=TargetKind.AGENT,
        target_name=target,
    )


def _reply_delivery_job(
    project_id: str,
    *,
    job_id: str,
    source_agent: str,
    source_job_id: str,
    target: str,
    status: JobStatus,
    updated_at: str = NOW,
    reply_id: str = 'reply_1',
    body: str | None = None,
) -> JobRecord:
    terminal_decision = None
    if status in {JobStatus.FAILED, JobStatus.CANCELLED, JobStatus.INCOMPLETE}:
        terminal_decision = {'reason': status.value}
    elif status is JobStatus.COMPLETED:
        terminal_decision = {'reason': 'task_complete'}
    return JobRecord(
        job_id=job_id,
        submission_id=None,
        agent_name=target,
        provider='codex',
        request=_reply_delivery_message(
            project_id,
            source_agent=source_agent,
            target=target,
            source_job_id=source_job_id,
            reply_id=reply_id,
            body=body,
        ),
        status=status,
        terminal_decision=terminal_decision,
        cancel_requested_at=None,
        created_at='2026-05-20T12:00:01Z',
        updated_at=updated_at,
        target_kind=TargetKind.AGENT,
        target_name=target,
        provider_options={'reply_delivery': True, 'reply_delivery_reply_id': reply_id},
    )


def _record_reply_for_source(dispatcher: JobDispatcher, source: JobRecord, *, reply_id: str) -> None:
    attempt_id = f'att_{source.job_id}'
    message_id = f'msg_{source.job_id}'
    dispatcher._message_bureau_control._attempt_store.append(
        AttemptRecord(
            attempt_id=attempt_id,
            message_id=message_id,
            agent_name=source.agent_name,
            provider=source.provider,
            job_id=source.job_id,
            retry_index=0,
            health_snapshot_ref=None,
            started_at=source.created_at,
            updated_at=source.updated_at,
            attempt_state=AttemptState.COMPLETED,
        )
    )
    dispatcher._message_bureau_control._reply_store.append(
        ReplyRecord(
            reply_id=reply_id,
            message_id=message_id,
            attempt_id=attempt_id,
            agent_name=source.agent_name,
            terminal_status=ReplyTerminalStatus.COMPLETED,
            reply='OK',
            diagnostics={},
            finished_at=source.updated_at,
        )
    )


def _decision(*, reply: str = 'done', status: CompletionStatus = CompletionStatus.COMPLETED) -> CompletionDecision:
    return CompletionDecision(
        terminal=True,
        status=status,
        reason='task_complete' if status is CompletionStatus.COMPLETED else status.value,
        confidence=CompletionConfidence.EXACT,
        reply=reply,
        anchor_seen=True,
        reply_started=True,
        reply_stable=True,
        provider_turn_ref='turn-1',
        source_cursor=None,
        finished_at=NOW,
        diagnostics={},
    )


def _project_view_service(
    *,
    project_root: Path,
    project_id: str,
    layout: PathLayout,
    config: ProjectConfig,
    registry: AgentRegistry,
    dispatcher: JobDispatcher,
) -> ProjectViewService:
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(
        project_id=project_id,
        pid=123,
        socket_path=layout.ccbd_socket_path,
        generation=7,
        started_at=NOW,
    )
    return ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            paths=layout,
            clock=lambda: NOW,
        )
    )


def _write_active_unload_drain(layout: PathLayout, agent_name: str):
    store = DrainQueueStore(layout)
    intent = DrainIntent(
        intent_id=f'drain-test-{agent_name}',
        intent_kind='unload',
        agent_name=agent_name,
        created_at_s=10.0,
        reason='test busy unload',
    )
    result = store.load().enqueue(intent, now_s=10.0)
    record = plan_drain_transition(result.record, now_s=10.0, is_busy=lambda _record: True)
    store.save(result.queue.replace_record(record))
    return record


def test_project_view_exposes_active_reload_drains(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-drain-view'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    for agent_name in config.agents:
        registry.upsert(_runtime(agent_name, project_id=project_id))
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    _write_active_unload_drain(layout, 'agent2')

    response = _project_view_service(
        project_root=project_root,
        project_id=project_id,
        layout=layout,
        config=config,
        registry=registry,
        dispatcher=dispatcher,
    ).build_response()
    view = response['view']

    assert view['reload_drains']['active_count'] == 1
    assert view['reload_drains']['retry_command'] == 'ccb reload'
    assert view['reload_drains']['active_records'][0]['agent'] == 'agent2'
    agent1 = next(agent for agent in view['agents'] if agent['name'] == 'agent1')
    agent2 = next(agent for agent in view['agents'] if agent['name'] == 'agent2')
    assert agent1['reload_drain'] is None
    assert agent1['dispatch_blocked_by_reload_drain'] is False
    assert agent2['dispatch_blocked_by_reload_drain'] is True
    assert agent2['reload_drain']['intent_kind'] == 'unload'
    assert agent2['reload_drain']['phase'] == 'draining'
    assert agent2['reload_drain']['status'] == 'waiting'


def test_project_view_cache_invalidates_when_reload_drain_file_changes(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-drain-cache'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    for agent_name in config.agents:
        registry.upsert(_runtime(agent_name, project_id=project_id))
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(
        project_id=project_id,
        pid=123,
        socket_path=layout.ccbd_socket_path,
        generation=7,
        started_at=NOW,
    )
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            paths=layout,
            clock=lambda: NOW,
            cache_ttl_ms=60000,
        )
    )

    first = service.build_response()
    _write_active_unload_drain(layout, 'agent2')
    second = service.build_response()

    assert first['view']['reload_drains']['active_count'] == 0
    assert second is not first
    assert second['view']['reload_drains']['active_count'] == 1
    assert second['view']['agents'][1]['dispatch_blocked_by_reload_drain'] is True


def test_project_view_ignores_codex_provider_activity_without_ccb_job(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-provider-activity'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    runtime = _runtime('agent1', project_id=project_id)
    runtime.session_id = 'codex-session-1'
    registry.upsert(runtime)
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    write_activity(
        provider='codex',
        project_id=project_id,
        agent_name='agent1',
        runtime_dir=layout.agent_provider_runtime_dir('agent1', 'codex'),
        state='active',
        source='codex_hook',
        event_name='UserPromptSubmit',
        ccb_session_id='ccb-agent1-launch',
        provider_session_id='codex-session-1',
        pane_id='%1',
        workspace_path='/tmp/workspace',
        updated_at=NOW,
    )

    response = _project_view_service(
        project_root=project_root,
        project_id=project_id,
        layout=layout,
        config=config,
        registry=registry,
        dispatcher=dispatcher,
    ).build_response()

    agent = response['view']['agents'][0]
    assert agent['activity_state'] == 'idle'
    assert agent['activity_source'] == 'pane_liveness'
    assert agent['activity_reason'] == 'pane_alive'
    assert 'provider_runtime_status' not in agent


def test_project_view_ignores_codex_provider_activity_failed_for_running_job(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-provider-failed'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    runtime = _runtime('agent1', project_id=project_id)
    runtime.session_id = 'ccb-agent1-session'
    registry.upsert(runtime)
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    running = _job(project_id, job_id='job_running_1234', sender='user', target='agent1', status=JobStatus.RUNNING)
    dispatcher._append_job(running)
    dispatcher._state.mark_active_for(TargetKind.AGENT, 'agent1', running.job_id)
    write_activity(
        provider='codex',
        project_id=project_id,
        agent_name='agent1',
        runtime_dir=layout.agent_provider_runtime_dir('agent1', 'codex'),
        state='failed',
        source='codex_hook',
        event_name='Stop',
        ccb_session_id='ccb-agent1-session',
        pane_id='%1',
        workspace_path='/tmp/workspace',
        diagnostics={'reason': 'api_error'},
        updated_at=NOW,
    )

    response = _project_view_service(
        project_root=project_root,
        project_id=project_id,
        layout=layout,
        config=config,
        registry=registry,
        dispatcher=dispatcher,
    ).build_response()

    agent = response['view']['agents'][0]
    assert agent['activity_state'] == 'active'
    assert agent['activity_source'] == 'ccb_job'
    assert agent['activity_reason'] == 'job_running'
    assert agent['current_job_id'] == 'job_running_1234'


def test_project_view_ignores_provider_activity_for_wrong_pane(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-provider-wrong-pane'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    runtime = _runtime('agent1', project_id=project_id)
    runtime.session_id = 'ccb-agent1-session'
    registry.upsert(runtime)
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    write_activity(
        provider='codex',
        project_id=project_id,
        agent_name='agent1',
        runtime_dir=layout.agent_provider_runtime_dir('agent1', 'codex'),
        state='active',
        source='codex_hook',
        ccb_session_id='ccb-agent1-session',
        pane_id='%99',
        workspace_path='/tmp/workspace',
        updated_at=NOW,
    )

    response = _project_view_service(
        project_root=project_root,
        project_id=project_id,
        layout=layout,
        config=config,
        registry=registry,
        dispatcher=dispatcher,
    ).build_response()

    agent = response['view']['agents'][0]
    assert agent['activity_state'] == 'idle'
    assert agent['activity_source'] == 'pane_liveness'


def test_project_view_claude_activity_includes_pane_probe(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-provider-claude-pane-probe'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    runtime = _runtime('agent2', project_id=project_id)
    runtime.session_id = 'claude-session-1'
    registry.upsert(runtime)
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    ProjectNamespaceStateStore(layout).save(
        ProjectNamespaceState(
            project_id=project_id,
            namespace_epoch=3,
            tmux_socket_path=str(layout.ccbd_tmux_socket_path),
            tmux_session_name='ccb-provider-claude-pane-probe',
            layout_version=2,
        )
    )
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    write_activity(
        provider='claude',
        project_id=project_id,
        agent_name='agent2',
        runtime_dir=layout.agent_provider_runtime_dir('agent2', 'claude'),
        state='active',
        source='claude_hook',
        ccb_session_id='ccb-agent2-session',
        pane_id='%2',
        workspace_path='/tmp/workspace',
        updated_at=NOW,
    )
    backend = _SnapshotBackend()
    controller = ProjectNamespaceController(
        layout,
        project_id,
        backend_factory=lambda socket_path=None: backend,
    )

    response = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            namespace_controller=controller,
            paths=layout,
            clock=lambda: NOW,
        )
    ).build_response()

    agent = response['view']['agents'][1]
    assert agent['activity_source'] == 'claude_runtime'
    assert agent['activity_reason'] == 'claude_activity_active'
    assert agent['provider_runtime_status']['state'] == 'working'
    assert agent['provider_runtime_status']['source'] == 'activity'
    assert agent['provider_runtime_status']['pane_state'] == 'unknown'
    assert [args for args in backend.calls if args[:3] == ['capture-pane', '-p', '-t']] == [
        ['capture-pane', '-p', '-t', '%2', '-S', '-30']
    ]


def test_project_view_claude_runtime_status_reads_bound_session_with_pane_probe(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-claude-session-runtime'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    runtime = _runtime('agent2', project_id=project_id)
    transcript = layout.agent_provider_runtime_dir('agent2', 'claude') / 'session.jsonl'
    _write(
        transcript,
        json.dumps(
            {
                'type': 'assistant',
                'message': {
                    'role': 'assistant',
                    'stop_reason': 'end_turn',
                    'content': [{'type': 'text', 'text': 'done'}],
                },
            }
        )
        + '\n',
    )
    session_file = tmp_path / '.claude-session-agent2'
    _write(session_file, json.dumps({'claude_session_path': str(transcript)}))
    runtime.session_file = str(session_file)
    registry.upsert(runtime)
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    ProjectNamespaceStateStore(layout).save(
        ProjectNamespaceState(
            project_id=project_id,
            namespace_epoch=3,
            tmux_socket_path=str(layout.ccbd_tmux_socket_path),
            tmux_session_name='ccb-claude-session-runtime',
            layout_version=2,
        )
    )
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    backend = _SnapshotBackend()
    controller = ProjectNamespaceController(
        layout,
        project_id,
        backend_factory=lambda socket_path=None: backend,
    )

    response = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            namespace_controller=controller,
            paths=layout,
            clock=lambda: NOW,
        )
    ).build_response()

    agent = response['view']['agents'][1]
    assert agent['activity_state'] == 'idle'
    assert agent['activity_source'] == 'claude_runtime'
    assert agent['activity_reason'] == 'claude_session_assistant_end_turn'
    assert agent['activity_symbol'] == '◇'
    assert agent['provider_runtime_status']['state'] == 'free'
    assert agent['provider_runtime_status']['source'] == 'session'
    assert agent['provider_runtime_status']['session_state'] == 'free'
    assert agent['provider_runtime_status']['pane_state'] == 'unknown'
    assert [args for args in backend.calls if args[:3] == ['capture-pane', '-p', '-t']] == [
        ['capture-pane', '-p', '-t', '%2', '-S', '-30']
    ]


def test_project_view_claude_pane_active_overrides_idle_activity(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-claude-pane-active'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    runtime = _runtime('agent2', project_id=project_id)
    registry.upsert(runtime)
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    ProjectNamespaceStateStore(layout).save(
        ProjectNamespaceState(
            project_id=project_id,
            namespace_epoch=3,
            tmux_socket_path=str(layout.ccbd_tmux_socket_path),
            tmux_session_name='ccb-claude-pane-active',
            layout_version=2,
        )
    )
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    write_activity(
        provider='claude',
        project_id=project_id,
        agent_name='agent2',
        runtime_dir=layout.agent_provider_runtime_dir('agent2', 'claude'),
        state='idle',
        source='claude_hook',
        event_name='Stop',
        pane_id='%2',
        workspace_path='/tmp/workspace',
        updated_at=NOW,
    )

    class ClaudePaneActiveBackend(_SnapshotBackend):
        def _tmux_run(self, args: list[str], *, capture=False, check=False, timeout=None):
            self.calls.append(list(args))
            if args[:3] == ['capture-pane', '-p', '-t']:
                return type(
                    'CP',
                    (),
                    {
                        'returncode': 0,
                        'stdout': '● Thinking for 9s, running 1 shell command…\n\n❯\n',
                        'stderr': '',
                    },
                )()
            return self._snapshot_run(args, capture=capture, check=check, timeout=timeout)

    backend = ClaudePaneActiveBackend()
    controller = ProjectNamespaceController(
        layout,
        project_id,
        backend_factory=lambda socket_path=None: backend,
    )

    response = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            namespace_controller=controller,
            paths=layout,
            clock=lambda: NOW,
            cache_ttl_ms=0,
        )
    ).build_response()

    agent = response['view']['agents'][1]
    assert agent['activity_state'] == 'active'
    assert agent['activity_source'] == 'claude_runtime'
    assert agent['activity_reason'] == 'claude_pane_tool_running'
    assert agent['provider_runtime_status']['state'] == 'tool_running'
    assert agent['provider_runtime_status']['source'] == 'pane'
    assert agent['provider_runtime_status']['activity_state'] == 'idle'
    assert agent['provider_runtime_status']['pane_state'] == 'tool_running'


def test_project_view_claude_stale_active_terminal_summary_becomes_free(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-claude-pane-terminal-summary'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    runtime = _runtime('agent2', project_id=project_id)
    registry.upsert(runtime)
    current_time = ['2026-05-20T12:00:00Z']
    mount_manager = MountManager(layout, clock=lambda: current_time[0])
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    ProjectNamespaceStateStore(layout).save(
        ProjectNamespaceState(
            project_id=project_id,
            namespace_epoch=3,
            tmux_socket_path=str(layout.ccbd_tmux_socket_path),
            tmux_session_name='ccb-claude-pane-terminal-summary',
            layout_version=2,
        )
    )
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: current_time[0])
    write_activity(
        provider='claude',
        project_id=project_id,
        agent_name='agent2',
        runtime_dir=layout.agent_provider_runtime_dir('agent2', 'claude'),
        state='active',
        source='claude_hook',
        event_name='PreToolUse',
        pane_id='%2',
        workspace_path='/tmp/workspace',
        diagnostics={'tool_name': 'Bash'},
        updated_at=NOW,
    )

    class ClaudeTerminalSummaryBackend(_SnapshotBackend):
        def _tmux_run(self, args: list[str], *, capture=False, check=False, timeout=None):
            self.calls.append(list(args))
            if args[:3] == ['capture-pane', '-p', '-t']:
                return type(
                    'CP',
                    (),
                    {
                        'returncode': 0,
                        'stdout': 'Thought for 9s, ran 1 shell command\n\n❯\n',
                        'stderr': '',
                    },
                )()
            return self._snapshot_run(args, capture=capture, check=check, timeout=timeout)

    backend = ClaudeTerminalSummaryBackend()
    controller = ProjectNamespaceController(
        layout,
        project_id,
        backend_factory=lambda socket_path=None: backend,
    )
    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            namespace_controller=controller,
            paths=layout,
            clock=lambda: current_time[0],
            cache_ttl_ms=0,
        )
    )

    first = service.build_response()['view']['agents'][1]
    current_time[0] = '2026-05-20T12:00:59Z'
    before_threshold = service.build_response()['view']['agents'][1]
    current_time[0] = '2026-05-20T12:01:01Z'
    stale = service.build_response()['view']['agents'][1]

    assert first['provider_runtime_status']['state'] == 'tool_running'
    assert first['provider_runtime_status']['pane_state'] == 'terminal_summary'
    assert before_threshold['provider_runtime_status']['state'] == 'tool_running'
    assert stale['activity_state'] == 'idle'
    assert stale['activity_source'] == 'claude_runtime'
    assert stale['activity_reason'] == 'claude_pane_no_active_stale_no_progress'
    assert stale['activity_symbol'] == '◇'
    assert stale['provider_runtime_status']['state'] == 'free'
    assert stale['provider_runtime_status']['source'] == 'stabilizer'
    assert stale['provider_runtime_status']['pane_state'] == 'terminal_summary'
    assert 'raw_state=tool_running' in stale['provider_runtime_status']['notes']


def test_project_view_codex_runtime_status_overrides_sidebar_presentation(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-codex-runtime-sidebar'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    runtime = _runtime('agent1', project_id=project_id)
    registry.upsert(runtime)
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    ProjectNamespaceStateStore(layout).save(
        ProjectNamespaceState(
            project_id=project_id,
            namespace_epoch=3,
            tmux_socket_path=str(layout.ccbd_tmux_socket_path),
            tmux_session_name='ccb-codex-runtime-sidebar',
            layout_version=2,
        )
    )
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)

    class CodexWorkingBackend(_SnapshotBackend):
        def _tmux_run(self, args: list[str], *, capture=False, check=False, timeout=None):
            self.calls.append(list(args))
            if args[:3] == ['capture-pane', '-p', '-t']:
                return type('CP', (), {'returncode': 0, 'stdout': '• Working (28s • esc to interrupt)\n', 'stderr': ''})()
            return self._snapshot_run(args, capture=capture, check=check, timeout=timeout)

    backend = CodexWorkingBackend()
    controller = ProjectNamespaceController(
        layout,
        project_id,
        backend_factory=lambda socket_path=None: backend,
    )

    response = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            namespace_controller=controller,
            paths=layout,
            clock=lambda: NOW,
        )
    ).build_response()

    agent = response['view']['agents'][0]
    assert agent['activity_state'] == 'active'
    assert agent['activity_source'] == 'codex_runtime'
    assert agent['activity_reason'] == 'codex_working_status_line'
    assert agent['activity_symbol'] == '●'
    assert agent['activity_color'] == 'green'
    assert agent['provider_runtime_status']['state'] == 'working'
    assert agent['provider_runtime_status']['pane_state'] == 'working'


def test_project_view_codex_visible_working_remains_working_after_no_progress_threshold(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-codex-runtime-stale-working'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('agent1', project_id=project_id))
    current_time = ['2026-05-20T12:00:00Z']
    mount_manager = MountManager(layout, clock=lambda: current_time[0])
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    ProjectNamespaceStateStore(layout).save(
        ProjectNamespaceState(
            project_id=project_id,
            namespace_epoch=3,
            tmux_socket_path=str(layout.ccbd_tmux_socket_path),
            tmux_session_name='ccb-codex-runtime-stale-working',
            layout_version=2,
        )
    )
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: current_time[0])

    class CodexWorkingTimerOnlyBackend(_SnapshotBackend):
        def __init__(self) -> None:
            super().__init__()
            self.pane_text = '• Working (1s • esc to interrupt)\n'

        def _tmux_run(self, args: list[str], *, capture=False, check=False, timeout=None):
            self.calls.append(list(args))
            if args[:3] == ['capture-pane', '-p', '-t']:
                return type('CP', (), {'returncode': 0, 'stdout': self.pane_text, 'stderr': ''})()
            return self._snapshot_run(args, capture=capture, check=check, timeout=timeout)

    backend = CodexWorkingTimerOnlyBackend()
    controller = ProjectNamespaceController(
        layout,
        project_id,
        backend_factory=lambda socket_path=None: backend,
    )
    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            namespace_controller=controller,
            paths=layout,
            clock=lambda: current_time[0],
            cache_ttl_ms=0,
        )
    )

    first = service.build_response()['view']['agents'][0]
    backend.pane_text = '• Working (59s • esc to interrupt)\n'
    current_time[0] = '2026-05-20T12:00:59Z'
    before_threshold = service.build_response()['view']['agents'][0]
    backend.pane_text = '• Working (1m 1s • esc to interrupt)\n'
    current_time[0] = '2026-05-20T12:01:01Z'
    after_threshold = service.build_response()['view']['agents'][0]

    assert first['provider_runtime_status']['state'] == 'working'
    assert before_threshold['provider_runtime_status']['state'] == 'working'
    assert after_threshold['activity_state'] == 'active'
    assert after_threshold['activity_source'] == 'codex_runtime'
    assert after_threshold['activity_reason'] == 'codex_working_status_line'
    assert after_threshold['activity_symbol'] == '●'
    assert after_threshold['provider_runtime_status']['state'] == 'working'
    assert after_threshold['provider_runtime_status']['source'] == 'pane'
    assert after_threshold['provider_runtime_status']['pane_state'] == 'working'


def test_project_view_codex_no_active_status_without_pane_progress_becomes_free(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-codex-runtime-no-active-stale'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('agent1', project_id=project_id))
    current_time = ['2026-05-20T12:00:00Z']
    mount_manager = MountManager(layout, clock=lambda: current_time[0])
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    ProjectNamespaceStateStore(layout).save(
        ProjectNamespaceState(
            project_id=project_id,
            namespace_epoch=3,
            tmux_socket_path=str(layout.ccbd_tmux_socket_path),
            tmux_session_name='ccb-codex-runtime-no-active-stale',
            layout_version=2,
        )
    )
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: current_time[0])

    class CodexNoActiveBackend(_SnapshotBackend):
        def __init__(self) -> None:
            super().__init__()
            self.pane_text = 'Conversation interrupted\n'

        def _tmux_run(self, args: list[str], *, capture=False, check=False, timeout=None):
            self.calls.append(list(args))
            if args[:3] == ['capture-pane', '-p', '-t']:
                return type('CP', (), {'returncode': 0, 'stdout': self.pane_text, 'stderr': ''})()
            return self._snapshot_run(args, capture=capture, check=check, timeout=timeout)

    backend = CodexNoActiveBackend()
    controller = ProjectNamespaceController(
        layout,
        project_id,
        backend_factory=lambda socket_path=None: backend,
    )
    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            namespace_controller=controller,
            paths=layout,
            clock=lambda: current_time[0],
            cache_ttl_ms=0,
        )
    )

    first = service.build_response()['view']['agents'][0]
    current_time[0] = '2026-05-20T12:00:59Z'
    before_threshold = service.build_response()['view']['agents'][0]
    current_time[0] = '2026-05-20T12:01:01Z'
    stale = service.build_response()['view']['agents'][0]

    assert first['provider_runtime_status']['state'] == 'unknown'
    assert before_threshold['provider_runtime_status']['state'] == 'unknown'
    assert stale['activity_state'] == 'idle'
    assert stale['activity_source'] == 'codex_runtime'
    assert stale['activity_reason'] == 'codex_pane_no_active_stale_no_progress'
    assert stale['activity_symbol'] == '◇'
    assert stale['provider_runtime_status']['state'] == 'free'
    assert stale['provider_runtime_status']['source'] == 'stabilizer'
    assert stale['provider_runtime_status']['pane_state'] == 'unknown'
    assert 'raw_state=unknown' in stale['provider_runtime_status']['notes']
    assert 'raw_reason=no_known_status_pattern' in stale['provider_runtime_status']['notes']


def test_project_view_codex_no_active_status_progress_resets_no_progress_timer(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-codex-runtime-progress-reset'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('agent1', project_id=project_id))
    current_time = ['2026-05-20T12:00:00Z']
    mount_manager = MountManager(layout, clock=lambda: current_time[0])
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    ProjectNamespaceStateStore(layout).save(
        ProjectNamespaceState(
            project_id=project_id,
            namespace_epoch=3,
            tmux_socket_path=str(layout.ccbd_tmux_socket_path),
            tmux_session_name='ccb-codex-runtime-progress-reset',
            layout_version=2,
        )
    )
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: current_time[0])

    class CodexWorkingOutputBackend(_SnapshotBackend):
        def __init__(self) -> None:
            super().__init__()
            self.pane_text = 'Conversation interrupted\nchunk A\n'

        def _tmux_run(self, args: list[str], *, capture=False, check=False, timeout=None):
            self.calls.append(list(args))
            if args[:3] == ['capture-pane', '-p', '-t']:
                return type('CP', (), {'returncode': 0, 'stdout': self.pane_text, 'stderr': ''})()
            return self._snapshot_run(args, capture=capture, check=check, timeout=timeout)

    backend = CodexWorkingOutputBackend()
    controller = ProjectNamespaceController(
        layout,
        project_id,
        backend_factory=lambda socket_path=None: backend,
    )
    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            namespace_controller=controller,
            paths=layout,
            clock=lambda: current_time[0],
            cache_ttl_ms=0,
        )
    )

    service.build_response()
    backend.pane_text = 'Conversation interrupted\nchunk B\n'
    current_time[0] = '2026-05-20T12:01:01Z'
    progressed = service.build_response()['view']['agents'][0]
    backend.pane_text = 'Conversation interrupted\nchunk B\n'
    current_time[0] = '2026-05-20T12:02:02Z'
    stale = service.build_response()['view']['agents'][0]

    assert progressed['provider_runtime_status']['state'] == 'unknown'
    assert stale['provider_runtime_status']['state'] == 'free'
    assert stale['activity_reason'] == 'codex_pane_no_active_stale_no_progress'


def test_project_view_codex_running_unknown_displays_start(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-codex-runtime-start'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    runtime = _runtime('agent1', project_id=project_id)
    registry.upsert(runtime)
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    ProjectNamespaceStateStore(layout).save(
        ProjectNamespaceState(
            project_id=project_id,
            namespace_epoch=3,
            tmux_socket_path=str(layout.ccbd_tmux_socket_path),
            tmux_session_name='ccb-codex-runtime-start',
            layout_version=2,
        )
    )
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    running = _job(project_id, job_id='job_running_1234', sender='user', target='agent1', status=JobStatus.RUNNING)
    dispatcher._append_job(running)
    dispatcher._state.mark_active_for(TargetKind.AGENT, 'agent1', running.job_id)

    class CodexPromptBackend(_SnapshotBackend):
        def _tmux_run(self, args: list[str], *, capture=False, check=False, timeout=None):
            self.calls.append(list(args))
            if args[:3] == ['capture-pane', '-p', '-t']:
                return type('CP', (), {'returncode': 0, 'stdout': '› Reply with exactly: ok\n', 'stderr': ''})()
            return self._snapshot_run(args, capture=capture, check=check, timeout=timeout)

    backend = CodexPromptBackend()
    controller = ProjectNamespaceController(
        layout,
        project_id,
        backend_factory=lambda socket_path=None: backend,
    )

    response = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            namespace_controller=controller,
            paths=layout,
            clock=lambda: NOW,
        )
    ).build_response()

    agent = response['view']['agents'][0]
    assert agent['activity_state'] == 'pending'
    assert agent['activity_source'] == 'codex_runtime'
    assert agent['activity_reason'] == 'prompt_submitted_waiting_for_first_signal'
    assert agent['activity_symbol'] == '◌'
    assert agent['activity_color'] == 'yellow'
    assert agent['provider_runtime_status']['state'] == 'start'
    assert agent['provider_runtime_status']['notes'] == [
        'raw_state=unknown',
        'raw_reason=no_known_status_pattern',
    ]


def test_project_view_codex_running_ignores_session_free_without_pane_completion(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-codex-runtime-running-session-free'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    runtime = _runtime('agent1', project_id=project_id)
    runtime = replace(runtime, workspace_path=str(project_root))
    registry.upsert(runtime)
    session_root = (
        layout.agent_provider_state_dir('agent1', 'codex')
        / 'home'
        / 'sessions'
    )
    session_file = session_root / 'rollout.jsonl'
    _write(
        session_file,
        '\n'.join(
            (
                json.dumps(
                    {
                        'type': 'session_meta',
                        'payload': {
                            'cwd': str(project_root),
                            'session_id': 'sid-1',
                        },
                    }
                ),
                json.dumps({'type': 'event_msg', 'payload': {'type': 'task_complete'}}),
            )
        )
        + '\n',
    )
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    ProjectNamespaceStateStore(layout).save(
        ProjectNamespaceState(
            project_id=project_id,
            namespace_epoch=3,
            tmux_socket_path=str(layout.ccbd_tmux_socket_path),
            tmux_session_name='ccb-codex-runtime-running-session-free',
            layout_version=2,
        )
    )
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    running = _job(project_id, job_id='job_running_1234', sender='user', target='agent1', status=JobStatus.RUNNING)
    dispatcher._append_job(running)
    dispatcher._state.mark_active_for(TargetKind.AGENT, 'agent1', running.job_id)

    class CodexPromptBackend(_SnapshotBackend):
        def _tmux_run(self, args: list[str], *, capture=False, check=False, timeout=None):
            self.calls.append(list(args))
            if args[:3] == ['capture-pane', '-p', '-t']:
                return type('CP', (), {'returncode': 0, 'stdout': '› CCB_REQ_ID: job_running_1234\n', 'stderr': ''})()
            return self._snapshot_run(args, capture=capture, check=check, timeout=timeout)

    response = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            namespace_controller=ProjectNamespaceController(
                layout,
                project_id,
                backend_factory=lambda socket_path=None: CodexPromptBackend(),
            ),
            paths=layout,
            clock=lambda: NOW,
        )
    ).build_response()

    agent = response['view']['agents'][0]
    assert agent['activity_state'] == 'pending'
    assert agent['activity_source'] == 'codex_runtime'
    assert agent['activity_reason'] == 'prompt_submitted_waiting_for_first_signal'
    assert agent['activity_symbol'] == '◌'
    assert agent['provider_runtime_status']['state'] == 'start'
    assert agent['provider_runtime_status']['source'] == 'stabilizer'
    assert agent['provider_runtime_status']['session_state'] == 'free'
    assert agent['provider_runtime_status']['pane_state'] == 'unknown'
    assert 'raw_state=free' in agent['provider_runtime_status']['notes']
    assert 'raw_reason=codex_session_task_complete' in agent['provider_runtime_status']['notes']


def test_activity_resolver_provider_free_does_not_hide_running_job() -> None:
    activity = resolve_agent_activity(
        AgentActivityFacts(
            namespace_mounted=True,
            provider='codex',
            runtime_state='idle',
            pane_id='%1',
            pane_state='alive',
            current_job_status='running',
            current_job_id='job_running',
            current_job_updated_at=NOW,
            provider_runtime_state='free',
            provider_runtime_source='codex_runtime',
            provider_runtime_reason='codex_session_task_complete',
        ),
        now=NOW,
    )

    assert activity.state == 'active'
    assert activity.source == 'ccb_job'
    assert activity.reason == 'job_running'
    assert activity.current_job_id == 'job_running'


def test_project_view_marks_stale_provider_activity_failed_from_pane_error(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-provider-pane-error'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    runtime = _runtime('agent1', project_id=project_id)
    runtime.session_id = 'ccb-agent1-session'
    registry.upsert(runtime)
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    ProjectNamespaceStateStore(layout).save(
        ProjectNamespaceState(
            project_id=project_id,
            namespace_epoch=3,
            tmux_socket_path=str(layout.ccbd_tmux_socket_path),
            tmux_session_name='ccb-provider-pane-error',
            layout_version=2,
        )
    )
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    write_activity(
        provider='codex',
        project_id=project_id,
        agent_name='agent1',
        runtime_dir=layout.agent_provider_runtime_dir('agent1', 'codex'),
        state='active',
        source='codex_hook',
        ccb_session_id='ccb-agent1-session',
        pane_id='%1',
        workspace_path='/tmp/workspace',
        updated_at='2026-05-20T11:59:50Z',
    )

    class ErrorPaneBackend(_SnapshotBackend):
        def _tmux_run(self, args: list[str], *, capture=False, check=False, timeout=None):
            self.calls.append(list(args))
            if args[:3] == ['capture-pane', '-p', '-t']:
                return type(
                    'CP',
                    (),
                    {
                        'returncode': 0,
                        'stdout': (
                            'ERROR: Reconnecting... 5/5\n'
                            'ERROR: stream disconnected before completion: error sending request for url '
                            '(http://127.0.0.1:9/responses)\n'
                        ),
                        'stderr': '',
                    },
                )()
            return self._snapshot_run(args, capture=capture, check=check, timeout=timeout)

    backend = ErrorPaneBackend()
    controller = ProjectNamespaceController(
        layout,
        project_id,
        backend_factory=lambda socket_path=None: backend,
    )

    response = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            namespace_controller=controller,
            paths=layout,
            clock=lambda: NOW,
        )
    ).build_response()

    agent = response['view']['agents'][0]
    assert agent['activity_state'] == 'failed'
    assert agent['activity_source'] == 'codex_runtime'
    assert agent['activity_reason'] == 'provider_api_error'
    assert [args for args in backend.calls if args[:3] == ['capture-pane', '-p', '-t']] == [
        ['capture-pane', '-p', '-t', '%1', '-S', '-30']
    ]


def test_project_view_marks_codex_runtime_failed_from_http_status_error_without_activity_artifact(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-provider-http-error'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    runtime = _runtime('agent1', project_id=project_id)
    runtime.session_id = 'codex-session-1'
    registry.upsert(runtime)
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    ProjectNamespaceStateStore(layout).save(
        ProjectNamespaceState(
            project_id=project_id,
            namespace_epoch=3,
            tmux_socket_path=str(layout.ccbd_tmux_socket_path),
            tmux_session_name='ccb-provider-http-error',
            layout_version=2,
        )
    )
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    write_activity(
        provider='codex',
        project_id=project_id,
        agent_name='agent1',
        runtime_dir=layout.agent_provider_runtime_dir('agent1', 'codex'),
        state='active',
        source='codex_hook',
        ccb_session_id='ccb-agent1-launch',
        provider_session_id='codex-session-1',
        pane_id='%1',
        workspace_path='/tmp/workspace',
        updated_at='2026-05-20T11:59:50Z',
    )

    class HttpErrorPaneBackend(_SnapshotBackend):
        def _tmux_run(self, args: list[str], *, capture=False, check=False, timeout=None):
            self.calls.append(list(args))
            if args[:3] == ['capture-pane', '-p', '-t']:
                return type(
                    'CP',
                    (),
                    {
                        'returncode': 0,
                        'stdout': (
                            '› trigger CCB_HTTP_429_SMOKE and stop\n'
                            '■ exceeded retry limit, last status: 429 Too Many Requests\n'
                            '› Find and fix a bug in @filename\n'
                        ),
                        'stderr': '',
                    },
                )()
            return self._snapshot_run(args, capture=capture, check=check, timeout=timeout)

    backend = HttpErrorPaneBackend()
    controller = ProjectNamespaceController(
        layout,
        project_id,
        backend_factory=lambda socket_path=None: backend,
    )

    response = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            namespace_controller=controller,
            paths=layout,
            clock=lambda: NOW,
        )
    ).build_response()

    agent = response['view']['agents'][0]
    assert agent['activity_state'] == 'failed'
    assert agent['activity_source'] == 'codex_runtime'
    assert agent['activity_reason'] == 'provider_api_error'

    activity_payload = load_activity(layout.agent_provider_runtime_dir('agent1', 'codex'))
    assert activity_payload is not None
    assert activity_payload['state'] == 'active'
    assert activity_payload['source'] == 'codex_hook'

    clean_backend = _SnapshotBackend()
    clean_controller = ProjectNamespaceController(
        layout,
        project_id,
        backend_factory=lambda socket_path=None: clean_backend,
    )
    second_response = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            namespace_controller=clean_controller,
            paths=layout,
            clock=lambda: NOW,
            cache_ttl_ms=0,
        )
    ).build_response()

    second_agent = second_response['view']['agents'][0]
    assert second_agent['activity_state'] == 'idle'
    assert second_agent['activity_source'] == 'pane_liveness'
    assert second_agent['activity_reason'] == 'pane_alive'
    assert [args for args in clean_backend.calls if args[:3] == ['capture-pane', '-p', '-t']] == [
        ['capture-pane', '-p', '-t', '%1', '-S', '-30']
    ]


def test_project_view_does_not_use_legacy_codex_prompt_idle_status(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-provider-pane-idle'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    runtime = _runtime('agent1', project_id=project_id)
    runtime.session_id = 'codex-session-1'
    registry.upsert(runtime)
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    ProjectNamespaceStateStore(layout).save(
        ProjectNamespaceState(
            project_id=project_id,
            namespace_epoch=3,
            tmux_socket_path=str(layout.ccbd_tmux_socket_path),
            tmux_session_name='ccb-provider-pane-idle',
            layout_version=2,
        )
    )
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    write_activity(
        provider='codex',
        project_id=project_id,
        agent_name='agent1',
        runtime_dir=layout.agent_provider_runtime_dir('agent1', 'codex'),
        state='active',
        source='codex_hook',
        ccb_session_id='ccb-agent1-launch',
        provider_session_id='codex-session-1',
        pane_id='%1',
        workspace_path='/tmp/workspace',
        updated_at='2026-05-20T11:59:50Z',
    )

    class IdlePromptBackend(_SnapshotBackend):
        def _tmux_run(self, args: list[str], *, capture=False, check=False, timeout=None):
            self.calls.append(list(args))
            if args[:3] == ['capture-pane', '-p', '-t']:
                return type(
                    'CP',
                    (),
                    {
                        'returncode': 0,
                        'stdout': (
                            '› print exactly CCB_OK and stop\n\n'
                            '• CCB_OK\n\n'
                            '› Use /skills to list available skills\n'
                            '  gpt-5.5 xhigh · ~/repo\n'
                        ),
                        'stderr': '',
                    },
                )()
            return self._snapshot_run(args, capture=capture, check=check, timeout=timeout)

    backend = IdlePromptBackend()
    controller = ProjectNamespaceController(
        layout,
        project_id,
        backend_factory=lambda socket_path=None: backend,
    )

    response = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            namespace_controller=controller,
            paths=layout,
            clock=lambda: NOW,
        )
    ).build_response()

    agent = response['view']['agents'][0]
    assert agent['activity_state'] == 'pending'
    assert agent['activity_source'] == 'codex_runtime'
    assert agent['activity_reason'] == 'no_known_status_pattern'
    assert agent['activity_symbol'] == '?'
    assert agent['activity_color'] == 'gray'
    assert agent['provider_runtime_status']['state'] == 'unknown'


def test_project_view_surfaces_provider_auth_recovery_action(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-provider-auth-blocked'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    detail = 'run `codex login` in the source profile before remounting'
    runtime = _runtime(
        'agent1',
        project_id=project_id,
        state=AgentState.DEGRADED,
        health='provider-auth-revoked',
    )
    runtime.pane_state = 'missing'
    runtime.reconcile_state = 'blocked'
    runtime.last_failure_reason = detail
    registry.upsert(runtime)
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(
        project_id=project_id,
        pid=123,
        socket_path=layout.ccbd_socket_path,
        generation=7,
        started_at=NOW,
    )
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    response = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            clock=lambda: NOW,
        )
    ).build_response()

    agent = next(item for item in response['view']['agents'] if item['name'] == 'agent1')
    assert agent['activity_state'] == 'failed'
    assert agent['activity_reason'] == 'provider_auth_revoked'
    assert agent['runtime_health'] == 'provider-auth-revoked'
    assert agent['reconcile_state'] == 'blocked'
    assert agent['runtime_failure_reason'] == detail


def test_project_view_returns_minimal_windows_agents_and_comms(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    for agent_name in config.agents:
        registry.upsert(_runtime(agent_name, project_id=project_id))
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(
        project_id=project_id,
        pid=123,
        socket_path=layout.ccbd_socket_path,
        generation=7,
        started_at=NOW,
    )
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    running = _job(project_id, job_id='job_running_1234', sender='agent2', target='agent1', status=JobStatus.RUNNING)
    dispatcher._append_job(running)
    dispatcher._state.mark_active_for(TargetKind.AGENT, 'agent1', running.job_id)
    queued = _job(project_id, job_id='job_queued_5678', sender='user', target='agent3', status=JobStatus.QUEUED)
    dispatcher._append_job(queued)
    dispatcher._state.enqueue_for(TargetKind.AGENT, 'agent3', queued.job_id)

    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            clock=lambda: NOW,
        )
    )

    response = service.build_response()
    view = response['view']

    assert response['cache']['ttl_ms'] == 1000
    assert response['cache']['sequence'] == 1
    assert view['project']['display_name'] == 'repo'
    assert view['ccbd']['state'] == MountState.MOUNTED.value
    assert view['namespace']['sidebar']['view']['agents_height'] == '50%'
    assert view['namespace']['sidebar']['view']['comms_height'] == '15%'
    assert view['namespace']['sidebar']['view']['tips_height'] == '35%'
    assert view['namespace']['sidebar']['view']['comms_limit'] == 5
    assert view['namespace']['sidebar']['view']['tips'][0] == 'C-b d  detach'
    assert 'C-b h/j/k/l pane' in view['namespace']['sidebar']['view']['tips']
    assert 'copy: y yank' in view['namespace']['sidebar']['view']['tips']
    assert [window['name'] for window in view['windows']] == ['main', 'ops']
    assert view['windows'][0]['agents'] == ['agent1', 'agent2']
    assert [agent['name'] for agent in view['agents']] == ['agent1', 'agent2', 'agent3']
    assert view['agents'][0]['activity_state'] == 'active'
    assert view['agents'][0]['activity_source'] == 'ccb_job'
    assert view['agents'][0]['activity_reason'] == 'job_running'
    assert view['agents'][0]['current_job_id'] == 'job_running_1234'
    assert view['agents'][0]['queue_depth'] == 1
    assert view['agents'][2]['activity_state'] == 'pending'
    assert view['agents'][2]['activity_source'] == 'ccb_job'
    assert view['agents'][2]['activity_reason'] == 'job_queued'
    assert view['agents'][2]['current_job_id'] == 'job_queued_5678'
    assert view['agents'][2]['queue_depth'] == 1
    assert [item['id'] for item in view['comms']] == ['job_running_1234', 'job_queued_5678']
    assert view['comms'][0]['sender'] == 'agent2'
    assert view['comms'][0]['target'] == 'agent1'
    assert view['comms'][0]['execution_phase'] == 'executing'
    assert view['comms'][1]['execution_phase'] == 'queued'


def test_execution_phase_only_marks_request_anchored_provider_idle_as_orphaned() -> None:
    job = _job('project', job_id='job_running', sender='agent2', target='agent1', status=JobStatus.RUNNING)
    configured_agents = frozenset({'agent1', 'agent2'})

    for hint in (None, 'provider_prompt_input_stuck', 'provider_prompt_idle_stale'):
        phase = project_view_service._execution_phase(
            job,
            reply_delivery=None,
            configured_agents=configured_agents,
            running_recover_hint=hint,
        )
        assert phase == ('executing', 'job_running')

    assert project_view_service._execution_phase(
        job,
        reply_delivery=None,
        configured_agents=configured_agents,
        running_recover_hint='provider_prompt_idle',
    ) == ('orphaned', 'orphaned_active_inbound')


def test_project_view_includes_provider_runtime_for_active_execution(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-provider-runtime'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    for agent_name in config.agents:
        registry.upsert(_runtime(agent_name, project_id=project_id))
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(
        project_id=project_id,
        pid=123,
        socket_path=layout.ccbd_socket_path,
        generation=7,
        started_at=NOW,
    )
    execution_service = SimpleNamespace(
        active_runtime_snapshots=lambda: (
            {
                'job_id': 'job_running_1234',
                'agent_name': 'agent1',
                'provider': 'codex',
                'source_kind': 'protocol_event_stream',
                'primary_authority': 'protocol_log',
                'runtime_state': {
                    'delivery_state': 'pending_anchor',
                    'anchor_seen': False,
                    'delivery_started_at': '2026-05-20T11:59:40Z',
                    'delivery_timeout_s': 120.0,
                },
            },
        )
    )
    dispatcher = JobDispatcher(layout, config, registry, execution_service=execution_service, clock=lambda: NOW)
    running = _job(project_id, job_id='job_running_1234', sender='agent2', target='agent1', status=JobStatus.RUNNING)
    dispatcher._append_job(running)
    dispatcher._state.mark_active_for(TargetKind.AGENT, 'agent1', running.job_id)

    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            clock=lambda: NOW,
        )
    )

    agent1 = service.build_response()['view']['agents'][0]

    assert agent1['current_job_id'] == 'job_running_1234'
    assert agent1['provider_runtime']['job_id'] == 'job_running_1234'
    assert agent1['provider_runtime']['primary_authority'] == 'protocol_log'
    assert agent1['provider_runtime']['runtime_state']['delivery_state'] == 'pending_anchor'


def test_project_view_does_not_attach_mismatched_provider_runtime_to_current_job(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-provider-runtime-mismatch'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    for agent_name in config.agents:
        registry.upsert(_runtime(agent_name, project_id=project_id))
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(
        project_id=project_id,
        pid=123,
        socket_path=layout.ccbd_socket_path,
        generation=7,
        started_at=NOW,
    )
    execution_service = SimpleNamespace(
        active_runtime_snapshots=lambda: (
            {
                'job_id': 'job_other_9999',
                'agent_name': 'agent1',
                'provider': 'codex',
                'runtime_state': {'delivery_state': 'accepted'},
            },
        )
    )
    dispatcher = JobDispatcher(layout, config, registry, execution_service=execution_service, clock=lambda: NOW)
    running = _job(project_id, job_id='job_running_1234', sender='agent2', target='agent1', status=JobStatus.RUNNING)
    dispatcher._append_job(running)
    dispatcher._state.mark_active_for(TargetKind.AGENT, 'agent1', running.job_id)

    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            clock=lambda: NOW,
        )
    )

    agent1 = service.build_response()['view']['agents'][0]

    assert agent1['current_job_id'] == 'job_running_1234'
    assert 'provider_runtime' not in agent1


def test_project_view_marks_multiple_orphan_provider_runtimes_as_conflict(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-provider-runtime-conflict'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    for agent_name in config.agents:
        registry.upsert(_runtime(agent_name, project_id=project_id))
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(
        project_id=project_id,
        pid=123,
        socket_path=layout.ccbd_socket_path,
        generation=7,
        started_at=NOW,
    )
    execution_service = SimpleNamespace(
        active_runtime_snapshots=lambda: (
            {'job_id': 'job_orphan_a', 'agent_name': 'agent1', 'provider': 'codex'},
            {'job_id': 'job_orphan_b', 'agent_name': 'agent1', 'provider': 'codex'},
        )
    )
    dispatcher = JobDispatcher(layout, config, registry, execution_service=execution_service, clock=lambda: NOW)

    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            clock=lambda: NOW,
        )
    )

    agent1 = service.build_response()['view']['agents'][0]

    assert agent1['provider_runtime']['conflict'] == 'multiple_provider_runtimes_without_control_job'
    assert agent1['provider_runtime']['runtime_count'] == 2
    assert agent1['provider_runtime']['job_ids'] == ['job_orphan_a', 'job_orphan_b']


def test_project_view_includes_tool_window_without_agent_row(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-tool-view'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config_with_tool_window()
    registry = AgentRegistry(layout, config)
    for agent_name in config.agents:
        registry.upsert(_runtime(agent_name, project_id=project_id))
    mount_manager = MountManager(layout, clock=lambda: NOW)
    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=JobDispatcher(layout, config, registry, clock=lambda: NOW),
            clock=lambda: NOW,
        )
    )

    view = service.build_response()['view']

    assert [window['name'] for window in view['windows']] == ['main', 'ops', 'files']
    tool = view['windows'][2]
    assert tool['kind'] == 'tool'
    assert tool['label'] == 'files'
    assert tool['show_in_sidebar'] is True
    assert tool['agents'] == []
    assert [agent['name'] for agent in view['agents']] == ['agent1', 'agent2', 'agent3']


def test_project_view_hot_reloads_sidebar_view_config(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-sidebar-view'
    project_root.mkdir()
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """version = 2
entry_window = "main"

[windows]
main = "agent1:codex, agent2:claude"
ops = "agent3:codex"

[ui.sidebar.view]
agents_height = "50%"
comms_height = "15%"
tips_height = "35%"
comms_limit = 4
tips = ["C-b d detach"]
""",
    )
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    mount_manager = MountManager(layout, clock=lambda: NOW)
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            clock=lambda: NOW,
            cache_ttl_ms=0,
        )
    )

    first = service.build_response()['view']['namespace']['sidebar']['view']
    _write(
        config_path,
        """version = 2
entry_window = "main"

[windows]
main = "agent1:codex, agent2:claude"
ops = "agent3:codex"

[ui.sidebar.view]
agents_height = "60%"
comms_height = "10%"
tips_height = "30%"
comms_limit = 2
tips = ["C-b z zoom", "C-b c new win"]
""",
    )
    second = service.build_response()['view']['namespace']['sidebar']['view']

    assert first['agents_height'] == '50%'
    assert first['comms_height'] == '15%'
    assert first['tips_height'] == '35%'
    assert first['comms_limit'] == 4
    assert first['tips'] == ['C-b d detach']
    assert second['agents_height'] == '60%'
    assert second['comms_height'] == '10%'
    assert second['tips_height'] == '30%'
    assert second['comms_limit'] == 2
    assert second['tips'] == ['C-b z zoom', 'C-b c new win']


def test_project_view_reports_sidebar_view_config_error_without_losing_last_good_view(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-sidebar-view-error'
    project_root.mkdir()
    config_path = project_root / '.ccb' / 'ccb.config'
    _write(
        config_path,
        """version = 2
entry_window = "main"

[windows]
main = "agent1:codex, agent2:claude"
ops = "agent3:codex"

[ui.sidebar.view]
comms_limit = 2
tips = ["C-b z zoom"]
""",
    )
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    mount_manager = MountManager(layout, clock=lambda: NOW)
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            clock=lambda: NOW,
            cache_ttl_ms=0,
        )
    )

    _write(
        config_path,
        """version = 2
entry_window = "main"

[windows]
main = "agent1:codex, agent2:claude"
ops = "agent3:codex"

[ui.sidebar.view]
tips = ["missing comma" "next"]
""",
    )
    sidebar = service.build_response()['view']['namespace']['sidebar']

    assert sidebar['view']['comms_limit'] == config.sidebar_view.comms_limit
    assert sidebar['view']['tips'][0] == 'C-b d  detach'
    assert 'invalid TOML config' in sidebar['view_error']


def test_project_view_comms_includes_recent_terminal_jobs(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-comms-terminal'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    for agent_name in config.agents:
        registry.upsert(_runtime(agent_name, project_id=project_id))
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    old_running = _job(
        project_id,
        job_id='job_running_old',
        sender='agent2',
        target='agent1',
        status=JobStatus.RUNNING,
        updated_at='2026-05-20T11:59:00Z',
    )
    dispatcher._append_job(old_running)
    dispatcher._state.mark_active_for(TargetKind.AGENT, 'agent1', old_running.job_id)
    completed = _job(
        project_id,
        job_id='job_done_recent',
        sender='agent1',
        target='agent2',
        status=JobStatus.COMPLETED,
        updated_at='2026-05-20T12:00:05Z',
    )
    dispatcher._append_job(completed)
    failed_old = _job(
        project_id,
        job_id='job_failed_old',
        sender='user',
        target='agent3',
        status=JobStatus.FAILED,
        updated_at='2026-05-20T11:58:00Z',
    )
    dispatcher._append_job(failed_old)

    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            clock=lambda: NOW,
        )
    )

    comms = service.build_response()['view']['comms']

    assert [item['id'] for item in comms[:3]] == ['job_done_recent', 'job_running_old', 'job_failed_old']
    assert comms[0]['status'] == 'completed'
    assert comms[0]['short_reason'] == 'task_complete'
    assert comms[2]['status'] == 'failed'


def test_project_view_comms_exposes_mobile_attachment_metadata(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-comms-attachments'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    for agent_name in config.agents:
        registry.upsert(_runtime(agent_name, project_id=project_id))
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    dispatcher._append_job(
        _job(
            project_id,
            job_id='job_mobile_attachment',
            sender='user',
            target='agent1',
            status=JobStatus.COMPLETED,
            body='Uploaded attachment: probe.txt',
            route_options={
                'source': 'mobile_gateway',
                'attachments': [
                    {
                        'file_id': 'mobile-file-1',
                        'file_name': 'probe.txt',
                        'mime_type': 'text/plain',
                        'size_bytes': 11,
                        'kind': 'document',
                        'local_path': '/tmp/should-not-leak',
                    }
                ],
            },
        )
    )

    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            clock=lambda: NOW,
        )
    )

    comm = service.build_response()['view']['comms'][0]

    assert comm['id'] == 'job_mobile_attachment'
    assert comm['attachments'] == [
        {
            'file_id': 'mobile-file-1',
            'file_name': 'probe.txt',
            'mime_type': 'text/plain',
            'size_bytes': 11,
            'kind': 'document',
        }
    ]
    assert 'local_path' not in str(comm['attachments'])


def test_project_view_filters_dismissed_comms_from_shared_state(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-comms-dismissed'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    for agent_name in config.agents:
        registry.upsert(_runtime(agent_name, project_id=project_id))
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    dismissed = _job(
        project_id,
        job_id='job_dismissed',
        sender='agent1',
        target='agent2',
        status=JobStatus.COMPLETED,
        updated_at='2026-05-20T12:00:05Z',
    )
    kept = _job(
        project_id,
        job_id='job_kept',
        sender='agent2',
        target='agent1',
        status=JobStatus.COMPLETED,
        updated_at='2026-05-20T12:00:04Z',
    )
    dispatcher._append_job(dismissed)
    dispatcher._append_job(kept)
    state_store = ProjectViewStateStore(layout, project_id=project_id)
    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            state_store=state_store,
            clock=lambda: NOW,
            cache_ttl_ms=0,
        )
    )

    before = service.build_response()['view']['comms']
    state_store.dismiss_comms('job_dismissed')
    after = service.build_response()['view']['comms']

    assert [item['id'] for item in before[:2]] == ['job_dismissed', 'job_kept']
    assert [item['id'] for item in after] == ['job_kept']


def test_project_view_terminal_comms_do_not_mark_agent_failed(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-terminal-agent-clean'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('agent3', project_id=project_id))
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    cancelled = _job(
        project_id,
        job_id='job_cancelled_recent',
        sender='agent1',
        target='agent3',
        status=JobStatus.CANCELLED,
        updated_at=NOW,
        body='cancelled ask',
    )
    dispatcher._append_job(cancelled)
    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            clock=lambda: NOW,
        )
    )

    view = service.build_response()['view']
    agent3 = next(agent for agent in view['agents'] if agent['name'] == 'agent3')

    assert agent3['activity_state'] == 'idle'
    assert agent3['activity_reason'] == 'pane_alive'
    assert 'current_job_id' not in agent3
    assert view['comms'][0]['id'] == cancelled.job_id
    assert view['comms'][0]['status'] == 'cancelled'


def test_project_view_marks_callback_parent_waiting_for_child(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-callback-waiting-agent'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    for agent_name in config.agents:
        registry.upsert(_runtime(agent_name, project_id=project_id))
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)

    parent_job_id = dispatcher.submit(
        MessageEnvelope(
            project_id=project_id,
            to_agent='agent1',
            from_actor='user',
            body='root task',
            task_id='task-callback-wait',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    ).jobs[0].job_id
    dispatcher.tick()
    child_job_id = dispatcher.submit(
        MessageEnvelope(
            project_id=project_id,
            to_agent='agent2',
            from_actor='agent1',
            body='child task',
            task_id='task-callback-wait',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
            route_options={'mode': 'chain'},
        )
    ).jobs[0].job_id
    edge = CallbackEdgeStore(layout).get_latest_for_child_job(child_job_id)
    assert edge is not None
    assert edge.state is CallbackEdgeState.PENDING
    dispatcher.tick()
    dispatcher.complete(parent_job_id, _decision(reply='delegated to child'))

    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            clock=lambda: NOW,
        )
    )

    view = service.build_response()['view']
    agent1 = next(agent for agent in view['agents'] if agent['name'] == 'agent1')
    agent2 = next(agent for agent in view['agents'] if agent['name'] == 'agent2')

    assert agent1['activity_state'] == 'pending'
    assert agent1['activity_source'] == 'chain'
    assert agent1['activity_reason'] == 'chain_waiting_child'
    assert agent1['chain_waiting_child_job_id'] == child_job_id
    assert agent1['chain_waiting_child_agent'] == 'agent2'
    assert agent1['chain_waiting_state'] == 'pending'
    assert agent2['activity_state'] == 'pending'
    assert agent2['activity_source'] == 'claude_runtime'
    assert agent2['activity_reason'] == 'prompt_submitted_waiting_for_first_signal'
    assert agent2['current_job_id'] == child_job_id


def test_project_view_comms_collapses_retry_attempts_by_message(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-comms-retry-lineage'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    for agent_name in config.agents:
        registry.upsert(_runtime(agent_name, project_id=project_id))
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    first = _job(
        project_id,
        job_id='job_retry_failed',
        sender='agent2',
        target='agent1',
        status=JobStatus.FAILED,
        updated_at='2026-05-20T12:00:01Z',
        terminal_reason='transport_error',
        body='recover this task',
    )
    latest = _job(
        project_id,
        job_id='job_retry_completed',
        sender='agent2',
        target='agent1',
        status=JobStatus.COMPLETED,
        updated_at='2026-05-20T12:00:02Z',
        body='recover this task',
    )
    dispatcher._append_job(first)
    dispatcher._append_job(latest)
    dispatcher._message_bureau_control._attempt_store.append(
        AttemptRecord(
            attempt_id='att_retry_failed',
            message_id='msg_retry_lineage',
            agent_name='agent1',
            provider='codex',
            job_id=first.job_id,
            retry_index=0,
            health_snapshot_ref=None,
            started_at=first.created_at,
            updated_at=first.updated_at,
            attempt_state=AttemptState.FAILED,
        )
    )
    dispatcher._message_bureau_control._attempt_store.append(
        AttemptRecord(
            attempt_id='att_retry_completed',
            message_id='msg_retry_lineage',
            agent_name='agent1',
            provider='codex',
            job_id=latest.job_id,
            retry_index=1,
            health_snapshot_ref=None,
            started_at=latest.created_at,
            updated_at=latest.updated_at,
            attempt_state=AttemptState.COMPLETED,
        )
    )
    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            clock=lambda: NOW,
        )
    )

    comms = service.build_response()['view']['comms']

    assert [item['id'] for item in comms] == [latest.job_id]
    assert comms[0]['status'] == 'completed'
    assert comms[0]['status_label'] == 'back'
    assert comms[0]['recoverable'] is False


def test_project_view_comms_folds_reply_delivery_into_source_ask(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-comms-replies'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    for agent_name in config.agents:
        registry.upsert(_runtime(agent_name, project_id=project_id))
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    source = _job(
        project_id,
        job_id='job_source_1234',
        sender='agent2',
        target='agent3',
        status=JobStatus.COMPLETED,
        updated_at='2026-05-20T12:00:01Z',
        body='review the cross-window routing result',
    )
    dispatcher._append_job(source)
    reply_delivery = _reply_delivery_job(
        project_id,
        job_id='job_delivery_5678',
        source_agent='agent3',
        source_job_id=source.job_id,
        target='agent2',
        status=JobStatus.COMPLETED,
        updated_at='2026-05-20T12:00:02Z',
    )
    dispatcher._append_job(reply_delivery)
    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            clock=lambda: NOW,
        )
    )

    comms = service.build_response()['view']['comms']

    assert [item['id'] for item in comms] == [source.job_id]
    assert comms[0]['sender'] == 'agent2'
    assert comms[0]['target'] == 'agent3'
    assert comms[0]['status'] == 'completed'
    assert comms[0]['business_status'] == 'replied'
    assert comms[0]['status_label'] == 'done'
    assert comms[0]['body_preview'] == 'review the cross-window routing result'
    assert comms[0]['reply_status'] == 'completed'
    assert comms[0]['reply_delivery_job_id'] == reply_delivery.job_id


def test_project_view_comms_folds_reply_delivery_by_reply_record_without_body_job(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-comms-replies-structured'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    for agent_name in config.agents:
        registry.upsert(_runtime(agent_name, project_id=project_id))
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    source = _job(
        project_id,
        job_id='job_source_structured',
        sender='agent2',
        target='agent3',
        status=JobStatus.COMPLETED,
        updated_at='2026-05-20T12:00:01Z',
        body='check structured reply delivery folding',
    )
    dispatcher._append_job(source)
    _record_reply_for_source(dispatcher, source, reply_id='reply_structured')
    reply_delivery = _reply_delivery_job(
        project_id,
        job_id='job_delivery_structured',
        source_agent='agent3',
        source_job_id='',
        target='agent2',
        status=JobStatus.COMPLETED,
        updated_at='2026-05-20T12:00:02Z',
        reply_id='reply_structured',
        body='CCB_REPLY from=agent3 reply=reply_structured status=completed\n\nOK',
    )
    dispatcher._append_job(reply_delivery)
    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            clock=lambda: NOW,
        )
    )

    comms = service.build_response()['view']['comms']

    assert [item['id'] for item in comms] == [source.job_id]
    assert comms[0]['business_status'] == 'replied'
    assert comms[0]['reply_status'] == 'completed'
    assert comms[0]['reply_delivery_job_id'] == reply_delivery.job_id
    assert comms[0]['body_preview'] == 'check structured reply delivery folding'


def test_project_view_resolves_reply_delivery_sources_without_jsonl_list_all(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-comms-indexed-replies'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    for agent_name in config.agents:
        registry.upsert(_runtime(agent_name, project_id=project_id))
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    for index in range(3):
        source = _job(
            project_id,
            job_id=f'job_source_indexed_{index}',
            sender='agent2',
            target='agent3',
            status=JobStatus.COMPLETED,
            updated_at=f'2026-05-20T12:00:0{index}Z',
            body=f'indexed reply delivery {index}',
        )
        dispatcher._append_job(source)
        _record_reply_for_source(dispatcher, source, reply_id=f'reply_indexed_{index}')
        dispatcher._append_job(
            _reply_delivery_job(
                project_id,
                job_id=f'job_delivery_indexed_{index}',
                source_agent='agent3',
                source_job_id='',
                target='agent2',
                status=JobStatus.COMPLETED,
                updated_at=f'2026-05-20T12:00:1{index}Z',
                reply_id=f'reply_indexed_{index}',
                body=f'CCB_REPLY from=agent3 reply=reply_indexed_{index} status=completed\n\nOK',
            )
        )
    attempt_store = dispatcher._message_bureau_control._attempt_store
    reply_store = dispatcher._message_bureau_control._reply_store
    message_store = dispatcher._message_bureau_control._message_store
    monkeypatch.setattr(attempt_store, 'list_all', lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('unexpected attempt list_all')))
    monkeypatch.setattr(attempt_store, 'list_message', lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('unexpected attempt list_message')))
    monkeypatch.setattr(reply_store, 'list_all', lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('unexpected reply list_all')))
    monkeypatch.setattr(message_store, 'list_all', lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('unexpected message list_all')))
    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            clock=lambda: NOW,
        )
    )

    comms = service.build_response()['view']['comms']

    assert [item['id'] for item in comms] == [
        'job_source_indexed_2',
        'job_source_indexed_1',
        'job_source_indexed_0',
    ]
    assert {item['business_status'] for item in comms} == {'replied'}


def test_reply_delivery_lookup_fallback_avoids_jsonl_list_all(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-comms-fallback'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    for agent_name in config.agents:
        registry.upsert(_runtime(agent_name, project_id=project_id))
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    source = _job(
        project_id,
        job_id='job_source_fallback',
        sender='agent2',
        target='agent3',
        status=JobStatus.COMPLETED,
        updated_at='2026-05-20T12:00:01Z',
        body='fallback reply delivery',
    )
    dispatcher._append_job(source)
    _record_reply_for_source(dispatcher, source, reply_id='reply_fallback')
    delivery = _reply_delivery_job(
        project_id,
        job_id='job_delivery_fallback',
        source_agent='agent3',
        source_job_id='',
        target='agent2',
        status=JobStatus.COMPLETED,
        updated_at='2026-05-20T12:00:02Z',
        reply_id='reply_fallback',
        body='CCB_REPLY from=agent3 reply=reply_fallback status=completed\n\nOK',
    )
    dispatcher._append_job(delivery)
    attempt_store = dispatcher._message_bureau_control._attempt_store
    reply_store = dispatcher._message_bureau_control._reply_store
    message_store = dispatcher._message_bureau_control._message_store
    monkeypatch.setattr(attempt_store, 'list_all', lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('unexpected attempt list_all')))
    monkeypatch.setattr(attempt_store, 'list_message', lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('unexpected attempt list_message')))
    monkeypatch.setattr(reply_store, 'list_all', lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('unexpected reply list_all')))
    monkeypatch.setattr(message_store, 'list_all', lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('unexpected message list_all')))

    deliveries = project_view_service._reply_deliveries_by_source_job_id(dispatcher, (source, delivery))

    assert deliveries == {source.job_id: delivery}


def test_project_view_recent_jobs_uses_bounded_tail_reads(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-comms-tail'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    for agent_name in config.agents:
        registry.upsert(_runtime(agent_name, project_id=project_id))
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    for index in range(160):
        dispatcher._append_job(
            _job(
                project_id,
                job_id=f'job_tail_{index:03d}',
                sender='cmd',
                target='agent1',
                status=JobStatus.COMPLETED,
                updated_at=f'2026-05-20T12:{index // 60:02d}:{index % 60:02d}Z',
                body=f'tail job {index}',
            )
        )
    monkeypatch.setattr(
        dispatcher._job_store,
        'list_agent',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('unexpected full list_agent')),
    )
    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            clock=lambda: NOW,
        )
    )

    comms = service.build_response()['view']['comms']

    assert [item['id'] for item in comms] == [
        'job_tail_159',
        'job_tail_158',
        'job_tail_157',
        'job_tail_156',
        'job_tail_155',
        'job_tail_154',
        'job_tail_153',
        'job_tail_152',
    ]


def test_project_view_recent_jobs_uses_adaptive_scan_budget(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-comms-adaptive-budget'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    for agent_name in config.agents:
        registry.upsert(_runtime(agent_name, project_id=project_id))
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    captured: dict[str, object] = {}

    def fake_recent_jobs(agent_names, *, per_agent_limit, per_agent_initial_limit, result_limit, statuses):
        captured['agent_names'] = tuple(agent_names)
        captured['per_agent_limit'] = per_agent_limit
        captured['per_agent_initial_limit'] = per_agent_initial_limit
        captured['result_limit'] = result_limit
        captured['statuses'] = tuple(statuses)
        return ()

    monkeypatch.setattr(dispatcher._job_store, 'list_project_view_recent_jobs', fake_recent_jobs)
    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            clock=lambda: NOW,
        )
    )

    service.build_response()

    assert captured['agent_names'] == ('agent1', 'agent2', 'agent3')
    assert captured['per_agent_limit'] == 128
    assert captured['per_agent_initial_limit'] == 32
    assert captured['result_limit'] == 64
    assert 'completed' in captured['statuses']


def test_project_view_recent_jobs_uses_required_rust_summary_helper(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-comms-summary-helper'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    for agent_name in config.agents:
        registry.upsert(_runtime(agent_name, project_id=project_id))
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    dispatcher._append_job(
        _job(
            project_id,
            job_id='job_python_path_should_not_run',
            sender='cmd',
            target='agent1',
            status=JobStatus.COMPLETED,
            updated_at='2026-05-20T12:00:01Z',
            body='python fallback should not run',
        )
    )
    helper = _write_helper(
        tmp_path / 'recent_jobs_helper.py',
        """import json, sys
if sys.argv[1:] == ['--capabilities']:
    print(json.dumps({'schema_version': 1, 'capabilities': ['project_view.recent_jobs', 'jobs.query.recent']}))
else:
    request = json.loads(sys.stdin.read())
    print(json.dumps({'schema_version': 1, 'ok': True, 'capability': request['capability'], 'payload': {
        'jobs': [{
            'job_id': 'job_from_summary_helper',
            'agent_name': 'agent1',
            'target_name': 'agent1',
            'provider': 'codex',
            'status': 'completed',
            'terminal_decision': {'reason': 'task_complete'},
            'created_at': '2026-05-20T11:59:00Z',
            'updated_at': '2026-05-20T12:00:09Z',
            'provider_options': {},
            'request': {
                'project_id': 'proj',
                'to_agent': 'agent1',
                'from_actor': 'cmd',
                'body': 'summary helper body',
                'task_id': None,
                'reply_to': None,
                'message_type': 'ask',
                'delivery_scope': 'single',
                'silence_on_success': False,
                'route_options': {},
                'body_artifact': None,
            },
        }],
        'scanned': 1,
        'returned': 1,
        'truncated': False,
        'next_budget_hint': {'per_agent_initial': 32, 'per_agent_max': 64},
        'error': None,
    }}))
""",
    )
    monkeypatch.setenv('CCB_RUST_PROJECT_VIEW_RECENT_JOBS', '1')
    monkeypatch.setenv(RUST_HELPER_BIN_ENV, str(helper))
    monkeypatch.setattr(
        dispatcher._job_store,
        'list_agent_tail',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('unexpected Python list_agent_tail fallback')),
    )
    monkeypatch.setattr(
        dispatcher._job_store,
        'list_agent_tails_batch',
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('unexpected Python batch tail fallback')),
    )
    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            clock=lambda: NOW,
        )
    )

    comms = service.build_response()['view']['comms']

    assert [item['id'] for item in comms] == ['job_from_summary_helper']
    assert comms[0]['body_preview'] == 'summary helper body'


def test_project_view_backfills_reply_delivery_source_outside_recent_tail(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-comms-delivery-tail'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    for agent_name in config.agents:
        registry.upsert(_runtime(agent_name, project_id=project_id))
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    source = _job(
        project_id,
        job_id='job_old_reply_source',
        sender='agent2',
        target='agent3',
        status=JobStatus.COMPLETED,
        updated_at='2026-05-20T10:00:00Z',
        body='old source outside recent tail',
    )
    dispatcher._append_job(source)
    _record_reply_for_source(dispatcher, source, reply_id='reply_old_source')
    for index in range(140):
        dispatcher._append_job(
            _job(
                project_id,
                job_id=f'job_tail_filler_{index:03d}',
                sender='cmd',
                target='agent3',
                status=JobStatus.COMPLETED,
                updated_at=f'2026-05-20T11:{index // 60:02d}:{index % 60:02d}Z',
                body=f'filler {index}',
            )
        )
    delivery = _reply_delivery_job(
        project_id,
        job_id='job_recent_reply_delivery',
        source_agent='agent3',
        source_job_id='',
        target='agent2',
        status=JobStatus.COMPLETED,
        updated_at='2026-05-20T12:00:30Z',
        reply_id='reply_old_source',
        body='CCB_REPLY from=agent3 reply=reply_old_source status=completed\n\nOK',
    )
    dispatcher._append_job(delivery)
    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            clock=lambda: NOW,
        )
    )

    comms = service.build_response()['view']['comms']

    assert comms[0]['id'] == source.job_id
    assert comms[0]['business_status'] == 'replied'
    assert comms[0]['reply_delivery_job_id'] == delivery.job_id


def test_project_view_comms_marks_agent_reply_delivery_pending(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-comms-pending-reply'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    for agent_name in config.agents:
        registry.upsert(_runtime(agent_name, project_id=project_id))
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    source = _job(
        project_id,
        job_id='job_source_waiting',
        sender='agent2',
        target='agent3',
        status=JobStatus.COMPLETED,
        updated_at='2026-05-20T12:00:01Z',
    )
    dispatcher._append_job(source)
    delivering_source = _job(
        project_id,
        job_id='job_source_delivering',
        sender='agent2',
        target='agent3',
        status=JobStatus.COMPLETED,
        updated_at='2026-05-20T12:00:02Z',
    )
    dispatcher._append_job(delivering_source)
    delivery = _reply_delivery_job(
        project_id,
        job_id='job_delivery_running',
        source_agent='agent3',
        source_job_id=delivering_source.job_id,
        target='agent2',
        status=JobStatus.RUNNING,
        updated_at='2026-05-20T12:00:03Z',
    )
    dispatcher._append_job(delivery)
    dispatcher._state.mark_active_for(TargetKind.AGENT, delivery.target_name, delivery.job_id)
    cmd_source = _job(
        project_id,
        job_id='job_cmd_source',
        sender='cmd',
        target='agent3',
        status=JobStatus.COMPLETED,
        updated_at='2026-05-20T12:00:02Z',
    )
    dispatcher._append_job(cmd_source)
    silent_source = _job(
        project_id,
        job_id='job_silent_source',
        sender='agent1',
        target='agent3',
        status=JobStatus.COMPLETED,
        updated_at='2026-05-20T12:00:03Z',
        silence_on_success=True,
    )
    dispatcher._append_job(silent_source)
    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            clock=lambda: NOW,
        )
    )

    comms_by_id = {item['id']: item for item in service.build_response()['view']['comms']}

    assert comms_by_id[source.job_id]['business_status'] == 'delivering'
    assert comms_by_id[source.job_id]['execution_phase'] == 'reply_queued'
    assert comms_by_id[source.job_id]['execution_phase_reason'] == 'reply_delivery_pending'
    assert comms_by_id[delivering_source.job_id]['business_status'] == 'delivering'
    assert comms_by_id[delivering_source.job_id]['execution_phase'] == 'reply_delivering'
    assert comms_by_id[delivering_source.job_id]['execution_phase_reason'] == 'reply_delivery_running'
    assert comms_by_id[cmd_source.job_id]['business_status'] == 'replied'
    assert comms_by_id[silent_source.job_id]['business_status'] == 'completed'


def test_project_view_comms_cleans_instructional_body_preview(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-comms-preview-cleanup'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    for agent_name in config.agents:
        registry.upsert(_runtime(agent_name, project_id=project_id))
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    english = _job(
        project_id,
        job_id='job_reply_exactly',
        sender='cmd',
        target='agent3',
        status=JobStatus.COMPLETED,
        updated_at='2026-05-20T12:00:01Z',
        body='Reply exactly: COMMS_BUSINESS_VIEW_OK',
    )
    chinese = _job(
        project_id,
        job_id='job_only_reply',
        sender='cmd',
        target='agent2',
        status=JobStatus.COMPLETED,
        updated_at='2026-05-20T12:00:02Z',
        body='只回复 CONCURRENT_A_OK',
    )
    probe = _job(
        project_id,
        job_id='job_probe_reply',
        sender='cmd',
        target='agent1',
        status=JobStatus.COMPLETED,
        updated_at='2026-05-20T12:00:03Z',
        body='只回复 D23R_OK',
    )
    dispatcher._append_job(english)
    dispatcher._append_job(chinese)
    dispatcher._append_job(probe)
    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            clock=lambda: NOW,
        )
    )

    comms_by_id = {item['id']: item for item in service.build_response()['view']['comms']}

    assert comms_by_id[english.job_id]['body_preview'] == 'smoke: comms business view'
    assert comms_by_id[chinese.job_id]['body_preview'] == 'smoke: concurrent a'
    assert comms_by_id[probe.job_id]['body_preview'] == 'probe: D23R'


def test_project_view_sequence_ignores_generated_at_only(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    timestamps = iter(['2026-05-20T12:00:00Z', '2026-05-20T12:00:01Z'])
    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            clock=lambda: next(timestamps),
            sequence_cache=ProjectViewSequenceCache(),
        )
    )

    first = service.build_response()
    second = service.build_response()

    assert first['cache']['generated_at'] == second['cache']['generated_at']
    assert first['view']['generated_at'] == second['view']['generated_at']
    assert first['cache']['sequence'] == second['cache']['sequence']


def test_project_view_sequence_changes_when_content_changes(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('agent1', project_id=project_id))
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            clock=lambda: NOW,
            sequence_cache=ProjectViewSequenceCache(),
            cache_ttl_ms=0,
        )
    )

    first = service.build_response()
    running = _job(project_id, job_id='job_running_1234', sender='agent2', target='agent1', status=JobStatus.RUNNING)
    dispatcher._append_job(running)
    dispatcher._state.mark_active_for(TargetKind.AGENT, 'agent1', running.job_id)
    second = service.build_response()

    assert first['cache']['sequence'] == 1
    assert second['cache']['sequence'] == 2
    assert first['view']['agents'][0]['activity_state'] == 'idle'
    assert second['view']['agents'][0]['activity_state'] == 'active'
    assert second['view']['agents'][0]['activity_source'] == 'ccb_job'
    assert second['view']['agents'][0]['activity_reason'] == 'job_running'
    assert second['view']['agents'][0]['current_job_id'] == 'job_running_1234'
    assert [item['id'] for item in second['view']['comms']] == ['job_running_1234']


def test_project_view_cache_hit_skips_tmux_calls(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-cache-tmux'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('agent1', project_id=project_id))
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    ProjectNamespaceStateStore(layout).save(
        ProjectNamespaceState(
            project_id=project_id,
            namespace_epoch=3,
            tmux_socket_path=str(layout.ccbd_tmux_socket_path),
            tmux_session_name='ccb-cache-tmux',
            layout_version=2,
        )
    )
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    backend = _SnapshotBackend()
    controller = ProjectNamespaceController(
        layout,
        project_id,
        backend_factory=lambda socket_path=None: backend,
    )
    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            namespace_controller=controller,
            clock=lambda: NOW,
        )
    )

    first = service.build_response()
    backend.calls.clear()
    second = service.build_response()

    assert first is second
    assert backend.calls == []


def test_project_view_uses_longer_idle_cache_ttl(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-idle-cache-ttl'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('agent1', project_id=project_id))
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            clock=lambda: NOW,
        )
    )

    assert service.build_response()['cache']['ttl_ms'] == 5000


def test_project_view_uses_short_cache_ttl_when_work_is_active(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-active-cache-ttl'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('agent1', project_id=project_id))
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    job = _job(project_id, job_id='job_active_ttl', sender='agent2', target='agent1', status=JobStatus.RUNNING)
    dispatcher._append_job(job)
    dispatcher._state.mark_active_for(TargetKind.AGENT, 'agent1', job.job_id)
    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            clock=lambda: NOW,
        )
    )

    assert service.build_response()['cache']['ttl_ms'] == 1000


def test_project_view_idle_cache_ttl_can_restore_legacy_cadence(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv('CCB_PROJECT_VIEW_IDLE_TTL_MS', '1000')
    project_root = tmp_path / 'repo-idle-cache-ttl-env'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('agent1', project_id=project_id))
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            clock=lambda: NOW,
        )
    )

    assert service.build_response()['cache']['ttl_ms'] == 1000



def test_project_view_cache_invalidates_when_dispatcher_jobs_change(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-cache-dispatcher-revision'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    for agent_name in config.agents:
        registry.upsert(_runtime(agent_name, project_id=project_id))
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            clock=lambda: NOW,
            cache_ttl_ms=60000,
        )
    )

    first = service.build_response()
    dispatcher._append_job(
        _job(
            project_id,
            job_id='job_cache_dirty',
            sender='user',
            target='agent1',
            status=JobStatus.COMPLETED,
            body='cache should observe this without waiting for TTL',
        )
    )
    second = service.build_response()

    assert second is not first
    assert [item['id'] for item in second['view']['comms']] == ['job_cache_dirty']

def test_project_view_updates_build_cache_and_tmux_metrics(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-project-view-metrics'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('agent3', project_id=project_id, state=AgentState.BUSY))
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    ProjectNamespaceStateStore(layout).save(
        ProjectNamespaceState(
            project_id=project_id,
            namespace_epoch=3,
            tmux_socket_path=str(layout.ccbd_tmux_socket_path),
            tmux_session_name='ccb-project-view-metrics',
            layout_version=2,
        )
    )
    old_job = _job(
        project_id,
        job_id='job_project_view_metrics',
        sender='agent1',
        target='agent3',
        status=JobStatus.RUNNING,
        updated_at='2026-05-20T11:57:00Z',
    )
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    dispatcher._append_job(old_job)
    dispatcher._state.mark_active_for(TargetKind.AGENT, 'agent3', old_job.job_id)
    backend = _ProviderIdleWithoutAnchorBackend()
    controller = ProjectNamespaceController(
        layout,
        project_id,
        backend_factory=lambda socket_path=None: backend,
    )
    metrics = SimpleNamespace(
        last_project_view_build_duration_s=None,
        last_project_view_response_duration_s=None,
        project_view_cache_hits=0,
        project_view_cache_misses=0,
        last_project_view_tmux_command_count=None,
        last_project_view_capture_pane_count=None,
        last_project_view_store_scan_count=None,
    )
    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            namespace_controller=controller,
            clock=lambda: NOW,
            metrics=metrics,
        )
    )

    first = service.build_response()
    second = service.build_response()

    assert first is second
    assert metrics.project_view_cache_misses == 1
    assert metrics.project_view_cache_hits == 1
    assert metrics.last_project_view_build_duration_s >= 0.0
    assert metrics.last_project_view_response_duration_s >= 0.0
    assert metrics.last_project_view_tmux_command_count == 4
    assert metrics.last_project_view_capture_pane_count == 1
    assert metrics.last_project_view_store_scan_count == 3
    service.invalidate_cache()
    service.build_response()
    assert metrics.project_view_cache_misses == 2
    assert metrics.last_project_view_tmux_command_count == 4
    assert metrics.last_project_view_capture_pane_count == 1
    assert metrics.last_project_view_store_scan_count == 3


def test_project_view_consumes_sidebar_refresh_request_without_crashing(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-project-view-sidebar-refresh'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('agent1', project_id=project_id))
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    ProjectNamespaceStateStore(layout).save(
        ProjectNamespaceState(
            project_id=project_id,
            namespace_epoch=3,
            tmux_socket_path=str(layout.ccbd_tmux_socket_path),
            tmux_session_name='ccb-project-view-sidebar-refresh',
            layout_version=2,
        )
    )
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    backend = _SidebarRefreshBackend(session_name='ccb-project-view-sidebar-refresh')
    controller = ProjectNamespaceController(
        layout,
        project_id,
        backend_factory=lambda socket_path=None: backend,
    )
    metrics = SimpleNamespace(
        project_view_sidebar_refreshes=0,
        project_view_sidebar_refresh_failures=0,
        last_project_view_sidebar_refresh_duration_s=None,
    )
    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            namespace_controller=controller,
            clock=lambda: NOW,
            metrics=metrics,
        )
    )

    service.request_sidebar_refresh()
    response = service.build_response()

    assert response['cache']['sequence'] == 1
    assert ['send-keys', '-t', '%90', 'C-l'] in backend.calls
    assert metrics.project_view_sidebar_refreshes == 1
    assert metrics.project_view_sidebar_refresh_failures == 0
    assert metrics.last_project_view_sidebar_refresh_duration_s >= 0.0


class _FocusBackend:
    def _tmux_run(self, args: list[str], *, capture=False, check=False, timeout=None):
        del check, timeout
        assert capture is True
        if args[:3] == ['display-message', '-p', '-t']:
            return type('CP', (), {'returncode': 0, 'stdout': 'ops\t%2\tagent\tagent3\n', 'stderr': ''})()
        raise AssertionError(args)


class _SnapshotBackend:
    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def _tmux_run(self, args: list[str], *, capture=False, check=False, timeout=None):
        self.calls.append(list(args))
        return self._snapshot_run(args, capture=capture, check=check, timeout=timeout)

    def _snapshot_run(self, args: list[str], *, capture=False, check=False, timeout=None):
        del check, timeout
        assert capture is True
        if args[:3] == ['display-message', '-p', '-t']:
            return type('CP', (), {'returncode': 0, 'stdout': 'main\t%11\tagent\tagent1\n', 'stderr': ''})()
        if args[:2] == ['list-windows', '-t']:
            return type(
                'CP',
                (),
                {
                    'returncode': 0,
                    'stdout': 'main\t@1\t0\nops\t@2\t1\n',
                    'stderr': '',
                },
            )()
        if args[:2] == ['list-panes', '-a']:
            return type(
                'CP',
                (),
                {
                    'returncode': 0,
                    'stdout': (
                        'ccb-snap\tmain\t%90\tproj-snap\tsidebar\tmain\tmain\n'
                        'ccb-snap\tops\t%91\tproj-snap\tsidebar\tops\tops\n'
                        'other\tmain\t%99\tproj-snap\tsidebar\tmain\tmain\n'
                    ),
                    'stderr': '',
                },
            )()
        if args[:3] == ['capture-pane', '-p', '-t']:
            return type('CP', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()
        raise AssertionError(args)


class _SidebarRefreshBackend(_SnapshotBackend):
    def __init__(self, *, session_name: str) -> None:
        super().__init__()
        self.session_name = session_name

    def list_panes_by_user_options(self, expected: dict[str, str]) -> list[str]:
        if expected.get('@ccb_role') == 'sidebar':
            return ['%90']
        return []

    def _tmux_run(self, args: list[str], *, capture=False, check=False, timeout=None):
        if args == ['display-message', '-p', '-t', '%90', '#{session_name}']:
            self.calls.append(list(args))
            return type('CP', (), {'returncode': 0, 'stdout': f'{self.session_name}\n', 'stderr': ''})()
        if args[:2] == ['send-keys', '-t']:
            self.calls.append(list(args))
            return type('CP', (), {'returncode': 0, 'stdout': '', 'stderr': ''})()
        return super()._tmux_run(args, capture=capture, check=check, timeout=timeout)


class _ProviderPromptBackend(_SnapshotBackend):
    def _tmux_run(self, args: list[str], *, capture=False, check=False, timeout=None):
        self.calls.append(list(args))
        if args[:3] == ['capture-pane', '-p', '-t']:
            return type(
                'CP',
                (),
                {
                    'returncode': 0,
                    'stdout': (
                        'Do you trust the contents of this directory?\n'
                        'Working with untrusted contents comes with higher risk.\n'
                        'Press enter to continue\n'
                    ),
                    'stderr': '',
                },
            )()
        return self._snapshot_run(args, capture=capture, check=check, timeout=timeout)


class _ProviderIdleAfterRequestBackend(_SnapshotBackend):
    def __init__(self, job_id: str) -> None:
        super().__init__()
        self._job_id = job_id

    def _tmux_run(self, args: list[str], *, capture=False, check=False, timeout=None):
        self.calls.append(list(args))
        if args[:3] == ['capture-pane', '-p', '-t']:
            return type(
                'CP',
                (),
                {
                    'returncode': 0,
                    'stdout': (
                        f'❯ CCB_REQ_ID: {self._job_id}\n\n'
                        '  cancelled in provider\n\n'
                        '● cancelled\n'
                        '────────────────────────────────\n'
                        '❯ \n'
                        '🤖 Sonnet 4.6 | 📁 repo\n'
                    ),
                    'stderr': '',
                },
            )()
        return self._snapshot_run(args, capture=capture, check=check, timeout=timeout)


class _ProviderIdleWithoutAnchorBackend(_SnapshotBackend):
    def _tmux_run(self, args: list[str], *, capture=False, check=False, timeout=None):
        self.calls.append(list(args))
        if args[:3] == ['capture-pane', '-p', '-t']:
            return type(
                'CP',
                (),
                {
                    'returncode': 0,
                    'stdout': (
                        'Claude Code v2.1.142\n'
                        '/repo\n\n'
                        '────────────────────────────────\n'
                        '❯ \n'
                        '🤖 Sonnet 4.6 | 📁 repo\n'
                    ),
                    'stderr': '',
                },
            )()
        return self._snapshot_run(args, capture=capture, check=check, timeout=timeout)


def test_project_view_marks_active_window_and_agent_from_namespace_focus(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-focus'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    ProjectNamespaceStateStore(layout).save(
        ProjectNamespaceState(
            project_id=project_id,
            namespace_epoch=2,
            tmux_socket_path=str(layout.ccbd_tmux_socket_path),
            tmux_session_name='ccb-focus',
            layout_version=2,
        )
    )
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    controller = ProjectNamespaceController(
        layout,
        project_id,
        backend_factory=lambda socket_path=None: _FocusBackend(),
    )
    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            namespace_controller=controller,
            clock=lambda: NOW,
        )
    )

    view = service.build_response()['view']

    assert view['namespace']['active_window'] == 'ops'
    assert view['namespace']['active_pane_id'] == '%2'
    assert [window['active'] for window in view['windows']] == [False, True]
    assert [agent['active'] for agent in view['agents']] == [False, False, True]


def test_project_view_reads_window_and_sidebar_tmux_metadata(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-snapshot'
    project_root.mkdir()
    layout = PathLayout(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id='proj-snap', pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    ProjectNamespaceStateStore(layout).save(
        ProjectNamespaceState(
            project_id='proj-snap',
            namespace_epoch=3,
            tmux_socket_path=str(layout.ccbd_tmux_socket_path),
            tmux_session_name='ccb-snap',
            layout_version=2,
        )
    )
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    controller = ProjectNamespaceController(
        layout,
        'proj-snap',
        backend_factory=lambda socket_path=None: _SnapshotBackend(),
    )
    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id='proj-snap',
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            namespace_controller=controller,
            clock=lambda: NOW,
        )
    )

    view = service.build_response()['view']

    assert view['namespace']['active_window'] == 'main'
    assert [
        (window['name'], window['tmux_window_id'], window['tmux_window_index'], window['sidebar_pane_id'])
        for window in view['windows']
    ] == [
        ('main', '@1', 0, '%90'),
        ('ops', '@2', 1, '%91'),
    ]


def test_project_view_uses_rust_tmux_parser_when_enabled(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-snapshot-helper'
    project_root.mkdir()
    layout = PathLayout(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id='proj-snap-helper', pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    ProjectNamespaceStateStore(layout).save(
        ProjectNamespaceState(
            project_id='proj-snap-helper',
            namespace_epoch=3,
            tmux_socket_path=str(layout.ccbd_tmux_socket_path),
            tmux_session_name='ccb-snap-helper',
            layout_version=2,
        )
    )
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    backend = _SnapshotBackend()
    controller = ProjectNamespaceController(
        layout,
        'proj-snap-helper',
        backend_factory=lambda socket_path=None: backend,
    )
    helper = _write_helper(
        tmp_path / 'project_view_helper.py',
        """import json, sys
if sys.argv[1:] == ['--capabilities']:
    print(json.dumps({'schema_version': 1, 'capabilities': ['project_view.tmux.parse']}))
else:
    request = json.loads(sys.stdin.read())
    print(json.dumps({'schema_version': 1, 'ok': True, 'capability': request['capability'], 'payload': {
        'focus': {'active_window': 'ops', 'active_pane_id': '%22', 'active_agent': 'agent3'},
        'windows': {
            'main': {'tmux_window_id': '@helper-main', 'tmux_window_index': 10},
            'ops': {'tmux_window_id': '@helper-ops', 'tmux_window_index': 11}
        },
        'sidebars': {'main': '%190', 'ops': '%191'},
    }}))
""",
    )
    monkeypatch.setenv(RUST_PROJECT_VIEW_ENV, '1')
    monkeypatch.setenv(RUST_HELPER_BIN_ENV, str(helper))
    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id='proj-snap-helper',
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            namespace_controller=controller,
            clock=lambda: NOW,
        )
    )

    view = service.build_response()['view']

    assert view['namespace']['active_window'] == 'ops'
    assert [agent['active'] for agent in view['agents']] == [False, False, True]
    assert [
        (window['name'], window['tmux_window_id'], window['tmux_window_index'], window['sidebar_pane_id'])
        for window in view['windows']
    ] == [
        ('main', '@helper-main', 10, '%190'),
        ('ops', '@helper-ops', 11, '%191'),
    ]


def test_project_view_required_tmux_parser_missing_helper_raises_without_python_fallback(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(RUST_PROJECT_VIEW_ENV, 'required')
    monkeypatch.setenv(RUST_HELPER_BIN_ENV, str(tmp_path / 'missing-helper'))

    with pytest.raises(RuntimeError, match='no Python fallback'):
        project_view_service._parse_tmux_project_view_outputs(
            focus_stdout='main\t%11\tagent\tagent1\n',
            windows_stdout='main\t@1\t0\n',
            sidebars_stdout='ccb-snap\tmain\t%90\tproj-snap\tsidebar\tmain\tmain\n',
            session_name='ccb-snap',
            project_id='proj-snap',
        )


def test_project_view_captures_each_running_pane_once_per_build(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-capture-once'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('agent3', project_id=project_id, state=AgentState.BUSY))
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    ProjectNamespaceStateStore(layout).save(
        ProjectNamespaceState(
            project_id=project_id,
            namespace_epoch=3,
            tmux_socket_path=str(layout.ccbd_tmux_socket_path),
            tmux_session_name='ccb-capture-once',
            layout_version=2,
        )
    )
    old_job = _job(
        project_id,
        job_id='job_capture_once',
        sender='agent1',
        target='agent3',
        status=JobStatus.RUNNING,
        updated_at='2026-05-20T11:57:00Z',
    )
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    dispatcher._append_job(old_job)
    dispatcher._state.mark_active_for(TargetKind.AGENT, 'agent3', old_job.job_id)
    backend = _ProviderIdleWithoutAnchorBackend()
    controller = ProjectNamespaceController(
        layout,
        project_id,
        backend_factory=lambda socket_path=None: backend,
    )
    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            namespace_controller=controller,
            clock=lambda: NOW,
        )
    )

    view = service.build_response()['view']

    assert view['comms'][0]['id'] == old_job.job_id
    assert [
        args
        for args in backend.calls
        if args[:3] == ['capture-pane', '-p', '-t'] and args[3] == '%3'
    ] == [['capture-pane', '-p', '-t', '%3', '-S', '-30']]


def test_project_view_reuses_namespace_backend_within_build(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-backend-once'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('agent3', project_id=project_id, state=AgentState.BUSY))
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    ProjectNamespaceStateStore(layout).save(
        ProjectNamespaceState(
            project_id=project_id,
            namespace_epoch=3,
            tmux_socket_path=str(layout.ccbd_tmux_socket_path),
            tmux_session_name='ccb-backend-once',
            layout_version=2,
        )
    )
    old_job = _job(
        project_id,
        job_id='job_backend_once',
        sender='agent1',
        target='agent3',
        status=JobStatus.RUNNING,
        updated_at='2026-05-20T11:57:00Z',
    )
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    dispatcher._append_job(old_job)
    dispatcher._state.mark_active_for(TargetKind.AGENT, 'agent3', old_job.job_id)
    backend = _ProviderIdleWithoutAnchorBackend()
    backend_factory_calls = []
    controller = ProjectNamespaceController(
        layout,
        project_id,
        backend_factory=lambda socket_path=None: backend_factory_calls.append(socket_path) or backend,
    )
    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            namespace_controller=controller,
            clock=lambda: NOW,
        )
    )

    service.build_response()

    assert backend_factory_calls == [str(layout.ccbd_tmux_socket_path)]


def test_project_view_marks_provider_prompt_as_pending(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-provider-prompt'
    project_root.mkdir()
    layout = PathLayout(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('agent1', project_id='proj-prompt'))
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id='proj-prompt', pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    ProjectNamespaceStateStore(layout).save(
        ProjectNamespaceState(
            project_id='proj-prompt',
            namespace_epoch=3,
            tmux_socket_path=str(layout.ccbd_tmux_socket_path),
            tmux_session_name='ccb-prompt',
            layout_version=2,
        )
    )
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    controller = ProjectNamespaceController(
        layout,
        'proj-prompt',
        backend_factory=lambda socket_path=None: _ProviderPromptBackend(),
    )
    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id='proj-prompt',
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            namespace_controller=controller,
            clock=lambda: NOW,
        )
    )

    agent1 = service.build_response()['view']['agents'][0]

    assert agent1['activity_state'] == 'pending'
    assert agent1['activity_source'] == 'codex_runtime'
    assert agent1['activity_reason'] == 'provider_waiting_for_user'
    assert agent1['activity_symbol'] == '?'
    assert agent1['activity_color'] == 'yellow'


def test_project_view_marks_running_job_idle_after_provider_prompt_reappears(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-provider-idle-running'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('agent3', project_id=project_id, state=AgentState.BUSY))
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    ProjectNamespaceStateStore(layout).save(
        ProjectNamespaceState(
            project_id=project_id,
            namespace_epoch=3,
            tmux_socket_path=str(layout.ccbd_tmux_socket_path),
            tmux_session_name='ccb-idle-running',
            layout_version=2,
        )
    )
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    job_id = _submit(dispatcher, project_id, sender='agent1', target='agent3', body='cancelled in provider')
    dispatcher.tick()
    job = dispatcher.get(job_id)
    assert job is not None
    job = replace(job, updated_at='2026-05-20T11:59:20Z')
    dispatcher._append_job(job)
    controller = ProjectNamespaceController(
        layout,
        project_id,
        backend_factory=lambda socket_path=None: _ProviderIdleAfterRequestBackend(job.job_id),
    )
    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            namespace_controller=controller,
            clock=lambda: NOW,
        )
    )

    view = service.build_response()['view']
    agent3 = next(agent for agent in view['agents'] if agent['name'] == 'agent3')
    comm = view['comms'][0]

    assert agent3['activity_state'] == 'pending'
    assert agent3['activity_source'] == 'codex_runtime'
    assert agent3['activity_reason'] == 'prompt_submitted_waiting_for_first_signal'
    assert agent3['activity_symbol'] == '◌'
    assert agent3['activity_color'] == 'yellow'
    assert agent3['current_job_id'] == job.job_id
    assert comm['id'] == job.job_id
    assert comm['business_status'] == 'replying'
    assert comm['status_label'] == 'work'
    assert comm['execution_phase'] == 'executing'
    assert comm['execution_phase_reason'] == 'job_running'
    assert comm['recoverable'] is False
    assert comm['block_reason'] is None


def test_project_view_does_not_mark_fresh_running_prompt_idle_as_recoverable(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-provider-idle-running-fresh'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('agent3', project_id=project_id, state=AgentState.BUSY))
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    ProjectNamespaceStateStore(layout).save(
        ProjectNamespaceState(
            project_id=project_id,
            namespace_epoch=3,
            tmux_socket_path=str(layout.ccbd_tmux_socket_path),
            tmux_session_name='ccb-idle-running-fresh',
            layout_version=2,
        )
    )
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    job_id = _submit(dispatcher, project_id, sender='agent1', target='agent3', body='fresh running prompt')
    dispatcher.tick()
    job = dispatcher.get(job_id)
    assert job is not None
    controller = ProjectNamespaceController(
        layout,
        project_id,
        backend_factory=lambda socket_path=None: _ProviderIdleAfterRequestBackend(job.job_id),
    )
    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            namespace_controller=controller,
            clock=lambda: NOW,
        )
    )

    view = service.build_response()['view']
    agent3 = next(agent for agent in view['agents'] if agent['name'] == 'agent3')
    comm = view['comms'][0]

    assert agent3['activity_state'] == 'pending'
    assert agent3['activity_source'] == 'codex_runtime'
    assert agent3['activity_reason'] == 'prompt_submitted_waiting_for_first_signal'
    assert agent3['activity_symbol'] == '◌'
    assert agent3['activity_color'] == 'yellow'
    assert agent3['current_job_id'] == job.job_id
    assert comm['id'] == job.job_id
    assert comm['business_status'] == 'replying'
    assert comm['status_label'] == 'work'
    assert comm['execution_phase'] == 'executing'
    assert comm['execution_phase_reason'] == 'job_running'
    assert comm['recoverable'] is False
    assert comm['block_reason'] is None


def test_project_view_marks_stale_claude_idle_job_as_orphaned(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-claude-orphaned-inbound'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    base = _config()
    config = replace(base, agents={**base.agents, 'agent3': _spec('agent3', 'claude')})
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('agent3', project_id=project_id, state=AgentState.BUSY))
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    ProjectNamespaceStateStore(layout).save(
        ProjectNamespaceState(
            project_id=project_id,
            namespace_epoch=3,
            tmux_socket_path=str(layout.ccbd_tmux_socket_path),
            tmux_session_name='ccb-claude-orphaned-inbound',
            layout_version=2,
        )
    )
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    job_id = _submit(dispatcher, project_id, sender='agent1', target='agent3', body='stale running request')
    dispatcher.tick()
    job = dispatcher.get(job_id)
    assert job is not None
    job = replace(job, updated_at='2026-05-20T11:59:20Z')
    dispatcher._append_job(job)
    controller = ProjectNamespaceController(
        layout,
        project_id,
        backend_factory=lambda socket_path=None: _ProviderIdleAfterRequestBackend(job.job_id),
    )
    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            namespace_controller=controller,
            clock=lambda: NOW,
        )
    )

    comm = service.build_response()['view']['comms'][0]

    assert comm['id'] == job.job_id
    assert comm['business_status'] == 'blocked'
    assert comm['execution_phase'] == 'orphaned'
    assert comm['execution_phase_reason'] == 'orphaned_active_inbound'
    assert comm['block_reason'] == 'provider_prompt_idle'
    assert comm['recoverable'] is True
    evidence = comm['orphaned_active_inbound']
    assert evidence['inbound_event_id']
    assert evidence['inbound_status'] == 'delivering'
    assert evidence['mailbox_state'] == 'delivering'
    assert evidence['mailbox_head_inbound_event_id'] == evidence['inbound_event_id']
    assert evidence['active_inbound_event_id'] == evidence['inbound_event_id']
    assert evidence['lease_state'] == 'acquired'


def test_project_view_does_not_use_legacy_codex_prompt_idle_recovery_without_anchor(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-provider-idle-no-anchor'
    project_root.mkdir()
    layout = PathLayout(project_root)
    project_id = compute_project_id(project_root)
    config = _config()
    registry = AgentRegistry(layout, config)
    registry.upsert(_runtime('agent3', project_id=project_id, state=AgentState.BUSY))
    mount_manager = MountManager(layout, clock=lambda: NOW)
    mount_manager.mark_mounted(project_id=project_id, pid=123, socket_path=layout.ccbd_socket_path, generation=1, started_at=NOW)
    ProjectNamespaceStateStore(layout).save(
        ProjectNamespaceState(
            project_id=project_id,
            namespace_epoch=3,
            tmux_socket_path=str(layout.ccbd_tmux_socket_path),
            tmux_session_name='ccb-idle-no-anchor',
            layout_version=2,
        )
    )
    dispatcher = JobDispatcher(layout, config, registry, clock=lambda: NOW)
    old_job = _job(
        project_id,
        job_id='job_prompt_idle_stale',
        sender='agent1',
        target='agent3',
        status=JobStatus.RUNNING,
        updated_at='2026-05-20T11:57:00Z',
        body='lost anchor in scrollback',
    )
    dispatcher._append_job(old_job)
    dispatcher._state.record(old_job)
    dispatcher._state.mark_active_for(TargetKind.AGENT, 'agent3', old_job.job_id)
    controller = ProjectNamespaceController(
        layout,
        project_id,
        backend_factory=lambda socket_path=None: _ProviderIdleWithoutAnchorBackend(),
    )
    service = ProjectViewService(
        ProjectViewDependencies(
            project_root=project_root,
            project_id=project_id,
            config=config,
            registry=registry,
            mount_manager=mount_manager,
            namespace_state_store=ProjectNamespaceStateStore(layout),
            dispatcher=dispatcher,
            namespace_controller=controller,
            clock=lambda: NOW,
        )
    )

    comm = service.build_response()['view']['comms'][0]

    assert comm['id'] == old_job.job_id
    assert comm['business_status'] == 'replying'
    assert comm['status_label'] == 'work'
    assert comm['recoverable'] is False
    assert comm['block_reason'] is None


def test_activity_resolver_core_states() -> None:
    assert resolve_agent_activity(AgentActivityFacts(namespace_mounted=False), now=NOW).state == 'offline'
    queued = resolve_agent_activity(
        AgentActivityFacts(
            namespace_mounted=True,
            provider='codex',
            runtime_state='idle',
            pane_id='%1',
            pane_state='alive',
            current_job_status='queued',
            current_job_id='job_queued',
            current_job_updated_at=NOW,
        ),
        now=NOW,
    )
    assert queued.state == 'pending'
    assert queued.source == 'ccb_job'
    assert queued.reason == 'job_queued'
    assert queued.current_job_id == 'job_queued'
    running = resolve_agent_activity(
        AgentActivityFacts(
            namespace_mounted=True,
            provider='codex',
            runtime_state='idle',
            pane_id='%1',
            pane_state='alive',
            current_job_status='running',
            current_job_id='job_running',
            current_job_updated_at=NOW,
        ),
        now=NOW,
    )
    assert running.state == 'active'
    assert running.source == 'ccb_job'
    assert running.reason == 'job_running'
    assert running.current_job_id == 'job_running'
    stale = resolve_agent_activity(
        AgentActivityFacts(
            namespace_mounted=True,
            runtime_state='idle',
            pane_id='%1',
            pane_state='alive',
            current_job_status='running',
            current_job_id='job_stale',
            current_job_updated_at='2026-05-20T11:57:00Z',
        ),
        now=NOW,
    )
    assert stale.state == 'pending'
    assert stale.source == 'ccb_job'
    assert stale.reason == 'job_running_stale'
    assert resolve_agent_activity(
        AgentActivityFacts(namespace_mounted=True, pane_id='%1', pane_state='missing', reconcile_state='recovering'),
        now=NOW,
    ).reason == 'pane_missing_recovering'
    assert resolve_agent_activity(
        AgentActivityFacts(namespace_mounted=True, pane_id='%1', pane_state='missing'),
        now=NOW,
    ).reason == 'pane_missing_unowned'

    auth_blocked = resolve_agent_activity(
        AgentActivityFacts(
            namespace_mounted=True,
            provider='codex',
            runtime_state='degraded',
            runtime_health='provider-auth-revoked',
            reconcile_state='blocked',
            pane_id='%1',
            pane_state='missing',
        ),
        now=NOW,
    )
    assert auth_blocked.state == 'failed'
    assert auth_blocked.source == 'runtime_health'
    assert auth_blocked.reason == 'provider_auth_revoked'


def test_activity_resolver_ignores_terminal_job_status_for_top_activity() -> None:
    activity = resolve_agent_activity(
        AgentActivityFacts(
            namespace_mounted=True,
            runtime_state='idle',
            pane_id='%1',
            pane_state='alive',
            current_job_status='failed',
            current_job_id='job_failed',
            current_job_updated_at=NOW,
        ),
        now=NOW,
    )

    assert activity.state == 'idle'
    assert activity.source == 'pane_liveness'
    assert activity.reason == 'pane_alive'
    assert activity.current_job_id is None


def test_activity_resolver_provider_prompt() -> None:
    activity = resolve_agent_activity(
        AgentActivityFacts(
            namespace_mounted=True,
            runtime_state='idle',
            pane_id='%1',
            pane_state='alive',
            pane_text='Do you trust the contents of this directory?\nPress enter to continue\n',
        ),
        now=NOW,
    )

    assert activity.state == 'pending'
    assert activity.source == 'provider_prompt'
    assert activity.reason == 'provider_waiting_for_user'


def test_activity_resolver_ignores_stale_provider_prompt_after_codex_idle_prompt() -> None:
    pane_text = '\n'.join(
        [
            'Do you trust the contents of this directory?',
            'Press enter to continue',
            '',
            '› CCB_REQ_ID: job_old',
            '',
            '• done',
            '',
            '› Implement {feature}',
            '',
            '  gpt-5.5 xhigh · ~/yunwei/test_ccb2',
        ]
    )

    activity = resolve_agent_activity(
        AgentActivityFacts(
            namespace_mounted=True,
            runtime_state='idle',
            pane_id='%1',
            pane_state='alive',
            pane_text=pane_text,
        ),
        now=NOW,
    )

    assert activity.state == 'idle'
    assert activity.source == 'pane_liveness'
    assert activity.reason == 'pane_alive'


def test_activity_resolver_treats_idle_prompt_after_request_as_agent_idle() -> None:
    activity = resolve_agent_activity(
        AgentActivityFacts(
            namespace_mounted=True,
            runtime_state='busy',
            pane_id='%1',
            pane_state='alive',
            pane_text='❯ CCB_REQ_ID: job_prompt_idle_2\n\ncancelled\n\n❯ \n',
        ),
        now=NOW,
    )

    assert activity.state == 'idle'
    assert activity.source == 'pane_liveness'
    assert activity.reason == 'pane_alive'


def test_activity_resolver_ignores_fresh_job_when_provider_prompt_is_idle() -> None:
    activity = resolve_agent_activity(
        AgentActivityFacts(
            namespace_mounted=True,
            runtime_state='busy',
            pane_id='%1',
            pane_state='alive',
            pane_text='❯ CCB_REQ_ID: job_prompt_idle_new\n\n❯ \n',
        ),
        now=NOW,
    )

    assert activity.state == 'idle'
    assert activity.reason == 'pane_alive'


def test_activity_resolver_does_not_use_job_state_without_provider_working_signal() -> None:
    activity = resolve_agent_activity(
        AgentActivityFacts(
            namespace_mounted=True,
            runtime_state='busy',
            pane_id='%1',
            pane_state='alive',
            pane_text='❯ CCB_REQ_ID: job_still_waiting\n\nworking on task\n',
        ),
        now=NOW,
    )

    assert activity.state == 'idle'
    assert activity.source == 'pane_liveness'
    assert activity.reason == 'pane_alive'


def test_activity_resolver_keeps_input_stuck_detection_in_comms_not_agent_status() -> None:
    activity = resolve_agent_activity(
        AgentActivityFacts(
            namespace_mounted=True,
            runtime_state='busy',
            pane_id='%1',
            pane_state='alive',
            pane_text='❯ CCB_REQ_ID: job_input_stuck\n\n  查询北京天气\n',
        ),
        now=NOW,
    )

    assert activity.state == 'idle'
    assert activity.source == 'pane_liveness'
    assert activity.reason == 'pane_alive'


def test_activity_resolver_ignores_fresh_input_stuck_job_for_agent_status() -> None:
    activity = resolve_agent_activity(
        AgentActivityFacts(
            namespace_mounted=True,
            runtime_state='busy',
            pane_id='%1',
            pane_state='alive',
            pane_text='❯ CCB_REQ_ID: job_input_new\n\n  查询北京天气\n',
        ),
        now=NOW,
    )

    assert activity.state == 'idle'
    assert activity.reason == 'pane_alive'


def test_activity_resolver_provider_prompt_does_not_hide_running_tool() -> None:
    activity = resolve_agent_activity(
        AgentActivityFacts(
            namespace_mounted=True,
            runtime_state='busy',
            pane_id='%1',
            pane_state='alive',
            pane_text='❯ CCB_REQ_ID: job_running_tool\n\nBash(sleep 60)\n⎿ Running… (10s)\n\n❯ \n',
        ),
        now=NOW,
    )

    assert activity.state == 'active'
    assert activity.source == 'provider_pane'
    assert activity.reason == 'provider_working'


def test_activity_resolver_provider_working_pane() -> None:
    activity = resolve_agent_activity(
        AgentActivityFacts(
            namespace_mounted=True,
            runtime_state='idle',
            pane_id='%1',
            pane_state='alive',
            pane_text='Working (28s • esc to interrupt)',
        ),
        now=NOW,
    )

    assert activity.state == 'active'
    assert activity.source == 'provider_pane'
    assert activity.reason == 'provider_working'


def test_activity_resolver_disables_legacy_codex_pane_heuristics() -> None:
    activity = resolve_agent_activity(
        AgentActivityFacts(
            namespace_mounted=True,
            provider='codex',
            runtime_state='idle',
            pane_id='%1',
            pane_state='alive',
            pane_text='Working (28s • esc to interrupt)',
        ),
        now=NOW,
    )

    assert activity.state == 'idle'
    assert activity.source == 'pane_liveness'
    assert activity.reason == 'pane_alive'


def test_activity_resolver_codex_runtime_status_presentation() -> None:
    start = resolve_agent_activity(
        AgentActivityFacts(
            namespace_mounted=True,
            runtime_state='idle',
            pane_id='%1',
            pane_state='alive',
            current_job_status='running',
            current_job_id='job_start',
            current_job_updated_at=NOW,
            provider_runtime_state='start',
            provider_runtime_source='codex_runtime',
            provider_runtime_reason='prompt_submitted_waiting_for_first_signal',
        ),
        now=NOW,
    )
    working = resolve_agent_activity(
        AgentActivityFacts(
            namespace_mounted=True,
            runtime_state='idle',
            pane_id='%1',
            pane_state='alive',
            provider_runtime_state='working',
            provider_runtime_source='codex_runtime',
            provider_runtime_reason='codex_working_status_line',
        ),
        now=NOW,
    )
    free = resolve_agent_activity(
        AgentActivityFacts(
            namespace_mounted=True,
            runtime_state='idle',
            pane_id='%1',
            pane_state='alive',
            provider_runtime_state='free',
            provider_runtime_source='codex_runtime',
            provider_runtime_reason='codex_session_task_complete',
        ),
        now=NOW,
    )
    interrupted = resolve_agent_activity(
        AgentActivityFacts(
            namespace_mounted=True,
            runtime_state='idle',
            pane_id='%1',
            pane_state='alive',
            provider_runtime_state='interrupted',
            provider_runtime_source='codex_runtime',
            provider_runtime_reason='codex_session_turn_aborted',
        ),
        now=NOW,
    )
    unknown = resolve_agent_activity(
        AgentActivityFacts(
            namespace_mounted=True,
            provider='codex',
            runtime_state='idle',
            pane_id='%1',
            pane_state='alive',
            pane_text='Working (28s • esc to interrupt)',
            provider_runtime_state='unknown',
            provider_runtime_source='codex_runtime',
            provider_runtime_reason='no_known_status_pattern',
        ),
        now=NOW,
    )

    assert start.to_record()['activity_state'] == 'pending'
    assert start.to_record()['activity_symbol'] == '◌'
    assert start.to_record()['activity_color'] == 'yellow'
    assert start.to_record()['activity_source'] == 'codex_runtime'
    assert start.to_record()['activity_reason'] == 'prompt_submitted_waiting_for_first_signal'
    assert working.to_record()['activity_state'] == 'active'
    assert working.to_record()['activity_symbol'] == '●'
    assert working.to_record()['activity_color'] == 'green'
    assert free.to_record()['activity_state'] == 'idle'
    assert free.to_record()['activity_symbol'] == '◇'
    assert free.to_record()['activity_color'] == 'blue'
    assert interrupted.to_record()['activity_state'] == 'pending'
    assert interrupted.to_record()['activity_symbol'] == '!'
    assert interrupted.to_record()['activity_color'] == 'yellow'
    assert interrupted.to_record()['activity_source'] == 'codex_runtime'
    assert interrupted.to_record()['activity_reason'] == 'codex_session_turn_aborted'
    assert unknown.to_record()['activity_state'] == 'pending'
    assert unknown.to_record()['activity_symbol'] == '?'
    assert unknown.to_record()['activity_color'] == 'gray'
    assert unknown.to_record()['activity_source'] == 'codex_runtime'
    assert unknown.to_record()['activity_reason'] == 'no_known_status_pattern'


def test_activity_resolver_claude_runtime_status_presentation() -> None:
    tool = resolve_agent_activity(
        AgentActivityFacts(
            namespace_mounted=True,
            runtime_state='idle',
            pane_id='%2',
            pane_state='alive',
            provider_runtime_state='tool_running',
            provider_runtime_source='claude_runtime',
            provider_runtime_reason='claude_activity_tool_running',
        ),
        now=NOW,
    )
    waiting = resolve_agent_activity(
        AgentActivityFacts(
            namespace_mounted=True,
            runtime_state='idle',
            pane_id='%2',
            pane_state='alive',
            provider_runtime_state='waiting_for_user',
            provider_runtime_source='claude_runtime',
            provider_runtime_reason='claude_activity_waiting_for_user',
        ),
        now=NOW,
    )
    unknown = resolve_agent_activity(
        AgentActivityFacts(
            namespace_mounted=True,
            runtime_state='idle',
            pane_id='%2',
            pane_state='alive',
            provider_runtime_state='unknown',
            provider_runtime_source='claude_runtime',
            provider_runtime_reason='no_known_session_signal',
        ),
        now=NOW,
    )

    assert tool.to_record()['activity_state'] == 'active'
    assert tool.to_record()['activity_symbol'] == '◆'
    assert tool.to_record()['activity_source'] == 'claude_runtime'
    assert waiting.to_record()['activity_state'] == 'pending'
    assert waiting.to_record()['activity_symbol'] == '?'
    assert waiting.to_record()['activity_source'] == 'claude_runtime'
    assert unknown.to_record()['activity_state'] == 'pending'
    assert unknown.to_record()['activity_symbol'] == '?'
    assert unknown.to_record()['activity_color'] == 'gray'
    assert unknown.to_record()['activity_source'] == 'claude_runtime'


def test_activity_resolver_provider_background_terminal_running_after_prompt() -> None:
    activity = resolve_agent_activity(
        AgentActivityFacts(
            namespace_mounted=True,
            runtime_state='idle',
            pane_id='%1',
            pane_state='alive',
            pane_text=(
                '• Working (20s • esc to interrupt) · 1 background terminal running · /ps to view · /stop to close\n'
                '\n'
                '› Use /skills to list available skills\n'
                '\n'
                '  gpt-5.5 medium · ~/yunwei/test_ccb2\n'
            ),
        ),
        now=NOW,
    )

    assert activity.state == 'active'
    assert activity.source == 'provider_pane'
    assert activity.reason == 'provider_working'


def test_activity_resolver_claude_scheduled_task_shell_running_after_prompt() -> None:
    activity = resolve_agent_activity(
        AgentActivityFacts(
            namespace_mounted=True,
            runtime_state='idle',
            pane_id='%1',
            pane_state='alive',
            pane_text=(
                '✻ Running scheduled task (Jun 11 9:33pm)\n'
                '\n'
                '● 已完成第29次，正在执行第30次。进度：29/50（58%）。\n'
                '\n'
                '✻ Sautéed for 9s · 1 shell still running\n'
                '\n'
                '─────────────────────────────────────────────────────\n'
                '❯ CCB_REQ_ID: job_86550847f237\n'
                '\n'
                '  再执行一次：循环50次，每次等待30s。\n'
                '─────────────────────────────────────────────────────\n'
                '  🤖 Sonnet 4.6 | 📁 test_ccb2 | ⏵⏵ bypass permissions on · 1 shell\n'
            ),
        ),
        now=NOW,
    )

    assert activity.state == 'active'
    assert activity.source == 'provider_pane'
    assert activity.reason == 'provider_working'


def test_activity_resolver_ignores_codex_hook_activity() -> None:
    pane_text = '\n'.join(
        [
            '╭───────────────────────────────────────────────╮',
            '│ >_ OpenAI Codex (v0.134.0)                    │',
            '│ model:       gpt-5.5 xhigh   /model to change │',
            '╰───────────────────────────────────────────────╯',
            '',
            '› trigger CCB_REFUSED_SMOKE and stop',
            '',
            '• Working (18s • esc to interrupt)',
            '• Messages to be submitted after next tool call (press esc to interrupt and send immediately)',
            '  ↳ trigger CCB_NEXT_AFTER_FAIL and stop',
            '',
            '› Use /skills to list available skills',
            '',
            '  gpt-5.5 xhigh · ~/yunwei/test_ccb2',
        ]
    )

    activity = resolve_agent_activity(
        AgentActivityFacts(
            namespace_mounted=True,
            provider='codex',
            runtime_state='idle',
            pane_id='%3',
            pane_state='alive',
            pane_text=pane_text,
            provider_activity_state='active',
            provider_activity_source='codex_hook',
            provider_activity_reason='provider_userpromptsubmit',
            provider_activity_updated_at=NOW,
        ),
        now=NOW,
    )

    assert activity.state == 'idle'
    assert activity.source == 'pane_liveness'
    assert activity.reason == 'pane_alive'


def test_activity_resolver_ignores_stale_provider_working_history() -> None:
    pane_text = '\n'.join(
        [
            '• Booting MCP server: puppeteer (0s • esc to interrupt)',
            '',
            '› Find and fix a bug in @filename',
            '',
            '╭───────────────────────────────────────────────╮',
            '│ >_ OpenAI Codex (v0.133.0)                    │',
            '│                                               │',
            '│ model:       gpt-5.5 xhigh   /model to change │',
            '│ directory:   ~/yunwei/ccb_sidebar_test        │',
            '│ permissions: YOLO mode                        │',
            '╰───────────────────────────────────────────────╯',
            '',
            '  gpt-5.5 xhigh · ~/yunwei/ccb_sidebar_test',
            '',
            '',
            '',
            '',
            '',
            '',
            '',
            '',
            '',
            '',
            '',
        ]
    )
    activity = resolve_agent_activity(
        AgentActivityFacts(
            namespace_mounted=True,
            runtime_state='idle',
            pane_id='%3',
            pane_state='alive',
            pane_text=pane_text,
        ),
        now=NOW,
    )

    assert activity.state == 'idle'
    assert activity.source == 'pane_liveness'
    assert activity.reason == 'pane_alive'


def test_activity_resolver_ignores_stale_working_history_after_tail_prompt() -> None:
    pane_text = '\n'.join(
        [
            'Working (28s • esc to interrupt)',
            '',
            '› Find and fix a bug in @filename',
            '',
            '• fixed',
            '',
            '› Run /review on my current changes',
            '',
            '  gpt-5.5 xhigh · ~/yunwei/test_ccb2',
        ]
    )

    activity = resolve_agent_activity(
        AgentActivityFacts(
            namespace_mounted=True,
            runtime_state='idle',
            pane_id='%3',
            pane_state='alive',
            pane_text=pane_text,
        ),
        now=NOW,
    )

    assert activity.state == 'idle'
    assert activity.source == 'pane_liveness'
    assert activity.reason == 'pane_alive'
