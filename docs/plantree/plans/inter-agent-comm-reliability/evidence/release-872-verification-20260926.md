# v8.7.2 installation and upgrade verification

Date: 2026-09-26
Candidate source: 4a1cfca6b (tree identical to 281002e56 after metadata commit separation).

- Final focused regression and metadata/package checks: 603 passed in 17.67s.
- Built Linux stable archive from committed source with Rust helpers.
- Installed packed @seemseam/ccb 8.7.2 through its normal npm postinstall, downloading the candidate archive and SHA256SUMS from a temporary loopback HTTPS server with an explicit test CA. The real managed Python bootstrap completed; CLI reported v8.7.2.
- GitHub v8.7.1 Linux artifact created a real helper session and completed jobs job_3546d0ff8ca0 and job_7620daa85c3e. After relaunch under that old package, the stored command naturally accumulated seven resume suffixes. No runtime/session files were edited to create the condition.
- Stopped the old project through CCB and started the same project using the npm-installed 8.7.2 payload. Session 01a0dd9d-7440-7dc2-ba2b-d43db07d5ef6 remained unchanged and the saved command normalized to one resume suffix. No clear or runtime-file editing was needed.
- Upgrade Codex-to-Codex chain: parent job_4b8c6d5a4e5c, child job_e8c6318a0b0f, continuation job_5efe5901a9b2, exact marker DONE_UPGRADE_872 completed.
- Installed-payload crash test: killed only verified test-owned helper PID 1816379. Parent job_20533b1b2b80 submitted a child while helper was recovering; child job_43c2d5abdfdd and continuation job_6838c73d6889 completed after native automatic recovery. Exact marker DONE_PACKAGED_CRASH_872; same helper session retained; no manual retry or repair.
- Final mailbox check: both idle, queue depth 0, pending replies 0. Project-local ccb kill returned ok/unmounted, forced=false.
- Harness corrections: local HTTP is unsupported by npm's HTTPS downloader, so used temporary trusted loopback HTTPS. Extracting the archive into its own download directory replaces the archive pathname with its included legacy symlink; rebuilt into a separate serving directory and did not extract there. Neither failed attempt was counted as an install pass.
- Trusted-base isolation checker was unchanged. Runtime fix passed non-Windows scope; common metadata passed non-Windows scope; Windows-only metadata passed Windows scope. The original release-preparation history was consolidated to remove an intermediate add/revert of Windows metadata; final tree bytes stayed identical.

Remote CI, publication receipts and final temporary-root removal are recorded after they complete.

Additional CI qualification: macOS and WSL communication matrix reproduced
the main-branch Claude broadcast failure. The line-oriented stub completed
before the delayed Enter finished optional language guidance; production
input protection correctly held the next job as composer_layout_unknown.
After repainting unanchored completed-request tails (including the sanitized
no-final-blank-line case), 18 stub regressions passed and the full local
system_comm_matrix.sh passed: mixed broadcast, dual Codex/Claude/Gemini,
cross-project isolation and kill cleanup. Remote reruns use hotfix 1c8fb64f1,
common metadata 4997b921d, Windows metadata 91e9eb733.

All disposable projects and matrix runtimes were stopped through ccb kill;
the temporary HTTP/HTTPS servers were stopped. Removed only the three
test-created host discovery entries from the initial harness attempt and
validated JSON. Project/provider-copy directories and matrix temporary homes
were deleted; original Nozeli and source workspace were untouched.

Remote repair qualification: PR #357 head 1c8fb64f1 passed every gate and was
merged as 96d705cbc. Linux full suites: 7351 passed, 11 skipped, 42 deselected;
macOS full suite: 7243 passed, 119 skipped, 42 deselected. Lifecycle: 21 passed,
7383 deselected. Real macOS and WSL matrix/soak/stress/cleanup all passed in
run 36242374936.

Combined release candidate 91e9eb733 passed Linux full suites, lifecycle,
provider blackbox, Rust, package install, trusted Windows isolation and real
macOS/WSL runs. First macOS full suite failed only the existing
test_managed_pane_command_ignores_stale_socket_node at its 15s subprocess
timeout. No Codex source/test diff exists between the green repair head and
combined release candidate. A single failed-job rerun was requested on run
36242381378; do not count the first attempt as passing.

The common-only metadata PR intentionally lacks the two Windows-owned version
updates in the separate Windows PR; its two version-consistency failures are
resolved in the combined candidate. No isolation gate was weakened.

The single failed-job rerun passed: macOS 7243 passed, 119 skipped, 42
deselected in 839.78s, job 108407874090. The combined candidate's required gate
passed. PRs #357/#358/#359 merged in order; release source is
91fb0a4d2ef43ea9040c5507409db73cc5d4cf06, byte-identical to candidate 91e9eb733.
Annotated v8.7.2 was pushed at that source. Release workflows: artifacts
36244311573, npm 36244311632, Windows 36244311643, sidebar 36244311584.
All four publication workflows completed successfully. Public v8.7.2 assets:
Linux x86_64, macOS universal, signed Android APK/metadata, Windows x64 and
sidebar helper. Downloaded all assets and verified SHA256SUMS plus the separate
Windows/sidebar checksums. Android metadata reports version 8.7.2/code 8070002.
The public bilingual release body matches the committed notes apart from a
trailing blank line. The remote annotated tag peels to the exact release source.

Official npm registry reports @seemseam/ccb 8.7.2 and latest=8.7.2, integrity
`sha512-lVWR+AKy3Kmww/tTSwuQs5zdFynjzxA6ODk7LxIWzY91bh05rDoltyjVu8LKZVb+CfGsNFG+wVZRUGs8BxkbYQ==`.
A fresh registry install completed; CLI printed v8.7.2 and managed Python
3.14.4 imported aiohttp and required cryptography primitives. Installed Codex
command rewriting and OMP composer bridge files match the release source.

Local verification limitations: registry propagation briefly returned 404
immediately after publication; subsequent official queries/install succeeded.
Node's direct GitHub download timed out on this proxy-configured machine.
Stopped that temporary install and retried with process-local
`NODE_OPTIONS=--use-env-proxy`, without changing global configuration, download
URLs, TLS validation or product code. The public CI install also passed.

Published: https://github.com/SeemSeam/claude_codex_bridge/releases/tag/v8.7.2

Release reply (issue remains open):
https://github.com/SeemSeam/claude_codex_bridge/issues/356#issuecomment-5846628558

Cleanup complete: the entire disposable release-test root, public install,
downloaded assets/caches and temporary HTTPS certificates were removed after
verification. No test processes or host discovery entries remain. Only this
sanitized evidence and the source repair checkout are retained.
