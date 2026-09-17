import 'package:flutter/material.dart';

import '../agent_chat/agent_execution_status.dart';
import '../agent_chat/agent_state_display.dart';
import '../../models/ccb_agent.dart';
import '../../models/ccb_project_view.dart';
import '../../models/ccb_window.dart';
import '../../widgets/working_status_style.dart';
import 'agent_window_switchers.dart';
import 'working_status_summary.dart';
import 'project_view_selection.dart';

class MobileAgentSwitcherPanel extends StatelessWidget {
  const MobileAgentSwitcherPanel({
    required this.view,
    required this.selectedAgent,
    required this.collapsed,
    required this.onCollapse,
    required this.onExpand,
    required this.onWindowSelected,
    required this.onAgentSelected,
    this.unreadAgentNames = const {},
    super.key,
  });

  final CcbProjectView view;
  final CcbAgent? selectedAgent;
  final bool collapsed;
  final VoidCallback onCollapse;
  final VoidCallback onExpand;
  final ValueChanged<String> onWindowSelected;
  final ValueChanged<String> onAgentSelected;
  final Set<String> unreadAgentNames;

  @override
  Widget build(BuildContext context) {
    if (view.agents.isEmpty) {
      return const SizedBox.shrink();
    }
    final colorScheme = Theme.of(context).colorScheme;
    final accent = workingStatusAccent(colorScheme);
    final windows = orderedWindowsForView(view);
    final selectedWindow = selectedWindowForView(view, selectedAgent);
    final currentAgents =
        selectedWindow == null
            ? view.agents
            : agentsForWindow(view, selectedWindow.name);
    final agent = selectedAgent;
    final unreadWindowNames = _unreadWindowNames(
      view: view,
      selectedWindow: selectedWindow,
      unreadAgentNames: unreadAgentNames,
    );
    final hasUnread = unreadAgentNames.isNotEmpty;
    // Aggregates come from authoritative execution classification across all
    // windows, so a nonselected window's work stays visible.
    final workingAgentCounts = _workingAgentCounts(view, windows);
    final workingCount = workingAgentCountForView(view);
    final exceptionCount =
        view.agents
            .where(
              (a) => agentActivityDisplay(a) == AgentActivityDisplay.exception,
            )
            .length;
    if (collapsed) {
      return Material(
        key: const ValueKey('mobile-agent-switcher-collapsed'),
        color: Theme.of(context).colorScheme.surface,
        child: InkWell(
          onTap: onExpand,
          child: SizedBox(
            height: 48,
            child: Padding(
              padding: const EdgeInsets.symmetric(horizontal: 16),
              child: Row(
                children: [
                  TaskCompletionUnreadIcon(
                    unreadKey: const ValueKey(
                      'mobile-agent-switcher-unread-star',
                    ),
                    showUnread: hasUnread,
                    child: Icon(
                      Icons.auto_awesome_rounded,
                      size: 20,
                      color:
                          workingCount > 0
                              ? accent
                              : exceptionCount > 0
                              ? colorScheme.error
                              : colorScheme.primary,
                    ),
                  ),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Row(
                      children: [
                        Expanded(
                          child: Text(
                            key: const ValueKey(
                              'mobile-agent-switcher-summary',
                            ),
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: Theme.of(context).textTheme.titleSmall,
                            _mobileAgentSummary(
                              selectedWindow: selectedWindow,
                              selectedAgent: agent,
                              agentCount: view.agents.length,
                            ),
                          ),
                        ),
                        WorkingCountSummary(count: workingCount),
                      ],
                    ),
                  ),
                  IconButton(
                    key: const ValueKey('mobile-agent-switcher-expand-action'),
                    tooltip: 'Show agents',
                    visualDensity: VisualDensity.compact,
                    constraints: const BoxConstraints.tightFor(
                      width: 40,
                      height: 40,
                    ),
                    padding: EdgeInsets.zero,
                    onPressed: onExpand,
                    icon: const Icon(Icons.keyboard_arrow_down),
                  ),
                ],
              ),
            ),
          ),
        ),
      );
    }
    return Column(
      key: const ValueKey('mobile-agent-switcher-expanded'),
      children: [
        WindowSwitcher(
          windows: windows,
          selectedWindowName: selectedWindow?.name,
          unreadWindowNames: unreadWindowNames,
          workingAgentCounts: workingAgentCounts,
          onWindowSelected: onWindowSelected,
        ),
        const SizedBox(height: 2),
        AgentSwitcher(
          agents: currentAgents.isEmpty ? view.agents : currentAgents,
          selectedAgentName: selectedAgent?.name,
          unreadAgentNames: unreadAgentNames,
          onAgentSelected: (agent) {
            onAgentSelected(agent.name);
          },
        ),
        const SizedBox(height: 2),
        InkWell(
          key: const ValueKey('mobile-agent-switcher-collapse-action'),
          onTap: onCollapse,
          borderRadius: BorderRadius.circular(4),
          child: SizedBox(
            width: 72,
            height: 32,
            child: Center(
              child: Container(
                width: 36,
                height: 3,
                decoration: BoxDecoration(
                  color: Theme.of(context).colorScheme.outlineVariant,
                  borderRadius: BorderRadius.circular(2),
                ),
              ),
            ),
          ),
        ),
      ],
    );
  }

  String _mobileAgentSummary({
    required CcbWindow? selectedWindow,
    required CcbAgent? selectedAgent,
    required int agentCount,
  }) {
    final agent = selectedAgent;
    if (agent == null) {
      return '$agentCount agents';
    }
    final window = selectedWindow;
    if (window == null) {
      return agent.name;
    }
    return '${window.label} / ${agent.name}';
  }
}

Map<String, int> _workingAgentCounts(
  CcbProjectView view,
  List<CcbWindow> windows,
) {
  return {
    for (final window in windows)
      window.name:
          agentsForWindow(
            view,
            window.name,
          ).where(agentHasSourceWorkingActivity).length,
  };
}

Set<String> _unreadWindowNames({
  required CcbProjectView view,
  required CcbWindow? selectedWindow,
  required Set<String> unreadAgentNames,
}) {
  if (unreadAgentNames.isEmpty) {
    return const {};
  }
  final selectedName = selectedWindow?.name;
  final unread = <String>{};
  for (final window in orderedWindowsForView(view)) {
    if (window.name == selectedName) {
      continue;
    }
    // Membership comes from agentsForWindow so agents that only declare
    // agent.window still map onto their window (window.agents can be empty).
    for (final member in agentsForWindow(view, window.name)) {
      if (unreadAgentNames.contains(member.name)) {
        unread.add(window.name);
        break;
      }
    }
  }
  return unread;
}
