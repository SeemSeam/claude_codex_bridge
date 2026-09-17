import 'package:flutter/material.dart';

import '../../l10n/ccb_mobile_localizations.dart';
import '../../models/ccb_agent.dart';
import '../../models/ccb_project_view.dart';
import '../provider_control/provider_control_sheet.dart';

class ProjectChatHeader extends StatelessWidget {
  const ProjectChatHeader({
    required this.view,
    required this.selectedAgent,
    required this.onBack,
    required this.onOpenTerminal,
    required this.onOpenConnectionDetails,
    this.onRefreshConversation,
    this.onShowChat,
    this.onOpenProviderControl,
    this.terminalMode = false,
    super.key,
  });

  final CcbProjectView view;
  final CcbAgent? selectedAgent;
  final VoidCallback? onBack;
  final VoidCallback? onRefreshConversation;
  final VoidCallback? onOpenTerminal;
  final VoidCallback? onShowChat;
  final VoidCallback? onOpenProviderControl;
  final VoidCallback onOpenConnectionDetails;
  final bool terminalMode;

  @override
  Widget build(BuildContext context) {
    final textTheme = Theme.of(context).textTheme;
    final strings = CcbMobileLocalizations.of(context);
    return SizedBox(
      key: const ValueKey('project-chat-header'),
      // Keep terminal geometry unchanged. Chat saves vertical chrome while
      // allowing the two identity lines to grow with accessibility text size.
      height:
          terminalMode
              ? 64
              : (MediaQuery.textScalerOf(context).scale(36) + 16).clamp(
                56.0,
                double.infinity,
              ),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 8),
        child: Row(
          children: [
            if (onBack != null)
              IconButton(
                key: const ValueKey('project-back-button'),
                tooltip: strings.projects,
                onPressed: onBack,
                icon: const Icon(Icons.arrow_back),
              ),
            Expanded(
              child: Padding(
                padding: EdgeInsets.symmetric(horizontal: terminalMode ? 8 : 4),
                child: Column(
                  mainAxisAlignment: MainAxisAlignment.center,
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      view.project.displayName,
                      key: const ValueKey('project-chat-title'),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                      style: textTheme.titleMedium,
                    ),
                    if (selectedAgent != null)
                      Text(
                        _agentProviderIdentity(
                          selectedAgent!,
                          pendingLabel: strings.providerPendingShort,
                        ),
                        key: const ValueKey('agent-provider-identity'),
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: textTheme.bodySmall?.copyWith(
                          color: Theme.of(context).colorScheme.onSurfaceVariant,
                        ),
                      ),
                  ],
                ),
              ),
            ),
            if (onOpenProviderControl != null)
              IconButton(
                key: const ValueKey('agent-provider-control-action'),
                tooltip: strings.providerControl,
                onPressed: onOpenProviderControl,
                icon: const Icon(Icons.tune),
              ),
            if (onRefreshConversation != null)
              IconButton(
                key: const ValueKey('agent-conversation-refresh-action'),
                tooltip: strings.refreshConversation,
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
              onPressed: terminalMode ? onShowChat : onOpenTerminal,
              icon: Icon(
                terminalMode ? Icons.chat_bubble_outline : Icons.terminal,
              ),
            ),
            IconButton(
              key: const ValueKey('connection-details-action'),
              tooltip: strings.diagnostics,
              onPressed: onOpenConnectionDetails,
              icon: const Icon(Icons.more_vert),
            ),
          ],
        ),
      ),
    );
  }
}

String _agentProviderIdentity(CcbAgent agent, {required String pendingLabel}) {
  final control = agent.providerControl;
  if (control != null) {
    final identity = providerIdentityText(control);
    return control.hasPendingChange ? '$identity · $pendingLabel' : identity;
  }
  return providerLabel(agent.provider);
}
