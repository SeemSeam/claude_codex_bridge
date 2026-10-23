import 'dart:async';
import 'dart:convert';

import 'package:ccb_mobile/features/agent_chat/conversation_bubble.dart';
import 'package:ccb_mobile/features/agent_chat/conversation_item_presentation.dart';
import 'package:ccb_mobile/models/ccb_agent_conversation.dart';
import 'package:ccb_mobile/models/ccb_conversation_item.dart';
import 'package:ccb_mobile/native_session/native_session.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

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
      contentLoader: (_) async => const NativeContentPage(
        text: 'Complete queued request', offset: 0, totalBytes: 23, version: 'v',
      ),
    ))));
    await tester.tap(find.byKey(const ValueKey('conversation-read-original')));
    await tester.pumpAndSettle();
    expect(find.text('Complete queued request'), findsOneWidget);
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
                (_) async => const NativeContentPage(
                  text: 'Full user request',
                  offset: 0,
                  totalBytes: 17,
                  version: 'v',
                ),
          ),
        ),
      ),
    );
    expect(find.text('Latest request tail'), findsOneWidget);
    expect(find.text('No assistant answer'), findsNothing);
    await tester.tap(find.text('Read original'));
    await tester.pumpAndSettle();
    expect(find.text('Full user request'), findsOneWidget);
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
          'native_content': {
            'text': '中文🙂\n',
            'next_cursor': null,
            'offset': 0,
            'total_bytes': 11,
            'version': 'v',
          },
        },
      });
      final copy = CcbAgentConversation.fromJson(conversation.toJson());
      expect(copy.nativeContent!.text, '中文🙂\n');
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
        return NativeContentPage(
          text: texts[index],
          offset: utf8.encode(texts.take(index).join()).length,
          totalBytes: utf8.encode(texts.join()).length,
          version: 'immutable',
          nextCursor: index == 2 ? null : 'page${index + 1}',
        );
      }

      await tester.pumpWidget(host(load));
      expect(calls, isEmpty);
      expect(find.text('Running'), findsOneWidget);
      expect(find.text('Completed'), findsNothing);
      expect(find.text('Readable latest answer'), findsOneWidget);
      expect(find.text('Bounded preview'), findsNothing);
      expect(find.text(const NativeTurnLabels().latestWindow), findsOneWidget);
      await tester.tap(find.byKey(const ValueKey('native-read-original')));
      await tester.pumpAndSettle();
      final read = <String>[];
      for (var index = 0; index < texts.length; index++) {
        final text =
            tester
                .widget<SelectableText>(
                  find.byKey(const ValueKey('native-original-text')),
                )
                .data!;
        read.add(text);
        expect(text, texts[index]);
        for (var other = 0; other < texts.length; other++) {
          if (other != index) expect(find.text(texts[other]), findsNothing);
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
      expect(find.text(texts[1]), findsOneWidget);
      expect(calls.last, 'page1');
      await tester.longPress(
        find.byKey(const ValueKey('native-original-text')),
      );
      await tester.pumpAndSettle();
      await tester.tap(find.byKey(const ValueKey('native-original-close')));
      await tester.pumpAndSettle();
      expect(find.byKey(const ValueKey('native-original-text')), findsNothing);
      for (final text in texts) {
        expect(find.text(text), findsNothing);
      }
      await tester.tap(find.byKey(const ValueKey('native-read-original')));
      await tester.pumpAndSettle();
      expect(calls.last, 'page0');
      expect(find.text(texts.first), findsOneWidget);
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
    expect(find.textContaining('Original could not load'), findsOneWidget);
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
      const NativeContentPage(
        text: 'late',
        offset: 0,
        totalBytes: 4,
        version: 'v',
      ),
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
