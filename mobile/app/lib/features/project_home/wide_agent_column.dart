import 'package:flutter/material.dart';

import '../agent_chat/agent_state_display.dart';
import '../../models/ccb_agent.dart';
import '../../models/ccb_project_view.dart';
import '../../widgets/working_status_style.dart';
import 'agent_window_switchers.dart';
import 'project_view_selection.dart';

class WideAgentColumn extends StatelessWidget {
  const WideAgentColumn({
    required this.view,
    required this.selectedAgentName,
    this.onShowProjects,
    this.unreadAgentNames = const {},
    required this.onAgentSelected,
    super.key,
  });

  final CcbProjectView view;
  final String? selectedAgentName;
  final VoidCallback? onShowProjects;
  final Set<String> unreadAgentNames;
  final ValueChanged<CcbAgent> onAgentSelected;

  @override
  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    final windows = orderedWindowsForView(view);
    return Padding(
      key: const ValueKey('agent-secondary-list'),
      padding: const EdgeInsets.fromLTRB(8, 8, 8, 8),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          SizedBox(
            height: 56,
            child: Row(
              children: [
                Expanded(child: Text('Agents', style: textTheme.titleMedium)),
                if (onShowProjects != null)
                  IconButton(
                    key: const ValueKey('wide-project-expand-action'),
                    tooltip: 'Show projects',
                    onPressed: onShowProjects,
                    icon: const Icon(Icons.view_sidebar),
                  ),
              ],
            ),
          ),
          Expanded(
            child: ListView(
              children: [
                for (final window in windows) ...[
                  Padding(
                    key: ValueKey('wide-window-group-${window.name}'),
                    padding: const EdgeInsets.fromLTRB(12, 12, 12, 4),
                    child: Text(
                      window.label,
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: textTheme.labelMedium?.copyWith(
                        color: Theme.of(context).colorScheme.onSurfaceVariant,
                      ),
                    ),
                  ),
                  for (final agent in agentsForWindow(view, window.name))
                    Padding(
                      padding: const EdgeInsets.only(bottom: 4),
                      child: _WideAgentTile(
                        agent: agent,
                        selected: agent.name == selectedAgentName,
                        unread: unreadAgentNames.contains(agent.name),
                        onTap: () {
                          onAgentSelected(agent);
                        },
                      ),
                    ),
                ],
              ],
            ),
          ),
        ],
      ),
    );
  }
}

class _WideAgentTile extends StatelessWidget {
  const _WideAgentTile({
    required this.agent,
    required this.selected,
    required this.unread,
    required this.onTap,
  });

  final CcbAgent agent;
  final bool selected;
  final bool unread;
  final VoidCallback onTap;

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;
    final display = agentActivityDisplay(agent);
    final stateColor = agentStateColor(display, colorScheme);
    final fill = agentStateFill(display, colorScheme);
    final leadingIcon =
        display == AgentActivityDisplay.idle && selected
            ? Icons.check_circle
            : display == AgentActivityDisplay.idle
            ? Icons.radio_button_unchecked
            : _wideStateIcon(display);
    return Tooltip(
      message: agent.name,
      child: ListTile(
        key: ValueKey('agent-${agent.name}'),
        tileColor: fill,
        selected: selected,
        // Selection changes the outline, never the activity background.
        selectedTileColor: fill,
        shape: RoundedRectangleBorder(
          side:
              selected
                  ? BorderSide(color: colorScheme.primary, width: 1.6)
                  : display == AgentActivityDisplay.idle ||
                      display == AgentActivityDisplay.unknown
                  ? BorderSide.none
                  : BorderSide(color: stateColor, width: 1.6),
          borderRadius: BorderRadius.circular(8),
        ),
        leading: TaskCompletionUnreadIcon(
          unreadKey: ValueKey('agent-unread-star-${agent.name}'),
          showUnread: unread,
          child: Icon(
            leadingIcon,
            size: display == AgentActivityDisplay.idle ? 20 : 16,
            color:
                display == AgentActivityDisplay.idle
                    ? (selected
                        ? colorScheme.primary
                        : colorScheme.onSurfaceVariant)
                    : stateColor,
          ),
        ),
        title: Text(
          agent.name,
          key: ValueKey('agent-label-${agent.name}'),
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
        ),
        trailing: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            AgentStateLabel(state: display),
            if (agent.queueDepth > 0)
              Badge(
                label: Text(agent.queueDepth.toString()),
                child: const SizedBox.square(dimension: 18),
              ),
          ],
        ),
        onTap: onTap,
      ),
    );
  }

  IconData _wideStateIcon(AgentActivityDisplay state) {
    switch (state) {
      case AgentActivityDisplay.working:
        return Icons.circle;
      case AgentActivityDisplay.exception:
        return Icons.error_outline;
      case AgentActivityDisplay.offline:
        return Icons.cloud_off_outlined;
      case AgentActivityDisplay.unknown:
        return Icons.help_outline;
      case AgentActivityDisplay.idle:
        return Icons.radio_button_unchecked;
    }
  }
}
