from __future__ import annotations

import json
from pathlib import Path

import pytest

from mobile_gateway.service import _omp_native_conversation_items


def _message(identity: str, parent: str | None, role: str, content=None, **extra) -> dict:
    return {
        'type': 'message', 'id': identity, 'parentId': parent,
        'timestamp': '2026-09-06T12:00:01.000Z',
        'message': {'role': role, 'content': content or [], **extra},
    }


def _project(tmp_path: Path, records: list[dict], *, tail: str = ''):
    root = tmp_path / 'repo'
    return _omp_native_conversation_items(
        root, project_id='project', agent='mobile', snapshot={
            'session_id': 'native-session', 'generation': 1, 'revision': len(records),
            'entries': records, 'state': {'isStreaming': False},
            'connected': True, 'ready': True,
        },
    )

def test_omp_collab_omitted_metadata_keeps_tools_in_original_user_turn(tmp_path):
    items = _omp_native_conversation_items(tmp_path, project_id='p', agent='mobile', snapshot={
        'session_id': 's', 'state': {'isStreaming': False, 'leafId': 'final'},
        'parent_links': [['user', None], ['private-before', 'user'], ['call', 'private-before'],
                         ['private-after', 'call'], ['result', 'private-after'], ['final', 'result']],
        'entries': [
            {'type': 'custom_message', 'customType': 'collab-prompt', 'id': 'user', 'parentId': None, 'content': 'read'},
            _message('call', 'private-before', 'assistant', [{'type':'toolCall','id':'c','name':'read','arguments':{}}], stopReason='tool_use'),
            _message('result', 'private-after', 'toolResult', 'read output', toolCallId='c'),
            _message('final', 'result', 'assistant', 'done', stopReason='stop'),
        ],
    })
    assert [item['kind'] for item in items] == ['user_message', 'agent_reply']
    assert items[1]['native_turn']['turn_id'] == 'user'
    assert [entry['id'] for entry in items[1]['native_turn']['entries']] == ['call', 'result', 'final']
    assert items[1]['body'] == 'done'


def test_omp_multiple_tools_share_user_turn_and_next_user_isolated(tmp_path: Path):
    records = [
        _message('u1', None, 'user', 'first'),
        _message('a1', 'u1', 'assistant', [
            {'type': 'thinking', 'thinking': 'visible reasoning', 'signature': 'secret'},
            {'type': 'text', 'text': 'checking'},
            {'type': 'toolCall', 'id': 'raw:call/1', 'name': 'read', 'arguments': {'path': 'a'}},
            {'type': 'toolCall', 'id': 'raw:call/2', 'name': 'read', 'arguments': {'path': 'b'}},
        ], stopReason='tool_use', providerMetadata={'secret': True}),
        _message('t1', 'a1', 'toolResult', 'one', toolCallId='raw:call/1', toolName='read', isError=False),
        _message('t2', 't1', 'toolResult', 'two', toolCallId='raw:call/2', toolName='read', isError=True),
        _message('a2', 't2', 'assistant', 'final answer', stopReason='stop'),
        _message('u2', 'a2', 'user', 'second'),
        _message('a3', 'u2', 'assistant', 'second answer'),
    ]
    items = _project(tmp_path, records)
    assert [(i['kind'], i['body']) for i in items] == [
        ('user_message', 'first'), ('agent_reply', 'final answer'),
        ('user_message', 'second'), ('agent_reply', 'second answer'),
    ]
    first, second = items[1]['native_turn'], items[3]['native_turn']
    assert first['schema_version'] == 1
    assert first['session_id'] == 'native-session'
    assert first['turn_id'] == 'u1'
    assert first['status'] == 'completed'
    assert second['turn_id'] == 'u2'
    assert second['status'] == 'unknown'
    assert [e['id'] for e in first['entries']] == ['a1', 't1', 't2', 'a2']
    assert first['entries'][0]['blocks'][0] == {'type': 'reasoning', 'text': 'visible reasoning'}
    assert first['entries'][0]['blocks'][2] == {
        'type': 'tool_call', 'call_id': 'raw:call/1', 'name': 'read', 'input': {'path': 'a'},
    }
    assert first['entries'][1]['blocks'][0] == {
        'type': 'tool_result', 'text': 'one', 'call_id': 'raw:call/1', 'name': 'read', 'is_error': False,
    }
    assert 'secret' not in json.dumps(items)
    assert 'completed_at' in items[1]
    assert 'completed_at' not in items[3]
    # Appending in the same turn cannot change either turn's identity.
    extended = _project(tmp_path, records + [_message('a4', 'a3', 'assistant', 'done', stopReason='stop')])
    assert [i['id'] for i in extended] == [i['id'] for i in items]


def test_omp_active_branch_and_truncated_tail_keep_persisted_history(tmp_path: Path):
    items = _project(tmp_path, [
        _message('u', None, 'user', 'question'),
        _message('old', 'u', 'assistant', 'inactive', stopReason='stop'),
        _message('new', 'u', 'assistant', 'active', stopReason='stop'),
    ], tail='{"type":"message","id":"unfinished')
    assert [i['body'] for i in items] == ['question', 'active']
    assert [e['id'] for e in items[1]['native_turn']['entries']] == ['new']


@pytest.mark.parametrize('reason,status', [('error', 'failed'), ('aborted', 'aborted')])
def test_omp_terminal_without_body_survives_and_keeps_process(tmp_path: Path, reason, status):
    items = _project(tmp_path, [
        _message('u', None, 'user', 'question'),
        _message('a', 'u', 'assistant', 'partial answer', stopReason='tool_use'),
        _message('end', 'a', 'assistant', stopReason=reason, errorMessage='provider unavailable'),
    ])
    reply = items[1]
    assert reply['body'] == 'partial answer'
    assert reply['native_turn']['status'] == status
    assert [e['id'] for e in reply['native_turn']['entries']] == ['a', 'end']
    assert reply['native_turn']['entries'][-1]['blocks'] == [
        {'type': 'text', 'text': 'provider unavailable'},
    ]
    assert 'completed_at' in reply
    empty = _project(tmp_path, [_message('failure', None, 'assistant', stopReason=reason)])
    assert len(empty) == 1
    assert empty[0]['body'] == ''
    assert empty[0]['native_turn']['status'] == status


@pytest.mark.parametrize('reason', ['tool_use', 'stop', None])
def test_omp_idle_host_does_not_complete_tool_only_turn(tmp_path: Path, reason):
    items = _project(tmp_path, [
        _message('u', None, 'user', 'do work'),
        _message('a', 'u', 'assistant', [
            {'type': 'toolCall', 'id': 'call', 'name': 'exec', 'arguments': {}},
        ], **({'stopReason': reason} if reason else {})),
        _message('t', 'a', 'toolResult', 'output', toolCallId='call'),
    ])
    assert len(items) == 2
    assert items[1]['body'] == ''
    assert items[1]['native_turn']['status'] == 'unknown'
    assert 'completed_at' not in items[1]


def test_omp_unanchored_entries_do_not_guess_shared_turn(tmp_path: Path):
    items = _project(tmp_path, [
        _message('a', None, 'assistant', 'orphan'),
        _message('t', 'a', 'toolResult', 'result', toolCallId='unknown'),
    ])
    assert len(items) == 2
    assert [i['native_turn']['turn_id'] for i in items] == ['a', 't']
    assert [len(i['native_turn']['entries']) for i in items] == [1, 1]


def test_omp_live_append_replace_durable_and_generation(tmp_path: Path):
    from mobile_gateway.omp_conversation import project_omp_snapshot
    snapshot = {
        'session_id': 'session', 'generation': 1,
        'entries': [_message('u', None, 'user', 'question')],
        'state': {'isStreaming': True}, 'stream_id': 'stream-1',
        'streaming_message': {'role': 'assistant', 'content': 'partial'},
    }
    _, turns = project_omp_snapshot(snapshot, clean_text=lambda text: text)
    assert turns[0].body == 'partial'
    assert turns[0].status == 'running'
    snapshot['streaming_message']['content'] = 'replacement'
    _, replaced = project_omp_snapshot(snapshot, clean_text=lambda text: text)
    assert replaced[0].turn_id == turns[0].turn_id
    assert replaced[0].body == 'replacement'
    snapshot['entries'].append(_message('a', 'u', 'assistant', 'replacement', stopReason='stop'))
    snapshot['stream_entry_ids'] = {'stream-1': 'a'}
    snapshot['state'] = {'isStreaming': False}
    _, durable = project_omp_snapshot(snapshot, clean_text=lambda text: text)
    assert [entry['id'] for entry in durable[0].entries] == ['a']
    assert durable[0].status == 'completed'
    snapshot.update(generation=2, entries=[], streaming_message=None)
    assert project_omp_snapshot(snapshot, clean_text=lambda text: text)[1] == []


def test_omp_custom_collab_prompt_and_explicit_branch():
    from mobile_gateway.omp_conversation import project_omp_snapshot
    snapshot = {
        'session_id': 'session', 'entries': [
            {'type': 'custom_message', 'customType': 'collab-prompt', 'id': 'u',
             'parentId': None, 'content': 'mobile prompt', 'details': {'from': 'mobile'}},
            _message('chosen', 'u', 'assistant', 'chosen'),
            _message('inactive', 'u', 'assistant', 'inactive'),
        ], 'state': {'leafId': 'chosen', 'isStreaming': True},
    }
    _, turns = project_omp_snapshot(snapshot, clean_text=lambda text: text)
    assert turns[0].user['blocks'] == [{'type': 'text', 'text': 'mobile prompt'}]
    assert turns[0].body == 'chosen'
    assert turns[0].status == 'running'


@pytest.mark.parametrize('state,status', [
    ({'isStreaming': False, 'isAborting': False, 'queuedMessageCount': 0}, 'completed'),
    ({'isStreaming': True}, 'running'),
    ({'isStreaming': False, 'isAborting': True}, 'running'),
    ({'isStreaming': False, 'queuedMessageCount': 1}, 'running'),
    ({}, 'unknown'),
])
def test_omp_completion_requires_durable_stop_and_idle_host(state, status):
    from mobile_gateway.omp_conversation import project_omp_snapshot
    snapshot = {
        'session_id': 'session', 'entries': [
            _message('u', None, 'user', 'question'),
            _message('a', 'u', 'assistant', 'answer', stopReason='stop'),
        ], 'state': state,
    }
    _, turns = project_omp_snapshot(snapshot, clean_text=lambda text: text)
    assert turns[0].status == status


def test_omp_live_message_does_not_complete_from_idle_state():
    from mobile_gateway.omp_conversation import project_omp_snapshot
    snapshot = {
        'session_id': 'session', 'entries': [_message('u', None, 'user', 'question')],
        'streaming_message': {'role': 'assistant', 'content': 'partial', 'stopReason': 'stop'},
        'stream_id': 'live', 'state': {'isStreaming': False},
    }
    _, turns = project_omp_snapshot(snapshot, clean_text=lambda text: text)
    assert turns[0].status == 'running'


def test_omp_authoritative_empty_branch_does_not_show_old_entries():
    from mobile_gateway.omp_conversation import project_omp_snapshot
    snapshot = {
        'session_id': 'session', 'entries': [_message('old', None, 'user', 'old branch')],
        'state': {'leafId': None, 'isStreaming': False},
    }
    assert project_omp_snapshot(snapshot, clean_text=lambda text: text)[1] == []
