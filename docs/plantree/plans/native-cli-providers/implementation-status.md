# Native CLI Providers Implementation Status

Date: 2026-07-21
Last updated: 2026-08-17

## Current Phase

Native completion pivot is implemented in source and `v7.5.0` has been
published. Kimi, DeepSeek/DeepCode, AGY, and MiMo now use provider-native
session/event logs or structured result streams for completion detection
instead of asking the model to print `CCB_DONE`. Kimi and OpenCode inherited
ask skill injection landed in commit `a4395c2`. MiMo inherited ask instruction
injection and `mimo run --format json` execution landed in commit `fce17c3`.

Official DeepSeek Harness support is published in `v8.6.9` under provider key
`dsh`; it does not replace the `deepseek` /
`deepcode` provider. DSH is modeled as a service-backed Web provider. The
current POSIX runtime pane only carries the signal-forwarding host process and
logs; asks, reply extraction, exact completion, restore, clear, and compact do
not use terminal input or pane-text heuristics. Unit and focused integration
regressions pass, as does an isolated official-host no-credential failure
probe. An external `ccb_test` mount using the official host also passed
exact-session restart, keyless native-error terminalization, clear rotation,
and structured compact. Authenticated answer success remains explicit
qualification evidence rather than an inferred claim.

Pi's per-job headless execution has been replaced in the source worktree by
visible managed-pane execution while preserving 8.5.0 headless jobs and an
explicit rollback mode. The terminal contract uses Pi's native
`agent_settled` event rather than interactive session-message stop reasons.
Authenticated Pi 0.82.1 visible-pane, clear/reuse, and headless rollback
acceptance passed; the slice is awaiting commit. Topic:
[topics/pi-visible-pane-completion.md](topics/pi-visible-pane-completion.md).
The other next-wave providers retain their previously landed paths.

Kimi follow-up receipt and diagnostics hardening has landed in source. This work
is explicitly Kimi-only: it does not change default provider behavior for Codex,
Claude, Gemini, OpenCode, DeepSeek, MiMo, AGY, or next-wave native CLI
providers. Topic:
[topics/kimi-receipt-and-diagnostics-hardening.md](topics/kimi-receipt-and-diagnostics-hardening.md).

Kimi exact-session restart hardening has also landed in the active source
candidate. First launch remains fresh. The completion reader binds a native
session to the per-agent CCB record only after observing that agent's exact
request anchor; manual/dead-pane restart then capability-checks and selects
only that id through a persisted command-template insertion point. Invalid or
unsupported bindings fail fresh without deleting provider state. This remains
separate from in-flight job resume support.

AGY delivery stability hardening has landed in source after a real
`main -> frontend_engineer:agy` empty-reply investigation. AGY now defers prompt
delivery until the Antigravity pane is input-ready, keeps busy-pane jobs running
instead of stacking retries into one native turn, records coalesced native
`USER_INPUT` diagnostics, keeps observing after ambiguous tmux send errors, and
can use stable pane fallback when transcript flushes lag. Topic:
[topics/agy-delivery-stability-hardening.md](topics/agy-delivery-stability-hardening.md).

The Issue #318 fix is published in `v8.6.9`. Managed AGY now refreshes
its provider-recognized recent-keyring-failure marker inside the agent-private
HOME before every launch, selecting file token storage immediately instead of
waiting for a failing OS keyring operation. User `spec.env` cannot redirect the
managed HOME, legacy marker links are detached, and the source credential home
remains read-only inheritance input. No setting is injected into other
providers.

Z.ai CLI provider support has landed in source as `provider = "zai"` using the
shared native CLI subprocess adapter. It targets conversational `zai` CLIs with
headless `--prompt` support, such as `@guizmo-ai/zai-cli`; official
`@z_ai/coding-helper` remains a setup helper for loading GLM Coding Plan into
other tools, not a standalone CCB ask runtime.

Grok completion hardening has landed in source. Successful Grok asks now
require provider-native `EndTurn` evidence with a non-empty reply. Process exit
without native terminal evidence is incomplete, and neither model-printed
`CCB_DONE` nor an internal CCB turn-boundary item is completion authority.

Grok startup argument normalization for Issue #255 has landed in source.
Explicit `startup_args = ["--fullscreen"]` now suppresses CCB's injected
`--minimal` default; unrelated startup arguments retain the minimal default.

Cursor visible-pane execution landed in merge commit `7a008597`. New Cursor
jobs use the named managed pane, wait for stable idle, bind completion to the
exact request anchor in a top-level transcript, and retain explicit headless
rollback. Interrupted in-flight Cursor jobs remain resubmit-required.

## Last Landed

- Stable `v8.6.9` was published from commit `677edc72c` with annotated tag
  `v8.6.9`, a bilingual GitHub Release, 10 platform/helper assets, and
  `@seemseam/ccb@8.6.9` on npm `latest`.
- Issue #318: AGY `1.1.13` managed launches select private file token storage
  through the confirmed private-home marker path without a source-home or
  cross-provider write path.
- Kimi exact-session restart now binds native identity only from an observed
  per-agent request anchor, persists one restart-command insertion point, and
  fails fresh when binding or capability validation fails. Real Kimi 1.47.0
  same-workdir agents resumed distinct native UUIDs and returned only their
  own hidden prior tokens after CCB-controlled restart. Focused/integration
  gates passed `45`, `120`, and `193` tests; the full Python remainder passed
  `5455` tests with `2` skips and `2` explicit baseline deselections.
- Fixed Issue #255 without changing the shared native CLI launcher: Grok
  selects a prebuilt fullscreen launch configuration only when its explicit
  startup arguments contain `--fullscreen`.
- Shared pane-quiet support and Kimi/DeepSeek provider backends were added in
  the earlier first slice and remain as compatibility/test support.
- Source-runtime smoke project added at
  `/home/bfly/yunwei/test_ccb2/native_provider_smoke`.
- Real Kimi validation project added at
  `/home/bfly/yunwei/test_ccb2/kimi_ccb_real` with `kimi1:kimi, kimi2:kimi`.
- Kimi launcher no longer injects implicit `--continue`; Kimi 1.47.0 exits
  when no previous session exists for the workdir.
- Pane-quiet parsing now ignores prompt-echo done markers, strips Kimi TUI's
  leading assistant bullet, and defers Kimi prompt delivery until the TUI input
  area is ready.
- Current native pivot:
  - Kimi reads `~/.kimi/sessions/<project-md5>/<session>/wire.jsonl`.
  - DeepSeek reads `~/.deepcode/projects/<project-code>/sessions-index.json`
    plus `<session>.jsonl`.
  - AGY reads
    `~/.gemini/antigravity-cli/brain/<conversation>/.system_generated/logs/transcript*.jsonl`.
  - Provider stubs now write those native stores for source runtime tests.
- Ask skill projection:
  - Kimi inherited ask skill lives at
    `inherit_skills/kimi_skills/ask/SKILL.md` and is passed to Kimi through a
    managed provider-state skills root with `--skills-dir`. CCB also passes
    existing Kimi default project/user skill directories first because Kimi
    treats explicit `--skills-dir` as replacement for default discovery.
  - OpenCode inherited ask guidance lives at
    `inherit_skills/opencode_skills/ask.md` and is appended to generated
    `opencode.json.instructions` through
    `.ccb/runtime/skills/<agent>/opencode/ask.md`.
- MiMo provider integration:
  - Provider key `mimo`, default command `mimo`, override `MIMO_START_CMD`.
  - Launch prepares `MIMOCODE_HOME`, `MIMOCODE_CONFIG`, memory bridge, and
    generated ask instruction path in MiMo `mimocode.json`.
  - CCB ask execution uses `mimo run --pure --format json --dir <workdir>` as a
    per-job native subprocess. The visible pane remains a managed MiMo session
    for user/runtime maintenance, but completion no longer depends on TUI pane
    prompt injection.
  - Completion parser handles MiMo 0.1.0 JSON events where reply text is in
    `part.text` and stop reason is in `part.reason` under a
    `step_finish` event.
- Next-wave native CLI provider implementation:
  - Optional provider ids `qwen`, `cursor`, `copilot`, `crush`, `kiro`, and
    `pi`
    are registered through provider-core manifests, execution adapters,
    session bindings, runtime launchers, runtime specs, command defaults, and
    session filenames.
  - Shared native CLI subprocess support now handles prompt wrapping without
    `CCB_DONE`, stdout/stderr artifacts, JSONL/stream-json result parsing,
    stdout-on-exit providers, empty reply diagnostics, nonzero exit failures,
    and tool/intermediate events that do not terminalize early.
  - Default commands and overrides:
    `qwen`/`QWEN_START_CMD`, `agent`/`CURSOR_START_CMD`,
    `copilot`/`COPILOT_START_CMD`, `crush`/`CRUSH_START_CMD`,
    `kiro-cli`/`KIRO_START_CMD`, and `pi`/`PI_START_CMD`.
  - Visible panes use the shared simple-tmux launcher. Qwen/Copilot ask
    execution uses per-job structured subprocesses and Crush/Kiro use process
    exit plus stdout. Cursor sends new asks to its named visible pane and
    observes exact anchored top-level transcript evidence; its structured
    subprocess is retained for explicit rollback. Pi sends new asks to its
    visible pane and observes exact `agent_settled` lifecycle sidecar evidence;
    its structured subprocess is retained for rollback and persisted 8.5.0
    jobs.
  - Talk1 review found one release-blocking gap: Crush ask execution used
    `--data-dir`, but the visible pane did not. The shared native CLI launcher
    now supports prepared-state-derived visible arguments, and the Crush visible
    pane starts with `--data-dir <provider-state>/data`.
  - Storage classification now recognizes Qwen, Cursor, Copilot, Crush, Kiro,
    and Pi provider-state contents as native CLI provider-owned session/cache
    or projected skill evidence instead of leaving them as unknown paths.
- Kimi receipt and diagnostics hardening:
  - Kimi inherited ask skill now projects a structured receipt contract with
    `status`, inspected files, findings, reject cases, required tests, no-open,
    and blockers fields.
  - Kimi native timeout with no captured reply emits `no_captured_reply`,
    `provider_no_reply`, `receipt_valid=false`, and
    `receipt_class=no_captured_reply`.
  - Forced `--artifact-reply` on a no-captured Kimi reply now stores the empty
    artifact as metadata while the visible reply says no Kimi provider reply was
    captured.
  - `ccb trace` job summaries expose Kimi terminal reason, reply chars, elapsed
    seconds, forced-artifact status, and receipt class.
  - Kimi provider manifest now reports `supports_resume=false` for CCB
    in-flight execution restore, matching adapter restore diagnostics.
- AGY delivery stability hardening:
  - AGY prompt delivery now waits for an input-ready Antigravity prompt before
    sending, storing pending prompts while the pane is busy.
  - AGY polling no longer treats old anchor-missing windows as terminal while
    the pane remains busy.
- AGY can emit stable pane fallback evidence when transcript writes lag.
- AGY preserves ambiguous tmux send errors as diagnostics and still accepts
  native transcript evidence instead of failing before attribution is known.
- Coalesced native `USER_INPUT` rows with multiple `CCB_REQ_ID` anchors are
  diagnosed as `agy_request_coalesced` for superseded jobs.
- Z.ai provider registration:
  - Built-in optional provider id: `zai`.
  - Pane launcher: `zai --directory <workspace>` with per-agent managed `HOME`.
  - Ask runner: `zai --directory <workspace> --no-color --prompt <wrapped prompt>`.
  - Override env: `ZAI_START_CMD`.
  - Completion detection: provider-native subprocess exit/stdout through the
    shared native CLI adapter, not model-printed `CCB_DONE`.

## Active TODO

1. Keep authenticated DSH answer success and real macOS AGY keyring-bypass
   startup as explicit post-landing qualification; do not infer either from
   keyless, stub, or local marker probes.
2. Decide whether to keep the smoke/real test projects as reusable validation
   fixtures.
3. Decide whether provider-specific auth diagnostics should land before the
   next public release or remain a follow-up.
4. Decide whether real authenticated blackbox asks for all six next-wave CLIs
   should be required before a public release or tracked as manual follow-up.

## Blocked By

None for design. Real provider API execution may require user-owned
Kimi/DeepSeek/MiMo/Qwen/Qoder/Copilot/Cursor/Kiro/Crush/Pi/Z.ai credentials; CCB integration
can still be validated with provider command templates, installed CLI
help/version checks, and source-backed parser tests.

Qoder `1.1.2` help/version and a credential-free real stream probe passed on
2026-07-22. The probe confirmed UUID-only `--session-id`, documented `-p`,
`--output-format stream-json`, `-w`, `--config-dir`, permission modes, and the
missing-login error envelope. CCB now parses that envelope fail-closed and
keeps visible/headless config state under the same agent-local root. A real
authenticated answer is not claimed.

Kimi hardening source work is unblocked. Remaining Kimi prompt-mode and auth
diagnostic ideas stay deferred/open until real usage needs them.

## Last Verified

`v8.6.9` release verification, 2026-08-17:

- Complete pytest gate in the dependency-qualified environment: `7166 passed,
  3 skipped, 4 subtests passed`.
- Linux/Windows release scripts, Mobile version surface, Windows x64 baseline,
  and trusted-base Windows PR isolation: `76 passed`.
- Issue #318 marker isolation and legacy-link regressions: `3 passed, 30
  deselected`; the installed AGY executable reports `1.1.13`.
- Bilingual release-note validation and `npm pack --dry-run` passed; the npm
  package identity is `@seemseam/ccb@8.6.9` with 19 allowlisted files.
- Isolated `/home/bfly/yunwei/ccb_source/ccb_test version` from
  `/home/bfly/yunwei/test_ccb2` reported source version `8.6.9`.
- Release Artifacts, Native Windows Release, Sidebar Helper, npm publish,
  Tests, Cross-Platform Compatibility, and CCBD Real Platform Smoke all
  completed successfully for commit `677edc72c`.
- Public Linux, macOS, Android, Windows, and Sidebar checksums matched; a fresh
  npm-prefix install returned `v8.6.9` and passed managed-runtime imports.

Cursor visible-pane verification, 2026-08-12:

- `test/test_cursor_provider.py`, `test/test_cursor_transcript.py`,
  `test/test_native_cli_provider_execution.py`, and
  `test/test_native_cli_providers.py`: `119 passed`.
- Coverage includes default/rollback selection, exact anchor binding,
  stale/subagent exclusion, transcript rewrite, stable-idle deferred delivery,
  terminal evidence, timeout, pane death, reply delivery, and cancellation.

Grok fullscreen startup regression, 2026-07-15:

- The default/unrelated-argument path and explicit fullscreen override path
  passed together (`9 passed`).
- The expanded Grok, native CLI provider/execution/completion, launcher, and
  session-file regression set passed (`186 passed`).
- Config loading, provider catalog/registry, and repository hygiene regressions
  passed (`139 passed`).

Grok native CCB skill projection verification, 2026-07-13:

- Grok normal-start permission alignment passed launcher regression coverage
  (`11 passed`) and the wider Grok/native execution set (`202 passed`). In the
  opened real project, both session records contained
  `--permission-mode bypassPermissions`, both TUI footers reported
  `always-approve`, and `grok1` executed `pwd` without an approval prompt.
  Safe-start coverage verifies that `ccb -s` emits neither bypass mode nor CCB
  skill allow rules.
- Added instance-local native `ask` and `ccb-clear` packages, ownership-marked
  projection, normal-start native bypass permission, safe-start isolation,
  persisted policy, exact visible-pane caller environment, disable/conflict
  behavior, and storage classification.
- Focused Grok, native execution, launcher, skill-template, hygiene, storage,
  and release-package tests: `204 passed`.
- Both skill packages passed the skill creator `quick_validate.py` gate.
- Real source-runtime project
  `/home/bfly/yunwei/test_ccb2/grok-ask-emergency-20260713` mounted `grok1` and
  `grok2`; native `grok inspect --json` found both skills at distinct managed
  home paths, and both session records persisted exact caller identity plus
  only `Bash(command ask *)` and `Bash(command ccb clear*)` allow rules.
- The stale system OAuth file was reproduced first: managed copies matched it
  byte-for-byte, while Grok reported `invalid_grant` and a revoked refresh
  token. After `grok login --oauth`, both managed homes inherited the new
  system auth file byte-for-byte and reported `is_expired=false`.
- Authenticated direct job `job_a42e4b458172` returned exactly
  `GROK_DIRECT_PROXY_OK_0713` and completed from native `EndTurn` as
  `grok_run_stop`.
- Authenticated parent job `job_da429c4e9645` loaded the injected `ask` skill,
  created child job `job_caa4e135bb0c` on `grok2`, recovered the child result
  through continuation job `job_963718371d32`, and returned
  `GROK_PARENT_CHAIN_OK_0713` to the original caller.
- Authenticated job `job_f39147abc472` loaded `ccb-clear` and ran exactly
  `ccb clear grok2`; CCB reported one cleared target and no skips or failures.
  Post-clear jobs `job_dd26aa9feb33` and `job_fc5b5d865c59` proved `grok2`
  remained usable and `grok1` remained untouched.
- The first opened-frontend test exposed that per-job headless execution hid
  both the request and reply from the managed Grok panes. Grok execution now
  sends the request to the target pane, reads that visible session's native
  `updates.jsonl`, and binds completion to the matching
  `turn_completed/end_turn` event. Reply delivery is dispatched to the
  caller's visible pane.
- Opened-frontend job `job_3d7385171e82` visibly appeared in `grok1`, returned
  `FRONTEND_GROK1_VISIBLE_OK_0713` from native `end_turn`, and reply-delivery
  job `job_6892ec58f379` visibly returned the result to `grok2`. Both panes ran
  Grok's terminal-native `--minimal` UI so requests and replies remained
  inspectable in tmux scrollback.
- The host's intercepted DNS route initially sent Grok traffic to the wrong
  endpoint. Relaunching the source runtime with the user's local Clash proxy
  environment restored the provider network path; no CCB proxy fallback or TLS
  weakening was added.

Grok native completion verification:

- Focused Grok and structured-event tests: `25 passed, 29 deselected`.
- Complete Grok/native CLI execution files: `54 passed`.
- Wider provider/registry tests: `31 passed`.
- Real authenticated source-runtime job `job_8e38805500d5` returned
  `GROK_NATIVE_ENDTURN_OK_0711`; raw provider output contained
  `type=end`, `stopReason=EndTurn`, and trace terminalized with
  `grok_run_stop`.

AGY delivery stability focused verification:

- `PYTHONPATH=lib python -m pytest -q test/test_agy_execution_polling.py test/test_native_cli_completion.py -k agy`:
  `8 passed, 12 deselected`.
- `PYTHONPATH=lib python -m pytest -q test/test_agy_execution_polling.py test/test_native_cli_completion.py test/test_native_cli_providers.py test/test_v2_provider_catalog.py test/test_opencode_execution_polling.py`:
  `34 passed`.
- Isolated source-runtime smoke from
  `/home/bfly/yunwei/test_ccb2/native_provider_smoke` with
  `/home/bfly/yunwei/ccb_source/ccb_test`, stub AGY, isolated `HOME` and
  `CCB_SOURCE_HOME`: completed `job_74d4989cca04` with
  `agy_transcript_response_done`.

Kimi hardening focused verification:

- `PYTHONPATH=lib python -m pytest -q test/test_ask_skill_templates.py`:
  `3 passed`.
- `PYTHONPATH=lib python -m pytest -q test/test_native_cli_completion.py -k kimi`:
  `8 passed, 5 deselected`.
- `PYTHONPATH=lib python -m pytest -q test/test_v2_message_bureau_dispatcher_integration.py -k artifact`:
  `7 passed, 54 deselected`.
- `PYTHONPATH=lib python -m pytest -q test/test_v2_cli_render.py -k trace`:
  `3 passed, 21 deselected`.
- `PYTHONPATH=lib python -m pytest -q test/test_v2_provider_catalog.py`:
  `4 passed`.
- `PYTHONPATH=lib python -m py_compile` for touched Kimi, artifact, trace, and
  catalog modules: passed.
- `git diff --check`: passed.

## 2026-07-22 Kimi And Qoder Corrective Verification

Issue/PR review found two provider-contract gaps and closed them on the unified
repair branch:

- Kimi native observation now binds only the current leading request header,
  ignores thinking text, accepts only explicit successful terminal states, and
  supports both legacy `.kimi/sessions/.../wire.jsonl` and current
  `.kimi-code/sessions/wd_<name>_<hash>/<session>/agents/<agent>/wire.jsonl`
  layouts. Exact session roots are launcher-recorded and fail fresh on drift.
- Qoder now uses the real `qodercli` headless surface: `-p`, `-w`,
  `--config-dir`, `--output-format stream-json`, a deterministic UUID session
  id, and an explicit permission mode. Completion requires the native result
  envelope; auth/error results and zero exit without terminal evidence fail
  closed.
- The optional execution-registry expectation now includes `qoder`; the
  duplicate `grok` expectation was removed.

Evidence:

- Kimi focused suites: `202 passed`.
- Qoder focused suites: `183 passed`.
- Official `@qoder-ai/qodercli@1.1.2` was installed in an isolated external
  probe. Its real help and credential-free stream confirmed UUID-only session
  ids, the documented headless flags, `system/init`, assistant error, and
  `result is_error=true`. The CCB-generated command reached that native result
  and failed closed as `Not logged in` without reading credentials.
- Final cross-feature affected-suite run after all four repairs:
  `418 passed in 81.18s`.
- Full repository run before correcting the registry expectation:
  `5595 passed, 2 skipped, 2 failed in 1026.68s`; the deterministic failure was
  the stale Qoder expected set and is fixed. Its registry follow-up is
  `20 passed`. The other failure was an unrelated ccbd heartbeat test timing
  race under full-suite load; the exact test passed once immediately and then
  10 consecutive isolated repeats.
- From `/home/bfly/yunwei/test_ccb2`, the repair worktree's `ccb_test
  --diagnose` reported `allowed_source_test_project: yes`; `--print-version`
  returned `v8.2.1`, and `doctor` completed with the repair worktree recorded as
  `install_path`/source authority.

## 2026-07-24 Qoder CLI CN Contract Landing

- Added provider key `qoderclicn` for `@qodercn-ai/qoderclicn@1.1.3`, binary
  `qoderclicn`, with Node `>=20`.
- Reused the corrected Qoder command and stream contracts: agent-local config,
  explicit workspace and permission mode, print-mode stream JSON, and a
  deterministic UUIDv5 instead of the rejected raw CCB job id.
- Added a Qoder CN observer with provider-specific terminal reasons. Native
  completion requires `result.is_error=false` and a normal stop reason;
  assistant text does not terminalize, auth failures fail, and exit zero without
  a result reports `qoderclicn_native_terminal_missing`.
- Unified visible and headless `qoderclicn_config_dir` state, preserved explicit
  command/startup config and permission options, and mapped automatic permission
  to `auto` with `dont_ask` as the normal headless default.
- Agent-local `settings.json` now preserves existing keys while disabling both
  `general.enableAutoUpdate` and `general.enableAutoUpdateNotification`. The
  user-global `~/.qoder-cn/settings.json` is outside CCB's write boundary.
- Classified Qoder CN `.auth/` state as secret and added focused command,
  observer, launcher, registry, runtime, and storage regression coverage.

Historical first-slice verification:

- `node --version` returned `v22.20.0`.
- `npm view @moonshot-ai/kimi-code@0.14.2 version bin engines --json` returned
  bin `kimi` and engine `>=22.19.0`.
- `npm view @vegamo/deepcode-cli@0.1.29 version bin engines --json` returned
  bin `deepcode` and engine `>=22`.
- `npx --yes @moonshot-ai/kimi-code@0.14.2 --help` and `--version` succeeded.
- `npx --yes @vegamo/deepcode-cli@0.1.29 --help` and `--version` succeeded.
- `python -m pytest -q test/test_pane_quiet_support.py
  test/test_native_cli_providers.py test/test_v2_provider_catalog.py
  test/test_v2_provider_core_registry.py test/test_runtime_specs.py`:
  `18 passed`.
- Focused config/runtime-launch checks: `3 passed`.
- Full `test/test_v2_config_loader.py`: `87 passed`.
- Full `test/test_v2_runtime_launch.py`: `78 passed`.
- Kill/provider catalog/registry focused set: `12 passed`.
- Execution/completion/session-binding related set: `88 passed`.
- `python -m py_compile` for new provider modules and touched stubs passed.
- `git diff --check` passed.
- Full repository `python -m pytest -q`: `2585 passed, 2 skipped`.
- `/home/bfly/yunwei/ccb_source/ccb_test --diagnose` passed from
  `/home/bfly/yunwei/test_ccb2/native_provider_smoke`.
- Smoke `ccb_test config validate`: valid, agents `deep1, kimi1`.
- Smoke `ccb_test -s`: launched `kimi1` and `deep1`.
- Smoke `ccb_test ask`: both providers completed with
  `completion_reason: pane_done_marker`.
- Smoke `ccb_test restart kimi1` and `restart deep1`: both `restart_status: ok`.
- Smoke post-restart asks: both completed with `pane_done_marker`.
- Smoke `ccb_test reload --dry-run`: `plan_class: no_change`.
- Smoke runtime stopped with `ccb_test kill -f`.
- Real Kimi CLI install check from `/home/bfly/yunwei/test_ccb2`:
  `kimi --version` returned `kimi, version 1.47.0`.
- Real Kimi CCB project
  `/home/bfly/yunwei/test_ccb2/kimi_ccb_real`:
  `ccb_test config validate` valid, agents `kimi1, kimi2`.
- Real Kimi `ccb_test -s` launched both Kimi panes with command `kimi`.
- Real Kimi immediate post-start ask completed with
  `reply: KIMI_READY_SEND_OK` and `completion_reason: pane_done_marker`,
  proving ready-before-send protection.
- Real Kimi serial ask set completed for `KIMI_SERIAL_OK_1` through
  `KIMI_SERIAL_OK_5`.
- Real Kimi after-fix concurrent pressure set submitted 8 jobs across
  `kimi1` and `kimi2`; all completed with exact replies
  `KIMI_AFTERFIX_OK_1` through `KIMI_AFTERFIX_OK_8`.
- Real Kimi `ccb_test restart kimi1` succeeded; post-restart ask completed
  with `reply: KIMI_RESTART_AFTERFIX_OK`.
- Real Kimi `ccb_test clear kimi1 kimi2` succeeded; post-clear ask completed
  with `completion_reason: pane_done_marker`.
- Real Kimi artifact reply path stored
  `job_29c1cf2fb1a2-art_0bc032960b444864.txt`.
- Focused related pytest set after the real-Kimi fixes:
  `113 passed`.

Current native pivot verification:

- Native pivot compile check:
  `python -m py_compile test/stubs/provider_stub.py
  lib/provider_backends/kimi/execution.py
  lib/provider_backends/deepseek/execution.py
  lib/provider_backends/agy/execution_runtime/poll.py`: passed.
- Native pivot focused tests:
  `python -m pytest -q test/test_agy_execution_polling.py
  test/test_native_cli_completion.py
  test/test_native_cli_providers.py test/test_v2_provider_catalog.py
  test/test_pane_quiet_support.py`: `25 passed`.
- Native pivot focused config/catalog tests:
  `python -m pytest -q test/test_native_cli_completion.py
  test/test_native_cli_providers.py test/test_v2_provider_catalog.py
  test/test_v2_provider_core_registry.py test/test_runtime_specs.py
  test/test_v2_config_loader.py -k 'native or provider_catalog or optional or
  kimi or deepseek or agy or runtime_spec'`: `16 passed, 90 deselected`.
- Source-runtime smoke from
  `/home/bfly/yunwei/test_ccb2/native_provider_smoke` with isolated
  `HOME=/home/bfly/yunwei/test_ccb2/source_home`:
  - `ccb_test config validate`: valid, agents `agy1, deep1, kimi1`.
  - `ccb_test -s`: `start_status: ok`, agents `kimi1, deep1, agy1`.
  - `ccb_test ask kimi1`: job `job_462bb2fd5afb`, reply completed with
    reason `kimi_turn_end`.
  - `ccb_test ask deep1`: job `job_bfa7f505da0f`, reply completed with
    reason `deepseek_session_completed`.
  - `ccb_test ask agy1`: job `job_bd583f5b76cb`, reply completed with
    reason `agy_transcript_response_done`.
  - Native files observed under source home:
    Kimi `wire.jsonl`, DeepCode `sessions-index.json`/session jsonl, and AGY
    `transcript.jsonl`.
  - `ccb_test ping all`: mounted and idle for `agy1`, `deep1`, `kimi1`.
  - Smoke runtime stopped with `ccb_test kill -f`.
- Source-marker probe:
  - Kimi real 1.47.0 local wire logs use
    `TurnBegin`/`ContentPart`/`StatusUpdate`/`TurnEnd`.
  - Kimi npm source `@moonshot-ai/kimi-code@0.14.2` also exposes event-stream
    names `turn.started`/`assistant.delta`/`turn.ended`; the Kimi parser now
    accepts source-style `turn.prompt`/`assistant.delta`/`turn.ended` records
    when they appear in `wire.jsonl`.
  - Kimi launcher now injects `--auto-approve` for CCB auto-permission on
    current Kimi versions, while treating legacy/alias flags
    `--auto`, `--auto-approve`, `--yes`, `-y`, and `--yolo` as explicit
    auto-permission flags to avoid duplication.
  - DeepCode source `@vegamo/deepcode-cli@0.1.29` confirms
    `permission_denied`; DeepSeek polling now terminalizes it as
    `deepseek_native_permission_denied`.
  - AGY local runtime artifacts confirm transcript `DONE` rows as the stable
    evidence surface; sqlite conversation DB status enums remain diagnostic
    only.
  - OpenCode npm package `opencode-ai@1.16.2` is a binary installer wrapper;
    CCB's existing OpenCode storage contract remains the native completion
    authority (`time.completed`).
- Source-marker focused verification:
  - `python -m py_compile lib/provider_backends/kimi/native_log.py
    lib/provider_backends/deepseek/native_log.py
    lib/provider_backends/deepseek/execution.py
    test/test_native_cli_completion.py`: passed.
  - `python -m pytest -q test/test_native_cli_completion.py`: `8 passed`.
  - `python -m pytest -q test/test_agy_execution_polling.py
    test/test_native_cli_completion.py test/test_native_cli_providers.py
    test/test_v2_provider_catalog.py test/test_pane_quiet_support.py`:
    `27 passed`.
- Kimi auto flag compatibility verification:
  - `kimi --auto-approve --version`: succeeded on local Kimi 1.47.0.
  - `python -m py_compile lib/provider_backends/kimi/launcher.py
    test/test_native_cli_providers.py`: passed.
  - `python -m pytest -q test/test_native_cli_providers.py`: `5 passed`.

Next-wave provider source verification:

- Landing history:
  [history/next-wave-provider-landing-2026-06-13.md](history/next-wave-provider-landing-2026-06-13.md)
- Compile check:
  `python -m py_compile lib/provider_backends/native_cli_support/*.py
  lib/provider_backends/qwen/*.py lib/provider_backends/copilot/*.py
  lib/provider_backends/cursor/*.py lib/provider_backends/crush/*.py
  lib/provider_backends/kiro/*.py lib/provider_core/runtime_specs.py
  lib/provider_core/runtime_shared.py lib/provider_core/pathing.py
  lib/provider_core/registry_runtime/builtin_backends.py
  test/stubs/provider_stub.py`: passed.
- Focused source tests:
  `pytest -q test/test_native_cli_provider_execution.py
  test/test_v2_provider_catalog.py test/test_v2_provider_core_registry.py
  test/test_v2_execution_registry.py test/test_runtime_specs.py
  test/test_v2_runtime_launch.py::test_provider_start_parts_respect_env_override
  test/test_v2_runtime_launch.py::test_provider_start_parts_fall_back_to_default_binary
  test/test_v2_runtime_launch.py::test_native_cli_launcher_builds_provider_state_payload`:
  `35 passed`.
- Native CLI adapter timeout regression:
  `pytest -q test/test_native_cli_provider_execution.py`: `23 passed`.
  This covers completed, empty reply, nonzero exit, run timeout, and structured
  tool-event intermediate behavior for `qwen`, `cursor`, `copilot`, `crush`,
  and `kiro`.
- Control-plane env regression:
  `pytest -q test/test_runtime_env_control_plane.py
  test/test_v2_runtime_launch.py::test_provider_start_parts_respect_env_override
  test/test_v2_runtime_launch.py::test_provider_start_parts_fall_back_to_default_binary`:
  `9 passed`.
- Wider touched-provider suite:
  `pytest -q test/test_native_cli_provider_execution.py
  test/test_v2_provider_catalog.py test/test_v2_provider_core_registry.py
  test/test_v2_execution_registry.py test/test_runtime_specs.py
  test/test_v2_config_loader.py test/test_v2_runtime_launch.py
  test/test_storage_classification.py test/test_repo_hygiene.py`:
  `238 passed`.
- Talk1 blocker fix verification:
  `pytest -q
  test/test_v2_runtime_launch.py::test_native_cli_launcher_builds_provider_state_payload
  test/test_storage_classification.py`: `14 passed`.
- Native execution/control-plane regression after the blocker fix:
  `pytest -q test/test_native_cli_provider_execution.py
  test/test_runtime_env_control_plane.py`: `30 passed`.
- Compile check after the blocker fix:
  `python -m py_compile lib/provider_backends/native_cli_support/launcher.py
  lib/provider_backends/crush/launcher.py
  lib/storage_classification/provider_home.py
  test/test_v2_runtime_launch.py test/test_storage_classification.py`: passed.
- Source-runtime smoke from
  `/home/bfly/yunwei/test_ccb2/next_wave_provider_smoke` with isolated
  `HOME=/home/bfly/yunwei/test_ccb2/source_home` and provider `*_START_CMD`
  overrides pointing at `test/stubs/provider_stub.py`:
  - `ccb_test config validate`: valid, agents
    `qwen1, cursor1, copilot1, crush1, kiro1`.
  - `ccb_test -s`: `start_status: ok`, mounted all five providers.
  - Initial launch exposed a control-plane env gap: ccbd filtered
    `QWEN_START_CMD` and failed with `qwen executable not found in PATH`.
    `runtime_env.control_plane` now explicitly passes provider
    start-command override env vars while continuing to filter outer provider
    home/session authority.
  - `ccb_test ask qwen1`: job `job_1129f5da5303`, completed with
    reason `qwen_run_stop`.
  - `ccb_test ask cursor1`: job `job_2e2e1e2d4b9a`, completed with
    reason `cursor_run_stop`.
  - `ccb_test ask copilot1`: job `job_a302bc361685`, completed with
    reason `copilot_run_stop`.
  - `ccb_test ask crush1`: job `job_b4c402fa2231`, completed with
    reason `crush_run_exit`.
  - `ccb_test ask kiro1`: job `job_9ad49ec0674a`, completed with
    reason `kiro_run_exit`.
  - `ccb_test pend --queue --detail all`: queue depth 0, pending replies 0,
    all five agents idle/restored.
  - Post-review recheck confirmed `.ccb/.crush-crush1-session` start command
    contains `--data-dir` with
    `/home/bfly/yunwei/test_ccb2/next_wave_provider_smoke/.ccb/agents/crush1/provider-state/crush/data`.
  - Post-review asks completed:
    `job_4f21ddb8ba16` (`qwen_run_stop`),
    `job_2450b6ebe7fa` (`cursor_run_stop`),
    `job_264559b3e599` (`copilot_run_stop`),
    `job_4217a811aebe` (`crush_run_exit`), and
    `job_abaee56ecd84` (`kiro_run_exit`).
  - Post-review `ccb_test pend --queue --detail all`: queue depth 0, pending
    replies 0, all five agents idle/restored.
  - Smoke runtime stopped with `ccb_test kill -f`.
- Current focused verification after Pi landing:
  `python -m pytest -q test/test_native_cli_provider_execution.py
  test/test_runtime_env_control_plane.py test/test_v2_provider_catalog.py
  test/test_v2_provider_core_registry.py test/test_v2_execution_registry.py
  test/test_runtime_specs.py test/test_v2_config_loader.py
  test/test_v2_runtime_launch.py test/test_storage_classification.py
  test/test_repo_hygiene.py`: `238 passed`.
  `python -m py_compile` for touched provider/core modules passed.
  `git diff --check` passed.
- Real CLI version smoke from
  `/home/bfly/yunwei/test_ccb2/cli-integration-lab`:
  - `qwen --version`: `0.18.0`.
  - `agent --version`: `2026.06.12-19-59-36-f6aba9a`.
  - `copilot --version`: `GitHub Copilot CLI 1.0.61`.
  - `crush --version`: `crush version v0.76.0`.
  - `kiro-cli --version`: `kiro-cli 2.7.0`.
  - `pi --version`: `0.79.3`.
- Pi add-on source-runtime smoke after adding `pi1:pi`:
  - Installed `@earendil-works/pi-coding-agent@0.79.3` into
    `/home/bfly/yunwei/test_ccb2/cli-integration-lab/npm-prefix`.
  - `/home/bfly/yunwei/test_ccb2/.ccb/ccb.config` now includes `pi1:pi` in the
    `native` window, with PATH pointing at the lab npm prefix.
  - `/home/bfly/yunwei/test_ccb2/next_wave_provider_smoke/.ccb/ccb.config`
    now includes `pi1:pi`.
  - `ccb_test config validate` accepted all six smoke agents.
  - `ccb_test -s` mounted `qwen1, cursor1, copilot1, crush1, kiro1, pi1`.
  - `ccb_test ask pi1`: job `job_69a5905eb423`, completed with reply
    `stub reply for job_69a5905eb423`.
  - `ccb_test trace job_69a5905eb423`: reply reason `pi_run_stop`.
  - `.ccb/.pi-pi1-session` start command contains isolated
    `PI_CODING_AGENT_DIR`, `PI_CODING_AGENT_SESSION_DIR`,
    `PI_SKIP_VERSION_CHECK=1`, `PI_TELEMETRY=0`, and visible
    `--session-dir <provider-state>/sessions --no-approve`.
  - Smoke runtime stopped with `ccb_test kill -f`.
- Full source test gate on the current branch:
  `pytest test/ -q -m "not provider_blackbox"`:
  `2621 passed, 2 skipped, 21 deselected`.
- Full compile gate:
  `python -m compileall -q lib bin ccb`: passed.
- `git diff --check`: passed.

Ask skill injection verification:

- `python -m py_compile lib/provider_backends/opencode/launcher.py
  lib/provider_backends/kimi/launcher.py lib/provider_backends/kimi/skills.py
  lib/provider_core/inherited_skills.py lib/storage_classification/service.py
  lib/storage_classification/provider_home.py`: passed.
- `python -m pytest -q test/test_native_cli_providers.py
  test/test_provider_hook_settings.py test/test_v2_runtime_launch.py
  test/test_project_memory_real_context.py
  test/test_provider_memory_external_matrix.py test/test_storage_classification.py
  test/test_repo_hygiene.py test/test_ask_skill_templates.py`:
  `141 passed, 1 skipped`.
- `git diff --check`: passed.
- From `/home/bfly/yunwei/test_ccb2` with isolated source home,
  `/home/bfly/yunwei/ccb_source/ccb_test --diagnose`: passed.
- From `/home/bfly/yunwei/test_ccb2` with isolated source home,
  `/home/bfly/yunwei/ccb_source/ccb_test config validate`: valid.

MiMo provider verification:

- Real installed MiMo package:
  - `npm view @mimo-ai/cli` showed latest stable `0.1.0`.
  - `npm install -g @mimo-ai/cli@0.1.0` installed binary `mimo`.
  - `mimo --version` returned `0.1.0`.
- Real native run:
  - From `/home/bfly/yunwei/test_ccb2/mimo_real` with isolated MiMo home,
    `mimo run --format json --dir ... 'Reply exactly: MIMO_CCB_REAL_OK'`
    exited 0 and produced JSON `text` plus `step_finish reason=stop`.
- Source-runtime real CCB run:
  - `/home/bfly/yunwei/ccb_source/ccb_test --diagnose` passed from
    `/home/bfly/yunwei/test_ccb2`.
  - `/home/bfly/yunwei/test_ccb2/mimo_ccb_real/.ccb/ccb.config` contains
    `cmd; mimo1:mimo`.
  - `ccb_test config validate`: valid, agent `mimo1`.
  - `ccb_test -s`: `start_status: ok`.
  - First TUI-style attempt proved the old pane prompt injection path did not
    create MiMo DB messages; this drove the decision to switch CCB execution to
    `mimo run --format json`.
  - After implementing nested MiMo JSON parsing and restarting ccbd,
    `ccb_test ask mimo1 'Reply exactly: MIMO_CCB_RUN_OK_3'` completed job
    `job_ae41cad0e98a` with reply `MIMO_CCB_RUN_OK_3` and
    `completion_reason: mimo_run_stop`.
  - Matching stdout artifact:
    `.ccb/agents/mimo1/provider-runtime/mimo/completion/job_ae41cad0e98a.mimo-run.jsonl`
    contained `part.text = MIMO_CCB_RUN_OK_3` and
    `step_finish` / `part.reason = stop`.
- Release-gate rerun:
  - A non-pure probe exposed `mimo_run_finished:tool-calls` with an empty
    reply after MiMo invoked its memory plugin.
  - CCB MiMo execution now passes `--pure`, and tool-call `step_finish` is
    treated as an intermediate finish reason until final text or process exit.
  - After restart, `ccb_test ask mimo1 'Reply exactly: MIMO_RELEASE_751_OK'`
    completed job `job_023d114681ca` with reply `MIMO_RELEASE_751_OK` and
    `completion_reason: mimo_run_stop`.
- Focused final test set:
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
- Full release-gate pytest: `2613 passed, 2 skipped`.

Next-wave install/source lab:

- External lab root:
  `/home/bfly/yunwei/test_ccb2/cli-integration-lab`.
- Installed binary checks:
  - `qwen --version`: `0.18.0`.
  - `copilot --version`: `GitHub Copilot CLI 1.0.61`.
  - `crush --version`: `crush version v0.76.0`.
  - `agent --version`: `2026.06.12-19-59-36-f6aba9a`.
  - `kiro-cli --version`: `kiro-cli 2.7.0`.
- Installed package/source checks:
  - `@qwen-code/qwen-code` exposes bin `qwen` and requires Node `>=22.0.0`.
  - `@github/copilot` exposes bin `copilot`.
  - `@charmland/crush` exposes bin `crush`.
  - Cursor Agent and Kiro CLI install from official shell installers, not npm.
- Source/bundle locations:
  - `/home/bfly/yunwei/test_ccb2/cli-integration-lab/src/qwen-code`.
  - `/home/bfly/yunwei/test_ccb2/cli-integration-lab/src/copilot-cli`.
  - `/home/bfly/yunwei/test_ccb2/cli-integration-lab/src/crush`.
  - `/home/bfly/yunwei/test_ccb2/cli-integration-lab/src/kiro`.
  - `/home/bfly/yunwei/test_ccb2/cli-integration-lab/home/.local/share/cursor-agent/versions/2026.06.12-19-59-36-f6aba9a`.
- `git diff --check`: passed.
