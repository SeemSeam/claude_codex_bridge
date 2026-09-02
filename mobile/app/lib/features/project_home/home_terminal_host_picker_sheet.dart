import 'package:flutter/material.dart';

import '../../l10n/ccb_mobile_localizations.dart';
import '../../pairing/gateway_pairing.dart';
import 'project_home_gateway_profiles.dart';

/// One computer offered as a host terminal target. The status line is prepared by
/// the caller, which is the only place that knows each host's connection state.
class HomeTerminalHostOption {
  const HomeTerminalHostOption({
    required this.profile,
    required this.statusLabel,
    this.customName,
    this.available = true,
  });

  final GatewayPairedHost profile;

  /// Short connection state of this computer, shown under its name.
  final String statusLabel;

  /// Name the user chose for this computer, or null while it still shows the
  /// name derived from its pairing.
  final String? customName;

  /// False when this computer cannot open a terminal, either because pairing did
  /// not grant terminal access or because the computer is unreachable.
  final bool available;

  /// Stable identity of this option, matching the aggregated project list keys.
  String get key => projectHomeGatewayProfileKey(profile);
}

/// Asks which paired computer should own the terminal. Returns the chosen host,
/// or null when the sheet is dismissed without a choice.
Future<GatewayPairedHost?> showHomeTerminalHostPickerSheet(
  BuildContext context, {
  required List<HomeTerminalHostOption> options,
}) {
  return showModalBottomSheet<GatewayPairedHost>(
    context: context,
    showDragHandle: true,
    builder: (context) => _HomeTerminalHostPickerSheet(options: options),
  );
}

/// Host list of [showHomeTerminalHostPickerSheet]. Kept private so the sheet is
/// always entered through the function that supplies the modal route.
class _HomeTerminalHostPickerSheet extends StatelessWidget {
  const _HomeTerminalHostPickerSheet({required this.options});

  final List<HomeTerminalHostOption> options;

  @override
  Widget build(BuildContext context) {
    final theme = Theme.of(context);
    final strings = CcbMobileLocalizations.of(context);
    return SafeArea(
      child: Column(
        key: const ValueKey('home-terminal-host-picker-sheet'),
        mainAxisSize: MainAxisSize.min,
        crossAxisAlignment: CrossAxisAlignment.stretch,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 0, 20, 8),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  strings.openTerminal,
                  style: theme.textTheme.titleLarge,
                ),
                const SizedBox(height: 2),
                Text(
                  strings.chooseTerminalHost,
                  style: theme.textTheme.bodySmall?.copyWith(
                    color: theme.colorScheme.onSurfaceVariant,
                  ),
                ),
              ],
            ),
          ),
          const Divider(height: 1),
          Flexible(
            child: ListView.separated(
              key: const ValueKey('home-terminal-host-list'),
              padding: const EdgeInsets.symmetric(vertical: 8),
              shrinkWrap: true,
              itemCount: options.length,
              separatorBuilder: (context, index) => const Divider(height: 1),
              itemBuilder:
                  (context, index) => _HostOptionTile(option: options[index]),
            ),
          ),
        ],
      ),
    );
  }
}

/// One selectable computer. An unavailable computer stays visible but greyed out
/// so a paired host never silently disappears from the target list.
class _HostOptionTile extends StatelessWidget {
  const _HostOptionTile({required this.option});

  final HomeTerminalHostOption option;

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;
    final enabled = option.available;
    return ListTile(
      key: ValueKey('home-terminal-host-${option.key}'),
      enabled: enabled,
      leading: Icon(
        enabled ? Icons.computer : Icons.cloud_off_outlined,
        color: enabled ? null : colorScheme.onSurfaceVariant,
      ),
      title: Text(
        projectHomeGatewayProfileHostName(
          option.profile,
          customName: option.customName,
        ),
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
      ),
      subtitle: Text(
        option.statusLabel,
        maxLines: 1,
        overflow: TextOverflow.ellipsis,
      ),
      trailing: const Icon(Icons.chevron_right),
      onTap:
          enabled
              ? () => Navigator.of(context).pop(option.profile)
              : null,
    );
  }
}
