# Screen Capture And Caller Inspection Guidance

Date: 2026-09-19
Role: evidence
Status: source implemented; isolated live pane capture verified
Related: [roadmap](../roadmap.md), [notice contract](../topics/empty-result-caller-notice.md)

## Implementation

- Added `ccb screen <agent> [--lines 0..1000] [--json]` via the normal CLI
  parser, model, service, help, and dispatch surfaces. Default captures visible
  text; `--lines N` adds up to N scrollback lines. ANSI is removed and layout
  preserved. Metadata includes project, agent, pane, timestamp, history bound.
- The service connects only to an existing mounted daemon, reads ProjectView,
  selects its exact tmux socket, and verifies pane project/agent/epoch/role and
  managed ownership. It rechecks pane identity (including session) and graph
  binding after capture. Capture is bounded to two seconds. Missing/dead/foreign
  or changed bindings fail with no text, no keys, no focus change, no log fallback.
- Empty-result notice now supplies concrete screen/trace/logs commands and
  caller-owned choices. FAILED/INCOMPLETE replies retain their factual body and
  receive one shared inspection section. No new automatic reactivation, resend,
  or semantic cause classification was added. Existing API/runtime retries remain.
- Known pure-empty reasons from OMP/Pi/Cursor/Grok now join Codex/Claude at the
  no-auto-retry boundary even if a generic `delivery_retryable` flag is set.
- Cancel, successful silence, delegated parent, chain child, and internal back
  exclusions remain at the existing finalization boundaries.

## Deterministic Verification

Command prefix: `PYTHONPATH=lib uv run --with pytest --with pytest-asyncio --no-project python -m pytest -q`

Files:

```text
test/test_cli_screen.py
test/test_caller_inspection_guidance.py
test/test_v2_cli_parser.py
test/test_v2_cli_router.py
test/test_v2_phase2_wiring.py
test/test_terminal_runtime_tmux_panes.py
test/test_ccbd_retry_failure_detail.py
test/test_v2_message_bureau_dispatcher_integration.py
test/test_v2_ccbd_dispatcher.py
test/test_unified_message_fifo.py
test/test_reply_delivery_polling_gate.py
```

Coverage includes parser bounds, JSON/plain output, exact socket selection,
dead/foreign/changed/unbound panes, unavailable daemon, capture error/timeout,
visible versus history tmux arguments, ANSI/layout handling, abnormal payload
preservation and idempotence, chain/cancel/silence exclusions, quota failure
and empty-notice persistence/delivery, and provider-empty retry bypass.

Result: **356 passed**. `git diff --check` and compilation of the new/changed
CLI, capture, and finalization modules also passed.

## Live Verification And Limits

Used source `ccb_test` from `/var/tmp/ccb-real-fifo-ukpp4c/project`, with that
external path explicitly allowed by `CCB_TEST_ROOTS` and inherited caller
variables removed. The isolated installation's Python supplies runtime
dependencies (the shell's default Python lacks aiohttp).

At 02:58 UTC, against the retained independent installed daemon:

- `screen cx --json`: exit 0; real Codex pane `%2`, 288 text characters.
- `screen om --lines 120 --json`: exit 0; real OMP pane `%3`, 1836 characters.
- `screen cx`: exit 0; readable metadata and current screen.
- `screen unknown --json`: exit 1; unknown-agent error, no screen text.
- `screen --help`: exit 0; new command contract displayed.

No provider prompts, keys, or daemon restart were needed. The running daemon
and default installed CLI were not updated in this slice; new reply rendering
requires deployment/restart through the normal installation workflow. No claim
of real network/quota/model-switch fault injection is made. The earlier OMP
native empty-continuation qualification gap remains open. No commit or push.
