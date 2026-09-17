import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:ccb_mobile/app/app_theme.dart';
import 'package:ccb_mobile/features/agent_chat/agent_execution_status.dart';
import 'package:ccb_mobile/features/agent_chat/agent_state_display.dart';
import 'package:ccb_mobile/features/project_home/agent_window_switchers.dart';
import 'package:ccb_mobile/features/project_home/mobile_agent_switcher.dart';
import 'package:ccb_mobile/features/project_home/project_home_scaffold_host.dart';
import 'package:ccb_mobile/features/project_home/project_list.dart';
import 'package:ccb_mobile/features/project_home/wide_agent_column.dart';
import 'package:ccb_mobile/models/ccb_agent.dart';
import 'package:ccb_mobile/models/ccb_project.dart';
import 'package:ccb_mobile/models/ccb_project_view.dart';
import 'package:ccb_mobile/models/ccb_window.dart';
import 'package:ccb_mobile/widgets/working_status_style.dart';

void main() {
  testWidgets(
    'review: unread window retains agent.window membership fallback',
    (tester) async {
      final view = _view(explicitMembership: false);
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: MobileAgentSwitcherPanel(
              view: view,
              selectedAgent: view.agents.first,
              collapsed: false,
              unreadAgentNames: const {'reviewer'},
              onCollapse: () {},
              onExpand: () {},
              onWindowSelected: (_) {},
              onAgentSelected: (_) {},
            ),
          ),
        ),
      );
      expect(
        find.byKey(const ValueKey('window-unread-star-review')),
        findsOneWidget,
      );
    },
  );

  testWidgets('agent chips render the full state matrix with fills and text', (
    tester,
  ) async {
    // A wide surface keeps every chip in the lazily built viewport.
    await tester.binding.setSurfaceSize(const Size(1600, 900));
    addTearDown(() => tester.binding.setSurfaceSize(null));
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SizedBox(
            width: 1600,
            child: AgentSwitcher(
              agents: [
                _agent(name: 'sel-idle', activityState: 'idle'),
                _agent(name: 'sel-work', activityState: 'running'),
                _agent(name: 'unsel-idle', activityState: 'idle'),
                _agent(name: 'unsel-work', activityState: 'busy'),
                _agent(name: 'failed', activityState: 'failed'),
                _agent(name: 'offline', activityState: 'offline'),
                _agent(name: 'unknown'),
              ],
              selectedAgentName: 'sel-idle',
              unreadAgentNames: const {
                'sel-idle',
                'sel-work',
                'failed',
                'offline',
                'unknown',
              },
              onAgentSelected: (_) {},
            ),
          ),
        ),
      ),
    );

    final colorScheme =
        Theme.of(
          tester.element(find.byKey(const ValueKey('agent-switcher'))),
        ).colorScheme;
    final green = workingStatusAccent(colorScheme);

    ChoiceChip chip(String name) =>
        tester.widget<ChoiceChip>(find.byKey(ValueKey('agent-$name')));

    // Selection keeps its own primary border and never replaces state fill.
    final selIdle = chip('sel-idle');
    expect(selIdle.selected, isTrue);
    expect(selIdle.side?.color, colorScheme.primary);
    expect(
      selIdle.backgroundColor,
      agentStateFill(AgentActivityDisplay.idle, colorScheme),
    );

    final selWork = chip('sel-work');
    expect(selWork.selected, isFalse);
    expect(selWork.side?.color, green);
    expect(
      selWork.backgroundColor,
      agentStateFill(AgentActivityDisplay.working, colorScheme),
    );

    // Unselected states carry their own fills and borders.
    final unselIdle = chip('unsel-idle');
    expect(unselIdle.selected, isFalse);
    expect(unselIdle.side?.color, colorScheme.outlineVariant);
    expect(
      unselIdle.backgroundColor,
      agentStateFill(AgentActivityDisplay.idle, colorScheme),
    );

    final unselWork = chip('unsel-work');
    expect(unselWork.side?.color, green);
    expect(
      unselWork.backgroundColor,
      agentStateFill(AgentActivityDisplay.working, colorScheme),
    );

    final failed = chip('failed');
    expect(failed.side?.color, colorScheme.error);
    expect(
      failed.backgroundColor,
      agentStateFill(AgentActivityDisplay.exception, colorScheme),
    );

    final offline = chip('offline');
    expect(
      offline.backgroundColor,
      agentStateFill(AgentActivityDisplay.offline, colorScheme),
    );

    final unknown = chip('unknown');
    expect(
      unknown.backgroundColor,
      agentStateFill(AgentActivityDisplay.unknown, colorScheme),
    );

    // Every state paints its localized label, visible as real text nodes.
    expect(find.text('Working'), findsNWidgets(2));
    expect(find.text('Idle'), findsNWidgets(2));
    expect(find.text('Exception'), findsOneWidget);
    expect(find.text('Offline'), findsOneWidget);
    expect(find.text('Unknown'), findsOneWidget);
    // State labels keep their own never-truncated boxes inside the chips.
    for (final name in [
      'sel-work',
      'unsel-work',
      'failed',
      'offline',
      'unknown',
      'sel-idle',
    ]) {
      final chipRect = tester.getRect(find.byKey(ValueKey('agent-$name')));
      final labelFinder = find.descendant(
        of: find.byKey(ValueKey('agent-$name')),
        matching: find.byKey(
          ValueKey('agent-state-label-${_expectedLabelState(name).name}'),
        ),
      );
      final labelRect = tester.getRect(labelFinder);
      expect(labelRect.width, greaterThan(0));
      expect(labelRect.right, lessThanOrEqualTo(chipRect.right));
    }

    // Unread orange badges coexist with every state, independently.
    for (final name in [
      'sel-idle',
      'sel-work',
      'failed',
      'offline',
      'unknown',
    ]) {
      expect(find.byKey(ValueKey('agent-unread-star-$name')), findsOneWidget);
      final star = tester.widget<Icon>(
        find.descendant(
          of: find.byKey(ValueKey('agent-unread-star-$name')),
          matching: find.byIcon(Icons.star),
        ),
      );
      expect(star.color, unreadBadgeColor(colorScheme));
    }
    expect(
      find.byKey(const ValueKey('agent-unread-star-unsel-work')),
      findsNothing,
    );
  });

  testWidgets('window switcher aggregates working counts per window', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: WindowSwitcher(
            windows: [_window('main'), _window('review')],
            selectedWindowName: 'main',
            workingAgentCounts: const {'review': 2},
            onWindowSelected: (_) {},
          ),
        ),
      ),
    );

    final colorScheme =
        Theme.of(
          tester.element(find.byKey(const ValueKey('window-switcher'))),
        ).colorScheme;
    final green = workingStatusAccent(colorScheme);

    final mainChip = tester.widget<ChoiceChip>(
      find.byKey(const ValueKey('window-tab-main')),
    );
    final reviewChip = tester.widget<ChoiceChip>(
      find.byKey(const ValueKey('window-tab-review')),
    );

    // Nonselected windows carry their working aggregate with visible text.
    expect(mainChip.side?.color, colorScheme.primary);
    expect(reviewChip.side?.color, green);
    expect(
      find.byKey(const ValueKey('window-working-count-main')),
      findsNothing,
    );
    final reviewCount = tester.widget<Text>(
      find.byKey(const ValueKey('window-working-count-review')),
    );
    expect(reviewCount.data, '2 working');
    expect(reviewCount.style?.color, green);
    final reviewLabel = tester.widget<Text>(
      find.byKey(const ValueKey('window-label-review')),
    );
    expect(reviewLabel.overflow, TextOverflow.ellipsis);
    final reviewTooltip = tester.widget<Tooltip>(
      find.ancestor(
        of: find.byKey(const ValueKey('window-tab-review')),
        matching: find.byType(Tooltip),
      ),
    );
    expect(reviewTooltip.message, 'review · 2 working');
  });

  testWidgets('collapsed mobile switcher keeps working context visible', (
    tester,
  ) async {
    final view = _view();
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: MobileAgentSwitcherPanel(
            view: view,
            selectedAgent: view.agents.first,
            collapsed: true,
            onCollapse: () {},
            onExpand: () {},
            onWindowSelected: (_) {},
            onAgentSelected: (_) {},
          ),
        ),
      ),
    );

    final colorScheme =
        Theme.of(
          tester.element(
            find.byKey(const ValueKey('mobile-agent-switcher-collapsed')),
          ),
        ).colorScheme;

    expect(find.text('2 working'), findsOneWidget);
    final icon = tester.widget<Icon>(
      find.descendant(
        of: find.byKey(const ValueKey('mobile-agent-switcher-collapsed')),
        matching: find.byIcon(Icons.auto_awesome_rounded),
      ),
    );
    expect(icon.color, workingStatusAccent(colorScheme));
  });

  testWidgets('expanded mobile switcher shows per-window working counts', (
    tester,
  ) async {
    final view = _view();
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SizedBox(
            width: 520,
            child: MobileAgentSwitcherPanel(
              view: view,
              selectedAgent: view.agents.first,
              collapsed: false,
              onCollapse: () {},
              onExpand: () {},
              onWindowSelected: (_) {},
              onAgentSelected: (_) {},
            ),
          ),
        ),
      ),
    );

    final colorScheme =
        Theme.of(
          tester.element(
            find.byKey(const ValueKey('mobile-agent-switcher-expanded')),
          ),
        ).colorScheme;
    final green = workingStatusAccent(colorScheme);

    final mainChip = tester.widget<ChoiceChip>(
      find.byKey(const ValueKey('window-tab-main')),
    );
    final reviewChip = tester.widget<ChoiceChip>(
      find.byKey(const ValueKey('window-tab-review')),
    );
    // Selected window shows the selection border; its working aggregate stays
    // in the green count text and tinted fill.
    expect(mainChip.side?.color, colorScheme.primary);
    expect(reviewChip.side?.color, green);
    expect(
      tester
          .widget<Text>(find.byKey(const ValueKey('window-working-count-main')))
          .data,
      '1 working',
    );
    expect(
      tester
          .widget<Text>(
            find.byKey(const ValueKey('window-working-count-review')),
          )
          .data,
      '1 working',
    );

    final worker = tester.widget<ChoiceChip>(
      find.byKey(const ValueKey('agent-worker')),
    );
    expect(worker.side?.color, green);
    expect(find.text('Working'), findsWidgets);
    expect(
      find.descendant(
        of: find.byKey(const ValueKey('agent-idle')),
        matching: find.byIcon(Icons.check_circle),
      ),
      findsOneWidget,
    );
  });

  testWidgets('working indicators reset when execution goes idle', (
    tester,
  ) async {
    var working = true;
    await tester.pumpWidget(
      MaterialApp(
        home: StatefulBuilder(
          builder: (context, setState) {
            return Scaffold(
              body: Column(
                children: [
                  AgentSwitcher(
                    agents: [
                      working
                          ? _agent(name: 'worker', activityState: 'running')
                          : _agent(name: 'worker'),
                    ],
                    selectedAgentName: 'worker',
                    onAgentSelected: (_) {},
                  ),
                  TextButton(
                    key: const ValueKey('toggle-working'),
                    onPressed: () => setState(() => working = false),
                    child: const Text('toggle'),
                  ),
                ],
              ),
            );
          },
        ),
      ),
    );

    final green = workingStatusAccent(
      Theme.of(
        tester.element(find.byKey(const ValueKey('agent-switcher'))),
      ).colorScheme,
    );
    var chip = tester.widget<ChoiceChip>(
      find.byKey(const ValueKey('agent-worker')),
    );
    // Selected while working: selection border, working fill, dot, label.
    expect(
      chip.side?.color,
      Theme.of(
        tester.element(find.byKey(const ValueKey('agent-switcher'))),
      ).colorScheme.primary,
    );
    expect(
      chip.backgroundColor,
      agentStateFill(
        AgentActivityDisplay.working,
        Theme.of(
          tester.element(find.byKey(const ValueKey('agent-switcher'))),
        ).colorScheme,
      ),
    );
    expect(green, isNotNull);
    expect(find.byIcon(Icons.circle), findsOneWidget);
    expect(find.text('Working'), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('toggle-working')));
    await tester.pumpAndSettle();

    chip = tester.widget<ChoiceChip>(
      find.byKey(const ValueKey('agent-worker')),
    );
    expect(find.byIcon(Icons.circle), findsNothing);
    expect(find.text('Working'), findsNothing);
    // Cleared evidence reports unknown, never an invented idle.
    expect(find.text('Unknown'), findsOneWidget);
    // Selection stays visible through the primary border while the unknown
    // state shows its explicit marker instead of an invented idle check.
    expect(find.byIcon(Icons.help_outline), findsOneWidget);
    final chipNow = tester.widget<ChoiceChip>(
      find.byKey(const ValueKey('agent-worker')),
    );
    expect(
      chipNow.side?.color,
      Theme.of(
        tester.element(find.byKey(const ValueKey('agent-switcher'))),
      ).colorScheme.primary,
    );
  });

  testWidgets('long agent names are capped but fully accessible', (
    tester,
  ) async {
    const longName =
        'extremely-long-agent-name-that-would-stretch-the-selector';
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SizedBox(
            width: 220,
            child: AgentSwitcher(
              agents: [_agent(name: longName, activityState: 'idle')],
              selectedAgentName: longName,
              onAgentSelected: (_) {},
            ),
          ),
        ),
      ),
    );

    final label = tester.widget<Text>(
      find.byKey(const ValueKey('agent-label-$longName')),
    );
    expect(label.maxLines, 1);
    expect(label.overflow, TextOverflow.ellipsis);
    final tooltip = tester.widget<Tooltip>(
      find.ancestor(
        of: find.byKey(const ValueKey('agent-$longName')),
        matching: find.byType(Tooltip),
      ),
    );
    expect(tooltip.message, longName);
    // The idle label still renders fully beside the ellipsized name.
    final chipRect = tester.getRect(
      find.byKey(const ValueKey('agent-$longName')),
    );
    final stateRect = tester.getRect(
      find.byKey(const ValueKey('agent-state-label-idle')),
    );
    expect(stateRect.width, greaterThan(0));
    expect(stateRect.right, lessThanOrEqualTo(chipRect.right));
    expect(tester.takeException(), isNull);
  });

  testWidgets('window working count stays visible beside a long label', (
    tester,
  ) async {
    final longLabel = 'release-hotfix-window-with-a-very-long-name';
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SizedBox(
            width: 320,
            child: WindowSwitcher(
              windows: [
                CcbWindow(
                  name: longLabel,
                  label: longLabel,
                  kind: 'agents',
                  order: 0,
                  active: true,
                  agents: const [],
                ),
              ],
              selectedWindowName: longLabel,
              workingAgentCounts: {longLabel: 3},
              onWindowSelected: (_) {},
            ),
          ),
        ),
      ),
    );

    expect(tester.takeException(), isNull);
    final label = tester.widget<Text>(
      find.byKey(ValueKey('window-label-$longLabel')),
    );
    expect(label.overflow, TextOverflow.ellipsis);
    final countKey = ValueKey('window-working-count-$longLabel');
    final chipRect = tester.getRect(
      find.byKey(ValueKey('window-tab-$longLabel')),
    );
    final countRect = tester.getRect(find.byKey(countKey));
    expect(countRect.width, greaterThan(0));
    expect(countRect.right, lessThanOrEqualTo(chipRect.right - 4));
    expect(countRect.left, greaterThanOrEqualTo(chipRect.left));
    expect(tester.widget<Text>(find.byKey(countKey)).data, '3 working');
  });

  testWidgets('collapsed summary keeps working status beside a long name', (
    tester,
  ) async {
    final view = _view();
    final selected = _agent(
      name: 'extremely-long-selected-agent-name-for-summary',
      activityState: 'running',
    );
    final longView = CcbProjectView(
      project: view.project,
      namespaceEpoch: view.namespaceEpoch,
      tmuxSocketPath: null,
      tmuxSessionName: null,
      activeWindow: 'main',
      activePaneId: null,
      windows: view.windows,
      agents: [selected, ...view.agents.skip(1)],
      contentItems: const [],
      notifications: const [],
      terminalHistories: const {},
    );
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SizedBox(
            width: 320,
            child: MobileAgentSwitcherPanel(
              view: longView,
              selectedAgent: selected,
              collapsed: true,
              onCollapse: () {},
              onExpand: () {},
              onWindowSelected: (_) {},
              onAgentSelected: (_) {},
            ),
          ),
        ),
      ),
    );

    expect(tester.takeException(), isNull);
    final summary = tester.widget<Text>(
      find.byKey(const ValueKey('mobile-agent-switcher-summary')),
    );
    expect(summary.overflow, TextOverflow.ellipsis);
    final statusRect = tester.getRect(
      find.byKey(const ValueKey('mobile-agent-switcher-working-count')),
    );
    final barRect = tester.getRect(
      find.byKey(const ValueKey('mobile-agent-switcher-collapsed')),
    );
    expect(statusRect.width, greaterThan(0));
    expect(statusRect.right, lessThan(barRect.right));
    expect(statusRect.left, greaterThan(barRect.left));
    expect(
      tester
          .widget<Text>(
            find.byKey(const ValueKey('mobile-agent-switcher-working-count')),
          )
          .data,
      contains('working'),
    );
  });

  testWidgets(
    'working selected chip and wide tile stay independently readable',
    (tester) async {
      final semantics = tester.ensureSemantics();
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: AgentSwitcher(
              agents: [_agent(name: 'both', activityState: 'running')],
              selectedAgentName: 'both',
              unreadAgentNames: const {'both'},
              onAgentSelected: (_) {},
            ),
          ),
        ),
      );
      final colorScheme =
          Theme.of(
            tester.element(find.byKey(const ValueKey('agent-switcher'))),
          ).colorScheme;
      final chip = tester.widget<ChoiceChip>(
        find.byKey(const ValueKey('agent-both')),
      );
      expect(chip.selected, isTrue);
      expect(chip.side?.color, colorScheme.primary);
      expect(
        chip.backgroundColor,
        agentStateFill(AgentActivityDisplay.working, colorScheme),
      );
      expect(find.text('Working'), findsOneWidget);
      expect(
        find.descendant(
          of: find.byKey(const ValueKey('agent-both')),
          matching: find.byIcon(Icons.circle),
        ),
        findsOneWidget,
      );
      expect(find.bySemanticsLabel('Working'), findsOneWidget);
      expect(
        find.byKey(const ValueKey('agent-unread-star-both')),
        findsOneWidget,
      );

      final view = _view();
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: SizedBox(
              width: 460,
              child: WideAgentColumn(
                view: view,
                selectedAgentName: 'worker',
                onAgentSelected: (_) {},
              ),
            ),
          ),
        ),
      );
      final wideTile = tester.widget<ListTile>(
        find.byKey(const ValueKey('agent-worker')),
      );
      expect(
        wideTile.tileColor,
        agentStateFill(AgentActivityDisplay.working, colorScheme),
      );
      expect(
        wideTile.selectedTileColor,
        wideTile.tileColor,
        reason: 'selection must preserve the activity fill',
      );
      expect(
        find.descendant(
          of: find.byKey(const ValueKey('agent-worker')),
          matching: find.text('Working'),
        ),
        findsOneWidget,
      );
      semantics.dispose();
    },
  );

  testWidgets('switchers use 48 pixel chip targets when height allows', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SizedBox(
            width: 480,
            child: MobileAgentSwitcherPanel(
              view: _view(),
              selectedAgent: _view().agents.first,
              collapsed: false,
              onCollapse: () {},
              onExpand: () {},
              onWindowSelected: (_) {},
              onAgentSelected: (_) {},
            ),
          ),
        ),
      ),
    );

    expect(
      tester.getSize(find.byKey(const ValueKey('agent-idle'))).height,
      greaterThanOrEqualTo(48),
    );
    expect(
      tester.getSize(find.byKey(const ValueKey('window-tab-main'))).height,
      greaterThanOrEqualTo(48),
    );
  });

  testWidgets('switchers compact under short landscape heights', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        home: MediaQuery(
          data: const MediaQueryData(size: Size(844, 390)),
          child: Scaffold(
            body: SizedBox(
              width: 480,
              child: MobileAgentSwitcherPanel(
                view: _view(),
                selectedAgent: _view().agents.first,
                collapsed: false,
                onCollapse: () {},
                onExpand: () {},
                onWindowSelected: (_) {},
                onAgentSelected: (_) {},
              ),
            ),
          ),
        ),
      ),
    );

    expect(tester.takeException(), isNull);
    expect(
      tester.getSize(find.byKey(const ValueKey('agent-switcher'))).height,
      lessThan(48),
    );
    expect(
      tester.getSize(find.byKey(const ValueKey('agent-idle'))).height,
      greaterThanOrEqualTo(32),
    );
  });

  testWidgets('switcher rows survive large text without overflow', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        home: Builder(
          builder: (context) {
            return MediaQuery(
              data: MediaQuery.of(
                context,
              ).copyWith(textScaler: const TextScaler.linear(1.6)),
              child: Scaffold(
                body: SizedBox(
                  width: 320,
                  child: MobileAgentSwitcherPanel(
                    view: _view(),
                    selectedAgent: _view().agents.first,
                    collapsed: false,
                    onCollapse: () {},
                    onExpand: () {},
                    onWindowSelected: (_) {},
                    onAgentSelected: (_) {},
                  ),
                ),
              ),
            );
          },
        ),
      ),
    );

    expect(tester.takeException(), isNull);
    expect(
      tester.getSize(find.byKey(const ValueKey('agent-switcher'))).height,
      greaterThanOrEqualTo(44),
    );
    expect(
      tester.getSize(find.byKey(const ValueKey('window-switcher'))).height,
      greaterThanOrEqualTo(44),
    );
  });

  testWidgets('working accent follows the light theme', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: ccbLightTheme(),
        darkTheme: ccbDarkTheme(),
        themeMode: ThemeMode.light,
        home: const Scaffold(body: _ThemeProbeSwitcher()),
      ),
    );
    expect(
      _workingSideColor(tester),
      workingStatusAccent(ccbLightTheme().colorScheme),
    );
  });

  testWidgets('working accent follows the dark theme', (tester) async {
    await tester.pumpWidget(
      MaterialApp(
        theme: ccbLightTheme(),
        darkTheme: ccbDarkTheme(),
        themeMode: ThemeMode.dark,
        home: const Scaffold(body: _ThemeProbeSwitcher()),
      ),
    );
    expect(
      _workingSideColor(tester),
      workingStatusAccent(ccbDarkTheme().colorScheme),
    );
  });

  testWidgets('server project tiles show working counts and boolean fallback', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        home: ProjectHomeServerProjectListHost(
          projects: [
            const CcbProject(
              id: 'counted',
              displayName: 'Counted',
              root: '/srv/counted',
              hasWorkingAgents: true,
              workingAgentCount: 2,
            ),
            const CcbProject(
              id: 'boolean-only',
              displayName: 'Boolean',
              root: '/srv/boolean',
              hasWorkingAgents: true,
            ),
            const CcbProject(
              id: 'idle',
              displayName: 'Idle',
              root: '/srv/idle',
            ),
          ],
          onRefreshProjects: () {},
          onOpenTerminal: () {},
          onOpenSettings: () {},
          onOpenProject: (_) {},
        ),
      ),
    );

    expect(find.text('2 working'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('project-working-count-counted')),
      findsOneWidget,
    );
    // Old projects that only report the boolean still get visible text.
    expect(find.text('Working'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('project-working-count-boolean-only')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('project-working-count-idle')),
      findsNothing,
    );
    // Health stays visible as secondary information.
    expect(find.text('unknown'), findsNWidgets(3));
  });

  testWidgets('live project tile trailing shows working count', (tester) async {
    final view = _view();
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ProjectListTile(
            view: view,
            selectedAgent: view.agents.first,
            selected: false,
            onOpen: () {},
          ),
        ),
      ),
    );

    expect(
      find.byKey(const ValueKey('project-working-count-proj')),
      findsOneWidget,
    );
    expect(find.text('2 working'), findsOneWidget);

    final idleView = _view(workingStates: const [null, null]);
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ProjectListTile(
            view: idleView,
            selectedAgent: idleView.agents.first,
            selected: false,
            onOpen: () {},
          ),
        ),
      ),
    );
    expect(
      find.byKey(const ValueKey('project-working-count-proj')),
      findsNothing,
    );
    // Agent count returns once nothing is working.
    expect(find.text('3'), findsOneWidget);
  });

  testWidgets('running to idle and failed clears every working layer', (
    tester,
  ) async {
    String? state = 'running';
    await tester.pumpWidget(
      MaterialApp(
        home: StatefulBuilder(
          builder: (context, setState) {
            final view = _view(workingStates: [state, state]);
            final anyWorking = view.agents.any(agentHasSourceWorkingActivity);
            return Scaffold(
              body: ListView(
                children: [
                  MobileAgentSwitcherPanel(
                    view: view,
                    selectedAgent: view.agents.first,
                    collapsed: false,
                    unreadAgentNames: const {'worker', 'reviewer'},
                    onCollapse: () {},
                    onExpand: () {},
                    onWindowSelected: (_) {},
                    onAgentSelected: (_) {},
                  ),
                  MobileAgentSwitcherPanel(
                    view: view,
                    selectedAgent: view.agents.first,
                    collapsed: true,
                    unreadAgentNames: const {'worker', 'reviewer'},
                    onCollapse: () {},
                    onExpand: () {},
                    onWindowSelected: (_) {},
                    onAgentSelected: (_) {},
                  ),
                  ProjectListTile(
                    view: view,
                    selectedAgent: view.agents.first,
                    selected: false,
                    hasWorkingAgents: anyWorking,
                    onOpen: () {},
                  ),
                  TextButton(
                    key: const ValueKey('set-idle'),
                    onPressed: () => setState(() => state = null),
                    child: const Text('idle'),
                  ),
                  TextButton(
                    key: const ValueKey('set-failed'),
                    onPressed: () => setState(() => state = 'failed'),
                    child: const Text('failed'),
                  ),
                ],
              ),
            );
          },
        ),
      ),
    );

    void expectWorkingEverywhere() {
      expect(
        find.byKey(const ValueKey('window-working-count-main')),
        findsOneWidget,
      );
      expect(
        find.byKey(const ValueKey('window-working-count-review')),
        findsOneWidget,
      );
      expect(
        find.byKey(const ValueKey('mobile-agent-switcher-working-count')),
        findsOneWidget,
      );
      expect(
        find.byKey(const ValueKey('project-working-count-proj')),
        findsOneWidget,
      );
      expect(find.byIcon(Icons.circle), findsWidgets);
    }

    void expectNoWorkingEverywhere() {
      expect(
        find.byKey(const ValueKey('window-working-count-main')),
        findsNothing,
      );
      expect(
        find.byKey(const ValueKey('window-working-count-review')),
        findsNothing,
      );
      expect(
        find.byKey(const ValueKey('mobile-agent-switcher-working-count')),
        findsNothing,
      );
      expect(
        find.byKey(const ValueKey('project-working-count-proj')),
        findsNothing,
      );
      expect(
        find.byKey(const ValueKey('project-working-row-proj')),
        findsNothing,
      );
      // Unread survives every transition, independently of working.
      expect(
        find.byKey(const ValueKey('window-unread-star-review')),
        findsOneWidget,
      );
      expect(
        find.byKey(const ValueKey('agent-unread-star-worker')),
        findsOneWidget,
      );
      expect(
        find.byKey(const ValueKey('mobile-agent-switcher-unread-star')),
        findsOneWidget,
      );
    }

    expectWorkingEverywhere();

    await tester.tap(find.byKey(const ValueKey('set-idle')));
    await tester.pumpAndSettle();
    expectNoWorkingEverywhere();

    await tester.tap(find.byKey(const ValueKey('set-failed')));
    await tester.pumpAndSettle();
    expectNoWorkingEverywhere();
    // Failed agents never paint working, but stay visible with their label.
    expect(find.text('Exception'), findsWidgets);
    expect(find.byKey(const ValueKey('window-tab-review')), findsOneWidget);
  });

  testWidgets(
    'display mapping separates offline and unknown from legacy idle default',
    (tester) async {
      // Focus alone is not activity.
      expect(
        agentActivityDisplay(_agent(name: 'a', active: true)),
        AgentActivityDisplay.unknown,
      );
      // No evidence at all reports unknown, never invented idle.
      expect(
        agentActivityDisplay(_agent(name: 'a')),
        AgentActivityDisplay.unknown,
      );
      // Explicit idle stays idle.
      expect(
        agentActivityDisplay(_agent(name: 'a', activityState: 'idle')),
        AgentActivityDisplay.idle,
      );
      // Offline splits out of the legacy exception bucket.
      expect(
        agentActivityDisplay(
          _agent(
            name: 'a',
            activityState: 'idle',
            queueDepth: 2,
            activityReason: 'previous tool running',
          ),
        ),
        AgentActivityDisplay.idle,
        reason: 'explicit idle must beat retained work hints',
      );
      expect(
        agentActivityDisplay(_agent(name: 'a', activityState: 'offline')),
        AgentActivityDisplay.offline,
      );
      expect(
        agentActivityDisplay(
          _agent(name: 'a', activityReason: 'provider offline'),
        ),
        AgentActivityDisplay.offline,
      );
      // Working and failure evidence keep the authoritative semantics.
      expect(
        agentActivityDisplay(_agent(name: 'a', activityState: 'running')),
        AgentActivityDisplay.working,
      );
      expect(
        agentActivityDisplay(_agent(name: 'a', queueDepth: 2)),
        AgentActivityDisplay.working,
      );
      expect(
        agentActivityDisplay(_agent(name: 'a', activityState: 'failed')),
        AgentActivityDisplay.exception,
      );
      // A failure outranks retained working markers.
      expect(
        agentActivityDisplay(
          _agent(name: 'a', activityState: 'failed', queueDepth: 3),
        ),
        AgentActivityDisplay.exception,
      );
      // Legacy classifier default stays untouched for chat surfaces.
      expect(
        agentExecutionStatus(
          agent: _agent(name: 'a'),
          isAwaitingAgentResponse: false,
        ).state,
        'idle',
      );
    },
  );
}

AgentActivityDisplay _expectedLabelState(String name) {
  switch (name) {
    case 'sel-work':
    case 'unsel-work':
      return AgentActivityDisplay.working;
    case 'failed':
      return AgentActivityDisplay.exception;
    case 'offline':
      return AgentActivityDisplay.offline;
    case 'unknown':
      return AgentActivityDisplay.unknown;
    default:
      return AgentActivityDisplay.idle;
  }
}

Color? _workingSideColor(WidgetTester tester) {
  return tester
      .widget<ChoiceChip>(find.byKey(const ValueKey('agent-worker')))
      .side
      ?.color;
}

class _ThemeProbeSwitcher extends StatelessWidget {
  const _ThemeProbeSwitcher();

  @override
  Widget build(BuildContext context) {
    return AgentSwitcher(
      agents: const [
        CcbAgent(
          name: 'worker',
          provider: 'codex',
          window: 'main',
          order: 0,
          active: false,
          queueDepth: 0,
          activityState: 'running',
        ),
      ],
      selectedAgentName: null,
      onAgentSelected: (_) {},
    );
  }
}

CcbAgent _agent({
  required String name,
  String? activityState,
  String? activitySource,
  String? activityReason,
  bool active = false,
  int queueDepth = 0,
  String window = 'main',
}) {
  return CcbAgent(
    name: name,
    provider: 'codex',
    window: window,
    order: 0,
    active: active,
    queueDepth: queueDepth,
    activityState: activityState,
    activitySource: activitySource,
    activityReason: activityReason,
  );
}

CcbWindow _window(String name) {
  return CcbWindow(
    name: name,
    label: name,
    kind: 'agents',
    order: 0,
    active: name == 'main',
    agents: const [],
  );
}

CcbProjectView _view({
  List<String?> workingStates = const ['running', 'running'],
  bool explicitMembership = true,
}) {
  final mainAgents = <CcbAgent>[
    _agent(name: 'idle', activityState: 'idle'),
    if (workingStates.isNotEmpty)
      _agent(name: 'worker', activityState: workingStates.first),
  ];
  final reviewAgents = <CcbAgent>[
    if (workingStates.length > 1)
      _agent(
        name: 'reviewer',
        activityState: workingStates[1],
        window: 'review',
      )
    else
      _agent(name: 'reviewer', activityState: 'idle', window: 'review'),
  ];
  return CcbProjectView(
    project: const CcbProject(id: 'proj', displayName: 'Project', root: '/p'),
    namespaceEpoch: 1,
    tmuxSocketPath: null,
    tmuxSessionName: null,
    activeWindow: 'main',
    activePaneId: null,
    windows: [
      CcbWindow(
        name: 'main',
        label: 'main',
        kind: 'agents',
        order: 0,
        active: true,
        agents:
            explicitMembership
                ? [for (final agent in mainAgents) agent.name]
                : const [],
      ),
      CcbWindow(
        name: 'review',
        label: 'review',
        kind: 'agents',
        order: 1,
        active: false,
        agents:
            explicitMembership
                ? [for (final agent in reviewAgents) agent.name]
                : const [],
      ),
    ],
    agents: [...mainAgents, ...reviewAgents],
    contentItems: const [],
    notifications: const [],
    terminalHistories: const {},
  );
}
