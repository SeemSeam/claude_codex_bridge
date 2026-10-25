import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart' show HitTestBehavior, ScrollDirection;

import '../../l10n/ccb_mobile_localizations.dart';
import '../../models/ccb_conversation_item.dart';
import '../../models/ccb_project_view.dart';
import '../../repository/mobile_ccb_repository.dart';
import 'agent_message_composer.dart';
import 'conversation_timeline.dart';
import 'selected_agent_workspace_model.dart';
import 'omp_runtime_panel.dart';
import 'ui_request_card.dart';

class NoSelectedAgentWorkspaceView extends StatelessWidget {
  const NoSelectedAgentWorkspaceView({super.key});

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;
    final strings = CcbMobileLocalizations.of(context);
    return DecoratedBox(
      key: const ValueKey('selected-agent-workspace'),
      decoration: BoxDecoration(
        border: Border.all(color: colorScheme.outlineVariant),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Padding(
        padding: const EdgeInsets.all(16),
        child: Text(strings.noAgents),
      ),
    );
  }
}

class SelectedAgentWorkspaceView extends StatelessWidget {
  const SelectedAgentWorkspaceView({
    required this.repository,
    required this.view,
    required this.model,
    required this.timelineController,
    required this.draftController,
    required this.draftFocusNode,
    required this.enableComposerCollapse,
    required this.onRetry,
    required this.onToggleExpanded,
    required this.onNearEnd,
    required this.onUserNearEnd,
    required this.onNearStart,
    required this.onUserScrollDirectionChanged,
    required this.onJumpToLatest,
    required this.onCollapseComposer,
    required this.onExpandComposer,
    required this.draftAttachments,
    required this.downloadingAttachmentIds,
    required this.downloadedAttachmentIds,
    required this.onPickImageAttachment,
    required this.onPickFileAttachment,
    required this.onRemoveAttachment,
    required this.onDownloadAttachment,
    required this.onOpenAttachment,
    required this.onDeleteFailedMessage,
    required this.onSend,
    required this.onQueue,
    required this.onSendEscape,
    required this.onUiResponse,
    this.expandedItemKeyBuilder,
    this.sendEnabled = true,
    this.sendDisabledReason,
    super.key,
  });

  final MobileCcbRepository repository;
  final CcbProjectView view;
  final SelectedAgentWorkspaceModel model;
  final ScrollController timelineController;
  final TextEditingController draftController;
  final FocusNode draftFocusNode;
  final bool enableComposerCollapse;
  final ValueChanged<CcbConversationItem> onRetry;
  final ValueChanged<String> onToggleExpanded;
  final VoidCallback onNearEnd;
  final VoidCallback onUserNearEnd;
  final VoidCallback onNearStart;
  final ValueChanged<ScrollDirection> onUserScrollDirectionChanged;
  final VoidCallback onJumpToLatest;
  final VoidCallback onCollapseComposer;
  final VoidCallback onExpandComposer;
  final List<CcbMessageAttachment> draftAttachments;
  final Set<String> downloadingAttachmentIds;
  final Set<String> downloadedAttachmentIds;
  final VoidCallback onPickImageAttachment;
  final VoidCallback onPickFileAttachment;
  final ValueChanged<String> onRemoveAttachment;
  final ValueChanged<CcbMessageAttachment> onDownloadAttachment;
  final ValueChanged<CcbMessageAttachment> onOpenAttachment;
  final ValueChanged<CcbConversationItem> onDeleteFailedMessage;
  final VoidCallback onSend;
  final VoidCallback onQueue;
  final VoidCallback onSendEscape;
  final Future<void> Function(String value) onUiResponse;
  final GlobalKey Function(String itemId)? expandedItemKeyBuilder;
  final bool sendEnabled;
  final String? sendDisabledReason;

  @override
  Widget build(BuildContext context) {
    return Column(
      key: const ValueKey('selected-agent-workspace'),
      children: [
        if (model.ompRuntime != null && model.workingReplyItemId == null)
          OmpRuntimePanel(runtime: model.ompRuntime!),
        Expanded(
          child: ListenableBuilder(
            listenable: draftFocusNode,
            builder: (context, _) {
              final flutterView = View.of(context);
              final keyboardOverlapsView =
                  flutterView.viewInsets.bottom / flutterView.devicePixelRatio >
                  conversationTimelineKeyboardInsetThreshold;
              final composerExpanded =
                  enableComposerCollapse && !model.isComposerCollapsed;
              final bottomRevealPadding =
                  keyboardOverlapsView
                      ? conversationTimelineComposerRevealPadding
                      : composerExpanded
                      ? conversationTimelineExpandedComposerRevealPadding
                      : conversationTimelineFollowLatestPadding;
              return Stack(
                children: [
                  Positioned.fill(
                    child: _ComposerDismissRegion(
                      key: const ValueKey('agent-compose-dismiss-region'),
                      onDismiss: onCollapseComposer,
                      child: ConversationTimeline(
                        key: ValueKey(
                          'agent-chat-timeline-${model.agent.name}',
                        ),
                        repository: repository,
                        view: view,
                        agent: model.agent,
                        contentItems: model.contentItems,
                        initialHistory: null,
                        items: model.timelineItems,
                        isLoading: model.isLoadingConversation,
                        controller: timelineController,
                        expandedItemIds: model.expandedItemIds,
                        bottomRevealPadding: bottomRevealPadding,
                        workingItemId: model.workingReplyItemId,
                        ompRuntime: model.ompRuntime,
                        downloadingAttachmentIds: downloadingAttachmentIds,
                        downloadedAttachmentIds: downloadedAttachmentIds,
                        onRetry: onRetry,
                        onDeleteFailedMessage: onDeleteFailedMessage,
                        onToggleExpanded: onToggleExpanded,
                        onNearEnd: onNearEnd,
                        onUserNearEnd: onUserNearEnd,
                        onNearStart: onNearStart,
                        onUserScrollDirectionChanged:
                            onUserScrollDirectionChanged,
                        hasOlderItems: model.hasOlderConversation,
                        onDownloadAttachment: onDownloadAttachment,
                        onOpenAttachment: onOpenAttachment,
                        expandedItemKeyBuilder: expandedItemKeyBuilder,
                      ),
                    ),
                  ),
                  if (!model.isLoadingConversation &&
                      model.timelineItems.isEmpty)
                    Positioned(
                      top: 24,
                      left: 8,
                      right: 8,
                      child: const IgnorePointer(
                        child: Center(
                          child: Text(
                            'No conversation yet',
                            key: ValueKey('agent-conversation-empty'),
                          ),
                        ),
                      ),
                    ),
                  if (model.hasNewMessages)
                    Positioned(
                      right: 8,
                      bottom: 8,
                      child: _NewMessagesButton(onJumpToLatest: onJumpToLatest),
                    ),
                ],
              );
            },
          ),
        ),
        if (model.pendingUiRequest != null)
          UiRequestCard(
            key: ValueKey('ui-request-${identityHashCode(repository)}-${view.project.id}-${view.namespaceEpoch}-${model.agent.name}-${model.uiRequestScope}-${model.pendingUiRequest!.reqId}'),
            request: model.pendingUiRequest!, onSubmit: onUiResponse,
            stale: !sendEnabled || !model.uiRequestAvailable,
          ),
        const SizedBox(height: 6),
        AgentMessageComposer(
          agentName: model.agent.name,
          showQueueButton: model.agent.provider == 'codex',
          controller: draftController,
          focusNode: draftFocusNode,
          isSending: model.isSending,
          sendEnabled: sendEnabled,
          sendDisabledReason: sendDisabledReason,
          collapsible: enableComposerCollapse,
          collapsed: enableComposerCollapse && model.isComposerCollapsed,
          onCollapse: onCollapseComposer,
          onExpand: onExpandComposer,
          draftAttachments: draftAttachments,
          onPickImage: onPickImageAttachment,
          onPickFile: onPickFileAttachment,
          onRemoveAttachment: onRemoveAttachment,
          onSend: onSend,
          onQueue: onQueue,
          onSendEscape: onSendEscape,
        ),
      ],
    );
  }
}

class _NewMessagesButton extends StatelessWidget {
  const _NewMessagesButton({required this.onJumpToLatest});

  final VoidCallback onJumpToLatest;

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;
    final strings = CcbMobileLocalizations.of(context);
    return Material(
      color: colorScheme.surfaceContainerHigh,
      shape: const StadiumBorder(),
      elevation: 2,
      child: TextButton.icon(
        key: const ValueKey('agent-new-messages-jump'),
        onPressed: onJumpToLatest,
        icon: const Icon(Icons.south, size: 18),
        label: Text(strings.newMessages),
        style: TextButton.styleFrom(
          visualDensity: VisualDensity.compact,
          foregroundColor: colorScheme.primary,
          padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
        ),
      ),
    );
  }
}

class _ComposerDismissRegion extends StatefulWidget {
  const _ComposerDismissRegion({
    required this.onDismiss,
    required this.child,
    super.key,
  });

  final VoidCallback onDismiss;
  final Widget child;

  @override
  State<_ComposerDismissRegion> createState() => _ComposerDismissRegionState();
}

class _ComposerDismissRegionState extends State<_ComposerDismissRegion> {
  static const double _tapSlop = 12;

  int? _pointer;
  Offset? _downPosition;

  void _handlePointerDown(PointerDownEvent event) {
    _pointer ??= event.pointer;
    if (_pointer != event.pointer) {
      return;
    }
    _downPosition = event.position;
  }

  void _handlePointerUp(PointerUpEvent event) {
    if (_pointer != event.pointer) {
      return;
    }
    final downPosition = _downPosition;
    _pointer = null;
    _downPosition = null;
    if (downPosition == null) {
      return;
    }
    if ((event.position - downPosition).distance <= _tapSlop) {
      widget.onDismiss();
    }
  }

  void _handlePointerCancel(PointerCancelEvent event) {
    if (_pointer == event.pointer) {
      _pointer = null;
      _downPosition = null;
    }
  }

  @override
  Widget build(BuildContext context) {
    return Listener(
      behavior: HitTestBehavior.translucent,
      onPointerDown: _handlePointerDown,
      onPointerUp: _handlePointerUp,
      onPointerCancel: _handlePointerCancel,
      child: widget.child,
    );
  }
}
