import '../../models/ccb_agent.dart';

/// Display-only activity state for list surfaces. It refines
/// [agentExecutionStatus] for presentation: `offline` is split out of the
/// legacy exception bucket and missing evidence reports `unknown` instead of
/// inheriting the classifier's historical Idle default. Working and failure
/// evidence itself is unchanged.
enum AgentActivityDisplay { working, idle, exception, offline, unknown }

/// Resolve the display state of [agent].
///
/// Precedence (documented; mirrors agent_execution_status where they overlap):
///
/// 1. Explicit offline evidence — activity state `offline`, or offline in the
///    source/reason text — shows offline, subdued, before other failures.
/// 2. Exception evidence — failed/failure/error/faulted/crashed states or
///    failed/error/auth/interrupt/cancel/abort/dead/timeout/denied text —
///    shows exception. A failure is never painted over by retained working
///    markers.
/// 3. Explicit idle overrides retained queue/progress hints.
/// 4. Working evidence — active/busy/pending/running/start/starting/working
///    states, a positive queue depth, or queued/reconnect/running/start/
///    submitted/tool/waiting/working/prompt text — shows working.
/// 5. Anything else — no activity evidence at all — shows unknown. The legacy
///    classifier defaults these to Idle; display must not invent idle.
///
/// `agent.active` is pane focus only and never maps to a state here.
AgentActivityDisplay agentActivityDisplay(CcbAgent agent) {
  final state = _normalized(agent.activityState);
  final text =
      '${_normalized(agent.activitySource) ?? ''} '
      '${_normalized(agent.activityReason) ?? ''}';
  if (state == 'offline' || text.contains('offline')) {
    return AgentActivityDisplay.offline;
  }
  if (_exceptionStates.contains(state) || _exceptionText.any(text.contains)) {
    return AgentActivityDisplay.exception;
  }
  if (_idleStates.contains(state)) {
    return AgentActivityDisplay.idle;
  }
  if (_workingStates.contains(state) ||
      agent.queueDepth > 0 ||
      _workingText.any(text.contains)) {
    return AgentActivityDisplay.working;
  }
  return AgentActivityDisplay.unknown;
}

const _exceptionStates = {
  'failed',
  'failure',
  'error',
  'faulted',
  'offline',
  'crashed',
};

const _exceptionText = [
  'failed',
  'failure',
  'error',
  'auth',
  'interrupt',
  'cancel',
  'abort',
  'dead',
  'timeout',
  'timed_out',
  'denied',
];

const _workingStates = {
  'active',
  'busy',
  'pending',
  'running',
  'start',
  'starting',
  'working',
};

const _workingText = [
  'queued',
  'reconnect',
  'running',
  'start',
  'submitted',
  'tool',
  'waiting',
  'working',
  'prompt',
];

const _idleStates = {'idle', 'free', 'completed', 'complete', 'done'};

String? _normalized(String? value) {
  final text = value?.trim().toLowerCase();
  return text == null || text.isEmpty ? null : text;
}
