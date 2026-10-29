import 'dart:async';
import 'dart:convert';
import 'dart:typed_data';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:ccb_mobile/ccb_mobile.dart';

class RecordingTerminalTransport
    implements TerminalTransport, HostTerminalTransport {
  RecordingTerminalTransport({
    this.writeError,
    List<Object>? openErrors,
    List<Object>? reconnectErrors,
  }) : openErrors = openErrors ?? <Object>[],
       reconnectErrors = reconnectErrors ?? <Object>[];

  final Object? writeError;
  final List<Object> openErrors;
  final List<Object> reconnectErrors;
  final requests = <TerminalOpenRequest>[];
  final hostRequests = <HostTerminalOpenRequest>[];
  final terminatedHostSessions = <String>[];
  final hostOperationLog = <String>[];
  final sessions = <RecordingTerminalSession>[];

  @override
  Future<TerminalSession> open(TerminalOpenRequest request) async {
    requests.add(request);
    if (openErrors.isNotEmpty) {
      throw openErrors.removeAt(0);
    }
    final session = RecordingTerminalSession(
      request.attachCommand,
      writeError: writeError,
      reconnectErrors: reconnectErrors,
      viewport: TerminalViewport(
        geometry: request.geometry,
        resizePolicy: TerminalResizePolicy.fixedSource,
      ),
    );
    sessions.add(session);
    return session;
  }

  @override
  Future<TerminalSession> openHostTerminal(
    HostTerminalOpenRequest request,
  ) async {
    hostRequests.add(request);
    hostOperationLog.add('open:${request.clientSessionId}');
    final session = RecordingTerminalSession(
      request.attachCommand,
      onClose: () => hostOperationLog.add('close:${request.clientSessionId}'),
      viewport: TerminalViewport(
        geometry: request.geometry,
        resizePolicy: TerminalResizePolicy.client,
      ),
    );
    sessions.add(session);
    return session;
  }

  @override
  Future<void> terminateHostTerminal(String clientSessionId) async {
    hostOperationLog.add('terminate:$clientSessionId');
    terminatedHostSessions.add(clientSessionId);
  }
}

class RecordingTerminalSession
    implements
        TerminalSession,
        TerminalViewportSession,
        TerminalProjectionSession {
  RecordingTerminalSession(
    this.launchedCommand, {
    this.writeError,
    List<Object>? reconnectErrors,
    this.onClose,
    TerminalViewport? viewport,
  }) : reconnectErrors = reconnectErrors ?? <Object>[],
       _viewport =
           viewport ??
           const TerminalViewport(
             geometry: TerminalGeometry(),
             resizePolicy: TerminalResizePolicy.fixedSource,
           );

  final _output = StreamController<Uint8List>.broadcast();
  final _viewportChanges = StreamController<TerminalViewport>.broadcast();
  final _projectionChanges = StreamController<TerminalProjection>.broadcast();
  TerminalViewport _viewport;
  TerminalProjection? _projection;
  final Object? writeError;
  final List<Object> reconnectErrors;
  final VoidCallback? onClose;

  @override
  final String launchedCommand;

  final written = <List<int>>[];
  final pasted = <String>[];
  final resized = <TerminalGeometry>[];
  var reconnectCount = 0;
  var closed = false;

  @override
  Stream<Uint8List> get output => _output.stream;

  @override
  TerminalViewport get viewport => _viewport;

  @override
  Stream<TerminalViewport> get viewportChanges => _viewportChanges.stream;

  @override
  TerminalProjection? get projection => _projection;

  @override
  Stream<TerminalProjection> get projectionChanges => _projectionChanges.stream;

  void setViewport(TerminalViewport viewport) {
    _viewport = viewport;
    _viewportChanges.add(viewport);
  }

  void addOutput(String text) {
    _output.add(Uint8List.fromList(utf8.encode(text)));
  }

  void addProjection({
    String history = '',
    required String screen,
    int sequence = 1,
  }) {
    final projection = TerminalProjection(
      historyBytes: utf8.encode(history),
      screenBytes: utf8.encode(screen),
      sequence: sequence,
    );
    _projection = projection;
    _projectionChanges.add(projection);
  }

  void addOutputError(Object error) {
    _output.addError(error);
  }

  Future<void> endOutput() {
    return _output.close();
  }

  bool get hasOutputListener => _output.hasListener;

  @override
  Future<void> close() async {
    if (closed) {
      return;
    }
    closed = true;
    onClose?.call();
    await _output.close();
    await _viewportChanges.close();
    await _projectionChanges.close();
  }

  @override
  Future<void> paste(String text) async {
    pasted.add(text);
  }

  @override
  Future<void> reconnect() async {
    reconnectCount += 1;
    if (reconnectErrors.isNotEmpty) {
      throw reconnectErrors.removeAt(0);
    }
  }

  @override
  Future<void> resize(TerminalGeometry geometry) async {
    resized.add(geometry);
    _viewport = TerminalViewport(
      geometry: geometry,
      resizePolicy: _viewport.resizePolicy,
      revision: _viewport.revision + 1,
    );
  }

  @override
  Future<void> writeBytes(List<int> bytes) async {
    final error = writeError;
    if (error != null) {
      throw error;
    }
    written.add(bytes);
  }
}

class RecordingGatewayRepository implements MobileCcbRepository {
  RecordingGatewayRepository() : _delegate = FakeMobileCcbRepository.demo();

  final FakeMobileCcbRepository _delegate;
  final focusAgentCalls = <(String, String, int)>[];
  final focusWindowCalls = <(String, String, int)>[];
  final conversationCalls = <(String, String, int)>[];
  final terminalHistoryCalls = <(String, String, int, int)>[];
  final submittedMessages = <CcbAgentMessageSubmitRequest>[];
  final lifecycleCalls = <(String, CcbLifecycleAction)>[];
  ReadableTerminalHistory? terminalHistoryOverride;

  @override
  Future<CcbProjectView> focusAgent({
    required String projectId,
    required String agent,
    required int namespaceEpoch,
  }) async {
    focusAgentCalls.add((projectId, agent, namespaceEpoch));
    return _delegate.focusAgent(
      projectId: projectId,
      agent: agent,
      namespaceEpoch: namespaceEpoch,
    );
  }

  @override
  Future<CcbProjectView> focusWindow({
    required String projectId,
    required String window,
    required int namespaceEpoch,
  }) {
    focusWindowCalls.add((projectId, window, namespaceEpoch));
    return _delegate.focusWindow(
      projectId: projectId,
      window: window,
      namespaceEpoch: namespaceEpoch,
    );
  }

  @override
  Future<CcbProjectView> getProjectView(String projectId) {
    return _delegate.getProjectView(projectId);
  }

  @override
  Future<List<CcbProject>> listProjects() {
    return _delegate.listProjects();
  }

  @override
  Future<ReadableTerminalHistory?> getReadableTerminalHistory({
    required String projectId,
    required String agent,
    required int namespaceEpoch,
    int maxLines = 200,
  }) {
    terminalHistoryCalls.add((projectId, agent, namespaceEpoch, maxLines));
    final override = terminalHistoryOverride;
    if (override != null) {
      return Future.value(override);
    }
    return _delegate.getReadableTerminalHistory(
      projectId: projectId,
      agent: agent,
      namespaceEpoch: namespaceEpoch,
      maxLines: maxLines,
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
    conversationCalls.add((projectId, agent, namespaceEpoch));
    return _delegate.getAgentConversation(
      projectId: projectId,
      agent: agent,
      namespaceEpoch: namespaceEpoch,
      limit: limit,
      cursor: cursor,
    );
  }

  @override
  Future<CcbAgentMessageSubmitResult> submitAgentMessage(
    CcbAgentMessageSubmitRequest request,
  ) {
    submittedMessages.add(request);
    return _delegate.submitAgentMessage(request);
  }

  @override
  Future<CcbProjectLifecycleResult> requestLifecycle({
    required String projectId,
    required CcbLifecycleAction action,
  }) {
    lifecycleCalls.add((projectId, action));
    return _delegate.requestLifecycle(projectId: projectId, action: action);
  }

  @override
  Future<GatewayFileUploadResult> uploadFile({
    required String projectId,
    required String agentName,
    required String fileName,
    required String mimeType,
    required List<int> bytes,
  }) {
    return _delegate.uploadFile(
      projectId: projectId,
      agentName: agentName,
      fileName: fileName,
      mimeType: mimeType,
      bytes: bytes,
    );
  }

  @override
  Future<List<int>> downloadFile({
    required String projectId,
    required String agentName,
    required String fileId,
  }) {
    return _delegate.downloadFile(
      projectId: projectId,
      agentName: agentName,
      fileId: fileId,
    );
  }
}

class ControlledSubmitRepository extends RecordingGatewayRepository {
  final _firstSubmitGate = Completer<void>();

  void finishFirstSubmit() {
    if (!_firstSubmitGate.isCompleted) {
      _firstSubmitGate.complete();
    }
  }

  @override
  Future<CcbAgentMessageSubmitResult> submitAgentMessage(
    CcbAgentMessageSubmitRequest request,
  ) async {
    submittedMessages.add(request);
    if (submittedMessages.length == 1) {
      await _firstSubmitGate.future;
    }
    return _delegate.submitAgentMessage(request);
  }
}

class StaleEpochGatewayRepository extends RecordingGatewayRepository {
  StaleEpochGatewayRepository()
    : _initialView = CcbProjectView.fromProjectViewPayload(
        demoPayloadWithEpoch(4),
      ),
      _refreshedView = CcbProjectView.fromProjectViewPayload(
        demoPayloadWithEpoch(5),
      );

  final CcbProjectView _initialView;
  final CcbProjectView _refreshedView;
  var getProjectViewCalls = 0;

  @override
  Future<CcbProjectView> getProjectView(String projectId) async {
    getProjectViewCalls += 1;
    return getProjectViewCalls == 1 ? _initialView : _refreshedView;
  }

  @override
  Future<CcbAgentConversation> getAgentConversation({
    required String projectId,
    required String agent,
    required int namespaceEpoch,
    int limit = 50,
    String? cursor,
  }) async {
    conversationCalls.add((projectId, agent, namespaceEpoch));
    return CcbAgentConversation(
      projectId: projectId,
      agentName: agent,
      namespaceEpoch: namespaceEpoch,
      items: const [],
      generatedAt: DateTime.utc(2026, 6, 21),
    );
  }

  @override
  Future<CcbAgentMessageSubmitResult> submitAgentMessage(
    CcbAgentMessageSubmitRequest request,
  ) async {
    submittedMessages.add(request);
    if (request.namespaceEpoch == _initialView.namespaceEpoch) {
      throw GatewayHttpException(
        Uri.parse('http://gateway.local/messages'),
        409,
        '{"error":"stale namespace epoch"}',
      );
    }
    final message = CcbConversationItem.userMessage(
      id: request.idempotencyKey,
      agentName: request.agentName,
      body: request.body,
      state: CcbConversationDeliveryState.sent,
    );
    return CcbAgentMessageSubmitResult(
      accepted: true,
      idempotencyKey: request.idempotencyKey,
      messageId: request.idempotencyKey,
      state: CcbConversationDeliveryState.sent,
      message: message,
    );
  }
}

class MarkdownGatewayRepository extends RecordingGatewayRepository {
  @override
  Future<CcbAgentConversation> getAgentConversation({
    required String projectId,
    required String agent,
    required int namespaceEpoch,
    int limit = 50,
    String? cursor,
  }) async {
    conversationCalls.add((projectId, agent, namespaceEpoch));
    return CcbAgentConversation(
      projectId: projectId,
      agentName: agent,
      namespaceEpoch: namespaceEpoch,
      items: [
        CcbConversationItem(
          id: 'reply-markdown',
          agentName: agent,
          kind: CcbConversationItemKind.agentReply,
          title: 'Agent reply',
          body:
              '## Markdown reply\n\n'
              '- first item\n'
              '- second item\n\n'
              '1. ordered item\n'
              '2. follow up item\n\n'
              '- [x] done item\n'
              '- [ ] todo item\n\n'
              '> quoted insight\n\n'
              'Inline `inline code`, **bold text**, and *italic text*.\n\n'
              '```dart\n'
              'final ok = true;\n'
              '```\n\n'
              '| Column | Value |\n'
              '| :--- | ---: |\n'
              '| alpha | 42 |\n\n'
              '[docs link](https://example.com)',
          format: 'markdown',
          source: 'completion_snapshot',
        ),
      ],
      generatedAt: DateTime.utc(2026, 6, 21),
    );
  }
}

class LongConversationRepository extends RecordingGatewayRepository {
  LongConversationRepository({required this.messageCount})
    : _view = CcbProjectView.fromProjectViewPayload(
        demoPayloadWithoutTerminalHistory(),
      );

  final int messageCount;
  final CcbProjectView _view;
  var getProjectViewCalls = 0;

  @override
  Future<CcbProjectView> getProjectView(String projectId) async {
    getProjectViewCalls += 1;
    return _view;
  }

  @override
  Future<CcbAgentConversation> getAgentConversation({
    required String projectId,
    required String agent,
    required int namespaceEpoch,
    int limit = 50,
    String? cursor,
  }) async {
    conversationCalls.add((projectId, agent, namespaceEpoch));
    return CcbAgentConversation(
      projectId: projectId,
      agentName: agent,
      namespaceEpoch: namespaceEpoch,
      items: [
        for (var index = 0; index < messageCount; index += 1)
          CcbConversationItem(
            id: 'long-${index.toString().padLeft(3, '0')}',
            agentName: agent,
            kind: CcbConversationItemKind.agentReply,
            title: 'Long reply ${index.toString().padLeft(3, '0')}',
            body: longConversationBody(index),
            source: 'long_fixture',
          ),
      ],
      generatedAt: DateTime.utc(2026, 6, 22),
    );
  }

  @override
  Future<ReadableTerminalHistory?> getReadableTerminalHistory({
    required String projectId,
    required String agent,
    required int namespaceEpoch,
    int maxLines = 200,
  }) async {
    terminalHistoryCalls.add((projectId, agent, namespaceEpoch, maxLines));
    return null;
  }

  @override
  Future<CcbAgentMessageSubmitResult> submitAgentMessage(
    CcbAgentMessageSubmitRequest request,
  ) async {
    submittedMessages.add(request);
    final message = CcbConversationItem.userMessage(
      id: request.idempotencyKey,
      agentName: request.agentName,
      body: request.body,
      state: CcbConversationDeliveryState.sent,
    );
    return CcbAgentMessageSubmitResult(
      accepted: true,
      idempotencyKey: request.idempotencyKey,
      messageId: request.idempotencyKey,
      state: CcbConversationDeliveryState.sent,
      message: message,
    );
  }
}

String longConversationBody(int index) {
  final label = index.toString().padLeft(3, '0');
  return [
    'Long reply $label',
    for (var line = 0; line < 8; line += 1)
      'Detail line $line for virtualized conversation item $label.',
  ].join('\n');
}

Map<String, Object?> demoPayloadWithoutTerminalHistory() {
  final payload = demoPayloadWithEpoch(4);
  final view = payload['view'] as Map<String, Object?>;
  view.remove('terminal_history');
  return payload;
}

Map<String, Object?> demoPayloadWithEpoch(int epoch) {
  final payload =
      jsonDecode(jsonEncode(demoProjectViewFixture)) as Map<String, Object?>;
  final view = payload['view']! as Map<String, Object?>;
  final namespace = view['namespace']! as Map<String, Object?>;
  namespace['epoch'] = epoch;
  return payload;
}

Map<String, Object?> demoPayloadWithReviewWindow() {
  final payload = demoPayloadWithEpoch(4);
  final view = payload['view']! as Map<String, Object?>;
  final windows = view['windows']! as List<Object?>;
  final agents = view['agents']! as List<Object?>;
  windows.add(<String, Object?>{
    'name': 'review',
    'label': 'review',
    'kind': 'agents',
    'order': 1,
    'active': false,
    'agents': <Object?>['reviewer'],
    'tmux_window_id': '@2',
    'tmux_window_index': 1,
  });
  agents.add(<String, Object?>{
    'name': 'reviewer',
    'provider': 'codex',
    'window': 'review',
    'order': 0,
    'pane_id': '%3',
    'active': false,
    'queue_depth': 0,
    'runtime_health': 'healthy',
    'state': 'idle',
  });
  return payload;
}

Finder renderedTextContaining(String text) {
  return find.byElementPredicate((element) {
    final widget = element.widget;
    if (widget is Text) {
      return (widget.data ?? widget.textSpan?.toPlainText() ?? '').contains(
        text,
      );
    }
    if (widget is RichText) {
      // A Text paints through its own RichText; count the Text once.
      return element.findAncestorWidgetOfExactType<Text>() == null &&
          widget.text.toPlainText().contains(text);
    }
    if (widget is SelectableText) {
      return (widget.data ?? widget.textSpan?.toPlainText() ?? '').contains(
        text,
      );
    }
    return false;
  });
}

class MemorySecureStore implements GatewaySecureStore {
  final Map<String, String> values = {};

  @override
  Future<void> delete({required String key}) async {
    values.remove(key);
  }

  @override
  Future<String?> read({required String key}) async {
    return values[key];
  }

  @override
  Future<void> write({required String key, required String value}) async {
    values[key] = value;
  }
}
