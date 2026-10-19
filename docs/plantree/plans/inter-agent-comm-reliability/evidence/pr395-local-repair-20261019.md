# PR #395 local integration and repair

Date: 2026-10-19
Mode: execute-ready; owner authorized saving existing changes, pulling the PR,
testing, repairing, and committing locally. No push or publication is included.

## Source and boundaries

- Original local source: `567cecc90` (v8.7.11).
- Existing README and Mobile planning edits saved as `ab71a34e2`.
- PR #395 head: `da91247f86596782648c01b6129d0194b42e4a88`.
- Remote main / PR base: `277a8d3a6fdc61bfadc965b48bf70fb561e277e4`,
  including the already merged PR #384.
- PR #395 remains open remotely; this is owner-authorized local integration.
- Local `.zai/settings.json` is excluded. Provider configuration/authentication,
  installed agents, version metadata and release artifacts are not changed.

## Reproduced failures and correction

The imported watchdog cleared the head's delivery job reference but retained a
conflicting lease. Subsequent ticks could not deliver and each stall threshold
created another pending job. Even after manual lease removal and replacement
completion, superseded jobs remained queued.

The correction retains the original job and attempt. After 300 seconds, an idle
target with no execution evidence can release an acquired conflicting lease only
when its event is missing or terminal. Live queued/delivering events retain their
lease. A matching stranded lease can be reset without replacing the job. Pending
jobs without a lease need no rewrite and continue through normal scheduling.
Event-store or execution-state read failures, recorded starts, persisted execution,
live execution and another active target job all prevent automatic intervention.

Claude's expanded border regex rejected plain horizontal borders with trailing
spaces. Restored padding support for both plain and titled borders, with empty
and human-draft cases. Also restored the separate instructional-preview test
function that the imported callback test had accidentally absorbed.

## Verification

Interpreter: `/home/bfly/.local/share/codex-dual/.venv/bin/python` (Python 3.14.4).

- Regression tests before source correction: **15 failed, 77 passed**; failures
  cover lease recovery, execution uncertainty and border padding.
- Final related suites: **634 passed, 28 skipped**, 7.11 seconds.
- Covered suites: project view, pending-stall repair, Codex status observation,
  model independence, draft guard/FIFO, unified message FIFO, reply start and
  polling, Claude suggestions, Codex replies, mailbox lease/kernel,
  dispatcher/message-bureau integration, ask service/retry lineage, and platform
  maintenance/config/terminal/isolation checks.
- End-to-end dispatcher regression: original delivery reaches RUNNING, completes,
  mailbox and queue drain, attempt completes, and no replacement job is created.
  Restart cases preserve job identity with and without a matching lease.
- Isolation checker extracted from trusted pre-pull `567cecc90`: **PASS** for
  `567cecc90..277a8d3a6` (platform-owned scope) and
  `277a8d3a6..da91247f8` (shared scope). Shared reverse dependencies remain
  within the existing debt ceiling.
- `git diff --check`: passed.

The 28 skips require native platform transactions or a platform-specific shell.
No new native platform or real-provider acceptance is claimed. Remote PR CI had 12 passing
checks before local repairs; it does not validate this local follow-up. Next:
qualify the final candidate on required native platforms before later publication.
