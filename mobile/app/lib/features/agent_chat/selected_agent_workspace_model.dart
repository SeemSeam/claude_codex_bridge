import '../../native_session/native_session_turn.dart';
import '../../models/omp_runtime.dart';
import '../../models/ccb_agent.dart';
import '../../models/ccb_content_item.dart';
import '../../models/ccb_conversation_item.dart';
import '../../models/ccb_project_view.dart';
import '../../models/readable_terminal_history.dart';
import 'agent_chat_controller.dart';
import 'agent_execution_status.dart';

export 'agent_execution_status.dart';

const providerSessionBoundarySource = 'provider_session_boundary';

class SelectedAgentWorkspaceModel {
  const SelectedAgentWorkspaceModel({
    required this.agent,
    this.contentItems = const [],
    this.initialHistory,
    required this.timelineItems,
    this.commsItems = const [],
    required this.isLoadingConversation,
    required this.hasOlderConversation,
    required this.expandedItemIds,
    required this.hasNewMessages,
    required this.isSending,
    required this.isAwaitingAgentResponse,
    required this.isComposerCollapsed,
    required this.executionStatus,
    this.workingReplyItemId,
    this.ompRuntime,
    this.pendingUiRequest,
    this.uiRequestScope,
    this.uiRequestAvailable = false,
  });

  final CcbAgent agent;
  final List<CcbContentItem> contentItems;
  final ReadableTerminalHistory? initialHistory;
  final List<CcbConversationItem> timelineItems;
  final List<CcbConversationItem> commsItems;
  final bool isLoadingConversation;
  final bool hasOlderConversation;
  final Set<String> expandedItemIds;
  final bool hasNewMessages;
  final bool isSending;
  final bool isAwaitingAgentResponse;
  final bool isComposerCollapsed;
  final AgentExecutionStatus? executionStatus;
  final String? workingReplyItemId;
  final OmpRuntime? ompRuntime;
  final OmpPendingUiRequest? pendingUiRequest;
  final String? uiRequestScope;
  final bool uiRequestAvailable;
}

SelectedAgentWorkspaceModel selectedAgentWorkspaceModel({
  required CcbProjectView view,
  required CcbAgent agent,
  required AgentChatController chatController,
  required bool isAwaitingAgentResponse,
  bool hasLocalExecutionException = false,
}) {
  final remoteConversation = chatController.remoteConversationFor(agent.name);
  final sameScope = remoteConversation != null &&
      remoteConversation.projectId == view.project.id &&
      remoteConversation.namespaceEpoch == view.namespaceEpoch &&
      remoteConversation.agentName == agent.name &&
      (agent.providerControl?.sessionId == null ||
       remoteConversation.ompSessionId == agent.providerControl!.sessionId);
  final conversationRuntime = sameScope ? remoteConversation.ompRuntime : null;
  final agentRuntime = agent.ompRuntime;
  final agentObserved = agentRuntime?.statusBar?.observedAt ?? agentRuntime?.observedAt;
  final conversationObserved = conversationRuntime?.statusBar?.observedAt ?? conversationRuntime?.observedAt;
  final newerAgentRuntime = agentObserved != null &&
      (conversationObserved == null || agentObserved.isAfter(conversationObserved));
  final observedRuntime = newerAgentRuntime ? agentRuntime : conversationRuntime ?? agentRuntime;
  // Native status timestamps may not advance when an interactive prompt ends.
  // Prompt freshness follows the enclosing runtime observation instead.
  final agentRequestObserved = agentRuntime?.observedAt;
  final conversationRequestObserved = conversationRuntime?.observedAt;
  final pendingRuntime = agentRequestObserved != null &&
      (conversationRequestObserved == null || agentRequestObserved.isAfter(conversationRequestObserved))
      ? agentRuntime : conversationRuntime ?? agentRuntime;
  final runtime = isOmpHostUnavailable(chatController.conversationErrorFor(agent.name))
      ? observedRuntime?.disconnected() : observedRuntime;
  final isLoadingConversation = chatController.isLoadingConversation(
    agent.name,
  );
  final executionStatus = agentExecutionStatus(
    agent: agent,
    isAwaitingAgentResponse: isAwaitingAgentResponse,
    hasLocalExecutionException: hasLocalExecutionException,
    ompRuntime: runtime,
  );
  final rawTimelineItems = [
    if (remoteConversation != null)
      for (final item in remoteConversation.items)
        if (_isDefaultChatRemoteItem(item)) item,
    ...chatController.localMessagesFor(agent.name),
  ];
  final rawWorkingReplyItemId =
      executionStatus.state == 'working'
          ? selectedAgentWorkingReplyItemId(rawTimelineItems)
          : null;
  final nativeWorkingTurn = runtime == null ? null : rawTimelineItems
      .where((item) => item.id == rawWorkingReplyItemId || _isExecutingUserMessage(item))
      .map((item) => item.nativeTurn)
      .whereType<NativeSessionTurn>()
      .lastOrNull;
  final workingPresentationId = syntheticAgentWorkingConversationItemId(agent.name) +
      (nativeWorkingTurn == null ? '' : '-${nativeWorkingTurn.sessionId}-${nativeWorkingTurn.turnId}');
  if (executionStatus.state == 'working') {
    chatController.claimRemotePresentationId(
      agent.name,
      presentationId: workingPresentationId,
      remoteItemId: rawWorkingReplyItemId,
    );
  }
  final timelineItems = <CcbConversationItem>[];
  String? workingReplyItemId;
  String? previousProviderSessionId;
  if (remoteConversation != null) {
    for (final item in remoteConversation.items) {
      if (!_isDefaultChatRemoteItem(item)) {
        continue;
      }
      final presented = chatController.presentationItemFor(
        agent.name,
        item,
        preferredPresentationId:
            item.id == rawWorkingReplyItemId ? workingPresentationId : null,
      );
      final sessionId = _providerNativeSessionId(presented);
      if (sessionId != null &&
          previousProviderSessionId != null &&
          sessionId != previousProviderSessionId) {
        timelineItems.add(
          providerSessionBoundaryConversationItem(
            agent.name,
            nextItem: presented,
          ),
        );
      }
      timelineItems.add(presented);
      if (sessionId != null) {
        previousProviderSessionId = sessionId;
      }
      if (item.id == rawWorkingReplyItemId) {
        workingReplyItemId = presented.id;
      }
    }
  }
  for (final item in chatController.localMessagesFor(agent.name)) {
    timelineItems.add(item);
    if (item.id == rawWorkingReplyItemId) {
      workingReplyItemId = item.id;
    }
  }
  final visibleTimelineItems =
      workingReplyItemId == null && executionStatus.state == 'working'
          ? [
            ...timelineItems,
            syntheticAgentWorkingConversationItem(
              agent.name,
              startedAt: runtime == null ? _latestUserSentAt(timelineItems) : null,
            ).copyWith(id: workingPresentationId),
          ]
          : timelineItems;
  final visibleWorkingReplyItemId =
      workingReplyItemId ??
      (executionStatus.state == 'working' ? workingPresentationId : null);
  return SelectedAgentWorkspaceModel(
    agent: agent,
    contentItems: view.contentForAgent(agent.name),
    initialHistory: null,
    timelineItems: visibleTimelineItems,
    commsItems: [
      if (remoteConversation != null)
        for (final item in remoteConversation.items)
          if (item.kind == CcbConversationItemKind.commsItem) item,
    ],
    isLoadingConversation: isLoadingConversation,
    hasOlderConversation: chatController.hasOlderConversation(agent.name),
    expandedItemIds: chatController.expandedItemIds(agent.name),
    hasNewMessages: chatController.hasNewMessages(agent.name),
    isSending: chatController.isSubmitting(agent.name),
    isAwaitingAgentResponse: isAwaitingAgentResponse,
    isComposerCollapsed: chatController.isComposerCollapsed(agent.name),
    executionStatus: executionStatus,
    workingReplyItemId: visibleWorkingReplyItemId,
    ompRuntime: runtime,
    pendingUiRequest: pendingRuntime?.pendingUiRequest,
    uiRequestScope: '${remoteConversation?.ompSessionId}:${remoteConversation?.ompGeneration}',
    uiRequestAvailable: sameScope && pendingRuntime?.connected == true && pendingRuntime?.ready == true &&
        remoteConversation.ompSessionId != null && remoteConversation.ompGeneration != null &&
        !isOmpHostUnavailable(chatController.conversationErrorFor(agent.name)),
  );
}

CcbConversationItem providerSessionBoundaryConversationItem(
  String agentName, {
  required CcbConversationItem nextItem,
}) {
  return CcbConversationItem(
    id: 'provider-session-boundary-${nextItem.id}',
    agentName: agentName,
    kind: CcbConversationItemKind.systemNotice,
    title: 'New context',
    body: 'New context',
    source: providerSessionBoundarySource,
    sessionId: nextItem.sessionId,
    sentAt: nextItem.sentAt,
  );
}

bool isProviderSessionBoundaryItem(CcbConversationItem item) =>
    item.kind == CcbConversationItemKind.systemNotice &&
    item.source == providerSessionBoundarySource;

String? _providerNativeSessionId(CcbConversationItem item) {
  final sessionId = item.sessionId?.trim();
  if (sessionId == null || sessionId.isEmpty) {
    return null;
  }
  return (item.source ?? '').startsWith('provider_native/') ? sessionId : null;
}

String syntheticAgentWorkingConversationItemId(String agentName) =>
    'synthetic-working-reply-$agentName';

CcbConversationItem syntheticAgentWorkingConversationItem(
  String agentName, {
  DateTime? startedAt,
}) {
  return CcbConversationItem(
    id: syntheticAgentWorkingConversationItemId(agentName),
    agentName: agentName,
    kind: CcbConversationItemKind.agentReply,
    title: 'Agent reply',
    body: 'Working...',
    source: 'project_view',
    startedAt: startedAt,
  );
}

DateTime? _latestUserSentAt(List<CcbConversationItem> items) {
  for (final item in items.reversed) {
    if (_isExecutingUserMessage(item)) {
      return item.sentAt;
    }
  }
  return null;
}

String? selectedAgentWorkingReplyItemId(List<CcbConversationItem> items) {
  CcbConversationItem? latestUser;
  CcbConversationItem? latestReply;
  var latestUserIndex = -1;
  var latestReplyIndex = -1;
  for (var index = 0; index < items.length; index += 1) {
    final item = items[index];
    if (_isExecutingUserMessage(item)) {
      latestUser = item;
      latestUserIndex = index;
    } else if (item.kind == CcbConversationItemKind.agentReply) {
      latestReply = item;
      latestReplyIndex = index;
    }
  }
  if (latestReply == null) {
    return null;
  }
  if (latestReply.source == 'provider_native/omp' &&
      latestReply.nativeTurn?.status.name == 'running') {
    return latestReply.id;
  }
  final completedNativeCurrentTurn =
      latestReply.completedAt != null &&
      _isProviderNativeReply(latestReply) &&
      latestUserIndex >= 0 &&
      latestReplyIndex > latestUserIndex;
  if (latestReply.completedAt != null && !completedNativeCurrentTurn) {
    return null;
  }
  final replyStartedAt = latestReply.startedAt ?? latestReply.sentAt;
  final userSentAt = latestUser?.sentAt;
  if (replyStartedAt == null) {
    if (latestUser != null) {
      return null;
    }
    return _isCurrentTurnReplyCandidate(latestReply) ? latestReply.id : null;
  }
  if (userSentAt != null && replyStartedAt.isBefore(userSentAt)) {
    return null;
  }
  return _isCurrentTurnReplyCandidate(latestReply) ? latestReply.id : null;
}

bool _isExecutingUserMessage(CcbConversationItem item) =>
    item.kind == CcbConversationItemKind.userMessage &&
    item.state != CcbConversationDeliveryState.queued &&
    (item.source != 'mobile/collab' ||
        item.state == CcbConversationDeliveryState.sent);

bool _isCurrentTurnReplyCandidate(CcbConversationItem item) {
  final source = item.source ?? '';
  return source.isEmpty || source.startsWith('provider_native/');
}

bool _isProviderNativeReply(CcbConversationItem item) =>
    item.source?.startsWith('provider_native/') ?? false;

bool _isDefaultChatRemoteItem(CcbConversationItem item) {
  if (item.kind == CcbConversationItemKind.commsItem) {
    return false;
  }
  final source = item.source ?? '';
  return !source.startsWith('tmux output') &&
      !source.startsWith('terminal ') &&
      !source.startsWith('tmux scrollback');
}
