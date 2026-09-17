# Agent State Colors

Status: Implemented and independently verified locally; not committed or released
Authority: User-approved visual revision, 2026-09-16
Related: [Previous implementation and evidence](agent-working-visibility.md)

## User acceptance correction

Final agent1 verification: 835 passed, 1 skipped; analyze clean; APK built and
installed preserving emulator pairing. Corrected explicit idle precedence over
retained queue/progress hints, wide selected-state fill replacement, unknown
text contrast, and unnecessary truncation of short agent names. Added assertions
for idle precedence and selected wide fill. After the final label-layout change,
33 targeted tests passed and APK rebuilt/installed successfully.
Logs: `/var/tmp/ccb-colors-final-tests.log`, `/var/tmp/ccb-colors-layout.log`.
Actual emulator screenshot `/var/tmp/ccb-mobile-ui-adZJ4d/colors-final.png`
shows green `agent1 Working`, neutral `demo Idle`, independent selection outline,
and window `1 working`; `colors-collapsed.png` verifies the actual folded host
bar's green count. Unread/exception/offline/unknown combinations and transitions
were covered with fixtures; no live provider errors or new tasks were induced.
English was the emulator locale; Chinese labels are covered in widget tests.

The preceding 835-test implementation passed functional checks but the user
could not distinguish work, selection, unread and idle at a glance. This
revision supersedes its blue-outline/dot-only presentation. Preserve existing
uncommitted improvements and regressions; do not revert earlier work.

## Required behavior

- Working agent: green tinted whole label, green border/dot, visible localized
  `name · 工作中` / `name · Working`. Idle: neutral gray and explicit idle text.
- Exception: red tint/border and explicit exception text. Explicit offline:
  subdued appearance and offline text. Missing/unknown evidence: unknown,
  never invent idle from the execution classifier's legacy default.
- Use existing execution evidence for working/failure; distinguish focus
  (`active` bool) from actual activity. Document the display-only mapping and
  precedence, including explicit idle vs old source/reason/queue evidence.
  Do not change backend lifecycle or broaden parser semantics in this task.
- Selection has its own check/indicator; it must not replace status fill.
- Unread is an independent orange badge with accessible unread meaning,
  simultaneously visible on any activity state. Do not alter acknowledgement
  or notification delivery semantics; retain membership fallback behavior.
- Windows display `N 工作中` / `N working`, not bare N, and green working
  emphasis. Real collapsed host bar, home and wide layouts use the same
  working palette and readable counts. Do not invent failure counts at home
  when server data only supplies working totals.
- Localize new labels through existing English/Chinese localization. Keep
  status text visible next to ellipsized names, light/dark contrast, keyboard
  layouts, 320px widths and enlarged text. No new flashing or polling.

## Verification and handoff

Test selected/unselected × working/idle/exception/offline/unknown × unread;
assert actual fills and visible labels, plus state changes without sticky
colors. Include the real ProjectHomeMobileChatScaffoldHost expanded/collapsed
paths, window/home aggregate reset, full-name access, long labels and large
text. Preserve previous unread membership and no-idle-polling regressions.
Run format, analyze and full Flutter suite. Agent1 independently rebuilds,
installs preserving emulator pairing, and checks screenshots/interactions.
No commit, push, release, auth/provider/Windows/storage or unrelated changes.
Rollback is scoped local code only, no data migration. Screenshots and tests
must be identified as live-emulator versus controlled fixture evidence.

## Implementation record (demo, 2026-09-16)

### Files

- `agent_state_display.dart` (new, features/agent_chat): display-only
  `AgentActivityDisplay {working,idle,exception,offline,unknown}` resolver
  with documented precedence — offline evidence first, then exception, then
  working (state sets / queue depth / source-reason text), then explicit
  idle (idle/free/completed/complete/done), else unknown. `agent.active`
  never maps to a state. `agentExecutionStatus` semantics untouched (the
  legacy Idle default is asserted to remain for chat surfaces).
- `working_status_style.dart`: working accent is now green
  (light `0xFF1E8E3E`, dark `0xFF81C995`), plus `agentStateColor`,
  `agentStateFill` (translucent state tints; idle/unknown get neutral
  surface tints), and orange `unreadBadgeColor`
  (light `0xFFE8710A`, dark `0xFFFFB77C`).
- l10n: `agentStateWorking/Idle/Exception/Offline/Unknown`,
  `unreadBadgeLabel`, `agentWorkingCountLabel(n)` (`N working` / `N 工作中`).
- `agent_window_switchers.dart`: agent chips carry state fill on both
  `backgroundColor` and `selectedColor`, a state border (green/red/outline;
  selection adds the primary border and never replaces the fill), a leading
  state icon, and a localized `AgentStateLabel` suffix (own
  `FittedBox.scaleDown` box, `Semantics(container:true, label)` with the
  inner text excluded from semantics to avoid duplicated announcements).
  Window chips show `N working` in green beside the ellipsized label.
  `TaskCompletionUnreadIcon` star is orange with a localized unread
  semantics label, overlaying any state.
- `wide_agent_column.dart`: tiles use `tileColor` state fill, translucent
  selection overlay, leading state icon, and an `AgentStateLabel` trailing.
- Real `_MobileCollapsedProjectBar` (host), `MobileAgentSwitcherPanel`,
  `WorkingCountSummary`: green `N working` via l10n, leading icon green when
  any agent works and red when only exceptions remain, name/status split
  kept. Home live tile, server tile and multihost tile show green
  `N working` (`agentStateWorking` fallback for boolean-only projects); no
  failure counts invented anywhere.

### Verification

- `agent_working_visibility_widget_test.dart` (19 tests): full
  selected/unselected × working/idle/exception/offline/unknown × unread
  matrix asserting chip fill/border colors, visible localized label text and
  per-chip label geometry; window `N working` text and color; collapsed
  summary geometry beside long names; 48 px targets and compact landscape;
  1.6× text and 320 px width without overflow; light/dark accents; server
  boolean fallback; live-tile reset; all-layer clearing running→idle→failed
  with unread retained; display-precedence unit assertions (focus ≠ activity,
  no-evidence ≠ idle, offline split, failure outranks working).
- Real-host tests in `project_home_scaffold_host_test.dart` (unchanged from
  the previous round, still green): collapsed bar cross-window status with
  geometry and action-row preservation, expanded per-window counts, and
  idle/failed clearing with unread retention.
- Preserved regressions: unread membership fallback red test, multihost
  aggregation, view refresh/task completion. The no-polling test now asserts
  `getProjectViewCalls` plus header-status absence; chip labels legitimately
  render from the already-loaded view (an agent with queued work shows
  Working without any extra fetch).
- `dart format` on touched files only; `flutter analyze` clean; full
  `flutter test` suite green (835 passed, 1 skipped, 0 failed) on
  Flutter 3.44.2.

### Notes

- Four unrelated files (`home_terminal_host_picker_sheet.dart`,
  `project_home_gateway_profiles.dart`, `project_home_multi_host_projects.dart`,
  `project_home_update_panel.dart`) showed whitespace-only diffs caused by an
  overly broad `dart format lib/features/project_home/` invocation; verified
  format-only and reverted — they are not part of this delivery.
- Collapse handle remains 32×72 px (recorded limitation from the previous
  topic); selection indicator is the primary border plus the idle check.
