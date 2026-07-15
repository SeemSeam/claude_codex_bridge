<div align="center">

# CCB 手机 App 来了！

**轻快的多 Agent TUI，稳定的跨 Provider 协作底座**<br>
**让 Codex、Claude、Gemini 等 CLI Agent 可见、可控、可接管地协同工作**

<p>
  <img src="https://img.shields.io/badge/version-8.1.7-orange.svg" alt="version">
  <img src="https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20WSL-lightgrey.svg" alt="platform">
  <img src="https://img.shields.io/badge/providers-17%20CLI%20families-0B7285.svg" alt="providers">
</p>

<p>
  <img src="https://img.shields.io/badge/Codex-111111?style=flat-square&logo=openai&logoColor=white" alt="Codex">
  <img src="https://img.shields.io/badge/Claude-D97757?style=flat-square&logo=anthropic&logoColor=white" alt="Claude">
  <img src="https://img.shields.io/badge/Gemini-4285F4?style=flat-square&logo=googlegemini&logoColor=white" alt="Gemini">
  <img src="https://img.shields.io/badge/Grok-000000?style=flat-square&logo=x&logoColor=white" alt="Grok CLI">
  <img src="https://img.shields.io/badge/Kimi-111111?style=flat-square&logo=moonshotai&logoColor=white" alt="Kimi">
  <img src="https://img.shields.io/badge/MiMo-FF6900?style=flat-square&logo=xiaomi&logoColor=white" alt="MiMo">
  <img src="https://img.shields.io/badge/Qwen-6A5CFF?style=flat-square" alt="Qwen">
  <img src="https://img.shields.io/badge/Cursor-111111?style=flat-square" alt="Cursor">
  <img src="https://img.shields.io/badge/Copilot-111111?style=flat-square&logo=githubcopilot&logoColor=white" alt="GitHub Copilot">
  <img src="https://img.shields.io/badge/Crush-FF5A5F?style=flat-square" alt="Crush">
  <img src="https://img.shields.io/badge/Kiro-6D5EF6?style=flat-square" alt="Kiro">
  <img src="https://img.shields.io/badge/Pi-111111?style=flat-square" alt="Pi">
  <img src="https://img.shields.io/badge/Z.ai-111111?style=flat-square" alt="Z.ai">
  <img src="https://img.shields.io/badge/OpenCode-111111?style=flat-square" alt="OpenCode">
  <img src="https://img.shields.io/badge/Antigravity-6D5EF6?style=flat-square&logo=google&logoColor=white" alt="Antigravity">
  <img src="https://img.shields.io/badge/Droid-3DDC84?style=flat-square&logo=android&logoColor=white" alt="Droid">
</p>

**中文** | [English](../README.md) | [日本語](ja.md) | [Français](fr.md) | [Deutsch](de.md) | [العربية](ar.md) | [Español](es.md) | [Português](pt.md) | [한국어](ko.md) | [Русский](ru.md)

[快速开始](#quick-start) · [Mobile App](#mobile-app) · [Rich 模式](#rich-mode) · [配置团队](#configure-agents) · [使用文档](../docs/manuals/user-guide/) · [开发文档](../docs/manuals/developer-guide/)

<p align="center">
  <img src="../assets/readme_v7/ccb-hero-zh-light.png" alt="CCB 可见多 Agent CLI 工作台" width="960">
</p>

</div>

<a id="why-ccb"></a>

## 为什么用 CCB？

- 强稳定的 agent 间通信能力，支持 `A -> B -> C`、`A,B -> C`、`A -> B,C` 等复杂协作关系。
- 每个 agent 都是完整原生终端，支持可见的界面排布和直接接管。
- 后台 daemon 持续运行，可以脱离前台界面保持项目状态。
- Hub 能力：一个命令同时并发运行多家 CLI provider。
- 手机远程控制器：跨 provider 语音操控、文件传输和远程终端访问。

<a id="how-to-install"></a>

## 如何安装

推荐使用 npm 安装或更新：

```bash
npm install -g @seemseam/ccb
```

安装完成后，后续更新直接使用 CCB 自带 updater：

```bash
ccb update
```

<details>
<summary><b>GitHub release 包和源码安装兜底</b></summary>

如果当前环境不方便使用 npm，可以到 [Releases](https://github.com/SeemSeam/claude_codex_bridge/releases) 下载与你的平台匹配的包，解压后安装：

```bash
tar -xzf ccb-*.tar.gz
cd ccb-*
./install.sh install
```

源码安装只建议开发或临时兜底使用：

```bash
git clone https://github.com/SeemSeam/claude_codex_bridge.git
cd claude_codex_bridge
./install.sh install
```

源码安装会让全局 `ccb` / `ask` 链接回当前 checkout。普通用户更建议使用 npm 包。

</details>

<a id="quick-start"></a>

## 快速开始

### 1. 启动

在工作目录执行：

```bash
ccb
```

如果启动时提示无法自动创建 `.ccb` 或找不到项目锚点，需要手动创建 `.ccb` 作为项目锚点：

```bash
mkdir -p .ccb
```

<a id="configure-agents"></a>

### 2. 配置工作台

空白项目现在会轻量启动：CCB 只打开一个 `main` window，并根据本机实际可用的 CLI（依次优先 Codex、Claude、Gemini，再到其他 provider）创建一个名为 `demo` 的 agent，不再默认挂载多 Agent 团队。

点击 CCB sidebar 左上角的 **⚙ 设置** 图标即可打开本地配置控制面；也可以在项目目录运行 `ccb config ui`。

<p align="center">
  <img src="../assets/readme_v7/config-control-panel.png" alt="CCB 配置控制面正在编辑默认 demo agent" width="960">
</p>

控制面可以配置 windows、pane 拆分、provider、模型、thinking 等级、API 覆盖、workspace、Rich 模式和 sidebar；保存前会先校验，并支持 reload dry-run 和受保护的热加载。保存后会生成 `.ccb/ccb.config`，将当前 provider 和拓扑固定为项目配置。

需要高级多 Agent 拓扑时，可以继续在控制面中可视化添加，或手动创建 `.ccb/ccb.config`。v2 `[windows]` 中的 `,` 和 `;` 分别控制 window 内的上下堆叠和左右分栏，例如 `A,B;C,D` 接近四宫格布局。

```toml
version = 2

[windows]
main = "main:codex"
work = "worker1:codex(worktree), worker2:claude(worktree)"
review = "reviewer:claude, qa:gemini"

[ui.sidebar]
mode = "every_window"
width = "15%"
bottom_height = 20
agents_height = "50%"
comms_height = "15%"
tips_height = "35%"
comms_limit = 3
```

验证配置并启动工作台：

```bash
ccb config validate
ccb
```

### 3. 开始协作

你可以直接在某个 agent pane 里输入，也可以让 agent 之间协作：

```text
/ask reviewer review the latest parser changes and list blocking issues.
```

也可以在工作编排中让 agent 自动调用 `/ask` 完成委派和交接。建议通过修改 agent 记忆或项目共享记忆 `.ccb/ccb_memory.md` 进行编排。

<a id="mobile-app"></a>

## 手机远程控制（Android）

推荐使用手机控制 CCB：可以接入所有 CCB 项目，控制每个 agent，语音输入，并传递文件。

```bash
ccb update mobile
```

该命令会指导你完成安装和配置。

<p align="center">
  <img src="../assets/readme_v7/mobile-control-chat.jpg" alt="CCB Mobile agent 对话" width="180">
  <img src="../assets/readme_v7/mobile-control-terminal.jpg" alt="CCB Mobile 终端控制" width="180">
  <img src="../assets/readme_v7/mobile-control-files.jpg" alt="CCB Mobile 文件传输" width="180">
  <img src="../assets/readme_v7/mobile-control-pairing.jpg" alt="CCB Mobile 配对和连接" width="180">
</p>

<details>
<summary><b>Mobile App 详情、安全边界和源码</b></summary>

桌面端 v8.1.7 不包含 CCB Mobile 更新；保留的 Android APK 版本为 v8.1.4：

- [下载 CCB Mobile v8.1.4 APK](https://github.com/SeemSeam/claude_codex_bridge/releases/download/v8.1.4/ccb-mobile-v8.1.4.apk)
- App 源码：[`mobile/app`](../mobile/app)
- 服务端 gateway 源码：[`lib/mobile_gateway`](../lib/mobile_gateway)

手机端定位是远程控制真实服务器上的 CCB 项目。它可以从 server-wide mobile gateway 获取所有已挂载项目，切换 window/agent，渲染 agent 对话上下文，以 pane-native 输入方式发送文本，打开 terminal 视图，并通过认证 gateway 上传/下载图片和文档附件。

安全边界：

- CCB gateway 只绑定 loopback，例如 `127.0.0.1:8787`。
- 远程访问使用 Tailscale Serve，不启用 Tailscale Funnel。
- CCB 不保存 Tailscale 密码、OAuth token、admin API token，也不会自动修改 tailnet ACL/grants。
- 手机只获得 pairing profile 授权的 scope，例如 view、content、terminal、file upload 和 file download。

</details>

<a id="rich-mode"></a>

## Rich 富媒体终端

在终端查看文件结构、打开文件、编辑文档和预览媒体内容。

<p align="center">
  <img src="../assets/readme_v7/rich-workbench.png" alt="CCB rich 富媒体工作台在 WezTerm 中使用 Yazi 预览" width="860">
</p>

```bash
ccb update rich
```

rich 启用后，普通 `ccb` 会自动打开 rich WezTerm launcher，只有当当前已经处于 CCB 自己拉起的 rich WezTerm 中时才不会再次跳转；运行 `ccb uninstall rich` 可退回普通终端启动。

<a id="agent-roles"></a>

## Agent Roles Spec 规范和角色库

CCB 支持 [Agent Roles Spec](https://github.com/SeemSeam/agent-roles-spec)：这是一个 host-neutral 的专业 agent 封装规范，可把 skills、记忆和工具依赖打包成可安装、可挂载、可卸载的 Role Pack。该仓库同时也是公开角色库。

<details>
<summary><b>查看公开角色列表</b></summary>

| Role | 基本功能 |
| :--- | :--- |
| `agentroles.ccb_self` | CCB 自维护、配置辅助、运行诊断、受保护恢复和工作流编排。 |
| `agentroles.archi` | 架构审查、边界检查、耦合分析、可维护性风险和后续 gate 建议。 |
| `agentroles.frontend_engineer` | 前端设计与实现、设计系统、可访问性、浏览器 QA 和受审查的 AGY 委派。 |
| `agentroles.mobile_app_engineer` | iOS、Android、React Native、Expo、Flutter、SwiftUI、Jetpack Compose 等移动端设计与实现。 |
| `agentroles.mother` | Role 创建、Role source 审计、角色研究、蓝图设计和 Agent Roles 规范合规检查。 |
| `agentroles.su_ccb` | SU-CCB 工作流操作，覆盖需求分析、计划、派发、审查 gate、归档和恢复。 |

</details>

<a id="config-memory"></a>

## 配置和共享记忆

普通项目配置推荐直接使用左上角的 **⚙ 设置** 控制面。如果希望由 Agent 辅助设计配置或诊断运行状态，`ccb_self` 仍作为可选 Role Pack 提供，可以用 `ccb roles add agentroles.ccb_self:codex` 添加。

`.ccb/ccb_memory.md` 是项目级共享记忆文档，适合记录团队协作规则、项目约束、长期上下文和 agent 交接约定。把跨 agent 的稳定信息放在这里，比把同一段说明复制到多个 provider 私有记忆里更可靠。

<a id="contact"></a>

## 联系方式

- Email: `bfly123@126.com`
- [Telegram group & contact / TG 群与联系](https://t.me/+BKn03v8I_ehmYzRk)
- 微信: `seemseam-com`

<p align="center">
  <img src="../assets/weixin.jpg" alt="微信群" width="240">
</p>

<a id="community"></a>

## 社区和致谢

感谢 [Linux.do 社区](https://linux.do) 在测试、反馈和讨论中的支持。

感谢 [tmux-agent-sidebar](https://github.com/hiroppy/tmux-agent-sidebar) 提供的 sidebar 思路和启发。

<a id="release-notes"></a>

## 新版本记录

<details open>
<summary><b>v8.1.7</b> - 紧急 ask/reply 投递修复</summary>

- 保持受管 Codex ask/reply 投递绑定到目标会话和请求锚点。
- 修复已接受的 reply-delivery 确认被误判为空任务回复并反复重排队。
- 仅发布桌面端与 npm 包；本次不包含 CCB Mobile 更新。

</details>

<details>
<summary><b>v8.1.6</b> - 紧急通讯可靠性修复</summary>

- 修复受管 Codex 会话重绑定与请求级投递确认。
- 活跃 mailbox 工作未完成时禁止清理 provider context。
- 仅发布桌面端与 npm 包；本次不包含 CCB Mobile 更新。

</details>

<details>
<summary><b>v8.1.5</b> - 已撤回</summary>

- 因公开版本说明披露未发布内容而撤回。

</details>

<details open>
<summary><b>v8.1.4</b> - Codex subagent 隔离与 Grok 原生 skills</summary>

- 阻止 Codex 原生 subagent rollout 抢占 CCB 请求绑定，也不能替换权威父会话和父 turn。
- 内置 subagent 的活动、消息和完成事件只留在父 agent 的协作流程内，不再返回给 CCB caller。
- Python runtime 与 Rust accelerator 使用相同隔离规则，并通过真实登录态 `spawn_agent` 回归确认 caller 只收到父 agent 最终回复。
- 为每个受管 Grok home 独立注入原生 `ask` 与 `ccb-clear` skills，并且只开放这两个命令所需的精确权限。
- 启动前刷新继承的系统 Grok 登录态，并通过每个 agent 的可见原生 Grok 会话处理 CCB 请求；双 Grok 真实测试已通过可见 ask、结果回收、指定目标 clear 和 clear 后隔离验证。

</details>

<details open>
<summary><b>v8.1.3</b> - Mobile 交互可靠性与 Grok 完成判定</summary>

- 合并流式回复到同一个工作气泡并保持气泡元素身份，避免对话刷新闪烁、重复消息和错误的工作中状态。
- 刷新期间稳定保留 agent/window 选择，保留 pane 真实 terminal scrollback，并要求用户明确激活键盘后才发送 terminal 输入。
- Android 配对改用内嵌 ML Kit 扫码器，并确保 release 混淆后相关类仍被保留。
- 过滤 Codex 本地控制 transcript 条目，并要求 Grok 提供原生 turn-completion 证据后才结束托管请求。

</details>

<details>
<summary><b>v8.1.2</b> - Mobile 对话可靠性与安装器证书恢复</summary>

- 加固 Mobile invalidation 恢复、快照、实时对话刷新、附件回显合并和任务完成通知。
- 恢复展开消息滚动与项目文件链接，简化 terminal 快捷键、压缩控制栏，并移除重复 terminal 标题。
- 复用的托管 Python 环境会刷新旧版 pip 以获得系统证书支持，并且只在 truststore 后端实际可用时启用。
- 扩展 macOS DNS、代理、超时和证书错误的 HTTPS 镜像重试识别，不关闭 TLS 校验。

</details>

<details>
<summary><b>v8.1.1</b> - Mobile 实时恢复与 macOS 安装稳定性</summary>

- Mobile gateway 新增有界 SSE invalidation 流，项目、活动和对话变化可刷新权威状态，不再依赖活跃页面轮询。
- 新增有界只读快照、重连状态和自动恢复；gateway 中断期间保留所选 host、项目、agent、近期对话及完成通知。
- Mobile host 启动时可识别并安全接管匹配的旧 gateway 进程，避免升级期间出现重复监听。
- macOS release 更新会保留健康的托管 Python 环境，并在 TLS 或网络失败后通过可配置镜像重试安装 `watchdog`。

</details>

<details>
<summary><b>v8.1.0</b> - 配置控制面与轻量默认工作台</summary>

- 新增可视化项目配置控制面，可通过 sidebar 左上角的 **⚙ 设置** 或 `ccb config ui` 打开，支持配置校验、差异审查、保存、reload dry-run 和受保护的热加载。
- 空白项目现在只挂载一个名为 `demo` 的 agent，并自动选择本机首个可用 CLI；显式项目配置和用户配置仍可定义任意单 Agent 或多 Agent 拓扑。
- 新增 Grok CLI 托管支持、Kimi Code v0.23.1 readiness 兼容、OpenCode fresh session 修复，以及 Claude/Gemini hook launcher 可靠执行。
- 改进 CCB Mobile gateway profile 持久化、配对凭据保留、项目健康缓存、列表预热显示和 terminal UI 效率。
- 多语言 README 统一整理到 `README/`，加入真实配置控制面截图，并将 package、Mobile、workflow 和 release metadata 对齐到 8.1.0。

</details>

<details>
<summary><b>v8.0.19</b> - Mobile host 启动健康检查修复</summary>

- `ccb update mobile` 对 server-wide loopback `/v1/health` 使用更宽松的单次请求超时和总启动等待窗口，避免挂载项目较多时误判后台 gateway 不健康。
- 新增 regression test 覆盖健康响应晚于旧 0.5 秒请求超时时仍应启动成功。
- `ccb update mobile` 默认 APK 下载链接、README、package metadata 和 mobile app version metadata 对齐到 8.0.19。

</details>

<details>
<summary><b>v8.0.18</b> - Codex auth 投影与 Mobile host health 修复</summary>

- Codex managed `CODEX_HOME` materialize 阶段会同步 `auth.json`、`config.toml`、`company-codex-api-key`、`company-codex.config.toml`，以及 `config.toml` 中引用到的安全 auth/key/token sidecar 文件名。
- 新增 `.ccb-auth-projection.json` 证据 manifest，只记录来源/目标存在性、大小和 SHA256，不保存密钥明文。
- 显式 Codex API authority 会清理继承来的 auth sidecar，避免全局登录态和 agent-local API 配置混用。
- WSL 下 Codex 解析到 Windows interop 可执行文件时，`ccb doctor` 会标记 `reason=wsl_windows_interop_executable`。
- server-wide mobile 项目发现会容忍 stale project 记录，避免单个失效项目拖垮整体列表。
- README 角色列表改为折叠显示，并更新联系方式中的微信图片。
- `ccb update mobile` 默认 APK 下载链接、README、package metadata 和 mobile app version metadata 对齐到 8.0.18。

</details>

<details>
<summary><b>v8.0.17</b> - Ask 回复稳定性与 Mobile 更新修复</summary>

- Codex ask 回复检测改为基于无进展时间，长 session 文件持续写入时不会按提交时间误失败。
- 官方 session/log 缺失时返回可诊断的非成功状态，避免 silent stuck；明确 shutdown 时标记为 provider crashed。
- Mobile frontdesk 消息提交改走 ccbd ask job，不再直接写 pane。
- `ccb watch` 默认不再 10 秒超时，`ccb update mobile` 等长等待路径可以等到真实终态；显式 `CCB_WATCH_TIMEOUT_S` 仍会生效。
- `ccb update mobile` 默认 APK 下载链接、README、package metadata 和 mobile app version metadata 对齐到 8.0.17。

</details>

<details>
<summary><b>v8.0.16</b> - Mobile 重连与 pane 活动记录</summary>

- CCB Mobile Terminal 模式增加自动重连诊断与恢复路径，断线时会显示 Reconnecting，并在恢复后继续使用当前 agent pane。
- mobile gateway 在 pane 输入路径记录项目活动，保证 mobile 项目列表最近使用排序能感知 Terminal 输入。
- `ccb update mobile` 默认 APK 下载链接、README、package metadata 和 mobile release manifest 对齐到 8.0.16。

</details>

<details>
<summary><b>v8.0.12</b> - Release CI 可移植性与 README 多语言同步</summary>

- mobile host registry 测试现在把临时 Unix socket 放到短的 `/tmp/ccb-sock-*` 路径，避免 macOS CI 触发 `AF_UNIX path too long`。
- `ccb update mobile`、README 链接、package metadata 和 mobile release manifest 对齐到 8.0.12 APK。
- v8.0.12 引入了结构一致的多语言 README；当前多语言文件统一放在 [`README/`](./) 目录，中文版本为 [`zh.md`](zh.md)，根目录 `README.md` 为英文首页。

</details>

<details>
<summary><b>v8.0.0</b> - CCB Mobile Monorepo 发布</summary>

- Flutter 版 CCB Mobile 源码正式进入本仓库，并在 GitHub Release 中发布 Android APK。
- 新增 server-wide mobile 项目发现、配对、认证 gateway 路由、pane-native 消息输入、对话上下文渲染、terminal 访问，以及图片/文档上传下载能力。
- 将 `ccb update mobile` 提升为 Tailscale Tailnet onboarding 的统一入口，同时保持 gateway 仅监听 loopback，不启用 Funnel、不保存 token、不自动修改 ACL/grants。

</details>

<details>
<summary><b>v7.7.0</b> - Runtime Accelerator 发布加固</summary>

- Release artifacts 现在会携带可选 Rust `ccb-runtime-accelerator`，安装版 Codex agent 在预期存在 sidecar 时不再静默退回 Python 热路径。
- 当项目路径导致 Unix socket 路径过长时，accelerator socket 会自动落到短的 per-user runtime socket root。
- 加固 callback repair 和 Codex binding cache invalidation，并记录完整回归、长 idle Codex soak、Claude callback 和混合 provider 集成测试证据。

</details>

<details>
<summary><b>v7.6.19</b> - 长任务 ask 默认等待策略</summary>

- 普通长时间 `ask` 默认继续等待真实 provider/completion 结果，不再仅因 heartbeat 诊断自动 terminalize 为 `incomplete/heartbeat_timeout`。
- Codex、Claude、Gemini 的 pane-backed no-terminal timeout 默认改为显式 opt-in，仍保留显式 reliability timeout 策略。
- 已用 32 分钟 source-runtime ask smoke 验证：任务超过 30 分钟仍保持 running，随后以 `result_message` 完成，未出现 `heartbeat_timeout` 或 `incomplete` 证据。

</details>

完整历史请看 [CHANGELOG.md](../CHANGELOG.md)。
