import 'package:flutter/material.dart';
import 'package:flutter/rendering.dart' show ScrollDirection;

import '../../app/chat_background.dart';
import '../../l10n/ccb_mobile_localizations.dart';
import '../../models/ccb_agent.dart';
import '../../models/ccb_project.dart';
import '../../models/ccb_project_view.dart';
import '../../models/ccb_window.dart';
import '../../repository/mobile_ccb_repository.dart';
import '../../transport/terminal_transport.dart';
import '../agent_chat/selected_agent_workspace.dart';
import '../provider_control/provider_control_sheet.dart';
import '../terminal/agent_terminal_workspace.dart';
import '../../cache/mobile_snapshot_store.dart';
import 'gateway_reconnecting_banner.dart';
import 'project_home_terminal_navigation.dart';
import 'project_shell_widgets.dart';

class ProjectHomeProjectListHost extends StatelessWidget {
  const ProjectHomeProjectListHost({
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
    return ProjectListScaffold(
      view: view,
      selectedAgent: selectedAgent,
      onOpenProject: onOpenProject,
      onOpenNotifications: onOpenNotifications,
      onOpenConnectionDetails: onOpenConnectionDetails,
      hasUnreadTaskCompletion: hasUnreadTaskCompletion,
      hasWorkingAgents: hasWorkingAgents,
    );
  }
}

class ProjectHomeServerProjectListHost extends StatelessWidget {
  const ProjectHomeServerProjectListHost({
    required this.projects,
    required this.onRefreshProjects,
    required this.onOpenTerminal,
    required this.onOpenSettings,
    required this.onOpenProject,
    this.unreadProjectIds = const {},
    this.workingProjectIds = const {},
    super.key,
  });

  final List<CcbProject> projects;
  final VoidCallback onRefreshProjects;
  final VoidCallback onOpenTerminal;
  final VoidCallback onOpenSettings;
  final ValueChanged<CcbProject> onOpenProject;
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
              ),
              Expanded(
                child:
                    projects.isEmpty
                        ? Center(
                          key: const ValueKey('project-list-empty'),
                          child: Text(strings.noCcbProjectsFound),
                        )
                        : ListView.separated(
                          key: const ValueKey('project-list'),
                          itemCount: projects.length,
                          separatorBuilder:
                              (context, index) => const Divider(height: 1),
                          itemBuilder: (context, index) {
                            final project = projects[index];
                            return _ServerProjectListTile(
                              project: project,
                              hasUnreadTaskCompletion: unreadProjectIds
                                  .contains(project.id),
                              hasWorkingAgents:
                                  project.hasWorkingAgents ||
                                  workingProjectIds.contains(project.id),
                              onOpen: () {
                                onOpenProject(project);
                              },
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

class _ServerProjectListTile extends StatelessWidget {
  const _ServerProjectListTile({
    required this.project,
    required this.onOpen,
    required this.hasUnreadTaskCompletion,
    required this.hasWorkingAgents,
  });

  final CcbProject project;
  final VoidCallback onOpen;
  final bool hasUnreadTaskCompletion;
  final bool hasWorkingAgents;

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    final root = project.root.trim();
    final health = project.health.trim();
    return ProjectWorkingRowHighlight(
      projectId: project.id,
      hasWorkingAgents: hasWorkingAgents,
      child: ListTile(
        key: ValueKey('project-open-${project.id}'),
        contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 8),
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
          style: textTheme.titleMedium,
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
                style: textTheme.bodySmall?.copyWith(
                  color: Theme.of(context).colorScheme.onSurfaceVariant,
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

class ProjectHomeMobileChatScaffoldHost extends StatefulWidget {
  const ProjectHomeMobileChatScaffoldHost({
    required this.view,
    required this.selectedAgent,
    required this.repository,
    required this.terminalTransport,
    required this.usePaneInputForMessages,
    required this.mobileAgentsCollapsed,
    required this.onBack,
    required this.onOpenConnectionDetails,
    required this.onCollapseAgents,
    required this.onExpandAgents,
    required this.onWindowSelected,
    required this.onAgentSelected,
    required this.onRefreshView,
    required this.onTimelineScrollDirectionChanged,
    this.unreadAgentNames = const {},
    this.onProjectActivity,
    this.snapshotStore,
    this.snapshotNamespace,
    this.sendEnabled = true,
    this.sendDisabledReason,
    this.conversationRefreshToken = 0,
    this.reconnectRetryIn,
    this.onRetryConnection,
    this.terminalMode,
    this.onTerminalModeChanged,
    super.key,
  });

  final CcbProjectView view;
  final CcbAgent? selectedAgent;
  final MobileCcbRepository repository;
  final TerminalTransport? terminalTransport;
  final bool usePaneInputForMessages;
  final bool mobileAgentsCollapsed;
  final VoidCallback onBack;
  final VoidCallback onOpenConnectionDetails;
  final VoidCallback onCollapseAgents;
  final VoidCallback onExpandAgents;
  final VoidCallback? onProjectActivity;
  final ValueChanged<String> onWindowSelected;
  final ValueChanged<String> onAgentSelected;
  final Future<CcbProjectView?> Function() onRefreshView;
  final ValueChanged<ScrollDirection> onTimelineScrollDirectionChanged;
  final Set<String> unreadAgentNames;
  final MobileSnapshotStore? snapshotStore;
  final String? snapshotNamespace;
  final bool sendEnabled;
  final String? sendDisabledReason;
  final int conversationRefreshToken;
  final Duration? reconnectRetryIn;
  final VoidCallback? onRetryConnection;
  final bool? terminalMode;
  final ValueChanged<bool>? onTerminalModeChanged;

  @override
  State<ProjectHomeMobileChatScaffoldHost> createState() =>
      _ProjectHomeMobileChatScaffoldHostState();
}

class _ProjectHomeMobileChatScaffoldHostState
    extends State<ProjectHomeMobileChatScaffoldHost> {
  final SelectedAgentWorkspaceController _workspaceController =
      SelectedAgentWorkspaceController();
  var _localTerminalMode = false;

  bool get _terminalMode => widget.terminalMode ?? _localTerminalMode;

  @override
  void didUpdateWidget(covariant ProjectHomeMobileChatScaffoldHost oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.view.project.id != widget.view.project.id ||
        widget.selectedAgent == null) {
      _localTerminalMode = false;
    }
    if (oldWidget.conversationRefreshToken != widget.conversationRefreshToken) {
      _workspaceController.refreshLatest();
    }
  }

  @override
  void dispose() {
    _workspaceController.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final selectedAgent = widget.selectedAgent;
    final terminalAction =
        selectedAgent == null
            ? null
            : _terminalMode
            ? _showChat
            : _showTerminal;
    final hasBackground = ccbWorkspaceBackgroundEnabled(context);
    final scaffold = Scaffold(
      backgroundColor: hasBackground ? Colors.transparent : null,
      body: SafeArea(
        child: Padding(
          key: const ValueKey('project-chat-screen'),
          padding: const EdgeInsets.fromLTRB(8, 4, 8, 8),
          child: Column(
            children: [
              if (widget.mobileAgentsCollapsed)
                _MobileCollapsedProjectBar(
                  view: widget.view,
                  selectedAgent: selectedAgent,
                  unreadAgentNames: widget.unreadAgentNames,
                  onShowProjects: widget.onBack,
                  onExpandAgents: widget.onExpandAgents,
                  onRefreshConversation: _workspaceController.refreshLatest,
                  onOpenTerminal: terminalAction,
                  onOpenProviderControl: _providerControlAction,
                  terminalMode: _terminalMode,
                  onShowChat: _showChat,
                  onOpenConnectionDetails: widget.onOpenConnectionDetails,
                )
              else ...[
                ProjectChatHeader(
                  view: widget.view,
                  selectedAgent: selectedAgent,
                  onBack: widget.onBack,
                  onRefreshConversation: _workspaceController.refreshLatest,
                  onOpenTerminal: terminalAction,
                  terminalMode: _terminalMode,
                  onShowChat: _showChat,
                  onOpenProviderControl: _providerControlAction,
                  onOpenConnectionDetails: widget.onOpenConnectionDetails,
                ),
                const SizedBox(height: 4),
                MobileAgentSwitcherPanel(
                  view: widget.view,
                  selectedAgent: selectedAgent,
                  collapsed: false,
                  unreadAgentNames: widget.unreadAgentNames,
                  onCollapse: widget.onCollapseAgents,
                  onExpand: widget.onExpandAgents,
                  onWindowSelected: _handleWindowSelected,
                  onAgentSelected: widget.onAgentSelected,
                ),
              ],
              if (widget.onRetryConnection != null) ...[
                const SizedBox(height: 4),
                GatewayReconnectingBanner(
                  retryIn: widget.reconnectRetryIn,
                  onRetry: widget.onRetryConnection!,
                  onDiagnostics: widget.onOpenConnectionDetails,
                ),
              ],
              const SizedBox(height: 4),
              Expanded(
                child: IndexedStack(
                  key: const ValueKey('project-agent-content-modes'),
                  index: _terminalMode ? 1 : 0,
                  children: [
                    TickerMode(
                      enabled: !_terminalMode,
                      child: IgnorePointer(
                        ignoring: _terminalMode,
                        child: ExcludeFocus(
                          excluding: _terminalMode,
                          child: KeyedSubtree(
                            key: const ValueKey('project-agent-chat-mode'),
                            child: SelectedAgentWorkspace(
                              repository: widget.repository,
                              terminalTransport: widget.terminalTransport,
                              usePaneInputForMessages:
                                  widget.usePaneInputForMessages,
                              view: widget.view,
                              agent: selectedAgent,
                              enableComposerCollapse: true,
                              onRefreshView: widget.onRefreshView,
                              onUserScrollDirectionChanged:
                                  widget.onTimelineScrollDirectionChanged,
                              onProjectActivity: widget.onProjectActivity,
                              controller: _workspaceController,
                              snapshotStore: widget.snapshotStore,
                              snapshotNamespace: widget.snapshotNamespace,
                              sendEnabled: widget.sendEnabled,
                              sendDisabledReason: widget.sendDisabledReason,
                              refreshToken: widget.conversationRefreshToken,
                            ),
                          ),
                        ),
                      ),
                    ),
                    TickerMode(
                      enabled: _terminalMode,
                      child:
                          _terminalMode && selectedAgent != null
                              ? AgentTerminalWorkspace(
                                key: const ValueKey(
                                  'project-agent-terminal-mode',
                                ),
                                repository: widget.repository,
                                view: widget.view,
                                agent: selectedAgent,
                                terminalTransport: widget.terminalTransport,
                                gatewayTerminal: widget.usePaneInputForMessages,
                                active: _terminalMode,
                                onUserScrollDirectionChanged:
                                    widget.onTimelineScrollDirectionChanged,
                              )
                              : const SizedBox.shrink(),
                    ),
                  ],
                ),
              ),
            ],
          ),
        ),
      ),
    );
    return CcbWorkspaceBackground(terminal: _terminalMode, child: scaffold);
  }

  void _showTerminal() {
    final selectedAgent = widget.selectedAgent;
    if (selectedAgent == null) {
      return;
    }
    final outcome =
        widget.usePaneInputForMessages
            ? projectHomeGatewayTerminalNavigation(
              view: widget.view,
              agentName: selectedAgent.name,
              hasTerminalTransport: widget.terminalTransport != null,
            )
            : projectHomeFakeTerminalNavigation(
              view: widget.view,
              agentName: selectedAgent.name,
            );
    if (outcome.kind == ProjectHomeTerminalNavigationKind.noTransport) {
      ScaffoldMessenger.of(
        context,
      ).showSnackBar(SnackBar(content: Text(outcome.snackMessage!)));
      return;
    }
    if (outcome.kind != ProjectHomeTerminalNavigationKind.open) {
      ScaffoldMessenger.of(context).showSnackBar(
        const SnackBar(
          content: Text('Terminal target is unavailable. Refresh and retry.'),
        ),
      );
      return;
    }
    _setTerminalMode(true);
    widget.onCollapseAgents();
  }

  void _showChat() {
    if (!_terminalMode) {
      return;
    }
    _setTerminalMode(false);
  }

  void _setTerminalMode(bool value) {
    final callback = widget.onTerminalModeChanged;
    if (callback != null) {
      callback(value);
      return;
    }
    setState(() {
      _localTerminalMode = value;
    });
  }

  VoidCallback? get _providerControlAction {
    if (widget.selectedAgent == null ||
        widget.selectedAgent!.providerControl == null ||
        widget.repository is! MobileCcbProviderControlRepository) {
      return null;
    }
    return _showProviderControl;
  }

  Future<void> _showProviderControl() async {
    final agent = widget.selectedAgent;
    final repository = widget.repository;
    if (agent == null || repository is! MobileCcbProviderControlRepository) {
      return;
    }
    final changed = await showProviderControlSheet(
      context,
      repository: repository as MobileCcbProviderControlRepository,
      projectId: widget.view.project.id,
      agent: agent,
    );
    if (changed) {
      await widget.onRefreshView();
    }
  }

  void _handleWindowSelected(String windowName) {
    if (!_terminalMode) {
      widget.onWindowSelected(windowName);
      return;
    }
    final agentName = projectHomeLocalWindowSelectionAgentName(
      widget.view,
      windowName,
    );
    if (agentName != null) {
      widget.onAgentSelected(agentName);
    }
  }
}

enum _MobileCollapsedProjectAction { projects, diagnostics }

class _MobileCollapsedProjectBar extends StatelessWidget {
  const _MobileCollapsedProjectBar({
    required this.view,
    required this.selectedAgent,
    required this.unreadAgentNames,
    required this.onShowProjects,
    required this.onExpandAgents,
    required this.onRefreshConversation,
    required this.onOpenTerminal,
    required this.onOpenProviderControl,
    required this.terminalMode,
    required this.onShowChat,
    required this.onOpenConnectionDetails,
  });

  final CcbProjectView view;
  final CcbAgent? selectedAgent;
  final Set<String> unreadAgentNames;
  final VoidCallback onShowProjects;
  final VoidCallback onExpandAgents;
  final VoidCallback onRefreshConversation;
  final VoidCallback? onOpenTerminal;
  final VoidCallback? onOpenProviderControl;
  final bool terminalMode;
  final VoidCallback onShowChat;
  final VoidCallback onOpenConnectionDetails;

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;
    final strings = CcbMobileLocalizations.of(context);
    final selectedWindow = selectedWindowForView(view, selectedAgent);
    final hasUnread = unreadAgentNames.isNotEmpty;
    return Material(
      key: const ValueKey('mobile-agent-switcher-collapsed'),
      color: colorScheme.surface,
      shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.circular(8),
        side: BorderSide(color: colorScheme.outlineVariant),
      ),
      clipBehavior: Clip.antiAlias,
      child: SizedBox(
        height: 60,
        child: Row(
          children: [
            Expanded(
              child: InkWell(
                onTap: onExpandAgents,
                child: Padding(
                  padding: const EdgeInsets.symmetric(horizontal: 12),
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
                          color: colorScheme.primary,
                        ),
                      ),
                      const SizedBox(width: 10),
                      Expanded(
                        child: Column(
                          mainAxisAlignment: MainAxisAlignment.center,
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Text(
                              view.project.displayName,
                              key: const ValueKey('project-chat-title'),
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: Theme.of(context).textTheme.labelLarge,
                            ),
                            const SizedBox(height: 2),
                            Text(
                              _mobileAgentSummary(
                                selectedWindow: selectedWindow,
                                selectedAgent: selectedAgent,
                                agentCount: view.agents.length,
                                pendingLabel: strings.providerPendingShort,
                              ),
                              key: const ValueKey(
                                'mobile-agent-switcher-summary',
                              ),
                              maxLines: 1,
                              overflow: TextOverflow.ellipsis,
                              style: Theme.of(context).textTheme.bodyMedium
                                  ?.copyWith(color: colorScheme.onSurface),
                            ),
                          ],
                        ),
                      ),
                    ],
                  ),
                ),
              ),
            ),
            if (onOpenProviderControl != null)
              IconButton(
                key: const ValueKey('agent-provider-control-action'),
                tooltip: strings.providerControl,
                visualDensity: VisualDensity.compact,
                onPressed: onOpenProviderControl,
                icon: const Icon(Icons.tune),
              ),
            IconButton(
              key: const ValueKey('mobile-agent-switcher-expand-action'),
              tooltip: 'Show agents',
              visualDensity: VisualDensity.compact,
              onPressed: onExpandAgents,
              icon: const Icon(Icons.keyboard_arrow_down),
            ),
            IconButton(
              key: const ValueKey('agent-conversation-refresh-action'),
              tooltip: strings.refreshConversation,
              visualDensity: VisualDensity.compact,
              onPressed: onRefreshConversation,
              icon: const Icon(Icons.refresh),
            ),
            IconButton(
              key: ValueKey(
                terminalMode
                    ? 'return-to-agent-chat-button'
                    : 'open-agent-terminal-button',
              ),
              tooltip:
                  terminalMode ? strings.returnToChat : strings.openTerminal,
              visualDensity: VisualDensity.compact,
              onPressed: terminalMode ? onShowChat : onOpenTerminal,
              icon: Icon(
                terminalMode ? Icons.chat_bubble_outline : Icons.terminal,
              ),
            ),
            PopupMenuButton<_MobileCollapsedProjectAction>(
              key: const ValueKey('project-chat-overflow-action'),
              tooltip: strings.diagnostics,
              icon: const Icon(Icons.more_vert),
              onSelected: (action) {
                switch (action) {
                  case _MobileCollapsedProjectAction.projects:
                    onShowProjects();
                  case _MobileCollapsedProjectAction.diagnostics:
                    onOpenConnectionDetails();
                }
              },
              itemBuilder:
                  (context) => [
                    PopupMenuItem<_MobileCollapsedProjectAction>(
                      key: const ValueKey('project-chat-projects-menu-item'),
                      value: _MobileCollapsedProjectAction.projects,
                      child: Text(strings.projects),
                    ),
                    PopupMenuItem<_MobileCollapsedProjectAction>(
                      key: const ValueKey('project-chat-diagnostics-menu-item'),
                      value: _MobileCollapsedProjectAction.diagnostics,
                      child: Text(strings.diagnostics),
                    ),
                  ],
            ),
          ],
        ),
      ),
    );
  }

  String _mobileAgentSummary({
    required CcbWindow? selectedWindow,
    required CcbAgent? selectedAgent,
    required int agentCount,
    required String pendingLabel,
  }) {
    final agent = selectedAgent;
    if (agent == null) {
      return '$agentCount agents';
    }
    final window = selectedWindow;
    if (window == null) {
      return agent.name;
    }
    final control = agent.providerControl;
    final provider =
        control == null
            ? providerLabel(agent.provider)
            : providerIdentityText(control);
    final pending = control?.hasPendingChange == true ? ' · $pendingLabel' : '';
    return '${window.label} / ${agent.name} · $provider$pending';
  }
}

class ProjectHomeWideScaffoldHost extends StatelessWidget {
  const ProjectHomeWideScaffoldHost({
    required this.view,
    required this.selectedAgent,
    required this.repository,
    required this.terminalTransport,
    required this.usePaneInputForMessages,
    required this.sidebarState,
    required this.onOpenProject,
    required this.onOpenNotifications,
    required this.onOpenConnectionDetails,
    required this.onShowProjects,
    required this.onAgentSelected,
    required this.onOpenTerminal,
    required this.onToggleSidebar,
    required this.onHorizontalDragStart,
    required this.onHorizontalDragUpdate,
    required this.onHorizontalDragEnd,
    required this.onRefreshView,
    this.unreadAgentNames = const {},
    this.onProjectActivity,
    this.hasUnreadTaskCompletion = false,
    this.hasWorkingAgents = false,
    this.snapshotStore,
    this.snapshotNamespace,
    this.sendEnabled = true,
    this.sendDisabledReason,
    this.conversationRefreshToken = 0,
    this.reconnectRetryIn,
    this.onRetryConnection,
    this.terminalMode = false,
    this.onShowChat,
    super.key,
  });

  final CcbProjectView view;
  final CcbAgent? selectedAgent;
  final MobileCcbRepository repository;
  final TerminalTransport? terminalTransport;
  final bool usePaneInputForMessages;
  final WideSidebarState sidebarState;
  final VoidCallback onOpenProject;
  final VoidCallback onOpenNotifications;
  final VoidCallback onOpenConnectionDetails;
  final VoidCallback onShowProjects;
  final ValueChanged<CcbAgent> onAgentSelected;
  final ValueChanged<String> onOpenTerminal;
  final VoidCallback? onProjectActivity;
  final VoidCallback onToggleSidebar;
  final GestureDragStartCallback onHorizontalDragStart;
  final GestureDragUpdateCallback onHorizontalDragUpdate;
  final GestureDragEndCallback onHorizontalDragEnd;
  final Future<CcbProjectView?> Function() onRefreshView;
  final Set<String> unreadAgentNames;
  final bool hasUnreadTaskCompletion;
  final bool hasWorkingAgents;
  final MobileSnapshotStore? snapshotStore;
  final String? snapshotNamespace;
  final bool sendEnabled;
  final String? sendDisabledReason;
  final int conversationRefreshToken;
  final Duration? reconnectRetryIn;
  final VoidCallback? onRetryConnection;
  final bool terminalMode;
  final VoidCallback? onShowChat;

  @override
  Widget build(BuildContext context) {
    final activeAgent = selectedAgent;
    final terminalUsesFullWidth = terminalMode && activeAgent != null;
    final sidebars =
        terminalUsesFullWidth
            ? const <Widget>[]
            : switch (sidebarState) {
              WideSidebarState.expanded => <Widget>[
                SizedBox(
                  width: projectHomeWideProjectColumnWidth,
                  child: WideProjectColumn(
                    view: view,
                    selectedAgent: selectedAgent,
                    onProjectSelected: onOpenProject,
                    onOpenNotifications: onOpenNotifications,
                    onOpenConnectionDetails: onOpenConnectionDetails,
                    hasUnreadTaskCompletion: hasUnreadTaskCompletion,
                    hasWorkingAgents: hasWorkingAgents,
                  ),
                ),
                const VerticalDivider(width: 1),
                SizedBox(
                  width: projectHomeWideAgentColumnWidth,
                  child: WideAgentColumn(
                    view: view,
                    selectedAgentName: selectedAgent?.name,
                    unreadAgentNames: unreadAgentNames,
                    onAgentSelected: onAgentSelected,
                  ),
                ),
              ],
              WideSidebarState.projectCollapsed => <Widget>[
                SizedBox(
                  width: projectHomeWideAgentColumnWidth,
                  child: WideAgentColumn(
                    view: view,
                    selectedAgentName: selectedAgent?.name,
                    unreadAgentNames: unreadAgentNames,
                    onShowProjects: onShowProjects,
                    onAgentSelected: onAgentSelected,
                  ),
                ),
              ],
              WideSidebarState.allCollapsed => <Widget>[
                SizedBox(
                  width: projectHomeWideCollapsedSidebarWidth,
                  child: WideCollapsedSidebarRail(
                    view: view,
                    selectedAgent: selectedAgent,
                    onExpand: onShowProjects,
                    onOpenNotifications: onOpenNotifications,
                    onOpenConnectionDetails: onOpenConnectionDetails,
                  ),
                ),
              ],
            };
    final hasBackground = ccbWorkspaceBackgroundEnabled(context);
    final scaffold = Scaffold(
      backgroundColor: hasBackground ? Colors.transparent : null,
      body: SafeArea(
        child: Row(
          key: const ValueKey('wide-project-workspace'),
          children: [
            ...sidebars,
            if (!terminalUsesFullWidth)
              WideSidebarDragHandle(
                sidebarState: sidebarState,
                onToggle: onToggleSidebar,
                onHorizontalDragStart: onHorizontalDragStart,
                onHorizontalDragUpdate: onHorizontalDragUpdate,
                onHorizontalDragEnd: onHorizontalDragEnd,
              ),
            Expanded(
              child: Padding(
                key: const ValueKey('wide-project-chat-screen'),
                padding: const EdgeInsets.fromLTRB(8, 4, 8, 8),
                child: Column(
                  children: [
                    ProjectChatHeader(
                      view: view,
                      selectedAgent: selectedAgent,
                      onBack: null,
                      terminalMode: terminalMode,
                      onShowChat: onShowChat,
                      onOpenTerminal:
                          selectedAgent == null || terminalMode
                              ? null
                              : () {
                                onOpenTerminal(selectedAgent!.name);
                              },
                      onOpenConnectionDetails: onOpenConnectionDetails,
                      onOpenProviderControl:
                          selectedAgent?.providerControl != null &&
                                  repository
                                      is MobileCcbProviderControlRepository
                              ? () async {
                                final changed = await showProviderControlSheet(
                                  context,
                                  repository:
                                      repository
                                          as MobileCcbProviderControlRepository,
                                  projectId: view.project.id,
                                  agent: selectedAgent!,
                                );
                                if (changed) {
                                  await onRefreshView();
                                }
                              }
                              : null,
                    ),
                    if (onRetryConnection != null) ...[
                      const SizedBox(height: 4),
                      GatewayReconnectingBanner(
                        retryIn: reconnectRetryIn,
                        onRetry: onRetryConnection!,
                        onDiagnostics: onOpenConnectionDetails,
                      ),
                    ],
                    const SizedBox(height: 4),
                    Expanded(
                      child:
                          terminalMode && activeAgent != null
                              ? AgentTerminalWorkspace(
                                key: const ValueKey(
                                  'wide-project-agent-terminal-mode',
                                ),
                                repository: repository,
                                view: view,
                                agent: activeAgent,
                                terminalTransport: terminalTransport,
                                gatewayTerminal: usePaneInputForMessages,
                                active: true,
                              )
                              : SelectedAgentWorkspace(
                                repository: repository,
                                terminalTransport: terminalTransport,
                                usePaneInputForMessages:
                                    usePaneInputForMessages,
                                view: view,
                                agent: selectedAgent,
                                enableComposerCollapse: false,
                                onRefreshView: onRefreshView,
                                onUserScrollDirectionChanged: null,
                                onProjectActivity: onProjectActivity,
                                snapshotStore: snapshotStore,
                                snapshotNamespace: snapshotNamespace,
                                sendEnabled: sendEnabled,
                                sendDisabledReason: sendDisabledReason,
                                refreshToken: conversationRefreshToken,
                              ),
                    ),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
    return CcbWorkspaceBackground(terminal: terminalMode, child: scaffold);
  }
}
