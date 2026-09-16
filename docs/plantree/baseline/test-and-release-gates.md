# Test And Release Gates

Date: 2026-05-25

## Core PR Maintenance Requirements

Latest review evidence: [PR350 local integration](evidence/pr350-local-integration-20260916.md).
Earlier PR review: [PR349-350 review](evidence/pr349-350-review-20260914.md).
Previous evidence: [PR340-344 review and repairs](evidence/pr340-344-review-20260909.md).

Owner reaffirmation: 2026-09-07. These are mandatory development, PR review,
merge, and release-maintenance constraints. They record required behavior,
not a claim that all existing code has already passed an audit.

### One-Way Provider Inheritance

- CCB must not reverse-manage Providers. External Provider configuration,
  authentication, account selection, and environment are inheritance sources;
  the direction is external Provider state to CCB-owned managed state only.
- CCB configuration, refresh, clear, kill, cleanup, logout, and uninstall must
  not write back, repair, overwrite, delete, or invalidate external Provider
  state. Explicit CCB overrides stay local to the selected CCB Agent.
- Review remote effects as well as local writes: copied rotating OAuth tokens
  do not create independent authority, and managed refresh/revocation must not
  invalidate the user's external login.
- Provider-related PRs must identify source and destination ownership and
  supply relevant regression evidence that external state remains unchanged.
  Unproven ownership or reverse mutation blocks merge.

Detailed authority: [Provider auth authority plan](../plans/provider-auth-authority/README.md)
and [one-way external authority decision](../plans/provider-auth-authority/decisions/001-one-way-external-authority.md).

### Windows And Herdr Isolation

- Keep Windows/Herdr implementation in approved Windows-owned surfaces,
  primarily `lib/platforms/windows/` and `platforms/windows/`, with scoped
  Windows tests and planning documents. Do not contaminate Linux/macOS,
  shared runtime, generic clients, installers, dependencies, or tests.
- Existing shared-to-Windows imports are a frozen debt ceiling. New reverse
  dependencies are forbidden; a platform guard alone does not exempt a
  shared-file change from ownership review.
- Separate Windows functional PRs from global release metadata changes,
  including `VERSION`, root READMEs, changelog, npm metadata, and release notes.
  Global release changes require a separately reviewed release PR.
- Require the trusted-base isolation gate against the actual PR base/head,
  plus relevant Windows tests and non-Windows regression evidence. The PR
  cannot weaken its own checker or workflow to pass. Record the revisions,
  gate result, and any unavailable platform verification in the review.
- Isolation violations block merge; a successful Windows build does not
  establish Linux/macOS isolation.

Detailed authority: [Windows PR isolation decision](../plans/windows-native-release/decisions/003-windows-pr-diff-isolation.md)
and [trusted-base native-only gate](../plans/windows-native-release/decisions/004-trusted-base-native-only-gate.md).

## Documentation Gates

- Markdown links introduced by the README refresh resolve locally.
- `README.md` and `README_zh.md` remain content-parity peers.
- Asset references load from the repo and include useful alt text.
- The README does not require private paths, private API keys, or local-only
  project names to understand examples.

## Config Example Gates

- Compact layout examples align with
  [ccb-config-layout-contract.md](../../ccb-config-layout-contract.md).
- `version = 2` windows examples do not mix legacy `cmd` with `[windows]`.
- Agent-local `key`, `url`, and `model` examples follow the shortcut contract.
- Worktree examples state the git repository requirement.

## Media Gates

- New screenshots and animations are captured from current v7 behavior or are
  explicitly labeled as mockups.
- Public media hides API keys, local tokens, private prompts, and irrelevant
  machine-specific paths.
- Each animation has a static fallback or nearby prose summary for readers who
  cannot view animated media.
- File sizes are checked before committing to avoid making the README slow to
  load.

## Suggested Verification

These are candidate gates for the README implementation phase; exact commands
should be confirmed before use:

- Markdown link check.
- README image path check.
- `ccb config validate` against documented config snippets if the command is
  available in the installed release.
- A smoke start against a temporary project when producing real screenshots or
  videos.

## Source Runtime Isolation Gates

- Source changes are validated with
  `/home/bfly/yunwei/ccb_source/ccb_test` from the dedicated default external
  project `/home/bfly/yunwei/test_ccb2`.
- Any other external source-test project must be explicitly allowed with
  `CCB_TEST_ROOTS` or `CCB_SOURCE_ALLOWED_ROOTS`; legacy sibling directories
  such as `test_ccb` and `ccb_test2` are not default roots.
- Stateful source validation does not run from
  `/home/bfly/yunwei/ccb_source`, and `ccb_test --project` does not point at a
  path inside that checkout.
- Runbooks use the absolute source `ccb_test` wrapper or first record
  `command -v ccb_test` plus `readlink -f` so stale release/smoke wrappers on
  `PATH` cannot be mistaken for current source validation.
- `ccb_test --diagnose` reports the wrapper path, source `ccb`, effective
  roots, checked paths, and source-test allowance before stateful validation
  when wrapper or root selection is uncertain.
- Provider/account state for source runtime validation is isolated with
  `HOME=/home/bfly/yunwei/test_ccb2/source_home` and
  `CCB_SOURCE_HOME=/home/bfly/yunwei/test_ccb2/source_home`, unless the test
  intentionally covers inherited real provider configuration.
- `.ccb/agents/*` and `.ccb/ccbd/*` under the source checkout are treated as
  installed-release work-environment runtime state, not disposable source-test
  artifacts.
- `CCB_SOURCE_RUNTIME_OK=1` is a diagnostics-only override and must not be used
  for ordinary source validation.

## Continuous Integration Topology

The active CI gate optimization and ownership map is maintained in
[ci-test-gates](../plans/ci-test-gates/README.md). Its governing rule is to
preserve each behavioral and platform boundary while avoiding repeated full
suite execution for every OS-by-Python combination. Timing-sensitive tests
must use deterministic synchronization rather than retries or relaxed
assertions.
