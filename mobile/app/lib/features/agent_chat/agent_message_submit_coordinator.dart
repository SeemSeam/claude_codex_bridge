import 'dart:async';
import 'dart:math';

import '../../models/ccb_agent.dart';
import '../../models/ccb_conversation_item.dart';
import '../../models/ccb_project_view.dart';
import '../../repository/mobile_ccb_repository.dart';
import '../../transport/terminal_transport.dart';
import 'agent_chat_controller.dart';
import 'agent_chat_state_helpers.dart';
import 'agent_conversation_loader.dart';
import 'agent_pane_message_submitter.dart';
import 'agent_repository_message_submitter.dart';

typedef AgentMessageSubmitStateMutation = void Function(void Function() update);
typedef AgentMessageSubmitIsMounted = bool Function();
typedef AgentMessageSubmitTimelineNearEnd = bool Function(String agentName);
typedef AgentMessageSubmitTimelineScrollToEnd = void Function(String agentName);
typedef AgentMessageSubmitLoadConversation =
    Future<void> Function(String agentName);
typedef AgentMessageDraftAccepted = void Function();
typedef AgentMessageDeliveryUpdated =
    void Function(CcbConversationItem replacement);

class AgentMessageSubmitCoordinator {
  const AgentMessageSubmitCoordinator({
    required AgentChatController chatController,
    required AgentMessageSubmitIsMounted isMounted,
    required AgentMessageSubmitStateMutation mutateState,
    required AgentMessageSubmitTimelineNearEnd isTimelineNearEnd,
    required AgentMessageSubmitTimelineScrollToEnd scrollTimelineToEnd,
    required AgentMessageSubmitLoadConversation loadConversation,
    AgentPaneMessageSubmitter? paneSubmitter,
  }) : _chatController = chatController,
       _isMounted = isMounted,
       _mutateState = mutateState,
       _isTimelineNearEnd = isTimelineNearEnd,
       _scrollTimelineToEnd = scrollTimelineToEnd,
       _loadConversation = loadConversation,
       _paneSubmitter = paneSubmitter;

  final AgentChatController _chatController;
  final AgentMessageSubmitIsMounted _isMounted;
  final AgentMessageSubmitStateMutation _mutateState;
  final AgentMessageSubmitTimelineNearEnd _isTimelineNearEnd;
  final AgentMessageSubmitTimelineScrollToEnd _scrollTimelineToEnd;
  final AgentMessageSubmitLoadConversation _loadConversation;
  final AgentPaneMessageSubmitter? _paneSubmitter;

  Future<void> send({
    required CcbAgent agent,
    required String body,
    List<CcbMessageAttachment> attachments = const [],
    required CcbProjectView view,
    required MobileCcbRepository repository,
    TerminalTransport? terminalTransport,
    bool usePaneInput = false,
    List<int>? paneSubmitBytes,
    required AgentViewRefresh? refreshView,
    required AgentMessageDraftAccepted onAccepted,
    AgentMessageDeliveryUpdated? onDeliveryUpdated,
  }) async {
    if (_chatController.isSubmitting(agent.name)) {
      return;
    }
    final trimmedBody = body.trim();
    if (trimmedBody.isEmpty && attachments.isEmpty) {
      return;
    }
    final message = CcbConversationItem.userMessage(
      id:
          agent.provider == 'omp'
              ? 'local-${agent.name}-${DateTime.now().microsecondsSinceEpoch}-${Random.secure().nextInt(1 << 32)}'
              : _chatController.nextLocalMessageId(agent.name),
      agentName: agent.name,
      body: trimmedBody,
      attachments: attachments,
      sentAt: DateTime.now().toUtc(),
    );
    const shouldScroll = true;
    _mutateState(() {
      _chatController.addLocalMessage(agent.name, message);
      _chatController.beginSubmitting(agent.name);
      onAccepted();
      _chatController.recordTimelineAppendState(
        agentName: agent.name,
        changed: true,
        shouldScroll: shouldScroll,
      );
    });
    _scrollTimelineToEnd(agent.name);
    try {
      await _submitLocalMessageWithView(
        agent: agent,
        message: message,
        view: view,
        repository: repository,
        terminalTransport: terminalTransport,
        usePaneInput: usePaneInput,
        paneSubmitBytes: paneSubmitBytes,
        refreshView: refreshView,
        onDeliveryUpdated: onDeliveryUpdated,
      );
    } finally {
      if (_isMounted()) {
        _mutateState(() {
          _chatController.finishSubmitting(agent.name);
        });
      }
    }
  }

  Future<void> retry({
    required CcbConversationItem item,
    required CcbProjectView view,
    required MobileCcbRepository repository,
    TerminalTransport? terminalTransport,
    bool usePaneInput = false,
    List<int>? paneSubmitBytes,
    required AgentViewRefresh? refreshView,
    AgentMessageDraftAccepted? onAccepted,
    AgentMessageDeliveryUpdated? onDeliveryUpdated,
  }) async {
    if (_chatController.isSubmitting(item.agentName)) {
      return;
    }
    final agent = view.agentByName(item.agentName);
    if (agent == null) {
      return;
    }
    final pending = item.copyWith(state: CcbConversationDeliveryState.pending);
    _mutateState(() {
      _chatController.beginSubmitting(item.agentName);
      _chatController.replaceLocalMessage(item.agentName, item.id, pending);
      onAccepted?.call();
    });
    try {
      await _submitLocalMessageWithView(
        agent: agent,
        message: pending,
        view: view,
        repository: repository,
        terminalTransport: terminalTransport,
        usePaneInput: usePaneInput,
        paneSubmitBytes: paneSubmitBytes,
        refreshView: refreshView,
        onDeliveryUpdated: onDeliveryUpdated,
      );
    } finally {
      if (_isMounted()) {
        _mutateState(() {
          _chatController.finishSubmitting(item.agentName);
        });
      }
    }
  }

  Future<void> _submitLocalMessageWithView({
    required CcbAgent agent,
    required CcbConversationItem message,
    required CcbProjectView view,
    required MobileCcbRepository repository,
    required TerminalTransport? terminalTransport,
    required bool usePaneInput,
    required List<int>? paneSubmitBytes,
    required AgentViewRefresh? refreshView,
    AgentMessageDeliveryUpdated? onDeliveryUpdated,
  }) async {
    if (!isOmpCollabAgent(agent) && usePaneInput && _paneSubmitter != null) {
      await _submitPaneMessageWithView(
        agent: agent,
        message: message,
        view: view,
        repository: repository,
        terminalTransport: terminalTransport,
        paneSubmitBytes: paneSubmitBytes,
        refreshView: refreshView,
        onDeliveryUpdated: onDeliveryUpdated,
      );
      return;
    }
    await _submitRepositoryMessageWithView(
      agent: agent,
      message: message,
      view: view,
      repository: repository,
      refreshView: refreshView,
      onDeliveryUpdated: onDeliveryUpdated,
    );
  }

  Future<void> _submitPaneMessageWithView({
    required CcbAgent agent,
    required CcbConversationItem message,
    required CcbProjectView view,
    required MobileCcbRepository repository,
    required TerminalTransport? terminalTransport,
    required List<int>? paneSubmitBytes,
    required AgentViewRefresh? refreshView,
    AgentMessageDeliveryUpdated? onDeliveryUpdated,
  }) async {
    if (terminalTransport == null) {
      final outcome = await _paneSubmitter!.submit(
        transport: null,
        agent: agent,
        message: message,
        view: view,
        refreshView: refreshView,
        submitBytes: paneSubmitBytes ?? const [13],
      );
      if (!_isMounted()) {
        return;
      }
      _replaceLocalMessage(agent.name, message.id, outcome.replacement);
      onDeliveryUpdated?.call(outcome.replacement);
      return;
    }
    final repositorySubmitter = AgentRepositoryMessageSubmitter(
      repository: repository,
    );
    List<CcbMessageAttachment> uploadedAttachments;
    try {
      uploadedAttachments = await repositorySubmitter.uploadAttachments(
        agent: agent,
        message: message,
        view: view,
      );
    } catch (error) {
      if (!_isMounted()) {
        return;
      }
      final replacement = _failedMessage(message, error);
      _replaceLocalMessage(agent.name, message.id, replacement);
      onDeliveryUpdated?.call(replacement);
      return;
    }

    final paneMessage = message.copyWith(attachments: uploadedAttachments);
    final paneBody = _paneBodyForMessage(paneMessage);
    final outcome = await _paneSubmitter!.submit(
      transport: terminalTransport,
      agent: agent,
      message: paneMessage,
      view: view,
      refreshView: refreshView,
      paneBody: paneBody,
      submitBytes: paneSubmitBytes ?? const [13],
    );
    if (!_isMounted()) {
      return;
    }
    _replaceLocalMessage(agent.name, message.id, outcome.replacement);
    onDeliveryUpdated?.call(outcome.replacement);
    if (outcome.shouldRefreshTerminalHistory) {
      unawaited(_loadConversation(agent.name));
    }
  }

  Future<void> _submitRepositoryMessageWithView({
    required CcbAgent agent,
    required CcbConversationItem message,
    required CcbProjectView view,
    required MobileCcbRepository repository,
    required AgentViewRefresh? refreshView,
    AgentMessageDeliveryUpdated? onDeliveryUpdated,
  }) async {
    final outcome = await AgentRepositoryMessageSubmitter(
      repository: repository,
    ).submit(
      agent: agent,
      message: message,
      view: view,
      conversation: _chatController.remoteConversationFor(agent.name),
    );
    if (!_isMounted()) {
      return;
    }
    final conversation = outcome.conversation;
    if (conversation != null) {
      final replacement = outcome.replacement;
      if (replacement != null) {
        onDeliveryUpdated?.call(replacement);
      }
      final remoteCoversMessage = remoteConversationCoversUserMessage(
        remoteConversation: conversation,
        message: replacement ?? message,
      );
      final shouldScroll = _isTimelineNearEnd(agent.name);
      var changed = false;
      _mutateState(() {
        final update = _chatController.applyRemoteConversation(
          agentName: agent.name,
          conversation: conversation,
          shouldScroll: shouldScroll,
        );
        changed = update.changed;
        if (remoteCoversMessage) {
          _chatController.removeLocalMessage(agent.name, message.id);
        } else if (replacement != null) {
          _chatController.replaceLocalMessage(
            agent.name,
            message.id,
            replacement,
          );
        }
      });
      if (changed && shouldScroll) {
        _scrollTimelineToEnd(agent.name);
      }
      return;
    }
    final replacement = outcome.replacement;
    if (replacement != null) {
      _replaceLocalMessage(agent.name, message.id, replacement);
      onDeliveryUpdated?.call(replacement);
    }
    if (outcome.shouldRefreshConversation) {
      unawaited(_loadConversation(agent.name));
    }
  }

  void _replaceLocalMessage(
    String agentName,
    String id,
    CcbConversationItem replacement,
  ) {
    _mutateState(() {
      _chatController.replaceLocalMessage(agentName, id, replacement);
    });
  }
}

CcbConversationItem _failedMessage(CcbConversationItem message, Object error) {
  return message.copyWith(
    state: CcbConversationDeliveryState.failed,
    attachments: [
      for (final attachment in message.attachments)
        attachment.copyWith(
          state:
              attachment.state == CcbMessageAttachmentState.available
                  ? attachment.state
                  : CcbMessageAttachmentState.failed,
          errorMessage: error.toString(),
        ),
    ],
  );
}

String _paneBodyForMessage(CcbConversationItem message) {
  final body = message.body.trim();
  if (message.attachments.isEmpty) {
    return body;
  }
  final lines = <String>[
    if (body.isNotEmpty) body,
    'Attached files:',
    for (final attachment in message.attachments)
      '- ${_paneAttachmentLabel(attachment)} (${attachment.mimeType}, '
          '${attachment.sizeBytes} bytes, file id: ${attachment.fileId})',
  ];
  return lines.join('\n');
}

String _paneAttachmentLabel(CcbMessageAttachment attachment) {
  final projectPath = attachment.projectRelativePath;
  if (projectPath != null && projectPath.trim().isNotEmpty) {
    return '[${attachment.fileName}]($projectPath)';
  }
  return attachment.fileName;
}
