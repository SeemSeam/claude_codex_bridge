# CCB Config And Layout Contract

## 1. Purpose

This document defines the non-drifting user-facing contract for project configuration, pane layout, and tmux presentation in `ccb_source`.

It is the authoritative design anchor for:

- `.ccb/ccb.config`
- `.ccb/ccb_memory.md` and `.ccb/agents/<agent>/memory.md` project memory placement
- compact layout grammar
- config source precedence and fallback
- pane naming and pane color identity
- tmux split sizing rules for the project UI

## 2. User-Facing Config Contract

- Effective config is resolved in three layers:
  1. built-in default config from code;
  2. user config at `~/.ccb/ccb.config`;
  3. project config at `.ccb/ccb.config`.
- Higher layers override lower layers by replacing the whole effective config; CCB does not merge partial config documents across layers.
- `.ccb/ccb.config` is the highest-priority user-facing project config file.
- `~/.ccb/ccb.config` is the user-level config file used only when project config is absent.
- CCB must not auto-create, reconstruct, or rewrite `.ccb/ccb.config`; it is a user-authored project file.
- When both `.ccb/ccb.config` and `~/.ccb/ccb.config` are absent, config loading must use the built-in default project config from code and report the source kind as `builtin_default`.
- The built-in default project config contains exactly one `demo` agent in the
  `main` window. It selects the first locally available supported provider CLI,
  preferring `codex`, then `claude`, then `gemini`, followed by the remaining
  built-in providers in registry order.
- Provider availability uses the effective provider executable, including a
  configured `*_START_CMD` override. If no supported provider executable is
  available, the default remains `demo:codex` so startup fails with the normal
  actionable missing-executable diagnostic instead of inventing an invalid
  empty topology.
- Built-in provider selection is evaluated while the built-in default is in
  use. Saving from the config control panel creates an explicit project config
  and therefore pins the selected provider and topology.
- User help text, validation output, diagnostics, and docs must report the active config source kind: `project_config`, `user_config`, or `builtin_default`.
- `.ccb/config.yaml` is not part of the contract and must not be read or written by current code.

### 2.1 Project Shared Memory Files

Project memory files are user context, not startup/layout authority.

- `.ccb/ccb_memory.md` under the project anchor is the shared CCB project memory file.
  - Startup may create it only when missing.
  - Startup must not overwrite user edits.
  - Teams may whitelist and commit it when they want shared agent collaboration rules.
- `.ccb/agents/<agent>/memory.md` is optional agent-private memory.
  - It is user-editable local data and must not be deleted or rewritten by
    normal startup.
  - `<agent>` is the normalized logical agent name from `.ccb/ccb.config`.
  - This path is anchored under the project `.ccb/` directory and does not move
    with relocated runtime state.
- Generated project-memory metadata and runtime bundles are CCB-owned state:
  - `<runtime_state_root>/state/memory.seed.json` records template seed
    metadata for the initial `.ccb/ccb_memory.md`.
  - `<runtime_state_root>/runtime/memory/<agent>.md` is generated runtime
    memory for providers that need a stable file path.
  - `project_root/.ccb/runtime/memory/<agent>.md` may be generated as a
    provider compatibility bridge for tools such as OpenCode that consume
    project-relative instruction paths.
- When runtime state is relocated, user-editable memory remains under the
  project anchor while generated runtime memory follows `runtime_state_root`.
- `.ccb/` is local/runtime state by default. Projects that want to commit
  selected files such as `.ccb/ccb.config` must opt in with their own
  `.gitignore` whitelist.

## 3. Compact Layout Grammar

The primary config format is compact text.

Leaf tokens:

- `cmd`
- `agent_name:provider`
- `agent_name:provider(worktree)`

Any leaf token may add an `@N` pane split percent hint, for example `cmd@30`,
`agent_name:provider@70`, or `agent_name:provider(worktree)@40`. The hint is
layout syntax, not an agent field or overlay. Runtime materialization clamps
explicit split hints into the tmux-safe `1..99` range.

Operators:

- `;`
  - horizontal split, left to right
- `,`
  - vertical split, top to bottom
- `(...)`
  - explicit grouping

Operator precedence:

1. `,`
2. `;`

Examples:

- `agent1:codex`
- `agent1:codex, agent2:claude`
- `agent1:codex; agent2:codex, agent3:claude`
- `agent1:codex; agent2:codex, (agent3:claude; agent4:gemini)`
- `kimi_agent:kimi, qwen_agent:qwen, cursor_agent:cursor, kiro_agent:kiro, pi_agent:pi`

## 4. Semantic Rules

- `cmd` is a legacy compact-layout concept and is not accepted as a `[windows]`
  topology leaf.
- Each configured agent must appear exactly once in the layout.
- Built-in provider keys are currently `codex`, `claude`, `gemini`,
  `opencode`, `droid`, `agy`, `kimi`, `deepseek`, `dsh`, `mimo`, `qwen`,
  `qoder`, `qoderclicn`, `cursor`, `copilot`, `crush`, `kiro`, `pi`, `omp`,
  `zai`, and `grok`. The `deepseek` provider key launches the DeepSeek-oriented
  Deep Code CLI command `deepcode` by default. The separate `dsh` key starts
  the official DeepSeek Harness Web service with command `dsh`; CCB uses its
  structured loopback API, and any current pane is only a lifecycle/log
  carrier. The `mimo` key launches Xiaomi MiMo Code with command `mimo`;
  `qwen`, `cursor`, `copilot`,
  `crush`, `kiro`, and `pi` launch `qwen`, `agent`, `copilot`, `crush`,
  `kiro-cli`, and `pi` respectively; `zai` and `grok` launch `zai` and `grok`.
  Each command may be overridden by its provider start-command environment
  variable.
- The command pane remains a runtime anchor outside the `[windows]` agent leaf
  grammar.
- Compact config leaf order defines `default_agents`.
- Rich `ccb.config` formats may define agents separately, but must still provide a `layout` compatible with the same leaf rules.

### 4.1 Compact Header With Agent Overlay

`ccb.config` may combine a compact layout header with trailing TOML agent
overlays.

Example:

```toml
cmd, agent1:codex; agent2:claude

[agents.agent1]
key = "..."
url = "..."
```

Contract:

- The first compact block is the authority for:
  - `layout`
  - `default_agents`
  - `cmd_enabled`
  - agent `provider`
  - agent `workspace_mode`
- The trailing TOML overlay may define only `agents.<name>...` tables and the
  project-level `[maintenance.heartbeat]` table.
- Hybrid overlays must not redefine compact-header-owned agent fields such as:
  - `provider`
  - `workspace_mode`
- Hybrid overlays must not introduce agents that do not already exist in the
  compact layout header.
- Config rendering should prefer:
  - pure compact when no per-agent overlay is needed
  - compact header + TOML overlay when agent-local overrides are needed
  - expanding simple overlay cases into full-document TOML is not the preferred
    canonical output

### 4.1.1 Maintenance Heartbeat Config

Rich or hybrid `ccb.config` may define project-scoped maintenance heartbeat
policy:

```toml
[maintenance.heartbeat]
enabled = true
assessor = "ccb_self"
interval_s = 3600
min_interval_s = 300
unknown_streak_cap = 3
escalation_policy = "report_only"
startup_ensure = true
```

Contract:

- `[maintenance.heartbeat]` is CCB runtime policy, not agent layout authority.
- Maintenance heartbeat is opt-in. Startup must not start or ensure a
  heartbeat runner unless the effective config explicitly sets
  `maintenance.heartbeat.enabled = true`.
- The table is optional. Defaults are:
  - `enabled = false`
  - `assessor = "ccb_self"`
  - `interval_s = 3600`
  - `min_interval_s = 300`
  - `unknown_streak_cap = 3`
  - `escalation_policy = "report_only"`
  - `startup_ensure = true`
- `enabled` and `startup_ensure` are booleans.
- `assessor` is normalized with the configured-agent name grammar, but config
  loading does not require that the assessor is currently configured. Missing
  assessor state is surfaced by heartbeat status/diagnostics.
- `interval_s`, `min_interval_s`, and `unknown_streak_cap` are positive
  integers. `min_interval_s` must not exceed `interval_s`.
- v1 accepted escalation policies are `report_only` and `ask_user`, but the
  field is status-only in v1. Both values use the same bounded silent assessor
  activation path; the selected value is exposed in status/diagnostics and can
  be used by the assessor as advisory intent.
- `ccb maintenance status` may read and report this policy.
- `ccb maintenance tick` may use this policy to run a bounded one-shot
  diagnosis, write maintenance heartbeat status/schedule/activation state when
  enabled, and submit at most one silent ask to the configured assessor for
  non-healthy evidence.
- `ccb maintenance schedule --after <duration> [--reason <text>]` may write
  only the CCB-owned next heartbeat schedule. Requested delays shorter than
  `min_interval_s` are raised to `min_interval_s`.
- `ccb maintenance enable` and `disable` must not edit `.ccb/ccb.config` until
  config editing policy is defined before those commands mutate behavior.
- `ccb reload --dry-run` must classify a pure `[maintenance.heartbeat]` diff as
  `maintenance_change`, not `layout_change`.
- `ccb reload` may publish a `maintenance_change` by refreshing the project
  service graph and config signatures without tmux namespace mutation, runtime
  mount/unload, or agent pane restart.

### 4.1.2 Explicit Windows Topology

Rich TOML may declare named project windows through `[windows]`:

```toml
version = 2
entry_window = "main"

[windows]
main = "main:codex"
work = "worker1:codex(worktree), worker2:claude(worktree)"

[ui.sidebar]
mode = "every_window"
width = "15%"
bottom_height = 20
agents_height = "50%"
comms_height = "15%"
tips_height = "35%"
comms_limit = 5
comms_compact = true
tips_enabled = true
tips = [
  "C-b d  detach",
  "C-b h/j/k/l pane",
  "C-b H/J/K/L resize",
  "C-b o  next pane",
  "C-b z  zoom",
  "C-b w  tree",
  "C-b n/p next/prev",
  "C-b 0-9 jump win",
  "C-b [  copy mode",
  "copy: PgUp/PgDn",
  "copy: v select",
  "copy: y yank",
  "copy: q exit",
  "C-b ]  paste",
  "C-b c  new win",
  "C-b ,  rename",
  "C-b ?  keys",
]
```

Contract:

- legacy compact and hybrid configs that do not declare `[windows]` remain
  single-window configs; they are mounted in the project workspace window and
  keep their existing `cmd` pane semantics
- when a legacy layout has `cmd_enabled=true`, its normalized single-window
  topology and topology signature retain the `cmd` leaf, while that window's
  `agent_names` continues to contain only configured agents
- the default managed sidebar is outside that complete legacy user layout;
  sidebar, cmd, and agent leaves must materialize as distinct panes for both
  left and right sidebar positions
- topology consumers must resolve the unique managed cmd identity for the
  current project/session/window/namespace epoch; physical pane order and the
  first/root pane are not cmd authority
- `[windows]` is the authority for layout, default agent traversal, per-window
  agent grouping, provider selection, default workspace mode, and the effective
  configured-agent set.
- Each `[windows]` value uses the compact layout grammar, but `cmd` is not supported in windows topology.
- Every agent leaf in `[windows]` must declare a provider.
- Each configured agent is an agent leaf referenced by `[windows]` and must appear in exactly one window layout.
- On native Windows, provider-less `pwsh`, `powershell`, `bash`, and `wincmd`
  leaves are built-in interactive shell tool aliases. They create managed tool
  panes without coding-provider runtimes; `wincmd` is the spelling for
  `cmd.exe` because `cmd` remains reserved. These provider-less aliases fail
  config validation on WSL, Linux, and macOS. A provider suffix disambiguates
  the same text as an Agent name, so `bash:codex` remains an Agent rather than
  a shell tool pane.
- `[tool_windows.<name>]` may declare a managed non-agent tmux window. Tool
  windows are part of managed topology but not part of the configured agent
  set. The built-in supported file/workbench surface is the `rich` layout alias;
  legacy `neovim`, `nvim`, and `ccb-nvim` tool-window configurations are not
  supported.
- A tool window requires `command`, may set `label`, and defaults
  `show_in_sidebar = true`.
- `command` affects managed tmux topology and explicit reload planning.
  `label` and `show_in_sidebar` are project-view presentation fields; changing
  them must not recreate the tool pane or change the provider runtime set.
- A project-authored `tool_windows.<name>.command` is executable authority. It
  must have a matching user-state approval receipt before cold-start or reload
  materializes its pane. Built-in layout aliases are CCB-owned commands and do
  not require project-command approval.
- Tool window names use the same grammar as `[windows]` names and must not
  duplicate an agent window name.
- Tool windows do not declare providers, workspace modes, restore policies,
  model/API shortcuts, provider profiles, or agent overlays.
- Tool windows must not appear in `ccb ask` targets, provider runtime
  authority, dispatcher queues, completion tracking, Comms, or provider
  activity status.
- `entry_window` may reference either an agent window or a tool window.
- Windows topology must not be combined with legacy `default_agents`, `layout`, or `cmd_enabled` fields.
- `entry_window` is optional and defaults to the first declared window.
- Guarded reload may remove an agent window only through the `remove_agent`
  namespace patch path. If removing the window's last agent pane causes tmux to
  close that window before an explicit `kill-window`, the apply path must treat
  the window as already removed, record it in `namespace_removed_windows`, and
  preserve `namespace_removed_agents` / `namespace_removed_panes` diagnostics.
- Guarded reload may replace an existing agent only when the old and new
  `[windows]` topology keep the same agent in the same logical slot. A provider
  suffix or provider-spec change on that leaf is classified as `replace_agent`,
  not `layout_change`; apply reuses the managed pane as namespace evidence and
  respawns the agent runtime in that pane instead of reflowing or recreating
  unrelated panes.
- Guarded reload should reflow a window after successful append-only
  `add_agent` and idle `remove_agent` mutations, then reapply managed sidebar
  width. When a removal leaves a Window owned entirely by the static project
  config, reflow must realize its validated target binary topology, including
  horizontal/vertical direction, explicit `@N` hints, inline managed tools,
  and Sidebar position, while preserving pane identity. Windows containing
  dynamic Agent overlays and legacy topology records without a usable layout
  retain the fixed runtime order for one to six effective Agent panes:
  `p1,p3,p5` in the left column and `p2,p4,p6` in the right column. Windows
  outside either safe shape fall back to tmux even compaction.
  Reflow must preserve surviving pane identity and report
  `namespace_reflowed_windows` or `namespace_reflow_errors` in apply
  diagnostics.
- Runtime layout diagnostics may observe every tmux pane in a managed window,
  including sidebar or tool panes. Agent layout checks must count only panes
  whose CCB identity matches that window's configured/effective `agent_names`;
  `runtime_pane_count` is allowed to be larger than agent `pane_count`.
- `[ui.sidebar]` is valid only with windows topology. Defaults are `mode = "every_window"`, `width = "15%"`, `bottom_height = 20`, and `position = "left"`; `width` accepts either a positive integer column count or a percentage string.
- `position` accepts only `left` or `right`. `left` keeps the sidebar as the first horizontal pane before the user layout; `right` keeps the same vertical sidebar pane after the user layout. Bottom or horizontal sidebar placement is not part of this contract.
- In `mode = "every_window"`, CCB treats `width` as a project-wide sidebar width regardless of left/right position. Topology refreshes must resize every managed sidebar pane to the same configured share of its tmux window so page/window switches do not leave sidebars at different widths. If the user drags a sidebar border, CCB stores that runtime column width in the project tmux session and applies it to every managed sidebar window until the session is recreated. If tmux later resizes a window because a terminal client attaches or changes size, CCB reapplies the stored runtime width instead of treating the auto-resized pane width as a new user preference.
- Sidebar presentation fields live directly under `[ui.sidebar]`: `agents_height`, `comms_height`, `tips_height`, `comms_limit`, `comms_compact`, `tips_enabled`, and `tips`. They control only the sidebar pane's internal presentation and must not redefine managed windows, agents, pane ownership, provider runtime, or message/job authority.
- Sidebar presentation field changes are UI-only: they are delivered through `project_view` and must not force namespace topology recreation. `agents_height` controls the top Tree/Agent panel, `comms_height` controls the Comms panel, and `tips_height` controls the Tips panel; all three accept a positive integer row count or a percentage string. The default split is `50%`, `15%`, and `35%`.
- Legacy `[ui.sidebar.view]` remains accepted as a compatibility input, but config rendering must emit the single-table `[ui.sidebar]` form.
- If a hot-loaded sidebar presentation parse fails, `project_view.namespace.sidebar.view_error` reports the config error and the sidebar displays a `config ✕` warning while retaining the daemon's last valid view config.
- Agent leaves in `[windows]` provide canonical `provider` and default
  `workspace_mode` (`agent:provider` means `inplace`;
  `agent:provider(worktree)` means `git-worktree`). They may also include an
  `@N` split hint, for example `agent:provider@60` or
  `agent:provider(worktree)@40`.
- `[agents.<name>]` tables are overlays for names referenced by `[windows]`.
  Canonical user-authored overlays must not repeat topology-owned fields that
  are already expressible in the window leaf:
  - `provider`
  - `workspace_mode = "inplace"`
  - `workspace_mode = "git-worktree"`
- Legacy rich TOML that repeats `provider` or `workspace_mode` remains accepted
  when it does not conflict, but `ccb config validate` reports style warnings
  and CCB-generated config must not introduce those redundant fields.
- `workspace_mode = "copy"` remains an advanced overlay-only mode until the
  compact leaf grammar has a first-class copy-mode spelling.
- `[agents.<name>].role` may bind a configured agent to a reusable Role Pack
  such as `agentroles.archi`. The role id is stable package identity; the
  agent name remains the project-local ask target. Role ids must use
  publisher-qualified form such as `agentroles.archi` or `seemseam.archi`.
- Role binding is not topology authority by itself. An agent with a role still
  must be referenced by `[windows]` to become configured and mounted.
- Multiple configured agents may bind the same role id. This is the canonical
  way to run several project-local instances of one Role Pack with distinct
  names, providers, private memory, queues, panes, and provider homes.
- Role id shorthand in `[windows]`, for example `agentroles.archi:codex`, is a
  convenience for the role manifest's default agent name only. A second
  instance of the same role must use an explicit agent leaf plus
  `[agents.<name>].role`, or `ccb roles add <role-id>:<provider> --agent <name>`.
- `ccb ask <role-id> ...` is only an alias when exactly one configured agent is
  bound to that role. If multiple agents share the role, users must target the
  project-local agent name explicitly.
- Role assets are projected into managed provider homes as rebuildable role
  assets. Provider sessions, auth, runtime authority, mailbox state, and agent
  private memory must remain agent/project scoped.
- `[agents.<name>]` tables for names no longer referenced by `[windows]` are ignored as stale overlay residue and must not become configured agents or block startup.
  `ccb config validate` reports them as style warnings so accidental misspelled
  overlays are visible.

Example managed tool window:

```toml
[tool_windows.files]
command = "CCB_WORKBENCH_PROFILE=rich CCB_WORKBENCH_FORCE_RICH=1 ccb-workbench files"
label = "files"
```

The preferred built-in spelling is the compact `rich` layout alias inside
`[windows]`, for example `main = "agent1:codex, rich"`. tmux compatibility
settings for tool windows must remain project/session/window scoped.

### 4.2 Agent API Shortcut

For the common case where an agent only needs its own API key or base URL, rich
or hybrid `ccb.config` may use agent-local shortcut fields in the agent table:

```toml
[agents.agent1]
key = "..."
url = "..."
```

Contract:

- `key` and `url` are supported only for known API-backed providers with
  first-class
  mappings:
  - `codex`
  - `claude`
  - `gemini`
- `key` and `url` are the only canonical shortcut fields.
- `key/url` is user-facing sugar only. The loader must compile it to the existing
  provider-profile API env authority for that provider and force
  `provider_profile.inherit_api = false`.
- For Codex, compiling the `url` shortcut must normalize a bare origin such as
  `https://example.test` to the OpenAI-compatible API root
  `https://example.test/v1`.
- Explicit `key/url` authority must also suppress inherited provider state that
  would silently redefine that API authority.
  For all shortcut-backed providers, compiling `key/url` must also disable
  inherited auth projection so managed startup does not retain a second
  credential authority beside the explicit agent-local API route.
  For Codex, `key/url` disables inherited global `config.toml` routing
  projection, replaces it with an agent-local managed `config.toml`
  `model_provider` / `model_providers.<id>` authority derived from that
  explicit API route. That managed Codex route must use the standard custom
  provider shape with `requires_openai_auth = false`, and explicit base-url env
  exports must be suppressed so the managed `config.toml` remains the single
  route authority. An explicit `key` also disables inherited global `auth.json`
  credential projection.
- When `key` or `url` is present, provider API env must not also be declared in:
  - `agents.<name>.env`
  - `agents.<name>.provider_profile.env`
- Advanced API env not expressible as `key` or `url` remains a
  `provider_profile.env` concern. Do not invent a second runtime path for that
  advanced case.
- Legacy nested syntax under `agents.<name>.api` remains accepted for backward
  compatibility, but it is non-canonical.
- Config rendering and recovery must preserve the user-facing `key/url` shortcut
  instead of expanding it back into verbose provider-profile API env or nested
  `api` tables.
- Changing API authority for an existing managed agent is provider-startup
  configuration, not a hot-reload mutation. Config UI must save the active
  config, skip the non-dry-run reload for a `replace_agent` plan, and record a
  restart-required intent. The current provider process remains unchanged until
  the user next starts CCB; that foreground start must create a fresh provider
  process and managed provider home from the saved `key/url` or profile
  authority.

For Codex official-login users who do not have an API key, `key/url` is not
available. A project with multiple concurrent Codex agents must not copy one
file-backed ChatGPT `auth.json` into every managed home because refresh-token
rotation is a single serialized stream. Such projects may set:

```toml
[agents.agent1.provider_profile]
inherit_auth = false
```

and then log in that agent's managed Codex home directly. With no explicit
agent API authority, `inherit_auth = false` means "do not inherit global
Codex credentials"; it must preserve an existing agent-local `auth.json`.

### 4.3 Provider Profile MCP Overlay

Provider profiles may declare agent-local MCP server overrides:

```toml
[agents.agent1.provider_profile.mcp_servers.codegraph]
command = "/usr/local/bin/codegraph"
args = ["serve", "--mcp"]
```

Contract:

- The overlay is keyed by MCP server name.
- For Codex, CCB merges the overlay into the managed agent-local
  `config.toml` under `mcp_servers`. Same-name servers override inherited
  source config; different names are additive.
- The overlay is preserved in provider profile records and config rendering.
- This is source configuration, not provider runtime state. Running providers
  may need the normal CCB reload/restart flow before a changed MCP set is
  visible.
- Claude may still use provider-native `.claude.json` / `claude mcp` scoping
  for user-level MCP configuration; this overlay is not a replacement for
  provider-native Claude plugin management.

### 4.4 Agent Model Shortcut

For the common case where an agent only needs a provider model override, rich or
hybrid `ccb.config` may use an agent-local model shortcut:

```toml
[agents.agent1]
model = "gpt-5"
```

Contract:

- `model` is supported only for providers with first-class launch mappings:
  - `codex`
  - `claude`
  - `gemini`
  - `opencode`
  - `mimo`
  - `deepseek`
- `model` is user-facing sugar only. Providers with model flags compile it onto
  the existing startup-argument path. DeepSeek compiles it to
  `DEEPCODE_MODEL`, the Deep Code CLI's documented runtime override.
- `model` may coexist with unrelated `startup_args`, but must not be combined
  with provider model flags already present in `startup_args`.
- Config rendering and recovery must preserve the user-facing `model` field
  instead of expanding it into provider-specific `startup_args`.

### 4.5 Static Agent Thinking Shortcut

Static rich or hybrid agent overlays may select a provider-supported reasoning
level:

```toml
[agents.agent1]
model = "gpt-5.5"
thinking = "high"
```

Contract:

- `thinking` may accompany an explicit `model` or apply to the provider's
  inherited default model. The control panel requires an explicit model before
  offering model-specific levels; manual inherited-model use remains valid.
- Codex accepts the model-specific level exposed by the installed Codex model
  catalog and compiles it to `-c model_reasoning_effort="<level>"`.
- DeepSeek V4 Pro and V4 Flash accept `off`, `high`, or `max`. CCB compiles
  these to the Deep Code CLI's `DEEPCODE_THINKING_ENABLED` and
  `DEEPCODE_REASONING_EFFORT` environment overrides.
- Other providers do not have a static CCB thinking mapping. Their `thinking`
  field must fail validation instead of being ignored.
- Structured `thinking` must not be combined with an equivalent Codex
  `startup_args` config override or Deep Code environment override.
- Config rendering preserves `thinking` and removes generated provider launch
  arguments from user-facing TOML.

### 4.6 Provider Command Template

For provider-specific launch wrappers, rich or hybrid `ccb.config` may define an
agent-local command template:

```toml
[agents.agent1]
provider_command_template = "sandbox=1 {command} omx --madmax"
```

Contract:

- `provider_command_template` must contain exactly one `{command}` placeholder.
- CCB first builds the normal provider command segment, including managed
  provider arguments, agent `startup_args`, and provider resume flags.
- CCB then replaces `{command}` with that provider command segment.
- The template wraps only the provider command segment. CCB-managed env prefixes,
  managed provider homes, caller context exports, and shell setup stay outside
  the template and keep their original ordering.
- Providers must reject malformed templates during config loading rather than
  attempting partial fallback at startup.
- A project-authored `agents.<name>.provider_command_template` must have the
  same project-command approval as tool-window commands before provider launch.

### 4.6.1 Project Command Approval

Only explicit `tool_windows.<name>.command` and
`agents.<name>.provider_command_template` values in the project-local
`.ccb/ccb.config` cross this trust boundary. Configs without those fields,
user-level defaults, and CCB-owned layout aliases keep their existing behavior.

- The approval digest binds the canonical project root and the sorted exact
  field names and values; unrelated config edits do not invalidate it.
- Receipts live outside the repository under the user state root:
  `$XDG_STATE_HOME/ccb/trust/project-commands` (or
  `~/.local/state/ccb/trust/project-commands`) on Linux/macOS/WSL and
  `%LOCALAPPDATA%\CCB\trust\project-commands` on Windows.
- Interactive startup prints JSON-escaped field names and values and requires
  an explicit yes. `ccb config approve-commands` provides the same deliberate
  review path.
- Missing, invalid, stale, or changed approval fails closed in non-interactive
  startup, daemon bootstrap, reload, and immediately before the shell-backed
  execution sink. The diagnostic directs the user to
  `ccb config approve-commands`.
- `ccb -s` continues to control provider auto-permission only; it neither grants
  nor bypasses project-command approval.

### 4.7 Workspace Mode Semantics

- `workspace_mode = "inplace"` means the agent uses the project root directly.
- `workspace_mode = "git-worktree"` means the project root must be a valid git
  repository and startup must materialize a real `git worktree`.
- `git-worktree` must not silently fall back to copying the project directory
  when the project root is not a git repository. Startup must fail with an
  actionable error instead.
- `workspace_path` may be set on an agent with `workspace_mode =
  "git-worktree"` to point at an exact external worktree path. CCB validates the
  path but does not create, remove, prune, copy, or switch branches in that
  external workspace.
- `workspace_group` may be set on an agent with `workspace_mode =
  "git-worktree"` to share a CCB-managed internal worktree with other agents in
  the same group. Group worktrees live under `.ccb/workspaces/groups/<group>`
  and use branch `ccb/group/<group>`.
- CCB may write an untracked root `.ccb-workspace.json` binding into a managed
  worktree. Startup retirement excludes only that exact path, and only when it
  is a regular non-symlink schema-v2 `workspace_binding` whose project id,
  target project, workspace path, workspace mode, branch, and agent identity
  match current planner authority. Shared groups allow another current group
  member's non-empty agent identity.
- Every other tracked modification or untracked path remains dirty and blocks
  automatic retirement. Missing, malformed, foreign, symlinked, tracked, or
  authority-mismatched binding files are not cleanup exemptions.
- Automatic retirement requires the owned branch to be an actual ancestor of
  `HEAD`, rechecks dirtiness after removing the validated untracked binding,
  and uses non-force `git worktree remove`. Squash or patch equivalence does not
  authorize automatic branch deletion.
- `workspace_path` and `workspace_group` are mutually exclusive.
- `workspace_mode = "copy"` is the only mode that may create an explicit
  directory copy of the project tree.

### 4.8 Loop Capacity Role Profiles

Rich or hybrid `ccb.config` may define loop capacity policy under `[loop]`.
This is source policy for dynamic execution nodes, not a configured agent set
by itself.

Example:

```toml
[loop.capacity]
enabled = true
max_nodes = 4
default_lifetime = "current_round"
name_template = "loop-{loop_id}-{profile}-{index}"
reuse = "prefer_idle"

[loop.role_profiles.worker]
role = "agentroles.coder"
provider = "codex"
model = "gpt-5"
thinking = "high"
workspace_mode = "git-worktree"
workspace_group = "worker_pool"
max_instances = 2
reuse = "prefer_idle"

[loop.role_profiles.code_reviewer]
role = "agentroles.code_reviewer"
provider = "codex"
model = "gpt-5"
thinking = "medium"
workspace_mode = "git-worktree"
max_instances = 2
```

Contract:

- `[loop.capacity]` controls script-driven loop capacity policy. It accepts:
  - `enabled`
  - `max_nodes`
  - `default_lifetime`
  - `name_template`
  - `reuse`
- `[loop.role_profiles.<profile>]` declares named capacity profiles. It
  accepts:
  - `role`
  - `provider`
  - `model`
  - `thinking`
  - `workspace_mode`
  - `workspace_group`
  - `startup_args`
  - `provider_profile`
  - `max_instances`
  - `reuse`
- Profile names use the same normalized agent-name grammar as generated agent
  names.
- `role` must be an installed Role Pack id in publisher-qualified form such as
  `agentroles.coder`.
- `provider` follows normal CCB provider naming. If `model` is set, the
  provider must support the existing model shortcut path.
- `model` is preserved as a user-facing field and must not be combined with a
  provider model flag in `startup_args`.
- `thinking` is a provider-neutral source-policy field and currently accepts
  `low`, `medium`, or `high`. When a profile becomes a runtime agent, the same
  provider compiler used by static agents applies it; unsupported
  provider/model mappings fail visibly instead of being ignored.
- `workspace_group` requires `workspace_mode = "git-worktree"`.
- `provider_profile` follows the same source-configuration boundary as
  `[agents.<name>.provider_profile]`; provider sessions and auth remain
  agent/project scoped.
- `max_nodes` and each profile's `max_instances` must be positive integers, and
  `max_nodes` must not exceed the total profile capacity.
- `name_template` must include `{loop_id}`, `{profile}`, and `{index}`, and the
  rendered sample must be a valid agent name.
- `default_lifetime` accepts `current_round`, `current_loop`, or
  `manual_release`.
- `reuse` accepts `prefer_idle`, `always_new`, or `pinned`.
- Loop profiles do not mount agents by themselves. They become runtime agents
  only when a CCB-owned command such as `ccb loop capacity ensure` resolves
  them into generated or reused project-local agent instances.
- Config rendering must preserve `[loop.capacity]` and
  `[loop.role_profiles.*]` as source policy rather than expanding them into
  `[agents]` tables.
- Active loop capacity state is stored under the runtime-state root at
  `runtime/loops/<loop-id>/capacity.json` and merged into project config
  loading as a CCB-owned runtime overlay while the loop is `ensured`.
- `config validate`, startup, and guarded reload see active loop-generated
  agents from that overlay, but the user-authored `.ccb/ccb.config` file must
  not be rewritten by `ensure`, `status`, or `release`.
- `ccb loop capacity release --idle-only` marks loop-owned generated agents
  released; released states are ignored by config loading so generated agents
  do not become durable project intent.
- Reload or apply failure must roll back or leave recoverable diagnostics; it
  must not leave duplicate generated agents or silently convert loop capacity
  into user-authored config.

## 5. Default Layout Contract

Bootstrap must generate a balanced two-column layout over all visible panes.

For `cmd + N agents`:

- 1 agent: `cmd; agent1`
- 2 agents: `cmd; agent1, agent2`
- 3 agents: `cmd, agent1; agent2, agent3`
- 4 agents: `cmd, agent1; agent2, agent3, agent4`

General rule:

- split the full pane list into left and right halves
- stack each half vertically
- keep pane areas uniform by sizing each split according to descendant leaf counts

## 6. Tmux Layout Execution Contract

- The current pane is the `cmd` anchor pane.
- Layout execution must prune the configured layout to the requested foreground agent subset plus `cmd`.
- Layout execution must first build a normalized visible-layout plan from `parse -> prune -> render`, and that normalized render is the visible layout signature.
- Layout execution must preserve the relative structure of the configured layout after pruning.
- Recursive split percentages must use explicit leaf `@N` hints when present; otherwise they must be computed from leaf-count ratios, not hardcoded repeated `50%` splits.
- Pane pruning must never silently reorder agents.
- Incremental in-place splitting on top of an already materialized project namespace is not a valid way to realize a different visible layout signature.
- When the desired visible layout signature changes, startup must recreate the project namespace before rematerializing tmux panes.
- During layout materialization, newly split panes must be created with a silent placeholder command in the initial `split-window` call, not as empty panes that are later respawned. Once assigned to a provider leaf, panes must keep using the normal managed respawn path so provider shell, stderr-log, and `remain-on-exit` semantics are preserved.

## 7. Pane Presentation Contract

- `.ccb/ccb.config` logical leaf names are the only authority for pane display names.
- Pane titles must be the exact logical names:
  - `cmd`
  - `agent1`, `agent2`, ...
- Pane border labels must show the logical pane name, not tmux pane numbers.
- Provider-specific pane markers such as `CCB-agent1-...` are internal runtime evidence only:
  - they may be persisted in provider session files
  - they must not override pane titles, pane headers, or focus labels in the project namespace UI
- Tmux pane user options and visible titles must be reconciled back to the configured logical name whenever a project-owned pane is reused or rebound.
- The command pane and agent panes must have stable, distinct color identities.
- Pane styling is session-scoped CCB UI state and must not permanently overwrite unrelated user tmux themes.

## 8. Project Namespace UI Contract

- The project-owned tmux socket/session is responsible for its own theme and pane header rendering.
- Project UI correctness must not depend on whether the invoking shell is already inside some outer tmux server.
- Namespace creation or reuse must reapply session-scoped CCB tmux options on the project-owned socket.
- CCB-managed tmux sessions use vi copy/scroll mode by default (`mode-keys vi`) with a 50000-line scrollback history, including `v` to begin selection, `y` to copy and leave copy mode, and Vim-style pane navigation (`prefix+h/j/k/l`) plus pane resizing (`prefix+H/J/K/L`).
- When a project-owned pane dies and the daemon chooses namespace-level recovery, it must recreate and re-project the configured layout so each logical pane returns to its canonical position.
- Namespace `layout_version` is the compatibility key for visible pane topology and tmux UI presentation:
  - when the stored namespace layout version differs from the current code contract, the project namespace must be recreated
  - recreating the namespace is the preferred healing path for stale pane geometry or stale session-scoped UI options
- Namespace state must also track the current visible layout signature derived from `.ccb/ccb.config` after foreground pruning.
- If the stored visible layout signature differs from the desired visible layout signature for the current foreground start, the namespace must be recreated instead of trying to patch geometry in place.

## 9. Update Discipline

- If `.ccb/ccb.config` grammar changes, update this document in the same patch.
- If project memory file placement or generated memory path semantics change,
  update this document in the same patch.
- If bootstrap layout defaults change, update this document in the same patch.
- If pane naming, split sizing, or pane theming rules change materially, update this document in the same patch.
