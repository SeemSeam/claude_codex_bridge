# OpenCode snapshot-growth repair qualification

Date: 2026-11-07. Scope: issue #402 follow-up; local source, preview installation
and disposable Linux Provider fixtures. No publication or issue response.

## Repair

- Managed OpenCode launch protects volatile `.ccb` paths through local Git
  exclusions, including nested projects and linked worktrees. Existing rules,
  tracked config/memory and index entries are preserved; unsafe writes and
  overridden rules emit sanitized warnings.
- Legacy storage uses one private main+WAL copy per observation, bounded to
  256 MiB total and a cooperative two-second copy budget. SQL queries have
  individual two-second progress-handler budgets and omit message summaries.
  No SQLite connection or shared-memory access targets the live Provider DB.
- Read failure preserves the successful cursor; backoff and forced nonblocking
  scans allow recovery without session timestamp changes. A second refresh
  cannot absorb an undelivered reply. A recovery-only diagnostic change is
  persisted even without new completion items.
- Continuous observation failure reaches an explicit incomplete decision after
  30 seconds, never automatic replay or a claim that the Provider stopped.
  Missing identity evidence has a separate reason from proven mismatch.

Contracts: [OpenCode](../../../../opencode-completion-contract.md) and
[diagnostics](../../../../ccbd-diagnostics-contract.md).

## Automated regression

Source and installed regression commands use:

```sh
python -m pytest -q test/test_opencode*.py test/test_execution_phase.py \
  test/test_ccbd_project_view*.py test/test_v2_provider_restore_launchers.py \
  test/test_provider_core_memory_projection.py test/test_native_cli_providers.py \
  test/test_provider_execution_service_runtime.py
```

Source: **318 passed in 18.81s**. Final installed application:
**318 passed in 18.75s**.
Fault fixtures cover copy failure/recovery, completion without a session update,
query/copy budgets, a sparse 257 MiB input rejected before copying, single-copy
observation consistency, same-signature backoff and periodic revalidation,
exactly-once reply emission, continuous failure and idle recovery diagnostics.
Git fixtures cover nested escaped paths, worktrees, existing rules, tracked
runtime/config/memory, non-Git paths, failed writes, locks, symlinks and negation.

## Real Provider qualification

`test/opencode_legacy_growth_smoke.py` launches actual OpenCode 1.19.5 with
snapshotting enabled, a temporary Git source project and local SSE model.
Provider HOME/XDG, database, logs and snapshots reside under the fixture's
`.ccb/agents/fixture/provider-state/opencode` tree. Each of 20 sequential inputs
changes a volatile runtime log. Native HTTP submits the prompt; production
legacy SQLite-private-copy observation attributes and completes each reply.
One copy failure is injected during request 8 and recovers on that same job.

- Source run: 20/20 exact replies; no volatile runtime paths in native summary
  diffs; maximum DB+WAL 4,607,648 bytes; observed completion 0.061–1.028 seconds.
- Final installed run: 20/20 exact replies; no runtime diffs; maximum DB+WAL
  4,611,744 bytes; observed completion 0.051–0.701 seconds.
- Real OpenCode 2.2.4 native smoke passed in source and preview installation:
  queued admission, exact input binding, held live stream, queued cancellation,
  no early completion and native idle success. Both use a local model fixture.
- Existing v1 native control smoke also passed before installation.

These are real Provider processes with fixture model responses, not cloud-model,
reporter Arch-host, full tmux/ask business-chain or Mobile VM acceptance.
The #402 reporter's specific hang-to-growth causal path remains unproven.

## Local installation and boundaries

Preview root: `/home/bfly/.local/share/ccb-previews/opencode-growth-20261107`.
Built through `scripts/build_linux_release.py --allow-dirty --git-ref v8.7.21`;
installed through its packaged `install.sh` with isolated HOME/XDG/CODEX_HOME,
application and executable prefixes. Role/watchdog/Provider auto-install is off.
Version remains 8.7.21, channel/source_kind `preview`; the base tag identity is
not a release of these dirty-worktree changes. Existing unrelated local edits
are present in this preview snapshot and remain outside this repair's commit.

Stable `~/.local/bin/ccb` and mounted business agents are not switched or
restarted. No external Provider authentication or databases are read or changed.
Exclusion does not shrink old DBs; no deletion, VACUUM or checkpoint is performed.

## Final receipt

- Final artifact SHA256: `87e408ee027e8822580d7cdc9921754ecc013d3290821636844c2b676a0fdb56`.
- All ten production files and the recovery tests compare byte-for-byte with
  the installed application. Installed `ccb --print-version`: `v8.7.21`.
- Real installed v1 and v2 smokes passed as described above.
- Source compilation and `git diff --check` passed.
- Local logs: `/tmp/ccb-opencode-growth-final-tests.log`,
  `/tmp/ccb-opencode-growth-installed-tests.log`,
  `/tmp/ccb-opencode-growth-installed-real.log`,
  `/tmp/ccb-opencode-growth-installed-v2.log`,
  `/tmp/ccb-opencode-growth-build.log`, `/tmp/ccb-opencode-growth-install.log`.
  Logs and preview artifacts are local operational receipts, not tracked assets.
