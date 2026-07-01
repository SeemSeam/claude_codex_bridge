<div align="center">

# CCB

**基于agent平权思想设计**
**可见、可控的多 Agent 合作TUI工作台**

<p>
  <img src="https://img.shields.io/badge/version-8.0.7-orange.svg" alt="version">
  <img src="https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20WSL-lightgrey.svg" alt="platform">
  <img src="https://img.shields.io/badge/providers-15%20CLI%20families-0B7285.svg" alt="providers">
</p>

<p>
  <img src="https://img.shields.io/badge/Codex-111111?style=flat-square&logo=openai&logoColor=white" alt="Codex">
  <img src="https://img.shields.io/badge/Claude-D97757?style=flat-square&logo=anthropic&logoColor=white" alt="Claude">
  <img src="https://img.shields.io/badge/Gemini-4285F4?style=flat-square&logo=googlegemini&logoColor=white" alt="Gemini">
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

**中文** | [English](README.md)

[快速开始](#快速开始) · [v7 界面](#v7-界面速览) · [Rich 模式](#rich-mode-new) · [Mobile App](#mobile-app) · [配置团队](#配置-agent-团队) · [使用文档](docs/manuals/user-guide/) · [开发文档](docs/manuals/developer-guide/)

<p align="center">
  <img src="assets/readme_v7/ccb-hero-zh.png" alt="CCB v7 可见多 Agent CLI 工作台" width="960">
</p>

</div>

---

## 支持的 CLI

可在 `.ccb/ccb.config` 中按 agent 混用不同 CLI；实际可用性取决于本机 CLI 安装和账号权限。

<table>
  <tr>
    <td>Codex<br><code>codex</code></td>
    <td>Claude<br><code>claude</code></td>
    <td>Gemini<br><code>gemini</code></td>
    <td>Kimi<br><code>kimi</code></td>
    <td>MiMo<br><code>mimo</code></td>
  </tr>
  <tr>
    <td>Qwen<br><code>qwen</code></td>
    <td>Cursor<br><code>cursor</code></td>
    <td>GitHub Copilot CLI<br><code>copilot</code></td>
    <td>Crush<br><code>crush</code></td>
    <td>Kiro CLI<br><code>kiro</code></td>
  </tr>
  <tr>
    <td>Pi<br><code>pi</code></td>
    <td>Z.ai CLI<br><code>zai</code></td>
    <td>OpenCode<br><code>opencode</code></td>
    <td>Antigravity<br><code>agy</code></td>
    <td>Droid<br><code>droid</code></td>
  </tr>
</table>

**全新角色规范**：可把 skills、记忆和工具依赖封装进自封闭 Role Pack，快速生成可热加载、可卸载的专业 agent。

## 为什么用 CCB？

| 看得见 | 混合 provider | 项目级控制 |
| :--- | :--- | :--- |
| 每个 agent 都是真实终端，支持界面排布设计。 | 一个命令同时并发运行多 CLI。 | 稳定后台通信，支持多线并发任务编排。 |

## 快速开始

### 1. 安装或更新

新安装推荐使用 npm 包：

```bash
npm install -g @seemseam/ccb
```

安装完成后，后续更新直接使用 CCB 自带 updater：

```bash
ccb update
```

可选富媒体工作台用 `ccb update rich` 安装或刷新；它会优先下载并验证可封装的二进制，只把必要的终端、媒体和字体依赖交给平台包管理器安装：

```bash
ccb update rich
```

rich 启用后，普通 `ccb` 会自动打开 rich WezTerm launcher，只有当当前已经处于 CCB 自己拉起的 rich WezTerm 中时才不会再次跳转；运行 `ccb uninstall rich` 可退回普通终端启动。

可选手机控制端用下面的入口安装或刷新本机侧配置：

```bash
ccb update mobile
```

该命令会检查 mobile gateway 依赖，按需引导 Tailscale 登录/Serve 配置，
保持 gateway 仅监听 loopback，并打印当前 Android APK 下载链接和扫码配对步骤。

<details>
<summary><b>GitHub release 包和源码安装兜底</b></summary>

如果当前环境不方便使用 npm，可以到 [Releases](https://github.com/bfly123/claude_code_bridge/releases) 下载与你的平台匹配的包，解压后安装：

```bash
tar -xzf ccb-*.tar.gz
cd ccb-*
./install.sh install
```

源码安装只建议开发或临时兜底使用：

```bash
git clone https://github.com/bfly123/claude_code_bridge.git
cd claude_code_bridge
./install.sh install
```

源码安装会让全局 `ccb` / `ask` 链接回当前 checkout。普通用户更建议使用 npm 包。

</details>

开箱即用：在项目目录执行 `ccb` 即可使用。
如果启动时提示无法自动创建 `.ccb` 或找不到项目锚点，需要手动创建 `.ccb` 作为项目锚点：

```bash
mkdir -p .ccb
```

### 2. 创建项目配置

在项目根目录创建 `.ccb/ccb.config`。v7 推荐直接从多 window 拓扑理解配置：`[windows]` 定义窗口和 agent 分组，`agent:provider` 定义每个 agent 使用哪家 CLI，`(worktree)` 表示该 agent 使用独立 git worktree。

```toml
version = 2
entry_window = "main"

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

如果你不确定应该如何分组、要几个 worker、哪些 agent 用 worktree、哪些 agent 需要独立模型或 API，可以直接问当前工作台里的 `ccb_self`。它是 CCB 内置的 self-agent，理解 CCB 命令、配置权威层、roles、windows、reload 边界和常见恢复路径，并能用私有 `ccb-config` skill 和你讨论后生成配置方案。空白项目默认包含 `ccb_self`。

验证配置：

```bash
ccb config validate
```

启动工作台：

```bash
ccb
```

### 3. 开始协作

你可以直接在某个 agent pane 里输入，也可以让 agent 之间协作：

```text
/ask reviewer review the latest parser changes and list blocking issues.
```

也可以在工作编排中让 agent 自动调用 `/ask` 完成委派和交接。

### v7 界面速览

| 区域 | 说明 |
| :--- | :--- |
| Sidebar | 显示刷新/关闭 CCB 控件、window 和 agent 列表、内部通信状态，以及可在配置文件中修改并热加载的 tips。 |
| 鼠标操作 | 支持点击切换 window、agent 和 pane，也可在通信区刷新、kill 或删除条目。 |
| 工作区 | 每个 pane 都是真实 CLI；可以鼠标点击切换，也可以用 tmux 快捷键切换。 |
| 常用技巧 | `Ctrl-b h/j/k/l` 切换相邻 pane，`Ctrl-b z` 放大或还原当前 CLI pane。 |

<a id="rich-mode-new"></a>

### Rich 模式（NEW!）

运行 `ccb update rich` 安装可选富媒体工作台；它会尽量封装 Yazi 等二进制，并用 WezTerm 承载富媒体终端界面，提供 Markdown 渲染和图片/PDF/视频预览。安装后，普通 `ccb` 会自动打开 rich launcher，只有当当前已经处于 CCB 自己拉起的 rich WezTerm 中时才不会再次跳转；`ccb rich` 仍可作为显式启动入口。

<p align="center">
  <img src="assets/readme_v7/rich-workbench.png" alt="CCB rich 富媒体工作台在 WezTerm 中使用 Yazi 预览" width="860">
</p>

<a id="mobile-app"></a>

### Mobile App（Android Alpha）

CCB 8.0.7 已把 Flutter 版 CCB Mobile 源码放入 [`mobile/`](mobile/)，
并在 GitHub Release 中发布 Android APK：

- [下载 CCB Mobile v8.0.7 APK](https://github.com/bfly123/claude_code_bridge/releases/download/v8.0.7/ccb-mobile-v8.0.7.apk)
- App 源码：[`mobile/app`](mobile/app)
- 服务端 gateway 源码：[`lib/mobile_gateway`](lib/mobile_gateway)

手机端定位是远程控制真实服务器上的 CCB 项目。它可以从 server-wide
mobile gateway 获取所有已挂载项目，切换 window/agent，渲染 agent
对话上下文，以 pane-native 输入方式发送文本，打开 terminal 视图，并通过
认证 gateway 上传/下载图片和文档附件。

首次配置建议：

```bash
ccb update mobile
```

然后按终端提示：

1. 在桌面/服务器和手机上安装并登录同一个 Tailscale tailnet。
2. 在 Android 手机上安装 APK。
3. 在桌面/服务器运行 `ccb update mobile`。
4. 打开 CCB Mobile，扫描终端打印的配对二维码。

安全边界：

- CCB gateway 只绑定 loopback，例如 `127.0.0.1:8787`。
- 远程访问使用 Tailscale Serve，不启用 Tailscale Funnel。
- CCB 不保存 Tailscale 密码、OAuth token、admin API token，也不会自动修改
  tailnet ACL/grants。
- 手机只获得 pairing profile 授权的 scope，例如 view、content、terminal、
  file upload 和 file download。

### Agent Roles Spec 规范和角色库

CCB 支持 [Agent Roles Spec](https://github.com/SeemSeam/agent-roles-spec)：这是一个 host-neutral 的专业 agent 封装规范，可把专业角色打包成可安装、可挂载、可卸载的 Role Pack。该仓库同时也是公开角色库。

<details>
<summary><b>当前角色库</b></summary>

| Role | 基本功能 |
| :--- | :--- |
| `agentroles.ccb_self` | CCB 自维护、配置辅助、运行诊断、受保护恢复和工作流编排。 |
| `agentroles.archi` | 架构审查、边界检查、耦合分析、可维护性风险和后续 gate 建议。 |
| `agentroles.frontend_engineer` | 前端设计与实现、设计系统、可访问性、浏览器 QA 和受审查的 AGY 委派。 |
| `agentroles.mobile_app_engineer` | iOS、Android、React Native、Expo、Flutter、SwiftUI、Jetpack Compose 等移动端设计与实现。 |
| `agentroles.mother` | Role 创建、Role source 审计、角色研究、蓝图设计和 Agent Roles 规范合规检查。 |
| `agentroles.su_ccb` | SU-CCB 工作流操作，覆盖需求分析、计划、派发、审查 gate、归档和恢复。 |

</details>

### 联系方式

- Email: `bfly123@126.com`
- **[Telegram group & contact / TG 群与联系](https://t.me/+BKn03v8I_ehmYzRk)**
- 微信: `seemseam-com`

<p align="center">
  <img src="assets/weixin.jpg" alt="微信群" width="240">
</p>

---

## 更多阅读

初次使用先看“快速开始”即可。下面按用途折叠，需要哪块再展开。

| 主题 | 什么时候看 |
| :--- | :--- |
| 概念和定位 | 了解 CCB 是什么、多 agent 为什么有用，以及与其他方案的区别。 |
| 日常操作 | 查常用命令、tmux 基础操作和复制粘贴。 |
| 配置和角色 | 配 `.ccb/ccb.config`、Role Packs、`ccb_self` 配置助手。 |
| 协作与维护 | ask 路由、安装更新、FAQ 和致谢。 |
| 版本记录 | 查看当前 v7 重点和历史版本条目。 |

<details open>
<summary><b>概念和定位</b></summary>

### CCB 是什么

CCB 是一个项目级 agent CLI 工作台。它用 tmux 管理多个真实 CLI agent，把启动、恢复、通信、配置、窗口和运行态聚合在一个项目里。

- **真实 CLI，不是模拟面板**：每个 agent pane 都运行对应 provider 的真实 CLI。
- **可见协作**：sidebar 展示窗口、agent 状态和通信区；用户可以用鼠标直接切 pane。
- **混合 provider**：一个项目里可以同时跑 Codex、Claude、Gemini、Kimi（`kimi`）、MiMo（`mimo`）、Qwen（`qwen`）、Cursor（`cursor`）、Copilot（`copilot`）、Crush（`crush`）、Kiro（`kiro`）、Pi（`pi`）、Z.ai CLI（`zai`）、OpenCode、Droid 和 Antigravity（`agy`）。
- **项目级配置**：`.ccb/ccb.config` 决定团队、布局、窗口、worktree、model、key、url。
- **内置 CCB 专家**：空白项目默认包含 `ccb_self`，它是具备 CCB 自理解能力的自维护 agent，可帮助使用 CCB、设计配置、诊断运行态、恢复工作流。
- **Roles**：全新的角色封装概念；它让携带“重武器”（独立 skills、记忆和
  工具依赖等）的专业角色瞬间“降临”到目标项目中，成为一个可以快速热加载和
  卸载的独立 agent，同时保持主环境、用户全局配置和项目运行状态不发生改变。
- **可恢复运行态**：CCB 后台守护 agent pane，支持 attach、恢复和项目级清理。
- **显式协作通道**：agent 可以通过 `/ask`、`$ask`、callback 和 silence 进行委派与交接。

### 为什么需要多 agents

小任务用单 agent 就够了；一旦任务需要规划、并行实现、审查、测试和交接，多 agents 的价值就变成：把角色、上下文、模型和执行过程拆开管理。CCB 的重点是把多个真实 CLI agent 放进同一个可见终端工作台。

| 价值 | 直观理解 |
| :--- | :--- |
| 角色分离 | `main` 负责任务拆分，`worker` 负责实现，`reviewer` 负责审查。 |
| 并行推进 | 一个 agent 写代码时，另一个 agent 可以读文档、跑验证或审查风险。 |
| 模型和上下文分层 | 不同 agent 可以用不同 provider、model、API、worktree 和记忆。 |

<details>
<summary><b>展开：单 agent 为什么会吃力？</b></summary>

- 角色混杂会影响上下文集中度：同一个会话既要做架构，又要写代码，还要自我审查，容易在长任务里丢掉重点。
- 可执行复杂度有上限：任务越长，越需要拆分、交接、核对和回滚点。
- 成本压力更高：如果所有步骤都依赖一个最强模型，简单任务也会变贵。
- 工具和 skill 集中会变难管理：什么都给一个 agent，等于把权限、说明和工具复杂度全部堆在一起。
- 串行等待效率低：一个 agent 在跑测试、读日志或长时间思考时，其他可并行工作无法自然展开。

</details>

### 多 agents 方案怎么选

多 agents 不是一种固定形态。先用下面这张表判断大方向，细节可以展开看。

| 方案 | 一句话 | 更适合你如果 |
| :--- | :--- | :--- |
| [Claude Code 原生 subagents](https://code.claude.com/docs/en/sub-agents) / [agent teams](https://code.claude.com/docs/en/agent-teams) | Claude Code 内部的原生分工。 | 你主要留在 Claude Code，并接受更多协调由 Claude lead 处理。 |
| [Hive / OpenHive](https://github.com/aden-hive/hive) | 面向生产工作流的多 agent harness。 | 你要状态、恢复、观测、成本控制和图式工作流。 |
| CCB | 可见、可控、混合 provider 的本地 CLI agent 工作台。 | 你要把 Codex、Claude、Gemini、Kimi、MiMo、Qwen、Cursor、Copilot、Crush、Kiro、Z.ai CLI、OpenCode、Antigravity 等真实 CLI 放到一个项目终端里操作。 |

<details>
<summary><b>展开：模型、可控性、上下文和复杂工作流怎么区别？</b></summary>

| 关键问题 | Claude Code 原生 | Hive / OpenHive | CCB |
| :--- | :--- | :--- | :--- |
| 能否使用不同家的模型 | 可给 teammate / subagent 指定 Claude 模型；整体仍在 Claude Code 体系内。 | 通过 LiteLLM 路线支持大量 hosted / local provider。 | 按 agent 选择 Codex、Claude、Gemini、Kimi、MiMo、Qwen、Cursor、Copilot、Crush、Kiro、Z.ai CLI、OpenCode、Droid、Antigravity 等，并可设置独立 model / key / url。 |
| 过程是否可见 | in-process 或 split panes，取决于模式和终端。 | 强调 runtime observability 和控制台视角。 | 默认就是 tmux 可见 pane，用户能直接点击、输入、复制、观察每个 CLI。 |
| 拓扑是否可控 | 可自然语言指定队友，但运行时协调较多交给 lead。 | 由目标生成图式拓扑，偏 harness。 | 配置文件显式定义 agent、窗口、pane、worktree 和 sidebar。 |
| 上下文是否可管理 | subagent / teammate 有独立上下文；team 有任务和消息状态。 | 角色记忆、状态持久化、恢复能力是核心卖点。 | 每个 CLI 保留自己的 provider 会话；项目共享记忆和 per-agent 记忆可选。 |
| 最适合的落点 | Claude Code 内部的快速委派和团队模式。 | 业务流程自动化、长期运行和生产可靠性。 | 本地开发、代码协作、跨 provider CLI agent 可视化工作台。 |

CCB 也支持复杂工作流，但它不是自动生成 DAG 的 harness；复杂度主要通过 `.ccb/ccb.config`、多 window、角色记忆、worktree、model/API 配置和 ask/callback 路由显式设计。

</details>

</details>

<details>
<summary><b>日常操作</b></summary>

### 日常操作

| 目标 | 命令 |
| :--- | :--- |
| 启动或重新 attach 当前项目工作台 | `ccb` |
| 安全启动，保留各 agent 配置的权限策略 | `ccb -s` |
| 重建运行态，保留配置和同名 managed agent 历史 | `ccb -n` |
| 停止当前项目后台 | `ccb kill` |
| 强制清理当前项目残留后再重建 | `ccb kill -f` 后接 `ccb -n` |
| 更新到最新稳定 release | `ccb update` |
| 安装或刷新可选 rich 富媒体工作台 | `ccb update rich` |
| 移除 rich 模式并退回普通启动 | `ccb uninstall rich` |
| 打开 rich 富媒体工作台 | `ccb rich` |
| 查看当前使用的配置层 | `ccb config validate` |
| 预览配置热加载计划，不修改 tmux | `ccb reload --dry-run` |
| 应用支持的配置变更，不重启其他 agent | `ccb reload` |

### tmux 常规操作

CCB 虽然基本全部可以使用鼠标操作，但是学会 tmux 命令可以显著增加便利性。下面列举了部分常用的键盘操作快捷键。

本节只讲 tmux。下面的 `<prefix>` 默认为 `Ctrl-b`：**先按 `Ctrl-b`，松开，再按后面的功能键**。功能键建议在英文输入法下按，避免中文输入法拦截符号键。

| 目标 | 功能键 | 说明 |
| :--- | :--- | :--- |
| 切换到相邻 pane | `方向键` | 选择上、下、左、右相邻 agent pane。 |
| 切到下一个 pane | `o` | 不关心方向时快速轮转。 |
| 放大 / 还原当前 pane | `z` | 看长输出、diff、日志时非常有用。 |
| 打开 window / pane 列表 | `w` | 在多 window、多 pane 时选择目标。 |
| 下一个 window | `n` | 切到下一个 tmux window。 |
| 上一个 window | `p` | 切到上一个 tmux window。 |
| 切到编号 window | `0` 到 `9` | 直接跳到对应编号的 window。 |
| 进入复制/滚动模式 | `[` | 查看历史输出、滚动、选择文本。 |
| 退出复制/滚动模式 | `q` 或 `Esc` | 回到正常输入。 |
| 粘贴 tmux buffer | `]` | 粘贴 tmux 自己复制的内容。 |
| 暂时 detach 会话 | `d` | 退出显示但不停止 CCB 后台，会话仍可重新 attach。 |

复制粘贴建议：

- **鼠标复制**：大多数终端里可以直接左键拖选复制；如果拖选被 tmux 接管，先进入复制/滚动模式再选择。
- **绕过 tmux 拖选**：很多终端支持 `Shift + 鼠标拖选` 使用终端原生复制。
- **系统粘贴**：Linux/Windows 终端通常是 `Ctrl+Shift+V`，macOS 终端通常是 `Cmd+V`。
- **tmux 粘贴**：如果内容已经在 tmux buffer 里，用功能键 `]`。

<details>
<summary><b>更多常用 tmux 操作</b></summary>

| 目标 | 功能键 | 说明 |
| :--- | :--- | :--- |
| 在复制/滚动模式中滚动 | `PageUp` / `PageDown` / `方向键` | 不同终端支持略有差异。 |
| 在复制/滚动模式中搜索 | `Ctrl-s` / `Ctrl-r` | 分别常用于向前/向后搜索。 |
| 新建 window | `c` | 只在你明确需要额外 shell 时使用。 |
| 重命名 window | `,` | 多 window 工作流中更容易识别。 |
| 显示快捷键帮助 | `?` | 忘记快捷键时查看 tmux 帮助。 |

不建议新用户一开始就使用关闭 pane/window 的 tmux 快捷键。停止 CCB 项目请优先使用 CCB 的项目级退出命令，避免只杀掉某个可恢复 pane 造成误判。

</details>

</details>

<details>
<summary><b>配置和角色</b></summary>

### 配置 agent 团队

CCB 配置有三层，优先级从低到高：

1. 内置默认配置。
2. 用户配置 `~/.ccb/ccb.config`。
3. 项目配置 `.ccb/ccb.config`。

更高层会整体替换低层，不做局部合并。当前项目的权威配置文件是 `.ccb/ccb.config`；旧路径 `.ccb_config/ccb.config` 只应作为迁移参考。
内置默认配置是 v2 `[windows]` 拓扑，包含 `agent1`、`agent2`、`agent3` 和 `ccb_self`。可选 rich 富媒体工作台通过 `ccb update rich` 安装；启用后普通 `ccb` 会走 rich launcher，运行 `ccb uninstall rich` 后退回普通终端启动。默认 `ccb_self` 使用 `codex` 并绑定 `agentroles.ccb_self`。

`.ccb/ccb.config` 主要配置这些内容：

| 配置内容 | 写法或位置 | 说明 |
| :--- | :--- | :--- |
| window 分组 | `[windows]` | 把 agent 分到 `main`、`work`、`review`、`research` 等 tmux window。 |
| agent 名称和 provider | `main:codex`、`reviewer:claude` | 名称用于界面、ask 路由和记忆文件；provider 决定启动哪家 CLI。 |
| 工作区隔离 | `worker1:codex(worktree)` | 给实现类 agent 独立 git worktree，降低互相覆盖的风险。 |
| sidebar 行为 | `[ui.sidebar]` | 控制 sidebar 是否每个 window 都显示、左右位置、宽度和 Comms 高度。 |
| 工具 window | `[tool_windows.<name>]` | 添加 rich 富媒体工作台这类非 agent 托管 window；sidebar 只显示一行，不是 `ask` 目标。 |
| 单 agent 模型/API | `[agents.<name>]` | 可为不同 agent 配 `model`、`key`、`url` 等。 |
| Role Pack 绑定 | `agentroles.archi:codex` | 通过 window leaf 绑定可复用角色包；role 资产统一安装，再投影到解析出的 agent。 |
| 角色说明 | `[agents.<name>] description = "..."` | 给 agent 一个简短职责说明；更长的工作流规则建议写到 memory。 |

在已启动的项目里修改 `.ccb/ccb.config` 后，先运行 `ccb reload --dry-run` 预览计划，再运行 `ccb reload` 应用。显式 reload 可以动态新增 agent、新增 window、新增/删除托管工具 window、卸载 idle agent、删除 idle window，同时保持无关 agent 和 pane 继续运行。它不是后台文件监听；busy agent 卸载、provider 替换、agent 移动、工具命令替换和任意布局重排会被拒绝，不会 kill 现有 pane。

如果你想先讨论配置而不是手写，可以直接让 `ccb_self` 描述目标团队。空白项目默认已经有这个路由；使用用户配置或项目配置覆盖内置默认的项目，如果还没有 `ccb_self`，需要先添加 `agentroles.ccb_self`。它的内置 `ccb-config` skill 会先提出完整方案，确认后再修改 `.ccb/ccb.config`。

#### Role Packs

Role Pack 通过 [Agent Roles Spec](https://github.com/SeemSeam/agent-roles-spec)
定义可复用的 agent 角色。这个规范是 host-neutral 的专业 agent 包格式：一个
Role 可以把稳定身份、职责、非目标、记忆、skills、prompts、references、tools、
plugin 内容、验证说明和 host adapter metadata 放进一个可审查的独立单元。

它的意义是把边界分清楚：Role source 保持可移植、可版本化；项目绑定决定这个
Role 挂载到哪里；provider 运行态、凭据、任务进度和生成的投影文件都留在 Role
外部。这样专门 agent 更容易安装、更新、审计、迁移和卸载，不需要在每个项目里复制
大段 prompt，也不会污染用户全局配置。

推荐默认 catalog roles 包括 `agentroles.ccb_self` 和
`agentroles.archi`：前者是 CCB 自维护角色，后者用于架构审查，来自
`agent-roles-spec`，并由 Architec 支撑。`install.sh install` 默认会尝试安装
或刷新这些推荐角色；`ccb update` 会在用户环境里刷新已安装 role，并安装缺失的
推荐角色。也可以手动刷新：

```bash
ccb roles list
ccb roles show agentroles.archi
ccb roles install agentroles.archi
ccb roles update agentroles.ccb_self
ccb roles update agentroles.archi
```

项目内的 role 绑定仍由 `.ccb/role-lock.json` 固定。`ccb update` 不会改写项目
锁。在项目内运行 `ccb` 时，CCB 会比较已绑定 role lock 和用户环境里当前安装的
role；如果锁已经落后，交互式启动会询问是否就地刷新项目锁，非交互启动只打印
提醒。

强烈建议 CCB 项目保留 `ccb_self`，因为它是 CCB 内置专家 agent，携带 CCB
项目配置、命令使用、role 绑定、reload 边界、运行诊断、受保护恢复、工作链修复和
单 agent 重启辅助等专用知识，同时不接管业务任务。空白项目的内置默认配置已经
包含它；已有项目，或使用用户配置/项目配置替换内置默认的项目，需要该维护 agent
时应显式把它作为 window leaf 加进去：

```bash
ccb roles add agentroles.ccb_self:codex
ccb reload
```

在项目里使用 `agentroles.archi` 时，把它作为 window leaf 加进去：

```bash
ccb roles add agentroles.archi:codex
ccb reload
```

这会写入紧凑形式 `agentroles.archi:codex`。运行时 CCB 会把它解析成项目本地
agent `archi`，并把 role memory 和 skills 投影到该 agent 的 managed
provider home。

<details>
<summary><b>配置格式示例：单窗口、多 window、per-agent 模型/API</b></summary>

#### 单窗口紧凑配置

```text
cmd; main:codex, worker1:codex(worktree); reviewer:claude
```

含义：

- `cmd` 是 shell pane，不是 agent。
- `main`、`worker1`、`reviewer` 是 agent 名称。
- `codex`、`claude` 是 provider。
- `;` 表示左右分栏，`,` 表示上下堆叠。
- `(worktree)` 表示该 agent 使用独立 git worktree。

#### 多 window 拓扑

当你想把规划、实现、审查、研究分到不同 tmux window 时，使用 `version = 2` 和 `[windows]`：

```toml
version = 2
entry_window = "main"

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

注意：`cmd` 只属于紧凑/混合单窗口布局；`[windows]` 拓扑里不要写 `cmd`。

#### Rich 富媒体工作台工具 window

工具 window 是 CCB 管理的 tmux window，但不是 agent。它不会出现在 `ccb ask` 目标中，也不会创建 provider runtime 记录。

```toml
version = 2
entry_window = "main"

[windows]
main = "main:codex"

[tool_windows.rich]
command = "CCB_WORKBENCH_PROFILE=rich CCB_WORKBENCH_FORCE_RICH=1 ccb-workbench files"
label = "rich"
```

`ccb update rich` 会在 CCB 自己的 XDG 目录下准备可选工作台包，优先下载并验证可封装的二进制，只把 WezTerm、Markdown/PDF/图片/视频辅助工具和推荐字体等必要依赖交给平台包管理器。WSL 下可以调用 Windows 原生 `wezterm.exe`，同时让 rich 工具继续运行在当前 Linux 发行版内。普通 `ccb update` 不会主动安装或刷新该包；需要安装、修复或更新时重新运行 `ccb update rich`。运行 `ccb uninstall rich` 会移除该包，并让普通 `ccb` 回到常规终端启动。设置 `CCB_RICH_DOWNLOAD_BINARIES=0` 可跳过二进制下载，设置 `CCB_RICH_INSTALL_DEPS=0` 可跳过系统包安装。

#### 给 agent 单独配置模型、API key 或 base URL

如果只需要布局，用紧凑格式即可；如果某些 agent 需要单独模型或 API 路由，在紧凑头后追加 TOML overlay：

```toml
cmd; fast:codex, deep:codex; reviewer:claude

[agents.fast]
model = "gpt-5-mini"

[agents.deep]
key = "sk-..."
url = "https://api.example.com/v1"
model = "gpt-5"

[agents.reviewer]
model = "sonnet"
```

不要把真实 API key 提交到公开仓库。`key` / `url` 是 agent 级快捷字段；更复杂的 provider 环境变量应放到 provider profile 或 agent env 中。

</details>

### 使用 ccb_self 配置 CCB

完整的 `ccb-config` skill 属于 `agentroles.ccb_self` 角色，不再作为所有 agent 都继承的公共 skill。CCB 默认会安装或刷新这个 Role Pack，空白项目的内置默认配置也会包含 `ccb_self`。已有项目，或使用用户配置/项目配置替换内置默认的项目，需要维护助手时应显式绑定它。

`ccb_self` 不只是配置助手，它被设计成 CCB 的自理解 agent。使用 CCB 时遇到布局解释、团队拓扑选择、`.ccb/ccb.config` 迁移、运行态诊断、恢复路径或工作流修复问题，都可以先问它。

如果你不想手写 `.ccb/ccb.config`，可以直接询问 `ccb_self`，再用自然语言描述项目目标、并行程度、窗口分组、worktree 隔离、provider/model/API 偏好。`ccb_self` 会使用它内置的 `ccb-config` 和你讨论后提出完整配置方案。

示例：

```bash
ccb ask ccb_self "为一个 Python library 设计团队：main 负责任务拆分，三个 worker 使用 worktree 并行实现，一个 reviewer 做回归和风险审查。保留单窗口还是拆成 main/work/review 三个 window 由你建议。"
```

如果是尚未配置 `ccb_self` 的已有项目，先运行
`ccb roles add agentroles.ccb_self:codex` 和 `ccb reload`。

<details>
<summary><b>ccb-config 的写入流程和边界</b></summary>

1. 你用自然语言描述项目和团队目标。
2. `ccb_self` 内置的 `ccb-config` 读取当前配置权威层，判断是新建、修改还是迁移。
3. 它先提出完整配置方案，不应直接改文件。
4. 你确认后，它只修改 `.ccb/ccb.config`。
5. 它运行配置校验，并在可动态应用时提醒你使用 `ccb reload --dry-run` / `ccb reload` 生效。

默认情况下，`ccb-config` 不会修改 `.ccb/ccb_memory.md` 或 `.ccb/agents/<agent>/memory.md`。只有当你明确要求 `ccb_self` “设计工作流记忆”或“写入角色记忆”时，才应该修改这些 memory 文件。

</details>

</details>

<details>
<summary><b>协作与维护</b></summary>

### Agent 之间如何协作

普通 `ask` 是提交即返回：把任务交给目标 agent 后，当前 agent 不应该轮询等待。

| 场景 | 推荐方式 |
| :--- | :--- |
| 人直接指定目标 agent | `/ask reviewer ...` 或 `$ask reviewer ...` |
| 当前 agent 在 active CCB task 内，必须等子任务结果才能回复 | `ask --callback reviewer` |
| 当前 agent 派发独立任务，成功结果不需要回来 | `ask --silence worker1` |
| 调试队列、诊断状态 | `pend`、`watch`、`ping` 等只作为诊断工具使用 |

agent 提交子任务时，先按结果意图选参数，再按依赖关系和内容保真补充参数：

| 需求 | 推荐参数 |
| :--- | :--- |
| 发布或执行任务，成功结果不需要回来 | `--silence` |
| 需要简短结果：状态、发现、风险、阻塞、下一步 | `--compact` |
| 需要完整咨询、分析、报告、生成文档或结构化结论 | `--artifact-reply` |
| 当前 active 父任务必须等子任务结果才能继续 | 追加 `--callback` |
| 需要保留精确粘贴的日志、diff、JSON/YAML、表格或复制文本 | 追加 `--artifact-request` |
| 输入和输出都需要保真 | `--artifact-io` |
| 只是短问题或短交接，行内文本足够 | 普通 `ask` |

`--callback` 和 `--silence` 管任务关系；artifact 参数管内容保真。自动长消息
spill 只是兜底，所以只要精确输入或完整输出重要，就应该主动使用 artifact 参数。

<details>
<summary><b>Callback 为什么重要</b></summary>

如果 agent A 正在处理一个来自用户的 CCB task，又需要 agent B 的结果才能完成，就应该用 callback。CCB 会记录 parent/child 关系，让 A 当前 turn 结束；等 B 完成后，CCB 再把结果作为 continuation 交回 A。这样不会阻塞队列，也不会让 agent 靠轮询浪费上下文。

</details>

### 安装和更新

#### 环境要求

- 推荐 npm 安装路径需要 Node.js 和 npm
- Python 3.10+
- `tmux`
- 至少一个你要使用的 agent CLI，例如 Codex、Claude、Gemini、Kimi、MiMo、Qwen、Cursor、Copilot、Crush、Kiro、Z.ai CLI、OpenCode、Droid 或 Antigravity
- Linux、macOS 或 WSL

当前 v7 / 新版本不声明原生 Windows 支持。原生 Windows 只支持到 v5 线；如果你在 Windows 上使用新版本，推荐使用 WSL，并让 `ccb` 与 agent CLI 都运行在 WSL 内。

#### npm 优先

首次安装推荐使用 npm：

```bash
npm install -g @seemseam/ccb
```

后续更新直接使用：

```bash
ccb update
```

[GitHub Releases](https://github.com/bfly123/claude_code_bridge/releases) 仍作为不方便使用 npm 时的备选路径。源码 checkout 安装只适合开发、验证修复或临时兜底。

#### 卸载

```bash
ccb uninstall
ccb reinstall

# 备用方式：在安装包或源码目录内
./install.sh uninstall
```

### 常见问题

<details>
<summary><b>启动后没有看到预期 agent</b></summary>

先运行 `ccb config validate`，确认 `config_source_kind` 是你预期的层级。项目配置 `.ccb/ccb.config` 优先级最高；如果它不存在，CCB 才会使用 `~/.ccb/ccb.config` 或内置默认配置。

</details>

<details>
<summary><b>复制粘贴不好用</b></summary>

优先试鼠标拖选复制和 `Ctrl+Shift+V` / `Cmd+V` 粘贴。如果鼠标选择被 tmux 接管，使用 `<prefix>` 后的功能键 `[` 进入复制/滚动模式；如果只是想绕过 tmux，很多终端支持 `Shift + 鼠标拖选`。

</details>

<details>
<summary><b>想把旧 compact 配置迁移到多 window</b></summary>

让 `ccb_self` 使用它内置的 `ccb-config`，描述你想要的窗口分组，例如 main/work/review。迁移时应保留旧 agent 名称、provider、worktree 标记、model/key/url 等字段，确认后再写入 `[windows]`。

</details>

<details>
<summary><b>sidebar helper 不可用</b></summary>

优先使用 release 包，因为 release 包会携带或处理 sidebar helper。源码安装时如果缺少可用的预编译 helper，可能需要本机 Rust 工具链来构建。

</details>

### 社区和致谢

感谢 [Linux.do 社区](https://linux.do) 在测试、反馈和讨论中的支持。

感谢 [tmux-agent-sidebar](https://github.com/hiroppy/tmux-agent-sidebar) 提供的 sidebar 思路和启发。

</details>

<details>
<summary><b>版本记录</b></summary>

### 新版本记录

v7 线重点：

- 原生 CCB sidebar，支持 per-window 项目视图、agent 状态和鼠标切换。
- Comms 从 agent 活动中拆分，通信状态和 provider pane 活动更清晰。
- 新增 `version = 2` `[windows]` 拓扑，可按工作流分组多个 tmux window。
- 显式 `ccb reload` 支持动态加载 agent/window 和 idle 卸载，不重启无关 agent。
- 保留 compact / hybrid 旧配置兼容，单窗口团队不需要强制迁移。
- 加固 tmux、Ghostty、release helper、Codex trust 和 provider 会话恢复路径。

<details open>
<summary><b>v8.0.7</b> - CCB Mobile 通知与主题稳定化</summary>

- 新增 server-wide 任务完成通知 SSE 和 Android 本地通知，payload 保持低敏，并已真实验证点击回到目标 project/agent。
- 合入 provider runtime 状态判断，供手机 activity indicator 和 sidebar 状态使用。
- 更新手机 App 的 steel-blue/slate 主题、正文样式、展开气泡高度限制和通知分组点击处理。
- 同步 VERSION、package metadata、mobile app 版本、README 链接、workflow 默认值和 APK 下载链接到 8.0.7。

</details>

<details>
<summary><b>Unreleased</b> - 空回复诊断细化</summary>

- 将泛化的 `incomplete/task_complete_empty_reply` 完成原因拆分为
  `model_empty_output`（模型无输出）、`delivery_late_empty`（未看到请求
  anchor 就收到 turn boundary，说明 prompt 未投递或 reader 绑定到旧日志）、
  `api_empty_after_error`（turn 内出现 api_error 后空完成）。
- 在空回复决策的 diagnostics 中新增 `empty_reply_reason`。
- Codex provider 在 turn 内观察到 `api_error` 事件时，会在
  `TURN_BOUNDARY` payload 中带上 `api_error_seen: true`。

</details>

<details>
<summary><b>v8.0.6</b> - CCB Mobile 真实项目对话稳定性</summary>

- 改进 Android CCB Mobile 真实项目 pane-native 对话、状态恢复、
  terminal stream、扫码/手动配对、输入体验、多语言和时间线噪音过滤。
- 合入 CCB source 线上的 server-wide 项目列表、原生 provider transcript、
  provider runtime status 和 mobile terminal pane input 修复。
- `ccb update mobile` 改用紧凑终端配对二维码，保持原始 CCB Mobile 桥形图标，
  并把相机不可用时的扫码失败改成可操作 fallback，避免展示 native 插件错误。
- 同步 VERSION、package metadata、mobile app version metadata、README 链接、
  workflow 默认值和 APK 下载链接到 8.0.6。

</details>

<details>
<summary><b>v8.0.4</b> - CCB Mobile 项目列表稳定性</summary>

- server-wide mobile gateway 的 `/v1/projects` 现在会并发检查已挂载项目健康状态，
  同时保持 registry 顺序，避免项目较多时手机端加载超时或连接中断。
- 手机断开或超时时，mobile gateway 不再为普通 BrokenPipe/connection reset
  写出刷 traceback。

</details>

<details>
<summary><b>v8.0.3</b> - npm Release Metadata 修复</summary>

- 修复 npm provenance metadata，使 `@seemseam/ccb` 的发布仓库与 GitHub
  Actions 使用的 canonical 仓库一致。
- 同步 VERSION、package metadata、mobile app version metadata、README 链接、
  workflow 默认值和 APK 下载链接到 8.0.3。

</details>

<details>
<summary><b>v8.0.2</b> - Mobile Tailnet Onboarding 修复</summary>

- 正确识别 Tailscale Serve 一次性授权链接，不再把原始 timeout 暴露给用户。
- 如果 `:8787` 的 Tailscale Serve 代理已经正确指向 loopback mobile gateway，
  `ccb update mobile` 会直接复用并进入配对二维码流程。
- 修复 source worktree 安装时误复制 `.git` worktree 标记的问题，避免安装版
  `ccb` 被误判为源码 checkout。

</details>

<details>
<summary><b>v8.0.1</b> - CCB Mobile 极简配对</summary>

- 将 `ccb update mobile` 收敛为唯一面向普通用户的设置入口：检测
  Tailscale、引导登录/安装、启动 server-wide loopback gateway 和 Tailscale
  Serve，并直接在终端打印配对二维码。
- 手机端首次启动不再进入 fake/demo 项目，而是显示配对说明、Tailscale 下载提示
  和扫码按钮。
- 检测到已保存 pairing profile 后，手机端会自动进入 server-wide 已挂载项目列表，
  降低普通手机使用的配置压力。

</details>

<details>
<summary><b>v8.0.0</b> - CCB Mobile Monorepo 发布</summary>

- Flutter 版 CCB Mobile 源码正式进入本仓库，并在 GitHub Release 中发布
  Android APK。
- 新增 server-wide mobile 项目发现、配对、认证 gateway 路由、pane-native
  消息输入、对话上下文渲染、terminal 访问，以及图片/文档上传下载能力。
- 将 `ccb update mobile` 提升为 Tailscale Tailnet onboarding 的统一入口，
  同时保持 gateway 仅监听 loopback，不启用 Funnel、不保存 token、不自动修改
  ACL/grants。

</details>

<details>
<summary><b>v7.7.0</b> - Runtime Accelerator 发布加固</summary>

- Release artifacts 现在会携带可选 Rust `ccb-runtime-accelerator`，安装版
  Codex agent 在预期存在 sidecar 时不再静默退回 Python 热路径。
- 当项目路径导致 Unix socket 路径过长时，accelerator socket 会自动落到
  短的 per-user runtime socket root。
- 加固 callback repair 和 Codex binding cache invalidation，并记录完整
  回归、长 idle Codex soak、Claude callback 和混合 provider 集成测试证据。

</details>

<details>
<summary><b>v7.6.19</b> - 长任务 ask 默认等待策略</summary>

- 普通长时间 `ask` 默认继续等待真实 provider/completion 结果，不再仅因
  heartbeat 诊断自动 terminalize 为 `incomplete/heartbeat_timeout`。
- Codex、Claude、Gemini 的 pane-backed no-terminal timeout 默认改为显式
  opt-in，仍保留显式 reliability timeout 策略。
- 已用 32 分钟 source-runtime ask smoke 验证：任务超过 30 分钟仍保持
  running，随后以 `result_message` 完成，未出现 `heartbeat_timeout` 或
  `incomplete` 证据。

</details>

<details>
<summary><b>v7.6.18</b> - CCB UI 主题偏好</summary>

- 新增顶层 `ccb theme` 主题切换命令，可调整 CCB 自有 tmux/sidebar UI，
  并支持用 `+` / `-` 在深色和浅色 palette 间循环。
- 新增适合浅色 terminal 背景的 tmux status、pane border、sidebar、agent
  活动状态和 comms 状态配色。
- 生成的 rich WezTerm profile 会读取同一个全局 CCB 主题偏好，并在下次
  打开或 reload 时同步主题。

</details>

<details>
<summary><b>v7.6.17</b> - Codex Log Symlink Target 修复</summary>

- 当 `/tmp/ccb-codex-logs-*` 清理导致 managed Codex `logs_2.sqlite` 临时
  symlink target 目录消失时，启动前会自动重建 target parent。
- 如果坏 symlink 无法修复，CCB 会先移除 symlink 并恢复本地备份，再让
  Codex 初始化自己的 SQLite 数据库。
- 增加缺失 symlink target parent 启动路径的回归测试。

</details>

<details>
<summary><b>v7.6.16</b> - Codex SQLite Migration 恢复修复</summary>

- 修复 managed Codex `logs_2.sqlite` redirect：CCB 不再预创建 Codex 自有
  SQLite schema，改为等待 Codex 自己完成 migration。
- 只有在 Codex 创建 log database 和 `_sqlx_migrations` 记录后，才安装 CCB
  的 diagnostic insert-block trigger。
- 对中间问题版本留下的异常临时 log database 做自愈：先挪到备份，再让
  Codex 通过正常 migration 路径重新创建。

</details>

<details>
<summary><b>v7.6.15</b> - Codex 诊断与 Sidebar Focus 修复</summary>

- managed Codex 默认把 `logs_2.sqlite` 诊断写入重定向到临时存储，并阻断
  diagnostic log insert；diagnostics 模式可恢复原始数据库路径用于排查。
- 如果临时 SQLite symlink 无法安装，会回退到 in-place diagnostic trigger
  路径，避免启动失败。
- 修复 sidebar 点击其他 tmux window 中 agent 的聚焦路径：先选择目标
  window，再选择 pane；缺少 window 元数据时继续保留 pane id 兜底。

</details>

<details>
<summary><b>v7.6.14</b> - Mobile Gateway Alpha 与 Codex 诊断治理</summary>

- 新增 Mobile Gateway Alpha 能力：认证 pairing、focus routes、terminal
  open/resume/history routes、websocket terminal frames、public route metadata
  和设备撤销命令。
- 新增 sidebar 右侧放置能力，并把 canonical `[ui.sidebar]` 渲染扁平化；
  legacy `[ui.sidebar.view]` 输入继续兼容。
- 支持多个本地 agent 共用同一个 Role Pack role id，不再被折叠成单一运行时身份。
- 降低 Codex diagnostic SQLite 写盘抖动：默认过滤 TRACE/DEBUG 日志行并保留
  INFO/ERROR；设置 `CCB_CODEX_DIAGNOSTIC_LOGS=1` 可关闭过滤。

</details>

<details>
<summary><b>v7.6.13</b> - Provider Profile Overlay 修复</summary>

- Codex plugin override 现在按预期顺序解析：继承的 source config、
  `provider_profile.plugins`，最后是
  `CCB_CODEX_PLUGIN_OVERRIDES_JSON` / `CCB_CODEX_PLUGIN_OVERRIDES` 环境覆盖。
- 没有继承 `config.toml` 的 Codex agent 现在也会把
  `provider_profile.plugins` 写入 managed `config.toml`。
- Claude `provider_profile.mcp_servers` 现在即使 source `.claude.json`
  不存在也会生效，`enabled = false` 会清理 agent trust file 里的 stale
  managed MCP server。
- callback continuation 现在会保留明确的 upstream finalization target；
  继承的 ask skills 也会提醒 agent 在 upstream 结果可用前不要提前回答
  callback continuation。

</details>

<details>
<summary><b>v7.6.12</b> - Claude MCP 与 Hook 继承</summary>

- managed Claude agent 现在会从 source `.claude.json` 继承 Claude Code MCP
  配置，包括全局 `mcpServers` 和当前 project/workspace 的 MCP server 状态。
- project 级 MCP 状态只映射到当前 managed workspace key，不会把无关 source
  project 记录复制进 agent home。
- source-home Claude Code hooks 会与 CCB-managed finish/activity hooks 合并，
  用户安装的 hook 工具在 agent restart 后仍可见。
- managed Claude `.claude.json` 现在按 secret provider state 处理，因为 MCP
  定义可能包含环境变量或接近认证材料的启动配置。

</details>

<details>
<summary><b>v7.6.11</b> - Layout Percent 与 Codex MCP Overlay</summary>

- 新增显式 pane split 比例 layout token，例如 `agent1:codex@30`；没有
  `@N` 后缀时继续保持原有 sibling panes 均分行为。
- 新增通过 `provider_profile.mcp_servers` 配置 per-agent Codex MCP overlay；
  同名 MCP server 覆盖继承配置，不同名 additive。
- managed Codex home projection 现在会保留可信 Codex command hook，并改进
  sidebar Comms/Tips 的滚动和拖拽调整，同时在 `ccb trace` 暴露更多 reply
  artifact 证据。

</details>

<details>
<summary><b>v7.6.10</b> - Z.ai Provider 支持</summary>

- 新增 Z.ai CLI optional provider：支持 `provider = "zai"`、可见
  `zai --directory` pane，以及 per-job `zai --prompt` 执行。
- 使用 Z.ai 原生 subprocess 完成边界：进程退出加 JSONL stdout 中的
  assistant 内容提取，不要求模型打印 `CCB_DONE`。
- 新增 `ZAI_START_CMD`、provider session/pathing、deterministic stub、
  focused execution 测试，并同步 README provider 支持列表。

</details>

<details>
<summary><b>v7.6.9</b> - Kimi / AGY Provider 可靠性</summary>

- Kimi execution 现在记录 receipt、无捕获输出诊断、trace 和 resume
  metadata，便于定位缺失回复和恢复 turn。
- AGY prompt delivery 现在等待 ready evidence，处理 pane fallback 和
  ambiguous tmux send 结果，并更清楚地报告合并请求诊断。
- dispatcher、mailbox trace 和 text artifact 诊断现在会暴露排查 Kimi/AGY
  delivery 与 completion 边界所需的 provider 细节。

</details>

<details>
<summary><b>v7.6.8</b> - Role Pack Current Store</summary>

- Role Pack 运行时现在跟随 `.roles/installed/<role-id>/current` 下的当前安装包；
  旧的多版本 store 只作为兼容输入，不再是运行时主权。
- 项目 `.ccb/role-lock.json` 现在是 legacy diagnostic：CCB 不再写入、不再从
  lock adopt，也不会因为旧 lock 残留而 suppress role memory/skills。
- provider 启动 session 会记录 role id、version 和 digest；当启动 digest 与
  installed current 不一致时，restart 会明确失败，而不是静默恢复旧 provider
  会话并假装采用了新 role。
- release artifact 元数据 patch 现在指向 bash launcher 拆分后的 `ccb.py`，
  确保构建出的 tarball 携带正确版本。

</details>

<details>
<summary><b>v7.6.7</b> - Rich Workbench 闭环</summary>

- 普通 `ccb` 和 `ccb rich` 现在会启动 CCB 托管的 rich WezTerm；只有已经在该
  CCB 托管 rich 会话内时才跳过自动启动，普通外部 WezTerm 不再误判为 rich。
- 运行入口统一走 `_ccb-python` launcher，让安装版和源码版命令都固定到预期
  Python 解释器。
- 内置默认配置继续把 `ccb_self` 放在独立 `claude` window，同时普通默认启动
  不恢复 standalone Neovim tool window。

</details>

<details>
<summary><b>v7.6.6</b> - Role Store Home Pinning</summary>

- role store lookup 现在会固定在 managed provider home 之外，provider session
  改写 `HOME` 时不再误查 provider-local `.roles` 目录。
- CCB 启动边界会保留 `AGENT_ROLES_STORE`；未显式设置时回退到真实
  source/account home 下的 role store。
- 缺失 role 的诊断会打印解析后的 role store 路径，便于定位 provider-home
  漂移问题。

</details>

<details>
<summary><b>v7.6.5</b> - Rich WezTerm 输入法</summary>

- 生成的 rich WezTerm 配置现在会启用 IME，并把 `XMODIFIERS=@im=...`
  映射为 WezTerm 的 XIM 名称，修复 X11 下 fcitx/ibus 中文输入连接问题。
- 生成的 `ccb-workbench` wrapper 会在启动 WezTerm 前探测运行中或已安装的
  `fcitx5`、`fcitx`、`ibus-daemon`，只在用户未设置时补齐输入法环境变量。
- 保留 v7.6.4 已绿发布面，以及 v7.6.2 的 rich/tmux 修复，供 npm latest
  安装实测。

</details>

<details>
<summary><b>v7.6.4</b> - macOS Release Install Smoke</summary>

- 保留 7.6.3 的 macOS temporary-root 加固，同时让 CI release install smoke
  对隔离的 sibling `CODEX_BIN_DIR` 显式设置临时 bin override。
- 不放宽用户侧 installer 安全规则，但允许 release workflow 从临时 smoke root
  验证 macOS 包安装。
- 保留 v7.6.2 已发布的 rich workbench 与 tmux 单行 status 修复，供用户安装
  实测。

</details>

<details>
<summary><b>v7.6.3</b> - macOS CI 绿灯补丁</summary>

- install guard 现在会识别 GitHub Actions macOS runner 使用的
  `${TMPDIR:-/tmp}` canonical parent，避免 `/private/var/folders/...` 临时
  路径被误放行。
- doctor 的 temporary implementation 检测同步兼容 macOS `/tmp` symlink
  行为，避免 `/private/tmp` 和 `/private/var/folders/...` 路径导致 CI 误红。
- 保留 v7.6.2 已发布的 rich workbench 与 tmux 单行 status 修复，供用户安装
  实测。

</details>

<details>
<summary><b>v7.6.2</b> - Rich Workbench 热修复</summary>

- `.ccb/ccb.config` 现在可以把 `rich` 当作工具/layout alias 使用，不需要
  provider runtime；它会 materialize 成托管工具 pane/window，不会成为 `ask`
  目标。
- `ccb update rich` 启用 bundle 后，普通 `ccb` 在既有 rich/WezTerm 会话外可
  自动走 rich launcher，同时避免递归重复拉起 WezTerm。
- 新增 `ccb uninstall rich`、`ccb rich uninstall` 和 `ccb rich disable`，
  可回到普通 CCB 启动；完整 `ccb uninstall` 语义保持不变。
- rich 更新只清理 CCB-owned legacy editor roots 和链接，不会碰用户自己的
  editor 安装和个人配置。

</details>

<details>
<summary><b>v7.6.1</b> - Rich Workbench 二进制封装</summary>

- `ccb update rich` 会优先封装并验证 Yazi/ya 二进制，再让包管理器兜底。
- Linux rich 安装优先使用官方 Yazi musl 构建，再回退 GNU 构建，避免旧稳定
  发行版遇到较新的 glibc 要求。
- 下载的 Yazi 二进制必须通过 `--version` 验证才会启用；无效的 managed
  二进制会被移除，保证后续 fallback 仍可工作。
- WSL 下 rich launcher 可使用 Windows 原生 `wezterm.exe`，同时让 CCB、Yazi
  和 preview helpers 继续运行在当前 Linux 发行版内。

</details>

<details>
<summary><b>v7.6.0</b> - Rich Workbench 生命周期</summary>

- Rich workbench 变为显式可选 bundle，统一通过 `ccb update rich` 安装和更新。
- 普通 `install.sh install` 和 `ccb update` 只处理 CCB 本体，不再自动
  provision standalone Neovim。
- 公开 `ccb tools ... neovim` 路由会拒绝 standalone provisioning 并提示
  `ccb update rich`；`ccb rich` 只启动已经安装并启用的 rich bundle。
- CCB tmux 状态栏恢复为单行，移除旧的第二行复制提示。

</details>

<details>
<summary><b>v7.5.3</b> - Kimi 运行可靠性与 Hindsight 兼容性</summary>

- 增强 Kimi 运行路径，但不改变其他 provider 的执行路径：当 native turn
  log 没有及时暴露完成回复时，Kimi 可对 K2.7 Code 使用稳定 pane 证据兜底。
- Kimi Hindsight 记忆改为 CCB 执行边界上的显式 opt-in：只有配置
  `.hindsight/kimi.json`、`.hindsight/codex.json`、`HINDSIGHT_API_URL` 或
  `HINDSIGHT_BANK_ID` 时才启用，失败时只记录 provider diagnostics，不阻塞任务。
- CCB 物化 managed Codex home 时会保留可信 Codex command hook，包括
  Hindsight Codex hooks。运维可通过 `CCB_CODEX_INHERITED_HOOK_EVENTS` 和
  `CCB_CODEX_INHERITED_COMMAND_HOOK_MARKERS` 扩展 allowlist；任意 root hook
  仍会被过滤。
- Kimi bridge 和 `scripts/hindsight` helper 同时兼容 `HINDSIGHT_API_KEY` 与
  `HINDSIGHT_API_TOKEN`。
- README 更明确展示支持的 provider surface，同时保持无关 provider 行为不变。

</details>

<details>
<summary><b>v7.5.2</b> - Native CLI Provider Wave</summary>

- 新增 Qwen Code（`qwen`）、Cursor Agent（`cursor`）、GitHub
  Copilot CLI（`copilot`）、Crush（`crush`）、Kiro CLI（`kiro`）、Pi（`pi`）和 Z.ai CLI（`zai`）
  作为内置 optional provider。
- 使用原生 per-job CLI 执行和 provider 自有完成信号：Qwen、Cursor、
  Copilot 和 Pi 解析 stream-json / JSON result 事件；Crush、Kiro 和 Z.ai CLI 使用进程退出
  加 stdout。新增适配器不要求模型打印 `CCB_DONE`；Pi 以原生 `turn_end`
  作为结束点。
- 新增 `QWEN_START_CMD`、`CURSOR_START_CMD`、`COPILOT_START_CMD`、
  `CRUSH_START_CMD`、`KIRO_START_CMD`、`PI_START_CMD`、`ZAI_START_CMD` 命令覆盖，以及 provider session
  binding、runtime launcher、deterministic stub 和 focused execution 测试。

</details>

<details>
<summary><b>v7.5.1</b> - MiMo Provider 发布面</summary>

- 在 README 公开 provider strip 增加带 Xiaomi 标识的 MiMo 徽标，并把
  首页定位更新为 8 个 CLI family。
- 将已提交的 MiMo native provider 集成纳入 7.5 线发布：managed `mimo`
  pane、`MIMO_START_CMD`、生成式 MiMo instructions，以及
  `mimo run --pure --format json` 完成解析。
- 同步 npm package metadata 和 release workflow 默认 tag 到新的 patch
  release。

</details>

<details>
<summary><b>v7.5.0</b> - 原生 CLI Provider 与首页同步</summary>

- 新增 Kimi managed native CLI provider 支持，并补齐更通用的 native CLI
  runtime 基础能力，覆盖 runtime spec、session binding、启动命令覆盖和清理路径。
- Kimi 和 Antigravity 的完成判定改为读取 provider 自有 session 或
  transcript 证据，不再要求模型打印 `CCB_DONE`。
- CCB auto-permission 对 Kimi 默认注入当前版本支持的 `--auto-approve`，
  同时识别 `--auto`、`--yes`、`-y`、`--yolo` 等旧版或别名标识，避免重复注入。
- 同步英文和中文 README 首页，刷新 hero assets，并统一为 7 个公开 CLI
  family 的定位。

</details>

<details>
<summary><b>v7.4.4</b> - Claude end_turn 与 npm 发布面修复</summary>

- Claude pane-backed ask 在 primary assistant response 带
  `stop_reason=end_turn`、已看到请求 anchor 且回复非空时，会立即产生
  `TURN_BOUNDARY(reason=assistant_end_turn)` 并正常完成，不再等到 900 秒
  reliability timeout。
- 空的 session-boundary terminal event 如果之前没有 assistant 回复证据，会
  终止为 `incomplete/task_complete_empty_reply`，并带
  `empty_provider_reply` 诊断。
- 恢复 `@seemseam/ccb` npm 发布面：补回 package metadata、CLI runner
  wrappers，以及等待 GitHub release assets 后再发布 npm 包的 Trusted
  Publishing workflow。
- 刷新 v7 README 首页：使用 canonical hero assets，默认 npm-first 安装，并
  更明确说明 `ccb_self` 是 CCB 内置的使用、配置、诊断和恢复专家。

</details>

<details>
<summary><b>v7.4.3</b> - PR #225 可靠性跟进修复</summary>

- 恢复 Claude launcher contract：inline `--settings` 只反映 materialized
  settings overlay，不再把 provider env 注入 settings JSON。
- 修复 Claude 在 WSL 调 Windows executable 时的环境透传：路径变量使用 `/p`
  转换，`ANTHROPIC_*` API 值作为 raw env 名透传。
- 加固 Antigravity resume lookup，兼容 SQLite 返回的 `bytes`、`str` 和
  `memoryview` metadata，并在查找失败时降级为 `--continue`。
- 新增 Claude settings contract、WSL API env forwarding 和 AGY resume
  fallback 的回归测试。

</details>

<details>
<summary><b>v7.4.2</b> - Self-supervision 与空回复防护</summary>

- 通过有界 provider-runtime snapshot、project-view 活动证据、suspicion
  envelope 和 self-first diagnosis path 加固 CCB self-supervision。
- Claude/Gemini hook 空回复、Codex protocol `task_complete` 空回复，以及
  AGY done-marker 空回复会终止为带 diagnostics 的 `incomplete`。
- 保留有意的无回复语义：`--silence` 成功仍是 `completed`，callback parent
  的 `callback_pending` 空回复仍合法，异常 silent completion 仍可诊断。
- 收紧默认 Role Pack install 和项目 role-lock refresh，覆盖
  `agentroles.archi` 与 `agentroles.ccb_self`。

</details>

<details>
<summary><b>v7.4.1</b> - Maintenance heartbeat 与 ccb_self 默认配置</summary>

- 加固项目级 maintenance heartbeat runner、schedule 处理、activation
  去重抑制和 diagnostics 证据路径，同时保持 heartbeat 只能显式启用。
- 空白项目内置默认配置新增 `ccb_self:codex` 并绑定 canonical
  `agentroles.ccb_self`，安装/更新时刷新推荐角色，但不改写已有自定义配置。
- CCB source 与 `agent-roles-spec` 的角色 id `agentroles.ccb_self` 对齐；
  `agentrole.ccb_self` 仅作为 legacy 输入兼容。
- 收紧生成配置的单一权威、Role Pack hook 的 CCB_BIN/project-root 路径和
  Codex prompt delivery acceptance guard。
- 新增 `ccb_self` expert manual、plan decisions，以及 expert reference 和
  communication recovery guidance 相关测试。

</details>

<details>
<summary><b>v7.4.0</b> - ccb_self 自维护角色</summary>

- 新增 `agentroles.ccb_self` 自维护 Role Pack 路径，覆盖 CCB 配置所有权、运行诊断、受保护恢复、工作链修复和单 agent 重启辅助。
- 完整 `ccb-config` 改为 `ccb_self` 私有内置 skill，不再作为全局继承 skill 发给所有 agent。
- 安装/更新的 Role Pack provisioning 默认安装或刷新推荐默认角色，包括 `agentroles.ccb_self`。
- 空白项目内置默认配置新增 `ccb_self:codex`，并绑定 `agentroles.ccb_self`；已有自定义配置仍可显式添加 `agentroles.ccb_self:codex`。

</details>

<details>
<summary><b>v7.3.8</b> - AGY adapter 与项目 tmux history</summary>

- 新增 Antigravity (`agy`) `pane_quiet` execution adapter，包含协议解析、命令分发、轮询和配套文档，可作为 CCB 托管 provider 运行。
- CCB 托管项目 tmux session 默认保留 50000 行 scrollback history，并覆盖 project namespace 创建/复用和 detached runtime fallback 路径。
- 在 authoritative project session 存在后，稳定重放 tmux mouse、vi key、clipboard、focus 和 history 策略。
- Claude startup 会尽量以内联 JSON 传递 `--settings`，避免非 ASCII source path 在 provider 启动链路中失效。

</details>

<details>
<summary><b>v7.3.7</b> - Ask 参数策略与 skill 指引</summary>

- inherited Claude、Codex、Droid ask skills 改为先按结果意图选择参数：`--silence`、`--compact`、`--artifact-reply` 或普通 `ask`。
- 依赖关系保持显式：只有 active 父任务必须等待子任务结果时才追加 `--callback`。
- artifact transport 与任务关系分离：需要精确输入或输入/输出保真时才使用 `--artifact-request` 和 `--artifact-io`。
- README / README_zh 的 Agent Collaboration 小节新增 ask 参数速查表。
- 新增 ask-parameter-policy plan tree、决策记录、参数矩阵和验证记录。

</details>

<details>
<summary><b>v7.3.6</b> - Provider memory ownership 清理</summary>

- 新增 provider memory ownership policy：Claude、Codex、OpenCode 的托管上下文不再把 provider-native project memory 重复注入 CCB 生成 bundle；Gemini 暂时保留旧行为，等待单独审计。
- 只在 provider user memory 源层过滤旧 CCB install marker blocks 和 legacy collaboration sections，不改写用户自己的 memory 文件。
- 默认 `.ccb/ccb_memory.md` template 升级到 v5，移除已由 CCB managed memory 提供的重复 Ask Communication 块。
- 新增 seed-aware shared memory migration：只升级未编辑过的旧生成模板，保留用户编辑过的项目记忆。
- Claude route-mode install 不再写 `~/.claude/rules/ccb-config.md`；install/uninstall 只删除带 CCB marker 的旧 external config，保留未标记用户文件。
- 修复 source runtime startup 的 tmux UI version detection import cycle，保持 `ccb_test` 路径可安全启动。

</details>

<details>
<summary><b>v7.3.5</b> - Tmux border hook 热修复</summary>

- 修复 tmux `after-select-pane` hook 可能持久保存 `/tmp/ccb-v...-release.../config/ccb-border.sh` 这类临时 release 路径，导致点击 pane 后报 `returned 127` 的问题。
- border hook 改为 `run-shell -b` 并带可执行 guard，脚本路径失效时不会反复刷 tmux 错误。
- `ccb update` 后 best-effort 刷新当前 tmux UI hooks，让从 v7.3.4 升级的 session 自动重写坏 hook，且不会把 UI 刷新失败算作 Role Pack provisioning failure。
- v7.3.4 已撤回并标记为 prerelease；稳定升级目标请使用 v7.3.5 或更新版本。

</details>

<details>
<summary><b>v7.3.4</b> - Withdrawn Prerelease</summary>

- `agentroles.archi` tooling 简化为统一使用全局 `@seemseam/archi` npm 包；CCB 不再拆分管理 Hippo、llmgateway、pip、venv、git 或 editable Archi 依赖。
- `ccb roles install/update/doctor agentroles.archi` 对齐 npm 提供的 `archi` CLI 以及包内 bundled Hippo/llmgateway capabilities。
- `bin/ccb-arch` 改为转发到 `archi`；缺失时直接提示 `npm install -g @seemseam/archi`。
- 修复 sidebar focus/refresh 行为，从 sidebar 选择 agent 不再不必要地 restart panes。
- 已撤回：tmux border hook 可能持久保存临时 release 路径并在之后报 `ccb-border.sh ... returned 127`；请使用 v7.3.5 或更新版本。
- 新增带保护的 `ccb_test` source entrypoint，用于隔离验证源码 checkout，不影响已安装的 CCB。
- 托管 OpenCode pane 通过 `opencode.json` 和 `OPENCODE_DISABLE_AUTOUPDATE=true` 禁用 autoupdate。
- 刷新继承的 `ccb-config` skills：支持 config-only、跟随用户语言、修复 YAML description quoting、菜单分组更清晰，并将 sidebar refresh 指引改为 restart panes。
- 新增 config-designer UI plan tree，并包含 main 分支的 `@percent` layout split token 与 Antigravity lifecycle cleanup 更新。

</details>

<details>
<summary><b>v7.3.3</b> - Withdrawn Draft</summary>

- 该版本因 sidebar focus/refresh regression 在稳定 rollout 前撤回，不作为推荐 release，也不应用于升级；请使用 v7.3.5 或更新版本。

</details>

<details>
<summary><b>v7.3.2</b> - 首次安装 Role Pack provisioning 修复</summary>

- 修复完全空白环境首次安装时的 Role Pack provisioning 问题：`install.sh` 在 `agentroles.archi` 尚未安装时先执行 update，可能导致 provisioning 未完成。
- 保留已有安装的刷新路径：仍先执行 `ccb roles update agentroles.archi`，当返回 role not installed / run roles install / run agent-roles install 时 fallback 到 `ccb roles install agentroles.archi`。
- 将可选 Role Pack provisioning 的 skip 提示从 update 对齐为 install。
- v7.3.1 仍是已发布版本，但存在空白环境首次安装 Role Pack provisioning bug；新安装和稳定推荐版本请使用 v7.3.2。

</details>

<details>
<summary><b>v7.3.1</b> - Agent Roles、Artifact Ask 和共享 Workspace Release</summary>

- 新增 daemon 管理的 ask artifact 传输：`--artifact-request`、`--artifact-reply` 和 `--artifact-io`，长输出可通过 callback 继续传递 artifact 路径。
- Agent Roles store 路径稳定到外部 `agent-roles` manager 和 `.roles/installed`，同时保留 `ccb.archi` 到 `agentroles.archi` 的兼容输入。
- 新增 `workspace_path`、`workspace_group` 共享 workspace 控制，以及 `provider_command_template`，可包裹 CCB 构造好的 provider 启动命令且不破坏 resume。
- 修复 root 下 Claude 启动、OpenCode 恢复旧会话后 `ccb clear` submit 时序、managed Neovim 原始 runtime 路径保留。
- 刷新继承的 `ask` 和 `ccb-config` skills，覆盖 submit-only ask 规则、artifact 模式、windows-first 配置、共享 workspace 和 provider command template。
- 稳定 WSL/root 发布测试，让非 root Claude 命令断言不再受 runner UID 影响。

</details>

<details>
<summary><b>v7.3.0</b> - Superseded Prerelease</summary>

- 已由 v7.3.1 supersede；远端 WSL Tests workflow 暴露了 root-sensitive Claude 命令断言。v7.3.0 GitHub release 保留为 prerelease，未上传正式 release artifacts。

</details>

<details>
<summary><b>v7.2.12</b> - Agent Roles Store Migration Release</summary>

- 默认使用外部 `agent-roles` package manager 执行 Role Pack install、update 和 sync。
- Role Pack payload 默认写入 spec-owned `.roles/installed` store。
- 自动将已有 legacy installed role snapshot 复制到 `.roles/installed`，不删除旧 store；迁移后 runtime lookup 只读取 spec-owned store。
- `ccb roles update --path ...` 也会通过 Agent Roles manager，path update 不再写 legacy CCB store。
- Supersede v7.2.11；v7.2.11 是未完成的 opt-in preview 发布，不应作为推荐版本使用。

</details>

<details>
<summary><b>v7.2.11</b> - Superseded Agent Roles Opt-In Preview</summary>

- 已被 v7.2.12 supersede，因为发布方向从 opt-in `CCB_AGENT_ROLES_MANAGER=1` preview 改为 default-on Agent Roles manager migration。

</details>

<details>
<summary><b>v7.2.10</b> - Role Pack Post-Update Hotfix</summary>

- 修复 managed `ccb update`：可选 Role Pack 和 Neovim provisioning 现在会交给新安装的 `ccb __post-update` entrypoint 执行，不再由旧 updater 进程继续跑。
- 将 legacy installed `ccb.archi` metadata 修复到 canonical `agentroles.archi`，旧 `source_path` 已不存在时会回退到当前 catalog source。
- 可选 post-update provisioning 失败仍只作为 warning；但设置 `CCB_INSTALL_ROLES=1`、`CCB_INSTALL_NEOVIM=1` 或 `CCB_POST_UPDATE_REQUIRED=1` 时，required provisioning 失败会让父 update 失败。
- 新配置说明统一使用 `agentroles.archi`；`ccb.archi` 仅保留为 legacy input alias。

</details>

<details>
<summary><b>v7.2.9</b> - Agent Roles Catalog Release</summary>

- 将生产架构角色从 CCB 源码树移出，改为从 `agent-roles-spec` 消费 `agentroles.archi`。
- 增加 catalog 驱动的 role list/install/update/sync/add/doctor 流程，并覆盖 installed-role metadata、project lock、digest pinning 和显式 re-add 更新。
- 将 role memory、CCB adapter memory、provider skills 和 Architec adapter hooks 投影到 managed provider home。
- 保留 `ccb.archi` 兼容输入别名，但写入 canonical `agentroles.archi` binding 和 lock。
- 修复 source runtime guard：从源码 checkout cwd 发起的 `ccb --project <allowed-test-dir> ...` smoke 命令现在会按目标项目校验，可通过发布 gate。
- 将生成的 soak、fastpath 和 storage cleanup smoke 目录显式传入 `CCB_SOURCE_ALLOWED_ROOTS`。
- 将 WSL mounted startup smoke 在 `/mnt/c/Temp` 下生成的项目显式传入 `CCB_SOURCE_ALLOWED_ROOTS`。
- 加固 Claude restart provider blackbox 测试：等待 running partial reply 反映出来后再断言。
- 加固 Role Pack CI fixture，使完整 GitHub Actions 测试不再依赖 sibling `agent-roles-spec` checkout。

</details>

<details>
<summary><b>v7.2.8</b> - Superseded Role Fixture Hotfix</summary>

- v7.2.8 已由 v7.2.9 取代；发布 gate 发现完整 GitHub Actions runner 没有 Role Pack 测试预期的 sibling `agent-roles-spec` checkout。

</details>

<details>
<summary><b>v7.2.7</b> - Superseded WSL Mounted Smoke Hotfix</summary>

- v7.2.7 已由 v7.2.8 取代；发布 gate 发现 Claude restart partial-reply 断言存在 provider blackbox timing race。

</details>

<details>
<summary><b>v7.2.6</b> - Superseded Official Smoke Root Hotfix</summary>

- v7.2.6 已由 v7.2.7 取代；发布 gate 发现 main Tests workflow 里的 WSL mounted startup smoke 也需要把 `/mnt/c/Temp` 下生成的项目传入 `CCB_SOURCE_ALLOWED_ROOTS`。

</details>

<details>
<summary><b>v7.2.5</b> - Superseded Source Runtime Guard Hotfix</summary>

- v7.2.5 已由 v7.2.6 取代；发布 gate 发现官方 soak、fastpath 和 storage cleanup smoke 需要把生成的测试根显式传入 `CCB_SOURCE_ALLOWED_ROOTS`。

</details>

<details>
<summary><b>v7.2.4</b> - Superseded Agent Roles Catalog Release</summary>

- v7.2.4 已由 v7.2.5 取代；发布 gate 发现源码 checkout cwd 发起的 `--project` 命令会在 CCBD real platform smoke 中被 source runtime guard 拒绝。

</details>

<details>
<summary><b>v7.2.3</b> - Root Install Support Validation Hotfix</summary>

- 保留 v7.2.2 的 root 安装确认行为：root 安装必须显式确认，卸载仍不受该门控影响。
- 保留安装 identity metadata 和 `ccb doctor` runtime user / owner / root 诊断。
- 修复 WSL 发布验证：安装 metadata 测试在需要时显式模拟非 root 身份，避免被 runner 实际 root 身份影响。

</details>

<details>
<summary><b>v7.2.2</b> - Root Install Confirmation Release</summary>

- 新增明确的 root 安装确认门：`install.sh install` 默认拒绝 root 执行，交互输入 `yes` 才允许，非交互 root 安装必须设置 `CCB_ALLOW_ROOT_INSTALL=1`。
- root 确认门只作用于安装，不阻止卸载清理，因此 root-owned 安装仍可移除。
- 安装 metadata 现在记录 root 状态、install user 和 sudo user 信息。
- `ccb doctor` 增加 runtime user、owner、root 状态诊断，并在 root 运行于非 root 项目时给出 warning。
- 修复架构审查提到的非阻断 build-info 类型卫生项：`read_build_info()` 现在返回 `dict[str, object]`。

</details>

<details>
<summary><b>v7.2.1</b> - Antigravity Runtime Follow-Up</summary>

- 补齐 `agy` / Google Antigravity 的 runtime 和 session 管线：provider runtime spec、client spec、provider-core 公共导出，以及 `.agy-<agent>-session` 命名。
- 增加命名 Antigravity pane 启动回归覆盖，包含 `AGY_START_CMD`、auto-permission、restore continuation 和 prepared-state 兼容。
- 对齐 README provider 列表和发布面，让 Antigravity 与 Codex、Claude、Gemini、OpenCode、Droid 一起出现在用户可见说明中。
- 明确 no-change reload 语义：配置无变化时执行非 dry-run `ccb reload` 返回 `noop` / `no_op`，且不发布 graph。
- 增加 Agent Roles 公开规格项目规划文档，作为未来 host-neutral RolePack 项目的设计记录。

</details>

<details>
<summary><b>v7.2.0</b> - Role Packs And Managed Tools Release</summary>

- 新增 Role Pack 体验面，内置 `ccb.archi` 架构师 role，包含 role memory、Codex/Claude skill 投影和项目 role lock。
- `ccb roles add ccb.archi:codex` 成为主要接入命令；config 保留 shorthand，运行时解析为本地 agent `archi`。
- `ccb roles install/update ccb.archi` 默认刷新 role 资产和依赖；安装/更新时交互提示，非交互场景会给出后续运行命令。
- 新增托管工具 window、sidebar 行和安全的 reload add/remove 行为。
- 包含 main 上已合入的 `agy` / Google Antigravity provider 支持。

</details>

<details>
<summary><b>v7.1.1</b> - Sidebar View Height Release</summary>

- 在 `[ui.sidebar.view]` 下新增三段 sidebar 高度配置：`agents_height`、`comms_height`、`tips_height`。
- 原生 sidebar 默认内部分区调整为 Agents `50%`、Comms `15%`、Tips `35%`。
- config 解析、project_view payload、reload 计划和 Rust sidebar TUI 都会传递并使用这些高度设置。
- 修复同名 agent reload/remount 可靠性：动态卸载后的 retired agent 可以用同名重新创建，不再被旧 runtime authority residue 阻塞；已停止的旧 session 记录仍可保留并供重建继承。
- 同步更新 Codex/Claude 继承的 `ccb-config` skill 文档和 reference，生成或迁移 windows topology 时会暴露这三个参数。

</details>

<details>
<summary><b>v7.1.0</b> - Dynamic Reload Release</summary>

- 新增 `.ccb/ccb.config` 显式热加载：`ccb reload --dry-run` 预览计划，`ccb reload` 应用支持的变更。
- 可以在当前 ccbd daemon 下动态挂载追加的 agent 和新增 window，不打断无关 pane。
- 可以动态卸载 idle 状态下被移除的 agent 和被移除的 idle window，并保留其他 agent pane。
- config signature drift 会被视为 reload-pending，而不是 daemon 重启触发器；busy 卸载和不安全替换仍会 fail closed。

</details>

<details>
<summary><b>v7.0.11</b> - Provider Activity And Sidebar Focus Release</summary>

- 通过 provider-native hook artifact 记录活动证据，让 sidebar 更准确地区分 active、pending、idle 和 failed provider 工作状态。
- project focus 成功后会失效 project view cache，并立即刷新同一 project session 内的 sidebar panes，减少鼠标/聚焦操作后的状态滞后。
- 普通 tmux pane 点击恢复为直接 `select-pane -t = ; send-keys -M`，避免走隐藏子进程路径造成点击响应变慢。
- 同步加固 namespace config、provider hook 安装设置、clipboard/runtime launch 路径和 Codex managed trust 处理，并补充聚焦回归测试。

</details>

<details>
<summary><b>v7.0.10</b> - Sidebar Tips And Tmux Controls Release</summary>

- 保持原生 sidebar 三栏比例稳定：Tree `1/3`、紧凑 Comms `1/4`、Tips `5/12`。
- 扩展默认 Tips：未配置自定义 tips 的项目会显示 pane 移动/resize、window 切换、copy mode、paste、help 等 tmux 快捷键。
- 保留右上角 `↻` 和 `×` 控制：`×` 执行 project-level `ccb kill`，`q` 和 `Esc` 只退出 sidebar。
- 文档和运行态继续保留 CCB-managed tmux Vim 控制：`mode-keys vi`、copy-mode `v` / `C-v` / `y`、`prefix+h/j/k/l`、`prefix+H/J/K/L`。

</details>

<details>
<summary><b>v7.0.9</b> - README v7 Redesign Release</summary>

- 重写 `README_zh.md`，围绕 v7 可见多 agent 工作台、任务优先上手、多 agent 方案对比、v7 界面速览、快速开始、tmux 常规操作、配置示例和安装更新流程组织内容。
- 新增 `assets/readme_v7/` 真实 v7 终端截图，用于 README 演示。
- 保留 README 重设计计划和辅助资料到 `docs/plantree/`。
- 保持 v7.0.8 的 runtime、`ccb clear`、config overlay 和 sidebar 修复不变，只刷新 GitHub 面向用户的文档包。

</details>

</details>

完整历史请看 [CHANGELOG.md](CHANGELOG.md)。
