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


def read_blocks(pager, items, cursor, revision=1):
    """Reconstruct semantic source blocks just as a paged consumer would."""
    blocks = []
    page_index = 0
    while cursor:
        page = pager.content(items, scope=SCOPE, revision=revision, cursor=cursor)
        assert page['schema_version'] == 2
        assert page['page_index'] == page_index
        assert 'text' not in page and 'offset' not in page and 'total_bytes' not in page
        assert sum(len(block['text'].encode('utf-8')) for block in page['blocks']) <= CONTENT_BYTES
        identities = [(block['entry_id'], block['block_index']) for block in page['blocks']]
        assert len(identities) == len(set(identities))
        payload = envelope({'items': [], 'native_content': page})
        assert ascii_size(payload) <= PAGE_BYTES
        assert len(RelayInnerMessage(kind='response', request_id='req-budget-0001',
                                    payload=payload).to_bytes()) <= PAGE_BYTES
        for fragment in page['blocks']:
            block = dict(fragment)
            continued_before = block.pop('continued_before')
            continued_after = block.pop('continued_after')
            assert isinstance(block.pop('render_prefix'), str)
            assert isinstance(block.pop('render_suffix'), str)
            if continued_before:
                assert blocks[-1]['continued_after']
                assert {k: v for k, v in blocks[-1].items() if k not in {'text', 'continued_after'}} == {
                    k: v for k, v in block.items() if k != 'text'}
                blocks[-1]['text'] += block['text']
                blocks[-1]['continued_after'] = continued_after
            else:
                if blocks:
                    assert not blocks[-1]['continued_after']
                blocks.append({**block, 'continued_after': continued_after})
        cursor = page['next_cursor']
        page_index += 1
    assert all(not block.pop('continued_after') for block in blocks)
    assert len(blocks) == page['total_blocks']
    return blocks


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
    blocks = read_blocks(pager, [queued], cursor)
    assert blocks == [{'entry_id': queued['id'], 'block_index': 0, 'role': 'user',
                       'type': 'text', 'text': queued['body']}]


def test_semantic_content_hash_utf8_boundaries_and_complete_tools(tmp_path):
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
    first = pager.content(originals, scope=SCOPE, revision=1, cursor=cursor)
    assert first['version'] == hashlib.sha256(expected).hexdigest()
    blocks = read_blocks(pager, originals, cursor)
    assert [block['type'] for block in blocks] == ['text', 'reasoning', 'tool_call', 'tool_result']
    assert [block['entry_id'] for block in blocks] == ['final', 'call', 'call', 'result']
    assert [block['block_index'] for block in blocks] == [0, 0, 1, 0]
    # Native projection trims prose/reasoning/result whitespace before paging;
    # tool arguments retain the original string, including its final newline.
    assert [block['text'] for block in blocks] == [
        text.rstrip(), text.rstrip(), json.dumps({'code': text}, ensure_ascii=False, indent=2), text.rstrip()]
    assert blocks[2]['call_id'] == 'c'
    assert blocks[2]['name'] == 'read'
    assert blocks[3]['role'] == 'tool'


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
    for invalid in ('', 'omp-content-v1.', 'omp-content-v2.', 'omp-content-v2.%%%'):
        with pytest.raises(OmpContentError) as error:
            pager.content(items, scope=SCOPE, revision=1, cursor=invalid)
        assert error.value.status_code == 400


def test_content_rejects_invalid_block_positions_and_tampering():
    pager = OmpContentPager()
    items = [item('a', '中文😀' * 50000)]
    blocks, version, _ = pager._document(items[0], SCOPE, 1)
    for index, offset in ((-1, 0), (len(blocks), 0), (0, -1), (0, len(blocks[0]['text']) + 1)):
        cursor = pager._cursor(SCOPE, 'a', version, index, offset)
        with pytest.raises(OmpContentError) as error:
            pager.content(items, scope=SCOPE, revision=1, cursor=cursor)
        assert error.value.status_code == 400
    cursor = pager._cursor(SCOPE, 'a', version, 0)
    tampered = cursor[:-4] + ('AAAA' if cursor[-4:] != 'AAAA' else 'BBBB')
    with pytest.raises(OmpContentError) as error:
        pager.content(items, scope=SCOPE, revision=1, cursor=tampered)
    assert error.value.status_code == 400


@pytest.mark.parametrize('text', ['漢' * 100000, '\x00' * 100000, '😀' * 100000])
def test_extreme_escaping_stays_within_actual_ascii_page_budget(text):
    pager = OmpContentPager()
    original = {'id': 'generic', 'kind': 'agent_reply', 'body': text}
    preview = pager.page([original], scope=SCOPE, revision=1, limit=1, cursor=None)['items'][0]
    blocks = read_blocks(pager, [original], preview['read_cursor'])
    assert blocks == [{'entry_id': 'generic', 'block_index': 0, 'role': 'assistant',
                       'type': 'text', 'text': text}]


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
    assert content['total_blocks'] == 3
    assert content['blocks'][0]['text'] == 'small'
    assert content['blocks'][0]['type'] == 'reasoning'


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
    blocks = read_blocks(pager, [original], projected['read_cursor'])
    assert blocks == [{'entry_id': original['id'], 'block_index': 0, 'role': 'user',
                       'type': 'text', 'text': original['body']}]


def test_markdown_paragraph_and_fenced_code_fragments_preserve_exact_content():
    text = '# Answer\n\n' + 'paragraph text ' * 200 + '\n\n```python\n' + (
        'print("中文😀")\n' * 9000) + '```\n\nFinal paragraph.\n'
    original = {'id': 'markdown', 'kind': 'agent_reply', 'body': text}
    pager = OmpContentPager()
    preview = pager.page([original], scope=SCOPE, revision=1, limit=1, cursor=None)['items'][0]
    cursor = preview['read_cursor']
    first = pager.content([original], scope=SCOPE, revision=1, cursor=cursor)
    assert first['blocks'][0]['continued_before'] is False
    assert first['blocks'][0]['continued_after'] is True
    assert first['blocks'][0]['text'].endswith('\n')
    second = pager.content([original], scope=SCOPE, revision=1, cursor=first['next_cursor'])
    assert second['blocks'][0]['continued_before'] is True
    assert second['blocks'][0]['continued_after'] is True
    assert first['blocks'][0]['render_suffix'] == '\n```'
    assert second['blocks'][0]['render_prefix'] == '```python\n'
    assert second['blocks'][0]['render_suffix'] == '\n```'
    blocks = read_blocks(pager, [original], cursor)
    assert len(blocks) == 1
    assert blocks[0]['text'] == text


def test_paragraph_split_emits_source_block_only_once_per_page():
    paragraph = 'a' * 20000 + '\n\n'
    text = paragraph + 'b' * 20000 + '\n\n' + 'c' * 20000
    original = {'id': 'paragraphs', 'kind': 'agent_reply', 'body': text}
    pager = OmpContentPager()
    _, version, _ = pager._document(original, SCOPE, 1)
    cursor = pager._cursor(SCOPE, original['id'], version, 0)
    first = pager.content([original], scope=SCOPE, revision=1, cursor=cursor)
    assert len(first['blocks']) == 1
    assert first['blocks'][0]['text'] == paragraph
    assert first['blocks'][0]['continued_after'] is True
    second = pager.content([original], scope=SCOPE, revision=1, cursor=first['next_cursor'])
    assert len(second['blocks']) == 1
    assert second['blocks'][0]['continued_before'] is True
    assert second['blocks'][0]['text'] == 'b' * 20000 + '\n\n'
    assert read_blocks(pager, [original], cursor)[0]['text'] == text



@pytest.mark.parametrize('boundary', ['opening', 'closing'])
def test_exact_page_fence_boundary_needs_no_synthetic_markup(boundary):
    if boundary == 'opening':
        text = 'a' * (CONTENT_BYTES - 2) + '\n\n```python\n' + 'code\n' * 10000 + '```\n'
    else:
        opener, closer = '```python\n', '\n```\n'
        text = opener + 'x' * (CONTENT_BYTES - len(opener) - len(closer)) + closer + 'after\n' * 10000
    original = {'id': 'fence-boundary', 'kind': 'agent_reply', 'body': text}
    pager = OmpContentPager()
    _, version, _ = pager._document(original, SCOPE, 1)
    cursor = pager._cursor(SCOPE, original['id'], version, 0)
    first = pager.content([original], scope=SCOPE, revision=1, cursor=cursor)
    assert len(first['blocks'][0]['text'].encode('utf-8')) == CONTENT_BYTES
    assert first['blocks'][0]['render_suffix'] == ''
    second = pager.content([original], scope=SCOPE, revision=1, cursor=first['next_cursor'])
    assert second['blocks'][0]['render_prefix'] == ''
    assert read_blocks(pager, [original], cursor)[0]['text'] == text

@pytest.mark.parametrize('opener,render_opener', [
    ('```python ' + 'metadata' * 20000, '```python'),
    ('```' + 'oversizedlanguage' * 10000, '```'),
    ('`' * 100000, '`' * 64),
])
def test_oversized_fence_opening_line_keeps_bounded_render_context(opener, render_opener):
    closing = '`' * (len(opener) if not opener.strip('`') else 3)
    text = opener + '\nprint("中文😀")\n' + closing + '\n'
    original = {'id': 'long-fence', 'kind': 'agent_reply', 'body': text}
    pager = OmpContentPager()
    _, version, _ = pager._document(original, SCOPE, 1)
    cursor = pager._cursor(SCOPE, original['id'], version, 0)
    first = pager.content([original], scope=SCOPE, revision=1, cursor=cursor)
    assert '\n' not in first['blocks'][0]['text']
    render_delimiter = render_opener[:len(render_opener) - len(render_opener.lstrip('`'))]
    assert first['blocks'][0]['render_suffix'] == '\n' + render_delimiter
    second = pager.content([original], scope=SCOPE, revision=1, cursor=first['next_cursor'])
    assert second['blocks'][0]['render_prefix'] == render_opener + '\n'
    assert len(second['blocks'][0]['render_prefix']) <= 129
    assert read_blocks(pager, [original], cursor)[0]['text'] == text


def test_semantic_order_multiple_answers_execution_and_no_body_duplicate():
    original = item('ordered', 'DUPLICATED BODY MUST NOT APPEAR')
    original['native_turn']['entries'] = [
        {'id': 'early', 'role': 'assistant', 'blocks': [
            {'type': 'reasoning', 'text': 'plan'},
            {'type': 'text', 'text': 'first prose'},
            {'type': 'tool_call', 'name': 'read', 'input': {'path': '/tmp/a'}}]},
        {'id': 'result', 'role': 'tool', 'blocks': [
            {'type': 'tool_result', 'text': 'failed', 'is_error': True, 'call_id': 'c'}]},
        {'id': 'last', 'role': 'assistant', 'blocks': [{'type': 'text', 'text': 'final prose'}]},
    ]
    pager = OmpContentPager()
    _, version, _ = pager._document(original, SCOPE, 1)
    blocks = read_blocks(pager, [original], pager._cursor(SCOPE, original['id'], version, 0))
    assert [block['text'] for block in blocks] == [
        'first prose', 'final prose', 'plan', '{\n  "path": "/tmp/a"\n}', 'failed']
    assert [(block['entry_id'], block['block_index']) for block in blocks] == [
        ('early', 1), ('last', 0), ('early', 0), ('early', 2), ('result', 0)]
    assert blocks[-1]['is_error'] is True
    assert blocks[-1]['call_id'] == 'c'
