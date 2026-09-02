import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:ccb_mobile/features/project_home/project_home_gateway_profiles.dart';
import 'package:ccb_mobile/features/project_home/project_home_host_rename_dialog.dart';
import 'package:ccb_mobile/features/project_home/project_home_multi_host_list.dart';
import 'package:ccb_mobile/features/project_home/project_home_multi_host_projects.dart';
import 'package:ccb_mobile/l10n/ccb_mobile_localizations.dart';
import 'package:ccb_mobile/models/ccb_project.dart';
import 'package:ccb_mobile/pairing/gateway_pairing.dart';
import 'package:ccb_mobile/transport/route_provider.dart';

void main() {
  testWidgets('a renamed computer heads its group with the chosen name', (
    tester,
  ) async {
    _usePhoneSurface(tester);
    final renamed = _pairedHost(hostId: 'rhost_alpha', deviceId: 'phone');
    final untouched = _pairedHost(hostId: 'rhost_beta', deviceId: 'phone');

    await tester.pumpWidget(
      _localizedApp(
        child: ProjectHomeMultiHostProjectListHost(
          result: _resultFor([renamed, untouched]),
          customHostNames: {projectHomeCustomHostNameKey(renamed): '公司台式机'},
          onRefreshProjects: () {},
          onOpenTerminal: () {},
          onOpenSettings: () {},
          onOpenProject: (_) {},
          onRenameHost: (_) {},
        ),
      ),
    );

    expect(_headerName(tester, 'rhost_alpha/phone'), '公司台式机');
    // A computer the user never renamed keeps the name its pairing carried.
    expect(_headerName(tester, 'rhost_beta/phone'), 'rhost_beta');
    // The rename action shares the header row, so the name must still fit.
    expect(tester.takeException(), isNull);
  });

  testWidgets('the group header asks to rename its own computer', (
    tester,
  ) async {
    _usePhoneSurface(tester);
    final alpha = _pairedHost(hostId: 'rhost_alpha', deviceId: 'phone');
    final beta = _pairedHost(hostId: 'rhost_beta', deviceId: 'phone');
    final renameRequests = <String>[];

    await tester.pumpWidget(
      _localizedApp(
        child: ProjectHomeMultiHostProjectListHost(
          result: _resultFor([alpha, beta]),
          onRefreshProjects: () {},
          onOpenTerminal: () {},
          onOpenSettings: () {},
          onOpenProject: (_) {},
          onRenameHost: (profile) {
            renameRequests.add(projectHomeGatewayProfileKey(profile));
          },
        ),
      ),
    );
    await tester.tap(
      find.byKey(const ValueKey('multi-host-group-rename-rhost_beta/phone')),
    );

    expect(renameRequests, ['rhost_beta/phone']);
  });

  testWidgets('the rename dialog offers the pairing name as its hint', (
    tester,
  ) async {
    ProjectHomeHostRenameResult? result;
    var closed = false;

    await tester.pumpWidget(
      _localizedApp(
        child: _RenameDialogHost(
          automaticName: 'rhost_alpha',
          currentName: null,
          onClosed: (value) {
            result = value;
            closed = true;
          },
        ),
      ),
    );
    await tester.tap(find.byKey(const ValueKey('open-rename-dialog')));
    await tester.pumpAndSettle();

    expect(find.text('rhost_alpha'), findsOneWidget);
    // Nothing to reset while the computer still shows its pairing name.
    expect(find.byKey(const ValueKey('host-rename-reset')), findsNothing);

    await tester.enterText(
      find.byKey(const ValueKey('host-rename-field')),
      '  公司台式机 ',
    );
    await tester.tap(find.byKey(const ValueKey('host-rename-save')));
    await tester.pumpAndSettle();

    expect(closed, isTrue);
    expect(result?.name, '公司台式机');
  });

  testWidgets('clearing the name restores the pairing derived name', (
    tester,
  ) async {
    ProjectHomeHostRenameResult? result;
    var closed = false;

    await tester.pumpWidget(
      _localizedApp(
        child: _RenameDialogHost(
          automaticName: 'rhost_alpha',
          currentName: '公司台式机',
          onClosed: (value) {
            result = value;
            closed = true;
          },
        ),
      ),
    );
    await tester.tap(find.byKey(const ValueKey('open-rename-dialog')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('host-rename-reset')));
    await tester.pumpAndSettle();

    expect(closed, isTrue);
    // A reset is a decision, so it is reported with a null name rather than as
    // a dismissal that would leave the stored name in place.
    expect(result, isNotNull);
    expect(result?.name, isNull);
  });

  testWidgets('dismissing the rename dialog reports no choice at all', (
    tester,
  ) async {
    ProjectHomeHostRenameResult? result;
    var closed = false;

    await tester.pumpWidget(
      _localizedApp(
        child: _RenameDialogHost(
          automaticName: 'rhost_alpha',
          currentName: '公司台式机',
          onClosed: (value) {
            result = value;
            closed = true;
          },
        ),
      ),
    );
    await tester.tap(find.byKey(const ValueKey('open-rename-dialog')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('host-rename-cancel')));
    await tester.pumpAndSettle();

    expect(closed, isTrue);
    expect(result, isNull);
  });
}

/// Opens the rename dialog from a real route, so the dialog is exercised through
/// the modal path the grouped project list uses.
class _RenameDialogHost extends StatelessWidget {
  const _RenameDialogHost({
    required this.automaticName,
    required this.currentName,
    required this.onClosed,
  });

  final String automaticName;
  final String? currentName;
  final ValueChanged<ProjectHomeHostRenameResult?> onClosed;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      body: Center(
        child: TextButton(
          key: const ValueKey('open-rename-dialog'),
          onPressed: () async {
            onClosed(
              await showProjectHomeHostRenameDialog(
                context,
                automaticName: automaticName,
                currentName: currentName,
              ),
            );
          },
          child: const Text('rename'),
        ),
      ),
    );
  }
}

String? _headerName(WidgetTester tester, String groupKey) {
  return tester
      .widget<Text>(find.byKey(ValueKey('multi-host-group-name-$groupKey')))
      .data;
}

void _usePhoneSurface(WidgetTester tester) {
  tester.view.physicalSize = const Size(390, 844);
  tester.view.devicePixelRatio = 1;
  addTearDown(tester.view.resetPhysicalSize);
  addTearDown(tester.view.resetDevicePixelRatio);
}

Widget _localizedApp({required Widget child}) {
  return MaterialApp(
    locale: const Locale('zh'),
    supportedLocales: CcbMobileLocalizations.supportedLocales,
    localizationsDelegates: GlobalMaterialLocalizations.delegates,
    home: child,
  );
}

ProjectHomeMultiHostProjectsResult _resultFor(List<GatewayPairedHost> hosts) {
  return ProjectHomeMultiHostProjectsResult.fromCatalogs([
    for (final host in hosts)
      ProjectHomeHostCatalog(
        profile: host,
        projects: [
          CcbProject(
            id: '${host.profile.hostId}-project',
            displayName: '${host.profile.hostId} 项目',
            root: '/srv/${host.profile.hostId}',
          ),
        ],
      ),
  ]);
}

GatewayPairedHost _pairedHost({
  required String hostId,
  required String deviceId,
}) {
  return GatewayPairedHost(
    profile: GatewayHostProfile(
      hostId: hostId,
      deviceId: deviceId,
      routeProvider: RouteProvider(
        kind: RouteProviderKind.relay,
        gatewayUrl: Uri.parse('https://relay.example.test'),
      ),
      scopes: const {'view'},
    ),
    deviceToken: '$hostId-token',
    projectId: hostId,
    createdAt: DateTime.utc(2026, 6, 22),
  );
}
