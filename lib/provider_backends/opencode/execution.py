from __future__ import annotations

from pathlib import Path

from ccbd.api_models import JobRecord
from provider_core.protocol import request_anchor_for_job
from provider_execution.base import ProviderPollResult, ProviderRuntimeContext, ProviderSubmission
from terminal_runtime import get_backend_for_session

from .comm import OpenCodeLogReader
from .execution_runtime import poll_submission as _poll_submission_impl
from .execution_runtime import start_submission as _start_submission_impl
from .execution_runtime.helpers import load_session as _load_session_impl
from .execution_runtime.helpers import state_session_path as _state_session_path_impl
from .protocol import wrap_opencode_prompt
from .session import load_project_session


class OpenCodeProviderAdapter:
    provider = "opencode"

    def restore_diagnostics(self) -> dict[str, object]:
        return {
            "resume_supported": False,
            "restore_mode": "resubmit_required",
            "restore_reason": "provider_resume_unsupported",
            "restore_detail": "opencode live polling works, but restart-time execution resume is not implemented yet",
        }

    def start(self, job: JobRecord, *, context: ProviderRuntimeContext | None, now: str) -> ProviderSubmission:
        return _start_submission_impl(
            job,
            context=context,
            now=now,
            provider=self.provider,
            load_session_fn=_load_session,
            backend_for_session_fn=get_backend_for_session,
            reader_cls=OpenCodeLogReader,
            request_anchor_fn=request_anchor_for_job,
            wrap_prompt_fn=wrap_opencode_prompt,
        )

    def cancel(self, submission: ProviderSubmission) -> None:
        if submission.runtime_state.get('native_v2'):
            from .execution_runtime.native_v2 import cancel
            cancel(submission)

    def poll(self, submission: ProviderSubmission, *, now: str) -> ProviderPollResult | None:
        return _poll_submission_impl(
            submission,
            now=now,
            state_session_path_fn=_state_session_path,
        )


def _load_session(work_dir: Path, *, agent_name: str):
    return _load_session_impl(work_dir, agent_name=agent_name, primary_agent="opencode", load_project_session_fn=load_project_session)


def _state_session_path(state: dict[str, object]) -> str:
    return _state_session_path_impl(state)


def build_execution_adapter() -> OpenCodeProviderAdapter:
    return OpenCodeProviderAdapter()


__all__ = ["OpenCodeProviderAdapter", "build_execution_adapter"]
