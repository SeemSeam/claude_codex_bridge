"""Native status observes indexed canonical state without content or queue access."""
import os
import shutil
import subprocess

import pytest

from provider_backends.omp.composer_bridge import BRIDGE, IMPORTS


@pytest.mark.skipif(shutil.which('bun') is None, reason='requires OMP Bun runtime')
def test_native_status_events_canonical_todos_and_bounded_branch_scan(tmp_path):
    harness = r'''
import assert from "node:assert/strict";
const actor = "worker", launchSessionId = "launch", runtimeInstanceId = "runtime";
const handlers: Record<string, any[]> = {};
const pi = {on(name: string, callback: any) {(handlers[name] ??= []).push(callback);}};
''' + BRIDGE + r'''
let session = "session", leaf: string | null = null, idle = true, lookups = 0;
const records = new Map<string, any>();
const append = (entry: any) => {records.set(entry.id, {...entry, parentId: leaf}); leaf = entry.id;};
const emit = async (event: string, data: any = {}) => {for (const callback of handlers[event] ?? []) await callback(data, ctx);};
const ctx = {hasUI: true, isIdle: () => idle,
 sessionManager: {getSessionId: () => session, getLeafId: () => leaf,
 getEntry: (id: string) => {lookups++; return records.get(id);},
 getEntries: () => {throw Error("full enumeration");}, getBranch: () => {throw Error("full branch");}},
 hasQueuedMessages: () => {throw Error("queue inspection forbidden");},
 ui: {getEditorText: () => {if (!idle) throw Error("busy draft read"); return "private draft";},
 setEditorText: () => {throw Error("draft mutation");}}};
await emit("session_start");
const inspect = () => editorReply({operation: "inspect", actor, launch_session_id: launchSessionId});
let snapshot = await inspect();
assert.equal(snapshot.state, "nonempty");
assert.ok(!JSON.stringify(snapshot).includes("private draft"));
assert.equal(snapshot.status_bar.todos.total, 0);
idle = false;
snapshot = await inspect();
assert.equal(snapshot.status_bar.is_streaming, true);
assert.equal(snapshot.status_bar.elapsed_ms, null); // extension joined mid-turn
await emit("agent_start");
await new Promise(resolve => setTimeout(resolve, 8));
const started = (await inspect()).status_bar;
assert.ok(started.elapsed_ms >= 0); assert.ok(started.started_at);
await emit("tool_execution_start", {intent: "  Reading native state... "});
assert.equal((await inspect()).status_bar.step, "Reading native state");
await emit("message_update", {message: {content: [{type: "text", text: "secret"},
 {type: "toolCall", arguments: {i: "First"}}, {type: "toolCall", arguments: {i: " Latest... "}}]}});
assert.equal((await inspect()).status_bar.step, "Latest");
assert.equal((await inspect()).status_bar.started_at, started.started_at);
const phases = [{name: "phase", tasks: ["one", "two", "three"].map(content => ({content, status: "pending"}))}];
const todo = (id: string, data: any, extra: any = {}) => append({id, type: "message",
 message: {role: "toolResult", toolName: "todo", details: {op: "init", phases: data}, ...extra}});
todo("init", phases);
assert.equal((await inspect()).status_bar.todos.total, 3);
const initialLeaf = leaf;
phases[0].tasks[0].status = "abandoned";
todo("done", phases);
assert.equal((await inspect()).status_bar.todos.done, 1);
todo("view", [], {details: {op: "view", phases: []}});
todo("failed", [], {isError: true});
assert.equal((await inspect()).status_bar.todos.done, 1);
todo("clear", []);
assert.equal((await inspect()).status_bar.todos.total, 0);
todo("restore", phases);
append({id: "nested-eval", type: "custom", customType: "user_todo_edit", data: {phases: []}});
assert.equal((await inspect()).status_bar.todos.total, 0);
append({id: "user-edit", type: "custom", customType: "user_todo_edit", data: {phases}});
assert.equal((await inspect()).status_bar.todos.total, 3);
leaf = initialLeaf;
await emit("session_tree");
assert.equal((await inspect()).status_bar.todos.total, 3);
assert.equal((await inspect()).status_bar.started_at, null);
for (let index = 0; index < 1700; index++) append({id: "history" + index, type: "message", message: {role: "user", content: "private"}});
await emit("session_tree");
let resolved = false;
for (let query = 0; query < 10; query++) {
 const before = lookups; const bar = (await inspect()).status_bar;
 assert.ok(lookups - before <= 256);
 if (bar.todos.available) {resolved = true; assert.equal(bar.todos.total, 3); break;}
}
assert.ok(resolved);
const before = lookups; await inspect(); assert.equal(lookups, before);
idle = true; await emit("agent_end");
const ended = (await inspect()).status_bar;
assert.equal(ended.is_streaming, false); assert.equal(ended.elapsed_ms, null); assert.equal(ended.step, null);
session = "reconnect"; await emit("session_switch"); idle = false;
assert.equal((await inspect()).status_bar.elapsed_ms, null);
await emit("session_shutdown");
'''
    result = subprocess.run(['bun', 'run', '-'], input=IMPORTS + harness,
                            text=True, capture_output=True, timeout=30,
                            env={**os.environ, 'CCB_OMP_COMPOSER_SOCKET': str(tmp_path / 'status.sock')})
    assert result.returncode == 0, result.stderr
