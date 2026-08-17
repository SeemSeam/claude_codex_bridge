import 'package:flutter/widgets.dart';

class CcbMobileLocalizations {
  const CcbMobileLocalizations(this.locale);

  final Locale locale;

  static const supportedLocales = <Locale>[Locale('en'), Locale('zh')];

  static CcbMobileLocalizations of(BuildContext context) {
    return CcbMobileLocalizations(Localizations.localeOf(context));
  }

  bool get isChinese => locale.languageCode.toLowerCase() == 'zh';

  String get appTitle => 'CCB Mobile';

  String get connectTitle => isChinese ? '连接 CCB Mobile' : 'Connect CCB Mobile';

  String get connectDescription =>
      isChinese
          ? '连接方式由电脑端选择；手机只需扫码或粘贴连接码。'
          : 'Choose the route on the computer, then scan or paste its connection code.';

  String get runComputerCommandTitle =>
      isChinese ? '在电脑上运行一条命令' : 'Run one command on the computer';

  String get runComputerCommandBody =>
      isChinese
          ? '命令会让你在电脑端选择连接方式，然后生成二维码和连接码。'
          : 'Choose the connection route in the computer prompt; it then prints a QR and connection code.';

  String get scanQrTitle => isChinese ? '扫描二维码' : 'Scan the QR';

  String get scanQrBody =>
      isChinese
          ? '扫描电脑显示的二维码；地址和路由配置已经包含在其中。'
          : 'Scan the computer QR; it already contains the address and route configuration.';

  String get pairing => isChinese ? '正在配对' : 'Pairing';

  String get scanComputerQr => isChinese ? '扫描电脑二维码' : 'Scan computer QR';

  String get enterConnectionCode =>
      isChinese ? '输入连接码' : 'Enter connection code';

  String get connectionCodeSummary =>
      isChinese ? '无法扫码时使用' : 'Use when scanning is unavailable';

  String get connectionCode => isChinese ? '连接码' : 'Connection code';

  String get connectionCodeHint =>
      isChinese
          ? '粘贴电脑端完整输出的 ccb1_ 连接码'
          : 'Paste the complete ccb1_ code printed by the computer';

  String get connectWithCode => isChinese ? '使用连接码连接' : 'Connect with code';

  String get couldNotLoadProject =>
      isChinese ? '无法加载项目' : 'Could not load project';

  String get couldNotLoadProjects =>
      isChinese ? '无法加载项目列表' : 'Could not load projects';

  String get retry => isChinese ? '重试' : 'Retry';

  String get continueAnyway => isChinese ? '仍然继续' : 'Continue anyway';

  String get lanPairingWarningTitle =>
      isChinese
          ? '连接 LAN 前请检查手机网络'
          : 'Check the phone network before LAN pairing';

  String get lanPairingWarningIntroduction =>
      isChinese
          ? '这个连接码使用电脑的局域网地址。当前手机网络可能无法访问它。'
          : 'This code uses the computer\'s local-network address, which may not be reachable from the phone\'s current network.';

  String get lanPhoneOfflineTitle =>
      isChinese ? '手机当前没有可用网络' : 'The phone is offline';

  String get lanPhoneOfflineBody =>
      isChinese
          ? '请打开 Wi-Fi，并确认手机和电脑连接到同一个可信局域网。'
          : 'Turn on Wi-Fi and connect the phone and computer to the same trusted local network.';

  String get lanLocalNetworkRequiredTitle =>
      isChinese ? '请连接与电脑相同的 Wi-Fi' : 'Connect to the computer\'s Wi-Fi';

  String lanLocalNetworkRequiredBody(String gatewayHost) {
    return isChinese
        ? '当前未检测到 Wi-Fi 或以太网，仅使用移动数据通常无法访问 $gatewayHost。若手机正在共享热点，请确认电脑已连接该热点。'
        : 'No Wi-Fi or Ethernet connection was detected. Mobile data normally cannot reach $gatewayHost. If this phone is sharing a hotspot, confirm the computer joined it.';
  }

  String get lanVpnMayBlockTitle =>
      isChinese ? 'VPN 可能阻止局域网连接' : 'A VPN may block the LAN connection';

  String lanVpnMayBlockBody(String gatewayHost) {
    return isChinese
        ? '请允许 VPN 访问本地网络，或暂时关闭 VPN 后重试 $gatewayHost。'
        : 'Allow local-network access in the VPN, or temporarily disable it and retry $gatewayHost.';
  }

  String get lanGatewayUnreachableTitle =>
      isChinese
          ? '已连接本地网络，但电脑端不可达'
          : 'Local network connected, but the computer is unreachable';

  String lanGatewayUnreachableBody(String gatewayHost) {
    return isChinese
        ? '确认手机和电脑在同一 Wi-Fi，未使用访客/设备隔离网络，防火墙允许 $gatewayHost。若电脑 IP 已变化，请在电脑重新运行 ccb update mobile 并扫码。'
        : 'Check that both devices use the same Wi-Fi, guest/client isolation is off, and the firewall allows $gatewayHost. If the computer IP changed, rerun ccb update mobile and scan the new code.';
  }

  String get rePair => isChinese ? '重新配对' : 'Re-pair';

  String get deleteMessage => isChinese ? '删除' : 'Delete';

  String get collapseMessage => isChinese ? '折叠消息' : 'Collapse message';

  String get expandMessage => isChinese ? '展开消息' : 'Expand message';

  String get newContext => isChinese ? '新上下文' : 'New context';

  String get backToProjects => isChinese ? '返回项目列表' : 'Back to projects';

  String get useFakeDemo => isChinese ? '使用演示模式' : 'Use fake demo';

  String get backToSetup => isChinese ? '返回设置' : 'Back to setup';

  String get refreshProjects => isChinese ? '刷新项目' : 'Refresh projects';

  String get noCcbProjectsFound =>
      isChinese ? '未找到 CCB 项目' : 'No CCB projects found';

  String get noAgents => isChinese ? '没有 agent' : 'No agents';

  String get noAgent => isChinese ? '无 agent' : 'no agent';

  String get notifications => isChinese ? '通知' : 'Notifications';

  String get diagnostics => isChinese ? '诊断' : 'Diagnostics';

  String get settings => isChinese ? '设置' : 'Settings';

  String get theme => isChinese ? '主题' : 'Theme';

  String get themeDescription =>
      isChinese
          ? '选择适合长时间查看对话、日志和项目状态的显示风格。'
          : 'Choose a display style for long chat, log, and project sessions.';

  String get themeSystem => isChinese ? '跟随系统' : 'System';

  String get themeLight => isChinese ? '浅色' : 'Light';

  String get themeDark => isChinese ? '深色' : 'Dark';

  String get chatBackground => isChinese ? '工作区背景' : 'Workspace background';

  String get chatBackgroundDescription =>
      isChinese
          ? '选择一张本机图片作为聊天和终端的全屏背景。图片只保存在此设备，不会上传到 CCB。'
          : 'Choose a local image as the full-screen background for chats and terminals. It stays on this device and is never uploaded to CCB.';

  String get chatBackgroundSurfaceOpacity =>
      isChinese ? '内容表面不透明度' : 'Content surface opacity';

  String get chooseChatBackground => isChinese ? '选择图片' : 'Choose image';

  String get replaceChatBackground => isChinese ? '更换图片' : 'Replace image';

  String get removeChatBackground =>
      isChinese ? '移除工作区背景' : 'Remove workspace background';

  String get chatBackgroundTooLarge =>
      isChinese ? '图片不能超过 20 MB。' : 'The image must be 20 MB or smaller.';

  String get chatBackgroundUnsupported =>
      isChinese
          ? '请选择 PNG、JPEG、GIF、WebP 或 BMP 图片。'
          : 'Choose a PNG, JPEG, GIF, WebP, or BMP image.';

  String get chatBackgroundCouldNotSave =>
      isChinese
          ? '无法保存工作区背景图片。'
          : 'Could not save the workspace background image.';

  String get terminalShortcuts => isChinese ? '终端快捷键' : 'Terminal shortcuts';

  String get terminalSettings => isChinese ? '终端设置' : 'Terminal settings';

  String get terminalTextSize => isChinese ? '终端字体' : 'Terminal text size';

  String get restoreDefaults => isChinese ? '恢复默认' : 'Restore defaults';

  String get reorder => isChinese ? '调整顺序' : 'Reorder';

  String get backgroundConnection =>
      isChinese ? '保持后台连接' : 'Keep connected in background';

  String get backgroundConnectionDescription =>
      isChinese
          ? '配对后使用系统常驻通知保持实时事件连接。会增加耗电，可随时关闭。'
          : 'After pairing, keep the live event connection active with a persistent system notification. This uses more battery and can be disabled anytime.';

  String get backgroundConnectionCouldNotStart =>
      isChinese
          ? '无法启动后台连接，设置已关闭。'
          : 'Could not start the background connection. The setting was disabled.';

  String get backgroundConnectionSystemSettings =>
      isChinese ? '系统后台权限' : 'System background access';

  String get backgroundConnectionSystemRestricted =>
      isChinese
          ? 'Android 已限制此 App 的后台活动。点击打开系统设置。'
          : 'Android is restricting background activity for this app. Tap to open system settings.';

  String get backgroundConnectionSystemOptimized =>
      isChinese
          ? '系统仍在进行电池优化，部分设备可能中断连接。点击检查系统设置。'
          : 'Battery optimization is active and may interrupt the connection on some devices. Tap to review system settings.';

  String get backgroundConnectionSystemUnrestricted =>
      isChinese
          ? '系统未限制后台活动。点击可查看系统设置。'
          : 'System background activity is unrestricted. Tap to review system settings.';

  String get backgroundConnectionSystemUnknown =>
      isChinese
          ? '无法读取系统后台限制状态。点击打开系统设置。'
          : 'Could not read the system background restriction state. Tap to open system settings.';

  String get backgroundConnectionSystemSettingsCouldNotOpen =>
      isChinese
          ? '无法打开 Android 系统设置。'
          : 'Could not open Android system settings.';

  String get mobileUpdates =>
      isChinese ? 'CCB Mobile 更新' : 'CCB Mobile updates';

  String currentVersion(String version) {
    return isChinese ? '当前版本：$version' : 'Current version: $version';
  }

  String get mobileUpdatesDescription =>
      isChinese
          ? '启动时会自动检查新版本，也可以在这里手动检查。'
          : 'Updates are checked automatically at startup, or you can check manually here.';

  String get mobileUpdateInstallNote =>
      isChinese
          ? '覆盖安装会保留已配对资料。若 Android 提示签名冲突，说明曾安装不同签名的测试包，需要一次性卸载后再安装正式包。'
          : 'Cover-installing preserves paired data. If Android reports a signature conflict, an older test APK used a different signature and must be uninstalled once before installing the official build.';

  String get checkForUpdates => isChinese ? '检查更新' : 'Check for updates';

  String get checkingForUpdates => isChinese ? '正在检查' : 'Checking';

  String get alreadyLatestVersion =>
      isChinese ? '当前已是最新版本。' : 'You are up to date.';

  String newVersionAvailable(String version) =>
      isChinese ? '发现新版本 $version。' : 'Version $version is available.';

  String get downloadAndInstall => isChinese ? '下载并安装' : 'Download and install';

  String get downloadingUpdate => isChinese ? '正在下载' : 'Downloading';

  String downloadingVersion(String version) =>
      isChinese
          ? '正在下载 $version 并校验安装包…'
          : 'Downloading and verifying $version…';

  String get androidInstallerOpened =>
      isChinese
          ? '安装包已校验，已打开 Android 安装器。'
          : 'APK verified. Android installer opened.';

  String get updateCheckFailed =>
      isChinese
          ? '检查更新失败，请检查网络或打开发布页。'
          : 'Update check failed. Check your network or open the release page.';

  String get updateDownloadFailed =>
      isChinese
          ? '更新下载或校验失败，请重试或打开发布页。'
          : 'Update download or verification failed. Retry or open the release page.';

  String get openReleasePage => isChinese ? '打开发布页' : 'Open release page';

  String get updateAvailableTitle =>
      isChinese ? '发现 CCB Mobile 更新' : 'CCB Mobile update available';

  String get later => isChinese ? '稍后' : 'Later';

  String get updateNow => isChinese ? '立即更新' : 'Update now';

  String get openApkDownload => openReleasePage;

  String get couldNotOpenUpdateUrl =>
      isChinese ? '无法打开更新下载链接' : 'Could not open update download';

  String get projects => isChinese ? '项目' : 'Projects';

  String get openTerminal => isChinese ? '打开终端' : 'Open Terminal';

  String get computerTerminal => isChinese ? '电脑终端' : 'Computer terminal';

  String get newTerminal => isChinese ? '新建终端' : 'New terminal';

  String get closeTerminal => isChinese ? '关闭当前终端' : 'Close terminal';

  String closeTerminalQuestion(String name) =>
      isChinese
          ? '终止 $name 中运行的 shell？'
          : 'Terminate the shell running in $name?';

  String shellName(int index) => isChinese ? '终端 $index' : 'Shell $index';

  String get maximumTerminalsReached =>
      isChinese ? '最多可同时打开 6 个终端' : 'Up to 6 terminals can be open';

  String get hostTerminalAccessUnavailable =>
      isChinese
          ? '当前配对未启用电脑终端权限，请重新配对'
          : 'Re-pair to enable computer terminal access';

  String get chooseTerminalProject =>
      isChinese ? '选择项目和终端' : 'Choose a project and terminal';

  String get windows => isChinese ? '窗口' : 'Windows';

  String get agents => isChinese ? 'Agent' : 'Agents';

  String get activeWindow => isChinese ? '当前活动窗口' : 'Active window';

  String get windowTerminal => isChinese ? '窗口当前 pane' : 'Window active pane';

  String get noTerminalTargets =>
      isChinese ? '这个项目没有可用终端' : 'No terminals are available for this project';

  String get terminalAccessUnavailable =>
      isChinese
          ? '当前配对未启用终端权限'
          : 'Terminal access is not enabled for this pairing';

  String get returnToChat => isChinese ? '返回对话' : 'Return to Chat';

  String messageAgent(String agentName) {
    return isChinese ? '给 $agentName 发消息' : 'Message $agentName';
  }

  String get openMessageInput => isChinese ? '展开消息输入框' : 'Open message input';

  String get collapseMessageInput =>
      isChinese ? '折叠消息输入框' : 'Collapse message input';

  String get attachFile => isChinese ? '添加附件' : 'Attach file';

  String get sendTab => isChinese ? '发送 Tab' : 'Send Tab';

  String get sendEsc => isChinese ? '发送 Esc' : 'Send Esc';

  String get sendMessage => isChinese ? '发送消息' : 'Send message';

  String get sendingMessage => isChinese ? '正在发送' : 'Sending message';

  String get photoImage => isChinese ? '图片' : 'Photo/Image';

  String get file => isChinese ? '文件' : 'File';

  String get cancel => isChinese ? '取消' : 'Cancel';

  String get close => isChinese ? '关闭' : 'Close';

  String get open => isChinese ? '打开' : 'Open';

  String get removeAttachment => isChinese ? '移除附件' : 'Remove attachment';

  String get openAttachment => isChinese ? '打开附件' : 'Open attachment';

  String get downloadAttachment => isChinese ? '下载附件' : 'Download attachment';

  String openAttachmentQuestion(String fileName) {
    return isChinese
        ? '使用系统应用打开 $fileName？'
        : 'Open $fileName with another app?';
  }

  String openUrlQuestion(String url) {
    return isChinese
        ? '使用浏览器或其他应用打开这个链接？\n$url'
        : 'Open this link with a browser or another app?\n$url';
  }

  String get openUrl => isChinese ? '打开链接' : 'Open link';

  String get couldNotOpenUrl => isChinese ? '无法打开链接' : 'Could not open link';

  String get refreshConversation => isChinese ? '刷新对话' : 'Refresh conversation';

  String get providerControl => isChinese ? '模型与用量' : 'Model and usage';

  String get providerModel => isChinese ? '模型' : 'Model';

  String get providerSelectModel => isChinese ? '选择模型' : 'Select model';

  String get providerThinking => isChinese ? '思考强度' : 'Thinking';

  String get providerSessionUsage => isChinese ? '当前会话用量' : 'Session usage';

  String get providerAccountQuota => isChinese ? '账户配额' : 'Account quota';

  String get providerUsageUnavailable => isChinese ? '暂不可用' : 'Unavailable';

  String get providerRestartRequired =>
      isChinese ? '重启 Agent 后生效' : 'Applies after agent restart';

  String get providerPendingRestart =>
      isChinese ? '等待 Agent 重启' : 'Pending agent restart';

  String get providerPendingShort => isChinese ? '待重启' : 'pending restart';

  String providerConfigured(String model) =>
      isChinese ? '已配置：$model' : 'Configured: $model';

  String providerContextUsage(String used, String maximum) =>
      isChinese ? '$used / $maximum 上下文' : '$used / $maximum context';

  String providerInputTokens(String value) =>
      isChinese ? '输入 $value' : 'Input $value';

  String providerCachedTokens(String value) =>
      isChinese ? '缓存 $value' : 'Cached $value';

  String providerOutputTokens(String value) =>
      isChinese ? '输出 $value' : 'Output $value';

  String get providerSave => isChinese ? '保存选择' : 'Save selection';

  String get providerApply => isChinese ? '应用' : 'Apply';

  String get providerSaving => isChinese ? '正在保存' : 'Saving';

  String get providerRefresh =>
      isChinese ? '刷新模型与用量' : 'Refresh model and usage';

  String get providerConfirmTitle =>
      isChinese ? '应用模型设置？' : 'Apply model settings?';

  String get providerConfirmBody =>
      isChinese
          ? '当前任务不会中断；新设置会在 Agent 下次重启后生效。'
          : 'The current task will not be interrupted. The new setting applies after the next agent restart.';

  String get providerScopeRequired =>
      isChinese
          ? '当前配对未授权修改模型，请重新配对后再试。'
          : 'This pairing cannot change models. Re-pair to grant access.';

  String get providerHostUpdateRequired =>
      isChinese
          ? '电脑端 CCB 版本不支持模型控制。请在电脑执行 ccb update，然后重新连接。'
          : 'The computer CCB version does not support model controls. Run ccb update on the computer, then reconnect.';

  String get providerRequestRejected =>
      isChinese
          ? '电脑端拒绝了这次设置。请刷新模型状态后重试。'
          : 'The computer rejected this setting. Refresh the model state and try again.';

  String get providerNoModels => isChinese ? '没有可选模型' : 'No selectable models';

  String get providerUsageDetails =>
      isChinese ? '查看用量详情' : 'View usage details';

  String providerThinkingOption(String option) {
    final normalized = option.trim().toLowerCase();
    if (isChinese) {
      return switch (normalized) {
        'off' => '关闭',
        'minimal' => '最低',
        'low' => '低',
        'medium' => '中',
        'high' => '高',
        'xhigh' || 'extra_high' || 'extra-high' => '超高',
        'max' => '最高',
        'ultra' => '极致',
        _ => option,
      };
    }
    return switch (normalized) {
      'off' => 'Off',
      'minimal' => 'Minimal',
      'low' => 'Low',
      'medium' => 'Medium',
      'high' => 'High',
      'xhigh' || 'extra_high' || 'extra-high' => 'Extra high',
      'max' => 'Max',
      'ultra' => 'Ultra',
      _ => option,
    };
  }

  String get searchModels => isChinese ? '搜索模型' : 'Search models';

  String get newMessages => isChinese ? '新消息' : 'New messages';

  String get communicating => isChinese ? '通讯中' : 'Communicating';

  String agentCompleted(String agentName) {
    return isChinese ? '$agentName 已完成' : '$agentName completed';
  }

  String executionStatus(String label) {
    if (!isChinese) {
      return label;
    }
    return switch (label) {
      'Idle' => '空闲',
      'Working' => 'Working',
      'Exception' => '异常',
      _ => label,
    };
  }

  String get stopProject => isChinese ? '停止项目' : 'Stop project';

  String stopProjectQuestion(String projectName) {
    return isChinese ? '停止 $projectName？' : 'Stop $projectName?';
  }

  String get stop => isChinese ? '停止' : 'Stop';

  String get runtime => isChinese ? '运行模式' : 'Runtime';

  String runtimeModeLabel(String label) {
    if (!isChinese) {
      return label;
    }
    return switch (label) {
      'Fake' => '演示',
      'Paired' => '已配对',
      _ => label,
    };
  }

  String get gatewayProfile => isChinese ? '网关配置' : 'Gateway profile';

  String get checking => isChinese ? '检查中' : 'Checking';

  String get checkRoute => isChinese ? '检查路由' : 'Check Route';

  String get checkingRoute => isChinese ? '正在检查路由' : 'Checking route';

  String get routeUnchecked => isChinese ? '路由未检查' : 'Route unchecked';
}
