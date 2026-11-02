"""Read structured OpenCode v1 service snapshots without opening provider storage."""
from __future__ import annotations

import json
import os
import re
import base64
from urllib.parse import quote, urlencode, urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener


class NativeStateError(RuntimeError):
    pass


class NativeRequestRejected(NativeStateError):
    pass


class NativeTransportError(NativeStateError):
    pass


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise NativeStateError('OpenCode native state redirect refused')


class OpenCodeNativeState:
    """Explicit local endpoint; errors never fall back to another state source."""
    def __init__(self, url: str, directory: str, *, password_env: str | None = None):
        parsed = urlsplit(url)
        if (parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', '::1', 'localhost'}
                or parsed.username or parsed.password or parsed.query or parsed.fragment
                or parsed.path not in {'', '/'}):
            raise NativeStateError('OpenCode native state requires an explicit loopback HTTP origin')
        self.url, self.directory = url.rstrip('/'), directory
        self.headers = {}
        if password_env:
            if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', password_env):
                raise NativeStateError('Invalid explicit OpenCode password environment reference')
            password = os.environ.get(password_env)
            if not password:
                raise NativeStateError('Explicit OpenCode password environment reference is unavailable')
            token = base64.b64encode(('opencode:' + password).encode()).decode()
            self.headers['Authorization'] = 'Basic ' + token
        self.parts: dict[str, list[dict]] = {}
        self.opener = build_opener(ProxyHandler({}), _NoRedirect())

    def get(self, path: str, *, query=None):
        return self.request('GET', path, query=query)

    def request(self, method: str, path: str, *, body=None, query=None):
        parameters = {'directory': self.directory, **(query or {})}
        request = Request(self.url + path + '?' + urlencode(parameters), method=method,
                          data=json.dumps(body).encode() if body is not None else None,
                          headers={**self.headers, **({'Content-Type': 'application/json'} if body is not None else {})})
        try:
            with self.opener.open(request, timeout=1) as response:
                data = response.read(16 * 1024 * 1024 + 1)
                if method == 'DELETE' and response.status == 204 and not data:
                    return None
            if len(data) > 16 * 1024 * 1024:
                raise ValueError('snapshot too large')
            return json.loads(data)
        except HTTPError as exc:
            error = NativeRequestRejected if 400 <= exc.code < 500 else NativeTransportError
            raise error('OpenCode native request rejected; no storage fallback') from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise NativeTransportError('OpenCode native transport unavailable; no replay') from exc
        except Exception as exc:
            raise NativeStateError('OpenCode native state unavailable or incompatible; no storage fallback') from exc

    def session(self, session_id: str) -> dict:
        # Exact binding is mandatory: never select the newest session from a
        # shared service, or let a user's session silently replace the CCB task.
        value = self.get('/session/' + quote(session_id, safe=''))
        if (not isinstance(value, dict) or value.get('id') != session_id
                or value.get('directory') != self.directory or not isinstance(value.get('time'), dict)):
            raise NativeStateError('OpenCode native session/directory binding mismatch or unsupported API')
        return {'path': None, 'payload': value}

    def messages(self, session_id: str) -> list[dict]:
        values = self.get('/session/' + quote(session_id, safe='') + '/message')
        if not isinstance(values, list):
            raise NativeStateError('Unsupported OpenCode native message schema')
        messages, parts = [], {}
        for value in values:
            if not isinstance(value, dict):
                raise NativeStateError('Unsupported OpenCode native message schema')
            info, content = value.get('info'), value.get('parts')
            if (not isinstance(info, dict) or info.get('sessionID') != session_id
                    or not isinstance(info.get('id'), str) or not isinstance(content, list)
                    or not all(isinstance(part, dict) and part.get('messageID') == info['id']
                               and part.get('sessionID') == session_id for part in content)):
                raise NativeStateError('OpenCode native message binding mismatch or unsupported API')
            if info.get('role') == 'assistant' and not isinstance(info.get('parentID'), str):
                raise NativeStateError('OpenCode native assistant parent binding unavailable')
            messages.append(info)
            parts[info['id']] = content
        self.parts = parts
        return messages
