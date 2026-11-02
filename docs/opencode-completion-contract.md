## Opencode Completion Contract

This document defines the authoritative completion contract for the `opencode`
provider in `ccb_source`.

Managed OpenCode startup/config isolation is also anchored here because
OpenCode does not yet have a separate session-isolation contract.
Authentication projection and logout isolation must also satisfy
[docs/provider-auth-inheritance-contract.md](/home/bfly/yunwei/ccb_source/docs/provider-auth-inheritance-contract.md).

### Authority

- `CCB_REQ_ID` is a request-binding marker only.
- `CCB_DONE` is not part of the `opencode` completion authority.
- The authoritative runtime evidence comes from native structured session and
  message records. Version 1 / legacy completion and version 2 native idle
  completion have separate contracts below.

### Request Binding (v1 / legacy)

- A managed `opencode` job writes `CCB_REQ_ID: <job_id>` into the user prompt.
- The reply belongs to that job only when the observed assistant message points
  to the user message through `parentID` or `parent_id`, and the parent prompt
  resolves back to the same `CCB_REQ_ID`.
- Session identity and `session_id_filter` scope the storage reader, but they do
  not replace request binding.

### Completion (v1 / legacy)

- An `opencode` assistant reply becomes complete only when the matched assistant
  message has `time.completed`.
- Before `time.completed`, reply text may be surfaced as an in-progress preview,
  but it must not finalize the job.
- The execution adapter emits a `TURN_BOUNDARY` with reason
  `assistant_completed` when a matched assistant reaches completion.

### No-Wrap Mode (v1 / legacy)

- `no_wrap` intentionally skips managed request binding.
- In `no_wrap`, `opencode` may still surface reply previews and completed
  replies from the bound session, but the result is degraded because it is not
  anchored by `CCB_REQ_ID`.

### Managed Config Projection

- CCB launches managed OpenCode with `OPENCODE_CONFIG` pointing to a generated
  config file under `.ccb/agents/<agent>/provider-state/opencode/opencode.json`.
- The generated config is CCB-owned projected config, not user-editable source.
- CCB must not rewrite `project_root/opencode.json`; when that file exists,
  startup reads it and merges its fields into the generated config.
- User project config wins for all fields except `instructions`.
- `instructions` is merged as a stable union of user entries plus CCB-generated
  entries:
  - `.ccb/runtime/memory/<agent>.md` when inherited memory is enabled.
  - `.ccb/runtime/skills/<agent>/opencode/ask.md` when inherited skills are
    enabled.
- Invalid project `opencode.json` must not block startup; CCB writes a minimal
  generated config and records `opencode_config_merge_failed` in agent events.
- `inherit_memory = false` omits the memory bridge but does not disable
  inherited skill instructions. CCB removes the generated config and omits
  `OPENCODE_CONFIG` only when both inherited memory and inherited skills are
  disabled.
- Project `AGENTS.md` remains an OpenCode-native project instruction source and
  is excluded from the CCB-generated runtime memory bundle to avoid duplicate
  loading through both native discovery and the generated instructions bridge.
- `.ccb/agents/<agent>/memory.md` remains a CCB bundle input; CCB does not edit
  project `AGENTS.md` during OpenCode startup.

### Managed State And Authentication

- Managed OpenCode must use agent-local `HOME`, `XDG_CONFIG_HOME`,
  `XDG_DATA_HOME`, `XDG_STATE_HOME`, and `XDG_CACHE_HOME` roots under
  `.ccb/agents/<agent>/provider-state/opencode/`.
- Its structured session storage and logs must resolve from the persisted
  agent-local `OPENCODE_STORAGE_ROOT` and `OPENCODE_LOG_ROOT`, not from an
  import-time or caller-global `~/.local/share/opencode` path.
- `auth.json` and `account.json` may be inherited only as ordinary one-way
  copies into the managed XDG data root. Provider refresh or logout may change
  only those managed copies.
- Visible panes, headless execution, health inspection, and completion readers
  must use the same session-persisted managed roots.
- On WSL, managed Windows-facing `USERPROFILE`, `LOCALAPPDATA`, and `APPDATA`
  roots must not fall back to the user's global Windows OpenCode state.
- Startup must fail or remain on an unauthenticated private managed home if
  these roots cannot be prepared; it must not regain authentication by writing
  to the user's global OpenCode data directory.

### Managed Session Startup

- CCB may inject OpenCode `--continue` only when the effective restore policy is
  not fresh.
- `ccb -n`, `--new-context`, and agent `restore = "fresh"` are fresh launches
  and must not inject `--continue`.
- Explicit OpenCode session selectors in the configured command or
  `startup_args` are authoritative. If `opencode` is already launched with
  `--session`, `-s`, `--continue`, or `-c`, CCB must not prepend another
  automatic `--continue`.

### Non-Goals

- Quiet terminal periods are not completion authority for `opencode`.
- `CCB_DONE`, terminal idle time, or pane text markers must not be reintroduced
  as the primary completion path for `opencode`.

### Native State And Safe Legacy Reads

- Managed launches may explicitly capture `CCB_OPENCODE_API_URL` as
  `opencode_api_url` in the CCB-owned session binding. This is an existing
  loopback OpenCode service origin, not a request to start another service.
  The TUI and reader must refer to the same native session and workspace.
- Native observation requires an already bound exact `opencode_session_id`;
  it never selects the newest session from a shared server. Only
  loopback HTTP origins without credentials, path, query, or redirects are
  supported. Optional Basic authentication references an explicitly named
  environment variable; authentication is never read from Provider files.
  A native API error never silently falls back to database storage.
- The initial native adapter supports the v1 structured service endpoints
  `GET /session/{sessionID}` and `GET /session/{sessionID}/message`, with
  session/directory, message/part, parentID, request marker and time.completed
  validation. Idle state does not authorize completion.
- OpenCode 2.2.x uses the opt-in native task transport below; it never opens
  v2 SQL storage or fabricates v1 parentID links.
- Legacy database observation never opens the Provider's database or WAL/shm
  with SQLite. It copies main+WAL into a private temporary directory and
  performs query-only reads there. A changed copy is discarded. This adds
  disk I/O; native observation is preferred where a compatible service and
  exact session binding exist. The external shm is never copied or opened.
- Missing legacy tables/columns are an explicit UnsupportedOpenCodeSchema,
  not an empty reply. Initial capture occurs before prompt submission, so
  incompatible storage fails without sending business work (issues #401/#402).


### OpenCode 2.2.x Native Task Transport

The native v2 transport is opt-in, qualified against OpenCode 2.2.4. Set
`CCB_OPENCODE_API_VERSION=2`, `CCB_OPENCODE_API_URL=http://127.0.0.1:<port>`
and, for an authenticated service, `CCB_OPENCODE_API_PASSWORD_ENV=<variable>`
before the managed launch. The named variable supplies the existing service
password; CCB persists only its name. Configure startup arguments with an exact
`--session ses_...`; optional `--server` must match the API origin. Automatic
continue and standalone launches are rejected. CCB neither starts nor selects
an external service/session automatically.

- Verify `/api/info` reports 2.2.x and the exact native session has the expected
  workspace before submitting. Missing bindings fail before task admission.
- Submit once to `/api/session/{id}/prompt` with a new native input ID, queue
  delivery, job ID metadata and a prompt hash. A receipt means admission only.
  Lost or malformed receipts never authorize replay or terminal injection.
- Read paginated native messages, using inbox evidence for pending admission.
  Validate the exact user input ID, metadata and prompt hash. Inspect only the
  subsequent interval through the first native idle. Foreign user, synthetic,
  shell, skill or workspace-changing inputs make attribution ambiguous and
  terminate observation as incomplete.
- Native assistant `time.completed` is a step boundary and cannot finish a
  task. A successful native idle must also have a final completed assistant,
  visible text, `finish=stop`, and no unresolved tools or running compaction.
  Failure/interruption produces explicit terminal status; empty or inconsistent
  success produces incomplete. No fake v1 parent or TURN_BOUNDARY is emitted.
- Native messages may expose only completed steps; live streaming previews are
  best effort. TUI pane liveness is not completion authority for native work.
- Network failures have a continuous 30-second observation grace period, reset
  by a successful read. Unknown admission is bounded without replay; an exact
  pending inbox entry may wait for existing work to finish.
- Cancellation requests deletion of only the exact pending inbox input and
  checks for raced native delivery. Its idempotent 204 is not proof that an
  active task stopped. OpenCode 2.2.x has
  no atomic active-turn interrupt fenced by expected input ID. Active native
  cancellation therefore reports unsupported and warns that native work may
  continue; CCB never substitutes session-wide interruption or terminal keys.

This transport does not change Mobile discovery or automatically migrate
existing configured agents. Restart-time execution observation restore remains
unsupported; never automatically replay an uncertain native admission. Enable
it only with the matching explicit native service and session binding.
