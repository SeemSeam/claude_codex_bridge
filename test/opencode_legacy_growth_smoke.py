"""Opt-in real 1.19.5 Git/session smoke with local model and safe legacy reader.

Run: python test/opencode_legacy_growth_smoke.py [--app-root /installed/ccb]
All Provider homes/data are disposable; no cloud model or user auth is used.
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
from urllib.parse import urlencode
from urllib.request import Request, build_opener, ProxyHandler


class ModelHandler(BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'
    def log_message(self, *_):
        pass
    def do_POST(self):
        try:
            size = int(self.headers.get('Content-Length', '0'))
            assert 0 < size < 8 * 1024 * 1024
            body = json.loads(self.rfile.read(size))
            self.server.requests += 1
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Connection', 'close')
            self.end_headers()
            for delta, finish in [({'role': 'assistant'}, None), ({'content': 'LOCAL_LEGACY_OK'}, None), ({}, 'stop')]:
                payload = {'id': 'fixture', 'object': 'chat.completion.chunk', 'created': int(time.time()),
                           'model': body['model'], 'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
                self.wfile.write(('data: ' + json.dumps(payload) + '\n\n').encode())
                self.wfile.flush()
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()
        except Exception as error:
            self.server.errors.append(type(error).__name__)
        finally:
            self.close_connection = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--opencode', default='opencode')
    parser.add_argument('--jobs', type=int, default=20)
    args = parser.parse_args()
    app_root = args.app_root.resolve()
    sys.path.insert(0, str(app_root / 'lib'))
    from opencode_runtime.git_excludes import ensure_runtime_excludes
    import opencode_runtime.storage as storage
    from provider_backends.opencode.comm import OpenCodeLogReader
    assert Path(storage.__file__).resolve().is_relative_to(app_root)
    binary = shutil.which(args.opencode)
    assert binary
    assert subprocess.check_output([binary, '--version'], text=True).strip() == '1.19.5'
    model = ThreadingHTTPServer(('127.0.0.1', 0), ModelHandler)
    model.daemon_threads = True
    model.requests, model.errors = 0, []
    worker = threading.Thread(target=model.serve_forever, daemon=True)
    worker.start()
    with tempfile.TemporaryDirectory(prefix='ccb-opencode-growth-') as temporary:
        root = Path(temporary)
        state = root / '.ccb/agents/fixture/provider-state/opencode'
        state.mkdir(parents=True)
        env = {'PATH': os.environ.get('PATH', ''), 'OPENCODE_DISABLE_AUTOUPDATE': 'true', 'OPENCODE_DISABLE_MODELS_FETCH': 'true'}
        for key, name in [('HOME', 'home'), ('XDG_DATA_HOME', 'data'), ('XDG_CONFIG_HOME', 'config'),
                          ('XDG_STATE_HOME', 'state'), ('XDG_CACHE_HOME', 'cache')]:
            (state / name).mkdir(mode=0o700)
            env[key] = str(state / name)
        config = {'snapshot': True, 'provider': {'ccb_local': {
            'npm': '@ai-sdk/openai-compatible', 'name': 'CCB local fixture',
            'options': {'apiKey': 'ccb-local-fixture-not-a-secret', 'baseURL': f'http://127.0.0.1:{model.server_port}/v1'},
            'models': {'ccb-local': {'name': 'CCB local', 'limit': {'context': 32768, 'output': 512}}},
        }}, 'model': 'ccb_local/ccb-local', 'small_model': 'ccb_local/ccb-local'}
        (root / 'opencode.json').write_text(json.dumps(config))
        (root / 'source.txt').write_text('fixture source\n')
        def git(*args):
            subprocess.run(['git', '-C', str(root), *args], env=env, check=True, capture_output=True)
        git('init')
        assert ensure_runtime_excludes(root) == ()
        git('add', 'source.txt', 'opencode.json')
        git('-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'fixture')
        with socket.socket() as probe:
            probe.bind(('127.0.0.1', 0))
            port = probe.getsockname()[1]
        origin = f'http://127.0.0.1:{port}'
        opener = build_opener(ProxyHandler({}))
        def api(path, data=None):
            request = Request(origin + path + ('' if path == '/global/health' else '?' + urlencode({'directory': str(root)})),
                              data=json.dumps(data).encode() if data is not None else None,
                              headers={'Content-Type': 'application/json'})
            with opener.open(request, timeout=1 if path == '/global/health' else 30) as response:
                return json.load(response)
        with (state / 'server.log').open('w') as log:
            process = subprocess.Popen([binary, '--print-logs', '--log-level', 'INFO', 'serve', '--hostname', '127.0.0.1', '--port', str(port)],
                                       cwd=root, env=env, stdout=log, stderr=log)
            try:
                deadline = time.monotonic() + 25
                while True:
                    try:
                        api('/global/health')
                        break
                    except Exception:
                        if process.poll() is not None or time.monotonic() > deadline:
                            raise RuntimeError('isolated v1 startup failed')
                        time.sleep(.05)
                sid = api('/session', {})['id']
                reader = OpenCodeLogReader(root=state / 'data/opencode/storage', work_dir=root,
                                           project_id='fixture', session_id_filter=sid)
                reader._force_read_interval = .2
                observed = reader.capture_state()
                sizes, latencies = [], []
                for index in range(args.jobs):
                    job = f'job_growth{index:02d}'
                    (state / 'changing-runtime.log').write_text('runtime fixture ' + job)
                    started = time.monotonic()
                    api('/session/' + sid + '/message', {'model': {'providerID': 'ccb_local', 'modelID': 'ccb-local'},
                        'parts': [{'type': 'text', 'text': 'CCB_REQ_ID: ' + job + '\nReply LOCAL_LEGACY_OK'}]})
                    original = storage._copy_bounded
                    if index == 7:
                        def fail(*a, **k):
                            raise storage.OpenCodeObservationUnavailable('snapshot_changed')
                        storage._copy_bounded = fail
                        try:
                            text, failed = reader.try_get_message(observed)
                            assert text is None and failed['observation_error'] == 'snapshot_changed'
                            assert failed['session_updated'] == observed['session_updated']
                            observed = failed
                        finally:
                            storage._copy_bounded = original
                        reader._observation_retry_at = 0
                    deadline = time.monotonic() + 10
                    while True:
                        text, observed = reader.try_get_message(observed)
                        if text and observed.get('last_assistant_req_id') == job and observed.get('last_assistant_completed') is not None:
                            assert text == 'LOCAL_LEGACY_OK'
                            break
                        if time.monotonic() > deadline:
                            print('Fixture observation:', observed, flush=True)
                            print('Fixture messages:', reader._read_messages(sid), flush=True)
                            for message in reader._read_messages(sid):
                                print('Fixture parts:', reader._read_parts(message['id']), flush=True)
                            raise RuntimeError('legacy reply did not recover: ' + str(observed.get('observation_error')))
                        time.sleep(.05)
                    assert reader.try_get_message(observed)[0] is None
                    db = reader._storage.resolve_opencode_db_path()
                    sizes.append(sum(path.stat().st_size for path in [db, Path(str(db) + '-wal')] if path.exists()))
                    shm = Path(str(db) + '-shm')
                    assert not shm.exists() or shm.stat().st_size >= 32768
                    latencies.append(round(time.monotonic() - started, 3))
                    print(f'job {index + 1}/{args.jobs}: completed; DB+WAL={sizes[-1]} bytes; {latencies[-1]}s', flush=True)
                summaries = reader._storage.fetch_opencode_db_rows("SELECT json_extract(data, '$.summary.diffs') AS diffs FROM message WHERE session_id=?", (sid,))
                for row in summaries:
                    for diff in json.loads(row['diffs'] or '[]'):
                        assert '.ccb/agents/' not in diff.get('file', ''), 'volatile runtime entered native summary'
                assert max(sizes) < 16 * 1024 * 1024, sizes
                assert process.poll() is None and not model.errors
                print(f'PASS real OpenCode 1.19.5 Git smoke: {args.jobs}/{args.jobs} exact legacy replies; local model; copy-failure recovery; no runtime diffs; max DB+WAL={max(sizes)} bytes; latency={min(latencies)}..{max(latencies)}s', flush=True)
            except Exception:
                print('Isolated fixture server log:', (state / 'server.log').read_text()[-6000:], flush=True)
                raise
            finally:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                model.shutdown()
                model.server_close()
                worker.join(timeout=1)


if __name__ == '__main__':
    main()
