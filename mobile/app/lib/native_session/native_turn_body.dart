import 'dart:convert';

import 'package:flutter/material.dart';

import 'native_session_turn.dart';

/// Neutral defaults; hosts may supply their own localized strings.
class NativeTurnLabels {
  const NativeTurnLabels({
    this.process = 'Execution process',
    this.reasoning = 'Reasoning',
    this.toolCall = 'Tool call',
    this.toolResult = 'Tool result',
    this.running = 'Running',
    this.completed = 'Completed',
    this.failed = 'Failed',
    this.aborted = 'Aborted',
    this.unknown = 'Status unknown',
    this.pendingTools = 'Tool execution unfinished',
    this.noAnswer = 'No assistant answer',
    this.message = 'Assistant message',
  });

  final String process;
  final String reasoning;
  final String toolCall;
  final String toolResult;
  final String running;
  final String completed;
  final String failed;
  final String aborted;
  final String unknown;
  final String pendingTools;
  final String noAnswer;
  final String message;

  String status(NativeSessionStatus value) => switch (value) {
    NativeSessionStatus.running => running,
    NativeSessionStatus.completed => completed,
    NativeSessionStatus.failed => failed,
    NativeSessionStatus.aborted => aborted,
    NativeSessionStatus.unknown => unknown,
  };
}

/// A single turn's content, without an outer conversation bubble or actions.
///
/// [textBuilder] renders visible assistant prose using the host's text/markdown
/// renderer. No CCB models, localization, terminal, or business APIs are needed.
class NativeTurnBody extends StatelessWidget {
  const NativeTurnBody({
    super.key,
    required this.turn,
    required this.textBuilder,
    this.labels = const NativeTurnLabels(),
  });

  final NativeSessionTurn turn;
  final Widget Function(BuildContext, String) textBuilder;
  final NativeTurnLabels labels;

  @override
  Widget build(BuildContext context) {
    final process = turn.processBlocks;
    final answer = turn.answerText;
    final status = turn.effectiveStatus;
    final attention =
        status == NativeSessionStatus.failed ||
        status == NativeSessionStatus.aborted ||
        status == NativeSessionStatus.unknown;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      mainAxisSize: MainAxisSize.min,
      children: [
        Semantics(
          liveRegion: true,
          child: Text(
            labels.status(status),
            style: Theme.of(context).textTheme.labelMedium?.copyWith(
              color: attention ? Theme.of(context).colorScheme.error : null,
            ),
          ),
        ),
        if (turn.hasPendingTools) Text(labels.pendingTools),
        if (process.isNotEmpty)
          _Disclosure(
            key: ValueKey((turn.sessionId, turn.turnId, 'process')),
            title: labels.process,
            builder:
                (context) => Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    for (final item in process) _processBlock(context, item),
                  ],
                ),
          ),
        if (answer.isNotEmpty)
          SelectionArea(child: textBuilder(context, answer))
        else
          Text(labels.noAnswer),
      ],
    );
  }

  Widget _processBlock(BuildContext context, NativeSessionProcessBlock item) {
    final block = item.block;
    final title = switch (block.type) {
      NativeSessionBlockType.text => labels.message,
      NativeSessionBlockType.reasoning => labels.reasoning,
      NativeSessionBlockType.toolCall => labels.toolCall,
      NativeSessionBlockType.toolResult => labels.toolResult,
    };
    final name = block.name;
    final callId = block.callId;
    return _Disclosure(
      key: ValueKey((item.entryId, item.blockIndex)),
      title: [
        title,
        if (name != null && name.isNotEmpty) name,
        if (callId != null && callId.isNotEmpty) callId,
        if (block.isError == true) labels.failed,
      ].join(' · '),
      builder: (context) {
        if (block.type == NativeSessionBlockType.text) {
          return SelectionArea(child: textBuilder(context, block.text ?? ''));
        }
        final content = [
          if (block.input != null)
            const JsonEncoder.withIndent('  ').convert(block.input),
          if (block.text != null) block.text!,
        ].join('\n\n');
        return _ScrollableContent(text: content);
      },
    );
  }
}

/// Unlike an eagerly built ExpansionTile children list, this does not invoke
/// the body builder at all while collapsed, even for very large tool output.
class _Disclosure extends StatefulWidget {
  const _Disclosure({super.key, required this.title, required this.builder});

  final String title;
  final WidgetBuilder builder;

  @override
  State<_Disclosure> createState() => _DisclosureState();
}

class _DisclosureState extends State<_Disclosure> {
  bool _expanded = false;

  @override
  Widget build(BuildContext context) => Column(
    crossAxisAlignment: CrossAxisAlignment.stretch,
    mainAxisSize: MainAxisSize.min,
    children: [
      Semantics(
        expanded: _expanded,
        child: TextButton(
          style: TextButton.styleFrom(alignment: Alignment.centerLeft),
          onPressed: () => setState(() => _expanded = !_expanded),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Icon(_expanded ? Icons.expand_less : Icons.expand_more),
              const SizedBox(width: 4),
              Expanded(child: Text(widget.title)),
            ],
          ),
        ),
      ),
      if (_expanded)
        Padding(
          padding: const EdgeInsetsDirectional.only(start: 12, bottom: 8),
          child: widget.builder(context),
        ),
    ],
  );
}

class _ScrollableContent extends StatefulWidget {
  const _ScrollableContent({required this.text});

  final String text;

  @override
  State<_ScrollableContent> createState() => _ScrollableContentState();
}

class _ScrollableContentState extends State<_ScrollableContent> {
  final _controller = ScrollController();

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) => ConstrainedBox(
    constraints: const BoxConstraints(maxHeight: 320),
    child: Scrollbar(
      controller: _controller,
      thumbVisibility: true,
      child: SingleChildScrollView(
        controller: _controller,
        primary: false,
        padding: const EdgeInsetsDirectional.only(end: 16),
        child: SelectableText(widget.text),
      ),
    ),
  );
}
