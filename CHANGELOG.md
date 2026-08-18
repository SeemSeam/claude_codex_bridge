# Changelog

## v8.6.10 (2026-08-18)

- Fixed Issue #319: after an external Claude OAuth re-login, a stopped managed
  restart now refreshes the existing Agent-private Keychain credential instead
  of retaining a revoked token and returning 401.
- Preserved a Claude-private Keychain refresh when the inherited source is
  unchanged; external Claude Keychain services remain read-only.
- Added fail-closed handling for symlinked CCB credential projections and
  private Keychain inspection errors. No project, conversation, pairing, or
  configuration migration is required.

## v8.6.9 (2026-08-17)

- Added the official DeepSeek Harness as the separate Developer Preview
  provider `dsh`, with loopback HTTP/WebSocket transport, exact native request
  and turn completion, observer-only restore, native clear/compact handling,
  and agent-private account, skill, and runtime state.
- Fixed Issue #318 by making managed AGY 1.1.13 select its private file token
  store immediately, without a keyring timeout or writes to the source user
  HOME.
- Reverted Windows PR behavior that had crossed into shared Linux/macOS
  runtime modules, then added a trusted-base native-only Windows PR gate that
  rejects shared client, release, Mobile, npm, and generic-test changes.
- No project, conversation, pairing, or configuration migration is required.
  DeepSeek Harness remains optional and does not change the existing
  `deepseek` / `deepcode` provider.

## v8.6.8 (2026-08-17)

- Added adjustable workspace-surface opacity for local CCB Mobile backgrounds
  and extended the selected image across the project list and workspace chrome.
- Opened expanded long replies at their bottom, kept the latest bubble visible,
  and moved downloadable attachments into normal in-bubble message content.
- Hardened native Windows and Herdr runtime handling for non-UTF-8 process
  output, foreground Provider detection, pane authority, and deferred cleanup
  of still-running Agent state (PRs #314, #315, and #317; PR #316 superseded
  by the hardened cross-platform test correction).
- No pairing, conversation, project, or configuration migration is required.

## v8.6.7 (2026-08-16)

- Added local CCB Mobile workspace background customization across project
  chrome, agent chat, Agent Terminal, and standalone Computer Terminal.
- Added private-storage image validation, replacement/removal cleanup, and
  terminal readability scrims without uploading the selected image.
- No pairing, conversation, or project migration is required.

## v8.6.6 (2026-08-15)

- Restarted an already-running installed Mobile Host after a successful
  `ccb update`, preserving its route, host identity, and pairing instead of
  leaving the old gateway process on stale code. Stopped and source-owned
  Mobile Hosts remain untouched.
- Raised the Relay stream's initial receive credit to the maximum valid
  message size on both host and Flutter client, preventing large but valid
  terminal-history snapshots from blocking before the first credit update.
- Added one bounded Claude `Enter` retry when the current composer still holds
  the exact dispatched prompt evidence, without treating pane history or the
  retry itself as activation (PR #305).
- Kept explicit Agent environment and Provider routing above inherited Claude
  settings, and rebuilt reused bindings when the normalized Provider profile
  changes without clearing conversation history (PRs #307 and #308).
- Honored non-root Herdr split parents and added native-Windows `pwsh`,
  `powershell`, `bash`, and `wincmd` shell panes while preserving the same
  provider-qualified names as Agents (PRs #309 and #310).
- No configuration, pairing, or conversation migration is required. Upgrade
  both the CCB host and Mobile APK for the Relay terminal fix. Native Windows
  x64 remains beta.

## v8.6.5 (2026-08-14)

- Reconstructed wrapped Agent terminal rows as logical lines before local
  mobile reflow, so portrait and landscape widths use the device viewport
  without resizing the desktop-owned tmux pane.
- Replaced append-only full-screen terminal repaints with structured history
  and mutable-screen projections. Prompt edits and Backspace now replace the
  active row in place instead of accumulating stale lines in scrollback.
- Kept backward-compatible terminal bytes on the wire while allowing current
  Android clients to distinguish real scroll history from screen replacement.
- Existing pairing and project state require no migration. Upgrade both the
  CCB host and Mobile APK to activate the corrected projection protocol.

## v8.6.4 (2026-08-14)

- Reworked CCB Mobile Agent terminals as responsive local projections: phone
  columns now follow the device viewport while the desktop tmux pane retains
  its authoritative geometry.
- Added inline full-width Agent terminal mode, persisted terminal font sizing,
  and configurable shortcut ordering without cluttering the terminal surface.
- Hardened LAN and Relay terminal lifecycle handling, including reconnect
  coalescing, stale socket/stream cleanup, source-geometry notifications, and
  clean gateway shutdown of active sessions.
- Added fail-closed reattachment of persisted Windows Herdr namespace and pane
  references, including capability intersection with stricter live evidence
  (PR #304).
- Existing pairing data requires no migration. Upgrade the CCB host and Mobile
  APK together to use the complete terminal viewport protocol.

## v8.6.3 (2026-08-13)

- CCB Mobile now turns ordinary file links under the current Agent's
  `.ccb/workspaces/<agent>/...` tree into authenticated download attachments,
  including generated PDF reports.
- The workspace exception remains fail-closed: other Agent workspaces, hidden
  workspace paths, and the rest of `.ccb` runtime state are not exposed.
- No project configuration, pairing, or stored-state migration is required.

## v8.6.2 (2026-08-13)

- Added fail-closed, exact command approvals for project-local tool-window
  commands and Provider command templates, including the explicit
  `ccb config approve-commands` workflow (Issue #299).
- Preserved managed Codex conversation context when the current session record
  is corrupt by selecting the latest valid recoverable session instead of
  silently starting fresh.
- Added visible-pane Cursor dispatch and completion evidence, Pi native session
  history, and OMP Provider-configuration inheritance into managed homes.
- Expanded CCB Mobile with multi-session host terminals and Relay
  capability-negotiated Provider controls, while hardening native Windows
  project registration, endpoint markers, TCP descriptors, and readiness.
- Improved native Windows process liveness and command-line handling, Herdr
  namespace teardown, and background launcher behavior; integrated PRs #298,
  #300, #301, and #302.
- Projects without the newly protected command fields require no migration.
  Intentional configured commands must be approved once and reapproved after
  an exact value change. Windows x64 remains beta.

## v8.6.1 (2026-08-12)

- Added capability-driven CCB Mobile Provider controls with explicit
  configured/active/pending state, Codex/Claude session usage and optional
  account quota, and guarded restart-required model/thinking changes.
- Added a cross-project Mobile terminal launcher and customizable terminal
  shortcut order, visibility, and defaults.
- Added the built-in `ccb-compact` Skill and `ccb compact` command with busy
  gating and fail-closed Provider-native command selection.
- Replaced Config UI's hard-coded Role suggestions with the complete available
  Role catalog, including `agentroles.mother` when present.
- Preserved native-session boundaries in Mobile history, clipped wide terminal
  snapshots to pane width, delivered combined text-plus-key terminal frames in
  order, bound Provider mutation idempotency to its target, and refreshed the
  WeChat community QR image.
- No configuration or conversation migration is required. Windows x64 remains
  beta and Provider model/thinking changes require a managed Agent restart.

## v8.6.0 (2026-08-12)

- Added the first native Windows x64 distribution to the stable CCB release:
  native PE launchers, a checksum-validated PowerShell installer, and a
  Windows-owned Herdr runtime and packaging lane. Windows support remains beta
  and requires Python 3.10+, WezTerm, Git Bash, and Herdr 0.8.0+.
- Restored Pi's validated native JSONL session after CCB restart, while keeping
  fresh-context and explicit session controls unchanged.
- Preserved Linux, macOS, WSL, npm, Sidebar, and Android stable publication
  paths while isolating Windows code under platform-owned directories.
- Fixed project-scoped tmux socket rebinding after backend cache reuse and made
  non-interactive Windows installation honor `-Yes` when Herdr is absent.
  Removed the backend import cycle that could block mobile/ccbd service imports.
- No configuration or conversation migration is required.

## v8.6.0-beta.3 (2026-08-11)

- Made the Windows installer's `-Yes` and `CCB_INSTALL_ASSUME_YES=1` modes acknowledge the guarded missing-Herdr continuation without reading interactive input.
- Hardened the remaining Herdr prompt against a null `Read-Host` result.
- Supersedes `v8.6.0-beta.2`, whose native tests and ZIP build passed but whose archive installation stopped at the missing-Herdr prompt before a GitHub Release was created.

## v8.6.0-beta.2 (2026-08-11)

- Fixed the Windows release builder allowlist so it no longer requires the nonexistent `commands/` directory.
- Added a regression check that every default payload directory exists before another immutable prerelease is tagged.
- Rebound auto-selected tmux backends to each project's authoritative socket,
  fixing the Linux, macOS, and WSL lifecycle regression introduced by PR #293.
- Supersedes `v8.6.0-beta.1`, whose native tests passed but whose ZIP build was correctly blocked before a GitHub Release was created.

## v8.6.0-beta.1 (2026-08-11)

- Added an isolated native Windows x64 prerelease lane under `platforms/windows/` and `lib/platforms/windows/`.
- Added native PE launchers for `ccb`, `ask`, `autonew`, and `ctx-transfer`, packaged as `ccb-windows-x86_64.zip` with a SHA256 sidecar.
- Updated `install.ps1` to install from a validated Windows ZIP while retaining source-checkout installation.
- Restored Linux/macOS workflows, executable modes, docs, and reconnect tests removed or rewritten by PR #293; prerelease tags do not trigger stable npm, sidebar, mobile, Linux, or macOS publication.
- Marked this release as a Windows beta: Python 3.10+, WezTerm, Git Bash, and Herdr remain prerequisites, and the binaries are unsigned.

## v8.5.7 (2026-08-08)

- Keep the required `ccb-clear` control Skill and add `ccb-diagnose` for every
  supported managed Agent. `$ccb_diagnose <agentname>` combines authoritative
  runtime and job state with bounded deep Pane inspection, applies only guarded
  recovery, verifies the result, and requires explicit approval before issue
  submission.
- Self-heal expired mailbox deliveries without replaying business work and
  forward Provider no-terminal timeout settings to `ccbd` (PR #288). Remove
  stale pre-activation Claude assistant bookkeeping so old busy turns cannot
  contaminate newly queued requests.
- Show communication short ids, Agent queue depth, and active job suffixes in
  the sidebar; preserve Kiro settings under isolated `KIRO_HOME` without
  copying sessions.
- Resolve reconnect readiness probes against the active Codex Provider route,
  extending guarded network/capacity recovery to custom Provider endpoints.
- No configuration or conversation migration is required.

## v8.5.6 (2026-08-06)

### Continuous Provider Inheritance

- **Explicit Authority Wins Per Dimension**: CCB uses configured API, token,
  URL, route, account, and Provider profile values first. Only dimensions not
  explicitly owned are read from the current external Provider environment or
  login state.
- **Restart Refreshes Without Reverse Effects**: a stopped Provider generation
  is rebuilt from the current external snapshot and private projection. CCB
  never writes the inherited result back to the user's shell, Provider home,
  IDE, keyring, or remote login.
- **Conversation History Survives Authority Changes**: same-authority launches
  use native resume. Codex, Claude, and Gemini use a qualified fork/import path
  when available; otherwise CCB records a linked continuation while preserving
  the stable conversation, workspace, queue, session record, and transcript
  history. The withdrawn v8.5.5 archive shape is recovered only with matching
  ownership evidence.

### Codex Reconnect

- **Bundled Automatic Activation**: CCB installs and arms `codex-reconnect`
  automatically for managed Codex panes after a concrete thread binding.
- **Bounded Capacity Recovery**: terminal network and selected-model capacity
  failures are classified only for the exact thread and pane, then receive at
  most one literal `continue` after the existing safety and readiness gates.

### Withdrawal And Compatibility

- **v8.5.5 Withdrawn**: the GitHub release was withdrawn on 2026-08-05 after a
  legacy-session migration regression made existing managed conversations
  unavailable to native `resume`. v8.5.6 is the replacement; no manual clear or
  history deletion is required.

## v8.5.5 (2026-08-05)

> Withdrawn from GitHub on 2026-08-05 after a legacy-session migration
> regression made existing managed conversations unavailable to native resume.
> Use v8.5.4 until the replacement release is published.

### Provider State Refresh

- **Restart Uses Current Provider Authority**: `ccb restart <agent>` now rebuilds
  the Provider launch from the current profile, login, API key, endpoint, proxy,
  and model state instead of replaying the command persisted when the pane was
  first created. Switching accounts or API routes no longer requires
  `ccb clear`.
- **Cross-Authority Resume Is Blocked Safely**: Codex archives an incompatible
  managed session binding before starting fresh; Claude and Gemini suppress
  native resume when the saved session belongs to another Provider authority.
  Provider login state and history files remain intact.
- **Credentials Stay Private**: managed session metadata stores only an
  Agent-private HMAC fingerprint of Provider authority inputs, never raw API
  keys, tokens, endpoints, or proxy credentials.

### Relay And Community

- **New Relay Invitations Default To 1 GiB/Day**: newly issued Relay Host
  invitations now allow 1 GiB per 24-hour quota window. Explicit
  `--max-bytes-per-day` values and existing Host credentials are unchanged.
- **Community QR Refreshed**: the WeChat group image and README cache key now
  point to the current invitation image.

## v8.5.4 (2026-08-03)

### Ask Routing

- **Chains Now Require A Real Dependency**: inherited ask guidance for every
  supported Provider defaults to plain routing. `--chain` is reserved for an
  active parent task that cannot finish without that exact child result;
  communication tests, batch sends, notifications, and independent asks no
  longer imply a chain dependency.
- **Reply Delivery Cannot Become A False Parent**: control-plane
  `reply_delivery` work is excluded from active-parent detection, so an
  overlapping ordinary ask is not rejected or attached to an acknowledgement.

### Agent History And Config UI

- **History Cleanup Is Bounded Per Agent**: Config UI can scan or clean one
  Agent or all Agents with 7/30/90-day retention. Current session bindings,
  records inside the retention window, and each Provider's newest transcript
  remain protected; deletion is restricted to the managed transcript allowlist.
- **The Panel Is Focused Again**: the current Config UI keeps configuration,
  activation review, and history cleanup while temporarily delisting the
  read-only Agent communication-flow observer.

### Mobile LAN Recovery

- **LAN Failures Are Actionable**: Android uses coarse, non-identifying
  connectivity evidence to warn before LAN pairing and explain reconnecting
  LAN routes. Stored profiles are preserved, and users can Retry or open the
  existing authenticated Diagnostics flow.
- **Silent Terminal Disconnects Recover**: terminal WebSockets use a 15-second
  client ping interval so Wi-Fi half-open connections enter the existing
  reconnect/cursor-resume path without replaying input.
- **Network Identity Stays Private**: CCB Mobile requests only
  `ACCESS_NETWORK_STATE`; it does not read SSID, BSSID, location, or other
  network identity. Computer-side onboarding now covers same-network, hotspot,
  guest isolation, VPN, firewall, and DHCP address-change recovery.

## v8.5.3 (2026-08-01)

### Relay Capacity

- **New Invitations Default To 200 MiB/Day**: newly issued Relay Host
  invitations now allow 200 MiB per 24-hour quota window instead of
  100 MiB. Explicit `--max-bytes-per-day` values still override the default;
  existing Host credentials retain the quota issued with them.

### Callback Repair Efficiency

- **Large Ledgers No Longer Amplify Idle Reads**: callback-edge latest state
  is cached from the append-only authority and safely invalidated on external
  changes. Repair inspects only nonterminal candidates and scans each target's
  job history at most once per tick, preventing historical callback growth
  from causing sustained idle CPU and cached-read pressure.
- **No Ledger Migration Required**: JSONL remains the durable authority and
  existing ledgers are rebuilt into the derived in-memory view on restart.

### Claude Agent Environment

- **General Agent Variables Reach Claude**: non-API variables from
  `agents.<name>.env`, including proxy and tool configuration paths, are now
  exported to managed Claude launches. CCB-managed API credentials, endpoints,
  and runtime-home overrides retain precedence (PR #284).

## v8.5.2 (2026-07-30)

### Bounded Pane Recovery

- **Recovery Requires Stability**: a respawned pane remains probing for
  90 seconds and must receive a new healthy observation before it can drain
  queued work.
- **Crash Loops Back Off And Stop**: unstable recovery uses
  30s/60s/120s/5m/10m/30m delays and opens a durable circuit after six
  attempts. Runtime diagnostics expose the probe, consecutive-failure, and
  circuit state.
- **Crash Writes Stay Bounded**: each Provider runtime retains only the newest
  50 pane-crash records, pane history is cleared before respawn, and unchanged
  helper manifests are not rewritten.
- **Provider Failures Are Contained**: stale CCB-owned Claude continuation
  state can be removed without changing authentication, while a missing
  managed Codex app server fails closed and requests restart/remount.

### Concise Managed Communication

- **Stable Guidance Lives In Memory**: normal asks no longer repeat the default
  reply-policy and cancellation-file blocks. Compact/silent mode remains
  explicit, and runtime interruption is the primary cancellation path.

### Rich Terminal Isolation

- **GUI Launches Detach Cleanly**: CCB Rich WezTerm starts in a new process
  session with standard streams detached, preventing GUI diagnostics and
  job-control signals from polluting or stopping the invoking terminal.
- **Wayland Cursor Compatibility Is Private**: a CCB-owned XCursor overlay
  supplies the missing `hand` cursor without replacing the selected theme or
  modifying user/system cursor files.

## v8.5.1 (2026-07-30)

### Claude Completion Integrity

- **Late Final Snapshots Win**: Claude assistant snapshots are aggregated by
  API message id, so a thinking-only `end_turn` waits for the visible final
  text instead of completing with earlier `Let me...` process narration.
- **Terminal Replies Are Isolated From Progress**: completion artifacts now use
  only the confirmed terminal assistant message; tool-only boundaries and
  stop-reason-free process text cannot promote the cumulative progress buffer.
- **Failure And Attribution Are Fail-Closed**: stalled mid-stream responses are
  failed, empty Stop hooks allow a bounded late-final grace, and all normal or
  recovery hook paths require exact request, workspace, timestamp, and Claude
  session identity.

### Pi Visible Completion

- **Pi Replies Stay Visible In The Managed TUI**: new Pi asks execute in the
  existing managed pane and publish exact lifecycle evidence through the
  official extension API instead of running in a hidden one-shot process.
- **Only The Settled Final Message Completes**: exact dispatch, actor, launch
  session, runtime instance, and request identity bind the final
  `agent_settled` reply. Tool narration, retries, stale sessions, manual input,
  and partial or malformed events cannot become a successful reply.
- **Long Runs Are Not Arbitrarily Truncated**: Pi pane execution has no default
  terminal wall-clock cutoff. Native cancellation no longer asks the model to
  probe a cancel file, avoiding an extra tool call and uncached-token cost.
  Persisted 8.5.0 jobs and `CCB_PI_EXECUTION_MODE=headless` retain the
  compatible one-shot path.

### Mobile Host Health

- **Loopback Health Checks Ignore Proxies**: Mobile host startup checks connect
  directly to the local gateway even when `HTTP_PROXY`, `HTTPS_PROXY`, or
  `ALL_PROXY` is configured, preventing false startup failures.

## v8.5.0 (2026-07-29)

### Pi And OMP Completion

- **Native Terminal Evidence Is Exact**: Pi `0.82.1` requires its final
  `agent_settled` event, while OMP `17.1.6` requires
  `agent_end.isTerminal=true`. Intermediate turns, retries, and legacy event
  shapes cannot complete a CCB job.
- **Completion Waits For Stream Closure**: semantic completion is accepted only
  after the one-shot process exits and output closes. Missing outcomes,
  malformed or truncated JSONL, provider errors, and nonzero exits fail
  closed; terminal OMP `yield` results remain supported.

### npm Runtime And Config UI

- **npm Installs Repair Their Managed Python**: postinstall now creates or
  repairs the release-managed virtual environment and installs the required
  runtime dependencies instead of falling back to an incomplete system Python.
- **Linked Worktrees Stay In Source Mode**: source installs now recognize both
  Git directories and linked-worktree `.git` files instead of applying packaged
  release behavior inside a development checkout.
- **Sidebar Settings Opens Reliably**: the settings launcher recovers the
  release-managed interpreter and opens Config UI even when inherited runtime
  paths are stale.

### Mobile Activity

- **Provider Activity Is Synchronized**: Mobile ask state, provider activity,
  completion notifications, and selected-agent views now follow exact
  server-side state across reconnects.
- **Automatic Recovery Is Quiet**: expected reconnect transitions no longer
  surface misleading snackbar errors.

### Managed Provider Assets

- **Asset Projection Is One-Way And Owned**: managed Provider assets are
  refreshed inside private homes without writable aliases back to user-global
  state, and packaged Qoder `ask` / `ccb-clear` controls are now guaranteed.

## v8.4.3 (2026-07-27)

### Managed Provider Authentication

- **Authentication Is One-Way And Agent-Private**: managed visible and
  headless provider processes now use private HOME, XDG, provider, session,
  storage, and credential roots. Safe external credentials are inherited into
  ordinary agent-local files without writable aliases back to user state.
- **Global Credentials Stay Read-Only**: managed refresh and logout operations
  cannot rewrite the user's shell, IDE, another agent, or another project's
  authentication. Unsupported or ambiguous credential backends fail closed
  instead of escaping the managed boundary.

### Managed Control Skills

- **Required CCB Controls Are Always Present**: packaged `ask` and `ccb-clear`
  controls are projected for supported managed agents even when optional skill
  inheritance is disabled. Managed Codex also retains its required `reconnect`
  control.
- **Broken Optional Skills Stay Isolated**: optional skills are projected per
  entry, so a broken external skill cannot suppress CCB-owned controls or
  unrelated valid skills.

### Mobile Pairing And Terminal

- **Relay Pairing Fits Narrow Terminals**: Relay pairing can render a compact
  signed-capability QR that fits a 97-column terminal while retaining the
  owner-only PNG fallback. The Mobile app validates and reconstructs the full
  pairing payload from the compact form.
- **Terminal Resize And Recovery Are Race-Safe**: synchronized snapshot
  generations prevent resize/read races, resize triggers a clean repaint, and
  the Mobile client renews terminal handles after closed, output, or stream
  failures.

## v8.4.2 (2026-07-27)

### Config UI And Appearance

- **Settings Opens With The Managed Runtime**: the sidebar launcher now
  recovers from stale inherited Python paths and opens Config UI with the
  release-managed interpreter.
- **Theme Selection Is Persistent**: Config UI exposes the supported CCB
  themes, including system-default behavior. The saved preference is
  authoritative across Rich WezTerm and sidebar restarts instead of being
  overwritten by stale process environment.

### Mobile Relay And Pairing

- **Relay Terminal Streaming Is Stable**: bounded backpressure, initial
  snapshot handling, incremental screen updates, and wide-character accounting
  prevent dropped or corrupted mobile terminal output.
- **Dense Pairing Codes Stay Scannable**: when a QR payload cannot fit safely
  in the current terminal, CCB writes an owner-only PNG and prints its path
  instead of rendering wrapped, unreadable terminal art.

### Provider Updates

- **Package-Manager Ownership Is Preserved**: npm packages update through their
  detected user or NVM prefix, Bun-owned packages retain their Bun home, and
  non-writable system installations remain report-only.
- **Unsafe Registry Metadata Fails Closed**: releases that resolve to local
  package dependencies are reported but not installed.

## v8.4.1 (2026-07-26)

### Release Installer

- **Linux Release Dependencies Are Isolated**: official release installs now
  create a CCB-managed Python virtual environment on Linux as well as macOS.
  Mobile Relay dependencies no longer require or modify the system Python's
  pip installation.
- **Interrupted Downloads Retry Safely**: transient pip download failures such
  as `IncompleteRead` and broken connections retry on the same package index
  when no configured fallback index is available.

## v8.4.0 (2026-07-26)

### Mobile Relay And Pairing

- **Encrypted Relay Transport**: CCB Mobile can connect through an AEAD
  encrypted Relay with bounded admission, multiplexed unary/terminal/event/file
  streams, one-time host invitations, and official or self-hosted deployment
  modes. Relay operators cannot read application payloads.
- **Route Choice Lives On The Computer**: `ccb update mobile` guides Tailscale,
  validated private-LAN, official Relay, and self-hosted Relay setup. The phone
  now only scans a QR code or enters a pairing code.
- **Trusted Android Updates**: the app checks canonical GitHub release metadata
  and verifies the signed APK's exact size and SHA-256 before asking Android to
  install it.

### Identity, Appearance, And Recovery

- **Projects Survive Relocation**: stable project identity is preserved when a
  project directory is moved or renamed, while ambiguous or foreign identity
  claims fail closed.
- **System Theme Following**: terminal, workbench, and Config UI surfaces can
  follow the operating system's light/dark appearance without changing project
  behavior.
- **Managed Codex Reconnect**: the opt-in reconnect watcher is projected into
  managed Codex homes, binds exact pane/session identity, retains only bounded
  terminal failure evidence, and refuses unsafe recovery input.

### Configuration And Operations

- **Config Changes Apply Predictably**: provider-affecting Config UI edits are
  recorded for the next CCB start instead of silently mutating active panes.
- **Relay Deployment Is Operator-Bounded**: production service, TLS proxy,
  invitation issuance, quota, rollback, and self-hosting guidance are included;
  public invitations remain operator-issued and single use.

## v8.3.1 (2026-07-24)

### Unified Provider Updates

- **Provider Upgrades Are Managed By `ccb update`**: managed panes suppress
  supported provider-native startup prompts, while `ccb update` detects the
  executable owner, compares exact versions, and offers update, decline, or
  version-scoped skip choices without restarting active panes.
- **Non-Interactive Policy Is Explicit**: `--providers check`,
  `--providers all`, and `--providers none` provide report-only, supported
  bulk-update, and skip behavior for automation and controlled deployments.

### Provider Storage And Cleanup

- **Project Provider Caches Are Retired**: Claude uses the user-installed
  executable and Gemini uses one user-scoped rebuildable cache instead of
  copying provider software into every CCB project.
- **Legacy Cleanup Is Bounded And Automatic**: post-update and successful
  project shutdown clean only ownership-verified legacy Claude/Gemini cache
  payloads. Active projects, malformed manifests, foreign links, sessions,
  authentication data, and user-scoped caches are preserved; one update can
  opt out with `--no-cache-cleanup`.

### Configuration, Lifecycle, And Rich Mode

- **Qoder CLI CN Is A Native Provider**: the official
  `@qodercn-ai/qoderclicn` distribution is registered as `qoderclicn`, with
  isolated visible/headless config and session state, UUID session ids,
  permission mapping, native stream-result completion, and managed suppression
  of provider-owned update prompts.
- **Qoder Uses Its Documented Headless Contract**: Qoder and Qoder CLI CN use
  native `--print`, workspace, and `--config-dir` arguments instead of the
  retired generic bare-mode assumptions.
- **Config UI Access Can Be Persistent**: projects can configure a stable
  loopback port and a token source through `token_env` or a protected
  project-relative `token_file`, while the CLI avoids printing token values.
- **Shutdown Finalizers Stay Reliable**: accepted shutdown work keeps its
  completion path while the server is stopping, preventing cleanup callbacks
  from being abandoned.
- **Kill Tolerates The Expected Socket-Close Race**: if the keeper closes ccbd
  immediately after shutdown intent is recorded, the CLI continues bounded
  local authority cleanup instead of surfacing a transient missing-socket
  error.
- **WSL Process Audits Degrade Safely**: live processes remain visible when WSL
  does not expose per-process procfs I/O counters; the audit reports partial I/O
  capability instead of misclassifying those processes as vanished.
- **Yazi Uses A Compact Two-Column Layout**: Rich mode removes the redundant
  preview column and allocates more width to the file list and active preview.

### Release Surface

- **Packaging Is Portable And Synchronized**: sidebar checksum generation works
  across supported hosts, and CLI, npm, Linux, macOS, and Android versions,
  assets, links, and workflow defaults target 8.3.1.

## v8.3.0 (2026-07-22)

### Managed Provider Reliability

- **Requests Stay Bound To Their Native Turns**: Kimi resumes the exact native
  session and accepts replies only from the current wire turn, while Claude
  queued prompts become eligible only after their activation boundary.
- **Qoder Follows Its Real CLI Contract**: managed execution uses qodercli's
  native non-interactive, session, resume, update, storage, and completion
  behavior instead of assumptions inherited from other providers.
- **Provider Extensions Are Inherited Safely**: managed Claude, Gemini, Droid,
  Qwen, and Copilot homes project supported extensions and plugins only with
  explicit ownership evidence, preserving user-owned assets and cleanup
  boundaries.

### Job Integrity And Diagnostics

- **Follow-Ups Target The Exact Active Job**: active work can receive bounded
  follow-up steering without starting a competing request or losing the
  original job, message, attempt, and provider-turn lineage.
- **Execution State Is Correlated End To End**: project view, trace, doctor,
  heartbeat, sidebar, and Mobile surfaces expose shared execution phases and
  bounded diagnosis for orphaned active inbound work.
- **Cancelled Callback Chains Terminate Cleanly**: cancellation now records a
  terminal continuation outcome instead of leaving child callbacks or delivery
  state indefinitely active.

### Update, Workspace, And Mobile Safety

- **npm Owns npm-Managed Upgrades**: vendored npm payloads delegate updates to
  the package manager, keeping package metadata and installed files atomic.
- **Marker-Only Worktrees Retire Conservatively**: CCB removes an owned binding
  only after proving no user payload is present, while retaining legacy or
  ambiguous worktrees for recovery.
- **Mobile Terminal Mode Stays In The Project**: chat and terminal views share
  the selected project workspace, preserve agent/window navigation, and avoid
  escaping into a detached screen.
- **Sidebar Settings Open Reliably On WSL And macOS**: project tmux panes retain
  desktop/browser transport state, host-native openers run before generic
  browser discovery, immediate opener failures fall through, and the sidebar
  displays a manual-open warning instead of appearing to ignore the click.

### Release Surface

- **Release Versions Are Synchronized**: CLI, npm, Linux, macOS, Android,
  localized badges, workflow defaults, download links, and checksums target
  8.3.0.

## v8.2.1 (2026-07-17)

### Startup And Lifecycle Reliability

- **Startup Generations Are Fenced End To End**: keeper, daemon, socket,
  lifecycle, lease, and mounted publication now carry one verified startup
  identity, preventing stale or overlapping attempts from publishing authority.
- **Readiness Is Proven Before Mount Publication**: ccbd opens a bounded
  bootstrap probe and validates socket ownership, serving identity, namespace,
  pane topology, and runtime authority before the project becomes mounted.
- **Startup Diagnostics Are Actionable**: operation counts, process and I/O
  samples, readiness timelines, exact T1 attribution, and phase checkpoints are
  available through the performance and diagnostics tooling without weakening
  lifecycle checks.
- **CLI-Only Cost Has An Isolated S0 Baseline**: the benchmark primes one
  healthy project, measures the version introspection fast path without opening
  another startup transaction, and proves daemon, namespace, runtime, and
  startup-report authority remained unchanged before teardown.

### Recovery And Communication

- **Revoked Provider Authentication Stops Restart Loops**: unrecoverable auth
  failures become a durable blocked runtime state, remain queued instead of
  respawning, and expose the required login action through ping, project view,
  and the sidebar. This completes Issue #251 option 2 while retaining supported
  in-place credential refresh.
- **Reload And Shutdown Preserve Authority Boundaries**: additive topology
  reloads validate session and namespace-epoch pane identity, and an accepted
  shutdown request remains idempotent while the server is already stopping.
- **Runtime Imports Are Order Independent**: the app runtime package now loads
  public exports lazily, eliminating a handler/bootstrap circular import that
  could break isolated diagnostics and test collection.

### Mobile And Gateway

- **Android Can Keep A User-Enabled Background Connection**: the Mobile app
  exposes Android background-access status, foreground-service controls, and
  battery-restriction guidance while preserving explicit opt-in behavior.
- **Conversation Progress Stays Agent Scoped**: each agent retains one working
  reply indicator, so background refreshes and agent switching cannot duplicate
  or transfer an in-progress reply.
- **Push Support Remains Optional**: FCM authentication dependencies stay
  optional and fail closed when deployment-owned Firebase configuration is not
  present.

### Planning And Release Surface

- **Parallel PlanTree Work Has A Global Authority Design**: the workflow plan
  documents cross-worktree roots, global roadmap control, lane ownership,
  evidence projection, migration, and acceptance gates for future v3 rollout.
- **Release Versions Are Synchronized**: CLI, npm, Linux, macOS, Android,
  localized badges, workflow defaults, download links, and checksums target
  8.2.1.

## v8.2.0 (2026-07-16)

### Startup Performance

- **Cold And Warm Starts Do Less Repeated Work**: ccbd reuses validated pane,
  topology, provider-profile, storage, and identity evidence across the startup
  critical path while preserving lifecycle and ownership checks.
- **Tmux And Storage Operations Are Bounded**: startup snapshots and atomic
  storage helpers avoid repeated subprocess and filesystem work without adding
  an always-on polling loop or changing per-agent launch ordering.
- **Copied Or Moved Projects Recover Safely**: an unmounted lease left by a
  different project anchor is treated as residue and replaced with a fresh
  generation, while a mounted foreign lease still fails closed; reattached
  runtime records adopt the current project identity.

### Communication Reliability

- **Codex Delivery Stays Session-Bound**: managed asks bind delivery and reply
  collection to the intended request anchor and durable session identity,
  including after a provider context clear.
- **Accepted Transport Acknowledgements Cannot Loop**: an accepted empty
  reply-delivery acknowledgement is consumed as transport completion instead
  of being requeued as an empty provider answer.

### Provider And Configuration Reliability

- **Grok Fullscreen Overrides Minimal Mode**: explicit `--fullscreen` startup
  arguments suppress CCB's default `--minimal`, while default and unrelated
  argument launches keep minimal mode. This resolves Issue #255.
- **Claude Authentication Kind Is Preserved**: managed Claude homes retain the
  inherited credential type instead of rewriting it into an incompatible
  login form.
- **Config Choices Remain Explicit**: model and thinking selections stay bound
  to the selected agent across multi-window overlays instead of reverting to
  inherited values after save.

### Mobile And Gateway Reliability

- **Recovery And Conversation Refresh Are Stable**: foreground recovery,
  retained chat state, active selection, and working indicators converge
  without refresh flicker or stale outcomes replacing current state.
- **Attachments Cover Real Mobile Work**: image, document, and video selection,
  Tab-based attachment queueing, linked host-file download, and attachment
  progress/error states are supported through the authenticated gateway.
- **Terminal And Conversation Interaction Is Smoother**: terminal input opens
  from the latest output, expanded bubbles hand scrolling back predictably,
  and background refreshes preserve the visible timeline.
- **Opt-In Push Delivery Is Hardened**: device-bound FCM registration,
  deduplication, and foreground/background lifecycle handling are available to
  deployment-configured builds; the signed public APK keeps Firebase disabled
  by default and fails closed without operator-owned configuration.

### Release Surface

- **Desktop, npm, And Android Versions Are Synchronized**: Linux and macOS
  artifacts, `@seemseam/ccb`, CLI metadata, Mobile version code, APK download
  links, checksums, and localized version badges all target 8.2.0.
- **Community Asset Packaging Is Current**: the updated PNG community image is
  included and verified by the release builder.
- **Fresh Installs Preserve The Python Launcher**: release installs keep the
  shared interpreter resolver intact when the executable directory is inside
  the install prefix, preventing recursive launcher invocation during smoke
  checks and custom-prefix installs.

## v8.1.6 (2026-07-14) — Withdrawn

- Superseded by later releases. Detailed notes are intentionally omitted.

## v8.1.5 (2026-07-14) — Withdrawn

- Withdrawn and superseded. Detailed notes are intentionally omitted.

## v8.1.4 (2026-07-13)

### Mobile Connection Recovery

- **Gateway Recovery Is Centrally Supervised**: foreground resume, route
  activation, repository probes, terminal transport, and task notifications
  now report through one bounded connection state machine instead of starting
  competing recovery loops.
- **Stale Outcomes Cannot Replace Current State**: generation fencing and
  atomic route verification discard late probe, activation, and reconnect
  results after profile or route changes.
- **Background Notifications Release Their Sockets**: notification streams
  suspend with the app lifecycle, close their active connections, and resume
  through serialized subscriptions without duplicate listeners.
- **Authentication Failures Stay Actionable**: authorization failures are
  classified separately from transient transport failures so reconnect policy
  cannot hide a required re-pair operation.

### Codex Native Subagent Isolation

- **Child Rollouts Cannot Capture CCB Requests**: managed Codex session
  discovery, watchdog recovery, and persisted binding updates now reject native
  subagent rollouts and recover the authoritative top-level session.
- **Parent Turn Binding Stays Immutable**: once a CCB request is anchored to its
  parent Codex turn, child task identifiers cannot replace that binding or
  finalize the request.
- **Child Collaboration Output Stays Private**: native subagent activity,
  messages, and foreign-turn completion events are excluded from CCB reply
  collection in both the Python runtime and Rust accelerator.
- **Real Provider Path Verified**: an authenticated managed Codex test using the
  built-in `spawn_agent` path confirmed that the caller receives only the
  parent agent's final result.

### Grok Native CCB Skills

- **Ask And Clear Skills Projected Per Agent**: managed Grok homes now receive
  independently owned native `ask` and `ccb-clear` skills when skill
  inheritance is enabled, without replacing conflicting user-owned skills.
- **Normal Starts Use Native Maximum Permission**: managed Grok now starts with
  `--permission-mode bypassPermissions`, aligned with other auto-permission
  providers. Safe `ccb -s` starts omit both bypass mode and the CCB skill allow
  rules; skill inheritance still controls only the two projected command rules.
- **System Login Is Refreshed Into Managed Homes**: Grok startup refreshes
  inherited auth/config state from the user-owned Grok home before the managed
  visible session launches.
- **Ask Uses The Visible Native Session**: incoming CCB requests are delivered
  to the target Grok pane and complete only from that prompt's native
  `turn_completed/end_turn` event; reply delivery returns to the caller's
  visible Grok pane instead of disappearing into a detached headless session.
- **Real Cross-Agent Path Verified**: a two-Grok authenticated source-runtime
  test passed visible Grok-to-Grok ask and result recovery, native `EndTurn`
  completion, named `ccb clear grok2`, and post-clear isolation checks.

### Sidebar Config Entry Reliability

- **Current Helper Replaces Stale Panes In Place**: managed sidebar panes record
  the installed helper content identity, and both daemon topology refresh and
  foreground startup repair stale helpers without restarting agent panes.
- **Release Installs Replace Runnable Old Helpers**: installation now compares
  bundled helper content and rebuilds live-source helpers when Rust inputs are
  newer, rather than treating any runnable binary as current.
- **Settings Launch Is Observable**: the gear action resolves the CCB executable
  from the same install tree, accepts the full two-cell hit area, and reports
  opening state, the local URL, or the concrete startup error in the sidebar.

### Claude Legacy Hook Migration

- **Old Project Hooks Self-Heal**: managed Claude preparation removes only the
  legacy CCB commands that run extensionless finish/activity Bash launchers
  through Python, while preserving user hooks, valid Python scripts, current
  direct launchers, and every unrelated project setting.

### Config Control Agent Removal

- **Selected Agents Can Be Removed Visually**: the V1/V2 config editor removes
  the selected Agent leaf, collapses its binary split by promoting the sibling
  subtree, cleans the stale Agent overlay, and supports undo before activation.
- **Hot Removal Preserves Unrelated Sessions**: guarded hot reload delegates to
  the existing `remove_agent` transaction, reflows only the affected Window,
  realizes the target binary topology, preserves other Agent panes, and reports
  busy Agent drain as saved pending work instead of a generic failure.

### Release Surface

- **Release Metadata Synchronized**: VERSION, source CLI metadata,
  `package.json`, Mobile app metadata and download links, workflow dispatch
  defaults, README variants, and release notes are aligned for 8.1.4.

## v8.1.3 (2026-07-13)

### Mobile Interaction Reliability

- **Live Replies Stay In One Bubble**: streamed and refreshed conversation
  state now merge into the existing working bubble while preserving element
  identity, eliminating refresh flicker, duplicate replies, and false working
  indicators.
- **Selection And Notifications Stay Stable**: agent/window selection survives
  project refreshes, retained invalidations no longer trigger refresh storms,
  and completion notifications settle against authoritative conversation state.
- **Terminal History Remains Pane-Authentic**: the first terminal snapshot
  includes bounded tmux scrollback without duplicating screen content, later
  repaints retain the user's history position, and keyboard input requires an
  explicit activation gesture.
- **Embedded Pairing Scanner Ships In Release**: Android pairing uses the
  embedded ML Kit scanner and release minification preserves the scanner's
  required classes.

### Provider Reliability

- **Codex Control Entries Filtered**: provider-local control transcript rows no
  longer appear as user conversation content in Mobile.
- **Grok Completion Requires Turn Evidence**: managed Grok requests wait for
  native turn-completion evidence instead of finalizing on intermediate output.

### Release Surface

- **Release Metadata Synchronized**: VERSION, source CLI metadata,
  `package.json`, Mobile app metadata and download links, workflow dispatch
  defaults, README variants, and release notes are aligned for 8.1.3.

## v8.1.2 (2026-07-11)

### Mobile Conversation And Terminal Reliability

- **Invalidation Recovery Hardened**: snapshots, invalidation cursors, live
  conversation refreshes, task-completion notifications, and attachment echo
  reconciliation now recover consistently without duplicate or stale items.
- **Conversation Navigation Restored**: expanded bubbles scroll correctly and
  project-backed file links resolve through the authenticated gateway.
- **Terminal Controls Simplified**: compact terminal controls remove redundant
  shortcuts and duplicate headers while preserving navigation and input.

### macOS Installer Certificate Recovery

- **Legacy pip Is Refreshed Safely**: reused managed environments with pip older
  than 24.2 refresh pip before optional dependencies are installed, enabling
  current system-certificate behavior.
- **truststore Is Capability-Gated**: pip 22.2 through 24.1 opts into system
  trust only when the truststore backend is actually available.
- **HTTPS Fallback Coverage Expanded**: common macOS DNS, proxy, timeout,
  connection, and certificate errors trigger the configurable HTTPS mirror
  retry without adding HTTP indexes or disabling TLS verification.

### Release Surface

- **Release Metadata Synchronized**: VERSION, source CLI metadata,
  `package.json`, Mobile app metadata and download links, workflow dispatch
  defaults, README variants, and release notes are aligned for 8.1.2.

## v8.1.1 (2026-07-10)

### Mobile Realtime Recovery

- **Invalidations Replace Active Polling**: the server-wide gateway now
  publishes a bounded SSE invalidation journal for project, activity, and
  conversation changes. CCB Mobile refreshes authoritative REST state from
  those signals without send-follow polling or active-project polling.
- **Snapshots Survive Reconnects**: bounded read-only snapshots preserve the
  selected host, project, agent, and recent conversation state through gateway
  interruptions. The app exposes reconnect status, recovers automatically,
  keeps completion notifications working, and does not replay stale sends.
- **Legacy Gateway Processes Are Adopted**: Mobile host startup recognizes and
  safely takes over the previous foreground gateway process when its script,
  command, and listener match, avoiding duplicate listeners during upgrades.

### macOS Installer Reliability

- **Managed Python Environments Are Reused**: release updates preserve and
  validate the existing managed `.venv`, so a working `watchdog` installation
  is no longer deleted and downloaded again on every update.
- **pip Has A Guarded Mirror Fallback**: install-time pip commands respect
  `CCB_PIP_INDEX_URL` and, on macOS TLS or network failures, retry through the
  configurable TUNA PyPI fallback. The fallback can be disabled with
  `CCB_PIP_FALLBACK_INDEX_URL=0`; no global pip configuration or TLS bypass is
  written.

### Release Surface

- **Release Metadata Synchronized**: VERSION, source CLI metadata,
  `package.json`, Mobile app metadata and download links, workflow dispatch
  defaults, README variants, and release notes are aligned for 8.1.1.

## v8.1.0 (2026-07-10)

### Configuration Control And Lighter Defaults

- **Visual Config Control Added**: the sidebar's top-left `⚙` action and
  `ccb config ui` now open a loopback-only project control panel for windows,
  pane splits, providers, models, thinking levels, API overrides, workspaces,
  Rich mode, sidebar settings, validation, diff review, save, and guarded hot
  reload.
- **Blank Projects Start With One Agent**: when project and user config are
  absent, CCB now mounts exactly one agent named `demo` and selects the first
  locally available supported CLI, preferring Codex, Claude, and Gemini. An
  explicit project or user config remains fully authoritative for custom
  single- or multi-agent topologies.
- **Config Documentation Refreshed**: the English homepage and all localized
  READMEs now show the settings entry, the real control-panel UI, and the new
  lightweight default; localized files live under `README/` with Chinese at
  `README/zh.md`.

### Provider Integration And Reliability

- **Grok CLI Integrated**: Grok is available as a managed native CLI provider
  with isolated home/session state, readiness and completion handling,
  diagnostics, tests, and public README badge coverage.
- **Kimi Readiness Updated**: Kimi Code v0.23.1 startup detection no longer
  depends on the removed K2.7 brand banner.
- **OpenCode Fresh Sessions Honored**: explicit fresh/new-context launches no
  longer auto-continue an older OpenCode session.
- **Claude And Gemini Hook Interpreter Fixed**: managed completion and
  activity hooks execute the CCB bash launchers directly, allowing their
  shebang and `_ccb-python` resolver to select a compatible Python runtime and
  avoiding the extensionless-launcher failure reported in GitHub issue #249.

### Mobile Performance And Resilience

- **Gateway Profiles Persisted**: preferred Mobile gateway profiles and paired
  credentials survive temporary gateway outages instead of being discarded.
- **Project Discovery Kept Responsive**: server project health is cached and
  the previous project list remains visible while refreshes warm, reducing
  repeated health checks and transient empty states.
- **Terminal UI Work Reduced**: working-indicator repaints and terminal input
  state scans are reduced without changing input or activity behavior.

### Release Surface

- **Release Metadata Synchronized**: VERSION, source CLI metadata,
  `package.json`, Mobile app metadata and download links, workflow dispatch
  defaults, README variants, and release notes are aligned for 8.1.0.

## v8.0.19 (2026-07-07)

### Mobile Host Startup

- **Mobile Host Health Window Extended**: `ccb update mobile` now gives
  the server-wide loopback health endpoint a longer per-request and overall
  startup window before declaring the mobile host unhealthy. This prevents
  false failures when many mounted projects make `/v1/health` take longer than
  the previous 0.5 second request timeout.
- **Mobile Host Health Regression Covered**: tests now cover a healthy
  mobile host whose health response arrives after the old too-short timeout.
- **Release Surface Synchronized**: VERSION, package.json, mobile app version
  metadata, update links, README variants, and release workflow defaults are
  aligned for 8.0.19.

## v8.0.18 (2026-07-07)

### Codex Auth Projection And Mobile Host Health

- **Codex Auth Sidecars Projected Safely**: managed `CODEX_HOME`
  materialization now mirrors `auth.json`, `config.toml`,
  `company-codex-api-key`, `company-codex.config.toml`, and safe
  auth/key/token sidecar filenames referenced by `config.toml`.
- **Auth Projection Manifest Added**: managed homes write
  `.ccb-auth-projection.json` with source/target existence, size, and SHA256
  metadata only, without storing credential plaintext.
- **API Authority Isolation Preserved**: explicit Codex API authority removes
  inherited auth sidecars so global login state and agent-local API config do
  not mix.
- **WSL Codex Doctor Improved**: `ccb doctor` marks Codex binaries resolved to
  Windows interop executables with `reason=wsl_windows_interop_executable`.
- **Mobile Host Health Hardened**: server-wide mobile project discovery now
  tolerates stale project records instead of failing the whole list.
- **README Contact Polish Added**: the public Role list is folded and the
  contact WeChat image has been refreshed.
- **Release Surface Synchronized**: VERSION, package.json, mobile app version
  metadata, update links, README variants, and release workflow defaults are
  aligned for 8.0.18.

## v8.0.17 (2026-07-07)

### Ask Stability And Mobile Update

- **Ask Reply Detection Hardened**: Codex prompt delivery now uses a
  no-progress timer based on compact session evidence instead of failing only
  from elapsed submit time, reducing false failures on slow WSL/mac session
  writes.
- **Missing Session Diagnostics Added**: when the official Codex session/log
  cannot be found past the no-progress window, CCB returns attributable
  non-success diagnostics instead of silently waiting; shutdown evidence is
  reported as provider-crashed failure.
- **Mobile Frontdesk Submit Routed Through Ask**: CCB Mobile frontdesk messages
  now submit ccbd ask jobs rather than writing directly to panes.
- **Mobile Update Watch Waits For Real Terminal State**: `ccb watch` no longer
  has a default 10 second timeout, so `ccb update mobile` and other long
  update flows can wait for the real terminal condition. Explicit
  `CCB_WATCH_TIMEOUT_S` remains supported.
- **Release Surface Synchronized**: VERSION, package.json, mobile app version
  metadata, update links, README variants, and release workflow defaults are
  aligned for 8.0.17.

## v8.0.16 (2026-07-05)

### Mobile Reconnect And Activity

- **Mobile Terminal Reconnect Hardened**: CCB Mobile terminal sessions now show
  explicit reconnecting state, disable unsafe input while disconnected, and
  recover through the existing fresh-handle path when the gateway comes back.
- **Mobile Pane Activity Recorded**: gateway-side pane input now bumps project
  activity so server-wide project recency can reflect Terminal-mode usage.
- **Release Surface Synchronized**: VERSION, package.json, mobile app version
  metadata, update links, mobile release manifest, and release workflow
  defaults are aligned for 8.0.16.

## v8.0.15 (2026-07-05)

### Ask Routing And Mobile Runtime

- **Ask Chain Terminology Simplified**: inter-agent dependent work now uses
  `chain` terminology consistently across CLI help, skills, manuals, and
  tests, removing the older callback wording from user-facing guidance.
- **Ask Project Boundary Hardened**: `ask` routing is constrained to the
  current `.ccb` project by default so provider cwd drift cannot silently send
  work into another mounted project.
- **Mobile Terminal Pane Snapshots Stabilized**: CCB Mobile terminal streaming
  now reads the selected tmux pane snapshot directly and requires pane evidence
  before opening a terminal session.
- **Mobile Conversation Loading Cache Included**: this release carries the
  server-side mobile conversation page cache, reducing repeated
  provider-native transcript parsing during mobile refreshes.
- **Mobile Project Activity Improved**: server project activity and mobile
  status payloads better reflect provider pane state, running jobs, and stale
  terminal output recovery.
- **Release Surface Synchronized**: VERSION, package.json, mobile app version
  metadata, update links, README variants, and release workflow defaults are
  aligned for 8.0.15.

## v8.0.14 (2026-07-04)

### Mobile Runtime Polish

- **Pairing QR Scanner Safety Restored**: `ccb update mobile` now keeps the
  full pairing payload and scanner-safe quiet zone in both managed and direct
  mobile gateway paths.
- **Running Project Attention Strengthened**: CCB Mobile now uses brighter
  card-level working highlights and recency-aware project ordering for active
  agents.
- **Working Reply Readability Preserved**: active reply bubbles keep the normal
  readable interior surface while showing working state through border/glow
  treatment.
- **Conversation Pages Cached**: server-side mobile conversation paging caches
  provider-native pages to reduce repeated Codex rollout parsing for large
  agents such as `lead`.
- **Release Surface Synchronized**: VERSION, package.json, mobile app version
  metadata, update links, and the mobile release manifest are aligned for
  8.0.14.

## v8.0.13 (2026-07-03)

### Mobile Pairing QR

- **Pairing QR Restored For Managed Mobile Update**: `ccb update mobile` now prints the pairing QR when reusing the host-wide background mobile gateway instead of only printing the pairing code.
- **Pairing QR Display Tightened**: the terminal QR keeps the original full JSON payload but removes the extra quiet-zone border so it takes less space without changing app-side QR semantics.
- **Mobile App Link Updated**: `ccb update mobile`, README links, package metadata, and the mobile release manifest now point to the 8.0.13 APK.

## v8.0.12 (2026-07-03)

### Release CI Portability

- **macOS Socket Test Fixed**: mobile host registry tests now bind their
  temporary Unix sockets under a short `/tmp/ccb-sock-*` path so macOS CI does
  not fail on `AF_UNIX path too long`.
- **Mobile App Link Updated**: `ccb update mobile`, README links, package
  metadata, and the mobile release manifest now point to the 8.0.12 APK.

## v8.0.11 (2026-07-03)

### Release CI And Mobile APK

- **CI Smoke Roots Fixed**: tag and main test workflows now pass explicit
  `CCB_TEST_ROOTS` for temporary dynamic-layout smoke projects, matching the
  hardened `ccb_test` source-test boundary.
- **Mobile App Link Updated**: `ccb update mobile`, README links, package
  metadata, and the mobile release manifest now point to the 8.0.11 APK.

## v8.0.10 (2026-07-03)

### Release Metadata

- **Release Workflow Default Updated**: the release-artifacts workflow manual
  default tag now follows the package version so full CI release checks pass.
- **Mobile App Link Updated**: `ccb update mobile`, README links, package
  metadata, and the mobile release manifest now point to the 8.0.10 APK.

## v8.0.9 (2026-07-03)

### Mobile Gateway And Conversation Refresh

- **Mobile Update Reconnect Stabilized**: `ccb update mobile` now reuses a
  healthy host-wide mobile gateway, refreshes stale pairing handoffs without
  restarting the gateway, and returns instead of occupying the foreground.
- **Lead Conversation Refresh Fixed**: CCB Mobile keeps the visible agent
  selected across ProjectView refreshes, so large native Codex transcripts such
  as `ccb_mobile / lead` keep loading and refreshing without drifting to the
  active tmux pane.
- **Provider Status Improved**: Claude provider runtime status reporting is
  available to the mobile and project-view surfaces alongside the existing
  Codex paths.
- **Mobile App Link Updated**: `ccb update mobile`, README links, package
  metadata, and the mobile release manifest now point to the 8.0.9 APK.

## v8.0.8 (2026-07-01)

### Mobile Status And Transcript Polish

- **Running Output Highlighted**: CCB Mobile now highlights the active terminal-derived output bubble when an agent is working, while avoiding stale historical reply markers.
- **Conversation Timestamps Preserved**: submitted messages, native transcripts, comms fallback, and job-history fallback records now keep stable `sent_at` / `completed_at` / duration metadata through refreshes.
- **Codex Runtime Status Stabilized**: source-side Codex session and pane status handling now reports interrupted turns explicitly and collapses stale no-progress display state without hiding raw diagnostics.
- **Mobile Source Synchronized**: the Flutter app source under `mobile/app` is updated from the active mobile worktree, including scanner, theme, notification, file, transcript, and chat interaction fixes.
- **Release Surface Synchronized**: VERSION, package.json, README mobile links, mobile app version metadata, and release notes are aligned for 8.0.8.

## v8.0.0 (2026-06-27)

### CCB Mobile Monorepo Release

- **Mobile Source Joined The Main Repository**: the Flutter CCB Mobile app now
  lives under `mobile/` alongside the core CLI, ccbd, and mobile gateway
  source.
- **Android APK Published**: the release includes
  `ccb-mobile-v8.0.0.apk` plus SHA256 and manifest assets for Android Alpha
  installation and validation.
- **Server-Wide Mobile Gateway Promoted**: mobile setup now targets
  server-wide project discovery, authenticated pairing, route diagnostics,
  pane-native text input, transcript rendering, terminal access, and
  image/document upload and download.
- **Tailnet Onboarding Unified**: `ccb update mobile` guides Tailscale setup,
  keeps the gateway loopback-only, uses Tailscale Serve instead of Funnel, and
  avoids storing Tailscale tokens or modifying ACL/grants.
- **Release Surface Synchronized**: VERSION, CLI version constants,
  package.json, mobile app version metadata, README release notes, mobile
  documentation, launcher icon assets, and npm packaging metadata are aligned
  for 8.0.0.

## v7.7.0 (2026-06-27)

### Runtime Accelerator Release Hardening

- **Runtime Accelerator Sidecar Shipped**: release artifacts now build and
  package `bin/ccb-runtime-accelerator`, so the default Codex accelerator path
  is available to installed users instead of silently falling back to Python.
- **Short Socket Fallback Added**: accelerator sockets now relocate to the
  per-user runtime socket root when a project-local Unix socket path would
  exceed platform limits.
- **Callback And Binding Stability Hardened**: pending callback edges keep the
  dispatcher in the hot loop, and Codex binding scan signatures now follow the
  managed session root.
- **Regression Evidence Expanded**: full Python regression, Rust sidecar gates,
  release preview, Codex soak, Claude callback, and mixed-provider integration
  evidence are recorded in the runtime accelerator review notes.
- **Release Surface Synchronized**: VERSION, CLI version constants,
  package.json, Rust crate metadata, release workflow defaults, README release
  notes, and npm packaging metadata are aligned for 7.7.0.

## v7.6.19 (2026-06-26)

### Long-Running Ask Wait Policy

- **Ask Heartbeat Timeout Disabled By Default**: running-job heartbeat
  observations now remain internal diagnostics by default instead of
  terminalizing ordinary `ask` jobs as `incomplete/heartbeat_timeout`.
- **Pane-Backed Provider Timeouts Made Opt-In**: Codex, Claude, and Gemini
  default `no_terminal_timeout_s` values are now `0.0`; explicit reliability
  timeout policies remain supported.
- **Long Runtime Evidence Recorded**: a source-runtime ask smoke ran beyond 30
  minutes, stayed running at 1800s and 1860s, then completed at 1980s with
  `result_message` and no `heartbeat_timeout`/`incomplete` evidence.
- **Release Surface Synchronized**: VERSION, CLI version constants,
  package.json, release workflow defaults, README release notes, and npm
  packaging metadata are aligned for 7.6.19.

## v7.6.18 (2026-06-26)

### CCB UI Theme Preference

- **Global Theme Command Added**: `ccb theme` now shows or changes the global
  CCB UI theme, including `+` and `-` cycling across dark and light palettes.
- **Light Tmux And Sidebar Themes Added**: CCB-owned tmux status, pane borders,
  sidebar colors, and activity/status indicators now have readable light-mode
  styling for light terminal backgrounds.
- **Rich WezTerm Theme Sync Added**: generated rich WezTerm profiles read the
  same CCB theme preference and export matching tmux/sidebar theme variables.
- **Release Surface Synchronized**: VERSION, CLI version constants,
  package.json, release workflow defaults, README release notes, and npm
  packaging metadata are aligned for 7.6.18.

## v7.6.17 (2026-06-25)

### Codex Log Symlink Target Repair

- **Codex Temp Log Symlink Repaired**: managed Codex startup now recreates the
  `logs_2.sqlite` symlink target parent when `/tmp/ccb-codex-logs-*` cleanup
  removes it between launches.
- **Bad Symlink Fallback Hardened**: if the symlink target cannot be repaired,
  CCB removes the broken symlink and restores the local backup before Codex
  starts, avoiding startup failures in Codex-owned SQLite initialization.
- **Regression Coverage Added**: focused tests cover missing temp target
  parents and preserve the existing diagnostic-log redirect and restore paths.
- **Release Surface Synchronized**: VERSION, CLI version constants,
  package.json, release workflow defaults, README release notes, and npm
  packaging metadata are aligned for 7.6.17.

## v7.6.16 (2026-06-23)

### Codex SQLite Migration Recovery

- **Codex Log Redirect Migration Fixed**: managed Codex `logs_2.sqlite`
  redirect now leaves Codex-owned SQLite schema creation to Codex itself and
  waits for `_sqlx_migrations` before installing the CCB diagnostic insert
  trigger.
- **Bad Intermediate Temp DBs Repaired**: temporary log databases left by the
  bad intermediate policy are detected when `logs` exists without a successful
  Codex migration record, backed up, and recreated by Codex on the normal
  migration path.
- **Diagnostic Fallbacks Preserved**: diagnostics restore and symlink fallback
  behavior remain covered while the default path avoids precreating Codex's log
  schema.
- **Release Surface Synchronized**: VERSION, CLI version constants,
  package.json, release workflow defaults, README release notes, and npm
  packaging metadata are aligned for 7.6.16.

## v7.6.15 (2026-06-23)

### Codex Diagnostics And Sidebar Focus

- **Codex Diagnostic Logs Redirected**: managed Codex homes now redirect
  `logs_2.sqlite` diagnostic writes to a temporary database by default and
  block diagnostic log inserts, while diagnostics mode restores the original
  database behavior for troubleshooting.
- **Codex Diagnostic Fallback Hardened**: when the temporary SQLite symlink
  path cannot be installed, CCB falls back to the in-place diagnostic trigger
  path instead of failing startup.
- **Sidebar Cross-Window Focus Fixed**: sidebar agent clicks now first select
  the target tmux window and then select the pane, preserving pane-id fallback
  when window metadata is missing.
- **Release Surface Synchronized**: VERSION, CLI version constants,
  package.json, release workflow defaults, README release notes, and npm
  packaging metadata are aligned for 7.6.15.

## v7.6.14 (2026-06-23)

### Mobile Gateway Alpha And Codex Diagnostics

- **Mobile Gateway Alpha Added**: authenticated pairing, focus routes, terminal
  open/resume/history routes, websocket terminal frames, public route metadata,
  and device revocation commands now provide the first mobile control surface.
- **Sidebar Layout Extended**: canonical rendering now keeps sidebar topology
  and presentation fields in one `[ui.sidebar]` table, accepts legacy
  `[ui.sidebar.view]` compatibility input, and supports `position = "right"`
  for right-side sidebar placement.
- **Multiple Local Agents Per Role Fixed**: local agents can share one Role Pack
  role id while retaining distinct agent names and runtime identities.
- **Codex Diagnostic SQLite Churn Reduced**: managed Codex homes now install a
  stable diagnostic log filter that drops TRACE/DEBUG rows by default, keeps
  INFO/ERROR rows, avoids unnecessary trigger recreation, and can be disabled
  with `CCB_CODEX_DIAGNOSTIC_LOGS=1`.
- **Release Surface Synchronized**: VERSION, CLI version constants,
  package.json, release workflow defaults, README release notes, and npm
  packaging metadata are aligned for 7.6.14.

## v7.6.13 (2026-06-22)

### Provider Profile Overlay Fixes

- **Codex Plugin Overlay Precedence Fixed**: Codex plugin overrides now apply
  in the intended order of inherited source config, `provider_profile.plugins`,
  then `CCB_CODEX_PLUGIN_OVERRIDES_JSON` / `CCB_CODEX_PLUGIN_OVERRIDES` env
  overrides.
- **Codex Stub Config Plugin Overlays Fixed**: Codex agents without an
  inherited `config.toml`, or with config inheritance disabled, now still
  materialize `provider_profile.plugins` into the managed `config.toml`.
- **Claude Profile MCP Without Source Trust Fixed**: Claude
  `provider_profile.mcp_servers` now materializes even when the source
  `.claude.json` does not exist, and `enabled = false` clears stale managed MCP
  servers from the agent trust file.
- **Callback Continuation Guarded**: callback continuations now keep the
  upstream finalization target explicit, and inherited ask skills warn agents
  not to answer a callback continuation before the upstream result is available.
- **Release Surface Synchronized**: VERSION, CLI version constants,
  package.json, release workflow defaults, README release notes, and npm
  packaging metadata are aligned for 7.6.13.

## v7.6.12 (2026-06-18)

### Claude MCP And Hook Inheritance

- **Claude MCP Projection Added**: managed Claude agents now inherit source
  Claude Code MCP configuration from `.claude.json`, including global
  `mcpServers` and current project/workspace MCP server state.
- **Claude Hook Inheritance Fixed**: managed Claude settings now merge inherited
  source-home hooks with CCB-managed finish/activity hooks so user-installed
  Claude Code hook tools stay visible after agent restart.
- **Workspace Mapping Kept Scoped**: project-level MCP state is mapped only onto
  the current managed workspace/project key; unrelated source project records
  are not copied into the managed Claude home.
- **Secret Handling Hardened**: managed Claude `.claude.json` now classifies as
  secret provider state because inherited MCP definitions may carry environment
  variables or auth-adjacent launch material.
- **Release Surface Synchronized**: VERSION, CLI version constants,
  package.json, release workflow defaults, README release notes, and npm
  packaging metadata are aligned for 7.6.12.

## v7.6.11 (2026-06-18)

### Layout Percent And Codex MCP Overlays

- **Layout Percent Tokens Added**: layout leaves may now use an optional
  `@N` suffix, such as `agent1:codex@30`, to request explicit sibling pane
  split percentages while preserving the old even-split default when omitted.
- **Codex MCP Overlays Added**: `provider_profile.mcp_servers` can now define
  source-controlled per-agent Codex MCP server overlays. Same-name servers
  override inherited Codex config; different names are additive.
- **Codex Hook Projection Hardened**: managed Codex home materialization now
  preserves trusted command hooks, including Hindsight Codex hooks, while
  continuing to filter arbitrary root hooks.
- **Sidebar And Trace Diagnostics Improved**: sidebar Comms/Tips panels gain
  better scrolling and resize behavior, and `ccb trace` exposes reply artifact
  evidence for empty or forced artifact replies.
- **Release Surface Synchronized**: VERSION, CLI version constants,
  package.json, release workflow defaults, README release notes, and npm
  packaging metadata are aligned for 7.6.11.

## v7.6.10 (2026-06-17)

### Z.ai Provider Support

- **Z.ai CLI Provider Added**: CCB now includes `provider = "zai"` as an
  optional managed native CLI provider with visible `zai --directory` panes and
  per-job `zai --prompt` execution.
- **Native Completion Boundary Preserved**: Z.ai jobs terminalize on subprocess
  exit plus assistant stdout extraction from JSONL output, without requiring
  model-printed `CCB_DONE`.
- **Provider Surface Integrated**: `ZAI_START_CMD`, provider session/pathing,
  storage classification, deterministic test stubs, focused execution tests,
  and README/provider lists now include Z.ai.
- **Release Surface Synchronized**: VERSION, CLI version constants,
  package.json, release workflow defaults, README release notes, and npm
  packaging metadata are aligned for 7.6.10.

## v7.6.9 (2026-06-17)

### Kimi And AGY Provider Reliability

- **Kimi Completion Evidence Hardened**: Kimi execution now records receipt,
  no-captured-output diagnostics, trace, and resume metadata so missing replies
  and recovered turns are easier to diagnose.
- **AGY Delivery Reliability Improved**: AGY prompt delivery now waits for
  ready evidence, handles pane fallback and ambiguous tmux send outcomes, and
  reports coalesced request diagnostics more clearly.
- **Provider Trace Diagnostics Expanded**: dispatcher, mailbox trace, and text
  artifact paths now expose the provider diagnostics needed to investigate
  Kimi/AGY delivery and completion edge cases.
- **Release Surface Synchronized**: VERSION, CLI version constants,
  package.json, release workflow defaults, README release notes, and npm
  packaging metadata are aligned for 7.6.9.

## v7.6.8 (2026-06-17)

### Role Pack Current Store

- **Single Current Role Store**: Role Pack runtime lookup now follows the
  installed current role package under `.roles/installed/<role-id>/current`;
  legacy `versions/<version>/<digest>` stores remain compatibility input, not
  runtime authority.
- **Project Role Locks Demoted**: `.ccb/role-lock.json` is no longer written or
  used to pin role resolution. Existing lock files are treated as legacy
  diagnostics and no longer suppress role memory or skills.
- **Role-Aware Restart Guard**: provider launch session files now record role
  id, version, and digest. `ccb restart <agent>` fails explicitly when the
  launched digest differs from installed current, avoiding a misleading resume
  of an old provider conversation.
- **Release Metadata Patch Fixed**: release artifact builds now patch version,
  commit, and date metadata in `ccb.py` after the bash launcher split.
- **Release Surface Synchronized**: VERSION, CLI version constants,
  package.json, release workflow defaults, README release notes, and npm
  packaging metadata are aligned for 7.6.8.

## v7.6.7 (2026-06-17)

### Rich Workbench Closure

- **Rich Launcher Closure**: plain `ccb` and `ccb rich` now start the
  CCB-managed rich WezTerm launcher unless already running inside that managed
  rich session; ordinary external WezTerm sessions no longer suppress rich
  auto-start.
- **Runtime Launcher Pinning**: command entrypoints now share the `_ccb-python`
  launcher so installed and source runtimes use a consistent interpreter.
- **Default Self Window Updated**: the built-in default config keeps `ccb_self`
  in its own window with the `claude` provider while preserving the rich-only
  optional workbench model and avoiding ordinary default Neovim tool windows.
- **Release Surface Synchronized**: VERSION, CLI version constants,
  package.json, release workflow defaults, README release notes, and npm
  packaging metadata are aligned for 7.6.7.

## v7.6.6 (2026-06-16)

### Role Store Home Pinning

- **Managed Provider Role Lookup Fixed**: CCB now pins role store resolution
  outside managed provider homes so provider session `HOME` rewrites no longer
  make `agentroles.*` lookups fall back to `.ccb/agents/.../provider-state/.../home/.roles`.
- **Role Store Diagnostics Improved**: missing role errors now include the
  resolved role store path, making provider-home drift visible when diagnosing
  role installation or host-adapter startup issues.
- **Release Surface Synchronized**: VERSION, CLI version constants,
  package.json, release workflow defaults, README release notes, and npm
  packaging metadata are aligned for 7.6.6.

## v7.6.5 (2026-06-16)

### Rich WezTerm IME

- **Rich WezTerm IME Fixed**: generated rich WezTerm profiles now enable IME
  support and derive `xim_im_name` from `XMODIFIERS`, allowing X11 fcitx/ibus
  input methods to connect when users type Chinese or other IME-backed text.
- **Workbench IME Environment Normalized**: generated `ccb-workbench` wrappers
  preserve user-provided input-method variables, and otherwise detect
  `fcitx5`, `fcitx`, or `ibus-daemon` before launching WezTerm, filling
  `XMODIFIERS`, `GTK_IM_MODULE`, and `QT_IM_MODULE` only when unset.
- **Release Surface Synchronized**: VERSION, CLI version constants,
  package.json, release workflow defaults, README release notes, and npm
  packaging metadata are aligned for 7.6.5.

## v7.6.4 (2026-06-16)

### macOS Release Install Smoke

- **macOS Install Smoke Fixed**: CI release-install smoke now explicitly opts
  into installing a temporary release prefix with a temporary sibling
  `CODEX_BIN_DIR`, matching the hardened temporary install guard introduced in
  7.6.3 without weakening user-facing installer safety.
- **Release Surface Synchronized**: VERSION, CLI version constants,
  package.json, release workflow defaults, README release notes, and the macOS
  install smoke workflow are aligned for 7.6.4.

## v7.6.3 (2026-06-16)

### macOS CI Green Patch

- **macOS Temporary Roots Fixed**: install-time temporary-prefix guards now
  recognize the canonical `${TMPDIR:-/tmp}` parent in addition to `/tmp`,
  `/private/tmp`, `/var/tmp`, and `/dev/shm`, matching GitHub Actions macOS
  runner paths under `/private/var/folders/...`.
- **Doctor Temporary Detection Fixed**: doctor runtime checks include the
  resolved `tempfile.gettempdir()` root so temporary ccbd implementations are
  diagnosed consistently across Linux and macOS `/tmp` symlink behavior.
- **Release Surface Synchronized**: VERSION, CLI version constants,
  package.json, release workflow defaults, README release notes, and the macOS
  CI compatibility tests are aligned for 7.6.3.

## v7.6.2 (2026-06-16)

### Rich Workbench Hotfix

- **Rich Layout Alias Fixed**: `.ccb/ccb.config` can now use `rich` as a
  tool/layout alias without requiring a provider runtime. The alias is
  materialized as a managed tool pane/window and remains outside `ask`
  routing.
- **Rich Auto-Start Added**: after `ccb update rich` installs and enables the
  bundle, plain `ccb` can launch the rich workbench by default outside an
  existing rich/WezTerm session while avoiding recursive WezTerm launches.
- **Rich Disable/Uninstall Added**: `ccb uninstall rich`, `ccb rich uninstall`,
  and `ccb rich disable` remove or disable rich mode without changing the
  normal full `ccb uninstall` path.
- **Legacy Editor Cleanup Hardened**: rich updates clean CCB-owned legacy
  standalone editor roots and links while leaving user-owned editor
  installations and personal config untouched.
- **Release Surface Synchronized**: VERSION, CLI version constants,
  package.json, release workflow defaults, README release notes, and focused
  rich workbench tests are aligned for 7.6.2.

## v7.6.1 (2026-06-16)

### Rich Workbench Binary Packaging

- **Yazi Bundle Download Added**: `ccb update rich` now downloads CCB-owned
  Yazi/ya release binaries where supported and places them under the managed
  workbench `bin` directory before falling back to system package managers.
- **Linux Musl Preference Added**: Linux x86_64/aarch64 rich installs prefer
  official Yazi musl builds before GNU builds, avoiding newer glibc
  requirements on older stable distributions.
- **Binary Validation Hardened**: downloaded `yazi` and `ya` must pass
  `--version` validation before activation; invalid managed binaries are
  removed so package-manager fallback remains possible.
- **WSL WezTerm Routing Added**: rich launchers under WSL can use
  Windows-native `wezterm.exe` while keeping CCB, Yazi, and preview helpers
  running inside the current Linux distribution.
- **Release Surface Synchronized**: VERSION, CLI version constants,
  package.json, release workflow defaults, README release notes, and rich
  workbench plan-tree evidence are aligned for 7.6.1.

## v7.6.0 (2026-06-15)

### Rich Workbench Lifecycle

- **Rich Workbench Bundle Added**: rich workbench components now install and
  update through the explicit `ccb update rich` entrypoint instead of ordinary
  CCB install/update paths.
- **Standalone Neovim Provisioning Removed**: normal `install.sh install`,
  `ccb update`, and public `ccb tools ... neovim` routes no longer provision a
  standalone Neovim tool. They now direct users to `ccb update rich`.
- **Rich Launch Tightened**: `ccb rich` launches only an installed and enabled
  rich bundle, preventing implicit provisioning from the launch path.
- **tmux Status Simplified**: CCB returns to a single-line tmux status bar and
  removes the old second-line copy hint.
- **Release Surface Synchronized**: VERSION, CLI version constants,
  package.json, release workflow defaults, README release notes, and rich
  workbench documentation are aligned for 7.6.0.

## v7.5.3 (2026-06-14)

### Kimi Runtime Reliability And Hindsight Compatibility

- **Kimi Pane Fallback Added**: Kimi can now use stable pane evidence for K2.7
  Code when the native turn log does not expose a completed reply in time,
  without changing other provider execution paths.
- **Kimi Hindsight Bridge Added**: Kimi can recall and retain Hindsight memory
  at the CCB execution boundary when `.hindsight/kimi.json`,
  `.hindsight/codex.json`, `HINDSIGHT_API_URL`, or `HINDSIGHT_BANK_ID` is
  configured. Hindsight failures remain non-blocking diagnostics.
- **Hindsight Token Compatibility Fixed**: both `HINDSIGHT_API_KEY` and
  `HINDSIGHT_API_TOKEN` are accepted by the Kimi bridge and the
  `scripts/hindsight` helper.
- **Codex Hindsight Hook Inheritance Scoped**: managed Codex homes may inherit
  only allowlisted Hindsight hooks from `.hindsight/codex/scripts/`, preserving
  CCB-managed activity hooks and filtering unrelated root hooks.
- **README Release Surface Updated**: English and Chinese README files document
  the Kimi/Hindsight behavior and keep the public supported-provider surface
  aligned with the 7.5 line.

## v7.5.2 (2026-06-13)

### Native CLI Provider Wave

- **Provider Strip Expanded**: English and Chinese README provider strips now
  include Qwen, Cursor, GitHub Copilot, Crush, Kiro, and Pi, and the public
  provider badge count is updated to 14 CLI families.
- **Next-Wave Native Providers Added**: CCB registers optional provider ids
  `qwen`, `cursor`, `copilot`, `crush`, `kiro`, and `pi` with runtime specs,
  session bindings, simple tmux launchers, command overrides, deterministic
  stubs, and focused execution coverage.
- **Native Completion Preserved**: Qwen, Cursor, Copilot, and Pi parse
  structured JSON/stream-json events, while Crush and Kiro use subprocess exit
  plus stdout. Pi terminalizes on native `turn_end`; none of these adapters
  require model-printed `CCB_DONE`.
- **Release Surface Synchronized**: VERSION, CLI version constants,
  package.json, release workflow defaults, README release notes, and native
  provider plan-tree evidence are aligned for the 7.5.2 patch release.

## v7.5.1 (2026-06-13)

### MiMo Provider Release Surface

- **MiMo Public Provider Surface**: English and Chinese README provider strips
  now include MiMo with a Xiaomi-branded badge, and the public positioning is
  updated from seven to eight CLI families.
- **MiMo Run-Mode Hardened**: CCB now runs MiMo ask jobs through
  `mimo run --pure --format json`, avoiding external plugin/tool-call
  intermediate steps that can consume simple CCB ask jobs before final text.
- **MiMo Completion Parsing Fixed**: MiMo `tool-calls` step-finish events are
  treated as intermediate evidence until final assistant text or process exit,
  while normal `step_finish` / `part.reason=stop` still completes as
  `mimo_run_stop`.
- **Release Surface Synchronized**: VERSION, CLI version constants,
  package.json, release workflow defaults, README release notes, and native
  provider plan-tree evidence are aligned for the 7.5.1 patch release.

## v7.5.0 (2026-06-13)

### Native CLI Providers And Homepage Sync

- **Native CLI Provider Expansion**: Kimi and the DeepCode-backed `deepseek`
  adapter now have managed tmux launchers, runtime specs, provider registry
  integration, session bindings, command overrides, and cleanup coverage.
- **Native Completion Detection**: Kimi, DeepCode/DeepSeek, and Antigravity
  now bind on `CCB_REQ_ID` and complete from provider-owned session, snapshot,
  or transcript evidence instead of requiring model-printed `CCB_DONE`.
- **Provider Diagnostics Hardened**: Kimi `TurnEnd` empty replies,
  DeepCode completed-empty replies, DeepCode `permission_denied`, and AGY
  missing-anchor/timeout paths now surface explicit incomplete or failed
  diagnostics.
- **Kimi Auto-Permission Compatibility**: CCB now injects Kimi's current
  `--auto-approve` flag for auto-permission while recognizing legacy/alias
  flags such as `--auto`, `--yes`, `-y`, and `--yolo` as explicit user input.
- **README Homepage Synchronized**: English and Chinese README homepages now
  share the same agent-parity positioning, v7 UI tour shape, refreshed hero
  assets, and seven public CLI-family strip.

## v7.4.4 (2026-06-12)

### Claude End-Turn And npm Release Surface

- **Claude `end_turn` Terminalization Fixed**: Claude pane-backed asks that
  emit a primary assistant response with `stop_reason=end_turn`, observed
  request anchor, and non-empty reply now produce normalized
  `TURN_BOUNDARY(reason=assistant_end_turn)` evidence and complete promptly
  instead of waiting for the 900-second reliability timeout.
- **Session-Boundary Empty Reply Guard Added**: empty session-boundary terminal
  events with no prior assistant reply now finish as
  `incomplete/task_complete_empty_reply` with `empty_provider_reply`
  diagnostics, matching the existing protocol empty-reply policy.
- **npm Release Surface Restored**: the source tree now includes the
  `@seemseam/ccb` package manifest, npm CLI runner wrappers, and a
  tag-triggered Trusted Publishing workflow that waits for GitHub release
  artifacts before publishing the npm package.
- **README v7 Homepage Refreshed**: public README files now use the canonical
  v7 hero assets, npm-first install wording, and stronger `ccb_self`
  positioning as CCB's built-in usage, config, diagnostics, and recovery
  expert.

## v7.4.3 (2026-06-12)

### PR #225 Reliability Follow-Up

- **Claude Launcher Contract Restored**: inline `--settings` now stays equal
  to the materialized settings overlay instead of injecting provider env into
  settings JSON, preserving the existing launcher contract after PR #225.
- **Claude WSL Env Forwarding Fixed**: Windows executable launches from WSL
  now mark only path variables with `/p` in `WSLENV` and forward
  `ANTHROPIC_AUTH_TOKEN`, `ANTHROPIC_API_KEY`, and `ANTHROPIC_BASE_URL` as raw
  environment values.
- **AGY Resume Lookup Hardened**: Antigravity conversation metadata lookup now
  accepts SQLite `bytes`, `str`, and `memoryview` values, and falls back to
  `--continue` if resume lookup fails instead of hard-failing launcher startup.
- **Runtime Regression Coverage Added**: launcher tests cover the restored
  Claude settings contract, WSL API env forwarding, and AGY resume fallback
  behavior.

## v7.4.2 (2026-06-12)

### Self-Supervision And Empty Reply Guards

- **Self-Supervision Evidence Hardened**: maintenance heartbeat now uses
  bounded provider-runtime snapshots, project-view activity evidence, suspicion
  envelopes, and a self-first diagnosis path for CCB runtime health signals.
- **Empty Provider Replies Terminalized**: Claude/Gemini empty hook replies,
  Codex protocol `task_complete` empty replies, and AGY pane done-marker empty
  replies now finish as `incomplete` with diagnostics instead of looking like
  successful empty completions.
- **Silence And Callback Semantics Preserved**: `--silence` success remains a
  valid completed no-reply path, callback parent `callback_pending` empty
  replies remain legal, and abnormal silent completions stay diagnosable.
- **Role Pack Refresh Tightened**: default Role Pack install and project role
  lock refresh paths now handle `agentroles.archi` and `agentroles.ccb_self`
  more consistently during update/startup.

## v7.4.1 (2026-06-11)

### Maintenance Heartbeat And ccb_self Defaults

- **Maintenance Heartbeat Runner Hardened**: adds the project-scoped
  maintenance heartbeat runner, schedule handling, activation suppression, and
  diagnostics evidence paths while keeping heartbeat disabled unless explicitly
  enabled.
- **ccb_self Default Alignment**: blank-project built-in config now includes
  `ccb_self:codex` bound to canonical `agentroles.ccb_self`, and install/update
  provisioning refreshes the recommended CCB self role without changing
  existing custom project configs.
- **Role Identity Compatibility Fixed**: CCB source now matches the
  `agent-roles-spec` catalog identity `agentroles.ccb_self` and only treats
  `agentrole.ccb_self` as legacy input compatibility.
- **Config Authority And Prompt Delivery Hardened**: generated config guidance
  keeps one role-binding authority, Role Pack hook paths use the correct CCB
  binary/project root, and Codex prompt delivery acceptance guards reduce false
  completion.
- **ccb_self Expert Knowledge Docs Added**: adds the CCB self expert manual,
  plan-tree decisions, and test coverage for the role's expert reference and
  communication recovery guidance.

## v7.4.0 (2026-06-10)

### ccb_self Maintenance Role

- **CCB Self Role Added**: `agentroles.ccb_self` is the dedicated CCB
  self-maintenance role for runtime diagnosis, guarded recovery, chain repair,
  CCB configuration ownership, and single-agent restart assistance.
- **Private ccb-config Ownership**: full `ccb-config` now lives inside the
  `ccb_self` Role Pack instead of being inherited by every managed agent.
- **Recommended Role Provisioning**: install/update Role Pack provisioning now
  installs or refreshes recommended default roles, including
  `agentroles.ccb_self`, by default.
- **Project Adoption Guidance**: README guidance now strongly recommends adding
  `agentroles.ccb_self:codex` to CCB projects that should have a dedicated
  maintenance assistant while keeping project topology changes explicit.

## v7.3.8 (2026-06-08)

### AGY Adapter And Project Tmux History

- **AGY Execution Adapter Added**: Antigravity (`agy`) now has a
  `pane_quiet` execution adapter with protocol parsing, command dispatch,
  polling, and supporting docs, so it can participate as a managed CCB
  provider without relying on the older launcher path.
- **Project Tmux History Preserved**: CCB-managed project tmux sessions now
  apply a 50000-line scrollback history limit alongside the existing mouse and
  vi key policies, including project namespace creation/reuse and detached
  runtime fallback paths.
- **Tmux Policy Contract Updated**: startup and layout contracts now state that
  project-owned sessions reapply `history-limit 50000`, `mouse on`,
  `mode-keys vi`, and related bindings after the authoritative session exists.
- **Claude Settings Launch Hardened**: Claude startup now passes inline
  `--settings` JSON when possible, preserving non-ASCII source paths that can
  fail when only a settings file path is passed through the provider CLI.

## v7.3.7 (2026-06-08)

### Ask Parameter Policy And Skill Guidance

- **Ask Skills Use Result Intent First**: inherited Claude, Codex, and Droid
  ask skills now choose `--silence`, `--compact`, `--artifact-reply`, or plain
  `ask` from the desired result shape first, then add `--chain` only for
  active parent dependency chains and artifact request/io flags only when exact
  content preservation is needed.
- **Artifact Transport Guidance Tightened**: ask skill guidance now separates
  task relationship flags from content-preservation flags, treats automatic
  long-message spill as a fallback, and discourages conflicting combinations
  such as `--silence --artifact-reply`.
- **README Ask Quick Reference Added**: English and Chinese README Agent
  Collaboration sections now include a compact ask-parameter selection table
  for silence, compact, artifact reply/request/io, callback, and plain ask.
- **Ask Parameter Policy Plan Added**: the plan tree now documents the ask
  parameter matrix, callback/silence boundaries, artifact transport policy,
  decisions, roadmap, and validation history.
- **Skill Template Tests Expanded**: ask skill template tests now assert the
  result-intent decision card, artifact policy, command shapes, diagnostics
  guardrails, and no-Chinese drift across inherited templates.

## v7.3.6 (2026-06-07)

### Provider Memory Ownership Cleanup

- **Provider Memory Ownership Policy Added**: Claude, Codex, and OpenCode
  managed contexts now exclude provider-native project memory from the CCB
  generated bundle, preventing duplicate project instructions when those
  providers already load their own native memory files. Gemini keeps the
  previous inclusion behavior pending a separate provider audit.
- **Provider User Memory Filtered**: provider user memory is filtered only at
  the provider-user-memory source layer, removing legacy CCB install marker
  blocks and old collaboration sections without rewriting user-owned files.
- **Shared Memory Template Simplified**: the default `.ccb/ccb_memory.md`
  template is updated to v5 and no longer repeats the Ask Communication block
  already supplied through CCB managed agent memory.
- **Seed-Aware Shared Memory Migration Added**: CCB upgrades only old,
  unedited generated shared-memory templates; edited project memory is
  preserved and not overwritten.
- **Claude Route-Mode Install Hardened**: route-mode installs no longer write
  `~/.claude/rules/ccb-config.md`. Install/uninstall remove only CCB-marked
  legacy external config files and preserve unmarked user files.
- **Source Runtime Tmux UI Import Cycle Fixed**: tmux UI version detection no
  longer imports management-runtime version helpers, keeping source-checkout
  startup paths import-safe under `ccb_test`.

## v7.3.5 (2026-06-07)

### Tmux Border Hook Hotfix

- **Release Path Hook Fixed**: `config/ccb-tmux-on.sh` now resolves stable
  installed config scripts through `CODEX_INSTALL_PREFIX` or `PATH` before
  falling back to the current release tree, preventing long-lived tmux hooks
  from storing temporary `/tmp/ccb-v...-release.../config/ccb-border.sh`
  paths.
- **Border Hook Guarded**: Python runtime hook generation now writes
  `after-select-pane` with `run-shell -b` plus a `[ -x script ] || exit 0`
  guard, so stale script paths do not repeatedly report
  `ccb-border.sh ... returned 127` while switching panes.
- **Post-Update Tmux UI Refresh Added**: `ccb update` now best-effort refreshes
  active tmux UI hooks with `set_tmux_ui_active(True)`, allowing users upgrading
  from v7.3.4 to automatically rewrite bad hooks without turning UI refresh
  failures into Role Pack provisioning failures.
- **v7.3.4 Withdrawn**: v7.3.4 remains published only as a prerelease/withdrawn
  build because it can persist temporary release paths into tmux hooks. Use
  v7.3.5 or newer as the stable upgrade target.

## v7.3.4 (2026-06-07)

### Withdrawn Prerelease

- v7.3.4 was withdrawn because its tmux border hook could persist temporary
  release paths such as `/tmp/ccb-v...-release.../config/ccb-border.sh` into
  long-lived sessions, causing pane clicks to report `returned 127` after the
  temporary build directory disappeared. Use v7.3.5 or newer.
- **Archi Role Tooling Simplified**: `agentroles.archi` now uses the global
  `@seemseam/archi` npm package as the single Architec tool source. CCB no
  longer tries to split Hippo, llmgateway, editable git checkouts, or managed
  pip/venv installs into separate Archi dependencies.
- **Archi Doctor Aligned With Bundled Capabilities**: `ccb roles install`,
  `ccb roles update`, and `ccb roles doctor agentroles.archi` now report and
  validate the npm package, the `archi` CLI, and its bundled Hippo/llmgateway
  capabilities instead of CCB-managed Python tool internals.
- **Legacy `ccb-arch` Forwarder Updated**: `bin/ccb-arch` forwards directly to
  `archi` when available and otherwise prints the required
  `npm install -g @seemseam/archi` command.
- **Sidebar Focus Restart Fixed**: sidebar-driven focus changes no longer
  restart agent panes unnecessarily, preserving live provider state while still
  allowing explicit pane restarts for refresh workflows.
- **Guarded Source Test Entrypoint Added**: `ccb_test` provides a guarded
  source-checkout entrypoint for isolated development validation without
  modifying or shadowing the installed CCB runtime.
- **OpenCode Autoupdate Disabled In Managed Panes**: generated `opencode.json`
  now sets `autoupdate = false`, and managed OpenCode launches include
  `OPENCODE_DISABLE_AUTOUPDATE=true` so CCB-owned panes do not self-update
  under the workspace runtime.
- **Config Skill Refined**: inherited `ccb-config` skills now support
  config-only operation, follow the user's language more consistently, fix YAML
  description quoting, and group menu/config guidance more clearly.
- **Sidebar Refresh Uses Pane Restart**: sidebar refresh guidance now prefers
  restarting panes, keeping UI state changes explicit and recoverable.
- **Config Designer Planning Added**: the plan tree now includes the local
  config-designer UI roadmap, decisions, and open questions.
- **Layout And Antigravity Updates Included**: this release carries the main
  branch `@percent` layout split token support and Antigravity lifecycle/zombie
  cleanup updates.

## v7.3.3 (2026-06-06)

### Withdrawn Draft

- v7.3.3 was withdrawn before stable rollout because it carried a sidebar
  focus/refresh regression. It is not the recommended release and should not be
  used for upgrades; use v7.3.5 or newer.

## v7.3.2 (2026-06-05)

### First-Install Role Pack Provisioning Hotfix

- **Blank Install Role Pack Provisioning Fixed**: release and source installs
  in a completely blank environment now recover when the initial
  `ccb roles update agentroles.archi` reports that the role is not installed.
  The installer falls back to `ccb roles install agentroles.archi`, allowing
  the first install to provision `.roles/installed` and the `ccb-archi`
  wrapper automatically.
- **Existing Install Refresh Preserved**: existing installations still use the
  update path first, so already-installed Role Packs continue to refresh in
  place without forcing a reinstall.
- **Install Prompt Copy Aligned**: optional Role Pack provisioning skip text now
  refers to install rather than update, matching the blank-install path.
- **v7.3.1 Superseded For First Installs**: v7.3.1 remains a published release
  but has a known first-install Role Pack provisioning bug in blank
  environments. Use v7.3.2 as the recommended stable release.

## v7.3.1 (2026-06-05)

### Agent Roles, Artifact Ask, And Shared Workspace Release

- **Agent Roles Store Flow Simplified**: CCB now uses the external Agent Roles
  manager as the only Role Pack writer and reads installed roles only from
  `.roles/installed`. The old CCB-owned writer and
  `CCB_AGENT_ROLES_MANAGER` rollback switch were removed.
- **Legacy Role Store Is Migration-Only**: existing `$XDG_DATA_HOME/ccb/roles`
  snapshots are copied into `.roles/installed` at management boundaries, but
  runtime lookup no longer falls back to the legacy store.
- **Role Add Aligned With Agent Roles**: `ccb roles add` now auto-installs
  missing system-source roles through `agent-roles --path`, preserving
  `ccb.archi` compatibility while writing canonical `agentroles.archi`
  bindings and locks.
- **Ask Artifact Transport Added**: `ask` and `ccb ask` now support
  `--artifact-request`, `--artifact-reply`, and `--artifact-io` so large or
  explicitly artifact-backed request/reply bodies can be stored as CCB text
  artifacts while the visible message carries the file path, byte count, and
  digest.
- **Callback Artifact Replies Preserved**: daemon-managed artifact replies work
  with callback continuations, letting child agents return long evidence through
  artifact paths without requiring agent-authored sentinel files.
- **OpenCode Clear Timing Fixed**: `ccb clear` adds an OpenCode-only submit
  delay after restoring old sessions, fixing cases where `/clear` appeared in
  the pane but was not submitted automatically.
- **Managed Neovim Runtime Path Preserved**: managed Neovim activation now
  links or wraps the extracted original binary instead of copying the bare
  executable, so Neovim can still resolve its relative `../lib/nvim/runtime`
  tree and LazyVim health checks pass.
- **Claude Root Startup Fixed**: managed Claude startup under root now includes
  the required sandbox environment and permission bypass flags
  (`IS_SANDBOX=1` and `--dangerously-skip-permissions`) while keeping the root
  install confirmation safeguards from v7.2.2/v7.2.3.
- **Shared Workspace Controls Added**: `.ccb/ccb.config` supports
  `workspace_path` for explicitly pointing an agent at an external worktree and
  `workspace_group` for sharing an internal grouped worktree across multiple
  agents.
- **Provider Command Templates Added**: agent config supports
  `provider_command_template`, where `{command}` expands to CCB's normal
  provider startup command after resume/session logic has been applied. This
  allows wrappers such as environment assignments or extra flags before/after
  the provider command without replacing the resume flow.
- **Config Skill Updated**: inherited `ccb-config` skills now default to a
  windows-first topology discussion, proactively clarify key choices, and cover
  Agent Roles, shared workspaces, and provider command templates.
- **Ask Skill Guidance Tightened**: inherited `ask` skills now make submit-only
  behavior, callback-only nested ask, artifact modes, and diagnostics-only
  `ask get`/`pend`/`watch`/`ping` commands explicit.
- **Source Development Isolation Preserved**: project memory and release
  guidance keep source checkout changes isolated from the system-installed CCB;
  development runtime testing should use `ccb_test` from external test projects
  such as `test_ccb2`.
- **Test Gates Stabilized**: Role Pack tests now use a fake Agent Roles CLI that
  does not require `tomllib`, `tomli`, or `toml` in the subprocess interpreter,
  and inherited ask skill template checks remain stable across line wrapping.
- **WSL Root Test Assertions Stabilized**: Claude command tests that verify
  ordinary non-root command tails now force the mocked root detector to false,
  keeping remote WSL/root runners from appending root compatibility flags to
  non-root expectations.
- **v7.2.x Hotfixes Included**: this release also carries the v7.2.x fixes for
  Antigravity runtime follow-up, source checkout guards, WSL mounted-drive smoke
  roots, provider blackbox wait timing, Role Pack CI fixtures, post-update role
  migration, and release artifact dispatch.

## v7.3.0 (2026-06-05)

### Superseded Prerelease

- Superseded by v7.3.1 after the remote WSL Tests workflow exposed root-sensitive
  Claude command assertions. The GitHub release was kept as a prerelease and did
  not upload official Linux/macOS release artifacts.

## v7.2.12 (2026-06-04)

### Agent Roles Store Migration Release

- **Agent Roles Manager Default Enabled**: Role Pack install, update, and sync now use the external `agent-roles` package manager and write role payloads into the spec-owned `.roles/installed` store by default.
- **Legacy Store Migration Added**: existing `$XDG_DATA_HOME/ccb/roles` installed snapshots are copied into `.roles/installed` at Role Pack management boundaries without deleting the old store, preserving existing project lock digest resolution.
- **Path Update Aligned**: `ccb roles update --path ...` now also routes through the Agent Roles manager and writes `.roles/installed` instead of the legacy CCB store.
- **Sync Validation Hardened**: malformed `agent-roles sync --json` role rows now fail closed, while `ccb roles sync --with-tools` composes manager-owned payload sync with CCB-owned tool hook execution.
- **Role Config Guidance Updated**: inherited `ccb-config` skill docs now describe `.roles/installed` as the default package store while keeping `.ccb/ccb.config` limited to canonical role ids.

## v7.2.11 (2026-06-04)

### Superseded Agent Roles Opt-In Preview

- Superseded by v7.2.12 after the release direction changed from an opt-in `CCB_AGENT_ROLES_MANAGER=1` preview to a default-on Agent Roles manager migration. Do not use v7.2.11 as the recommended release.

## v7.2.10 (2026-06-04)

### Role Pack Post-Update Hotfix

- **Post-Update Handoff Fixed**: `ccb update` now installs the new release, smoke-checks the installed entrypoint, and delegates Role Pack plus Neovim provisioning to the newly installed `ccb __post-update` command instead of continuing with the old updater process.
- **Legacy Role Store Repair Added**: installed `ccb.archi` metadata is repaired under canonical `agentroles.archi`, and stale removed source paths fall back to the current catalog role source.
- **Forced Provisioning Failure Propagates**: `CCB_INSTALL_ROLES=1`, `CCB_INSTALL_NEOVIM=1`, and `CCB_POST_UPDATE_REQUIRED=1` now make post-update subprocess failures fail the parent update, while optional post-update provisioning remains a warning after the core update succeeds.
- **Role Config Guidance Aligned**: inherited `ccb-config` skill docs now generate `agentroles.archi` bindings and mention `ccb.archi` only as a legacy migration alias.

## v7.2.9 (2026-06-04)

### Agent Roles Catalog Release

- **Agent Roles Catalog Added**: CCB now consumes `agentroles.archi` from the external `agent-roles-spec` catalog instead of shipping production role content inside the CCB source tree.
- **Catalog Role Lifecycle Hardened**: `ccb roles list/install/update/sync/add/doctor` now works with catalog roles, installed-role metadata, project locks, digest pinning, and explicit re-add semantics.
- **Runtime Projection Preserved**: role memory, adapter memory, provider skills, and CCB adapter tool hooks project into managed Codex/Claude homes for `agentroles.archi`.
- **Update Flow Improved**: `install.sh install` and `ccb update` handle catalog role refreshes, report newly available roles, and keep non-interactive follow-up commands explicit.
- **Compatibility Alias Kept**: legacy `ccb.archi` inputs resolve to `agentroles.archi` while project config and locks use the canonical catalog role id.
- **Source Runtime Guard Fixed**: source checkout commands that pass `--project` now validate the target project against allowed test roots, restoring CCBD communication smoke checks launched from the source checkout.
- **Official Smoke Roots Fixed**: real-platform soak, fastpath, and storage cleanup smoke checks now pass their generated test roots through `CCB_SOURCE_ALLOWED_ROOTS`.
- **WSL Mounted Startup Smoke Fixed**: the main Tests workflow now passes the generated `/mnt/c/Temp` startup-smoke project through `CCB_SOURCE_ALLOWED_ROOTS`.
- **Provider Blackbox Wait Hardened**: the Claude restart blackbox test now waits for the running partial reply to be reflected before asserting it.
- **Role Pack CI Fixture Hardened**: Role Pack tests now use an isolated `agentroles.archi` preview fixture instead of requiring a sibling `agent-roles-spec` checkout in CI.

## v7.2.8 (2026-06-04)

### Superseded Role Fixture Hotfix

- Superseded by v7.2.9 after the release gate found that full GitHub Actions runners did not have the sibling `agent-roles-spec` checkout expected by Role Pack tests.

## v7.2.7 (2026-06-04)

### Superseded WSL Mounted Smoke Hotfix

- Superseded by v7.2.8 after the release gate found a provider blackbox timing race in the Claude restart partial-reply assertion.

## v7.2.6 (2026-06-04)

### Superseded Official Smoke Root Hotfix

- Superseded by v7.2.7 after the release gate found that the WSL mounted startup smoke in the main Tests workflow also needed its generated `/mnt/c/Temp` project in `CCB_SOURCE_ALLOWED_ROOTS`.

## v7.2.5 (2026-06-04)

### Superseded Source Runtime Guard Hotfix

- Superseded by v7.2.6 after the release gate found that official soak, fastpath, and storage cleanup smoke checks needed explicit generated test roots in `CCB_SOURCE_ALLOWED_ROOTS`.

## v7.2.4 (2026-06-04)

### Superseded Agent Roles Catalog Release

- Superseded by v7.2.5 after the release gate found that source checkout `--project` commands were rejected from the source cwd during CCBD real platform smoke checks.

## v7.2.3 (2026-06-03)

### Root Install Support Validation Hotfix

- **Root Install Support Preserved**: keeps the v7.2.2 root install confirmation gate, install identity metadata, and `ccb doctor` runtime identity diagnostics intact.
- **WSL Release Validation Fixed**: install metadata tests now explicitly simulate a non-root user when validating metadata quoting, so WSL runners that execute as root no longer fail the release matrix for environment-dependent reasons.

## v7.2.2 (2026-06-03)

### Root Install Confirmation Release

- **Root Install Confirmation Added**: `install.sh install` now refuses root execution by default, requires interactive `yes` confirmation, and requires `CCB_ALLOW_ROOT_INSTALL=1` for non-interactive root installs.
- **Uninstall Path Kept Ungated**: the root confirmation guard applies only to install operations and does not block uninstall cleanup.
- **Install Identity Metadata Added**: install metadata now records root status, install user, and sudo user details so future diagnostics can explain how CCB was installed.
- **Doctor Runtime Identity Diagnostics Added**: `ccb doctor` reports runtime user, owner, root state, and warns when root runs inside a non-root project.
- **Build Info Type Hygiene Fixed**: `read_build_info()` now returns `dict[str, object]`, resolving the non-blocking type hygiene issue found during architecture review.

## v7.2.1 (2026-06-02)

### Antigravity Runtime Follow-Up

- **Antigravity Runtime Specs Completed**: `agy` now has provider runtime/client specs, public provider-core exports, and `.agy-<agent>-session` naming alongside the backend registration added in v7.2.0.
- **Antigravity Launch Coverage Added**: tests now cover named Antigravity pane launch, `AGY_START_CMD`, auto-permission, restore continuation, and prepared-state compatibility.
- **Provider README Surface Aligned**: README and README_zh now show Antigravity in the top provider badge, comparison tables, and install prerequisites.
- **Reload No-Change Semantics Clarified**: docs and tests describe non-dry-run no-change reload as `noop` / `no_op` with no graph publish.
- **Agent Roles Planning Added**: plantree now records the future host-neutral `agent-roles` RolePack specification project plan.

## v7.2.0 (2026-06-02)

### Role Packs And Managed Tools Release

- **Role Pack UX Added**: `ccb roles add ccb.archi:codex` is now the primary project entry point; configs keep the shorthand `ccb.archi:codex`, while runtime resolves it to the local `archi` agent and projects role memory plus provider skills.
- **Bundled `ccb.archi` Role Added**: the first built-in Role Pack provides an Architec-backed architecture reviewer with role memory, Codex/Claude skills, and CCB-managed Architec tooling.
- **Role Dependency Flow Simplified**: `ccb roles install ccb.archi` and `ccb roles update ccb.archi` install or refresh both role assets and dependencies by default; `install.sh install` and `ccb update` prompt interactive users and show `ccb roles update ccb.archi` as the non-interactive follow-up.
- **Managed Tool Windows Added**: CCB config now supports `[tool_windows.<name>]` for non-agent windows such as the bundled `neovim` tool, including sidebar/project-view rows, reload add/remove support, and `ccb tools install/doctor neovim` provisioning.
- **Antigravity Provider Included**: the release includes the new `agy` / Google Antigravity CLI provider support already landed on `main`.

## v7.1.1 (2026-05-31)

### Sidebar View Height Release

- **Three Sidebar Heights Added**: `[ui.sidebar.view]` now supports `agents_height`, `comms_height`, and `tips_height` so projects can tune all native sidebar sections.
- **Default Sidebar Split Updated**: default native sidebar layout is now Agents `50%`, Comms `15%`, and Tips `35%`.
- **Project View And TUI Synced**: config parsing, project view payloads, reload planning, and the Rust sidebar TUI now carry and apply `tips_height` alongside the existing section heights.
- **Reload Remount Reliability Fixed**: after dynamically unloading an agent, recreating an agent with the same name no longer fails on retired runtime authority residue; fully retired/stopped agent records and session files can be retained and inherited by the rebuilt agent.
- **CCB Config Skill Updated**: inherited Codex/Claude `ccb-config` source docs and references now expose all three sidebar view height parameters when generating or migrating windows topology.

## v7.1.0 (2026-05-30)

### Dynamic Reload Release

- **Config Reload Preview Added**: after editing `.ccb/ccb.config`, use `ccb reload --dry-run` to preview the daemon-side plan before changing tmux state.
- **Dynamic Reload Apply Added**: `ccb reload` can dynamically add agents, add windows, unload idle agents, and remove idle windows under the existing `ccbd` daemon without restarting unrelated panes.
- **Busy Changes Fail Closed**: busy/unsafe unloads, provider replacements, agent moves, and arbitrary reshapes are rejected without killing existing panes.
- **Reload-Pending Diagnostics Added**: config signature drift is surfaced as reload-pending state so users can explicitly review and apply safe changes instead of relying on daemon restart behavior.

## v7.0.11 (2026-05-28)

### Provider Activity And Sidebar Focus Release

- **Provider Activity Hooks Added**: CCB now records provider-native activity evidence from hook artifacts so the sidebar can distinguish active, pending, idle, and failed provider work more accurately than pane text alone.
- **Sidebar Activity Refresh Improved**: focus changes invalidate the cached project view and immediately refresh sidebar panes in the same project session, reducing visible stale status after mouse or focus actions.
- **Sidebar Click Latency Reduced**: tmux pane clicks return to the direct `select-pane -t = ; send-keys -M` binding instead of spawning the hidden sidebar click subprocess path for ordinary pane focus.
- **Namespace And Hook Runtime Hardened**: project namespace config, provider hook install settings, clipboard/runtime launch paths, and Codex managed trust handling were tightened together with focused regression tests.

## v7.0.10 (2026-05-27)

### Sidebar Tips And Tmux Controls Release

- **Sidebar Three-Panel Layout Added**: the native sidebar now keeps the agent tree, compact Comms, and Tips panels in a stable `1/3`, `1/4`, `5/12` vertical split.
- **Default Tips Expanded**: projects without custom sidebar tips now show a fuller tmux help list covering pane movement and resize, window switching, copy mode, paste, and help.
- **Sidebar Controls Preserved**: the top-right sidebar controls remain `↻` and `×`; `×` performs project-level `ccb kill`, while `q` and `Esc` exit only the sidebar pane.
- **Tmux Vim Controls Documented And Applied**: CCB-managed tmux keeps `mode-keys vi`, copy-mode `v`/`C-v`/`y`, `prefix+h/j/k/l` pane focus, and `prefix+H/J/K/L` pane resizing.
- **Sidebar View Config Added**: optional `[ui.sidebar.view]` config can tune tree height, compact Comms, and Tips text without changing managed topology or forcing namespace recreation.

## v7.0.9 (2026-05-26)

### README v7 Redesign Release

- **Public README Rebuilt**: redesigns `README.md` and `README_zh.md` around the v7 visible multi-agent workspace, with task-first positioning, multi-agent approach comparison, v7 UI tour, Quick Start, tmux basics, config examples, and install/update guidance.
- **README Visual Assets Added**: adds real v7 terminal screenshots under `assets/readme_v7/` for the English and Chinese README walkthroughs.
- **Planning Docs Preserved**: adds `docs/plantree/` planning notes covering the README v7 redesign decisions, roadmap, baseline docs, media plan, and publication choices.
- **Runtime Surface Preserved**: keeps the v7.0.8 runtime behavior and release fixes intact while refreshing the GitHub-facing documentation package.

## v7.0.8 (2026-05-25)

### Clear Context And Config Overlay Release

- **Agent Context Clear Command Added**: `ccb clear [agent...]` sends provider-native `/clear` to all or selected mounted agent panes without deleting project state or restarting runtimes, with `all` and unknown-agent validation handled through `ccbd`.
- **Pane Click Focus Fixed**: project tmux pane mouse clicks now use the correct `select-pane` action, restoring normal focus switching instead of emitting `command select-pane`.
- **Windows Overlay Config Parsing Corrected**: explicit `version = 2` `[windows]` topology is now the authoritative mounted-agent set, same-name `[agents.<name>]` tables act as overlays, stale unreferenced agent tables are ignored, and overlay providers must still match the window leaf provider.
- **CCB Clear Skill Added**: Claude and Codex inherited skills now include `ccb-clear`, installers project the skill, and Claude managed settings allow `Bash(ccb clear *)`.

## v7.0.7 (2026-05-25)

### Sidebar Controls And Width Sync Release

- **Sidebar Top Controls Expanded**: the native sidebar now exposes full refresh, in-place project pane restart, and exit actions directly from the title bar, with matching keyboard and tmux mouse bindings.
- **Pane Restart And Click Routing Added**: `project_restart_panes` and hidden `ccb __sidebar-click` paths now let sidebar actions refresh project panes and restore focus through `ccbd`.
- **Sidebar Width Sync Hardened**: sidebar widths now accept integer column sizes, drag-resize uses `resize-pane -M`, `after-resize-pane` syncs widths across windows, and the global `window-resized` hook reapplies the stored width for the active CCB session instead of learning tmux's temporary compression.

## v7.0.6 (2026-05-24)

### macOS Release Test Smoke Hotfix

- **macOS Release Install Smoke Fixed**: GitHub Tests now prebuild a host-runnable `bin/ccb-agent-sidebar` before simulating a release install from a source checkout, matching the packaged release shape.
- **v7.0.5 Hotfixes Preserved**: this release includes the Claude keychain service override, macOS `ccb update` sidebar helper preservation, and explicit sidebar rebuild failure handling from v7.0.5.

## v7.0.5 (2026-05-24)

### Claude Keychain And macOS Update Hotfix

- **Claude Keychain Override Added**: `CCB_KEYCHAIN_SERVICE_OVERRIDE` can bind managed Claude materialization to a specific macOS Keychain service, and control-plane environment handling preserves the override.
- **macOS Update Preserves Sidebar Helper**: `ccb update` staging now skips line-ending normalization for binary files, so `bin/ccb-agent-sidebar` is not corrupted during macOS updates.
- **Sidebar Rebuild Failure Made Explicit**: installers now require a Rust toolchain when the sidebar helper must be rebuilt locally, instead of silently continuing with a missing rebuild path.

## v7.0.4 (2026-05-23)

### Project View Refresh And Runtime Hardening Release

- **Project View Refresh Optimized**: sidebar/project view responses now reuse short-lived cached responses, use bounded tail reads for recent jobs, and avoid repeated tmux pane captures during a single view build.
- **Runtime State Reads Hardened**: job, message-bureau, and JSONL stores gained targeted latest/tail lookup helpers so comms and sidebar state do not need broad scans of growing runtime files.
- **Keeper And Startup Robustness Improved**: keeper lifecycle checks now verify process command lines against the project root, and daemon/socket lifecycle paths include additional ownership and stopping-state safeguards.
- **Inherited Skill Install Cleanup Improved**: `ccb-config` is now the canonical inherited skill name, legacy `ccb_config` and obsolete helper skills are removed silently during install, and useful tool packages were refreshed.

## v7.0.3 (2026-05-23)

### macOS Sidebar Universal Binary Hotfix

- **macOS Sidebar Runs Natively**: the macOS release artifact now builds `ccb-agent-sidebar` for both `x86_64-apple-darwin` and `aarch64-apple-darwin`, then combines them with `lipo` into the shipped `bin/ccb-agent-sidebar` universal binary.
- **macOS Release Gate Added**: release artifact CI now inspects the macOS helper with `file`, requires `universal binary`, and runs the helper `--help` smoke before uploading assets.
- **macOS Test Smoke Extended**: GitHub Tests now build the macOS release preview with both Apple targets and verify the packaged helper instead of only checking that the tarball exists.

## v7.0.2 (2026-05-23)

### Codex Trust And Sidebar Compatibility Hotfix

- **Codex Managed Trust Fixed**: managed Codex homes now trust both the project root and active workspace path through `[projects."..."] trust_level = "trusted"`, and auto-permission startup uses native `--ask-for-approval never --sandbox danger-full-access` flags instead of an invalid top-level trust override.
- **Linux Sidebar Assets Made More Compatible**: release artifact and standalone sidebar helper workflows now build Linux binaries on Ubuntu 22.04 so published helpers do not require newer `GLIBC_2.39` hosts.
- **Sidebar Install Recovery Hardened**: installers smoke-test existing and prebuilt `ccb-agent-sidebar` binaries before trusting them, rebuild locally with cargo when needed, and source wrappers now resolve symlinks before locating the repo target binary.
- **Sidebar Activity And Layout Follow-Up**: project sidebar status now reflects active, queued, stale, callback-waiting, and provider-background activity more accurately, while sidebar width handling preserves the agent grid area.

## v7.0.1 (2026-05-23)

### Sidebar Release Packaging Hotfix

- **macOS Checksum Portability Fixed**: `bin/package-ccb-agent-sidebar-release` now writes sidebar artifact SHA256 files with `sha256sum`, macOS `shasum -a 256`, or a `python3` fallback, restoring macOS GitHub Tests for the v7 release line.

## v7.0.0 (2026-05-23)

### Native Sidebar Control Release

- **Native CCB Sidebar Added**: adds the Rust `ccb-agent-sidebar` helper with per-window project view, fixed gray sidebar identity, colored provider/runtime activity status, mouse/keyboard focus switching, and release packaging hooks.
- **Comms Tracking Split From Agent Activity**: top agent rows now reflect real provider pane/runtime activity, while the bottom Comms section remains the CCB ask/job tracking and recovery surface.
- **Window Topology Config Added**: `ccb_config` docs and skills now cover `version = 2` `[windows]` syntax; explicit windows mount multiple named tmux windows with sidebar panes.
- **Legacy Config Compatibility Preserved**: compact and hybrid configs without `[windows]` remain single business-window layouts and keep existing `cmd` semantics.
- **Terminal And Install Compatibility Hardened**: includes Ghostty/tmux `TERM` normalization, tmux environment/mouse fixes, source wrapper handling, release sidebar binary packaging, and Codex legacy root-only session migration into private home sessions.

## v6.2.9 (2026-05-22)

### Callback Visibility And Diagnostics Release

- **Callback Root Replies Are Visible**: delegated callback root jobs now show `callback_pending` while the child chain is still running, then `ask get` and `watch` surface the final message-bureau reply after the continuation completes.
- **Ask Observer Commands Marked Diagnostics-Only**: inherited ask skills, CLI help, project/runtime memory surfaces, and tests now describe `ask get`, `pend`, `watch`, and `ping` as explicit debugging tools, not normal ask workflow steps.
- **Long CCB Text Artifacts Added**: oversized ask bodies, terminal replies, notices, and callback continuation text spill to bounded UTF-8 artifacts under `.ccb/ccbd/artifacts/text/`, with previews and diagnostics bundle coverage.
- **Shutdown Cleanup Hardened**: remote kill now tracks both prepared and current control-plane pids, and foreground tmux exit can best-effort request project stop-all so an exited namespace does not leave the backend looking active.

## v6.2.8 (2026-05-21)

### Config Source, Stop Cleanup, And Tmux Policy Release

- **Config Source Fixes Included**: the current release package includes explicit config source kinds for built-in defaults, user `~/.ccb/ccb.config`, and project `.ccb/ccb.config`, with project config taking highest priority.
- **Kill Cleanup Ordering Included**: `stop_all` defers project tmux namespace destruction until after the socket response finalizer so `ccb kill` and `ccb kill -f` can complete cleanup from inside a CCB pane.
- **Managed Tmux Policy Follow-Up Added**: isolated managed tmux sessions now explicitly enable CCB-owned `mouse on` and `set-clipboard on` policy in project namespaces and detached tmux paths.

## v6.2.7 (2026-05-21)

### Config Source And Stop Cleanup Release

- **Three-Layer Config Sources Added**: config resolution now reports explicit source kinds for built-in defaults, user config at `~/.ccb/ccb.config`, and project config at `.ccb/ccb.config`, with project config taking highest priority.
- **Config Validate Output Clarified**: `ccb config validate` now surfaces `config_source_kind` and `used_builtin_default`, and README/docs/`ccb_config` skill guidance describe the active config layer instead of assuming only project config.
- **Kill Cleanup Ordering Fixed**: `stop_all` now defers project tmux namespace destruction until after the socket response finalizer, so `ccb kill` and `ccb kill -f` launched from a CCB tmux pane can finish daemon cleanup before their pane is destroyed.

## v6.2.6 (2026-05-20)

### Tmux Isolation And Startup Hardening Release

- **CCB Tmux Uses Isolated Config By Default**: managed tmux commands now run with `tmux -f /dev/null ...`, with `CCB_TMUX_CONFIG` available for explicit managed overrides, so user `~/.tmux.conf` plugins and hooks cannot alter CCB pane topology.
- **Source Install Startup Path Hardened**: source/dev installs now use a Python wrapper, honor `CCB_PYTHON_BIN`, run post-install entrypoint smoke checks, and keep Droid MCP registration bounded by a timeout.
- **Provider Startup Reliability Improved**: restore-fresh behavior takes effect correctly and Claude managed homes write trust state while preserving the accepted `--permission-mode bypassPermissions` auto-permission path.
- **Ask Removed-Flag Surface Kept Simple**: removed wait-alias migration guidance remains absent from the current ask parser and tests.

## v6.2.5 (2026-05-19)

### Claude Managed Memory De-Duplication Hotfix

- **Claude Project Memory De-Duplicated**: managed `.claude/CLAUDE.md` bundles no longer copy the project-level `CLAUDE.md`, allowing Claude to load it natively from the working directory.
- **Managed Memory Sources Preserved**: provider user memory from real `~/.claude/CLAUDE.md`, `.ccb/ccb_memory.md`, and per-agent `.ccb/agents/<agent>/memory.md` still project into the managed Claude home.
- **Project Memory Loader Flag Added**: `load_memory_sources(..., include_provider_native_project=False)` can now skip provider-native project memory while the default behavior still includes it for existing callers.

## v6.2.4 (2026-05-18)

### Codex Managed Config TOML Hotfix

- **Codex Managed Config Rendering Hardened**: managed Codex `config.toml` inheritance now renders dict values as inline TOML tables, preserving parsed inline-table arrays without crashing on `unsupported TOML value type: dict`.
- **Fallback Feature Merge Fixed**: when no TOML reader is available, managed Codex fallback copy now updates an existing `[features]` section instead of appending duplicate sections, and it stops correctly at both `[table]` and `[[array_of_tables]]` boundaries.
- **Installer TOML Parser Dependency Added**: Linux/macOS and Windows installers now auto-install `tomli>=2.0.0` when no `tomllib`/`tomli`/`toml` reader is available, support `CCB_INSTALL_TOMLI=0`, and install `tomli` inside the managed venv before optional watchdog.

## v6.2.3 (2026-05-18)

### Architecture Hotspot Optimization Release

- **Release Checker Split Into Focused Modules**: the GitHub release checker now keeps the CLI entrypoint small while moving local state, Markdown, GitHub, workflow, and asset checks into dedicated helper modules.
- **Provider Memory Projection Shared**: Codex, Claude, Gemini, and OpenCode memory projection now share provider-core helpers for projection events, markers, signatures, and bundle materialization while preserving provider-specific behavior.
- **Startup Update Flow Simplified**: startup update handling is split into state, refresh, and flow modules so update checks and install refresh logic are easier to review and test.
- **Storage Classification Boundary Extracted**: provider-home cleanup classification now has a dedicated module with direct tests for provider precedence and unknown-provider handling.
- **Architecture Plan Captured**: the optimization roadmap, decisions, and post-phase Architec results are recorded under `plans/architecture-optimization/`.

## v6.2.2 (2026-05-18)

### Codex Managed Home Migration Prompt Hotfix

- **Codex External Migration Prompt Disabled In Managed Homes**: managed Codex `config.toml` now forces `[features].external_migration = false` so managed panes do not stop on an interactive migration prompt.
- **Inherited Config Preserved**: source-home Codex config, model/API settings, and existing feature flags are still inherited; only the managed-home external migration prompt is disabled.
- **Fallback Copy Path Hardened**: when TOML parsing is unavailable, the copied managed config still gets a managed `[features]` override for `external_migration = false`.

## v6.2.1 (2026-05-18)

### Inherited CCB Config Skill Release

- **Inherited `ccb_config` Skill Added**: Claude and Codex installs now inherit a `ccb_config` skill for designing `.ccb/ccb.config`, choosing roles/providers/worktree layout, and updating shared plus per-agent CCB memory.
- **Inherited Skill Layout Consolidated**: CCB-owned inherited skills now live under `inherit_skills/`; optional `useful_tools/` remain user-installable tools and are not inherited by default.
- **Ask Guidance Simplified**: injected ask reply guidance is shorter, English-only in source text, skips nested-routing instructions in every ask body, and recognizes more explicit-output requests.
- **Project Memory Wording Simplified**: generated project/runtime memory now uses shorter submit-once guidance while keeping callback and silence routing rules available where they belong.
- **Config Memory Routing Clarified**: `ccb_config` memory patterns prefer direct owner-to-next-owner callback handoffs and separate root work packages for parallel chains, without claiming single-task multi-callback fan-in.

## v6.2.0 (2026-05-17)

### Callback Ask Chain Release

- **Callback Ask Chains Added**: `ccb ask --chain <agent>` lets an active agent delegate work whose result is needed before finishing the original task; CCB resumes the parent as a continuation task when the child reply is ready.
- **Nested Ask Guardrails Enforced**: plain nested `ask` from an active CCB task is rejected; use `--chain` for needed child results or `--silence` for independent no-result-needed work.
- **Durable Callback Routing Added**: callback edges persist parent/child routing state, repair crash windows, and support chained continuations across multiple agents.
- **Ask Skills And Memory Updated**: Claude, Codex, and Droid ask skills plus generated project memory now document callback delegation and stop-after-submit behavior.

## v6.1.21 (2026-05-17)

### Kill And Restart Cleanup Hotfix

- **Forced Kill Finalization Survives Client Disconnects**: `ccb kill -f` now still queues daemon finalization when the requesting pane disappears before the socket response is written.
- **Project-Scoped Kill Cleanup Hardened**: kill cleanup preserves full tmux socket paths, reads lifecycle owner/keeper PID authority, and scopes process fallback matching to CCB control-plane commands for the same project.
- **Stale Execution Residue Cleared**: ccbd startup and late provider updates now clear execution files for cancelled, completed, or missing jobs so `doctor` no longer reports stale work as active or recoverable authority.
- **Startup/Kill Contract Updated**: documents the shutdown finalizer, project-scoped process cleanup, tmux socket path, and stale execution-state cleanup requirements.

## v6.1.20 (2026-05-16)

### Claude Active Version Cache Release

- **Managed Claude Follows Source Active Version**: when the source home uses the standard Claude Code layout, CCB now detects `~/.local/bin/claude -> ~/.local/share/claude/versions/<version>/claude` and makes managed Claude use that active version.
- **Active Version Is Cached Safely**: the selected source-home Claude version is copied into the CCB provider cache, then managed `.local/bin/claude` points at the cached copy instead of selecting another version already present in shared cache.
- **Fallback Behavior Preserved**: if the source active-version layout is unavailable, Claude binary routing keeps the previous shared-cache behavior.
- **Provider Hook Routing Updated**: Claude provider workspace preparation now passes the source home into binary-cache routing so the active-version preference is applied during managed startup.

## v6.1.19 (2026-05-16)

### Managed Ask Skill Projection Release

- **Managed Ask Skills Project Across Providers**: Claude inherited `skills/` and `commands/` now use CCB projected assets instead of copy-sync, so managed agents inherit system-installed ask skills without duplicating user provider homes.
- **Droid Managed FACTORY_HOME Added**: Droid now gets a managed provider home with system `~/.factory/skills` projected into each managed Droid home, plus session-scoped Droid sessions rooted under that managed home.
- **Droid Session Readers Follow Managed Sessions**: Droid launch/session payloads, execution polling, and communicator state now use the session-scoped Droid sessions root so restart and session rotation stay bound to the managed session log.
- **Ask Replies Are Guided By Default**: `ccb ask` now injects concise reply guidance, adds `--compact` for distilled answers and `--silence` for silent-on-success asks.

## v6.1.18 (2026-05-15)

### Heartbeat Timeout And Useful Tools Release

- **Heartbeat Timeout Now Terminalizes Stalled Jobs**: running-job heartbeat observations stay internal until three no-progress intervals, then CCB emits one terminal `heartbeat_timeout` reply that recommends sending a small communication test before another large task.
- **Provider Reliability Progress Is Semantic**: cursor movement, polling timestamps, session snapshot rotation, and other reader bookkeeping no longer extend provider completion deadlines; only semantic progress refreshes reliability state.
- **Reliability State Survives Persistence**: provider runtime persistence preserves `reliability_*` fields so restored jobs keep their timeout deadline instead of resetting it.
- **Useful Tools Bundle Included**: release artifacts now include the packaged `useful_tools/useful_tools.zip` alongside the versioned optional tool tree.

## v6.1.17 (2026-05-15)

### Completion Binding And Codex Session Hotfix

- **Claude Completion Request Binding Fixed**: Claude Stop hooks now resolve the current outer `CCB_REQ_ID` from structured transcript/user prompt records, so forwarded text or tool output containing older request ids cannot write completion events to the wrong job.
- **Codex Session Resume Stabilized**: Codex memory projection fingerprint changes are now diagnostic freshness metadata, not conversation identity; restart no longer archives or resets the Codex session just because `.ccb/ccb_memory.md` changed.
- **Mailbox Stale Request Recovery Included**: merged PR #205 so terminal `task_request` queue heads can be discarded or acked when their attempt is already terminal, preventing mailbox queues from staying stuck in delivering.
- **Regression Coverage Added**: adds transcript req_id parsing, provider finish hook, Codex resume, and mailbox stale-head recovery coverage.

## v6.1.16 (2026-05-14)

### Memory Handoff And Claude Route Hotfix

- **Ask Handoff Guidance Stabilized**: generated managed-memory bundles now include CCB-owned submit-only ask coordination rules, so stale `.ccb/ccb_memory.md` text cannot reintroduce polling/waiting behavior after restart.
- **Project Memory Template Tightened**: new `.ccb/ccb_memory.md` files now describe `/ask` as a fire-and-forget handoff and avoid obsolete `ccb -h` guidance in the managed memory seed.
- **Claude ccswitch Route Inheritance Fixed**: managed Claude startup now prefers the source-home `~/.claude/settings.json` `ANTHROPIC_BASE_URL` over a stale caller-shell `ANTHROPIC_BASE_URL`, so ccswitch route changes take effect after `ccb kill && ccb`.
- **Claude Source Contract Documented**: the Claude isolation contract now records source settings as the route authority when no agent-specific profile URL is configured.

## v6.1.15 (2026-05-14)

### Kill Shutdown Reliability Hotfix

- **Remote Kill Fully Stops ccbd**: `ccb kill` now snapshots the active `ccbd` and keeper pids before remote `stop_all`, then waits for those exact control-plane processes to exit instead of trusting `phase=unmounted` alone.
- **Cleanup Works Immediately After Kill**: remote kill now finalizes lifecycle state to `phase=unmounted` / `desired_state=stopped`, so `ccb cleanup` can run right after `ccb kill` without requiring a second kill.
- **Orphan Runtime Cleanup Hardened**: lingering provider-runtime pid files and orphan process groups are still collected during kill finalization, with regression coverage for stale/new-generation pid races.

## v6.1.14 (2026-05-14)

### macOS Claude Keychain Boundary Follow-up

- **Keychain Fallback Contract Documented**: records the managed Claude `Library/Keychains` fallback as agent-local secret auth compatibility state
- **Diagnostics Boundary Clarified**: support bundles must not follow the fallback Keychains symlink, and storage diagnostics classify it as secret auth state

## v6.1.13 (2026-05-14)

### macOS Claude Keychain Fallback

- **Claude Keychain Fallback Added**: managed Claude homes on macOS now link `Library/Keychains` when `com.apple.security.plist` is absent, so official Claude login lookup still resolves the user's login keychain
- **Auth Cleanup Remains Symmetric**: disabling Claude auth inheritance removes both the projected Keychain preference and the fallback Keychains link
- **Storage Diagnostics Hardened**: `ccb doctor storage` now classifies the managed Claude `Library/Keychains` symlink as secret auth state instead of unknown out-of-bounds residue

## v6.1.12 (2026-05-13)

### Claude Tmux Permission Release

- **Claude Tmux Permission Prompt Fix Released**: packages the merged Claude auto-permission pane fix, using `--permission-mode bypassPermissions` plus `skipDangerousModePermissionPrompt` so tmux panes do not block on an unanswerable confirmation prompt
- **Cleanup Hardening Included**: carries forward the WSL cleanup smoke alignment and Claude rollback cache preservation from v6.1.11/v6.1.10

## v6.1.11 (2026-05-13)

### WSL Cleanup Smoke Alignment

- **WSL Storage Smoke Updated**: real-platform WSL cleanup validation now expects relocated mounted-drive projects to report shared cache as enabled, matching the current storage contract
- **Claude Cleanup Rollback Fix Included**: keeps the active Claude Code version plus one rollback version during `ccb cleanup`

## v6.1.10 (2026-05-13)

### Claude Cleanup Rollback Hotfix

- **Claude Rollback Cache Preserved**: `ccb cleanup` now keeps the active Claude Code version plus one rollback version while pruning older rebuildable version-cache entries
- **Real Platform Cleanup Smoke Restored**: storage cleanup behavior now matches the macOS and WSL real-platform smoke expectations for Claude current/rollback preservation and Gemini cache pruning

## v6.1.9 (2026-05-13)

### Storage Dedup And Shutdown Hardening

- **Provider Storage Footprint Reduced**: Codex projected assets now prefer symlinks/shared bundles, Claude version/cache cleanup handles shared cache locations, and Gemini rebuildable caches route through shared/external cache paths instead of piling up per agent
- **Cleanup Reclaims Runtime Residue**: `ccb cleanup` now prunes old Claude shared versions, Gemini shared cache content, rebuildable Claude caches, and stale pane crash logs while preserving session/auth authority
- **Kill Shutdown Reliability Hardened**: `ccb kill` now snapshots old `ccbd`/keeper pids before shutdown, waits for them to really exit, treats Linux zombies as dead, and avoids killing a newer backend generation
- **Claude Tmux Startup Stabilized**: Claude auto-permission launches now use `--permission-mode bypassPermissions` plus a settings overlay to skip the tmux-unanswerable bypass confirmation prompt

## v6.1.8 (2026-05-13)

### macOS Claude Keychain Preference Hotfix

- **Claude Keychain Preference Projection Fixed**: managed Claude homes on macOS now inherit `Library/Preferences/com.apple.security.plist`, preserving the default Keychain preference needed for Claude login lookup
- **Auth Isolation Preserved**: the preference file is projected only on Darwin and is removed when Claude auth inheritance is disabled

## v6.1.7 (2026-05-12)

### Codex Memory Freshness Hotfix

- **Codex Shared Memory Refresh Fixed**: Codex startup now records the managed `AGENTS.md` memory projection fingerprint and skips stale `resume` bindings when `.ccb/ccb_memory.md` changes
- **Ask Skill Submit Discipline Tightened**: Claude and Droid ask skills now require heredoc submission and stop immediately after submit, avoiding accidental polling or waiting

## v6.1.6 (2026-05-11)

### Startup And Claude Auth Hotfix

- **Start/Maintenance Race Fixed**: ccbd now prevents heartbeat maintenance from mutating project tmux panes while a start request is laying out and launching agents
- **Project Memory Anchor Tightened**: CCB no longer creates, imports, or depends on project-root `CCB.md`; `.ccb/ccb_memory.md` is the only shared CCB memory anchor
- **Claude macOS Login Inheritance Fixed**: managed Claude startup now checks the current `Claude Code-credentials` Keychain service before older service names

## v6.1.5 (2026-05-11)

### Tmux Startup Hotfix

- **Pane Startup Race Fixed**: project layout panes are now created with a silent placeholder in the initial tmux split, preventing fast-exiting shells from causing `Cannot split: pane ... does not exist` or `respawn pane failed: can't find pane`
- **Provider Launch Semantics Preserved**: agent panes still use the managed respawn path, preserving provider shell, stderr log, and `remain-on-exit` behavior
- **Tmux Regression Coverage Added**: tests now cover real tmux layout creation with an exiting `default-command` plus guardrails that keep provider commands off the structural split path

## v6.1.4 (2026-05-11)

### Project Shared Memory V1

- **Shared Project Memory Landed**: `.ccb/ccb_memory.md` is now the shared project memory anchor injected into managed Claude, Codex, Gemini, and OpenCode agents during startup
- **Per-Agent Memory Layer Added**: `.ccb/agents/<agent>/memory.md` now participates as an agent-private overlay on top of the shared project file
- **Provider Startup Contract Unified**: memory projection now runs through a single writer path with explicit launch context, stable workspace resolution, and fail-fast launch behavior across providers
- **Gemini Managed Memory Smoke Validated**: Gemini CLI 0.41.2 was real-smoke validated against managed `.gemini/GEMINI.md` loading via `GEMINI_CLI_HOME`

## v6.1.2 (2026-05-11)

### Provider Storage Boundary Hardening

- **Storage Audit Expanded**: `ccb doctor storage` now reports explicit storage classes for provider authority, sessions, secrets, workspaces, user content, projected config, rebuildable cache, and startup authority bundles
- **Safe Cleanup Added**: `ccb cleanup` now holds the project lifecycle lock, refuses active `ccbd` or pending ask jobs, prunes old Claude version caches conservatively, and removes only safe Gemini rebuildable caches
- **Diagnostics Bundle Hardened**: support bundles now include storage summaries while excluding provider secrets, Claude binary caches, Gemini rebuildable caches, and Codex startup bundles even when classification fails
- **Provider Runtime Boundaries Tightened**: non-Codex profile runtime-home overrides are rejected, Codex legacy profile homes migrate into managed provider state safely, and duplicate effective provider homes fail validation
- **Shared Cache Foundation Added**: future provider shared-cache roots now resolve through `PathLayout`, reject unsafe WSL drvfs placement without relocation, and create a versioned `MANIFEST.json`

## v6.1.0 (2026-05-09)

### CCBD Ask Stability And Observer Convergence

- **Ask Submit Fastpath Stabilized**: `ccb ask` now returns bounded receipts without waiting on provider readiness, mailbox history projection, or long maintenance ticks; real Linux fastpath stress validated 60 queued asks with p95 submit latency under 250ms
- **Lifecycle And Shutdown Races Closed**: stop-all, shutdown, and background supervision now respect lifecycle stopping state so stopped runtimes and terminal jobs are not revived by stale maintenance or recovery work
- **Provider Completion Recovery Hardened**: Codex polling now follows rebound session bindings after restart, so replies written to a new managed session log can still terminalize the original job
- **Mailbox Summary Read Model Landed**: queue, inbox, pend, and related observer views now prefer maintained mailbox summaries and explicitly degrade on missing/corrupt summaries instead of silently scanning full history on routine paths
- **Observer Surfaces Weakened**: `pend`, `watch`, `queue`, and `inbox` are documented and rendered as non-authoritative snapshots, reducing confusion between weak mailbox observations and lineage inspection
- **Real Platform Validation Added**: new GitHub Actions coverage runs macOS and WSL ccbd/ask smoke tests, communication matrix, short soak, and fastpath stress with stub providers; Linux local validation covered full pytest, comm matrix, soak, and fastpath stress

## v6.0.29 (2026-05-07)

### WSL Runtime State Relocation

- **Runtime State Moved Off Mounted Drives**: on WSL projects rooted under `/mnt/<drive>/...`, project authority remains in `.ccb` while `ccbd/` and agent runtime state relocate to a local Linux state root with explicit marker files
- **Diagnostics and Bundle Mapping Updated**: doctor output and support bundles now expose the project anchor, runtime-state root, relocation reason, and logical `.ccb` archive paths for relocated runtime files
- **Provider Lookup and Ask Routing Kept Stable**: relocated runtime directories still resolve back to the project anchor for session discovery and ask sender attribution without changing Linux or macOS default layout behavior
- **Runtime Markers Are Validated**: relocated runtime markers and refs now reject malformed or mismatched payloads, so stale relocation residue cannot silently remap one project to another
- **WSL Smoke Matches the Final Contract**: the release smoke now expects the runtime-root relocation path that the relocated project actually writes, instead of treating the first relocation step as the final socket fallback

## v6.0.28 (2026-05-07)

### WSL Control Plane Socket Hardening

- **WSL Control Plane Startup Hardened**: keeper and daemon readiness probes now share the configured control-plane RPC timeout instead of using shorter hardcoded budgets that could misread a slow mounted-drive startup as config drift
- **Socket Server Accept Path Decoupled**: ccbd now accepts connections separately from a serialized worker lane, so one slow or incomplete client request no longer blocks new control-plane probes or heartbeats
- **Transient Connect Retry Added**: Unix socket clients retry only short-lived connect races within the existing timeout budget, without retrying already-sent RPC requests or mutating operations
- **README Refreshed**: the public README was reorganized around the current agent CLI hub/team workflow and updated release guidance

## v6.0.27 (2026-05-06)

### macOS Foreground Attach Timeout Hardening

- **Foreground Attach Timeout Split**: interactive `ccb` startup now uses foreground-attach-specific RPC and target-ready budgets instead of reusing the short daemon probe timeout
- **macOS Attach Race Reduced**: foreground attach now tolerates slower post-start `ccbd` ping and tmux namespace/window visibility on macOS without redefining daemon startup success
- **Clearer Attach Failures**: attach errors now distinguish between an unresponsive control-plane ping and a responsive daemon whose project namespace is not yet attachable

## v6.0.26 (2026-05-05)

### macOS Install And Claude Ask Cleanup

- **macOS Release Install Fixed**: release installs now keep generated CLI wrappers bound to the managed `.venv` Python, avoiding environment drift when optional dependencies such as `watchdog` are installed
- **WSL Install Tests Unblocked**: watchdog install regression tests now explicitly confirm WSL non-interactive install mode so CI exercises the intended optional-dependency path
- **Claude Ask Prompt Slimmed Down**: managed Claude `ask` no longer injects local ask skill runtime text into the prompt body, so agent-to-agent asks stay limited to the request anchor and the user's original message

## v6.0.25 (2026-05-02)

### Gemini Managed Home Alignment

- **Gemini Login Inheritance Fixed**: managed Gemini panes now set `GEMINI_CLI_HOME` to the isolated home root so Gemini CLI reads projected `.gemini/.env`, settings, and login state from the intended managed boundary
- **Regression Coverage Added**: launcher tests now lock the aligned `HOME`, `GEMINI_CLI_HOME`, and `GEMINI_ROOT` contract and guard against nested `.gemini/.gemini` settings writes
- **Community Contact Trimmed**: README removed the standalone Linux.do contact entry while keeping the Linux.do community acknowledgement

## v6.0.24 (2026-05-02)

### WSL Official Login Transport

- **WSL Provider Transport Inherited**: managed provider panes now preserve user-session proxy, CA, browser, and WSL interop environment needed by official-login and Codex Apps/MCP networking paths
- **Managed Isolation Preserved**: transport inheritance is centralized and does not allow caller-global `CODEX_HOME`, `GEMINI_ROOT`, `CLAUDE_PROJECTS_ROOT`, or `CCB_CALLER_*` runtime authority to override agent-scoped managed state
- **Gemini Login Projection Extended**: managed Gemini homes now project allowlisted `.gemini/.env` API credentials, `google_accounts.json`, and `GEMINI_CLI_HOME` while diagnostics continue excluding copied auth artifacts
- **Opencode Session Detection Hardened**: opencode now treats env-session mode as active only when its provider-specific runtime env is present, avoiding stale generic `CCB_SESSION_ID` contamination
- **Community Entry Refreshed**: README now includes the refreshed WeChat group QR image and Linux.do community acknowledgement so users can find the current support channels from the public project page

## v6.0.23 (2026-05-01)

### CI Matrix Stabilization

- **Release CI Greened**: latest release validation now points at a commit whose full GitHub Actions test workflow passes across Ubuntu, macOS, WSL, and install smoke jobs
- **Provider Blackbox Coverage Focused**: heavy pane-backed provider restart / rotate / settle tests now run in a dedicated Ubuntu provider-blackbox job instead of being repeated across every OS and Python matrix cell
- **macOS Socket Test Race Fixed**: ccbd socket tests now wait for the daemon socket to answer ping requests before issuing RPCs, avoiding macOS runner readiness races

## v6.0.22 (2026-04-29)

### Claude macOS Login Inheritance

- **macOS Keychain Login Inherited**: managed Claude startup now reads official Claude Code login credentials from macOS Keychain and materializes an equivalent project-scoped `.claude/.credentials.json` inside isolated Claude homes
- **Claude Account Metadata Refreshed**: inherited `.claude.json` account metadata now refreshes from the source home while preserving managed workspace trust and excluding source workspace trust or API key secrets
- **Default Config Startup Fixed**: keeper startup now treats a missing `.ccb/ccb.config` as a request to use the built-in default project config instead of exiting before `ccbd` can mount
- **Regression Coverage Expanded**: tests now lock Keychain projection, metadata refresh, and disabled-auth cleanup paths for managed Claude login inheritance

## v6.0.21 (2026-04-28)

### Claude Hook Asset Projection

- **CodeIsland Hook Assets Inherited**: managed Claude startup now copies referenced source-home hook assets such as `.codeisland/` when inherited Claude hooks call `$HOME/.codeisland/...`, preventing missing-hook failures inside isolated Claude homes
- **Config Boundary Preserved**: third-party hook assets are copied only when Claude config inheritance is enabled and the inherited hook payload actually references that home-relative asset path
- **Diagnostics Redaction Extended**: diagnostic bundles now exclude copied `.codeisland/` provider-state assets while still including ordinary managed Claude settings for support

## v6.0.20 (2026-04-28)

### Claude Official Login Source Home Fix

- **Claude Official Login Source Home Fixed**: managed Claude startup now treats `.ccb/agents/*/provider-state/*/home` as an isolated runtime home, not the user's source home, so official browser-login credentials are copied from the real account home
- **Claude Credential Path Coverage**: managed Claude homes now project Claude Code official-login credentials from `.claude/.credentials.json` while retaining compatibility with `.config/claude-code/auth.json`
- **Regression Coverage Added**: tests now lock source-home fallback, launcher projection, diagnostics redaction, and workspace preparation for official Claude login inheritance

## v6.0.19 (2026-04-28)

### Claude Official Login Inheritance

- **Claude Official Login Projection**: managed Claude homes now project Claude Code official login credentials from `.claude/.credentials.json`, so browser-login-backed auth can be inherited into isolated CCB runtimes instead of only API-token-based settings auth
- **Managed Login Auth Retention**: when global Claude auth artifacts disappear but managed Claude state already holds a valid project-scoped login, startup now preserves that managed login auth across restart instead of silently dropping it
- **Auth Cleanup And Regression Coverage**: disabling auth inheritance now clears stale copied Claude login credentials, and targeted tests now lock the projection, cleanup, and launcher startup paths

## v6.0.18 (2026-04-28)

### Gemini Hook Empty-Reply Guard

- **Empty Gemini Hook Replies No Longer Burn Jobs**: managed Gemini `AfterAgent` hooks that fire with an empty reply now downgrade to `incomplete` instead of terminalizing as a false exact completion
- **Exact Hook Polling Becomes Safer**: Gemini exact-hook polling now ignores `completed` hook artifacts with no reply text, allowing observed session-stability or timeout reliability paths to converge the request instead of accepting a blank terminal result
- **Regression Coverage Added**: targeted tests now lock the empty-reply guard at both the finish-hook artifact writer and Gemini execution-service polling layers

## v6.0.17 (2026-04-28)

### Gemini Custom Endpoint Env Propagation

- **Gemini Endpoint Override Restored**: managed Gemini startup now preserves `GOOGLE_GEMINI_BASE_URL` end to end, so custom endpoint and proxy-backed Gemini CLI setups no longer fall back to Google's default production API host
- **Gemini Model Env Allowlisted**: control-plane and provider-profile env filtering now preserve `GEMINI_MODEL`, allowing isolated Gemini agents to keep explicit model selection instead of silently dropping it at startup
- **Config Shortcut Alignment**: Gemini `key` / `url` shortcuts now materialize the same environment variables the current Gemini CLI actually reads, keeping explicit config-based routes aligned with shell-level env behavior

## v6.0.16 (2026-04-27)

### Codex Plugin Projection & Cmd Shell Compatibility

- **Codex Plugin Projection Fixed**: managed Codex homes now project plugin-bundle authority under `.tmp/plugins/` and `.tmp/plugins.sha`, so isolated agents inherit marketplace and installed plugin assets coherently instead of starting with plugin-enabled config but missing bundles
- **Plugin Refresh Semantics Tightened**: startup now refreshes the managed plugin projection as one authority unit, removes stale managed plugin residue when the source projection disappears, and skips unnecessary recopies when the source `plugins.sha` marker is unchanged
- **Cmd Shell / Session Env Hardening**: the `cmd` pane now directly `exec`s the resolved user shell and preserves ordinary user-session transport variables such as `DISPLAY`, `WAYLAND_DISPLAY`, `DBUS_SESSION_BUS_ADDRESS`, `XAUTHORITY`, and `SSH_AUTH_SOCK`, improving fish/zsh and GUI-command compatibility

## v6.0.15 (2026-04-27)

### Codex Route Authority & Foreground Attach Polish

- **Codex Explicit Route Authority**: managed Codex homes now materialize agent-local `config.toml` and `auth.json` as the sole authority for explicit `key` / `url` routes, so agent-scoped API overrides replace inherited global provider routes instead of drifting back to system config
- **Codex Session Namespace Rotation**: managed Codex startup now fingerprints explicit route authority, stamps reusable session bindings with that authority, and rotates stale `sessions/` namespaces before launch when the bound route no longer matches
- **Foreground Attach UX Hardening**: interactive `ccb` startup now seeds tmux namespace creation from the real terminal viewport and issues a best-effort client refresh after attach so first paint matches the current terminal size without manual redraw

## v6.0.14 (2026-04-26)

### Claude Logout Recovery Hardening

- **Managed Claude Auth Preservation**: managed Claude homes now preserve agent-local login auth when the global Claude home has been logged out, so a project-scoped re-login survives restart instead of re-entering a browser-link loop
- **Auth Projection Semantics Tightened**: Claude startup still refreshes source auth when it exists, but stops treating missing source auth as an instruction to blank managed auth; disabled auth inheritance continues to clear stale copied auth state
- **Startup Regression Coverage Expanded**: targeted tests now lock this behavior at the projection layer, provider workspace preparation, and Claude launcher startup path

## v6.0.13 (2026-04-25)

### macOS Release Path & Preview Packaging Fix

- **macOS Release Path**: shared release artifact naming and updater resolution now cover the macOS universal bundle alongside Linux/WSL release assets
- **Source Dev Install Mode**: installs from a git checkout now stay linked to the live source tree, skip startup auto-update prompts, and can switch to a managed release install through `ccb update`
- **Agent API / Model Shortcuts**: `.ccb/ccb.config` now accepts flat per-agent `key`, `url`, and `model` shortcuts so common provider overrides stay concise
- **Preview Packaging Hardening**: preview release exports now exclude generated output paths inside the repo, fixing recursive self-copy failures such as `dist-macos-smoke`

## v6.0.12 (2026-04-24)

### Non-Blocking Startup Update Prompt

- **Cached Startup Update Prompt**: interactive foreground `ccb` start can now read install-scoped cached release metadata and offer an upgrade prompt only when a newer stable release is already known locally
- **Background Refresh Without Startup Stall**: cache misses or stale cache now schedule a background refresh with short network budgets instead of joining the project startup transaction
- **Prompt Deferral And Silence Controls**: users can upgrade immediately, continue and defer the prompt for the current version, or silence that exact version
- **Startup Contract Boundary Preserved**: startup supervision now explicitly treats release-update checks as advisory logic outside the lifecycle startup transaction

## v6.0.11 (2026-04-24)

### Project Startup Hotfix

- **Cold-Start Namespace Classification Fix**: project tmux namespace liveness now treats `no server running on <project socket>` as an absent namespace that should be created or recreated, instead of surfacing a false `failed to inspect tmux session` startup failure
- **Project Lifecycle Regression Coverage**: added backend/state regression tests for the absent-server cold-start path so real `ccb -> ping -> kill` lifecycle flows remain covered
- **Startup Contract Clarified**: the startup supervision contract now explicitly defines project-socket `no server running` as a namespace-absent signal during create/recreate decisions

## v6.0.10 (2026-04-24)

### Startup Budget Hardening & Gemini Login Inheritance

- **Gemini Login Auth Inheritance**: managed Gemini startup now projects `security.auth.selectedType` and `oauth_creds.json` for login-backed `oauth-personal` reuse, while stale copied credentials are removed whenever auth inheritance is disabled
- **Shared Tmux Ready Budget**: project-owned pane respawn now uses the same tmux object readiness retry budget as namespace create/reflow instead of a separate shorter timeout, reducing transient `no server running` failures during startup and supervision
- **Background Startup Compatibility**: background lifecycle startup preserves supervisor compatibility and keeps readiness-probe budgets separated from operational RPC timeouts
- **Diagnostics Credential Redaction**: support bundles now exclude Gemini `oauth_creds.json` together with other provider credential artifacts

## v6.0.9 (2026-04-23)

### Cross-Platform Lifecycle & Watch Stability

- **WSL Runtime Compatibility**: Unix socket placement and installer staging now avoid unsupported WSL mounted-drive paths, and tmux namespace readiness is retried more cleanly during startup
- **macOS Lifecycle Hardening**: lifecycle restore, startup timing, and project identity handling were tightened so macOS runs converge on the same authority model as Linux instead of flaking during startup or recovery
- **Respawn Resilience**: transient tmux fork, server-exit, and readiness failures are now retried at the runtime boundary instead of surfacing as spurious lifecycle breakage
- **Watch Reconnect Recovery**: `watch` and ask-wait flows can recover terminal results from persisted state after brief daemon loss while still honoring reconnect deadlines instead of hanging indefinitely
- **Cross-Platform Validation Expanded**: GitHub Actions now covers macOS install smoke and WSL compatibility paths together with the existing Linux test matrix

## v6.0.7 (2026-04-22)

### Lifecycle Authority & Shutdown Stability

- **Keeper-Owned Lifecycle Authority**: project lifecycle is now anchored around keeper-owned `lifecycle.json`, clearer generation ownership, and stricter namespace epoch authority
- **Mounted-State Read Path Fixes**: `ping ccbd` and `ping <agent>` now read mounted/runtime state from current authority instead of drifting to stale failure views after restart or recovery
- **Shutdown Transaction Hardening**: `ccb kill` and `ccb kill -f` now terminalize non-terminal jobs inside the same shutdown transaction so in-flight work cannot reappear as restore or auto-retry authority after restart
- **Real Blackbox Validation**: real-project lifecycle repro on `ask -> kill -f -> restart` now converges to `project_shutdown` with no lingering active execution

## v6.0.6 (2026-04-21)

### 🔒 Agent Isolation Stability & Foreground Kill Lifecycle

- **Foreground Kill Lifecycle Fix**: `ccb kill` no longer leaves interactive `ccb` reporting a false foreground-attach failure after the project tmux namespace is intentionally destroyed
- **Codex Session Isolation Contract Landed**: managed Codex startup now keeps agent-scoped session authority bound to the agent-owned managed home instead of ambient project or global provider state
- **Provider Control-Plane Isolation Tightened**: project-scoped control-plane processes now scrub inherited provider runtime markers more strictly so agent runtime identity does not leak into `ccb`, keeper, or `ccbd`
- **Agent Isolation Stability**: restart and recovery paths continue to preserve project-scoped managed provider boundaries for Codex, Claude, and Gemini agents

## v6.0.5 (2026-04-20)

### 🔒 Agent Isolation Stability

- **Agent Isolation Stability**: strengthened managed agent isolation so Codex, Claude, and Gemini agent sessions stay bound to their own project-scoped provider state under `.ccb`
- **Provider Home Boundaries**: Claude and Gemini startup now reject stale persisted provider homes that point outside the current agent's managed state unless an explicit validated provider profile owns that home
- **Restart Inheritance Safety**: fresh managed Gemini starts no longer adopt ambient `GEMINI_ROOT` or global `~/.gemini` history just because the same work directory was used manually
- **Project Dotfile Protection**: managed startup keeps provider hook/trust state inside agent provider-state homes and does not rewrite project-level `.claude`, `.gemini`, or `.codex` provider dotfiles

## v6.0.4 (2026-04-17)

### 🔁 Legacy Update Compatibility

- **Backward-Compatible Release Assets**: Linux release tarballs now include a compatibility alias so older 6.x updaters that treat the asset filename as the extracted directory can still install successfully
- **Pre-6.0.3 Upgrade Path Restored**: existing `v6.0.1` and `v6.0.2` installs can now update to the latest stable release without relying on patched local updater code
- **Self-Update Hotfix Retained**: current runtime still resolves the extracted release directory correctly and no longer depends on the compatibility alias

## v6.0.3 (2026-04-17)

### 🔧 Self-Update Hotfix

- **Release Tarball Upgrade Fix**: `ccb update` now resolves the extracted release directory correctly instead of treating the `.tar.gz` filename as a directory path
- **Installer Handoff Restored**: self-update once again finds `install.sh` inside extracted release assets and completes the replacement flow end to end
- **Release Build Hygiene**: Linux release packaging now ignores local `.ccb-requests/` mailbox residue so official builds are not blocked by runtime leftovers

## v6.0.2 (2026-04-17)

### 🔁 Agent Routing & Install Guardrails

- **Caller Attribution Fix**: `ccb ask` now preserves the originating agent identity so replies route back to the correct mailbox instead of drifting to `user` or `cmd`
- **Mailbox Delivery Stability**: control-plane reply routing now keeps async `cmd` mailbox delivery aligned with the real caller chain
- **Mixed-Case Agent Recovery**: config layout recovery now normalizes mixed-case agent names consistently during restore and startup
- **macOS Dependency Warning**: `install.sh` now warns when Homebrew is missing on macOS before tmux and related dependencies are installed

## v6.0.1 (2026-04-16)

### 🔧 Release Hygiene & Upgrade Safety

- **Tracked Temp Cleanup**: Removed accidentally tracked `.tmp_pytest` artifacts that contaminated GitHub source archives
- **Repo Hygiene Guard**: Added a regression test to block ephemeral test artifacts from entering the git index again
- **Safer Tar Validation**: Upgrade/install extraction now rejects unsafe symlink targets before unpacking
- **Clearer Extraction Errors**: Unsafe archive failures now explain that the archive contains unsafe paths or links and should be replaced with a clean source archive or official release asset

## v6.0.0 (2026-04-16)

### 🚀 Multi-Agent Runtime

- **Infinite Parallel Agent Edition**: CCB v6 establishes the runtime foundation for effectively unbounded multi-agent delegation inside one project
- **Independent Agent Identity**: Each agent can carry its own role, task stream, skill set, and collaboration style
- **Stable Native Communication**: Agent-to-agent orchestration continues through the built-in control plane instead of shell-level glue

### 🧭 Public CLI Surface

- **User Workflow Reduced**: Public startup and rebuild flow is now intentionally centered on `ccb`, `ccb -s`, `ccb -n`, `ccb kill`, and `ccb kill -f`
- **Control Plane Retained**: `ask`, `ping`, `pend`, and `watch` remain available for model-side coordination without dominating user help
- **Safe Rebuild Semantics**: Legacy project runtime state is rebuilt from `.ccb/ccb.config`, while current 6.x projects retain an explicit runtime marker

### 🌳 Workspace & Recovery

- **Default Inplace Workspaces**: Agents now default to `inplace`; isolated branches are opt-in via `agent:provider(worktree)`
- **Worktree Reconciliation**: Added stable handling for added, removed, renamed, dirty, missing, and unmerged worktree agents during start, kill, and `ccb -n`
- **Restore Stability**: Namespace root panes are preserved during cleanup so restart/restore flows no longer self-delete active project panes

### 🤖 Provider & Release Reliability

- **Gemini Multi-Round Completion**: Gemini completion polling now survives planning/tool rounds and waits for the real final reply
- **Linux Release Path**: `ccb update` for the 6.x line is now aligned to Linux/WSL release assets instead of source snapshots
- **Release Metadata Preservation**: Install/update paths preserve embedded version, commit, and date metadata, including git worktree installs

## v5.3.0 (2026-04-14)

### 🚀 CLI & Workspace Model

- **Public CLI Simplified**: User-facing startup flow is now centered on `ccb`, `ccb -s`, `ccb -n`, `ccb kill`, and `ccb kill -f`
- **Explicit Worktree Opt-In**: Compact `ccb.config` entries now default to `workspace_mode='inplace'`; isolated branches require `agent:provider(worktree)`
- **Internal Control Plane Preserved**: `ask`, `ping`, `pend`, and `watch` remain available for model-side orchestration without crowding the main user help

### 🔧 Project State Recovery

- **Reset Rebuilds Cleanly**: `ccb -n` rebuilds project runtime state while preserving `.ccb/ccb.config`
- **Stale Worktree Cleanup**: Startup and reset paths now prune missing registered git worktrees before rematerializing agent workspaces
- **Agent Change Reconciliation**: Adding agents no longer disturbs existing worktrees; removing or renaming worktree agents retires clean branches and blocks on unmerged or dirty ones
- **Kill Warnings**: `ccb kill` now warns clearly when project worktree agents still have unmerged or dirty state that needs user attention

### 🤖 Completion Reliability

- **Gemini Multi-Round Stability**: Gemini completion polling now tracks tool-call activity and no longer treats the first stable planning message as the final answer
- **Detector Reset Safety**: Session rotation clears tool-active state so later turns are evaluated independently

### ✅ Regression Coverage

- Added focused tests for the simplified CLI surface, worktree reconciliation and reset/kill safeguards, and Gemini early-completion regression paths

## v5.2.8 (2026-03-07)

### 📝 Documentation

- **tmux Layout Tip**: Added English and Chinese usage notes explaining that `Ctrl+b` then `Space` cycles tmux layouts and can be pressed repeatedly

## v5.2.7 (2026-03-07)

### 🔧 Stability Fixes

- **Completion Status**: Completion hook now distinguishes `completed`, `cancelled`, `failed`, and `incomplete` instead of reporting every terminal state as completed
- **Cancellation Handling**: Gemini and Claude adapters now consistently honor cancellation and emit a terminal status instead of leaving requests stuck in processing
- **Routing Safety**: Completion routing now keeps parent-project to subdirectory compatibility while preventing nested child sessions from hijacking parent notifications
- **Codex Session Binding**: Bound Codex requests no longer drift to a newer session log in the same worktree
- **askd Startup Guardrails**: `bin/ask` now respects `CCB_ASKD_AUTOSTART=0` and scrubs inherited daemon lifecycle env before spawning askd
- **Claude Session Backfill**: `ccb` startup again backfills `work_dir` and `work_dir_norm` into existing `.claude-session` files
- **Regression Tests**: Added focused tests for completion status handling, caller routing, autostart behavior, cancellation paths, and Codex session binding

## v5.2.5 (2026-02-15)

### 🔧 Bug Fixes

- **Async Guardrail**: Added global mandatory turn-stop rule to `claude-md-ccb.md` to prevent Claude from polling after async `ask` submission
- **Marker Consistency**: `bin/ask` now emits `[CCB_ASYNC_SUBMITTED provider=xxx]` matching all other provider scripts
- **SKILL.md DRY**: Ask skill rules reference global guardrail with local fallback, eliminating duplicate maintenance
- **Command References**: Fixed `/ping` → `/cping` and `ping` → `ccb-ping` in docs

## v5.2.4 (2026-02-11)

### 🔧 Bug Fixes

- **Explicit CCB_CALLER**: `bin/ask` no longer defaults to `"claude"` when `CCB_CALLER` is unset; exits with an error instead
- **SKILL.md template**: Ask skill execution template now explicitly passes `CCB_CALLER=claude`

## v5.2.3 (2026-02-09)

### 🚀 Project-Local History + Legacy Compatibility

- **Local History**: Context exports now save to `./.ccb/history/` per project
- **CWD Scope**: Auto transfer runs only for the current working directory
- **Legacy Migration**: Auto-detect `.ccb_config` and upgrade to `.ccb` when possible
- **Claude /continue**: Attach the latest history file with a single skill

## v5.2.2 (2026-02-04)

### 🚀 Session Switch Capture

- **Old Session Fields**: `.claude-session` now records `old_claude_session_id` / `old_claude_session_path` with `old_updated_at`
- **Auto Context Export**: Previous Claude session is extracted to `./.ccb/history/claude-<timestamp>-<old_id>.md`
- **Transfer Cleanup**: Improved noise filtering while preserving tool-only actions

## v5.1.2 (2026-01-29)

### 🔧 Bug Fixes & Improvements

- **Claude Completion Hook**: Unified askd now triggers completion hook for Claude
- **askd Lifecycle**: askd is bound to CCB lifecycle to avoid stale daemons
- **Mounted Detection**: `ccb-mounted` now uses ping-based detection across all platforms
- **State File Lookup**: `askd_client` falls back to `CCB_RUN_DIR` for daemon state files

## v5.1.1 (2025-01-28)

### 🔧 Bug Fixes & Improvements

- **Unified Daemon**: All providers now use unified askd daemon architecture
- **Install/Uninstall**: Fixed installation and uninstallation bugs
- **Process Management**: Fixed kill/termination issues

### 🔧 ask Foreground Defaults

- `bin/ask`: Foreground mode available via `--foreground`; `--background` forces legacy async
- Managed Codex sessions default to foreground to avoid background cleanup
- Environment overrides: `CCB_ASK_FOREGROUND=1` / `CCB_ASK_BACKGROUND=1`
- Foreground runs sync and suppresses completion hook unless `CCB_COMPLETION_HOOK_ENABLED` is set
- `CCB_CALLER` now defaults to `codex` in Codex sessions when unset

## v5.1.0 (2025-01-26)

### 🚀 Major Changes: Unified Command System

**New unified commands replace provider-specific commands:**

| Old Commands | New Unified Command |
|--------------|---------------------|
| `cask`, `gask`, `oask`, `dask`, `lask` | `ask <provider> <message>` |
| `cping`, `gping`, `oping`, `dping`, `lping` | `ccb-ping <provider>` (skill: `/cping`) |
| `cpend`, `gpend`, `opend`, `dpend`, `lpend` | `pend <provider> [N]` |

**Supported providers:** `gemini`, `codex`, `opencode`, `droid`, `claude`

### 🪟 Windows Backend Direction

- The old native-Windows backend path has been removed from the active codebase
- Current Unix runtime is tmux-only
- Native Windows mux support is being redesigned around `psmux`

### 🔧 Technical Improvements

- `completion_hook.py`: Uses `sys.executable` for cross-platform script execution
- `bin/ask`:
  - Unix: Uses `nohup` for true background execution
  - Windows: Uses PowerShell script + message file to avoid escaping issues
- Added `SKILL.md.powershell` for `cping` and `pend` skills

### 📦 Skills System

New unified skills:
- `/ask <provider> <message>` - Async request to AI provider
- `/cping <provider>` - Test provider connectivity
- `/pend <provider> [N]` - View latest provider reply

### ⚠️ Breaking Changes

- Old provider-specific commands (`cask`, `gask`, etc.) are deprecated
- Old skills (`/cask`, `/gask`, etc.) are removed
- Use new unified commands instead

### 🔄 Migration Guide

```bash
# Old way
cask "What is 1+1?"
gping
cpend

# New way
ask codex "What is 1+1?"
ccb-ping gemini
pend codex
```

---

For older versions, see [CHANGELOG_4.0.md](CHANGELOG_4.0.md)
