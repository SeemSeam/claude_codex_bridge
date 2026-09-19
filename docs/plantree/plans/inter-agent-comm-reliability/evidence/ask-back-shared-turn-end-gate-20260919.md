# Ask/Back Shared Turn-End Gate (Minimal Reuse)

Date: 2026-09-19
Role: evidence
Status: implemented; deterministic verification passed; real-provider qualification unchanged/open
Related: [turn-end-only fix](provider-turn-end-only-20260919.md),
[roadmap](../roadmap.md), [FIFO contract](../topics/unified-message-fifo.md)

## Owner Direction And Change

Owner-confirmed minimal reuse: ask and back deliveries share the existing
public Provider turn-end judgement. No new detector, no reply-delivery
timeout, no second per-provider rule set in the dispatcher.

Before this round the dispatcher polling gate held a parallel provider
fork: `_reply_delivery_turn_end_proven` re-derived codex/claude/pi-family
turn-attribution from provider-internal runtime fields, and delivery
identity came from the legacy `reply_delivery_complete_on_dispatch`
runtime flag. Removed both duplications in
`lib/ccbd/services/dispatcher_runtime/polling_service.py`:

- Delivery identity now comes from the durable job record through the
  existing public helper `is_reply_delivery_job`
  (`reply_delivery_runtime/common.py`: `reply_delivery` message type or
  provider option) — the same identity the delivery finalization paths
  already use. The legacy runtime flag remains readable only as a
  compatibility fallback when a submission is validated without its job
  record; it identifies the message class and never declares completion
  capability. Adapter writes of the flag are unchanged.
- `_reply_delivery_turn_end_proven` (the per-provider second rule set) is
  deleted. Deliveries flow through the same shared gate as asks:
  `annotate_completion_authority`, then delivery-only body normalization,
  then the codex active-acceptance isolation gate for every resulting
  COMPLETED decision. The body policy (`_reply_delivery_turn_end_outcome`): an
  INCOMPLETE empty turn end (`task_complete_empty_reply`,
  `hook_stop_empty_reply`, `<provider>_empty_reply`, or
  `error_type=empty_provider_reply`) completes as
  `COMPLETED/reply_delivery_turn_complete`. FAILED/CANCELLED and other
  attributable incompletes keep their own status and semantics; asks keep
  the `non_empty_reply` gate and the existing empty-result caller notice
  policy.
- Provider raw-event interpretation stays in the adapters/detectors; the
  codex acceptance gate, session/anchor isolation, cancellation,
  no-progress reliability policy, and Claude 180 s final-text grace are
  unchanged. Inspected and intentionally untouched:
  `lib/completion/{registry,tracker,models}.py`,
  `lib/provider_execution/service_runtime/{polling,reliability}.py`,
  `lib/provider_execution/completion_authority.py`.

Observable reason change (fail-closed preserved): a codex delivery whose
terminal decision lacks proven anchor acceptance now finalizes as
`INCOMPLETE/terminal_before_provider_acceptance` (shared acceptance gate)
instead of the ask-oriented `task_complete_empty_reply`. The delivery never
completed in that state before or after; only the attributable reason is
more precise, and the empty turn end remains recorded via the
`reply_delivery_turn_end` diagnostic.

## Files

- `lib/ccbd/services/dispatcher_runtime/polling_service.py`: shared gate,
  job-record delivery identity, legacy-flag fallback, removed
  `_reply_delivery_turn_end_decision`/`_reply_delivery_turn_end_proven`;
  both call sites (`poll_completion_updates`, `_tick_tracker`) pass the
  durable job record.
- `test/test_reply_delivery_polling_gate.py`: updated the unproven-acceptance
  pin; added job-marker delivery completion without the legacy flag,
  ask-marker empty turn end kept for the caller notice, and cancelled
  delivery terminal not disguised as success.
- `test/test_unified_message_fifo.py`: updated the unproven-acceptance pin
  (both decision wordings); `HoldingExecutionService` gained
  `delivery_runtime_flag`; added an end-to-end empty-turn-end delivery
  completion identified only by the job marker (head consumed, no
  recursive notice to the source agent).

## Verification

Runner: `uv run --with pytest --with pytest-asyncio --no-project python -m pytest -q`
with `PYTHONPATH=lib`, from the repository root.

- Same 19-file suite as the prior evidence records (delivery, dispatcher,
  adapter, mailbox suites):
  **406 passed in 3.53 s** (402 prior + 4 new; none removed).
- Adjacent gate consumers:
  `test_active_job_followup.py test_v2_ccbd_mount_ownership.py
  test_v2_ccbd_socket.py test_v2_completion_tracker.py
  test_v2_completion_detectors.py` -> **113 passed**.
- `python -m compileall -q` on the touched runtime packages -> passed;
  `git diff --check` -> clean.

Coverage mapping for this change: ask/back shared completion path (gate
unit tests plus FIFO integration), with/without the legacy delivery flag
(job-marker gate tests and the no-flag end-to-end delivery), anchored
normal end (`test_validate_decision_normalizes_proven_empty_delivery_turn_ends`,
`test_confirmed_reply_delivery_empty_transport_ack_remains_completed`),
empty-body back completes (`reply_delivery_turn_complete` cases), ordinary
ask empty body kept for the caller notice policy
(`test_job_marker_ask_keeps_empty_turn_end_for_caller_notice`,
`test_ordinary_codex_empty_completion_still_fails_closed`), abnormal
terminal outcomes not disguised as success (FAILED `pane_dead`, CANCELLED
`cancelled_by_user`, `omp_request_superseded`), long-running turns not
advanced by elapsed time
(`test_dispatcher_reply_delivery_without_flag_waits_for_provider_turn_end`),
and restart/persistence consistency
(`test_provider_completed_delivery_stays_consumed_after_restart`,
`test_cancelled_delivery_is_not_resurrected_and_queue_advances`).

## Limits

Independent agent1 review: the 19-file set plus the five adjacent files were
re-run together: **519 passed in 29.19 s**. A separate conflicting-identity
probe exposed a compatibility bug: a durable ordinary ask plus a legacy
delivery flag was converted to `COMPLETED/reply_delivery_turn_complete`.
Agent1 corrected `_is_reply_delivery` to return the durable job classification
whenever a job is present, using the legacy flag only when no job exists.
Added `test_durable_ask_identity_overrides_legacy_delivery_flag` to preserve
the ordinary ask empty-result notice path under this conflict.
After that correction, the polling gate, unified FIFO, and dispatcher
integration suites passed together: **118 passed in 1.79 s**.
`git diff --check` also passed. The full 519-test set was not repeated after
this localized correction.

- Deterministic verification only. This is not a real Codex/Claude
  qualification run; the checked-environment credential gaps in the
  roadmap still apply. No live daemon campaign was re-run for this round:
  the change is internal to the dispatcher completion gate, the prior
  /var/tmp fake-provider campaign launcher is not reproducible from the
  retained artifacts, and restarting daemons is out of scope here.
- No commits, pushes, releases, installed-daemon restarts, or external
  Provider auth/config changes. `mobile/`, `.zai/`, and unrelated working
  tree modifications are untouched.
