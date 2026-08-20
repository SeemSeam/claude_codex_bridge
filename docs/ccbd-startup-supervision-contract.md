# CCBD Startup And Supervision Contract

## 1. Purpose

This document defines the non-drifting contract for project-scoped startup, backend ownership, runtime supervision, pane recovery, and kill/shutdown behavior in `ccb_source`.

It is the authoritative design anchor for:

- `ccb` startup behavior
- `ccb` foreground attach behavior
- `ccbd` daemon lifecycle
- project-scoped runtime ownership
- configured-agent mounting
- pane/session/runtime recovery
- `ccb kill` semantics

The repo-local agent memory file [AGENTS.md](/home/bfly/yunwei/ccb_source/AGENTS.md) must always point back to this document rather than duplicating it.

Diagnostics-specific rules live in [docs/ccbd-diagnostics-contract.md](/home/bfly/yunwei/ccb_source/docs/ccbd-diagnostics-contract.md). Startup/shutdown behavior and diagnostics must evolve together.

Startup errors must preserve enough cause detail for the diagnostics contract to be useful. In particular, project tmux namespace preparation failures must not collapse original tmux stderr/stdout into only a generic foreground message such as `failed to prepare tmux server`.

Module/function-level redesign for the project-scoped tmux namespace model lives in [docs/ccbd-project-namespace-lifecycle-plan.md](/home/bfly/yunwei/ccb_source/docs/ccbd-project-namespace-lifecycle-plan.md).

Detailed redesign for pane recovery layering and continuous foreground attach lives in [docs/ccbd-pane-recovery-continuous-attach-plan.md](/home/bfly/yunwei/ccb_source/docs/ccbd-pane-recovery-continuous-attach-plan.md).

Detailed lifecycle-state, keeper-authority, and provider-helper ownership sequencing lives in [docs/ccbd-lifecycle-stability-plan.md](/home/bfly/yunwei/ccb_source/docs/ccbd-lifecycle-stability-plan.md).

User-facing config and tmux layout rules live in [docs/ccb-config-layout-contract.md](/home/bfly/yunwei/ccb_source/docs/ccb-config-layout-contract.md). Startup behavior must honor that layout contract rather than inventing its own pane topology.

Managed Codex conversation isolation rules live in [docs/codex-session-isolation-contract.md](/home/bfly/yunwei/ccb_source/docs/codex-session-isolation-contract.md). Startup behavior must honor that provider-state contract rather than inferring Codex identity from shared `work_dir`.

Managed Claude conversation isolation rules live in [docs/claude-session-isolation-contract.md](/home/bfly/yunwei/ccb_source/docs/claude-session-isolation-contract.md). Startup behavior must honor that provider-state contract rather than inferring Claude identity from shared `work_dir` or global `~/.claude`.

Common provider asset projection and effective-root rules live in
[docs/provider-asset-projection-contract.md](/home/bfly/yunwei/ccb_source/docs/provider-asset-projection-contract.md).

Official DeepSeek Harness service, session, transport, completion, and context
control rules live in
[docs/dsh-service-provider-contract.md](/home/bfly/yunwei/ccb_source/docs/dsh-service-provider-contract.md).

## 2. Problem Statement

The current codebase already contains pieces of the required behavior:

- project-scoped backend ownership via lease/lock
- runtime inspection and pane health checks
- provider-side `ensure_pane()` recovery hooks
- daemon restart-on-next-command behavior
- stop/kill cleanup logic

But those pieces do not currently form a single always-on control-plane contract.

The main failure mode is structural:

- startup authority is split across config, lease, runtime store, provider session files, and tmux facts
- runtime recovery is partially implemented but only executed on some paths
- pane death can mark an agent degraded without triggering a daemon-owned reconciliation loop
- shutdown behavior is split between server-side and CLI fallback logic

This document fixes the contract boundary first, so later implementation does not drift back into scattered patches.

## 3. Scope

In scope:

- one backend per `.ccb` anchor
- daemon startup and takeover rules
- configured-agent desired-state rules
- runtime supervision and recovery rules
- pane death handling
- records under `.ccb/ccbd/`
- `ccb kill` end-to-end semantics
- startup and recovery test matrix

Out of scope:

- provider-specific prompt/protocol details
- completion extraction policy
- mailbox/message semantics except where they depend on runtime liveness

## 4. Terms

- `project anchor`
  - the directory containing `.ccb/`
- `project backend`
  - the unique authoritative `ccbd` process for one project anchor
- `desired agents`
  - the configured agent set defined by `.ccb/ccb.config`
- `authority`
  - the state source allowed to define current project truth
- `evidence`
  - observable facts used for recovery decisions but not allowed to redefine authority
- `residue`
  - stale or extra state from previous runs, renames, or corruption; cleanup input only
- `runtime supervision`
  - the daemon-owned loop that keeps desired agents mounted and healthy
- `keeper`
  - a small watchdog process that restarts `ccbd` after crashes; it is not the project backend

## 5. Hard Contract

### 5.1 Project Scope

- One `.ccb` anchor defines one project control-plane scope.
- The directory that owns `.ccb/` is the only authority root for that project.
- `.ccb/project.identity.json` defines the anchor's stable `project_id` and
  stable project slug once that record exists. The recorded `bound_root` is a
  replaceable locator used to detect relocation or copying; it must not be
  hashed again to redefine project identity.
- New empty anchors receive a random 256-bit project ID. A pre-identity legacy
  anchor preserves its current path-derived ID when its existing anchor content
  or current runtime authority agrees with the current root, or adopts the
  unanimous legacy ID when inactive runtime evidence points to a previous root
  that no longer exists.
- Conflicting legacy IDs or a foreign legacy binding that cannot be proven to
  be an inactive move must fail closed instead of silently minting another ID.
- Moving or renaming an inactive anchor must preserve its project ID and slug,
  update its root binding, and advance its binding epoch.
- A copied identity whose previously bound root still exists must fail closed
  until an explicit project-fork workflow gives the copy a distinct identity.
- Relocation must fail closed while recorded backend authority is still live;
  a live keeper, ccbd PID, or connectable ccbd socket must not be silently
  reinterpreted as inactive move residue.
- Project lifecycle state must live under that project's `.ccb/` only.
- Startup, supervision, and shutdown must be reasoned per project anchor, never globally.
- CCB-managed tmux servers must be started with an isolated tmux config so user-level
  tmux plugins, hooks, and global options cannot alter project pane topology.
- Any tmux behavior CCB depends on after config isolation, including mouse and
  clipboard support, must be applied as CCB-owned server policy rather than
  inherited from user `.tmux.conf`.

### 5.2 One Authoritative Backend

- Each project anchor may have at most one authoritative `ccbd` backend.
- each project anchor may also have at most one project-scoped `keeper`; different projects must have independent keepers and independent `ccbd` generations
- keeper is the only authority allowed to advance project lifecycle phase and to spawn a new `ccbd` generation
- CLI commands may express desired lifecycle state and wait for readiness, but must not compete with keeper by directly owning a second backend-start authority
- backend authority is split on purpose:
  - `.ccb/ccbd/lifecycle.json` defines project lifecycle phase and current desired owner generation
  - `.ccb/ccbd/lease.json` defines liveness for the current `ccbd` generation only
  - socket ownership proves readiness for that current generation
- An `UNMOUNTED` lease whose `project_id` no longer matches the current anchor
  is copied/moved-project residue, not live backend authority; startup may
  supersede it with a fresh generation for the current anchor.
- A stale or dead mounted lease and lifecycle whose `project_id` no longer
  matches the current stable anchor identity may be marked unmounted and
  rebuilt under the current identity inside the startup lock. The replacement
  must preserve monotonic generation and must recompute the effective socket
  locator from the current `PathLayout`.
- Keeper lifecycle reconciliation must validate both `keeper_pid` and
  `project_id`; matching a copied keeper PID alone must never carry a foreign
  lifecycle ID into a new startup fence.
- A live mounted lease with a different `project_id` must still fail closed;
  only a startup-lock reconciliation that proves the lease stale or dead may
  mark it unmounted before takeover.
- A second `ccbd` may only replace the current one through explicit takeover rules.
- Once takeover has replaced the recorded lease holder, the previous daemon must treat that lease as lost authority:
  - heartbeat refresh must not succeed against a replaced holder
  - backend-local shutdown or unmount must not rewrite a newer holder's lease
  - backend-local socket cleanup must not unlink a newer generation's replacement socket
  - stale control-plane helpers must prefer reading current authority over forcing an unmount write
- Provider-specific background daemons must not become competing project authorities.

### 5.3 Desired Agent Set

- Effective config is resolved in three layers: built-in default, user config at `~/.ccb/ccb.config`, then project config at `.ccb/ccb.config`.
- `.ccb/ccb.config` is the highest-priority forward authority for the project's desired agent mount set and foreground layout when it exists.
- When `.ccb/ccb.config` is absent, `~/.ccb/ccb.config` is the user-level forward authority for the project's desired agent mount set and foreground layout when it exists.
- When both files are absent, the built-in default config is the forward authority for the desired agent mount set and foreground layout.
- Before CLI startup performs provider/backend probing, it must inspect the two
  project-local executable config fields defined by the layout contract and
  obtain a matching user-state approval in an interactive terminal. ccbd is a
  non-interactive enforcement boundary: bootstrap must reject missing or stale
  approval before publishing runtime state or materializing the service graph.
  Reload and the final tool/provider shell execution sinks must re-check the
  exact current authority to close config-to-execution races.
- The built-in default desired set contains exactly one `demo` agent in the
  `main` window. Its provider is the first locally available supported CLI in
  built-in priority order (`codex`, `claude`, `gemini`, then optional
  providers). If none is available, `demo:codex` remains the diagnostic
  fallback.
- Built-in provider availability honors the effective provider executable,
  including `*_START_CMD` overrides. This selection applies only while both
  user and project config are absent; explicit config remains authoritative and
  may mount any supported single- or multi-agent topology.
- Effective config logical names are the only forward authority for project-namespace pane display names.
- Until a future explicit `enabled` or `desired_state` field exists, all configured agents are desired agents.
- `default_agents` and CLI `requested_agents` do not redefine long-lived backend ownership.
- `requested_agents` may affect foreground behavior or warm-start order only.

Maintenance heartbeat startup boundary:

- Maintenance heartbeat is disabled by default and must be enabled manually in
  effective config with `[maintenance.heartbeat] enabled = true`.
- `[maintenance.heartbeat]` in effective config may request maintenance
  heartbeat startup ensure when `enabled = true` and `startup_ensure = true`.
- v1 startup ensure is optional and non-fatal: ordinary `ccb` startup must not
  fail only because maintenance heartbeat schedule/status files are missing,
  corrupt, or because a project-scoped maintenance runner cannot be arranged.
- Startup ensure may arrange a CCB-owned project-scoped maintenance heartbeat
  schedule consumer helper when heartbeat is enabled, `startup_ensure = true`,
  and the configured assessor is present.
- The schedule consumer helper is outside provider context and is not a ccbd or
  keeper lifecycle authority. It may read effective config and
  `.ccb/ccbd/maintenance-heartbeat/schedule.json`, record runner diagnostics
  under `.ccb/ccbd/maintenance-heartbeat/runner.json`, and invoke the same
  bounded one-shot due tick used by `ccb maintenance tick`.
- The helper must not classify health, write heartbeat status directly, submit
  assessor activations directly, repair providers, mutate agent runtime
  authority, or mutate daemon lifecycle authority. Those actions remain owned
  by the one-shot tick and existing CCB control-plane surfaces.
- If the helper cannot be started, startup ensure may fall back to the same
  bounded one-shot due tick used by `ccb maintenance tick`. The fallback must
  respect persisted `schedule.json`; a future `next_run_at` exits as
  `too_early` without status, schedule, or activation writes.
- Startup ensure failures are reported in the start summary and heartbeat
  diagnostics when possible, not raised as hard startup failures.
- A startup-triggered helper or fallback tick may cause at most one silent ask
  to the configured assessor through the mounted daemon dispatcher for
  non-healthy evidence, then return to CCB control-plane scheduling.
- Maintenance heartbeat status belongs under
  `.ccb/ccbd/maintenance-heartbeat/` and must not be stored under
  `.ccb/ccbd/heartbeats/<subject-kind>/`.
- `ccb kill` and shutdown must not be blocked by maintenance heartbeat
  schedule/status/runner residue. `ccb kill` must best-effort signal a live
  maintenance schedule consumer helper, but a missing, stale, corrupt, or
  unresponsive helper must not block the shutdown transaction. Heartbeat locks
  must use their own stale-lock rules and must not reuse keeper, lease, or
  startup locks as schedule authority.

### 5.4 Authority Hierarchy

Authority order must be enforced exactly as follows:

1. effective config, resolved as `.ccb/ccb.config` > `~/.ccb/ccb.config` > built-in default
2. `.ccb/ccbd/lifecycle.json`
3. `.ccb/ccbd/lease.json`
4. `.ccb/ccbd/start-policy.json`
5. `.ccb/agents/<configured-agent>/runtime.json` for the current daemon generation

Evidence sources:

- provider session files
- tmux pane liveness
- provider-runtime pid files
- runtime-root contents when runtime state is relocated away from the anchor

Residue sources:

- `.ccb/agents/<unknown-agent>/`
- stale session files
- stale runtime files from previous generations
- malformed runtime files

Rules:

- evidence may guide recovery
- residue may guide cleanup
- managed long-lived provider helpers are slot-scoped runtime resources, not independent authority:
  - a helper or bridge process group must belong to one configured agent slot and one runtime generation
  - helper manifests and runtime records may define ownership for cleanup and restart purposes
  - runtime authority and helper ownership must be written from the same agent-authority update path; later outer-layer field patching must not leave helper ownership on an older daemon/runtime generation
  - helper pids, detached parents, or process names alone are evidence only
  - the managed DSH Web host is a slot-owned service process; its current pane
    is only the POSIX lifecycle/log carrier, while the loopback endpoint record
    and native DSH session/RPC events provide transport and completion evidence
  - a managed Codex app-server used by the visible TUI is a child of the
    slot-owned Codex bridge process group; its socket and pid live only in that
    agent's provider-runtime directory
  - Codex startup may attach the TUI with `--remote` only after the owned Unix
    socket is ready; otherwise the TUI falls back to its ordinary local mode and
    exact active-turn follow-up remains unavailable
  - the remote branch writes an owned marker containing the exact socket path;
    capability requires that marker plus a live WebSocket, and the local
    fallback branch must leave no marker
  - an overlong or unsupported preferred app-server socket path uses the
    existing runtime-socket placement policy with a hash of the full agent
    provider-runtime directory; cleanup owns that exact effective socket
  - app-server startup failure, bridge shutdown, and project stop/kill must
    terminate the exact child and remove the owned socket/pid/marker; the
    project stop path repeats this cleanup after process termination so a
    forced bridge exit cannot leave false capability evidence; neither a stale
    socket nor a helper pid advertises follow-up capability
- configured-agent provider session files are agent-scoped by `.ccb/ccb.config` logical agent name
- provider-base session files such as `.codex-session` or `.claude-session` are legacy or unscoped evidence only:
  - they must not be reinterpreted as a configured agent's identity
  - they may be consulted only when no explicit agent binding is available
- runtime-state relocation markers under either the anchor or the relocated runtime root are evidence only; they must not redefine project authority
- residue such as provider session files or preserved workspaces must not by itself block config bootstrap
- neither evidence nor residue may silently redefine authority
- runtime pid loss is evidence only; for pane-backed runtime it must not preempt pane/session-based recovery checks

Managed Codex session authority rules:

- for a configured Codex agent, the effective managed `CODEX_HOME` belongs to that agent identity, not to the shared `work_dir`
- absent an explicit validated provider-profile runtime home, the default managed Codex home is `.ccb/agents/<agent>/provider-state/codex/home/`
- the effective managed Codex session root is derived from that home as `<codex_home>/sessions`
- startup must set and persist both `CODEX_HOME` and `CODEX_SESSION_ROOT`; `CODEX_SESSION_ROOT` alone is not sufficient managed-provider authority
- startup must also persist Codex provider-route authority for managed explicit
  routes, and a bound Codex session under such a route is reusable only after
  that concrete binding is stamped with matching bound-session authority
- startup must treat the active managed Codex `sessions/` directory as
  Agent-owned conversation history, not credential authority and not mere
  residue
- missing private-HMAC namespace metadata in the canonical Agent-managed home
  is legacy evidence to adopt when its paths and any older route proof remain
  compatible; it is not positive mismatch evidence
- when authority changes inside that canonical namespace, startup must retain
  the `sessions/` tree in place so native history remains visible, record the
  prior binding in the stable CCB conversation history, and prevent that
  incompatible binding from automatic resume
- startup may rotate/archive a session tree only when its home/root is outside
  the validated Agent-managed namespace or other ownership evidence is unsafe;
  authority change alone is not an archive instruction
- withdrawn-v8.5.5 `*-global` or matching legacy-route archives may be merged
  back after exact old-binding/path ownership checks; if authority changed in
  the meantime, the recovered binding is historical linked-continuation
  evidence rather than the current resume target
- provider-base workspace files such as `.codex-session` remain unscoped evidence only unless no explicit configured-agent binding exists
- startup and restore must persist and reuse the effective managed `codex_home` and derived `codex_session_root` when available
- restore must not scan or adopt global `~/.codex/sessions` merely because a manual Codex conversation shares the same `work_dir`

Managed Claude session authority rules:

- for a configured Claude agent, the effective managed `HOME` belongs to that agent identity, not to the shared `work_dir`
- absent an explicit validated provider-profile runtime home, the default managed Claude home is `.ccb/agents/<agent>/provider-state/claude/home/`
- the effective managed Claude projects root is derived from that home as `<claude_home>/.claude/projects`
- the effective managed Claude session-env root is derived from that home as `<claude_home>/.claude/session-env`
- startup must set and persist `HOME`, `claude_home`, `claude_projects_root`, and `claude_session_env_root`
- provider-base workspace files such as `.claude-session` remain unscoped evidence only unless no explicit configured-agent binding exists
- startup and restore must persist and reuse the effective managed Claude home and derived roots when available
- restore must not scan or adopt global `~/.claude/projects` merely because a manual Claude conversation shares the same `work_dir`

Managed conversation continuity rules:

- `ccb_session_id` identifies one launch generation; it must not be reused as
  Provider credential authority
- `ccb_conversation_id` remains stable across stopped Provider launches and
  authority generations for the same configured Agent conversation
- session authority records must retain the authority generation, continuity
  status, resume compatibility, and prior Provider bindings without secret
  material
- same-authority generations may use Provider-native resume after path and
  binding checks; known or unproven cross-authority bindings become linked
  continuations while their native transcripts remain in the Agent-managed
  history root
- linked continuation preserves history and prevents an automatic clear, but
  it must not be described as lossless native context import until the exact
  Provider/version import path is qualified

Managed provider startup mutation rules:

- startup preparation must not create or delete project-level provider dotfiles such as `.claude/settings.json`, `.claude/settings.local.json`, `.gemini/settings.json`, `.codex/*`, or equivalent provider-owned workspace config, and must not rewrite unrelated project settings
- as a narrow compatibility exception, managed Claude preparation may atomically remove only legacy CCB command hooks that invoke an extensionless `ccb-provider-finish-hook` or `ccb-provider-activity-hook` through Python; it must preserve all other project settings and hooks and leave malformed settings files untouched
- startup may create `.ccb/ccb_memory.md` under the project anchor when it is missing, but must
  treat it as user-editable project memory after creation
- startup must not create, import, or otherwise rely on project-root `CCB.md`
- startup must materialize project memory as an idempotent preparation step
  before launching a managed provider process:
  - source files are selected by the provider memory ownership policy; common
    inputs include `.ccb/ccb_memory.md`, filtered provider user memory,
    optional `.ccb/agents/<agent>/memory.md`, and provider-native project memory
    only when that provider does not already load it natively
  - generated seed metadata belongs under
    `<runtime_state_root>/state/memory.seed.json`
  - generated runtime bundles belong under
    `<runtime_state_root>/runtime/memory/<agent>.md`
  - providers that require project-relative memory paths may create generated
    bridge files under `project_root/.ccb/runtime/memory/<agent>.md`
  - unchanged generated content should not be rewritten only to refresh mtime
  - failures to create or refresh project-memory files should degrade with a
    warning unless a provider requires that generated file to start correctly
- `prepare_provider_workspace` is the single writer for generated provider
  memory/config projections during normal pane startup; provider command
  builders should only read the already prepared paths/env and must not refresh
  auth/config/session material as a side effect
- startup must classify reusable bindings before provider preparation:
  - an accepted live binding performs zero provider-home/profile/memory
    preparation because no provider process is being launched
  - a missing or rejected binding performs exactly one
    `prepare_provider_workspace` pass before its launch or relaunch
  - startup must resolve one canonical effective start command before provider
    run-cwd resolution and preparation; that same resolved command must govern
    permission projection and the later launch without policy recomputation
  - the launch path must consume that prepared state and must not repeat profile
    materialization or provider-home projection
- content-addressable generated records and projections must not be replaced or
  fsynced when their serialized content is unchanged
- managed provider home projection must receive project root, agent name, and
  workspace path explicitly from the startup context; it must not recover
  project identity by walking up from relocated runtime-state paths
- when `prepare_provider_workspace` asks a launcher to resolve the provider run
  cwd before a pane launch session exists, it must call `resolve_run_cwd` with
  `launch_session_id = None`; providers must not treat that prepare-phase value
  as persisted session authority
- pane-backed provider launchers may declare a `prepare_launch_context` hook
  when command assembly needs project-scoped context:
  - the runtime launcher must call `prepare_runtime`, then
    `prepare_launch_context`, then pass the final `prepared_state` into
    `build_start_cmd`
  - `prepare_launch_context` may add fields such as `project_root`,
    `workspace_path`, and `agent_events_path`; those fields are
    launch-preparation state, not persisted provider session authority
  - providers that require these fields must fail fast when they are absent
    instead of silently inferring project identity from runtime paths
  - `build_session_payload` receives the same final `prepared_state` used by
    command assembly
- provider bootstrap config needed for managed launches must live under `.ccb/agents/<agent>/provider-state/<provider>/` or an explicit validated provider-profile runtime home
- provider authentication inheritance must satisfy
  [docs/provider-auth-inheritance-contract.md](/home/bfly/yunwei/ccb_source/docs/provider-auth-inheritance-contract.md):
  source user state is read-only inheritance input, managed homes are the only
  writable provider state, and logout/token refresh must never propagate back
  to the user's provider environment
- visible and headless provider launches must overwrite `HOME` plus every
  applicable provider/XDG/storage root with agent-local managed values; WSL
  launches must also pin Windows-facing `USERPROFILE` and app-data roots where
  the provider uses them
- credential or account paths must not be symlinks, junctions, mounts, hard
  links, or other writable aliases to user state; recognized legacy aliases
  must be detached without traversing their source
- managed Codex startup must write `check_for_update_on_startup = false` into
  the generated agent-local `CODEX_HOME/config.toml`; managed Claude startup
  must export `DISABLE_AUTOUPDATER=1`, `DISABLE_LOGIN_COMMAND=1`, and
  `DISABLE_LOGOUT_COMMAND=1`; managed Gemini startup must write both
  `general.enableAutoUpdate = false` and
  `general.enableAutoUpdateNotification = false` into the generated
  agent-local `.gemini/settings.json`. Managed Grok receives
  `GROK_DISABLE_AUTOUPDATER=1`; managed Droid receives
  `FACTORYD_DISABLE_AUTO_UPDATE=1`; managed AGY receives
  `AGY_CLI_DISABLE_AUTO_UPDATE=1`. All managed provider processes also
  receive `NO_UPDATE_NOTIFIER=1` for CLIs using the common Node
  update-notifier convention. These overrides apply only to CCB-managed
  provider processes and must not modify the user's provider-global
  configuration.
  Provider version checks and upgrades belong to the explicit `ccb update`
  flow, never to pane startup or job delivery.
- managed Gemini and Qwen must each enable both of their supported file-storage
  switches so OAuth refresh/logout cannot select an external OS credential
  backend
- managed Codex launch must export the canonical agent-scoped
  `CCB_SESSION_FILE` path into that Codex process. The value points to the
  existing project session authority so opt-in tools such as the inherited
  `reconnect` skill can bind the exact project tmux socket and pane after
  generic `TMUX` variables are sanitized. It must not be inherited from the
  caller shell or treated as backend/startup authority.
- managed Claude startup must use the user-installed Provider executable and
  must not create/copy/hash/link a CCB project-scoped binary cache; recognized
  legacy CCB cache links may be detached during preparation without deleting
  cache payload
- managed Gemini startup routes rebuildable npm/XDG cache to one user-scoped
  `~/.cache/ccb/provider-cache/gemini/` tree and must not create the retired
  `~/.cache/ccb/projects/<project-id>/provider-cache/gemini/` route
- managed OpenCode startup writes `.ccb/agents/<agent>/provider-state/opencode/opencode.json` as a generated `OPENCODE_CONFIG` file; it reads and merges project `opencode.json` without modifying that project file, pins agent-local HOME/XDG data/config/state/cache plus structured storage/log roots, projects auth/account artifacts as one-way copies into that managed data root, uses project-relative memory instructions through `.ccb/runtime/memory/<agent>.md`, uses project-relative inherited ask skill instructions through `.ccb/runtime/skills/<agent>/opencode/ask.md`, disables OpenCode autoupdate for managed panes so startup and job delivery cannot be blocked by an interactive update prompt, and injects `--continue` only when the effective restore policy is not fresh and the configured command does not already contain an explicit OpenCode session selector
- managed Kimi startup must not infer conversation authority from work-directory
  recency or inject `--continue`: `.kimi-<agent>-session` owns a native Kimi
  session only after that agent's exact `CCB_REQ_ID` is observed in the native
  `wire.jsonl`; the record stores the native session id/path, normalized work
  directory, Kimi share root, and observation time separately from
  `ccb_session_id`
- managed Kimi pane restart and dead-pane recovery must validate the current
  project, agent, work directory, share root, exact native layout, and current
  CLI exact-session capability before materializing `--session <owned-id>` (or
  the capability-confirmed stable long equivalent) at the single persisted
  command-template insertion point; first launch, reset, missing/malformed/
  mismatched/symlinked authority, storage drift, a missing command template,
  or an unsupported CLI starts fresh and clears the carried native binding
  without deleting provider-owned data
- explicit user Kimi session-control arguments remain authoritative and must
  not receive a second automatic selector; Kimi's provider manifest
  `supports_resume=false` continues to describe interrupted in-flight CCB job
  restoration and does not prohibit exact provider-conversation continuity
  between managed pane launches
- managed MiMo startup writes `.ccb/agents/<agent>/provider-state/mimo/mimocode.json` as a generated `MIMOCODE_CONFIG` file, uses per-agent `MIMOCODE_HOME` under `.ccb/agents/<agent>/provider-state/mimo/home`, uses project-relative memory instructions through `.ccb/runtime/memory/<agent>.md`, uses project-relative inherited ask skill instructions through `.ccb/runtime/skills/<agent>/mimo/ask.md`, and disables MiMo autoupdate/analysis in managed panes
- managed Copilot preparation reads only validated source
  `config.json.installedPlugins` entries, rebases their `cache_path` values,
  and transactionally seeds exact agent-local plugin trees under
  `.ccb/agents/<agent>/provider-state/copilot/home/installed-plugins/`; it must
  preserve authentication, settings, permissions, sessions, plugin data, MCP
  state, unowned conflicts, and locally diverged metadata or tree content;
  interactive and headless launches both use that agent-local `COPILOT_HOME`
  and set `COPILOT_CACHE_HOME` to the agent-local
  `.ccb/agents/<agent>/provider-state/copilot/data/cache/`
- managed Qwen, Cursor, Copilot, Crush, Grok, Kiro, Pi, and Z.ai startup uses the shared native CLI launcher shape: provider state under `.ccb/agents/<agent>/provider-state/<provider>/`, session payloads that record `<provider>_state_dir`, `<provider>_home`, and `<provider>_data_dir`, managed `HOME` for visible and headless processes, and start-command overrides through `QWEN_START_CMD`, `CURSOR_START_CMD`, `COPILOT_START_CMD`, `CRUSH_START_CMD`, `GROK_START_CMD`, `KIRO_START_CMD`, `PI_START_CMD`, and `ZAI_START_CMD`; known login files are allowlisted one-way projections into those homes, while unknown formats require login inside the private managed home rather than a writable fallback to global provider state; managed Grok startup may project system `.grok/auth.json` and `.grok/config.toml` into the agent-scoped Grok home when inheritance is enabled, while Grok sessions and runtime output remain under the managed home; Grok asks use provider-native headless output and must tolerate both streaming JSON events and aggregated JSON output, with optional model/effort overrides from session data or `CCB_GROK_MODEL` / `CCB_GROK_EFFORT`; Grok success requires a provider-native terminal event such as streaming `type=end` with `stopReason=EndTurn` or the documented compatible native turn-end shape, and a zero process exit without native terminal evidence must close as `incomplete/grok_native_terminal_missing`, never as completed; `CCB_REQ_ID` remains request-attribution metadata, while model-printed `CCB_DONE`, CCB turn-end text, process exit, and the normalized internal `TURN_BOUNDARY` item are not Grok completion authority
- managed Pi and OMP asks use provider-specific structured one-shot observers.
  Pi `0.82.1` requires a final `agent_settled`; OMP `17.1.6` requires
  `agent_end.isTerminal=true`. In both cases `turn_end` is progress only,
  successful semantic completion must still wait for process exit and output
  closure, and zero exit without the required native event closes as
  `incomplete` rather than completed. Older event shapes fail closed.
- managed DSH startup launches the official `dsh web` profile on an
  ephemeral loopback port through a signal-forwarding host wrapper. The
  current POSIX implementation may place that wrapper in the assigned pane,
  but it must never submit prompts, parse replies, infer quiet completion, or
  turn process exit into success from pane state. DSH asks use HTTP/WebSocket
  session RPC and exact native `source.rpcId` plus same-turn
  `assistant/message` and `turn/end(completed)` evidence as defined by
  `docs/dsh-service-provider-contract.md`. Any future non-pane service
  supervisor must preserve project ownership, restart, shutdown, diagnostics,
  and zero-orphan guarantees before replacing this carrier.
- managed Qoder and Qoder CLI CN startup must resolve the final explicit or
  managed `--config-dir` before projecting skills; optional system skills, Role
  skills, and packaged `ask`/`ccb-clear`/`ccb-compact`/`ccb-diagnose` controls
  target that same effective root for both visible and headless execution. The
  released provider key
  `qoderclicn` remains stable. An explicit config root equal to the source
  account's `.qoder` or `.qoder-cn` root remains external user authority and
  must not be mutated by CCB projection.
- managed AGY must use private agent-local `.gemini` and `.antigravity`
  directories. It may copy allowlisted authentication/config files from the
  user's Windows provider home, but it must not symlink or junction either
  managed directory back to that source. Before every launch, CCB must safely
  refresh AGY's provider-recognized
  `.gemini/antigravity-cli/cache/antigravity-keyring-unavailable` marker inside
  that private home so AGY selects file token storage without first waiting on
  the OS keyring. This marker must not be injected globally, placed in the
  source home, or shared with any other provider.
- managed Droid must set the OS `HOME` and `FACTORY_HOME_OVERRIDE` to
  `.ccb/agents/<agent>/provider-state/droid/home/`, set `FACTORY_HOME` to its
  `.factory/` child, and set `FACTORY_DISABLE_KEYRING=true`; known v2 keyring
  material may be read only and converted into private `auth.v2.file` plus
  `auth.v2.key`
- managed Cursor must set `AGENT_CLI_CREDENTIAL_STORE=file`; on macOS it may
  read Cursor's split token services and create only the private
  `<managed-home>/.cursor/auth.json`. New Cursor asks execute in the exact
  managed visible pane by default so configured startup/model arguments and
  pane continuity remain authoritative. CCB defers delivery until the pane is
  stably idle, binds the turn to an exact `CCB_REQ_ID` in a top-level Cursor
  transcript under that managed home, and completes only from a later matching
  `turn_ended` record; stale and subagent transcripts are not terminal
  authority. `CCB_CURSOR_EXECUTION_MODE=headless` is the explicit rollback
  path, and interrupted pane jobs remain resubmit-required. Managed Copilot
  must set `COPILOT_DISABLE_KEYTAR=1`.
- managed Kiro may snapshot the known source SQLite database only through a
  read-only connection and retain only auth/config tables. Kiro must fail
  closed on macOS while its current CLI exposes no private credential-store
  switch.
- managed Grok visible startup defaults to `--minimal`; when agent
  `startup_args` explicitly contains `--fullscreen`, CCB suppresses only that
  injected `--minimal` default before appending user arguments, while unrelated
  startup arguments continue to preserve the minimal default
- agent workspaces may still be created or reconciled as workspace mounts, but provider configuration/trust state must remain inside the managed provider boundary rather than the project worktree
- a configured `git-worktree` workspace requires the project root to be a git repository; startup must fail rather than silently copying a non-git project tree
- the project control plane (`ccb`, keeper, `ccbd`) must not inherit provider-runtime session identity or managed-home variables from the caller shell:
  - examples include `CCB_SESSION_ID`, `CCB_SESSION_FILE`, `CCB_CALLER_*`, `CODEX_*`, `CLAUDE_*`, `GEMINI_*`, `OPENCODE_*`, and equivalent provider runtime markers
  - those variables are runtime-local evidence for the currently running managed agent process, not startup authority for a new or existing project backend
  - provider runtime environment must be injected only into the managed provider process being launched, not leaked into project-scoped control-plane subprocesses
  - when the caller is itself a managed provider pane, daemon and tmux
    control-plane subprocesses must restore the real source-user `HOME`, repair
    XDG roots that point into managed state/cache, and discard injected
    provider API authority; the managed pane environment must not become a
    reverse configuration source; this applies to nonstandard managed homes
    identified by CCB caller/session markers as well as standard provider-state
    path shapes, and unresolved source-user homes fail closed
- that provider-runtime scrub must still preserve ordinary user-session variables needed for the project command pane to behave like the user's shell:
  - examples include `PATH`, `SHELL`, `DISPLAY`, `WAYLAND_DISPLAY`,
    `DBUS_SESSION_BUS_ADDRESS`, `XAUTHORITY`, and `SSH_AUTH_SOCK`
  - user-session transport variables such as proxy settings, custom CA bundle
    paths, browser/session IPC state, and WSL interop markers must also be
    preserved for control-plane children and explicitly injected into managed
    provider panes
  - examples include `HTTPS_PROXY`, `ALL_PROXY`, `NO_PROXY`,
    `CODEX_CA_CERTIFICATE`, `SSL_CERT_FILE`, `NODE_EXTRA_CA_CERTS`, `BROWSER`,
    `WSL_INTEROP`, and `WSL_DISTRO_NAME`
  - the project tmux namespace must refresh shell and desktop transport state
    for command, tool, and sidebar panes; at minimum this includes `PATH`,
    `SHELL`, `BROWSER`, desktop display/session IPC variables, and WSL/Windows
    Terminal interop markers, so a sidebar child such as `ccb config ui` does
    not run with a stale or daemon-only desktop environment
  - those variables are user-session transport or shell-usability state, not managed-provider session authority
  - this allowance must not reopen provider runtime authority inheritance;
    managed variables such as `CODEX_HOME`, `CODEX_SESSION_ROOT`,
    `GEMINI_ROOT`, `GEMINI_CLI_HOME`, `CLAUDE_PROJECTS_ROOT`,
    `OPENCODE_*`, `DROID_*`, and `CCB_CALLER_*` remain runtime-local and must
    be injected only by the provider launch path that owns them

Missing-config recovery rules:

- if `.ccb/ccb.config` is missing, startup must use the built-in default project config from code
- bootstrap must not auto-create, reconstruct, or rewrite `.ccb/ccb.config`
- persisted runtime residue, including `.ccb/agents/*/agent.json`, must not be promoted into a reconstructed user config file
- only a user-authored `.ccb/ccb.config` may replace the built-in default project config

Runtime start policy rules:

- `.ccb/ccbd/start-policy.json` records the current project run's recovery startup policy
- `auto_permission` is inherited project runtime policy, not a one-shot pane-local flag
- recovery restore is not inherited from the original CLI invocation; daemon-owned recovery must always use restore semantics
- plain foreground `ccb` without explicit flags is defined as `restore=true` and `auto_permission=true`
- for managed Codex, `auto_permission=true` must also bypass Codex hook-trust prompts for the managed invocation; `ccb -s` keeps provider-native hook trust prompts enabled
- therefore:
  - explicit foreground `ccb` start uses the CLI-provided `restore` flag and `auto_permission` flag
  - daemon-owned recovery mount, pane recovery, namespace reflow, and post-crash remount must always use `restore=true`
  - those same daemon-owned recovery paths must reuse the persisted `auto_permission` policy from `.ccb/ccbd/start-policy.json`
- `ccb kill` / project stop-all must clear `.ccb/ccbd/start-policy.json`
- daemon-owned background maintenance must not proactively create a missing
  runtime from scratch unless persisted `.ccb/ccbd/start-policy.json`
  authority exists for the current project run

### 5.5 Startup Transaction

Startup must be a single project-scoped transaction:

1. inspect anchor state
2. inspect lifecycle state
3. inspect config state
4. inspect backend lease/socket/heartbeat state
5. decide whether the current phase is `unmounted`, `starting`, `mounted`, `stopping`, or `failed`
6. if startup is required, keeper serializes the new generation under `phase=starting`
7. the new backend binds and proves socket readiness before being published as mounted authority
8. ensure project tmux namespace
9. compute desired agents
10. compute recovery/start plan
11. commit startup actions
12. emit startup result and persist startup report

Lifecycle startup mutation rules:

- durable atomic replacement prevents partial JSON, but it does not make a
  lifecycle `load -> modify -> save` sequence transactional.  CLI running
  intent, keeper lifecycle initialization, and keeper startup transaction
  creation must therefore serialize through the project `startup.lock` and
  reload lifecycle plus lease inspection after acquiring it
- a CLI start may transition `desired_state=stopped` to `running`; when the
  current desired state is already `running`, it must not rewrite the complete
  lifecycle record, clear keeper/daemon observations, or project a newer disk
  config signature into an already accepted generation
- keeper must allocate `startup_id` and generation and durably publish
  `phase=starting` inside that short locked transaction, then release the lock
  before spawning or waiting for the child because the child acquires the same
  lock while claiming backend ownership
- keeper success and failure finalization must reload lifecycle under the lock
  and compare both `startup_id` and generation.  A stale transaction must not
  overwrite a newer startup, a concurrent stop, or fields already published by
  the child; a matching child-published `mounted` record is already final and
  must not be redundantly rebuilt from the pre-spawn `starting` snapshot
- every lifecycle or lease read-modify-write that can overlap start, stop,
  heartbeat, keeper observation, reload-signature handoff, or namespace-epoch
  publication must use that same `startup.lock`, reload current authority after
  acquiring it, and revalidate desired state, generation, startup identity, and
  lease holder before saving.  An atomic file replace is not a substitute for
  this cross-process transaction rule
- keeper passes its exact `startup_id + generation` to the child through a
  one-shot environment fence.  The child consumes the fence, rejects a missing
  or contradictory lifecycle before claiming the lease, and must use the
  keeper-assigned generation even when an earlier failed generation never
  created `lease.json`
- an unfenced compatibility child must reject a keeper-owned `phase=starting`
  record with a startup id.  It may not reinterpret that transaction as a
  legacy direct start, reset its generation, or overwrite its latest report
- immediately after the durable `phase=starting` save returns, keeper samples
  the host monotonic performance counter while still inside the short startup
  transaction and may carry that diagnostics-only value in the same one-shot
  child environment.  The child consumes and removes it at process entry; a
  missing or malformed value must not block startup and must never become
  lifecycle, lease, or report authority
- child progress, mounted publication, failure cleanup, unmount, heartbeat,
  and latest daemon-boot report publication are generation-fenced.  Once a
  stop or newer startup supersedes the transaction, the old child may exit but
  must not rewrite lifecycle, lease, socket authority, or the latest startup
  report
- child startup order is bind/listen, durable starting-owner claim, ping-only
  self-probe through the normal request worker, mounted lease publication,
  fenced runtime restore/adopt, and finally lifecycle `phase=mounted` with
  `startup_stage=mounted`.  The self-probe and runtime bootstrap do not hold
  `startup.lock`; every authority transition reacquires the short lock and
  revalidates generation, owner PID, daemon instance, socket path, and startup
  id when present
- the normal accept loop must be active while the post-probe runtime bootstrap
  runs.  During that bounded interval lifecycle remains
  `phase=starting/startup_stage=runtime_bootstrap`; ping is serviceable but
  non-ping RPC remains gated.  Runtime restore, handoff recovery, and runtime
  adoption recheck authority between recoverable units, and only the child may
  promote this active transaction.  Keeper steady-state observation must not
  synthesize mounted from the interim mounted lease.  That suppression applies
  only when lifecycle generation, owner PID, daemon instance, and socket path
  match the observed mounted lease; a stale child stage must not mask a live
  replacement generation
- direct `CcbdApp.start()` prepares only through the self-probe, interim mounted
  lease, and `starting/runtime_bootstrap`.  It must not publish final mounted
  without continuous accept.  A later `serve_forever()` detects that prepared
  transaction, starts the accept/maintenance loops, completes fenced runtime
  bootstrap, and only then publishes `mounted/mounted`
- final `mounted/mounted` persistence and opening the normal-RPC bootstrap gate
  are one request-dispatch transaction.  The socket gate remains closed while
  the durable lifecycle save runs; a request that observed the socket in that
  interval waits at the gate and may enter its handler only after publication
  succeeds.  Request dispatch must distinguish `_stop_event` from a ready gate:
  shutdown may clear per-attempt bootstrap flags for cleanup, but it must never
  make an accepted request runnable after serving has stopped
- runtime-bootstrap completion requires an explicit publication callback and
  validates the listening socket, active bootstrap state, stop state, sticky
  worker error, and live request worker both before and after that callback.
  Any callback or validation failure sets the serving stop event while the gate
  is still held.  This includes a durable write that completed `replace` but
  failed its directory `fsync`: neither ping nor a normal RPC may interpret the
  visible mounted record as successful readiness
- an unfenced same-process restart may reuse the OS PID but not the daemon
  instance.  Once its next generation has been allocated, mounted publication
  validates that exact generation and new daemon instance rather than
  recomputing ownership from PID/socket equality
- shutdown intent and `desired_state=stopped` are one locked transaction.
  Delayed shutdown finalization must confirm that the same project still has a
  live shutdown intent and remains stopped; it is a no-op after a later start
  has cleared the intent or published a running transaction
- keeper readiness accepts a child only when the ping reports a positive
  serving PID and a non-empty daemon instance and independently proves the
  expected startup id and lease/lifecycle generation plus a matching
  `mounted/running` lifecycle and mounted startup stage. The serving PID need
  not equal the immediate `Popen.pid`: a Windows venv launcher or another
  process wrapper may re-exec the daemon under a different PID. The startup
  transaction identity and generation fences remain authoritative. The ping
  itself is linearly ordered after the final publication gate opens; serving-
  process memory or a mounted file observed while that gate is held is not
  sufficient authority
- if readiness waiting fails or times out, keeper must terminate and reap only
  the independently spawned child process group before recording the failed
  attempt; a late child must not remain able to publish authority
- config loading, process creation, ping/readiness waiting, and other unbounded
  work must remain outside the startup lock

Each foreground start command owns one `startup_run_id`. The CLI sends that
identity with the scoped start RPC, the daemon persists it while still holding
the start transaction lock, and the RPC response echoes it. The daemon is the
only authority that writes the `start_command` startup report; foreground code
must never perform a post-RPC read-modify-write of the latest report because a
later serialized start may already have replaced it.

Startup critical-path rules:

- one existing-namespace validation pass should reuse one tmux pane snapshot
  for topology validation, active-pane discovery, and binding membership; a
  failed snapshot may fall back to direct inspection without weakening identity
  checks
- because that snapshot is server-wide, authoritative topology matching must
  reject panes outside the project session, panes owned by another project,
  stale namespace epochs, and dead panes before cmd, agent, sidebar, or tool
  cardinality is evaluated; duplicate identities in sibling sessions must not
  force recreation of an otherwise valid current namespace
- Codex live-process validation for one startup batch must lazily reuse one
  `/proc` parent-map snapshot; the snapshot scope must end before post-launch
  validation so newly launched processes cannot be hidden by stale data
- pane identity options should be committed as one tmux command batch per pane
  rather than one subprocess per option
- provider launches remain dependency-ordered unless a future bounded scheduler
  proves provider/home/tmux ownership is disjoint; startup must not introduce
  unbounded launch concurrency merely to reduce wall time
- startup reports must expose stage and per-agent timings so optimization and
  regressions can be evaluated from persisted evidence rather than inferred
  from foreground attach latency
- reuse of an already accepted binding preserves its existing successful
  `healthy` or `restored` runtime health. Startup reporting must read the final
  post-restore runtime authority, so a no-op warm start neither reports a
  pre-restore classification nor rewrites restored provenance as healthy
- non-interactive foreground start observations must separately expose real
  CLI phase timings for pre-RPC workspace reconciliation, daemon ensure, the
  start RPC, and all synchronous post-RPC work through `start_agents` return;
  rendering and interactive attach remain outside that measurement boundary

Startup readiness diagnostics:

- readiness evidence is a correlated diagnostics timeline, not lifecycle
  authority and not a second startup state machine
- T0 is current `ccb.py` entry; source-wrapper/Python bootstrap before T0 is
  measured separately and must not be folded into a daemon milestone
- T1 is the keeper's acceptance of the correlated startup intent/startup id.
  For a cold start, the exact diagnostics checkpoint is the host monotonic
  sample taken immediately after the durable `phase=starting` save returns.
  It may replace the CLI observation upper bound only when startup id,
  generation, current daemon lease identity, and
  `T0 <= T1 <= T2 <= RPC` all match.  Otherwise the later CLI observation
  remains `observed_upper_bound`, never exact T1
- T2 is the compatible current-generation control-plane handle; T3 is the
  current project namespace/session becoming attachable; T4 is authority
  commit for the effective requested Agent set; T6 is authority commit for
  the full desired Agent set
- T5 is actual foreground attach/first-frame readiness.  A `--no-attach`
  measurement records `not_applicable_no_attach` and must not estimate T5
- the absolute host monotonic origin and keeper-acceptance counter may cross
  local process/RPC boundaries only as transient diagnostics input.  Startup
  reports persist relative durations, trace/run/generation correlation,
  status, source provenance, and Agent scopes, but never either raw counter
- successful cold no-attach evidence with an exact checkpoint is ordered
  `T0 <= T1 <= T2 <= RPC <= T3 <= T4 <= T6`; the provisional fallback uses
  `T1-upper-bound == T2`.  T4 must name exactly the effective requested set and
  T6 exactly the configured desired set
- malformed readiness input, provenance mismatch, missing positive generation,
  a clock observation before the origin, or a repeated later observation must
  be ignored or rejected without changing startup behavior.  The first valid
  observation for a milestone wins
- a structurally complete timeline that still contains a cold T1 upper bound
  is provisional measurement evidence and must not satisfy the exact keeper
  checkpoint gate.  A warm already-mounted start records
  `not_required_already_mounted` and must not reuse the daemon boot checkpoint

Startup waiter rules:

- lifecycle `phase=mounted` with `startup_stage=mounted` publishes backend
  control-plane readiness only; `phase=starting/startup_stage=runtime_bootstrap`
  is observable progress and is not caller-ready
- control-plane readiness means:
  - the current authoritative generation bound the project socket
  - the current authoritative generation answers the minimal control-plane readiness probe for that socket
  - the current authoritative generation published the matching current lease authority
- the child self-probe must traverse the normal request worker and validate a
  one-time nonce plus project/generation/PID/daemon-instance/startup identity.
  Connections already waiting before the self-client are deferred so a slow
  half-request cannot consume the self-probe budget; deferred mutations remain
  behind the bootstrap gate
- the socket server must keep accepting control-plane connections even when an earlier client connects but does not send a complete request:
  - accepted connections must have a bounded request-read timeout
  - accept and request handling must be decoupled so the kernel listen queue is not consumed by one bad or slow client
  - request handlers and heartbeat/reconcile ticks must still execute serially in one worker lane, preserving current runtime-file write ordering
  - mutating-operation post-request ticks, including the double tick after `submit`, must remain synchronous with the handled request in that worker lane
- worker-lane heartbeat/reconcile failures must terminate the serving loop and release backend ownership; the server must not remain accept-only with a dead worker lane:
  - the first request/maintenance worker failure in one bound-socket generation
    is sticky until the serving loop observes and raises it.  Starting the other
    worker or entering `serve_forever()` must not clear an error recorded after
    the self-probe; error state is reset only when a fresh socket generation is
    successfully bound
- the bootstrap gate covers the readiness decision through handler start.  It
  is not released between checking bootstrap/stop state and selecting a normal
  handler.  This preserves fail-closed shutdown semantics without adding a
  second request lane; the steady-state cost is one uncontended in-process lock
  per parsed RPC
- clients may retry transient connect failures such as `ENOENT`, `ECONNREFUSED`, and `EAGAIN` inside the caller's existing RPC timeout budget, but must not retry after a request has been sent
- commands that only need control-plane RPC, including `ccb ask`, `ping`, `pend`, `watch`, `queue`, and similar daemon callers, must stop waiting at control-plane readiness
- those non-foreground callers must not wait for project-namespace attachability or full desired-agent recovery before submitting work
- interactive `ccb` may continue waiting past control-plane readiness for project-namespace/UI readiness and desired-agent recovery
- CLI callers must not own an independent direct-spawn startup path or a separate local "daemon must be ready in N seconds" authority
- instead, CLI callers express desired lifecycle state, observe the keeper-owned `startup_id` / generation transaction, and return as soon as that transaction reaches success or failure
- `startup_transaction_timeout_s` is the maximum budget ceiling for one keeper-owned cold-start transaction:
  - the default ceiling is 30 seconds so multi-agent cold starts have bounded headroom on supported macOS and WSL filesystems
  - it is not a fixed sleep
  - it is not a generic per-RPC timeout
  - foreground `ccb` startup may use it for the scoped `start` RPC that completes namespace, desired-agent, and startup-report work after control-plane readiness is reached
  - it must return immediately when the relevant transaction reaches success or failure
  - it must not delay ordinary hot-path calls against an already mounted backend
  - stalled startup should also be bounded by a shorter progress-stall policy based on lifecycle startup progress

`ccb` foreground `start_status: ok` is valid only when:

- the project backend is healthy and authoritative
- the project lifecycle phase is `mounted`
- the authoritative mounted generation is the same generation that successfully bound the current project socket
- the project tmux namespace exists at the project-owned socket/session recorded under `.ccb/ccbd/`
- the project tmux namespace has the current session-scoped CCB UI contract applied on that project-owned socket/session
- that project session contains the current namespace window contract:
  - one control window used as the long-lived session anchor
  - for legacy layouts, one workspace window used as the visible pane layout
    anchor
  - for explicit `[windows]` topology, every declared logical window required
    by the current topology signature; `entry_window` is the foreground anchor
    and is not the identity of agents in the other logical windows
- project-generated tmux identifiers must remain tmux-target-safe:
  - project namespace session names must be normalized before use as tmux targets
  - transient workspace reflow operations must address windows by tmux `window_id`, not temporary dotted window names
- config is valid for the current anchor
- desired agents have reached an acceptable mounted state

Acceptable mounted state means one of:

- healthy and attached
- recovering with explicit persisted reason and active reconcile ownership

It must never mean:

- stale binding accepted as success
- missing config silently replaced despite existing project state
- `phase=starting` with only a socket path placeholder but no ready server
- a mounted lease whose socket is not yet ready for live ping
- degraded runtime reported as healthy startup completion

Foreground command split:

- `ccb`
  - ensures backend authority
  - ensures the project tmux namespace
  - ensures desired agents are mounted
  - plain `ccb` is the default interactive start path and implicitly includes `-a -r`
  - release-update advisory checks may read install-scoped cached metadata and schedule background refresh, but they must not join or block the project startup transaction
  - when `ccb` is running in an interactive terminal and will foreground-attach after startup, it should treat that terminal viewport as authoritative startup input and pass the current terminal size into the startup transaction
  - in an interactive terminal, attaches the foreground to the project namespace after the start transaction succeeds
  - foreground attach must tolerate short tmux visibility lag after namespace create/reflow:
    - persisted namespace state may become visible slightly before tmux session/window targets are selectable
    - `ccb` must therefore perform a bounded readiness wait for the authoritative session and workspace window before declaring foreground attach failure
    - this bounded wait must use foreground-attach-specific policy, not the short `rpc_probe_timeout_s` used for daemon compatibility probes
    - the foreground attach RPC budget is allowed to match the stable operational client budget, while daemon config/probe checks must remain fast-fail
    - the foreground attach target-ready budget must remain bounded by the startup transaction budget so namespace/UI lag does not redefine backend startup authority
  - once the tmux client is observed attached, `ccb` should issue a best-effort tmux client refresh so the first attached frame does not depend on a manual user redraw
  - once foreground attach has been established, later foreground client exit,
    detach, terminal close, or transport loss must not rewrite project
    lifecycle authority or request shutdown; only explicit `ccb kill` or
    backend-owned severe-loss recovery may transition the project toward stop
  - in a non-interactive terminal, reports the start transaction without attaching to tmux
  - startup success and foreground attach success are distinct outcomes; foreground attach failure must not rewrite a successful startup report as failed
  - foreground attach errors must state whether `ccbd` failed to answer the attach ping or whether `ccbd` was responsive but the project namespace was not attachable
- ask-family and other non-foreground daemon commands
  - reuse the same keeper-owned backend startup transaction
  - stop waiting at control-plane readiness
  - must not enter namespace attach waits
  - must not reinterpret a namespace/UI delay as backend startup failure
  - may rely on externally attached actionable runtime authority without first
    forcing daemon-owned provider-session mount authority
- `ccb -n`
  - is an explicit destructive project reset before start
  - must require interactive confirmation
  - must clear and rebuild project-owned runtime state, logs, workspaces, and mail/message residue
  - must preserve `.ccb/ccb.config` exactly when it exists
  - must preserve `.ccb/project.identity.json` exactly when it exists so reset
    rebuilds runtime state without changing project identity or stable slug
  - must preserve user-owned `.ccb/ccb_memory.md`, `.ccb/history/`, and
    `.ccb/agents/<agent>/memory.md` files
  - must preserve managed provider conversation history for the same normalized
    agent name and provider present in the effective config, including
    `.ccb/agents/<agent>/provider-state/<provider>/` under the effective
    `PathLayout` runtime root and the matching project session file such as
    `.codex-<agent>-session`
  - must not preserve provider-runtime, mailbox, jobs, pane, helper, or
    non-configured/wrong-provider agent residue
  - if `.ccb/ccb.config` does not exist, startup may bootstrap the default config after reset
  - the same invocation must then continue through the normal `ccb` start transaction rather than using a separate startup implementation
  - that first post-reset startup must force `restore=false` so provider-global
    history cannot silently reattach old conversations outside the preserved
    managed provider-state boundary
  - after the fresh post-reset startup completes, later ordinary `ccb` runs return to the default `-a -r` semantics
- removed attach-only commands
  - the foreground attach stage belongs to `ccb`
  - no public command may attach to the namespace without first running the normal `ccb` startup transaction
  - removed command shims may print guidance, but must not enter parser, dispatch, daemon connection, namespace creation, or provider runtime paths

Project namespace compatibility:

- namespace `layout_version` covers visible pane topology and project-socket tmux UI contract, not just split geometry
- project namespace state must also persist the current visible layout signature produced from `.ccb/ccb.config` after foreground pruning
- for legacy `layout` configurations, the topology projection and signature
  must retain the `cmd` leaf when `cmd_enabled=true`; `cmd` remains excluded
  from `WindowSpec.agent_names` because it is a namespace slot, not an agent
- when stored namespace `layout_version` differs from the current code contract, startup must recreate the project namespace rather than trying to mutate a stale session in place
- when the stored visible layout signature differs from the desired visible layout signature for the current foreground start, startup must recreate the project namespace rather than incrementally splitting an old pane tree
- when startup creates a fresh project namespace session, the root pane must begin as a silent placeholder process rather than an interactive shell
- when startup creates a fresh project namespace session for an interactive foreground `ccb`, the initial tmux session size should come from that foreground terminal-size hint rather than a detached fixed-size default
- for a fresh namespace, the `cmd` pane bootstrap happens only after layout
  finalization and must replace the unique authoritative `role=cmd,slot=cmd`
  silent placeholder in place
- topology materialization must allocate and label `cmd`, sidebar, agent, and
  tool panes as distinct slots; startup must carry the exact authoritative cmd
  pane id from the current session/project/window/namespace-epoch snapshot and
  must never infer cmd from the first or physical root pane in a window
- project topology validation must distinguish structural slot ownership from
  process liveness.  An Agent pane that still has the exact current
  session/project/role/slot/logical-window/managed-by/namespace-epoch identity
  remains the structural owner of that slot even when `pane_dead=1`; it must be
  assigned back to the normal target-only respawn path rather than interpreted
  as whole-topology loss.  Active-pane, binding-reuse, focus, and UI decisions
  remain live-only
- logical-window existence is structural as well: a window containing only an
  exact current dead Agent pane still exists.  Missing, duplicate, foreign,
  wrong-session, wrong-project, wrong-window, or wrong-epoch slots remain
  fail-closed and must not be accepted as current topology
- a topology-managed `cmd` project with a missing or duplicate authoritative
  cmd pane must fail closed or recreate with reason
  `topology_cmd_panes_changed`; it must not respawn a sidebar or agent pane as
  cmd
- project-namespace bootstrap must create the authoritative silent-placeholder session as its first tmux mutation:
  - startup must not issue a standalone `start-server` before `new-session`, because a tmux server with no session may exit immediately
  - `new-session` must establish the server and authoritative project session in one operation
  - CCB-managed tmux policy that may require a live server/session, such as `destroy-unattached off`, `mouse on`, `history-limit 10000` (overridable with `CCB_TMUX_HISTORY_LIMIT`), `set-clipboard on`, `allow-passthrough on`, `mode-keys vi`, vi copy-mode bindings, and Vim-style pane focus/resize bindings, must be applied only after the authoritative project session exists
  - tmux environment synchronization must preserve terminal/media capability signals including `TERM`, `TERM_PROGRAM`, `TERM_PROGRAM_VERSION`, WezTerm/Kitty image-protocol identifiers, and CCB rich-workbench variables such as `CCB_WORKBENCH_TERMINAL_PROGRAM`, so CCB-owned tool panes can make the same rich-media decision as the foreground launcher
- project-owned pane mutation commands, including `respawn-pane` used by `cmd` bootstrap and pane-backed runtime launch/relaunch, must use the same shared tmux ready-retry budget as namespace create/reflow rather than a separate shorter timeout
- namespace session liveness on the project-owned tmux socket must treat `can't find session`, `no server running on <project socket>`, and a missing project socket reported as `error connecting ... (No such file or directory)` as "namespace absent" for create/recreate decisions; startup must not fail that path as a generic tmux inspect error
- startup must not rely on "real shell first, respawn later" behavior for the `cmd` pane, because that leaves stale prompt residue and can surface zsh no-newline `%` markers
- `cmd` bootstrap must directly `exec` the resolved user shell and must not depend on shell-language-specific inline bootstrap snippets that assume the wrapper shell is POSIX-compatible
- `cmd`-anchored projects must treat exact project-namespace pane membership as the reuse gate for pane-backed bindings
- provider-specific live runtime identity proof may further narrow that reuse gate
- a persisted resolved Provider profile further narrows binding reuse:
  - if its provider, Agent identity, normalized explicit runtime home, API/env
    authority, MCP/plugin projection, inheritance flags, inherited-skill
    filters, or skill overlays differ from the current desired profile,
    startup must reject the reused binding and run normal managed Provider
    preparation before relaunch
  - rejecting a binding for profile drift must preserve restore policy and
    conversation history; it is not conversation clear authority
- for project-namespace reuse, exact membership means:
  - same project-owned tmux socket
  - same authoritative tmux session
  - same logical `slot_key`
  - for explicit `[windows]` topology, same logical window name from
    `@ccb_window` (or the matching tmux window name for compatibility) and same
    current `namespace_epoch`
  - for legacy records without logical-window metadata, same current
    authoritative workspace `window_id`
  - the actual tmux `window_id` is captured as a generation-local locator and
    runtime fact; the entry window id must not be required for an agent whose
    explicit logical window matches
- for managed Codex agents with a bound `codex_session_id`, exact namespace membership is still not sufficient:
  - startup must also prove that the live pane process is running the bound `resume <codex_session_id>` conversation
  - for explicit managed Codex routes, the persisted bound-session authority
    must also match the current route authority before `resume` is allowed
  - if that proof is unavailable or negative, startup must reject pane reuse and relaunch through the managed start command
- agent-only legacy layouts with `cmd` disabled may reuse instance-scoped provider session evidence when that session file does not explicitly declare a conflicting tmux socket
- that legacy reuse exception is narrow:
  - if the session file explicitly declares a tmux socket and it is not the project socket, startup must reject it
  - if same-socket pane inspection proves the pane belongs to a detached sibling session or foreign project identity, startup must reject it
  - if provider live-identity proof is merely unavailable or `unknown`, startup may still reuse that legacy instance-scoped binding
  - if provider live-identity proof is explicitly `mismatch`, startup must reject it and relaunch
  - inferred default-server socket facts must not override an otherwise valid instance-scoped legacy binding
- native Herdr layout materialization must split from the exact requested
  `parent_pane`, including a non-root parent; redirecting a requested child
  split back to the workspace root changes the declared topology and is not a
  valid compatibility fallback

### 5.6 Runtime Supervision Is A Daemon Responsibility

The project backend must continuously keep desired agents mounted.

When `.ccb/ccb.config` enables `cmd`, the backend must also continuously keep the project-owned `cmd` slot present and healthy inside the authoritative workspace window.

This responsibility belongs to a daemon-owned supervision loop, not to:

- the next CLI command
- the next job start
- an incidental read path like `ps` or `doctor`
- health inspection paths such as `HealthMonitor.check_all()`

The supervision loop must run on backend heartbeat/tick and reconcile every desired agent, regardless of whether there is queued work.

That daemon-owned responsibility is still bounded by runtime authority rules:

- externally attached actionable runtimes are current runtime authority, not an
  implicit request to start a daemon-owned provider-session mount
- daemon authority adoption must not rewrite an `external-attach` runtime into
  `provider-session` only to stamp current daemon generation
- background maintenance may recover or observe an externally attached runtime,
  but missing-runtime proactive mount remains gated by persisted start-policy
  authority

For `cmd`-enabled projects:

- `cmd` is a project-namespace slot, not an entry in `AgentRegistry`
- `cmd` supervision must therefore happen at the namespace layer, not by pretending `cmd` is a provider runtime
- a healthy `cmd` slot means exactly one authoritative pane in the configured
  workspace window still matches (the physical root pane may be a sidebar):
  - `role=cmd`
  - `slot_key=cmd`
  - `managed_by=ccbd`
  - current project session and `namespace_epoch`
  - current authoritative logical workspace window

### 5.7 Pane Death Recovery Contract

When a desired agent's pane dies, the daemon must reconcile it in the background using this order:

1. inspect current runtime authority
2. inspect provider session and terminal facts
3. if `ensure_pane()` can recover the pane, rebind runtime authority in place
4. if the original pane target is gone but the current project workspace window is still healthy, local recovery must create the replacement pane inside that current workspace window and immediately rebind it to the same logical `slot_key`
5. otherwise, if the project tmux session is still healthy and namespace-level repair is needed, reflow the workspace window inside that same session and relaunch the configured layout there
6. otherwise, if runtime facts prove session-level corruption and full project-wide reflow is safe, recreate the project namespace and relaunch the configured layout
7. otherwise tear down stale binding authority
8. relaunch runtime through the normal launch path
9. persist recovery result and retry/backoff state
10. treat an immediately live replacement pane as provisional, then require a
    post-respawn health observation across the stability window before
    publishing recovery success

Important rule:

- recovery must happen even if the agent is idle and no new job arrives
- when `cmd` is enabled, pane death or slot drift for `cmd` must also be detected and repaired on heartbeat even if no user command is running in that pane
- `cmd` recovery must first try session-preserving local slot replacement inside the current workspace window before escalating to project reflow
- ordinary `pane-dead` / `pane-missing` recovery must not use project-server destruction as the first-line path
- `respawn_pane()` returning and an immediate `is_alive()` check prove only
  that recovery entered `health=recovering` / `reconcile_state=probing`; they
  must not emit `recover_succeeded`
- a probe succeeds only after at least 90 seconds and at least one healthy pane
  observation whose `last_seen_at` is later than the recovery attempt; queued
  work must remain accepted but undispatched while the runtime is probing
- unstable automatic pane recovery uses consecutive-failure backoff of
  30 seconds, 60 seconds, 120 seconds, 5 minutes, 10 minutes, then 30 minutes.
  Six consecutive attempts without a stable probe open
  `health=recovery-circuit-open` / `reconcile_state=blocked`; heartbeat must
  then stop respawning, logging crashes, appending recovery attempts, and
  starting dispatcher work until an explicit restart/remount creates a fresh
  mount attempt
- `restart_count` remains cumulative diagnostics; `recovery_failure_count`
  counts only the current consecutive unstable recovery series and resets
  after a stable probe or a fresh explicit mount attempt
- a provider-declared terminal recovery block is not ordinary pane death. For
  revoked Codex auth, runtime authority must transition to degraded health
  `provider-auth-revoked` with `reconcile_state=blocked`, preserve the
  actionable login/remount reason, and stop heartbeat recovery, replacement
  pane creation, dispatcher starts, and further `restart_count` increments
  until an explicit remount repairs or replaces that authority
- a Claude crash containing `No conversation found to continue` may repair
  only the CCB-owned persisted `--continue` argument and stale Claude session
  id/path before respawn; it must not mutate provider authentication
- a Codex crash containing `failed to connect to remote app server` is a
  managed-helper failure, not a successful local pane recovery. It must fail
  closed with an actionable single-agent restart/remount reason
- a still-present, exact-owned `pane-dead` Agent leaf is not a namespace
  recreate reason.  Namespace identity and healthy peer runtime identity must
  remain unchanged while the dead target alone is prepared and respawned
- pane-backed runtime authority must carry `slot_key`, current logical-window
  `window_id`, logical window name when explicit, and `workspace_epoch`; pane id
  is evidence, not identity
- local replacement must target the authoritative current logical window for
  that slot in the project session, not whichever tmux target the provider
  backend would create by default
- if local replacement changes pane id inside a project-owned namespace and project-wide reflow is currently safe, the daemon must immediately continue into session-preserving workspace reflow so the pane returns to canonical layout position
- session-preserving workspace reflow is the first namespace-level escalation for `pane_recovery:*`
- if local replacement cannot restore `cmd`, `cmd` slot recovery must escalate through that same session-preserving `pane_recovery:*` reflow path, with `pane_recovery:cmd` as the canonical reason
- if pane recovery is done by project-namespace reflow, pane position must return to the canonical layout derived from `.ccb/ccb.config`, not whichever slot tmux happens to assign during local recovery
- workspace reflow must preserve the tmux server and tmux session; only the workspace window may be replaced
- transient tmux/server-readiness failures during heartbeat-driven supervision must degrade or retry background maintenance, but must not by themselves crash or unmount the current authoritative `ccbd`
- heartbeat-driven namespace liveness probes must use a short non-blocking readiness budget; if the project tmux server/socket is transiently unavailable, the daemon must defer that maintenance pass instead of spending the full foreground startup timeout inside `has-session` / `list-panes`
- heartbeat-driven mount/reflow attempts that hit transient tmux/server unavailability must preserve current authority, record retry/backoff evidence, and retry later; they must not immediately reinterpret that transient as a stable missing-session signal
- recovery must always use restore semantics even if the original foreground `ccb` invocation did not pass `-r`
- recovery must inherit `auto_permission` from the persisted project start policy rather than falling back to hardcoded defaults

Project-namespace reflow safety rules:

- project-wide full reflow is an escalation path, not the default response to ordinary pane death
- session-preserving workspace reflow is allowed only when the affected runtime belongs to the project-owned tmux socket/session recorded under `.ccb/ccbd/`
- full project reflow is allowed only when the session itself is no longer a trustworthy repair boundary
- only reflow when no other configured agent is currently `BUSY`
- if reflow is not safe, fall back to local provider recovery rather than disrupting unrelated work

Manual pane restart:

- an explicit user-triggered project pane restart is not ordinary pane-death recovery; while the project namespace is healthy, it must respawn configured agent panes in place and preserve the attached tmux session
- namespace recreation is an escalation fallback only when the current project namespace is no longer a trustworthy repair boundary
- the restart target set is all configured agents from `.ccb/ccb.config`, not only the currently focused or default subset
- the restart must inherit restore and auto-permission choices from the persisted project start policy
- when requested from a sidebar pane, the sidebar must remain attached while the daemon restarts agent panes
- each managed sidebar pane records the content identity of the helper binary that it is running; topology refresh must compare that identity with the currently installed helper and respawn only a stale sidebar pane in place
- refreshing a stale sidebar helper must preserve the project tmux session, window topology, and every configured agent pane; helper replacement must not be coupled to agent restart or full namespace reflow
- after a successful start RPC, the current foreground CLI must perform the same bounded helper-identity repair directly against the authoritative project tmux socket; this compatibility path updates sidebars when a healthy daemon from an older compatible CCB release is still resident
- foreground helper repair failure must be reported in start output without reclassifying a successfully started project or mutating namespace, lifecycle, lease, or agent-runtime authority

Project-socket cleanup rules:

- startup must compute the authoritative active pane set for the current project-owned tmux socket
- the protected active set contains only one live exact match for each pane
  identity expected by the current topology.  A same-session/project/epoch pane
  with an unknown or removed role/slot/window identity is residue, not active
  authority, and must remain outside that set so ordinary orphan cleanup can
  remove it without forcing a namespace rebuild
- same-socket pane/session residue is evidence only; it must not be silently tolerated just because it lives on the project socket
- startup must clean project-owned orphan panes on the project socket during the startup transaction, not wait for a later manual cleanup path
- UNIX-socket cleanup must be identity-safe:
  - a daemon may unlink the project socket path only if the current filesystem entry is still the exact socket inode it bound
  - startup must not blind-unlink an existing project socket path merely because the path exists; it must first prove that the current inode belongs to the same authoritative generation or to a fully invalidated predecessor
  - a live existing UNIX socket or a non-socket filesystem entry must fail
    closed and remain untouched; only an unconnectable socket whose inode is
    unchanged across the stale check may be removed
  - bind/listen/timeout setup is one local resource transaction: any failure
    closes the new fd and unlinks only the inode created by that attempt
  - closing the owned fd, checking/unlinking its path, and releasing lease/
    lifecycle authority must run under the same project `startup.lock` used by
    bind.  Worker joins run after releasing that lock
  - shutting down an old daemon must never remove a newer daemon's replacement socket path

### 5.8 Daemon Must Not Stay Dead

Strictly satisfying "backend must not die" requires a process outside `ccbd` itself.

Target architecture:

- `ccbd` remains the only authoritative project backend
- a lightweight project-scoped `keeper` process monitors it
- the keeper may restart `ccbd` after crashes
- the keeper is the only authority allowed to initiate a fresh backend generation
- the keeper never owns project runtime authority inside the mounted backend generation
- the keeper must reap exited direct children so crashed `ccbd` pids do not linger as zombie evidence
- forced takeover is allowed only after the lifecycle state and lease inspection together prove that the previous generation has entered a true takeover window:
  - `MISSING`
  - `UNMOUNTED`
  - `STALE`
- `DEGRADED` with a live pid plus fresh heartbeat is observation only, not restart authority, even if the project socket is temporarily unreachable
- therefore temporary UNIX-socket accept stalls during active work must surface as degraded availability, not a keeper-triggered daemon replacement
- config-check or live-ping timeout against a nominally mounted daemon is degraded observation only unless lifecycle state or ownership proof explicitly marks the generation failed
- keeper config-check and graceful-shutdown probes must use the shared short `rpc_probe_timeout_s`; they must not use private shorter literals that make mounted generations look failed during normal startup load
- if takeover does occur, any superseded daemon that wakes up again must fail its next lease refresh and exit rather than continuing to serve against stale authority
- keeper restart is a keepalive mechanism, not an unbounded crash-loop generator:
  - resource-pressure startup failures such as fork/process exhaustion, memory exhaustion, or file-descriptor exhaustion must suppress automatic restart immediately
  - repeated `ccbd` startup transaction failures must suppress automatic restart after a bounded attempt count
  - suppression must record lifecycle `phase=failed`, `desired_state=stopped`, and a `last_failure_reason` prefixed with `keeper_restart_suppressed`
  - the keeper process must then exit instead of polling forever; a later explicit user `ccb` command may clear shutdown intent, express `desired_state=running`, and start a fresh keeper
  - suppression must not apply to normal mounted-daemon observation failures where the generation is still live and heartbeat-fresh; those remain degraded observations, not replacement authority

If keeper is absent, the system can only provide "restart on next `ccb` command", which is weaker than the target contract.

When `ccb` re-enters a project after an explicit shutdown, startup must first clear prior shutdown intent before keeper/daemon keepalive can resume.

### 5.9 Kill And Shutdown Transaction

`ccb kill` at the project anchor must execute a single shutdown transaction:

1. acquire shutdown intent
2. prevent keeper restart
3. stop new intake
4. terminalize all non-terminal project jobs so no queued/running request survives as restore or retry authority
5. stop running agent executions
6. stop all desired agents
7. destroy the project tmux namespace at the project-owned socket/session
8. terminate surviving provider runtime pids that outlive namespace destruction
9. mark configured-agent runtime authority as stopped
10. unmount backend lease
11. close socket server
12. persist shutdown report

Shutdown must be best-effort toward residue and strict toward authority.

That means:

- malformed or unknown residue must not block kill
- explicit `ccb kill` is a strong management action and must not be blocked merely because the current backend is `DEGRADED` with fresh heartbeat but an unreachable socket
- configured-agent authority must end in a clean stopped/unmounted state
- non-terminal jobs must not survive explicit project stop as active restore or automatic retry authority
- provider execution state is slot-owned runtime residue once the latest job record is terminal or missing; startup/rebuild and late provider updates must clear those stale execution files so `doctor` does not report cancelled/completed work as active or recoverable execution authority
- shutdown terminalization must not create fresh provider work while draining existing work; in particular, after-complete hooks such as automatic reply delivery must be suspended once project stop is requested
- once shutdown intent is acquired, the backend must not run any further reconcile/heartbeat tick that could remount desired agents during the same shutdown transaction
- once shutdown intent is acquired, the maintenance heartbeat schedule consumer
  helper must exit or be best-effort signalled; it must not invoke a fresh
  `ccb maintenance tick` during the same shutdown transaction
- once shutdown intent is acquired, new mutating RPC requests such as `submit`, `start`, `restore`, `retry`, or `attach` must be rejected with a stable lifecycle-level stopping error; clients must not surface raw socket reset errors as the user-visible contract
- shutdown-style RPC handlers that return an after-response finalizer must enqueue that finalizer even when writing the response fails; `stop_all` may destroy the tmux pane that issued `ccb kill`, and a disconnected client must not prevent backend unmount/finalization
- local daemon shutdown helpers must not stop at `mark_unmounted()` plus socket close; they must run the same stop-all cleanup transaction first so provider-runtime pid files, namespace state, and configured-agent authority do not survive a backend-local shutdown
- CLI remote-stop shutdown helpers must snapshot structured control-plane pids and record shutdown intent before sending `stop_all`; they must also keep tracking any current `ccbd` and project `keeper` pids still published by the project lease during the bounded shutdown wait so a missed pre-stop snapshot cannot leave a live backend behind
- if the keeper closes the control-plane transport after shutdown intent is recorded but before `stop_all` or `shutdown` returns, the CLI must continue through bounded local pid cleanup and lifecycle finalization; this transport loss is not proof of success, but it must not surface as a raw socket error or abort authoritative cleanup
- CLI remote-stop shutdown helpers must not treat lifecycle `phase=unmounted` alone as terminal; after a successful `stop_all` response they must also wait for the recorded and currently published `ccbd` / project `keeper` pids to exit, terminate lingering control-plane pids with the same bounded pid-tree cleanup used by the local shutdown path, and persist lifecycle `phase=unmounted` / `desired_state=stopped`
- orphan process collection must include structured control-plane pid authority from `.ccb/ccbd/lease.json`, `.ccb/ccbd/keeper.json`, and `.ccb/ccbd/lifecycle.json`; `/proc` command-line matching is only a fallback evidence source and must not be the only way to find ccbd/keeper residue
- control-plane `/proc` fallback matching must be scoped to CCB control-plane commands for the same `--project <project_root>`; it must not broadly kill every process whose command line mentions the project root
- tmux shutdown cleanup must preserve full project socket paths from `TMUX`, `CCB_TMUX_SOCKET_PATH`, and runtime authority records; collapsing `/path/to/tmux.sock` to `tmux.sock` targets a different tmux server and violates project-scoped kill semantics
- process liveness checks used by shutdown cleanup must treat Linux zombie (`Z`) processes as already dead; uninterruptible (`D`) processes remain alive evidence and may survive until the kernel releases them
- lease writes that transition backend authority to `unmounted` must be holder-safe:
  - daemon-local shutdown paths may only unmount the lease they still own
  - CLI or keeper cleanup paths acting on an inspected lease must not overwrite a newer holder that took over after inspection
  - a holder mismatch is not equivalent to a missing lease.  Lifecycle-only
    fallback cleanup is allowed only after a fresh locked read proves no lease
    exists; a foreign mounted lease leaves lifecycle authority untouched
- long-lived provider helper groups must also be cleaned as part of the same authoritative shutdown transaction:
  - helper cleanup must be keyed by slot ownership and runtime generation, not by blind global process-name scans
  - helper orphan sweeping is a safety fuse, not the normal meaning of `ccb kill`

## 6. Required Runtime States

At minimum, the supervision model must distinguish these states:

- `unmounted`
- `starting`
- `healthy`
- `recovering`
- `degraded`
- `blocked`
- `stopped`
- `failed`

For desired agents, `recovering` and `degraded` are not the same:

- `recovering`
  - daemon owns a live reconcile attempt or a provisional replacement pane is
    inside its stability window; dispatcher starts remain blocked
- `degraded`
  - agent is not healthy and no active recovery has yet succeeded
- `blocked`
  - recovery reached a terminal provider condition requiring explicit user
    action; daemon heartbeat must preserve the reason without retrying

The current code already records `degraded`, but the target contract requires a distinct supervised recovery state.

## 7. Records Under .ccb

The following records are required.

### 7.1 Backend Authority

Path:

- `.ccb/ccbd/lifecycle.json`
- `.ccb/ccbd/lease.json`
- `.ccb/ccbd/state.json`

Required fields for lifecycle authority:

- `project_id`
- `desired_state`
- `phase`
- `generation`
- `startup_id`
- `keeper_pid`
- `owner_pid`
- `owner_daemon_instance_id`
- `socket_path`
- `config_signature`
- `phase_started_at`
- optional `last_failure_reason`
- optional `shutdown_intent`

Required fields for backend liveness:

- `project_id`
- `ccbd_pid`
- `namespace_epoch`
- `tmux_socket_path`
- `tmux_session_name`
- `socket_path`
- `generation`
- `started_at`
- `last_heartbeat_at`
- `mount_state`
- `config_signature`
- optional `keeper_pid`
- optional `daemon_instance_id`

Write rule:

- `lease.json.socket_path` and lifecycle authority `socket_path` must always record the effective active socket path for the current generation; preferred socket paths are diagnostics only and may live under the project anchor or a relocated runtime root, but they do not redefine authority.

### 7.2 Startup Report

Path:

- `.ccb/ccbd/startup-report.json`

Required purpose:

- capture why startup succeeded, failed, took over, or recovered

Minimum content:

- anchor state
- config state
- daemon inspection
- socket placement decision
  - at minimum preferred/effective socket path, root kind, and fallback reason for both `ccbd` and project tmux socket selection
- desired agents
- actions taken
- final status

### 7.3 Supervision Event Log

Path:

- `.ccb/ccbd/supervision.jsonl`

Required purpose:

- append-only record of pane death detection, relaunch attempts,
  `recover_probing`, recovery failures, circuit-open blocks, and stable success
  transitions

### 7.4 Agent Runtime Authority

Path:

- `.ccb/agents/<agent>/runtime.json`

Required fields beyond current baseline:

- `daemon_generation`
- `runtime_generation`
- `desired_state`
- `reconcile_state`
- `restart_count`
- `recovery_failure_count`
- `last_reconcile_at`
- `last_failure_reason`
- optional `runtime_owner_pid`
- optional `runtime_owner_pgid`
- optional `tmux_socket_name`
- optional `tmux_socket_path`

Required write semantics:

- `started_at`, `binding_generation`, `runtime_generation`, and `daemon_generation` must advance only when a new runtime authority epoch is created
- a no-op reattach or repeated observation of the same binding within the same daemon generation must not silently bump `binding_generation`
- when daemon generation changes, the resulting runtime authority must remain self-consistent even if the binding facts are otherwise reused
- a supervision mount/recovery attempt must not overwrite a newer runtime authority epoch that was attached or adopted concurrently; once superseded, the older attempt may emit evidence but must not write failed or stale runtime authority back into `runtime.json`
- mount-attempt ownership is represented by `mount_attempt_id` on
  `runtime.json`; daemon-owned mount start, attach, success finalize, and
  failure finalize must all compare against that token before writing authority
- if an external attach supersedes a daemon-owned mount attempt, older attach or
  finalize paths may emit diagnostics evidence but must not retake authority;
  a foreground start whose attempt-scoped attach is rejected must fail closed
  before restore bookkeeping and must not report the superseded launch as
  mounted
- runtime authority writes must go through the explicit agent-authority path (`attach` / authority-adopt / authority-mutate equivalents), not through generic outer-layer state patching
- generic runtime state patching may update operational fields such as `state`, `health`, queue/reconcile markers, and last-seen timestamps, but must not mutate epoch/binding ownership fields
- registry persistence must reject non-authority writes that attempt to change authority-owned fields for an existing runtime record

Unknown agent directories under `.ccb/agents/` are residue unless they are present in current config.

### 7.5 Provider Helper Ownership

Path:

- `.ccb/agents/<agent>/helper.json`

Required purpose:

- record long-lived slot-scoped provider helper ownership such as bridge processes
- make helper cleanup generation-safe and slot-safe

Minimum content:

- `agent_name`
- `runtime_generation`
- `helper_kind`
- `leader_pid`
- `pgid`
- `started_at`
- optional `owner_daemon_generation`
- optional `state`

Required write semantics:

- helper ownership must be derived from the final persisted runtime authority for that slot
- `owner_daemon_generation` must match the runtime authority that currently owns that helper group
- helper `started_at` must reflect the current runtime authority epoch, not a superseded slot generation
- helper manifest writes must use canonical persisted `runtime_generation`; they must not silently fall back to `binding_generation`, pane facts, or pid residue

### 7.6 Keeper State

Path:

- `.ccb/ccbd/keeper.json`

Required purpose:

- record the project-scoped keeper process that currently owns daemon keepalive
- make keeper restart attempts and recent failure reason inspectable without treating keeper as backend authority

Minimum content:

- `project_id`
- `keeper_pid`
- `started_at`
- `last_check_at`
- `state`
- `restart_count`
- optional `last_restart_at`
- optional `last_failure_reason`

When automatic daemon restart is suppressed, `state` must become `failed` and
`last_failure_reason` must use the `keeper_restart_suppressed:*` prefix. This is
keeper keepalive state only; backend authority remains in lifecycle and lease
records.

### 7.7 Shutdown Intent

Path:

- `.ccb/ccbd/shutdown-intent.json`

Required purpose:

- persist explicit shutdown intent so keeper will not restart `ccbd` during or after `ccb kill`

Minimum content:

- `project_id`
- `requested_at`
- `requested_by_pid`
- `reason`

### 7.8 Reload Drain State

Path:

- `.ccb/ccbd/reload-drain.json`

Required purpose:

- record bounded pending unload/replace drain state before dynamic unload or
  replacement is exposed
- preserve explicit timeout, age, and pending-count bounds for drain decisions

Minimum content:

- `bounds`
- `records`
- for each record: intent kind, agent name, phase, status, created/updated
  timestamps, timeout deadline, max-age deadline, reason, and busy observation

Write semantics:

- this file is not backend lifecycle authority, lease authority, runtime
  authority, or namespace authority
- writes must occur only from explicit drain state-machine operations; daemon
  heartbeat and steady-state handler reads must not scan it
- Phase 4 `retired` records are terminal state markers only and must not imply
  tmux pane removal, provider stop, runtime authority deletion, service graph
  publish, or namespace patch

### 7.9 Reload Handoff

Path:

- `.ccb/ccbd/reload-handoff.json`

Required purpose:

- record explicit additive reload ownership while `.ccb/ccb.config` already
  contains the target signature but the mounted daemon may still report the old
  service-graph signature
- prove that an accepted reload transaction is still being handled by the same
  mounted daemon holder

Minimum content:

- `project_id`
- `started_at`
- `old_config_signature`
- `target_config_signature`
- current daemon `pid`, `daemon_instance_id`, and `generation`
- `status=applying`
- `ttl_s`

Write semantics:

- this file is not backend lifecycle authority, lease authority, runtime
  authority, namespace authority, or a config-watch trigger
- a modern mounted daemon with a config-signature mismatch is treated as
  `reload pending`, not as daemon incompatibility. Keeper and CLI compatibility
  checks must leave the daemon running so explicit `ccb reload` or sidebar
  reload can apply the changed config without interrupting existing agents.
- the `ccb reload` CLI may write it immediately before submitting the explicit
  non-dry-run RPC, and the daemon may overwrite it inside the accepted apply
  transaction after the plan is accepted; both writers use the same holder and
  signature checks, only write when target and current config signatures
  differ, and clear in `finally`
- handoff acceptance still requires freshness, matching project id, matching
  target signature, matching old daemon signature, and matching current lease
  holder pid, daemon instance, generation, socket, and liveness evidence
- stale, mismatched, unreadable, or missing handoff records must fail closed for
  handoff trust, but must not by themselves trigger daemon restart while the
  modern daemon remains mounted and connectable

### 7.10 Config Restart Intent

Path:

- `.ccb/ccbd/config-restart-intent.json`

Required purpose:

- Config UI may explicitly classify an active-config edit as restart-bound.
  This is a foreground-start instruction, not generic config drift and not
  keeper authority.
- The record contains the active config file digest plus the current daemon
  instance and generation, but never API keys, URLs, env values, or other
  config contents.
- Keeper must leave the mounted daemon running until the user next starts CCB.
- The next foreground `ccb` must stop that recorded daemon holder and start a
  fresh generation even when an earlier faulty reload already published the
  target config signature.
- A successful start clears the intent only after the fresh lease reports the
  current active config signature. A changed target, failed start, or reused
  daemon holder leaves the intent pending.
- Ordinary manual file edits without this explicit intent retain the existing
  reload-pending behavior and must not restart the daemon automatically.

### 7.11 Diagnostics Bundle

Command:

- `ccb doctor --bundle`

Required purpose:

- export a project-scoped support artifact that is sufficient for remote bug triage without interactive shell access

Required content:

- latest startup/shutdown/restore reports
- backend authority files
- backend stdout/stderr logs
- supervision and cleanup event history
- per-agent runtime authority and recent provider/runtime logs
- manifest rows that mark missing or truncated files explicitly

## 8. Implementation Shape

The design should converge toward these domains:

- `startup inspection`
- `startup policy`
- `startup transaction`
- `runtime supervision`
- `shutdown transaction`
- `reporting/read path`

Recommended module split:

- `lib/ccbd/startup/inspection.py`
- `lib/ccbd/startup/policy.py`
- `lib/ccbd/startup/transaction.py`
- `lib/ccbd/supervision/inspector.py`
- `lib/ccbd/supervision/loop.py`
- `lib/ccbd/shutdown/transaction.py`
- `lib/ccbd/reports/startup_report.py`

The key rule is not the exact package name. The key rule is separation:

- inspect first
- decide next
- mutate last

## 9. Current Code Alignment And Gap

The current code already aligns with the contract in some places:

- unique backend ownership is partly enforced by `OwnershipGuard`
- heartbeat and lease refresh exist in `CcbdApp`
- pane/session inspection exists in `HealthMonitor`
- provider recovery hooks exist through `ensure_pane()`
- runtime relaunch support exists in the runtime launch path

But there is one critical gap:

- recovery is not owned by a continuous daemon supervision loop

Current behavior:

- `HealthMonitor` can detect pane death and sometimes repair bindings
- further recovery is mainly attempted when a new job is about to start

Target behavior:

- daemon heartbeat itself must reconcile desired agents continuously

This gap is the main reason the current system can appear to "know how to recover" but still fail to keep idle agents mounted after pane death.

## 10. Phased Delivery

### Phase A: Contract Preservation

- keep one authoritative backend per anchor
- keep config as desired-agent authority
- keep residue from blocking kill
- stop silent authority drift

### Phase B: Runtime Supervision Loop

- add daemon-owned reconcile loop for all desired agents
- recover pane death without waiting for job start
- persist supervision state and retry/backoff

### Phase C: Keeper

- add project-scoped keeper
- restart `ccbd` after crash
- respect shutdown intent so `ccb kill` remains authoritative

### Phase D: Unified Reports

- startup report
- supervision event log
- shutdown report
- read paths consume reports instead of inferring partial truth ad hoc

## 11. Acceptance Matrix

The design is not complete until the following scenarios are automated and green.

Anchor and config:

- `.ccb` missing
- `.ccb` empty
- `.ccb` exists with persisted state but missing config
- config malformed
- config changed while backend is alive

Backend ownership:

- healthy mounted daemon
- stale lease with dead pid
- mounted lease with dead socket
- healthy lease with config mismatch
- backend crash while keeper is active
- explicit `ccb kill` does not trigger keeper restart

Runtime supervision:

- stale binding on startup
- pane dies while agent is idle
- pane dies while agent has queued work
- `ensure_pane()` succeeds
- `ensure_pane()` fails and relaunch succeeds
- repeated relaunch failure enters backoff/recovering state
- an immediately live respawn remains probing and cannot drain queued work
- a healthy post-respawn observation completes only after the 90-second
  stability window
- six unstable respawns open the recovery circuit and repeated heartbeats add
  no further attempts
- explicit restart/remount clears the consecutive recovery circuit counter
- Claude missing-conversation repair removes only CCB-owned continuation state
- Codex managed-helper loss blocks rather than respawning forever
- crash artifacts remain bounded during repeated failure

Shutdown:

- normal `ccb kill`
- forced `ccb kill -f`
- unknown stale agent directories exist
- malformed runtime residue exists
- project-owned panes are removed
- backend lease ends unmounted

## 12. Change Discipline

If future work changes any of the following, this document must be updated in the same patch:

- who owns backend authority
- what defines desired agents
- whether daemon or job path owns runtime recovery
- whether keeper exists and what it is allowed to do
- what `ccb kill` guarantees
- what files under `.ccb/ccbd/` are authoritative

If implementation and this document disagree, the disagreement must be treated as an architecture issue, not hand-waved as an implementation detail.
