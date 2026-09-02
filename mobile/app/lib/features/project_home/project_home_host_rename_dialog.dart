import 'package:flutter/material.dart';

import '../../l10n/ccb_mobile_localizations.dart';

/// Longest computer name that is still readable inside a group header, so a
/// pasted path or token cannot push the status label off the row.
const projectHomeHostNameMaxLength = 40;

/// Outcome of [showProjectHomeHostRenameDialog]: either the name the user typed
/// or an explicit reset back to the pairing-derived name. Dismissing the dialog
/// yields no result at all, so cancelling never rewrites a stored name.
class ProjectHomeHostRenameResult {
  const ProjectHomeHostRenameResult._(this.name);

  /// The user chose an explicit name for this computer.
  const ProjectHomeHostRenameResult.named(String name) : this._(name);

  /// The user cleared the name, so the pairing-derived name applies again.
  const ProjectHomeHostRenameResult.automatic() : this._(null);

  /// Chosen name, or null when the pairing-derived name should be restored.
  final String? name;
}

/// Asks for the name of one paired computer, prefilled with its current name.
/// Returns null when the dialog is dismissed, so the stored name is left alone.
Future<ProjectHomeHostRenameResult?> showProjectHomeHostRenameDialog(
  BuildContext context, {
  required String automaticName,
  String? currentName,
}) {
  return showDialog<ProjectHomeHostRenameResult>(
    context: context,
    builder:
        (context) => _ProjectHomeHostRenameDialog(
          automaticName: automaticName,
          currentName: currentName,
        ),
  );
}

/// Name field of [showProjectHomeHostRenameDialog]. Kept private so the dialog
/// is always entered through the function that supplies the modal route.
class _ProjectHomeHostRenameDialog extends StatefulWidget {
  const _ProjectHomeHostRenameDialog({
    required this.automaticName,
    required this.currentName,
  });

  /// Name this computer falls back to, shown as the hint of an empty field.
  final String automaticName;

  /// Name the user chose earlier, or null while the automatic name is in use.
  final String? currentName;

  @override
  State<_ProjectHomeHostRenameDialog> createState() =>
      _ProjectHomeHostRenameDialogState();
}

class _ProjectHomeHostRenameDialogState
    extends State<_ProjectHomeHostRenameDialog> {
  late final TextEditingController _controller = TextEditingController(
    text: widget.currentName ?? '',
  );

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  /// Closes with the typed name, treating a field the user emptied as a reset so
  /// clearing the text has the same effect as the explicit reset action.
  void _submit() {
    final name = _controller.text.trim();
    Navigator.of(context).pop(
      name.isEmpty
          ? const ProjectHomeHostRenameResult.automatic()
          : ProjectHomeHostRenameResult.named(name),
    );
  }

  @override
  Widget build(BuildContext context) {
    final strings = CcbMobileLocalizations.of(context);
    return AlertDialog(
      key: const ValueKey('host-rename-dialog'),
      title: Text(strings.renameHost),
      content: TextField(
        key: const ValueKey('host-rename-field'),
        controller: _controller,
        autofocus: true,
        maxLength: projectHomeHostNameMaxLength,
        textInputAction: TextInputAction.done,
        onSubmitted: (_) => _submit(),
        decoration: InputDecoration(
          labelText: strings.hostNameLabel,
          hintText: widget.automaticName,
          helperText: strings.hostNameHelp,
          helperMaxLines: 2,
        ),
      ),
      actions: [
        if (widget.currentName != null)
          TextButton(
            key: const ValueKey('host-rename-reset'),
            onPressed:
                () => Navigator.of(
                  context,
                ).pop(const ProjectHomeHostRenameResult.automatic()),
            child: Text(strings.useAutomaticHostName),
          ),
        TextButton(
          key: const ValueKey('host-rename-cancel'),
          onPressed: () => Navigator.of(context).pop(),
          child: Text(strings.cancel),
        ),
        TextButton(
          key: const ValueKey('host-rename-save'),
          onPressed: _submit,
          child: Text(strings.save),
        ),
      ],
    );
  }
}
