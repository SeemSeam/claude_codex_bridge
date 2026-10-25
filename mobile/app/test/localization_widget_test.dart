import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:ccb_mobile/ccb_mobile.dart';
import 'package:ccb_mobile/features/agent_chat/agent_message_composer.dart';
import 'package:ccb_mobile/features/project_home/project_home_onboarding.dart';
import 'package:ccb_mobile/features/project_home/project_home_scaffold_host.dart';

void main() {
  testWidgets('onboarding follows Chinese locale', (tester) async {
    final connectionCodeController = TextEditingController();
    addTearDown(connectionCodeController.dispose);

    await tester.pumpWidget(
      _localizedApp(
        locale: const Locale('zh'),
        child: ProjectHomeOnboardingScaffold(
          connectionCodeController: connectionCodeController,
          claiming: false,
          loadingProfiles: false,
          onScan: () {},
          onClaim: () {},
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.text('连接 CCB Mobile'), findsOneWidget);
    expect(find.text('在电脑上运行一条命令'), findsOneWidget);
    expect(find.text('扫描二维码'), findsOneWidget);
    expect(find.text('扫描电脑二维码'), findsOneWidget);
    expect(find.text('输入连接码'), findsOneWidget);
    expect(find.text('激活 CCB 官方 Relay'), findsNothing);

    final pairingPanel = find.byKey(const ValueKey('gateway-pairing-panel'));
    await tester.ensureVisible(pairingPanel);
    await tester.pumpAndSettle();
    await tester.tap(pairingPanel);
    await tester.pumpAndSettle();

    expect(find.text('连接码'), findsOneWidget);
    expect(find.text('使用连接码连接'), findsOneWidget);
    expect(find.text('网关地址'), findsNothing);
    expect(find.text('配对码'), findsNothing);
    expect(find.text('设备名称'), findsNothing);
    expect(find.text('路由'), findsNothing);
  });

  testWidgets('server project list follows Chinese locale', (tester) async {
    await tester.pumpWidget(
      _localizedApp(
        locale: const Locale('zh'),
        child: ProjectHomeServerProjectListHost(
          projects: const [],
          onRefreshProjects: () {},
          onOpenTerminal: () {},
          onOpenSettings: () {},
          onOpenProject: (_) {},
        ),
      ),
    );

    expect(find.text('未找到 CCB 项目'), findsOneWidget);
  });

  testWidgets('composer follows Chinese locale', (tester) async {
    final controller = TextEditingController();
    addTearDown(controller.dispose);

    await tester.pumpWidget(
      _localizedApp(
        locale: const Locale('zh'),
        child: Scaffold(
          body: AgentMessageComposer(
            agentName: 'lead',
            controller: controller,
            isSending: false,
            collapsible: false,
            collapsed: false,
            onCollapse: () {},
            onExpand: () {},
            draftAttachments: const [],
            onPickImage: () {},
            onPickFile: () {},
            onRemoveAttachment: (_) {},
            onSend: () {},
            onQueue: () {},
            onSendEscape: () {},
          ),
        ),
      ),
    );

    expect(find.text('给 lead 发消息'), findsOneWidget);

    await tester.tap(find.byKey(const ValueKey('agent-attachment-button')));
    await tester.pumpAndSettle();

    expect(find.text('图片'), findsOneWidget);
    expect(find.text('文件'), findsOneWidget);
    expect(find.text('取消'), findsOneWidget);
  });
}

Widget _localizedApp({required Locale locale, required Widget child}) {
  return MaterialApp(
    locale: locale,
    supportedLocales: CcbMobileLocalizations.supportedLocales,
    localizationsDelegates: GlobalMaterialLocalizations.delegates,
    home: child,
  );
}
