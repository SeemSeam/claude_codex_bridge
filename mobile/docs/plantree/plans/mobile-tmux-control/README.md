# Mobile Tmux Control Plan

Date: 2026-06-17
Status: In Progress

Deployment modes: [official and self-hosted Relay](../../../relay/relay-deployment-modes.md).
Guided setup: [interactive mobile route onboarding](topics/mobile-update-interactive-route-onboarding.md).
Phone pairing: [scan-or-code onboarding](topics/mobile-pairing-scan-or-code.md).

## Purpose

Design a mobile and iPad remote-control surface for CCB that connects to
server-side CCB tmux workspaces and preserves CCB's existing multi-project and
multi-agent ownership model.

The product is agent-first and server-remote: the main job is readable control
of CCB projects and named agents running on a server. Raw terminal/tmux control
must remain available, but it is an explicit control/debug fallback rather
than the default project page. The app should not become an independent mobile
agent application.

## Context

CCB already treats tmux as a managed runtime detail:

- one `.ccb` anchor owns one `ccbd` backend;
- one backend owns one project tmux namespace and long-lived session;
- panes, windows, and provider runtime records are reconciled by `ccbd`;
- `pane_id` values are evidence, not durable identity;
- foreground attach is only a UI connection and must not imply lifecycle
  ownership.

Relevant CCB source-of-truth documents live in the source checkout:

- [runtime-flows.md](/home/bfly/yunwei/ccb_source/docs/plantree/baseline/runtime-flows.md)
- [storage-and-state.md](/home/bfly/yunwei/ccb_source/docs/plantree/baseline/storage-and-state.md)
- [ccbd-startup-supervision-contract.md](/home/bfly/yunwei/ccb_source/docs/ccbd-startup-supervision-contract.md)
- [ccbd-pane-recovery-continuous-attach-plan.md](/home/bfly/yunwei/ccb_source/docs/ccbd-pane-recovery-continuous-attach-plan.md)
- [ccb-config-layout-contract.md](/home/bfly/yunwei/ccb_source/docs/ccb-config-layout-contract.md)

Relevant implementation anchors in `/home/bfly/yunwei/ccb_source`:

- `lib/cli/parser.py`
- `lib/cli/parser_runtime/commands.py`
- `lib/cli/phase2_runtime/dispatch.py`
- `lib/cli/services/mobile.py`
- `lib/mobile_gateway/service.py`
- `lib/ccbd/socket_client.py`
- `lib/ccbd/socket_client_runtime/endpoints.py`
- `lib/ccbd/project_view/service.py`
- `lib/ccbd/project_focus/service.py`
- `lib/terminal_runtime/tmux_send.py`
- `lib/terminal_runtime/tmux_panes_runtime/queries_runtime/service.py`

## Planning Files

- [Settings background presets](topics/settings-background-presets.md) tracks
  the authorized offline wallpaper choices and conservative settings cleanup.

- [Conservative UI optimization list](topics/conservative-ui-optimization.md)
  is the current scope for visual refinement; Terminal stays unchanged except
  for a conditional review of shortcut-bar folding.

- [Conservative UI demo review](../../../../design-demo/MULTI_WINDOW_REVIEW.md)
  records the isolated prototype scope: preserve existing navigation and dual
  modes; adjust spacing and state styling only. Pending owner review, not a
  production implementation approval.

- [Agent working visibility](topics/agent-working-visibility.md) defines the
  current agent/window/home status and mobile usability improvement package.

- [goal.md](goal.md) is the reusable long-running goal prompt for landing the
  full mobile project.
- [goal-emulator-only.md](goal-emulator-only.md) is the reusable long-running
  goal prompt for landing the remaining project using only local Android
  Emulator, loopback gateway, isolated CCB runtime, and local test harnesses.
- [goal-chat-first-agents.md](goal-chat-first-agents.md) is the reusable
  long-running goal prompt for landing every configured agent as a
  ChatGPT/DeepSeek-style conversation surface with a persistent composer.
- [goal-server-wide-mobile-install.md](goal-server-wide-mobile-install.md) is
  the reusable long-running goal prompt for landing `ccb install mobile` as a
  server-level mobile gateway with a host project registry and app home page
  listing all mounted/reachable CCB projects.
- [goal-agent-native-conversation.md](goal-agent-native-conversation.md) is the
  reusable long-running goal prompt for correcting ordinary mobile chat so it
  writes to the selected agent pane/native input path and loads
  provider-native transcript history instead of only CCB ask/job records.
- [goal-low-latency-conversation.md](goal-low-latency-conversation.md) is the
  reusable long-running goal prompt for optimizing selected-agent conversation
  latency, live output smoothness, transcript reconciliation, and strict local
  Android Emulator evidence after pane-backed chat lands.
- [goal-paseo-inspired-runtime-hardening.md](goal-paseo-inspired-runtime-hardening.md)
  is the implementation-driving goal for keeping the current Flutter/CCB
  architecture while adopting Paseo-inspired host persistence, unified
  reconnect, cursor catch-up, Push, presence, and optional foreground-service
  behavior with strict emulator and physical-phone evidence.
- [goal-provider-control-plane.md](goal-provider-control-plane.md) directly
  aligns Provider identity, model/thinking controls, session usage, and
  account quota semantics with pinned open-source Paseo contracts while
  retaining CCB runtime authority.
- [topics/lan-network-awareness-and-recovery.md](topics/lan-network-awareness-and-recovery.md)
  is the execute-ready LAN reliability package for phone-side Wi-Fi/VPN
  guidance, reconnect diagnostics, terminal heartbeat, and DHCP/address-change
  recovery instructions.
- [roadmap.md](roadmap.md) tracks likely implementation phases.
- [implementation-status.md](implementation-status.md) is the current
  execution handoff for the active landing phase.
- [history/evidence-index.md](history/evidence-index.md) records accepted
  checkpoint evidence that should stay discoverable without bloating active
  status.
- [topics/source-research.md](topics/source-research.md) summarizes external
  tmux/mobile/agent-remote projects and their fit for CCB.
- [topics/tmux-mobile-fork-adaptation.md](topics/tmux-mobile-fork-adaptation.md)
  records the earlier tmux-mobile fork analysis and the remaining gateway/tmux
  behavior references.
- [topics/tmux-mobile-source-deep-dive.md](topics/tmux-mobile-source-deep-dive.md)
  records the local source review of tmux-mobile docs, security model, backend,
  frontend, and tests.
- [topics/termux-app-native-client-analysis.md](topics/termux-app-native-client-analysis.md)
  records the Android-only Termux native-client analysis and why it is not the
  current cross-platform base.
- [topics/native-flutter-base-source-analysis.md](topics/native-flutter-base-source-analysis.md)
  records the local source review of Paseo, ServerBox, MuxPod, tmux-mobile,
  Termux, Blink, ConnectBot, mosh, and ttyd for the native server-remote path.
- [topics/native-flutter-ccb-blueprint.md](topics/native-flutter-ccb-blueprint.md)
  defines the recommended Flutter/native product architecture and landing
  sequence.
- [topics/architecture-and-reuse-plan.md](topics/architecture-and-reuse-plan.md)
  is the execution architecture and open-source reuse gate before app coding.
- [topics/app-architecture-refactor-plan.md](topics/app-architecture-refactor-plan.md)
  defines the app-side giant-file reduction and feature/controller extraction
  plan after pane-backed chat landed.
- [topics/remote-access-roadmap.md](topics/remote-access-roadmap.md)
  defines the relay-first remote access plan, local emulator validation path,
  and advanced Cloudflare route.
- [topics/tailscale-tailnet-stable-route.md](topics/tailscale-tailnet-stable-route.md)
  defines the stable private Tailnet route using Tailscale Serve while keeping
  the CCB gateway loopback-bound and relay-compatible.
- [topics/server-wide-mobile-install-and-project-registry.md](topics/server-wide-mobile-install-and-project-registry.md)
  defines the next server-scoped `ccb install mobile` direction: one
  server-level mobile gateway, host-level CCB project registry, and app home
  page listing all mounted/reachable CCB projects instead of a current-project
  demo.
- [topics/agent-native-conversation-and-input-correction.md](topics/agent-native-conversation-and-input-correction.md)
  defines the active correction package after manual AVD testing proved the
  default composer still uses the mobile ask/message route and conversation
  backfill still over-indexes on CCB job history.
- [topics/pane-live-output-and-smooth-conversation.md](topics/pane-live-output-and-smooth-conversation.md)
  defines the next conversation-smoothness package: use selected-pane terminal
  output as the low-latency live source, keep provider-native transcript as
  final readable history, avoid blind polling, and validate `/status`, long
  executions, scroll stability, and idle power on real server-wide AVD runs.
- [topics/task-completion-notification-lifecycle.md](topics/task-completion-notification-lifecycle.md)
  defines the follow-up notification lifecycle fix: suppress retained old
  completion events on first subscription, keep the stream alive for future
  completions, maintain app-internal unread markers, and show project-level
  running indicators without parsing conversation text.
- [topics/relay-route-provider-spike.md](topics/relay-route-provider-spike.md)
  defines the first relay route-provider contract and emulator-only fake/local
  acceptance gates before a public relay exists.
- [topics/public-relay-invitation-and-aliyun-deployment.md](topics/public-relay-invitation-and-aliyun-deployment.md)
  defines the hosted relay, one-time applicant invitation, minimal metadata,
  end-to-end encryption, abuse controls, Alibaba Cloud deployment, and inputs
  required from the operator.
- [topics/public-relay-android-emulator-acceptance.md](topics/public-relay-android-emulator-acceptance.md)
  defines the strict public WSS Android Emulator, one-time invitation,
  confidentiality, real-project workflow, recovery, capacity, and no-storage
  acceptance gates.
- [topics/emulator-only-acceptance-checklist.md](topics/emulator-only-acceptance-checklist.md)
  consolidates the current emulator-only completion gates, local proof, AVD
  smoke commands, accepted deferrals, and final audit surface.
- [topics/android-emulator-comprehensive-test-plan.md](topics/android-emulator-comprehensive-test-plan.md)
  defines the active VM-first regression plan after manual testing found that
  consecutive fake/local chat sends can replace the previous visible message.
- [topics/local-real-backend-comprehensive-test-plan.md](topics/local-real-backend-comprehensive-test-plan.md)
  defines the real local CCB backend acceptance matrix: Android Emulator
  through loopback gateway and `adb reverse`, paired gateway mode, full
  send-to-agent-reply closure, attachments, downloads, terminal, diagnostics,
  lifecycle, revoke, reconnect, and response-speed metrics.
- [topics/app-stress-and-performance-test-plan.md](topics/app-stress-and-performance-test-plan.md)
  defines the staged CCB Mobile stress/performance plan: non-disruptive
  stability snapshots, real server-wide project navigation, native
  conversation pressure, file/image stress, long-history rendering,
  multi-project isolation, recovery, and soak gates.
- [topics/app-deep-test-compass-plan.md](topics/app-deep-test-compass-plan.md)
  is the detailed test compass: it maps every real-AVD validation lane to
  concrete actions, metrics, evidence packets, budgets, rejection gates, and
  the next prioritized missing runs.
- [topics/app-comprehensive-test-program.md](topics/app-comprehensive-test-program.md)
  is the top-level execution program for deep real-AVD validation: it groups
  environment, pane chat, history, file/artifact, recovery, performance, power,
  and manual-review work into coherent worker packages with artifact schemas
  and reviewer rejection rules.
- [topics/app-local-avd-full-acceptance-matrix.md](topics/app-local-avd-full-acceptance-matrix.md)
  turns the stress plan into a concrete local Android Emulator acceptance
  matrix with server-wide real-project setup, pane-equivalent chat, manual
  refresh behavior, file/artifact transfer, recovery, performance, power, and
  reviewer rejection gates.
- [topics/app-real-avd-stress-casebook.md](topics/app-real-avd-stress-casebook.md)
  assigns named real-AVD test case ids to server-wide project discovery,
  selected-pane identity, pane-equivalent send/reply, desktop-origin sync,
  file/image upload, backend artifact download, recovery, power, and rendering
  gates.
- [topics/physical-tailnet-device-validation-runbook.md](topics/physical-tailnet-device-validation-runbook.md)
  defines the remaining physical Android phone + Tailnet validation lane:
  read-only preflight, server-wide pairing, pane-equivalent conversation,
  files/artifacts, recovery, and power/performance soak.
- [topics/local-avd-real-project-test-runbook.md](topics/local-avd-real-project-test-runbook.md)
  is the operator-facing execution script for local Android Emulator testing:
  it adds real pane-backed project fixture gates, exact stage actions,
  artifact shape, refresh/power checks, and reviewer rejection rules so fake
  or demo evidence cannot be mistaken for real backend acceptance.
- [topics/cloudflare-tunnel-live-smoke.md](topics/cloudflare-tunnel-live-smoke.md)
  defines the Cloudflare Tunnel live smoke and setup path.
- [topics/cloudflare-alpha-hardening.md](topics/cloudflare-alpha-hardening.md)
  records the named-tunnel setup shape, public-route security posture, and
  remaining Cloudflare alpha gates.
- [topics/tmux-mobile-ccb-implementation-blueprint.md](topics/tmux-mobile-ccb-implementation-blueprint.md)
  preserves a tmux-mobile-derived gateway adaptation blueprint for reference.
- [topics/implementation-scope-and-estimate.md](topics/implementation-scope-and-estimate.md)
  sizes the likely code change surface, critical path, and construction time.
- [topics/landing-execution-plan.md](topics/landing-execution-plan.md)
  breaks the design into concrete implementation packages, files, tests, and
  acceptance gates.
- [topics/product-requirements.md](topics/product-requirements.md) captures the
  core mobile/iPad requirements around projects, agents, lifecycle,
  notifications, and Markdown/math display.
- [topics/chat-background-customization.md](topics/chat-background-customization.md)
  defines local-only chat wallpaper selection, persistence, readability, and
  chat-versus-terminal acceptance boundaries.
- [topics/ccb-mobile-control-architecture.md](topics/ccb-mobile-control-architecture.md)
  defines the recommended CCB-specific architecture.
- [topics/mobile-api-contract.md](topics/mobile-api-contract.md) sketches the
  gateway API and the `ccbd` endpoints likely needed for an MVP.
- [topics/gateway-contract-checkpoint.md](topics/gateway-contract-checkpoint.md)
  freezes the route-agnostic gateway contract checkpoint before gateway
  implementation.
- [topics/ccb-mobile-serve-ready-check.md](topics/ccb-mobile-serve-ready-check.md)
  records the CCB source ready-check and first `ccb mobile serve` package
  boundary.
- [topics/mobile-ux-flows.md](topics/mobile-ux-flows.md) describes the mobile
  information architecture and CCB-specific user flows.
- [topics/chat-first-agent-workspace.md](topics/chat-first-agent-workspace.md)
  replans the selected-agent workspace as a ChatGPT/DeepSeek-style timeline
  plus persistent composer, with raw terminal as an explicit fallback.
- [topics/markdown-rendering.md](topics/markdown-rendering.md) defines
  Markdown-first display for ask, Comms, replies, artifacts, and mobile reading.
- [topics/terminal-transport-spike.md](topics/terminal-transport-spike.md)
  defines the terminal transport risks and validation spikes.
- [topics/terminal-viewport-and-input-design.md](topics/terminal-viewport-and-input-design.md)
  preserves the terminal-mode viewport, zoom, mouse, wheel, and shortcut
  design while keeping the chat-first workspace clean.
- [topics/agent-terminal-mode-remote-pane-control.md](topics/agent-terminal-mode-remote-pane-control.md)
  defines the ready-to-execute package for per-agent Terminal mode: promote the
  existing gateway `xterm` terminal surface into the selected-agent workspace,
  complete direct pane input controls, and require real Android Emulator
  evidence before acceptance.
- [open-questions.md](open-questions.md) captures decisions still needed before
  implementation.
- [decisions/001-ccb-authority-before-generic-tmux.md](decisions/001-ccb-authority-before-generic-tmux.md)
  records the key product/architecture stance.
- [decisions/002-gateway-pwa-before-native-client.md](decisions/002-gateway-pwa-before-native-client.md)
  records the now-superseded web/PWA-first MVP proposal.
- [decisions/003-markdown-first-agent-content.md](decisions/003-markdown-first-agent-content.md)
  records the proposed content-display stance.
- [decisions/004-tmux-first-server-remote.md](decisions/004-tmux-first-server-remote.md)
  records the clarified product mission.
- [decisions/005-native-flutter-tmux-first-client.md](decisions/005-native-flutter-tmux-first-client.md)
  records the updated native Flutter client direction.
- [decisions/006-cloudflare-tunnel-before-custom-relay.md](decisions/006-cloudflare-tunnel-before-custom-relay.md)
  records the remote-access route decision.
- [decisions/007-native-baseline-before-ccb-gateway.md](decisions/007-native-baseline-before-ccb-gateway.md)
  records the first landing sequence: native app baseline and socket-aware
  tmux vertical slice before CCB gateway work.
- [decisions/008-permissive-baseline-until-agpl-approval.md](decisions/008-permissive-baseline-until-agpl-approval.md)
  records the Batch 1 license/base decision before app source scaffold.
- [decisions/009-ssh-direct-pty-first-terminal-slice.md](decisions/009-ssh-direct-pty-first-terminal-slice.md)
  records the selected first real terminal transport after isolated harness
  evidence.
- [decisions/010-cli-managed-mobile-gateway-sidecar.md](decisions/010-cli-managed-mobile-gateway-sidecar.md)
  records the first `ccb mobile serve` runtime ownership decision.
- [decisions/011-relay-default-remote-route.md](decisions/011-relay-default-remote-route.md)
  records that CCB Relay, not Cloudflare named tunnels, should be the default
  not-on-LAN route for ordinary CCB Mobile users.
- [decisions/012-agent-first-project-workspace.md](decisions/012-agent-first-project-workspace.md)
  records that the default project page is a top agent switcher plus one
  selected-agent workspace, with raw terminal as an explicit fallback.
- [decisions/013-readable-terminal-history.md](decisions/013-readable-terminal-history.md)
  records that selected-agent workspaces should include vertically scrollable,
  best-effort readable terminal history from current tmux scrollback, while
  structured CCB content remains authoritative.
- [decisions/014-chat-first-agent-workspace.md](decisions/014-chat-first-agent-workspace.md)
  records that the default selected-agent workspace is a chat-style
  conversation timeline with a persistent composer, not a dashboard or raw
  terminal surface.
- [decisions/015-pane-backed-chat-input.md](decisions/015-pane-backed-chat-input.md)
  records that the chat-style composer should write to the selected agent's
  tmux pane and render pane output/history, rather than wrapping the CCB
  ask/message submission route.
- [decisions/016-pane-composer-send-primitive.md](decisions/016-pane-composer-send-primitive.md)
  records that the current mobile alpha keeps app-side terminal paste plus
  Enter as the compact composer primitive, with partial sends surfaced as
  `Check pane` instead of hidden retry.
- [decisions/017-optional-mobile-bundle.md](decisions/017-optional-mobile-bundle.md)
  records that host-side CCB Mobile tooling should live in CCB source as an
  explicit optional bundle installed with `ccb update mobile`, not as part of
  mandatory `ccb update`.
- [decisions/018-stable-android-release-channel.md](decisions/018-stable-android-release-channel.md)
  records that Android release APKs must use stable release signing material,
  while the first in-app update entry opens the configured APK/release URL and
  explains same-signature cover-install versus one-time different-signature
  migration.
- [decisions/019-app-lifetime-task-completion-notifications.md](decisions/019-app-lifetime-task-completion-notifications.md)
  records that P0 task-completion phone notifications are app-lifetime local
  notifications fed by a server-wide low-sensitive gateway event stream, with
  push/foreground-service reliability deferred.
- [decisions/022-device-bound-push-delivery.md](decisions/022-device-bound-push-delivery.md)
  records the gateway's device-bound push-token, payload, authorization,
  migration, and external-provider boundary.
- [decisions/023-one-time-public-relay-admission.md](decisions/023-one-time-public-relay-admission.md)
  records that each hosted-relay applicant receives a one-use invitation that
  activates one host key, while reusable CCB phone pairing remains separate.
- [decisions/024-official-and-self-hosted-relay-modes.md](decisions/024-official-and-self-hosted-relay-modes.md)
  records the official and self-hosted Relay deployment modes.
- [decisions/025-paseo-provider-control-alignment.md](decisions/025-paseo-provider-control-alignment.md)
  supersedes Decision 008 for the Provider-control scope and records direct,
  attributed AGPLv3 alignment with pinned Paseo source.

## Current Direction

Build a native Flutter phone/iPad client around a CCB-scoped pane-backed
chat-first agent workspace with explicit raw-terminal fallback:

1. Connect the phone/iPad to CCB projects and named agents on the server.
2. Default to a project page with a top agent switcher, exactly one selected
   agent, a vertically scrollable conversation timeline, and a persistent
   bottom composer.
3. Submit normal user input by writing to the selected agent's CCB-validated
   tmux pane through the existing terminal transport; do not add an `ask`
   wrapper above the pane.
4. Render provider-native transcript, pane output, and retained scrollback into
   compact chat-style entries. CCB ask/job history is supplemental
   compatibility data, not the default conversation source. Full raw
   terminal/tmux control remains available through explicit Open Terminal
   actions and per-agent Terminal mode with keyboard, paste, resize, and
   reconnect support.
5. Move project path, gateway URL, pairing code, runtime id, route diagnostics,
   and low-level terminal state behind connection details or settings.
6. Discover CCB projects and agents through `ccbd`, not raw filesystem scans or
   arbitrary tmux sessions.
7. Use `project_view` as the side data model for project, window, agent,
   activity, Comms, and health state.
8. Use existing focus endpoints when the user explicitly changes CCB-managed
   focus.
9. Use a server-level mobile gateway and project registry so the phone sees
   all mounted/reachable CCB projects on that server, then keep a frequent
   project list that can open, wake, and close those projects.
10. Show named agents with fast switching, state, completion, callback, and
   health indicators.
11. Render pane-derived output, Comms, replies, artifacts, Markdown, and math
   formulas in a readable mobile/iPad timeline.
12. Keep destructive tmux operations gated or disabled unless `ccbd` owns the
   action.
13. Use LAN/manual URL for local validation, CCB Relay as the default
   not-on-LAN route, Tailnet as a stable private route, and Cloudflare Tunnel
   as an advanced route while keeping the app and gateway protocol
   relay-compatible.
14. Use a server-wide low-sensitive notification event stream for app-lifetime
    task-completion system notifications, with every paired client eligible to
    notify and push-level killed-app delivery deferred.

## Initial Recommendation

For the native server-remote path:

- use ServerBox as the preferred native fork candidate if an AGPL mobile app
  component is acceptable;
- use MuxPod as the strongest tmux-specific mobile UX and command-strategy
  reference;
- use tmux-mobile as the best MIT reference for a server-side
  WebSocket/xterm/tmux gateway;
- use Paseo as the strongest reference for QR pairing, daemon/client protocol,
  relay, terminal frames, and agent-mobile workflow;
- keep Termux, Blink, ConnectBot, mosh, and ttyd as narrower reference points,
  not product bases.
- use CCB Relay as the default public remote access path for ordinary users,
  keep Tailnet as the stable private route, keep Cloudflare Tunnel as an
  advanced/self-hosted option, and preserve local LAN/manual URL validation for
  emulator and same-network testing.

The CCB-specific product should remain server-remote: CCB and provider CLIs run
on the server, while the native client is a CCB-aware controller for projects,
selected-agent workspaces, content, notifications, and explicit terminal/pane
control when needed.
