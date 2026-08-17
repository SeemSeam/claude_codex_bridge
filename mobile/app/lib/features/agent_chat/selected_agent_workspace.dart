import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart' show ScrollDirection;
import 'package:file_picker/file_picker.dart';
import 'package:mime/mime.dart';
import 'package:path_provider/path_provider.dart';
import 'package:open_filex/open_filex.dart';
import 'package:path/path.dart' as p;

import '../../cache/mobile_snapshot_store.dart';
import '../../l10n/ccb_mobile_localizations.dart';
import '../../models/ccb_agent.dart';
import '../../models/ccb_agent_conversation.dart';
import '../../models/ccb_conversation_item.dart';
import '../../models/ccb_project_view.dart';
import '../../repository/mobile_ccb_repository.dart';
import '../../transport/terminal_transport.dart';
import 'agent_chat_controller.dart';
import 'agent_chat_ui_controller_store.dart';
import 'agent_conversation_refresh_coordinator.dart';
import 'agent_local_message_store.dart';
import 'agent_message_submit_coordinator.dart';
import 'agent_pane_event_coordinator.dart';
import 'agent_pane_message_submitter.dart';
import 'agent_submission_semantics.dart';
import 'agent_turn_sync_tracker.dart';
import 'pane_chat_controller.dart';
import 'selected_agent_workspace_model.dart';
import 'selected_agent_workspace_view.dart';

const agentMessageMaxAttachments = 5;
const agentMessageMaxAttachmentBytes = 25 * 1024 * 1024;
const agentMessageMaxDownloadBytes = 128 * 1024 * 1024;
const selectedAgentTabKeyBytes = [9];
const selectedAgentEscapeKeyBytes = [27];
const selectedAgentExpandScrollDuration = Duration(milliseconds: 220);

class SelectedAgentWorkspace extends StatefulWidget {
  const SelectedAgentWorkspace({
    required this.repository,
    required this.terminalTransport,
    required this.usePaneInputForMessages,
    required this.view,
    required this.agent,
    required this.enableComposerCollapse,
    required this.onRefreshView,
    this.onUserScrollDirectionChanged,
    this.onProjectActivity,
    this.localMessageStore,
    this.snapshotStore,
    this.snapshotNamespace,
    this.sendEnabled = true,
    this.sendDisabledReason,
    this.refreshToken = 0,
    this.controller,
  });

  final MobileCcbRepository repository;
  final TerminalTransport? terminalTransport;
  final bool usePaneInputForMessages;
  final CcbProjectView view;
  final CcbAgent? agent;
  final bool enableComposerCollapse;
  final Future<CcbProjectView?> Function()? onRefreshView;
  final ValueChanged<ScrollDirection>? onUserScrollDirectionChanged;
  final VoidCallback? onProjectActivity;
  final AgentLocalMessageStore? localMessageStore;
  final MobileSnapshotStore? snapshotStore;
  final String? snapshotNamespace;
  final bool sendEnabled;
  final String? sendDisabledReason;
  final int refreshToken;
  final SelectedAgentWorkspaceController? controller;

  @override
  State<SelectedAgentWorkspace> createState() => _SelectedAgentWorkspaceState();
}

class SelectedAgentWorkspaceController {
  VoidCallback? _refreshLatest;

  void refreshLatest() {
    _refreshLatest?.call();
  }

  void _attachRefreshLatest(VoidCallback refreshLatest) {
    _refreshLatest = refreshLatest;
  }

  void _detachRefreshLatest() {
    _refreshLatest = null;
  }

  void dispose() {
    _refreshLatest = null;
  }
}

class _SelectedAgentWorkspaceState extends State<SelectedAgentWorkspace>
    with WidgetsBindingObserver {
  final AgentChatController _chatController = AgentChatController();
  final AgentChatUiControllerStore _uiControllers =
      AgentChatUiControllerStore();
  late final AgentLocalMessageStore _localMessageStore =
      widget.localMessageStore ?? AgentLocalMessageStore();
  late final AgentConversationRefreshCoordinator
  _conversationRefreshCoordinator = AgentConversationRefreshCoordinator(
    chatController: _chatController,
    isMounted: () => mounted,
    mutateState: _mutateChatState,
    isTimelineNearEnd: _isTimelineNearEnd,
    scrollTimelineToEnd: _scrollTimelineToEnd,
    onConversationLoaded: _handleConversationLoaded,
  );
  late final AgentPaneEventCoordinator _paneEventCoordinator =
      AgentPaneEventCoordinator(
        chatController: _chatController,
        isMounted: () => mounted,
        mutateState: _mutateChatState,
        isTimelineNearEnd: _isTimelineNearEnd,
        scrollTimelineToEnd: _scrollTimelineToEnd,
      );
  late final AgentPaneMessageSubmitter _paneMessageSubmitter =
      AgentPaneMessageSubmitter(onEvent: _handlePaneChatEvent);
  late final AgentMessageSubmitCoordinator _messageSubmitCoordinator =
      AgentMessageSubmitCoordinator(
        chatController: _chatController,
        isMounted: () => mounted,
        mutateState: _mutateChatState,
        isTimelineNearEnd: _isTimelineNearEnd,
        scrollTimelineToEnd: _scrollTimelineToEnd,
        loadConversation: _loadConversation,
        paneSubmitter: _paneMessageSubmitter,
      );
  final Set<String> _downloadingAttachmentIds = {};
  final Map<String, String> _downloadedAttachmentPaths = {};
  final Map<String, double> _preExpansionTimelineOffsets = {};
  final Map<String, GlobalKey> _expandedTimelineItemKeys = {};
  final AgentTurnSyncTracker _turnSyncTracker = AgentTurnSyncTracker();
  final Map<String, int> _awaitingTurnGenerations = {};
  final Map<String, Timer> _startObservationTimers = {};
  final Set<String> _localExceptionStatusAgentNames = {};
  final Map<String, String> _recentPaneOutputText = {};
  final Set<String> _pendingClearNewMessageAgents = {};
  FocusNode? _observedDraftFocusNode;
  String? _observedDraftFocusAgentName;
  var _nextDraftAttachmentIndex = 0;

  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    widget.controller?._attachRefreshLatest(_refreshSelectedAgentLatest);
    _restoreLocalMessagesForSelectedAgent();
    _restoreConversationSnapshot();
    _loadSelectedAgentConversation();
  }

  @override
  void didUpdateWidget(covariant SelectedAgentWorkspace oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.controller != widget.controller) {
      oldWidget.controller?._detachRefreshLatest();
      widget.controller?._attachRefreshLatest(_refreshSelectedAgentLatest);
    }
    final projectOrAgentChanged =
        oldWidget.view.project.id != widget.view.project.id ||
        oldWidget.agent?.name != widget.agent?.name;
    if (oldWidget.repository != widget.repository ||
        oldWidget.terminalTransport != widget.terminalTransport ||
        oldWidget.view.project.id != widget.view.project.id ||
        oldWidget.view.namespaceEpoch != widget.view.namespaceEpoch ||
        oldWidget.agent?.name != widget.agent?.name) {
      if (oldWidget.terminalTransport != widget.terminalTransport ||
          oldWidget.view.project.id != widget.view.project.id ||
          oldWidget.view.namespaceEpoch != widget.view.namespaceEpoch) {
        unawaited(_paneMessageSubmitter.closeSessions());
        _chatController.clearRefreshedTerminalHistories();
      }
      if (projectOrAgentChanged) {
        _restoreLocalMessagesForSelectedAgent();
        _restoreConversationSnapshot();
      }
      _loadSelectedAgentConversation();
    } else {
      final agent = widget.agent;
      final activityChanged =
          agentActivitySignature(oldWidget.agent) !=
          agentActivitySignature(agent);
      final viewAdvanced =
          agent != null &&
          _turnSyncTracker.isAwaiting(agent.name) &&
          AgentViewWatermark.fromView(
            widget.view,
          ).isNewerThan(AgentViewWatermark.fromView(oldWidget.view));
      if (agent != null && (activityChanged || viewAdvanced)) {
        _syncLocalExecutionStateFromView(
          view: widget.view,
          agentName: agent.name,
        );
        unawaited(
          _refreshSelectedAgentConversation(agent, viewOverride: widget.view),
        );
      }
    }
    if (oldWidget.refreshToken != widget.refreshToken) {
      _loadSelectedAgentConversation();
    }
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _observedDraftFocusNode?.removeListener(_handleDraftFocusChanged);
    widget.controller?._detachRefreshLatest();
    for (final timer in _startObservationTimers.values) {
      timer.cancel();
    }
    _startObservationTimers.clear();
    unawaited(_paneMessageSubmitter.closeSessions());
    _uiControllers.dispose();
    super.dispose();
  }

  @override
  void didChangeMetrics() {
    super.didChangeMetrics();
    final agentName = widget.agent?.name;
    if (agentName != null) {
      _keepLatestVisibleAfterComposerChange(agentName);
    }
  }

  TextEditingController _draftController(String agentName) {
    return _uiControllers.draftController(agentName);
  }

  FocusNode _draftFocusNode(String agentName) {
    return _uiControllers.draftFocusNode(agentName);
  }

  void _observeDraftFocusNode(String agentName, FocusNode node) {
    if (_observedDraftFocusNode == node &&
        _observedDraftFocusAgentName == agentName) {
      return;
    }
    _observedDraftFocusNode?.removeListener(_handleDraftFocusChanged);
    _observedDraftFocusNode = node;
    _observedDraftFocusAgentName = agentName;
    node.addListener(_handleDraftFocusChanged);
  }

  void _handleDraftFocusChanged() {
    final agentName = _observedDraftFocusAgentName;
    if (agentName == null || !mounted || widget.agent?.name != agentName) {
      return;
    }
    _keepLatestVisibleAfterComposerChange(agentName);
  }

  List<CcbMessageAttachment> _draftAttachments(String agentName) {
    return _uiControllers.draftAttachments(agentName);
  }

  void _mutateChatState(void Function() update) {
    if (!mounted) {
      return;
    }
    final agentName = widget.agent?.name;
    if (agentName != null && _isTimelineNearEnd(agentName)) {
      _uiControllers.anchorTimelineToEndForNextLayout(agentName);
    }
    setState(update);
    if (agentName != null) {
      unawaited(_persistLocalMessages(agentName));
    }
  }

  Future<void> _restoreLocalMessagesForSelectedAgent() async {
    final agentName = widget.agent?.name;
    if (agentName == null) {
      return;
    }
    final projectId = widget.view.project.id;
    final List<CcbConversationItem> messages;
    try {
      messages = await _localMessageStore.load(
        projectId: projectId,
        agentName: agentName,
      );
    } catch (_) {
      return;
    }
    if (!mounted ||
        widget.view.project.id != projectId ||
        widget.agent?.name != agentName) {
      return;
    }
    setState(() {
      _chatController.restoreLocalMessages(agentName, messages);
    });
  }

  Future<void> _restoreConversationSnapshot() async {
    final store = widget.snapshotStore;
    final namespace = widget.snapshotNamespace;
    final agent = widget.agent;
    final epoch = widget.view.namespaceEpoch;
    if (store == null || namespace == null || agent == null || epoch == null) {
      return;
    }
    final projectId = widget.view.project.id;
    final payload = await store.read(
      mobileConversationSnapshotKey(
        namespace: namespace,
        projectId: projectId,
        agent: agent.name,
        namespaceEpoch: epoch,
      ),
    );
    if (!mounted ||
        payload == null ||
        widget.view.project.id != projectId ||
        widget.agent?.name != agent.name ||
        widget.view.namespaceEpoch != epoch) {
      return;
    }
    try {
      final conversation = CcbAgentConversation.fromJson(payload);
      if (conversation.projectId != projectId ||
          conversation.agentName != agent.name ||
          conversation.namespaceEpoch != epoch) {
        return;
      }
      setState(() {
        _chatController.applyRemoteConversation(
          agentName: agent.name,
          conversation: conversation,
          shouldScroll: false,
        );
      });
    } catch (_) {
      // Invalid snapshots are ignored; the authoritative refresh remains live.
    }
  }

  void _persistConversationSnapshot(CcbAgentConversation conversation) {
    final store = widget.snapshotStore;
    final namespace = widget.snapshotNamespace;
    if (store == null || namespace == null || conversation.namespaceEpoch < 0) {
      return;
    }
    unawaited(
      store.write(
        mobileConversationSnapshotKey(
          namespace: namespace,
          projectId: conversation.projectId,
          agent: conversation.agentName,
          namespaceEpoch: conversation.namespaceEpoch,
        ),
        conversation.toJson(),
      ),
    );
  }

  void _handleConversationLoaded(
    CcbAgentConversation conversation,
    CcbProjectView view,
  ) {
    _persistConversationSnapshot(conversation);
    if (!mounted ||
        widget.view.project.id != conversation.projectId ||
        view.project.id != conversation.projectId ||
        widget.agent?.name != conversation.agentName ||
        widget.view.namespaceEpoch != conversation.namespaceEpoch ||
        view.namespaceEpoch != conversation.namespaceEpoch) {
      return;
    }
    _syncLocalExecutionStateFromView(
      view: view,
      agentName: conversation.agentName,
      conversationReconciled: true,
    );
  }

  Future<void> _persistLocalMessages(String agentName) async {
    try {
      await _localMessageStore.save(
        projectId: widget.view.project.id,
        agentName: agentName,
        messages: _chatController.localMessagesFor(agentName),
      );
    } catch (_) {
      // Local retry state is best-effort; chat flow should continue.
    }
  }

  void _addAttachments(
    String agentName,
    List<CcbMessageAttachment> attachments,
  ) {
    setState(() {
      _uiControllers.addDraftAttachments(agentName, attachments);
    });
  }

  void _removeAttachment(String agentName, String localId) {
    setState(() {
      _uiControllers.removeDraftAttachment(agentName, localId);
    });
  }

  ScrollController _scrollController(String agentName) {
    return _uiControllers.timelineScrollController(agentName);
  }

  void _toggleExpandedItem(String agentName, String itemId) {
    final shouldReveal =
        !_chatController.expandedItemIds(agentName).contains(itemId);
    final restoreOffset =
        shouldReveal
            ? null
            : _preExpansionTimelineOffsets.remove(
              _expandedTimelineOffsetKey(agentName, itemId),
            );
    if (shouldReveal) {
      _uiControllers.cancelTimelineAutoFollow(agentName);
      _uiControllers.cancelTimelineEndAnchor(agentName);
      final controller = _scrollController(agentName);
      if (controller.hasClients) {
        _preExpansionTimelineOffsets[_expandedTimelineOffsetKey(
              agentName,
              itemId,
            )] =
            controller.position.pixels;
      }
    }
    setState(() {
      _chatController.toggleExpandedItem(agentName, itemId);
    });
    if (shouldReveal) {
      _scrollExpandedItemToBottom(agentName, itemId);
    } else if (restoreOffset != null) {
      _restoreTimelineOffset(agentName, restoreOffset);
    }
  }

  String _expandedTimelineOffsetKey(String agentName, String itemId) {
    return '$agentName:$itemId';
  }

  void _scrollExpandedItemToBottom(String agentName, String itemId) {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || widget.agent?.name != agentName) {
        return;
      }
      final controller = _scrollController(agentName);
      if (!controller.hasClients) {
        return;
      }
      if (_latestTimelineItemId(agentName) == itemId) {
        _scrollTimelineToEnd(agentName);
        return;
      }
      final itemContext = _expandedTimelineItemKey(itemId).currentContext;
      if (itemContext == null) {
        return;
      }
      Scrollable.ensureVisible(
        itemContext,
        alignment: 1,
        duration: selectedAgentExpandScrollDuration,
        curve: Curves.easeOutCubic,
      );
    });
  }

  GlobalKey _expandedTimelineItemKey(String itemId) {
    final scope = '${widget.view.project.id}:${widget.agent?.name}:$itemId';
    return _expandedTimelineItemKeys.putIfAbsent(
      scope,
      () => GlobalKey(debugLabel: 'expanded-conversation-item-$scope'),
    );
  }

  void _restoreTimelineOffset(String agentName, double offset) {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || widget.agent?.name != agentName) {
        return;
      }
      final controller = _scrollController(agentName);
      if (!controller.hasClients) {
        return;
      }
      final target = offset.clamp(
        controller.position.minScrollExtent,
        controller.position.maxScrollExtent,
      );
      controller.jumpTo(target.toDouble());
    });
  }

  void _loadSelectedAgentConversation() {
    if (!mounted) {
      return;
    }
    final agent = widget.agent;
    if (agent == null) {
      return;
    }
    unawaited(_refreshSelectedAgentConversation(agent));
  }

  Future<void> _refreshSelectedAgentConversation(
    CcbAgent agent, {
    CcbProjectView? viewOverride,
  }) async {
    if (!mounted || widget.agent?.name != agent.name) {
      return;
    }
    final view = viewOverride ?? widget.view;
    await _loadConversation(agent.name, viewOverride: view);
  }

  void _collapseComposer(String agentName) {
    if (!widget.enableComposerCollapse) {
      return;
    }
    final wasNearLatest = _isTimelineNearEnd(agentName);
    _draftFocusNode(agentName).unfocus();
    setState(() {
      _chatController.collapseComposer(agentName);
    });
    if (wasNearLatest) {
      _scrollTimelineToEnd(agentName);
    }
  }

  void _expandComposer(String agentName) {
    final wasNearLatest = _isTimelineNearEnd(agentName);
    setState(() {
      _chatController.expandComposer(agentName);
    });
    _draftFocusNode(agentName).requestFocus();
    if (wasNearLatest) {
      _scrollTimelineToEnd(agentName);
    }
  }

  Future<void> _loadConversation(
    String agentName, {
    CcbProjectView? viewOverride,
  }) async {
    await _conversationRefreshCoordinator.load(
      repository: widget.repository,
      view: viewOverride ?? widget.view,
      agentName: agentName,
      refreshView: widget.onRefreshView,
    );
  }

  Future<void> _sendMessage(CcbAgent agent) async {
    if (!widget.sendEnabled || widget.view.namespaceEpoch == null) {
      _showSnack(widget.sendDisabledReason ?? 'Refresh target before sending');
      return;
    }
    final controller = _draftController(agent.name);
    final body = controller.text;
    final attachments = _draftAttachments(agent.name);
    await _messageSubmitCoordinator.send(
      agent: agent,
      body: body,
      attachments: attachments,
      view: widget.view,
      repository: widget.repository,
      terminalTransport: widget.terminalTransport,
      usePaneInput: widget.usePaneInputForMessages,
      refreshView: widget.onRefreshView,
      onAccepted: () {
        controller.clear();
        _uiControllers.clearDraftAttachments(agent.name);
        _localExceptionStatusAgentNames.remove(agent.name);
        _recentPaneOutputText.remove(agent.name);
        widget.onProjectActivity?.call();
      },
      onDeliveryUpdated: (replacement) {
        _handleMessageDeliveryUpdated(
          agent: agent,
          body: body,
          hasAttachments: attachments.isNotEmpty,
          replacement: replacement,
        );
      },
    );
    // A successful pane write retains the single placeholder until an
    // invalidation-driven authoritative snapshot reconciles the turn.
  }

  Future<void> _sendPaneKey(
    CcbAgent agent, {
    required List<int> bytes,
    required String label,
  }) async {
    final outcome = await _paneMessageSubmitter.sendKey(
      transport: widget.terminalTransport,
      agent: agent,
      view: widget.view,
      refreshView: widget.onRefreshView,
      bytes: bytes,
    );
    if (!mounted || widget.agent?.name != agent.name) {
      return;
    }
    if (!outcome.sent) {
      _showSnack('Could not send $label: ${outcome.error}');
      return;
    }
    _refreshLatest(agent.name);
  }

  Future<void> _sendDraftThenPaneKey(
    CcbAgent agent, {
    required List<int> bytes,
    required String label,
  }) async {
    final controller = _draftController(agent.name);
    final body = controller.text;
    final attachments = _draftAttachments(agent.name);
    if (body.trim().isEmpty && attachments.isEmpty) {
      await _sendPaneKey(agent, bytes: bytes, label: label);
      return;
    }
    if (!widget.sendEnabled || widget.view.namespaceEpoch == null) {
      _showSnack(widget.sendDisabledReason ?? 'Refresh target before sending');
      return;
    }
    await _messageSubmitCoordinator.send(
      agent: agent,
      body: body,
      attachments: attachments,
      view: widget.view,
      repository: widget.repository,
      terminalTransport: widget.terminalTransport,
      usePaneInput: true,
      paneSubmitBytes: bytes,
      refreshView: widget.onRefreshView,
      onAccepted: () {
        controller.clear();
        _uiControllers.clearDraftAttachments(agent.name);
        _localExceptionStatusAgentNames.remove(agent.name);
        _recentPaneOutputText.remove(agent.name);
        widget.onProjectActivity?.call();
      },
      onDeliveryUpdated: (replacement) {
        _handleMessageDeliveryUpdated(
          agent: agent,
          body: body,
          hasAttachments: attachments.isNotEmpty,
          replacement: replacement,
        );
      },
    );
  }

  Future<void> _pickAttachments({
    required String agentName,
    required FileType type,
  }) async {
    try {
      final result = await FilePicker.pickFiles(
        allowMultiple: true,
        type: type,
      );
      if (result == null || result.files.isEmpty) {
        return;
      }
      final current = _draftAttachments(agentName);
      final remainingSlots = agentMessageMaxAttachments - current.length;
      if (remainingSlots <= 0) {
        _showSnack('Attach up to $agentMessageMaxAttachments files');
        return;
      }
      final accepted = <CcbMessageAttachment>[];
      for (final file in result.files.take(remainingSlots)) {
        final path = file.path;
        if (path == null || path.isEmpty) {
          continue;
        }
        final size = file.size;
        if (size > agentMessageMaxAttachmentBytes) {
          _showSnack('${file.name} is larger than 25 MB');
          continue;
        }
        final fileName = file.name.isEmpty ? p.basename(path) : file.name;
        final extension = _attachmentExtension(
          pickerExtension: file.extension,
          fileName: fileName,
          path: path,
        );
        final mimeType =
            lookupMimeType(path) ??
            lookupMimeType(fileName) ??
            _mimeTypeForExtension(extension) ??
            'application/octet-stream';
        if (!_isSupportedAttachment(
          type: type,
          extension: extension,
          mimeType: mimeType,
        )) {
          _showSnack('$fileName is not a supported attachment type');
          continue;
        }
        final localId = 'draft-$agentName-${_nextDraftAttachmentIndex++}';
        final storedPath = await _copyDraftAttachmentFile(
          sourcePath: path,
          localId: localId,
          fileName: fileName,
        );
        accepted.add(
          CcbMessageAttachment(
            fileId: localId,
            fileName: fileName,
            mimeType: mimeType,
            sizeBytes: size,
            localPath: storedPath,
            kind:
                mimeType.startsWith('image/')
                    ? CcbMessageAttachmentKind.image
                    : CcbMessageAttachmentKind.document,
            state: CcbMessageAttachmentState.queued,
          ),
        );
      }
      if (accepted.isNotEmpty) {
        _addAttachments(agentName, accepted);
        _focusComposer(agentName);
      }
      if (result.files.length > remainingSlots) {
        _showSnack('Attach up to $agentMessageMaxAttachments files');
      }
    } catch (error) {
      _showSnack('Could not pick attachment: $error');
    }
  }

  Future<String> _copyDraftAttachmentFile({
    required String sourcePath,
    required String localId,
    required String fileName,
  }) async {
    if (!Platform.isAndroid && !Platform.isIOS) {
      return sourcePath;
    }
    try {
      final dir = await getApplicationDocumentsDirectory();
      final draftDir = Directory(p.join(dir.path, 'draft_attachments'));
      await draftDir.create(recursive: true);
      final safeName = _safeFileName(fileName);
      final target = File(
        p.join(draftDir.path, '${_safeFileName(localId)}-$safeName'),
      );
      return (await File(sourcePath).copy(target.path)).path;
    } catch (_) {
      return sourcePath;
    }
  }

  Future<String?> _downloadAttachment(
    CcbAgent agent,
    CcbMessageAttachment attachment, {
    required String projectId,
    required bool openAfterDownload,
  }) async {
    if (!_isCurrentAgentSelection(projectId: projectId, agent: agent)) {
      return null;
    }
    try {
      final localPath = attachment.localPath;
      if (localPath != null && localPath.isNotEmpty) {
        if (openAfterDownload) {
          await _openAttachmentFile(localPath, mimeType: attachment.mimeType);
        }
        return localPath;
      }
      final downloadedPath = _downloadedAttachmentPaths[attachment.fileId];
      if (downloadedPath != null) {
        if (openAfterDownload) {
          await _openAttachmentFile(
            downloadedPath,
            mimeType: attachment.mimeType,
          );
        }
        return downloadedPath;
      }
      if (attachment.sizeBytes > agentMessageMaxDownloadBytes) {
        _showSnack('${attachment.fileName} is larger than 128 MB');
        return null;
      }
      if (_downloadingAttachmentIds.contains(attachment.fileId)) {
        return null;
      }
      setState(() {
        _downloadingAttachmentIds.add(attachment.fileId);
      });
      final bytes = await widget.repository.downloadFile(
        projectId: projectId,
        agentName: agent.name,
        fileId: attachment.fileId,
      );
      if (bytes.length > agentMessageMaxDownloadBytes) {
        if (_isCurrentAgentSelection(projectId: projectId, agent: agent)) {
          _showSnack('${attachment.fileName} is larger than 128 MB');
        }
        return null;
      }
      final dir = await getApplicationDocumentsDirectory();
      final file = File(p.join(dir.path, _safeFileName(attachment.fileName)));
      await file.writeAsBytes(bytes);
      if (!_isCurrentAgentSelection(projectId: projectId, agent: agent)) {
        return null;
      }
      setState(() {
        _downloadedAttachmentPaths[attachment.fileId] = file.path;
      });
      _showSnack('Saved ${attachment.fileName}');
      if (openAfterDownload) {
        await _openAttachmentFile(file.path, mimeType: attachment.mimeType);
      }
      return file.path;
    } catch (error) {
      if (_isCurrentAgentSelection(projectId: projectId, agent: agent)) {
        _showSnack('Failed to open file: $error');
      }
      return null;
    } finally {
      if (mounted) {
        setState(() {
          _downloadingAttachmentIds.remove(attachment.fileId);
        });
      }
    }
  }

  Future<void> _confirmAndOpenAttachment(
    CcbAgent agent,
    CcbMessageAttachment attachment,
    String projectId,
  ) async {
    final strings = CcbMobileLocalizations.of(context);
    final confirmed = await showDialog<bool>(
      context: context,
      builder: (context) {
        return AlertDialog(
          title: Text(strings.openAttachment),
          content: Text(strings.openAttachmentQuestion(attachment.fileName)),
          actions: [
            TextButton(
              key: const ValueKey('open-attachment-cancel-action'),
              onPressed: () => Navigator.of(context).pop(false),
              child: Text(strings.cancel),
            ),
            FilledButton(
              key: const ValueKey('open-attachment-confirm-action'),
              onPressed: () => Navigator.of(context).pop(true),
              child: Text(strings.open),
            ),
          ],
        );
      },
    );
    if (confirmed != true ||
        !_isCurrentAgentSelection(projectId: projectId, agent: agent)) {
      return;
    }
    await _downloadAttachment(
      agent,
      attachment,
      projectId: projectId,
      openAfterDownload: true,
    );
  }

  Future<void> _openAttachmentFile(String path, {String? mimeType}) async {
    await OpenFilex.open(path, type: mimeType);
  }

  bool _isCurrentAgentSelection({
    required String projectId,
    required CcbAgent agent,
  }) {
    return mounted &&
        widget.view.project.id == projectId &&
        widget.agent?.name == agent.name;
  }

  void _showSnack(String message) {
    if (!mounted) {
      return;
    }
    ScaffoldMessenger.of(
      context,
    ).showSnackBar(SnackBar(content: Text(message)));
  }

  String _appendRecentPaneOutput({
    required String agentName,
    required String output,
  }) {
    final previous = _recentPaneOutputText[agentName];
    final combined =
        previous == null || previous.isEmpty
            ? output
            : '$previous${_paneOutputJoiner(previous, output)}$output';
    final start = combined.length > 1000 ? combined.length - 1000 : 0;
    final recent = combined.substring(start);
    _recentPaneOutputText[agentName] = recent;
    return recent;
  }

  Future<void> _retryMessage(CcbConversationItem item) async {
    final agent = widget.view.agentByName(item.agentName);
    await _messageSubmitCoordinator.retry(
      item: item,
      view: widget.view,
      repository: widget.repository,
      terminalTransport: widget.terminalTransport,
      usePaneInput: widget.usePaneInputForMessages,
      refreshView: widget.onRefreshView,
      onAccepted: () {
        if (agent != null) {
          _localExceptionStatusAgentNames.remove(agent.name);
        }
      },
      onDeliveryUpdated: (replacement) {
        if (agent == null) {
          return;
        }
        _handleMessageDeliveryUpdated(
          agent: agent,
          body: item.body,
          hasAttachments: item.attachments.isNotEmpty,
          replacement: replacement,
        );
      },
    );
  }

  void _deleteFailedMessage(CcbConversationItem item) {
    if (item.state != CcbConversationDeliveryState.failed) {
      return;
    }
    _mutateChatState(() {
      _chatController.removeLocalMessage(item.agentName, item.id);
    });
  }

  void _handlePaneChatEvent(PaneChatEvent event) {
    if (event.kind == PaneChatEventKind.output) {
      if (!mounted) {
        return;
      }
      setState(() {
        final recentOutput = _appendRecentPaneOutput(
          agentName: event.agentName,
          output: event.body,
        );
        if (_paneOutputHasTerminalException(recentOutput)) {
          _recentPaneOutputText.remove(event.agentName);
          _clearAwaitingPaneResponse(event.agentName);
          _localExceptionStatusAgentNames.add(event.agentName);
        } else {
          _localExceptionStatusAgentNames.remove(event.agentName);
        }
      });
      return;
    }
    final wasAwaiting = _turnSyncTracker.isAwaiting(event.agentName);
    _clearAwaitingPaneResponse(event.agentName);
    final changed = _paneEventCoordinator.apply(event);
    if (wasAwaiting && mounted && !changed) {
      setState(() {});
    }
  }

  void _refreshLatest(String agentName) {
    final agent = widget.agent;
    if (agent == null || agent.name != agentName) {
      return;
    }
    unawaited(_refreshLatestForAgent(agent, refreshViewFirst: true));
  }

  Future<void> _refreshLatestForAgent(
    CcbAgent agent, {
    required bool refreshViewFirst,
  }) async {
    var view = widget.view;
    if (refreshViewFirst) {
      final refreshed = await widget.onRefreshView?.call();
      if (!mounted || widget.agent?.name != agent.name) {
        return;
      }
      if (refreshed != null) {
        view = refreshed;
        _syncLocalExecutionStateFromView(
          view: refreshed,
          agentName: agent.name,
        );
      }
    }
    await _refreshSelectedAgentConversation(agent, viewOverride: view);
  }

  _ExecutionSyncResult _syncLocalExecutionStateFromView({
    required CcbProjectView view,
    required String agentName,
    bool conversationReconciled = false,
  }) {
    final refreshedAgent = view.agentByName(agentName);
    if (refreshedAgent == null) {
      return _ExecutionSyncResult.pending;
    }
    final status = agentExecutionStatus(
      agent: refreshedAgent,
      isAwaitingAgentResponse: false,
    );
    final decision = _turnSyncTracker.observe(
      agentName: agentName,
      sourceState: status.state,
      conversation: _chatController.remoteConversationFor(agentName),
      view: view,
      agent: refreshedAgent,
      conversationReconciled: conversationReconciled,
    );
    if (status.state == 'working') {
      _cancelStartObservationTimeout(agentName);
      _localExceptionStatusAgentNames.remove(agentName);
      return _ExecutionSyncResult.pending;
    }
    if (status.state == 'exception') {
      return _ExecutionSyncResult.pending;
    }
    if (status.state == 'idle' && decision.settled) {
      _localExceptionStatusAgentNames.remove(agentName);
      if (decision.isCompleted &&
          decision.hadPendingTurn &&
          decision.observedSourceWorking) {
        _showSnack(
          CcbMobileLocalizations.of(context).agentCompleted(agentName),
        );
        return _ExecutionSyncResult.completed;
      }
      return _ExecutionSyncResult.settledIdle;
    }
    return _ExecutionSyncResult.pending;
  }

  void _updateAwaitingForSubmission({
    required CcbAgent agent,
    required String body,
    required bool hasAttachments,
  }) {
    if (!agentSubmissionExpectsAssistantReply(
      agent: agent,
      body: body,
      hasAttachments: hasAttachments,
    )) {
      _clearAwaitingPaneResponse(agent.name);
      return;
    }
    _turnSyncTracker.markSubmitted(
      agentName: agent.name,
      conversation: _chatController.remoteConversationFor(agent.name),
    );
    _awaitingTurnGenerations[agent.name] =
        (_awaitingTurnGenerations[agent.name] ?? 0) + 1;
  }

  void _clearAwaitingPaneResponse(String agentName) {
    _cancelStartObservationTimeout(agentName);
    _awaitingTurnGenerations[agentName] =
        (_awaitingTurnGenerations[agentName] ?? 0) + 1;
    _turnSyncTracker.clear(agentName);
  }

  void _handleMessageDeliveryUpdated({
    required CcbAgent agent,
    required String body,
    required bool hasAttachments,
    required CcbConversationItem replacement,
  }) {
    final agentName = agent.name;
    if (replacement.state == CcbConversationDeliveryState.sent) {
      _updateAwaitingForSubmission(
        agent: agent,
        body: body,
        hasAttachments: hasAttachments,
      );
      if (!_turnSyncTracker.isAwaiting(agentName)) {
        return;
      }
      _syncLocalExecutionStateFromView(view: widget.view, agentName: agentName);
      _scheduleIdleReconciliation(agentName);
      _scheduleStartObservationTimeout(
        agentName: agentName,
        localMessageId: replacement.id,
      );
      return;
    }
    _mutateChatState(() {
      _clearAwaitingPaneResponse(agentName);
    });
  }

  void _scheduleStartObservationTimeout({
    required String agentName,
    required String localMessageId,
  }) {
    _cancelStartObservationTimeout(agentName);
    final generation = _awaitingTurnGenerations[agentName] ?? 0;
    _startObservationTimers[agentName] = Timer(
      agentTurnStartObservationTimeout,
      () {
        _startObservationTimers.remove(agentName);
        unawaited(
          _handleStartObservationTimeout(
            agentName: agentName,
            localMessageId: localMessageId,
            generation: generation,
          ),
        );
      },
    );
  }

  Future<void> _handleStartObservationTimeout({
    required String agentName,
    required String localMessageId,
    required int generation,
  }) async {
    if (!mounted ||
        widget.agent?.name != agentName ||
        !_turnSyncTracker.isAwaiting(agentName) ||
        _awaitingTurnGenerations[agentName] != generation ||
        _turnSyncTracker.hasObservedSourceWorking(agentName)) {
      return;
    }
    final agent = widget.agent;
    if (agent == null) {
      return;
    }
    await _refreshLatestForAgent(agent, refreshViewFirst: true);
    if (!mounted ||
        widget.agent?.name != agentName ||
        !_turnSyncTracker.isAwaiting(agentName) ||
        _awaitingTurnGenerations[agentName] != generation ||
        _turnSyncTracker.hasObservedSourceWorking(agentName)) {
      return;
    }
    _mutateChatState(() {
      final localItems = _chatController.localMessagesFor(agentName);
      for (final item in localItems) {
        if (item.id != localMessageId) {
          continue;
        }
        _chatController.replaceLocalMessage(
          agentName,
          localMessageId,
          item.copyWith(state: CcbConversationDeliveryState.unconfirmed),
        );
        break;
      }
      _localExceptionStatusAgentNames.add(agentName);
      _clearAwaitingPaneResponse(agentName);
    });
  }

  void _cancelStartObservationTimeout(String agentName) {
    _startObservationTimers.remove(agentName)?.cancel();
  }

  void _scheduleIdleReconciliation(String agentName) {
    if (!AgentViewWatermark.fromView(widget.view).hasValue) {
      return;
    }
    final generation = _awaitingTurnGenerations[agentName] ?? 0;
    Future<void>.delayed(agentTurnInitialReconciliationDelay, () async {
      if (!mounted ||
          widget.agent?.name != agentName ||
          !_turnSyncTracker.isAwaiting(agentName) ||
          _awaitingTurnGenerations[agentName] != generation) {
        return;
      }
      final agent = widget.agent;
      if (agent == null) {
        return;
      }
      await _refreshLatestForAgent(agent, refreshViewFirst: true);
    });
  }

  bool _isTimelineNearEnd(String agentName) {
    return _uiControllers.isTimelineNearEnd(agentName);
  }

  void _keepLatestVisibleAfterComposerChange(String agentName) {
    if (!_isTimelineNearEnd(agentName)) {
      return;
    }
    _scrollTimelineToEnd(agentName);
  }

  void _handleTimelineUserScrollDirection(
    String agentName,
    ScrollDirection direction,
  ) {
    _uiControllers.cancelTimelineAutoFollow(agentName);
    widget.onUserScrollDirectionChanged?.call(direction);
  }

  Future<void> _loadOlderConversation(String agentName) async {
    if (!_chatController.hasOlderConversation(agentName) ||
        _chatController.isLoadingConversation(agentName)) {
      return;
    }
    final controller = _scrollController(agentName);
    final beforeMax =
        controller.hasClients ? controller.position.maxScrollExtent : null;
    final beforePixels =
        controller.hasClients ? controller.position.pixels : null;
    final changed = await _conversationRefreshCoordinator.loadOlder(
      repository: widget.repository,
      view: widget.view,
      agentName: agentName,
      refreshView: widget.onRefreshView,
    );
    if (!changed || beforeMax == null || beforePixels == null) {
      return;
    }
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted ||
          widget.agent?.name != agentName ||
          !controller.hasClients) {
        return;
      }
      final delta = controller.position.maxScrollExtent - beforeMax;
      final target = (beforePixels + delta).clamp(
        controller.position.minScrollExtent,
        controller.position.maxScrollExtent,
      );
      controller.jumpTo(target);
    });
  }

  void _clearNewMessageFlag(String agentName) {
    if (!_chatController.hasNewMessages(agentName)) {
      return;
    }
    if (!_pendingClearNewMessageAgents.add(agentName)) {
      return;
    }
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _pendingClearNewMessageAgents.remove(agentName);
      if (!mounted ||
          widget.agent?.name != agentName ||
          !_chatController.hasNewMessages(agentName)) {
        return;
      }
      setState(() {
        _chatController.clearNewMessageFlag(agentName);
      });
    });
  }

  void _jumpToLatest(String agentName) {
    setState(() {
      _chatController.clearNewMessageFlag(agentName);
    });
    _scrollTimelineToEnd(agentName);
  }

  void _refreshSelectedAgentLatest() {
    final agentName = widget.agent?.name;
    if (agentName == null) {
      return;
    }
    _refreshLatest(agentName);
  }

  void _scrollTimelineToEnd(String agentName, {int attempt = 0}) {
    _clearNewMessageFlag(agentName);
    _uiControllers.scrollTimelineToEnd(
      agentName,
      isActive: (agentName) => mounted && widget.agent?.name == agentName,
      targetItemId: _latestTimelineItemId(agentName),
      attempt: attempt,
    );
  }

  String? _latestTimelineItemId(String agentName) {
    final selectedAgent = widget.agent;
    if (selectedAgent == null || selectedAgent.name != agentName) {
      return null;
    }
    final model = selectedAgentWorkspaceModel(
      view: widget.view,
      agent: selectedAgent,
      chatController: _chatController,
      isAwaitingAgentResponse: _turnSyncTracker.isAwaiting(selectedAgent.name),
      hasLocalExecutionException: _localExceptionStatusAgentNames.contains(
        selectedAgent.name,
      ),
    );
    return model.timelineItems.isEmpty ? null : model.timelineItems.last.id;
  }

  @override
  Widget build(BuildContext context) {
    final selectedAgent = widget.agent;
    if (selectedAgent == null) {
      return const NoSelectedAgentWorkspaceView();
    }
    final model = selectedAgentWorkspaceModel(
      view: widget.view,
      agent: selectedAgent,
      chatController: _chatController,
      isAwaitingAgentResponse: _turnSyncTracker.isAwaiting(selectedAgent.name),
      hasLocalExecutionException: _localExceptionStatusAgentNames.contains(
        selectedAgent.name,
      ),
    );
    final draftFocusNode = _draftFocusNode(selectedAgent.name);
    _observeDraftFocusNode(selectedAgent.name, draftFocusNode);
    return Column(
      children: [
        Expanded(
          child: SelectedAgentWorkspaceView(
            repository: widget.repository,
            view: widget.view,
            model: model,
            timelineController: _scrollController(selectedAgent.name),
            draftController: _draftController(selectedAgent.name),
            draftFocusNode: draftFocusNode,
            enableComposerCollapse: widget.enableComposerCollapse,
            draftAttachments: _draftAttachments(selectedAgent.name),
            downloadingAttachmentIds: _downloadingAttachmentIds,
            downloadedAttachmentIds: _downloadedAttachmentPaths.keys.toSet(),
            onPickImageAttachment: () {
              _pickAttachments(
                agentName: selectedAgent.name,
                type: FileType.image,
              );
            },
            onPickFileAttachment: () {
              _pickAttachments(
                agentName: selectedAgent.name,
                type: FileType.any,
              );
            },
            onRemoveAttachment: (localId) {
              _removeAttachment(selectedAgent.name, localId);
            },
            onDownloadAttachment: (attachment) {
              final projectId = widget.view.project.id;
              _downloadAttachment(
                selectedAgent,
                attachment,
                projectId: projectId,
                openAfterDownload: false,
              );
            },
            onOpenAttachment: (attachment) {
              final projectId = widget.view.project.id;
              _confirmAndOpenAttachment(selectedAgent, attachment, projectId);
            },
            onRetry: _retryMessage,
            onDeleteFailedMessage: _deleteFailedMessage,
            onToggleExpanded: (itemId) {
              _toggleExpandedItem(selectedAgent.name, itemId);
            },
            onNearEnd: () {
              _clearNewMessageFlag(selectedAgent.name);
            },
            onUserNearEnd: () {
              _clearNewMessageFlag(selectedAgent.name);
            },
            onUserScrollDirectionChanged: (direction) {
              _handleTimelineUserScrollDirection(selectedAgent.name, direction);
            },
            onNearStart: () {
              _loadOlderConversation(selectedAgent.name);
            },
            onJumpToLatest: () {
              _jumpToLatest(selectedAgent.name);
            },
            onCollapseComposer: () {
              _collapseComposer(selectedAgent.name);
            },
            onExpandComposer: () {
              _expandComposer(selectedAgent.name);
            },
            onSend: () {
              _sendMessage(selectedAgent);
            },
            onSendTab: () {
              _sendDraftThenPaneKey(
                selectedAgent,
                bytes: selectedAgentTabKeyBytes,
                label: 'Tab',
              );
            },
            onSendEscape: () {
              _sendPaneKey(
                selectedAgent,
                bytes: selectedAgentEscapeKeyBytes,
                label: 'Esc',
              );
            },
            expandedItemKeyBuilder: _expandedTimelineItemKey,
            sendEnabled:
                widget.sendEnabled && widget.view.namespaceEpoch != null,
            sendDisabledReason: widget.sendDisabledReason,
          ),
        ),
      ],
    );
  }

  void _focusComposer(String agentName) {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (!mounted || widget.agent?.name != agentName) {
        return;
      }
      _draftFocusNode(agentName).requestFocus();
    });
  }
}

String? _mimeTypeForExtension(String? extension) {
  return switch (extension?.toLowerCase()) {
    'jpg' || 'jpeg' => 'image/jpeg',
    'png' => 'image/png',
    'gif' => 'image/gif',
    'webp' => 'image/webp',
    'heic' => 'image/heic',
    'heif' => 'image/heif',
    'bmp' => 'image/bmp',
    'mp4' || 'm4v' => 'video/mp4',
    'mov' => 'video/quicktime',
    'webm' => 'video/webm',
    'mkv' => 'video/x-matroska',
    'avi' => 'video/x-msvideo',
    'pdf' => 'application/pdf',
    'txt' => 'text/plain',
    'md' => 'text/markdown',
    'doc' => 'application/msword',
    'docx' =>
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    _ => null,
  };
}

String? _attachmentExtension({
  required String? pickerExtension,
  required String fileName,
  required String path,
}) {
  if (pickerExtension != null && pickerExtension.isNotEmpty) {
    return pickerExtension.toLowerCase();
  }
  final fileNameExtension = p.extension(fileName);
  if (fileNameExtension.isNotEmpty) {
    return fileNameExtension.substring(1).toLowerCase();
  }
  final pathExtension = p.extension(path);
  if (pathExtension.isNotEmpty) {
    return pathExtension.substring(1).toLowerCase();
  }
  return null;
}

bool _isSupportedAttachment({
  required FileType type,
  required String? extension,
  required String mimeType,
}) {
  if (type == FileType.image) {
    return mimeType.startsWith('image/');
  }
  return _mimeTypeForExtension(extension) != null;
}

bool _paneOutputHasTerminalException(String output) {
  final text = output.trim().toLowerCase();
  if (text.isEmpty || text.contains('esc to interrupt')) {
    return false;
  }
  return text.contains('conversation interrupted') ||
      text.contains('request interrupted') ||
      text.contains('interrupted by user') ||
      text.contains('cancelled') ||
      text.contains('canceled') ||
      text.contains('aborted');
}

String _paneOutputJoiner(String previous, String output) {
  if (previous.isEmpty || output.isEmpty) {
    return '';
  }
  final previousEndsWithWhitespace = RegExp(r'\s$').hasMatch(previous);
  final outputStartsWithWhitespace = RegExp(r'^\s').hasMatch(output);
  return previousEndsWithWhitespace || outputStartsWithWhitespace ? '' : ' ';
}

class _ExecutionSyncResult {
  const _ExecutionSyncResult({
    required this.settled,
    required this.isCompleted,
  });

  static const pending = _ExecutionSyncResult(
    settled: false,
    isCompleted: false,
  );
  static const settledIdle = _ExecutionSyncResult(
    settled: true,
    isCompleted: false,
  );
  static const completed = _ExecutionSyncResult(
    settled: true,
    isCompleted: true,
  );

  final bool settled;
  final bool isCompleted;
}

String _safeFileName(String fileName) {
  final cleaned = fileName.replaceAll(RegExp(r'[\\/:*?"<>|]+'), '_').trim();
  return cleaned.isEmpty ? 'attachment' : cleaned;
}
