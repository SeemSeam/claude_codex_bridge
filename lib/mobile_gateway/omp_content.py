"""Bounded OMP history and immutable, readable semantic content pages.

The complete projected item's canonical JSON supplies only the version hash.
Content pages expose prose and execution blocks, never that JSON envelope.
"""
from __future__ import annotations

import base64
from bisect import bisect_right
from collections import OrderedDict
import hashlib
import hmac
import secrets
import json
import threading
import re

PAGE_BYTES = 192 * 1024
OUTER_RESERVE = 16 * 1024
INLINE_ITEM_BYTES = 24 * 1024
CONTENT_BYTES = 32 * 1024
PREFIX = 'omp-content-v2.'
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
        self._cursor_key = secrets.token_bytes(32)

    def _document(self, item, scope, revision):
        # Collab revisions fence immutable snapshots. Cache bounded semantic
        # documents so Next need not serialize/hash an entire tool turn again.
        key = (*scope, revision, item['id'])
        with self._lock:
            cached = self._documents.get(key)
            if cached is not None:
                self._documents.move_to_end(key)
                return cached
        data = json.dumps(item, ensure_ascii=False, sort_keys=True,
                          separators=(',', ':')).encode('utf-8')
        blocks = self._semantic_blocks(item)
        for block in blocks:
            block['_fences'] = self._fence_contexts(block['text'])
        # Canonical JSON is only a hash input, not retained or sent to readers.
        # Include source size in the conservative charge for Python text storage.
        weight = len(data) + sum(len(block['text'].encode('utf-8')) for block in blocks)
        document = (blocks, hashlib.sha256(data).hexdigest(), weight)
        with self._lock:
            if weight <= 32 * 1024 * 1024:
                previous = self._documents.pop(key, None)
                if previous is not None:
                    self._bytes -= previous[2]
                while self._documents and self._bytes + weight > 32 * 1024 * 1024:
                    self._bytes -= self._documents.popitem(last=False)[1][2]
                self._documents[key] = document
                self._bytes += weight
        return document

    @staticmethod
    def _scope_version(scope):
        return hashlib.sha256(json.dumps(scope, ensure_ascii=True,
                                        separators=(',', ':')).encode('ascii')).hexdigest()

    def _cursor(self, scope, item_id, version, block_index, offset=0, page_index=0):
        raw = json.dumps([self._scope_version(scope), item_id, version,
                          block_index, offset, page_index],
                         ensure_ascii=True, separators=(',', ':')).encode('ascii')
        signature = hmac.digest(self._cursor_key, raw, 'sha256')
        return PREFIX + base64.urlsafe_b64encode(signature + raw).decode('ascii').rstrip('=')

    @staticmethod
    def _semantic_blocks(item):
        native = item.get('native_turn') or {}
        if item.get('kind') == 'user_message' or not native:
            return [{'entry_id': item['id'], 'block_index': 0,
                     'role': 'user' if item.get('kind') == 'user_message' else 'assistant',
                     'type': 'text', 'text': str(item.get('body') or '')}]
        prose, execution = [], []
        for entry_index, entry in enumerate(native.get('entries') or []):
            for block_index, source in enumerate(entry.get('blocks') or []):
                kind = source.get('type')
                if kind not in {'text', 'reasoning', 'tool_call', 'tool_result'}:
                    continue
                value = source.get('input', '') if kind == 'tool_call' else source.get('text', '')
                text = (json.dumps(value, ensure_ascii=False, indent=2)
                        if kind == 'tool_call' and not isinstance(value, str)
                        else str(value or ''))
                block = {'entry_id': entry.get('id') or f"{item['id']}:{entry_index}",
                         'block_index': block_index, 'role': entry.get('role') or 'assistant',
                         'type': kind, 'text': text}
                block.update({key: source[key] for key in ('name', 'call_id', 'is_error')
                              if key in source})
                (prose if block['role'] == 'assistant' and kind == 'text'
                 else execution).append(block)
        return prose + execution or [{'entry_id': item['id'], 'block_index': 0,
                                     'role': 'assistant', 'type': 'text',
                                     'text': str(item.get('body') or '')}]

    @staticmethod
    def _fence_contexts(text):
        transitions = []
        active = None
        for match in re.finditer(r'^ {0,3}(`{3,}|~{3,})([^\n]*)\n?', text, re.MULTILINE):
            delimiter, info = match.group(1), match.group(2)
            if active is None:
                if delimiter[0] == '`' and '`' in info:
                    continue
                language = re.match(r'\s*([A-Za-z0-9_+.-]{1,64})(?=\s|$)', info)
                render_delimiter = delimiter[:64]
                active = (render_delimiter,
                          render_delimiter + (language.group(1) if language else ''),
                          len(delimiter))
                # Mark the opening line from its start: it may itself span
                # several pages. Only bounded render context is repeated.
                transitions.append((match.start(1), active))
                continue
            elif (delimiter[0] == active[0][0] and len(delimiter) >= active[2]
                  and not info.strip()):
                active = None
            else:
                continue
            transitions.append((match.end(), active))
        return transitions

    @staticmethod
    def _render_context(block, offset, end):
        transitions = block['_fences']
        # Tuple search only compares offsets: state can be None or a tuple.
        positions = block.get('_fence_positions')
        if positions is None:
            positions = block['_fence_positions'] = [position for position, _ in transitions]
        def active_at(position):
            index = bisect_right(positions, position) - 1
            # At an opening boundary the preceding fragment has no fence,
            # and the next fragment already contains the real opener. Closing
            # boundaries, by contrast, have already consumed the real closer.
            if (index >= 0 and positions[index] == position
                    and transitions[index][1] is not None):
                index -= 1
            return transitions[index][1] if index >= 0 else None
        before, after = active_at(offset), active_at(end)
        return ((before[1] + '\n') if offset and before else '',
                ('\n' + after[0]) if end < len(block['text']) and after else '')

    @staticmethod
    def _fragment_end(text, offset, budget):
        # At most budget code points are inspected; UTF-8 boundaries stay intact.
        candidate = text[offset:offset + budget]
        encoded = candidate.encode('utf-8')
        end = offset + len(encoded[:budget].decode('utf-8', errors='ignore'))
        if end < len(text):
            fragment = text[offset:end]
            # Prefer substantial paragraphs, then lines. Continuation flags let
            # readers identify fragments inside a Markdown code fence explicitly.
            for separator in ('\n\n', '\n'):
                boundary = fragment.rfind(separator)
                if boundary >= len(fragment) // 2:
                    return offset + boundary + len(separator)
        return end

    def content(self, items, *, scope, revision, cursor):
        try:
            if not isinstance(cursor, str) or not cursor.startswith(PREFIX) or len(cursor) > 4096:
                raise ValueError()
            encoded = cursor[len(PREFIX):]
            signed = base64.b64decode(encoded + '=' * (-len(encoded) % 4),
                                      altchars=b'-_', validate=True)
            signature, raw = signed[:32], signed[32:]
            if not hmac.compare_digest(signature, hmac.digest(self._cursor_key, raw, 'sha256')):
                raise ValueError()
            fields = json.loads(raw)
            if not isinstance(fields, list) or len(fields) != 6:
                raise ValueError()
            scope_version, item_id, version, index, offset, page_index = fields
            if (not isinstance(item_id, str) or not isinstance(version, str) or len(version) != 64
                    or any(type(value) is not int or value < 0
                           for value in (index, offset, page_index))):
                raise ValueError()
        except (ValueError, TypeError, UnicodeError) as exc:
            raise OmpContentError('invalid content cursor') from exc
        if scope_version != self._scope_version(scope):
            raise OmpContentError('stale content cursor', 409)
        item = next((item for item in items if item['id'] == item_id), None)
        if item is None:
            raise OmpContentError('stale content cursor', 409)
        blocks, actual_version, _ = self._document(item, scope, revision)
        if version != actual_version:
            raise OmpContentError('stale content cursor', 409)
        if index >= len(blocks) or offset > len(blocks[index]['text']):
            raise OmpContentError('invalid content offset')
        page = {'schema_version': 2, 'version': version, 'blocks': [],
                'page_index': page_index, 'next_cursor': None, 'total_blocks': len(blocks)}
        remaining = CONTENT_BYTES
        while index < len(blocks):
            block = blocks[index]
            text = block['text']
            end = self._fragment_end(text, offset, remaining)
            if end == offset and offset < len(text):
                break
            while True:
                prefix, suffix = self._render_context(block, offset, end)
                fragment = {key: value for key, value in block.items() if not key.startswith('_')}
                fragment.update(text=text[offset:end], continued_before=offset > 0,
                                continued_after=end < len(text),
                                render_prefix=prefix, render_suffix=suffix)
                next_index, next_offset = (index, end) if end < len(text) else (index + 1, 0)
                next_cursor = (self._cursor(scope, item_id, version, next_index, next_offset, page_index + 1)
                               if next_index < len(blocks) else None)
                candidate = {**page, 'blocks': [*page['blocks'], fragment],
                             'next_cursor': next_cursor}
                if ascii_size(candidate) + OUTER_RESERVE <= PAGE_BYTES:
                    break
                if page['blocks']:
                    return page
                if end - offset <= 1:
                    raise OmpContentError('content block metadata exceeds page budget', 502)
                end = offset + (end - offset) // 2
            page = candidate
            remaining -= len(fragment['text'].encode('utf-8'))
            index, offset = next_index, next_offset
            # A source block has one identity in the UI. Continue it on the
            # next page instead of emitting duplicate identities on this page.
            if fragment['continued_after'] or not remaining:
                break
        return page

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
                _, version, _ = self._document(item, scope, revision)
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
