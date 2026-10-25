import '../models/ccb_agent_conversation.dart';
import '../models/ccb_project.dart';
import '../models/ccb_project_lifecycle.dart';
import '../models/ccb_project_view.dart';
import '../models/ccb_provider_control.dart';
import '../models/readable_terminal_history.dart';
import '../transport/gateway_transport.dart';

abstract interface class MobileCcbRepository {
  Future<List<CcbProject>> listProjects();

  Future<CcbProjectView> getProjectView(String projectId);

  Future<CcbProjectView> focusAgent({
    required String projectId,
    required String agent,
    required int namespaceEpoch,
  });

  Future<CcbProjectView> focusWindow({
    required String projectId,
    required String window,
    required int namespaceEpoch,
  });

  Future<ReadableTerminalHistory?> getReadableTerminalHistory({
    required String projectId,
    required String agent,
    required int namespaceEpoch,
    int maxLines = 200,
  });

  Future<CcbAgentConversation> getAgentConversation({
    required String projectId,
    required String agent,
    required int namespaceEpoch,
    int limit = 50,
    String? cursor,
  });

  Future<CcbAgentMessageSubmitResult> submitAgentMessage(
    CcbAgentMessageSubmitRequest request,
  );

  Future<CcbProjectLifecycleResult> requestLifecycle({
    required String projectId,
    required CcbLifecycleAction action,
  });

  Future<GatewayFileUploadResult> uploadFile({
    required String projectId,
    required String agentName,
    required String fileName,
    required String mimeType,
    required List<int> bytes,
  });

  Future<List<int>> downloadFile({
    required String projectId,
    required String agentName,
    required String fileId,
  });
}

abstract interface class MobileCcbRepositoryFileUploader {
  Future<GatewayFileUploadResult> uploadFileFromPath({
    required String projectId,
    required String agentName,
    required String fileName,
    required String mimeType,
    required String path,
  });
}

abstract interface class MobileCcbProviderControlRepository {
  Future<CcbProviderControlDetails> getAgentProviderControl({
    required String projectId,
    required String agentName,
  });

  Future<CcbProviderAccountUsage> getAgentProviderQuota({
    required String projectId,
    required String agentName,
  });

  Future<CcbProviderSettingsResult> updateAgentProviderSettings({
    required String projectId,
    required String agentName,
    required String model,
    String? thinking,
    required String expectedRevision,
    required int expectedNamespaceEpoch,
    required String expectedProvider,
    String? expectedRuntimeRevision,
    required String idempotencyKey,
  });
}

abstract interface class MobileCcbUiResponseRepository {
  Future<void> uiResponse({required String projectId, required String agent,
    required int namespaceEpoch, required String expectedOmpSessionId,
    required String expectedOmpGeneration, required int reqId, required String value});
}
