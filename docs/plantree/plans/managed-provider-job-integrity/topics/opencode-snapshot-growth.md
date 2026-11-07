# OpenCode snapshot recursion and observation starvation

Mode: status-update, 2026-11-07. Source repair and qualification implemented.
No active project or external Provider state is migrated by this work.

## Evidence and confidence

Issue #402 follow-up:
https://github.com/SeemSeam/claude_codex_bridge/issues/402#issuecomment-6611682188

Reporter measured 853 MB after seven small requests without excluding `.ccb/`;
after adding a local Git exclusion, 12/12 requests completed and the database
remained below 1 MB. Growth is measured; the exact causal path to their hung job
is not yet established. Their test used OpenCode 1.19.5, not the opt-in v2 API.

OpenCode v1.19.5 `packages/opencode/src/snapshot/index.ts` reads the source
repository's `info/exclude` through Git, copies those rules into its snapshot
repository, and applies `check-ignore --no-index` to candidate files. Local
exclusion is therefore relevant even to its separate snapshot repository.

Confirmed CCB defects from current source:

- `lib/opencode_runtime/storage.py`: each database query creates another full
  main+WAL copy. Changing file signatures or copy/query failures become empty
  rows, so absence cannot be distinguished from unavailable observation.
- Message, assistant part and parent part reads can use different snapshots.
  A single observation can combine inconsistent snapshots and repeat large I/O.
- `reply_polling_runtime/loop.py`: nonblocking polls scan only when the session
  timestamp changes. Forced reads are gated on `block`, and their timer is
  recreated per call. `refresh_observed_state` advances the session timestamp
  even when no reply was delivered and catches read errors.
- Isolated reproduction imported production `read_since`: initial scan returns
  no reply and advances session timestamp 9 -> 10; data then recovers with a
  completed reply while timestamp stays 10. Five further nonblocking calls
  invoke no reply reads and all return none (one scan total).
- Refresh can also absorb a newer assistant into the baseline without emitting
  it. A later equality comparison can suppress that result. This needs its own
  regression, independent of timestamp gating.
- `execution_phase.py` uses `provider_identity_mismatch` whenever current
  identity is not proven true, including unavailable evidence. This diagnostic
  alone does not prove a genuine session/identity change.

## Implemented repair boundaries

1. Prevent recursive source snapshots before Provider launch. Add an idempotent
   CCB-owned block to the actual Git `info/exclude`, resolved with
   `git rev-parse --git-path info/exclude` (resolve relative to the Git command
   directory and reject an exclude symlink before writing). Cover new and
   existing projects plus materialized/external workspaces; do not mutate Git
   metadata from generic read-only project resolution. Respect existing rules,
   worktree Git files, subdirectory roots, escaped names and local negations.
   Verify representative volatile files with `git check-ignore --no-index`.
   Prefer precise CCB volatile paths, preserving shareable `.ccb/ccb.config`
   and memory workflows. Blanket `.ccb/` remains a documented emergency
   workaround. Final pattern coverage must be inventoried against PathLayout.
   Already tracked runtime files need a warning; never auto-remove from index.
   Non-Git projects skip; an unwritable exclude emits an actionable warning.
2. Make polling recovery correct before optimizing. Preserve successful-read
   watermarks and last-emitted assistant state across failures. Separate empty
   data from unavailable/unstable/corrupt snapshots. Persist a monotonic forced
   scan clock across nonblocking calls. An unchanged session timestamp must not
   permanently suppress updates to messages/parts. Never consume a completed
   result into a baseline without emitting it to the existing job exactly once.
   Retain exact session, parent input and request-anchor validation.
3. Use one validated private snapshot/transaction per observation cycle for
   session/messages/parts; reduce repeated full copies. Avoid parsing large
   unused summary payloads where a verified legacy schema supports projection.
   Consider metadata-keyed snapshot reuse only after failure recovery is sound;
   inode/WAL changes and reset must invalidate it. Bound bytes, disk space,
   duration and retry/backoff; report exhausted observation budgets explicitly.
   No silent DB-to-file fallback on read errors, no replay, and no direct
   SQLite connection to the external live DB/WAL/SHM.
4. Separate unavailable identity evidence from actual mismatches in diagnostics.
   Provide sanitized read-failure reason, last successful observation and next
   retry. A storage budget failure is incomplete observation, not successful
   completion and not proof the Provider stopped. Keep native API opt-in as an
   alternative; native APIs alone do not prevent recursive Provider snapshots.

## Acceptance and limits

- Git fixtures: new/existing repo, subdirectory anchor, worktree, pre-existing
  rules, tracked config, tracked runtime, non-Git and unwritable exclude.
- Isolated real 1.19.5 service + local model in a Git project, at least 20
  sequential requests. Confirm volatile paths do not appear in summaries,
  database/snapshot growth remains bounded and no new SIGBUS/SHM mutations.
- Fault injection: unstable/copy/query failure followed by recovery with an
  unchanged session timestamp; message/part updates without session updates;
  completion racing baseline refresh; exactly one terminal reply, no replay.
- Large synthetic database and WAL: count bytes/copies per poll; assert bounded
  failure is visible and subsequent reads can recover without a new job.
- Preserve v1/native-v2, cancellation, foreign input and external-state
  ownership regressions; install acceptance before an authorized commit.
- Do not delete, VACUUM, checkpoint or rewrite existing Provider databases or
  snapshot histories. Exclusion prevents new recursion but does not reclaim
  existing growth. Reporter-specific hang attribution still needs redacted
  completion/runtime evidence; do not collect credentials or raw DB dumps.

## Qualification and remaining work

Source regression: 318 passed. Real OpenCode 1.19.5, isolated Git repository,
managed-style runtime paths and a local SSE model: 20/20 exactly attributed
replies, no runtime paths in summary diffs, maximum DB+WAL 4,607,648 bytes.
A deliberately failed copy recovered against the same request without replay.
The real 2.2.4 native smoke also passed. These are Linux fixture tests, not the
reporter's Arch host, a cloud model or a Mobile VM acceptance claim.

Installed qualification and artifacts are tracked in
[the evidence record](../evidence/opencode-snapshot-growth-20261107.md).
No stable installation switch, publication or issue response is included.
