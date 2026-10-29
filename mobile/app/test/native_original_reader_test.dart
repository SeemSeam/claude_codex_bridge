import 'dart:async';

import 'package:ccb_mobile/features/agent_chat/conversation_bubble.dart';
import 'package:ccb_mobile/features/agent_chat/conversation_item_presentation.dart';
import 'package:ccb_mobile/models/ccb_agent_conversation.dart';
import 'package:ccb_mobile/models/ccb_conversation_item.dart';
import 'package:ccb_mobile/native_session/native_session.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:ccb_mobile/features/agent_chat/content_text_styles.dart';

NativeContentPage page(String text, {int index = 0, String version = 'v',
  String? next, bool before = false, bool after = false}) => NativeContentPage(
  blocks: [NativeContentBlock(entryId: 'entry', blockIndex: 0,
    role: 'assistant', type: 'text', text: text,
    continuedBefore: before, continuedAfter: after)],
  pageIndex: index, totalBlocks: 1, version: version, nextCursor: next,
);

CcbConversationItem overflowReply() => CcbConversationItem.fromJson({
  'id': 'large',
  'agent': 'demo',
  'kind': 'agent_reply',
  'body': 'Bounded preview',
  'source': 'provider_native/omp',
  'native_turn': {
    'schema_version': 1,
    'session_id': 'session',
    'turn_id': 'turn',
    'status': 'running',
    'inline_window': true,
    'entries': [
      {
        'id': 'latest', 'role': 'assistant',
        'blocks': [{'type': 'text', 'text': 'Readable latest answer'}],
      },
    ],
    'read_cursor': 'page0',
    'reason': 'Content exceeds inline budget; read original',
  },
});

Widget host(Future<NativeContentPage> Function(String) loader) => MaterialApp(
  home: Scaffold(
    body: SingleChildScrollView(
      child: ConversationBubble(
        item: overflowReply(),
        expanded: false,
        onToggleExpanded: (_) {},
        contentLoader: loader,
      ),
    ),
  ),
);

void main() {
  testWidgets('duplicate source fragments show a load error instead of duplicate widget keys', (tester) async {
    await tester.pumpWidget(host((_) async => const NativeContentPage(
      pageIndex: 0, totalBlocks: 1, version: 'duplicate', blocks: [
        NativeContentBlock(entryId: 'same', blockIndex: 0, role: 'assistant',
          type: 'text', text: 'First fragment', continuedAfter: true),
        NativeContentBlock(entryId: 'same', blockIndex: 0, role: 'assistant',
          type: 'text', text: 'Second fragment', continuedBefore: true),
      ],
    )));
    await tester.tap(find.byKey(const ValueKey('native-read-original')));
    await tester.pumpAndSettle();
    expect(find.textContaining('Content could not load'), findsOneWidget);
    expect(find.text('First fragment', findRichText: true), findsNothing);
    expect(find.text('Second fragment', findRichText: true), findsNothing);
    expect(tester.takeException(), isNull);
    await tester.tap(find.byKey(const ValueKey('native-original-close')));
    await tester.pumpAndSettle();
    expect(find.byKey(const ValueKey('native-original-reader')), findsNothing);
  });
  testWidgets('split fenced code remains Markdown and paging ignores rapid taps', (tester) async {
    final pending = Completer<NativeContentPage>();
    var nextCalls = 0;
    await tester.pumpWidget(host((cursor) async {
      if (cursor != 'page0') { nextCalls++; return pending.future; }
      return const NativeContentPage(pageIndex: 0, totalBlocks: 1, version: 'code',
        nextCursor: 'page1', blocks: [NativeContentBlock(entryId: 'code',
          blockIndex: 0, role: 'assistant', type: 'text',
          text: '```dart\nfinal first = "中文🙂";\n', continuedAfter: true,
          renderSuffix: '\n```')]);
    }));
    await tester.tap(find.byKey(const ValueKey('native-read-original')));
    await tester.pumpAndSettle();
    expect(find.textContaining('final first', findRichText: true), findsOneWidget);
    expect(find.text('Continues on next page'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('native-original-next')));
    await tester.tap(find.byKey(const ValueKey('native-original-next')));
    await tester.pump();
    expect(nextCalls, 1);
    pending.complete(const NativeContentPage(pageIndex: 1, totalBlocks: 1,
      version: 'code', blocks: [NativeContentBlock(entryId: 'code', blockIndex: 0,
        role: 'assistant', type: 'text', text: 'final second = 42;\n```',
        continuedBefore: true, renderPrefix: '```dart\n')]));
    await tester.pumpAndSettle();
    expect(find.textContaining('final second', findRichText: true), findsOneWidget);
    expect(find.textContaining('final first', findRichText: true), findsNothing);
    expect(find.text('Continued from previous page'), findsOneWidget);
    expect(find.text('Page 2'), findsOneWidget);
  });
  testWidgets('complete content renders Markdown and independent execution disclosures', (tester) async {
    await tester.pumpWidget(host((_) async => const NativeContentPage(
      pageIndex: 0, totalBlocks: 4, version: 'v', blocks: [
        NativeContentBlock(entryId: 'a', blockIndex: 0, role: 'assistant',
          type: 'text', text: '# Readable answer\n\n**Important** [reference](https://example.com)\n\n```dart\nfinal answer = 42;\n```'),
        NativeContentBlock(entryId: 'a', blockIndex: 1, role: 'assistant',
          type: 'reasoning', text: 'Private execution explanation'),
        NativeContentBlock(entryId: 'a', blockIndex: 2, role: 'assistant',
          type: 'tool_call', name: 'shell', text: 'command: echo hello'),
        NativeContentBlock(entryId: 't', blockIndex: 0, role: 'tool',
          type: 'tool_result', text: 'hello\n  formatted output'),
      ],
    )));
    await tester.tap(find.byKey(const ValueKey('native-read-original')));
    await tester.pumpAndSettle();
    expect(find.byType(Dialog), findsOneWidget);
    expect(find.text('Readable answer', findRichText: true), findsOneWidget);
    expect(find.textContaining('Important reference', findRichText: true), findsOneWidget);
    expect(find.textContaining('final answer = 42;', findRichText: true), findsOneWidget);
    expect(find.text('Private execution explanation', findRichText: true), findsNothing);
    expect(find.text('command: echo hello'), findsNothing);
    await tester.tap(find.byKey(const ValueKey('native-content-execution-a-2')));
    await tester.pumpAndSettle();
    expect(find.text('command: echo hello'), findsOneWidget);
    expect(find.text('hello\n  formatted output'), findsNothing);
    await tester.tap(find.byKey(const ValueKey('native-content-execution-t-0')));
    await tester.pumpAndSettle();
    expect(find.text('hello\n  formatted output'), findsOneWidget);
    expect(find.text('Private execution explanation', findRichText: true), findsNothing);
  });

  for (final invalid in ['version', 'index', 'continuation']) {
    testWidgets('reader rejects changed $invalid without replacing current content', (tester) async {
      await tester.pumpWidget(host((cursor) async => cursor == 'page0'
        ? page('First immutable page', next: 'page1', after: true)
        : page('Invalid second page', index: invalid == 'index' ? 2 : 1,
            version: invalid == 'version' ? 'changed' : 'v',
            before: invalid != 'continuation')));
      await tester.tap(find.byKey(const ValueKey('native-read-original')));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('native-original-next')));
      await tester.pumpAndSettle();
      expect(find.textContaining('Content could not load'), findsOneWidget);
      expect(find.text('First immutable page', findRichText: true), findsOneWidget);
      expect(find.text('Invalid second page', findRichText: true), findsNothing);
    });
  }
  testWidgets('queued original remains readable without native turn ownership', (tester) async {
    final item = CcbConversationItem.fromJson({
      'id': 'queue-key', 'agent': 'demo', 'kind': 'user_message',
      'body': 'Queue preview', 'source': 'mobile/collab', 'state': 'queued',
      'read_cursor': 'queue-page', 'read_reason': 'Content exceeds inline budget',
    });
    final restored = CcbConversationItem.fromJson(item.copyWith(body: item.body).toJson());
    expect(restored.nativeTurn, isNull);
    expect(restored.readCursor, 'queue-page');
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: ConversationBody(
      item: restored,
      contentLoader: (_) async => page('Complete queued request'),
    ))));
    await tester.tap(find.byKey(const ValueKey('conversation-read-original')));
    await tester.pumpAndSettle();
    expect(find.text('Complete queued request', findRichText: true), findsOneWidget);
  });
  testWidgets('oversized user input retains an explicit original reader', (
    tester,
  ) async {
    final json = overflowReply().toJson()
      ..['kind'] = 'user_message'
      ..['body'] = 'Latest request tail'
      ..remove('native_turn')
      ..['read_cursor'] = 'page0'
      ..['read_reason'] = 'Recent content only; earlier content omitted';
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ConversationBody(
            item: CcbConversationItem.fromJson(json),
            contentLoader:
                (_) async => page('Full user request'),
          ),
        ),
      ),
    );
    expect(find.text('Latest request tail'), findsOneWidget);
    expect(find.text('No assistant answer'), findsNothing);
    await tester.tap(find.text('Complete content'));
    await tester.pumpAndSettle();
    expect(find.text('Full user request', findRichText: true), findsOneWidget);
  });
  test(
    'overflow fields survive item copies and conversation serialization',
    () {
      final item = overflowReply().copyWith(body: 'Copied preview');
      final restored = CcbConversationItem.fromJson(item.toJson());
      expect(restored.nativeTurn!.readCursor, 'page0');
      expect(restored.nativeTurn!.inlineWindow, isTrue);
      expect(restored.nativeTurn!.answerText, 'Readable latest answer');
      expect(
        restored.nativeTurn!.reason,
        'Content exceeds inline budget; read original',
      );
      expect(restored.nativeTurn!.status, NativeSessionStatus.running);
      final conversation = CcbAgentConversation.fromJson({
        'conversation': {
          'project_id': 'p',
          'agent': 'demo',
          'namespace_epoch': 1,
          'items': [],
          'native_content': page('中文🙂\n').toJson(),
        },
      });
      final copy = CcbAgentConversation.fromJson(conversation.toJson());
      expect(copy.nativeContent!.blocks.single.text, '中文🙂\n');
      expect(copy.nativeContent!.version, 'v');
      expect(copy.nativeContent!.nextCursor, isNull);
    },
  );

  testWidgets(
    'original reader is lazy, exact Unicode paged, bounded and disposable',
    (tester) async {
      final texts = ['开始🙂\n', '工具结果𐐀\n', '结束é'];
      final calls = <String>[];
      Future<NativeContentPage> load(String cursor) async {
        calls.add(cursor);
        final index = int.parse(cursor.substring(4));
        return page(texts[index], index: index, version: 'immutable',
          next: index == 2 ? null : 'page${index + 1}',
          before: index > 0, after: index < 2);
      }

      await tester.pumpWidget(host(load));
      expect(calls, isEmpty);
      expect(find.text('Running'), findsOneWidget);
      expect(find.text('Completed'), findsNothing);
      expect(find.text('Readable latest answer', findRichText: true), findsOneWidget);
      expect(find.text('Bounded preview'), findsNothing);
      expect(find.text(const NativeTurnLabels().latestWindow), findsOneWidget);
      await tester.tap(find.byKey(const ValueKey('native-read-original')));
      await tester.pumpAndSettle();
      final read = <String>[];
      for (var index = 0; index < texts.length; index++) {
        final text = tester.widget<CcbMarkdown>(
          find.descendant(of: find.byKey(const ValueKey('native-original-reader')),
            matching: find.byType(CcbMarkdown)),
        ).data;
        expect(find.text(texts[index].trim(), findRichText: true), findsOneWidget);
        read.add(text);
        expect(text, texts[index]);
        for (var other = 0; other < texts.length; other++) {
          if (other != index) expect(find.text(texts[other].trim(), findRichText: true), findsNothing);
        }
        if (index != texts.length - 1) {
          await tester.tap(find.byKey(const ValueKey('native-original-next')));
          await tester.pumpAndSettle();
        }
      }
      expect(read.join(), texts.join());
      expect(calls, ['page0', 'page1', 'page2']);
      expect(
        tester
            .widget<TextButton>(
              find.byKey(const ValueKey('native-original-next')),
            )
            .onPressed,
        isNull,
      );
      await tester.tap(find.byKey(const ValueKey('native-original-previous')));
      await tester.pumpAndSettle();
      expect(find.text(texts[1].trim(), findRichText: true), findsOneWidget);
      expect(calls.last, 'page1');
      await tester.longPress(find.text(texts[1].trim(), findRichText: true));
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('native-original-close')));
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('native-original-reader')), findsNothing);
      for (final text in texts) {
        expect(find.text(text), findsNothing);
      }
      await tester.tap(find.byKey(const ValueKey('native-read-original')));
      await tester.pumpAndSettle();
      expect(calls.last, 'page0');
      expect(find.text(texts.first.trim(), findRichText: true), findsOneWidget);
      expect(
        tester
            .widget<TextButton>(
              find.byKey(const ValueKey('native-original-previous')),
            )
            .onPressed,
        isNull,
      );
    },
  );
  testWidgets('stale original failure stays visible without automatic reload', (
    tester,
  ) async {
    var calls = 0;
    await tester.pumpWidget(
      host((_) async {
        calls++;
        throw StateError('409 stale native content cursor');
      }),
    );
    await tester.tap(find.byKey(const ValueKey('native-read-original')));
    await tester.pumpAndSettle();
    expect(calls, 1);
    expect(find.textContaining('Content could not load'), findsOneWidget);
    await tester.pump(const Duration(seconds: 5));
    expect(calls, 1);
    expect(find.byKey(const ValueKey('native-original-close')), findsOneWidget);
  });

  testWidgets('closing in-flight original discards late content', (
    tester,
  ) async {
    final pending = Completer<NativeContentPage>();
    await tester.pumpWidget(host((_) => pending.future));
    await tester.tap(find.byKey(const ValueKey('native-read-original')));
    await tester.pump();
    await tester.tap(find.byKey(const ValueKey('native-original-close')));
    await tester.pumpAndSettle();
    pending.complete(
      page('late'),
    );
    await tester.pumpAndSettle();
    expect(find.text('late'), findsNothing);
    expect(tester.takeException(), isNull);
  });

  for (final error in [
    'RelayGatewayException(payload_too_large)',
    'TimeoutException: request timed out',
  ]) {
    testWidgets('$error is a request failure, not native disconnected', (
      tester,
    ) async {
      var retried = false;
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: OmpConversationErrorBanner(
              error: error,
              onRetry: () => retried = true,
            ),
          ),
        ),
      );
      expect(
        find.byKey(const ValueKey('omp-conversation-load-failed')),
        findsOneWidget,
      );
      expect(
        find.byKey(const ValueKey('omp-collab-disconnected')),
        findsNothing,
      );
      expect(
        find.textContaining('OMP conversation could not load'),
        findsOneWidget,
      );
      expect(find.text('Reconnect'), findsNothing);
      await tester.tap(find.text('Details'));
      await tester.pumpAndSettle();
      expect(
        find.textContaining('does not mean the native socket disconnected'),
        findsOneWidget,
      );
      expect(find.textContaining(error), findsNothing);
      await tester.tap(find.text('Close'));
      await tester.pumpAndSettle();
      await tester.tap(find.text('Retry'));
      expect(retried, isTrue);
    });
  }

  testWidgets('explicit unavailable host keeps reconnect presentation', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: OmpConversationErrorBanner(
            error: '503 OMP Collab snapshot not ready',
            onRetry: () {},
          ),
        ),
      ),
    );
    expect(
      find.byKey(const ValueKey('omp-collab-disconnected')),
      findsOneWidget,
    );
    expect(find.text('Reconnect'), findsOneWidget);
  });
}
