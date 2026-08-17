# Integration Design

Date: 2026-06-13

## Provider Keys And Commands

| Provider key | Default command | Override env |
| :--- | :--- | :--- |
| `kimi` | `kimi` | `KIMI_START_CMD` |
| `deepseek` | `deepcode` | `DEEPSEEK_START_CMD` |
| `mimo` | `mimo` | `MIMO_START_CMD` |
| `qwen` | `qwen` | `QWEN_START_CMD` |
| `copilot` | `copilot` | `COPILOT_START_CMD` |
| `cursor` | `agent` | `CURSOR_START_CMD` |
| `kiro` | `kiro-cli` | `KIRO_START_CMD` |
| `crush` | `crush` | `CRUSH_START_CMD` |
| `pi` | `pi` | `PI_START_CMD` |
| `grok` | `grok` | `GROK_START_CMD` |
| `dsh` | `dsh` | `DSH_START_CMD` |

The `deepseek` provider key follows user intent and model family language; the
actual CLI command remains `deepcode` because that is the DeepSeek documented
terminal integration.

The separate `dsh` key is the official DeepSeek Harness. It is service-backed:
the executable starts `dsh web`, while CCB uses the structured loopback Web
carrier for requests and native events.

The `cursor` provider key follows product naming; the default executable is
`agent` because that is what the official Cursor Agent installer exposes.

## Runtime Model

These providers enter CCB as optional built-in managed providers:

- `kimi` and `deepseek` use `ProviderManifest` `SESSION_BOUNDARY`.
- `kimi` uses `CompletionSourceKind.SESSION_EVENT_LOG`.
- `deepseek` uses `CompletionSourceKind.SESSION_SNAPSHOT`.
- `mimo` uses `CompletionFamily.STRUCTURED_RESULT` and
  `CompletionSourceKind.STRUCTURED_RESULT_STREAM` for ask execution because
  terminalization comes from `mimo run --format json` result events.
- `ProviderRuntimeLauncher` uses `simple_tmux`.
- `ProviderSessionBinding` uses `.kimi-session`, `.deepseek-session`, and
  `.mimo-session`.
- Startup command supports `spec.startup_args`, `spec.env`, caller context env,
  and `provider_command_template`.
- Provider start-command override env vars such as `QWEN_START_CMD` and
  `KIRO_START_CMD` are control-plane inputs. They must be passed from the CLI
  process into ccbd so background startup and source-runtime smoke can use the
  same command authority as foreground launchers. Provider home/session authority
  remains isolated and must not be broadly passed through by prefix.

Most next-wave runtimes split visible pane startup from ask execution:

- `qwen`, `copilot`, and `grok` use per-job subprocess
  execution with JSONL/stream-json parsing.
- `crush` and `kiro` use per-job subprocess execution with process exit plus
  stdout as the completion signal.
- Visible panes still use simple tmux launchers for user observation and
  runtime maintenance.
- Cursor is a deliberate exception: new asks execute in the exact managed
  visible pane, defer until stable idle, bind to an exact request anchor in a
  top-level transcript, and terminalize only from the bound turn's
  `turn_ended`. The earlier structured subprocess is an explicit headless
  rollback path.
- Pi is also a deliberate exception: new asks execute in its managed visible
  pane and an official Pi extension writes exact-request lifecycle evidence.
  The earlier per-job structured subprocess remains the explicit headless
  rollback and persisted `pi_run` compatibility path.
- Shared native CLI launchers may derive visible-pane arguments from prepared
  provider-state. Crush uses this to start visible panes with
  `--data-dir <provider-state>/data`, matching the state isolation used by
  `crush run`.
- Shared native CLI launchers may also derive visible-pane env vars from
  prepared provider-state. Pi uses this to set `PI_CODING_AGENT_DIR`,
  `PI_CODING_AGENT_SESSION_DIR`, `PI_SKIP_VERSION_CHECK`, and `PI_TELEMETRY`.
- Grok uses the shared native CLI launcher with per-agent managed `HOME` and
  visible `--no-auto-update`, while CCB ask execution runs official headless
  `grok -p` jobs and reads streaming-json output artifacts.
- Existing partial backend directories for `qwen` and `copilot` have been
  upgraded to modern backend shape before registration:
  `manifest.py`, `launcher.py`, `execution.py`, and tests.
- DSH is not part of the shared interactive native-CLI execution shape. The
  current POSIX launcher may carry its signal-forwarding Web host wrapper in a
  managed pane, but job submission and completion use HTTP/WebSocket only.
  This avoids claiming a headless service supervisor that the mounted-runtime
  lifecycle does not yet implement.

## Kimi Restart Session Authority

Kimi conversation continuity is agent-scoped even when multiple Kimi agents
share one in-place work directory. A fresh managed pane receives neither
`--continue` nor an invented native session id. Once the completion reader
observes that agent's exact `CCB_REQ_ID` in a native `wire.jsonl`, it persists
the native session id/path, normalized work directory, legacy Kimi share root,
current `.kimi-code` state root, storage layout, and observation time in
`.kimi-<agent>-session`. The CCB pane-launch id remains a separate
control-plane identity and is never passed to Kimi.

Each launch also persists a command template containing one CCB-owned
exact-session insertion point plus the configured Kimi capability command.
Manual restart and dead-pane recovery validate the agent/project/workdir/share
binding, exact non-symlinked native layout, and current long-option capability
before materializing `--session <owned-id>`. Missing, malformed, mismatched,
storage-drifted, or unsupported authority fails fresh and clears only the
carried binding; it does not inspect credentials or delete provider data.
Explicit user `--session`, `--resume`, `--continue`, and known versioned short
controls take precedence and never receive a second automatic selector.

This restart behavior is distinct from the Kimi manifest's
`supports_resume=false`: the manifest describes recovery of an interrupted CCB
job, while exact-session selection preserves the provider conversation between
managed pane launches.

## Completion Strategy

The current strategy uses provider-native session/event stores or structured
result streams:

1. Send a wrapped prompt to the managed provider pane.
2. The prompt contains `CCB_REQ_ID: <job_id>`.
3. Do not ask Kimi, DeepSeek/DeepCode, or AGY to print `CCB_DONE`.
4. Kimi polls both owned legacy `.kimi` and current `.kimi-code` `wire.jsonl`
   layouts, binds the turn by an exact leading `CCB_REQ_ID` header, emits
   `ASSISTANT_FINAL` from native text parts, and emits `TURN_BOUNDARY` on
   `TurnEnd`, successful terminal `step.end`, or a reply-bearing next-turn
   boundary. Once native evidence owns the anchor, pane scraping cannot
   override an in-progress native reply.
5. DeepSeek polls DeepCode `sessions-index.json` and session jsonl, binds the
   user message by `CCB_REQ_ID`, emits `ASSISTANT_FINAL` from assistant
   messages, and emits `TURN_BOUNDARY` on native `status=completed`.
6. AGY polls Antigravity transcript logs, binds `USER_INPUT` by `CCB_REQ_ID`,
   emits `ASSISTANT_FINAL` from model response events, and emits
   `TURN_BOUNDARY` when a completed response is observed. AGY prompt delivery
   is ready-gated: CCB defers the prompt while the Antigravity pane is busy,
   sends only after an empty input prompt is observed, diagnoses native
   coalesced `CCB_REQ_ID` rows, and uses stable pane fallback only when
   transcript persistence lags. Before launch, CCB refreshes AGY's own recent
   keyring-failure marker inside the managed HOME so the public `1.1.13` CLI
   selects file token storage immediately; no AGY storage setting is exported
   to other providers or written into the source user HOME.
7. MiMo asks run as native subprocesses using
   `mimo run --format json --dir <workdir>`. CCB emits `ASSISTANT_FINAL` from
   nested `part.text` events and emits `TURN_BOUNDARY` / terminal completed on
   `step_finish` with `part.reason=stop`.
8. Qwen asks parse `stream-json` or JSON output and terminalize from
   result/final assistant envelopes.
9. Qoder asks use documented print mode, an agent-local `--config-dir`, and a
   deterministic UUID session id. Only a non-error native `result` envelope
   with a normal stop reason completes the job; auth/error envelopes and a
   clean exit without `result` fail closed.
10. Cursor asks execute in the named visible pane, bind the exact request
   anchor in a top-level Cursor transcript, and terminalize only from a later
   matching `turn_ended`. `agent --print --output-format stream-json` remains
   the explicit headless rollback mode.
11. Copilot asks parse `--output-format json` JSONL in prompt mode and
   terminalize from the final prompt-mode result event.
11. Crush asks collect stdout from `crush run --quiet` and trust process exit;
   source evidence shows `crush run` itself exits only after a matching
   `RunComplete`.
12. Kiro asks initially collect stdout from `kiro-cli chat --no-interactive
   --wrap never` and treat process exit as completion until a stable structured
   chat event source is found.
13. Pi asks are dispatched to the managed visible pane and terminalize only
   from the exact bound extension `agent_settled` event. `turn_end`,
   `agent_end`, assistant text, and tool events are progress; the latest
   settled visible text is the reply. Persisted/headless rollback jobs retain
   the `pi --mode json` process-exit fence.
14. Grok asks parse official `grok --no-auto-update -p ... --output-format
   streaming-json --session-id <job>` output. CCB accepts both generic
   assistant/result envelopes and JSON-RPC style `session/update`
   `agent_message_chunk` events, terminalizing from native stop/end events or
   process exit with captured reply text.
15. DSH asks open the native event WebSocket first, submit with the CCB job id
   as `session.prompt` RPC id, bind the exact durable
   `user/message.source.rpcId`, and complete only from a committed non-empty
   same-turn assistant reply plus `turn/end(completed)`. Every other native
   terminal, missing anchor, malformed event, empty reply, process exit, and
   timeout fails closed. Restore observes the same persisted RPC without
   reposting.
16. Completed-native-empty replies are `incomplete` with
   `empty_provider_reply` diagnostics, not `completed`.
17. Long-running native CLI subprocesses terminalize with explicit
   provider-specific timeout reasons such as `qwen_run_timeout` and terminate
   the child process group instead of waiting only for the outer reliability
   fallback.

## Skill And Instruction Injection

Provider onboarding must include a capability-projection check in addition to
native completion detection:

- If the provider exposes native skills, use that native surface.
- If the provider exposes only instruction files/config, inject CCB ask guidance
  through that instruction surface.
- Do not ask the model to rediscover `ask` usage from memory alone when a
  provider-native or provider-supported projection path exists.

Current behavior:

- Kimi gets inherited CCB ask skill content from
  `inherit_skills/kimi_skills/ask/SKILL.md`. Startup materializes a managed
  skills root under `.ccb/agents/<agent>/provider-state/kimi/inherited-skills`
  and passes it to Kimi with `--skills-dir`. Because Kimi treats any
  `--skills-dir` as replacement for default discovery, CCB first passes
  existing default Kimi project/user skill directories, then appends managed
  inherited and role skill roots. CCB appends the packaged inherited root only
  when it created/owns that projection or adopted an exact source symlink; an
  unmarked directory or foreign marker is preserved and is not claimed as the
  packaged root.
- OpenCode does not expose a stable `--skills-dir` equivalent in the observed
  CLI help. CCB writes `.ccb/runtime/skills/<agent>/opencode/ask.md` and appends
  that path to generated `opencode.json.instructions` alongside the memory
  bridge.
- MiMo writes `.ccb/runtime/skills/<agent>/mimo/ask.md` and appends that path
  to generated `mimocode.json.instructions` alongside the memory bridge.
- Qwen should prefer native settings/instruction surfaces when confirmed;
  until then, inherited ask guidance can be injected through prompt wrapping
  while preserving `QWEN_HOME` isolation.
- Pi should prefer native skills/resources if CCB later projects richer ask
  guidance; first landing keeps prompt wrapping and isolates Pi global/session
  state with `PI_CODING_AGENT_DIR` and `PI_CODING_AGENT_SESSION_DIR`.
- Copilot startup inherits already-installed user plugins through the native
  `config.json.installedPlugins` surface: CCB allowlists plugin metadata,
  rebases `cache_path`, and copies each validated source tree into the
  agent-local `COPILOT_HOME`. Aggregate and per-tree markers permit refresh or
  cleanup only while both metadata and tree content remain unchanged; local
  divergence transfers ownership to the user. CCB does not copy auth,
  settings, permissions, sessions, plugin data, MCP secrets, or marketplace
  cache, and routes the latter to agent-local `COPILOT_CACHE_HOME`. Packaged
  ask guidance continues to use prompt wrapping until a separate CCB-owned
  Copilot plugin is explicitly designed.
- Cursor should project inherited ask guidance through repeatable
  `--plugin-dir` if the installed bundle accepts the same local plugin shape;
  otherwise use prompt wrapping in the first slice.
- Crush should use prompt wrapping first unless source validation confirms a
  stable skills/config path. `--data-dir` keeps any managed instructions inside
  provider state.
- Kiro should use prompt wrapping first because no stable skill/instruction
  projection surface has been confirmed for chat mode.
- Grok supports AGENTS.md, skills, plugins, hooks, and MCP according to official
  docs. The first CCB slice uses prompt wrapping plus managed `HOME`; richer
  Grok skill/plugin projection should be designed only after a local contract
  is verified.
- DSH receives optional user skills, Role skills, and required CCB control
  skills through its native `$DSH_HOME/skills` surface. Its generated CCB
  memory bundle uses `$DSH_HOME/AGENTS.md`; session/cache trees are not copied
  from the user's source home.
- `inherit_skills = false` disables inherited skill projection. For OpenCode,
  `inherit_memory = false` disables only the memory bridge; inherited ask
  instructions continue unless `inherit_skills = false` is also set.

## Config Boundary

Supported native-provider config:

```toml
[windows]
main = "kimi_agent:kimi, deep_agent:deepseek, mimo_agent:mimo"

[agents.kimi_agent]
provider = "kimi"

[agents.deep_agent]
provider = "deepseek"

[agents.mimo_agent]
provider = "mimo"
```

Next-wave provider config:

```toml
[windows]
main = "qwen1:qwen, cursor1:cursor, copilot1:copilot, crush1:crush, grok1:grok, kiro1:kiro, pi1:pi, research:dsh"

[agents.qwen1]
provider = "qwen"

[agents.cursor1]
provider = "cursor"

[agents.copilot1]
provider = "copilot"

[agents.crush1]
provider = "crush"

[agents.grok1]
provider = "grok"

[agents.kiro1]
provider = "kiro"

[agents.pi1]
provider = "pi"

[agents.research]
provider = "dsh"
model = "deepseek-v4-flash"
thinking = "high"
```

DSH supports `key`/`url` shortcuts through `DEEPSEEK_API_KEY` and
`DEEPSEEK_BASE_URL`. It also honors inherited user-owned DSH credentials and
API route according to provider-profile inheritance policy; CCB never obtains
credentials.

Not supported in first slice:

- `key` / `url` shortcuts for Kimi or DeepSeek.
- `key` / `url` shortcuts for MiMo.
- `key` / `url` shortcuts for Qwen, Cursor, Copilot, Crush, Grok, or Kiro.
- Automatic writing of `~/.deepcode/settings.json`.
- Automatic Kimi login.
- Automatic credential acquisition for Qwen, Cursor, Copilot, Crush, or Kiro.
- Automatic Grok login or SuperGrok/X subscription acquisition.

## Tests

Focused unit tests should cover:

- Optional provider registry includes `kimi`, `deepseek`, and `mimo`.
- Runtime specs include `.kimi-session`, `.deepseek-session`, and
  `.mimo-session`.
- Start command env overrides and default executables.
- Kimi startup includes existing default skill directories and materialized CCB
  skill directories as repeatable `--skills-dir` arguments, while skipping
  missing directories.
- OpenCode generated config preserves user instructions and appends memory and
  ask-skill instruction entries without duplication.
- MiMo generated config preserves user instructions and appends memory and
  ask-skill instruction entries without duplication.
- Session binding maps and runtime launcher maps include the native providers.
- Native readers parse Kimi `wire.jsonl`, DeepCode sessions, and AGY
  transcripts.
- MiMo execution parses `mimo run --format json` nested `part.text` and
  `part.reason=stop`.
- Provider adapters emit `SESSION_ROTATE`, `ANCHOR_SEEN`, `ASSISTANT_FINAL`,
  and `TURN_BOUNDARY` from native evidence.
- Provider adapters diagnose completed-native-empty replies and fail on missing
  runtime state.
- Config loader accepts agents using `provider = "kimi"` and
  `provider = "deepseek"` and `provider = "mimo"`.
- Optional provider registry includes `qwen`, `cursor`, `copilot`, `crush`,
  `grok`, `kiro`, and `pi`.
- Existing partial `qwen` and `copilot` backend packages are migrated to
  modern manifest/launcher/execution contracts while old protocol helpers
  remain only for compatibility tests.
- Qwen parser handles `stream-json` assistant and result envelopes.
- Cursor defaults to visible-pane execution, waits for stable idle, dispatches
  once, excludes stale/subagent transcripts, binds the exact request anchor,
  and requires a matching `turn_ended`; its headless rollback parser still
  handles `agent --print --output-format stream-json` envelopes.
- Copilot parser handles prompt-mode JSONL output.
- Crush execution treats nonzero exit as failure and zero exit/stdout as
  completion, with empty stdout producing an empty-reply diagnostic.
- Grok execution builds the official headless command and parses
  `session/update` chunk/stop events.
- Kiro execution treats nonzero exit as failure and zero exit/stdout as
  completion until a better native event source is confirmed.
- All next-wave adapters report provider-specific run timeouts and
  terminate the subprocess when `CCB_<PROVIDER>_RUN_TIMEOUT_S` is exceeded.
- Crush visible pane launch includes `--data-dir <provider-state>/data`.
- Native CLI provider-state classification covers session/cache/projected skill
  evidence for Qwen, Cursor, Copilot, Crush, Grok, Kiro, and Pi.
- DSH tests cover service launch without prompt input, loopback endpoint
  validation, exact RPC/turn completion, every native failure terminal,
  empty/reasoning-only output, process exit without native terminal,
  observer-only resume, native clear/compact control, managed home/authority,
  config UI, updater, and storage classification.

Source-runtime validation should run from `/home/bfly/yunwei/test_ccb2` using
`/home/bfly/yunwei/ccb_source/ccb_test` and isolated source home. Real CLI
help/version checks validate installability; CCB ask completion can use
provider command templates that point to deterministic stub TUIs when API
credentials are unavailable.
