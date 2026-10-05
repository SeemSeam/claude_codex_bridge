from __future__ import annotations

from pathlib import Path

from ccbd.api_models import JobRecord
from completion.models import CompletionDecision
from provider_core.protocol import request_anchor_for_job
from provider_execution.base import (
    ProviderPollResult,
    ProviderRuntimeContext,
    ProviderSubmission,
)
from provider_execution.common import request_anchor_from_runtime_state
from provider_execution.followups import unsupported_active_followup_capability
from provider_execution.reliability import CompletionReliabilityPolicy
from terminal_runtime import get_backend_for_session

from .comm import ClaudeLogReader
from .execution_runtime import poll_submission as _poll_submission
from .execution_runtime import resume_submission as _resume_submission
from .execution_runtime import start_active_submission as _start_active_submission
from .execution_runtime.hook_results import capture_exact_hook_cancel_evidence
from .session import load_project_session


class ClaudeProviderAdapter:
    provider = 'claude'
    restart_resume_supported = True
    completion_reliability_policy = CompletionReliabilityPolicy(
        provider='claude',
        primary_authority='hook_artifact_or_session_event_log',
        no_terminal_timeout_s=0.0,
    )

    def start(self, job: JobRecord, *, context: ProviderRuntimeContext | None, now: str) -> ProviderSubmission:
        return _start_active_submission(
            self,
            job,
            context=context,
            now=now,
            load_session_fn=_load_session,
            backend_for_session_fn=get_backend_for_session,
            reader_factory=_reader_factory,
            request_anchor_fn=request_anchor_for_job,
        )

    def poll(self, submission: ProviderSubmission, *, now: str) -> ProviderPollResult | None:
        return _poll_submission(self, submission, now=now)

    def capture_cancel_evidence(
        self,
        submission: ProviderSubmission,
        *,
        now: str,
    ) -> CompletionDecision | None:
        return capture_exact_hook_cancel_evidence(submission, now=now)

    def export_runtime_state(self, submission: ProviderSubmission) -> dict[str, object]:
        return {
            'mode': submission.runtime_state.get('mode'),
            'state': submission.runtime_state.get('state') or {},
            'pane_id': submission.runtime_state.get('pane_id'),
            'request_anchor': request_anchor_from_runtime_state(submission.runtime_state, fallback=submission.job_id),
            'next_seq': submission.runtime_state.get('next_seq'),
            'anchor_seen': submission.runtime_state.get('anchor_seen'),
            'no_wrap': submission.runtime_state.get('no_wrap'),
            'reply_buffer': submission.runtime_state.get('reply_buffer'),
            'raw_buffer': submission.runtime_state.get('raw_buffer'),
            'session_path': submission.runtime_state.get('session_path'),
            'last_assistant_uuid': submission.runtime_state.get('last_assistant_uuid'),
            'active_assistant_message_id': submission.runtime_state.get('active_assistant_message_id'),
            'active_assistant_text': submission.runtime_state.get('active_assistant_text'),
            'active_assistant_stop_reason': submission.runtime_state.get('active_assistant_stop_reason'),
            'active_assistant_has_tool_use': submission.runtime_state.get('active_assistant_has_tool_use'),
            'terminal_reply': submission.runtime_state.get('terminal_reply'),
            'prompt_enqueued': submission.runtime_state.get('prompt_enqueued'),
            'queue_dequeue_observed': submission.runtime_state.get('queue_dequeue_observed'),
            'prompt_activated': submission.runtime_state.get('prompt_activated'),
            'prompt_enqueue_uuid': submission.runtime_state.get('prompt_enqueue_uuid'),
            'prompt_activation_uuid': submission.runtime_state.get('prompt_activation_uuid'),
            'completion_dir': submission.runtime_state.get('completion_dir'),
            'prompt_text': submission.runtime_state.get('prompt_text'),
            'prompt_sent': submission.runtime_state.get('prompt_sent'),
            'prompt_sent_at': submission.runtime_state.get('prompt_sent_at'),
            'activation_enter_count': submission.runtime_state.get('activation_enter_count'),
            'activation_enter_at': submission.runtime_state.get('activation_enter_at'),
            'activation_enter_evidence': submission.runtime_state.get('activation_enter_evidence'),
            'reply_delivery_complete_on_dispatch': submission.runtime_state.get('reply_delivery_complete_on_dispatch'),
            'reply_delivery_require_ready': submission.runtime_state.get('reply_delivery_require_ready'),
            'ready_wait_started_at': submission.runtime_state.get('ready_wait_started_at'),
            'ready_timeout_s': submission.runtime_state.get('ready_timeout_s'),
            'matching_paste_recovery_attempted': submission.runtime_state.get('matching_paste_recovery_attempted'),
            'matching_paste_recovery_offset': submission.runtime_state.get('matching_paste_recovery_offset'),
        }

    def resume(
        self,
        job: JobRecord,
        submission: ProviderSubmission,
        *,
        context: ProviderRuntimeContext | None,
        persisted_state,
        now: str,
    ) -> ProviderSubmission | None:
        del persisted_state, now
        if context is None or not context.workspace_path:
            return None
        return _resume_submission(
            job,
            submission,
            context=context,
            load_session_fn=_load_session,
            backend_for_session_fn=get_backend_for_session,
            reader_factory=_reader_factory,
        )

    def active_followup_capability(self, submission):
        del submission
        return unsupported_active_followup_capability(
            'claude_tui_missing_atomic_active_turn_precondition',
            mechanism='claude_queued_command_post_dispatch_only',
        )


def _reader_factory(session):
    projects_root = getattr(session, 'claude_projects_root', None)
    root = Path(projects_root).expanduser() if projects_root else None
    return ClaudeLogReader(root=root, work_dir=Path(session.work_dir))


def _load_session(work_dir: Path, *, agent_name: str):
    from .execution_runtime.start import load_session as _runtime_load_session

    return _runtime_load_session(load_project_session, work_dir, agent_name=agent_name)


def build_execution_adapter() -> ClaudeProviderAdapter:
    return ClaudeProviderAdapter()


__all__ = ['ClaudeProviderAdapter', 'build_execution_adapter']
