# Archived Input Draft Guard Design

Role: archive-only source
Status: superseded by [current contract](../topics/input-draft-delivery-guard.md)
Preserved: pre-implementation topic verbatim, 2026-09-20

---

# Input Draft Protection Before Queued Delivery

Date: 2026-09-20
Role: design and acceptance contract
Status: shape-plan; implementation is capability-gated
Related: [roadmap](../roadmap.md), [plan README](../README.md), [FIFO contract](../topics/unified-message-fifo.md)

## Decision, Scope, And Readiness

### New real-probe evidence (2026-09-20)

[Deep Codex/Claude probes](../evidence/codex-claude-composer-deep-probe-20260920.md)
now supersede the initial Codex collision concern: the owner explicitly excludes
typing the literal placeholder. The normal Codex placeholder is usable within a
recognized main composer, subject to busy/overlay/attachment exclusions. Claude
native dynamic suggestions were reproduced with a local mock API: unaccepted
ghosts disappear on paste; accepted suggestions become real drafts; clearing
can restore the ghost. ANSI attributes distinguish those states in three tested
themes. Single Ctrl-C passed 24 idle default-editor clear/readback cases across
the two providers, but is not safe in arbitrary busy/empty/modal states. These
are editor-capability findings, not CCB guard integration or release acceptance.

[Isolated composer tests](../evidence/composer-native-probe-20260920.md) now
verify OMP's live `getEditorText`/`setEditorText` API for multiline read and clear
with readback. Earlier matrix entries below describe the pre-probe audit;
OMP's editor capability is now evidenced, while CCB integration is still pending.
The initial Codex collision probe remains recorded as historical evidence but
is outside the owner-selected first-version scope. Prefer OMP's native editor
API, normal-composer placeholder recognition for Codex, and attribute-aware
full-composer observation for Claude. Earlier matrix entries below are the
pre-probe source audit; the linked real evidence supersedes their capability
uncertainty only for the explicitly tested configurations.

### Agent1 review disposition (2026-09-20)

The minimal architecture is accepted as a design direction, not authorization
to implement or a declaration of provider qualification. Reusing deferred
submissions is preferable to adding a send transaction; terminal I/O must not
expand the global transition-lock scope.

Codex's lack of an existing deferred sender is a verified implementation gap,
not an owner-approved exclusion from the requested feature. The Claude/OMP-only
slice proposed below is a staging option, not full acceptance. Before selecting
the implementation scope, assess a narrow Codex readiness/send split and its
poll/restore impact. If Codex is deferred, explicitly disclose the coverage gap
instead of presenting the feature as complete for the current project.

For post-claim guard checks, distinguish this job's own active-slot ownership
from an actually executing provider turn: rejecting any active job would
deadlock `draft_guard_pending` against itself. Readiness timeouts must not send
or silently retry past a draft guard; audit existing timeout consumers while
retaining the established provider completion/no-progress policies. Acceptance
tests must cover these two interactions as well as the 180-second boundary.

### User-confirmed contract

The existing unified chronological FIFO remains the only ordering authority for
ordinary `ask` requests and `back` result deliveries. A queued item may inspect
the interactive composer only after the target's current processing turn has
ended and that item is the mailbox head. It then follows this exact policy:

1. `empty`: send.
2. `nonempty`: wait a fixed 180 seconds; send early if it becomes empty.
3. At 180 seconds, if still `nonempty`: clear the whole composer, confirm it is
   empty, then send.

The timer does not observe continued editing or reset on edits. It is neither
an admission nor execution timeout, cannot interrupt a live turn, and cannot
allow a later item to pass the waiting head. The first version has no draft
backup or recovery feature. `unknown` is never `empty` for sending and never
`nonempty` for timed clearing.

### Design recommendation and scope boundary

Apply one guard to normal interactive-pane `ask` and `back` delivery: both paste
text plus Enter into the same human composer, and split policies retain an
equivalent data-loss path. Put it before the request/reply claim branch, after
the shared mailbox-head check. A binding positively verified as headless/API or
another non-composer transport continues through its current send path without
this guard. A missing, ambiguous, or changed channel binding is not headless:
it is `unknown` for an *enabled* guard path. Cancellation, native clear, and
explicitly supported active-turn followups keep their existing control paths.

This is a plan-only result. No provider has source-proven general multiline
tri-state inspection *and* input-only clear-confirm. This does **not** authorize
a global FIFO stop: a provider/path for which the feature is not enabled keeps
its current delivery behavior and is explicitly reported as unprotected. Once a
provider/path is enabled, `unknown` is fail-closed and holds that head; inability
to inspect must not be treated as nonempty and erased later. Destructive 180 s
clear is enabled only for a separately qualified provider/path combination.

## Source Audit

All paths are repository-relative and inspected on 2026-09-20.

| Concern | Source evidence | Consequence |
| --- | --- | --- |
| FIFO head and claims | `lib/ccbd/services/dispatcher_runtime/lifecycle_start_runtime/queue.py`: `_start_agent_mailbox_job()` reads `head_pending_event()` and branches by event type; `_claim_request_job()`/`_claim_reply_delivery()` remove the queued entry. | Put the common guard before either claim; do not add a queue or reorder request/reply. |
| Current turn gate | `lib/ccbd/services/dispatcher_runtime/lifecycle_start_runtime/recovery_runtime/slots.py:_iter_queued_runtimes()` skips a target with `DispatcherState.active_job()`; existing provider completion remains the authority in [FIFO](../topics/unified-message-fifo.md). | Reuse that gate and terminal evidence. Do not build another completion detector. |
| Claim means execution | `lib/ccbd/services/dispatcher_runtime/lifecycle_start_runtime/start.py:start_running_job()` writes `RUNNING`, starts the Message Bureau attempt, marks active, syncs `BUSY`, then calls execution start. | Keep the fixed 180-second head gate pre-claim where possible. A provider's existing same-job `RUNNING`/active deferred-send state may legitimately retain that slot without creating another attempt or queue item. |
| Terminal transport | `lib/provider_execution/common_runtime/terminal.py:send_prompt_to_runtime_target()` delegates to terminal send; `lib/terminal_runtime/tmux_send.py:TmuxTextSender._paste_via_buffer()` pastes, waits the configured 0.5 s default, then sends Enter. | The sender is too low-level for queue, timer, or draft policy; a final recheck is required immediately before it. |
| Generic clear | `interrupt_and_clear_runtime_target()` sends `C-c`, `Escape`, `C-u` (or raw equivalents), with no confirmation. | It can interrupt active work and cannot meet clear-confirm. |

### Provider capability matrix

`Supported` is source evidence for a narrow capability, not feature
qualification. `Unproven` is a release blocker, not a fallback.

| Capability | Codex | OMP | Claude |
| --- | --- | --- | --- |
| Existing CCB turn end | Supported: `lib/provider_backends/codex/execution_runtime/polling_runtime.py:poll_submission()` feeds the anchored state machine; `state_machine_runtime/terminal_events_runtime.py:append_task_complete_item()` emits `TURN_BOUNDARY`. | Supported: `lib/provider_backends/omp/pane_execution.py:OmpPaneExecutionAdapter` inherits Pi event handling with `agent_settled` final authority. | Supported: existing attributed hook/event-stream completion; retain it. |
| User-started busy evidence | Partial only: `lib/provider_backends/codex/execution_runtime/readiness.py:looks_ready()` rejects visible `Working`/`Thinking`/`Running`, but is UI heuristic rather than qualified generic-turn evidence. | Plausible, unqualified: `lib/provider_backends/omp/launcher.py:_omp_completion_extension_source()` records `input`, `agent_start`, and `agent_settled`; `lib/provider_backends/pi/pane_events.py:inspect_pi_runtime()` derives busy by actor/session/runtime instance. Real user-turn coverage is not demonstrated. | Not qualified: `lib/provider_backends/claude/execution_runtime/start.py:looks_ready()` returns true for a prompt line and even `Esc to interrupt`; it cannot be a busy-safe gate. |
| Full composer `empty/nonempty/unknown` | Unproven. `lib/provider_backends/codex/execution_runtime/readiness.py:wait_for_runtime_ready()` is an 8 s UI-readiness helper, not a composer parser. | Unproven. Extension events do not expose an unsent draft. | Unproven. `lib/provider_backends/claude/execution_runtime/start.py:_current_prompt_tail()` finds only the same `❯` line; it is not a general multiline parser. |
| Multiline user draft | Unproven. | Unproven. | Unproven; polling helpers recognize CCB's own pasted placeholder, not arbitrary human drafts. |
| Input-only clear and confirmation | Unproven; no safe adapter found. | Unproven; no safe adapter found. | Unproven. `lib/provider_backends/claude/execution_runtime/start.py:clear_stale_prompt_input()` calls generic interrupt/clear and never confirms success. |
| Current send boundary | Direct after `wait_for_runtime_ready()` in `codex/execution_runtime/start.py:start_active_submission()`; still needs final re-observation. | `lib/provider_backends/pi/pane_execution.py:_dispatch_if_ready()` reads `inspect_pi_runtime()`, then calls `_append_dispatch()` immediately before terminal send; OMP uses it. | Delayed: `claude/execution_runtime/polling.py:_dispatch_deferred_prompt()` calls `_prompt_delivery_due()` then `send_prompt()`. |

For Claude, `execution_runtime/start.py:send_prompt()` currently calls
`clear_stale_prompt_input()` before every send. Guarded `ask`/`back` must remove
or bypass it and use only qualified clear-confirm after 180 seconds.
`_prompt_delivery_due()` permits send after its independent 8-second ready
timeout; it must never bypass the draft guard or turn/busy condition. Codex and
OMP also need a final guard at their actual sender, not just dispatcher entry.

### Claude activation retry after first paste

`lib/provider_backends/claude/execution_runtime/polling.py:poll_submission()`
calls `_maybe_resend_activation_enter()` after the first dispatch. The helper
currently permits one later Enter when the current composer has this job's
marker, `[Pasted text ...]` placeholder, or prompt-tail fingerprint, and is not
visibly busy. It exists because tmux paste is asynchronous and
`TmuxTextSender._paste_via_buffer()` sends its first Enter after the default
roughly 0.5-second `CCB_TMUX_ENTER_DELAY`.

That evidence proves some CCB prompt remains, but does not presently prove that
the user did not append text after the first paste. For a **guard-enabled**
Claude delivery, retain the first send only after sealed final authorization,
then permit the retry only with a qualified full-composer equality/provenance
proof that excludes appended user text. Placeholder-only and tail-only evidence
are insufficient for this guarded retry. Without that proof, record no second
Enter and let existing activation/terminal recovery report the unresolved
delivery; do not reuse the retry to submit a mixed draft. Existing unprotected
Claude paths retain their current bounded retry behavior. Neither the first
0.5-second paste-to-Enter interval nor the later retry can be atomic against
human input; this is a declared first-version residual risk, not a reason to
weaken FIFO or synthesize a draft restore feature.

## Minimal Architecture

### State and ownership

| State | Meaning under this plan |
| --- | --- |
| `queued` | Durable mailbox event and queued job; the only state while inspection or 180-second wait is pending. |
| `head_guard` (ephemeral) | Dispatcher-owned record for the observed mailbox head; not a JobRecord status, claim, Mailbox attempt, or active slot. |
| `running` / `active` | Existing ownership for this one claimed task, from provider start through attributed turn end. It may include that provider's already-supported `prompt_sent=False` preparation/deferred-send phase; it blocks later FIFO entries but never creates a second attempt. |

Add one small dispatcher-local `HeadDraftGuardState`, keyed by target and bound
to project, agent, inbound-event identity, job identity, pane id, and provider
session/runtime generation. It holds only monotonic `nonempty_since`, one
destructive-clear-attempt marker, and a reason code; it never holds draft text.
Keep it beside lifecycle-start state, in memory only, and out of durable FIFO.

The scheduling order is:

1. Existing slot recovery proves no CCB active job. Existing provider
   turn/reconciliation evidence owns any user-started busy decision.
2. Read the existing mailbox head and resolve its queued job without removing it
   or marking any attempt started.
3. A positively verified non-composer binding bypasses this feature and uses its
   existing send path. For a configured interactive guarded path, resolve a
   stable binding. Missing, changed, or ambiguous binding is `unknown` and
   preserves the head; it cannot start the timer.
4. Ask the adapter for `empty`, `nonempty`, or `unknown`. First qualified
   `nonempty` starts the monotonic timer. `empty` permits the immediate final
   check; changed observation, busy transition, or identity mismatch invalidates
   it.
5. At deadline, only fresh `nonempty` with unchanged binding may invoke
   input-only clear. Re-observe `empty`, then do the final head/binding/busy/
   composer check immediately before normal claim/start. This final check and
   terminal I/O are not made atomic.

### Recommended minimum integration

The fixed 180-second guard is a pre-claim nonblocking check in
`lifecycle_start_runtime/queue.py`; it leaves the mailbox head and job pending.
No dispatcher `_chain_transition_lock` scope may include pane capture,
clear-confirm, paste, Enter, or provider start: that lock is also used by
cancellation, finalization, and callback operations. Existing mailbox claim and
execution-service locks retain their current short state-transition purpose.

After the final pre-claim observation is empty, call the normal
`start_running_job()` path. A direct-send provider is guard-eligible only when
it can make that final check at its actual sender without a later readiness wait
that can reopen the composer race. The capture-to-paste/Enter window remains
non-atomic.

When a provider already has a `ProviderSubmission` with `prompt_sent=False`, it
is acceptable for the *same claimed job* to remain `RUNNING`/active while a
small guarded pending phase rechecks the composer. This is not a new attempt,
requeue, or FIFO bypass: the active slot intentionally keeps all later messages
behind this task. A post-claim final change to `nonempty`, `busy`, or `unknown`
updates that submission to `draft_guard_pending`, performs no send, and lets the
ordinary polling cadence re-evaluate it. The 180-second rule applies again to
that same pending task; it is nonblocking for other targets. A clear failure
stops automatic re-clear but a later observed `empty` continues this task.

This reuse is concrete for Claude and OMP pane mode:

- Claude already persists `prompt_sent=False` and polls
  `_dispatch_deferred_prompt()`. Guarded submissions must mark
  `draft_guard_pending`; polling runs the shared guard before that function, and
  `_prompt_delivery_due()` cannot send while it remains pending.
- OMP inherits Pi pane mode, where `prompt_sent=False` and `pending_prompt` are
  held by `_dispatch_if_ready()`. The guarded branch must run the shared guard
  before `_append_dispatch()`/terminal send. `inspect_pi_runtime()` remains the
  existing read-only readiness/busy input and no dispatch record is written
  until guard release.
- Codex currently runs `wait_for_runtime_ready()` and sends synchronously from
  `start_active_submission()`, with no equivalent persisted unsent/deferred
  prompt state. It cannot yet put the final guard at the actual sender without
  new factoring. Keep Codex interactive delivery explicitly `unprotected` in
  this slice; a future narrow readiness/send split is separate work, not a
  reason to add a transaction.

This is the recommended small implementation. A durable `commit_intent`,
two-stage start/adopt lifecycle, or a transaction spanning terminal I/O is not
required for the first slice and is deferred as an optional future response only
if real crash evidence shows the existing lifecycle cannot meet the safety goal.

### Timer, invalidation, and final-send rule

- Use injected monotonic, fake-clock-testable dispatcher time; never
  `JobRecord.updated_at`, provider time, wall time, or `sleep(180)`.
- Start only on first qualified `nonempty` after no active turn and head
  selection. Do not reset for edits or repeated `nonempty` reads.
- Drop pre-claim state when the head is cancelled/replaced, pane/session/
  generation changes, a user turn becomes busy, daemon restarts, or observation
  is `unknown`. A restored `draft_guard_pending` active submission also starts a
  fresh 180 seconds; no persisted deadline is copied. Busy/unknown time never
  counts.
- Allow one clear attempt for unchanged guard generation. Failure, unable to
  confirm, or immediate nonempty/unknown readback stops automatic re-clear, but
  does not terminalize the head. A later qualified `empty` observation (for
  example, after the user clears it) proceeds to send; it does not need a second
  clear. A later empty-to-nonempty transition is a fresh draft generation and
  starts a new fixed timer without tracking edits or resetting a running timer.
- Recheck active/busy, head identity, binding identity, and composer directly
  before the terminal sender. Tmux has no atomic capture/clear/paste operation;
  human input in that final window is residual risk. A pre-claim detected change
  keeps the job queued; a provider deferred-send detected change keeps the same
  running job in `draft_guard_pending`.

## Failure, Recovery, And Competition Rules

| Event | Required result |
| --- | --- |
| Head cancellation/abandonment | Delete matching guard state; normal FIFO cancellation chooses the next head. |
| User turn begins while waiting or at deadline | Do not clear/send; invalidate timer and wait for existing reconciliation to establish idle. |
| Daemon restart | A queued head discards deadline and re-observes. A restored active `draft_guard_pending` submission also rechecks with a fresh timer; neither copies a prior deadline. |
| Pane/session/runtime switch | Discard state and rebind; never clear/send to old pane. |
| `unknown`, capture error, menu/modal, copy mode, missing pane | Block head with reason; no timer, clear, or Enter. Operator visibility omits composer text. |
| Clear failure or nonempty readback | No paste/Enter and no repeated automatic clear. Retain head; a later qualified empty observation unblocks send. |
| Final pre-claim check changes | Keep the queued head and apply normal empty/nonempty/unknown rule. No attempt, active slot, dispatch log, or retry is created. |
| Deferred-send check changes after claim | Retain the same `RUNNING`/active submission as `draft_guard_pending`; no new attempt or requeue. Its later guarded check may send only after normal release conditions. |
| Paste/Enter outcome uncertain | Preserve the provider's existing delivery/anchor recovery semantics; this feature must not add a universal send-unknown journal, automatic re-paste, re-Enter, or requeue. |
| Two ticks / final race | Check head identity and guard generation inside existing single-target claim path. Second tick sees unchanged head or active claim, never later item. |
| Later `ask` or `back` | Remains behind mailbox head. No class-specific bypass or timer lane. |

### Restore audit and rule

`lib/provider_execution/service_runtime/restore.py:restore_submission()` calls
`restore_helpers.resume_or_result()`, then persists the returned submission.
It does not classify a general terminal-send transaction. Preserve that boundary:
this feature changes only its own draft-pending submissions.

| Provider/state on restore | Audited behavior | Guard rule |
| --- | --- | --- |
| Codex active submission | `codex/execution_runtime/start.py:resume_submission()` rebinds reader/backend/pane and does not call terminal send. Codex has no existing persisted `prompt_sent=False` deferred sender. | Do not create a new Codex send transaction. Existing anchor/delivery-state polling resumes; Codex remains unprotected until a separate readiness/send split can be qualified. |
| Claude recorded `prompt_sent=False` | `claude/execution_runtime/start.py:resume_submission()` returns an active submission; its normal poll can reach `_dispatch_deferred_prompt()`. | Persist `draft_guard_pending`; after resume, route through the common guard with a fresh timer before that dispatcher. It must not send or reuse an old deadline. |
| OMP pane recorded `prompt_sent=False` | `pi/pane_execution.py:resume()` rebinds event runtime and rebuilds `pending_prompt`; normal poll re-enters `_dispatch_if_ready()`. | Persist `draft_guard_pending`; after resume, run the common guard before `_append_dispatch()`/send, with fresh timer and no copied prompt dispatch. |
| Claude/OMP recorded `prompt_sent=True` | Existing polls observe their already-started turn; Claude may run its bounded activation-Enter recovery. | Do not treat as new unsent work. Guarded Claude retains the stricter no-second-Enter-without-full-ownership rule. |
| Codex not yet anchor-accepted or any low-level paste/Enter interruption | Current persisted fields do not prove a universal physical send outcome; a crash in `ExecutionService.start()` before it persists a submission is outside restore. | Retain existing provider-specific recovery/terminal semantics. Do not introduce `commit_intent`, resend, or generic `send_unknown` policy in this feature. |

The only added durable marker proposed for first version is
`draft_guard_pending` on an already active, recorded-unsent Claude/OMP pane
submission. It has no draft text and no persisted deadline. If resume cannot
re-establish the same trusted binding, it remains provider recovery failure under
existing rules; it must not bypass the guard by replaying the prompt.

## Implementation Plan

1. **Qualification contract and seam.** Add a narrow provider result for full
   composer observation, clear-confirm capability, and binding identity. It
   returns `unknown` plus reason codes, not boolean readiness. Keep it local to
   interactive adapters; do not generalize headless paths or add a framework.
2. **Pre-claim dispatcher gate.** In
   `lib/ccbd/services/dispatcher_runtime/lifecycle_start_runtime/queue.py`, add
   one common head guard between `_mailbox_head_event()` and request/reply claims.
   Add injected-clock ephemeral state in lifecycle-start runtime. Explicitly
   bypass only trusted non-composer bindings; unprotected providers retain their
   current path. Keep mailbox ordering and completion rules unchanged.
3. **Reuse active deferred submissions.** Add only the guarded pending marker
   and polling gate needed for existing `prompt_sent=False` Claude/OMP pane
   submissions. It retains the current job's `RUNNING`/active ownership and
   attempt; it does not create an attempt, journal, queue entry, or lock-spanning
   I/O transaction. Restore routes this marker through a fresh shared guard.
4. **Provider final boundaries.** For OMP, suppress start-time dispatch for a
   guarded job and gate `pi/pane_execution.py:_dispatch_if_ready()` before
   `_append_dispatch()`. Refactor Claude
   `execution_runtime/start.py:send_prompt()` and
   `execution_runtime/polling.py:_dispatch_deferred_prompt()` so stale-clear
   cannot bypass the guard and ready timeout cannot authorize injection. For
   guarded Claude, constrain `_maybe_resend_activation_enter()` to exact
   whole-composer provenance or suppress its second Enter. Keep Codex
   unprotected pending its separately justified readiness/send split. Enable
   each guarded provider only after adapter tests and a disposable-project run.
5. **Safety integration.** Route cancellation, restore/restart, binding loss,
   and draft-pending state through existing paths while clearing only matching
   guard state. Do not alter general send-uncertain semantics. Add concise
   English reason codes without draft bodies.
6. **Gate release.** Keep all unqualified provider/path pairs unprotected and
   operational. Enable non-destructive guard behavior and then destructive
   deadline clear only for qualified provider/path pairs. No queue migration:
   durable queue identity/order remains unchanged.

Candidate tests: `test/test_unified_message_fifo.py`,
`test/test_v2_ccbd_dispatcher.py`,
`test/test_v2_message_bureau_dispatcher_integration.py`,
`test/test_codex_readiness.py`, `test/test_claude_execution_runtime_start.py`,
`test/test_claude_queued_prompt_activation.py`, and
`test/test_omp_pane_execution.py`. New test placement follows the small adapter
seam; add Claude/OMP draft-pending restore and guarded-Claude activation cases.
This is deliberately not a broad provider framework.

## Verification And Rollout

### Deterministic tests

Inject a fake monotonic clock and capture/send double. Cover empty immediate
send; 179/180-second boundary; early empty; edits that do not reset; unknown
before/during/after deadline; multiline nonempty; one failed clear; later manual
clear; cancellation; restart; pane/session change; user busy transition; two
ticks; a final pre-claim change with no claim; and A -> X -> B where A's head
wait blocks X/B but another target progresses. Assert no `RUNNING`, active slot,
Mailbox attempt, or OMP dispatch record before pre-claim authorization. Cover
Claude/OMP same-job `draft_guard_pending`, slot retention, later manual clear,
restart timer reset, and no automatic requeue/new attempt. Also pin that CCB
does not alter existing low-level send-uncertain recovery behavior or log draft
text.

For Claude, test the ordinary initial 0.5-second paste-to-Enter timing as a
non-atomic boundary, then test guarded activation retry with exact whole-composer
ownership, placeholder-only, tail-only, busy, and user-appended-text cases. The
last three must not send a second Enter for a guarded message; unprotected legacy
behavior remains separately pinned.

### Disposable real-provider qualification

Use an independent throwaway project and disposable panes, never the current
work pane. Use only already-authorized managed state: do not read or alter
provider credentials. For each provider, show correct empty, one-line
nonempty, multiline nonempty, modal/unknown, and post-clear empty results; clear
does not interrupt idle; a manually submitted turn produces busy evidence; and
final send obeys the guard. Test Codex, OMP, and Claude independently. OMP
extension user-turn evidence and Claude multiline readback remain unqualified
until this run exists.

### Release, rollback, and remaining risk

Release notes disclose fixed 180 seconds and possible permanent draft deletion.
The provider/path enablement table must separately state `unprotected` (legacy
delivery remains available), `guarded-no-clear` (if later introduced), and
`guarded-clear-qualified`; no global default may turn unqualified FIFO heads
into blocked work. Telemetry is limited to identities, elapsed time, capability,
and reason codes. Rollback disables guard without FIFO migration or bulk replay;
already-cleared drafts cannot be recovered. Residual risk is human input between
final capture and paste/Enter plus provider UI/version drift; failed reads stay
blocked only for enabled guarded paths, never become timed clear.

## Readiness And Choices

No new user product choice is required: FIFO, 180 seconds, no edit-reset, and
no draft recovery are settled. The recommended minimum is ready to implement
only after provider qualification proves full composer observation and
input-only clear-confirm. Claude/OMP additionally need the narrow
`draft_guard_pending` poll/restore gate described above. Codex has no deferred
unsent submission to reuse and keeps legacy unprotected delivery until a
separate readiness/send split is justified and qualified. A durable send
transaction remains deferred architecture, not a first-slice prerequisite.
