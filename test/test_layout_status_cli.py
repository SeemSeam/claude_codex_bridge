from __future__ import annotations

from io import StringIO
import importlib
import json
from pathlib import Path
from types import SimpleNamespace

from cli.models import ParsedLayoutCommand
from cli.parser import CliParser
from cli.phase2 import maybe_handle_phase2
from storage.paths import PathLayout


def _write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def _write_json(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding='utf-8')


def _run_phase2(argv: list[str], *, cwd: Path) -> tuple[int, dict[str, object], str]:
    stdout = StringIO()
    stderr = StringIO()
    result = maybe_handle_phase2(argv, cwd=cwd, stdout=stdout, stderr=stderr)
    payload = json.loads(stdout.getvalue()) if stdout.getvalue().strip() else {}
    return result, payload, stderr.getvalue()


def test_layout_parser_supports_status_without_pane_count() -> None:
    assert CliParser().parse(['layout', 'status', '--json']) == ParsedLayoutCommand(
        project=None,
        action='status',
        json_output=True,
    )


def test_layout_resolve_reports_window_class_overflow_without_mutating(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-layout-resolve-overflow'
    _write(
        project_root / '.ccb' / 'ccb.config',
        """version = 2
entry_window = "main"

[windows]
main = "frontdesk:fake"
plan-orchestrate = "p1:fake, p2:fake, p3:fake, p4:fake, p5:fake, p6:fake"
""",
    )

    result, payload, stderr = _run_phase2(
        ['layout', 'resolve', 'planner2', '--window-class', 'plan-orchestrate', '--json'],
        cwd=project_root,
    )

    assert result == 0, stderr
    assert payload['layout_status'] == 'ok'
    assert payload['action'] == 'resolve'
    assert payload['agent'] == 'planner2'
    assert payload['placement_mode'] == 'window_class'
    assert payload['placement']['window_name'] == 'plan-orchestrate-2'
    assert payload['resolved_window_name'] == 'plan-orchestrate-2'
    assert payload['target_surface'] == 'window'
    assert payload['target_window_exists'] is False
    assert payload['will_create_window'] is True
    assert payload['target_window_pane_count'] == 0
    assert payload['target_window_capacity'] == 6
    assert payload['target_window_agent_names'] == ['planner2']
    assert payload['window_count'] == 2
    assert payload['pane_count'] == 7
    assert not (PathLayout(project_root).runtime_state_root / 'runtime' / 'agents' / 'planner2').exists()


def test_layout_resolve_reports_execution_node_window(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-layout-resolve-node'
    _write(
        project_root / '.ccb' / 'ccb.config',
        """version = 2
entry_window = "main"

[windows]
main = "orchestrator:fake"
""",
    )

    result, payload, stderr = _run_phase2(
        [
            'layout',
            'resolve',
            'worker1',
            '--window-class',
            'plan-orchestrate',
            '--loop-id',
            'round1',
            '--node-id',
            'node1',
            '--json',
        ],
        cwd=project_root,
    )

    assert result == 0, stderr
    assert payload['placement_mode'] == 'execution_node'
    assert payload['placement']['window_class'] == 'plan-orchestrate'
    assert payload['placement']['window_name'] == 'node-round1-node1'
    assert payload['resolved_window_name'] == 'node-round1-node1'
    assert payload['target_window_exists'] is False
    assert payload['will_create_window'] is True
    assert payload['target_window_agent_names'] == ['worker1']


def test_layout_resolve_defaults_to_entry_window_for_explicit_windows(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-layout-resolve-entry'
    _write(
        project_root / '.ccb' / 'ccb.config',
        """version = 2
entry_window = "main"

[windows]
main = "frontdesk:fake"
""",
    )

    result, payload, stderr = _run_phase2(['layout', 'resolve', 'helper', '--json'], cwd=project_root)

    assert result == 0, stderr
    assert payload['placement_mode'] == 'auto'
    assert payload['resolved_window_name'] == 'main'
    assert payload['target_surface'] == 'entry_window'
    assert payload['target_window_exists'] is True
    assert payload['will_create_window'] is False
    assert payload['target_window_agent_names'] == ['frontdesk', 'helper']


def test_layout_status_reports_effective_windows_and_dynamic_agent_overlay(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-layout-status'
    _write(
        project_root / '.ccb' / 'ccb.config',
        """version = 2
entry_window = "main"

[windows]
main = "frontdesk:codex"
plan-orchestrate = "planner:codex"
""",
    )
    layout = PathLayout(project_root)
    _write_json(
        layout.runtime_state_root / 'runtime' / 'agents' / 'helper' / 'lifecycle.json',
        {
            'schema_version': 1,
            'record_type': 'ccb_dynamic_agent_lifecycle',
            'agent_lifecycle_status': 'active',
            'agent': 'helper',
            'role': 'agentroles.worker',
            'provider': 'codex',
            'workspace_mode': 'inplace',
            'target': '.',
            'lifecycle_state': 'hidden',
            'visibility_state': 'hidden',
            'dispatch_disabled': False,
            'window_name': 'plan-orchestrate',
            'apply': {
                'apply_status': 'applied',
                'plan_class': 'add_agent',
                'stage': 'publish_transaction',
            },
            'placement': {
                'mode': 'window',
                'window_name': 'plan-orchestrate',
                'layout_policy': 'append-or-create-window',
                'pane_id': '%9',
            },
            'pane_id': '%9',
        },
    )

    result, payload, stderr = _run_phase2(['layout', 'status', '--json'], cwd=project_root)

    assert result == 0, stderr
    assert payload['layout_status'] == 'ok'
    assert payload['action'] == 'status'
    assert payload['windows_explicit'] is True
    assert payload['window_count'] == 2
    assert payload['pane_count'] == 3
    windows = {window['name']: window for window in payload['windows']}
    assert windows['main']['agent_names'] == ['frontdesk']
    assert windows['plan-orchestrate']['agent_names'] == ['planner', 'helper']
    helper = [agent for agent in windows['plan-orchestrate']['agents'] if agent['agent'] == 'helper'][0]
    assert helper['source'] == 'dynamic'
    assert helper['agent_kind'] == 'dynamic'
    assert helper['ownership_class'] == 'dynamic_session'
    assert helper['lifecycle_state'] == 'hidden'
    assert helper['dispatch_state'] == 'enabled'
    assert helper['apply_status'] == 'applied'
    assert helper['apply_plan_class'] == 'add_agent'
    assert helper['failed_apply'] is False
    assert helper['pane_identity_source'] == 'record'
    assert helper['pane_id'] == '%9'
    assert helper['runtime_state'] == 'missing'
    frontdesk = windows['main']['agents'][0]
    assert frontdesk['source'] == 'configured'
    assert frontdesk['agent_kind'] == 'static'
    assert frontdesk['ownership_class'] == 'static_configured'
    assert frontdesk['dispatch_state'] == 'enabled'
    assert frontdesk['failed_apply'] is False
    assert payload['namespace']['status'] == 'unmounted'


def test_layout_status_marks_loop_capacity_agents_as_loop_source(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-layout-status-loop'
    _write(
        project_root / '.ccb' / 'ccb.config',
        """version = 2
entry_window = "main"

[windows]
main = "orchestrator:fake"
""",
    )
    layout = PathLayout(project_root)
    _write_json(
        layout.runtime_state_root / 'runtime' / 'loops' / 'round1' / 'capacity.json',
        {
            'schema_version': 1,
            'record_type': 'ccb_loop_capacity_state',
            'loop_capacity_status': 'ensured',
            'loop_id': 'round1',
            'agents': [
                {
                    'name': 'loop-round1-worker-1',
                    'profile': 'worker',
                    'role': 'agentroles.coder',
                    'provider': 'fake',
                    'workspace_mode': 'inplace',
                    'loop_id': 'round1',
                    'node_id': 'node1',
                    'window_name': 'node-round1-node1',
                    'placement': {
                        'mode': 'execution_node',
                        'loop_id': 'round1',
                        'node_id': 'node1',
                        'window_name': 'node-round1-node1',
                    },
                    'state': 'planned',
                },
                {
                    'name': 'loop-round1-code_reviewer-1',
                    'profile': 'code_reviewer',
                    'role': 'agentroles.code_reviewer',
                    'provider': 'fake',
                    'workspace_mode': 'inplace',
                    'loop_id': 'round1',
                    'node_id': 'node1',
                    'window_name': 'node-round1-node1',
                    'placement': {
                        'mode': 'execution_node',
                        'loop_id': 'round1',
                        'node_id': 'node1',
                        'window_name': 'node-round1-node1',
                    },
                    'state': 'planned',
                },
            ],
        },
    )

    result, payload, stderr = _run_phase2(['layout', 'status', '--json'], cwd=project_root)

    assert result == 0, stderr
    assert payload['loop_agent_count'] == 2
    windows = {window['name']: window for window in payload['windows']}
    node_agents = windows['node-round1-node1']['agents']
    assert [(agent['agent'], agent['source'], agent['loop_id'], agent['node_id']) for agent in node_agents] == [
        ('loop-round1-worker-1', 'loop', 'round1', 'node1'),
        ('loop-round1-code_reviewer-1', 'loop', 'round1', 'node1'),
    ]
    assert node_agents[0]['agent_kind'] == 'loop'
    assert node_agents[0]['ownership_class'] == 'loop_capacity'
    assert node_agents[0]['dispatch_state'] == 'enabled'
    assert node_agents[0]['profile'] == 'worker'
    assert node_agents[1]['profile'] == 'code_reviewer'


def test_layout_status_reports_parked_and_failed_apply_diagnostics(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-layout-status-parked'
    _write(
        project_root / '.ccb' / 'ccb.config',
        """version = 2
entry_window = "main"

[windows]
main = "main:fake"
""",
    )
    layout = PathLayout(project_root)
    _write_json(
        layout.runtime_state_root / 'runtime' / 'agents' / 'parked_helper' / 'lifecycle.json',
        {
            'schema_version': 1,
            'record_type': 'ccb_dynamic_agent_lifecycle',
            'agent_lifecycle_status': 'active',
            'agent': 'parked_helper',
            'role': 'agentroles.planner',
            'provider': 'fake',
            'workspace_mode': 'inplace',
            'target': '.',
            'lifecycle_state': 'parked',
            'visibility_state': 'hidden',
            'dispatch_disabled': True,
            'window_name': 'main',
            'placement': {
                'mode': 'window',
                'window_name': 'main',
                'pane_id': '%5',
            },
            'apply': {
                'apply_status': 'failed',
                'plan_class': 'view_only_change',
                'stage': 'namespace_patch',
                'namespace_patch_status': 'failed',
            },
            'pane_id': '%5',
        },
    )

    result, payload, stderr = _run_phase2(['layout', 'status', '--json'], cwd=project_root)

    assert result == 0, stderr
    windows = {window['name']: window for window in payload['windows']}
    helper = [agent for agent in windows['main']['agents'] if agent['agent'] == 'parked_helper'][0]
    assert helper['source'] == 'dynamic'
    assert helper['agent_kind'] == 'dynamic'
    assert helper['ownership_class'] == 'dynamic_session'
    assert helper['lifecycle_state'] == 'parked'
    assert helper['dispatch_state'] == 'disabled'
    assert helper['dispatch_disabled'] is True
    assert helper['apply_status'] == 'failed'
    assert helper['apply_plan_class'] == 'view_only_change'
    assert helper['apply_stage'] == 'namespace_patch'
    assert helper['failed_apply'] is True
    assert helper['pane_identity_source'] == 'record'


def test_layout_status_skips_tmux_observation_for_unmounted_namespace_state(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo-layout-status-unmounted'
    _write(
        project_root / '.ccb' / 'ccb.config',
        """version = 2
entry_window = "main"

[windows]
main = "main:fake"
""",
    )
    layout = PathLayout(project_root)
    _write_json(
        layout.ccbd_state_path,
        {
            'schema_version': 2,
            'record_type': 'ccbd_project_namespace_state',
            'project_id': layout.project_id,
            'namespace_epoch': 1,
            'tmux_socket_path': str(layout.ccbd_tmux_socket_path),
            'tmux_session_name': layout.ccbd_tmux_session_name,
            'layout_version': 3,
            'layout_signature': 'stale-signature',
            'workspace_window_name': 'main',
            'workspace_window_id': '@0',
            'workspace_epoch': 1,
            'ui_attachable': False,
        },
    )

    result, payload, stderr = _run_phase2(['layout', 'status', '--json'], cwd=project_root)

    assert result == 0, stderr
    assert payload['namespace']['state_load_status'] == 'ok'
    assert payload['namespace']['status'] == 'unmounted'
    assert payload['observed'] == {
        'observe_status': 'skipped',
        'reason': 'namespace_unmounted',
    }


def test_layout_status_observes_tmux_pane_geometry(
    tmp_path: Path,
    monkeypatch,
) -> None:
    layout_status_service = importlib.import_module('cli.services.layout_status')

    project_root = tmp_path / 'repo-layout-status-observed'
    _write(
        project_root / '.ccb' / 'ccb.config',
        """version = 2
entry_window = "main"

[windows]
main = "main:fake, helper:fake"
""",
    )
    layout = PathLayout(project_root)
    _write_json(
        layout.ccbd_state_path,
        {
            'schema_version': 2,
            'record_type': 'ccbd_project_namespace_state',
            'project_id': layout.project_id,
            'namespace_epoch': 1,
            'tmux_socket_path': str(layout.ccbd_tmux_socket_path),
            'tmux_session_name': layout.ccbd_tmux_session_name,
            'layout_version': 3,
            'layout_signature': 'signature',
            'workspace_window_name': 'main',
            'workspace_window_id': '@1',
            'workspace_epoch': 1,
            'ui_attachable': True,
        },
    )

    class FakeTmuxBackend:
        def __init__(self, *, socket_path: str):
            self.socket_path = socket_path

        def _tmux_run(self, args, **_kwargs):
            assert args[:4] == ['list-panes', '-a', '-t', layout.ccbd_tmux_session_name]
            assert 'pane_index' in args[-1]
            assert 'pane_left' in args[-1]
            assert 'pane_top' in args[-1]
            assert 'pane_width' in args[-1]
            assert 'pane_height' in args[-1]
            return SimpleNamespace(
                stdout=(
                    f"main\t@1\t%1\tmain\tmain\tmain\tmain\t{layout.project_id}\tccb\t1\t0\t0\t0\t0\t100\t30\n"
                    f"main\t@1\t%2\thelper\thelper\thelper\tmain\t{layout.project_id}\tccb\t0\t0\t1\t101\t0\t80\t30\n"
                )
            )

    monkeypatch.setattr(layout_status_service, 'make_terminal_backend', FakeTmuxBackend)
    monkeypatch.setattr(layout_status_service.shutil, 'which', lambda _name: '/usr/bin/tmux')
    monkeypatch.setattr(
        layout_status_service,
        'ping_local_state',
        lambda _context: SimpleNamespace(
            mount_state='mounted',
            socket_connectable=True,
            tmux_socket_path=str(layout.ccbd_tmux_socket_path),
        ),
    )

    result, payload, stderr = _run_phase2(['layout', 'status', '--json'], cwd=project_root)

    assert result == 0, stderr
    assert payload['observed']['observe_status'] == 'ok'
    windows = {window['name']: window for window in payload['windows']}
    observed_panes = windows['main']['observed']['panes']
    assert [(pane['pane_id'], pane['pane_index'], pane['pane_left'], pane['pane_top'], pane['pane_width'], pane['pane_height']) for pane in observed_panes] == [
        ('%1', 0, 0, 0, 100, 30),
        ('%2', 1, 101, 0, 80, 30),
    ]
    agents = {agent['agent']: agent for agent in windows['main']['agents']}
    assert agents['main']['observed']['pane_index'] == 0
    assert agents['helper']['observed']['pane_width'] == 80
