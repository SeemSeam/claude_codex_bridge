from __future__ import annotations

from agents.models_runtime.config_runtime.workflow import WORKFLOW_MODE_AGENTIC_LOOP


def agentic_loop_enabled(dispatcher) -> bool:
    """Use loaded project authority, never role names or stale loop files."""
    workflow = getattr(dispatcher._config, 'workflow', None)
    return workflow is not None and workflow.mode == WORKFLOW_MODE_AGENTIC_LOOP
