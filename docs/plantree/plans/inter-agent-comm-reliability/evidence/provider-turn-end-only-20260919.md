# Provider Turn-End Only Delivery Fix

Date: 2026-09-19
Role: evidence
Status: implemented; focused verification passed; real-provider qualification open
Related: [roadmap](../roadmap.md), [prior review](agent1-independent-review-20260919.md)

## Owner Clarification And Implementation

Every provider already has its conversation-turn completion mechanism. Reply
delivery must reuse the ordinary provider completion pipeline; no new timeout
fallback or classification of providers as lacking turn detection is needed.
The legacy delivery flag is not a completion-capability declaration.

Removed the newly introduced `reply_delivery_runtime/watchdog.py`, its tick
hook and exports, and the helper that classified submissions by the absence
of the delivery flag. Successful sends remain running in `start_completion`.
Existing `ExecutionService.poll` and dispatcher completion handling finish
deliveries using provider decisions. Existing provider detectors, failure and
cancellation policies were not redesigned.

## Verification

The same 19-file suite enumerated in the prior review passed: **402 passed in
3.94 s**, using `uv run --with pytest --with pytest-asyncio --no-project python
-m pytest -q` with those files.

Replaced timeout-advance expectations with:

- `test_dispatcher_reply_delivery_without_flag_waits_for_provider_turn_end`:
  the retired timeout environment variable and a simulated day of elapsed
  time cannot release the slot; a provider terminal update through
  `poll_completions` consumes the delivery and permits the next ask.
- `test_provider_completed_delivery_stays_consumed_after_restart`: ordinary
  acceptance plus turn-end evidence, without a delivery flag, completes the
  delivery once and prevents resurrection after dispatcher recreation.

The prior watchdog overlap finding is fixed in source. This verification is
deterministic, not a new real Codex/Claude run or full-suite run. No commit,
push, release, installed-daemon restart, or external credential change occurred.
