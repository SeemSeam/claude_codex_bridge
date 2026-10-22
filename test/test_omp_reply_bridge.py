"""Run generated reply bridge through Bun sockets with native-style JSONL entries."""
import os
import shutil
import subprocess

import pytest

from provider_backends.omp.composer_bridge import BRIDGE, IMPORTS

HARNESS = r'''
import assert from "node:assert/strict";
import {appendFileSync as appendFixture, writeFileSync as writeFixture} from "node:fs";
const actor = "worker", launchSessionId = "launch", runtimeInstanceId = "runtime";
const eventPath = process.env.CCB_OMP_COMPOSER_SOCKET! + ".events";
const handlers: Record<string, any> = {}, sent: any[] = [];
let rejectNext = false;
const pi = {
  on(name: string, callback: any) {handlers[name] = callback;},
  sendMessage(message: any, options: any) {
    const item: any = {message, options}; sent.push(item);
    if (rejectNext) {rejectNext = false; return Promise.reject(Error("ambiguous"));}
    return new Promise<void>(resolve => {item.resolve = resolve;});
  },
};
''' + BRIDGE + r'''
let session = "session", entries: any[] = [], idle = false;
const sessionFile = () => process.env.CCB_OMP_COMPOSER_SOCKET! + "." + session + ".jsonl";
const ctx = {hasUI: true, isIdle: () => idle,
  sessionManager: {getSessionId: () => session, getSessionFile: sessionFile, getEntries: () => entries},
  ui: {getEditorText: () => "", setEditorText: () => {throw Error("editor mutation");}}};
await handlers.session_start({}, ctx);
await new Promise(resolve => setTimeout(resolve, 30));
const request = (operation: string, fields: any = {}, fragment = false): Promise<any> => new Promise((resolve, reject) => {
  const client = createConnection(process.env.CCB_OMP_COMPOSER_SOCKET!);
  let text = "";
  client.on("error", reject);
  client.setTimeout(1500, () => {client.destroy(); reject(Error("blocked on native turn"));});
  client.on("connect", () => {
    const bytes = Buffer.from(JSON.stringify({actor, launch_session_id: launchSessionId,
      runtime_instance_id: runtimeInstanceId, session_id: session, operation, ...fields}) + "\n");
    if (fragment) {
      const split = bytes.indexOf(Buffer.from("界")) + 1;
      client.write(bytes.subarray(0, split)); setTimeout(() => client.write(bytes.subarray(split)), 5);
    } else client.write(bytes);
  });
  client.on("data", chunk => {
    text += chunk.toString();
    if (!text.includes("\n")) return;
    const value = JSON.parse(text);
    assert.equal(value.runtime_instance_id, runtimeInstanceId);
    assert.equal(typeof value.session_id, "string");
    resolve(value); client.destroy();
  });
});
const memoryEntry = (i: number) => ({id: "entry-" + i, type: "custom_message", ...sent[i].message});
const diskEntry = (entry: any) => appendFixture(sessionFile(), JSON.stringify(entry) + "\n");
const persist = (i: number) => {const entry = memoryEntry(i); entries.push(entry); diskEntry(entry);};
'''


def _run(tmp_path, scenario):
    result = subprocess.run(
        ['bun', 'run', '-'], input=IMPORTS + HARNESS + scenario + '\nawait handlers.session_shutdown();\n',
        text=True, capture_output=True, timeout=20,
        env={**os.environ, 'CCB_OMP_COMPOSER_SOCKET': str(tmp_path / 'reply.sock')},
    )
    assert result.returncode == 0, result.stderr


pytestmark = pytest.mark.skipif(shutil.which('bun') is None, reason='requires OMP Bun runtime')


def test_busy_admission_native_receipt_dedup_and_binding(tmp_path):
    _run(tmp_path, r'''
assert.equal((await request("inspect")).reason, "provider_busy");
assert.equal((await request("reply_capabilities")).reply_ingress_version, 1);
assert.equal((await request("reply_capabilities", {actor: "wrong"})).state, "unknown");
const one = {delivery_id: "one", body: "first"}, two = {delivery_id: "two", body: "second"};
for (const binding of [{actor: "wrong"}, {launch_session_id: "wrong"},
                       {runtime_instance_id: "wrong"}, {session_id: "wrong"}]) {
  assert.equal((await request("reply_deliver", {...one, ...binding})).state, "unknown");
}
assert.equal(sent.length, 0);
assert.deepEqual((await Promise.all([request("reply_deliver", one), request("reply_deliver", one),
  request("reply_deliver", two)])).map(x => x.state), ["pending", "pending", "pending"]);
assert.equal(sent.length, 2); // Both business promises are still blocked.
assert.deepEqual(sent[0].options, {deliverAs: "aside", triggerTurn: true});
assert.equal(sent[0].message.customType, "ccb-reply");
assert.equal(sent[0].message.display, true);
assert.equal(sent[0].message.content, one.body);
sent[0].resolve();
assert.equal((await request("reply_status", one)).state, "pending");
assert.equal((await request("reply_deliver", {...one, body: "changed"})).reason, "reply_body_conflict");
const entry = memoryEntry(0); entries.push(entry);
assert.equal((await request("reply_status", one)).state, "pending"); // Memory is not persistence.
diskEntry({...entry, id: "wrong-native-id"});
assert.equal((await request("reply_status", one)).state, "pending");
diskEntry(entry);
const receipt = await request("reply_status", one);
assert.equal(receipt.state, "received");
assert.equal(receipt.delivery_id, one.delivery_id);
assert.equal(receipt.body_sha256, sent[0].message.details.body_sha256);
assert.equal((await request("reply_deliver", one)).state, "received");
assert.equal((await request("reply_status", {delivery_id: "absent"})).state, "missing");
const restored = {id: "restored-native", type: "custom_message", customType: "ccb-reply", content: "restored",
  details: {delivery_id: "restored", body_sha256: replyHash("restored")}};
entries.push(restored); diskEntry(restored);
assert.equal((await request("reply_deliver", {delivery_id: "restored", body: "restored"})).state, "received");
assert.equal((await request("reply_deliver", {delivery_id: "restored", body: "different"})).reason, "reply_body_conflict");
const oldEntries = entries;
session = "new"; entries = [];
await handlers.session_switch({}, ctx);
assert.equal((await request("reply_status", {...one, session_id: "session"})).reason, "reply_binding_changed");
assert.equal((await request("reply_status", one)).state, "missing");
assert.equal((await request("reply_capabilities")).session_id, "new");
session = "session"; entries = oldEntries;
await handlers.session_switch({}, ctx);
assert.equal((await request("reply_status", one)).state, "received");
assert.equal(sent.length, 2);
idle = true;
assert.equal((await request("inspect")).state, "empty");
''')


def test_size_unknown_and_durable_intent_restart(tmp_path):
    _run(tmp_path, r'''
assert.equal((await request("reply_deliver", {delivery_id: "x".repeat(257), body: "x"})).reason, "invalid_delivery_id");
assert.equal((await request("reply_deliver", {delivery_id: "large", body: "界".repeat(87382)})).reason, "invalid_reply_body");
const large = {delivery_id: "x".repeat(256), body: "界".repeat(87381) + "x"};
assert.equal(Buffer.byteLength(large.body), 256 * 1024);
assert.equal((await request("reply_deliver", large, true)).state, "pending");
assert.equal(sent[0].message.content, large.body);
rejectNext = true;
const failed = {delivery_id: "failed", body: "held"};
await request("reply_deliver", failed);
assert.equal((await request("reply_status", failed)).state, "unknown");
assert.equal((await request("reply_deliver", failed)).state, "unknown");
assert.equal(sent.length, 2);
// Simulate process-memory loss: the fsynced owner journal must prevent replay.
replyAttempts.clear();
assert.equal((await request("reply_deliver", failed)).state, "unknown");
assert.equal((await request("reply_status", failed)).state, "unknown");
assert.equal((await request("reply_deliver", {...failed, body: "different"})).reason, "reply_body_conflict");
assert.equal(sent.length, 2);
persist(1);
assert.equal((await request("reply_status", failed)).state, "received");
// A partial native JSONL record cannot be a receipt.
const entry = memoryEntry(0); entries.push(entry);
writeFixture(sessionFile(), JSON.stringify(entry));
assert.equal((await request("reply_status", large)).state, "pending");
appendFixture(sessionFile(), "\n");
assert.equal((await request("reply_status", large)).state, "received");
// Truncated intent journals fail closed, never send again.
appendFixture(replyIntentPath, '{"partial":');
assert.equal((await request("reply_deliver", {delivery_id: "new", body: "new"})).reason, "reply_intent_unknown");
assert.equal(sent.length, 2);
''')


def test_attempt_budget_holds_inflight(tmp_path):
    _run(tmp_path, r'''
for (let i = 0; i < 256; i++) {
  assert.equal((await request("reply_deliver", {delivery_id: String(i), body: "body"})).state, "pending");
}
assert.equal((await request("reply_deliver", {delivery_id: "overflow", body: "body"})).reason, "reply_admission_full");
assert.equal(sent.length, 256);
assert.equal((await request("reply_deliver", {delivery_id: "0", body: "body"})).state, "pending");
persist(0); sent[0].resolve();
assert.equal((await request("reply_deliver", {delivery_id: "overflow", body: "body"})).state, "pending");
assert.equal(sent.length, 257);
assert.equal((await request("reply_deliver", {delivery_id: "0", body: "body"})).state, "received");
''')
