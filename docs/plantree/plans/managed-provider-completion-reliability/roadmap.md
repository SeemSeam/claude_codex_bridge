# Managed Provider Completion Reliability Roadmap

Date: 2026-06-12

## Status Summary

2026-09-14 current maintenance slice: [Issues #345–#348 repair](topics/issues-345-348-repair.md).
Implementation, testing, commit, issue replies, and v8.6.17 publication are
complete (evidence:
[repair evidence record](../../baseline/evidence/issues-345-348-repair-20260914.md));
all four repairs reproduced failing before and passing after. The older status
below is a historical checkpoint, not the current worktree/remote state.

- Current status: Codex native subagent reply fencing is implemented on `main`
  in the working tree; not committed or pushed.
- Last verified: focused Codex/Python regressions (`41 passed`), complete Codex
  plus execution/mailbox suites (`293 passed`), Rust accelerator tests
  (`10 passed`), and a real source-runtime Codex `spawn_agent` ask
  (`job_670426094f8e`) whose persisted/caller reply was exactly
  `PARENT_FINAL_ONLY_0713` while the child rollout contained
  `CHILD_SECRET_FINAL_0713`.
- Next target: final diff/static review, then commit the Codex native subagent
  reply-fence slice on `main` when requested.

## Done

- Captured the Claude pane-backed incident: two Claude-backed jobs produced
  visible assistant replies and `assistant_chunk` events with
  `stop_reason = "end_turn"`, but CCB stayed `terminal=false` until
  `completion_timeout`.
- Received `worker1` code analysis. It identified that Claude
  `stop_reason=end_turn` is parsed and included in `ASSISTANT_CHUNK`, but the
  Claude state machine only emits `TURN_BOUNDARY` for `CCB_DONE` text or
  `system/turn_duration`.
- Received `reviewer1` ask-system review. It confirmed the same P0 bug and
  raised related risks: `SessionBoundaryDetector` empty boundary completion,
  timeout-with-reply semantics, and heartbeat visibility for
  provider-finished-but-not-terminal states.
- Chose the first repair boundary: fix Claude state-machine terminal evidence
  and session-boundary empty reply behavior before broader timeout or heartbeat
  policy changes.
- Implemented the P0 working-tree slice:
  - Claude primary assistant `stop_reason=end_turn` with non-empty reply and an
    observed anchor now emits `TURN_BOUNDARY(reason=assistant_end_turn)`.
  - `SessionBoundaryDetector` now treats an empty `TURN_BOUNDARY` with no prior
    assistant reply as `incomplete/task_complete_empty_reply` with
    `empty_reply` and `error_type=empty_provider_reply` diagnostics.
  - Focused tests cover primary `end_turn`, subagent `end_turn`, `tool_use`,
    empty `end_turn`, existing `CCB_DONE`, `turn_duration`, and empty
    session-boundary handling.
- Completed reviewer1 code review for the P0 slice with PASS and no blocking
  issues. The review confirmed the `end_turn` guards, duplicate-boundary guard,
  empty boundary behavior, and deferral of silence/callback/session-rotation
  hardening to the next validation phase.
- Added public release-note coverage for the Claude `stop_reason=end_turn`
  terminalization and `SessionBoundaryDetector` empty boundary guard under the
  release candidate line now targeting `v7.5.0`.
- Captured the Codex prompt-delivery boundary: worker mailbox events can be
  consumed while the managed Codex session log never records the active
  `CCB_REQ_ID`; the current failure terminalizes as
  `codex_prompt_delivery_failed / delivery_anchor_missing`.
- Added the Codex repair plan:
  [topics/codex-prompt-delivery-binding-drift.md](topics/codex-prompt-delivery-binding-drift.md).
- Implemented Codex native subagent reply fencing:
  - subagent rollouts are excluded from scan, watchdog, persisted binding,
    reader rotation, and recovery authority;
  - top-level `task_started.turn_id` is immutable for the active CCB job;
  - foreign-turn assistant/terminal events and native collaboration messages
    cannot enter the caller-visible reply;
  - Python and Rust accelerator paths share the same behavior.

## In Progress

- Monitor the long-running post-release main Tests workflow; tag-triggered
  release workflows and required release artifacts are complete.

- Final review and landing of the Codex native subagent reply-fence slice.

## Next

1. Add tracker/dispatcher-level regression coverage proving accepted Claude
   `end_turn` terminal evidence prevents the 900-second reliability timeout
   path.
2. Add or run existing silence and callback-chain regressions to confirm earlier
   terminalization does not change delivery suppression or callback routing
   semantics.
3. Add session-rotation regression coverage for stale detector state before a
   later anchored Claude `end_turn`.
4. Run any remaining release-gate provider completion suites before
   merge/release handoff.
5. Record any review findings or policy changes before broadening timeout or
   heartbeat behavior.
6. Implement Codex binding evidence for dead/stale `codex.pid`, bridge PID,
   pane PID mismatch, session log freshness, and provider activity freshness.
7. Add a Codex delivery preflight gate so known-stale bindings fail as
   retryable provider-runtime errors before tmux prompt paste.
8. Surface `delivery_anchor_missing` as degraded provider health evidence in
   doctor/project-view/maintenance paths with guarded restart guidance.

## Deferred

- Decide timeout-with-reply semantics. `completion_timeout` with a non-empty
  reply may need richer diagnostics, but should not be reclassified as
  `completed` without a separate decision.
- Add maintenance heartbeat suspicion for provider reply evidence present while
  CCB remains non-terminal.
- Revisit callback and silence interaction only after the P0 repair is stable.
- Decide whether `stop_sequence` or `max_tokens` ever constitute terminal
  evidence for Claude; they are intentionally excluded from the first slice.
- Decide whether Codex `delivery_anchor_missing` can ever trigger automatic
  restart and retry, or whether retry must remain explicit to avoid duplicate
  downstream side effects.
