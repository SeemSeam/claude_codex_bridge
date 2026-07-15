<div align="center">

# CCB - Mobile Has Arrived!

**A lightweight multi-agent TUI with a stable cross-provider collaboration layer**<br>
**Coordinate Codex, Claude, Gemini, and other CLI agents in visible, controllable workflows you can take over**

<p>
  <img src="https://img.shields.io/badge/version-8.1.6-orange.svg" alt="version">
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

[中文](README/zh.md) | **English** | [日本語](README/ja.md) | [Français](README/fr.md) | [Deutsch](README/de.md) | [العربية](README/ar.md) | [Español](README/es.md) | [Português](README/pt.md) | [한국어](README/ko.md) | [Русский](README/ru.md)

[Quick Start](#quick-start) · [Mobile App](#mobile-app) · [Rich Mode](#rich-mode) · [Configure Agents](#configure-agents) · [User Guide](docs/manuals/user-guide/) · [Developer Guide](docs/manuals/developer-guide/)

<p align="center">
  <img src="assets/readme_v7/ccb-hero-en-light.png" alt="CCB visible multi-agent CLI workspace" width="960">
</p>

</div>

<a id="why-ccb"></a>

## Why CCB?

- Stable inter-agent communication for complex collaboration graphs such as `A -> B -> C`, `A,B -> C`, and `A -> B,C`.
- Every agent is a full native terminal with visible layout control and direct takeover.
- The background daemon keeps project state alive even when the foreground UI is closed.
- Hub capability: run multiple CLI providers concurrently from one command.
- Mobile remote controller: cross-provider voice control, file transfer, and remote terminal access.

<a id="how-to-install"></a>

## How to Install

Install or update with npm:

```bash
npm install -g @seemseam/ccb
```

After CCB is installed, use CCB's updater:

```bash
ccb update
```

To roll back, use the same transactional updater with an older released version,
for example `ccb update 8.1.3`. CCB rejects a same-version artifact whose build
identity differs from the installed build, and restores the prior local prefix
if the update transaction fails. If restoration itself cannot complete, CCB
retains and reports the external recovery backup path.

<details>
<summary><b>GitHub release package and source install fallbacks</b></summary>

If npm is not convenient in your environment, download the matching package from [Releases](https://github.com/SeemSeam/claude_codex_bridge/releases), unpack it, and install:

```bash
tar -xzf ccb-*.tar.gz
cd ccb-*
./install.sh install
```

Source install is intended only for development or temporary fallback:

```bash
git clone https://github.com/SeemSeam/claude_codex_bridge.git
cd claude_codex_bridge
./install.sh install
```

Source install links global `ccb` / `ask` back to the checkout. Regular users should prefer the npm package.

</details>

<a id="quick-start"></a>

## Quick Start

### 1. Launch

Run this from your working directory:

```bash
ccb
```

If startup reports that `.ccb` cannot be created automatically or that the project anchor is missing, create `.ccb` manually:

```bash
mkdir -p .ccb
```

<a id="configure-agents"></a>

### 2. Configure The Workspace

A blank project starts light: CCB opens one `main` window with a single agent named `demo`, selecting the first supported CLI available on the machine (Codex, Claude, Gemini, then other providers). It no longer mounts a multi-agent team by default.

Click the **⚙ Settings** icon at the top-left of the CCB sidebar to open the local configuration control panel. You can also run `ccb config ui` from the project directory.

<p align="center">
  <img src="assets/readme_v7/config-control-panel.png" alt="CCB configuration control panel editing the default demo agent" width="960">
</p>

The panel edits windows, pane splits, providers, models, thinking levels, API overrides, workspaces, Rich mode, and sidebar settings. It validates changes before saving and supports reload dry-runs and guarded hot reload. Saving creates `.ccb/ccb.config` and pins the selected provider and topology for this project.

For an advanced multi-agent topology, edit it visually or create `.ccb/ccb.config` manually. In v2 `[windows]`, `,` and `;` control vertical stacking and horizontal splits inside each window, so `A,B;C,D` is close to a four-pane layout.

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

Validate the config and start the workspace:

```bash
ccb config validate
ccb
```

### 3. Collaborate

You can type directly in any agent pane, or let agents collaborate:

```text
/ask reviewer review the latest parser changes and list blocking issues.
```

Agents can also call `/ask` during workflow orchestration to delegate and hand off work. Use agent memory or the project-wide shared memory file `.ccb/ccb_memory.md` for durable coordination.

<a id="mobile-app"></a>

## Mobile Remote Control (Android)

The recommended way to control CCB from a phone can connect to all CCB projects, control each agent, accept voice input, and transfer files.

```bash
ccb update mobile
```

This command guides installation and configuration.

<p align="center">
  <img src="assets/readme_v7/mobile-control-chat.jpg" alt="CCB Mobile agent chat" width="180">
  <img src="assets/readme_v7/mobile-control-terminal.jpg" alt="CCB Mobile terminal control" width="180">
  <img src="assets/readme_v7/mobile-control-files.jpg" alt="CCB Mobile file transfer" width="180">
  <img src="assets/readme_v7/mobile-control-pairing.jpg" alt="CCB Mobile pairing and connection" width="180">
</p>

<details>
<summary><b>Mobile App details, safety boundary, and source</b></summary>

CCB Mobile is unchanged in desktop v8.1.6. The last retained Android APK is v8.1.4:

- [Download CCB Mobile v8.1.4 APK](https://github.com/SeemSeam/claude_codex_bridge/releases/download/v8.1.4/ccb-mobile-v8.1.4.apk)
- App source: [`mobile/app`](mobile/app)
- Server gateway source: [`lib/mobile_gateway`](lib/mobile_gateway)

The phone app is a remote controller for real CCB projects running on a server. It can discover mounted projects from the server-wide mobile gateway, switch windows and agents, render agent conversation context, send text through pane-native input, open a terminal view, and upload/download images and documents through the authenticated gateway.

Safety boundary:

- The CCB gateway binds only to loopback, for example `127.0.0.1:8787`.
- Remote access uses Tailscale Serve, not Tailscale Funnel.
- CCB does not store Tailscale passwords, OAuth tokens, admin API tokens, or automatically modify tailnet ACLs/grants.
- The phone receives only the scopes authorized by the pairing profile, such as view, content, terminal, file upload, and file download.

</details>

<a id="rich-mode"></a>

## Rich Media Terminal

Browse file trees, open files, edit documents, and preview media inside the terminal.

<p align="center">
  <img src="assets/readme_v7/rich-workbench.png" alt="CCB rich media workbench using Yazi preview in WezTerm" width="860">
</p>

```bash
ccb update rich
```

After rich mode is enabled, plain `ccb` opens the rich WezTerm launcher automatically unless it is already running inside a CCB-managed rich WezTerm session. Run `ccb uninstall rich` to return to normal terminal startup.

<a id="agent-roles"></a>

## Agent Roles Spec And Role Catalog

CCB supports [Agent Roles Spec](https://github.com/SeemSeam/agent-roles-spec), a host-neutral specification for packaging specialist agents. It can bundle skills, memory, and tool dependencies into installable, mountable, and removable Role Packs. That repository also serves as the public role catalog.

<details>
<summary><b>View the public role catalog</b></summary>

| Role | Purpose |
| :--- | :--- |
| `agentroles.ccb_self` | CCB self-maintenance, config help, runtime diagnosis, protected recovery, and workflow orchestration. |
| `agentroles.archi` | Architecture review, boundary checks, coupling analysis, maintainability risks, and follow-up gate advice. |
| `agentroles.frontend_engineer` | Frontend design and implementation, design systems, accessibility, browser QA, and reviewed AGY delegation. |
| `agentroles.mobile_app_engineer` | Mobile design and implementation for iOS, Android, React Native, Expo, Flutter, SwiftUI, Jetpack Compose, and more. |
| `agentroles.mother` | Role creation, role source audit, role research, blueprint design, and Agent Roles spec compliance checks. |
| `agentroles.su_ccb` | SU-CCB workflow operations for requirement analysis, planning, dispatch, review gates, archiving, and recovery. |

</details>

<a id="config-memory"></a>

## Config And Shared Memory

Use the **⚙ Settings** control panel for normal project configuration. If you want agent-assisted configuration and runtime diagnosis, `ccb_self` remains available as an optional Role Pack and can be added with `ccb roles add agentroles.ccb_self:codex`.

`.ccb/ccb_memory.md` is the project-wide shared memory document. Use it for team collaboration rules, project constraints, long-lived context, and agent handoff conventions. Stable cross-agent information belongs there instead of being copied into several provider-private memory files.

<a id="contact"></a>

## Contact

- Email: `bfly123@126.com`
- [Telegram group & contact / TG 群与联系](https://t.me/+BKn03v8I_ehmYzRk)
- WeChat: `seemseam-com`

<p align="center">
  <img src="assets/weixin.jpg" alt="WeChat group" width="240">
</p>

<a id="community"></a>

## Community And Credits

Thanks to the [Linux.do community](https://linux.do) for testing, feedback, and discussion.

Thanks to [tmux-agent-sidebar](https://github.com/hiroppy/tmux-agent-sidebar) for sidebar ideas and inspiration.

<a id="release-notes"></a>

## Release Notes

<details open>
<summary><b>v8.1.6</b> - Emergency communication reliability fix</summary>

- Fixed managed Codex session rebinding and request-specific delivery acknowledgement.
- Prevented context clearing while active mailbox work is in flight.
- Desktop and npm only; CCB Mobile is unchanged and not included.

</details>

<details>
<summary><b>v8.1.5</b> - Withdrawn</summary>

- Withdrawn because its public release notes disclosed unreleased details.

</details>

<details open>
<summary><b>v8.1.4</b> - Codex subagent isolation and Grok native skills</summary>

- Prevented Codex native subagent rollouts from capturing CCB request binding or replacing the authoritative parent session and turn.
- Kept built-in subagent activity, messages, and completion events inside the parent agent's collaboration flow instead of returning them to the CCB caller.
- Matched the isolation behavior in the Python runtime and Rust accelerator, with an authenticated `spawn_agent` regression proving that callers receive only the parent final reply.
- Added independently projected native `ask` and `ccb-clear` skills to each managed Grok home; normal starts use Grok's native `bypassPermissions` mode while safe starts keep approval enabled.
- Refreshed inherited system Grok login state before startup and routed CCB requests through each agent's visible native Grok session; authenticated two-agent testing passed visible ask, result recovery, named clear, and post-clear isolation.

</details>

<details open>
<summary><b>v8.1.3</b> - Mobile interaction reliability and Grok completion</summary>

- Stabilized Mobile live conversations by merging streamed replies into one working bubble, preserving bubble identity, and avoiding refresh flicker or false working states.
- Kept agent and window selection stable across refreshes, retained pane-authentic terminal scrollback, and required explicit keyboard activation before terminal input.
- Replaced the Android pairing bridge with the embedded ML Kit scanner and preserved its release-build classes through minification.
- Filtered Codex local control transcript entries and required Grok's native turn-completion evidence before a managed request is finalized.

</details>

<details>
<summary><b>v8.1.2</b> - Mobile conversation reliability and installer certificate recovery</summary>

- Hardened Mobile invalidation recovery, snapshots, live conversation updates, attachment echo reconciliation, and task-completion notifications.
- Restored expanded-message scrolling and project file links while simplifying terminal shortcuts, compacting controls, and removing duplicate terminal headers.
- Reused managed Python environments now refresh legacy pip versions for system certificate support and opt into truststore only when its backend is available.
- Expanded guarded HTTPS mirror fallback detection for macOS DNS, proxy, timeout, and certificate failures without disabling TLS verification.

</details>

<details>
<summary><b>v8.1.1</b> - Mobile realtime recovery and macOS installer resilience</summary>

- Added a bounded Mobile gateway SSE invalidation stream so project, activity, and conversation changes refresh authoritative state without active-view polling.
- Added bounded read-only Mobile snapshots, reconnect status and automatic recovery while retaining the selected host, project, agent, recent conversation state, and completion notifications.
- Mobile host startup now recognizes and safely adopts matching legacy gateway processes, avoiding duplicate listeners during upgrades.
- macOS release updates preserve healthy managed Python environments and retry `watchdog` installation through a configurable mirror after TLS or network failures.

</details>

<details>
<summary><b>v8.1.0</b> - Config control plane and lighter defaults</summary>

- Added a visual project configuration control panel, opened from the sidebar's top-left **⚙ Settings** action or with `ccb config ui`, with validation, diff review, save, reload dry-run, and guarded hot reload.
- Blank projects now mount exactly one agent named `demo`, selecting the first locally available supported CLI; explicit project and user configs still support any single- or multi-agent topology.
- Added managed Grok CLI integration, Kimi Code v0.23.1 readiness support, correct OpenCode fresh-session behavior, and reliable Claude/Gemini hook launcher execution.
- Improved CCB Mobile gateway profile persistence, paired-credential retention, project health caching, warm-list visibility, and terminal UI efficiency.
- Reorganized localized READMEs under `README/`, added the real config-control screenshot, and synchronized package, Mobile, workflow, and release metadata for 8.1.0.

</details>

<details>
<summary><b>v8.0.19</b> - Mobile host startup health-check fix</summary>

- `ccb update mobile` now uses more tolerant per-request and overall startup timeouts for the server-wide loopback `/v1/health` endpoint, avoiding false failures when many projects are mounted.
- Added a regression test covering health responses that arrive after the previous 0.5-second request timeout.
- The default APK URL, README, package metadata, and mobile app version metadata now point to 8.0.19.

</details>

<details>
<summary><b>v8.0.18</b> - Codex auth projection and Mobile host health fixes</summary>

- Managed Codex homes now project `auth.json`, `config.toml`, company API sidecars, and safe auth/key/token sidecar filenames referenced by `config.toml`.
- Added `.ccb-auth-projection.json` evidence manifests that record source and target presence, size, and SHA256 without storing secret values.
- Explicit Codex API authority clears inherited auth sidecars, WSL diagnostics identify Windows interop executables, and server-wide mobile discovery tolerates stale project records.
- The role catalog is now collapsed by default, the WeChat image is refreshed, and mobile release metadata points to 8.0.18.

</details>

<details>
<summary><b>v8.0.17</b> - Ask reply reliability and Mobile update fixes</summary>

- Codex ask completion now uses no-progress time, so actively growing long-session files do not fail based only on submission age.
- Missing official session or log evidence returns a diagnosable non-success state, while explicit shutdown is reported as a provider crash.
- Mobile frontdesk submissions use ccbd ask jobs, and `ccb watch` no longer defaults to a 10-second timeout.

</details>

<details>
<summary><b>v8.0.16</b> - Mobile reconnect and pane activity tracking</summary>

- CCB Mobile Terminal mode adds reconnect diagnostics and recovery while keeping the current agent pane selected.
- Pane-native mobile input now records project activity so project recency reflects Terminal usage.

</details>

<details>
<summary><b>v8.0.12</b> - Release CI portability and README localization</summary>

- Mobile host registry tests now place temporary Unix sockets under a short `/tmp/ccb-sock-*` path, avoiding `AF_UNIX path too long` failures on macOS CI.
- `ccb update mobile`, README links, package metadata, and the mobile release manifest now point to the 8.0.12 APK.
- v8.0.12 introduced a multilingual README set with a shared section structure; localized files now live under [`README/`](README/), with Chinese at [`README/zh.md`](README/zh.md).

</details>

<details>
<summary><b>v8.0.0</b> - CCB Mobile Monorepo release</summary>

- The Flutter CCB Mobile source officially moved into this repository, with the Android APK published through GitHub Releases.
- Added server-wide mobile project discovery, pairing, authenticated gateway routes, pane-native message input, conversation context rendering, terminal access, and image/document upload and download.
- Promoted `ccb update mobile` into the unified Tailscale Tailnet onboarding entrypoint while keeping the gateway loopback-only, avoiding Funnel, not storing tokens, and not automatically modifying ACLs/grants.

</details>

<details>
<summary><b>v7.7.0</b> - Runtime Accelerator release hardening</summary>

- Release artifacts now include the optional Rust `ccb-runtime-accelerator`; installed Codex agents no longer silently fall back to the Python hot path when the sidecar is expected.
- When a project path makes the Unix socket path too long, the accelerator socket automatically moves to a short per-user runtime socket root.
- Hardened callback repair and Codex binding cache invalidation, with recorded regression, long-idle Codex soak, Claude callback, and mixed-provider integration evidence.

</details>

<details>
<summary><b>v7.6.19</b> - Long-running ask default wait policy</summary>

- Regular long-running `ask` calls now continue waiting for real provider/completion results instead of terminalizing as `incomplete/heartbeat_timeout` only because of heartbeat diagnostics.
- Codex, Claude, and Gemini pane-backed no-terminal timeouts are now explicit opt-in by default, while explicit reliability timeout policies remain available.
- A 32-minute source-runtime ask smoke confirmed that a task can remain running for more than 30 minutes, then complete with `result_message`, without `heartbeat_timeout` or `incomplete` evidence.

</details>

See the full history in [CHANGELOG.md](CHANGELOG.md).
