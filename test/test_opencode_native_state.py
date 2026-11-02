import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread

import pytest

from opencode_runtime.native import NativeStateError, OpenCodeNativeState
from provider_backends.opencode.runtime.log_reader_facade import OpenCodeLogReader


@pytest.fixture
def native_server(tmp_path):
    fixtures = {}
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass
        def do_GET(self):
            path = self.path.split('?')[0]
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(fixtures.get(path)).encode())
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{server.server_port}', fixtures
    server.shutdown()
    server.server_close()
    thread.join()


def test_native_snapshot_uses_exact_session_parent_and_completion(native_server, tmp_path, monkeypatch):
    url, fixtures = native_server
    fixtures['/session/ses_native'] = {'id': 'ses_native', 'directory': str(tmp_path), 'time': {'updated': 30}}
    fixtures['/session/ses_native/message'] = [
        {'info': {'id': 'msg_user', 'sessionID': 'ses_native', 'role': 'user', 'time': {'created': 1}},
         'parts': [{'id': 'prt_user', 'messageID': 'msg_user', 'sessionID': 'ses_native',
                    'type': 'text', 'text': 'CCB_REQ_ID: job_native\nReply OK'}]},
        {'info': {'id': 'msg_assistant', 'sessionID': 'ses_native', 'parentID': 'msg_user',
                  'role': 'assistant', 'time': {'created': 2, 'completed': 3}},
         'parts': [{'id': 'prt_reply', 'messageID': 'msg_assistant', 'sessionID': 'ses_native',
                    'type': 'text', 'text': 'OK'}]},
    ]
    reader = OpenCodeLogReader(root=tmp_path / 'absent', work_dir=tmp_path,
                               session_id_filter='ses_native', api_url=url)
    monkeypatch.setattr(reader._storage, 'fetch_opencode_db_rows', lambda *_: pytest.fail('native reader accessed SQLite'))
    state = reader.capture_state()
    assert state['session_id'] == 'ses_native'
    assert state['last_assistant_parent_id'] == 'msg_user'
    assert state['last_assistant_completed'] == 3
    assert state['last_assistant_req_id'] == 'job_native'
    fixtures['/session/ses_native/message'][1]['info']['sessionID'] = 'foreign'
    with pytest.raises(NativeStateError, match='binding mismatch'):
        reader.capture_state()
    fixtures['/session/ses_native']['directory'] = '/foreign'
    with pytest.raises(NativeStateError, match='binding mismatch'):
        reader.capture_state()


def test_native_rejects_missing_binding_and_incompatible_v2_schema(native_server, tmp_path):
    url, fixtures = native_server
    with pytest.raises(NativeStateError, match='exact session'):
        OpenCodeLogReader(work_dir=tmp_path, api_url=url)
    native = OpenCodeNativeState(url, str(tmp_path))
    fixtures['/session/s'] = {'data': {'id': 's', 'location': {'directory': str(tmp_path)}}}
    with pytest.raises(NativeStateError, match='unsupported API'):
        native.session('s')
    fixtures['/session/s/message'] = [{'id': 'a', 'type': 'assistant', 'content': []}]
    with pytest.raises(NativeStateError, match='unsupported API'):
        native.messages('s')


@pytest.mark.parametrize('url', ['http://example.com', 'http://user:secret@localhost', 'https://localhost', 'http://localhost/api'])
def test_native_refuses_nonlocal_or_credential_bearing_origins(url):
    with pytest.raises(NativeStateError):
        OpenCodeNativeState(url, '/workspace')
