# Provider Authentication Inheritance Contract

## 1. Purpose

This document defines the non-drifting authentication and account-state
boundary for every provider process managed by CCB.

Provider-specific session contracts may narrow this contract, but they must not
weaken it. The user's provider state is inheritance input only. Agent-scoped
managed state is the only mutable authority selected by a managed process.

## 2. One-Way Authority

When authentication inheritance is enabled:

- CCB may read an allowlisted credential, account, or auth-selection artifact
  from the real user provider home or an external OS credential service.
- CCB may materialize an independent agent-scoped representation before
  process launch.
- A managed provider may refresh, replace, or delete only its managed
  representation.
- CCB and the managed provider must never write, rename, delete, chmod, or
  reconcile the source artifact from the managed representation.
- A managed logout must not log out the provider in the user's shell, IDE,
  another CCB agent, or another project.

The flow is strictly:

`source user state -> managed provider state -> managed provider process`

There is no reverse synchronization path. `inherit_auth=false` prevents a new
source projection; a provider-specific contract may preserve an already-private
agent login, but that state remains local and must not become a source alias.

A stopped manual Provider restart must re-run the normal managed launch
preparation before process creation. It must not replay a persisted shell
command as a substitute for re-reading current inherited auth/API state. A
conversation binding may be resumed only when the Provider-specific authority
fence proves it is compatible with the newly prepared generation. Conversation
clear remains local context management and is not required to activate a new
credential or route.

Authority is resolved per dimension in this order:

1. explicit Agent-local API, token, URL, route, or Provider profile state from
   `.ccb/ccb.config`;
2. current external Provider state for dimensions not owned explicitly; and
3. no credential or route when neither source supplies that dimension.

An explicit dimension must not be shadowed by ambient shell or Provider-home
state, and explicit failure must not fall back to ambient authority. A fully
stopped CCB/backend start reads a new external snapshot from the environment,
Provider home, and supported read-only credential services inherited by that
new backend process. It does not hot-mutate a running Provider generation.

External reads have three outcomes: `present`, `authoritative_absent`, and
`unknown_error`. Confirmed absence may remove only a managed projection whose
owner-only provenance record proves it came from that source. A malformed or
missing provenance record preserves unmarked Agent-private state. A read,
permission, parse, or credential-service error blocks the new generation and
must not be reclassified as logout or used to delete the last projection.

Provider authority and conversation identity are separate. Each Agent keeps a
stable CCB conversation id and an ordered authority-generation history. The
same proven authority may use Provider-native resume. A changed or unknown
authority must retain the old native transcript and binding as historical
evidence but may resume it only when the Provider-specific contract proves
compatibility. Otherwise CCB starts a linked continuation and leaves the old
transcript discoverable by the Provider's native history surface. This is not
permission to claim automatic transcript import when the Provider has no
qualified import mechanism.

## 3. Filesystem Boundary

Credential, token, account, auth-selection, browser-profile, and mixed
auth/config paths must be ordinary private paths inside the managed provider
home, except for the narrowly scoped Claude Keychain representation described
in section 5.

They must not be:

- a symbolic link, Windows junction, bind mount, hard link, or other writable
  alias to the source user state;
- a shared credential directory used by two managed agents;
- a fallback to the caller's `HOME`, `USERPROFILE`, XDG data/config/state
  roots, or provider-specific global home;
- placed in a shared or rebuildable cache.

Before materializing inherited state, CCB must detach a recognized legacy
destination alias without traversing or deleting its source. Source symlinks are
not credential sources. A destination symlink or hard link is broken before
CCB writes the projection, including when the current source artifact is
missing.

Mixed files such as provider config containing both plugin/config and login
fields must use an explicit allowlist. CCB copies only the fields required for
the selected inheritance policy and keeps the result inside the managed
boundary.

Replaceable credential projections must carry an owner-only provenance record
that names projected paths or fields without containing secret values. Source
absence, inheritance opt-out, and cleanup may remove only entries named by a
valid record. Unmarked files remain Agent-private or unknown and are preserved.

## 4. Process Environment Boundary

Visible panes and headless provider subprocesses must receive the same private
state boundary.

The launcher must overwrite, rather than merely default, every provider root
that could select mutable account state. This includes:

- `HOME`;
- `USERPROFILE` and relevant Windows roaming/local-app-data roots for Windows
  providers launched through WSL;
- XDG config, data, state, and cache roots when used by the provider;
- provider-specific home, config, session, storage, log, or database roots;
- a provider-supported file-credential switch when its default may select an
  OS credential backend.

Provider runtime roots inherited from the caller environment are contamination,
not launch authority. `WSLENV` may forward only managed values selected by the
launcher.

When CCB itself is invoked from a managed provider pane, daemon and tmux
control-plane children must remove the pane's provider session markers, managed
roots, credential-store switches, and injected API authority. They must recover
the real source user `HOME` and any XDG roots that pointed into managed provider
state. Nonstandard managed homes are identified by CCB caller/session markers,
not only by path shape. If the operating-system account home cannot be resolved,
startup fails closed instead of treating the managed home as an inheritance
source. A managed process environment is never a reverse inheritance source.

Explicit API keys, URLs, profiles, or provider routes remain agent-local
authority. They must not rewrite global login files and must not be shadowed by
an inherited global credential copy.

For managed Claude, any key exported by `agents.<name>.env` must be removed
from the inherited `~/.claude/settings.json` `env` projection before launch.
An explicit Agent or Provider-profile `ANTHROPIC_BASE_URL` likewise suppresses
the inherited settings route. This prevents Claude's settings layer from
shadowing the higher-priority CCB authority while leaving unrelated inherited
settings intact.

A user-authored provider command wrapper that resets a protected root or
credential-store switch after CCB constructs the command is an explicit escape
from managed isolation. CCB must not add such an escape itself, and diagnostics
should report the external override without reading its secrets.

## 5. OS Credential Services

The shared keyring reader exposes read operations only:

- macOS uses `security find-generic-password`;
- Node keytar compatibility calls only `getPassword`;
- Linux Secret Service compatibility uses `secret-tool lookup`.

It does not expose set or delete operations.

Claude is the sole current exception that needs a writable OS credential
representation on macOS. CCB may:

- read the user's ordinary Claude services as source authority;
- write or delete only an agent-derived service name of the form
  `Claude Code-credentials-<agent-home-hash>` or its custom-OAuth equivalent;
- refuse the operation if the derived name equals any external source service.

Managed Claude must set `CLAUDE_CONFIG_DIR` and
`CLAUDE_SECURESTORAGE_CONFIG_DIR` to its private `.claude` directory and disable
both interactive login and logout commands. It must never copy
`com.apple.security.plist`, link `Library/Keychains`, or add/delete the user's
ordinary Claude Keychain services.

Gemini, Cursor, and Droid may read known external keyring entries only to
materialize provider-supported files inside their private managed homes. Their
managed processes are then forced to file storage and never select the source
keyring. If conversion is unavailable or invalid, CCB leaves that managed
provider unauthenticated instead of attaching the global credential backend.

AGY `1.1.13` exposes no public token-storage switch. Before every managed AGY
launch, CCB therefore refreshes AGY's own recent-keyring-failure marker at
`<managed-home>/.gemini/antigravity-cli/cache/antigravity-keyring-unavailable`.
AGY then selects its file token store immediately instead of attempting the OS
keyring first. The marker is an owner-only regular file under the private
managed home; CCB must detach any legacy link at that path and must never read,
create, or refresh the corresponding path in the source user home.

## 6. Built-In Provider Requirements

| Provider | Managed account authority | Required isolation behavior |
| --- | --- | --- |
| Claude | private `.claude` files plus an agent-namespaced macOS service | private `HOME`/Claude roots; disable login/logout; external services read-only |
| Codex | private `CODEX_HOME` auth/config/sidecars | private session and SQLite roots; WSL `USERPROFILE` pinned |
| Gemini | private `.gemini` OAuth/account/encrypted files | `GEMINI_FORCE_FILE_STORAGE=true` and `GEMINI_FORCE_ENCRYPTED_FILE_STORAGE=true`; external keyring read-only migration |
| OpenCode | private XDG data/config/state and structured storage roots | auth/account files are one-way copies; storage/log writers stay private |
| Droid | private `<managed-home>/.factory` v2 auth files | `FACTORY_DISABLE_KEYRING=true`; known keyring v2 material is converted to a private key file |
| AGY | private `.gemini` and `.antigravity` trees | no source symlink or Windows junction; allowlisted file copies only; refresh the private AGY keyring-bypass marker before launch |
| Qwen | private `QWEN_HOME` OAuth/account files | both Qwen file-storage switches enabled |
| Cursor | private platform-specific `cursor/auth.json` | `AGENT_CLI_CREDENTIAL_STORE=file`; macOS token services are read-only import sources |
| Copilot | private `COPILOT_HOME` auth-bearing config and secret trees | `COPILOT_DISABLE_KEYTAR=1`; private cache root |
| Kiro | private auth/config files and filtered SQLite snapshot | source database opened read-only; macOS fails closed while no private credential-store switch exists |
| Qoder / QoderCN | private documented `--config-dir` and `.auth` tree | visible and headless processes use the same exact private root |
| Kimi / DeepSeek / MiMo / Grok | provider-specific private homes | allowlisted auth/config files copied in one direction |
| OMP | private `.omp/agent` selected by `PI_CODING_AGENT_DIR` | auth-capable YAML/legacy config is copied one way only when both config and auth inheritance are enabled; `agent.db` is opened read-only and rebuilt as an agent-private auth-only snapshot with cache, history, statistics, blocks, and refresh leases removed |
| Crush / Pi / Z.ai | private HOME/XDG/provider roots | allowlisted auth records copied in one direction; unknown formats remain private-login only |

New built-in providers must declare their mutable account roots and credential
backend before they are accepted into the managed launcher registry. If a
release cannot isolate a provider's credential writes, that platform/provider
combination must fail closed.

## 7. Diagnostics And Cleanup

Diagnostics may report projection status, source/target path classes, file
names, sizes, timestamps, and hashes needed to prove isolation. They must never
export credential contents or Keychain payloads.

Cleanup may remove managed credential copies and agent-namespaced credential
entries after the owning provider is stopped. It must not traverse source
aliases or remove external user state.

## 8. Verification

Provider isolation tests must cover the applicable boundaries:

- materialize inheritance from a source fixture into a private target;
- mutate or remove the target as a simulated refresh/logout and prove the
  source fixture is unchanged;
- prove destination symlinks, hard links, or junctions are detached without
  changing their source;
- prove OS credential import uses read-only source operations and any Claude
  write/delete targets only an agent-derived service;
- prove visible and headless launches select managed roots and file-storage
  switches;
- prove WSL launches pin managed Windows-facing roots;
- prove auth files and secret fields are absent from diagnostics exports.
- prove explicit CCB API/route authority suppresses competing ambient
  credential and route state;
- prove a new stopped launch observes changed external state while source
  bytes, mode, and timestamps remain unchanged;
- prove `unknown_error` preserves the prior managed projection and blocks the
  new launch rather than acting as logout;
- prove same-authority resume and incompatible linked continuation retain one
  stable CCB conversation id and keep historical native transcripts visible.

Tests must not validate this contract by logging in to or logging out of a real
user account.
