import 'dart:convert';

import '../../models/ccb_agent.dart';
import '../../models/ccb_agent_conversation.dart';
import '../../models/ccb_conversation_item.dart';
import '../../models/readable_terminal_history.dart';
import '../../transport/http_gateway_transport.dart';
import 'pane_chat_controller.dart';

bool isOmpCollabAgent(CcbAgent? agent) =>
    agent?.provider == 'omp' &&
    agent?.providerControl?.runtimeSource == 'collab';

String messageBodyKey(String body) => body.trim();

bool paneInputMayHaveReachedPane(Object error) {
  return error is PaneChatSendException && error.inputMayHaveReachedPane;
}

CcbConversationDeliveryState paneFailureDeliveryState(Object error) {
  return paneInputMayHaveReachedPane(error)
      ? CcbConversationDeliveryState.unconfirmed
      : CcbConversationDeliveryState.failed;
}

String conversationSignature(CcbAgentConversation? conversation) {
  final items = conversation?.items;
  if (items == null || items.isEmpty) {
    return '';
  }
  return [
    conversation?.ompSessionId ?? '',
    conversation?.ompGeneration ?? '',
    conversation?.namespaceEpoch.toString() ?? '',
    conversation?.branchId ?? '',
    for (final item in items)
      [
        item.id,
        item.kind.wireName,
        item.state?.wireName ?? '',
        item.sentAt?.toUtc().toIso8601String() ?? '',
        item.startedAt?.toUtc().toIso8601String() ?? '',
        item.completedAt?.toUtc().toIso8601String() ?? '',
        item.durationMs?.toString() ?? '',
        item.body.hashCode,
        if (item.nativeTurn != null) jsonEncode(item.nativeTurn!.toJson()),
      ].join(':'),
  ].join('|');
}

CcbConversationItem normalizePaneAttachmentEcho(CcbConversationItem item) {
  if (item.kind != CcbConversationItemKind.userMessage) {
    return item;
  }
  final echo = _parsePaneAttachmentEcho(item.body);
  if (echo == null) {
    return item;
  }
  return item.copyWith(
    body: echo.body,
    attachments: item.attachments.isEmpty ? echo.attachments : null,
  );
}

String terminalHistorySignature(ReadableTerminalHistory? history) {
  if (history == null || history.blocks.isEmpty) {
    return '';
  }
  return [
    history.agentName,
    history.historyScope,
    history.sourcePaneId ?? '',
    history.stale.toString(),
    for (final block in history.blocks)
      [
        block.id,
        block.type,
        block.title ?? '',
        block.text.hashCode,
        block.language ?? '',
        block.status ?? '',
      ].join(':'),
  ].join('|');
}

bool conversationHasTerminalDerivedItems(CcbAgentConversation? conversation) {
  final items = conversation?.items;
  if (items == null) {
    return false;
  }
  return items.any(isTerminalDerivedConversationItem);
}

bool conversationHasProviderNativeItems(CcbAgentConversation? conversation) {
  final items = conversation?.items;
  if (items == null) {
    return false;
  }
  return items.any(isProviderNativeConversationItem);
}

bool isProviderNativeConversationItem(CcbConversationItem item) {
  return item.source?.startsWith('provider_native/') ?? false;
}

bool isTerminalDerivedConversationItem(CcbConversationItem item) {
  final source = item.source ?? '';
  return source.startsWith('terminal input') ||
      source.startsWith('tmux output') ||
      source.startsWith('tmux scrollback') ||
      source.startsWith('terminal journal') ||
      source.startsWith('current screen');
}

bool isTerminalInputConversationItem(CcbConversationItem item) {
  return item.source?.startsWith('terminal input') ?? false;
}

bool isStaleNamespaceEpochError(Object error) {
  if (error is GatewayHttpException) {
    return error.statusCode == 409 &&
        error.message.contains('stale namespace epoch');
  }
  return error.toString().contains('stale namespace epoch');
}

List<CcbConversationItem> pruneLocalMessagesCoveredByRemote({
  required List<CcbConversationItem> localItems,
  required CcbAgentConversation remoteConversation,
}) {
  if (localItems.isEmpty) {
    return localItems;
  }
  final remoteUsers = [
    for (final item in remoteConversation.items)
      if (item.kind == CcbConversationItemKind.userMessage) item,
  ];
  if (remoteUsers.isEmpty) {
    return localItems;
  }
  final consumedRemoteIndexes = <int>{};
  final next = <CcbConversationItem>[];
  for (final item in localItems) {
    if (item.state == CcbConversationDeliveryState.failed) {
      next.add(item);
      continue;
    }
    final remoteIndex = _firstCoveringRemoteUserIndex(
      remoteUsers: remoteUsers,
      consumedRemoteIndexes: consumedRemoteIndexes,
      local: item,
    );
    if (remoteIndex == null) {
      next.add(item);
    } else {
      consumedRemoteIndexes.add(remoteIndex);
    }
  }
  return next;
}

bool remoteConversationCoversUserMessage({
  required CcbAgentConversation remoteConversation,
  required CcbConversationItem message,
}) {
  if (message.kind != CcbConversationItemKind.userMessage) {
    return false;
  }
  for (final remote in remoteConversation.items) {
    if (remoteUserMessageCoversLocalMessage(remote: remote, local: message)) {
      return true;
    }
  }
  return false;
}

bool remoteUserMessageCoversLocalMessage({
  required CcbConversationItem remote,
  required CcbConversationItem local,
}) {
  if (remote.kind != CcbConversationItemKind.userMessage ||
      local.kind != CcbConversationItemKind.userMessage) {
    return false;
  }
  if (remote.id == local.id) {
    return true;
  }
  if (remote.source == 'mobile/collab' ||
      remote.source == 'provider_native/omp' ||
      local.source == 'mobile/collab') {
    return false;
  }
  final bodyKey = messageBodyKey(local.body);
  if (bodyKey.isNotEmpty && messageBodyKey(remote.body) == bodyKey) {
    return true;
  }
  final attachmentKey = _attachmentCoverageKey(local.attachments);
  if (attachmentKey.isEmpty) {
    return false;
  }
  if (bodyKey.isEmpty &&
      _attachmentCoverageKey(remote.attachments) == attachmentKey) {
    return true;
  }
  return remoteUserMessageIsPaneAttachmentEcho(remote: remote, local: local);
}

bool remoteUserMessageIsPaneAttachmentEcho({
  required CcbConversationItem remote,
  required CcbConversationItem local,
}) {
  if (remote.kind != CcbConversationItemKind.userMessage ||
      local.kind != CcbConversationItemKind.userMessage ||
      local.attachments.isEmpty) {
    return false;
  }
  final remoteBody = _normalizedMultilineBody(remote.body);
  if (remoteBody.isEmpty) {
    return false;
  }
  final parsed = _parsePaneAttachmentEcho(remoteBody);
  if (parsed == null || parsed.body != _normalizedMultilineBody(local.body)) {
    return false;
  }
  return local.attachments.every(
    (attachment) => parsed.attachments.any(
      (candidate) =>
          candidate.fileName == attachment.fileName &&
          candidate.mimeType == attachment.mimeType &&
          candidate.sizeBytes == attachment.sizeBytes,
    ),
  );
}

int? _firstCoveringRemoteUserIndex({
  required List<CcbConversationItem> remoteUsers,
  required Set<int> consumedRemoteIndexes,
  required CcbConversationItem local,
}) {
  for (var index = 0; index < remoteUsers.length; index += 1) {
    if (consumedRemoteIndexes.contains(index)) {
      continue;
    }
    if (remoteUserMessageCoversLocalMessage(
      remote: remoteUsers[index],
      local: local,
    )) {
      return index;
    }
  }
  return null;
}

String _normalizedMultilineBody(String body) {
  return body.trim().replaceAll('\r\n', '\n').replaceAll('\r', '\n');
}

_PaneAttachmentEcho? _parsePaneAttachmentEcho(String body) {
  final lines = _normalizedMultilineBody(body).split('\n');
  for (var markerIndex = 0; markerIndex < lines.length; markerIndex += 1) {
    if (lines[markerIndex].trim() != 'Attached files:') {
      continue;
    }
    final attachments = <CcbMessageAttachment>[];
    var allAttachmentLines = true;
    for (final rawLine in lines.skip(markerIndex + 1)) {
      final line = rawLine.trim();
      if (line.isEmpty) {
        continue;
      }
      final attachment = _parsePaneAttachmentLine(line);
      if (attachment == null) {
        allAttachmentLines = false;
        break;
      }
      attachments.add(attachment);
    }
    if (!allAttachmentLines || attachments.isEmpty) {
      continue;
    }
    return _PaneAttachmentEcho(
      body: lines.take(markerIndex).join('\n').trim(),
      attachments: attachments,
    );
  }
  return null;
}

CcbMessageAttachment? _parsePaneAttachmentLine(String line) {
  final match = RegExp(
    r'^-\s+(.+)\s+\(([^,]+),\s+(\d+)\s+bytes,\s+file id:\s+([^)]+)\)$',
  ).firstMatch(line);
  if (match == null) {
    return null;
  }
  final fileReference = match.group(1)?.trim() ?? '';
  final markdownLink = RegExp(
    r'^\[([^\]]+)\]\((.+)\)$',
  ).firstMatch(fileReference);
  final fileName = markdownLink?.group(1)?.trim() ?? fileReference;
  final projectRelativePath = markdownLink?.group(2)?.trim();
  final mimeType = match.group(2)?.trim() ?? '';
  final sizeBytes = int.tryParse(match.group(3) ?? '') ?? 0;
  final fileId = match.group(4)?.trim() ?? '';
  if (fileName.isEmpty || mimeType.isEmpty || fileId.isEmpty) {
    return null;
  }
  return CcbMessageAttachment(
    fileId: fileId,
    fileName: fileName,
    mimeType: mimeType,
    sizeBytes: sizeBytes,
    kind:
        mimeType.startsWith('image/')
            ? CcbMessageAttachmentKind.image
            : CcbMessageAttachmentKind.document,
    state: CcbMessageAttachmentState.available,
    projectRelativePath:
        projectRelativePath?.isNotEmpty == true ? projectRelativePath : null,
  );
}

class _PaneAttachmentEcho {
  const _PaneAttachmentEcho({required this.body, required this.attachments});

  final String body;
  final List<CcbMessageAttachment> attachments;
}

String _attachmentCoverageKey(List<CcbMessageAttachment> attachments) {
  if (attachments.isEmpty) {
    return '';
  }
  return [
    for (final attachment in attachments)
      [
        attachment.fileName,
        attachment.mimeType,
        attachment.sizeBytes,
      ].join(':'),
  ].join('|');
}
