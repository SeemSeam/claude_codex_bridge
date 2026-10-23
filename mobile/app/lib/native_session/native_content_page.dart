/// A bounded semantic fragment of an immutable conversation item.
class NativeContentBlock {
  const NativeContentBlock({
    required this.entryId,
    required this.blockIndex,
    required this.role,
    required this.type,
    required this.text,
    this.name,
    this.callId,
    this.isError,
    this.continuedBefore = false,
    this.continuedAfter = false,
    this.renderPrefix = '',
    this.renderSuffix = '',
  });

  final String entryId;
  final int blockIndex;
  final String role;
  final String type;
  final String text;
  final String? name;
  final String? callId;
  final bool? isError;
  final bool continuedBefore;
  final bool continuedAfter;
  /// Presentation-only fence context; never part of the original source text.
  final String renderPrefix;
  final String renderSuffix;

  factory NativeContentBlock.fromJson(Map<String, Object?> json) {
    final type = json['type'] as String;
    final index = json['block_index'] as int;
    final entryId = json['entry_id'] as String;
    final role = json['role'] as String;
    if (!const {'text', 'reasoning', 'tool_call', 'tool_result'}.contains(type) ||
        index < 0 || entryId.isEmpty ||
        !const {'assistant', 'user', 'tool', 'system', 'developer'}.contains(role)) {
      throw const FormatException('Invalid content block');
    }
    return NativeContentBlock(
      entryId: entryId,
      blockIndex: index,
      role: role,
      type: type,
      text: json['text'] as String,
      name: json['name'] as String?,
      callId: json['call_id'] as String?,
      isError: json['is_error'] as bool?,
      continuedBefore: json['continued_before'] as bool,
      continuedAfter: json['continued_after'] as bool,
      renderPrefix: json['render_prefix'] as String? ?? '',
      renderSuffix: json['render_suffix'] as String? ?? '',
    );
  }

  Map<String, Object?> toJson() => {
    'entry_id': entryId,
    'block_index': blockIndex,
    'role': role,
    'type': type,
    'text': text,
    if (name != null) 'name': name,
    if (callId != null) 'call_id': callId,
    if (isError != null) 'is_error': isError,
    'continued_before': continuedBefore,
    'continued_after': continuedAfter,
    if (renderPrefix.isNotEmpty) 'render_prefix': renderPrefix,
    if (renderSuffix.isNotEmpty) 'render_suffix': renderSuffix,
  };
}

/// One bounded page; source blocks may continue on adjacent pages.
class NativeContentPage {
  const NativeContentPage({
    required this.blocks,
    required this.pageIndex,
    required this.totalBlocks,
    required this.version,
    this.nextCursor,
  });

  final List<NativeContentBlock> blocks;
  final String? nextCursor;
  final int pageIndex;
  final int totalBlocks;
  final String version;

  factory NativeContentPage.fromJson(Map<String, Object?> json) {
    if (json['schema_version'] != 2) {
      throw const FormatException('Unsupported content schema');
    }
    final pageIndex = json['page_index'] as int;
    final totalBlocks = json['total_blocks'] as int;
    final version = json['version'] as String;
    if (pageIndex < 0 || totalBlocks < 0 || version.isEmpty) {
      throw const FormatException('Invalid content page');
    }
    return NativeContentPage(
      blocks: List<NativeContentBlock>.unmodifiable(
        (json['blocks'] as List).map(
          (block) => NativeContentBlock.fromJson(
            Map<String, Object?>.from(block as Map),
          ),
        ),
      ),
      nextCursor: json['next_cursor'] as String?,
      pageIndex: pageIndex,
      totalBlocks: totalBlocks,
      version: version,
    );
  }

  Map<String, Object?> toJson() => {
    'schema_version': 2,
    'blocks': [for (final block in blocks) block.toJson()],
    'next_cursor': nextCursor,
    'page_index': pageIndex,
    'total_blocks': totalBlocks,
    'version': version,
  };
}
