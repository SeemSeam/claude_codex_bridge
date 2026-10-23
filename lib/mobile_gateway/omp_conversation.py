"""Project OMP's active native parent chain into user-anchored turns.

This module handles only provider records. Filesystem validation, artifact handling,
conversation timing and transport item construction stay in the gateway service.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable
import base64
import json

from .omp_content import OmpContentError, OmpContentPager

HISTORY_PREFIX = 'omp-history-v1.'
HISTORY_CONTENT_PREFIX = 'omp-history-content-v1.'


def history_cursor(scope, position, end=None, content=None):
    prefix = HISTORY_CONTENT_PREFIX if content is not None else HISTORY_PREFIX
    fields = [OmpContentPager._scope_version(scope), position, end, content]
    return prefix + base64.urlsafe_b64encode(json.dumps(
        fields, separators=(',', ':')).encode()).decode().rstrip('=')


def parse_history_cursor(cursor, scope):
    try:
        prefix = HISTORY_CONTENT_PREFIX if cursor.startswith(HISTORY_CONTENT_PREFIX) else HISTORY_PREFIX
        if not cursor.startswith(prefix) or len(cursor) > 32768:
            raise ValueError()
        encoded = cursor[len(prefix):]
        fields = json.loads(base64.b64decode(encoded + '=' * (-len(encoded) % 4),
                                            altchars=b'-_', validate=True))
        if not isinstance(fields, list) or len(fields) != 4:
            raise ValueError()
        fence, position, end, content = fields
        if (not isinstance(position, dict) or set(position) != {'cursor', 'skip', 'turns'}
                or not isinstance(position['cursor'], str) or type(position['skip']) is not int
                or type(position['turns']) is not int or not 1 <= position['turns'] <= 100
                or position['skip'] < 0 or end is not None and (type(end) is not int or end < 0)
                or content is not None and not isinstance(content, str)):
            raise ValueError()
    except (ValueError, TypeError, UnicodeError, KeyError) as exc:
        raise OmpContentError('invalid history cursor') from exc
    if fence != OmpContentPager._scope_version(scope):
        raise OmpContentError('stale history cursor', 409)
    return position, end, content


def load_omp_turn_window(fetch, position=None, *, turns=1):
    """Read a bounded user-anchored window, crossing bounded native pages.

    Positions replay a signed native start cursor plus a record offset, never
    a mutable replica index. Oversized record chunks are decoded only here.
    """
    page = fetch(None if position is None else position['cursor'])
    first = page
    turns = turns if position is None else position['turns']
    start = {'cursor': page['start_cursor'], 'skip': 0 if position is None else position['skip'],
             'turns': turns}
    skip = start['skip']
    records = []
    chunks = bytearray()
    chunk_id = None
    pages = 1

    def retain(record):
        message = record.get('message')
        if isinstance(message, dict) or _is_user_record(record):
            records.append(record)
        elif records:
            # The signed native chain proves this exact omitted edge. Collapse
            # metadata immediately, retaining neither its payload nor its IDs.
            records[-1] = {**records[-1], 'parentId': record.get('parentId')}

    while True:
        entries = page.get('entries', [])
        if skip > len(entries):
            raise OmpContentError('stale history cursor', 409)
        for index, record in enumerate(entries[skip:], skip):
            retain(record)
            if _is_user_record(record):
                turns -= 1
                if turns == 0 or pages >= 2:
                    after = ({'cursor': page['start_cursor'], 'skip': index + 1, 'turns': start['turns']}
                             if index + 1 < len(entries) or page.get('entry_chunks') else
                             {'cursor': page['next_cursor'], 'skip': 0, 'turns': start['turns']}
                             if page.get('next_cursor') else None)
                    return first, list(reversed(records)), start, after
        skip = 0
        for chunk in page.get('entry_chunks', []):
            if chunk.get('encoding') != 'base64' or chunk.get('offset') != len(chunks):
                raise OmpContentError('invalid native history chunk', 502)
            if chunk_id is not None and chunk_id != chunk['entry_id']:
                raise OmpContentError('invalid native history chunk', 502)
            chunk_id = chunk['entry_id']
            chunks.extend(base64.b64decode(chunk['data'], validate=True))
            if len(chunks) == chunk['total_bytes']:
                record = json.loads(chunks)
                if record.get('id') != chunk_id:
                    raise OmpContentError('invalid native history chunk', 502)
                retain(record)
                chunks.clear()
                chunk_id = None
                if _is_user_record(record):
                    turns -= 1
                    if turns == 0 or pages >= 2:
                        after = ({'cursor': page['next_cursor'], 'skip': 0, 'turns': start['turns']}
                                 if page.get('next_cursor') else None)
                        return first, list(reversed(records)), start, after
        if not page.get('next_cursor'):
            if chunks:
                raise OmpContentError('incomplete native history chunk', 502)
            return first, list(reversed(records)), start, None
        page = fetch(page['next_cursor'])
        pages += 1
        if any(page.get(key) != first.get(key) for key in ('session_id', 'generation', 'branch_id')):
            raise OmpContentError('stale history cursor', 409)


def _is_user_record(record):
    message = record.get('message') or {}
    return (record.get('type') == 'custom_message' and record.get('customType') == 'collab-prompt'
            or message.get('role') == 'user'
            or message.get('role') == 'custom' and message.get('customType') == 'collab-prompt')


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ''


def _content_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ''
    return '\n\n'.join(
        block['text'] for block in value
        if isinstance(block, dict) and block.get('type') == 'text'
        and isinstance(block.get('text'), str)
    )


def _stop_reason(message: dict) -> str:
    for key in ('stopReason', 'stop_reason', 'finishReason', 'finish_reason'):
        reason = _text(message.get(key)).lower().replace('-', '_')
        if reason:
            return 'tool_use' if reason == 'tooluse' else reason
    return ''


def _blocks(message: dict, role: str, clean_text: Callable[[str], str]) -> list[dict]:
    if role == 'tool':
        block = {'type': 'tool_result', 'text': clean_text(_content_text(message.get('content')))}
        for target, keys in (
            ('call_id', ('toolCallId', 'tool_call_id', 'call_id')),
            ('name', ('toolName', 'name')),
        ):
            value = next((_text(message.get(key)) for key in keys if _text(message.get(key))), '')
            if value:
                block[target] = value
        error = message.get('isError', message.get('is_error'))
        if isinstance(error, bool):
            block['is_error'] = error
        return [block]
    content = message.get('content')
    if isinstance(content, str):
        content = [{'type': 'text', 'text': content}]
    blocks = []
    for source in content if isinstance(content, list) else []:
        if not isinstance(source, dict):
            continue
        kind = source.get('type')
        if kind == 'text':
            text = clean_text(_text(source.get('text')))
            if text:
                blocks.append({'type': 'text', 'text': text})
        elif kind in {'thinking', 'reasoning'}:
            text = _text(source.get('thinking')) or _text(source.get('text'))
            if text:
                blocks.append({'type': 'reasoning', 'text': text})
        elif kind in {'toolCall', 'tool_call', 'tool_use'}:
            block = {'type': 'tool_call'}
            call_id = _text(source.get('id')) or _text(source.get('call_id'))
            name = _text(source.get('name'))
            if call_id:
                block['call_id'] = call_id
            if name:
                block['name'] = name
            if 'arguments' in source:
                block['input'] = source['arguments']
            elif 'input' in source:
                block['input'] = source['input']
            blocks.append(block)
    error = next((_text(message.get(key)) for key in ('errorMessage', 'error_message', 'error')
                  if _text(message.get(key))), '')
    if error:
        blocks.append({'type': 'text', 'text': clean_text(error)})
    return blocks


@dataclass
class OmpTurn:
    turn_id: str
    user: dict | None = None
    entries: list[dict] = field(default_factory=list)
    first_record: dict = field(default_factory=dict)
    last_record: dict = field(default_factory=dict)
    line_number: int = 0
    status: str = 'unknown'
    body: str = ''




def _project_records(records, session_id, latest_id, clean_text):
    chain = []
    visited = set()
    while latest_id and latest_id not in visited and latest_id in records:
        visited.add(latest_id)
        number, record = records[latest_id]
        chain.append((number, record))
        latest_id = _text(record.get('parentId'))
    turns = []
    current = None
    for number, record in reversed(chain):
        message = record.get('message')
        if record.get('type') == 'custom_message' and record.get('customType') == 'collab-prompt':
            message = {'role': 'user', 'content': record.get('content')}
        if not isinstance(message, dict):
            continue
        role = _text(message.get('role')).lower()
        if role == 'custom' and message.get('customType') == 'collab-prompt':
            role = 'user'
        if role in {'toolresult', 'tool_result'}:
            role = 'tool'
        if role not in {'user', 'assistant', 'tool'}:
            continue
        entry = {'id': record['id'], 'role': role, 'blocks': _blocks(message, role, clean_text)}
        if record.get('streaming') is True:
            entry['streaming'] = True
        reason = _stop_reason(message) if role == 'assistant' else ''
        if reason:
            entry['stop_reason'] = reason
        if role == 'user':
            current = OmpTurn(record['id'], user=entry, first_record=record,
                              last_record=record, line_number=number)
            turns.append(current)
            continue
        # Missing a user identity is not evidence of a shared turn.
        turn = current
        if turn is None:
            turn = OmpTurn(record['id'], first_record=record, line_number=number)
            turns.append(turn)
        turn.entries.append(entry)
        turn.last_record = record
        if role == 'assistant':
            has_error = any(_text(message.get(key))
                            for key in ('errorMessage', 'error_message', 'error'))
            answer_block_count = len(entry['blocks']) - int(has_error)
            visible = '\n\n'.join(block['text'] for index, block in enumerate(entry['blocks'])
                                  if index < answer_block_count and block['type'] == 'text')
            if visible:
                turn.body = visible
            if reason in {'error', 'failed', 'failure'} or (not reason and has_error):
                turn.status = 'failed'
            elif reason in {'aborted', 'abort', 'cancelled', 'canceled'}:
                turn.status = 'aborted'
            elif reason == 'tool_use' or any(b['type'] == 'tool_call' for b in entry['blocks']):
                turn.status = 'running'
            elif reason in {'stop', 'end_turn'}:
                turn.status = 'completed'
            else:
                turn.status = 'unknown'
        elif turn.status not in {'failed', 'aborted'}:
            turn.status = 'running'
    return session_id, turns


def project_omp_snapshot(snapshot: dict, *, clean_text: Callable[[str], str]) -> tuple[str, list[OmpTurn]]:
    """Project one complete host replica; never combine different generations."""
    session_id = _text(snapshot.get('session_id'))
    records = {
        record['id']: (number, record)
        for number, record in enumerate(snapshot.get('entries', []), 1)
        if isinstance(record, dict) and _text(record.get('id'))
    }
    # Only the same native owner supplies these metadata-only ancestry links.
    # They bridge records omitted by Collab without guessing by arrival/text.
    for identity, parent in snapshot.get('parent_links', []):
        if isinstance(identity, str) and identity not in records:
            records[identity] = (0, {'id': identity, 'parentId': parent, 'type': 'custom'})
    state = snapshot.get('state') or {}
    leaf = (_text(state.get('leafId')) if 'leafId' in state
            else next(reversed(records), ''))
    streaming = snapshot.get('streaming_message')
    stream_id = _text(snapshot.get('stream_id'))
    has_live_message = False
    if isinstance(streaming, dict) and stream_id:
        durable = (snapshot.get('stream_entry_ids') or {}).get(stream_id)
        if not durable or durable not in records:
            has_live_message = True
            # Stream identity is transport-owned, not derived from mutable text.
            records[stream_id] = (len(records) + 1, {
                'type': 'message', 'id': stream_id, 'parentId': leaf,
                'message': streaming,
                'streaming': True,
            })
            leaf = stream_id
    session_id, turns = _project_records(records, session_id, leaf, clean_text)
    if turns:
        latest = turns[-1]
        # Host isStreaming spans the whole turn (including tools), but an idle
        # host can also mean paused/incomplete work. Completion additionally
        # requires the durable final assistant stop, never just host idleness.
        queued = state.get('queuedMessageCount')
        busy = (state.get('isStreaming') is True or state.get('isAborting') is True
                or isinstance(queued, (int, float)) and queued > 0 or has_live_message)
        if busy:
            latest.status = 'running'
        elif latest.status not in {'failed', 'aborted'}:
            if state.get('isStreaming') is not False or latest.status != 'completed':
                latest.status = 'unknown'
    return session_id, turns
