enum NativeSessionStatus { running, completed, failed, aborted, unknown }

enum NativeSessionRole { user, assistant, tool }

enum NativeSessionBlockType { text, reasoning, toolCall, toolResult }

/// Only public, visible native content is represented here. Provider metadata
/// and hidden reasoning signatures are deliberately not retained.
class NativeSessionBlock {
  const NativeSessionBlock({
    required this.type,
    this.text,
    this.callId,
    this.name,
    this.input,
    this.isError,
  });

  final NativeSessionBlockType type;
  final String? text;
  final String? callId;
  final String? name;
  final Object? input;
  final bool? isError;

  factory NativeSessionBlock.fromJson(Map<String, Object?> json) {
    final type = switch (json['type']) {
      'text' => NativeSessionBlockType.text,
      'reasoning' => NativeSessionBlockType.reasoning,
      'tool_call' => NativeSessionBlockType.toolCall,
      'tool_result' => NativeSessionBlockType.toolResult,
      _ => throw FormatException('Unsupported native block type', json['type']),
    };
    return NativeSessionBlock(
      type: type,
      text: json['text'] as String?,
      callId: json['call_id'] as String?,
      name: json['name'] as String?,
      input: json['input'],
      isError: json['is_error'] as bool?,
    );
  }

  Map<String, Object?> toJson() => {
    'type': switch (type) {
      NativeSessionBlockType.toolCall => 'tool_call',
      NativeSessionBlockType.toolResult => 'tool_result',
      _ => type.name,
    },
    if (text != null) 'text': text,
    if (callId != null) 'call_id': callId,
    if (name != null) 'name': name,
    if (input != null) 'input': input,
    if (isError != null) 'is_error': isError,
  };
}

class NativeSessionEntry {
  NativeSessionEntry({
    required this.id,
    required this.role,
    required List<NativeSessionBlock> blocks,
    this.stopReason,
    this.streaming = false,
  }) : blocks = List.unmodifiable(blocks);

  final String id;
  final NativeSessionRole role;
  final List<NativeSessionBlock> blocks;
  final String? stopReason;
  final bool streaming;

  factory NativeSessionEntry.fromJson(Map<String, Object?> json) {
    return NativeSessionEntry(
      id: _identity(json, 'id'),
      role: NativeSessionRole.values.firstWhere(
        (role) => role.name == json['role'],
        orElse:
            () =>
                throw FormatException('Unsupported native role', json['role']),
      ),
      blocks:
          (json['blocks'] as List)
              .map(
                (block) => NativeSessionBlock.fromJson(
                  Map<String, Object?>.from(block as Map),
                ),
              )
              .toList(),
      stopReason: json['stop_reason'] as String?,
      streaming: json['streaming'] == true,
    );
  }

  Map<String, Object?> toJson() => {
    'id': id,
    'role': role.name,
    'blocks': blocks.map((block) => block.toJson()).toList(),
    if (stopReason != null) 'stop_reason': stopReason,
    if (streaming) 'streaming': true,
  };
}

/// A block's native identity and position, including its tool association.
class NativeSessionProcessBlock {
  const NativeSessionProcessBlock({
    required this.entryId,
    required this.blockIndex,
    required this.block,
  });

  final String entryId;
  final int blockIndex;
  final NativeSessionBlock block;
}

class NativeSessionTurn {
  NativeSessionTurn({
    required this.sessionId,
    required this.turnId,
    required this.status,
    required List<NativeSessionEntry> entries,
    this.readCursor,
    this.reason,
    this.retainedInline = false,
  }) : entries = _deduplicate(entries);

  static const schemaVersion = 1;
  final String sessionId;
  final String turnId;
  final NativeSessionStatus status;
  final List<NativeSessionEntry> entries;
  final String? readCursor;
  final String? reason;
  final bool retainedInline;

  factory NativeSessionTurn.fromJson(Map<String, Object?> json) {
    if (json['schema_version'] != schemaVersion) {
      throw FormatException(
        'Unsupported native turn schema',
        json['schema_version'],
      );
    }
    return NativeSessionTurn(
      sessionId: _identity(json, 'session_id'),
      turnId: _identity(json, 'turn_id'),
      status: NativeSessionStatus.values.firstWhere(
        (status) => status.name == json['status'],
        orElse: () => NativeSessionStatus.unknown,
      ),
      entries:
          (json['entries'] as List)
              .map(
                (entry) => NativeSessionEntry.fromJson(
                  Map<String, Object?>.from(entry as Map),
                ),
              )
              .toList(),
      readCursor: json['read_cursor'] as String?,
      reason: json['reason'] as String?,
      retainedInline: json['retained_inline'] == true,
    );
  }

  Map<String, Object?> toJson() => {
    'schema_version': schemaVersion,
    'session_id': sessionId,
    'turn_id': turnId,
    'status': status.name,
    'entries': entries.map((entry) => entry.toJson()).toList(),
    if (readCursor != null) 'read_cursor': readCursor,
    if (reason != null) 'reason': reason,
    if (retainedInline) 'retained_inline': true,
  };

  /// Latest assistant text entry, for callers needing the final native identity.
  /// Rendering uses [answerText], which includes all assistant prose.
  NativeSessionEntry? get answerEntry {
    for (final entry in entries.reversed) {
      if (entry.role == NativeSessionRole.assistant &&
          entry.blocks.any(
            (block) =>
                block.type == NativeSessionBlockType.text &&
                (block.text?.trim().isNotEmpty ?? false),
          )) {
        return entry;
      }
    }
    return null;
  }

  /// Every visible assistant text block in native order. Equal text in distinct
  /// entries is intentional content, not evidence of duplicate delivery.
  String get answerText => [
    for (final entry in entries)
      if (entry.role == NativeSessionRole.assistant)
        for (final block in entry.blocks)
          if (block.type == NativeSessionBlockType.text &&
              (block.text?.isNotEmpty ?? false))
            block.text!,
  ].join('\n\n');

  List<NativeSessionProcessBlock> get processBlocks {
    // Assistant prose is always readable, independently of process disclosure.
    return [
      for (final entry in entries)
        if (entry.role != NativeSessionRole.user)
          for (var index = 0; index < entry.blocks.length; index++)
            if (!(entry.role == NativeSessionRole.assistant &&
                entry.blocks[index].type == NativeSessionBlockType.text))
              NativeSessionProcessBlock(
                entryId: entry.id,
                blockIndex: index,
                block: entry.blocks[index],
              ),
    ];
  }

  /// Match results only to preceding calls with the same native call ID.
  /// Anonymous calls cannot be guessed complete from unrelated tool output.
  bool get hasPendingTools {
    if (retainedInline) return false;
    final pending = <String, int>{};
    var anonymousCalls = 0;
    for (final entry in entries) {
      for (final block in entry.blocks) {
        if (block.type == NativeSessionBlockType.toolCall) {
          final id = block.callId;
          if (id == null || id.isEmpty) {
            anonymousCalls++;
          } else {
            pending.update(id, (count) => count + 1, ifAbsent: () => 1);
          }
        } else if (block.type == NativeSessionBlockType.toolResult) {
          final id = block.callId;
          if (id != null && pending.containsKey(id)) {
            final count = pending[id]!;
            if (count == 1) {
              pending.remove(id);
            } else {
              pending[id] = count - 1;
            }
          }
        }
      }
    }
    return anonymousCalls > 0 || pending.isNotEmpty;
  }

  /// A persisted message's stop reason (including tool_use) never completes
  /// a turn. Even explicit completion cannot hide an unfinished tool call.
  NativeSessionStatus get effectiveStatus {
    if (retainedInline) return status;
    if (status != NativeSessionStatus.completed) return status;
    if (hasPendingTools) return NativeSessionStatus.unknown;
    for (final entry in entries.reversed) {
      if (entry.role != NativeSessionRole.assistant) continue;
      if (entry.stopReason == 'tool_use' ||
          entry.stopReason == 'tool_calls' ||
          entry.blocks.any(
            (block) => block.type == NativeSessionBlockType.toolCall,
          )) {
        return NativeSessionStatus.unknown;
      }
      break;
    }
    return status;
  }
}

String _identity(Map<String, Object?> json, String key) {
  final value = json[key];
  if (value is! String || value.isEmpty) {
    throw FormatException(
      'Native identity must be a nonempty string: $key',
      value,
    );
  }
  return value;
}

List<NativeSessionEntry> _deduplicate(List<NativeSessionEntry> entries) {
  // Map replacement preserves original position while the newest snapshot for
  // an entry ID wins. Identical text in different native entries is retained.
  final byId = <String, NativeSessionEntry>{};
  for (final entry in entries) {
    byId[entry.id] = entry;
  }
  return List.unmodifiable(byId.values);
}
