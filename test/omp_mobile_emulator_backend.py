#!/usr/bin/env python3
"""Isolated real-Collab Android smoke. No model client is constructed.

Run from repository root (after sourcing mobile/tools/mobile_toolchain_env.sh):
  python test/omp_mobile_emulator_backend.py --upstream /absolute/pi-coding-agent --run
  python test/omp_mobile_emulator_backend.py --upstream /absolute/pi-coding-agent --relay --run
  python test/omp_mobile_emulator_backend.py --upstream /absolute/pi-coding-agent --relay --long-history --run

--relay injects >2MiB native history, starts ProductionRelayService plus the real
RelayHostConnector with isolated admission, keys and TLS trust, and runs the full
Android smoke using RelaySocketGatewayTransport through adb reverse. History
page IDs and exact semantic text/SHA256 are checked by the production repository;
only fixture controls/stats use direct authenticated HTTP. The full smoke also
submits three native inputs while busy and checks FIFO order, no concurrent
prompts, stable queued IDs, and preserved intermediate/durable history.
--long-history seeds >28,000 native entries, a >16,384 metadata-only gap and
>48MiB of native JSON records. Repository pagination runs without a UI page loop;
the Android UI still exercises history, readable content and branch changes.
Every history response comes from the generated production composer BRIDGE.
No paid inference runs.
All state lives in a temporary directory; fixture
controls require a random bearer token and the listener is IPv4 loopback only.
"""
from __future__ import annotations

import argparse
import json
import hashlib
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
                                    build_mobile_gateway_server, parse_listen_address,
                                    _omp_native_conversation_items, _clean_native_message_text)
from provider_core.pathing import session_filename_for_agent
from provider_backends.omp.composer_bridge import BRIDGE, IMPORTS

# Reuse the native smoke's pinned-release loader and isolated native wrapper
# staging verbatim, rather than introducing another package resolver convention.
NATIVE = r'''
console.error('fixture phase: native modules loaded');
const { serializeReplicatedEntry } = await import(pathToFileURL(path.join(source, 'src/collab/replication-shrink.ts')).href);
const manager = SessionManager.inMemory(root);
const listeners = new Set();
let prompts = 0, changes = 0, modelRevision = 1, disconnected = false;
let model = {provider: 'fixture', id: 'one'}, thinking = 'off', live = null;
let automaticLive = false, activePrompts = 0, maxActivePrompts = 0;
const dispatchOrder = [];
const notices = [];
let nativePages = 0, maxNativePageBytes = 0;
let historyFailure = false;
const nativeGetEntry = manager.getEntry.bind(manager);
manager.getEntry = id => { if (historyFailure) throw Error('Fixture native history unavailable'); return nativeGetEntry(id); };
const emit = event => {for (const fn of listeners) fn(event);};
const beginLive = () => {
  live = {role:'assistant',content:[{type:'text',text:`Fixture live turn ${prompts}`}],timestamp:Date.now()};
  session.isStreaming = true;
  emit({type:'agent_start'});
  emit({type:'message_start',message:live});
  emit({type:'message_update',message:live,assistantMessageEvent:{type:'text_delta',contentIndex:0,delta:live.content[0].text,partial:live}});
};
const models = ['one', 'two'].map(id => ({provider: 'fixture', id,
  name: `Fixture ${id} (no inference)`, reasoning_levels: ['off']}));
const session = {
  isStreaming: false, isAborting: false, isSessionTransitioning: false,
  queuedMessageCount: 0, sessionName: 'isolated fixture', model: null, thinkingLevel: 'off',
  subscribe: fn => { listeners.add(fn); return () => listeners.delete(fn); },
  emitNotice(...args) { notices.push(args); },
  async promptCustomMessage(message) {
    prompts++;
    manager.appendCustomMessageEntry(message.customType, message.content, message.display, message.details, message.attribution);
    dispatchOrder.push({ordinal:prompts,content:message.content});
    if (automaticLive) {
      activePrompts++;
      maxActivePrompts = Math.max(maxActivePrompts, activePrompts);
      beginLive();
    }
    return true;
  },
};
const noop = () => {};
const ctx = {session, sessionManager: manager, settings: Settings.isolated(),
  statusLine: {setCollabStatus: noop, invalidate: noop, getCachedContextBreakdown: () => ({usedTokens: 0, contextWindow: 1000})},
  ui: {requestRender: noop}, showStatus: noop, updatePendingMessagesDisplay: noop};
if (process.env.OMP_FIXTURE_LONG === '1') {
  manager.appendMessage({role:'user',content:'Fixture earliest history marker',timestamp:Date.now()});
  manager.appendMessage({role:'assistant',content:[{type:'text',text:'Fixture earliest history answer'}],stopReason:'stop',timestamp:Date.now()});
  for (let index = 0; index < 28500; index++) {
    manager.appendCustomEntry('fixture-history-metadata', {index, payload:'metadata 雪🌍 '.repeat(150)});
    if (index === 19000) {
      manager.appendMessage({role:'user',content:'Fixture middle history marker',timestamp:Date.now()});
      manager.appendMessage({role:'assistant',content:[{type:'text',text:'Fixture middle history answer'}],stopReason:'stop',timestamp:Date.now()});
    }
  }
}
if (process.env.OMP_FIXTURE_LARGE === '1') {
  const longHistory = process.env.OMP_FIXTURE_LONG === '1';
  for (let index = 0; index < 96; index++) {
    manager.appendMessage({role:'user',content:`Fixture history ${index}`,timestamp:Date.now()});
    manager.appendMessage({role:'assistant',content:[{type:'text',text:`Fixture history reply ${index}: ` + 'Unicode 雪🌍 café é\n'.repeat(longHistory ? 24000 : 1100)}],stopReason:'stop',timestamp:Date.now()});
  }
  manager.appendMessage({role:'user',content:'Fixture oversized question',timestamp:Date.now()});
  manager.appendMessage({role:'assistant',content:[{type:'thinking',thinking:'Fixture oversized reasoning\nCheck Unicode without changing original text.'},
    {type:'toolCall',id:'fixture-oversize-call',name:'read',arguments:{path:'fixture-large.txt'}},
    {type:'text',text:'Fixture oversized original\n\n## Readable Markdown\n\n**Unicode proof** with `inline code`.\n\n```text\noriginal line one\noriginal line two\n```\n\n'},
    {type:'text',text:'原文🌍 café é\n'.repeat(35000)}],stopReason:'tool_use',timestamp:Date.now()});
  manager.appendMessage({role:'toolResult',toolCallId:'fixture-oversize-call',toolName:'read',content:[{type:'text',text:'Fixture oversized tool result\n' + '工具🔧結果\n'.repeat(12000)}],isError:false,timestamp:Date.now()});
  manager.appendMessage({role:'assistant',content:[{type:'text',text:'Fixture oversized complete'}],stopReason:'stop',timestamp:Date.now()});
  manager.appendMessage({role:'user',content:'Fixture readable question',timestamp:Date.now()});
  manager.appendMessage({role:'assistant',content:[
    {type:'thinking',thinking:'Fixture readable reasoning\nCheck the source before answering.'},
    {type:'toolCall',id:'fixture-readable-call',name:'read',arguments:{path:'fixture-readable.txt'}},
    {type:'text',text:'## Fixture readable answer\n\n**Readable emphasis** and `inline sample`.\n\n```text\nfirst readable line\nsecond readable line\n```'}],stopReason:'tool_use',timestamp:Date.now()});
  manager.appendMessage({role:'toolResult',toolCallId:'fixture-readable-call',toolName:'read',content:[{type:'text',text:'Fixture readable tool result\nA second readable result line.\n' + '工具🔧結果\n'.repeat(35000)}],isError:false,timestamp:Date.now()});
  manager.appendMessage({role:'assistant',content:[{type:'text',text:'Fixture readable complete'}],stopReason:'stop',timestamp:Date.now()});
}
manager.appendMessage({role:'user', content:'Fixture history question', timestamp:Date.now()});
manager.appendMessage({role:'assistant', content:[{type:'text', text:'Fixture initial native snapshot'}], stopReason:'stop', timestamp:Date.now()});
const relay = startRelay();
let host = new CollabHost(ctx);
await host.start(relay.url);
console.error('fixture phase: Collab host started');
const net = await import('node:net');
const editor = path.join(root, 'editor.sock');
process.env.CCB_OMP_COMPOSER_SOCKET = path.join(root, 'production-editor.sock');
const { initialize } = await import(process.env.OMP_FIXTURE_COMPOSER_MODULE);
const handlers = new Map();
const productionContext = {sessionManager:manager,hasUI:true,isIdle:()=>!session.isStreaming,
  ui:{getEditorText:()=>'',setEditorText:()=>{}}, modelRegistry:{getAvailable:()=>models}};
initialize({on:(name,fn)=>handlers.set(name,fn)}, 'mobile', 'fixture-launch', 'fixture-runtime-one');
await handlers.get('session_start')({},productionContext);
// Only model selection remains controlled. inspect and every history operation
// traverse the generated production composer socket and the native manager.
const companion = net.createServer(socket => {
  let data = '';
  socket.on('data', chunk => { data += chunk; if (!data.includes('\n')) return;
    socket.removeAllListeners('data');
    const request = JSON.parse(data.split('\n')[0]);
    if (!['models','set_model'].includes(request.operation)) {
      const upstream = net.createConnection(process.env.CCB_OMP_COMPOSER_SOCKET);
      let responseBytes = 0;
      upstream.on('connect',()=>upstream.write(data));
      upstream.on('data',chunk=>{responseBytes += chunk.length;socket.write(chunk);});
      upstream.on('end',()=>{
        if (['history_page','ancestry_page'].includes(request.operation)) {
          nativePages++; maxNativePageBytes = Math.max(maxNativePageBytes,responseBytes);
        }
        socket.end();
      });
      upstream.on('error',()=>socket.end(JSON.stringify({error:'Production companion unavailable',status_code:503})+'\n'));
      socket.on('close',()=>upstream.destroy());
      return;
    }
    try {
      if (request.session_id !== manager.getSessionId() || request.runtime_instance_id !== 'fixture-runtime-one')
        throw Object.assign(Error('Fixture companion changed'), {status_code:409});
      if (request.operation === 'set_model') {
        if (request.expected_model_revision !== String(modelRevision) || request.expected_model?.id !== model.id || request.expected_thinking !== thinking)
          throw Object.assign(Error('Fixture model changed'), {status_code:409});
        if (!models.some(candidate => candidate.provider === request.model?.provider && candidate.id === request.model?.id)) throw Error('Unknown fixture model');
        model = request.model; thinking = request.thinking; modelRevision++; changes++;
      }
      socket.end(JSON.stringify({session_id:manager.getSessionId(),runtime_instance_id:'fixture-runtime-one',model,thinking_level:thinking,model_revision:String(modelRevision),models})+'\n');
    } catch(error) {socket.end(JSON.stringify({error:error.message,status_code:error.status_code??503})+'\n');}
  });
});
await new Promise(resolve => companion.listen(editor, resolve));
const guest = new ManagedGuest({cwd:root, actor:'mobile', launch_session_id:'fixture-launch',
  registry_dir:collabHostsRuntimeDir(), editor_socket:editor}, relay);
await guest.sync();
let draining = false, closed = false;
const drainTimer = setInterval(() => {
  if (closed || draining) return;
  draining = true;
  guest.exclusive(() => guest.drain()).catch(() => {}).finally(() => {draining = false;});
}, 250);
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
          case 'history-error': historyFailure = true; break;
          case 'history-recover': historyFailure = false; break;
          case 'append-history':
            manager.appendMessage({role:'user',content:'Fixture append history marker',timestamp:Date.now()});
            manager.appendMessage({role:'assistant',content:[{type:'text',text:'Fixture append history answer'}],stopReason:'stop',timestamp:Date.now()});
            break;
          case 'branch-history':
            manager.resetLeaf();
            manager.appendMessage({role:'user',content:'Fixture isolated branch marker',timestamp:Date.now()});
            manager.appendMessage({role:'assistant',content:[{type:'text',text:'Fixture isolated branch answer'}],stopReason:'stop',timestamp:Date.now()});
            await handlers.get('session_tree')({},productionContext);
            break;
          case 'automatic_live': automaticLive = true; break;
          case 'intermediate': {
            if (!live) throw Error('No live fixture turn');
            const completed = {...live,stopReason:'tool_use'};
            manager.appendMessage(completed);
            emit({type:'message_end',message:completed});
            live = {role:'assistant',content:[{type:'text',text:`Fixture intermediate continuation ${prompts}`}],timestamp:Date.now()};
            emit({type:'message_start',message:live});
            emit({type:'message_update',message:live,assistantMessageEvent:{type:'text_delta',contentIndex:0,delta:live.content[0].text,partial:live}});
            break;
          }
          case 'large-live-one':
          case 'large-live-two': {
            if (!live) throw Error('No live fixture turn');
            const marker = request.action === 'large-live-one' ? 'BOUNDED LIVE LATEST ONE' : 'BOUNDED LIVE LATEST TWO';
            live = {...live,content:[{type:'text',text:'Earlier visible content 雪🌍\n'.repeat(5000) + marker}]};
            emit({type:'message_update',message:live,assistantMessageEvent:{type:'text_delta',contentIndex:0,delta:marker,partial:live}});
            break;
          }
          case 'live':
            beginLive();
            break;
          case 'finish':
            if (!live) throw Error('No live fixture turn');
            manager.appendMessage({...live,stopReason:'tool_use',content:[...live.content,{type:'toolCall',id:`fixture-call-${prompts}`,name:'read',arguments:{path:'fixture.txt'}}]});
            emit({type:'message_end',message:live});
            manager.appendMessage({role:'toolResult',toolCallId:`fixture-call-${prompts}`,toolName:'read',content:[{type:'text',text:`Fixture tool result ${prompts}`}],isError:false,timestamp:Date.now()});
            manager.appendMessage({role:'assistant',content:[{type:'text',text:`Fixture durable turn ${prompts}`}],stopReason:'stop',timestamp:Date.now()});
            live = null; session.isStreaming = false;
            if (automaticLive) activePrompts--;
            emit({type:'agent_end',messages:[]}); break;
          case 'error':
            emit({type:'agent_start'});
            manager.appendMessage({role:'assistant',content:[],stopReason:'error',errorMessage:'Fixture explicit failure',timestamp:Date.now()});
            session.isStreaming = false; emit({type:'agent_end',messages:[]}); break;
          case 'disconnect': await host.stop(); disconnected = true; break;
          case 'reconnect': host = new CollabHost(ctx); await host.start(relay.url); disconnected = false; await guest.sync(); break;
          default: throw Error('Unknown fixture scenario');
        }
        await new Promise(resolve => setTimeout(resolve,100));
        result = {prompts, changes, active_prompts:activePrompts,max_active_prompts:maxActivePrompts,dispatch_order:dispatchOrder,session_id:manager.getSessionId(), generation:guest.snapshot().generation, model, pid:process.pid};
      } else if (request.operation === 'snapshot') {
        if (disconnected) throw Object.assign(Error('Fixture disconnected'),{status_code:503});
        result = await guest.exclusive(() => guest.refreshSnapshot());
      } else if (request.operation === 'submit') result = await guest.exclusive(() => guest.admit(request));
      else if (request.operation === 'history') result = await guest.exclusive(() => guest.history(request));
      else if (request.operation === 'fixture-seed') {
        // Fixture-only expected metrics. Never used to serve production history.
        const entries = manager.getEntries();
        let run = 0, gap = 0, wire = 0, messageBytes = 0;
        for (const entry of entries) {
          wire += Buffer.byteLength(JSON.stringify(entry),'utf8');
          if (entry.type === 'message') messageBytes += Buffer.byteLength(serializeReplicatedEntry(entry).json,'utf8');
          run = entry.type === 'message' ? 0 : run + 1; gap = Math.max(gap,run);
        }
        const oversized = entries.findIndex(e => e.type === 'message' && e.message.role === 'assistant' &&
          Array.isArray(e.message.content) && e.message.content.some(b => b.text?.includes('Fixture oversized original')));
        const semanticBlocks = [];
        if (oversized >= 0) {
          for (const entry of entries.slice(oversized)) {
            if (entry.type !== 'message') continue;
            const message = entry.message;
            if (message.role === 'user') break;
            for (const [index, block] of message.content.entries()) {
              const type = message.role === 'toolResult' ? 'tool_result' :
                block.type === 'thinking' ? 'reasoning' : block.type === 'toolCall' ? 'tool_call' : 'text';
              semanticBlocks.push({entry_id:entry.id,block_index:index,role:message.role === 'toolResult' ? 'tool' : message.role,type,
                text:type === 'reasoning' ? block.thinking : type === 'tool_call' ? JSON.stringify(block.arguments,null,2) : block.text});
            }
          }
        }
        result = {...guest.snapshot(),entries:entries.filter(e=>e.type==='message'),
          parent_links:entries.map(e=>[e.id,e.parentId??null]),
          fixture_metrics:{entry_count:entries.length,max_metadata_gap:gap,raw_history_bytes:wire,
            wire_history_bytes:messageBytes},fixture_semantic_blocks:semanticBlocks};
      }
      else if (request.operation === 'models' || request.operation === 'set_model') result = await guest.exclusive(() => guest.models(request,request.operation === 'set_model'));
      else if (request.operation === 'stats') {
        const snapshot = guest.snapshot();
        result = {prompts, changes,active_prompts:activePrompts,max_active_prompts:maxActivePrompts,dispatch_order:dispatchOrder,
          pending_inputs:snapshot.pending_inputs,state:snapshot.state,streaming:session.isStreaming,
          connected:snapshot.connected,ready:snapshot.ready,history_error:snapshot.history_error,notices,
          native_pages:nativePages,max_native_page_bytes:maxNativePageBytes,
          joined_count:notices.filter(args=>String(args[1]).includes('joined the collab session')).length,
          left_count:notices.filter(args=>String(args[1]).includes('left the collab session')).length,
          session_id:manager.getSessionId(), generation:snapshot.generation, model, pid:process.pid};
      }
      else throw Error('Unknown fixture bridge operation');
      socket.end(JSON.stringify({actor:'mobile',launch_session_id:'fixture-launch',result})+'\n');
    } catch(error) {socket.end(JSON.stringify({actor:'mobile',launch_session_id:'fixture-launch',error:error.message,status_code:error.status_code??503})+'\n');}
  });
});
await new Promise(resolve => bridgeServer.listen(bridge,resolve));
await fs.chmod(bridge,0o600);
console.log(JSON.stringify({fixture_ready:true,root,bridge,session_id:manager.getSessionId()}));
process.on('SIGTERM',async()=>{closed = true;clearInterval(drainTimer);await guest.exclusive(async()=>{guest.reset();await guest.persist();});await handlers.get('session_shutdown')();await host.stop();relay.close();companion.close();bridgeServer.close();await fs.rm(root,{recursive:true,force:true});process.exit(0);});
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
    parser.add_argument('--relay', action='store_true', help='exercise real encrypted TLS CCB Relay with >2MiB native history')
    parser.add_argument('--long-history', action='store_true', help='seed >28k native records, >16k metadata gap and >48MiB without UI paging loops')
    args = parser.parse_args()
    if args.relay and args.preflight:
        parser.error('--relay requires the Android repository smoke; --preflight is HTTP-only')
    args.evidence.mkdir(parents=True, exist_ok=True)
    loader = (REPO / 'test/omp_collab_native_smoke.mjs').read_text().split('const manager = SessionManager.inMemory(root);', 1)[0]
    loader = loader.replace("'../lib/provider_backends/omp/collab_runtime.mjs'", json.dumps((REPO / 'lib/provider_backends/omp/collab_runtime.mjs').as_uri()))
    for expression in ('const nativeExports = await import', 'const bundled = await Bun.build',
                       'const { CollabHost, SessionManager, Settings, collabHostsRuntimeDir } = await import'):
        loader = loader.replace(expression, f'console.error({json.dumps("fixture phase: " + expression)});\n' + expression)
    with tempfile.TemporaryDirectory(prefix='ccb-omp-emulator-loader-') as temporary:
        script = Path(temporary) / 'fixture.mjs'
        composer_module = Path(temporary) / 'production-composer.ts'
        composer_module.write_text(IMPORTS + '\nexport function initialize(pi: any, actor: string, launchSessionId: string, runtimeInstanceId: string) {\n' + BRIDGE + '\n}\n')
        script.write_text(loader + NATIVE)
        stderr = (args.evidence / 'native.log').open('w')
        native_env = {**os.environ, 'HOME': temporary, 'PI_CONFIG_DIR': '.omp',
                      'PI_CODING_AGENT_DIR': str(Path(temporary) / '.omp/agent'),
                      'XDG_CONFIG_HOME': str(Path(temporary) / 'config'),
                      'XDG_DATA_HOME': str(Path(temporary) / 'data'),
                      'XDG_STATE_HOME': str(Path(temporary) / 'state'),
                      'OMP_FIXTURE_LARGE': '1' if args.relay or args.long_history else '0',
                      'OMP_FIXTURE_LONG': '1' if args.long_history else '0',
                      'OMP_FIXTURE_COMPOSER_MODULE': composer_module.as_uri(),
                      'XDG_CACHE_HOME': str(Path(temporary) / 'cache')}
        child = subprocess.Popen(['bun', '--no-install', str(script), str(args.upstream.resolve())], stdout=subprocess.PIPE, stderr=stderr, text=True, start_new_session=True, env=native_env)
        server = None
        relay = None
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
                    client.settimeout(120)
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
                max_conversation_bytes = 0
                content_reads = 0
                older_reads = 0
                history_expectations = None
                last_conversation = None
                def dispatch_get(self, path, headers=None):
                    if '/conversation' in path:
                        self.reads += 1
                        if 'cursor=' in path:
                            self.older_reads += 1
                    if path == '/__fixture/stats':
                        self._authenticate(headers, required_scopes=('view',))
                        return 200, {**bridge('stats'),'conversation_reads':self.reads,'terminal_calls':self.terminals,
                            'max_conversation_bytes':self.max_conversation_bytes,'content_reads':self.content_reads,
                            'older_page_reads':self.older_reads,
                            'history':self.history_expectations}
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
                        self._record_conversation(payload)
                        self.last_conversation = {'status_code':status,'payload':payload}
                        (args.evidence/'last-conversation.json').write_text(json.dumps(self.last_conversation,indent=2))
                    return status, payload

                def _record_conversation(self, payload):
                    size = len(json.dumps(payload, ensure_ascii=True, separators=(',', ':')).encode('ascii'))
                    self.max_conversation_bytes = max(self.max_conversation_bytes, size)
                    if (payload.get('conversation') or {}).get('native_content') is not None:
                        self.content_reads += 1
                    if args.relay and size > 192 * 1024:
                        raise AssertionError(f'Conversation exceeded relay page budget: {size}')

                def dispatch_post(self, path, body, headers=None):
                    if path.startswith('/__fixture/'):
                        self._authenticate(headers, required_scopes=('view',))
                        action = path.removeprefix('/__fixture/')
                        if action == 'fifo-proof':
                            stats = bridge('stats')
                            if (stats['max_active_prompts'] != 1 or stats['active_prompts'] != 0
                                    or stats['prompts'] != 6 or (body or {}).get('dispatch_ordinals') != [4,5,6]):
                                raise MobileGatewayError('FIFO proof mismatch', status_code=400)
                            (args.evidence/'fifo-integrity.json').write_text(json.dumps({
                                'proof':body,'stats':stats},indent=2))
                            return 200, {'recorded':True}
                        if action in {'history-proof', 'branch-proof'}:
                            (args.evidence/f'{action}.json').write_text(json.dumps({'proof':body,'stats':bridge('stats')},indent=2))
                            return 200, {'recorded':True}
                        if action == 'proof':
                            proof = {key:(body or {}).get(key) for key in (
                                'history_pages','history_items','content_pages','semantic_blocks')}
                            expected = self.history_expectations or {}
                            if (proof['semantic_blocks'] != expected.get('semantic_blocks')
                                    or proof['history_items'] != expected.get('item_count')):
                                raise MobileGatewayError('Integrity proof mismatch', status_code=400)
                            (args.evidence/'relay-integrity.json').write_text(json.dumps(proof,indent=2))
                            return 200, {'recorded':True}
                        if action == 'screenshot':
                            name = (body or {}).get('name')
                            if name not in {'initial','bounded-bubble','original','original-next','history','history-error','history-recovered','history-branch','live','durable','tools','model','reconnect','fifo-queued','fifo-complete','final'}:
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
            if args.relay:
                from omp_mobile_fixture_relay import AndroidFixtureRelay
                relay = AndroidFixtureRelay(Path(temporary)/'relay', url, claim['device']['device_id'])
            defines = {'OMP_FIXTURE_URL':url,'OMP_FIXTURE_TOKEN':token,
                       'OMP_FIXTURE_LONG':'true' if args.long_history else 'false'}
            if relay:
                defines.update(relay.defines)
            # The token file is local-only and removed with the loader directory.
            defines_file = Path(temporary)/'defines.json'
            defines_file.write_text(json.dumps(defines)); defines_file.chmod(0o600)
            server = build_mobile_gateway_server(parse_listen_address(f'127.0.0.1:{args.port}'),service)
            threading.Thread(target=server.serve_forever,daemon=True).start()
            if args.relay or args.long_history:
                snapshot = bridge('fixture-seed')
                items = _omp_native_conversation_items(root, project_id='omp-fixture', agent='mobile', snapshot=snapshot)
                originals = [item for item in items if item.get('kind') == 'agent_reply' and 'Fixture oversized original' in json.dumps(item,ensure_ascii=False)]
                if len(originals) != 1:
                    raise RuntimeError('Oversized fixture turn missing')
                original = originals[0]
                data = json.dumps(original, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')
                semantic_blocks = []
                for block in snapshot['fixture_semantic_blocks']:
                    # Compare source text after the existing native projection
                    # cleaner, independently of content pagination/rendering.
                    source_text = block.pop('text')
                    text = (source_text if block['type'] == 'tool_call' else
                        _clean_native_message_text(source_text)).encode('utf-8')
                    semantic_blocks.append({**block, 'bytes':len(text),
                        'sha256':hashlib.sha256(text).hexdigest()})
                # Independent source order: answers first, execution chronological.
                semantic_blocks.sort(key=lambda block: not (block['role'] == 'assistant' and block['type'] == 'text'))
                readable = next(item for item in items if item.get('kind') == 'agent_reply'
                    and 'Fixture readable answer' in json.dumps(item,ensure_ascii=False))
                metrics = snapshot['fixture_metrics']
                history_bytes = metrics['raw_history_bytes']
                if history_bytes <= 2 * 1024 * 1024 or len(data) <= 512 * 1024:
                    raise RuntimeError('Large-history fixture did not reach acceptance sizes')
                if args.long_history and (metrics['entry_count'] <= 28000 or metrics['max_metadata_gap'] <= 16384 or metrics['wire_history_bytes'] <= 48 * 1024 * 1024):
                    raise RuntimeError('Long-history fixture did not reach acceptance sizes')
                service.history_expectations = {'item_count':len(items),
                    'original_id':original['id'],'readable_id':readable['id'], **metrics,
                    'original_version':hashlib.sha256(data).hexdigest(),
                    'semantic_blocks':semantic_blocks,
                    'ids_sha256':hashlib.sha256('\n'.join(sorted(item['id'] for item in items)).encode()).hexdigest()}
                (args.evidence/'history-expectations.json').write_text(json.dumps(service.history_expectations,indent=2))
            (args.evidence/'initial-native-snapshot.json').write_text(json.dumps(bridge('snapshot'),indent=2))
            if not args.relay:
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
                if relay:
                    subprocess.run(['adb','-s',args.device,'reverse',f'tcp:{relay.port}',f'tcp:{relay.port}'],check=True,timeout=15)
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
                        (args.evidence/'backend-final.json').write_text(json.dumps({**bridge('stats'),'conversation_reads':service.reads,'terminal_calls':service.terminals,'model_calls':0,
                            'max_conversation_bytes':service.max_conversation_bytes,'content_reads':service.content_reads,
                            'relay':relay.diagnostics() if relay else None},indent=2))
                    if flutter.returncode:
                        raise SystemExit(flutter.returncode)
                finally:
                    subprocess.run(['adb','-s',args.device,'reverse','--remove',f'tcp:{args.port}'],check=False,timeout=15)
                    if relay:
                        subprocess.run(['adb','-s',args.device,'reverse','--remove',f'tcp:{relay.port}'],check=False,timeout=15)
            else:
                import shlex
                print('cd mobile/app && '+shlex.join(command),flush=True)
                threading.Event().wait()
        finally:
            if relay:
                relay.close()
            if server:
                server.shutdown(); server.server_close()
            stop_process_group(child)
            stderr.close()


if __name__ == '__main__':
    main()
