from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agents.models import AgentRuntime, AgentState
from ccbd.stop_flow_runtime.pid_cleanup import collect_pid_candidates
from ccbd.stop_flow_runtime.service import stop_all_project
from cli.services.kill_runtime.pid_cleanup import collect_project_authority_pid_candidates
from ccbd.stop_flow_runtime.pid_cleanup import terminate_runtime_pids
from ccbd.stop_flow_runtime.runtime_records import extra_agent_dir_names
from runtime_pid_cleanup import collect_project_process_candidates
from runtime_pid_cleanup.termination import terminate_runtime_pids as terminate_runtime_pids_impl


def test_stop_all_project_defers_namespace_destroy_until_after_response(tmp_path: Path) -> None:
    events: list[str] = []

    class FakeNamespace:
        def destroy(self, **kwargs):
            events.append(f"destroy:{kwargs['reason']}:{kwargs['force']}")
            return SimpleNamespace(destroyed=True, namespace_epoch=3)

    class FakeRegistry:
        def list_known_agents(self):
            return ()

    paths = SimpleNamespace(
        agents_dir=tmp_path / '.ccb' / 'agents',
        ccbd_socket_path=tmp_path / '.ccb' / 'ccbd' / 'ccbd.sock',
        agent_dir=lambda agent_name: tmp_path / '.ccb' / 'agents' / agent_name,
        agent_runtime_path=lambda agent_name: tmp_path / '.ccb' / 'agents' / agent_name / 'runtime.json',
        agent_helper_path=lambda agent_name: tmp_path / '.ccb' / 'agents' / agent_name / 'helper.json',
    )
    paths.agents_dir.mkdir(parents=True)

    execution = stop_all_project(
        project_root=tmp_path,
        project_id='proj-1',
        paths=paths,
        registry=FakeRegistry(),
        project_namespace=FakeNamespace(),
        clock=lambda: '2026-05-21T00:00:00Z',
        force=True,
        cleanup_project_tmux_orphans_by_socket_fn=lambda **kwargs: (),
        tmux_cleanup_history_store_cls=lambda paths: SimpleNamespace(append=lambda event: None),
    )

    assert execution.summary.state == 'unmounted'
    assert execution.actions_taken == ('destroy_namespace:deferred', 'cleanup_tmux_orphans:skipped', 'terminate_runtime_pids:0')
    assert events == []

    execution.deferred_actions[0]()

    assert events == ['destroy:stop_all:True']


def test_stop_all_project_removes_codex_app_server_authority_artifacts(tmp_path: Path) -> None:
    from storage.paths import PathLayout

    paths = PathLayout(tmp_path)
    runtime_dir = paths.agent_dir('codex') / 'provider-runtime' / 'codex'
    runtime_dir.mkdir(parents=True)
    (runtime_dir / 'app-server.pid').write_text('not-a-live-pid\n', encoding='utf-8')
    (runtime_dir / 'app-server.remote').write_text('/run/user/1000/ccb-runtime/app-server-test.sock\n', encoding='utf-8')
    runtime = AgentRuntime(
        agent_name='codex',
        state=AgentState.IDLE,
        pid=None,
        started_at='2026-07-21T00:00:00Z',
        last_seen_at='2026-07-21T00:00:00Z',
        runtime_ref=None,
        session_ref=None,
        workspace_path=str(tmp_path),
        project_id='proj-1',
        backend_type='tmux',
        queue_depth=0,
        socket_path=None,
        health='healthy',
        provider='codex',
    )

    class FakeRegistry:
        def list_known_agents(self):
            return ('codex',)

        def get(self, agent_name):
            assert agent_name == 'codex'
            return runtime

        def upsert_authority(self, updated):
            return updated

    execution = stop_all_project(
        project_root=tmp_path,
        project_id='proj-1',
        paths=paths,
        registry=FakeRegistry(),
        project_namespace=None,
        clock=lambda: '2026-07-21T00:00:00Z',
        force=False,
        cleanup_project_tmux_orphans_by_socket_fn=lambda **kwargs: (),
        tmux_cleanup_history_store_cls=lambda paths: SimpleNamespace(append=lambda event: None),
    )

    assert not (runtime_dir / 'app-server.pid').exists()
    assert not (runtime_dir / 'app-server.remote').exists()
    assert 'cleanup_codex_app_server_artifacts:2' in execution.actions_taken


def test_extra_agent_dir_names_skips_configured_names(tmp_path: Path) -> None:
    agents_dir = tmp_path / '.ccb' / 'agents'
    (agents_dir / 'agent1').mkdir(parents=True)
    (agents_dir / 'cmd').mkdir(parents=True)
    (agents_dir / 'agent5').mkdir(parents=True)
    (agents_dir / 'not-a-dir.txt').write_text('x', encoding='utf-8')

    paths = SimpleNamespace(agents_dir=agents_dir)

    assert extra_agent_dir_names(paths, ('agent1', 'cmd')) == ('agent5',)


def test_collect_pid_candidates_uses_runtime_root_and_force_fallback(tmp_path: Path) -> None:
    agent_dir = tmp_path / '.ccb' / 'agents' / 'agent1'
    provider_runtime_dir = agent_dir / 'provider-runtime' / 'codex'
    provider_runtime_dir.mkdir(parents=True)
    (provider_runtime_dir / 'fallback.pid').write_text('456\n', encoding='utf-8')

    dedicated_runtime_root = tmp_path / 'runtime-root'
    dedicated_runtime_root.mkdir()
    (dedicated_runtime_root / 'codex.pid').write_text('789\n', encoding='utf-8')

    runtime = SimpleNamespace(runtime_pid=123, pid=None, runtime_root=str(dedicated_runtime_root))
    candidates = collect_pid_candidates(agent_dir, runtime=runtime, fallback_to_agent_dir=True)

    assert candidates[123] == [agent_dir / 'runtime.json']
    assert candidates[456] == [provider_runtime_dir / 'fallback.pid']
    assert candidates[789] == [dedicated_runtime_root / 'codex.pid']


def test_collect_pid_candidates_includes_helper_manifest_leader_pid(tmp_path: Path) -> None:
    agent_dir = tmp_path / '.ccb' / 'agents' / 'agent1'
    agent_dir.mkdir(parents=True)
    (agent_dir / 'helper.json').write_text(
        (
            '{"schema_version":1,"record_type":"provider_helper_manifest","agent_name":"agent1",'
            '"runtime_generation":3,"helper_kind":"codex_bridge","leader_pid":654,"pgid":654,'
            '"started_at":"2026-04-21T00:00:00Z","owner_daemon_generation":9,"state":"running"}\n'
        ),
        encoding='utf-8',
    )

    candidates = collect_pid_candidates(agent_dir, runtime=None, fallback_to_agent_dir=False)

    assert candidates[654] == [agent_dir / 'helper.json']


def test_collect_project_process_candidates_matches_ccb_runtime_cmdline(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    ccb_root = project_root / '.ccb'
    proc_root = tmp_path / 'proc'
    for pid in ('101', '202', '303'):
        (proc_root / pid).mkdir(parents=True)

    mapping = {
        101: f'python -m provider_backends.codex.bridge --runtime-dir {ccb_root / "agents/agent1/provider-runtime/codex"}',
        202: f'tmux -S {ccb_root / "ccbd/tmux.sock"} new-session -d',
        303: 'python unrelated.py',
    }

    candidates = collect_project_process_candidates(
        project_root,
        proc_root=proc_root,
        read_proc_cmdline_fn=lambda pid: mapping.get(pid, ''),
        current_pid=999999,
    )

    assert sorted(candidates) == [101, 202]
    assert candidates[101] == [ccb_root]
    assert candidates[202] == [ccb_root]


def test_collect_project_authority_pid_candidates_includes_ccbd_and_keeper_pids(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    ccbd_dir = project_root / '.ccb' / 'ccbd'
    ccbd_dir.mkdir(parents=True)
    (ccbd_dir / 'lease.json').write_text(
        '{"record_type":"ccbd_lease","ccbd_pid":101,"keeper_pid":202}\n',
        encoding='utf-8',
    )
    (ccbd_dir / 'keeper.json').write_text(
        '{"record_type":"ccbd_keeper","keeper_pid":202}\n',
        encoding='utf-8',
    )

    candidates = collect_project_authority_pid_candidates(project_root)

    assert candidates[101] == [ccbd_dir / 'lease.json']
    assert candidates[202] == [ccbd_dir / 'lease.json', ccbd_dir / 'keeper.json']


def test_collect_project_process_candidates_does_not_include_authority_pids(tmp_path: Path) -> None:
    project_root = tmp_path / 'repo'
    ccbd_dir = project_root / '.ccb' / 'ccbd'
    proc_root = tmp_path / 'proc'
    ccbd_dir.mkdir(parents=True)
    proc_root.mkdir()
    (ccbd_dir / 'lease.json').write_text(
        '{"record_type":"ccbd_lease","ccbd_pid":101,"keeper_pid":202}\n',
        encoding='utf-8',
    )

    candidates = collect_project_process_candidates(
        project_root,
        proc_root=proc_root,
        read_proc_cmdline_fn=lambda pid: '',
        current_pid=999999,
    )

    assert candidates == {}


def test_terminate_runtime_pids_skips_broad_project_process_scan(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo'
    seen: dict[str, object] = {}

    monkeypatch.setattr(
        'ccbd.stop_flow_runtime.pid_cleanup._terminate_runtime_pids_impl',
        lambda **kwargs: seen.update(kwargs),
    )

    terminate_runtime_pids(project_root=project_root, pid_candidates={123: [project_root / 'hint.pid']})

    assert seen['collect_project_process_candidates_fn'] is None
    assert seen['pid_candidates'] == {123: [project_root / 'hint.pid']}


def test_terminate_runtime_pids_reaps_helper_group_from_manifest(tmp_path: Path, monkeypatch) -> None:
    project_root = tmp_path / 'repo'
    helper_path = project_root / '.ccb' / 'agents' / 'agent1' / 'helper.json'
    helper_path.parent.mkdir(parents=True)
    helper_path.write_text(
        (
            '{"schema_version":1,"record_type":"provider_helper_manifest","agent_name":"agent1",'
            '"runtime_generation":2,"helper_kind":"codex_bridge","leader_pid":777,"pgid":888,'
            '"started_at":"2026-04-21T00:00:00Z","owner_daemon_generation":5,"state":"running"}\n'
        ),
        encoding='utf-8',
    )
    hint_pid = project_root / 'hint.pid'
    hint_pid.write_text('777\n', encoding='utf-8')
    killed: list[tuple[int, int]] = []
    removed: list[tuple[Path, ...]] = []

    monkeypatch.setattr('provider_runtime.helper_cleanup._kill_helper_group', lambda pgid, sig: killed.append((pgid, int(sig))) or True)
    monkeypatch.setattr(
        'provider_runtime.helper_cleanup._shared_is_pid_alive',
        lambda _pid: False,
    )

    terminate_runtime_pids_impl(
        project_root=project_root,
        pid_candidates={777: [helper_path, hint_pid]},
        is_pid_alive_fn=lambda pid: False,
        pid_matches_project_fn=lambda pid, project_root, hint_paths: True,
        terminate_pid_tree_fn=lambda pid, timeout_s, is_pid_alive_fn: True,
        remove_pid_files_fn=lambda paths: removed.append(tuple(paths)),
    )

    assert killed[0][0] == 888
    assert removed == [(helper_path, hint_pid)]
    assert helper_path.exists() is False


__all__ = []
