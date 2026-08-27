from __future__ import annotations

from types import SimpleNamespace

from agents.models import AgentState
from ccbd.services.dispatcher_runtime.runtime_state import sync_runtime
from terminal_runtime.agent_lifecycle import TerminalAgentLifecycleSink


class _Backend:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def sync_pane_agent_state(self, **kwargs) -> bool:
        self.calls.append(dict(kwargs))
        return True


def test_terminal_agent_lifecycle_sink_forwards_state_with_monotonic_pane_sequence() -> None:
    backend = _Backend()
    sink = TerminalAgentLifecycleSink(
        backend_factory=lambda: backend,
        namespace_ref_fn=lambda: {
            'backend_impl': 'test',
            'session_name': 'session-1',
        },
        seq_start=1,
    )

    assert sink.sync(
        provider='codex',
        state=AgentState.BUSY,
        pane_id='%1',
        session_id='thread-1',
    ) is True
    assert sink.sync(
        provider='codex',
        state=AgentState.IDLE,
        pane_id='%1',
        session_id='thread-1',
    ) is True

    assert [call['seq'] for call in backend.calls] == [2, 3]
    assert [call['runtime_state'] for call in backend.calls] == ['busy', 'idle']
    assert all(call['pane_id'] == '%1' for call in backend.calls)


def test_terminal_agent_lifecycle_sink_is_optional() -> None:
    sink = TerminalAgentLifecycleSink(
        backend_factory=object,
        namespace_ref_fn=lambda: {'backend_impl': 'test', 'session_name': 'session-1'},
    )

    assert sink.sync(
        provider='codex',
        state=AgentState.IDLE,
        pane_id='%1',
    ) is False


def test_sync_runtime_uses_active_pane_and_keeps_sink_best_effort() -> None:
    class _Registry:
        def get(self, agent_name):
            assert agent_name == 'codex1'
            return runtime

    class _RuntimeService:
        def __init__(self) -> None:
            self.calls = []

        def patch_runtime_state(self, current, **kwargs) -> None:
            self.calls.append((current, kwargs))

    class _Sink:
        def __init__(self) -> None:
            self.calls = []

        def sync(self, **kwargs) -> None:
            self.calls.append(kwargs)
            raise RuntimeError('backend unavailable')

    runtime = SimpleNamespace(
        state=AgentState.IDLE,
        provider='codex',
        pane_id=None,
        active_pane_id='%9',
        session_id='thread-9',
        session_ref='/sessions/thread-9.jsonl',
    )
    runtime_service = _RuntimeService()
    sink = _Sink()
    dispatcher = SimpleNamespace(
        _registry=_Registry(),
        _runtime_service=runtime_service,
        _state=SimpleNamespace(
            active_job=lambda agent_name: object(),
            queue_depth=lambda agent_name: 1,
        ),
        _agent_lifecycle_sink=sink,
        _clock=lambda: '2026-08-27T00:00:00Z',
    )

    sync_runtime(dispatcher, 'codex1')

    assert runtime_service.calls[0][1]['state'] is AgentState.BUSY
    assert sink.calls == [
        {
            'provider': 'codex',
            'state': AgentState.BUSY,
            'pane_id': '%9',
            'session_id': 'thread-9',
            'session_path': '/sessions/thread-9.jsonl',
        }
    ]
