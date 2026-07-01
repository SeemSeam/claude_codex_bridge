from __future__ import annotations

from completion.detectors.anchored_session_stability import AnchoredSessionStabilityDetector
from completion.detectors.session_boundary import SessionBoundaryDetector
from completion.detectors.terminal_text_quiet import TerminalTextQuietDetector
from completion.detectors.protocol_turn import ProtocolTurnDetector
from completion.detectors.structured_result import StructuredResultDetector
from completion.models import (
    CompletionConfidence,
    CompletionCursor,
    CompletionItem,
    CompletionItemKind,
    CompletionRequestContext,
    CompletionSourceKind,
    CompletionStatus,
)


def _ctx() -> CompletionRequestContext:
    return CompletionRequestContext(
        req_id='req-1',
        agent_name='agent1',
        provider='codex',
        timeout_s=10,
    )


def _cursor(seq: int) -> CompletionCursor:
    return CompletionCursor(source_kind=CompletionSourceKind.PROTOCOL_EVENT_STREAM, event_seq=seq)


def _item(kind: CompletionItemKind, seq: int, ts: str, payload: dict | None = None) -> CompletionItem:
    return CompletionItem(
        kind=kind,
        timestamp=ts,
        cursor=_cursor(seq),
        provider='codex',
        agent_name='agent1',
        req_id='req-1',
        payload=payload or {},
    )


def test_protocol_turn_detector_waits_for_turn_boundary() -> None:
    detector = ProtocolTurnDetector()
    detector.bind(_ctx(), _cursor(0))
    detector.ingest(_item(CompletionItemKind.ANCHOR_SEEN, 1, '2026-03-18T00:00:01Z'))
    detector.ingest(_item(CompletionItemKind.ASSISTANT_CHUNK, 2, '2026-03-18T00:00:02Z', {'text': 'partial'}))
    assert detector.decision().terminal is False

    detector.ingest(
        _item(
            CompletionItemKind.TURN_BOUNDARY,
            3,
            '2026-03-18T00:00:03Z',
            {'reason': 'task_complete', 'last_agent_message': 'done'},
        )
    )
    decision = detector.decision()
    assert decision.terminal is True
    assert decision.status is CompletionStatus.COMPLETED
    assert decision.confidence is CompletionConfidence.EXACT
    assert decision.reply == 'done'


def test_protocol_turn_detector_marks_empty_task_complete_as_model_empty_output() -> None:
    detector = ProtocolTurnDetector()
    detector.bind(_ctx(), _cursor(0))
    detector.ingest(_item(CompletionItemKind.ANCHOR_SEEN, 1, '2026-03-18T00:00:01Z'))
    detector.ingest(
        _item(
            CompletionItemKind.TURN_BOUNDARY,
            2,
            '2026-03-18T00:00:02Z',
            {'reason': 'task_complete', 'turn_id': 'turn-empty'},
        )
    )

    decision = detector.decision()
    assert decision.terminal is True
    assert decision.status is CompletionStatus.INCOMPLETE
    assert decision.reason == 'model_empty_output'
    assert decision.confidence is CompletionConfidence.EXACT
    assert decision.reply == ''
    assert decision.provider_turn_ref == 'turn-empty'
    assert decision.diagnostics['provider_terminal_reason'] == 'task_complete'
    assert decision.diagnostics['empty_reply'] is True
    assert decision.diagnostics['empty_reply_reason'] == 'model_empty_output'
    assert decision.diagnostics['error_type'] == 'empty_provider_reply'
    assert 'without assistant reply text' in decision.diagnostics['diagnosis']


def test_protocol_turn_detector_marks_empty_boundary_before_anchor_as_delivery_late_empty() -> None:
    detector = ProtocolTurnDetector()
    detector.bind(_ctx(), _cursor(0))
    detector.ingest(
        _item(
            CompletionItemKind.TURN_BOUNDARY,
            1,
            '2026-03-18T00:00:01Z',
            {'reason': 'task_complete', 'turn_id': 'turn-empty'},
        )
    )

    decision = detector.decision()
    assert decision.terminal is True
    assert decision.status is CompletionStatus.INCOMPLETE
    assert decision.reason == 'delivery_late_empty'
    assert decision.diagnostics['empty_reply_reason'] == 'delivery_late_empty'
    assert 'before the request anchor was observed' in decision.diagnostics['diagnosis']


def test_protocol_turn_detector_marks_empty_boundary_after_api_error_as_api_empty_after_error() -> None:
    detector = ProtocolTurnDetector()
    detector.bind(_ctx(), _cursor(0))
    detector.ingest(_item(CompletionItemKind.ANCHOR_SEEN, 1, '2026-03-18T00:00:01Z'))
    detector.ingest(
        _item(
            CompletionItemKind.TURN_BOUNDARY,
            2,
            '2026-03-18T00:00:02Z',
            {'reason': 'task_complete', 'turn_id': 'turn-empty', 'api_error_seen': True},
        )
    )

    decision = detector.decision()
    assert decision.terminal is True
    assert decision.status is CompletionStatus.INCOMPLETE
    assert decision.reason == 'api_empty_after_error'
    assert decision.diagnostics['empty_reply_reason'] == 'api_empty_after_error'
    assert 'API error' in decision.diagnostics['diagnosis']


def test_session_boundary_detector_marks_empty_boundary_as_model_empty_output() -> None:
    detector = SessionBoundaryDetector()
    detector.bind(_ctx(), _cursor(0))
    detector.ingest(_item(CompletionItemKind.ANCHOR_SEEN, 1, '2026-03-18T00:00:01Z'))
    detector.ingest(
        _item(
            CompletionItemKind.TURN_BOUNDARY,
            2,
            '2026-03-18T00:00:02Z',
            {'reason': 'assistant_end_turn', 'turn_id': 'turn-empty'},
        )
    )

    decision = detector.decision()
    assert decision.terminal is True
    assert decision.status is CompletionStatus.INCOMPLETE
    assert decision.reason == 'model_empty_output'
    assert decision.confidence is CompletionConfidence.OBSERVED
    assert decision.reply == ''
    assert decision.provider_turn_ref == 'turn-empty'
    assert decision.diagnostics['provider_terminal_reason'] == 'assistant_end_turn'
    assert decision.diagnostics['empty_reply'] is True
    assert decision.diagnostics['empty_reply_reason'] == 'model_empty_output'
    assert decision.diagnostics['error_type'] == 'empty_provider_reply'
    assert 'without assistant reply text' in decision.diagnostics['diagnosis']


def test_protocol_turn_detector_preserves_abort_diagnostics() -> None:
    detector = ProtocolTurnDetector()
    detector.bind(_ctx(), _cursor(0))
    detector.ingest(
        _item(
            CompletionItemKind.TURN_ABORTED,
            1,
            '2026-03-18T00:00:01Z',
            {
                'reason': 'turn_aborted',
                'status': 'failed',
                'text': 'Login required. Please run codex login.',
                'error_message': 'Login required. Please run codex login.',
            },
        )
    )
    decision = detector.decision()
    assert decision.terminal is True
    assert decision.status is CompletionStatus.FAILED
    assert decision.reason == 'turn_aborted'
    assert decision.diagnostics['text'] == 'Login required. Please run codex login.'
    assert decision.diagnostics['error_message'] == 'Login required. Please run codex login.'


def test_structured_result_detector_completes_on_result() -> None:
    detector = StructuredResultDetector()
    detector.bind(_ctx(), _cursor(0))
    detector.ingest(_item(CompletionItemKind.ASSISTANT_CHUNK, 1, '2026-03-18T00:00:01Z', {'text': 'partial'}))
    detector.ingest(_item(CompletionItemKind.RESULT, 2, '2026-03-18T00:00:02Z', {'result_text': 'final'}))
    decision = detector.decision()
    assert decision.terminal is True
    assert decision.status is CompletionStatus.COMPLETED
    assert decision.reason == 'stream_result'
    assert decision.reply == 'final'


def test_terminal_text_quiet_detector_uses_done_marker() -> None:
    detector = TerminalTextQuietDetector()
    detector.bind(_ctx(), _cursor(0))
    detector.ingest(
        _item(
            CompletionItemKind.ASSISTANT_FINAL,
            1,
            '2026-03-18T00:00:01Z',
            {'text': 'final', 'done_marker': True},
        )
    )
    decision = detector.decision()
    assert decision.terminal is True
    assert decision.reason == 'terminal_done_marker'
    assert decision.confidence is CompletionConfidence.DEGRADED


def test_terminal_text_quiet_detector_falls_back_on_timeout_when_allowed() -> None:
    detector = TerminalTextQuietDetector()
    detector.bind(_ctx(), _cursor(0))
    detector.ingest(_item(CompletionItemKind.ASSISTANT_CHUNK, 1, '2026-03-18T00:00:01Z', {'text': 'reply'}))
    detector.finalize_timeout('2026-03-18T00:00:05Z', _cursor(1))
    decision = detector.decision()
    assert decision.terminal is True
    assert decision.status is CompletionStatus.COMPLETED
    assert decision.reason == 'terminal_quiet'
    assert decision.confidence is CompletionConfidence.DEGRADED


def test_anchored_session_stability_detector_times_out_without_legacy_fallback() -> None:
    detector = AnchoredSessionStabilityDetector(settle_window_s=2.0)
    detector.bind(_ctx(), _cursor(0))
    detector.ingest(
        _item(
            CompletionItemKind.SESSION_SNAPSHOT,
            1,
            '2026-03-18T00:00:01Z',
            {'message_id': 'm1', 'reply': 'hello', 'message_count': 2, 'last_updated': '1'},
        )
    )
    detector.finalize_timeout('2026-03-18T00:00:05Z', _cursor(1))
    decision = detector.decision()
    assert decision.terminal is True
    assert decision.status is CompletionStatus.INCOMPLETE
    assert decision.reason == 'timeout'


def test_terminal_text_quiet_detector_fails_on_pane_dead() -> None:
    detector = TerminalTextQuietDetector()
    detector.bind(_ctx(), _cursor(0))
    detector.ingest(_item(CompletionItemKind.PANE_DEAD, 1, '2026-03-18T00:00:01Z', {'reason': 'pane_dead'}))
    decision = detector.decision()
    assert decision.terminal is True
    assert decision.status is CompletionStatus.FAILED
    assert decision.reason == 'pane_dead'
    assert decision.confidence is CompletionConfidence.DEGRADED


def test_anchored_session_stability_detector_waits_for_settle_window() -> None:
    detector = AnchoredSessionStabilityDetector(settle_window_s=2.0)
    detector.bind(_ctx(), _cursor(0))
    detector.ingest(_item(CompletionItemKind.ANCHOR_SEEN, 1, '2026-03-18T00:00:01Z'))
    detector.ingest(
        _item(
            CompletionItemKind.SESSION_SNAPSHOT,
            2,
            '2026-03-18T00:00:02Z',
            {'message_id': 'm1', 'reply': 'hello', 'message_count': 2, 'last_updated': '1'},
        )
    )
    detector.tick('2026-03-18T00:00:03Z', _cursor(2))
    assert detector.decision().terminal is False

    detector.tick('2026-03-18T00:00:04Z', _cursor(2))
    decision = detector.decision()
    assert decision.terminal is True
    assert decision.status is CompletionStatus.COMPLETED
    assert decision.reason == 'session_reply_stable'
    assert decision.confidence is CompletionConfidence.OBSERVED


def test_anchored_session_stability_detector_resets_on_mutation() -> None:
    detector = AnchoredSessionStabilityDetector(settle_window_s=2.0)
    detector.bind(_ctx(), _cursor(0))
    detector.ingest(_item(CompletionItemKind.ANCHOR_SEEN, 1, '2026-03-18T00:00:01Z'))
    detector.ingest(
        _item(
            CompletionItemKind.SESSION_SNAPSHOT,
            2,
            '2026-03-18T00:00:02Z',
            {'message_id': 'm1', 'reply': 'hello', 'message_count': 2, 'last_updated': '1'},
        )
    )
    detector.tick('2026-03-18T00:00:03Z', _cursor(2))
    detector.ingest(
        _item(
            CompletionItemKind.SESSION_MUTATION,
            3,
            '2026-03-18T00:00:03Z',
            {'message_id': 'm1', 'reply': 'hello world', 'message_count': 2, 'last_updated': '2'},
        )
    )
    detector.tick('2026-03-18T00:00:04Z', _cursor(3))
    assert detector.decision().terminal is False
    detector.tick('2026-03-18T00:00:05Z', _cursor(3))
    assert detector.decision().terminal is True


def test_anchored_session_stability_detector_does_not_complete_after_rotate_without_new_reply() -> None:
    detector = AnchoredSessionStabilityDetector(settle_window_s=2.0)
    detector.bind(_ctx(), _cursor(0))
    detector.ingest(_item(CompletionItemKind.ANCHOR_SEEN, 1, '2026-03-18T00:00:01Z'))
    detector.ingest(
        _item(
            CompletionItemKind.SESSION_SNAPSHOT,
            2,
            '2026-03-18T00:00:02Z',
            {'message_id': 'm1', 'reply': 'hello', 'message_count': 2, 'last_updated': '1'},
        )
    )
    detector.ingest(
        _item(
            CompletionItemKind.SESSION_ROTATE,
            3,
            '2026-03-18T00:00:03Z',
            {'session_path': '/tmp/new-session.json'},
        )
    )
    detector.tick('2026-03-18T00:00:06Z', _cursor(3))
    decision = detector.decision()
    assert decision.terminal is False
    assert decision.reply == ''


def test_anchored_session_stability_detector_waits_while_tool_calls_are_active() -> None:
    detector = AnchoredSessionStabilityDetector(settle_window_s=2.0)
    detector.bind(_ctx(), _cursor(0))
    detector.ingest(_item(CompletionItemKind.ANCHOR_SEEN, 1, '2026-03-18T00:00:01Z'))
    detector.ingest(
        _item(
            CompletionItemKind.SESSION_SNAPSHOT,
            2,
            '2026-03-18T00:00:02Z',
            {
                'message_id': 'm1',
                'reply': 'I will inspect the files first.',
                'message_count': 2,
                'last_updated': '1',
                'tool_call_count': 1,
            },
        )
    )
    detector.tick('2026-03-18T00:00:06Z', _cursor(2))
    assert detector.decision().terminal is False

    detector.ingest(
        _item(
            CompletionItemKind.SESSION_SNAPSHOT,
            3,
            '2026-03-18T00:00:07Z',
            {
                'message_id': 'm2',
                'reply': 'Final answer.',
                'message_count': 3,
                'last_updated': '2',
                'tool_call_count': 0,
            },
        )
    )
    detector.tick('2026-03-18T00:00:08Z', _cursor(3))
    assert detector.decision().terminal is False
    detector.tick('2026-03-18T00:00:09Z', _cursor(3))
    assert detector.decision().terminal is True
