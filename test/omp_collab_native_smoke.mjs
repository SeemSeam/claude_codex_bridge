// Run: bun test/omp_collab_native_smoke.mjs /absolute/path/to/pi-coding-agent/package
// Uses upstream CollabHost and SessionManager, loopback only, no model client.
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { pathToFileURL } from 'node:url';
import { mock } from 'bun:test';
import { ManagedGuest, startRelay } from '../lib/provider_backends/omp/collab_runtime.mjs';

const source = process.argv[2];
if (!source) throw Error('Pass installed upstream pi-coding-agent package directory');
// Download-cache packages have no sibling node_modules links. Bare imports can
// otherwise select an unrelated global install; use this exact release's cache
// and published export maps, without modifying upstream packages.
const packageInfo = JSON.parse(await fs.readFile(path.join(source, 'package.json'), 'utf8'));
const packageCache = path.dirname(path.resolve(source));
let nativePackageRoot;
const manifestCache = new Map();
const pinnedPlugin = {
  name: 'ccb-native-smoke-pinned-upstream',
  setup(build) {
    build.onResolve({ filter: /^@oh-my-pi\// }, async ({ path: specifier }) => {
      const match = /^@oh-my-pi\/([^/]+)(.*)$/.exec(specifier);
      const root = match[1] === 'pi-natives' ? nativePackageRoot : path.join(packageCache, `${match[1]}@${packageInfo.version}@@@1`);
      let manifest = manifestCache.get(root);
      if (!manifest) {
        manifest = JSON.parse(await fs.readFile(path.join(root, 'package.json'), 'utf8'));
        manifestCache.set(root, manifest);
      }
      const subpath = match[2] ? `.${match[2]}` : '.';
      let target = manifest.exports?.[subpath];
      if (!target && manifest.exports) {
        const patterns = Object.keys(manifest.exports).filter(key => key.includes('*')).sort((a, b) => b.length - a.length);
        for (const pattern of patterns) {
          const [prefix, suffix] = pattern.split('*');
          if (!subpath.startsWith(prefix) || !subpath.endsWith(suffix)) continue;
          const value = manifest.exports[pattern];
          target = typeof value === 'string' ? value : value.import ?? value.bun ?? value.default;
          target = target?.replace('*', subpath.slice(prefix.length, suffix.length ? -suffix.length : undefined));
          break;
        }
      }
      if (target && typeof target !== 'string') target = target.import ?? target.bun ?? target.default;
      if (!target && !manifest.exports) target = match[2] ? `.${match[2]}` : manifest.main;
      if (typeof target !== 'string') throw Error(`Upstream ${packageInfo.version} does not export ${specifier}`);
      return { path: path.resolve(root, target), external: match[1] === 'pi-natives' };
    });
    build.onResolve({ filter: /^(?![./]|@oh-my-pi\/|node:|bun:)[A-Za-z@]/ }, ({ path: specifier }) => ({ path: specifier, external: true }));
  },
};
Bun.plugin(pinnedPlugin);
const root = await fs.mkdtemp(path.join(os.tmpdir(), 'ccb-native-collab-'));
// Cache paths outside node_modules are intentionally treated as workspace
// builds by the native loader. Stage only its unmodified JS wrapper in a
// temporary normal install layout and symlink the exact release binary package.
nativePackageRoot = path.join(root, 'node_modules/@oh-my-pi/pi-natives');
await fs.mkdir(nativePackageRoot, { recursive: true });
const nativeSource = path.join(packageCache, `pi-natives@${packageInfo.version}@@@1`);
await fs.copyFile(path.join(nativeSource, 'package.json'), path.join(nativePackageRoot, 'package.json'));
await fs.cp(path.join(nativeSource, 'native'), path.join(nativePackageRoot, 'native'), { recursive: true });
await fs.symlink(path.join(packageCache, `pi-natives-${process.platform}-${process.arch}@${packageInfo.version}@@@1`),
  path.join(root, `node_modules/@oh-my-pi/pi-natives-${process.platform}-${process.arch}`), 'dir');
// Bun createRequire package resolution may bypass runtime resolver hooks.
// Native-directory candidates are unconditional: link the real release addons
// there as well, without copying binaries or touching installed packages.
const platformRoot = path.join(packageCache, `pi-natives-${process.platform}-${process.arch}@${packageInfo.version}@@@1`);
for (const filename of await fs.readdir(platformRoot)) {
  if (filename.endsWith('.node')) await fs.symlink(path.join(platformRoot, filename), path.join(nativePackageRoot, 'native', filename));
}
process.env.HOME = root;
process.env.PI_CODING_AGENT_DIR = path.join(root, '.omp/agent');
delete process.env.PI_CONFIG_DIR;
delete process.env.OMP_PROFILE;
delete process.env.PI_PROFILE;
// Direct physical import guarantees the supported installed loader runs from
// node_modules before any upstream module can trigger a bare native import.
const nativeExports = await import(pathToFileURL(path.join(nativePackageRoot, 'native/index.js')).href);
// Some upstream dependency loaders use require(), bypassing Bun onResolve.
// Re-export the already loaded real addon-backed module, never a fake native API.
mock.module('@oh-my-pi/pi-natives', () => nativeExports);
mock.module(path.join(nativeSource, 'native/index.js'), () => nativeExports);
mock.module(path.resolve(source, '../../../global/node_modules/@oh-my-pi/pi-natives/native/index.js'), () => nativeExports);
const entrypoint = path.join(root, 'upstream-entry.ts');
await fs.writeFile(entrypoint, `export { CollabHost } from ${JSON.stringify(path.join(source, 'src/collab/host.ts'))};\nexport { SessionManager } from ${JSON.stringify(path.join(source, 'src/session/session-manager.ts'))};\nexport { Settings } from ${JSON.stringify(path.join(source, 'src/config/settings.ts'))};\nexport { collabHostsRuntimeDir } from ${JSON.stringify(path.join(source, 'src/collab/registry.ts'))};`);
const bundled = await Bun.build({ entrypoints: [entrypoint], outdir: path.join(root, 'bundle'), target: 'bun', plugins: [pinnedPlugin] });
if (!bundled.success) throw new AggregateError(bundled.logs, 'Cannot bundle pinned upstream fixture');
await fs.symlink(path.resolve(source, '../../../global/node_modules'), path.join(root, 'bundle/node_modules'), 'dir');
const { CollabHost, SessionManager, Settings, collabHostsRuntimeDir } = await import(pathToFileURL(bundled.outputs[0].path).href);
const manager = SessionManager.inMemory(root);
const listeners = new Set();
let prompts = 0;
const session = {
  isStreaming: false, isAborting: false, isSessionTransitioning: false,
  queuedMessageCount: 0, sessionName: 'local smoke', model: null, thinkingLevel: 'off',
  subscribe: fn => { listeners.add(fn); return () => listeners.delete(fn); },
  emitNotice() {},
  async promptCustomMessage(message) {
    prompts++;
    manager.appendCustomMessageEntry(message.customType, message.content, message.display, message.details, message.attribution);
    return true;
  },
};
const noop = () => {};
const ctx = {
  session, sessionManager: manager, settings: Settings.isolated(),
  statusLine: { setCollabStatus: noop, invalidate: noop, getCachedContextBreakdown: () => ({ usedTokens: 0, contextWindow: 1000 }) },
  ui: { requestRender: noop }, showStatus: noop, updatePendingMessagesDisplay: noop,
};
const relay = startRelay();
const host = new CollabHost(ctx);
const guest = new ManagedGuest({ cwd: root }, relay);
try {
  await host.start(relay.url);
  // Discovery uses the actual upstream private registry, not invented frames.
  guest.binding.registry_dir = collabHostsRuntimeDir();
  const editor = path.join(root, 'editor.sock');
  const net = await import('node:net');
  const server = net.createServer(socket => socket.once('data', () => socket.end(JSON.stringify({ pid: process.pid, session_id: manager.getSessionId(), state: 'empty' }) + '\n')));
  await new Promise(resolve => server.listen(editor, resolve));
  guest.binding.editor_socket = editor;
  try {
    await guest.sync();
    assert.equal(guest.ready, true);
    assert.equal(guest.snapshot().session_id, manager.getSessionId());
    const request = { body: 'local test attachment /tmp/not-uploaded.txt', idempotency_key: 'native-smoke', expected_session_id: manager.getSessionId(), expected_generation: guest.snapshot().generation };
    const result = await guest.submit(request);
    assert.equal(result.state, 'unconfirmed');
    for (let i = 0; prompts === 0 && i < 100; i++) await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(prompts, 1);
    for (let i = 0; guest.attempts.get('native-smoke').result.state !== 'sent' && i < 100; i++) await new Promise(resolve => setTimeout(resolve, 10));
    const duplicate = await guest.submit(request);
    assert.equal(duplicate.request_id, result.request_id);
    assert.equal(duplicate.state, 'sent');
    assert.ok(duplicate.entry_id);
    assert.equal(prompts, 1);
    const message = { role: 'assistant', content: [{ type: 'text', text: 'live local text' }], timestamp: Date.now() };
    for (const fn of listeners) fn({ type: 'message_update', message, assistantMessageEvent: { type: 'text_delta', contentIndex: 0, delta: 'live local text', partial: message } });
    for (let i = 0; !guest.streaming && i < 100; i++) await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(guest.streaming.content[0].text, 'live local text');
    assert.ok(guest.streamId);
    manager.appendMessage(message);
    for (let i = 0; guest.streaming && i < 100; i++) await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(guest.streaming, null);
    for (const fn of listeners) fn({ type: 'message_end', message });
    const next = { ...message, timestamp: Date.now() + 1 };
    for (const fn of listeners) fn({ type: 'message_start', message: next });
    for (const fn of listeners) fn({ type: 'message_end', message: next });
    manager.appendMessage(next);
    for (let i = 0; guest.entries.filter(entry => entry.type === 'message' && entry.message?.role === 'assistant').length < 2 && i < 100; i++) await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(guest.streaming, null);
    assert.equal(guest.entries.filter(entry => entry.type === 'message' && entry.message?.role === 'assistant').length, 2);
    assert.deepEqual(guest.snapshot().stream_entry_ids, {});
    await host.stop();
    for (let i = 0; guest.connected && i < 100; i++) await new Promise(resolve => setTimeout(resolve, 10));
    assert.equal(guest.connected, false);
    console.log('native CollabHost smoke passed: snapshot, prompt, idempotency, live text, disconnect; zero model calls');
  } finally { server.close(); }
} finally {
  guest.reset(); await host.stop(); relay.close();
  await fs.rm(root, { recursive: true, force: true });
}
