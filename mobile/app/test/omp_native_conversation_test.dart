import 'dart:convert';

import 'package:ccb_mobile/features/agent_chat/agent_chat_state_helpers.dart';
import 'package:ccb_mobile/features/agent_chat/conversation_bubble.dart';
import 'package:ccb_mobile/models/ccb_agent_conversation.dart';
import 'package:ccb_mobile/models/ccb_conversation_item.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

CcbConversationItem reply({bool finished = false}) =>
    CcbConversationItem.fromJson({
      'id': 'omp-session-user-reply',
      'agent': 'demo',
      'kind': 'agent_reply',
      'body': finished ? 'Final answer' : '',
      'format': 'markdown',
      'source': 'provider_native/omp',
      'native_turn': {
        'schema_version': 1,
        'session_id': 'session',
        'turn_id': 'user',
        'status': finished ? 'completed' : 'running',
        'entries': [
          {
            'id': 'call-entry',
            'role': 'assistant',
            'stop_reason': 'tool_use',
            'blocks': [
              {'type': 'reasoning', 'text': 'Visible reasoning'},
              {
                'type': 'tool_call',
                'name': 'read',
                'call_id': 'call-1',
                'input': {'path': 'file.txt'},
              },
            ],
          },
          if (finished) ...[
            {
              'id': 'result-entry',
              'role': 'tool',
              'blocks': [
                {
                  'type': 'tool_result',
                  'call_id': 'call-1',
                  'name': 'read',
                  'text': 'Native file output',
                },
              ],
            },
            {
              'id': 'answer-entry',
              'role': 'assistant',
              'stop_reason': 'stop',
              'blocks': [
                {'type': 'text', 'text': 'Final answer'},
              ],
            },
          ],
        ],
      },
    });

void main() {
  testWidgets('legacy OMP native history renders body without native turn', (tester) async {
    final item = CcbConversationItem.fromJson({
      'id': 'legacy-omp-reply',
      'agent': 'demo',
      'kind': 'agent_reply',
      'body': 'Native history from Mac',
      'source': 'provider_native/omp',
    });
    expect(item.nativeTurn, isNull);
    expect(isProviderNativeConversationItem(item), isTrue);
    await tester.pumpWidget(MaterialApp(
      home: Scaffold(body: ConversationBubble(
        item: item,
        expanded: false,
        onToggleExpanded: (_) {},
      )),
    ));
    expect(find.text('Native history from Mac'), findsOneWidget);
  });

  test('process-only changes invalidate cached conversation presentation', () {
    CcbAgentConversation conversation(CcbConversationItem item) =>
        CcbAgentConversation(
          projectId: 'project',
          agentName: 'demo',
          namespaceEpoch: 1,
          items: [item],
        );
    final first = reply();
    final json = first.toJson();
    final updated = jsonDecode(jsonEncode(json)) as Map<String, dynamic>;
    ((updated['native_turn']['entries'][0]['blocks']) as List).add({
      'type': 'reasoning',
      'text': 'More visible progress',
    });
    final next = CcbConversationItem.fromJson(updated);
    expect(
      conversationSignature(conversation(next)),
      isNot(conversationSignature(conversation(first))),
    );
    final restored = CcbConversationItem.fromJson(
      jsonDecode(jsonEncode(next.toJson())),
    );
    expect(
      restored.nativeTurn!.processBlocks.last.block.text,
      'More visible progress',
    );
  });

  testWidgets('OMP bubble keeps one reply while tools become a final answer', (
    tester,
  ) async {
    Future<void> show(CcbConversationItem item) => tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SingleChildScrollView(
            child: ConversationBubble(
              item: item,
              expanded: false,
              onToggleExpanded: (_) {},
            ),
          ),
        ),
      ),
    );
    await show(reply());
    expect(find.text('Running'), findsOneWidget);
    expect(find.text('Tool execution unfinished'), findsOneWidget);
    await tester.tap(find.text('Execution process'));
    await tester.pumpAndSettle();
    await tester.tap(find.text('Tool call · read · call-1'));
    await tester.pumpAndSettle();
    expect(find.textContaining('file.txt'), findsOneWidget);
    await show(reply(finished: true));
    await tester.pumpAndSettle();
    expect(find.byType(ConversationBubble), findsOneWidget);
    expect(find.text('Completed'), findsOneWidget);
    expect(find.text('Tool execution unfinished'), findsNothing);
    expect(find.text('Tool result · read · call-1'), findsOneWidget);
    expect(tester.takeException(), isNull);
  });
}
