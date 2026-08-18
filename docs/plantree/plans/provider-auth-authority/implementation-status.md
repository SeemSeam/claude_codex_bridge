# Provider Authentication Authority Implementation Status

Date: 2026-08-18

## Current Phase

The v8.5.5 compatibility repair and the continuous-inheritance implementation
are committed and published on `origin/main` for Codex, Claude, and Gemini as
CCB v8.5.6 source commit
[`8b35d868`](https://github.com/SeemSeam/claude_codex_bridge/commit/8b35d868f402e5f68929782a6c2df657a8750d21).
The reviewed release state is tagged at
[`58b49c12`](https://github.com/SeemSeam/claude_codex_bridge/commit/58b49c121f4e162f74cf6d15be608e89745f9166)
and published through the bilingual
[`v8.5.6` GitHub Release](https://github.com/SeemSeam/claude_codex_bridge/releases/tag/v8.5.6)
and npm package `@seemseam/ccb@8.5.6`.
Explicit CCB
authority wins per configured dimension; otherwise a newly prepared, stopped
Provider generation reads the current external state into a private projection.
The source boundary is one-way, and a change of authority increments CCB's
generation without clearing the stable conversation, workspace, queue, session
record, or transcript history.

Native resume remains capability-gated. A qualified fork/import is used when
the installed CLI supports it; an unqualified or unsafe binding is retained as
linked continuation evidence instead of being hidden from history.

## Last Landed

- Issue #319 macOS Claude OAuth re-login repair was published in CCB `v8.6.10`
  from implementation commit `84f2bc73c` and release commit `705c932ec`, with
  annotated tag `v8.6.10`, GitHub Release assets, and npm
  `@seemseam/ccb@8.6.10`. On stopped restart, CCB compares the inherited source
  payload with the prior CCB-owned `.credentials.json` projection and updates
  only the matching Agent-derived Keychain service when the source changed. An
  unchanged source preserves a managed Claude refresh performed in that private
  service. External Claude Keychain services remain read-only; symlinked
  projections and private Keychain inspection errors fail closed.
- v8.5.5 was withdrawn from GitHub on 2026-08-05 after its first startup moved
  all pre-HMAC Codex sessions out of the active namespace.
- Codex now adopts compatible pre-HMAC namespaces in place and restores the
  exact v8.5.5 `*-global`/matching-legacy-route archive shape when old binding
  evidence proves ownership.
- Recovery preserves a newer current binding while returning archived
  transcripts to native `resume`; Agents without a newer binding recover the
  old current binding.
- Claude and Gemini allow one compatible legacy continuation before persisting
  the new fingerprint; explicit mismatches still start fresh.
- `ccb restart <agent>` no longer executes persisted `session.start_cmd`.
- Codex API/login authority changes retain the active `sessions/` namespace,
  remove only the stale native binding, and record a linked continuation while
  leaving credentials and transcripts untouched.
- Claude/Gemini API, route, or login authority changes suppress incompatible
  native `--continue`/`--resume latest`, then use fork/import when supported or
  retain a linked continuation without deleting private auth or history.
- Session records now carry a stable `ccb_conversation_id`, authority
  generation/history, parent linkage, and explicit resume compatibility.
- Codex binding completion now replaces `pending_native_binding` with durable
  managed-history or proven native-fork compatibility, including idempotent
  repair when the native id and path were already present.
- Bundled `codex-reconnect` is installed and automatically armed after a
  managed Codex thread binds; capacity/network recovery remains bounded to one
  exact-thread `continue`.
- The v8.5.6 source is installed locally in source/dev mode. The global
  commands report CCB `8.5.6` from the source line containing implementation
  commit `8b35d868` and bundled `codex-reconnect 0.3.5`.
- Tag `v8.5.6` points to release commit `58b49c12`. Linux, macOS universal,
  signed Android, checksum, and bilingual GitHub Release publication passed;
  npm Trusted Publishing succeeded and `latest` resolves to `8.5.6`.

## Active TODO

1. Complete organic real network/capacity qualification for `codex-reconnect`;
   deterministic fixtures must remain the only pressure-safe fallback.
2. Close the writable-home boundary for arbitrary `provider_profile.home`.
3. Continue capability qualification and writer-lease enforcement for providers
   beyond the current Codex/Claude/Gemini slice.

## Blocked By

Organic Provider fault qualification, broader rotating-OAuth enforcement, and
native capability coverage remain gated by external Provider behavior and
private-login decisions. An unqualified Provider must use linked continuation
rather than block local history continuity.

## Last Verified

- Issue #319 regression tests: `4 passed`.
- Provider/restart/authority/storage regression matrix: `406 passed`.
- Full source suite in the qualification environment: `7170 passed, 3
  skipped, 4 subtests passed` (the initial 27 environment-only failures passed
  when rerun with the qualification interpreter and `aiohttp` on `PATH`).
- `py_compile` and `git diff --check` passed for the current local patch.
- Real macOS Keychain qualification is still unavailable on this Linux host;
  the macOS behavior remains covered by deterministic fakes and remains a
  platform-owner qualification item after publication.
- v8.6.10 release verification: Release Artifacts run `32123813089`, Native
  Windows run `32123813069`, Release Sidebar run `32123813053`, and npm OIDC
  run `32123813131` all completed successfully. The public release contains
  Linux/macOS, Windows, Mobile, sidebar, and checksum assets; npm `latest`
  resolves to `8.6.10`.

- `/home/bfly/yunwei/ccb_source/ccb_test --diagnose` passed from the external
  `/home/bfly/yunwei/test_ccb2` project root.
- Focused authority, restore, launch, and reconnect integration tests: `189
  passed` from the external qualification pytest environment.
- A later consolidated focused regression run passed `294` tests.
- `python3 -m compileall` for the touched Python modules and `git diff --check`
  passed.
- Full source pytest run from the source root with the qualification dependency
  environment: `6188 passed, 2 skipped, 4 subtests passed`.
- External source-runtime project
  `reconnect-autostart-live-20260805-01` advanced from inherited authority
  generation 1 to 2 without changing `ccb_conversation_id`, retained both
  transcript files with matching user-message hashes, resumed the same current
  Codex thread on a same-authority Agent restart, created no archive, armed
  reconnect automatically, and shut down to `unmounted` with reconnect `off`
  and no project process residue.
- Implementation commit `8b35d868f402e5f68929782a6c2df657a8750d21`
  was independently confirmed on GitHub `main`; the local source/dev
  installation reported CCB `8.5.6` and `codex-reconnect 0.3.5` from the
  external test root.
- Release Artifacts run `31060954687` and npm OIDC run `31060954935` completed
  successfully. Public Release assets include `SHA256SUMS`, Linux and macOS
  archives, and the signed CCB Mobile APK/manifest.
