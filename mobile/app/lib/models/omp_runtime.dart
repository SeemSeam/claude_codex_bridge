/// Bounded, transcript-free native OMP runtime observation.
class OmpPendingUiOption {
  const OmpPendingUiOption({required this.label, this.description});
  final String label;
  final String? description;
  factory OmpPendingUiOption.fromJson(Object? value) {
    if (value is String) return OmpPendingUiOption(label: _uiText(value, 256)!);
    final map = value is Map ? value : const <Object?, Object?>{};
    return OmpPendingUiOption(label: _uiText(map['label'], 256) ?? '', description: _uiText(map['description'], 512));
  }
}

class OmpPendingUiRequest {
  const OmpPendingUiRequest({required this.reqId, required this.kind, required this.title, this.options = const [], this.initialIndex, this.marker, this.checkedIndices = const [], this.helpText, this.prefill});
  final int reqId; final String kind; final String title;
  final List<OmpPendingUiOption> options; final int? initialIndex; final String? marker;
  final List<int> checkedIndices; final String? helpText; final String? prefill;
  factory OmpPendingUiRequest.fromJson(Map<String, Object?> json) => OmpPendingUiRequest(
    reqId: _integer(json['req_id']) ?? -1, kind: _text(json['kind']) ?? '', title: _uiText(json['title'], 512) ?? '',
    options: (json['options'] is List ? (json['options'] as List) : const []).take(16).map(OmpPendingUiOption.fromJson).toList(growable: false), initialIndex: _integer(json['initial_index']), marker: _uiText(json['marker'], 512),
    checkedIndices: (json['checked_indices'] is List ? (json['checked_indices'] as List) : const []).take(16).map((v) => _integer(v) ?? -1).where((v) => v >= 0 && v < 16).toList(growable: false), helpText: _uiText(json['help_text'], 512), prefill: _uiText(json['prefill'], 512),
  );
}

String? _uiText(Object? value, int maxLength) => value is! String ? null : value.length <= maxLength ? value : value.substring(0, maxLength);

OmpPendingUiRequest? _pendingUiRequest(Object? value) {
  if (value is! Map || value['req_id'] is! int) return null;
  final request = OmpPendingUiRequest.fromJson(_map(value));
  return request.reqId < 0 || !const ['select', 'editor'].contains(request.kind) ? null : request;
}

/// Bounded, transcript-free native OMP runtime observation.
class OmpRuntime {
  const OmpRuntime({required this.connected, required this.ready,
    required this.observedAt, this.isStreaming, this.isAborting = false,
    this.queuedMessageCount = 0, this.startedAt, this.model,
    this.thinkingLevel, this.contextUsage, this.agents = const [],
    this.tasks = const [], this.activities = const [],
    this.progressAvailable = false, this.truncated = false, this.statusBar, this.pendingUiRequest});

  final bool connected;
  final bool ready;
  final bool? isStreaming;
  final bool isAborting;
  final int queuedMessageCount;
  final DateTime? startedAt;
  final DateTime? observedAt;
  final OmpRuntimeModel? model;
  final String? thinkingLevel;
  final OmpContextUsage? contextUsage;
  final List<OmpRuntimeAgent> agents;
  final List<OmpRuntimeTask> tasks;
  final List<OmpRuntimeActivity> activities;
  final bool progressAvailable;
  final bool truncated;
  final OmpStatusBar? statusBar;
  final OmpPendingUiRequest? pendingUiRequest;

  bool get hasNativeStatus => statusBar?.available == true;

  OmpRuntime disconnected() => OmpRuntime(connected: false, ready: false,
    observedAt: observedAt, isStreaming: isStreaming, isAborting: isAborting,
    queuedMessageCount: queuedMessageCount, startedAt: startedAt, model: model,
    thinkingLevel: thinkingLevel, contextUsage: contextUsage, agents: agents,
    tasks: tasks, activities: activities, progressAvailable: progressAvailable,
    truncated: truncated, statusBar: statusBar, pendingUiRequest: pendingUiRequest);

  Map<String, Object?> stableSnapshot() {
    final snapshot = toJson()..remove('observed_at');
    // Prompts affect live refresh identity but are never persisted in toJson.
    final request = pendingUiRequest;
    snapshot['pending_ui_request'] = request == null ? null : {
      'req_id': request.reqId, 'kind': request.kind, 'title': request.title,
      'options': [for (final option in request.options) {'label': option.label, 'description': option.description}],
      'initial_index': request.initialIndex, 'marker': request.marker,
      'checked_indices': request.checkedIndices, 'help_text': request.helpText, 'prefill': request.prefill,
    };
    final native = snapshot['status_bar'];
    if (native is Map<String, Object?>) {
      native..remove('observed_at')..remove('elapsed_ms');
    }
    return snapshot;
  }

  String get mainState => !connected ? 'offline' : !ready ? 'unknown'
      : hasNativeStatus ? (statusBar!.isStreaming ? 'working' : 'idle')
      : isAborting || isStreaming == true || queuedMessageCount > 0
      ? 'working' : isStreaming == false ? 'idle' : 'unknown';

  factory OmpRuntime.fromJson(Map<String, Object?> json, {String? sessionId}) => OmpRuntime(
    connected: json['connected'] == true, ready: json['ready'] == true,
    observedAt: _date(json['observed_at']),
    isStreaming: json['is_streaming'] is bool ? json['is_streaming'] as bool : null,
    isAborting: json['is_aborting'] == true,
    queuedMessageCount: _integer(json['queued_message_count']) ?? 0,
    startedAt: _date(json['started_at']),
    model: json['model'] is Map ? OmpRuntimeModel.fromJson(_map(json['model'])) : null,
    thinkingLevel: _text(json['thinking_level']),
    contextUsage: json['context_usage'] is Map ? OmpContextUsage.fromJson(_map(json['context_usage'])) : null,
    agents: _rows(json['agents']).map(OmpRuntimeAgent.fromJson).toList(growable: false),
    tasks: _rows(json['tasks']).map(OmpRuntimeTask.fromJson).toList(growable: false),
    activities: _rows(json['activities']).take(8).map(OmpRuntimeActivity.fromJson).toList(growable: false),
    progressAvailable: json['progress_available'] == true,
    truncated: json['truncated'] == true,
    statusBar: json['status_bar'] is Map ? OmpStatusBar.fromJson(_map(json['status_bar']), expectedSessionId: sessionId) : null,
    pendingUiRequest: _pendingUiRequest(json['pending_ui_request']),
  );

  Map<String, Object?> toJson() => {
    'connected': connected, 'ready': ready, 'observed_at': _iso(observedAt),
    'status_bar': statusBar?.toJson() ?? {'available': false},
    'is_streaming': isStreaming, 'is_aborting': isAborting,
    'queued_message_count': queuedMessageCount, 'started_at': _iso(startedAt),
    'model': model?.toJson(), 'thinking_level': thinkingLevel,
    'context_usage': contextUsage?.toJson(),
    'agents': [for (final agent in agents) agent.toJson()],
    'activities': [for (final activity in activities) activity.toJson()],
    'progress_available': progressAvailable,
    'tasks': [for (final task in tasks) task.toJson()], 'truncated': truncated,
  };
}

/// Snapshot produced by the native session, never inferred from chat/tool text.
class OmpStatusBar {
  const OmpStatusBar({required this.available, this.source, this.sessionId,
    this.observedAt, this.isStreaming = false, this.startedAt, this.elapsedMs,
    this.step, this.todos = const OmpTodos(available: false)});
  final bool available;
  final String? source;
  final String? sessionId;
  final DateTime? observedAt;
  final bool isStreaming;
  final DateTime? startedAt;
  final int? elapsedMs;
  final String? step;
  final OmpTodos todos;
  factory OmpStatusBar.fromJson(Map<String, Object?> json, {String? expectedSessionId}) {
    final source = _text(json['source']);
    final sessionId = _text(json['session_id']);
    final observedAt = _date(json['observed_at']);
    return OmpStatusBar(
      available: json['available'] == true && source == 'native_events' &&
          sessionId != null && (expectedSessionId == null || sessionId == expectedSessionId) &&
          observedAt != null && json['is_streaming'] is bool,
      source: source, sessionId: sessionId, observedAt: observedAt,
      isStreaming: json['is_streaming'] == true, startedAt: _date(json['started_at']),
      elapsedMs: _nonnegative(json['elapsed_ms']), step: _text(json['step']),
      todos: OmpTodos.fromJson(_map(json['todos'])));
  }
  Map<String, Object?> toJson() => !available ? {'available': false} : {
    'available': true, 'source': source, 'session_id': sessionId,
    'observed_at': _iso(observedAt), 'is_streaming': isStreaming,
    'started_at': _iso(startedAt), 'elapsed_ms': elapsedMs, 'step': step,
    'todos': todos.toJson(),
  };
}

class OmpTodos {
  const OmpTodos({required this.available, this.done = 0, this.total = 0,
    this.inProgress = 0, this.blocked = 0, this.items = const [], this.truncated = false});
  final bool available;
  final int done, total, inProgress, blocked;
  final List<OmpTodoItem> items;
  final bool truncated;
  factory OmpTodos.fromJson(Map<String, Object?> json) => OmpTodos(
    available: json['available'] == true && _nonnegative(json['done']) != null &&
        _nonnegative(json['total']) != null,
    done: _nonnegative(json['done']) ?? 0, total: _nonnegative(json['total']) ?? 0,
    inProgress: _nonnegative(json['in_progress']) ?? 0,
    blocked: _nonnegative(json['blocked']) ?? 0,
    items: _rows(json['items']).map(OmpTodoItem.fromJson).toList(growable: false),
    truncated: json['truncated'] == true);
  Map<String, Object?> toJson() => !available ? {'available': false} : {
    'available': true, 'done': done, 'total': total, 'in_progress': inProgress,
    'blocked': blocked, 'items': [for (final item in items) item.toJson()],
    'truncated': truncated,
  };
}

class OmpTodoItem {
  const OmpTodoItem({required this.content, required this.status, required this.phase, this.blocker});
  final String content, status, phase;
  final String? blocker;
  factory OmpTodoItem.fromJson(Map<String, Object?> json) => OmpTodoItem(
    content: _text(json['content']) ?? '', status: _text(json['status']) ?? 'unknown',
    phase: _text(json['phase']) ?? '', blocker: _text(json['blocker']));
  Map<String, Object?> toJson() => {'content': content, 'status': status,
    'phase': phase, if (blocker != null) 'blocker': blocker};
}

class OmpRuntimeModel {
  const OmpRuntimeModel({required this.provider, required this.id, this.name});
  final String provider;
  final String id;
  final String? name;
  factory OmpRuntimeModel.fromJson(Map<String, Object?> json) => OmpRuntimeModel(
    provider: _text(json['provider']) ?? '', id: _text(json['id']) ?? '', name: _text(json['name']));
  Map<String, Object?> toJson() => {'provider': provider, 'id': id, if (name != null) 'name': name};
}

class OmpContextUsage {
  const OmpContextUsage({this.tokens, this.contextWindow, this.percent});
  final int? tokens;
  final int? contextWindow;
  final double? percent;
  factory OmpContextUsage.fromJson(Map<String, Object?> json) => OmpContextUsage(
    tokens: _integer(json['tokens']), contextWindow: _integer(json['contextWindow']),
    percent: (json['percent'] as num?)?.toDouble());
  Map<String, Object?> toJson() => {'tokens': tokens, 'contextWindow': contextWindow, 'percent': percent};
}

class OmpRuntimeAgent {
  const OmpRuntimeAgent({required this.id, required this.displayName,
    required this.kind, required this.status, this.parentId, this.createdAt,
    this.lastActivity, this.hasSessionFile = false});
  final String id;
  final String displayName;
  final String kind;
  final String status;
  final String? parentId;
  final DateTime? createdAt;
  final DateTime? lastActivity;
  final bool hasSessionFile;
  factory OmpRuntimeAgent.fromJson(Map<String, Object?> json) => OmpRuntimeAgent(
    id: _text(json['id']) ?? '', displayName: _text(json['displayName']) ?? '',
    kind: _text(json['kind']) ?? '', status: _text(json['status']) ?? 'unknown',
    parentId: _text(json['parentId']), createdAt: _date(json['createdAt']),
    lastActivity: _date(json['lastActivity']), hasSessionFile: json['hasSessionFile'] == true);
  Map<String, Object?> toJson() => {'id': id, 'displayName': displayName, 'kind': kind,
    'status': status, 'parentId': parentId, 'createdAt': _iso(createdAt),
    'lastActivity': _iso(lastActivity), 'hasSessionFile': hasSessionFile};
}

class OmpRuntimeTask {
  const OmpRuntimeTask({required this.id, required this.agent, required this.status,
    required this.updatedAt, this.description, this.parentToolCallId, this.index,
    this.detached, this.startedAt, this.durationMs, this.toolName,
    this.task, this.intent, this.stage, this.progressText, this.completionPercent,
    this.toolCount, this.requests, this.tokens, this.contextTokens, this.contextWindow});
  final String id;
  final String agent;
  final String status;
  final DateTime? updatedAt;
  final String? description;
  final String? parentToolCallId;
  final int? index;
  final bool? detached;
  final DateTime? startedAt;
  final int? durationMs;
  final String? toolName;
  final String? task;
  final String? intent;
  final String? stage;
  final String? progressText;
  final double? completionPercent;
  final int? toolCount;
  final int? requests;
  final int? tokens;
  final int? contextTokens;
  final int? contextWindow;
  factory OmpRuntimeTask.fromJson(Map<String, Object?> json) => OmpRuntimeTask(
    id: _text(json['id']) ?? '', agent: _text(json['agent']) ?? '',
    status: _text(json['status']) ?? 'unknown', updatedAt: _date(json['updated_at']),
    description: _text(json['description']), parentToolCallId: _text(json['parent_tool_call_id']),
    index: _integer(json['index']), detached: json['detached'] as bool?,
    startedAt: _date(json['started_at']), durationMs: _integer(json['duration_ms']),
    toolName: _text(json['tool_name']), task: _text(json['task']),
    intent: _text(json['intent']), stage: _text(json['stage'], limit: 256),
    progressText: _text(json['progress_text'], limit: 768, tail: true),
    completionPercent: _percent(json['completion_percent']),
    toolCount: _nonnegative(json['tool_count']), requests: _nonnegative(json['requests']),
    tokens: _nonnegative(json['tokens']), contextTokens: _nonnegative(json['context_tokens']),
    contextWindow: _nonnegative(json['context_window']));
  Map<String, Object?> toJson() => {'id': id, 'agent': agent, 'status': status,
    'updated_at': _iso(updatedAt), if (description != null) 'description': description,
    if (parentToolCallId != null) 'parent_tool_call_id': parentToolCallId,
    if (index != null) 'index': index, if (detached != null) 'detached': detached,
    if (startedAt != null) 'started_at': _iso(startedAt),
    if (durationMs != null) 'duration_ms': durationMs, if (toolName != null) 'tool_name': toolName,
    if (task != null) 'task': task, if (intent != null) 'intent': intent,
    if (stage != null) 'stage': stage, if (progressText != null) 'progress_text': progressText,
    if (completionPercent != null) 'completion_percent': completionPercent,
    if (toolCount != null) 'tool_count': toolCount, if (requests != null) 'requests': requests,
    if (tokens != null) 'tokens': tokens, if (contextTokens != null) 'context_tokens': contextTokens,
    if (contextWindow != null) 'context_window': contextWindow};
}

class OmpRuntimeActivity {
  const OmpRuntimeActivity({required this.id, required this.kind,
    required this.status, required this.title, required this.updatedAt,
    this.text, this.toolName, this.startedAt, this.durationMs});
  final String id;
  final String kind;
  final String status;
  final String title;
  final String? text;
  final String? toolName;
  final DateTime? startedAt;
  final DateTime? updatedAt;
  final int? durationMs;
  factory OmpRuntimeActivity.fromJson(Map<String, Object?> json) => OmpRuntimeActivity(
    id: _text(json['id']) ?? '', kind: _text(json['kind']) ?? 'notice',
    status: _text(json['status']) ?? 'info', title: _text(json['title']) ?? '',
    text: _text(json['text'], limit: 768, tail: true), toolName: _text(json['tool_name']),
    startedAt: _date(json['started_at']), updatedAt: _date(json['updated_at']),
    durationMs: _nonnegative(json['duration_ms']));
  Map<String, Object?> toJson() => {'id': id, 'kind': kind, 'status': status,
    'title': title, 'updated_at': _iso(updatedAt),
    if (text != null) 'text': text, if (toolName != null) 'tool_name': toolName,
    if (startedAt != null) 'started_at': _iso(startedAt),
    if (durationMs != null) 'duration_ms': durationMs};
}

Map<String, Object?> _map(Object? value) => value is Map
    ? {for (final entry in value.entries) entry.key.toString(): entry.value} : const {};
Iterable<Map<String, Object?>> _rows(Object? value) => value is Iterable
    ? value.whereType<Map>().take(64).map(_map) : const [];
String? _text(Object? value, {int limit = 512, bool tail = false}) {
  if (value is! String || value.trim().isEmpty) return null;
  final text = value.trim();
  return text.length > limit ? (tail ? text.substring(text.length - limit) : text.substring(0, limit)) : text;
}
int? _integer(Object? value) => value is num ? value.toInt() : int.tryParse('$value');
int? _nonnegative(Object? value) => value is num && value.isFinite && value >= 0
    ? value.toInt() : null;
double? _percent(Object? value) => value is num && value.isFinite && value >= 0 && value <= 100
    ? value.toDouble() : null;
DateTime? _date(Object? value) => value is num
    ? DateTime.fromMillisecondsSinceEpoch(value.toInt(), isUtc: true)
    : DateTime.tryParse('$value')?.toUtc();
String? _iso(DateTime? value) => value?.toUtc().toIso8601String();
