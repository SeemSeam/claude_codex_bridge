import 'dart:convert';

const relayInnerProtocolVersion = 1;
const relayStreamMaxMessageBytes = 512 * 1024;
const relayStreamInitialWindowBytes = relayStreamMaxMessageBytes;
const relayStreamMaxWindowBytes = 2 * 1024 * 1024;

enum RelayInnerKind {
  request('request'),
  response('response'),
  streamOpen('stream_open'),
  streamData('stream_data'),
  streamWindow('stream_window'),
  streamClose('stream_close'),
  streamCancel('stream_cancel'),
  error('error');

  const RelayInnerKind(this.wireName);

  final String wireName;

  static RelayInnerKind fromWireName(String value) {
    for (final kind in values) {
      if (kind.wireName == value.trim()) {
        return kind;
      }
    }
    throw const FormatException('relay inner message kind is unsupported');
  }
}

class RelayInnerMessage {
  RelayInnerMessage({
    required this.kind,
    Map<String, Object?> payload = const {},
    this.requestId,
    this.streamId,
    this.operation,
    this.creditBytes,
    this.schemaVersion = relayInnerProtocolVersion,
  }) : payload = Map.unmodifiable(payload) {
    _validate();
  }

  factory RelayInnerMessage.request({
    required String requestId,
    required String operation,
    required Map<String, Object?> payload,
  }) => RelayInnerMessage(
    kind: RelayInnerKind.request,
    requestId: requestId,
    operation: operation,
    payload: payload,
  );

  factory RelayInnerMessage.streamOpen({
    required String streamId,
    required String operation,
    required Map<String, Object?> payload,
    int creditBytes = relayStreamInitialWindowBytes,
  }) => RelayInnerMessage(
    kind: RelayInnerKind.streamOpen,
    streamId: streamId,
    operation: operation,
    creditBytes: creditBytes,
    payload: payload,
  );

  factory RelayInnerMessage.streamData({
    required String streamId,
    required Map<String, Object?> payload,
  }) => RelayInnerMessage(
    kind: RelayInnerKind.streamData,
    streamId: streamId,
    payload: payload,
  );

  factory RelayInnerMessage.streamWindow({
    required String streamId,
    required int creditBytes,
  }) => RelayInnerMessage(
    kind: RelayInnerKind.streamWindow,
    streamId: streamId,
    creditBytes: creditBytes,
  );

  factory RelayInnerMessage.streamCancel(String streamId) =>
      RelayInnerMessage(kind: RelayInnerKind.streamCancel, streamId: streamId);

  factory RelayInnerMessage.fromJson(Map<String, Object?> json) {
    return RelayInnerMessage(
      schemaVersion: _positiveInt(json['schema_version'], 'schema_version'),
      kind: RelayInnerKind.fromWireName(_requiredText(json['kind'], 'kind')),
      requestId: _optionalText(json['request_id']),
      streamId: _optionalText(json['stream_id']),
      operation: _optionalText(json['operation']),
      creditBytes: _optionalPositiveInt(json['credit_bytes'], 'credit_bytes'),
      payload: _objectMap(json['payload'], 'payload'),
    );
  }

  factory RelayInnerMessage.decode(List<int> bytes) {
    if (bytes.length > relayStreamMaxMessageBytes) {
      throw const FormatException('relay inner message is too large');
    }
    final decoded = jsonDecode(utf8.decode(bytes));
    if (decoded is! Map) {
      throw const FormatException('relay inner message is not an object');
    }
    return RelayInnerMessage.fromJson({
      for (final entry in decoded.entries) entry.key.toString(): entry.value,
    });
  }

  final int schemaVersion;
  final RelayInnerKind kind;
  final String? requestId;
  final String? streamId;
  final String? operation;
  final int? creditBytes;
  final Map<String, Object?> payload;

  Map<String, Object?> toJson() {
    _validate();
    return {
      'schema_version': schemaVersion,
      'kind': kind.wireName,
      if (requestId != null) 'request_id': requestId,
      if (streamId != null) 'stream_id': streamId,
      if (operation != null) 'operation': operation,
      if (creditBytes != null) 'credit_bytes': creditBytes,
      'payload': payload,
    };
  }

  List<int> encode() {
    final bytes = utf8.encode(jsonEncode(toJson()));
    if (bytes.length > relayStreamMaxMessageBytes) {
      throw const FormatException('relay inner message is too large');
    }
    return bytes;
  }

  void _validate() {
    if (schemaVersion != relayInnerProtocolVersion) {
      throw const FormatException('relay inner protocol downgrade rejected');
    }
    switch (kind) {
      case RelayInnerKind.request:
      case RelayInnerKind.response:
        _identifier(requestId, 'request_id');
        if (streamId != null || creditBytes != null) {
          throw const FormatException('relay request identity is invalid');
        }
      case RelayInnerKind.streamOpen:
      case RelayInnerKind.streamData:
      case RelayInnerKind.streamWindow:
      case RelayInnerKind.streamClose:
      case RelayInnerKind.streamCancel:
        _identifier(streamId, 'stream_id');
        if (requestId != null) {
          throw const FormatException('relay stream identity is invalid');
        }
      case RelayInnerKind.error:
        if ((requestId == null) == (streamId == null)) {
          throw const FormatException('relay error identity is invalid');
        }
    }
    if (kind == RelayInnerKind.request &&
        !_unaryOperations.contains(operation)) {
      throw const FormatException('relay unary operation is not allowed');
    }
    if (kind == RelayInnerKind.streamOpen) {
      if (!_streamOperations.contains(operation)) {
        throw const FormatException('relay stream operation is not allowed');
      }
      _window(creditBytes);
    } else if (kind == RelayInnerKind.streamWindow) {
      if (operation != null) {
        throw const FormatException('relay stream window is invalid');
      }
      _window(creditBytes);
    } else if (creditBytes != null) {
      throw const FormatException('relay inner credit is invalid');
    } else if (kind != RelayInnerKind.request && operation != null) {
      throw const FormatException('relay inner operation is invalid');
    }
  }
}

int relayInnerPayloadSize(Map<String, Object?> payload) {
  return utf8.encode(jsonEncode(payload)).length;
}

const _unaryOperations = {
  'pair_claim',
  'health',
  'device',
  'list_projects',
  'get_project_view',
  'get_agent_provider_control',
  'get_agent_provider_quota',
  'update_agent_provider_settings',
  'focus_agent',
  'focus_window',
  'terminal_history',
  'agent_conversation',
  'submit_agent_message',
  'ui_response',
  'lifecycle',
  'open_terminal',
  'open_host_terminal',
  'terminate_host_terminal',
};

const _streamOperations = {
  'terminal',
  'notifications',
  'file_upload',
  'file_download',
};
final _identifierPattern = RegExp(r'^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$');

String _identifier(String? value, String name) {
  if (value == null || !_identifierPattern.hasMatch(value)) {
    throw FormatException('relay inner $name is invalid');
  }
  return value;
}

int _window(int? value) {
  if (value == null || value <= 0 || value > relayStreamMaxWindowBytes) {
    throw const FormatException('relay stream window is invalid');
  }
  return value;
}

Map<String, Object?> _objectMap(Object? value, String name) {
  if (value == null) {
    return const {};
  }
  if (value is Map) {
    return {
      for (final entry in value.entries) entry.key.toString(): entry.value,
    };
  }
  throw FormatException('relay inner $name is not an object');
}

String _requiredText(Object? value, String name) {
  final text = (value ?? '').toString().trim();
  if (text.isEmpty) {
    throw FormatException('relay inner $name is missing');
  }
  return text;
}

String? _optionalText(Object? value) {
  final text = (value ?? '').toString().trim();
  return text.isEmpty ? null : text;
}

int _positiveInt(Object? value, String name) {
  final parsed = value is int ? value : int.tryParse((value ?? '').toString());
  if (parsed == null || parsed <= 0) {
    throw FormatException('relay inner $name is invalid');
  }
  return parsed;
}

int? _optionalPositiveInt(Object? value, String name) {
  return value == null ? null : _positiveInt(value, name);
}
