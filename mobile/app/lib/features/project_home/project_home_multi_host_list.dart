import 'package:flutter/material.dart';

import '../../app/chat_background.dart';
import '../../l10n/ccb_mobile_localizations.dart';
import '../../pairing/gateway_pairing.dart';
import 'project_home_gateway_profiles.dart';
import 'project_home_multi_host_projects.dart';
import 'project_list.dart';

/// Aggregated project list across every paired computer, grouped into one
/// section per computer. Each section header names its owning host, so projects
/// that share an id across computers stay distinguishable.
class ProjectHomeMultiHostProjectListHost extends StatelessWidget {
  const ProjectHomeMultiHostProjectListHost({
    required this.result,
    required this.onRefreshProjects,
    required this.onOpenTerminal,
    required this.onOpenSettings,
    required this.onOpenProject,
    required this.onRenameHost,
    this.customHostNames = const {},
    this.unreadProjectIds = const {},
    this.workingProjectIds = const {},
    super.key,
  });

  final ProjectHomeMultiHostProjectsResult result;
  final VoidCallback onRefreshProjects;
  final VoidCallback onOpenTerminal;
  final VoidCallback onOpenSettings;
  final ValueChanged<ProjectHomeHostProject> onOpenProject;

  /// Opens the rename flow of one computer, so a group header can carry a name
  /// the user recognises instead of the identifier the pairing carried.
  final ValueChanged<GatewayPairedHost> onRenameHost;

  /// Names the user chose for paired computers, keyed by
  /// [projectHomeCustomHostNameKey].
  final Map<String, String> customHostNames;
  final Set<String> unreadProjectIds;
  final Set<String> workingProjectIds;

  @override
  Widget build(BuildContext context) {
    final strings = CcbMobileLocalizations.of(context);
    final hasBackground = ccbWorkspaceBackgroundEnabled(context);
    final scaffold = Scaffold(
      backgroundColor: hasBackground ? Colors.transparent : null,
      body: SafeArea(
        child: Padding(
          key: const ValueKey('multi-host-project-list-screen'),
          padding: const EdgeInsets.fromLTRB(12, 4, 12, 8),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.stretch,
            children: [
              Row(
                children: [
                  Expanded(
                    child: Padding(
                      padding: const EdgeInsets.only(left: 4),
                      child: Row(
                        children: [
                          Flexible(
                            child: Text(
                              strings.hostsOnline(
                                result.onlineHostCount,
                                result.hostCount,
                              ),
                              key: const ValueKey(
                                'multi-host-project-list-summary',
                              ),
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: Theme.of(
                                context,
                              ).textTheme.bodySmall?.copyWith(
                                color:
                                    Theme.of(
                                      context,
                                    ).colorScheme.onSurfaceVariant,
                              ),
                            ),
                          ),
                          // A host that has not answered yet is not counted as
                          // online, so the count is marked as still settling.
                          if (result.catalogs.any((catalog) => catalog.pending))
                            const Padding(
                              key: ValueKey(
                                'multi-host-project-list-progress',
                              ),
                              padding: EdgeInsets.only(left: 8),
                              child: SizedBox(
                                width: 12,
                                height: 12,
                                child: CircularProgressIndicator(
                                  strokeWidth: 2,
                                ),
                              ),
                            ),
                        ],
                      ),
                    ),
                  ),
                  IconButton(
                    key: const ValueKey('project-list-refresh-action'),
                    tooltip: strings.refreshProjects,
                    onPressed: onRefreshProjects,
                    icon: const Icon(Icons.refresh),
                  ),
                  IconButton(
                    key: const ValueKey('project-list-terminal-action'),
                    tooltip: strings.openTerminal,
                    onPressed: onOpenTerminal,
                    icon: const Icon(Icons.terminal),
                  ),
                  IconButton(
                    key: const ValueKey('project-list-settings-action'),
                    tooltip: strings.settings,
                    onPressed: onOpenSettings,
                    icon: const Icon(Icons.settings_outlined),
                  ),
                ],
              ),
              Expanded(child: _buildBody(strings)),
            ],
          ),
        ),
      ),
    );
    return CcbWorkspaceBackground(child: scaffold);
  }

  /// Renders one section per paired computer: a host header followed by that
  /// computer's project rows. A host that owns no project, is still connecting,
  /// or failed to answer keeps its header and shows a placeholder row, so every
  /// paired computer stays visible instead of silently disappearing.
  Widget _buildBody(CcbMobileLocalizations strings) {
    final pending = result.catalogs.any((catalog) => catalog.pending);
    if (result.groups.isEmpty) {
      return Center(
        key: const ValueKey('multi-host-project-list-empty'),
        child:
            pending
                ? const CircularProgressIndicator()
                : Text(strings.noCcbProjectsFound),
      );
    }
    final rows = _buildRows();
    return ListView.separated(
      key: const ValueKey('multi-host-project-list'),
      itemCount: rows.length,
      // Project rows inside one computer stay divided, while a new host section
      // gets plain spacing so its header reads as the start of a group.
      separatorBuilder: (context, index) {
        return rows[index + 1].kind == _MultiHostRowKind.hostHeader
            ? const SizedBox(height: 8)
            : const Divider(height: 1, indent: 16);
      },
      itemBuilder: (context, index) {
        final row = rows[index];
        switch (row.kind) {
          case _MultiHostRowKind.hostHeader:
            return _HostGroupHeader(
              group: row.group,
              customName: projectHomeCustomHostName(
                customHostNames,
                row.group.profile,
              ),
              onRename: () {
                onRenameHost(row.group.profile);
              },
            );
          case _MultiHostRowKind.hostPlaceholder:
            return _HostGroupPlaceholder(group: row.group);
          case _MultiHostRowKind.hostProject:
            final entry = row.entry!;
            return _MultiHostProjectListTile(
              entry: entry,
              hasUnreadTaskCompletion: unreadProjectIds.contains(
                entry.project.id,
              ),
              hasWorkingAgents:
                  entry.project.hasWorkingAgents ||
                  workingProjectIds.contains(entry.project.id),
              onOpen: () {
                onOpenProject(entry);
              },
            );
        }
      },
    );
  }

  /// Flattens the per-computer groups into renderable rows, keeping each group's
  /// header directly above the rows it owns.
  List<_MultiHostRow> _buildRows() {
    return [
      for (final group in result.groups) ...[
        _MultiHostRow.header(group),
        if (group.isEmpty)
          _MultiHostRow.placeholder(group)
        else
          for (final entry in group.entries)
            _MultiHostRow.project(group: group, entry: entry),
      ],
    ];
  }
}

/// Kind of a rendered line in the grouped list.
enum _MultiHostRowKind { hostHeader, hostProject, hostPlaceholder }

/// One rendered line of the aggregated list: a computer header, one of that
/// computer's projects, or a placeholder standing in for a computer that owns no
/// project row. Each line carries the group it belongs to, so the renderer can
/// draw it without looking back for its owning computer.
class _MultiHostRow {
  const _MultiHostRow.header(this.group)
    : kind = _MultiHostRowKind.hostHeader,
      entry = null;

  const _MultiHostRow.placeholder(this.group)
    : kind = _MultiHostRowKind.hostPlaceholder,
      entry = null;

  /// Project lines always carry their entry, so rendering never has to guard
  /// against a project row without a project.
  const _MultiHostRow.project({
    required this.group,
    required ProjectHomeHostProject this.entry,
  }) : kind = _MultiHostRowKind.hostProject;

  final _MultiHostRowKind kind;
  final ProjectHomeHostGroup group;

  /// Set only for [_MultiHostRowKind.hostProject] lines.
  final ProjectHomeHostProject? entry;
}

/// One project row inside a computer's section. The owning computer is carried
/// by the section header, so the row itself only renders project detail and is
/// indented to read as a child of that header.
class _MultiHostProjectListTile extends StatelessWidget {
  const _MultiHostProjectListTile({
    required this.entry,
    required this.onOpen,
    required this.hasUnreadTaskCompletion,
    required this.hasWorkingAgents,
  });

  final ProjectHomeHostProject entry;
  final VoidCallback onOpen;
  final bool hasUnreadTaskCompletion;
  final bool hasWorkingAgents;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final project = entry.project;
    final root = project.root.trim();
    final health = project.health.trim();
    return ProjectWorkingRowHighlight(
      projectId: project.id,
      hasWorkingAgents: hasWorkingAgents,
      child: ListTile(
        key: ValueKey('multi-host-project-open-${entry.key}'),
        contentPadding: const EdgeInsets.fromLTRB(24, 8, 16, 8),
        leading: ProjectAttentionAvatar(
          projectId: project.id,
          favorite: project.favorite,
          hasUnreadTaskCompletion: hasUnreadTaskCompletion,
          hasWorkingAgents: hasWorkingAgents,
        ),
        title: Text(
          project.displayName,
          maxLines: 1,
          overflow: TextOverflow.ellipsis,
          style: theme.textTheme.titleMedium,
        ),
        subtitle: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (root.isNotEmpty)
              Text(root, maxLines: 1, overflow: TextOverflow.ellipsis),
            if (health.isNotEmpty) ...[
              const SizedBox(height: 4),
              Text(
                health,
                maxLines: 1,
                overflow: TextOverflow.ellipsis,
                style: theme.textTheme.bodySmall?.copyWith(
                  color: theme.colorScheme.onSurfaceVariant,
                ),
              ),
            ],
          ],
        ),
        trailing: const Icon(Icons.chevron_right),
        onTap: onOpen,
      ),
    );
  }
}

/// Section header naming the computer that owns the rows below it, together with
/// its connection state, project count, and a rename action. The name takes the
/// remaining width so a long computer name ellipsizes instead of overflowing the
/// header row.
class _HostGroupHeader extends StatelessWidget {
  const _HostGroupHeader({
    required this.group,
    required this.customName,
    required this.onRename,
  });

  final ProjectHomeHostGroup group;

  /// Name the user chose for this computer, or null while it still shows the
  /// name derived from its pairing.
  final String? customName;
  final VoidCallback onRename;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final strings = CcbMobileLocalizations.of(context);
    final colorScheme = theme.colorScheme;
    final catalog = group.catalog;
    final offline = catalog.offline;
    final nameColor = offline ? colorScheme.onSurfaceVariant : null;
    return Container(
      key: ValueKey('multi-host-group-header-${group.key}'),
      padding: const EdgeInsets.fromLTRB(12, 10, 12, 6),
      child: Row(
        children: [
          _buildLeading(colorScheme),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              projectHomeGatewayProfileHostName(
                group.profile,
                customName: customName,
              ),
              key: ValueKey('multi-host-group-name-${group.key}'),
              maxLines: 1,
              overflow: TextOverflow.ellipsis,
              style: theme.textTheme.titleSmall?.copyWith(color: nameColor),
            ),
          ),
          const SizedBox(width: 8),
          Text(
            _statusLabel(strings),
            key: ValueKey('multi-host-group-status-${group.key}'),
            maxLines: 1,
            overflow: TextOverflow.ellipsis,
            style: theme.textTheme.labelSmall?.copyWith(
              color: colorScheme.onSurfaceVariant,
            ),
          ),
          const SizedBox(width: 4),
          // Sized down to the status line so renaming stays reachable without
          // taking width away from the computer name.
          IconButton(
            key: ValueKey('multi-host-group-rename-${group.key}'),
            tooltip: strings.renameHost,
            onPressed: onRename,
            padding: EdgeInsets.zero,
            visualDensity: VisualDensity.compact,
            constraints: const BoxConstraints.tightFor(width: 32, height: 32),
            iconSize: 18,
            color: colorScheme.onSurfaceVariant,
            icon: const Icon(Icons.drive_file_rename_outline),
          ),
        ],
      ),
    );
  }

  /// Status glyph of this computer: still contacting, unreachable, or answered.
  Widget _buildLeading(ColorScheme colorScheme) {
    final catalog = group.catalog;
    if (catalog.pending) {
      return const SizedBox(
        width: 14,
        height: 14,
        child: CircularProgressIndicator(strokeWidth: 2),
      );
    }
    return Icon(
      catalog.offline ? Icons.cloud_off_outlined : Icons.computer,
      size: 16,
      color: colorScheme.onSurfaceVariant,
    );
  }

  /// Connection state of this computer, with the project count appended once the
  /// computer has actually answered with rows.
  String _statusLabel(CcbMobileLocalizations strings) {
    final catalog = group.catalog;
    if (catalog.offline) {
      return strings.hostOffline;
    }
    if (catalog.pending) {
      return strings.hostConnecting;
    }
    return '${strings.hostOnline} · ${strings.hostProjectCount(group.entries.length)}';
  }
}

/// Placeholder row keeping a computer visible when it owns no project row, so an
/// unreachable or empty computer still accounts for itself under its header.
class _HostGroupPlaceholder extends StatelessWidget {
  const _HostGroupPlaceholder({required this.group});

  final ProjectHomeHostGroup group;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final strings = CcbMobileLocalizations.of(context);
    final catalog = group.catalog;
    final label =
        catalog.offline
            ? strings.hostOffline
            : catalog.pending
            ? strings.hostConnecting
            : strings.hostNoProjects;
    return Padding(
      key: ValueKey('multi-host-group-placeholder-${group.key}'),
      padding: const EdgeInsets.fromLTRB(24, 4, 16, 12),
      child: Text(
        label,
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
        style: theme.textTheme.bodySmall?.copyWith(
          color: theme.colorScheme.onSurfaceVariant,
        ),
      ),
    );
  }
}
