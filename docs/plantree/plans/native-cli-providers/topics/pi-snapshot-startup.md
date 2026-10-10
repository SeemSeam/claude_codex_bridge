# Pi package snapshot startup repair

Date: 2026-10-10
Mode: status-update (implemented and tested in isolated worktree; uncommitted)

## Scope and acceptance

User requested importing OMP sources into system Pi, then improving and testing
the reported Pi startup defects. Native source import is explicit user-directed
configuration work, not CCB reverse-management. Three configured sources passed
real Pi completion; easyfun discovery returned HTTP 401.

For local package snapshots, resolve a graph keyed by canonical installed
package directory (never name/version alone). Copy each node once; link repeated
edges within the immutable snapshot. Preserve nested dependency paths, cycles,
scoped names, optional dependencies, and distinct physical versions.

Hash selected source payloads and resolved edges before staging. Under a
category-wide build lock, verify a cached output against its recorded digest;
never accept a corrupted bundle. Recheck source fingerprints after copying.
Publish the complete bundle plus receipt atomically. New versioned staging
folders are covered by the same lock and may be reclaimed by the next builder;
legacy unowned folders are not automatically deleted.

Add opt-in `.ccb-snapshot-exclude` containing relative paths (one per line,
no glob/negation semantics). Do not apply `.gitignore` implicitly. Keep package
metadata and the exclusion control file mandatory. No source writes.

Pass `CCB_STARTUP_TRANSACTION_TIMEOUT_S` across control-plane environment
filters. Do not increase the default deadline. The incident's EOF cause remains
unproven; changing generic socket scheduling is outside this bounded fix.

## Verification

Regression tests: warm reuse performs zero copies; content changes without
mtime/size changes invalidate; tampering fails; shared dependency executes once;
cycles, multiple physical versions, scoped and optional dependencies resolve;
explicit exclusion preserves runtime outputs; interrupted builds recover;
concurrent builders reuse one final bundle. Run existing provider snapshot,
projected-asset and environment suites, then real Pi against the candidate.

## Rollback

Source changes remain isolated and uninstalled. New snapshots use a distinct
versioned namespace, so existing snapshots and active agents stay valid.
Native Pi models have an owner-only pre-edit backup alongside models.json.

## Results

[Verification evidence](../evidence/pi-snapshot-startup-20261010.md): 362 tests
passed; three native Pi sources, real SDK/Zod loading, and two-agent cold/warm
startup passed. Easyfun HTTP 401 and original macOS EOF qualification remain open.
