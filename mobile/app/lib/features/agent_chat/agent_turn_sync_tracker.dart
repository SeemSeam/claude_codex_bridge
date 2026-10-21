import 'dart:convert';

import '../../models/ccb_agent.dart';
import '../../models/ccb_agent_conversation.dart';
import '../../models/ccb_conversation_item.dart';
import '../../models/ccb_project_view.dart';
import '../../native_session/native_session.dart';

const agentTurnInitialReconciliationDelay = Duration(seconds: 3);
const agentTurnStartObservationTimeout = Duration(seconds: 15);

enum AgentTurnSyncResult { pending, settledIdle, completed, exception }

class AgentTurnSyncDecision {
  const AgentTurnSyncDecision({
    required this.result,
    required this.hadPendingTurn,
    required this.observedSourceWorking,
  });

  final AgentTurnSyncResult result;
  final bool hadPendingTurn;
  final bool observedSourceWorking;

  bool get settled => result != AgentTurnSyncResult.pending;
  bool get isCompleted => result == AgentTurnSyncResult.completed;
}

class AgentTurnSyncTracker {
  final Map<String, _PendingAgentTurn> _pendingTurns = {};
  final Set<String> _sourceWorkingAgents = {};

  bool isAwaiting(String agentName) => _pendingTurns.containsKey(agentName);

  bool hasObservedSourceWorking(String agentName) =>
      _pendingTurns[agentName]?.observedSourceWorking ?? false;

  void markSubmitted({
    required String agentName,
    required CcbAgentConversation? conversation,
  }) {
    _pendingTurns[agentName] = _PendingAgentTurn(
      replyBaseline: _AgentReplySnapshot.fromConversation(conversation),
    );
  }

  void clear(String agentName) {
    _pendingTurns.remove(agentName);
    _sourceWorkingAgents.remove(agentName);
  }

  AgentTurnSyncDecision observe({
    required String agentName,
    required String sourceState,
    required CcbAgentConversation? conversation,
    required CcbProjectView view,
    required CcbAgent agent,
    required bool conversationReconciled,
  }) {
    final normalizedState = sourceState.trim().toLowerCase();
    final pendingTurn = _pendingTurns[agentName];
    if (normalizedState == 'working') {
      _sourceWorkingAgents.add(agentName);
      pendingTurn?.observedSourceWorking = true;
      return AgentTurnSyncDecision(
        result: AgentTurnSyncResult.pending,
        hadPendingTurn: pendingTurn != null,
        observedSourceWorking:
            pendingTurn?.observedSourceWorking ??
            _sourceWorkingAgents.contains(agentName),
      );
    }

    if (normalizedState == 'exception') {
      final observedWorking =
          pendingTurn?.observedSourceWorking ??
          _sourceWorkingAgents.contains(agentName);
      clear(agentName);
      return AgentTurnSyncDecision(
        result: AgentTurnSyncResult.exception,
        hadPendingTurn: pendingTurn != null,
        observedSourceWorking: observedWorking,
      );
    }

    if (normalizedState != 'idle') {
      return AgentTurnSyncDecision(
        result: AgentTurnSyncResult.pending,
        hadPendingTurn: pendingTurn != null,
        observedSourceWorking:
            pendingTurn?.observedSourceWorking ??
            _sourceWorkingAgents.contains(agentName),
      );
    }

    final sourceWorking = _sourceWorkingAgents.remove(agentName);
    if (pendingTurn == null) {
      return AgentTurnSyncDecision(
        result: AgentTurnSyncResult.settledIdle,
        hadPendingTurn: false,
        observedSourceWorking: sourceWorking,
      );
    }
    pendingTurn.observedSourceWorking =
        pendingTurn.observedSourceWorking || sourceWorking;

    final replyProgress = _AgentReplySnapshot.fromConversation(
      conversation,
    ).progressSince(pendingTurn.replyBaseline);

    // A live native reply is direct provider progress even if a pane snapshot
    // momentarily reports the idle prompt while the terminal is repainting.
    if (replyProgress.hasRunningReply) {
      pendingTurn.observedSourceWorking = true;
      _sourceWorkingAgents.add(agentName);
      return const AgentTurnSyncDecision(
        result: AgentTurnSyncResult.pending,
        hadPendingTurn: true,
        observedSourceWorking: true,
      );
    }

    // A newer idle view is not correlated to this submission. It can be the
    // stale pre-start view returned while the pane is accepting the prompt.
    // Settle only after this turn observed source Working, or after the native
    // conversation proves that a completed reply advanced beyond baseline.
    if (conversationReconciled && pendingTurn.observedSourceWorking) {
      clear(agentName);
      return AgentTurnSyncDecision(
        result: AgentTurnSyncResult.completed,
        hadPendingTurn: true,
        observedSourceWorking: pendingTurn.observedSourceWorking,
      );
    }
    if (replyProgress.hasReplyProgress && !replyProgress.hasRunningReply) {
      clear(agentName);
      return AgentTurnSyncDecision(
        result:
            pendingTurn.observedSourceWorking
                ? AgentTurnSyncResult.completed
                : AgentTurnSyncResult.settledIdle,
        hadPendingTurn: true,
        observedSourceWorking: pendingTurn.observedSourceWorking,
      );
    }
    return AgentTurnSyncDecision(
      result: AgentTurnSyncResult.pending,
      hadPendingTurn: true,
      observedSourceWorking: pendingTurn.observedSourceWorking,
    );
  }
}

class AgentViewWatermark {
  const AgentViewWatermark({this.generatedAt, this.sequence});

  factory AgentViewWatermark.fromView(CcbProjectView view) {
    return AgentViewWatermark(
      generatedAt: view.generatedAt,
      sequence: view.sequence,
    );
  }

  final DateTime? generatedAt;
  final int? sequence;

  bool get hasValue => generatedAt != null || sequence != null;

  bool isNewerThan(AgentViewWatermark other) {
    final currentTime = generatedAt;
    final previousTime = other.generatedAt;
    if (currentTime != null && previousTime != null) {
      return currentTime.isAfter(previousTime);
    }
    final currentSequence = sequence;
    final previousSequence = other.sequence;
    if (currentSequence != null && previousSequence != null) {
      return currentSequence > previousSequence;
    }
    return false;
  }
}

String agentActivitySignature(CcbAgent? agent) {
  if (agent == null) {
    return '';
  }
  return [
    agent.name,
    agent.active,
    agent.queueDepth,
    agent.runtimeHealth,
    agent.activityState,
    agent.activitySource,
    agent.activityReason,
    agent.activitySymbol,
    agent.activityColor,
    agent.lastProgressAt,
  ].join('|');
}

class _PendingAgentTurn {
  _PendingAgentTurn({required this.replyBaseline});

  final _AgentReplySnapshot replyBaseline;
  bool observedSourceWorking = false;
}

class _AgentReplySnapshot {
  const _AgentReplySnapshot({
    required this.replyCount,
    required this.latestReplySignature,
    required this.latestReplyRunning,
  });

  factory _AgentReplySnapshot.fromConversation(
    CcbAgentConversation? conversation,
  ) {
    var replyCount = 0;
    CcbConversationItem? latestReply;
    for (final item in conversation?.items ?? const <CcbConversationItem>[]) {
      if (item.kind != CcbConversationItemKind.agentReply) {
        continue;
      }
      replyCount += 1;
      latestReply = item;
    }
    return _AgentReplySnapshot(
      replyCount: replyCount,
      latestReplySignature:
          latestReply == null ? null : _replySignature(latestReply),
      latestReplyRunning:
          latestReply != null && latestReply.nativeTurn != null
              ? {
                NativeSessionStatus.running,
                NativeSessionStatus.unknown,
              }.contains(latestReply.nativeTurn!.effectiveStatus)
              : latestReply != null && latestReply.completedAt == null,
    );
  }

  final int replyCount;
  final String? latestReplySignature;
  final bool latestReplyRunning;

  _AgentReplyProgress progressSince(_AgentReplySnapshot baseline) {
    final hasReplyProgress =
        replyCount > baseline.replyCount ||
        (latestReplySignature != null &&
            latestReplySignature != baseline.latestReplySignature);
    return _AgentReplyProgress(
      hasReplyProgress: hasReplyProgress,
      hasRunningReply: hasReplyProgress && latestReplyRunning,
    );
  }
}

class _AgentReplyProgress {
  const _AgentReplyProgress({
    required this.hasReplyProgress,
    required this.hasRunningReply,
  });

  final bool hasReplyProgress;
  final bool hasRunningReply;
}

String _replySignature(CcbConversationItem item) {
  return [
    item.id,
    item.startedAt?.microsecondsSinceEpoch.toString() ?? '',
    item.completedAt?.microsecondsSinceEpoch.toString() ?? '',
    item.body,
    item.attachments.length.toString(),
    if (item.nativeTurn != null) jsonEncode(item.nativeTurn!.toJson()),
  ].join('|');
}
