from __future__ import annotations

from dataclasses import replace
import os
from pathlib import Path
import re
import subprocess
from types import SimpleNamespace

from agents.models import AgentSpec, PermissionMode, QueuePolicy, RestoreMode, RuntimeMode, WorkspaceMode
from agents.store import AgentSpecStore
from ccbd.lifecycle_report_store import CcbdStartupReportStore
from ccbd.models import CcbdStartupReport
from cli.context import CliContextBuilder
from cli.models import ParsedStartCommand
from cli.services.start import _refresh_running_sidebar_helpers, start_agents
from cli.services.start_runtime import (
    _readiness_trace_payload,
    start_agents as start_agents_runtime,
)
from cli.startup_process_trace import (
    capture_source_wrapper_trace,
    consume_process_bootstrap_trace,
    mark_ccb_main,
)
from cli.services.tmux_project_cleanup import ProjectTmuxCleanupSummary
from project.resolver import bootstrap_project
from storage.paths import PathLayout
from workspace.binding import WorkspaceBindingStore
from workspace.materializer import WorkspaceMaterializer
from workspace.planner import WorkspacePlanner
import pytest


def _init_git_repo(project_root: Path) -> None:
    project_root.mkdir(parents=True, exist_ok=True)
    (project_root / 'README.md').write_text('hello\n', encoding='utf-8')
    subprocess.run(['git', 'init'], cwd=project_root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    subprocess.run(['git', 'config', 'user.email', 'test@example.com'], cwd=project_root, check=True)
    subprocess.run(['git', 'config', 'user.name', 'Test User'], cwd=project_root, check=True)
    subprocess.run(['git', 'add', '.'], cwd=project_root, check=True)
    subprocess.run(['git', 'commit', '-m', 'init'], cwd=project_root, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


def _worktree_spec(name: str = 'agent1') -> AgentSpec:
    return AgentSpec(
        name=name,
        provider='codex',
        target='.',
        workspace_mode=WorkspaceMode.GIT_WORKTREE,
        workspace_root=None,
        runtime_mode=RuntimeMode.PANE_BACKED,
        restore_default=RestoreMode.AUTO,
        permission_default=PermissionMode.MANUAL,
        queue_policy=QueuePolicy.SERIAL_PER_AGENT,
    )


def test_start_agents_calls_ccbd_start_with_cli_flags(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-start-thin-client'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('demo:codex\n', encoding='utf-8')
    bootstrap_project(project_root)
    command = ParsedStartCommand(project=None, agent_names=('demo',), restore=True, auto_permission=True)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)

    seen: dict[str, object] = {}

    class _FakeClient:
        def start(self, **kwargs):
            seen.update(kwargs)
            return {
                'project_root': str(project_root),
                'project_id': context.project.project_id,
                'started': ['demo'],
                'socket_path': str(context.paths.ccbd_socket_path),
                'cleanup_summaries': [],
            }

    monkeypatch.setattr(
        'cli.services.start.ensure_daemon_started',
        lambda context: SimpleNamespace(client=_FakeClient(), started=True),
    )

    summary = start_agents(context, command)

    startup_run_id = str(seen.pop('startup_run_id'))
    assert re.fullmatch(r'start_[0-9a-f]{32}', startup_run_id)
    assert seen == {
        'agent_names': ('demo',),
        'restore': True,
        'auto_permission': True,
        'daemon_started': True,
    }
    assert summary.project_root == str(project_root)
    assert summary.project_id == context.project.project_id
    assert summary.started == ('demo',)
    assert summary.daemon_started is True
    assert summary.startup_run_id == startup_run_id
    assert summary.socket_path == str(context.paths.ccbd_socket_path)


def test_foreground_start_refreshes_sidebar_with_current_cli_when_daemon_is_reused(
    tmp_path: Path,
    monkeypatch,
) -> None:
    project_root = tmp_path / 'repo-start-sidebar-upgrade'
    namespace = SimpleNamespace(
        tmux_socket_path=str(project_root / '.ccb' / 'ccbd' / 'tmux.sock'),
        tmux_session_name='ccb-project',
        namespace_epoch=7,
    )
    controller = SimpleNamespace(load=lambda: namespace)
    backend = object()
    topology_plan = object()
    seen: dict[str, object] = {}
    context = SimpleNamespace(
        paths=PathLayout(project_root),
        project=SimpleNamespace(project_id='project-1', project_root=project_root),
    )

    monkeypatch.setattr(
        'cli.services.start.ProjectNamespaceController',
        lambda layout, project_id: controller,
    )
    monkeypatch.setattr(
        'cli.services.start.load_project_config',
        lambda root: SimpleNamespace(config=object()),
    )
    monkeypatch.setattr(
        'cli.services.start.build_namespace_topology_plan',
        lambda config: topology_plan,
    )
    monkeypatch.setattr(
        'cli.services.start.make_terminal_backend',
        lambda *, socket_path: seen.setdefault('backend_socket', socket_path) and backend,
    )

    def refresh(
        current_controller,
        current_backend,
        *,
        topology_plan,
        tmux_session_name,
        namespace_epoch,
    ):
        seen['controller'] = current_controller
        seen['backend'] = current_backend
        seen['topology_plan'] = topology_plan
        seen['tmux_session_name'] = tmux_session_name
        seen['namespace_epoch'] = namespace_epoch
        return ('%7',)

    monkeypatch.setattr('cli.services.start.refresh_topology_sidebar_helpers', refresh)

    result = _refresh_running_sidebar_helpers(context)

    assert result == {'status': 'refreshed', 'panes': ('%7',)}
    assert seen == {
        'backend_socket': namespace.tmux_socket_path,
        'controller': controller,
        'backend': backend,
        'topology_plan': topology_plan,
        'tmux_session_name': 'ccb-project',
        'namespace_epoch': 7,
    }


def test_start_agents_passes_terminal_size_when_provided(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-start-terminal-size'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('demo:codex\n', encoding='utf-8')
    bootstrap_project(project_root)
    command = ParsedStartCommand(project=None, agent_names=('demo',), restore=True, auto_permission=True)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)

    seen: dict[str, object] = {}

    class _FakeClient:
        def start(self, **kwargs):
            seen.update(kwargs)
            return {
                'project_root': str(project_root),
                'project_id': context.project.project_id,
                'started': ['demo'],
                'socket_path': str(context.paths.ccbd_socket_path),
                'cleanup_summaries': [],
            }

    monkeypatch.setattr(
        'cli.services.start.ensure_daemon_started',
        lambda context: SimpleNamespace(client=_FakeClient(), started=True),
    )

    start_agents(context, command, terminal_size=(233, 61))

    assert seen['terminal_size'] == (233, 61)


def test_start_agents_uses_startup_transaction_timeout_for_start_rpc(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-start-rpc-timeout'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('demo:codex\n', encoding='utf-8')
    bootstrap_project(project_root)
    command = ParsedStartCommand(project=None, agent_names=('demo',), restore=False, auto_permission=False)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    events: list[tuple[str, float | None]] = []

    class _StartClient:
        def __init__(self, timeout_s: float | None) -> None:
            self.timeout_s = timeout_s

        def start(self, **kwargs):
            del kwargs
            events.append(('start', self.timeout_s))
            return {
                'project_root': str(project_root),
                'project_id': context.project.project_id,
                'started': ['demo'],
                'socket_path': str(context.paths.ccbd_socket_path),
                'cleanup_summaries': [],
            }

    class _BaseClient:
        def with_timeout(self, timeout_s: float | None):
            events.append(('with_timeout', timeout_s))
            return _StartClient(timeout_s)

    monkeypatch.setattr('cli.services.start.STARTUP_TRANSACTION_TIMEOUT_S', 12.5)
    monkeypatch.setattr(
        'cli.services.start.ensure_daemon_started',
        lambda context: SimpleNamespace(client=_BaseClient(), started=False),
    )

    summary = start_agents(context, command)

    assert events == [('with_timeout', 12.5), ('start', 12.5)]
    assert summary.started == ('demo',)


def test_start_runtime_keeps_legacy_report_store_injection_without_using_it(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-start-legacy-report-injection'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('demo:codex\n', encoding='utf-8')
    bootstrap_project(project_root)
    command = ParsedStartCommand(project=None, agent_names=('demo',), restore=False, auto_permission=False)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)

    class _FakeClient:
        def start(self, **kwargs):
            return {
                'project_root': str(project_root),
                'project_id': context.project.project_id,
                'started': ['demo'],
                'socket_path': str(context.paths.ccbd_socket_path),
                'cleanup_summaries': [],
                'startup_run_id': kwargs['startup_run_id'],
            }

    class _ExplodingLegacyStore:
        def __init__(self, paths):
            del paths
            raise AssertionError('foreground CLI must not construct the startup report store')

    summary = start_agents_runtime(
        context,
        command,
        ensure_daemon_started_fn=lambda context: SimpleNamespace(client=_FakeClient(), started=False),
        cleanup_summary_cls=ProjectTmuxCleanupSummary,
        startup_report_store_cls=_ExplodingLegacyStore,
    )

    assert summary.started == ('demo',)


def test_start_agents_attaches_maintenance_heartbeat_startup_summary(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-start-heartbeat'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('demo:codex\n', encoding='utf-8')
    bootstrap_project(project_root)
    command = ParsedStartCommand(project=None, agent_names=('demo',), restore=False, auto_permission=False)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)

    class _FakeClient:
        def start(self, **kwargs):
            del kwargs
            return {
                'project_root': str(project_root),
                'project_id': context.project.project_id,
                'started': ['demo'],
                'socket_path': str(context.paths.ccbd_socket_path),
                'cleanup_summaries': [],
            }

    monkeypatch.setattr(
        'cli.services.start.ensure_daemon_started',
        lambda context: SimpleNamespace(client=_FakeClient(), started=False),
    )
    monkeypatch.setattr(
        'cli.services.start.startup_ensure_maintenance_heartbeat',
        lambda context: {'maintenance_status': 'ok', 'action': 'tick', 'tick_status': 'healthy'},
    )

    summary = start_agents(context, command)

    assert summary.maintenance_heartbeat == {
        'maintenance_status': 'ok',
        'action': 'tick',
        'tick_status': 'healthy',
    }


def test_start_agents_parses_cleanup_summaries_from_ccbd_payload(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-start-cleanup'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('demo:codex\n', encoding='utf-8')
    bootstrap_project(project_root)
    command = ParsedStartCommand(project=None, agent_names=(), restore=False, auto_permission=False)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)

    class _FakeClient:
        def start(self, **kwargs):
            del kwargs
            return {
                'project_root': str(project_root),
                'project_id': context.project.project_id,
                'started': ['demo'],
                'socket_path': str(context.paths.ccbd_socket_path),
                'cleanup_summaries': [
                    {
                        'socket_name': 'sock-a',
                        'owned_panes': ['%44'],
                        'active_panes': ['%44'],
                        'orphaned_panes': [],
                        'killed_panes': [],
                    },
                ],
            }

    monkeypatch.setattr(
        'cli.services.start.ensure_daemon_started',
        lambda context: SimpleNamespace(client=_FakeClient(), started=False),
    )

    summary = start_agents(context, command)

    assert summary.daemon_started is False
    assert len(summary.cleanup_summaries) == 1
    assert summary.cleanup_summaries[0].socket_name == 'sock-a'
    assert summary.cleanup_summaries[0].owned_panes == ('%44',)


def test_start_agents_does_not_rewrite_daemon_owned_startup_report_after_rpc(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-start-report'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('demo:codex\n', encoding='utf-8')
    bootstrap_project(project_root)
    command = ParsedStartCommand(project=None, agent_names=('demo',), restore=False, auto_permission=False)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    CcbdStartupReportStore(context.paths).save(
        CcbdStartupReport(
            project_id=context.project.project_id,
            generated_at='2026-04-03T00:00:00Z',
            trigger='start_command',
            status='ok',
            requested_agents=('demo',),
            desired_agents=('demo',),
            restore_requested=False,
            auto_permission=False,
            daemon_generation=1,
            daemon_started=False,
            startup_run_id='start_' + 'b' * 32,
            config_signature='sig-1',
            inspection={},
            restore_summary={},
            actions_taken=('launch_runtime:demo',),
            cleanup_summaries=(),
            agent_results=(),
            failure_reason=None,
        )
    )

    class _FakeClient:
        def start(self, **kwargs):
            del kwargs
            return {
                'project_root': str(project_root),
                'project_id': context.project.project_id,
                'started': ['demo'],
                'socket_path': str(context.paths.ccbd_socket_path),
                'cleanup_summaries': [],
            }

    monkeypatch.setattr(
        'cli.services.start.ensure_daemon_started',
        lambda context: SimpleNamespace(client=_FakeClient(), started=True),
    )

    start_agents(context, command)

    report = CcbdStartupReportStore(context.paths).load()
    assert report is not None
    assert report.daemon_started is False
    assert report.startup_run_id == 'start_' + 'b' * 32


def test_start_agents_rejects_mismatched_rpc_correlation(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-start-report-mismatch'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('demo:codex\n', encoding='utf-8')
    bootstrap_project(project_root)
    command = ParsedStartCommand(project=None, agent_names=('demo',), restore=False, auto_permission=False)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)

    class _FakeClient:
        def start(self, **kwargs):
            del kwargs
            return {
                'project_root': str(project_root),
                'project_id': context.project.project_id,
                'started': ['demo'],
                'socket_path': str(context.paths.ccbd_socket_path),
                'cleanup_summaries': [],
                'startup_run_id': 'start_' + 'f' * 32,
            }

    monkeypatch.setattr(
        'cli.services.start.ensure_daemon_started',
        lambda context: SimpleNamespace(client=_FakeClient(), started=True),
    )

    with pytest.raises(RuntimeError, match='correlation mismatch'):
        start_agents(context, command)


def test_start_agents_reports_full_noninteractive_cli_timing_boundary(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-start-cli-timing'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('demo:codex\n', encoding='utf-8')
    bootstrap_project(project_root)
    command = ParsedStartCommand(project=None, agent_names=('demo',), restore=False, auto_permission=False)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)

    class _FakeClient:
        def start(self, **kwargs):
            return {
                'project_root': str(project_root),
                'project_id': context.project.project_id,
                'started': ['demo'],
                'socket_path': str(context.paths.ccbd_socket_path),
                'cleanup_summaries': [],
                'startup_run_id': kwargs['startup_run_id'],
            }

    monkeypatch.setattr(
        'cli.services.start.ensure_daemon_started',
        lambda context: SimpleNamespace(client=_FakeClient(), started=False),
    )
    monkeypatch.setattr('cli.services.start._refresh_running_sidebar_helpers', lambda context: {'status': 'current'})
    monkeypatch.setattr(
        'cli.services.start._attach_start_layout_summary',
        lambda context, summary: replace(summary, layout_summary={'layout_summary_status': 'ok'}),
    )
    monkeypatch.setattr('cli.services.start.startup_ensure_maintenance_heartbeat', lambda context: None)

    summary = start_agents(context, command)

    assert summary.cli_timings_ms is not None
    assert set(summary.cli_timings_ms) == {
        'cli_pre_rpc',
        'daemon_ensure',
        'start_rpc',
        'cli_post_rpc',
        'cli_total',
        'sidebar_helper_refresh',
        'layout_status',
        'maintenance_heartbeat',
    }
    assert all(value >= 0 for value in summary.cli_timings_ms.values())
    assert summary.cli_timings_ms['cli_total'] >= summary.cli_timings_ms['start_rpc']


def test_startup_process_trace_is_monotonic_and_consumed(monkeypatch) -> None:
    values = {
        'CCB_STARTUP_TIMING_TRACE': '1',
        'CCB_STARTUP_TRACE_ID': 'trace_' + 'a' * 32,
        'CCB_STARTUP_TRACE_SPAWN_NS': '1000000',
        'CCB_STARTUP_TRACE_WRAPPER_ENTRY_NS': '2000000',
        'CCB_STARTUP_TRACE_WRAPPER_PRE_EXEC_NS': '4000000',
        'CCB_TEST_ENTRYPOINT': '1',
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)

    capture_source_wrapper_trace(7_000_000)
    mark_ccb_main(11_000_000)
    trace_id, timings, origin_ns = consume_process_bootstrap_trace(16_000_000)

    assert trace_id == 'trace_' + 'a' * 32
    assert origin_ns == 7_000_000
    assert timings == {
        'popen_begin_to_ccb_test_entry': 1.0,
        'ccb_test_entry_to_pre_exec': 2.0,
        'ccb_test_pre_exec_to_ccb_py_entry': 3.0,
        'ccb_py_entry_to_main': 4.0,
        'ccb_py_main_to_cli_start': 5.0,
    }
    assert all(key not in os.environ for key in values if key != 'CCB_TEST_ENTRYPOINT')


def test_startup_process_trace_rejects_non_wrapper_envelope_and_consumes_raw_env(monkeypatch) -> None:
    values = {
        'CCB_STARTUP_TIMING_TRACE': '1',
        'CCB_STARTUP_TRACE_ID': 'trace_' + 'b' * 32,
        'CCB_STARTUP_TRACE_SPAWN_NS': '1000000',
        'CCB_STARTUP_TRACE_WRAPPER_ENTRY_NS': '2000000',
        'CCB_STARTUP_TRACE_WRAPPER_PRE_EXEC_NS': '3000000',
    }
    for key, value in values.items():
        monkeypatch.setenv(key, value)

    capture_source_wrapper_trace(4_000_000)
    trace_id, timings, origin_ns = consume_process_bootstrap_trace(5_000_000)

    assert trace_id is None
    assert timings is None
    assert origin_ns is None
    assert all(key not in os.environ for key in values)


def test_readiness_trace_payload_uses_cli_entry_origin_and_mounted_generation() -> None:
    payload = _readiness_trace_payload(
        trace_id='trace_' + 'c' * 32,
        origin_ns=1_000_000,
        attach_mode='no_attach',
        handle=SimpleNamespace(
            started=False,
            inspection=SimpleNamespace(generation=7, startup_id='keeper-startup-7'),
        ),
        control_plane_ready_ns=6_000_000,
    )

    assert payload is not None
    assert payload['trace_id'] == 'trace_' + 'c' * 32
    assert payload['origin_monotonic_ns'] == 1_000_000
    assert payload['expected_daemon_generation'] == 7
    assert payload['attach_mode'] == 'no_attach'
    assert payload['T1_lifecycle_intent']['status'] == 'not_required_already_mounted'
    assert payload['T2_control_plane_ready']['elapsed_ms'] == 5.0


def test_readiness_trace_payload_requires_positive_mounted_generation() -> None:
    payload = _readiness_trace_payload(
        trace_id='trace_' + 'd' * 32,
        origin_ns=1_000_000,
        attach_mode='no_attach',
        handle=SimpleNamespace(
            started=False,
            inspection=SimpleNamespace(generation=None, startup_id='keeper-startup-missing'),
        ),
        control_plane_ready_ns=6_000_000,
    )

    assert payload is None


def test_start_agents_attaches_compact_layout_identity_summary(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-start-layout-summary'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text(
        'version = 2\n\n[windows]\nmain = "frontdesk:fake"\nplan-orchestrate = "planner:fake"\n',
        encoding='utf-8',
    )
    bootstrap_project(project_root)
    command = ParsedStartCommand(project=None, agent_names=(), restore=False, auto_permission=False)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)

    class _FakeClient:
        def start(self, **kwargs):
            del kwargs
            return {
                'project_root': str(project_root),
                'project_id': context.project.project_id,
                'started': ['frontdesk', 'planner'],
                'socket_path': str(context.paths.ccbd_socket_path),
                'cleanup_summaries': [],
            }

    monkeypatch.setattr(
        'cli.services.start.ensure_daemon_started',
        lambda context: SimpleNamespace(client=_FakeClient(), started=True),
    )
    monkeypatch.setattr(
        'cli.services.start.layout_status',
        lambda context: {
            'layout_status': 'ok',
            'ccbd_state': 'mounted',
            'windows_explicit': True,
            'entry_window': 'main',
            'window_count': 2,
            'pane_count': 2,
            'dynamic_agent_count': 0,
            'loop_agent_count': 0,
            'runtime_agent_count': 2,
            'observed': {'observe_status': 'ok', 'observed_pane_count': 2},
            'windows': [
                {
                    'name': 'main',
                    'index': 1,
                    'pane_count': 1,
                    'runtime_pane_count': 1,
                    'agent_names': ['frontdesk'],
                    'agents': [
                        {
                            'agent': 'frontdesk',
                            'source': 'configured',
                            'agent_kind': 'configured',
                            'ownership_class': 'static_configured',
                            'dispatch_state': 'enabled',
                            'window_name': 'main',
                            'pane_id': '%1',
                            'pane_identity_source': 'observed',
                            'runtime_state': 'running',
                            'apply_status': None,
                            'failed_apply': False,
                        }
                    ],
                },
                {
                    'name': 'plan-orchestrate',
                    'index': 2,
                    'pane_count': 1,
                    'runtime_pane_count': 1,
                    'agent_names': ['planner'],
                    'agents': [],
                },
            ],
        },
    )

    summary = start_agents(context, command)

    assert summary.layout_summary is not None
    assert summary.layout_summary['layout_summary_status'] == 'ok'
    assert summary.layout_summary['window_count'] == 2
    assert summary.layout_summary['observed_pane_count'] == 2
    frontdesk = summary.layout_summary['windows'][0]['agents'][0]
    assert frontdesk['ownership_class'] == 'static_configured'
    assert frontdesk['pane_identity_source'] == 'observed'


def test_start_agents_surfaces_layout_summary_failure_without_failing_start(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-start-layout-summary-failure'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('demo:fake\n', encoding='utf-8')
    bootstrap_project(project_root)
    command = ParsedStartCommand(project=None, agent_names=(), restore=False, auto_permission=False)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)

    class _FakeClient:
        def start(self, **kwargs):
            del kwargs
            return {
                'project_root': str(project_root),
                'project_id': context.project.project_id,
                'started': ['demo'],
                'socket_path': str(context.paths.ccbd_socket_path),
                'cleanup_summaries': [],
            }

    def _raise_layout_status(_context):
        raise RuntimeError('layout probe failed')

    monkeypatch.setattr(
        'cli.services.start.ensure_daemon_started',
        lambda context: SimpleNamespace(client=_FakeClient(), started=False),
    )
    monkeypatch.setattr('cli.services.start.layout_status', _raise_layout_status)

    summary = start_agents(context, command)

    assert summary.started == ('demo',)
    assert summary.layout_summary == {
        'layout_summary_status': 'unavailable',
        'layout_status': 'unavailable',
        'error_type': 'RuntimeError',
        'error': 'layout probe failed',
    }


def test_start_agents_validates_config_before_starting_daemon(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-start-invalid-config'
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('agent1:codex, cmd\n', encoding='utf-8')
    bootstrap_project(project_root)
    command = ParsedStartCommand(project=None, agent_names=(), restore=False, auto_permission=False)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    daemon_calls: list[str] = []

    monkeypatch.setattr(
        'cli.services.start.ensure_daemon_started',
        lambda context: daemon_calls.append(str(context.project.project_root)) or SimpleNamespace(client=None, started=False),
    )

    with pytest.raises(Exception, match='layout_spec must anchor cmd as the first pane'):
        start_agents(context, command)

    assert daemon_calls == []


def test_start_agents_retires_removed_merged_worktree_before_start(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-start-retire-worktree'
    _init_git_repo(project_root)
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('demo:codex\n', encoding='utf-8')
    bootstrap_project(project_root)

    spec = _worktree_spec('agent1')
    layout = PathLayout(project_root)
    AgentSpecStore(layout).save(spec)
    plan = WorkspacePlanner().plan(spec, bootstrap_project(project_root))
    WorkspaceMaterializer().materialize(plan)
    WorkspaceBindingStore().save(plan)

    command = ParsedStartCommand(project=None, agent_names=(), restore=True, auto_permission=True)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)

    class _FakeClient:
        def start(self, **kwargs):
            del kwargs
            return {
                'project_root': str(project_root),
                'project_id': context.project.project_id,
                'started': ['demo'],
                'socket_path': str(context.paths.ccbd_socket_path),
                'cleanup_summaries': [],
            }

    monkeypatch.setattr(
        'cli.services.start.ensure_daemon_started',
        lambda context: SimpleNamespace(client=_FakeClient(), started=False),
    )

    summary = start_agents(context, command)

    assert summary.started == ('demo',)
    assert len(summary.worktree_retired) == 1
    assert summary.worktree_retired[0].agent_name == 'agent1'
    assert plan.workspace_path.exists() is False
    assert context.paths.agent_dir('agent1').exists() is False
    worktrees = subprocess.run(
        ['git', '-C', str(project_root), 'worktree', 'list', '--porcelain'],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ).stdout
    assert str(plan.workspace_path) not in worktrees


def test_start_agents_blocks_removed_unmerged_worktree_before_start(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-start-block-worktree'
    _init_git_repo(project_root)
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('demo:codex\n', encoding='utf-8')
    bootstrap_project(project_root)

    spec = _worktree_spec('agent1')
    layout = PathLayout(project_root)
    AgentSpecStore(layout).save(spec)
    plan = WorkspacePlanner().plan(spec, bootstrap_project(project_root))
    WorkspaceMaterializer().materialize(plan)
    (plan.workspace_path / 'feature.txt').write_text('worktree-only\n', encoding='utf-8')
    subprocess.run(['git', '-C', str(plan.workspace_path), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(plan.workspace_path), 'commit', '-m', 'worktree'], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    WorkspaceBindingStore().save(plan)

    command = ParsedStartCommand(project=None, agent_names=(), restore=True, auto_permission=True)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)
    seen: dict[str, object] = {}

    class _FakeClient:
        def start(self, **kwargs):
            seen['called'] = kwargs
            return {}

    monkeypatch.setattr(
        'cli.services.start.ensure_daemon_started',
        lambda context: SimpleNamespace(client=_FakeClient(), started=False),
    )

    with pytest.raises(RuntimeError, match='worktree state'):
        start_agents(context, command)

    assert seen == {}
    assert plan.workspace_path.exists() is True


def test_start_agents_reports_active_unmerged_worktree_warning(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo-start-warn-worktree'
    _init_git_repo(project_root)
    (project_root / '.ccb').mkdir(parents=True, exist_ok=True)
    (project_root / '.ccb' / 'ccb.config').write_text('agent1:codex(worktree)\n', encoding='utf-8')
    bootstrap_project(project_root)

    spec = _worktree_spec('agent1')
    layout = PathLayout(project_root)
    AgentSpecStore(layout).save(spec)
    plan = WorkspacePlanner().plan(spec, bootstrap_project(project_root))
    WorkspaceMaterializer().materialize(plan)
    (plan.workspace_path / 'feature.txt').write_text('worktree-only\n', encoding='utf-8')
    subprocess.run(['git', '-C', str(plan.workspace_path), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(plan.workspace_path), 'commit', '-m', 'worktree'], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

    command = ParsedStartCommand(project=None, agent_names=(), restore=True, auto_permission=True)
    context = CliContextBuilder().build(command, cwd=project_root, bootstrap_if_missing=False)

    class _FakeClient:
        def start(self, **kwargs):
            del kwargs
            return {
                'project_root': str(project_root),
                'project_id': context.project.project_id,
                'started': ['agent1'],
                'socket_path': str(context.paths.ccbd_socket_path),
                'cleanup_summaries': [],
            }

    monkeypatch.setattr(
        'cli.services.start.ensure_daemon_started',
        lambda context: SimpleNamespace(client=_FakeClient(), started=False),
    )

    summary = start_agents(context, command)

    assert len(summary.worktree_warnings) == 1
    assert summary.worktree_warnings[0].agent_name == 'agent1'
    assert summary.worktree_warnings[0].merged is False
