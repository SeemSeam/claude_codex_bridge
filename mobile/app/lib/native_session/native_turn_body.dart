import 'dart:convert';

import 'package:flutter/material.dart';

import 'native_session_turn.dart';
import 'native_content_page.dart';

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
    this.readOriginal = 'Read original',
    this.preview = 'Preview only — content exceeds inline budget',
    this.previous = 'Previous',
    this.next = 'Next',
    this.close = 'Close',
    this.retry = 'Retry',
    this.loadFailed =
        'Original could not load. It may have changed; close and refresh the conversation.',
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
  final String readOriginal;
  final String preview;
  final String previous;
  final String next;
  final String close;
  final String retry;
  final String loadFailed;

  String status(NativeSessionStatus value) => switch (value) {
    NativeSessionStatus.running => running,
    NativeSessionStatus.completed => completed,
    NativeSessionStatus.failed => failed,
    NativeSessionStatus.aborted => aborted,
    NativeSessionStatus.unknown => unknown,
  };
}

Future<void> showNativeOriginalReader(
  BuildContext context, {
  required String cursor,
  required Future<NativeContentPage> Function(String) loader,
  NativeTurnLabels labels = const NativeTurnLabels(),
}) => showDialog<void>(
  context: context,
  builder:
      (_) => _OriginalReader(cursor: cursor, loader: loader, labels: labels),
);

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
    this.contentLoader,
    this.previewText = '',
  });

  final NativeSessionTurn turn;
  final Widget Function(BuildContext, String) textBuilder;
  final NativeTurnLabels labels;
  final Future<NativeContentPage> Function(String cursor)? contentLoader;
  final String previewText;

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
        if (turn.readCursor != null) ...[
          Text(labels.preview),
          if (previewText.isNotEmpty)
            SelectionArea(child: textBuilder(context, previewText)),
          TextButton(
            key: const ValueKey('native-read-original'),
            onPressed:
                contentLoader == null
                    ? null
                    : () => showNativeOriginalReader(
                      context,
                      cursor: turn.readCursor!,
                      loader: contentLoader!,
                      labels: labels,
                    ),
            child: Text(labels.readOriginal),
          ),
        ],
        if (turn.retainedInline)
          _Disclosure(
            key: ValueKey((turn.sessionId, turn.turnId, 'retained-inline')),
            title: '${labels.message} — previously loaded snapshot',
            builder:
                (context) => Column(
                  crossAxisAlignment: CrossAxisAlignment.stretch,
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    if (answer.isNotEmpty)
                      SelectionArea(child: textBuilder(context, answer)),
                    for (final item in process) _processBlock(context, item),
                  ],
                ),
          ),
        if (!turn.retainedInline && process.isNotEmpty)
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
        if (!turn.retainedInline && answer.isNotEmpty)
          SelectionArea(child: textBuilder(context, answer))
        else if (turn.readCursor == null && !turn.retainedInline)
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

/// Only the current page's text is retained; previous pages are fetched again
/// using their immutable cursors. Closing disposes text and selection state.
class _OriginalReader extends StatefulWidget {
  const _OriginalReader({
    required this.cursor,
    required this.loader,
    required this.labels,
  });

  final String cursor;
  final Future<NativeContentPage> Function(String) loader;
  final NativeTurnLabels labels;

  @override
  State<_OriginalReader> createState() => _OriginalReaderState();
}

class _OriginalReaderState extends State<_OriginalReader> {
  final _cursors = <String>[];
  NativeContentPage? _page;
  String? _version;
  int _index = 0;
  bool _loading = true;
  bool _failed = false;

  @override
  void initState() {
    super.initState();
    _cursors.add(widget.cursor);
    _load(0);
  }

  Future<void> _load(int index) async {
    setState(() {
      _loading = true;
      _failed = false;
    });
    try {
      final page = await widget.loader(_cursors[index]);
      if (!mounted) return;
      if (_version != null && _version != page.version) {
        throw const FormatException('Original changed');
      }
      if (index == _index + 1 &&
          _page != null &&
          page.offset != _page!.offset + utf8.encode(_page!.text).length) {
        throw const FormatException('Noncontiguous original page');
      }
      setState(() {
        _version = page.version;
        _page = page;
        _index = index;
        _loading = false;
      });
    } catch (_) {
      if (!mounted) return;
      setState(() {
        _loading = false;
        _failed = true;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final page = _page;
    final labels = widget.labels;
    return AlertDialog(
      key: const ValueKey('native-original-reader'),
      title: Text(labels.readOriginal),
      content: SizedBox(
        width: 640,
        height: MediaQuery.sizeOf(context).height * 0.55,
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (_loading) const LinearProgressIndicator(),
            if (_failed)
              Semantics(liveRegion: true, child: Text(labels.loadFailed)),
            if (page != null) ...[
              Text(
                '${page.offset}–${page.offset + utf8.encode(page.text).length} / ${page.totalBytes} bytes',
              ),
              Expanded(
                child: SingleChildScrollView(
                  key: ValueKey((_index, page.version)),
                  child: SelectableText(
                    page.text,
                    key: const ValueKey('native-original-text'),
                  ),
                ),
              ),
            ],
          ],
        ),
      ),
      actions: [
        if (_failed && page == null)
          TextButton(
            onPressed: _loading ? null : () => _load(0),
            child: Text(labels.retry),
          ),
        TextButton(
          key: const ValueKey('native-original-previous'),
          onPressed: _loading || _index == 0 ? null : () => _load(_index - 1),
          child: Text(labels.previous),
        ),
        TextButton(
          key: const ValueKey('native-original-next'),
          onPressed:
              _loading || page?.nextCursor == null
                  ? null
                  : () {
                    if (_cursors.length == _index + 1) {
                      _cursors.add(page!.nextCursor!);
                    }
                    _load(_index + 1);
                  },
          child: Text(labels.next),
        ),
        TextButton(
          key: const ValueKey('native-original-close'),
          onPressed: () => Navigator.of(context).pop(),
          child: Text(labels.close),
        ),
      ],
    );
  }
}
