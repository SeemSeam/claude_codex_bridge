import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:ccb_mobile/features/project_home/agent_window_switchers.dart';
import 'package:ccb_mobile/features/project_home/project_list.dart';
import 'package:ccb_mobile/features/project_home/wide_agent_column.dart';
import 'package:ccb_mobile/models/ccb_agent.dart';
import 'package:ccb_mobile/models/ccb_project.dart';
import 'package:ccb_mobile/models/ccb_project_view.dart';
import 'package:ccb_mobile/models/ccb_window.dart';
import 'package:ccb_mobile/widgets/working_attention_beat.dart';
import 'package:ccb_mobile/widgets/working_status_style.dart';

void main() {
  testWidgets('agent switcher highlights source-working agents with a border', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: AgentSwitcher(
            agents: [
              _agent(name: 'idle'),
              _agent(
                name: 'working',
                activityState: 'active',
                activitySource: 'codex_runtime',
                activityReason: 'codex_working_status_line',
              ),
            ],
            selectedAgentName: 'idle',
            unreadAgentNames: const {'working'},
            onAgentSelected: (_) {},
          ),
        ),
      ),
    );

    final colorScheme =
        Theme.of(
          tester.element(find.byKey(const ValueKey('agent-switcher'))),
        ).colorScheme;
    final idle = tester.widget<ChoiceChip>(
      find.byKey(const ValueKey('agent-idle')),
    );
    final working = tester.widget<ChoiceChip>(
      find.byKey(const ValueKey('agent-working')),
    );

    // The selected idle chip shows the selection border; its neutral fill and
    // idle text stay visible underneath.
    expect(idle.side?.color, colorScheme.primary);
    expect(working.side?.color, workingStatusAccent(colorScheme));
    expect(working.side?.width, 1.6);
    expect(
      find.byKey(const ValueKey('agent-unread-star-working')),
      findsOneWidget,
    );
  });

  testWidgets(
    'project row can show working highlight and unread star together',
    (tester) async {
      await tester.pumpWidget(
        const MaterialApp(
          home: Scaffold(
            body: ProjectWorkingRowHighlight(
              projectId: 'proj',
              hasWorkingAgents: true,
              child: ProjectAttentionAvatar(
                projectId: 'proj',
                favorite: false,
                hasUnreadTaskCompletion: true,
                hasWorkingAgents: true,
              ),
            ),
          ),
        ),
      );
      expect(
        find.byKey(const ValueKey('project-working-row-proj')),
        findsOneWidget,
      );
      // The beat strip and the 6px left stripe were removed; the row keeps a
      // single tinted border so working decoration is not redundant.
      expect(
        find.byKey(const ValueKey('project-working-row-beat-proj')),
        findsNothing,
      );
      expect(find.byType(WorkingAttentionBeat), findsNothing);
      expect(
        find.byKey(const ValueKey('project-unread-star-proj')),
        findsOneWidget,
      );

      final rowDecorations =
          tester
              .widgetList<DecoratedBox>(
                find.descendant(
                  of: find.byKey(const ValueKey('project-working-row-proj')),
                  matching: find.byType(DecoratedBox),
                ),
              )
              .map((box) => box.decoration)
              .whereType<BoxDecoration>();
      final colorScheme = ThemeData().colorScheme;
      final accent = projectWorkingRowAccent(colorScheme);
      expect(
        rowDecorations.any((decoration) {
          final border = decoration.border;
          return border is Border &&
              border.top.width == 2.2 &&
              border.top.color == projectWorkingRowBorder(accent);
        }),
        isTrue,
      );
      expect(
        rowDecorations.any((decoration) {
          final border = decoration.border;
          return border is Border && border.left.width == 6;
        }),
        isFalse,
      );
      expect(projectWorkingRowTint(colorScheme), isNot(Colors.transparent));
      expect(
        find.descendant(
          of: find.byKey(const ValueKey('project-working-row-proj')),
          matching: find.byType(AnimatedBuilder),
        ),
        findsNothing,
      );
      expect(
        rowDecorations.any((decoration) => decoration.boxShadow != null),
        isFalse,
      );
    },
  );

  testWidgets(
    'wide agent list highlights source-working agents with a border',
    (tester) async {
      final view = CcbProjectView(
        project: const CcbProject(
          id: 'proj',
          displayName: 'Project',
          root: '/p',
        ),
        namespaceEpoch: 1,
        tmuxSocketPath: null,
        tmuxSessionName: null,
        activeWindow: 'main',
        activePaneId: null,
        windows: const [
          CcbWindow(
            name: 'main',
            label: 'main',
            kind: 'agents',
            order: 0,
            active: true,
            agents: ['idle', 'working'],
          ),
        ],
        agents: [
          _agent(name: 'idle'),
          _agent(
            name: 'working',
            activityState: 'running',
            activitySource: 'codex_runtime',
          ),
        ],
        contentItems: const [],
        notifications: const [],
        terminalHistories: const {},
      );

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: WideAgentColumn(
              view: view,
              selectedAgentName: 'idle',
              onAgentSelected: (_) {},
            ),
          ),
        ),
      );

      final colorScheme =
          Theme.of(
            tester.element(find.byKey(const ValueKey('agent-secondary-list'))),
          ).colorScheme;
      final idleTile = tester.widget<ListTile>(
        find.byKey(const ValueKey('agent-idle')),
      );
      final workingTile = tester.widget<ListTile>(
        find.byKey(const ValueKey('agent-working')),
      );
      final idleShape = idleTile.shape as RoundedRectangleBorder;
      final workingShape = workingTile.shape as RoundedRectangleBorder;

      // Selected idle tile keeps the primary selection border over the
      // neutral state fill.
      expect(idleShape.side.color, colorScheme.primary);
      expect(idleShape.side.width, 1.6);
      expect(workingShape.side.color, workingStatusAccent(colorScheme));
      expect(workingShape.side.width, 1.6);
    },
  );
}

CcbAgent _agent({
  required String name,
  String? activityState,
  String? activitySource,
  String? activityReason,
}) {
  return CcbAgent(
    name: name,
    provider: 'codex',
    window: 'main',
    order: 0,
    active: false,
    queueDepth: 0,
    activityState: activityState,
    activitySource: activitySource,
    activityReason: activityReason,
  );
}
