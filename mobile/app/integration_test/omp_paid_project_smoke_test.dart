import 'dart:io';
import 'dart:convert';
import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:integration_test/integration_test.dart';
import 'package:ccb_mobile/features/agent_chat/selected_agent_workspace.dart';
import 'package:ccb_mobile/features/agent_chat/conversation_bubble.dart';
import 'package:ccb_mobile/repository/gateway_mobile_ccb_repository.dart';
import 'package:ccb_mobile/transport/http_gateway_transport.dart';
import 'package:ccb_mobile/transport/route_provider.dart';

void main() {
  IntegrationTestWidgetsFlutterBinding.ensureInitialized();
  testWidgets(
    'current project paid OMP real conversation',
    (tester) async {
      tester.testTextInput.register();
      addTearDown(tester.testTextInput.unregister);
      const marker = String.fromEnvironment(
        'PAID_MARKER',
        defaultValue: 'CCB_PAID_MOBILE_OK_20261020',
      );
      final repository = GatewayMobileCcbRepository(
        transport: HttpGatewayTransport(
          profile: GatewayHostProfile(
            hostId: 'paid-local',
            deviceId: 'paid-test',
            routeProvider: RouteProvider(
              kind: RouteProviderKind.lan,
              gatewayUrl: Uri.parse(const String.fromEnvironment('PAID_URL')),
            ),
            scopes: const {'view', 'message_submit', 'provider_settings'},
          ),
          deviceToken: const String.fromEnvironment('PAID_TOKEN'),
        ),
      );
      final view =
          (await tester.runAsync(
            () => repository.getProjectView('current-paid'),
          ))!;
      final agent =
          view.agentByName(const String.fromEnvironment('PAID_AGENT'))!;
      expect(agent.providerControl?.runtimeSource, 'collab');
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            appBar: AppBar(title: const Text('CCB current project · paid OMP')),
            body: SelectedAgentWorkspace(
              repository: repository,
              terminalTransport: null,
              usePaneInputForMessages: true,
              view: view,
              agent: agent,
              enableComposerCollapse: false,
              onRefreshView: () async => view,
            ),
          ),
        ),
      );
      Future<void> until(bool Function() predicate, String reason) async {
        final deadline = DateTime.now().add(const Duration(minutes: 4));
        while (!predicate() && DateTime.now().isBefore(deadline)) {
          await tester.pump(const Duration(milliseconds: 200));
          await tester.runAsync(
            () => Future<void>.delayed(const Duration(milliseconds: 200)),
          );
        }
        expect(predicate(), isTrue, reason: reason);
      }

      final send = find.byKey(const ValueKey('agent-message-send-button'));
      await until(
        () => tester.widget<IconButton>(send).onPressed != null,
        'Collab ready',
      );
      final prompt =
          '这是用户授权的真实付费模型手机气泡验证。仅执行只读操作，不得修改、创建或删除任何文件，不运行构建和测试，不委派任务。请重新用 read 工具读取当前项目 VERSION 与 mobile/README.md 的前 12 行，不复用上轮结果。最后用中文简短回复，包含唯一标记 $marker、VERSION 的实际值以及 README 的标题。必须真实调用 read 工具。';
      if (!const bool.fromEnvironment('PAID_REUSE_RESULT')) {
        await tester.enterText(
          find.byKey(const ValueKey('agent-message-composer')),
          prompt,
        );
        await tester.tap(send);
      }
      final seen = <String>{};
      await until(() {
        final replies = tester
            .widgetList<ConversationBubble>(find.byType(ConversationBubble))
            .where(
              (bubble) =>
                  bubble.item.nativeTurn != null &&
                  bubble.item.kind.name == 'agentReply',
            );
        for (final bubble in replies) {
          if (bubble.item.completedAt == null) seen.add(bubble.item.id);
        }
        return replies.any(
          (bubble) =>
              bubble.item.body.contains(marker) &&
              bubble.item.completedAt != null,
        );
      }, 'Real paid model must read files and complete');
      final reply = tester
          .widgetList<ConversationBubble>(find.byType(ConversationBubble))
          .singleWhere(
            (bubble) =>
                bubble.item.kind.name == 'agentReply' &&
                bubble.item.body.contains(marker),
          );
      expect(
        reply.item.nativeTurn!.processBlocks.any(
          (block) => block.block.type.name == 'toolCall',
        ),
        isTrue,
      );
      expect(
        reply.item.nativeTurn!.processBlocks.any(
          (block) => block.block.type.name == 'toolResult',
        ),
        isTrue,
      );
      expect(reply.item.body, contains('8.7.12'));
      expect(reply.item.body, contains('CCB Mobile'));
      // ignore: avoid_print
      print(
        'PAID_REAL_RESULT ${jsonEncode({'id': reply.item.id, 'session': reply.item.sessionId, 'status': reply.item.nativeTurn!.effectiveStatus.name, 'body': reply.item.body, 'observedRunningIds': seen.toList(), 'processBlocks': reply.item.nativeTurn!.processBlocks.length})}',
      );
      await tester.ensureVisible(find.text('Execution process'));
      await tester.tap(find.text('Execution process'));
      await tester.pump(const Duration(milliseconds: 300));
      expect(find.textContaining('Tool call'), findsWidgets);
      expect(find.textContaining('Tool result'), findsWidgets);
      await tester.runAsync(() async {
        final client = HttpClient();
        final request = await client.getUrl(
          Uri.parse(
            '${const String.fromEnvironment('PAID_URL')}/v1/projects/current-paid/agents/${agent.name}/conversation?namespace_epoch=1',
          ),
        );
        request.headers.set(
          HttpHeaders.authorizationHeader,
          'Bearer ${const String.fromEnvironment('PAID_TOKEN')}',
        );
        final response = await request.close();
        expect(response.statusCode, 200);
        await response.drain<void>();
        client.close();
      });
      await Future<void>.delayed(const Duration(seconds: 8));
      await tester.pumpWidget(const SizedBox.shrink());
    },
    timeout: const Timeout(Duration(minutes: 6)),
  );
}
