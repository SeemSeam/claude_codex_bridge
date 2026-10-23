from __future__ import annotations

import hashlib
import json

import pytest

from mobile_gateway.omp_content import (
    CONTENT_BYTES, INLINE_ITEM_BYTES, PAGE_BYTES, OmpContentError, OmpContentPager, ascii_size,
)
from mobile_gateway.relay_stream import RelayInnerMessage
from mobile_gateway.service import _omp_native_conversation_items

SCOPE = ('project', 'mobile', 4, 'session', 'generation:1')


def item(identity, text):
    return {'id': identity, 'agent': 'mobile', 'session_id': 'session',
            'kind': 'agent_reply', 'state': 'completed', 'source': 'provider_native/omp',
            'body': text, 'attachments': [],
            'native_turn': {'session_id': 'session', 'turn_id': identity,
                            'status': 'completed', 'entries': [
                                {'role': 'assistant', 'blocks': [
                                    {'type': 'reasoning', 'text': text},
                                    {'type': 'tool_call', 'input': {'code': text}},
                                    {'type': 'tool_result', 'text': text}]}]}}


def envelope(page):
    return {'schema_version': 1, 'status': 'ok', 'conversation': {
        'project_id': 'project', 'agent': 'mobile', 'namespace_epoch': 4, **page}}


def test_pages_budget_actual_ascii_relay_bytes_and_never_skip_items():
    pager = OmpContentPager()
    items = [item(str(i), '中文😀' * (1200 if i % 2 else 2)) for i in range(70)]
    items[15]['attachments'] = [{'label': 'a' * 700000}]
    seen = []
    cursor = None
    while True:
        page = pager.page(items, scope=SCOPE, revision=1, limit=50, cursor=cursor)
        assert ascii_size(envelope(page)) <= PAGE_BYTES
        assert len(RelayInnerMessage(kind='response', request_id='req-budget-0001',
                                    payload=envelope(page)).to_bytes()) <= PAGE_BYTES
        assert page['items']
        seen = [row['id'] for row in page['items']] + seen
        for row in page['items']:
            if row.get('native_turn', {}).get('read_cursor'):
                assert row['native_turn']['inline_window'] is True
                assert row['native_turn']['entries']
                assert row['native_turn']['status'] == 'completed'
                assert row['native_turn']['reason']
                assert row['body'] == ''
                assert row['attachments'] == []
        cursor = page['next_cursor']
        if cursor is None:
            break
    assert seen == [row['id'] for row in items]
    assert pager.page(items[:1], scope=SCOPE, revision=1, limit=50, cursor=None)['items'][0] is items[0]


def test_long_queued_input_uses_shared_budget_and_generic_original():
    pager = OmpContentPager()
    queued = {'id': 'original-key', 'agent': 'mobile', 'kind': 'user_message',
              'body': '中文😀' * 50000, 'state': 'queued', 'source': 'mobile/collab',
              'queue_position': 3}
    page = pager.page([queued], scope=SCOPE, revision=1, limit=50, cursor=None)
    preview = page['items'][0]
    assert ascii_size(envelope(page)) <= PAGE_BYTES
    assert 'native_turn' not in preview
    assert preview['state'] == 'queued'
    assert preview['queue_position'] == 3
    assert preview['body'] == queued['body'][-len(preview['body']):]
    assert 0 < len(preview['body']) <= 8000
    assert ascii_size(preview) <= INLINE_ITEM_BYTES
    cursor = preview['read_cursor']
    chunks = []
    while cursor:
        content = pager.content([queued], scope=SCOPE, revision=1, cursor=cursor)
        assert ascii_size(envelope({'native_content': content})) <= PAGE_BYTES
        chunks.append(content['text'])
        cursor = content['next_cursor']
    assert json.loads(''.join(chunks)) == queued


def test_original_roundtrip_hash_utf8_boundaries_and_complete_tools(tmp_path):
    text = '中文😀\\\n' * 60000
    records = [
        {'type': 'message', 'id': 'user', 'parentId': None,
         'message': {'role': 'user', 'content': 'read'}},
        {'type': 'message', 'id': 'call', 'parentId': 'user',
         'message': {'role': 'assistant', 'content': [
             {'type': 'thinking', 'thinking': text},
             {'type': 'toolCall', 'id': 'c', 'name': 'read', 'arguments': {'code': text}}]}},
        {'type': 'message', 'id': 'result', 'parentId': 'call',
         'message': {'role': 'toolResult', 'toolCallId': 'c', 'content': text}},
        {'type': 'message', 'id': 'final', 'parentId': 'result',
         'message': {'role': 'assistant', 'content': text, 'stopReason': 'stop'}},
    ]
    originals = _omp_native_conversation_items(tmp_path, project_id='project', agent='mobile', snapshot={
        'session_id': 'session', 'generation': 'generation:1', 'revision': 1,
        'entries': records, 'state': {'isStreaming': False}, 'connected': True, 'ready': True,
    })
    expected = json.dumps(originals[-1], ensure_ascii=False, sort_keys=True,
                          separators=(',', ':')).encode('utf-8')
    assert len(expected) > 512 * 1024
    pager = OmpContentPager()
    page = pager.page(originals, scope=SCOPE, revision=1, limit=50, cursor=None)
    cursor = page['items'][-1]['native_turn']['read_cursor']
    chunks = []
    while cursor:
        content = pager.content(originals, scope=SCOPE, revision=1, cursor=cursor)
        chunk = content['text'].encode('utf-8')
        assert 0 < len(chunk) <= CONTENT_BYTES
        assert content['offset'] == sum(map(len, chunks))
        assert content['total_bytes'] == len(expected)
        assert content['version'] == hashlib.sha256(expected).hexdigest()
        assert ascii_size(envelope({'items': [], 'native_content': content})) <= PAGE_BYTES
        RelayInnerMessage(kind='response', request_id='req-budget-0001',
                          payload=envelope({'items': [], 'native_content': content})).to_bytes()
        chunks.append(chunk)
        cursor = content['next_cursor']
    assert b''.join(chunks) == expected
    assert json.loads(b''.join(chunks)) == originals[-1]


@pytest.mark.parametrize('position,value', [(0, 'other'), (1, 'other'), (2, 5),
                                            (3, 'new-session'), (4, 'new-generation')])
def test_content_rejects_wrong_namespace_and_generation(position, value):
    pager = OmpContentPager()
    items = [item('a', 'x' * 600000)]
    cursor = pager.page(items, scope=SCOPE, revision=1, limit=50, cursor=None)['items'][0]['native_turn']['read_cursor']
    scope = list(SCOPE)
    scope[position] = value
    with pytest.raises(OmpContentError) as error:
        pager.content(items, scope=tuple(scope), revision=1, cursor=cursor)
    assert error.value.status_code == 409


def test_content_rejects_changed_deleted_and_invalid_cursor():
    pager = OmpContentPager()
    items = [item('a', 'x' * 600000)]
    cursor = pager.page(items, scope=SCOPE, revision=1, limit=50, cursor=None)['items'][0]['native_turn']['read_cursor']
    for changed in ([], [item('a', 'changed')]):
        with pytest.raises(OmpContentError) as error:
            pager.content(changed, scope=SCOPE, revision=2, cursor=cursor)
        assert error.value.status_code == 409
    for invalid in ('', 'omp-content-v1.', 'omp-content-v1.%%%'):
        with pytest.raises(OmpContentError) as error:
            pager.content(items, scope=SCOPE, revision=1, cursor=invalid)
        assert error.value.status_code == 400


def test_content_rejects_offsets_inside_utf8_and_out_of_range():
    pager = OmpContentPager()
    items = [item('a', '中文😀' * 50000)]
    data, version = pager._document(items[0], SCOPE, 1)
    continuation = next(i for i, value in enumerate(data) if value & 0xc0 == 0x80)
    for offset in (-1, continuation, len(data) + 1):
        cursor = pager._cursor(SCOPE, 'a', version, offset)
        with pytest.raises(OmpContentError) as error:
            pager.content(items, scope=SCOPE, revision=1, cursor=cursor)
        assert error.value.status_code == 400


def test_content_extreme_escaping_stays_within_ascii_page_budget():
    pager = OmpContentPager()
    items = [item('a', '漢' * 100000)]
    page = pager.page(items, scope=SCOPE, revision=1, limit=50, cursor=None)
    content = pager.content(items, scope=SCOPE, revision=1,
                           cursor=page['items'][0]['native_turn']['read_cursor'])
    assert len(content['text'].encode('utf-8')) <= CONTENT_BYTES
    assert ascii_size(envelope({'items': [], 'native_content': content})) <= PAGE_BYTES


def test_giant_metadata_and_attachments_do_not_escape_inline_budget():
    pager = OmpContentPager()
    original = item('stable-id', 'small')
    original.update(title='漢' * 300000, attachments=[{'label': 'x' * 700000}],
                    unexpected='y' * 700000)
    page = pager.page([original], scope=SCOPE, revision=1, limit=50, cursor=None)
    projected = page['items'][0]
    assert projected['id'] == original['id']
    assert ascii_size(envelope(page)) <= PAGE_BYTES
    assert projected['attachments'] == []
    assert 'unexpected' not in projected
    cursor = projected['native_turn']['read_cursor']
    content = pager.content([original], scope=SCOPE, revision=1, cursor=cursor)
    assert content['total_bytes'] > 1400000


def test_oversized_window_keeps_latest_answer_and_readable_recent_tool_context():
    original = item('turn', 'old body prefix' * 10000)
    native = original['native_turn']
    native.update(status='running', streaming=True)
    native['entries'] = [
        {'id': 'old-answer', 'role': 'assistant', 'blocks': [
            {'type': 'text', 'text': 'obsolete answer'}]},
        {'id': 'answer', 'role': 'assistant', 'stop_reason': 'tool_use',
         'streaming': True, 'blocks': [
             {'type': 'reasoning', 'text': 'old reasoning\n' * 10000},
             {'type': 'text', 'text': 'answer beginning\n' * 2000 + '\nLATEST ANSWER'}]},
        {'id': 'call', 'role': 'assistant', 'blocks': [
            {'type': 'tool_call', 'call_id': 'call-1', 'name': 'read',
             'input': {'path': '/latest/path', 'content': 'huge input' * 10000}}]},
        {'id': 'result', 'role': 'tool', 'blocks': [
            {'type': 'tool_result', 'call_id': 'call-1', 'name': 'read',
             'is_error': True, 'text': 'old output\n' * 10000 + 'LATEST TOOL ERROR'}]},
    ]
    before = json.dumps(original, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    pager = OmpContentPager()
    projected = pager.page([original], scope=SCOPE, revision=1, limit=1, cursor=None)['items'][0]
    window = projected['native_turn']
    assert projected['body'] == ''
    assert window['inline_window'] is True
    assert window['status'] == 'running'
    assert window['streaming'] is True
    assert 'omitted' in window['reason']
    entries = {entry['id']: entry for entry in window['entries']}
    answer = entries['answer']
    assert answer['stop_reason'] == 'tool_use'
    assert answer['streaming'] is True
    prose = next(block['text'] for block in answer['blocks'] if block['type'] == 'text')
    assert prose.endswith('LATEST ANSWER')
    assert 0 < len(prose) <= 8000
    call = entries['call']['blocks'][0]
    assert (call['call_id'], call['name']) == ('call-1', 'read')
    assert isinstance(call['input'], str)
    assert '/latest/path' in call['input']
    assert len(call['input']) <= 1500
    result = entries['result']['blocks'][0]
    assert result['text'].endswith('LATEST TOOL ERROR')
    assert len(result['text']) <= 1500
    assert result['is_error'] is True
    assert result['call_id'] == 'call-1'
    assert ascii_size(projected) <= INLINE_ITEM_BYTES
    assert json.dumps(original, ensure_ascii=False, sort_keys=True, separators=(',', ':')) == before
    content = pager.content([original], scope=SCOPE, revision=1, cursor=window['read_cursor'])
    assert content['version'] == hashlib.sha256(before.encode('utf-8')).hexdigest()


def test_wrapped_cursor_unicode_and_long_identity_fit_actual_ascii_item_budget():
    original = item('turn-' + 'x' * 700, 'stale' * 10000)
    original['native_turn']['entries'] = [
        {'id': 'answer-' + 'a' * 700, 'role': 'assistant', 'blocks': [
            {'type': 'text', 'text': '漢😀' * 30000 + 'LATEST PROSE'}]},
        {'id': 'call-' + 'b' * 700, 'role': 'assistant', 'blocks': [
            {'type': 'tool_call', 'name': 'inspect', 'call_id': 'c' * 700,
             'input': {'path': '/unicode/path', 'data': '漢😀' * 50000}}]},
        {'id': 'result-' + 'd' * 700, 'role': 'tool', 'blocks': [
            {'type': 'tool_result', 'name': 'inspect', 'call_id': 'c' * 700,
             'is_error': False, 'text': '漢😀' * 50000 + 'LATEST RESULT'}]},
    ]
    wrapper = 'wrapped:' + 'route' * 200 + ':'
    projected = OmpContentPager().page(
        [original], scope=SCOPE, revision=1, limit=1, cursor=None,
        wrap_content_cursor=lambda cursor: wrapper + cursor)['items'][0]
    window = projected['native_turn']
    assert projected['id'] == original['id']
    assert window['read_cursor'].startswith(wrapper)
    assert ascii_size(projected) <= INLINE_ITEM_BYTES
    assert len(json.dumps(projected, ensure_ascii=True, separators=(',', ':')).encode('ascii')) <= INLINE_ITEM_BYTES
    assert [entry['id'] for entry in window['entries']] == [
        entry['id'] for entry in original['native_turn']['entries']]
    blocks = [block for entry in window['entries'] for block in entry['blocks']]
    assert blocks[0]['text'].endswith('LATEST PROSE')
    assert blocks[-1]['text'].endswith('LATEST RESULT')
    assert '/unicode/path' in blocks[1]['input']


def test_recent_window_limits_chronology_and_answer_independent_of_process_volume():
    original = item('turn', 'body' * 10000)
    original['native_turn']['entries'] = [
        {'id': 'answer', 'role': 'assistant', 'blocks': [
            {'type': 'text', 'text': 'LATEST ASSISTANT PROSE'}]},
        *[{'id': f'tool-{index}', 'role': 'tool', 'blocks': [
            {'type': 'tool_result', 'text': f'recent output {index}-{block}'}
            for block in range(5)]} for index in range(20)],
    ]
    projected = OmpContentPager().page([original], scope=SCOPE, revision=1, limit=1, cursor=None)['items'][0]
    entries = projected['native_turn']['entries']
    assert len(entries) <= 8
    assert sum(len(entry['blocks']) for entry in entries) <= 24
    ids = [entry['id'] for entry in original['native_turn']['entries']]
    assert [ids.index(entry['id']) for entry in entries] == sorted(ids.index(entry['id']) for entry in entries)
    assert entries[0]['blocks'][0]['text'] == 'LATEST ASSISTANT PROSE'
    assert entries[-1]['blocks'][-1]['text'] == 'recent output 19-4'


def test_refresh_window_replaces_changed_latest_text_and_keeps_original_versions_fenced():
    pager = OmpContentPager()
    original = item('turn', 'body' * 10000)
    original['native_turn']['entries'] = [
        {'id': 'answer', 'role': 'assistant', 'streaming': True,
         'blocks': [{'type': 'text', 'text': 'old text' * 10000 + 'FIRST TAIL'}]}]
    first = pager.page([original], scope=SCOPE, revision=1, limit=1, cursor=None)['items'][0]
    original['native_turn']['entries'][0]['blocks'][0]['text'] = 'new text' * 10000 + 'SECOND TAIL'
    original['native_turn']['entries'][0]['streaming'] = False
    original['native_turn']['entries'][0]['stop_reason'] = 'stop'
    second = pager.page([original], scope=SCOPE, revision=2, limit=1, cursor=None)['items'][0]
    first_entry = first['native_turn']['entries'][0]
    second_entry = second['native_turn']['entries'][0]
    assert first_entry['blocks'][0]['text'].endswith('FIRST TAIL')
    assert second_entry['blocks'][0]['text'].endswith('SECOND TAIL')
    assert 'FIRST TAIL' not in second_entry['blocks'][0]['text']
    assert second_entry['streaming'] is False
    assert second_entry['stop_reason'] == 'stop'
    assert second['native_turn']['inline_window'] is True
    assert first['native_turn']['read_cursor'] != second['native_turn']['read_cursor']
    with pytest.raises(OmpContentError) as error:
        pager.content([original], scope=SCOPE, revision=2, cursor=first['native_turn']['read_cursor'])
    assert error.value.status_code == 409


def test_oversized_native_user_keeps_tail_and_complete_original():
    original = item('native-user', 'old user text\n' * 20000 + 'LATEST USER REQUEST')
    original['kind'] = 'user_message'
    original['native_turn']['entries'] = [
        {'id': 'native-user', 'role': 'user', 'blocks': [
            {'type': 'text', 'text': original['body']}]}]
    pager = OmpContentPager()
    projected = pager.page([original], scope=SCOPE, revision=1, limit=1, cursor=None)['items'][0]
    assert 'native_turn' not in projected
    assert projected['body'].endswith('LATEST USER REQUEST')
    assert len(projected['body']) <= 8000
    assert projected['read_reason']
    assert ascii_size(projected) <= INLINE_ITEM_BYTES
    content = pager.content([original], scope=SCOPE, revision=1, cursor=projected['read_cursor'])
    expected = json.dumps(original, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
    assert content['version'] == hashlib.sha256(expected).hexdigest()
