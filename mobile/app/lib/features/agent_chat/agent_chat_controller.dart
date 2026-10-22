import '../../models/ccb_agent_conversation.dart';
import '../../models/ccb_conversation_item.dart';
import '../../models/readable_terminal_history.dart';
import '../../native_session/native_session_turn.dart';
import 'agent_chat_state_helpers.dart';

class AgentChatConversationUpdate {
  const AgentChatConversationUpdate({required this.changed});

  final bool changed;
}

class AgentChatController {
  final Map<String, List<CcbConversationItem>> _localMessages = {};
  final Map<String, CcbAgentConversation> _remoteConversations = {};
  final Map<String, ReadableTerminalHistory> _refreshedTerminalHistories = {};
  final Map<String, String> _conversationErrors = {};
  final Map<String, Set<String>> _expandedConversationItems = {};
  final Set<String> _loadingConversations = {};
  final Set<String> _submittingAgents = {};
  final Set<String> _collapsedComposers = {};
  final Set<String> _agentsWithNewMessages = {};
  final Map<String, String> _remotePresentationIds = {};
  var _messageCounter = 0;

  String nextLocalMessageId(String agentName) {
    return 'local-$agentName-${_messageCounter++}';
  }

  String nextTerminalLiveOutputId(String agentName) {
    return 'terminal-live-output-$agentName-${_messageCounter++}';
  }

  CcbAgentConversation? remoteConversationFor(String agentName) {
    return _remoteConversations[agentName];
  }

  List<CcbConversationItem> localMessagesFor(String agentName) {
    return _localMessages[agentName] ?? const <CcbConversationItem>[];
  }

  ReadableTerminalHistory? refreshedTerminalHistoryFor(String agentName) {
    return _refreshedTerminalHistories[agentName];
  }

  String? conversationErrorFor(String agentName) {
    return _conversationErrors[agentName];
  }

  Set<String> expandedItemIds(String agentName) {
    return _expandedConversationItems[agentName] ?? const <String>{};
  }

  bool isLoadingConversation(String agentName) {
    return _loadingConversations.contains(agentName);
  }

  bool isSubmitting(String agentName) {
    return _submittingAgents.contains(agentName);
  }

  bool isComposerCollapsed(String agentName) {
    return _collapsedComposers.contains(agentName);
  }

  bool hasNewMessages(String agentName) {
    return _agentsWithNewMessages.contains(agentName);
  }

  bool hasOlderConversation(String agentName) {
    return _remoteConversations[agentName]?.nextCursor != null;
  }

  /// Keeps the single working placeholder's element key while its native
  /// current reply arrives and subsequently completes.
  CcbConversationItem presentationItemFor(
    String agentName,
    CcbConversationItem item, {
    String? preferredPresentationId,
  }) {
    if (item.kind != CcbConversationItemKind.agentReply ||
        !(item.source?.startsWith('provider_native/') ?? false)) {
      return item;
    }
    final key = '$agentName\u0000${item.id}';
    final existing = _remotePresentationIds[key];
    if (existing != null) {
      return item.copyWith(id: existing);
    }
    if (preferredPresentationId != null) {
      _rememberRemotePresentationId(key, preferredPresentationId);
      return item.copyWith(id: preferredPresentationId);
    }
    if (item.completedAt == null) {
      final presentationId = 'native-current-reply-$agentName-${item.id}';
      _rememberRemotePresentationId(key, presentationId);
      return item.copyWith(id: presentationId);
    }
    return item;
  }

  void claimRemotePresentationId(
    String agentName, {
    required String presentationId,
    String? remoteItemId,
  }) {
    final agentPrefix = '$agentName\u0000';
    final claimedKey =
        remoteItemId == null ? null : '$agentPrefix$remoteItemId';
    _remotePresentationIds.removeWhere(
      (key, value) =>
          key.startsWith(agentPrefix) &&
          value == presentationId &&
          key != claimedKey,
    );
    if (claimedKey != null) {
      _rememberRemotePresentationId(claimedKey, presentationId);
    }
  }

  void _rememberRemotePresentationId(String key, String presentationId) {
    _remotePresentationIds[key] = presentationId;
    while (_remotePresentationIds.length > 256) {
      _remotePresentationIds.remove(_remotePresentationIds.keys.first);
    }
  }

  String? olderConversationCursor(String agentName) {
    return _remoteConversations[agentName]?.nextCursor;
  }

  void beginLoadingConversation(String agentName) {
    _loadingConversations.add(agentName);
    _conversationErrors.remove(agentName);
  }

  void finishLoadingConversation(String agentName) {
    _loadingConversations.remove(agentName);
  }

  void setConversationError(String agentName, Object error) {
    _conversationErrors[agentName] = error.toString();
  }

  void beginSubmitting(String agentName) {
    _submittingAgents.add(agentName);
  }

  void finishSubmitting(String agentName) {
    _submittingAgents.remove(agentName);
  }

  void collapseComposer(String agentName) {
    _collapsedComposers.add(agentName);
  }

  void expandComposer(String agentName) {
    _collapsedComposers.remove(agentName);
  }

  void toggleExpandedItem(String agentName, String itemId) {
    final expanded = _expandedConversationItems.putIfAbsent(
      agentName,
      () => <String>{},
    );
    if (!expanded.add(itemId)) {
      expanded.remove(itemId);
    }
    if (expanded.isEmpty) {
      _expandedConversationItems.remove(agentName);
    }
  }

  void clearNewMessageFlag(String agentName) {
    _agentsWithNewMessages.remove(agentName);
  }

  void recordTimelineAppendState({
    required String agentName,
    required bool changed,
    required bool shouldScroll,
  }) {
    if (!changed) {
      return;
    }
    if (shouldScroll) {
      _agentsWithNewMessages.remove(agentName);
    } else {
      _agentsWithNewMessages.add(agentName);
    }
  }

  void addLocalMessage(String agentName, CcbConversationItem message) {
    _localMessages.update(
      agentName,
      (items) => [...items, message],
      ifAbsent: () => [message],
    );
    _syncMessageCounter(message.id);
  }

  void restoreLocalMessages(
    String agentName,
    List<CcbConversationItem> messages,
  ) {
    if (messages.isEmpty) {
      _localMessages.remove(agentName);
      return;
    }
    _localMessages[agentName] = List<CcbConversationItem>.unmodifiable(
      messages,
    );
    for (final message in messages) {
      _syncMessageCounter(message.id);
    }
  }

  void updateLocalMessages(
    String agentName,
    List<CcbConversationItem> Function(List<CcbConversationItem> items) update,
  ) {
    final next = update(localMessagesFor(agentName));
    if (next.isEmpty) {
      _localMessages.remove(agentName);
    } else {
      _localMessages[agentName] = next;
    }
  }

  void replaceLocalMessage(
    String agentName,
    String id,
    CcbConversationItem replacement,
  ) {
    updateLocalMessages(agentName, (items) {
      return [for (final item in items) item.id == id ? replacement : item];
    });
  }

  void removeLocalMessage(String agentName, String id) {
    updateLocalMessages(agentName, (items) {
      return [
        for (final item in items)
          if (item.id != id) item,
      ];
    });
  }

  bool setRefreshedTerminalHistory(
    String agentName,
    ReadableTerminalHistory history,
  ) {
    final previousSignature = terminalHistorySignature(
      _refreshedTerminalHistories[agentName],
    );
    final nextSignature = terminalHistorySignature(history);
    if (previousSignature == nextSignature) {
      return false;
    }
    _refreshedTerminalHistories[agentName] = history;
    return true;
  }

  void clearRefreshedTerminalHistories() {
    _refreshedTerminalHistories.clear();
  }

  AgentChatConversationUpdate applyRemoteConversation({
    required String agentName,
    required CcbAgentConversation conversation,
    required bool shouldScroll,
  }) {
    final previous = _remoteConversations[agentName];
    final sameScope =
        previous != null && _sameConversationScope(previous, conversation);
    if (previous != null && !sameScope) {
      _expandedConversationItems.remove(agentName);
      _remotePresentationIds.removeWhere(
        (key, _) => key.startsWith('$agentName\u0000'),
      );
    }
    final incremental =
        sameScope &&
        conversation.ompSessionId != null &&
        conversation.branchId != null;
    final nextConversation = _conversationWithLocalTimingFallback(
      agentName,
      incremental ? _mergeConversation(previous, conversation) : conversation,
    );
    final previousSignature = conversationSignature(
      _remoteConversations[agentName],
    );
    final nextSignature = conversationSignature(nextConversation);
    final changed = previousSignature != nextSignature;
    _remoteConversations[agentName] = nextConversation;
    _conversationErrors.remove(agentName);
    _pruneLocalMessagesCoveredByRemote(agentName, nextConversation);
    recordTimelineAppendState(
      agentName: agentName,
      changed: changed,
      shouldScroll: shouldScroll,
    );
    return AgentChatConversationUpdate(changed: changed);
  }

  AgentChatConversationUpdate prependRemoteConversationPage({
    required String agentName,
    required CcbAgentConversation conversation,
  }) {
    final previous = _remoteConversations[agentName];
    if (previous == null) {
      return applyRemoteConversation(
        agentName: agentName,
        conversation: conversation,
        shouldScroll: false,
      );
    }
    if (!_sameConversationScope(previous, conversation)) {
      // A response to a page request from a superseded session is stale.
      return const AgentChatConversationUpdate(changed: false);
    }
    final previousSignature = conversationSignature(previous);
    final seenIds = <String>{};
    final previousById = {for (final item in previous.items) item.id: item};
    final mergedItems = <CcbConversationItem>[];
    for (final item in [...conversation.items, ...previous.items]) {
      if (seenIds.add(item.id)) {
        mergedItems.add(previousById[item.id] ?? item);
      }
    }
    final merged = CcbAgentConversation(
      projectId: previous.projectId,
      agentName: previous.agentName,
      namespaceEpoch: previous.namespaceEpoch,
      items: mergedItems,
      nextCursor: conversation.nextCursor,
      generatedAt: previous.generatedAt,
      ompSessionId: previous.ompSessionId,
      ompGeneration: previous.ompGeneration,
      nativeContent: previous.nativeContent,
      branchId: previous.branchId,
    );
    final next = _conversationWithLocalTimingFallback(agentName, merged);
    final nextSignature = conversationSignature(next);
    _remoteConversations[agentName] = next;
    _conversationErrors.remove(agentName);
    _pruneLocalMessagesCoveredByRemote(agentName, next);
    return AgentChatConversationUpdate(
      changed: previousSignature != nextSignature,
    );
  }

  bool _sameConversationScope(
    CcbAgentConversation previous,
    CcbAgentConversation next,
  ) =>
      previous.projectId == next.projectId &&
      previous.namespaceEpoch == next.namespaceEpoch &&
      previous.ompSessionId == next.ompSessionId &&
      previous.ompGeneration == next.ompGeneration &&
      previous.branchId == next.branchId;

  CcbAgentConversation _mergeConversation(
    CcbAgentConversation previous,
    CcbAgentConversation next,
  ) {
    final incoming = {for (final item in next.items) item.id: item};
    final items = <CcbConversationItem>[
      for (final item in previous.items)
        incoming.containsKey(item.id)
            ? _mergeNativeItem(item, incoming.remove(item.id)!)
            : item,
      ...incoming.values,
    ];
    return CcbAgentConversation(
      projectId: next.projectId,
      agentName: next.agentName,
      namespaceEpoch: next.namespaceEpoch,
      items: items,
      // Refreshing latest50 must not reset the already consumed page boundary.
      nextCursor: previous.nextCursor,
      generatedAt: next.generatedAt,
      ompSessionId: next.ompSessionId,
      ompGeneration: next.ompGeneration,
      branchId: next.branchId,
      nativeContent: next.nativeContent,
    );
  }

  CcbConversationItem _mergeNativeItem(
    CcbConversationItem previous,
    CcbConversationItem next,
  ) {
    final oldTurn = previous.nativeTurn;
    final newTurn = next.nativeTurn;
    if (oldTurn == null ||
        newTurn == null ||
        oldTurn.sessionId != newTurn.sessionId ||
        oldTurn.turnId != newTurn.turnId) {
      return next;
    }
    if (newTurn.readCursor != null) {
      // Retained content remains reachable, explicitly as an older snapshot,
      // not misrepresented as the current oversized original.
      final retained =
          oldTurn.entries.where((entry) => !entry.streaming).toList();
      if (retained.isEmpty) return next;
      return next.copyWith(
        nativeTurn: NativeSessionTurn(
          sessionId: newTurn.sessionId,
          turnId: newTurn.turnId,
          status: newTurn.status,
          entries: retained,
          readCursor: newTurn.readCursor,
          reason: newTurn.reason,
          retainedInline: true,
        ),
      );
    }
    final entries = <String, NativeSessionEntry>{
      for (final entry in oldTurn.entries)
        if (!entry.streaming) entry.id: entry,
    };
    for (final entry in newTurn.entries) {
      entries[entry.id] = entry;
    }
    return next.copyWith(
      nativeTurn: NativeSessionTurn(
        sessionId: newTurn.sessionId,
        turnId: newTurn.turnId,
        status: newTurn.status,
        entries: entries.values.toList(),
        reason: newTurn.reason,
      ),
    );
  }

  CcbAgentConversation _conversationWithLocalTimingFallback(
    String agentName,
    CcbAgentConversation conversation,
  ) {
    final localItems =
        _localMessages[agentName] ?? const <CcbConversationItem>[];
    final localById = <String, CcbConversationItem>{};
    final localByBody = <String, List<CcbConversationItem>>{};
    final localWithAttachments = <CcbConversationItem>[];
    for (final item in localItems) {
      if (item.kind != CcbConversationItemKind.userMessage ||
          (!_hasConversationTiming(item) && item.attachments.isEmpty)) {
        continue;
      }
      localById[item.id] = item;
      final bodyKey = messageBodyKey(item.body);
      if (bodyKey.isNotEmpty) {
        localByBody.putIfAbsent(bodyKey, () => []).add(item);
      }
      if (item.attachments.isNotEmpty) {
        localWithAttachments.add(item);
      }
    }
    final hasLocalFallback =
        localById.isNotEmpty ||
        localByBody.isNotEmpty ||
        localWithAttachments.isNotEmpty;
    var changed = false;
    final nextItems = <CcbConversationItem>[];
    for (final rawItem in conversation.items) {
      final item = normalizePaneAttachmentEcho(rawItem);
      if (item.body != rawItem.body ||
          item.attachments.length != rawItem.attachments.length) {
        changed = true;
      }
      if (!hasLocalFallback ||
          item.kind != CcbConversationItemKind.userMessage ||
          _hasConversationTiming(item)) {
        nextItems.add(item);
        continue;
      }
      final idFallback = localById[item.id];
      final fallback =
          idFallback ??
          _takeLocalTimingForBody(localByBody, item.body) ??
          _takeLocalAttachmentEchoFallback(localWithAttachments, item);
      if (fallback == null) {
        nextItems.add(item);
        continue;
      }
      _removeLocalTimingForBody(localByBody, fallback);
      _removeLocalAttachmentFallback(localWithAttachments, fallback);
      final useLocalAttachmentPresentation =
          remoteUserMessageIsPaneAttachmentEcho(remote: item, local: fallback);
      changed = true;
      nextItems.add(
        item.copyWith(
          body: useLocalAttachmentPresentation ? fallback.body : null,
          sentAt: fallback.sentAt,
          startedAt: fallback.startedAt,
          completedAt: fallback.completedAt,
          durationMs: fallback.durationMs,
          attachments:
              useLocalAttachmentPresentation ? fallback.attachments : null,
        ),
      );
    }
    if (!changed) {
      return conversation;
    }
    return CcbAgentConversation(
      projectId: conversation.projectId,
      agentName: conversation.agentName,
      namespaceEpoch: conversation.namespaceEpoch,
      items: nextItems,
      nextCursor: conversation.nextCursor,
      generatedAt: conversation.generatedAt,
      ompSessionId: conversation.ompSessionId,
      ompGeneration: conversation.ompGeneration,
      nativeContent: conversation.nativeContent,
      branchId: conversation.branchId,
    );
  }

  CcbConversationItem? _takeLocalTimingForBody(
    Map<String, List<CcbConversationItem>> localByBody,
    String body,
  ) {
    final candidates = localByBody[messageBodyKey(body)];
    if (candidates == null || candidates.isEmpty) {
      return null;
    }
    return candidates.removeAt(0);
  }

  void _removeLocalTimingForBody(
    Map<String, List<CcbConversationItem>> localByBody,
    CcbConversationItem item,
  ) {
    final bodyKey = messageBodyKey(item.body);
    final candidates = localByBody[bodyKey];
    if (candidates == null) {
      return;
    }
    candidates.removeWhere((candidate) => candidate.id == item.id);
    if (candidates.isEmpty) {
      localByBody.remove(bodyKey);
    }
  }

  CcbConversationItem? _takeLocalAttachmentEchoFallback(
    List<CcbConversationItem> localItems,
    CcbConversationItem remote,
  ) {
    final index = localItems.indexWhere(
      (local) =>
          remoteUserMessageIsPaneAttachmentEcho(remote: remote, local: local),
    );
    if (index < 0) {
      return null;
    }
    return localItems.removeAt(index);
  }

  void _removeLocalAttachmentFallback(
    List<CcbConversationItem> localItems,
    CcbConversationItem item,
  ) {
    localItems.removeWhere((local) => local.id == item.id);
  }

  bool _hasConversationTiming(CcbConversationItem item) {
    return item.sentAt != null ||
        item.startedAt != null ||
        item.completedAt != null ||
        item.durationMs != null;
  }

  void _pruneLocalMessagesCoveredByRemote(
    String agentName,
    CcbAgentConversation conversation,
  ) {
    final current = _localMessages[agentName];
    if (current == null || current.isEmpty) {
      return;
    }
    final next = pruneLocalMessagesCoveredByRemote(
      localItems: current,
      remoteConversation: conversation,
    );
    if (next.isEmpty) {
      _localMessages.remove(agentName);
    } else {
      _localMessages[agentName] = next;
    }
  }

  void _syncMessageCounter(String id) {
    final match = RegExp(r'-(\d+)$').firstMatch(id);
    final value = match == null ? null : int.tryParse(match.group(1)!);
    if (value != null && value >= _messageCounter) {
      _messageCounter = value + 1;
    }
  }
}
