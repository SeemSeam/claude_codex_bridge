from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

from provider_backends.omp import launcher as omp_launcher
from provider_backends.pi import launcher as pi_launcher




@pytest.mark.skipif(shutil.which("bun") is None, reason="Bun is required for extension event replay")
@pytest.mark.parametrize("provider", ["pi", "omp"])
def test_extension_replays_model_control_new_turn_and_session_switch(tmp_path: Path, provider: str) -> None:
    extension = tmp_path / "extension.ts"
    harness = tmp_path / "harness.ts"
    events = tmp_path / "events.jsonl"
    dispatches = tmp_path / "dispatch.jsonl"
    source = pi_launcher._PI_COMPLETION_EXTENSION_SOURCE if provider == "pi" else omp_launcher._omp_completion_extension_source()
    extension.write_text(source, encoding="utf-8")
    harness.write_text(
        r'''
import extension from "./extension.ts";
import { appendFileSync, readFileSync } from "node:fs";
import { createHash } from "node:crypto";

const handlers = new Map<string, Array<Function>>();
const pi = {
  on(name: string, handler: Function) {
    const values = handlers.get(name) || [];
    values.push(handler);
    handlers.set(name, values);
  },
};
extension(pi);
const context = {
  sessionManager: {
    getSessionId: () => "native-session",
    getSessionFile: () => "/tmp/native-session.jsonl",
  },
};
async function emit(name: string, event: any = {}, ctx: any = context) {
  for (const handler of handlers.get(name) || []) await handler(event, ctx);
}
function rows() {
  const value = readFileSync(process.env.CCB_TEST_COMPLETION_EVENTS!, "utf8");
  return value.split(/\r?\n/).filter(Boolean).map((line) => JSON.parse(line));
}
function dispatch(reqId: string, prompt: string) {
  const runtime = rows()[0].runtime_instance_id;
  appendFileSync(process.env.CCB_TEST_DISPATCH_EVENTS!, JSON.stringify({
    schema_version: 1,
    actor: process.env.CCB_CALLER_ACTOR,
    launch_session_id: process.env.CCB_SESSION_ID,
    runtime_instance_id: runtime,
    req_id: reqId,
    dispatch_id: "dispatch-" + reqId,
    prompt_sha256: createHash("sha256").update(prompt, "utf8").digest("hex"),
  }) + "\n");
}
async function start(reqId: string) {
  const prompt = "CCB_REQ_ID: " + reqId + "\nperform task";
  dispatch(reqId, prompt);
  await emit("input", { text: prompt, source: "interactive" });
  await emit("before_agent_start", { prompt, images: [], systemPrompt: [] });
  await emit("agent_start");
}
const parentContext = context;
const subagentContext = {
  sessionManager: {
    getSessionId: () => "subagent-session",
    getSessionFile: () => "/tmp/subagent-session.jsonl",
  },
};

await emit("session_start", {}, parentContext);
await start("job_model");
await emit("input", { text: "/model", source: "interactive" });
if (rows().some((row) => row.type === "request_superseded" && row.req_id === "job_model")) {
  throw new Error("/model superseded the active request");
}
if (process.env.CCB_TEST_PROVIDER === "omp") await emit("agent_end", { messages: [], willContinue: false }, parentContext);
else await emit("agent_settled", {}, parentContext);
const settled = rows().findLast((row) => row.type === "agent_settled");
if (settled?.req_id !== "job_model") throw new Error("/model lost request attribution");

await start("job_subagent");
await emit("before_agent_start", { prompt: "subagent delegated review", images: [], systemPrompt: [] }, subagentContext);
await emit("agent_start", {}, subagentContext);
await emit("agent_end", { messages: [{ role: "assistant", content: "subagent done" }] }, subagentContext);
if (process.env.CCB_TEST_PROVIDER === "omp") await emit("agent_end", { messages: [{ role: "assistant", content: "parent done" }], willContinue: false }, parentContext);
else {
  await emit("agent_end", { messages: [{ role: "assistant", content: "parent done" }] }, parentContext);
  await emit("agent_settled", {}, parentContext);
}
const subRows = rows().filter((row) => row.type === "request_superseded" && row.req_id === "job_subagent");
if (subRows.length > 0) throw new Error("subagent turn superseded the parent request");
const subSettled = rows().findLast((row) => row.type === "agent_settled");
if (subSettled?.req_id !== "job_subagent") throw new Error("parent request lost attribution after subagent turns");
if (process.env.CCB_TEST_PROVIDER === "omp") {
  // OMP folds settle into agent_end; verify the parent agent_end text directly.
  const parentEnd = rows().findLast((row) => row.type === "agent_end" && row.req_id === "job_subagent");
  if (String(parentEnd?.assistant?.text || "") !== "parent done") throw new Error("subagent output leaked into the parent reply");
} else if (String(subSettled?.assistant?.text || "") !== "parent done") throw new Error("subagent output leaked into the parent reply");

await start("job_human");
await emit("input", { text: "human follow-up", source: "interactive" });
await emit("before_agent_start", { prompt: "human follow-up", images: [], systemPrompt: [] });
const human = rows().findLast((row) => row.type === "request_superseded");
if (human?.req_id !== "job_human" || human?.superseded_by !== "unmanaged_agent_turn") {
  throw new Error("actual unmanaged agent turn did not supersede the request");
}

await start("job_switch");
await emit("session_switch", { reason: "new", previousSessionFile: "/tmp/old.jsonl" });
const switched = rows().findLast((row) => row.type === "request_superseded");
if (switched?.req_id !== "job_switch" || switched?.superseded_by !== "session_switch:new") {
  throw new Error("session switch did not supersede the request");
}
''',
        encoding="utf-8",
    )
    env = dict(os.environ)
    env.update(
        CCB_PI_COMPLETION_EVENTS=str(events),
        CCB_PI_DISPATCH_EVENTS=str(dispatches),
        CCB_OMP_COMPLETION_EVENTS=str(events),
        CCB_OMP_DISPATCH_EVENTS=str(dispatches),
        CCB_OMP_COMPOSER_SOCKET="",
        CCB_TEST_COMPLETION_EVENTS=str(events),
        CCB_TEST_DISPATCH_EVENTS=str(dispatches),
        CCB_TEST_PROVIDER=provider,
        CCB_CALLER_ACTOR="demo",
        CCB_SESSION_ID="ccb-test-session",
    )
    completed = subprocess.run(
        [shutil.which("bun") or "bun", str(harness)],
        cwd=tmp_path,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert completed.returncode == 0, completed.stdout + completed.stderr
    parsed = [json.loads(line) for line in events.read_text(encoding="utf-8").splitlines()]
    assert not any(
        row.get("type") == "request_superseded" and row.get("req_id") == "job_model"
        for row in parsed
    )
