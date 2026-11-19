"""Linux native OMP resize smoke. Run with PYTHONPATH pointing at the app's lib."""
import http.server
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile
import threading
import time
from types import SimpleNamespace

from provider_backends.omp.launcher import build_runtime_launcher


class Model(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_args):
        pass

    def do_POST(self):
        assert self.path == '/v1/chat/completions'
        request = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        assert request['stream'] and request['model'] == 'resize'
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Cache-Control', 'no-cache')
        self.send_header('Connection', 'close')
        self.send_header('Transfer-Encoding', 'chunked')
        self.end_headers()
        def send(data):
            self.wfile.write(f'{len(data):X}\r\n'.encode() + data + b'\r\n')
            self.wfile.flush()

        for delta, finish in [({'role': 'assistant'}, None),
                              ({'content': 'STREAM-BEGIN ' + 'wrap-test ' * 24 + '\n\n'}, None),
                              ({'content': ' STREAM-END'}, None), ({}, 'stop')]:
            if delta.get('content') == ' STREAM-END':
                assert self.server.release.wait(25), 'stream was not released'
            chunk = dict(id='local-resize', object='chat.completion.chunk', created=int(time.time()), model='resize',
                         choices=[dict(index=0, delta=delta, finish_reason=finish)])
            send(('data: ' + json.dumps(chunk) + '\n\n').encode())
        send(b'data: [DONE]\n\n')
        self.wfile.write(b'0\r\n\r\n')
        self.wfile.flush()
        self.close_connection = True


def until(check, message):
    deadline = time.monotonic() + 6
    while time.monotonic() < deadline:
        if check():
            return
        time.sleep(0.05)
    raise AssertionError(message)


def smoke(root, model):
    env = dict(PATH=os.environ['PATH'], HOME=str(root), TERM='xterm-256color', LANG='C.UTF-8',
               OMP_SKIP_SETUP='1', NO_UPDATE_NOTIFIER='1')
    extension = root / 'model.ts'
    sizes = root / 'sizes'
    extension.write_text(f'''import {{ appendFileSync }} from "node:fs";
export default function(pi) {{
  pi.registerProvider('ccb-local-resize', {{ baseUrl: 'http://127.0.0.1:{model.server_port}/v1',
    apiKey: 'local-only', api: 'openai-completions',
    models: [{{ id: 'resize', name: 'Resize', reasoning: false, input: ['text'],
      cost: {{ input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }}, contextWindow: 131072, maxTokens: 8192 }}] }});
  process.stdout.on('resize', () => appendFileSync({json.dumps(str(sizes))},
    `${{process.stdout.columns}}x${{process.stdout.rows}}\\n`));
}}
''')
    state = dict(workspace_path=str(root), omp_home=str(root / 'home'), omp_state_dir=str(root / 'state'))
    config = root / 'fixture.json'
    config.write_text(json.dumps({'recap': {'enabled': False}}))
    spec = SimpleNamespace(name='smoke', startup_args=(
        '--extension', str(extension), '--model', 'ccb-local-resize/resize',
        '--config', str(config), '--no-skills',
    ), env={}, provider_command_template=None)
    command = build_runtime_launcher().build_start_cmd(SimpleNamespace(restore=False), spec, root / 'runtime',
                                                       'resize-smoke', state)
    tmux_config = root / 'tmux.conf'
    tmux_config.write_text('set -g status off\n')
    base = ['tmux', '-S', str(root / 'tmux.sock'), '-f', str(tmux_config)]

    def tm(*args):
        return subprocess.check_output([*base, *args], env=env, cwd=root, text=True).strip()

    def capture(history=False):
        return tm('capture-pane', '-p', '-t', '%0', *(['-S', '-'] if history else []))

    def resize(width, height, action=None):
        before = sizes.read_text().splitlines() if sizes.exists() else []
        if action:
            tm(*action)
        else:
            tm('resize-window', '-t', 'probe', '-x', str(width), '-y', str(height))
        until(lambda: sizes.exists() and f'{width}x{height}' in sizes.read_text().splitlines()[len(before):],
              f'OMP did not receive resize to {width}x{height}')

    def wrap_rows():
        screen = capture()
        if screen.count('wrap-test') != 24:
            return 0
        return sum('wrap-test' in row for row in screen.splitlines())

    def assert_stream_frame():
        screen = capture()
        assert screen.count('collab:2') == 1, screen
        assert screen.count('Working…') == 1, screen
        assert screen.count('STREAM-BEGIN') == 1, screen
        assert capture(True).count('STREAM-BEGIN') == 1

    try:
        tm('new-session', '-d', '-s', 'probe', '-x', '100', '-y', '32',
           'exec /bin/sh -c ' + shlex.quote(command))
        tm('set-option', 'remain-on-exit', 'on')
        until(lambda: 'Resize' in capture() or 'ccb-local-resize' in capture(), 'OMP model not ready')
        resize(72, 30)
        resize(120, 34)
        tm('send-keys', '-t', '%0', 'Isolated resize probe', 'Enter')
        until(lambda: wrap_rows() and 'Working…' in capture(), 'fixture response did not stream')
        wide_rows = wrap_rows()
        resize(66, 28)
        until(lambda: wrap_rows() > wide_rows, 'Streamed text did not reflow on shrink')
        assert_stream_frame()
        resize(110, 34)
        until(lambda: 0 < wrap_rows() <= wide_rows, 'Streamed text did not reflow on widen')
        assert_stream_frame()
        tm('split-window', '-h', '-d', '-t', '%0', 'sleep 30')
        width, height = map(int, tm('display-message', '-pt', '%0', '#{pane_width} #{pane_height}').split())
        resize(110, 34, ('resize-pane', '-Z', '-t', '%0'))
        resize(width, height, ('resize-pane', '-Z', '-t', '%0'))
        until(lambda: wrap_rows() > wide_rows, 'Streamed text did not reflow on unzoom')
        assert_stream_frame()
        model.release.set()
        until(lambda: 'STREAM-END' in capture(), 'fixture response did not finish')
        assert capture(True).count('STREAM-END') == 1
        tm('send-keys', '-t', '%0', 'C-d')
        until(lambda: not Path(state['omp_collab_socket']).exists(), 'bridge socket not cleaned up')
        assert not Path(state['omp_collab_overlay']).exists()
    except Exception:
        print(capture())
        raise
    finally:
        model.release.set()
        subprocess.run([*base, 'kill-server'], env=env, capture_output=True)
        until(lambda: not Path(state['omp_collab_socket']).exists(), 'bridge did not stop after tmux exit')
    print('Native OMP passed: idle + streaming resize, zoom/unzoom, single transcript, clean shutdown')


if __name__ == '__main__':
    assert shutil.which('tmux') and shutil.which('omp') and shutil.which('bun')
    with tempfile.TemporaryDirectory(prefix='ccb-resize-') as tmp, \
            http.server.ThreadingHTTPServer(('127.0.0.1', 0), Model) as model:
        model.release = threading.Event()
        threading.Thread(target=model.serve_forever, daemon=True).start()
        try:
            smoke(Path(tmp), model)
        finally:
            model.shutdown()
