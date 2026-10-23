import 'package:flutter/material.dart';
import 'package:flutter_markdown_plus/flutter_markdown_plus.dart';

import '../../l10n/ccb_mobile_localizations.dart';
import '../../models/ccb_conversation_item.dart';
import '../../native_session/native_session.dart';
import '../../platform/external_url_opener.dart';
import 'agent_chat_state_helpers.dart';
import 'content_text_styles.dart';

class ConversationPreview extends StatelessWidget {
  const ConversationPreview({required this.item, super.key});

  final CcbConversationItem item;

  @override
  Widget build(BuildContext context) {
    return Text(
      conversationPreviewTextFor(item),
      key: ValueKey('conversation-preview-${item.id}'),
      maxLines: conversationPreviewMaxLines(item),
      overflow: TextOverflow.ellipsis,
      style: Theme.of(context).textTheme.bodyMedium,
    );
  }
}

class ConversationBody extends StatelessWidget {
  const ConversationBody({
    required this.item,
    this.onOpenArtifactActions,
    this.contentLoader,
    super.key,
  });

  final CcbConversationItem item;
  final ValueChanged<String>? onOpenArtifactActions;
  final Future<NativeContentPage> Function(String cursor)? contentLoader;

  @override
  Widget build(BuildContext context) {
    if (item.readCursor != null) {
      final chinese = CcbMobileLocalizations.of(context).isChinese;
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        mainAxisSize: MainAxisSize.min,
        children: [
          _buildText(context, item.body),
          TextButton(
            key: const ValueKey('conversation-read-original'),
            onPressed:
                contentLoader == null
                    ? null
                    : () => showNativeOriginalReader(
                      context,
                      cursor: item.readCursor!,
                      loader: contentLoader!,
                    ),
            child: Text(chinese ? '阅读原文' : 'Read original'),
          ),
        ],
      );
    }
    final turn = item.nativeTurn;
    if (turn != null &&
        (item.kind == CcbConversationItemKind.agentReply ||
            turn.readCursor != null)) {
      return NativeTurnBody(
        turn: turn,
        textBuilder: _buildText,
        contentLoader: contentLoader,
        previewText: item.body,
        labels:
            CcbMobileLocalizations.of(context).isChinese
                ? const NativeTurnLabels(
                  process: '执行过程',
                  reasoning: '思考',
                  toolCall: '工具调用',
                  toolResult: '工具结果',
                  running: '运行中',
                  completed: '已完成',
                  failed: '失败',
                  aborted: '已中止',
                  unknown: '状态未知',
                  pendingTools: '工具执行尚未完成',
                  noAnswer: '本轮尚无正文回复',
                  message: '中间回复',
                  readOriginal: '阅读原文',
                  preview: '仅为预览 — 内容超出内联大小限制',
                  latestWindow: '仅显示近期内容；较早内容或较长段落已省略',
                  previous: '上一页',
                  next: '下一页',
                  close: '关闭',
                  retry: '重试',
                  loadFailed: '无法加载原文，内容可能已变更。请关闭后刷新会话。',
                )
                : const NativeTurnLabels(),
      );
    }
    return _buildText(context, item.body);
  }

  Widget _buildText(BuildContext context, String text) {
    if (shouldRenderConversationMarkdown(item)) {
      return MarkdownBody(
        key: ValueKey('markdown-body-conversation-${item.id}'),
        data: text,
        selectable: true,
        styleSheet: ccbMarkdownStyleSheet(context),
        onTapLink: (text, href, title) {
          if (href != null && href.startsWith('ccb-artifact://')) {
            final fileId = href.replaceFirst('ccb-artifact://', '');
            if (onOpenArtifactActions != null) {
              onOpenArtifactActions!(fileId);
            }
          } else if (isOpenableExternalUrl(href)) {
            confirmAndOpenExternalUrl(context, href!);
          } else {
            showBlockedConversationLink(context, href ?? text);
          }
        },
      );
    }
    return SelectableText(text, key: ValueKey('conversation-body-${item.id}'));
  }
}

class ConversationStateChip extends StatelessWidget {
  const ConversationStateChip({
    required super.key,
    required this.state,
    this.collab = false,
    this.needsReview = false,
  });

  final CcbConversationDeliveryState state;
  final bool collab;
  final bool needsReview;

  @override
  Widget build(BuildContext context) {
    return Chip(
      visualDensity: VisualDensity.compact,
      label: Text(
        needsReview
            ? (CcbMobileLocalizations.of(context).isChinese
                ? '需要复核'
                : 'Needs review')
            : collab && state == CcbConversationDeliveryState.unconfirmed
            ? (CcbMobileLocalizations.of(context).isChinese
                ? '原生准入未确认'
                : 'Admission unconfirmed')
            : conversationStateLabel(state),
      ),
    );
  }
}

bool conversationShouldCollapse(
  CcbConversationItem item, {
  required bool hasCustomChild,
}) {
  if (item.nativeTurn != null &&
      item.kind == CcbConversationItemKind.agentReply) {
    return false;
  }
  if (hasCustomChild) {
    return true;
  }
  if (isTerminalDerivedConversationItem(item)) {
    return true;
  }
  if (item.kind == CcbConversationItemKind.userMessage) {
    return item.body.length > 360 || '\n'.allMatches(item.body).length > 6;
  }
  return item.body.length > 220 || '\n'.allMatches(item.body).length > 4;
}

int conversationPreviewMaxLines(CcbConversationItem item) {
  if (isTerminalInputConversationItem(item)) {
    return 1;
  }
  if (isTerminalDerivedConversationItem(item)) {
    return 2;
  }
  return 3;
}

String conversationPreviewText(String body) {
  final lines = [
    for (final line in body.split('\n'))
      if (line.trim().isNotEmpty) stripPreviewMarkdown(line.trim()),
  ];
  if (lines.isEmpty) {
    return 'No content';
  }
  return lines.take(3).join('\n');
}

String conversationPreviewTextFor(CcbConversationItem item) {
  if (!isTerminalDerivedConversationItem(item)) {
    return conversationPreviewText(item.body);
  }
  final lines = [
    for (final line in item.body.split('\n'))
      if (line.trim().isNotEmpty) line.trim(),
  ];
  if (lines.isEmpty) {
    return 'No content';
  }
  return lines.take(3).join('\n');
}

String stripPreviewMarkdown(String line) {
  return line
      .replaceFirst(RegExp(r'^(#{1,6})\s+'), '')
      .replaceFirst(RegExp(r'^[-*]\s+'), '')
      .replaceFirst(RegExp(r'^\d+\.\s+'), '')
      .replaceFirst(RegExp(r'^>\s+'), '')
      .replaceAll(RegExp(r'[*_`]+'), '');
}

bool shouldRenderConversationMarkdown(CcbConversationItem item) {
  if (isTerminalDerivedConversationItem(item)) {
    return false;
  }
  if (item.format.toLowerCase() == 'markdown') {
    return true;
  }
  return switch (item.kind) {
    CcbConversationItemKind.agentReply ||
    CcbConversationItemKind.callbackRequest ||
    CcbConversationItemKind.commsItem ||
    CcbConversationItemKind.userMessage => true,
    CcbConversationItemKind.statusEvent ||
    CcbConversationItemKind.toolEvent ||
    CcbConversationItemKind.artifactCard ||
    CcbConversationItemKind.terminalHistoryBlock ||
    CcbConversationItemKind.systemNotice => false,
  };
}

String conversationDisplayTitle(CcbConversationItem item) {
  if (item.kind == CcbConversationItemKind.agentReply &&
      !isTerminalDerivedConversationItem(item)) {
    final agentName = item.agentName.trim();
    return agentName.isEmpty ? 'Agent' : agentName;
  }
  return item.title;
}

String? conversationTimestampLabel(
  BuildContext context,
  CcbConversationItem item, {
  bool includeDuration = true,
}) {
  final time = _conversationDisplayTime(item);
  final duration =
      includeDuration ? _conversationExecutionDuration(item) : null;
  if (time == null && duration == null) {
    return null;
  }
  return [
    if (time != null) _formatConversationTime(context, time),
    if (duration != null) _formatConversationDuration(duration),
  ].join(' · ');
}

String? visibleConversationSourceLabel(CcbConversationItem item) {
  final source = item.source?.trim();
  if (source == null || source.isEmpty) {
    return null;
  }
  if (isTerminalDerivedConversationItem(item)) {
    return null;
  }
  return switch (item.kind) {
    CcbConversationItemKind.statusEvent ||
    CcbConversationItemKind.toolEvent ||
    CcbConversationItemKind.artifactCard ||
    CcbConversationItemKind.terminalHistoryBlock ||
    CcbConversationItemKind.systemNotice => source,
    CcbConversationItemKind.userMessage ||
    CcbConversationItemKind.agentReply ||
    CcbConversationItemKind.callbackRequest ||
    CcbConversationItemKind.commsItem => null,
  };
}

DateTime? _conversationDisplayTime(CcbConversationItem item) {
  return switch (item.kind) {
    CcbConversationItemKind.userMessage => item.sentAt,
    CcbConversationItemKind.agentReply => item.sentAt ?? item.completedAt,
    _ => null,
  };
}

Duration? _conversationExecutionDuration(CcbConversationItem item) {
  if (item.kind != CcbConversationItemKind.agentReply) {
    return null;
  }
  final durationMs = item.durationMs;
  if (durationMs != null && durationMs >= 0) {
    return Duration(milliseconds: durationMs);
  }
  final startedAt = item.startedAt;
  final completedAt = item.completedAt;
  if (startedAt == null || completedAt == null) {
    return null;
  }
  final duration = completedAt.difference(startedAt);
  return duration.isNegative ? null : duration;
}

String _formatConversationTime(BuildContext context, DateTime value) {
  final local = value.toLocal();
  final mediaQuery = MediaQuery.maybeOf(context);
  final time = MaterialLocalizations.of(context).formatTimeOfDay(
    TimeOfDay.fromDateTime(local),
    alwaysUse24HourFormat: mediaQuery?.alwaysUse24HourFormat ?? false,
  );
  final now = DateTime.now().toLocal();
  if (local.year == now.year &&
      local.month == now.month &&
      local.day == now.day) {
    return time;
  }
  return '${local.month}/${local.day} $time';
}

String _formatConversationDuration(Duration duration) {
  final totalSeconds = duration.inSeconds;
  final hours = totalSeconds ~/ 3600;
  final minutes = (totalSeconds % 3600) ~/ 60;
  final seconds = totalSeconds % 60;
  if (hours > 0) {
    return '${hours}h ${minutes}m';
  }
  if (minutes > 0) {
    return '${minutes}m ${seconds}s';
  }
  return '${seconds}s';
}

IconData conversationIcon(CcbConversationItemKind kind) {
  return switch (kind) {
    CcbConversationItemKind.userMessage => Icons.person,
    CcbConversationItemKind.agentReply => Icons.smart_toy,
    CcbConversationItemKind.callbackRequest => Icons.record_voice_over,
    CcbConversationItemKind.commsItem => Icons.forum,
    CcbConversationItemKind.statusEvent => Icons.info_outline,
    CcbConversationItemKind.toolEvent => Icons.construction,
    CcbConversationItemKind.artifactCard => Icons.article,
    CcbConversationItemKind.terminalHistoryBlock => Icons.history,
    CcbConversationItemKind.systemNotice => Icons.tune,
  };
}

String conversationStateLabel(CcbConversationDeliveryState state) {
  return switch (state) {
    CcbConversationDeliveryState.pending => 'Pending',
    CcbConversationDeliveryState.queued => 'Queued',
    CcbConversationDeliveryState.sent => 'Sent',
    CcbConversationDeliveryState.failed => 'Failed',
    CcbConversationDeliveryState.unconfirmed => 'Check pane',
  };
}

void showBlockedConversationLink(BuildContext context, String text) {
  ScaffoldMessenger.of(
    context,
  ).showSnackBar(SnackBar(content: Text('Open links from raw source: $text')));
}

bool isOpenableExternalUrl(String? href) {
  final uri = href == null ? null : Uri.tryParse(href);
  if (uri == null || !uri.hasScheme) {
    return false;
  }
  return uri.scheme == 'http' || uri.scheme == 'https';
}

Future<void> confirmAndOpenExternalUrl(BuildContext context, String url) async {
  final strings = CcbMobileLocalizations.of(context);
  final confirmed = await showDialog<bool>(
    context: context,
    builder: (context) {
      return AlertDialog(
        title: Text(strings.openUrl),
        content: Text(strings.openUrlQuestion(url)),
        actions: [
          TextButton(
            key: const ValueKey('open-url-cancel-action'),
            onPressed: () => Navigator.of(context).pop(false),
            child: Text(strings.cancel),
          ),
          FilledButton(
            key: const ValueKey('open-url-confirm-action'),
            onPressed: () => Navigator.of(context).pop(true),
            child: Text(strings.open),
          ),
        ],
      );
    },
  );
  if (confirmed != true || !context.mounted) {
    return;
  }
  try {
    final opened = await openExternalUrl(url);
    if (!opened && context.mounted) {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text(strings.couldNotOpenUrl)));
    }
  } catch (error) {
    if (!context.mounted) {
      return;
    }
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text('${strings.couldNotOpenUrl}: $error')),
    );
  }
}

/// A failed request is not evidence that the native agent socket disconnected.
/// Only explicit host-unavailable responses use the disconnected presentation.
class OmpConversationErrorBanner extends StatelessWidget {
  const OmpConversationErrorBanner({
    super.key,
    required this.error,
    required this.onRetry,
  });

  final String error;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    final chinese = CcbMobileLocalizations.of(context).isChinese;
    final hostUnavailable =
        error.contains('OMP Collab snapshot not ready') ||
        error.contains('OMP Collab snapshot is not ready') ||
        error.contains('OMP Collab is unavailable') ||
        error.contains('host_disconnected') ||
        error.contains('host_not_ready');
    final explanation =
        hostUnavailable
            ? (chinese
                ? 'OMP 主机尚未就绪。保留的消息可能已过期，请重新连接后再发送。'
                : 'OMP Collab disconnected. Retained messages may be stale; reconnect before sending.')
            : (chinese
                ? 'OMP 会话无法加载。请求失败并不代表原生连接已断开；保留的消息可能已过期。'
                : 'OMP conversation could not load. A failed request does not mean the native socket disconnected; retained messages may be stale.');
    return MaterialBanner(
      key: ValueKey(
        hostUnavailable
            ? 'omp-collab-disconnected'
            : 'omp-conversation-load-failed',
      ),
      content: Text(
        hostUnavailable
            ? explanation
            : (chinese
                ? 'OMP 会话无法加载。保留的消息可能已过期。'
                : 'OMP conversation could not load. Retained messages may be stale.'),
      ),
      actions: [
        TextButton(
          onPressed:
              () => showDialog<void>(
                context: context,
                builder:
                    (context) => AlertDialog(
                      title: Text(chinese ? '会话请求' : 'Conversation request'),
                      content: Text(explanation),
                      actions: [
                        TextButton(
                          onPressed: () => Navigator.of(context).pop(),
                          child: Text(chinese ? '关闭' : 'Close'),
                        ),
                      ],
                    ),
              ),
          child: Text(chinese ? '查看详情' : 'Details'),
        ),
        TextButton(
          onPressed: onRetry,
          child: Text(
            hostUnavailable
                ? (chinese ? '重新连接' : 'Reconnect')
                : (chinese ? '重试' : 'Retry'),
          ),
        ),
      ],
    );
  }
}
