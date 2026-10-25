import 'dart:async';

import 'package:flutter/material.dart';

import '../../l10n/ccb_mobile_localizations.dart';
import '../../models/omp_runtime.dart';

/// Native status-bar state, not a transcript or a recent-tool feed.
class OmpRuntimePanel extends StatefulWidget {
  const OmpRuntimePanel({required this.runtime, super.key});
  final OmpRuntime runtime;

  @override
  State<OmpRuntimePanel> createState() => _OmpRuntimePanelState();
}

class _OmpRuntimePanelState extends State<OmpRuntimePanel> {
  final Stopwatch _clock = Stopwatch();
  Timer? _timer;
  String? _observation;
  int? _elapsedMs;

  bool get _live => widget.runtime.connected && widget.runtime.ready;
  OmpStatusBar? get _native => widget.runtime.hasNativeStatus ? widget.runtime.statusBar : null;

  @override
  void initState() {
    super.initState();
    _observe();
  }

  @override
  void didUpdateWidget(covariant OmpRuntimePanel oldWidget) {
    super.didUpdateWidget(oldWidget);
    _observe();
  }

  void _observe() {
    final native = _native;
    final observation = native == null ? null :
        '${native.sessionId}|${native.startedAt}|${native.observedAt}|${native.isStreaming}';
    if (observation != _observation) {
      _observation = observation;
      _elapsedMs = native?.elapsedMs;
      _clock..stop()..reset();
    }
    final running = _live && native?.isStreaming == true && _elapsedMs != null;
    if (running) {
      _clock.start();
      _timer ??= Timer.periodic(const Duration(seconds: 1), (_) {
        if (mounted) setState(() {});
      });
    } else {
      _clock.stop();
      _timer?.cancel();
      _timer = null;
    }
  }

  @override
  void dispose() {
    _timer?.cancel();
    _clock.stop();
    super.dispose();
  }

  String _duration(int milliseconds) {
    final seconds = milliseconds ~/ 1000;
    return '${seconds ~/ 60}:${(seconds % 60).toString().padLeft(2, '0')}';
  }

  @override
  Widget build(BuildContext context) {
    final runtime = widget.runtime;
    final native = _native;
    final chinese = CcbMobileLocalizations.of(context).isChinese;
    String tr(String en, String zh) => chinese ? zh : en;
    final main = switch (runtime.mainState) {
      'working' => tr('Working', '工作中'),
      'idle' => tr('Idle', '空闲'),
      'offline' => tr('Disconnected', '未连接'),
      _ => tr('Status unavailable', '状态不可用'),
    };
    final elapsed = _elapsedMs == null ? null : _duration(_elapsedMs! + _clock.elapsedMilliseconds);
    final todos = _live ? native?.todos : null;
    OmpTodoItem? current;
    for (final item in todos?.items ?? const <OmpTodoItem>[]) {
      if (item.status == 'in_progress') {
        current = item;
        break;
      }
    }
    Widget line(String text, String key, {bool heading = false}) => Text(
      text, key: ValueKey(key), maxLines: 3, overflow: TextOverflow.ellipsis,
      style: Theme.of(context).textTheme.bodySmall?.copyWith(
        fontWeight: heading ? FontWeight.w600 : null));
    void showDetails() {
      showDialog<void>(context: context, builder: (dialogContext) => AlertDialog(
        title: Text(tr('OMP runtime details', 'OMP 运行详情')),
        content: SizedBox(width: 480, child: SingleChildScrollView(
          child: Column(crossAxisAlignment: CrossAxisAlignment.start, children: [
            if (native == null) Text(tr('Native status unavailable', '原生状态不可用')),
            if (todos?.available == true) ...[
              Text('Todo ${todos!.done}/${todos.total}'),
              for (final item in todos.items) Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: Text('${item.status} · ${item.phase}\n${item.content}'
                  '${item.blocker == null ? '' : '\n${item.blocker}'}')),
              if (todos.truncated) Text(tr('Todo list truncated; counts are complete', '任务列表已截断；计数完整')),
            ],
            const Divider(),
            Text('${tr('Native queue', '原生队列')}: ${_live ? runtime.queuedMessageCount : '—'}'),
            if (runtime.model != null) Text('${runtime.model!.provider}/${runtime.model!.name ?? runtime.model!.id}'),
            if (runtime.thinkingLevel != null) Text('${tr('Thinking', '思考')}: ${runtime.thinkingLevel}'),
            if (runtime.contextUsage != null) Text('${tr('Context', '上下文')}: '
              '${runtime.contextUsage!.tokens ?? '—'}/${runtime.contextUsage!.contextWindow ?? '—'}'),
            for (final task in runtime.tasks.take(8)) Text(
              '${task.agent} · ${task.status}\n${task.task ?? task.description ?? ''}'),
            for (final agent in runtime.agents.take(16)) Text(
              '${agent.displayName.isEmpty ? agent.id : agent.displayName} · ${agent.status}'),
          ]))),
        actions: [TextButton(onPressed: () => Navigator.pop(dialogContext),
          child: Text(tr('Close', '关闭')))],
      ));
    }
    return Container(
      key: const ValueKey('omp-runtime-panel'), width: double.infinity,
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 5),
      decoration: BoxDecoration(color: Theme.of(context).colorScheme.surfaceContainerLow,
        borderRadius: BorderRadius.circular(8)),
      child: Column(mainAxisSize: MainAxisSize.min, crossAxisAlignment: CrossAxisAlignment.start, children: [
        Row(children: [
          Expanded(child: line('OMP · $main${elapsed == null ? '' : ' · $elapsed'}',
            'omp-runtime-status', heading: true)),
          TextButton(key: const ValueKey('omp-runtime-details'), onPressed: showDetails,
            style: TextButton.styleFrom(minimumSize: const Size(48, 32), padding: const EdgeInsets.symmetric(horizontal: 8)),
            child: Text(tr('Details', '详情'))),
        ]),
        if (_live && runtime.mainState == 'working' && elapsed == null)
          line(tr('Run time unavailable', '运行时间不可用'), 'omp-runtime-elapsed-unavailable'),
        if (native == null || !_live)
          line(tr('Native status unavailable', '原生状态不可用'), 'omp-runtime-unavailable'),
        line('${tr('Step', '步骤')}: ${!_live || native == null ? tr('Unavailable', '不可用') : !native.isStreaming ? '—' : native.step ?? tr('Waiting for native step', '等待原生步骤')}',
          'omp-runtime-step'),
        line(todos?.available == true
          ? 'Todo ${todos!.done}/${todos.total}${current == null ? '' : ' · ${current.content}'}'
          : tr('Todo unavailable', 'Todo 不可用'), 'omp-runtime-todos'),
      ]),
    );
  }
}
