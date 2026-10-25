import 'dart:convert';

import '../models/ccb_agent_conversation.dart';
import '../models/ccb_project.dart';
import '../models/ccb_project_lifecycle.dart';
import '../models/ccb_project_view.dart';
import '../models/ccb_provider_control.dart';
import '../models/ccb_terminal_target.dart';
import '../models/readable_terminal_history.dart';
import 'route_provider.dart';
import 'terminal_transport.dart';
abstract interface class GatewayUiResponseTransport {
  Future<void> uiResponse({required String projectId, required String agent,
    required int namespaceEpoch, required String expectedOmpSessionId,
    required String expectedOmpGeneration, required int reqId, required String value});
}

abstract interface class GatewayTransport {
  GatewayHostProfile get profile;

  Future<GatewayHealth> health();

  Future<GatewayDevice> device();

  Future<List<CcbProject>> listProjects();

  Future<CcbProjectView> getProjectView(String projectId);

  Future<CcbProjectView> focusAgent({
    required String projectId,
    required String agent,
    required int namespaceEpoch,
  });

  Future<CcbProjectView> focusWindow({
    required String projectId,
    required String window,
    required int namespaceEpoch,
  });

  Future<ReadableTerminalHistory?> getReadableTerminalHistory({
    required String projectId,
    required String agent,
    required int namespaceEpoch,
    int maxLines = 200,
  });

  Future<CcbAgentConversation> getAgentConversation({
    required String projectId,
    required String agent,
    required int namespaceEpoch,
    int limit = 50,
    String? cursor,
  });

  Future<CcbAgentMessageSubmitResult> submitAgentMessage(
    CcbAgentMessageSubmitRequest request,
  );

  Future<CcbProjectLifecycleResult> requestLifecycle({
    required String projectId,
    required CcbLifecycleAction action,
  });

  Future<GatewayTerminalHandle> openTerminal(
    GatewayTerminalOpenRequest request,
  );

  Stream<GatewayTerminalFrame> terminalFrames(
    GatewayTerminalHandle handle, {
    int? resumeCursor,
  });

  Future<void> sendTerminalFrame(
    GatewayTerminalHandle handle,
    GatewayTerminalFrame frame,
  );

  Future<GatewayFileUploadResult> uploadFile({
    required String projectId,
    required String agentName,
    required String fileName,
    required String mimeType,
    required List<int> bytes,
  });

  Future<List<int>> downloadFile({
    required String projectId,
    required String agentName,
    required String fileId,
  });
}

abstract interface class GatewayFilePathUploader {
  Future<GatewayFileUploadResult> uploadFileFromPath({
    required String projectId,
    required String agentName,
    required String fileName,
    required String mimeType,
    required String path,
  });
}

abstract interface class GatewayPresenceTransport {
  Future<void> reportPresence({
    required bool visible,
    String? focusedProjectId,
    String? focusedAgent,
    String? terminalId,
    bool userActivity = false,
  });
}

abstract interface class GatewayProviderControlTransport {
  Future<CcbProviderControlDetails> getAgentProviderControl({
    required String projectId,
    required String agentName,
  });

  Future<CcbProviderAccountUsage> getAgentProviderQuota({
    required String projectId,
    required String agentName,
  });

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
  });
}

abstract interface class GatewayHostTerminalTransport {
  Future<GatewayTerminalHandle> openHostTerminal(
    GatewayHostTerminalOpenRequest request,
  );

  Future<void> terminateHostTerminal({required String clientSessionId});
}

class GatewayFileUploadResult {
  const GatewayFileUploadResult({
    required this.fileId,
    required this.fileName,
    this.mimeType,
    this.sizeBytes,
    this.projectRelativePath,
    this.projectPath,
  });

  final String fileId;
  final String fileName;
  final String? mimeType;
  final int? sizeBytes;
  final String? projectRelativePath;
  final String? projectPath;

  factory GatewayFileUploadResult.fromJson(Map<String, Object?> json) {
    return GatewayFileUploadResult(
      fileId:
          _optionalPayloadText(json['file_id']) ??
          _optionalPayloadText(json['id']) ??
          _requiredPayloadText(json['attachment_id'], 'file_id'),
      fileName:
          _optionalPayloadText(json['file_name']) ??
          _optionalPayloadText(json['filename']) ??
          '',
      mimeType: _optionalPayloadText(json['mime_type']),
      sizeBytes: _optionalPayloadInt(json['size_bytes']),
      projectRelativePath: _optionalPayloadText(json['project_relative_path']),
      projectPath: _optionalPayloadText(json['project_path']),
    );
  }

  Map<String, Object?> toJson() {
    return {
      'file_id': fileId,
      'file_name': fileName,
      if (mimeType != null) 'mime_type': mimeType,
      if (sizeBytes != null) 'size_bytes': sizeBytes,
      if (projectRelativePath != null)
        'project_relative_path': projectRelativePath,
      if (projectPath != null) 'project_path': projectPath,
    };
  }
}

class GatewayHealth {
  const GatewayHealth({
    required this.status,
    required this.serverTime,
    this.capabilities = const {},
  });

  final String status;
  final DateTime serverTime;
  final Set<String> capabilities;

  Map<String, Object?> toJson() {
    return {
      'status': status,
      'server_time': serverTime.toUtc().toIso8601String(),
      'capabilities': capabilities.toList()..sort(),
    };
  }
}

class GatewayDevice {
  const GatewayDevice({
    required this.deviceId,
    required this.projectId,
    required this.scopes,
    required this.routeProvider,
    required this.revoked,
    this.name,
    this.pairingId,
    this.gatewayUrl,
    this.createdAt,
    this.lastSeenAt,
    this.revokedAt,
  });

  final String deviceId;
  final String? name;
  final String projectId;
  final String? pairingId;
  final Set<String> scopes;
  final RouteProviderKind routeProvider;
  final Uri? gatewayUrl;
  final DateTime? createdAt;
  final DateTime? lastSeenAt;
  final bool revoked;
  final DateTime? revokedAt;

  factory GatewayDevice.fromJson(Map<String, Object?> json) {
    return GatewayDevice(
      deviceId: _requiredPayloadText(json['device_id'], 'device_id'),
      name: _optionalPayloadText(json['name']),
      projectId: _requiredPayloadText(json['project_id'], 'project_id'),
      pairingId: _optionalPayloadText(json['pairing_id']),
      scopes: _payloadStringSet(json['scopes']),
      routeProvider: RouteProviderKind.fromWireName(
        _requiredPayloadText(json['route_provider'], 'route_provider'),
      ),
      gatewayUrl: _optionalPayloadUri(json['gateway_url']),
      createdAt: _optionalPayloadDateTime(json['created_at']),
      lastSeenAt: _optionalPayloadDateTime(json['last_seen_at']),
      revoked: _payloadBool(json['revoked']),
      revokedAt: _optionalPayloadDateTime(json['revoked_at']),
    );
  }

  Map<String, Object?> toJson() {
    return {
      'device_id': deviceId,
      if (_hasText(name)) 'name': name,
      'project_id': projectId,
      if (_hasText(pairingId)) 'pairing_id': pairingId,
      'scopes': scopes.toList()..sort(),
      'route_provider': routeProvider.wireName,
      if (gatewayUrl != null) 'gateway_url': gatewayUrl.toString(),
      if (createdAt != null) 'created_at': createdAt!.toUtc().toIso8601String(),
      if (lastSeenAt != null)
        'last_seen_at': lastSeenAt!.toUtc().toIso8601String(),
      'revoked': revoked,
      if (revokedAt != null) 'revoked_at': revokedAt!.toUtc().toIso8601String(),
    };
  }
}

class GatewayTerminalOpenRequest {
  GatewayTerminalOpenRequest({
    required this.target,
    this.geometry = const TerminalGeometry(),
    this.schemaVersion = 1,
  });

  final GatewayTerminalTarget target;
  final TerminalGeometry geometry;
  final int schemaVersion;

  factory GatewayTerminalOpenRequest.fromCcbTarget(
    CcbTerminalTarget target, {
    TerminalGeometry geometry = const TerminalGeometry(),
  }) {
    return GatewayTerminalOpenRequest(
      target: GatewayTerminalTarget.fromCcbTarget(target),
      geometry: geometry,
    );
  }

  Map<String, Object?> toJson() {
    return {
      'schema_version': schemaVersion,
      'project_id': target.projectId,
      'namespace_epoch': target.namespaceEpoch,
      'target': target.toJson(),
      'geometry': {
        'columns': geometry.columns,
        'rows': geometry.rows,
        'pixel_width': geometry.pixelWidth,
        'pixel_height': geometry.pixelHeight,
      },
    };
  }
}

class GatewayHostTerminalOpenRequest {
  const GatewayHostTerminalOpenRequest({
    required this.clientSessionId,
    required this.displayName,
    this.geometry = const TerminalGeometry(),
    this.schemaVersion = 1,
  });

  final String clientSessionId;
  final String displayName;
  final TerminalGeometry geometry;
  final int schemaVersion;

  Map<String, Object?> toJson() {
    return {
      'schema_version': schemaVersion,
      'client_session_id': clientSessionId,
      'display_name': displayName,
      'geometry': {
        'columns': geometry.columns,
        'rows': geometry.rows,
        'pixel_width': geometry.pixelWidth,
        'pixel_height': geometry.pixelHeight,
      },
    };
  }
}

class GatewayTerminalTarget {
  GatewayTerminalTarget({
    required this.projectId,
    required this.namespaceEpoch,
    required this.kind,
    this.agent,
    this.window,
    this.paneId,
  }) {
    if (projectId.trim().isEmpty) {
      throw ArgumentError.value(projectId, 'projectId', 'required');
    }
    if (namespaceEpoch < 0) {
      throw ArgumentError.value(namespaceEpoch, 'namespaceEpoch', 'required');
    }
    switch (kind) {
      case CcbTerminalTargetKind.agent:
        _requireText(agent, 'agent');
      case CcbTerminalTargetKind.windowActivePane:
        _requireText(window, 'window');
      case CcbTerminalTargetKind.paneEvidence:
        if (!_hasText(agent) && !_hasText(window)) {
          throw StateError('pane evidence must include agent or window');
        }
    }
  }

  final String projectId;
  final int namespaceEpoch;
  final CcbTerminalTargetKind kind;
  final String? agent;
  final String? window;
  final String? paneId;

  factory GatewayTerminalTarget.fromCcbTarget(CcbTerminalTarget target) {
    if (!target.canAcceptTerminalInput) {
      throw StateError(
        'gateway terminal target requires stable CCB identity and '
        'terminal_input scope',
      );
    }
    return GatewayTerminalTarget(
      projectId: target.projectId,
      namespaceEpoch: target.namespaceEpoch,
      kind: target.kind,
      agent: target.agent,
      window: target.window,
      paneId: target.paneId,
    );
  }

  Map<String, Object?> toJson() {
    return {
      'kind': kind.wireName,
      if (_hasText(agent)) 'agent': agent,
      if (_hasText(window)) 'window': window,
      if (_hasText(paneId)) 'pane_id': paneId,
    };
  }
}

class GatewayTerminalHandle {
  const GatewayTerminalHandle({
    required this.terminalId,
    required this.terminalToken,
    required this.expiresAt,
    required this.websocketUrl,
    required this.targetEpoch,
    required this.targetSummary,
  });

  final String terminalId;
  final String terminalToken;
  final DateTime expiresAt;
  final Uri websocketUrl;
  final int targetEpoch;
  final GatewayTerminalTargetSummary targetSummary;

  Map<String, Object?> toJson() {
    return {
      'terminal_id': terminalId,
      'terminal_token': terminalToken,
      'expires_at': expiresAt.toUtc().toIso8601String(),
      'websocket_url': websocketUrl.toString(),
      'target_epoch': targetEpoch,
      'target_summary': targetSummary.toJson(),
    };
  }
}

class GatewayTerminalTargetSummary {
  const GatewayTerminalTargetSummary({
    required this.projectId,
    this.agent,
    this.window,
  });

  final String projectId;
  final String? agent;
  final String? window;

  Map<String, Object?> toJson() {
    return {
      'project_id': projectId,
      if (_hasText(agent)) 'agent': agent,
      if (_hasText(window)) 'window': window,
    };
  }
}

class GatewayTerminalFrame {
  const GatewayTerminalFrame._(this.type, this.payload);

  final GatewayTerminalFrameType type;
  final Map<String, Object?> payload;

  factory GatewayTerminalFrame.open({
    required String terminalId,
    required String token,
    int? resumeCursor,
    int? lastInputSequence,
    TerminalViewport? viewport,
  }) {
    return GatewayTerminalFrame._(GatewayTerminalFrameType.open, {
      'terminal_id': terminalId,
      'token': token,
      if (resumeCursor != null) 'resume_cursor': resumeCursor,
      if (lastInputSequence != null) 'last_input_seq': lastInputSequence,
      if (viewport != null) ...{
        'geometry': _terminalGeometryJson(viewport.geometry),
        'resize_policy': viewport.resizePolicy.wireName,
        'geometry_revision': viewport.revision,
      },
    });
  }

  factory GatewayTerminalFrame.input({
    required int sequence,
    required List<int> bytes,
  }) {
    _requirePositiveSequence(sequence);
    return GatewayTerminalFrame._(GatewayTerminalFrameType.input, {
      'seq': sequence,
      'bytes_b64': base64Encode(bytes),
    });
  }

  factory GatewayTerminalFrame.paste({
    required int sequence,
    required String text,
  }) {
    _requirePositiveSequence(sequence);
    return GatewayTerminalFrame._(GatewayTerminalFrameType.paste, {
      'seq': sequence,
      'text': text,
    });
  }

  factory GatewayTerminalFrame.resize(TerminalGeometry geometry) {
    return GatewayTerminalFrame._(GatewayTerminalFrameType.resize, {
      'columns': geometry.columns,
      'rows': geometry.rows,
      'pixel_width': geometry.pixelWidth,
      'pixel_height': geometry.pixelHeight,
    });
  }

  factory GatewayTerminalFrame.geometry(TerminalViewport viewport) {
    return GatewayTerminalFrame._(GatewayTerminalFrameType.geometry, {
      ..._terminalGeometryJson(viewport.geometry),
      'resize_policy': viewport.resizePolicy.wireName,
      'revision': viewport.revision,
    });
  }

  factory GatewayTerminalFrame.output({
    required int sequence,
    required List<int> bytes,
    List<int>? projectionScreenBytes,
    List<int>? projectionHistoryBytes,
    List<int>? projectionHistoryAppendBytes,
    bool projectionHistoryReset = false,
  }) {
    _requirePositiveSequence(sequence);
    return GatewayTerminalFrame._(GatewayTerminalFrameType.output, {
      'seq': sequence,
      'bytes_b64': base64Encode(bytes),
      if (projectionScreenBytes != null) ...{
        'render_mode': 'replace_snapshot',
        'screen_b64': base64Encode(projectionScreenBytes),
        'history_reset': projectionHistoryReset,
      },
      if (projectionHistoryBytes != null)
        'history_b64': base64Encode(projectionHistoryBytes),
      if (projectionHistoryAppendBytes != null)
        'history_append_b64': base64Encode(projectionHistoryAppendBytes),
    });
  }

  factory GatewayTerminalFrame.closed(String reason) {
    _requireText(reason, 'reason');
    return GatewayTerminalFrame._(GatewayTerminalFrameType.closed, {
      'reason': reason,
    });
  }

  factory GatewayTerminalFrame.error(String code) {
    _requireText(code, 'code');
    return GatewayTerminalFrame._(GatewayTerminalFrameType.error, {
      'code': code,
    });
  }

  factory GatewayTerminalFrame.fromJson(Map<String, Object?> json) {
    final type = GatewayTerminalFrameType.fromWireName(
      _requiredJsonText(json['type'], 'type'),
    );
    return switch (type) {
      GatewayTerminalFrameType.open => GatewayTerminalFrame.open(
        terminalId: _requiredJsonText(json['terminal_id'], 'terminal_id'),
        token: _jsonText(json['token']),
        resumeCursor: _jsonOptionalInt(json['resume_cursor']),
        lastInputSequence: _jsonOptionalInt(json['last_input_seq']),
        viewport: _optionalTerminalViewport(
          json,
          geometryKey: 'geometry',
          revisionKey: 'geometry_revision',
        ),
      ),
      GatewayTerminalFrameType.input => GatewayTerminalFrame.input(
        sequence: _requiredJsonInt(json['seq'], 'seq'),
        bytes: base64Decode(_requiredJsonText(json['bytes_b64'], 'bytes_b64')),
      ),
      GatewayTerminalFrameType.paste => GatewayTerminalFrame.paste(
        sequence: _requiredJsonInt(json['seq'], 'seq'),
        text: _requiredJsonText(json['text'], 'text'),
      ),
      GatewayTerminalFrameType.resize => GatewayTerminalFrame.resize(
        TerminalGeometry(
          columns: _requiredJsonInt(json['columns'], 'columns'),
          rows: _requiredJsonInt(json['rows'], 'rows'),
          pixelWidth: _jsonInt(json['pixel_width']),
          pixelHeight: _jsonInt(json['pixel_height']),
        ),
      ),
      GatewayTerminalFrameType.geometry => GatewayTerminalFrame.geometry(
        _requiredTerminalViewport(json),
      ),
      GatewayTerminalFrameType.output => GatewayTerminalFrame.output(
        sequence: _requiredJsonInt(json['seq'], 'seq'),
        bytes: base64Decode(_requiredJsonText(json['bytes_b64'], 'bytes_b64')),
        projectionScreenBytes:
            _jsonText(json['render_mode']) == 'replace_snapshot'
                ? base64Decode(
                  _requiredJsonText(json['screen_b64'], 'screen_b64'),
                )
                : null,
        projectionHistoryBytes: _optionalBase64Bytes(json['history_b64']),
        projectionHistoryAppendBytes: _optionalBase64Bytes(
          json['history_append_b64'],
        ),
        projectionHistoryReset: json['history_reset'] == true,
      ),
      GatewayTerminalFrameType.closed => GatewayTerminalFrame.closed(
        _requiredJsonText(json['reason'], 'reason'),
      ),
      GatewayTerminalFrameType.error => GatewayTerminalFrame.error(
        _requiredJsonText(json['code'], 'code'),
      ),
    };
  }

  Map<String, Object?> toJson() {
    return {'type': type.wireName, ...payload};
  }
}

enum GatewayTerminalFrameType {
  open('open'),
  input('input'),
  paste('paste'),
  resize('resize'),
  geometry('geometry'),
  output('output'),
  closed('closed'),
  error('error');

  const GatewayTerminalFrameType(this.wireName);

  final String wireName;

  static GatewayTerminalFrameType fromWireName(String value) {
    for (final type in values) {
      if (type.wireName == value) {
        return type;
      }
    }
    throw FormatException('unknown gateway terminal frame type: $value');
  }
}

Map<String, Object?> _terminalGeometryJson(TerminalGeometry geometry) {
  return {
    'columns': geometry.columns,
    'rows': geometry.rows,
    'pixel_width': geometry.pixelWidth,
    'pixel_height': geometry.pixelHeight,
  };
}

TerminalViewport? _optionalTerminalViewport(
  Map<String, Object?> json, {
  String? geometryKey,
  String revisionKey = 'revision',
}) {
  final geometryValue = geometryKey == null ? json : json[geometryKey];
  if (geometryValue == null || geometryValue is! Map) {
    return null;
  }
  final geometry = {
    for (final entry in geometryValue.entries)
      entry.key.toString(): entry.value,
  };
  final policyText = _jsonText(json['resize_policy']).trim();
  if (policyText.isEmpty) {
    return null;
  }
  return TerminalViewport(
    geometry: TerminalGeometry(
      columns: _requiredJsonInt(geometry['columns'], 'columns'),
      rows: _requiredJsonInt(geometry['rows'], 'rows'),
      pixelWidth: _jsonInt(geometry['pixel_width']),
      pixelHeight: _jsonInt(geometry['pixel_height']),
    ),
    resizePolicy: TerminalResizePolicy.fromWireName(policyText),
    revision: _jsonInt(json[revisionKey]),
  );
}

TerminalViewport _requiredTerminalViewport(Map<String, Object?> json) {
  final viewport = _optionalTerminalViewport(json);
  if (viewport == null) {
    throw const FormatException('gateway terminal geometry frame is invalid');
  }
  return viewport;
}

void _requireText(String? value, String name) {
  if (!_hasText(value)) {
    throw ArgumentError.value(value, name, 'required');
  }
}

void _requirePositiveSequence(int sequence) {
  if (sequence < 1) {
    throw ArgumentError.value(sequence, 'sequence', 'must be positive');
  }
}

bool _hasText(String? value) => value != null && value.trim().isNotEmpty;

String _jsonText(Object? value) => (value ?? '').toString();

List<int>? _optionalBase64Bytes(Object? value) {
  final text = _jsonText(value).trim();
  return text.isEmpty ? null : base64Decode(text);
}

String _requiredJsonText(Object? value, String name) {
  final text = _jsonText(value).trim();
  if (text.isEmpty) {
    throw FormatException('gateway terminal frame missing field: $name');
  }
  return text;
}

int _jsonInt(Object? value) {
  if (value is int) {
    return value;
  }
  return int.tryParse((value ?? '').toString()) ?? 0;
}

int? _jsonOptionalInt(Object? value) {
  if (value == null) {
    return null;
  }
  if (value is int) {
    return value;
  }
  return int.tryParse(value.toString());
}

int _requiredJsonInt(Object? value, String name) {
  final parsed = value is int ? value : int.tryParse((value ?? '').toString());
  if (parsed == null) {
    throw FormatException('gateway terminal frame missing integer: $name');
  }
  return parsed;
}

String _requiredPayloadText(Object? value, String name) {
  final text = _optionalPayloadText(value);
  if (!_hasText(text)) {
    throw FormatException('gateway payload missing text field: $name');
  }
  return text!;
}

String? _optionalPayloadText(Object? value) {
  final text = (value ?? '').toString().trim();
  return text.isEmpty ? null : text;
}

int? _optionalPayloadInt(Object? value) {
  if (value == null) {
    return null;
  }
  if (value is int) {
    return value;
  }
  return int.tryParse(value.toString());
}

Set<String> _payloadStringSet(Object? value) {
  if (value is Iterable) {
    return {for (final item in value) item.toString()};
  }
  return const {};
}

Uri? _optionalPayloadUri(Object? value) {
  final text = _optionalPayloadText(value);
  if (text == null) {
    return null;
  }
  final uri = Uri.tryParse(text);
  if (uri == null || !uri.hasScheme || uri.host.isEmpty) {
    throw FormatException('gateway payload invalid URI: $text');
  }
  return uri;
}

DateTime? _optionalPayloadDateTime(Object? value) {
  final parsed = DateTime.tryParse((value ?? '').toString());
  return parsed?.toUtc();
}

bool _payloadBool(Object? value) {
  if (value is bool) {
    return value;
  }
  return value.toString().toLowerCase() == 'true';
}
