import 'dart:async';

import 'package:flutter/gestures.dart';
import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart' show ScrollDirection;

import '../../app/chat_background.dart';
import '../../l10n/ccb_mobile_localizations.dart';
import '../../models/ccb_conversation_item.dart';
import '../../widgets/working_attention_beat.dart';
import 'conversation_item_presentation.dart';

const double _minLimitedConversationBodyHeight = 220;
const double _conversationBodyViewportReserveHeight = 88;
const double _conversationBodyInitialScrollOffset = 1000000000;

class ConversationBubble extends StatelessWidget {
  const ConversationBubble({
    required this.item,
    required this.expanded,
    required this.onToggleExpanded,
    this.child,
    this.onRetry,
    this.onDelete,
    this.onDownloadAttachment,
    this.onOpenAttachment,
    this.downloadingAttachmentIds = const {},
    this.downloadedAttachmentIds = const {},
    this.timelineViewportHeight,
    this.timelineScrollController,
    this.onUserScrollDirectionChanged,
    this.isWorking = false,
    super.key,
  });

  final CcbConversationItem item;
  final bool expanded;
  final ValueChanged<String> onToggleExpanded;
  final Widget? child;
  final VoidCallback? onRetry;
  final VoidCallback? onDelete;
  final ValueChanged<CcbMessageAttachment>? onDownloadAttachment;
  final ValueChanged<CcbMessageAttachment>? onOpenAttachment;
  final Set<String> downloadingAttachmentIds;
  final Set<String> downloadedAttachmentIds;
  final double? timelineViewportHeight;
  final ScrollController? timelineScrollController;
  final ValueChanged<ScrollDirection>? onUserScrollDirectionChanged;
  final bool isWorking;

  void _toggleExpanded() {
    onToggleExpanded(item.id);
  }

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;
    final strings = CcbMobileLocalizations.of(context);
    final isUser = item.kind == CcbConversationItemKind.userMessage;
    final collapsible = conversationShouldCollapse(
      item,
      hasCustomChild: child != null,
    );
    final sourceLabel = visibleConversationSourceLabel(item);
    final showWorking =
        isWorking && item.state != CcbConversationDeliveryState.failed;
    final timestampLabel = conversationTimestampLabel(
      context,
      item,
      includeDuration: MediaQuery.sizeOf(context).width >= 360 && !showWorking,
    );
    final body =
        child ??
        ConversationBody(
          item: item,
          onOpenArtifactActions: (fileId) {
            final attachment =
                item.attachments.where((a) => a.fileId == fileId).firstOrNull;
            if (attachment == null) {
              return;
            }
            _showConversationAttachmentActions(
              context,
              attachment: attachment,
              onDownload:
                  onDownloadAttachment == null
                      ? null
                      : () => onDownloadAttachment!(attachment),
              onOpen:
                  onOpenAttachment == null
                      ? null
                      : () => onOpenAttachment!(attachment),
            );
          },
        );
    final bubbleColor =
        isUser ? colorScheme.primaryContainer : colorScheme.surfaceContainerLow;
    final borderColor = switch (item.state) {
      CcbConversationDeliveryState.failed => colorScheme.error,
      CcbConversationDeliveryState.unconfirmed => colorScheme.tertiary,
      _ when showWorking => colorScheme.primary,
      _ => colorScheme.outlineVariant,
    };
    final borderWidth = showWorking ? 2.4 : 1.0;
    final visibleState =
        item.state == CcbConversationDeliveryState.sent ? null : item.state;
    final metadataColor =
        isUser
            ? colorScheme.onPrimaryContainer.withValues(alpha: 0.72)
            : colorScheme.onSurfaceVariant;
    final attachmentList =
        item.attachments.isEmpty
            ? null
            : ConversationAttachmentList(
              item: item,
              onDownloadAttachment: onDownloadAttachment,
              onOpenAttachment: onOpenAttachment,
              downloadingAttachmentIds: downloadingAttachmentIds,
              downloadedAttachmentIds: downloadedAttachmentIds,
            );
    final bodyWithAttachments =
        attachmentList == null
            ? body
            : Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [body, const SizedBox(height: 6), attachmentList],
            );
    return Align(
      alignment: isUser ? Alignment.centerRight : Alignment.centerLeft,
      child: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 720),
        child: Semantics(
          container: true,
          hint: showWorking ? strings.executionStatus('Working') : null,
          child: _ConversationBubbleSurface(
            itemId: item.id,
            isWorking: showWorking,
            color: bubbleColor,
            borderColor: borderColor,
            borderWidth: borderWidth,
            child: Padding(
              padding: const EdgeInsets.all(10),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Material(
                    color: Colors.transparent,
                    child: InkWell(
                      onTap: collapsible ? _toggleExpanded : null,
                      child: Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Icon(conversationIcon(item.kind), size: 16),
                          const SizedBox(width: 6),
                          Expanded(
                            child: Row(
                              mainAxisSize: MainAxisSize.min,
                              children: [
                                Flexible(
                                  child: Text(
                                    conversationDisplayTitle(item),
                                    style:
                                        Theme.of(context).textTheme.titleSmall,
                                    maxLines: 1,
                                    overflow: TextOverflow.ellipsis,
                                  ),
                                ),
                                if (timestampLabel != null) ...[
                                  const SizedBox(width: 6),
                                  Text(
                                    timestampLabel,
                                    key: ValueKey(
                                      'conversation-timestamp-${item.id}',
                                    ),
                                    style: Theme.of(context).textTheme.bodySmall
                                        ?.copyWith(color: metadataColor),
                                    maxLines: 1,
                                    softWrap: false,
                                    overflow: TextOverflow.fade,
                                  ),
                                ],
                                if (showWorking) ...[
                                  const SizedBox(width: 8),
                                  _ConversationWorkingStatus(
                                    key: ValueKey(
                                      'conversation-working-${item.id}',
                                    ),
                                    startedAt: item.startedAt ?? item.sentAt,
                                  ),
                                ],
                              ],
                            ),
                          ),
                          if (visibleState != null)
                            ConversationStateChip(
                              key: ValueKey('conversation-state-${item.id}'),
                              state: visibleState,
                            ),
                          if (collapsible)
                            IconButton(
                              key: ValueKey('conversation-expand-${item.id}'),
                              visualDensity: VisualDensity.compact,
                              padding: EdgeInsets.zero,
                              constraints: const BoxConstraints.tightFor(
                                width: 32,
                                height: 32,
                              ),
                              tooltip:
                                  expanded
                                      ? strings.collapseMessage
                                      : strings.expandMessage,
                              onPressed: _toggleExpanded,
                              icon: Icon(
                                expanded
                                    ? Icons.expand_less
                                    : Icons.expand_more,
                              ),
                            ),
                        ],
                      ),
                    ),
                  ),
                  if (sourceLabel != null) ...[
                    const SizedBox(height: 1),
                    Text(
                      sourceLabel,
                      style: Theme.of(context).textTheme.bodySmall,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ],
                  const SizedBox(height: 6),
                  if (collapsible && !expanded)
                    Material(
                      color: Colors.transparent,
                      child: InkWell(
                        onTap: _toggleExpanded,
                        child: ConversationPreview(item: item),
                      ),
                    )
                  else
                    ConversationBodyViewport(
                      item: item,
                      hasCustomChild: child != null,
                      timelineViewportHeight: timelineViewportHeight,
                      timelineScrollController: timelineScrollController,
                      onUserScrollDirectionChanged:
                          onUserScrollDirectionChanged,
                      child: bodyWithAttachments,
                    ),
                  if (onRetry != null || onDelete != null) ...[
                    const SizedBox(height: 6),
                    Align(
                      alignment: Alignment.centerRight,
                      child: Wrap(
                        alignment: WrapAlignment.end,
                        spacing: 4,
                        runSpacing: 4,
                        children: [
                          if (onDelete != null)
                            TextButton.icon(
                              key: ValueKey('delete-message-${item.id}'),
                              onPressed: onDelete,
                              icon: const Icon(Icons.delete_outline),
                              label: Text(strings.deleteMessage),
                            ),
                          if (onRetry != null)
                            TextButton.icon(
                              key: ValueKey('retry-message-${item.id}'),
                              onPressed: onRetry,
                              icon: const Icon(Icons.refresh),
                              label: Text(strings.retry),
                            ),
                        ],
                      ),
                    ),
                  ],
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}

class _ConversationBubbleSurface extends StatefulWidget {
  const _ConversationBubbleSurface({
    required this.itemId,
    required this.isWorking,
    required this.color,
    required this.borderColor,
    required this.borderWidth,
    required this.child,
  });

  final String itemId;
  final bool isWorking;
  final Color color;
  final Color borderColor;
  final double borderWidth;
  final Widget child;

  @override
  State<_ConversationBubbleSurface> createState() =>
      _ConversationBubbleSurfaceState();
}

class _ConversationBubbleSurfaceState
    extends State<_ConversationBubbleSurface> {
  @override
  Widget build(BuildContext context) {
    if (!widget.isWorking) {
      return _buildMaterial(context);
    }
    final accent = conversationWorkingBubbleAccent(
      Theme.of(context).colorScheme,
    );
    return Stack(
      clipBehavior: Clip.hardEdge,
      children: [
        _buildMaterial(context),
        Positioned(
          right: 5,
          top: 7,
          bottom: 7,
          child: IgnorePointer(
            child: WorkingAttentionBeat(
              key: ValueKey('conversation-working-beat-${widget.itemId}'),
              child: DecoratedBox(
                decoration: BoxDecoration(
                  color: accent,
                  borderRadius: BorderRadius.circular(2),
                ),
                child: const SizedBox(width: 3),
              ),
            ),
          ),
        ),
      ],
    );
  }

  Widget _buildMaterial(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;
    final surfaceColor = ccbWorkspaceSurfaceColor(context, widget.color);
    final borderSide =
        widget.isWorking
            ? conversationWorkingBubbleBorderSide(colorScheme)
            : BorderSide(color: widget.borderColor, width: widget.borderWidth);
    return Material(
      key: ValueKey('conversation-item-${widget.itemId}'),
      color: surfaceColor,
      clipBehavior: Clip.antiAlias,
      shape: RoundedRectangleBorder(
        side: borderSide,
        borderRadius: BorderRadius.circular(8),
      ),
      child: widget.child,
    );
  }
}

@visibleForTesting
Color conversationWorkingBubbleAccent(ColorScheme colorScheme) {
  return colorScheme.brightness == Brightness.dark
      ? const Color(0xFFFFC857)
      : const Color(0xFFDB6E00);
}

@visibleForTesting
BorderSide conversationWorkingBubbleBorderSide(ColorScheme colorScheme) {
  return BorderSide(
    color: conversationWorkingBubbleAccent(colorScheme),
    width: 2.4,
  );
}

class _ConversationWorkingStatus extends StatefulWidget {
  const _ConversationWorkingStatus({required this.startedAt, super.key});

  final DateTime? startedAt;

  @override
  State<_ConversationWorkingStatus> createState() =>
      _ConversationWorkingStatusState();
}

class _ConversationWorkingStatusState
    extends State<_ConversationWorkingStatus> {
  Timer? _timer;

  @override
  void didChangeDependencies() {
    super.didChangeDependencies();
    _syncTimer();
  }

  @override
  void didUpdateWidget(_ConversationWorkingStatus oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.startedAt != widget.startedAt) {
      _syncTimer();
    }
  }

  @override
  void dispose() {
    _timer?.cancel();
    super.dispose();
  }

  void _syncTimer() {
    _timer?.cancel();
    _timer = null;
    if (widget.startedAt == null || _workingElapsedTimerDisabled(context)) {
      return;
    }
    _timer = Timer.periodic(const Duration(seconds: 1), (_) {
      if (mounted) {
        setState(() {});
      }
    });
  }

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;
    final label = CcbMobileLocalizations.of(context).executionStatus('Working');
    final elapsed = _workingElapsedLabel(widget.startedAt);
    final text = elapsed == null ? label : '$label · $elapsed';
    return Tooltip(
      message: text,
      child: Semantics(
        label: text,
        child: Row(
          key: const ValueKey('conversation-working-status-label'),
          mainAxisSize: MainAxisSize.min,
          children: [
            Icon(Icons.pending_rounded, size: 14, color: colorScheme.primary),
            const SizedBox(width: 3),
            Text(
              text,
              key: const ValueKey('conversation-working-status-text'),
              style: Theme.of(context).textTheme.bodySmall?.copyWith(
                color: colorScheme.primary,
                fontWeight: FontWeight.w700,
              ),
              maxLines: 1,
              softWrap: false,
              overflow: TextOverflow.fade,
            ),
          ],
        ),
      ),
    );
  }
}

bool _workingElapsedTimerDisabled(BuildContext context) {
  final mediaQuery = MediaQuery.maybeOf(context);
  final isWidgetTest = WidgetsBinding.instance.runtimeType.toString().contains(
    'Test',
  );
  return isWidgetTest || (mediaQuery?.disableAnimations ?? false);
}

String? _workingElapsedLabel(DateTime? startedAt) {
  if (startedAt == null) {
    return null;
  }
  final elapsed = DateTime.now().difference(startedAt);
  if (elapsed.isNegative) {
    return '00:00';
  }
  final totalSeconds = elapsed.inSeconds;
  final hours = totalSeconds ~/ 3600;
  final minutes = (totalSeconds % 3600) ~/ 60;
  final seconds = totalSeconds % 60;
  if (hours > 0) {
    return '$hours:${minutes.toString().padLeft(2, '0')}:'
        '${seconds.toString().padLeft(2, '0')}';
  }
  return '${minutes.toString().padLeft(2, '0')}:'
      '${seconds.toString().padLeft(2, '0')}';
}

@visibleForTesting
double conversationBodyViewportMaxHeight(
  Size screenSize, {
  double? timelineViewportHeight,
}) {
  final baseHeight =
      timelineViewportHeight != null &&
              timelineViewportHeight.isFinite &&
              timelineViewportHeight > 0
          ? timelineViewportHeight
          : screenSize.height;
  if (baseHeight <= _minLimitedConversationBodyHeight) {
    return baseHeight;
  }
  return (baseHeight - _conversationBodyViewportReserveHeight)
      .clamp(_minLimitedConversationBodyHeight, baseHeight)
      .toDouble();
}

@visibleForTesting
bool conversationBodyNeedsViewportLimit(
  CcbConversationItem item, {
  required bool hasCustomChild,
}) {
  if (hasCustomChild) {
    return true;
  }
  return conversationShouldCollapse(item, hasCustomChild: hasCustomChild);
}

class ConversationBodyViewport extends StatefulWidget {
  const ConversationBodyViewport({
    required this.item,
    required this.child,
    required this.hasCustomChild,
    this.timelineViewportHeight,
    this.timelineScrollController,
    this.onUserScrollDirectionChanged,
    super.key,
  });

  final CcbConversationItem item;
  final Widget child;
  final bool hasCustomChild;
  final double? timelineViewportHeight;
  final ScrollController? timelineScrollController;
  final ValueChanged<ScrollDirection>? onUserScrollDirectionChanged;

  @override
  State<ConversationBodyViewport> createState() =>
      _ConversationBodyViewportState();
}

class _ConversationBodyViewportState extends State<ConversationBodyViewport> {
  late final ScrollController _scrollController = ScrollController(
    initialScrollOffset: _conversationBodyInitialScrollOffset,
  );
  VelocityTracker? _velocityTracker;
  int? _activePointer;
  bool _didHandoffToTimeline = false;

  void _handlePointerDown(PointerDownEvent event) {
    if (_activePointer != null) {
      return;
    }
    _activePointer = event.pointer;
    _velocityTracker = VelocityTracker.withKind(event.kind)
      ..addPosition(event.timeStamp, event.position);
    _didHandoffToTimeline = false;
  }

  void _handlePointerMove(PointerMoveEvent event) {
    if (_activePointer != event.pointer) {
      return;
    }
    _velocityTracker?.addPosition(event.timeStamp, event.position);
  }

  void _handlePointerUp(PointerUpEvent event) {
    if (_activePointer != event.pointer) {
      return;
    }
    final tracker =
        _velocityTracker?..addPosition(event.timeStamp, event.position);
    if (_didHandoffToTimeline && tracker != null) {
      continueConversationBodyBoundaryFling(
        tracker.getVelocity().pixelsPerSecond.dy,
        widget.timelineScrollController,
      );
    }
    _resetPointerTracking();
  }

  void _handlePointerCancel(PointerCancelEvent event) {
    if (_activePointer == event.pointer) {
      _resetPointerTracking();
    }
  }

  void _resetPointerTracking() {
    _activePointer = null;
    _velocityTracker = null;
    _didHandoffToTimeline = false;
  }

  @override
  void dispose() {
    _scrollController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    if (!conversationBodyNeedsViewportLimit(
      widget.item,
      hasCustomChild: widget.hasCustomChild,
    )) {
      return widget.child;
    }
    final maxHeight = conversationBodyViewportMaxHeight(
      MediaQuery.sizeOf(context),
      timelineViewportHeight: widget.timelineViewportHeight,
    );
    return SizedBox(
      key: ValueKey('conversation-body-viewport-${widget.item.id}'),
      height: maxHeight,
      child: Listener(
        onPointerDown: _handlePointerDown,
        onPointerMove: _handlePointerMove,
        onPointerUp: _handlePointerUp,
        onPointerCancel: _handlePointerCancel,
        child: NotificationListener<ScrollNotification>(
          onNotification: (notification) {
            if (notification is ScrollStartNotification &&
                notification.dragDetails != null) {
              stopConversationTimelineActivity(widget.timelineScrollController);
            }
            final direction = conversationBodyUserScrollDirection(notification);
            if (direction != ScrollDirection.idle) {
              widget.onUserScrollDirectionChanged?.call(direction);
            }
            _didHandoffToTimeline =
                handoffConversationBodyBoundaryOverscroll(
                  notification,
                  widget.timelineScrollController,
                ) ||
                _didHandoffToTimeline;
            return true;
          },
          child: Scrollbar(
            controller: _scrollController,
            thumbVisibility: true,
            child: SingleChildScrollView(
              controller: _scrollController,
              primary: false,
              padding: EdgeInsets.zero,
              child: widget.child,
            ),
          ),
        ),
      ),
    );
  }
}

@visibleForTesting
bool stopConversationTimelineActivity(
  ScrollController? timelineScrollController,
) {
  if (timelineScrollController == null ||
      !timelineScrollController.hasClients ||
      !timelineScrollController.position.isScrollingNotifier.value) {
    return false;
  }
  timelineScrollController.jumpTo(timelineScrollController.position.pixels);
  return true;
}

@visibleForTesting
ScrollDirection conversationBodyUserScrollDirection(
  ScrollNotification notification,
) {
  if (notification is ScrollUpdateNotification &&
      notification.dragDetails != null) {
    final delta = notification.dragDetails!.delta.dy;
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

@visibleForTesting
bool handoffConversationBodyBoundaryOverscroll(
  ScrollNotification notification,
  ScrollController? timelineScrollController,
) {
  if (notification is! OverscrollNotification ||
      notification.dragDetails == null ||
      timelineScrollController == null ||
      !timelineScrollController.hasClients ||
      notification.overscroll == 0) {
    return false;
  }
  final position = timelineScrollController.position;
  final target = (position.pixels + notification.overscroll).clamp(
    position.minScrollExtent,
    position.maxScrollExtent,
  );
  if (target == position.pixels) {
    return false;
  }
  position.jumpTo(target.toDouble());
  return true;
}

@visibleForTesting
bool continueConversationBodyBoundaryFling(
  double pointerVelocity,
  ScrollController? timelineScrollController,
) {
  if (pointerVelocity == 0 ||
      timelineScrollController == null ||
      !timelineScrollController.hasClients) {
    return false;
  }
  final position = timelineScrollController.position;
  final scrollVelocity = -pointerVelocity;
  if ((scrollVelocity < 0 && position.pixels <= position.minScrollExtent) ||
      (scrollVelocity > 0 && position.pixels >= position.maxScrollExtent)) {
    return false;
  }
  final target = (position.pixels + (scrollVelocity * 0.22)).clamp(
    position.minScrollExtent,
    position.maxScrollExtent,
  );
  final durationMs = (pointerVelocity.abs() / 5).clamp(140, 360).round();
  unawaited(
    timelineScrollController.animateTo(
      target.toDouble(),
      duration: Duration(milliseconds: durationMs),
      curve: Curves.decelerate,
    ),
  );
  return true;
}

class ConversationAttachmentList extends StatelessWidget {
  const ConversationAttachmentList({
    required this.item,
    required this.onDownloadAttachment,
    required this.onOpenAttachment,
    required this.downloadingAttachmentIds,
    required this.downloadedAttachmentIds,
    super.key,
  });

  final CcbConversationItem item;
  final ValueChanged<CcbMessageAttachment>? onDownloadAttachment;
  final ValueChanged<CcbMessageAttachment>? onOpenAttachment;
  final Set<String> downloadingAttachmentIds;
  final Set<String> downloadedAttachmentIds;

  @override
  Widget build(BuildContext context) {
    return Wrap(
      key: ValueKey('conversation-attachment-list-${item.id}'),
      spacing: 8,
      runSpacing: 4,
      children: [
        for (final attachment in item.attachments)
          ConversationAttachmentChip(
            attachment: _withDownloadState(attachment),
            onDownload:
                onDownloadAttachment == null
                    ? null
                    : () => onDownloadAttachment!(attachment),
            onOpen:
                onOpenAttachment == null
                    ? null
                    : () => onOpenAttachment!(attachment),
          ),
      ],
    );
  }

  CcbMessageAttachment _withDownloadState(CcbMessageAttachment attachment) {
    if (downloadingAttachmentIds.contains(attachment.fileId)) {
      return attachment.copyWith(state: CcbMessageAttachmentState.uploading);
    }
    if (downloadedAttachmentIds.contains(attachment.fileId)) {
      return attachment.copyWith(state: CcbMessageAttachmentState.downloaded);
    }
    return attachment;
  }
}

class ConversationAttachmentChip extends StatelessWidget {
  const ConversationAttachmentChip({
    required this.attachment,
    required this.onDownload,
    required this.onOpen,
    super.key,
  });

  final CcbMessageAttachment attachment;
  final VoidCallback? onDownload;
  final VoidCallback? onOpen;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final strings = CcbMobileLocalizations.of(context);
    final failed = attachment.state == CcbMessageAttachmentState.failed;
    final busy =
        attachment.state == CcbMessageAttachmentState.queued ||
        attachment.state == CcbMessageAttachmentState.uploading ||
        attachment.state == CcbMessageAttachmentState.processing;
    final downloaded = attachment.state == CcbMessageAttachmentState.downloaded;
    final label = StringBuffer(attachment.fileName);
    if (attachment.sizeBytes > 0) {
      label.write(' (${_formatBytes(attachment.sizeBytes)})');
    }
    final errorMessage = attachment.errorMessage;
    if (failed && errorMessage != null) {
      label.write(' - $errorMessage');
    }
    return Tooltip(
      message:
          failed
              ? errorMessage ?? ''
              : downloaded
              ? strings.openAttachment
              : strings.downloadAttachment,
      child: InkWell(
        key: ValueKey('conversation-attachment-chip-${attachment.fileId}'),
        borderRadius: BorderRadius.circular(16),
        onTap:
            busy || (onDownload == null && onOpen == null)
                ? null
                : () => _showAttachmentActions(context),
        onLongPress:
            busy || (onDownload == null && onOpen == null)
                ? null
                : () => _showAttachmentActions(context),
        child: Chip(
          avatar:
              busy
                  ? SizedBox.square(
                    key: ValueKey(
                      'agent-attachment-progress-${attachment.fileId}',
                    ),
                    dimension: 16,
                    child: const CircularProgressIndicator(strokeWidth: 2),
                  )
                  : Icon(
                    key: ValueKey(
                      downloaded
                          ? 'conversation-attachment-open-${attachment.fileId}'
                          : 'conversation-attachment-download-${attachment.fileId}',
                    ),
                    failed
                        ? Icons.error_outline
                        : downloaded
                        ? Icons.folder_open
                        : attachment.isImage
                        ? Icons.image_outlined
                        : Icons.description_outlined,
                    color: failed ? theme.colorScheme.error : null,
                    size: 16,
                  ),
          label: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 220),
            child: Text(label.toString(), overflow: TextOverflow.ellipsis),
          ),
        ),
      ),
    );
  }

  void _showAttachmentActions(BuildContext context) {
    _showConversationAttachmentActions(
      context,
      attachment: attachment,
      onDownload: onDownload,
      onOpen: onOpen,
    );
  }
}

void _showConversationAttachmentActions(
  BuildContext context, {
  required CcbMessageAttachment attachment,
  required VoidCallback? onDownload,
  required VoidCallback? onOpen,
}) {
  final strings = CcbMobileLocalizations.of(context);
  final download = onDownload;
  final open = onOpen;
  showModalBottomSheet<void>(
    context: context,
    showDragHandle: true,
    builder: (context) {
      return SafeArea(
        child: Column(
          key: ValueKey('conversation-attachment-actions-${attachment.fileId}'),
          mainAxisSize: MainAxisSize.min,
          children: [
            ListTile(
              key: ValueKey(
                'conversation-attachment-action-download-${attachment.fileId}',
              ),
              leading: const Icon(Icons.download),
              title: Text(strings.downloadAttachment),
              onTap:
                  download == null
                      ? null
                      : () {
                        Navigator.of(context).pop();
                        download();
                      },
            ),
            ListTile(
              key: ValueKey(
                'conversation-attachment-action-open-${attachment.fileId}',
              ),
              leading: const Icon(Icons.open_in_new),
              title: Text(strings.openAttachment),
              onTap:
                  open == null
                      ? null
                      : () {
                        Navigator.of(context).pop();
                        open();
                      },
            ),
            ListTile(
              key: ValueKey(
                'conversation-attachment-action-cancel-${attachment.fileId}',
              ),
              leading: const Icon(Icons.close),
              title: Text(strings.cancel),
              onTap: () => Navigator.of(context).pop(),
            ),
          ],
        ),
      );
    },
  );
}

String _formatBytes(int bytes) {
  if (bytes >= 1024 * 1024) {
    return '${(bytes / (1024 * 1024)).toStringAsFixed(1)} MB';
  }
  if (bytes >= 1024) {
    return '${(bytes / 1024).toStringAsFixed(1)} KB';
  }
  return '$bytes B';
}
