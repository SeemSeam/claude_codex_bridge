# CCB Provider State Storage Boundary Plan

## 1. Purpose

This plan defines the code-level and layout-level boundary for storage under
`.ccb/`, especially provider-managed state under:

```text
.ccb/agents/<agent>/provider-state/<provider>/
.ccb/provider-profiles/
.ccb/shared-cache/
```

The original goal was classification before deletion. The current goal is to
keep that explicit storage model while retiring the project-scoped
Claude/Gemini cache safely, without touching conversation authority or
breaking `ccbd` / `ask` stability.

Superseding decision, 2026-07-23:

- CCB no longer creates or depends on project-scoped Claude/Gemini caches under
  `~/.cache/ccb/projects/<project-id-prefix>/provider-cache/`.
- Managed Claude panes use the user-installed executable and disable Claude's
  self-updater. CCB does not copy provider binaries into project cache.
- Managed Gemini panes use one user-scoped rebuildable cache under
  `~/.cache/ccb/provider-cache/gemini/`; cache identity is not project or
  session authority.
- Existing project-scoped caches are legacy residue. Startup detaches only
  recognized CCB-owned Claude cache links, while explicit stopped-project
  cleanup removes the current legacy cache. Cross-project orphan cleanup
  requires `ccb cleanup --legacy-provider-caches` and a valid manifest whose
  recorded project root no longer exists.

This plan complements:

- [docs/ccbd-startup-supervision-contract.md](/home/bfly/yunwei/ccb_source/docs/ccbd-startup-supervision-contract.md)
- [docs/ccb-config-layout-contract.md](/home/bfly/yunwei/ccb_source/docs/ccb-config-layout-contract.md)
- [docs/codex-session-isolation-contract.md](/home/bfly/yunwei/ccb_source/docs/codex-session-isolation-contract.md)
- [docs/claude-session-isolation-contract.md](/home/bfly/yunwei/ccb_source/docs/claude-session-isolation-contract.md)
- [docs/gemini-session-isolation-contract.md](/home/bfly/yunwei/ccb_source/docs/gemini-session-isolation-contract.md)
- [docs/claude-binary-cache-dedup-plan.md](/home/bfly/yunwei/ccb_source/docs/claude-binary-cache-dedup-plan.md)

The provider session isolation contracts remain authoritative for conversation,
auth, config, and restore semantics. This document narrows the storage boundary
problem: what belongs in project/agent authority, what is session evidence, and
what is merely rebuildable cache.

Online `ccbd` views are not allowed to depend on future storage cleanup to be
usable. ProjectView and sidebar-facing reads must use bounded tail reads,
targeted lookups, or a future materialized read model; full JSONL compaction is
a storage optimization, not a prerequisite for keeping the live UI responsive.

## 2. Current Findings

Observed local `.ccb` shape:

```text
.ccb/ccbd/                         small control-plane ledger
.ccb/agents/<agent>/runtime.json    agent runtime authority
.ccb/agents/<agent>/provider-state  managed provider home/state
.ccb/provider-profiles/             currently may contain profile runtime homes
```

The control-plane portion is not the storage problem. In the observed project,
`.ccb/ccbd` is small, while most disk usage comes from provider-managed homes:

- Claude version binaries under
  `.ccb/agents/<agent>/provider-state/claude/home/.local/share/claude/versions/`
- Codex session/log sqlite data and plugin projections under managed Codex homes
- Gemini npm/node-gyp cache under managed Gemini homes
- Codex runtime home data under `.ccb/provider-profiles/<agent>/codex/`

The current code paths also show structural ambiguity:

- `agents_dir` is rooted at `runtime_state_root`, but `provider_profiles_dir` is
  rooted at the anchor `.ccb`, so WSL relocation can split runtime state from
  provider profile state.
- provider profiles can become runtime homes for Codex when a profile has
  explicit env/home authority, which makes `.ccb/provider-profiles/` hold
  sessions, logs, plugins, and cache rather than only configuration templates.
- provider home materialization copies or projects inherited assets such as
  Codex plugins, Codex skills, Claude skills, Claude commands, and hook assets
  per managed home.
- managed provider `HOME` isolation is necessary for session correctness, but
  third-party tools also write binary/cache artifacts under that same `HOME`.
- diagnostics currently distinguishes secrets mostly by filename/path blacklist,
  not by a shared storage classification model.

## 3. Storage Classes

CCB must classify project storage before it attempts cleanup or deduplication.
Classification must be deterministic: one path has one primary class. If a path
matches multiple rules, the classifier must use this precedence:

```text
SECRET > SESSION > AUTHORITY > STARTUP_AUTHORITY_BUNDLE > RUNTIME_EPHEMERAL > WORKSPACE > USER_CONTENT > PROJECTED_CONFIG > REBUILDABLE_CACHE > RESIDUE > UNKNOWN
```

The classifier may attach secondary metadata such as `provider`, `agent`,
`active`, `reclaimable`, `unsafe_symlink`, or `reason`, but the primary class
must not drift between readers.

### 3.1 Authority

Authority defines current project/backend meaning. It must be preserved by
default and must not be pruned by cache cleanup.

Examples:

- `.ccb/ccb.config`
- `.ccb/ccbd/lifecycle.json`
- `.ccb/ccbd/lease.json`
- `.ccb/ccbd/keeper.json`
- `<runtime_state_root>/state/memory.seed.json`
- `.ccb/agents/<agent>/agent.json`
- `.ccb/agents/<agent>/runtime.json`
- `.ccb/agents/<agent>/helper.json`
- current mailbox summary records
- runtime relocation marker/ref files

### 3.2 Session Authority And Session Evidence

Session authority binds a managed provider process to a concrete provider
conversation namespace. It is provider-specific and may live inside an
agent-scoped managed home.

Examples:

- Codex managed `CODEX_HOME` and `CODEX_SESSION_ROOT`
- Codex active `sessions/` namespace for the agent
- Codex `.ccb-session-namespace.json` inside the managed home
- Claude managed `HOME`, `.claude/projects/`, and `.claude/session-env/`
- Claude `.claude/.claude.json` managed trust/account/MCP metadata authority
- Gemini managed `HOME`, `GEMINI_CLI_HOME`, and `GEMINI_ROOT`
- Gemini `<gemini_home>/.gemini/tmp/`
- project-scoped `.codex-<agent>-session`,
  `.claude-<agent>-session`, `.gemini-<agent>-session`

These files may be large, but they are not generic cache. They affect restore
and conversation continuity.

### 3.3 Runtime Ephemeral

Runtime ephemeral files support currently running helpers and panes. They may be
recreated by a fresh launch, but live processes can depend on them.

Examples:

- `.ccb/agents/<agent>/provider-runtime/<provider>/`
- `<runtime_state_root>/runtime/memory/<agent>.md`
- `project_root/.ccb/runtime/memory/<agent>.md` provider compatibility bridge
- bridge pids, FIFOs, runtime logs, session switch records
- managed Codex app-server socket, pid, remote-attachment marker, stdout, and
  stderr artifacts under the agent's provider-runtime directory
- a managed Codex app-server socket whose preferred path is unsafe may use the
  shared runtime-socket root with a provider-runtime-derived hashed name; it
  remains runtime-ephemeral and its exact effective path is recorded by the
  agent-local remote marker
- project sockets and heartbeat artifacts

Cleanup must only remove this class during explicit stop/reset/kill flows or
after ownership checks prove the process generation is dead. Explicit project
stop/kill removes the exact managed Codex app-server socket, pid, and remote
marker after terminating the owned processes, including when the bridge could
not finish its own graceful cleanup.

### 3.4 Startup Authority Bundle

Startup authority bundles are provider-specific files that are not conversation
identity but still affect launch semantics. They must be preserved atomically as
a unit.

Examples:

- Codex `.tmp/plugins/` plus `.tmp/plugins.sha` when present
- Codex source `.tmp/marketplaces/` and `plugins/cache/` are seed sources, not
  shareable managed startup bundles; their managed targets are per-agent
  writable state
- Claude source `.claude/plugins/` may be exposed as read-only seed authority
  through `CLAUDE_CODE_PLUGIN_SEED_DIR`; a new agent may bootstrap one local
  writable copy with plugin registry paths rebased into that local root before
  its first interactive process, but the source is not copied into shared cache
- provider startup projection manifests that must match their payload tree

Rules:

- cleanup must not treat these files as ordinary cache
- diagnostics may summarize them but must not split manifest and payload
- generated OpenCode `provider-state/opencode/opencode.json` is
  `PROJECTED_CONFIG`; project `opencode.json` remains user content outside the
  provider-state tree
- Qwen, Cursor, Copilot, Crush, Kiro, Pi, and Z.ai use shared native CLI
  provider-state roots with `<provider>_home` and `<provider>_data_dir`;
  Qwen's marker-owned `extensions/` seed and Copilot's marker-owned
  `config.json.installedPlugins` entries plus installed plugin trees are
  `PROJECTED_CONFIG`, while their remaining contents are provider-owned
  session/auth/cache evidence rather than project worktree content
- Droid uses
  `.ccb/agents/<agent>/provider-state/droid/home/` as its private OS home and
  the `.factory/` child as its Factory state root; auth, sessions, plugins, and
  settings must not escape that pair
- sharing is allowed only after content-addressed whole-bundle storage and
  atomic replacement are implemented
- default behavior remains per-agent/per-home storage

### 3.5 Rebuildable Cache

Rebuildable cache does not define project authority or provider conversation
identity. It can be shared, pruned, or regenerated.

Examples:

- Claude `.local/share/claude/versions/`
- Claude `.local/bin/claude` shim/symlink when it only points to a version cache
- Gemini `.npm/_cacache/`
- Gemini `.cache/node-gyp/`
- Gemini `.cache/vscode-ripgrep/`
- provider package manager caches that do not include session/auth state
- Copilot's agent-local `<provider-state>/copilot/data/cache/` selected by
  `COPILOT_CACHE_HOME`

This class is the primary target for storage optimization. Rebuildable cache
records must include enough metadata for safe decisions, such as
`reachable_from_current_symlink`, `is_active_version`, or `reclaimable=false`
for active tool versions.

### 3.6 Projected Config And Inherited Assets

Projected config is copied, synthesized, or routed into managed homes to make
isolated provider startup work. Immutable inherited assets may use a symlink
or a content-addressed shared-cache route when the target is confirmed to be a
CCB-managed projection. If symlinks are unavailable, startup may fall back to a
marked copy.

Authentication and login-state projection is stricter than ordinary immutable
asset projection. It follows
[docs/provider-auth-inheritance-contract.md](/home/bfly/yunwei/ccb_source/docs/provider-auth-inheritance-contract.md):
credential and account paths are one-way ordinary copies into the managed home
and must never be symlinks, junctions, mounts, hard links, or reverse-synced
aliases to user state.

Examples:

- Codex `config.toml`
- Codex inherited `skills/` and `commands/`
- Qoder and Qoder CLI CN `skills/` under the effective managed
  `--config-dir`
- Claude `.claude/settings.json`
- Claude `.claude/skills/`, `.claude/commands/`, `.claude/CLAUDE.md`
- Claude `.claude/plugins/` as the agent-local writable plugin root selected by
  `CLAUDE_CODE_PLUGIN_CACHE_DIR`
- Droid inherited `skills/`, marker-owned local `plugins/`, and only the
  marker-owned `enabledPlugins` entries merged into managed `settings.json`
- Gemini `.gemini/settings.json`, `.gemini/trustedFolders.json`, and
  marker-owned local `.gemini/extensions/`
- Qwen marker-owned local `extensions/`
- Copilot's allowlisted `config.json.installedPlugins` metadata, aggregate
  ownership marker, per-tree markers, and agent-local `installed-plugins/`
  copies
- Kimi inherited and role `skills/` directories under managed provider state
- OpenCode generated `opencode.json` and generated ask skill instruction files
  under `.ccb/runtime/skills/<agent>/opencode/`

Auth, OAuth, token, and credential files are never `PROJECTED_CONFIG` even when
they were created by a projection step. They must classify as `SECRET`.
Provider sessions, auth, memory files, provider-runtime FIFO/completion
artifacts, `.claude/projects/`, and `.gemini/tmp/` must not be routed through
shared-cache.

Projected-tree replacement and cleanup require a valid local regular-file
marker with schema version 1, record type `ccb_projected_asset`, the exact
consumer label, a non-empty source, and a recognized projection mode. An
unmarked directory remains user-owned even when its content matches the
current source. A foreign, malformed, wrong-label, or symlinked marker blocks
projection and cleanup. The only markerless migration may write a marker next
to an existing symlink that already resolves exactly to the current source;
it must not replace that symlink. The compatibility
`allow_unmarked_replace` keyword grants no ownership authority.

### 3.7 Secret

Secret material must not be exported in diagnostics and must not be moved to a
shared cache.

Examples:

- provider auth files
- Claude `.claude/.claude.json`, because inherited MCP server definitions may include
  environment variables or other auth-adjacent launch material even though the
  file also contains managed workspace trust authority
- Codex `auth.json`
- Codex auth sidecars such as `company-codex-api-key`,
  `company-codex.config.toml`, and `.ccb-auth-projection.json`
- Claude `.claude/.credentials.json`
- Claude `.config/claude-code/auth.json`
- Gemini `.gemini/oauth_creds.json`
- Gemini `.gemini/google_accounts.json`
- Gemini `.gemini/gemini-credentials.json` and provider OAuth token files
- Droid `.factory/auth.v2.file`, `.factory/auth.v2.key`, and alternate auth
  records
- Cursor platform-specific `auth.json`
- OpenCode `auth.json` and `account.json`
- Kiro's filtered `data.sqlite3`, because it still contains auth rows
- auth-bearing mixed records such as DeepSeek `settings.json`, Kimi
  `config.toml`, Crush `providers.json`, and Z.ai `user-settings.json`
- API key material
- OAuth credential files
- macOS Keychain-derived Claude credentials
- `.env` files containing provider credentials
- Copilot `config.json`, because installed-plugin metadata shares that file
  with authentication and application state, plus Copilot `mcp-secrets/` and
  `mcp-oauth-config/`

Secrets may still live inside managed provider homes, but storage tooling must
handle them through explicit allow/deny classifications.

### 3.8 Workspace

Workspace data is user-visible working-copy state. It may contain uncommitted
changes produced by an agent or by the user and must not be treated as stale
residue.

Examples:

- `.ccb/workspaces/<agent>/`
- git-worktree materializations owned by an agent workspace binding
- copy-mode working directories for non-inplace agents

Cleanup must not remove this class. Git-worktree teardown requires explicit
workspace lifecycle handling, not provider cache cleanup.

### 3.9 User Content

User content is project-local material created to aid handoff, continuation, or
operator workflow. It is not provider conversation state, but it is also not
cache.

Examples:

- `.ccb/history/` handoff/context-transfer documents
- `.ccb/ccb_memory.md` project shared memory, when present under the project anchor
- `.ccb/agents/<agent>/memory.md` agent-private memory anchored under the
  project `.ccb/` directory
- user-authored notes under the project anchor

Cleanup must preserve this class unless a future explicit user-content command
is introduced.

### 3.10 Residue

Residue is old evidence that may guide recovery or diagnostics but must not
redefine current project authority.

Examples:

- unknown `.ccb/agents/<unknown-agent>/` directories
- provider-base session files not scoped to a configured agent
- old provider homes after profile changes
- archived Codex sessions from provider authority rotation

Residue cleanup should be opt-in or tied to explicit reset flows.

## 4. Target Layout

The target layout separates authority, managed session state, and rebuildable
cache.

```text
.ccb/
  ccb.config
  ccbd/
    ...
  agents/
    <agent>/
      agent.json
      runtime.json
      helper.json
      provider-runtime/
        <provider>/
      provider-state/
        <provider>/
          home/
            provider session/config/auth authority
  provider-profiles/
    <profile-or-agent>/
      <provider>/
        profile template/config only
  history/
    user handoff/context-transfer artifacts
  workspaces/
    <agent>/
      agent working copy state
  shared-cache/
    codex/
      startup-bundles/
        content-addressed-only/
```

User-scoped rebuildable cache outside project/runtime authority:

```text
~/.cache/ccb/provider-cache/
  gemini/
    MANIFEST.json
    npm/
    xdg/
```

Rules:

- `.ccb/agents/<agent>/provider-state/<provider>/home` remains the default
  managed session boundary.
- Managed Grok may copy inherited system `.grok/auth.json` and `.grok/config.toml`
  into `.ccb/agents/<agent>/provider-state/grok/home/.grok/` when profile
  inheritance is enabled. This is credential/config projection only; Grok
  sessions, active-session state, logs, and runtime output remain agent-scoped
  under the managed home.
- Managed Copilot extracts only validated `installedPlugins` entries from the
  source `config.json`, rebases each accepted `cache_path`, and copies the exact
  corresponding source tree into the agent-local `installed-plugins/` root.
  Authentication, settings, permissions, sessions, plugin data, MCP secrets,
  and marketplace cache remain outside projection authority.
- `.ccb/provider-profiles/` must not silently become a long-lived runtime home
  unless the user explicitly configures that path as an external provider home.
- `.ccb/shared-cache/` contains only rebuildable cache and never conversation
  sessions, mailbox data, runtime authority, auth secrets, or trust authority.
- New Claude/Gemini startup paths must not create provider cache under
  `.ccb/shared-cache/` or `~/.cache/ccb/projects/`.
- Codex startup bundles may use shared cache only after content-addressed
  whole-bundle storage and atomic replacement exist; default remains per-agent.
- If runtime state is relocated on WSL-mounted filesystems, profile/runtime
  state that affects startup should follow the same effective runtime state root
  unless the user explicitly opts into an external path.
- macOS Keychain-derived credentials must remain agent-scoped and must not be
  shared. Converted Gemini, Cursor, and Droid credentials live only in managed
  homes. Managed Claude must not copy
  `com.apple.security.plist` or link its `Library/Keychains` path to the user's
  Keychain; its only writable Keychain target is an agent-derived namespaced
  service. Recognized legacy links/preferences are detached or removed without
  traversing the source.

## 5. Code-Level Changes

### Phase A - Storage Classification API

Add a read-only classifier before cleanup or migration.

Suggested module:

```text
lib/storage_classification/
```

Suggested model:

```text
StorageClass.AUTHORITY
StorageClass.SESSION
StorageClass.RUNTIME_EPHEMERAL
StorageClass.STARTUP_AUTHORITY_BUNDLE
StorageClass.WORKSPACE
StorageClass.USER_CONTENT
StorageClass.REBUILDABLE_CACHE
StorageClass.PROJECTED_CONFIG
StorageClass.SECRET
StorageClass.RESIDUE
StorageClass.UNKNOWN
```

Required behavior:

- classify paths under `.ccb/ccbd`
- classify paths under `.ccb/agents/<agent>`
- classify provider-state subtrees by provider-specific rules
- classify `.ccb/provider-profiles`
- calculate byte totals by class/provider/agent
- return a single primary class per path using the precedence in Section 3
- attach active/cache metadata, including
  `reachable_from_current_symlink` and `is_active_version` when available
- detect symlink loops and out-of-bound symlinks as `UNKNOWN` with a reason
- emit a versioned JSON schema for `doctor storage --json`
- never delete files in this phase

Suggested command surface:

```text
ccb doctor storage
ccb doctor storage --json
```

Exit criteria:

- users can see disk usage by class, provider, and agent
- diagnostics can report cache vs authority without exporting large binaries
- malformed or unknown paths are reported as `UNKNOWN` or `RESIDUE`, not ignored
- Codex session namespace markers, Claude `.claude/.claude.json`, and Gemini
  `.gemini/tmp/` do not classify as `UNKNOWN`; Claude
  `.claude/.claude.json` uses the
  `SECRET` primary class because it may contain inherited MCP launch env

### Phase A.5 - Provider Profile Runtime-Home Migration

Before changing provider-profile semantics, migrate existing profile-backed
runtime homes safely.

Required behavior:

- detect `.ccb/provider-profiles/<agent>/codex/` trees that already contain
  sessions, auth, logs, plugin bundles, or other runtime-home data
- move or copy that runtime-home data into
  `.ccb/agents/<agent>/provider-state/codex/home/` only when the target is
  absent or compatible
- update persisted project session fields such as `codex_home`,
  `codex_session_root`, and bound session paths
- if legacy session material exists, validate the persisted Codex session
  authority before moving files; missing, malformed, or non-matching authority
  must abort migration and leave the legacy tree in place
- perform migration before current config/auth/plugin projection, discard any
  migrated plugin projection, then refresh projection from the active
  profile/source home so stale legacy auth or plugin bundles cannot override
  `inherit_auth` or mix with the current plugin bundle
- startup must support one upgrade cycle where it can read the old profile path,
  migrate/fallback safely, and rewrite authority to the new managed home
- never migrate secrets into shared cache
- reject legacy profile runtime homes that contain symlinks instead of partially
  moving data and rewriting authority
- do not migrate while the owning provider runtime is active; non-terminal
  agent runtime authority with a live `pid`/`runtime_pid`, or transitional
  `starting`/`busy`/`stopping` state without usable pid evidence, must leave the
  legacy tree untouched

Exit criteria:

- existing Codex conversations survive the provider-profile boundary change
- old profile-backed homes are classified as `RESIDUE` only after authority has
  been rewritten
- restore does not silently fall back to a fresh bootstrap because a session
  root moved
- after migration, non-explicit Codex profiles resolve to
  `.ccb/agents/<agent>/provider-state/codex/home/`; only explicit
  `provider_profile.home` remains a profile-backed runtime home

### Phase B - Path Boundary Cleanup In Code

Make path ownership explicit.

Required changes:

- introduce `provider_profile_root` and `provider_runtime_home` as separate
  concepts in models and path helpers
- prevent default profile materialization from creating session/log/cache data
  under `.ccb/provider-profiles`
- require explicit config for any profile-backed `runtime_home`; the initial
  allowed explicit runtime-home path is Codex only
- keep Claude and Gemini on managed agent-scoped homes until they have matching
  route/fingerprint rotation semantics for external runtime homes
- validate effective provider homes for all configured agents and hard fail on
  duplicate homes for the same provider
- decide whether default `provider_profiles_dir` should live under
  `runtime_state_root` when runtime state is relocated
- record path class in diagnostics and startup reports where useful

Exit criteria:

- provider profile records are configuration templates by default
- runtime homes are agent-scoped unless explicitly and safely overridden
- duplicate effective `<provider>_home` values fail startup before launch
- WSL relocation no longer leaves part of runtime-critical provider state on
  unsupported project-mounted storage

### Phase C - Provider Cache Cleanup

Implement conservative cache-specific cleanup behind one command surface:
`ccb cleanup`. `ccb doctor storage` remains the inspection path; cleanup itself
does not add dry-run or provider-specific CLI modes.

Required behavior:

- Claude: inspect `.local/share/claude/versions` and current symlink target
- Gemini: inspect `.npm/_cacache`, `.cache/node-gyp`, `.cache/vscode-ripgrep`
- Codex: inspect `.tmp/plugins` and `.tmp/plugins.sha` only as
  `STARTUP_AUTHORITY_BUNDLE`, not as reclaimable cache
- report deleted bytes and skipped paths after cleanup
- refuse unsafe symlink traversal
- hold the same project startup/lifecycle lock used by `ccbd` startup while
  checking state and pruning, so cleanup cannot race a concurrent backend start
- never touch session roots or auth files
- refuse to run while `ccbd` is active or pending/running `ask` jobs exist
- treat malformed or unreadable job JSONL as unknown pending work and refuse
  cleanup rather than crashing or pruning blindly
- detach only Claude symlinks that resolve exactly to a recognized legacy CCB
  project/shared cache before removing that cache
- remove the stopped current project's legacy Claude/Gemini cache directories
- scan other project buckets only with explicit
  `--legacy-provider-caches`; require a valid provider manifest, verify its
  project id against the recorded absolute project root, and delete only when
  that project root no longer exists
- after a successful project `ccb kill`, retry removal of that project's
  retired Claude/Gemini cache without an extra byte-size traversal

Suggested command surface:

```text
ccb cleanup
```

Exit criteria:

- `ccb doctor storage` explains cache vs. authority before cleanup
- repeated cleanup is stable and idempotent
- provider startup and ask completion semantics are unchanged
- `ccb cleanup` deletes only safe rebuildable cache and preserves authority,
  sessions, secrets, and startup authority bundles

### Phase D - Retire Project-Scoped Provider Cache

The earlier Phase D project-scoped Claude/Gemini shared-cache direction is
superseded. It reduced duplication only within one project, accumulated
unbounded orphan buckets across deleted projects, and did not improve the warm
startup path because reusable bindings skip provider preparation.

Required behavior:

- managed Claude startup must not create, copy, hash, or select binaries under
  a CCB project cache
- managed Claude startup must disable provider self-update and detach
  recognized legacy CCB cache symlinks without deleting cache payload during
  startup
- managed Gemini startup may share rebuildable npm/XDG cache only at the user
  scope, never at project or agent scope
- user-scoped Gemini cache resolution must not recursively nest when a managed
  Gemini process invokes CCB with its own `XDG_CACHE_HOME`
- provider sessions, auth, config, trust, and runtime homes remain agent scoped
- project-scoped legacy cleanup must remain outside normal startup and job
  delivery; a newly installed post-update runner may perform bounded migration
  with a user-level lock, project lifecycle gates, and conservative deferral

Exit criteria:

- a new Claude/Gemini project creates no
  `~/.cache/ccb/projects/<id>/provider-cache`
- two projects use the same user-scoped Gemini rebuildable cache
- Claude executes from the user/provider installation managed by `ccb update`
- Linux, macOS, and WSL managed launches preserve session isolation

### Phase E - JSONL Retention And Compaction

Control-plane JSONL growth is not the urgent disk issue, but it needs a future
policy.

Required behavior:

- keep append-only semantics for current authority/event readers
- add optional compaction snapshots for old terminal jobs/messages
- preserve enough history for diagnostics and support bundles
- never compact active/running/accepted jobs

Exit criteria:

- long-running projects do not grow unbounded event ledgers
- `pend`, `queue`, `inbox`, `watch`, `doctor`, and restore paths keep their
  current semantics

## 6. Provider-Specific Boundaries

### 6.1 Codex

Must remain agent-isolated:

- `CODEX_HOME`
- `CODEX_SESSION_ROOT`
- active `sessions/`
- explicit provider authority marker
- `.ccb-session-namespace.json`
- project-scoped `.codex-<agent>-session`
- `.tmp/plugins.sha` as the managed-home startup authority marker

Must remain secret and agent-local:

- `auth.json`
- auth sidecars copied from the source Codex home, including
  `company-codex-api-key`, `company-codex.config.toml`, and the
  `.ccb-auth-projection.json` evidence manifest

May route through projected assets or shared-cache:

- inherited `skills/` and `commands/`; startup should prefer symlinks to the
  source home and fall back to marked copies
- Kimi inherited skill roots; startup routes them as projected assets and passes
  them with `--skills-dir`; an unmarked conflicting managed-state directory is
  preserved and omitted from the active managed roots
- `.tmp/plugins/`; the real bundle may live under
  `.ccb/shared-cache/codex/plugin-bundles/<sha>/`, with managed homes pointing
  at that bundle and retaining their local `.tmp/plugins.sha`

Must remain writable and agent-local:

- `.tmp/marketplaces/`
- `plugins/cache/`

The source versions of those two paths may seed a staged local copy. They must
not be linked to the source home, shared between agents, or used to justify
replacement of an unmarked target.

Rebuildable diagnostic storage may route to owner-controlled temporary state:

- managed `logs_2.sqlite` may be an agent-scoped symlink to the CCB temporary
  log root, keyed by managed Codex home and provider runtime directory
- the default pressure filter drops ordinary diagnostic rows but preserves
  exact `codex_core::session::turn` rows containing `Turn error:`; these rare
  rows are bounded failure evidence for reconnect correlation, not session
  authority or permission to restore general diagnostic logging

Do not share:

- active sessions
- provider authority markers
- auth files
- `.tmp/plugins.sha`
- per-agent conversation logs

### 6.2 Claude

Must remain agent-isolated:

- managed `HOME`
- `.claude/projects/`
- `.claude/session-env/`
- `.claude/settings.json`
- `.claude/.claude.json`
- `.claude/plugins/`, including its `marketplaces/` and `cache/` children

Must remain secret and agent-local:

- `.claude/.claude.json`
- `.claude/.credentials.json`
- `.config/claude-code/auth.json`

Legacy rebuildable cache:

- `.local/share/claude/versions/` and `.local/bin/claude` inside managed homes
- retired project cache under
  `~/.cache/ccb/projects/<project-id-prefix>/provider-cache/claude/`
- rebuildable Claude residue under `.cache/claude`, `.npm/_logs`,
  `.claude/cache`, `.claude/telemetry`, and `.claude/paste-cache`

Claude plugin source authority may be shared only through the provider's
read-only `CLAUDE_CODE_PLUGIN_SEED_DIR` contract. The misleadingly named
`CLAUDE_CODE_PLUGIN_CACHE_DIR` points at the full writable plugins root, not its
`cache/` child, and must resolve to a different managed path for every agent.
Do not route managed `.claude/plugins/marketplaces` or `.claude/plugins/cache`
through CCB shared cache without a future provider-supported ownership design.

New managed startup does not generate these binary caches. It uses the
user-installed Claude executable, disables self-update in managed panes, and
leaves session/auth isolation in the private managed `HOME`.

The Claude-specific implementation details live in
[docs/claude-binary-cache-dedup-plan.md](/home/bfly/yunwei/ccb_source/docs/claude-binary-cache-dedup-plan.md).

### 6.3 Gemini

Must remain agent-isolated:

- managed `HOME`
- `GEMINI_CLI_HOME`
- `GEMINI_ROOT`
- `<gemini_home>/.gemini/tmp/`
- `.gemini/settings.json`
- `.gemini/trustedFolders.json`

Must remain secret and agent-local:

- `.gemini/oauth_creds.json`
- `.gemini/google_accounts.json`
- `.gemini/gemini-credentials.json`
- `.gemini/mcp-oauth-tokens.json`
- `.gemini/a2a-oauth-tokens.json`

Managed Gemini must force both supported file-storage modes. An existing
external `gemini-cli-oauth` entry may be read only to seed a private managed
file; the managed process must never select, update, or delete that external
credential.

User-scoped rebuildable cache:

- `NPM_CONFIG_CACHE` and `npm_config_cache` routed to
  `~/.cache/ccb/provider-cache/gemini/npm`
- `XDG_CACHE_HOME` routed to
  `~/.cache/ccb/provider-cache/gemini/xdg`

These routes must not change `HOME`, `GEMINI_CLI_HOME`, `GEMINI_ROOT`, auth, or
session identity. The retired per-project route is legacy residue only.

### 6.4 Install-Wide Provider Update Metadata

`ccb update` may persist non-secret, user-wide provider update metadata at:

```text
$XDG_STATE_HOME/ccb/provider-updates.json
```

with `~/.local/state/ccb/provider-updates.json` as the fallback. This file is
not provider session authority and must never live inside an agent-managed
provider home. It may contain provider id, resolved executable path, installed
and available versions, package owner, exact muted version, last decision, and
last verified update result. It must not contain credentials, provider
configuration payloads, session ids, prompts, or conversation content.

The update lock is user-wide and adjacent to this state. Provider pane startup
must neither read the state as launch authority nor perform registry checks;
only the explicit CCB update flow owns discovery and mutation.

### 6.4 Qoder

Qoder must use its documented `--config-dir` option to bind the managed visible
TUI to the exact agent-local provider-state root. CCB ask execution must reuse
that owned visible pane; it must not create a hidden per-job print subprocess.
The unsupported `QODER_HOME` assumption is not an isolation boundary.

Must remain agent-isolated:

- the exact `--config-dir` root
- `.auth/`, logs, sessions, settings, installation identity, and security state
- the visible provider conversation and its pane/session binding

Must remain secret and agent-local:

- all `.auth/` descendants
- any future credential or token records introduced under the config root

Candidates for rebuildable cache:

- `.cache/` descendants such as endpoint and DNS caches

An explicit user-provided `--config-dir` overrides CCB's managed root and must
be preserved without adding a second option. CCB diagnostics may report that
external path but must not copy, classify, clean, or inspect its credentials.

## 7. WSL And macOS Requirements

WSL:

- project anchors on `/mnt/<drive>` must avoid placing Unix sockets or
  runtime-critical mutable state on unsupported filesystems
- provider profile/runtime state must not be split across anchor and relocated
  root in a way that breaks startup or cleanup
- user-scoped Gemini cache lives in the Linux/WSL user cache and must not be
  placed on the project-mounted drive merely because the project anchor is
  under drvfs
- Claude/Gemini startup does not require project shared-cache availability
- shared-cache disabled reason codes are currently limited to:
  - `wsl_drvfs_requires_runtime_relocation`: the anchor is on drvfs without a
    usable relocated runtime-state root; shared cache root must be reported as
    unavailable

macOS:

- Claude Keychain-derived credentials must remain per managed home; the
  writable secure-storage record is an agent-derived namespaced service, never
  an ordinary source service
- managed Claude must remove a recognized legacy `Library/Keychains` link
  without following it and must not create a replacement
- Cursor and Gemini Keychain entries are read-only inheritance sources whose
  converted files remain inside the managed home
- Kiro must fail closed while the installed CLI cannot select a private
  credential backend
- shared binary/cache logic must not move or export Keychain-derived auth files
- cleanup must handle symlink metadata conservatively

Linux:

- default behavior should continue to use project-local `.ccb` when safe
- user-level cache is acceptable only for rebuildable cache, not authority

## 8. Diagnostics Contract Changes

Diagnostics should stop treating provider-state as an undifferentiated tree.

Required changes:

- `doctor` should surface storage class totals
- diagnostics bundle should include cache manifests but not large cache payloads
- secret filtering should use storage classification plus provider-specific
  rules, not only filename blacklists
- unknown provider-state paths should be visible as unknown/residue
- the classifier must return one primary class per path; conflict precedence is
  defined in Section 3
- cache summaries should include active/reclaimable metadata and a reason when
  a path is not safe to prune
- `ccb doctor storage` must separately report the stopped current project's
  retired external provider-cache root/presence/bytes and the new user-scoped
  provider-cache root/presence. The user-scoped cache size is not recursively
  scanned in the normal project diagnostic path because it is shared and may
  contain a large npm tree. These values are outside the `.ccb` `total_bytes`
  classification total.

Bundle export rule:

- authority and small session evidence may be copied according to existing
  diagnostics policy
- rebuildable cache should be summarized by path, size, provider, and class
- startup authority bundles should be summarized as a unit and not split across
  manifest/payload files
- secrets must never be copied
- diagnostic bundles must include `generated/storage-summary.json` so skipped
  cache/startup-bundle payloads remain visible as summaries

## 9. Tests

Required unit tests:

- classify `.ccb/ccbd` authority vs events
- classify agent runtime files as authority/runtime
- classify Codex session roots as session authority
- classify Codex `.ccb-session-namespace.json` as session authority
- classify Codex plugin projection plus sha as `STARTUP_AUTHORITY_BUNDLE`
- classify Claude versions as rebuildable cache and active version separately
- classify Claude `.claude/.claude.json` as managed trust/session authority with
  `SECRET` as the primary storage class
- classify Gemini npm/node-gyp cache as rebuildable cache
- classify Gemini `.gemini/tmp/` as session
- classify Copilot `config.json` as secret mixed state, installed plugin
  metadata/trees as projected config, plugin data as session state, and
  `data/cache/` as rebuildable cache
- classify provider auth files as secret
- provider profile default path does not become runtime home
- explicit Codex provider profile home remains allowed and visible as explicit
- duplicate effective provider homes fail validation
- cleanup refuses to run while ccbd or ask jobs are active
- unsafe symlinks classify as `UNKNOWN` with a reason

Required integration tests:

- Linux storage audit on a multi-provider project
- WSL relocated runtime-state project storage audit
- WSL drvfs project without relocation reports shared-cache disabled
- macOS Claude storage audit with Keychain-compatible managed home
- `ccb ask` still completes after storage audit
- `ccbd` restart still restores managed provider sessions
- cleanup preserves provider sessions/auth and is idempotent
- provider-profile Codex migration preserves bound session authority

Required real tests:

- multi-agent Codex ask/reply loop
- Claude managed launch with version cache present
- Gemini managed launch with npm cache present
- two same-provider agents remain session-isolated after cache inspection
- WSL project on mounted drive starts and asks successfully
- macOS project starts and asks successfully

## 10. Recommended Implementation Order

1. Implement Phase A storage classification and `doctor storage --json`.
2. Add diagnostics bundle cache summaries and secret-safe classification.
3. Implement Phase A.5 provider-profile Codex runtime-home migration.
4. Fix provider profile/runtime-home path boundary so default profiles do not
   accumulate runtime sessions/log/cache.
5. Implement `ccb cleanup` as the single conservative provider-cache cleanup
   command.
6. Keep `ccb doctor storage` as the only cleanup preview/audit surface.
7. Evaluate Codex startup-bundle sharing only after content-addressed
   whole-bundle atomic replacement exists.
8. Retire project-scoped Claude/Gemini cache generation, migrate recognized
   legacy links conservatively, and run bounded cleanup from the newly
   installed post-update runner with active projects deferred to `ccb kill`.
9. Keep only the user-scoped Gemini npm/XDG cache after Linux/macOS/WSL real
   launch verification.
10. Add JSONL retention/compaction after provider-state size is under control.

## 11. Non-Goals

- Do not weaken managed provider session isolation.
- Do not make `.ccb/agents/<agent>/provider-state` globally shared.
- Do not delete cache as part of `doctor`.
- Do not treat unknown files as safe to remove.
- Do not share auth, trust, session roots, mailbox records, runtime authority,
  or active provider logs.
- Do not make cleanup part of normal `ask` critical path.
- Do not stop active projects for update-time cleanup or make cleanup failure
  fail an otherwise successful core update.
- Do not run destructive `ccb cleanup` concurrently with an active backend:
  cleanup must acquire the project startup/lifecycle guard, confirm `ccbd` is
  stopped inside that guard, and refuse to prune while pending `ask` jobs exist.

## 12. First Concrete Slice

The first implementation slice should be read-only:

- add storage classification models
- add provider-specific classifiers for Codex, Claude, and Gemini
- add `ccb doctor storage --json`
- add human render for `ccb doctor storage`
- include `schema_version`, primary class, provider, agent, size, active flags,
  reclaimability, and reason fields in JSON output
- add tests with synthetic `.ccb` trees

This slice directly addresses the current ambiguity without risking data loss.
Only after the classifier is trusted should cleanup or shared-cache behavior be
implemented.

## 13. Current Implementation Status

Implemented:

- Phase A storage classification API exists under `lib/storage_classification/`.
- `ccb doctor storage` and `ccb doctor storage --json` expose storage totals and
  per-entry class/provider/agent/size metadata.
- `ccb doctor storage` reports `shared_cache_status=enabled` for usable project
  and relocated runtime roots; WSL drvfs without runtime relocation remains
  disabled with `wsl_drvfs_requires_runtime_relocation`.
- `PathLayout` exposes `shared_cache_dir` and `provider_shared_cache_dir()` as
  the single future shared-cache root under the effective runtime-state root,
  so WSL relocation will not split shared cache back onto unsupported anchor
  filesystems. The provider-specific helper accepts only canonical shared-cache
  candidate provider `codex`; Claude/Gemini are rejected so retired
  project-scoped caches cannot be recreated through this API.
- `PathLayout.ensure_provider_shared_cache_dir()` is the only approved shared
  cache creation helper. It writes a versioned `MANIFEST.json`, creates under
  the effective runtime-state root, and hard-fails on WSL drvfs project anchors
  unless runtime-state relocation is active.
- `ccb doctor storage` emits `shared_cache_root` and
  `shared_cache_root_usable`. While shared cache is disabled the usable flag is
  `false`; when a WSL drvfs anchor is not relocated, `shared_cache_root` is
  `null` rather than an unsafe project-mounted path.
- Provider auth/OAuth files classify as `SECRET`, not `PROJECTED_CONFIG`.
- Copilot `config.json` classifies as `SECRET` because it mixes authentication
  and application state. Its CCB-owned installed plugin trees and markers
  classify as `PROJECTED_CONFIG`, `plugin-data/` remains session state, and the
  agent-local `data/cache/` selected by `COPILOT_CACHE_HOME` classifies as
  `REBUILDABLE_CACHE`.
- Codex `.tmp/plugins/` plus `.tmp/plugins.sha` classify as
  `STARTUP_AUTHORITY_BUNDLE`, not rebuildable cache.
- Codex `.ccb-session-namespace.json` and Gemini `.gemini/tmp/` classify as
  session authority/evidence; Claude `.claude/.claude.json` remains managed
  trust/session authority but classifies as `SECRET`.
- Claude version-cache entries include active-version metadata:
  `active`, `is_active_version`, `reachable_from_current_symlink`,
  `reclaimable`, and `reason`.
- `.ccb/history/` classifies as `USER_CONTENT`, not provider session state.
- `.ccb/workspaces/` classifies as `WORKSPACE`, not residue.
- Non-Codex `provider_profile.home` is rejected at config load and materializer
  boundaries. Claude/Gemini runtime homes remain managed
  `.ccb/agents/<agent>/provider-state/<provider>/home` paths.
- Phase A.5 Codex migration moves old default
  `.ccb/provider-profiles/<agent>/codex/` runtime-home data into
  `.ccb/agents/<agent>/provider-state/codex/home/` only after session
  authority preflight passes, merges without overwriting existing managed data,
  and rewrites persisted `codex_home`, `codex_session_root`,
  `codex_session_path`, `start_cmd`, and `codex_start_cmd` authority fields.
  Command fields are rewritten with path-boundary checks so unrelated strings
  that merely share the legacy path prefix are preserved.
- Non-explicit Codex provider profiles now materialize config/auth/plugin
  projection into the agent-scoped managed Codex home. Explicit
  `provider_profile.home` remains the only profile-backed Codex runtime-home
  override.
- Non-explicit Codex resolved profile records leave `profile_root` unset so
  persisted provider-profile metadata does not point at the removed legacy
  `.ccb/provider-profiles/<agent>/codex/` runtime-home path.
- Codex migration runs before profile projection, and projection then refreshes
  config/auth/plugins from the active source home/profile after discarding any
  migrated plugin tree, so legacy auth files or plugin trees do not bypass
  current `inherit_auth` or mix bundle versions.
- Codex migration is skipped when agent runtime authority still points at a
  live non-terminal provider runtime process, while stale `idle`/`degraded`
  records without a live pid do not block upgrade migration.
- Codex legacy profile migration writes a best-effort
  `codex_profile_migration` event to `agents/<agent>/events.jsonl` on migrated
  or skipped outcomes so upgrade diagnostics can explain why a legacy tree
  remained in place.
- Claude/Gemini launchers ignore older persisted `runtime_home` values in
  provider profile records.
- Startup preparation rejects duplicate effective provider runtime homes before
  provider launch.
- `ccb config validate` rejects duplicate effective provider runtime homes
  before startup as well.
- `ccb cleanup` is implemented as the single cleanup entrypoint. It refuses to
  run while `ccbd` is active or ask jobs are pending/running, prunes old local
  Claude version residue, removes rebuildable Claude/Gemini cache residue,
  safely detaches CCB-owned legacy Claude cache links, removes the stopped
  current project's retired provider cache, and trims stale
  `pane-crash-*.log` runtime residue.
- `ccb cleanup --legacy-provider-caches` additionally removes provider caches
  for recorded project roots that no longer exist. It validates the CCB
  manifest, absolute project root, and recomputed project id before deletion;
  caches for existing projects and malformed/mismatched manifests are kept.
- After a real version change, the newly installed `ccb __post-update` runner
  performs the retired-cache migration only when authorized by its parent
  updater. A stale-owner-aware user-level lock prevents simultaneous update
  windows from duplicating the sweep. It cleans a stopped current project under
  `startup.lock`, removes cross-project payload only from per-Provider
  schema-v1 manifest-valid buckets whose recorded roots no longer exist, and
  defers active/current or other existing projects to their next successful
  `ccb kill`.
- Automatic migration does not perform a separate recursive byte-size walk,
  never removes the user-scoped Gemini cache, preserves unknown Provider
  entries, malformed/symlinked manifests and foreign links, and records a
  bounded result in `$XDG_STATE_HOME/ccb/provider-cache-cleanup.json`.
- `ccb update --no-cache-cleanup` skips this migration for one update. Cleanup
  errors are warnings and do not roll back the core release; cleanup is skipped
  when required post-update provisioning has already selected rollback.
- `ccb cleanup` holds the project `startup.lock` while re-checking backend/job
  state and pruning; malformed job JSONL blocks cleanup conservatively.
- cleanup reports symlinked Claude `versions/` directories and skips
  out-of-bounds Gemini cache paths instead of traversing them.
- diagnostics bundle export writes `generated/storage-summary.json` and uses
  `StorageClass` to exclude `SECRET`, `REBUILDABLE_CACHE`, and
  `STARTUP_AUTHORITY_BUNDLE` provider payloads from the archive.
- diagnostics bundle provider-state walking does not follow symlinks and
  hard-excludes Codex plugin bundles, Claude version caches, and Gemini/npm
  rebuildable caches even if storage classification fails.
- new managed Claude/Gemini startup no longer consumes the project-scoped
  shared-cache status as launch authority.
- storage diagnostics report the retired current-project cache as a separate
  boundary total and identify the user-scoped Provider cache without
  recursively scanning it, so legacy external cache is no longer invisible
  beside `.ccb` totals without slowing normal diagnostics.
- managed Claude startup exports `DISABLE_AUTOUPDATER=1`,
  `DISABLE_LOGIN_COMMAND=1`, and `DISABLE_LOGOUT_COMMAND=1`, consumes the
  user-installed executable, and does not create a CCB binary cache or writable
  alias to the user's ordinary Keychain services. On macOS, the only writable
  secure-storage target is an agent-derived namespaced service.
- managed Gemini startup disables update checks and routes npm/XDG cache to one
  user-scoped `~/.cache/ccb/provider-cache/gemini/` tree without recursive
  cache nesting.
- 2026-07-23 cache-retirement validation:
  - 496 broad Provider/storage/CLI regressions passed
  - all 79 phase-2 entrypoint tests passed in their focused run
  - repository-wide pytest reached 5933 passed and 15 skipped with one
    unrelated OpenCode shutdown `ENOENT`; the exact test passed on isolated
    rerun
  - external `/home/bfly/yunwei/test_ccb2/cache-retirement-smoke` mounted real
    managed Gemini and Claude panes, completed cold and warm starts, and killed
    cleanly; Claude resolved to the user installation at
    `/home/bfly/.local/share/claude/versions/2.1.206`
  - the retired project-cache path remained absent while the user-scoped
    Gemini cache was created
  - stopped current-project cleanup detached an exact legacy Claude link and
    removed current Claude/Gemini payloads; explicit orphan cleanup removed
    only manifest-valid Claude/Gemini payloads, preserved an unknown Provider
    directory, and active-backend cleanup was refused
  - automatic cleanup follow-up passed 636 related regressions, 11 repository
    hygiene checks, syntax compilation, and whitespace validation
  - an isolated external `ccb_test kill` removed a synthetic current-project
    cache and exposed the cleanup action; an authorized post-update smoke
    removed one valid orphan, preserved unknown and user-scoped content, wrote
    state, produced Chinese output, and honored `--no-cache-cleanup`
  - the final clean-environment repository run passed 5806 tests with 2 skips
    and zero failures
- Linux real validation passed with the current Phase A-C implementation:
  - full unit suite: `1747 passed`
  - communication matrix: `test/system_comm_matrix.sh` passed, covering mixed
    providers, same-provider dual agents, cross-project isolation, `watch`,
    `pend`, and kill cleanup
  - fastpath stress: `test/system_fastpath_stress.sh` passed with 60 asks,
    submit p95 `225ms`, max `252ms`
  - shortened Linux soak after the shutdown reply-delivery fix:
    `CCB_LINUX_SOAK_SECONDS=180 CCB_LINUX_SOAK_KILL_EVERY=3
    test/system_linux_soak.sh` passed with 14 iterations, repeated
    kill/restart, submit p95 `212ms`, max `212ms`
  - real cleanup validation on the soak project passed after injecting Claude
    and Gemini cache residue: no pending jobs remained, `ccb cleanup` removed
    old Claude version cache plus Gemini npm/node-gyp caches while preserving
    Claude current/rollback versions and Gemini `.gemini/tmp` session state
- During Linux cleanup validation, an accepted reply-delivery residue was found
  from shutdown-time after-complete scheduling. The shutdown contract now
  suspends automatic reply-delivery creation once project stop is requested, so
  stop-all terminalization cannot create replacement provider work while
  draining existing jobs.
- The real-platform GitHub Actions workflow
  `.github/workflows/ccbd-real-platform.yml` now includes macOS and WSL
  `ccb doctor storage --json` plus `ccb cleanup` smoke steps. Those steps
  inject Claude version-cache and Gemini npm/node-gyp cache residue through the
  effective `PathLayout`, then assert cleanup removes only rebuildable cache and
  preserves Claude current binaries plus Gemini `.gemini/tmp` session
  state. WSL also accepts either pre-relocation or relocated shared-cache
  disabled reasons.
- Remote macOS and WSL validation passed on GitHub Actions run
  `25632010275` for commit `d693004`:
  <https://github.com/SeemSeam/claude_codex_bridge/actions/runs/25632010275>
  - macOS real ccbd/ask smoke passed in `5m27s`, including lifecycle smoke,
    communication matrix, short soak, fastpath stress, and storage cleanup
    smoke.
  - WSL mounted-drive ccbd/ask smoke passed in `8m40s`, including lifecycle
    smoke, WSL path/relocation tests, communication matrix, short soak,
    fastpath stress, and storage cleanup smoke.

Not implemented yet:

- JSONL retention and compaction.

Next recommended work:

1. Complete Linux source-runtime launch/cleanup verification, then repeat the
   managed Claude/Gemini smoke on macOS and WSL.
2. Keep cross-project legacy deletion explicit and manifest-gated; do not move
   it into startup or automatic update.
