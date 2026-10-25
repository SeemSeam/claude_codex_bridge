import 'omp_runtime.dart';
import '../native_session/native_content_page.dart';
import 'ccb_conversation_item.dart';

class CcbAgentConversation {
  const CcbAgentConversation({
    required this.projectId,
    required this.agentName,
    required this.namespaceEpoch,
    required this.items,
    this.nextCursor,
    this.generatedAt,
    this.ompSessionId,
    this.ompGeneration,
    this.nativeContent,
    this.branchId,
    this.ompRuntime,
  });

  final String projectId;
  final String agentName;
  final int namespaceEpoch;
  final List<CcbConversationItem> items;
  final String? nextCursor;
  final DateTime? generatedAt;
  final String? ompSessionId;
  final String? ompGeneration;
  final NativeContentPage? nativeContent;
  final String? branchId;
  final OmpRuntime? ompRuntime;

  factory CcbAgentConversation.fromJson(Map<String, Object?> json) {
    final conversation = _map(json['conversation']);
    final source = conversation.isEmpty ? json : conversation;
    return CcbAgentConversation(
      projectId: _text(source['project_id']),
      agentName:
          _optionalText(source['agent']) ??
          _optionalText(source['agent_name']) ??
          '',
      namespaceEpoch: _int(source['namespace_epoch']),
      items: [
        for (final item in _mapList(source['items']))
          CcbConversationItem.fromJson(item),
      ],
      nextCursor: _optionalText(source['next_cursor']),
      generatedAt: _optionalDateTime(source['generated_at']),
      ompSessionId:
          source['transport'] == 'collab'
              ? _optionalText(source['session_id'])
              : null,
      ompGeneration:
          source['transport'] == 'collab'
              ? _optionalText(source['generation']?.toString())
              : null,
      branchId: _optionalText(source['branch_id']),
      ompRuntime: source['omp_runtime'] is Map
          ? OmpRuntime.fromJson(_map(source['omp_runtime']), sessionId: _optionalText(source['session_id'])) : null,
      nativeContent:
          source['native_content'] is Map
              ? NativeContentPage.fromJson(_map(source['native_content']))
              : null,
    );
  }

  Map<String, Object?> toJson() {
    return {
      'project_id': projectId,
      'agent': agentName,
      'namespace_epoch': namespaceEpoch,
      'items': [for (final item in items) item.toJson()],
      if (nextCursor != null) 'next_cursor': nextCursor,
      if (generatedAt != null)
        'generated_at': generatedAt!.toUtc().toIso8601String(),
      if (ompSessionId != null) 'transport': 'collab',
      if (ompSessionId != null) 'session_id': ompSessionId,
      if (ompGeneration != null) 'generation': ompGeneration,
      if (branchId != null) 'branch_id': branchId,
      if (nativeContent != null) 'native_content': nativeContent!.toJson(),
      if (ompRuntime != null) 'omp_runtime': ompRuntime!.toJson(),
    };
  }
}

class CcbAgentMessageSubmitRequest {
  CcbAgentMessageSubmitRequest({
    required this.projectId,
    required this.agentName,
    required this.namespaceEpoch,
    required this.idempotencyKey,
    required this.body,
    this.format = 'markdown',
    this.attachments = const [],
    this.schemaVersion = 1,
    this.expectedOmpSessionId,
    this.expectedOmpGeneration,
  }) {
    _requireText(projectId, 'projectId');
    _requireText(agentName, 'agentName');
    _requireText(idempotencyKey, 'idempotencyKey');
    if (attachments.isEmpty) {
      _requireText(body, 'body');
    }
  }

  final int schemaVersion;
  final String projectId;
  final String agentName;
  final int namespaceEpoch;
  final String idempotencyKey;
  final String body;
  final String format;
  final List<CcbMessageAttachment> attachments;
  final String? expectedOmpSessionId;
  final String? expectedOmpGeneration;

  Map<String, Object?> toJson() {
    return {
      'schema_version': schemaVersion,
      'project_id': projectId,
      'agent': agentName,
      'namespace_epoch': namespaceEpoch,
      'idempotency_key': idempotencyKey,
      'body': body,
      'format': format,
      if (expectedOmpSessionId != null)
        'expected_omp_session_id': expectedOmpSessionId,
      if (expectedOmpGeneration != null)
        'expected_omp_generation': expectedOmpGeneration,
      if (attachments.isNotEmpty)
        'attachments': [for (final a in attachments) a.toSubmitJson()],
    };
  }
}

class CcbAgentMessageSubmitResult {
  const CcbAgentMessageSubmitResult({
    required this.accepted,
    required this.idempotencyKey,
    required this.messageId,
    required this.state,
    this.message,
    this.conversation,
  });

  final bool accepted;
  final String idempotencyKey;
  final String messageId;
  final CcbConversationDeliveryState state;
  final CcbConversationItem? message;
  final CcbAgentConversation? conversation;

  factory CcbAgentMessageSubmitResult.fromJson(Map<String, Object?> json) {
    final submit = _map(json['message_submit']);
    final source = submit.isEmpty ? json : submit;
    final messageJson = _map(source['message']);
    final parsedMessage =
        messageJson.isEmpty ? null : CcbConversationItem.fromJson(messageJson);
    final submitCreatedAt = _optionalDateTime(source['created_at']);
    final message =
        parsedMessage == null ||
                parsedMessage.sentAt != null ||
                submitCreatedAt == null
            ? parsedMessage
            : parsedMessage.copyWith(sentAt: submitCreatedAt);
    final conversationJson = _map(source['conversation']);
    final conversation =
        conversationJson.isEmpty
            ? null
            : CcbAgentConversation.fromJson(conversationJson);
    final accepted = _bool(source['accepted'], fallback: true);
    final state =
        ccbConversationDeliveryStateFromWireName(source['state']) ??
        message?.state ??
        (accepted
            ? CcbConversationDeliveryState.sent
            : CcbConversationDeliveryState.failed);
    return CcbAgentMessageSubmitResult(
      accepted: accepted,
      idempotencyKey:
          _optionalText(source['idempotency_key']) ??
          _optionalText(source['client_message_id']) ??
          _optionalText(message?.id) ??
          '',
      messageId:
          _optionalText(source['message_id']) ??
          _optionalText(message?.id) ??
          '',
      state: state,
      message: message,
      conversation: conversation,
    );
  }

  Map<String, Object?> toJson() {
    return {
      'accepted': accepted,
      'idempotency_key': idempotencyKey,
      'message_id': messageId,
      'state': state.wireName,
      if (message != null) 'message': message!.toJson(),
      if (conversation != null) 'conversation': conversation!.toJson(),
    };
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

List<Map<String, Object?>> _mapList(Object? value) {
  if (value is Iterable) {
    return [for (final item in value) _map(item)];
  }
  return const [];
}

String _text(Object? value, {String fallback = ''}) {
  final text = (value ?? '').toString().trim();
  return text.isEmpty ? fallback : text;
}

String? _optionalText(Object? value) {
  final text = _text(value);
  return text.isEmpty ? null : text;
}

int _int(Object? value) {
  if (value is int) {
    return value;
  }
  return int.tryParse((value ?? '').toString()) ?? 0;
}

bool _bool(Object? value, {required bool fallback}) {
  if (value is bool) {
    return value;
  }
  return switch (_text(value)) {
    'true' => true,
    'false' => false,
    _ => fallback,
  };
}

DateTime? _optionalDateTime(Object? value) {
  final parsed = DateTime.tryParse((value ?? '').toString());
  return parsed?.toUtc();
}

void _requireText(String value, String name) {
  if (value.trim().isEmpty) {
    throw ArgumentError.value(value, name, 'required');
  }
}
