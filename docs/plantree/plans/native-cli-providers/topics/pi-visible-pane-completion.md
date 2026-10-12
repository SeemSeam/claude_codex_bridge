# Pi Visible-Pane Completion

Date: 2026-07-29
Status: source implementation and authenticated acceptance complete; awaiting commit

## Problem

CCB 8.5.0 executes every Pi ask in a separate headless
`pi --mode json` subprocess. That path has reliable `agent_settled` completion,
but the managed Pi TUI cannot show the request or response. It also retains a
fixed per-process wall-clock timeout, so a long Pi turn can be marked
incomplete and terminated even while Pi is still making valid progress.

PR #283 demonstrates that sending the ask to the managed Pi pane restores TUI
visibility. Its session-JSONL completion detector is not sufficient to land as
written: Pi does not persist `agent_settled` in the interactive session file,
non-tool stop reasons include failure/incomplete outcomes, existing
`mode=pi_run` jobs would be routed to the wrong adapter, and the proposed
300-second timeout is a regression from the 8.5.0 terminal contract.

## Frozen Contract

- New Pi asks use the already managed, visible Pi pane by default.
- `CCB_PI_EXECUTION_MODE=headless` is the explicit rollback switch.
- Adapter dispatch is selected per submission from persisted runtime mode.
  Existing `mode=pi_run` submissions continue through the 8.5.0 headless
  adapter after an update or daemon restore.
- CCB loads a provider-local, read-only Pi extension with `--extension`. The
  extension writes normalized lifecycle events to the agent runtime completion
  directory; it does not read or mutate credentials, provider configuration,
  or the user's login state.
- A pane job binds to its exact prompt digest or the matching request and
  one-use `CCB_DISPATCH_ID` header, plus the CCB actor, launch session, and
  live Pi process instance. The dispatch header survives image-editor input
  transformations; a request id alone is never sufficient. See
  [transformed-input binding](pi-transformed-input.md). The reader starts at the
  event-log byte offset captured before prompt delivery, so an earlier
  completion cannot satisfy a new job.
- `turn_end`, `agent_end`, tool results, retry events, and assistant text are
  progress evidence only. `agent_settled` is the sole successful terminal
  boundary because it follows Pi's automatic retry, compaction, and queued
  continuation handling.
- The terminal reply is the latest visible assistant text for the bound
  request. Thinking blocks and prior process narration are excluded.
- `stop` with non-empty text completes. `error` fails. `aborted`, `length`,
  missing outcomes, and empty settled replies are incomplete.
- A malformed complete sidecar record fails closed. A partially written
  trailing record remains pending until its newline is written.
- Unmanaged interactive/RPC input during a bound CCB turn supersedes that job
  as incomplete instead of letting the later manual reply be attributed to it.
- Pane death and prompt-delivery failure terminalize explicitly. Pane execution
  has no fixed wall-clock cutoff by default; an operator may opt into the
  existing semantic no-progress watchdog with
  `CCB_PI_NO_TERMINAL_TIMEOUT_S`.
- Cancellation sends Pi's native Escape interrupt only when lifecycle
  evidence identifies the exact active request and live instance. Unsent,
  settled, superseded, and unrelated turns receive no keys; Ctrl-C and editor
  clearing are avoided. Because both pane and headless Pi paths have native
  cancellation, CCB does not ask the model to probe a cancel-flag file before
  every step; this avoids the extra tool call and uncached token cost.
  Exported pane state omits backend objects and can rebind to the exact current
  launch session after daemon restore.

## Landing Sequence

1. Materialize the Pi lifecycle extension and event-log paths in launcher
   state/session payload.
2. Add a strict append-only event reader and terminal outcome mapper.
3. Add a composite adapter that routes new starts by configuration and all
   later operations by persisted mode.
4. Cover lifecycle, protocol corruption, stale attribution, mode migration,
   cancel, export/resume, pane liveness, and launcher projection in unit tests.
5. Run source tests only from `/home/bfly/yunwei/test_ccb2`.
6. With the user's existing Pi login, open a source-runtime project and verify
   that both the request and final reply are visible in Pi's pane while CCB
   returns exactly one matching completed reply.

## Acceptance Gates

- A realistic sequence containing tool use, retry/progress, final assistant
  text, and `agent_settled` produces one completed reply with only the final
  text.
- No `agent_settled` event cannot complete, including when session JSONL
  already contains a `stop` assistant message.
- Short replies complete; thinking-only or empty replies do not.
- `error`, `aborted`, and `length` map to failed/incomplete as frozen above.
- Old log content, foreign request ids, a different actor, or a different
  launch session cannot be attributed to the current job.
- A new unmanaged user input during a bound turn closes the CCB job as
  superseded and cannot become its terminal reply.
- A persisted 8.5.0 `pi_run` submission remains on the headless adapter.
- Pane state export/restore rebinds only when the current managed launch
  session matches; otherwise restoration requires safe resubmission.
- Existing Pi headless completion tests remain green through the rollback
  adapter, and OMP behavior is unchanged.
- Real authenticated acceptance records exact Pi version, source wrapper,
  project path, job id, trace outcome, visible pane evidence, and cleanup.

## Rollback

Set `CCB_PI_EXECUTION_MODE=headless` and restart the CCB backend. This changes
only where new Pi jobs execute. Existing pane and headless submissions continue
to be polled according to their persisted mode.

## Evidence

### Source and unit acceptance

- The PR #283 approach was reviewed but not cherry-picked. Its visible-pane
  dispatch direction was retained; session-JSONL terminal inference, the
  fixed 300-second timeout, non-persisted mode routing, and screen readiness
  heuristics were replaced by the frozen contract above.
- Focused Pi lifecycle/launcher/restore/control-plane suites passed, including
  realistic process/tool/retry/final/settled ordering, binding failures,
  unmanaged-input supersession, malformed/partial JSONL, mode migration,
  cancellation, and owner-only sidecar projection.
- Existing Pi/OMP headless, native CLI, provider catalog, runtime launch,
  execution-service reliability, and control-plane environment regressions
  passed in the combined source test lanes.
- Final focused matrix after strict dispatch-id validation:
  `324 passed`.
- Full repository suite:
  `6087 passed, 2 skipped, 4 subtests passed`; its sole failed case was the
  final Agent Roles smoke because the test process lacked
  `AGENT_ROLES_SPEC_HOME`. The exact case passed independently after supplying
  the local Agent Roles source (`1 passed`).
- `ruff check --select I,F` passes for the new Pi implementation and focused
  test modules. `git diff --check` passes.

### Authenticated Pi 0.82.1 acceptance

- Source wrapper:
  `/home/bfly/yunwei/ccb_source/ccb_test`
- External project:
  `/home/bfly/yunwei/test_ccb2/pi-visible-pane-20260729`
- Isolated source-test home:
  `/home/bfly/yunwei/test_ccb2/source_home`
- Inherited authentication source:
  `/home/bfly`
- Pi/model observed in the visible pane and session:
  `pi 0.82.1`, provider `openai-codex`, model `gpt-5.5`, thinking `medium`
- Final visible job:
  `job_fe14de081923`
  - pane displayed both the exact CCB request and `PI_FINAL_CODE_OK`
  - no model-facing cancel-file notice or cancellation-check tool call appeared
  - dispatch sidecar bound the request through unique
    `dispatch_id=9e60ab2ae730427b816fea580b372c3e`, exact prompt hash, actor,
    launch session, runtime instance, and request id
  - sidecar recorded
    `request_start(input_source=interactive) → agent_start → assistant_message
    → turn_end → agent_end → agent_settled`
  - trace recorded `reply_count=1`, `completed`, `pi_run_stop`, and the exact
    16-character reply
- Clear/reuse job:
  `job_3ac2c97ca8d7` completed once with `PI_AFTER_CLEAR_OK`.
- Explicit headless rollback job:
  `job_69f9d35307a7` completed once with `PI_HEADLESS_PATH_OK`; persisted
  diagnostics recorded `mode=pi_run` and
  `source_kind=structured_result_stream`, while the visible pane stayed idle.
- The source runtime was stopped with `ccb_test kill`; final state was
  `unmounted`.
