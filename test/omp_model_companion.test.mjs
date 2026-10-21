import { test, expect } from 'bun:test';

// Execute the production embedded companion, not a duplicate implementation.
async function fixture() {
  const source = await Bun.file(new URL('../lib/provider_backends/omp/composer_bridge.py', import.meta.url)).text();
  const bridge = /BRIDGE = r'''([\s\S]*?)'''/.exec(source)[1];
  const handlers = new Map();
  const models = [
    { provider: 'local-test', id: 'first', name: 'First', reasoning: false, apiKey: 'never-expose', headers: { Authorization: 'secret' } },
    { provider: 'local-test', id: 'second', name: 'Second', reasoning: true, thinking: { efforts: ['low', 'high'] }, baseUrl: 'private' },
  ];
  let thinking = 'off', sets = 0, idle = true, draft = '', sessionId = 'native', raceModel = false;
  const entries = [];
  const ctx = {
    hasUI: true, model: models[0], ui: { getEditorText: () => draft, setEditorText: value => { draft = value; } },
    isIdle: () => idle, hasQueuedMessages: () => false,
    modelRegistry: { getAvailable: () => models },
    sessionManager: { getSessionId: () => sessionId, getLeafId: () => null, getEntries: () => entries },
  };
  const pi = {
    on: (type, handler) => handlers.set(type, handler),
    getThinkingLevel: () => thinking,
    setThinkingLevel: value => { thinking = value; entries.push({ type: 'thinking_level_change', id: `effort-${entries.length}` }); },
    setModel: async model => { sets++; ctx.model = raceModel ? models[0] : model; entries.push({ type: 'model_change', id: `model-${entries.length}` }); return true; },
  };
  const saved = process.env.CCB_OMP_COMPOSER_SOCKET;
  process.env.CCB_OMP_COMPOSER_SOCKET = '';
  const compiled = new Bun.Transpiler({ loader: 'ts', target: 'bun' }).transformSync(`function make(pi: any) {
    const runtimeInstanceId = 'runtime', actor = 'a', launchSessionId = 'launch';
    ${bridge}
    return editorReply;
  }`);
  const reply = new Function(`${compiled}; return make;`)()(pi);
  if (saved === undefined) delete process.env.CCB_OMP_COMPOSER_SOCKET;
  else process.env.CCB_OMP_COMPOSER_SOCKET = saved;
  await handlers.get('session_start')(null, ctx);
  const request = operation => ({ operation, actor: 'a', launch_session_id: 'launch' });
  const change = async key => {
    const current = await reply(request('models'));
    return { ...request('set_model'), runtime_instance_id: 'runtime', session_id: 'native',
      model: { provider: 'local-test', id: 'second' }, thinking: 'high',
      expected_model: current.model, expected_thinking: current.thinking_level,
      expected_model_revision: current.model_revision, idempotency_key: key };
  };
  return { reply, request, change, sets: () => sets, busy: () => { idle = false; },
    draft: () => { draft = 'unsent'; }, switch: () => { sessionId = 'other'; },
    raceModel: () => { raceModel = true; }, thinking: () => thinking };
}

test('native catalog returns only sanitized identifiers and baked effort ladder', async () => {
  const f = await fixture(), result = await f.reply(f.request('models'));
  expect(result.models[0]).toEqual({ provider: 'local-test', id: 'first', name: 'First', reasoning_levels: ['off'] });
  expect(result.models[1].reasoning_levels).toEqual(['off', 'low', 'high']);
  expect(JSON.stringify(result)).not.toContain('never-expose');
  expect(JSON.stringify(result)).not.toContain('Authorization');
});
test('native model/effort APIs apply once and return authoritative readback', async () => {
  const f = await fixture(), request = await f.change('change');
  const result = await f.reply(request);
  expect(result.model.id).toBe('second');
  expect(result.thinking_level).toBe('high');
  expect(await f.reply(request)).toEqual(result);
  expect(f.sets()).toBe(1);
});
test('draft, busy, session and model-revision fences reject before mutation', async () => {
  for (const mode of ['draft', 'busy', 'switch', 'revision']) {
    const f = await fixture(), request = await f.change(mode);
    if (mode === 'revision') request.expected_model_revision = 'stale';
    else f[mode]();
    expect((await f.reply(request)).status_code).toBe(409);
    expect(f.sets()).toBe(0);
  }
});
test('model admission is rechecked after microtask boundary', async () => {
  const f = await fixture(), request = await f.change('race');
  const pending = f.reply(request);
  f.draft();
  expect((await pending).status_code).toBe(409);
  expect(f.sets()).toBe(0);
});
test('concurrent native model selection cannot receive the requested effort', async () => {
  const f = await fixture(), request = await f.change('model-race');
  f.raceModel();
  expect((await f.reply(request)).status_code).toBe(502);
  expect(f.thinking()).toBe('off');
});
