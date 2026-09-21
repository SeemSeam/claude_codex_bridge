# Input Draft Protection Before Queued Delivery

Date: 2026-09-20
Role: topic-capsule and first-version contract
Status: local v8.7.0 source preparation requested; technical readiness review remains open before production promotion
Related: [roadmap](../roadmap.md), [verification](../evidence/input-draft-guard-v1-20260920.md)

## Current scope

The working tree contains a candidate implementation and isolated experiments
covering OMP, Codex and Claude. This is not a release, installed shared-runtime
promotion, or retrospective authorization to enable the feature. Codex includes
a narrow deferred-send split; it is not excluded.
The earlier design and rejected alternatives are preserved verbatim in
[pre-implementation source](../history/input-draft-delivery-guard-preimplementation-20260920.md).
That archive supersedes the former long topic; this capsule owns current state.

## Contract

Existing chronological ask/back FIFO and attributed provider turn completion
remain the prerequisites. Only the eligible queue head inspects the composer.

1. Empty: proceed through the existing claim/start path and final sender check.
2. Nonempty: wait a fixed 180 monotonic seconds; release early if it becomes empty.
3. At the deadline: recheck unchanged binding and nonempty idle composer, clear
   once, and confirm empty before sending. Delayed rendering may require a later
   poll; it does not authorize another clear.

Continued typing does not restart the timer. Busy, unknown, modal, copy mode or
binding change blocks sending/clearing and invalidates elapsed waiting time.
An unconfirmed clear is not retried on the same binding; later manually empty
input releases the job. Daemon restart starts a fresh timer. There is no draft
backup. The owner excludes typing the exact Codex placeholder as a collision case.

## Provider boundaries and candidate audit

| Provider | Empty/draft observation | Clear |
| --- | --- | --- |
| Codex | Candidate parser recognizes default main composer, cursor, continuation rows, footer and fixed placeholder; native busy-row veto outside the draft | **Blocked:** candidate sends `Ctrl-C`; this is not an input-only clear primitive |
| Claude | Candidate parser recognizes bordered default composer and ANSI dim/inverse ghost text; includes native busy and pending native queue vetoes | **Blocked:** candidate sends `Ctrl-C`; full readback does not make this an input-only clear primitive |
| OMP | Candidate uses native getEditorText through a scoped local extension socket, native idle check, default Status Band focus check | Candidate uses native setEditorText with runtime/session identity checks and readback; independent extension/security review remains required |

The candidate treats OMP's default Status Band `╰─` layout as qualified and
other layouts as unknown. That remains an evidence claim, not a rollout
approval. Non-composer bindings proven to be headless/API retain legacy send;
an unknown binding must not be treated as headless. Unenabled provider/path
pairs retain legacy delivery and are explicitly unprotected; enabled paths fail
closed on unavailable inspection. Custom keybindings, Vim modes, arbitrary
attachments and unrecognized layouts are not generally qualified. These parsers
are version-sensitive UI observations.

## Candidate implementation and required corrections

The candidate establishes the desired minimum scheduling shape:

- `lib/ccbd/services/dispatcher_runtime/lifecycle_start_runtime/queue.py`
  calls `ExecutionService.draft_allows_start()` before request/reply claim,
  then rechecks queue/active identity after terminal I/O. It does not hold
  `_chain_transition_lock` across capture, clear, paste, Enter, or provider start.
- `lib/provider_execution/draft_guard.py` uses a per-head monotonic timer and
  one clear attempt. `lib/terminal_runtime/tmux_backend.py:capture_composer()`
  captures ANSI screen state and verifies pane metadata before/after capture.
- Final Codex, Claude and OMP sender paths use the same guard. A final change
  can retain the existing job in `RUNNING`/active with `prompt_sent=False`, so
  there is no new attempt, requeue, or FIFO bypass. Polling suppresses completion
  tracking/timeouts while that unsent state persists; persisted timer objects are
  intentionally discarded and restore polls the composer again.
- Guarded Claude disables `_maybe_resend_activation_enter()`: its
  placeholder/anchor/tail evidence cannot rule out user text appended after the
  first paste. The paste-to-Enter interval is still non-atomic.

These corrections are required before an enabled destructive 180-second path is
implementation-ready:

1. `DraftTarget.clear()` currently maps Codex and Claude clearing to `C-c`.
   The source audit itself records that `C-c` can interrupt a turn or exit an
   empty CLI. A screen observation immediately before/after it cannot prove
   input-only ownership through the intervening race. Do not call those paths
   qualified or enable their deadline clear until a provider-specific safe clear
   primitive is evidenced. Until then their enabled paths must hold on
   `nonempty` at the deadline, or remain explicitly unprotected by a conscious
   rollout choice; `unknown` must never be converted to timed clear.
2. `guarded_send()` sets `prompt_sent=True` before terminal I/O and all three
   adapters convert an exception into `FAILED/draft_guard_send_unknown` through
   `send_unknown_result()`. This is a new cross-provider send-uncertainty
   semantic, contrary to the bounded first-version scope. Keep no automatic
   repaste or requeue after an ambiguous write, but audit and use each provider's
   existing ambiguous-delivery/recovery disposition; do not introduce a generic
   terminal rule without a separately approved contract and tests.
3. OMP's extension socket is a new trusted local control surface. Its owner,
   session/launch/runtime binding, stale-socket handling and no-draft-text
   exposure require a focused security and lifecycle review before it is called
   production-qualified.

There is no remaining product-choice request: fixed 180 seconds, no edit reset,
one FIFO for ask/back, head/turn-end prerequisites, fail-closed enabled paths,
and no draft recovery are already decided. The listed items are source/evidence
gaps, not choices to be silently resolved by rollout.

## Evidence, verification and deployment

See [v1 evidence](../evidence/input-draft-guard-v1-20260920.md) for the
candidate's reported deterministic FIFO/restore/failure tests, captured native
fixtures and actual 180-second experiments on Codex 0.155.1, Claude 2.1.278 and
OMP 18.2.6. It does not resolve the two blockers above.

[Installed real-project qualification](../evidence/installed-draft-guard-20260920.md)
uses a separate installation and real Codex/OMP/Claude models. It reports fixes
for the historical interrupt-phrase veto, draft keyword false detection and Claude
busy animation without an interrupt hint. The shared working installation is
unchanged. Deployment requires an updated daemon and newly launched OMP bridge.
Existing OMP processes cannot acquire an extension by changing daemon code alone.

The reported injected daemon termination preserved OMP/Claude drafts and queued work, but
Codex's remote app-server session failed to reconnect. Its blocked queue required
explicit cancellation, pane restart and resubmission; automatic recovery across
this provider failure is not qualified. Unknown is still never permission to
erase a draft or send into a disconnected provider.

Capture, clear, paste and Enter are not atomic against human input. In particular,
the existing paste-to-Enter delay remains a race window. The candidate experiments
establish only tested-editor observations, not full daemon/model/API qualification
or safe `Ctrl-C` ownership. Before enablement, run fake-clock tests for all timer
and cancellation transitions, then an independent disposable-pane qualification
of the approved clear primitive and each provider's ambiguous-send disposition.
Future CLI UI changes need fixture and disposable-pane requalification.
