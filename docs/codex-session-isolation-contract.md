# Codex Session Isolation Contract

## 1. Purpose

This document defines the non-drifting contract for `ccb`-managed Codex home and session isolation.

It is the authoritative design anchor for:

- `codex` startup environment under `ccb`
- agent-scoped Codex provider state layout
- Codex home and session root selection and persistence
- Codex bootstrap binding vs bound-session reading
- isolation from non-`ccb` Codex conversations

This document complements, but does not replace, the project startup contract in
[docs/ccbd-startup-supervision-contract.md](/home/bfly/yunwei/ccb_source/docs/ccbd-startup-supervision-contract.md).
Storage class naming, diagnostics classification, shared-cache eligibility, and
cleanup sequencing for managed Codex files are defined by
[docs/ccb-provider-state-storage-boundary-plan.md](/home/bfly/yunwei/ccb_source/docs/ccb-provider-state-storage-boundary-plan.md).

Detailed implementation sequencing lives in
[docs/codex-managed-home-isolation-plan.md](/home/bfly/yunwei/ccb_source/docs/codex-managed-home-isolation-plan.md).

## 2. Identity Model

`ccb` must treat these identities as distinct:

- `agent identity`
  - project anchor + logical agent name + provider
- `runtime generation`
  - one launch generation, currently represented by `ccb_session_id`
- `provider conversation identity`
  - the concrete Codex conversation, represented by `codex_session_id`

`work_dir` is context only. It must not be treated as the primary identity for a managed Codex agent.

`CODEX_HOME` is the managed provider-state boundary. `CODEX_SESSION_ROOT`
is derived state inside that boundary, not an independent isolation authority.

Verified operational constraint:

- with `codex-cli 0.121.0`, setting only `CODEX_SESSION_ROOT` is not sufficient to contain Codex conversation logs
- setting an isolated `CODEX_HOME` is required for managed Codex logs and session state to remain under `ccb` authority

## 3. Storage Contract

For a managed Codex agent named `<agent>`:

- runtime artifacts live under:
  - `.ccb/agents/<agent>/provider-runtime/codex/`
- stable provider state lives under:
  - `.ccb/agents/<agent>/provider-state/codex/`

The canonical managed Codex runtime artifact layout includes at minimum:

- `.ccb/agents/<agent>/provider-runtime/codex/completion/`
- `.ccb/agents/<agent>/provider-runtime/codex/bridge.log`

By default, the managed Codex home is:

- `.ccb/agents/<agent>/provider-state/codex/home/`

By default, the managed Codex session root is derived from that home:

- `.ccb/agents/<agent>/provider-state/codex/home/sessions/`

The managed `sessions/` tree is a first-class namespace, not disposable residue:

- Codex may automatically continue the most recent conversation it finds inside
  the active managed `sessions/` tree even without an explicit `resume` command
- therefore `ccb` must treat the active `sessions/` tree as route-bound
  authority and not merely as log storage
- when route authority becomes incompatible, `ccb` must rotate the current
  `sessions/` tree out of the active namespace before starting a fresh
  conversation

If the effective Codex home is explicitly overridden by a provider profile, the effective session root must still be:

- `<codex_home>/sessions/`

Provider-profile runtime homes are explicit authority only when they preserve the managed isolation contract. Non-explicit Codex provider profiles must use the agent-scoped managed home under `.ccb/agents/<agent>/provider-state/codex/home/`; startup migrates old default `.ccb/provider-profiles/<agent>/codex/` runtime-home data into that managed home and rewrites persisted `codex_home`/`codex_session_root` authority. Two configured Codex agents must not resolve to the same effective `codex_home` unless a future explicit shared-home feature declares and validates that weaker isolation mode.

Legacy provider-profile migration must validate persisted Codex session
authority before moving session material. Missing, malformed, or non-matching
authority must leave the legacy tree in place rather than moving files and
breaking recovery. Migration must also leave the legacy tree untouched when the
agent runtime authority still points at a live non-terminal provider runtime
process, or when a transitional runtime state lacks usable pid evidence.
Migrated and skipped outcomes must be diagnosable through project-local agent
events.
After a successful move, startup refreshes current
config/auth/plugin projection from the active profile/source home so stale
legacy projected files cannot override current `inherit_auth` or mix plugin
bundle versions; migrated plugin projection is discarded before the active
source plugin bundle is projected.

The managed session file must persist:

- `codex_home`
- `codex_session_root`
- `codex_session_id` once bound
- `codex_session_path` once bound
- `codex_provider_authority_fingerprint` for the launch-time route authority
- `codex_session_authority_fingerprint` once a concrete bound session is known under an explicit route
- home-level session-namespace authority under the managed Codex home so startup
  can detect whether the active `sessions/` tree is compatible before launch

These fields are authority for managed Codex runtime recovery.

For new managed launches, `codex_home` is mandatory. A session file without
`codex_home` is legacy evidence that must be migrated or rejected before it can
be used as normal managed authority.

Credential, config, and memory projection is not conversation identity. `ccb`
may project the user's source Codex credentials and config into the private
managed home so the provider can authenticate, but projected credential files
remain secret material and must not be exported by diagnostics. For Codex
official-login auth, copied file-backed `auth.json` credentials are only safe
for a single serialized Codex stream; multiple concurrent managed Codex agents
must not rely on independent copies of the same ChatGPT refresh token. When a
Codex profile sets `inherit_auth=false` without an explicit agent API key, `ccb`
must not copy global source-home credentials and must preserve any existing
agent-local `auth.json` in the managed Codex home so operators can maintain
one official-login auth stream per agent. The managed home may also project auth
sidecar files referenced by source `config.toml`, such as
`company-codex-api-key`, `company-codex.config.toml`, or other safe
auth/key/token filenames at the source Codex home root. These sidecars and the
`.ccb-auth-projection.json` evidence manifest are secret, agent-local startup
material. They are normally refreshed during managed-home materialization. If
a dead managed Codex pane is classified as `provider_auth_revoked`, recovery
may refresh only inherited auth files from the stable source Codex home before
respawning; it must not refresh config, plugins, memory, skills, commands, or
session authority in that recovery path.
The managed `CODEX_HOME/AGENTS.md` file is a CCB-generated memory bundle, not user data; it
combines filtered inheritable source-home `AGENTS.md`, project shared
`.ccb/ccb_memory.md`, and agent-private `.ccb/agents/<agent>/memory.md` when
present. Provider-native project `AGENTS.md` is excluded from the generated
bundle because Codex owns native project-memory loading. `inherit_memory=false`
must remove that generated `AGENTS.md` without disabling skill or command
projection.

Codex plugin-bundle projection is also not conversation identity, but it is
startup authority. Managed homes that preserve plugin-related source-home config
or command/skill behavior must also preserve the source plugin-bundle authority
required to satisfy that behavior.

## 4. Startup Contract

When `ccb` starts a managed Codex agent:

- it must explicitly set the effective `CODEX_HOME`
- it must explicitly set the effective `CODEX_SESSION_ROOT`
- it must ensure `CODEX_SESSION_ROOT == CODEX_HOME/sessions`
- it must create the managed home and session root before launching Codex
- it must materialize required Codex config, credential, and credential-sidecar
  projections into the managed home without treating them as session identity
- it must refresh only inheritable Codex config, auth, skills, commands,
  plugin-bundle, and memory projections into the managed home on each managed
  launch so source-home and project-memory updates become visible after restart
- for Codex official-login auth with `inherit_auth=false` and no explicit API
  authority, it must leave an existing managed-home `auth.json` untouched rather
  than deleting it, because that file may be an agent-local ChatGPT login stream
  with its own refresh token
- it must obtain `project_root`, `workspace_path`, and agent event-path context
  from the launcher's `prepare_launch_context` output rather than reverse
  engineering identity from provider runtime paths
- for plugin-bundle projection, startup must treat `.tmp/plugins/` plus
  `.tmp/plugins.sha` when present as one managed-home authority unit rather
  than cherry-picking only marketplace or manifest fragments
- when API inheritance is enabled, it must pass the current inheritable Codex API environment into the managed Codex process at launch time rather than relying on stale one-time projection state
- it may inherit user-session transport variables required for official-login
  connectivity, ChatGPT Apps/MCP connectivity, proxy routing, custom trust
  stores, browser launch, and WSL interop; examples include `HTTPS_PROXY`,
  `ALL_PROXY`, `NO_PROXY`, `CODEX_CA_CERTIFICATE`, `SSL_CERT_FILE`,
  `NODE_EXTRA_CA_CERTS`, `BROWSER`, `WSL_INTEROP`, and `WSL_DISTRO_NAME`
- user-session transport inheritance is not Codex session authority and must
  not allow caller-global runtime variables such as `CODEX_HOME`,
  `CODEX_SESSION_ROOT`, `CODEX_RUNTIME_DIR`, `CODEX_INPUT_FIFO`,
  `CODEX_OUTPUT_FIFO`, `CODEX_TERMINAL`, or `CCB_CALLER_*` to override the
  managed launcher's agent-scoped values
- when explicit agent API authority is configured, the managed home must not
  project global Codex config that can redefine provider routing; instead the
  managed `config.toml` must materialize an agent-local `model_provider` /
  `model_providers.<id>` authority derived from the explicit API route so Codex
  uses that route without consulting caller-global login state
- that explicit managed route must use Codex's standard custom-provider shape
  with `requires_openai_auth = false`
- when such a managed explicit route is present, startup must not also export
  `OPENAI_BASE_URL` or `OPENAI_API_BASE` into the managed Codex process, so
  route authority stays singular
- when an explicit Codex API key is configured, the managed home must not keep a
  copied global `auth.json` that could shadow that explicit key
- when an explicit Codex API key is configured, startup must materialize an
  agent-local `auth.json` derived from that key inside the managed `CODEX_HOME`
  so Codex request auth and managed route authority stay aligned
- startup must validate the active managed `sessions/` namespace against the
  current provider-route authority before launching Codex
- when that namespace is missing authority metadata or records a different route
  authority, startup must archive/rotate the current `sessions/` tree and clear
  stale bound-session fields before launch so Codex cannot auto-continue an
  incompatible conversation from the same home
- it must write the effective `codex_home` and `codex_session_root` into the agent session file
- it must create the canonical runtime `completion/` directory and `bridge.log` before the managed launch is considered bootstrap-ready
- it must not rely on global `~/.codex/sessions` as the default managed session namespace

Profile-provided runtime-home overrides are explicit forward authority only after uniqueness and boundary validation.

Absent such an override, the managed agent-scoped `CODEX_HOME` is the default authority.

Startup must fail clearly or mark the agent degraded when the requested managed
home cannot be prepared. It must not silently fall back to the caller's global
Codex home.

Project control-plane isolation rule:

- `ccb`, keeper, and `ccbd` must not inherit Codex runtime-local session variables from the caller shell
- examples include `CCB_SESSION_ID`, `CCB_SESSION_FILE`, `CCB_CALLER_*`, `CODEX_RUNTIME_DIR`, `CODEX_INPUT_FIFO`, `CODEX_OUTPUT_FIFO`, `CODEX_TERMINAL`, and equivalent runtime markers
- those variables belong only to the managed Codex runtime process that was launched for one agent generation
- a fresh project control-plane subprocess must treat such caller-shell variables as contamination, not startup authority
- only the managed agent session file and managed provider-state under `.ccb/agents/<agent>/provider-state/codex/` may define restore authority for a project-scoped Codex agent

## 5. Binding Contract

Managed Codex session reading has exactly two modes:

- `bootstrap`
  - used when the agent is not yet bound to a concrete Codex conversation
  - may scan for a candidate session only within that agent's own `codex_home/sessions`
  - may use `work_dir` only as a filter inside that managed home
- `bound`
  - used after `codex_session_id` or `codex_session_path` exists
  - must prefer the bound session
  - must verify the bound path remains inside that agent's managed home
  - must not drift to a newer workspace session outside explicit rebinding logic

Codex native subagent rollouts are never managed-agent session authority:

- a rollout whose `session_meta.thread_source` is `subagent`, or whose
  `session_meta.source` declares native subagent provenance, must be excluded
  from bootstrap scans, watchdog binding, persisted binding updates, session
  rotation, resume, and completion polling
- this exclusion applies even when the subagent rollout is newer, has the same
  workspace `cwd`, inherits the parent's conversation, or contains the same
  `CCB_REQ_ID`
- a legacy persisted binding that points at a native subagent rollout must be
  treated as invalid evidence and recovered by scanning only top-level
  rollouts inside the same agent-scoped managed home

Binding logic must not use shared `work_dir` as the cross-agent reconciliation key.

Asynchronous binding paths such as log watchdogs must also honor the managed home and must not rebind an already bound managed session to a different Codex conversation only because a newer workspace log appeared.

Managed readers must not widen their search to global `~/.codex/sessions`,
even when they can see a request anchor there. A request anchor observed outside
the managed home is a contract violation or legacy-leak diagnostic, not a
completion source.

For an accepted CCB job, the top-level Codex turn binding is immutable. The
first matching top-level `task_started.turn_id` binds the turn; assistant and
terminal events carrying another turn id must not update reply state or
terminalize the job. Codex collaboration `agent_message` and
`sub_agent_activity` records are internal provider evidence and must not enter
the caller-visible reply buffer. Caller-visible completion may come only from
the bound top-level turn's final assistant message or `task_complete`.

Native in-pane Codex session switches are supported only through the managed
session-switch boundary:

- users may still run Codex-native `/new` or `codex resume <session_id>` inside
  the managed pane
- ordinary log readers must remain bound to the current session and must not
  restore v5-style workspace-wide following
- the agent-local bridge binding tracker may observe the agent's own managed
  `codex_home/sessions` namespace and propose a session switch
- a proposed switch may be auto-committed only when the candidate log is inside
  the current agent's managed home, has the same `work_dir`, is newer than the
  current binding, is the only valid candidate, and belongs to the current
  runtime generation
- when a running job is visible, auto-commit additionally requires the candidate
  log to contain that job's request anchor
- for an active wrapped job, a unique newer top-level candidate inside the same
  agent-managed session root may be adopted by exact request-anchor ownership
  even when `/clear` produced stale or changed `cwd` metadata; this exception
  does not apply to idle discovery, subagent logs, external roots, ambiguous
  candidates, or text matches that are not the exact request anchor
- ambiguous candidates, missing anchors, external logs, and runtime mismatches
  must be recorded as switch diagnostics and must not change binding authority
- the switch committer is the only code path allowed to update
  `codex_session_id`, `codex_session_path`, old-binding metadata, and persisted
  resume command fields for a native in-pane switch
- switch persistence must be atomic and serialized across bridge/execution
  writers; stale writers must not overwrite a newer binding, and transfer or
  reader activation must occur only after durable persistence succeeds
- `doctor --fix` may reuse the same switch committer as a fallback repair path,
  but doctor must not define a separate binding authority model

Successful in-pane switches may produce a `rotated_in_process` runtime identity
state. That state means the live pane is still the managed pane and the session
file has been re-bound through the managed switch committer, even though the
original process command line may not literally contain
`codex ... resume <codex_session_id>`.

Runtime pane reuse is a separate proof obligation from session-file binding:

- a live tmux pane is not sufficient proof that the managed Codex agent is attached to the bound provider conversation
- when `codex_session_id` exists, startup may reuse an existing live pane only if the live provider process identity proves it is running `codex ... resume <codex_session_id>`
- if the live process identity is missing, unknown, or proves a different/non-resume Codex command without a committed managed in-pane switch, startup must reject that pane as reusable evidence and relaunch through the normal managed start command
- the persisted `start_cmd` or `codex_start_cmd` is desired launch authority, not proof that the current pane process was launched with that command
- relaunch after identity mismatch must preserve the agent-scoped `codex_home`, derived `codex_session_root`, and bound `codex_session_id` so ordinary `ccb` restores history while `ccb -n` remains the explicit fresh-start path
- after a dead pane reports revoked provider auth, recovery may respawn once
  only when `inherit_auth=true`, the stable source-home `auth.json` is valid,
  and its auth projection differs from the managed home
- missing, invalid, same-path, or unchanged source auth must block pane respawn
  and replacement-pane creation with an actionable error; recovery must not
  loop on the same revoked credential
- a blocked dead pane must persist `pane_recovery_block` in the managed session
  record so heartbeat does not repeatedly capture the same crash or retry the
  same credential; a successful live pane or a normal remount clears the block
- `inherit_auth=false` and explicit API authority must preserve their existing
  agent-local credential boundary and must not be overwritten from global auth
- when the current explicit agent-local Codex provider authority differs from
  the provider authority recorded for the last managed session, startup must
  skip `resume` and start a fresh Codex conversation inside the same managed
  home rather than reattaching a session created under a different route
- managed `CODEX_HOME/AGENTS.md` memory projection fingerprints are diagnostic
  and freshness metadata, not conversation identity; a changed memory projection
  must not by itself skip `resume`, archive the active `sessions/` namespace, or
  clear the bound Codex conversation
- if updated project memory must become the initial prompt context for Codex,
  that is an explicit fresh-start or session-switch choice rather than implicit
  restart behavior
- for explicit managed routes, launch-intent fingerprint alone is not sufficient
  proof for `resume`
- a bound Codex conversation may be resumed only when
  `codex_session_authority_fingerprint` matches the current explicit route
- if a legacy session file has only launch fingerprint but no bound-session
  fingerprint, startup must treat that binding as untrusted and force one fresh
  conversation so the binding can be re-established cleanly
- forcing a fresh conversation under the same managed home also requires a
  compatible active `sessions/` namespace; skipping explicit `resume` alone is
  not sufficient because Codex may auto-continue the most recent conversation in
  that namespace

Legacy agent-only reuse exception:

- when the project is in an agent-only legacy layout with `cmd` disabled and the instance-scoped session file does not declare a conflicting tmux socket, startup may still reuse that binding even if live Codex identity proof is temporarily unavailable or `unknown`
- that exception does not apply when live identity explicitly proves `mismatch`

## 6. Isolation Contract

By default:

- two `ccb`-managed Codex agents must not share a Codex home
- two `ccb`-managed Codex agents must not share a session root
- two `inplace` Codex agents may share the same `work_dir`, but must still remain isolated
- a non-`ccb` Codex conversation started in the same working directory must not be implicitly adopted by a managed agent

External Codex conversations may only be adopted through an explicit future bind/import flow.

Therefore `ccb` and a manually-run `codex` command in the project directory are
separate worlds:

- the manual command may use the user's normal `~/.codex`
- the managed agent must use its agent-scoped private `CODEX_HOME`
- shared `cwd` or matching request text does not merge their conversations

## 7. Compatibility Contract

To avoid breaking restore for older managed sessions, startup may reuse and
migrate a previously recorded Codex home when it is already persisted in the
agent session authority.

Compatibility reuse is evidence-driven migration support only. New managed launches must write the current explicit `codex_home` and derived `codex_session_root` contract back to authority.

Legacy root-only sessions are not a long-term operating mode:

- if the old root is project-local managed state, startup may relocate or bridge it into the canonical private home and then rewrite authority
- if the old session evidence points to global `~/.codex/sessions` or another non-managed home, normal startup must not silently adopt it
- any import of leaked or external global Codex sessions requires an explicit future repair/import flow

`ccb -n` remains a valid way to rebuild a project with fresh managed homes. The
first post-reset startup must force `restore=false` as defined by the startup
contract, so old provider-global history is not silently reattached.

## 8. Diagnostics Contract

When managed Codex state lives inside the project under `.ccb/agents/<agent>/provider-state/codex/`, diagnostics and support bundles should treat that provider-state tree as project-local evidence.

Diagnostics export should include:

- managed home summary metadata
- managed session-root logs and related project-local session files
- non-secret isolated `config.toml` overlays when present
- non-secret plugin projection summary metadata when available
- explicit contract-violation evidence when Codex writes outside the managed home

Diagnostics export must exclude copied credential files such as `auth.json`.

Runtime diagnostics should distinguish these cases:

- `unbound_waiting_for_managed_log`
- `managed_home_empty_after_launch`
- `codex_wrote_outside_managed_home`
- `bound_session_outside_managed_home`
- `legacy_root_only_session_requires_migration`

These states are diagnosable failures or migrations. They are not permission to
scan global provider history as a fallback.
