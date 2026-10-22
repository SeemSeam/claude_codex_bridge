"""Actual OMP PTY reply admission/receipt smoke using only a loopback provider.

Run: python test/omp_reply_native_smoke.py [--app-root /installed/ccb] [--omp omp]
The selected app root supplies the generated production extension and bridge.
No credentials, existing projects, or existing native sessions are inherited.
This is deliberately an opt-in integration smoke, not a pytest test.
"""
from __future__ import annotations

import argparse
import hashlib
import http.server
import importlib
import json
import os
import pty
import select
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path


ACTOR = 'native-receipt-smoke'
LAUNCH = 'isolated-native-receipt-smoke'
ANCHOR = 'native-receipt-original'
DRAFT = 'UNSUBMITTED native receipt smoke draft'


def digest(text):
    return hashlib.sha256(text.encode()).hexdigest()


def records(path):
    if not path.exists():
        return []
    # Native append can be observed between write boundaries. Ignore only the
    # incomplete final record, never malformed completed records.
    lines = path.read_text().splitlines(keepends=True)
    return [json.loads(line) for line in lines if line.endswith('\n')]


class LocalProvider(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self):
        super().__init__(('127.0.0.1', 0), ProviderHandler)
        self.requests = []
        self.errors = []
        self.next_response = threading.Event()
        self.second_request = threading.Event()
        self.lock = threading.Lock()


class ProviderHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = 'HTTP/1.1'

    def log_message(self, *_args):
        pass

    def do_POST(self):
        try:
            assert self.path == '/v1/chat/completions', self.path
            size = int(self.headers.get('Content-Length', '0'))
            assert 0 < size <= 4 * 1024 * 1024, 'Invalid local request size'
            body = json.loads(self.rfile.read(size))
            assert body.get('stream') is True, 'OMP did not use actual streaming API'
            assert body.get('model') == 'native-receipt-local'
            with self.server.lock:
                self.server.requests.append(body)
                number = len(self.server.requests)
            if number > 1:
                self.server.second_request.set()
                if not self.server.next_response.wait(45):
                    raise TimeoutError('Smoke did not release delayed model response')
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Cache-Control', 'no-cache')
            self.send_header('Connection', 'close')
            self.end_headers()

            def chunk(delta, finish=None):
                data = {'id': f'local-{number}', 'object': 'chat.completion.chunk',
                        'created': int(time.time()), 'model': 'native-receipt-local',
                        'choices': [{'index': 0, 'delta': delta, 'finish_reason': finish}]}
                self.wfile.write(('data: ' + json.dumps(data) + '\n\n').encode())
                self.wfile.flush()

            chunk({'role': 'assistant'})
            if number == 1:
                tools = body.get('tools', [])
                assert any(tool.get('function', {}).get('name') == 'ccb_smoke_hold' for tool in tools), ('Native registered hold tool missing: ' + repr([tool.get('function', {}).get('name') for tool in tools]))
                chunk({'tool_calls': [{'index': 0, 'id': 'local-hold-1', 'type': 'function',
                                      'function': {'name': 'ccb_smoke_hold', 'arguments': '{}'}}]})
                chunk({}, 'tool_calls')
            else:
                chunk({'content': 'LOCAL ORIGINAL RUN SETTLED'})
                chunk({}, 'stop')
            self.wfile.write(b'data: [DONE]\n\n')
            self.wfile.flush()
            self.close_connection = True
        except (BrokenPipeError, ConnectionResetError):
            self.close_connection = True
        except Exception as error:
            self.server.errors.append(f'{type(error).__name__}: {error}')
            self.close_connection = True


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app-root', type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument('--omp', default='omp')
    args = parser.parse_args()
    app_root = args.app_root.resolve()
    if not (app_root / 'lib/provider_backends/omp/launcher.py').is_file():
        raise RuntimeError('--app-root must contain installed lib/provider_backends/omp/launcher.py')
    sys.path.insert(0, str(app_root / 'lib'))
    launcher = importlib.import_module('provider_backends.omp.launcher')
    collab_module = importlib.import_module('provider_backends.omp.collab')
    receipt_module = importlib.import_module('provider_backends.omp.reply_delivery')
    execution_module = importlib.import_module('provider_execution.base')
    completion_module = importlib.import_module('completion.models')
    # Fail closed if a runner has already imported another installation.
    assert Path(launcher.__file__).resolve().is_relative_to(app_root), 'Wrong app-root imported'
    omp = shutil.which(args.omp)
    if not omp or not shutil.which('bun'):
        raise RuntimeError('Install upstream OMP and Bun first')
    started = time.monotonic()
    deadline = started + 53  # reserve shutdown time inside the 60-second bound

    def stage(name):
        print(f'{time.monotonic() - started:6.2f}s {name}', flush=True)

    with tempfile.TemporaryDirectory(prefix='ccb-native-reply-') as temporary:
        root = Path(temporary)
        home = root / 'home'
        home.mkdir(mode=0o700)
        agent_home = home / '.omp/agent'
        agent_home.mkdir(parents=True, mode=0o700)
        (agent_home / 'config.yml').write_text('startup:\n  setupWizard: false\n  checkUpdate: false\n')
        runtime = root / '.ccb/runtime'
        collab = runtime / 'collab'
        collab.mkdir(parents=True, mode=0o700)
        editor = root / 'editor.sock'
        endpoint = collab / 'owner.sock'
        overlay = collab / 'config.json'
        binding = collab / 'binding.json'
        events = root / 'events.jsonl'
        dispatch = root / 'dispatch.jsonl'
        observation = root / 'observation.json'
        held = root / 'held'
        release = root / 'release'
        binding.write_text(json.dumps({
            'actor': ACTOR, 'launch_session_id': LAUNCH, 'cwd': str(root),
            'socket': str(endpoint), 'overlay': str(overlay), 'editor_socket': str(editor),
            'registry_dir': str(home / '.omp/run/collab-hosts'),
            'attempt_log': str(collab / 'attempts.jsonl'),
        }))
        (root / '.ccb/.omp-native-receipt-smoke-session').write_text(json.dumps({
            'provider': 'omp', 'agent_name': ACTOR, 'ccb_session_id': LAUNCH,
            'runtime_dir': str(runtime), 'start_dir': str(root), 'omp_collab_socket': str(endpoint),
        }))
        server = LocalProvider()
        server_thread = threading.Thread(target=server.serve_forever, daemon=True)
        server_thread.start()
        source = launcher._omp_completion_extension_source()
        opener = 'export default function ccbOmpCompletion(pi: any): void {'
        assert source.count(opener) == 1, 'Installed extension entrypoint incompatible'
        # This adds only a real provider, real hold tool, and passive native
        # observation. The production reply bridge is not changed or mocked.
        registration = '''
  pi.registerProvider("ccb-native-receipt-local", {
    baseUrl: LOCAL_URL, apiKey: "loopback-only-not-a-secret", api: "openai-completions",
    models: [{ id: "native-receipt-local", name: "Loopback receipt smoke", reasoning: false,
      input: ["text"], cost: { input: 0, output: 0, cacheRead: 0, cacheWrite: 0 },
      contextWindow: 32768, maxTokens: 512 }],
  });
  let smokeContext: any = null;
  let smokeTimer: ReturnType<typeof setInterval> | null = null;
  pi.on("session_start", async (_event: any, ctx: any) => {
    smokeContext = ctx;
    smokeTimer = setInterval(() => {
      try {
        const value = { idle: smokeContext.isIdle(),
          draft_sha256: createHash("sha256").update(smokeContext.ui.getEditorText()).digest("hex"),
          session_id: smokeContext.sessionManager.getSessionId(),
          session_file: smokeContext.sessionManager.getSessionFile() };
        smokeWriteFile(OBSERVATION + ".tmp", JSON.stringify(value));
        smokeRename(OBSERVATION + ".tmp", OBSERVATION);
      } catch (error) {
        smokeWriteFile(OBSERVATION + ".error", String(error));
      }
    }, 40);
    smokeTimer.unref();
  });
  pi.on("session_shutdown", async () => { if (smokeTimer) clearInterval(smokeTimer); });
  pi.registerTool({ name: "ccb_smoke_hold", label: "Local smoke hold",
    loadMode: "essential",
    description: "Wait for the isolated smoke controller", parameters: pi.zod.object({}),
    async execute(_id: any, _params: any, _update: any, _ctx: any, abort: any) {
      smokeWriteFile(HELD, "held");
      const until = Date.now() + 45000;
      while (!smokeExists(RELEASE)) {
        if (abort?.aborted || Date.now() > until) throw new Error("Local smoke hold timed out/aborted");
        await new Promise(resolve => setTimeout(resolve, 30));
      }
      return { content: [{ type: "text", text: "Local smoke hold released" }], details: {} };
    },
  });
'''
        for token, value in {'LOCAL_URL': f'http://127.0.0.1:{server.server_port}/v1',
                             'OBSERVATION': str(observation), 'HELD': str(held), 'RELEASE': str(release)}.items():
            registration = registration.replace(token, json.dumps(value))
        extension = root / 'completion.ts'
        extension.write_text('import { writeFileSync as smokeWriteFile, renameSync as smokeRename, existsSync as smokeExists } from "node:fs";\n' + source.replace(opener, opener + registration, 1))
        environment = {
            'PATH': os.environ.get('PATH', ''), 'HOME': str(home), 'TERM': 'xterm-256color',
            'LANG': 'C.UTF-8', 'NO_UPDATE_NOTIFIER': '1',
            'PI_CODING_AGENT_DIR': str(home / '.omp/agent'),
            'PI_CODING_AGENT_SESSION_DIR': str(root / 'sessions'),
            'CCB_CALLER_ACTOR': ACTOR, 'CCB_SESSION_ID': LAUNCH,
            'CCB_OMP_COMPLETION_EVENTS': str(events), 'CCB_OMP_DISPATCH_EVENTS': str(dispatch),
            'CCB_OMP_COMPOSER_SOCKET': str(editor),
        }
        master, slave = pty.openpty()
        process = None
        client = collab_module.OmpCollabClient(timeout=0.6)
        last_stage = 'startup'
        terminal = bytearray()

        def drain():
            if select.select([master], [], [], 0.02)[0]:
                try:
                    terminal.extend(os.read(master, 65536))
                    del terminal[:-12000]
                except OSError:
                    pass

        def wait_for(name, check):
            nonlocal last_stage
            last_stage = name
            while time.monotonic() < deadline:
                drain()
                if server.errors:
                    raise AssertionError('Local provider failed: ' + '; '.join(server.errors))
                if process.poll() is not None:
                    raise AssertionError(f'Actual OMP exited during {name}: {process.returncode}')
                result = check()
                if result:
                    stage(name)
                    return result
                time.sleep(0.02)
            raise TimeoutError(f'60s smoke deadline approaching during {name}')

        def observe():
            if observation.with_suffix('.json.error').exists():
                raise AssertionError('Native observation API incompatible: ' + observation.with_suffix('.json.error').read_text())
            return json.loads(observation.read_text()) if observation.exists() else {}

        def snapshot_ready():
            try:
                result = client.get_snapshot(root, ACTOR)
                return result if result.get('ready') and result.get('connected') else None
            except collab_module.OmpCollabError:
                return None

        def rpc(operation, **fields):
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
                connection.settimeout(min(0.8, max(0.05, deadline - time.monotonic())))
                connection.connect(str(editor))
                connection.sendall((json.dumps(dict(operation=operation, actor=ACTOR,
                                                    launch_session_id=LAUNCH, **fields)) + '\n').encode())
                data = bytearray()
                while b'\n' not in data:
                    part = connection.recv(4096)
                    assert part and len(data) < 65536, 'Native reply bridge disconnected/oversized'
                    data.extend(part)
                return json.loads(data.split(b'\n', 1)[0])

        try:
            command = shlex.join([omp, '--session-dir', str(root / 'sessions'),
                                  '--extension', str(extension), '--config', str(overlay),
                                  '--approval-mode', 'yolo'])
            rendered = launcher._wrap_collab_command(command, {'omp_collab_binding': str(binding)})
            process = subprocess.Popen(['/bin/sh', '-c', 'exec ' + rendered], cwd=root, env=environment,
                                       stdin=slave, stdout=slave, stderr=slave, start_new_session=True)
            os.close(slave)
            slave = None
            snapshot = wait_for('actual PTY and installed bridge ready', snapshot_ready)
            models = client.get_models(root, ACTOR)
            client.set_model(root, ACTOR, {'provider': 'ccb-native-receipt-local', 'id': 'native-receipt-local'}, 'off',
                             expected_session_id=models['session_id'], expected_generation=models['generation'],
                             expected_model=models['model'], expected_thinking=models['thinking_level'],
                             expected_model_revision=models['model_revision'], idempotency_key='local-model')
            capability = rpc('reply_capabilities')
            assert capability.get('reply_ingress_version') == 1, f'Native reply API unavailable: {capability}'
            fence = {key: capability[key] for key in ('runtime_instance_id', 'session_id')}
            dispatch_id = '0123456789abcdef0123456789abcdef'
            prompt = f'CCB_REQ_ID: {ANCHOR}\nCCB_DISPATCH_ID: {dispatch_id}\nRun the isolated local hold tool, then finish.'
            dispatch.write_text(json.dumps(dict(schema_version=1, actor=ACTOR, launch_session_id=LAUNCH,
                                                runtime_instance_id=fence['runtime_instance_id'], req_id=ANCHOR,
                                                dispatch_id=dispatch_id, prompt_sha256=digest(prompt))) + '\n')
            snapshot = client.get_snapshot(root, ACTOR)
            client.submit(root, ACTOR, prompt, 'single-seed', expected_session_id=snapshot['session_id'],
                          expected_generation=snapshot['generation'])
            wait_for('real streamed tool call holding native turn', lambda: held.exists() and observe().get('idle') is False)
            os.write(master, DRAFT.encode())
            wait_for('unsubmitted draft observed during native turn', lambda: observe().get('draft_sha256') == digest(DRAFT))
            deliveries = [('native-reply-one', 'First isolated native reply'),
                          ('native-reply-two', 'Second isolated native reply')]
            for delivery_id, body in deliveries:
                assert not release.exists() and observe().get('idle') is False
                before = time.monotonic()
                answer = rpc('reply_deliver', **fence, delivery_id=delivery_id, body=body)
                assert answer.get('state') in {'pending', 'received'}, f'Native admission failed: {answer}'
                assert answer.get('body_sha256') == digest(body)
                assert time.monotonic() - before < 1, 'Admission waited for held business turn'
                stage(f'{delivery_id} admitted while actual native tool busy')
            assert not any(row['type'] == 'agent_settled' and row.get('req_id') == ANCHOR for row in records(events))
            release.write_text('release')
            wait_for('original run reached delayed next model response', server.second_request.is_set)

            def receipts_ready():
                for delivery_id, body in deliveries:
                    answer = rpc('reply_status', **fence, delivery_id=delivery_id, body_sha256=digest(body))
                    assert answer.get('state') in {'pending', 'received'}, f'Native receipt failed: {answer}'
                    if answer['state'] != 'received':
                        return False
                return True

            wait_for('both native persisted receipts received before original run ends', receipts_ready)
            for delivery_id, body in deliveries:
                state = dict(draft_guard_socket=str(editor), actor=ACTOR, launch_session_id=LAUNCH,
                             reply_receipt_transport=True, runtime_instance_id=fence['runtime_instance_id'],
                             reply_receipt_session_id=fence['session_id'], reply_body_sha256=digest(body))
                submission = execution_module.ProviderSubmission(
                    job_id=delivery_id, agent_name=ACTOR, provider='omp',
                    accepted_at=completion_module.utc_now_iso(), ready_at=completion_module.utc_now_iso(),
                    source_kind=completion_module.CompletionSourceKind.SESSION_EVENT_LOG,
                    reply='', runtime_state=state)
                consumed = receipt_module.poll_receipt(submission, state, now=completion_module.utc_now_iso())
                assert isinstance(consumed, execution_module.ProviderPollResult), 'Python adapter did not consume native receipt'
                assert consumed.decision and consumed.decision.terminal
                assert consumed.decision.reason == 'omp_reply_received'
                assert consumed.decision.diagnostics['business_processing_complete'] is False
            stage('Python adapter consumed both native receipts while original run still busy')
            assert not server.next_response.is_set()
            assert observe().get('idle') is False
            assert observe().get('draft_sha256') == digest(DRAFT), 'Reply delivery touched composer draft'
            session_file = Path(observe()['session_file'])
            assert session_file.resolve().is_relative_to(root), 'Native session escaped isolated root'

            def assert_native_entries():
                native = records(session_file)
                for delivery_id, body in deliveries:
                    matches = [entry for entry in native if entry.get('type') == 'custom_message'
                               and entry.get('customType') == 'ccb-reply'
                               and entry.get('details', {}).get('delivery_id') == delivery_id]
                    assert len(matches) == 1, f'{delivery_id}: expected exactly one actual on-disk native custom entry'
                    assert matches[0]['content'] == body
                    assert matches[0]['details']['body_sha256'] == digest(body)
                # Native Collab persists submitted prompts as user-attributed
                # collab-prompt custom messages rather than role:user records.
                users = [entry for entry in native if
                         (entry.get('type') == 'message' and entry.get('message', {}).get('role') == 'user')
                         or (entry.get('type') == 'custom_message' and entry.get('attribution') == 'user')]
                assert len(users) == 1, 'Reply ingress submitted/superseded a native user prompt'
                assert users[0].get('customType') == 'collab-prompt' and users[0].get('content') == prompt

            assert_native_entries()
            # Same IDs must be harmless re-admissions, not duplicate native entries.
            for delivery_id, body in deliveries:
                answer = rpc('reply_deliver', **fence, delivery_id=delivery_id, body=body)
                assert answer.get('state') == 'received', answer
            assert_native_entries()
            before_finish = records(events)
            assert not any(row['type'] in {'request_superseded', 'binding_error'} for row in before_finish)
            starts = [row for row in before_finish if row['type'] == 'request_start']
            assert len(starts) == 1 and starts[0]['req_id'] == ANCHOR and starts[0]['dispatch_matched']
            assert not any(row['type'] == 'agent_settled' and row.get('req_id') == ANCHOR for row in before_finish)
            stage('disk exactly-once entries, original anchor and draft preserved')
            server.next_response.set()
            wait_for('original anchored terminal eventually settled', lambda: next((row for row in records(events)
                     if row['type'] == 'agent_settled' and row.get('req_id') == ANCHOR
                     and 'LOCAL ORIGINAL RUN SETTLED' in (row.get('assistant') or {}).get('text', '')), None))
            wait_for('native TUI idle with identical unsubmitted draft', lambda: observe().get('idle') is True
                     and observe().get('draft_sha256') == digest(DRAFT))
            assert_native_entries()
            final_events = records(events)
            assert not any(row['type'] in {'request_superseded', 'binding_error'} for row in final_events)
            assert len([row for row in final_events if row['type'] == 'request_start']) == 1
            attempts = records(collab / 'attempts.jsonl')
            assert len(attempts) == 1, f'Expected one seed prompt admission, got {len(attempts)}'
            assert len(server.requests) >= 2 and not server.errors
            stage('PASS actual OMP: two busy admissions, durable native receipts, no supersession, draft intact')
        except Exception:
            print(f'{time.monotonic() - started:6.2f}s FAIL stage={last_stage}', file=sys.stderr, flush=True)
            import re
            evidence = {
                'provider_request_count': len(server.requests), 'provider_errors': server.errors,
                'events': records(events)[-12:],
                'attempts': records(collab / 'attempts.jsonl'),
                'observation': observe(),
                'terminal': re.sub(r'\x1b(?:\[[0-?]*[ -/]*[@-~]|\][^\x07]*(?:\x07|\x1b\\))', '',
                                   terminal.decode('utf-8', errors='replace'))[-6000:],
            }
            rendered_evidence = json.dumps(evidence, ensure_ascii=True)
            rendered_evidence = rendered_evidence.replace(str(root), '<isolated-root>')
            rendered_evidence = rendered_evidence.replace('loopback-only-not-a-secret', '<local-key>')
            print(rendered_evidence, file=sys.stderr, flush=True)
            raise
        finally:
            server.next_response.set()
            release.write_text('cleanup')
            client.close_all()
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGTERM)
                try:
                    process.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    os.killpg(process.pid, signal.SIGKILL)
                    process.wait(timeout=1)
            if slave is not None:
                os.close(slave)
            os.close(master)
            server.shutdown()
            server.server_close()
            server_thread.join(timeout=1)


if __name__ == '__main__':
    main()
