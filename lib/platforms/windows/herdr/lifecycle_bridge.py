from __future__ import annotations

from collections.abc import Callable, Mapping

from agents.models import AgentState


_HERDR_STATE_BY_AGENT_STATE = {
    AgentState.STARTING: "idle",
    AgentState.IDLE: "idle",
    AgentState.BUSY: "working",
    AgentState.STOPPING: "unknown",
    AgentState.STOPPED: "unknown",
    AgentState.DEGRADED: "unknown",
    AgentState.FAILED: "unknown",
}


class HerdrAgentLifecycleBridge:
    """CCB-authority bridge for Herdr panes that must report lifecycle state.

    CCB registers a pane with ``report-agent idle + seq`` at launch and then
    calls this bridge on each runtime state transition.  It maps CCB AgentState
    to Herdr states and keeps a monotonically increasing ``seq`` so stale
    reports cannot overwrite a newer state.
    """

    def __init__(
        self,
        *,
        backend_factory: Callable[[], object],
        namespace_ref_fn: Callable[[], Mapping[str, object] | None],
        seq_start: int = 0,
    ) -> None:
        self._backend_factory = backend_factory
        self._namespace_ref_fn = namespace_ref_fn
        self._seq = max(int(seq_start), 0)

    @property
    def seq(self) -> int:
        return self._seq

    def sync(
        self,
        *,
        provider: str,
        state: AgentState | str,
        pane_id: str | None,
        session_id: str | None = None,
        session_path: str | None = None,
    ) -> bool:
        herdr_state = _herdr_state(state)
        pane_id = str(pane_id or "").strip()
        if not herdr_state or not pane_id or not str(provider or "").strip():
            return False
        namespace_ref = self._namespace_ref_fn()
        if not namespace_ref:
            return False
        session_name = str(namespace_ref.get("session_name") or "").strip()
        if not session_name or str(namespace_ref.get("backend_impl") or "") != "herdr":
            return False
        backend = self._backend_factory()
        if backend is None:
            return False
        attach = getattr(backend, "attach_persisted_session", None)
        if callable(attach):
            attach(dict(namespace_ref), pane_id=pane_id)
        reporter = getattr(backend, "report_pane_agent", None)
        if not callable(reporter):
            return False
        self._seq += 1
        pane_ref = {
            "backend_impl": "herdr",
            "pane_id": pane_id,
            "session_name": session_name,
        }
        reporter(
            pane_ref,
            provider_kind=provider,
            state=herdr_state,
            seq=self._seq,
            session_id=session_id,
            session_path=session_path,
        )
        return True


def _herdr_state(state: AgentState | str) -> str | None:
    if isinstance(state, AgentState):
        return _HERDR_STATE_BY_AGENT_STATE.get(state)
    try:
        return _HERDR_STATE_BY_AGENT_STATE.get(AgentState(str(state).strip().lower()))
    except ValueError:
        return None


__all__ = ["HerdrAgentLifecycleBridge"]
