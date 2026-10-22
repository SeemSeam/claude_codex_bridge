import 'package:ccb_mobile/features/agent_chat/agent_chat_controller.dart';
import 'package:ccb_mobile/features/agent_chat/selected_agent_workspace_model.dart';
import 'package:ccb_mobile/models/ccb_agent.dart';
import 'package:ccb_mobile/models/ccb_agent_conversation.dart';
import 'package:ccb_mobile/models/ccb_conversation_item.dart';
import 'package:ccb_mobile/models/ccb_project.dart';
import 'package:ccb_mobile/models/ccb_project_view.dart';
import 'package:ccb_mobile/models/readable_terminal_history.dart';
import 'package:test/test.dart';

void main() {
  test('queued and held users do not displace the native running reply', () {
    final reply = CcbConversationItem.fromJson({
      'id': 'active-reply', 'agent': 'mobile', 'kind': 'agent_reply',
      'body': 'partial', 'source': 'provider_native/omp',
      'native_turn': {
        'schema_version': 1,
        'session_id': 'session', 'turn_id': 'active', 'status': 'running',
        'entries': <Object?>[],
      },
    });
    for (final state in [CcbConversationDeliveryState.queued, CcbConversationDeliveryState.unconfirmed]) {
      final pending = CcbConversationItem(
        id: 'next', agentName: 'mobile', kind: CcbConversationItemKind.userMessage,
        title: 'You', body: 'same', source: 'mobile/collab', state: state,
        sentAt: DateTime.utc(2026, 10, 22),
      );
      expect(selectedAgentWorkingReplyItemId([reply, pending]), 'active-reply');
      expect(selectedAgentWorkingReplyItemId([pending]), isNull);
    }
  });
  test('inserts one visible boundary between provider sessions', () {
    final chatController = AgentChatController();
    final view = _view();
    final agent = _agent();
    chatController.applyRemoteConversation(
      agentName: agent.name,
      shouldScroll: true,
      conversation: CcbAgentConversation(
        projectId: view.project.id,
        agentName: agent.name,
        namespaceEpoch: view.namespaceEpoch!,
        items: [
          const CcbConversationItem(
            id: 'old-reply',
            agentName: 'mobile',
            kind: CcbConversationItemKind.agentReply,
            title: 'Agent reply',
            body: 'old context',
            source: 'provider_native/codex',
            sessionId: 'session-old',
          ),
          CcbConversationItem(
            id: 'new-user',
            agentName: 'mobile',
            kind: CcbConversationItemKind.userMessage,
            title: 'You',
            body: 'new context',
            source: 'provider_native/codex',
            sessionId: 'session-new',
          ),
          CcbConversationItem(
            id: 'new-reply',
            agentName: 'mobile',
            kind: CcbConversationItemKind.agentReply,
            title: 'Agent reply',
            body: 'new answer',
            source: 'provider_native/codex',
            sessionId: 'session-new',
          ),
        ],
        generatedAt: DateTime.utc(2026, 8, 12),
      ),
    );

    final model = selectedAgentWorkspaceModel(
      view: view,
      agent: agent,
      chatController: chatController,
      isAwaitingAgentResponse: false,
    );

    expect(model.timelineItems.map((item) => item.body), [
      'old context',
      'New context',
      'new context',
      'new answer',
    ]);
    final boundary = model.timelineItems[1];
    expect(isProviderSessionBoundaryItem(boundary), isTrue);
    expect(boundary.id, 'provider-session-boundary-new-user');
    expect(boundary.sessionId, 'session-new');
  });

  test('does not insert a boundary within one provider session', () {
    final chatController = AgentChatController();
    final view = _view();
    final agent = _agent();
    chatController.applyRemoteConversation(
      agentName: agent.name,
      shouldScroll: true,
      conversation: CcbAgentConversation(
        projectId: view.project.id,
        agentName: agent.name,
        namespaceEpoch: view.namespaceEpoch!,
        items: [
          const CcbConversationItem(
            id: 'same-user',
            agentName: 'mobile',
            kind: CcbConversationItemKind.userMessage,
            title: 'You',
            body: 'question',
            source: 'provider_native/claude',
            sessionId: 'same-session',
          ),
          CcbConversationItem(
            id: 'same-reply',
            agentName: 'mobile',
            kind: CcbConversationItemKind.agentReply,
            title: 'Agent reply',
            body: 'answer',
            source: 'provider_native/claude',
            sessionId: 'same-session',
          ),
        ],
        generatedAt: DateTime.utc(2026, 8, 12),
      ),
    );

    final model = selectedAgentWorkspaceModel(
      view: view,
      agent: agent,
      chatController: chatController,
      isAwaitingAgentResponse: false,
    );

    expect(model.timelineItems.where(isProviderSessionBoundaryItem), isEmpty);
  });

  test('session boundary never becomes the working reply target', () {
    final chatController = AgentChatController();
    final agent = _agent(
      activityState: 'active',
      activitySource: 'codex_runtime',
      activityReason: 'codex_working_status_line',
    );
    final view = _view(agent: agent);
    chatController.applyRemoteConversation(
      agentName: agent.name,
      shouldScroll: true,
      conversation: CcbAgentConversation(
        projectId: view.project.id,
        agentName: agent.name,
        namespaceEpoch: view.namespaceEpoch!,
        items: [
          const CcbConversationItem(
            id: 'prior-reply',
            agentName: 'mobile',
            kind: CcbConversationItemKind.agentReply,
            title: 'Agent reply',
            body: 'prior',
            source: 'provider_native/codex',
            sessionId: 'prior-session',
          ),
          CcbConversationItem(
            id: 'current-user',
            agentName: 'mobile',
            kind: CcbConversationItemKind.userMessage,
            title: 'You',
            body: 'current',
            source: 'provider_native/codex',
            sessionId: 'current-session',
            sentAt: DateTime.utc(2026, 8, 12, 9),
          ),
          CcbConversationItem(
            id: 'current-reply',
            agentName: 'mobile',
            kind: CcbConversationItemKind.agentReply,
            title: 'Agent reply',
            body: 'streaming',
            source: 'provider_native/codex',
            sessionId: 'current-session',
            startedAt: DateTime.utc(2026, 8, 12, 9, 0, 1),
          ),
        ],
        generatedAt: DateTime.utc(2026, 8, 12),
      ),
    );

    final model = selectedAgentWorkspaceModel(
      view: view,
      agent: agent,
      chatController: chatController,
      isAwaitingAgentResponse: false,
    );

    expect(
      model.workingReplyItemId,
      syntheticAgentWorkingConversationItemId(agent.name),
    );
    expect(
      model.timelineItems
          .singleWhere((item) => isProviderSessionBoundaryItem(item))
          .id,
      isNot(model.workingReplyItemId),
    );
  });

  test('keeps comms updates out of visible conversation timeline', () {
    final chatController = AgentChatController();
    final view = _view();
    final agent = _agent();
    chatController.applyRemoteConversation(
      agentName: agent.name,
      shouldScroll: true,
      conversation: CcbAgentConversation(
        projectId: view.project.id,
        agentName: agent.name,
        namespaceEpoch: view.namespaceEpoch!,
        items: [
          const CcbConversationItem(
            id: 'reply-1',
            agentName: 'mobile',
            kind: CcbConversationItemKind.agentReply,
            title: 'Agent reply',
            body: 'real answer',
            source: 'completion_snapshot',
          ),
          const CcbConversationItem(
            id: 'comms-1',
            agentName: 'mobile',
            kind: CcbConversationItemKind.commsItem,
            title: 'Comms',
            body: 'project view updated',
            source: 'project_view',
          ),
        ],
        generatedAt: DateTime.utc(2026, 6, 24),
      ),
    );

    final model = selectedAgentWorkspaceModel(
      view: view,
      agent: agent,
      chatController: chatController,
      isAwaitingAgentResponse: false,
    );

    expect(model.timelineItems.map((item) => item.id), ['reply-1']);
    expect(model.commsItems.map((item) => item.id), ['comms-1']);
    expect(model.hasOlderConversation, isFalse);
  });

  test('reports older conversation availability from next cursor', () {
    final chatController = AgentChatController();
    final view = _view();
    final agent = _agent();
    chatController.applyRemoteConversation(
      agentName: agent.name,
      shouldScroll: true,
      conversation: CcbAgentConversation(
        projectId: view.project.id,
        agentName: agent.name,
        namespaceEpoch: view.namespaceEpoch!,
        items: const [],
        nextCursor: '8',
        generatedAt: DateTime.utc(2026, 6, 24),
      ),
    );

    final model = selectedAgentWorkspaceModel(
      view: view,
      agent: agent,
      chatController: chatController,
      isAwaitingAgentResponse: true,
    );

    expect(model.hasOlderConversation, isTrue);
    expect(model.isAwaitingAgentResponse, isTrue);
  });

  test('keeps refreshed pane history out of provider native transcript', () {
    final chatController = AgentChatController();
    final view = _view();
    final agent = _agent();
    chatController.applyRemoteConversation(
      agentName: agent.name,
      shouldScroll: true,
      conversation: CcbAgentConversation(
        projectId: view.project.id,
        agentName: agent.name,
        namespaceEpoch: view.namespaceEpoch!,
        items: [
          const CcbConversationItem(
            id: 'native-reply',
            agentName: 'mobile',
            kind: CcbConversationItemKind.agentReply,
            title: 'Agent reply',
            body: 'native transcript answer',
            source: 'provider_native/codex',
          ),
        ],
        generatedAt: DateTime.utc(2026, 6, 24),
      ),
    );
    chatController.setRefreshedTerminalHistory(
      agent.name,
      const ReadableTerminalHistory(
        agentName: 'mobile',
        historyScope: 'tmux_scrollback',
        blocks: [
          ReadableTerminalBlock(
            id: 'status-output',
            type: 'log',
            title: 'Terminal output',
            text: 'Credits remaining: 42%',
          ),
        ],
      ),
    );

    final model = selectedAgentWorkspaceModel(
      view: view,
      agent: agent,
      chatController: chatController,
      isAwaitingAgentResponse: false,
    );

    expect(model.timelineItems.map((item) => item.body), [
      'native transcript answer',
    ]);
    expect(model.timelineItems.single.source, 'provider_native/codex');
  });

  test('reports working execution status from project view activity', () {
    final chatController = AgentChatController();
    final agent = _agent(
      activityState: 'active',
      activitySource: 'codex_runtime',
      activityReason: 'codex_session_task_started',
    );

    final model = selectedAgentWorkspaceModel(
      view: _view(agent: agent),
      agent: agent,
      chatController: chatController,
      isAwaitingAgentResponse: false,
    );

    expect(model.executionStatus?.label, 'Working');
    expect(model.executionStatus?.state, 'working');
    expect(model.executionStatus?.isRefreshing, isFalse);
  });

  test('folds reconnecting status into working', () {
    final chatController = AgentChatController();
    final agent = _agent(
      activityState: 'pending',
      activitySource: 'codex_runtime',
      activityReason: 'codex_runtime_reconnecting',
    );

    final model = selectedAgentWorkspaceModel(
      view: _view(agent: agent),
      agent: agent,
      chatController: chatController,
      isAwaitingAgentResponse: false,
    );

    expect(model.executionStatus?.label, 'Working');
    expect(model.executionStatus?.state, 'working');
    expect(model.executionStatus?.isRefreshing, isTrue);
  });

  test('reports exception status from project view activity', () {
    final chatController = AgentChatController();
    final agent = _agent(
      activityState: 'failed',
      activitySource: 'codex_runtime',
      activityReason: 'provider_api_error',
    );

    final model = selectedAgentWorkspaceModel(
      view: _view(agent: agent),
      agent: agent,
      chatController: chatController,
      isAwaitingAgentResponse: false,
    );

    expect(model.executionStatus?.label, 'Exception');
    expect(model.executionStatus?.state, 'exception');
    expect(model.executionStatus?.isRefreshing, isFalse);
  });

  test(
    'current pending activity reports an interrupted error before working',
    () {
      final chatController = AgentChatController();
      final agent = _agent(
        activityState: 'pending',
        activitySource: 'codex_runtime',
        activityReason: 'conversation_interrupted',
      );

      final model = selectedAgentWorkspaceModel(
        view: _view(agent: agent),
        agent: agent,
        chatController: chatController,
        isAwaitingAgentResponse: false,
      );

      expect(model.executionStatus?.label, 'Exception');
      expect(model.executionStatus?.state, 'exception');
      expect(model.executionStatus?.isRefreshing, isFalse);
    },
  );

  test(
    'project view failed activity overrides local awaiting response status',
    () {
      final chatController = AgentChatController();
      final agent = _agent(
        activityState: 'failed',
        activitySource: 'codex_runtime',
        activityReason: 'provider_api_error',
      );

      final model = selectedAgentWorkspaceModel(
        view: _view(agent: agent),
        agent: agent,
        chatController: chatController,
        isAwaitingAgentResponse: true,
      );

      expect(model.executionStatus?.label, 'Exception');
      expect(model.executionStatus?.state, 'exception');
      expect(model.executionStatus?.isRefreshing, isFalse);
    },
  );

  test('local exception is not hidden by a retained working marker', () {
    final chatController = AgentChatController();
    final agent = _agent(
      activityState: 'active',
      activitySource: 'codex_runtime',
      activityReason: 'codex_working_status_line',
    );

    final model = selectedAgentWorkspaceModel(
      view: _view(agent: agent),
      agent: agent,
      chatController: chatController,
      isAwaitingAgentResponse: false,
      hasLocalExecutionException: true,
    );

    expect(model.executionStatus?.label, 'Exception');
    expect(model.executionStatus?.state, 'exception');
    expect(model.executionStatus?.isRefreshing, isFalse);
  });

  test('reports local terminal exception when no current work is visible', () {
    final chatController = AgentChatController();
    final agent = _agent();

    final model = selectedAgentWorkspaceModel(
      view: _view(agent: agent),
      agent: agent,
      chatController: chatController,
      isAwaitingAgentResponse: false,
      hasLocalExecutionException: true,
    );

    expect(model.executionStatus?.label, 'Exception');
    expect(model.executionStatus?.state, 'exception');
    expect(model.executionStatus?.isRefreshing, isFalse);
  });

  test('reports idle execution status when no work is visible', () {
    final chatController = AgentChatController();
    final agent = _agent(provider: 'claude');

    final model = selectedAgentWorkspaceModel(
      view: _view(agent: agent),
      agent: agent,
      chatController: chatController,
      isAwaitingAgentResponse: false,
    );

    expect(model.executionStatus?.label, 'Idle');
    expect(model.executionStatus?.state, 'idle');
    expect(model.executionStatus?.isRefreshing, isFalse);
  });

  test('loading idle agent history does not create a working bubble', () {
    final chatController = AgentChatController();
    final agent = _agent(
      activityState: 'idle',
      activitySource: 'provider_pane',
      activityReason: 'provider_prompt_idle',
    );
    chatController.beginLoadingConversation(agent.name);

    final model = selectedAgentWorkspaceModel(
      view: _view(agent: agent),
      agent: agent,
      chatController: chatController,
      isAwaitingAgentResponse: false,
    );

    expect(model.isLoadingConversation, isTrue);
    expect(model.executionStatus?.state, 'idle');
    expect(model.workingReplyItemId, isNull);
    expect(
      model.timelineItems.where(
        (item) =>
            item.id == syntheticAgentWorkingConversationItemId(agent.name),
      ),
      isEmpty,
    );
  });

  test(
    'reports idle when provider prompt idle reason accompanies idle state',
    () {
      final chatController = AgentChatController();
      final agent = _agent(
        activityState: 'idle',
        activitySource: 'provider_pane',
        activityReason: 'provider_prompt_idle',
      );

      final model = selectedAgentWorkspaceModel(
        view: _view(agent: agent),
        agent: agent,
        chatController: chatController,
        isAwaitingAgentResponse: false,
      );

      expect(model.executionStatus?.label, 'Idle');
      expect(model.executionStatus?.state, 'idle');
      expect(model.executionStatus?.isRefreshing, isFalse);
    },
  );

  test('local awaiting response overrides stale idle project view', () {
    final chatController = AgentChatController();
    final agent = _agent(
      activityState: 'idle',
      activitySource: 'provider_pane',
      activityReason: 'provider_prompt_idle',
    );

    final model = selectedAgentWorkspaceModel(
      view: _view(agent: agent),
      agent: agent,
      chatController: chatController,
      isAwaitingAgentResponse: true,
    );

    expect(model.executionStatus?.label, 'Working');
    expect(model.executionStatus?.state, 'working');
    expect(model.executionStatus?.isRefreshing, isFalse);
  });

  test('marks only the latest unfinished reply as working', () {
    final chatController = AgentChatController();
    final view = _view();
    final agent = _agent(
      activityState: 'active',
      activitySource: 'codex_runtime',
      activityReason: 'codex_working_status_line',
    );
    chatController.applyRemoteConversation(
      agentName: agent.name,
      shouldScroll: true,
      conversation: CcbAgentConversation(
        projectId: view.project.id,
        agentName: agent.name,
        namespaceEpoch: view.namespaceEpoch!,
        items: [
          CcbConversationItem(
            id: 'user-1',
            agentName: 'mobile',
            kind: CcbConversationItemKind.userMessage,
            title: 'You',
            body: 'first',
            sentAt: DateTime.utc(2026, 7, 1, 9),
          ),
          CcbConversationItem(
            id: 'reply-1',
            agentName: 'mobile',
            kind: CcbConversationItemKind.agentReply,
            title: 'Agent reply',
            body: 'done',
            sentAt: DateTime.utc(2026, 7, 1, 9, 1),
            completedAt: DateTime.utc(2026, 7, 1, 10),
          ),
          CcbConversationItem(
            id: 'user-2',
            agentName: 'mobile',
            kind: CcbConversationItemKind.userMessage,
            title: 'You',
            body: 'second',
            sentAt: DateTime.utc(2026, 7, 1, 10, 30),
          ),
          CcbConversationItem(
            id: 'reply-2',
            agentName: 'mobile',
            kind: CcbConversationItemKind.agentReply,
            title: 'Agent reply',
            body: 'still streaming',
            startedAt: DateTime.utc(2026, 7, 1, 10, 31),
          ),
        ],
        generatedAt: DateTime.utc(2026, 7, 1),
      ),
    );

    final model = selectedAgentWorkspaceModel(
      view: _view(agent: agent),
      agent: agent,
      chatController: chatController,
      isAwaitingAgentResponse: false,
    );

    expect(model.executionStatus?.state, 'working');
    expect(model.workingReplyItemId, 'reply-2');
    expect(
      model.timelineItems
          .map((item) => item.id)
          .contains(syntheticAgentWorkingConversationItemId(agent.name)),
      isFalse,
    );
  });

  test('shows synthetic working reply when no unfinished reply exists', () {
    final chatController = AgentChatController();
    final view = _view();
    final agent = _agent(
      activityState: 'active',
      activitySource: 'codex_runtime',
      activityReason: 'codex_working_status_line',
    );
    chatController.applyRemoteConversation(
      agentName: agent.name,
      shouldScroll: true,
      conversation: CcbAgentConversation(
        projectId: view.project.id,
        agentName: agent.name,
        namespaceEpoch: view.namespaceEpoch!,
        items: [
          CcbConversationItem(
            id: 'user-1',
            agentName: 'mobile',
            kind: CcbConversationItemKind.userMessage,
            title: 'You',
            body: 'old request',
            sentAt: DateTime.utc(2026, 7, 1, 10),
          ),
          CcbConversationItem(
            id: 'reply-1',
            agentName: 'mobile',
            kind: CcbConversationItemKind.agentReply,
            title: 'Agent reply',
            body: 'old answer from legacy history',
            completedAt: DateTime.utc(2026, 7, 1, 10, 6),
            durationMs: 6000,
          ),
        ],
        generatedAt: DateTime.utc(2026, 7, 1),
      ),
    );

    final model = selectedAgentWorkspaceModel(
      view: _view(agent: agent),
      agent: agent,
      chatController: chatController,
      isAwaitingAgentResponse: false,
    );

    expect(model.executionStatus?.state, 'working');
    expect(
      model.workingReplyItemId,
      syntheticAgentWorkingConversationItemId(agent.name),
    );
    expect(model.timelineItems.map((item) => item.id), [
      'user-1',
      'reply-1',
      syntheticAgentWorkingConversationItemId(agent.name),
    ]);
    final completedReply = model.timelineItems[1];
    expect(completedReply.completedAt, DateTime.utc(2026, 7, 1, 10, 6));
    expect(completedReply.durationMs, 6000);
    final placeholder = model.timelineItems.last;
    expect(placeholder.kind, CcbConversationItemKind.agentReply);
    expect(placeholder.body, 'Working...');
    expect(placeholder.startedAt, DateTime.utc(2026, 7, 1, 10));
    expect(placeholder.completedAt, isNull);
  });

  test(
    'uses current native reply as working target despite early completion',
    () {
      final chatController = AgentChatController();
      final view = _view();
      final agent = _agent(
        activityState: 'active',
        activitySource: 'codex_runtime',
        activityReason: 'codex_working_status_line',
      );
      chatController.applyRemoteConversation(
        agentName: agent.name,
        shouldScroll: true,
        conversation: CcbAgentConversation(
          projectId: view.project.id,
          agentName: agent.name,
          namespaceEpoch: view.namespaceEpoch!,
          items: [
            CcbConversationItem(
              id: 'user-current',
              agentName: agent.name,
              kind: CcbConversationItemKind.userMessage,
              title: 'You',
              body: 'new request',
              sentAt: DateTime.utc(2026, 7, 2, 9),
            ),
            CcbConversationItem(
              id: 'reply-current',
              agentName: agent.name,
              kind: CcbConversationItemKind.agentReply,
              title: 'Agent reply',
              body: 'visible live reply text',
              source: 'provider_native/codex',
              sentAt: DateTime.utc(2026, 7, 2, 9, 0, 2),
              startedAt: DateTime.utc(2026, 7, 2, 9, 0, 1),
              completedAt: DateTime.utc(2026, 7, 2, 9, 0, 2),
              durationMs: 1000,
            ),
          ],
          generatedAt: DateTime.utc(2026, 7, 2),
        ),
      );

      final model = selectedAgentWorkspaceModel(
        view: _view(agent: agent),
        agent: agent,
        chatController: chatController,
        isAwaitingAgentResponse: false,
      );

      expect(model.executionStatus?.state, 'working');
      final presentationId = syntheticAgentWorkingConversationItemId(
        agent.name,
      );
      expect(model.workingReplyItemId, presentationId);
      expect(model.timelineItems.map((item) => item.id), [
        'user-current',
        presentationId,
      ]);
      expect(model.timelineItems.last.body, 'visible live reply text');
    },
  );

  test('moves working presentation identity to the newest reply turn', () {
    final chatController = AgentChatController();
    final view = _view();
    final agent = _agent(
      activityState: 'active',
      activitySource: 'codex_runtime',
      activityReason: 'codex_working_status_line',
    );
    final presentationId = syntheticAgentWorkingConversationItemId(agent.name);

    chatController.applyRemoteConversation(
      agentName: agent.name,
      shouldScroll: true,
      conversation: CcbAgentConversation(
        projectId: view.project.id,
        agentName: agent.name,
        namespaceEpoch: view.namespaceEpoch!,
        items: [
          CcbConversationItem(
            id: 'user-1',
            agentName: agent.name,
            kind: CcbConversationItemKind.userMessage,
            title: 'You',
            body: 'first request',
            sentAt: DateTime.utc(2026, 7, 16, 8),
          ),
          CcbConversationItem(
            id: 'reply-1',
            agentName: agent.name,
            kind: CcbConversationItemKind.agentReply,
            title: 'Agent reply',
            body: 'first visible reply',
            source: 'provider_native/codex',
            startedAt: DateTime.utc(2026, 7, 16, 8, 0, 1),
            completedAt: DateTime.utc(2026, 7, 16, 8, 0, 2),
          ),
        ],
        generatedAt: DateTime.utc(2026, 7, 16, 8, 0, 2),
      ),
    );
    final firstTurn = selectedAgentWorkspaceModel(
      view: _view(agent: agent),
      agent: agent,
      chatController: chatController,
      isAwaitingAgentResponse: false,
    );
    expect(firstTurn.timelineItems.last.id, presentationId);

    chatController.applyRemoteConversation(
      agentName: agent.name,
      shouldScroll: true,
      conversation: CcbAgentConversation(
        projectId: view.project.id,
        agentName: agent.name,
        namespaceEpoch: view.namespaceEpoch!,
        items: [
          ...chatController.remoteConversationFor(agent.name)!.items,
          CcbConversationItem(
            id: 'user-2',
            agentName: agent.name,
            kind: CcbConversationItemKind.userMessage,
            title: 'You',
            body: 'second request',
            sentAt: DateTime.utc(2026, 7, 16, 8, 1),
          ),
        ],
        generatedAt: DateTime.utc(2026, 7, 16, 8, 1),
      ),
    );

    final secondTurnPlaceholder = selectedAgentWorkspaceModel(
      view: _view(agent: agent),
      agent: agent,
      chatController: chatController,
      isAwaitingAgentResponse: false,
    );
    expect(
      secondTurnPlaceholder.timelineItems.where(
        (item) => item.id == presentationId,
      ),
      hasLength(1),
    );
    expect(secondTurnPlaceholder.timelineItems.last.body, 'Working...');
    expect(
      secondTurnPlaceholder.timelineItems
          .singleWhere((item) => item.body == 'first visible reply')
          .id,
      'reply-1',
    );

    chatController.applyRemoteConversation(
      agentName: agent.name,
      shouldScroll: true,
      conversation: CcbAgentConversation(
        projectId: view.project.id,
        agentName: agent.name,
        namespaceEpoch: view.namespaceEpoch!,
        items: [
          ...chatController.remoteConversationFor(agent.name)!.items,
          CcbConversationItem(
            id: 'reply-2',
            agentName: agent.name,
            kind: CcbConversationItemKind.agentReply,
            title: 'Agent reply',
            body: 'second visible reply',
            source: 'provider_native/codex',
            startedAt: DateTime.utc(2026, 7, 16, 8, 1, 1),
            completedAt: DateTime.utc(2026, 7, 16, 8, 1, 2),
          ),
        ],
        generatedAt: DateTime.utc(2026, 7, 16, 8, 1, 2),
      ),
    );

    final secondTurn = selectedAgentWorkspaceModel(
      view: _view(agent: agent),
      agent: agent,
      chatController: chatController,
      isAwaitingAgentResponse: false,
    );

    expect(secondTurn.workingReplyItemId, presentationId);
    expect(
      secondTurn.timelineItems.where((item) => item.id == presentationId),
      hasLength(1),
    );
    expect(
      secondTurn.timelineItems
          .singleWhere((item) => item.body == 'first visible reply')
          .id,
      'reply-1',
    );
    expect(
      secondTurn.timelineItems
          .singleWhere((item) => item.id == presentationId)
          .body,
      'second visible reply',
    );
  });

  test(
    'keeps working element identity from placeholder through completion',
    () {
      final chatController = AgentChatController();
      final workingAgent = _agent(
        activityState: 'active',
        activitySource: 'codex_runtime',
        activityReason: 'codex_working_status_line',
      );
      final view = _view(agent: workingAgent);
      final presentationId = syntheticAgentWorkingConversationItemId(
        workingAgent.name,
      );

      final placeholderModel = selectedAgentWorkspaceModel(
        view: view,
        agent: workingAgent,
        chatController: chatController,
        isAwaitingAgentResponse: false,
      );
      expect(placeholderModel.timelineItems.single.id, presentationId);
      expect(placeholderModel.timelineItems.single.body, 'Working...');

      chatController.applyRemoteConversation(
        agentName: workingAgent.name,
        shouldScroll: true,
        conversation: CcbAgentConversation(
          projectId: view.project.id,
          agentName: workingAgent.name,
          namespaceEpoch: view.namespaceEpoch!,
          items: [
            CcbConversationItem(
              id: 'user-current',
              agentName: workingAgent.name,
              kind: CcbConversationItemKind.userMessage,
              title: 'You',
              body: 'new request',
              sentAt: DateTime.utc(2026, 7, 2, 9),
            ),
            CcbConversationItem(
              id: 'reply-current',
              agentName: workingAgent.name,
              kind: CcbConversationItemKind.agentReply,
              title: 'Agent reply',
              body: 'visible live reply text',
              source: 'provider_native/codex',
              startedAt: DateTime.utc(2026, 7, 2, 9, 0, 1),
              completedAt: DateTime.utc(2026, 7, 2, 9, 0, 2),
            ),
          ],
          generatedAt: DateTime.utc(2026, 7, 2),
        ),
      );

      final liveReplyModel = selectedAgentWorkspaceModel(
        view: view,
        agent: workingAgent,
        chatController: chatController,
        isAwaitingAgentResponse: false,
      );
      expect(liveReplyModel.timelineItems.last.id, presentationId);
      expect(liveReplyModel.timelineItems.last.body, 'visible live reply text');
      expect(liveReplyModel.workingReplyItemId, presentationId);

      final idleAgent = _agent(
        activityState: 'idle',
        activitySource: 'codex_runtime',
        activityReason: 'codex_session_task_complete',
      );
      final completedModel = selectedAgentWorkspaceModel(
        view: _view(agent: idleAgent),
        agent: idleAgent,
        chatController: chatController,
        isAwaitingAgentResponse: false,
      );
      expect(completedModel.timelineItems.last.id, presentationId);
      expect(completedModel.workingReplyItemId, isNull);
    },
  );

  test('does not put terminal output in the default working timeline', () {
    final chatController = AgentChatController();
    final view = _view();
    final agent = _agent(
      activityState: 'active',
      activitySource: 'codex_runtime',
      activityReason: 'codex_working_status_line',
    );
    chatController.applyRemoteConversation(
      agentName: agent.name,
      shouldScroll: true,
      conversation: CcbAgentConversation(
        projectId: view.project.id,
        agentName: agent.name,
        namespaceEpoch: view.namespaceEpoch!,
        items: const [
          CcbConversationItem(
            id: 'terminal-history-1',
            agentName: 'mobile',
            kind: CcbConversationItemKind.agentReply,
            title: 'Log',
            body: 'Brewed for 5s',
            source: 'tmux output / tmux_scrollback / %2',
          ),
        ],
        generatedAt: DateTime.utc(2026, 7, 1),
      ),
    );

    final model = selectedAgentWorkspaceModel(
      view: _view(agent: agent),
      agent: agent,
      chatController: chatController,
      isAwaitingAgentResponse: false,
    );

    expect(model.executionStatus?.state, 'working');
    expect(
      model.workingReplyItemId,
      syntheticAgentWorkingConversationItemId(agent.name),
    );
  });

  test(
    'shows synthetic working reply instead of stale reply after latest user',
    () {
      final chatController = AgentChatController();
      final view = _view();
      final agent = _agent(
        activityState: 'active',
        activitySource: 'codex_runtime',
        activityReason: 'codex_working_status_line',
      );
      chatController.applyRemoteConversation(
        agentName: agent.name,
        shouldScroll: true,
        conversation: CcbAgentConversation(
          projectId: view.project.id,
          agentName: agent.name,
          namespaceEpoch: view.namespaceEpoch!,
          items: const [
            CcbConversationItem(
              id: 'reply-1',
              agentName: 'mobile',
              kind: CcbConversationItemKind.agentReply,
              title: 'Agent reply',
              body: 'old answer',
            ),
            CcbConversationItem(
              id: 'user-2',
              agentName: 'mobile',
              kind: CcbConversationItemKind.userMessage,
              title: 'You',
              body: 'new request',
            ),
          ],
          generatedAt: DateTime.utc(2026, 7, 1),
        ),
      );

      final model = selectedAgentWorkspaceModel(
        view: _view(agent: agent),
        agent: agent,
        chatController: chatController,
        isAwaitingAgentResponse: false,
      );

      expect(model.executionStatus?.state, 'working');
      expect(
        model.workingReplyItemId,
        syntheticAgentWorkingConversationItemId(agent.name),
      );
      expect(model.timelineItems.map((item) => item.id), [
        'reply-1',
        'user-2',
        syntheticAgentWorkingConversationItemId(agent.name),
      ]);
    },
  );

  test('does not show synthetic working reply when idle', () {
    final chatController = AgentChatController();
    final view = _view();
    final agent = _agent(
      activityState: 'idle',
      activitySource: 'provider_pane',
      activityReason: 'provider_prompt_idle',
    );
    chatController.applyRemoteConversation(
      agentName: agent.name,
      shouldScroll: true,
      conversation: CcbAgentConversation(
        projectId: view.project.id,
        agentName: agent.name,
        namespaceEpoch: view.namespaceEpoch!,
        items: const [
          CcbConversationItem(
            id: 'reply-1',
            agentName: 'mobile',
            kind: CcbConversationItemKind.agentReply,
            title: 'Agent reply',
            body: 'old answer',
          ),
        ],
        generatedAt: DateTime.utc(2026, 7, 1),
      ),
    );

    final model = selectedAgentWorkspaceModel(
      view: _view(agent: agent),
      agent: agent,
      chatController: chatController,
      isAwaitingAgentResponse: false,
    );

    expect(model.executionStatus?.state, 'idle');
    expect(model.workingReplyItemId, isNull);
    expect(model.timelineItems.map((item) => item.id), ['reply-1']);
  });

  test('does not show synthetic working reply for exception status', () {
    final chatController = AgentChatController();
    final view = _view();
    final agent = _agent(
      activityState: 'failed',
      activitySource: 'provider_pane',
      activityReason: 'conversation_interrupted',
    );
    chatController.applyRemoteConversation(
      agentName: agent.name,
      shouldScroll: true,
      conversation: CcbAgentConversation(
        projectId: view.project.id,
        agentName: agent.name,
        namespaceEpoch: view.namespaceEpoch!,
        items: const [
          CcbConversationItem(
            id: 'reply-1',
            agentName: 'mobile',
            kind: CcbConversationItemKind.agentReply,
            title: 'Agent reply',
            body: 'old answer',
          ),
        ],
        generatedAt: DateTime.utc(2026, 7, 1),
      ),
    );

    final model = selectedAgentWorkspaceModel(
      view: _view(agent: agent),
      agent: agent,
      chatController: chatController,
      isAwaitingAgentResponse: false,
    );

    expect(model.executionStatus?.state, 'exception');
    expect(model.workingReplyItemId, isNull);
    expect(model.timelineItems.map((item) => item.id), ['reply-1']);
  });

  test('marks running reply when reply starts after local user send time', () {
    final chatController = AgentChatController();
    final view = _view();
    final agent = _agent(
      activityState: 'active',
      activitySource: 'codex_runtime',
      activityReason: 'codex_working_status_line',
    );
    final userSentAt = DateTime.utc(2026, 7, 1, 10, 0, 0);
    final replyStartedAt = userSentAt.add(const Duration(seconds: 1));

    chatController.addLocalMessage(
      agent.name,
      CcbConversationItem.userMessage(
        id: 'local-user',
        agentName: agent.name,
        body: 'new request',
        sentAt: userSentAt,
        state: CcbConversationDeliveryState.sent,
      ),
    );
    chatController.applyRemoteConversation(
      agentName: agent.name,
      shouldScroll: true,
      conversation: CcbAgentConversation(
        projectId: view.project.id,
        agentName: agent.name,
        namespaceEpoch: view.namespaceEpoch!,
        items: [
          CcbConversationItem(
            id: 'reply-running',
            agentName: agent.name,
            kind: CcbConversationItemKind.agentReply,
            title: 'Agent reply',
            body: 'still running',
            source: 'provider_native/codex',
            startedAt: replyStartedAt,
          ),
        ],
        generatedAt: replyStartedAt,
      ),
    );

    final model = selectedAgentWorkspaceModel(
      view: _view(agent: agent),
      agent: agent,
      chatController: chatController,
      isAwaitingAgentResponse: true,
    );

    expect(model.executionStatus?.state, 'working');
    expect(
      model.workingReplyItemId,
      syntheticAgentWorkingConversationItemId(agent.name),
    );
  });
}

CcbProjectView _view({CcbAgent? agent}) {
  final resolvedAgent = agent ?? _agent();
  return CcbProjectView(
    project: const CcbProject(
      id: 'proj',
      displayName: 'Project',
      root: '/tmp/proj',
    ),
    namespaceEpoch: 7,
    tmuxSocketPath: null,
    tmuxSessionName: null,
    activeWindow: 'main',
    activePaneId: null,
    windows: const [],
    agents: [resolvedAgent],
    contentItems: const [],
    notifications: const [],
    terminalHistories: const {},
  );
}

CcbAgent _agent({
  String provider = 'codex',
  String? activityState,
  String? activitySource,
  String? activityReason,
}) {
  return CcbAgent(
    name: 'mobile',
    provider: provider,
    window: 'main',
    order: 0,
    active: true,
    queueDepth: 0,
    activityState: activityState,
    activitySource: activitySource,
    activityReason: activityReason,
  );
}
