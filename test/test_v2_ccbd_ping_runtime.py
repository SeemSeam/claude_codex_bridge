from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agents.models import (
    AgentRuntime,
    AgentState,
    AgentSpec,
    PermissionMode,
    ProjectConfig,
    QueuePolicy,
    RestoreMode,
    RuntimeMode,
    WorkspaceMode,
)
from ccbd.handlers.ping_runtime.handler import build_ping_handler
from ccbd.handlers.ping_runtime.payloads import build_agent_payload, build_ccbd_payload
from ccbd.models import LeaseHealth
from cli.services.ping import ping_target
from cli.models import ParsedPingCommand
from storage.path_helpers import SocketPlacement


def _config() -> ProjectConfig:
    spec = AgentSpec(
        name='demo',
        provider='codex',
        target='.',
        workspace_mode=WorkspaceMode.GIT_WORKTREE,
        workspace_root=None,
        runtime_mode=RuntimeMode.PANE_BACKED,
        restore_default=RestoreMode.AUTO,
        permission_default=PermissionMode.MANUAL,
        queue_policy=QueuePolicy.SERIAL_PER_AGENT,
    )
    return ProjectConfig(version=2, default_agents=('demo',), agents={'demo': spec})


def _inspection(
    *,
    phase: str,
    desired_state: str,
    socket_path: str = '/home/demo/.local/state/ccb/projects/proj-1/ccbd/ccbd.sock',
):
    return SimpleNamespace(
        phase=phase,
        desired_state=desired_state,
        health=LeaseHealth.UNMOUNTED,
        generation=7,
        socket_path=socket_path,
        pid_alive=False,
        socket_connectable=False,
        heartbeat_fresh=False,
        takeover_allowed=True,
        reason='lease_unmounted',
        startup_id='startup-123' if phase == 'starting' else None,
        startup_stage='spawn_requested' if phase == 'starting' else None,
        last_progress_at='2026-04-21T00:00:01Z' if phase == 'starting' else None,
        startup_deadline_at='2026-04-21T00:00:20Z' if phase == 'starting' else None,
        last_failure_reason='startup_in_progress' if phase == 'starting' else None,
        shutdown_intent=None,
        lease=SimpleNamespace(
            mount_state=SimpleNamespace(value='unmounted'),
            socket_path=socket_path,
            last_heartbeat_at='2026-04-21T00:00:00Z',
        ),
    )


def _paths() -> SimpleNamespace:
    return SimpleNamespace(
        ccbd_socket_placement=SocketPlacement(
            preferred_path=Path('/home/demo/.local/state/ccb/projects/proj-1/ccbd/ccbd.sock'),
            effective_path=Path('/home/demo/.local/state/ccb/projects/proj-1/ccbd/ccbd.sock'),
            root_kind='runtime',
            fallback_reason=None,
            filesystem_hint=None,
        ),
        ccbd_tmux_socket_placement=SocketPlacement(
            preferred_path=Path('/home/demo/.local/state/ccb/projects/proj-1/ccbd/tmux.sock'),
            effective_path=Path('/home/demo/.local/state/ccb/projects/proj-1/ccbd/tmux.sock'),
            root_kind='runtime',
            fallback_reason=None,
            filesystem_hint=None,
        ),
    )


def _metrics() -> SimpleNamespace:
    return SimpleNamespace(
        last_request_queue_wait_s=0.012,
        last_submit_duration_s=0.034,
        last_ping_duration_s=0.056,
        last_handler_latency_s_by_op={'ping': 0.056, 'project_view': 0.067},
        last_maintenance_duration_s=0.078,
        last_heartbeat_duration_s=0.089,
        heartbeat_step_duration_s={'health_monitor': 0.001, 'runtime_supervision': 0.002},
        last_heartbeat_agents_inspected=1,
        last_heartbeat_runtime_store_writes=0,
        pending_maintenance_ticks=2,
        last_project_view_response_duration_s=0.044,
        last_project_view_build_duration_s=0.045,
        project_view_cache_hits=3,
        project_view_cache_misses=4,
        last_project_view_tmux_command_count=5,
        last_project_view_capture_pane_count=1,
        last_project_view_store_scan_count=2,
        service_graph_version=1,
        service_graph_created_at='2026-05-29T00:00:00Z',
        service_graph_retained_count=1,
        service_graph_retained_count_scope='published_graph_count_not_inflight_retention',
        last_reload_duration_s=None,
        last_reload_plan_class=None,
        last_reload_error=None,
        process_snapshot=lambda: {
            'rss_bytes': 123456,
            'virtual_memory_bytes': 654321,
            'fd_count': 8,
            'thread_count': 3,
        },
    )


def test_build_ccbd_payload_prefers_lifecycle_phase_over_lease_mount_state() -> None:
    payload = build_ccbd_payload(
        project_id='proj-1',
        config=_config(),
        paths=_paths(),
        inspection=_inspection(phase='starting', desired_state='running'),
        execution_summary={},
        restore_summary={},
        namespace_summary={},
        namespace_event_summary={},
        start_policy_summary={},
        control_plane_metrics=_metrics(),
    )

    assert payload['mount_state'] == 'starting'
    assert payload['desired_state'] == 'running'
    assert payload['socket_path'] == '/home/demo/.local/state/ccb/projects/proj-1/ccbd/ccbd.sock'
    assert payload['preferred_socket_path'] == '/home/demo/.local/state/ccb/projects/proj-1/ccbd/ccbd.sock'
    assert payload['effective_socket_path'] == '/home/demo/.local/state/ccb/projects/proj-1/ccbd/ccbd.sock'
    assert payload['socket_root_kind'] == 'runtime'
    assert payload['socket_fallback_reason'] is None
    assert payload['socket_filesystem_hint'] is None
    assert payload['tmux_socket_path'] == '/home/demo/.local/state/ccb/projects/proj-1/ccbd/tmux.sock'
    assert payload['diagnostics']['last_request_queue_wait_s'] == 0.012
    assert payload['diagnostics']['last_submit_duration_s'] == 0.034
    assert payload['diagnostics']['last_ping_duration_s'] == 0.056
    assert payload['diagnostics']['last_handler_latency_s_by_op'] == {'ping': 0.056, 'project_view': 0.067}
    assert payload['diagnostics']['last_maintenance_duration_s'] == 0.078
    assert payload['diagnostics']['last_heartbeat_duration_s'] == 0.089
    assert payload['diagnostics']['heartbeat_step_duration_s'] == {
        'health_monitor': 0.001,
        'runtime_supervision': 0.002,
    }
    assert payload['diagnostics']['last_heartbeat_agents_inspected'] == 1
    assert payload['diagnostics']['last_heartbeat_runtime_store_writes'] == 0
    assert payload['diagnostics']['pending_maintenance_ticks'] == 2
    assert payload['diagnostics']['last_project_view_response_duration_s'] == 0.044
    assert payload['diagnostics']['last_project_view_build_duration_s'] == 0.045
    assert payload['diagnostics']['project_view_cache_hits'] == 3
    assert payload['diagnostics']['project_view_cache_misses'] == 4
    assert payload['diagnostics']['last_project_view_tmux_command_count'] == 5
    assert payload['diagnostics']['last_project_view_capture_pane_count'] == 1
    assert payload['diagnostics']['last_project_view_store_scan_count'] == 2
    assert payload['diagnostics']['rss_bytes'] == 123456
    assert payload['diagnostics']['virtual_memory_bytes'] == 654321
    assert payload['diagnostics']['fd_count'] == 8
    assert payload['diagnostics']['thread_count'] == 3
    assert payload['diagnostics']['service_graph_version'] == 1
    assert payload['diagnostics']['service_graph_created_at'] == '2026-05-29T00:00:00Z'
    assert payload['diagnostics']['service_graph_retained_count'] == 1
    assert (
        payload['diagnostics']['service_graph_retained_count_scope']
        == 'published_graph_count_not_inflight_retention'
    )
    assert payload['diagnostics']['last_reload_duration_s'] is None
    assert payload['diagnostics']['last_reload_plan_class'] is None
    assert payload['diagnostics']['last_reload_error'] is None
    assert payload['diagnostics']['last_failure_reason'] == 'startup_in_progress'
    assert payload['diagnostics']['startup_id'] == 'startup-123'
    assert payload['diagnostics']['startup_stage'] == 'spawn_requested'
    assert payload['diagnostics']['last_progress_at'] == '2026-04-21T00:00:01Z'
    assert payload['diagnostics']['startup_deadline_at'] == '2026-04-21T00:00:20Z'


def test_ping_handler_all_uses_lifecycle_phase_for_ccbd_state() -> None:
    config = _config()

    def _unexpected_summary():
        raise AssertionError('ccbd-only summaries should not load for ping all')

    handler = build_ping_handler(
        project_id='proj-1',
        config=config,
        paths=_paths(),
        registry=SimpleNamespace(
            list_known_agents=lambda: ('demo',),
            spec_for=lambda name: config.agents[name],
            get=lambda name: None,
        ),
        health_monitor=SimpleNamespace(
            check_all=lambda: {},
            daemon_health=lambda: _inspection(phase='starting', desired_state='running'),
        ),
        execution_registry=SimpleNamespace(get=lambda provider: None),
        execution_state_store=SimpleNamespace(summary=_unexpected_summary),
        restore_report_store=SimpleNamespace(load=_unexpected_summary),
        namespace_state_store=SimpleNamespace(load=_unexpected_summary),
        namespace_event_store=SimpleNamespace(load_latest=_unexpected_summary),
        start_policy_store=SimpleNamespace(load=_unexpected_summary),
        metrics=_metrics(),
    )

    payload = handler({'target': 'all'})

    assert payload['ccbd_state'] == 'starting'
    assert payload['agents'][0]['mount_state'] == 'starting'
    assert payload['agents'][0]['diagnostics']['desired_state'] == 'running'


def test_ping_handler_ccbd_does_not_force_health_check() -> None:
    config = _config()

    def _unexpected_check_all():
        raise AssertionError('check_all should not run for light ping')

    handler = build_ping_handler(
        project_id='proj-1',
        config=config,
        paths=_paths(),
        registry=SimpleNamespace(
            list_known_agents=lambda: ('demo',),
            spec_for=lambda name: config.agents[name],
            get=lambda name: None,
        ),
        health_monitor=SimpleNamespace(
            check_all=_unexpected_check_all,
            daemon_health=lambda: _inspection(phase='mounted', desired_state='running'),
        ),
        execution_registry=SimpleNamespace(get=lambda provider: None),
        execution_state_store=SimpleNamespace(summary=lambda: {}),
        metrics=_metrics(),
    )

    payload = handler({'target': 'ccbd'})

    assert payload['mount_state'] == 'mounted'
    assert payload['diagnostics']['last_ping_duration_s'] >= 0.0


def test_ping_handler_prefers_local_daemon_health() -> None:
    config = _config()
    calls: list[str] = []

    handler = build_ping_handler(
        project_id='proj-1',
        config=config,
        paths=_paths(),
        registry=SimpleNamespace(
            list_known_agents=lambda: ('demo',),
            spec_for=lambda name: config.agents[name],
            get=lambda name: None,
        ),
        health_monitor=SimpleNamespace(
            daemon_health=lambda: calls.append('remote') or _inspection(phase='failed', desired_state='running'),
            local_daemon_health=lambda: calls.append('local')
            or _inspection(phase='mounted', desired_state='running'),
        ),
        execution_registry=SimpleNamespace(get=lambda provider: None),
        execution_state_store=SimpleNamespace(summary=lambda: {}),
        metrics=_metrics(),
    )

    payload = handler({'target': 'ccbd'})

    assert payload['mount_state'] == 'mounted'
    assert calls == ['local']


def test_build_agent_payload_prefers_runtime_mount_state_over_project_phase() -> None:
    config = _config()
    runtime = AgentRuntime(
        agent_name='demo',
        state=AgentState.BUSY,
        pid=123,
        started_at='2026-04-22T00:00:00Z',
        last_seen_at='2026-04-22T00:00:01Z',
        runtime_ref='tmux:%1',
        session_ref='session-1',
        workspace_path='/tmp/ws',
        project_id='proj-1',
        backend_type='pane-backed',
        queue_depth=1,
        socket_path=None,
        health='healthy',
        provider='codex',
    )

    def _unexpected_summary():
        raise AssertionError('ccbd-only summaries should not load for ping agent')

    handler = build_ping_handler(
        project_id='proj-1',
        config=config,
        paths=_paths(),
        registry=SimpleNamespace(
            list_known_agents=lambda: ('demo',),
            spec_for=lambda name: config.agents[name],
            get=lambda name: runtime,
        ),
        health_monitor=SimpleNamespace(
            check_all=lambda: {},
            daemon_health=lambda: _inspection(phase='failed', desired_state='running'),
        ),
        execution_registry=SimpleNamespace(get=lambda provider: None),
        execution_state_store=SimpleNamespace(summary=_unexpected_summary),
        restore_report_store=SimpleNamespace(load=_unexpected_summary),
        namespace_state_store=SimpleNamespace(load=_unexpected_summary),
        namespace_event_store=SimpleNamespace(load_latest=_unexpected_summary),
        start_policy_store=SimpleNamespace(load=_unexpected_summary),
        metrics=_metrics(),
    )

    payload = handler({'target': 'demo'})

    assert payload['mount_state'] == 'mounted'
    assert payload['runtime_state'] == 'busy'
    assert payload['health'] == 'healthy'


def test_ping_target_unmounted_ccbd_includes_timing_fields(monkeypatch, tmp_path: Path) -> None:
    context = SimpleNamespace()

    monkeypatch.setattr(
        'cli.services.ping.ping_local_state',
        lambda _context: SimpleNamespace(
            project_id='proj-1',
            mount_state='unmounted',
            health='unmounted',
            generation=0,
            project_anchor_path='/tmp/repo/.ccb',
            runtime_state_root='/tmp/repo/.ccb',
            runtime_root_kind='project',
            runtime_relocation_reason=None,
            runtime_filesystem_hint=None,
            runtime_marker_status='not_required',
            socket_path=None,
            preferred_socket_path='/tmp/repo/.ccb/ccbd/ccbd.sock',
            effective_socket_path='/tmp/repo/.ccb/ccbd/ccbd.sock',
            socket_root_kind='project',
            socket_fallback_reason=None,
            socket_filesystem_hint=None,
            tmux_socket_path='/tmp/repo/.ccb/ccbd/tmux.sock',
            tmux_preferred_socket_path='/tmp/repo/.ccb/ccbd/tmux.sock',
            tmux_effective_socket_path='/tmp/repo/.ccb/ccbd/tmux.sock',
            tmux_socket_root_kind='project',
            tmux_socket_fallback_reason=None,
            tmux_socket_filesystem_hint=None,
            last_heartbeat_at=None,
            pid_alive=False,
            socket_connectable=False,
            heartbeat_fresh=False,
            takeover_allowed=True,
            reason='lease_unmounted',
            startup_id=None,
            startup_stage=None,
            last_progress_at=None,
            startup_deadline_at=None,
            last_failure_reason=None,
            shutdown_intent=None,
        ),
    )

    payload = ping_target(context, ParsedPingCommand(project=None, target='ccbd'))

    assert payload['mount_state'] == 'unmounted'
    assert payload['last_request_queue_wait_s'] is None
    assert payload['last_submit_duration_s'] is None
    assert payload['last_ping_duration_s'] is None
    assert payload['last_maintenance_duration_s'] is None
    assert payload['last_heartbeat_duration_s'] is None
    assert payload['pending_maintenance_ticks'] is None


def test_ping_target_all_uses_non_mutating_probe(monkeypatch) -> None:
    context = SimpleNamespace()
    seen: list[bool] = []

    monkeypatch.setattr(
        'cli.services.ping.ping_local_state',
        lambda _context: SimpleNamespace(
            mount_state='mounted',
            project_id='proj-1',
            health='healthy',
            generation=1,
            project_anchor_path='/tmp/repo/.ccb',
            runtime_state_root='/tmp/repo/.ccb',
            runtime_root_kind='project',
            runtime_relocation_reason=None,
            runtime_filesystem_hint=None,
            runtime_marker_status='not_required',
            socket_path='/tmp/repo/.ccb/ccbd/ccbd.sock',
            preferred_socket_path='/tmp/repo/.ccb/ccbd/ccbd.sock',
            effective_socket_path='/tmp/repo/.ccb/ccbd/ccbd.sock',
            socket_root_kind='project',
            socket_fallback_reason=None,
            socket_filesystem_hint=None,
            tmux_socket_path='/tmp/repo/.ccb/ccbd/tmux.sock',
            tmux_preferred_socket_path='/tmp/repo/.ccb/ccbd/tmux.sock',
            tmux_effective_socket_path='/tmp/repo/.ccb/ccbd/tmux.sock',
            tmux_socket_root_kind='project',
            tmux_socket_fallback_reason=None,
            tmux_socket_filesystem_hint=None,
            last_heartbeat_at='2026-05-08T00:00:00Z',
            pid_alive=True,
            socket_connectable=True,
            heartbeat_fresh=True,
            takeover_allowed=False,
            reason='healthy',
            startup_id=None,
            startup_stage=None,
            last_progress_at=None,
            startup_deadline_at=None,
            last_failure_reason=None,
            shutdown_intent=None,
        ),
    )

    class _Client:
        def ping(self, target: str) -> dict:
            return {'project_id': 'proj-1', 'ccbd_state': 'mounted', 'agents': [], 'target': target}

    def _connect(_context, *, allow_restart_stale):
        seen.append(allow_restart_stale)
        return SimpleNamespace(client=_Client())

    monkeypatch.setattr('cli.services.ping.connect_mounted_daemon', _connect)

    payload = ping_target(context, ParsedPingCommand(project=None, target='all'))

    assert payload['target'] == 'all'
    assert seen == [False]


def test_ping_target_agent_uses_non_mutating_probe(monkeypatch) -> None:
    context = SimpleNamespace()
    seen: list[bool] = []

    monkeypatch.setattr(
        'cli.services.ping.ping_local_state',
        lambda _context: SimpleNamespace(
            mount_state='mounted',
            project_id='proj-1',
            health='healthy',
            generation=1,
            project_anchor_path='/tmp/repo/.ccb',
            runtime_state_root='/tmp/repo/.ccb',
            runtime_root_kind='project',
            runtime_relocation_reason=None,
            runtime_filesystem_hint=None,
            runtime_marker_status='not_required',
            socket_path='/tmp/repo/.ccb/ccbd/ccbd.sock',
            preferred_socket_path='/tmp/repo/.ccb/ccbd/ccbd.sock',
            effective_socket_path='/tmp/repo/.ccb/ccbd/ccbd.sock',
            socket_root_kind='project',
            socket_fallback_reason=None,
            socket_filesystem_hint=None,
            tmux_socket_path='/tmp/repo/.ccb/ccbd/tmux.sock',
            tmux_preferred_socket_path='/tmp/repo/.ccb/ccbd/tmux.sock',
            tmux_effective_socket_path='/tmp/repo/.ccb/ccbd/tmux.sock',
            tmux_socket_root_kind='project',
            tmux_socket_fallback_reason=None,
            tmux_socket_filesystem_hint=None,
            last_heartbeat_at='2026-05-08T00:00:00Z',
            pid_alive=True,
            socket_connectable=True,
            heartbeat_fresh=True,
            takeover_allowed=False,
            reason='healthy',
            startup_id=None,
            startup_stage=None,
            last_progress_at=None,
            startup_deadline_at=None,
            last_failure_reason=None,
            shutdown_intent=None,
        ),
    )

    class _Client:
        def ping(self, target: str) -> dict:
            return {'project_id': 'proj-1', 'agent_name': target, 'mount_state': 'mounted', 'health': 'healthy', 'diagnostics': {}}

    def _connect(_context, *, allow_restart_stale):
        seen.append(allow_restart_stale)
        return SimpleNamespace(client=_Client())

    monkeypatch.setattr('cli.services.ping.connect_mounted_daemon', _connect)

    payload = ping_target(context, ParsedPingCommand(project=None, target='demo'))

    assert payload['agent_name'] == 'demo'
    assert seen == [False]


def _usage_limit_assessment():
    """A ProviderPaneAssessment-shaped namespace with a usage-limit signal."""
    return SimpleNamespace(
        binding=None,
        session=SimpleNamespace(pane_id='%9'),
        terminal='tmux',
        pane_state='alive',
        health='usage-limited',
        pane_signal_state='usage_limit',
        pane_signal_reason='provider_usage_limit',
        retry_after='2026-07-02T10:21:00',
        pane_tail="You've hit your usage limit. try again at Jul 2nd, 2026 10:21 AM.",
    )


def test_build_agent_payload_includes_provider_health_when_assessment_supplied() -> None:
    config = _config()
    runtime = AgentRuntime(
        agent_name='demo',
        state=AgentState.BUSY,
        pid=123,
        started_at='2026-04-22T00:00:00Z',
        last_seen_at='2026-04-22T00:00:01Z',
        runtime_ref='tmux:%1',
        session_ref='session-1',
        workspace_path='/tmp/ws',
        project_id='proj-1',
        backend_type='pane-backed',
        queue_depth=1,
        socket_path=None,
        health='healthy',
        provider='codex',
    )

    payload = build_agent_payload(
        project_id='proj-1',
        agent_name='demo',
        registry=SimpleNamespace(
            spec_for=lambda name: config.agents[name],
            get=lambda name: runtime,
        ),
        inspection=_inspection(phase='mounted', desired_state='running'),
        execution_registry=SimpleNamespace(get=lambda provider: None),
        provider_pane_assessment=_usage_limit_assessment(),
    )

    provider_health = payload['diagnostics']['provider_health']
    assert provider_health['state'] == 'usage-limited'
    assert provider_health['signal_state'] == 'usage_limit'
    assert provider_health['signal_reason'] == 'provider_usage_limit'
    assert provider_health['retry_after'] == '2026-07-02T10:21:00'


def test_build_agent_payload_omits_provider_health_when_assessment_healthy() -> None:
    config = _config()
    runtime = AgentRuntime(
        agent_name='demo',
        state=AgentState.BUSY,
        pid=123,
        started_at='2026-04-22T00:00:00Z',
        last_seen_at='2026-04-22T00:00:01Z',
        runtime_ref='tmux:%1',
        session_ref='session-1',
        workspace_path='/tmp/ws',
        project_id='proj-1',
        backend_type='pane-backed',
        queue_depth=1,
        socket_path=None,
        health='healthy',
        provider='codex',
    )

    payload = build_agent_payload(
        project_id='proj-1',
        agent_name='demo',
        registry=SimpleNamespace(
            spec_for=lambda name: config.agents[name],
            get=lambda name: runtime,
        ),
        inspection=_inspection(phase='mounted', desired_state='running'),
        execution_registry=SimpleNamespace(get=lambda provider: None),
        provider_pane_assessment=SimpleNamespace(
            binding=None,
            session=None,
            terminal='tmux',
            pane_state='alive',
            health='healthy',
            pane_signal_state=None,
            pane_signal_reason=None,
            retry_after=None,
            pane_tail=None,
        ),
    )

    assert 'provider_health' not in payload['diagnostics']


def test_build_agent_payload_omits_provider_health_when_no_assessment() -> None:
    config = _config()
    runtime = AgentRuntime(
        agent_name='demo',
        state=AgentState.BUSY,
        pid=123,
        started_at='2026-04-22T00:00:00Z',
        last_seen_at='2026-04-22T00:00:01Z',
        runtime_ref='tmux:%1',
        session_ref='session-1',
        workspace_path='/tmp/ws',
        project_id='proj-1',
        backend_type='pane-backed',
        queue_depth=1,
        socket_path=None,
        health='healthy',
        provider='codex',
    )

    payload = build_agent_payload(
        project_id='proj-1',
        agent_name='demo',
        registry=SimpleNamespace(
            spec_for=lambda name: config.agents[name],
            get=lambda name: runtime,
        ),
        inspection=_inspection(phase='mounted', desired_state='running'),
        execution_registry=SimpleNamespace(get=lambda provider: None),
    )

    assert 'provider_health' not in payload['diagnostics']


def test_ping_handler_surfaces_provider_health_from_health_monitor_assessment() -> None:
    config = _config()
    runtime = AgentRuntime(
        agent_name='demo',
        state=AgentState.BUSY,
        pid=123,
        started_at='2026-04-22T00:00:00Z',
        last_seen_at='2026-04-22T00:00:01Z',
        runtime_ref='tmux:%1',
        session_ref='session-1',
        workspace_path='/tmp/ws',
        project_id='proj-1',
        backend_type='pane-backed',
        queue_depth=1,
        socket_path=None,
        health='healthy',
        provider='codex',
    )
    captured = {}

    def _assess(*, runtime, registry, session_bindings, namespace_state_store):
        captured['called'] = True
        return _usage_limit_assessment()

    handler = build_ping_handler(
        project_id='proj-1',
        config=config,
        paths=_paths(),
        registry=SimpleNamespace(
            list_known_agents=lambda: ('demo',),
            spec_for=lambda name: config.agents[name],
            get=lambda name: runtime,
        ),
        health_monitor=SimpleNamespace(
            check_all=lambda: {},
            daemon_health=lambda: _inspection(phase='mounted', desired_state='running'),
            _assess_provider_pane=_assess,
            _registry=SimpleNamespace(spec_for=lambda name: config.agents[name]),
            _session_bindings={},
            _namespace_state_store=None,
        ),
        execution_registry=SimpleNamespace(get=lambda provider: None),
    )

    payload = handler({'target': 'demo'})

    assert captured.get('called') is True
    provider_health = payload['diagnostics']['provider_health']
    assert provider_health['signal_state'] == 'usage_limit'
    assert provider_health['retry_after'] == '2026-07-02T10:21:00'


def test_ping_handler_skips_provider_health_when_monitor_lacks_assess_seam() -> None:
    config = _config()
    runtime = AgentRuntime(
        agent_name='demo',
        state=AgentState.BUSY,
        pid=123,
        started_at='2026-04-22T00:00:00Z',
        last_seen_at='2026-04-22T00:00:01Z',
        runtime_ref='tmux:%1',
        session_ref='session-1',
        workspace_path='/tmp/ws',
        project_id='proj-1',
        backend_type='pane-backed',
        queue_depth=1,
        socket_path=None,
        health='healthy',
        provider='codex',
    )

    handler = build_ping_handler(
        project_id='proj-1',
        config=config,
        paths=_paths(),
        registry=SimpleNamespace(
            list_known_agents=lambda: ('demo',),
            spec_for=lambda name: config.agents[name],
            get=lambda name: runtime,
        ),
        # health_monitor has no _assess_provider_pane seam (legacy/stub).
        health_monitor=SimpleNamespace(
            check_all=lambda: {},
            daemon_health=lambda: _inspection(phase='mounted', desired_state='running'),
        ),
        execution_registry=SimpleNamespace(get=lambda provider: None),
    )

    payload = handler({'target': 'demo'})

    assert 'provider_health' not in payload['diagnostics']
