# Inter-Agent Communication Reliability Roadmap

Date: 2026-06-14

Last updated: 2026-09-19

## Current Slice: Unified FIFO And Empty-Result Notices

Status: Implemented in the working tree; the watchdog overlap found in
[independent review](evidence/agent1-independent-review-20260919.md) is fixed by
[reusing existing provider turn completion](evidence/provider-turn-end-only-20260919.md),
and the dispatcher polling gate now shares one provider turn-end rule set for
asks and deliveries ([shared gate round](evidence/ask-back-shared-turn-end-gate-20260919.md)):
job-record delivery identity, no per-provider delivery fork, no legacy-flag
capability semantics. 406 focused tests pass, including elapsed-time hold and
turn-end advance. Real installed Codex/OMP FIFO and Codex empty-notice tests
passed; OMP empty-terminal and Claude qualification remain open. Earlier transport
history is retained below.

### Done

- Read-only `ccb screen <agent> [--lines 0..1000] [--json]` and abnormal ask
  reply guidance: inspect pane, correlate original job trace, then let caller
  choose wait/retrieve/continue/resend/pause. Original statuses and errors
  remain; pure empty-provider outcomes cannot bypass the no-retry rule through
  generic retryable diagnostics. [Verification](evidence/screen-caller-inspection-20260919.md).
- Recorded owner decisions: chronological ask/back FIFO without class priority;
  processing-turn completion before the next delivery; caller-owned recovery
  after an empty-result notice, without automatic empty-result resend.
- Inspected reply dispatch completion, reply message construction, empty-result
  detectors, formatting, and retry classification in current source.
- Q1 audit answers recorded in the FIFO and empty-result topic files
  (durable order key, chain routes, per-provider turn evidence, busy-turn
  reconciliation, Claude final-text grace retention).
- Q2: unified mailbox-head claim in `lifecycle_start_runtime/queue.py` (single
  arbitration fork from the head event type; no reply-first pass);
  `DispatcherState.rebuild` now derives per-agent pending order from mailbox
  inbound admission order with JobStore order as legacy fallback
  (`fifo_order.py`, `records.py`).
- Q3: Codex `_reply_delivery_accepted_result` and Claude
  `_reply_delivery_terminal_if_dispatched` send/anchor shortcuts removed;
  deliveries complete on anchored turn-end evidence. The dispatcher polling
  gate normalizes proven empty turn ends to
  `COMPLETED/reply_delivery_turn_complete`; cursor/grok/pi dispatch shortcuts
  were also removed in the review-fix round below; the fake
  provider now marks deliveries provider-tracked so source-run campaigns
  exercise the held path. Mailbox lease safety re-verified (1800 s TTL sweep
  skips agents with running jobs).
- E1: empty results removed from automatic retry eligibility
  (`finalization_retry_runtime/policy.py`); one-time caller inspection notice
  constructed at finalization before retry planning
  (`finalization_runtime/empty_result_notice.py`), stored on the ReplyRecord
  (reply-level `notice`/`notice_kind` markers lifted in `record_reply`);
  dead empty-exhaustion warning branches removed; cancellation, explicit
  errors, silence, chain lineage, and the Claude 180 s final-text grace
  unchanged.
- V1 deterministic: new `test/test_unified_message_fifo.py` (request-first
  ordering, strict held-delivery A->X->B, single claim per target,
  same-timestamp order, restart rebuild from mailbox order, empty turn end
  consumption, polling-gate unit cases); rewritten pins for codex/claude
  delivery completion and empty-result notice/retry behavior.
- V1 isolated runtime: real daemon + pane layout + fake providers driven
  through `ccb_test` from an external allowed project with isolated HOME
  (evidence under `/var/tmp/ccb-fifo-qual-20260918/.ccb/agents/*/jobs.jsonl`):
  mid-turn snapshot shows request queued while the head turn runs; timestamps
  show strict serial order; reply delivery consumed after a full fake turn.
- Review-fix round (agent1 acceptance findings, 2026-09-19):
  1. Provider coverage: dispatch-completion shortcuts removed in cursor,
     grok, and pi (OMP inherits pi), all of which complete held deliveries on
     their anchored event-stream evidence; `start_completion.py` no longer
     completes any delivery on send. The initially added 1800 s watchdog was
     rejected in independent review and removed after owner clarification:
     all deliveries reuse existing provider turn-end decisions; an absent
     delivery flag does not imply missing completion capabilities. The
     polling gate now normalizes the `<provider>_empty_reply` family and
     requires prompt+anchor proof for the pi/omp/cursor/grok families.
     (Superseded 2026-09-19 by the shared-gate round below: the dispatcher
     no longer maintains that per-provider delivery proof fork.)
  2. Terminal disposition: `resolve_reply_delivery_terminal` distinguishes
     pre-acceptance transport failures (requeue, bounded to 3 by
     `reply_delivery_requeued` history before `reply_delivery_abandoned`)
     from post-acceptance terminals (consume, event
     `reply_delivery_terminal_after_delivery`); cancelled deliveries are
     never resurrected. Regressions: cancel-not-resurrected + queue advance
     + restart idempotence; live cancel probe consumed the head.
  3. Notice breadth: `is_empty_result_outcome` now covers every terminal
     INCOMPLETE/COMPLETED outcome owing the caller a result with no body and
     no artifact (e.g. `incomplete/omp_request_superseded` with
     `superseded_by=unmanaged_input`); CANCELLED/FAILED keep their own
     semantics. Chain regressions prove the original caller receives the
     notice when the final continuation is empty.
  4. Retry boundary: `is_pure_empty_provider_outcome` outranks
     `delivery_retryable` in `is_retryable_failure`, so pure empty provider
     results never auto-retry/reactivate (probe test proves one attempt, no
     activation, one notice); independent transport/runtime/API policies
     unchanged.
- Fix-round verification: 402 passed across the 19 dispatcher/delivery/
  adapter suites (including rewritten cursor/grok/pi held-delivery tests and
  the pi/omp gate unit cases); live external source-run re-verified strict
  serial order, a delivery completed through a full fake turn, and a
  mid-turn delivery cancel that consumed the head without resurrection
  (evidence: `/var/tmp/ccb-fifo-qual-20260918/.ccb/agents/*/jobs.jsonl`).
- Shared-gate round (owner-confirmed minimal reuse, 2026-09-19):
  `_reply_delivery_turn_end_decision`/`_reply_delivery_turn_end_proven`
  removed from `polling_service.py`; asks and deliveries now pass through
  one gate (authority annotation + unchanged codex acceptance isolation),
  delivery identity comes from the durable job record via
  `is_reply_delivery_job` with the legacy
  `reply_delivery_complete_on_dispatch` flag kept only as a no-job compat
  fallback, and the only delivery-specific policy is
  `_reply_delivery_turn_end_outcome` (empty INCOMPLETE turn end completes
  as `reply_delivery_turn_complete`; FAILED/CANCELLED/attributable
  incompletes unchanged; asks keep the `non_empty_reply` gate and notice
  policy). Codex deliveries without proven acceptance now finalize as
  `terminal_before_provider_acceptance` instead of the ask-oriented
  `task_complete_empty_reply` (fail-closed in both). Verified: 406 passed
  across the same 19 suites plus 113 in adjacent gate-consumer suites; see
  [evidence](evidence/ask-back-shared-turn-end-gate-20260919.md).

- Real installed Codex/OMP qualification (2026-09-19): both targets passed
  A→X→B FIFO with 25 s tool work in A; Codex real empty return produced one
  caller notice and no CCB retry. See [evidence](evidence/installed-codex-omp-qualification-20260919.md).

### Next

1. Real Claude held-delivery qualification remains open. Earlier isolated
   authentication failures do not apply to the successful Codex/OMP installed
   campaign. Use authorized managed state without changing external auth.
2. Investigate OMP native empty continuation: agent_end reported
   will_continue=true without agent_settled, so the real empty-result notice
   path was not reached. The probe was explicitly cancelled after observation;
   do not label this a successful empty-terminal qualification.
3. Update protocol/operator docs for the removed automatic empty-result retry
   in the documentation pass before release promotion.

### Deferred

- Priority scheduling, return-result preference, native queue-key substitution,
  automatic semantic recovery, and native Windows transport changes.

Next target: real-provider qualification and doc updates. No
commits, pushes, or releases are authorized by this planning update.

## Status Summary

- Current status: unified FIFO + empty-result notice slice implemented in
  the working tree, including the 2026-09-19 agent1 review-fix round
  (provider coverage, terminal disposition, notice breadth, retry boundary);
  real Codex/Claude qualification open in the checked environments; release
  promotion still requires normal review/commit/release gates.
- Last analysis: PR226 improves low-probability Linux/macOS/WSL transport
  races, but it should not be treated as a completed stability boundary without
  follow-up guards.
- Last verified: targeted PR226-adjacent tests passed in a clean
  `origin/main` worktree:
  `python -m pytest -q test/test_bridge_fifo_persistent_reader.py test/test_fifo_delivery.py test/test_transport_parity.py test/test_cancel_flags.py`
  -> `26 passed`.
- Current review: PR238 is useful empty-reply diagnostics, but not a root
  communication-stability fix. PR239 contains useful observability pieces, but
  its Codex 900 second degraded fallback is a policy change rather than
  accepted-turn ownership, and the branch is too broad to merge as-is.
- Root direction: prioritize accepted-turn ownership, provider epochs, compact
  CCB-owned evidence, and chain reply lineage before adopting degraded timeout
  behavior as a default.
- Current focus: ask-after-reply temporal stability under clear and long
  provider session files.
- Minimal plan: start with tests, provider-acceptance fields, clear barriers,
  compact evidence on the existing polling path, and recovery-only fallback.
- Clear analysis: current `ccb clear` is pane input, not an accepted-turn or
  provider-epoch boundary. Codex and Claude both need the same monotonic
  post-clear evidence boundary before further timeout fallback work.
- `ccb_clear` direction: keep provider session files as raw evidence, but let
  CCB own continuity through per-agent epochs and a short post-clear probe.
- Temporal stability continuation: implement in small slices: tests/probes,
  provider acceptance fields, `ccb_clear` barrier, compact polling evidence,
  terminal predicates, reply lineage, and recovery-only fallback.
- Narrowed first slice: first try a small hard-gate implementation using the
  existing `pending_anchor`, `anchor_seen`, session-rotate, and empty-reply
  detector paths before adding a tailer or larger evidence subsystem.
- Coworker review `job_7311c8f04328` passed the small hard-gate proposal with
  conditions: first implement provider-acceptance, fallback-quarantine, and
  session-rotate gates; defer clear barrier until dispatcher integration is
  selected.
- Low-latency direction: first remove broad fallback scans from the normal path
  and add compact evidence; persistent tailer remains benchmark-backed optional
  optimization.
- Coworker review `job_b5689ffafbf1` raised concerns that tailer-first is
  overdesigned for the first slice; this plan adopts the simpler polling-first
  implementation order.
- First source slice landed in the working tree: Codex active completions now
  require accepted request-anchor evidence or explicit `no_wrap`; broad anchor
  fallback is quarantined as diagnostic evidence; session rotate without a
  fresh anchor cannot complete as success.
- Last verified for the first source slice:
  `PYTHONPATH=lib python -m pytest -q test/test_v2_ccbd_dispatcher.py`
  -> `39 passed`;
  `PYTHONPATH=lib python -m pytest -q test/test_v2_execution_service.py`
  -> `63 passed`;
  `PYTHONPATH=lib python -m compileall -q ...` -> passed;
  `git diff --check -- ...` -> passed.
- Added strict temporal verification with fake-clock duration: fallback
  quarantine can persist for 30 minutes without completing the job, and a later
  official Codex session binding switch still produces `SESSION_ROTATE`,
  fresh `ANCHOR_SEEN`, and accepted new-session reply evidence.
- Second timing slice landed in the working tree: Codex prompt delivery now
  tracks `delivery_last_progress_at`; slow official session file movement keeps
  the ask pending, while a missing official session/log past the no-progress
  window returns `incomplete/codex_session_file_missing` with
  `no_reply_reason=completion_detection_gap`.
- Last verified for the no-progress slice:
  `PYTHONPATH=lib python -m pytest -q test/test_v2_execution_service.py`
  -> `65 passed`;
  `PYTHONPATH=lib python -m pytest -q test/test_stability_regressions.py`
  -> `16 passed`;
  `PYTHONPATH=lib python -m pytest -q test/test_provider_execution_service_runtime.py`
  -> `11 passed`;
  `PYTHONPATH=lib python -m pytest -q test/test_codex_runtime_accelerator_polling.py`
  -> `5 passed`;
  `PYTHONPATH=lib python -m compileall -q ...` -> passed;
  `git diff --check` -> passed.

## Done

- Recorded that the current user decision is plan-tree only: do not land new
  source changes or promote follow-up work into main.
- Classified PR226 benefits for Linux, macOS, and WSL:
  - reduces FIFO no-reader windows with a persistent reader
  - bounds sender waits when the bridge is unavailable
  - adds read-level ACK evidence
  - uses spool files for large FIFO payloads
  - adds communication-path logs for previously silent failures
- Recorded current risk analysis in
  [topics/pr226-risk-and-adoption-note.md](topics/pr226-risk-and-adoption-note.md).
- Recorded the current ask/clear/session ownership boundary and PR238/PR239
  review in
  [topics/accepted-turn-binding-and-pr238-239-review.md](topics/accepted-turn-binding-and-pr238-239-review.md).
- Expanded the root-fix architecture in
  [topics/root-stability-architecture.md](topics/root-stability-architecture.md).
- Added the focused temporal-stability model in
  [topics/ask-reply-temporal-stability.md](topics/ask-reply-temporal-stability.md).
- Added the minimal non-overdesigned landing plan in
  [topics/minimal-temporal-stability-plan.md](topics/minimal-temporal-stability-plan.md).
- Added source-backed Codex/Claude post-clear behavior analysis in
  [topics/clear-after-logic-codex-claude.md](topics/clear-after-logic-codex-claude.md).
- Added provider-neutral `ccb_clear` epoch/probe design in
  [topics/ccb-clear-epoch-probe-design.md](topics/ccb-clear-epoch-probe-design.md).
- Added temporal-stability implementation slice design in
  [topics/temporal-stability-slice-design.md](topics/temporal-stability-slice-design.md).
- Added narrowed small hard-gate first-slice proposal in
  [topics/small-hard-gate-first-slice-plan.md](topics/small-hard-gate-first-slice-plan.md).
- Added persistent tailer low-latency design in
  [topics/persistent-tailer-low-latency-design.md](topics/persistent-tailer-low-latency-design.md).
- Recorded coworker review and accepted simplification in
  [topics/coworker-review-20260706-temporal-stability.md](topics/coworker-review-20260706-temporal-stability.md).
- Recorded coworker review for the narrowed hard-gate slice in
  [topics/coworker-review-20260706-small-hard-gate.md](topics/coworker-review-20260706-small-hard-gate.md).
- Implemented the reviewed first-batch hard gates from
  [topics/small-hard-gate-first-slice-plan.md](topics/small-hard-gate-first-slice-plan.md):
  provider acceptance, fallback quarantine, session rotate gating, and
  dispatcher-side normalization before `dispatcher.complete(...)`.
- Added the no-progress delivery timeout and degradation design in
  [topics/no-progress-delivery-timeout-and-degradation-plan.md](topics/no-progress-delivery-timeout-and-degradation-plan.md).
- Implemented the first no-progress delivery timeout source slice from
  [topics/no-progress-delivery-timeout-and-degradation-plan.md](topics/no-progress-delivery-timeout-and-degradation-plan.md):
  compact progress marker, `delivery_last_progress_at`, missing-session
  `incomplete` diagnostics, and no-progress snapshot deadline.

## Next

1. Decide whether CCB wants PR226-style transport hardening as a release goal
   or only as a diagnostic/stress-mode hardening track.
2. If promoted later, add focused tests for ACK semantics, marker uniqueness,
   spool path constraints, and cancel prompt injection before source changes.
3. Keep Linux/macOS/WSL as the only supported target set for this plan slice.
4. For ask/clear/session reliability, prioritize accepted-turn binding,
   provider epoch evidence, and compact CCB-owned event indexing before adding
   more timeout fallback behavior.
5. Resolve the first two root design questions: clear invalidation semantics
   and where compact provider evidence should live.
6. Promote the temporal state machine into an implementation-ready Slice 0 test
   list before touching source behavior.
7. Use [topics/minimal-temporal-stability-plan.md](topics/minimal-temporal-stability-plan.md)
   as the readiness gate for the first implementation slice.
8. Treat [topics/clear-after-logic-codex-claude.md](topics/clear-after-logic-codex-claude.md)
   as the provider-specific source map for clear barrier implementation.
9. Treat [topics/ccb-clear-epoch-probe-design.md](topics/ccb-clear-epoch-probe-design.md)
   as the workflow contract for self-clear, post-clear probe, and provider
   session continuity.
10. Use [topics/temporal-stability-slice-design.md](topics/temporal-stability-slice-design.md)
    to prepare the first implementation-ready test and field slice.
11. Add the second-batch clear barrier through a dispatcher-owned or
    dispatcher-consumed signal; do not make `project_clear.py` own job
    completion directly.
12. Decide whether to implement the deferred diagnostic-only pieces from
    [topics/no-progress-delivery-timeout-and-degradation-plan.md](topics/no-progress-delivery-timeout-and-degradation-plan.md):
    PR238 empty-reply sub-classification and narrow PR239 high-confidence
    provider error mapping.
13. Add reply lineage hardening for B -> C -> A chain completion paths.
14. Use [topics/persistent-tailer-low-latency-design.md](topics/persistent-tailer-low-latency-design.md)
    as an optional optimization candidate pending benchmark evidence.
15. Use [topics/coworker-review-20260706-temporal-stability.md](topics/coworker-review-20260706-temporal-stability.md)
    as the first-slice complexity gate.

## Deferred

- Changing shipped ACK wording or sender result semantics.
- Changing marker generation.
- Enforcing spool path restrictions.
- Changing cancel prompt injection behavior.
- Merging PR239 wholesale as a stability fix.
- Making Codex bounded no-terminal fallback the default without a separate
  policy decision and accepted-turn/epoch tests.
- Adding persistent tailer or a large global event-store abstraction before the
  compact polling evidence contract and recovery-only fallback are proven.
- Native Windows transport support.
