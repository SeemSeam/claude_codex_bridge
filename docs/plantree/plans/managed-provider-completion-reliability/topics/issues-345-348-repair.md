# Issues 345–348 Repair Plan

Date: 2026-09-14
Role: implementation plan
Status: implemented locally; awaiting agent1 review and issue replies
Authority: [Core maintenance gates](../../../baseline/test-and-release-gates.md)
Evidence: [Issues 345-348 repair evidence](../../../baseline/evidence/issues-345-348-repair-20260914.md)

## Scope And Ownership

Repair Codex terminal error classification, remote resume / clear reporting,
managed app-server lifecycle, and Kimi timeout environment propagation.
Baseline inspected: main `20ac0ec58`. Preserve unrelated working-tree changes,
including PR349/350 review documents and `.zai/`. Do not merge those PRs.
External Provider login/configuration remains one-way inheritance input.
Do not modify external credentials, weaken permission policy, bypass provider
safety stops, or introduce Windows/Herdr dependencies into shared runtime.

## Ordered Repair Slices And Acceptance

1. **#347: authoritative terminal errors.** Event normalization currently drops
   `task_complete.error` and terminal handling emits a normal boundary.
   Preserve the error category/message through normalized terminal evidence,
   execution finalization, detector, persisted result and reply delivery.
   A provider error must produce unsuccessful terminal status and cannot use
   earlier progress as a successful handback. Do not retry, switch models or
   resubmit to bypass a safety stop. Use synthetic event fixtures only.
   Test progress followed by an error with null/missing final text, generic and
   safety error categories, genuine successful completion, empty completion,
   and correct task/turn binding. Cover the integrated completion path.

2. **#346: compatible remote resume and honest clear.** Generated remote resume
   currently retains permission override flags. Determine the CLI contract and
   keep the requested permission policy effective: do not blindly strip a
   restrictive policy. Prefer a verified compatible launch path; if remote
   resume cannot honor the policy, select supported local resume or fail with
   an actionable diagnostic. Test fresh/remote/local/restore paths, explicit
   startup args, restricted roles, auto-permission, and supported old CLI paths.
   `clear` currently reports success after sending input to an existing pane.
   Reject dead/unavailable providers and distinguish input delivery from a
   confirmed context reset. Cover retained dead panes, missing panes, transport
   failure, busy jobs, and successful provider-native reset where observable.

3. **#345: lifecycle fencing.** The issue's exact incident is not yet reproduced.
   Startup checks only socket existence; cleanup unlinks shared artifact paths
   without generation ownership. Build a deterministic fake app-server / real
   Unix-socket reproduction for old/new overlap before implementing a bounded
   fix. Fence socket/PID/marker cleanup to the owning generation and establish
   readiness beyond stale path existence. Test delayed readiness, early exit,
   old-generation cleanup after replacement, restart overlap, timeout/fallback,
   and no duplicate prompt submission. Preserve bounded recovery circuits;
   do not hide persistent failure with unlimited retries. Record which incident
   mechanisms were reproduced and any macOS-only verification gap.

4. **#348: propagate the existing timeout knob.** Offline inspection confirms
   `control_plane_env` removes `CCB_KIMI_NATIVE_TURN_TIMEOUT_S`. Add this exact
   setting through the daemon environment path without broadening the allowlist
   to arbitrary Provider variables. Test configured/default/invalid values,
   daemon environment construction, a synthetic long turn within the configured
   budget, and expiration after that budget. Use a fake clock, not a five-minute
   sleep. Activity-based inactivity timeouts are deferred: this patch restores
   the existing knob and must not claim it changes the default 300-second cap.

## Verification And Handoff

### Agent1 Review: Remaining Release Blockers (2026-09-14)

The first agent3 handoff is not accepted for publication yet. Independent
synthetic checks reproduced all of the following on the submitted candidate:

- Shutdown cleanup deletes a live successor's PID and remote marker before
  deciding to retain its socket. Preserve the entire successor-owned artifact
  set, with consistent synchronization across start/stop/shutdown cleanup.
- Failed-start `_restore_pid_record` overwrites a live successor record with an
  older snapshot. A failed generation must not restore over another owner.
- Concurrent fresh starts have no serialized claim; a fresh socket inode and
  a still-alive child do not prove the child owns that endpoint. Fence the
  generation claim and cleanup, and test overlapping first starts as well as
  restart overlap. The pane's `-S` check also needs examination against stale
  generation attachment; supervisor-only tests do not cover pane selection.
- Remote-resume compatibility misses `--sandbox=read-only` and other supported
  permission flag forms. Account for short aliases, attached values and config
  overrides without treating option values as subcommands or dropping policy.
- Clear's new `confirmed: input_delivered` field is not shown by `render_clear`;
  CLI users still see only `status=cleared`. Surface the limited confirmation.
- Complete the short-socket test currently deselected for `/tmp` inode pressure
  using an isolated suitable temporary root or repairing the test's hardcoded
  temp-root assumption without weakening its long-path assertion.

Agent3 should repair these findings and rerun affected gates before agent1
continues with the already authorized commit and v8.6.17 release.

Use isolated test state and provider stubs; no running user agents are restarted.
`/tmp` inodes are exhausted on this host: use a fresh `mktemp -d /var/tmp/...`
for TMPDIR. System Python lacks pytest; an isolated uv environment with pytest,
cryptography and aiohttp was usable. Resolve additional dependencies from the
repository's CI definitions as needed; do not alter the user's Provider env.

Require new reproductions to fail before and pass after the fixes, then relevant
Codex/Kimi, completion, clear/control-plane and lifecycle regressions. Run the
trusted-main Windows isolation gate and `git diff --check`. Broaden tests for
shared detector/clear changes. Record exact commands, counts, failures, platform
limits and files in a linked evidence record. Tests must not weaken assertions
or classify unrelated failures as passing.

Agent3 hands back local changes and evidence to agent1 for review. Its delegated
scope remains implementation and testing, without publication actions.

Owner authorization added 2026-09-14: after all tests and review pass, agent1
commits the reviewed repairs and publishes the next patch release. Current
GitHub and npm latest are `8.6.16`; candidate is `8.6.17`, subject to a fresh
tag/registry collision check. No further routine publication approval is needed.
Keep repair commits separate from global release metadata. Publish from an
isolated clean worktree, excluding `.zai/` and unrelated changes. Synchronize
version references and Mobile build number, write bilingual release notes,
run release identity/package gates, then push main and the immutable tag.
Track npm, Linux/macOS, Android, Windows and Sidebar workflows to completion;
verify remote tag identity, GitHub assets/checksums and exact npm installation.
After passing tests, agent1 posts accurate issue replies with the fix scope,
verification and remaining limitations; link the release once verified.
Local-only fixes must not be described as released. Preserve rollback through
separately identifiable repair slices; no external Provider state migration
is permitted. Do not publish while required tests or material review findings
remain unresolved; report precise external blockers if they cannot be resolved.
