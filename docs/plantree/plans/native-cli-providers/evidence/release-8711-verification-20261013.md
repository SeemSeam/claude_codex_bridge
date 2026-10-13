# v8.7.11 release verification

Date: 2026-10-13
Status: GitHub and npm published; public asset verification passed.

## Source and scope

- Previous tag: v8.7.10 (`636afa2f2`).
- PR385 merged source: `55d3303bf`; integration evidence: `e7e726f31`.
- V2 workflow isolation: `64ef0fbcf` (PR386, recognized merged).
- Reviewed global release metadata: `bddd0ddbf` (PR389).
- Exact platform metadata/release: `567cecc903a8fe80ca2373b1dc4550c583ea2e42` (PR390).
- Immutable annotated tag: `052b02235341fe36672b3973851395ff574b5134`.
- PR389/390 were closed after their exact heads became reachable from main.
- The earlier PR387/388 stack was superseded; the only candidate adjustment
  changes two evidence-document lines. Runtime, tests, metadata, QR and notes
  are byte-identical to the candidate that passed local full tests.

## Qualification

Local full suite: 7752 passed, 12 skipped, 42 deselected in 410.71 s.
Hosted Linux Python 3.10/3.11/3.12: 7739 passed, 25 skipped, 42 deselected each.
Hosted macOS Python 3.11: 7631 passed, 133 skipped, 42 deselected in 958.34 s.
Skipped cases retain their opt-in/platform boundaries; no assertions relaxed.

Exact final-source gates all succeeded:

| Gate | Run |
| --- | --- |
| Tests, lifecycle, Provider blackbox and install | 38342891462 |
| macOS/WSL actual daemon communication and stress | 38342894161 |
| Cross-platform compatibility | 38342897256 |
| Native Windows build/install, publish=false | 38342900607 |
| Platform metadata trusted-base isolation | 38342892370 |
| Global metadata trusted-base isolation | 38342887934 |
| Source trusted-base isolation | 38342859529 |

The initial source isolation run classified a provider evidence document's
platform-validation wording as functional scope. Clarified the evidence;
checker/policy unchanged. Final source PR and both metadata PRs passed.
Duplicate intermediate/main full-test runs were cancelled to concentrate
capacity on the exact candidate; cancelled runs are not passing evidence.

## Publication and public verification

[Release](https://github.com/SeemSeam/claude_codex_bridge/releases/tag/v8.7.11)
contains the committed substantive English and Chinese notes. Main and tag
resolve to the exact candidate. Publication succeeded for all four workflows:
artifacts 38344412550, npm 38344412541, Sidebar 38344412523,
native Windows 38344412518. npm used Trusted Publishing and provenance,
transparency log index 3240977449.

All 10 public assets were downloaded. GitHub SHA256 digests and combined and
individual checksum manifests match. Linux/macOS BUILD_INFO, Pi source,
workflow policy and QR image match reviewed source. Native ZIP source identity
matches the tag. Both Mobile manifests match the APK size/hash; independently
parsed APK binary XML reports versionName 8.7.11 and versionCode 8070011.

The WeChat technical group 2 PNG is exactly the owner's supplied attachment,
174048 bytes, cache identifier 583d37f5; its printed expiry is October 20.
All README variants refresh the image key, version badge and Mobile link.

Raw receipts: /var/tmp/ccb-8711-public-verification.json,
/var/tmp/ccb-8711-hosted-test-counts.json, /var/tmp/ccb-8711-full-pytest.log.

## Limits

Installed-library and packaged dispatcher tests are not authenticated Pi
image-transformation model acceptance. This release does not claim that test.
Existing project runtime remains running with previously imported code;
reopen affected projects safely to load the release. Unrelated local Mobile
planning changes were preserved and excluded from publication.

The first npm install/query observed registry propagation ETARGET/E404 after
successful publication; registry latest subsequently became 8.7.11. No version
was republished and no immutable tag was moved.

## Public npm payload verification

Registry version and latest both confirm 8.7.11. Two normal fresh npm installs
failed on this host's Node -> GitHub connection (socket hang up, then
ETIMEDOUT to 20.205.243.166:443). Do not report normal local installation passed.

Fallback downloaded the actual npm package with scripts disabled, extracted
the checksum-verified public Linux artifact into its expected payload path,
then ran the unmodified npm installer to bootstrap its managed Python runtime.
CLI reports v8.7.11, ask help passes, and standalone tests on that actual public
payload pass 12 V2 submission/recovery combinations, V2/V3 completion and
strict V3 direct handoff. This verifies the package/runtime while leaving the
normal Node download path unqualified locally. Hosted npm runtime smoke passes.
No external Provider configuration or work-project runtime was changed.

Fallback prefix: /var/tmp/ccb-8711-fresh-npm/fallback-prefix.
Raw install logs: /var/tmp/ccb-8711-fresh-npm/.
