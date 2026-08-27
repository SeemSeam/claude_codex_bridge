from __future__ import annotations

from collections.abc import Callable, Mapping

from agents.models import AgentState


class TerminalAgentLifecycleSink:
    """Forward runtime state changes to an optional terminal backend capability."""

    def __init__(
        self,
        *,
        backend_factory: Callable[[], object],
        namespace_ref_fn: Callable[[], Mapping[str, object] | None],
        seq_start: int = 0,
    ) -> None:
        self._backend_factory = backend_factory
        self._namespace_ref_fn = namespace_ref_fn
        self._seq_start = max(int(seq_start), 0)
        self._seq_by_pane: dict[str, int] = {}

    def sync(
        self,
        *,
        provider: str,
        state: AgentState,
        pane_id: str | None,
        session_id: str | None = None,
        session_path: str | None = None,
    ) -> bool:
        pane_id = str(pane_id or '').strip()
        provider = str(provider or '').strip()
        if not pane_id or not provider:
            return False
        namespace_ref = self._namespace_ref_fn()
        if not isinstance(namespace_ref, Mapping):
            return False
        backend = self._backend_factory()
        syncer = getattr(backend, 'sync_pane_agent_state', None)
        if not callable(syncer):
            return False
        seq = self._seq_by_pane.get(pane_id, self._seq_start) + 1
        synced = syncer(
            namespace_ref=dict(namespace_ref),
            pane_id=pane_id,
            provider_kind=provider,
            runtime_state=state.value,
            seq=seq,
            session_id=session_id,
            session_path=session_path,
        )
        if synced is False:
            return False
        self._seq_by_pane[pane_id] = seq
        return True


__all__ = ['TerminalAgentLifecycleSink']
