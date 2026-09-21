# v8.7.0 local source preparation

Date: 2026-09-21
Scope: owner-requested local commit, version synchronization and bilingual README.
Branch: `release/v8.7.0`, isolated from `a46e0830b`.
Public push, tag, registry publication and shared-runtime promotion are not included.

## Validation

- Default full CI selection: **7321 passed, 13 failed, 4 skipped, 42 deselected**.
- Failure audit: 9 socket protocol fixtures have virtual runtimes without a real
  composer; 3 dual-agent fixtures patched the sender backend but not the newly
  added pre-claim backend resolution. Unknown input correctly held their jobs.
  Adapted only those test transports; no runtime protection or assertions were
  weakened. A headless-fixture experiment changed adapter semantics and was
  discarded. Windows release projection also needed its artifact reference
  synchronized to v8.7.0; this is metadata only.
- First failed-case rerun: **5 passed, 8 failed**; the 3 dual-agent cases and
  projection passed. After final socket fixture correction, the whole socket
  file plus guard/FIFO, Windows projection and CLI version checks:
  **130 passed**. All 13 original failures are covered by successful reruns.
  Thus all **7334** selected non-skipped cases passed across the full run and
  targeted reruns; this is not a second clean full-suite run.
- CLI `--print-version`: `v8.7.0`, using the repository's populated Python venv.
- Bilingual release-note validator: passed. `git diff --check`: passed.
- `npm pack --dry-run --json`: 19 allowlisted package entries for
  `@seemseam/ccb@8.7.0`, without project runtime state. No registry publish.
- Prior real installed-model tests and their limitations remain in
  [installed evidence](installed-draft-guard-20260920.md). They were not rerun
  for documentation, release metadata or fake-transport fixture changes.

Commands (repository root, venv bin on PATH, `PYTHONPATH=lib`):

```sh
python -m pytest -q test/ --tb=short --durations=15 -m 'not provider_blackbox and not ccb_lifecycle_smoke'
python -m pytest -q --lf --tb=short
python -m pytest -q test/test_v2_ccbd_socket.py test/test_input_draft_fifo.py test/test_input_draft_guard.py test/test_windows_x64_release_surface.py test/test_cli_versioning_local.py --tb=short
python ccb.py --print-version
npm pack --dry-run --json
git diff --check
```

## Remaining readiness findings

Local source preparation does not close the existing topic's production gates:
Codex/Claude `Ctrl-C` clear ownership, generic ambiguous-send terminalization,
and OMP bridge security/lifecycle review. README and bilingual release notes
describe the first-version testing scope and concurrent-input limitation.
Codex remote app-server recovery after forced daemon termination remains manual.
No new macOS, Windows or Android artifact qualification is claimed.

The Linux artifact must be built from the committed source; its outcome and
source identity are reported separately after the source commit.
