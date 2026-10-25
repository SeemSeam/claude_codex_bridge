import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';

import 'package:ccb_mobile/ccb_mobile.dart';
import 'package:ccb_mobile/features/agent_chat/selected_agent_workspace_model.dart';
import 'package:ccb_mobile/features/agent_chat/selected_agent_workspace_view.dart';

void main() {
  testWidgets('default chat excludes comms updates and terminal status cards', (
    tester,
  ) async {
    final scrollController = ScrollController();
    final draftController = TextEditingController();
    final focusNode = FocusNode();
    addTearDown(scrollController.dispose);
    addTearDown(draftController.dispose);
    addTearDown(focusNode.dispose);

    final agent = _agent();
    final workingPlaceholder = syntheticAgentWorkingConversationItem(
      agent.name,
    );
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SizedBox(
            height: 520,
            child: SelectedAgentWorkspaceView(
              repository: FakeMobileCcbRepository.demo(),
              view: _view(),
              model: SelectedAgentWorkspaceModel(
                agent: agent,
                contentItems: const [],
                initialHistory: null,
                timelineItems: [
                  CcbConversationItem(
                    id: 'reply-1',
                    agentName: agent.name,
                    kind: CcbConversationItemKind.agentReply,
                    title: 'Agent reply',
                    body: 'real backend answer',
                    source: 'completion_snapshot',
                  ),
                  workingPlaceholder,
                ],
                commsItems: [
                  CcbConversationItem(
                    id: 'comms-1',
                    agentName: agent.name,
                    kind: CcbConversationItemKind.commsItem,
                    title: 'Comms',
                    body: 'project view updated',
                    source: 'project_view',
                  ),
                ],
                isLoadingConversation: false,
                hasOlderConversation: false,
                expandedItemIds: const {},
                hasNewMessages: false,
                isSending: false,
                isAwaitingAgentResponse: true,
                isComposerCollapsed: false,
                executionStatus: agentExecutionStatus(
                  agent: _agent(),
                  isAwaitingAgentResponse: true,
                ),
                workingReplyItemId: workingPlaceholder.id,
              ),
              timelineController: scrollController,
              draftController: draftController,
              draftFocusNode: focusNode,
              enableComposerCollapse: false,
              onRetry: (_) {},
              onToggleExpanded: (_) {},
              onNearEnd: () {},
              onUserNearEnd: () {},
              onNearStart: () {},
              onUserScrollDirectionChanged: (_) {},
              onJumpToLatest: () {},
              onCollapseComposer: () {},
              onExpandComposer: () {},
              draftAttachments: const [],
              downloadingAttachmentIds: const {},
              downloadedAttachmentIds: const {},
              onPickImageAttachment: () {},
              onPickFileAttachment: () {},
              onRemoveAttachment: (_) {},
              onDownloadAttachment: (_) {},
              onOpenAttachment: (_) {},
              onDeleteFailedMessage: (_) {},
              onSend: () {},
              onQueue: () {},
              onSendEscape: () {},
              onUiResponse: (_) async {},
            ),
          ),
        ),
      ),
    );

    expect(find.byKey(const ValueKey('agent-comms-status')), findsNothing);
    expect(find.text('Communicating'), findsNothing);
    expect(find.byKey(const ValueKey('agent-working-status')), findsNothing);
    expect(
      find.byKey(const ValueKey('conversation-working-status-text')),
      findsOneWidget,
    );
    expect(find.text('project view updated'), findsNothing);
    expect(
      find.byKey(const ValueKey('agent-conversation-refresh-action')),
      findsNothing,
    );
    expect(_composerGap(tester), lessThanOrEqualTo(8));
    expect(
      find.byKey(const ValueKey('conversation-item-comms-1')),
      findsNothing,
    );
    expect(
      find.byKey(const ValueKey('conversation-item-reply-1')),
      findsOneWidget,
    );
  });
}

double _composerGap(WidgetTester tester) {
  final timelineBottom =
      tester
          .getBottomLeft(find.byKey(const ValueKey('agent-chat-timeline')))
          .dy;
  final composerTop =
      tester.getTopLeft(find.byKey(const ValueKey('agent-chat-composer'))).dy;
  return composerTop - timelineBottom;
}

CcbProjectView _view() {
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
    agents: [_agent()],
    contentItems: const [],
    notifications: const [],
    terminalHistories: const {},
  );
}

CcbAgent _agent() {
  return const CcbAgent(
    name: 'mobile',
    provider: 'codex',
    window: 'main',
    order: 0,
    active: true,
    queueDepth: 0,
  );
}
