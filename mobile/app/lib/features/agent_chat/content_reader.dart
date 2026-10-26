import 'package:flutter/material.dart';

import '../../models/ccb_content_item.dart';
import 'clipboard_feedback.dart';
import 'content_text_styles.dart';
import 'conversation_item_presentation.dart';

class AgentContentReader extends StatelessWidget {
  const AgentContentReader({required this.items, super.key});

  final List<CcbContentItem> items;

  @override
  Widget build(BuildContext context) {
    if (items.isEmpty) {
      return const Text('No structured content yet');
    }
    return Column(
      key: const ValueKey('structured-content-reader'),
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        for (final item in items) ...[
          ContentItemView(item: item),
          if (item != items.last) const Divider(height: 24),
        ],
      ],
    );
  }
}

class ContentItemView extends StatelessWidget {
  const ContentItemView({required this.item, super.key});

  final CcbContentItem item;

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    final colorScheme = Theme.of(context).colorScheme;
    final title = item.title ?? item.kind;
    return Column(
      key: ValueKey('content-item-${item.id}'),
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(title, style: textTheme.titleMedium),
                  const SizedBox(height: 2),
                  Text(
                    '${item.kind} / ${item.format}'
                    '${item.source == null ? '' : ' / ${item.source}'}',
                    style: textTheme.bodySmall,
                  ),
                ],
              ),
            ),
            IconButton(
              key: ValueKey('copy-content-${item.id}'),
              tooltip: 'Copy content',
              onPressed: () {
                copyTextWithFeedback(context, item.text);
              },
              icon: const Icon(Icons.copy),
            ),
          ],
        ),
        const SizedBox(height: 8),
        DecoratedBox(
          decoration: BoxDecoration(
            color: colorScheme.surface,
            border: Border.all(color: colorScheme.outlineVariant),
            borderRadius: BorderRadius.circular(8),
          ),
          child: Padding(
            padding: const EdgeInsets.all(12),
            child: CcbMarkdown(
              key: ValueKey('markdown-body-${item.id}'),
              data: item.text,
              onLinkTap: (href, label) {
                if (isOpenableExternalUrl(href)) {
                  confirmAndOpenExternalUrl(context, href);
                } else {
                  showBlockedConversationLink(context, href);
                }
              },
            ),
          ),
        ),
        Material(
          color: Colors.transparent,
          child: ExpansionTile(
            key: ValueKey('raw-source-${item.id}'),
            tilePadding: EdgeInsets.zero,
            title: const Text('Raw source'),
            childrenPadding: EdgeInsets.zero,
            children: [
              Align(
                alignment: Alignment.centerLeft,
                child: SelectableText(
                  item.text,
                  style: textTheme.bodySmall?.copyWith(fontFamily: 'monospace'),
                ),
              ),
            ],
          ),
        ),
      ],
    );
  }
}
