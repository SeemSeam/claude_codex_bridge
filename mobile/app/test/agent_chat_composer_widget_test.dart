import 'dart:async';
import 'dart:io';

import 'package:file_picker/file_picker.dart';
import 'package:file_picker/src/platform/file_picker_platform_interface.dart';
import 'package:flutter/material.dart';
import 'package:flutter/services.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:xterm/xterm.dart';

import 'package:ccb_mobile/ccb_mobile.dart';
import 'package:ccb_mobile/features/agent_chat/agent_chat_controller.dart';
import 'package:ccb_mobile/features/agent_chat/agent_message_composer.dart';
import 'package:ccb_mobile/features/agent_chat/agent_turn_sync_tracker.dart';
import 'package:ccb_mobile/features/agent_chat/selected_agent_workspace.dart';
import 'package:ccb_mobile/features/agent_chat/selected_agent_workspace_model.dart';
import 'package:ccb_mobile/features/agent_chat/selected_agent_workspace_view.dart';

import 'support/project_home_test_driver.dart';
import 'support/project_home_test_fakes.dart';

void main() {
  testWidgets('OMP queue receipts preserve live awaiting and allow next send', (tester) async {
    await setTestSurfaceSize(tester, const Size(390, 844));
    const agent = CcbAgent(
      name: 'mobile', provider: 'omp', window: 'main', order: 0,
      active: true, queueDepth: 0,
      providerControl: CcbProviderControl(provider: 'omp', runtimeSource: 'collab'),
    );
    final repository = _QueuedCollabRepository();
    await tester.pumpWidget(MaterialApp(home: Scaffold(body: SelectedAgentWorkspace(
      repository: repository, terminalTransport: null,
      usePaneInputForMessages: true, view: _workspaceView(agent), agent: agent,
      enableComposerCollapse: false, onRefreshView: null,
    ))));
    await tester.pumpAndSettle();
    for (final body in ['first', 'second', 'third']) {
      await tester.enterText(find.byKey(const ValueKey('agent-message-composer')), body);
      await tester.tap(find.byKey(const ValueKey('agent-message-send-button')));
      await tester.pumpAndSettle();
      final workspace = tester.widget<SelectedAgentWorkspaceView>(find.byType(SelectedAgentWorkspaceView));
      expect(workspace.model.isAwaitingAgentResponse, isTrue);
    }
    expect(repository.submittedMessages, hasLength(3));
    expect(find.text('Queued'), findsNWidgets(2));
    await tester.pumpWidget(const SizedBox.shrink());
  });
  testWidgets(
    'Mac legacy OMP sends without generation and keeps terminal keys without polling',
    (tester) async {
      await setTestSurfaceSize(tester, const Size(390, 844));
      const agent = CcbAgent(
        name: 'mobile',
        provider: 'omp',
        window: 'main',
        order: 0,
        active: true,
        queueDepth: 0,
      );
      final repository = RecordingGatewayRepository();
      final terminal = RecordingTerminalTransport();
      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: SelectedAgentWorkspace(
              repository: repository,
              terminalTransport: terminal,
              usePaneInputForMessages: true,
              view: _workspaceView(agent),
              agent: agent,
              enableComposerCollapse: false,
              onRefreshView: null,
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();
      final initial = repository.conversationCalls.length;
      await tester.pump(const Duration(seconds: 6));
      await tester.pumpAndSettle();
      expect(repository.conversationCalls.length, initial);
      expect(
        find.byKey(const ValueKey('omp-collab-disconnected')),
        findsNothing,
      );
      final input = find.byKey(const ValueKey('agent-message-composer'));
      await tester.enterText(input, 'legacy terminal prompt');
      await tester.tap(find.byKey(const ValueKey('agent-message-send-button')));
      await tester.pumpAndSettle();
      expect(repository.submittedMessages, isEmpty);
      expect(terminal.sessions.single.pasted, ['legacy terminal prompt']);
      expect(terminal.sessions.single.written, [
        [13],
      ]);
      await tester.tap(input);
      await tester.pump();
      expect(find.byKey(const ValueKey('agent-queue-button')), findsNothing);
      await tester.tap(find.byKey(const ValueKey('agent-quick-key-esc')));
      await tester.pumpAndSettle();
      expect(terminal.sessions.single.written, [
        [13],
        [27],
      ]);
      await tester.pumpWidget(const SizedBox.shrink());
    },
  );

  testWidgets('only visible OMP Collab refreshes and pauses in background', (
    tester,
  ) async {
    const agent = CcbAgent(
      name: 'mobile',
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
    final repository = RecordingGatewayRepository();
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SelectedAgentWorkspace(
            repository: repository,
            terminalTransport: null,
            usePaneInputForMessages: true,
            view: _workspaceView(agent),
            agent: agent,
            enableComposerCollapse: false,
            onRefreshView: null,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();
    final initial = repository.conversationCalls.length;
    await tester.pump(const Duration(seconds: 5));
    await tester.pumpAndSettle();
    expect(repository.conversationCalls.length, greaterThan(initial));
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.hidden);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.paused);
    final paused = repository.conversationCalls.length;
    await tester.pump(const Duration(seconds: 5));
    expect(repository.conversationCalls.length, paused);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.hidden);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.inactive);
    tester.binding.handleAppLifecycleStateChanged(AppLifecycleState.resumed);
    await tester.pump(const Duration(seconds: 5));
    await tester.pumpAndSettle();
    expect(repository.conversationCalls.length, greaterThan(paused));
    await tester.pumpWidget(const SizedBox.shrink());
    final disposed = repository.conversationCalls.length;
    await tester.pump(const Duration(seconds: 5));
    expect(repository.conversationCalls.length, disposed);
  });
  testWidgets('message composer sends on hardware enter', (tester) async {
    final controller = TextEditingController(text: 'hello mobile');
    var sendCount = 0;

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: AgentMessageComposer(
            agentName: 'mobile',
            controller: controller,
            isSending: false,
            collapsible: false,
            collapsed: false,
            onCollapse: () {},
            onExpand: () {},
            draftAttachments: const [],
            onPickImage: () {},
            onPickFile: () {},
            onRemoveAttachment: (_) {},
            onSend: () {
              sendCount += 1;
            },
            onQueue: () {},
            onSendEscape: () {},
          ),
        ),
      ),
    );

    await tester.tap(find.byKey(const ValueKey('agent-message-composer')));
    await tester.pump();
    await tester.sendKeyEvent(LogicalKeyboardKey.enter);
    await tester.pump();

    expect(sendCount, 1);
  });

  testWidgets('message composer ignores hardware enter while sending', (
    tester,
  ) async {
    final controller = TextEditingController(text: 'hello mobile');
    var sendCount = 0;

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: AgentMessageComposer(
            agentName: 'mobile',
            controller: controller,
            isSending: true,
            collapsible: false,
            collapsed: false,
            onCollapse: () {},
            onExpand: () {},
            draftAttachments: const [],
            onPickImage: () {},
            onPickFile: () {},
            onRemoveAttachment: (_) {},
            onSend: () {
              sendCount += 1;
            },
            onQueue: () {},
            onSendEscape: () {},
          ),
        ),
      ),
    );

    await tester.tap(find.byKey(const ValueKey('agent-message-composer')));
    await tester.pump();
    await tester.sendKeyEvent(LogicalKeyboardKey.enter);
    await tester.pump();

    expect(sendCount, 0);
  });

  testWidgets('agent status prioritizes working over refresh after send', (
    tester,
  ) async {
    final agent = CcbAgent(
      name: 'mobile',
      provider: 'codex',
      window: 'main',
      order: 0,
      active: true,
      queueDepth: 0,
    );
    final draftController = TextEditingController();
    final focusNode = FocusNode();
    final timelineController = ScrollController();
    final workingPlaceholder = syntheticAgentWorkingConversationItem(
      agent.name,
    );
    addTearDown(draftController.dispose);
    addTearDown(focusNode.dispose);
    addTearDown(timelineController.dispose);

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SelectedAgentWorkspaceView(
            repository: FakeMobileCcbRepository.demo(),
            view: _workspaceView(agent),
            model: SelectedAgentWorkspaceModel(
              agent: agent,
              contentItems: const [],
              initialHistory: null,
              timelineItems: [workingPlaceholder],
              commsItems: const [],
              isLoadingConversation: true,
              hasOlderConversation: false,
              expandedItemIds: const {},
              hasNewMessages: false,
              isSending: true,
              isAwaitingAgentResponse: true,
              isComposerCollapsed: false,
              executionStatus: agentExecutionStatus(
                agent: agent,
                isAwaitingAgentResponse: true,
              ),
              workingReplyItemId: workingPlaceholder.id,
            ),
            timelineController: timelineController,
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
    );

    expect(find.byKey(const ValueKey('agent-working-status')), findsNothing);
    expect(
      find.byKey(const ValueKey('conversation-working-status-text')),
      findsOneWidget,
    );
    expect(find.text('Idle'), findsNothing);
  });

  testWidgets(
    'workspace shows synthetic working bubble without running reply',
    (tester) async {
      final agent = CcbAgent(
        name: 'mobile',
        provider: 'codex',
        window: 'main',
        order: 0,
        active: true,
        queueDepth: 0,
        activityState: 'active',
        activitySource: 'codex_runtime',
        activityReason: 'codex_working_status_line',
      );
      final chatController = AgentChatController();
      final view = _workspaceView(agent);
      chatController.applyRemoteConversation(
        agentName: agent.name,
        shouldScroll: true,
        conversation: CcbAgentConversation(
          projectId: view.project.id,
          agentName: agent.name,
          namespaceEpoch: view.namespaceEpoch!,
          items: [
            CcbConversationItem(
              id: 'reply-completed',
              agentName: agent.name,
              kind: CcbConversationItemKind.agentReply,
              title: 'Agent reply',
              body: 'completed before status poll',
              completedAt: DateTime.utc(2026, 7, 2, 8, 30),
              durationMs: 90000,
            ),
          ],
          generatedAt: DateTime.utc(2026, 7, 2),
        ),
      );
      final model = selectedAgentWorkspaceModel(
        view: view,
        agent: agent,
        chatController: chatController,
        isAwaitingAgentResponse: false,
      );
      final draftController = TextEditingController();
      final focusNode = FocusNode();
      final timelineController = ScrollController();
      addTearDown(draftController.dispose);
      addTearDown(focusNode.dispose);
      addTearDown(timelineController.dispose);

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: SelectedAgentWorkspaceView(
              repository: FakeMobileCcbRepository.demo(),
              view: view,
              model: model,
              timelineController: timelineController,
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
      );
      await tester.pump();

      final placeholderId = syntheticAgentWorkingConversationItemId(agent.name);
      expect(find.text('completed before status poll'), findsOneWidget);
      expect(find.text('Working...'), findsOneWidget);
      expect(
        find.byKey(ValueKey('conversation-working-$placeholderId')),
        findsOneWidget,
      );
      expect(
        find.byKey(ValueKey('conversation-working-beat-$placeholderId')),
        findsOneWidget,
      );
      expect(
        find.byKey(const ValueKey('conversation-working-status-text')),
        findsOneWidget,
      );
      expect(find.byKey(const ValueKey('agent-working-status')), findsNothing);
      expect(
        find.byKey(const ValueKey('conversation-working-reply-completed')),
        findsNothing,
      );
    },
  );

  testWidgets('message composer shows focused quick key toolbar', (
    tester,
  ) async {
    await setTestSurfaceSize(tester, const Size(390, 844));

    final controller = TextEditingController();
    var tabCount = 0;
    var escapeCount = 0;

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: AgentMessageComposer(
            agentName: 'mobile',
            controller: controller,
            isSending: false,
            collapsible: false,
            collapsed: false,
            onCollapse: () {},
            onExpand: () {},
            draftAttachments: const [],
            onPickImage: () {},
            onPickFile: () {},
            onRemoveAttachment: (_) {},
            onSend: () {},
            onQueue: () {
              tabCount += 1;
            },
            onSendEscape: () {
              escapeCount += 1;
            },
          ),
        ),
      ),
    );

    expect(find.byKey(const ValueKey('agent-quick-key-toolbar')), findsNothing);
    expect(find.byKey(const ValueKey('agent-queue-button')), findsNothing);
    expect(find.byKey(const ValueKey('agent-quick-key-esc')), findsNothing);

    await tester.tap(find.byKey(const ValueKey('agent-message-composer')));
    await tester.pump();

    final toolbar = find.byKey(const ValueKey('agent-quick-key-toolbar'));
    final composer = find.byKey(const ValueKey('agent-message-composer'));
    expect(toolbar, findsOneWidget);
    expect(find.byKey(const ValueKey('agent-queue-button')), findsOneWidget);
    expect(find.byKey(const ValueKey('agent-quick-key-esc')), findsOneWidget);
    expect(
      tester.getBottomLeft(toolbar).dy,
      lessThanOrEqualTo(tester.getTopLeft(composer).dy),
    );

    await tester.tap(find.byKey(const ValueKey('agent-queue-button')));
    await tester.pump();
    await tester.tap(find.byKey(const ValueKey('agent-quick-key-esc')));
    await tester.pump();

    expect(tabCount, 1);
    expect(escapeCount, 1);

    FocusManager.instance.primaryFocus?.unfocus();
    await tester.pump();
    expect(find.byKey(const ValueKey('agent-quick-key-toolbar')), findsNothing);
  });

  testWidgets('workspace tap outside composer collapses input and quick keys', (
    tester,
  ) async {
    await setTestSurfaceSize(tester, const Size(390, 844));

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SelectedAgentWorkspace(
            repository: FakeMobileCcbRepository.demo(),
            terminalTransport: null,
            usePaneInputForMessages: false,
            view: _workspaceView(
              const CcbAgent(
                name: 'mobile',
                provider: 'codex',
                window: 'main',
                order: 0,
                active: true,
                queueDepth: 0,
              ),
            ),
            agent: const CcbAgent(
              name: 'mobile',
              provider: 'codex',
              window: 'main',
              order: 0,
              active: true,
              queueDepth: 0,
            ),
            enableComposerCollapse: true,
            onRefreshView: null,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const ValueKey('agent-message-composer')));
    await tester.pump();

    expect(find.byKey(const ValueKey('agent-chat-composer')), findsOneWidget);
    expect(
      find.byKey(const ValueKey('agent-quick-key-toolbar')),
      findsOneWidget,
    );

    await tester.tap(
      find.byKey(const ValueKey('agent-compose-dismiss-region')),
    );
    await tester.pumpAndSettle();

    expect(
      find.byKey(const ValueKey('agent-chat-composer-collapsed')),
      findsOneWidget,
    );
    expect(find.byKey(const ValueKey('agent-quick-key-toolbar')), findsNothing);
  });

  testWidgets('refresh updates codex execution status from project view', (
    tester,
  ) async {
    await tester.pumpWidget(
      const MaterialApp(home: Scaffold(body: _WorkspaceRefreshStatusHarness())),
    );
    await tester.pumpAndSettle();

    expect(find.byKey(const ValueKey('agent-working-status')), findsNothing);
    expect(find.text('Idle'), findsNothing);
    expect(find.text('Working'), findsNothing);

    await tester.tap(find.byKey(const ValueKey('test-header-refresh-action')));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));

    expect(find.byKey(const ValueKey('agent-working-status')), findsNothing);
    expect(find.text('Working'), findsOneWidget);
  });

  testWidgets('refresh prioritizes interrupted codex status over working', (
    tester,
  ) async {
    await tester.pumpWidget(
      const MaterialApp(
        home: Scaffold(
          body: _WorkspaceRefreshStatusHarness(
            refreshActivityReason: 'conversation_interrupted',
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.byKey(const ValueKey('agent-working-status')), findsNothing);
    expect(find.text('Idle'), findsNothing);

    await tester.tap(find.byKey(const ValueKey('test-header-refresh-action')));
    await tester.pump();
    await tester.pump(const Duration(milliseconds: 100));

    expect(find.byKey(const ValueKey('agent-working-status')), findsNothing);
    expect(find.text('Working'), findsNothing);
  });

  testWidgets('message composer shows attachment tray and picker sheet', (
    tester,
  ) async {
    final controller = TextEditingController();
    var pickedFile = false;
    var pickedImage = false;
    String? removed;

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: AgentMessageComposer(
            agentName: 'mobile',
            controller: controller,
            isSending: false,
            collapsible: false,
            collapsed: false,
            onCollapse: () {},
            onExpand: () {},
            draftAttachments: const [
              CcbMessageAttachment(
                fileId: 'draft-1',
                fileName: 'very-long-notes-file-name.txt',
                mimeType: 'text/plain',
                sizeBytes: 2048,
                state: CcbMessageAttachmentState.queued,
              ),
            ],
            onPickImage: () {
              pickedImage = true;
            },
            onPickFile: () {
              pickedFile = true;
            },
            onRemoveAttachment: (localId) {
              removed = localId;
            },
            onSend: () {},
            onQueue: () {},
            onSendEscape: () {},
          ),
        ),
      ),
    );

    expect(find.byKey(const ValueKey('agent-attachment-tray')), findsOneWidget);
    expect(
      find.byKey(const ValueKey('agent-attachment-chip-draft-1')),
      findsOneWidget,
    );

    await tester.tap(find.byKey(const ValueKey('agent-attachment-button')));
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('agent-attachment-sheet')),
      findsOneWidget,
    );

    await tester.tap(find.byKey(const ValueKey('agent-attachment-pick-file')));
    await tester.pumpAndSettle();
    expect(pickedFile, isTrue);
    expect(pickedImage, isFalse);

    await tester.tap(
      find.byKey(const ValueKey('agent-attachment-remove-draft-1')),
    );
    expect(removed, 'draft-1');
  });

  testWidgets('file picker cancel keeps attachment draft empty', (
    tester,
  ) async {
    final originalPicker = FilePickerPlatform.instance;
    FilePickerPlatform.instance = _FakeFilePicker([null]);
    addTearDown(() {
      FilePickerPlatform.instance = originalPicker;
    });

    await tester.pumpWidget(
      MaterialApp(
        home: ProjectHomeScreen(repository: FakeMobileCcbRepository.demo()),
      ),
    );
    await tester.pumpAndSettle();
    await openCurrentProject(tester);

    await tester.tap(find.byKey(const ValueKey('agent-attachment-button')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('agent-attachment-pick-file')));
    await tester.pumpAndSettle();

    expect(find.byKey(const ValueKey('agent-attachment-tray')), findsNothing);
    expect(find.text('Attach up to 5 files'), findsNothing);
  });

  testWidgets('file picker enforces max attachments and keeps accepted files', (
    tester,
  ) async {
    final originalPicker = FilePickerPlatform.instance;
    final tempDir = Directory.systemTemp.createTempSync(
      'ccb-mobile-picker-max-',
    );
    addTearDown(() {
      FilePickerPlatform.instance = originalPicker;
      tempDir.deleteSync(recursive: true);
    });
    FilePickerPlatform.instance = _FakeFilePicker([
      FilePickerResult([
        for (var index = 0; index < 6; index += 1)
          PlatformFile(
            name: 'notes-$index.txt',
            path: _tempFile(tempDir, 'notes-$index.txt').path,
            size: 12,
          ),
      ]),
    ]);

    await tester.pumpWidget(
      MaterialApp(
        home: ProjectHomeScreen(repository: FakeMobileCcbRepository.demo()),
      ),
    );
    await tester.pumpAndSettle();
    await openCurrentProject(tester);

    await tester.tap(find.byKey(const ValueKey('agent-attachment-button')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('agent-attachment-pick-file')));
    await tester.pumpAndSettle();
    await _waitForFinder(
      tester,
      find.byKey(const ValueKey('agent-attachment-tray')),
    );

    expect(find.byKey(const ValueKey('agent-attachment-tray')), findsOneWidget);
    for (var index = 0; index < 5; index += 1) {
      expect(
        find.byKey(ValueKey('agent-attachment-chip-draft-mobile-$index')),
        findsOneWidget,
      );
    }
    expect(
      find.byKey(const ValueKey('agent-attachment-chip-draft-mobile-5')),
      findsNothing,
    );
    expect(find.text('Attach up to 5 files'), findsOneWidget);
  });

  testWidgets(
    'image picker accepts extension-only photos and submits uploaded image',
    (tester) async {
      final originalPicker = FilePickerPlatform.instance;
      final tempDir = Directory.systemTemp.createTempSync(
        'ccb-mobile-picker-image-',
      );
      final image = File('${tempDir.path}/picked-image-cache');
      image.writeAsBytesSync([0xff, 0xd8, 0xff, 0xd9]);
      addTearDown(() {
        FilePickerPlatform.instance = originalPicker;
        tempDir.deleteSync(recursive: true);
      });
      FilePickerPlatform.instance = _FakeFilePicker([
        FilePickerResult([
          PlatformFile(
            name: 'camera-roll-image.jpg',
            path: image.path,
            size: image.lengthSync(),
          ),
        ]),
      ]);
      final repository = ImageUploadGatewayRepository();
      final agent = const CcbAgent(
        name: 'mobile',
        provider: 'codex',
        window: 'main',
        order: 0,
        active: true,
        queueDepth: 0,
        paneId: '%2',
      );
      final view = _workspaceView(agent);

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: SelectedAgentWorkspace(
              repository: repository,
              terminalTransport: null,
              usePaneInputForMessages: false,
              view: view,
              agent: agent,
              enableComposerCollapse: true,
              onRefreshView: () async => view,
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      await tester.tap(find.byKey(const ValueKey('agent-attachment-button')));
      await tester.pumpAndSettle();
      await tester.tap(
        find.byKey(const ValueKey('agent-attachment-pick-image')),
      );
      await tester.pumpAndSettle();

      expect(
        find.byKey(
          const ValueKey('agent-attachment-image-preview-draft-mobile-0'),
        ),
        findsOneWidget,
      );
      expect(
        find.text('camera-roll-image.jpg is not a supported attachment type'),
        findsNothing,
      );

      await tester.enterText(
        find.byKey(const ValueKey('agent-message-composer')),
        'please inspect this image',
      );
      await tester.tap(find.byKey(const ValueKey('agent-message-send-button')));
      for (
        var attempt = 0;
        attempt < 20 && repository.pathUploads.isEmpty;
        attempt += 1
      ) {
        await tester.pump(const Duration(milliseconds: 50));
      }

      expect(repository.pathUploads.single.mimeType, 'image/jpeg');
      expect(repository.pathUploads.single.fileName, 'camera-roll-image.jpg');
      for (
        var attempt = 0;
        attempt < 20 && repository.submittedMessages.isEmpty;
        attempt += 1
      ) {
        await tester.pump(const Duration(milliseconds: 50));
      }
      expect(
        repository.submittedMessages.single.body,
        'please inspect this image',
      );
      final submittedAttachment =
          repository.submittedMessages.single.attachments.single;
      expect(submittedAttachment.fileId, 'uploaded-image-1');
      expect(submittedAttachment.fileName, 'camera-roll-image.jpg');
      expect(submittedAttachment.mimeType, 'image/jpeg');
      expect(submittedAttachment.effectiveKind, CcbMessageAttachmentKind.image);
      expect(find.text('Failed'), findsNothing);
    },
  );

  testWidgets(
    'file picker accepts an extension-only MP4 and submits uploaded video',
    (tester) async {
      final originalPicker = FilePickerPlatform.instance;
      final tempDir = Directory.systemTemp.createTempSync(
        'ccb-mobile-picker-video-',
      );
      final video = File('${tempDir.path}/picked-video-cache');
      video.writeAsBytesSync([0x00, 0x00, 0x00, 0x18, 0x66, 0x74, 0x79, 0x70]);
      addTearDown(() {
        FilePickerPlatform.instance = originalPicker;
        tempDir.deleteSync(recursive: true);
      });
      final picker = _FakeFilePicker([
        FilePickerResult([
          PlatformFile(
            name: 'phone-clip.mp4',
            path: video.path,
            size: video.lengthSync(),
          ),
        ]),
      ]);
      FilePickerPlatform.instance = picker;
      final repository = ImageUploadGatewayRepository();
      final agent = const CcbAgent(
        name: 'mobile',
        provider: 'codex',
        window: 'main',
        order: 0,
        active: true,
        queueDepth: 0,
        paneId: '%2',
      );
      final view = _workspaceView(agent);

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: SelectedAgentWorkspace(
              repository: repository,
              terminalTransport: null,
              usePaneInputForMessages: false,
              view: view,
              agent: agent,
              enableComposerCollapse: true,
              onRefreshView: () async => view,
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      await tester.tap(find.byKey(const ValueKey('agent-attachment-button')));
      await tester.pumpAndSettle();
      await tester.tap(
        find.byKey(const ValueKey('agent-attachment-pick-file')),
      );
      await tester.pumpAndSettle();

      expect(picker.requestedTypes, [FileType.any]);
      expect(
        find.byKey(const ValueKey('agent-attachment-chip-draft-mobile-0')),
        findsOneWidget,
      );
      expect(
        find.text('phone-clip.mp4 is not a supported attachment type'),
        findsNothing,
      );

      await tester.enterText(
        find.byKey(const ValueKey('agent-message-composer')),
        'please inspect this video',
      );
      await tester.tap(find.byKey(const ValueKey('agent-message-send-button')));
      for (
        var attempt = 0;
        attempt < 20 && repository.pathUploads.isEmpty;
        attempt += 1
      ) {
        await tester.pump(const Duration(milliseconds: 50));
      }

      expect(repository.pathUploads.single.mimeType, 'video/mp4');
      expect(repository.pathUploads.single.fileName, 'phone-clip.mp4');
      final submittedAttachment =
          repository.submittedMessages.single.attachments.single;
      expect(submittedAttachment.fileName, 'phone-clip.mp4');
      expect(submittedAttachment.mimeType, 'video/mp4');
      expect(
        submittedAttachment.effectiveKind,
        CcbMessageAttachmentKind.document,
      );
      expect(find.text('Failed'), findsNothing);
    },
  );

  testWidgets('pane image echo merges into one attachment message', (
    tester,
  ) async {
    final originalPicker = FilePickerPlatform.instance;
    final tempDir = Directory.systemTemp.createTempSync(
      'ccb-mobile-pane-image-',
    );
    final image = File('${tempDir.path}/picked-image-cache');
    image.writeAsBytesSync([0xff, 0xd8, 0xff, 0xd9]);
    addTearDown(() {
      FilePickerPlatform.instance = originalPicker;
      tempDir.deleteSync(recursive: true);
    });
    FilePickerPlatform.instance = _FakeFilePicker([
      FilePickerResult([
        PlatformFile(
          name: 'camera-roll-image.jpg',
          path: image.path,
          size: image.lengthSync(),
        ),
      ]),
    ]);
    final repository = PaneImageEchoRepository();
    final terminalTransport = RecordingTerminalTransport();
    final agent = const CcbAgent(
      name: 'mobile',
      provider: 'codex',
      window: 'main',
      order: 0,
      active: true,
      queueDepth: 0,
      paneId: '%2',
    );
    final view = _workspaceView(agent);

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SelectedAgentWorkspace(
            repository: repository,
            terminalTransport: terminalTransport,
            usePaneInputForMessages: true,
            view: view,
            agent: agent,
            enableComposerCollapse: true,
            onRefreshView: () async => view,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const ValueKey('agent-attachment-button')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('agent-attachment-pick-image')));
    await tester.pumpAndSettle();
    await tester.enterText(
      find.byKey(const ValueKey('agent-message-composer')),
      'please inspect this image',
    );
    await tester.tap(find.byKey(const ValueKey('agent-message-send-button')));

    for (
      var attempt = 0;
      attempt < 30 &&
          find
              .byKey(const ValueKey('conversation-item-remote-image-echo'))
              .evaluate()
              .isEmpty;
      attempt += 1
    ) {
      await tester.pump(const Duration(milliseconds: 100));
    }

    expect(
      terminalTransport.sessions.single.pasted.single,
      contains('Attached files:'),
    );
    expect(
      terminalTransport.sessions.single.pasted.single,
      contains('camera-roll-image.jpg'),
    );
    expect(
      find.byKey(const ValueKey('conversation-item-local-mobile-0')),
      findsNothing,
    );
    expect(
      find.byKey(const ValueKey('conversation-item-remote-image-echo')),
      findsOneWidget,
    );
    expect(renderedTextContaining('please inspect this image'), findsOneWidget);
    expect(renderedTextContaining('Attached files:'), findsNothing);
    expect(
      find.byKey(
        const ValueKey('conversation-attachment-list-remote-image-echo'),
      ),
      findsOneWidget,
    );
  });

  testWidgets('oversized file rejection preserves existing attachment draft', (
    tester,
  ) async {
    final originalPicker = FilePickerPlatform.instance;
    final tempDir = Directory.systemTemp.createTempSync(
      'ccb-mobile-picker-size-',
    );
    addTearDown(() {
      FilePickerPlatform.instance = originalPicker;
      tempDir.deleteSync(recursive: true);
    });
    FilePickerPlatform.instance = _FakeFilePicker([
      FilePickerResult([
        PlatformFile(
          name: 'accepted.txt',
          path: _tempFile(tempDir, 'accepted.txt').path,
          size: 12,
        ),
      ]),
      FilePickerResult([
        PlatformFile(
          name: 'too-large.pdf',
          path: _tempFile(tempDir, 'too-large.pdf').path,
          size: agentMessageMaxAttachmentBytes + 1,
        ),
      ]),
    ]);

    await tester.pumpWidget(
      MaterialApp(
        home: ProjectHomeScreen(repository: FakeMobileCcbRepository.demo()),
      ),
    );
    await tester.pumpAndSettle();
    await openCurrentProject(tester);

    await tester.tap(find.byKey(const ValueKey('agent-attachment-button')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('agent-attachment-pick-file')));
    await tester.pumpAndSettle();
    await _waitForFinder(
      tester,
      find.byKey(const ValueKey('agent-attachment-chip-draft-mobile-0')),
    );
    expect(
      find.byKey(const ValueKey('agent-attachment-chip-draft-mobile-0')),
      findsOneWidget,
    );

    await tester.tap(find.byKey(const ValueKey('agent-attachment-button')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('agent-attachment-pick-file')));
    await tester.pumpAndSettle();

    expect(
      find.byKey(const ValueKey('agent-attachment-chip-draft-mobile-0')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('agent-attachment-chip-draft-mobile-1')),
      findsNothing,
    );
    expect(find.text('too-large.pdf is larger than 25 MB'), findsOneWidget);
  });

  testWidgets('unsupported file rejection keeps attachment draft empty', (
    tester,
  ) async {
    final originalPicker = FilePickerPlatform.instance;
    final tempDir = Directory.systemTemp.createTempSync(
      'ccb-mobile-picker-unsupported-',
    );
    addTearDown(() {
      FilePickerPlatform.instance = originalPicker;
      tempDir.deleteSync(recursive: true);
    });
    FilePickerPlatform.instance = _FakeFilePicker([
      FilePickerResult([
        PlatformFile(
          name: 'installer.exe',
          path: _tempFile(tempDir, 'installer.exe').path,
          size: 12,
        ),
      ]),
    ]);

    await tester.pumpWidget(
      MaterialApp(
        home: ProjectHomeScreen(repository: FakeMobileCcbRepository.demo()),
      ),
    );
    await tester.pumpAndSettle();
    await openCurrentProject(tester);

    await tester.tap(find.byKey(const ValueKey('agent-attachment-button')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('agent-attachment-pick-file')));
    await tester.pumpAndSettle();

    expect(find.byKey(const ValueKey('agent-attachment-tray')), findsNothing);
    expect(
      find.text('installer.exe is not a supported attachment type'),
      findsOneWidget,
    );
  });

  testWidgets('agent tap selects and explicit action opens fake terminal', (
    tester,
  ) async {
    await tester.pumpWidget(const CcbMobileApp(enableProductOnboarding: false));
    await tester.pumpAndSettle();
    await openCurrentProject(tester);

    await tester.tap(find.byKey(const ValueKey('agent-lead')));
    await tester.pumpAndSettle();

    expect(find.byType(TerminalView), findsNothing);
    expectAgentSelected(tester, 'lead');

    await tester.tap(find.byKey(const ValueKey('open-agent-terminal-button')));
    await tester.pumpAndSettle();

    expect(find.byType(TerminalView), findsOneWidget);
    expect(find.text('demo'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('return-to-agent-chat-button')),
      findsOneWidget,
    );
    expect(
      find.text('tmux -S /tmp/ccb-demo/tmux.sock attach-session -t ccb-demo'),
      findsNothing,
    );
  });

  testWidgets('chat composer preserves drafts per selected agent', (
    tester,
  ) async {
    await tester.pumpWidget(const CcbMobileApp(enableProductOnboarding: false));
    await tester.pumpAndSettle();
    await openCurrentProject(tester);

    expect(find.byKey(const ValueKey('agent-chat-timeline')), findsOneWidget);
    expect(find.byKey(const ValueKey('agent-chat-composer')), findsOneWidget);
    expect(
      find.byKey(const ValueKey('agent-message-composer')),
      findsOneWidget,
    );

    await tester.enterText(
      find.byKey(const ValueKey('agent-message-composer')),
      'mobile draft',
    );
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const ValueKey('agent-lead')));
    await tester.pumpAndSettle();
    var composer = tester.widget<TextField>(
      find.byKey(const ValueKey('agent-message-composer')),
    );
    expect(composer.controller?.text, isEmpty);

    await tester.enterText(
      find.byKey(const ValueKey('agent-message-composer')),
      'lead draft',
    );
    await tester.pumpAndSettle();

    await tester.tap(find.byKey(const ValueKey('agent-mobile')));
    await tester.pumpAndSettle();
    composer = tester.widget<TextField>(
      find.byKey(const ValueKey('agent-message-composer')),
    );
    expect(composer.controller?.text, 'mobile draft');

    await tester.tap(find.byKey(const ValueKey('agent-lead')));
    await tester.pumpAndSettle();
    composer = tester.widget<TextField>(
      find.byKey(const ValueKey('agent-message-composer')),
    );
    expect(composer.controller?.text, 'lead draft');
  });

  testWidgets('chat composer shows pending sent failed and retry states', (
    tester,
  ) async {
    final repository = ControlledSubmitRepository();
    await tester.pumpWidget(
      MaterialApp(home: ProjectHomeScreen(repository: repository)),
    );
    await tester.pumpAndSettle();
    await openCurrentProject(tester);

    await tester.enterText(
      find.byKey(const ValueKey('agent-message-composer')),
      'hello selected agent',
    );
    await tester.tap(find.byKey(const ValueKey('agent-message-send-button')));
    await tester.pump();

    var sendButton = tester.widget<IconButton>(
      find.byKey(const ValueKey('agent-message-send-button')),
    );
    expect(sendButton.onPressed, isNull);
    expect(find.byType(CircularProgressIndicator), findsAtLeastNWidgets(1));
    await tester.tap(find.byKey(const ValueKey('agent-message-send-button')));
    await tester.sendKeyEvent(LogicalKeyboardKey.enter);
    await tester.pump();
    expect(repository.submittedMessages, hasLength(1));
    await dragUntilVisible(
      tester,
      const ValueKey('conversation-item-local-mobile-0'),
      const Offset(0, -700),
    );
    expect(
      find.byKey(const ValueKey('conversation-item-local-mobile-0')),
      findsOneWidget,
    );
    expect(
      find.descendant(
        of: find.byKey(const ValueKey('conversation-state-local-mobile-0')),
        matching: find.text('Pending'),
      ),
      findsOneWidget,
    );

    repository.finishFirstSubmit();
    await tester.pump(const Duration(milliseconds: 120));
    sendButton = tester.widget<IconButton>(
      find.byKey(const ValueKey('agent-message-send-button')),
    );
    expect(sendButton.onPressed, isNotNull);
    await dragUntilVisible(
      tester,
      const ValueKey('conversation-item-local-mobile-0'),
      const Offset(0, 700),
    );
    expect(
      find.byKey(const ValueKey('conversation-state-local-mobile-0')),
      findsNothing,
    );

    await tester.enterText(
      find.byKey(const ValueKey('agent-message-composer')),
      'please fail this fake send',
    );
    await tester.tap(find.byKey(const ValueKey('agent-message-send-button')));
    await tester.pump(const Duration(milliseconds: 120));

    await dragUntilVisible(
      tester,
      const ValueKey('conversation-item-local-mobile-1'),
      const Offset(0, -700),
    );
    expect(
      find.descendant(
        of: find.byKey(const ValueKey('conversation-state-local-mobile-1')),
        matching: find.text('Failed'),
      ),
      findsOneWidget,
    );
    final retryButton = find.byKey(
      const ValueKey('retry-message-local-mobile-1'),
    );
    await tester.ensureVisible(retryButton);
    await tester.pumpAndSettle();
    await tester.tap(retryButton);
    await tester.pump();
    expect(repository.submittedMessages, hasLength(3));
    expect(repository.submittedMessages.last.idempotencyKey, 'local-mobile-1');
    expect(
      find.byKey(const ValueKey('conversation-item-local-mobile-2')),
      findsNothing,
    );
    await dragUntilVisible(
      tester,
      const ValueKey('conversation-item-local-mobile-1'),
      const Offset(0, -700),
    );
    expect(
      find.descendant(
        of: find.byKey(const ValueKey('conversation-state-local-mobile-1')),
        matching: find.text('Pending'),
      ),
      findsOneWidget,
    );

    await tester.pump(const Duration(milliseconds: 120));
    await dragUntilVisible(
      tester,
      const ValueKey('conversation-item-local-mobile-1'),
      const Offset(0, 700),
    );
    expect(
      find.byKey(const ValueKey('conversation-state-local-mobile-1')),
      findsNothing,
    );
  });

  testWidgets('failed local message can be deleted from the timeline', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        home: ProjectHomeScreen(repository: FakeMobileCcbRepository.demo()),
      ),
    );
    await tester.pumpAndSettle();
    await openCurrentProject(tester);

    await tester.enterText(
      find.byKey(const ValueKey('agent-message-composer')),
      'please fail and delete this send',
    );
    await tester.tap(find.byKey(const ValueKey('agent-message-send-button')));
    await tester.pump(const Duration(milliseconds: 180));

    await dragUntilVisible(
      tester,
      const ValueKey('conversation-item-local-mobile-0'),
      const Offset(0, -700),
    );
    expect(
      find.descendant(
        of: find.byKey(const ValueKey('conversation-state-local-mobile-0')),
        matching: find.text('Failed'),
      ),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('retry-message-local-mobile-0')),
      findsOneWidget,
    );

    final deleteButton = find.byKey(
      const ValueKey('delete-message-local-mobile-0'),
    );
    await tester.ensureVisible(deleteButton);
    await tester.pumpAndSettle();
    await tester.tap(deleteButton);
    await tester.pumpAndSettle();

    expect(
      find.byKey(const ValueKey('conversation-item-local-mobile-0')),
      findsNothing,
    );
    expect(find.text('please fail and delete this send'), findsNothing);
  });

  testWidgets('sent fake repository message remains visible after completion', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        home: ProjectHomeScreen(repository: FakeMobileCcbRepository.demo()),
      ),
    );
    await tester.pumpAndSettle();
    await openCurrentProject(tester);

    await tester.enterText(
      find.byKey(const ValueKey('agent-message-composer')),
      'visible after submit',
    );
    await tester.tap(find.byKey(const ValueKey('agent-message-send-button')));
    await tester.pump(const Duration(milliseconds: 180));

    await dragUntilVisible(
      tester,
      const ValueKey('conversation-item-local-mobile-0'),
      const Offset(0, -700),
    );
    expect(find.text('visible after submit'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('conversation-state-local-mobile-0')),
      findsNothing,
    );
  });

  testWidgets('sent fake repository keeps consecutive button sends visible', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        home: ProjectHomeScreen(repository: FakeMobileCcbRepository.demo()),
      ),
    );
    await tester.pumpAndSettle();
    await openCurrentProject(tester);

    await tester.enterText(
      find.byKey(const ValueKey('agent-message-composer')),
      'button first visible',
    );
    await tester.tap(find.byKey(const ValueKey('agent-message-send-button')));
    await tester.pump(const Duration(milliseconds: 180));
    await tester.enterText(
      find.byKey(const ValueKey('agent-message-composer')),
      'button second visible',
    );
    await tester.tap(find.byKey(const ValueKey('agent-message-send-button')));
    await tester.pump(const Duration(milliseconds: 180));

    await dragUntilVisible(
      tester,
      const ValueKey('conversation-item-local-mobile-0'),
      const Offset(0, 700),
    );
    expect(find.text('button first visible'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('conversation-state-local-mobile-0')),
      findsNothing,
    );
    await dragUntilVisible(
      tester,
      const ValueKey('conversation-item-local-mobile-1'),
      const Offset(0, -700),
    );
    expect(find.text('button second visible'), findsOneWidget);
    expect(
      find.byKey(const ValueKey('conversation-state-local-mobile-1')),
      findsNothing,
    );
  });

  testWidgets('sent fake repository keeps consecutive enter sends visible', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        home: ProjectHomeScreen(repository: FakeMobileCcbRepository.demo()),
      ),
    );
    await tester.pumpAndSettle();
    await openCurrentProject(tester);

    await tester.enterText(
      find.byKey(const ValueKey('agent-message-composer')),
      'enter first visible',
    );
    await tester.sendKeyEvent(LogicalKeyboardKey.enter);
    await tester.pump(const Duration(milliseconds: 180));
    await tester.enterText(
      find.byKey(const ValueKey('agent-message-composer')),
      'enter second visible',
    );
    await tester.sendKeyEvent(LogicalKeyboardKey.enter);
    await tester.pump(const Duration(milliseconds: 180));

    await dragUntilVisible(
      tester,
      const ValueKey('conversation-item-local-mobile-0'),
      const Offset(0, 700),
    );
    expect(find.text('enter first visible'), findsOneWidget);
    await dragUntilVisible(
      tester,
      const ValueKey('conversation-item-local-mobile-1'),
      const Offset(0, -700),
    );
    expect(find.text('enter second visible'), findsOneWidget);
  });

  testWidgets('sent fake repository preserves duplicate body counts', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        home: ProjectHomeScreen(repository: FakeMobileCcbRepository.demo()),
      ),
    );
    await tester.pumpAndSettle();
    await openCurrentProject(tester);

    for (var index = 0; index < 2; index += 1) {
      await tester.enterText(
        find.byKey(const ValueKey('agent-message-composer')),
        'duplicate visible body',
      );
      await tester.tap(find.byKey(const ValueKey('agent-message-send-button')));
      await tester.pump(const Duration(milliseconds: 180));
    }

    await dragUntilVisible(
      tester,
      const ValueKey('conversation-item-local-mobile-0'),
      const Offset(0, 700),
    );
    expect(
      find.descendant(
        of: find.byKey(const ValueKey('conversation-item-local-mobile-0')),
        matching: find.text('duplicate visible body'),
      ),
      findsOneWidget,
    );
    await dragUntilVisible(
      tester,
      const ValueKey('conversation-item-local-mobile-1'),
      const Offset(0, -700),
    );
    expect(
      find.descendant(
        of: find.byKey(const ValueKey('conversation-item-local-mobile-1')),
        matching: find.text('duplicate visible body'),
      ),
      findsOneWidget,
    );
  });

  testWidgets('gateway attachment download ignores duplicate pending taps', (
    tester,
  ) async {
    final repository = DownloadGateRepository();
    await tester.pumpWidget(
      MaterialApp(home: ProjectHomeScreen(repository: repository)),
    );
    await tester.pumpAndSettle();
    await openCurrentProject(tester);

    await dragUntilVisible(
      tester,
      const ValueKey('conversation-attachment-chip-gateway-file'),
      const Offset(0, -700),
    );
    tester
        .widget<InkWell>(
          find.byKey(
            const ValueKey('conversation-attachment-chip-gateway-file'),
          ),
        )
        .onTap!();
    await tester.pumpAndSettle();
    await tester.tap(
      find.byKey(
        const ValueKey('conversation-attachment-action-download-gateway-file'),
      ),
    );
    await tester.pump();
    final busyChip = tester.widget<InkWell>(
      find.byKey(const ValueKey('conversation-attachment-chip-gateway-file')),
    );
    expect(busyChip.onTap, isNull);

    expect(repository.downloadCalls, 1);
    expect(
      find.byKey(const ValueKey('agent-attachment-progress-gateway-file')),
      findsOneWidget,
    );
  });

  testWidgets('stale attachment action does not download after agent switch', (
    tester,
  ) async {
    final repository = DownloadGateRepository();
    var selectedAgent = _agentNamed('mobile');
    var view = _workspaceView(selectedAgent);
    StateSetter? updateHarness;

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: StatefulBuilder(
            builder: (context, setState) {
              updateHarness = setState;
              return SelectedAgentWorkspace(
                repository: repository,
                terminalTransport: null,
                usePaneInputForMessages: false,
                view: view,
                agent: selectedAgent,
                enableComposerCollapse: true,
                onRefreshView: null,
              );
            },
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    await dragUntilVisible(
      tester,
      const ValueKey('conversation-attachment-chip-gateway-file'),
      const Offset(0, -700),
    );
    await tester.longPress(
      find.byKey(const ValueKey('conversation-attachment-chip-gateway-file')),
    );
    await tester.pumpAndSettle();
    expect(
      find.byKey(
        const ValueKey('conversation-attachment-action-download-gateway-file'),
      ),
      findsOneWidget,
    );

    updateHarness!(() {
      selectedAgent = _agentNamed('lead');
      view = _workspaceView(selectedAgent);
    });
    await tester.pump();
    await tester.tap(
      find.byKey(
        const ValueKey('conversation-attachment-action-download-gateway-file'),
      ),
    );
    await tester.pump();

    expect(repository.downloadCalls, 0);
    expect(
      find.byKey(const ValueKey('agent-attachment-progress-gateway-file')),
      findsNothing,
    );
  });

  testWidgets('oversized gateway attachment does not start download', (
    tester,
  ) async {
    final repository = DownloadGateRepository(
      attachmentSizeBytes: agentMessageMaxDownloadBytes + 1,
    );
    await tester.pumpWidget(
      MaterialApp(home: ProjectHomeScreen(repository: repository)),
    );
    await tester.pumpAndSettle();
    await openCurrentProject(tester);

    await dragUntilVisible(
      tester,
      const ValueKey('conversation-attachment-chip-gateway-file'),
      const Offset(0, -700),
    );
    tester
        .widget<InkWell>(
          find.byKey(
            const ValueKey('conversation-attachment-chip-gateway-file'),
          ),
        )
        .onTap!();
    await tester.pumpAndSettle();
    await tester.tap(
      find.byKey(
        const ValueKey('conversation-attachment-action-download-gateway-file'),
      ),
    );
    await tester.pump();

    expect(repository.downloadCalls, 0);
    expect(
      find.text('gateway-notes.txt is larger than 128 MB'),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('agent-attachment-progress-gateway-file')),
      findsNothing,
    );
  });

  testWidgets('release sized gateway attachment can start download', (
    tester,
  ) async {
    final repository = DownloadGateRepository(
      attachmentSizeBytes: 74 * 1024 * 1024,
    );
    await tester.pumpWidget(
      MaterialApp(home: ProjectHomeScreen(repository: repository)),
    );
    await tester.pumpAndSettle();
    await openCurrentProject(tester);

    await dragUntilVisible(
      tester,
      const ValueKey('conversation-attachment-chip-gateway-file'),
      const Offset(0, -700),
    );
    tester
        .widget<InkWell>(
          find.byKey(
            const ValueKey('conversation-attachment-chip-gateway-file'),
          ),
        )
        .onTap!();
    await tester.pumpAndSettle();
    await tester.tap(
      find.byKey(
        const ValueKey('conversation-attachment-action-download-gateway-file'),
      ),
    );
    await tester.pump();

    expect(repository.downloadCalls, 1);
    expect(
      find.byKey(const ValueKey('agent-attachment-progress-gateway-file')),
      findsOneWidget,
    );
  });

  testWidgets('manual refresh reloads conversation without terminal fallback', (
    tester,
  ) async {
    final repository = FallbackTerminalHistoryRepository();
    await tester.pumpWidget(
      MaterialApp(home: ProjectHomeScreen(repository: repository)),
    );
    await tester.pumpAndSettle();
    await openCurrentProject(tester);

    expect(repository.conversationCalls, isNotEmpty);
    final initialTerminalHistoryCalls = repository.terminalHistoryCalls.length;
    final initialConversationCalls = repository.conversationCalls.length;
    expect(repository.terminalHistoryCalls, isEmpty);
    expect(
      find.text('Conversation endpoint has no pane history.'),
      findsOneWidget,
    );

    repository.terminalHistoryOverride = const ReadableTerminalHistory(
      agentName: 'mobile',
      historyScope: 'tmux_scrollback',
      sourcePaneId: '%2',
      blocks: [
        ReadableTerminalBlock(
          id: 'sync-output',
          type: 'log',
          title: 'Terminal output',
          text: 'Pane sync visible',
        ),
      ],
    );

    await tester.pump(const Duration(seconds: 10));
    await tester.pump();

    expect(repository.terminalHistoryCalls.length, initialTerminalHistoryCalls);
    expect(find.text('Pane sync visible'), findsNothing);

    await tester.tap(
      find.byKey(const ValueKey('agent-conversation-refresh-action')),
    );
    await tester.pumpAndSettle();

    expect(
      repository.conversationCalls.length,
      greaterThan(initialConversationCalls),
    );
    expect(repository.terminalHistoryCalls.length, initialTerminalHistoryCalls);
    expect(find.text('Pane sync visible'), findsNothing);
  });

  testWidgets(
    'user scrolling near latest does not refresh status or timeline',
    (tester) async {
      await setTestSurfaceSize(tester, const Size(390, 844));
      final repository = LongConversationRepository(messageCount: 36);
      await tester.pumpWidget(
        MaterialApp(home: ProjectHomeScreen(repository: repository)),
      );
      await tester.pumpAndSettle();
      await openCurrentProject(tester);

      final initialViewCalls = repository.getProjectViewCalls;
      final initialConversationCalls = repository.conversationCalls.length;
      final initialTerminalHistoryCalls =
          repository.terminalHistoryCalls.length;

      await tester.drag(
        find.byKey(const ValueKey('agent-chat-timeline')),
        const Offset(0, -700),
      );
      await tester.pump(const Duration(milliseconds: 100));

      expect(repository.getProjectViewCalls, initialViewCalls);
      expect(repository.conversationCalls.length, initialConversationCalls);
      expect(
        repository.terminalHistoryCalls.length,
        initialTerminalHistoryCalls,
      );
    },
  );

  testWidgets(
    'opened agent does not show terminal-derived conversation by default',
    (tester) async {
      final repository = PaneConversationRepository();
      await tester.pumpWidget(
        MaterialApp(home: ProjectHomeScreen(repository: repository)),
      );
      await tester.pumpAndSettle();
      await openCurrentProject(tester);

      await tester.tap(
        find.byKey(const ValueKey('agent-conversation-refresh-action')),
      );
      await tester.pump();

      expect(repository.conversationCalls, isNotEmpty);
      expect(repository.terminalHistoryCalls, isEmpty);
      expect(find.text('Pane conversation visible'), findsNothing);
    },
  );

  testWidgets('opened agent with native conversation skips terminal fallback', (
    tester,
  ) async {
    final repository = NativeConversationRepository();
    await tester.pumpWidget(
      MaterialApp(home: ProjectHomeScreen(repository: repository)),
    );
    await tester.pumpAndSettle();
    await openCurrentProject(tester);

    await tester.tap(
      find.byKey(const ValueKey('agent-conversation-refresh-action')),
    );
    await tester.pumpAndSettle();

    expect(repository.conversationCalls, isNotEmpty);
    expect(repository.terminalHistoryCalls, isEmpty);
    expect(find.text('Native conversation visible'), findsOneWidget);
  });

  testWidgets('gateway conversation replies render markdown in chat bubbles', (
    tester,
  ) async {
    await tester.pumpWidget(
      MaterialApp(
        home: ProjectHomeScreen(repository: MarkdownGatewayRepository()),
      ),
    );
    await tester.pumpAndSettle();
    await openCurrentProject(tester);

    await tapVisible(
      tester,
      const ValueKey('conversation-expand-reply-markdown'),
    );
    expect(
      find.byKey(const ValueKey('markdown-body-conversation-reply-markdown')),
      findsOneWidget,
    );
    expect(
      find.byKey(const ValueKey('conversation-body-reply-markdown')),
      findsNothing,
    );
    expect(find.text('Markdown reply'), findsOneWidget);
    expect(find.text('first item'), findsOneWidget);
    expect(find.text('second item'), findsOneWidget);
    expect(find.text('ordered item'), findsOneWidget);
    expect(find.text('follow up item'), findsOneWidget);
    expect(find.text('done item'), findsOneWidget);
    expect(find.text('todo item'), findsOneWidget);
    expect(find.text('quoted insight'), findsOneWidget);
    expect(renderedTextContaining('inline code'), findsWidgets);
    expect(renderedTextContaining('bold text'), findsWidgets);
    expect(renderedTextContaining('italic text'), findsWidgets);
    expect(find.text('final ok = true;'), findsOneWidget);
    expect(find.text('Column'), findsOneWidget);
    expect(find.text('Value'), findsOneWidget);
    expect(find.text('alpha'), findsOneWidget);
    expect(find.text('42'), findsOneWidget);
    expect(find.text('docs link', findRichText: true), findsOneWidget);

    await tester.ensureVisible(find.text('docs link', findRichText: true));
    await tester.pumpAndSettle();
    await tester.tap(find.text('docs link', findRichText: true));
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('open-url-confirm-action')),
      findsOneWidget,
    );
  });

  testWidgets('paired composer submits chat through pane input', (
    tester,
  ) async {
    final secureStore = MemorySecureStore();
    final profileStore = GatewayHostProfileStore(secureStore: secureStore);
    final host = GatewayPairedHost(
      profile: GatewayHostProfile(
        hostId: 'proj-demo',
        deviceId: 'dev-partial-pane',
        routeProvider: RouteProvider(
          kind: RouteProviderKind.lan,
          gatewayUrl: Uri.parse('http://127.0.0.1:8787'),
        ),
        scopes: const {'view', 'content', 'focus', 'terminal_input', 'notify'},
      ),
      deviceToken: 'device-secret',
      projectId: 'proj-demo',
    );
    await profileStore.save(host);
    final terminalTransport = RecordingTerminalTransport(
      writeError: const TerminalTransportException('enter failed'),
    );
    final repository = RecordingGatewayRepository();

    await tester.pumpWidget(
      MaterialApp(
        home: ProjectHomeScreen(
          repository: FakeMobileCcbRepository.demo(),
          profileStore: profileStore,
          gatewayRepositoryFactory: (profile) => repository,
          gatewayTerminalTransportFactory: (profile) => terminalTransport,
        ),
      ),
    );
    await tester.pumpAndSettle();
    await activateStoredGatewayProfile(tester);

    await tester.enterText(
      find.byKey(const ValueKey('agent-message-composer')),
      'partial pane send',
    );
    await tester.tap(find.byKey(const ValueKey('agent-message-send-button')));
    await tester.pumpAndSettle();

    expect(repository.submittedMessages, isEmpty);
    expect(terminalTransport.requests, hasLength(1));
    expect(terminalTransport.sessions.single.pasted, ['partial pane send']);
    expect(terminalTransport.sessions.single.written, isEmpty);
    expect(find.text('partial pane send'), findsOneWidget);
    expect(find.text('Check pane'), findsOneWidget);
  });

  testWidgets('paired composer waits for pane acknowledgement before working', (
    tester,
  ) async {
    final terminalTransport = BlockingPasteTerminalTransport(
      writeError: const TerminalTransportException('enter failed'),
    );
    final repository = RecordingGatewayRepository();
    final agent = _statusAgent(
      provider: 'claude',
      activityState: 'idle',
      activitySource: 'claude_runtime',
      activityReason: 'claude_pane_idle_prompt',
    );
    final view = _workspaceView(agent);

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SelectedAgentWorkspace(
            repository: repository,
            terminalTransport: terminalTransport,
            usePaneInputForMessages: true,
            view: view,
            agent: agent,
            enableComposerCollapse: true,
            onRefreshView: () async => view,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    await tester.enterText(
      find.byKey(const ValueKey('agent-message-composer')),
      'pending pane send',
    );
    await tester.tap(find.byKey(const ValueKey('agent-message-send-button')));
    await tester.pump();

    expect(repository.submittedMessages, isEmpty);
    await dragUntilVisible(
      tester,
      const ValueKey('conversation-item-local-mobile-0'),
      const Offset(0, -700),
    );
    expect(renderedTextContaining('pending pane send'), findsOneWidget);
    expect(find.byKey(const ValueKey('agent-working-status')), findsNothing);
    expect(
      find.byKey(const ValueKey('conversation-working-status-text')),
      findsNothing,
    );

    terminalTransport.completePaste();
    await tester.pumpAndSettle();

    expect(terminalTransport.sessions.single.pasted, ['pending pane send']);
    expect(terminalTransport.sessions.single.written, isEmpty);
    expect(find.byKey(const ValueKey('agent-working-status')), findsNothing);
    expect(
      find.byKey(const ValueKey('conversation-working-status-text')),
      findsNothing,
    );
  });

  testWidgets('Claude local clear does not retain a working placeholder', (
    tester,
  ) async {
    final terminalTransport = RecordingTerminalTransport();
    final repository = RecordingGatewayRepository();
    final agent = _statusAgent(
      provider: 'claude',
      activityState: 'idle',
      activitySource: 'claude_runtime',
      activityReason: 'claude_pane_idle_prompt',
    );
    final view = _workspaceView(agent);

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: SelectedAgentWorkspace(
            repository: repository,
            terminalTransport: terminalTransport,
            usePaneInputForMessages: true,
            view: view,
            agent: agent,
            enableComposerCollapse: true,
            onRefreshView: () async => view,
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    await tester.enterText(
      find.byKey(const ValueKey('agent-message-composer')),
      '/clear',
    );
    await tester.tap(find.byKey(const ValueKey('agent-message-send-button')));
    await tester.pumpAndSettle();

    expect(terminalTransport.sessions.single.pasted, ['/clear']);
    expect(
      find.byKey(const ValueKey('conversation-working-status-text')),
      findsNothing,
    );
    expect(find.text('Working...'), findsNothing);
  });

  testWidgets('paired terminal output updates status without chat bubble', (
    tester,
  ) async {
    final secureStore = MemorySecureStore();
    final profileStore = GatewayHostProfileStore(secureStore: secureStore);
    final host = GatewayPairedHost(
      profile: GatewayHostProfile(
        hostId: 'proj-demo',
        deviceId: 'dev-stream-status',
        routeProvider: RouteProvider(
          kind: RouteProviderKind.lan,
          gatewayUrl: Uri.parse('http://127.0.0.1:8787'),
        ),
        scopes: const {'view', 'content', 'focus', 'terminal_input', 'notify'},
      ),
      deviceToken: 'device-secret',
      projectId: 'proj-demo',
    );
    await profileStore.save(host);
    final terminalTransport = RecordingTerminalTransport();
    final repository = RecordingGatewayRepository();

    await tester.pumpWidget(
      MaterialApp(
        home: ProjectHomeScreen(
          repository: FakeMobileCcbRepository.demo(),
          profileStore: profileStore,
          gatewayRepositoryFactory: (profile) => repository,
          gatewayTerminalTransportFactory: (profile) => terminalTransport,
        ),
      ),
    );
    await tester.pumpAndSettle();
    await activateStoredGatewayProfile(tester);

    await tester.enterText(
      find.byKey(const ValueKey('agent-message-composer')),
      'stream status only',
    );
    await tester.tap(find.byKey(const ValueKey('agent-message-send-button')));
    await tester.pump();

    expect(terminalTransport.sessions, hasLength(1));
    terminalTransport.sessions.single.addOutput('stream-only-visible-status\n');
    await tester.pump();

    expect(find.byKey(const ValueKey('agent-working-status')), findsNothing);
    expect(
      find.byKey(const ValueKey('conversation-working-status-text')),
      findsOneWidget,
    );
    expect(find.text('Terminal output'), findsNothing);
    expect(find.textContaining('stream-only-visible-status'), findsNothing);
  });

  testWidgets('unsolicited idle terminal repaint does not start working', (
    tester,
  ) async {
    final terminalTransport = RecordingTerminalTransport();
    final repository = ControlledCompletedReplyConversationRepository();
    final controller = SelectedAgentWorkspaceController();
    var refreshCount = 0;
    var view = _workspaceView(
      _statusAgent(
        activityState: 'idle',
        activitySource: 'provider_pane',
        activityReason: 'provider_prompt_idle',
      ),
    );

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: StatefulBuilder(
            builder: (context, setState) {
              final agent = view.agentByName('mobile')!;
              return SelectedAgentWorkspace(
                repository: repository,
                terminalTransport: terminalTransport,
                usePaneInputForMessages: true,
                view: view,
                agent: agent,
                enableComposerCollapse: true,
                controller: controller,
                onRefreshView: () async {
                  refreshCount += 1;
                  final refreshed = _workspaceView(
                    _statusAgent(
                      activityState: refreshCount == 1 ? 'active' : 'idle',
                      activitySource:
                          refreshCount == 1 ? 'codex_runtime' : 'provider_pane',
                      activityReason:
                          refreshCount == 1
                              ? 'codex_working_status_line'
                              : 'provider_prompt_idle',
                    ),
                  );
                  setState(() {
                    view = refreshed;
                  });
                  return refreshed;
                },
              );
            },
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    await tester.enterText(
      find.byKey(const ValueKey('agent-message-composer')),
      'work before idle repaint',
    );
    await tester.tap(find.byKey(const ValueKey('agent-message-send-button')));
    await tester.pump();
    expect(terminalTransport.sessions, hasLength(1));
    repository.releaseReply();
    controller.refreshLatest();
    await tester.pumpAndSettle();
    controller.refreshLatest();
    await tester.pumpAndSettle();
    expect(
      find.byKey(const ValueKey('conversation-working-status-text')),
      findsNothing,
    );

    terminalTransport.sessions.single.addOutput('idle prompt repaint\n');
    await tester.pump();

    expect(
      find.byKey(const ValueKey('conversation-working-status-text')),
      findsNothing,
    );
    expect(find.textContaining('idle prompt repaint'), findsNothing);
  });

  testWidgets(
    'single idle refresh settles awaiting after completed reply loads',
    (tester) async {
      final terminalTransport = RecordingTerminalTransport();
      final repository = ControlledCompletedReplyConversationRepository();
      final controller = SelectedAgentWorkspaceController();
      var refreshCount = 0;
      var view = _workspaceView(
        _statusAgent(
          activityState: 'idle',
          activitySource: 'provider_pane',
          activityReason: 'provider_prompt_idle',
        ),
      );

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: StatefulBuilder(
              builder: (context, setState) {
                final agent = view.agentByName('mobile')!;
                return SelectedAgentWorkspace(
                  repository: repository,
                  terminalTransport: terminalTransport,
                  usePaneInputForMessages: true,
                  view: view,
                  agent: agent,
                  enableComposerCollapse: true,
                  controller: controller,
                  onRefreshView: () async {
                    refreshCount += 1;
                    final refreshed = _workspaceView(
                      _statusAgent(
                        activityState: 'idle',
                        activitySource: 'provider_pane',
                        activityReason: 'provider_prompt_idle',
                      ),
                    );
                    setState(() {
                      view = refreshed;
                    });
                    return refreshed;
                  },
                );
              },
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      await tester.enterText(
        find.byKey(const ValueKey('agent-message-composer')),
        'single refresh reply',
      );
      await tester.tap(find.byKey(const ValueKey('agent-message-send-button')));
      await tester.pump();
      expect(
        find.byKey(const ValueKey('conversation-working-status-text')),
        findsOneWidget,
      );

      repository.releaseReply();
      controller.refreshLatest();
      await tester.pumpAndSettle();

      expect(refreshCount, 1);
      expect(find.text('completed reply after idle'), findsOneWidget);
      expect(
        find.byKey(const ValueKey('conversation-working-status-text')),
        findsNothing,
      );
    },
  );

  testWidgets(
    'paired terminal interruption takes priority until activity resumes',
    (tester) async {
      final secureStore = MemorySecureStore();
      final profileStore = GatewayHostProfileStore(secureStore: secureStore);
      final host = GatewayPairedHost(
        profile: GatewayHostProfile(
          hostId: 'proj-demo',
          deviceId: 'dev-stream-interrupted-status',
          routeProvider: RouteProvider(
            kind: RouteProviderKind.lan,
            gatewayUrl: Uri.parse('http://127.0.0.1:8787'),
          ),
          scopes: const {
            'view',
            'content',
            'focus',
            'terminal_input',
            'notify',
          },
        ),
        deviceToken: 'device-secret',
        projectId: 'proj-demo',
      );
      await profileStore.save(host);
      final terminalTransport = RecordingTerminalTransport();
      final repository = RecordingGatewayRepository();

      await tester.pumpWidget(
        MaterialApp(
          home: ProjectHomeScreen(
            repository: FakeMobileCcbRepository.demo(),
            profileStore: profileStore,
            gatewayRepositoryFactory: (profile) => repository,
            gatewayTerminalTransportFactory: (profile) => terminalTransport,
          ),
        ),
      );
      await tester.pumpAndSettle();
      await activateStoredGatewayProfile(tester);

      await tester.enterText(
        find.byKey(const ValueKey('agent-message-composer')),
        'stream interrupted status',
      );
      await tester.tap(find.byKey(const ValueKey('agent-message-send-button')));
      await tester.pump();

      expect(terminalTransport.sessions, hasLength(1));
      terminalTransport.sessions.single.addOutput('Conversation ');
      await tester.pump();
      expect(
        find.byKey(const ValueKey('conversation-working-status-text')),
        findsOneWidget,
      );

      terminalTransport.sessions.single.addOutput('interrupted\n');
      await tester.pump();

      expect(find.byKey(const ValueKey('agent-working-status')), findsNothing);
      expect(
        find.byKey(const ValueKey('conversation-working-status-text')),
        findsNothing,
      );
      expect(find.textContaining('Conversation interrupted'), findsNothing);

      terminalTransport.sessions.single.addOutput('Working time 00:12\n');
      await tester.pump();

      expect(
        find.byKey(const ValueKey('conversation-working-status-text')),
        findsOneWidget,
      );
    },
  );

  testWidgets('terminal activity does not start blind conversation polling', (
    tester,
  ) async {
    final terminalTransport = RecordingTerminalTransport();
    var refreshCalls = 0;
    var view = _workspaceView(
      _statusAgent(
        activityState: 'idle',
        activitySource: 'provider_pane',
        activityReason: 'provider_prompt_idle',
      ),
    );

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: StatefulBuilder(
            builder: (context, setState) {
              final agent = view.agentByName('mobile')!;
              return SelectedAgentWorkspace(
                repository: FakeMobileCcbRepository.demo(),
                terminalTransport: terminalTransport,
                usePaneInputForMessages: true,
                view: view,
                agent: agent,
                enableComposerCollapse: true,
                onRefreshView: () async {
                  refreshCalls += 1;
                  final refreshed = _workspaceView(
                    _statusAgent(
                      activityState: 'idle',
                      activitySource: 'provider_pane',
                      activityReason: 'provider_prompt_idle',
                    ),
                  );
                  setState(() {
                    view = refreshed;
                  });
                  return refreshed;
                },
              );
            },
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    expect(find.byKey(const ValueKey('agent-working-status')), findsNothing);
    expect(find.text('Idle'), findsNothing);

    await tester.enterText(
      find.byKey(const ValueKey('agent-message-composer')),
      'work then idle',
    );
    await tester.tap(find.byKey(const ValueKey('agent-message-send-button')));
    await tester.pump();

    expect(terminalTransport.sessions.single.pasted, ['work then idle']);
    expect(
      find.byKey(const ValueKey('conversation-working-status-text')),
      findsOneWidget,
    );

    terminalTransport.sessions.single.addOutput('work then idle\n');
    await tester.pump();

    await tester.pump(const Duration(milliseconds: 120));

    expect(refreshCalls, 0);
    expect(
      find.byKey(const ValueKey('conversation-working-status-text')),
      findsOneWidget,
    );
    expect(find.text('mobile completed'), findsNothing);

    await tester.pump(const Duration(seconds: 3));
    await tester.pumpAndSettle();

    expect(
      find.byKey(const ValueKey('conversation-working-status-text')),
      findsOneWidget,
    );
    expect(find.text('Idle'), findsNothing);
    expect(find.text('mobile completed'), findsNothing);
  });

  testWidgets(
    'paired send keeps working through one stale idle reconciliation',
    (tester) async {
      final terminalTransport = RecordingTerminalTransport();
      final repository = RecordingGatewayRepository();
      var refreshCalls = 0;
      final baselineTime = DateTime.utc(2026, 7, 28, 10);
      var view = _workspaceView(
        _statusAgent(
          activityState: 'idle',
          activitySource: 'claude_runtime',
          activityReason: 'claude_pane_idle_prompt',
        ),
        generatedAt: baselineTime,
        sequence: 10,
        namespaceEpoch: 4,
      );

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: StatefulBuilder(
              builder: (context, setState) {
                final agent = view.agentByName('mobile')!;
                return SelectedAgentWorkspace(
                  repository: repository,
                  terminalTransport: terminalTransport,
                  usePaneInputForMessages: true,
                  view: view,
                  agent: agent,
                  enableComposerCollapse: true,
                  onRefreshView: () async {
                    refreshCalls += 1;
                    final refreshed = _workspaceView(
                      _statusAgent(
                        activityState: 'idle',
                        activitySource: 'claude_runtime',
                        activityReason: 'claude_pane_idle_prompt',
                      ),
                      generatedAt: baselineTime.add(const Duration(seconds: 4)),
                      sequence: 11,
                      namespaceEpoch: 4,
                    );
                    setState(() {
                      view = refreshed;
                    });
                    return refreshed;
                  },
                );
              },
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      await tester.enterText(
        find.byKey(const ValueKey('agent-message-composer')),
        'provider never consumed this turn',
      );
      await tester.tap(find.byKey(const ValueKey('agent-message-send-button')));
      await tester.pump();

      expect(
        find.byKey(const ValueKey('conversation-working-status-text')),
        findsOneWidget,
      );
      await tester.pump(const Duration(seconds: 2));
      expect(refreshCalls, 0);

      await tester.pump(const Duration(seconds: 2));
      await tester.pumpAndSettle();

      expect(refreshCalls, 1);
      expect(
        find.byKey(const ValueKey('conversation-working-status-text')),
        findsOneWidget,
      );
      expect(find.text('mobile completed'), findsNothing);
    },
  );

  testWidgets(
    'acknowledged pane send becomes unconfirmed without start evidence',
    (tester) async {
      final terminalTransport = RecordingTerminalTransport();
      final repository = RecordingGatewayRepository();
      var refreshCalls = 0;
      final agent = _statusAgent(
        provider: 'claude',
        activityState: 'idle',
        activitySource: 'claude_runtime',
        activityReason: 'claude_pane_idle_prompt',
      );
      final view = _workspaceView(
        agent,
        generatedAt: DateTime.utc(2026, 7, 28, 10),
        sequence: 10,
        namespaceEpoch: 4,
      );

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: SelectedAgentWorkspace(
              repository: repository,
              terminalTransport: terminalTransport,
              usePaneInputForMessages: true,
              view: view,
              agent: agent,
              enableComposerCollapse: true,
              onRefreshView: () async {
                refreshCalls += 1;
                return view;
              },
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      await tester.enterText(
        find.byKey(const ValueKey('agent-message-composer')),
        'provider never starts',
      );
      await tester.tap(find.byKey(const ValueKey('agent-message-send-button')));
      await tester.pump();

      expect(
        find.byKey(const ValueKey('conversation-working-status-text')),
        findsOneWidget,
      );

      await tester.pump(agentTurnStartObservationTimeout);
      await tester.pumpAndSettle();

      expect(refreshCalls, greaterThanOrEqualTo(2));
      expect(
        find.byKey(const ValueKey('conversation-working-status-text')),
        findsNothing,
      );
      expect(find.text('Check pane'), findsOneWidget);
    },
  );

  testWidgets('idle snapshots do not replay or poll a pending pane send', (
    tester,
  ) async {
    final terminalTransport = RecordingTerminalTransport();
    final repository = ControlledCompletedReplyConversationRepository();
    var refreshCalls = 0;
    var view = _workspaceView(
      _statusAgent(
        activityState: 'idle',
        activitySource: 'provider_pane',
        activityReason: 'provider_prompt_idle',
      ),
    );

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: StatefulBuilder(
            builder: (context, setState) {
              final agent = view.agentByName('mobile')!;
              return SelectedAgentWorkspace(
                repository: repository,
                terminalTransport: terminalTransport,
                usePaneInputForMessages: true,
                view: view,
                agent: agent,
                enableComposerCollapse: true,
                onRefreshView: () async {
                  refreshCalls += 1;
                  final refreshed = _workspaceView(
                    refreshCalls == 1
                        ? _statusAgent(
                          activityState: 'active',
                          activitySource: 'codex_runtime',
                          activityReason: 'codex_working_status_line',
                        )
                        : _statusAgent(
                          activityState: 'idle',
                          activitySource: 'provider_pane',
                          activityReason: 'provider_prompt_idle',
                        ),
                  );
                  setState(() {
                    view = refreshed;
                  });
                  return refreshed;
                },
              );
            },
          ),
        ),
      ),
    );
    await tester.pumpAndSettle();

    await tester.enterText(
      find.byKey(const ValueKey('agent-message-composer')),
      'work then really idle',
    );
    await tester.tap(find.byKey(const ValueKey('agent-message-send-button')));
    await tester.pump();

    expect(
      find.byKey(const ValueKey('conversation-working-status-text')),
      findsOneWidget,
    );

    await tester.pump(const Duration(milliseconds: 120));
    await tester.pump();

    expect(refreshCalls, 0);
    expect(
      find.byKey(const ValueKey('conversation-working-status-text')),
      findsOneWidget,
    );
    expect(find.text('mobile completed'), findsNothing);

    await tester.pump(const Duration(seconds: 2));
    await tester.pumpAndSettle();

    expect(refreshCalls, 0);
    expect(find.text('completed reply after idle'), findsNothing);
    expect(
      find.byKey(const ValueKey('conversation-working-status-text')),
      findsOneWidget,
    );
    expect(find.text('mobile completed'), findsNothing);
  });

  testWidgets(
    'conversation changes require an explicit invalidation or refresh',
    (tester) async {
      final terminalTransport = RecordingTerminalTransport();
      final repository = WorkingPaneConversationRepository();
      var view = _workspaceView(
        _statusAgent(
          activityState: 'idle',
          activitySource: 'provider_pane',
          activityReason: 'provider_prompt_idle',
        ),
      );

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: StatefulBuilder(
              builder: (context, setState) {
                final agent = view.agentByName('mobile')!;
                return SelectedAgentWorkspace(
                  repository: repository,
                  terminalTransport: terminalTransport,
                  usePaneInputForMessages: true,
                  view: view,
                  agent: agent,
                  enableComposerCollapse: true,
                  onRefreshView: () async {
                    final refreshed = _workspaceView(
                      _statusAgent(
                        activityState: 'active',
                        activitySource: 'codex_runtime',
                        activityReason: 'codex_working_status_line',
                      ),
                    );
                    setState(() {
                      view = refreshed;
                    });
                    return refreshed;
                  },
                );
              },
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(repository.conversationCalls, hasLength(1));

      await tester.enterText(
        find.byKey(const ValueKey('agent-message-composer')),
        'show reply while working',
      );
      await tester.tap(find.byKey(const ValueKey('agent-message-send-button')));
      await tester.pump();

      expect(
        find.byKey(const ValueKey('conversation-working-status-text')),
        findsOneWidget,
      );
      expect(find.text('reply while still working'), findsNothing);

      final callsAfterConfirmation = repository.conversationCalls.length;
      await tester.pump(const Duration(seconds: 2));
      await tester.pump();

      expect(repository.conversationCalls.length, callsAfterConfirmation);
      expect(find.text('reply while still working'), findsNothing);
      expect(find.text('mobile completed'), findsNothing);
      expect(
        find.byKey(const ValueKey('conversation-working-status-text')),
        findsOneWidget,
      );
    },
  );

  testWidgets(
    'idle pane status does not synthesize a terminal reply without invalidation',
    (tester) async {
      final terminalTransport = RecordingTerminalTransport();
      final repository = LateIdleReplyConversationRepository();
      var view = _workspaceView(
        _statusAgent(
          activityState: 'idle',
          activitySource: 'provider_pane',
          activityReason: 'provider_prompt_idle',
        ),
      );

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: StatefulBuilder(
              builder: (context, setState) {
                final agent = view.agentByName('mobile')!;
                return SelectedAgentWorkspace(
                  repository: repository,
                  terminalTransport: terminalTransport,
                  usePaneInputForMessages: true,
                  view: view,
                  agent: agent,
                  enableComposerCollapse: true,
                  onRefreshView: () async {
                    final refreshed = _workspaceView(
                      _statusAgent(
                        activityState: 'idle',
                        activitySource: 'provider_pane',
                        activityReason: 'provider_prompt_idle',
                      ),
                    );
                    setState(() {
                      view = refreshed;
                    });
                    return refreshed;
                  },
                );
              },
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      await tester.enterText(
        find.byKey(const ValueKey('agent-message-composer')),
        'late reply under idle view',
      );
      await tester.tap(find.byKey(const ValueKey('agent-message-send-button')));
      await tester.pump();

      expect(
        find.byKey(const ValueKey('conversation-working-status-text')),
        findsOneWidget,
      );
      expect(find.text('late reply under idle view'), findsOneWidget);

      terminalTransport.sessions.single.addOutput(
        'late reply under idle view\n',
      );
      await tester.pump();

      final callsAfterConfirmation = repository.conversationCalls.length;
      await tester.pump(const Duration(seconds: 2));
      await tester.pumpAndSettle();

      expect(repository.conversationCalls.length, callsAfterConfirmation);
      expect(
        find.byKey(const ValueKey('conversation-working-status-text')),
        findsOneWidget,
      );
      expect(find.text('late running reply'), findsNothing);
      expect(
        find.byKey(const ValueKey('conversation-working-reply-late')),
        findsNothing,
      );
      expect(
        find.byKey(const ValueKey('conversation-working-status-text')),
        findsOneWidget,
      );
      expect(find.text('mobile completed'), findsNothing);
    },
  );

  testWidgets(
    'selected agent activity update refreshes session and marks running reply',
    (tester) async {
      final repository = RunningStatusConversationRepository();
      final viewNotifier = ValueNotifier<CcbProjectView>(
        _workspaceView(
          _statusAgent(
            activityState: 'idle',
            activitySource: 'provider_pane',
            activityReason: 'provider_prompt_idle',
          ),
        ),
      );

      await tester.pumpWidget(
        MaterialApp(
          home: Scaffold(
            body: ValueListenableBuilder<CcbProjectView>(
              valueListenable: viewNotifier,
              builder: (context, view, _) {
                final agent = view.agentByName('mobile')!;
                return SelectedAgentWorkspace(
                  repository: repository,
                  terminalTransport: null,
                  usePaneInputForMessages: false,
                  view: view,
                  agent: agent,
                  enableComposerCollapse: true,
                  onRefreshView: () async => viewNotifier.value,
                );
              },
            ),
          ),
        ),
      );
      await tester.pumpAndSettle();

      expect(repository.conversationCalls, hasLength(1));
      expect(find.text('completed before update'), findsOneWidget);
      expect(
        find.byKey(const ValueKey('conversation-working-reply-running')),
        findsNothing,
      );

      viewNotifier.value = _workspaceView(
        _statusAgent(
          activityState: 'active',
          activitySource: 'codex_runtime',
          activityReason: 'codex_working_status_line',
        ),
      );
      await tester.pump();
      await tester.pumpAndSettle();

      expect(repository.conversationCalls.length, greaterThan(1));
      expect(find.text('running after activity update'), findsOneWidget);
      expect(
        find.byKey(
          ValueKey(
            'conversation-working-${syntheticAgentWorkingConversationItemId('mobile')}',
          ),
        ),
        findsOneWidget,
      );
      expect(find.byKey(const ValueKey('agent-working-status')), findsNothing);
      expect(find.textContaining('Working ·'), findsOneWidget);
    },
  );

  testWidgets('paired pane command result refreshes session conversation', (
    tester,
  ) async {
    final secureStore = MemorySecureStore();
    final profileStore = GatewayHostProfileStore(secureStore: secureStore);
    final host = GatewayPairedHost(
      profile: GatewayHostProfile(
        hostId: 'proj-demo',
        deviceId: 'dev-pane-status',
        routeProvider: RouteProvider(
          kind: RouteProviderKind.lan,
          gatewayUrl: Uri.parse('http://127.0.0.1:8787'),
        ),
        scopes: const {'view', 'content', 'focus', 'terminal_input', 'notify'},
      ),
      deviceToken: 'device-secret',
      projectId: 'proj-demo',
    );
    await profileStore.save(host);
    final terminalTransport = RecordingTerminalTransport();
    final repository = StatusConversationRepository();

    await tester.pumpWidget(
      MaterialApp(
        home: ProjectHomeScreen(
          repository: FakeMobileCcbRepository.demo(),
          profileStore: profileStore,
          gatewayRepositoryFactory: (profile) => repository,
          gatewayTerminalTransportFactory: (profile) => terminalTransport,
        ),
      ),
    );
    await tester.pumpAndSettle();
    await activateStoredGatewayProfile(tester);

    await tester.enterText(
      find.byKey(const ValueKey('agent-message-composer')),
      '/status',
    );
    await tester.tap(find.byKey(const ValueKey('agent-message-send-button')));
    await tester.pump();
    await tester.pump(const Duration(seconds: 1));
    await tester.pumpAndSettle();

    expect(repository.submittedMessages, isEmpty);
    expect(terminalTransport.sessions.single.pasted, ['/status']);
    expect(terminalTransport.sessions.single.written, [
      [13],
    ]);
    expect(repository.conversationCalls, isNotEmpty);
    expect(repository.terminalHistoryCalls, isEmpty);
    expect(find.text('Credits remaining: 42%'), findsOneWidget);
  });

  testWidgets('Codex queue button uses native slash command and preserves visible draft', (
    tester,
  ) async {
    await setTestSurfaceSize(tester, const Size(390, 844));

    final secureStore = MemorySecureStore();
    final profileStore = GatewayHostProfileStore(secureStore: secureStore);
    final host = GatewayPairedHost(
      profile: GatewayHostProfile(
        hostId: 'proj-demo',
        deviceId: 'dev-tab-draft',
        routeProvider: RouteProvider(
          kind: RouteProviderKind.lan,
          gatewayUrl: Uri.parse('http://127.0.0.1:8787'),
        ),
        scopes: const {'view', 'content', 'focus', 'terminal_input', 'notify'},
      ),
      deviceToken: 'device-secret',
      projectId: 'proj-demo',
    );
    await profileStore.save(host);
    final terminalTransport = RecordingTerminalTransport();
    final repository = RecordingGatewayRepository();

    await tester.pumpWidget(
      MaterialApp(
        home: ProjectHomeScreen(
          repository: FakeMobileCcbRepository.demo(),
          profileStore: profileStore,
          gatewayRepositoryFactory: (profile) => repository,
          gatewayTerminalTransportFactory: (profile) => terminalTransport,
        ),
      ),
    );
    await tester.pumpAndSettle();
    await activateStoredGatewayProfile(tester);

    await tester.tap(find.byKey(const ValueKey('agent-message-composer')));
    await tester.pump();
    await tester.enterText(
      find.byKey(const ValueKey('agent-message-composer')),
      'draft before tab',
    );
    await tester.pump();

    expect(find.byKey(const ValueKey('agent-queue-button')), findsOneWidget);
    await tester.tap(find.byKey(const ValueKey('agent-queue-button')));
    await tester.pump();

    expect(repository.submittedMessages, isEmpty);
    expect(terminalTransport.requests, hasLength(1));
    expect(terminalTransport.sessions.single.pasted, ['/queue draft before tab']);
    expect(terminalTransport.sessions.single.written, [
      [13],
    ]);
    expect(find.text('draft before tab'), findsOneWidget);
    expect(
      tester
          .widget<TextField>(
            find.byKey(const ValueKey('agent-message-composer')),
          )
          .controller!
          .text,
      isEmpty,
    );
  });

  testWidgets('Codex queue button uploads and queues image attachment', (
    tester,
  ) async {
    await setTestSurfaceSize(tester, const Size(390, 844));

    final originalPicker = FilePickerPlatform.instance;
    final tempDir = Directory.systemTemp.createTempSync(
      'ccb-mobile-tab-image-',
    );
    final image = File('${tempDir.path}/picked-image-cache');
    image.writeAsBytesSync([0xff, 0xd8, 0xff, 0xd9]);
    addTearDown(() {
      FilePickerPlatform.instance = originalPicker;
      tempDir.deleteSync(recursive: true);
    });
    FilePickerPlatform.instance = _FakeFilePicker([
      FilePickerResult([
        PlatformFile(
          name: 'queued-photo.jpg',
          path: image.path,
          size: image.lengthSync(),
        ),
      ]),
    ]);

    final secureStore = MemorySecureStore();
    final profileStore = GatewayHostProfileStore(secureStore: secureStore);
    final host = GatewayPairedHost(
      profile: GatewayHostProfile(
        hostId: 'proj-demo',
        deviceId: 'dev-tab-image',
        routeProvider: RouteProvider(
          kind: RouteProviderKind.lan,
          gatewayUrl: Uri.parse('http://127.0.0.1:8787'),
        ),
        scopes: const {'view', 'content', 'focus', 'terminal_input', 'notify'},
      ),
      deviceToken: 'device-secret',
      projectId: 'proj-demo',
    );
    await profileStore.save(host);
    final terminalTransport = RecordingTerminalTransport();
    final repository = ImageUploadGatewayRepository();

    await tester.pumpWidget(
      MaterialApp(
        home: ProjectHomeScreen(
          repository: FakeMobileCcbRepository.demo(),
          profileStore: profileStore,
          gatewayRepositoryFactory: (profile) => repository,
          gatewayTerminalTransportFactory: (profile) => terminalTransport,
        ),
      ),
    );
    await tester.pumpAndSettle();
    await activateStoredGatewayProfile(tester);

    await tester.tap(find.byKey(const ValueKey('agent-attachment-button')));
    await tester.pumpAndSettle();
    await tester.tap(find.byKey(const ValueKey('agent-attachment-pick-image')));
    await tester.pumpAndSettle();
    await tester.enterText(
      find.byKey(const ValueKey('agent-message-composer')),
      'queue this image',
    );

    await tester.tap(find.byKey(const ValueKey('agent-queue-button')));
    for (
      var attempt = 0;
      attempt < 20 && repository.pathUploads.isEmpty;
      attempt += 1
    ) {
      await tester.pump(const Duration(milliseconds: 50));
    }
    await tester.pumpAndSettle();

    expect(repository.pathUploads.single.fileName, 'queued-photo.jpg');
    expect(terminalTransport.sessions.single.pasted.single, startsWith('/queue '));
    expect(repository.pathUploads.single.mimeType, 'image/jpeg');
    expect(
      terminalTransport.sessions.single.pasted.single,
      contains('queue this image'),
    );
    expect(
      terminalTransport.sessions.single.pasted.single,
      contains('queued-photo.jpg'),
    );
    expect(terminalTransport.sessions.single.written, [
      [13],
    ]);
    expect(find.text('queue this image'), findsOneWidget);
    expect(
      find.byKey(
        const ValueKey('conversation-attachment-chip-uploaded-image-1'),
      ),
      findsOneWidget,
    );
    expect(find.byKey(const ValueKey('agent-attachment-tray')), findsNothing);
  });

  testWidgets(
    'stale namespace epoch keeps the draft and never replays chat send',
    (tester) async {
      final repository = StaleEpochGatewayRepository();
      await tester.pumpWidget(
        MaterialApp(home: ProjectHomeScreen(repository: repository)),
      );
      await tester.pumpAndSettle();
      await openCurrentProject(tester);

      expect(
        find.byKey(const ValueKey('agent-message-composer')),
        findsOneWidget,
      );

      await tester.enterText(
        find.byKey(const ValueKey('agent-message-composer')),
        'retry after stale epoch',
      );
      await tester.pump();
      final sendButton = tester.widget<IconButton>(
        find.byKey(const ValueKey('agent-message-send-button')),
      );
      sendButton.onPressed!();
      await tester.pumpAndSettle();

      expect(repository.getProjectViewCalls, 1);
      expect(
        [for (final item in repository.submittedMessages) item.namespaceEpoch],
        [4],
      );
      expect(find.text('retry after stale epoch'), findsOneWidget);
      expect(find.text('Failed'), findsOneWidget);
    },
  );
}

class _QueuedCollabRepository extends RecordingGatewayRepository {
  @override
  Future<CcbAgentConversation> getAgentConversation({
    required String projectId, required String agent, required int namespaceEpoch,
    int limit = 50, String? cursor,
  }) async => CcbAgentConversation(
    projectId: projectId, agentName: agent, namespaceEpoch: namespaceEpoch,
    items: const [], ompSessionId: 'native', ompGeneration: 'instance:1',
  );

  @override
  Future<CcbAgentMessageSubmitResult> submitAgentMessage(CcbAgentMessageSubmitRequest request) async {
    submittedMessages.add(request);
    final state = submittedMessages.length == 1
        ? CcbConversationDeliveryState.sent : CcbConversationDeliveryState.queued;
    return CcbAgentMessageSubmitResult(
      accepted: true, idempotencyKey: request.idempotencyKey,
      messageId: request.idempotencyKey, state: state,
      message: CcbConversationItem(
        id: request.idempotencyKey, agentName: request.agentName,
        kind: CcbConversationItemKind.userMessage, title: 'You', body: request.body,
        source: 'mobile/collab', state: state,
      ),
    );
  }
}

File _tempFile(Directory dir, String name) {
  final file = File('${dir.path}/$name');
  file.writeAsStringSync('test attachment $name');
  return file;
}

Future<void> _waitForFinder(
  WidgetTester tester,
  Finder finder, {
  Duration timeout = const Duration(seconds: 5),
}) async {
  final stopwatch = Stopwatch()..start();
  while (stopwatch.elapsed < timeout) {
    await tester.pump(const Duration(milliseconds: 50));
    if (tester.any(finder)) {
      return;
    }
  }
  expect(finder, findsOneWidget);
}

class _WorkspaceRefreshStatusHarness extends StatefulWidget {
  const _WorkspaceRefreshStatusHarness({
    this.refreshActivityReason = 'codex_runtime_reconnecting',
  });

  final String refreshActivityReason;

  @override
  State<_WorkspaceRefreshStatusHarness> createState() =>
      _WorkspaceRefreshStatusHarnessState();
}

class _WorkspaceRefreshStatusHarnessState
    extends State<_WorkspaceRefreshStatusHarness> {
  final SelectedAgentWorkspaceController _controller =
      SelectedAgentWorkspaceController();
  late CcbAgent _agent = _statusAgent();
  late CcbProjectView _view = _workspaceView(_agent);

  @override
  Widget build(BuildContext context) {
    return Column(
      children: [
        IconButton(
          key: const ValueKey('test-header-refresh-action'),
          onPressed: _controller.refreshLatest,
          icon: const Icon(Icons.refresh),
        ),
        Expanded(
          child: SelectedAgentWorkspace(
            repository: FakeMobileCcbRepository.demo(),
            terminalTransport: null,
            usePaneInputForMessages: false,
            view: _view,
            agent: _agent,
            enableComposerCollapse: true,
            controller: _controller,
            onRefreshView: () async {
              final agent = _statusAgent(
                activityState: 'pending',
                activitySource: 'codex_runtime',
                activityReason: widget.refreshActivityReason,
              );
              final view = _workspaceView(agent);
              setState(() {
                _agent = agent;
                _view = view;
              });
              return view;
            },
          ),
        ),
      ],
    );
  }
}

CcbAgent _statusAgent({
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

CcbAgent _agentNamed(String name) {
  return CcbAgent(
    name: name,
    provider: 'codex',
    window: 'main',
    order: name == 'mobile' ? 0 : 1,
    active: true,
    queueDepth: 0,
  );
}

class _FakeFilePicker extends FilePickerPlatform {
  _FakeFilePicker(this.results);

  final List<FilePickerResult?> results;
  final requestedTypes = <FileType>[];
  var _index = 0;

  @override
  Future<FilePickerResult?> pickFiles({
    String? dialogTitle,
    String? initialDirectory,
    FileType type = FileType.any,
    List<String>? allowedExtensions,
    Function(FilePickerStatus)? onFileLoading,
    int compressionQuality = 0,
    bool allowMultiple = false,
    bool withData = false,
    bool withReadStream = false,
    bool lockParentWindow = false,
    bool readSequential = false,
    bool cancelUploadOnWindowBlur = true,
  }) async {
    requestedTypes.add(type);
    if (_index >= results.length) {
      return null;
    }
    return results[_index++];
  }
}

CcbProjectView _workspaceView(
  CcbAgent agent, {
  DateTime? generatedAt,
  int? sequence,
  int namespaceEpoch = 7,
}) {
  return CcbProjectView(
    project: const CcbProject(
      id: 'proj-demo',
      displayName: 'Project',
      root: '/tmp/project',
    ),
    namespaceEpoch: namespaceEpoch,
    tmuxSocketPath: null,
    tmuxSessionName: null,
    activeWindow: agent.window,
    activePaneId: null,
    windows: const [],
    agents: [agent],
    contentItems: const [],
    notifications: const [],
    terminalHistories: const {},
    generatedAt: generatedAt,
    sequence: sequence,
  );
}

class DownloadGateRepository extends RecordingGatewayRepository {
  DownloadGateRepository({this.attachmentSizeBytes = 16});

  final int attachmentSizeBytes;
  final _downloadGate = Completer<List<int>>();
  var downloadCalls = 0;

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
          id: 'gateway-reply-with-file',
          agentName: 'mobile',
          kind: CcbConversationItemKind.agentReply,
          title: 'Gateway reply',
          body: 'Download the file.',
          attachments: [
            CcbMessageAttachment(
              fileId: 'gateway-file',
              fileName: 'gateway-notes.txt',
              mimeType: 'text/plain',
              sizeBytes: attachmentSizeBytes,
            ),
          ],
        ),
      ],
      generatedAt: DateTime.utc(2026, 6, 23),
    );
  }

  @override
  Future<List<int>> downloadFile({
    required String projectId,
    required String agentName,
    required String fileId,
  }) {
    downloadCalls += 1;
    return _downloadGate.future;
  }
}

class FallbackTerminalHistoryRepository extends RecordingGatewayRepository {
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
          id: 'conversation-without-terminal-$agent',
          agentName: agent,
          kind: CcbConversationItemKind.agentReply,
          title: 'Agent reply',
          body: 'Conversation endpoint has no pane history.',
          source: 'repository',
        ),
      ],
      generatedAt: DateTime.utc(2026, 6, 26),
    );
  }
}

class RunningStatusConversationRepository extends RecordingGatewayRepository {
  var _loads = 0;

  @override
  Future<CcbAgentConversation> getAgentConversation({
    required String projectId,
    required String agent,
    required int namespaceEpoch,
    int limit = 50,
    String? cursor,
  }) async {
    conversationCalls.add((projectId, agent, namespaceEpoch));
    _loads += 1;
    final now = DateTime.utc(2026, 6, 30, 12);
    return CcbAgentConversation(
      projectId: projectId,
      agentName: agent,
      namespaceEpoch: namespaceEpoch,
      items: [
        _loads == 1
            ? CcbConversationItem(
              id: 'reply-completed',
              agentName: agent,
              kind: CcbConversationItemKind.agentReply,
              title: 'Agent reply',
              body: 'completed before update',
              source: 'provider_native/codex',
              startedAt: now.subtract(const Duration(minutes: 2)),
              completedAt: now.subtract(const Duration(minutes: 1)),
            )
            : CcbConversationItem(
              id: 'reply-running',
              agentName: agent,
              kind: CcbConversationItemKind.agentReply,
              title: 'Agent reply',
              body: 'running after activity update',
              source: 'provider_native/codex',
              startedAt: now,
            ),
      ],
      generatedAt: now,
    );
  }
}

class WorkingPaneConversationRepository extends RecordingGatewayRepository {
  var _loads = 0;

  @override
  Future<CcbAgentConversation> getAgentConversation({
    required String projectId,
    required String agent,
    required int namespaceEpoch,
    int limit = 50,
    String? cursor,
  }) async {
    conversationCalls.add((projectId, agent, namespaceEpoch));
    _loads += 1;
    final now = DateTime.utc(2026, 6, 30, 12);
    return CcbAgentConversation(
      projectId: projectId,
      agentName: agent,
      namespaceEpoch: namespaceEpoch,
      items:
          _loads < 4
              ? const []
              : [
                CcbConversationItem(
                  id: 'reply-working-live',
                  agentName: agent,
                  kind: CcbConversationItemKind.agentReply,
                  title: 'Agent reply',
                  body: 'reply while still working',
                  source: 'provider_native/codex',
                  startedAt: now,
                ),
              ],
      generatedAt: now,
    );
  }
}

class LateIdleReplyConversationRepository extends RecordingGatewayRepository {
  var _loads = 0;

  @override
  Future<CcbAgentConversation> getAgentConversation({
    required String projectId,
    required String agent,
    required int namespaceEpoch,
    int limit = 50,
    String? cursor,
  }) async {
    conversationCalls.add((projectId, agent, namespaceEpoch));
    _loads += 1;
    final now = DateTime.utc(2100, 7, 1, 12);
    return CcbAgentConversation(
      projectId: projectId,
      agentName: agent,
      namespaceEpoch: namespaceEpoch,
      items:
          _loads < 4
              ? const []
              : [
                CcbConversationItem(
                  id: 'reply-late',
                  agentName: agent,
                  kind: CcbConversationItemKind.agentReply,
                  title: 'Agent reply',
                  body: 'late running reply',
                  source: 'provider_native/codex',
                  startedAt: now,
                ),
              ],
      generatedAt: now.add(Duration(seconds: _loads)),
    );
  }
}

class ControlledCompletedReplyConversationRepository
    extends RecordingGatewayRepository {
  var _showReply = false;

  void releaseReply() {
    _showReply = true;
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
    final startedAt = DateTime.utc(2100, 7, 1, 12);
    return CcbAgentConversation(
      projectId: projectId,
      agentName: agent,
      namespaceEpoch: namespaceEpoch,
      items:
          !_showReply
              ? const []
              : [
                CcbConversationItem(
                  id: 'reply-completed-after-idle',
                  agentName: agent,
                  kind: CcbConversationItemKind.agentReply,
                  title: 'Agent reply',
                  body: 'completed reply after idle',
                  source: 'provider_native/codex',
                  startedAt: startedAt,
                  completedAt: startedAt.add(const Duration(seconds: 3)),
                ),
              ],
      generatedAt: startedAt,
    );
  }
}

class ImageUploadGatewayRepository extends RecordingGatewayRepository
    implements MobileCcbRepositoryFileUploader {
  final pathUploads = <_ImagePathUpload>[];

  @override
  Future<GatewayFileUploadResult> uploadFileFromPath({
    required String projectId,
    required String agentName,
    required String fileName,
    required String mimeType,
    required String path,
  }) async {
    pathUploads.add(
      _ImagePathUpload(
        projectId: projectId,
        agentName: agentName,
        fileName: fileName,
        mimeType: mimeType,
        path: path,
      ),
    );
    return GatewayFileUploadResult(
      fileId: 'uploaded-image-${pathUploads.length}',
      fileName: fileName,
      mimeType: mimeType,
      sizeBytes: 4,
    );
  }
}

class PaneImageEchoRepository extends ImageUploadGatewayRepository {
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
      items:
          pathUploads.isEmpty
              ? const []
              : [
                CcbConversationItem.userMessage(
                  id: 'remote-image-echo',
                  agentName: agent,
                  body:
                      'please inspect this image\n'
                      'Attached files:\n'
                      '- camera-roll-image.jpg (image/jpeg, 4 bytes, '
                      'file id: uploaded-image-1)',
                  state: CcbConversationDeliveryState.sent,
                ),
              ],
      generatedAt: DateTime.utc(2026, 7, 1, 12),
    );
  }
}

class _ImagePathUpload {
  const _ImagePathUpload({
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

class StatusConversationRepository extends RecordingGatewayRepository {
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
          id: 'status-reply-$agent',
          agentName: agent,
          kind: CcbConversationItemKind.agentReply,
          title: 'Agent reply',
          body: 'Credits remaining: 42%',
          source: 'provider_native/codex',
          completedAt: DateTime.utc(2026, 6, 30, 12),
        ),
      ],
      generatedAt: DateTime.utc(2026, 6, 30, 12),
    );
  }
}

class PaneConversationRepository extends RecordingGatewayRepository {
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
          id: 'pane-conversation-$agent',
          agentName: agent,
          kind: CcbConversationItemKind.agentReply,
          title: 'Agent reply',
          body: 'Pane conversation visible',
          source: 'tmux output / tmux_scrollback / %2',
        ),
      ],
      generatedAt: DateTime.utc(2026, 6, 26),
    );
  }
}

class NativeConversationRepository extends RecordingGatewayRepository {
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
          id: 'native-conversation-$agent',
          agentName: agent,
          kind: CcbConversationItemKind.agentReply,
          title: 'Agent reply',
          body: 'Native conversation visible',
          source: 'provider_native/codex',
        ),
      ],
      generatedAt: DateTime.utc(2026, 6, 26),
    );
  }
}

class BlockingPasteTerminalTransport implements TerminalTransport {
  BlockingPasteTerminalTransport({this.writeError});

  final Object? writeError;
  final _pasteGate = Completer<void>();
  final requests = <TerminalOpenRequest>[];
  final sessions = <BlockingPasteTerminalSession>[];

  @override
  Future<TerminalSession> open(TerminalOpenRequest request) async {
    requests.add(request);
    final session = BlockingPasteTerminalSession(
      request.attachCommand,
      pasteGate: _pasteGate.future,
      writeError: writeError,
    );
    sessions.add(session);
    return session;
  }

  void completePaste() {
    if (!_pasteGate.isCompleted) {
      _pasteGate.complete();
    }
  }
}

class BlockingPasteTerminalSession implements TerminalSession {
  BlockingPasteTerminalSession(
    this.launchedCommand, {
    required Future<void> pasteGate,
    this.writeError,
  }) : _pasteGate = pasteGate;

  final Future<void> _pasteGate;
  final Object? writeError;
  final _output = StreamController<Uint8List>.broadcast();

  @override
  final String launchedCommand;

  final pasted = <String>[];
  final written = <List<int>>[];

  @override
  Stream<Uint8List> get output => _output.stream;

  @override
  Future<void> close() async {
    await _output.close();
  }

  @override
  Future<void> paste(String text) async {
    pasted.add(text);
    await _pasteGate;
  }

  @override
  Future<void> reconnect() async {}

  @override
  Future<void> resize(TerminalGeometry geometry) async {}

  @override
  Future<void> writeBytes(List<int> bytes) async {
    final error = writeError;
    if (error != null) {
      throw error;
    }
    written.add(bytes);
  }
}
