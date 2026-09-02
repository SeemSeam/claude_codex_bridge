import 'package:ccb_mobile/features/project_home/project_home_gateway_profiles.dart';
import 'package:ccb_mobile/features/project_home/project_home_profile_bootstrapper.dart';
import 'package:ccb_mobile/features/project_home/runtime_mode_panel.dart'
    as runtime_mode_panel;
import 'package:ccb_mobile/pairing/gateway_pairing.dart';
import 'package:ccb_mobile/transport/route_provider.dart';
import 'package:test/test.dart';

import 'support/project_home_test_fakes.dart';

void main() {
  test('runtime mode panel keeps gateway profile helper API path', () {
    final profile = _pairedHost(
      hostId: 'project',
      deviceId: 'phone',
      routeKind: RouteProviderKind.relay,
    );

    expect(
      runtime_mode_panel.projectHomeGatewayProfileKey(profile),
      'project/phone',
    );
    expect(
      runtime_mode_panel.projectHomeGatewayProfileLabel(profile),
      'project / phone / relay',
    );
  });

  test('a chosen computer name wins over the pairing derived name', () {
    final profile = _pairedHost(hostId: 'rhost_alpha', deviceId: 'phone');
    final customNames = {
      projectHomeCustomHostNameKey(profile): '  公司台式机  ',
    };

    expect(projectHomeCustomHostName(customNames, profile), '公司台式机');
    expect(
      projectHomeGatewayProfileHostName(
        profile,
        customName: projectHomeCustomHostName(customNames, profile),
      ),
      '公司台式机',
    );
  });

  test('a computer without a chosen name keeps its pairing name', () {
    final named = _pairedHost(hostId: 'rhost_alpha', deviceId: 'phone');
    final other = _pairedHost(hostId: 'rhost_beta', deviceId: 'phone');
    final customNames = {projectHomeCustomHostNameKey(named): 'Alpha'};

    expect(projectHomeCustomHostName(customNames, other), isNull);
    expect(
      projectHomeGatewayProfileHostName(
        other,
        customName: projectHomeCustomHostName(customNames, other),
      ),
      'rhost_beta',
    );
  });

  test('a blank chosen name falls back to the pairing derived name', () {
    final profile = _pairedHost(hostId: 'rhost_alpha', deviceId: 'phone');
    final customNames = {projectHomeCustomHostNameKey(profile): '   '};

    expect(projectHomeCustomHostName(customNames, profile), isNull);
    expect(projectHomeGatewayProfileHostName(profile, customName: '   '),
        'rhost_alpha');
  });

  test('merge replaces existing profile with same host and device', () {
    final oldProfile = _pairedHost(
      hostId: 'project',
      deviceId: 'phone',
      token: 'old-token',
      routeKind: RouteProviderKind.lan,
    );
    final replacement = _pairedHost(
      hostId: 'project',
      deviceId: 'phone',
      token: 'new-token',
      routeKind: RouteProviderKind.cloudflareTunnel,
    );
    final otherProfile = _pairedHost(hostId: 'other', deviceId: 'tablet');

    final merged = mergeProjectHomeGatewayProfiles([
      oldProfile,
      otherProfile,
    ], replacement);

    expect(merged, hasLength(2));
    expect(merged, contains(same(replacement)));
    expect(merged, contains(same(otherProfile)));
    expect(merged, isNot(contains(same(oldProfile))));
  });

  test('merge result is sorted by project home profile label', () {
    final beta = _pairedHost(hostId: 'beta', deviceId: 'phone');
    final alphaTablet = _pairedHost(hostId: 'alpha', deviceId: 'tablet');
    final alphaPhone = _pairedHost(hostId: 'alpha', deviceId: 'phone');

    final merged = mergeProjectHomeGatewayProfiles([
      beta,
      alphaTablet,
    ], alphaPhone);

    expect(merged.map(projectHomeGatewayProfileLabel), [
      'alpha / phone / lan',
      'alpha / tablet / lan',
      'beta / phone / lan',
    ]);
  });

  test(
    'load without debug profile keeps selected profile or chooses store first',
    () async {
      final beta = _pairedHost(hostId: 'beta', deviceId: 'phone');
      final alpha = _pairedHost(hostId: 'alpha', deviceId: 'phone');
      final store = _OrderedProfileStore([beta, alpha]);
      final bootstrapper = ProjectHomeProfileBootstrapper(store: store);

      final keptSelection = await bootstrapper.bootstrap(
        selectedProfile: beta,
        debugProfile: null,
      );
      final firstSelection = await bootstrapper.bootstrap(
        selectedProfile: null,
        debugProfile: null,
      );

      expect(keptSelection.selectedProfile, same(beta));
      expect(firstSelection.selectedProfile, same(beta));
      expect(firstSelection.profiles.map(projectHomeGatewayProfileLabel), [
        'beta / phone / lan',
        'alpha / phone / lan',
      ]);
    },
  );

  test(
    'restart restores last successful new route instead of sorted old route',
    () async {
      final secureStore = MemorySecureStore();
      var now = DateTime.utc(2026, 7, 10, 9);
      final store = GatewayHostProfileStore(
        secureStore: secureStore,
        now: () => now,
      );
      final oldRoute = _pairedHost(
        hostId: 'host-demo',
        deviceId: 'old-phone',
        gatewayUrl: Uri.parse('http://127.0.0.1:18899'),
      );
      await store.save(oldRoute);
      now = now.add(const Duration(minutes: 1));
      final newRoute = _pairedHost(
        hostId: 'host-demo',
        deviceId: 'new-phone',
        gatewayUrl: Uri.parse('http://127.0.0.1:8787'),
      );
      await store.save(newRoute);
      await store.markSuccessful(newRoute);

      final restarted = ProjectHomeProfileBootstrapper(store: store);
      final result = await restarted.load(selectedProfile: null);

      expect(result.profiles, hasLength(2));
      expect(result.selectedProfile?.profile.deviceId, 'new-phone');
      expect(
        result.selectedProfile?.profile.routeProvider.gatewayUrl,
        Uri.parse('http://127.0.0.1:8787'),
      );
    },
  );

  test(
    'an unverified selected profile cannot displace the last successful route',
    () async {
      final store = GatewayHostProfileStore(secureStore: MemorySecureStore());
      final successful = _pairedHost(
        hostId: 'host-demo',
        deviceId: 'phone',
        gatewayUrl: Uri.parse('http://127.0.0.1:8787'),
      );
      final failed = _pairedHost(
        hostId: 'host-demo',
        deviceId: 'tablet',
        gatewayUrl: Uri.parse('https://host-demo.example.test'),
      );
      await store.save(successful);
      await store.markSuccessful(successful);
      await store.save(failed);
      // Represents a selection made before the gateway can be verified.
      await store.markSelected(failed);

      final restored = await ProjectHomeProfileBootstrapper(
        store: store,
      ).load(selectedProfile: null);

      expect(restored.selectedProfile?.profile.deviceId, 'phone');
      expect(
        restored.selectedProfile?.profile.routeProvider.gatewayUrl,
        Uri.parse('http://127.0.0.1:8787'),
      );
    },
  );

  test(
    'legacy profiles migrate to their most recently saved route deterministically',
    () async {
      final secureStore = MemorySecureStore();
      var now = DateTime.utc(2026, 7, 10, 9);
      final store = GatewayHostProfileStore(
        secureStore: secureStore,
        now: () => now,
      );
      final older = _pairedHost(
        hostId: 'host-demo',
        deviceId: 'old-phone',
        gatewayUrl: Uri.parse('http://127.0.0.1:18899'),
      );
      await store.save(older);
      now = now.add(const Duration(minutes: 1));
      final newer = _pairedHost(
        hostId: 'host-demo',
        deviceId: 'new-phone',
        gatewayUrl: Uri.parse('http://127.0.0.1:8787'),
      );
      await store.save(newer);

      final firstBoot = await ProjectHomeProfileBootstrapper(
        store: store,
      ).load(selectedProfile: null);
      final secondBoot = await ProjectHomeProfileBootstrapper(
        store: store,
      ).load(selectedProfile: null);

      expect(firstBoot.selectedProfile?.profile.deviceId, 'new-phone');
      expect(secondBoot.selectedProfile?.profile.deviceId, 'new-phone');
    },
  );

  test(
    'same host and route supersedes obsolete local device profile',
    () async {
      final store = GatewayHostProfileStore(secureStore: MemorySecureStore());
      final obsolete = _pairedHost(
        hostId: 'host-demo',
        deviceId: 'old-phone',
        gatewayUrl: Uri.parse('http://127.0.0.1:8787'),
      );
      final replacement = _pairedHost(
        hostId: 'host-demo',
        deviceId: 'new-phone',
        gatewayUrl: Uri.parse('http://127.0.0.1:8787'),
      );

      await store.save(obsolete);
      await store.markSuccessful(obsolete);
      await store.save(replacement);

      final profiles = await store.list();
      expect(profiles.map((profile) => profile.profile.deviceId), [
        'new-phone',
      ]);
      expect(
        (await store.resolvePreferred(profiles))?.profile.deviceId,
        'new-phone',
      );
    },
  );

  test(
    'debug profile is saved, selected, and marked for auto activation',
    () async {
      final store = GatewayHostProfileStore(secureStore: MemorySecureStore());
      final bootstrapper = ProjectHomeProfileBootstrapper(store: store);
      final stored = _pairedHost(hostId: 'stored', deviceId: 'phone');
      final debug = _pairedHost(hostId: 'debug', deviceId: 'phone');
      await store.save(stored);

      final result = await bootstrapper.bootstrap(
        selectedProfile: stored,
        debugProfile: debug,
        autoActivateDebugProfile: true,
      );
      final savedDebug = await store.read(hostId: 'debug', deviceId: 'phone');

      expect(result.selectedProfile, same(debug));
      expect(result.activateProfile, same(debug));
      expect(savedDebug?.deviceToken, 'debug-token');
      expect(result.profiles.map(projectHomeGatewayProfileLabel), [
        'debug / phone / lan',
        'stored / phone / lan',
      ]);
    },
  );
}

class _OrderedProfileStore extends GatewayHostProfileStore {
  _OrderedProfileStore(this._profiles)
    : super(secureStore: MemorySecureStore());

  final List<GatewayPairedHost> _profiles;

  @override
  Future<List<GatewayPairedHost>> list() async => List.of(_profiles);
}

GatewayPairedHost _pairedHost({
  required String hostId,
  required String deviceId,
  String? token,
  RouteProviderKind routeKind = RouteProviderKind.lan,
  Uri? gatewayUrl,
}) {
  return GatewayPairedHost(
    profile: GatewayHostProfile(
      hostId: hostId,
      deviceId: deviceId,
      routeProvider: RouteProvider(
        kind: routeKind,
        gatewayUrl: gatewayUrl ?? Uri.parse('http://$hostId.example.test'),
      ),
      scopes: const {'view'},
    ),
    deviceToken: token ?? '$hostId-token',
    projectId: hostId,
    createdAt: DateTime.utc(2026, 6, 22),
  );
}
