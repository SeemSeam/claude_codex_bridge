"""Finish a synchronized remote child's reply after controller process death."""

from dataclasses import replace

from ccbd.api_models import JobStatus
from completion.models import CompletionConfidence, CompletionDecision, CompletionStatus


def restore_missing_reply(dispatcher, child):
    if child is None or child.status is not JobStatus.COMPLETED:
        return None
    spec = dispatcher._config.agents.get(child.agent_name)
    terminal = child.terminal_decision or {}
    diagnostics = terminal.get('diagnostics') or {}
    if (
        not getattr(spec, 'remote_workspace', None)
        or diagnostics.get('workspace_sync', {}).get('status') != 'synced'
    ):
        return None
    # Called under the chain transition lock, only after the existing reply
    # store was checked. A crash after this append is handled by CCB's normal
    # repair path using that reply; the child is never submitted again.
    decision = CompletionDecision(
        terminal=True,
        status=CompletionStatus.COMPLETED,
        reason=terminal.get('reason') or 'completed',
        confidence=CompletionConfidence(terminal.get('confidence') or 'degraded'),
        reply=terminal.get('reply') or '',
        anchor_seen=bool(terminal.get('anchor_seen')),
        reply_started=bool(terminal.get('reply_started')),
        reply_stable=bool(terminal.get('reply_stable')),
        provider_turn_ref=terminal.get('provider_turn_ref'),
        source_cursor=None,
        finished_at=terminal.get('finished_at') or child.updated_at,
        diagnostics=diagnostics,
    )
    bureau = dispatcher._message_bureau
    bureau.record_attempt_terminal(child, decision, finished_at=decision.finished_at)
    child = replace(child, request=replace(child.request, silence_on_success=False))
    reply_id = bureau.record_reply(
        child, decision, finished_at=decision.finished_at, deliver_to_caller=False
    )
    return bureau._reply_store.get_latest(reply_id) if reply_id else None
