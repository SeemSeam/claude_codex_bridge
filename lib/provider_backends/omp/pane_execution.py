from __future__ import annotations

from provider_backends.pi.pane_execution import PiPaneExecutionAdapter

from .session import load_project_session

OMP_PANE_MODE = "omp_pane"
OMP_EXTENSION_READY_TIMEOUT_ENV = "CCB_OMP_EXTENSION_READY_TIMEOUT_S"
OMP_EXTENSION_READY_TIMEOUT_DEFAULT = 30.0


class OmpPaneExecutionAdapter(PiPaneExecutionAdapter):
    def __init__(self) -> None:
        super().__init__(
            provider="omp",
            pane_mode=OMP_PANE_MODE,
            session_field_prefix="omp",
            session_loader=load_project_session,
            terminal_event_type="agent_settled",
            terminal_authority="omp_extension_agent_end_final",
            extension_ready_timeout_env=OMP_EXTENSION_READY_TIMEOUT_ENV,
            extension_ready_timeout_default=OMP_EXTENSION_READY_TIMEOUT_DEFAULT,
            persist_native_session=False,
            intermediate_stop_reasons=("tool_use",),
        )


__all__ = [
    "OMP_EXTENSION_READY_TIMEOUT_DEFAULT",
    "OMP_EXTENSION_READY_TIMEOUT_ENV",
    "OMP_PANE_MODE",
    "OmpPaneExecutionAdapter",
]
