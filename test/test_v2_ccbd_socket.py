from __future__ import annotations

import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
from types import SimpleNamespace

import pytest

from agents.models import AgentRuntime, AgentState, AgentRestoreState, RestoreMode
from agents.store import AgentRuntimeStore
from ccbd.api_models import DeliveryScope, MessageEnvelope
from ccbd.app import CcbdApp
from ccbd.app_runtime.lifecycle import hot_loop_work_pending
from ccbd.services.dispatcher import JobDispatcher
from ccbd.services.registry import AgentRegistry
from ccbd.services.lifecycle import build_lifecycle
from ccbd.services.project_namespace_state import ProjectNamespaceEvent, ProjectNamespaceState
from ccbd.socket_client import CcbdClient, CcbdClientError
from ccbd.socket_server import CcbdSocketServer
from completion.models import CompletionConfidence, CompletionDecision, CompletionStatus
from message_bureau import AttemptStore, MessageStore
from message_bureau.reply_payloads import delivery_job_id_from_payload
from mailbox_kernel import InboundEventStatus, InboundEventStore, InboundEventType
from project.ids import compute_project_id
from project.resolver import ProjectContext
from provider_execution.registry import build_default_execution_registry
from provider_execution.service import ExecutionService
from provider_execution.state_store import ExecutionStateStore


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def _agent_config_text(*pairs: tuple[str, str]) -> str:
    return ','.join(f'{agent_name}:{provider}' for agent_name, provider in pairs) + '\n'


def _single_agent_config_text(agent_name: str, provider: str) -> str:
    return _agent_config_text((agent_name, provider))


def _prepare_project(project_root: Path, config_text: str):
    project_root.mkdir()
    config_dir = project_root / '.ccb'
    _write(config_dir / 'ccb.config', config_text)
    return ProjectContext(
        cwd=project_root,
        project_root=project_root,
        config_dir=config_dir,
        project_id=compute_project_id(project_root),
        source='test',
    )


def _runtime(agent_name: str, *, project_id: str, workspace_path: str, pid: int) -> AgentRuntime:
    return AgentRuntime(
        agent_name=agent_name,
        state=AgentState.IDLE,
        pid=pid,
        started_at='2026-03-18T00:00:00Z',
        last_seen_at='2026-03-18T00:00:00Z',
        runtime_ref=f'{agent_name}-runtime',
        session_ref=f'{agent_name}-session',
        workspace_path=workspace_path,
        project_id=project_id,
        backend_type='tmux',
        queue_depth=0,
        socket_path=None,
        health='healthy',
    )


def _wait_for(path: Path, timeout: float = 10.0) -> None:
    deadline = time.time() + timeout
    last_error: str | None = None
    while time.time() < deadline:
        if path.exists():
            if path.suffix != '.sock':
                return
            try:
                payload = CcbdClient(path, timeout_s=1.0).ping('ccbd')
                diagnostics = payload.get('diagnostics')
                stage = diagnostics.get('startup_stage') if isinstance(diagnostics, dict) else None
                if stage in {None, '', 'mounted'}:
                    return
            except CcbdClientError as exc:
                last_error = str(exc)
        time.sleep(0.05)
    suffix = f' last_error={last_error!r}' if last_error else ''
    raise AssertionError(f'timed out waiting for {path}{suffix}')


def _wait_for_job_status(client: CcbdClient, job_id: str, expected: str, *, timeout: float = 3.0) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = client.get(job_id)
        if last['status'] == expected:
            return last
        time.sleep(0.05)
    raise AssertionError(f'expected job {job_id} status={expected!r}; last={last!r}')


def _wait_for_job_payload(client: CcbdClient, job_id: str, predicate, *, timeout: float = 3.0) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = client.get(job_id)
        if predicate(last):
            return last
        time.sleep(0.05)
    raise AssertionError(f'expected job {job_id} payload predicate; last={last!r}')


def _wait_for_watch_payload(client: CcbdClient, job_id: str, predicate, *, timeout: float = 3.0) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = client.watch(job_id)
        if predicate(last):
            return last
        time.sleep(0.05)
    raise AssertionError(f'expected watch {job_id} payload predicate; last={last!r}')


def _wait_for_reply_delivery_scheduled(client: CcbdClient, agent_name: str, *, timeout: float = 3.0) -> dict:
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        last = client.inbox(agent_name)
        head = last.get('head') if isinstance(last, dict) else None
        if isinstance(head, dict) and delivery_job_id_from_payload(head.get('payload_ref')):
            return last
        time.sleep(0.05)
    raise AssertionError(f'expected reply delivery scheduling for {agent_name}; last={last!r}')


def _decision(*, status: CompletionStatus = CompletionStatus.COMPLETED, reply: str = 'done') -> CompletionDecision:
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
        finished_at='2026-03-18T00:00:10Z',
        diagnostics={},
    )


def _freeze_next_job_id(app: CcbdApp, monkeypatch: pytest.MonkeyPatch, job_id: str) -> None:
    original_new_id = app.dispatcher._new_id

    def _new_id(kind: str) -> str:
        if kind == 'job':
            return job_id
        return original_new_id(kind)

    monkeypatch.setattr(app.dispatcher, '_new_id', _new_id)


@pytest.fixture
def noninteractive_socket_transport(monkeypatch):
    # These protocol tests inject runtimes/readers without a real composer.
    # Model their unprotected fake transport at the new pre-claim boundary;
    # actual empty/draft/unknown FIFO behavior is in test_input_draft_fifo.py.
    monkeypatch.setattr('provider_execution.draft_guard.resolve_job_target', lambda job, context: None)


@pytest.mark.usefixtures('noninteractive_socket_transport')
def test_ccbd_socket_roundtrip_and_shutdown(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    ctx = _prepare_project(project_root, _single_agent_config_text('codex', 'codex'))
    app = CcbdApp(project_root)
    app.registry.upsert(
        _runtime(
            'codex',
            project_id=ctx.project_id,
            workspace_path=str(app.paths.workspace_path('codex')),
            pid=777,
        )
    )

    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for(app.paths.ccbd_socket_path)

    client = CcbdClient(app.paths.ccbd_socket_path)
    ping = client.ping('codex')
    assert ping['agent_name'] == 'codex'
    assert ping['provider'] == 'codex'

    submit = client.submit(
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
    job_id = submit['job_id']
    app.dispatcher.tick()
    running = _wait_for_job_status(client, job_id, 'running')
    assert running['status'] == 'running'

    app.dispatcher.complete(
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
    completed = client.get(job_id)
    assert completed['status'] == 'completed'
    assert completed['reply'] == 'done'
    assert completed['generation'] == 1
    completed_again = client.get(job_id)
    assert completed_again['status'] == 'completed'
    assert completed_again['reply'] == 'done'
    assert completed_again['completion_reason'] == 'task_complete'
    assert completed_again['completion_confidence'] == 'exact'

    watch = client.watch(job_id)
    assert watch['terminal'] is True
    assert watch['generation'] == 1
    event_types = [event['type'] for event in watch['events']]
    assert event_types[:2] == ['job_accepted', 'job_started']
    assert 'completion_terminal' in event_types
    assert event_types[-1] == 'job_completed'
    watch_again = client.watch(job_id)
    assert watch_again['terminal'] is True
    assert watch_again['reply'] == 'done'

    queue_all = client.queue('all')
    assert queue_all['target'] == 'all'
    assert queue_all['agent_count'] >= 1

    shutdown = client.shutdown()
    assert shutdown['state'] == 'unmounted'
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert app.mount_manager.load_state().mount_state.value == 'unmounted'


@pytest.mark.usefixtures('noninteractive_socket_transport')
def test_ccbd_socket_get_and_watch_resolve_callback_root_final_reply(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-callback-get-watch'
    ctx = _prepare_project(project_root, _agent_config_text(('main', 'codex'), ('worker', 'codex')))
    app = CcbdApp(project_root)
    app.registry.upsert(
        _runtime(
            'main',
            project_id=ctx.project_id,
            workspace_path=str(app.paths.workspace_path('main')),
            pid=777,
        )
    )
    app.registry.upsert(
        _runtime(
            'worker',
            project_id=ctx.project_id,
            workspace_path=str(app.paths.workspace_path('worker')),
            pid=778,
        )
    )

    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for(app.paths.ccbd_socket_path)
    client = CcbdClient(app.paths.ccbd_socket_path)

    parent_job_id = client.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent='main',
            from_actor='user',
            body='delegate and return final',
            task_id='task-callback',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )['job_id']
    app.dispatcher.tick()
    _wait_for_job_status(client, parent_job_id, 'running')

    child_job_id = client.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent='worker',
            from_actor='main',
            body='collect evidence',
            task_id='task-callback',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
            route_options={'mode': 'chain'},
        )
    )['job_id']
    app.dispatcher.complete(parent_job_id, _decision(reply='delegated to worker'))

    delegated = client.get(parent_job_id)
    assert delegated['status'] == 'completed'
    assert delegated['reply'] == ''
    assert delegated['completion_reason'] == 'callback_pending'
    assert delegated['visible_reply_source'] == 'callback_delegated_pending'

    app.dispatcher.tick()
    app.dispatcher.complete(child_job_id, _decision(reply='worker result'))
    app.dispatcher.tick()
    edge = app.dispatcher._message_bureau.callback_edge_for_child_job(child_job_id)
    assert edge is not None
    assert edge.continuation_job_id
    app.dispatcher.complete(edge.continuation_job_id, _decision(reply='FINAL CALLBACK RESULT'))

    completed = client.get(parent_job_id)
    assert completed['reply'] == 'FINAL CALLBACK RESULT'
    assert completed['completion_reason'] == 'task_complete'
    assert completed['visible_reply_source'] == 'message_bureau_reply'
    assert completed['visible_reply_id']

    watched = client.watch(parent_job_id)
    assert watched['terminal'] is True
    assert watched['reply'] == 'FINAL CALLBACK RESULT'
    assert watched['visible_reply_source'] == 'message_bureau_reply'

    shutdown = client.shutdown()
    assert shutdown['state'] == 'unmounted'
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_ccbd_control_plane_metrics_record_queue_wait_and_handler_durations(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-metrics'
    ctx = _prepare_project(project_root, _single_agent_config_text('codex', 'codex'))
    app = CcbdApp(project_root)
    app.registry.upsert(
        _runtime(
            'codex',
            project_id=ctx.project_id,
            workspace_path=str(app.paths.workspace_path('codex')),
            pid=777,
        )
    )

    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for(app.paths.ccbd_socket_path)

    client = CcbdClient(app.paths.ccbd_socket_path)
    client.ping('ccbd')
    submit = client.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent='codex',
            from_actor='user',
            body='hello',
            task_id='task-metrics',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )

    assert submit['job_id'].startswith('job_')
    assert app.control_plane_metrics.last_request_queue_wait_s is not None
    assert app.control_plane_metrics.last_request_queue_wait_s >= 0.0
    assert app.control_plane_metrics.last_ping_duration_s is not None
    assert app.control_plane_metrics.last_ping_duration_s >= 0.0
    assert app.control_plane_metrics.last_handler_latency_s_by_op['ping'] >= 0.0
    assert app.control_plane_metrics.last_submit_duration_s is not None
    assert app.control_plane_metrics.last_submit_duration_s >= 0.0
    assert app.control_plane_metrics.last_handler_latency_s_by_op['submit'] >= 0.0
    assert app.control_plane_metrics.pending_maintenance_ticks in (0, 1)

    app.heartbeat()
    assert app.control_plane_metrics.last_maintenance_duration_s is not None
    assert app.control_plane_metrics.last_maintenance_duration_s >= 0.0
    assert app.control_plane_metrics.last_heartbeat_duration_s is not None
    assert app.control_plane_metrics.last_heartbeat_duration_s >= 0.0

    client.shutdown()
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_ccbd_heartbeat_records_step_metrics_without_background_worker(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-heartbeat-metrics'
    ctx = _prepare_project(project_root, _single_agent_config_text('codex', 'codex'))
    app = CcbdApp(project_root)
    app.lease = SimpleNamespace(generation=3)
    app.lifecycle_store.save(
        build_lifecycle(
            project_id=ctx.project_id,
            occurred_at='2026-03-18T00:00:05Z',
            desired_state='running',
            phase='mounted',
            generation=3,
            keeper_pid=app.keeper_pid,
            owner_pid=app.pid,
            owner_daemon_instance_id=app.daemon_instance_id,
            config_signature=str(app.config_identity.get('config_signature') or '').strip() or None,
            socket_path=app.paths.ccbd_socket_path,
        )
    )
    monkeypatch.setattr(app.mount_manager, 'refresh_heartbeat', lambda **kwargs: app.lease)
    monkeypatch.setattr(app.health_monitor, 'check_all', lambda: {})
    monkeypatch.setattr(app.runtime_supervision, 'reconcile_once', lambda: {'codex': 'healthy'})
    monkeypatch.setattr(app.dispatcher, 'reconcile_runtime_views', lambda: None)
    monkeypatch.setattr(app.dispatcher, 'tick', lambda: ())
    monkeypatch.setattr(app.dispatcher, 'poll_completions', lambda: ())
    monkeypatch.setattr(app.job_heartbeat, 'tick', lambda dispatcher: ())

    app.heartbeat()

    assert app.control_plane_metrics.last_heartbeat_duration_s is not None
    assert app.control_plane_metrics.heartbeat_step_duration_s['health_monitor'] >= 0.0
    assert app.control_plane_metrics.heartbeat_step_duration_s['runtime_supervision'] >= 0.0
    assert 'frontdesk_session_observer' not in app.control_plane_metrics.heartbeat_step_duration_s
    assert app.control_plane_metrics.last_heartbeat_agents_inspected == 1
    assert app.control_plane_metrics.last_heartbeat_runtime_store_writes == 0


def test_ccbd_bootstrap_arms_job_heartbeat_reaper(tmp_path: Path) -> None:
    # Regression: the daemon must construct the job heartbeat service with a
    # positive terminal_notice_count so a wedged (no-progress) 'ask' job is
    # eventually terminated instead of starving its mailbox queue forever.
    # If this drifts back to None the 2026-07-09 comm stall class reappears.
    from ccbd.app_runtime.bootstrap import JOB_HEARTBEAT_TERMINAL_NOTICE_COUNT

    project_root = tmp_path / 'repo-heartbeat-reaper-armed'
    _prepare_project(project_root, _single_agent_config_text('codex', 'codex'))
    app = CcbdApp(project_root)

    assert isinstance(JOB_HEARTBEAT_TERMINAL_NOTICE_COUNT, int)
    assert JOB_HEARTBEAT_TERMINAL_NOTICE_COUNT > 0
    assert app.job_heartbeat._terminal_notice_count == JOB_HEARTBEAT_TERMINAL_NOTICE_COUNT


def test_ccbd_socket_bad_client_does_not_block_later_ping(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-bad-client'
    _prepare_project(project_root, _single_agent_config_text('demo', 'fake'))
    app = CcbdApp(project_root)

    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for(app.paths.ccbd_socket_path)

    bad = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        bad.connect(str(app.paths.ccbd_socket_path))
        time.sleep(0.1)

        ping = CcbdClient(app.paths.ccbd_socket_path, timeout_s=1.5).ping('ccbd')

        assert ping['project_id'] == app.project_id
    finally:
        bad.close()
        app.request_shutdown()
        thread.join(timeout=2)

    assert not thread.is_alive()


def test_ccbd_socket_shutdown_does_not_remove_replaced_socket_path(tmp_path: Path) -> None:
    with tempfile.TemporaryDirectory(prefix='ccb-sock-', dir=str(Path(tempfile.gettempdir()))) as temp_dir:
        socket_path = Path(temp_dir) / f'ccbd-{os.getpid()}.sock'
        old_server = CcbdSocketServer(socket_path)
        old_server.listen()
        old_stat = socket_path.stat()

        socket_path.unlink()

        new_server = CcbdSocketServer(socket_path)
        new_server.listen()
        new_stat = socket_path.stat()

        assert (old_stat.st_dev, old_stat.st_ino) != (new_stat.st_dev, new_stat.st_ino)

        old_server.shutdown()
        assert socket_path.exists()
        current_stat = socket_path.stat()
        assert (current_stat.st_dev, current_stat.st_ino) == (new_stat.st_dev, new_stat.st_ino)

        new_server.shutdown()
        assert not socket_path.exists()


def test_socket_server_uses_larger_listen_backlog(tmp_path: Path, monkeypatch) -> None:
    socket_path = tmp_path / 'ccbd.sock'
    listen_backlogs: list[int] = []

    class _FakeSocket:
        def bind(self, path: str) -> None:
            assert path == str(socket_path)

        def listen(self, backlog: int) -> None:
            listen_backlogs.append(backlog)

        def settimeout(self, timeout: float) -> None:
            del timeout

        def close(self) -> None:
            pass

    monkeypatch.setattr('ccbd.control_plane_transport.unix.socket.socket', lambda *args, **kwargs: _FakeSocket())
    monkeypatch.setattr('ccbd.control_plane_transport.unix.bound_socket_identity', lambda path: (1, 2))

    server = CcbdSocketServer(socket_path)
    server.listen()
    server.shutdown()

    assert listen_backlogs == [128]


def test_socket_server_bounds_accepted_connection_queue(tmp_path: Path) -> None:
    socket_path = tmp_path / 'ccbd.sock'
    server = CcbdSocketServer(socket_path)
    closed: list[int] = []

    class _Conn:
        def __init__(self, index: int) -> None:
            self.index = index

        def close(self) -> None:
            closed.append(self.index)

    import ccbd.socket_server_runtime.loop as socket_loop

    for index in range(140):
        socket_loop.enqueue_connection(server, _Conn(index))

    assert server._connection_queue.qsize() == 128
    assert closed == list(range(12))


def test_socket_worker_drops_stale_queued_connection(tmp_path: Path, monkeypatch) -> None:
    socket_path = tmp_path / 'ccbd.sock'
    server = CcbdSocketServer(socket_path)
    closed: list[str] = []
    handled: list[str] = []

    class _Conn:
        def close(self) -> None:
            closed.append('closed')

    import ccbd.socket_server_runtime.loop as socket_loop

    monkeypatch.setattr(socket_loop.time, 'monotonic', lambda: 10.5)
    server._connection_queue.put_nowait((_Conn(), 8.0))
    server._connection_queue.put_nowait(server._worker_sentinel)
    server._handle_connection = lambda conn: handled.append('handled') or 'ping'  # type: ignore[method-assign]

    socket_loop.worker_loop(server, interval=0.05, on_tick=None)

    assert closed == ['closed']
    assert handled == []


def test_socket_server_timeout_after_shutdown_does_not_run_tick(tmp_path: Path) -> None:
    socket_path = tmp_path / 'ccbd.sock'
    server = CcbdSocketServer(socket_path)
    tick_calls: list[str] = []

    class _TimeoutAfterShutdownSocket:
        def settimeout(self, timeout: float | None) -> None:
            del timeout

        def accept(self):
            server._stop_event.set()
            server._server = None
            raise socket.timeout('timed out')

    def _fake_listen() -> None:
        server._stop_event.clear()
        server._server = _TimeoutAfterShutdownSocket()

    server.listen = _fake_listen  # type: ignore[method-assign]

    server.serve_forever(poll_interval=0.05, on_tick=lambda: tick_calls.append('tick'))

    assert tick_calls == []


def test_socket_server_propagates_worker_tick_errors() -> None:
    with tempfile.TemporaryDirectory(prefix='ccb-sock-', dir=str(Path(tempfile.gettempdir()))) as temp_dir:
        server = CcbdSocketServer(Path(temp_dir) / 'ccbd.sock')

        try:
            with pytest.raises(RuntimeError, match='tick boom'):
                server.serve_forever(
                    poll_interval=0.01,
                    on_tick=lambda: (_ for _ in ()).throw(RuntimeError('tick boom')),
                )
        finally:
            server.shutdown()

        assert server._worker_thread is None


def test_ccbd_stop_all_does_not_run_post_shutdown_heartbeat(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-stop-all'
    ctx = _prepare_project(project_root, _single_agent_config_text('demo', 'fake'))
    app = CcbdApp(project_root)
    destroy_events: list[str] = []
    app.project_namespace.ensure = lambda: SimpleNamespace(  # type: ignore[method-assign]
        tmux_socket_path=str(app.paths.ccbd_tmux_socket_path),
        tmux_session_name=app.paths.ccbd_tmux_session_name,
        namespace_epoch=1,
    )
    app.project_namespace.destroy = (  # type: ignore[method-assign]
        lambda **kwargs: destroy_events.append('destroy')
        or SimpleNamespace(destroyed=True, namespace_epoch=1)
    )

    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for(app.paths.ccbd_socket_path)

    client = CcbdClient(app.paths.ccbd_socket_path)
    started = client.start(agent_names=('demo',), restore=False, auto_permission=False)
    assert started['started'] == ['demo']
    assert app.start_policy_store.load() is not None

    stopped = client.stop_all(force=False)
    assert stopped['state'] == 'unmounted'
    assert destroy_events == []

    thread.join(timeout=2)
    assert not thread.is_alive()
    assert destroy_events == ['destroy']

    runtime = AgentRuntimeStore(app.paths).load('demo')
    assert runtime is not None
    assert runtime.state is AgentState.STOPPED
    assert runtime.desired_state == 'stopped'
    assert runtime.reconcile_state == 'stopped'
    assert runtime.runtime_ref is None
    lifecycle = app.lifecycle_store.load()
    assert lifecycle is not None
    assert lifecycle.desired_state == 'stopped'
    assert lifecycle.phase == 'unmounted'


def test_ccbd_stop_all_does_not_scan_project_processes_during_rpc(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-stop-all-no-project-scan'
    _prepare_project(project_root, _single_agent_config_text('demo', 'fake'))
    app = CcbdApp(project_root)
    app.project_namespace.ensure = lambda: SimpleNamespace(  # type: ignore[method-assign]
        tmux_socket_path=str(app.paths.ccbd_tmux_socket_path),
        tmux_session_name=app.paths.ccbd_tmux_session_name,
        namespace_epoch=1,
    )
    app.project_namespace.destroy = lambda **kwargs: SimpleNamespace(destroyed=True, namespace_epoch=1)  # type: ignore[method-assign]
    captured: dict[str, object] = {}

    def _capture_terminate_runtime_pids(
        *,
        project_root,
        pid_candidates,
        is_pid_alive_fn,
        pid_matches_project_fn,
        terminate_pid_tree_fn,
        remove_pid_files_fn,
        collect_project_process_candidates_fn=None,
    ):
        del project_root
        del pid_candidates
        del is_pid_alive_fn
        del pid_matches_project_fn
        del terminate_pid_tree_fn
        del remove_pid_files_fn
        captured['collect_project_process_candidates_fn'] = collect_project_process_candidates_fn

    monkeypatch.setattr(
        'ccbd.stop_flow_runtime.pid_cleanup._terminate_runtime_pids_impl',
        _capture_terminate_runtime_pids,
    )

    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for(app.paths.ccbd_socket_path)

    client = CcbdClient(app.paths.ccbd_socket_path)
    started = client.start(agent_names=('demo',), restore=False, auto_permission=False)
    assert started['started'] == ['demo']

    stopped = client.stop_all(force=False)
    assert stopped['state'] == 'unmounted'

    thread.join(timeout=2)
    assert not thread.is_alive()
    assert captured['collect_project_process_candidates_fn'] is None


def test_ccbd_stop_all_force_terminalizes_running_jobs_before_restart_restore(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-stop-all-running-job'
    _prepare_project(project_root, _single_agent_config_text('demo', 'fake'))
    app = CcbdApp(project_root)
    app.project_namespace.ensure = lambda: SimpleNamespace(  # type: ignore[method-assign]
        tmux_socket_path=str(app.paths.ccbd_tmux_socket_path),
        tmux_session_name=app.paths.ccbd_tmux_session_name,
        namespace_epoch=1,
    )
    app.project_namespace.destroy = lambda **kwargs: SimpleNamespace(destroyed=True, namespace_epoch=1)  # type: ignore[method-assign]

    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for(app.paths.ccbd_socket_path)

    client = CcbdClient(app.paths.ccbd_socket_path)
    started = client.start(agent_names=('demo',), restore=False, auto_permission=False)
    assert started['started'] == ['demo']

    receipt = client.submit(
        MessageEnvelope(
            project_id=app.project_id,
            to_agent='demo',
            from_actor='user',
            body='stop me',
            task_id='fake;latency_ms=1500',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    job_id = receipt['job_id']
    running = _wait_for_job_status(client, job_id, 'running')
    assert running['status'] == 'running'
    _wait_for(app.paths.execution_state_path(job_id))

    stopped = client.stop_all(force=True)
    assert stopped['state'] == 'unmounted'

    thread.join(timeout=2)
    assert not thread.is_alive()

    registry = AgentRegistry(app.paths, app.config)
    runtime = AgentRuntimeStore(app.paths).load('demo')
    assert runtime is not None
    registry.upsert(runtime)
    restarted = JobDispatcher(
        app.paths,
        app.config,
        registry,
        execution_service=ExecutionService(
            build_default_execution_registry(),
            clock=lambda: '2026-03-18T00:00:05Z',
            state_store=ExecutionStateStore(app.paths),
        ),
        clock=lambda: '2026-03-18T00:00:05Z',
    )

    assert restarted.restore_running_jobs() == ()
    terminal = restarted.get(job_id)
    assert terminal is not None
    assert terminal.status.value == 'incomplete'
    assert terminal.terminal_decision is not None
    assert terminal.terminal_decision['reason'] == 'project_shutdown'
    assert terminal.terminal_decision['diagnostics']['shutdown_reason'] == 'stop_all'
    assert terminal.terminal_decision['diagnostics']['forced'] is True
    assert not app.paths.execution_state_path(job_id).exists()


def test_ccbd_socket_rejects_mutating_requests_while_lifecycle_stopping(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo-stopping-guard'
    ctx = _prepare_project(project_root, _single_agent_config_text('codex', 'codex'))
    app = CcbdApp(project_root)
    app.registry.upsert(
        _runtime(
            'codex',
            project_id=ctx.project_id,
            workspace_path=str(app.paths.workspace_path('codex')),
            pid=777,
        )
    )
    # Keep the socket alive long enough to exercise the request guard itself;
    # lifecycle supervision shutdown is covered by the adjacent heartbeat test.
    monkeypatch.setattr(app, 'heartbeat', lambda: app.lease)

    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for(app.paths.ccbd_socket_path)

    app.lifecycle_store.save(
        build_lifecycle(
            project_id=ctx.project_id,
            occurred_at='2026-03-18T00:00:05Z',
            desired_state='stopped',
            phase='stopping',
            generation=app.lease.generation if app.lease is not None else 1,
            keeper_pid=app.keeper_pid,
            owner_pid=app.pid,
            owner_daemon_instance_id=app.daemon_instance_id,
            config_signature=str(app.config_identity.get('config_signature') or '').strip() or None,
            socket_path=app.paths.ccbd_socket_path,
            shutdown_intent='kill',
        )
    )

    client = CcbdClient(app.paths.ccbd_socket_path)

    ping = client.ping('codex')
    assert ping['agent_name'] == 'codex'

    with pytest.raises(CcbdClientError, match='lifecycle_stopping'):
        client.submit(
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

    # Stop the in-process server directly; the RPC assertion under test is the
    # stable lifecycle rejection above.
    app.socket_server.request_shutdown()
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_ccbd_heartbeat_skips_maintenance_steps_while_lifecycle_stopping(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-stopping-heartbeat'
    ctx = _prepare_project(project_root, _single_agent_config_text('codex', 'codex'))
    app = CcbdApp(project_root)
    app.lease = SimpleNamespace(generation=3)
    app.lifecycle_store.save(
        build_lifecycle(
            project_id=ctx.project_id,
            occurred_at='2026-03-18T00:00:05Z',
            desired_state='stopped',
            phase='stopping',
            generation=3,
            keeper_pid=app.keeper_pid,
            owner_pid=app.pid,
            owner_daemon_instance_id=app.daemon_instance_id,
            config_signature=str(app.config_identity.get('config_signature') or '').strip() or None,
            socket_path=app.paths.ccbd_socket_path,
            shutdown_intent='stop_all',
        )
    )
    monkeypatch.setattr(app.mount_manager, 'refresh_heartbeat', lambda **kwargs: app.lease)
    monkeypatch.setattr(
        app.health_monitor,
        'check_all',
        lambda: (_ for _ in ()).throw(AssertionError('health monitor should be suspended')),
    )
    monkeypatch.setattr(
        app.runtime_supervision,
        'reconcile_once',
        lambda: (_ for _ in ()).throw(AssertionError('supervision should be suspended')),
    )
    monkeypatch.setattr(
        app.dispatcher,
        'tick',
        lambda: (_ for _ in ()).throw(AssertionError('dispatcher tick should be suspended')),
    )

    app.heartbeat()

    lifecycle = app.lifecycle_store.load()
    assert lifecycle is not None
    assert lifecycle.phase == 'stopping'
    assert lifecycle.last_failure_reason is None


def test_ccbd_heartbeat_skips_maintenance_while_start_lock_held(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-start-heartbeat-lock'
    ctx = _prepare_project(project_root, _single_agent_config_text('codex', 'codex'))
    app = CcbdApp(project_root)
    app.lease = SimpleNamespace(generation=3)
    app.lifecycle_store.save(
        build_lifecycle(
            project_id=ctx.project_id,
            occurred_at='2026-03-18T00:00:05Z',
            desired_state='running',
            phase='mounted',
            generation=3,
            keeper_pid=app.keeper_pid,
            owner_pid=app.pid,
            owner_daemon_instance_id=app.daemon_instance_id,
            config_signature=str(app.config_identity.get('config_signature') or '').strip() or None,
            socket_path=app.paths.ccbd_socket_path,
        )
    )
    monkeypatch.setattr(app.mount_manager, 'refresh_heartbeat', lambda **kwargs: app.lease)
    monkeypatch.setattr(
        app.health_monitor,
        'check_all',
        lambda: (_ for _ in ()).throw(AssertionError('health monitor should not race start')),
    )
    monkeypatch.setattr(
        app.runtime_supervision,
        'reconcile_once',
        lambda: (_ for _ in ()).throw(AssertionError('supervision should not race start')),
    )
    monkeypatch.setattr(
        app.dispatcher,
        'tick',
        lambda: (_ for _ in ()).throw(AssertionError('dispatcher tick should not race start')),
    )

    app.start_maintenance_lock.acquire()
    try:
        app.heartbeat()
    finally:
        app.start_maintenance_lock.release()

    lifecycle = app.lifecycle_store.load()
    assert lifecycle is not None
    assert lifecycle.phase == 'mounted'
    assert lifecycle.last_failure_reason is None


def test_ccbd_heartbeat_skips_heavy_idle_maintenance_between_full_ticks(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-idle-heartbeat-skip'
    ctx = _prepare_project(project_root, _single_agent_config_text('codex', 'codex'))
    app = CcbdApp(project_root)
    app.lease = SimpleNamespace(generation=3)
    app.lifecycle_store.save(
        build_lifecycle(
            project_id=ctx.project_id,
            occurred_at='2026-03-18T00:00:05Z',
            desired_state='running',
            phase='mounted',
            generation=3,
            keeper_pid=app.keeper_pid,
            owner_pid=app.pid,
            owner_daemon_instance_id=app.daemon_instance_id,
            config_signature=str(app.config_identity.get('config_signature') or '').strip() or None,
            socket_path=app.paths.ccbd_socket_path,
        )
    )
    monkeypatch.setattr('ccbd.app_runtime.lifecycle.monotonic', lambda: 100.0)
    app._last_full_heartbeat_at = 95.0
    monkeypatch.setattr(app.mount_manager, 'refresh_heartbeat', lambda **kwargs: app.lease)
    monkeypatch.setattr(
        app.health_monitor,
        'check_all',
        lambda: (_ for _ in ()).throw(AssertionError('idle heartbeat should skip heavy health checks')),
    )

    app.heartbeat()

    assert app.lease.generation == 3


def test_ccbd_heartbeat_runs_heavy_maintenance_for_active_execution(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-active-heartbeat'
    ctx = _prepare_project(project_root, _single_agent_config_text('codex', 'codex'))
    app = CcbdApp(project_root)
    app.lease = SimpleNamespace(generation=3)
    app.lifecycle_store.save(
        build_lifecycle(
            project_id=ctx.project_id,
            occurred_at='2026-03-18T00:00:05Z',
            desired_state='running',
            phase='mounted',
            generation=3,
            keeper_pid=app.keeper_pid,
            owner_pid=app.pid,
            owner_daemon_instance_id=app.daemon_instance_id,
            config_signature=str(app.config_identity.get('config_signature') or '').strip() or None,
            socket_path=app.paths.ccbd_socket_path,
        )
    )
    calls: list[str] = []
    app.execution_service._active['job_1'] = object()
    monkeypatch.setattr('ccbd.app_runtime.lifecycle.monotonic', lambda: 100.0)
    app._last_full_heartbeat_at = 99.0
    monkeypatch.setattr(app.mount_manager, 'refresh_heartbeat', lambda **kwargs: app.lease)
    monkeypatch.setattr(app.health_monitor, 'check_all', lambda: calls.append('health'))
    monkeypatch.setattr(app.runtime_supervision, 'reconcile_once', lambda: {})
    monkeypatch.setattr(app.dispatcher, 'reconcile_runtime_views', lambda: None)
    monkeypatch.setattr(app.dispatcher, 'tick', lambda: None)
    monkeypatch.setattr(app.dispatcher, 'poll_completions', lambda: ())
    monkeypatch.setattr(app.job_heartbeat, 'tick', lambda dispatcher: None)

    app.heartbeat()

    assert calls == ['health']


def test_ccbd_heartbeat_treats_pending_callback_edges_as_hot_work() -> None:
    app = SimpleNamespace(
        execution_service=SimpleNamespace(_active={}),
        dispatcher=SimpleNamespace(
            _state=SimpleNamespace(
                active_items=lambda: (),
                _queues={},
            ),
            _message_bureau=SimpleNamespace(pending_callback_edges=lambda: (object(),)),
        ),
    )

    assert hot_loop_work_pending(app) is True


def test_ccbd_full_idle_heartbeat_does_not_rewrite_last_seen_only_runtime_state(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-idle-heartbeat-last-seen'
    ctx = _prepare_project(project_root, _single_agent_config_text('demo', 'fake'))
    app = CcbdApp(project_root)
    app.lease = SimpleNamespace(generation=3)
    app.lifecycle_store.save(
        build_lifecycle(
            project_id=ctx.project_id,
            occurred_at='2026-03-18T00:00:05Z',
            desired_state='running',
            phase='mounted',
            generation=3,
            keeper_pid=app.keeper_pid,
            owner_pid=app.pid,
            owner_daemon_instance_id=app.daemon_instance_id,
            config_signature=str(app.config_identity.get('config_signature') or '').strip() or None,
            socket_path=app.paths.ccbd_socket_path,
        )
    )
    app.registry.upsert(
        _runtime(
            'demo',
            project_id=ctx.project_id,
            workspace_path=str(app.paths.workspace_path('demo')),
            pid=os.getpid(),
        )
    )
    save_count = app.registry._runtime_store.save_count
    monkeypatch.setattr('ccbd.app_runtime.lifecycle.monotonic', lambda: 100.0)
    app._last_full_heartbeat_at = 0.0
    monkeypatch.setattr(app.mount_manager, 'refresh_heartbeat', lambda **kwargs: app.lease)

    app.heartbeat()
    save_count = app.registry._runtime_store.save_count
    app._last_full_heartbeat_at = 0.0
    app.heartbeat()

    assert app.control_plane_metrics.last_heartbeat_agents_inspected == 1
    assert app.control_plane_metrics.last_heartbeat_runtime_store_writes == 0
    assert app.registry._runtime_store.save_count == save_count


def test_ping_namespace_summary(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-ping-namespace'
    ctx = _prepare_project(project_root, _single_agent_config_text('codex', 'codex'))
    app = CcbdApp(project_root)
    app.namespace_state_store.save(
        ProjectNamespaceState(
            project_id=ctx.project_id,
            namespace_epoch=4,
            tmux_socket_path=str(app.paths.ccbd_tmux_socket_path),
            tmux_session_name=app.paths.ccbd_tmux_session_name,
            layout_version=1,
            ui_attachable=True,
            last_started_at='2026-04-03T00:05:00Z',
        )
    )
    app.namespace_event_store.append(
        ProjectNamespaceEvent(
            event_kind='namespace_created',
            project_id=ctx.project_id,
            occurred_at='2026-04-03T00:05:00Z',
            namespace_epoch=4,
            tmux_socket_path=str(app.paths.ccbd_tmux_socket_path),
            tmux_session_name=app.paths.ccbd_tmux_session_name,
        )
    )
    app.persist_start_policy(auto_permission=True)
    app.runtime_supervision.reconcile_once = lambda: {}

    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for(app.paths.ccbd_socket_path)

    client = CcbdClient(app.paths.ccbd_socket_path)
    ping = client.ping('ccbd')

    assert ping['namespace_epoch'] == 4
    assert ping['namespace_tmux_socket_path'] == str(app.paths.ccbd_tmux_socket_path)
    assert ping['namespace_tmux_session_name'] == app.paths.ccbd_tmux_session_name
    assert ping['namespace_last_event_kind'] == 'namespace_created'
    assert ping['start_policy_auto_permission'] is True
    assert ping['start_policy_recovery_restore'] is True

    client.shutdown()
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_start_persists_policy(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-start-policy'
    _prepare_project(project_root, _single_agent_config_text('demo', 'fake'))
    app = CcbdApp(project_root)
    monkeypatch.setattr(
        app.runtime_supervisor,
        'start',
        lambda **kwargs: SimpleNamespace(
            to_record=lambda: {
                'project_root': str(project_root),
                'project_id': app.project_id,
                'started': ['demo'],
                'socket_path': str(app.paths.ccbd_socket_path),
            }
        ),
    )

    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for(app.paths.ccbd_socket_path)

    client = CcbdClient(app.paths.ccbd_socket_path)
    started = client.start(agent_names=('demo',), restore=False, auto_permission=True)

    assert started['started'] == ['demo']
    policy = app.start_policy_store.load()
    assert policy is not None
    assert policy.auto_permission is True
    assert policy.recovery_restore is False
    assert policy.source == 'start_command'

    client.shutdown()
    thread.join(timeout=2)
    assert not thread.is_alive()

def test_ccbd_attach_and_restore_roundtrip(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    ctx = _prepare_project(project_root, _single_agent_config_text('codex', 'codex'))
    app = CcbdApp(project_root)
    app.restore_store.save(
        'codex',
        AgentRestoreState(
            restore_mode=RestoreMode.AUTO,
            last_checkpoint='checkpoint-1',
            conversation_summary='remember this state',
            open_tasks=['continue'],
            files_touched=['README.md'],
        ),
    )

    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for(app.paths.ccbd_socket_path)

    client = CcbdClient(app.paths.ccbd_socket_path)
    attached = client.attach(
        agent_name='codex',
        workspace_path=str(app.paths.workspace_path('codex')),
        backend_type='pane-backed',
        runtime_ref='codex:codex:attached',
        session_ref='session:codex',
    )
    assert attached['agent_name'] == 'codex'
    assert attached['health'] == 'healthy'
    assert attached['binding_source'] == 'external-attach'

    restored = client.restore('codex')
    assert restored['last_restore_status'] == 'checkpoint'
    reattached = client.attach(
        agent_name='codex',
        workspace_path=str(app.paths.workspace_path('codex')),
        backend_type='pane-backed',
        runtime_ref='tmux:%88',
        session_ref='session:codex:new',
    )
    assert reattached['runtime_ref'] == 'tmux:%88'
    assert reattached['session_ref'] == 'session:codex:new'
    runtime = app.registry.get('codex')
    assert runtime is not None
    assert runtime.health == 'restored'
    assert runtime.runtime_ref == 'tmux:%88'
    assert runtime.session_ref == 'session:codex:new'
    assert runtime.binding_source.value == 'external-attach'

    client.shutdown()
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_ccbd_attach_only_runtime_is_not_eagerly_mounted_without_start_policy(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-attach-only'
    _prepare_project(project_root, _single_agent_config_text('demo', 'codex'))
    app = CcbdApp(project_root)

    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for(app.paths.ccbd_socket_path)

    client = CcbdClient(app.paths.ccbd_socket_path)
    attached = client.attach(
        agent_name='demo',
        workspace_path=str(app.paths.workspace_path('demo')),
        backend_type='pane-backed',
        runtime_ref='tmux:%1',
        session_ref='demo-session-id',
    )

    assert attached['binding_source'] == 'external-attach'
    time.sleep(0.2)
    runtime = app.registry.get('demo')
    assert runtime is not None
    assert runtime.runtime_ref == 'tmux:%1'
    assert runtime.session_ref == 'demo-session-id'
    assert runtime.binding_source.value == 'external-attach'
    assert runtime.health == 'healthy'

    client.shutdown()
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_ccbd_missing_runtime_is_proactively_mounted_when_start_policy_exists(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-policy-mount'
    _prepare_project(project_root, _single_agent_config_text('demo', 'fake'))
    app = CcbdApp(project_root)
    app.persist_start_policy(auto_permission=True)

    mounted: list[str] = []

    def _mount(agent_name: str) -> None:
        mounted.append(agent_name)
        app.runtime_service.attach(
            agent_name=agent_name,
            workspace_path=str(app.paths.workspace_path(agent_name)),
            backend_type='pane-backed',
            runtime_ref='tmux:%42',
            session_ref='demo-session-id',
            binding_source='provider-session',
        )

    app.runtime_supervision._ctx = app.runtime_supervision._ctx.__class__(
        **{
            **app.runtime_supervision._ctx.__dict__,
            'mount_agent_fn': _mount,
        }
    )

    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for(app.paths.ccbd_socket_path)

    deadline = time.time() + 2.0
    while time.time() < deadline and not mounted:
        time.sleep(0.05)

    assert mounted == ['demo']
    runtime = app.registry.get('demo')
    assert runtime is not None
    assert runtime.runtime_ref == 'tmux:%42'
    assert runtime.session_ref == 'demo-session-id'
    assert runtime.binding_source.value == 'provider-session'

    client = CcbdClient(app.paths.ccbd_socket_path)
    client.shutdown()
    thread.join(timeout=2)
    assert not thread.is_alive()


@pytest.mark.usefixtures('noninteractive_socket_transport')
def test_ccbd_queue_reports_registered_agent_mailboxes(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-queue'
    ctx = _prepare_project(
        project_root,
        _agent_config_text(('codex', 'codex'), ('claude', 'claude')),
    )
    app = CcbdApp(project_root)
    app.registry.upsert(
        _runtime(
            'codex',
            project_id=ctx.project_id,
            workspace_path=str(app.paths.workspace_path('codex')),
            pid=777,
        )
    )
    app.registry.upsert(
        _runtime(
            'claude',
            project_id=ctx.project_id,
            workspace_path=str(app.paths.workspace_path('claude')),
            pid=778,
        )
    )

    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for(app.paths.ccbd_socket_path)

    client = CcbdClient(app.paths.ccbd_socket_path)
    submit = client.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent='codex',
            from_actor='claude',
            body='hello queue',
            task_id='task-queue',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    job_id = submit['job_id']
    _wait_for_job_status(client, job_id, 'running')

    running_queue = client.queue('codex', detail=True)
    assert running_queue['target'] == 'codex'
    assert running_queue['agent']['mailbox_state'] == 'delivering'
    assert running_queue['agent']['active']['job_id'] == job_id

    app.dispatcher.complete(job_id, _decision(reply='done queue'))

    reply_queue_summary = client.queue('claude')
    assert reply_queue_summary['target'] == 'claude'
    assert reply_queue_summary['agent']['pending_reply_count'] == 1
    assert 'queued_events' not in reply_queue_summary['agent']

    reply_queue = client.queue('claude', detail=True)
    assert reply_queue['target'] == 'claude'
    assert reply_queue['agent']['pending_reply_count'] == 1
    assert reply_queue['agent']['queued_events'][0]['event_type'] == 'task_reply'

    client.shutdown()
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_ccbd_trace_returns_attempt_reply_and_mailbox_events(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-trace'
    ctx = _prepare_project(
        project_root,
        _agent_config_text(('codex', 'codex'), ('claude', 'claude')),
    )
    app = CcbdApp(project_root)
    app.registry.upsert(
        _runtime(
            'codex',
            project_id=ctx.project_id,
            workspace_path=str(app.paths.workspace_path('codex')),
            pid=777,
        )
    )
    app.registry.upsert(
        _runtime(
            'claude',
            project_id=ctx.project_id,
            workspace_path=str(app.paths.workspace_path('claude')),
            pid=778,
        )
    )

    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for(app.paths.ccbd_socket_path)

    client = CcbdClient(app.paths.ccbd_socket_path)
    submit = client.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent='codex',
            from_actor='claude',
            body='hello trace',
            task_id='task-trace',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    job_id = submit['job_id']
    app.dispatcher.tick()
    app.dispatcher.complete(job_id, _decision(reply='trace done'))

    payload = client.trace(job_id)

    assert payload['target'] == job_id
    assert payload['resolved_kind'] == 'job'
    assert payload['job_id'] == job_id
    assert payload['message_count'] == 1
    assert payload['attempt_count'] == 1
    assert payload['reply_count'] == 1
    assert payload['event_count'] == 2
    assert payload['job_count'] == 1
    assert payload['messages'][0]['from_actor'] == 'claude'
    assert payload['attempts'][0]['job_id'] == job_id
    assert payload['replies'][0]['reply_preview'] == 'trace done'
    assert {item['event_type'] for item in payload['events']} == {'task_request', 'task_reply'}

    client.shutdown()
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_ccbd_inbox_and_ack_roundtrip_reply_delivery(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-inbox-ack'
    ctx = _prepare_project(
        project_root,
        _agent_config_text(('codex', 'codex'), ('claude', 'claude')),
    )
    app = CcbdApp(project_root)
    app.registry.upsert(
        _runtime(
            'codex',
            project_id=ctx.project_id,
            workspace_path=str(app.paths.workspace_path('codex')),
            pid=777,
        )
    )
    app.registry.upsert(
        _runtime(
            'claude',
            project_id=ctx.project_id,
            workspace_path=str(app.paths.workspace_path('claude')),
            pid=778,
        )
    )

    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for(app.paths.ccbd_socket_path)

    client = CcbdClient(app.paths.ccbd_socket_path)
    submit = client.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent='codex',
            from_actor='claude',
            body='hello inbox',
            task_id='task-inbox-ack',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    job_id = submit['job_id']
    app.dispatcher.tick()
    app.dispatcher.complete(job_id, _decision(reply='socket inbox reply'))

    inbox = client.inbox('claude')
    assert inbox['target'] == 'claude'
    assert inbox['head']['event_type'] == 'task_reply'
    assert inbox['head']['reply'] == 'socket inbox reply'
    assert inbox['items'] == []

    inbox_summary = client.inbox('claude', detail=False)
    assert inbox_summary['target'] == 'claude'
    assert inbox_summary['head']['event_type'] == 'task_reply'
    assert inbox_summary['head']['reply'] == 'socket inbox reply'
    assert inbox_summary['items'] == []

    inbox_detail = client.inbox('claude', detail=True)
    assert inbox_detail['items']

    _wait_for_reply_delivery_scheduled(client, 'claude')
    with pytest.raises(CcbdClientError, match='automatic reply delivery has been scheduled'):
        client.ack('claude')

    client.shutdown()
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_ccbd_socket_rejects_cmd_sender(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-inbox-ack-cmd'
    ctx = _prepare_project(
        project_root,
        'cmd; codex:codex,claude:claude\n',
    )
    app = CcbdApp(project_root)
    app.registry.upsert(
        _runtime(
            'codex',
            project_id=ctx.project_id,
            workspace_path=str(app.paths.workspace_path('codex')),
            pid=777,
        )
    )

    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for(app.paths.ccbd_socket_path)

    client = CcbdClient(app.paths.ccbd_socket_path)
    with pytest.raises(CcbdClientError, match='unknown sender agent: cmd'):
        client.submit(
            MessageEnvelope(
                project_id=ctx.project_id,
                to_agent='codex',
                from_actor='cmd',
                body='hello cmd inbox',
                task_id='task-inbox-cmd',
                reply_to=None,
                message_type='ask',
                delivery_scope=DeliveryScope.SINGLE,
            )
        )

    with pytest.raises(CcbdClientError, match='unknown mailbox target: cmd'):
        client.inbox('cmd')
    with pytest.raises(CcbdClientError, match='unknown mailbox target: cmd'):
        client.ack('cmd')

    client.shutdown()
    thread.join(timeout=2)
    assert not thread.is_alive()


@pytest.mark.usefixtures('noninteractive_socket_transport')
def test_ccbd_resubmit_creates_new_message_record_with_origin(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-resubmit-socket'
    ctx = _prepare_project(project_root, _single_agent_config_text('codex', 'codex'))
    app = CcbdApp(project_root)
    app.registry.upsert(
        _runtime(
            'codex',
            project_id=ctx.project_id,
            workspace_path=str(app.paths.workspace_path('codex')),
            pid=777,
        )
    )

    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for(app.paths.ccbd_socket_path)

    client = CcbdClient(app.paths.ccbd_socket_path)
    submit = client.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent='codex',
            from_actor='user',
            body='hello resubmit',
            task_id='task-resubmit-socket',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    job_id = submit['job_id']
    _wait_for_job_status(client, job_id, 'running')
    app.dispatcher.complete(job_id, _decision(status=CompletionStatus.INCOMPLETE, reply='retry me'))
    _wait_for_job_status(client, job_id, 'incomplete')

    original_message = MessageStore(app.paths).list_all()[-1]
    payload = client.resubmit(original_message.message_id)

    assert payload['original_message_id'] == original_message.message_id
    assert payload['message_id'] != original_message.message_id
    assert len(payload['jobs']) == 1
    assert payload['jobs'][0]['agent_name'] == 'codex'

    new_message = MessageStore(app.paths).get_latest(payload['message_id'])
    assert new_message is not None
    assert new_message.origin_message_id == original_message.message_id

    client.shutdown()
    thread.join(timeout=2)
    assert not thread.is_alive()


@pytest.mark.usefixtures('noninteractive_socket_transport')
def test_ccbd_retry_creates_new_attempt_under_existing_message(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-retry-socket'
    ctx = _prepare_project(project_root, _single_agent_config_text('codex', 'codex'))
    app = CcbdApp(project_root)
    app.registry.upsert(
        _runtime(
            'codex',
            project_id=ctx.project_id,
            workspace_path=str(app.paths.workspace_path('codex')),
            pid=777,
        )
    )

    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for(app.paths.ccbd_socket_path)

    client = CcbdClient(app.paths.ccbd_socket_path)
    submit = client.submit(
        MessageEnvelope(
            project_id=ctx.project_id,
            to_agent='codex',
            from_actor='user',
            body='hello retry',
            task_id='task-retry-socket',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    job_id = submit['job_id']
    _wait_for_job_status(client, job_id, 'running')
    app.dispatcher.complete(job_id, _decision(status=CompletionStatus.INCOMPLETE, reply='retry me'))
    _wait_for_job_status(client, job_id, 'incomplete')

    original_message = MessageStore(app.paths).list_all()[-1]
    original_attempt = AttemptStore(app.paths).get_latest_by_job_id(job_id)
    assert original_attempt is not None

    payload = client.retry(job_id)

    assert payload['target'] == job_id
    assert payload['message_id'] == original_message.message_id
    assert payload['original_attempt_id'] == original_attempt.attempt_id
    assert payload['attempt_id'] != original_attempt.attempt_id
    assert payload['job_id'] != job_id
    assert payload['agent_name'] == 'codex'

    new_attempt = AttemptStore(app.paths).get_latest(payload['attempt_id'])
    assert new_attempt is not None
    assert new_attempt.message_id == original_message.message_id
    assert new_attempt.retry_index == 1

    codex_events = InboundEventStore(app.paths).list_agent('codex')
    assert codex_events[-1].attempt_id == payload['attempt_id']
    assert codex_events[-1].event_type is InboundEventType.TASK_REQUEST
    assert codex_events[-1].status in {InboundEventStatus.QUEUED, InboundEventStatus.DELIVERING}
    if codex_events[-1].status is InboundEventStatus.DELIVERING:
        assert codex_events[-1].started_at is not None

    client.shutdown()
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_ccbd_socket_ignores_client_disconnect_during_response(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-broken-pipe'
    _prepare_project(project_root, _single_agent_config_text('codex', 'codex'))
    app = CcbdApp(project_root)

    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for(app.paths.ccbd_socket_path)

    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(str(app.paths.ccbd_socket_path))
    sock.sendall((json.dumps({'api_version': 2, 'op': 'ping', 'request': {'target': 'ccbd'}}) + '\n').encode('utf-8'))
    sock.close()

    time.sleep(0.1)

    client = CcbdClient(app.paths.ccbd_socket_path)
    ping = client.ping('ccbd')
    assert ping['mount_state'] == 'mounted'

    client.shutdown()
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_ccbd_attach_without_provider_binding_does_not_synthesize_refs(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-unbound'
    _prepare_project(project_root, _single_agent_config_text('codex', 'codex'))
    app = CcbdApp(project_root)
    app.restore_store.save(
        'codex',
        AgentRestoreState(
            restore_mode=RestoreMode.AUTO,
            last_checkpoint='checkpoint-1',
            conversation_summary='remember this state',
            open_tasks=['continue'],
            files_touched=['README.md'],
        ),
    )

    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for(app.paths.ccbd_socket_path)

    client = CcbdClient(app.paths.ccbd_socket_path)
    attached = client.attach(
        agent_name='codex',
        workspace_path=str(app.paths.workspace_path('codex')),
        backend_type='pane-backed',
    )
    assert attached['runtime_ref'] is None
    assert attached['session_ref'] is None

    restored = client.restore('codex')
    assert restored['last_restore_status'] == 'checkpoint'
    runtime = app.registry.get('codex')
    assert runtime is not None
    assert runtime.health == 'restored'
    assert runtime.runtime_ref is None
    assert runtime.session_ref is None

    client.shutdown()
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_ccbd_attach_empty_binding_fields_clear_previous_refs(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-clear-binding'
    ctx = _prepare_project(project_root, _single_agent_config_text('codex', 'codex'))
    app = CcbdApp(project_root)

    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for(app.paths.ccbd_socket_path)

    client = CcbdClient(app.paths.ccbd_socket_path)
    client.attach(
        agent_name='codex',
        workspace_path=str(app.paths.workspace_path('codex')),
        backend_type='pane-backed',
        runtime_ref='tmux:%88',
        session_ref='session:codex:new',
        health='healthy',
    )

    cleared = client.attach(
        agent_name='codex',
        workspace_path=str(app.paths.workspace_path('codex')),
        backend_type='pane-backed',
        runtime_ref='',
        session_ref='',
        health='degraded',
    )

    assert cleared['runtime_ref'] is None
    assert cleared['session_ref'] is None
    assert cleared['health'] == 'degraded'
    runtime = app.registry.get('codex')
    assert runtime is not None
    assert runtime.runtime_ref is None
    assert runtime.session_ref is None
    assert runtime.health == 'degraded'

    client.shutdown()
    thread.join(timeout=2)
    assert not thread.is_alive()


@pytest.mark.usefixtures('noninteractive_socket_transport')
def test_ccbd_socket_codex_protocol_turn_completes_via_tracker(monkeypatch, tmp_path: Path) -> None:
    from provider_execution import codex as codex_adapter_module

    fixed_req_id = 'job_codex1'
    sent: list[tuple[str, str]] = []
    project_root = tmp_path / 'repo-codex'
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('demo', 'codex'))

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            sent.append((pane_id, text))

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%1'

    class FakeSession:
        data = {}
        codex_session_path = str(tmp_path / 'demo-session.jsonl')
        codex_session_id = 'demo-session-id'
        work_dir = str(project_root)

        def ensure_pane(self):
            return True, '%1'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._events = [
                {
                    'role': 'user',
                    'text': f'CCB_REQ_ID: {fixed_req_id}\n\nprompt',
                    'entry_type': 'response_item',
                    'payload_type': 'message',
                    'timestamp': '2026-03-18T00:00:00Z',
                },
                {
                    'role': 'assistant',
                    'text': 'partial',
                    'entry_type': 'event_msg',
                    'payload_type': 'agent_message',
                    'timestamp': '2026-03-18T00:00:01Z',
                },
                {
                    'role': 'assistant',
                    'text': 'final without done',
                    'entry_type': 'event_msg',
                    'payload_type': 'agent_message',
                    'phase': 'final_answer',
                    'timestamp': '2026-03-18T00:00:02Z',
                },
                {
                    'role': 'assistant',
                    'text': 'final without done',
                    'entry_type': 'response_item',
                    'payload_type': 'message',
                    'phase': 'final_answer',
                    'timestamp': '2026-03-18T00:00:02Z',
                },
                {
                    'role': 'system',
                    'text': 'partial\nfinal without done',
                    'entry_type': 'event_msg',
                    'payload_type': 'task_complete',
                    'turn_id': 'turn-codex-socket',
                    'last_agent_message': 'partial\nfinal without done',
                    'timestamp': '2026-03-18T00:00:03Z',
                },
            ]

        def capture_state(self):
            return {'index': 0}

        def try_get_entries(self, state):
            index = int(state.get('index', 0))
            if index >= len(self._events):
                return [], state
            return [self._events[index]], {'index': index + 1}

    monkeypatch.setattr(codex_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(codex_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(codex_adapter_module, 'CodexLogReader', FakeReader)

    app = CcbdApp(project_root)
    _freeze_next_job_id(app, monkeypatch, fixed_req_id)
    app.paths.workspace_path('demo').mkdir(parents=True, exist_ok=True)
    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for(app.paths.ccbd_socket_path)

    client = CcbdClient(app.paths.ccbd_socket_path)
    attached = client.attach(
        agent_name='demo',
        workspace_path=str(app.paths.workspace_path('demo')),
        backend_type='pane-backed',
        runtime_ref='tmux:%1',
        session_ref='demo-session-id',
    )
    assert attached['agent_name'] == 'demo'

    submit = client.submit(
        MessageEnvelope(
            project_id=app.project_id,
            to_agent='demo',
            from_actor='user',
            body='hello codex',
            task_id='task-codex',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    job_id = submit['job_id']

    completed = _wait_for_job_status(client, job_id, 'completed', timeout=3.0)
    assert completed['reply'] == 'partial\nfinal without done'
    assert completed['completion_reason'] == 'task_complete'
    assert completed['completion_confidence'] == 'exact'
    assert sent and sent[0][0] == '%1'
    assert fixed_req_id in sent[0][1]
    assert 'CCB_DONE:' not in sent[0][1]

    watch = client.watch(job_id)
    assert watch['terminal'] is True
    event_types = [event['type'] for event in watch['events']]
    assert event_types.count('completion_item') == 4
    assert 'completion_state_updated' in event_types
    assert 'completion_terminal' in event_types
    assert event_types[-1] == 'job_completed'

    shutdown = client.shutdown()
    assert shutdown['state'] == 'unmounted'
    thread.join(timeout=2)
    assert not thread.is_alive()


@pytest.mark.usefixtures('noninteractive_socket_transport')
def test_ccbd_socket_codex_protocol_turn_handles_interrupted_abort(monkeypatch, tmp_path: Path) -> None:
    from provider_execution import codex as codex_adapter_module

    fixed_req_id = 'job_codex2'
    sent: list[tuple[str, str]] = []
    project_root = tmp_path / 'repo-codex-abort'
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('demo', 'codex'))

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            sent.append((pane_id, text))

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%1'

    class FakeSession:
        data = {}
        codex_session_path = str(tmp_path / 'demo-session.jsonl')
        codex_session_id = 'demo-session-id'
        work_dir = str(project_root)

        def ensure_pane(self):
            return True, '%1'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._events = [
                {
                    'role': 'user',
                    'text': f'CCB_REQ_ID: {fixed_req_id}\n\nprompt',
                    'entry_type': 'response_item',
                    'payload_type': 'message',
                    'timestamp': '2026-03-18T00:00:00Z',
                },
                {
                    'role': 'assistant',
                    'text': 'partial before interrupt',
                    'entry_type': 'event_msg',
                    'payload_type': 'agent_message',
                    'timestamp': '2026-03-18T00:00:01Z',
                },
                {
                    'role': 'system',
                    'text': '',
                    'entry_type': 'event_msg',
                    'payload_type': 'turn_aborted',
                    'turn_id': 'turn-codex-abort',
                    'reason': 'interrupted',
                    'timestamp': '2026-03-18T00:00:02Z',
                },
            ]

        def capture_state(self):
            return {'index': 0}

        def try_get_entries(self, state):
            index = int(state.get('index', 0))
            if index >= len(self._events):
                return [], state
            return [self._events[index]], {'index': index + 1}

    monkeypatch.setattr(codex_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(codex_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(codex_adapter_module, 'CodexLogReader', FakeReader)

    app = CcbdApp(project_root)
    _freeze_next_job_id(app, monkeypatch, fixed_req_id)
    app.paths.workspace_path('demo').mkdir(parents=True, exist_ok=True)
    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for(app.paths.ccbd_socket_path)

    client = CcbdClient(app.paths.ccbd_socket_path)
    client.attach(
        agent_name='demo',
        workspace_path=str(app.paths.workspace_path('demo')),
        backend_type='pane-backed',
        runtime_ref='tmux:%1',
        session_ref='demo-session-id',
    )

    submit = client.submit(
        MessageEnvelope(
            project_id=app.project_id,
            to_agent='demo',
            from_actor='user',
            body='interrupt codex',
            task_id='task-codex-abort',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    job_id = submit['job_id']

    cancelled = _wait_for_job_status(client, job_id, 'cancelled', timeout=3.0)
    assert cancelled['reply'] == 'partial before interrupt'
    assert cancelled['completion_reason'] == 'interrupted'
    assert cancelled['completion_confidence'] == 'exact'
    assert sent and sent[0][0] == '%1'
    assert fixed_req_id in sent[0][1]
    assert 'CCB_DONE:' not in sent[0][1]

    watch = client.watch(job_id)
    assert watch['terminal'] is True
    event_types = [event['type'] for event in watch['events']]
    assert 'completion_terminal' in event_types
    assert event_types[-1] == 'job_cancelled'

    shutdown = client.shutdown()
    assert shutdown['state'] == 'unmounted'
    thread.join(timeout=2)
    assert not thread.is_alive()


@pytest.mark.usefixtures('noninteractive_socket_transport')
def test_ccbd_socket_claude_session_boundary_completes_via_tracker(monkeypatch, tmp_path: Path) -> None:
    from provider_execution import claude as claude_adapter_module

    fixed_req_id = 'job_claude1'
    sent: list[tuple[str, str]] = []
    project_root = tmp_path / 'repo-claude'
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('demo', 'claude'))

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            sent.append((pane_id, text))

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%2'

    class FakeSession:
        data = {}
        claude_session_path = str(tmp_path / 'claude-session.jsonl')
        claude_session_id = 'claude-session-id'
        claude_projects_root = None
        work_dir = str(project_root)

        def ensure_pane(self):
            return True, '%2'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._events = [
                ('user', f'CCB_REQ_ID: {fixed_req_id}\n\nprompt'),
                ('assistant', 'partial'),
                ('assistant', f'final\nCCB_DONE: {fixed_req_id}'),
            ]

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': str(tmp_path / 'claude-session.jsonl'), 'offset': 0}

        def try_get_events(self, state):
            index = int(state.get('index', 0))
            if index >= len(self._events):
                return [], state
            return [self._events[index]], {**state, 'index': index + 1}

    monkeypatch.setattr(claude_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(claude_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(claude_adapter_module, 'ClaudeLogReader', FakeReader)

    app = CcbdApp(project_root)
    _freeze_next_job_id(app, monkeypatch, fixed_req_id)
    app.paths.workspace_path('demo').mkdir(parents=True, exist_ok=True)
    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for(app.paths.ccbd_socket_path)

    client = CcbdClient(app.paths.ccbd_socket_path)
    attached = client.attach(
        agent_name='demo',
        workspace_path=str(app.paths.workspace_path('demo')),
        backend_type='pane-backed',
        runtime_ref='tmux:%2',
        session_ref='claude-session-id',
    )
    assert attached['agent_name'] == 'demo'

    submit = client.submit(
        MessageEnvelope(
            project_id=app.project_id,
            to_agent='demo',
            from_actor='user',
            body='hello claude',
            task_id='task-claude',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    job_id = submit['job_id']

    completed = _wait_for_job_status(client, job_id, 'completed', timeout=3.0)
    assert completed['reply'] == 'final'
    assert completed['completion_reason'] == 'task_complete'
    assert completed['completion_confidence'] == 'observed'
    assert sent and sent[0][0] == '%2'
    assert fixed_req_id in sent[0][1]

    watch = client.watch(job_id)
    assert watch['terminal'] is True
    event_types = [event['type'] for event in watch['events']]
    assert event_types.count('completion_item') == 4
    assert 'completion_state_updated' in event_types
    assert 'completion_terminal' in event_types
    assert event_types[-1] == 'job_completed'

    shutdown = client.shutdown()
    assert shutdown['state'] == 'unmounted'
    thread.join(timeout=2)
    assert not thread.is_alive()


@pytest.mark.usefixtures('noninteractive_socket_transport')
def test_ccbd_socket_claude_turn_duration_completion_without_done_marker(monkeypatch, tmp_path: Path) -> None:
    from provider_execution import claude as claude_adapter_module

    fixed_req_id = 'job_claude2'
    sent: list[tuple[str, str]] = []
    project_root = tmp_path / 'repo-claude-td'
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('demo', 'claude'))

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            sent.append((pane_id, text))

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%2'

    class FakeSession:
        data = {}
        claude_session_path = str(tmp_path / 'claude-session.jsonl')
        claude_session_id = 'claude-session-id'
        claude_projects_root = None
        work_dir = str(project_root)

        def ensure_pane(self):
            return True, '%2'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._events = [
                {'role': 'user', 'text': f'CCB_REQ_ID: {fixed_req_id}\n\nprompt', 'entry_type': 'user'},
                {'role': 'assistant', 'text': 'final without done', 'entry_type': 'assistant', 'uuid': 'assistant-1'},
                {'role': 'system', 'text': '', 'entry_type': 'system', 'subtype': 'turn_duration', 'parent_uuid': 'assistant-1'},
            ]

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': str(tmp_path / 'claude-session.jsonl'), 'offset': 0}

        def try_get_entries(self, state):
            index = int(state.get('index', 0))
            if index >= len(self._events):
                return [], state
            return [self._events[index]], {**state, 'index': index + 1}

    monkeypatch.setattr(claude_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(claude_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(claude_adapter_module, 'ClaudeLogReader', FakeReader)

    app = CcbdApp(project_root)
    _freeze_next_job_id(app, monkeypatch, fixed_req_id)
    app.paths.workspace_path('demo').mkdir(parents=True, exist_ok=True)
    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for(app.paths.ccbd_socket_path)

    client = CcbdClient(app.paths.ccbd_socket_path)
    client.attach(
        agent_name='demo',
        workspace_path=str(app.paths.workspace_path('demo')),
        backend_type='pane-backed',
        runtime_ref='tmux:%2',
        session_ref='claude-session-id',
    )

    submit = client.submit(
        MessageEnvelope(
            project_id=app.project_id,
            to_agent='demo',
            from_actor='user',
            body='hello claude',
            task_id='task-claude-turn',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    job_id = submit['job_id']

    completed = _wait_for_job_status(client, job_id, 'completed', timeout=3.0)
    assert completed['reply'] == 'final without done'
    assert completed['completion_reason'] == 'turn_duration'
    assert completed['completion_confidence'] == 'observed'
    assert sent and sent[0][0] == '%2'
    assert fixed_req_id in sent[0][1]

    shutdown = client.shutdown()
    assert shutdown['state'] == 'unmounted'
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_ccbd_socket_gemini_session_snapshot_completes_via_tracker(monkeypatch, tmp_path: Path) -> None:
    from provider_execution import gemini as gemini_adapter_module

    fixed_req_id = 'job_gemini1'
    sent: list[tuple[str, str]] = []
    project_root = tmp_path / 'repo-gemini'
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('demo', 'gemini'))

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            sent.append((pane_id, text))

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%3'

    class FakeSession:
        data = {}
        gemini_session_path = str(tmp_path / 'gemini-session.json')
        gemini_session_id = 'gemini-session-id'
        work_dir = str(project_root)

        def ensure_pane(self):
            return True, '%3'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._emitted = False

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': str(tmp_path / 'gemini-session.json'), 'msg_count': 0}

        def try_get_message(self, state):
            if self._emitted:
                return None, state
            self._emitted = True
            return (
                'stable reply',
                {
                    **state,
                    'msg_count': 2,
                    'last_gemini_id': 'msg-2',
                    'mtime_ns': 123456789,
                },
            )

    monkeypatch.setattr(gemini_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(gemini_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(gemini_adapter_module, 'GeminiLogReader', FakeReader)

    app = CcbdApp(project_root)
    _freeze_next_job_id(app, monkeypatch, fixed_req_id)
    app.paths.workspace_path('demo').mkdir(parents=True, exist_ok=True)
    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for(app.paths.ccbd_socket_path)

    client = CcbdClient(app.paths.ccbd_socket_path)
    attached = client.attach(
        agent_name='demo',
        workspace_path=str(app.paths.workspace_path('demo')),
        backend_type='pane-backed',
        runtime_ref='tmux:%3',
        session_ref='gemini-session-id',
    )
    assert attached['agent_name'] == 'demo'

    submit = client.submit(
        MessageEnvelope(
            project_id=app.project_id,
            to_agent='demo',
            from_actor='user',
            body='hello gemini',
            task_id='task-gemini',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    job_id = submit['job_id']

    completed = _wait_for_job_status(client, job_id, 'completed', timeout=5.0)
    assert completed['reply'] == 'stable reply'
    assert completed['completion_reason'] == 'session_reply_stable'
    assert completed['completion_confidence'] == 'observed'
    assert sent and sent[0][0] == '%3'
    assert fixed_req_id in sent[0][1]

    watch = client.watch(job_id)
    assert watch['terminal'] is True
    event_types = [event['type'] for event in watch['events']]
    assert event_types.count('completion_item') == 2
    assert 'completion_terminal' in event_types
    assert 'job_completed' in event_types

    shutdown = client.shutdown()
    assert shutdown['state'] == 'unmounted'
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_ccbd_socket_gemini_long_silence_and_session_rotate_do_not_finish_early(monkeypatch, tmp_path: Path) -> None:
    from provider_execution import gemini as gemini_adapter_module

    fixed_req_id = 'job_geminirotate'
    project_root = tmp_path / 'repo-gemini-rotate'
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('demo', 'gemini'))

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            del pane_id, text

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%3'

    class FakeSession:
        data = {}
        gemini_session_path = str(tmp_path / 'gemini-session-old.json')
        gemini_session_id = 'gemini-session-id'
        work_dir = str(project_root)

        def ensure_pane(self):
            return True, '%3'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._calls = 0
            self._emitted = False

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': str(tmp_path / 'gemini-session-old.json'), 'msg_count': 0}

        def try_get_message(self, state):
            self._calls += 1
            if self._calls < 4 or self._emitted:
                return None, state
            self._emitted = True
            return (
                'rotated stable reply',
                {
                    **state,
                    'session_path': str(tmp_path / 'gemini-session-new.json'),
                    'msg_count': 4,
                    'last_gemini_id': 'msg-4',
                    'mtime_ns': 987654321,
                },
            )

    monkeypatch.setattr(gemini_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(gemini_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(gemini_adapter_module, 'GeminiLogReader', FakeReader)

    app = CcbdApp(project_root)
    _freeze_next_job_id(app, monkeypatch, fixed_req_id)
    app.paths.workspace_path('demo').mkdir(parents=True, exist_ok=True)
    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for(app.paths.ccbd_socket_path)

    client = CcbdClient(app.paths.ccbd_socket_path)
    client.attach(
        agent_name='demo',
        workspace_path=str(app.paths.workspace_path('demo')),
        backend_type='pane-backed',
        runtime_ref='tmux:%3',
        session_ref='gemini-session-id',
    )

    submit = client.submit(
        MessageEnvelope(
            project_id=app.project_id,
            to_agent='demo',
            from_actor='user',
            body='hello rotate gemini',
            task_id='task-gemini-rotate',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    job_id = submit['job_id']

    running = _wait_for_job_status(client, job_id, 'running', timeout=5.0)
    assert running['status'] == 'running'
    assert running['completion_reason'] is None

    completed = _wait_for_job_status(client, job_id, 'completed', timeout=5.0)
    assert completed['reply'] == 'rotated stable reply'
    assert completed['completion_reason'] == 'session_reply_stable'
    assert completed['completion_confidence'] == 'observed'

    watch = client.watch(job_id)
    assert watch['terminal'] is True
    event_types = [event['type'] for event in watch['events']]
    assert event_types.count('completion_item') == 4
    assert 'completion_terminal' in event_types
    assert event_types[-1] == 'job_completed'

    shutdown = client.shutdown()
    assert shutdown['state'] == 'unmounted'
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_ccbd_socket_gemini_tool_call_progress_does_not_finish_on_first_round(monkeypatch, tmp_path: Path) -> None:
    from provider_execution import gemini as gemini_adapter_module

    fixed_req_id = 'job_geminitoolwait'
    project_root = tmp_path / 'repo-gemini-toolwait'
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('demo', 'gemini'))

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            del pane_id, text

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%3'

    class FakeSession:
        data = {}
        gemini_session_path = str(tmp_path / 'gemini-session.json')
        gemini_session_id = 'gemini-session-id'
        work_dir = str(project_root)

        def ensure_pane(self):
            return True, '%3'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._calls = 0

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': str(tmp_path / 'gemini-session.json'), 'msg_count': 0}

        def try_get_message(self, state):
            self._calls += 1
            if self._calls == 1:
                return (
                    'I will inspect the manuscript first.',
                    {
                        **state,
                        'msg_count': 1,
                        'last_gemini_id': 'msg-1',
                        'mtime_ns': 111,
                        'last_tool_call_count': 1,
                    },
                )
            if self._calls < 10:
                return None, state
            if self._calls == 10:
                return (
                    'Final review result.',
                    {
                        **state,
                        'msg_count': 2,
                        'last_gemini_id': 'msg-2',
                        'mtime_ns': 222,
                        'last_tool_call_count': 0,
                    },
                )
            return None, state

    monkeypatch.setattr(gemini_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(gemini_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(gemini_adapter_module, 'GeminiLogReader', FakeReader)

    app = CcbdApp(project_root)
    _freeze_next_job_id(app, monkeypatch, fixed_req_id)
    app.paths.workspace_path('demo').mkdir(parents=True, exist_ok=True)
    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for(app.paths.ccbd_socket_path)

    client = CcbdClient(app.paths.ccbd_socket_path)
    client.attach(
        agent_name='demo',
        workspace_path=str(app.paths.workspace_path('demo')),
        backend_type='pane-backed',
        runtime_ref='tmux:%3',
        session_ref='gemini-session-id',
    )

    submit = client.submit(
        MessageEnvelope(
            project_id=app.project_id,
            to_agent='demo',
            from_actor='user',
            body='hello gemini tool progress',
            task_id='task-gemini-tool-progress',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    job_id = submit['job_id']

    running = _wait_for_job_payload(
        client,
        job_id,
        lambda payload: (
            payload['status'] == 'running'
            and payload.get('reply') == 'I will inspect the manuscript first.'
            and payload.get('completion_reason') is None
        ),
        timeout=5.0,
    )
    assert running['status'] == 'running'
    assert running['completion_reason'] is None
    assert running['reply'] == 'I will inspect the manuscript first.'

    completed = _wait_for_job_status(client, job_id, 'completed', timeout=5.0)
    assert completed['reply'] == 'Final review result.'
    assert completed['completion_reason'] == 'session_reply_stable'

    shutdown = client.shutdown()
    assert shutdown['state'] == 'unmounted'
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_ccbd_socket_gemini_rotate_clears_stale_reply_preview(monkeypatch, tmp_path: Path) -> None:
    from provider_execution import gemini as gemini_adapter_module

    fixed_req_id = 'job_geminipreview'
    project_root = tmp_path / 'gpr'
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('demo', 'gemini'))

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            del pane_id, text

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%3'

    class FakeSession:
        data = {}
        gemini_session_path = str(tmp_path / 'gemini-session-old.json')
        gemini_session_id = 'gemini-session-id'
        work_dir = str(project_root)

        def ensure_pane(self):
            return True, '%3'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._calls = 0

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def capture_state(self):
            return {'session_path': str(tmp_path / 'gemini-session-old.json'), 'msg_count': 0}

        def try_get_message(self, state):
            self._calls += 1
            if self._calls == 1:
                return (
                    'old preview reply',
                    {
                        **state,
                        'msg_count': 1,
                        'last_gemini_id': 'msg-old',
                        'mtime_ns': 111,
                    },
                )
            if self._calls == 2:
                return (
                    None,
                    {
                        **state,
                        'session_path': str(tmp_path / 'gemini-session-new.json'),
                        'msg_count': 0,
                        'last_gemini_id': None,
                        'mtime_ns': 222,
                    },
                )
            return None, state

    monkeypatch.setattr(gemini_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(gemini_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(gemini_adapter_module, 'GeminiLogReader', FakeReader)

    app = CcbdApp(project_root)
    _freeze_next_job_id(app, monkeypatch, fixed_req_id)
    app.paths.workspace_path('demo').mkdir(parents=True, exist_ok=True)
    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for(app.paths.ccbd_socket_path)

    client = CcbdClient(app.paths.ccbd_socket_path)
    client.attach(
        agent_name='demo',
        workspace_path=str(app.paths.workspace_path('demo')),
        backend_type='pane-backed',
        runtime_ref='tmux:%3',
        session_ref='gemini-session-id',
    )

    submit = client.submit(
        MessageEnvelope(
            project_id=app.project_id,
            to_agent='demo',
            from_actor='user',
            body='hello gemini rotate preview',
            task_id='task-gemini-preview-reset',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    job_id = submit['job_id']

    running = _wait_for_job_payload(
        client,
        job_id,
        lambda payload: payload['status'] == 'running' and payload.get('reply') != 'old preview reply',
        timeout=5.0,
    )
    assert running['status'] == 'running'
    assert running['reply'] != 'old preview reply'
    assert running['completion_reason'] is None

    watch = _wait_for_watch_payload(
        client,
        job_id,
        lambda payload: len([event for event in payload['events'] if event['type'] == 'completion_item']) >= 4,
        timeout=5.0,
    )
    assert watch['terminal'] is False
    completion_items = [event for event in watch['events'] if event['type'] == 'completion_item']
    assert len(completion_items) >= 4

    shutdown = client.shutdown()
    assert shutdown['state'] == 'unmounted'
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_ccbd_socket_opencode_completed_reply_uses_session_boundary_tracker(monkeypatch, tmp_path: Path) -> None:
    from provider_execution import opencode as opencode_adapter_module

    fixed_req_id = 'job_opencode1'
    sent: list[tuple[str, str]] = []
    project_root = tmp_path / 'repo-opencode'
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('demo', 'opencode'))

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            sent.append((pane_id, text))

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%4'

    class FakeSession:
        data = {}
        opencode_project_id = 'proj-demo'
        opencode_session_id_filter = 'ses-demo'
        work_dir = str(project_root)

        def ensure_pane(self):
            return True, '%4'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def capture_state(self):
            return {'session_path': str(tmp_path / 'opencode-session.json'), 'session_id': 'ses-demo'}

        def try_get_message(self, state):
            return (
                'legacy final',
                {
                    **state,
                    'last_assistant_id': 'msg-final',
                    'last_assistant_parent_id': 'msg-user',
                    'last_assistant_req_id': fixed_req_id,
                    'last_assistant_completed': 1234,
                },
            )

    monkeypatch.setattr(opencode_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(opencode_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(opencode_adapter_module, 'OpenCodeLogReader', FakeReader)

    app = CcbdApp(project_root)
    _freeze_next_job_id(app, monkeypatch, fixed_req_id)
    app.paths.workspace_path('demo').mkdir(parents=True, exist_ok=True)
    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for(app.paths.ccbd_socket_path)

    client = CcbdClient(app.paths.ccbd_socket_path)
    client.attach(
        agent_name='demo',
        workspace_path=str(app.paths.workspace_path('demo')),
        backend_type='pane-backed',
        runtime_ref='tmux:%4',
        session_ref='ses-demo',
    )

    submit = client.submit(
        MessageEnvelope(
            project_id=app.project_id,
            to_agent='demo',
            from_actor='user',
            body='hello opencode',
            task_id='task-opencode',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    job_id = submit['job_id']

    completed = _wait_for_job_status(client, job_id, 'completed', timeout=3.0)
    assert completed['reply'] == 'legacy final'
    assert completed['completion_reason'] == 'assistant_completed'
    assert completed['completion_confidence'] == 'observed'
    assert sent and sent[0][0] == '%4'
    assert fixed_req_id in sent[0][1]

    shutdown = client.shutdown()
    assert shutdown['state'] == 'unmounted'
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_ccbd_socket_opencode_pane_dead_becomes_failed_degraded(monkeypatch, tmp_path: Path) -> None:
    from provider_execution import opencode as opencode_adapter_module

    fixed_req_id = 'job_opencodedead'
    project_root = tmp_path / 'repo-opencode-dead'
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('demo', 'opencode'))

    class DeadBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            del pane_id, text

        def is_alive(self, pane_id: str) -> bool:
            del pane_id
            return False

    class FakeSession:
        data = {}
        opencode_project_id = 'proj-demo'
        opencode_session_id_filter = 'ses-demo'
        work_dir = str(project_root)

        def ensure_pane(self):
            return True, '%4'

    class EmptyReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def capture_state(self):
            return {'session_path': str(tmp_path / 'opencode-session.json'), 'session_id': 'ses-demo'}

        def try_get_message(self, state):
            return None, state

    monkeypatch.setattr(opencode_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(opencode_adapter_module, 'get_backend_for_session', lambda data: DeadBackend())
    monkeypatch.setattr(opencode_adapter_module, 'OpenCodeLogReader', EmptyReader)

    app = CcbdApp(project_root)
    _freeze_next_job_id(app, monkeypatch, fixed_req_id)
    app.paths.workspace_path('demo').mkdir(parents=True, exist_ok=True)
    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for(app.paths.ccbd_socket_path)

    client = CcbdClient(app.paths.ccbd_socket_path)
    client.attach(
        agent_name='demo',
        workspace_path=str(app.paths.workspace_path('demo')),
        backend_type='pane-backed',
        runtime_ref='tmux:%4',
        session_ref='ses-demo',
    )

    submit = client.submit(
        MessageEnvelope(
            project_id=app.project_id,
            to_agent='demo',
            from_actor='user',
            body='hello opencode dead',
            task_id='task-opencode-dead',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    job_id = submit['job_id']

    failed = _wait_for_job_status(client, job_id, 'failed', timeout=3.0)
    assert 'ccb screen demo' in failed['reply']
    assert f'ccb trace {job_id}' in failed['reply']
    assert failed['completion_reason'] == 'pane_dead'
    assert failed['completion_confidence'] == 'degraded'

    shutdown = client.shutdown()
    assert shutdown['state'] == 'unmounted'
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_ccbd_socket_droid_legacy_completion_via_tracker(monkeypatch, tmp_path: Path) -> None:
    from provider_execution import droid as droid_adapter_module

    fixed_req_id = 'job_droid1'
    sent: list[tuple[str, str]] = []
    project_root = tmp_path / 'repo-droid'
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('demo', 'droid'))

    class FakeBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            sent.append((pane_id, text))

        def is_alive(self, pane_id: str) -> bool:
            return pane_id == '%5'

    class FakeSession:
        data = {}
        droid_session_path = str(tmp_path / 'droid-session.jsonl')
        droid_session_id = 'droid-session-id'
        work_dir = str(project_root)

        def ensure_pane(self):
            return True, '%5'

    class FakeReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs
            self._events = [
                ('user', f'CCB_REQ_ID: {fixed_req_id}\n\nprompt'),
                ('assistant', 'partial'),
                ('assistant', f'final\nCCB_DONE: {fixed_req_id}'),
            ]

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def set_session_id_hint(self, session_id) -> None:
            del session_id

        def capture_state(self):
            return {'session_path': str(tmp_path / 'droid-session.jsonl'), 'offset': 0}

        def try_get_events(self, state):
            index = int(state.get('index', 0))
            if index >= len(self._events):
                return [], state
            return [self._events[index]], {**state, 'index': index + 1}

    monkeypatch.setattr(droid_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(droid_adapter_module, 'get_backend_for_session', lambda data: FakeBackend())
    monkeypatch.setattr(droid_adapter_module, 'DroidLogReader', FakeReader)

    app = CcbdApp(project_root)
    _freeze_next_job_id(app, monkeypatch, fixed_req_id)
    app.paths.workspace_path('demo').mkdir(parents=True, exist_ok=True)
    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for(app.paths.ccbd_socket_path)

    client = CcbdClient(app.paths.ccbd_socket_path)
    client.attach(
        agent_name='demo',
        workspace_path=str(app.paths.workspace_path('demo')),
        backend_type='pane-backed',
        runtime_ref='tmux:%5',
        session_ref='droid-session-id',
    )

    submit = client.submit(
        MessageEnvelope(
            project_id=app.project_id,
            to_agent='demo',
            from_actor='user',
            body='hello droid',
            task_id='task-droid',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    job_id = submit['job_id']

    completed = _wait_for_job_status(client, job_id, 'completed', timeout=3.0)
    assert completed['reply'] == 'partial\nfinal'
    assert completed['completion_reason'] == 'terminal_done_marker'
    assert completed['completion_confidence'] == 'degraded'
    assert sent and sent[0][0] == '%5'
    assert fixed_req_id in sent[0][1]

    shutdown = client.shutdown()
    assert shutdown['state'] == 'unmounted'
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_ccbd_socket_droid_pane_dead_becomes_failed_degraded(monkeypatch, tmp_path: Path) -> None:
    from provider_execution import droid as droid_adapter_module

    fixed_req_id = 'job_droiddead'
    project_root = tmp_path / 'repo-droid-dead'
    _write(project_root / '.ccb' / 'ccb.config', _single_agent_config_text('demo', 'droid'))

    class DeadBackend:
        def send_text(self, pane_id: str, text: str) -> None:
            del pane_id, text

        def is_alive(self, pane_id: str) -> bool:
            del pane_id
            return False

    class FakeSession:
        data = {}
        droid_session_path = str(tmp_path / 'droid-session.jsonl')
        droid_session_id = 'droid-session-id'
        work_dir = str(project_root)

        def ensure_pane(self):
            return True, '%5'

    class EmptyReader:
        def __init__(self, *args, **kwargs) -> None:
            del args, kwargs

        def set_preferred_session(self, session_path) -> None:
            del session_path

        def set_session_id_hint(self, session_id) -> None:
            del session_id

        def capture_state(self):
            return {'session_path': str(tmp_path / 'droid-session.jsonl'), 'offset': 0}

        def try_get_events(self, state):
            return [], state

    monkeypatch.setattr(droid_adapter_module, 'load_project_session', lambda work_dir, instance=None: FakeSession())
    monkeypatch.setattr(droid_adapter_module, 'get_backend_for_session', lambda data: DeadBackend())
    monkeypatch.setattr(droid_adapter_module, 'DroidLogReader', EmptyReader)

    app = CcbdApp(project_root)
    _freeze_next_job_id(app, monkeypatch, fixed_req_id)
    app.paths.workspace_path('demo').mkdir(parents=True, exist_ok=True)
    thread = threading.Thread(target=app.serve_forever, kwargs={'poll_interval': 0.05}, daemon=True)
    thread.start()
    _wait_for(app.paths.ccbd_socket_path)

    client = CcbdClient(app.paths.ccbd_socket_path)
    client.attach(
        agent_name='demo',
        workspace_path=str(app.paths.workspace_path('demo')),
        backend_type='pane-backed',
        runtime_ref='tmux:%5',
        session_ref='droid-session-id',
    )

    submit = client.submit(
        MessageEnvelope(
            project_id=app.project_id,
            to_agent='demo',
            from_actor='user',
            body='hello droid dead',
            task_id='task-droid-dead',
            reply_to=None,
            message_type='ask',
            delivery_scope=DeliveryScope.SINGLE,
        )
    )
    job_id = submit['job_id']

    failed = _wait_for_job_status(client, job_id, 'failed', timeout=3.0)
    assert 'ccb screen demo' in failed['reply']
    assert f'ccb trace {job_id}' in failed['reply']
    assert failed['completion_reason'] == 'pane_dead'
    assert failed['completion_confidence'] == 'degraded'

    shutdown = client.shutdown()
    assert shutdown['state'] == 'unmounted'
    thread.join(timeout=2)
    assert not thread.is_alive()
