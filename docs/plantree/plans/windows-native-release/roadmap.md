# Native Windows Release Roadmap

Date: 2026-08-12

## Completed locally

- Restored workflows, documentation, reconnect tests, executable modes, and
  Unix release code removed or rewritten by PR #293.
- Moved Windows-owned runtime and release code into dedicated folders.
- Added a native Rust launcher and Windows-only packaging workflow.
- Kept Windows out of npm package metadata and Unix release builders.
- Restored project-scoped tmux socket binding after PR #293's backend cache
  reuse broke the Linux/macOS/WSL lifecycle smoke.

## Published candidate

- `v8.6.0-beta.1` is immutable and superseded: native tests passed, but the
  ZIP builder rejected a stale `commands/` allowlist entry before publication.
- `v8.6.0-beta.2` is immutable and superseded: native tests and ZIP build
  passed, but archive installation incorrectly prompted for missing Herdr even
  with `-Yes`; no GitHub Release was created.
- `v8.6.0-beta.3` is published as a GitHub prerelease. Windows 2022 native
  tests, PE/ZIP build, PowerShell archive install, installed launcher smoke,
  SHA256 verification, and asset publication passed.
- Stable npm, Linux/macOS artifact, Sidebar, and Android publication routes
  remained untouched.

## Published stable v8.6.0

- Promoted the repository version to stable `8.6.0` across Python, npm, mobile,
  and Windows launcher identity.
- Kept the Windows projection and archive manifest at beta support tier.
- The isolated Windows workflow now builds both immutable beta tags and
  stable CCB tags, attaching its ZIP to the same GitHub Release.
- Stable Linux, macOS, Android, Sidebar, and npm workflows published normally;
  the Windows workflow does not replace or gate their
  platform-specific assets.
- Preserved `v8.6.0-beta.3` as immutable evidence rather than moving its tag.
- GitHub Latest, all ten published assets, their downloaded checksums, npm
  `latest`, and a clean npm-installed CLI smoke were verified after publication.

## Published stable v8.6.1

- Published the audited Mobile Provider controls, direct terminal and shortcut
  controls, built-in `ccb-compact`, and the complete Config UI Role catalog.
- Fixed Provider-mutation idempotency so cached results are bound to the exact
  project and Agent, and fixed compound Mobile terminal input frames so text
  and Enter reach the Pane in order.
- Kept the isolated Windows x64 artifact at beta support tier and attached it
  to the same stable GitHub Release without changing Unix/npm ownership.
- Main-commit Tests, Cross-Platform, and CCBD Real Platform gates passed on the
  exact release commit. All publication workflows passed for Linux, macOS,
  Android, Windows, Sidebar, and npm.
- Verified GitHub Latest, all ten downloaded assets and checksum files, the
  Android manifest, Windows PE x86-64 launchers, npm `latest`, and a clean
  npm-installed CLI and `ccb compact` help smoke.

## v8.6.8 isolation remediation

- Audited the four post-`v8.6.7` Windows/Herdr feature commits against the
  accepted ownership boundary. Every commit crossed into shared, Unix, or
  generic test files and is rejected by the new diff policy.
- Landed exact reverse patches in `7d74e92a8` for `92dae890d`, `0eb15c4b1`,
  `f87f995ff`, and `67a95fea1`. The two later PR merge commits contain no
  additional effective change, so reverting those merge objects would not
  remove the cherry-picked behavior.
- Preserved the Mobile changes, release/version history, Windows newline-test
  skip, and unrelated test-mock corrections in `v8.6.8`.
- Added and landed the dedicated Windows PR isolation workflow and checker in
  `23d62228f`. It detects
  Windows scope from changed paths, diff markers, and commit subjects; blocks
  shared/Linux/macOS/npm/Mobile paths; and freezes the existing shared-to-
  Windows reverse-import inventory against expansion.
- Hardened and landed the native-only gate in `702870c1b`: global release
  metadata is no longer exempt, Windows API markers and `from platforms import
  windows` imports are detected, and the workflow executes the checker from the
  trusted base revision under `pull_request_target`.
- Local Linux verification passed. Real macOS and affected-host Windows
  validation remain external CI/manual gates.
- Detailed evidence: [evidence/v8.6.8-windows-pr-isolation-audit.md](evidence/v8.6.8-windows-pr-isolation-audit.md).

## Next after stable publication

1. Make the dedicated isolation workflow a required PR check alongside the
   existing Ubuntu and
   macOS test lanes on future Windows PRs.
2. Install the next immutable ZIP on a real user Windows x64 machine.
3. Validate WezTerm + Herdr startup, pane creation, capture, restart, kill, and
   Codex/Claude provider workflows.
4. Record failures without upgrading the support tier prematurely.
5. Cut a new immutable release for fixes; do not move an already published
   stable or beta tag.
