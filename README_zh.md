<div align="center">

# CCB - 可见、可控的多 Agent CLI 工作台

<p>
  <img src="https://img.shields.io/badge/v7-multi--agent--workspace-0B7285?style=for-the-badge" alt="v7 multi-agent workspace">
  <img src="https://img.shields.io/badge/terminal-tmux-2F9E44?style=for-the-badge" alt="tmux">
  <img src="https://img.shields.io/badge/providers-Codex%20%7C%20Claude%20%7C%20Gemini%20%7C%20OpenCode-CF1322?style=for-the-badge" alt="providers">
</p>

[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20WSL-lightgrey.svg)]()
[![Python](https://img.shields.io/badge/python-3.10%2B-blue.svg)]()
[![Version](https://img.shields.io/badge/version-7.1.1-orange.svg)]()
[![Release](https://img.shields.io/badge/install-release--first-orange.svg)]()

**中文** | [English](README.md)

[为什么需要多 agents](#为什么需要多-agents) · [方案对比](#多-agents-方案怎么选) · [v7 界面](#v7-界面速览) · [快速开始](#快速开始) · [tmux 常规操作](#tmux-常规操作) · [配置团队](#配置-agent-团队) · [安装更新](#安装和更新)

</div>

---

## 为什么需要多 agents

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

## 多 agents 方案怎么选

多 agents 不是一种固定形态。先用下面这张表判断大方向，细节可以展开看。

| 方案 | 一句话 | 更适合你如果 |
| :--- | :--- | :--- |
| [Claude Code 原生 subagents](https://code.claude.com/docs/en/sub-agents) / [agent teams](https://code.claude.com/docs/en/agent-teams) | Claude Code 内部的原生分工。 | 你主要留在 Claude Code，并接受更多协调由 Claude lead 处理。 |
| [Hive / OpenHive](https://github.com/aden-hive/hive) | 面向生产工作流的多 agent harness。 | 你要状态、恢复、观测、成本控制和图式工作流。 |
| CCB | 可见、可控、混合 provider 的本地 CLI agent 工作台。 | 你要把 Codex、Claude、Gemini、OpenCode 等真实 CLI 放到一个项目终端里操作。 |

<details>
<summary><b>展开：模型、可控性、上下文和复杂工作流怎么区别？</b></summary>

| 关键问题 | Claude Code 原生 | Hive / OpenHive | CCB |
| :--- | :--- | :--- | :--- |
| 能否使用不同家的模型 | 可给 teammate / subagent 指定 Claude 模型；整体仍在 Claude Code 体系内。 | 通过 LiteLLM 路线支持大量 hosted / local provider。 | 按 agent 选择 Codex、Claude、Gemini、OpenCode、Droid 等，并可设置独立 model / key / url。 |
| 过程是否可见 | in-process 或 split panes，取决于模式和终端。 | 强调 runtime observability 和控制台视角。 | 默认就是 tmux 可见 pane，用户能直接点击、输入、复制、观察每个 CLI。 |
| 拓扑是否可控 | 可自然语言指定队友，但运行时协调较多交给 lead。 | 由目标生成图式拓扑，偏 harness。 | 配置文件显式定义 agent、窗口、pane、worktree 和 sidebar。 |
| 上下文是否可管理 | subagent / teammate 有独立上下文；team 有任务和消息状态。 | 角色记忆、状态持久化、恢复能力是核心卖点。 | 每个 CLI 保留自己的 provider 会话；项目共享记忆和 per-agent 记忆可选。 |
| 最适合的落点 | Claude Code 内部的快速委派和团队模式。 | 业务流程自动化、长期运行和生产可靠性。 | 本地开发、代码协作、跨 provider CLI agent 可视化工作台。 |

CCB 也支持复杂工作流，但它不是自动生成 DAG 的 harness；复杂度主要通过 `.ccb/ccb.config`、多 window、角色记忆、worktree、model/API 配置和 ask/callback 路由显式设计。

</details>

## CCB 是什么

CCB 是一个项目级 agent CLI 工作台。它用 tmux 管理多个真实 CLI agent，把启动、恢复、通信、配置、窗口和运行态聚合在一个项目里。

- **真实 CLI，不是模拟面板**：每个 agent pane 都运行对应 provider 的真实 CLI。
- **可见协作**：sidebar 展示窗口、agent 状态和通信区；用户可以用鼠标直接切 pane。
- **混合 provider**：一个项目里可以同时跑 Codex、Claude、Gemini、OpenCode、Droid 和 Antigravity（`agy`）。
- **项目级配置**：`.ccb/ccb.config` 决定团队、布局、窗口、worktree、model、key、url。
- **可恢复运行态**：CCB 后台守护 agent pane，支持 attach、恢复和项目级清理。
- **显式协作通道**：agent 可以通过 `/ask`、`$ask`、callback 和 silence 进行委派与交接。

## v7 界面速览

下面截图来自 `ccb_test2` 项目的真实深色终端会话。图片上的标注只解释区域，不代表必须记住所有快捷键。

<p align="center">
  <img src="assets/readme_v7/ccb-test2-terminal-annotated.png" alt="CCB v7 终端工作台区域说明" width="960">
</p>

| 区域 | 作用 |
| :--- | :--- |
| Sidebar | 显示当前 window、agent 列表、provider 标签、当前选中 agent 和状态提示。 |
| Comms | 显示 ask/callback 等协作消息和通信状态。 |
| Agent pane | 每个 pane 是一个真实 CLI 会话，例如 Codex 或 Claude。 |
| 当前输入目标 | 状态栏和 pane 边框会提示当前输入会发给哪个 agent。 |
| 状态栏 | 显示项目名、当前 agent、CCB 版本、日期和常用鼠标/键盘提示。 |
| Window 分组 | v7 可用 `[windows]` 把 agent 按 main、work、review、research 等窗口分组。 |

Sidebar 相关实现使用并借鉴了 [tmux-agent-sidebar](https://github.com/hiroppy/tmux-agent-sidebar) 的思路，在此表示感谢。

## 快速开始

### 1. 安装或更新

新用户优先使用 release 包。到 [Releases](https://github.com/SeemSeam/claude_codex_bridge/releases) 下载与你的平台匹配的包，解压后安装：

```bash
tar -xzf ccb-*.tar.gz
cd ccb-*
./install.sh install
```

如果你已经装过 CCB：

```bash
ccb update
```

<details>
<summary><b>源码安装只建议开发或临时兜底使用</b></summary>

```bash
git clone https://github.com/SeemSeam/claude_codex_bridge.git
cd claude_codex_bridge
./install.sh install
```

源码安装会让全局 `ccb` / `ask` 链接回当前 checkout。普通用户更建议安装或更新到稳定 release。

</details>

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

[ui.sidebar.view]
agents_height = "50%"
comms_height = "15%"
tips_height = "35%"
comms_limit = 3
```

如果你不确定应该如何分组、要几个 worker、哪些 agent 用 worktree、哪些 agent 需要独立模型或 API，可以先让支持 skill 的 agent 使用 `ccb-config` 和你讨论并生成配置方案。

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

## 日常操作

| 目标 | 命令 |
| :--- | :--- |
| 启动或重新 attach 当前项目工作台 | `ccb` |
| 安全启动，保留各 agent 配置的权限策略 | `ccb -s` |
| 重建运行态，保留配置和同名 managed agent 历史 | `ccb -n` |
| 停止当前项目后台 | `ccb kill` |
| 强制清理当前项目残留后再重建 | `ccb kill -f` 后接 `ccb -n` |
| 更新到最新稳定 release | `ccb update` |
| 查看当前使用的配置层 | `ccb config validate` |
| 预览配置热加载计划，不修改 tmux | `ccb reload --dry-run` |
| 应用支持的配置变更，不重启其他 agent | `ccb reload` |

## tmux 常规操作

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

## 配置 agent 团队

CCB 配置有三层，优先级从低到高：

1. 内置默认配置。
2. 用户配置 `~/.ccb/ccb.config`。
3. 项目配置 `.ccb/ccb.config`。

更高层会整体替换低层，不做局部合并。当前项目的权威配置文件是 `.ccb/ccb.config`；旧路径 `.ccb_config/ccb.config` 只应作为迁移参考。

`.ccb/ccb.config` 主要配置这些内容：

| 配置内容 | 写法或位置 | 说明 |
| :--- | :--- | :--- |
| window 分组 | `[windows]` | 把 agent 分到 `main`、`work`、`review`、`research` 等 tmux window。 |
| agent 名称和 provider | `main:codex`、`reviewer:claude` | 名称用于界面、ask 路由和记忆文件；provider 决定启动哪家 CLI。 |
| 工作区隔离 | `worker1:codex(worktree)` | 给实现类 agent 独立 git worktree，降低互相覆盖的风险。 |
| sidebar 行为 | `[ui.sidebar]` | 控制 sidebar 是否每个 window 都显示、宽度和 Comms 高度。 |
| 单 agent 模型/API | `[agents.<name>]` | 可为不同 agent 配 `model`、`key`、`url` 等。 |
| 角色说明 | `[agents.<name>] description = "..."` | 给 agent 一个简短职责说明；更长的工作流规则建议写到 memory。 |

在已启动的项目里修改 `.ccb/ccb.config` 后，先运行 `ccb reload --dry-run` 预览计划，再运行 `ccb reload` 应用。显式 reload 可以动态新增 agent、新增 window、卸载 idle agent、删除 idle window，同时保持无关 agent 和 pane 继续运行。它不是后台文件监听；busy agent 卸载、provider 替换、agent 移动和任意布局重排会被拒绝，不会 kill 现有 pane。

如果你想先讨论配置而不是手写，可以直接用 `ccb-config` skill 描述目标团队。它会先提出完整方案，确认后再修改 `.ccb/ccb.config`。

<details>
<summary><b>配置格式示例：单窗口、多 window、per-agent 模型/API</b></summary>

### 单窗口紧凑配置

```text
cmd; main:codex, worker1:codex(worktree); reviewer:claude
```

含义：

- `cmd` 是 shell pane，不是 agent。
- `main`、`worker1`、`reviewer` 是 agent 名称。
- `codex`、`claude` 是 provider。
- `;` 表示左右分栏，`,` 表示上下堆叠。
- `(worktree)` 表示该 agent 使用独立 git worktree。

### 多 window 拓扑

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

[ui.sidebar.view]
agents_height = "50%"
comms_height = "15%"
tips_height = "35%"
comms_limit = 3
```

注意：`cmd` 只属于紧凑/混合单窗口布局；`[windows]` 拓扑里不要写 `cmd`。

### 给 agent 单独配置模型、API key 或 base URL

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

## 使用 ccb-config skill 配置

如果你不想手写 `.ccb/ccb.config`，可以让支持 skill 的 agent 使用 `ccb-config` 帮你设计。推荐先用自然语言描述项目目标、并行程度、窗口分组、worktree 隔离、provider/model/API 偏好，让它和你讨论后提出完整配置方案。

示例：

```text
$ccb-config 为一个 Python library 设计团队：main 负责任务拆分，三个 worker 使用 worktree 并行实现，一个 reviewer 做回归和风险审查。保留单窗口还是拆成 main/work/review 三个 window 由你建议。
```

<details>
<summary><b>ccb-config 的写入流程和边界</b></summary>

1. 你用自然语言描述项目和团队目标。
2. `ccb-config` 读取当前配置权威层，判断是新建、修改还是迁移。
3. 它先提出完整配置方案，不应直接改文件。
4. 你确认后，它只修改 `.ccb/ccb.config`。
5. 它运行配置校验，并在可动态应用时提醒你使用 `ccb reload --dry-run` / `ccb reload` 生效。

默认情况下，`ccb-config` 不会修改 `.ccb/ccb_memory.md` 或 `.ccb/agents/<agent>/memory.md`。只有当你明确要求“设计工作流记忆”或“写入角色记忆”时，才应该修改这些 memory 文件。

</details>

## Agent 之间如何协作

普通 `ask` 是提交即返回：把任务交给目标 agent 后，当前 agent 不应该轮询等待。

| 场景 | 推荐方式 |
| :--- | :--- |
| 人直接指定目标 agent | `/ask reviewer ...` 或 `$ask reviewer ...` |
| 当前 agent 在 active CCB task 内，必须等子任务结果才能回复 | `ask --callback reviewer` |
| 当前 agent 派发独立任务，成功结果不需要回来 | `ask --silence worker1` |
| 调试队列、诊断状态 | `pend`、`watch`、`ping` 等只作为诊断工具使用 |

<details>
<summary><b>Callback 为什么重要</b></summary>

如果 agent A 正在处理一个来自用户的 CCB task，又需要 agent B 的结果才能完成，就应该用 callback。CCB 会记录 parent/child 关系，让 A 当前 turn 结束；等 B 完成后，CCB 再把结果作为 continuation 交回 A。这样不会阻塞队列，也不会让 agent 靠轮询浪费上下文。

</details>

## 编辑器工作流

<p align="center">
  <img src="assets/nvim.png" alt="Neovim 集成多模型代码审查" width="860">
</p>

CCB 不要求你离开编辑器。常见方式是：编辑器负责写代码，CCB 终端负责多 agent 规划、实现、审查、测试和交接。

## 安装和更新

### 环境要求

- Python 3.10+
- `tmux`
- 至少一个你要使用的 agent CLI，例如 Codex、Claude、Gemini、OpenCode 或 Droid
- Linux、macOS 或 WSL

当前 v7 / 新版本不声明原生 Windows 支持。原生 Windows 只支持到 v5 线；如果你在 Windows 上使用新版本，推荐使用 WSL，并让 `ccb` 与 agent CLI 都运行在 WSL 内。

### Release 优先

首次安装推荐使用 [GitHub Releases](https://github.com/SeemSeam/claude_codex_bridge/releases) 的 release 包；已安装用户推荐：

```bash
ccb update
```

源码 checkout 安装只适合开发、验证修复或 release 包暂不可用时临时使用。

### 卸载

```bash
ccb uninstall
ccb reinstall

# 备用方式：在安装包或源码目录内
./install.sh uninstall
```

## 常见问题

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

使用 `ccb-config` 描述你想要的窗口分组，例如 main/work/review。迁移时应保留旧 agent 名称、provider、worktree 标记、model/key/url 等字段，确认后再写入 `[windows]`。

</details>

<details>
<summary><b>sidebar helper 不可用</b></summary>

优先使用 release 包，因为 release 包会携带或处理 sidebar helper。源码安装时如果缺少可用的预编译 helper，可能需要本机 Rust 工具链来构建。

</details>

## 社区和致谢

📧 Email: `bfly123@126.com`

💬 微信: `seemseam-com`

感谢 [Linux.do 社区](https://linux.do) 在测试、反馈和讨论中的支持。

感谢 [tmux-agent-sidebar](https://github.com/hiroppy/tmux-agent-sidebar) 提供的 sidebar 思路和启发。

<div align="center">
  <img src="assets/weixin.jpg" alt="微信群" width="300">
</div>

## 新版本记录

v7 线重点：

- 原生 CCB sidebar，支持 per-window 项目视图、agent 状态和鼠标切换。
- Comms 从 agent 活动中拆分，通信状态和 provider pane 活动更清晰。
- 新增 `version = 2` `[windows]` 拓扑，可按工作流分组多个 tmux window。
- 显式 `ccb reload` 支持动态加载 agent/window 和 idle 卸载，不重启无关 agent。
- 保留 compact / hybrid 旧配置兼容，单窗口团队不需要强制迁移。
- 加固 tmux、Ghostty、release helper、Codex trust 和 provider 会话恢复路径。

<details open>
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

完整历史请看 [CHANGELOG.md](CHANGELOG.md)。
