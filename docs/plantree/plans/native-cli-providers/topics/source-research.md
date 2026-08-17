# Source Research

Date: 2026-06-13

## Kimi

Observed upstream:

- GitHub release source used for real validation:
  `MoonshotAI/kimi-cli` tag `1.47.0`.
- Earlier npm package probe: `@moonshot-ai/kimi-code@0.14.2`.
- Binary: `kimi`.
- Node engine: `>=22.19.0`.
- Official startup path: run `kimi` inside a project after installation and
  authenticate with `/login` or `kimi login`.
- CLI help also exposes `--prompt` for noninteractive prompt mode and
  `--output-format text|stream-json`.
- Kimi 1.47.0 exits with an error when launched with `--continue` in a workdir
  with no previous session, so CCB must not inject restore flags implicitly.
- Kimi 1.47.0 TUI echoes submitted prompt text before the assistant reply; CCB
  must ignore the prompt-echo `CCB_DONE` line and wait for the model's own
  done marker.
- Kimi 1.47.0 needs the TUI input area to be ready before CCB sends prompt
  text; prompt delivery immediately after pane creation can otherwise be
  printed before the welcome screen and not executed.
- Kimi 1.47.0 help exposes `--yolo`, `--yes`, `--auto-approve`, and `-y` for
  automatic approval. It rejects the older `--auto` flag at CLI parse time.
  CCB should inject `--auto-approve` for new Kimi versions while still
  recognizing user-provided `--auto` as an explicit legacy auto flag to avoid
  duplicate injection.
- Kimi 1.47.0 help exposes repeatable `--skills-dir DIRECTORY`, and documents
  that it overrides default discovery rather than appending to it. Official
  Kimi CLI skill discovery scans project `.kimi/skills`, `.claude/skills`,
  `.codex/skills`, `.agents/skills`, plus user `~/.kimi/skills`,
  `~/.claude/skills`, `~/.codex/skills`, `~/.config/agents/skills`, and
  `~/.agents/skills`. CCB therefore must pass existing default directories
  explicitly before appending managed provider-state skill roots.
- Source and documentation probes confirm directory skills use
  `<skill>/SKILL.md` with frontmatter fields `name` and `description`, while
  flat `<skill>.md` files are also discovered at top level. CCB can inject
  inherited Kimi ask skills without prompt wrapping by materializing a
  provider-state skills root and passing it with `--skills-dir`.
- Kimi 1.47.0 writes project-scoped turn evidence under
  `~/.kimi/sessions/<md5(project-path)>/<session>/wire.jsonl`.
- Observed Kimi native turn events include `TurnBegin` with `user_input`,
  `ContentPart` text chunks, `StatusUpdate` with `message_id`, and `TurnEnd`.
  CCB can bind on `CCB_REQ_ID` in `TurnBegin` and complete on `TurnEnd`.
- Source probe of npm package `@moonshot-ai/kimi-code@0.14.2` found a second
  event vocabulary in `dist/main.mjs`: `turn.started`, `assistant.delta`, and
  `turn.ended` with `reason=completed|cancelled|failed`. The package's
  `FileSystemAgentRecordPersistence` writes JSON records to `wire.jsonl`, but
  the real 1.47.0 binary observed locally writes the capitalized
  `TurnBegin`/`ContentPart`/`TurnEnd` wrapper shape. CCB therefore treats the
  1.47.0 shape as the primary observed contract and accepts the source-style
  event names as compatibility if they appear in a wire log.

First CCB slice:

- Register provider key `kimi`.
- Default executable is `kimi`.
- Override command with `KIMI_START_CMD`.
- Use interactive pane-backed runtime first so behavior matches other managed
  CCB agents.

## DeepSeek / Deep Code

Observed upstream:

- DeepSeek API docs list Deep Code as an open-source terminal AI coding
  assistant for DeepSeek-V4.
- GitHub: `lessweb/deepcode-cli`.
- Package: `@vegamo/deepcode-cli@0.1.29`.
- Binary: `deepcode`.
- Node engine: `>=22`.
- DeepSeek docs configure Deep Code through `~/.deepcode/settings.json` with
  `MODEL`, `BASE_URL`, and `API_KEY` under `env`.
- CLI help says `deepcode -p/--prompt` launches with a pre-filled prompt; it
  does not claim noninteractive completion output.
- DeepCode documents/ships project session persistence under
  `~/.deepcode/projects/<project-code>/sessions-index.json` plus
  `<session-id>.jsonl`.
- Observed DeepCode status values include completed and non-terminal states;
  CCB can bind by the user jsonl message containing `CCB_REQ_ID` and complete
  on native `status=completed`.
- Source probe of `@vegamo/deepcode-cli@0.1.29` confirmed the status set:
  `pending`, `processing`, `completed`, `failed`, `interrupted`,
  `ask_permission`, `waiting_for_user`, and `permission_denied`.
  `denySessionPermission()` updates a session entry to
  `status=permission_denied` with a `failReason`, so CCB should terminalize
  that state with diagnostics instead of waiting for timeout.

First CCB slice:

- Register provider key `deepseek`.
- Default executable is `deepcode`.
- Override command with `DEEPSEEK_START_CMD`.
- Do not auto-create or fetch API keys.

## AGY / Antigravity

Observed local storage:

- Local binary: `/home/bfly/.local/bin/agy`, version `1.1.13` (verified
  2026-08-17).
- CLI help exposes `--print`, `--prompt-interactive`, `--conversation`,
  `--continue`, and `--print-timeout`.
- Antigravity writes transcript logs under
  `~/.gemini/antigravity-cli/brain/<conversation>/.system_generated/logs/`.
- Transcript jsonl rows include `source`, `type`, `status`, `created_at`, and
  `content`.
- CCB can bind by `USER_EXPLICIT` / `USER_INPUT` rows containing `CCB_REQ_ID`
  and complete from `MODEL` response rows such as `PLANNER_RESPONSE` with
  `status=DONE`.
- Local transcript inventory found only redaction-safe event triples such as
  `USER_EXPLICIT/USER_INPUT/DONE`, `MODEL/PLANNER_RESPONSE/DONE`,
  `MODEL/RUN_COMMAND/DONE`, and `MODEL/VIEW_FILE/DONE`.
- Antigravity also stores sqlite conversation databases under
  `~/.gemini/antigravity-cli/conversations/<conversation>.db`. The observed
  `steps` table uses numeric `step_type` and `status` values; because the
  stable meaning of those enums is not source-confirmed, transcript jsonl
  remains the primary CCB completion authority and sqlite is only a possible
  future diagnostic aid.
- AGY `1.1.13` has no documented token-storage flag and does not contain the
  Gemini CLI `GEMINI_FORCE_FILE_STORAGE` switches. Binary symbols and an
  isolated syscall trace confirm that its composite token store bypasses the
  OS keyring when the caller requests file storage internally or when
  `~/.gemini/antigravity-cli/cache/antigravity-keyring-unavailable` is recent.
  CCB cannot select the internal caller boolean through the public CLI, so its
  managed launcher refreshes that provider-recognized marker only inside the
  agent-private HOME.

## OpenCode

Observed local/package boundary:

- Local `opencode --version` returned `1.16.2`.
- Package `opencode-ai@1.16.2` is a small npm installer/binary wrapper
  (`bin/opencode.exe`, `postinstall.mjs`) rather than directly reviewable
  application source.
- CCB already has an authoritative native completion contract in
  `docs/opencode-completion-contract.md`: `CCB_DONE`, terminal quiet time, and
  pane text are not completion authority; a matched assistant message is
  complete only when OpenCode structured storage records `time.completed`.
- No change is needed for the current native pivot beyond keeping tests/docs
  from reintroducing pane marker completion for OpenCode.

## MiMo Code

Observed upstream:

- GitHub: `XiaomiMiMo/MiMo-Code`.
- Inspected tag/head: `v0.1.0` / commit
  `42e7da3d51dba1129cd3abfa214e29f7385924a3`.
- Package: `@mimo-ai/cli`.
- Binary: `mimo`.
- Local official install: `npm install -g @mimo-ai/cli@0.1.0`.
- Local `mimo --version` returned `0.1.0`.
- CLI help exposes `mimo run [message..]`, `--format default|json`,
  `--dir`, `--model`, `--agent`, `--session`, `--continue`, and
  `--dangerously-skip-permissions`.
- `MIMOCODE_HOME` is the primary home override. It controls `data`, `cache`,
  `config`, and `state` subdirectories and must be absolute.
- `MIMOCODE_CONFIG` and `MIMOCODE_CONFIG_CONTENT` are supported config
  injection surfaces. MiMo also reads `mimocode.json/jsonc`.
- Config supports `instructions`, so CCB can inject memory and ask guidance as
  generated instruction files.
- Config also exposes `skills.paths`, but current CCB integration uses
  instruction-file injection because it is enough for the ask guidance and
  avoids committing to MiMo skill discovery semantics before more upstream
  evidence exists.

Completion evidence:

- Real `mimo run --format json` emits newline-delimited JSON events.
- Observed successful event shape:
  - `type = step_start`
  - `type = text`, with assistant text nested under `part.text`
  - `type = step_finish`, with stop reason nested under `part.reason = stop`
- MiMo also writes sqlite state at
  `$MIMOCODE_HOME/data/mimocode.db`, with assistant `message.data` containing
  `role=assistant`, `finish=stop`, and `time.completed`, plus text parts in
  `part.data`.
- A visible managed MiMo TUI pane can start successfully, but active pane prompt
  injection did not create MiMo session/message rows in the real CCB test.
  Therefore CCB ask execution should use `mimo run --format json` as the
  primary completion authority while keeping the managed pane for visibility,
  restart, and runtime maintenance.

CCB slice:

- Register provider key `mimo`.
- Default executable is `mimo`.
- Override command with `MIMO_START_CMD`.
- Startup prepares `MIMOCODE_HOME`, generated `mimocode.json`, memory bridge,
  and inherited ask instruction bridge.
- Per-job ask execution runs `mimo run --format json --dir <workdir>` and
  terminalizes on `step_finish` / `part.reason=stop`.

## Qwen Code

Observed upstream/local lab:

- GitHub: `QwenLM/qwen-code`.
- Package: `@qwen-code/qwen-code@0.18.0`.
- Binary: `qwen`.
- Node engine: `>=22.0.0`.
- Local lab install reports `qwen --version` as `0.18.0`.
- `qwen --help` says the positional prompt defaults to one-shot mode, while
  `-i/--prompt-interactive` executes a prompt and stays interactive.
- Help exposes `--bare`, `--model`, `--approval-mode
  plan|default|auto-edit|auto|yolo`, `--allowed-tools`, `--mcp-config`,
  `--openai-api-key`, `--openai-base-url`, `--input-format text|stream-json`,
  `--output-format text|json|stream-json`, `--json-fd`, `--json-file`,
  `--json-schema`, `--input-file`, `--continue`, `--resume`, `--session-id`,
  `--fork-session`, and `--worktree`.
- Source includes `DualOutputBridge`, which can keep the TUI on stdout while
  writing structured JSON events to a secondary fd or file.
- Source tests confirm stream-json envelopes include `system`, `assistant`,
  and `result` output in noninteractive mode.
- Source and changelog evidence show `QWEN_HOME` can isolate config/state.

CCB direction:

- Provider key `qwen`; default command `qwen`; override `QWEN_START_CMD`.
- Prefer per-job structured subprocess execution first:
  `qwen --bare --output-format stream-json --session-id <job-session>
  <wrapped prompt>`.
- Use isolated provider state through `QWEN_HOME`.
- Consider a visible pane later using `--json-file` dual output, but do not
  make that the first completion authority.

## Qoder CLI

- Official package: `@qoder-ai/qodercli`; isolated npm metadata and install on
  2026-07-22 resolved version `1.1.2`, binary `qodercli`, Node `>=20`.
- Official print-mode contract uses `-p` / `--print` with
  `--output-format text|json|stream-json`; `-w` selects the workspace,
  `--config-dir` selects the user-level config root, and `--permission-mode`
  controls headless authorization.
- Real `qodercli 1.1.2 --help` also exposes `--session-id`, but a direct probe
  proved it accepts UUIDs only. A CCB `job_*` identifier is rejected before
  execution, so CCB derives a deterministic UUIDv5 from each job id.
- A credential-free real print probe emitted `system/init`, an assistant error,
  and `result` with `is_error=true` for missing login. CCB uses a Qoder-specific
  stream observer so this becomes a failed native run rather than a successful
  reply containing the login error.
- Both visible and headless launches use the same exact agent-local
  `--config-dir`. CCB does not depend on undocumented `QODER_HOME` behavior.
  Users may authenticate through the visible managed pane or provide the
  documented `QODER_PERSONAL_ACCESS_TOKEN`; CCB does not acquire or inspect it.
- Official references:
  [Using CLI](https://docs.qoder.com/en/cli/using-cli),
  [Quick Start](https://docs.qoder.com/en/cli/quick-start), and
  [Permissions](https://docs.qoder.com/en/cli/permissions).

## Qoder CLI CN

- Official npm package: `@qodercn-ai/qoderclicn`; isolated install and runtime
  probes on 2026-07-24 used version `1.1.3`, binary `qoderclicn`, Node `>=20`.
- The native command contract matches Qoder's documented print surface:
  `-p` / `--print`, `-w <workspace>`, `--output-format stream-json`,
  `--config-dir <root>`, `--permission-mode <mode>`, and `--session-id <uuid>`.
  A raw CCB id such as `job_qoderclicn_*` exits `42` with
  `Invalid session ID. Must be a valid UUID`, so CCB derives a deterministic
  provider-scoped UUIDv5.
- A credential-free `1.1.3` probe emitted an assistant envelope with
  `error=authentication_failed`, followed by `result.is_error=true`. Assistant
  text is non-terminal; only a successful result envelope with a normal stop
  reason completes. A clean process exit without that result fails closed.
- Visible and headless commands share the agent-local `qoderclicn_home` /
  `qoderclicn_config_dir`. Explicit config and permission flags from
  `QODERCLICN_START_CMD` or startup args remain authoritative and are not
  duplicated. Auto permission maps to `auto`; normal headless execution uses
  `dont_ask`.
- Qoder CN defaults to update checks. CCB merges
  `general.enableAutoUpdate=false` and
  `general.enableAutoUpdateNotification=false` into the agent-local
  `settings.json`, preserving unrelated settings and leaving the user's global
  `~/.qoder-cn/settings.json` untouched. Official references:
  [CLI arguments and permissions](https://docs.qoder.cn/cli/using-the-cli) and
  [installation and updates](https://docs.qoder.cn/cli/qoder-cli-cn-get-started-quickly).

## GitHub Copilot CLI

Observed upstream/local lab:

- GitHub: `github/copilot-cli`.
- Package: `@github/copilot@1.0.61`.
- Binary: `copilot`.
- Local lab install reports `GitHub Copilot CLI 1.0.61`.
- Help exposes noninteractive `-p/--prompt`, `-C <directory>`, `--continue`,
  `--session-id`, `--resume`, `--log-dir`, `--model`, `--mode`,
  `--autopilot`, `--no-ask-user`, `--output-format`, `--plugin-dir`,
  `--additional-mcp-config`, `--allow-all-tools`, and `--allow-all-paths`.
- Changelog confirms `--output-format json` emits JSONL in prompt mode.
- Changelog confirms `COPILOT_HOME` is honored and `--plugin-dir` supports
  local plugins, including `.claude-plugin/plugin.json` compatibility.
- Changelog confirms `COPILOT_PLUGIN_DIR_ONLY` can disable automatic plugin
  discovery for deterministic plugin sets.

CCB direction:

- Provider key `copilot`; default command `copilot`; override
  `COPILOT_START_CMD`.
- Prefer per-job structured subprocess execution:
  `COPILOT_HOME=<state> copilot -C <workdir> -p <wrapped prompt>
  --output-format json --session-id <uuid>`.
- Project inherited ask guidance through `--plugin-dir` before falling back to
  prompt wrapping.

## Cursor Agent

Observed local lab:

- Official installer: `https://cursor.com/install`.
- Installed binary: `agent`, with alias/bundle command `cursor-agent`.
- Local lab install reports version `2026.06.12-19-59-36-f6aba9a`.
- Help exposes `--api-key` / `CURSOR_API_KEY`, `--print`, `--output-format
  text|json|stream-json`, `--stream-partial-output`, `--mode plan|ask`,
  `--resume`, `--continue`, `--model`, `--force`, `--yolo`, `--sandbox`,
  `--trust`, `--workspace`, and repeatable `--plugin-dir`.
- The public source was not cloned; the installed bundle is available under
  the lab home at
  `home/.local/share/cursor-agent/versions/2026.06.12-19-59-36-f6aba9a`.

CCB direction:

- Provider key `cursor`; default command `agent`; override `CURSOR_START_CMD`.
- Prefer per-job structured subprocess execution:
  `agent --print --output-format stream-json --workspace <workdir> --trust
  <wrapped prompt>`.
- Use isolated provider home for CCB-managed state. Auth can use inherited user
  state or `CURSOR_API_KEY`, but CCB must not create credentials.

## Kiro CLI

Observed local lab:

- Official installer: `https://cli.kiro.dev/install`.
- Installed binary: `kiro-cli`.
- Local lab install reports `kiro-cli 2.7.0`.
- GitHub `kirodotdev/Kiro` is useful project/docs evidence, but is not the
  full CLI source.
- `kiro-cli chat --help` exposes `--resume`, `--resume-id`, `--agent`,
  `--model`, `--effort`, `--trust-all-tools`, `--trust-tools`,
  `--no-interactive`, `--wrap`, `--tui`, `--legacy-ui`,
  `--agent-engine v2|v1|kas`, and `--mode vibe|spec`.
- `--format plain|json|json-pretty` is documented for list commands, not for
  normal chat turns.

CCB direction:

- Provider key `kiro`; default command `kiro-cli`; override `KIRO_START_CMD`.
- Treat as higher risk until a stable structured chat event stream is found.
- First adapter should likely use `kiro-cli chat --no-interactive --wrap never
  <wrapped prompt>` and completion by subprocess exit/stdout, with pane-backed
  fallback only if needed.

## Charm Crush

Observed upstream/local lab:

- GitHub: `charmbracelet/crush`.
- Package: `@charmland/crush@0.76.0`.
- Binary: `crush`.
- Local lab install reports `crush version v0.76.0`.
- Help exposes interactive `crush`, noninteractive `crush run`, `--cwd`,
  `--yolo`, `--data-dir`, `--session`, `--continue`, and commands including
  `login`, `logs`, `models`, `projects`, `run`, `server`, `session`, `stats`,
  and `update-providers`.
- Source `internal/cmd/run.go` mints a per-call `runID`, sends the prompt with
  that run id, streams assistant text to stdout, and exits only on a matching
  `RunComplete`. `RunComplete` carries final text and is the authoritative end
  of run signal.
- `--data-dir` is the clean state isolation surface.

CCB direction:

- Provider key `crush`; default command `crush`; override `CRUSH_START_CMD`.
- Prefer per-job subprocess execution:
  `crush --data-dir <provider-state> --cwd <workdir> run --quiet
  <wrapped prompt>`.
- Treat process exit/stdout as the first completion source. If CCB later needs
  deeper diagnostics, inspect Crush's local data/logs and RunComplete-backed
  source behavior.

## Pi

Observed official docs:

- Official site/docs: `https://pi.dev/` and `https://pi.dev/docs/latest`.
- Package: `@earendil-works/pi-coding-agent`.
- Binary: `pi`.
- Quick start installs with
  `npm install -g --ignore-scripts @earendil-works/pi-coding-agent`; the
  install script is optional for normal npm installs.
- CLI reference exposes `pi [options] [@files...] [messages...]`.
- Noninteractive/structured mode uses `pi --mode json "Your prompt"` and
  outputs JSON objects one per line.
- JSON event stream docs define turn lifecycle events including `turn_start`
  and `turn_end`; `turn_end` carries the final assistant `message` and tool
  results.
- The official extension API exposes `before_agent_start`, assistant/tool
  events, `agent_end`, and `agent_settled`. Pi 0.82.1 documents
  `agent_settled` as the boundary after automatic retry, compaction, and
  queued continuation have finished.
- CLI extension loading uses `--extension <path>`, so CCB can observe native
  lifecycle state without screen scraping or modifying Pi's auth/config files.
- Session options include `--session-dir <dir>`, `--session`, `--resume`,
  `--continue`, `--no-session`, and `--name`.
- Trust options include `--approve` and `--no-approve`; noninteractive modes do
  not show a trust prompt.
- Environment variables include `PI_CODING_AGENT_DIR`,
  `PI_CODING_AGENT_SESSION_DIR`, `PI_SKIP_VERSION_CHECK`, `PI_OFFLINE`, and
  `PI_TELEMETRY`.

CCB direction:

- Provider key `pi`; default command `pi`; override `PI_START_CMD`.
- Prefer managed visible-pane execution. Load a runtime-owned extension with
  `--extension`, bind its append-only lifecycle sidecar to exact dispatch,
  actor, launch-session, and runtime-instance identity, and terminalize only
  on `agent_settled` with final `stop` plus non-empty visible text.
- Retain the per-job structured subprocess
  `pi --mode json --session-dir ... --no-approve --name <job>` for
  `CCB_PI_EXECUTION_MODE=headless` rollback and persisted 8.5.0 `pi_run`
  jobs. That path still waits for both `agent_settled` and process exit.
- Keep visible pane state isolated with `PI_CODING_AGENT_DIR` and
  `PI_CODING_AGENT_SESSION_DIR`; skip startup version checks with
  `PI_SKIP_VERSION_CHECK=1`.

## Z.ai CLI

Observed upstream/docs:

- Official Z.ai `@z_ai/coding-helper` is a Coding Tool Helper, not a
  standalone coding-agent runtime. It installs/configures GLM Coding Plan for
  tools such as Claude Code, OpenCode, Crush, and Factory Droid.
- Conversational ZAI CLI evidence comes from `guizmo-ai/zai-glm-cli` /
  `@guizmo-ai/zai-cli`.
- Binary: `zai`.
- README documents interactive mode as `zai` or `zai -d /path/to/project`.
- README documents headless mode:
  `zai --prompt "analyze package.json and suggest improvements"` and
  `zai -p "run tests" --max-tool-rounds 50`.
- CLI reference lists `zai [options] [message...]`, `-d/--directory <dir>`,
  `-p/--prompt <prompt>`, `--no-color`, `--model`, `--api-key`, and
  `--base-url`.
- Configuration defaults to `~/.zai/user-settings.json`; environment variables
  include `ZAI_API_KEY`, `ZAI_BASE_URL`, and `ZAI_MODEL`.
- Project instructions can live in `.zai/ZAI.md`.

CCB direction:

- Provider key `zai`; default command `zai`; override `ZAI_START_CMD`.
- Visible pane command: `zai --directory <workspace>`.
- Per-job subprocess execution:
  `HOME=<provider-state>/home zai --directory <workdir> --no-color --prompt <wrapped prompt>`.
- Completion source is process exit plus stdout through the shared native CLI
  adapter. Do not ask the model to print `CCB_DONE`.
- Treat official `@z_ai/coding-helper` as setup tooling for users who want GLM
  Coding Plan in other providers, not as the CCB provider runtime.

## Grok Build CLI

Observed upstream/docs:

- Official product page: `https://x.ai/cli`.
- Official docs: `https://docs.x.ai/build/overview` and
  `https://docs.x.ai/build/cli/headless-scripting`.
- Package: `@xai-official/grok`.
- Binary: `grok`.
- npm metadata probe on 2026-07-09 returned `@xai-official/grok@0.2.93` with
  bin `grok` and platform optional dependencies such as
  `@xai-official/grok-linux-x64@0.2.93`.
- The official Grok Build CLI is distributed as a platform binary, not as
  reviewable application source. CCB should not rely on private binary internals
  or reverse-engineered session logs.
- Official docs define three modes: interactive TUI, headless scripting, and
  ACP (`grok agent stdio`).
- Headless mode uses `grok -p "prompt"` and supports `--cwd <PATH>`,
  `--session-id <ID>`, `--resume`, `--continue`, `--model`,
  `--output-format plain|json|streaming-json`, `--always-approve`,
  `--no-alt-screen`, and `--no-auto-update`.
- Docs say headless sessions are stored in `~/.grok/sessions`; user config
  lives at `~/.grok/config.toml`.
- Docs recommend `--no-auto-update` for headless or ACP automation to avoid
  background update checks in scripts/CI.
- ACP mode runs `grok agent stdio` over JSON-RPC. The official example shows
  assistant text arriving as `session/update` chunks with
  `update.sessionUpdate = agent_message_chunk` and `update.content.text`.
  `session/prompt` returns completion metadata while text arrives separately.

CCB direction:

- Provider key `grok`; default command `grok`; override `GROK_START_CMD`.
- Visible pane command: `HOME=<provider-state>/home grok --no-auto-update`.
- Per-job subprocess execution:
  `HOME=<provider-state>/home grok --no-auto-update -p <wrapped prompt>
  --cwd <workdir> --output-format streaming-json --session-id <job_id>`.
- Completion source is provider-native streaming-json/stdout through the shared
  native CLI adapter. The Grok observer should parse JSON-RPC style
  `session/update` `agent_message_chunk` events and fall back to generic
  assistant/result envelopes when Grok emits simpler structured output.
- Do not ask Grok to print `CCB_DONE`.
- Do not auto-acquire Grok login, X subscription, or API keys. Users can
  authenticate the managed Grok home through the visible pane or use inherited
  environment such as `XAI_API_KEY` when supported by the CLI.

## Official DeepSeek Harness

Observed official source and package evidence on 2026-08-14:

- GitHub: `deepseek-ai/deepseek-harness`.
- npm package: `@deepseek-ai/dsh@0.1.0-rc.6`; binary `dsh`.
- Inspected source commit:
  `47f943859bef60e4160492346772ded9b24f765a`.
- Node requirement: `^22.19 || >=24`.
- DSH is a service-oriented harness with terminal, Web, headless, and SDK
  surfaces; CCB uses the official Web carrier rather than pretending the host
  process is an interactive agent TUI.
- Managed host: `dsh web --host 127.0.0.1 --port 0`; readiness is published as
  `dsh web: http://127.0.0.1:<port>`.
- Unary calls are HTTP POST. Browser event downlinks use
  `/api/events.mux` and `/api/events.host` WebSockets.
- `session.prompt` keeps its RPC id in the durable
  `user/message.source.rpcId`. Native turn evidence includes `turn/start`,
  `user/message`, committed `assistant/message`, and `turn/end`.
- Native terminal reasons are `completed`, `aborted`, `blocked`, `error`,
  `max-tokens`, and `interrupted`.
- `session.history` pages backward by `beforeSeq` and supports exact
  request/turn reconstruction without prompt repost.
- The official built-in DeepSeek route is `deepseek-official`; the inspected
  catalog exposes `deepseek-v4-flash` and `deepseek-v4-pro` with reasoning
  levels `off`, `high`, and `max`.
- DSH supports native `/compact` and does not expose native `/clear`.
- DSH home is `$DSH_HOME` (default `~/.dsh`); inspected inputs include
  `.credentials.yaml`, `.env`, `settings.yaml`, `skills/`, and `AGENTS.md`.

CCB direction:

- Provider key `dsh`; default executable `dsh`; override `DSH_START_CMD`.
- Isolated per-Agent DSH home and native session id.
- WebSocket open before submission; CCB job id as the native prompt RPC id.
- Success only from exact durable request anchor, committed non-empty
  same-turn assistant reply, and same-turn native `completed` terminal.
- Observer-only history restore; no prompt repost.
- Pane, when present under the current POSIX lifecycle, is host/log transport
  only and has no request/reply/completion authority.

## Local Probe Evidence

- Local Node: `v22.20.0`.
- Installed real Kimi release:
  `/home/bfly/.local/bin/kimi --version` returned `kimi, version 1.47.0`.
- `npm view @moonshot-ai/kimi-code@0.14.2 version bin engines --json` returned
  bin `kimi` and engine `>=22.19.0`.
- `npm view @vegamo/deepcode-cli@0.1.29 version bin engines --json` returned
  bin `deepcode` and engine `>=22`.
- `npx --yes @moonshot-ai/kimi-code@0.14.2 --help` succeeded.
- `npx --yes @vegamo/deepcode-cli@0.1.29 --help` succeeded.
- Extracted npm package tarballs under `/tmp/ccb-native-src-probe` for local
  source inspection.
- Local AGY transcript/db inventory confirmed transcript completion evidence
  without printing user content.
- `npm pack opencode-ai@1.16.2` confirmed OpenCode's npm package exposes only
  installer/binary wrapper files, so the existing CCB storage contract remains
  the local source of truth for OpenCode completion behavior.
- `kimi --auto-approve --version` succeeded on local Kimi 1.47.0, while
  `kimi --auto --version` failed with "No such option: --auto".
- `npm view @mimo-ai/cli` returned latest stable `0.1.0` and binary `mimo`.
- `mimo run --format json` real local probe returned exact reply
  `MIMO_CCB_REAL_OK` and showed the nested `part.text` /
  `part.reason=stop` event shape used by CCB.
- Next-wave lab root:
  `/home/bfly/yunwei/test_ccb2/cli-integration-lab`.
- `@qwen-code/qwen-code@0.18.0`, `@github/copilot@1.0.61`, and
  `@charmland/crush@0.76.0` installed under the lab npm prefix.
- Cursor Agent and Kiro CLI installed under isolated lab `HOME` from their
  official installer scripts.
- Installed version checks returned Qwen `0.18.0`, Copilot `1.0.61`, Cursor
  Agent `2026.06.12-19-59-36-f6aba9a`, Kiro `2.7.0`, and Crush `v0.76.0`.
- `npm view @xai-official/grok name version description bin dist.tarball
  optionalDependencies --json` returned package `0.2.93`, bin `grok`, and
  platform optional dependency versions.
- Installed `@deepseek-ai/dsh@0.1.0-rc.6` under the isolated lab prefix and
  verified its `dsh` executable with local Node `v22.22.1`.
- An isolated no-credential official `dsh web` probe reached loopback
  readiness, durably recorded the exact CCB RPC anchor, and ended with native
  `turn/end(error)` plus an empty reply. This proves transport and fail-closed
  terminal handling only; it is not authenticated answer-success evidence.
