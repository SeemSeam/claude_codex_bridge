# Inter-Agent Communication Reliability

Date: 2026-06-14

Last updated: 2026-09-19

## Current Planning Slice

Mode: `execute-ready` / `status-update`; owner-approved source fix and evidence.

Owner-approved direction under implementation and review:
ordinary ask requests and back results share one chronological FIFO per
target Agent through the exact processing-turn end; empty results generate a
caller inspection notice instead of automatic empty-result retries or
reactivation. The no-evidence watchdog identified by independent review was
removed on 2026-09-19; deliveries reuse existing provider turn completion,
and the dispatcher polling gate now shares one turn-end rule set for asks
and deliveries (job-record delivery identity, no per-provider fork).
Real Codex/OMP FIFO qualification passed using an isolated installed snapshot;
Codex empty notice passed, OMP empty-terminal and Claude qualification remain
open (see roadmap).

- [Independent review](evidence/agent1-independent-review-20260919.md):
  402 existing tests pass; an independent exclusivity probe reproduces the
  original watchdog overlap defect.
- [Turn-end-only fix](evidence/provider-turn-end-only-20260919.md): watchdog
  removed; elapsed time cannot advance the queue; 402 tests pass.
- [Shared ask/back turn-end gate](evidence/ask-back-shared-turn-end-gate-20260919.md):
  dispatcher delivery fork removed; job-record delivery identity with legacy
  flag as compat fallback; 406 tests pass.
- [Installed Codex/OMP qualification](evidence/installed-codex-omp-qualification-20260919.md):
  real A→X→B ordering passed in both directions; Codex empty notice passed;
  OMP native empty continuation did not emit a final settled event.
- [Screen and abnormal reply guidance](evidence/screen-caller-inspection-20260919.md):
  read-only `ccb screen <agent>` and caller-owned abnormal-result inspection
  landed in source; real Codex/OMP pane capture verified without daemon restart.
- [v8.6.19 release validation](evidence/release-8619-validation-20260919.md):
  source commits, full-suite failure audit, corrected rerun and package gates.

- [Unified FIFO design](topics/unified-message-fifo.md): ordering, execution
  ownership, recovery, implementation slices, acceptance tests, and the Q1
  audit answers.
- [Empty-result notice design](topics/empty-result-caller-notice.md): minimal
  classification, caller guidance, retry removal, compatibility gates, and
  the Q1 audit answers.

These topics define the intended contract; recorded implementation evidence
does not yet establish full acceptance. Earlier transport analysis below
remains historical context.

## Purpose

Track CCB inter-agent message transport reliability proposals and source
slices before they are allowed into the release path.

This plan currently records analysis of PR226-style changes:

- persistent Codex FIFO reader
- bounded non-blocking FIFO writes
- read ACK files
- large payload spool pointers
- cancel flag visibility
- communication-path logging

## Current Decision

The first small hard-gate source slice and the Codex no-progress delivery
timeout slice have been implemented in the working tree after coworker review.
They are intentionally narrower than PR238/PR239: keep provider-turn ownership
as the root fix, do not merge broad degraded-timeout policy, and do not
introduce a persistent tailer until benchmark evidence requires it.

The useful direction is accepted for further study on Linux, macOS, and WSL,
but adoption requires a tighter reliability boundary and regression coverage.
Native Windows support is explicitly out of scope for this plan slice.

## Authority

Runtime contracts still live in the product documents under `docs/`.
This plan root records candidate transport changes, risks, and readiness gates;
it does not override shipped behavior.

Related authority and context:

- [../../../managed-provider-completion-reliability-plan.md](../../../managed-provider-completion-reliability-plan.md)
- [../../baseline/README.md](../../baseline/README.md)
- [../../../../plans/ccb-communication-test-plan.md](../../../../plans/ccb-communication-test-plan.md)

## File Map

- [roadmap.md](roadmap.md): current planning state, deferred gates, and next
  review targets.
- [topics/pr226-risk-and-adoption-note.md](topics/pr226-risk-and-adoption-note.md):
  PR226 risk analysis and adoption criteria.
- [topics/accepted-turn-binding-and-pr238-239-review.md](topics/accepted-turn-binding-and-pr238-239-review.md):
  current ask/clear/session stability boundary and PR238/PR239 review.
- [topics/root-stability-architecture.md](topics/root-stability-architecture.md):
  deeper root-fix architecture for accepted turns, provider epochs, compact
  CCB-owned evidence, and chain reply ownership.
- [topics/ask-reply-temporal-stability.md](topics/ask-reply-temporal-stability.md):
  focused ask-after-reply temporal stability model for clear, long session
  files, epoch barriers, and reply lineage.
- [topics/minimal-temporal-stability-plan.md](topics/minimal-temporal-stability-plan.md):
  minimal landing plan using provider acceptance fields, clear barriers, compact
  evidence on the existing polling path, and recovery-only fallback.
- [topics/clear-after-logic-codex-claude.md](topics/clear-after-logic-codex-claude.md):
  source-backed analysis of the original post-clear behavior for Codex and
  Claude, including why clear currently lacks a CCB-owned epoch boundary.
- [topics/ccb-clear-epoch-probe-design.md](topics/ccb-clear-epoch-probe-design.md):
  provider-neutral `ccb_clear` design for epoch barriers, self-clear, post-clear
  probes, and provider session continuity.
- [topics/temporal-stability-slice-design.md](topics/temporal-stability-slice-design.md):
  implementation-slice design that returns from `ccb_clear` to accepted-turn,
  compact-evidence, terminal-predicate, and reply-lineage hardening.
- [topics/small-hard-gate-first-slice-plan.md](topics/small-hard-gate-first-slice-plan.md):
  narrowed first implementation proposal that reuses current
  `pending_anchor`/`anchor_seen`/session-rotate logic as hard completion gates
  before introducing tailer or broader rewrites.
- [topics/persistent-tailer-low-latency-design.md](topics/persistent-tailer-low-latency-design.md):
  candidate low-latency optimization if benchmarks show existing polling remains
  too slow after fallback scans are removed from the normal path.
- [topics/coworker-review-20260706-temporal-stability.md](topics/coworker-review-20260706-temporal-stability.md):
  accepted review input that defers persistent tailer from the first slice and
  simplifies post-clear probe semantics.
- [topics/coworker-review-20260706-small-hard-gate.md](topics/coworker-review-20260706-small-hard-gate.md):
  accepted review input for the narrowed small hard-gate first slice, including
  first-batch gate order and clear-barrier deferral.
- [topics/no-progress-delivery-timeout-and-degradation-plan.md](topics/no-progress-delivery-timeout-and-degradation-plan.md):
  second timing slice that changes Codex delivery failure from start-time
  timeout to no-progress timeout, and uses PR238/PR239 only for attributable
  non-success diagnostics.

## Scope

In scope:

- Linux, macOS, and WSL CCB inter-agent communication reliability.
- Codex bridge FIFO lifecycle and send-path behavior.
- ACK semantics, large request transport, marker uniqueness, and failure
  diagnosability.
- Cancel visibility at the CCB-to-agent prompt boundary.

Out of scope for this slice:

- Native Windows transport, startup, health, or mux behavior.
- Provider-specific completion terminalization remains in
  [../managed-provider-completion-reliability/README.md](../managed-provider-completion-reliability/README.md).
  This slice consumes its evidence and changes empty-result handling after a
  terminal decision; it does not weaken request/session/turn attribution.
- Immediate source implementation or release promotion.
