import 'package:flutter/material.dart';

import '../../l10n/ccb_mobile_localizations.dart';
import '../../widgets/working_status_style.dart';
import '../agent_chat/agent_execution_status.dart';
import '../../models/ccb_project_view.dart';

int workingAgentCountForView(CcbProjectView view) {
  return view.agents.where(agentHasSourceWorkingActivity).length;
}

/// Shared never-truncated working status used by the collapsed agent bars.
///
/// The host collapsed bar (`_MobileCollapsedProjectBar`) and the switcher
/// panel's collapsed branch both render this single component, so the two
/// surfaces cannot drift: accent from [workingStatusAccent], the count keeps
/// its own box (`FittedBox.scaleDown` instead of clipping), and the name text
/// next to it ellipsizes independently.
class WorkingCountSummary extends StatelessWidget {
  const WorkingCountSummary({required this.count, this.style, super.key});

  final int count;
  final TextStyle? style;

  @override
  Widget build(BuildContext context) {
    if (count <= 0) {
      return const SizedBox.shrink();
    }
    final accent = workingStatusAccent(Theme.of(context).colorScheme);
    final label = CcbMobileLocalizations.of(
      context,
    ).agentWorkingCountLabel(count);
    return Semantics(
      container: true,
      label: label,
      child: Padding(
        padding: const EdgeInsets.only(left: 8),
        child: FittedBox(
          fit: BoxFit.scaleDown,
          child: ExcludeSemantics(
            child: Text(
              label,
              key: const ValueKey('mobile-agent-switcher-working-count'),
              maxLines: 1,
              overflow: TextOverflow.visible,
              softWrap: false,
              style: (style ?? Theme.of(context).textTheme.titleSmall)
                  ?.copyWith(color: accent, fontWeight: FontWeight.w600),
            ),
          ),
        ),
      ),
    );
  }
}
