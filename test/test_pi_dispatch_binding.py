from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from completion.models import CompletionStatus
from provider_backends.pi.launcher import _PI_COMPLETION_EXTENSION_SOURCE
from test_pi_pane_execution import ACTOR, LAUNCH_ID, _context, _job, _start_ready


@pytest.mark.skipif(shutil.which("bun") is None, reason="Bun is required for extension event replay")
@pytest.mark.parametrize(
    "scenario",
    ("image", "legacy_exact", "legacy_changed", "wrong_nonce", "wrong_actor",
     "wrong_launch", "wrong_instance", "replay"),
)
def test_pi_dispatch_binding_survives_input_transform_and_restore(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    scenario: str,
) -> None:
    job = _job(body="Inspect /tmp/reference.jpg and reply IMAGE_OK")
    adapter, submission, backend, events, dispatches = _start_ready(monkeypatch, tmp_path, job=job)
    (tmp_path / "extension.ts").write_text(_PI_COMPLETION_EXTENSION_SOURCE, encoding="utf-8")
    (tmp_path / "input.json").write_text(
        json.dumps({"prompt": backend.sent[0][1],
                    "dispatch": json.loads(dispatches.read_text()), "scenario": scenario}),
        encoding="utf-8",
    )
    harness = tmp_path / "harness.ts"
    harness.write_text(r'''
import extension from "./extension.ts";
import { appendFileSync, readFileSync } from "node:fs";
import { createHash } from "node:crypto";
import assert from "node:assert/strict";

const { prompt: original, dispatch, scenario } = JSON.parse(readFileSync("input.json", "utf8"));
const handlers = new Map<string, Function[]>();
extension({ on(name: string, handler: Function) {
  handlers.set(name, [...(handlers.get(name) || []), handler]);
}});
const context = { sessionManager: {
  getSessionId: () => "native-session", getSessionFile: () => "/tmp/native-session.jsonl",
}};
async function emit(name: string, event: any = {}) {
  for (const handler of handlers.get(name) || []) await handler(event, context);
}
function rows() {
  return readFileSync(process.env.CCB_PI_COMPLETION_EVENTS!, "utf8")
    .split(/\r?\n/).filter(Boolean).map(line => JSON.parse(line));
}
await emit("session_start");
const instance = rows().at(-1).runtime_instance_id;
dispatch.runtime_instance_id = instance;
let raw = original;
if (scenario.startsWith("legacy")) {
  raw = raw.replace(/^CCB_DISPATCH_ID:.*\n/m, "");
  dispatch.prompt_sha256 = createHash("sha256").update(raw).digest("hex");
}
let transformed = raw.replace("/tmp/reference.jpg", "[Image #1]") + "\nAttached image metadata";
if (scenario === "legacy_exact") transformed = raw;
if (scenario === "wrong_nonce") transformed = transformed.replace(/^CCB_DISPATCH_ID:.*$/m,
  "CCB_DISPATCH_ID: " + "f".repeat(32));
if (scenario === "wrong_actor") dispatch.actor = "other-agent";
if (scenario === "wrong_launch") dispatch.launch_session_id = "other-launch";
if (scenario === "wrong_instance") dispatch.runtime_instance_id = "other-instance";
appendFileSync(process.env.CCB_PI_DISPATCH_EVENTS!, JSON.stringify(dispatch) + "\n");
await emit("input", { text: transformed, source: "interactive" });
await emit("before_agent_start", { prompt: transformed });
await emit("agent_start");
const message = { role: "assistant", stopReason: "stop", content: [{ type: "text", text: "IMAGE_OK" }] };
await emit("message_end", { message });
await emit("turn_end", { message, toolResults: [] });
await emit("agent_end", { messages: [message] });
await emit("agent_settled");
if (scenario === "replay") {
  await emit("input", { text: transformed, source: "interactive" });
  await emit("before_agent_start", { prompt: transformed });
  const starts = rows().filter(row => row.type === "request_start");
  assert.equal(starts.filter(row => row.dispatch_matched).length, 1);
  assert.equal(starts.at(-1).dispatch_matched, false);
}
console.log(JSON.stringify({ instance }));
''', encoding="utf-8")
    env = dict(os.environ)
    env.update(CCB_PI_COMPLETION_EVENTS=str(events), CCB_PI_DISPATCH_EVENTS=str(dispatches),
               CCB_CALLER_ACTOR=ACTOR, CCB_SESSION_ID=LAUNCH_ID)
    result = subprocess.run([shutil.which("bun") or "bun", str(harness)], cwd=tmp_path,
                            env=env, capture_output=True, text=True, timeout=30, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    instance = json.loads(result.stdout)["instance"]
    persisted = replace(submission, runtime_state={
        **adapter.export_runtime_state(submission), "runtime_instance_id": instance,
    })
    resumed = adapter.resume(job, persisted, context=_context(tmp_path),
                             persisted_state=persisted.runtime_state, now="2026-07-29T00:00:04Z")
    assert resumed is not None
    outcome = adapter.poll(resumed, now="2026-07-29T00:00:05Z")
    assert outcome is not None and outcome.decision is not None
    if scenario in {"image", "legacy_exact", "replay"}:
        assert outcome.decision.status is CompletionStatus.COMPLETED
        assert outcome.decision.reply == "IMAGE_OK"
        records = [json.loads(line) for line in events.read_text().splitlines()]
        assert len([r for r in records if r["type"] == "agent_settled" and r["req_id"] == job.job_id]) == 1
    else:
        assert outcome.decision.status is CompletionStatus.INCOMPLETE
        assert outcome.decision.reason == "pi_request_binding_invalid"
        assert outcome.decision.reply == ""


def test_pi_no_wrap_preserves_raw_prompt_without_dispatch_header(monkeypatch, tmp_path: Path) -> None:
    _, submission, backend, _, _ = _start_ready(monkeypatch, tmp_path, job=_job(body="raw text", no_wrap=True))
    assert backend.sent == [("%9", "raw text")]
    assert submission.runtime_state["dispatch_id"] == ""


def test_pi_resume_unsent_prompt_preserves_dispatch_nonce(monkeypatch, tmp_path: Path) -> None:
    job = _job()
    adapter, submission, _, _, _ = _start_ready(monkeypatch, tmp_path, job=job)
    persisted = replace(submission, runtime_state={
        **adapter.export_runtime_state(submission), "prompt_sent": False,
    })
    restored = adapter.resume(job, persisted, context=_context(tmp_path),
                              persisted_state=persisted.runtime_state, now="2026-07-29T00:00:01Z")
    assert restored is not None
    nonce = submission.runtime_state["dispatch_id"]
    assert f"CCB_DISPATCH_ID: {nonce}\n" in restored.runtime_state["pending_prompt"]
