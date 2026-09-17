# Agent Working Visibility

Role: topic-capsule
Date: 2026-09-16
Status: Prior functional increment verified; visual revision in progress

Current execution authority: [Agent state colors](agent-state-colors.md).
User feedback supersedes the blue-dot-only appearance; evidence below records
the prior functional increment, not acceptance of the new visual requirements.

## Scope and observed problem

User requests distinct working colors for agents, windows, and home projects,
with Android emulator inspection, implementation by demo, and independent review.
Current checkout is `/home/bfly/yunwei/ccb-v2`; preserve existing PR350 repairs.

Emulator observations are stored locally at
`/var/tmp/ccb-mobile-ui-adZJ4d/{home,detail,agents}.png` (not durable artifacts).
The installed debug app offers an 8.6.17 update; screenshots are UX evidence,
not proof that installed code equals current source.
Home already highlights some working projects but only labels runtime health.
Source WindowSwitcher accepts unread state but no working state; AgentSwitcher
uses tertiary for both working and focused agents. Collapsed selector has no
working aggregate. Long names consume the horizontal selector; the collapse
handle is a small target. Backend freshness is not yet proven defective.

## Implementation contract

- Reuse authoritative agent execution classification; `active` focus alone,
  unread messages, or stored pending text must not imply actual execution.
- Distinguish working with a consistent theme-aware accent and visible text;
  selection uses a separate check/outline treatment. Unread stays independent.
- Aggregate working agent counts by window, including nonselected windows;
  collapsed selector keeps working context visible without expanding.
- Home shows working count when known, generic working text when only the
  boolean exists. Retain health as secondary information. Simplify redundant
  working decoration; do not add continuous flashing or unnecessary motion.
- Keep narrow/wide layouts consistent, cap long labels with full-name access,
  and provide usable touch targets (48 logical pixels where interactive).
- Verify activity events and snapshots update agent/window/project displays;
  fix a freshness bug only with a regression demonstrating it. Preserve host,
  project, namespace and event ordering isolation. No new full-project polling.
- No auth/provider lifecycle, Windows, release metadata, storage schema or
  conversation parser changes. Preserve `.zai/` and unrelated changes.

## Acceptance

Widget coverage: working/idle/failure, selected idle vs unselected working,
working plus unread, multiple windows and collapsed selector, count reset,
long names, narrow width, enlarged text, light/dark theme and reduced motion.
Verify busy-to-idle/failure and reconnect/stale events without cross-host or
namespace bleed. Maintain existing no-idle-polling and multihost tests.
Run Flutter analyze, targeted regressions and full Flutter suite. Any backend
change requires relevant Python tests and regression evidence.
Agent1 independently reviews, builds/installs into existing paired emulator,
and records screenshots/interaction and controlled transition evidence without
sending disruptive messages to real busy agents. No release/commit is implied.

## Deferred suggestions

Consider searchable agent selection for very large lists, optional home
working filter, and clearer project title/path hierarchy after this increment.
Do not expand into a navigation redesign or alter native transcript content.

## Rollback

Changes remain reviewable local frontend/test edits, with no data migration.
Restore only this scoped diff if acceptance fails; retain pairing and runtime.

## Execution record (demo, 2026-09-16)

### Independent review checkpoint (agent1)

Second checkpoint: independently reran full suite (833 passed, 1 skipped),
analyze clean, debug build/install successful. On emulator-5554, real ccb-v2
shows idle selected demo, working unselected agent1 with unread star, and
main window count 1 (`final-agents.png`). However folding the actual page
removes ALL working context (`final-collapsed.png`). The production caller
in `project_home_scaffold_host.dart` uses `_MobileCollapsedProjectBar`, not
`MobileAgentSwitcherPanel(collapsed: true)`; the latter is only covered by
component fixtures. This is an acceptance blocker: implement the working
aggregate in the actual host and add host-level regression/transition tests.
Screenshots are under `/var/tmp/ccb-mobile-ui-adZJ4d/`; full test output is
`/var/tmp/ccb-mobile-final-tests.log`. No busy agent was interrupted or sent
test input. Actual provider lifecycle transition has not been forced.

- Independently ran targeted tests: 27 passed; analyze: no issues.
- Built debug APK using `mobile/tools/mobile_toolchain_env.sh`, installed with
  `adb install -r` on emulator-5554 retaining pairing. Home now displays
  `1 working`; long agent labels visibly truncate. Local screenshots:
  `/var/tmp/ccb-mobile-ui-adZJ4d/review-home.png` and `review-agents.png`.
- Added a failing regression `review: unread window retains agent.window
  membership fallback`: nonselected window unread disappears when explicit
  membership is empty but agent.window supplies valid membership. Restore
  `agentsForWindow` aggregation rather than iterating window.agents alone.
- Working suffix shares the ellipsized text with long window/agent names in
  window and collapsed controls: reserve visible space for the status.
- Agent chip still communicates working only through color/dot, with name-only
  tooltip. Add readable status and semantics independent of color.
- Revisit normal-height 48px touch targets with constrained keyboard layout
  handled adaptively; tests currently measure container rather than hit area.
- Remaining acceptance: corrections, full-suite rerun, controlled all-level
  busy-to-idle/failure transitions, and final emulator inspection.

### Files

- `mobile/app/lib/widgets/working_status_style.dart` (new): canonical
  theme-aware `workingStatusAccent` (dark `0xFF59DFFF` / light `0xFF0077CC`,
  previously only the home row pair) plus `workingStatusCountLabel`.
- `mobile/app/lib/features/project_home/agent_window_switchers.dart`:
  AgentSwitcher — working dot icon + accent border; selected idle gets
  `check_circle` + primary; bare `active` focus is neutral `onSurfaceVariant`
  (tertiary no longer double-books). WindowSwitcher — new
  `workingAgentCounts` map, accent border + `· N` label span + tooltip.
  Labels capped (agent 120 / window 150) with ellipsis + tooltip for full
  names. Rows scale with text (`max(44, scaled)`).
- `mobile/app/lib/features/project_home/mobile_agent_switcher.dart`: per-window
  counts from `agentsForWindow` + `agentHasSourceWorkingActivity` (all
  windows); collapsed summary appends accent `· N working` and tints the
  leading icon; collapse handle enlarged 8→32 px tall, 72 px wide.
- `mobile/app/lib/features/project_home/wide_agent_column.dart`: same accent
  and icon matrix for the wide layout.
- `mobile/app/lib/features/project_home/project_list.dart`: row accent
  delegates to the canonical helper; removed the 6 px left stripe and the
  `WorkingAttentionBeat` strip (tint + single border remain, no motion);
  avatar tinted when working; live tile trailing shows working count (agent
  count when idle).
- `mobile/app/lib/features/project_home/project_home_scaffold_host.dart` and
  `project_home_multi_host_list.dart`: server/multihost tiles show
  `workingStatusCountLabel(project.workingAgentCount)` (`"N working"`, or
  `"working"` for boolean-only legacy projects) above the retained health
  line. Backend already ships `working_agent_count`
  (`lib/mobile_gateway/service.py`), no server change needed.

### Freshness chain

Traced `_handleGatewayInvalidationEvent` → `_recordGatewayAgentActivityOverride`
→ `_applyGatewayAgentActivityOverride` → view rebuild →
`_rememberProjectActivity`/`_workingProjectIdsFor`; and
`projectSummaryChangedKind` (no active project) → `_fetchServerProjects`.
Override keys are host+project+agent with `namespaceEpoch` matching and a
snapshot-predates-event guard. No regression reproduced in
`project_home_view_refresh_test.dart`,
`project_home_view_refresh_widget_test.dart`, or
`project_home_task_completion_widget_test.dart`, so per contract no freshness
or server change was made and no polling was added.

### Tests

- New `mobile/app/test/agent_working_visibility_widget_test.dart` (11 tests):
  working/idle/failed chips, selected-idle vs unselected-working, working+unread,
  per-window counts incl. nonselected, collapsed summary, count reset,
  long-name cap + tooltip, 320 px narrow + 1.6× text without overflow,
  44 px rows / 32 px handle targets, light and dark theme accents, server tile
- Targeted regressions green (85 tests across scaffold host, layout,
  multihost aggregation, view refresh, task completion, server projects,
  working suites); `flutter analyze` clean; full `flutter test` suite green
  (826 passed, 1 skipped, 0 failed) on Flutter 3.44.2.

### Review fixes (demo, 2026-09-16, after agent1 checkpoint)

1. `_unreadWindowNames` membership restored to `agentsForWindow`, so agents
   that only declare `agent.window` still mark their window unread;
   agent1's red test `review: unread window retains agent.window membership
   fallback` now passes unmodified.
2. Window chips and the collapsed summary split name and status into separate
   children: the label ellipsizes (max 140 px chip / Expanded) while the
   working count keeps its own never-truncated box (`TextOverflow.visible`,
   `softWrap: false`, FittedBox scale-down in the collapsed bar). Geometry
   tests assert the status rect lies inside the chip/bar rect with the long
   label present, not just `find.textContaining`.
3. Agent working state is readable: chip tooltip `"<name> · working"`, a
   `Semantics(container: true, label: 'working')` on the dot, mirrored in the
   wide column tile. working+selected asserts chip fill + accent border + dot
   + tooltip + semantics simultaneously; unread star stays independent.
4. Adaptive touch targets: chips use `materialTapTargetSize.padded` with 48 px
   rows whenever vertical space allows; below 480 px available height (keyboard
   open, short landscape) rows compact to 40 px with shrink-wrap. Tests assert
   real chip render-box heights (≥48 normal, ≥32 compact) and the composer
   keyboard-insets regression stays green. Collapse handle remains 32×72.
5. New `running to idle and failed clears every working layer` test drives one
   stateful fixture through agent chip, both window chips, collapsed summary,
   and home tile (count key + row highlight) with unread stars asserted after
   each transition.

Evidence: `flutter analyze` clean; targeted cluster green (75 tests); full
`flutter test` suite green (833 passed, 1 skipped, 0 failed) on Flutter 3.44.2.
agent1's same-source APK screenshot `review-home.png` under
`/var/tmp/ccb-mobile-ui-adZJ4d/` shows "1 working" on working home cards;
idle cards omit the working label. All touched Dart files formatted with
`dart format`.

### Deviations

- Collapse handle is 32×72 px (was 8 px): a 48 px handle inflated the panel
  enough to squeeze the chat timeline; accepted as the one sub-48 target.
- Working dot is 12 px inside a 24 px avatar; selected+working shows the dot
  (selection remains visible via the chip fill and accent border).

### Second checkpoint resolution (demo, 2026-09-16)

agent1's blocker confirmed: the production collapsed surface is
`_MobileCollapsedProjectBar` (project_home_scaffold_host.dart), while
`MobileAgentSwitcherPanel(collapsed: true)` had no production caller —
component-green did not mean user-visible. Resolved as follows; earlier
"full suite green" claims above are read as component-level evidence only.

- New shared `mobile/app/lib/features/project_home/working_status_summary.dart`:
  `workingAgentCountForView` (authoritative classification across all windows)
  and `WorkingCountSummary` (accent text, own never-truncated
  `FittedBox.scaleDown` box, `Semantics 'N working'`, key
  `mobile-agent-switcher-working-count`). Both the real host bar and the panel
  collapsed branch render this one component, so the surfaces cannot drift.
- `_MobileCollapsedProjectBar` now shows the cross-window working count:
  summary line is `Row[Expanded(ellipsized summary), WorkingCountSummary]`,
  leading icon takes the working accent, fixed 60 px height relaxed to a
  60 px minimum so large text grows the bar. Provider control / refresh /
  terminal / overflow actions and the unread star are unchanged and asserted.
- `MobileAgentSwitcherPanel` collapsed branch keeps no private status markup
  (shared component only). The branch itself stays for API/test compatibility
  and is documented as non-production; the production path is the host bar.
- Red-first host tests in project_home_scaffold_host_test.dart, both failing
  on current code before the fix: `collapsed host bar keeps cross-window
  working status visible` (count text + geometry rect-inside-bar + action row
  + unread + expanded per-window counts for the same host) and
  `collapsed host bar clears working status on idle and failed`
  (running → idle → failed via stateful rebuild, unread retained).
  Fixture `_view` gained `agentActivity`/`mobileQueueDepth` injection.
- Evidence: `flutter analyze` clean; targeted cluster 99 tests green;
  full `flutter test` suite green (835 passed, 1 skipped, 0 failed) on
  Flutter 3.44.2. Touched files formatted with `dart format`.

### Final independent verification (agent1, 2026-09-16)

- Full Flutter suite independently rerun: 835 passed, 1 skipped, no failures.
  Log: `/var/tmp/ccb-mobile-acceptance-tests.log`.
- `flutter analyze --no-pub`: no issues; debug APK built successfully using
  `mobile/tools/mobile_toolchain_env.sh` and installed with `adb install -r`.
  Existing pairing retained; no gateway/provider restart or test message sent.
- Actual emulator-5554 production page inspected: idle selected demo differs
  from working unselected agent1; working agent keeps its unread star; main
  window shows count 1. Tapping the collapse handle now retains `1 working`
  and the unread star in the real host bar. Name truncates independently.
  Local screenshots: `/var/tmp/ccb-mobile-ui-adZJ4d/accepted-open.png` and
  `/var/tmp/ccb-mobile-ui-adZJ4d/accepted-collapsed.png`.
- Idle/failure transitions are verified by controlled widget/host fixtures,
  not by interrupting live provider tasks. Physical-phone qualification and
  extreme accessibility settings are not claimed by this emulator check.
- Local implementation verified; no commit, push or release performed.
