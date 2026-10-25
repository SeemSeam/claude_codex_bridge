import 'dart:async';
import 'dart:collection';
import 'dart:convert';
import 'dart:io';
import 'dart:math';

import 'package:cryptography/cryptography.dart';

import '../models/ccb_agent_conversation.dart';
import '../models/ccb_project.dart';
import '../models/ccb_project_lifecycle.dart';
import '../models/ccb_project_view.dart';
import '../models/ccb_provider_control.dart';
import '../models/readable_terminal_history.dart';
import 'gateway_transport.dart';
import 'relay_crypto.dart';
import 'relay_gateway_transport.dart';
import 'relay_protocol.dart';
import 'relay_stream_protocol.dart';
import 'route_provider.dart';

class RelayGatewayException implements Exception {
  const RelayGatewayException(this.message, {this.statusCode});

  final String message;
  final int? statusCode;

  @override
  String toString() {
    final status = statusCode == null ? '' : '$statusCode ';
    return 'RelayGatewayException($status$message)';
  }
}

class RelaySocketGatewayTransport
    implements
        GatewayTransport,
        GatewayUiResponseTransport,
        GatewayProviderControlTransport,
        GatewayHostTerminalTransport {
  static const _fileChunkBytes = 32 * 1024;
  static const _maxUploadBytes = 25 * 1024 * 1024;
  static const _maxDownloadBytes = 128 * 1024 * 1024;
  static const _fileTransferTimeout = Duration(minutes: 2);

  RelaySocketGatewayTransport({
    required this.profile,
    required String deviceToken,
    HttpClient? httpClient,
    Duration timeout = const Duration(seconds: 8),
    Duration projectListWarmupRetryDelay = const Duration(milliseconds: 200),
    int projectListWarmupMaxAttempts = 16,
    bool allowInsecureLoopbackForTests = false,
  }) : _deviceToken = deviceToken,
       _httpClient = httpClient ?? HttpClient(),
       _timeout = timeout,
       _projectListWarmupRetryDelay = projectListWarmupRetryDelay,
       _projectListWarmupMaxAttempts = projectListWarmupMaxAttempts,
       _allowInsecureLoopbackForTests = allowInsecureLoopbackForTests {
    if (profile.routeProvider.kind != RouteProviderKind.relay) {
      throw ArgumentError.value(
        profile.routeProvider.kind.wireName,
        'profile.routeProvider.kind',
        'RelaySocketGatewayTransport requires a relay profile',
      );
    }
    _validatedRelayOrigin(
      profile.routeProvider.websocketUrl,
      allowInsecureLoopbackForTests: allowInsecureLoopbackForTests,
    );
    if (!_hasText(profile.routeProvider.hostFingerprint)) {
      throw ArgumentError.value(
        profile.routeProvider.hostFingerprint,
        'profile.routeProvider.hostFingerprint',
        'RelaySocketGatewayTransport requires a host fingerprint',
      );
    }
    if (profile.routeProvider.relayBootstrap == null &&
        profile.routeProvider.relayAccess == null) {
      throw ArgumentError.value(
        null,
        'profile.routeProvider',
        'RelaySocketGatewayTransport requires relay bootstrap or access credentials',
      );
    }
  }

  @override
  final GatewayHostProfile profile;

  final String _deviceToken;
  final HttpClient _httpClient;
  final Duration _timeout;
  final Duration _projectListWarmupRetryDelay;
  final int _projectListWarmupMaxAttempts;
  final bool _allowInsecureLoopbackForTests;
  _RelaySocketSession? _session;
  Future<_RelaySocketSession>? _connecting;
  var _nextIdentifier = 1;
  bool _closed = false;

  @override
  Future<GatewayHealth> health() async {
    final body = await _requestBody('health', const {});
    return GatewayHealth(
      status: _text(body['status'], fallback: 'unknown'),
      serverTime: _dateTime(body['server_time']),
      capabilities: _stringSet(body['capabilities']),
    );
  }

  Future<Map<String, Object?>> claimPairing({
    required String pairingCode,
    required String deviceName,
    required String phoneAuthPublicKeyB64,
    String? deviceId,
  }) {
    return _requestBody('pair_claim', {
      'pairing_code': pairingCode,
      'device_name': deviceName,
      'phone_auth_pubkey_b64': phoneAuthPublicKeyB64,
      if (_hasText(deviceId)) 'device_id': deviceId,
    });
  }

  @override
  Future<GatewayDevice> device() async {
    final body = await _requestBody('device', const {});
    return GatewayDevice.fromJson(_objectMap(body['device'], 'device'));
  }

  @override
  Future<List<CcbProject>> listProjects() async {
    final attempts =
        _projectListWarmupMaxAttempts < 1 ? 1 : _projectListWarmupMaxAttempts;
    for (var attempt = 0; attempt < attempts; attempt += 1) {
      final body = await _requestBody('list_projects', const {});
      final projects = body['projects'];
      if (projects is! Iterable) {
        throw const FormatException('relay projects response missing projects');
      }
      final parsed = [
        for (final item in projects)
          if (item is Map)
            CcbProject.fromJson({
              for (final entry in item.entries)
                entry.key.toString(): entry.value,
            }),
      ];
      final warming = body['health_warming'] == true;
      if (!warming || attempt == attempts - 1) {
        return parsed;
      }
      await Future<void>.delayed(_projectListWarmupRetryDelay);
    }
    return const <CcbProject>[];
  }

  @override
  Future<CcbProjectView> getProjectView(String projectId) async {
    final body = await _requestBody('get_project_view', {
      'project_id': projectId,
    });
    return CcbProjectView.fromProjectViewPayload(body);
  }

  @override
  Future<CcbProviderControlDetails> getAgentProviderControl({
    required String projectId,
    required String agentName,
  }) async {
    final body = await _requestBody('get_agent_provider_control', {
      'project_id': projectId,
      'agent': agentName,
    });
    return CcbProviderControlDetails.fromJson(body);
  }

  @override
  Future<CcbProviderAccountUsage> getAgentProviderQuota({
    required String projectId,
    required String agentName,
  }) async {
    final body = await _requestBody('get_agent_provider_quota', {
      'project_id': projectId,
      'agent': agentName,
    });
    return CcbProviderAccountUsage.fromJson(
      _objectMap(body['account_usage'], 'account_usage'),
    );
  }

  @override
  Future<CcbProviderSettingsResult> updateAgentProviderSettings({
    required String projectId,
    required String agentName,
    required String model,
    String? thinking,
    required String expectedRevision,
    required int expectedNamespaceEpoch,
    required String expectedProvider,
    String? expectedRuntimeRevision,
    required String idempotencyKey,
  }) async {
    final body = await _requestBody('update_agent_provider_settings', {
      'project_id': projectId,
      'agent': agentName,
      'model': model,
      if (_hasText(thinking)) 'thinking': thinking,
      'expected_revision': expectedRevision,
      'expected_namespace_epoch': expectedNamespaceEpoch,
      'expected_provider': expectedProvider,
      'expected_runtime_revision': expectedRuntimeRevision,
      'idempotency_key': idempotencyKey,
    });
    return CcbProviderSettingsResult.fromJson(body);
  }

  @override
  Future<CcbProjectView> focusAgent({
    required String projectId,
    required String agent,
    required int namespaceEpoch,
  }) async {
    final body = await _requestBody('focus_agent', {
      'project_id': projectId,
      'agent': agent,
      'namespace_epoch': namespaceEpoch,
    });
    return CcbProjectView.fromProjectViewPayload(body);
  }

  @override
  Future<CcbProjectView> focusWindow({
    required String projectId,
    required String window,
    required int namespaceEpoch,
  }) async {
    final body = await _requestBody('focus_window', {
      'project_id': projectId,
      'window': window,
      'namespace_epoch': namespaceEpoch,
    });
    return CcbProjectView.fromProjectViewPayload(body);
  }

  @override
  Future<ReadableTerminalHistory?> getReadableTerminalHistory({
    required String projectId,
    required String agent,
    required int namespaceEpoch,
    int maxLines = 200,
  }) async {
    final body = await _requestBody('terminal_history', {
      'project_id': projectId,
      'agent': agent,
      'namespace_epoch': namespaceEpoch,
      'max_lines': maxLines,
    });
    return ReadableTerminalHistory.fromJson(
      agentName: agent,
      json: _objectMap(body['terminal_history'], 'terminal_history'),
    );
  }

  @override
  Future<CcbAgentConversation> getAgentConversation({
    required String projectId,
    required String agent,
    required int namespaceEpoch,
    int limit = 50,
    String? cursor,
  }) async {
    final body = await _requestBody('agent_conversation', {
      'project_id': projectId,
      'agent': agent,
      'namespace_epoch': namespaceEpoch,
      'limit': limit,
      if (_hasText(cursor)) 'cursor': cursor,
    });
    return CcbAgentConversation.fromJson(body);
  }

  @override
  Future<void> uiResponse({required String projectId, required String agent,
    required int namespaceEpoch, required String expectedOmpSessionId,
    required String expectedOmpGeneration, required int reqId, required String value}) async {
    await _requestBody('ui_response', {
      'project_id': projectId, 'agent': agent, 'namespace_epoch': namespaceEpoch,
      'expected_omp_session_id': expectedOmpSessionId,
      'expected_omp_generation': expectedOmpGeneration, 'req_id': reqId, 'value': value,
    });
  }

  @override
  Future<CcbAgentMessageSubmitResult> submitAgentMessage(
    CcbAgentMessageSubmitRequest request,
  ) async {
    final body = await _requestBody('submit_agent_message', request.toJson());
    return CcbAgentMessageSubmitResult.fromJson(body);
  }

  @override
  Future<CcbProjectLifecycleResult> requestLifecycle({
    required String projectId,
    required CcbLifecycleAction action,
  }) async {
    final body = await _requestBody('lifecycle', {
      'project_id': projectId,
      'action': action.wireName,
    });
    return CcbProjectLifecycleResult.fromJson(body);
  }

  @override
  Future<GatewayTerminalHandle> openTerminal(
    GatewayTerminalOpenRequest request,
  ) async {
    final body = await _requestBody('open_terminal', request.toJson());
    return _terminalHandle(body);
  }

  @override
  Future<GatewayTerminalHandle> openHostTerminal(
    GatewayHostTerminalOpenRequest request,
  ) async {
    final body = await _requestBody('open_host_terminal', request.toJson());
    return _terminalHandle(body);
  }

  @override
  Future<void> terminateHostTerminal({required String clientSessionId}) async {
    await _requestBody('terminate_host_terminal', {
      'schema_version': 1,
      'client_session_id': clientSessionId,
    });
  }

  @override
  Stream<GatewayTerminalFrame> terminalFrames(
    GatewayTerminalHandle handle, {
    int? resumeCursor,
  }) {
    late final StreamController<GatewayTerminalFrame> controller;
    _RelayClientStream? relayStream;
    StreamSubscription<Map<String, Object?>>? subscription;

    Future<void> cancel() async {
      await subscription?.cancel();
      final stream = relayStream;
      if (stream != null) {
        await _cancelStream(stream);
      }
    }

    Future<void> connect() async {
      try {
        final stream = await _openStream(
          operation: 'terminal',
          payload: {
            'terminal_id': handle.terminalId,
            'terminal_token': handle.terminalToken,
            if (resumeCursor != null) 'resume_cursor': resumeCursor,
          },
          terminalId: handle.terminalId,
        );
        relayStream = stream;
        subscription = stream.events.listen(
          (payload) {
            final frame = _objectMap(payload['frame'], 'terminal frame');
            controller.add(GatewayTerminalFrame.fromJson(frame));
          },
          onError: controller.addError,
          onDone: controller.close,
        );
      } catch (error, stackTrace) {
        controller.addError(error, stackTrace);
        await controller.close();
      }
    }

    controller = StreamController<GatewayTerminalFrame>(
      onListen: () => unawaited(connect()),
      onPause: () => subscription?.pause(),
      onResume: () => subscription?.resume(),
      onCancel: cancel,
    );
    return controller.stream;
  }

  @override
  Future<void> sendTerminalFrame(
    GatewayTerminalHandle handle,
    GatewayTerminalFrame frame,
  ) async {
    final session = await _ensureSession();
    final stream = session.terminalStreams[handle.terminalId];
    if (stream == null || stream.closed) {
      throw const RelayGatewayException('relay terminal stream is not open');
    }
    await _sendStreamData(stream, {'frame': frame.toJson()});
  }

  @override
  Future<GatewayFileUploadResult> uploadFile({
    required String projectId,
    required String agentName,
    required String fileName,
    required String mimeType,
    required List<int> bytes,
  }) async {
    if (bytes.length > _maxUploadBytes) {
      throw const RelayGatewayException('relay upload exceeds size limit');
    }
    final stream = await _openStream(
      operation: 'file_upload',
      payload: {
        'project_id': projectId,
        'agent': agentName,
        'file_name': fileName,
        'mime_type': mimeType,
        if (_hasText(_deviceToken)) 'device_token': _deviceToken,
      },
    );
    final result = Completer<Map<String, Object?>>();
    late final StreamSubscription<Map<String, Object?>> subscription;
    subscription = stream.events.listen(
      (payload) {
        final value = payload['result'];
        if (!result.isCompleted && value is Map) {
          result.complete({
            for (final entry in value.entries)
              entry.key.toString(): entry.value,
          });
        }
      },
      onError: (Object error, StackTrace stackTrace) {
        if (!result.isCompleted) {
          result.completeError(error, stackTrace);
        }
      },
      onDone: () {
        if (!result.isCompleted) {
          result.completeError(
            const RelayGatewayException('relay upload ended without result'),
          );
        }
      },
    );
    try {
      for (var offset = 0; offset < bytes.length; offset += _fileChunkBytes) {
        final end = min(offset + _fileChunkBytes, bytes.length);
        await _sendStreamData(stream, {
          'chunk_b64': _b64Encode(bytes.sublist(offset, end)),
        });
      }
      await _sendStreamData(stream, const {'eof': true});
      final response = await result.future.timeout(_fileTransferTimeout);
      return GatewayFileUploadResult.fromJson(
        _relayResultBody(response, operation: 'upload'),
      );
    } finally {
      await subscription.cancel();
      await _cancelStream(stream);
    }
  }

  @override
  Future<List<int>> downloadFile({
    required String projectId,
    required String agentName,
    required String fileId,
  }) async {
    final stream = await _openStream(
      operation: 'file_download',
      payload: {
        'project_id': projectId,
        'agent': agentName,
        'file_id': fileId,
        if (_hasText(_deviceToken)) 'device_token': _deviceToken,
      },
    );
    final bytes = <int>[];
    final completed = Completer<void>();
    late final StreamSubscription<Map<String, Object?>> subscription;
    subscription = stream.events.listen(
      (payload) {
        final result = payload['result'];
        if (result is Map) {
          try {
            _relayResultBody({
              for (final entry in result.entries)
                entry.key.toString(): entry.value,
            }, operation: 'download');
          } catch (error, stackTrace) {
            if (!completed.isCompleted) {
              completed.completeError(error, stackTrace);
            }
          }
          return;
        }
        final chunk = _optionalText(payload['chunk_b64']);
        if (chunk != null) {
          bytes.addAll(_b64Decode(chunk));
          if (bytes.length > _maxDownloadBytes && !completed.isCompleted) {
            completed.completeError(
              const RelayGatewayException('relay download exceeds size limit'),
            );
          }
        }
        if (payload['eof'] == true && !completed.isCompleted) {
          completed.complete();
        }
      },
      onError: (Object error, StackTrace stackTrace) {
        if (!completed.isCompleted) {
          completed.completeError(error, stackTrace);
        }
      },
      onDone: () {
        if (!completed.isCompleted) {
          completed.completeError(
            const RelayGatewayException('relay download ended before EOF'),
          );
        }
      },
    );
    try {
      await completed.future.timeout(_fileTransferTimeout);
      return bytes;
    } finally {
      await subscription.cancel();
      await _cancelStream(stream);
    }
  }

  Stream<Map<String, Object?>> notificationEvents({
    String? lastEventId,
    Map<String, String> watchQuery = const {},
    void Function()? onConnected,
  }) {
    late final StreamController<Map<String, Object?>> controller;
    _RelayClientStream? relayStream;
    StreamSubscription<Map<String, Object?>>? subscription;

    Future<void> cancel() async {
      await subscription?.cancel();
      final stream = relayStream;
      if (stream != null) {
        await _cancelStream(stream);
      }
    }

    Future<void> connect() async {
      try {
        final stream = await _openStream(
          operation: 'notifications',
          payload: {
            if (_hasText(lastEventId)) 'last_event_id': lastEventId,
            ...watchQuery,
            if (_hasText(_deviceToken)) 'device_token': _deviceToken,
          },
          onReady: onConnected,
        );
        relayStream = stream;
        subscription = stream.events.listen(
          (payload) {
            controller.add(_objectMap(payload['event'], 'notification event'));
          },
          onError: controller.addError,
          onDone: controller.close,
        );
      } catch (error, stackTrace) {
        controller.addError(error, stackTrace);
        await controller.close();
      }
    }

    controller = StreamController<Map<String, Object?>>(
      onListen: () => unawaited(connect()),
      onPause: () => subscription?.pause(),
      onResume: () => subscription?.resume(),
      onCancel: cancel,
    );
    return controller.stream;
  }

  Future<Map<String, Object?>> _requestBody(
    String operation,
    Map<String, Object?> payload,
  ) async {
    final response = await _request(operation, payload);
    return _objectMap(response['body'], 'body');
  }

  Future<Map<String, Object?>> _request(
    String operation,
    Map<String, Object?> payload,
  ) async {
    if (_closed) {
      throw const RelayGatewayException('relay transport is closed');
    }
    final session = await _ensureSession();
    if (session.advertisesUnaryOperations &&
        !session.unaryOperations.contains(operation)) {
      throw const RelayGatewayException('operation_not_allowed');
    }
    final requestId = _identifier('request');
    final completer = Completer<Map<String, Object?>>();
    session.pendingRequests[requestId] = completer;
    try {
      final requestPayload = {
        ...payload,
        if (_hasText(_deviceToken)) 'device_token': _deviceToken,
      };
      await _sendInner(
        session,
        RelayInnerMessage.request(
          requestId: requestId,
          operation: operation,
          payload: requestPayload,
        ),
      );
      return await completer.future.timeout(_timeout);
    } finally {
      session.pendingRequests.remove(requestId);
    }
  }

  Future<_RelaySocketSession> _ensureSession() async {
    final existing = _session;
    if (existing != null &&
        !existing.closed &&
        existing.socket.readyState == WebSocket.open) {
      return existing;
    }
    final connecting = _connecting;
    if (connecting != null) {
      return connecting;
    }
    final future = _openSession();
    _connecting = future;
    try {
      return await future;
    } finally {
      if (identical(_connecting, future)) {
        _connecting = null;
      }
    }
  }

  Future<_RelaySocketSession> _openSession() async {
    if (_closed) {
      throw const RelayGatewayException('relay transport is closed');
    }
    final route = profile.routeProvider;
    final relayOrigin = _validatedRelayOrigin(
      route.websocketUrl,
      allowInsecureLoopbackForTests: _allowInsecureLoopbackForTests,
    );
    final socket = await WebSocket.connect(
      relayOrigin.resolve('/v2/phone').toString(),
      customClient: _httpClient,
    ).timeout(_timeout);
    socket.pingInterval = const Duration(seconds: 20);
    StreamIterator<dynamic>? reader;
    RelayCryptoSession? crypto;
    try {
      final access = route.relayAccess;
      final bootstrap = route.relayBootstrap;
      late final String sessionId;
      late final List<int> clientPrivateKeyBytes;
      late final String phoneNonceB64;
      final authorization = <String, Object?>{};
      if (access != null) {
        sessionId = 'session-${_b64Encode(_randomBytes(18))}';
        clientPrivateKeyBytes = _randomBytes(32);
        phoneNonceB64 = _b64Encode(_randomBytes(24));
      } else if (bootstrap != null) {
        sessionId = bootstrap.sessionId;
        clientPrivateKeyBytes = _b64Decode(bootstrap.clientPrivateKeyB64);
        phoneNonceB64 = bootstrap.phoneNonceB64;
        authorization['rendezvous_capability'] = bootstrap.rendezvousCapability;
      } else {
        throw const RelayGatewayException(
          'relay bootstrap or access credentials are missing',
        );
      }
      final clientPublicKeyB64 = await _publicKeyB64(clientPrivateKeyBytes);
      if (access != null) {
        authorization['access_grant'] = access.accessGrant;
        authorization['phone_session_proof'] = await _phoneSessionProof(
          access: access,
          relayAudience: relayOrigin.toString(),
          hostId: profile.hostId,
          deviceId: profile.deviceId,
          sessionId: sessionId,
          clientPublicKeyB64: clientPublicKeyB64,
          phoneNonceB64: phoneNonceB64,
        );
      }
      final clientHello = RelayFrame(
        sessionId: sessionId,
        sequence: 1,
        kind: RelayFrameKind.clientHello,
        payload: {
          'host_id': profile.hostId,
          'device_id': profile.deviceId,
          'client_pubkey_b64': clientPublicKeyB64,
          'phone_nonce_b64': phoneNonceB64,
          'supported_versions': [relayProtocolVersion],
          ...authorization,
        },
      );
      socket.add(jsonEncode(clientHello.toJson()));
      reader = StreamIterator<dynamic>(socket);
      final hostHello = await _receiveFrame(reader, timeout: _timeout);
      if (hostHello.kind != RelayFrameKind.hostHello) {
        throw const RelayGatewayException('relay host hello was not received');
      }
      RelayHandshakeTranscript.negotiate(
        clientHello: clientHello,
        hostHello: hostHello,
      );
      final observedFingerprint = _requiredText(
        hostHello.payload['server_fingerprint'],
        'host_hello.server_fingerprint',
      );
      if (observedFingerprint != route.hostFingerprint) {
        throw const RelayGatewayException('relay host fingerprint mismatch');
      }
      final hostPublicKeyB64 = _requiredText(
        hostHello.payload['host_pubkey_b64'],
        'host_hello.host_pubkey_b64',
      );
      final schedule = await RelayV2KeySchedule.derive(
        localPrivateKeyBytes: clientPrivateKeyBytes,
        peerPublicKeyB64: hostPublicKeyB64,
        role: 'phone',
        sessionId: sessionId,
        clientPublicKeyB64: clientPublicKeyB64,
        hostPublicKeyB64: hostPublicKeyB64,
        expectedHostFingerprint: route.hostFingerprint!,
      );
      crypto = schedule.session(role: 'phone');
      final session = _RelaySocketSession(
        socket: socket,
        reader: reader,
        crypto: crypto,
        unaryOperations: _stringSet(hostHello.payload['unary_operations']),
        streamOperations: _stringSet(hostHello.payload['stream_operations']),
        advertisesUnaryOperations: hostHello.payload.containsKey(
          'unary_operations',
        ),
        advertisesStreamOperations: hostHello.payload.containsKey(
          'stream_operations',
        ),
      );
      crypto = null;
      _session = session;
      session.readTask = _readLoop(session);
      return session;
    } catch (_) {
      crypto?.close();
      await reader?.cancel();
      await socket.close();
      rethrow;
    }
  }

  Future<void> _sendInner(
    _RelaySocketSession session,
    RelayInnerMessage message,
  ) {
    return session.sendSerial.run(() async {
      if (session.closed || session.socket.readyState != WebSocket.open) {
        throw const RelayGatewayException('relay socket disconnected');
      }
      final envelope = await session.crypto.seal(
        operation: 'relay.inner.v1',
        plaintext: message.encode(),
      );
      session.socket.add(
        jsonEncode(
          RelayFrame.gatewayEnvelope(
            envelope: RelayGatewayEnvelope(
              schemaVersion: envelope.schemaVersion,
              sessionId: envelope.sessionId,
              sequence: envelope.sequence,
              operation: envelope.operation,
              direction: envelope.direction,
              ciphertextB64: envelope.ciphertextB64,
              nonceB64: envelope.nonceB64,
              keyId: envelope.keyId,
            ),
            sequence: session.nextOuterSequence++,
          ).toJson(),
        ),
      );
    });
  }

  Future<_RelayClientStream> _openStream({
    required String operation,
    required Map<String, Object?> payload,
    String? terminalId,
    void Function()? onReady,
  }) async {
    final session = await _ensureSession();
    if (session.advertisesStreamOperations &&
        !session.streamOperations.contains(operation)) {
      throw const RelayGatewayException('operation_not_allowed');
    }
    final streamId = _identifier('stream');
    late final _RelayClientStream stream;
    stream = _RelayClientStream(
      streamId: streamId,
      operation: operation,
      terminalId: terminalId,
      onReady: onReady,
      sendWindow:
          (credit) => _sendInner(
            session,
            RelayInnerMessage.streamWindow(
              streamId: streamId,
              creditBytes: credit,
            ),
          ),
      cancel: () => _cancelStream(stream),
    );
    session.streams[streamId] = stream;
    if (terminalId != null) {
      session.terminalStreams[terminalId] = stream;
    }
    try {
      await _sendInner(
        session,
        RelayInnerMessage.streamOpen(
          streamId: streamId,
          operation: operation,
          payload: payload,
        ),
      );
      return stream;
    } catch (_) {
      session.streams.remove(streamId);
      _forgetTerminalStream(session, stream);
      await stream.close();
      rethrow;
    }
  }

  Future<void> _sendStreamData(
    _RelayClientStream stream,
    Map<String, Object?> payload,
  ) async {
    final session = await _ensureSession();
    if (!identical(session.streams[stream.streamId], stream)) {
      throw const RelayGatewayException('relay stream is stale');
    }
    await stream.sendSerial.run(() async {
      final size = relayInnerPayloadSize(payload);
      await stream.takeSendCredit(size, timeout: _timeout);
      await _sendInner(
        session,
        RelayInnerMessage.streamData(
          streamId: stream.streamId,
          payload: payload,
        ),
      );
    });
  }

  Future<void> _cancelStream(_RelayClientStream stream) async {
    if (stream.closed) {
      return;
    }
    final session = _session;
    if (session != null &&
        identical(session.streams[stream.streamId], stream)) {
      session.streams.remove(stream.streamId);
      _forgetTerminalStream(session, stream);
      if (!session.closed) {
        try {
          await _sendInner(
            session,
            RelayInnerMessage.streamCancel(stream.streamId),
          );
        } catch (_) {
          // Socket teardown below is authoritative when cancellation cannot send.
        }
      }
    }
    await stream.close();
  }

  Future<void> _readLoop(_RelaySocketSession session) async {
    Object? failure;
    StackTrace? failureStack;
    try {
      while (!session.closed && session.socket.readyState == WebSocket.open) {
        final frame = await _receiveFrame(session.reader);
        if (frame.kind == RelayFrameKind.close) {
          throw const RelayGatewayException('relay session closed');
        }
        if (frame.kind != RelayFrameKind.gatewayEnvelope) {
          continue;
        }
        final envelope = frame.gatewayEnvelope();
        if (envelope.operation != 'relay.inner.v1') {
          throw const RelayGatewayException('relay inner protocol mismatch');
        }
        final plaintext = await session.crypto.open(
          RelayV2Envelope.fromJson({
            ...envelope.toJson(),
            if (envelope.direction == null)
              'direction': RelayCryptoDirection.hostToPhone.wireName,
          }),
        );
        await _dispatchInner(session, RelayInnerMessage.decode(plaintext));
      }
    } catch (error, stackTrace) {
      failure = error;
      failureStack = stackTrace;
    } finally {
      await _discardSession(
        session,
        failure ?? const RelayGatewayException('relay socket disconnected'),
        failureStack,
      );
    }
  }

  Future<void> _dispatchInner(
    _RelaySocketSession session,
    RelayInnerMessage message,
  ) async {
    final requestId = message.requestId;
    if (message.kind == RelayInnerKind.response && requestId != null) {
      final completer = session.pendingRequests.remove(requestId);
      if (completer != null && !completer.isCompleted) {
        final response = Map<String, Object?>.from(message.payload);
        if (response['ok'] != true) {
          completer.completeError(
            RelayGatewayException(
              _text(
                response['error'],
                fallback: 'relay gateway request failed',
              ),
              statusCode: _int(response['status']),
            ),
          );
        } else {
          completer.complete(response);
        }
      }
      return;
    }
    final streamId = message.streamId;
    if (message.kind == RelayInnerKind.error) {
      final error = RelayGatewayException(
        _text(message.payload['code'], fallback: 'relay request rejected'),
      );
      if (requestId != null) {
        final completer = session.pendingRequests.remove(requestId);
        if (completer == null) {
          throw error;
        }
        completer.completeError(error);
      } else if (streamId != null) {
        final stream = session.streams.remove(streamId);
        final terminalId = stream?.terminalId;
        if (terminalId != null &&
            identical(session.terminalStreams[terminalId], stream)) {
          session.terminalStreams.remove(terminalId);
        }
        stream?.addError(error);
        await stream?.close();
      }
      return;
    }
    if (streamId == null) {
      throw const RelayGatewayException('relay stream identity missing');
    }
    final stream = session.streams[streamId];
    if (stream == null || stream.closed) {
      return;
    }
    switch (message.kind) {
      case RelayInnerKind.streamData:
        await stream.add(message.payload);
      case RelayInnerKind.streamWindow:
        stream.addSendCredit(message.creditBytes ?? 0);
      case RelayInnerKind.streamClose:
      case RelayInnerKind.streamCancel:
        session.streams.remove(streamId);
        _forgetTerminalStream(session, stream);
        await stream.close();
      case RelayInnerKind.request:
      case RelayInnerKind.response:
      case RelayInnerKind.streamOpen:
      case RelayInnerKind.error:
        throw const RelayGatewayException(
          'relay inner message direction invalid',
        );
    }
  }

  Future<RelayFrame> _receiveFrame(
    StreamIterator<dynamic> reader, {
    Duration? timeout,
  }) async {
    final moved =
        timeout == null
            ? await reader.moveNext()
            : await reader.moveNext().timeout(timeout);
    if (!moved) {
      throw const RelayGatewayException('relay socket disconnected');
    }
    final message = reader.current;
    final decoded = jsonDecode(switch (message) {
      final String value => value,
      final List<int> value => utf8.decode(value),
      _ => throw const FormatException('relay frame message unsupported'),
    });
    if (decoded is! Map) {
      throw const FormatException('relay frame is not an object');
    }
    final json = {
      for (final entry in decoded.entries) entry.key.toString(): entry.value,
    };
    if (json['kind'] == 'error') {
      final payload = _objectMap(json['payload'], 'payload');
      throw RelayGatewayException(
        _text(payload['code'], fallback: 'relay_rejected'),
      );
    }
    return RelayFrame.fromJson(json);
  }

  Future<void> close({bool force = false}) async {
    _closed = true;
    final session = _session;
    if (session != null) {
      await _discardSession(
        session,
        const RelayGatewayException('relay transport is closed'),
        null,
      );
    }
    _httpClient.close(force: force);
  }

  void _forgetTerminalStream(
    _RelaySocketSession session,
    _RelayClientStream stream,
  ) {
    final terminalId = stream.terminalId;
    if (terminalId != null &&
        identical(session.terminalStreams[terminalId], stream)) {
      session.terminalStreams.remove(terminalId);
    }
  }

  String _identifier(String prefix) {
    final next = _nextIdentifier++;
    return '$prefix-${DateTime.now().microsecondsSinceEpoch}-$next';
  }

  Future<void> _discardSession(
    _RelaySocketSession session,
    Object error,
    StackTrace? stackTrace,
  ) async {
    if (session.closed) {
      return;
    }
    session.closed = true;
    if (identical(_session, session)) {
      _session = null;
    }
    for (final completer in session.pendingRequests.values) {
      if (!completer.isCompleted) {
        completer.completeError(error, stackTrace);
      }
    }
    session.pendingRequests.clear();
    for (final stream in session.streams.values.toList(growable: false)) {
      stream.addError(error, stackTrace);
      await stream.close();
    }
    session.streams.clear();
    session.terminalStreams.clear();
    session.crypto.close();
    await session.reader.cancel();
    await session.socket.close();
  }
}

class _RelaySocketSession {
  _RelaySocketSession({
    required this.socket,
    required this.reader,
    required this.crypto,
    required this.unaryOperations,
    required this.streamOperations,
    required this.advertisesUnaryOperations,
    required this.advertisesStreamOperations,
  });

  final WebSocket socket;
  final StreamIterator<dynamic> reader;
  final RelayCryptoSession crypto;
  final Set<String> unaryOperations;
  final Set<String> streamOperations;
  final bool advertisesUnaryOperations;
  final bool advertisesStreamOperations;
  final sendSerial = _SerialExecutor();
  final pendingRequests = <String, Completer<Map<String, Object?>>>{};
  final streams = <String, _RelayClientStream>{};
  final terminalStreams = <String, _RelayClientStream>{};
  Future<void>? readTask;
  int nextOuterSequence = 2;
  bool closed = false;
}

class _RelayClientStream {
  _RelayClientStream({
    required this.streamId,
    required this.operation,
    required this.sendWindow,
    required this.cancel,
    this.terminalId,
    this.onReady,
  }) {
    _controller = StreamController<Map<String, Object?>>(
      sync: true,
      onPause: () => _paused = true,
      onResume: () {
        _paused = false;
        _flushPendingWindow();
      },
      onCancel: () => cancel(),
    );
  }

  final String streamId;
  final String operation;
  final String? terminalId;
  final void Function()? onReady;
  final Future<void> Function(int credit) sendWindow;
  final Future<void> Function() cancel;
  final sendSerial = _SerialExecutor();
  late final StreamController<Map<String, Object?>> _controller;
  int _sendCredit = 0;
  int _pendingWindow = 0;
  Completer<void>? _creditChanged;
  bool _paused = false;
  bool _readyReported = false;
  bool closed = false;

  Stream<Map<String, Object?>> get events => _controller.stream;

  void addSendCredit(int credit) {
    if (credit <= 0 || closed) {
      return;
    }
    if (_sendCredit + credit > relayStreamMaxWindowBytes) {
      addError(const RelayGatewayException('relay stream credit overflow'));
      unawaited(close());
      return;
    }
    _sendCredit += credit;
    if (!_readyReported) {
      _readyReported = true;
      onReady?.call();
    }
    final changed = _creditChanged;
    _creditChanged = null;
    if (changed != null && !changed.isCompleted) {
      changed.complete();
    }
  }

  Future<void> takeSendCredit(int bytes, {required Duration timeout}) async {
    if (bytes <= 0 || bytes > relayStreamMaxMessageBytes) {
      throw const RelayGatewayException('relay stream payload is too large');
    }
    while (!closed && _sendCredit < bytes) {
      final changed = _creditChanged ??= Completer<void>();
      await changed.future.timeout(timeout);
    }
    if (closed) {
      throw const RelayGatewayException('relay stream is closed');
    }
    _sendCredit -= bytes;
  }

  Future<void> add(Map<String, Object?> payload) async {
    if (closed) {
      return;
    }
    final bytes = relayInnerPayloadSize(payload);
    _controller.add(payload);
    if (_paused) {
      _pendingWindow += bytes;
    } else {
      await sendWindow(bytes);
    }
  }

  void addError(Object error, [StackTrace? stackTrace]) {
    if (!closed && !_controller.isClosed) {
      _controller.addError(error, stackTrace);
    }
  }

  Future<void> _flushPendingWindow() async {
    final credit = _pendingWindow;
    _pendingWindow = 0;
    if (credit > 0 && !closed) {
      try {
        await sendWindow(credit);
      } catch (error, stackTrace) {
        addError(error, stackTrace);
      }
    }
  }

  Future<void> close() async {
    if (closed) {
      return;
    }
    closed = true;
    final changed = _creditChanged;
    _creditChanged = null;
    if (changed != null && !changed.isCompleted) {
      changed.complete();
    }
    if (!_controller.isClosed) {
      await _controller.close();
    }
  }
}

class _SerialExecutor {
  Future<void> _tail = Future<void>.value();

  Future<T> run<T>(Future<T> Function() action) async {
    final previous = _tail.catchError((_) {});
    final completer = Completer<void>();
    _tail = previous.then((_) => completer.future);
    await previous;
    try {
      return await action();
    } finally {
      completer.complete();
    }
  }
}

Future<String> _phoneSessionProof({
  required RelayPhoneAccessCredentials access,
  required String relayAudience,
  required String hostId,
  required String deviceId,
  required String sessionId,
  required String clientPublicKeyB64,
  required String phoneNonceB64,
}) async {
  final now = DateTime.now().toUtc().millisecondsSinceEpoch ~/ 1000;
  final grantDigest = await Sha256().hash(utf8.encode(access.accessGrant));
  final payload = <String, Object?>{
    'typ': 'ccb-relay-phone-proof-v1',
    'schema_version': relayProtocolVersion,
    'host_id': hostId,
    'device_id': deviceId,
    'session_id': sessionId,
    'client_pubkey_b64': clientPublicKeyB64,
    'phone_nonce_b64': phoneNonceB64,
    'grant_sha256_b64': _b64Encode(grantDigest.bytes),
    'aud': relayAudience,
    'nonce_b64': _b64Encode(_randomBytes(18)),
    'iat': now,
    'exp': now + 60,
  };
  final canonicalPayload = _canonicalJson(payload);
  final signingBytes = utf8.encode(
    'ccb-relay-phone-proof-v1\n$canonicalPayload',
  );
  final keyPair = await Ed25519().newKeyPairFromSeed(
    _b64Decode(access.phoneAuthPrivateKeyB64),
  );
  final signature = await Ed25519().sign(signingBytes, keyPair: keyPair);
  return 'ccb-relay-phone-proof-v1.'
      '${_b64Encode(utf8.encode(canonicalPayload))}.'
      '${_b64Encode(signature.bytes)}';
}

String _canonicalJson(Map<String, Object?> value) {
  Object? sortValue(Object? item) {
    if (item is Map) {
      return SplayTreeMap<String, Object?>.from({
        for (final entry in item.entries)
          entry.key.toString(): sortValue(entry.value),
      });
    }
    if (item is Iterable) {
      return [for (final child in item) sortValue(child)];
    }
    return item;
  }

  return jsonEncode(sortValue(value));
}

List<int> _randomBytes(int length) {
  final random = Random.secure();
  return List<int>.generate(length, (_) => random.nextInt(256));
}

String _b64Encode(List<int> value) {
  return base64UrlEncode(value).replaceAll('=', '');
}

Uri _validatedRelayOrigin(
  Uri? uri, {
  required bool allowInsecureLoopbackForTests,
}) {
  if (uri == null) {
    throw ArgumentError.value(uri, 'websocketUrl', 'relay WSS origin required');
  }
  final loopbackTest =
      allowInsecureLoopbackForTests &&
      uri.scheme == 'ws' &&
      _isLoopbackHost(uri.host);
  if (uri.scheme != 'wss' && !loopbackTest) {
    throw ArgumentError.value(uri, 'websocketUrl', 'relay URL must use WSS');
  }
  if (uri.host.isEmpty || uri.hasQuery || uri.hasFragment) {
    throw ArgumentError.value(
      uri,
      'websocketUrl',
      'relay URL must be an origin',
    );
  }
  if (uri.path.isNotEmpty && uri.path != '/') {
    throw ArgumentError.value(
      uri,
      'websocketUrl',
      'relay URL must be an origin',
    );
  }
  return uri.replace(path: '', query: null, fragment: null);
}

bool _isLoopbackHost(String host) {
  return host == 'localhost' ||
      host == '127.0.0.1' ||
      host == '::1' ||
      host.startsWith('127.');
}

Future<String> _publicKeyB64(List<int> privateKeyBytes) async {
  final keyPair = await X25519().newKeyPairFromSeed(privateKeyBytes);
  final publicKey = await keyPair.extractPublicKey();
  return base64UrlEncode(publicKey.bytes).replaceAll('=', '');
}

List<int> _b64Decode(String value) {
  final text = value.trim();
  return base64Url.decode(
    text.padRight(text.length + ((4 - text.length % 4) % 4), '='),
  );
}

DateTime _dateTime(Object? value) {
  final parsed = DateTime.tryParse((value ?? '').toString());
  return parsed?.toUtc() ?? DateTime.fromMillisecondsSinceEpoch(0, isUtc: true);
}

int? _int(Object? value) {
  if (value is int) {
    return value;
  }
  return int.tryParse((value ?? '').toString());
}

Set<String> _stringSet(Object? value) {
  if (value is Iterable) {
    return {for (final item in value) item.toString()};
  }
  return const {};
}

String _text(Object? value, {String fallback = ''}) {
  final text = (value ?? '').toString().trim();
  return text.isEmpty ? fallback : text;
}

String _requiredText(Object? value, String name) {
  final text = _text(value);
  if (text.isEmpty) {
    throw FormatException('relay response missing text field: $name');
  }
  return text;
}

Map<String, Object?> _relayResultBody(
  Map<String, Object?> result, {
  required String operation,
}) {
  if (result['ok'] != true) {
    throw RelayGatewayException(
      _text(result['error'], fallback: 'relay $operation failed'),
      statusCode: _int(result['status']),
    );
  }
  return _objectMap(result['body'], '$operation body');
}

bool _hasText(String? value) => value != null && value.trim().isNotEmpty;

Map<String, Object?> _objectMap(Object? value, String name) {
  if (value is Map) {
    return {
      for (final entry in value.entries) entry.key.toString(): entry.value,
    };
  }
  throw FormatException('relay response missing object field: $name');
}

GatewayTerminalHandle _terminalHandle(Map<String, Object?> json) {
  final summary = _objectMap(json['target_summary'], 'target_summary');
  return GatewayTerminalHandle(
    terminalId: _requiredText(json['terminal_id'], 'terminal_id'),
    terminalToken: _requiredText(json['terminal_token'], 'terminal_token'),
    expiresAt: _requiredDateTime(json['expires_at'], 'expires_at'),
    websocketUrl: Uri.parse(
      _requiredText(json['websocket_url'], 'websocket_url'),
    ),
    targetEpoch: _requiredInt(json['target_epoch'], 'target_epoch'),
    targetSummary: GatewayTerminalTargetSummary(
      projectId: _requiredText(
        summary['project_id'],
        'target_summary.project_id',
      ),
      agent: _optionalText(summary['agent']),
      window: _optionalText(summary['window']),
    ),
  );
}

DateTime _requiredDateTime(Object? value, String name) {
  final parsed = DateTime.tryParse((value ?? '').toString());
  if (parsed == null) {
    throw FormatException('relay response missing datetime field: $name');
  }
  return parsed.toUtc();
}

int _requiredInt(Object? value, String name) {
  if (value is int) {
    return value;
  }
  final parsed = int.tryParse((value ?? '').toString());
  if (parsed == null) {
    throw FormatException('relay response missing int field: $name');
  }
  return parsed;
}

String? _optionalText(Object? value) {
  final text = _text(value);
  return text.isEmpty ? null : text;
}
