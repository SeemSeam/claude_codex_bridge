# OMP Visible-Pane Completion

Date: 2026-09-05
Status: source implementation plus authenticated source and installed-runtime acceptance complete; awaiting commit

## Goal

Make CCB `ask` input and OMP output visible in the already managed OMP pane.
The former per-job `omp --mode json --print` subprocess remains available only
for explicit rollback and persisted-job compatibility.

## Landed Contract

- New OMP jobs default to `mode=omp_pane`.
- `CCB_OMP_EXECUTION_MODE=headless` selects the previous `mode=omp_run`
  subprocess adapter for new jobs.
- Persisted `omp_run` jobs always route back to the headless adapter regardless
  of the current default.
- The launcher loads an owner-only TypeScript extension and creates owner-only
  lifecycle and dispatch JSONL sidecars under the agent runtime completion
  directory.
- Dispatch binds the exact wrapped-prompt SHA-256, `CCB_REQ_ID`, actor, CCB
  launch session, runtime instance, and one-time dispatch id before terminal
  evidence is accepted.
- OMP 18.1.10 exposes `agent_end.willContinue` rather than Pi's
  `agent_settled`. The OMP extension emits the normalized settle event only
  when `willContinue !== true`; continuation events remain progress.
- Malformed complete sidecar records fail closed. Partial trailing records wait
  for completion. Foreign actor, launch-session, runtime-instance, and request
  events cannot complete a job.
- Prompt-send failure, pane death, superseding unmanaged input, cancellation,
  and extension-readiness timeout preserve the existing pane adapter's bounded
  failure behavior.
- The provider manifest now advertises exact session-event completion and
  mode-aware resume.
- Every OMP managed home receives the required `ask`, `ccb-clear`,
  `ccb-compact`, and `ccb-diagnose` Agent Skills under
  `.omp/agent/skills`. These CCB control skills use the packaged
  Codex-compatible Agent Skills contract and remain enabled when optional
  auth, config, or user skill inheritance is disabled.

## Verification

- Focused Pi/OMP pane and headless completion set: `70 passed`.
- Expanded native execution, provider catalog/registry, runtime launcher,
  execution-service, and restore set: `231 passed`.
- Focused OMP required-control-skill set: `29 passed`.
- Expanded OMP skill projection regression set: `225 passed`.
- `python3 -m py_compile` passed for all changed provider modules.
- `git diff --check` passed.
- OMP 18.1.10 RPC startup accepted the generated extension without a load or
  TypeScript error.

## Authenticated Acceptance

The isolated source runtime at
`/home/bfly/yunwei/test_ccb2/omp-visible-pane-20260905` passed with OMP
18.1.10 and the configured Bingxing model source:

- Job `job_f8f68d50ae6c` visibly showed the exact `CCB_REQ_ID` and request body
  in the managed OMP pane.
- The same pane visibly showed `OMP_VISIBLE_PANE_OK_2_20260905` as the final
  reply.
- Dispatch `23f874cab83e471cb577bdfaf67044b4` matched the exact actor, launch
  session, runtime instance, request id, and prompt digest.
- Lifecycle evidence recorded one matching request, assistant reply,
  `agent_end` with `will_continue=false`, and normalized settle event.
- Trace recorded `reply_count=1`, `status=completed`,
  `completion_reason=omp_run_stop`, and the exact 30-character reply.
- The queue returned to idle with depth zero.

An initial job was discarded as acceptance evidence because inherited caller
environment routed it to another project's same-named `demo` agent. The final
job cleared those caller variables and was verified against the isolated
project id, socket, pane, sidecars, and trace.

## Installed-Runtime Acceptance

The current source candidate was copied into the managed dev installation and
the original project runtime was rebuilt with that installation:

- Bare `ccb` resolves to the managed copied installation rather than the source
  checkout and runs without `CCB_SOURCE_RUNTIME_OK`.
- Daemon generation 6 reports OMP `resume_supported=true` with
  `restore_mode=persisted_mode_dispatch`.
- Job `job_f173b0f5b0d6` visibly showed its exact `CCB_REQ_ID`, request body,
  and `OMP_INSTALLED_ASK_VISIBLE_OK_20260905_1` reply in the `demo` pane.
- Dispatch `8c87f544e9b3408da2293419cec8ffc7` matched the request, actor, launch
  session, and runtime instance. Lifecycle events ended with
  `agent_end(will_continue=false)` and normalized `agent_settled`.
- Trace reported one attempt, one reply, `status=completed`, and
  `completion_reason=omp_run_stop`; the queue and inbox returned to zero.

The installed dev runtime was then refreshed to `8.6.12` and its managed
Python materialized the four ownership-marked CCB control skills for both
existing OMP agents, `demo` and `agent3`. Both agents restarted successfully
and remained bound to their live managed panes. Job `job_2f830698377f` asked
`demo` to inspect its current skill inventory; pane `%1` visibly showed the
request and exact reply `ask,ccb-clear,ccb-compact,ccb-diagnose`. Trace recorded
one attempt, one reply, `status=completed`, and `omp_run_stop`, after which all
agent queues were idle.

Bidirectional native-skill communication also passed between the two installed
OMP agents. In the forward direction, `demo` loaded its projected `ask` skill,
submitted chained child `job_fe44cacefdec` to `agent3`, and completed through
continuation `job_59bcca8f4157`. In the reverse direction, `agent3` loaded its
own projected `ask` skill, submitted `job_d2ffc57b276c` to `demo`, and completed
through `job_1aca6c4f562d`. Both panes visibly showed the skill load, chained
command, child request/reply, and continuation result. Each child and
continuation trace recorded one attempt, one reply, `status=completed`, and
`omp_run_stop`; all queues returned to zero.
