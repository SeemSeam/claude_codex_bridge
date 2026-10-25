import 'dart:async';

import 'package:ccb_mobile/features/agent_chat/ui_request_card.dart';
import 'package:ccb_mobile/features/agent_chat/agent_chat_controller.dart';
import 'package:ccb_mobile/features/agent_chat/selected_agent_workspace_model.dart';
import 'package:ccb_mobile/models/ccb_agent_conversation.dart';
import 'package:ccb_mobile/models/ccb_project_view.dart';
import 'package:ccb_mobile/l10n/ccb_mobile_localizations.dart';
import 'package:ccb_mobile/models/omp_runtime.dart';
import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

const select = OmpPendingUiRequest(reqId: 41, kind: 'select', title: 'Choose a mode',
  helpText: 'Choose carefully', initialIndex: 0, options: [
    OmpPendingUiOption(label: 'Safe', description: 'Keep existing files'),
    OmpPendingUiOption(label: 'Fast'),
  ]);

Widget app(Widget child, {String language = 'en'}) => MaterialApp(
  locale: Locale(language), supportedLocales: CcbMobileLocalizations.supportedLocales,
  localizationsDelegates: GlobalMaterialLocalizations.delegates,
  home: Scaffold(body: child),
);

void main() {
  test('live conversations update prompt identity and end clears it without prose', () {
    final controller = AgentChatController();
    final view = CcbProjectView.fromProjectViewPayload({'view': {
      'project': {'id': 'p', 'display_name': 'P'}, 'namespace': {'epoch': 1},
      'agents': [{'name': 'omp', 'provider': 'omp', 'provider_control': {'session_id': 's', 'runtime_source': 'collab'}}],
    }});
    SelectedAgentWorkspaceModel model() => selectedAgentWorkspaceModel(view: view,
      agent: view.agents.single, chatController: controller, isAwaitingAgentResponse: false);
    void apply(OmpRuntime runtime, {String session = 's'}) => controller.applyRemoteConversation(
      agentName: 'omp', shouldScroll: false, conversation: CcbAgentConversation(
        projectId: 'p', agentName: 'omp', namespaceEpoch: 1, items: const [],
        ompSessionId: session, ompGeneration: 'g', ompRuntime: runtime));
    apply(OmpRuntime(connected: true, ready: true, observedAt: DateTime.utc(2026), pendingUiRequest: select));
    expect(model().pendingUiRequest?.reqId, 41);
    expect(model().uiRequestAvailable, isTrue);
    expect(model().timelineItems, isEmpty, reason: 'A native prompt is not an ordinary message');
    apply(OmpRuntime(connected: false, ready: false, observedAt: DateTime.utc(2026, 1, 2), pendingUiRequest: select));
    expect(model().pendingUiRequest?.reqId, 41);
    expect(model().uiRequestAvailable, isFalse);
    apply(OmpRuntime(connected: true, ready: true, observedAt: DateTime.utc(2026, 1, 3)));
    expect(model().pendingUiRequest, isNull);
    apply(OmpRuntime(connected: true, ready: true, observedAt: DateTime.utc(2026, 1, 4), pendingUiRequest: select), session: 'stale');
    expect(model().pendingUiRequest, isNull);
    expect(model().uiRequestAvailable, isFalse);
  });

  testWidgets('select is interactive with title help descriptions and initial index', (tester) async {
    final values = <String>[];
    await tester.pumpWidget(app(UiRequestCard(request: select, onSubmit: (v) async { values.add(v); })));
    expect(find.text('Choose a mode'), findsOneWidget);
    expect(find.text('Choose carefully'), findsOneWidget);
    expect(find.text('Keep existing files'), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('omp-ui-option-1')));
    await tester.ensureVisible(find.byKey(const ValueKey('omp-ui-submit')));
    await tester.tap(find.byKey(const ValueKey('omp-ui-submit')));
    await tester.pump();
    expect(values, ['Fast']);
    expect(find.byKey(const ValueKey('omp-ui-sent')), findsOneWidget);
  });

  testWidgets('editor prefill is separate input and sends edited value', (tester) async {
    String? sent;
    await tester.pumpWidget(app(UiRequestCard(request: const OmpPendingUiRequest(
      reqId: 42, kind: 'editor', title: 'Edit response', prefill: 'Starting text'),
      onSubmit: (v) async { sent = v; })));
    expect(tester.widget<TextField>(find.byKey(const ValueKey('omp-ui-editor'))).controller!.text, 'Starting text');
    await tester.enterText(find.byKey(const ValueKey('omp-ui-editor')), 'Custom response');
    await tester.ensureVisible(find.byKey(const ValueKey('omp-ui-submit')));
    await tester.tap(find.byKey(const ValueKey('omp-ui-submit')));
    await tester.pump();
    expect(sent, 'Custom response');
  });

  testWidgets('select permits Other custom input without using chat composer', (tester) async {
    String? sent;
    await tester.pumpWidget(app(UiRequestCard(request: select, onSubmit: (v) async { sent = v; })));
    await tester.ensureVisible(find.byKey(const ValueKey('omp-ui-other')));
    await tester.tap(find.byKey(const ValueKey('omp-ui-other')));
    await tester.pump();
    await tester.enterText(find.byKey(const ValueKey('omp-ui-other-field')), 'My mode');
    await tester.pump();
    await tester.ensureVisible(find.byKey(const ValueKey('omp-ui-submit')));
    await tester.tap(find.byKey(const ValueKey('omp-ui-submit')));
    await tester.pump();
    expect(sent, 'My mode');
    expect(find.byKey(const ValueKey('agent-message-composer')), findsNothing);
  });

  testWidgets('sending disables repeat submit and unknown failures never auto replay', (tester) async {
    final response = Completer<void>();
    var count = 0;
    await tester.pumpWidget(app(UiRequestCard(request: select, onSubmit: (_) { count++; return response.future; })));
    await tester.ensureVisible(find.byKey(const ValueKey('omp-ui-submit')));
    await tester.tap(find.byKey(const ValueKey('omp-ui-submit')));
    await tester.pump();
    expect(tester.widget<FilledButton>(find.byKey(const ValueKey('omp-ui-submit'))).onPressed, isNull);
    response.completeError(StateError('stale'));
    await tester.pump();
    expect(find.byKey(const ValueKey('omp-ui-error')), findsOneWidget);
    expect(tester.widget<FilledButton>(find.byKey(const ValueKey('omp-ui-submit'))).onPressed, isNull);
    await tester.pump(const Duration(seconds: 5));
    expect(count, 1);
  });

  testWidgets('stale or disconnected request cannot submit', (tester) async {
    var count = 0;
    await tester.pumpWidget(app(UiRequestCard(request: select, stale: true, onSubmit: (_) async { count++; })));
    expect(find.byKey(const ValueKey('omp-ui-stale')), findsOneWidget);
    expect(tester.widget<FilledButton>(find.byKey(const ValueKey('omp-ui-submit'))).onPressed, isNull);
    expect(tester.widget<ListTile>(find.byKey(const ValueKey('omp-ui-option-0'))).onTap, isNull);
    expect(count, 0);
  });

  testWidgets('prompt controls and stale notice follow Chinese locale', (tester) async {
    await tester.pumpWidget(app(UiRequestCard(request: select, stale: true, onSubmit: (_) async {}), language: 'zh'));
    expect(find.text('其他 / 自定义'), findsOneWidget);
    expect(find.text('提交'), findsOneWidget);
    expect(find.text('请求已过期或连接已断开。'), findsOneWidget);
  });

  test('legacy absent invalid and ended pending requests are null', () {
    expect(OmpRuntime.fromJson({}).pendingUiRequest, isNull);
    expect(OmpRuntime.fromJson({'pending_ui_request': null}).pendingUiRequest, isNull);
    expect(OmpRuntime.fromJson({'pending_ui_request': {'req_id': '1', 'kind': 'select'}}).pendingUiRequest, isNull);
    expect(OmpRuntime.fromJson({'pending_ui_request': {'req_id': 1, 'kind': 'unknown'}}).pendingUiRequest, isNull);
  });

  test('pending request is bounded ephemeral typed and part of live signature', () {
    final long = List.filled(600, 'x').join();
    final parsed = OmpRuntime.fromJson({'connected': true, 'ready': true, 'pending_ui_request': {
      'req_id': 9, 'kind': 'select', 'title': long, 'help_text': long, 'prefill': long,
      'options': List.filled(20, {'label': long, 'description': long}),
    }});
    final request = parsed.pendingUiRequest!;
    expect(request.reqId, 9);
    expect(request.options.length, 16);
    expect(request.options.first.label.length, 256);
    expect(request.options.first.description!.length, 512);
    expect(request.title.length, 512);
    expect(request.helpText!.length, 512);
    expect(request.prefill!.length, 512);
    expect(parsed.disconnected().connected, isFalse);
    expect(parsed.disconnected().pendingUiRequest, same(request));
    expect(parsed.stableSnapshot()['pending_ui_request'], isNotNull);
    expect(OmpRuntime.fromJson(parsed.toJson()).pendingUiRequest, isNull,
      reason: 'A restored snapshot must not replay an interactive prompt');
  });
}
