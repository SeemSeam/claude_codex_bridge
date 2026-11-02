"""OpenCode 2.2 native timeline authority, without synthetic v1 parent links."""
from __future__ import annotations

import hashlib
from urllib.parse import quote

from .native import NativeStateError, OpenCodeNativeState


class OpenCodeV2State(OpenCodeNativeState):
    def session(self, session_id: str) -> dict:
        health = self.get('/api/info')
        info = health if isinstance(health, dict) else None
        if not isinstance(info, dict) or not str(info.get('version', '')).startswith('2.2.'):
            raise NativeStateError('Unsupported OpenCode v2 service version (requires 2.2.x)')
        result = self.get('/api/session/' + quote(session_id, safe=''))
        value = result.get('data') if isinstance(result, dict) else None
        if (not isinstance(value, dict) or value.get('id') != session_id
                or not isinstance(value.get('location'), dict)
                or value['location'].get('directory') != self.directory
                or not isinstance(value.get('time'), dict)):
            raise NativeStateError('OpenCode v2 session/workspace binding mismatch')
        return value

    def submit(self, session_id: str, *, input_id: str, job_id: str, prompt: str):
        return self.request('POST', '/api/session/' + quote(session_id, safe='') + '/prompt', body={
            'id': input_id, 'text': prompt, 'delivery': 'queue',
            'metadata': {'ccb_job_id': job_id, 'ccb_prompt_sha256': hashlib.sha256(prompt.encode()).hexdigest()},
        })

    def timeline(self, session_id: str, input_id: str) -> list[dict]:
        path = '/api/session/' + quote(session_id, safe='') + '/message'
        query = {'order': 'desc', 'limit': 200}
        records, ids, cursors, budget = [], set(), set(), 0
        for _ in range(64):
            page = self.get(path, query=query)
            if (not isinstance(page, dict) or not isinstance(page.get('data'), list)
                    or not isinstance(page.get('cursor'), dict)):
                raise NativeStateError('Unsupported OpenCode v2 timeline envelope')
            for item in page['data']:
                if (not isinstance(item, dict) or not isinstance(item.get('id'), str)
                        or item['id'] in ids or not isinstance(item.get('time'), dict)
                        or not isinstance(item.get('type'), str)):
                    raise NativeStateError('Invalid or duplicate OpenCode v2 timeline message')
                ids.add(item['id'])
                # Bound memory and scan work independently from per-page limits.
                budget += len(str(item).encode())
                if budget > 32 * 1024 * 1024:
                    raise NativeStateError('OpenCode v2 timeline byte budget exceeded')
                records.append(item)
                if item['id'] == input_id:
                    return list(reversed(records))
            cursor = page['cursor'].get('next')
            if cursor is None:
                return []  # The exact input is still queued or not projected.
            if not isinstance(cursor, str) or not cursor or cursor in cursors or not page['data']:
                raise NativeStateError('Invalid OpenCode v2 timeline cursor')
            cursors.add(cursor)
            query = {'cursor': cursor, 'limit': 200}
        raise NativeStateError('OpenCode v2 timeline page budget exceeded')

    def observe(self, session_id: str, *, input_id: str, job_id: str, prompt_hash: str) -> dict:
        self.session(session_id)
        records = self.timeline(session_id, input_id)
        if not records:
            inbox = self.get('/api/session/' + quote(session_id, safe='') + '/inbox')
            entries = inbox.get('data') if isinstance(inbox, dict) else None
            if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
                raise NativeStateError('Unsupported OpenCode v2 inbox envelope')
            matches = [entry for entry in entries if entry.get('id') == input_id]
            if len(matches) > 1:
                raise NativeStateError('Duplicate OpenCode v2 inbox input')
            if matches:
                item = matches[0]
                payload = item.get('payload')
                if (item.get('sessionID') != session_id or item.get('type') != 'user'
                        or not isinstance(payload, dict) or not isinstance(payload.get('text'), str)
                        or hashlib.sha256(payload['text'].encode()).hexdigest() != prompt_hash
                        or not isinstance(payload.get('metadata'), dict)
                        or payload['metadata'].get('ccb_job_id') != job_id
                        or payload['metadata'].get('ccb_prompt_sha256') != prompt_hash):
                    raise NativeStateError('OpenCode v2 queued input binding mismatch')
            return {'state': 'pending', 'reply': '', 'anchor_seen': False, 'queued': bool(matches)}
        user = records[0]
        if (user['type'] != 'user' or not isinstance(user.get('metadata'), dict)
                or user['metadata'].get('ccb_job_id') != job_id
                or user.get('metadata', {}).get('ccb_prompt_sha256') != prompt_hash
                or not isinstance(user.get('text'), str)
                or hashlib.sha256(user['text'].encode()).hexdigest() != prompt_hash):
            raise NativeStateError('OpenCode v2 admitted input binding mismatch')
        reply, assistant, terminal, unsettled = '', None, None, False
        for item in records[1:]:
            kind = item['type']
            if kind == 'idle':
                terminal = item
                break
            if kind in {'user', 'synthetic', 'shell', 'skill', 'location-switched'}:
                raise NativeStateError('OpenCode v2 input superseded or coalesced with foreign work')
            if kind == 'compaction' and item.get('status') == 'running':
                unsettled = True
            if kind in {'agent-switched', 'model-switched', 'system', 'compaction'}:
                continue
            if kind != 'assistant' or not isinstance(item.get('content'), list):
                raise NativeStateError('Unsupported OpenCode v2 timeline message type')
            assistant = item
            blocks = item['content']
            if not all(isinstance(block, dict) and block.get('type') in {'text', 'reasoning', 'tool'} for block in blocks):
                raise NativeStateError('Unsupported OpenCode v2 assistant content')
            text = '\n'.join(block['text'] for block in blocks if block['type'] == 'text' and isinstance(block.get('text'), str)).strip()
            reply = text
            unsettled |= any(block['type'] == 'tool' and
                             (not isinstance(block.get('state'), dict) or
                              block['state'].get('status') not in {'completed', 'error'}) for block in blocks)
        observed = {'state': 'running', 'reply': reply, 'anchor_seen': True,
                    'assistant_id': assistant['id'] if assistant else None}
        if terminal is None:
            return observed
        outcome = terminal.get('outcome')
        if outcome not in {'succeeded', 'failed', 'interrupted'}:
            raise NativeStateError('Unsupported OpenCode v2 idle outcome')
        observed.update(state=outcome, idle_id=terminal['id'])
        if outcome == 'succeeded':
            if (not assistant or assistant.get('time', {}).get('completed') is None
                    or assistant.get('finish') != 'stop' or not reply or unsettled
                    or any(block['type'] == 'tool' and block.get('state', {}).get('status') in {'streaming', 'running'}
                           for block in assistant['content'])):
                observed['state'] = 'incomplete'
        return observed
