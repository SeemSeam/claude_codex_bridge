# Native CLI Providers Roadmap

Date: 2026-06-13
Last updated: 2026-08-17

## Status Summary

- Current status: native completion pivot has landed in source and `v7.5.0`
  has been published. Kimi, DeepSeek/DeepCode, AGY, and MiMo no longer use
  `CCB_DONE` as their primary completion signal. Kimi and OpenCode inherited
  ask skill injection landed in commit `a4395c2`; MiMo inherited ask
  instruction injection and native `mimo run --format json` execution landed
  in commit `fce17c3`. Kimi receipt/diagnostics hardening has landed in source
  with Kimi-only guarded behavior. AGY delivery stability hardening has landed
  in source with ready-gated prompt delivery, pane fallback, and coalesced
  request diagnostics. Kimi restart now uses observation-bound per-agent native
  session ownership and capability-confirmed exact selection rather than
  workdir-global `--continue`. Qoder's merged provider registration is now
  corrected to use documented print/config arguments, UUID session identity,
  agent-local config state, and provider-specific stream terminalization. Qoder
  CLI CN is registered separately as `qoderclicn` and reuses that corrected
  contract for `@qodercn-ai/qoderclicn` rather than the retired generic adapter.
- Last verified: stable `v8.6.9` was published from commit `677edc72c` with all
  seven release/test workflows successful, 10 GitHub assets, verified public
  checksums, and `@seemseam/ccb@8.6.9` on npm `latest`. The source candidate
  passed the complete
  pytest gate with `7166 passed, 3 skipped, 4 subtests passed`; the focused
  Linux/Windows release, Mobile version, and Windows PR-isolation set passed
  with `76 passed`. The bilingual notes check, `npm pack --dry-run`, and
  isolated source-wrapper version check also passed for `8.6.9`.
- Qoder correction verification on 2026-07-22 used real `qodercli 1.1.2`
  help/version and credential-free stream output plus focused launcher,
  execution, catalog, registry, and storage tests. Authenticated completion
  remains a release-qualification gate rather than an inferred pass.
- Qoder CN verification on 2026-07-24 used isolated
  `@qodercn-ai/qoderclicn@1.1.3` (binary `qoderclicn`, Node `>=20`). The raw
  `job_qoderclicn_*` session id was rejected with exit `42`; UUIDv5 reached the
  native stream, whose unauthenticated assistant error plus
  `result.is_error=true` confirmed the CN-specific fail-closed observer. Managed
  panes share one agent-local config root and disable both update switches.
- Official DeepSeek Harness is now published in `v8.6.9` as the separate
  service provider `dsh`. It starts `dsh web` on loopback, submits
  through HTTP/WebSocket session RPC, requires exact native RPC/turn terminal
  evidence, restores with an observer-only history reader, and never treats
  its host/log pane as prompt or completion authority. Focused DSH/config/
  context-control tests pass. An isolated external `ccb_test` mount with
  official `0.1.0-rc.6` also passed host-instance-fenced restart, clear, and
  native compact; its no-key ask produced the expected exact request anchor
  plus native `turn/end(error)` fail-closed result.
- Next target: review and release readiness for the next native CLI provider
  wave after source-runtime and real CLI version smoke passed for Qwen Code,
  Cursor Agent, GitHub Copilot CLI, Charm Crush, Kiro CLI, and Pi. Talk1's
  Crush visible-pane isolation blocker has been fixed in source. Pi
  visible-pane execution has completed source and authenticated acceptance,
  with exact
  `agent_settled` completion, persisted headless-mode compatibility, an
  explicit rollback switch, and pane/trace evidence tracked in
  [topics/pi-visible-pane-completion.md](topics/pi-visible-pane-completion.md).

## Done

- Published stable `v8.6.9` from commit `677edc72c` through an annotated tag,
  bilingual GitHub Release, Linux/macOS/Windows/Mobile and Sidebar assets, and
  npm Trusted Publishing. Public checksum and fresh npm-install verification
  passed after all release and platform workflows completed successfully.
- Researched and integrated official DeepSeek Harness:
  - upstream `deepseek-ai/deepseek-harness`, npm `@deepseek-ai/dsh`, binary
    `dsh`, inspected at `0.1.0-rc.6` / commit
    `47f943859bef60e4160492346772ded9b24f765a`;
  - selected a service-backed Web carrier rather than terminal input;
  - isolated each Agent's `DSH_HOME` and projected only credentials, API
    route, settings, skills, and memory allowed by provider-profile policy;
  - bound each CCB job id to DSH `session.prompt` RPC id and durable
    `user/message.source.rpcId`;
  - required a committed non-empty same-turn assistant reply and native
    `turn/end(completed)`; every other terminal and process-only closure fails
    closed;
  - added native session rotation for `ccb clear`, API-backed `/compact`,
    model/reasoning/API shortcuts, update discovery, storage classification,
    and required DSH control skills.

- Confirmed Kimi Code is a terminal AI coding agent from Moonshot AI, launched
  with `kimi`, and npm package `@moonshot-ai/kimi-code` exposes bin `kimi`.
- Confirmed DeepSeek API docs list Deep Code as a terminal AI coding assistant
  for DeepSeek-V4, installed by `npm install -g @vegamo/deepcode-cli` and
  launched with `deepcode`.
- Chose provider keys:
  - `kimi` maps to executable `kimi`.
  - `deepseek` maps to executable `deepcode`.
- Chose first-slice completion strategy: pane-backed prompt wrapping and
  pane-text detection with `CCB_REQ_ID` and `CCB_DONE`, mirroring the earlier
  `agy` boundary while sharing new generic support code. This is now retained
  only as historical first-slice evidence and compatibility helper coverage.
- Added shared `pane_quiet_support` helpers for prompt wrapping, pane snapshots,
  start/poll behavior, done marker completion, empty-reply diagnostics, and
  input-unresponsive timeout.
- Added Kimi and DeepSeek backend modules with manifests, execution adapters,
  session bindings, and simple-tmux launchers.
- Registered both providers as optional built-ins, runtime specs, command
  defaults, session filenames, doctor/kill paths, tests, and config contract
  documentation.
- Validated and fixed real Kimi 1.47.0 behavior:
  - Kimi launcher does not append implicit `--continue`, because first launch
    without a workdir session exits in Kimi 1.47.0.
  - Pane-quiet parsing ignores single prompt-echo done markers and waits for
    the model's own done marker.
  - Pane-quiet parsing strips Kimi TUI's leading assistant bullet from the
    first reply line.
  - Kimi prompt delivery is deferred until the TUI input area is visible, so
    asks submitted immediately after start/restart are not lost before Kimi is
    ready.
  - A per-agent CCB session record owns a native Kimi session only after its
    exact request anchor is observed; managed restart validates and selects
    that exact id, while invalid authority starts fresh.
- Validated source runtime with a stub-backed smoke project:
  - `config validate` accepted `kimi1:kimi, deep1:deepseek`.
  - `ccb_test -s` launched both providers through tmux.
  - `ccb_test ask` completed for both providers with
    `completion_reason: pane_done_marker`.
  - `ccb_test restart kimi1` and `ccb_test restart deep1` succeeded.
  - post-restart asks completed for both providers.
  - `reload --dry-run` returned `plan_class: no_change`.
- Validated source runtime with a real Kimi project:
  - `config validate` accepted `kimi1:kimi, kimi2:kimi`.
  - `ccb_test -s` launched both Kimi panes.
  - immediate post-start ask completed with exact reply
    `KIMI_READY_SEND_OK`.
  - serial ask set completed for `KIMI_SERIAL_OK_1..5`.
  - 8-job concurrent pressure set across both agents completed with exact
    replies `KIMI_AFTERFIX_OK_1..8`.
  - `restart kimi1`, post-restart ask, `clear kimi1 kimi2`, post-clear ask,
    artifact reply, `reload --dry-run`, and `ping` checks passed.
- Focused related pytest set after real-Kimi fixes: `113 passed`.
- Replaced primary completion detection for Kimi, DeepSeek/DeepCode, and AGY:
  - Kimi polls Kimi `wire.jsonl`, binds by `CCB_REQ_ID`, emits completion on
    native `TurnEnd`, and diagnoses `TurnEnd` with empty reply as
    `kimi_native_empty_reply`.
  - DeepSeek polls DeepCode `sessions-index.json` plus session jsonl, emits
    completion on native `status=completed`, and diagnoses completed empty
    replies as `deepseek_native_empty_reply`.
  - AGY polls Antigravity transcript logs and emits completion from native
    model `*_RESPONSE` events.
  - OpenCode already uses native storage and remains unchanged.
- Updated deterministic provider stubs to write Kimi, DeepCode, and AGY native
  stores instead of pane `CCB_DONE` for these providers.
- Focused native completion verification:
  `python -m pytest -q test/test_agy_execution_polling.py
  test/test_native_cli_completion.py
  test/test_native_cli_providers.py test/test_v2_provider_catalog.py
  test/test_pane_quiet_support.py`: `25 passed`.
- Explored upstream/source and local runtime evidence for native completion
  markers:
  - Kimi real 1.47.0 writes `TurnBegin`/`ContentPart`/`TurnEnd`; npm source
    also exposes `turn.started`/`assistant.delta`/`turn.ended`, now accepted as
    a compatibility input when present in a wire log.
  - DeepCode source confirms `permission_denied`; CCB now returns
    `deepseek_native_permission_denied` with diagnostics instead of waiting for
    timeout.
  - AGY local transcript inventory confirms `USER_EXPLICIT/USER_INPUT/DONE`
    plus `MODEL/*_RESPONSE/DONE` as the practical native completion marker.
- Added inherited ask skill projection for additional providers:
  - Kimi receives `inherit_skills/kimi_skills/ask/SKILL.md` through managed
    provider-state skills and `--skills-dir`; existing Kimi default project/user
    skill directories are preserved when CCB switches Kimi into explicit
    `--skills-dir` mode.
  - OpenCode receives `inherit_skills/opencode_skills/ask.md` through generated
    `.ccb/runtime/skills/<agent>/opencode/ask.md` and
    `opencode.json.instructions`.
  - OpenCode `inherit_memory` and `inherit_skills` are independent: memory can
    be disabled while keeping the ask instruction bridge.
  - OpenCode projection event de-duplication now includes skill hash evidence,
    so skill-only injection does not emit repeated unchanged events.
- Focused ask skill injection verification:
  `python -m pytest -q test/test_native_cli_providers.py
  test/test_provider_hook_settings.py test/test_v2_runtime_launch.py
  test/test_project_memory_real_context.py
  test/test_provider_memory_external_matrix.py test/test_storage_classification.py
  test/test_repo_hygiene.py test/test_ask_skill_templates.py`:
  `141 passed, 1 skipped`; `git diff --check` passed.
- Added MiMo Code as optional provider `mimo`:
  - Official package `@mimo-ai/cli@0.1.0` exposes binary `mimo`; local install
    reports `mimo --version` as `0.1.0`.
  - CCB startup still mounts a managed visible MiMo pane and materializes
    MiMo `mimocode.json` with memory plus ask instruction paths.
  - CCB ask execution uses a per-job native subprocess:
    `mimo run --pure --format json --dir <workdir> <wrapped prompt>`.
  - Completion is observed from JSON result events: `part.text` supplies the
    assistant reply and `step_finish` / `part.reason=stop` terminalizes with
    `completion_reason: mimo_run_stop`.
  - Completed-native-empty MiMo results terminalize as
    `mimo_run_empty_reply` instead of waiting for reliability timeout.
  - CCB passes `--pure` for MiMo run-mode asks so external plugin/tool-call
    intermediate steps do not consume simple CCB ask jobs before final text.
- MiMo verification:
  - Real installed `mimo run --format json --dir
    /home/bfly/yunwei/test_ccb2/mimo_real` completed with exact reply
    `MIMO_CCB_REAL_OK`.
  - Source-runtime real CCB project
    `/home/bfly/yunwei/test_ccb2/mimo_ccb_real` accepted `cmd; mimo1:mimo`,
    launched with `/home/bfly/yunwei/ccb_source/ccb_test -s`, and completed
    job `job_ae41cad0e98a` with reply `MIMO_CCB_RUN_OK_3` and
    `completion_reason: mimo_run_stop`.
  - Release-gate rerun with `--pure` completed job `job_023d114681ca` with
    reply `MIMO_RELEASE_751_OK` and `completion_reason: mimo_run_stop`; the
    preceding non-pure probe exposed `mimo_run_finished:tool-calls`, now
    covered as an intermediate finish reason.
  - Focused touched-provider tests:
    `python -m pytest -q test/test_mimo_provider.py
    test/test_native_cli_providers.py test/test_v2_provider_catalog.py
    test/test_v2_provider_core_registry.py test/test_runtime_specs.py
    test/test_v2_config_loader.py test/test_v2_runtime_launch.py
    test/test_storage_classification.py test/test_repo_hygiene.py
    test/test_ask_skill_templates.py test/test_provider_hook_settings.py
    test/test_project_memory_real_context.py
    test/test_provider_memory_external_matrix.py test/test_opencode_comm_sqlite.py
    test/test_opencode_execution_polling.py
    test/test_provider_execution_service_runtime.py`: `263 passed, 1 skipped`.
  - Final full release-gate pytest: `2613 passed, 2 skipped`.
  - `git diff --check`: passed.
- Installed and researched the next requested CLI wave in an external lab:
  - `qwen`: `@qwen-code/qwen-code@0.18.0`, binary `qwen`.
  - `copilot`: `@github/copilot@1.0.61`, binary `copilot`.
  - `cursor`: official Cursor Agent install, binary `agent`, version
    `2026.06.12-19-59-36-f6aba9a`.
  - `kiro`: official Kiro CLI install, binary `kiro-cli`, version `2.7.0`.
  - `crush`: `@charmland/crush@0.76.0`, binary `crush`.
- Cloned or located source/bundle evidence under
  `/home/bfly/yunwei/test_ccb2/cli-integration-lab`:
  - `src/qwen-code` from `QwenLM/qwen-code`.
  - `src/copilot-cli` from `github/copilot-cli`.
  - `src/crush` from `charmbracelet/crush`.
  - `src/kiro` from `kirodotdev/Kiro`; this is project/docs evidence, not full
    CLI source.
  - Cursor Agent installed bundle under
    `home/.local/share/cursor-agent/versions/2026.06.12-19-59-36-f6aba9a`.
- Landed the next-wave minimal built-in provider path in source:
  - `qwen`, `cursor`, `copilot`, `crush`, `kiro`, and `pi` are optional provider ids
    with modern backend shape.
  - Shared native CLI subprocess support handles per-job command execution,
    stdout/stderr artifacts, structured-result parsing, stdout-on-exit
    completion, empty replies, nonzero exits, run timeouts with process
    termination, and tool/intermediate events.
  - Qwen and Copilot use JSONL/stream-json subprocess result parsing; Crush and
    Kiro use subprocess exit plus stdout. Cursor now defaults to the named
    visible pane with exact anchored top-level transcript completion; its
    earlier stream-json subprocess remains an explicit rollback mode. Pi also
    uses its managed visible pane with exact lifecycle sidecar evidence.
  - Crush visible pane startup now passes `--data-dir <provider-state>/data`,
    matching the ask execution isolation boundary.
  - Qwen, Cursor, Copilot, Crush, Kiro, and Pi provider-state contents now
    classify as native CLI provider-owned session/cache or projected skill
    evidence.
  - Focused source tests passed with `35 passed`.
- Fixed control-plane environment propagation for provider start-command
  overrides:
  - `runtime_env.control_plane` now allows the provider `*_START_CMD` variables
    exported by `provider_core.runtime_shared`.
  - Outer provider home/session authority such as `CODEX_HOME`, `QWEN_HOME`, and
    `CCB_SESSION_ID` remains filtered.
  - This fixed source-runtime launch when deterministic provider stubs are
    supplied through `QWEN_START_CMD`, `CURSOR_START_CMD`, `COPILOT_START_CMD`,
    `CRUSH_START_CMD`, `KIRO_START_CMD`, and `PI_START_CMD`.
- Validated next-wave source runtime with
  `/home/bfly/yunwei/test_ccb2/next_wave_provider_smoke`:
  - `config validate` accepted
    `qwen1:qwen, cursor1:cursor, copilot1:copilot, crush1:crush, kiro1:kiro, pi1:pi`.
  - `ccb_test -s` mounted all six providers after Pi was added.
  - Prior ask traces for Qwen/Cursor/Copilot/Crush/Kiro completed with
    `qwen_run_stop`, `cursor_run_stop`, `copilot_run_stop`, `crush_run_exit`,
    and `kiro_run_exit`; Pi add-on smoke completed with `pi_run_stop`.
  - Pi session payload confirmed isolated `PI_CODING_AGENT_DIR`,
    `PI_CODING_AGENT_SESSION_DIR`, `PI_SKIP_VERSION_CHECK=1`,
    `PI_TELEMETRY=0`, and visible `--session-dir ... --no-approve`.
- Validated no-account real CLI version smoke in
  `/home/bfly/yunwei/test_ccb2/cli-integration-lab`:
  - Qwen `0.18.0`.
  - Cursor Agent `2026.06.12-19-59-36-f6aba9a`.
  - GitHub Copilot CLI `1.0.61`.
  - Charm Crush `0.76.0`.
  - Kiro CLI `2.7.0`.
  - Pi `0.79.3`.
- Native CLI adapter timeout regression passed with `23 passed`; wider
  touched-provider suite including storage classification passed with
  `238 passed`; post-review source-runtime recheck passed for the first five
  next-wave providers and confirmed Crush visible-pane `--data-dir`; Pi
  add-on smoke passed with `pi_run_stop`; full source test gate passed with
  `2621 passed, 2 skipped, 21 deselected`;
  `git diff --check` passed.
- Cursor visible-pane execution landed in merge commit `7a008597`: jobs wait
  for stable idle, dispatch exactly once to the named pane, bind the exact
  `CCB_REQ_ID` in a top-level managed transcript, and require the matching
  `turn_ended`; `CCB_CURSOR_EXECUTION_MODE=headless` retains rollback. The
  Cursor/native-provider focused suite passed `119` tests on 2026-08-12.
- Landed Kimi-only receipt and diagnostics hardening:
  - Kimi inherited ask skill projects the structured receipt contract:
    `status`, `inspected`, `exact_files`, `findings`, `reject_cases`,
    `required_tests`, `no_open`, and `blockers`.
  - Kimi no-captured timeout decisions emit machine-readable
    `receipt_class=no_captured_reply` diagnostics.
  - Forced Kimi empty artifact replies no longer instruct callers to read an
    empty artifact as task evidence.
  - `ccb trace` surfaces Kimi terminal reason, reply chars, elapsed seconds,
    artifact-forced status, and receipt class.
  - Kimi manifest `supports_resume=false` now matches CCB in-flight execution
    restore semantics.
- Landed AGY delivery stability hardening:
  - AGY start stores pending prompts while the Antigravity pane is busy and
    sends only after the empty input prompt is observed.
  - AGY poll keeps busy-pane jobs running past the old anchor-missing window
    instead of terminalizing and allowing later retries to merge into the same
    native turn.
  - AGY poll can use stable pane fallback when transcript writes lag.
  - AGY preserves ambiguous tmux send errors as diagnostics and continues
    observing transcript/pane evidence before deciding failure.
  - AGY transcript parsing detects multiple `CCB_REQ_ID` anchors in one native
    `USER_INPUT` and reports superseded requests as `agy_request_coalesced`.
- Fixed Issue #318 for AGY `1.1.13`:
  - each managed launch refreshes AGY's confirmed recent-keyring-failure marker
    inside the agent-private HOME, bypassing the unavailable OS keyring without
    an undocumented environment switch;
  - marker replacement detaches legacy links, source credentials remain
    one-way inputs, user environment overrides cannot redirect the managed
    HOME, and no other provider receives AGY-specific state.
- Added Z.ai CLI provider support:
  - `provider = "zai"` is registered as an optional native CLI provider.
  - Startup uses managed provider-state and `ZAI_START_CMD` override.
  - Per-job ask uses `zai --prompt` headless execution with stdout/process-exit
    completion through the shared native CLI adapter.
  - Official Z.ai Coding Helper remains documented as a setup helper, not the
    CCB provider runtime.
  - Focused AGY/native provider verification passed with `34 passed`; isolated
    stub source-runtime smoke completed with `agy_transcript_response_done`.
- Hardened Grok result collection to require provider-native turn completion:
  - Streaming `type=end` plus `stopReason=EndTurn` and compatible native
    turn-end events are the only successful completion authority.
  - Exit zero without native terminal evidence closes as
    `incomplete/grok_native_terminal_missing`, including partial-text cases.
  - `Cancelled` native ends close immediately as incomplete, while internal
    `TURN_BOUNDARY` remains derived evidence rather than authority.
  - Focused tests passed with `25 passed`; the complete Grok/native execution
    files passed with `54 passed`; wider provider/registry tests passed with
    `31 passed`; a real authenticated Grok ask completed from native
    `EndTurn` as `grok_run_stop`.
- Fixed Issue #255 in Grok visible startup: explicit `--fullscreen` agent
  startup arguments suppress CCB's injected `--minimal` default, while default
  and unrelated-argument launches continue to use minimal mode. Focused and
  expanded provider regressions passed with `9 passed` and `186 passed`; config,
  registry/catalog, and repository hygiene coverage passed with `139 passed`.
- Added Grok-native CCB skills and execution context:
  - `ask` and `ccb-clear` project independently into each managed
    `.grok/skills` directory with inspectable ownership markers.
  - Normal starts use Grok's native `bypassPermissions` mode, aligned with
    other auto-permission providers, and add the two exact skill command
    prefixes when projection is ready. Safe starts add neither bypass mode nor
    skill rules; disabled inheritance, missing sources, and unmarked conflicts
    suppress only the skill rules.
  - Headless jobs restore exact agent/project/session caller identity before a
    skill invokes CCB, while Grok native completion authority remains unchanged.
  - Focused tests passed with `204 passed`; real two-instance native skill
    discovery and session-policy inspection passed on 2026-07-13.
  - After refreshing the user-owned xAI OAuth session, direct ask,
    Grok-to-Grok `ask --chain` result recovery, native `EndTurn` completion,
    named `ccb clear grok2`, and post-clear target isolation all passed in the
    opened source-runtime project. The host required its configured local Clash
    proxy because intercepted DNS otherwise resolved the provider endpoint
    incorrectly.
  - Opened-frontend testing then exposed that the per-job headless path did not
    update either visible Grok pane. Grok asks now use pane-native prompt
    delivery, collect replies from the matching visible session event stream,
    require native `turn_completed/end_turn`, and dispatch CCB replies back to
    the caller pane. Real job `job_3d7385171e82` and reply-delivery job
    `job_6892ec58f379` passed the visible `grok2 -> grok1 -> grok2` route.

## In Progress

- Provider-specific auth/doctor diagnostics decision for the next-wave CLIs.
- Authenticated DSH answer success and a real macOS AGY startup remain explicit
  follow-up qualification evidence rather than inferred release claims.
- Grok cross-agent communication acceptance is complete for direct ask,
  chained result recovery, named clear, and post-clear isolation.

## Next

1. Decide whether any provider needs a follow-up doctor/auth diagnostic before
   release.
2. Decide whether to keep
   `/home/bfly/yunwei/test_ccb2/next_wave_provider_smoke` as a reusable smoke
   fixture.
3. Run a real source-runtime AGY smoke in `/home/bfly/yunwei/test_ccb2` when an
   authenticated AGY account is available; unit coverage currently validates
   the delivery and parsing mechanics without provider login.

## Deferred

- Kimi prompt-mode adapter using `kimi --prompt` and `--output-format`.
- Provider-specific auth/config diagnostics for Kimi login and Deep Code
  `settings.json`.
- MiMo provider-specific auth/config diagnostics if users hit
  `mimo run` account or model setup failures.
- Support aliases such as `deepcode` if real user configs show that provider
  key is needed.
- Model/key/url shortcut projection after upstream config semantics are stable
  and tested.
- Native ACP/server-mode integrations for Qwen, Copilot, or Cursor remain
  deferred. Cursor's current supported interactive path is transcript-backed
  visible-pane execution, not ACP/server mode.
