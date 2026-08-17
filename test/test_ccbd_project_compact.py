from __future__ import annotations

from types import SimpleNamespace

import pytest

from ccbd.handlers.project_compact import build_project_compact_context_handler
from ccbd.handlers.project_context import COMPACT_COMMANDS


class _Registry:
    def __init__(self, runtimes: dict[str, object]) -> None:
        self._runtimes = runtimes

    def get(self, agent_name: str):
        return self._runtimes.get(agent_name)


class _Backend:
    def __init__(self, *, existing_panes: set[str] | None = None) -> None:
        self.existing_panes = existing_panes or {'%1', '%2'}
        self.calls: list[tuple[str, ...]] = []

    def pane_exists(self, pane_id: str) -> bool:
        return pane_id in self.existing_panes

    def _ensure_not_in_copy_mode(self, pane_id: str) -> None:
        self.calls.append(('copy-mode-quit', pane_id))

    def _tmux_run(self, args, *, check=False, capture=False):
        del check, capture
        self.calls.append(tuple(args))


def _app(*, agents: dict[str, object], runtimes: dict[str, object], dispatcher=None):
    return SimpleNamespace(
        config=SimpleNamespace(agents=agents),
        registry=_Registry(runtimes),
        dispatcher=dispatcher,
        project_namespace=SimpleNamespace(load=lambda: SimpleNamespace(tmux_socket_path='/tmp/tmux.sock')),
    )


def test_compact_targets_all_and_uses_provider_native_commands(monkeypatch) -> None:
    backend = _Backend()
    monkeypatch.setattr('ccbd.handlers.project_compact.TmuxBackend', lambda *, socket_path: backend)
    app = _app(
        agents={
            'agent1': SimpleNamespace(provider='codex'),
            'agent2': SimpleNamespace(provider='gemini'),
        },
        runtimes={
            'agent1': SimpleNamespace(active_pane_id='%1'),
            'agent2': SimpleNamespace(active_pane_id='%2'),
        },
    )

    result = build_project_compact_context_handler(app)({})

    assert result['status'] == 'ok'
    assert result['results'] == [
        {'agent': 'agent1', 'status': 'compacted', 'provider': 'codex', 'pane_id': '%1', 'command': '/compact'},
        {'agent': 'agent2', 'status': 'compacted', 'provider': 'gemini', 'pane_id': '%2', 'command': '/compress'},
    ]
    assert backend.calls == [
        ('copy-mode-quit', '%1'),
        ('send-keys', '-t', '%1', 'C-u'),
        ('send-keys', '-t', '%1', '-l', '/compact'),
        ('send-keys', '-t', '%1', 'Enter'),
        ('copy-mode-quit', '%2'),
        ('send-keys', '-t', '%2', 'C-u'),
        ('send-keys', '-t', '%2', '-l', '/compress'),
        ('send-keys', '-t', '%2', 'Enter'),
    ]


def test_compact_named_targets_are_deduplicated(monkeypatch) -> None:
    backend = _Backend()
    monkeypatch.setattr('ccbd.handlers.project_compact.TmuxBackend', lambda *, socket_path: backend)
    app = _app(
        agents={'agent1': SimpleNamespace(provider='codex'), 'agent2': SimpleNamespace(provider='claude')},
        runtimes={'agent1': SimpleNamespace(active_pane_id='%1'), 'agent2': SimpleNamespace(active_pane_id='%2')},
    )

    result = build_project_compact_context_handler(app)({'agent_names': ['agent2', 'agent2']})

    assert result['agent_names'] == ['agent2']
    assert len(result['results']) == 1
    assert result['results'][0]['agent'] == 'agent2'


def test_compact_rejects_all_with_named_agents() -> None:
    app = _app(agents={'agent1': SimpleNamespace(provider='codex')}, runtimes={})

    with pytest.raises(ValueError, match='all.*cannot be combined'):
        build_project_compact_context_handler(app)({'agent_names': ['all', 'agent1']})


def test_compact_blocks_busy_agents_before_pane_input(monkeypatch) -> None:
    backend = _Backend()
    monkeypatch.setattr('ccbd.handlers.project_compact.TmuxBackend', lambda *, socket_path: backend)
    dispatcher = SimpleNamespace(
        _has_outstanding_work=lambda agent_name: agent_name == 'agent1',
        _state=SimpleNamespace(active_job=lambda name: 'job-1', queue_depth=lambda name: 1),
    )
    app = _app(
        agents={'agent1': SimpleNamespace(provider='codex')},
        runtimes={'agent1': SimpleNamespace(active_pane_id='%1')},
        dispatcher=dispatcher,
    )

    result = build_project_compact_context_handler(app)({})

    assert result['status'] == 'blocked'
    assert result['results'][0]['reason'] == 'agent_has_outstanding_work'
    assert backend.calls == []


def test_compact_reports_unverified_provider_without_input(monkeypatch) -> None:
    backend = _Backend()
    monkeypatch.setattr('ccbd.handlers.project_compact.TmuxBackend', lambda *, socket_path: backend)
    app = _app(
        agents={'agent1': SimpleNamespace(provider='zai')},
        runtimes={'agent1': SimpleNamespace(active_pane_id='%1')},
    )

    result = build_project_compact_context_handler(app)({})

    assert result['status'] == 'unsupported'
    assert result['results'][0]['reason'] == 'provider_native_compact_unverified'
    assert backend.calls == []


def test_compact_opencode_waits_before_submit(monkeypatch) -> None:
    backend = _Backend()
    monkeypatch.setattr('ccbd.handlers.project_compact.TmuxBackend', lambda *, socket_path: backend)
    monkeypatch.setattr('ccbd.handlers.project_context.time.sleep', lambda seconds: backend.calls.append(('sleep', str(seconds))))
    app = _app(
        agents={'agent1': SimpleNamespace(provider='opencode')},
        runtimes={'agent1': SimpleNamespace(active_pane_id='%1')},
    )

    result = build_project_compact_context_handler(app)({})

    assert result['results'][0]['status'] == 'compacted'
    assert ('sleep', '0.3') in backend.calls


def test_compact_dsh_uses_structured_api_without_pane_input(monkeypatch, tmp_path) -> None:
    backend = _Backend()
    monkeypatch.setattr(
        'ccbd.handlers.project_compact.TmuxBackend',
        lambda *, socket_path: (_ for _ in ()).throw(
            AssertionError(f'DSH compact must not open tmux backend {socket_path}')
        ),
    )
    session_file = tmp_path / '.dsh-session'
    calls = []
    monkeypatch.setattr(
        'provider_backends.dsh.control.compact_dsh_session',
        lambda path: calls.append(path) or {
            'session_id': 'session-1',
            'command': '/compact',
            'detail': 'Compacted.',
        },
    )
    app = _app(
        agents={'dsh1': SimpleNamespace(provider='dsh')},
        runtimes={'dsh1': SimpleNamespace(session_file=str(session_file))},
    )
    app.project_namespace = SimpleNamespace(load=lambda: None)

    result = build_project_compact_context_handler(app)({})

    assert result['status'] == 'ok'
    assert result['results'] == [
        {
            'agent': 'dsh1',
            'status': 'compacted',
            'provider': 'dsh',
            'command': '/compact',
            'detail': 'Compacted.',
        }
    ]
    assert calls == [session_file]
    assert backend.calls == []


def test_compact_capability_table_covers_all_builtin_provider_keys() -> None:
    expected = {
        'codex', 'claude', 'gemini', 'opencode', 'droid', 'agy', 'kimi', 'deepseek',
        'dsh', 'mimo', 'qwen', 'qoder', 'qoderclicn', 'cursor', 'copilot', 'crush', 'grok',
        'kiro', 'pi', 'omp', 'zai',
    }
    assert set(COMPACT_COMMANDS) == expected
    assert {provider: command for provider, command in COMPACT_COMMANDS.items() if command} == {
        'codex': '/compact',
        'claude': '/compact',
        'gemini': '/compress',
        'opencode': '/compact',
        'droid': '/compress',
        'agy': '/compress',
        'kimi': '/compact',
        'mimo': '/compact',
        'qwen': '/compress',
        'copilot': '/compact',
        'crush': '/summarize',
        'pi': '/compact',
        'omp': '/compact',
        'dsh': '/compact',
    }
