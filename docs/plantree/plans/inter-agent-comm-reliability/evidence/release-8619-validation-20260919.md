# v8.6.19 Release Validation

Date: 2026-09-19
Role: evidence
Status: local release gates passed; public publication verification follows

Includes OMP exact native context restoration (`4b966363b`) and unified FIFO,
screen capture, and English abnormal-reply guidance (`648276053`). Release
preparation is isolated at `/var/tmp/ccb-release-8619-0UnDmC/worktree`; unrelated
Mobile planning files and `.zai` in the working project are excluded.

## Test evidence

- Focused provider/dispatcher/screen suite: **410 passed**, including opt-in
  real OMP 18.2.1 native conversation loading with no model invocation.
- Release builders, npm runner, CLI parser/router/wiring: **164 passed**.
- Full default CI test selection (`not provider_blackbox and not ccb_lifecycle_smoke`):
  **7246 passed, 17 failed, 4 skipped, 42 deselected** on the first run.
- Failure audit: 14 require `python` on PATH (the interpreter was initially
  invoked by absolute path); 2 socket tests expected old empty failure bodies;
  1 generic launcher test conflated OMP's launch ID with native session identity.
  Updated the three obsolete expectations to assert inspection commands and
  native-identity separation. Restored the venv bin directory to PATH.
- Exact failed-case rerun: **17 passed**. Thus all 7263 selected non-skipped
  cases passed across the full run and targeted rerun; this is not a claim of
  a second full-suite run. Commands:

```sh
PYTHONPATH=lib /home/bfly/yunwei/ccb-v2/.venv/bin/python -m pytest -q test/ --tb=short --durations=15 -m 'not provider_blackbox and not ccb_lifecycle_smoke'
PATH=/home/bfly/yunwei/ccb-v2/.venv/bin:$PATH PYTHONPATH=lib python -m pytest -q --lf --tb=short
```

## Package evidence

- Bilingual release-note validator passed; npm dry-run contains 19 expected
  package entries at version 8.6.19. Version, CLI, workflow defaults, Mobile
  version/build code and download links, and Windows release reference agree.
- Linux stable archive built successfully, SHA256SUMS verified, archive contains
  new screen/notice modules and OMP restoration, and extracted CLI reports
  `v8.6.19` and correct screen help. Build/source identity is embedded in BUILD_INFO.
- Compilation and `git diff --check` passed. macOS, Android and Windows release
  builds remain delegated to the existing tagged-source GitHub workflows.

## Limits

Real Codex/OMP FIFO and screen qualification are linked in the existing evidence.
OMP native empty continuation and real Claude FIFO qualification remain open;
network/quota/model-switch fault scenarios were not exhaustively injected.
Public artifacts and npm installation must be verified after workflow completion.
