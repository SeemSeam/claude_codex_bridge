import 'dart:async';
import 'dart:io';
import 'dart:typed_data';

import 'package:ccb_mobile/features/agent_chat/agent_chat_controller.dart';
import 'package:ccb_mobile/features/agent_chat/agent_message_submit_coordinator.dart';
import 'package:ccb_mobile/features/agent_chat/agent_pane_message_submitter.dart';
import 'package:ccb_mobile/models/ccb_agent.dart';
import 'package:ccb_mobile/models/ccb_agent_conversation.dart';
import 'package:ccb_mobile/models/ccb_conversation_item.dart';
import 'package:ccb_mobile/transport/gateway_transport.dart';
import 'package:ccb_mobile/models/ccb_project.dart';
import 'package:ccb_mobile/models/ccb_project_lifecycle.dart';
import 'package:ccb_mobile/models/ccb_project_view.dart';
import 'package:ccb_mobile/models/ccb_provider_control.dart';
import 'package:ccb_mobile/models/readable_terminal_history.dart';
import 'package:ccb_mobile/repository/mobile_ccb_repository.dart';
import 'package:ccb_mobile/transport/terminal_transport.dart';
import 'package:test/test.dart';

void main() {
  group('AgentMessageSubmitCoordinator', () {
    test(
      'OMP new controllers cannot reuse a prior prompt key and retry keeps its key',
      () async {
        const agent = CcbAgent(
          name: 'lead',
          provider: 'omp',
          providerControl: CcbProviderControl(
            provider: 'omp',
            runtimeSource: 'collab',
          ),
          window: 'main',
          order: 0,
          active: true,
          queueDepth: 0,
        );
        final repository = _SubmitRepository(
          responses: [
            for (var index = 0; index < 3; index++)
              const CcbAgentMessageSubmitResult(
                accepted: true,
                idempotencyKey: 'receipt',
                messageId: 'receipt',
                state: CcbConversationDeliveryState.unconfirmed,
              ),
          ],
        );
        final firstChat = AgentChatController();
        await _coordinator(chatController: firstChat).send(
          agent: agent,
          body: 'first',
          view: _view(epoch: 7),
          repository: repository,
          refreshView: null,
          onAccepted: () {},
        );
        final secondChat = AgentChatController();
        final second = _coordinator(chatController: secondChat);
        await second.send(
          agent: agent,
          body: 'second',
          view: _view(epoch: 7),
          repository: repository,
          refreshView: null,
          onAccepted: () {},
        );
        expect(
          repository.requests[0].idempotencyKey,
          isNot(repository.requests[1].idempotencyKey),
        );
        await second.retry(
          item: secondChat.localMessagesFor('lead').single,
          view: _view(epoch: 7),
          repository: repository,
          refreshView: null,
        );
        expect(
          repository.requests[2].idempotencyKey,
          repository.requests[1].idempotencyKey,
        );
      },
    );
    test(
      'Mac legacy OMP honors pane input instead of Collab routing',
      () async {
        const agent = CcbAgent(
          name: 'lead',
          provider: 'omp',
          window: 'main',
          order: 0,
          active: true,
          queueDepth: 0,
        );
        final repository = _SubmitRepository();
        final terminal = _RecordingTerminalTransport();
        final pane = AgentPaneMessageSubmitter(onEvent: (_) {});
        await _coordinator(
          chatController: AgentChatController(),
          paneSubmitter: pane,
        ).send(
          agent: agent,
          body: 'legacy prompt',
          view: _view(epoch: 7),
          repository: repository,
          terminalTransport: terminal,
          usePaneInput: true,
          refreshView: null,
          onAccepted: () {},
        );
        expect(repository.requests, isEmpty);
        expect(terminal.sessions.single.pasted, ['legacy prompt']);
        expect(terminal.sessions.single.written, [
          [13],
        ]);
        await pane.closeSessions();
      },
    );

    test('OMP uses Collab gateway even when pane input is requested', () async {
      const agent = CcbAgent(
        name: 'lead',
        provider: 'omp',
        providerControl: CcbProviderControl(
          provider: 'omp',
          runtimeSource: 'collab',
        ),
        window: 'main',
        order: 0,
        active: true,
        queueDepth: 0,
      );
      final chat = AgentChatController();
      final repository = _SubmitRepository(
        responses: [
          CcbAgentMessageSubmitResult(
            accepted: true,
            idempotencyKey: 'local-lead-0',
            messageId: 'local-lead-0',
            state: CcbConversationDeliveryState.unconfirmed,
          ),
        ],
      );
      final terminal = _RecordingTerminalTransport();
      final pane = AgentPaneMessageSubmitter(onEvent: (_) {});
      final coordinator = _coordinator(
        chatController: chat,
        paneSubmitter: pane,
      );
      await coordinator.send(
        agent: agent,
        body: 'native prompt',
        view: _view(epoch: 7),
        repository: repository,
        terminalTransport: terminal,
        usePaneInput: true,
        refreshView: null,
        onAccepted: () {},
      );
      expect(repository.requests.single.body, 'native prompt');
      expect(terminal.requests, isEmpty);
      expect(
        chat.localMessagesFor('lead').single.state,
        CcbConversationDeliveryState.unconfirmed,
      );
      await pane.closeSessions();
    });
    test(
      'sends repository message and replaces optimistic local item',
      () async {
        final chatController = AgentChatController();
        final repository = _SubmitRepository(
          responses: [
            CcbAgentMessageSubmitResult(
              accepted: true,
              idempotencyKey: 'local-lead-0',
              messageId: 'remote-msg',
              state: CcbConversationDeliveryState.sent,
              message: _userMessage(
                id: 'remote-msg',
                body: 'continue',
                state: CcbConversationDeliveryState.sent,
              ),
            ),
          ],
        );
        final acceptedDrafts = <String>[];
        final loads = <String>[];
        final scrolled = <String>[];
        final coordinator = _coordinator(
          chatController: chatController,
          loadConversation: (agentName) {
            loads.add(agentName);
            return Future.value();
          },
          scrollTimelineToEnd: scrolled.add,
        );

        await coordinator.send(
          agent: _leadAgent,
          body: '  continue  ',
          view: _view(epoch: 7),
          repository: repository,
          refreshView: null,
          onAccepted: () {
            acceptedDrafts.add('lead');
          },
        );

        expect(repository.requests.map((item) => item.body), ['continue']);
        expect(acceptedDrafts, ['lead']);
        expect(chatController.isSubmitting('lead'), isFalse);
        expect(chatController.localMessagesFor('lead'), [
          isA<CcbConversationItem>()
              .having((item) => item.id, 'id', 'remote-msg')
              .having((item) => item.sentAt, 'sentAt', isNotNull)
              .having(
                (item) => item.state,
                'state',
                CcbConversationDeliveryState.sent,
              ),
        ]);
        expect(scrolled, ['lead']);
        expect(loads, ['lead']);
      },
    );

    test(
      'uses repository submit when terminal transport is available by default',
      () async {
        final chatController = AgentChatController();
        final repository = _SubmitRepository(
          responses: [
            CcbAgentMessageSubmitResult(
              accepted: true,
              idempotencyKey: 'local-lead-0',
              messageId: 'remote-msg',
              state: CcbConversationDeliveryState.sent,
              message: _userMessage(
                id: 'remote-msg',
                body: 'hi from phone',
                state: CcbConversationDeliveryState.sent,
              ),
            ),
          ],
        );
        final transport = _RecordingTerminalTransport();
        final loads = <String>[];
        final coordinator = _coordinator(
          chatController: chatController,
          paneSubmitter: AgentPaneMessageSubmitter(onEvent: (_) {}),
          loadConversation: (agentName) {
            loads.add(agentName);
            return Future.value();
          },
        );

        await coordinator.send(
          agent: _leadAgent,
          body: '  hi from phone  ',
          view: _view(epoch: 7),
          repository: repository,
          terminalTransport: transport,
          refreshView: null,
          onAccepted: () {},
        );

        expect(repository.requests.map((item) => item.body), ['hi from phone']);
        expect(transport.requests, isEmpty);
        expect(transport.sessions, isEmpty);
        expect(
          chatController.localMessagesFor('lead').single.state,
          CcbConversationDeliveryState.sent,
        );
        expect(loads, ['lead']);
      },
    );

    test(
      'uses pane-backed terminal send only when pane input is required',
      () async {
        final chatController = AgentChatController();
        final repository = _SubmitRepository();
        final transport = _RecordingTerminalTransport();
        final loads = <String>[];
        final coordinator = _coordinator(
          chatController: chatController,
          paneSubmitter: AgentPaneMessageSubmitter(onEvent: (_) {}),
          loadConversation: (agentName) {
            loads.add(agentName);
            return Future.value();
          },
        );

        await coordinator.send(
          agent: _leadAgent,
          body: '  hi from phone  ',
          view: _view(epoch: 7),
          repository: repository,
          terminalTransport: transport,
          usePaneInput: true,
          refreshView: null,
          onAccepted: () {},
        );

        expect(repository.requests, isEmpty);
        expect(transport.requests.single.target.namespaceEpoch, 7);
        expect(transport.sessions.single.pasted, ['hi from phone']);
        expect(transport.sessions.single.written, [
          [13],
        ]);
        expect(
          chatController.localMessagesFor('lead').single.state,
          CcbConversationDeliveryState.sent,
        );
        expect(loads, ['lead']);
      },
    );

    test('pane-backed attachment send includes project file link', () async {
      final attachmentFile = File(
        '${Directory.systemTemp.path}/ccb-mobile-pane-attachment.txt',
      );
      await attachmentFile.writeAsString('attachment');
      addTearDown(() async {
        if (await attachmentFile.exists()) {
          await attachmentFile.delete();
        }
      });
      final chatController = AgentChatController();
      final repository = _SubmitRepository(
        uploadResult: const GatewayFileUploadResult(
          fileId: 'file-1',
          fileName: 'notes.txt',
          mimeType: 'text/plain',
          sizeBytes: 10,
          projectRelativePath: '.ccb/mobile/uploads/lead/file-1-notes.txt',
          projectPath: '/repo/.ccb/mobile/uploads/lead/file-1-notes.txt',
        ),
      );
      final transport = _RecordingTerminalTransport();
      final coordinator = _coordinator(
        chatController: chatController,
        paneSubmitter: AgentPaneMessageSubmitter(onEvent: (_) {}),
      );

      await coordinator.send(
        agent: _leadAgent,
        body: 'review this',
        attachments: [
          CcbMessageAttachment(
            fileId: 'local-file',
            fileName: 'notes.txt',
            mimeType: 'text/plain',
            sizeBytes: 10,
            localPath: attachmentFile.path,
          ),
        ],
        view: _view(epoch: 7),
        repository: repository,
        terminalTransport: transport,
        usePaneInput: true,
        refreshView: null,
        onAccepted: () {},
      );

      expect(transport.sessions.single.pasted.single, contains('review this'));
      expect(
        transport.sessions.single.pasted.single,
        contains('[notes.txt](.ccb/mobile/uploads/lead/file-1-notes.txt)'),
      );
    });

    test(
      'does not fallback to repository submit when pane input is required',
      () async {
        final chatController = AgentChatController();
        final repository = _SubmitRepository();
        final coordinator = _coordinator(
          chatController: chatController,
          paneSubmitter: AgentPaneMessageSubmitter(onEvent: (_) {}),
        );

        await coordinator.send(
          agent: _leadAgent,
          body: 'hi through paired gateway',
          view: _view(epoch: 7),
          repository: repository,
          terminalTransport: null,
          usePaneInput: true,
          refreshView: null,
          onAccepted: () {},
        );

        expect(repository.requests, isEmpty);
        expect(
          chatController.localMessagesFor('lead').single.state,
          CcbConversationDeliveryState.failed,
        );
      },
    );

    test(
      'keeps sent local item when returned conversation misses submission',
      () async {
        final chatController = AgentChatController();
        final repository = _SubmitRepository(
          responses: [
            CcbAgentMessageSubmitResult(
              accepted: true,
              idempotencyKey: 'local-lead-0',
              messageId: 'remote-msg',
              state: CcbConversationDeliveryState.sent,
              conversation: _conversation(body: 'done'),
            ),
          ],
        );
        final loads = <String>[];
        final scrolled = <String>[];
        final coordinator = _coordinator(
          chatController: chatController,
          isTimelineNearEnd: (_) => true,
          loadConversation: (agentName) {
            loads.add(agentName);
            return Future.value();
          },
          scrollTimelineToEnd: scrolled.add,
        );

        await coordinator.send(
          agent: _leadAgent,
          body: 'continue',
          view: _view(epoch: 7),
          repository: repository,
          refreshView: null,
          onAccepted: () {},
        );

        expect(chatController.localMessagesFor('lead'), [
          isA<CcbConversationItem>()
              .having((item) => item.id, 'id', 'local-lead-0')
              .having((item) => item.body, 'body', 'continue')
              .having(
                (item) => item.state,
                'state',
                CcbConversationDeliveryState.sent,
              ),
        ]);
        expect(
          chatController.remoteConversationFor('lead')?.items.single.body,
          'done',
        );
        expect(chatController.hasNewMessages('lead'), isFalse);
        expect(scrolled, ['lead', 'lead']);
        expect(loads, isEmpty);
      },
    );

    test(
      'keeps earlier sent local item when later remote conversation covers only the later send',
      () async {
        final chatController = AgentChatController();
        final repository = _SubmitRepository(
          responses: [
            CcbAgentMessageSubmitResult(
              accepted: true,
              idempotencyKey: 'local-lead-0',
              messageId: 'local-lead-0',
              state: CcbConversationDeliveryState.sent,
              conversation: _conversation(body: 'remote reply only'),
            ),
            CcbAgentMessageSubmitResult(
              accepted: true,
              idempotencyKey: 'local-lead-1',
              messageId: 'local-lead-1',
              state: CcbConversationDeliveryState.sent,
              conversation: _conversationWithItems([
                _userMessage(
                  id: 'local-lead-1',
                  body: 'second send',
                  state: CcbConversationDeliveryState.sent,
                ),
              ]),
            ),
          ],
        );
        final coordinator = _coordinator(
          chatController: chatController,
          isTimelineNearEnd: (_) => true,
        );

        await coordinator.send(
          agent: _leadAgent,
          body: 'first send',
          view: _view(epoch: 7),
          repository: repository,
          refreshView: null,
          onAccepted: () {},
        );
        await coordinator.send(
          agent: _leadAgent,
          body: 'second send',
          view: _view(epoch: 7),
          repository: repository,
          refreshView: null,
          onAccepted: () {},
        );

        expect(
          chatController.localMessagesFor('lead').map((item) => item.body),
          ['first send'],
        );
        expect(
          chatController.localMessagesFor('lead').single.state,
          CcbConversationDeliveryState.sent,
        );
        expect(
          chatController
              .remoteConversationFor('lead')
              ?.items
              .map((item) => item.body),
          ['second send'],
        );
      },
    );

    test(
      'applies returned conversation and removes covered local item',
      () async {
        final chatController = AgentChatController();
        final repository = _SubmitRepository(
          responses: [
            CcbAgentMessageSubmitResult(
              accepted: true,
              idempotencyKey: 'local-lead-0',
              messageId: 'remote-msg',
              state: CcbConversationDeliveryState.sent,
              conversation: _conversationWithItems([
                _userMessage(
                  id: 'remote-msg',
                  body: 'continue',
                  state: CcbConversationDeliveryState.sent,
                ),
              ]),
            ),
          ],
        );
        final scrolled = <String>[];
        final coordinator = _coordinator(
          chatController: chatController,
          isTimelineNearEnd: (_) => true,
          scrollTimelineToEnd: scrolled.add,
        );

        await coordinator.send(
          agent: _leadAgent,
          body: 'continue',
          view: _view(epoch: 7),
          repository: repository,
          refreshView: null,
          onAccepted: () {},
        );

        expect(chatController.localMessagesFor('lead'), isEmpty);
        expect(
          chatController.remoteConversationFor('lead')?.items.single.body,
          'continue',
        );
        expect(scrolled, ['lead', 'lead']);
      },
    );

    test('ignores blank sends without touching state', () async {
      final chatController = AgentChatController();
      final repository = _SubmitRepository();
      var accepted = false;
      final coordinator = _coordinator(chatController: chatController);

      await coordinator.send(
        agent: _leadAgent,
        body: '   ',
        view: _view(epoch: 7),
        repository: repository,
        refreshView: null,
        onAccepted: () {
          accepted = true;
        },
      );

      expect(repository.requests, isEmpty);
      expect(accepted, isFalse);
      expect(chatController.localMessagesFor('lead'), isEmpty);
      expect(chatController.isSubmitting('lead'), isFalse);
    });

    test('ignores concurrent send attempts while already submitting', () async {
      final chatController = AgentChatController();
      final repository = _SubmitRepository(
        responses: [
          CcbAgentMessageSubmitResult(
            accepted: true,
            idempotencyKey: 'local-lead-0',
            messageId: 'remote-msg',
            state: CcbConversationDeliveryState.sent,
          ),
        ],
      );
      var acceptedCount = 0;
      final coordinator = _coordinator(chatController: chatController);

      chatController.beginSubmitting('lead');

      await coordinator.send(
        agent: _leadAgent,
        body: 'concurrent',
        view: _view(epoch: 7),
        repository: repository,
        refreshView: null,
        onAccepted: () {
          acceptedCount += 1;
        },
      );

      expect(repository.requests, isEmpty);
      expect(acceptedCount, 0);
    });

    test('allows attachment-only repository sends', () async {
      final chatController = AgentChatController();
      final repository = _SubmitRepository(
        responses: [
          CcbAgentMessageSubmitResult(
            accepted: true,
            idempotencyKey: 'local-lead-0',
            messageId: 'remote-msg',
            state: CcbConversationDeliveryState.sent,
          ),
        ],
      );
      final coordinator = _coordinator(chatController: chatController);

      await coordinator.send(
        agent: _leadAgent,
        body: '   ',
        attachments: const [
          CcbMessageAttachment(
            fileId: 'file-1',
            fileName: 'notes.txt',
            mimeType: 'text/plain',
            sizeBytes: 12,
          ),
        ],
        view: _view(epoch: 7),
        repository: repository,
        refreshView: null,
        onAccepted: () {},
      );

      expect(repository.requests.single.body, isEmpty);
      expect(repository.requests.single.attachments.single.fileId, 'file-1');
      expect(
        chatController.localMessagesFor('lead').single.state,
        CcbConversationDeliveryState.sent,
      );
    });

    test('keeps consecutive attachment-only sends visible', () async {
      final chatController = AgentChatController();
      final repository = _SubmitRepository(
        responses: [
          CcbAgentMessageSubmitResult(
            accepted: true,
            idempotencyKey: 'local-lead-0',
            messageId: 'local-lead-0',
            state: CcbConversationDeliveryState.sent,
            conversation: _conversationWithItems([
              _attachmentMessage(id: 'local-lead-0', fileName: 'one.txt'),
            ]),
          ),
          CcbAgentMessageSubmitResult(
            accepted: true,
            idempotencyKey: 'local-lead-1',
            messageId: 'local-lead-1',
            state: CcbConversationDeliveryState.sent,
            conversation: _conversationWithItems([
              _attachmentMessage(id: 'local-lead-0', fileName: 'one.txt'),
              _attachmentMessage(id: 'local-lead-1', fileName: 'two.txt'),
            ]),
          ),
        ],
      );
      final coordinator = _coordinator(chatController: chatController);

      await coordinator.send(
        agent: _leadAgent,
        body: '',
        attachments: const [
          CcbMessageAttachment(
            fileId: 'file-1',
            fileName: 'one.txt',
            mimeType: 'text/plain',
            sizeBytes: 12,
          ),
        ],
        view: _view(epoch: 7),
        repository: repository,
        refreshView: null,
        onAccepted: () {},
      );
      await coordinator.send(
        agent: _leadAgent,
        body: '',
        attachments: const [
          CcbMessageAttachment(
            fileId: 'file-2',
            fileName: 'two.txt',
            mimeType: 'text/plain',
            sizeBytes: 12,
          ),
        ],
        view: _view(epoch: 7),
        repository: repository,
        refreshView: null,
        onAccepted: () {},
      );

      expect(chatController.localMessagesFor('lead'), isEmpty);
      expect(
        chatController
            .remoteConversationFor('lead')
            ?.items
            .map((item) => item.attachments.single.fileName),
        ['one.txt', 'two.txt'],
      );
    });
  });
}

AgentMessageSubmitCoordinator _coordinator({
  required AgentChatController chatController,
  bool Function(String agentName)? isTimelineNearEnd,
  void Function(String agentName)? scrollTimelineToEnd,
  Future<void> Function(String agentName)? loadConversation,
  AgentPaneMessageSubmitter? paneSubmitter,
}) {
  return AgentMessageSubmitCoordinator(
    chatController: chatController,
    isMounted: () => true,
    mutateState: (update) {
      update();
    },
    isTimelineNearEnd: isTimelineNearEnd ?? (_) => true,
    scrollTimelineToEnd: scrollTimelineToEnd ?? (_) {},
    loadConversation: loadConversation ?? (_) => Future.value(),
    paneSubmitter: paneSubmitter,
  );
}

const _leadAgent = CcbAgent(
  name: 'lead',
  provider: 'codex',
  window: 'main',
  order: 0,
  active: true,
  queueDepth: 0,
  paneId: '%2',
);

CcbProjectView _view({required int? epoch}) {
  return CcbProjectView(
    project: const CcbProject(
      id: 'proj',
      displayName: 'Project',
      root: '/repo',
    ),
    namespaceEpoch: epoch,
    tmuxSocketPath: null,
    tmuxSessionName: null,
    activeWindow: 'main',
    activePaneId: '%2',
    windows: const [],
    agents: const [_leadAgent],
    contentItems: const [],
    notifications: const [],
    terminalHistories: const {},
  );
}

CcbConversationItem _userMessage({
  required String id,
  required String body,
  required CcbConversationDeliveryState state,
}) {
  return CcbConversationItem.userMessage(
    id: id,
    agentName: 'lead',
    body: body,
    state: state,
  );
}

CcbConversationItem _attachmentMessage({
  required String id,
  required String fileName,
}) {
  return CcbConversationItem.userMessage(
    id: id,
    agentName: 'lead',
    body: '',
    attachments: [
      CcbMessageAttachment(
        fileId: id,
        fileName: fileName,
        mimeType: 'text/plain',
        sizeBytes: 12,
      ),
    ],
    state: CcbConversationDeliveryState.sent,
  );
}

CcbAgentConversation _conversation({required String body}) {
  return _conversationWithItems([
    CcbConversationItem(
      id: 'reply',
      agentName: 'lead',
      kind: CcbConversationItemKind.agentReply,
      title: 'Agent reply',
      body: body,
    ),
  ]);
}

CcbAgentConversation _conversationWithItems(List<CcbConversationItem> items) {
  return CcbAgentConversation(
    projectId: 'proj',
    agentName: 'lead',
    namespaceEpoch: 7,
    items: items,
    generatedAt: DateTime.utc(2026, 6, 22),
  );
}

class _SubmitRepository implements MobileCcbRepository {
  _SubmitRepository({this.responses = const [], this.uploadResult});

  final List<Object> responses;
  final GatewayFileUploadResult? uploadResult;
  final requests = <CcbAgentMessageSubmitRequest>[];
  var _responseIndex = 0;

  @override
  Future<CcbAgentMessageSubmitResult> submitAgentMessage(
    CcbAgentMessageSubmitRequest request,
  ) async {
    requests.add(request);
    if (_responseIndex >= responses.length) {
      throw StateError('missing queued submit response');
    }
    final response = responses[_responseIndex];
    _responseIndex += 1;
    if (response is Exception) {
      throw response;
    }
    if (response is Error) {
      throw response;
    }
    return response as CcbAgentMessageSubmitResult;
  }

  @override
  Future<CcbProjectView> focusAgent({
    required String projectId,
    required String agent,
    required int namespaceEpoch,
  }) {
    throw UnimplementedError();
  }

  @override
  Future<CcbProjectView> focusWindow({
    required String projectId,
    required String window,
    required int namespaceEpoch,
  }) {
    throw UnimplementedError();
  }

  @override
  Future<CcbAgentConversation> getAgentConversation({
    required String projectId,
    required String agent,
    required int namespaceEpoch,
    int limit = 50,
    String? cursor,
  }) {
    throw UnimplementedError();
  }

  @override
  Future<CcbProjectView> getProjectView(String projectId) {
    throw UnimplementedError();
  }

  @override
  Future<ReadableTerminalHistory?> getReadableTerminalHistory({
    required String projectId,
    required String agent,
    required int namespaceEpoch,
    int maxLines = 200,
  }) {
    throw UnimplementedError();
  }

  @override
  Future<List<CcbProject>> listProjects() {
    throw UnimplementedError();
  }

  @override
  Future<CcbProjectLifecycleResult> requestLifecycle({
    required String projectId,
    required CcbLifecycleAction action,
  }) {
    throw UnimplementedError();
  }

  @override
  Future<GatewayFileUploadResult> uploadFile({
    required String projectId,
    required String agentName,
    required String fileName,
    required String mimeType,
    required List<int> bytes,
  }) async {
    return uploadResult ?? (throw UnimplementedError());
  }

  @override
  Future<List<int>> downloadFile({
    required String projectId,
    required String agentName,
    required String fileId,
  }) async {
    throw UnimplementedError();
  }
}

class _RecordingTerminalTransport implements TerminalTransport {
  final requests = <TerminalOpenRequest>[];
  final sessions = <_RecordingTerminalSession>[];

  @override
  Future<TerminalSession> open(TerminalOpenRequest request) async {
    requests.add(request);
    final session = _RecordingTerminalSession(request.attachCommand);
    sessions.add(session);
    return session;
  }
}

class _RecordingTerminalSession implements TerminalSession {
  _RecordingTerminalSession(this.launchedCommand);

  final StreamController<Uint8List> _output = StreamController.broadcast();
  final pasted = <String>[];
  final written = <List<int>>[];

  @override
  final String launchedCommand;

  @override
  Stream<Uint8List> get output => _output.stream;

  @override
  Future<void> close() async {
    await _output.close();
  }

  @override
  Future<void> paste(String text) async {
    pasted.add(text);
  }

  @override
  Future<void> reconnect() async {}

  @override
  Future<void> resize(TerminalGeometry geometry) async {}

  @override
  Future<void> writeBytes(List<int> bytes) async {
    written.add(bytes);
  }
}
