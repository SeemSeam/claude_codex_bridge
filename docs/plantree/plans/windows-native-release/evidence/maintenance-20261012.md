# Native Windows maintenance qualification — 2026-10-12

Base: main `636afa2f` (v8.7.10). Tested runtime and acceptance tooling:
`7cd23cdc`; the following evidence-only commit does not change that payload.

## Changes

- Windows installer/uninstaller settings transactions use strict UTF-8, validate
  object/permission shapes, reject malformed input before install work, preserve
  deep JSON, create exact backups and atomically replace files without BOM.
  Changes detected since the read abort rather than overwrite another editor.
- Native interactive startup inspects its actual WezTerm ancestor. Older,
  mismatched and unverified ConPTY pairs are refused before startup/attachment.
  Explicit inspection is available through `ccb windows-terminal-check --json`.
- The standalone Windows updater verifies official release checksums, stages a
  candidate, refuses active installations, checks native EXE execution before and
  after switching, and preserves configuration/venv/user files. It supports
  rollback and explicit interrupted-switch recovery. Existing archive installs
  route through this transaction; shared `ccb update` remains unchanged.

## Native qualification

- Windows/Herdr/rmux/WezTerm Python tests: 533 passed, 2 platform skips.
- Additional shared foreground regression tests: 18 passed. Shared production
  code was not changed. Total: 551 passed, 2 skipped.
- Native Rust launcher tests: 2 passed. Full Windows archive built with the local
  Windows GNU Rust toolchain and its LLVM linker; no WSL was used. MSVC was not
  available locally; the existing hosted release lane retains its MSVC build.
- 63 distinct in-scope acceptance checks passed: startup/reconnect/concurrent
  opens, Codex real request, foreground UI, scoped terminal input, Edge config UI,
  config save/restore, explicit failures, project stop/concurrent reopen, real
  upgrade/rollback/recovery, installer update dispatch, download/checksum and
  installed entrypoint/source-content checks. Text checkout line endings are
  normalized when comparing packaged source with Git blobs.
- Both an ASCII installation path and a Chinese path containing spaces exercised
  real EXE updates. A deliberately failing candidate succeeded while staged and
  failed after switching; the updater restored the previous installation. An
  interrupted old-root move was recovered through the external recovery CLI.
- The old 20240203 WezTerm host was recognized through actual process ancestry
  and blocked on interactive open without changing agent identities. The matching
  Microsoft-signed 1.24.2607.10001 pair passed ancestry and input checks. The input
  suite tests 30 seconds of idle input per provider, mouse reports, resize/focus,
  Unicode paste, punctuation and editing. It does not certify every OS IME.
- Trusted-base Windows isolation checker passed. Changed files are Windows-owned
  implementation, documentation, existing release workflow and Windows-named
  tests; no shared runtime files or isolation policy changes.

Native candidate ZIP SHA256:
`a5e096bd325130a4d0d6774b0994786223ffe63c1b854f5f7b957ba709d11032`.
This is a locally built test artifact, not a published release.

## Scope and observed limits

The user excluded Claude model requests after the configured service rejected
`gpt-6.1-sol` and did not offer `claude-sonnet-4-6`. Those attempts are recorded as
failures/exclusions, not successful replies. Codex returned the expected marker;
Claude terminal input checks require no model request. One control-plane handshake
was rejected during the first job observation; the retry consumed the existing
receipt successfully without changing authentication implementation or policy.

Herdr single-agent restart remains unsupported. Its acceptance check verifies the
explicit unsupported result and unchanged identities; full project stop/reopen is
tested separately. The cold start took 178 seconds on this host; this PR does not
claim a startup performance improvement. Newer ConPTY pairs are reported as
unqualified newer pairs, and unidentified hosts remain warnings, not verified
compatibility. Backups/failed candidates are retained and require manual pruning.

All live acceptance work used marked disposable projects, which were stopped
after validation. The user's daily installation was not replaced. Private config
UI URLs, provider credentials and raw private logs are not included here.
