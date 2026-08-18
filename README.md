<div align="center">

# CCB - Mobile Has Arrived!

**A lightweight multi-agent TUI with a stable cross-provider collaboration layer**<br>
**Coordinate Codex, Claude, Gemini, and other CLI agents in visible, controllable workflows you can take over**

<p>
  <img src="https://img.shields.io/badge/version-8.6.10-orange.svg" alt="version">
  <img src="https://img.shields.io/badge/platform-Linux%20%7C%20macOS%20%7C%20WSL%20%7C%20Windows%20beta-lightgrey.svg" alt="platform">
  <img src="https://img.shields.io/badge/providers-17%20CLI%20families-0B7285.svg" alt="providers">
</p>

<p>
  <img src="https://img.shields.io/badge/Codex-111111?style=flat-square&logo=openai&logoColor=white" alt="Codex">
  <img src="https://img.shields.io/badge/Claude-D97757?style=flat-square&logo=anthropic&logoColor=white" alt="Claude">
  <img src="https://img.shields.io/badge/Gemini-4285F4?style=flat-square&logo=googlegemini&logoColor=white" alt="Gemini">
  <img src="https://img.shields.io/badge/DeepSeek%20Harness-4D6BFE?style=flat-square" alt="DeepSeek Harness">
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
- Interactive CLI agents are full native terminals with visible layout control
  and direct takeover; service-backed providers keep an explicit managed
  host/log surface without pretending it is their request protocol.
- The background daemon keeps project state alive even when the foreground UI is closed.
- Hub capability: run multiple CLI providers concurrently from one command.
- Mobile remote controller: cross-provider voice control, file transfer, and remote terminal access.

<a id="how-to-install"></a>

## How to Install

Install or update an npm-managed CCB with npm:

```bash
npm install -g @seemseam/ccb@latest
```

For GitHub release-package or source installs, use CCB's transactional updater:

```bash
ccb update
```

On an npm-managed install, `ccb update` prints the equivalent npm command and
does not modify npm's vendored payload in place.

<details>
<summary><b>Native Windows x64 beta</b></summary>

The Windows beta artifact is attached to the matching stable CCB GitHub
release. Download `ccb-windows-x86_64.zip` and its `.sha256` sidecar, verify
the digest, extract the ZIP, then run:

```powershell
.\install.ps1 install -Yes
ccb --print-version
```

It requires native Windows x64, Python 3.10+, WezTerm, Git Bash, and Herdr
0.8.0 or newer. The installer creates an install-local managed Python runtime.
The binaries are unsigned, and `ccb update` remains diagnostic-only for this
beta support tier; install a later Windows build by rerunning its validated
`install.ps1`.

</details>

CCB-managed provider panes suppress known provider-native startup update prompts.
After updating CCB—or immediately when CCB is already current—`ccb update`
checks installed provider CLIs and offers supported updates once. Use
`--providers check`, `--providers all`, or `--providers none` for explicit
report-only, non-interactive update, or skip behavior. Declining prompts again
on the next `ccb update`; skipping a version hides only that exact version.
CCB never restarts active provider panes during this flow, so an accepted
provider update applies when that pane next starts or is explicitly restarted.

The official DeepSeek Harness integration is available as the separate
Developer Preview provider key `dsh` (`deepseek` continues to mean the Deep
Code CLI). Install its current npm release with a supported Node runtime, then
select `dsh` in Config UI or use an Agent leaf such as `research:dsh`:

```bash
npm install -g @deepseek-ai/dsh
dsh --version
```

CCB starts `dsh web` on loopback and communicates through DSH's structured
HTTP/WebSocket carrier. The current POSIX runtime may host that service in a
managed pane only for lifecycle/log ownership; prompts, replies, native
completion, `ccb compact`, and restore do not depend on terminal input or
pane-text heuristics. Configure `DEEPSEEK_API_KEY` (and
optionally `DEEPSEEK_BASE_URL`) in user-owned DSH state or through CCB's
provider-profile/API controls; CCB does not obtain credentials automatically.

After a release change, the newly installed CCB also retires old
project-scoped Claude/Gemini caches. Manifest-valid caches for deleted projects
are removed immediately. A stopped current project is cleaned immediately;
active or other existing projects are preserved and cleaned after their next
successful `ccb kill`. Unknown providers, malformed manifests, foreign
symlinks, sessions/auth, and the user-scoped Gemini cache are never removed by
this migration. Use `ccb update --no-cache-cleanup` to skip it for one update.

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

#### Persistent local Config UI access

The Config UI always binds to loopback. To use a stable local port and token, configure a token **source** in `.ccb/ccb.config`; never put a literal token in that file:

```toml
[config_ui]
port = 43123
token_env = "CCB_CONFIG_UI_TOKEN"
# Or use this instead of token_env:
# token_file = ".ccb/config-ui.token"
```

`--port` remains a one-run override. `token_file` must be project-relative, non-symlinked, and owner-only (`chmod 600 .ccb/config-ui.token` on POSIX). Without a token source, CCB keeps the existing random token and ephemeral-port behavior. The CLI prints only the loopback URL and token source, never the token value.

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

CCB 8.6.10 includes the Flutter CCB Mobile source in [`mobile/`](mobile/) and publishes the Android APK through GitHub Releases:

- [Download CCB Mobile v8.6.10 APK](https://github.com/SeemSeam/claude_codex_bridge/releases/download/v8.6.10/ccb-mobile-v8.6.10.apk)
- App source: [`mobile/app`](mobile/app)
- Server gateway source: [`lib/mobile_gateway`](lib/mobile_gateway)

The phone app is a remote controller for real CCB projects running on a server. It can discover mounted projects from the server-wide mobile gateway, switch windows and agents, render agent conversation context, send text through pane-native input, open a terminal view, and upload/download images and documents through the authenticated gateway.

Safety boundary:

- The CCB gateway binds to loopback by default, for example `127.0.0.1:8787`.
- For direct LAN access, bind one specific private interface address (wildcard and public addresses are rejected): `ccb install mobile --route-provider lan --listen 192.168.31.155:8787`. The pairing URL is inferred from `--listen`; no forwarding process or `--public-url` is needed.
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

Supported managed Agents receive the built-in `ask`, `ccb-clear`, `ccb-compact`, and `ccb-diagnose` control skills even when optional skill inheritance is disabled. Use `$ccb_diagnose <agentname>` to inspect one Agent's authoritative runtime/job state and live pane evidence, apply bounded recovery when safe, and review a redacted issue draft before explicitly authorizing GitHub submission. Managed Codex also keeps `reconnect`.

`.ccb/ccb_memory.md` is the project-wide shared memory document. Use it for team collaboration rules, project constraints, long-lived context, and agent handoff conventions. Stable cross-agent information belongs there instead of being copied into several provider-private memory files.

<a id="contact"></a>

## Contact

- Email: `bfly123@126.com`
- [Telegram group & contact / TG 群与联系](https://t.me/+BKn03v8I_ehmYzRk)
- WeChat: `seemseam-com`

<p align="center">
  <img src="assets/weixin.png?v=0a86422d" alt="CCB WeChat group 2" width="240">
</p>

> WeChat group QR codes are valid for seven days. If this one has expired, add `seemseam-com` to request the latest invitation.

<a id="community"></a>

## Community And Credits

Thanks to the [Linux.do community](https://linux.do) for testing, feedback, and discussion.

Thanks to [tmux-agent-sidebar](https://github.com/hiroppy/tmux-agent-sidebar) for sidebar ideas and inspiration.

<a id="release-notes"></a>

## Release Notes

<details open>
<summary><b>v8.6.10</b> - Claude OAuth re-login isolation</summary>

- Refresh an existing Agent-private Claude Keychain credential after an external OAuth re-login, so a stopped managed restart does not keep using a revoked token (Issue #319).
- Preserve a Claude-private Keychain refresh when the inherited source credential is unchanged; external Claude Keychain services remain read-only.
- Fail closed for symlinked CCB credential projections and private Keychain inspection errors. No project, conversation, pairing, or configuration migration is required.

</details>

<details>
<summary><b>v8.6.9</b> - DeepSeek Harness, AGY startup, and Windows isolation</summary>

- Add the official DeepSeek Harness as the separate Developer Preview provider `dsh`, using its loopback HTTP/WebSocket service and exact native turn evidence.
- Make managed AGY 1.1.13 select private file token storage immediately, avoiding the keyring timeout without writing to the user's source HOME (Issue #318).
- Roll back Windows PR changes that crossed into shared Linux/macOS runtime code and add a trusted-base native-only PR gate that rejects future cross-platform leakage.
- Keep DSH clear, compact, exact-session restore, credentials, skills, and runtime state inside provider-native, agent-private boundaries.

</details>

<details>
<summary><b>v8.6.5</b> - Responsive and reliable Mobile terminals</summary>

- Reflow Agent terminal snapshots to the phone viewport while preserving the desktop tmux pane geometry.
- Keep terminal input and reconnection stable across LAN and Relay routes, and close active sessions cleanly during gateway shutdown.
- Keep terminal font size and shortcut order in the shared Terminal settings panel, with full-width inline terminal mode on phones and wide layouts.
- Reattach persisted Windows Herdr namespace and pane references with fail-closed capability checks (PR #304).

</details>

<details>
<summary><b>v8.6.3</b> - Mobile access to agent workspace artifacts</summary>

- Turn links to ordinary files in the current Agent's `.ccb/workspaces/&lt;agent&gt;/...` tree into authenticated Mobile download attachments.
- Keep the boundary fail-closed: other Agent workspaces, hidden workspace paths, and all other private `.ccb` runtime state remain unavailable.
- Preserve the existing Mobile client behavior and pairing model; no project configuration or state migration is required.

</details>

<details>
<summary><b>v8.6.2</b> - Explicit command approval, resilient sessions, and broader Mobile terminals</summary>

- Require exact, external approval before project configuration can execute tool-window commands or custom Provider command templates; use `ccb config approve-commands` for intentional values.
- Recover managed Codex conversations from the latest valid session when the current session record is corrupt, without silently clearing context.
- Add visible-pane Cursor execution, Pi native history, OMP Provider-config inheritance, and more reliable Windows process and namespace cleanup.
- Expand CCB Mobile with multi-session host terminals, Relay capability-negotiated Provider controls, and stronger native Windows readiness validation.

</details>

<details>
<summary><b>v8.6.1</b> - Mobile Provider controls, direct terminals, and safe context compaction</summary>

- Show selected-Agent Provider identity, configured/active/pending model and thinking state, Codex/Claude native session usage, and optional account quota in CCB Mobile.
- Persist supported model/thinking choices through guarded, restart-required host configuration without interrupting active work or exposing Provider credentials.
- Open project-window or Agent terminals directly from Mobile home and customize terminal shortcut visibility and order.
- Add the built-in `ccb-compact` Skill and `ccb compact` command with outstanding-work checks and fail-closed Provider command selection.
- Populate Config UI from the complete available Role catalog, retain native-session boundaries in Mobile history, and refresh the WeChat group QR image.

</details>

<details>
<summary><b>v8.5.7</b> - Built-in agent diagnosis and stuck-delivery recovery</summary>

- Keep `ccb-clear` in every supported managed Agent and add the required `ccb-diagnose` Skill. Run `$ccb_diagnose &lt;agentname&gt;` to combine daemon, lineage, queue, inbox, trace, provider-log, and live Pane evidence for one Agent.
- Classify working, waiting-input, stale-prompt, Provider-error, dead/blank, and misframed Pane states; apply only evidence-backed bounded control-plane recovery and verify the Agent afterward.
- Generate a redacted incident draft after diagnosis, but require fresh user authorization before creating any GitHub issue.
- Recover abandoned mailbox deliveries without replaying business work, expose queue depth and active job identifiers in the sidebar, preserve Kiro login/settings through its isolated `KIRO_HOME`, forward Provider no-terminal timeout settings, and keep old Claude busy-turn records fenced from newly queued requests.
- Probe reconnect readiness against the active Codex Provider route so custom Provider capacity and connectivity failures can continue through the existing guarded recovery path.

</details>

<details>
<summary><b>v8.5.6</b> - Continuous Provider inheritance without clearing CCB conversations</summary>

- Resolve each configured API, token, URL, route, and account dimension from CCB-local authority first, then read only the missing dimensions from the current external Provider state.
- Refresh inherited state on a stopped Provider generation without writing back to the user's shell, Provider home, IDE, keyring, or remote login.
- Keep one stable CCB conversation and its workspace, queue, and history across authority generations. Same-authority sessions use native resume; supported Codex/Claude/Gemini import or fork when safe, otherwise CCB records a linked continuation instead of hiding history.
- Recover usable sessions affected by the withdrawn v8.5.5 migration shape without clearing them. The withdrawn v8.5.5 package is not reused.
- Install and automatically arm bundled `codex-reconnect` for managed Codex panes; terminal network and selected-model capacity failures receive one bounded `continue` recovery when the exact pane and thread are proven safe.
- Raise the default quota for newly issued Relay Host invitations from 200 MiB to 1 GiB per 24-hour window; explicit overrides and existing credentials remain unchanged.
- Refresh the WeChat community QR image and cache key. No configuration or data migration is required.

</details>

<details>
<summary><b>v8.5.5</b> - Withdrawn compatibility release</summary>

- Withdrawn from GitHub on 2026-08-05 after a legacy-session migration regression made existing managed conversations unavailable to native `resume`.
- Use v8.5.6 or v8.5.4; do not install v8.5.5 for managed sessions.

</details>

<details>
<summary><b>v8.5.4</b> - Safer ask routing, bounded history cleanup, and actionable Mobile LAN recovery</summary>

- Treat `--chain` as a real dependency only: independent asks, communication tests, batches, notifications, and reply-delivery acknowledgements no longer create false callback chains.
- Scan or clean one Agent or all Agents from Config UI with 7/30/90-day retention while protecting active bindings, recent history, and each Provider's newest transcript.
- Keep the current Config UI focused on configuration and history cleanup by temporarily removing the read-only communication-flow observer.
- Add Android LAN preflight and reconnect guidance, Retry/Diagnostics actions, and a 15-second terminal WebSocket heartbeat without reading SSID, BSSID, or other network identity.
- Flutter analysis and all 736 App tests pass; the physical Android Wi-Fi/hotspot/VPN/DHCP-change matrix remains an explicit validation limitation.

</details>

<details>
<summary><b>v8.5.3</b> - Higher Relay quota, bounded callback repair, and complete Claude agent env</summary>

- Raise the default quota for newly issued Relay Host invitations from 100 MiB to 200 MiB per 24-hour window; explicit operator quota flags still take precedence.
- Cache the latest callback-edge view and bound repair candidates, preventing old append-only callback history from driving sustained idle CPU and cached-read amplification.
- Pass non-API variables from `agents.&lt;name&gt;.env` into managed Claude launches while preserving CCB's API credential and endpoint precedence (PR #284).
- Existing Relay Host credentials retain their issued quota; operators must update them separately or issue a new invitation.

</details>

<details>
<summary><b>v8.5.2</b> - Bounded pane recovery, quieter asks, and isolated Rich terminal launches</summary>

- Keep a respawned pane in a 90-second probation window and hold queued work until a new healthy observation confirms recovery.
- Back off unstable recovery through 30s/60s/120s/5m/10m/30m, then open a circuit after six attempts instead of restarting and writing indefinitely.
- Bound each Provider runtime to the newest 50 pane-crash records, clear stale pane history, and skip unchanged helper-manifest writes.
- Repair only stale CCB-managed Claude continuation state without touching authentication; fail closed when the managed Codex app server is unavailable.
- Move stable reply/cancellation policy into managed project memory so normal asks no longer repeat prompt blocks or require per-step cancel-file polling.
- Detach CCB Rich WezTerm from the parent TTY and provide a private Wayland XCursor overlay without replacing the selected cursor theme.

</details>

<details>
<summary><b>v8.5.1</b> - Complete Claude replies, visible Pi execution, and proxy-safe Mobile health checks</summary>

- Aggregate Claude snapshots by assistant message so thinking-only boundaries and tool narration cannot replace the true final reply.
- Recover missing Claude completion hooks only from exact request, Agent, workspace, time, and session evidence; stalled mid-stream responses fail closed.
- Execute new Pi asks in the managed visible pane and complete only from the exact bound `agent_settled` message.
- Keep long Pi runs free from a fixed default terminal timeout and remove the model-facing cancel-file probe that consumed an extra tool call and uncached tokens.
- Preserve persisted Pi 8.5.0 jobs and the explicit `CCB_PI_EXECUTION_MODE=headless` rollback path.
- Bypass configured HTTP proxies for local Mobile gateway health checks.

</details>

<details>
<summary><b>v8.5.0</b> - Exact Pi/OMP completion, self-healing npm runtime, synchronized Mobile activity, and safer managed assets</summary>

- Bound Pi completion to the latest `agent_settled` event and OMP completion to `agent_end.isTerminal=true`, while waiting for process exit and output closure before terminalizing.
- Failed closed on missing native terminal evidence, missing final outcomes, malformed or truncated JSONL, provider errors, and nonzero exits; terminal OMP `yield` remains a supported successful result.
- Bootstrapped and repaired the npm-managed Python environment during install, so release runtime dependencies no longer depend on an incomplete system Python fallback; linked Git worktrees remain correctly classified as source installs.
- Made the sidebar settings launcher recover its release-managed runtime and reliably open Config UI.
- Synchronized Mobile ask/provider activity with exact server-side state, stable completion notifications, and quieter automatic reconnect behavior.
- Tightened one-way managed Provider asset projection and added packaged Qoder control skills without exposing user-global state to managed mutation.

</details>

<details>
<summary><b>v8.4.3</b> - Isolated Provider auth, guaranteed control skills, and reliable Mobile pairing and terminal recovery</summary>

- Isolated mutable authentication, account, session, and storage state inside each managed Provider home for visible and headless execution.
- Made external credentials one-way inheritance input, so managed refresh or logout cannot modify the user's shell, IDE, another Agent, or another project.
- Guaranteed packaged `ask` and `ccb-clear` controls for supported managed Agents even when optional skill inheritance is disabled; managed Codex also keeps `reconnect`.
- Projected optional skills independently so one broken external entry cannot suppress CCB controls or unrelated valid skills.
- Added a compact, validated Relay pairing QR that fits a 97-column terminal while retaining the owner-only PNG fallback.
- Made Mobile terminal resize and stream recovery race-safe with synchronized snapshots, clean repaint, and automatic handle renewal.

</details>

<details>
<summary><b>v8.4.2</b> - Persistent Config UI themes, stable Relay terminal streaming, and safe Provider updates</summary>

- Opened Config UI with the release-managed interpreter even when the sidebar inherited a stale Python path.
- Added persistent CCB theme selection, including system-default behavior, across Config UI, Rich WezTerm, and sidebar restarts.
- Stabilized Relay terminal snapshots and incremental updates with bounded backpressure and correct wide-character accounting.
- Generated owner-only PNG pairing codes when dense payloads cannot render safely in the current terminal.
- Preserved npm/NVM and Bun package-manager ownership during Provider updates.
- Reported non-writable system installs and unsafe local registry dependencies without attempting an update.

</details>

<details>
<summary><b>v8.4.0</b> - Encrypted Mobile Relay, simpler pairing, stable project identity, and Codex reconnect</summary>

- Added end-to-end encrypted CCB Mobile Relay transport with operator-issued one-time invitations, bounded admission, multiplexed streams, and official or self-hosted deployment modes.
- Moved route selection to `ccb update mobile`: choose Tailscale, a validated private LAN address, CCB Relay, or a self-hosted Relay while the phone only scans a QR code or enters a pairing code.
- Added trusted in-app Android updates that verify canonical GitHub release metadata, APK size, and SHA-256 before handing installation to the operating system.
- Preserved project identity when a CCB project is moved or renamed, and added system-following light/dark themes across terminal and configuration surfaces.
- Integrated opt-in Codex reconnect supervision into managed homes, retaining bounded terminal error evidence and refusing unsafe pane or session mismatches.
- Kept Relay payloads encrypted end to end; Relay operators can observe connection metadata but not task prompts, replies, terminal content, or transferred files.

</details>

<details>
<summary><b>v8.3.1</b> - Unified Provider updates, safe cache retirement, and persistent Config UI access</summary>

- Centralized supported Provider upgrades under `ccb update`, with exact version checks, explicit decline/skip choices, and no automatic restart of active panes.
- Retired project-scoped Claude/Gemini software caches in favor of the user-installed Claude executable and one user-scoped Gemini cache.
- Added bounded post-update and post-shutdown cleanup that preserves active projects, unknown content, sessions, authentication data, and user-owned caches.
- Added persistent Config UI loopback port and protected token-source settings without exposing token values.
- Added native Qoder CLI CN support with isolated config/session state and corrected Qoder `--print` / `--config-dir` execution.
- Preserved shutdown finalizers while the server is stopping and made sidebar release checksum generation portable.
- Switched Rich mode to a compact two-column Yazi layout and synchronized all release surfaces to 8.3.1.

</details>

<details>
<summary><b>v8.3.0</b> - Exact provider turns, job integrity, and project-bound Mobile terminal</summary>

- Bound Kimi, Claude, and Qoder execution to their native turn, activation, session, and completion contracts.
- Added exact active-job follow-ups, correlated execution phases, orphaned-inbound diagnosis, and terminal cancellation outcomes.
- Inherited provider extensions and Copilot plugins with explicit projected-asset ownership safeguards.
- Delegated npm-managed upgrades to npm and made marker-only worktree retirement conservative.
- Kept Mobile chat and terminal modes inside the selected project workspace and synchronized all release surfaces to 8.3.0.
- Fixed sidebar settings launch on WSL and macOS with native browser fallbacks, refreshed desktop-session environment, and visible manual-open status.

</details>

<details>
<summary><b>v8.2.1</b> - Deterministic startup, actionable auth recovery, and Android background access</summary>

- Added end-to-end startup generation fencing, bounded readiness proof, and detailed startup operation/timeline diagnostics.
- Stopped unrecoverable provider-auth restart loops and exposed the required login action through ping, project view, and the sidebar.
- Preserved additive reload identity and idempotent shutdown behavior under the stricter lifecycle authority model.
- Added opt-in Android background connection controls and kept one working reply state per agent.
- Synchronized Linux, macOS, npm, and signed Android artifacts for 8.2.1.

</details>

<details>
<summary><b>v8.2.0</b> - Faster startup, provider fixes, and Mobile reliability</summary>

- Reduced repeated ccbd startup work while preserving lifecycle and ownership checks.
- Fixed Grok fullscreen startup, Claude credential-kind preservation, and Config UI model/thinking selections reverting to inherited values.
- Kept Codex ask delivery session-bound and stopped accepted empty transport acknowledgements from requeueing indefinitely.
- Improved Mobile recovery, conversation and terminal interaction, image/document/video attachments, linked-file downloads, and device-bound FCM delivery.
- Synchronized Linux, macOS, npm, and signed Android artifacts for 8.2.0.

</details>

<details>
<summary><b>v8.1.6</b> - Withdrawn</summary>

- Superseded by later releases. Detailed notes are intentionally omitted.

</details>

<details>
<summary><b>v8.1.5</b> - Withdrawn</summary>

- Withdrawn and superseded. Detailed notes are intentionally omitted.

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
