from __future__ import annotations

from completion.detectors.base import BaseCompletionDetector
from completion.models import CompletionConfidence, CompletionItem, CompletionItemKind, CompletionStatus, first_non_empty


class SessionBoundaryDetector(BaseCompletionDetector):
    def ingest(self, item: CompletionItem) -> None:
        self._require_bound()
        self._consume_common_item(item)

        if item.kind in {CompletionItemKind.ASSISTANT_CHUNK, CompletionItemKind.ASSISTANT_FINAL}:
            text = first_non_empty(item.payload, 'text', 'reply', 'last_agent_message')
            if text:
                self._record_reply(item, text)
            self._set_pending()
            return

        if item.kind is CompletionItemKind.TURN_BOUNDARY:
            reply = first_non_empty(item.payload, 'last_agent_message', 'reply', 'text') or ''
            if reply:
                self._record_reply(item, reply, stable=True)
            elif not self._state.reply_started:
                reason = self._classify_empty_boundary(item)
                self._set_terminal(
                    status=CompletionStatus.INCOMPLETE,
                    reason=reason,
                    confidence=CompletionConfidence.OBSERVED,
                    finished_at=item.timestamp,
                    reply='',
                    diagnostics=self._empty_boundary_diagnostics(item, reason=reason),
                )
                return
            self._set_terminal(
                status=CompletionStatus.COMPLETED,
                reason=first_non_empty(item.payload, 'reason', 'completion_reason') or 'turn_duration',
                confidence=CompletionConfidence.OBSERVED,
                finished_at=item.timestamp,
                reply=reply,
            )
            return

        if item.kind is CompletionItemKind.ERROR:
            self._set_terminal(
                status=CompletionStatus.FAILED,
                reason=first_non_empty(item.payload, 'reason', 'error') or 'api_error',
                confidence=CompletionConfidence.OBSERVED,
                finished_at=item.timestamp,
                diagnostics=self._terminal_diagnostics_from_item(item),
            )
            return

        if item.kind is CompletionItemKind.PANE_DEAD:
            self._set_terminal(
                status=CompletionStatus.FAILED,
                reason=first_non_empty(item.payload, 'reason') or 'pane_dead',
                confidence=CompletionConfidence.DEGRADED,
                finished_at=item.timestamp,
                diagnostics=self._terminal_diagnostics_from_item(item),
            )
            return

        self._set_pending()

    def _classify_empty_boundary(self, item: CompletionItem) -> str:
        if self._api_error_seen(item):
            return 'api_empty_after_error'
        if not self._state.anchor_seen:
            return 'delivery_late_empty'
        return 'model_empty_output'

    def _api_error_seen(self, item: CompletionItem) -> bool:
        value = item.payload.get('api_error_seen') or item.payload.get('error_seen')
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in ('true', '1', 'yes')
        return False

    def _empty_boundary_diagnostics(self, item: CompletionItem, *, reason: str) -> dict:
        diagnostics = self._terminal_diagnostics_from_item(item)
        diagnosis = self._empty_boundary_diagnosis(reason)
        diagnostics.setdefault('provider_terminal_reason', first_non_empty(item.payload, 'reason', 'completion_reason') or 'turn_duration')
        diagnostics.setdefault('empty_reply', True)
        diagnostics.setdefault('empty_reply_reason', reason)
        diagnostics.setdefault('error_type', 'empty_provider_reply')
        diagnostics.setdefault('message', diagnosis)
        diagnostics.setdefault('diagnosis', diagnosis)
        return diagnostics

    def _empty_boundary_diagnosis(self, reason: str) -> str:
        if reason == 'api_empty_after_error':
            return (
                'Provider reported an API error during the turn and then completed without assistant reply text; '
                'inspect the protocol session log and authentication/API output.'
            )
        if reason == 'delivery_late_empty':
            return (
                'Provider session boundary arrived before the request anchor was observed; '
                'the prompt may not have been delivered or the reader was bound to stale history.'
            )
        return (
            'Provider session boundary reported completion without assistant reply text; '
            'inspect the protocol session log, pane state, and authentication/API output.'
        )
