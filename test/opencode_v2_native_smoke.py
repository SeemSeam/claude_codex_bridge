"""Opt-in real OpenCode v2 task smoke with an isolated service and local model.

Run: python test/opencode_v2_native_smoke.py --opencode /pinned/v2/opencode
      [--app-root /installed/ccb]
No existing Provider home, configuration or credentials are inherited.
"""
from __future__ import annotations

import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace


class ModelHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *_args):
        pass
    def do_POST(self):
        try:
            size = int(self.headers.get('Content-Length', '0'))
            assert 0 < size < 4 * 1024 * 1024
            body = json.loads(self.rfile.read(size))
            assert body.get('model') == 'ccb-local'
            self.server.requests.append(body)
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Connection', 'close')
            self.end_headers()
            def send(delta, finish=None):
                value = {'id': 'ccb-fixture', 'object': 'chat.completion.chunk', 'created': int(time.time()),
                         'model': 'ccb-local', 'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
                self.wfile.write(('data: ' + json.dumps(value) + '\n\n').encode())
                self.wfile.flush()
            send({'role': 'assistant'})
            send({'content': 'LOCAL_V2_'})
            self.server.streaming.set()
            if not self.server.release.wait(20):
                raise TimeoutError('Smoke did not release local stream')
            send({'content': 'OK'})
            send({}, 'stop')
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()
            self.close_connection = True
        except Exception as error:
            self.server.errors.append(type(error).__name__)
            self.close_connection = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--opencode', required=True)
    args = parser.parse_args()
    app_root = args.app_root.resolve()
    sys.path.insert(0, str(app_root / 'lib'))
    from completion.models import CompletionStatus, utc_now_iso
    from opencode_runtime.native_v2 import OpenCodeV2State
    from provider_backends.opencode.execution_runtime import native_v2 as execution
    from provider_backends.opencode.execution_runtime.polling import poll_submission
    assert Path(execution.__file__).resolve().is_relative_to(app_root)
    executable = shutil.which(args.opencode)
    assert executable, 'Pinned OpenCode v2 executable required'
    model = ThreadingHTTPServer(('127.0.0.1', 0), ModelHandler)
    model.daemon_threads = True
    model.streaming, model.release = threading.Event(), threading.Event()
    model.requests, model.errors = [], []
    worker = threading.Thread(target=model.serve_forever, daemon=True)
    worker.start()
    with tempfile.TemporaryDirectory(prefix='ccb-opencode-v2-native-') as temporary:
        root = Path(temporary)
        env = {'PATH': os.environ.get('PATH', ''), 'OPENCODE_DISABLE_AUTOUPDATE': 'true',
               'OPENCODE_PASSWORD': 'ccb-local-fixture-not-a-secret'}
        for key, name in [('HOME', 'home'), ('XDG_DATA_HOME', 'data'), ('XDG_CONFIG_HOME', 'config'),
                          ('XDG_STATE_HOME', 'state'), ('XDG_CACHE_HOME', 'cache')]:
            (root / name).mkdir(mode=0o700)
            env[key] = str(root / name)
        (root / 'opencode.json').write_text(json.dumps({'providers': {'ccb_local': {
            'package': 'aisdk:@ai-sdk/openai-compatible',
            'settings': {'apiKey': 'ccb-local-fixture-not-a-secret', 'baseURL': f'http://127.0.0.1:{model.server_port}/v1'},
            'models': {'ccb-local': {'limit': {'context': 32768, 'output': 512}}},
        }}, 'model': 'ccb_local/ccb-local'}))
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        with (root / 'server.log').open('w') as log:
            process = subprocess.Popen([executable, 'serve', '--hostname', '127.0.0.1', '--port', str(port)],
                                       cwd=root, env=env, stdout=log, stderr=log)
            # Only this explicit fixture reference is made available to the CCB client.
            previous = os.environ.get('CCB_SMOKE_V2_PASSWORD')
            os.environ['CCB_SMOKE_V2_PASSWORD'] = env['OPENCODE_PASSWORD']
            try:
                client = OpenCodeV2State(f'http://127.0.0.1:{port}', str(root), password_env='CCB_SMOKE_V2_PASSWORD')
                deadline = time.monotonic() + 25
                while True:
                    try:
                        if client.get('/api/info').get('version') == '2.2.4':
                            break
                    except Exception:
                        pass
                    if process.poll() is not None or time.monotonic() > deadline:
                        raise RuntimeError('Isolated pinned v2 service failed to start')
                    time.sleep(0.05)
                response = client.request('POST', '/api/session', body={'location': {'directory': str(root)},
                    'model': {'providerID': 'ccb_local', 'id': 'ccb-local'}})
                sid = response['data']['id']
                client.session(sid)
                prepared = SimpleNamespace(session=SimpleNamespace(work_dir=str(root)), backend=object(), pane_id='fixture')
                submission = execution.start(SimpleNamespace(job_id='job_v2smoke', agent_name='fixture'), prepared,
                    api_url=client.url, session_id=sid, prompt='CCB_REQ_ID: job_v2smoke\nReply LOCAL_V2_OK',
                    now=utc_now_iso(), password_env='CCB_SMOKE_V2_PASSWORD')
                assert submission.runtime_state['admission'] == 'accepted'
                seen_running = False
                streaming_since = None
                while True:
                    result = poll_submission(submission, now=utc_now_iso(), state_session_path_fn=lambda _: None)
                    submission = result.submission
                    if result.decision:
                        if result.decision.status is not CompletionStatus.COMPLETED:
                            entries = client.timeline(sid, submission.runtime_state['native_input_id'])
                            print('Fixture timeline:', [(entry['type'], entry.get('description'), entry.get('metadata'), entry.get('text', '')[:300], entry.get('finish'), entry.get('error'), entry.get('outcome'), entry.get('content')) for entry in entries], flush=True)
                        assert result.decision.status is CompletionStatus.COMPLETED, (result.decision.reason, result.decision.diagnostics)
                        assert result.decision.reply == 'LOCAL_V2_OK', result.decision.reply
                        assert result.decision.diagnostics['native_idle_id']
                        break
                    if model.streaming.is_set() and not model.release.is_set():
                        assert result.decision is None
                        if streaming_since is None:
                            streaming_since = time.monotonic()
                        if time.monotonic() - streaming_since >= 0.3:
                            seen_running = True
                            queued = execution.start(SimpleNamespace(job_id='job_cancel', agent_name='fixture'), prepared,
                                api_url=client.url, session_id=sid, prompt='CCB_REQ_ID: job_cancel\nDo not execute',
                                now=utc_now_iso(), password_env='CCB_SMOKE_V2_PASSWORD')
                            execution.cancel(queued)
                            inbox = client.get('/api/session/' + sid + '/inbox')['data']
                            assert all(entry['id'] != queued.runtime_state['native_input_id'] for entry in inbox)
                            model.release.set()
                    if time.monotonic() > deadline:
                        raise TimeoutError('Native v2 task failed to settle: ' + str(model.errors))
                    time.sleep(0.05)
                assert seen_running and model.requests and not model.errors, (seen_running, len(model.requests), model.errors)
                print('PASS real OpenCode 2.2.4: native queued admission, exact input binding, held live stream, queued input cancellation, no early completion, native idle success; local model only')
            finally:
                model.release.set()
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                if previous is None:
                    os.environ.pop('CCB_SMOKE_V2_PASSWORD', None)
                else:
                    os.environ['CCB_SMOKE_V2_PASSWORD'] = previous
                model.shutdown()
                model.server_close()
                worker.join(timeout=1)


if __name__ == '__main__':
    main()
