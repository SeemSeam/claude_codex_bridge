# Managed Provider Completion Reliability

Date: 2026-06-12

## Purpose

Track short implementation slices for managed pane-backed provider completion
terminalization, prompt-delivery acceptance, empty-reply guards, timeout
behavior, and diagnostics.

Current active incidents:

- Issues #345–#348: Codex app-server lifecycle, remote resume/clear reporting,
  terminal error propagation, and Kimi timeout environment filtering. See the
  [repair plan](topics/issues-345-348-repair.md) for current scope and gates.

- Claude-backed `ask` jobs can visibly finish in the provider session, emit
  `assistant_chunk` with `stop_reason = "end_turn"`, but not reach CCB
  `terminal=true` until the 900-second reliability timeout.
- Codex-backed workers can consume the mailbox `task_request` while the
  pane-backed Codex session never records the active `CCB_REQ_ID`, eventually
  failing with `codex_prompt_delivery_failed / delivery_anchor_missing`.
- Codex native subagents can fork the parent conversation, inherit the same
  `CCB_REQ_ID`, and emit a separate `task_complete`; that child rollout and
  child result must never become the CCB target agent's caller-visible reply.

## Authority

The product/runtime contract remains:

- [../../../managed-provider-completion-reliability-plan.md](../../../managed-provider-completion-reliability-plan.md)

This plan root records active repair sequencing and review handoff only. It
does not override provider/session contracts.

## File Map

- [roadmap.md](roadmap.md): active repair sequence and deferred items.
- [open-questions.md](open-questions.md): unresolved design choices.
- [topics/claude-end-turn-terminalization.md](topics/claude-end-turn-terminalization.md):
  incident analysis, accepted P0 slice, test matrix, and rollout risks.
- [topics/codex-prompt-delivery-binding-drift.md](topics/codex-prompt-delivery-binding-drift.md):
  Codex prompt-delivery acceptance failure model, runtime binding drift repair
  slices, and recovery guardrails.

## Related Plans

- [Empty-result caller notices](../inter-agent-comm-reliability/topics/empty-result-caller-notice.md):
  2026-09-18 planned policy replaces automatic empty-result recovery with a
  caller inspection notice; preserves exact terminal evidence and attribution.

- [../ccb-maintenance-heartbeat/README.md](../ccb-maintenance-heartbeat/README.md)
- [../ccb-maintenance-heartbeat/topics/ask-runtime-health-mechanism.md](../ccb-maintenance-heartbeat/topics/ask-runtime-health-mechanism.md)

## Scope

In scope:

- Provider-specific conversion of authoritative provider terminal evidence into
  normalized completion items.
- Provider-specific prompt-delivery acceptance evidence for pane-backed
  runtimes before completion tracking begins.
- `SessionBoundaryDetector` empty boundary behavior.
- Focused tests for Claude `end_turn`, subagents, tool-use turns, empty reply,
  callback/silence compatibility, and timeout fallback.
- Focused tests for Codex stale PID/session binding, delivery preflight,
  `delivery_anchor_missing` diagnostics, native subagent session/turn fencing,
  and maintenance heartbeat visibility.
- Follow-up planning, deferred to later slices, for
  provider-finished-but-CCB-not-terminal suspicion evidence used by
  maintenance heartbeat and `ccb_self`.

Out of scope for the first repair slice:

- Changing generic detector behavior to interpret provider-specific
  `stop_reason` fields.
- Treating `max_tokens` or `stop_sequence` as normal completed turns without
  separate provider-specific evidence.
- Reclassifying timeout-with-reply from `incomplete` to `completed`.
- Automatically resending Codex prompts when no anchor is observed.
- Changing ask submit-only CLI semantics.
- Mutating live `.ccb` runtime state during validation.
