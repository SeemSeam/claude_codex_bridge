import 'dart:io';

import '../models/ccb_agent_conversation.dart';
import '../models/ccb_project.dart';
import '../models/ccb_project_lifecycle.dart';
import '../models/ccb_project_view.dart';
import '../models/ccb_provider_control.dart';
import '../models/readable_terminal_history.dart';
import '../transport/gateway_transport.dart';
import '../transport/gateway_connection_outcome.dart';
import '../transport/http_gateway_transport.dart';
import 'mobile_ccb_repository.dart';

abstract interface class MobileGatewayProfileHealthProbe {
  Future<GatewayHealth> health();

  Future<GatewayDevice> device();
}

/// Performs the complete core-route verification as one connection outcome.
abstract interface class MobileGatewayCoreRouteVerifier {
  /// Verifies both core routes as one supervisor-authoritative operation.
  /// Implementations must not report a successful ordinary read until the
  /// entire verification has completed.
  Future<void> verifyCoreRoutes();
}

abstract interface class MobileGatewayPresenceReporter {
  Future<void> reportPresence({
    required bool visible,
    String? focusedProjectId,
    String? focusedAgent,
    bool userActivity = false,
  });
}

class GatewayMobileCcbRepository
    implements
        MobileCcbRepository,
        MobileCcbUiResponseRepository,
        MobileCcbRepositoryFileUploader,
        MobileCcbProviderControlRepository,
        MobileGatewayProfileHealthProbe,
        MobileGatewayCoreRouteVerifier,
        MobileGatewayPresenceReporter,
        GatewayConnectionOutcomeReportable {
  GatewayMobileCcbRepository({required GatewayTransport transport})
    : _transport = transport;

  final GatewayTransport _transport;
  GatewayConnectionOutcomeReporter? _outcomeReporter;

  @override
  set outcomeReporter(GatewayConnectionOutcomeReporter? reporter) {
    _outcomeReporter = reporter;
  }

  @override
  Future<void> uiResponse({required String projectId, required String agent,
    required int namespaceEpoch, required String expectedOmpSessionId,
    required String expectedOmpGeneration, required int reqId, required String value}) {
    final transport = _transport;
    if (transport is! GatewayUiResponseTransport) throw UnsupportedError('UI responses are unavailable');
    return _report(GatewayConnectionOperation.mutation, () => (transport as GatewayUiResponseTransport).uiResponse(
      projectId: projectId, agent: agent, namespaceEpoch: namespaceEpoch,
      expectedOmpSessionId: expectedOmpSessionId, expectedOmpGeneration: expectedOmpGeneration, reqId: reqId, value: value));
  }

  Future<T> _report<T>(
    GatewayConnectionOperation operation,
    Future<T> Function() action,
  ) async {
    try {
      final result = await action();
      _outcomeReporter?.succeeded(operation);
      return result;
    } catch (error) {
      _outcomeReporter?.failed(operation, error);
      rethrow;
    }
  }

  @override
  Future<void> verifyCoreRoutes() async {
    try {
      final health = await _transport.health();
      if (health.status.toLowerCase() != 'ok') {
        throw GatewayHttpException(Uri(), 503, 'gateway health is degraded');
      }
      final device = await _transport.device();
      if (device.revoked) {
        throw GatewayHttpException(Uri(), 401, 'device revoked');
      }
      _outcomeReporter?.succeeded(GatewayConnectionOperation.coreRead);
    } catch (error) {
      _outcomeReporter?.failed(GatewayConnectionOperation.coreRead, error);
      rethrow;
    }
  }

  @override
  Future<List<CcbProject>> listProjects() {
    return _report(
      GatewayConnectionOperation.dataRead,
      _transport.listProjects,
    );
  }

  @override
  Future<GatewayHealth> health() {
    return _report(GatewayConnectionOperation.dataRead, _transport.health);
  }

  @override
  Future<GatewayDevice> device() {
    return _report(GatewayConnectionOperation.dataRead, _transport.device);
  }

  @override
  Future<void> reportPresence({
    required bool visible,
    String? focusedProjectId,
    String? focusedAgent,
    bool userActivity = false,
  }) {
    final transport = _transport;
    if (transport is! GatewayPresenceTransport) {
      return Future<void>.value();
    }
    return _report(
      GatewayConnectionOperation.mutation,
      () => (transport as GatewayPresenceTransport).reportPresence(
        visible: visible,
        focusedProjectId: focusedProjectId,
        focusedAgent: focusedAgent,
        userActivity: userActivity,
      ),
    );
  }

  @override
  Future<CcbProjectView> getProjectView(String projectId) {
    return _report(
      GatewayConnectionOperation.dataRead,
      () => _transport.getProjectView(projectId),
    );
  }

  @override
  Future<CcbProviderControlDetails> getAgentProviderControl({
    required String projectId,
    required String agentName,
  }) {
    final transport = _transport;
    if (transport is! GatewayProviderControlTransport) {
      throw UnsupportedError('gateway does not support provider control');
    }
    return _report(
      GatewayConnectionOperation.dataRead,
      () => (transport as GatewayProviderControlTransport)
          .getAgentProviderControl(projectId: projectId, agentName: agentName),
    );
  }

  @override
  Future<CcbProviderAccountUsage> getAgentProviderQuota({
    required String projectId,
    required String agentName,
  }) {
    final transport = _transport;
    if (transport is! GatewayProviderControlTransport) {
      throw UnsupportedError('gateway does not support provider quota');
    }
    return _report(
      GatewayConnectionOperation.dataRead,
      () => (transport as GatewayProviderControlTransport)
          .getAgentProviderQuota(projectId: projectId, agentName: agentName),
    );
  }

  @override
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
  }) {
    final transport = _transport;
    if (transport is! GatewayProviderControlTransport) {
      throw UnsupportedError('gateway does not support provider control');
    }
    return _report(
      GatewayConnectionOperation.mutation,
      () => (transport as GatewayProviderControlTransport)
          .updateAgentProviderSettings(
            projectId: projectId,
            agentName: agentName,
            model: model,
            thinking: thinking,
            expectedRevision: expectedRevision,
            expectedNamespaceEpoch: expectedNamespaceEpoch,
            expectedProvider: expectedProvider,
            expectedRuntimeRevision: expectedRuntimeRevision,
            idempotencyKey: idempotencyKey,
          ),
    );
  }

  @override
  Future<CcbProjectView> focusAgent({
    required String projectId,
    required String agent,
    required int namespaceEpoch,
  }) {
    return _report(
      GatewayConnectionOperation.mutation,
      () => _transport.focusAgent(
        projectId: projectId,
        agent: agent,
        namespaceEpoch: namespaceEpoch,
      ),
    );
  }

  @override
  Future<CcbProjectView> focusWindow({
    required String projectId,
    required String window,
    required int namespaceEpoch,
  }) {
    return _report(
      GatewayConnectionOperation.mutation,
      () => _transport.focusWindow(
        projectId: projectId,
        window: window,
        namespaceEpoch: namespaceEpoch,
      ),
    );
  }

  @override
  Future<ReadableTerminalHistory?> getReadableTerminalHistory({
    required String projectId,
    required String agent,
    required int namespaceEpoch,
    int maxLines = 200,
  }) {
    return _report(
      GatewayConnectionOperation.dataRead,
      () => _transport.getReadableTerminalHistory(
        projectId: projectId,
        agent: agent,
        namespaceEpoch: namespaceEpoch,
        maxLines: maxLines,
      ),
    );
  }

  @override
  Future<CcbAgentConversation> getAgentConversation({
    required String projectId,
    required String agent,
    required int namespaceEpoch,
    int limit = 50,
    String? cursor,
  }) {
    return _report(
      GatewayConnectionOperation.dataRead,
      () => _transport.getAgentConversation(
        projectId: projectId,
        agent: agent,
        namespaceEpoch: namespaceEpoch,
        limit: limit,
        cursor: cursor,
      ),
    );
  }

  @override
  Future<CcbAgentMessageSubmitResult> submitAgentMessage(
    CcbAgentMessageSubmitRequest request,
  ) {
    return _report(
      GatewayConnectionOperation.mutation,
      () => _transport.submitAgentMessage(request),
    );
  }

  @override
  Future<CcbProjectLifecycleResult> requestLifecycle({
    required String projectId,
    required CcbLifecycleAction action,
  }) {
    return _report(
      GatewayConnectionOperation.mutation,
      () => _transport.requestLifecycle(projectId: projectId, action: action),
    );
  }

  @override
  Future<GatewayFileUploadResult> uploadFile({
    required String projectId,
    required String agentName,
    required String fileName,
    required String mimeType,
    required List<int> bytes,
  }) {
    return _report(
      GatewayConnectionOperation.mutation,
      () => _transport.uploadFile(
        projectId: projectId,
        agentName: agentName,
        fileName: fileName,
        mimeType: mimeType,
        bytes: bytes,
      ),
    );
  }

  @override
  Future<GatewayFileUploadResult> uploadFileFromPath({
    required String projectId,
    required String agentName,
    required String fileName,
    required String mimeType,
    required String path,
  }) {
    final transport = _transport;
    if (transport is GatewayFilePathUploader) {
      return _report(
        GatewayConnectionOperation.mutation,
        () => (transport as GatewayFilePathUploader).uploadFileFromPath(
          projectId: projectId,
          agentName: agentName,
          fileName: fileName,
          mimeType: mimeType,
          path: path,
        ),
      );
    }
    return File(path).readAsBytes().then(
      (bytes) => uploadFile(
        projectId: projectId,
        agentName: agentName,
        fileName: fileName,
        mimeType: mimeType,
        bytes: bytes,
      ),
    );
  }

  @override
  Future<List<int>> downloadFile({
    required String projectId,
    required String agentName,
    required String fileId,
  }) {
    return _report(
      GatewayConnectionOperation.dataRead,
      () => _transport.downloadFile(
        projectId: projectId,
        agentName: agentName,
        fileId: fileId,
      ),
    );
  }
}
