#!/usr/bin/env python3
"""Isolated real-Collab Android smoke. No model client is constructed.

Run from repository root (after sourcing mobile/tools/mobile_toolchain_env.sh):
  python test/omp_mobile_emulator_backend.py --upstream /absolute/pi-coding-agent --run

The only synthetic pieces are the upstream host's model-free session prompt sink,
model companion catalog, and fixture scenario controls. Flutter and gateway are
production implementations. All state lives in a temporary directory; fixture
controls require a random bearer token and the listener is IPv4 loopback only.
"""
from __future__ import annotations

import argparse
import json
import os
import queue
import signal
import time
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import threading
from urllib.request import Request, urlopen
from urllib.error import HTTPError

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / 'lib'))
from mobile_gateway.service import (MobileGatewayService, MobileGatewayError,
                                    build_mobile_gateway_server, parse_listen_address)
from provider_core.pathing import session_filename_for_agent

# Reuse the native smoke's pinned-release loader and isolated native wrapper
# staging verbatim, rather than introducing another package resolver convention.
NATIVE = r'''
console.error('fixture phase: native modules loaded');
const manager = SessionManager.inMemory(root);
const listeners = new Set();
let prompts = 0, changes = 0, modelRevision = 1, disconnected = false;
let model = {provider: 'fixture', id: 'one'}, thinking = 'off', live = null;
const models = ['one', 'two'].map(id => ({provider: 'fixture', id,
  name: `Fixture ${id} (no inference)`, reasoning_levels: ['off']}));
const session = {
  isStreaming: false, isAborting: false, isSessionTransitioning: false,
  queuedMessageCount: 0, sessionName: 'isolated fixture', model: null, thinkingLevel: 'off',
  subscribe: fn => { listeners.add(fn); return () => listeners.delete(fn); }, emitNotice() {},
  async promptCustomMessage(message) {
    prompts++;
    manager.appendCustomMessageEntry(message.customType, message.content, message.display, message.details, message.attribution);
    return true;
  },
};
const noop = () => {};
const ctx = {session, sessionManager: manager, settings: Settings.isolated(),
  statusLine: {setCollabStatus: noop, invalidate: noop, getCachedContextBreakdown: () => ({usedTokens: 0, contextWindow: 1000})},
  ui: {requestRender: noop}, showStatus: noop, updatePendingMessagesDisplay: noop};
manager.appendMessage({role:'user', content:'Fixture history question', timestamp:Date.now()});
manager.appendMessage({role:'assistant', content:[{type:'text', text:'Fixture initial native snapshot'}], stopReason:'stop', timestamp:Date.now()});
const relay = startRelay();
let host = new CollabHost(ctx);
await host.start(relay.url);
console.error('fixture phase: Collab host started');
const net = await import('node:net');
const editor = path.join(root, 'editor.sock');
const runtimeId = 'fixture-runtime-one';
const companion = net.createServer(socket => {
  let data = '';
  socket.on('data', chunk => { data += chunk; if (!data.includes('\n')) return;
    try {
      const request = JSON.parse(data.split('\n')[0]);
      let response;
      if (request.operation === 'inspect') response = {pid:process.pid, session_id:manager.getSessionId(), runtime_instance_id:runtimeId, leaf_id:manager.getLeafId(), state:'empty'};
      else if (request.operation === 'ancestry') response = {pid:process.pid, session_id:manager.getSessionId(), runtime_instance_id:runtimeId, leaf_id:manager.getLeafId(), parent_links:manager.getEntries().map(entry=>[entry.id,entry.parentId??null])};
      else {
        if (request.session_id !== manager.getSessionId() || request.runtime_instance_id !== runtimeId) throw Object.assign(Error('Fixture companion changed'), {status_code:409});
        if (request.operation === 'set_model') {
          if (request.expected_model_revision !== String(modelRevision) || request.expected_model?.id !== model.id || request.expected_thinking !== thinking)
            throw Object.assign(Error('Fixture model changed'), {status_code:409});
          if (!models.some(candidate => candidate.provider === request.model?.provider && candidate.id === request.model?.id)) throw Error('Unknown fixture model');
          model = request.model; thinking = request.thinking; modelRevision++; changes++;
        } else if (request.operation !== 'models') throw Error('Unknown companion operation');
        response = {session_id:manager.getSessionId(), runtime_instance_id:runtimeId, model, thinking_level:thinking, model_revision:String(modelRevision), models};
      }
      socket.end(JSON.stringify(response)+'\n');
    } catch(error) {socket.end(JSON.stringify({error:error.message, status_code:error.status_code ?? 503})+'\n');}
  });
});
await new Promise(resolve => companion.listen(editor, resolve));
const guest = new ManagedGuest({cwd:root, actor:'mobile', launch_session_id:'fixture-launch',
  registry_dir:collabHostsRuntimeDir(), editor_socket:editor}, relay);
await guest.sync();
const emit = event => {for (const fn of listeners) fn(event);};
const bridge = path.join(root, 'runtime/collab/bridge.sock');
await fs.mkdir(path.dirname(bridge), {recursive:true, mode:0o700});
const bridgeServer = net.createServer(socket => {
  let data = '';
  socket.on('data', async chunk => {
    data += chunk; if (!data.includes('\n')) return;
    socket.removeAllListeners('data');
    let request;
    try {
      request = JSON.parse(data.split('\n')[0]);
      let result;
      if (request.operation === 'scenario') {
        switch (request.action) {
          case 'live':
            live = {role:'assistant', content:[{type:'text',text:`Fixture live turn ${prompts}`}], timestamp:Date.now()};
            session.isStreaming = true;
            emit({type:'agent_start'});
            emit({type:'message_start',message:live});
            emit({type:'message_update',message:live,assistantMessageEvent:{type:'text_delta',contentIndex:0,delta:live.content[0].text,partial:live}});
            break;
          case 'finish':
            if (!live) throw Error('No live fixture turn');
            manager.appendMessage({...live,stopReason:'tool_use',content:[...live.content,{type:'toolCall',id:`fixture-call-${prompts}`,name:'read',arguments:{path:'fixture.txt'}}]});
            emit({type:'message_end',message:live});
            manager.appendMessage({role:'toolResult',toolCallId:`fixture-call-${prompts}`,toolName:'read',content:[{type:'text',text:`Fixture tool result ${prompts}`}],isError:false,timestamp:Date.now()});
            manager.appendMessage({role:'assistant',content:[{type:'text',text:`Fixture durable turn ${prompts}`}],stopReason:'stop',timestamp:Date.now()});
            live = null; session.isStreaming = false; emit({type:'agent_end',messages:[]}); break;
          case 'error':
            manager.appendMessage({role:'assistant',content:[],stopReason:'error',errorMessage:'Fixture explicit failure',timestamp:Date.now()}); break;
          case 'disconnect': await host.stop(); disconnected = true; break;
          case 'reconnect': host = new CollabHost(ctx); await host.start(relay.url); disconnected = false; await guest.sync(); break;
          default: throw Error('Unknown fixture scenario');
        }
        await new Promise(resolve => setTimeout(resolve,100));
        result = {prompts, changes, session_id:manager.getSessionId(), generation:guest.snapshot().generation, model, pid:process.pid};
      } else if (request.operation === 'snapshot') {
        if (disconnected) throw Object.assign(Error('Fixture disconnected'),{status_code:503});
        await guest.sync(); result = guest.snapshot();
      } else if (request.operation === 'submit') result = await guest.submit(request);
      else if (request.operation === 'models' || request.operation === 'set_model') result = await guest.models(request,request.operation === 'set_model');
      else if (request.operation === 'stats') result = {prompts, changes, session_id:manager.getSessionId(), generation:guest.snapshot().generation, model, pid:process.pid};
      else throw Error('Unknown fixture bridge operation');
      socket.end(JSON.stringify({actor:'mobile',launch_session_id:'fixture-launch',result})+'\n');
    } catch(error) {socket.end(JSON.stringify({actor:'mobile',launch_session_id:'fixture-launch',error:error.message,status_code:error.status_code??503})+'\n');}
  });
});
await new Promise(resolve => bridgeServer.listen(bridge,resolve));
await fs.chmod(bridge,0o600);
console.log(JSON.stringify({fixture_ready:true,root,bridge,session_id:manager.getSessionId()}));
process.on('SIGTERM',async()=>{guest.reset();await host.stop();relay.close();companion.close();bridgeServer.close();await fs.rm(root,{recursive:true,force:true});process.exit(0);});
'''


def stop_process_group(process):
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        pass
    # Reap descendants even if the direct parent already exited.
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait(timeout=5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream', type=Path, required=True)
    parser.add_argument('--port', type=int, default=18780)
    parser.add_argument('--device', default='emulator-5580')
    parser.add_argument('--evidence', type=Path, default=Path('/tmp/ccb-omp-emulator-evidence'))
    parser.add_argument('--run', action='store_true', help='run Flutter test and collect adb screenshots')
    parser.add_argument('--preflight', action='store_true', help='save native snapshot and authenticated HTTP conversation, then exit without Flutter or adb')
    args = parser.parse_args()
    args.evidence.mkdir(parents=True, exist_ok=True)
    loader = (REPO / 'test/omp_collab_native_smoke.mjs').read_text().split('const manager = SessionManager.inMemory(root);', 1)[0]
    loader = loader.replace("'../lib/provider_backends/omp/collab_runtime.mjs'", json.dumps((REPO / 'lib/provider_backends/omp/collab_runtime.mjs').as_uri()))
    for expression in ('const nativeExports = await import', 'const bundled = await Bun.build',
                       'const { CollabHost, SessionManager, Settings, collabHostsRuntimeDir } = await import'):
        loader = loader.replace(expression, f'console.error({json.dumps("fixture phase: " + expression)});\n' + expression)
    with tempfile.TemporaryDirectory(prefix='ccb-omp-emulator-loader-') as temporary:
        script = Path(temporary) / 'fixture.mjs'
        script.write_text(loader + NATIVE)
        stderr = (args.evidence / 'native.log').open('w')
        native_env = {**os.environ, 'HOME': temporary, 'PI_CONFIG_DIR': '.omp',
                      'PI_CODING_AGENT_DIR': str(Path(temporary) / '.omp/agent'),
                      'XDG_CONFIG_HOME': str(Path(temporary) / 'config'),
                      'XDG_DATA_HOME': str(Path(temporary) / 'data'),
                      'XDG_STATE_HOME': str(Path(temporary) / 'state'),
                      'XDG_CACHE_HOME': str(Path(temporary) / 'cache')}
        child = subprocess.Popen(['bun', '--no-install', str(script), str(args.upstream.resolve())], stdout=subprocess.PIPE, stderr=stderr, text=True, start_new_session=True, env=native_env)
        server = None
        try:
            lines = queue.Queue()
            def read_startup():
                for line in child.stdout:
                    lines.put(line)
                lines.put(None)
            threading.Thread(target=read_startup, daemon=True).start()
            info = None
            deadline = time.monotonic() + 90
            while info is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise RuntimeError(f'Native startup timed out; inspect {args.evidence / "native.log"}')
                try:
                    line = lines.get(timeout=remaining)
                except queue.Empty:
                    raise RuntimeError(f'Native startup timed out; inspect {args.evidence / "native.log"}') from None
                if line is None:
                    raise RuntimeError(f'Native host failed; inspect {args.evidence / "native.log"}')
                try:
                    value = json.loads(line)
                    if value.get('fixture_ready'):
                        info = value
                except (ValueError, AttributeError):
                    pass
            root = Path(info['root'])
            (root / '.ccb').mkdir(mode=0o700)
            (root / '.ccb/ccb.config').write_text('version = 2\nentry_window = "main"\n[windows]\nmain = "mobile:omp"\n')
            (root / '.ccb' / session_filename_for_agent('omp', 'mobile')).write_text(json.dumps({
                'provider':'omp','agent_name':'mobile','active':True,'start_dir':str(root),
                'ccb_session_id':'fixture-launch','runtime_dir':str(root / 'runtime'),
                'omp_collab_socket':info['bridge']}))

            def bridge(operation, **fields):
                with socket.socket(socket.AF_UNIX) as client:
                    client.settimeout(5)
                    client.connect(info['bridge'])
                    client.sendall((json.dumps({'operation':operation, **fields})+'\n').encode())
                    response = bytearray()
                    while b'\n' not in response:
                        part = client.recv(65536)
                        if not part:
                            raise RuntimeError('Native fixture bridge closed')
                        response.extend(part)
                    result = json.loads(response.split(b'\n')[0])
                    if result.get('error'):
                        raise MobileGatewayError(result['error'], status_code=result.get('status_code',503))
                    return result['result']

            class Client:
                def project_view(self, **kwargs):
                    return {'view': {'project': {'id':'omp-fixture','name':'OMP fixture','root':str(root)},
                        'namespace': {'epoch':4,'socket_path':str(root/'unused-tmux'),'session_name':'fixture'},
                        'agents':[{'name':'mobile','provider':'omp','pane_id':'%1'}], 'windows':[]}}

            class FixtureGateway(MobileGatewayService):
                reads = 0
                terminals = 0
                last_conversation = None
                def dispatch_get(self, path, headers=None):
                    if '/conversation' in path:
                        self.reads += 1
                    if path == '/__fixture/stats':
                        self._authenticate(headers, required_scopes=('view',))
                        return 200, {**bridge('stats'),'conversation_reads':self.reads,'terminal_calls':self.terminals}
                    if path == '/__fixture/diagnostics':
                        self._authenticate(headers, required_scopes=('view',))
                        return 200, {'snapshot':bridge('snapshot'), 'last_conversation':self.last_conversation}
                    try:
                        status, payload = super().dispatch_get(path, headers)
                    except MobileGatewayError as error:
                        if '/conversation' in path:
                            self.last_conversation = {'status_code':error.status_code,'error':str(error)}
                            (args.evidence/'last-conversation.json').write_text(json.dumps(self.last_conversation,indent=2))
                        raise
                    if '/conversation' in path:
                        self.last_conversation = {'status_code':status,'payload':payload}
                        (args.evidence/'last-conversation.json').write_text(json.dumps(self.last_conversation,indent=2))
                    return status, payload

                def dispatch_post(self, path, body, headers=None):
                    if path.startswith('/__fixture/'):
                        self._authenticate(headers, required_scopes=('view',))
                        action = path.removeprefix('/__fixture/')
                        if action == 'screenshot':
                            name = (body or {}).get('name')
                            if name not in {'initial','live','durable','tools','model','reconnect','final'}:
                                raise MobileGatewayError('Unknown screenshot',status_code=400)
                            with (args.evidence / f'{name}.png').open('wb') as image:
                                subprocess.run(['adb','-s',args.device,'exec-out','screencap','-p'],stdout=image,check=True,timeout=15)
                            return 200, {'saved':name}
                        return 200, bridge('scenario',action=action)
                    if 'terminal' in path:
                        self.terminals += 1
                    try:
                        status, response = super().dispatch_post(path,body,headers)
                    except MobileGatewayError as error:
                        if '/messages' in path or '/provider-control' in path:
                            (args.evidence/'last-submit.json').write_text(json.dumps({'body':body,'status':error.status_code,'error':str(error)},indent=2))
                        raise
                    if '/messages' in path or '/provider-control' in path:
                        (args.evidence/'last-submit.json').write_text(json.dumps({'body':body,'status':status,'response':response},indent=2))
                    return status, response

            def forbidden_terminal(*args, **kwargs):
                service.terminals += 1
                raise AssertionError('OMP fixture must never use terminal input')

            service = FixtureGateway(project_id='omp-fixture', project_root=root,
                ccbd_client_factory=Client,mobile_dir=root/'mobile',terminal_message_sender=forbidden_terminal)
            url = f'http://127.0.0.1:{args.port}'
            pairing = service.create_pairing_payload(gateway_url=url,scopes=('view','message_submit','provider_settings'))
            _, claim = service.dispatch_post('/v1/pairing/claim',{'pairing_code':pairing['pairing_code']})
            token = claim['device_token']
            defines = {'OMP_FIXTURE_URL':url,'OMP_FIXTURE_TOKEN':token}
            # The token file is local-only and removed with the loader directory.
            defines_file = Path(temporary)/'defines.json'
            defines_file.write_text(json.dumps(defines)); defines_file.chmod(0o600)
            server = build_mobile_gateway_server(parse_listen_address(f'127.0.0.1:{args.port}'),service)
            threading.Thread(target=server.serve_forever,daemon=True).start()
            (args.evidence/'initial-native-snapshot.json').write_text(json.dumps(bridge('snapshot'),indent=2))
            request = Request(url+'/v1/projects/omp-fixture/agents/mobile/conversation?namespace_epoch=4',headers={'Authorization':f'Bearer {token}'})
            try:
                with urlopen(request,timeout=10) as response:
                    initial = json.load(response)
            except HTTPError as error:
                (args.evidence/'preflight-error.json').write_text(json.dumps({'status_code':error.code,'body':error.read().decode()},indent=2))
                raise RuntimeError('Gateway preflight failed; inspect preflight-error.json') from None
            (args.evidence/'initial-conversation.json').write_text(json.dumps(initial,indent=2))
            if not any(item.get('body') == 'Fixture initial native snapshot' for item in initial.get('conversation',{}).get('items',[])):
                raise RuntimeError('Initial native reply absent; inspect initial-native-snapshot.json and initial-conversation.json')
            if args.preflight:
                print(f'Native and real HTTP gateway preflight complete: {args.evidence}',flush=True)
                return
            command = ['flutter','test','integration_test/omp_collab_mobile_smoke_test.dart','-d',args.device,f'--dart-define-from-file={defines_file}']
            print(f'Fixture ready; evidence: {args.evidence}',flush=True)
            if args.run:
                subprocess.run(['adb','-s',args.device,'reverse',f'tcp:{args.port}',f'tcp:{args.port}'],check=True,timeout=15)
                try:
                    flutter = subprocess.Popen(command,cwd=REPO/'mobile/app',start_new_session=True)
                    try:
                        flutter.wait(timeout=1200)
                    finally:
                        if flutter.returncode != 0:
                            try:
                                with (args.evidence/'failure.png').open('wb') as image:
                                    subprocess.run(['adb','-s',args.device,'exec-out','screencap','-p'],stdout=image,check=True,timeout=15)
                            except (OSError,subprocess.SubprocessError) as error:
                                (args.evidence/'failure-screenshot-error.txt').write_text(str(error))
                            try:
                                (args.evidence/'failure-native-snapshot.json').write_text(json.dumps(bridge('snapshot'),indent=2))
                            except (OSError,RuntimeError,MobileGatewayError) as error:
                                (args.evidence/'failure-native-error.txt').write_text(str(error))
                        stop_process_group(flutter)
                    (args.evidence/'backend-final.json').write_text(json.dumps({**bridge('stats'),'conversation_reads':service.reads,'terminal_calls':service.terminals,'model_calls':0},indent=2))
                    if flutter.returncode:
                        raise SystemExit(flutter.returncode)
                finally:
                    subprocess.run(['adb','-s',args.device,'reverse','--remove',f'tcp:{args.port}'],check=False,timeout=15)
            else:
                import shlex
                print('cd mobile/app && '+shlex.join(command),flush=True)
                threading.Event().wait()
        finally:
            if server:
                server.shutdown(); server.server_close()
            stop_process_group(child)
            stderr.close()


if __name__ == '__main__':
    main()
