import 'dart:async';
import 'dart:math';

import 'package:flutter/material.dart';

import '../../l10n/ccb_mobile_localizations.dart';
import '../../models/ccb_agent.dart';
import '../../models/ccb_provider_control.dart';
import '../../repository/mobile_ccb_repository.dart';
import '../../transport/http_gateway_transport.dart';
import '../../transport/relay_socket_gateway_transport.dart';

// Compact Provider selection and usage behavior aligns with Paseo at pinned
// commit b599d38, adapted to Flutter and CCB's restart-required lifecycle.
Future<bool> showProviderControlSheet(
  BuildContext context, {
  required MobileCcbProviderControlRepository repository,
  required String projectId,
  required CcbAgent agent,
}) async {
  return await showModalBottomSheet<bool>(
        context: context,
        isScrollControlled: true,
        showDragHandle: true,
        builder: (context) {
          return ProviderControlSheet(
            repository: repository,
            projectId: projectId,
            agent: agent,
          );
        },
      ) ??
      false;
}

class ProviderControlSheet extends StatefulWidget {
  const ProviderControlSheet({
    required this.repository,
    required this.projectId,
    required this.agent,
    super.key,
  });

  final MobileCcbProviderControlRepository repository;
  final String projectId;
  final CcbAgent agent;

  @override
  State<ProviderControlSheet> createState() => _ProviderControlSheetState();
}

class _ProviderControlSheetState extends State<ProviderControlSheet> {
  final _searchController = TextEditingController();
  CcbProviderControlDetails? _details;
  Object? _error;
  String? _model;
  String? _thinking;
  var _loading = true;
  var _saving = false;
  var _loadGeneration = 0;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _searchController.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    final generation = ++_loadGeneration;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final details = await widget.repository.getAgentProviderControl(
        projectId: widget.projectId,
        agentName: widget.agent.name,
      );
      if (!mounted || generation != _loadGeneration) {
        return;
      }
      setState(() {
        _details = details;
        _model =
            details.control.configuredModel ??
            details.control.activeModel ??
            (details.catalog.models.isEmpty
                ? null
                : details.catalog.models.first.id);
        _thinking =
            details.control.configuredThinking ??
            details.control.activeThinking;
        _loading = false;
      });
      _normalizeThinking();
    } catch (error) {
      if (!mounted || generation != _loadGeneration) {
        return;
      }
      setState(() {
        _error = error;
        _loading = false;
      });
    }
  }

  void _normalizeThinking() {
    final model = _selectedModel;
    if (model == null || model.reasoningLevels.isEmpty) {
      _thinking = null;
      return;
    }
    if (!model.reasoningLevels.contains(_thinking)) {
      _thinking = model.defaultReasoningLevel ?? model.reasoningLevels.first;
    }
  }

  CcbProviderModel? get _selectedModel {
    final wanted = _model;
    if (wanted == null) {
      return null;
    }
    for (final model
        in _details?.catalog.models ?? const <CcbProviderModel>[]) {
      if (model.id == wanted) {
        return model;
      }
    }
    return null;
  }

  Future<void> _applySelection({
    required String model,
    required String? thinking,
  }) async {
    final details = _details;
    final revision = details?.configRevision;
    if (details == null || revision == null || _saving) {
      return;
    }
    if (model == _model && thinking == _thinking) {
      return;
    }
    final strings = CcbMobileLocalizations.of(context);
    if (details.control.mutationMode == 'restart_required') {
      final confirmed = await showDialog<bool>(
        context: context,
        builder: (context) {
          return AlertDialog(
            title: Text(strings.providerConfirmTitle),
            content: Text(strings.providerConfirmBody),
            actions: [
              TextButton(
                onPressed: () => Navigator.of(context).pop(false),
                child: Text(strings.cancel),
              ),
              FilledButton(
                key: const ValueKey('confirm-provider-settings-save'),
                onPressed: () => Navigator.of(context).pop(true),
                child: Text(strings.providerApply),
              ),
            ],
          );
        },
      );
      if (confirmed != true || !mounted) {
        return;
      }
    }
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await widget.repository.updateAgentProviderSettings(
        projectId: widget.projectId,
        agentName: widget.agent.name,
        model: model,
        thinking: thinking,
        expectedRevision: revision,
        expectedNamespaceEpoch: details.namespaceEpoch,
        expectedProvider: details.control.provider,
        expectedRuntimeRevision: details.control.runtimeRevision,
        idempotencyKey: _idempotencyKey(),
      );
      if (!mounted) {
        return;
      }
      Navigator.of(context).pop(true);
    } catch (error) {
      if (!mounted) {
        return;
      }
      setState(() {
        _error = error;
      });
    } finally {
      if (mounted) {
        setState(() {
          _saving = false;
        });
      }
    }
  }

  Future<void> _selectModel(CcbProviderModel model) async {
    final thinking =
        model.id == _model && model.reasoningLevels.contains(_thinking)
            ? _thinking
            : model.defaultReasoningLevel ??
                (model.reasoningLevels.isEmpty
                    ? null
                    : model.reasoningLevels.first);
    await _applySelection(model: model.id, thinking: thinking);
  }

  Future<void> _selectThinking() async {
    final model = _selectedModel;
    if (model == null || model.reasoningLevels.isEmpty || _saving) {
      return;
    }
    final strings = CcbMobileLocalizations.of(context);
    final selected = await showModalBottomSheet<String>(
      context: context,
      showDragHandle: true,
      builder: (context) {
        return SafeArea(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(20, 0, 20, 8),
                child: Align(
                  alignment: Alignment.centerLeft,
                  child: Text(
                    strings.providerThinking,
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                ),
              ),
              const Divider(height: 1),
              for (final option in model.reasoningLevels)
                ListTile(
                  key: ValueKey('provider-thinking-option-$option'),
                  leading: const Icon(Icons.psychology_alt_outlined),
                  title: Text(strings.providerThinkingOption(option)),
                  trailing:
                      option == _thinking ? const Icon(Icons.check) : null,
                  onTap: () => Navigator.of(context).pop(option),
                ),
            ],
          ),
        );
      },
    );
    if (selected != null && mounted) {
      await _applySelection(model: model.id, thinking: selected);
    }
  }

  Future<void> _showUsage() {
    final details = _details;
    if (details == null) {
      return Future.value();
    }
    return showModalBottomSheet<void>(
      context: context,
      isScrollControlled: true,
      showDragHandle: true,
      builder: (context) {
        return _ProviderUsageSheet(
          repository: widget.repository,
          projectId: widget.projectId,
          agentName: widget.agent.name,
          details: details,
        );
      },
    );
  }

  @override
  Widget build(BuildContext context) {
    final strings = CcbMobileLocalizations.of(context);
    final media = MediaQuery.of(context);
    return Material(
      color: Theme.of(context).colorScheme.surface,
      child: SafeArea(
        child: SizedBox(
          key: const ValueKey('provider-control-sheet'),
          height: min(720, media.size.height * 0.84),
          child: Column(
            children: [
              Padding(
                padding: const EdgeInsets.fromLTRB(20, 0, 8, 8),
                child: Row(
                  children: [
                    const Icon(Icons.tune),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Text(
                            strings.providerSelectModel,
                            style: Theme.of(context).textTheme.titleMedium,
                          ),
                          Text(
                            '${widget.agent.name} · ${providerLabel(widget.agent.provider)}',
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                            style: Theme.of(context).textTheme.bodySmall,
                          ),
                        ],
                      ),
                    ),
                    IconButton(
                      key: const ValueKey('provider-control-refresh'),
                      tooltip: strings.providerRefresh,
                      onPressed: _loading ? null : _load,
                      icon: const Icon(Icons.refresh),
                    ),
                    IconButton(
                      tooltip: strings.cancel,
                      onPressed: () => Navigator.of(context).pop(false),
                      icon: const Icon(Icons.close),
                    ),
                  ],
                ),
              ),
              const Divider(height: 1),
              Expanded(child: _body(strings)),
            ],
          ),
        ),
      ),
    );
  }

  Widget _body(CcbMobileLocalizations strings) {
    if (_loading && _details == null) {
      return const Center(
        key: ValueKey('provider-control-loading'),
        child: CircularProgressIndicator(),
      );
    }
    final details = _details;
    if (details == null) {
      return _ErrorState(error: _error, onRetry: _load);
    }
    final query = _searchController.text.trim().toLowerCase();
    final models = [
      for (final model in details.catalog.models)
        if (query.isEmpty ||
            model.id.toLowerCase().contains(query) ||
            model.label.toLowerCase().contains(query))
          model,
    ];
    final hasUsage =
        details.control.usage != null ||
        details.control.capabilities.accountQuota;
    return Column(
      children: [
        if (_saving)
          const LinearProgressIndicator(
            key: ValueKey('provider-control-saving'),
          ),
        Padding(
          padding: const EdgeInsets.fromLTRB(20, 14, 20, 10),
          child: Column(
            children: [
              _RuntimeIdentity(control: details.control),
              if (details.control.provider == 'omp' &&
                  details.control.mutationMode == 'native')
                Padding(
                  padding: const EdgeInsets.only(top: 8),
                  child: Text(
                    strings.isChinese
                        ? '直接切换当前 OMP 会话；不重启、不修改项目配置。运行中或有桌面草稿时拒绝切换。'
                        : 'Changes the current OMP session without restart or project configuration changes. Busy sessions and desktop drafts block switching.',
                    key: const ValueKey('omp-native-model-control-note'),
                    style: Theme.of(context).textTheme.bodySmall,
                  ),
                ),
              if (details.control.hasPendingChange) ...[
                const SizedBox(height: 10),
                _PendingRestartBanner(control: details.control),
              ],
              if (details.catalog.modelSelectable &&
                  details.catalog.models.isNotEmpty) ...[
                const SizedBox(height: 12),
                TextField(
                  key: const ValueKey('provider-model-search'),
                  controller: _searchController,
                  decoration: InputDecoration(
                    hintText: strings.searchModels,
                    prefixIcon: const Icon(Icons.search),
                    isDense: true,
                  ),
                  onChanged: (_) => setState(() {}),
                ),
              ],
            ],
          ),
        ),
        const Divider(height: 1),
        Expanded(
          child:
              !details.catalog.modelSelectable
                  ? const SizedBox.shrink()
                  : models.isEmpty
                  ? Center(child: Text(strings.providerNoModels))
                  : ListView.separated(
                    key: const ValueKey('provider-model-list'),
                    itemCount: models.length,
                    separatorBuilder: (_, _) => const Divider(height: 1),
                    itemBuilder: (context, index) {
                      final model = models[index];
                      final selected =
                          model.id == (details.control.activeModel ?? _model);
                      final pending =
                          !selected &&
                          model.id ==
                              (details.control.pendingModel ??
                                  (details.control.restartPending
                                      ? details.control.configuredModel
                                      : null));
                      return ListTile(
                        key: ValueKey('provider-model-option-${model.id}'),
                        leading: const Icon(Icons.memory_outlined),
                        title: Text(model.label),
                        subtitle: _modelSubtitle(model),
                        trailing:
                            selected
                                ? const Icon(Icons.check)
                                : pending
                                ? const Icon(Icons.schedule_outlined)
                                : null,
                        selected: selected,
                        enabled: !_saving,
                        onTap: _saving ? null : () => _selectModel(model),
                      );
                    },
                  ),
        ),
        if ((_selectedModel?.reasoningLevels ?? const <String>[]).isNotEmpty ||
            hasUsage ||
            details.catalog.modelSelectable) ...[
          const Divider(height: 1),
          if ((_selectedModel?.reasoningLevels ?? const <String>[]).isNotEmpty)
            ListTile(
              key: const ValueKey('provider-thinking-trigger'),
              leading: const Icon(Icons.psychology_alt_outlined),
              title: Text(strings.providerThinking),
              subtitle:
                  _thinking == null
                      ? null
                      : Text(strings.providerThinkingOption(_thinking!)),
              trailing: const Icon(Icons.chevron_right),
              enabled: !_saving,
              onTap: _saving ? null : _selectThinking,
            ),
          if (hasUsage)
            ListTile(
              key: const ValueKey('provider-usage-trigger'),
              leading: const Icon(Icons.data_usage_outlined),
              title: Text(strings.providerSessionUsage),
              subtitle: Text(
                details.control.usage?.contextUtilization == null
                    ? strings.providerUsageDetails
                    : strings.providerContextUsage(
                      formatTokenCount(
                        details.control.usage?.contextWindowUsedTokens,
                      ),
                      formatTokenCount(
                        details.control.usage?.contextWindowMaxTokens,
                      ),
                    ),
              ),
              trailing: const Icon(Icons.chevron_right),
              onTap: _showUsage,
            ),
          if (details.catalog.modelSelectable)
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 2, 20, 12),
              child: Align(
                alignment: Alignment.centerLeft,
                child: Text(
                  details.control.mutationMode == 'restart_required'
                      ? strings.providerRestartRequired
                      : details.control.mutationMode,
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ),
            ),
        ],
        if (_error != null)
          Padding(
            padding: const EdgeInsets.fromLTRB(20, 0, 20, 12),
            child: _InlineError(error: _error!),
          ),
      ],
    );
  }

  Widget? _modelSubtitle(CcbProviderModel model) {
    final parts = <String>[];
    final description = model.description?.trim();
    if (description != null && description.isNotEmpty) {
      parts.add(description);
    }
    final context = model.contextWindowMaxTokens;
    if (context != null) {
      parts.add('${formatTokenCount(context)} context');
    }
    if (model.reasoningLevels.isNotEmpty) {
      parts.add(model.reasoningLevels.join(' · '));
    }
    return parts.isEmpty ? null : Text(parts.join('  |  '));
  }
}

class _RuntimeIdentity extends StatelessWidget {
  const _RuntimeIdentity({required this.control});

  final CcbProviderControl control;

  @override
  Widget build(BuildContext context) {
    final colorScheme = Theme.of(context).colorScheme;
    return Row(
      key: const ValueKey('provider-runtime-identity'),
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Icon(Icons.memory, color: colorScheme.primary),
        const SizedBox(width: 12),
        Expanded(
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              Text(
                providerIdentityText(control),
                style: Theme.of(context).textTheme.titleSmall,
              ),
              if (control.configuredModel != null &&
                  control.configuredModel != control.activeModel)
                Text(
                  CcbMobileLocalizations.of(
                    context,
                  ).providerConfigured(control.configuredModel!),
                  style: Theme.of(context).textTheme.bodySmall,
                ),
            ],
          ),
        ),
      ],
    );
  }
}

class _PendingRestartBanner extends StatelessWidget {
  const _PendingRestartBanner({required this.control});

  final CcbProviderControl control;

  @override
  Widget build(BuildContext context) {
    final strings = CcbMobileLocalizations.of(context);
    final colors = Theme.of(context).colorScheme;
    return Container(
      key: const ValueKey('provider-control-pending'),
      padding: const EdgeInsets.symmetric(horizontal: 12, vertical: 10),
      decoration: BoxDecoration(
        color: colors.secondaryContainer,
        border: Border.all(color: colors.secondary),
        borderRadius: BorderRadius.circular(6),
      ),
      child: Row(
        children: [
          const Icon(Icons.schedule, size: 18),
          const SizedBox(width: 8),
          Expanded(
            child: Text(
              '${strings.providerPendingRestart}: '
              '${control.pendingModel ?? control.configuredModel ?? ''}',
            ),
          ),
        ],
      ),
    );
  }
}

class _ProviderUsageSheet extends StatefulWidget {
  const _ProviderUsageSheet({
    required this.repository,
    required this.projectId,
    required this.agentName,
    required this.details,
  });

  final MobileCcbProviderControlRepository repository;
  final String projectId;
  final String agentName;
  final CcbProviderControlDetails details;

  @override
  State<_ProviderUsageSheet> createState() => _ProviderUsageSheetState();
}

class _ProviderUsageSheetState extends State<_ProviderUsageSheet> {
  CcbProviderAccountUsage? _accountUsage;
  Object? _quotaError;
  var _quotaLoading = false;

  @override
  void initState() {
    super.initState();
    _accountUsage = widget.details.accountUsage;
    if (widget.details.control.capabilities.accountQuota) {
      _quotaLoading = true;
      unawaited(_loadQuota());
    }
  }

  Future<void> _loadQuota() async {
    try {
      final usage = await widget.repository.getAgentProviderQuota(
        projectId: widget.projectId,
        agentName: widget.agentName,
      );
      if (!mounted) {
        return;
      }
      setState(() {
        _accountUsage = usage;
        _quotaError = null;
        _quotaLoading = false;
      });
    } catch (error) {
      if (!mounted) {
        return;
      }
      setState(() {
        _quotaError = error;
        _quotaLoading = false;
      });
    }
  }

  @override
  Widget build(BuildContext context) {
    final strings = CcbMobileLocalizations.of(context);
    final media = MediaQuery.of(context);
    return SafeArea(
      child: SizedBox(
        height: min(620, media.size.height * 0.72),
        child: Column(
          children: [
            Padding(
              padding: const EdgeInsets.fromLTRB(20, 0, 8, 8),
              child: Row(
                children: [
                  const Icon(Icons.data_usage_outlined),
                  const SizedBox(width: 12),
                  Expanded(
                    child: Text(
                      strings.providerControl,
                      style: Theme.of(context).textTheme.titleMedium,
                    ),
                  ),
                  IconButton(
                    key: const ValueKey('provider-usage-close'),
                    tooltip: strings.cancel,
                    onPressed: () => Navigator.of(context).pop(),
                    icon: const Icon(Icons.close),
                  ),
                ],
              ),
            ),
            const Divider(height: 1),
            Expanded(
              child: ListView(
                padding: const EdgeInsets.fromLTRB(20, 18, 20, 24),
                children: [
                  if (widget.details.control.usage != null)
                    _SessionUsageSection(usage: widget.details.control.usage!)
                  else
                    Text(strings.providerUsageUnavailable),
                  if (_quotaLoading) ...[
                    const SizedBox(height: 24),
                    _AccountQuotaLoading(),
                  ] else if (_accountUsage != null) ...[
                    const SizedBox(height: 24),
                    _AccountQuotaSection(usage: _accountUsage!),
                  ] else if (_quotaError != null ||
                      widget.details.control.capabilities.accountQuota) ...[
                    const SizedBox(height: 24),
                    const _AccountQuotaUnavailable(),
                  ],
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class _SessionUsageSection extends StatelessWidget {
  const _SessionUsageSection({required this.usage});

  final CcbAgentUsage usage;

  @override
  Widget build(BuildContext context) {
    final strings = CcbMobileLocalizations.of(context);
    final utilization = usage.contextUtilization;
    return Column(
      key: const ValueKey('provider-session-usage'),
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          strings.providerSessionUsage,
          style: Theme.of(context).textTheme.titleSmall,
        ),
        const SizedBox(height: 8),
        if (utilization != null) ...[
          LinearProgressIndicator(value: utilization),
          const SizedBox(height: 6),
          Text(
            strings.providerContextUsage(
              formatTokenCount(usage.contextWindowUsedTokens),
              formatTokenCount(usage.contextWindowMaxTokens),
            ),
          ),
        ],
        const SizedBox(height: 6),
        Wrap(
          spacing: 16,
          runSpacing: 4,
          children: [
            if (usage.inputTokens != null)
              Text(
                strings.providerInputTokens(
                  formatTokenCount(usage.inputTokens),
                ),
              ),
            if (usage.cachedInputTokens != null)
              Text(
                strings.providerCachedTokens(
                  formatTokenCount(usage.cachedInputTokens),
                ),
              ),
            if (usage.outputTokens != null)
              Text(
                strings.providerOutputTokens(
                  formatTokenCount(usage.outputTokens),
                ),
              ),
          ],
        ),
      ],
    );
  }
}

class _AccountQuotaSection extends StatelessWidget {
  const _AccountQuotaSection({required this.usage});

  final CcbProviderAccountUsage usage;

  @override
  Widget build(BuildContext context) {
    final strings = CcbMobileLocalizations.of(context);
    return Column(
      key: const ValueKey('provider-account-quota'),
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(
              child: Text(
                strings.providerAccountQuota,
                style: Theme.of(context).textTheme.titleSmall,
              ),
            ),
            if (usage.planLabel != null) Text(usage.planLabel!),
          ],
        ),
        const SizedBox(height: 8),
        if (usage.status != 'available')
          Text(strings.providerUsageUnavailable)
        else ...[
          for (final window in usage.windows) ...[
            _QuotaWindow(window: window),
            const SizedBox(height: 10),
          ],
          for (final balance in usage.balances)
            Text(
              '${balance.label}: ${balance.remaining?.toStringAsFixed(2) ?? '—'} '
              '${balance.unit ?? ''}',
            ),
          for (final detail in usage.details)
            Text('${detail.label}: ${detail.value}'),
        ],
      ],
    );
  }
}

class _AccountQuotaLoading extends StatelessWidget {
  @override
  Widget build(BuildContext context) {
    final strings = CcbMobileLocalizations.of(context);
    return Column(
      key: const ValueKey('provider-account-quota-loading'),
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          strings.providerAccountQuota,
          style: Theme.of(context).textTheme.titleSmall,
        ),
        const SizedBox(height: 8),
        const LinearProgressIndicator(),
      ],
    );
  }
}

class _AccountQuotaUnavailable extends StatelessWidget {
  const _AccountQuotaUnavailable();

  @override
  Widget build(BuildContext context) {
    final strings = CcbMobileLocalizations.of(context);
    return Column(
      key: const ValueKey('provider-account-quota-unavailable'),
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          strings.providerAccountQuota,
          style: Theme.of(context).textTheme.titleSmall,
        ),
        const SizedBox(height: 8),
        Text(strings.providerUsageUnavailable),
      ],
    );
  }
}

class _QuotaWindow extends StatelessWidget {
  const _QuotaWindow({required this.window});

  final CcbProviderUsageWindow window;

  @override
  Widget build(BuildContext context) {
    final used = window.usedPct;
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Row(
          children: [
            Expanded(child: Text(window.label)),
            Text(used == null ? '—' : '${used.toStringAsFixed(0)}%'),
          ],
        ),
        const SizedBox(height: 4),
        LinearProgressIndicator(value: used == null ? null : used / 100),
      ],
    );
  }
}

class _ErrorState extends StatelessWidget {
  const _ErrorState({required this.error, required this.onRetry});

  final Object? error;
  final VoidCallback onRetry;

  @override
  Widget build(BuildContext context) {
    final strings = CcbMobileLocalizations.of(context);
    return Center(
      key: const ValueKey('provider-control-error'),
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            const Icon(Icons.cloud_off_outlined, size: 36),
            const SizedBox(height: 12),
            Text(_friendlyError(strings, error)),
            const SizedBox(height: 12),
            OutlinedButton.icon(
              onPressed: onRetry,
              icon: const Icon(Icons.refresh),
              label: Text(strings.retry),
            ),
          ],
        ),
      ),
    );
  }
}

class _InlineError extends StatelessWidget {
  const _InlineError({required this.error});

  final Object error;

  @override
  Widget build(BuildContext context) {
    final strings = CcbMobileLocalizations.of(context);
    final colors = Theme.of(context).colorScheme;
    return Text(
      _friendlyError(strings, error),
      key: const ValueKey('provider-control-inline-error'),
      style: TextStyle(color: colors.error),
    );
  }
}

String providerIdentityText(CcbProviderControl control) {
  final parts = <String>[providerLabel(control.provider)];
  final model = control.displayModel;
  final thinking = control.displayThinking;
  if (model != null) {
    parts.add(model);
  }
  if (thinking != null) {
    parts.add(thinking);
  }
  return parts.join(' / ');
}

String providerLabel(String provider) {
  final text = provider.trim();
  if (text.isEmpty) {
    return 'Provider unavailable';
  }
  return text[0].toUpperCase() + text.substring(1);
}

String formatTokenCount(int? value) {
  if (value == null) {
    return '—';
  }
  if (value >= 1000000) {
    return '${(value / 1000000).toStringAsFixed(1)}M';
  }
  if (value >= 1000) {
    return '${(value / 1000).toStringAsFixed(1)}K';
  }
  return '$value';
}

String _friendlyError(CcbMobileLocalizations strings, Object? error) {
  if (error is GatewayHttpException && error.statusCode == 403) {
    return strings.providerScopeRequired;
  }
  if (error is RelayGatewayException) {
    if (error.statusCode == 403) {
      return strings.providerScopeRequired;
    }
    if (error.message == 'operation_not_allowed' ||
        error.message == 'relay_operation_not_allowed') {
      return strings.providerHostUpdateRequired;
    }
    if (error.message == 'gateway_rejected') {
      return strings.providerRequestRejected;
    }
  }
  return error?.toString() ?? strings.providerUsageUnavailable;
}

String _idempotencyKey() {
  final random = Random.secure();
  final suffix =
      List.generate(
        12,
        (_) => random.nextInt(256).toRadixString(16).padLeft(2, '0'),
      ).join();
  return 'provider-${DateTime.now().microsecondsSinceEpoch}-$suffix';
}
