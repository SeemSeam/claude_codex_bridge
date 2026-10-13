# Agentic Loop Workflow Implementation Status

Date: 2026-07-14
Status: In progress - Planner readless provider enforcement repair required
Branch: `main`
Current code candidate: `62753d63791f8b644ee6f5f5433fe57070fb2c84`

## V2 compatibility repair — 2026-10-13

Source and installed-tree tests pass for explicit workflow gating; live daemon
activation awaits a user-controlled restart after the active turn. See
[evidence/v2-workflow-isolation-20261013.md](evidence/v2-workflow-isolation-20261013.md).

## Current Phase

The current-main source/fake baseline remains accepted at `2c936a48`, with
`4992 passed, 2 skipped`. The Phase2 dependency-assembly repair landed as
`62753d63`; its pinned short-path regression gate now passes all 83 Phase2
tests and leaves zero residue.

Fresh provider admission is paused on a higher-priority Planner permission
defect. The readless, reply-only Planner RolePack still projected readable
file-backed skills; Codex exposed `exec_command`, and Claude exposed `Read` and
`Bash`. Required `deny_all_except` therefore was not enforced by either real
provider session. Provider hard-enforcement and the Planner verification
contract must be repaired and refrozen before another real lane.

The release target remains one visible Frontdesk-started lane, one semantic
orchestration bundle, and one to four independently reviewed
`Coder + Code Reviewer` workgroups. Codex is primary and Claude secondary;
providers without authenticated local state are outside the real gate.

The active release target is single-lane production closure.
The branch is not production-ready for that workflow target; the accepted R1 foundation remains
bounded by [single-lane-r1-authority-runtime-closure-20260711.md](history/single-lane-r1-authority-runtime-closure-20260711.md)
and does not waive the current provider and installed-candidate gates.

## Authority

- Release goal: [single-lane-multi-workgroup-release-goal.md](goals/single-lane-multi-workgroup-release-goal.md)
- Decision 029: [029-planner-feedback-and-task-set-closure.md](decisions/029-planner-feedback-and-task-set-closure.md)
- Historical Phase 1-6 bounded acceptance: [phase1-6-acceptance-report-20260705.md](history/phase1-6-acceptance-report-20260705.md)
- Source acceptance: [g6c-current-main-source-acceptance-20260714.md](history/g6c-current-main-source-acceptance-20260714.md)
- Wiring/provider diagnostic: [g6d-phase2-wiring-real-provider-diagnostic-20260714.md](history/g6d-phase2-wiring-real-provider-diagnostic-20260714.md)
- Planner permission diagnostic: [g6e-planner-readless-provider-projection-diagnostic-20260714.md](history/g6e-planner-readless-provider-projection-diagnostic-20260714.md)

Scripts own task, task-set, revision, closure, integration, topology, round,
release, and delivery authority. Provider replies remain evidence, not state
authority.

## Last Landed

- `2c936a48` closes release identity and rollback-backup safety.
- `62753d63` explicitly wires `loop_runner_auto` and
  `frontdesk_intake_command` into both production Phase2 builders.

## Active TODO

1. Enforce Planner readless/deny-all behavior for Codex and Claude or fail
   mount honestly; inline its complete reply contract and remove parser drift.
2. Recompute all RolePack/provider projection digests and pass deterministic
   projection, launch, parser, and zero-tool provider-stub gates.
3. Run fresh visible Codex-primary C1/C2 and qualified Claude-secondary C3
   projects, including route mix, rework, restart, busy-retain, sidebar
   pressure, task-set closure, release, delivery, and zero residue.
4. Repeat each exact available weaker model/profile/RolePack digest at least
   five times; do not invent identifiers or substitute transport for model
   family qualification.
5. Run the terminal full source suite and G7 unused-version package, install,
   update, rollback, and installed-candidate gates; keep G8 separately authorized.

## Blocked By

Production/default enablement remains gated by Planner hard permission
enforcement, fresh visible provider evidence, the terminal full source run,
and the package/update/rollback gate. Codex `gpt-5.4` is only a live candidate;
the current Claude transport resolves to third-party `deepseek-v4-pro`, so the
Claude-family strong-model lane remains `ENV_UNMET`. The
installed `v8.1.4` control plane is not accepted for important worker dispatch
after `ccb clear`; source `ccb_test` worker pools must be used until an
installed candidate contains the exact-anchor session fix.

## Acceptance Ownership

Workers execute bounded implementation and test blocks; `mother` reviews Role
boundaries and RolePack consistency. `talk2` owns dependency ordering, raw
evidence and diff review, acceptance decisions, cleanup, landing, and next-step
routing. Real-provider acceptance uses inherited Codex/Claude state and a
project-local `AGENT_ROLES_STORE`.

## Last Verified

- Callback fixture `source-callback-fixture-c725-20260714T110602Z`: `5 passed`,
  two non-empty exact-anchor replies, zero residue.
- Rejected real lanes at
  `/home/bfly/yunwei/test_ccb2/real-provider-visible-c1-codex-20260714T112158Z`
  and `/home/bfly/yunwei/test_ccb2/real-visible-c3-claude-20260714T112344Z`.
  Both exposed the wiring defect and forbidden Planner tools, then were
  force-unmounted with zero project-owned residue.
- Wiring repair root `auto-runner-wiring-job_acf28692264d`: pre-fix `3 failed`;
  post-fix `5 passed`, nearby `16 passed` and `5 passed`, CLI exit-zero/idle.
- Pinned Phase2 root
  `/home/bfly/yunwei/test_ccb2/phase2-pinned-gate-123ee43c-20260714T115921Z`,
  `83 passed`, fixed detached HEAD, short paths, zero residue.
- Model qualification is partial: only Codex `gpt-5.4` has old live evidence;
  no Claude-family exact ID is proven. G7 preflight rejects occupied `8.1.4`
  and `8.1.5`; the current minimum patch candidate is `8.1.6`, pending a final
  recheck.

## Non-Claims

This status does not accept Planner enforcement, fresh provider workflows,
weak-model stability, the terminal full source suite, installed-package
acceptance, production/default enablement, publication, or a release tag.
