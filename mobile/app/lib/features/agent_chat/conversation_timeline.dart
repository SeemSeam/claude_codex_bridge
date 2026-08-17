import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart' show ScrollCacheExtent, ScrollDirection;

import '../../models/ccb_agent.dart';
import '../../models/ccb_content_item.dart';
import '../../models/ccb_conversation_item.dart';
import '../../models/ccb_project_view.dart';
import '../../models/readable_terminal_history.dart';
import '../../repository/mobile_ccb_repository.dart';
import '../../l10n/ccb_mobile_localizations.dart';
import 'content_reader.dart';
import 'conversation_bubble.dart';
import 'readable_terminal_history_panel.dart';
import 'selected_agent_workspace_model.dart';

const double conversationTimelineFollowLatestPadding = 6;
const double conversationTimelineExpandedComposerRevealPadding = 20;
const double conversationTimelineComposerRevealPadding = 32;
const double conversationTimelineExpandedRevealPadding =
    conversationTimelineExpandedComposerRevealPadding;
const double conversationTimelineNearEndThreshold = 120;
const double conversationTimelineKeyboardInsetThreshold = 80;

class ConversationTimeline extends StatelessWidget {
  const ConversationTimeline({
    required this.repository,
    required this.view,
    required this.agent,
    required this.contentItems,
    required this.initialHistory,
    required this.items,
    required this.isLoading,
    required this.controller,
    required this.expandedItemIds,
    required this.downloadingAttachmentIds,
    required this.downloadedAttachmentIds,
    required this.onRetry,
    required this.onDeleteFailedMessage,
    required this.onToggleExpanded,
    required this.onNearEnd,
    required this.onUserNearEnd,
    required this.onNearStart,
    required this.onUserScrollDirectionChanged,
    required this.hasOlderItems,
    required this.onDownloadAttachment,
    required this.onOpenAttachment,
    this.expandedItemKeyBuilder,
    this.bottomRevealPadding = conversationTimelineFollowLatestPadding,
    this.workingItemId,
    super.key,
  });

  final MobileCcbRepository repository;
  final CcbProjectView view;
  final CcbAgent agent;
  final List<CcbContentItem> contentItems;
  final ReadableTerminalHistory? initialHistory;
  final List<CcbConversationItem> items;
  final bool isLoading;
  final ScrollController controller;
  final Set<String> expandedItemIds;
  final Set<String> downloadingAttachmentIds;
  final Set<String> downloadedAttachmentIds;
  final ValueChanged<CcbConversationItem> onRetry;
  final ValueChanged<CcbConversationItem> onDeleteFailedMessage;
  final ValueChanged<String> onToggleExpanded;
  final VoidCallback onNearEnd;
  final VoidCallback onUserNearEnd;
  final VoidCallback onNearStart;
  final ValueChanged<ScrollDirection> onUserScrollDirectionChanged;
  final bool hasOlderItems;
  final ValueChanged<CcbMessageAttachment> onDownloadAttachment;
  final ValueChanged<CcbMessageAttachment> onOpenAttachment;
  final GlobalKey Function(String itemId)? expandedItemKeyBuilder;
  final double bottomRevealPadding;
  final String? workingItemId;

  @override
  Widget build(BuildContext context) {
    final contentById = {for (final item in contentItems) item.id: item};
    final showLoadingIndicator = isLoading && items.isEmpty;
    final loadingOffset = showLoadingIndicator ? 1 : 0;
    return NotificationListener<ScrollNotification>(
      onNotification: (notification) {
        final userDriven = isUserDrivenScrollNotification(notification);
        if (userDriven) {
          final direction = userScrollDirectionForNotification(notification);
          if (direction != ScrollDirection.idle) {
            onUserScrollDirectionChanged(direction);
          }
        }
        if (isScrollMetricsNearEnd(notification.metrics)) {
          onNearEnd();
          if (userDriven) {
            onUserNearEnd();
          }
        }
        if (userDriven &&
            hasOlderItems &&
            isScrollMetricsNearStart(notification.metrics)) {
          onNearStart();
        }
        return false;
      },
      child: LayoutBuilder(
        builder: (context, constraints) {
          final bottomReadPadding =
              (expandedItemIds.isEmpty
                      ? bottomRevealPadding
                      : conversationTimelineExpandedRevealPadding)
                  .clamp(0.0, constraints.maxHeight)
                  .toDouble();
          return ListView.separated(
            key: const ValueKey('agent-chat-timeline'),
            controller: controller,
            primary: false,
            padding: EdgeInsets.only(bottom: bottomReadPadding),
            scrollCacheExtent: const ScrollCacheExtent.pixels(420),
            itemCount: items.length + loadingOffset,
            separatorBuilder: (context, index) => const SizedBox(height: 8),
            itemBuilder: (context, index) {
              if (showLoadingIndicator && index == 0) {
                return const LinearProgressIndicator(
                  key: ValueKey('agent-conversation-loading'),
                );
              }
              final item = items[index - loadingOffset];
              final expanded = expandedItemIds.contains(item.id);
              return KeyedSubtree(
                key: conversationTimelineItemKey(item.id),
                child: _ConversationTimelineItem(
                  key: expanded ? expandedItemKeyBuilder?.call(item.id) : null,
                  item: item,
                  timelineViewportHeight: constraints.maxHeight,
                  timelineScrollController: controller,
                  onUserScrollDirectionChanged: onUserScrollDirectionChanged,
                  content:
                      item.contentId == null
                          ? null
                          : contentById[item.contentId],
                  repository: repository,
                  view: view,
                  agent: agent,
                  initialHistory: initialHistory,
                  expanded: expanded,
                  isWorking: item.id == workingItemId,
                  downloadingAttachmentIds: downloadingAttachmentIds,
                  downloadedAttachmentIds: downloadedAttachmentIds,
                  onRetry: onRetry,
                  onDeleteFailedMessage: onDeleteFailedMessage,
                  onToggleExpanded: onToggleExpanded,
                  onDownloadAttachment: onDownloadAttachment,
                  onOpenAttachment: onOpenAttachment,
                ),
              );
            },
          );
        },
      ),
    );
  }
}

Key conversationTimelineItemKey(String itemId) =>
    ValueKey('conversation-timeline-item-$itemId');

class _ConversationTimelineItem extends StatelessWidget {
  const _ConversationTimelineItem({
    required this.item,
    required this.timelineViewportHeight,
    required this.timelineScrollController,
    required this.onUserScrollDirectionChanged,
    required this.content,
    required this.repository,
    required this.view,
    required this.agent,
    required this.initialHistory,
    required this.expanded,
    required this.isWorking,
    required this.downloadingAttachmentIds,
    required this.downloadedAttachmentIds,
    required this.onRetry,
    required this.onDeleteFailedMessage,
    required this.onToggleExpanded,
    required this.onDownloadAttachment,
    required this.onOpenAttachment,
    super.key,
  });

  final CcbConversationItem item;
  final double timelineViewportHeight;
  final ScrollController timelineScrollController;
  final ValueChanged<ScrollDirection> onUserScrollDirectionChanged;
  final CcbContentItem? content;
  final MobileCcbRepository repository;
  final CcbProjectView view;
  final CcbAgent agent;
  final ReadableTerminalHistory? initialHistory;
  final bool expanded;
  final bool isWorking;
  final Set<String> downloadingAttachmentIds;
  final Set<String> downloadedAttachmentIds;
  final ValueChanged<CcbConversationItem> onRetry;
  final ValueChanged<CcbConversationItem> onDeleteFailedMessage;
  final ValueChanged<String> onToggleExpanded;
  final ValueChanged<CcbMessageAttachment> onDownloadAttachment;
  final ValueChanged<CcbMessageAttachment> onOpenAttachment;

  @override
  Widget build(BuildContext context) {
    if (isProviderSessionBoundaryItem(item)) {
      return _ProviderSessionBoundary(itemId: item.id);
    }
    if (item.kind == CcbConversationItemKind.terminalHistoryBlock) {
      return ConversationBubble(
        item: item,
        expanded: expanded,
        timelineViewportHeight: timelineViewportHeight,
        timelineScrollController: timelineScrollController,
        onUserScrollDirectionChanged: onUserScrollDirectionChanged,
        isWorking: isWorking,
        onToggleExpanded: onToggleExpanded,
        child: AgentReadableHistoryLoader(
          repository: repository,
          projectId: view.project.id,
          agentName: agent.name,
          namespaceEpoch: view.namespaceEpoch,
          initialHistory: initialHistory,
        ),
        onDownloadAttachment: onDownloadAttachment,
        onOpenAttachment: onOpenAttachment,
        downloadingAttachmentIds: downloadingAttachmentIds,
        downloadedAttachmentIds: downloadedAttachmentIds,
      );
    }
    final contentItem = content;
    if (contentItem != null) {
      return ConversationBubble(
        item: item,
        expanded: expanded,
        timelineViewportHeight: timelineViewportHeight,
        timelineScrollController: timelineScrollController,
        onUserScrollDirectionChanged: onUserScrollDirectionChanged,
        isWorking: isWorking,
        onToggleExpanded: onToggleExpanded,
        child: AgentContentReader(items: [contentItem]),
        onDownloadAttachment: onDownloadAttachment,
        onOpenAttachment: onOpenAttachment,
        downloadingAttachmentIds: downloadingAttachmentIds,
        downloadedAttachmentIds: downloadedAttachmentIds,
      );
    }
    return ConversationBubble(
      item: item,
      expanded: expanded,
      timelineViewportHeight: timelineViewportHeight,
      timelineScrollController: timelineScrollController,
      onUserScrollDirectionChanged: onUserScrollDirectionChanged,
      isWorking: isWorking,
      onToggleExpanded: onToggleExpanded,
      onRetry:
          item.state == CcbConversationDeliveryState.failed
              ? () {
                onRetry(item);
              }
              : null,
      onDelete:
          item.state == CcbConversationDeliveryState.failed
              ? () {
                onDeleteFailedMessage(item);
              }
              : null,
      onDownloadAttachment: onDownloadAttachment,
      onOpenAttachment: onOpenAttachment,
      downloadingAttachmentIds: downloadingAttachmentIds,
      downloadedAttachmentIds: downloadedAttachmentIds,
    );
  }
}

class _ProviderSessionBoundary extends StatelessWidget {
  const _ProviderSessionBoundary({required this.itemId});

  final String itemId;

  @override
  Widget build(BuildContext context) {
    final color = Theme.of(context).colorScheme.outlineVariant;
    final label = CcbMobileLocalizations.of(context).newContext;
    return Semantics(
      key: ValueKey('provider-session-boundary-$itemId'),
      label: label,
      child: Row(
        children: [
          Expanded(child: Divider(color: color)),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 10),
            child: Text(
              label,
              style: Theme.of(context).textTheme.labelSmall?.copyWith(
                color: Theme.of(context).colorScheme.onSurfaceVariant,
              ),
            ),
          ),
          Expanded(child: Divider(color: color)),
        ],
      ),
    );
  }
}

bool isUserDrivenScrollNotification(ScrollNotification notification) {
  return (notification is ScrollUpdateNotification &&
          notification.dragDetails != null) ||
      (notification is OverscrollNotification &&
          notification.dragDetails != null);
}

ScrollDirection userScrollDirectionForNotification(
  ScrollNotification notification,
) {
  if (notification is ScrollUpdateNotification) {
    final delta = notification.dragDetails?.delta.dy;
    if (delta == null) {
      return ScrollDirection.idle;
    }
    if (delta < 0) {
      return ScrollDirection.reverse;
    }
    if (delta > 0) {
      return ScrollDirection.forward;
    }
  }
  if (notification is OverscrollNotification &&
      notification.dragDetails != null) {
    if (notification.overscroll > 0) {
      return ScrollDirection.reverse;
    }
    if (notification.overscroll < 0) {
      return ScrollDirection.forward;
    }
  }
  return ScrollDirection.idle;
}

bool isScrollMetricsNearEnd(ScrollMetrics metrics) {
  return metrics.maxScrollExtent - metrics.pixels <=
      conversationTimelineNearEndThreshold;
}

bool isScrollMetricsNearStart(ScrollMetrics metrics) {
  return metrics.pixels <= 72;
}
