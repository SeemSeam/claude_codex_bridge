// Local-only implementation of upstream OMP 18.12.6 Collab wire protocol v3.
// No Nozeli patches, admission extensions, or public relay are required.
import fs from 'node:fs/promises';
import path from 'node:path';
import net from 'node:net';
import { spawn } from 'node:child_process';
import { randomBytes, randomUUID, createCipheriv, createDecipheriv, createHash } from 'node:crypto';

export const MAX_FRAME = 2 * 1024 * 1024;
export const MAX_RECENT_BYTES = 4 * 1024 * 1024;
export const MAX_RECENT_ENTRIES = 512;
export const MAX_PENDING = 64;
export const MAX_PROMPT_BYTES = 256 * 1024;
const MAX_PENDING_BYTES = 1024 * 1024;
const MAX_JOURNAL = 4 * 1024 * 1024;
export const MAX_RUNTIME_ROWS = 128;
export const MAX_RUNTIME_STRING = 128;
export const MAX_RUNTIME_BYTES = 12 * 1024;
// Runtime is bounded semantic metadata, never arbitrary arguments or result JSON.
// Agent-registry rows are full replacement snapshots. Task progress identity is
// progress.id (not the spawn index); only lifecycle frames declare completion.
const TASK_CHANNELS = new Set(['task:subagent:progress', 'task:subagent:lifecycle']);
const TERMINAL_TASK_STATUSES = new Set(['completed', 'failed', 'aborted']);
const finiteNonnegative = value => typeof value === 'number' && Number.isFinite(value) && value >= 0;
const fail = (message, status_code = 503) => Object.assign(new Error(message), { status_code });
const pause = ms => new Promise(resolve => setTimeout(resolve, ms));

export function createProcessGroupStopper(getPid, graceMs = 2000) {
  let shutdown = null;
  return signal => {
    if (shutdown) return shutdown;
    const pid = getPid();
    if (!Number.isSafeInteger(pid) || pid <= 0) return Promise.resolve();
    shutdown = (async () => {
      const signalGroup = value => {
        try { process.kill(-pid, value); return true; }
        catch (error) { if (error.code === 'ESRCH') return false; throw error; }
      };
      if (!signalGroup(signal)) return;
      const deadline = Date.now() + graceMs;
      // Keep these waits referenced: the shell leader can exit while a native
      // descendant ignores the first signal and still needs group escalation.
      while (signalGroup(0) && Date.now() < deadline) await pause(Math.min(25, graceMs));
      if (signalGroup(0)) signalGroup('SIGKILL');
    })();
    return shutdown;
  };
}

export function seal(key, frame) {
  const iv = randomBytes(12), cipher = createCipheriv('aes-256-gcm', key, iv);
  return Buffer.concat([Buffer.alloc(4), iv, cipher.update(JSON.stringify(frame)), cipher.final(), cipher.getAuthTag()]);
}
export function open(key, envelope) {
  const data = Buffer.from(envelope);
  if (data.length < 32) throw fail('Malformed Collab frame');
  const decipher = createDecipheriv('aes-256-gcm', key, data.subarray(4, 16));
  decipher.setAuthTag(data.subarray(-16));
  return JSON.parse(Buffer.concat([decipher.update(data.subarray(16, -16)), decipher.final()]).toString());
}

async function ownsTcpPeer(pid, port, relayPort) {
  if (!Number.isSafeInteger(pid) || !port) return false;
  if (process.platform !== 'linux') return false;
  try {
    const lines = (await fs.readFile('/proc/net/tcp', 'utf8')).split('\n');
    const row = lines.map(line => line.trim().split(/\s+/)).find(fields =>
      fields[1] === `0100007F:${port.toString(16).toUpperCase().padStart(4, '0')}` &&
      fields[2] === `0100007F:${relayPort.toString(16).toUpperCase().padStart(4, '0')}` && fields[3] === '01');
    if (!row) return false;
    // Command templates may keep a shell/wrapper parent. Only that launched
    // process tree is eligible; editor+registry still bind the exact native PID.
    const candidates = [pid];
    for (let index = 0; index < candidates.length && index < 32; index++) {
      const candidate = candidates[index];
      try {
        const descriptors = await fs.readdir(`/proc/${candidate}/fd`);
        if (descriptors.length > 8192) return false;
        for (const fd of descriptors) {
          try { if (await fs.readlink(`/proc/${candidate}/fd/${fd}`) === `socket:[${row[9]}]`) return true; }
          catch { /* Descriptor may close while enumerating. */ }
        }
        const children = (await fs.readFile(`/proc/${candidate}/task/${candidate}/children`, 'utf8')).trim();
        if (children) candidates.push(...children.split(/\s+/).map(Number));
      } catch { /* A wrapper can exit while enumerating. */ }
    }
  } catch { /* /proc unavailable: never admit an unverified host. */ }
  return false;
}

export function startRelay() {
  // One process-owned loopback relay. Room payloads and hello write tokens stay
  // encrypted; no browser or public relay ever receives a control capability.
  let host = null, room = null, nextPeer = 1;
  let hostPid = process.pid;
  const capability = randomBytes(32).toString('hex');
  const guests = new Map();
  const send = (ws, data) => {
    if (!ws || ws.readyState !== 1) return;
    if (ws.getBufferedAmount() + Buffer.byteLength(data) > 8 * MAX_FRAME) { ws.close(4029); return; }
    ws.send(data);
  };
  const server = Bun.serve({
    hostname: '127.0.0.1', port: 0,
    async fetch(req, server) {
      const url = new URL(req.url), match = /^\/r\/([A-Za-z0-9_-]{10,64})$/.exec(url.pathname);
      const role = url.searchParams.get('role');
      if (!match || !['host', 'guest'].includes(role)) return new Response(null, { status: 404 });
      const peer = server.requestIP(req);
      if (role === 'host' ? !await ownsTcpPeer(hostPid, peer?.port, server.port) : req.headers.get('x-ccb-collab-capability') !== capability) return new Response(null, { status: 403 });
      if (role === 'host' ? host !== null : !host || room !== match[1] || guests.size >= 4) return new Response(null, { status: 409 });
      if (server.upgrade(req, { data: { role, room: match[1], peer: role === 'host' ? 0 : nextPeer++ } })) return;
      return new Response(null, { status: 400 });
    },
    websocket: {
      maxPayloadLength: MAX_FRAME, backpressureLimit: 8 * MAX_FRAME, closeOnBackpressureLimit: true,
      open(ws) {
        const d = ws.data;
        if (d.role === 'host') {
          if (host) { ws.close(4009); return; }
          host = ws; room = d.room;
        } else {
          if (!host || room !== d.room || guests.size >= 4) { ws.close(4004); return; }
          guests.set(d.peer, ws); send(host, JSON.stringify({ t: 'peer-joined', peer: d.peer }));
        }
      },
      message(ws, message) {
        if (typeof message === 'string' || message.length < 32) { ws.close(1008); return; }
        const data = Buffer.from(message), target = data.readUInt32BE(0);
        if (ws === host) {
          data.writeUInt32BE(0, 0);
          if (target === 0) for (const guest of guests.values()) send(guest, data);
          else send(guests.get(target), data);
        } else if (guests.get(ws.data.peer) === ws && target === 0) {
          data.writeUInt32BE(ws.data.peer, 0); send(host, data);
        } else ws.close(1008);
      },
      close(ws) {
        if (host === ws) {
          host = null; room = null;
          for (const guest of guests.values()) guest.close(4001);
          guests.clear();
        } else if (guests.delete(ws.data.peer)) send(host, JSON.stringify({ t: 'peer-left', peer: ws.data.peer }));
      },
    },
  });
  return { url: `ws://127.0.0.1:${server.port}`, capability, setHostPid: pid => { hostPid = pid; }, close: () => server.stop(true) };
}

export async function query(endpoint, request, max = 65536, timeout = 800) {
  return await new Promise((resolve, reject) => {
    const socket = net.createConnection(endpoint);
    let bytes = 0, chunks = [];
    const finish = (error, value) => { socket.destroy(); error ? reject(error) : resolve(value); };
    socket.setTimeout(timeout, () => finish(fail('OMP local query timed out')));
    socket.once('error', () => finish(fail('OMP local query unavailable')));
    socket.once('connect', () => socket.write(JSON.stringify(request) + '\n'));
    socket.on('data', data => {
      bytes += data.length;
      if (bytes > max) return finish(fail('OMP local response too large'));
      chunks.push(data);
      if (data.includes(10)) {
        try { finish(null, JSON.parse(Buffer.concat(chunks).toString().split('\n')[0])); }
        catch { finish(fail('Invalid OMP local response')); }
      }
    });
    socket.once('end', () => finish(fail('OMP local query disconnected')));
  });
}
async function privateStat(file, kind) {
  const info = await fs.lstat(file);
  if (info.uid !== process.getuid() || info.mode & 0o077 || !info[kind]()) throw fail('Unsafe OMP local binding');
  return info;
}

export class ManagedGuest {
  constructor(binding, relay) {
    this.binding = binding; this.relay = relay;
    this.revision = 0; this.attempts = new Map(); this.serial = Promise.resolve(); this.active = null; this.reset();
  }
  exclusive(action) {
    const result = this.serial.then(action);
    this.serial = result.catch(() => {});
    return result;
  }
  holdQueued() {
    for (const attempt of this.attempts.values()) {
      if (attempt.result.state === 'queued') attempt.result.state = 'held';
    }
    // A handed-off prompt is never replayed, including after reconnection.
    this.active = null;
  }
  async persist() {
    if (!this.binding.attempt_log) return;
    const data = [...this.attempts].map(([key, value]) => JSON.stringify({ key, ...value })).join('\n') + '\n';
    if (data === this.persistedData) return;
    if (Buffer.byteLength(data) > MAX_JOURNAL) throw fail('OMP queue journal capacity reached', 409);
    const target = this.binding.attempt_log, temporary = target + '.' + randomUUID() + '.tmp';
    let file;
    try {
      file = await fs.open(temporary, 'wx', 0o600);
      await file.writeFile(data); await file.sync(); await file.close(); file = null;
      await fs.rename(temporary, target);
      const directory = await fs.open(path.dirname(target), 'r');
      try { await directory.sync(); } finally { await directory.close(); }
      this.persistedData = data;
    } catch {
      throw fail('OMP queue persistence failed; delivery held', 502);
    } finally {
      await file?.close(); await fs.unlink(temporary).catch(() => {});
    }
  }
  async restore() {
    if (!this.binding.attempt_log) return;
    try {
      const info = await privateStat(this.binding.attempt_log, 'isFile');
      if (info.size > MAX_JOURNAL) throw fail('OMP queue journal exceeds budget');
      for (const line of (await fs.readFile(this.binding.attempt_log, 'utf8')).split('\n').filter(Boolean)) {
        const entry = JSON.parse(line);
        if (this.attempts.size >= 4096) throw fail('OMP idempotency capacity reached');
        const { key, ...attempt } = entry;
        this.attempts.set(key, attempt);
      }
      const pending = this.pendingInputs();
      if (pending.length > MAX_PENDING || pending.some(a => Buffer.byteLength(a.body) > MAX_PROMPT_BYTES) ||
          pending.reduce((n, a) => n + Buffer.byteLength(a.body), 0) > MAX_PENDING_BYTES)
        throw fail('OMP restored queue exceeds budget');
      this.holdQueued();
      await this.persist();
    } catch (error) { if (error.code !== 'ENOENT') throw error; }
  }
  pendingInputs() {
    return [...this.attempts].filter(([, a]) => typeof a.body === 'string').map(([key, a], index) => ({
      id: a.result.request_id, idempotency_key: key, body: a.body, state: a.result.state,
      request_id: a.result.request_id, session_id: a.result.session_id,
      entry_id: a.result.entry_id,
      generation: a.result.generation, position: index + 1,
    }));
  }
  reset() {
    this.holdQueued();
    this.ws?.close(); this.ws = null; this.connected = false; this.ready = false;
    this.entries = []; this.bytes = 0; this.state = {}; this.streaming = null;
    this.entryIndex = new Map(); this.entrySizes = new Map(); this.replicaTruncated = false;
    this.connectionError = null; this.historyError = null;
    this.branchId = null;
    // Upstream v3 has no shared event/entry identity; never guess a mapping.
    this.streamId = null; this.streamEntries = {};
    this.leafId = null;
    this.resetRuntime();
    this.parentLinks = [];
    this.revision++;
  }
  resetRuntime() {
    this.runtimeAgents = []; this.runtimeTasks = new Map(); this.runtimeTruncated = false;
    this.runtimeTaskTombstones = new Set();
    this.runtimeActivities = new Map(); this.runtimeToolEnds = new Set();
    this.runtimeStartedAt = null; this.runtimeObservedAt = new Date().toISOString();
    this.pendingUiRequest = null;
  }
  runtimeString(value, limit = MAX_RUNTIME_STRING, tail = false) {
    if (typeof value !== 'string') return undefined;
    if (value.length > limit) this.runtimeTruncated = true;
    return tail ? value.slice(-limit) : value.slice(0, limit);
  }
  runtimeIdentity(value) {
    if (typeof value !== 'string' || !value.length) return undefined;
    // Do not truncate identity: distinct native IDs must never alias.
    if (value.length > MAX_RUNTIME_STRING) { this.runtimeTruncated = true; return undefined; }
    return value;
  }
  uiRequest(frame) {
    if (!frame || !Number.isSafeInteger(frame.reqId) || frame.reqId < 0 || !['select', 'editor'].includes(frame.kind) ||
        typeof frame.title !== 'string' || frame.title.length > 512 ||
        (frame.helpText != null && (typeof frame.helpText !== 'string' || frame.helpText.length > 512)) ||
        (frame.prefill != null && (typeof frame.prefill !== 'string' || frame.prefill.length > 512)) ||
        (frame.selectionMarker != null && (typeof frame.selectionMarker !== 'string' || frame.selectionMarker.length > 512)) ||
        (frame.options != null && !Array.isArray(frame.options))) return null;
    const options = [];
    for (const option of frame.options ?? []) {
      if (options.length >= 16) return null;
      const label = typeof option === 'string' ? option : option?.label;
      const description = typeof option === 'string' ? undefined : option?.description;
      if (typeof label !== 'string' || label.length > 256 ||
          (description != null && (typeof description !== 'string' || description.length > 512))) return null;
      options.push(description === undefined ? { label } : { label, description });
    }
    if ((frame.initialIndex != null && (!Number.isSafeInteger(frame.initialIndex) || frame.initialIndex < 0 || frame.initialIndex >= options.length)) ||
        (frame.checkedIndices != null && !Array.isArray(frame.checkedIndices))) return null;
    const checked = [];
    for (const index of frame.checkedIndices ?? []) {
      if (!Number.isSafeInteger(index) || index < 0 || index >= options.length || checked.includes(index)) return null;
      checked.push(index);
    }
    const request = { req_id: frame.reqId, kind: frame.kind, title: frame.title, options,
      initial_index: frame.initialIndex ?? null, marker: frame.selectionMarker ?? null, checked_indices: checked,
      help_text: frame.helpText ?? null, prefill: frame.prefill ?? null, responded: false };
    // Reserve room for the runtime envelope and preserve option labels exactly.
    const size = () => Buffer.byteLength(JSON.stringify(request).replace(/[^\x00-\x7f]/g, c => '\\u' + c.charCodeAt(0).toString(16).padStart(4, '0')));
    for (const [row, key] of [...options.map(option => [option, 'description']),
      [request, 'help_text'], [request, 'prefill'], [request, 'marker'], [request, 'title']]) {
      while (size() > 8192 && row[key]?.length) {
        row[key] = row[key].slice(0, Math.floor(row[key].length / 2));
        this.runtimeTruncated = true;
      }
    }
    if (size() > 8192) { this.runtimeTruncated = true; return null; }
    return request;
  }
  visibleText(content, type = 'text') {
    if (!Array.isArray(content)) return undefined;
    let text = '';
    for (const block of content) {
      const value = block?.type === type ? block[type] : undefined;
      if (typeof value === 'string') text = this.runtimeString(text + value, 768, true);
    }
    return text || undefined;
  }
  finishActivities() {
    for (const activity of this.runtimeActivities.values()) {
      if (activity.status !== 'running') continue;
      // A turn boundary or lost connection is not a tool/retry completion receipt.
      activity.status = 'unknown'; activity.updated_at = this.runtimeObservedAt;
      delete activity.duration_ms;
      if (activity.kind === 'tool') {
        this.runtimeToolEnds.add(activity.id);
        if (this.runtimeToolEnds.size > MAX_RUNTIME_ROWS * 4) this.runtimeToolEnds.delete(this.runtimeToolEnds.values().next().value);
      }
    }
  }
  observeActivity(event) {
    const now = this.runtimeObservedAt, type = event.type;
    if (type === 'agent_start') this.finishActivities();
    if (type === 'agent_end') { this.finishActivities(); return; }
    const put = (id, kind, status, title, text, tool) => {
      const previous = this.runtimeActivities.get(id);
      const row = { ...previous, id, kind, status, title: this.runtimeString(title, 512), updated_at: now };
      if (text !== undefined) row.text = this.runtimeString(text, 768, true);
      if (tool !== undefined) row.tool_name = this.runtimeString(tool);
      if (status === 'running') {
        if (type.endsWith('_start')) row.started_at = now;
        delete row.duration_ms;
      }
      if (status !== 'running' && row.started_at && (previous?.status === 'running' || type === 'tool_execution_end'))
        row.duration_ms = Math.max(0, Date.parse(now) - Date.parse(row.started_at));
      this.runtimeActivities.delete(id); this.runtimeActivities.set(id, row);
      while (this.runtimeActivities.size > MAX_RUNTIME_ROWS) {
        const oldest = [...this.runtimeActivities].find(([, activity]) => activity.status !== 'running');
        this.runtimeActivities.delete(oldest?.[0] ?? this.runtimeActivities.keys().next().value);
        this.runtimeTruncated = true;
      }
    };
    if (['tool_execution_start', 'tool_execution_update', 'tool_execution_end'].includes(type)) {
      const call = this.runtimeIdentity(event.toolCallId);
      if (!call) return;
      const id = `tool:${call}`, previous = this.runtimeActivities.get(id);
      const ended = type === 'tool_execution_end';
      if (ended) {
        this.runtimeToolEnds.add(id);
        if (this.runtimeToolEnds.size > MAX_RUNTIME_ROWS * 4) this.runtimeToolEnds.delete(this.runtimeToolEnds.values().next().value);
      }
      const intent = this.runtimeString(event.intent, 512) || this.runtimeString(event.args?.i, 512) || this.runtimeString(event.args?.intent, 512);
      // Detached callbacks may continue after the tool returned. Preserve their
      // real text without reviving a completed call or claiming the main is busy.
      const status = ended ? (event.isError ? 'failed' : 'completed') :
        this.runtimeToolEnds.has(id) ? (previous?.status ?? 'unknown') :
        previous && previous.status !== 'running' ? previous.status : this.state.isStreaming ? 'running' : 'info';
      put(id, 'tool', status, intent || previous?.title || event.toolName || 'Tool',
        this.visibleText((ended ? event.result : event.partialResult)?.content), event.toolName);
    } else if (['message_start', 'message_update', 'message_end'].includes(type) && event.message?.role === 'assistant') {
      for (const [kind, blockType, title] of [['reasoning', 'thinking', 'Thinking'], ['message', 'text', 'Assistant']]) {
        const text = this.visibleText(event.message.content, blockType);
        if (text) put(`assistant:${kind}`, kind, type === 'message_end' || !this.state.isStreaming ? 'completed' : 'running', title, text);
      }
    } else if (type === 'auto_retry_start') {
      const attempt = finiteNonnegative(event.attempt) ? ` ${event.attempt}` : '';
      const max = finiteNonnegative(event.maxAttempts) ? `/${event.maxAttempts}` : '';
      put('session:retry', 'retry', 'running', `Retrying${attempt}${max}`, event.errorMessage);
    } else if (type === 'auto_retry_end') {
      put('session:retry', 'retry', event.success ? 'completed' : 'failed', event.success ? 'Retry succeeded' : 'Retry failed', event.finalError ?? '');
    } else if (type === 'auto_compaction_start') {
      put('session:compaction', 'compaction', 'running', 'Compacting context', event.reason);
    } else if (type === 'auto_compaction_end') {
      put('session:compaction', 'compaction', event.aborted ? 'aborted' : event.errorMessage ? 'failed' : 'completed',
        event.aborted ? 'Compaction aborted' : event.skipped ? 'Compaction skipped' : 'Context compacted', event.errorMessage ?? '');
    } else if (type === 'notice') {
      put('session:notice', 'notice', event.level === 'error' ? 'failed' : 'info', 'Notice', event.message);
    }
  }
  observeAgents(agents) {
    this.runtimeAgents = [];
    if (!Array.isArray(agents)) return;
    if (agents.length > MAX_RUNTIME_ROWS) this.runtimeTruncated = true;
    // Select active rows before applying the bound, not after slicing history.
    const selected = [];
    for (const active of [true, false]) {
      for (const agent of agents) {
        if (selected.length >= MAX_RUNTIME_ROWS) break;
        if (!agent || !this.runtimeIdentity(agent.id)) continue;
        if ((agent.status === 'running') === active) selected.push(agent);
      }
    }
    for (const agent of selected) {
      if (!agent || !this.runtimeIdentity(agent.id)) continue;
      this.runtimeAgents.push({ id: agent.id, displayName: this.runtimeString(agent.displayName) ?? '',
        kind: this.runtimeString(agent.kind) ?? '', parentId: this.runtimeIdentity(agent.parentId) ?? null,
        status: this.runtimeString(agent.status) ?? '',
        createdAt: finiteNonnegative(agent.createdAt) ? agent.createdAt : null,
        lastActivity: finiteNonnegative(agent.lastActivity) ? agent.lastActivity : null,
        hasSessionFile: agent.hasSessionFile === true });
    }
  }
  observeTask(channel, data) {
    if (!TASK_CHANNELS.has(channel) || !data || typeof data !== 'object') return;
    const lifecycle = channel === 'task:subagent:lifecycle';
    const progress = lifecycle ? null : data.progress;
    const id = this.runtimeIdentity(lifecycle ? data.id : progress?.id);
    if (!id || (lifecycle && data.status !== 'started' && !TERMINAL_TASK_STATUSES.has(data.status))) return;
    const existing = this.runtimeTasks.get(id);
    const starting = lifecycle && data.status === 'started';
    // Only an explicit new lifecycle start may revive a kept-alive agent.
    // Evicted terminal identities remain in a separate bounded tombstone set.
    if (!starting && (TERMINAL_TASK_STATUSES.has(existing?.status) || this.runtimeTaskTombstones.has(id))) return;
    if (!existing && this.runtimeTasks.size >= MAX_RUNTIME_ROWS) {
      const terminal = [...this.runtimeTasks].find(([, task]) => TERMINAL_TASK_STATUSES.has(task.status));
      this.runtimeTruncated = true;
      if (!terminal) return;
      this.runtimeTasks.delete(terminal[0]);
    }
    if (starting) this.runtimeTaskTombstones.delete(id);
    const now = this.runtimeObservedAt;
    const task = existing ?? { id, agent: this.runtimeString(data.agent) ?? '', status: 'running', updated_at: now };
    task.status = lifecycle ? data.status : (task.status === 'started' ? 'started' : 'running');
    task.updated_at = now;
    if (starting) {
      task.started_at = now;
      delete task.duration_ms; delete task.tool_name;
      for (const key of ['task', 'intent', 'stage', 'progress_text', 'completion_percent', 'tool_count', 'requests', 'tokens', 'context_tokens', 'context_window']) delete task[key];
    }
    if (TERMINAL_TASK_STATUSES.has(task.status)) {
      this.runtimeTaskTombstones.delete(id); this.runtimeTaskTombstones.add(id);
      if (this.runtimeTaskTombstones.size > MAX_RUNTIME_ROWS * 4) {
        this.runtimeTaskTombstones.delete(this.runtimeTaskTombstones.values().next().value);
        this.runtimeTruncated = true;
      }
    }
    const description = this.runtimeString(lifecycle ? data.description : progress.description);
    if (description !== undefined) task.description = description;
    const parent = this.runtimeIdentity(data.parentToolCallId);
    if (parent !== undefined) task.parent_tool_call_id = parent;
    if (Number.isSafeInteger(data.index) && data.index >= 0) task.index = data.index;
    if (typeof data.detached === 'boolean') task.detached = data.detached;
    if (progress) {
      if (finiteNonnegative(progress.durationMs)) task.duration_ms = progress.durationMs;
      const tool = this.runtimeString(progress.currentTool);
      if (tool !== undefined) task.tool_name = tool;
      else delete task.tool_name;
      for (const [key, value, limit] of [['task', progress.assignment ?? data.assignment ?? progress.task ?? data.task, 512],
        ['intent', progress.currentToolIntent || progress.lastIntent, 512], ['stage', progress.stage, 256]]) {
        const text = this.runtimeString(value, limit);
        if (text !== undefined) task[key] = text; else delete task[key];
      }
      if (Array.isArray(progress.recentOutput)) {
        let text = '';
        for (const line of progress.recentOutput) if (typeof line === 'string') text = this.runtimeString(text ? `${text}\n${line}` : line, 768, true);
        task.progress_text = text;
      } else delete task.progress_text;
      for (const [key, source] of [['completion_percent', 'completionPercent'], ['tool_count', 'toolCount'],
        ['requests', 'requests'], ['tokens', 'tokens'], ['context_tokens', 'contextTokens'], ['context_window', 'contextWindow']]) {
        const value = progress[source];
        if (finiteNonnegative(value) && (key !== 'completion_percent' || value <= 100)) task[key] = value;
        else delete task[key];
      }
    } else if (TERMINAL_TASK_STATUSES.has(task.status)) {
      delete task.tool_name; delete task.intent; delete task.stage;
    }
    this.runtimeTasks.set(id, task);
  }
  runtimeSnapshot() {
    const state = this.state ?? {}, model = state.model, context = state.contextUsage;
    const provider = this.runtimeString(model?.provider), id = this.runtimeString(model?.id);
    const name = this.runtimeString(model?.name);
    const thinking = this.runtimeString(state.thinkingLevel);
    const activities = [...this.runtimeActivities.values()].reverse().sort((a, b) => (a.status !== 'running') - (b.status !== 'running'));
    if (activities.length > 8) this.runtimeTruncated = true;
    const observation = this.observation;
    const observed = observation?.editor?.status_bar;
    const sameOwner = this.connected && this.ready && this.host &&
      observation?.host?.instanceId === this.host.instanceId && observation?.host?.generation === this.host.generation &&
      observation?.editor?.session_id === this.host.sessionId &&
      (observation?.editor?.branch_id ?? null) === (this.branchId ?? null);
    const statusBar = sameOwner && observed?.available === true && observed.source === 'native_events' &&
      observed.session_id === this.host.sessionId && typeof observed.is_streaming === 'boolean'
      ? structuredClone(observed) : { available: false };
    const pending = this.connected && this.ready && !this.pendingUiRequest?.responded ? this.pendingUiRequest : null;
    const pendingUiRequest = pending ? (({ req_id, kind, title, options, initial_index, marker, checked_indices, help_text, prefill }) =>
      ({ req_id, kind, title, options, initial_index, marker, checked_indices, help_text, prefill }))(pending) : null;
    const runtime = { connected: this.connected, ready: this.ready, progress_available: true,
      is_streaming: this.connected && this.ready && typeof state.isStreaming === 'boolean' ? state.isStreaming : null,
      is_aborting: this.connected && this.ready && state.isAborting === true,
      queued_message_count: this.connected && this.ready && Number.isSafeInteger(state.queuedMessageCount) && state.queuedMessageCount >= 0 ? state.queuedMessageCount : 0,
      started_at: this.connected && this.ready ? this.runtimeStartedAt : null,
      observed_at: this.runtimeObservedAt,
      status_bar: statusBar,
      model: provider && id ? { provider, id, ...(name === undefined ? {} : { name }) } : null,
      thinking_level: thinking ?? null,
      context_usage: context && finiteNonnegative(context.tokens) && finiteNonnegative(context.contextWindow) && finiteNonnegative(context.percent)
        ? { tokens: context.tokens, contextWindow: context.contextWindow, percent: context.percent } : null,
      activities: activities.slice(0, 8), agents: [...this.runtimeAgents], tasks: [...this.runtimeTasks.values()],
      pending_ui_request: pendingUiRequest, truncated: this.runtimeTruncated };
    if (statusBar.available) {
      runtime.is_streaming = statusBar.is_streaming;
      runtime.started_at = statusBar.is_streaming ? statusBar.started_at : null;
    }
    let bytes = Buffer.byteLength(JSON.stringify(runtime));
    // Keep main metadata and active work ahead of terminal/background history.
    // Internal terminal tombstones remain retained even when omitted from output.
    while (bytes > MAX_RUNTIME_BYTES && (runtime.tasks.length || runtime.agents.length || runtime.activities.length)) {
      let rows = runtime.agents, index = rows.findLastIndex(agent => agent.status !== 'running');
      if (index < 0) { rows = runtime.tasks; index = rows.findLastIndex(task => TERMINAL_TASK_STATUSES.has(task.status)); }
      if (index < 0 && runtime.agents.length) { rows = runtime.agents; index = rows.length - 1; }
      if (index < 0 && runtime.tasks.length) { rows = runtime.tasks; index = rows.length - 1; }
      if (index < 0) { rows = runtime.activities; index = rows.length - 1; }
      rows.splice(index, 1);
      runtime.truncated = true;
      bytes = Buffer.byteLength(JSON.stringify(runtime));
    }
    while (Buffer.byteLength(JSON.stringify(runtime)) > MAX_RUNTIME_BYTES && runtime.status_bar.todos?.items?.length) {
      runtime.status_bar.todos.items.pop(); runtime.status_bar.todos.truncated = true; runtime.truncated = true;
    }
    return runtime;
  }
  async discover() {
    const b = this.binding;
    const deadline = Date.now() + 1200;
    // Extension observation is the exact native owner PID/session/cwd, never mtime.
    const editor = await query(b.editor_socket, { operation: 'inspect', actor: b.actor, launch_session_id: b.launch_session_id });
    if (!Number.isSafeInteger(editor.pid) || !editor.session_id) throw fail('OMP owner not observed');
    const names = await fs.readdir(b.registry_dir);
    if (names.length > 128) throw fail('OMP registry capacity exceeded');
    let found = null;
    for (const name of names) {
      if (Date.now() > deadline) throw fail('OMP registry discovery deadline exceeded');
      if (!name.endsWith('.json')) continue;
      const file = path.join(b.registry_dir, name);
      try {
        const info = await privateStat(file, 'isFile');
        if (info.size > 8192) continue;
        const meta = JSON.parse(await fs.readFile(file, 'utf8'));
        if (meta.version !== 1 || meta.pid !== editor.pid) continue;
        await privateStat(meta.endpoint, 'isSocket');
        const response = await query(meta.endpoint, { v: 1, token: meta.token, op: 'snapshot' });
        const h = response.snapshot;
        if (!response.ok || response.v !== 1 || h.pid !== editor.pid || h.sessionId !== editor.session_id || path.resolve(h.cwd) !== path.resolve(b.cwd)) continue;
        if (found) throw fail('Ambiguous OMP Collab owner');
        found = { meta, host: h, editor };
      } catch (error) { if (error.message === 'Ambiguous OMP Collab owner') throw error; }
    }
    if (!found) throw fail('Managed OMP Collab unavailable (requires upstream registry v1 and Collab v3)');
    return found;
  }
  async sync() {
    const found = await this.discover(), h = found.host;
    this.observation = found;
    this.branchId = found.editor.branch_id ?? this.branchId;
    this.leafId = found.editor.leaf_id ?? null;
    if (this.host?.instanceId === h.instanceId && this.host.generation === h.generation && this.ws?.readyState === 1) return;
    this.reset(); this.host = h;
    this.branchId = found.editor.branch_id ?? null;
    this.leafId = found.editor.leaf_id ?? null;
    if (!h.relayConnected || h.access !== 'control') throw fail('OMP Collab host not ready');
    const response = await query(found.meta.endpoint, { v: 1, token: found.meta.token, op: 'link', generation: h.generation, access: 'control' });
    if (!response.ok || response.v !== 1) throw fail('OMP Collab generation changed', 409);
    // Registry returns a browser URL whose fragment contains the native link.
    let link = new URL(response.url);
    if (link.hash) link = new URL(decodeURIComponent(link.hash.slice(1)));
    if (link.origin !== this.relay.url.replace('ws:', 'http:') && link.origin !== this.relay.url) throw fail('OMP relay binding mismatch');
    const match = /^\/r\/([A-Za-z0-9_-]{10,64})\.([A-Za-z0-9_-]+)$/.exec(link.pathname);
    if (!match) throw fail('Unsupported OMP Collab link');
    const secret = Buffer.from(match[2], 'base64url');
    if (secret.length !== 48) throw fail('OMP Collab control unavailable');
    const key = secret.subarray(0, 32), writeToken = secret.subarray(32).toString('base64url');
    const ws = new WebSocket(`${this.relay.url}/r/${match[1]}?role=guest`, { headers: { 'x-ccb-collab-capability': this.relay.capability } });
    this.ws = ws; this.key = key;
    ws.binaryType = 'arraybuffer';
    ws.onopen = () => { if (this.ws === ws) ws.send(seal(key, { t: 'hello', proto: 3, name: 'CCB Mobile', writeToken })); };
    ws.onmessage = event => {
      if (this.ws !== ws) return;
      try { this.receive(open(key, event.data)); }
      catch (error) { this.pendingUiRequest = null; this.connectionError = error.message; ws.close(); this.connected = false; this.ready = false; this.runtimeStartedAt = null; this.runtimeObservedAt = new Date().toISOString(); this.finishActivities(); this.revision++; }
    };
    const lost = () => {
      if (this.ws !== ws) return;
      this.holdQueued();
      this.pendingUiRequest = null;
      this.connectionError ??= 'OMP Collab connection lost';
      this.connected = false; this.ready = false; this.streaming = null; this.streamId = null; this.revision++;
      this.runtimeStartedAt = null; this.runtimeObservedAt = new Date().toISOString();
      this.finishActivities();
    };
    ws.onclose = lost; ws.onerror = lost;
    const deadline = Date.now() + 1200;
    while (this.ws === ws && !this.ready && Date.now() < deadline) await pause(20);
  }
  receive(frame) {
    this.runtimeObservedAt = new Date().toISOString();
    if (frame.t === 'welcome') {
      if (frame.proto !== 3 || frame.header?.id !== this.host.sessionId || frame.readOnly) throw fail('OMP welcome binding mismatch');
      this.resetRuntime(); this.observeAgents(frame.agents);
      this.connected = true; this.ready = false; this.entries = []; this.bytes = 0; this.state = frame.state;
      this.entryIndex.clear(); this.entrySizes.clear(); this.replicaTruncated = false;
    } else if (frame.t === 'snapshot-chunk') {
      if (!this.connected || this.ready || !Array.isArray(frame.entries)) throw fail('Invalid OMP snapshot barrier');
      for (const entry of frame.entries) this.addEntry(entry);
      if (frame.final) this.ready = true;
    } else if (frame.t === 'entry') {
      if (!this.ready) throw fail('OMP live entry before snapshot');
      this.addEntry(frame.entry);
      this.leafId = frame.entry.id;
    } else if (frame.t === 'state') {
      this.state = frame.state;
      if (this.active && frame.state.isStreaming) this.active.started = true;
      if (this.active?.started && frame.state.isStreaming === false) this.active.stopped = true;
      if (!frame.state.isStreaming) { this.streaming = null; this.streamId = null; }
      if (frame.state.isStreaming === false) { this.runtimeStartedAt = null; this.finishActivities(); }
    } else if (frame.t === 'agents' && this.connected) {
      this.observeAgents(frame.agents);
    } else if (frame.t === 'bus' && this.connected) {
      this.observeTask(frame.channel, frame.data);
    } else if (frame.t === 'event' && this.ready) {
      const event = frame.event, message = event.message;
      this.observeActivity(event);
      if (event.type === 'agent_start') { this.runtimeStartedAt = this.runtimeObservedAt; this.state = { ...this.state, isStreaming: true }; }
      if (event.type === 'agent_end') { this.runtimeStartedAt = null; this.state = { ...this.state, isStreaming: false, isAborting: false }; }
      if (this.active && ['agent_start', 'turn_start', 'message_start'].includes(event.type)) this.active.started = true;
      if (this.active?.started && ['agent_end', 'turn_end'].includes(event.type)) this.active.stopped = true;
      if (['message_start', 'message_update'].includes(event.type) && message?.role === 'assistant') {
        this.streamId ??= randomUUID(); this.streaming = message;
      } else if (event.type === 'message_end' && message?.role === 'assistant') {
        this.streaming = null; this.streamId = null;
      }
    } else if (frame.t === 'ui-request' && this.ready) {
      const request = this.uiRequest(frame.request);
      if (request && request.req_id !== this.pendingUiRequest?.req_id) this.pendingUiRequest = request;
    } else if (frame.t === 'ui-request-end' && this.ready) {
      if (frame.reqId === this.pendingUiRequest?.req_id) this.pendingUiRequest = null;
    } else if (['bye', 'error'].includes(frame.t)) {
      this.holdQueued();
      this.pendingUiRequest = null;
      this.connectionError = typeof frame.message === 'string' ? frame.message : 'OMP Collab host disconnected';
      this.connected = false; this.ready = false; this.ws?.close();
      this.runtimeStartedAt = null;
      this.finishActivities();
    }
    this.revision++;
  }
  addEntry(entry) {
    if (!entry || typeof entry.id !== 'string') throw fail('Invalid OMP entry');
    const old = this.entryIndex.get(entry.id);
    if (old) {
      if (JSON.stringify(old) !== JSON.stringify(entry)) throw fail('OMP entry changed');
      return;
    }
    const size = Buffer.byteLength(JSON.stringify(entry));
    // The native session is authoritative. Consume every snapshot frame through
    // its final barrier, retaining only a bounded recent window, not all history.
    while (this.entries.length && (this.entries.length >= MAX_RECENT_ENTRIES || this.bytes + size > MAX_RECENT_BYTES)) {
      const evicted = this.entries.shift();
      this.bytes -= this.entrySizes.get(evicted.id);
      this.entryIndex.delete(evicted.id); this.entrySizes.delete(evicted.id);
      this.replicaTruncated = true;
    }
    // Only a live entry on the same ordered channel can bind the sole handoff.
    // Historical entries, timestamps and matching text are never evidence.
    if (this.ready && this.active && !this.active.entry_id &&
        ((entry.type === 'custom_message' && entry.customType === 'collab-prompt') ||
         (entry.type === 'message' && entry.message?.role === 'custom' && entry.message.customType === 'collab-prompt'))) {
      this.active.entry_id = entry.id;
      this.active.attempt.result.entry_id = entry.id;
      this.active.attempt.result.state = 'sent';
    }
    // Host entries/events share an ordered CollabSocket. A live durable
    // assistant supersedes the preceding ephemeral stream, without ID guessing.
    if (this.ready && entry.type === 'message' && entry.message?.role === 'assistant') {
      this.streaming = null; this.streamId = null;
    }
    if (size <= MAX_RECENT_BYTES) {
      this.entries.push(entry); this.bytes += size;
      this.entryIndex.set(entry.id, entry); this.entrySizes.set(entry.id, size);
    } else this.replicaTruncated = true;
  }
  snapshot() {
    return { session_id: this.host?.sessionId ?? '', generation: `${this.host?.instanceId ?? ''}:${this.host?.generation ?? 0}`,
      runtime: this.runtimeSnapshot(),
      connected: this.connected, ready: this.ready, entries: this.entries,
      history_complete: false, history_paged: true, recent_truncated: this.replicaTruncated,
      connection_error: this.connectionError, history_error: this.historyError,
      streaming_message: this.streaming, stream_id: this.streamId, stream_entry_ids: this.streamEntries,
      parent_links: this.parentLinks,
      branch_id: this.branchId ?? '',
      pending_inputs: this.pendingInputs(),
      input_bindings: [...this.attempts].filter(([, a]) => typeof a.result.entry_id === 'string').map(([key, a]) => ({
        entry_id: a.result.entry_id, idempotency_key: key,
        session_id: a.result.session_id, generation: a.result.generation,
      })),
      state: { ...this.state, leafId: this.leafId }, revision: this.revision };
  }
  async nativePage(operation, request = {}) {
    const observation = this.observation;
    const response = await query(this.binding.editor_socket, {
      operation, actor: this.binding.actor, launch_session_id: this.binding.launch_session_id,
      runtime_instance_id: observation.editor.runtime_instance_id, session_id: this.host.sessionId,
      cursor: request.cursor ?? null, limit: request.limit ?? 128, max_bytes: request.max_bytes ?? 262144,
    }, MAX_FRAME);
    if (response.error) throw fail(response.error, response.status_code ?? 503);
    if (response.session_id !== this.host.sessionId ||
        response.runtime_instance_id !== observation.editor.runtime_instance_id ||
        typeof response.branch_id !== 'string' || typeof response.has_more !== 'boolean' ||
        (response.has_more && typeof response.next_cursor !== 'string'))
      throw fail('OMP native history binding unavailable', 409);
    return response;
  }
  async refreshSnapshot() {
    try { await this.sync(); if (this.connected && this.ready) this.connectionError = null; }
    catch (error) {
      // Owner discovery failures invalidate the replica, unlike an independent
      // history request failing while its authenticated live socket is healthy.
      this.reset(); this.connectionError = error.message;
      return this.snapshot();
    }
    if (this.binding.editor_socket) {
      try {
        const ancestry = await this.nativePage('ancestry_page');
        if (!Array.isArray(ancestry.parent_links) || ancestry.parent_links.length > 128)
          throw fail('OMP ancestry page unavailable');
        this.branchId = ancestry.branch_id;
        this.parentLinks = ancestry.parent_links;
        this.leafId = ancestry.leaf_id ?? null;
        this.historyError = null;
      } catch (error) { this.historyError = error.message; }
    }
    return this.snapshot();
  }
  async history(request) {
    await this.sync();
    const generation = this.snapshot().generation;
    if ((request.expected_session_id != null && request.expected_session_id !== this.host.sessionId) ||
        (request.expected_generation != null && request.expected_generation !== generation))
      throw fail('OMP conversation changed; refresh history', 409);
    if (!Number.isSafeInteger(request.limit ?? 128) || (request.limit ?? 128) < 1 || (request.limit ?? 128) > 128 ||
        !Number.isSafeInteger(request.max_bytes ?? 262144) || (request.max_bytes ?? 262144) < 4096 || (request.max_bytes ?? 262144) > 262144 ||
        (request.cursor != null && (typeof request.cursor !== 'string' || request.cursor.length > 16384)))
      throw fail('Invalid OMP history page', 409);
    const page = await this.nativePage('history_page', request);
    if (!Array.isArray(page.entries) || page.entries.length > 128 || !Array.isArray(page.entry_chunks))
      throw fail('Invalid OMP history page');
    this.branchId = page.branch_id; this.leafId = page.leaf_id ?? null;
    return { ...this.snapshot(), entries: page.entries, entry_chunks: page.entry_chunks,
      history_complete: false, history_error: null,
      anchor_leaf_id: page.anchor_leaf_id, leaf_id: page.leaf_id, start_cursor: page.start_cursor,
      next_cursor: page.next_cursor, has_more: page.has_more };
  }
  async models(request, mutate = false) {
    await this.sync();
    if (!this.ready || !this.connected) throw fail('OMP Collab owner unavailable');
    if (mutate && this.pendingInputs().length) throw fail('OMP inputs pending; wait before changing model', 409);
    if (mutate && (request.expected_session_id !== this.host.sessionId || request.expected_generation !== this.snapshot().generation))
      throw fail('OMP conversation changed; refresh model controls', 409);
    const observation = this.observation;
    const response = await query(this.binding.editor_socket, {
      operation: mutate ? 'set_model' : 'models', actor: this.binding.actor,
      launch_session_id: this.binding.launch_session_id,
      runtime_instance_id: observation.editor.runtime_instance_id, session_id: this.host.sessionId,
      ...(mutate ? { model: request.model, thinking: request.thinking,
        expected_model: request.expected_model, expected_thinking: request.expected_thinking,
        expected_model_revision: request.expected_model_revision, idempotency_key: request.idempotency_key } : {}),
    }, MAX_FRAME, mutate ? 1200 : 800).catch(error => {
      if (mutate) throw fail('OMP model delivery unknown; refresh without replay', 502);
      throw error;
    });
    if (response.error) throw fail(response.error, response.status_code ?? 503);
    if (response.session_id !== this.host.sessionId || response.runtime_instance_id !== observation.editor.runtime_instance_id || !Array.isArray(response.models))
      throw fail('OMP model companion binding changed', 409);
    return { session_id: response.session_id, generation: this.snapshot().generation,
      model: response.model, thinking_level: response.thinking_level,
      model_revision: response.model_revision, models: response.models };
  }
  uiResponse(request) { return this.exclusive(() => this.respondUi(request)); }
  async respondUi(request) {
    if (!Number.isSafeInteger(request.req_id) || request.req_id < 0 ||
        (request.value != null && (typeof request.value !== 'string' || request.value.length > 512)))
      throw fail('Invalid OMP UI response', 409);
    await this.sync();
    if (request.expected_session_id !== this.host?.sessionId || request.expected_generation !== this.snapshot().generation)
      throw fail('OMP conversation changed; refresh before responding', 409);
    if (!this.ready || !this.connected || this.ws?.readyState !== 1) throw fail('OMP Collab disconnected');
    const pending = this.pendingUiRequest;
    if (!pending || pending.req_id !== request.req_id || pending.responded)
      throw fail('OMP UI request changed; refresh before responding', 409);
    // A local send exception cannot prove the upstream did not receive this
    // sealed frame. Consume the exact request before attempting delivery.
    pending.responded = true;
    this.revision++;
    try {
      const frame = { t: 'ui-response', reqId: request.req_id };
      if (request.value != null) frame.value = request.value;
      this.ws.send(seal(this.key, frame));
    } catch {
      throw fail('OMP UI response delivery unknown; refresh without replay', 502);
    }
    return { req_id: request.req_id };
  }
  submit(request) { return this.exclusive(() => this.admit(request)); }
  async admit(request) {
    const key = request.idempotency_key;
    if (typeof key !== 'string' || !key.length || key.length > 256 || typeof request.body !== 'string' || !request.body.trim()) throw fail('Invalid OMP prompt', 409);
    if (Buffer.byteLength(request.body) > MAX_PROMPT_BYTES) throw fail('OMP prompt too large', 409);
    const digest = createHash('sha256').update(request.body).digest('hex');
    const previous = this.attempts.get(key);
    if (previous) {
      if (previous.digest !== digest) throw fail('OMP idempotency conflict', 409);
      return { ...previous.result };
    }
    if (this.attempts.size >= 4096) throw fail('OMP idempotency capacity reached', 409);
    const pending = this.pendingInputs();
    if (pending.length >= MAX_PENDING || pending.reduce((n, a) => n + Buffer.byteLength(a.body), 0) + Buffer.byteLength(request.body) > MAX_PENDING_BYTES)
      throw fail('OMP queue capacity reached', 409);
    await this.sync();
    if (request.expected_session_id !== this.host?.sessionId || request.expected_generation !== this.snapshot().generation) throw fail('OMP conversation changed; refresh before submitting', 409);
    if (this.pendingUiRequest) throw fail('OMP UI request pending; respond before submitting', 409);
    if (!this.ready || !this.connected || this.ws?.readyState !== 1) throw fail('OMP Collab disconnected');
    const result = { state: 'queued', queue_position: pending.length + 1, request_id: randomUUID(), session_id: this.host.sessionId, generation: this.snapshot().generation };
    this.attempts.set(key, { digest, body: request.body, result });
    try { await this.persist(); }
    catch (error) { this.attempts.delete(key); throw error; }
    this.revision++;
    await this.drain().catch(() => {});
    return { ...result };
  }
  async drain() {
    try { await this.sync(); }
    catch { this.holdQueued(); await this.persist(); return; }
    if (!this.ready || !this.connected || this.ws?.readyState !== 1) {
      this.holdQueued(); await this.persist(); return;
    }
    const observation = this.observation;
    const generation = this.snapshot().generation;
    for (const attempt of this.attempts.values()) {
      if (attempt.result.state === 'queued' &&
          (attempt.result.session_id !== this.host.sessionId || attempt.result.generation !== generation)) attempt.result.state = 'held';
    }
    if (this.active && observation.host.busy) { this.active.started = true; this.active.busyObserved = true; }
    const idle = check => check.host.busy === false && check.editor.state === 'empty' &&
      !check.host.inputRequired && !this.state.isStreaming && !this.streaming;
    if (!idle(observation) || this.pendingUiRequest) { await this.persist(); return; }
    if (this.active) {
      if (!this.active.started || !this.active.entry_id || (!this.active.stopped && !this.active.busyObserved)) { await this.persist(); return; }
      delete this.active.attempt.body;
      this.active = null;
    }
    // Unknown handoffs survive disconnect/restart and block further delivery.
    if ([...this.attempts.values()].some(a => a.body !== undefined && ['unconfirmed', 'sent'].includes(a.result.state))) {
      await this.persist(); return;
    }
    const attempt = [...this.attempts.values()].find(a => a.result.state === 'queued');
    if (!attempt) { await this.persist(); return; }
    const check = await this.discover();
    if (check.host.instanceId !== this.host.instanceId || check.host.generation !== this.host.generation || check.editor.session_id !== this.host.sessionId) {
      this.holdQueued(); await this.persist(); return;
    }
    if (!idle(check)) { await this.persist(); return; }
    attempt.result.state = 'unconfirmed';
    // Persist the uncertain handoff BEFORE touching the upstream socket.
    try { await this.persist(); }
    catch (error) { attempt.result.state = 'held'; throw error; }
    this.active = { attempt, started: false, entry_id: null };
    try { this.ws.send(seal(this.key, { t: 'prompt', text: attempt.body })); }
    catch { /* No replay and no subsequent dispatch without start evidence. */ }
    this.revision++;
  }
}

export async function run(bindingPath, argv) {
  process.umask(0o077);
  const binding = JSON.parse(await fs.readFile(bindingPath, 'utf8'));
  await privateStat(path.dirname(binding.socket), 'isDirectory');
  const relay = startRelay();
  relay.setHostPid(null);
  await fs.writeFile(binding.overlay, JSON.stringify({ collab: { autoStart: 'control', relayUrl: relay.url } }), { mode: 0o600 });
  const guest = new ManagedGuest(binding, relay);
  await guest.restore();
  let child, connections = 0, closed = false, draining = false;
  const server = net.createServer(socket => {
    if (++connections > 8) { connections--; socket.destroy(); return; }
    let chunks = [], bytes = 0, handled = false;
    socket.setTimeout(2500, () => socket.destroy());
    socket.on('error', () => {}); socket.once('close', () => connections--);
    socket.on('data', async data => {
      if (handled) return;
      chunks.push(data); bytes += data.length;
      if (bytes > 1024 * 1024) { handled = true; socket.destroy(); return; }
      if (!data.includes(10)) return;
      handled = true;
      const response = { actor: binding.actor, launch_session_id: binding.launch_session_id };
      try {
        const request = JSON.parse(Buffer.concat(chunks).toString().split('\n')[0]);
        if (request.actor !== binding.actor || request.launch_session_id !== binding.launch_session_id) throw fail('OMP launch binding changed', 409);
        response.result = await guest.exclusive(async () => {
          if (closed) throw fail('OMP Collab shutting down');
          if (request.operation === 'snapshot') return await guest.refreshSnapshot();
          if (request.operation === 'history') return await guest.history(request);
          if (request.operation === 'submit') return await guest.admit(request);
          if (request.operation === 'ui_response') return await guest.respondUi(request);
          if (request.operation === 'models' || request.operation === 'set_model') return await guest.models(request, request.operation === 'set_model');
          throw fail('Unsupported OMP operation', 409);
        });
      } catch (error) {
        response.error = error.status_code ? error.message : 'OMP Collab unavailable'; response.status_code = error.status_code ?? 503;
      }
      socket.end(JSON.stringify(response) + '\n');
    });
  });
  // Never unlink an existing endpoint: each launch gets a unique socket.
  await new Promise((resolve, reject) => { server.once('error', reject); server.listen(binding.socket, resolve); });
  await fs.chmod(binding.socket, 0o600);
  const drainTimer = setInterval(() => {
    if (closed || draining) return;
    draining = true;
    guest.exclusive(() => guest.drain()).catch(() => {}).finally(() => { draining = false; });
  }, 250);
  const ownerPid = process.ppid;
  const stopChild = createProcessGroupStopper(() => child?.pid);
  const ownerWatch = setInterval(() => {
    if (process.ppid !== ownerPid) stopChild('SIGHUP').catch(() => {});
  }, 1000);
  const cleanup = async () => {
    if (closed) return; closed = true;
    clearInterval(ownerWatch);
    clearInterval(drainTimer);
    try { await guest.exclusive(async () => { guest.reset(); await guest.persist(); }); }
    finally { server.close(); relay.close(); }
    await fs.unlink(binding.socket).catch(() => {});
    await fs.unlink(binding.overlay).catch(() => {});
    if (path.basename(path.dirname(binding.socket)).startsWith('ccb-omp-')) await fs.rmdir(path.dirname(binding.socket)).catch(() => {});
  };
  for (const signal of ['SIGTERM', 'SIGINT', 'SIGHUP']) process.on(signal, () => { stopChild(signal).catch(() => {}); });
  try {
    child = spawn(argv[0], argv.slice(1), { stdio: 'inherit', detached: true });
    relay.setHostPid(child.pid);
    process.exitCode = await new Promise((resolve, reject) => { child.once('error', reject); child.once('exit', code => resolve(code ?? 1)); });
  } finally {
    try { await stopChild('SIGHUP'); }
    finally { await cleanup(); }
  }
}
if (import.meta.main) {
  const [binding, separator, ...argv] = process.argv.slice(2);
  if (!binding || separator !== '--' || !argv.length) process.exit(64);
  try { await run(binding, argv); }
  catch { console.error('CCB: managed OMP Collab startup failed (no capabilities logged)'); process.exit(1); }
}
