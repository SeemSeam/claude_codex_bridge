<div align="center">

# CCB

**Designed around agent parity**
**Visible, controllable multi-agent cooperative TUI workspace**

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

**English** | [中文](README_zh.md)

[Quick Start](#quick-start) · [v7 UI](#v7-ui-tour) · [Rich Mode](#rich-mode-new) · [Mobile App](#mobile-app) · [Configure Agents](#configure-your-agent-team) · [User Guide](docs/manuals/user-guide/) · [Developer Guide](docs/manuals/developer-guide/)

<p align="center">
  <img src="assets/readme_v7/ccb-hero-en.png" alt="CCB v7 visible multi-agent CLI workspace" width="960">
</p>

</div>

---

## Supported CLIs

Mix CLIs per agent in `.ccb/ccb.config`; actual availability depends on the local CLI installation and account access.

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

**New role specification**: package skills, memory, and tool dependencies into self-contained Role Packs, then create hot-loadable and removable specialist agents.

## Why CCB?

| See the work | Mix providers | Keep control |
| :--- | :--- | :--- |
| Every agent is a real terminal with layout control. | Run multiple CLIs concurrently from one command. | Stable background communication for multi-line task orchestration. |

## Quick Start

## Repository Layout

This repository now carries both the CCB core and the mobile controller source:

- core CLI, ccbd, mobile gateway, and Python tests live at the repository root;
- Flutter mobile app source, mobile tools, and mobile planning docs live under
  [`mobile/`](mobile/).

The mobile app remains a separate build surface inside the monorepo. Core
commands such as `ccb update mobile` should consume released mobile artifacts
or the mobile release manifest, not Android build outputs checked into source.

### 1. Install or update

New installs should use the npm package:

```bash
npm install -g @seemseam/ccb
```

After CCB is installed, use CCB's updater:

```bash
ccb update
```

Install or refresh the optional rich media workbench; it bundles verified binaries where possible and installs only the required terminal/media/font dependencies through the platform package manager:

```bash
ccb update rich
```

After rich is enabled, plain `ccb` opens the rich WezTerm launcher unless it is already running inside a CCB-managed rich WezTerm; use `ccb uninstall rich` to return to the normal terminal startup.

Install or refresh the optional mobile controller setup:

```bash
ccb update mobile
```

This command checks the local mobile gateway prerequisites, guides Tailscale
login/Serve setup when needed, keeps the gateway loopback-only, and prints the
current Android APK download link plus pairing steps.

<details>
<summary><b>GitHub release package and source install fallbacks</b></summary>

If npm is not available in your environment, download the matching package from [Releases](https://github.com/bfly123/claude_code_bridge/releases):

```bash
tar -xzf ccb-*.tar.gz
cd ccb-*
./install.sh install
```

Source install is for development or temporary fallback use:

```bash
git clone https://github.com/bfly123/claude_code_bridge.git
cd claude_code_bridge
./install.sh install
```

Source installs link global `ccb` / `ask` back to the checkout. Regular users should prefer the npm package.

</details>

Out of the box, run `ccb` from your project directory. If startup reports that `.ccb` cannot be created automatically or that the project anchor is missing, create `.ccb` manually:

```bash
mkdir -p .ccb
```

### 2. Create project config

Create `.ccb/ccb.config` in your project root. For v7, it is better to understand config from multi-window topology first: `[windows]` defines tmux windows and agent groups, `agent:provider` defines which CLI each agent uses, and `(worktree)` gives an agent its own git worktree.

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

If you are not sure how to group windows, how many workers you need, which agents should use worktrees, or which agents need separate models or API routes, ask `ccb_self`. It is CCB's built-in self-agent: it understands CCB commands, config authority, roles, windows, reload behavior, and common recovery paths, and can design the config with its private `ccb-config` skill. Blank projects include `ccb_self`; existing custom configs can add it with `ccb roles add agentroles.ccb_self:codex`.

Validate the config:

```bash
ccb config validate
```

Start the workspace:

```bash
ccb
```

### 3. Collaborate

Type directly in an agent pane, or route work between agents:

```text
/ask reviewer review the latest parser changes and list blocking issues.
```

Agents can also call `/ask` from workflow orchestration to delegate and hand off work automatically.

### v7 UI Tour

| Region | Purpose |
| :--- | :--- |
| Sidebar | Shows refresh/close CCB controls, windows and agents, internal communication state, and tips that can be edited in config and hot-reloaded. |
| Mouse control | Click to switch windows, agents, and panes; refresh, kill, or delete communication entries from the communication area. |
| Workspace | Every pane is a real CLI. Switch by mouse or tmux shortcuts. |
| Useful shortcuts | `Ctrl-b h/j/k/l` switches adjacent panes; `Ctrl-b z` zooms or restores the current CLI pane. |

<a id="rich-mode-new"></a>

### Rich Mode (NEW!)

Run `ccb update rich` to install the optional rich workbench; it bundles Yazi where possible, uses WezTerm for the rich terminal surface, and gives Markdown rendering plus image/PDF/video previews. After installation, plain `ccb` automatically opens this rich launcher unless it is already running inside a CCB-managed rich WezTerm; `ccb rich` remains available as an explicit launcher.

<p align="center">
  <img src="assets/readme_v7/rich-workbench.png" alt="CCB rich workbench with Yazi preview in WezTerm" width="860">
</p>

<a id="mobile-app"></a>

### Mobile App (Android Alpha)

CCB 8.0.7 includes the Flutter CCB Mobile source under [`mobile/`](mobile/)
and publishes an Android APK as a GitHub Release asset:

- [Download CCB Mobile v8.0.7 APK](https://github.com/bfly123/claude_code_bridge/releases/download/v8.0.7/ccb-mobile-v8.0.7.apk)
- App source: [`mobile/app`](mobile/app)
- Server/gateway source: [`lib/mobile_gateway`](lib/mobile_gateway)

The mobile app is a remote controller for real server-side CCB projects. It can
discover all mounted CCB projects exposed by the server-wide mobile gateway,
switch windows and agents, render the agent transcript, send text as pane-native
input, open terminal views, and upload/download image or document attachments
through the authenticated gateway.

Recommended first-time setup:

```bash
ccb update mobile
```

Then follow the printed steps:

1. Install and sign in to Tailscale on the desktop/server and the phone.
2. Install the APK on Android.
3. Run `ccb update mobile` on the desktop/server.
4. Open CCB Mobile and scan the QR printed by the terminal.

Security boundaries:

- The CCB gateway remains bound to loopback, such as `127.0.0.1:8787`.
- Tailscale Serve is used for tailnet access; Tailscale Funnel is not enabled.
- CCB does not store Tailscale passwords, OAuth tokens, admin API tokens, or
  alter tailnet ACLs/grants.
- The phone only receives the scopes granted by the pairing profile, such as
  view, content, terminal, file upload, and file download.

### Agent Roles Spec And Role Catalog

CCB supports the [Agent Roles Spec](https://github.com/SeemSeam/agent-roles-spec), a host-neutral way to package specialist agents as portable Role Packs. The same repository also acts as the public role catalog.

<details>
<summary><b>Available catalog roles</b></summary>

| Role | Basic function |
| :--- | :--- |
| `agentroles.ccb_self` | CCB self-maintenance, config help, runtime diagnosis, guarded recovery, and workflow orchestration. |
| `agentroles.archi` | Architecture review, boundary checks, coupling analysis, maintainability risk, and practical next-step gates. |
| `agentroles.frontend_engineer` | Frontend design and implementation, design-system work, accessibility, browser QA, and reviewed AGY delegation. |
| `agentroles.mobile_app_engineer` | Mobile app design and implementation across iOS, Android, React Native, Expo, Flutter, SwiftUI, and Jetpack Compose. |
| `agentroles.mother` | Role creation, role-source audit, role research, blueprinting, and Agent Roles spec compliance review. |
| `agentroles.su_ccb` | SU-CCB workflow operation for requirement analysis, planning, dispatch, review gates, archive, and recovery. |

</details>

### Contact

- Email: `bfly123@126.com`
- **[Telegram group & contact](https://t.me/+BKn03v8I_ehmYzRk)**
- WeChat: `seemseam-com`

<p align="center">
  <img src="assets/weixin.jpg" alt="WeChat group" width="240">
</p>

---

## More Reading

Start with Quick Start for first use. Open only the reference area you need.

| Topic | When to open it |
| :--- | :--- |
| Concepts and positioning | What CCB is, why multi-agent workflows help, and how CCB compares with other approaches. |
| Daily operation | Common commands and tmux basics for routine use. |
| Configuration and roles | `.ccb/ccb.config`, Role Packs, and `ccb_self` configuration help. |
| Collaboration and maintenance | Ask routes, install/update notes, FAQ, and credits. |
| Release notes | Current v7 highlights and historical release entries. |

<details open>
<summary><b>Concepts and positioning</b></summary>

### What Is CCB?

CCB is a project-level agent CLI workspace. It uses tmux to manage multiple real CLI agents and unifies startup, restore, communication, configuration, windows, and runtime state for one project.

- **Real CLI sessions, not fake panels**: every agent pane runs the actual provider CLI.
- **Visible collaboration**: the sidebar shows windows, agents, status, and communication; users can switch panes by mouse.
- **Mixed providers**: one project can run Codex, Claude, Gemini, Kimi (`kimi`), MiMo (`mimo`), Qwen (`qwen`), Cursor (`cursor`), Copilot (`copilot`), Crush (`crush`), Kiro (`kiro`), Pi (`pi`), Z.ai CLI (`zai`), OpenCode, Droid, and Antigravity (`agy`) together.
- **Project config**: `.ccb/ccb.config` defines the team, layout, windows, worktrees, model, key, and url.
- **Built-in CCB expert**: blank projects include `ccb_self`, a self-maintenance agent with deep CCB knowledge for usage guidance, config design, diagnostics, recovery, and workflow repair.
- **Roles**: a new role packaging model that lets specialized agents carrying
  "heavy weapons" such as independent skills, memory, and tool dependencies
  instantly land in a target project as hot-loadable, removable agents, while
  leaving the main environment, user global config, and project runtime state
  unchanged.
- **Recoverable runtime**: CCB supervises agent panes and supports attach, restore, and project-scoped cleanup.
- **Explicit collaboration channel**: agents can delegate through `/ask`, `$ask`, callback, and silence routes.

### Why Multi Agents

A single agent is enough for small tasks. Once work needs planning, parallel edits, review, testing, and handoff, multi agents help separate roles, context, models, and execution. CCB focuses on putting multiple real CLI agents into one visible terminal workspace.

| Value | Plain meaning |
| :--- | :--- |
| Role separation | `main` plans, `worker` implements, `reviewer` checks risk. |
| Parallel progress | One agent can edit while another reads docs, validates, or reviews. |
| Model and context layering | Different agents can use different providers, models, APIs, worktrees, and memory. |

<details>
<summary><b>Why one agent starts to struggle</b></summary>

- Mixed roles reduce context focus: one conversation tries to architect, edit, test, and review itself.
- Complex task execution has a ceiling: long work needs split points, handoffs, checks, and rollback boundaries.
- Cost pressure is higher: if every step needs the strongest model, even simple sub-tasks become expensive.
- Tool and skill management becomes harder: a "does everything" agent also accumulates too much authority and instruction load.
- Serial waiting is inefficient: when one agent is reading logs or running tests, other independent work cannot naturally continue.

</details>

### Which Multi-Agent Approach Should You Use?

Multi-agent systems are not one fixed shape. Use the short table first; expand the details only if you are comparing tradeoffs.

| Approach | One-line summary | Best fit |
| :--- | :--- | :--- |
| [Claude Code native subagents](https://code.claude.com/docs/en/sub-agents) / [agent teams](https://code.claude.com/docs/en/agent-teams) | Native delegation inside Claude Code. | You mostly stay in Claude Code and want more coordination handled by a Claude lead. |
| [Hive / OpenHive](https://github.com/aden-hive/hive) | Production-oriented multi-agent workflow harness. | You need state, recovery, observability, cost controls, and graph workflows. |
| CCB | Visible, controllable local CLI-agent workspace with mixed providers. | You want Codex, Claude, Gemini, Kimi, MiMo, Qwen, Cursor, Copilot, Crush, Kiro, Z.ai CLI, OpenCode, Antigravity, and other real CLIs in one project terminal. |

<details>
<summary><b>Details: model choice, control, context, and complex workflows</b></summary>

| Question | Claude Code native | Hive / OpenHive | CCB |
| :--- | :--- | :--- | :--- |
| Different model vendors? | Can choose Claude models for teammates/subagents; overall path is still Claude Code. | LiteLLM route covers many hosted and local providers. | Choose Codex, Claude, Gemini, Kimi, MiMo, Qwen, Cursor, Copilot, Crush, Kiro, Z.ai CLI, OpenCode, Droid, Antigravity, and per-agent model/key/url. |
| Is the process visible? | In-process or split panes depending on mode. | Runtime observability and dashboard-style control. | Real tmux panes by default; users can click, type, copy, and inspect each CLI. |
| Is topology controllable? | Natural-language teammate setup, with much coordination handled by the lead. | Goal-generated graph-like topology, harness oriented. | Config explicitly defines agents, windows, panes, worktrees, and sidebar behavior. |
| Is context manageable? | Subagents/teammates have separate contexts; teams have task and message state. | Role memory, durable state, and recovery are core design points. | Each CLI keeps its provider session; shared project memory and per-agent memory are optional. |
| Best landing zone | Fast delegation inside Claude Code. | Business automation, long-running workflows, production reliability. | Local development with visible cross-provider CLI agents. |

CCB also supports complex workflows, but it is not an automatic DAG generator. You design complexity explicitly through `.ccb/ccb.config`, windows, role memory, worktrees, model/API settings, and ask/callback routes.

</details>

</details>

<details>
<summary><b>Daily operation</b></summary>

### Daily Operation

| Goal | Command |
| :--- | :--- |
| Start or reattach the current project workspace | `ccb` |
| Safe start, keeping configured/manual permission behavior | `ccb -s` |
| Rebuild runtime state while keeping config and same-name managed agent history | `ccb -n` |
| Stop this project's background runtime | `ccb kill` |
| Force cleanup before rebuilding | `ccb kill -f` then `ccb -n` |
| Update to the latest stable release | `ccb update` |
| Install or refresh the optional rich workbench | `ccb update rich` |
| Remove rich mode and return normal startup | `ccb uninstall rich` |
| Open the rich workbench | `ccb rich` |
| Inspect the active config layer | `ccb config validate` |
| Preview a config reload plan without changing tmux | `ccb reload --dry-run` |
| Apply supported config changes without restarting other agents | `ccb reload` |

### tmux Basics

CCB can be used mostly with the mouse, but learning a few tmux shortcuts makes daily work much faster. This section lists only common tmux keyboard operations.

In this section, `<prefix>` means `Ctrl-b`: **press `Ctrl-b`, release it, then press the function key**. Use an English input method for the function key so punctuation keys are not intercepted by another IME.

| Goal | Function key | Notes |
| :--- | :--- | :--- |
| Move to an adjacent pane | `h` / `j` / `k` / `l` or Arrow keys | CCB-managed tmux sessions enable Vim-style pane focus keys. |
| Resize current pane | `H` / `J` / `K` / `L` | Repeatable resize keys in Vim directions. |
| Move to the next pane | `o` | Fast rotation when direction does not matter. |
| Zoom / unzoom current pane | `z` | Useful for long output, diffs, and logs. |
| Open window / pane list | `w` | Pick a target in larger layouts. |
| Next window | `n` | Switch to the next tmux window. |
| Previous window | `p` | Switch to the previous tmux window. |
| Jump to numbered window | `0` to `9` | Jump directly by tmux window number. |
| Enter copy / scroll mode | `[` | Review history, scroll, and select text. |
| Exit copy / scroll mode | `q` or `Esc` | Return to normal input. |
| Paste tmux buffer | `]` | Paste content copied into tmux's own buffer. |
| Detach session | `d` | Leave the display without stopping CCB; you can reattach later. |

Copy and paste tips:

- **Mouse copy**: in most terminals, drag with the left mouse button to copy; if tmux captures the drag, enter copy / scroll mode first.
- **Bypass tmux selection**: many terminals support `Shift + mouse drag` for native terminal selection.
- **System paste**: Linux/Windows terminals usually use `Ctrl+Shift+V`; macOS terminals usually use `Cmd+V`.
- **tmux paste**: if the content is in the tmux buffer, use function key `]`.

<details>
<summary><b>More common tmux operations</b></summary>

| Goal | Function key | Notes |
| :--- | :--- | :--- |
| Scroll in copy / scroll mode | `PageUp` / `PageDown` / `Arrow keys` | Terminal support can vary. |
| Start selection in copy / scroll mode | `v` | CCB uses tmux vi copy mode. |
| Copy selection in copy / scroll mode | `y` | Copies to the tmux buffer and exits copy mode. |
| Search in copy / scroll mode | `Ctrl-s` / `Ctrl-r` | Commonly forward / backward search. |
| Create a window | `c` | Use only when you intentionally need another shell. |
| Rename a window | `,` | Helps identify multi-window workflows. |
| Show tmux key help | `?` | Useful when you forget a shortcut. |

New users should avoid pane/window killing shortcuts at first. To stop a CCB project, prefer CCB's project-level shutdown command instead of killing one recoverable pane by accident.

</details>

</details>

<details>
<summary><b>Configuration and roles</b></summary>

### Configure Your Agent Team

CCB resolves config in three layers, from lowest to highest priority:

1. Built-in default config.
2. User config at `~/.ccb/ccb.config`.
3. Project config at `.ccb/ccb.config`.

Higher layers replace lower layers as a whole; they are not merged. The project authority file is `.ccb/ccb.config`. The old `.ccb_config/ccb.config` path is legacy migration evidence only.
The built-in default is a v2 `[windows]` config with `agent1`, `agent2`, `agent3`, and `ccb_self`. The optional rich workbench can be installed with `ccb update rich`; once enabled, normal `ccb` startup uses the rich launcher unless you run `ccb uninstall rich`. The default `ccb_self` agent uses `codex` and is bound to `agentroles.ccb_self`.

`.ccb/ccb.config` mainly controls:

| Config area | Syntax or location | Notes |
| :--- | :--- | :--- |
| Window grouping | `[windows]` | Group agents into tmux windows such as `main`, `work`, `review`, or `research`. |
| Agent name and provider | `main:codex`, `reviewer:claude` | Names are used by the UI, ask routing, and memory files; provider decides which CLI starts. |
| Workspace isolation | `worker1:codex(worktree)` | Gives implementation agents isolated git worktrees to reduce accidental overlap. |
| Sidebar behavior | `[ui.sidebar]` | Controls whether the sidebar appears in every window, its left/right position, width, and Comms height. |
| Tool windows | `[tool_windows.<name>]` | Add managed non-agent windows such as the rich workbench; they appear as one sidebar row and are not `ask` targets. |
| Per-agent model/API | `[agents.<name>]` | Configure `model`, `key`, `url`, and related agent-local overrides. |
| Role Pack binding | `agentroles.archi:codex` | Bind a reusable role package through a window leaf; role assets are installed once and projected into the derived agent. |
| Role description | `[agents.<name>] description = "..."` | Give an agent a short responsibility note; longer workflow rules belong in memory. |

After editing `.ccb/ccb.config` in a mounted project, run `ccb reload --dry-run` to preview the plan and `ccb reload` to apply it. The explicit reload path can dynamically add agents, add windows, add/remove managed tool windows, unload idle agents, and remove idle windows while keeping unrelated agents and panes running. It does not run as a background file watcher, and unsafe changes such as busy unloads, provider replacement, agent moves, tool command replacement, and arbitrary reshapes are rejected without killing existing panes.

If you want to discuss the configuration before writing it by hand, ask `ccb_self` to describe the target team. Blank projects include this route by default; projects with a user or project config should add `agentroles.ccb_self` if they have overridden the built-in default. Its built-in `ccb-config` skill proposes a complete config first, then writes `.ccb/ccb.config` only after confirmation.

#### Role Packs

Role Packs define reusable agent roles through the
[Agent Roles Spec](https://github.com/SeemSeam/agent-roles-spec). The spec is a
host-neutral package format for specialist agents: a Role carries a stable
identity, responsibilities, non-goals, memory, skills, prompts, references,
tools, plugin content, validation notes, and host adapter metadata as one
reviewable unit.

The practical value is separation. Role source stays portable and versioned;
project bindings decide where that Role is mounted; runtime provider state,
credentials, task progress, and generated projection output stay outside the
Role. That makes specialist agents easier to install, update, audit, migrate,
and remove without copying long prompts into every project or mutating user
global configuration.

The recommended default catalog roles are `agentroles.ccb_self`, the CCB
self-maintenance role, and `agentroles.archi`, an architecture reviewer role
from `agent-roles-spec` backed by Architec. `install.sh install` automatically
attempts to install or refresh these recommended roles by default; `ccb update`
refreshes installed roles and installs missing recommended roles in the user
environment. You can also refresh manually:

```bash
ccb roles list
ccb roles show agentroles.archi
ccb roles install agentroles.archi
ccb roles update agentroles.ccb_self
ccb roles update agentroles.archi
```

Project role bindings stay pinned by `.ccb/role-lock.json`. `ccb update` does
not rewrite project locks. When you run `ccb` inside a project, CCB checks
bound role locks against the current installed roles; interactive starts ask
whether to refresh stale project locks in place, and non-interactive starts
print a warning only.

`ccb_self` is strongly recommended for CCB projects because it is the built-in
CCB expert agent. It carries CCB-specific knowledge about project config,
command usage, role binding, reload boundaries, runtime diagnostics, guarded
recovery, workflow repair, and single-agent restart assistance without taking
over product work. Blank projects include it in the built-in default. Existing
projects, and projects with user or project config that replace the built-in
default, should add it explicitly where they want that maintenance agent:

```bash
ccb roles add agentroles.ccb_self:codex
ccb reload
```

To use `agentroles.archi` in a project, add it as a window leaf:

```bash
ccb roles add agentroles.archi:codex
ccb reload
```

This writes the compact form `agentroles.archi:codex`. At runtime CCB resolves
it to the project-local agent `archi`, then projects the role memory and skills
into that agent's managed provider home.

<details>
<summary><b>Config format examples: single window, multi-window, per-agent model/API</b></summary>

#### Single-window compact config

```text
cmd; main:codex, worker1:codex(worktree); reviewer:claude
```

Meaning:

- `cmd` is a shell pane, not an agent.
- `main`, `worker1`, and `reviewer` are agent names.
- `codex` and `claude` are providers.
- `;` splits left-to-right; `,` stacks top-to-bottom.
- `(worktree)` means that agent uses an isolated git worktree.

#### Multi-window topology

When you want planning, implementation, review, and research in different tmux windows, use `version = 2` and `[windows]`:

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

Note: `cmd` belongs to compact/hybrid single-window layouts. Do not put `cmd` inside `[windows]`.

#### Rich workbench tool window

Tool windows are tmux windows managed by CCB, but they are not agents. They do not appear in `ccb ask` targets and do not create provider runtime records.

```toml
version = 2
entry_window = "main"

[windows]
main = "main:codex"

[tool_windows.rich]
command = "CCB_WORKBENCH_PROFILE=rich CCB_WORKBENCH_FORCE_RICH=1 ccb-workbench files"
label = "rich"
```

`ccb update rich` prepares the optional workbench bundle under CCB-owned XDG paths, downloads and validates bundled binaries where available, and uses the platform package manager only for required rich dependencies such as WezTerm, Markdown/PDF/image/video helpers, and recommended fonts. Under WSL, CCB can launch Windows-native `wezterm.exe` while running the rich tools inside the current Linux distro. Normal `ccb update` keeps this bundle untouched; rerun `ccb update rich` to install, repair, or refresh it. Run `ccb uninstall rich` to remove the bundle and return plain `ccb` to normal terminal startup. Set `CCB_RICH_DOWNLOAD_BINARIES=0` to skip bundled binary downloads, or `CCB_RICH_INSTALL_DEPS=0` to skip system package installation.

#### Per-agent model, API key, or base URL

Use compact format when layout is enough. If some agents need separate models or API routes, keep the compact header and add TOML overlays:

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

Do not commit real API keys to a public repository. `key` / `url` are agent-local shortcuts; advanced provider environment variables belong in provider profile or agent env fields.

</details>

### Use ccb_self For CCB Config

The full `ccb-config` skill belongs to the `agentroles.ccb_self` role. It is not a globally inherited skill for every agent. CCB installs or refreshes this Role Pack by default, and blank projects include `ccb_self` in the built-in default config. Existing projects, or projects with a user/project config that replaces the built-in default, should bind it where they want the maintenance assistant.

`ccb_self` is more than a config helper: it is designed as CCB's self-understanding agent. Use it when you need help using CCB, explaining the active layout, choosing an agent topology, migrating `.ccb/ccb.config`, diagnosing project runtime state, or repairing a CCB workflow.

If you do not want to hand-write `.ccb/ccb.config`, ask `ccb_self` and describe your project goal, parallelism, window grouping, worktree isolation, provider/model/API preferences. `ccb_self` uses its built-in `ccb-config` skill to discuss the shape with you and propose a complete config.

Example:

```bash
ccb ask ccb_self "Design a team for a Python library: main coordinates work, three workers implement in worktrees, and one reviewer checks regressions and risks. Recommend whether this should stay single-window or become main/work/review windows."
```

For an existing project that does not already configure `ccb_self`, run
`ccb roles add agentroles.ccb_self:codex` and `ccb reload` first.

<details>
<summary><b>ccb-config write flow and boundaries</b></summary>

1. Describe the project and team goal in natural language.
2. `ccb_self`'s built-in `ccb-config` reads the current config authority and decides whether this is a new config, an edit, or a migration.
3. It proposes one complete config before writing.
4. You confirm the proposal, then it edits only `.ccb/ccb.config`.
5. It validates the config and tells you to use `ccb reload --dry-run` / `ccb reload` when the change can be applied dynamically.

By default, `ccb-config` does not edit `.ccb/ccb_memory.md` or `.ccb/agents/<agent>/memory.md`. It should touch those memory files only when you explicitly ask `ccb_self` for workflow memory or role memory design.

</details>

</details>

<details>
<summary><b>Collaboration and maintenance</b></summary>

### Agent Collaboration

Normal `ask` is submit-and-return: after handing work to the target agent, the current agent should not poll and wait.

| Scenario | Recommended route |
| :--- | :--- |
| Human directly targets an agent | `/ask reviewer ...` or `$ask reviewer ...` |
| Current agent is inside an active CCB task and needs a child result before replying | `ask --callback reviewer` |
| Current agent sends independent work whose successful result does not need to return | `ask --silence worker1` |
| Queue or status diagnostics | `pend`, `watch`, `ping`, and similar commands are diagnostics only |

When an agent submits a child task, choose flags from the result intent first,
then add dependency and artifact preservation only when needed:

| Need | Recommended flags |
| :--- | :--- |
| Publish or execute work; successful result is not useful | `--silence` |
| Get a short outcome: status, findings, risks, blockers, or next steps | `--compact` |
| Get full consultation, analysis, report, generated doc, or structured findings | `--artifact-reply` |
| Continue an active parent task only after the child result arrives | add `--callback` |
| Preserve exact pasted logs, diff, JSON/YAML, table, or copied text | add `--artifact-request` |
| Preserve exact input and full output | `--artifact-io` |
| Short question or short handoff where inline text is enough | plain `ask` |

`--callback` and `--silence` control task relationship. Artifact flags control
content preservation. The automatic long-message spill is only a fallback, so
use artifact flags proactively when exact input or full output matters.

<details>
<summary><b>Why callback matters</b></summary>

If agent A is handling a user-originated CCB task and needs agent B's result to finish, A should use callback. CCB records the parent/child relationship, lets A's current turn end, and later delivers B's result back to A as a continuation. That avoids polling, queue blocking, and wasted context.

</details>

### Install And Update

#### Requirements

- Node.js and npm for the recommended npm install path
- Python 3.10+
- `tmux`
- At least one agent CLI you plan to use, such as Codex, Claude, Gemini, Kimi, MiMo, Qwen, Cursor, Copilot, Crush, Kiro, Z.ai CLI, OpenCode, Droid, or Antigravity
- Linux, macOS, or WSL

Current v7 / newer versions do not claim native Windows support. Native Windows support only applies to the v5 line. If you are on Windows and want current versions, use WSL and keep both `ccb` and agent CLIs inside WSL.

#### npm first

For first install, prefer npm:

```bash
npm install -g @seemseam/ccb
```

For later updates:

```bash
ccb update
```

[GitHub Releases](https://github.com/bfly123/claude_code_bridge/releases) remain available for environments where npm is unavailable. Source checkout install is for development, fix validation, or temporary fallback.

#### Uninstall

```bash
ccb uninstall
ccb reinstall

# Fallback from the package or source directory:
./install.sh uninstall
```

### FAQ

<details>
<summary><b>The expected agents did not appear</b></summary>

Run `ccb config validate` and check that `config_source_kind` is the layer you expected. Project config `.ccb/ccb.config` has highest priority; if it is missing, CCB uses `~/.ccb/ccb.config` or the built-in default.

</details>

<details>
<summary><b>Copy/paste is awkward</b></summary>

First try mouse-drag copy and `Ctrl+Shift+V` / `Cmd+V` paste. If tmux captures the drag, use function key `[` after `<prefix>` to enter copy / scroll mode. If you only want native terminal selection, many terminals support `Shift + mouse drag`.

</details>

<details>
<summary><b>I want to migrate an old compact config to multi-window</b></summary>

Ask `ccb_self` to use its built-in `ccb-config` and describe your target window groups, such as main/work/review. Migration should preserve old agent names, providers, worktree markers, model/key/url fields, and write `[windows]` only after confirmation.

</details>

<details>
<summary><b>The sidebar helper is unavailable</b></summary>

Prefer a release package because it carries or handles the sidebar helper. Source installs may need a local Rust toolchain if no compatible prebuilt helper is available.

</details>

### Community And Credits

Thanks to the [Linux.do community](https://linux.do) for testing, feedback, and discussion.

Thanks to [tmux-agent-sidebar](https://github.com/hiroppy/tmux-agent-sidebar) for the sidebar ideas and inspiration.

</details>

<details>
<summary><b>Release notes</b></summary>

### Release Notes

v7 highlights:

- Native CCB sidebar with per-window project view, agent status, and mouse switching.
- Comms split from agent activity, making communication status and provider pane activity clearer.
- `version = 2` `[windows]` topology for workflow-oriented tmux window grouping.
- Explicit `ccb reload` support for dynamic agent/window load and idle unload without restarting unrelated agents.
- Compact / hybrid config compatibility, so single-window teams do not need forced migration.
- Hardened tmux, Ghostty, release helper, Codex trust, and provider session restore paths.

<details open>
<summary><b>v8.0.7</b> - CCB Mobile Notifications And Theme Stabilization</summary>

- Adds server-wide task-completion notification SSE plus Android local notifications with low-sensitive payloads and verified tap routing back to the target project/agent.
- Carries provider runtime status detection for mobile activity indicators and sidebar state.
- Updates the mobile app with steel-blue/slate theming, improved readable content styles, height-limited expanded bubbles, and notification group tap handling.
- Synchronizes VERSION, package metadata, mobile app version metadata, README links, workflow defaults, and APK download URLs for 8.0.7.

</details>

<details>
<summary><b>Unreleased</b> - Empty-Reply Diagnostics Split</summary>

- Splits the generic `incomplete/task_complete_empty_reply` completion reason
  into `model_empty_output`, `delivery_late_empty`, and `api_empty_after_error`
  so senders can see why the recipient produced no reply.
- Adds `empty_reply_reason` diagnostics to the empty-reply decision payload.
- Surfaces `api_error_seen` in Codex `TURN_BOUNDARY` payloads when an `api_error`
  event is observed during the turn.

</details>

<details>
<summary><b>v8.0.6</b> - CCB Mobile Real Project Chat Stabilization</summary>

- Improves Android CCB Mobile real-project pane-native chat, status recovery,
  terminal stream handling, QR/manual pairing, input ergonomics, localization,
  and noisy timeline filtering.
- Includes the latest server-wide project listing, native provider transcript,
  provider runtime status, and mobile terminal pane input fixes from the CCB
  source line.
- Uses a compact terminal pairing QR in `ccb update mobile`, keeps the original
  CCB Mobile bridge icon identity, and makes camera-unavailable scanner fallback
  actionable instead of showing native plugin errors.
- Synchronizes VERSION, package metadata, mobile app version metadata, README
  links, workflow defaults, and APK download URLs for 8.0.6.

</details>

<details>
<summary><b>v8.0.4</b> - CCB Mobile Project List Stability</summary>

- Accelerates `/v1/projects` for server-wide mobile gateways by checking
  mounted project health concurrently while preserving registry order.
- Reduces noisy BrokenPipe/connection-reset tracebacks when a phone disconnects
  or times out while a response is being written.

</details>

<details>
<summary><b>v8.0.3</b> - npm Release Metadata Fix</summary>

- Fixes npm provenance metadata so `@seemseam/ccb` publishes from the canonical
  GitHub repository used by GitHub Actions.
- Synchronizes VERSION, package metadata, mobile app version metadata, README
  links, workflow defaults, and APK download URLs for 8.0.3.

</details>

<details>
<summary><b>v8.0.2</b> - Mobile Tailnet Onboarding Fixes</summary>

- Handles the Tailscale Serve one-time approval flow without exposing raw
  timeout errors.
- Reuses an existing correct Tailscale Serve proxy for `:8787`, so users who
  already approved or started Serve can immediately get the pairing QR.
- Fixes source worktree installs so installed `ccb` is not mistaken for a source
  checkout.

</details>

<details>
<summary><b>v8.0.1</b> - Simplified CCB Mobile Pairing</summary>

- Makes `ccb update mobile` the single user-facing setup command: it checks
  Tailscale, guides login/install, starts the server-wide loopback gateway plus
  Tailscale Serve, and prints a pairing QR directly in the terminal.
- Updates CCB Mobile first launch to show pairing instructions, Tailscale
  download guidance, and a scan button instead of opening the fake/demo project.
- Automatically opens the server-wide mounted project list after a stored
  pairing profile is found, reducing setup friction for normal phone use.

</details>

<details>
<summary><b>v8.0.0</b> - CCB Mobile Monorepo Release</summary>

- Ships the Flutter CCB Mobile source inside this repository and publishes the
  Android APK as a GitHub Release asset.
- Adds server-wide mobile project discovery, pairing, authenticated gateway
  routes, pane-native message input, transcript rendering, terminal access, and
  image/document upload and download support.
- Promotes `ccb update mobile` as the guided setup entry for Tailscale Tailnet
  onboarding while preserving loopback-only gateway binding and avoiding Funnel,
  token storage, or ACL/grant automation.

</details>

<details>
<summary><b>v7.7.0</b> - Runtime Accelerator Release Hardening</summary>

- Ships the optional Rust `ccb-runtime-accelerator` in release artifacts, so
  installed Codex agents can use the sidecar hotpath instead of falling back to
  Python when the binary is expected.
- Adds short runtime socket fallback for long project paths that would exceed
  Unix socket path limits.
- Hardens callback repair and Codex binding cache invalidation, then records
  full regression, long-idle Codex soak, Claude callback, and mixed-provider
  integration evidence.

</details>

<details>
<summary><b>v7.6.19</b> - Long-Running Ask Wait Policy</summary>

- Keeps ordinary long-running `ask` jobs waiting by default instead of
  terminalizing them as `incomplete/heartbeat_timeout` from heartbeat
  diagnostics alone.
- Makes Codex, Claude, and Gemini pane-backed no-terminal timeouts opt-in by
  default while preserving explicit reliability timeout policies.
- Validated with a 32-minute source-runtime ask smoke: the job stayed running
  past 30 minutes, then completed with `result_message` and no
  `heartbeat_timeout`/`incomplete` evidence.

</details>

<details>
<summary><b>v7.6.18</b> - CCB UI Theme Preference</summary>

- Adds `ccb theme` as the top-level theme switch for CCB-owned tmux/sidebar UI,
  with `+` and `-` cycling across dark and light palettes.
- Adds readable light-mode tmux status, pane border, sidebar, activity, and
  comms status colors for light terminal backgrounds.
- Makes generated rich WezTerm profiles follow the same global CCB theme
  preference on the next launch/reload.

</details>

<details>
<summary><b>v7.6.17</b> - Codex Log Symlink Target Repair</summary>

- Repairs managed Codex `logs_2.sqlite` temp symlink targets when
  `/tmp/ccb-codex-logs-*` cleanup removes the target directory between starts.
- Falls back by removing an unrecoverable broken symlink and restoring the local
  backup before Codex initializes its SQLite databases.
- Adds regression coverage for the missing symlink target parent startup path.

</details>

<details>
<summary><b>v7.6.16</b> - Codex SQLite Migration Recovery</summary>

- Fixes the managed Codex `logs_2.sqlite` redirect so CCB no longer
  pre-creates Codex-owned SQLite schema; Codex runs its own migrations first.
- Installs the CCB diagnostic insert-block trigger only after Codex has created
  the log database and `_sqlx_migrations` records.
- Repairs bad temporary log databases left by the intermediate policy by moving
  them aside and letting Codex recreate them through the normal migration path.

</details>

<details>
<summary><b>v7.6.15</b> - Codex Diagnostics And Sidebar Focus</summary>

- Redirects managed Codex `logs_2.sqlite` diagnostic writes to temporary
  storage by default and blocks diagnostic log inserts, while diagnostics mode
  can restore the original database path for troubleshooting.
- Falls back to the in-place diagnostic trigger path when the temporary SQLite
  symlink cannot be installed.
- Fixes sidebar clicks for agents in other tmux windows by selecting the target
  window before selecting the pane, with pane-id fallback when window metadata
  is missing.

</details>

<details>
<summary><b>v7.6.14</b> - Mobile Gateway Alpha And Codex Diagnostics</summary>

- Adds the mobile gateway alpha surface: authenticated pairing, focus routes,
  terminal open/resume/history routes, websocket terminal frames, public route
  metadata, and device revocation commands.
- Adds right-side sidebar placement and flattened `[ui.sidebar]` rendering while
  keeping legacy `[ui.sidebar.view]` input compatible.
- Supports multiple local agents sharing one Role Pack role id without
  collapsing them into a single runtime identity.
- Reduces Codex diagnostic SQLite churn by filtering TRACE/DEBUG log rows by
  default while preserving INFO/ERROR rows; `CCB_CODEX_DIAGNOSTIC_LOGS=1`
  disables the filter.

</details>

<details>
<summary><b>v7.6.13</b> - Provider Profile Overlay Fixes</summary>

- Codex plugin overrides now resolve in the intended order: inherited source
  config, `provider_profile.plugins`, then `CCB_CODEX_PLUGIN_OVERRIDES_JSON` /
  `CCB_CODEX_PLUGIN_OVERRIDES`.
- Codex agents without an inherited `config.toml` now still materialize
  `provider_profile.plugins` into managed `config.toml`.
- Claude `provider_profile.mcp_servers` now works even when the source
  `.claude.json` does not exist, and `enabled = false` clears stale managed MCP
  servers from the agent trust file.
- Callback continuations now preserve the upstream finalization target, and
  inherited ask skills remind agents not to answer callback continuations before
  upstream results are available.

</details>

<details>
<summary><b>v7.6.12</b> - Claude MCP And Hook Inheritance</summary>

- Managed Claude agents now inherit Claude Code MCP configuration from the
  source `.claude.json`, including global `mcpServers` and current
  project/workspace MCP server state.
- Project-level MCP state is mapped only onto the current managed workspace key,
  so unrelated source project records are not copied into the agent home.
- Source-home Claude Code hooks are merged with CCB-managed finish/activity
  hooks, so installed user hook tools stay visible after agent restart.
- Managed Claude `.claude.json` is treated as secret provider state because MCP
  definitions may include environment variables or auth-adjacent launch data.

</details>

<details>
<summary><b>v7.6.11</b> - Layout Percent And Codex MCP Overlays</summary>

- Adds explicit pane split ratios in layout tokens, such as
  `agent1:codex@30`, while preserving the existing even-split default when no
  suffix is present.
- Adds source-controlled per-agent Codex MCP overlays through
  `provider_profile.mcp_servers`; same-name MCP servers override inherited
  Codex config and different names are additive.
- Preserves trusted Codex command hooks during managed Codex home projection,
  improves sidebar Comms/Tips scrolling and resizing, and exposes more reply
  artifact evidence in `ccb trace`.

</details>

<details>
<summary><b>v7.6.10</b> - Z.ai Provider Support</summary>

- Adds managed optional Z.ai CLI provider support with `provider = "zai"`,
  visible `zai --directory` panes, and per-job `zai --prompt` execution.
- Uses Z.ai's native subprocess completion boundary: process exit plus
  assistant stdout extraction from JSONL output, without model-printed
  `CCB_DONE`.
- Adds `ZAI_START_CMD`, provider session/pathing support, deterministic stubs,
  focused execution tests, and README/provider list updates.

</details>

<details>
<summary><b>v7.6.9</b> - Kimi And AGY Provider Reliability</summary>

- Kimi execution now records receipt, no-captured-output diagnostics, trace,
  and resume metadata so missing replies and recovered turns are easier to
  diagnose.
- AGY prompt delivery now waits for ready evidence, handles pane fallback and
  ambiguous tmux send outcomes, and reports coalesced request diagnostics more
  clearly.
- Dispatcher, mailbox trace, and text artifact diagnostics now expose the
  provider details needed to investigate Kimi/AGY delivery and completion edge
  cases.

</details>

<details>
<summary><b>v7.6.8</b> - Role Pack Current Store</summary>

- Role Pack runtime lookup now follows the installed current package under
  `.roles/installed/<role-id>/current`; legacy multi-version stores remain
  compatibility input only.
- Project `.ccb/role-lock.json` files are now legacy diagnostics: CCB no
  longer writes them, adopts from them, or suppresses role memory and skills
  because of stale lock residue.
- Provider launch sessions record role id, version, and digest; restart now
  fails explicitly when the launch digest differs from installed current instead
  of silently resuming an old provider conversation.
- Release artifact metadata patching now targets `ccb.py` after the bash
  launcher split, keeping built tarballs on the intended version.

</details>

<details>
<summary><b>v7.6.7</b> - Rich Workbench Closure</summary>

- Plain `ccb` and `ccb rich` now launch the CCB-managed rich WezTerm unless
  already inside that managed rich session; ordinary external WezTerm sessions
  no longer suppress rich auto-start.
- Runtime entrypoints share the `_ccb-python` launcher, keeping installed and
  source command execution pinned to the intended Python interpreter.
- Built-in defaults keep `ccb_self` in its own `claude` window, while ordinary
  default startup still avoids standalone Neovim tool windows.

</details>

<details>
<summary><b>v7.6.6</b> - Role Store Home Pinning</summary>

- Pins role store lookup outside managed provider homes so provider session
  `HOME` rewrites no longer make role checks search provider-local `.roles`
  directories.
- Preserves `AGENT_ROLES_STORE` through CCB launch boundaries and falls back to
  the real source/account home role store when no explicit store is set.
- Missing role diagnostics now print the resolved role store path, making
  provider-home drift easier to identify.

</details>

<details>
<summary><b>v7.6.5</b> - Rich WezTerm IME</summary>

- Enables IME support in the generated rich WezTerm config and maps
  `XMODIFIERS=@im=...` into WezTerm's XIM name so X11 fcitx/ibus input works
  for Chinese and other IME-backed text.
- Generated `ccb-workbench` wrappers now detect running or installed
  `fcitx5`, `fcitx`, or `ibus-daemon` before launching WezTerm, while
  preserving any user-provided input-method environment.
- Keeps the v7.6.4 green release surface and all v7.6.2 rich/tmux fixes intact
  for npm latest install testing.

</details>

<details>
<summary><b>v7.6.4</b> - macOS Release Install Smoke</summary>

- Keeps the 7.6.3 macOS temporary-root hardening and updates the CI release
  install smoke to use the explicit temporary-bin override for its isolated
  sibling `CODEX_BIN_DIR`.
- Leaves user-facing installer safety intact while allowing the release
  workflow to validate macOS package installation from a temporary smoke root.
- Keeps the v7.6.2 rich workbench and tmux single-status-row fixes intact for
  user install testing.

</details>

<details>
<summary><b>v7.6.3</b> - macOS CI Green Patch</summary>

- Fixes macOS temporary-root detection for install guards by recognizing the
  resolved `${TMPDIR:-/tmp}` parent used by GitHub Actions runners.
- Aligns doctor temporary implementation detection with macOS `/tmp` symlink
  behavior, preventing false red CI on `/private/tmp` and
  `/private/var/folders/...` paths.
- Keeps the v7.6.2 rich workbench and tmux single-status-row fixes intact for
  user install testing.

</details>

<details>
<summary><b>v7.6.2</b> - Rich Workbench Hotfix</summary>

- Allows `rich` in `.ccb/ccb.config` as a tool/layout alias without requiring
  a provider runtime; it materializes as a managed tool pane/window and is not
  an `ask` target.
- After `ccb update rich` enables the bundle, plain `ccb` can use the rich
  launcher outside an existing rich/WezTerm session while avoiding recursive
  WezTerm launches.
- Adds `ccb uninstall rich`, `ccb rich uninstall`, and `ccb rich disable` for
  returning to normal CCB startup without changing full `ccb uninstall`
  behavior.
- Rich updates clean only CCB-owned legacy editor roots and links, leaving
  user-owned editor installs and personal config untouched.

</details>

<details>
<summary><b>v7.6.1</b> - Rich Workbench Binary Packaging</summary>

- `ccb update rich` now bundles verified Yazi/ya binaries where possible before
  falling back to package managers.
- Linux rich installs prefer official Yazi musl builds before GNU builds to
  avoid newer glibc requirements on older stable distributions.
- Downloaded Yazi binaries must pass `--version` validation before activation,
  and invalid managed binaries are removed so fallback paths remain available.
- Under WSL, rich launchers can use Windows-native `wezterm.exe` while keeping
  CCB, Yazi, and preview helpers inside the current Linux distro.

</details>

<details>
<summary><b>v7.6.0</b> - Rich Workbench Lifecycle</summary>

- Makes rich workbench an explicit optional bundle installed with
  `ccb update rich`.
- Keeps ordinary `install.sh install` and `ccb update` focused on CCB itself;
  they no longer auto-provision standalone Neovim.
- Public `ccb tools ... neovim` routes now refuse standalone provisioning and
  point users to `ccb update rich`; `ccb rich` launches only an installed and
  enabled rich bundle.
- Restores the CCB tmux status bar to one line by removing the old second-line
  copy hint.

</details>

<details>
<summary><b>v7.5.3</b> - Kimi Runtime Reliability And Hindsight Compatibility</summary>

- Adds Kimi runtime hardening without changing other provider execution paths:
  Kimi can fall back to stable pane evidence for K2.7 Code when the native turn
  log does not expose a completed reply in time.
- Makes Kimi Hindsight memory opt-in at the CCB execution boundary. It activates
  only when `.hindsight/kimi.json`, `.hindsight/codex.json`,
  `HINDSIGHT_API_URL`, or `HINDSIGHT_BANK_ID` is configured, and failures remain
  non-blocking provider diagnostics.
- Preserves trusted Codex command hooks, including Hindsight Codex hooks, when
  CCB materializes managed Codex homes. Operators can extend the allowlist with
  `CCB_CODEX_INHERITED_HOOK_EVENTS` and
  `CCB_CODEX_INHERITED_COMMAND_HOOK_MARKERS`; arbitrary root hooks remain
  filtered out.
- Accepts both `HINDSIGHT_API_KEY` and `HINDSIGHT_API_TOKEN` for the Kimi
  bridge and the `scripts/hindsight` helper.
- Documents the supported provider surface more clearly in the README while
  keeping unrelated provider behavior unchanged.

</details>

<details>
<summary><b>v7.5.2</b> - Native CLI Provider Wave</summary>

- Adds built-in optional provider ids for Qwen Code (`qwen`), Cursor Agent
  (`cursor`), GitHub Copilot CLI (`copilot`), Crush (`crush`), Kiro CLI
  (`kiro`), Pi (`pi`), and Z.ai CLI (`zai`).
- Uses native per-job CLI execution and provider-owned completion signals:
  stream-json / JSON result events for Qwen, Cursor, Copilot, and Pi; process
  exit plus stdout for Crush, Kiro, and Z.ai CLI. These adapters do not require
  model-printed `CCB_DONE`; Pi terminalizes on native `turn_end`.
- Adds `QWEN_START_CMD`, `CURSOR_START_CMD`, `COPILOT_START_CMD`,
  `CRUSH_START_CMD`, `KIRO_START_CMD`, `PI_START_CMD`, and `ZAI_START_CMD`
  command overrides plus provider session bindings, runtime launchers,
  deterministic stubs, and focused
  execution tests.

</details>

<details>
<summary><b>v7.5.1</b> - MiMo Provider Release Surface</summary>

- Adds MiMo Code to the public README provider strip with a Xiaomi-branded
  MiMo badge and updates the top-level positioning to eight CLI families.
- Publishes the committed MiMo native provider integration in the 7.5 line:
  managed `mimo` panes, `MIMO_START_CMD`, generated MiMo instructions, and
  `mimo run --pure --format json` completion parsing.
- Synchronizes npm package metadata and release workflow defaults with the
  new patch release.

</details>

<details>
<summary><b>v7.5.0</b> - Native CLI Providers And Homepage Sync</summary>

- Adds managed native CLI provider support for Kimi plus broader native CLI
  runtime groundwork, including runtime specs, session bindings, command
  overrides, and cleanup coverage.
- Moves Kimi and Antigravity completion detection toward provider-owned
  session or transcript evidence instead of requiring model-printed `CCB_DONE`.
- Uses Kimi's current `--auto-approve` flag for CCB auto-permission while
  recognizing legacy/alias flags such as `--auto`, `--yes`, `-y`, and `--yolo`.
- Synchronizes the English and Chinese README homepages with refreshed hero
  assets and the seven public CLI-family positioning.

</details>

<details>
<summary><b>v7.4.4</b> - Claude End-Turn And npm Release Surface</summary>

- Completes Claude pane-backed asks promptly when a primary assistant response
  emits `stop_reason=end_turn` with an observed request anchor and non-empty
  reply, avoiding the previous 900-second timeout path.
- Treats empty session-boundary terminal events with no prior assistant reply
  as `incomplete/task_complete_empty_reply` with empty-provider diagnostics.
- Restores the `@seemseam/ccb` npm release surface with package metadata, CLI
  runner wrappers, and tag-triggered Trusted Publishing after GitHub release
  assets are available.
- Refreshes the v7 README homepage around canonical hero assets, npm-first
  install, and clearer `ccb_self` guidance.

</details>

<details>
<summary><b>v7.4.3</b> - PR #225 Reliability Follow-Up</summary>

- Restores the Claude launcher contract: inline `--settings` now reflects the
  materialized settings overlay without injecting provider env into settings
  JSON.
- Fixes Claude WSL Windows-executable environment forwarding so path variables
  use `/p` translation while `ANTHROPIC_*` API values pass through as raw env
  names.
- Hardens Antigravity resume lookup for SQLite `bytes`, `str`, and
  `memoryview` metadata and falls back to `--continue` if lookup fails.
- Adds regression tests for the Claude settings contract, WSL API env
  forwarding, and AGY resume fallback behavior.

</details>

<details>
<summary><b>v7.4.2</b> - Self-Supervision And Empty Reply Guards</summary>

- Hardens CCB self-supervision with bounded provider-runtime snapshots,
  project-view activity evidence, suspicion envelopes, and a self-first
  diagnosis path.
- Treats empty Claude/Gemini hook replies, Codex protocol `task_complete`
  empty replies, and AGY done-marker empty replies as `incomplete` with
  diagnostics.
- Preserves intentional no-reply behavior: `--silence` success remains
  completed, callback parent `callback_pending` remains legal, and abnormal
  silent completions stay diagnosable.
- Tightens default Role Pack install and project role-lock refresh handling for
  `agentroles.archi` and `agentroles.ccb_self`.

</details>

<details>
<summary><b>v7.4.1</b> - Maintenance Heartbeat And ccb_self Defaults</summary>

- Hardens the project-scoped maintenance heartbeat runner, schedule handling,
  activation suppression, and diagnostics evidence paths while keeping
  heartbeat opt-in.
- Adds `ccb_self:codex` bound to canonical `agentroles.ccb_self` in the
  built-in blank-project default and refreshes the recommended role during
  install/update provisioning without rewriting existing custom configs.
- Aligns CCB source with the `agent-roles-spec` role id
  `agentroles.ccb_self`; `agentrole.ccb_self` is accepted only as legacy input
  compatibility.
- Tightens generated config authority, Role Pack hook paths, and Codex prompt
  delivery acceptance guards.
- Adds the `ccb_self` expert manual, plan decisions, and tests for expert
  reference and communication recovery guidance.

</details>

<details>
<summary><b>v7.4.0</b> - ccb_self Maintenance Role</summary>

- Adds the `agentroles.ccb_self` self-maintenance Role Pack path for CCB config
  ownership, diagnostics, guarded recovery, chain repair, and single-agent
  restart assistance.
- Moves full `ccb-config` into the private `ccb_self` Role skill instead of a
  globally inherited skill.
- Installs or refreshes recommended default Role Packs, including
  `agentroles.ccb_self`, during install/update Role Pack provisioning.
- Adds `ccb_self:codex` bound to `agentroles.ccb_self` in the built-in blank
  project default; existing custom configs can still add
  `agentroles.ccb_self:codex` explicitly.

</details>

<details>
<summary><b>v7.3.8</b> - AGY Adapter And Project Tmux History</summary>

- Adds the Antigravity (`agy`) `pane_quiet` execution adapter with protocol parsing, command dispatch, polling, and docs for managed provider operation.
- Preserves 50000 lines of scrollback history for CCB-managed project tmux sessions, including project namespace create/reuse and detached runtime fallback paths.
- Keeps tmux mouse, vi key, clipboard, focus, and history policies consistently reapplied after the authoritative project session exists.
- Hardens Claude startup by passing inline `--settings` JSON when possible, preserving non-ASCII source paths through provider launch.

</details>

<details>
<summary><b>v7.3.7</b> - Ask Parameter Policy And Skill Guidance</summary>

- Updates inherited Claude, Codex, and Droid ask skills to choose flags from result intent first: `--silence`, `--compact`, `--artifact-reply`, or plain `ask`.
- Keeps dependency handling explicit by adding `--callback` only when an active parent job must wait for a child result.
- Separates artifact transport from task relationship: use `--artifact-request` and `--artifact-io` when exact input or input/output preservation matters.
- Adds the Agent Collaboration ask-parameter quick reference to README and README_zh.
- Adds the ask-parameter-policy plan tree, decision records, parameter matrix, and validation notes.

</details>

<details>
<summary><b>v7.3.6</b> - Provider Memory Ownership Cleanup</summary>

- Adds provider memory ownership policy: Claude, Codex, and OpenCode managed contexts no longer duplicate provider-native project memory inside the CCB generated bundle; Gemini keeps the previous behavior pending audit.
- Filters legacy CCB install marker blocks and old collaboration sections only from provider user memory, without rewriting user-owned memory files.
- Updates the default `.ccb/ccb_memory.md` template to v5 and removes the duplicate Ask Communication block already supplied by managed CCB memory.
- Adds seed-aware shared memory migration, upgrading only unedited old generated templates while preserving edited project memory.
- Stops Claude route-mode installs from writing `~/.claude/rules/ccb-config.md`; install/uninstall now remove only CCB-marked legacy external config and preserve unmarked user files.
- Keeps source runtime startup import-safe by avoiding the tmux UI version detection cycle under `ccb_test`.

</details>

<details>
<summary><b>v7.3.5</b> - Tmux Border Hook Hotfix</summary>

- Fixes tmux `after-select-pane` hooks that could persist temporary release paths like `/tmp/ccb-v...-release.../config/ccb-border.sh` and later report `returned 127` when clicking panes.
- Makes border hooks use `run-shell -b` with an executable guard, so stale script paths do not spam tmux errors.
- Refreshes active tmux UI hooks after `ccb update` on a best-effort basis, so users upgrading from v7.3.4 automatically rewrite bad hooks without failing Role Pack provisioning.
- v7.3.4 is withdrawn/prerelease; use v7.3.5 or newer as the stable upgrade target.

</details>

<details>
<summary><b>v7.3.4</b> - Withdrawn Prerelease</summary>

- Simplifies `agentroles.archi` tooling around the global `@seemseam/archi` npm package; CCB no longer manages separate Hippo, llmgateway, pip, venv, git, or editable Archi dependencies.
- Aligns `ccb roles install/update/doctor agentroles.archi` with the npm-provided `archi` CLI and bundled Hippo/llmgateway capabilities.
- Updates `bin/ccb-arch` to forward to `archi`, with a clear `npm install -g @seemseam/archi` hint when the CLI is missing.
- Fixes sidebar focus/refresh handling so selecting agents from the sidebar no longer restarts panes unnecessarily.
- Withdrawn because tmux border hooks could persist temporary release paths and later report `ccb-border.sh ... returned 127`; use v7.3.5 or newer.
- Adds the guarded `ccb_test` source entrypoint for isolated source-checkout validation without affecting installed CCB.
- Disables OpenCode autoupdate for managed panes through `opencode.json` and `OPENCODE_DISABLE_AUTOUPDATE=true`.
- Refreshes inherited `ccb-config` skills for config-only use, language-following behavior, YAML description quoting, clearer menu grouping, and sidebar pane restart guidance.
- Adds the config-designer UI plan tree and includes the main-branch `@percent` layout token plus Antigravity lifecycle cleanup updates.

</details>

<details>
<summary><b>v7.3.3</b> - Withdrawn Draft</summary>

- Withdrawn before stable rollout because it carried a sidebar focus/refresh regression. It is not the recommended release and should not be used for upgrades; use v7.3.5 or newer.

</details>

<details>
<summary><b>v7.3.2</b> - First-Install Role Pack Provisioning Hotfix</summary>

- Fixes a blank-environment first install bug where `install.sh` tried to update `agentroles.archi` before it was installed, leaving Role Pack provisioning incomplete.
- Keeps the existing install refresh path: `ccb roles update agentroles.archi` is still attempted first, then falls back to `ccb roles install agentroles.archi` when the role is missing.
- Aligns optional Role Pack skip messaging with the install path.
- Supersedes v7.3.1 as the recommended stable release for new installs; v7.3.1 is published but has the blank first-install Role Pack provisioning bug.

</details>

<details>
<summary><b>v7.3.1</b> - Agent Roles, Artifact Ask, And Shared Workspace Release</summary>

- Adds daemon-managed ask artifact transport with `--artifact-request`, `--artifact-reply`, and `--artifact-io`, including callback-compatible artifact replies for long outputs.
- Finalizes the Agent Roles store path around the external `agent-roles` manager and `.roles/installed`, while preserving `ccb.archi` compatibility for `agentroles.archi`.
- Adds shared workspace controls with `workspace_path` and `workspace_group`, plus `provider_command_template` for wrapping the CCB-built provider command without breaking resume handling.
- Fixes Claude startup under root, OpenCode `ccb clear` submit timing after restored sessions, and managed Neovim activation so the original runtime path is preserved.
- Refreshes inherited `ask` and `ccb-config` skills for submit-only ask rules, artifact modes, windows-first config, shared workspaces, and provider command templates.
- Stabilizes WSL/root release tests by making non-root Claude command assertions independent from the runner UID.

</details>

<details>
<summary><b>v7.3.0</b> - Superseded Prerelease</summary>

- Superseded by v7.3.1 after the remote WSL Tests workflow exposed root-sensitive Claude command assertions. The v7.3.0 GitHub release was kept as a prerelease and did not upload official release artifacts.

</details>

<details>
<summary><b>v7.2.12</b> - Agent Roles Store Migration Release</summary>

- Uses the external `agent-roles` package manager by default for Role Pack install, update, and sync.
- Writes Role Pack payloads into the spec-owned `.roles/installed` store.
- Copies existing legacy installed role snapshots into `.roles/installed` without deleting the old store; runtime lookup reads the spec-owned store only after migration.
- Routes `ccb roles update --path ...` through the Agent Roles manager so path updates also write `.roles/installed`.
- Supersedes v7.2.11, which was an incomplete opt-in preview release and should not be used as the recommended release.

</details>

<details>
<summary><b>v7.2.11</b> - Superseded Agent Roles Opt-In Preview</summary>

- Superseded by v7.2.12 after the release direction changed from an opt-in `CCB_AGENT_ROLES_MANAGER=1` preview to a default-on Agent Roles manager migration.

</details>

<details>
<summary><b>v7.2.10</b> - Role Pack Post-Update Hotfix</summary>

- Fixes managed `ccb update` so optional Role Pack and Neovim provisioning runs through the newly installed `ccb __post-update` entrypoint instead of the old updater process.
- Repairs legacy installed `ccb.archi` role metadata under canonical `agentroles.archi` and falls back to the current catalog source when old source paths are gone.
- Preserves optional post-update provisioning as warnings, while `CCB_INSTALL_ROLES=1`, `CCB_INSTALL_NEOVIM=1`, or `CCB_POST_UPDATE_REQUIRED=1` still fail the parent update when required provisioning fails.
- Keeps new config guidance on `agentroles.archi`; `ccb.archi` remains a legacy input alias only.

</details>

<details>
<summary><b>v7.2.9</b> - Agent Roles Catalog Release</summary>

- Moves the production architecture role out of the CCB source tree and consumes `agentroles.archi` from `agent-roles-spec`.
- Adds catalog-backed role list/install/update/sync/add/doctor behavior with installed-role metadata, project locks, digest pinning, and explicit re-add updates.
- Projects role memory, CCB adapter memory, provider skills, and Architec adapter hooks into managed provider homes.
- Keeps `ccb.archi` as a compatibility alias while writing canonical `agentroles.archi` bindings and locks.
- Fixes the source runtime guard so `ccb --project <allowed-test-dir> ...` smoke commands launched from the source checkout pass the release gate.
- Passes generated soak, fastpath, and storage cleanup smoke roots through `CCB_SOURCE_ALLOWED_ROOTS`.
- Passes the WSL mounted startup smoke project under `/mnt/c/Temp` through `CCB_SOURCE_ALLOWED_ROOTS`.
- Hardens the Claude restart provider blackbox test to wait for the running partial reply before asserting it.
- Hardens Role Pack CI fixtures so full GitHub Actions tests do not require a sibling `agent-roles-spec` checkout.

</details>

<details>
<summary><b>v7.2.8</b> - Superseded Role Fixture Hotfix</summary>

- Superseded by v7.2.9 after the release gate found that full GitHub Actions runners did not have the sibling `agent-roles-spec` checkout expected by Role Pack tests.

</details>

<details>
<summary><b>v7.2.7</b> - Superseded WSL Mounted Smoke Hotfix</summary>

- Superseded by v7.2.8 after the release gate found a provider blackbox timing race in the Claude restart partial-reply assertion.

</details>

<details>
<summary><b>v7.2.6</b> - Superseded Official Smoke Root Hotfix</summary>

- Superseded by v7.2.7 after the release gate found that the WSL mounted startup smoke in the main Tests workflow also needed its generated `/mnt/c/Temp` project in `CCB_SOURCE_ALLOWED_ROOTS`.

</details>

<details>
<summary><b>v7.2.5</b> - Superseded Source Runtime Guard Hotfix</summary>

- Superseded by v7.2.6 after the release gate found that official soak, fastpath, and storage cleanup smoke checks needed explicit generated test roots in `CCB_SOURCE_ALLOWED_ROOTS`.

</details>

<details>
<summary><b>v7.2.4</b> - Superseded Agent Roles Catalog Release</summary>

- Superseded by v7.2.5 after the release gate found that source checkout `--project` commands were rejected from the source cwd during CCBD real platform smoke checks.

</details>

<details>
<summary><b>v7.2.3</b> - Root Install Support Validation Hotfix</summary>

- Keeps the root install confirmation behavior from v7.2.2: root installs require explicit confirmation, while uninstall remains ungated.
- Preserves install identity metadata and `ccb doctor` runtime user/owner/root diagnostics.
- Fixes WSL release validation by making install metadata tests explicitly simulate non-root identity where required.

</details>

<details>
<summary><b>v7.2.2</b> - Root Install Confirmation Release</summary>

- Adds an explicit root install confirmation gate: `install.sh install` refuses root by default, accepts interactive `yes`, and requires `CCB_ALLOW_ROOT_INSTALL=1` for non-interactive root installs.
- Keeps uninstall cleanup outside the root confirmation gate, so root-owned installs can still be removed.
- Records install identity metadata including root status, install user, and sudo user details.
- Extends `ccb doctor` with runtime user, owner, root state, and a warning when root runs inside a non-root project.
- Fixes the non-blocking build-info type hygiene issue by returning `dict[str, object]` from `read_build_info()`.

</details>

<details>
<summary><b>v7.2.1</b> - Antigravity Runtime Follow-Up</summary>

- Completes `agy` / Google Antigravity runtime and session plumbing with provider runtime specs, client specs, public provider-core exports, and `.agy-<agent>-session` naming.
- Adds regression coverage for named Antigravity pane launches using `AGY_START_CMD`, auto-permission, restore continuation, and prepared-state compatibility.
- Aligns README provider lists and release surface so Antigravity is visible alongside Codex, Claude, Gemini, OpenCode, and Droid.
- Clarifies no-change reload semantics: non-dry-run `ccb reload` with no config delta returns `noop` / `no_op` without publishing a graph.
- Adds Agent Roles public specification planning notes for the future host-neutral RolePack project.

</details>

<details>
<summary><b>v7.2.0</b> - Role Packs And Managed Tools Release</summary>

- Adds the Role Pack surface with the built-in `ccb.archi` architecture role, role memory, Codex/Claude skill projection, and project role locks.
- Makes `ccb roles add ccb.archi:codex` the primary role onboarding command; config stores the shorthand while runtime resolves it to the local `archi` agent.
- Makes `ccb roles install/update ccb.archi` refresh role assets and dependencies by default; install/update prompts interactive users and gives non-interactive users the follow-up command.
- Adds managed tool windows, sidebar rows, and safe reload add/remove behavior for non-agent tools.
- Includes the new `agy` / Google Antigravity provider support from `main`.

</details>

<details>
<summary><b>v7.1.1</b> - Sidebar View Height Release</summary>

- Adds three configurable sidebar sections under `[ui.sidebar.view]`: `agents_height`, `comms_height`, and `tips_height`.
- Changes the default native sidebar split to Agents `50%`, Comms `15%`, and Tips `35%`.
- Carries the height settings through config parsing, project_view payloads, reload planning, and the Rust sidebar TUI.
- Fixes reload reliability for same-name agent remounts: a dynamically unloaded retired agent can be rebuilt under the same name without `runtime_authority_already_exists`, while old stopped session records remain available for inheritance.
- Updates the inherited Codex/Claude `ccb-config` skill docs and references so generated or migrated windows topology exposes all three values.

</details>

<details>
<summary><b>v7.1.0</b> - Dynamic Reload Release</summary>

- Adds explicit hot reload for `.ccb/ccb.config`: use `ccb reload --dry-run` to preview and `ccb reload` to apply supported changes.
- Dynamically mounts append-only agents and new windows under the existing ccbd daemon without interrupting unrelated panes.
- Dynamically unloads idle removed agents and idle removed windows while preserving remaining agent panes.
- Treats config signature drift as reload-pending instead of a daemon restart trigger; busy unloads and unsafe replacements still fail closed.
- Starts the Role Pack surface with `ccb.archi`, `ccb roles ...`, project role
  locks, role memory inclusion, and provider skill projection.

</details>

<details>
<summary><b>v7.0.11</b> - Provider Activity And Sidebar Focus Release</summary>

- Records provider-native activity evidence from hook artifacts so sidebar status can reflect active, pending, idle, and failed provider work more accurately.
- Refreshes sidebar panes immediately after project focus changes by invalidating the cached project view and sending an in-session refresh.
- Restores fast tmux pane click focus with direct `select-pane -t = ; send-keys -M`, avoiding the slower hidden subprocess path for ordinary pane clicks.
- Hardens namespace config, provider hook install settings, clipboard/runtime launch paths, and Codex managed trust handling with focused regression coverage.

</details>

<details>
<summary><b>v7.0.10</b> - Sidebar Tips And Tmux Controls Release</summary>

- Keeps the native sidebar as a stable three-panel view: Tree `1/3`, compact Comms `1/4`, and Tips `5/12`.
- Expands default Tips for projects without custom tips, covering pane movement and resize, window switching, copy mode, paste, and help.
- Preserves the top-right `↻` and `×` controls: `×` runs project-level `ccb kill`, while `q` and `Esc` exit only the sidebar.
- Documents and keeps CCB-managed tmux Vim controls: `mode-keys vi`, copy-mode `v` / `C-v` / `y`, `prefix+h/j/k/l`, and `prefix+H/J/K/L`.

</details>

<details>
<summary><b>v7.0.9</b> - README v7 Redesign Release</summary>

- Rebuilds `README.md` around the v7 visible multi-agent workspace, task-first onboarding, multi-agent approach comparison, v7 UI tour, Quick Start, tmux basics, config examples, and install/update flow.
- Adds real v7 terminal screenshots under `assets/readme_v7/` for the public README walkthrough.
- Preserves the README redesign plan and supporting notes under `docs/plantree/`.
- Keeps the v7.0.8 runtime, `ccb clear`, config overlay, and sidebar fixes intact while refreshing the GitHub-facing documentation package.

</details>

</details>

See [CHANGELOG.md](CHANGELOG.md) for the full history.
