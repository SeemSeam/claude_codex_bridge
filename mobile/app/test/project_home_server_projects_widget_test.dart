import 'dart:convert';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:ccb_mobile/ccb_mobile.dart';

import 'support/project_home_test_driver.dart';
import 'support/project_home_test_fakes.dart';

void main() {
  testWidgets('home terminal opens independent host shell tabs', (
    tester,
  ) async {
    await setTestSurfaceSize(tester, const Size(390, 844));
    final profile = _pairedHost(hostId: 'server-host', deviceId: 'phone');
    final profileStore = await _profileStoreWith([profile]);
    final gatewayRepository = _ServerProjectsRepository([
      _projectFixture(
        id: 'test_ccb2',
        displayName: 'test_ccb2',
        root: '/srv/ccb/test_ccb2',
      ),
    ]);
    final terminalTransport = RecordingTerminalTransport();

    await tester.pumpWidget(
      MaterialApp(
        home: ProjectHomeScreen(
          repository: FakeMobileCcbRepository.demo(),
          profileStore: profileStore,
          gatewayRepositoryFactory: (_) => gatewayRepository,
          gatewayTerminalTransportFactory: (_) => terminalTransport,
        ),
      ),
    );
    await tester.pumpAndSettle();
    await _activatePairedGatewayListOnly(tester);

    await tester.tap(
      find.byKey(const ValueKey('project-list-terminal-action')),
    );
    await tester.pumpAndSettle();

    expect(find.byKey(const ValueKey('host-terminal-screen')), findsOneWidget);
    expect(gatewayRepository.getProjectViewCalls, isEmpty);
    expect(terminalTransport.hostRequests, hasLength(1));
    expect(terminalTransport.hostRequests.single.clientSessionId, 'shell-1');

    await tester.tap(find.byKey(const ValueKey('host-terminal-add')));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('host-terminal-tab-2')), findsOneWidget);
    expect(terminalTransport.hostRequests, hasLength(2));
    expect(terminalTransport.hostRequests.last.clientSessionId, 'shell-2');

    await tester.tap(find.byKey(const ValueKey('host-terminal-close')));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'Close'));
    await tester.pumpAndSettle();

    expect(terminalTransport.terminatedHostSessions, ['shell-2']);
    expect(terminalTransport.sessions[1].closed, isTrue);
    expect(
      terminalTransport.hostOperationLog,
      containsAllInOrder(['close:shell-2', 'terminate:shell-2']),
    );
    expect(find.byKey(const ValueKey('host-terminal-tab-2')), findsNothing);

    await tester.binding.handlePopRoute();
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('host-terminal-screen')), findsNothing);
    expect(terminalTransport.terminatedHostSessions, ['shell-2']);
  });

  testWidgets('closing final host terminal closes stream before termination', (
    tester,
  ) async {
    await setTestSurfaceSize(tester, const Size(390, 844));
    final terminalTransport = RecordingTerminalTransport();

    await tester.pumpWidget(
      MaterialApp(home: HostTerminalScreen(transport: terminalTransport)),
    );
    await tester.pumpAndSettle();
    final session = terminalTransport.sessions.single;

    await tester.tap(find.byKey(const ValueKey('host-terminal-close')));
    await tester.pumpAndSettle();
    await tester.tap(find.widgetWithText(FilledButton, 'Close'));
    await tester.pumpAndSettle();

    expect(session.closed, isTrue);
    expect(terminalTransport.terminatedHostSessions, ['shell-1']);
    expect(
      terminalTransport.hostOperationLog,
      containsAllInOrder(['close:shell-1', 'terminate:shell-1']),
    );
    expect(find.byKey(const ValueKey('host-terminal-screen')), findsNothing);
  });

  testWidgets('host terminal caps concurrent tabs at six', (tester) async {
    await setTestSurfaceSize(tester, const Size(390, 844));
    final terminalTransport = RecordingTerminalTransport();

    await tester.pumpWidget(
      MaterialApp(home: HostTerminalScreen(transport: terminalTransport)),
    );
    await tester.pumpAndSettle();
    for (var slot = 2; slot <= 6; slot += 1) {
      await tester.tap(find.byKey(const ValueKey('host-terminal-add')));
      await tester.pumpAndSettle();
    }

    expect(terminalTransport.hostRequests, hasLength(6));
    expect(find.byKey(const ValueKey('host-terminal-tab-6')), findsOneWidget);
    final addButton = tester.widget<IconButton>(
      find.byKey(const ValueKey('host-terminal-add')),
    );
    expect(addButton.onPressed, isNull);
  });

  testWidgets(
    'paired gateway lists server projects before opening real project',
    (tester) async {
      await setTestSurfaceSize(tester, const Size(390, 844));
      final profile = _pairedHost(hostId: 'server-host', deviceId: 'phone');
      final profileStore = await _profileStoreWith([profile]);
      final gatewayRepository = _ServerProjectsRepository([
        _projectFixture(
          id: 'test_ccb2',
          displayName: 'test_ccb2',
          root: '/srv/ccb/test_ccb2',
        ),
        _projectFixture(
          id: 'ccb_mobile',
          displayName: 'ccb_mobile',
          root: '/home/bfly/yunwei/ccb_mobile',
        ),
      ]);
      final gatewayTerminalTransport = RecordingTerminalTransport();

      await tester.pumpWidget(
        MaterialApp(
          home: ProjectHomeScreen(
            repository: FakeMobileCcbRepository.demo(),
            profileStore: profileStore,
            gatewayRepositoryFactory: (_) => gatewayRepository,
            gatewayTerminalTransportFactory: (_) => gatewayTerminalTransport,
          ),
        ),
      );
      await tester.pumpAndSettle();

      await openConnectionDetails(tester);
      await expandTile(tester, const ValueKey('runtime-mode-panel'));
      final segments = tester.widget<SegmentedButton<AppRuntimeMode>>(
        find.byKey(const ValueKey('runtime-mode-segments')),
      );
      segments.onSelectionChanged?.call({AppRuntimeMode.pairedGateway});
      await tester.pumpAndSettle();
      await dismissConnectionDetails(tester);

      expect(gatewayRepository.listProjectsCalls, 1);
      expect(gatewayRepository.getProjectViewCalls, isEmpty);
      expect(find.byKey(const ValueKey('project-list')), findsOneWidget);
      expect(
        find.byKey(const ValueKey('project-open-test_ccb2')),
        findsOneWidget,
      );
      expect(
        find.byKey(const ValueKey('project-open-ccb_mobile')),
        findsOneWidget,
      );
      expect(find.text('test_ccb2'), findsOneWidget);
      expect(find.text('/srv/ccb/test_ccb2'), findsOneWidget);

      await tester.tap(find.byKey(const ValueKey('project-open-test_ccb2')));
      await tester.pumpAndSettle();

      expect(gatewayRepository.getProjectViewCalls, ['test_ccb2']);
      expect(
        find.byKey(const ValueKey('selected-agent-workspace')),
        findsOneWidget,
      );

      await tester.tap(find.byKey(const ValueKey('agent-message-composer')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('agent-quick-key-tab')));
      await tester.pumpAndSettle();

      expect(gatewayTerminalTransport.requests, hasLength(1));
      expect(
        gatewayTerminalTransport.requests.single.attachCommand,
        'gateway terminal stream test_ccb2/mobile',
      );
      expect(gatewayTerminalTransport.sessions.single.pasted, isEmpty);
      expect(gatewayTerminalTransport.sessions.single.written, [
        [9],
      ]);

      await tester.tap(find.byKey(const ValueKey('agent-quick-key-esc')));
      await tester.pumpAndSettle();

      expect(gatewayTerminalTransport.requests, hasLength(1));
      expect(gatewayTerminalTransport.sessions.single.written, [
        [9],
        [27],
      ]);

      await tester.enterText(
        find.byKey(const ValueKey('agent-message-composer')),
        'server route smoke',
      );
      await tester.tap(find.byKey(const ValueKey('agent-message-send-button')));
      await tester.pumpAndSettle();

      expect(gatewayRepository.submittedMessages, isEmpty);
      expect(gatewayTerminalTransport.requests, hasLength(1));
      expect(gatewayTerminalTransport.sessions.single.pasted, [
        'server route smoke',
      ]);
      expect(gatewayTerminalTransport.sessions.single.written, [
        [9],
        [27],
        [13],
      ]);
      expect(find.byKey(const ValueKey('agent-working-status')), findsNothing);
      expect(
        find.byKey(const ValueKey('conversation-working-status-text')),
        findsOneWidget,
      );

      gatewayTerminalTransport.sessions.single.addOutput('pane output ready');
      await tester.pumpAndSettle();

      expect(find.byKey(const ValueKey('agent-working-status')), findsNothing);
      expect(
        find.byKey(const ValueKey('conversation-working-status-text')),
        findsOneWidget,
      );
      expect(find.text('pane output ready'), findsNothing);
    },
  );

  testWidgets('paired gateway project list sorts by recent activity', (
    tester,
  ) async {
    final profile = _pairedHost(hostId: 'server-host', deviceId: 'phone');
    final profileStore = await _profileStoreWith([profile]);
    final gatewayRepository = _ServerProjectsRepository([
      _projectFixture(
        id: 'older',
        displayName: 'Older',
        root: '/srv/older',
        lastActivityAt: '2026-07-04T09:01:00Z',
      ),
      _projectFixture(
        id: 'opened',
        displayName: 'Recently opened',
        root: '/srv/opened',
        lastOpenedAt: '2026-07-04T09:03:00Z',
      ),
      _projectFixture(
        id: 'reply',
        displayName: 'Latest reply',
        root: '/srv/reply',
        lastActivityAt: '2026-07-04T09:05:00Z',
      ),
    ]);

    await tester.pumpWidget(
      MaterialApp(
        home: ProjectHomeScreen(
          repository: FakeMobileCcbRepository.demo(),
          profileStore: profileStore,
          gatewayRepositoryFactory: (_) => gatewayRepository,
          gatewayTerminalTransportFactory: (_) => RecordingTerminalTransport(),
        ),
      ),
    );
    await tester.pumpAndSettle();

    await _activatePairedGatewayListOnly(tester);

    expect(
      tester
          .widgetList<ListTile>(
            find.descendant(
              of: find.byKey(const ValueKey('project-list')),
              matching: find.byType(ListTile),
            ),
          )
          .map((tile) => (tile.title! as Text).data),
      ['Latest reply', 'Recently opened', 'Older'],
    );
  });

  testWidgets(
    'paired gateway locally bumps opened project when returning list',
    (tester) async {
      await setTestSurfaceSize(tester, const Size(390, 844));
      final profile = _pairedHost(hostId: 'server-host', deviceId: 'phone');
      final profileStore = await _profileStoreWith([profile]);
      final gatewayRepository = _ServerProjectsRepository([
        _projectFixture(
          id: 'older',
          displayName: 'Older',
          root: '/srv/older',
          lastActivityAt: '2026-07-04T09:01:00Z',
        ),
        _projectFixture(
          id: 'opened',
          displayName: 'Opened stale source',
          root: '/srv/opened',
          lastOpenedAt: '2026-07-04T08:00:00Z',
        ),
        _projectFixture(
          id: 'reply',
          displayName: 'Latest reply',
          root: '/srv/reply',
          lastActivityAt: '2026-07-04T09:05:00Z',
        ),
      ]);

      await tester.pumpWidget(
        MaterialApp(
          home: ProjectHomeScreen(
            repository: FakeMobileCcbRepository.demo(),
            profileStore: profileStore,
            gatewayRepositoryFactory: (_) => gatewayRepository,
            gatewayTerminalTransportFactory:
                (_) => RecordingTerminalTransport(),
          ),
        ),
      );
      await tester.pumpAndSettle();

      await _activatePairedGatewayListOnly(tester);
      await tester.tap(find.byKey(const ValueKey('project-open-opened')));
      await tester.pumpAndSettle();
      expect(
        find.byKey(const ValueKey('selected-agent-workspace')),
        findsOneWidget,
      );

      await tester.tap(find.byKey(const ValueKey('project-back-button')));
      await tester.pumpAndSettle();

      expect(
        tester
            .widgetList<ListTile>(
              find.descendant(
                of: find.byKey(const ValueKey('project-list')),
                matching: find.byType(ListTile),
              ),
            )
            .map((tile) => (tile.title! as Text).data)
            .take(3),
        ['Opened stale source', 'Latest reply', 'Older'],
      );
    },
  );

  testWidgets('paired gateway does not poll open project execution status', (
    tester,
  ) async {
    await setTestSurfaceSize(tester, const Size(390, 844));
    final profile = _pairedHost(hostId: 'server-host', deviceId: 'phone');
    final profileStore = await _profileStoreWith([profile]);
    final gatewayRepository = _ServerProjectsRepository([
      _projectFixture(
        id: 'test_ccb2',
        displayName: 'test_ccb2',
        root: '/srv/ccb/test_ccb2',
        activityState: 'idle',
        activitySource: 'provider_pane',
        activityReason: 'provider_prompt_idle',
      ),
    ]);

    await tester.pumpWidget(
      MaterialApp(
        home: ProjectHomeScreen(
          repository: FakeMobileCcbRepository.demo(),
          profileStore: profileStore,
          gatewayRepositoryFactory: (_) => gatewayRepository,
          gatewayTerminalTransportFactory: (_) => RecordingTerminalTransport(),
        ),
      ),
    );
    await tester.pumpAndSettle();
    await _activatePairedGatewayListOnly(tester);
    await tester.tap(find.byKey(const ValueKey('project-open-test_ccb2')));
    await tester.pumpAndSettle();

    expect(gatewayRepository.getProjectViewCalls, ['test_ccb2']);
    expect(find.byKey(const ValueKey('agent-working-status')), findsNothing);
    // Chip state labels reflect the already loaded view (an agent may be
    // legitimately working from queue data); the no-poll guarantee is the
    // getProjectViewCalls assertion above, and the chat header status must
    // stay absent until an explicit status fetch happens.

    gatewayRepository.replaceProjects([
      _projectFixture(
        id: 'test_ccb2',
        displayName: 'test_ccb2',
        root: '/srv/ccb/test_ccb2',
        activityState: 'active',
        activitySource: 'codex_runtime',
        activityReason: 'codex_working_status_line',
      ),
    ]);

    await tester.pump(const Duration(seconds: 2));
    await tester.pump();

    expect(gatewayRepository.getProjectViewCalls, ['test_ccb2']);
    expect(find.byKey(const ValueKey('agent-working-status')), findsNothing);
    expect(
      find.byKey(const ValueKey('conversation-working-status-text')),
      findsNothing,
    );
  });

  testWidgets('paired gateway refreshes server project list', (tester) async {
    final profile = _pairedHost(hostId: 'server-host', deviceId: 'phone');
    final profileStore = await _profileStoreWith([profile]);
    final gatewayRepository = _ServerProjectsRepository([
      _projectFixture(
        id: 'test_ccb2',
        displayName: 'test_ccb2',
        root: '/srv/ccb/test_ccb2',
      ),
    ]);

    await tester.pumpWidget(
      MaterialApp(
        home: ProjectHomeScreen(
          repository: FakeMobileCcbRepository.demo(),
          profileStore: profileStore,
          gatewayRepositoryFactory: (_) => gatewayRepository,
          gatewayTerminalTransportFactory: (_) => RecordingTerminalTransport(),
        ),
      ),
    );
    await tester.pumpAndSettle();

    await _activatePairedGatewayListOnly(tester);

    expect(gatewayRepository.listProjectsCalls, 1);
    expect(find.byKey(const ValueKey('project-list')), findsOneWidget);
    expect(
      find.byKey(const ValueKey('project-list-refresh-action')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('project-list-settings-action')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('project-open-test_ccb2')),
      findsOneWidget,
    );
    expect(find.byKey(const ValueKey('project-open-ccb_mobile')), findsNothing);

    gatewayRepository.replaceProjects([
      _projectFixture(
        id: 'test_ccb2',
        displayName: 'test_ccb2',
        root: '/srv/ccb/test_ccb2',
      ),
      _projectFixture(
        id: 'ccb_mobile',
        displayName: 'ccb_mobile',
        root: '/home/bfly/yunwei/ccb_mobile',
      ),
    ]);

    await tester.tap(find.byKey(const ValueKey('project-list-refresh-action')));
    await tester.pumpAndSettle();

    expect(gatewayRepository.listProjectsCalls, 2);
    expect(
      find.byKey(const ValueKey('project-open-test_ccb2')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('project-open-ccb_mobile')),
      findsOneWidget,
    );
    expect(find.text('/home/bfly/yunwei/ccb_mobile'), findsOneWidget);
  });

  testWidgets('paired gateway project list opens setup settings and returns', (
    tester,
  ) async {
    final profile = _pairedHost(hostId: 'server-host', deviceId: 'phone');
    final profileStore = await _profileStoreWith([profile]);
    final gatewayRepository = _ServerProjectsRepository([
      _projectFixture(
        id: 'test_ccb2',
        displayName: 'test_ccb2',
        root: '/srv/ccb/test_ccb2',
      ),
    ]);

    await tester.pumpWidget(
      MaterialApp(
        home: ProjectHomeScreen(
          repository: FakeMobileCcbRepository.demo(),
          profileStore: profileStore,
          gatewayRepositoryFactory: (_) => gatewayRepository,
          gatewayTerminalTransportFactory: (_) => RecordingTerminalTransport(),
        ),
      ),
    );
    await tester.pumpAndSettle();

    await _activatePairedGatewayListOnly(tester);

    expect(find.byKey(const ValueKey('project-list')), findsOneWidget);
    await tester.tap(
      find.byKey(const ValueKey('project-list-settings-action')),
    );
    await tester.pumpAndSettle();

    expect(
      find.byKey(const ValueKey('project-home-onboarding')),
      findsOneWidget,
    );
    await expandTile(tester, const ValueKey('gateway-pairing-panel'));
    expect(find.byKey(const ValueKey('connection-code-field')), findsOneWidget);
    expect(find.byKey(const ValueKey('gateway-url-field')), findsNothing);

    tester
        .widget<IconButton>(
          find.byKey(const ValueKey('project-home-settings-back-button')),
        )
        .onPressed!();
    await tester.pumpAndSettle();

    expect(find.byKey(const ValueKey('project-list')), findsOneWidget);
    expect(
      find.byKey(const ValueKey('project-open-test_ccb2')),
      findsOneWidget,
    );
  });

  testWidgets('paired gateway project list refresh failure can retry', (
    tester,
  ) async {
    final profile = _pairedHost(hostId: 'server-host', deviceId: 'phone');
    final profileStore = await _profileStoreWith([profile]);
    final gatewayRepository = _ServerProjectsRepository([
      _projectFixture(
        id: 'test_ccb2',
        displayName: 'test_ccb2',
        root: '/srv/ccb/test_ccb2',
      ),
    ]);

    await tester.pumpWidget(
      MaterialApp(
        home: ProjectHomeScreen(
          repository: FakeMobileCcbRepository.demo(),
          profileStore: profileStore,
          gatewayRepositoryFactory: (_) => gatewayRepository,
          gatewayTerminalTransportFactory: (_) => RecordingTerminalTransport(),
        ),
      ),
    );
    await tester.pumpAndSettle();

    await _activatePairedGatewayListOnly(tester);

    gatewayRepository.listProjectsError = StateError('gateway unavailable');
    await tester.tap(find.byKey(const ValueKey('project-list-refresh-action')));
    await tester.pumpAndSettle();

    expect(
      find.byKey(const ValueKey('project-list-load-error')),
      findsOneWidget,
    );
    expect(find.textContaining('gateway unavailable'), findsOneWidget);

    gatewayRepository.listProjectsError = null;
    await tester.tap(find.byKey(const ValueKey('project-list-retry-button')));
    await tester.pumpAndSettle();

    expect(find.byKey(const ValueKey('project-list')), findsOneWidget);
    expect(
      find.byKey(const ValueKey('project-open-test_ccb2')),
      findsOneWidget,
    );
  });

  testWidgets(
    'paired gateway project load failure can return to project list',
    (tester) async {
      final profile = _pairedHost(hostId: 'server-host', deviceId: 'phone');
      final profileStore = await _profileStoreWith([profile]);
      final gatewayRepository = _ServerProjectsRepository([
        _projectFixture(
          id: 'test_ccb2',
          displayName: 'test_ccb2',
          root: '/srv/ccb/test_ccb2',
        ),
      ]);
      gatewayRepository.getProjectViewErrors['test_ccb2'] = StateError(
        'project unavailable',
      );

      await tester.pumpWidget(
        MaterialApp(
          home: ProjectHomeScreen(
            repository: FakeMobileCcbRepository.demo(),
            profileStore: profileStore,
            gatewayRepositoryFactory: (_) => gatewayRepository,
            gatewayTerminalTransportFactory:
                (_) => RecordingTerminalTransport(),
          ),
        ),
      );
      await tester.pumpAndSettle();

      await _activatePairedGatewayListOnly(tester);
      await tester.tap(find.byKey(const ValueKey('project-open-test_ccb2')));
      await tester.pumpAndSettle();

      expect(
        find.byKey(const ValueKey('project-view-load-error')),
        findsOneWidget,
      );
      expect(find.textContaining('project unavailable'), findsOneWidget);
      expect(
        find.byKey(const ValueKey('project-view-back-to-list-button')),
        findsOneWidget,
      );
      expect(
        find.byKey(const ValueKey('project-view-use-fake-button')),
        findsNothing,
      );

      gatewayRepository.getProjectViewErrors.clear();
      await tester.tap(
        find.byKey(const ValueKey('project-view-back-to-list-button')),
      );
      await tester.pumpAndSettle();

      expect(find.byKey(const ValueKey('project-list')), findsOneWidget);
      expect(
        find.byKey(const ValueKey('project-open-test_ccb2')),
        findsOneWidget,
      );
      expect(gatewayRepository.listProjectsCalls, 2);
    },
  );

  testWidgets(
    'paired project refresh failure uses reconnect banner without snackbar',
    (tester) async {
      final profile = _pairedHost(hostId: 'server-host', deviceId: 'phone');
      final profileStore = await _profileStoreWith([profile]);
      final gatewayRepository = _ServerProjectsRepository([
        _projectFixture(
          id: 'test_ccb2',
          displayName: 'test_ccb2',
          root: '/srv/ccb/test_ccb2',
        ),
      ]);

      await tester.pumpWidget(
        MaterialApp(
          home: ProjectHomeScreen(
            repository: FakeMobileCcbRepository.demo(),
            profileStore: profileStore,
            gatewayRepositoryFactory: (_) => gatewayRepository,
            gatewayTerminalTransportFactory:
                (_) => RecordingTerminalTransport(),
          ),
        ),
      );
      await tester.pumpAndSettle();
      await _activatePairedGatewayListOnly(tester);
      await tester.tap(find.byKey(const ValueKey('project-open-test_ccb2')));
      await tester.pumpAndSettle();

      gatewayRepository.getProjectViewErrors['test_ccb2'] = StateError(
        'automatic refresh unavailable',
      );
      await tester.tap(
        find.byKey(const ValueKey('agent-conversation-refresh-action')),
      );
      await tester.pump();
      await tester.pump();
      await tester.pump(const Duration(milliseconds: 100));

      expect(
        find.byKey(const ValueKey('gateway-reconnecting-banner')),
        findsOneWidget,
      );
      expect(
        find.textContaining('automatic refresh unavailable'),
        findsNothing,
      );
      expect(
        find.byKey(const ValueKey('selected-agent-workspace')),
        findsOneWidget,
      );
    },
  );

  testWidgets('paired back returns to server project list', (tester) async {
    final profile = _pairedHost(hostId: 'server-host', deviceId: 'phone');
    final profileStore = await _profileStoreWith([profile]);
    final gatewayRepository = _ServerProjectsRepository([
      _projectFixture(
        id: 'test_ccb2',
        displayName: 'test_ccb2',
        root: '/srv/ccb/test_ccb2',
      ),
    ]);

    await tester.pumpWidget(
      MaterialApp(
        home: ProjectHomeScreen(
          repository: FakeMobileCcbRepository.demo(),
          profileStore: profileStore,
          gatewayRepositoryFactory: (_) => gatewayRepository,
          gatewayTerminalTransportFactory: (_) => RecordingTerminalTransport(),
        ),
      ),
    );
    await tester.pumpAndSettle();
    await activateStoredGatewayProfile(tester);

    await tester.tap(find.byKey(const ValueKey('project-back-button')));
    await tester.pumpAndSettle();

    expect(find.byKey(const ValueKey('project-list')), findsOneWidget);
    expect(
      find.byKey(const ValueKey('project-open-test_ccb2')),
      findsOneWidget,
    );
    expect(gatewayRepository.getProjectViewCalls, ['test_ccb2']);
  });

  testWidgets(
    'paired system back steps from agent to project list to settings',
    (tester) async {
      final profile = _pairedHost(hostId: 'server-host', deviceId: 'phone');
      final profileStore = await _profileStoreWith([profile]);
      final gatewayRepository = _ServerProjectsRepository([
        _projectFixture(
          id: 'test_ccb2',
          displayName: 'test_ccb2',
          root: '/srv/ccb/test_ccb2',
        ),
      ]);

      await tester.pumpWidget(
        MaterialApp(
          home: ProjectHomeScreen(
            repository: FakeMobileCcbRepository.demo(),
            profileStore: profileStore,
            gatewayRepositoryFactory: (_) => gatewayRepository,
            gatewayTerminalTransportFactory:
                (_) => RecordingTerminalTransport(),
          ),
        ),
      );
      await tester.pumpAndSettle();
      await _activatePairedGatewayListOnly(tester);
      await tester.tap(find.byKey(const ValueKey('project-open-test_ccb2')));
      await tester.pumpAndSettle();

      expect(
        find.byKey(const ValueKey('selected-agent-workspace')),
        findsOneWidget,
      );

      await tester.binding.handlePopRoute();
      await tester.pumpAndSettle();

      expect(find.byKey(const ValueKey('project-list')), findsOneWidget);
      expect(
        find.byKey(const ValueKey('project-open-test_ccb2')),
        findsOneWidget,
      );

      await tester.binding.handlePopRoute();
      await tester.pumpAndSettle();

      expect(
        find.byKey(const ValueKey('project-home-onboarding')),
        findsOneWidget,
      );
      await expandTile(tester, const ValueKey('gateway-pairing-panel'));
      expect(
        find.byKey(const ValueKey('connection-code-field')),
        findsOneWidget,
      );
      expect(find.byKey(const ValueKey('gateway-url-field')), findsNothing);
    },
  );
}

Future<void> _activatePairedGatewayListOnly(WidgetTester tester) async {
  await openConnectionDetails(tester);
  await expandTile(tester, const ValueKey('runtime-mode-panel'));
  final segments = tester.widget<SegmentedButton<AppRuntimeMode>>(
    find.byKey(const ValueKey('runtime-mode-segments')),
  );
  segments.onSelectionChanged?.call({AppRuntimeMode.pairedGateway});
  await tester.pumpAndSettle();
  await dismissConnectionDetails(tester);
}

Future<GatewayHostProfileStore> _profileStoreWith(
  List<GatewayPairedHost> profiles,
) async {
  final store = GatewayHostProfileStore(secureStore: MemorySecureStore());
  for (final profile in profiles) {
    await store.save(profile);
  }
  return store;
}

GatewayPairedHost _pairedHost({
  required String hostId,
  required String deviceId,
}) {
  return GatewayPairedHost(
    profile: GatewayHostProfile(
      hostId: hostId,
      deviceId: deviceId,
      routeProvider: RouteProvider(
        kind: RouteProviderKind.lan,
        gatewayUrl: Uri.parse('http://127.0.0.1:8787'),
      ),
      scopes: const {
        'view',
        'focus',
        'terminal_input',
        'host_terminal',
        'lifecycle',
        'notify',
      },
    ),
    deviceToken: 'token-$hostId-$deviceId',
    projectId: hostId,
  );
}

Map<String, Object?> _projectFixture({
  required String id,
  required String displayName,
  required String root,
  String? activityState,
  String? activitySource,
  String? activityReason,
  String? lastOpenedAt,
  String? lastActivityAt,
}) {
  final view =
      jsonDecode(jsonEncode(demoProjectViewFixture['view']))
          as Map<String, Object?>;
  view['project'] = <String, Object?>{
    'id': id,
    'root': root,
    'display_name': displayName,
    'health': 'healthy',
    if (lastOpenedAt != null) 'last_opened_at': lastOpenedAt,
    if (lastActivityAt != null) 'last_activity_at': lastActivityAt,
  };
  if (activityState != null ||
      activitySource != null ||
      activityReason != null) {
    final agents = view['agents']! as List<Object?>;
    final targetAgent = agents.cast<Map<String, Object?>>().firstWhere(
      (agent) => agent['name'] == 'mobile',
      orElse: () => agents.first! as Map<String, Object?>,
    );
    if (activityState != null) {
      targetAgent['activity_state'] = activityState;
    }
    if (activitySource != null) {
      targetAgent['activity_source'] = activitySource;
    }
    if (activityReason != null) {
      targetAgent['activity_reason'] = activityReason;
    }
  }
  return <String, Object?>{'view': view};
}

class _ServerProjectsRepository implements MobileCcbRepository {
  _ServerProjectsRepository(List<Map<String, Object?>> projectPayloads)
    : _delegates = _projectDelegates(projectPayloads);

  final Map<String, FakeMobileCcbRepository> _delegates;
  final getProjectViewCalls = <String>[];
  final submittedMessages = <CcbAgentMessageSubmitRequest>[];
  final getProjectViewErrors = <String, Object>{};
  Object? listProjectsError;
  var listProjectsCalls = 0;

  void replaceProjects(List<Map<String, Object?>> projectPayloads) {
    _delegates
      ..clear()
      ..addAll(_projectDelegates(projectPayloads));
  }

  @override
  Future<List<CcbProject>> listProjects() async {
    listProjectsCalls += 1;
    final error = listProjectsError;
    if (error != null) {
      throw error;
    }
    return [
      for (final delegate in _delegates.values)
        (await delegate.listProjects()).single,
    ];
  }

  @override
  Future<CcbProjectView> getProjectView(String projectId) async {
    getProjectViewCalls.add(projectId);
    final error = getProjectViewErrors[projectId];
    if (error != null) {
      throw error;
    }
    return _delegate(projectId).getProjectView(projectId);
  }

  @override
  Future<CcbProjectView> focusAgent({
    required String projectId,
    required String agent,
    required int namespaceEpoch,
  }) {
    return _delegate(projectId).focusAgent(
      projectId: projectId,
      agent: agent,
      namespaceEpoch: namespaceEpoch,
    );
  }

  @override
  Future<CcbProjectView> focusWindow({
    required String projectId,
    required String window,
    required int namespaceEpoch,
  }) {
    return _delegate(projectId).focusWindow(
      projectId: projectId,
      window: window,
      namespaceEpoch: namespaceEpoch,
    );
  }

  @override
  Future<ReadableTerminalHistory?> getReadableTerminalHistory({
    required String projectId,
    required String agent,
    required int namespaceEpoch,
    int maxLines = 200,
  }) {
    return _delegate(projectId).getReadableTerminalHistory(
      projectId: projectId,
      agent: agent,
      namespaceEpoch: namespaceEpoch,
      maxLines: maxLines,
    );
  }

  @override
  Future<CcbAgentConversation> getAgentConversation({
    required String projectId,
    required String agent,
    required int namespaceEpoch,
    int limit = 50,
    String? cursor,
  }) {
    return _delegate(projectId).getAgentConversation(
      projectId: projectId,
      agent: agent,
      namespaceEpoch: namespaceEpoch,
      limit: limit,
      cursor: cursor,
    );
  }

  @override
  Future<CcbAgentMessageSubmitResult> submitAgentMessage(
    CcbAgentMessageSubmitRequest request,
  ) {
    submittedMessages.add(request);
    return _delegate(request.projectId).submitAgentMessage(request);
  }

  @override
  Future<CcbProjectLifecycleResult> requestLifecycle({
    required String projectId,
    required CcbLifecycleAction action,
  }) {
    return _delegate(
      projectId,
    ).requestLifecycle(projectId: projectId, action: action);
  }

  @override
  Future<GatewayFileUploadResult> uploadFile({
    required String projectId,
    required String agentName,
    required String fileName,
    required String mimeType,
    required List<int> bytes,
  }) {
    return _delegate(projectId).uploadFile(
      projectId: projectId,
      agentName: agentName,
      fileName: fileName,
      mimeType: mimeType,
      bytes: bytes,
    );
  }

  @override
  Future<List<int>> downloadFile({
    required String projectId,
    required String agentName,
    required String fileId,
  }) {
    return _delegate(
      projectId,
    ).downloadFile(projectId: projectId, agentName: agentName, fileId: fileId);
  }

  FakeMobileCcbRepository _delegate(String projectId) {
    final delegate = _delegates[projectId];
    if (delegate == null) {
      throw ArgumentError.value(projectId, 'projectId', 'unknown project');
    }
    return delegate;
  }

  static Map<String, FakeMobileCcbRepository> _projectDelegates(
    List<Map<String, Object?>> projectPayloads,
  ) {
    return {
      for (final payload in projectPayloads)
        CcbProjectView.fromProjectViewPayload(
          payload,
        ).project.id: FakeMobileCcbRepository(projectViewPayload: payload),
    };
  }
}
