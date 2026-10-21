import 'dart:convert';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:ccb_mobile/features/agent_chat/conversation_bubble.dart';
import 'package:ccb_mobile/features/agent_chat/selected_agent_workspace.dart';
import 'package:ccb_mobile/features/provider_control/provider_control_sheet.dart';
import 'package:ccb_mobile/models/ccb_agent_conversation.dart';
import 'package:ccb_mobile/models/ccb_conversation_item.dart';
import 'package:ccb_mobile/repository/gateway_mobile_ccb_repository.dart';
import 'package:ccb_mobile/transport/http_gateway_transport.dart';
import 'package:ccb_mobile/transport/route_provider.dart';

const _url = String.fromEnvironment('OMP_FIXTURE_URL');
const _token = String.fromEnvironment('OMP_FIXTURE_TOKEN');

// Scenario controls generate explicit fixture content, not model-quality proof.
// All user-visible data travels through the production HTTP repository/gateway.
Future<Map<String, dynamic>> _fixture(
  String action, [
  Map<String, Object?> body = const {},
]) async {
  final client = HttpClient();
  try {
    final request =
        action == 'stats'
            ? await client.getUrl(Uri.parse('$_url/__fixture/stats'))
            : await client.postUrl(Uri.parse('$_url/__fixture/$action'));
    request.headers.set(HttpHeaders.authorizationHeader, 'Bearer $_token');
    if (action != 'stats') {
      final encoded = utf8.encode(jsonEncode(body));
      request.contentLength = encoded.length;
      request.add(encoded);
    }
    final response = await request.close();
    final text = await utf8.decoder.bind(response).join();
    expect(response.statusCode, 200, reason: text);
    return jsonDecode(text) as Map<String, dynamic>;
  } finally {
    client.close(force: true);
  }
}

Future<void> _until(
  WidgetTester tester,
  bool Function() predicate, {
  String reason = 'expected UI state',
}) async {
  final deadline = DateTime.now().add(const Duration(seconds: 30));
  while (!predicate() && DateTime.now().isBefore(deadline)) {
    await tester.pump(const Duration(milliseconds: 100));
    await tester.runAsync(
      () => Future<void>.delayed(const Duration(milliseconds: 100)),
    );
  }
  expect(predicate(), isTrue, reason: reason);
}

List<ConversationBubble> _replies(WidgetTester tester) =>
    tester
        .widgetList<ConversationBubble>(find.byType(ConversationBubble))
        .where(
          (bubble) => bubble.item.kind == CcbConversationItemKind.agentReply,
        )
        .toList();

Future<void> _tap(WidgetTester tester, Finder finder) async {
  await tester.ensureVisible(finder);
  await tester.tap(finder);
  await tester.pump(const Duration(milliseconds: 300));
}

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();
  testWidgets('real OMP Collab mobile isolated deep smoke', (tester) async {
    tester.testTextInput.register();
    addTearDown(tester.testTextInput.unregister);
    expect(_url, isNotEmpty);
    expect(_token, isNotEmpty);
    final repository = GatewayMobileCcbRepository(
      transport: HttpGatewayTransport(
        profile: GatewayHostProfile(
          hostId: 'omp-fixture',
          deviceId: 'fixture-device',
          routeProvider: RouteProvider(
            kind: RouteProviderKind.lan,
            gatewayUrl: Uri.parse(_url),
          ),
          scopes: const {'view', 'message_submit', 'provider_settings'},
        ),
        deviceToken: _token,
      ),
    );
    final view = await tester.runAsync(
      () => repository.getProjectView('omp-fixture'),
    );
    expect(view, isNotNull);
    final agent = view!.agentByName('mobile')!;
    expect(agent.providerControl?.runtimeSource, 'collab');
    final controller = SelectedAgentWorkspaceController();
    late BuildContext appContext;
    await tester.pumpWidget(
      MaterialApp(
        locale: const Locale('en'),
        home: Builder(
          builder: (context) {
            appContext = context;
            return Scaffold(
              appBar: AppBar(title: const Text('OMP isolated smoke')),
              body: SelectedAgentWorkspace(
                repository: repository,
                terminalTransport: null,
                // OMP must select Collab despite the generic pane-input preference.
                usePaneInputForMessages: true,
                view: view,
                agent: agent,
                enableComposerCollapse: false,
                onRefreshView: () async => view,
                controller: controller,
              ),
            );
          },
        ),
      ),
    );
    await _until(
      tester,
      () => _replies(
        tester,
      ).any((b) => b.item.body == 'Fixture initial native snapshot'),
    );
    await tester.runAsync(() => _fixture('screenshot', {'name': 'initial'}));
    final before = (await tester.runAsync(() => _fixture('stats')))!;

    Future<void> send(String body, int count) async {
      final input = find.byKey(const ValueKey('agent-message-composer'));
      await tester.ensureVisible(input);
      await tester.enterText(input, body);
      expect(
        tester.widget<TextField>(input).controller!.text,
        body,
        reason: 'Integration input immediately reaches composer',
      );
      FocusManager.instance.primaryFocus?.unfocus();
      await SystemChannels.textInput.invokeMethod<void>('TextInput.hide');
      await tester.pump(const Duration(milliseconds: 400));
      expect(
        tester.widget<TextField>(input).controller!.text,
        body,
        reason: 'Android input must preserve draft before Send',
      );
      await _until(
        tester,
        () =>
            tester
                .widget<IconButton>(
                  find.byKey(const ValueKey('agent-message-send-button')),
                )
                .onPressed !=
            null,
      );
      await _tap(
        tester,
        find.byKey(const ValueKey('agent-message-send-button')),
      );
      await tester.runAsync(() => _fixture('screenshot', {'name': 'live'}));
      final remainingDraft = tester.widget<TextField>(input).controller!.text;
      expect(
        remainingDraft,
        isEmpty,
        reason: 'Send must consume the visible native draft',
      );
      await _until(
        tester,
        () => tester
            .widgetList<ConversationBubble>(find.byType(ConversationBubble))
            .any((b) => b.item.body.contains(body)),
        reason: 'visible submitted input $count',
      );
      FocusManager.instance.primaryFocus?.unfocus();
      await tester.pump(const Duration(milliseconds: 300));
      Map<String, dynamic> stats = {};
      final deadline = DateTime.now().add(const Duration(seconds: 10));
      do {
        stats = (await tester.runAsync(() => _fixture('stats')))!;
        if (stats['prompts'] == count) break;
        await tester.pump(const Duration(milliseconds: 100));
        await tester.runAsync(
          () => Future<void>.delayed(const Duration(milliseconds: 100)),
        );
      } while (DateTime.now().isBefore(deadline));
      expect(stats['prompts'], count);
      expect(stats['terminal_calls'], 0);
    }

    await send('Fixture mobile first question', 1);
    await tester.runAsync(() => _fixture('live'));
    await _until(
      tester,
      () => _replies(tester).any((b) => b.item.body == 'Fixture live turn 1'),
    );
    final liveId =
        _replies(
          tester,
        ).singleWhere((b) => b.item.body == 'Fixture live turn 1').item.id;
    await tester.runAsync(() => _fixture('screenshot', {'name': 'live'}));
    await tester.runAsync(() => _fixture('finish'));
    await _until(
      tester,
      () =>
          _replies(tester).any((b) => b.item.body == 'Fixture durable turn 1'),
    );
    final durable =
        _replies(
          tester,
        ).where((b) => b.item.body == 'Fixture durable turn 1').toList();
    expect(durable, hasLength(1));
    expect(
      durable.single.item.id,
      liveId,
      reason: 'live text becomes the same durable reply bubble',
    );
    expect(
      _replies(tester).where((b) => b.item.body == 'Fixture live turn 1'),
      isEmpty,
    );
    await tester.runAsync(() => _fixture('screenshot', {'name': 'durable'}));

    // Tool output is not built until both process and tool disclosures open.
    expect(find.textContaining('Fixture tool result 1'), findsNothing);
    final process = find.text('Execution process');
    await _tap(tester, process.last);
    final resultLabel = find.textContaining('Tool result');
    await _until(tester, () => resultLabel.evaluate().isNotEmpty);
    await _tap(tester, resultLabel.last);
    await _until(
      tester,
      () => find.textContaining('Fixture tool result 1').evaluate().isNotEmpty,
    );
    await tester.runAsync(() => _fixture('screenshot', {'name': 'tools'}));
    await _tap(tester, process.last);
    expect(find.textContaining('Fixture tool result 1'), findsNothing);

    await send('Fixture mobile second question', 2);
    await tester.runAsync(() => _fixture('live'));
    await _until(
      tester,
      () => _replies(tester).any((b) => b.item.body == 'Fixture live turn 2'),
    );
    expect(
      _replies(tester).where((b) => b.item.body == 'Fixture durable turn 1'),
      hasLength(1),
    );
    await tester.runAsync(() => _fixture('finish'));
    await _until(
      tester,
      () =>
          _replies(tester).any((b) => b.item.body == 'Fixture durable turn 2'),
    );
    expect(
      _replies(tester).where((b) => b.item.body == 'Fixture durable turn 2'),
      hasLength(1),
    );
    expect(
      _replies(
        tester,
      ).singleWhere((b) => b.item.body == 'Fixture durable turn 2').item.id,
      isNot(liveId),
    );

    final oldControl =
        (await tester.runAsync(
          () => repository.getAgentProviderControl(
            projectId: 'omp-fixture',
            agentName: 'mobile',
          ),
        ))!;
    final sheet = showProviderControlSheet(
      appContext,
      repository: repository,
      projectId: 'omp-fixture',
      agent: agent,
    );
    await _until(
      tester,
      () =>
          find
              .byKey(const ValueKey('provider-model-option-fixture/two'))
              .evaluate()
              .isNotEmpty,
    );
    await _tap(
      tester,
      find.byKey(const ValueKey('provider-model-option-fixture/two')),
    );
    await _until(
      tester,
      () => find.byType(ProviderControlSheet).evaluate().isEmpty,
      reason: 'native model selection closes after host readback',
    );
    await sheet;
    final afterModel = (await tester.runAsync(() => _fixture('stats')))!;
    expect(afterModel['model'], {'provider': 'fixture', 'id': 'two'});
    expect(afterModel['changes'], 1);
    expect(afterModel['pid'], before['pid']);
    expect(afterModel['session_id'], before['session_id']);
    expect(afterModel['generation'], before['generation']);
    final updatedControl =
        (await tester.runAsync(
          () => repository.getAgentProviderControl(
            projectId: 'omp-fixture',
            agentName: 'mobile',
          ),
        ))!;
    expect(updatedControl.control.activeModel, 'fixture/two');
    expect(updatedControl.control.restartPending, isFalse);
    expect(
      find.byKey(const ValueKey('provider-control-pending')),
      findsNothing,
    );
    final selectedSheet = showProviderControlSheet(
      appContext,
      repository: repository,
      projectId: 'omp-fixture',
      agent: agent,
    );
    await _until(
      tester,
      () =>
          find
              .byKey(const ValueKey('provider-model-option-fixture/two'))
              .evaluate()
              .isNotEmpty,
    );
    await tester.runAsync(() => _fixture('screenshot', {'name': 'model'}));
    Navigator.of(appContext).pop();
    await tester.pump(const Duration(milliseconds: 300));
    await selectedSheet;

    // Stale model revision must never mutate native runtime.
    await tester.runAsync(() async {
      try {
        await repository.updateAgentProviderSettings(
          projectId: 'omp-fixture',
          agentName: 'mobile',
          model: 'fixture/one',
          thinking: 'off',
          expectedRevision: oldControl.configRevision!,
          expectedNamespaceEpoch: 4,
          expectedProvider: 'omp',
          expectedRuntimeRevision: oldControl.control.runtimeRevision,
          idempotencyKey: 'fixture-stale-model',
        );
        fail('Stale model runtime was accepted');
      } on GatewayHttpException catch (error) {
        expect(error.statusCode, 409);
      }
    });

    await send('Fixture error question', 3);
    await tester.runAsync(() => _fixture('error'));
    await _until(tester, () => find.text('Failed').evaluate().isNotEmpty);
    final oldConversation =
        (await tester.runAsync(
          () => repository.getAgentConversation(
            projectId: 'omp-fixture',
            agent: 'mobile',
            namespaceEpoch: 4,
          ),
        ))!;
    await tester.runAsync(() => _fixture('disconnect'));
    controller.refreshLatest();
    await _until(
      tester,
      () =>
          find
              .byKey(const ValueKey('omp-collab-disconnected'))
              .evaluate()
              .isNotEmpty,
    );
    expect(
      tester
          .widget<IconButton>(
            find.byKey(const ValueKey('agent-message-send-button')),
          )
          .onPressed,
      isNull,
    );
    await tester.runAsync(() => _fixture('reconnect'));
    await _tap(tester, find.text('Reconnect'));
    await _until(
      tester,
      () =>
          find
              .byKey(const ValueKey('omp-collab-disconnected'))
              .evaluate()
              .isEmpty,
    );
    await tester.runAsync(() async {
      try {
        await repository.submitAgentMessage(
          CcbAgentMessageSubmitRequest(
            projectId: 'omp-fixture',
            agentName: 'mobile',
            namespaceEpoch: 4,
            idempotencyKey: 'fixture-stale-send',
            body: 'Must not arrive',
            expectedOmpSessionId: oldConversation.ompSessionId,
            expectedOmpGeneration: oldConversation.ompGeneration,
          ),
        );
        fail('Stale Collab generation was accepted');
      } on GatewayHttpException catch (error) {
        expect(error.statusCode, 409);
      }
    });
    await tester.runAsync(() => _fixture('screenshot', {'name': 'reconnect'}));

    // An actual lifecycle notification exercises the product observer and timer.
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.hidden);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.paused);
    await tester.runAsync(
      () => Future<void>.delayed(const Duration(seconds: 2)),
    );
    final pausedBefore = (await tester.runAsync(() => _fixture('stats')))!;
    await tester.runAsync(
      () => Future<void>.delayed(const Duration(seconds: 4)),
    );
    final pausedAfter = (await tester.runAsync(() => _fixture('stats')))!;
    expect(
      pausedAfter['conversation_reads'],
      pausedBefore['conversation_reads'],
      reason: 'no foreground polling while backgrounded',
    );
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.hidden);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await _until(
      tester,
      () => tester.binding.lifecycleState == AppLifecycleState.resumed,
    );
    await tester.pump(const Duration(seconds: 2));
    final finalStats = (await tester.runAsync(() => _fixture('stats')))!;
    expect(finalStats['terminal_calls'], 0);
    expect(finalStats['prompts'], 3);
    expect(finalStats['changes'], 1);
    await tester.runAsync(() => _fixture('screenshot', {'name': 'final'}));
    await tester.pumpWidget(const SizedBox.shrink());
    controller.dispose();
  });
}
