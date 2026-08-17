import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart' show ScrollDirection;
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:ccb_mobile/features/agent_chat/conversation_bubble.dart';
import 'package:ccb_mobile/features/agent_chat/agent_chat_state_helpers.dart';
import 'package:ccb_mobile/features/agent_chat/conversation_item_presentation.dart';
import 'package:ccb_mobile/models/ccb_conversation_item.dart';

void main() {
  test('preview text strips common markdown markers', () {
    final preview = conversationPreviewText('''
# Heading
- first item
1. second item
> quoted value
''');

    expect(preview, 'Heading\nfirst item\nsecond item');
  });

  test('terminal derived items stay plain and compact', () {
    final item = CcbConversationItem(
      id: 'terminal-output',
      agentName: 'lead',
      kind: CcbConversationItemKind.agentReply,
      title: 'Terminal output',
      body: '# Not authoritative markdown',
      format: 'markdown',
      source: 'tmux output / foreground',
    );

    expect(isTerminalDerivedConversationItem(item), isTrue);
    expect(shouldRenderConversationMarkdown(item), isFalse);
    expect(conversationPreviewTextFor(item), '# Not authoritative markdown');
    expect(conversationPreviewMaxLines(item), 2);
    expect(conversationShouldCollapse(item, hasCustomChild: false), isTrue);
  });

  test('normal chat replies render markdown when marked as markdown', () {
    final item = CcbConversationItem(
      id: 'reply',
      agentName: 'lead',
      kind: CcbConversationItemKind.agentReply,
      title: 'Agent reply',
      body: '# Markdown reply',
      format: 'markdown',
      source: 'completion_snapshot',
    );

    expect(shouldRenderConversationMarkdown(item), isTrue);
    expect(conversationShouldCollapse(item, hasCustomChild: true), isTrue);
    expect(visibleConversationSourceLabel(item), isNull);
  });

  test('internal source labels stay hidden in chat bubbles', () {
    final terminalItem = CcbConversationItem(
      id: 'terminal-output',
      agentName: 'lead',
      kind: CcbConversationItemKind.agentReply,
      title: 'Terminal output',
      body: 'output',
      source: 'tmux output / live',
    );
    const userItem = CcbConversationItem(
      id: 'user-1',
      agentName: 'lead',
      kind: CcbConversationItemKind.userMessage,
      title: 'You',
      body: 'hello',
      source: 'mobile_gateway',
    );

    expect(visibleConversationSourceLabel(terminalItem), isNull);
    expect(visibleConversationSourceLabel(userItem), isNull);
  });

  test('terminal preview preserves literal underscores', () {
    const item = CcbConversationItem(
      id: 'terminal-output',
      agentName: 'lead',
      kind: CcbConversationItemKind.agentReply,
      title: 'Terminal output',
      body: 'MOBILE_DYNAMIC_SYNC_OK',
      source: 'tmux output / live',
    );

    expect(conversationPreviewTextFor(item), 'MOBILE_DYNAMIC_SYNC_OK');
  });

  test('agent reply display titles use agent name for normal chat replies', () {
    final item = CcbConversationItem(
      id: 'reply',
      agentName: 'lead',
      kind: CcbConversationItemKind.agentReply,
      title: 'Agent reply',
      body: 'hello',
      source: 'completion_snapshot',
    );
    final terminalItem = CcbConversationItem(
      id: 'terminal-output',
      agentName: 'lead',
      kind: CcbConversationItemKind.agentReply,
      title: 'Terminal output',
      body: 'output',
      source: 'tmux output / live',
    );

    expect(conversationDisplayTitle(item), 'lead');
    expect(conversationDisplayTitle(terminalItem), 'Terminal output');
  });

  testWidgets('normal chat bubbles do not render internal source labels', (
    tester,
  ) async {
    final item = CcbConversationItem(
      id: 'reply',
      agentName: 'lead',
      kind: CcbConversationItemKind.agentReply,
      title: 'Agent reply',
      body: 'hello',
      source: 'completion_snapshot',
    );

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ConversationBubble(
            item: item,
            expanded: true,
            onToggleExpanded: (_) {},
          ),
        ),
      ),
    );

    expect(find.text('completion_snapshot'), findsNothing);
    expect(find.text('hello'), findsOneWidget);
  });

  testWidgets('user message title row shows sent time next to sender', (
    tester,
  ) async {
    final now = DateTime.now();
    final item = CcbConversationItem(
      id: 'user-1',
      agentName: 'lead',
      kind: CcbConversationItemKind.userMessage,
      title: 'You',
      body: 'hello',
      sentAt: DateTime(now.year, now.month, now.day, 11, 42),
      state: CcbConversationDeliveryState.sent,
    );

    await tester.pumpWidget(
      MaterialApp(
        home: MediaQuery(
          data: const MediaQueryData(
            size: Size(400, 800),
            alwaysUse24HourFormat: true,
          ),
          child: Scaffold(
            body: ConversationBubble(
              item: item,
              expanded: true,
              onToggleExpanded: (_) {},
            ),
          ),
        ),
      ),
    );

    expect(find.text('You'), findsOneWidget);
    expect(find.text('11:42'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('conversation-timestamp-user-1')),
      findsOneWidget,
    );
  });

  testWidgets(
    'agent reply title row uses agent name with sent time and duration',
    (tester) async {
      final now = DateTime.now();
      final item = CcbConversationItem(
        id: 'reply',
        agentName: 'lead',
        kind: CcbConversationItemKind.agentReply,
        title: 'Agent reply',
        body: 'done',
        sentAt: DateTime(now.year, now.month, now.day, 12, 3),
        durationMs: 72000,
        source: 'completion_snapshot',
      );

      await tester.pumpWidget(
        MaterialApp(
          home: MediaQuery(
            data: const MediaQueryData(
              size: Size(400, 800),
              alwaysUse24HourFormat: true,
            ),
            child: Scaffold(
              body: ConversationBubble(
                item: item,
                expanded: true,
                onToggleExpanded: (_) {},
              ),
            ),
          ),
        ),
      );

      expect(find.text('lead'), findsOneWidget);
      expect(find.text('Agent reply'), findsNothing);
      expect(find.text('12:03 · 1m 12s'), findsOneWidget);
    },
  );

  testWidgets('sent user messages omit redundant state chip', (tester) async {
    const item = CcbConversationItem(
      id: 'user-1',
      agentName: 'lead',
      kind: CcbConversationItemKind.userMessage,
      title: 'You',
      body: 'hello',
      state: CcbConversationDeliveryState.sent,
    );

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ConversationBubble(
            item: item,
            expanded: true,
            onToggleExpanded: (_) {},
          ),
        ),
      ),
    );

    expect(find.text('hello'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('conversation-state-user-1')),
      findsNothing,
    );
    expect(find.text('Sent'), findsNothing);
  });

  testWidgets('failed user messages keep actionable state chip', (
    tester,
  ) async {
    const item = CcbConversationItem(
      id: 'user-1',
      agentName: 'lead',
      kind: CcbConversationItemKind.userMessage,
      title: 'You',
      body: 'hello',
      state: CcbConversationDeliveryState.failed,
    );

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ConversationBubble(
            item: item,
            expanded: true,
            onToggleExpanded: (_) {},
          ),
        ),
      ),
    );

    expect(
      find.descendant(
        of: find.byKey(const ValueKey('conversation-state-user-1')),
        matching: find.text('Failed'),
      ),
      findsOneWidget,
    );
  });

  testWidgets('working reply uses normal surface with active border and beat', (
    tester,
  ) async {
    final item = CcbConversationItem(
      id: 'reply-working',
      agentName: 'lead',
      kind: CcbConversationItemKind.agentReply,
      title: 'Agent reply',
      body: 'Still running',
      source: 'provider_native/codex',
      startedAt: DateTime.now().add(const Duration(seconds: 10)),
    );

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ConversationBubble(
            item: item,
            expanded: true,
            isWorking: true,
            onToggleExpanded: (_) {},
          ),
        ),
      ),
    );

    expect(
      find.byKey(const ValueKey('conversation-working-reply-working')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('conversation-working-beat-reply-working')),
      findsOneWidget,
    );
    expect(find.byIcon(Icons.pending_rounded), findsOneWidget);
    expect(find.byType(CircularProgressIndicator), findsNothing);
    expect(find.text('Working · 00:00'), findsOneWidget);

    final material = tester.widget<Material>(
      find.byKey(const ValueKey('conversation-item-reply-working')),
    );
    final shape = material.shape as RoundedRectangleBorder;
    final colorScheme = ThemeData().colorScheme;
    expect(material.color, colorScheme.surfaceContainerLow);
    expect(material.color, isNot(colorScheme.primaryContainer));
    expect(material.color, isNot(colorScheme.errorContainer));
    expect(material.color, isNot(colorScheme.tertiaryContainer));
    expect(shape.side.color, conversationWorkingBubbleAccent(colorScheme));
    expect(shape.side.color, isNot(colorScheme.primary));
    expect(shape.side.color, isNot(colorScheme.tertiary));
    expect(shape.side.width, 2.4);

    expect(conversationWorkingBubbleBorderSide(colorScheme).width, 2.4);
    expect(
      find.descendant(
        of: find.byKey(const ValueKey('conversation-item-reply-working')),
        matching: find.byType(AnimatedBuilder),
      ),
      findsNothing,
    );
    expect(
      tester
          .widgetList<DecoratedBox>(find.byType(DecoratedBox))
          .map((box) => box.decoration)
          .whereType<BoxDecoration>()
          .any((decoration) => decoration.boxShadow != null),
      isFalse,
    );
  });

  testWidgets('working reply hides completed duration metadata', (
    tester,
  ) async {
    final item = CcbConversationItem(
      id: 'reply-working-completed',
      agentName: 'lead',
      kind: CcbConversationItemKind.agentReply,
      title: 'Agent reply',
      body: 'Visible reply still running',
      source: 'provider_native/codex',
      sentAt: DateTime(2026, 7, 2, 9, 0, 2),
      startedAt: DateTime(2026, 7, 2, 9, 0, 1),
      completedAt: DateTime(2026, 7, 2, 9, 0, 2),
      durationMs: 1000,
    );

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ConversationBubble(
            item: item,
            expanded: true,
            isWorking: true,
            onToggleExpanded: (_) {},
          ),
        ),
      ),
    );

    expect(
      find.byKey(
        const ValueKey('conversation-working-reply-working-completed'),
      ),
      findsOneWidget,
    );
    expect(find.textContaining('1s'), findsNothing);
    expect(find.textContaining('Working ·'), findsOneWidget);
    expect(
      find.byKey(
        const ValueKey('conversation-timestamp-reply-working-completed'),
      ),
      findsOneWidget,
    );
  });

  testWidgets('failed reply keeps error styling over working state', (
    tester,
  ) async {
    const item = CcbConversationItem(
      id: 'reply-failed',
      agentName: 'lead',
      kind: CcbConversationItemKind.agentReply,
      title: 'Agent reply',
      body: 'Failed after running',
      source: 'provider_native/codex',
      state: CcbConversationDeliveryState.failed,
    );

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ConversationBubble(
            item: item,
            expanded: true,
            isWorking: true,
            onToggleExpanded: (_) {},
          ),
        ),
      ),
    );

    expect(
      find.byKey(const ValueKey('conversation-working-reply-failed')),
      findsNothing,
    );
    expect(
      find.byKey(const ValueKey('conversation-working-beat-reply-failed')),
      findsNothing,
    );
    expect(find.byIcon(Icons.pending_rounded), findsNothing);

    final material = tester.widget<Material>(
      find.byKey(const ValueKey('conversation-item-reply-failed')),
    );
    final shape = material.shape as RoundedRectangleBorder;
    final colorScheme = ThemeData().colorScheme;
    expect(shape.side.color, colorScheme.error);
    expect(shape.side.width, 1);
  });

  testWidgets('expanded long bubbles can fill available height and scroll', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(400, 800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    final item = CcbConversationItem(
      id: 'long-reply',
      agentName: 'lead',
      kind: CcbConversationItemKind.agentReply,
      title: 'Agent reply',
      body: List.generate(80, (index) => 'line $index').join('\n'),
      source: 'completion_snapshot',
    );

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ConversationBubble(
            item: item,
            expanded: true,
            onToggleExpanded: (_) {},
          ),
        ),
      ),
    );

    final viewportFinder = find.byKey(
      const ValueKey('conversation-body-viewport-long-reply'),
    );
    expect(viewportFinder, findsOneWidget);
    expect(
      tester.getSize(viewportFinder).height,
      conversationBodyViewportMaxHeight(const Size(400, 800)),
    );
    expect(tester.getSize(viewportFinder).height, greaterThan(420));
    expect(find.byType(Scrollbar), findsOneWidget);

    final bodyScrollView = tester.widget<SingleChildScrollView>(
      find
          .descendant(
            of: viewportFinder,
            matching: find.byType(SingleChildScrollView),
          )
          .first,
    );
    expect(
      bodyScrollView.controller!.position.pixels,
      closeTo(bodyScrollView.controller!.position.maxScrollExtent, 1),
    );
  });

  testWidgets('expanded bubble keeps collapse action in the header', (
    tester,
  ) async {
    final toggledIds = <String>[];
    final item = CcbConversationItem(
      id: 'long-reply',
      agentName: 'lead',
      kind: CcbConversationItemKind.agentReply,
      title: 'Agent reply',
      body: List.generate(80, (index) => 'line $index').join('\n'),
      source: 'completion_snapshot',
    );

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ConversationBubble(
            item: item,
            expanded: true,
            onToggleExpanded: toggledIds.add,
          ),
        ),
      ),
    );

    expect(
      find.byKey(const ValueKey('conversation-floating-collapse-long-reply')),
      findsNothing,
    );
    expect(
      find.byKey(const ValueKey('conversation-expand-long-reply')),
      findsOneWidget,
    );

    await tester.tap(
      find.byKey(const ValueKey('conversation-expand-long-reply')),
    );
    await tester.pumpAndSettle();
    expect(toggledIds, ['long-reply']);
  });

  testWidgets('expanded bubble boundary overscroll hands off to timeline', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(400, 800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    final timelineController = ScrollController();
    addTearDown(timelineController.dispose);

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ListView(
            key: const ValueKey('parent-timeline'),
            controller: timelineController,
            children: const [SizedBox(height: 1200)],
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    timelineController.jumpTo(250);
    await tester.pump();

    final context = tester.element(
      find.byKey(const ValueKey('parent-timeline')),
    );
    final initialOffset = timelineController.offset;
    final handedOff = handoffConversationBodyBoundaryOverscroll(
      OverscrollNotification(
        metrics: FixedScrollMetrics(
          minScrollExtent: 0,
          maxScrollExtent: 800,
          pixels: 800,
          viewportDimension: 360,
          axisDirection: AxisDirection.down,
          devicePixelRatio: 1,
        ),
        context: context,
        dragDetails: DragUpdateDetails(
          globalPosition: Offset(200, 200),
          delta: Offset(0, -48),
        ),
        overscroll: 48,
      ),
      timelineController,
    );

    expect(handedOff, isTrue);
    final afterBottomHandoff = timelineController.offset;
    expect(afterBottomHandoff, greaterThan(initialOffset));

    final handedBack = handoffConversationBodyBoundaryOverscroll(
      OverscrollNotification(
        metrics: FixedScrollMetrics(
          minScrollExtent: 0,
          maxScrollExtent: 800,
          pixels: 0,
          viewportDimension: 360,
          axisDirection: AxisDirection.down,
          devicePixelRatio: 1,
        ),
        context: context,
        dragDetails: DragUpdateDetails(
          globalPosition: const Offset(200, 200),
          delta: const Offset(0, 48),
        ),
        overscroll: -48,
      ),
      timelineController,
    );
    expect(handedBack, isTrue);
    expect(timelineController.offset, lessThan(afterBottomHandoff));
  });

  testWidgets('expanded bubble reports inner drags to the timeline owner', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(400, 800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    final directions = <ScrollDirection>[];
    final item = CcbConversationItem(
      id: 'long-reply',
      agentName: 'lead',
      kind: CcbConversationItemKind.agentReply,
      title: 'Agent reply',
      body: List.generate(100, (index) => 'line $index').join('\n'),
      source: 'completion_snapshot',
    );

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ConversationBubble(
            item: item,
            expanded: true,
            onUserScrollDirectionChanged: directions.add,
            onToggleExpanded: (_) {},
          ),
        ),
      ),
    );

    final scrollableContext = tester.element(
      find.descendant(
        of: find.byKey(const ValueKey('conversation-body-viewport-long-reply')),
        matching: find.byType(SingleChildScrollView),
      ),
    );
    ScrollUpdateNotification(
      metrics: FixedScrollMetrics(
        minScrollExtent: 0,
        maxScrollExtent: 800,
        pixels: 120,
        viewportDimension: 360,
        axisDirection: AxisDirection.down,
        devicePixelRatio: 1,
      ),
      context: scrollableContext,
      dragDetails: DragUpdateDetails(
        globalPosition: Offset(200, 200),
        delta: Offset(0, -40),
      ),
      scrollDelta: 40,
    ).dispatch(scrollableContext);
    await tester.pump();

    expect(directions, contains(ScrollDirection.reverse));
  });

  testWidgets('expanded bubble boundary handoff preserves fling momentum', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(400, 800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    final timelineController = ScrollController();
    addTearDown(timelineController.dispose);
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ListView(
            controller: timelineController,
            children: const [SizedBox(height: 1600)],
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    timelineController.jumpTo(300);
    await tester.pump();

    final beforeFling = timelineController.offset;
    expect(
      continueConversationBodyBoundaryFling(-1400, timelineController),
      isTrue,
    );
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 120));

    expect(timelineController.offset, greaterThan(beforeFling));
  });

  testWidgets('new inner drag stops previous timeline momentum', (
    tester,
  ) async {
    tester.view.physicalSize = const Size(400, 800);
    tester.view.devicePixelRatio = 1;
    addTearDown(tester.view.resetPhysicalSize);
    addTearDown(tester.view.resetDevicePixelRatio);

    final timelineController = ScrollController();
    addTearDown(timelineController.dispose);
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ListView(
            controller: timelineController,
            children: const [SizedBox(height: 1600)],
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    timelineController.jumpTo(200);
    await tester.pump();
    timelineController.animateTo(
      700,
      duration: const Duration(seconds: 1),
      curve: Curves.linear,
    );
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 120));

    expect(stopConversationTimelineActivity(timelineController), isTrue);
    final stoppedOffset = timelineController.offset;
    await tester.pump(const Duration(milliseconds: 500));

    expect(timelineController.offset, closeTo(stoppedOffset, 0.1));
  });

  test('unconfirmed pane sends use check pane label', () {
    expect(
      conversationStateLabel(CcbConversationDeliveryState.unconfirmed),
      'Check pane',
    );
  });

  testWidgets(
    'collapsed attachment rows stay inside expanded message content',
    (tester) async {
      var expanded = false;
      final item = CcbConversationItem(
        id: 'msg-with-file',
        agentName: 'mobile',
        kind: CcbConversationItemKind.agentReply,
        title: 'Agent reply',
        body: List.generate(80, (index) => 'line $index').join('\n'),
        attachments: const [
          CcbMessageAttachment(
            fileId: 'file-inside',
            fileName: 'notes.txt',
            mimeType: 'text/plain',
            sizeBytes: 2048,
          ),
        ],
      );

      await tester.pumpWidget(
        MaterialApp(
          home: StatefulBuilder(
            builder:
                (context, setState) => Scaffold(
                  body: ConversationBubble(
                    item: item,
                    expanded: expanded,
                    onToggleExpanded: (_) {
                      setState(() => expanded = true);
                    },
                    onDownloadAttachment: (_) {},
                  ),
                ),
          ),
        ),
      );

      expect(
        find.byKey(
          const ValueKey('conversation-attachment-list-msg-with-file'),
        ),
        findsNothing,
      );

      await tester.tap(
        find.byKey(const ValueKey('conversation-expand-msg-with-file')),
      );
      await tester.pumpAndSettle();

      expect(
        find.descendant(
          of: find.byKey(
            const ValueKey('conversation-body-viewport-msg-with-file'),
          ),
          matching: find.byKey(
            const ValueKey('conversation-attachment-list-msg-with-file'),
          ),
        ),
        findsOneWidget,
      );
    },
  );

  testWidgets('conversation attachments expose download and progress states', (
    tester,
  ) async {
    final item = CcbConversationItem(
      id: 'msg-1',
      agentName: 'mobile',
      kind: CcbConversationItemKind.agentReply,
      title: 'Agent reply',
      body: 'See files',
      attachments: const [
        CcbMessageAttachment(
          fileId: 'file-1',
          fileName: 'notes.txt',
          mimeType: 'text/plain',
          sizeBytes: 2048,
        ),
        CcbMessageAttachment(
          fileId: 'file-2',
          fileName: 'image.png',
          mimeType: 'image/png',
          sizeBytes: 4096,
        ),
      ],
    );
    final downloads = <String>[];

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ListView(
            children: [
              ConversationBubble(
                item: item,
                expanded: true,
                onToggleExpanded: (_) {},
                downloadingAttachmentIds: const {'file-2'},
                onDownloadAttachment: (attachment) {
                  downloads.add(attachment.fileId);
                },
              ),
            ],
          ),
        ),
      ),
    );

    expect(
      find.byKey(const ValueKey('conversation-attachment-list-msg-1')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('conversation-body-viewport-msg-1')),
      findsNothing,
    );
    expect(
      find.descendant(
        of: find.byKey(const ValueKey('conversation-item-msg-1')),
        matching: find.byKey(
          const ValueKey('conversation-attachment-list-msg-1'),
        ),
      ),
      findsOneWidget,
    );
    expect(
      tester
          .getSize(find.byKey(const ValueKey('conversation-item-msg-1')))
          .height,
      lessThan(220),
    );
    expect(
      find.byKey(const ValueKey('conversation-attachment-chip-file-1')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('conversation-attachment-download-file-1')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('agent-attachment-progress-file-2')),
      findsOneWidget,
    );

    await tester.tap(
      find.byKey(const ValueKey('conversation-attachment-chip-file-1')),
    );
    await tester.pump(const Duration(milliseconds: 300));

    expect(downloads, isEmpty);
    expect(
      find.byKey(const ValueKey('conversation-attachment-actions-file-1')),
      findsOneWidget,
    );
    tester
        .widget<ListTile>(
          find.byKey(
            const ValueKey('conversation-attachment-action-download-file-1'),
          ),
        )
        .onTap!();
    await tester.pump();
    expect(downloads, ['file-1']);
  });

  testWidgets('conversation attachment tap can open through action sheet', (
    tester,
  ) async {
    final item = CcbConversationItem(
      id: 'msg-actions-tap',
      agentName: 'mobile',
      kind: CcbConversationItemKind.agentReply,
      title: 'Agent reply',
      body: 'See files',
      attachments: const [
        CcbMessageAttachment(
          fileId: 'file-1',
          fileName: 'notes.txt',
          mimeType: 'text/plain',
          sizeBytes: 2048,
        ),
      ],
    );
    final downloads = <String>[];
    final opens = <String>[];

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ConversationBubble(
            item: item,
            expanded: true,
            onToggleExpanded: (_) {},
            onDownloadAttachment: (attachment) {
              downloads.add(attachment.fileId);
            },
            onOpenAttachment: (attachment) {
              opens.add(attachment.fileId);
            },
          ),
        ),
      ),
    );

    await tester.tap(
      find.byKey(const ValueKey('conversation-attachment-chip-file-1')),
    );
    await tester.pumpAndSettle();

    expect(downloads, isEmpty);
    expect(opens, isEmpty);
    expect(
      find.byKey(const ValueKey('conversation-attachment-actions-file-1')),
      findsOneWidget,
    );
    await tester.tap(
      find.byKey(const ValueKey('conversation-attachment-action-open-file-1')),
    );
    await tester.pumpAndSettle();

    expect(downloads, isEmpty);
    expect(opens, ['file-1']);
  });

  testWidgets('conversation attachments expose long-press download and open', (
    tester,
  ) async {
    final item = CcbConversationItem(
      id: 'msg-actions',
      agentName: 'mobile',
      kind: CcbConversationItemKind.agentReply,
      title: 'Agent reply',
      body: 'See files',
      attachments: const [
        CcbMessageAttachment(
          fileId: 'file-1',
          fileName: 'notes.txt',
          mimeType: 'text/plain',
          sizeBytes: 2048,
        ),
      ],
    );
    final downloads = <String>[];
    final opens = <String>[];

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ConversationBubble(
            item: item,
            expanded: true,
            onToggleExpanded: (_) {},
            onDownloadAttachment: (attachment) {
              downloads.add(attachment.fileId);
            },
            onOpenAttachment: (attachment) {
              opens.add(attachment.fileId);
            },
          ),
        ),
      ),
    );

    await tester.longPress(
      find.byKey(const ValueKey('conversation-attachment-chip-file-1')),
    );
    await tester.pumpAndSettle();

    expect(
      find.byKey(const ValueKey('conversation-attachment-actions-file-1')),
      findsOneWidget,
    );
    await tester.tap(
      find.byKey(
        const ValueKey('conversation-attachment-action-download-file-1'),
      ),
    );
    await tester.pumpAndSettle();
    expect(downloads, ['file-1']);
    expect(opens, isEmpty);

    await tester.longPress(
      find.byKey(const ValueKey('conversation-attachment-chip-file-1')),
    );
    await tester.pumpAndSettle();
    await tester.tap(
      find.byKey(const ValueKey('conversation-attachment-action-open-file-1')),
    );
    await tester.pumpAndSettle();
    expect(opens, ['file-1']);
  });

  testWidgets('artifact markdown links show attachment actions', (
    tester,
  ) async {
    final item = CcbConversationItem(
      id: 'artifact-reply',
      agentName: 'mobile',
      kind: CcbConversationItemKind.agentReply,
      title: 'Agent reply',
      body: '[Download artifact](ccb-artifact://artifact-1)',
      format: 'markdown',
      source: 'completion_snapshot',
      attachments: const [
        CcbMessageAttachment(
          fileId: 'artifact-1',
          fileName: 'artifact.txt',
          mimeType: 'text/plain',
          sizeBytes: 32,
        ),
      ],
    );
    CcbMessageAttachment? downloaded;
    CcbMessageAttachment? opened;

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ConversationBubble(
            item: item,
            expanded: true,
            onToggleExpanded: (_) {},
            onDownloadAttachment: (attachment) {
              downloaded = attachment;
            },
            onOpenAttachment: (attachment) {
              opened = attachment;
            },
          ),
        ),
      ),
    );

    await tester.tap(
      find.byKey(const ValueKey('markdown-body-conversation-artifact-reply')),
    );
    await tester.pumpAndSettle();

    expect(downloaded, isNull);
    expect(opened, isNull);
    expect(
      find.byKey(const ValueKey('conversation-attachment-actions-artifact-1')),
      findsOneWidget,
    );
    expect(find.text('Blocked link'), findsNothing);

    await tester.tap(
      find.byKey(
        const ValueKey('conversation-attachment-action-download-artifact-1'),
      ),
    );
    await tester.pumpAndSettle();

    expect(downloaded?.fileId, 'artifact-1');
    expect(downloaded?.fileName, 'artifact.txt');

    await tester.tap(
      find.byKey(const ValueKey('markdown-body-conversation-artifact-reply')),
    );
    await tester.pumpAndSettle();
    await tester.tap(
      find.byKey(
        const ValueKey('conversation-attachment-action-open-artifact-1'),
      ),
    );
    await tester.pumpAndSettle();

    expect(opened?.fileId, 'artifact-1');
  });

  testWidgets('http markdown links ask before opening externally', (
    tester,
  ) async {
    const channel = MethodChannel('io.ccb.mobile/external_url');
    final opened = <String>[];
    tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(channel, (
      call,
    ) async {
      if (call.method == 'openUrl') {
        opened.add((call.arguments as Map)['url'] as String);
        return true;
      }
      return false;
    });
    addTearDown(() {
      tester.binding.defaultBinaryMessenger.setMockMethodCallHandler(
        channel,
        null,
      );
    });

    final item = CcbConversationItem(
      id: 'link-reply',
      agentName: 'mobile',
      kind: CcbConversationItemKind.agentReply,
      title: 'Agent reply',
      body: '[Open site](https://example.com/report)',
      format: 'markdown',
      source: 'completion_snapshot',
    );

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ConversationBubble(
            item: item,
            expanded: true,
            onToggleExpanded: (_) {},
          ),
        ),
      ),
    );

    await tester.tap(find.text('Open site'));
    await tester.pumpAndSettle();

    expect(
      find.byKey(const ValueKey('open-url-confirm-action')),
      findsOneWidget,
    );
    expect(opened, isEmpty);

    await tester.tap(find.byKey(const ValueKey('open-url-confirm-action')));
    await tester.pumpAndSettle();

    expect(opened, ['https://example.com/report']);
  });
}
