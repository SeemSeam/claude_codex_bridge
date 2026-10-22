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

    def page(self, items, *, scope, revision, limit, cursor):
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
                item = self._preview(item, self._cursor(scope, item['id'], version, 0))
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
        # An allowlist prevents giant attachment/unknown fields bypassing budget.
        preview = {key: value[:256] if isinstance(value, str) else value
                   for key, value in item.items()
                   if key in {'id', 'agent', 'session_id', 'kind', 'title', 'format',
                              'source', 'state', 'started_at', 'completed_at', 'duration_ms'}
                   and isinstance(value, (str, int, float, bool, type(None)))}
        native = item.get('native_turn') or {}
        preview.update(body=str(item.get('body') or '')[:512] + '\n\n[Content preview; read original]',
                       attachments=[], native_turn={
                           'schema_version': 1, 'session_id': preview.get('session_id'),
                           'turn_id': str(native.get('turn_id') or '')[:256],
                           'status': str(native.get('status') or '')[:256],
                           'transport': 'collab', 'entries': [],
                           'read_cursor': cursor, 'reason': REASON})
        return preview
