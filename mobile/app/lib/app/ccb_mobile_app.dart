import 'dart:async';
import 'dart:io';

import 'package:flutter/material.dart';
import 'package:flutter_localizations/flutter_localizations.dart';

import '../features/project_home/project_home_screen.dart';
import '../l10n/ccb_mobile_localizations.dart';
import '../pairing/gateway_pairing.dart';
import '../repository/fake_mobile_ccb_repository.dart';
import 'app_theme.dart';
import 'app_update.dart';
import 'background_connection.dart';
import 'chat_background.dart';
import 'mobile_network_status.dart';
import 'terminal_shortcut_preferences.dart';

class CcbMobileApp extends StatefulWidget {
  const CcbMobileApp({
    this.enableProductOnboarding = true,
    this.themePreferenceStore,
    this.backgroundConnectionPreferenceStore,
    this.backgroundConnectionPlatform,
    this.chatBackgroundStore,
    this.chatBackgroundPicker = pickCcbChatBackgroundImage,
    this.terminalShortcutPreferenceStore,
    this.mobileNetworkStatusPlatform,
    this.profileStore,
    this.updateService,
    this.automaticUpdateCheck = true,
    this.androidPlatformOverride,
    this.installApk = installCcbMobileApk,
    super.key,
  });

  final bool enableProductOnboarding;
  final CcbThemePreferenceStore? themePreferenceStore;
  final CcbBackgroundConnectionPreferenceStore?
  backgroundConnectionPreferenceStore;
  final BackgroundConnectionPlatform? backgroundConnectionPlatform;
  final CcbChatBackgroundStore? chatBackgroundStore;
  final CcbChatBackgroundPicker chatBackgroundPicker;
  final CcbTerminalShortcutPreferenceStore? terminalShortcutPreferenceStore;
  final MobileNetworkStatusPlatform? mobileNetworkStatusPlatform;
  final GatewayHostProfileStore? profileStore;
  final CcbMobileUpdateService? updateService;
  final bool automaticUpdateCheck;
  final bool? androidPlatformOverride;
  final Future<void> Function(File apk) installApk;

  @override
  State<CcbMobileApp> createState() => _CcbMobileAppState();
}

class _CcbMobileAppState extends State<CcbMobileApp> {
  final GlobalKey<NavigatorState> _navigatorKey = GlobalKey<NavigatorState>();
  late final CcbThemePreferenceStore _themePreferenceStore =
      widget.themePreferenceStore ?? FlutterCcbThemePreferenceStore();
  late final CcbBackgroundConnectionPreferenceStore
  _backgroundConnectionPreferenceStore =
      widget.backgroundConnectionPreferenceStore ??
      FlutterCcbBackgroundConnectionPreferenceStore();
  late final CcbTerminalShortcutPreferenceStore
  _terminalShortcutPreferenceStore =
      widget.terminalShortcutPreferenceStore ??
      FlutterCcbTerminalShortcutPreferenceStore();
  late final CcbChatBackgroundStore _chatBackgroundStore =
      widget.chatBackgroundStore ?? FlutterCcbChatBackgroundStore();
  CcbThemePreference _themePreference = CcbThemePreference.system;
  CcbChatBackgroundPreference? _chatBackgroundPreference;
  Future<void> _chatBackgroundOpacityWrite = Future<void>.value();
  CcbTerminalShortcutPreferences _terminalShortcutPreferences =
      CcbTerminalShortcutPreferences.defaults;
  Future<void> _terminalShortcutPreferenceWrite = Future<void>.value();
  bool _terminalShortcutPreferencesChangedLocally = false;
  bool _backgroundConnectionEnabled = false;
  bool _backgroundConnectionPreferenceLoaded = false;

  @override
  void initState() {
    super.initState();
    _loadThemePreference();
    _loadChatBackgroundPreference();
    _loadBackgroundConnectionPreference();
    _loadTerminalShortcutPreferences();
    WidgetsBinding.instance.addPostFrameCallback((_) {
      unawaited(_checkForUpdateOnLaunch());
    });
  }

  Future<void> _checkForUpdateOnLaunch() async {
    final isAndroid = widget.androidPlatformOverride ?? Platform.isAndroid;
    if (!widget.automaticUpdateCheck || !isAndroid) return;
    try {
      final service = widget.updateService ?? CcbMobileUpdateService();
      final result = await service.checkForUpdate();
      final release = result.release;
      final dialogContext = _navigatorKey.currentContext;
      if (!mounted || release == null || dialogContext == null) return;
      await _showUpdateDialog(dialogContext, service, release);
    } catch (_) {
      // Startup checks are best-effort and must never block the app.
    }
  }

  Future<void> _showUpdateDialog(
    BuildContext dialogContext,
    CcbMobileUpdateService service,
    CcbMobileRelease release,
  ) async {
    var downloading = false;
    String? errorMessage;
    await showDialog<void>(
      context: dialogContext,
      builder: (context) => StatefulBuilder(
        builder: (context, setDialogState) {
          final strings = CcbMobileLocalizations.of(context);
          return AlertDialog(
            title: Text(strings.updateAvailableTitle),
            content: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(strings.newVersionAvailable(release.version)),
                if (downloading) ...[
                  const SizedBox(height: 16),
                  const LinearProgressIndicator(),
                  const SizedBox(height: 8),
                  Text(strings.downloadingVersion(release.version)),
                ],
                if (errorMessage != null) ...[
                  const SizedBox(height: 12),
                  Text(
                    errorMessage!,
                    style: TextStyle(color: Theme.of(context).colorScheme.error),
                  ),
                ],
              ],
            ),
            actions: [
              TextButton(
                onPressed: downloading ? null : () => Navigator.of(context).pop(),
                child: Text(strings.later),
              ),
              FilledButton(
                key: const ValueKey('startup-update-install-button'),
                onPressed: downloading
                    ? null
                    : () async {
                        setDialogState(() {
                          downloading = true;
                          errorMessage = null;
                        });
                        try {
                          final apk = await service.downloadApk(release);
                          await widget.installApk(apk);
                          if (context.mounted) {
                            Navigator.of(context).pop();
                          }
                        } catch (_) {
                          if (context.mounted) {
                            setDialogState(() {
                              downloading = false;
                              errorMessage = strings.updateDownloadFailed;
                            });
                          }
                        }
                      },
                child: Text(strings.updateNow),
              ),
            ],
          );
        },
      ),
    );
  }

  Future<void> _loadThemePreference() async {
    final preference = await _themePreferenceStore.read();
    if (!mounted) {
      return;
    }
    setState(() {
      _themePreference = preference;
    });
  }

  void _setThemePreference(CcbThemePreference preference) {
    setState(() {
      _themePreference = preference;
    });
    unawaited(_themePreferenceStore.write(preference));
  }

  Future<void> _loadChatBackgroundPreference() async {
    CcbChatBackgroundPreference? preference;
    try {
      preference = await _chatBackgroundStore.read();
    } catch (_) {
      preference = null;
    }
    if (!mounted) {
      return;
    }
    setState(() {
      _chatBackgroundPreference = preference;
    });
  }

  Future<void> _chooseChatBackground() async {
    final selection = await widget.chatBackgroundPicker();
    if (selection == null) {
      return;
    }
    final preference = await _chatBackgroundStore.save(
      selection,
      surfaceOpacity:
          _chatBackgroundPreference?.surfaceOpacity ??
          ccbDefaultWorkspaceSurfaceOpacity,
    );
    if (!mounted) {
      return;
    }
    setState(() {
      _chatBackgroundPreference = preference;
    });
  }

  Future<void> _clearChatBackground() async {
    await _chatBackgroundStore.clear();
    if (!mounted) {
      return;
    }
    setState(() {
      _chatBackgroundPreference = null;
    });
  }

  Future<void> _setChatBackgroundSurfaceOpacity(double opacity) async {
    final current = _chatBackgroundPreference;
    if (current == null) {
      return;
    }
    final normalized = opacity.clamp(
      ccbMinWorkspaceSurfaceOpacity,
      ccbMaxWorkspaceSurfaceOpacity,
    ).toDouble();
    setState(() {
      _chatBackgroundPreference = current.copyWith(
        surfaceOpacity: normalized,
      );
    });
    _chatBackgroundOpacityWrite = _chatBackgroundOpacityWrite
        .then((_) async {
          try {
            final saved = await _chatBackgroundStore.updateSurfaceOpacity(
              normalized,
            );
            if (mounted && saved != null) {
              setState(() {
                _chatBackgroundPreference = saved;
              });
            }
          } catch (_) {
            // The in-memory value remains usable until the next app restart.
          }
        });
    await _chatBackgroundOpacityWrite;
  }

  Future<void> _loadBackgroundConnectionPreference() async {
    final enabled = await _backgroundConnectionPreferenceStore.read();
    if (!mounted) {
      return;
    }
    setState(() {
      _backgroundConnectionEnabled = enabled;
      _backgroundConnectionPreferenceLoaded = true;
    });
  }

  void _setBackgroundConnectionEnabled(bool enabled) {
    setState(() {
      _backgroundConnectionEnabled = enabled;
      _backgroundConnectionPreferenceLoaded = true;
    });
    unawaited(_backgroundConnectionPreferenceStore.write(enabled));
  }

  Future<void> _loadTerminalShortcutPreferences() async {
    CcbTerminalShortcutPreferences preferences;
    try {
      preferences = await _terminalShortcutPreferenceStore.read();
    } catch (_) {
      return;
    }
    if (!mounted || _terminalShortcutPreferencesChangedLocally) {
      return;
    }
    setState(() {
      _terminalShortcutPreferences = preferences;
    });
  }

  void _setTerminalShortcutPreferences(
    CcbTerminalShortcutPreferences preferences,
  ) {
    _terminalShortcutPreferencesChangedLocally = true;
    setState(() {
      _terminalShortcutPreferences = preferences;
    });
    _terminalShortcutPreferenceWrite = _terminalShortcutPreferenceWrite
        .then((_) => _terminalShortcutPreferenceStore.write(preferences))
        .catchError((_) {});
  }

  @override
  Widget build(BuildContext context) {
    final repository = FakeMobileCcbRepository.demo();
    return MaterialApp(
      navigatorKey: _navigatorKey,
      builder: (context, child) => CcbChatBackgroundScope(
        preference: _chatBackgroundPreference,
        onChoose: _chooseChatBackground,
        onClear: _clearChatBackground,
        onSurfaceOpacityChanged: _setChatBackgroundSurfaceOpacity,
        child: CcbTerminalShortcutPreferencesScope(
          preferences: _terminalShortcutPreferences,
          onChanged: _setTerminalShortcutPreferences,
          child: child ?? const SizedBox.shrink(),
        ),
      ),
      onGenerateTitle: (context) => CcbMobileLocalizations.of(context).appTitle,
      localizationsDelegates: GlobalMaterialLocalizations.delegates,
      supportedLocales: CcbMobileLocalizations.supportedLocales,
      theme: ccbLightTheme(),
      darkTheme: ccbDarkTheme(),
      themeMode: _themePreference.themeMode,
      home: ProjectHomeScreen(
        repository: repository,
        profileStore: widget.profileStore,
        showOnboardingWhenUnpaired: widget.enableProductOnboarding,
        autoActivateStoredProfile: widget.enableProductOnboarding,
        themePreference: _themePreference,
        onThemePreferenceChanged: _setThemePreference,
        backgroundConnectionEnabled: _backgroundConnectionEnabled,
        backgroundConnectionPreferenceLoaded:
            _backgroundConnectionPreferenceLoaded,
        onBackgroundConnectionEnabledChanged: _setBackgroundConnectionEnabled,
        backgroundConnectionPlatform: widget.backgroundConnectionPlatform,
        mobileNetworkStatusPlatform: widget.mobileNetworkStatusPlatform,
      ),
    );
  }
}
