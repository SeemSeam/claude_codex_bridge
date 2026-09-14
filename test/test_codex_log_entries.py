from __future__ import annotations

from provider_backends.codex.comm_runtime.log_entries import extract_entry, extract_event, extract_message, extract_user_message


def test_extract_message_from_response_item_joins_assistant_content() -> None:
    entry = {
        'type': 'response_item',
        'payload': {
            'type': 'message',
            'role': 'assistant',
            'content': [
                {'type': 'output_text', 'text': 'hello'},
                {'type': 'text', 'text': 'world'},
            ],
        },
    }

    assert extract_message(entry) == 'hello\nworld'


def test_extract_user_message_from_response_item_input_text() -> None:
    entry = {
        'type': 'response_item',
        'payload': {
            'type': 'message',
            'role': 'user',
            'content': [
                {'type': 'input_text', 'text': 'first'},
                {'type': 'input_text', 'text': 'second'},
            ],
        },
    }

    assert extract_user_message(entry) == 'first\nsecond'
    assert extract_entry(entry) == {
        'entry_type': 'response_item',
        'payload_type': 'message',
        'timestamp': None,
        'phase': None,
        'turn_id': None,
        'task_id': None,
        'reason': None,
        'last_agent_message': None,
        'error': None,
        'entry': entry,
        'role': 'user',
        'text': 'first\nsecond',
    }


def test_extract_entry_handles_system_event_payloads() -> None:
    task_complete = {
        'type': 'event_msg',
        'payload': {
            'type': 'task_complete',
            'last_agent_message': 'done',
            'reason': 'completed',
        },
    }
    turn_aborted = {
        'type': 'event_msg',
        'payload': {
            'type': 'turn_aborted',
            'message': 'stopped',
        },
    }

    assert extract_entry(task_complete)['role'] == 'system'
    assert extract_entry(task_complete)['text'] == 'done'
    assert extract_entry(turn_aborted)['reason'] == 'turn_aborted'


def test_extract_codex_task_started_preserves_parent_turn_binding() -> None:
    entry = {
        'type': 'event_msg',
        'payload': {'type': 'task_started', 'turn_id': 'parent-turn'},
    }

    assert extract_entry(entry) == {
        'entry_type': 'event_msg',
        'payload_type': 'task_started',
        'timestamp': None,
        'phase': None,
        'turn_id': 'parent-turn',
        'task_id': None,
        'reason': 'task_started',
        'last_agent_message': None,
        'error': None,
        'entry': entry,
        'role': 'system',
        'text': '',
    }


def test_extract_entry_preserves_task_complete_terminal_error() -> None:
    blocked = {
        'type': 'event_msg',
        'payload': {
            'type': 'task_complete',
            'last_agent_message': None,
            'error': {
                'message': 'This request was blocked by our safety systems.',
                'codex_error_info': 'misalignment_policy_violation',
            },
        },
    }

    normalized = extract_entry(blocked)

    assert normalized['role'] == 'system'
    assert normalized['error'] == {
        'message': 'This request was blocked by our safety systems.',
        'codex_error_info': 'misalignment_policy_violation',
    }


def test_extract_entry_task_complete_without_error_keeps_none() -> None:
    completed = {
        'type': 'event_msg',
        'payload': {
            'type': 'task_complete',
            'last_agent_message': 'final answer',
            'reason': 'completed',
        },
    }

    normalized = extract_entry(completed)

    assert normalized['error'] is None
    assert normalized['text'] == 'final answer'


def test_extract_codex_native_subagent_messages_are_not_reply_candidates() -> None:
    activity = {'type': 'event_msg', 'payload': {'type': 'sub_agent_activity', 'agent_path': '/root/child'}}
    child_reply = {
        'type': 'response_item',
        'payload': {
            'type': 'agent_message',
            'author': '/root/child',
            'recipient': '/root',
            'content': [{'type': 'input_text', 'text': 'child final'}],
        },
    }

    assert extract_entry(activity) is None
    assert extract_entry(child_reply) is None


def test_extract_event_returns_only_user_or_assistant_messages() -> None:
    entry = {
        'type': 'event_msg',
        'payload': {
            'type': 'assistant_message',
            'role': 'assistant',
            'message': 'reply',
        },
    }

    assert extract_event(entry) == ('assistant', 'reply')
