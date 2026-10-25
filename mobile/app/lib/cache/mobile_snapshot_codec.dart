import '../models/ccb_agent.dart';
import '../models/ccb_project.dart';
import '../models/ccb_project_view.dart';

Map<String, Object?> projectsSnapshotPayload(List<CcbProject> projects) => {
  'projects': [for (final project in projects) _projectJson(project)],
};

List<CcbProject> projectsFromSnapshotPayload(Map<String, Object?> payload) {
  final raw = payload['projects'];
  if (raw is! Iterable) {
    return const [];
  }
  return [
    for (final item in raw)
      if (item is Map)
        CcbProject.fromJson({
          for (final entry in item.entries) entry.key.toString(): entry.value,
        }),
  ];
}

Map<String, Object?> projectViewSnapshotPayload(CcbProjectView view) => {
  if (view.generatedAt != null || view.sequence != null || view.ttlMs != null)
    'cache': {
      if (view.generatedAt != null)
        'generated_at': view.generatedAt!.toUtc().toIso8601String(),
      if (view.sequence != null) 'sequence': view.sequence,
      if (view.ttlMs != null) 'ttl_ms': view.ttlMs,
    },
  'view': {
    'project': _projectJson(view.project),
    'namespace': {
      if (view.namespaceEpoch != null) 'epoch': view.namespaceEpoch,
      if (view.activeWindow != null) 'active_window': view.activeWindow,
      if (view.activePaneId != null) 'active_pane_id': view.activePaneId,
    },
    'agents': [for (final agent in view.agents) _agentJson(agent)],
    'windows': [
      for (final window in view.windows)
        {
          'name': window.name,
          'label': window.label,
          'kind': window.kind,
          'order': window.order,
          'active': window.active,
          'agents': window.agents,
          if (window.tmuxWindowId != null)
            'tmux_window_id': window.tmuxWindowId,
          if (window.tmuxWindowIndex != null)
            'tmux_window_index': window.tmuxWindowIndex,
        },
    ],
    // Terminal history is intentionally not a default chat cache/source.
    'content': {'items': const []},
  },
};

CcbProjectView? projectViewFromSnapshotPayload(Map<String, Object?> payload) {
  try {
    return CcbProjectView.fromProjectViewPayload(payload);
  } catch (_) {
    return null;
  }
}

Map<String, Object?> _projectJson(CcbProject project) => {
  'id': project.id,
  'display_name': project.displayName,
  'root': project.root,
  'favorite': project.favorite,
  'health': project.health,
  'has_working_agents': project.hasWorkingAgents,
  'working_agent_count': project.workingAgentCount,
  if (project.lastOpenedAt != null)
    'last_opened_at': project.lastOpenedAt!.toUtc().toIso8601String(),
  if (project.lastActivityAt != null)
    'last_activity_at': project.lastActivityAt!.toUtc().toIso8601String(),
};

Map<String, Object?> _agentJson(CcbAgent agent) => {
  'name': agent.name,
  'provider': agent.provider,
  'window': agent.window,
  'order': agent.order,
  'active': agent.active,
  'queue_depth': agent.queueDepth,
  if (agent.paneId != null) 'pane_id': agent.paneId,
  if (agent.runtimeHealth != null) 'runtime_health': agent.runtimeHealth,
  if (agent.activityState != null) 'activity_state': agent.activityState,
  if (agent.activitySymbol != null) 'activity_symbol': agent.activitySymbol,
  if (agent.activityColor != null) 'activity_color': agent.activityColor,
  if (agent.activitySource != null) 'activity_source': agent.activitySource,
  if (agent.activityReason != null) 'activity_reason': agent.activityReason,
  if (agent.lastProgressAt != null) 'last_progress_at': agent.lastProgressAt,
  if (agent.ompRuntime != null) 'omp_runtime': agent.ompRuntime!.toJson(),
};
