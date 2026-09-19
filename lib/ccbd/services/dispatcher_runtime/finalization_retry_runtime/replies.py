from __future__ import annotations

from dataclasses import replace

from ccbd.api_models import JobStatus

from ..failure_policy import nonretryable_api_failure_kind
from .details import retry_failure_detail
from .policy import DEFAULT_RETRYABLE_RUNTIME_REASONS, TIMEOUT_INSPECTION_REASONS


def with_retry_failure_reply(
    decision,
    job,
    *,
    attempt_number: int,
    max_attempts: int,
    scheduling_error: str | None = None,
):
    detail = retry_failure_detail(decision)
    is_fault_drill = str(decision.diagnostics.get('error_type') or '').strip().lower() == 'fault_injection'
    is_runtime_failure = str(decision.reason or '').strip().lower() in DEFAULT_RETRYABLE_RUNTIME_REASONS
    if scheduling_error:
        if is_fault_drill:
            reply = (
                f"Fault injection drill forced agent '{job.agent_name}' to fail on attempt "
                f'{attempt_number}/{max_attempts} ({detail}), but automatic retry could not be scheduled '
                f"({scheduling_error}). Execute this task locally or resend it to another healthy registered agent."
            )
        elif is_runtime_failure:
            reply = (
                f"Delivery to agent '{job.agent_name}' hit a retryable runtime/pane failure on attempt "
                f'{attempt_number}/{max_attempts} ({detail}), but automatic retry could not be scheduled '
                f"({scheduling_error}). Execute this task locally or resend it to another healthy registered agent."
            )
        else:
            reply = (
                f"Delivery to agent '{job.agent_name}' hit a retryable provider/API error on attempt "
                f'{attempt_number}/{max_attempts} ({detail}), but automatic retry could not be scheduled '
                f"({scheduling_error}). Execute this task locally or resend it to another healthy registered agent."
            )
    else:
        if is_fault_drill:
            reply = (
                f"Fault injection drill forced agent '{job.agent_name}' to fail after {attempt_number} attempts "
                f'({detail}). Execute this task locally or resend it to another healthy registered agent.'
            )
        elif is_runtime_failure:
            reply = (
                f"Delivery to agent '{job.agent_name}' failed after {attempt_number} attempts because its "
                f'runtime/pane could not be recovered ({detail}). Execute this task locally or resend it '
                'to another healthy registered agent.'
            )
        else:
            reply = (
                f"Delivery to agent '{job.agent_name}' failed after {attempt_number} attempts because the "
                f'provider/API connection kept failing ({detail}). Execute this task locally or resend it '
                'to another healthy registered agent.'
            )
    diagnostics = dict(decision.diagnostics or {})
    diagnostics['auto_retry'] = {
        'attempt_number': attempt_number,
        'max_attempts': max_attempts,
        'scheduling_error': scheduling_error,
    }
    return replace(decision, reply=reply, diagnostics=diagnostics)


def with_nonretryable_api_failure_reply(decision, job):
    detail = retry_failure_detail(decision)
    kind = nonretryable_api_failure_kind(decision)
    if kind == 'authentication':
        category = 'a non-retryable authentication/login error'
        remediation = 'Fix the provider login/session before retrying locally or resending it.'
    elif kind == 'permission':
        category = 'a non-retryable permission/access error'
        remediation = 'Fix the provider account permissions before retrying locally or resending it.'
    else:
        category = 'a non-retryable quota/billing error'
        remediation = 'Fix the provider billing/quota before retrying locally or resending it.'
    diagnostics = dict(decision.diagnostics or {})
    diagnostics['auto_retry'] = {
        'nonretryable_api_failure': True,
        'classification': kind,
    }
    reply = (
        f"Delivery to agent '{job.agent_name}' failed because the provider/API reported {category} "
        f'({detail}). {remediation} You can also execute this task locally or resend it to another healthy '
        'registered agent.'
    )
    return replace(decision, reply=reply, diagnostics=diagnostics)


def should_render_timeout_inspection_reply(decision) -> bool:
    if decision.status.value != JobStatus.INCOMPLETE.value:
        return False
    reason = str(decision.reason or '').strip().lower()
    return reason in TIMEOUT_INSPECTION_REASONS


def with_timeout_inspection_reply(decision, job):
    diagnostics = dict(decision.diagnostics or {})
    diagnostics['timeout_notice'] = {
        'job_id': job.job_id,
        'agent_name': job.agent_name,
        'action': 'inspect_running_attempt',
    }
    reply = (
        f"Delivery to agent '{job.agent_name}' timed out before a confirmed terminal reply. "
        f"The task may still be running in that agent session. Inspect job '{job.job_id}' in the "
        'agent pane or queue/trace views before deciding whether to continue or retry.'
    )
    return replace(decision, reply=reply, diagnostics=diagnostics)


__all__ = [
    'should_render_timeout_inspection_reply',
    'with_nonretryable_api_failure_reply',
    'with_retry_failure_reply',
    'with_timeout_inspection_reply',
]
