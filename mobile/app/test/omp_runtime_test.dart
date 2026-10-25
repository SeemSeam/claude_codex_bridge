import 'package:ccb_mobile/features/agent_chat/selected_agent_workspace_model.dart';
import 'package:ccb_mobile/features/agent_chat/agent_chat_controller.dart';
import 'package:ccb_mobile/features/agent_chat/agent_chat_state_helpers.dart';
import 'package:ccb_mobile/features/agent_chat/agent_state_display.dart';
import 'package:ccb_mobile/models/ccb_agent.dart';
import 'package:ccb_mobile/models/ccb_agent_conversation.dart';
import 'package:ccb_mobile/models/ccb_project_view.dart';
import 'package:ccb_mobile/models/omp_runtime.dart';
import 'package:ccb_mobile/models/ccb_conversation_item.dart';
import 'package:test/test.dart';

OmpRuntime runtime({bool connected = true, bool ready = true, bool? streaming = false,
  bool aborting = false, int queue = 0, int second = 0}) => OmpRuntime(
    connected: connected, ready: ready, isStreaming: streaming, isAborting: aborting,
    queuedMessageCount: queue, observedAt: DateTime.utc(2026, 10, 23, 12, 0, second));
CcbAgent agent(OmpRuntime value) => CcbAgent(name: 'omp', provider: 'omp',
  window: 'omp', order: 0, active: true, queueDepth: 99,
  activityState: 'working', ompRuntime: value);
CcbAgentConversation conversation(OmpRuntime value) => CcbAgentConversation(
  projectId: 'p', agentName: 'omp', namespaceEpoch: 1, items: const [],
  ompSessionId: 's', ompGeneration: 'g', branchId: 'b', ompRuntime: value);

void main() {
  Map<String, Object?> native({bool streaming = true, int elapsed = 1234,
    String session = 's', String step = 'Inspect session', int done = 1}) => {
    'available': true, 'source': 'native_events', 'session_id': session,
    'observed_at': '1995-01-01T00:00:00Z', 'is_streaming': streaming,
    'started_at': '1995-01-01T00:00:00Z', 'elapsed_ms': elapsed, 'step': step,
    'todos': {'available': true, 'done': done, 'total': 100, 'in_progress': 1,
      'blocked': 2, 'truncated': true, 'items': [
        {'content': 'Current task', 'status': 'in_progress', 'phase': 'Build', 'blocker': 'Dependency'},
      ]},
  };

  test('native status is typed session-bound and authoritative over legacy busy hints', () {
    final parsed = OmpRuntime.fromJson({'connected': true, 'ready': true,
      'is_streaming': true, 'is_aborting': true, 'queued_message_count': 99,
      'status_bar': native(streaming: false)}, sessionId: 's');
    expect(parsed.mainState, 'idle');
    expect(parsed.statusBar!.elapsedMs, 1234);
    expect(parsed.statusBar!.todos.total, 100);
    expect(parsed.statusBar!.todos.items.single.blocker, 'Dependency');
    expect(parsed.statusBar!.todos.truncated, isTrue);
    expect(OmpRuntime.fromJson(parsed.toJson(), sessionId: 's').toJson(), parsed.toJson());
    final wrongSession = OmpRuntime.fromJson({'status_bar': native(session: 'other')}, sessionId: 's');
    expect(wrongSession.hasNativeStatus, isFalse);
    expect(OmpStatusBar.fromJson({'available': true}).available, isFalse);
    expect(OmpStatusBar.fromJson({...native(), 'elapsed_ms': -1}).elapsedMs, isNull);
    expect(OmpTodos.fromJson({'available': false}).available, isFalse);
  });

  test('native step state and todos change signatures but timing alone does not', () {
    String signature(Map<String, Object?> bar) => conversationSignature(conversation(
      OmpRuntime.fromJson({'connected': true, 'ready': true, 'status_bar': bar})));
    final first = signature(native());
    expect(signature(native()), first);
    expect(signature(native(step: 'Write result')), isNot(first));
    expect(signature(native(elapsed: 2234)), first);
    expect(signature({...native(), 'observed_at': '1995-01-01T00:00:01Z'}), first);
    expect(signature(native(done: 2)), isNot(first));
    expect(signature(native(streaming: false)), isNot(first));
  });
  test('only explicit host unavailable errors freeze retained native runtime', () {
    final controller = AgentChatController();
    final busy = OmpRuntime.fromJson({'connected': true, 'ready': true, 'status_bar': native()});
    controller.applyRemoteConversation(agentName: 'omp', conversation: conversation(busy), shouldScroll: false);
    final view = CcbProjectView.fromProjectViewPayload({'view': {
      'project': {'id': 'p', 'display_name': 'P'}, 'namespace': {'epoch': 1},
      'agents': [{'name': 'omp', 'provider': 'omp', 'omp_runtime': busy.toJson()}],
    }});
    SelectedAgentWorkspaceModel model() => selectedAgentWorkspaceModel(view: view,
      agent: view.agents.single, chatController: controller, isAwaitingAgentResponse: false);
    controller.setConversationError('omp', 'history page unavailable');
    expect(model().ompRuntime!.mainState, 'working');
    controller.setConversationError('omp', 'OMP Collab is unavailable');
    expect(model().ompRuntime!.mainState, 'offline');
    expect(model().ompRuntime!.statusBar!.elapsedMs, 1234);
  });
  test('progress arrays and text remain bounded without losing the latest tail', () {
    final value = OmpRuntime.fromJson({
      'connected': true, 'ready': true, 'progress_available': true,
      'activities': [for (var i = 0; i < 12; i++) {
        'id': 'tool:$i', 'kind': 'tool', 'status': 'running',
        'title': 'Read current intent', 'text': '${'x' * 800}tail',
        'tool_name': 'read', 'updated_at': '2026-10-23T00:00:00Z',
      }],
      'tasks': [{
        'id': 't', 'agent': 'Helper', 'status': 'running',
        'task': 'Inspect native runtime', 'intent': 'Read updates',
        'stage': 'Checking gateway projection', 'progress_text': '${'x' * 800}latest',
        'completion_percent': 37.5, 'tool_count': 2, 'requests': 3,
        'tokens': 400, 'context_tokens': 500, 'context_window': 1000,
      }],
    });
    expect(value.activities, hasLength(8));
    expect(value.activities.first.text!.length, 768);
    expect(value.activities.first.text, endsWith('tail'));
    final task = value.tasks.single;
    expect(task.progressText!.length, 768);
    expect(task.progressText, endsWith('latest'));
  });

  test('progress metrics are absent unless valid and actually supplied', () {
    final absent = OmpRuntimeTask.fromJson(const {});
    expect(absent.completionPercent, isNull);
    expect(absent.tokens, isNull);
    final invalid = OmpRuntimeTask.fromJson({
      'completion_percent': 101, 'tool_count': -1, 'requests': double.nan,
      'tokens': double.infinity, 'context_tokens': '123', 'context_window': -1,
    });
    expect(invalid.completionPercent, isNull);
    expect(invalid.toolCount, isNull);
    expect(invalid.requests, isNull);
    expect(invalid.tokens, isNull);
    expect(invalid.contextTokens, isNull);
    expect(invalid.contextWindow, isNull);
    expect(OmpRuntimeTask.fromJson({'completion_percent': 0}).completionPercent, 0);
  });

  test('runtime signatures react to current progress but ignore observation clock', () {
    String signature(Map<String, Object?> fields) => conversationSignature(conversation(
      OmpRuntime.fromJson({'connected': true, 'ready': true, ...fields})));
    final initial = signature({'progress_available': true, 'activities': [{
      'id': 'tool:one', 'kind': 'tool', 'status': 'running', 'title': 'Read', 'text': 'first',
    }]});
    expect(signature({'progress_available': true, 'activities': [{
      'id': 'tool:one', 'kind': 'tool', 'status': 'running', 'title': 'Read', 'text': 'second',
    }]}), isNot(initial));
    final task = <String, Object?>{'id': 't', 'status': 'running', 'stage': 'Reading'};
    final before = signature({'tasks': [task]});
    for (final change in <Map<String, Object?>>[
      {'stage': 'Writing'}, {'intent': 'Inspect files'}, {'progress_text': 'Found updates'},
      {'completion_percent': 50}, {'tokens': 20},
    ]) {
      expect(signature({'tasks': [{...task, ...change}]}), isNot(before));
    }
    expect(signature({'progress_available': false}), isNot(signature({'progress_available': true})));
  });

  test('native replies keep their own identity across consecutive running turns', () {
    final controller = AgentChatController();
    final nativeAgent = agent(runtime());
    final view = CcbProjectView.fromProjectViewPayload({'view': {
      'project': {'id': 'p', 'display_name': 'P'}, 'namespace': {'epoch': 1},
      'agents': [<String, Object?>{'name': 'omp', 'provider': 'omp'}],
    }});
    CcbConversationItem item(String turn, bool user, String status) =>
      CcbConversationItem.fromJson({
        'id': '$turn-${user ? 'user' : 'reply'}', 'agent': 'omp',
        'kind': user ? 'user_message' : 'agent_reply', 'body': turn,
        'source': 'provider_native/omp', 'session_id': 's',
        'native_turn': {'schema_version': 1, 'session_id': 's', 'turn_id': turn,
          'status': status, 'entries': <Object?>[]},
      });
    SelectedAgentWorkspaceModel update(List<CcbConversationItem> items, bool busy) {
      controller.applyRemoteConversation(agentName: 'omp', shouldScroll: false,
        conversation: CcbAgentConversation(projectId: 'p', agentName: 'omp', namespaceEpoch: 1,
          ompSessionId: 's', ompGeneration: 'g', branchId: 'b', items: items,
          ompRuntime: runtime(streaming: busy, second: 1)));
      return selectedAgentWorkspaceModel(view: view, agent: nativeAgent,
        chatController: controller, isAwaitingAgentResponse: false);
    }
    final firstItems = [item('first', true, 'running'), item('first', false, 'running')];
    final first = update(firstItems, true).timelineItems.last.id;
    final finishedItems = [item('first', true, 'completed'), item('first', false, 'completed')];
    expect(update(finishedItems, false).timelineItems.last.id, first);
    final second = update([...finishedItems,
      item('second', true, 'running'), item('second', false, 'running')], true);
    final replies = second.timelineItems.where((item) => item.kind == CcbConversationItemKind.agentReply).toList();
    expect(replies.first.id, first);
    expect(replies.last.id, isNot(first));
  });
  test('selected conversation status wins unless fresher agent observation supersedes it', () {
    final controller = AgentChatController();
    final idle = runtime(second: 1);
    controller.applyRemoteConversation(agentName: 'omp', conversation: conversation(idle), shouldScroll: false);
    CcbProjectView view(OmpRuntime value) => CcbProjectView.fromProjectViewPayload({'view': {
      'project': {'id': 'p', 'display_name': 'P'},
      'namespace': {'epoch': 1},
      'agents': [{'name': 'omp', 'provider': 'omp', 'omp_runtime': value.toJson()}],
    }});
    final olderBusyView = view(runtime(streaming: true));
    var model = selectedAgentWorkspaceModel(view: olderBusyView,
      agent: olderBusyView.agents.single, chatController: controller, isAwaitingAgentResponse: true);
    expect(model.executionStatus!.state, 'idle');
    expect(model.workingReplyItemId, isNull);
    final disconnectedView = view(runtime(connected: false, streaming: true, second: 2));
    model = selectedAgentWorkspaceModel(view: disconnectedView,
      agent: disconnectedView.agents.single, chatController: controller, isAwaitingAgentResponse: true);
    expect(model.executionStatus!.state, 'offline');
    expect(model.workingReplyItemId, isNull);
  });
  test('native status transitions override retained generic activity and local wait', () {
    final states = <OmpRuntime, String>{
      runtime(streaming: true): 'working', runtime(aborting: true): 'working',
      runtime(queue: 1): 'working', runtime(): 'idle',
      runtime(streaming: null): 'unknown', runtime(ready: false): 'unknown',
      runtime(connected: false, streaming: true, aborting: true, queue: 4): 'offline',
    };
    for (final entry in states.entries) {
      expect(agentExecutionStatus(agent: agent(entry.key),
        isAwaitingAgentResponse: true).state, entry.value);
      expect(agentActivityDisplay(agent(entry.key)).name, entry.value);
    }
  });


  test('latest runtime replaces and older pages cannot regress runtime', () {
    final controller = AgentChatController();
    final busy = runtime(streaming: true);
    final idle = runtime(second: 1);
    controller.applyRemoteConversation(agentName: 'omp', conversation: conversation(busy), shouldScroll: false);
    final update = controller.applyRemoteConversation(agentName: 'omp', conversation: conversation(idle), shouldScroll: false);
    expect(update.changed, isTrue);
    controller.prependRemoteConversationPage(agentName: 'omp', conversation: conversation(busy));
    expect(controller.remoteConversationFor('omp')!.ompRuntime, same(idle));
    expect(conversationSignature(conversation(idle)),
      conversationSignature(conversation(runtime(second: 2))));
  });
}
