import 'dart:async';
import 'dart:collection';
import 'dart:convert';

import '../models/ccb_agent_conversation.dart';
import '../models/ccb_project.dart';
import '../models/ccb_project_lifecycle.dart';
import '../models/ccb_project_view.dart';
import '../models/ccb_provider_control.dart';
import '../models/readable_terminal_history.dart';
import 'gateway_transport.dart';
import 'relay_crypto.dart';
import 'route_provider.dart';

class RelayGatewayEnvelope {
  RelayGatewayEnvelope({
    required this.sessionId,
    required this.sequence,
    required this.operation,
    required this.ciphertextB64,
    required this.nonceB64,
    this.direction,
    this.schemaVersion = relayProtocolVersion,
    this.keyId,
  }) {
    _validate();
  }

  final int schemaVersion;
  final String sessionId;
  final int sequence;
  final String operation;
  final String ciphertextB64;
  final String nonceB64;
  final RelayCryptoDirection? direction;
  final String? keyId;

  factory RelayGatewayEnvelope.fromJson(Map<String, Object?> json) {
    return RelayGatewayEnvelope(
      schemaVersion: _int(
        json['schema_version'],
        fallback: relayProtocolVersion,
      ),
      sessionId: _requiredText(json['session_id'], 'session_id'),
      sequence: _requiredSequence(json['seq'], 'seq'),
      operation: _requiredText(json['op'], 'op'),
      direction: _optionalRelayDirection(json['direction']),
      ciphertextB64: _requiredBase64Text(
        json['ciphertext_b64'],
        'ciphertext_b64',
      ),
      nonceB64: _requiredBase64Text(json['nonce_b64'], 'nonce_b64'),
      keyId: _optionalText(json['key_id']),
    );
  }

  Map<String, Object?> toJson() {
    _validate();
    return {
      'schema_version': schemaVersion,
      'session_id': sessionId,
      'seq': sequence,
      if (direction != null) 'direction': direction!.wireName,
      'op': operation,
      'ciphertext_b64': ciphertextB64,
      'nonce_b64': nonceB64,
      if (_hasText(keyId)) 'key_id': keyId,
    };
  }

  void _validate() {
    if (schemaVersion != relayProtocolVersion) {
      throw const FormatException(
        'relay gateway envelope requires v2 schema_version',
      );
    }
    _requiredText(sessionId, 'session_id');
    _requiredSequence(sequence, 'seq');
    _requiredText(operation, 'op');
    _requiredBase64Text(ciphertextB64, 'ciphertext_b64');
    _requiredBase64Text(nonceB64, 'nonce_b64');
  }
}

abstract interface class _RelayGatewayEnvelopeCodec {
  Future<RelayGatewayEnvelope> seal({
    required String sessionId,
    required int sequence,
    required String operation,
    required Map<String, Object?> payload,
  });
}

class _RelayV2AeadEnvelopeCodec implements _RelayGatewayEnvelopeCodec {
  _RelayV2AeadEnvelopeCodec({required RelayCryptoSession session})
    : _session = session;

  final RelayCryptoSession _session;

  @override
  Future<RelayGatewayEnvelope> seal({
    required String sessionId,
    required int sequence,
    required String operation,
    required Map<String, Object?> payload,
  }) async {
    if (sessionId != _session.sessionId) {
      throw const FormatException('relay v2 codec session mismatch');
    }
    final frame = await _session.seal(
      operation: operation,
      plaintext: utf8.encode(jsonEncode(payload)),
    );
    if (frame.sequence != sequence) {
      throw const FormatException('relay v2 codec sequence mismatch');
    }
    return RelayGatewayEnvelope(
      schemaVersion: frame.schemaVersion,
      sessionId: frame.sessionId,
      sequence: frame.sequence,
      operation: frame.operation,
      direction: frame.direction,
      ciphertextB64: frame.ciphertextB64,
      nonceB64: frame.nonceB64,
      keyId: frame.keyId,
    );
  }
}

class RelayGatewayTransport
    implements
        GatewayTransport,
        GatewayUiResponseTransport,
        GatewayProviderControlTransport,
        GatewayHostTerminalTransport {
  RelayGatewayTransport({
    required GatewayTransport inner,
    required this.sessionId,
    required RelayCryptoSession cryptoSession,
  }) : _inner = inner,
       _codec = _RelayV2AeadEnvelopeCodec(session: cryptoSession) {
    if (inner.profile.routeProvider.kind != RouteProviderKind.relay) {
      throw ArgumentError.value(
        inner.profile.routeProvider.kind.wireName,
        'inner.profile.routeProvider.kind',
        'RelayGatewayTransport requires a relay profile',
      );
    }
  }

  final GatewayTransport _inner;
  final _RelayGatewayEnvelopeCodec _codec;
  final String sessionId;
  final _sealedRequests = <RelayGatewayEnvelope>[];
  int _nextSequence = 1;

  List<RelayGatewayEnvelope> get sealedRequests {
    return UnmodifiableListView(_sealedRequests);
  }

  @override
  GatewayHostProfile get profile => _inner.profile;

  @override
  Future<GatewayHealth> health() {
    return _record('health', const {}, _inner.health);
  }

  @override
  Future<GatewayDevice> device() {
    return _record('device', const {}, _inner.device);
  }

  @override
  Future<List<CcbProject>> listProjects() {
    return _record('list_projects', const {}, _inner.listProjects);
  }

  @override
  Future<CcbProjectView> getProjectView(String projectId) {
    return _record('get_project_view', {'project_id': projectId}, () {
      return _inner.getProjectView(projectId);
    });
  }

  @override
  Future<CcbProviderControlDetails> getAgentProviderControl({
    required String projectId,
    required String agentName,
  }) {
    final inner = _inner;
    if (inner is! GatewayProviderControlTransport) {
      throw UnsupportedError('relay gateway does not support provider control');
    }
    return _record(
      'get_agent_provider_control',
      {'project_id': projectId, 'agent': agentName},
      () => (inner as GatewayProviderControlTransport).getAgentProviderControl(
        projectId: projectId,
        agentName: agentName,
      ),
    );
  }

  @override
  Future<CcbProviderAccountUsage> getAgentProviderQuota({
    required String projectId,
    required String agentName,
  }) {
    final inner = _inner;
    if (inner is! GatewayProviderControlTransport) {
      throw UnsupportedError('relay gateway does not support provider quota');
    }
    return _record(
      'get_agent_provider_quota',
      {'project_id': projectId, 'agent': agentName},
      () => (inner as GatewayProviderControlTransport).getAgentProviderQuota(
        projectId: projectId,
        agentName: agentName,
      ),
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
  }) {
    final inner = _inner;
    if (inner is! GatewayProviderControlTransport) {
      throw UnsupportedError('relay gateway does not support provider control');
    }
    return _record(
      'update_agent_provider_settings',
      {
        'project_id': projectId,
        'agent': agentName,
        'model': model,
        if (_hasText(thinking)) 'thinking': thinking,
        'expected_revision': expectedRevision,
        'expected_namespace_epoch': expectedNamespaceEpoch,
        'expected_provider': expectedProvider,
        'expected_runtime_revision': expectedRuntimeRevision,
        'idempotency_key': idempotencyKey,
      },
      () => (inner as GatewayProviderControlTransport)
          .updateAgentProviderSettings(
            projectId: projectId,
            agentName: agentName,
            model: model,
            thinking: thinking,
            expectedRevision: expectedRevision,
            expectedNamespaceEpoch: expectedNamespaceEpoch,
            expectedProvider: expectedProvider,
            expectedRuntimeRevision: expectedRuntimeRevision,
            idempotencyKey: idempotencyKey,
          ),
    );
  }

  @override
  Future<CcbProjectView> focusAgent({
    required String projectId,
    required String agent,
    required int namespaceEpoch,
  }) {
    return _record(
      'focus_agent',
      {
        'project_id': projectId,
        'agent': agent,
        'namespace_epoch': namespaceEpoch,
      },
      () {
        return _inner.focusAgent(
          projectId: projectId,
          agent: agent,
          namespaceEpoch: namespaceEpoch,
        );
      },
    );
  }

  @override
  Future<CcbProjectView> focusWindow({
    required String projectId,
    required String window,
    required int namespaceEpoch,
  }) {
    return _record(
      'focus_window',
      {
        'project_id': projectId,
        'window': window,
        'namespace_epoch': namespaceEpoch,
      },
      () {
        return _inner.focusWindow(
          projectId: projectId,
          window: window,
          namespaceEpoch: namespaceEpoch,
        );
      },
    );
  }

  @override
  Future<ReadableTerminalHistory?> getReadableTerminalHistory({
    required String projectId,
    required String agent,
    required int namespaceEpoch,
    int maxLines = 200,
  }) {
    return _record(
      'terminal_history',
      {
        'project_id': projectId,
        'agent': agent,
        'namespace_epoch': namespaceEpoch,
        'max_lines': maxLines,
      },
      () {
        return _inner.getReadableTerminalHistory(
          projectId: projectId,
          agent: agent,
          namespaceEpoch: namespaceEpoch,
          maxLines: maxLines,
        );
      },
    );
  }

  @override
  Future<CcbAgentConversation> getAgentConversation({
    required String projectId,
    required String agent,
    required int namespaceEpoch,
    int limit = 50,
    String? cursor,
  }) {
    return _record(
      'agent_conversation',
      {
        'project_id': projectId,
        'agent': agent,
        'namespace_epoch': namespaceEpoch,
        'limit': limit,
        if (_hasText(cursor)) 'cursor': cursor,
      },
      () {
        return _inner.getAgentConversation(
          projectId: projectId,
          agent: agent,
          namespaceEpoch: namespaceEpoch,
          limit: limit,
          cursor: cursor,
        );
      },
    );
  }

  @override
  Future<void> uiResponse({required String projectId, required String agent,
    required int namespaceEpoch, required String expectedOmpSessionId,
    required String expectedOmpGeneration, required int reqId, required String value}) {
    final inner = _inner;
    if (inner is! GatewayUiResponseTransport) throw UnsupportedError('UI responses are unavailable');
    return _record('ui_response', {
      'project_id': projectId, 'agent': agent, 'namespace_epoch': namespaceEpoch,
      'expected_omp_session_id': expectedOmpSessionId, 'expected_omp_generation': expectedOmpGeneration,
      'req_id': reqId, 'value': value,
    }, () => (inner as GatewayUiResponseTransport).uiResponse(projectId: projectId, agent: agent,
      namespaceEpoch: namespaceEpoch, expectedOmpSessionId: expectedOmpSessionId,
      expectedOmpGeneration: expectedOmpGeneration, reqId: reqId, value: value));
  }

  @override
  Future<CcbAgentMessageSubmitResult> submitAgentMessage(
    CcbAgentMessageSubmitRequest request,
  ) {
    return _record('submit_agent_message', request.toJson(), () {
      return _inner.submitAgentMessage(request);
    });
  }

  @override
  Future<CcbProjectLifecycleResult> requestLifecycle({
    required String projectId,
    required CcbLifecycleAction action,
  }) {
    return _record(
      'lifecycle',
      {'project_id': projectId, 'action': action.wireName},
      () {
        return _inner.requestLifecycle(projectId: projectId, action: action);
      },
    );
  }

  @override
  Future<GatewayTerminalHandle> openTerminal(
    GatewayTerminalOpenRequest request,
  ) {
    return _record('open_terminal', request.toJson(), () {
      return _inner.openTerminal(request);
    });
  }

  @override
  Future<GatewayTerminalHandle> openHostTerminal(
    GatewayHostTerminalOpenRequest request,
  ) {
    final inner = _inner;
    if (inner is! GatewayHostTerminalTransport) {
      throw StateError('host terminal transport is unavailable');
    }
    final hostTransport = inner as GatewayHostTerminalTransport;
    return _record('open_host_terminal', request.toJson(), () {
      return hostTransport.openHostTerminal(request);
    });
  }

  @override
  Future<void> terminateHostTerminal({required String clientSessionId}) {
    final inner = _inner;
    if (inner is! GatewayHostTerminalTransport) {
      throw StateError('host terminal transport is unavailable');
    }
    final hostTransport = inner as GatewayHostTerminalTransport;
    return _record(
      'terminate_host_terminal',
      {'client_session_id': clientSessionId},
      () =>
          hostTransport.terminateHostTerminal(clientSessionId: clientSessionId),
    );
  }

  @override
  Stream<GatewayTerminalFrame> terminalFrames(
    GatewayTerminalHandle handle, {
    int? resumeCursor,
  }) {
    return Stream.fromFuture(
      _seal('terminal_frames', {
        'terminal_id': handle.terminalId,
        if (resumeCursor != null) 'resume_cursor': resumeCursor,
      }),
    ).asyncExpand((_) {
      return _inner.terminalFrames(handle, resumeCursor: resumeCursor);
    });
  }

  @override
  Future<void> sendTerminalFrame(
    GatewayTerminalHandle handle,
    GatewayTerminalFrame frame,
  ) {
    return _record(
      'send_terminal_frame',
      {'terminal_id': handle.terminalId, 'frame': frame.toJson()},
      () {
        return _inner.sendTerminalFrame(handle, frame);
      },
    );
  }

  @override
  Future<GatewayFileUploadResult> uploadFile({
    required String projectId,
    required String agentName,
    required String fileName,
    required String mimeType,
    required List<int> bytes,
  }) {
    return _record(
      'upload_file',
      {
        'project_id': projectId,
        'agent': agentName,
        'file_name': fileName,
        'mime_type': mimeType,
        'size_bytes': bytes.length,
      },
      () {
        return _inner.uploadFile(
          projectId: projectId,
          agentName: agentName,
          fileName: fileName,
          mimeType: mimeType,
          bytes: bytes,
        );
      },
    );
  }

  @override
  Future<List<int>> downloadFile({
    required String projectId,
    required String agentName,
    required String fileId,
  }) {
    return _record(
      'download_file',
      {'project_id': projectId, 'agent': agentName, 'file_id': fileId},
      () {
        return _inner.downloadFile(
          projectId: projectId,
          agentName: agentName,
          fileId: fileId,
        );
      },
    );
  }

  Future<T> _record<T>(
    String operation,
    Map<String, Object?> payload,
    Future<T> Function() action,
  ) async {
    await _seal(operation, payload);
    return action();
  }

  Future<void> _seal(String operation, Map<String, Object?> payload) async {
    final envelope = await _codec.seal(
      sessionId: sessionId,
      sequence: _nextSequence,
      operation: operation,
      payload: payload,
    );
    _nextSequence += 1;
    _sealedRequests.add(envelope);
  }
}

String _requiredText(Object? value, String name) {
  final text = _optionalText(value);
  if (!_hasText(text)) {
    throw FormatException('relay envelope missing text field: $name');
  }
  return text!;
}

String _requiredBase64Text(Object? value, String name) {
  final text = _requiredText(value, name);
  try {
    base64Url.decode(
      text.padRight(text.length + ((4 - text.length % 4) % 4), '='),
    );
  } on FormatException catch (error) {
    throw FormatException('relay envelope invalid base64 field: $name', error);
  }
  return text;
}

String? _optionalText(Object? value) {
  final text = (value ?? '').toString().trim();
  return text.isEmpty ? null : text;
}

RelayCryptoDirection? _optionalRelayDirection(Object? value) {
  final text = _optionalText(value);
  if (!_hasText(text)) {
    return null;
  }
  return RelayCryptoDirection.fromWireName(text!);
}

int _int(Object? value, {required int fallback}) {
  if (value is int) {
    return value;
  }
  return int.tryParse((value ?? '').toString()) ?? fallback;
}

bool _hasText(String? value) => value != null && value.trim().isNotEmpty;

int _requiredSequence(Object? value, String name) {
  BigInt? parsed;
  if (value is BigInt) {
    parsed = value;
  } else if (value is int) {
    parsed = BigInt.from(value);
  } else {
    parsed = BigInt.tryParse((value ?? '').toString());
  }
  if (parsed == null || parsed < BigInt.one) {
    throw FormatException('relay envelope missing positive integer: $name');
  }
  if (parsed > relayMaxSequence) {
    throw FormatException('relay envelope sequence exceeds uint64: $name');
  }
  final maxDartInt = BigInt.parse('9223372036854775807');
  if (parsed > maxDartInt) {
    throw FormatException(
      'relay envelope sequence exceeds Dart int range: $name',
    );
  }
  return parsed.toInt();
}
