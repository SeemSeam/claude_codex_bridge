import 'package:ccb_mobile/pairing/gateway_pairing.dart';
import 'package:ccb_mobile/transport/route_provider.dart';
import 'package:test/test.dart';

import 'support/project_home_test_fakes.dart';

void main() {
  test('paired computer has no stored name until it is renamed', () async {
    final store = GatewayHostProfileStore(secureStore: MemorySecureStore());

    expect(await store.listHostNames(), isEmpty);
  });

  test('renaming one computer keeps the other computers untouched', () async {
    final store = GatewayHostProfileStore(secureStore: MemorySecureStore());

    await store.writeHostName(
      hostId: 'rhost_alpha',
      deviceId: 'phone',
      name: '公司台式机',
    );
    await store.writeHostName(
      hostId: 'rhost_beta',
      deviceId: 'phone',
      name: 'MacBook',
    );

    expect(await store.listHostNames(), {
      GatewayHostProfileStore.hostNameKey(
        hostId: 'rhost_alpha',
        deviceId: 'phone',
      ): '公司台式机',
      GatewayHostProfileStore.hostNameKey(
        hostId: 'rhost_beta',
        deviceId: 'phone',
      ): 'MacBook',
    });
  });

  test('a name is trimmed and a blank name clears the stored name', () async {
    final store = GatewayHostProfileStore(secureStore: MemorySecureStore());
    final key = GatewayHostProfileStore.hostNameKey(
      hostId: 'rhost_alpha',
      deviceId: 'phone',
    );

    await store.writeHostName(
      hostId: 'rhost_alpha',
      deviceId: 'phone',
      name: '  书房主机  ',
    );
    expect(await store.listHostNames(), {key: '书房主机'});

    await store.writeHostName(
      hostId: 'rhost_alpha',
      deviceId: 'phone',
      name: '   ',
    );
    expect(await store.listHostNames(), isEmpty);
  });

  test('the same device id under another host keeps its own name', () async {
    final store = GatewayHostProfileStore(secureStore: MemorySecureStore());

    await store.writeHostName(
      hostId: 'rhost_alpha',
      deviceId: 'phone',
      name: 'Alpha',
    );
    await store.writeHostName(
      hostId: 'rhost_alpha',
      deviceId: 'tablet',
      name: 'Tablet route',
    );

    expect(await store.listHostNames(), hasLength(2));
    expect(
      await store.listHostNames(),
      containsPair(
        GatewayHostProfileStore.hostNameKey(
          hostId: 'rhost_alpha',
          deviceId: 'phone',
        ),
        'Alpha',
      ),
    );
  });

  test('unpairing a computer drops the name stored for it', () async {
    final secureStore = MemorySecureStore();
    final store = GatewayHostProfileStore(secureStore: secureStore);
    final paired = _pairedHost(hostId: 'rhost_alpha', deviceId: 'phone');

    await store.save(paired);
    await store.writeHostName(
      hostId: 'rhost_alpha',
      deviceId: 'phone',
      name: '公司台式机',
    );
    await store.writeHostName(
      hostId: 'rhost_beta',
      deviceId: 'phone',
      name: 'MacBook',
    );

    await store.delete(hostId: 'rhost_alpha', deviceId: 'phone');

    expect(await store.list(), isEmpty);
    expect(await store.listHostNames(), {
      GatewayHostProfileStore.hostNameKey(
        hostId: 'rhost_beta',
        deviceId: 'phone',
      ): 'MacBook',
    });
  });

  test('a malformed stored name map is ignored instead of thrown', () async {
    final secureStore = MemorySecureStore();
    final store = GatewayHostProfileStore(secureStore: secureStore);
    await store.writeHostName(
      hostId: 'rhost_alpha',
      deviceId: 'phone',
      name: 'Alpha',
    );
    final key = secureStore.values.keys.firstWhere(
      (candidate) => candidate.endsWith('.host_names'),
    );
    secureStore.values[key] = '["not-a-map"]';

    expect(await store.listHostNames(), isEmpty);
  });
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
