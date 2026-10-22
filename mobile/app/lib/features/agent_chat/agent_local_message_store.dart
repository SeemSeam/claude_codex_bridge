import 'dart:convert';
import 'dart:io';

import 'package:path/path.dart' as p;
import 'package:path_provider/path_provider.dart';

import '../../models/ccb_conversation_item.dart';

class AgentLocalMessageStore {
  AgentLocalMessageStore({Future<File> Function()? fileFactory})
    : _fileFactory = fileFactory ?? _defaultFile;

  final Future<File> Function() _fileFactory;

  static Future<File> _defaultFile() async {
    final dir = await getApplicationDocumentsDirectory();
    return File(p.join(dir.path, 'agent_local_messages.json'));
  }

  Future<List<CcbConversationItem>> load({
    required String projectId,
    required String agentName,
  }) async {
    final data = await _readData();
    final entries = _entries(data);
    final rawItems = entries[_key(projectId, agentName)];
    if (rawItems is! Iterable) {
      return const [];
    }
    return [
      for (final raw in rawItems)
        if (raw is Map)
          _itemFromJson({
            for (final entry in raw.entries) entry.key.toString(): entry.value,
          }),
    ].where(_shouldPersist).toList(growable: false);
  }

  Future<void> save({
    required String projectId,
    required String agentName,
    required List<CcbConversationItem> messages,
  }) async {
    final data = await _readData();
    final entries = _entries(data);
    final key = _key(projectId, agentName);
    final stored = messages.where(_shouldPersist).toList(growable: false);
    if (stored.isEmpty) {
      entries.remove(key);
    } else {
      entries[key] = [for (final message in stored) _itemToJson(message)];
    }
    data['version'] = 1;
    data['entries'] = entries;
    await _writeData(data);
  }

  Future<Map<String, Object?>> _readData() async {
    final file = await _fileFactory();
    if (!await file.exists()) {
      return <String, Object?>{'version': 1, 'entries': <String, Object?>{}};
    }
    try {
      final decoded = jsonDecode(await file.readAsString());
      if (decoded is Map) {
        return {
          for (final entry in decoded.entries)
            entry.key.toString(): entry.value,
        };
      }
    } catch (_) {
      // Corrupt local retry state should not block opening a real project.
    }
    return <String, Object?>{'version': 1, 'entries': <String, Object?>{}};
  }

  Future<void> _writeData(Map<String, Object?> data) async {
    final file = await _fileFactory();
    await file.parent.create(recursive: true);
    final temp = File('${file.path}.tmp');
    await temp.writeAsString(jsonEncode(data));
    await temp.rename(file.path);
  }

  static Map<String, Object?> _entries(Map<String, Object?> data) {
    final raw = data['entries'];
    if (raw is Map) {
      return {
        for (final entry in raw.entries) entry.key.toString(): entry.value,
      };
    }
    final entries = <String, Object?>{};
    data['entries'] = entries;
    return entries;
  }

  static String _key(String projectId, String agentName) {
    return '${Uri.encodeComponent(projectId)}:${Uri.encodeComponent(agentName)}';
  }

  static bool _shouldPersist(CcbConversationItem item) {
    return item.kind == CcbConversationItemKind.userMessage &&
        switch (item.state) {
          CcbConversationDeliveryState.pending ||
          CcbConversationDeliveryState.queued ||
          CcbConversationDeliveryState.failed ||
          CcbConversationDeliveryState.unconfirmed => true,
          _ => false,
        };
  }

  static CcbConversationItem _itemFromJson(Map<String, Object?> json) {
    return CcbConversationItem.fromJson(json);
  }

  static Map<String, Object?> _itemToJson(CcbConversationItem item) {
    return {
      ...item.toJson(),
      if (item.attachments.isNotEmpty)
        'attachments': [
          for (final attachment in item.attachments)
            {
              ...attachment.toJson(),
              if (attachment.localPath?.isNotEmpty == true)
                'local_path': attachment.localPath,
            },
        ],
    };
  }
}
