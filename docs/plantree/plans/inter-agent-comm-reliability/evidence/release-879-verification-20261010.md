# v8.7.9 Publication Verification — 2026-10-10

Status: GitHub and npm published; public artifacts and installed payload verified.
Default local npm download remains blocked by the host's GitHub TLS route.
A pre-existing macOS stale-socket CI timeout remains unresolved.

## Source identity and landing

- Repair: `e9789d5ecd1bf2535b2f6789603d24ba9c13c57e` (PR #376).
- Separate global metadata: `6f2c683cf0280df3dbf4635eb19531292e63b970`
  (PR #377); platform-owned metadata: `3f58126188687aa67babf544d118d5f9a0978885`
  (PR #378, reviewed against `6f2c683cf`). Trusted ownership gates passed
  separately for the actual review ranges; policy was not changed.
- Main was fast-forwarded atomically from `721be82903a3a2584bde4f455c4ee96fa4d652c1`
  to `3f58126188687aa67babf544d118d5f9a0978885`. This avoids temporarily
  inconsistent version pointers while keeping the review surfaces separate.
  PRs #376/#377 are recognized as merged. The stacked #378 was closed after
  its exact head had landed in main; it is not recorded as a PR merge.
- Immutable annotated tag object: `33ba8cd8837853bd12040e1b9bbd50b0fc35fd86`;
  tag target: `3f58126188687aa67babf544d118d5f9a0978885`.
- Git transport repeatedly failed TLS. GitHub object APIs preserved exact
  blob/tree/commit/tag hashes; the default-branch update used `force=false`.
- [Bilingual notes](../../../../releases/v8.7.9.md) match the public release
  body. PR #366 remains excluded. Unreviewed shared-checkout work was protected.

## Qualification before publication

The exact candidate `3f58126188687aa67babf544d118d5f9a0978885` passed:

| Gate | Public run | Result |
| --- | --- | --- |
| Full tests, Python compatibility, macOS, lifecycle, blackbox, Rust and install | [38019069442](https://github.com/SeemSeam/claude_codex_bridge/actions/runs/38019069442) | success |
| Real macOS and WSL ccbd/ask/soak/stress | [38019069446](https://github.com/SeemSeam/claude_codex_bridge/actions/runs/38019069446) | success |
| Linux/macOS cross-platform checks | [38019069841](https://github.com/SeemSeam/claude_codex_bridge/actions/runs/38019069841) | success |
| Native package build/install, publication disabled | [38019067382](https://github.com/SeemSeam/claude_codex_bridge/actions/runs/38019067382) | success |
| Trusted ownership boundary | [38019069469](https://github.com/SeemSeam/claude_codex_bridge/actions/runs/38019069469) | success |

Local candidate full suite: **7708 passed, 12 skipped, 42 deselected** in
494.20 s, with the accelerator built and the venv on PATH. Release/version/npm/
installer checks: 41 passed; platform packaging/surface checks: 22 passed.
Bilingual validation, npm pack allowlist (19 files), whitespace and 19 introduced
local Markdown links passed. The earlier invocation omitted `python` from PATH
and failed 16 cases; the corrected environment passed 356 affected-suite checks
and then the clean full candidate suite. No assertions were relaxed.

The assembled local archive passed independent install/bootstrap, four actual
Codex restrictive-policy probes, unchanged config/sentinel checks, and a real
Codex → OMP/Demo → Codex roundtrip (`job_5670249aea6c`): correct caller,
exact marker and `42`, one submission, consumed return, queues empty, cleanup 0.
[Source identity evidence](codex-ask-caller-identity-20261010.md) and
[reverse OMP/Claude qualification](provider-roundtrip-20261010.md) retain the
additional real service results and Pi insufficient-resource limitation.

## Public publication and payload verification

- [GitHub v8.7.9](https://github.com/SeemSeam/claude_codex_bridge/releases/tag/v8.7.9)
  is stable, with substantive equivalent English/Chinese notes and 10 assets.
- [@seemseam/ccb 8.7.9](https://www.npmjs.com/package/@seemseam/ccb/v/8.7.9)
  is published and `latest=8.7.9`, with SLSA provenance.
- All four tag publication workflows succeeded: artifacts
  [38020348482](https://github.com/SeemSeam/claude_codex_bridge/actions/runs/38020348482),
  native package [38020348454](https://github.com/SeemSeam/claude_codex_bridge/actions/runs/38020348454),
  Sidebar [38020348458](https://github.com/SeemSeam/claude_codex_bridge/actions/runs/38020348458),
  npm [38020348474](https://github.com/SeemSeam/claude_codex_bridge/actions/runs/38020348474).
- Downloaded every public asset; all 10 GitHub SHA256 digests, the combined
  checksum manifest and both individual archive checksum files match.
- Linux/macOS BUILD_INFO versions and commit abbreviations resolve to the tag;
  native ZIP records the full commit. Packaged launcher source bytes match
  the reviewed source. Abbreviation lengths differ by checkout object count;
  validation uses the exact full tag and a matching unique commit prefix.
- Both mobile manifests match the APK's SHA256/size and each other.
  Independently decoded the actual APK binary XML: versionName `8.7.9`,
  versionCode `8070009`.

| Public payload | SHA256 |
| --- | --- |
| Linux archive | `f8ccb28b258bf17ffc7ab5cd350b5d8333b7960b765cef753e00ba693b628f10` |
| macOS universal archive | `7b7e0319736eb2e571e891448ad16b34d3818509b384b969212d0c5179f51cb5` |
| Native ZIP | `127a8450e3bef83822a869729187b7f1c4df5bf2b0367a2d0f93d906a913f97a` |
| Android APK | `7af6fd82bf6422b12aa5c7a8b670dba30b37462f59bd2de74ed10459d6601566` |

## Local registry installation and real public-package test

Two default fresh npm installations hit `socket hang up` on github.com.
Node and curl independently reproduced TLS failure; an old v8.7.8 checksum
control also failed, and a direct no-proxy attempt timed out. Proxy case
normalization did not resolve it. No external proxy or Provider config changed.

Fallback verification installed the exact package from npm into a fresh prefix,
using `CCB_NPM_SKIP_DOWNLOAD=1` only for initial package installation. The already
independently downloaded/verified public archive was then staged into its normal
vendor directory. With the skip flag absent, the real npm wrapper automatically
built its managed Python environment. `ccb --version`, `ask --help`, exact
launcher bytes and npm registry signature audit passed (1 signed package).
This is a verified staged public-payload installation, not a passing default
network-download installation.

The resulting public npm payload passed actual Codex → OMP/Demo → Codex:
`job_5944a9332469`, marker `CCB_REAL_IDENTITY_4a2ab3ab`, from=caller, correct
`42`, one native ask call, task_reply consumed and empty pending queues.
The managed app-server was enabled; the disposable project exited through its
control plane with cleanup 0. The existing work project was not upgraded or
restarted; external Provider state remained unchanged.

## Unresolved post-publication CI timing

Earlier source run [38018676816](https://github.com/SeemSeam/claude_codex_bridge/actions/runs/38018676816)
failed macOS `test_managed_pane_command_ignores_stale_socket_node` at its
15-second subprocess limit; a single failed-job rerun also failed the same
case (7594 passed, 125 skipped, 42 deselected). The test and its shell-loop
implementation are unchanged from the previous release. The same failure
already appears in [v8.7.3 verification](release-873-verification-20260928.md).
The loop performs 100 sleeps plus filesystem/process checks. Runner latency
is a hypothesis, not an established root cause; passing exact-candidate
qualification does not resolve this intermittent failure.

Main's identical-source repeat run [38020325492](https://github.com/SeemSeam/claude_codex_bridge/actions/runs/38020325492)
also failed only that macOS case (7594 passed, 125 skipped, 42 deselected);
all other lanes succeeded. Its real platform run
[38020325564](https://github.com/SeemSeam/claude_codex_bridge/actions/runs/38020325564)
and cross-platform run
[38020325431](https://github.com/SeemSeam/claude_codex_bridge/actions/runs/38020325431)
succeeded. Do not describe all historical/main CI as green.
The source real-platform run's first macOS stress attempt exceeded the unchanged
1500 ms submit-p95 threshold at 1923 ms; its one bounded rerun passed, as did
exact-candidate and main real-platform qualification. Initial failures remain
recorded. Next: diagnose the stale-socket timing and use deterministic test
synchronization; do not raise its timeout or retry until green.
