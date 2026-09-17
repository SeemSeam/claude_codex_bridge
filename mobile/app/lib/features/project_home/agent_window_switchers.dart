import 'dart:math' as math;

import 'package:flutter/material.dart';

import '../agent_chat/agent_state_display.dart';
import '../../l10n/ccb_mobile_localizations.dart';
import '../../models/ccb_agent.dart';
import '../../models/ccb_window.dart';
import '../../widgets/working_status_style.dart';

/// Vertically constrained contexts (keyboard open, short landscape) use
/// compact rows so the composer keeps room; everywhere else rows target the
/// 48 logical pixel interactive minimum. Rows always grow with text scale.
double _switcherRowHeight(BuildContext context, double scaledBase) {
  final mq = MediaQuery.of(context);
  final available = mq.size.height - mq.viewInsets.bottom - mq.padding.vertical;
  final minimum = available < 480 ? 40.0 : 48.0;
  return math.max(minimum, MediaQuery.textScalerOf(context).scale(scaledBase));
}

bool _compactSwitcherLayout(BuildContext context) {
  final mq = MediaQuery.of(context);
  final available = mq.size.height - mq.viewInsets.bottom - mq.padding.vertical;
  return available < 480;
}

String _stateLabel(CcbMobileLocalizations strings, AgentActivityDisplay state) {
  switch (state) {
    case AgentActivityDisplay.working:
      return strings.agentStateWorking;
    case AgentActivityDisplay.idle:
      return strings.agentStateIdle;
    case AgentActivityDisplay.exception:
      return strings.agentStateException;
    case AgentActivityDisplay.offline:
      return strings.agentStateOffline;
    case AgentActivityDisplay.unknown:
      return strings.agentStateUnknown;
  }
}

IconData _stateIcon(AgentActivityDisplay state) {
  switch (state) {
    case AgentActivityDisplay.working:
      return Icons.circle;
    case AgentActivityDisplay.idle:
      return Icons.radio_button_unchecked;
    case AgentActivityDisplay.exception:
      return Icons.error_outline;
    case AgentActivityDisplay.offline:
      return Icons.cloud_off_outlined;
    case AgentActivityDisplay.unknown:
      return Icons.help_outline;
  }
}

/// Colored, localized status suffix kept beside an ellipsized name. The name
/// flexes and truncates; this label keeps its own space and never ellipsizes.
class AgentStateLabel extends StatelessWidget {
  const AgentStateLabel({required this.state, super.key});

  final AgentActivityDisplay state;

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;
    final strings = CcbMobileLocalizations.of(context);
    final color = agentStateColor(state, colorScheme);
    return Semantics(
      container: true,
      label: _stateLabel(strings, state),
      child: Padding(
        padding: const EdgeInsets.only(left: 6),
        child: FittedBox(
          fit: BoxFit.scaleDown,
          child: ExcludeSemantics(
            child: Text(
              _stateLabel(strings, state),
              key: ValueKey('agent-state-label-${state.name}'),
              maxLines: 1,
              overflow: TextOverflow.visible,
              softWrap: false,
              style: Theme.of(
                context,
              ).textTheme.labelSmall?.copyWith(color: color),
            ),
          ),
        ),
      ),
    );
  }
}

class AgentSwitcher extends StatelessWidget {
  const AgentSwitcher({
    required this.agents,
    required this.selectedAgentName,
    required this.onAgentSelected,
    this.unreadAgentNames = const {},
    super.key,
  });

  final List<CcbAgent> agents;
  final String? selectedAgentName;
  final ValueChanged<CcbAgent> onAgentSelected;
  final Set<String> unreadAgentNames;

  @override
  Widget build(BuildContext context) {
    if (agents.isEmpty) {
      return const SizedBox.shrink();
    }
    final colorScheme = Theme.of(context).colorScheme;
    return SizedBox(
      key: const ValueKey('agent-switcher'),
      height: _switcherRowHeight(context, 36),
      child: ListView.separated(
        scrollDirection: Axis.horizontal,
        itemCount: agents.length,
        separatorBuilder: (context, index) => const SizedBox(width: 6),
        itemBuilder: (context, index) {
          final agent = agents[index];
          final selected = agent.name == selectedAgentName;
          final display = agentActivityDisplay(agent);
          final stateColor = agentStateColor(display, colorScheme);
          final fill = agentStateFill(display, colorScheme);
          final unread = unreadAgentNames.contains(agent.name);
          // Leading icon shows the state; for plain idle the selection check
          // or focus sparkle takes the slot. `active` is focus only.
          final IconData icon;
          if (display == AgentActivityDisplay.idle && selected) {
            icon = Icons.check_circle;
          } else if (display == AgentActivityDisplay.idle && agent.active) {
            icon = Icons.auto_awesome_outlined;
          } else {
            icon = _stateIcon(display);
          }
          final Color iconColor =
              display == AgentActivityDisplay.idle && selected
                  ? colorScheme.primary
                  : display == AgentActivityDisplay.idle
                  ? colorScheme.onSurfaceVariant
                  : stateColor;
          return Tooltip(
            message: agent.name,
            child: ChoiceChip(
              key: ValueKey('agent-${agent.name}'),
              selected: selected,
              showCheckmark: false,
              // Status fill survives selection: both chip colors carry the
              // state tint, and selection adds the primary border/check.
              backgroundColor: fill,
              selectedColor: fill,
              side:
                  selected
                      ? BorderSide(color: colorScheme.primary, width: 1.8)
                      : display == AgentActivityDisplay.idle ||
                          display == AgentActivityDisplay.unknown
                      ? BorderSide(color: colorScheme.outlineVariant)
                      : BorderSide(color: stateColor, width: 1.6),
              visualDensity: VisualDensity.compact,
              labelPadding: const EdgeInsets.symmetric(horizontal: 4),
              materialTapTargetSize:
                  _compactSwitcherLayout(context)
                      ? MaterialTapTargetSize.shrinkWrap
                      : MaterialTapTargetSize.padded,
              avatar: TaskCompletionUnreadIcon(
                unreadKey: ValueKey('agent-unread-star-${agent.name}'),
                showUnread: unread,
                child: Icon(icon, size: 14, color: iconColor),
              ),
              label: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 168),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Flexible(
                      child: ConstrainedBox(
                        constraints: const BoxConstraints(maxWidth: 108),
                        child: Text(
                          agent.name,
                          key: ValueKey('agent-label-${agent.name}'),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                    ),
                    AgentStateLabel(state: display),
                  ],
                ),
              ),
              onSelected: (_) {
                onAgentSelected(agent);
              },
            ),
          );
        },
      ),
    );
  }
}

class WindowSwitcher extends StatelessWidget {
  const WindowSwitcher({
    required this.windows,
    required this.selectedWindowName,
    required this.onWindowSelected,
    this.unreadWindowNames = const {},
    this.workingAgentCounts = const {},
    super.key,
  });

  final List<CcbWindow> windows;
  final String? selectedWindowName;
  final ValueChanged<String> onWindowSelected;
  final Set<String> unreadWindowNames;

  /// Working agent count per window name, covering nonselected windows too.
  final Map<String, int> workingAgentCounts;

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;
    final strings = CcbMobileLocalizations.of(context);
    final accent = workingStatusAccent(colorScheme);
    return SizedBox(
      key: const ValueKey('window-switcher'),
      height: _switcherRowHeight(context, 26),
      child: ListView.separated(
        scrollDirection: Axis.horizontal,
        itemCount: windows.length,
        separatorBuilder: (context, index) => const SizedBox(width: 6),
        itemBuilder: (context, index) {
          final window = windows[index];
          final selected = window.name == selectedWindowName;
          final unread = unreadWindowNames.contains(window.name);
          final workingCount = workingAgentCounts[window.name] ?? 0;
          final tooltip =
              workingCount > 0
                  ? '${window.label} · ${strings.agentWorkingCountLabel(workingCount)}'
                  : window.label;
          return Tooltip(
            message: tooltip,
            child: ChoiceChip(
              key: ValueKey('window-tab-${window.name}'),
              selected: selected,
              showCheckmark: false,
              visualDensity: VisualDensity.compact,
              materialTapTargetSize:
                  _compactSwitcherLayout(context)
                      ? MaterialTapTargetSize.shrinkWrap
                      : MaterialTapTargetSize.padded,
              side:
                  selected
                      ? BorderSide(color: colorScheme.primary, width: 1.8)
                      : workingCount > 0
                      ? BorderSide(color: accent, width: 1.6)
                      : BorderSide(color: colorScheme.outlineVariant),
              avatar: TaskCompletionUnreadIcon(
                unreadKey: ValueKey('window-unread-star-${window.name}'),
                showUnread: unread,
                child: Icon(
                  selected
                      ? Icons.space_dashboard_rounded
                      : Icons.space_dashboard_outlined,
                  size: selected ? 18 : 17,
                  color:
                      selected
                          ? colorScheme.primary
                          : workingCount > 0
                          ? accent
                          : colorScheme.onSurfaceVariant,
                ),
              ),
              // Name and working count are separate children so an arbitrary
              // long label ellipsizes while the count keeps its own space and
              // stays fully visible.
              label: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 200),
                child: Row(
                  mainAxisSize: MainAxisSize.min,
                  children: [
                    Flexible(
                      child: ConstrainedBox(
                        constraints: const BoxConstraints(maxWidth: 140),
                        child: Text(
                          window.label,
                          key: ValueKey('window-label-${window.name}'),
                          maxLines: 1,
                          overflow: TextOverflow.ellipsis,
                        ),
                      ),
                    ),
                    if (workingCount > 0)
                      Flexible(
                        child: Padding(
                          padding: const EdgeInsets.only(left: 4),
                          child: FittedBox(
                            fit: BoxFit.scaleDown,
                            child: Text(
                              strings.agentWorkingCountLabel(workingCount),
                              key: ValueKey(
                                'window-working-count-${window.name}',
                              ),
                              maxLines: 1,
                              overflow: TextOverflow.visible,
                              softWrap: false,
                              style: TextStyle(
                                color: accent,
                                fontWeight: FontWeight.w600,
                              ),
                            ),
                          ),
                        ),
                      ),
                  ],
                ),
              ),
              onSelected: (_) {
                if (!selected) {
                  onWindowSelected(window.name);
                }
              },
            ),
          );
        },
      ),
    );
  }
}

class TaskCompletionUnreadIcon extends StatelessWidget {
  const TaskCompletionUnreadIcon({
    required this.child,
    required this.showUnread,
    required this.unreadKey,
    super.key,
  });

  final Widget child;
  final bool showUnread;
  final Key unreadKey;

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;
    return SizedBox(
      width: 24,
      height: 24,
      child: Stack(
        clipBehavior: Clip.none,
        children: [
          Center(child: child),
          if (showUnread)
            Positioned(
              key: unreadKey,
              right: -1,
              top: -1,
              child: Semantics(
                container: true,
                label: CcbMobileLocalizations.of(context).unreadBadgeLabel,
                child: Icon(
                  Icons.star,
                  size: 11,
                  color: unreadBadgeColor(colorScheme),
                ),
              ),
            ),
        ],
      ),
    );
  }
}
