# Install Update Stability Plan

Date: 2026-06-04

## Purpose

Make CCB fresh install and managed update stable across supported user
environments. A successful install or update must leave the `ccb` entrypoint
usable, avoid repeated dependency or Role Pack installs when nothing changed,
handle legacy state without user-facing traceback-style failures, and render
the main user prompts in Chinese or English.

This plan covers installer shell behavior, `ccb update`, post-update
provisioning, managed provider CLI updates, managed tools, Role Packs, and
validation. It is intentionally separate from the Role Pack plan: Role Packs
define role semantics, while this plan defines installation and update
resilience.

## File Map

- [v8.7.23 qualification](evidence/v8.7.23-release-20261119.md): local Bun/OMP
  update and terminal-display changes, package checks and publication receipt.
- [roadmap.md](roadmap.md): implementation sequence and open release gates.
- [topics/install-update-flow.md](topics/install-update-flow.md): end-to-end
  flow from fresh install through tarball update and post-update provisioning.
- [topics/environment-and-scenario-matrix.md](topics/environment-and-scenario-matrix.md):
  supported platforms, install modes, user profiles, TTY modes, network
  states, and expected behavior.
- [topics/dependency-and-role-idempotency.md](topics/dependency-and-role-idempotency.md):
  no-repeat rules for Python packages, managed venv, Neovim, Role Packs, and
  role-owned tools.
- [topics/i18n-output-contract.md](topics/i18n-output-contract.md): Chinese
  and English output contract for installer/update prompts and diagnostics.
- [decisions/001-npm-owns-vendored-payload.md](decisions/001-npm-owns-vendored-payload.md):
  npm remains the sole mutation authority for npm-packaged CCB payloads.
- [topics/validation-runbook.md](topics/validation-runbook.md): automated and
  real-environment validation commands before release.
- [history/v729-rolepack-update-failure-2026-06-04.md](history/v729-rolepack-update-failure-2026-06-04.md):
  incident note for the `ccb.archi` post-update Role Pack failure.

## Related Sources

- [../../../install-runtime-environment/README.md](../../../install-runtime-environment/README.md)
- [../rolepack-system/README.md](../rolepack-system/README.md)
- [../../../ccbd-startup-supervision-contract.md](../../../ccbd-startup-supervision-contract.md)
- [../../../ccb-wsl-compatibility-plan.md](../../../ccb-wsl-compatibility-plan.md)

## Scope

In scope:

- `install.sh install` and `install.sh uninstall`.
- Managed `ccb update` on Linux, macOS, and WSL, including package-manager
  delegation for npm installs.
- Release tarball extraction and staged installer handoff.
- Source/dev install behavior where global wrappers point at a live checkout.
- Python selection, managed venv, entrypoint smoke checks, root/sudo profile
  warnings, WSL confirmation, and tmux/provider prerequisites.
- Optional dependency provisioning: `tomli`, `watchdog`, Droid MCP, Neovim, and
  Role Packs.
- Post-update Role Pack refresh and legacy id migration such as
  `ccb.archi -> agentroles.archi`.
- Suppression of provider-native update prompts inside CCB-managed panes and
  explicit provider version management through `ccb update`.
- Retirement of legacy project-scoped Claude/Gemini caches during upgrade:
  new runtime preparation must not recreate them; the newly installed updater
  removes only bounded, manifest-verified orphan caches and defers active or
  existing projects to their next successful project kill.
- Chinese and English user-visible install/update prompts, warnings, and next
  actions.

Out of scope:

- Windows-native managed update.
- Automatically changing user shell rc files beyond existing PATH guidance.
- Installing missing provider CLIs or rewriting provider-native installers.
- Automatically merging root-owned and normal-user profiles.
- Background automatic Role Pack updates outside explicit install/update
  commands.
- Deleting cross-project provider-cache buckets during normal startup or job
  delivery, or deleting any update-time bucket whose per-Provider manifest and
  missing project root cannot be verified.

## Non-Drift Contract

- Core install/update success must not depend on optional Role Pack, Neovim,
  Droid, or network provisioning success unless the user explicitly requested a
  required install mode.
- Post-update provisioning must run with the newly installed `ccb` code, not
  the old updater process, once the staged installer has completed.
- An npm wrapper owns its vendored payload version. `ccb update` must not
  replace that payload behind the outer package manifest.
- Already-current dependencies and Role Packs must be reported as checked or
  current, not reinstalled.
- Legacy installed state must be canonicalized before provisioning. New writes
  use `agentroles.archi`; `ccb.archi` remains an input compatibility alias
  only.
- Every interactive prompt that affects install/update behavior must have
  Chinese and English text selected by `CCB_LANG` or locale detection.
- Managed update/startup must not recreate
  `~/.cache/ccb/projects/<project-id>/provider-cache`; upgrade migration may
  clean a stopped current project and manifest-valid deleted-project buckets.
  Active and other existing projects remain untouched until their next
  successful `ccb kill`; malformed/unknown/symlinked content remains untouched.
