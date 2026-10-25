import '../../models/omp_runtime.dart';
import '../../models/ccb_agent.dart';
bool isOmpHostUnavailable(String? error) => error != null && (
    error.contains('OMP Collab snapshot not ready') ||
    error.contains('OMP Collab snapshot is not ready') ||
    error.contains('OMP Collab is unavailable') ||
    error.contains('host_disconnected') || error.contains('host_not_ready'));


class AgentExecutionStatus {
  const AgentExecutionStatus({
    required this.label,
    required this.state,
    required this.isRefreshing,
  });

  final String label;
  final String state;
  final bool isRefreshing;
}

AgentExecutionStatus agentExecutionStatus({
  required CcbAgent agent,
  required bool isAwaitingAgentResponse,
  bool hasLocalExecutionException = false,
  OmpRuntime? ompRuntime,
}) {
  final runtime = ompRuntime ?? agent.ompRuntime;
  if (runtime != null) {
    final state = runtime.mainState;
    return AgentExecutionStatus(
      label: switch (state) {
        'working' => 'Working', 'idle' => 'Idle',
        'offline' => 'Offline', _ => 'Unknown',
      },
      state: state,
      isRefreshing: false,
    );
  }
  final state = _normalized(agent.activityState);
  final source = _normalized(agent.activitySource);
  final reason = _normalized(agent.activityReason);
  // A failure must never be painted over by a retained working marker.
  if (_isExceptionActivity(state: state, source: source, reason: reason) ||
      hasLocalExecutionException) {
    return const AgentExecutionStatus(
      label: 'Exception',
      state: 'exception',
      isRefreshing: false,
    );
  }
  if (_isWorkingActivity(
    state: state,
    source: source,
    reason: reason,
    queueDepth: agent.queueDepth,
  )) {
    return AgentExecutionStatus(
      label: 'Working',
      state: 'working',
      isRefreshing: state == 'pending',
    );
  }
  if (isAwaitingAgentResponse) {
    return const AgentExecutionStatus(
      label: 'Working',
      state: 'working',
      isRefreshing: false,
    );
  }
  if (_isIdleActivity(state)) {
    return const AgentExecutionStatus(
      label: 'Idle',
      state: 'idle',
      isRefreshing: false,
    );
  }
  return const AgentExecutionStatus(
    label: 'Idle',
    state: 'idle',
    isRefreshing: false,
  );
}

bool agentHasSourceWorkingActivity(CcbAgent agent) {
  final status = agentExecutionStatus(
    agent: agent,
    isAwaitingAgentResponse: false,
  );
  return status.state == 'working';
}

bool _isIdleActivity(String? state) {
  return const {
    'idle',
    'free',
    'completed',
    'complete',
    'done',
  }.contains(state);
}

bool _isExceptionActivity({
  required String? state,
  required String? source,
  required String? reason,
}) {
  if (const {
    'failed',
    'failure',
    'error',
    'faulted',
    'offline',
    'crashed',
  }.contains(state)) {
    return true;
  }
  final text = '${source ?? ''} ${reason ?? ''}';
  return text.contains('failed') ||
      text.contains('failure') ||
      text.contains('error') ||
      text.contains('offline') ||
      text.contains('auth') ||
      text.contains('interrupt') ||
      text.contains('cancel') ||
      text.contains('abort') ||
      text.contains('dead') ||
      text.contains('timeout') ||
      text.contains('timed_out') ||
      text.contains('denied');
}

bool _isWorkingActivity({
  required String? state,
  required String? source,
  required String? reason,
  required int queueDepth,
}) {
  if (const {
    'active',
    'busy',
    'pending',
    'running',
    'start',
    'starting',
    'working',
  }.contains(state)) {
    return true;
  }
  if (state != null &&
      (_isIdleActivity(state) ||
          const {
            'failed',
            'failure',
            'error',
            'faulted',
            'offline',
            'crashed',
          }.contains(state))) {
    return false;
  }
  final text = '${source ?? ''} ${reason ?? ''}';
  return queueDepth > 0 ||
      text.contains('queued') ||
      text.contains('reconnect') ||
      text.contains('running') ||
      text.contains('start') ||
      text.contains('submitted') ||
      text.contains('tool') ||
      text.contains('waiting') ||
      text.contains('working') ||
      text.contains('prompt');
}

String? _normalized(String? value) {
  final text = value?.trim().toLowerCase();
  return text == null || text.isEmpty ? null : text;
}
