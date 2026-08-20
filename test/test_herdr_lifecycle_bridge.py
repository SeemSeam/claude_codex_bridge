from __future__ import annotations

from types import SimpleNamespace

from agents.models import AgentState
from ccbd.services.dispatcher_runtime.runtime_state import sync_runtime
from platforms.windows.herdr.lifecycle_bridge import HerdrAgentLifecycleBridge


class _Reporter:
    def __init__(self) -> None:
        self.calls: list[tuple[dict[str, object], dict[str, object]]] = []
        self.attach_calls: list[tuple[dict[str, object], str | None]] = []

    def attach_persisted_session(
        self,
        namespace: dict[str, object],
        *,
        pane_id: str | None = None,
        pane_ref: dict[str, object] | None = None,
    ) -> None:
        del pane_ref
        self.attach_calls.append((dict(namespace), pane_id))

    def report_pane_agent(
        self,
        pane: dict[str, object],
        **kwargs: object,
    ) -> None:
        self.calls.append((dict(pane), dict(kwargs)))


def test_bridge_maps_ccb_states_and_increments_seq() -> None:
    reporter = _Reporter()
    bridge = HerdrAgentLifecycleBridge(
        backend_factory=lambda: reporter,
        namespace_ref_fn=lambda: {
            "backend_impl": "herdr",
            "session_name": "ccb-demo",
        },
    )

    assert bridge.sync(
        provider="codex",
        state=AgentState.BUSY,
        pane_id="w1:p2",
        session_id="ccb-session",
    ) is True
    assert bridge.sync(
        provider="codex",
        state=AgentState.IDLE,
        pane_id="w1:p2",
        session_id="ccb-session",
    ) is True

    assert reporter.calls == [
        (
            {
                "backend_impl": "herdr",
                "pane_id": "w1:p2",
                "session_name": "ccb-demo",
            },
            {
                "provider_kind": "codex",
                "state": "working",
                "seq": 1,
                "session_id": "ccb-session",
                "session_path": None,
            },
        ),
        (
            {
                "backend_impl": "herdr",
                "pane_id": "w1:p2",
                "session_name": "ccb-demo",
            },
            {
                "provider_kind": "codex",
                "state": "idle",
                "seq": 2,
                "session_id": "ccb-session",
                "session_path": None,
            },
        ),
    ]
    assert reporter.attach_calls == [
        (
            {
                "backend_impl": "herdr",
                "session_name": "ccb-demo",
            },
            "w1:p2",
        )
    ] * 2
    assert bridge.seq == 2


def test_bridge_skips_missing_pane_or_non_herdr_namespace() -> None:
    reporter = _Reporter()
    bridge = HerdrAgentLifecycleBridge(
        backend_factory=lambda: reporter,
        namespace_ref_fn=lambda: {
            "backend_impl": "tmux",
            "session_name": "tmux-session",
        },
    )

    assert bridge.sync(provider="codex", state=AgentState.BUSY, pane_id="") is False
    assert bridge.sync(provider="codex", state=AgentState.BUSY, pane_id="w1:p2") is False
    assert reporter.calls == []


def test_sync_runtime_forwards_ccb_state_to_herdr_bridge() -> None:
    reporter = _Reporter()
    bridge = HerdrAgentLifecycleBridge(
        backend_factory=lambda: reporter,
        namespace_ref_fn=lambda: {
            "backend_impl": "herdr",
            "session_name": "ccb-demo",
        },
    )
    runtime = SimpleNamespace(
        state=AgentState.IDLE,
        provider="codex",
        pane_id="wJ:p2",
        session_id="ccb-session",
        session_ref="D:/demo/.ccb/session",
    )

    class _State:
        @staticmethod
        def queue_depth(agent_name: str) -> int:
            del agent_name
            return 0

    class _Registry:
        def __init__(self) -> None:
            self.runtime = runtime

        def get(self, agent_name: str):
            del agent_name
            return self.runtime

        def upsert(self, updated):
            self.runtime = updated

    runtime_service = SimpleNamespace(
        patch_runtime_state=lambda current, **kwargs: current,
    )
    dispatcher = SimpleNamespace(
        _registry=_Registry(),
        _runtime_service=runtime_service,
        _state=_State(),
        _clock=lambda: "2026-08-19T00:00:00Z",
        _agent_lifecycle_bridge=bridge,
    )

    sync_runtime(dispatcher, "archi", state=AgentState.BUSY)

    assert reporter.calls == [
        (
            {
                "backend_impl": "herdr",
                "pane_id": "wJ:p2",
                "session_name": "ccb-demo",
            },
            {
                "provider_kind": "codex",
                "state": "working",
                "seq": 1,
                "session_id": "ccb-session",
                "session_path": "D:/demo/.ccb/session",
            },
        )
    ]
