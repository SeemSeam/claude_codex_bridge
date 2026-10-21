"""Actual managed OMP TUI idle smoke, zero submitted prompts/model requests.

Run from repository root: PYTHONPATH=lib python test/omp_collab_tui_smoke.py
Optional positional argument selects the omp binary. Uses an isolated HOME and
PTY; never reads or restarts any existing CCB/native session.
"""
from __future__ import annotations

import json
import os
import pty
import select
import shutil
import shlex
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from provider_backends.omp.collab import OmpCollabClient, OmpCollabError
from provider_backends.omp.launcher import _omp_completion_extension_source, _wrap_collab_command


def main():
    omp = shutil.which(sys.argv[1] if len(sys.argv) > 1 else 'omp')
    bun = shutil.which('bun')
    if not omp or not bun:
        raise RuntimeError('Install upstream OMP 18.11 and Bun first')
    with tempfile.TemporaryDirectory(prefix='ccb-tui-') as temporary:
        root = Path(temporary)
        home = root / 'home'
        runtime = root / '.ccb' / 'runtime'
        collab = runtime / 'collab'
        collab.mkdir(parents=True, mode=0o700)
        home.mkdir(mode=0o700)
        extension = root / 'completion.ts'
        source = _omp_completion_extension_source()
        opener = 'export default function ccbOmpCompletion(pi: any): void {'
        registration = r'''
  pi.registerProvider("ccb-local-smoke", {
    baseUrl: "http://127.0.0.1:9/v1", apiKey: "unused-local-smoke-key", api: "openai-completions",
    models: [
      { id: "first", name: "Local smoke first", reasoning: false, input: ["text"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }, contextWindow: 4096, maxTokens: 512 },
      { id: "second", name: "Local smoke second", reasoning: true,
        thinking: { mode: "effort", efforts: ["low", "high"] }, input: ["text"],
        cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 }, contextWindow: 4096, maxTokens: 512 },
    ],
  });
'''
        assert source.count(opener) == 1
        extension.write_text(source.replace(opener, opener + registration, 1))
        editor = root / 'editor.sock'
        endpoint = collab / 'owner.sock'
        overlay = collab / 'config.json'
        binding = collab / 'binding.json'
        binding.write_text(json.dumps({
            'actor': 'smoke', 'launch_session_id': 'isolated-smoke', 'cwd': str(root),
            'socket': str(endpoint), 'overlay': str(overlay), 'editor_socket': str(editor),
            'registry_dir': str(home / '.omp/run/collab-hosts'),
            'attempt_log': str(collab / 'attempts.jsonl'),
        }))
        (root / '.ccb/.omp-smoke-session').write_text(json.dumps({
            'provider': 'omp', 'agent_name': 'smoke', 'ccb_session_id': 'isolated-smoke',
            'runtime_dir': str(runtime), 'start_dir': str(root), 'omp_collab_socket': str(endpoint),
        }))
        # Do not inherit API keys, auth paths, user session directories, or proxies.
        environment = {
            'PATH': os.environ.get('PATH', ''), 'HOME': str(home), 'TERM': 'xterm-256color',
            'LANG': 'C.UTF-8', 'NO_UPDATE_NOTIFIER': '1',
            'PI_CODING_AGENT_DIR': str(home / '.omp/agent'),
            'PI_CODING_AGENT_SESSION_DIR': str(root / 'sessions'),
            'CCB_CALLER_ACTOR': 'smoke', 'CCB_SESSION_ID': 'isolated-smoke',
            'CCB_OMP_COMPLETION_EVENTS': str(root / 'events.jsonl'),
            'CCB_OMP_DISPATCH_EVENTS': str(root / 'dispatch.jsonl'),
            'CCB_OMP_COMPOSER_SOCKET': str(editor),
        }
        master, slave = pty.openpty()
        native_command = shlex.join([
            omp, '--session-dir', str(root / 'sessions'), '--extension', str(extension),
            '--config', str(overlay),
        ])
        rendered = _wrap_collab_command(native_command, {'omp_collab_binding': str(binding)})
        process = subprocess.Popen(['/bin/sh', '-c', 'exec ' + rendered], cwd=root, env=environment,
                                   stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
        os.close(slave)
        client = OmpCollabClient()
        snapshot = None
        try:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and process.poll() is None:
                # Drain the actual TUI's output without exposing prompts, links,
                # environment, or any terminal output in the smoke transcript.
                if select.select([master], [], [], 0.1)[0]:
                    try:
                        os.read(master, 65536)
                    except OSError:
                        break
                try:
                    snapshot = client.get_snapshot(root, 'smoke')
                    if snapshot.get('ready') and snapshot.get('connected'):
                        break
                except OmpCollabError:
                    pass
            assert snapshot and snapshot.get('ready') and snapshot.get('connected'), 'Actual upstream TUI Collab did not become ready'
            assert snapshot['session_id'], 'Missing actual native session identity'
            assert snapshot['state'].get('isStreaming') is False
            assert not (collab / 'attempts.jsonl').exists(), 'Smoke must never submit a model prompt'
            models = client.get_models(root, 'smoke')
            rows = [row for row in models['models'] if row['provider'] == 'ccb-local-smoke']
            assert {row['id'] for row in rows} == {'first', 'second'}
            assert 'unused-local-smoke-key' not in json.dumps(models)
            for identity, thinking in [('second', 'high'), ('first', 'off')]:
                changed = client.set_model(
                    root, 'smoke', {'provider': 'ccb-local-smoke', 'id': identity}, thinking,
                    expected_session_id=models['session_id'], expected_generation=models['generation'],
                    expected_model=models['model'], expected_thinking=models['thinking_level'],
                    expected_model_revision=models['model_revision'], idempotency_key=f'smoke-{identity}',
                )
                assert changed['model'] == {'provider': 'ccb-local-smoke', 'id': identity}
                assert changed['thinking_level'] == thinking
                models = client.get_models(root, 'smoke')
                assert models['model'] == changed['model']
            assert not (collab / 'attempts.jsonl').exists(), 'Model selection must not submit a prompt'
        finally:
            client.close_all()
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)
            os.close(master)
        assert not endpoint.exists(), 'Supervisor did not clean up its Unix bridge'
        assert not overlay.exists(), 'Supervisor did not clean up relay overlay'
        print('actual OMP TUI smoke passed: PTY, encrypted snapshot, native catalog, second/high then first/off model readback, shutdown; zero prompts')


if __name__ == '__main__':
    main()
