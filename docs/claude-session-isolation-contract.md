# Claude Session Isolation Contract

## 1. Purpose

This document defines the non-drifting contract for `ccb`-managed Claude home
and session isolation.

It is the authoritative design anchor for:

- `claude` startup environment under `ccb`
- agent-scoped Claude provider state layout
- Claude home and projects/session-env root persistence
- Claude bootstrap binding vs bound-session reading
- isolation from non-`ccb` Claude conversations

This document complements, but does not replace, the project startup contract in
[docs/ccbd-startup-supervision-contract.md](/home/bfly/yunwei/ccb_source/docs/ccbd-startup-supervision-contract.md).
Storage class naming, diagnostics classification, shared-cache eligibility, and
cleanup sequencing for managed Claude files are defined by
[docs/ccb-provider-state-storage-boundary-plan.md](/home/bfly/yunwei/ccb_source/docs/ccb-provider-state-storage-boundary-plan.md).
Claude binary/version cache specifics are further narrowed by
[docs/claude-binary-cache-dedup-plan.md](/home/bfly/yunwei/ccb_source/docs/claude-binary-cache-dedup-plan.md).
Authentication projection and logout isolation must also satisfy
[docs/provider-auth-inheritance-contract.md](/home/bfly/yunwei/ccb_source/docs/provider-auth-inheritance-contract.md).
Common asset routing, effective-root resolution, and marker ownership follow
[docs/provider-asset-projection-contract.md](/home/bfly/yunwei/ccb_source/docs/provider-asset-projection-contract.md).

## 2. Identity Model

`ccb` must treat these identities as distinct:

- `agent identity`
  - project anchor + logical agent name + provider
- `runtime generation`
  - one launch generation, currently represented by `ccb_session_id`
- `CCB conversation identity`
  - stable across managed launches and authority generations, represented by
    `ccb_conversation_id`
- `authority generation`
  - the ordered credential/route generation inside one CCB conversation,
    represented by `ccb_authority_generation`
- `provider conversation identity`
  - the concrete Claude conversation, represented by `claude_session_id`

`work_dir` is context only. It must not be treated as the primary identity for a
managed Claude agent.

The effective managed `HOME` is the provider-state boundary for Claude under
`ccb`. `~/.claude/projects` and `~/.claude/session-env` are derived state inside
that managed boundary, not independent isolation authorities.

Operational constraint:

- Claude Code does not expose a stable dedicated `CLAUDE_HOME` flag
- managed isolation therefore requires a private `HOME` projection
- setting only `CLAUDE_PROJECTS_ROOT` is not sufficient, because Claude also
  reads other state under `HOME`

Claude plugin seed and writable-root environment semantics follow the official
[Claude Code environment variable reference](https://code.claude.com/docs/en/env-vars).

## 3. Storage Contract

For a managed Claude agent named `<agent>`:

- runtime artifacts live under:
  - `.ccb/agents/<agent>/provider-runtime/claude/`
- stable provider state lives under:
  - `.ccb/agents/<agent>/provider-state/claude/`

By default, the managed Claude home is:

- `.ccb/agents/<agent>/provider-state/claude/home/`

Inside that home, the managed Claude state is:

- `.ccb/agents/<agent>/provider-state/claude/home/.claude/projects/`
- `.ccb/agents/<agent>/provider-state/claude/home/.claude/session-env/`
- `.ccb/agents/<agent>/provider-state/claude/home/.claude/settings.json`
- `.ccb/agents/<agent>/provider-state/claude/home/.claude/.credentials.json`
  - only when inherited Claude Code login auth is projected into the managed home
  - on macOS, this may be materialized from the user's Claude Code Keychain
    entry when that entry can be read during startup
- `Claude Code-credentials-<agent-home-hash>` in macOS Keychain, or the
  equivalent custom-OAuth prefix
  - only when the current Claude release requires secure storage in addition to
    the private credential file
  - the suffix is derived from the agent-private `.claude` path and must never
    equal an ordinary external Claude service name
- `.ccb/agents/<agent>/provider-state/claude/home/.config/claude-code/auth.json`
  - copied only for compatibility with older or alternate Claude Code login
    cache layouts
- `.ccb/agents/<agent>/provider-state/claude/home/.claude/skills/` when skill inheritance is enabled
- `.ccb/agents/<agent>/provider-state/claude/home/.claude/commands/` when command inheritance is enabled
- `.ccb/agents/<agent>/provider-state/claude/home/.claude/plugins/`
  - the normal agent-local writable plugin root when config/plugin inheritance
    is enabled
  - passed through `CLAUDE_CODE_PLUGIN_CACHE_DIR`; despite the environment
    variable name, Claude Code treats its value as the plugins root and manages
    `marketplaces/` and `cache/` below it
  - must not be a symlink to the source home or another managed agent
- `.ccb/agents/<agent>/provider-state/claude/home/.claude/ccb-empty-plugin-seed/`
  - an empty CCB-owned seed used when no usable source seed may be exposed
- `.ccb/agents/<agent>/provider-state/claude/home/.claude/ccb-empty-plugins/`
  - the isolated writable root used before any usable source seed exists
  - keeps the normal `plugins/` path available for a later first bootstrap
- `.ccb/agents/<agent>/provider-state/claude/home/.claude/ccb-restricted-plugins/`
  - the isolated writable plugin root used when `inherit_config=false` or a
    hard role policy disables inherited assets
- `.ccb/agents/<agent>/provider-state/claude/home/.claude/CLAUDE.md`
  - a CCB-generated memory projection when `inherit_memory = true`
  - not a user-editable source file
  - generated from filtered inherited provider user memory, project
    `.ccb/ccb_memory.md`, and optional `.ccb/agents/<agent>/memory.md`
  - project `CLAUDE.md` is excluded from the CCB-generated bundle because
    Claude Code owns native project-memory loading
  - provider-native rules directories such as `~/.claude/rules/` are not CCB
    generated-memory inputs
  - removed when `inherit_memory = false`
- `.ccb/agents/<agent>/provider-state/claude/home/.claude/.claude.json`
  - contains managed workspace trust plus selected inherited Claude account
    metadata required for official login reuse
  - when config inheritance is enabled, also contains inherited global Claude
    Code MCP servers plus current project/workspace-scoped MCP metadata needed
    for the isolated managed home to see the user's configured MCP tools
  - it is not a provider conversation identity

If the effective Claude home is explicitly overridden by a provider profile, the
effective projects root and session-env root must still be derived from that
home:

- `<claude_home>/.claude/projects/`
- `<claude_home>/.claude/session-env/`

Two configured Claude agents must not resolve to the same effective
`claude_home` unless a future explicit shared-home mode declares and validates
that weaker isolation contract.

The managed session file must persist:

- `claude_home`
- `claude_projects_root`
- `claude_session_env_root`
- `claude_session_id` once bound
- `claude_session_path` once bound
- `claude_provider_authority_fingerprint` for the launch-time API/login/route
  authority
- `ccb_conversation_id`, `ccb_authority_generation`, continuity status, resume
  compatibility, and prior Provider bindings

These fields are authority for managed Claude runtime recovery.

The fingerprint is an Agent-private HMAC over the selected profile, API
environment/route, and applicable inherited or Agent-private auth files. Its
owner-only key lives at
`.ccb/agents/<agent>/provider-state/claude/.ccb-authority-hmac-key`; neither raw
credentials nor a portable plain credential hash may be persisted in session
or diagnostic records.

Credential and config projection is not conversation identity. `ccb` may project
the user's source Claude auth/config into the private managed home so the
provider can authenticate, but projected secret material must not be exported by
diagnostics.

The user's source Claude home must be the real account home, or an explicit
`CCB_SOURCE_HOME` override. A managed provider home under
`.ccb/agents/<agent>/provider-state/<provider>/home` is runtime state and must
not be treated as the source home for inherited Claude config or login
credentials.

## 4. Startup Contract

When `ccb` starts a managed Claude agent:

- it must explicitly set the effective `HOME`
- it must explicitly set `CLAUDE_CONFIG_DIR == <claude_home>/.claude`
- it must explicitly set
  `CLAUDE_SECURESTORAGE_CONFIG_DIR == <claude_home>/.claude`
- it must explicitly set the effective `CLAUDE_PROJECTS_ROOT`
- it must ensure `CLAUDE_PROJECTS_ROOT == <claude_home>/.claude/projects`
- it must explicitly set
  `CLAUDE_SESSION_ENV_ROOT == <claude_home>/.claude/session-env`
- it must use the user-installed Claude executable, disable Claude self-update
  and both provider login/logout commands in the managed pane, and must not
  create a project-scoped CCB binary cache
- it must export `DISABLE_LOGIN_COMMAND=1` and `DISABLE_LOGOUT_COMMAND=1` so a
  managed Claude command cannot replace or remove ambient macOS Keychain login
  state
- it may detach only recognized CCB-owned legacy binary-cache symlinks from the
  managed home; it must preserve foreign symlinks and defer cache-payload
  deletion to explicit stopped-project cleanup
- it must create the managed home, projects root, and session-env root before
  launching Claude
- it must materialize required Claude auth/config projections into the managed
  home without treating them as conversation identity
- before adding `--continue`, it must prove that the recorded
  `claude_provider_authority_fingerprint` matches the newly prepared launch;
  mismatched proof must not directly continue the old native id
- on a mismatch, the old transcript path must be a regular file inside the
  current Agent-managed Claude home before it may seed a continuation
- Claude Code 2.1.220 supports `--resume <id> --fork-session`; when capability
  probing confirms that flag, startup uses it to create a new native id with
  imported context and binds that id to the current authority generation
- if the flag is unavailable or the old path cannot be proven Agent-owned,
  startup creates a linked fresh binding while preserving the old transcript
  and must not label the result as a native fork
- a legacy managed session with no authority fingerprint may continue once
  when its history and home remain inside the same Agent-managed Claude home;
  the new launch persists the current fingerprint, so later restarts return to
  strict matching
- `ccb restart <agent>` must use normal managed-home/profile preparation and
  this authority check rather than replaying the persisted `start_cmd`
- it must not use an existing managed provider home as the inherited source
  home; if the current process `HOME` is a CCB provider-state home, startup must
  fall back to the real account home or an explicit source-home override
- managed Claude home materialization is part of startup preparation, before
  hook/trust installation and before launcher command assembly
- managed `settings.json` projection must treat inherited system settings as the
  baseline and preserve managed runtime sections such as `hooks` and compatible
  Claude-written runtime state such as `permissions`
- managed `settings.json` hook projection must merge source-home Claude Code
  hooks with existing managed runtime hooks, rather than allowing the managed
  CCB finish/activity hooks to hide inherited user hooks on later restarts
- when CCB starts a managed Claude runtime with `auto_permission=true`, a
  managed `permissions` section that has drifted into a CCB-only command
  allowlist must not be preserved over inherited user permissions; CCB may drop
  that stale narrow section during managed-home materialization so the explicit
  `--permission-mode bypassPermissions` startup contract is not undermined by
  old Plan Mode/manual-review residue
- managed `settings.json` projection must treat Claude auth env keys such as
  `ANTHROPIC_AUTH_TOKEN` and `ANTHROPIC_API_KEY` as auth authority, not generic
  config
- managed settings and launcher environment projection must preserve the
  selected credential kind: `ANTHROPIC_AUTH_TOKEN` remains bearer-token
  authority and `ANTHROPIC_API_KEY` remains API-key authority; CCB must not
  synthesize one from the other or export both merely because one is present
- custom API-key acceptance metadata may be generated only for an actual
  `ANTHROPIC_API_KEY`; an inherited `ANTHROPIC_AUTH_TOKEN` must not be relabeled
  as an API key to bypass provider prompts
- compatibility cleanup may remove an equal-valued `ANTHROPIC_API_KEY` from
  existing managed settings when the same managed record already contains
  `ANTHROPIC_AUTH_TOKEN`; this is the legacy CCB-generated token-to-key alias,
  while distinct or API-key-only authority must remain intact
- managed login-auth projection must synchronize Claude Code credential cache
  artifacts required for non-interactive reuse, such as
  `.claude/.credentials.json`, when official login auth inheritance is enabled
- on macOS, where official Claude Code login secrets may live in macOS
  Keychain instead of a source-home file, managed login-auth projection may
  read the user's Claude Code Keychain item and materialize the equivalent
  managed `.claude/.credentials.json` cache; projected secret material remains
  provider state and must be excluded from diagnostics
- when the installed Claude release requires Keychain-backed secure storage,
  startup may seed only the agent-derived namespaced service selected from
  `CLAUDE_SECURESTORAGE_CONFIG_DIR`; refresh and cleanup may mutate only that
  service, while ordinary source Claude services remain read-only
- when a stopped restart observes that the inherited source credential changed
  from the previous CCB-owned projection, startup must update that exact
  agent-derived service before launching Claude; the presence of an older
  private service item must not suppress the refresh
- when the inherited source credential is unchanged, startup must preserve a
  different value already held by the agent-derived service because the
  managed Claude process may have refreshed its private credential after the
  last source projection
- managed login-auth projection must not copy
  `~/Library/Preferences/com.apple.security.plist` or link the managed
  `Library/Keychains` path to the user's Keychains; startup must remove a
  recognized legacy managed link and legacy copied preference without
  traversing the user's Keychain
- managed login-auth projection may also synchronize older or alternate Claude
  Code credential cache artifacts such as `.config/claude-code/auth.json` when
  they exist in the source home
- managed `.claude.json` projection must refresh inherited Claude account
  metadata such as `oauthAccount` and onboarding state from the source
  `<source-home>/.claude.json` into the active managed
  `<claude-home>/.claude/.claude.json` on each launch, while preserving managed
  workspace trust records already written there
- startup must migrate the CCB 8.4.3 legacy
  `<claude-home>/.claude.json` path by recursively merging it with the active
  file, giving active Claude-written fields precedence; it may remove the
  legacy file only after atomically writing the active path
- managed `.claude.json` projection must also refresh source-home global
  `mcpServers` and selected MCP fields for the current project/workspace
  record, including `mcpServers`, `enabledMcpjsonServers`,
  `disabledMcpjsonServers`, `disabledMcpServers`, and `mcpContextUris`, so
  managed Claude agents can inherit the user's Claude Code MCP tool setup
- managed `.claude.json` projection must not copy source workspace trust records
  as conversation authority, and must not copy source API-key secrets such as
  `primaryApiKey`
- managed `.claude.json` projection must not copy unrelated source project
  records; project-scoped MCP state may only be mapped onto the current managed
  workspace/project key
- when source-home auth inheritance is enabled and the source Claude settings
  still provide auth env keys, startup must refresh those source auth values
  into the managed home on each managed launch
- when API inheritance is enabled and no agent/provider profile explicitly sets
  `ANTHROPIC_BASE_URL`, startup must prefer the source-home
  `~/.claude/settings.json` route over a caller-shell `ANTHROPIC_BASE_URL`;
  tools such as `ccswitch` update the source settings file and must take effect
  after a managed Claude restart, while shell environment values are only a
  fallback when the source settings do not define a route
- when source-home auth inheritance is enabled but the source Claude settings no
  longer provide auth env keys, startup must preserve compatible managed-local
  Claude auth state already written inside the managed home instead of blanking
  it during projection; this allows an agent-scoped Claude re-login to survive
  restart after the global Claude home has been logged out
- when source-home auth inheritance is enabled but the source Claude home no
  longer provides official login credential artifacts, startup must preserve
  compatible managed-local Claude login auth already written inside the managed
  home instead of deleting it during projection; this allows an agent-scoped
  Claude re-login to survive restart after the global Claude home has been
  logged out
- when auth inheritance is disabled, startup must not silently keep stale
  managed Claude auth env state, stale copied login credential artifacts, or
  stale inherited Claude account metadata in the active
  `.claude/.claude.json`
- when skill inheritance is enabled, startup must route inherited Claude
  `skills/` into the managed home as independently marked entries on each
  managed launch; an invalid optional source entry must not suppress other
  valid entries, while ordinary unmarked entries are preserved
- independently of optional skill inheritance and restricted-role asset
  policy, startup must project the packaged `ask`, `ccb-clear`, `ccb-compact`,
  and `ccb-diagnose` control skills; those names are CCB-owned and are repaired
  without replacing unrelated skills
- when command inheritance is enabled, startup must route inherited Claude
  `commands/` into the managed home as a CCB projected asset on each managed
  launch under the same marker-first rule
- a legacy markerless Claude commands symlink may be adopted only when it
  already resolves exactly to the current source; a legacy skills symlink is
  detached inside the managed home when necessary to install the CCB-owned
  control entries, without writing through to the external source directory
- when config inheritance and inherited assets are enabled and the source
  `<source-home>/.claude/plugins/` contains `known_marketplaces.json`, a
  `marketplaces/` directory, or a `cache/` directory, startup must set
  `CLAUDE_CODE_PLUGIN_SEED_DIR` to that source plugins root before launching
  Claude
- the source plugin seed is shared read-only authority; startup must set
  `CLAUDE_CODE_PLUGIN_CACHE_DIR` to the current agent's managed
  `<claude-home>/.claude/plugins/` root so marketplace clones, installed plugin
  cache, and provider writes remain agent-local
- before the first interactive launch into a new writable plugin root, startup
  must atomically bootstrap that root from a usable source seed; Claude Code
  versions that synchronize seed marketplaces only after their initial plugin
  scan otherwise require a manual reload or second session
- bootstrap must rebase source-root `installPath` and `installLocation` registry
  values into the agent-local writable root before launch; installed plugin
  code must not execute through an absolute source-home cache path
- the bootstrap is a normal local copy, not a symlink; once the writable root
  exists, startup must preserve it and let Claude own subsequent mutations
- a source plugins directory containing only unrelated metadata such as
  `blocklist.json` is not a usable seed and must not be exposed; startup must
  instead export the managed empty seed and `ccb-empty-plugins` writable root
  so an ambient caller seed cannot leak into the session; if a usable source
  appears later, startup must bootstrap the still-missing normal `plugins/`
  root before launch; an empty legacy normal root may be replaced for this
  migration, but any root containing files or symlinks is provider-owned and
  must be preserved
- `inherit_config=false` and a hard role command policy disable plugin seed
  inheritance; startup must remove inherited plugin settings, export the
  managed empty seed, and use `ccb-restricted-plugins` rather than expose the
  source or normal plugin root
- two managed Claude agents may reference the same read-only source seed but
  must receive different writable plugin roots
- when CCB supplies an explicit `--settings` overlay, launcher capability
  detection must capture the complete Claude help output and pass
  `--setting-sources user,project,local` when supported; a truncated help probe
  must not silently hide managed user settings such as `enabledPlugins`
- when invoking a Windows Claude executable through WSL, both plugin path
  variables must be forwarded through `WSLENV` with `/p` path translation
- when memory inheritance is enabled, startup must refresh the managed
  `.claude/CLAUDE.md` projection on each managed launch so source-home and
  project-memory updates become visible after restart
- `inherit_memory` defaults to true and is independent of `inherit_skills` and
  `inherit_commands`; disabling skill inheritance must not disable memory
  projection
- managed `.claude/CLAUDE.md` projection must be generated atomically and
  idempotently; unchanged content should not be rewritten only to refresh mtime
- users must edit `.ccb/ccb_memory.md`, project `CLAUDE.md`, or
  `.ccb/agents/<agent>/memory.md` rather than the managed projection file
- managed Claude home materialization must receive `project_root`, logical
  `agent_name`, and `workspace_path` from the startup context; it must not infer
  project root by walking upward from provider-runtime paths, because runtime
  state may be relocated outside the project `.ccb` tree
- when inherited Claude hooks reference allowlisted source-home hook assets
  through home-relative paths such as `$HOME/.codeisland/...`, startup may copy
  those referenced assets into the managed home so the inherited hook command
  remains executable under the isolated `HOME`; those copied assets remain
  provider-state and must be excluded from diagnostics
- it may inherit user-session transport variables required for official-login
  connectivity, proxy routing, custom trust stores, browser launch, and WSL
  interop; examples include `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`,
  `SSL_CERT_FILE`, `NODE_EXTRA_CA_CERTS`, `BROWSER`, `WSL_INTEROP`, and
  `WSL_DISTRO_NAME`
- user-session transport inheritance is not Claude session authority and must
  not allow caller-global runtime variables such as `HOME`,
  `CLAUDE_PROJECTS_ROOT`, `CLAUDE_PROJECT_ROOT`, `CLAUDE_*`, or
  `CCB_CALLER_*` to override the managed launcher's agent-scoped values
- when the CCB process itself runs as root, managed Claude startup must add
  `IS_SANDBOX=1` and Claude Code's
  `--dangerously-skip-permissions` root-compatibility flag so Claude can start
  under root; this is a root-only compatibility path and must not affect
  non-root launches
- it must install Claude hook/trust state only inside that managed home
- it must write the effective `claude_home`, `claude_projects_root`, and
  `claude_session_env_root` into the agent session file
- it must not rely on global `~/.claude/projects` as the default managed Claude
  namespace
- it must not create or delete project-level `.claude/settings.json` or
  `.claude/settings.local.json` during startup, and must not rewrite unrelated
  project settings or hooks
- one compatibility migration may rewrite an existing project settings file:
  it may remove only command hooks whose executable is Python and whose script
  argument is an extensionless `ccb-provider-finish-hook` or
  `ccb-provider-activity-hook`; these are legacy CCB-owned launcher commands
  that execute Bash as Python
- that migration must parse settings structurally, preserve every unrelated
  hook and setting, skip malformed files without mutation, write atomically,
  and be idempotent

Absent an explicit validated provider-profile runtime home, the managed
agent-scoped private `HOME` is the default authority.

Startup must fail clearly or mark the agent degraded when the requested managed
home cannot be prepared. It must not silently fall back to the caller's global
Claude home.

## 5. Binding Contract

Managed Claude session reading has exactly two modes:

- `bootstrap`
  - used when the agent is not yet bound to a concrete Claude conversation
  - may scan for a candidate session only within that agent's own managed
    `claude_projects_root`
  - may use `work_dir` only as a filter inside that managed home
- `bound`
  - used after `claude_session_id` or `claude_session_path` exists
  - must prefer the bound session
  - must verify the bound path remains inside that agent's managed Claude home
  - must not drift to a newer workspace session outside explicit rebinding logic

Binding logic must not use shared `work_dir` as the cross-agent reconciliation
key.

Managed readers must not widen their search to global `~/.claude/projects`, even
when they can observe matching workspace paths there. A session outside the
managed home is a contract violation or legacy-leak diagnostic, not a completion
source.

An authority-changing continuation is a new bound native session, not direct
reuse of the old id. `native_fork_continuation` is valid only when startup
actually selected `--resume <old-id> --fork-session` and the new binding was
observed. Without that capability proof, the stable CCB conversation remains
linked to both generations but does not claim native context import.

Every completion path that consumes a Claude hook artifact must bind it to the
active request and persisted managed Claude session, including normal polling,
recovery that bypasses transcript-anchor activation, and cancellation salvage.
The hook schema, request id, provider, agent, workspace, and timestamp must
match the active submission. Both the hook `session_id` and tracked
`claude_session_path` identity must be present and equal; missing session
evidence is not a compatibility match. Implementations may normalize path
separators before extracting the final `.jsonl` stem, but must not search
another Claude home or infer identity from `work_dir`.

## 6. Isolation Contract

By default:

- two `ccb`-managed Claude agents must not share a Claude home
- two `ccb`-managed Claude agents must not share a Claude projects root
- two `inplace` Claude agents may share the same `work_dir`, but must still
  remain isolated
- a non-`ccb` Claude conversation started in the same working directory must not
  be implicitly adopted by a managed agent

Therefore `ccb` and a manually-run `claude` command in the project directory are
separate worlds:

- the manual command may use the user's normal home and `~/.claude`
- the managed agent must use its agent-scoped private `HOME`
- shared `cwd` or matching request text does not merge their conversations

## 7. Compatibility Contract

To avoid breaking restore for older managed sessions, startup may reuse and
migrate a previously recorded Claude home when it is already persisted in the
agent session authority.

Compatibility reuse is evidence-driven migration support only. New managed
launches must write the current explicit `claude_home`, `claude_projects_root`,
and `claude_session_env_root` contract back to authority.

Legacy session evidence pointing to global `~/.claude/projects` or another
non-managed home must not be silently adopted during normal startup.
Persisted session home evidence may be reused only when the resolved
`claude_home` is inside this agent's current managed home boundary or an
explicit validated provider-profile home. Otherwise it is diagnostic legacy
leak evidence, not restore authority.

`ccb -n` remains a valid way to rebuild a project with fresh managed homes. The
first post-reset startup must force `restore=false` as defined by the startup
contract, so old provider-global history is not silently reattached.

## 8. Diagnostics Contract

When managed Claude state lives inside the project under
`.ccb/agents/<agent>/provider-state/claude/`, diagnostics and support bundles
should treat that provider-state tree as project-local evidence.

Diagnostics export should include:

- managed home summary metadata
- managed Claude projects/session-env paths and related project-local session
  files
- non-secret isolated settings overlays when present
- explicit contract-violation evidence when Claude writes outside the managed
  home

Diagnostics export must exclude copied credential files and projected trust/auth
state such as `.claude/.credentials.json`, `.config/claude-code/auth.json`, and
`.claude/.claude.json`. Support bundles must not follow any legacy Keychain
link.
