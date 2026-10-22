import { test, expect } from 'bun:test';
import { ManagedGuest, seal, open, createProcessGroupStopper } from '../lib/provider_backends/omp/collab_runtime.mjs';
import { randomBytes } from 'node:crypto';
import fs from 'node:fs/promises';
import { spawn } from 'node:child_process';

function readyGuest() {
  const guest = new ManagedGuest({}, { url: 'ws://127.0.0.1:1' });
  guest.host = { instanceId: 'owner', generation: 1, sessionId: 'native' };
  guest.receive({ t: 'welcome', proto: 3, header: { id: 'native' }, state: { isStreaming: false } });
  guest.receive({ t: 'snapshot-chunk', entries: [], final: true });
  return guest;
}
test('upstream envelope encryption authenticates native frames', () => {
  const key = randomBytes(32), frame = { t: 'prompt', text: 'hello /tmp/attachment.txt' };
  const encrypted = seal(key, frame);
  expect(open(key, encrypted)).toEqual(frame);
  encrypted[20] ^= 1;
  expect(() => open(key, encrypted)).toThrow();
});
test('snapshot barrier rejects premature live entries', () => {
  const guest = new ManagedGuest({}, {});
  expect(() => guest.receive({ t: 'entry', entry: { id: 'x' } })).toThrow();
});
test('native live identity stays stable without guessing durable identity', () => {
  const guest = readyGuest();
  const message = { role: 'assistant', content: [{ type: 'text', text: 'live' }], timestamp: 123 };
  guest.receive({ t: 'event', event: { type: 'message_update', message } });
  const id = guest.snapshot().stream_id;
  expect(id).toBeTruthy();
  guest.receive({ t: 'event', event: { type: 'message_update', message: { ...message, content: [{ type: 'text', text: 'live text' }] } } });
  expect(guest.snapshot().stream_id).toBe(id);
  guest.receive({ t: 'event', event: { type: 'message_end', message } });
  guest.receive({ t: 'entry', entry: { type: 'message', id: 'durable', message } });
  expect(guest.snapshot().stream_entry_ids[id]).toBeUndefined();
  expect(guest.snapshot().streaming_message).toBeNull();
});
test('submit idempotency never replays and conflicting content rejects', async () => {
  const guest = readyGuest();
  guest.attempts.set('k', { digest: 'different', result: { state: 'unconfirmed' } });
  await expect(guest.submit({ idempotency_key: 'k', body: 'new' })).rejects.toThrow('conflict');
});
test('missing protocol and wrong-session welcome fail safely', () => {
  const guest = readyGuest();
  expect(() => guest.receive({ t: 'welcome', proto: 2, header: { id: 'native' } })).toThrow();
  expect(() => guest.receive({ t: 'welcome', proto: 3, header: { id: 'wrong' } })).toThrow();
});
test('live durable assistant supersedes stream before message_end', () => {
  const guest = readyGuest();
  const message = { role: 'assistant', content: [{ type: 'text', text: 'final' }] };
  guest.receive({ t: 'event', event: { type: 'message_start', message } });
  expect(guest.snapshot().stream_id).toBeTruthy();
  guest.receive({ t: 'entry', entry: { type: 'message', id: 'durable', message } });
  expect(guest.snapshot().streaming_message).toBeNull();
  guest.receive({ t: 'event', event: { type: 'message_end', message } });
  expect(guest.snapshot().entries).toHaveLength(1);
  expect(guest.snapshot().stream_entry_ids).toEqual({});
});
test('failed owner refresh closes an open replica and idle refresh recovers', async () => {
  const guest = readyGuest();
  let closes = 0, unavailable = true;
  guest.ws = { readyState: 1, close() { closes++; } };
  guest.sync = async () => {
    if (unavailable) throw Error('owner observation unavailable');
    if (guest.ws?.readyState === 1) return;
    guest.receive({ t: 'welcome', proto: 3, header: { id: 'native' }, state: { isStreaming: false } });
    guest.receive({ t: 'snapshot-chunk', entries: [], final: true });
  };
  expect((await guest.refreshSnapshot()).ready).toBe(false);
  expect(guest.ws).toBeNull();
  expect(closes).toBe(1);
  await guest.refreshSnapshot();
  expect(closes).toBe(1);
  unavailable = false;
  const recovered = await guest.refreshSnapshot();
  expect(recovered.ready).toBe(true);
  expect(recovered.connected).toBe(true);
  expect(recovered.generation).toBe('owner:1');
});

test('group stopper awaits escalation after wrapper exits and is idempotent', async () => {
  const descendantCode = `process.on('SIGTERM', () => {}); console.log('READY ' + process.pid); setInterval(() => {}, 1000);`;
  const wrapperCode = `require('node:child_process').spawn(process.execPath, ['-e', ${JSON.stringify(descendantCode)}], { stdio: ['ignore', 'inherit', 'inherit'] }); process.on('SIGTERM', () => process.exit(0)); setInterval(() => {}, 1000);`;
  const wrapper = spawn(process.execPath, ['-e', wrapperCode], { detached: true, stdio: ['ignore', 'pipe', 'pipe'] });
  const exited = new Promise(resolve => wrapper.once('exit', resolve));
  try {
    const descendant = await new Promise((resolve, reject) => {
      let output = '';
      const deadline = setTimeout(() => reject(Error('descendant did not become ready')), 1500);
      wrapper.once('error', error => { clearTimeout(deadline); reject(error); });
      wrapper.stdout.on('data', data => {
        output += data.toString();
        const match = /READY (\d+)/.exec(output);
        if (match) { clearTimeout(deadline); resolve(Number(match[1])); }
      });
    });
    const stop = createProcessGroupStopper(() => wrapper.pid, 100);
    const first = stop('SIGTERM');
    expect(stop('SIGHUP')).toBe(first);
    await exited;
    await first;
    let live = true;
    for (let index = 0; live && index < 50; index++) {
      try {
        if (process.platform === 'linux') live = !/\) Z /.test(await fs.readFile(`/proc/${descendant}/stat`, 'utf8'));
        else {
          process.kill(descendant, 0);
          live = true;
        }
      } catch (error) { if (error.code === 'ENOENT' || error.code === 'ESRCH') live = false; else throw error; }
      if (live) await new Promise(resolve => setTimeout(resolve, 10));
    }
    expect(live).toBe(false);
    expect(stop('SIGTERM')).toBe(first);
  } finally {
    try { process.kill(-wrapper.pid, 'SIGKILL'); } catch {}
  }
});

function queueGuest(binding = {}) {
  const guest = readyGuest(), sent = [];
  guest.binding = binding;
  guest.key = randomBytes(32);
  guest.ws = { readyState: 1, close() {}, send(data) { sent.push(open(guest.key, data)); } };
  const observed = { host: { ...guest.host, busy: true, inputRequired: false }, editor: { state: 'unknown', reason: 'provider_busy', session_id: 'native' } };
  guest.observation = observed;
  guest.sync = async () => {};
  guest.discover = async () => observed;
  const request = (key, body = key) => ({ idempotency_key: key, body, expected_session_id: 'native', expected_generation: 'owner:1' });
  const idle = () => { observed.host.busy = false; observed.editor.state = 'empty'; guest.receive({ t: 'state', state: { isStreaming: false } }); };
  const start = id => {
    guest.receive({ t: 'entry', entry: { id, type: 'custom_message', customType: 'collab-prompt', content: 'not matched by text', details: { from: 'CCB Mobile' } } });
    observed.host.busy = true;
    guest.receive({ t: 'event', event: { type: 'agent_start' } });
    guest.receive({ t: 'state', state: { isStreaming: true } });
  };
  return { guest, sent, observed, request, idle, start };
}

test('busy sends queue serially, preserve duplicate receipt and dispatch FIFO without overlap', async () => {
  const { guest, sent, request, idle, start } = queueGuest();
  const receipts = await Promise.all(['one', 'two', 'three'].map(key => guest.submit(request(key))));
  expect(receipts.map(r => r.state)).toEqual(['queued', 'queued', 'queued']);
  expect(receipts.map(r => r.queue_position)).toEqual([1, 2, 3]);
  expect(await guest.submit(request('two'))).toEqual(receipts[1]);
  expect(guest.snapshot().pending_inputs.map(a => a.body)).toEqual(['one', 'two', 'three']);
  expect(sent).toHaveLength(0);
  idle(); await guest.drain();
  expect(sent.map(a => a.text)).toEqual(['one']);
  await guest.drain();
  expect(sent).toHaveLength(1); // Idle alone is not proof that a handoff ran.
  start('native-one'); await guest.drain();
  expect(sent).toHaveLength(1);
  idle(); await guest.drain();
  expect(sent.map(a => a.text)).toEqual(['one', 'two']);
  start('native-two'); idle(); await guest.drain();
  expect(sent.map(a => a.text)).toEqual(['one', 'two', 'three']);
  start('native-three'); idle(); await guest.drain();
  expect(guest.snapshot().pending_inputs).toEqual([]);
  expect(guest.snapshot().input_bindings).toEqual(['one', 'two', 'three'].map(key => ({
    entry_id: `native-${key}`, idempotency_key: key, session_id: 'native', generation: 'owner:1',
  })));
  expect((await guest.submit(request('one'))).entry_id).toBe('native-one');
  expect(sent).toHaveLength(3);
});

test('draft and input required block drain, not durable queue admission', async () => {
  const { guest, sent, observed, request, idle } = queueGuest();
  idle(); observed.editor.state = 'nonempty';
  expect((await guest.submit(request('draft'))).state).toBe('queued');
  observed.editor.state = 'empty'; observed.host.inputRequired = true;
  await guest.drain(); expect(sent).toHaveLength(0);
  observed.host.inputRequired = false;
  await guest.drain(); expect(sent).toHaveLength(1);
});

test('disconnect retains unknown and holds unsent inputs without replay after reconnect', async () => {
  const { guest, sent, request, idle } = queueGuest();
  await guest.submit(request('one')); await guest.submit(request('two'));
  idle(); await guest.drain();
  guest.receive({ t: 'bye' });
  expect(guest.snapshot().pending_inputs.map(a => a.state)).toEqual(['unconfirmed', 'held']);
  guest.connected = true; guest.ready = true;
  await guest.drain();
  expect(sent).toHaveLength(1);
  expect((await guest.submit(request('one'))).state).toBe('unconfirmed');
});

test('generation change holds queued inputs instead of sending into new session', async () => {
  const { guest, sent, request, idle } = queueGuest();
  await guest.submit(request('old'));
  guest.host.generation++; idle(); await guest.drain();
  expect(guest.snapshot().pending_inputs[0].state).toBe('held');
  expect(sent).toHaveLength(0);
});

test('bounded queue rejects overflow without losing admitted bodies', async () => {
  const { guest, request } = queueGuest();
  for (let i = 0; i < 64; i++) await guest.submit(request(String(i)));
  await expect(guest.submit(request('overflow'))).rejects.toThrow('capacity');
  expect(guest.snapshot().pending_inputs).toHaveLength(64);
  await expect(guest.submit(request('large', 'x'.repeat(256 * 1024 + 1)))).rejects.toThrow('too large');
  expect((await guest.submit(request('0'))).state).toBe('queued');
});

test('persistent queued bodies are owner-only and restart held, unknown remains unknown', async () => {
  const root = await fs.mkdtemp('/tmp/ccb-queue-test-');
  try {
    const binding = { attempt_log: root + '/attempts.jsonl' };
    const { guest, request, idle } = queueGuest(binding);
    await guest.submit(request('first')); await guest.submit(request('second'));
    expect((await fs.stat(binding.attempt_log)).mode & 0o077).toBe(0);
    idle(); await guest.drain();
    const recovered = new ManagedGuest(binding, {});
    await recovered.restore();
    expect(recovered.snapshot().pending_inputs.map(a => a.state)).toEqual(['unconfirmed', 'held']);
    expect((await recovered.submit(request('first'))).request_id).toBe(guest.attempts.get('first').result.request_id);
  } finally { await fs.rm(root, { recursive: true, force: true }); }
});

test('body byte budget rejects overflow without dropping persisted queue', async () => {
  const { guest, request } = queueGuest();
  for (let i = 0; i < 4; i++) await guest.submit(request(String(i), 'x'.repeat(256 * 1024)));
  await expect(guest.submit(request('overflow', 'x'))).rejects.toThrow('capacity');
  expect(guest.snapshot().pending_inputs.map(a => a.idempotency_key)).toEqual(['0', '1', '2', '3']);
});

test('entry evidence alone cannot release next input without start and stop', async () => {
  const { guest, sent, request, idle } = queueGuest();
  await guest.submit(request('one')); await guest.submit(request('two'));
  idle(); await guest.drain();
  guest.receive({ t: 'entry', entry: { id: 'native-one', type: 'custom_message', customType: 'collab-prompt', content: 'one' } });
  idle(); await guest.drain();
  expect(sent).toHaveLength(1);
  guest.receive({ t: 'event', event: { type: 'agent_start' } });
  await guest.drain(); expect(sent).toHaveLength(1);
  guest.receive({ t: 'event', event: { type: 'agent_end' } });
  await guest.drain(); expect(sent).toHaveLength(2);
});

test('failed queue persistence never sends or evicts earlier input', async () => {
  const { guest, sent, request, idle } = queueGuest();
  const first = await guest.submit(request('first'));
  guest.persist = async () => { throw Error('journal full'); };
  await expect(guest.submit(request('second'))).rejects.toThrow('journal full');
  expect(guest.snapshot().pending_inputs.map(a => a.request_id)).toEqual([first.request_id]);
  idle(); await expect(guest.drain()).rejects.toThrow('journal full');
  expect(sent).toHaveLength(0);
  expect(guest.snapshot().pending_inputs[0].state).toBe('held');
});

test('model mutation refuses pending inputs before contacting editor', async () => {
  const { guest, request } = queueGuest();
  await guest.submit(request('queued'));
  await expect(guest.models({}, true)).rejects.toThrow('inputs pending');
});
