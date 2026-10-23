"""Exercise production native history bridge with indexed session-manager APIs."""
import os
import shutil
import subprocess

import pytest

from provider_backends.omp.composer_bridge import BRIDGE, IMPORTS


HARNESS = r'''
import assert from "node:assert/strict";
const actor = "worker", launchSessionId = "launch", runtimeInstanceId = "runtime";
const eventPath = process.env.CCB_OMP_COMPOSER_SOCKET! + ".events";
const handlers: Record<string, any> = {};
const pi = {on(name: string, callback: any) {handlers[name] = callback;}};
''' + BRIDGE + r'''
let session = "session", leaf: string | null = null, lookups = 0;
const records = new Map<string, any>();
const append = (entry: any) => {records.set(entry.id, entry); leaf = entry.id;};
const ctx = {hasUI: true, isIdle: () => true,
  sessionManager: {getSessionId: () => session, getLeafId: () => leaf,
    getEntry: (id: string) => {lookups++; return records.get(id);},
    getEntries: () => {throw Error("full session enumeration forbidden");},
    getBranch: () => {throw Error("full branch allocation forbidden");}},
  ui: {getEditorText: () => "", setEditorText: () => {throw Error("editor mutation");}}};
await handlers.session_start({}, ctx);
await new Promise(resolve => setTimeout(resolve, 30));
const request = (operation: string, fields: any = {}): Promise<any> => new Promise((resolve, reject) => {
  const client = createConnection(process.env.CCB_OMP_COMPOSER_SOCKET!);
  const chunks: Buffer[] = [];
  client.on("error", reject);
  client.setTimeout(2000, () => {client.destroy(); reject(Error("native history timeout"));});
  client.on("connect", () => client.write(JSON.stringify({actor, launch_session_id: launchSessionId,
    runtime_instance_id: runtimeInstanceId, session_id: session, operation, ...fields}) + "\n"));
  client.on("data", chunk => {
    chunks.push(chunk);
    if (!chunk.includes(10)) return;
    const bytes = Buffer.concat(chunks);
    const value = JSON.parse(bytes.toString("utf8"));
    if (!value.error) assert.ok(bytes.length <= (fields.max_bytes ?? 262144), "bounded wire response");
    resolve(value); client.destroy();
  });
});
'''


def _run(tmp_path, scenario):
    result = subprocess.run(
        ['bun', 'run', '-'],
        input=IMPORTS + HARNESS + scenario + '\nawait handlers.session_shutdown();\n',
        text=True, capture_output=True, timeout=40,
        env={**os.environ, 'CCB_OMP_COMPOSER_SOCKET': str(tmp_path / 'history.sock')},
    )
    assert result.returncode == 0, result.stderr


pytestmark = pytest.mark.skipif(shutil.which('bun') is None, reason='requires OMP Bun runtime')


def test_large_active_branch_preserves_raw_metadata_and_bounded_lookup(tmp_path):
    _run(tmp_path, r'''
for (let i = 0; i < 28001; i++) append({id: "e" + i, parentId: leaf,
  type: i % 5 === 0 ? "custom" : "message", timestamp: "native-time",
  ...(i % 5 === 0 ? {customType: "private-metadata", data: {secret: "native-only", index: i}}
    : {message: {role: i % 2 ? "user" : "assistant", content: [{type: "text", text: "entry-" + i}], extra: i}})});
// A foreign sibling exists in the session index, but must never be exported.
records.set("foreign", {id: "foreign", parentId: "e0", type: "custom", data: "foreign-private"});
const first = await request("history_page", {limit: 7});
assert.equal(first.entries.length, 7);
assert.equal(lookups, 7);
assert.deepEqual(first.entries[0], records.get("e28000"));
assert.equal(first.anchor_leaf_id, "e28000");
let cursor: any = null, seen: string[] = [];
do {
  const page = await request("ancestry_page", {cursor, limit: 128});
  assert.ok(page.parent_links.length <= 128);
  for (const [id, parent] of page.parent_links) {
    assert.equal(parent, records.get(id).parentId);
    seen.push(id);
  }
  cursor = page.next_cursor;
} while (cursor);
assert.equal(seen.length, 28001);
assert.equal(new Set(seen).size, 28001);
assert.equal(seen[0], "e28000");
assert.equal(seen.at(-1), "e0");
assert.ok(!seen.includes("foreign"));
assert.ok(!JSON.stringify(await request("ancestry_page")).includes("native-only"));
''')


def test_history_anchor_replay_append_and_branch_owner_fences(tmp_path):
    _run(tmp_path, r'''
for (let i = 0; i < 6; i++) append({id: "e" + i, parentId: leaf, type: "message", message: {role: "user", content: "x"}});
const first = await request("history_page", {limit: 2});
append({id: "appended", parentId: leaf, type: "custom", data: "new"});
const replay = await request("history_page", {cursor: first.start_cursor, limit: 2});
assert.deepEqual(replay.entries, first.entries);
assert.equal(replay.anchor_leaf_id, first.anchor_leaf_id);
assert.equal(replay.leaf_id, "appended");
assert.equal(replay.branch_id, first.branch_id);
assert.equal((await request("inspect")).branch_id, first.branch_id);
assert.equal((await request("history_page", {cursor: first.next_cursor})).entries[0].id, "e3");
for (const fields of [{actor: "wrong"}, {launch_session_id: "wrong"},
  {runtime_instance_id: "wrong"}, {session_id: "wrong"}]) {
  const result = await request("history_page", {...fields, cursor: first.start_cursor});
  assert.equal(result.entries, undefined);
}
assert.equal((await request("history_page", {cursor: first.start_cursor + "x"})).status_code, 409);
assert.equal((await request("ancestry_page", {cursor: first.start_cursor})).status_code, 409);
for (const event of ["session_tree", "session_branch", "session_switch"]) {
  const page = await request("history_page", {limit: 2});
  await handlers[event]({}, ctx);
  assert.equal((await request("history_page", {cursor: page.start_cursor})).status_code, 409);
  assert.notEqual((await request("inspect")).branch_id, page.branch_id);
}
const old = await request("history_page");
session = "other"; await handlers.session_switch({}, ctx);
assert.equal((await request("history_page", {cursor: old.start_cursor})).status_code, 409);
''')


def test_oversized_raw_record_is_lossless_bounded_json_byte_chunks(tmp_path):
    _run(tmp_path, r'''
append({id: "user", parentId: null, type: "message", message: {role: "user", content: "question"}});
const huge = {id: "huge", parentId: leaf, type: "message", timestamp: "native-time",
  message: {role: "toolResult", toolCallId: "call", toolName: "read", isError: false,
    content: [{type: "text", text: "界😀\\\"\n".repeat(40000)}], details: {complete: true}}};
append(huge);
const originalStringify = JSON.stringify;
let serializations = 0;
JSON.stringify = ((value: any, ...args: any[]) => {
  if (value === huge) serializations++;
  return (originalStringify as any)(value, ...args);
}) as typeof JSON.stringify;
let cursor: any = null, bytes: Buffer[] = [], offset = 0, total = 0;
do {
  const page = await request("history_page", {cursor, max_bytes: 8192});
  assert.equal(page.entries.length, 0);
  assert.equal(page.entry_chunks.length, 1);
  const chunk = page.entry_chunks[0];
  assert.equal(chunk.entry_id, "huge"); assert.equal(chunk.offset, offset);
  assert.equal(chunk.encoding, "base64"); total = chunk.total_bytes;
  const part = Buffer.from(chunk.data, "base64"); bytes.push(part); offset += part.length;
  cursor = page.next_cursor;
} while (offset < total);
assert.deepEqual(JSON.parse(Buffer.concat(bytes).toString("utf8")), huge);
assert.equal(serializations, 1, "chunk requests reuse one bounded serialized record");
assert.equal(historySerialized, null, "completed chunk transfer releases retained bytes");
JSON.stringify = originalStringify;
assert.equal((await request("history_page", {cursor, max_bytes: 8192})).entries[0].id, "user");
''')


def test_empty_branch_and_invalid_budgets_do_not_enumerate(tmp_path):
    _run(tmp_path, r'''
const empty = await request("history_page");
assert.deepEqual(empty.entries, []); assert.equal(empty.has_more, false);
assert.equal(empty.next_cursor, null); assert.equal(empty.anchor_leaf_id, null);
for (const fields of [{limit: 0}, {limit: 129}, {limit: 1.5}, {max_bytes: 4095}, {max_bytes: 262145}])
  assert.equal((await request("history_page", fields)).status_code, 400);
assert.equal(lookups, 0);
append({id: "broken", parentId: "missing", type: "custom", data: "must-not-log"});
const broken = await request("history_page");
assert.equal(broken.status_code, 409);
assert.equal(broken.entries, undefined);
assert.ok(!JSON.stringify(broken).includes("must-not-log"));
''')
