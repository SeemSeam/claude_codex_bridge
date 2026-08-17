from __future__ import annotations

from dataclasses import dataclass
import math
from pathlib import Path
from types import SimpleNamespace

import pytest

from agents.models import RuntimeMode
import cli.services.runtime_launch as runtime_launch
from cli.services.runtime_launch import RuntimeLaunchResult
import cli.services.runtime_launch_runtime.ensure as ensure_runtime
import cli.services.runtime_launch_runtime.tmux_runtime as tmux_runtime
from terminal_runtime.mux_backend_contract import make_pane_ref


class _Clock:
    def __init__(self) -> None:
        self.now_ns = 0

    def monotonic_ns(self) -> int:
        return self.now_ns

    def advance_ms(self, value: int) -> None:
        self.now_ns += value * 1_000_000


def _launch_with_clock(
    tmp_path: Path,
    monkeypatch,
    *,
    fail_post_launch: bool = False,
) -> dict[str, float]:
    clock = _Clock()
    monkeypatch.setattr(tmux_runtime, 'monotonic_ns', clock.monotonic_ns)

    class Backend:
        _socket_name = 'ccb-test'
        _socket_path = '/tmp/ccb-test.sock'

        def respawn_pane(self, pane_id, *, cmd, cwd, remain_on_exit):
            del pane_id, cmd, cwd, remain_on_exit
            clock.advance_ms(11)

    def prepare_runtime(runtime_dir):
        del runtime_dir
        clock.advance_ms(2)
        return {'prepared': True}

    def resolve_run_cwd(command, spec, plan, runtime_dir, launch_session_id):
        del command, spec, runtime_dir, launch_session_id
        clock.advance_ms(3)
        return plan.workspace_path

    def prepare_launch_context(context, spec, plan, runtime_dir, prepared_state):
        del context, spec, plan, runtime_dir
        clock.advance_ms(5)
        return prepared_state

    def build_start_cmd(command, spec, runtime_dir, launch_session_id, *, prepared_state):
        del command, spec, runtime_dir, launch_session_id, prepared_state
        clock.advance_ms(7)
        return 'provider start'

    def build_session_payload(**kwargs):
        del kwargs
        clock.advance_ms(17)
        return {'provider': 'codex'}

    def post_launch(*args):
        del args
        clock.advance_ms(23)
        if fail_post_launch:
            raise RuntimeError('post launch failed')

    launcher = SimpleNamespace(
        prepare_runtime=prepare_runtime,
        resolve_run_cwd=resolve_run_cwd,
        prepare_launch_context=prepare_launch_context,
        build_start_cmd=build_start_cmd,
        build_session_payload=build_session_payload,
        post_launch=post_launch,
    )
    context = SimpleNamespace(
        paths=SimpleNamespace(agent_dir=lambda name: tmp_path / '.ccb' / 'agents' / name),
        project=SimpleNamespace(project_id='project-test'),
    )
    spec = SimpleNamespace(name='demo', provider='codex')
    plan = SimpleNamespace(workspace_path=tmp_path / 'workspace')

    def apply_identity(*args, **kwargs):
        del args, kwargs
        clock.advance_ms(13)

    def write_session_file(**kwargs):
        del kwargs
        clock.advance_ms(19)

    monkeypatch.setattr(tmux_runtime, 'apply_ccb_pane_identity', apply_identity)
    call = lambda: tmux_runtime.launch_runtime(
        context,
        object(),
        spec,
        plan,
        launcher,
        backend_factory=lambda **kwargs: Backend(),
        pane_title_marker_fn=lambda context, spec: 'CCB-demo',
        launch_session_id_fn=lambda agent_name: 'ccb-demo-session',
        create_detached_tmux_pane_fn=lambda *args, **kwargs: pytest.fail('unexpected detached pane'),
        pane_meets_minimum_size_fn=lambda *args, **kwargs: True,
        best_effort_kill_tmux_pane_fn=lambda *args, **kwargs: None,
        write_session_file_fn=write_session_file,
        assigned_pane_id='%7',
        tmux_socket_path='/tmp/ccb-test.sock',
        allow_detached_fallback=False,
    )
    if fail_post_launch:
        with pytest.raises(RuntimeError, match='post launch failed') as captured:
            call()
        return getattr(captured.value, 'ccb_startup_timings_ms')
    return call()


def test_launch_tmux_runtime_records_additive_real_boundaries(tmp_path: Path, monkeypatch) -> None:
    timings = _launch_with_clock(tmp_path, monkeypatch)

    assert timings == {
        'prepare_launch_context': 10.0,
        'build_start_cmd': 7.0,
        'tmux_respawn': 11.0,
        'pane_identity': 13.0,
        'pane_agent_report': 0.0,
        'session_write': 36.0,
        'provider_post_launch': 23.0,
        'unattributed': 0.0,
    }
    assert sum(timings.values()) == 100.0


def test_launch_tmux_runtime_retains_completed_timings_on_failure(tmp_path: Path, monkeypatch) -> None:
    timings = _launch_with_clock(tmp_path, monkeypatch, fail_post_launch=True)

    assert timings['provider_post_launch'] == 23.0
    assert timings['unattributed'] == 0.0
    assert sum(timings.values()) == 100.0


def test_launch_tmux_runtime_uses_herdr_assigned_pane_ref_without_tmux_fallback(tmp_path: Path) -> None:
    calls: list[tuple[str, object]] = []
    pane_ref = make_pane_ref(
        backend_impl='herdr',
        pane_id='herdr-pane-1',
        session_name='ccb-herdr',
        window_name='main',
        agent_slug='demo',
    )

    class HerdrBackend:
        backend_impl = 'herdr'

        def respawn_pane(self, pane, *, command, cwd, env):
            calls.append(('respawn_pane', (dict(pane), list(command), cwd, dict(env))))

        def set_pane_identity(self, pane, **kwargs):
            calls.append(('set_pane_identity', (dict(pane), dict(kwargs))))

        def report_pane_agent(self, pane, **kwargs):
            calls.append(('report_pane_agent', (dict(pane), dict(kwargs))))

        def capture_pane(self, pane, *, lines):
            calls.append(('capture_pane', (dict(pane), lines)))
            return '', {'operation': 'capture_pane', 'backend_impl': 'herdr', 'pane_id': pane['pane_id'], 'status': 'ok', 'detail': None}

        def create_pane(self, *args, **kwargs):
            raise AssertionError('Herdr assigned pane launch must not create a detached tmux pane')

    def build_session_payload(**kwargs):
        calls.append(('build_session_payload', kwargs['pane_id']))
        return {'provider': 'codex'}

    def write_session_file(**kwargs):
        calls.append(('write_session_file', kwargs['pane_id']))

    launcher = SimpleNamespace(
        prepare_runtime=None,
        resolve_run_cwd=None,
        prepare_launch_context=None,
        build_start_cmd=lambda *args, **kwargs: 'provider start --flag',
        build_session_payload=build_session_payload,
        post_launch=None,
    )
    context = SimpleNamespace(
        paths=SimpleNamespace(agent_dir=lambda name: tmp_path / '.ccb' / 'agents' / name),
        project=SimpleNamespace(project_id='project-test'),
    )
    spec = SimpleNamespace(name='demo', provider='codex')
    plan = SimpleNamespace(workspace_path=tmp_path / 'workspace')

    timings = tmux_runtime.launch_runtime(
        context,
        object(),
        spec,
        plan,
        launcher,
        backend_factory=lambda **kwargs: HerdrBackend(),
        pane_title_marker_fn=lambda context, spec: 'CCB-demo',
        launch_session_id_fn=lambda agent_name: 'ccb-demo-session',
        create_detached_tmux_pane_fn=lambda *args, **kwargs: pytest.fail('unexpected detached tmux fallback'),
        pane_meets_minimum_size_fn=lambda *args, **kwargs: pytest.fail('unexpected tmux size probe'),
        best_effort_kill_tmux_pane_fn=lambda *args, **kwargs: pytest.fail('unexpected tmux kill'),
        write_session_file_fn=write_session_file,
        assigned_pane_ref=pane_ref,
        namespace_backend_impl='herdr',
        allow_detached_fallback=False,
    )

    assert timings['tmux_respawn'] >= 0
    assert calls[0][0] == 'respawn_pane'
    assert calls[0][1][0] == pane_ref
    # herdr launch command 可能被 _herdr_launch_command 包装为 ['&', sh_exe, script_path]
    # 或直接为 ['provider start --flag']（sh.exe 不可用时）
    cmd = calls[0][1][1]
    assert isinstance(cmd, list) and len(cmd) > 0
    assert calls[0][1][2] == str(plan.workspace_path)
    assert calls[0][1][3] == {}
    assert calls[1][0] == 'capture_pane'
    assert calls[2][0] == 'set_pane_identity'
    assert calls[2][1][0] == pane_ref
    assert calls[2][1][1]['project_id'] == 'project-test'
    assert calls[3][0] == 'report_pane_agent'
    assert calls[3][1][0] == pane_ref
    assert calls[3][1][1] == {
        'provider_kind': 'codex',
        'state': 'unknown',
        'session_id': 'ccb-demo-session',
    }
    assert ('build_session_payload', 'herdr-pane-1') in calls
    assert ('write_session_file', 'herdr-pane-1') in calls


def test_ensure_runtime_skips_tmux_tool_check_for_herdr_assigned_pane(monkeypatch) -> None:
    pane_ref = make_pane_ref(
        backend_impl='herdr',
        pane_id='herdr-pane-1',
        session_name='ccb-herdr',
        window_name='main',
        agent_slug='demo',
    )
    monkeypatch.setattr(ensure_runtime, '_pane_backed_launcher', lambda spec: object())
    monkeypatch.setattr(
        ensure_runtime.shutil,
        'which',
        lambda name: None if name == 'tmux' else f'/usr/bin/{name}',
    )
    launched: dict[str, object] = {}

    def launch(*args, **kwargs):
        del args
        launched.update(kwargs)
        return {}

    binding = object()
    result = ensure_runtime.ensure_agent_runtime(
        SimpleNamespace(project=SimpleNamespace(project_root='/tmp/project')),
        object(),
        SimpleNamespace(name='demo', provider='codex', runtime_mode=RuntimeMode.PANE_BACKED),
        SimpleNamespace(workspace_path='/tmp/workspace'),
        None,
        runtime_launch_result_cls=RuntimeLaunchResult,
        binding_runtime_alive_fn=lambda binding: False,
        provider_executable_fn=lambda provider: provider,
        cleanup_stale_tmux_binding_fn=lambda binding: None,
        launch_runtime_fn=launch,
        resolve_agent_binding_fn=lambda **kwargs: binding,
        assigned_pane_ref=pane_ref,
        namespace_backend_impl='herdr',
    )

    assert result.launched is True
    assert launched['assigned_pane_ref'] == pane_ref
    assert launched['namespace_backend_impl'] == 'herdr'


def test_runtime_backend_factory_binds_herdr_namespace_ref(monkeypatch) -> None:
    namespace_ref = {
        'backend_family': 'herdr-native',
        'backend_impl': 'herdr',
        'namespace_id': 'workspace-1',
        'session_name': 'ccb-herdr',
        'ipc_kind': 'herdr_socket',
        'ipc_ref': 'herdr://local',
        'restore_token': None,
    }
    backend = SimpleNamespace()
    observed: dict[str, object] = {}

    def get_backend(name: str):
        observed['name'] = name
        return backend

    monkeypatch.setattr(runtime_launch, 'get_terminal_backend', get_backend)

    factory = runtime_launch._runtime_backend_factory(
        namespace_backend_impl='herdr',
        namespace_ref=namespace_ref,
    )

    assert factory() is backend
    assert observed['name'] == 'herdr'
    assert backend._ccb_project_namespace_ref == namespace_ref


def test_ensure_runtime_adds_binding_resolve_and_supports_legacy_result(monkeypatch) -> None:
    clock = _Clock()
    monkeypatch.setattr(ensure_runtime, 'monotonic_ns', clock.monotonic_ns)
    monkeypatch.setattr(ensure_runtime, '_pane_backed_launcher', lambda spec: object())
    monkeypatch.setattr(ensure_runtime.shutil, 'which', lambda name: f'/usr/bin/{name}')

    def launch(*args, **kwargs):
        del args, kwargs
        clock.advance_ms(10)
        return {'build_start_cmd': 4.0, 'unattributed': 6.0}

    binding = object()

    def resolve(**kwargs):
        del kwargs
        clock.advance_ms(7)
        return binding

    kwargs = dict(
        runtime_launch_result_cls=RuntimeLaunchResult,
        binding_runtime_alive_fn=lambda binding: False,
        provider_executable_fn=lambda provider: provider,
        cleanup_stale_tmux_binding_fn=lambda binding: None,
        launch_runtime_fn=launch,
        resolve_agent_binding_fn=resolve,
    )
    context = SimpleNamespace(project=SimpleNamespace(project_root='/tmp/project'))
    result = ensure_runtime.ensure_agent_runtime(
        context,
        object(),
        SimpleNamespace(name='demo', provider='codex', runtime_mode=RuntimeMode.PANE_BACKED),
        SimpleNamespace(workspace_path='/tmp/workspace'),
        None,
        **kwargs,
    )

    assert result.timings_ms == {
        'build_start_cmd': 4.0,
        'unattributed': 6.0,
        'binding_resolve': 7.0,
    }
    assert all(math.isfinite(value) and value >= 0 for value in result.timings_ms.values())

    @dataclass(frozen=True)
    class LegacyResult:
        launched: bool
        binding: object | None

    kwargs['runtime_launch_result_cls'] = LegacyResult
    legacy = ensure_runtime.ensure_agent_runtime(
        context,
        object(),
        SimpleNamespace(name='demo', provider='codex', runtime_mode=RuntimeMode.PANE_BACKED),
        SimpleNamespace(workspace_path='/tmp/workspace'),
        None,
        **kwargs,
    )
    assert legacy == LegacyResult(launched=True, binding=binding)


def test_runtime_launch_result_drops_non_finite_or_negative_timings() -> None:
    result = RuntimeLaunchResult(
        launched=True,
        binding=None,
        timings_ms={
            'good': 1,
            'nan': float('nan'),
            'infinite': float('inf'),
            'negative': -1,
            'invalid': 'not-a-number',
        },
    )

    assert result.timings_ms == {'good': 1.0}
