# v8.7.1 preparation and public-state audit

Date: 2026-09-21
Branch: `release/v8.7.1`, based on local `9e8ff7420` (`release/v8.7.0`).
Scope: Rich system-default file opening, macOS/WSL routing, version metadata,
bilingual documentation and local commit. No public mutation authorized here.

## Checks

- Rich generation and opener tests: **34 passed**, including 2 real Yazi 26.5.6
  tmux UI cases. macOS and WSL are simulated-platform argument/routing tests,
  not GUI hardware qualification.
- Combined release/generation/native UI suite: **90 passed**:

```sh
CCB_TEST_REAL_YAZI=1 PYTHONPATH=lib python -m pytest -q \
  test/test_cli_tools_workbench.py test/test_workbench_yazi_open.py \
  test/test_release_visible_versions.py test/test_cli_versioning_local.py \
  test/test_build_linux_release_script.py test/test_build_macos_release_script.py \
  test/test_build_windows_release_script.py test/test_npm_runner.py \
  test/test_install_release_entrypoints.py test/test_windows_x64_release_surface.py \
  test/test_windows_x64_release_surface_baseline_version.py --tb=short
```

- CLI version: `v8.7.1`. Bilingual notes validator and whitespace checks pass.
- npm dry-run identifies `@seemseam/ccb@8.7.1` with the loader allowlist.
  Registry lookup for that exact version returned E404 (available).
- App version synchronized to `8.7.1+8070001`; workflow defaults, Windows
  projection metadata, localized badges and current APK links synchronized.
  No Windows functional implementation was added.
- Existing v8.7.0 full-suite evidence remains inherited; no provider runtime
  code changed in this patch, and that full suite was not rerun.
- Linux package build is performed from the committed source and reported
  separately. Actual macOS, WSL and Windows builds/GUI checks are unavailable.

## GitHub discrepancy

Read-only GitHub API checks found default `main` with `VERSION=8.6.19`, while
its README badge and newest embedded Release Notes entry were `8.6.13`. GitHub `releases/latest` and npm both reported
`v8.6.19` / `8.6.19`. There were no remote v8.7 tags. The v8.7.0 source commit
was local only. These are different failure causes: stale landing-page metadata
versus unpublished local source. The release skill requires both consistency
and explicit publication scope; passing a local preparation does not update
the default branch or public release record.

The new README consistency test prevents local badge/download/embedded-note drift.
After extending it to embedded entries, its focused rerun passed (1 test). A future
public release still needs an explicit remote default-branch check, tag/source
identity, bilingual release-body verification and package verification. The
production-readiness findings inherited from v8.7.0 remain open as documented
in the bilingual release notes; this evidence does not waive them.
