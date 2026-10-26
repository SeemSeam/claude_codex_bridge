import 'package:flutter/material.dart';
import 'package:gpt_markdown/gpt_markdown.dart';

/// Body text style for rendered Markdown paragraphs.
TextStyle? ccbMarkdownTextStyle(BuildContext context) {
  final theme = Theme.of(context);
  return theme.textTheme.bodyMedium?.copyWith(
    color: theme.colorScheme.onSurface,
  );
}

GptMarkdownThemeData ccbMarkdownTheme(BuildContext context) {
  final theme = Theme.of(context);
  final colorScheme = theme.colorScheme;
  final textTheme = theme.textTheme;
  final codeBackground = colorScheme.tertiaryContainer.withValues(
    alpha: colorScheme.brightness == Brightness.dark ? 0.26 : 0.34,
  );
  final quoteBackground = colorScheme.secondaryContainer.withValues(
    alpha: colorScheme.brightness == Brightness.dark ? 0.20 : 0.28,
  );
  return GptMarkdownTheme.of(context).copyWith(
    h1: textTheme.headlineSmall?.copyWith(
      color: colorScheme.primary,
      fontWeight: FontWeight.w700,
    ),
    h2: textTheme.titleLarge?.copyWith(
      color: colorScheme.secondary,
      fontWeight: FontWeight.w700,
    ),
    h3: textTheme.titleMedium?.copyWith(
      color: colorScheme.tertiary,
      fontWeight: FontWeight.w700,
    ),
    linkColor: colorScheme.secondary,
    linkHoverColor: colorScheme.secondary,
    autoAddDividerLineAfterH1: false,
    inlineCode: InlineCodeStyle(
      color: colorScheme.tertiary,
      fontFamily: 'monospace',
      backgroundColor: codeBackground,
    ),
    styleSheet: GptMarkdownStyleSheet(
      blockSpacing: 10,
      link: LinkStyle(
        color: colorScheme.secondary,
        decoration: TextDecoration.underline,
      ),
      codeBlock: CodeBlockStyle(
        backgroundColor: codeBackground,
        borderColor: colorScheme.outlineVariant,
        borderRadius: const Radius.circular(6),
        padding: const EdgeInsets.all(10),
      ),
      blockQuote: BlockQuoteStyle(
        barColor: colorScheme.secondary,
        barWidth: 4,
        backgroundColor: quoteBackground,
        padding: const EdgeInsets.fromLTRB(12, 8, 10, 8),
        textStyle: textTheme.bodyMedium?.copyWith(
          color: colorScheme.onSurfaceVariant,
          fontStyle: FontStyle.italic,
        ),
      ),
      table: TableStyle(
        borderColor: colorScheme.outlineVariant,
        headerTextStyle: textTheme.bodyMedium?.copyWith(
          color: colorScheme.primary,
          fontWeight: FontWeight.w700,
        ),
      ),
    ),
  );
}

/// Selectable, app-themed Markdown. The transparent Material hosts task-list
/// checkboxes, which bubbles do not otherwise provide.
class CcbMarkdown extends StatelessWidget {
  const CcbMarkdown({
    required this.data,
    required this.onLinkTap,
    super.key,
  });

  final String data;
  final void Function(String url, String label) onLinkTap;

  @override
  Widget build(BuildContext context) {
    return Material(
      type: MaterialType.transparency,
      child: SelectionArea(
        child: GptMarkdownTheme(
          gptThemeData: ccbMarkdownTheme(context),
          child: GptMarkdown(
            data,
            style: ccbMarkdownTextStyle(context),
            onLinkTap: onLinkTap,
          ),
        ),
      ),
    );
  }
}
