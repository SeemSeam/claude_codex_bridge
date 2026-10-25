import 'dart:async';
import 'dart:convert';
import 'dart:io';
import 'dart:typed_data';

import '../models/ccb_agent_conversation.dart';
import '../models/ccb_project.dart';
import '../models/ccb_project_lifecycle.dart';
import '../models/ccb_project_view.dart';
import '../models/ccb_provider_control.dart';
import '../models/readable_terminal_history.dart';
import 'gateway_transport.dart';
import 'route_provider.dart';

class GatewayHttpException implements Exception {
  GatewayHttpException(this.uri, this.statusCode, this.message);

  final Uri uri;
  final int statusCode;
  final String message;

  @override
  String toString() {
    return 'GatewayHttpException($statusCode $uri: $message)';
  }
}

class HttpGatewayTransport
    implements
        GatewayTransport,
        GatewayUiResponseTransport,
        GatewayFilePathUploader,
        GatewayPresenceTransport,
        GatewayProviderControlTransport,
        GatewayHostTerminalTransport {
  HttpGatewayTransport({
    required this.profile,
    String? deviceToken,
    HttpClient? httpClient,
    Duration timeout = const Duration(seconds: 5),
    this.terminalPingInterval = const Duration(seconds: 15),
    Duration projectListWarmupRetryDelay = const Duration(milliseconds: 200),
    int projectListWarmupMaxAttempts = 16,
  }) : assert(
         terminalPingInterval == null || terminalPingInterval > Duration.zero,
         'terminalPingInterval must be positive when enabled',
       ),
       _httpClient = httpClient ?? HttpClient(),
       _deviceToken = deviceToken,
       _timeout = timeout,
       _projectListWarmupRetryDelay = projectListWarmupRetryDelay,
       _projectListWarmupMaxAttempts = projectListWarmupMaxAttempts;

  @override
  final GatewayHostProfile profile;

  final HttpClient _httpClient;
  final String? _deviceToken;
  final Duration _timeout;
  final Duration? terminalPingInterval;
  final Duration _projectListWarmupRetryDelay;
  final int _projectListWarmupMaxAttempts;
  final Map<String, _TerminalSocketConnection> _terminalSockets = {};

  Uri get _baseUrl => profile.routeProvider.gatewayUrl;

  @override
  Future<GatewayHealth> health() async {
    final json = await _getJson('/v1/health');
    return GatewayHealth(
      status: _text(json['status'], fallback: 'unknown'),
      serverTime: _dateTime(json['server_time']),
      capabilities: _stringSet(json['capabilities']),
    );
  }

  @override
  Future<GatewayDevice> device() async {
    final json = await _getJson('/v1/devices/me');
    return GatewayDevice.fromJson(_objectMap(json['device'], 'device'));
  }

  @override
  Future<void> reportPresence({
    required bool visible,
    String? focusedProjectId,
    String? focusedAgent,
    String? terminalId,
    bool userActivity = false,
  }) async {
    await _postJson('/v1/devices/me/presence', {
      'visible': visible,
      if (_hasText(focusedProjectId))
        'focused_project_id': focusedProjectId!.trim(),
      if (_hasText(focusedAgent)) 'focused_agent': focusedAgent!.trim(),
      if (_hasText(terminalId)) 'terminal_id': terminalId!.trim(),
      'user_activity': userActivity,
    });
  }

  @override
  Future<List<CcbProject>> listProjects() async {
    final attempts =
        _projectListWarmupMaxAttempts < 1 ? 1 : _projectListWarmupMaxAttempts;
    for (var attempt = 0; attempt < attempts; attempt += 1) {
      final json = await _getJson('/v1/projects');
      final projects = json['projects'];
      if (projects is! Iterable) {
        throw FormatException(
          'gateway projects response missing projects list',
        );
      }
      final parsed = [
        for (final item in projects)
          if (item is Map)
            CcbProject.fromJson({
              for (final entry in item.entries)
                entry.key.toString(): entry.value,
            }),
      ];
      final warming = json['health_warming'] == true;
      if (!warming || attempt == attempts - 1) {
        return parsed;
      }
      await Future<void>.delayed(_projectListWarmupRetryDelay);
    }
    return const <CcbProject>[];
  }

  @override
  Future<CcbProjectView> getProjectView(String projectId) async {
    final encoded = Uri.encodeComponent(projectId);
    final json = await _getJson('/v1/projects/$encoded/view');
    return CcbProjectView.fromProjectViewPayload(json);
  }

  @override
  Future<CcbProviderControlDetails> getAgentProviderControl({
    required String projectId,
    required String agentName,
  }) async {
    final encodedProject = Uri.encodeComponent(projectId);
    final encodedAgent = Uri.encodeComponent(agentName);
    final json = await _getJson(
      '/v1/projects/$encodedProject/agents/$encodedAgent/provider-control',
    );
    return CcbProviderControlDetails.fromJson(json);
  }

  @override
  Future<CcbProviderAccountUsage> getAgentProviderQuota({
    required String projectId,
    required String agentName,
  }) async {
    final encodedProject = Uri.encodeComponent(projectId);
    final encodedAgent = Uri.encodeComponent(agentName);
    final json = await _getJson(
      '/v1/projects/$encodedProject/agents/$encodedAgent/provider-quota',
    );
    return CcbProviderAccountUsage.fromJson(
      _objectMap(json['account_usage'], 'account_usage'),
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
    final encodedProject = Uri.encodeComponent(projectId);
    final encodedAgent = Uri.encodeComponent(agentName);
    final json = await _postJson(
      '/v1/projects/$encodedProject/agents/$encodedAgent/provider-control',
      {
        'model': model,
        if (_hasText(thinking)) 'thinking': thinking,
        'expected_revision': expectedRevision,
        'expected_namespace_epoch': expectedNamespaceEpoch,
        'expected_provider': expectedProvider,
        'expected_runtime_revision': expectedRuntimeRevision,
        'idempotency_key': idempotencyKey,
      },
    );
    return CcbProviderSettingsResult.fromJson(json);
  }

  @override
  Future<CcbProjectView> focusAgent({
    required String projectId,
    required String agent,
    required int namespaceEpoch,
  }) async {
    final encoded = Uri.encodeComponent(projectId);
    final json = await _postJson('/v1/projects/$encoded/focus-agent', {
      'agent': agent,
      'namespace_epoch': namespaceEpoch,
    });
    return CcbProjectView.fromProjectViewPayload(json);
  }

  @override
  Future<CcbProjectView> focusWindow({
    required String projectId,
    required String window,
    required int namespaceEpoch,
  }) async {
    final encoded = Uri.encodeComponent(projectId);
    final json = await _postJson('/v1/projects/$encoded/focus-window', {
      'window': window,
      'namespace_epoch': namespaceEpoch,
    });
    return CcbProjectView.fromProjectViewPayload(json);
  }

  @override
  Future<ReadableTerminalHistory?> getReadableTerminalHistory({
    required String projectId,
    required String agent,
    required int namespaceEpoch,
    int maxLines = 200,
  }) async {
    final encoded = Uri.encodeComponent(projectId);
    final query =
        Uri(
          queryParameters: {
            'agent': agent,
            'namespace_epoch': namespaceEpoch.toString(),
            'max_lines': maxLines.toString(),
          },
        ).query;
    final json = await _getJson(
      '/v1/projects/$encoded/terminal-history?$query',
    );
    final history = _objectMap(json['terminal_history'], 'terminal_history');
    return ReadableTerminalHistory.fromJson(agentName: agent, json: history);
  }

  @override
  Future<CcbAgentConversation> getAgentConversation({
    required String projectId,
    required String agent,
    required int namespaceEpoch,
    int limit = 50,
    String? cursor,
  }) async {
    final encodedProject = Uri.encodeComponent(projectId);
    final encodedAgent = Uri.encodeComponent(agent);
    final query =
        Uri(
          queryParameters: {
            'namespace_epoch': namespaceEpoch.toString(),
            'limit': limit.toString(),
            if (_hasText(cursor)) 'cursor': cursor!,
          },
        ).query;
    final json = await _getJson(
      '/v1/projects/$encodedProject/agents/$encodedAgent/conversation?$query',
    );
    return CcbAgentConversation.fromJson(json);
  }

  @override
  Future<void> uiResponse({required String projectId, required String agent,
    required int namespaceEpoch, required String expectedOmpSessionId,
    required String expectedOmpGeneration, required int reqId, required String value}) async {
    await _postJson('/v1/projects/${Uri.encodeComponent(projectId)}/agents/${Uri.encodeComponent(agent)}/ui-response', {
      'namespace_epoch': namespaceEpoch, 'expected_omp_session_id': expectedOmpSessionId,
      'expected_omp_generation': expectedOmpGeneration, 'req_id': reqId, 'value': value,
    });
  }

  @override
  Future<CcbAgentMessageSubmitResult> submitAgentMessage(
    CcbAgentMessageSubmitRequest request,
  ) async {
    final encodedProject = Uri.encodeComponent(request.projectId);
    final encodedAgent = Uri.encodeComponent(request.agentName);
    final json = await _postJson(
      '/v1/projects/$encodedProject/agents/$encodedAgent/messages',
      request.toJson(),
    );
    return CcbAgentMessageSubmitResult.fromJson(json);
  }

  @override
  Future<CcbProjectLifecycleResult> requestLifecycle({
    required String projectId,
    required CcbLifecycleAction action,
  }) async {
    final encoded = Uri.encodeComponent(projectId);
    final json = await _postJson('/v1/projects/$encoded/lifecycle', {
      'project_id': projectId,
      'action': action.wireName,
    });
    return CcbProjectLifecycleResult.fromJson(json);
  }

  @override
  Future<GatewayTerminalHandle> openTerminal(
    GatewayTerminalOpenRequest request,
  ) async {
    final encoded = Uri.encodeComponent(request.target.projectId);
    final json = await _postJson(
      '/v1/projects/$encoded/terminals',
      request.toJson(),
    );
    return _terminalHandle(json);
  }

  @override
  Future<GatewayTerminalHandle> openHostTerminal(
    GatewayHostTerminalOpenRequest request,
  ) async {
    final json = await _postJson('/v1/terminals', request.toJson());
    return _terminalHandle(json);
  }

  @override
  Future<void> terminateHostTerminal({required String clientSessionId}) async {
    await _postJson('/v1/terminals/terminate', {
      'schema_version': 1,
      'client_session_id': clientSessionId,
    });
  }

  @override
  Stream<GatewayTerminalFrame> terminalFrames(
    GatewayTerminalHandle handle, {
    int? resumeCursor,
  }) {
    late StreamController<GatewayTerminalFrame> controller;
    late _TerminalSocketConnection connection;
    controller = StreamController<GatewayTerminalFrame>(
      onListen: () async {
        try {
          final socketFuture = WebSocket.connect(
            handle.websocketUrl.toString(),
            customClient: _httpClient,
          ).timeout(_timeout);
          connection = _TerminalSocketConnection(socketFuture);
          _terminalSockets[handle.terminalId] = connection;
          final socket = await socketFuture;
          // dart:io closes the WebSocket when a ping is not answered within
          // one interval. That turns silent Wi-Fi half-open connections into
          // the existing terminal reconnect/resume path without replaying
          // input frames.
          socket.pingInterval = terminalPingInterval;
          socket.add(
            jsonEncode(
              GatewayTerminalFrame.open(
                terminalId: handle.terminalId,
                token: handle.terminalToken,
                resumeCursor: resumeCursor,
              ).toJson(),
            ),
          );
          socket.listen(
            (message) {
              try {
                controller.add(_terminalFrameFromMessage(message));
              } catch (error, stackTrace) {
                controller.addError(error, stackTrace);
              }
            },
            onError: controller.addError,
            onDone: () {
              _forgetTerminalSocket(handle.terminalId, connection);
              if (!controller.isClosed) {
                controller.close();
              }
            },
            cancelOnError: false,
          );
        } catch (error, stackTrace) {
          _forgetTerminalSocket(handle.terminalId, connection);
          controller.addError(error, stackTrace);
          await controller.close();
        }
      },
      onCancel: () async {
        _forgetTerminalSocket(handle.terminalId, connection);
        final socket = await connection.socket;
        await socket.close();
      },
    );
    return controller.stream;
  }

  @override
  Future<void> sendTerminalFrame(
    GatewayTerminalHandle handle,
    GatewayTerminalFrame frame,
  ) async {
    final connection = _terminalSockets[handle.terminalId];
    if (connection == null) {
      throw StateError('gateway terminal WebSocket is not connected');
    }
    final socket = await connection.socket.timeout(_timeout);
    socket.add(jsonEncode(frame.toJson()));
  }

  void _forgetTerminalSocket(
    String terminalId,
    _TerminalSocketConnection connection,
  ) {
    if (identical(_terminalSockets[terminalId], connection)) {
      _terminalSockets.remove(terminalId);
    }
  }

  @override
  Future<GatewayFileUploadResult> uploadFile({
    required String projectId,
    required String agentName,
    required String fileName,
    required String mimeType,
    required List<int> bytes,
  }) async {
    return _uploadFileStream(
      projectId: projectId,
      agentName: agentName,
      fileName: fileName,
      mimeType: mimeType,
      contentLength: bytes.length,
      chunks: Stream<List<int>>.value(bytes),
    );
  }

  @override
  Future<GatewayFileUploadResult> uploadFileFromPath({
    required String projectId,
    required String agentName,
    required String fileName,
    required String mimeType,
    required String path,
  }) async {
    final file = File(path);
    return _uploadFileStream(
      projectId: projectId,
      agentName: agentName,
      fileName: fileName,
      mimeType: mimeType,
      contentLength: await file.length(),
      chunks: file.openRead(),
    );
  }

  Future<GatewayFileUploadResult> _uploadFileStream({
    required String projectId,
    required String agentName,
    required String fileName,
    required String mimeType,
    required int contentLength,
    required Stream<List<int>> chunks,
  }) async {
    final encodedProject = Uri.encodeComponent(projectId);
    final encodedAgent = Uri.encodeComponent(agentName);

    final uri = _baseUrl.resolve(
      '/v1/projects/$encodedProject/agents/$encodedAgent/files',
    );
    final request = await _httpClient.postUrl(uri).timeout(_timeout);

    // We send X-Ccb-File-Name URL encoded to handle special chars safely
    request.headers.set('X-Ccb-File-Name', Uri.encodeComponent(fileName));
    _applyHeaders(request, contentType: ContentType.parse(mimeType));

    final transferTimeout = _fileTransferTimeout(contentLength);
    request.contentLength = contentLength;
    await request.addStream(chunks).timeout(transferTimeout);

    final response = await request.close().timeout(transferTimeout);
    final json = await _decodeJsonResponse(uri, response);

    return GatewayFileUploadResult.fromJson(json);
  }

  Duration _fileTransferTimeout(int contentLength) {
    final extraSeconds = (contentLength / (1024 * 1024)).ceil();
    return _timeout + Duration(seconds: extraSeconds);
  }

  @override
  Future<List<int>> downloadFile({
    required String projectId,
    required String agentName,
    required String fileId,
  }) async {
    final encodedProject = Uri.encodeComponent(projectId);
    final encodedAgent = Uri.encodeComponent(agentName);
    final encodedFile = Uri.encodeComponent(fileId);

    final uri = _baseUrl.resolve(
      '/v1/projects/$encodedProject/agents/$encodedAgent/files/$encodedFile',
    );
    final request = await _httpClient.getUrl(uri).timeout(_timeout);

    // Accept anything
    request.headers.set(HttpHeaders.acceptHeader, '*/*');
    if (_hasText(_deviceToken)) {
      request.headers.set(
        HttpHeaders.authorizationHeader,
        'Bearer $_deviceToken',
      );
    }

    final response = await request.close().timeout(_timeout);

    if (response.statusCode < 200 || response.statusCode >= 300) {
      final body = await utf8
          .decodeStream(response)
          .timeout(_timeout)
          .catchError((_) => '');
      throw GatewayHttpException(uri, response.statusCode, body);
    }

    final builder = BytesBuilder();
    await for (final chunk in response.timeout(_timeout)) {
      builder.add(chunk);
    }
    return builder.takeBytes();
  }

  void close({bool force = false}) {
    for (final connection in _terminalSockets.values) {
      connection.socket.then((socket) => socket.close()).ignore();
    }
    _terminalSockets.clear();
    _httpClient.close(force: force);
  }

  Future<Map<String, Object?>> _getJson(String path) async {
    final uri = _baseUrl.resolve(path);
    final request = await _httpClient.getUrl(uri).timeout(_timeout);
    _applyHeaders(request);
    final response = await request.close().timeout(_timeout);
    return _decodeJsonResponse(uri, response);
  }

  Future<Map<String, Object?>> _postJson(
    String path,
    Map<String, Object?> body,
  ) async {
    final uri = _baseUrl.resolve(path);
    final request = await _httpClient.postUrl(uri).timeout(_timeout);
    _applyHeaders(request, contentType: ContentType.json);
    final bodyBytes = utf8.encode(jsonEncode(body));
    request.contentLength = bodyBytes.length;
    request.add(bodyBytes);
    final response = await request.close().timeout(_timeout);
    return _decodeJsonResponse(uri, response);
  }

  Future<Map<String, Object?>> _decodeJsonResponse(
    Uri uri,
    HttpClientResponse response,
  ) async {
    final body = await utf8.decodeStream(response).timeout(_timeout);
    if (response.statusCode < 200 || response.statusCode >= 300) {
      throw GatewayHttpException(uri, response.statusCode, body);
    }
    final decoded = jsonDecode(body);
    if (decoded is Map) {
      return {
        for (final entry in decoded.entries) entry.key.toString(): entry.value,
      };
    }
    throw FormatException('gateway response is not a JSON object: $uri');
  }

  void _applyHeaders(HttpClientRequest request, {ContentType? contentType}) {
    request.headers.set(HttpHeaders.acceptHeader, 'application/json');
    if (contentType != null) {
      request.headers.contentType = contentType;
    }
    if (_hasText(_deviceToken)) {
      request.headers.set(
        HttpHeaders.authorizationHeader,
        'Bearer $_deviceToken',
      );
    }
  }
}

class _TerminalSocketConnection {
  const _TerminalSocketConnection(this.socket);

  final Future<WebSocket> socket;
}

DateTime _dateTime(Object? value) {
  final parsed = DateTime.tryParse((value ?? '').toString());
  return parsed?.toUtc() ?? DateTime.fromMillisecondsSinceEpoch(0, isUtc: true);
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

bool _hasText(String? value) => value != null && value.trim().isNotEmpty;

GatewayTerminalHandle _terminalHandle(Map<String, Object?> json) {
  final summary = _objectMap(json['target_summary'], 'target_summary');
  return GatewayTerminalHandle(
    terminalId: _requiredText(json['terminal_id'], 'terminal_id'),
    terminalToken: _requiredText(json['terminal_token'], 'terminal_token'),
    expiresAt: _requiredDateTime(json['expires_at'], 'expires_at'),
    websocketUrl: _requiredUri(json['websocket_url'], 'websocket_url'),
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

Map<String, Object?> _objectMap(Object? value, String name) {
  if (value is Map) {
    return {
      for (final entry in value.entries) entry.key.toString(): entry.value,
    };
  }
  throw FormatException('gateway response missing object field: $name');
}

String _requiredText(Object? value, String name) {
  final text = _text(value);
  if (text.isEmpty) {
    throw FormatException('gateway response missing text field: $name');
  }
  return text;
}

String? _optionalText(Object? value) {
  final text = _text(value);
  return text.isEmpty ? null : text;
}

DateTime _requiredDateTime(Object? value, String name) {
  final parsed = DateTime.tryParse((value ?? '').toString());
  if (parsed == null) {
    throw FormatException('gateway response missing datetime field: $name');
  }
  return parsed.toUtc();
}

Uri _requiredUri(Object? value, String name) {
  final text = _requiredText(value, name);
  final uri = Uri.tryParse(text);
  if (uri == null || !uri.hasScheme || uri.host.isEmpty) {
    throw FormatException('gateway response invalid uri field: $name');
  }
  return uri;
}

int _requiredInt(Object? value, String name) {
  if (value is int) {
    return value;
  }
  final parsed = int.tryParse((value ?? '').toString());
  if (parsed == null) {
    throw FormatException('gateway response missing int field: $name');
  }
  return parsed;
}

GatewayTerminalFrame _terminalFrameFromMessage(Object? message) {
  final text = switch (message) {
    final String value => value,
    final List<int> value => utf8.decode(value),
    _ =>
      throw FormatException(
        'gateway terminal WebSocket sent unsupported message',
      ),
  };
  final decoded = jsonDecode(text);
  if (decoded is Map) {
    return GatewayTerminalFrame.fromJson({
      for (final entry in decoded.entries) entry.key.toString(): entry.value,
    });
  }
  throw const FormatException(
    'gateway terminal WebSocket message is not JSON object',
  );
}
