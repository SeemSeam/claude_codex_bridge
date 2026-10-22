import 'dart:convert';

import 'package:ccb_mobile/native_session/native_session.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

NativeSessionBlock text(String value) =>
    NativeSessionBlock(type: NativeSessionBlockType.text, text: value);

NativeSessionEntry entry(
  String id,
  List<NativeSessionBlock> blocks, {
  NativeSessionRole role = NativeSessionRole.assistant,
  String? stopReason,
}) => NativeSessionEntry(
  id: id,
  role: role,
  blocks: blocks,
  stopReason: stopReason,
);

NativeSessionTurn turn(
  List<NativeSessionEntry> entries, {
  NativeSessionStatus status = NativeSessionStatus.completed,
}) => NativeSessionTurn(
  sessionId: 'session',
  turnId: 'user-entry',
  status: status,
  entries: entries,
);

Widget host(
  NativeSessionTurn value, {
  Widget Function(BuildContext, String)? textBuilder,
  NativeTurnLabels labels = const NativeTurnLabels(),
}) => MaterialApp(
  home: Scaffold(
    body: SingleChildScrollView(
      child: NativeTurnBody(
        turn: value,
        labels: labels,
        textBuilder: textBuilder ?? (_, value) => Text(value),
      ),
    ),
  ),
);

void main() {
  testWidgets('all assistant prose stays visible across later assistant updates', (tester) async {
    final first = entry('a', [
      text('First explanation'),
      const NativeSessionBlock(type: NativeSessionBlockType.reasoning, text: 'detail'),
    ]);
    await tester.pumpWidget(host(turn([first])));
    await tester.tap(find.text('Execution process'));
    await tester.pump();
    await tester.tap(find.text('Reasoning'));
    await tester.pump();
    await tester.pumpWidget(host(turn([first, entry('b', [text('Later explanation')])])));
    expect(find.text('First explanation\n\nLater explanation'), findsOneWidget);
    // Stable native keys retain both disclosure levels through a refresh.
    expect(find.text('detail'), findsOneWidget);
  });

  testWidgets('inline to original keeps previous text reachable and labeled stale', (tester) async {
    final retained = NativeSessionTurn(
      sessionId: 'session', turnId: 'user-entry', status: NativeSessionStatus.completed,
      entries: [entry('a', [text('Entire earlier explanation')])],
      readCursor: 'current-original', retainedInline: true,
    );
    await tester.pumpWidget(host(retained));
    expect(find.text('Read original'), findsOneWidget);
    expect(find.text('Entire earlier explanation'), findsNothing);
    await tester.tap(find.text('Assistant message — previously loaded snapshot'));
    await tester.pump();
    expect(find.text('Entire earlier explanation'), findsOneWidget);
    expect(find.text('Preview only — content exceeds inline budget'), findsOneWidget);
  });

  test('entry identity, not identical text, controls deduplication', () {
    final value = turn([
      entry('a', [text('old snapshot')]),
      entry('b', [text('same text')]),
      entry('a', [text('same text')]),
      entry('c', [text('same text')]),
    ]);
    expect(value.entries.map((item) => item.id), ['a', 'b', 'c']);
    expect(value.entries.first.blocks.single.text, 'same text');
    expect(value.answerEntry?.id, 'c');
    expect(value.answerText, 'same text\n\nsame text\n\nsame text');
    expect(value.processBlocks, isEmpty);
  });

  test('process order and tool association survive final answer selection', () {
    final value = turn([
      entry('user', [text('request')], role: NativeSessionRole.user),
      entry('thinking', [
        const NativeSessionBlock(
          type: NativeSessionBlockType.reasoning,
          text: 'visible reasoning',
        ),
        text('I will inspect'),
        const NativeSessionBlock(
          type: NativeSessionBlockType.toolCall,
          callId: 'call-1',
          name: 'read',
          input: {'path': 'file'},
        ),
      ], stopReason: 'tool_use'),
      entry('result', [
        const NativeSessionBlock(
          type: NativeSessionBlockType.toolResult,
          callId: 'call-1',
          text: 'output, not the answer',
        ),
      ], role: NativeSessionRole.tool),
      entry('answer', [
        text('Final answer'),
        text('More detail'),
      ], stopReason: 'end_turn'),
    ]);
    expect(value.answerText, 'I will inspect\n\nFinal answer\n\nMore detail');
    expect(value.processBlocks.map((item) => item.block.type), [
      NativeSessionBlockType.reasoning,
      NativeSessionBlockType.toolCall,
      NativeSessionBlockType.toolResult,
    ]);
    expect(value.processBlocks.skip(1).map((item) => item.block.callId), [
      'call-1',
      'call-1',
    ]);
    expect(value.hasPendingTools, isFalse);
    expect(value.effectiveStatus, NativeSessionStatus.completed);
  });

  test(
    'v1 JSON round trip retains public content without provider metadata',
    () {
      final json = <String, Object?>{
        'schema_version': 1,
        'session_id': 's',
        'turn_id': 'u',
        'status': 'failed',
        'entries': [
          {
            'id': 'a',
            'role': 'assistant',
            'stop_reason': 'tool_use',
            'blocks': [
              {'type': 'reasoning', 'text': 'visible', 'signature': 'secret'},
              {
                'type': 'tool_call',
                'call_id': 'c',
                'name': 'read',
                'input': {
                  'path': 'file',
                  'lines': [1, 2],
                },
              },
            ],
            'provider_metadata': {'private': true},
          },
          {
            'id': 't',
            'role': 'tool',
            'blocks': [
              {
                'type': 'tool_result',
                'call_id': 'c',
                'text': 'oops',
                'is_error': true,
              },
            ],
          },
        ],
      };
      final value = NativeSessionTurn.fromJson(json);
      final encoded = value.toJson();
      expect(NativeSessionTurn.fromJson(encoded).toJson(), encoded);
      expect(jsonEncode(encoded), isNot(contains('secret')));
      expect(jsonEncode(encoded), isNot(contains('provider_metadata')));
      expect(value.status, NativeSessionStatus.failed);
      expect(value.entries.last.blocks.single.isError, isTrue);
      expect(value.entries.first.blocks.last.input, {
        'path': 'file',
        'lines': [1, 2],
      });
    },
  );

  test(
    'only preceding matching tool IDs resolve calls; never infer success',
    () {
      const call = NativeSessionBlock(
        type: NativeSessionBlockType.toolCall,
        callId: 'c',
      );
      const result = NativeSessionBlock(
        type: NativeSessionBlockType.toolResult,
        callId: 'c',
        text: 'ok',
      );
      final unfinished = turn([
        entry('a', [call], stopReason: 'tool_use'),
      ]);
      expect(unfinished.hasPendingTools, isTrue);
      expect(unfinished.effectiveStatus, NativeSessionStatus.unknown);
      final wrongId = turn([
        entry('a', [call]),
        entry('b', [
          const NativeSessionBlock(
            type: NativeSessionBlockType.toolResult,
            callId: 'other',
          ),
        ], role: NativeSessionRole.tool),
      ]);
      expect(wrongId.hasPendingTools, isTrue);
      final reversed = turn([
        entry('b', [result], role: NativeSessionRole.tool),
        entry('a', [call]),
      ]);
      expect(reversed.hasPendingTools, isTrue);
      final handoff = turn([
        entry('a', [call], stopReason: 'tool_use'),
        entry('b', [result], role: NativeSessionRole.tool),
      ]);
      expect(handoff.hasPendingTools, isFalse);
      expect(handoff.effectiveStatus, NativeSessionStatus.unknown);
      final running = turn([
        entry('a', [text('persisted message')], stopReason: 'end_turn'),
      ], status: NativeSessionStatus.running);
      expect(running.effectiveStatus, NativeSessionStatus.running);
    },
  );

  test('unknown status is explicit and unsupported schema is rejected', () {
    final json = turn([]).toJson();
    json['status'] = 'future-status';
    expect(
      NativeSessionTurn.fromJson(json).status,
      NativeSessionStatus.unknown,
    );
    json['schema_version'] = 2;
    expect(() => NativeSessionTurn.fromJson(json), throwsFormatException);
  });

  testWidgets(
    'host prose builder is used and reasoning is independently lazy',
    (tester) async {
      final built = <String>[];
      await tester.pumpWidget(
        host(
          turn([
            entry('a', [
              const NativeSessionBlock(
                type: NativeSessionBlockType.reasoning,
                text: 'private visible thought',
              ),
              text('Final prose'),
            ]),
          ]),
          textBuilder: (_, value) {
            built.add(value);
            return Text(value);
          },
        ),
      );
      expect(built, ['Final prose']);
      expect(find.text('private visible thought'), findsNothing);
      expect(find.text('Reasoning'), findsNothing);
      await tester.tap(find.text('Execution process'));
      await tester.pump();
      expect(find.text('Reasoning'), findsOneWidget);
      expect(find.text('private visible thought'), findsNothing);
      await tester.tap(find.text('Reasoning'));
      await tester.pump();
      expect(find.text('private visible thought'), findsOneWidget);
      expect(find.byType(SelectableText), findsOneWidget);
      expect(find.text('Final prose'), findsOneWidget);
    },
  );

  testWidgets(
    'tool input and full long output expand separately with call ID',
    (tester) async {
      final output =
          '${List.filled(400, 'a long output line').join('\n')}\nEND';
      await tester.pumpWidget(
        host(
          turn([
            entry('a', [
              const NativeSessionBlock(
                type: NativeSessionBlockType.toolCall,
                callId: 'call-42',
                name: 'read',
                input: {'path': 'file'},
              ),
            ]),
            entry('b', [
              NativeSessionBlock(
                type: NativeSessionBlockType.toolResult,
                callId: 'call-42',
                text: output,
              ),
            ], role: NativeSessionRole.tool),
            entry('c', [text('Done')]),
          ]),
        ),
      );
      expect(find.byType(SelectableText), findsNothing);
      await tester.tap(find.text('Execution process'));
      await tester.pump();
      expect(find.text('Tool call · read · call-42'), findsOneWidget);
      expect(find.text('Tool result · call-42'), findsOneWidget);
      expect(find.byType(SelectableText), findsNothing);
      await tester.tap(find.text('Tool call · read · call-42'));
      await tester.pump();
      expect(
        find.text(const JsonEncoder.withIndent('  ').convert({'path': 'file'})),
        findsOneWidget,
      );
      expect(find.text(output), findsNothing);
      await tester.tap(find.text('Tool result · call-42'));
      await tester.pump();
      final content = tester.widgetList<SelectableText>(
        find.byType(SelectableText),
      );
      expect(content.any((widget) => widget.data == output), isTrue);
      expect(find.byType(Scrollbar), findsNWidgets(2));
      await tester.tap(find.text('Execution process'));
      await tester.pump();
      expect(find.byType(SelectableText), findsNothing);
    },
  );

  testWidgets('tool-only unfinished and failed or aborted turns show status', (
    tester,
  ) async {
    await tester.pumpWidget(
      host(
        turn([
          entry('a', [
            const NativeSessionBlock(
              type: NativeSessionBlockType.toolCall,
              callId: 'pending',
            ),
          ]),
        ]),
      ),
    );
    expect(find.text('Status unknown'), findsOneWidget);
    expect(find.text('Tool execution unfinished'), findsOneWidget);
    expect(find.text('No assistant answer'), findsOneWidget);
    expect(find.text('Completed'), findsNothing);
    for (final status in [
      NativeSessionStatus.failed,
      NativeSessionStatus.aborted,
    ]) {
      await tester.pumpWidget(host(turn([], status: status)));
      expect(
        find.text(status == NativeSessionStatus.failed ? 'Failed' : 'Aborted'),
        findsOneWidget,
      );
      expect(find.text('No assistant answer'), findsOneWidget);
    }
  });

  testWidgets('hosts can localize neutral status and process strings', (
    tester,
  ) async {
    await tester.pumpWidget(
      host(
        turn([], status: NativeSessionStatus.failed),
        labels: const NativeTurnLabels(failed: '失败', noAnswer: '无回答'),
      ),
    );
    expect(find.text('失败'), findsOneWidget);
    expect(find.text('无回答'), findsOneWidget);
  });
}
