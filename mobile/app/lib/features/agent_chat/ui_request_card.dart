import 'package:flutter/material.dart';

import '../../l10n/ccb_mobile_localizations.dart';
import '../../models/omp_runtime.dart';

/// An ephemeral native request, separate from conversation and composer state.
class UiRequestCard extends StatefulWidget {
  const UiRequestCard({required this.request, required this.onSubmit,
    this.stale = false, super.key});
  final OmpPendingUiRequest request;
  final Future<void> Function(String value) onSubmit;
  final bool stale;

  @override
  State<UiRequestCard> createState() => _UiRequestCardState();
}

class _UiRequestCardState extends State<UiRequestCard> {
  int? _selected;
  late final TextEditingController _editor;
  bool _other = false;
  bool _sending = false;
  bool _submitted = false;
  bool _failed = false;

  OmpPendingUiRequest get r => widget.request;
  bool get editorKind => r.kind == 'editor';
  bool get disabled => _sending || _submitted || _failed || widget.stale;

  @override
  void initState() {
    super.initState();
    final initial = r.initialIndex;
    _selected = initial != null && initial >= 0 && initial < r.options.length ? initial : null;
    _editor = TextEditingController(text: r.prefill ?? '');
  }

  @override
  void dispose() { _editor.dispose(); super.dispose(); }

  Future<void> _submit() async {
    if (disabled) return;
    final value = editorKind || _other ? _editor.text : r.options[_selected!].label;
    if (value.length > 512) return;
    setState(() { _sending = true; });
    try {
      await widget.onSubmit(value);
      if (mounted) setState(() { _submitted = true; });
    } catch (_) {
      // A lost acknowledgement is not safe to replay. Wait for a fresh prompt.
      if (mounted) setState(() { _failed = true; });
    } finally {
      if (mounted) setState(() { _sending = false; });
    }
  }

  @override
  Widget build(BuildContext context) {
    final chinese = CcbMobileLocalizations.of(context).isChinese;
    final canSubmit = !disabled && (editorKind || _other
        ? _editor.text.trim().isNotEmpty && _editor.text.length <= 512
        : _selected != null);
    return Card(
      key: const ValueKey('omp-ui-request-card'),
      margin: const EdgeInsets.fromLTRB(8, 4, 8, 4),
      child: ConstrainedBox(constraints: const BoxConstraints(maxHeight: 300), child: SingleChildScrollView(
        padding: const EdgeInsets.all(12), child: Column(crossAxisAlignment: CrossAxisAlignment.stretch, children: [
          if (r.title.isNotEmpty) Text(r.title, style: Theme.of(context).textTheme.titleMedium),
          if (r.helpText?.isNotEmpty == true) Padding(padding: const EdgeInsets.only(top: 4), child: Text(r.helpText!)),
          if (editorKind) ...[
            const SizedBox(height: 8), TextField(key: const ValueKey('omp-ui-editor'), controller: _editor,
              maxLength: 512, minLines: 2, maxLines: 5, enabled: !disabled, onChanged: (_) => setState(() {})),
          ] else ...[
            const SizedBox(height: 4),
            for (var i = 0; i < r.options.length; i++) ListTile(
              key: ValueKey('omp-ui-option-$i'), dense: true, enabled: !disabled,
              selected: _selected == i && !_other, title: Text(r.options[i].label),
              subtitle: r.options[i].description == null ? null : Text(r.options[i].description!),
              leading: Icon(_selected == i && !_other ? Icons.radio_button_checked : Icons.radio_button_unchecked),
              trailing: r.checkedIndices.contains(i) ? const Icon(Icons.check) : null,
              onTap: disabled ? null : () => setState(() { _selected = i; _other = false; }),
            ),
            ListTile(key: const ValueKey('omp-ui-other'), dense: true, enabled: !disabled,
              selected: _other, leading: Icon(_other ? Icons.radio_button_checked : Icons.radio_button_unchecked),
              title: Text(chinese ? '其他 / 自定义' : 'Other / custom'),
              onTap: disabled ? null : () => setState(() { _other = true; _selected = null; })),
            if (_other) TextField(key: const ValueKey('omp-ui-other-field'), controller: _editor,
              maxLength: 512, enabled: !disabled, onChanged: (_) => setState(() {}), maxLines: 3),
          ],
          if (_failed) Text(chinese ? '回复未确认；请刷新并查看最新请求，不要重复发送。' : 'Response not confirmed. Refresh to check the latest request; do not resend.',
            key: const ValueKey('omp-ui-error'), style: TextStyle(color: Theme.of(context).colorScheme.error)),
          if (widget.stale) Text(chinese ? '请求已过期或连接已断开。' : 'Request is stale or disconnected.', key: const ValueKey('omp-ui-stale')),
          if (_submitted) Text(chinese ? '回复已发送' : 'Response sent', key: const ValueKey('omp-ui-sent')),
          const SizedBox(height: 8), Align(alignment: Alignment.centerRight, child: FilledButton(
            key: const ValueKey('omp-ui-submit'), onPressed: canSubmit ? _submit : null,
            child: _sending ? const SizedBox(width: 16, height: 16, child: CircularProgressIndicator(strokeWidth: 2)) : Text(chinese ? '提交' : 'Submit'),
          )),
        ]),
      )),
    );
  }
}
