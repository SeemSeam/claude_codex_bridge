# v8.7.10 release verification

Date: 2026-10-10
Status: v8.7.10 published; public artifacts, fresh npm install and real Pi verified.

## Source identity and scope

- Previous release: v8.7.9 at `3f58126188687aa67babf544d118d5f9a0978885`.
- Pi source fix: `d3df1ccf3` ([PR380](https://github.com/SeemSeam/claude_codex_bridge/pull/380)).
- Global metadata: `0745db30d` ([PR381](https://github.com/SeemSeam/claude_codex_bridge/pull/381)).
- Platform pointers / exact candidate: `636afa2f2d0a4cc10283573089faae71fbfbdd37`
  ([PR382](https://github.com/SeemSeam/claude_codex_bridge/pull/382)).
- Source, global release metadata and platform-owned changes were reviewed as
  separate diffs against the unchanged trusted isolation checker; all passed.
- No unrelated dirty main-checkout work or PR366 was included. System-native
  OMP-to-Pi source configuration is local user-directed work, not release content.

## Qualification

Local exact-candidate full suite: **7721 passed, 12 skipped, 42 deselected** in
472.21 seconds, after building the runtime accelerator and using the venv PATH.
The stale-socket case passed locally in 5.43 seconds. No assertions were relaxed.
Release/version/npm/platform packaging checks: **51 passed**. Bilingual notes,
new documentation links, whitespace, npm dry-run allowlist (19 files) passed.

Linux artifact built from the clean exact commit, then extracted and compared
against the reviewed snapshot source. BUILD_INFO version 8.7.10 and commit
636afa2f2 matched the full candidate. Real packaged CCB startup of two Pi agents
passed: project `startup-fe30487f`, unique marker `ec78e7a7`, both terminal
assistant messages with stopReason=stop, SDK Client/Zod callback evidence for
each actor, identical shared snapshot path and control-plane cleanup exit 0.
Actual keeper and ccbd processes both had the configured timeout value `90`.
CLI startup elapsed 4.56 seconds. This complements the corrected source
cold/warm probes documented in [source evidence](pi-snapshot-startup-20261010.md).

Raw local evidence: `/var/tmp/ccb-8710-full-pytest.log`,
`/var/tmp/ccb-8710-local-dist/`, and
`/var/tmp/ccb-pi-snapshot-real-20261010/startup-fe30487f/`.

| Exact-candidate hosted gate | Run | Result |
| --- | --- | --- |
| Tests | 38044485963 | success |
| macOS / WSL real platform | 38044488372 | success |
| Linux/macOS cross-platform | 38044490727 | success |
| Native build/install, publish=false | 38044493046 | success |
| Trusted platform isolation | 38044487855 | success |

Hosted macOS full suite: 7608 passed, 125 skipped, 42 deselected in 790.62 s.
Hosted Linux Python 3.12: 7716 passed, 17 skipped, 42 deselected in 626.39 s.
All exact-candidate gates passed on their initial completed run.

Intermediate source/metadata PR full tests and duplicate candidate PR runs
were explicitly cancelled to concentrate runner capacity on the final exact
candidate; they are not counted as passing gates.

## Limits

The original reported macOS EOF cause is not established. Legacy unowned staging
folders are not automatically deleted. Easyfun native source discovery remains
HTTP 401; three other system Pi sources passed representative real completions.
Source and local package tests do not claim publication or installed-host upgrade.

## Publication

Main was fast-forwarded through the GitHub API with `force=false` after all five
exact-candidate gates succeeded. Git HTTPS stalled before changing main and was
terminated; the API result was checked against the exact commit. Annotated tag
object: `17151f4dd7224534df8d5a55e801e6c674bb7629`.
[GitHub release](https://github.com/SeemSeam/claude_codex_bridge/releases/tag/v8.7.10)
was created with the committed bilingual notes.
PR380 is recognized merged; stacked PR381/PR382 were closed after verifying their
heads are reachable from main (closed, not platform-reported merged).

All four publication workflows succeeded: artifacts 38045715592, native package
38045715577, Sidebar 38045715579 and npm 38045715599. npm published through
Trusted Publishing with provenance (transparency log index 3184219037).

All 10 public assets were downloaded. GitHub SHA256 digests, combined checksum
manifest and individual ZIP/Sidebar checksum files matched. Linux/macOS
BUILD_INFO and the new Pi snapshot source match the reviewed commit; native ZIP
records the exact full tag commit. Both Mobile manifests agree with the APK
size/hash; the actual APK binary XML reports versionName 8.7.10 and versionCode
8070010. Public bilingual notes match the committed file exactly.

Verification receipt: `/var/tmp/ccb-8710-public-verification.json`.

## Fresh npm installation and public-package real test

Initial immediately-after-publish npm lookup returned the previous packument
and ETARGET, while the single-version registry API already exposed 8.7.10.
A fresh-cache registry query then confirmed version/latest 8.7.10, and a fresh
normal npm installation succeeded in 15 seconds, including automatic GitHub
archive download and managed Python bootstrap. No skip-download flag, staged
payload, proxy change or installer workaround was needed.

Installed prefix: `/var/tmp/ccb-8710-fresh-npm/prefix`.
CLI reports v8.7.10 / 636afa2; ask help succeeds. A fresh-cache npm signature
audit verified the registry signature for the one installed package. Package version, BUILD_INFO and
both changed runtime files match the release source. The actual published npm
payload passed real two-Pi startup in `startup-ae149ae3` (4.16 s): unique marker
`f4fc9b0e`, fresh terminal assistant outputs, valid SDK/Zod callback evidence for
both actors, one shared snapshot, timeout value 90 in actual keeper and ccbd,
and cleanup exit 0. Existing work-project agents were not upgraded/restarted.

Main's post-publication duplicate Tests 38045713613 and real-platform
38045713549 were still running at this checkpoint; cross-platform 38045713546
passed. The identical exact candidate completed all gates before publication.
