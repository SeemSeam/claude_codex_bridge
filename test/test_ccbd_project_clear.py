from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def _load_project_clear_module():
    # Avoid the existing ccbd.handlers package import cycle in this focused unit test.
    module_path = Path(__file__).resolve().parents[1] / 'lib' / 'ccbd' / 'handlers' / 'project_clear.py'
    spec = importlib.util.spec_from_file_location('ccbd_project_clear_for_test', module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


project_clear = _load_project_clear_module()
build_project_clear_context_handler = project_clear.build_project_clear_context_handler


class _Registry:
    def __init__(self, runtimes: dict[str, object]) -> None:
        self._runtimes = runtimes

    def get(self, agent_name: str):
        return self._runtimes.get(agent_name)


class _FakeBackend:
    def __init__(self, *, socket_path: str | None = None, existing_panes: set[str] | None = None) -> None:
        self.socket_path = socket_path
        self.existing_panes = existing_panes or {'%1', '%2'}
        self.calls: list[tuple[str, ...]] = []

    def pane_exists(self, pane_id: str) -> bool:
        return pane_id in self.existing_panes

    def _ensure_not_in_copy_mode(self, pane_id: str) -> None:
        self.calls.append(('copy-mode-quit', pane_id))

    def _tmux_run(self, args, *, check=False, capture=False):
        del check, capture
        self.calls.append(tuple(args))


def _app(
    *,
    runtimes: dict[str, object],
    agents: dict[str, object] | None = None,
    dispatcher: object | None = None,
):
    app = SimpleNamespace(
        config=SimpleNamespace(agents=agents or {'agent1': object(), 'agent2': object()}),
        registry=_Registry(runtimes),
        project_namespace=SimpleNamespace(load=lambda: SimpleNamespace(tmux_socket_path='/tmp/tmux.sock')),
    )
    if dispatcher is not None:
        app.dispatcher = dispatcher
    return app


def test_project_clear_context_handler_sends_provider_clear_to_all_agent_panes(monkeypatch) -> None:
    backend = _FakeBackend()
    monkeypatch.setattr(project_clear, 'TmuxBackend', lambda *, socket_path: backend)
    handler = build_project_clear_context_handler(
        _app(
            runtimes={
                'agent1': SimpleNamespace(active_pane_id='%1', pane_id=None),
                'agent2': SimpleNamespace(active_pane_id=None, pane_id='%2'),
            }
        )
    )

    payload = handler({})

    assert payload['status'] == 'ok'
    assert payload['agent_names'] == ['agent1', 'agent2']
    assert payload['results'] == [
        {'agent': 'agent1', 'status': 'cleared', 'pane_id': '%1', 'command': '/clear'},
        {'agent': 'agent2', 'status': 'cleared', 'pane_id': '%2', 'command': '/clear'},
    ]
    assert backend.calls == [
        ('copy-mode-quit', '%1'),
        ('send-keys', '-t', '%1', 'C-u'),
        ('send-keys', '-t', '%1', '-l', '/clear'),
        ('send-keys', '-t', '%1', 'Enter'),
        ('copy-mode-quit', '%2'),
        ('send-keys', '-t', '%2', 'C-u'),
        ('send-keys', '-t', '%2', '-l', '/clear'),
        ('send-keys', '-t', '%2', 'Enter'),
    ]


def test_project_clear_context_handler_targets_requested_agents_once(monkeypatch) -> None:
    backend = _FakeBackend()
    monkeypatch.setattr(project_clear, 'TmuxBackend', lambda *, socket_path: backend)
    handler = build_project_clear_context_handler(
        _app(
            runtimes={
                'agent1': SimpleNamespace(active_pane_id='%1'),
                'agent2': SimpleNamespace(active_pane_id='%2'),
            }
        )
    )

    payload = handler({'agent_names': ['agent2', 'agent2']})

    assert payload['agent_names'] == ['agent2']
    assert payload['results'] == [
        {'agent': 'agent2', 'status': 'cleared', 'pane_id': '%2', 'command': '/clear'},
    ]
    assert [call for call in backend.calls if call[:1] == ('send-keys',)] == [
        ('send-keys', '-t', '%2', 'C-u'),
        ('send-keys', '-t', '%2', '-l', '/clear'),
        ('send-keys', '-t', '%2', 'Enter'),
    ]


def test_project_clear_context_handler_delays_opencode_submit(monkeypatch) -> None:
    backend = _FakeBackend()
    monkeypatch.setattr(project_clear, 'TmuxBackend', lambda *, socket_path: backend)
    monkeypatch.setattr(
        project_clear.time,
        'sleep',
        lambda seconds: backend.calls.append(('sleep', str(seconds))),
    )
    handler = build_project_clear_context_handler(
        _app(
            agents={
                'opener': SimpleNamespace(provider='opencode'),
                'agent1': SimpleNamespace(provider='codex'),
            },
            runtimes={
                'opener': SimpleNamespace(active_pane_id='%1'),
                'agent1': SimpleNamespace(active_pane_id='%2'),
            },
        )
    )

    payload = handler({})

    assert payload['results'] == [
        {'agent': 'opener', 'status': 'cleared', 'pane_id': '%1', 'command': '/clear'},
        {'agent': 'agent1', 'status': 'cleared', 'pane_id': '%2', 'command': '/clear'},
    ]
    assert backend.calls == [
        ('copy-mode-quit', '%1'),
        ('send-keys', '-t', '%1', 'C-u'),
        ('send-keys', '-t', '%1', '-l', '/clear'),
        ('sleep', str(project_clear.OPENCODE_CLEAR_SUBMIT_DELAY_S)),
        ('send-keys', '-t', '%1', 'Enter'),
        ('copy-mode-quit', '%2'),
        ('send-keys', '-t', '%2', 'C-u'),
        ('send-keys', '-t', '%2', '-l', '/clear'),
        ('send-keys', '-t', '%2', 'Enter'),
    ]


def test_project_clear_context_handler_skips_missing_runtime_or_pane(monkeypatch) -> None:
    backend = _FakeBackend(existing_panes={'%1'})
    monkeypatch.setattr(project_clear, 'TmuxBackend', lambda *, socket_path: backend)
    handler = build_project_clear_context_handler(
        _app(
            runtimes={
                'agent1': SimpleNamespace(active_pane_id='%9', pane_id=None),
            }
        )
    )

    payload = handler({})

    assert payload['results'] == [
        {'agent': 'agent1', 'status': 'skipped', 'reason': 'pane_missing', 'pane_id': '%9'},
        {'agent': 'agent2', 'status': 'skipped', 'reason': 'runtime_missing'},
    ]
    assert backend.calls == []


def test_project_clear_context_handler_rejects_unknown_target() -> None:
    handler = build_project_clear_context_handler(_app(runtimes={}))

    with pytest.raises(ValueError, match='unknown agent: missing'):
        handler({'agent_names': ['missing']})


def test_project_clear_context_handler_blocks_active_or_queued_agent(monkeypatch) -> None:
    backend = _FakeBackend()
    monkeypatch.setattr(project_clear, 'TmuxBackend', lambda *, socket_path: backend)
    state = SimpleNamespace(
        active_job=lambda agent_name: 'job_active' if agent_name == 'agent1' else None,
        queue_depth=lambda agent_name: 2 if agent_name == 'agent1' else 0,
    )
    dispatcher = SimpleNamespace(
        _state=state,
        _has_outstanding_work=lambda agent_name: agent_name == 'agent1',
    )
    handler = build_project_clear_context_handler(
        _app(
            runtimes={'agent1': SimpleNamespace(active_pane_id='%1')},
            dispatcher=dispatcher,
        )
    )

    payload = handler({'agent_names': ['agent1']})

    assert payload['status'] == 'blocked'
    assert payload['results'] == [
        {
            'agent': 'agent1',
            'status': 'blocked',
            'reason': 'agent_has_outstanding_work',
            'active_job_id': 'job_active',
            'queue_depth': 2,
        }
    ]
    assert backend.calls == []
