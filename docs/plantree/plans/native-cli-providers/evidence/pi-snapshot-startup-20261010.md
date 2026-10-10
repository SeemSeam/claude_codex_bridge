# Pi source migration and package snapshot qualification

Date: 2026-10-10
Status: source verified in isolated worktree; prepared for authorized commit and release.
Base: v8.7.9 (`3f58126188687aa67babf544d118d5f9a0978885`).
Worktree: `/var/tmp/ccb-pi-snapshot-startup-20261010`.

## User-directed native source import

System Pi `~/.pi/agent/models.json` received OMP's four custom source
configurations. Existing models and system Pi settings were preserved. Chat
catalog contains paratera 4 entries (including the existing legacy model),
paratera2 19, bingxing 74. Non-chat image/video/embedding/etc. models were
excluded. Easyfun models discovery returned HTTP 401; its source is configured
but has no verified catalog and is not qualified as working.

Owner-only native configuration backup:
`/home/bfly/.pi/agent/models.json.bak-omp-import-20261010-180655`.
No credential values are included in this evidence or source tree.

Actual Pi 1.1.0 print-mode completions all returned `PI_SOURCE_OK`:

| Source/model | Result | Elapsed |
| --- | --- | --- |
| paratera/gpt-5.6-sol | passed | 17.79 s |
| paratera2/grok-4.20-non-reasoning | passed | 4.83 s |
| bingxing/GLM-4-Flash | passed | 3.12 s |

These are representative source smoke tests, not qualification of every model.
Results: `/var/tmp/ccb-pi-source-smoke-result.json`.

## Source fix

- Resolve local dependency graph by canonical installed directory; retain
  physical/version isolation, scoped paths, optional deps, and cycles.
- Copy each graph node once and use snapshot-internal relative symlinks for
  repeated edges; no links back into the external installation.
- Fingerprint selected payload content and resolved edges before creating any
  staging tree. Warm lookup verifies the full cached output against its receipt.
- Recheck copied payload, source payload, and existing resolution edges before
  atomic publication. Changed source or corrupt cache fails closed.
- Serialize graph-v1 builders under an OS-released lock. On next build, reclaim
  abandoned graph-v1 staging directories; preserve old unowned staging and
  existing final snapshots.
- Support opt-in `.ccb-snapshot-exclude` literal relative paths; do not infer
  runtime exclusions from `.gitignore`.
- Forward `CCB_STARTUP_TRANSACTION_TIMEOUT_S` through CLI/keeper/daemon env
  filtering, retaining the default 30-second policy.

## Verification

362 tests passed in 72.30 seconds across package snapshots, native providers,
projected assets, runtime environment, Pi history/resume/pane/completion,
native execution, startup fences, keeper and daemon startup waits.
New cases include zero-copy warm reuse, same-size/same-mtime content changes,
tamper rejection, actual Node shared module identity and physical isolation,
explicit exclusions, concurrent builders, hard process exit with staging
recovery, source mutation during copy, and two-hop timeout policy parsing.
`git diff --check` passed.

Real fixture installed `@modelcontextprotocol/sdk@1.29.0` and `zod@4.4.3`,
with a 1 MiB excluded backup. Same source tested against release and candidate:

| Measurement | v8.7.9 | Candidate |
| --- | ---: | ---: |
| Cold snapshot build | 5.197 s | 0.756 s |
| Warm snapshot lookup | 3.084 s | 0.379 s |
| es-errors physical instances | 88 | 1 |
| Selected snapshot file bytes | 32,335,861 | 15,517,957 |

Single-run local measurements, not cross-platform performance guarantees.
The original report's 71 MiB backup was not reproduced; fixture backup is 1 MiB.
Pi loaded the resulting extension, imported SDK Client and Zod successfully,
and returned `PI_SNAPSHOT_OK`. Extension event evidence recorded
`{"sdk":"function","zod":"loaded"}`.

The first CCB startup harness used an environment-only marker path, which the
control-plane filter omitted. Model completions succeeded but the fixture's
session-start callback failed; the subsequent run could also match historical
assistant text. Those initial runs are not extension-startup qualification.
The corrected fixture writes a PID-scoped marker directly into its disposable
evidence directory. Each startup prompt has a unique run marker, and acceptance
requires a current assistant message with stopReason=stop plus separate SDK/Zod
session-start evidence for both actors. Cold startup in `startup-0b0a14be`
passed (3.98 s, marker `f2baf8c6`); both agents referenced the same snapshot,
produced current native results and valid extension callbacks. Control-plane
ping, ps and queue checks passed, and cleanup via its own `ccb kill` returned 0.
The corrected warm startup also passed (3.37 s, marker `937e08ef`), including
both PID-scoped extension callbacks and fresh terminal assistant messages.
Existing user agents were not restarted.

Raw local evidence:
`/var/tmp/ccb-pi-snapshot-real-20261010/` (results.json, baseline-results.json,
startup-cold-results.json, startup-06ca90d6/results.json and CLI logs).

## Limits

Linux validation only; the original macOS empty-response incident is not
reproduced or proven fixed. Generic socket queue scheduling is unchanged.
Legacy orphan directories without ownership evidence are not auto-deleted.
Easyfun's 401 requires valid source-side access before a live model catalog or
completion can be qualified. Full release qualification is recorded separately; this source evidence alone
does not claim publication.
