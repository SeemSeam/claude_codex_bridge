# Install Update Stability Roadmap

Date: 2026-06-04
Last verified: 2026-07-23

## Done

- 2026-09-28, released in v8.7.3: install/update catalog followups suppress
  the six workflow preview roles (`ccb_checker`, `ccb_clarification_broker`,
  `ccb_plan_reviewer`, `ccb_round_checker`, `ccb_worker`, and `su_ccb`, all
  under `agentroles`). Explicit installation, installed-role updates and
  missing-source diagnostics retain their existing behavior. This promotes
  the uncommitted 2026-09-21 patch, not the older config-panel filtering fix.
  Update/RolePack/config-UI regression suites: 188 passed. Public archive and
  fresh npm payload both verify the filter and retained ordinary/default-role
  recommendations. See [release receipts](../inter-agent-comm-reliability/evidence/release-873-verification-20260928.md).

- Existing installer detects `CCB_LANG`/locale and has a `msg` function for
  selected shell installer messages.
- Existing installer blocks accidental root installs unless the user confirms
  interactively or sets `CCB_ALLOW_ROOT_INSTALL=1`.
- Existing installer selects Python 3.10+, creates managed venvs for release
  installs where requested, and writes Python wrappers for Python entrypoints.
- Existing installer runs installed entrypoint smoke checks:
  `$BIN_DIR/ccb --print-version` and `$BIN_DIR/ask --help`.
- Existing install/update flows make Neovim and Role Pack provisioning optional
  by default and skip them in non-interactive mode.
- Existing Role Pack catalog status can detect `current`, `available`,
  `update_available`, and `installed_source_missing`.
- Existing Role Pack implementation canonicalizes `ccb.archi` input to
  `agentroles.archi` in most runtime and CLI paths.
- Captured the v7.2.9 post-update `ccb.archi` failure in
  [history/v729-rolepack-update-failure-2026-06-04.md](history/v729-rolepack-update-failure-2026-06-04.md).
- `ccb update` now delegates post-update Role Pack and Neovim provisioning to
  the newly installed `ccb __post-update` entrypoint after tarball install and
  entrypoint verification; subprocess provisioning failure is reported as a
  warning without failing the core update.
- Post-update delegation now prefers the installed bin wrapper or explicit
  `CODEX_BIN_DIR` so managed Python environments stay in effect; forced
  provisioning failures can still fail the update.
- Added Role Pack legacy store canonicalization for installed `ccb.archi`
  metadata, including safe canonical metadata repair under `agentroles.archi`
  and fallback to the catalog source when old `source_path` values are gone.
- Added regression coverage that catalog `current` Role Packs do not call
  update hooks, and that inherited `ccb-config` docs use `ccb.archi` only as a
  legacy alias.
- `install.sh install` now refuses a temporary `CODEX_INSTALL_PREFIX` when
  `CODEX_BIN_DIR` is outside the same temporary prefix or temporary HOME,
  preventing release smoke installs from rewriting the user's real stable
  `ccb` wrapper by accident.
- npm runners now attest package ownership to the vendored Python process.
  Ordinary `ccb update` and startup update acceptance validate that
  provenance, print the exact `npm install -g @seemseam/ccb@<target>` action,
  and leave the vendored release untouched. The runner retains strict equality
  between the package manifest and payload `VERSION`, so the next invocation
  neither downgrades nor repeats an immediate startup/relaunch loop.
- 2026-07-22 verification for npm ownership: `92` update/install/package tests
  passed, `npm pack --dry-run` produced the expected 19-file package surface,
  and the final cross-feature affected suite passed `418` tests. Explicit
  update keeps inner `VERSION` byte-identical; startup acceptance defers the
  prompt, does not invoke tarball update, and does not relaunch.
- Provider CLI updates are centralized under explicit `ccb update`: managed
  Codex, Claude, Gemini, Droid, AGY, OpenCode, MiMo, Grok, Pi, and common
  Node update-notifier paths suppress provider-owned startup prompts;
  installed npm/native/Homebrew owners are detected conservatively; prompt,
  check, all, decline, selection, and exact-version mute behavior is
  supported; accepted updates are version-verified without restarting active
  panes. The 2026-07-23 gate passed 448 related update/provider regressions,
  31 release packaging/entrypoint tests, external `ccb_test --diagnose`, and a
  real report-only provider scan.
- Retired project-scoped Claude/Gemini Provider caches from the runtime path:
  Claude now uses the user-installed executable with self-update disabled,
  Gemini uses one user-scoped rebuildable npm/XDG cache, recognized legacy
  Claude links are detached without deleting payload during startup, and
  stopped-project cleanup owns payload removal. The 2026-07-23 gate passed 496
  broad provider/storage/CLI regressions, 79 phase-2 entrypoint tests, an
  external real Claude+Gemini mount/warm-start/kill smoke, current-project
  cleanup, explicit orphan cleanup, and active-backend refusal. Claude's pane
  resolved to the user installation at
  `/home/bfly/.local/share/claude/versions/2.1.206`. The repository-wide run
  reached 5933 passed and 15 skipped with one unrelated OpenCode shutdown
  `ENOENT`; that exact test passed when rerun alone.
- Added bounded post-update cleanup for that retired cache route. The newly
  installed `ccb` owns the migration; simultaneous update windows are
  deduplicated by a user-level lock, stopped current projects and verified
  deleted-project buckets can be cleaned immediately, active/existing projects
  are deferred to their next successful `ccb kill`, and unsafe content is
  preserved. `--no-cache-cleanup` is the per-update opt-out, migration state is
  recorded under the user state directory, cleanup failures never fail the
  core update, and required-provisioning rollback skips cleanup.
  The 2026-07-23 gate passed 636 update/provider/storage/kill/install/release
  regressions, 11 repository-hygiene checks, syntax compilation, and whitespace
  validation. An isolated external `ccb_test` smoke reported
  `cleanup_legacy_provider_cache:deleted=1` after kill; the parent-authorized
  post-update runner removed one manifest-valid orphan, preserved unknown and
  user-scoped cache content, wrote migration state, emitted Chinese output, and
  honored the per-run opt-out.
  The final clean-environment repository run passed 5806 tests with 2 skips and
  zero failures.

## In Progress

- 2026-10-29, implemented locally: explicit `ccb update` provider discovery
  now includes Bun when OMP is installed, ahead of the OMP candidate. Native
  user `.bun/bin/bun` installations use `bun upgrade` through existing
  prompt/check/all/none and version-mute controls; unknown package-manager
  owners remain report-only. Successful updates must pass a version probe.
  Failed CLI probes no longer parse dependency-error versions; OMP can fall
  back to its installed package manifest when old Bun prevents startup.
  Validation: 120 provider/update/startup-update tests passed, including
  runtime ordering, broken OMP discovery, check-only behavior, ownership,
  install-home isolation, and failed post-upgrade verification. Release and
  installed-update deployment remain pending. The 2026-11-19 v8.7.23 candidate
  passes 311 focused regressions with 2 opt-in skips; see
  [release qualification](evidence/v8.7.23-release-20261119.md).

- Define no-repeat provisioning contracts for dependencies and Role Packs.
- Define Chinese/English prompt coverage for shell installer and Python update
  paths.
- Extend update-tarball smoke isolation beyond the shell installer gate. A
  2026-06-15 audit found real user `ccb` pointing at
  `/tmp/ccb-v7.2.1-install-smoke/prefix/ccb`, with multiple live daemons using
  that temporary prefix; the direct install path is now guarded.

## Next

1. Complete broader Role Pack provisioning idempotency by status:
   `update_available` updates exactly once and missing catalog is a warning
   unless required.
2. Make role-owned tool hooks idempotent by tool manifest/version so unchanged
   Architec or future tools are not repeatedly installed.
3. Consolidate update/install prompts behind shared i18n message helpers for
   Chinese and English.
4. Add automated tests for every scenario in
   [topics/environment-and-scenario-matrix.md](topics/environment-and-scenario-matrix.md).
5. Add update-level temporary-prefix isolation tests for release simulations:
   when `HOME`, `XDG_*`, `CODEX_INSTALL_PREFIX`, and `CODEX_BIN_DIR` point at a
   disposable location, update must not mutate the real user's
   `~/.local/bin/ccb`, shell startup files, or live project daemons.
6. Add a real upgrade runbook that starts from an older released version with
   installed legacy `ccb.archi`, updates to the new release, and confirms no
   user-facing Role Pack error.

## Deferred

- Windows-native managed update.
- Signed installer/update manifests.
- Global background dependency update checks.
- Installation of missing provider CLIs and unsafe/unknown provider package
  owners; current work updates only already-installed providers with a
  verified safe adapter.
- Automatic cleanup of obsolete installed Role Pack digest versions.
