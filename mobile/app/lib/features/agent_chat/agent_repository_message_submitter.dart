import 'dart:async';
import 'dart:io';

import '../../models/ccb_agent.dart';
import '../../models/ccb_agent_conversation.dart';
import '../../models/ccb_conversation_item.dart';
import '../../models/ccb_project_view.dart';
import '../../repository/mobile_ccb_repository.dart';
import '../../transport/http_gateway_transport.dart';
import '../../transport/relay_socket_gateway_transport.dart';
import 'agent_chat_state_helpers.dart';
import 'agent_conversation_loader.dart';

class AgentRepositoryMessageSubmitter {
  const AgentRepositoryMessageSubmitter({
    required MobileCcbRepository repository,
    AgentViewRefresh? refreshView,
  }) : _repository = repository;

  final MobileCcbRepository _repository;

  Future<AgentRepositoryMessageSubmitOutcome> submit({
    required CcbAgent agent,
    required CcbConversationItem message,
    required CcbProjectView view,
    CcbAgentConversation? conversation,
  }) async {
    final namespaceEpoch = view.namespaceEpoch;
    if (namespaceEpoch == null) {
      return AgentRepositoryMessageSubmitOutcome.replaceLocalMessage(
        message.copyWith(state: CcbConversationDeliveryState.failed),
      );
    }
    try {
      final uploadedAttachments = await uploadAttachments(
        agent: agent,
        message: message,
        view: view,
      );
      final result = await _repository.submitAgentMessage(
        CcbAgentMessageSubmitRequest(
          projectId: view.project.id,
          agentName: agent.name,
          namespaceEpoch: namespaceEpoch,
          idempotencyKey: message.id,
          body: message.body,
          attachments: uploadedAttachments,
          expectedOmpSessionId:
              isOmpCollabAgent(agent) ? conversation?.ompSessionId : null,
          expectedOmpGeneration:
              isOmpCollabAgent(agent) ? conversation?.ompGeneration : null,
        ),
      );
      final resultMessage = result.message;
      final replacementBase =
          resultMessage == null
              ? message
              : resultMessage.copyWith(
                sentAt: resultMessage.sentAt ?? message.sentAt,
                startedAt: resultMessage.startedAt ?? message.startedAt,
                completedAt: resultMessage.completedAt ?? message.completedAt,
                durationMs: resultMessage.durationMs ?? message.durationMs,
              );
      final replacement = replacementBase.copyWith(
        state: result.state,
        attachments:
            resultMessage?.attachments.isNotEmpty == true
                ? null
                : uploadedAttachments,
      );
      final returnedConversation = result.conversation;
      if (returnedConversation != null) {
        return AgentRepositoryMessageSubmitOutcome.remoteConversation(
          returnedConversation,
          replacement: replacement,
        );
      }
      return AgentRepositoryMessageSubmitOutcome.replaceLocalMessage(
        replacement,
        shouldRefreshConversation: true,
      );
    } catch (error) {
      // A stale namespace is an unsafe replay boundary. Preserve the local
      // draft/attachment state and require an explicit user retry instead.
      return AgentRepositoryMessageSubmitOutcome.replaceLocalMessage(
        _failedMessage(message, error, omp: isOmpCollabAgent(agent)),
      );
    }
  }

  Future<List<CcbMessageAttachment>> uploadAttachments({
    required CcbAgent agent,
    required CcbConversationItem message,
    required CcbProjectView view,
  }) async {
    if (message.attachments.isEmpty) {
      return const [];
    }
    final uploaded = <CcbMessageAttachment>[];
    for (final attachment in message.attachments) {
      final path = attachment.localPath;
      if (path == null || path.isEmpty) {
        uploaded.add(
          attachment.copyWith(
            state: CcbMessageAttachmentState.available,
            clearLocalPath: true,
            clearErrorMessage: true,
          ),
        );
        continue;
      }
      final fileUploader = _repository;
      final result =
          fileUploader is MobileCcbRepositoryFileUploader
              ? await (fileUploader as MobileCcbRepositoryFileUploader)
                  .uploadFileFromPath(
                    projectId: view.project.id,
                    agentName: agent.name,
                    fileName: attachment.fileName,
                    mimeType: attachment.mimeType,
                    path: path,
                  )
              : await _repository.uploadFile(
                projectId: view.project.id,
                agentName: agent.name,
                fileName: attachment.fileName,
                mimeType: attachment.mimeType,
                bytes: await File(path).readAsBytes(),
              );
      uploaded.add(
        CcbMessageAttachment(
          fileId: result.fileId,
          fileName:
              result.fileName.isEmpty ? attachment.fileName : result.fileName,
          mimeType: result.mimeType ?? attachment.mimeType,
          sizeBytes: result.sizeBytes ?? attachment.sizeBytes,
          kind: attachment.effectiveKind,
          state: CcbMessageAttachmentState.available,
          projectRelativePath: result.projectRelativePath,
          projectPath: result.projectPath,
        ),
      );
    }
    return uploaded;
  }

  CcbConversationItem _failedMessage(
    CcbConversationItem message,
    Object error, {
    bool omp = false,
  }) {
    return message.copyWith(
      state:
          omp && _mayHaveReachedCollab(error)
              ? CcbConversationDeliveryState.unconfirmed
              : CcbConversationDeliveryState.failed,
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
}

bool _mayHaveReachedCollab(Object error) {
  final status = switch (error) {
    GatewayHttpException() => error.statusCode,
    RelayGatewayException() => error.statusCode,
    _ => null,
  };
  // Explicit rejections are safe to retry. A lost response or unknown native
  // admission is not: keep the original key and require reconciliation.
  return status == 502 || status == null || error is TimeoutException;
}

class AgentRepositoryMessageSubmitOutcome {
  const AgentRepositoryMessageSubmitOutcome._({
    this.conversation,
    this.replacement,
    this.shouldRefreshConversation = false,
  });

  factory AgentRepositoryMessageSubmitOutcome.remoteConversation(
    CcbAgentConversation conversation, {
    required CcbConversationItem replacement,
  }) {
    return AgentRepositoryMessageSubmitOutcome._(
      conversation: conversation,
      replacement: replacement,
    );
  }

  factory AgentRepositoryMessageSubmitOutcome.replaceLocalMessage(
    CcbConversationItem replacement, {
    bool shouldRefreshConversation = false,
  }) {
    return AgentRepositoryMessageSubmitOutcome._(
      replacement: replacement,
      shouldRefreshConversation: shouldRefreshConversation,
    );
  }

  final CcbAgentConversation? conversation;
  final CcbConversationItem? replacement;
  final bool shouldRefreshConversation;

  bool get hasRemoteConversation => conversation != null;
}
