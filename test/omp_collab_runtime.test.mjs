import { test, expect } from 'bun:test';
import { ManagedGuest, seal, open, createProcessGroupStopper, MAX_RECENT_BYTES, MAX_RECENT_ENTRIES, MAX_RUNTIME_ROWS, MAX_RUNTIME_STRING, MAX_RUNTIME_BYTES } from '../lib/provider_backends/omp/collab_runtime.mjs';
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
test('native status survives guest attachment only for the observed owner and session', () => {
  const guest = readyGuest();
  expect(guest.runtimeSnapshot().status_bar).toEqual({ available: false });
  const bar = { available: true, source: 'native_events', session_id: 'native',
    observed_at: '2026-10-23T10:00:01.000Z', is_streaming: true,
    started_at: '2026-10-23T09:59:00.000Z', elapsed_ms: 61000, step: 'Reading native state',
    todos: { available: true, done: 1, total: 3, in_progress: 1, blocked: 0, items: [], truncated: false } };
  guest.observation = { host: { ...guest.host }, editor: { session_id: 'native', status_bar: bar } };
  let runtime = guest.runtimeSnapshot();
  expect(runtime.status_bar).toEqual(bar);
  expect(runtime.is_streaming).toBe(true);
  expect(runtime.started_at).toBe(bar.started_at);
  expect(guest.runtimeSnapshot().status_bar.elapsed_ms).toBe(61000);
  for (const mutate of [observation => observation.host.generation++,
    observation => observation.editor.session_id = 'other',
    observation => observation.editor.status_bar.session_id = 'other']) {
    guest.observation = { host: { ...guest.host }, editor: { session_id: 'native', status_bar: structuredClone(bar) } };
    mutate(guest.observation);
    expect(guest.runtimeSnapshot().status_bar).toEqual({ available: false });
  }
  guest.observation = { host: { ...guest.host }, editor: { session_id: 'native', status_bar: bar } };
  guest.connected = false;
  expect(guest.runtimeSnapshot().status_bar).toEqual({ available: false });
});
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

test('large native snapshot drains through final barrier with bounded recent replica', () => {
  const guest = readyGuest();
  guest.receive({ t: 'welcome', proto: 3, header: { id: 'native' }, state: { isStreaming: false } });
  const text = 'x'.repeat(2048);
  for (let index = 0; index < 28001; index++) {
    guest.receive({ t: 'snapshot-chunk', entries: [{ id: String(index), parentId: index ? String(index - 1) : null,
      type: 'message', message: { role: index % 2 ? 'assistant' : 'user', content: text } }], final: false });
    expect(guest.bytes).toBeLessThanOrEqual(MAX_RECENT_BYTES);
    expect(guest.entries.length).toBeLessThanOrEqual(MAX_RECENT_ENTRIES);
  }
  expect(guest.ready).toBe(false);
  guest.receive({ t: 'snapshot-chunk', entries: [], final: true });
  expect(guest.ready).toBe(true);
  expect(guest.entries.at(-1).id).toBe('28000');
  expect(guest.entryIndex.size).toBe(guest.entries.length);
  expect(guest.snapshot().history_complete).toBe(false);
  expect(guest.snapshot().recent_truncated).toBe(true);
  guest.receive({ t: 'entry', entry: { id: '28001' } });
  expect(guest.entries.at(-1).id).toBe('28001');
  expect(guest.snapshot().state.leafId).toBe('28001');
});

test('ancestry page failure preserves healthy socket and reports history error', async () => {
  const guest = readyGuest();
  guest.binding.editor_socket = '/unused';
  let closes = 0;
  const ws = guest.ws = { readyState: 1, close() { closes++; } };
  guest.sync = async () => {};
  guest.nativePage = async () => { throw Error('ancestry unavailable'); };
  for (let index = 0; index < 3; index++) {
    const snapshot = await guest.refreshSnapshot();
    expect(snapshot.connected).toBe(true);
    expect(snapshot.ready).toBe(true);
    expect(snapshot.history_error).toBe('ancestry unavailable');
    expect(guest.ws).toBe(ws);
  }
  expect(closes).toBe(0);
  guest.nativePage = async () => ({ parent_links: [['latest', 'older']], branch_id: 'runtime:9', leaf_id: 'latest' });
  const recovered = await guest.refreshSnapshot();
  expect(recovered.history_error).toBeNull();
  expect(recovered.state.leafId).toBe('latest');
  expect(recovered.branch_id).toBe('runtime:9');
});

test('history forwards bounded native pages and byte chunks without claiming recent replica complete', async () => {
  const guest = readyGuest();
  guest.sync = async () => {};
  const chunk = { entry_id: 'large', encoding: 'base64', offset: 0, total_bytes: 9999999, data: 'e30=' };
  guest.nativePage = async (operation, request) => {
    expect(operation).toBe('history_page');
    expect(request.cursor).toBe('next');
    return { entries: [], entry_chunks: [chunk], branch_id: 'runtime:9', leaf_id: 'latest',
      anchor_leaf_id: 'anchor', start_cursor: 'start', next_cursor: 'continue', has_more: true };
  };
  const page = await guest.history({ cursor: 'next', limit: 128, expected_session_id: 'native', expected_generation: 'owner:1' });
  expect(page.entry_chunks).toEqual([chunk]);
  expect(page.has_more).toBe(true);
  expect(page.history_complete).toBe(false);
  expect(page.start_cursor).toBe('start');
  expect(page.next_cursor).toBe('continue');
  expect(guest.entries).toHaveLength(0);
  await expect(guest.history({ expected_session_id: 'wrong' })).rejects.toThrow('conversation changed');
  await expect(guest.history({ limit: 129 })).rejects.toThrow('Invalid OMP history page');
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

const taskFrame = (guest, data, lifecycle = false) => guest.receive({ t: 'bus',
  channel: `task:subagent:${lifecycle ? 'lifecycle' : 'progress'}`, data });

test('runtime exposes only bounded native state and replaces agent snapshots', () => {
  const guest = readyGuest();
  const agent = { id: 'child', displayName: 'Worker', kind: 'sub', parentId: 'main', status: 'running',
    createdAt: 10, lastActivity: 20, hasSessionFile: true, sessionFile: '/private/session.json', transcript: 'secret' };
  const state = { isStreaming: true, isAborting: true, queuedMessageCount: 3,
    model: { provider: 'anthropic', id: 'model', name: 'Name', apiKey: 'secret' }, thinkingLevel: 'high',
    contextUsage: { tokens: 100, contextWindow: 1000, percent: 10 }, cwd: '/private' };
  guest.receive({ t: 'welcome', proto: 3, header: { id: 'native' }, agents: [agent], state });
  expect(guest.snapshot().runtime.ready).toBe(false);
  guest.receive({ t: 'snapshot-chunk', entries: [], final: true });
  const runtime = guest.snapshot().runtime;
  expect(runtime).toMatchObject({ connected: true, ready: true, is_streaming: true, is_aborting: true,
    queued_message_count: 3, started_at: null, model: { provider: 'anthropic', id: 'model', name: 'Name' },
    thinking_level: 'high', context_usage: { tokens: 100, contextWindow: 1000, percent: 10 }, truncated: false });
  expect(runtime.agents).toEqual([{ id: 'child', displayName: 'Worker', kind: 'sub', parentId: 'main',
    status: 'running', createdAt: 10, lastActivity: 20, hasSessionFile: true }]);
  expect(JSON.stringify(runtime)).not.toContain('secret');
  expect(JSON.stringify(runtime)).not.toContain('/private');
  guest.receive({ t: 'agents', agents: [] });
  expect(guest.snapshot().runtime.agents).toEqual([]);
});

test('runtime main activity uses native flags and only observed agent_start starts clock', () => {
  const guest = readyGuest();
  taskFrame(guest, { index: 0, agent: 'worker', progress: { id: 'background', durationMs: 42 } });
  expect(guest.snapshot().runtime.is_streaming).toBe(false);
  guest.receive({ t: 'state', state: { isStreaming: true } });
  guest.receive({ t: 'event', event: { type: 'turn_start' } });
  expect(guest.snapshot().runtime.started_at).toBeNull();
  guest.receive({ t: 'event', event: { type: 'agent_start' } });
  const started = guest.snapshot().runtime.started_at;
  expect(Number.isFinite(Date.parse(started))).toBe(true);
  guest.receive({ t: 'event', event: { type: 'message_start', message: { role: 'assistant' } } });
  expect(guest.snapshot().runtime.started_at).toBe(started);
  guest.receive({ t: 'event', event: { type: 'agent_end' } });
  expect(guest.snapshot().runtime.started_at).toBeNull();
  expect(guest.snapshot().runtime.is_streaming).toBe(false);
  expect(guest.snapshot().runtime.tasks[0].status).toBe('running');
});

test('task identity is progress.id and only explicit lifecycle start revives terminal work', () => {
  const guest = readyGuest();
  const one = { agent: 'worker', index: 0, parentToolCallId: 'parent-one', detached: true,
    task: 'private prompt', sessionFile: '/private', progress: { id: 'one', status: 'completed',
      description: 'Inspect runtime', durationMs: 12, currentTool: 'read', currentToolArgs: '/private', recentOutput: ['secret'] } };
  taskFrame(guest, one);
  taskFrame(guest, { ...one, parentToolCallId: 'parent-two', progress: { id: 'two', durationMs: 13 } });
  let tasks = guest.snapshot().runtime.tasks;
  expect(tasks.map(task => task.id)).toEqual(['one', 'two']);
  expect(tasks[0]).toMatchObject({ status: 'running', parent_tool_call_id: 'parent-one', index: 0,
    description: 'Inspect runtime', duration_ms: 12, tool_name: 'read', detached: true });
  expect(tasks[0].started_at).toBeUndefined();
  taskFrame(guest, { id: 'one', agent: 'worker', index: 0, status: 'started' }, true);
  expect(guest.snapshot().runtime.tasks[0].status).toBe('started');
  expect(guest.snapshot().runtime.tasks[0].started_at).toBeTruthy();
  taskFrame(guest, { id: 'one', agent: 'worker', index: 0, status: 'completed' }, true);
  taskFrame(guest, one);
  expect(guest.snapshot().runtime.tasks[0].status).toBe('completed');
  taskFrame(guest, { id: 'one', agent: 'worker', index: 0, status: 'started' }, true);
  tasks = guest.snapshot().runtime.tasks;
  expect(tasks[0].status).toBe('started');
  expect(tasks[0].started_at).toBe(tasks[0].updated_at);
  expect(tasks[0].duration_ms).toBeUndefined();
  expect(tasks[0].tool_name).toBeUndefined();
  expect(tasks[1].status).toBe('running');
  expect(JSON.stringify(tasks)).not.toContain('/private');
  expect(JSON.stringify(tasks)).not.toContain('secret');
  for (const status of ['failed', 'aborted']) {
    taskFrame(guest, { id: status, agent: 'worker', index: 0, status }, true);
    taskFrame(guest, { ...one, progress: { id: status } });
    expect(guest.snapshot().runtime.tasks.find(task => task.id === status).status).toBe(status);
  }
});

test('runtime bounds rows, strings and terminal tombstones without identity aliasing', () => {
  const guest = readyGuest(), long = 'x'.repeat(MAX_RUNTIME_STRING + 1);
  guest.receive({ t: 'agents', agents: Array.from({ length: MAX_RUNTIME_ROWS + 10 }, (_, index) => ({ id: String(index), displayName: long })) });
  expect(guest.runtimeAgents).toHaveLength(MAX_RUNTIME_ROWS);
  expect(guest.snapshot().runtime.agents[0].displayName).toHaveLength(MAX_RUNTIME_STRING);
  taskFrame(guest, { id: 'terminal', agent: long, status: 'completed', description: long }, true);
  for (let index = 0; index < MAX_RUNTIME_ROWS + 10; index++) taskFrame(guest, { agent: long, index,
    progress: { id: String(index), description: long, currentTool: long } });
  taskFrame(guest, { agent: 'worker', progress: { id: 'terminal' } });
  const runtime = guest.snapshot().runtime;
  expect(guest.runtimeTasks.size).toBe(MAX_RUNTIME_ROWS);
  expect(guest.runtimeTasks.has('terminal')).toBe(false);
  expect(guest.runtimeTaskTombstones.has('terminal')).toBe(true);
  expect(guest.runtimeTasks.get('0').description).toHaveLength(MAX_RUNTIME_STRING);
  expect(Buffer.byteLength(JSON.stringify(runtime))).toBeLessThanOrEqual(MAX_RUNTIME_BYTES);
  expect(runtime.tasks.every(task => task.status === 'running')).toBe(true);
  expect(runtime.truncated).toBe(true);
  const other = readyGuest();
  taskFrame(other, { agent: 'worker', progress: { id: long } });
  expect(other.snapshot().runtime.tasks).toEqual([]);
  expect(other.snapshot().runtime.truncated).toBe(true);
});

test('task admission evicts old terminal rows and continues after many completed tasks', () => {
  const guest = readyGuest();
  for (let index = 0; index < MAX_RUNTIME_ROWS * 6; index++) {
    taskFrame(guest, { id: `task-${index}`, agent: 'worker', index, status: 'started' }, true);
    expect(guest.runtimeTasks.get(`task-${index}`).status).toBe('started');
    taskFrame(guest, { id: `task-${index}`, agent: 'worker', index, status: 'completed' }, true);
  }
  expect(guest.runtimeTasks.size).toBe(MAX_RUNTIME_ROWS);
  expect(guest.runtimeTaskTombstones.size).toBeLessThanOrEqual(MAX_RUNTIME_ROWS * 4);
  const evicted = `task-${MAX_RUNTIME_ROWS * 4}`;
  expect(guest.runtimeTasks.has(evicted)).toBe(false);
  taskFrame(guest, { agent: 'worker', progress: { id: evicted } });
  expect(guest.runtimeTasks.has(evicted)).toBe(false);
  taskFrame(guest, { id: evicted, agent: 'worker', status: 'started' }, true);
  expect(guest.runtimeTasks.get(evicted).status).toBe('started');
  expect(guest.runtimeTasks.get(evicted).duration_ms).toBeUndefined();
  taskFrame(guest, { id: 'latest', agent: 'worker', status: 'started' }, true);
  expect(guest.snapshot().runtime.tasks.some(task => task.id === 'latest')).toBe(true);
  expect(guest.snapshot().runtime.truncated).toBe(true);
});

test('native agent selection retains active rows beyond terminal history bound', () => {
  const guest = readyGuest();
  const agents = Array.from({ length: MAX_RUNTIME_ROWS + 10 }, (_, index) => ({ id: `parked-${index}`, status: 'parked' }));
  agents.push({ id: 'late-active', displayName: 'Active', status: 'running' });
  guest.receive({ t: 'agents', agents });
  expect(guest.runtimeAgents).toHaveLength(MAX_RUNTIME_ROWS);
  expect(guest.runtimeAgents[0].id).toBe('late-active');
  expect(guest.snapshot().runtime.agents.some(agent => agent.id === 'late-active')).toBe(true);
  expect(guest.snapshot().runtime.truncated).toBe(true);
});

test('runtime rejects unrelated channels and resets across welcome, disconnect and generation', async () => {
  const { guest, request } = queueGuest();
  await guest.submit(request('queued'));
  guest.receive({ t: 'bus', channel: 'untrusted', data: { id: 'bad', agent: 'bad', status: 'started' } });
  expect(guest.snapshot().runtime.tasks).toEqual([]);
  taskFrame(guest, { id: 'old', agent: 'worker', status: 'started' }, true);
  guest.receive({ t: 'state', state: { isStreaming: true, isAborting: true, queuedMessageCount: 4 } });
  guest.receive({ t: 'event', event: { type: 'agent_start' } });
  guest.receive({ t: 'bye', reason: 'closed' });
  expect(guest.snapshot().runtime).toMatchObject({ connected: false, ready: false, is_streaming: null,
    is_aborting: false, queued_message_count: 0, started_at: null });
  taskFrame(guest, { id: 'late', agent: 'worker', status: 'started' }, true);
  expect(guest.snapshot().runtime.tasks.map(task => task.id)).toEqual(['old']);
  expect(guest.snapshot().pending_inputs[0].state).toBe('held');
  guest.host.generation++;
  guest.reset();
  expect(guest.snapshot().runtime.tasks).toEqual([]);
  expect(guest.snapshot().runtime.agents).toEqual([]);
  expect(guest.snapshot().pending_inputs).toHaveLength(1);
  guest.receive({ t: 'welcome', proto: 3, header: { id: 'native' }, agents: [], state: { isStreaming: true } });
  guest.receive({ t: 'snapshot-chunk', entries: [], final: true });
  expect(guest.snapshot().runtime.started_at).toBeNull();
  taskFrame(guest, { id: 'new', agent: 'worker', status: 'started' }, true);
  guest.receive({ t: 'welcome', proto: 3, header: { id: 'native' }, agents: [], state: { isStreaming: false } });
  expect(guest.snapshot().runtime.tasks).toEqual([]);
});

test('missing and malformed runtime state stays explicitly unknown without fabricated metrics', () => {
  const guest = readyGuest();
  guest.receive({ t: 'state', state: { queuedMessageCount: -1, model: { provider: 'only' },
    contextUsage: { tokens: NaN, contextWindow: 10, percent: Infinity } } });
  expect(guest.snapshot().runtime).toMatchObject({ is_streaming: null, is_aborting: false,
    queued_message_count: 0, model: null, thinking_level: null, context_usage: null });
});

const activityEvent = (guest, event) => guest.receive({ t: 'event', event });

test('semantic tool updates arrive before result and concurrent calls retain identity and intent', () => {
  const guest = readyGuest();
  activityEvent(guest, { type: 'agent_start' });
  activityEvent(guest, { type: 'tool_execution_start', toolCallId: 'one', toolName: 'read', intent: 'Inspect schema', args: { i: 'fallback', secret: 'never expose' } });
  activityEvent(guest, { type: 'tool_execution_start', toolCallId: 'two', toolName: 'bash', args: { i: 'Compile project' } });
  activityEvent(guest, { type: 'tool_execution_update', toolCallId: 'one', toolName: 'read', partialResult: { content: [{ type: 'text', text: 'schema loaded' }], secret: 'never expose' } });
  let runtime = guest.snapshot().runtime;
  expect(runtime.progress_available).toBe(true);
  expect(runtime.activities.find(row => row.id === 'tool:one')).toMatchObject({ title: 'Inspect schema', text: 'schema loaded', status: 'running' });
  expect(runtime.activities.filter(row => row.status === 'running')).toHaveLength(2);
  expect(JSON.stringify(runtime)).not.toContain('never expose');
  activityEvent(guest, { type: 'tool_execution_end', toolCallId: 'one', toolName: 'read', result: {} });
  activityEvent(guest, { type: 'tool_execution_update', toolCallId: 'one', toolName: 'read', partialResult: { content: [{ type: 'text', text: 'detached final output' }] } });
  runtime = guest.snapshot().runtime;
  expect(runtime.activities.find(row => row.id === 'tool:one')).toMatchObject({ status: 'completed', text: 'detached final output' });
  expect(runtime.activities.find(row => row.id === 'tool:two').status).toBe('running');
  activityEvent(guest, { type: 'agent_end' });
  activityEvent(guest, { type: 'agent_start' });
  activityEvent(guest, { type: 'tool_execution_update', toolCallId: 'two', toolName: 'bash', partialResult: {} });
  expect(guest.snapshot().runtime.activities.find(row => row.id === 'tool:two').status).toBe('unknown');
  guest.receive({ t: 'bye' });
  expect(guest.snapshot().runtime.activities.every(row => row.status !== 'running')).toBe(true);
});

test('disconnect preserves background task evidence without claiming cancellation or tool success', () => {
  const guest = readyGuest();
  activityEvent(guest, { type: 'agent_start' });
  activityEvent(guest, { type: 'tool_execution_start', toolCallId: 'unconfirmed', toolName: 'read', intent: 'Inspect bridge' });
  taskFrame(guest, { id: 'background', agent: 'worker', status: 'started' }, true);
  guest.receive({ t: 'bye' });
  const runtime = guest.snapshot().runtime;
  expect(runtime.connected).toBe(false);
  expect(runtime.activities.find(row => row.id === 'tool:unconfirmed').status).toBe('unknown');
  expect(runtime.tasks.find(row => row.id === 'background').status).toBe('started');
});

test('runtime retains retries compaction notices and visible snippets without hidden blocks', () => {
  const guest = readyGuest();
  activityEvent(guest, { type: 'agent_start' });
  activityEvent(guest, { type: 'auto_retry_start', attempt: 2, maxAttempts: 3, errorMessage: 'Rate limited' });
  expect(guest.snapshot().runtime.activities[0]).toMatchObject({ kind: 'retry', status: 'running', title: 'Retrying 2/3', text: 'Rate limited' });
  activityEvent(guest, { type: 'auto_retry_end', success: false, finalError: 'Quota exhausted' });
  expect(guest.snapshot().runtime.activities[0]).toMatchObject({ status: 'failed', text: 'Quota exhausted' });
  activityEvent(guest, { type: 'auto_retry_start', attempt: 3, maxAttempts: 3 });
  activityEvent(guest, { type: 'auto_retry_end', success: true });
  activityEvent(guest, { type: 'auto_compaction_start', reason: 'threshold' });
  activityEvent(guest, { type: 'auto_compaction_end', aborted: true });
  activityEvent(guest, { type: 'notice', level: 'warning', message: 'Context nearly full' });
  for (let i = 0; i < 20; i++) activityEvent(guest, { type: 'message_update', message: { role: 'assistant', content: [
    { type: 'thinking', thinking: 'visible reasoning', signature: 'hidden signature' },
    { type: 'redacted_thinking', data: 'hidden reasoning' }, { type: 'text', text: 'x'.repeat(2000) + 'tail' }] } });
  const runtime = guest.snapshot().runtime;
  expect(runtime.activities).toHaveLength(5);
  expect(runtime.activities.find(row => row.kind === 'message').text).toHaveLength(768);
  expect(runtime.activities.find(row => row.kind === 'message').text.endsWith('tail')).toBe(true);
  expect(runtime.activities.find(row => row.kind === 'compaction').status).toBe('aborted');
  expect(runtime.activities.find(row => row.kind === 'retry').status).toBe('completed');
  expect(JSON.stringify(runtime)).not.toContain('hidden');
  expect(runtime.truncated).toBe(true);
});

test('task progress projects assignment intent stage output and supplied metrics only', () => {
  const guest = readyGuest();
  taskFrame(guest, { agent: 'worker', progress: { id: 'child', task: 'original', assignment: 'Inspect subsystem',
    currentTool: 'read', currentToolIntent: 'Read schema', lastIntent: 'Older intent', stage: 'Inspecting',
    recentOutput: ['a'.repeat(1000), 'Latest output'], completionPercent: 42, toolCount: 3, requests: 2,
    tokens: 900, contextTokens: 200, contextWindow: 1000, currentToolArgs: 'never expose' } });
  const task = guest.snapshot().runtime.tasks[0];
  expect(task).toMatchObject({ task: 'Inspect subsystem', intent: 'Read schema', stage: 'Inspecting', completion_percent: 42,
    tool_count: 3, requests: 2, tokens: 900, context_tokens: 200, context_window: 1000 });
  expect(task.progress_text).toHaveLength(768);
  expect(task.progress_text.endsWith('Latest output')).toBe(true);
  expect(guest.snapshot().runtime.is_streaming).toBe(false);
  taskFrame(guest, { agent: 'worker', progress: { id: 'child', lastIntent: 'Summarize', completionPercent: 101, tokens: Infinity } });
  expect(guest.snapshot().runtime.tasks[0].intent).toBe('Summarize');
  expect(guest.snapshot().runtime.tasks[0].completion_percent).toBeUndefined();
  expect(guest.snapshot().runtime.tasks[0].tokens).toBeUndefined();
  taskFrame(guest, { id: 'child', agent: 'worker', status: 'completed' }, true);
  expect(guest.snapshot().runtime.tasks[0].intent).toBeUndefined();
});

test('active semantic activity survives large old agent registry and bounded row selection', () => {
  const guest = readyGuest();
  guest.receive({ t: 'agents', agents: Array.from({ length: 128 }, (_, i) => ({ id: `old-${i}`, status: 'parked', displayName: 'x'.repeat(128) })) });
  activityEvent(guest, { type: 'agent_start' });
  for (let i = 0; i < 12; i++) activityEvent(guest, { type: 'tool_execution_start', toolCallId: `${i}`, toolName: 'read', intent: 'x'.repeat(512) });
  const runtime = guest.snapshot().runtime;
  expect(runtime.activities).toHaveLength(8);
  expect(runtime.activities[0].id).toBe('tool:11');
  expect(runtime.activities.every(row => row.status === 'running')).toBe(true);
  expect(Buffer.byteLength(JSON.stringify(runtime))).toBeLessThanOrEqual(MAX_RUNTIME_BYTES);
});

test('native UI requests are whitelisted and regular submit cannot answer them', async () => {
  const { guest, sent, request, idle } = queueGuest();
  idle();
  guest.receive({ t: 'ui-request', request: { reqId: 17, kind: 'select', title: 'Choose',
    options: ['One', { label: 'Two', description: 'Second', path: '/secret' }],
    initialIndex: 1, checkedIndices: [0], selectionMarker: '✓', helpText: 'Help', raw_args: '/secret' } });
  expect(guest.snapshot().runtime.pending_ui_request).toEqual({ req_id: 17, kind: 'select', title: 'Choose',
    options: [{ label: 'One' }, { label: 'Two', description: 'Second' }], initial_index: 1,
    checked_indices: [0], marker: '✓', help_text: 'Help', prefill: null });
  await expect(guest.submit(request('not-an-answer'))).rejects.toThrow('UI request pending');
  expect(guest.attempts.size).toBe(0);
  expect(sent).toEqual([]);
  guest.receive({ t: 'ui-request-end', reqId: 16 });
  expect(guest.snapshot().runtime.pending_ui_request.req_id).toBe(17);
  guest.receive({ t: 'ui-request-end', reqId: 17 });
  expect(guest.snapshot().runtime.pending_ui_request).toBeNull();
});

test('malformed and oversized native UI requests never become actionable', () => {
  const base = { reqId: 1, kind: 'select', title: 'Choose', options: ['one'] };
  for (const change of [{ reqId: true }, { reqId: -1 }, { reqId: 1.5 }, { kind: 'unknown' },
    { title: 'x'.repeat(513) }, { helpText: 'x'.repeat(513) }, { prefill: 'x'.repeat(513) },
    { options: ['x'.repeat(257)] }, { options: [{ label: 'one', description: 'x'.repeat(513) }] },
    { options: Array(17).fill('one') }, { initialIndex: 1 }, { checkedIndices: [2] },
    { checkedIndices: [0, 0] }, { options: [{ label: {} }] }]) {
    const guest = readyGuest();
    guest.receive({ t: 'ui-request', request: { ...base, ...change } });
    expect(guest.snapshot().runtime.pending_ui_request).toBeNull();
  }
  const guest = readyGuest();
  guest.receive({ t: 'ui-request', request: { ...base, title: 'x'.repeat(512), options: Array(16).fill('x'.repeat(256)) } });
  expect(guest.snapshot().runtime.pending_ui_request.options).toHaveLength(16);
});

test('UI response seals exact request without prompt, busy restart or replay', async () => {
  const { guest, sent } = queueGuest();
  guest.receive({ t: 'ui-request', request: { reqId: 21, kind: 'editor', title: 'Edit', prefill: 'original' } });
  const response = { req_id: 21, value: 'edited', expected_session_id: 'native', expected_generation: 'owner:1' };
  for (const change of [{ req_id: 20 }, { expected_session_id: 'stale' }, { expected_generation: 'owner:0' },
    { req_id: true }, { value: 'x'.repeat(513) }]) {
    await expect(guest.uiResponse({ ...response, ...change })).rejects.toThrow();
  }
  expect(sent).toEqual([]);
  expect(await guest.uiResponse(response)).toEqual({ req_id: 21 });
  expect(sent).toEqual([{ t: 'ui-response', reqId: 21, value: 'edited' }]);
  expect(guest.state.isStreaming).toBe(false);
  expect(guest.attempts.size).toBe(0);
  expect(guest.snapshot().runtime.pending_ui_request).toBeNull();
  await expect(guest.uiResponse(response)).rejects.toThrow('UI request changed');
  guest.receive({ t: 'ui-request', request: { reqId: 21, kind: 'editor', title: 'Edit' } });
  await expect(guest.uiResponse(response)).rejects.toThrow('UI request changed');
  expect(sent).toHaveLength(1);
});

test('UI cancellation omits value and unknown send outcome cannot be retried', async () => {
  const { guest, sent } = queueGuest();
  const response = { req_id: 3, value: null, expected_session_id: 'native', expected_generation: 'owner:1' };
  guest.receive({ t: 'ui-request', request: { reqId: 3, kind: 'editor', title: 'Edit' } });
  await guest.uiResponse(response);
  expect(sent).toEqual([{ t: 'ui-response', reqId: 3 }]);
  guest.receive({ t: 'ui-request-end', reqId: 3 });
  guest.receive({ t: 'ui-request', request: { reqId: 4, kind: 'editor', title: 'Edit' } });
  guest.ws.send = () => { throw Error('uncertain send'); };
  try { await guest.uiResponse({ ...response, req_id: 4 }); throw Error('unexpected success'); }
  catch (error) { expect(error.status_code).toBe(502); }
  await expect(guest.uiResponse({ ...response, req_id: 4 })).rejects.toThrow('UI request changed');
  guest.receive({ t: 'ui-request', request: { reqId: 5, kind: 'editor', title: 'Edit' } });
  guest.receive({ t: 'bye' });
  expect(guest.snapshot().runtime.pending_ui_request).toBeNull();
  await expect(guest.uiResponse({ ...response, req_id: 5 })).rejects.toThrow('disconnected');
  guest.reset();
  expect(guest.snapshot().runtime.pending_ui_request).toBeNull();
});

test('UI runtime prioritizes immutable option labels within escaped byte budget', () => {
  const guest = readyGuest();
  const labels = Array.from({ length: 16 }, (_, index) => `option-${index}`);
  guest.receive({ t: 'ui-request', request: { reqId: 70, kind: 'select', title: '題'.repeat(512),
    options: labels.map(label => ({ label, description: '説'.repeat(512) })), helpText: '助'.repeat(512) } });
  const runtime = guest.snapshot().runtime;
  expect(runtime.pending_ui_request.options.map(option => option.label)).toEqual(labels);
  expect(Buffer.byteLength(JSON.stringify(runtime))).toBeLessThanOrEqual(MAX_RUNTIME_BYTES);
  expect(runtime.truncated).toBe(true);
  guest.receive({ t: 'ui-request-end', reqId: 70 });
  guest.receive({ t: 'ui-request', request: { reqId: 71, kind: 'select', title: 'Choose',
    options: Array(16).fill('字'.repeat(256)) } });
  expect(guest.snapshot().runtime.pending_ui_request).toBeNull();
});
