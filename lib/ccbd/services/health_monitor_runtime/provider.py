from __future__ import annotations

from completion.models import CompletionConfidence, CompletionDecision, CompletionStatus

_HEALTH_TO_NO_REPLY_REASON = {
    'usage-limited': 'provider_usage_limit',
    'auth-failed': 'provider_auth_failed',
    'api-error': 'provider_api_error',
    'config-error': 'provider_config_error',
}


def provider_pane_health(monitor, runtime) -> str | None:
    assessment = monitor._assess_provider_pane(
        runtime=runtime,
        registry=monitor._registry,
        session_bindings=monitor._session_bindings,
        namespace_state_store=monitor._namespace_state_store,
    )
    if assessment is None:
        return None

    prior_health = runtime.health

    if assessment.session is None:
        updated = monitor._mark_degraded(runtime, health=assessment.health)
        _maybe_terminalize_for_provider_health(
            monitor,
            runtime.agent_name,
            prior_health,
            updated.health,
            assessment,
        )
        return updated.health

    if assessment.terminal != 'tmux':
        refreshed = monitor._rebind_runtime(runtime, assessment.session, assessment.binding)
        return refreshed.health

    if assessment.health == 'healthy':
        refreshed = monitor._rebind_runtime(runtime, assessment.session, assessment.binding)
        return refreshed.health
    updated = monitor._mark_degraded(
        runtime,
        health=assessment.health,
        session=assessment.session,
        binding=assessment.binding,
    )
    _maybe_terminalize_for_provider_health(
        monitor,
        runtime.agent_name,
        prior_health,
        updated.health,
        assessment,
    )
    return updated.health


def _maybe_terminalize_for_provider_health(
    monitor,
    agent_name: str,
    prior_health: str,
    current_health: str,
    assessment,
) -> None:
    """Terminalize an agent's active job when health flips to a provider failure.

    RULE (defensive against false positives): a job is terminalized here ONLY
    when the health flip is backed by a HIGH-CONFIDENCE pane signal
    (``assessment.pane_signal_state`` is set), never on a broad keyword match.
    ``assess_provider_pane`` already parses pane content in strict mode, so a
    non-None ``pane_signal_state`` IS the high-confidence evidence. If
    ``pane_signal_state`` is None the flip came from liveness/session state only
    and we do NOT terminalize (the job may simply be waiting on a rebinding).
    This prevents the proven prod failure where a healthy agent researching
    "usage limit"/"quota" had its job killed as provider_usage_limit.
    """
    no_reply_reason = _HEALTH_TO_NO_REPLY_REASON.get(current_health)
    if no_reply_reason is None:
        return
    if prior_health == current_health:
        return
    # Require a high-confidence pane signal. Without it the health flip is not
    # attributable to a specific provider banner and must not auto-terminate.
    if not getattr(assessment, 'pane_signal_state', None):
        return
    dispatcher = monitor._dispatcher
    if dispatcher is None:
        return
    active_job_id = dispatcher._state.active_job(agent_name)
    if active_job_id is None:
        return
    current_job = dispatcher.get(active_job_id)
    if current_job is None:
        return
    if current_job.status in dispatcher._terminal_event_by_status:
        return
    finished_at = monitor._clock()
    decision = CompletionDecision(
        terminal=True,
        status=CompletionStatus.INCOMPLETE,
        reason=no_reply_reason,
        confidence=CompletionConfidence.DEGRADED,
        reply='',
        anchor_seen=False,
        reply_started=False,
        reply_stable=False,
        provider_turn_ref=active_job_id,
        source_cursor=None,
        finished_at=finished_at,
        diagnostics={
            'no_reply_reason': no_reply_reason,
            'no_reply_detail': {
                'agent_name': agent_name,
                'prior_health': prior_health,
                'current_health': current_health,
                'pane_signal_state': assessment.pane_signal_state,
                'pane_signal_reason': assessment.pane_signal_reason,
                'retry_after': assessment.retry_after,
                'pane_tail': assessment.pane_tail,
            },
        },
    )
    dispatcher.complete(active_job_id, decision)


__all__ = ['provider_pane_health']
