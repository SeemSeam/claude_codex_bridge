from __future__ import annotations

from dataclasses import replace

from agents.models import AgentState


def sync_runtime(dispatcher, agent_name: str, *, state: AgentState | None = None) -> None:
    runtime = dispatcher._registry.get(agent_name)
    if runtime is None:
        return
    next_state = state
    if next_state is None:
        if dispatcher._state.active_job(agent_name) is not None:
            next_state = AgentState.BUSY
        elif runtime.state is AgentState.BUSY:
            next_state = AgentState.IDLE
        else:
            next_state = runtime.state
    if next_state is AgentState.BUSY and runtime.state is AgentState.STOPPED:
        next_state = AgentState.IDLE
    if dispatcher._runtime_service is not None:
        dispatcher._runtime_service.patch_runtime_state(
            runtime,
            state=next_state,
            queue_depth=dispatcher._state.queue_depth(agent_name),
            last_seen_at=dispatcher._clock(),
        )
    else:
        updated = replace(
            runtime,
            state=next_state,
            queue_depth=dispatcher._state.queue_depth(agent_name),
            last_seen_at=dispatcher._clock(),
        )
        dispatcher._registry.upsert(updated)
    bridge = getattr(dispatcher, '_agent_lifecycle_bridge', None)
    if callable(getattr(bridge, 'sync', None)):
        try:
            bridge.sync(
                provider=getattr(runtime, 'provider', None),
                state=next_state,
                pane_id=getattr(runtime, 'pane_id', None),
                session_id=getattr(runtime, 'session_id', None),
                session_path=getattr(runtime, 'session_ref', None),
            )
        except Exception:
            pass


__all__ = ['sync_runtime']
