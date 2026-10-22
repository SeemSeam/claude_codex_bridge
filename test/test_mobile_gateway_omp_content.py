from __future__ import annotations

import hashlib
import json

import pytest

from mobile_gateway.omp_content import (
    CONTENT_BYTES, PAGE_BYTES, OmpContentError, OmpContentPager, ascii_size,
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
                assert row['native_turn']['entries'] == []
                assert row['native_turn']['status'] == 'completed'
                assert 'preview' in row['body']
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
