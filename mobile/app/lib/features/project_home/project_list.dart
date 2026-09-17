import 'package:flutter/material.dart';

import '../agent_chat/agent_execution_status.dart';
import '../../app/chat_background.dart';
import '../../l10n/ccb_mobile_localizations.dart';
import '../../models/ccb_agent.dart';
import '../../models/ccb_project_view.dart';
import '../../widgets/working_status_style.dart';

class ProjectListScaffold extends StatelessWidget {
  const ProjectListScaffold({
    required this.view,
    required this.selectedAgent,
    required this.onOpenProject,
    required this.onOpenNotifications,
    required this.onOpenConnectionDetails,
    this.hasUnreadTaskCompletion = false,
    this.hasWorkingAgents = false,
    super.key,
  });

  final CcbProjectView view;
  final CcbAgent? selectedAgent;
  final VoidCallback onOpenProject;
  final VoidCallback onOpenNotifications;
  final VoidCallback onOpenConnectionDetails;
  final bool hasUnreadTaskCompletion;
  final bool hasWorkingAgents;

  @override
  Widget build(BuildContext context) {
    final strings = CcbMobileLocalizations.of(context);
    final hasBackground = ccbWorkspaceBackgroundEnabled(context);
    final scaffold = Scaffold(
      backgroundColor: hasBackground ? Colors.transparent : null,
      body: SafeArea(
        child: Padding(
          key: const ValueKey('project-list-screen'),
          padding: const EdgeInsets.fromLTRB(12, 4, 12, 8),
          child: Column(
            children: [
              Align(
                alignment: Alignment.centerRight,
                child: Wrap(
                  spacing: 2,
                  children: [
                    IconButton(
                      key: const ValueKey('notification-center-action'),
                      tooltip: strings.notifications,
                      onPressed: onOpenNotifications,
                      icon: Icon(
                        view.notifications.isEmpty
                            ? Icons.notifications_none
                            : Icons.notifications_active,
                      ),
                    ),
                    IconButton(
                      key: const ValueKey('connection-details-action'),
                      tooltip: strings.diagnostics,
                      onPressed: onOpenConnectionDetails,
                      icon: const Icon(Icons.more_horiz),
                    ),
                  ],
                ),
              ),
              Expanded(
                child: ListView.separated(
                  key: const ValueKey('project-list'),
                  itemCount: 1,
                  separatorBuilder:
                      (context, index) => const Divider(height: 1),
                  itemBuilder: (context, index) {
                    return ProjectListTile(
                      view: view,
                      selectedAgent: selectedAgent,
                      selected: false,
                      hasUnreadTaskCompletion: hasUnreadTaskCompletion,
                      hasWorkingAgents: hasWorkingAgents,
                      onOpen: onOpenProject,
                    );
                  },
                ),
              ),
            ],
          ),
        ),
      ),
    );
    return CcbWorkspaceBackground(child: scaffold);
  }
}

class ProjectListTile extends StatelessWidget {
  const ProjectListTile({
    required this.view,
    required this.selectedAgent,
    required this.selected,
    required this.onOpen,
    this.hasUnreadTaskCompletion = false,
    this.hasWorkingAgents = false,
    super.key,
  });

  final CcbProjectView view;
  final CcbAgent? selectedAgent;
  final bool selected;
  final VoidCallback onOpen;
  final bool hasUnreadTaskCompletion;
  final bool hasWorkingAgents;

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    final strings = CcbMobileLocalizations.of(context);
    final activeAgent = selectedAgent?.name ?? strings.noAgent;
    final activeWindow = view.activeWindow ?? selectedAgent?.window ?? 'main';
    final root = view.project.root.trim();
    final workingCount =
        view.agents.where(agentHasSourceWorkingActivity).length;
    final accent = workingStatusAccent(Theme.of(context).colorScheme);
    return ProjectWorkingRowHighlight(
      projectId: view.project.id,
      hasWorkingAgents: hasWorkingAgents,
      child: ListTile(
        key: const ValueKey('project-open-current'),
        contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 6),
        selected: selected,
        selectedTileColor: Theme.of(context).colorScheme.secondaryContainer,
        leading: ProjectAttentionAvatar(
          projectId: view.project.id,
          favorite: view.project.favorite,
          hasUnreadTaskCompletion: hasUnreadTaskCompletion,
          hasWorkingAgents: hasWorkingAgents,
        ),
        title: Text(
          view.project.displayName,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: textTheme.titleMedium,
        ),
        subtitle: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (root.isNotEmpty)
              Text(
                root,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: textTheme.bodySmall?.copyWith(
                  color: Theme.of(context).colorScheme.onSurfaceVariant,
                ),
              ),
            const SizedBox(height: 4),
            Text(
              'cmd $activeWindow · $activeAgent',
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: textTheme.bodySmall?.copyWith(
                color: Theme.of(context).colorScheme.onSurfaceVariant,
              ),
            ),
          ],
        ),
        trailing: Row(
          mainAxisSize: MainAxisSize.min,
          children: [
            if (workingCount > 0) ...[
              Icon(Icons.circle, size: 10, color: accent),
              const SizedBox(width: 4),
              FittedBox(
                fit: BoxFit.scaleDown,
                child: Text(
                  strings.agentWorkingCountLabel(workingCount),
                  key: ValueKey('project-working-count-${view.project.id}'),
                  maxLines: 1,
                  overflow: TextOverflow.visible,
                  softWrap: false,
                  style: textTheme.bodySmall?.copyWith(
                    color: accent,
                    fontWeight: FontWeight.w600,
                  ),
                ),
              ),
            ] else
              Text('${view.agents.length}'),
            const SizedBox(width: 6),
            const Icon(Icons.chevron_right),
          ],
        ),
        onTap: onOpen,
      ),
    );
  }
}

class ProjectWorkingRowHighlight extends StatefulWidget {
  const ProjectWorkingRowHighlight({
    required this.projectId,
    required this.hasWorkingAgents,
    required this.child,
    super.key,
  });

  final String projectId;
  final bool hasWorkingAgents;
  final Widget child;

  @override
  State<ProjectWorkingRowHighlight> createState() =>
      _ProjectWorkingRowHighlightState();
}

class _ProjectWorkingRowHighlightState
    extends State<ProjectWorkingRowHighlight> {
  @override
  Widget build(BuildContext context) {
    if (!widget.hasWorkingAgents) {
      return widget.child;
    }
    final colorScheme = Theme.of(context).colorScheme;
    final tint = ccbWorkspaceSurfaceColor(
      context,
      projectWorkingRowTint(colorScheme),
    );
    return Semantics(
      key: ValueKey('project-working-row-${widget.projectId}'),
      container: true,
      hint: 'Project has working agents',
      child: DecoratedBox(
        decoration: BoxDecoration(
          color: tint,
          borderRadius: BorderRadius.circular(10),
          border: Border.all(
            color: projectWorkingRowBorder(
              projectWorkingRowAccent(colorScheme),
            ),
            width: 2.2,
          ),
        ),
        child: Material(
          color: Colors.transparent,
          clipBehavior: Clip.antiAlias,
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(8)),
          child: widget.child,
        ),
      ),
    );
  }
}

@visibleForTesting
Color projectWorkingRowTint(ColorScheme colorScheme) {
  final accent = projectWorkingRowAccent(colorScheme);
  return colorScheme.brightness == Brightness.dark
      ? Color.alphaBlend(
        accent.withValues(alpha: 0.28),
        colorScheme.surfaceContainerHighest,
      )
      : Color.alphaBlend(
        accent.withValues(alpha: 0.22),
        colorScheme.surfaceContainerLowest,
      );
}

@visibleForTesting
Color projectWorkingRowAccent(ColorScheme colorScheme) {
  return workingStatusAccent(colorScheme);
}

@visibleForTesting
Color projectWorkingRowBorder(Color accent) {
  return accent.withValues(alpha: 0.96);
}

class ProjectAttentionAvatar extends StatelessWidget {
  const ProjectAttentionAvatar({
    required this.projectId,
    required this.favorite,
    required this.hasUnreadTaskCompletion,
    required this.hasWorkingAgents,
    super.key,
  });

  final String projectId;
  final bool favorite;
  final bool hasUnreadTaskCompletion;
  final bool hasWorkingAgents;

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;
    return SizedBox(
      width: 52,
      height: 52,
      child: Stack(
        clipBehavior: Clip.none,
        children: [
          Center(
            child: CircleAvatar(
              radius: 22,
              backgroundColor:
                  hasWorkingAgents
                      ? workingStatusAccent(colorScheme).withValues(alpha: 0.16)
                      : null,
              foregroundColor:
                  hasWorkingAgents ? workingStatusAccent(colorScheme) : null,
              child: Icon(favorite ? Icons.star : Icons.terminal),
            ),
          ),
          if (hasUnreadTaskCompletion)
            Positioned(
              key: ValueKey('project-unread-star-$projectId'),
              right: 0,
              top: 0,
              child: Icon(Icons.star, size: 15, color: colorScheme.error),
            ),
        ],
      ),
    );
  }
}
