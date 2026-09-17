# Mobile Tmux Control Roadmap

Date: 2026-06-18

## Settings Background Presets — Locally Verified

2026-09-17: implement three offline backgrounds on the existing custom-image
store and compact the settings introduction. Scope and verification:
[settings backgrounds](topics/settings-background-presets.md).
Analyze and full suite passed (838 tests, 1 skip); final thumbnail adjustment
passed 10 focused tests, APK rebuilt/installed and cold-start background
restoration verified on emulator-5554. Not committed or released.

## Conservative UI Refinement — Locally Verified

2026-09-17: owner accepted the conservative demo direction. The
[optimization list](topics/conservative-ui-optimization.md) defines ordered
visual refinements and native acceptance checks. Preserve existing window /
Agent navigation, automatic collapse and dual modes. Terminal layout stays;
shortcut folding was inspected and retained. Chat/header/composer and home
spacing refinements landed locally; analyze, 121 targeted and 836 full tests
(1 skip), APK build/install and emulator review passed. Evidence is in the
linked topic. Next: owner visual acceptance; not committed or released.

## Agent Working Visibility — Visual Revision Locally Verified

2026-09-16: demo implementation independently reviewed by agent1; 835 tests
passed, 1 skipped, analyze/build passed, actual expanded and collapsed Android
emulator pages verified. Not committed or released. Scope and evidence:
[agent working visibility](topics/agent-working-visibility.md).
User feedback: working and selection remain too similar. Current execution:
[Agent state colors](topics/agent-state-colors.md), whole-label green/gray/red
states, independent orange unread badges and explicit localized state text.
Revision implemented; agent1 independently verified tests, build and actual
expanded/folded emulator pages. Next: user review; no commit or release yet.

## Phase 4I: Paseo-Aligned Provider Control Plane — Complete

Status: Complete (2026-08-12).

- show Provider, active model, and thinking as selected-agent identity;
- adapt Paseo's Provider snapshot, model definition, usage, quota, and
  confirmed-mutation semantics at pinned commit `b599d38`;
- preserve CCB's Python gateway, Flutter app, ccbd/tmux/session authority, and
  fail-closed device scopes;
- persist validated Codex/Claude model and effort choices as explicit
  restart-required configuration changes, never as guessed live commands;
- expose bounded native-session usage and separately loaded account quota;
- keep direct HTTP and encrypted Relay contracts equivalent;
- require full tests/builds plus real server-wide Android Emulator evidence on
  dedicated Codex and Claude projects before completion.

Accepted evidence:
[history/provider-control-plane-acceptance-20260812.md](history/provider-control-plane-acceptance-20260812.md).

Execution authority:
[goal-provider-control-plane.md](goal-provider-control-plane.md) and
[Decision 025](decisions/025-paseo-provider-control-alignment.md).

## Phase 4H: Paseo-Inspired Runtime And Background Hardening — In Progress

Status: In Progress (2026-07-13).

- keep the existing Flutter app and CCB mobile gateway;
- introduce one app-lifetime connection supervisor with cached foreground
  recovery and fail-closed mutation behavior;
- persist and resume a bounded gateway event journal using explicit cursors;
- add presence-aware FCM completion delivery for background/process-killed
  operation rather than relying on an ordinary background WebSocket;
- provide optional user-controlled Android foreground connectivity for active
  Terminal/large-transfer use;
- make LAN failures actionable with coarse Android network awareness,
  phone-side Wi-Fi/VPN/guest-network/DHCP guidance, and a client terminal
  WebSocket heartbeat;
- preserve reusable unlimited pairing claims until explicit
  `ccb update mobile` rotation, while existing device credentials remain valid;
- require same-commit emulator and physical-phone background/Doze/reconnect/
  multi-device evidence before release.

Execution authority:
[goal-paseo-inspired-runtime-hardening.md](goal-paseo-inspired-runtime-hardening.md)
and [Decision 021](decisions/021-reusable-pairing-until-manual-rotation.md).
LAN hardening detail:
[topics/lan-network-awareness-and-recovery.md](topics/lan-network-awareness-and-recovery.md).

## Phase 4G: Realtime Authority, Recovery, and Bounded Cache — In Progress (review correction)

Status: In Progress (review correction, 2026-07-10).

- provider/native Chat authority replaces automatic terminal-history fallback;
- one gateway invalidation SSE journal signals explicitly observed
  project/activity/conversation changes without per-client registry scans;
- app refreshes from invalidations, uses bounded reconnect and offline-safe
  sends, and retains bounded read-only snapshots by host/project/agent/epoch;
- active-send scheduler and active-project 2-second polling are removed;
- real emulator evidence covers cold startup, automatic recovery, notification
  delivery, and a 259-second zero-view-request idle window, but final acceptance
  remains blocked until `resync_required`, cursor confirmation, stale snapshot
  UI, and HTTP audit evidence corrections are reviewed.

Product boundary for this package: completion notifications are generated from
explicit ProjectView/conversation observations and selected-target native
invalidation watches. They are not yet an unconditional all-project background
completion detector. Kimi/OpenCode/unknown-provider structured fallback is
visible on explicit conversation loads; it does not currently produce selected
SSE fingerprints unless a provider-native file fingerprint exists.

Evidence: [realtime recovery packet](/tmp/ccb-mobile-realtime-recovery-20260710/README.md).

## Phase 0: Research Spike

Status: Complete for native direction planning.

- Reviewed CCB project view, project focus, tmux namespace, socket-client RPC,
  and pane/runtime authority boundaries.
- Reviewed and cloned relevant external projects under
  `/tmp/ccb-mobile-research`: Paseo, ServerBox, MuxPod, tmux-mobile, Termux
  App, Blink, ConnectBot, mosh, and ttyd.
- Confirmed the product direction: native Android/iOS/iPadOS app,
  agent-first CCB workspace and content plane, with raw terminal/tmux control
  available as an explicit fallback.
- Recorded the native source review in
  [topics/native-flutter-base-source-analysis.md](topics/native-flutter-base-source-analysis.md).
- Recorded the native implementation blueprint in
  [topics/native-flutter-ccb-blueprint.md](topics/native-flutter-ccb-blueprint.md).

## Phase 0.5: Native Base Preparation

Status: In Progress.

Goal: choose the app repository shape and validate the native terminal base
before CCB runtime changes.

Recommended direction:

- use ServerBox as the preferred Flutter fork candidate if AGPL is acceptable;
- use MuxPod as the tmux UX and command strategy reference;
- use tmux-mobile and Paseo only as server/gateway/protocol references.

Work:

- decide whether the mobile app may be AGPL;
- keep one authoritative mobile implementation subtree under
  `ccb_source/mobile`;
- preserve upstream license notices and attribution;
- strip or hide generic server-management surfaces in the CCB profile;
- define the Flutter data model: host, project, window, agent, terminal target,
  content item, and notification;
- keep a fake transport so UI work does not require a live CCB server.

Acceptance criteria:

- Android debug build succeeds; iOS platform files are generated and remain
  pending macOS/Xcode validation;
- a fake project/agent list renders on phone and iPad;
- the app does not expose generic tmux session management in CCB mode;
- upstream license and attribution obligations are documented.

Current landing batch:

- treat `ccb_source/mobile/` as the only authoritative mobile workspace and
  keep the Flutter app under `app/`;
- finalize the architecture/reuse gate in
  [topics/architecture-and-reuse-plan.md](topics/architecture-and-reuse-plan.md)
  before app scaffold or upstream code import;
- use the permissive Batch 1 baseline from
  [decisions/008-permissive-baseline-until-agpl-approval.md](decisions/008-permissive-baseline-until-agpl-approval.md)
  until AGPL source reuse is explicitly accepted;
- start with a fake CCB transport and fixtures shaped like CCB `project_view`;
- implement the Flutter model/transport boundary before live networking;
- add socket-aware tmux command tests before any real terminal demo;
- wire fake project agents to a read-only `xterm` terminal screen that renders
  the exact socket-aware tmux attach command generated from `ProjectView`;
- prepare an isolated terminal validation harness that reads `project_view`
  over the ccbd Unix socket and prints mobile terminal target evidence;
- validate that harness against a started disposable CCB project and use the
  observed socket/session/agent evidence to select SSH direct PTY for the first
  real terminal slice;
- add `TerminalTransport` and `SshTerminalTransport` so live SSH direct PTY can
  be injected behind the terminal boundary without changing the CCB-first UI;
- add a developer SSH profile entry point that creates an injected live
  transport without storing credentials or changing the default fake terminal
  path;
- run a repeatable SSH direct PTY live smoke against a started isolated CCB
  project through temporary localhost sshd;
- add Android API 35 emulator `flutter run` smoke and a project-local
  toolchain environment helper;
- draft the route-agnostic gateway contract checkpoint before CCB source work;
- implement app-side `GatewayTransport` and `RouteProvider` interfaces with
  route-agnostic fake gateway contract tests;
- record the CCB source ready-check for `ccb mobile serve` and select a
  CLI-managed, loopback-first, current-project gateway sidecar as the first
  source package;
- land G1 in `/home/bfly/yunwei/ccb_source`: `ccb mobile serve` phase2 CLI
  routing, loopback-only current-project health/projects/view endpoints, and
  focused source tests;
- land app-side G1 HTTP wiring: `HttpGatewayTransport` and
  `GatewayMobileCcbRepository` consume the loopback health/projects/view JSON
  shape and fail closed for unsupported G1 routes;
- land G2 pairing/device-token foundation: CCB source emits a startup pairing
  code, stores pairing/device token hashes and audit under `.ccb/ccbd/mobile`,
  exposes claim/device-me/self-revoke routes, and the Flutter app imports the
  claim response into secure profile storage;
- land authenticated focus routes: CCB source exposes focus-agent/window
  gateway routes behind device token plus `focus` scope, reuses ccbd
  `project_focus_agent/window`, and the Flutter app posts focus requests
  through `HttpGatewayTransport`/`GatewayMobileCcbRepository`;
- land app pairing/profile UI and explicit runtime modes: manual gateway
  pairing creates secure profiles, paired gateway mode uses
  `GatewayMobileCcbRepository` for ProjectView/focus, and fake remains the
  default;
- land terminal-open/token foundation: CCB source mints short-lived
  terminal tokens behind `terminal_input` scope and validated ProjectView
  target identity, and the Flutter app parses gateway terminal-open handles
  while keeping WebSocket frame streaming fail-closed;
- land terminal WebSocket/PTy streaming foundation: CCB source validates
  terminal tokens, revalidates ProjectView namespace/target identity, owns a
  tmux attach client, streams output/input/paste/resize/close frames, and the
  Flutter app connects `terminalFrames`/`sendTerminalFrame` to a route-agnostic
  WebSocket;
- land paired-gateway terminal UI wiring: paired gateway mode now requests
  `terminal_input`, injects `GatewayTerminalTransport`, focuses the tapped
  agent, opens the existing terminal screen with a gateway terminal request,
  and routes output/input/paste/resize/close through the frame transport;
- land isolated gateway terminal smoke: source `project_focus_agent` now
  selects panes by CCB pane options when logical CCB window names differ from
  actual tmux window names, and the mobile smoke starts a disposable CCB
  project, pairs through loopback `ccb mobile serve`, opens a gateway terminal,
  proves output/input/paste/resize/close through the real WebSocket/PTy attach
  client, and verifies close/reconnect fail-closed behavior;
- land window-level terminal/focus UI: the app renders configured CCB windows
  separately from agents, maps window taps to `window_active_pane` targets,
  calls authenticated `focusWindow` in paired gateway mode, and opens the
  existing terminal screen/transport boundary without changing terminal frame
  schemas;
- land QR camera pairing: the app reuses `mobile_scanner` for camera QR
  detection, adds Android/iOS camera permission strings, parses the source
  gateway pairing JSON payload into `GatewayPairingPayload`, and auto-claims
  through the existing secure profile import path;
- remove developer SSH diagnostic mode after gateway-only validation:
  QR/paired-gateway onboarding is now the user-facing path, app commit
  `4b43a4f` removes the SSH runtime segment, profile injection, `dartssh2`
  dependency, `SshTerminalTransport`, and SSH direct smoke tool/tests, while
  keeping fake and paired-gateway runtime modes;
- land gateway terminal reconnect/resume cursor: source records terminal
  output sequence state and transport disconnects without closing the handle,
  requires matching `resume_cursor` for reconnect, rejects stale cursors, and
  the Flutter app reconnects gateway terminal streams from the latest output
  cursor;
- land app-side gateway route diagnostics: `GatewayTransport.device()` reads
  the existing `/v1/devices/me` contract, `GatewayRouteDiagnostics` verifies
  Cloudflare HTTPS/WSS and origin-only route shape, gateway
  health/capabilities, paired-device auth, route-provider scope, device
  gateway URL consistency, project reachability, and ProjectView redaction, and
  the runtime panel exposes an injectable route check without branching UI code
  on Cloudflare;
- land Cloudflare Tunnel smoke preparation: source `ccb mobile serve` accepts
  `--public-url` and `--route-provider` while staying loopback-bound, and the
  mobile smoke harness can run either a named Cloudflare Tunnel URL or a
  development `cloudflared tunnel --url` quick tunnel before executing the same
  route diagnostics and terminal WebSocket smoke path;
- accept the live Cloudflare quick-tunnel smoke: app commit `a8eeec0` adds
  smoke-only public DNS override for generated quick-tunnel hostnames, proves
  public `/v1/health`, route diagnostics, terminal
  output/input/paste/resize/close/reconnect, and cleanup through
  `cloudflared tunnel --url`, while keeping `ccb mobile serve`
  loopback-bound;
- harden public-route device revocation: source commit `8a264cae` adds
  host-local `ccb mobile devices` and `ccb mobile revoke <device_id>`, keeps
  admin revocation off the public HTTP surface, cascades device revoke to
  still-open terminal handles, and checks device revocation during terminal
  token auth;
- land named-tunnel setup handoff: app commits `4f41391`, `6f26591`,
  `1c2d4de`, `f4bb5e5`, `eadcece`, `de79cde`, `2ff36a9`, `11bae28`, and
  `e1e14a2`, plus `3f0a0b5`, `434ed01`, `53a50dd`, `9396842`, and `1d4e28c`
  add preflight, hostname matching, automated named-tunnel smoke startup,
  failed-preflight runtime guard, `next_actions`, `config_template`, template
  round-trip coverage, tunnel-name override handoff, fixed loopback listen
  validation, copyable smoke-command output, manual/existing tunnel smoke
  commands, origin-only public URL validation in source metadata generation,
  harness preflight, and app route diagnostics, and device gateway URL
  consistency diagnostics; source commits `44ba9edd`, `973a2707`, `444b648c`,
  `9ce07104`, `a2ac6f1e`, and `69891f03` plus `867300d7`, `8e047913`,
  `93c0de50`, `9cd71bd8`, and `a071e257` document/enforce the flow;
- guard route-provider schema boundaries: app commit `c87c924` proves
  route-provider metadata is ignored below the route boundary when extra
  fields appear in WebSocket frames or ProjectView payloads, and remains out
  of terminal ids, terminal handle summaries, terminal frame schemas, and
  ProjectView-derived terminal-open requests;
- land the first agent-first mobile project UI slice: app commit `1c0023b`
  adds the top agent switcher, one selected-agent main body, placeholder
  Comms/Markdown areas, project/gateway/runtime details behind a connection
  route, and explicit Open Terminal actions for raw tmux control;
- generated Android/iOS folders exist under `app/` after
  `flutter create --no-overwrite --platforms android,ios .`;
- local Flutter/Dart/JDK/Android SDK tooling is installed outside the repo and
  Android debug APK builds successfully;
- validate the first terminal path only against an isolated CCB test project,
  not `/home/bfly/yunwei/ccb_source` or this project's active runtime.
- land per-agent Terminal mode from
  [topics/agent-terminal-mode-remote-pane-control.md](topics/agent-terminal-mode-remote-pane-control.md):
  reuse the existing gateway `TerminalView`/WebSocket transport inside the
  selected-agent workspace, complete direct pane input controls, and require
  real Android Emulator screenshots/recording before acceptance.

## Phase 1: Gateway Contract And Native Tmux Terminal Vertical Slice

Goal: prove the phone/iPad can control one server-side CCB project safely,
with raw terminal mode available explicitly rather than as the default project
page.

Work:

- define `GatewayTransport` and route-provider metadata before hard-coding any
  Cloudflare-specific behavior;
- add socket-aware tmux command building:
  `tmux -S <project_socket> attach-session -t <session>`;
- open a terminal through either ServerBox-style SSH PTY or a gateway terminal
  WebSocket, with the first real slice using SSH direct PTY per
  [Decision 009](decisions/009-ssh-direct-pty-first-terminal-slice.md);
- use the CCB source ready-check in
  [topics/ccb-mobile-serve-ready-check.md](topics/ccb-mobile-serve-ready-check.md)
  and source commit `bcee866e` as the G1 gateway JSON contract before product
  pairing and gateway token work;
- use the landed Flutter `GatewayTransport` HTTP client and repository adapter
  for the G1 health/projects/view endpoints;
- wire paired-gateway terminal UI to the authenticated gateway terminal
  WebSocket now that source/app frame transport exists;
- add CCB terminal target metadata: project id, namespace epoch, tmux socket
  path, tmux session name, selected window/agent, and current pane evidence;
- support special keys, paste composer, copy, resize, background/resume, and
  reconnect;
- keep capture-polling as a fallback/diagnostic mode;
- test against an isolated CCB test project, not this source checkout runtime.

Acceptance criteria:

- a phone-sized viewport opens the exact CCB project tmux socket/session;
- default `tmux attach` is never used for a CCB project;
- closing the app or terminal view does not stop `ccbd`, provider panes, or the
  project tmux session;
- terminal input and multiline paste work;
- network/app background reconnect returns to the same project target or fails
  closed with a refresh;
- route-provider fields are absent from project ids, terminal ids, terminal
  frame schemas, and ProjectView payloads.

## Phase 2: CCB Project And Agent Control

Goal: make the native terminal feel like a CCB controller rather than a generic
SSH/tmux client.

Work:

- add QR pairing or temporary manual host/project configuration;
- list CCB projects and favorites through the server-wide registry defined in
  [topics/server-wide-mobile-install-and-project-registry.md](topics/server-wide-mobile-install-and-project-registry.md);
- read ProjectView through a gateway or SSH JSON wrapper;
- show named agents, windows, health, callback/completion, queue, and Comms
  attention;
- focus agent/window through `project_focus_agent` and `project_focus_window`;
- reject stale namespace epoch and stale pane evidence;
- hide or gate raw split/kill/new/rename tmux operations.

Acceptance criteria:

- a paired phone sees at least two CCB projects without listing unrelated tmux
  sessions;
- frequent projects can be pinned/reordered locally or gateway-side;
- tapping an agent/window uses CCB focus authority, not pane id alone;
- the side panel updates without reconnecting the terminal;
- degraded/offline `ccbd` locks unsafe actions and explains the state.

## Phase 6: Server-Wide Mobile Install And Multi-Project Registry

Status: Next planning-to-implementation target after the current local
real-backend app/gateway stabilization.

Goal: make CCB Mobile a server-level capability installed by
`ccb install mobile`, not a per-project `ccb mobile serve` demo. One paired
phone should see and open every mounted/reachable CCB project on the server.

Design authority:

- [topics/server-wide-mobile-install-and-project-registry.md](topics/server-wide-mobile-install-and-project-registry.md)

Work:

- add a CCB source host-level project registry that discovers mounted CCB
  projects from runtime state and pings each project's `ccbd`;
- route `/v1/projects/{project_id}/...` through the registry instead of the
  serve-time current project only;
- add `ccb install mobile` as an idempotent server-scoped install/activate
  command that can run outside a CCB project directory;
- make pairing identify the server gateway host separately from the default or
  last-opened project id;
- change the app home flow so paired gateway mode loads `listProjects()` first
  and renders all server projects before loading a selected `ProjectView`;
- add a real Android Emulator multi-project lane with two local CCB projects,
  per-project message/reply checks, attachment upload/download, backend
  artifact download, and latency metrics.

Acceptance criteria:

- `ccb install mobile` does not require a current CCB project root;
- `/v1/projects` returns at least two mounted local projects in the test
  harness without exposing socket/runtime paths;
- the app first page shows both projects while paired to a single server
  profile;
- opening project A and project B uses the exact selected project id for
  ProjectView, focus, message, file, terminal, and lifecycle routes;
- messages/files/artifacts do not cross projects;
- stale or unknown project ids fail closed with recoverable UI.

## Phase 3: Reading, Notifications, Lifecycle, And Chat Foundations

Goal: make mobile useful away from the desktop terminal and prepare the
selected-agent workspace to behave like a CCB chat client instead of a
dashboard.

Work:

- add Markdown/math content view for ask, Comms, replies, and text artifacts;
- add a selected-agent conversation model that can combine user messages,
  agent replies, callbacks, Comms, status events, artifacts, and readable
  terminal-history evidence;
- add a pane-backed composer model for multiline user input, pending/sent/
  failed-or-echoed state, safe retry, and per-agent draft preservation;
- add a CCB content endpoint or gateway route that resolves content ids safely;
- add safe content actions for validated remote files and URLs: long-press
  Download/Open actions for files/artifacts, external-app handoff through the
  OS chooser, and one-time confirmation before opening remote content outside
  the app;
- add a readable terminal history surface for the selected agent that captures
  current tmux pane scrollback, cleans ANSI/control noise, groups useful
  command/log/code/diff/Markdown-like blocks, and allows vertical history
  scrolling on phone;
- replace swipe-up agent-list reveal with an explicit pull-out control, then
  verify expanded conversation bubbles can scroll to bottom without jumping
  back to the top;
- derive initial completion/attention notifications from ProjectView/Comms
  deltas;
- extend completion notifications toward cross-project phone reminders for any
  pane-backed task completion, once the authoritative CCB/tmux event source is
  confirmed;
- add the P0 app-lifetime OS task-complete notification path through a
  server-wide gateway notification subscription: Android notification
  permission/channel setup after pairing/subscription, terse
  project-short-name plus agent completion text, platform default sound
  behavior, deep link, and persistent dedupe across reconnect, refresh,
  resume, and project switching;
- split the P0 notification landing into a source package for the server-wide
  low-sensitive completion event stream and an app package for subscription,
  Android notification channel/permission handling, local dedupe, and tap
  routing, per
  [Decision 019](decisions/019-app-lifetime-task-completion-notifications.md);
- add wake/open/close/stop through CCB lifecycle authority;
- add device scopes for `view`, `content`, `focus`, `terminal_input`,
  `notify`, optional `ask`/`message_submit`, `lifecycle`, and `admin`;
- add acknowledgement and deep links for notifications.

Acceptance criteria:

- the selected-agent workspace can render a chat-style timeline in fake mode
  without opening raw terminal;
- the composer can accept multiline input and produce visible pending/sent/
  failed states;
- Markdown content handles headings, lists, tables, code blocks, links, copy,
  and formulas on phone and iPad;
- validated remote files separate Download from Open, and Open hands the local
  downloaded copy to another app only after user confirmation;
- web URLs can open through the system browser/app chooser after confirmation,
  while local server paths remain blocked unless resolved by the gateway;
- readable terminal history lets the user scroll through the current retained
  pane history, while clearly labeling it as best-effort tmux scrollback rather
  than authoritative CCB content;
- notifications do not depend on terminal text scraping;
- notification deep links open the target project and agent/window/content;
- cross-project completion reminders do not require the user to already be on
  the completed task's project page;
- OS task-complete notifications contain only project short name, agent name,
  and completion text, use the platform default notification sound/channel
  behavior, and do not include prompt/reply/terminal details;
- in real Android Emulator validation, a backgrounded-but-not-killed app can
  receive a server-wide gateway completion event from a dedicated test project,
  post one OS notification, and tap back to the target project/agent;
- Android notification permission denial does not crash the app and degrades to
  in-app completion state without an OS notification;
- source-side tests prove completion event generation, `notify` scope denial,
  multi-client fanout, `dedupe_key` stability, and absence of prompt/output/path
  leakage before the app treats notifications as real cross-project signals;
- close never stops server-side CCB;
- stop never calls raw `tmux kill-server`;
- lifecycle/admin actions require explicit scope and confirmation.

## Phase 4: Chat-First Agent Workspace Landing

Status: Phase 4F. Pane-backed send and provider-native readable history are
implemented at smoke level; the active work is conversation smoothness:
low-latency active-send follow-up, terminal-stream/live-turn reconciliation,
and real AVD timing evidence. The old local C1-C5 acceptance gate remains
historical evidence only; default chat acceptance now requires the
agent-native correction in
[topics/agent-native-conversation-and-input-correction.md](topics/agent-native-conversation-and-input-correction.md)
plus the smoothness gates in
[topics/pane-live-output-and-smooth-conversation.md](topics/pane-live-output-and-smooth-conversation.md).

Goal: make the default paired-gateway mobile surface a ChatGPT/DeepSeek-style
conversation workbench for one selected CCB agent.

Work:

- C1 fake chat shell, C2 app repository boundary, C3 source message routes,
  C4 paired-gateway app wiring, and C5 local Android Emulator type-send-read
  smoke are landed for the earlier ask/message path.
- refactor the first project viewport into top agent switcher, selected-agent
  timeline, and persistent bottom composer;
- keep connection details, lifecycle, diagnostics, route state, and raw
  terminal details behind secondary routes or menus;
- add `MobileCcbRepository` conversation/message DTOs and fake transport
  coverage;
- rebase the default composer on selected-agent terminal session input/paste
  and use terminal output/history as the primary timeline source;
- load provider-native transcript history as the primary readable conversation
  source, with CCB ask/job records only as supplemental compatibility data;
- add the smooth live-output layer from
  [topics/pane-live-output-and-smooth-conversation.md](topics/pane-live-output-and-smooth-conversation.md):
  selected-pane terminal output streams into one stable live assistant turn,
  provider-native transcript later reconciles final readable history, and
  `/status` plus long-running executions remain visible without blind polling
  or many small reply cards;
- land the next low-latency follow-loop slice: first active-send refresh within
  `300 ms`, timing evidence for send accepted / pane send complete / first
  terminal byte / first conversation change / first rendered update, and zero
  idle conversation/history requests;
- verify ordinary mobile sends do not create ask jobs and never inject
  `CCB_REQ_ID`;
- preserve readable terminal history as a labeled timeline evidence block, not
  an authoritative reply source;
- add local Android Emulator loopback smoke that types into the composer,
  sends, observes queued/sent/reply or callback state, and verifies Open
  Terminal remains explicit.

Acceptance criteria:

- paired AVD opens to a selected-agent chat timeline with a usable bottom
  pane-backed composer;
- sending a message requires a selected-agent terminal session and reaches the
  tmux pane without calling `/agents/{agent}/messages`;
- selected-agent history loads provider-native user/assistant transcript
  records, not only `.ccb/agents/<agent>/jobs.jsonl`;
- real local AVD evidence records the selected project root and proves the
  phone is not validating against fake `demo` or the wrong current project;
- user messages, agent replies/callbacks, Comms, and artifacts render as
  readable timeline entries;
- one long provider execution updates one live/final reply turn instead of
  producing many disconnected reply cards;
- `/status` and other provider UI command output is visible from the selected
  pane stream even when it is not present in provider-native transcript files;
- idle selected-agent pages do not run a blind fixed-interval terminal-history
  refresh loop;
- agent switching preserves selected-agent scroll and draft state;
- the soft keyboard does not hide the composer or corrupt timeline layout;
- existing route diagnostics, lifecycle, readable history, and explicit raw
  terminal flows remain available.

## Phase 5: Relay-First Remote Alpha

Status: In Progress. Local Relay Packages A-D and their protocol/load gates
are implemented; Alibaba deployment and the no-`adb reverse` public Android
Emulator matrix are the next gates.

Goal: support real remote use when the phone is outside the server LAN without
requiring ordinary users to own a domain, configure Cloudflare, open router
ports, or have a public IP.

Work:

- complete the real local CCB backend matrix in
  [the local real-backend comprehensive plan](topics/local-real-backend-comprehensive-test-plan.md)
  before physical Tailnet validation;
- use
  [the app stress and performance plan](topics/app-stress-and-performance-test-plan.md)
  as the release-readiness matrix after pane-equivalent chat lands: start with
  non-disruptive snapshots, then ramp through native conversation, file/image,
  long-history rendering, multi-project, recovery, and soak gates;
- keep the completed Android Emulator app/UI behavior covered by
  [the comprehensive VM chat/attachment matrix](topics/android-emulator-comprehensive-test-plan.md)
  as auxiliary regression, not as proof of real backend chat closure;
- rebase local Android emulator paired-gateway validation on pane-backed chat
  sends and deterministic agent replies before public relay work;
- preserve local Android emulator paired-gateway validation with AVD
  `ccb_mobile_api35`, `adb reverse tcp:8787 tcp:8787`, and the existing
  `GatewayTransport` path after the chat-first surface lands;
- reserve `relay.seemlab.top` as the first planned public relay endpoint;
- define a relay route provider where the user host and phone both connect
  outbound to the relay;
- add a relay frame envelope, rendezvous model, E2EE handshake, reconnect
  model, abuse controls, and diagnostics;
- implement the hosted-relay package in
  [the invitation and Alibaba Cloud deployment plan](topics/public-relay-invitation-and-aliyun-deployment.md):
  one-use applicant invitation, host public-key binding, minimal anonymous
  security metadata, no forwarded-payload persistence, quotas, and operator
  issue/status/revoke commands;
- require
  [the public Relay Android Emulator matrix](topics/public-relay-android-emulator-acceptance.md)
  against the real Alibaba Cloud WSS endpoint before remote-alpha acceptance;
- keep CCB device tokens, scopes, revocation, and terminal tokens owned by the
  user host, not the relay;
- generate QR pairing with `route_provider: relay` while preserving LAN and
  Cloudflare payload compatibility;
- keep Cloudflare named tunnel as an advanced route provider.

Acceptance criteria:

- emulator can pair to a real local CCB backend, send to one selected agent,
  observe a deterministic backend agent reply in the timeline, upload and
  download mobile-selected attachments, download backend-agent generated
  files through authenticated gateway artifact/file links, measure response
  speed, read pane output/history, and control one CCB project through host
  loopback before public relay work starts;
- fake/local emulator chat preserves multiple consecutive sent messages,
  duplicate-body counts, document attachments, and image attachments without
  overwriting previous visible timeline entries;
- phone on cellular can pair and control one CCB project through CCB Relay;
- LAN/tailnet/relay/Cloudflare routes use the same app screens and mobile API;
- revoking the CCB device token blocks project list and terminal opening even
  if the relay route remains reachable;
- reconnect does not replay stale terminal input or lifecycle actions;
- relay cannot control CCB lifecycle and should not see terminal content in
  cleartext.
- one applicant invitation activates exactly one host under concurrent/retry
  tests, while the separate CCB mobile pairing handoff retains Decision 021
  semantics;
- relay logs, metrics, database, and filesystem contain no plaintext prompt,
  reply, terminal, path, file, token, or notification business payload.

## Phase 6: Advanced Routes And Self-Hosted Relay

Goal: keep advanced Cloudflare/tailnet/self-hosted relay options available
after the default relay path proves the gateway protocol.

Work:

- land `ccb update mobile` as a CCB-source optional bundle target, analogous
  to `ccb update rich`, so mobile/Tailscale dependencies stay out of mandatory
  `ccb update`; reviewed source worktree
  `/home/bfly/yunwei/ccb_source_mobile_update_tailnet` landed commits
  `b6e148f2` and `d73ae650` with 147 focused tests passing after follow-up;
- define the Android app upgrade/install lane so a new APK from the same
  channel can cover-install the existing app without signature conflict or
  forced uninstall; in-app upgrade can build on that lane later;
- document the stable Tailnet private route with Tailscale Serve, MagicDNS,
  tailnet HTTPS, grants, route diagnostics, WebSocket smoke, and revoke gates;
- keep Cloudflare named tunnel as a route provider behind `GatewayTransport`;
- reuse the same terminal tokens, terminal frames, event cursors, and content
  endpoints;
- support self-hosted relay configuration for users who do not want the
  default hosted relay;
- retain named-tunnel preflight and smoke tooling for domain/DNS users;
- define self-hosting docs, abuse controls, diagnostics, and trust model.

Acceptance criteria:

- `ccb update mobile` exists as the explicit host-side Mobile/Tailnet
  onboarding entry while normal `ccb update` leaves mobile dependencies alone;
- a same-channel Android APK can be installed over the existing app and
  preserves pairing/app data instead of requiring uninstall for signature
  mismatch;
- a relay spike can switch a host profile from Cloudflare URL to relay route
  without changing project ids or favorites;
- Tailnet pairing and terminal smoke can run from a physical phone/iPad
  without changing project ids, terminal ids, or UI screens;
- relay has no authority over CCB projects, tmux targets, or lifecycle actions;
- stopping relay does not stop server-side CCB projects;
- route-provider changes do not require Flutter UI rewrites.

## Deferred

- Generic SSH server management.
- Arbitrary tmux session browsing outside CCB projects.
- Mobile-created pane splits or tmux layout editing.
- Requiring every user to own a domain or configure Cloudflare.
- Running providers or CCB agents locally on the phone.
