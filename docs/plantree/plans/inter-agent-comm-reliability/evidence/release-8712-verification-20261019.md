# v8.7.12 publication verification

Date: 2026-10-19
Status: published on GitHub and npm; fresh installation verified.

## Identity and included work

Tag v8.7.12 points to `3a2b7770e0d0590deade5276c4fef2cc7a6ab9ef`.
The same exact candidate was advanced to main without force. REST Git object
upload preserved each local commit/tree identity after ordinary Git transport
stalled; the published annotated tag object is
`360acc1910c500df1a7f4d7a8431fd8852fec240`.

Includes PR #395 and its reviewed pending-job/lease and composer-padding repairs.
PR #386 is already present in v8.7.11; its V2/V3 routing and recovery slice passed
219 tests again. Platform-owned maintenance from the previously merged PR #384
is retained. Shared source, public release metadata and platform-owned metadata
were reviewed separately through PRs #396, #397 and #398; all trusted-base
ownership gates passed. The stacked metadata PRs were closed after their exact
heads became ancestors of main; the source PR is marked merged.

Existing README consolidation was saved in `ab71a34e2` but restored to the remote
baseline for this release. Moving old platform descriptions triggered ownership
classification; no gate policy was changed. Mobile planning documents are retained
as planning only, without runtime or authorization changes. Local `.zai` settings
are excluded, and external Provider authentication/configuration is untouched.

## Candidate qualification

- Full local regular lane: **7793 passed, 40 skipped, 42 deselected**, 391.73 s.
  The excluded specialist lanes were exercised by the hosted candidate gates.
- The initial local run used an incomplete subprocess PATH and also exposed
  unsynchronized CLI/platform metadata. All 416 relevant follow-up checks passed
  after environment correction and metadata synchronization; the full lane above
  then passed on the final candidate.
- Tests [39221108644](https://github.com/SeemSeam/claude_codex_bridge/actions/runs/39221108644):
  all Python compatibility/macOS, lifecycle, provider blackbox, helper, installer
  and Required test gate checks passed.
- Real-platform [39221112949](https://github.com/SeemSeam/claude_codex_bridge/actions/runs/39221112949)
  and cross-platform [39221116741](https://github.com/SeemSeam/claude_codex_bridge/actions/runs/39221116741): passed.
- Native build-only [39221120786](https://github.com/SeemSeam/claude_codex_bridge/actions/runs/39221120786):
  339 Python tests, two launcher tests, archive installation and command smoke passed.
- A locally built Linux archive matches the candidate's repaired sources and runs
  version 8.7.12. One hundred delivery/composer tests pass against archive source.
- Earlier candidates and their failed/stopped runs are not acceptance evidence.
  Redundant candidate runs were cancelled in favor of the exact final revision.

## Public delivery and independent verification

- [Release](https://github.com/SeemSeam/claude_codex_bridge/releases/tag/v8.7.12)
  is stable/latest with ten assets and committed equivalent English/Chinese notes.
- [npm](https://www.npmjs.com/package/@seemseam/ccb/v/8.7.12): version and latest
  dist-tag both resolve to 8.7.12.
- All tag publication workflows passed: artifacts `39223264017`, native package
  `39223264029`, Sidebar `39223264109`, and npm `39223264054`.
- Downloaded public Linux archive and Mobile manifest match SHA256SUMS. Linux
  BUILD_INFO reports the correct source prefix `3a2b777`; the Mobile manifest
  reports version 8.7.12 and build 8070012. The public archive's seven-character
  commit prefix differs in length from the local archive's nine-character prefix;
  both resolve to the same exact qualified commit.
- A normal fresh npm install into a temporary prefix succeeded, provisioned its
  managed Python 3.14.4 and dependencies, and reported `v8.7.12`. Installed repair
  and composer source files exactly match the candidate. The same 100 regressions
  passed against that freshly installed payload.

The main push triggered additional same-source checks after the already successful
candidate qualification; these may still run when this record is committed.
No active user project was restarted. Upgrade activation remains a user-controlled
safe project reopen, as stated in the published notes.

Raw evidence: `/var/tmp/ccb-8712-*`; clean release worktree:
`/var/tmp/ccb-release-8712-20261019`.
