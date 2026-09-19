# Unified Ask/Back FIFO

Date: 2026-09-18
Role: design and acceptance contract
Status: Working-tree implementation; watchdog removed and dispatcher ask/back gate unified (2026-09-19); focused verification passed; see roadmap for qualification gaps
Related: [roadmap](../roadmap.md), [empty-result notices](empty-result-caller-notice.md)

## Goal And Accepted Decisions

Prevent a second result from entering main while main is still processing the
first. All ordinary requests and results share the target Agent's chronological
queue. Message class and parent/child relationship never grant scheduling
priority. For arrival order result A, request X, result B, execute A → X → B.

Ask remains submit-only: acceptance does not block the sending Agent waiting
for a result. The provider turn must actually end before its execution slot is
released; merely invoking ask is not completion evidence. Logical task lineage
may outlive a turn but must not reserve that Agent's execution slot.

Owner clarification (2026-09-19): reuse every Provider's existing conversation
turn-end judgement. Do not add a reply-delivery elapsed-time fallback or infer
missing turn detection from an absent delivery flag. The dispatcher polling
gate therefore holds one shared rule set for asks and deliveries: delivery
identity comes from the durable job record (`is_reply_delivery_job`), the
legacy `reply_delivery_complete_on_dispatch` runtime flag is a compatibility
fallback only, and the single delivery-specific policy is that an empty
processing turn end completes the delivery (transport plus one turn, no body
owed); attributable failures and cancellations keep their own semantics.

Different Agents consume their queues independently and may run concurrently.
Cancellation and explicit active-turn control retain their existing separate
control paths; this does not authorize ordinary messages to bypass FIFO.

## Source Findings And Affected Surfaces

Paths below are relative to the repository root; inspected 2026-09-18.

- `lib/ccbd/services/dispatcher_runtime/reply_delivery_runtime/start_completion.py`:
  the default start-completion branch calls `dispatcher.complete` immediately
  after successful delivery start. A separate runtime-flag branch returns the
  job without that completion. Audit both consumers before changing semantics.
- `reply_delivery_runtime/preparation_message.py` in the same directory creates
  result delivery jobs and a message with `priority=10`; inspect actual ordering
  consumers rather than assuming this field alone proves priority inversion.
- `reply_delivery_runtime/preparation_head.py` uses delivery job completion to
  resolve result heads. Separate transport delivery from processing completion.
- `lib/ccbd/services/dispatcher_runtime/state_queue.py`, `submission_recording.py`,
  `callbacks.py`, `lib/mailbox_kernel/`, and `lib/message_bureau/`: integration
  inventory for queue admission, active ownership, lineage, and persisted recovery.
- Provider execution adapters and completion detectors supply exact turn evidence.
  Existing session/epoch/anchor fences remain mandatory.

These findings identify plausible mechanisms, not a diagnosis of a particular
reported live job. Runtime records for that incident have not been inspected.

## Queue And Execution Contract

1. Scope each queue by project and target Agent. Persist original target-queue
   acceptance time plus a monotonic tie-breaking sequence. Ordering follows CCB
   durable admission, not sender clocks or later delivery-job creation times.
2. Requests enter on acceptance; results enter when available for the caller.
   Delayed materialization, restarts, and retries must not assign a newer place
   to an already admitted entry. Duplicate admission must preserve identity/order.
3. Reuse existing durable mailbox/job identity and storage if sufficient. Do not
   introduce a second independent queue ledger or event platform by default.
4. Claim only the earliest pending entry and reserve the target slot atomically
   before transport submission. One target has at most one processing turn.
5. Transport accepted/delivered is progress, not permission to release the slot.
   Keep the slot until the exact target session/generation/processing turn ends.
6. Tool completion, quiet output, input availability, child completion, stale
   stop hooks, and send success are never substitutes for that terminal evidence.
7. Result processing may finish without a response to the result sender. This
   still releases the slot when its turn ends; it must not create reply loops.
8. Respect provider activity outside CCB jobs: if the target is already running
   a user-started turn, defer delivery until its end is established. Unknown
   activity requires reconciliation rather than optimistic injection.
9. Terminal failure/cancellation resolves the current entry once, with existing
   attributable status/notification rules. An unavailable provider retains queued
   work and reports blocked runtime state instead of dropping or bypassing it.
10. Restart reconciles any claimed entry with delivery/turn evidence before
    deciding to resume observation or submit. Unknown delivery is not grounds
    for blindly replaying a potentially side-effecting task.

Use the CCB queue to enforce order, not Codex Tab or Claude Ctrl-X Enter.
Provider-native queuing is not the authority for processing-turn exclusivity.

## Implementation Sequence

- Q1: Map ingress, dequeue, completion, chain resumption and restart paths;
  identify current persisted ordering keys and exact completion capabilities.
- Q2: Converge normal request/result arbitration at the existing scheduler;
  preserve one durable acceptance order through result materialization and
  atomically claim the target execution slot. Remove message-class preference.
- Q3: Change result-delivery completion to await its processing turn, retaining
  transport evidence independently. Wire restart and failure release paths.
- V1: Add deterministic regression cases below, then isolated live qualification.

## Acceptance And Verification

- A result is processing; request X and result B arrive: neither is injected
  before A ends, then X runs before B regardless of message class.
- Simultaneous arrivals, equal timestamps, delayed result-job creation, and
  daemon restart retain stable order with no duplicated admission.
- Two scheduler passes cannot claim two messages for the same target.
- Multiple target Agents run concurrently; A→B and B→A submissions return
  promptly and do not reserve slots while awaiting later results.
- Dispatch acceptance, tool completion, unrelated/stale terminal events and
  native subagent completion cannot release the occupied slot.
- A processing result that dispatches another ask releases on its own turn end,
  not on completion of the downstream logical task.
- Result processing with no upstream reply obligation produces no recursive
  empty-result notice. Existing silence and chain lineage remain intact.
- User-started busy turns, model/session changes, cancellation, provider death,
  and crashes before/after delivery are reconciled without cross-turn completion.

Candidate test surfaces: `test/test_v2_ccbd_dispatcher.py`,
`test/test_v2_message_bureau_dispatcher_integration.py`,
`test/test_v2_execution_service.py`, `test/test_claude_queued_prompt_activation.py`,
and callback/reply delivery tests discovered in Q1. Use fake clocks and explicit
events for scheduling tests, not timing sleeps.

## Q1 Audit Answers (2026-09-18, implementation slice)

Recorded from the source audit before implementation; file references are
repository-relative at implementation time.

- Durable order key: the mailbox kernel `InboundEventStore` per-agent append
  order (first-seen JSONL order preserved across revisions;
  `lib/mailbox_kernel/service_runtime/queries.py`). `head_pending_event`
  returns the first nonterminal event; the stored `priority` field
  (requests 100, replies 10) and `created_at` are display metadata only and
  never reorder the head (no scheduling consumer found). Requests are
  admitted by `record_submission`
  (`lib/message_bureau/facade_recording_submission.py`), results by
  `queue_reply_delivery`
  (`lib/message_bureau/facade_recording_terminal_replies.py`). Delivery-job
  creation in `prepare_reply_deliveries` is head-only and never reorders.
  Migration: no persisted-state migration is required; pending entries
  already carry the unified order. The restart gap is reconstruction:
  `DispatcherState.rebuild` (`lib/ccbd/services/dispatcher_runtime/state.py`)
  currently uses JobStore order, which can diverge from mailbox order after a
  stale-head repair recreates a delivery job later than a newer request job.
  Rebuild will derive per-agent pending order from mailbox pending events
  (request payload `job:<id>`; reply payload delivery-job id, or skip when
  not yet materialized), with JobStore order as the fallback for legacy jobs
  without events.
- Callback/chain routes: chain child completion goes through
  `submit_callback_continuation` → `_submit_continuation_job`, which uses the
  ordinary submission path (new message plus TASK_REQUEST event at the tail);
  delegated parents suppress their own reply; callback repair uses the same
  path. Control-queue `ack` operations consume replies caller-side and do not
  schedule target work. No return-result priority lane exists. The claim path
  forks reply-first in `_start_agent_mailbox_job`
  (`lifecycle_start_runtime/queue.py`); both branches gate on the same
  overall mailbox head, so no live inversion today, but arbitration will be
  collapsed into one claim driven by the head event type.
- Processing-turn identification: Codex identifies the turn through the
  wrapped `request_anchor`, `anchor_seen`, and the existing session-rotate
  hard gates; turn end is the anchored detector's terminal decision. The
  current shortcut `_reply_delivery_accepted_result`
  (`lib/provider_backends/codex/execution.py`) completes at `anchor_seen`
  and will be removed for delivery jobs so the anchored detector runs to the
  turn boundary. Claude identifies the turn through the attributed Stop hook
  (`poll_exact_hook`) or event-stream turn boundary after the bounded
  `reply_delivery_require_ready` send wait; the `prompt_sent` shortcut
  `_reply_delivery_terminal_if_dispatched` (`claude/execution_runtime/polling.py`)
  will be removed. Cursor, grok, and pi adapters expose only transport
  acceptance (`_reply_delivery_result` on dispatch) and keep dispatch
  completion; this limitation is reported rather than silently fixed.
- User-started busy turns: the only available evidence is the Claude
  `looks_ready` prompt detector gating the send (bounded by
  `ready_timeout_s` so an unconverged detector cannot deadlock the queue)
  and Codex's anchor-acceptance requirement with no-progress timeout.
  CCB holds one execution slot per target, which prevents CCB-originated
  overlap; indefinite deferral on uncertain user activity is not implemented
  and remains a recorded limitation. The bounded ready-wait send is
  unchanged.

## Readiness Gates

Before formal implementation, record exact answers from the source audit:

- Which durable order key already spans requests and results, and how will
  existing pending entries migrate without changing their order?
- Which callback/chain routes bypass ordinary delivery and must join arbitration
  while retaining the existing chain protocol? No return-result priority allowed.
- How do Codex and Claude identify the processing turn for reply deliveries,
  and which other adapters currently expose only transport acceptance?
- What evidence detects a user-started busy turn, and how is uncertain activity
  reconciled without claiming a false idle state?

These are implementation integration questions, not reopened product decisions.

## Rollout, Risks, And Rollback

FIFO intentionally permits head-of-line delay; it does not promise that a hung
provider turn will finish. Surface runtime failure/unknown state using existing
mechanisms. No semantic task-wait state or result-priority lane is added.

First qualify Linux/macOS/WSL with Codex and Claude in external source-test
projects under the [baseline gates](../../../baseline/test-and-release-gates.md).
No exploratory prompts into active workspaces and no external Provider state
changes. Do not introduce Windows dependencies into shared surfaces.

Before rollout, document persisted-state migration and rollback compatibility.
Drain or reconcile occupied slots before downgrading; never reset queues or
replay all pending entries to recover. Release metadata and publication are
separate work. Report existing adapter limitations before enabling the feature.
