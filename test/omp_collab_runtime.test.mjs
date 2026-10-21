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
