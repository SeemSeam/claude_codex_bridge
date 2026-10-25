import 'package:ccb_mobile/features/agent_chat/omp_runtime_panel.dart';
import 'package:ccb_mobile/models/omp_runtime.dart';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

final observed = DateTime.utc(1995);
OmpRuntime observation({bool connected = true, bool streaming = true,
  int? elapsed = 8300, int revision = 0, int turn = 0, bool available = true,
  bool todosAvailable = true, String step = 'Inspect native session', int done = 1}) => OmpRuntime(
    connected: connected, ready: true, isStreaming: !streaming,
    isAborting: true, queuedMessageCount: 99, observedAt: DateTime.utc(2099),
    statusBar: OmpStatusBar(available: available, source: 'native_events', sessionId: 's',
      observedAt: observed.add(Duration(seconds: revision)),
      startedAt: observed.add(Duration(minutes: turn)), elapsedMs: elapsed,
      isStreaming: streaming, step: step,
      todos: OmpTodos(available: todosAvailable, done: done, total: 3,
        inProgress: 1, items: const [
          OmpTodoItem(content: 'Finished setup', status: 'completed', phase: 'Build'),
          OmpTodoItem(content: 'Implement native snapshot', status: 'in_progress', phase: 'Build'),
          OmpTodoItem(content: 'Review result', status: 'pending', phase: 'Review'),
        ])));

void main() {
  Future<void> show(WidgetTester tester, OmpRuntime runtime) => tester.pumpWidget(MaterialApp(
    home: Scaffold(body: Column(children: [OmpRuntimePanel(runtime: runtime),
      const Expanded(child: Text('Chat area'))]))));
  String status(WidgetTester tester) => tester.widget<Text>(find.byKey(
    const ValueKey('omp-runtime-status'))).data!;
  Future<void> tick(WidgetTester tester) async {
    await tester.runAsync(() => Future<void>.delayed(const Duration(milliseconds: 1100)));
    await tester.pump(const Duration(seconds: 1));
  }

  testWidgets('native primary rows show elapsed step and exact todo count with current task', (tester) async {
    await show(tester, observation());
    expect(status(tester), 'OMP · Working · 0:08');
    expect(find.text('Step: Inspect native session'), findsOneWidget);
    expect(find.text('Todo 1/3 · Implement native snapshot'), findsOneWidget);
    expect(find.text('Finished setup'), findsNothing);
    await tester.tap(find.byKey(const ValueKey('omp-runtime-details')));
    await tester.pumpAndSettle();
    expect(find.textContaining('Finished setup'), findsOneWidget);
    expect(find.textContaining('Review result'), findsOneWidget);
    await tester.tap(find.text('Close'));
    await tester.pumpAndSettle();
    await show(tester, observation(revision: 1, done: 2, step: 'Reviewing result'));
    expect(find.text('Step: Reviewing result'), findsOneWidget);
    expect(find.text('Todo 2/3 · Implement native snapshot'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('elapsed ignores phone skew duplicates preserve baseline and new turn resets', (tester) async {
    await show(tester, observation(elapsed: 10000));
    expect(status(tester), 'OMP · Working · 0:10');
    await show(tester, observation(elapsed: 1000));
    expect(status(tester), 'OMP · Working · 0:10',
      reason: 'A duplicate native observation must not rewind the existing baseline');
    await show(tester, observation(elapsed: 1000, turn: 1, revision: 1));
    expect(status(tester), 'OMP · Working · 0:01');
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('native idle overrides stale generic busy and freezes elapsed', (tester) async {
    await show(tester, observation(streaming: false, elapsed: 6000));
    expect(status(tester), 'OMP · Idle · 0:06');
    await tick(tester);
    expect(status(tester), 'OMP · Idle · 0:06');
    await show(tester, observation(connected: false, elapsed: 6000));
    await tick(tester);
    expect(status(tester), 'OMP · Disconnected · 0:06');
    expect(find.byKey(const ValueKey('omp-runtime-unavailable')), findsOneWidget);
    expect(find.text('Todo unavailable'), findsOneWidget);
    expect(find.textContaining('Todo 1/3'), findsNothing);
    await show(tester, observation(streaming: false, elapsed: null, revision: 1));
    expect(status(tester), 'OMP · Idle');
    expect(find.byKey(const ValueKey('omp-runtime-elapsed-unavailable')), findsNothing);
    expect(find.text('Step: —'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('unavailable source and unknown native clock never invent elapsed or zero todos', (tester) async {
    await show(tester, observation(available: false));
    expect(find.text('Native status unavailable'), findsOneWidget);
    expect(find.text('Todo unavailable'), findsOneWidget);
    expect(find.textContaining('0/0'), findsNothing);
    expect(status(tester), isNot(contains('0:')));
    await show(tester, observation(elapsed: null, todosAvailable: false));
    expect(status(tester), 'OMP · Working');
    expect(find.text('Todo unavailable'), findsOneWidget);
    await tester.pumpWidget(const SizedBox());
  });

  testWidgets('canonical empty snapshot clears current todo and reports real zero count', (tester) async {
    await show(tester, observation());
    await show(tester, OmpRuntime(connected: true, ready: true, observedAt: observed,
      statusBar: OmpStatusBar(available: true, source: 'native_events', sessionId: 's',
        observedAt: observed.add(const Duration(seconds: 1)),
        todos: const OmpTodos(available: true))));
    expect(find.text('Todo 0/0'), findsOneWidget);
    expect(find.textContaining('Implement native snapshot'), findsNothing);
  });
}
