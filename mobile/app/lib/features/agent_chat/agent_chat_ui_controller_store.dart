import 'dart:async';

import 'package:flutter/material.dart';

import '../../models/ccb_conversation_item.dart';
import 'conversation_timeline.dart';

typedef AgentChatAgentIsActive = bool Function(String agentName);

const initialTimelineScrollOffset = 1000000000.0;
const agentChatFollowLatestScrollDuration = Duration(milliseconds: 180);
const double agentChatLayoutCorrectionTolerance = 0.5;

class AgentChatUiControllerStore {
  final Map<String, TextEditingController> _draftControllers = {};
  final Map<String, FocusNode> _draftFocusNodes = {};
  final Map<String, List<CcbMessageAttachment>> _draftAttachments = {};
  final Map<String, _AgentChatTimelineScrollController> _scrollControllers = {};
  final Map<String, int> _timelineAutoFollowGenerations = {};
  final Map<String, int> _timelineAutoFollowRequestRevisions = {};
  final Map<String, _TimelineAutoFollowRequest> _timelineAutoFollowRequests =
      {};
  final Set<String> _timelineAutoFollowScheduledAgents = {};
  final Set<String> _timelineAutoFollowAnimatingAgents = {};

  TextEditingController draftController(String agentName) {
    return _draftControllers.putIfAbsent(agentName, TextEditingController.new);
  }

  FocusNode draftFocusNode(String agentName) {
    return _draftFocusNodes.putIfAbsent(agentName, FocusNode.new);
  }

  List<CcbMessageAttachment> draftAttachments(String agentName) {
    return _draftAttachments[agentName] ?? const [];
  }

  void addDraftAttachments(
    String agentName,
    List<CcbMessageAttachment> attachments,
  ) {
    if (attachments.isEmpty) {
      return;
    }
    final current = _draftAttachments[agentName] ?? [];
    _draftAttachments[agentName] = [...current, ...attachments];
  }

  void removeDraftAttachment(String agentName, String localId) {
    final current = _draftAttachments[agentName] ?? [];
    _draftAttachments[agentName] =
        current.where((item) => item.fileId != localId).toList();
  }

  void clearDraftAttachments(String agentName) {
    _draftAttachments.remove(agentName);
  }

  ScrollController timelineScrollController(String agentName) {
    return _scrollControllers.putIfAbsent(
      agentName,
      () => _AgentChatTimelineScrollController(
        initialScrollOffset: initialTimelineScrollOffset,
      ),
    );
  }

  void anchorTimelineToEndForNextLayout(String agentName) {
    final controller = _scrollControllers[agentName];
    if (controller == null ||
        !controller.hasClients ||
        controller.position.isScrollingNotifier.value) {
      return;
    }
    controller.anchorToEndForNextLayout();
  }

  void cancelTimelineEndAnchor(String agentName) {
    _scrollControllers[agentName]?.cancelEndAnchorForNextLayout();
  }

  bool isTimelineNearEnd(String agentName) {
    final controller = _scrollControllers[agentName];
    if (controller == null || !controller.hasClients) {
      return true;
    }
    return isScrollMetricsNearEnd(controller.position);
  }

  void cancelTimelineAutoFollow(String agentName) {
    _timelineAutoFollowGenerations[agentName] =
        (_timelineAutoFollowGenerations[agentName] ?? 0) + 1;
    _timelineAutoFollowRequests.remove(agentName);
  }

  void scrollTimelineToEnd(
    String agentName, {
    required AgentChatAgentIsActive isActive,
    String? targetItemId,
    int attempt = 0,
  }) {
    final requestRevision =
        (_timelineAutoFollowRequestRevisions[agentName] ?? 0) + 1;
    _timelineAutoFollowRequestRevisions[agentName] = requestRevision;
    _timelineAutoFollowRequests[agentName] = _TimelineAutoFollowRequest(
      revision: requestRevision,
      interactionGeneration: _timelineAutoFollowGenerations[agentName] ?? 0,
      isActive: isActive,
      attempt: attempt,
    );
    _scheduleTimelineAutoFollow(agentName);
  }

  void _scheduleTimelineAutoFollow(String agentName) {
    if (_timelineAutoFollowAnimatingAgents.contains(agentName) ||
        !_timelineAutoFollowScheduledAgents.add(agentName)) {
      return;
    }
    WidgetsBinding.instance.addPostFrameCallback((_) {
      _timelineAutoFollowScheduledAgents.remove(agentName);
      _flushTimelineAutoFollow(agentName);
    });
  }

  void _flushTimelineAutoFollow(String agentName) {
    if (_timelineAutoFollowAnimatingAgents.contains(agentName)) {
      return;
    }
    final request = _timelineAutoFollowRequests[agentName];
    if (request == null) {
      return;
    }
    if (!_isCurrentAutoFollowRequest(agentName, request)) {
      _timelineAutoFollowRequests.remove(agentName);
      return;
    }
    final controller = _scrollControllers[agentName];
    if (controller == null || !controller.hasClients) {
      if (request.attempt < 5) {
        _timelineAutoFollowRequests[agentName] = request.nextAttempt();
        _scheduleTimelineAutoFollow(agentName);
      }
      return;
    }
    final target = controller.position.maxScrollExtent;
    final current = controller.position.pixels;
    if ((target - current).abs() <= agentChatLayoutCorrectionTolerance) {
      _timelineAutoFollowRequests.remove(agentName);
      return;
    }
    _timelineAutoFollowAnimatingAgents.add(agentName);
    unawaited(
      _animateTimelineToEnd(
        agentName: agentName,
        controller: controller,
        request: request,
        target: target,
      ),
    );
  }

  Future<void> _animateTimelineToEnd({
    required String agentName,
    required ScrollController controller,
    required _TimelineAutoFollowRequest request,
    required double target,
  }) async {
    try {
      await controller.animateTo(
        target,
        duration: agentChatFollowLatestScrollDuration,
        curve: Curves.easeOutCubic,
      );
    } catch (_) {
      // Disposal or direct user interaction can cancel the animation.
    } finally {
      _timelineAutoFollowAnimatingAgents.remove(agentName);
    }
    final latest = _timelineAutoFollowRequests[agentName];
    if (latest == null) {
      return;
    }
    if (!_isCurrentAutoFollowRequest(agentName, latest)) {
      _timelineAutoFollowRequests.remove(agentName);
      return;
    }
    if (latest.revision != request.revision) {
      _scheduleTimelineAutoFollow(agentName);
      return;
    }
    final needsLayoutCorrection =
        controller.hasClients &&
        (controller.position.maxScrollExtent - controller.position.pixels)
                .abs() >
            agentChatLayoutCorrectionTolerance;
    if (needsLayoutCorrection && latest.attempt < 2) {
      _timelineAutoFollowRequests[agentName] = latest.nextAttempt();
      _scheduleTimelineAutoFollow(agentName);
      return;
    }
    _timelineAutoFollowRequests.remove(agentName);
  }

  bool _isCurrentAutoFollowRequest(
    String agentName,
    _TimelineAutoFollowRequest request,
  ) {
    return request.isActive(agentName) &&
        request.interactionGeneration ==
            (_timelineAutoFollowGenerations[agentName] ?? 0);
  }

  void dispose() {
    for (final controller in _draftControllers.values) {
      controller.dispose();
    }
    for (final node in _draftFocusNodes.values) {
      node.dispose();
    }
    for (final controller in _scrollControllers.values) {
      controller.dispose();
    }
  }
}

class _TimelineAutoFollowRequest {
  const _TimelineAutoFollowRequest({
    required this.revision,
    required this.interactionGeneration,
    required this.isActive,
    required this.attempt,
  });

  final int revision;
  final int interactionGeneration;
  final AgentChatAgentIsActive isActive;
  final int attempt;

  _TimelineAutoFollowRequest nextAttempt() {
    return _TimelineAutoFollowRequest(
      revision: revision,
      interactionGeneration: interactionGeneration,
      isActive: isActive,
      attempt: attempt + 1,
    );
  }
}

class _AgentChatTimelineScrollController extends ScrollController {
  _AgentChatTimelineScrollController({required super.initialScrollOffset});

  bool _anchorToEndForNextLayout = false;
  int _anchorGeneration = 0;

  void anchorToEndForNextLayout() {
    _anchorToEndForNextLayout = true;
    final generation = ++_anchorGeneration;
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_anchorGeneration == generation) {
        _anchorToEndForNextLayout = false;
      }
    });
  }

  bool get shouldAnchorToEndForNextLayout => _anchorToEndForNextLayout;

  void consumeEndAnchor() {
    _anchorToEndForNextLayout = false;
  }

  void cancelEndAnchorForNextLayout() {
    _anchorGeneration += 1;
    _anchorToEndForNextLayout = false;
  }

  @override
  ScrollPosition createScrollPosition(
    ScrollPhysics physics,
    ScrollContext context,
    ScrollPosition? oldPosition,
  ) {
    return _AgentChatTimelineScrollPosition(
      physics: physics,
      context: context,
      initialPixels: initialScrollOffset,
      keepScrollOffset: keepScrollOffset,
      oldPosition: oldPosition,
      debugLabel: debugLabel,
      shouldAnchorToEnd: () => shouldAnchorToEndForNextLayout,
      consumeEndAnchor: consumeEndAnchor,
    );
  }
}

class _AgentChatTimelineScrollPosition extends ScrollPositionWithSingleContext {
  _AgentChatTimelineScrollPosition({
    required super.physics,
    required super.context,
    required super.initialPixels,
    required super.keepScrollOffset,
    required super.oldPosition,
    required super.debugLabel,
    required bool Function() shouldAnchorToEnd,
    required VoidCallback consumeEndAnchor,
  }) : _shouldAnchorToEnd = shouldAnchorToEnd,
       _consumeEndAnchor = consumeEndAnchor;

  final bool Function() _shouldAnchorToEnd;
  final VoidCallback _consumeEndAnchor;

  @override
  bool correctForNewDimensions(
    ScrollMetrics oldPosition,
    ScrollMetrics newPosition,
  ) {
    if (!_shouldAnchorToEnd()) {
      return super.correctForNewDimensions(oldPosition, newPosition);
    }
    final target = newPosition.maxScrollExtent;
    if ((target - pixels).abs() > agentChatLayoutCorrectionTolerance) {
      correctPixels(target);
      return false;
    }
    _consumeEndAnchor();
    return true;
  }
}
