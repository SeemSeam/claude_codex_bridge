# Managed Provider Completion Reliability Plan

## 1. Document Role

This document defines the architecture plan for reliable completion detection and terminalization for managed pane-backed providers.

Primary trigger incidents:

- GitHub issue `#180`
  - managed Codex on macOS accepts work but never converges the job out of `running`
- GitHub issue `#181`
  - managed Gemini on macOS leaves jobs in `running` when `AfterAgent` misfires, fires on the wrong turn, or never fires after provider-side failure / long-thinking state

This document is intentionally broader than "mac bugfix notes".

The current incidents were surfaced on macOS, but the design defect is not mac-only:

- provider completion authority is too dependent on one fragile provider-specific signal
- runtime artifact contracts are not validated as a coherent system
- `ccbd` has no generic completion-timeout closure when a provider never emits its expected terminal artifact

This plan applies to:

- managed `codex`
- managed `claude`
- managed `gemini`
- managed `cursor`
- managed `pi`
- managed `omp`
- managed `dsh`

in pane-backed mode. Cursor asks execute in the managed visible pane and use
exact anchored top-level transcript evidence. Pi asks execute in the managed
visible pane and use a provider-local lifecycle sidecar. OMP asks use per-job
structured one-shot subprocesses owned by their managed pane-backed agents.
Cursor and Pi retain explicit headless rollback paths; Pi's 8.5.0 one-shot path
also remains a persisted-job compatibility path.

DSH is service-backed rather than an interactive terminal. Its current POSIX
host process may use a pane as a lifecycle/log carrier, but request and
completion authority come only from the structured Web RPC/event protocol
defined in `docs/dsh-service-provider-contract.md`.

This document does not replace:

- `docs/ccbd-startup-supervision-contract.md`
- `docs/ccbd-diagnostics-contract.md`
- `docs/codex-session-isolation-contract.md`
- `docs/claude-session-isolation-contract.md`
- `docs/gemini-session-isolation-contract.md`
- `docs/opencode-completion-contract.md`

`opencode` already has its own completion contract and should not be silently folded into the changes below.

## 2. Current Problem

### 2.1 Surface Symptoms

Issue `#180` shows:

- Codex pane finishes visible work
- job remains `running`
- `ccb kill -f` is the only way to clear the execution
- runtime file layout is inconsistent:
  - session payload advertises `bridge_output.log`
  - bridge runtime writes `bridge.log`
  - `completion/` is not guaranteed to exist

Issue `#181` shows:

- Gemini hook is registered correctly
- hook artifact may be written for the wrong request id
- hook may emit `completed` with empty reply for an auth / info banner turn
- hook may never emit for the actual request after provider-side API failure or long-thinking stall
- job remains `running` forever because no alternate closure path exists

### 2.2 Shared Structural Root Cause

The shared problem is not "mac has weird timing".

The shared problem is:

```text
provider completion authority
  = split across provider-specific artifact assumptions
  + no unified runtime bootstrap validation
  + no generic no-terminal-evidence timeout
  + weak request-turn attribution for hook-driven providers
```

Current behavior by family:

- Codex
  - modeled as `PROTOCOL_TURN`
  - depends on Codex log/session reading
  - runtime layout contract is duplicated across launcher/session payload/bridge code
- Claude
  - modeled as `SESSION_BOUNDARY`
  - may use observed session events plus hook shortcut
- Gemini
  - modeled as `ANCHORED_SESSION_STABILITY`
  - currently treats hook artifact as the decisive exact terminal source when present

All three families currently lack a single reliability boundary that answers:

- what runtime artifacts must exist before the provider is considered launch-ready
- what the primary completion source is
- what alternative evidence may be used when the primary source is absent or misattributed
- how long a job may remain `running` without new reliable completion evidence
- how `ccbd` should converge the job when the provider stays alive but never emits a valid terminal signal

## 3. Architectural Diagnosis

### 3.1 Missing Completion Reliability Plane

Today the stack is:

- provider adapter emits items / terminal decision
- execution service persists whatever the provider emits
- dispatcher terminalizes the job only when the provider emitted a terminal decision

This means:

- if a provider returns `None` forever, the job remains `running` forever
- `ccbd` has no cross-provider closure authority for "provider alive but completion evidence missing"

That is the central design defect.

### 3.2 Missing Runtime Artifact Contract

Managed pane-backed providers also lack a strict runtime bootstrap contract.

Example from Codex:

- launcher payload says `tmux_log = runtime_dir/bridge_output.log`
- bridge env exports `CODEX_TMUX_LOG = runtime_dir/bridge_output.log`
- bridge runtime writes to `runtime_dir/bridge.log`

This is a direct contract split.

As long as file paths are duplicated across:

- launcher
- bridge runtime
- session payload
- readers

the system will continue to accumulate platform-sensitive residue bugs.

### 3.3 Weak Turn Attribution For Hook Providers

Current Gemini / Claude hook handling is too weakly bound to the active request turn.

Current artifact model:

- one file per `req_id`
- hook script extracts request id from prompt or transcript
- `load_event()` reads a single terminal event file

Problems:

- a hook may fire for an auth / info / retry side turn
- extracted `req_id` may refer to the last visible request marker, not the currently intended answer turn
- empty reply must not be terminalized as `completed`; hook-driven providers
  should report `incomplete` with diagnostics when a completion hook fires
  without assistant-visible reply text

Claude `Stop` hooks must not infer request identity by scanning for the latest
visible `CCB_REQ_ID` or latest `last-prompt`. The hook must bind the current
assistant stop to its actual transcript turn, walk the `parentUuid` chain back
to the turn's prompt user message, skip tool-result user records, and only emit
a completion artifact when that prompt itself is anchored by an outer
`CCB_REQ_ID`. Scheduled-task turns, user-interruption turns, auth/info turns, or
other provider-side turns must not reuse an earlier CCB request id.

This is not a mere parser issue.

It is a missing turn-identity contract.

### 3.4 No Generic No-Evidence Timeout

A managed request can currently get stuck in this bad state:

- pane is alive
- provider is still attached
- request was accepted and started
- partial or unrelated completion items may have been observed
- no valid terminal artifact arrives
- no provider adapter emits terminal decision
- execution stays active forever

That must not be legal.

## 4. Design Goals

The target architecture is:

```text
managed pane-backed request
  = one declared primary completion source
  + one validated runtime artifact contract
  + optional secondary degraded evidence sources
  + one control-plane-owned timeout closure path
```

Required outcomes:

- no managed job may stay `running` forever only because the provider never emitted a valid terminal artifact
- runtime artifact path mismatches must fail fast during startup, not surface later as zombie jobs
- hook-driven providers must not burn a job id on an unrelated auth/info turn
- diagnostics must show which completion source was expected, which one was observed, and why the job terminalized
- the design must preserve provider-family differences without scattering reliability logic across random call sites

## 5. Non-Goals

This plan does not:

- change keeper / `ccbd` lifecycle authority
- redefine `.ccb` startup authority
- replace provider-family-specific readers with one universal parser
- add native Windows support
- change `opencode` completion semantics

## 6. New Boundary: Completion Reliability Contract

Add a new provider/runtime-scoped contract layer:

- `CompletionReliabilityManifest`

This belongs beside existing completion manifests, but serves a different purpose.

Existing manifest answers:

- what completion family the provider belongs to
- what selector family / source kind is expected

New reliability manifest answers:

- what runtime artifacts must exist
- what the primary authority source is
- what secondary evidence sources may be consulted
- whether empty-reply terminalization is allowed
- what no-progress / no-terminal deadlines apply
- what degraded terminal reason to emit when primary completion authority never arrives

### 6.1 Suggested Fields

- `provider`
- `runtime_mode`
- `primary_authority`
  - `protocol_log`
  - `hook_artifact`
  - `session_event_log`
- `required_runtime_artifacts`
  - directories/files expected immediately after launch
- `optional_secondary_sources`
  - `session_log`
  - `pane_capture`
  - `hook_artifact`
  - `protocol_log`
- `allow_empty_terminal_reply`
- `empty_reply_requires_secondary_evidence`
- `no_progress_timeout_s`
- `no_terminal_timeout_s`
- `timeout_terminal_status`
  - usually `incomplete`
- `timeout_terminal_reason`
  - for example `completion_timeout`
- `supports_degraded_pane_capture`

The manifest must be provider-owned data, not inferred ad hoc in CLI code.

## 7. New Boundary: Completion Reliability Monitor

Add a control-plane-owned component in the execution layer:

- `CompletionReliabilityMonitor`

This component must live with provider execution, not in CLI, and not in generic dispatcher routing.

### 7.1 Responsibility

For each active submission, it tracks:

- when the job started
- when runtime bootstrap was confirmed
- when the last progress evidence arrived
- when the last primary-authority evidence arrived
- whether the latest evidence is exact / observed / degraded
- whether the request is past no-progress or no-terminal deadlines

It does not parse provider-specific logs itself.

Instead:

- provider adapters keep parsing provider-native streams
- the monitor evaluates reliability state and closure policy using normalized evidence facts

### 7.2 State Model

Each active submission should expose reliability facts in runtime state:

- `completion_bootstrap_state`
  - `pending`
  - `ready`
  - `failed`
- `completion_primary_state`
  - `waiting`
  - `observed`
  - `terminal`
  - `missing`
- `completion_last_progress_at`
- `completion_last_primary_evidence_at`
- `completion_last_secondary_evidence_at`
- `completion_timeout_deadline_at`
- `completion_reliability_reason`

### 7.3 Terminalization Rule

Default rule:

- a managed pane-backed submission may remain active indefinitely when
  `no_terminal_timeout_s <= 0`
- default runtime behavior should prefer waiting for provider/completion
  authority over synthesizing a timeout when later agent-health detection can
  handle stuck agents separately

Opt-in degraded closure:

- a managed pane-backed submission with `no_terminal_timeout_s > 0` may not
  remain active indefinitely after that deadline without valid primary
  authority

When an opt-in deadline is exceeded, the monitor must produce a terminal
decision.
Provider-native cursor movement, polling timestamps, rescan offsets, and other
reader bookkeeping are not progress evidence and must not extend the deadline.
Only semantic evidence such as request anchor observation, assistant reply text,
terminal artifacts, or provider turn binding should refresh progress.
Session snapshot/rotation bookkeeping is observable state, but it is not
completion progress by itself.

Opt-in degraded closure result:

- `status = incomplete`
- `reason = completion_timeout`
- `confidence = degraded`

If a provider-specific secondary source supports extracting a best-effort reply safely, that reply may be attached with clear degraded diagnostics.

Running-job heartbeat is a separate no-progress diagnostics guard:

- heartbeat observations remain internal diagnostics/events rather than caller-visible replies
- default job heartbeat does not terminalize running `ask` jobs; CCB keeps waiting for provider execution or completion-tracker authority
- `heartbeat_timeout` terminalization is opt-in/health-gated behavior and must not be used as a blind replacement for provider reliability decisions
- when an opt-in timeout policy is enabled, a real terminal provider reply before that threshold remains the only normal caller-facing reply

## 8. New Boundary: Runtime Artifact Layout Contract

Provider launchers must no longer hand-roll runtime file names in multiple places.

Introduce one canonical artifact-layout helper per provider runtime.

### 8.1 Codex

Add a canonical helper such as:

- `codex_runtime_artifact_layout(runtime_dir)`

It must own all runtime paths:

- `input.fifo`
- `output.fifo`
- `bridge.pid`
- `bridge.stdout.log`
- `bridge.stderr.log`
- canonical bridge terminal log
- `completion/`

All of these must be consumed from that helper by:

- launcher payload builder
- bridge env builder
- bridge runtime state
- diagnostics renderers

String literals for these names must not be duplicated across modules.

### 8.2 Hook Providers

Add equivalent helpers for:

- `gemini`
- `claude`

The helper must own:

- `completion/`
- `completion/events/`
- optional auxiliary diagnostics files if introduced later

### 8.3 Boot Validation

Provider startup must assert declared artifacts exist immediately after launch preparation.

Failure mode must be:

- runtime launch degraded / failed

not:

- accept jobs and wait forever

## 9. New Boundary: Turn-Scoped Hook Evidence

Single-file terminal overwrite is too weak for hook-driven providers.

Replace the hook completion model with an append-oriented turn evidence model.

### 9.1 Current Weakness

Current file:

- `completion/events/<req_id>.json`

stores only the latest terminal interpretation.

That loses:

- whether the hook fired multiple times
- whether an empty-reply artifact arrived before a later real reply
- whether the artifact belonged to an auth/info turn versus the request answer turn

### 9.2 Target Model

Use an append-only per-request ledger, for example:

- `completion/events/<req_id>.jsonl`

Each record should include:

- `event_kind`
  - `hook_seen`
  - `hook_empty_reply`
  - `hook_failure`
  - `hook_completed`
  - `hook_cancelled`
- `req_id`
- `provider_turn_ref`
- `session_id`
- `session_path`
- `reply`
- `reply_text_present`
- `hook_event_name`
- `diagnostics`
- `timestamp`

The poller may still synthesize one terminal decision, but the authority read path must be able to distinguish:

- empty informational hook
- wrong-turn hook
- genuine answer completion

## 10. Provider-Specific Repair Plan

### 10.1 Codex

Codex does not need to be forced into the Gemini/Claude hook model.

Native active-turn steering is input transport, not completion authority. When
the visible managed TUI shares an agent-scoped app-server, CCB may use
`turn/steer` with the already bound thread id and an `expectedTurnId`
precondition. Successful steering keeps the same job and immutable top-level
turn binding; it does not synthesize completion, reset reliability timers by
itself, or authorize another turn's assistant/terminal events. A terminal
precondition failure must yield to the existing completion/cancel authority.
Capability additionally requires the runtime-owned remote marker written only
by the TUI's `--remote` branch; a live app-server beside a local-fallback TUI
does not qualify.

Its design should remain:

- primary authority = protocol/session log

But reliability must be fixed in two places.

#### 10.1.1 Fix Runtime Layout Split

Current split:

- launcher/session payload uses `bridge_output.log`
- bridge runtime writes `bridge.log`

This must be unified by canonical layout helper.

Phase 1 chooses:

- canonical Codex bridge terminal log = `bridge.log`

Backward-compatible migration:

- choose one canonical file name
- optionally preserve the old name as a symlink or compatibility alias for one release cycle

#### 10.1.2 Create Declared Runtime Artifacts Up Front

Ensure startup explicitly creates:

- `completion/`
- canonical bridge log path
- other declared runtime files/directories

even if Codex completion does not primarily consume hook artifacts.

This keeps diagnostics and contract shape coherent.

#### 10.1.3 Add Bootstrap Self-Test

Immediately after bridge spawn, validate:

- bridge pid file exists
- bridge log path exists or is writable
- declared runtime artifacts exist

If not:

- mark startup degraded / failed
- do not accept async jobs silently

#### 10.1.4 Recover Stale Bound Session Logs Safely

Codex completion polling may encounter a stale bound session file when the
provider has switched to a new managed session log but the bridge-side binding
tracker did not update `.codex-*-session`.

The completion reader must not globally weaken bound-session isolation. Instead,
while an active job has not yet observed its request anchor, it may switch away
from the bound log only when all of these are true:

- the current bound log has no unread bytes at the captured cursor
- exactly one other log under the same managed Codex session root has matching
  workspace `cwd`
- that candidate log contains the active request anchor
- the candidate has a parseable Codex session id, so subsequent reads remain
  locked to that exact session

This is a completion-layer recovery path, not a replacement for bridge health
supervision. Bridge/helper death or a missing `CCB_SESSION_FILE` should still be
reported as a binding-health problem.

#### 10.1.5 Separate Prompt Delivery Acceptance From Completion Timeout

Codex pane-backed submission has two distinct failure boundaries:

- prompt delivery acceptance: the wrapped prompt must appear in a valid Codex
  protocol log as the active `CCB_REQ_ID`
- completion: after acceptance, Codex must eventually emit assistant/terminal
  evidence for that accepted turn

`running` at the dispatcher layer only means CCB has started the attempt and
sent text toward the pane. It must not be treated as proof that Codex accepted a
protocol turn.

For wrapped Codex turns, submission records `delivery_state = pending_anchor`
until the request anchor is observed. A Codex-specific delivery guard may
terminalize with `reason = codex_prompt_delivery_failed` only when all of these
hold:

- the job is still active, wrapped, and has not observed the request anchor
- the originally bound/current log is drained at the captured cursor, so the
  stale-session fallback has had a chance to run
- no unique newer top-level fallback log under the same agent-managed Codex
  session root contains the exact active request anchor; an exact anchor may
  override stale `cwd` metadata left by `/clear`, but never the managed-root or
  top-level-session boundary
- there is hard evidence that the pane cannot accept the prompt (`Shutting down`
  / `Pane is dead`) or the conservative delivery timeout has elapsed

When that unique exact-anchor fallback exists, the adapter must commit the new
session binding transactionally before polling it: persist the new path/id,
old-binding metadata, and resume command; validate that the candidate still
exists; then replace the in-memory reader. Failed or concurrent persistence
must leave the old binding intact and fail closed rather than polling one log
while the session file names another. Candidate discovery remains off the
steady-state path: it is allowed only while a wrapped delivery is awaiting its
anchor and the bound log is drained.

Reply delivery has the same acceptance boundary. Sending `CCB_REPLY` text to a
pane is not delivery completion. Codex reply-delivery prompts must carry their
own request anchor, remain running after pane dispatch, and consume the mailbox
head only after that exact anchor is observed in the bound protocol log.
The resulting provider acknowledgement is transport work, not a second
business response, so it may be empty only when all delivery proofs agree:
the runtime is explicitly marked for reply delivery, its state is `accepted`,
the request anchor was observed, and the terminal decision is exactly
`reply_delivery_sent` with accepted-delivery diagnostics. Missing any one of
those proofs keeps the normal non-empty-reply fail-closed gate. A confirmed
transport acknowledgement must not be downgraded to
`task_complete_empty_reply`, because terminal reply-delivery recovery would
otherwise requeue the same mailbox head indefinitely.

The first implementation must not automatically resend the prompt. Anchor
absence is observation failure, not proof that Codex never began executing.
Diagnostics should expose `delivery_failure_kind`, `delivery_retryable`, the
checked log/workspace paths, and the delivery timeout so operators can choose an
explicit retry without risking duplicate downstream side effects.

#### 10.1.6 Fence Native Subagent Completion From CCB Replies

Codex native subagents may create a second rollout under the managed agent's
private session root with the same workspace `cwd`. When the native child forks
the parent conversation it may also inherit the active `CCB_REQ_ID`, while
emitting its own `task_started`, collaboration messages, and `task_complete`.
That child evidence is not completion authority for the CCB job assigned to the
parent Codex agent.

The Codex adapter must enforce both boundaries:

- session binding excludes rollouts identified by
  `session_meta.thread_source = subagent` or equivalent native subagent
  provenance from scanning, watchdog updates, persistence, rotation, and
  recovery
- completion binds the top-level parent `turn_id` once and ignores assistant or
  terminal events carrying a different turn id; native collaboration
  `agent_message` content must not enter the reply buffer

The callback/mailbox layer continues routing by the original CCB job lineage.
It must never need to infer native provider parentage from reply text. Both
plain `ask` and `ask --chain` therefore receive only the CCB target agent's
top-level final reply, not a native subagent result.

### 10.2 Gemini

Gemini currently has the most fragile turn attribution.

#### 10.2.1 Strengthen Req-ID Ownership

Hook processing must not infer `completed` merely from "some request id was present in prompt text".

Required conditions for exact completion:

- artifact req_id matches the active request anchor
- hook event is associated with the current provider turn
- reply is non-empty, or provider diagnostics explicitly declare a valid empty terminal turn

If these are not all true:

- do not emit exact `completed`
- record degraded hook evidence instead

#### 10.2.2 Empty Reply Must Not Burn The Job

Current `reply = "[no response text]"` style terminalization should be demoted unless positively proven valid.

Default rule:

- empty reply + no assistant-visible content = `incomplete`, not `completed`
- Claude and Gemini hook readers must also normalize legacy or malformed
  `completed` + empty-reply hook events into terminal `incomplete` decisions
  with `empty_reply`, `empty_provider_reply`, and a human-readable diagnosis.
  Claude first holds an attributable empty Stop-hook as provisional evidence
  for a bounded final-text grace window, because Claude may persist a
  thinking-only `end_turn` snapshot before materializing the visible text for
  the same API message. A visible final that arrives during that window wins;
  only an unchanged empty hook after the grace may become `incomplete`.
- Protocol-turn providers such as managed Codex must normalize
  `task_complete` boundaries with no boundary reply and no prior
  assistant-visible reply evidence into terminal `incomplete` decisions with
  the same empty-reply diagnostic shape.
- Native-transcript providers such as Antigravity (`agy`) must normalize
  native completed/finished evidence with no extracted assistant reply into
  terminal `incomplete` decisions immediately, rather than completing or
  waiting for a long timeout.

#### 10.2.3 API Failure And Long-Thinking Need Closure

If Gemini shows:

- transport/API failure
- long-thinking stall
- hook absence beyond timeout

then the reliability monitor must terminalize with:

- `status = incomplete`
- `reason = completion_timeout` or `api_error`

depending on observed diagnostics

This must happen without requiring `ccb kill -f`.

### 10.3 Claude

Claude should adopt the same reliability plane as Gemini, even if the current public issue is narrower.

Required:

- same append-only hook evidence model
- same empty-terminal guard
- same timeout closure
- same diagnostics surface

Claude-specific session-boundary logic may still provide stronger observed completion than Gemini, but must no longer rely on hook exactness alone.

Claude queued prompt delivery has a separate activation boundary. A
`queue-operation/enqueue` carrying the exact outer request anchor proves only
that Claude accepted the command into its queue. A content-free
`queue-operation/dequeue` is uncorrelated diagnostic evidence. The queued job
becomes active only when Claude replays the exact prompt as
`attachment/queued_command.prompt`; a normal top-level user prompt remains the
idle-REPL activation path. `anchor_seen` may be emitted only after one of those
exact activation records, except for the explicit `no_wrap` contract.

Until activation, assistant text and UUIDs, tool-only and subagent events,
system turn boundaries and API errors, hook artifacts, and pane-idle recovery
must not contribute completion evidence to the queued job. Enqueue, dequeue
observation, activation, and anchoring must persist independently with the
reader cursor across daemon restart. Session rotation clears those correlated
facts and requires activation in the new top-level session. Pane dispatch,
elapsed time, apparent FIFO order, or an idle prompt may not substitute for
exact queued-command identity.

If a dispatched Claude prompt remains visibly staged in the current composer
without exact activation, the polling layer may retry `Enter` at most once.
That recovery is allowed only after the configured grace period, before the
bounded give-up deadline, while the pane is not busy, and while the current
composer—not transcript history—contains the current request marker, the
submitted prompt-tail fingerprint, or Claude's collapsed-paste placeholder.
The retry state and evidence kind are persisted for diagnostics. Sending
`Enter` remains input-delivery recovery only: it must never synthesize
`prompt_activated`, `anchor_seen`, completion, or reply ownership; the exact
Claude transcript records above remain the sole activation authority.

Claude session-name `slug` is display metadata, not subagent identity.
Top-level records with `isSidechain=false` remain eligible for request-anchor
tracking; real sidechains are fenced by `isSidechain=true` or explicit
subagent identity.

Claude assistant transcript records are snapshots, not independent completed
replies. Completion state therefore has three separate layers:

- `reply_buffer` is cumulative visible progress for streaming/diagnostics only
- the active assistant snapshot is keyed by Claude API `message.id`, with the
  transcript UUID used only as a compatibility fallback
- `terminal_reply` is assigned exactly once from the assistant message that
  satisfies a terminal boundary

A thinking-only or tool-only snapshot cannot supply terminal text, even when
it carries `stop_reason=end_turn`. If a later snapshot for the same
`message.id` supplies visible text, it inherits the earlier observed
`end_turn` and may complete. This pending message identity, text,
`stop_reason`, and tool-use state must survive daemon restart. Visible text
without `stop_reason` is progress, not a boundary; it may complete only after
an exact protocol marker, an attributable non-empty Stop hook, or a
`turn_duration` event whose `parentUuid` matches the current top-level
assistant transcript UUID. A tool-only `turn_duration` must not reuse earlier
progress narration.

The terminal boundary payload and completion artifact must use
`terminal_reply`, never the cross-message progress buffer. This preserves
genuine short replies such as `OK` while preventing process narration such as
`Let me read...` from replacing a later full final review. A response whose
first visible line is `API Error: Response stalled mid-stream` is failed and
incomplete provider output, never a completed answer.

Every Claude hook path, including normal polling, orphan recovery, and
cancellation salvage, must validate the exact request id, schema, provider,
agent, workspace, event time, and tracked Claude session before using the
artifact. Prompt activation alone is not proof that an on-disk hook belongs to
the current managed session.

Issue `#282` requires a narrow recovery exception when the provider has already
written an exact terminal Stop-hook artifact but transcript anchor observation
failed. The normal activation boundary remains authoritative during the grace
window. After that window, an orphaned exact hook may terminalize only when all
of these independent proofs agree:

- artifact request id is the active outer request id
- artifact provider, agent, and workspace match the active submission
- artifact timestamp is parseable, no earlier than submission acceptance, and
  no later than the current observation time
- both artifact and tracked Claude session identities are present and equal
- the target pane is observably idle

Session-path comparison may normalize `/` and `\` separators before extracting
the session id, but missing identity must fail closed. Recovery diagnostics
must record that anchor observation was missed and that the exact-hook fallback
was used.

Cancellation is a separate preservation path. Before destructively cancelling
an active Claude submission, the execution service may best-effort capture the
same strictly attributable exact hook without waiting for the orphan grace or
idle-pane proof. Only a non-empty reply is salvageable. Cancellation remains
the terminal job status, while its decision records the captured completion
status/source and preserves the reply or reply artifact. If no reply can be
captured, a forced empty artifact must be labeled as transport metadata rather
than task evidence.

### 10.4 Kimi

Kimi native completion must support both observed provider layouts without
weakening per-launch storage authority:

- legacy `.kimi/sessions/<md5(workdir)>/<session>/wire.jsonl`
- current `.kimi-code/sessions/wd_<basename>_<sha256-prefix>/<session>/agents/<agent>/wire.jsonl`

The launcher records both exact state roots and the completion reader scans
only those roots. A request binds first to a `CCB_REQ_ID` header at the start
of the submitted prompt; legacy fallback uses an exact token boundary and must
not match request-id prefixes or later mentions in another agent's prompt.

`TurnEnd` and successful terminal `step.end` reasons are primary turn
boundaries. A subsequent user turn may close a reply-bearing prior turn when
the provider omitted an explicit boundary. Unknown, cancelled, interrupted,
error, and tool-use finish reasons are not successful completion authority.
Once a native log owns the request anchor, pane text is rescue evidence only
and may not replace an incomplete native observation.

Observed native session paths remain agent-scoped restart authority. Both
layouts must validate the exact non-symlinked root, project directory, session
id, and wire path before exact-session restart; mismatched or missing roots
fail fresh rather than falling back to a workdir-global session.

### 10.5 Qoder

Qoder jobs use the documented print-mode stream contract with an exact
agent-local `--config-dir`, `-w <workspace>`, `-p`,
`--output-format stream-json`, and a deterministic UUID `--session-id`. CCB job
ids must not be passed directly because Qoder rejects non-UUID session ids.

Completion authority is a Qoder `result` envelope with `is_error=false` and a
normal stop reason. Assistant envelopes may provide the latest reply text but
do not terminalize by themselves. `is_error=true`, assistant error fields,
authentication failures, non-normal result reasons, nonzero exit, and a clean
process exit without a result envelope all fail or remain incomplete; they may
not be returned as successful assistant text.

### 10.6 Cursor Visible Transcript Completion

New Cursor asks execute in the exact managed visible pane. The adapter must
wait while the current pane or current-session transcript is active, require a
new top-level `turn_ended` after an observed active turn, and then confirm a
stable idle interval before sending exactly once. Pane text is readiness
evidence only; it is not completion authority.

Before dispatch, CCB captures transcript offsets and excludes top-level paths
that already contain the request anchor. Because Cursor may rewrite the prior
terminal record when appending a turn, pre-anchor discovery may rescan complete
top-level transcripts from the beginning. It binds only to the first eligible
user record containing the exact `CCB_REQ_ID`; subagent transcripts, malformed
or partial records, stale anchors, assistant text before binding, and terminal
records from other paths cannot complete the job.

After binding, assistant text from that same transcript is progress and reply
evidence. Only a later `turn_ended` from the bound transcript terminalizes:
`status=success` requires a non-empty reply to complete, while an empty success
or any other status closes incomplete. Readiness and run timeouts close
incomplete without resending. Cancellation interrupts only a pane to which the
prompt was delivered. Daemon restore cannot prove ownership of an interrupted
in-flight Cursor turn, so it is resubmit-required.

`CCB_CURSOR_EXECUTION_MODE=headless` retains the prior
`agent --print --output-format stream-json` adapter as an explicit rollback
path; it is not the default execution mode.

### 10.7 Pi Visible Lifecycle And OMP Structured Streams

The supported completion contract intentionally targets the current provider
protocols only:

- Pi `0.82.1`
- OMP `17.1.6`

Pi and OMP must have separate observers. Similar JSON event names do not make
their lifecycle semantics interchangeable.

New Pi asks are sent to the existing managed Pi pane. CCB loads one
runtime-owned Pi extension through the official `--extension` surface. The
extension observes lifecycle callbacks and appends a normalized, owner-only
JSONL sidecar in the agent runtime completion directory. It does not read or
write provider auth/configuration state. A separate owner-only dispatch log
binds the exact prompt digest to `req_id`, actor, CCB launch session, and live
runtime instance before the prompt is sent. Each extension process also emits
a random runtime instance id, so daemon restore can distinguish the same
session record from a restarted Pi process. Unmanaged interactive/RPC input
during a bound CCB turn emits explicit supersession evidence; its later reply
cannot be returned as the CCB job's final text.

Pi completion authority is the bound runtime instance's final
`agent_settled` event. `turn_end` is one model/tool turn and `agent_end` is one
low-level run; either may be followed by automatic retry, compaction retry, or
queued continuation. Assistant/tool events are semantic progress only. The
terminal reply comes only from the latest visible assistant text carried by
the settled event; thinking and earlier tool-round narration are excluded.

Pi pane completion additionally requires:

- exact match of request id, actor, CCB launch session, runtime instance, and
  the pre-send sidecar byte offset
- a successful final `stop` outcome
- a non-empty visible reply
- every complete sidecar record to parse

An `error` outcome fails. `aborted`, `length`, `tool_use`, missing outcome, and
empty settled replies are incomplete. A partial trailing sidecar record stays
pending until it becomes a complete newline-delimited record. Pane death,
extension bootstrap failure before dispatch, binding mismatch, and runtime
instance replacement close explicitly; CCB never silently reattributes the
job.

Pi pane execution has no fixed terminal wall-clock cutoff by default. The
provider reliability policy uses
`CCB_PI_NO_TERMINAL_TIMEOUT_S` only when an operator explicitly enables a
semantic no-progress watchdog. This prevents CCB from truncating a valid long
Pi turn. Cancellation interrupts the current pane run without killing the
managed Pi pane. Both pane and headless Pi paths have native cancellation, so
Pi prompts omit the generic model-facing cancel-file probe and avoid its extra
tool call and uncached-token cost.

OMP completion authority is an `agent_end` event carrying
`isTerminal=true`. An `agent_end` with `isTerminal=false`, or without the field,
is progress only. A later `agent_start` likewise invalidates any earlier
terminal observation. A successful terminal `yield` tool result is a valid
final outcome and its structured `details.data` is the reply source.

For OMP, and for Pi jobs intentionally started with
`CCB_PI_EXECUTION_MODE=headless` or restored from persisted `mode=pi_run`,
semantic completion is necessary but not sufficient:

- the one-shot process must exit before CCB terminalizes the job, so stdout is
  closed and late retry/advisor events cannot be truncated
- process exit code must be zero
- the final assistant outcome must be successful (`stop`, or OMP terminal
  `yield`)
- the reply must be non-empty
- every complete JSONL record must parse; an unterminated trailing record is a
  truncated stream

A clean one-shot process exit without the provider-specific semantic event closes as
`incomplete/<provider>_native_terminal_missing`. A semantic event without a
final outcome closes as `incomplete/<provider>_native_outcome_missing`.
Malformed or truncated output closes as
`incomplete/<provider>_native_protocol_invalid`. Nonzero exit and final
provider error remain failures. Older Pi headless streams that stop at
`agent_end` and older OMP streams without `isTerminal` deliberately fail
closed; CCB does not guess legacy completion.

### 10.8 DeepSeek Harness Native Service Events

DSH uses a long-lived loopback Web host and a per-job CCB observer process.
The event WebSocket must be open before `session.prompt`, and the CCB job id is
the native RPC id. The observer binds the exact durable
`user/message.source.rpcId` to its owning `turn/start`, accepts only committed
assistant-visible text from that turn, and requires the same turn's
`turn/end.reason.kind=completed`.

`aborted`, `blocked`, `error`, `max-tokens`, and `interrupted` are native
failure terminals. A reasoning-only or empty assistant message, a completed
turn without the exact native request anchor, a process exit without
`turn/end`, malformed history, or an unknown terminal closes failed or
incomplete. Pane text, pane quietness, and host exit are never fallback success
signals.

After ccbd restart, DSH resumes by scanning the same native session history
for the exact persisted RPC and starting an observer-only bridge if needed.
It never reposts an interrupted prompt. See
`docs/dsh-service-provider-contract.md` for the full carrier, session,
permission, clear, and compact boundaries.

## 11. Placement In Code

### 11.1 Completion Manifest Layer

Add reliability manifest data near provider manifest / completion manifest definitions.

Likely modules:

- `lib/completion/`
- `lib/provider_core/`
- provider-specific `manifest.py`

### 11.2 Execution Layer

Add reliability monitor in provider execution service path.

Likely modules:

- `lib/provider_execution/service.py`
- `lib/provider_execution/service_runtime/`

This is the correct layer because it already owns:

- active submissions
- polling cadence
- persisted execution state

### 11.3 Provider-Specific Readers

Provider readers remain provider-owned.

Likely modules:

- `lib/provider_backends/codex/execution_runtime/`
- `lib/provider_backends/gemini/execution_runtime/`
- `lib/provider_backends/claude/execution_runtime/`
- `bin/ccb-provider-finish-hook`
- `lib/provider_hooks/artifacts_runtime/`

Managed Claude preparation also owns one legacy completion-hook migration. An
existing project or local Claude settings file may contain the old CCB command
shape `python .../ccb-provider-finish-hook`, which asks Python to parse the
extensionless Bash launcher. Preparation must remove only that CCB-specific
Python-wrapped finish/activity command, preserve unrelated hooks and settings,
and install the current direct launcher command in the managed Claude home.

### 11.4 Diagnostics Layer

Expose reliability state in:

- `ccb ping <agent>`
- `ccb doctor`
- support bundle artifacts

Useful fields:

- `completion_primary_authority`
- `completion_primary_state`
- `completion_bootstrap_state`
- `completion_last_progress_at`
- `completion_timeout_deadline_at`
- `completion_reliability_reason`
- `completion_fallback_source`

## 12. Testing Strategy

### 12.1 Codex

Add tests for:

- canonical runtime artifact layout is consistent across launcher / bridge / session payload
- declared `completion/` and bridge log path exist after startup
- startup fails clearly if canonical artifact path cannot be provisioned

### 12.2 Gemini

Add tests for:

- hook event with wrong `req_id` does not terminalize another job
- empty reply hook produces degraded / incomplete, not completed
- no hook after API failure converges to timeout terminal decision
- long-thinking without hook converges to timeout terminal decision

### 12.3 Claude

Add tests for:

- empty hook reply does not burn job
- session-boundary and hook evidence merge correctly
- timeout closure works when hook never arrives
- a named top-level session `slug` does not hide the request anchor
- real `isSidechain=true` records remain excluded
- orphaned exact-hook recovery requires complete request, provider, agent,
  workspace, timestamp, session, grace, and idle-pane proof
- Linux and Windows-style session-path separators identify the same tracked
  session
- cancellation preserves a hook-only non-empty reply and labels an
  unsalvageable forced empty artifact as non-evidence
- the recorded process-text/tool-use/tool-result/thinking-only
  `end_turn`/late-final sequence emits exactly one terminal reply
- thinking-only `end_turn` remains pending across daemon restart and completes
  from later visible text with the same API `message.id`
- text without `stop_reason` stays pending until a matching `turn_duration`
- a genuine short-text `end_turn` completes without being swallowed
- tool-only boundaries never reuse accumulated process narration
- stalled-mid-stream API text produces `failed`, not `completed`
- normal Stop-hook polling rejects old or mismatched Claude session artifacts
- the final reply artifact contains only terminal message text and the message
  bureau records exactly one reply

### 12.4 Kimi

Add tests for:

- both native wire layouts and session-id extraction
- exact request headers, prefix collisions, and cross-agent mentions
- successful versus cancelled/error/tool-use `step.end` reasons
- next-turn closure when an explicit boundary is absent
- native in-progress evidence preventing completed pane override
- exact-session persistence, root drift, and symlink rejection for both layouts

### 12.5 Qoder

Add tests for:

- documented print/config/workspace arguments and UUID-only session identity
- result/assistant de-duplication and successful result terminalization
- authentication and `is_error=true` envelopes
- clean exit without a result envelope
- visible/headless config-root consistency and explicit user overrides

### 12.6 Cross-Provider Reliability

Add execution-layer tests for:

- active job cannot remain `running` forever without primary completion evidence
- reliability timeout is provider-manifest-driven
- degraded fallback decision is persisted and restorable

### 12.7 DeepSeek Harness

Add tests for:

- WebSocket-before-prompt ordering and exact RPC id submission
- exact native user anchor, same-turn assistant reply, and completed terminal
- all five non-success native terminal kinds
- reasoning-only, empty, malformed, cross-turn, and missing-anchor evidence
- process exit with reply text but no native terminal
- history reconstruction and observer-only restore without prompt repost
- native approval, question, cancellation, clear, and compact handling
- isolated official-host no-credential failure closure

### 12.8 Cursor

Add tests for:

- visible-pane default selection and explicit headless rollback
- exact top-level request-anchor binding, stale/subagent exclusion, and
  transcript-tail rewrite handling
- busy-pane deferral, new terminal evidence, stable idle confirmation, and
  exactly-once dispatch
- assistant progress without terminalization, successful/error/empty terminal
  outcomes, readiness/run timeout, dead pane, reply delivery, and cancellation
- resubmit-required daemon restore diagnostics

### 12.9 Pi And OMP

Add tests for:

- Pi visible-pane request/response evidence with the official extension loaded
- Pi process text, tool use/result, retry/progress, final text, and
  `agent_settled` producing exactly one terminal final reply
- no Pi `agent_settled`, short replies, empty replies, final `error`,
  `aborted`, and `length`
- Pi old offsets, foreign request/actor/launch/runtime identities, binding
  mismatch, unmanaged-input supersession, malformed complete JSONL, and
  partial trailing JSONL
- Pi busy-pane deferral, extension readiness failure before send, pane death,
  native cancellation without a model cancel-file probe, exact live-instance
  export/restore, and restarted-instance rejection
- persisted 8.5.0 `mode=pi_run` dispatch and
  `CCB_PI_EXECUTION_MODE=headless` rollback
- Pi headless `turn_end` / `agent_end` followed by retry and final
  `agent_settled`
- OMP nonterminal `agent_end`, delayed continuation, and final
  `agent_end.isTerminal=true`
- a headless semantic terminal event while the one-shot process is still alive
- a semantic terminal event whose process never closes converging to run timeout
- clean exit without semantic terminal evidence
- semantic terminal followed by nonzero exit
- final `error`, `aborted`, and `length` outcomes
- OMP terminal structured `yield`
- missing final outcome, malformed JSONL, and an unterminated trailing record

## 13. Rollout Phases

### Phase 1

- introduce document and reliability boundary
- fix Codex runtime artifact layout split
- add bootstrap assertions for declared runtime artifacts

### Phase 2

- add reliability manifest and execution-layer monitor
- add no-terminal timeout closure

### Phase 3

- migrate Gemini / Claude hook artifacts from one-shot terminal file to append-oriented evidence
- strengthen req-id / turn attribution

### Phase 4

- expose diagnostics
- run macOS end-to-end validation

## 14. Immediate Design Conclusions

The correct architectural reading of issues `#180` and `#181` is:

- they are not identical provider bugs
- they are the same reliability-class bug

More precisely:

```text
#180 = runtime artifact contract split
#181 = hook-turn attribution and no-terminal-timeout gap
shared parent = missing managed-provider completion reliability plane
```

Therefore the repair plan must not be:

- "patch Codex log filename here"
- "patch Gemini hook parser there"
- "add one more mac-only timeout in CLI"

The repair plan must be:

- define one reliability contract
- validate runtime artifacts at launch
- preserve provider-specific completion families
- add one control-plane-owned timeout closure path
- make hook evidence turn-scoped instead of blindly terminal

## 15. Callback Ask Continuations

Nested synchronous `ask` is not a supported completion model. When agent A is
running an active mailbox request, a normal child ask to agent B can complete
and queue a `TASK_REPLY` back to A, but that reply cannot be delivered while
A's active request is still the mailbox head. Agents must not wait or poll for
that child reply inside the same turn.

`ccb ask --chain <target>` provides the stable handoff for this case:

- it is valid only from an agent that currently owns an active parent job
- the child request is recorded with a durable callback edge
- the parent job may complete as delegated and suppress normal reply delivery
- the parent message remains open until a continuation attempt produces the
  final reply
- the child result is recorded as a `ReplyRecord` but is not delivered as a
  normal `TASK_REPLY` to the parent agent
- when the child logical message reaches a terminal reply, CCB submits a normal
  `callback_continuation` `TASK_REQUEST` back to the parent agent
- cancelled is a valid terminal child result: CCB submits exactly one parent
  continuation with the child identity, `cancelled` status, and any partial
  output instead of leaving the edge pending or converting cancellation into
  a callback failure
- the continuation uses the original caller as `from_actor`, preserving the
  normal final reply routing path

While an agent owns an active parent job, CCB rejects plain nested `ask`
submissions unless they are explicitly `--chain` or `--silence`. This guard
keeps accidental nested dependencies from completing into an undeliverable
`TASK_REPLY`; `--chain` is for needed child results, and `--silence` is for
independent no-result-needed work.

A control-plane `reply_delivery` job is transport work, not a delegable parent
job. It is excluded from active-parent detection: an overlapping ordinary ask
must not be rejected as nested work, and `--chain` must not bind a child edge
to the reply-delivery acknowledgement.

The first supported callback model is intentionally narrow:

- one outstanding callback child per parent job
- no inline provider-pane injection
- no mailbox FIFO bypass
- no fan-out / fan-in aggregation
- nested callback chains are supported because each level is a normal
  delegated parent plus later continuation
- a `callback_continuation` job must finish in its current turn; it may not
  create a new `--chain` edge back to that continuation's original caller

Durability is owned by callback edge records under the ccbd mailbox state. A
callback edge records the parent job/message, child job/message, original
caller, callback target, child reply id/status, continuation job/message, and
state. Dispatcher maintenance must repair the crash window where the child
reply was recorded and the continuation was not yet submitted. Repair is
idempotent: an edge with an existing continuation job is not submitted again.
Normal completion and cancellation serialize through the same chain transition
lock and callback edge authority. The first persisted terminal job wins a
completion/cancel race; a losing cancellation must not rewrite the completion
snapshot, attempt, reply, or edge. Internal stale-job recovery with
`record_reply=False` does not create a cancelled continuation because its retry
remains responsible for the existing message lineage. Cancelling the parent
itself before delegation completes terminalizes its outgoing edge as
`chain_parent_cancelled`; a later child result cannot reopen that edge or
create a continuation for the cancelled parent.

For a non-chain cancelled job, an empty result with no reply artifact is a
durable `ReplyRecord` plus a consumed-from-birth `completion_notice`. It is
visible in trace but does not increment the registered caller's mailbox depth
or create a provider reply-delivery turn. Partial text and artifact-backed
cancel results retain normal exactly-once `TASK_REPLY` delivery.

Callback edge state is also the backend safety boundary for nested delegation.
Edges must carry a timeout deadline, and dispatcher maintenance must transition
expired pending edges to a terminal timeout state, persist a failed reply on the
parent message, and deliver that failure to the original caller when the caller
owns a mailbox. Callback submission must enforce a bounded chain depth and
reject actor cycles before creating the child job. Callback submission from a
continuation job must resolve `route_options.callback_edge_id` through callback
edge storage and reject attempts to `--chain` the edge's original caller; the
continuation completion itself is the upstream delivery path. If continuation
submission fails after the child has completed, the edge must transition to a
terminal failed state and the parent message must not remain indefinitely
running.
