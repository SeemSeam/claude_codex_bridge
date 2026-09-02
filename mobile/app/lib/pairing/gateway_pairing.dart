import 'dart:convert';
import 'dart:io';

import 'package:flutter_secure_storage/flutter_secure_storage.dart';

import '../transport/route_provider.dart';

const gatewayPairingConnectionCodePrefix = 'ccb1_';
const gatewayCompactRelayQrPrefix = 'ccbr1_';
const _maxGatewayPairingTextLength = 16 * 1024;

class GatewayPairingException implements Exception {
  GatewayPairingException(this.uri, this.statusCode, this.message);

  final Uri uri;
  final int statusCode;
  final String message;

  @override
  String toString() {
    return 'GatewayPairingException($statusCode $uri: $message)';
  }
}

class GatewayPairingPayload {
  const GatewayPairingPayload({
    required this.pairingCode,
    required this.claimEndpoint,
    required this.routeProvider,
    required this.gatewayUrl,
    required this.scopes,
    this.projectId,
    this.expiresAt,
    this.hostId,
    this.websocketUrl,
    this.relayMode,
    this.hostFingerprint,
    this.relayBootstrap,
    this.relayBootstrapExpiresAt,
    this.relayBootstrapSingleUse = false,
  });

  final String pairingCode;
  final Uri claimEndpoint;
  final RouteProviderKind routeProvider;
  final Uri gatewayUrl;
  final Set<String> scopes;
  final String? projectId;
  final DateTime? expiresAt;
  final String? hostId;
  final Uri? websocketUrl;
  final RelayDeploymentMode? relayMode;
  final String? hostFingerprint;
  final RelayPhoneSessionBootstrap? relayBootstrap;
  final DateTime? relayBootstrapExpiresAt;
  final bool relayBootstrapSingleUse;

  factory GatewayPairingPayload.fromJson(Map<String, Object?> json) {
    final routeProvider = RouteProviderKind.fromWireName(
      _requiredText(json['route_provider'], 'route_provider'),
    );
    final gatewayUrl = _requiredUri(json['gateway_url'], 'gateway_url');
    final websocketUrl = _optionalUri(json['websocket_url']);
    final relayMode = RelayDeploymentMode.maybeFromJson(json['relay_mode']);
    validateRelayDeployment(
      kind: routeProvider,
      mode: relayMode,
      gatewayUrl: gatewayUrl,
      websocketUrl: websocketUrl,
    );
    return GatewayPairingPayload(
      pairingCode: _requiredText(json['pairing_code'], 'pairing_code'),
      claimEndpoint: _requiredUri(json['claim_endpoint'], 'claim_endpoint'),
      routeProvider: routeProvider,
      gatewayUrl: gatewayUrl,
      scopes: _stringSet(json['scopes']),
      projectId: _optionalText(json['project_id']),
      expiresAt: _optionalDateTime(json['expires_at']),
      hostId: _optionalText(json['host_id']),
      websocketUrl: websocketUrl,
      relayMode: relayMode,
      hostFingerprint: _optionalText(json['server_fingerprint']),
      relayBootstrap: RelayPhoneSessionBootstrap.maybeFromJson(json),
      relayBootstrapExpiresAt: _optionalDateTime(
        json['relay_bootstrap_expires_at'],
      ),
      relayBootstrapSingleUse: json['relay_bootstrap_single_use'] == true,
    );
  }

  factory GatewayPairingPayload.fromQrText(String text) {
    return GatewayPairingPayload.fromConnectionText(text);
  }

  factory GatewayPairingPayload.fromConnectionText(String text) {
    final trimmed = text.trim();
    if (trimmed.isEmpty) {
      throw const FormatException('connection code is empty');
    }
    if (trimmed.length > _maxGatewayPairingTextLength) {
      throw const FormatException('connection code is too large');
    }
    if (trimmed.startsWith(gatewayCompactRelayQrPrefix)) {
      return _compactRelayPairingPayload(trimmed);
    }
    var payloadText = trimmed;
    if (trimmed.startsWith(gatewayPairingConnectionCodePrefix)) {
      final encoded = trimmed.substring(
        gatewayPairingConnectionCodePrefix.length,
      );
      if (encoded.isEmpty) {
        throw const FormatException('connection code payload is empty');
      }
      try {
        final decodedBytes = base64Url.decode(base64Url.normalize(encoded));
        if (decodedBytes.length > _maxGatewayPairingTextLength) {
          throw const FormatException('connection code payload is too large');
        }
        payloadText = utf8.decode(decodedBytes, allowMalformed: false);
      } on FormatException {
        throw const FormatException('connection code is invalid');
      }
    }
    final decoded = jsonDecode(payloadText);
    if (decoded is Map) {
      return GatewayPairingPayload.fromJson({
        for (final entry in decoded.entries) entry.key.toString(): entry.value,
      });
    }
    throw const FormatException(
      'connection code must contain a pairing object',
    );
  }

  String toConnectionCode() {
    final payload = utf8.encode(jsonEncode(toJson()));
    final encoded = base64Url.encode(payload).replaceFirst(RegExp(r'=+$'), '');
    return '$gatewayPairingConnectionCodePrefix$encoded';
  }

  Map<String, Object?> toJson() {
    return {
      'pairing_code': pairingCode,
      'claim_endpoint': claimEndpoint.toString(),
      'route_provider': routeProvider.wireName,
      'gateway_url': gatewayUrl.toString(),
      'scopes': scopes.toList()..sort(),
      if (_hasText(projectId)) 'project_id': projectId,
      if (expiresAt != null) 'expires_at': expiresAt!.toUtc().toIso8601String(),
      if (_hasText(hostId)) 'host_id': hostId,
      if (websocketUrl != null) 'websocket_url': websocketUrl.toString(),
      if (relayMode != null) 'relay_mode': relayMode!.wireName,
      if (_hasText(hostFingerprint)) 'server_fingerprint': hostFingerprint,
      if (relayBootstrap != null) ...relayBootstrap!.toJson(),
      if (relayBootstrapExpiresAt != null)
        'relay_bootstrap_expires_at':
            relayBootstrapExpiresAt!.toUtc().toIso8601String(),
      if (relayBootstrapSingleUse) 'relay_bootstrap_single_use': true,
    };
  }
}

GatewayPairingPayload _compactRelayPairingPayload(String text) {
  final fields = text.substring(gatewayCompactRelayQrPrefix.length).split('|');
  if (fields.length != 5 || fields.any((value) => value.trim().isEmpty)) {
    throw const FormatException('compact relay QR is incomplete');
  }
  final [
    pairingCode,
    clientPrivateKeyB64,
    hostFingerprint,
    modeCode,
    rendezvousCapability,
  ] = fields;
  final relayMode = switch (modeCode) {
    'o' => RelayDeploymentMode.official,
    's' => RelayDeploymentMode.selfHosted,
    _ => throw const FormatException('compact relay QR mode is invalid'),
  };
  final capability = _relayCapabilityPayload(rendezvousCapability);
  if (_optionalText(capability['typ']) != 'ccb-relay-rv-v1') {
    throw const FormatException('compact relay QR capability type is invalid');
  }
  final websocketUrl = _requiredUri(
    capability['aud'],
    'relay capability audience',
  );
  if ((websocketUrl.scheme != 'wss' && websocketUrl.scheme != 'ws') ||
      !websocketUrl.hasAuthority ||
      websocketUrl.userInfo.isNotEmpty ||
      (websocketUrl.path.isNotEmpty && websocketUrl.path != '/') ||
      websocketUrl.hasQuery ||
      websocketUrl.hasFragment) {
    throw const FormatException('compact relay QR audience is invalid');
  }
  final gatewayUrl = Uri(
    scheme: websocketUrl.scheme == 'wss' ? 'https' : 'http',
    host: websocketUrl.host,
    port: websocketUrl.hasPort ? websocketUrl.port : null,
  );
  final expiresAt = _relayCapabilityExpiry(capability['exp']);
  return GatewayPairingPayload.fromJson({
    'pairing_code': pairingCode,
    'claim_endpoint': gatewayUrl.resolve('/v1/pairing/claim').toString(),
    'route_provider': RouteProviderKind.relay.wireName,
    'gateway_url': gatewayUrl.toString(),
    'scopes': const <String>[],
    'host_id': _requiredText(capability['host_id'], 'relay capability host_id'),
    'websocket_url': websocketUrl.toString(),
    'relay_mode': relayMode.wireName,
    'server_fingerprint': hostFingerprint,
    'relay_session_id': _requiredText(
      capability['session_id'],
      'relay capability session_id',
    ),
    'relay_client_private_key_b64': clientPrivateKeyB64,
    'relay_phone_nonce_b64': _requiredText(
      capability['phone_nonce_b64'],
      'relay capability phone_nonce_b64',
    ),
    'relay_rendezvous_capability': rendezvousCapability,
    'relay_bootstrap_expires_at': expiresAt.toIso8601String(),
    'relay_bootstrap_single_use': true,
  });
}

Map<String, Object?> _relayCapabilityPayload(String capability) {
  final segments = capability.split('.');
  if (segments.length != 3 || segments.any((segment) => segment.isEmpty)) {
    throw const FormatException('compact relay QR capability is invalid');
  }
  try {
    final bytes = base64Url.decode(base64Url.normalize(segments[1]));
    final decoded = jsonDecode(utf8.decode(bytes, allowMalformed: false));
    if (decoded is Map) {
      return {
        for (final entry in decoded.entries) entry.key.toString(): entry.value,
      };
    }
  } on FormatException {
    // Report one stable pairing error without exposing capability contents.
  }
  throw const FormatException('compact relay QR capability is invalid');
}

DateTime _relayCapabilityExpiry(Object? value) {
  if (value is! num ||
      !value.isFinite ||
      value <= 0 ||
      value != value.truncateToDouble()) {
    throw const FormatException('compact relay QR expiry is invalid');
  }
  return DateTime.fromMillisecondsSinceEpoch(
    value.toInt() * Duration.millisecondsPerSecond,
    isUtc: true,
  );
}

class GatewayPairedHost {
  const GatewayPairedHost({
    required this.profile,
    required this.deviceToken,
    this.projectId,
    this.createdAt,
    this.savedAt,
  });

  final GatewayHostProfile profile;
  final String deviceToken;
  final String? projectId;
  final DateTime? createdAt;
  final DateTime? savedAt;

  GatewayPairedHost copyWith({DateTime? savedAt}) {
    return GatewayPairedHost(
      profile: profile,
      deviceToken: deviceToken,
      projectId: projectId,
      createdAt: createdAt,
      savedAt: savedAt ?? this.savedAt,
    );
  }

  factory GatewayPairedHost.fromClaimJson(
    Map<String, Object?> json, {
    required GatewayPairingPayload pairing,
    String? relayPhoneAuthPrivateKeyB64,
  }) {
    final hostProfile = _map(json['host_profile']);
    final device = _map(json['device']);
    final deviceToken = _requiredText(json['device_token'], 'device_token');
    final deviceId =
        _optionalText(hostProfile['device_id']) ??
        _requiredText(device['device_id'], 'device.device_id');
    final projectId =
        _optionalText(hostProfile['project_id']) ?? pairing.projectId;
    final hostId =
        _optionalText(hostProfile['host_id']) ??
        projectId ??
        _requiredText(device['project_id'], 'device.project_id');
    final claimedAccessGrant = _optionalText(hostProfile['relay_access_grant']);
    final relayAccess =
        _hasText(claimedAccessGrant) && _hasText(relayPhoneAuthPrivateKeyB64)
            ? RelayPhoneAccessCredentials(
              accessGrant: claimedAccessGrant!,
              phoneAuthPrivateKeyB64: relayPhoneAuthPrivateKeyB64!,
            )
            : RelayPhoneAccessCredentials.maybeFromJson(hostProfile);
    final routeKind = RouteProviderKind.fromWireName(
      _optionalText(hostProfile['route_provider']) ??
          pairing.routeProvider.wireName,
    );
    final gatewayUrl =
        _optionalUri(hostProfile['gateway_url']) ?? pairing.gatewayUrl;
    final websocketUrl =
        _optionalUri(hostProfile['websocket_url']) ?? pairing.websocketUrl;
    final claimedRelayMode = RelayDeploymentMode.maybeFromJson(
      hostProfile['relay_mode'],
    );
    if (claimedRelayMode != null &&
        pairing.relayMode != null &&
        claimedRelayMode != pairing.relayMode) {
      throw const FormatException(
        'claimed relay deployment mode does not match pairing',
      );
    }
    final relayMode = claimedRelayMode ?? pairing.relayMode;
    validateRelayDeployment(
      kind: routeKind,
      mode: relayMode,
      gatewayUrl: gatewayUrl,
      websocketUrl: websocketUrl,
    );
    final routeProvider = RouteProvider(
      kind: routeKind,
      gatewayUrl: gatewayUrl,
      websocketUrl: websocketUrl,
      relayMode: relayMode,
      hostFingerprint: _optionalText(hostProfile['server_fingerprint']),
      relayBootstrap:
          relayAccess == null
              ? RelayPhoneSessionBootstrap.maybeFromJson(hostProfile)
              : null,
      relayAccess: relayAccess,
      capabilities: _stringSet(hostProfile['capabilities']),
      diagnostics: _stringMap(hostProfile['diagnostics']),
    );
    return GatewayPairedHost(
      profile: GatewayHostProfile(
        hostId: hostId,
        deviceId: deviceId,
        routeProvider: routeProvider,
        scopes:
            _stringSet(hostProfile['scopes']).isEmpty
                ? pairing.scopes
                : _stringSet(hostProfile['scopes']),
      ),
      deviceToken: deviceToken,
      projectId: projectId,
      createdAt: _optionalDateTime(device['created_at']),
    );
  }

  factory GatewayPairedHost.fromSecureJson(Map<String, Object?> json) {
    final profileJson = _map(json['profile']);
    final routeKind = RouteProviderKind.fromWireName(
      _requiredText(profileJson['route_provider'], 'route_provider'),
    );
    final gatewayUrl = _requiredUri(profileJson['gateway_url'], 'gateway_url');
    final websocketUrl = _optionalUri(profileJson['websocket_url']);
    final relayMode = RelayDeploymentMode.maybeFromJson(
      profileJson['relay_mode'],
    );
    validateRelayDeployment(
      kind: routeKind,
      mode: relayMode,
      gatewayUrl: gatewayUrl,
      websocketUrl: websocketUrl,
    );
    final routeProvider = RouteProvider(
      kind: routeKind,
      gatewayUrl: gatewayUrl,
      websocketUrl: websocketUrl,
      relayMode: relayMode,
      hostFingerprint: _optionalText(profileJson['server_fingerprint']),
      relayBootstrap: RelayPhoneSessionBootstrap.maybeFromJson(profileJson),
      relayAccess: RelayPhoneAccessCredentials.maybeFromJson(profileJson),
      capabilities: _stringSet(profileJson['capabilities']),
      diagnostics: _stringMap(profileJson['diagnostics']),
    );
    return GatewayPairedHost(
      profile: GatewayHostProfile(
        hostId: _requiredText(profileJson['host_id'], 'host_id'),
        deviceId: _requiredText(profileJson['device_id'], 'device_id'),
        routeProvider: routeProvider,
        scopes: _stringSet(profileJson['scopes']),
      ),
      deviceToken: _requiredText(json['device_token'], 'device_token'),
      projectId: _optionalText(json['project_id']),
      createdAt: _optionalDateTime(json['created_at']),
      savedAt: _optionalDateTime(json['saved_at']),
    );
  }

  Map<String, Object?> toSecureJson() {
    return {
      'schema_version': 2,
      'profile': profile.toJson(),
      'device_token': deviceToken,
      if (_hasText(projectId)) 'project_id': projectId,
      if (createdAt != null) 'created_at': createdAt!.toUtc().toIso8601String(),
      if (savedAt != null) 'saved_at': savedAt!.toUtc().toIso8601String(),
    };
  }
}

class GatewayPairingClient {
  GatewayPairingClient({
    HttpClient? httpClient,
    Duration timeout = const Duration(seconds: 5),
  }) : _httpClient = httpClient ?? HttpClient(),
       _timeout = timeout;

  final HttpClient _httpClient;
  final Duration _timeout;

  Future<GatewayPairedHost> claim({
    required GatewayPairingPayload pairing,
    required String deviceName,
    String? deviceId,
  }) async {
    final json = await _postJson(pairing.claimEndpoint, {
      'pairing_code': pairing.pairingCode,
      'device_name': deviceName,
      if (_hasText(deviceId)) 'device_id': deviceId,
    });
    return GatewayPairedHost.fromClaimJson(json, pairing: pairing);
  }

  Future<GatewayPairedHost> claimAndStore({
    required GatewayPairingPayload pairing,
    required String deviceName,
    required GatewayHostProfileStore store,
    String? deviceId,
  }) async {
    final paired = await claim(
      pairing: pairing,
      deviceName: deviceName,
      deviceId: deviceId,
    );
    await store.save(paired);
    return paired;
  }

  void close({bool force = false}) {
    _httpClient.close(force: force);
  }

  Future<Map<String, Object?>> _postJson(
    Uri uri,
    Map<String, Object?> payload,
  ) async {
    final request = await _httpClient.postUrl(uri).timeout(_timeout);
    request.headers.set(HttpHeaders.acceptHeader, 'application/json');
    request.headers.contentType = ContentType.json;
    final bodyBytes = utf8.encode(jsonEncode(payload));
    request.contentLength = bodyBytes.length;
    request.add(bodyBytes);
    final response = await request.close().timeout(_timeout);
    final body = await utf8.decodeStream(response).timeout(_timeout);
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw GatewayPairingException(uri, response.statusCode, body);
    }
    final decoded = jsonDecode(body);
    if (decoded is Map) {
      return {
        for (final entry in decoded.entries) entry.key.toString(): entry.value,
      };
    }
    throw FormatException('pairing response is not a JSON object: $uri');
  }
}

abstract interface class GatewaySecureStore {
  Future<String?> read({required String key});

  Future<void> write({required String key, required String value});

  Future<void> delete({required String key});
}

class FlutterGatewaySecureStore implements GatewaySecureStore {
  FlutterGatewaySecureStore({FlutterSecureStorage? storage})
    : _storage = storage ?? const FlutterSecureStorage();

  final FlutterSecureStorage _storage;

  @override
  Future<String?> read({required String key}) {
    return _storage.read(key: key);
  }

  @override
  Future<void> write({required String key, required String value}) {
    return _storage.write(key: key, value: value);
  }

  @override
  Future<void> delete({required String key}) {
    return _storage.delete(key: key);
  }
}

class GatewayHostProfileStore {
  GatewayHostProfileStore({
    GatewaySecureStore? secureStore,
    DateTime Function()? now,
  }) : _secureStore = secureStore ?? FlutterGatewaySecureStore(),
       _now = now ?? DateTime.now;

  static const _indexKey = 'ccb_mobile.gateway_profiles.index';
  static const _profilePrefix = 'ccb_mobile.gateway_profiles.profile.';
  static const _hostNamesKey = 'ccb_mobile.gateway_profiles.host_names';
  static const _lastSelectedKey = 'ccb_mobile.gateway_profiles.last_selected';
  static const _lastSuccessfulKey =
      'ccb_mobile.gateway_profiles.last_successful';

  final GatewaySecureStore _secureStore;
  final DateTime Function() _now;

  Future<void> save(GatewayPairedHost host) async {
    final stored = host.copyWith(savedAt: _now().toUtc());
    final key = profileKey(stored);
    await _secureStore.write(
      key: key,
      value: jsonEncode(stored.toSecureJson()),
    );
    final keys = await _readIndex();
    final supersededKeys = <String>[];
    for (final existingKey in keys) {
      if (existingKey == key) {
        continue;
      }
      final existing = await _readProfile(existingKey);
      if (existing != null && _sameHostRoute(existing, stored)) {
        supersededKeys.add(existingKey);
      }
    }
    for (final supersededKey in supersededKeys) {
      await _secureStore.delete(key: supersededKey);
      await _clearPreferenceFor(supersededKey);
    }
    keys.removeWhere(supersededKeys.contains);
    keys.add(key);
    await _writeIndex(keys);
  }

  Future<GatewayPairedHost?> read({
    required String hostId,
    required String deviceId,
  }) async {
    return _readProfile(_profileKey(hostId, deviceId));
  }

  Future<List<GatewayPairedHost>> list() async {
    final result = <GatewayPairedHost>[];
    for (final key in await _readIndex()) {
      final profile = await _readProfile(key);
      if (profile != null) {
        result.add(profile);
      }
    }
    return result;
  }

  /// Names the user gave paired computers on this phone, keyed by [hostNameKey].
  /// A computer that was never renamed is simply absent, so callers keep falling
  /// back to the name derived from the pairing itself.
  Future<Map<String, String>> listHostNames() async {
    final raw = await _secureStore.read(key: _hostNamesKey);
    if (!_hasText(raw)) {
      return const {};
    }
    try {
      final decoded = jsonDecode(raw!);
      if (decoded is! Map) {
        return const {};
      }
      return {
        for (final entry in decoded.entries)
          if (_optionalText(entry.value) case final name?)
            entry.key.toString(): name,
      };
    } on FormatException {
      return const {};
    }
  }

  /// Renames one paired computer. A null or blank [name] clears the stored name
  /// so the computer falls back to the name derived from its pairing. The name
  /// never leaves the phone: pairing carries no computer name to write back.
  Future<void> writeHostName({
    required String hostId,
    required String deviceId,
    required String? name,
  }) async {
    final key = hostNameKey(hostId: hostId, deviceId: deviceId);
    final names = {...await listHostNames()};
    final trimmed = _optionalText(name);
    if (trimmed == null) {
      if (names.remove(key) == null) {
        return;
      }
    } else {
      if (names[key] == trimmed) {
        return;
      }
      names[key] = trimmed;
    }
    await _writeHostNames(names);
  }

  /// Returns the most recently verified profile, falling back to a persisted
  /// selection and then a deterministic legacy choice. A legacy fallback is
  /// intentionally not persisted as a selection: only a completed gateway
  /// activation may establish a new preferred profile.
  Future<GatewayPairedHost?> resolvePreferred(
    Iterable<GatewayPairedHost> profiles,
  ) async {
    final candidates = profiles.toList(growable: false);
    if (candidates.isEmpty) {
      return null;
    }
    for (final preferenceKey in [_lastSuccessfulKey, _lastSelectedKey]) {
      final profile = await _readPreferredFrom(
        preferenceKey: preferenceKey,
        profiles: candidates,
      );
      if (profile != null) {
        return profile;
      }
    }
    return _mostRecentProfile(candidates);
  }

  /// Returns a device ID that may be included in a fresh one-time pairing
  /// claim. It never returns a device token and requires the same host/project
  /// evidence plus the exact route before reuse.
  Future<String?> reusableDeviceIdFor(GatewayPairingPayload pairing) async {
    if (!_hasText(pairing.projectId)) {
      return null;
    }
    final matches = (await list())
        .where((profile) => _matchesPairingRoute(profile, pairing))
        .toList(growable: false);
    if (matches.isEmpty) {
      return null;
    }
    for (final preferenceKey in [_lastSuccessfulKey, _lastSelectedKey]) {
      final profile = await _readPreferredFrom(
        preferenceKey: preferenceKey,
        profiles: matches,
      );
      if (profile != null) {
        return profile.profile.deviceId;
      }
    }
    return _mostRecentProfile(matches).profile.deviceId;
  }

  Future<void> markSelected(GatewayPairedHost host) {
    return _secureStore.write(key: _lastSelectedKey, value: profileKey(host));
  }

  Future<void> markSuccessful(GatewayPairedHost host) async {
    final key = profileKey(host);
    await _secureStore.write(key: _lastSelectedKey, value: key);
    await _secureStore.write(key: _lastSuccessfulKey, value: key);
  }

  Future<void> delete({
    required String hostId,
    required String deviceId,
  }) async {
    final key = _profileKey(hostId, deviceId);
    await _secureStore.delete(key: key);
    await _clearPreferenceFor(key);
    // An unpaired computer must not leave its chosen name behind, so a computer
    // paired again later starts from its pairing-derived name.
    await writeHostName(hostId: hostId, deviceId: deviceId, name: null);
    final keys = await _readIndex();
    keys.remove(key);
    await _writeIndex(keys);
  }

  Future<GatewayPairedHost?> _readProfile(String key) async {
    final raw = await _secureStore.read(key: key);
    if (!_hasText(raw)) {
      return null;
    }
    final decoded = jsonDecode(raw!);
    if (decoded is Map) {
      return GatewayPairedHost.fromSecureJson({
        for (final entry in decoded.entries) entry.key.toString(): entry.value,
      });
    }
    throw FormatException('stored gateway profile is not a JSON object');
  }

  Future<List<String>> _readIndex() async {
    final raw = await _secureStore.read(key: _indexKey);
    if (!_hasText(raw)) {
      return [];
    }
    final decoded = jsonDecode(raw!);
    if (decoded is Iterable) {
      return [for (final item in decoded) item.toString()];
    }
    return [];
  }

  Future<void> _writeIndex(List<String> keys) {
    final unique = keys.toSet().toList()..sort();
    return _secureStore.write(key: _indexKey, value: jsonEncode(unique));
  }

  Future<void> _writeHostNames(Map<String, String> names) {
    if (names.isEmpty) {
      return _secureStore.delete(key: _hostNamesKey);
    }
    return _secureStore.write(key: _hostNamesKey, value: jsonEncode(names));
  }

  Future<GatewayPairedHost?> _readPreferredFrom({
    required String preferenceKey,
    required Iterable<GatewayPairedHost> profiles,
  }) async {
    final selectedKey = await _secureStore.read(key: preferenceKey);
    if (!_hasText(selectedKey)) {
      return null;
    }
    for (final profile in profiles) {
      if (profileKey(profile) == selectedKey) {
        return profile;
      }
    }
    await _secureStore.delete(key: preferenceKey);
    return null;
  }

  Future<void> _clearPreferenceFor(String profileKey) async {
    for (final preferenceKey in [_lastSelectedKey, _lastSuccessfulKey]) {
      if (await _secureStore.read(key: preferenceKey) == profileKey) {
        await _secureStore.delete(key: preferenceKey);
      }
    }
  }

  static GatewayPairedHost _mostRecentProfile(
    Iterable<GatewayPairedHost> profiles,
  ) {
    return profiles.reduce((best, candidate) {
      final bestTime = best.savedAt ?? best.createdAt;
      final candidateTime = candidate.savedAt ?? candidate.createdAt;
      final timeComparison = _compareNullableDateTimes(candidateTime, bestTime);
      if (timeComparison != 0) {
        return timeComparison > 0 ? candidate : best;
      }
      return profileKey(candidate).compareTo(profileKey(best)) > 0
          ? candidate
          : best;
    });
  }

  static bool _matchesPairingRoute(
    GatewayPairedHost profile,
    GatewayPairingPayload pairing,
  ) {
    final pairingProjectId = pairing.projectId;
    return pairingProjectId != null &&
        (profile.profile.hostId == pairingProjectId ||
            profile.projectId == pairingProjectId) &&
        profile.profile.routeProvider.kind == pairing.routeProvider &&
        _canonicalGatewayUrl(profile.profile.routeProvider.gatewayUrl) ==
            _canonicalGatewayUrl(pairing.gatewayUrl);
  }

  static bool _sameHostRoute(GatewayPairedHost a, GatewayPairedHost b) {
    return a.profile.hostId == b.profile.hostId &&
        a.profile.routeProvider.kind == b.profile.routeProvider.kind &&
        _canonicalGatewayUrl(a.profile.routeProvider.gatewayUrl) ==
            _canonicalGatewayUrl(b.profile.routeProvider.gatewayUrl);
  }

  static String _canonicalGatewayUrl(Uri uri) {
    return uri.replace(path: '', query: null, fragment: null).toString();
  }

  static int _compareNullableDateTimes(DateTime? a, DateTime? b) {
    if (a == null) {
      return b == null ? 0 : -1;
    }
    if (b == null) {
      return 1;
    }
    return a.compareTo(b);
  }

  static String profileKey(GatewayPairedHost host) {
    return _profileKey(host.profile.hostId, host.profile.deviceId);
  }

  /// Stable identity of one paired computer. The profile storage key and the
  /// host name map share this encoding, so a host or device id containing a
  /// separator can never make one computer answer for another.
  static String hostNameKey({
    required String hostId,
    required String deviceId,
  }) {
    return base64Url
        .encode(utf8.encode('$hostId\n$deviceId'))
        .replaceAll('=', '');
  }

  static String _profileKey(String hostId, String deviceId) {
    return '$_profilePrefix${hostNameKey(hostId: hostId, deviceId: deviceId)}';
  }
}

Map<String, Object?> _map(Object? value) {
  if (value is Map) {
    return {
      for (final entry in value.entries) entry.key.toString(): entry.value,
    };
  }
  return const {};
}

Map<String, String> _stringMap(Object? value) {
  if (value is Map) {
    return {
      for (final entry in value.entries)
        entry.key.toString(): entry.value.toString(),
    };
  }
  return const {};
}

Set<String> _stringSet(Object? value) {
  if (value is Iterable) {
    return {for (final item in value) item.toString()};
  }
  return const {};
}

String _requiredText(Object? value, String field) {
  final text = _optionalText(value);
  if (!_hasText(text)) {
    throw FormatException('pairing response missing $field');
  }
  return text!;
}

String? _optionalText(Object? value) {
  final text = (value ?? '').toString().trim();
  return text.isEmpty ? null : text;
}

Uri _requiredUri(Object? value, String field) {
  final uri = _optionalUri(value);
  if (uri == null) {
    throw FormatException('pairing response missing $field');
  }
  return uri;
}

Uri? _optionalUri(Object? value) {
  final text = _optionalText(value);
  if (text == null) {
    return null;
  }
  final uri = Uri.tryParse(text);
  if (uri == null || !uri.hasScheme) {
    throw FormatException('invalid URI: $text');
  }
  return uri;
}

DateTime? _optionalDateTime(Object? value) {
  final parsed = DateTime.tryParse((value ?? '').toString());
  return parsed?.toUtc();
}

bool _hasText(String? value) => value != null && value.trim().isNotEmpty;
