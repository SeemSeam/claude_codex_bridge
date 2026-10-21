import 'dart:io';

import 'package:ccb_mobile/features/agent_chat/agent_repository_message_submitter.dart';
import 'package:ccb_mobile/models/ccb_agent.dart';
import 'package:ccb_mobile/models/ccb_agent_conversation.dart';
import 'package:ccb_mobile/models/ccb_conversation_item.dart';
import 'package:ccb_mobile/models/ccb_project.dart';
import 'package:ccb_mobile/models/ccb_project_lifecycle.dart';
import 'package:ccb_mobile/models/ccb_project_view.dart';
import 'package:ccb_mobile/models/ccb_provider_control.dart';
import 'package:ccb_mobile/models/readable_terminal_history.dart';
import 'package:ccb_mobile/repository/mobile_ccb_repository.dart';
import 'package:ccb_mobile/transport/gateway_transport.dart';
import 'package:ccb_mobile/transport/http_gateway_transport.dart';
import 'package:test/test.dart';

void main() {
  group('AgentRepositoryMessageSubmitter', () {
    test(
      'legacy OMP does not classify gateway errors as Collab admission',
      () async {
        const agent = CcbAgent(
          name: 'lead',
          provider: 'omp',
          window: 'main',
          order: 0,
          active: true,
          queueDepth: 0,
        );
        final repository = _SubmitRepository(
          responses: [
            GatewayHttpException(
              Uri.parse('http://localhost/messages'),
              502,
              'unavailable',
            ),
          ],
        );
        final outcome = await AgentRepositoryMessageSubmitter(
          repository: repository,
        ).submit(
          agent: agent,
          message: _localMessage(),
          view: _view(4, agents: [agent]),
          conversation: const CcbAgentConversation(
            projectId: 'proj',
            agentName: 'lead',
            namespaceEpoch: 4,
            items: [],
            ompSessionId: 'old-session',
            ompGeneration: 'old-generation',
          ),
        );
        expect(outcome.replacement?.state, CcbConversationDeliveryState.failed);
        expect(
          repository.requests.single.toJson()['expected_omp_session_id'],
          isNull,
        );
        expect(
          repository.requests.single.toJson()['expected_omp_generation'],
          isNull,
        );
      },
    );

    test(
      'OMP preserves unknown admission without a second submission',
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
            GatewayHttpException(
              Uri.parse('http://localhost/messages'),
              502,
              'Collab admission unknown',
            ),
          ],
        );
        final outcome = await AgentRepositoryMessageSubmitter(
          repository: repository,
        ).submit(
          agent: agent,
          message: _localMessage(),
          view: _view(4, agents: [agent]),
          conversation: const CcbAgentConversation(
            projectId: 'proj',
            agentName: 'lead',
            namespaceEpoch: 4,
            items: [],
            ompSessionId: 'native-session',
            ompGeneration: 'owner:7',
          ),
        );
        expect(
          outcome.replacement?.state,
          CcbConversationDeliveryState.unconfirmed,
        );
        expect(repository.requests, hasLength(1));
        expect(outcome.replacement?.id, 'local-lead-0');
        expect(
          repository.requests.single.toJson()['expected_omp_session_id'],
          'native-session',
        );
        expect(
          repository.requests.single.toJson()['expected_omp_generation'],
          'owner:7',
        );
      },
    );
    test('returns failed replacement without namespace epoch', () async {
      final repository = _SubmitRepository();

      final outcome = await AgentRepositoryMessageSubmitter(
        repository: repository,
      ).submit(agent: _leadAgent, message: _localMessage(), view: _view(null));

      expect(outcome.replacement?.state, CcbConversationDeliveryState.failed);
      expect(outcome.conversation, isNull);
      expect(outcome.shouldRefreshConversation, isFalse);
      expect(repository.requests, isEmpty);
    });

    test('returns remote conversation when submit includes one', () async {
      final conversation = _conversation(epoch: 4, body: 'done');
      final repository = _SubmitRepository(
        responses: [
          CcbAgentMessageSubmitResult(
            accepted: true,
            idempotencyKey: 'local-lead-0',
            messageId: 'remote-msg',
            state: CcbConversationDeliveryState.sent,
            conversation: conversation,
          ),
        ],
      );

      final outcome = await AgentRepositoryMessageSubmitter(
        repository: repository,
      ).submit(agent: _leadAgent, message: _localMessage(), view: _view(4));

      expect(outcome.conversation, same(conversation));
      expect(outcome.replacement?.id, 'local-lead-0');
      expect(outcome.replacement?.state, CcbConversationDeliveryState.sent);
      expect(outcome.shouldRefreshConversation, isFalse);
      expect(repository.requests.map((item) => item.namespaceEpoch), [4]);
    });

    test(
      'returns replacement and asks caller to refresh conversation',
      () async {
        final remoteMessage = _localMessage(
          id: 'remote-msg',
          state: CcbConversationDeliveryState.sent,
        );
        final repository = _SubmitRepository(
          responses: [
            CcbAgentMessageSubmitResult(
              accepted: true,
              idempotencyKey: 'local-lead-0',
              messageId: 'remote-msg',
              state: CcbConversationDeliveryState.sent,
              message: remoteMessage,
            ),
          ],
        );

        final outcome = await AgentRepositoryMessageSubmitter(
          repository: repository,
        ).submit(agent: _leadAgent, message: _localMessage(), view: _view(4));

        expect(outcome.replacement?.id, 'remote-msg');
        expect(outcome.replacement?.state, CcbConversationDeliveryState.sent);
        expect(outcome.conversation, isNull);
        expect(outcome.shouldRefreshConversation, isTrue);
      },
    );

    test(
      'does not replay input when submit hits stale namespace epoch',
      () async {
        final repository = _SubmitRepository(
          responses: [
            _staleEpochError(),
            CcbAgentMessageSubmitResult(
              accepted: true,
              idempotencyKey: 'local-lead-0',
              messageId: 'remote-msg',
              state: CcbConversationDeliveryState.sent,
              message: _localMessage(
                id: 'remote-msg',
                state: CcbConversationDeliveryState.sent,
              ),
            ),
          ],
        );
        var refreshCount = 0;

        final outcome = await AgentRepositoryMessageSubmitter(
          repository: repository,
          refreshView: () async {
            refreshCount += 1;
            return _view(5);
          },
        ).submit(agent: _leadAgent, message: _localMessage(), view: _view(4));

        expect(outcome.replacement?.state, CcbConversationDeliveryState.failed);
        expect(outcome.shouldRefreshConversation, isFalse);
        expect(refreshCount, 0);
        expect(repository.requests.map((item) => item.namespaceEpoch), [4]);
      },
    );

    test(
      'returns failed replacement when stale refresh cannot recover',
      () async {
        final repository = _SubmitRepository(responses: [_staleEpochError()]);

        final outcome = await AgentRepositoryMessageSubmitter(
          repository: repository,
          refreshView: () async => _view(5, agents: const []),
        ).submit(agent: _leadAgent, message: _localMessage(), view: _view(4));

        expect(outcome.replacement?.state, CcbConversationDeliveryState.failed);
        expect(outcome.shouldRefreshConversation, isFalse);
        expect(repository.requests.map((item) => item.namespaceEpoch), [4]);
      },
    );

    test('maps non-stale submit errors to failed replacement', () async {
      final repository = _SubmitRepository(responses: [StateError('down')]);

      final outcome = await AgentRepositoryMessageSubmitter(
        repository: repository,
      ).submit(agent: _leadAgent, message: _localMessage(), view: _view(4));

      expect(outcome.replacement?.state, CcbConversationDeliveryState.failed);
      expect(outcome.shouldRefreshConversation, isFalse);
      expect(repository.requests.map((item) => item.namespaceEpoch), [4]);
    });

    test(
      'message and upload mutation failures are each submitted once',
      () async {
        final messageRepository = _SubmitRepository(
          responses: [StateError('gateway unavailable')],
        );
        final messageOutcome = await AgentRepositoryMessageSubmitter(
          repository: messageRepository,
        ).submit(agent: _leadAgent, message: _localMessage(), view: _view(4));
        expect(
          messageOutcome.replacement?.state,
          CcbConversationDeliveryState.failed,
        );
        expect(messageRepository.requests, hasLength(1));

        final temp = await File(
          '${Directory.systemTemp.path}/ccb-mobile-single-upload-failure.txt',
        ).writeAsString('once');
        addTearDown(() => temp.delete());
        final uploadRepository = _SubmitRepository(
          uploadError: StateError('down'),
        );
        final uploadOutcome = await AgentRepositoryMessageSubmitter(
          repository: uploadRepository,
        ).submit(
          agent: _leadAgent,
          view: _view(4),
          message: CcbConversationItem.userMessage(
            id: 'local-upload-once',
            agentName: 'lead',
            body: '',
            attachments: [
              CcbMessageAttachment(
                fileId: 'draft-once',
                fileName: 'once.txt',
                mimeType: 'text/plain',
                sizeBytes: temp.lengthSync(),
                localPath: temp.path,
                state: CcbMessageAttachmentState.queued,
              ),
            ],
          ),
        );
        expect(
          uploadOutcome.replacement?.state,
          CcbConversationDeliveryState.failed,
        );
        expect(uploadRepository.uploads, hasLength(1));
        expect(uploadRepository.requests, isEmpty);
      },
    );

    test(
      'uploads local attachments before submitting attachment-only message',
      () async {
        final temp = await File(
          '${Directory.systemTemp.path}/ccb-mobile-attachment-test.txt',
        ).writeAsString('hello attachment');
        addTearDown(() {
          if (temp.existsSync()) {
            temp.deleteSync();
          }
        });
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
        final message = CcbConversationItem.userMessage(
          id: 'local-lead-0',
          agentName: 'lead',
          body: '',
          attachments: [
            CcbMessageAttachment(
              fileId: 'draft-1',
              fileName: 'notes.txt',
              mimeType: 'text/plain',
              sizeBytes: temp.lengthSync(),
              localPath: temp.path,
              state: CcbMessageAttachmentState.queued,
            ),
          ],
        );

        final outcome = await AgentRepositoryMessageSubmitter(
          repository: repository,
        ).submit(agent: _leadAgent, message: message, view: _view(4));

        expect(repository.uploads.single.fileName, 'notes.txt');
        expect(repository.uploads.single.bytes, 'hello attachment'.codeUnits);
        expect(repository.requests.single.body, isEmpty);
        expect(repository.requests.single.attachments.single.fileId, 'file-1');
        expect(
          repository.requests.single.attachments.single.projectRelativePath,
          '.ccb/mobile/uploads/lead/file-1-notes.txt',
        );
        expect(repository.requests.single.attachments.single.localPath, isNull);
        expect(outcome.replacement?.attachments.single.fileId, 'file-1');
        expect(
          outcome.replacement?.attachments.single.projectRelativePath,
          '.ccb/mobile/uploads/lead/file-1-notes.txt',
        );
        expect(
          outcome.replacement?.attachments.single.state,
          CcbMessageAttachmentState.available,
        );
      },
    );

    test(
      'uses repository path uploader for local attachments when available',
      () async {
        final temp = await File(
          '${Directory.systemTemp.path}/ccb-mobile-path-upload-test.txt',
        ).writeAsString('stream me');
        addTearDown(() {
          if (temp.existsSync()) {
            temp.deleteSync();
          }
        });
        final repository = _PathUploadRepository(
          responses: [
            CcbAgentMessageSubmitResult(
              accepted: true,
              idempotencyKey: 'local-lead-0',
              messageId: 'remote-msg',
              state: CcbConversationDeliveryState.sent,
            ),
          ],
        );
        final message = CcbConversationItem.userMessage(
          id: 'local-lead-0',
          agentName: 'lead',
          body: 'caption',
          attachments: [
            CcbMessageAttachment(
              fileId: 'draft-1',
              fileName: 'notes.txt',
              mimeType: 'text/plain',
              sizeBytes: temp.lengthSync(),
              localPath: temp.path,
              state: CcbMessageAttachmentState.queued,
            ),
          ],
        );

        final outcome = await AgentRepositoryMessageSubmitter(
          repository: repository,
        ).submit(agent: _leadAgent, message: message, view: _view(4));

        expect(repository.uploads, isEmpty);
        expect(repository.pathUploads.single.path, temp.path);
        expect(
          repository.requests.single.attachments.single.fileId,
          'path-file-1',
        );
        expect(outcome.replacement?.attachments.single.fileId, 'path-file-1');
      },
    );

    test('upload failure keeps local attachment and marks it failed', () async {
      final temp = await File(
        '${Directory.systemTemp.path}/ccb-mobile-attachment-fail.txt',
      ).writeAsString('hello attachment');
      addTearDown(() {
        if (temp.existsSync()) {
          temp.deleteSync();
        }
      });
      final repository = _SubmitRepository(uploadError: StateError('413'));
      final message = CcbConversationItem.userMessage(
        id: 'local-lead-0',
        agentName: 'lead',
        body: 'caption',
        attachments: [
          CcbMessageAttachment(
            fileId: 'draft-1',
            fileName: 'large.txt',
            mimeType: 'text/plain',
            sizeBytes: temp.lengthSync(),
            localPath: temp.path,
            state: CcbMessageAttachmentState.queued,
          ),
        ],
      );

      final outcome = await AgentRepositoryMessageSubmitter(
        repository: repository,
      ).submit(agent: _leadAgent, message: message, view: _view(4));

      expect(repository.requests, isEmpty);
      expect(outcome.replacement?.state, CcbConversationDeliveryState.failed);
      expect(
        outcome.replacement?.attachments.single.state,
        CcbMessageAttachmentState.failed,
      );
      expect(outcome.replacement?.attachments.single.localPath, temp.path);
      expect(
        outcome.replacement?.attachments.single.errorMessage,
        contains('413'),
      );
    });
  });
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

CcbProjectView _view(int? epoch, {List<CcbAgent> agents = const [_leadAgent]}) {
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
    agents: agents,
    contentItems: const [],
    notifications: const [],
    terminalHistories: const {},
  );
}

CcbConversationItem _localMessage({
  String id = 'local-lead-0',
  CcbConversationDeliveryState state = CcbConversationDeliveryState.pending,
}) {
  return CcbConversationItem.userMessage(
    id: id,
    agentName: 'lead',
    body: 'continue',
    state: state,
  );
}

CcbAgentConversation _conversation({required int epoch, required String body}) {
  return CcbAgentConversation(
    projectId: 'proj',
    agentName: 'lead',
    namespaceEpoch: epoch,
    items: [
      CcbConversationItem(
        id: 'reply-$epoch',
        agentName: 'lead',
        kind: CcbConversationItemKind.agentReply,
        title: 'Agent reply',
        body: body,
      ),
    ],
    generatedAt: DateTime.utc(2026, 6, 22),
  );
}

GatewayHttpException _staleEpochError() {
  return GatewayHttpException(
    Uri.parse('http://127.0.0.1/v1/projects/proj/agents/lead/messages'),
    409,
    'stale namespace epoch',
  );
}

class _SubmitRepository implements MobileCcbRepository {
  _SubmitRepository({this.responses = const [], this.uploadError});

  final List<Object> responses;
  final Object? uploadError;
  final requests = <CcbAgentMessageSubmitRequest>[];
  final uploads = <_UploadCall>[];
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
    uploads.add(
      _UploadCall(
        projectId: projectId,
        agentName: agentName,
        fileName: fileName,
        mimeType: mimeType,
        bytes: bytes,
      ),
    );
    final error = uploadError;
    if (error != null) {
      if (error is Exception) {
        throw error;
      }
      if (error is Error) {
        throw error;
      }
    }
    return GatewayFileUploadResult(
      fileId: 'file-${uploads.length}',
      fileName: fileName,
      mimeType: mimeType,
      sizeBytes: bytes.length,
      projectRelativePath:
          '.ccb/mobile/uploads/$agentName/file-${uploads.length}-$fileName',
      projectPath:
          '/repo/.ccb/mobile/uploads/$agentName/file-${uploads.length}-$fileName',
    );
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

class _PathUploadRepository extends _SubmitRepository
    implements MobileCcbRepositoryFileUploader {
  _PathUploadRepository({super.responses});

  final pathUploads = <_PathUploadCall>[];

  @override
  Future<GatewayFileUploadResult> uploadFile({
    required String projectId,
    required String agentName,
    required String fileName,
    required String mimeType,
    required List<int> bytes,
  }) {
    throw StateError('byte upload should not be called');
  }

  @override
  Future<GatewayFileUploadResult> uploadFileFromPath({
    required String projectId,
    required String agentName,
    required String fileName,
    required String mimeType,
    required String path,
  }) async {
    pathUploads.add(
      _PathUploadCall(
        projectId: projectId,
        agentName: agentName,
        fileName: fileName,
        mimeType: mimeType,
        path: path,
      ),
    );
    return GatewayFileUploadResult(
      fileId: 'path-file-${pathUploads.length}',
      fileName: fileName,
      mimeType: mimeType,
      sizeBytes: await File(path).length(),
      projectRelativePath:
          '.ccb/mobile/uploads/$agentName/path-file-${pathUploads.length}-$fileName',
      projectPath:
          '/repo/.ccb/mobile/uploads/$agentName/path-file-${pathUploads.length}-$fileName',
    );
  }
}

class _UploadCall {
  const _UploadCall({
    required this.projectId,
    required this.agentName,
    required this.fileName,
    required this.mimeType,
    required this.bytes,
  });

  final String projectId;
  final String agentName;
  final String fileName;
  final String mimeType;
  final List<int> bytes;
}

class _PathUploadCall {
  const _PathUploadCall({
    required this.projectId,
    required this.agentName,
    required this.fileName,
    required this.mimeType,
    required this.path,
  });

  final String projectId;
  final String agentName;
  final String fileName;
  final String mimeType;
  final String path;
}
