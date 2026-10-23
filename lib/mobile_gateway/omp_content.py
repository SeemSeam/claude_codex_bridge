"""Bounded OMP inline history and explicit, fenced original-content pages.

The original is sorted compact UTF-8 JSON of the complete projected item. Its
SHA-256 is the immutable version; cursor offsets always refer to those bytes.
Only Collab-backed OMP uses this policy, not legacy/native-file providers.
"""
from __future__ import annotations

import base64
from collections import OrderedDict
import hashlib
import json
import threading

PAGE_BYTES = 192 * 1024
OUTER_RESERVE = 16 * 1024
INLINE_ITEM_BYTES = 24 * 1024
CONTENT_BYTES = 32 * 1024
PREFIX = 'omp-content-v1.'
REASON = 'Content exceeds inline budget; read original'


def ascii_size(value):
    return len(json.dumps(value, ensure_ascii=True, separators=(',', ':')).encode('ascii'))


class OmpContentError(ValueError):
    def __init__(self, message, status_code=400):
        super().__init__(message)
        self.status_code = status_code


class OmpContentPager:
    def __init__(self):
        self._documents = OrderedDict()
        self._bytes = 0
        self._lock = threading.Lock()

    def _document(self, item, scope, revision):
        # Collab revisions fence immutable snapshots. Keep bounded serialized
        # originals so Next does not serialize/hash an entire tool turn again.
        key = (*scope, revision, item['id'])
        with self._lock:
            cached = self._documents.get(key)
            if cached is not None:
                self._documents.move_to_end(key)
                return cached
        data = json.dumps(item, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':')).encode('utf-8')
        document = (data, hashlib.sha256(data).hexdigest())
        with self._lock:
            if len(data) <= 32 * 1024 * 1024:
                while self._documents and self._bytes + len(data) > 32 * 1024 * 1024:
                    self._bytes -= len(self._documents.popitem(last=False)[1][0])
                previous = self._documents.pop(key, None)
                if previous is not None:
                    self._bytes -= len(previous[0])
                self._documents[key] = document
                self._bytes += len(data)
        return document

    @staticmethod
    def _scope_version(scope):
        return hashlib.sha256(json.dumps(scope, ensure_ascii=True,
                                        separators=(',', ':')).encode('ascii')).hexdigest()

    @staticmethod
    def _cursor(scope, item_id, version, offset):
        raw = json.dumps([OmpContentPager._scope_version(scope), item_id, version, offset],
                         ensure_ascii=True, separators=(',', ':')).encode('ascii')
        return PREFIX + base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')

    def content(self, items, *, scope, revision, cursor):
        try:
            encoded = cursor[len(PREFIX):]
            if not cursor.startswith(PREFIX) or not encoded or len(cursor) > 4096:
                raise ValueError()
            raw = base64.b64decode(encoded + '=' * (-len(encoded) % 4),
                                   altchars=b'-_', validate=True)
            fields = json.loads(raw)
            if not isinstance(fields, list) or len(fields) != 4:
                raise ValueError()
            item_id, version, offset = fields[-3:]
            if (not isinstance(item_id, str) or not isinstance(version, str)
                    or len(version) != 64 or type(offset) is not int or offset < 0):
                raise ValueError()
        except (ValueError, TypeError, UnicodeError) as exc:
            raise OmpContentError('invalid content cursor') from exc
        if fields[0] != self._scope_version(scope):
            raise OmpContentError('stale content cursor', 409)
        item = next((item for item in items if item['id'] == item_id), None)
        if item is None:
            raise OmpContentError('stale content cursor', 409)
        data, actual_version = self._document(item, scope, revision)
        if version != actual_version:
            raise OmpContentError('stale content cursor', 409)
        if offset > len(data) or offset < len(data) and data[offset] & 0xc0 == 0x80:
            raise OmpContentError('invalid content offset')
        end = min(len(data), offset + CONTENT_BYTES)
        while end < len(data) and data[end] & 0xc0 == 0x80:
            end -= 1
        while True:
            page = {'text': data[offset:end].decode('utf-8'), 'offset': offset,
                    'total_bytes': len(data), 'version': actual_version,
                    'next_cursor': self._cursor(scope, item_id, version, end)
                    if end < len(data) else None}
            if ascii_size(page) + OUTER_RESERVE <= PAGE_BYTES:
                return page
            end = offset + (end - offset) // 2
            while end < len(data) and data[end] & 0xc0 == 0x80:
                end -= 1

    def page(self, items, *, scope, revision, limit, cursor, wrap_content_cursor=None):
        try:
            end = len(items) if cursor is None else int(cursor)
            if end < 0 or end > len(items):
                raise ValueError()
        except (TypeError, ValueError) as exc:
            raise OmpContentError('invalid history cursor') from exc
        selected = []
        used = OUTER_RESERVE
        start = end
        for index in range(end - 1, max(-1, end - limit - 1), -1):
            item = items[index]
            # iterencode stops at the inline threshold instead of materializing
            # another aggregate copy of a multi-megabyte native_turn.
            size = 0
            for chunk in json.JSONEncoder(ensure_ascii=True, separators=(',', ':')).iterencode(item):
                size += len(chunk)
                if size > INLINE_ITEM_BYTES:
                    break
            if size > INLINE_ITEM_BYTES:
                _, version = self._document(item, scope, revision)
                read_cursor = self._cursor(scope, item['id'], version, 0)
                if wrap_content_cursor is not None:
                    read_cursor = wrap_content_cursor(read_cursor)
                item = self._preview(item, read_cursor)
                size = ascii_size(item)
            if used + size + 1 > PAGE_BYTES:
                break
            selected.append(item)
            used += size + 1
            start = index
        return {'items': list(reversed(selected)),
                'next_cursor': str(start) if start > 0 else None}

    @staticmethod
    def _preview(item, cursor):
        """Project a recent readable window; the cursor still names the full item.

        Measure the final ASCII JSON, including the caller's wrapped cursor.
        Entry/block identity is never shortened: an entry whose metadata cannot
        fit is omitted instead. Text is always a tail except tool input, whose
        JSON prefix keeps argument names readable without copying a giant dict.
        """
        preview = {key: value[:256] if isinstance(value, str) and key != 'id' else value
                   for key, value in item.items()
                   if key in {'id', 'agent', 'session_id', 'kind', 'title', 'format',
                              'source', 'state', 'started_at', 'completed_at', 'duration_ms',
                              'queue_position', 'delivery_reason'}
                   and isinstance(value, (str, int, float, bool, type(None)))}
        preview.update(body='', attachments=[])

        def fit_text(target, key, text, budget=INLINE_ITEM_BYTES, *, tail=True):
            target[key] = ''
            if ascii_size(preview) > budget:
                return False
            low, high = 0, len(text)
            while low < high:
                middle = (low + high + 1) // 2
                target[key] = text[-middle:] if tail else text[:middle]
                if ascii_size(preview) <= budget:
                    low = middle
                else:
                    high = middle - 1
            target[key] = (text[-low:] if tail else text[:low]) if low else ''
            return True

        native = item.get('native_turn')
        if not native or item.get('kind') == 'user_message':
            preview.update(read_cursor=cursor, read_reason=REASON)
            if not fit_text(preview, 'body', str(item.get('body') or '')[-8000:]):
                raise OmpContentError('original content cursor exceeds inline budget', 502)
            return preview

        window = {key: native[key] for key in
                  ('schema_version', 'session_id', 'turn_id', 'status', 'transport',
                   'stop_reason', 'streaming') if key in native}
        window.update(inline_window=True, entries=[], read_cursor=cursor,
                      reason='Recent inline window; earlier entries or long text omitted. '
                             'Read original for complete content.')
        preview['native_turn'] = window
        if ascii_size(preview) > INLINE_ITEM_BYTES:
            raise OmpContentError('original content cursor exceeds inline budget', 502)

        entries = native.get('entries') or []
        # Find prose independently of the process window: a long stream of tool
        # events must not push the latest assistant answer out of the bubble.
        primary = None
        recent = []
        for entry_index in range(len(entries) - 1, -1, -1):
            entry = entries[entry_index]
            blocks = entry.get('blocks') or []
            for block_index in range(len(blocks) - 1, -1, -1):
                block = blocks[block_index]
                position = (entry_index, block_index)
                if len(recent) < 24:
                    recent.append(position)
                if (primary is None and entry.get('role') == 'assistant'
                        and block.get('type') == 'text' and block.get('text')):
                    primary = position
            if primary is not None and len(recent) == 24:
                break

        selected = {}
        chosen_blocks = {}
        positions = ([primary] if primary is not None else [])
        positions += [position for position in recent if position != primary]
        for position in positions:
            if len(chosen_blocks) == 24:
                break
            entry_index, block_index = position
            if entry_index not in selected and len(selected) == 8:
                continue
            before_block = ascii_size(preview)
            entry = entries[entry_index]
            source = entry['blocks'][block_index]
            block = {key: source[key] for key in
                     ('type', 'call_id', 'name', 'is_error') if key in source}
            field = 'input' if source.get('type') == 'tool_call' else 'text'
            value = source.get(field, '')
            if field == 'input' and not isinstance(value, str):
                chunks = []
                length = 0
                for chunk in json.JSONEncoder(ensure_ascii=False).iterencode(value):
                    chunks.append(chunk[:1500 - length])
                    length += len(chunks[-1])
                    if length >= 1500:
                        break
                text = ''.join(chunks)
            else:
                text = str(value or '')[-(8000 if position == primary else 1500):]
            added_entry = entry_index not in selected
            if added_entry:
                selected[entry_index] = {key: entry[key] for key in
                                         ('id', 'role', 'status', 'stop_reason', 'streaming')
                                         if key in entry}
            chosen_blocks[position] = block

            def arrange():
                for index, projected in selected.items():
                    projected['blocks'] = [chosen_blocks[key] for key in sorted(chosen_blocks)
                                           if key[0] == index]
                window['entries'] = [selected[index] for index in sorted(selected)]

            arrange()
            # Leave room for recent tool context even with heavily escaped prose.
            budget = (INLINE_ITEM_BYTES - 10000 if position == primary and len(positions) > 1
                      else min(INLINE_ITEM_BYTES, before_block + 3000))
            if not fit_text(block, field, text, budget, tail=field != 'input'):
                del chosen_blocks[position]
                if added_entry:
                    del selected[entry_index]
                arrange()
        return preview
