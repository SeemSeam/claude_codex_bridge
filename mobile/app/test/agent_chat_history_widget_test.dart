import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:xterm/xterm.dart';

import 'package:ccb_mobile/ccb_mobile.dart';
import 'package:ccb_mobile/features/agent_chat/conversation_bubble.dart';
import 'package:ccb_mobile/features/agent_chat/conversation_timeline.dart';
import 'package:ccb_mobile/features/agent_chat/selected_agent_workspace_model.dart';

import 'support/project_home_test_driver.dart';
import 'support/project_home_test_fakes.dart';

void main() {
  testWidgets('background refresh keeps an existing timeline visually stable', (
    tester,
  ) async {
    final repository = FakeMobileCcbRepository.demo();
    final view = CcbProjectView.fromProjectViewPayload(demoProjectViewFixture);
    final agent = view.agentByName('lead')!;
    final controller = ScrollController();
    addTearDown(controller.dispose);
    final item = CcbConversationItem(
      id: 'stable-during-refresh',
      agentName: agent.name,
      kind: CcbConversationItemKind.agentReply,
      title: 'Agent reply',
      body: 'Do not move while refreshing',
      source: 'provider_native/codex',
    );

    Widget timeline({required bool isLoading}) {
      return MaterialApp(
        home: SizedBox(
          height: 600,
          child: ConversationTimeline(
            repository: repository,
            view: view,
            agent: agent,
            contentItems: const [],
            initialHistory: null,
            items: [item],
            isLoading: isLoading,
            controller: controller,
            expandedItemIds: const {},
            downloadingAttachmentIds: const {},
            downloadedAttachmentIds: const {},
            onRetry: (_) {},
            onDeleteFailedMessage: (_) {},
            onToggleExpanded: (_) {},
            onNearEnd: () {},
            onUserNearEnd: () {},
            onNearStart: () {},
            onUserScrollDirectionChanged: (_) {},
            hasOlderItems: false,
            onDownloadAttachment: (_) {},
            onOpenAttachment: (_) {},
          ),
        ),
      );
    }

    await tester.pumpWidget(timeline(isLoading: false));
    final itemFinder = find.byKey(conversationTimelineItemKey(item.id));
    final settledTop = tester.getTopLeft(itemFinder).dy;

    await tester.pumpWidget(timeline(isLoading: true));
    await tester.pump();

    expect(
      find.byKey(const ValueKey('agent-conversation-loading')),
      findsNothing,
    );
    expect(tester.getTopLeft(itemFinder).dy, settledTop);
  });

  testWidgets('empty timeline still shows its initial loading state', (
    tester,
  ) async {
    final repository = FakeMobileCcbRepository.demo();
    final view = CcbProjectView.fromProjectViewPayload(demoProjectViewFixture);
    final agent = view.agentByName('lead')!;
    final controller = ScrollController();
    addTearDown(controller.dispose);

    await tester.pumpWidget(
      MaterialApp(
        home: SizedBox(
          height: 600,
          child: ConversationTimeline(
            repository: repository,
            view: view,
            agent: agent,
            contentItems: const [],
            initialHistory: null,
            items: const [],
            isLoading: true,
            controller: controller,
            expandedItemIds: const {},
            downloadingAttachmentIds: const {},
            downloadedAttachmentIds: const {},
            onRetry: (_) {},
            onDeleteFailedMessage: (_) {},
            onToggleExpanded: (_) {},
            onNearEnd: () {},
            onUserNearEnd: () {},
            onNearStart: () {},
            onUserScrollDirectionChanged: (_) {},
            hasOlderItems: false,
            onDownloadAttachment: (_) {},
            onOpenAttachment: (_) {},
          ),
        ),
      ),
    );

    expect(
      find.byKey(const ValueKey('agent-conversation-loading')),
      findsOneWidget,
    );
  });

  testWidgets('timeline item identity is safe across route recreation', (
    tester,
  ) async {
    final repository = FakeMobileCcbRepository.demo();
    final view = CcbProjectView.fromProjectViewPayload(demoProjectViewFixture);
    final agent = view.agentByName('lead')!;
    final controller = ScrollController();
    addTearDown(controller.dispose);
    final item = CcbConversationItem(
      id: 'shared-provider-item',
      agentName: agent.name,
      kind: CcbConversationItemKind.agentReply,
      title: 'Agent reply',
      body: 'Stable provider reply',
      source: 'provider_native/codex',
    );

    Widget timeline() {
      return MaterialApp(
        home: SizedBox(
          height: 600,
          child: ConversationTimeline(
            repository: repository,
            view: view,
            agent: agent,
            contentItems: const [],
            initialHistory: null,
            items: [item],
            isLoading: false,
            controller: controller,
            expandedItemIds: const {},
            downloadingAttachmentIds: const {},
            downloadedAttachmentIds: const {},
            onRetry: (_) {},
            onDeleteFailedMessage: (_) {},
            onToggleExpanded: (_) {},
            onNearEnd: () {},
            onUserNearEnd: () {},
            onNearStart: () {},
            onUserScrollDirectionChanged: (_) {},
            hasOlderItems: false,
            onDownloadAttachment: (_) {},
            onOpenAttachment: (_) {},
          ),
        ),
      );
    }

    await tester.pumpWidget(timeline());
    await tester.pumpWidget(const MaterialApp(home: SizedBox.shrink()));
    await tester.pumpWidget(timeline());
    await tester.pump();

    expect(find.text('Stable provider reply'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });

  testWidgets('provider session boundary renders as a compact divider', (
    tester,
  ) async {
    final repository = FakeMobileCcbRepository.demo();
    final view = CcbProjectView.fromProjectViewPayload(demoProjectViewFixture);
    final agent = view.agentByName('lead')!;
    final controller = ScrollController();
    addTearDown(controller.dispose);
    final boundary = providerSessionBoundaryConversationItem(
      agent.name,
      nextItem: const CcbConversationItem(
        id: 'new-session-user',
        agentName: 'lead',
        kind: CcbConversationItemKind.userMessage,
        title: 'You',
        body: 'new message',
        source: 'provider_native/codex',
        sessionId: 'session-new',
      ),
    );

    await tester.pumpWidget(
      MaterialApp(
        home: SizedBox(
          height: 600,
          child: ConversationTimeline(
            repository: repository,
            view: view,
            agent: agent,
            contentItems: const [],
            initialHistory: null,
            items: [boundary],
            isLoading: false,
            controller: controller,
            expandedItemIds: const {},
            downloadingAttachmentIds: const {},
            downloadedAttachmentIds: const {},
            onRetry: (_) {},
            onDeleteFailedMessage: (_) {},
            onToggleExpanded: (_) {},
            onNearEnd: () {},
            onUserNearEnd: () {},
            onNearStart: () {},
            onUserScrollDirectionChanged: (_) {},
            hasOlderItems: false,
            onDownloadAttachment: (_) {},
            onOpenAttachment: (_) {},
          ),
        ),
      ),
    );

    expect(find.text('New context'), findsOneWidget);
    expect(find.byType(ConversationBubble), findsNothing);
    expect(
      find.byKey(ValueKey('provider-session-boundary-${boundary.id}')),
      findsOneWidget,
    );
  });

  testWidgets('readable terminal history scrolls through retained blocks', (
    tester,
  ) async {
    await tester.pumpWidget(const CcbMobileApp(enableProductOnboarding: false));
    await tester.pumpAndSettle();
    await openCurrentProject(tester);

    expect(
      find.byKey(const ValueKey('terminal-history-block-mobile-checkpoint-09')),
      findsNothing,
    );

    await tapVisible(
      tester,
      const ValueKey('conversation-expand-terminal-history-mobile'),
    );
    final historyScroll = find.byKey(
      const ValueKey('readable-terminal-history-scroll'),
    );
    expect(historyScroll, findsOneWidget);
    await tester.ensureVisible(historyScroll);
    await tester.pumpAndSettle();
    await tester.drag(historyScroll, const Offset(0, -900));
    await tester.pumpAndSettle();

    expect(
      find.byKey(const ValueKey('terminal-history-block-mobile-checkpoint-09')),
      findsOneWidget,
    );
    expect(find.text('Checkpoint 09'), findsWidgets);
    expect(
      find.text('Long retained scrollback stays reachable by drag.'),
      findsWidgets,
    );
    expect(find.byType(TerminalView), findsNothing);
  });

  testWidgets('tmux history stays out of compact chat bubbles', (tester) async {
    await tester.pumpWidget(const CcbMobileApp(enableProductOnboarding: false));
    await tester.pumpAndSettle();
    await openCurrentProject(tester);

    const inputId = 'terminal-history-input-mobile-mobile-command-adb';
    expect(
      find.byKey(const ValueKey('conversation-item-$inputId')),
      findsNothing,
    );

    const outputId = 'terminal-history-output-mobile-mobile-diff-content';
    expect(
      find.byKey(const ValueKey('conversation-item-$outputId')),
      findsNothing,
    );
    expect(find.byType(TerminalView), findsNothing);
  });

  testWidgets('chat timeline virtualizes long histories and keeps expansion', (
    tester,
  ) async {
    final repository = LongConversationRepository(messageCount: 160);
    await tester.pumpWidget(
      MaterialApp(home: ProjectHomeScreen(repository: repository)),
    );
    await tester.pumpAndSettle();
    await openCurrentProject(tester);

    expect(
      find.byKey(const ValueKey('conversation-item-long-000')),
      findsNothing,
    );
    await dragUntilVisible(
      tester,
      const ValueKey('conversation-item-long-159'),
      const Offset(0, -700),
    );
    expect(
      find.byKey(const ValueKey('conversation-item-long-159')),
      findsOneWidget,
    );

    await dragUntilVisible(
      tester,
      const ValueKey('conversation-expand-long-000'),
      const Offset(0, 700),
    );
    await tapVisible(tester, const ValueKey('conversation-expand-long-000'));

    expect(
      find.byKey(const ValueKey('markdown-body-conversation-long-000')),
      findsOneWidget,
    );

    await dragUntilVisible(
      tester,
      const ValueKey('conversation-item-long-159'),
      const Offset(0, -700),
    );

    expect(
      find.byKey(const ValueKey('markdown-body-conversation-long-000')),
      findsNothing,
    );

    await dragUntilVisible(
      tester,
      const ValueKey('conversation-item-long-000'),
      const Offset(0, 700),
    );

    expect(
      find.byKey(const ValueKey('markdown-body-conversation-long-000')),
      findsOneWidget,
    );
  });

  testWidgets(
    'expanding bottom bubble reveals its bottom and collapse restores',
    (tester) async {
      await setTestSurfaceSize(tester, const Size(390, 844));
      await tester.pumpWidget(
        MaterialApp(
          home: ProjectHomeScreen(
            repository: LongConversationRepository(messageCount: 160),
          ),
        ),
      );
      await tester.pumpAndSettle();
      await openCurrentProject(tester);

      await dragUntilVisible(
        tester,
        const ValueKey('conversation-expand-long-159'),
        const Offset(0, -700),
      );
      final timeline = tester.widget<ListView>(
        find.byKey(const ValueKey('agent-chat-timeline')),
      );
      final controller = timeline.controller!;
      final beforeExpandOffset = controller.position.pixels;

      await tapVisible(tester, const ValueKey('conversation-expand-long-159'));
      await tester.pumpAndSettle();

      final timelineBottom =
          tester
              .getBottomRight(find.byKey(const ValueKey('agent-chat-timeline')))
              .dy;
      final itemBottom =
          tester
              .getBottomRight(
                find.byKey(conversationTimelineItemKey('long-159')),
              )
              .dy;

      expect(timelineBottom - itemBottom, lessThan(80));
      expect(
        tester
            .getSize(
              find.byKey(const ValueKey('conversation-body-viewport-long-159')),
            )
            .height,
        greaterThan(420),
      );
      expect(
        find.byKey(const ValueKey('markdown-body-conversation-long-159')),
        findsOneWidget,
      );

      await tapVisible(tester, const ValueKey('conversation-expand-long-159'));
      await tester.pumpAndSettle();

      expect(controller.position.pixels, closeTo(beforeExpandOffset, 20));
    },
  );

  testWidgets(
    'expanded bottom bubble cannot scroll into large trailing blank',
    (tester) async {
      await setTestSurfaceSize(tester, const Size(390, 844));
      await tester.pumpWidget(
        MaterialApp(
          home: ProjectHomeScreen(
            repository: LongConversationRepository(messageCount: 160),
          ),
        ),
      );
      await tester.pumpAndSettle();
      await openCurrentProject(tester);

      await dragUntilVisible(
        tester,
        const ValueKey('conversation-expand-long-159'),
        const Offset(0, -700),
      );
      final timeline = tester.widget<ListView>(
        find.byKey(const ValueKey('agent-chat-timeline')),
      );
      final controller = timeline.controller!;

      await tester.tap(
        find.byKey(const ValueKey('conversation-expand-long-159')),
      );
      await tester.pumpAndSettle();

      controller.jumpTo(controller.position.maxScrollExtent);
      await tester.pumpAndSettle();

      final itemBottom =
          tester
              .getBottomRight(
                find.byKey(conversationTimelineItemKey('long-159')),
              )
              .dy;
      final timelineBottom =
          tester
              .getBottomRight(find.byKey(const ValueKey('agent-chat-timeline')))
              .dy;

      expect(timelineBottom - itemBottom, lessThan(140));
    },
  );

  testWidgets('new latest bubble is comfortably revealed while following', (
    tester,
  ) async {
    await setTestSurfaceSize(tester, const Size(390, 844));
    await tester.pumpWidget(
      MaterialApp(
        home: ProjectHomeScreen(
          repository: LongConversationRepository(messageCount: 120),
        ),
      ),
    );
    await tester.pumpAndSettle();
    await openCurrentProject(tester);

    await tester.enterText(
      find.byKey(const ValueKey('agent-message-composer')),
      'follow latest send',
    );
    await tester.tap(find.byKey(const ValueKey('agent-message-send-button')));
    await tester.pumpAndSettle();

    expect(find.text('follow latest send'), findsOneWidget);
    final itemBottom =
        tester
            .getBottomRight(
              find.byKey(const ValueKey('conversation-item-local-mobile-0')),
            )
            .dy;
    final timelineBottom =
        tester
            .getBottomRight(find.byKey(const ValueKey('agent-chat-timeline')))
            .dy;

    expect(timelineBottom - itemBottom, greaterThanOrEqualTo(6));
    expect(timelineBottom - itemBottom, lessThan(140));
    expect(_composerGap(tester), lessThanOrEqualTo(8));
  });

  testWidgets(
    'remote latest bubble stays visible on its first replacement frame',
    (tester) async {
      await setTestSurfaceSize(tester, const Size(390, 844));
      final repository = _MutableLongConversationRepository(messageCount: 120);
      await tester.pumpWidget(
        MaterialApp(home: ProjectHomeScreen(repository: repository)),
      );
      await tester.pumpAndSettle();
      await openCurrentProject(tester);

      repository.appendReply(
        'remote-first-frame',
        'Remote reply must not flash below the viewport before auto-follow.',
      );
      await tester.tap(
        find.byKey(const ValueKey('agent-conversation-refresh-action')),
      );
      await tester.pump();
      await tester.pump();

      final latest = find.byKey(
        conversationTimelineItemKey('remote-first-frame'),
      );
      expect(latest, findsOneWidget);
      final itemBottom = tester.getBottomRight(latest).dy;
      final timelineBottom =
          tester
              .getBottomRight(find.byKey(const ValueKey('agent-chat-timeline')))
              .dy;
      expect(itemBottom, lessThanOrEqualTo(timelineBottom));
      expect(timelineBottom - itemBottom, lessThan(140));
    },
  );

  testWidgets('collapsed composer keeps latest bubble tight to composer', (
    tester,
  ) async {
    await setTestSurfaceSize(tester, const Size(390, 844));
    await tester.pumpWidget(
      MaterialApp(
        home: ProjectHomeScreen(
          repository: LongConversationRepository(messageCount: 120),
        ),
      ),
    );
    await tester.pumpAndSettle();
    await openCurrentProject(tester);

    await tester.tap(
      find.byKey(const ValueKey('agent-composer-collapse-action')),
    );
    await tester.pumpAndSettle();

    final itemBottom =
        tester
            .getBottomRight(find.byKey(conversationTimelineItemKey('long-119')))
            .dy;
    final timelineBottom =
        tester
            .getBottomRight(find.byKey(const ValueKey('agent-chat-timeline')))
            .dy;

    expect(
      find.byKey(const ValueKey('agent-chat-composer-collapsed')),
      findsOneWidget,
    );
    expect(
      _timelineBottomPadding(tester),
      conversationTimelineFollowLatestPadding,
    );
    expect(timelineBottom - itemBottom, greaterThanOrEqualTo(6));
    expect(timelineBottom - itemBottom, lessThan(100));
    expect(_composerGap(tester), lessThanOrEqualTo(8));
  });

  testWidgets('expanded composer without soft keyboard uses compact reveal', (
    tester,
  ) async {
    await setTestSurfaceSize(tester, const Size(390, 844));
    await tester.pumpWidget(
      MaterialApp(
        home: ProjectHomeScreen(
          repository: LongConversationRepository(messageCount: 120),
        ),
      ),
    );
    await tester.pumpAndSettle();
    await openCurrentProject(tester);

    await tester.tap(
      find.byKey(const ValueKey('agent-composer-collapse-action')),
    );
    await tester.pumpAndSettle();
    await tester.tap(
      find.byKey(const ValueKey('agent-composer-expand-action')),
    );
    await tester.pumpAndSettle();
    await tester.showKeyboard(
      find.byKey(const ValueKey('agent-message-composer')),
    );
    await tester.pumpAndSettle();

    var itemBottom =
        tester
            .getBottomRight(find.byKey(conversationTimelineItemKey('long-119')))
            .dy;
    var timelineBottom =
        tester
            .getBottomRight(find.byKey(const ValueKey('agent-chat-timeline')))
            .dy;

    expect(
      find.byKey(const ValueKey('agent-message-composer')),
      findsOneWidget,
    );
    expect(
      _timelineBottomPadding(tester),
      conversationTimelineExpandedComposerRevealPadding,
    );
    expect(timelineBottom - itemBottom, greaterThanOrEqualTo(16));
    expect(timelineBottom - itemBottom, lessThan(112));

    await tester.tap(
      find.byKey(const ValueKey('agent-composer-collapse-action')),
    );
    await tester.pumpAndSettle();

    itemBottom =
        tester
            .getBottomRight(find.byKey(conversationTimelineItemKey('long-119')))
            .dy;
    timelineBottom =
        tester
            .getBottomRight(find.byKey(const ValueKey('agent-chat-timeline')))
            .dy;

    expect(
      find.byKey(const ValueKey('agent-chat-composer-collapsed')),
      findsOneWidget,
    );
    expect(
      _timelineBottomPadding(tester),
      conversationTimelineFollowLatestPadding,
    );
    expect(timelineBottom - itemBottom, lessThan(100));
  });

  testWidgets('soft keyboard inset dynamically reveals latest bubble', (
    tester,
  ) async {
    await setTestSurfaceSize(tester, const Size(390, 844));
    setTestViewInsets(tester, const EdgeInsets.only(bottom: 220));
    await tester.pumpWidget(
      MaterialApp(
        home: ProjectHomeScreen(
          repository: LongConversationRepository(messageCount: 120),
        ),
      ),
    );
    await tester.pumpAndSettle();
    await openCurrentProject(tester);

    await tester.tap(
      find.byKey(const ValueKey('agent-composer-collapse-action')),
    );
    await tester.pumpAndSettle();
    await tester.tap(
      find.byKey(const ValueKey('agent-composer-expand-action')),
    );
    await tester.pumpAndSettle();

    final itemBottom =
        tester
            .getBottomRight(find.byKey(conversationTimelineItemKey('long-119')))
            .dy;
    final timelineBottom =
        tester
            .getBottomRight(find.byKey(const ValueKey('agent-chat-timeline')))
            .dy;

    expect(
      _timelineBottomPadding(tester),
      conversationTimelineComposerRevealPadding,
    );
    expect(timelineBottom - itemBottom, greaterThanOrEqualTo(24));
    expect(timelineBottom - itemBottom, lessThan(128));
  });

  testWidgets('remote latest bubble does not yank while reading history', (
    tester,
  ) async {
    await setTestSurfaceSize(tester, const Size(390, 844));
    final repository = _MutableLongConversationRepository(messageCount: 120);
    await tester.pumpWidget(
      MaterialApp(home: ProjectHomeScreen(repository: repository)),
    );
    await tester.pumpAndSettle();
    await openCurrentProject(tester);

    await dragUntilVisible(
      tester,
      const ValueKey('conversation-item-long-020'),
      const Offset(0, 700),
    );
    final timeline = tester.widget<ListView>(
      find.byKey(const ValueKey('agent-chat-timeline')),
    );
    final controller = timeline.controller!;
    final beforeRefreshOffset = controller.position.pixels;

    repository.appendReply('remote-new', 'New remote reply while reading.');
    await tester.tap(
      find.byKey(const ValueKey('agent-conversation-refresh-action')),
    );
    await tester.pumpAndSettle();

    expect(controller.position.pixels, closeTo(beforeRefreshOffset, 1));
    expect(
      find.byKey(const ValueKey('agent-new-messages-jump')),
      findsOneWidget,
    );
    expect(_composerGap(tester), lessThanOrEqualTo(8));
    expect(
      find.byKey(const ValueKey('conversation-item-remote-new')),
      findsNothing,
    );
    expect(
      find.byKey(const ValueKey('conversation-item-long-020')),
      findsOneWidget,
    );
  });

  testWidgets('user upward drag after composer expand cancels snap-back', (
    tester,
  ) async {
    await setTestSurfaceSize(tester, const Size(390, 844));
    await tester.pumpWidget(
      MaterialApp(
        home: ProjectHomeScreen(
          repository: LongConversationRepository(messageCount: 120),
        ),
      ),
    );
    await tester.pumpAndSettle();
    await openCurrentProject(tester);

    await tester.tap(
      find.byKey(const ValueKey('agent-composer-collapse-action')),
    );
    await tester.pumpAndSettle();
    await tester.tap(
      find.byKey(const ValueKey('agent-composer-expand-action')),
    );
    await tester.pumpAndSettle();

    await tester.enterText(
      find.byKey(const ValueKey('agent-message-composer')),
      'cancel follow latest send',
    );
    await tester.tap(find.byKey(const ValueKey('agent-message-send-button')));
    await tester.pump();

    final timelineFinder = find.byKey(const ValueKey('agent-chat-timeline'));
    await tester.drag(timelineFinder, const Offset(0, 420));
    await tester.pumpAndSettle();

    final timeline = tester.widget<ListView>(timelineFinder);
    final controller = timeline.controller!;
    expect(
      controller.position.maxScrollExtent - controller.position.pixels,
      greaterThan(conversationTimelineNearEndThreshold),
    );
  });

  testWidgets('user send scrolls to latest while reading older history', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        home: ProjectHomeScreen(
          repository: LongConversationRepository(messageCount: 120),
        ),
      ),
    );
    await tester.pumpAndSettle();
    await openCurrentProject(tester);

    await dragUntilVisible(
      tester,
      const ValueKey('conversation-item-long-020'),
      const Offset(0, 700),
    );
    expect(find.byKey(const ValueKey('agent-new-messages-jump')), findsNothing);

    await tester.enterText(
      find.byKey(const ValueKey('agent-message-composer')),
      'history safe send',
    );
    await tester.tap(find.byKey(const ValueKey('agent-message-send-button')));
    await tester.pumpAndSettle();

    expect(find.byKey(const ValueKey('agent-new-messages-jump')), findsNothing);
    expect(find.text('history safe send'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('conversation-item-long-020')),
      findsNothing,
    );
  });
}

double _composerGap(WidgetTester tester) {
  final timelineBottom =
      tester
          .getBottomLeft(find.byKey(const ValueKey('agent-chat-timeline')))
          .dy;
  final expandedComposer = find.byKey(const ValueKey('agent-chat-composer'));
  final collapsedComposer = find.byKey(
    const ValueKey('agent-chat-composer-collapsed'),
  );
  final composerTop =
      tester
          .getTopLeft(
            expandedComposer.evaluate().isNotEmpty
                ? expandedComposer
                : collapsedComposer,
          )
          .dy;
  return composerTop - timelineBottom;
}

double _timelineBottomPadding(WidgetTester tester) {
  final timeline = tester.widget<ListView>(
    find.byKey(const ValueKey('agent-chat-timeline')),
  );
  final padding = timeline.padding;
  if (padding is EdgeInsets) {
    return padding.bottom;
  }
  return padding?.resolve(TextDirection.ltr).bottom ?? 0;
}

class _MutableLongConversationRepository extends LongConversationRepository {
  _MutableLongConversationRepository({required super.messageCount});

  final List<CcbConversationItem> _extraReplies = [];

  void appendReply(String id, String body) {
    _extraReplies.add(
      CcbConversationItem(
        id: id,
        agentName: 'lead',
        kind: CcbConversationItemKind.agentReply,
        title: 'Agent reply',
        body: body,
        source: 'test',
      ),
    );
  }

  @override
  Future<CcbAgentConversation> getAgentConversation({
    required String projectId,
    required String agent,
    required int namespaceEpoch,
    int limit = 50,
    String? cursor,
  }) async {
    final conversation = await super.getAgentConversation(
      projectId: projectId,
      agent: agent,
      namespaceEpoch: namespaceEpoch,
      limit: limit,
      cursor: cursor,
    );
    return CcbAgentConversation(
      projectId: conversation.projectId,
      agentName: conversation.agentName,
      namespaceEpoch: conversation.namespaceEpoch,
      items: [...conversation.items, ..._extraReplies],
      nextCursor: conversation.nextCursor,
      generatedAt: DateTime.utc(2026, 6, 22, 12, _extraReplies.length),
    );
  }
}
