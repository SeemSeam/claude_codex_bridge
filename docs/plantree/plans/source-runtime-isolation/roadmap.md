# Source Runtime Isolation Roadmap

Date: 2026-06-09

## Done

- `ccb` has a source-checkout guard that refuses stateful commands outside
  allowed external test roots while still allowing safe introspection.
- `ccb_test` wraps the source checkout and refuses to run from
  `/home/bfly/yunwei/ccb_source` or against a project inside the source
  checkout.
- `test/test_source_runtime_guard.py` covers the source entrypoint guard,
  `ccb_test` external-project behavior, and source-checkout rejection.
- Project memory now states that source validation must use `ccb_test` from an
  external project and must not install source changes into the global/system
  environment.
- Default source-test roots are narrowed to `/home/bfly/yunwei/test_ccb2` for
  both `ccb` and `ccb_test`; legacy sibling directories require explicit
  `CCB_SOURCE_ALLOWED_ROOTS` or `CCB_TEST_ROOTS` overrides.
- `ccb_test` preflight now rejects arbitrary external CWD and `--project`
  targets unless they are under an allowed source-test root.
- `ccb_test --diagnose` reports the running wrapper, source `ccb`, CWD,
  project paths, default roots, explicit env roots, effective roots, checked
  paths, and whether the current invocation is allowed as a source-test
  project.
- The 2026-06-15 stable-entrypoint closure added `ccb doctor` entrypoint and
  daemon implementation-root diagnostics, added installer protection against
  temporary-prefix installs writing external bin dirs, and restored
  `/home/bfly/.local/bin/ccb` to the durable installed release. See
  [topics/stable-entrypoint-boundary.md](topics/stable-entrypoint-boundary.md).
- The 2026-09-02 V2 maintenance migration preserved the post-v8.6.11 mobile
  Pi/OMP native-conversation fix from source commit `b0b9e5db1` as standalone
  commit `4347efa52`; the complete mobile gateway, Pi session, Pi/OMP
  completion, and provider-pathing gate passed with `171 passed`.
- The 2026-09-02 remote-maintenance audit backported merged PRs 328-330 as
  `168545cde`, `31fa74811`, and `fc634bb37`. Review found and repaired a
  concurrent first-publish failure in shared Provider snapshots as
  `2c605ec2c`; the combined focused gate passed with `395 passed`, and all four
  changes passed the Windows ownership checker as `scope=non-windows`.

## In Progress

- Make the operational workflow explicit in project memory, baseline gates,
  and active runbooks so agents stop treating source validation as a normal
  `ccb` command.
- Record cleanup rules for project-agent runtime state so active installed
  work-environment state is not deleted during source testing.
- Track restart hygiene for already-running project daemons that may still have
  inherited the old temporary smoke-prefix PATH or implementation root.
- Harden separate-prefix maintenance installs: `CODEX_INSTALL_PREFIX` and
  `CODEX_BIN_DIR` do not isolate shell, skill, tmux, or Provider-setting writes
  by themselves. Until explicit installer skip controls exist, `ccb2` rebuilds
  must also use its private `HOME` while retaining the real account only as
  `CCB_SOURCE_HOME`, and its internal bin directory must not enter the user's
  normal shell `PATH`.

## Next

1. Add a test or hygiene check that active runbooks use the absolute source
   wrapper when validating current source changes.
2. Define an explicit test-project reset procedure for
   `/home/bfly/yunwei/test_ccb2` that stops its backend before removing
   disposable runtime residue.
3. Add a small restart runbook for moving live projects off a temporary
   implementation root without deleting project runtime state.

## Deferred

- Automatic migration or deletion of old ad hoc test directories under
  `/home/bfly/yunwei`.
- Automatically repairing user shell startup files or global PATH order.
- Removing diagnostic overrides such as `CCB_SOURCE_RUNTIME_OK=1`.
