# V2 workflow isolation repair — 2026-10-13

Mode: execute-ready. Scope: V3 workflow interception of ordinary V2 dispatch.

## Decision and acceptance

Loaded ProjectConfig.workflow with mode agentic-loop is the sole authority for
automatic Frontdesk admission, Detailer replan admission, their startup journal
recovery, and the Frontdesk terminal-response boundary. Names, Planner presence,
old journals and executable installation paths cannot enable those policies.
V2 normal routing and sender validation remain authoritative. Explicit V3
workflow retains its strict Planner/silence/task-id and completion checks.

## Evidence

The unpatched installed CCB3 library reproduced the exact DispatchError in a
standalone Python process for a V2 Frontdesk -> Executor submission with no
Planner. The regression was then verified against both patched installed trees.

- 346 tests passed: dispatcher, Detailer handoff, message bureau integration,
  daemon config drift/startup wait, Phase6B Frontdesk runner.
- 363 tests passed: loop capacity CLI, Orchestrator RolePack, RolePacks,
  daemon keeper runtime.
- After adding corrupt Detailer journal coverage, 14 focused tests passed.
- Each installed tree passed standalone execution of 12 V2 admission/recovery
  cases (three sender names, silent/normal, Planner present/absent), both V2/V3
  completion cases and the strict V3 direct handoff positive case.
- These are real installed-code dispatcher/storage tests, not live model or
  end-to-end mounted-daemon delivery claims.

## Local installation repair and rollback

Both ~/.local/share/ccb2/app and ~/.local/share/ccb3/app receive the five narrowly
scoped runtime files. Fourteen generic launchers/symlinks overwritten by the
CCB3 installer are restored to matching existing CCB2 targets. The explicit
ccb3 preview entrypoint remains separate. No provider settings/authentication,
project config or runtime authority files are modified.

Backup manifest and original files:
/home/bfly/.local/share/ccb-maintenance-backups/v2-workflow-isolation-20261013-105636

The current project is version 2 but daemon PID 2703160 was launched from
CCB3. It remains running with imported old modules. Installed-file verification
does not establish live activation. A user-controlled project restart after the
current turn is still required; do not autonomously stop the active project or
replace panes. ps/queue showed five agents, zero queue/pending replies; fault
list was empty. No commit, push or release was performed for this repair.

## Remaining limits

The existing CCB2 installation reports v8.6.11 with channel v2-maint. Restoring
its entrypoints is not an upgrade or a new release. A future isolated CCB3
install must use its own bin directory; the current installer permits explicit
non-temporary global bin selection, so operator isolation remains necessary.
This repair covers confirmed automatic dispatcher interference and the global
entrypoints, not a claim that all historical V3 files have been removed.
