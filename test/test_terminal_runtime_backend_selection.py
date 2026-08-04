from __future__ import annotations

import pytest

import terminal_runtime.backend_selection as backend_selection_module
from terminal_runtime.backend_selection import (
    TerminalBackendSelection,
    TerminalLayoutService,
    make_terminal_backend,
)
from terminal_runtime.tmux_backend import TmuxBackend


class _FakeBackend:
    def __init__(self, name: str) -> None:
        self.name = name


def test_backend_selection_caches_detected_backend() -> None:
    calls: list[str] = []
    selection = TerminalBackendSelection(
        detect_terminal_fn=lambda: 'tmux',
        tmux_backend_factory=lambda: calls.append('tmux') or _FakeBackend('tmux'),
    )

    first = selection.get_backend()
    second = selection.get_backend()

    assert first is second
    assert isinstance(first, _FakeBackend)
    assert first.name == 'tmux'
    assert calls == ['tmux']


def test_backend_selection_uses_session_terminal_field() -> None:
    captured: dict[str, object] = {}

    def _tmux_backend_factory(socket_name=None, socket_path=None):
        captured['socket_name'] = socket_name
        captured['socket_path'] = socket_path
        return _FakeBackend('tmux')

    selection = TerminalBackendSelection(
        detect_terminal_fn=lambda: None,
        tmux_backend_factory=_tmux_backend_factory,
    )

    tmux_backend = selection.get_backend_for_session({'terminal': 'tmux', 'tmux_socket_name': 'sock-demo'})
    assert isinstance(tmux_backend, _FakeBackend)
    assert tmux_backend.name == 'tmux'
    assert captured['socket_name'] == 'sock-demo'
    assert captured['socket_path'] is None
    selection.get_backend_for_session({'terminal': 'tmux', 'tmux_socket_path': '/tmp/ccb.sock'})
    assert captured['socket_path'] == '/tmp/ccb.sock'
    assert selection.get_pane_id_from_session({'pane_id': '%1', 'tmux_session': '%old'}) == '%1'
    assert selection.get_pane_id_from_session({'tmux_session': '%old'}) == '%old'


@pytest.mark.parametrize('terminal_type', [None, 'tmux'])
def test_make_terminal_backend_returns_tmux_backend(terminal_type) -> None:
    assert isinstance(make_terminal_backend(terminal_type), TmuxBackend)


def test_make_terminal_backend_rejects_unimplemented_herdr_backend() -> None:
    with pytest.raises(
        NotImplementedError,
        match='herdr terminal backend lands in PR #2',
    ):
        make_terminal_backend('herdr')


def test_backend_selection_rejects_unimplemented_herdr_backend() -> None:
    selection = TerminalBackendSelection(
        detect_terminal_fn=lambda: 'tmux',
        tmux_backend_factory=lambda: _FakeBackend('tmux'),
    )

    with pytest.raises(
        NotImplementedError,
        match='herdr terminal backend lands in PR #2',
    ):
        selection.get_backend('herdr')


def test_tmux_backend_satisfies_terminal_backend_abstract_contract() -> None:
    assert TmuxBackend.__abstractmethods__ == frozenset()
    assert isinstance(TmuxBackend(), TmuxBackend)


def test_terminal_layout_service_delegates_to_runtime_layout() -> None:
    backend = _FakeBackend('tmux')
    captured: dict[str, object] = {}

    def fake_create_tmux_auto_layout(providers, **kwargs):
        captured['providers'] = providers
        captured.update(kwargs)

        class _Result:
            panes = {'a1': '%root'}

        return _Result()

    original = backend_selection_module.create_tmux_auto_layout
    backend_selection_module.create_tmux_auto_layout = fake_create_tmux_auto_layout
    service = TerminalLayoutService(
        tmux_backend_factory=lambda: backend,
        detached_session_name_fn=lambda **kwargs: 'ccb-demo-1',
        os_getpid_fn=lambda: 123,
        time_fn=lambda: 5.0,
        env={'TMUX': '/tmp/tmux'},
    )
    try:
        result = service.create_auto_layout(['a1'], cwd='/tmp/demo')
    finally:
        backend_selection_module.create_tmux_auto_layout = original

    assert result.panes == {'a1': '%root'}
    assert captured['providers'] == ['a1']
    assert captured['backend'] is backend
    assert captured['detached_session_name'] == 'ccb-demo-1'
    assert captured['inside_tmux'] is True


def test_terminal_layout_service_rejects_unimplemented_herdr_backend() -> None:
    service = TerminalLayoutService(
        tmux_backend_factory=lambda: _FakeBackend('tmux'),
        detached_session_name_fn=lambda **kwargs: 'ccb-demo-1',
    )

    with pytest.raises(
        NotImplementedError,
        match='herdr terminal backend lands in PR #2',
    ):
        service.create_auto_layout([], cwd='/tmp/demo', terminal_type='herdr')
