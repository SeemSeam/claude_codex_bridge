# Independent FIFO And Empty-Result Review

Date: 2026-09-19
Role: evidence
Status: failed acceptance; existing regression suites pass
Related: [roadmap](../roadmap.md), [FIFO contract](../topics/unified-message-fifo.md)

Follow-up: the watchdog finding below was fixed after owner clarification;
see [turn-end-only fix and verification](provider-turn-end-only-20260919.md).
This record preserves the pre-fix review evidence.

## Results

Agent1 independently ran the same 19 test files listed in demo's final
verification command, in two disjoint batches: 347 passed (12 files) and
55 passed (7 files), total **402 passed**. Runner:
`uv run --with pytest --with pytest-asyncio --no-project python -m pytest -q`.

First batch, under `test/`: test_unified_message_fifo.py,
test_ccbd_retry_failure_detail.py, test_codex_reply_delivery.py,
test_claude_execution_polling.py, test_v2_message_bureau_dispatcher_integration.py,
test_v2_ccbd_dispatcher.py, test_v2_execution_service.py,
test_claude_queued_prompt_activation.py, test_codex_runtime_accelerator_polling.py,
test_cursor_provider.py, test_grok_provider.py, test_pi_pane_execution.py.

Second batch: test_claude_hook_results.py, test_reply_delivery_start_completion.py,
test_reply_delivery_polling_gate.py, test_reply_delivery_formatting.py,
test_stability_regressions.py, test_v2_mailbox_kernel_service.py,
test_ccbd_lease_expiry_sweep.py.

These cover chronological admission/restart, held deliveries, cancellation,
superseded empty notices, chain notices, and pure-empty retry suppression.
Passing them does not establish the stronger turn-exclusivity contract below.

## P1: Watchdog Releases A Still-Live Provider Turn

`lib/ccbd/services/dispatcher_runtime/reply_delivery_runtime/watchdog.py`
terminalizes an untracked delivery solely on elapsed time (default 1800 s),
calling `dispatcher.complete` with INCOMPLETE/reply_delivery_no_turn_evidence.
Finalization clears the target slot and calls `ExecutionService.finish`.
That method only removes bookkeeping; it does not stop the provider turn.
The same tick can then dispatch another message to the same target.

Independent probe:
`/var/tmp/ccb-agent1-fifo-review-f2DNXg/test_turn_exclusivity.py`.
Run from repository root:

```sh
PYTHONPATH=lib uv run --with pytest --with pytest-asyncio --no-project python -m pytest -q /var/tmp/ccb-agent1-fifo-review-f2DNXg/test_turn_exclusivity.py
```

The probe reuses the existing watchdog integration scenario with a separate
provider-live oracle: a delivery starts a live turn, poll emits no terminal
events, and only cancellation can clear it in this scenario. It records new
submissions while the turn remains live; daemon bookkeeping completion cannot
clear that oracle. Advancing the fake clock beyond the configured 60 s bound
produces **1 failed**: a new ask starts while the delivery turn remains live.
No real provider or work-environment daemon was mutated.

The existing test
`test_dispatcher_untracked_reply_delivery_holds_then_watchdog_consumes`
explicitly expects this queue advance, so its passing result is not acceptance
evidence for strict exclusivity. The timeout must report uncertainty without
releasing the target until exact turn-end or verified stop evidence exists.
Add a long-running/no-evidence regression asserting no second submission.

## Verification Limits

- Compared FAILED node IDs from `/var/tmp/full-suite-run3.txt` and
  `/var/tmp/full-suite-fixround.txt`: 30 each, identical sets, zero additions.
  Both are modified-tree runs. This review did not independently reproduce
  all failures on clean HEAD and does not certify all 30 as environmental.
- No full-suite rerun in this review; the second log reports 7420 passed,
  30 failed, 3 skipped, 4 subtests passed.
- Real Codex/Claude turn qualification remains open. Prior credential probe
  results apply only to the environments actually checked, not the whole host.
- No runtime source changes, commits, pushes, releases, or external auth
  changes were made by this review. Temporary probe artifacts are local and
  may expire; the reproduction method above is the durable evidence.
