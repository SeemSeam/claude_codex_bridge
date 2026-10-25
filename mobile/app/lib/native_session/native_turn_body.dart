import 'package:flutter/material.dart';

import 'native_session_turn.dart';
import 'native_content_page.dart';

/// Neutral defaults; hosts may supply their own localized strings.
class NativeTurnLabels {
  const NativeTurnLabels({
    this.reasoning = 'Reasoning',
    this.toolCall = 'Tool call',
    this.toolResult = 'Tool result',
    this.running = 'Running',
    this.completed = 'Completed',
    this.failed = 'Failed',
    this.aborted = 'Aborted',
    this.unknown = 'Status unknown',
    this.noAnswer = 'No assistant answer',
    this.message = 'Assistant message',
    this.readOriginal = 'Complete content',
    this.preview = 'Preview only — content exceeds inline budget',
    this.latestWindow = 'Showing recent content only; earlier content or long passages omitted',
    this.previous = 'Previous',
    this.next = 'Next',
    this.close = 'Close',
    this.retry = 'Retry',
    this.page = 'Page',
    this.continuedBefore = 'Continued from previous page',
    this.continuedAfter = 'Continues on next page',
    this.loadFailed =
        'Content could not load. It may have changed; close and refresh the conversation.',
  });

  final String reasoning;
  final String toolCall;
  final String toolResult;
  final String running;
  final String completed;
  final String failed;
  final String aborted;
  final String unknown;
  final String noAnswer;
  final String message;
  final String readOriginal;
  final String preview;
  final String latestWindow;
  final String previous;
  final String next;
  final String close;
  final String retry;
  final String page;
  final String continuedBefore;
  final String continuedAfter;
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
  required Widget Function(BuildContext, String) textBuilder,
  NativeTurnLabels labels = const NativeTurnLabels(),
}) => showDialog<void>(
  context: context,
  useSafeArea: false,
  builder: (_) => _OriginalReader(
    cursor: cursor,
    loader: loader,
    textBuilder: textBuilder,
    labels: labels,
  ),
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
        if (turn.inlineWindow)
          Text(labels.latestWindow)
        else if (turn.readCursor != null) ...[
          Text(labels.preview),
          if (answer.isEmpty && previewText.isNotEmpty)
            SelectionArea(child: textBuilder(context, previewText)),
        ],
        if (turn.readCursor != null)
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
                      textBuilder: textBuilder,
                    ),
            child: Text(labels.readOriginal),
          ),
        if (answer.isNotEmpty)
          SelectionArea(child: textBuilder(context, answer)),
        if (answer.isEmpty && turn.readCursor == null && status != NativeSessionStatus.running)
          Text(labels.noAnswer),
      ],
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

/// Only the current page is retained; previous pages are refetched by cursor.
class _OriginalReader extends StatefulWidget {
  const _OriginalReader({
    required this.cursor,
    required this.loader,
    required this.textBuilder,
    required this.labels,
  });

  final String cursor;
  final Future<NativeContentPage> Function(String) loader;
  final Widget Function(BuildContext, String) textBuilder;
  final NativeTurnLabels labels;

  @override
  State<_OriginalReader> createState() => _OriginalReaderState();
}

class _OriginalReaderState extends State<_OriginalReader> {
  final _cursors = <String>[];
  final _scroll = ScrollController();
  NativeContentPage? _page;
  String? _version;
  int _index = 0;
  int _request = 0;
  int _requestedIndex = 0;
  bool _loading = false;
  bool _failed = false;

  @override
  void initState() {
    super.initState();
    _cursors.add(widget.cursor);
    _load(0);
  }

  @override
  void dispose() {
    _request++;
    _scroll.dispose();
    super.dispose();
  }

  bool _sameBlock(NativeContentBlock a, NativeContentBlock b) =>
      a.entryId == b.entryId && a.blockIndex == b.blockIndex &&
      a.type == b.type && a.role == b.role;

  void _validate(NativeContentPage page, int index) {
    final old = _page;
    final identities = <(String, int)>{};
    for (final block in page.blocks) {
      if (!identities.add((block.entryId, block.blockIndex))) {
        throw const FormatException('Duplicate content block in page');
      }
    }
    if (page.pageIndex != index || page.version.isEmpty ||
        (_version != null && _version != page.version) ||
        (old != null && old.totalBlocks != page.totalBlocks) ||
        (page.blocks.isEmpty && (page.totalBlocks != 0 || page.nextCursor != null)) ||
        (index == 0 && page.blocks.isNotEmpty && page.blocks.first.continuedBefore) ||
        (page.nextCursor == null && page.blocks.isNotEmpty && page.blocks.last.continuedAfter) ||
        (page.nextCursor != null && _cursors.take(index + 1).contains(page.nextCursor))) {
      throw const FormatException('Inconsistent content page');
    }
    if (old == null || old.blocks.isEmpty || page.blocks.isEmpty) return;
    NativeContentBlock? left;
    NativeContentBlock? right;
    if (index == _index + 1) {
      left = old.blocks.last;
      right = page.blocks.first;
    } else if (index == _index - 1) {
      left = page.blocks.last;
      right = old.blocks.first;
    }
    if (left != null && right != null &&
        (left.continuedAfter != right.continuedBefore ||
         _sameBlock(left, right) != left.continuedAfter)) {
      throw const FormatException('Noncontiguous content fragments');
    }
    if (index + 1 < _cursors.length && page.nextCursor != _cursors[index + 1]) {
      throw const FormatException('Content cursor changed');
    }
  }

  Future<void> _load(int index) async {
    if (_loading) return;
    final request = ++_request;
    _requestedIndex = index;
    setState(() {
      _loading = true;
      _failed = false;
    });
    try {
      final page = await widget.loader(_cursors[index]);
      if (!mounted || request != _request) return;
      _validate(page, index);
      if (_scroll.hasClients) _scroll.jumpTo(0);
      setState(() {
        _version = page.version;
        _page = page;
        _index = index;
        _loading = false;
      });
    } catch (_) {
      if (!mounted || request != _request) return;
      setState(() {
        _loading = false;
        _failed = true;
      });
    }
  }

  Widget _block(BuildContext context, NativeContentBlock block) {
    final labels = widget.labels;
    final prose = block.type == 'text';
    final body = Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      mainAxisSize: MainAxisSize.min,
      children: [
        if (block.continuedBefore) Text(labels.continuedBefore),
        if (prose || block.type == 'reasoning')
          SelectionArea(child: widget.textBuilder(
            context, '${block.renderPrefix}${block.text}${block.renderSuffix}',
          ))
        else
          SelectableText(
            block.text,
            style: Theme.of(context).textTheme.bodyMedium?.copyWith(fontFamily: 'monospace'),
          ),
        if (block.continuedAfter) Text(labels.continuedAfter),
      ],
    );
    if (prose) {
      return Padding(
        key: ValueKey('native-content-prose-${block.entryId}-${block.blockIndex}'),
        padding: const EdgeInsets.only(bottom: 16),
        child: body,
      );
    }
    return _Disclosure(
      key: ValueKey('native-content-execution-${block.entryId}-${block.blockIndex}'),
      title: [
        switch (block.type) {
          'reasoning' => labels.reasoning,
          'tool_call' => labels.toolCall,
          _ => labels.toolResult,
        },
        if (block.name?.isNotEmpty == true) block.name!,
        if (block.callId?.isNotEmpty == true) block.callId!,
        if (block.isError == true) labels.failed,
        if (block.continuedBefore) labels.continuedBefore,
        if (block.continuedAfter) labels.continuedAfter,
      ].join(' · '),
      builder: (_) => body,
    );
  }

  @override
  Widget build(BuildContext context) {
    final page = _page;
    final labels = widget.labels;
    return Dialog.fullscreen(
      key: const ValueKey('native-original-reader'),
      child: Scaffold(
        appBar: AppBar(
          automaticallyImplyLeading: false,
          title: Text(labels.readOriginal),
          actions: [
            TextButton(
              key: const ValueKey('native-original-close'),
              onPressed: () => Navigator.of(context).pop(),
              child: Text(labels.close),
            ),
          ],
        ),
        body: SafeArea(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              if (_loading) const LinearProgressIndicator(),
              if (_failed) ...[
                Semantics(liveRegion: true, child: Text(labels.loadFailed)),
                TextButton(
                  onPressed: _loading ? null : () => _load(_requestedIndex),
                  child: Text(labels.retry),
                ),
              ],
              if (page != null)
                Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
                  child: Text('${labels.page} ${page.pageIndex + 1}'),
                ),
              Expanded(
                child: SingleChildScrollView(
                  controller: _scroll,
                  padding: const EdgeInsets.all(16),
                  child: Column(
                    key: ValueKey((_index, page?.version)),
                    crossAxisAlignment: CrossAxisAlignment.stretch,
                    children: [
                      if (page != null)
                        for (final block in page.blocks) _block(context, block),
                    ],
                  ),
                ),
              ),
              Row(
                mainAxisAlignment: MainAxisAlignment.spaceBetween,
                children: [
                  TextButton(
                    key: const ValueKey('native-original-previous'),
                    onPressed: _loading || _index == 0 ? null : () => _load(_index - 1),
                    child: Text(labels.previous),
                  ),
                  TextButton(
                    key: const ValueKey('native-original-next'),
                    onPressed: _loading || page?.nextCursor == null ? null : () {
                      if (_cursors.length == _index + 1) _cursors.add(page!.nextCursor!);
                      _load(_index + 1);
                    },
                    child: Text(labels.next),
                  ),
                ],
              ),
            ],
          ),
        ),
      ),
    );
  }
}
