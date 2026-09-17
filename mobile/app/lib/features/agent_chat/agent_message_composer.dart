import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter/services.dart';

import '../../l10n/ccb_mobile_localizations.dart';
import '../../models/ccb_conversation_item.dart';

class AgentMessageComposer extends StatefulWidget {
  const AgentMessageComposer({
    required this.agentName,
    required this.controller,
    this.focusNode,
    required this.isSending,
    this.sendEnabled = true,
    this.sendDisabledReason,
    required this.collapsible,
    required this.collapsed,
    required this.onCollapse,
    required this.onExpand,
    required this.draftAttachments,
    required this.onPickImage,
    required this.onPickFile,
    required this.onRemoveAttachment,
    required this.onSend,
    required this.onSendTab,
    required this.onSendEscape,
    super.key,
  });

  final String agentName;
  final TextEditingController controller;
  final FocusNode? focusNode;
  final bool isSending;
  final bool sendEnabled;
  final String? sendDisabledReason;
  final bool collapsible;
  final bool collapsed;
  final VoidCallback onCollapse;
  final VoidCallback onExpand;
  final List<CcbMessageAttachment> draftAttachments;
  final VoidCallback onPickImage;
  final VoidCallback onPickFile;
  final ValueChanged<String> onRemoveAttachment;
  final VoidCallback onSend;
  final VoidCallback onSendTab;
  final VoidCallback onSendEscape;

  @override
  State<AgentMessageComposer> createState() => _AgentMessageComposerState();
}

class _AgentMessageComposerState extends State<AgentMessageComposer> {
  FocusNode? _ownedFocusNode;

  FocusNode get _effectiveFocusNode =>
      widget.focusNode ?? (_ownedFocusNode ??= FocusNode());

  @override
  void initState() {
    super.initState();
    _effectiveFocusNode.addListener(_handleFocusChanged);
  }

  @override
  void didUpdateWidget(covariant AgentMessageComposer oldWidget) {
    super.didUpdateWidget(oldWidget);
    if (oldWidget.focusNode == widget.focusNode) {
      return;
    }
    oldWidget.focusNode?.removeListener(_handleFocusChanged);
    _ownedFocusNode?.removeListener(_handleFocusChanged);
    if (widget.focusNode != null) {
      _ownedFocusNode?.dispose();
      _ownedFocusNode = null;
    }
    _effectiveFocusNode.addListener(_handleFocusChanged);
  }

  @override
  void dispose() {
    widget.focusNode?.removeListener(_handleFocusChanged);
    _ownedFocusNode
      ?..removeListener(_handleFocusChanged)
      ..dispose();
    super.dispose();
  }

  void _handleFocusChanged() {
    if (mounted) {
      setState(() {});
    }
  }

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;
    final strings = CcbMobileLocalizations.of(context);
    final mediaQuery = MediaQuery.of(context);
    final availableHeight =
        mediaQuery.size.height - mediaQuery.viewInsets.bottom;
    final showQuickKeyToolbar =
        _effectiveFocusNode.hasFocus &&
        mediaQuery.orientation == Orientation.portrait &&
        availableHeight >= 320;
    if (widget.collapsed) {
      final draft = widget.controller.text.trim();
      return Material(
        key: const ValueKey('agent-chat-composer-collapsed'),
        color: colorScheme.surface,
        clipBehavior: Clip.antiAlias,
        shape: RoundedRectangleBorder(
          side: BorderSide(color: colorScheme.outlineVariant),
          borderRadius: BorderRadius.circular(8),
        ),
        child: InkWell(
          onTap: widget.onExpand,
          child: Padding(
            padding: const EdgeInsets.fromLTRB(12, 4, 6, 4),
            child: SizedBox(
              height: 40,
              child: Row(
                children: [
                  const Icon(Icons.edit_note, size: 20),
                  const SizedBox(width: 8),
                  Expanded(
                    child: Text(
                      draft.isEmpty
                          ? strings.messageAgent(widget.agentName)
                          : draft,
                      key: const ValueKey('agent-chat-composer-collapsed-text'),
                      maxLines: 1,
                      overflow: TextOverflow.ellipsis,
                    ),
                  ),
                  IconButton(
                    key: const ValueKey('agent-composer-expand-action'),
                    tooltip: strings.openMessageInput,
                    visualDensity: VisualDensity.compact,
                    constraints: const BoxConstraints.tightFor(
                      width: 40,
                      height: 40,
                    ),
                    padding: EdgeInsets.zero,
                    onPressed: widget.onExpand,
                    icon: const Icon(Icons.keyboard_arrow_up),
                  ),
                ],
              ),
            ),
          ),
        ),
      );
    }
    return DecoratedBox(
      key: const ValueKey('agent-chat-composer'),
      decoration: BoxDecoration(
        color: colorScheme.surface,
        border: Border.all(color: colorScheme.outlineVariant),
        borderRadius: BorderRadius.circular(8),
      ),
      child: Padding(
        padding: const EdgeInsets.symmetric(horizontal: 6, vertical: 4),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: [
            if (widget.draftAttachments.isNotEmpty)
              Padding(
                padding: const EdgeInsets.only(bottom: 8),
                child: Wrap(
                  key: const ValueKey('agent-attachment-tray'),
                  spacing: 8,
                  runSpacing: 4,
                  children: [
                    for (final attachment in widget.draftAttachments)
                      _DraftAttachmentPreview(
                        attachment: attachment,
                        onRemove:
                            () => widget.onRemoveAttachment(attachment.fileId),
                      ),
                  ],
                ),
              ),
            if (showQuickKeyToolbar)
              Padding(
                padding: const EdgeInsets.only(bottom: 4),
                child: Row(
                  key: const ValueKey('agent-quick-key-toolbar'),
                  mainAxisAlignment: MainAxisAlignment.start,
                  children: [
                    IconButton(
                      key: const ValueKey('agent-quick-key-tab'),
                      tooltip: strings.sendTab,
                      visualDensity: VisualDensity.compact,
                      constraints: const BoxConstraints.tightFor(
                        width: 36,
                        height: 36,
                      ),
                      padding: EdgeInsets.zero,
                      onPressed:
                          widget.isSending || !widget.sendEnabled
                              ? null
                              : widget.onSendTab,
                      icon: const Icon(Icons.keyboard_tab),
                    ),
                    IconButton(
                      key: const ValueKey('agent-quick-key-esc'),
                      tooltip: strings.sendEsc,
                      visualDensity: VisualDensity.compact,
                      constraints: const BoxConstraints.tightFor(
                        width: 36,
                        height: 36,
                      ),
                      padding: EdgeInsets.zero,
                      onPressed:
                          widget.isSending || !widget.sendEnabled
                              ? null
                              : widget.onSendEscape,
                      icon: const Icon(Icons.close),
                    ),
                  ],
                ),
              ),
            Row(
              crossAxisAlignment: CrossAxisAlignment.end,
              children: [
                IconButton(
                  key: const ValueKey('agent-attachment-button'),
                  tooltip: strings.attachFile,
                  visualDensity: VisualDensity.compact,
                  constraints: const BoxConstraints.tightFor(
                    width: 40,
                    height: 40,
                  ),
                  padding: EdgeInsets.zero,
                  onPressed:
                      widget.isSending || !widget.sendEnabled
                          ? null
                          : () => _showAttachmentSheet(context),
                  icon: const Icon(Icons.attach_file),
                ),
                Expanded(
                  child: Shortcuts(
                    shortcuts: const {
                      SingleActivator(LogicalKeyboardKey.enter):
                          _SendMessageIntent(),
                    },
                    child: Actions(
                      actions: {
                        _SendMessageIntent: CallbackAction<_SendMessageIntent>(
                          onInvoke: (_) {
                            if (!widget.isSending && widget.sendEnabled) {
                              widget.onSend();
                            }
                            return null;
                          },
                        ),
                      },
                      child: TextField(
                        key: const ValueKey('agent-message-composer'),
                        controller: widget.controller,
                        focusNode: _effectiveFocusNode,
                        minLines: 1,
                        maxLines: 5,
                        textInputAction: TextInputAction.newline,
                        decoration: InputDecoration(
                          border: InputBorder.none,
                          isDense: true,
                          contentPadding: const EdgeInsets.symmetric(
                            vertical: 8,
                          ),
                          hintText: strings.messageAgent(widget.agentName),
                        ),
                      ),
                    ),
                  ),
                ),
                if (widget.collapsible)
                  IconButton(
                    key: const ValueKey('agent-composer-collapse-action'),
                    tooltip: strings.collapseMessageInput,
                    visualDensity: VisualDensity.compact,
                    constraints: const BoxConstraints.tightFor(
                      width: 40,
                      height: 40,
                    ),
                    padding: EdgeInsets.zero,
                    onPressed: widget.onCollapse,
                    icon: const Icon(Icons.keyboard_arrow_down),
                  ),
                Semantics(
                  label:
                      widget.sendEnabled
                          ? strings.sendMessage
                          : (widget.sendDisabledReason ??
                              'Sending is unavailable'),
                  child: IconButton.filled(
                    key: const ValueKey('agent-message-send-button'),
                    tooltip:
                        widget.isSending
                            ? strings.sendingMessage
                            : (widget.sendEnabled
                                ? strings.sendMessage
                                : (widget.sendDisabledReason ??
                                    'Sending is unavailable')),
                    visualDensity: VisualDensity.compact,
                    constraints: const BoxConstraints.tightFor(
                      width: 40,
                      height: 40,
                    ),
                    padding: EdgeInsets.zero,
                    onPressed:
                        widget.isSending || !widget.sendEnabled
                            ? null
                            : widget.onSend,
                    icon:
                        widget.isSending
                            ? const SizedBox.square(
                              dimension: 18,
                              child: CircularProgressIndicator(strokeWidth: 2),
                            )
                            : const Icon(Icons.send),
                  ),
                ),
              ],
            ),
          ],
        ),
      ),
    );
  }

  void _showAttachmentSheet(BuildContext context) {
    final strings = CcbMobileLocalizations.of(context);
    showModalBottomSheet<void>(
      context: context,
      showDragHandle: true,
      builder: (context) {
        return SafeArea(
          child: Column(
            key: const ValueKey('agent-attachment-sheet'),
            mainAxisSize: MainAxisSize.min,
            children: [
              ListTile(
                key: const ValueKey('agent-attachment-pick-image'),
                leading: const Icon(Icons.image_outlined),
                title: Text(strings.photoImage),
                onTap: () {
                  Navigator.of(context).pop();
                  widget.onPickImage();
                },
              ),
              ListTile(
                key: const ValueKey('agent-attachment-pick-file'),
                leading: const Icon(Icons.attach_file),
                title: Text(strings.file),
                onTap: () {
                  Navigator.of(context).pop();
                  widget.onPickFile();
                },
              ),
              ListTile(
                key: const ValueKey('agent-attachment-cancel'),
                leading: const Icon(Icons.close),
                title: Text(strings.cancel),
                onTap: () => Navigator.of(context).pop(),
              ),
            ],
          ),
        );
      },
    );
  }
}

class _SendMessageIntent extends Intent {
  const _SendMessageIntent();
}

class _DraftAttachmentPreview extends StatelessWidget {
  const _DraftAttachmentPreview({
    required this.attachment,
    required this.onRemove,
  });

  final CcbMessageAttachment attachment;
  final VoidCallback onRemove;

  @override
  Widget build(BuildContext context) {
    final strings = CcbMobileLocalizations.of(context);
    final localPath = attachment.localPath;
    if (attachment.isImage && localPath != null) {
      return SizedBox(
        key: ValueKey('agent-attachment-image-preview-${attachment.fileId}'),
        width: 72,
        child: Stack(
          children: [
            ClipRRect(
              borderRadius: BorderRadius.circular(6),
              child: Image.file(
                File(localPath),
                width: 64,
                height: 64,
                fit: BoxFit.cover,
                errorBuilder:
                    (context, error, stackTrace) => const SizedBox(
                      width: 64,
                      height: 64,
                      child: Icon(Icons.image_not_supported_outlined),
                    ),
              ),
            ),
            Positioned(
              right: 0,
              top: 0,
              child: IconButton.filledTonal(
                key: ValueKey('agent-attachment-remove-${attachment.fileId}'),
                visualDensity: VisualDensity.compact,
                constraints: const BoxConstraints.tightFor(
                  width: 28,
                  height: 28,
                ),
                padding: EdgeInsets.zero,
                tooltip: strings.removeAttachment,
                onPressed: onRemove,
                icon: const Icon(Icons.close, size: 16),
              ),
            ),
          ],
        ),
      );
    }
    return InputChip(
      key: ValueKey('agent-attachment-chip-${attachment.fileId}'),
      avatar: Icon(
        attachment.isImage ? Icons.image_outlined : Icons.description_outlined,
        size: 18,
      ),
      label: ConstrainedBox(
        constraints: const BoxConstraints(maxWidth: 180),
        child: Text(
          '${attachment.fileName} (${_formatBytes(attachment.sizeBytes)})',
          overflow: TextOverflow.ellipsis,
        ),
      ),
      onDeleted: onRemove,
      deleteIcon: Icon(
        Icons.close,
        key: ValueKey('agent-attachment-remove-${attachment.fileId}'),
      ),
    );
  }
}

String _formatBytes(int bytes) {
  if (bytes >= 1024 * 1024) {
    return '${(bytes / (1024 * 1024)).toStringAsFixed(1)} MB';
  }
  if (bytes >= 1024) {
    return '${(bytes / 1024).toStringAsFixed(1)} KB';
  }
  return '$bytes B';
}
