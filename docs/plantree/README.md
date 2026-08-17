# CCB Plan Tree

Date: 2026-05-25

## Purpose

This is the planning entrypoint for durable project plans that should be easy
to resume across agents and sessions.

## Authority Order

1. Product/runtime contracts under `docs/` remain authoritative for shipped
   behavior.
2. Registered plan roots under `docs/plantree/plans/` define active planning
   state.
3. `implementation-status.md` files are operational handoff notes and must not
   override roadmap or decision records.
4. Legacy plan files outside this tree are preserved in place until a specific
   migration decision exists.

## Baseline

- [baseline/README.md](baseline/README.md) indexes the lightweight project
  baseline used by plan roots.

## Active Plans

| Plan | Status | Scope |
| :--- | :--- | :--- |
| [readme-v7-redesign](plans/readme-v7-redesign/README.md) | In progress | Redesign public README content, screenshots, demo videos, and tmux onboarding for the v7 release line. |
| [sidebar-tips-layout](plans/sidebar-tips-layout/README.md) | In progress | Add a three-panel sidebar layout with compact Comms and configurable tmux Tips. |
| [sidebar-provider-activity](plans/sidebar-provider-activity/README.md) | Planning | Add provider-native activity evidence and structured runtime status for ProjectView clients, including sidebar and CCB Mobile Codex/Claude state. |
| [config-designer-ui](plans/config-designer-ui/README.md) | In progress | Add a local browser editor for `.ccb/ccb.config` and expose it through the native sidebar settings icon. |
| [ccbd-agent-hot-reload](plans/ccbd-agent-hot-reload/README.md) | In progress | Dynamically load, unload, and later replace agents in a running daemon without breaking unrelated panes. |
| [managed-tool-windows](plans/managed-tool-windows/README.md) | In progress | Add first-class non-agent tool windows and optional rich workbench surfaces that appear in sidebar without provider/agent rows. |
| [project-command-trust](plans/project-command-trust/README.md) | Implemented, verified, and committed locally | Require external, exact-value user approval before project-local tool and provider command fields execute. |
| [windows-wezterm-native](plans/windows-wezterm-native/README.md) | Planning | Evaluate and stage a Windows-native WezTerm mux backend for current CCB without tmux-based communication. |
| [windows-native-release](plans/windows-native-release/README.md) | Published; qualification open | CCB v8.6.6 continues the stable release lanes with an isolated Windows x64 beta artifact; real Windows GUI/provider qualification remains open. |
| [python-rust-hybrid-performance](plans/python-rust-hybrid-performance/README.md) | In progress | Stage performance-oriented Rust helpers around the current Python control plane without a full rewrite. |
| [ccb-runtime-performance](plans/ccb-runtime-performance/README.md) | In progress | Attribute startup, high-load, and pane-interaction CPU/latency across CCB core, shell/tmux, provider processes, and UI paths before selecting optimization work. |
| [ci-test-gates](plans/ci-test-gates/README.md) | Implemented locally; remote CI pending | Replace the repeated OS-by-Python full-suite product with orthogonal unit, lifecycle, specialist, and real-platform gates while preserving failure authority. |
| [ccb-idle-resource-pressure](plans/ccb-idle-resource-pressure/README.md) | In progress | Reduce SSD writes, memory use, and idle CPU when many CCB providers remain mounted but unused. |
| [rolepack-system](plans/rolepack-system/README.md) | Planning | Define a host-neutral Role Pack system for reusable agent roles, with CCB installation, projection, and governance as the first adapter. |
| [provider-memory-ownership](plans/provider-memory-ownership/README.md) | In progress | Replace ad hoc provider memory bundling with a source ownership manifest across Claude, Codex, and OpenCode. |
| [agent-roles-open-source](plans/agent-roles-open-source/README.md) | Planning | Plan the public `agent-roles` GitHub project as a spec-first RolePack standard with templates, reference roles, and future host adapters. |
| [install-update-stability](plans/install-update-stability/README.md) | In progress | Make fresh install, managed update, provider CLI updates, dependency provisioning, Role Pack refresh, and bilingual user output stable across supported environments. |
| [source-runtime-isolation](plans/source-runtime-isolation/README.md) | Planning | Keep source editing, source-under-test validation, and installed-release work environments isolated from each other. |
| [project-identity-relocation](plans/project-identity-relocation/README.md) | In progress | Decouple stable CCB project identity from absolute paths and recover stopped projects safely after directory moves or renames. |
| [ccb-self-role](plans/ccb-self-role/README.md) | Planning | Design the `agentroles.ccb_self` maintenance role, its broad skills, CCB/tmux diagnostics tools, and self-contained recovery boundaries. |
| [ccb-maintenance-heartbeat](plans/ccb-maintenance-heartbeat/README.md) | Planning | Add a generic CCB-owned maintenance heartbeat that independently diagnoses agent health and escalates risk, unknown, or unhealthy states to a semantic assessor, defaulting to `ccb_self`. |
| [codex-reconnect](plans/codex-reconnect/README.md) | Implemented; real-fault qualification open | Automatically arm each bound CCB Codex session, while retaining standalone `$reconnect on/off`, for same-model recovery after terminal network or service-overload failures. |
| [agentic-loop-workflow](plans/agentic-loop-workflow/README.md) | In progress | Current release target: one lane with one semantic bundle, 1-4 reviewed Worker+Reviewer workgroups, deterministic integration/release, opt-in Config V3, Config V2 compatibility, visible real-provider acceptance, and package/install gates. |
| [ccb-tui-workbench](plans/ccb-tui-workbench/README.md) | Planning | Add a first-window CCB TUI client with stable Frontdesk conversation, workflow status, clarification routing, serial task queueing, and result collection. |
| [mobile-gateway-service-lifecycle](plans/mobile-gateway-service-lifecycle/README.md) | Planning | Make `ccb update mobile` own a unique host-wide background mobile gateway, replacing stale managed services instead of failing on occupied loopback ports. |
| [managed-provider-completion-reliability](plans/managed-provider-completion-reliability/README.md) | Planning | Track managed pane-backed provider completion terminalization, empty-reply guards, and timeout reliability fixes. |
| [managed-provider-job-integrity](plans/managed-provider-job-integrity/README.md) | In progress | Repair provider plugin inheritance, native resume, turn binding, cancellation, execution diagnostics, and active-job control findings from PR257-PR266 in a gated order. |
| [provider-auth-authority](plans/provider-auth-authority/README.md) | In progress | Define one-way external Provider auth/config inheritance, CCB-local explicit API authority, and server-side-safe credential ownership without reverse interference. |
| [inter-agent-comm-reliability](plans/inter-agent-comm-reliability/README.md) | Planning | Track inter-agent message transport reliability proposals such as PR226-style persistent FIFO, ACK, large-payload spool, and cancel visibility for Linux, macOS, and WSL. |
| [callback-continuation-safety](plans/callback-continuation-safety/README.md) | In progress | Prevent callback continuation jobs from being misinterpreted as new upstream callback work, especially in mixed Codex/Claude chains. |
| [native-cli-providers](plans/native-cli-providers/README.md) | In progress | Add first-class optional provider adapters and control-skill projection for native CLIs plus the service-backed official DeepSeek Harness (`dsh`). |
| [workspace-sharing](plans/workspace-sharing/README.md) | In progress | Add explicit external workspace paths and internal shared worktree groups without changing default per-agent worktree behavior. |
| [ask-parameter-policy](plans/ask-parameter-policy/README.md) | Planning | Clarify how ask skills choose silence, compact, callback, and artifact flags from result intent, dependency, and content-preservation needs. |
| [ccb-manuals](plans/ccb-manuals/README.md) | Complete | Produced source-backed CCB developer and user manuals, including Archi/Hippo architecture analysis and a deep communication-logic chapter. |

## Legacy Planning Sources

These roots predate `docs/plantree/` and are intentionally left in place:

- [architecture-optimization](../../plans/architecture-optimization/README.md)
- [ccb-communication-test-plan.md](../../plans/ccb-communication-test-plan.md)
- [droid-delegation-skills-plan.md](../../plans/droid-delegation-skills-plan.md)
- [factory-ai-integration-plan.md](../../plans/factory-ai-integration-plan.md)
- [project-scoped-daemon-isolation-plan.md](../../plans/project-scoped-daemon-isolation-plan.md)

## How To Read

Start with the baseline, then the specific plan root. Use the plan
`implementation-status.md` only when resuming active work.
