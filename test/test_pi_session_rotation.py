from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

from completion.models import CompletionStatus
from provider_backends.omp.launcher import _omp_completion_extension_source
from provider_backends.pi.launcher import _PI_COMPLETION_EXTENSION_SOURCE
from provider_backends.pi.pane_events import inspect_pi_runtime
from test_pi_pane_execution import (
    ACTOR, LAUNCH_ID, NOW, _FakeBackend, _FakeSession, _append,
    _bind, _context, _event, _job, _runtime, _start_ready,
)
from provider_backends.pi.pane_execution import PiPaneExecutionAdapter


_ROTATION_REPLAY = r'''
import extension from "./extension.ts";
import assert from "node:assert/strict";
import { appendFileSync, readFileSync } from "node:fs";
import { createHash } from "node:crypto";

const { prompt, dispatch, mode, reason, provider } = JSON.parse(readFileSync("input.json", "utf8"));
function context(id: string) {
  return { sessionManager: {
    getSessionId: () => id, getSessionFile: () => "/sessions/" + id + ".jsonl",
  }};
}
const a = context("main-a"), b = context("main-b"), c = context("main-c");
const child = context("child-a");
function instance() {
  const handlers = new Map<string, Function[]>();
  extension({ on(name: string, handler: Function) {
    handlers.set(name, [...(handlers.get(name) || []), handler]);
  }});
  return async (name: string, event: any = {}, ctx: any = a) => {
    for (const handler of handlers.get(name) || []) await handler(event, ctx);
  };
}
function rows() {
  return readFileSync(process.env.CCB_TEST_EVENTS!, "utf8").trim().split(/\r?\n/).filter(Boolean).map(JSON.parse);
}
function sendDispatch(reqId: string, text: string, nonce: string) {
  appendFileSync(process.env.CCB_TEST_DISPATCH!, JSON.stringify({
    ...dispatch, req_id: reqId, dispatch_id: nonce,
    runtime_instance_id: rows()[0].runtime_instance_id,
    prompt_sha256: createHash("sha256").update(text).digest("hex"),
  }) + "\n");
}
async function start(emit: Function, ctx: any, text: string) {
  await emit("input", { text, source: "interactive" }, ctx);
  await emit("before_agent_start", { prompt: text }, ctx);
  await emit("agent_start", {}, ctx);
}
async function finish(emit: Function, ctx: any, text: string) {
  const message = { role: "assistant", stopReason: "stop", content: [{ type: "text", text }] };
  await emit("message_end", { message }, ctx);
  await emit("turn_end", { message, toolResults: [] }, ctx);
  await emit("agent_end", { messages: [message], willContinue: false }, ctx);
  if (provider === "pi") await emit("agent_settled", {}, ctx);
}
function ownerRows(start: number) {
  return rows().slice(start).filter(row => row.type !== "subagent_turn");
}
const old = instance();
await old("session_start", { reason: "startup" }, a);
const oldPrompt = "CCB_REQ_ID: displaced\nCCB_DISPATCH_ID: " + "a".repeat(32) + "\nold task";
sendDispatch("displaced", oldPrompt, "a".repeat(32));
await start(old, a, oldPrompt);

// The before event is cancellable; no successful lifecycle notification follows.
await old("session_before_switch", { reason: "new" }, a);
await old("session_before_fork", { entryId: "missing" }, a);
assert.equal(rows().filter(row => row.type === "request_superseded").length, 0);

const foreign = instance();
let marker = rows().length;
await foreign("session_start", { reason: "startup" }, child);
// A child cannot later promote its factory using a copied parent lineage.
await foreign("session_start", { reason: "new", previousSessionFile: "/sessions/main-a.jsonl" }, child);
await foreign("session_switch", { reason: "resume", previousSessionFile: "/sessions/child-a.jsonl" }, context("child-b"));
assert.deepEqual(ownerRows(marker), [], "child readiness/native observation escaped");

// Reject unrelated successful notifications as well as missing identities.
marker = rows().length;
await instance()("session_start", { reason, previousSessionFile: "/sessions/unrelated.jsonl" }, b);
await old("session_switch", { reason, previousSessionFile: "/sessions/unrelated.jsonl" }, b);
await old("session_switch", { reason, previousSessionFile: "" }, b);
await old("input", { text: prompt, source: "interactive" }, {});
assert.deepEqual(ownerRows(marker), [], "unrelated session changed ownership");

const current = mode === "cached" ? instance() : old;
await current(mode === "legacy" ? "session_switch" : "session_start", {
  reason, previousSessionFile: "/sessions/main-a.jsonl",
}, b);
const displaced = rows().filter(row => row.type === "request_superseded" && row.req_id === "displaced");
assert.equal(displaced.length, 1, "main replacement did not supersede its old request exactly once");
assert.equal(displaced[0].superseded_by, "session_switch:" + reason);
const rotationCount = rows().length;

sendDispatch(dispatch.req_id, prompt, dispatch.dispatch_id);
marker = rows().length;
await foreign("input", { text: prompt, source: "interactive" }, child);
await foreign("before_agent_start", { prompt }, child);
await foreign("agent_start", {}, child);
await finish(foreign, child, "CHILD OUTPUT");
assert.deepEqual(ownerRows(marker), [], "child consumed the next dispatch or emitted native/terminal events");
await start(current, b, prompt);

marker = rows().length;
await current("session_start", { reason: "reload" }, b);
assert.deepEqual(ownerRows(marker), [], "duplicate start reset readiness during an active request");
await current("session_switch", { reason, previousSessionFile: "/sessions/main-a.jsonl" }, b);
assert.equal(rows().slice(marker).filter(row => row.type !== "native_session").length, 0,
  "duplicate switch superseded or reset an active request");
const busyCount = rows().length;

// Old main events arrive after the next request was bound. They must not finish it.
marker = rows().length;
await finish(old, a, "LATE OLD OUTPUT");
await old("session_start", { reason: "startup" }, a);
assert.deepEqual(ownerRows(marker), [], "old main reclaimed or completed the new request");
await finish(current, b, "ROTATED_OK");
const final = rows().filter(row => row.type === "agent_settled" && row.req_id === dispatch.req_id);
assert.equal(final.length, 1);
assert.equal(final[0].assistant.text, "ROTATED_OK");

// Repeat the switch, proving that lineage tracks B rather than staying on A.
const last = mode === "cached" ? instance() : current;
await last(mode === "legacy" ? "session_switch" : "session_start", {
  reason: "new", previousSessionFile: "/sessions/main-b.jsonl",
}, c);
const lastPrompt = "CCB_REQ_ID: last\nCCB_DISPATCH_ID: " + "c".repeat(32) + "\nlast task";
sendDispatch("last", lastPrompt, "c".repeat(32));
await start(last, c, lastPrompt);
await finish(last, c, "LAST_OK");
assert.equal(rows().findLast(row => row.type === "agent_settled").assistant.text, "LAST_OK");
console.log(JSON.stringify({ instance: rows()[0].runtime_instance_id, rotationCount, busyCount }));
'''


@pytest.mark.skipif(shutil.which("bun") is None, reason="Bun is required for extension event replay")
@pytest.mark.parametrize("provider", ("pi", "omp"))
@pytest.mark.parametrize("mode", ("cached", "legacy", "reused-start"))
@pytest.mark.parametrize("reason", ("new", "resume", "fork"))
def test_generated_extension_rotates_main_and_fences_child_and_old_main(
    monkeypatch, tmp_path: Path, provider: str, mode: str, reason: str,
) -> None:
    adapter, submission, backend, events, dispatches = _start_ready(monkeypatch, tmp_path)
    source = _PI_COMPLETION_EXTENSION_SOURCE if provider == "pi" else _omp_completion_extension_source()
    (tmp_path / "extension.ts").write_text(source, encoding="utf-8")
    (tmp_path / "harness.ts").write_text(_ROTATION_REPLAY, encoding="utf-8")
    (tmp_path / "input.json").write_text(json.dumps({
        "prompt": backend.sent[0][1], "dispatch": json.loads(dispatches.read_text()),
        "mode": mode, "reason": reason, "provider": provider,
    }), encoding="utf-8")
    events.write_text("")
    dispatches.write_text("")
    env = dict(os.environ)
    env.update(CCB_PI_COMPLETION_EVENTS=str(events), CCB_PI_DISPATCH_EVENTS=str(dispatches),
               CCB_OMP_COMPLETION_EVENTS=str(events), CCB_OMP_DISPATCH_EVENTS=str(dispatches),
               CCB_OMP_COMPOSER_SOCKET="", CCB_TEST_EVENTS=str(events),
               CCB_TEST_DISPATCH=str(dispatches), CCB_CALLER_ACTOR=ACTOR, CCB_SESSION_ID=LAUNCH_ID)
    result = subprocess.run([shutil.which("bun") or "bun", "harness.ts"], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    replay = json.loads(result.stdout)
    instance = replay["instance"]
    if provider == "pi":
        records = [json.loads(line) for line in events.read_text().splitlines()]
        snapshot = tmp_path / "inspection.jsonl"
        snapshot.write_text("".join(json.dumps(row) + "\n" for row in records[:replay["rotationCount"]]))
        rotated = inspect_pi_runtime(snapshot, actor=ACTOR, launch_session_id=LAUNCH_ID)
        assert rotated.ready and not rotated.busy
        assert rotated.native_session_id == "main-b"
        snapshot.write_text("".join(json.dumps(row) + "\n" for row in records[:replay["busyCount"]]))
        busy = inspect_pi_runtime(snapshot, actor=ACTOR, launch_session_id=LAUNCH_ID)
        assert busy.busy and busy.active_req_id == submission.job_id
        start = next(i for i, row in enumerate(records)
                     if row["type"] == "request_start" and row["req_id"] == submission.job_id)
        events.write_text("".join(json.dumps(row) + "\n" for row in records[start:]))
        submission = replace(submission, runtime_state={
            **submission.runtime_state, "runtime_instance_id": instance, "event_offset": 0,
        })
        outcome = adapter.poll(submission, now="2026-11-09T00:00:05Z")
        assert outcome is not None and outcome.decision is not None
        assert outcome.decision.status is CompletionStatus.COMPLETED
        assert outcome.decision.reply == "ROTATED_OK"


def _native_session(tmp_path: Path, session_dir: Path, identity: str, *, create=True) -> Path:
    path = session_dir / f"{identity}.jsonl"
    if create:
        path.write_text(json.dumps({"type": "session", "id": identity, "cwd": str(tmp_path)}) + "\n")
    return path


def _bound_runtime(monkeypatch, tmp_path: Path):
    data, events, dispatch = _runtime(tmp_path)
    session_dir = tmp_path / "sessions"
    session_dir.mkdir()
    old_path = _native_session(tmp_path, session_dir, "old")
    record = tmp_path / ".ccb" / ".pi-pi1-session"
    data.update(session_file=str(record), work_dir=str(tmp_path), pi_session_dir=str(session_dir))
    record.write_text(json.dumps({
        **data, "pi_session_id": "old", "pi_session_path": str(old_path),
    }))
    _append(events, _event("extension_ready", pi_session_id="old", pi_session_path=str(old_path)))
    backend = _FakeBackend()
    _bind(monkeypatch, session=_FakeSession(data), backend=backend)
    adapter = PiPaneExecutionAdapter()
    submission = adapter.start(_job(), context=_context(tmp_path), now=NOW)
    return adapter, submission, events, record, session_dir


@pytest.mark.parametrize("mutation", ("valid", "actor", "launch_session_id", "runtime_instance_id", "empty-runtime"))
def test_inspect_native_observation_requires_current_ready_runtime(tmp_path: Path, mutation: str) -> None:
    events = tmp_path / "events.jsonl"
    before = _event("native_session", pi_session_id="before-ready", pi_session_path="/before-ready.jsonl")
    native = _event("native_session", pi_session_id="current", pi_session_path="/current.jsonl")
    if mutation == "empty-runtime":
        native["runtime_instance_id"] = ""
    elif mutation != "valid":
        native[mutation] = "foreign"
    _append(events, before, _event("extension_ready", pi_session_id="old", pi_session_path="/old.jsonl"), native)
    observation = inspect_pi_runtime(events, actor=ACTOR, launch_session_id=LAUNCH_ID)
    assert observation.native_session_id == ("current" if mutation == "valid" else "old")
    assert observation.native_session_path == ("/current.jsonl" if mutation == "valid" else "/old.jsonl")


@pytest.mark.parametrize("mutation", ("valid", "actor", "launch_session_id", "runtime_instance_id", "empty-runtime", "path", "header", "missing-id"))
def test_poll_persists_only_valid_current_native_observations(monkeypatch, tmp_path: Path, mutation: str) -> None:
    adapter, submission, events, record, session_dir = _bound_runtime(monkeypatch, tmp_path)
    path = _native_session(tmp_path, session_dir, "current")
    observation = _event("native_session", pi_session_id="current", pi_session_path=str(path))
    if mutation in {"actor", "launch_session_id", "runtime_instance_id"}:
        observation[mutation] = "foreign"
    elif mutation == "path":
        observation["pi_session_path"] = str(tmp_path / "outside.jsonl")
    elif mutation == "header":
        path.write_text(json.dumps({"type": "session", "id": "wrong", "cwd": str(tmp_path)}) + "\n")
    elif mutation == "empty-runtime":
        observation["runtime_instance_id"] = ""
    elif mutation == "missing-id":
        observation["pi_session_id"] = ""
    _append(events, observation)
    result = adapter.poll(submission, now="2026-11-09T00:00:02Z")
    assert result is not None
    assert json.loads(record.read_text())["pi_session_id"] == ("current" if mutation == "valid" else "old")


def test_poll_retries_latest_native_observation_after_delayed_transcript(monkeypatch, tmp_path: Path) -> None:
    adapter, submission, events, record, session_dir = _bound_runtime(monkeypatch, tmp_path)
    path = _native_session(tmp_path, session_dir, "current", create=False)
    _append(events, _event("native_session", pi_session_id="current", pi_session_path=str(path)))
    first = adapter.poll(submission, now="2026-11-09T00:00:02Z")
    assert first is not None
    assert json.loads(record.read_text())["pi_session_id"] == "old"
    _native_session(tmp_path, session_dir, "current")
    second = adapter.poll(first.submission, now="2026-11-09T00:00:03Z")
    assert second is not None
    assert json.loads(record.read_text())["pi_session_id"] == "current"


def test_newer_empty_observation_cancels_retry_of_old_candidate(monkeypatch, tmp_path: Path) -> None:
    adapter, submission, events, record, session_dir = _bound_runtime(monkeypatch, tmp_path)
    path = _native_session(tmp_path, session_dir, "delayed", create=False)
    _append(events, _event("native_session", pi_session_id="delayed", pi_session_path=str(path)))
    first = adapter.poll(submission, now="2026-11-09T00:00:02Z")
    assert first is not None
    _append(events, _event("native_session", pi_session_id="newer", pi_session_path=""))
    _native_session(tmp_path, session_dir, "delayed")
    second = adapter.poll(first.submission, now="2026-11-09T00:00:03Z")
    assert second is not None
    assert json.loads(record.read_text())["pi_session_id"] == "old"


def test_resume_rejects_new_runtime_before_persisting_its_binding(monkeypatch, tmp_path: Path) -> None:
    adapter, submission, events, record, session_dir = _bound_runtime(monkeypatch, tmp_path)
    path = _native_session(tmp_path, session_dir, "new-runtime")
    _append(events, _event("extension_ready", runtime_instance_id="other-runtime",
                           pi_session_id="new-runtime", pi_session_path=str(path)))
    resumed = adapter.resume(_job(), submission, context=_context(tmp_path),
                             persisted_state=submission.runtime_state, now="2026-11-09T00:00:02Z")
    assert resumed is None
    assert json.loads(record.read_text())["pi_session_id"] == "old"
