from __future__ import annotations

from ccbd.api_models import JobRecord
from provider_execution.base import (
    ProviderPollResult,
    ProviderRuntimeContext,
    ProviderSubmission,
)

from .execution_runtime import poll_submission as _poll_submission
from .execution_runtime import start_submission as _start_submission


class AgyProviderAdapter:
    provider = 'agy'

    def restore_diagnostics(self) -> dict[str, object]:
        return {
            'resume_supported': False,
            'restore_mode': 'resubmit_required',
            'restore_reason': 'provider_resume_unsupported',
            'restore_detail': 'agy adapter does not implement restart-time resume; resubmit after ccbd restart',
        }

    def start(
        self,
        job: JobRecord,
        *,
        context: ProviderRuntimeContext | None,
        now: str,
    ) -> ProviderSubmission:
        return _start_submission(job, context=context, now=now, provider=self.provider)

    def poll(
        self,
        submission: ProviderSubmission,
        *,
        now: str,
    ) -> ProviderPollResult | None:
        return _poll_submission(submission, now=now)

    def resume(
        self,
        job: JobRecord,
        submission: ProviderSubmission,
        *,
        context: ProviderRuntimeContext | None,
        persisted_state,
        now: str,
    ) -> ProviderSubmission | None:
        del job, submission, context, persisted_state, now
        return None


def build_execution_adapter() -> AgyProviderAdapter:
    return AgyProviderAdapter()


__all__ = ['AgyProviderAdapter', 'build_execution_adapter']
