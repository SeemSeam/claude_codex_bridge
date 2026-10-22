# CCB Mobile

Native Android/iOS/iPadOS remote controller for server-side CCB tmux projects.

This directory is the authoritative CCB Mobile source inside the CCB monorepo.
The legacy standalone `ccb_mobile` repository is retired as an implementation
surface and now exists only for migration/runtime compatibility notes.

CCB and provider CLIs run on the server; the mobile app is a controller for
project discovery, agent switching, terminal access, Markdown reading,
notifications, local/Tailnet gateway access, and file transfer.

## Android Alpha Release

CCB Mobile v8.7.16 release APK:

- [Download ccb-mobile-v8.7.16.apk](https://github.com/SeemSeam/claude_codex_bridge/releases/download/v8.7.16/ccb-mobile-v8.7.16.apk)
- Server setup entrypoint: `ccb update mobile`
- App source: [`app/`](app/)

### Android app updates

The Android app checks the latest GitHub release after startup and also exposes
a manual **Check for updates** action in the connection settings. Release
metadata and the APK SHA-256 digest are accepted only from the canonical GitHub
repository. The preferred API path also requires the manifest URL, APK URL,
size, and SHA-256 to match GitHub's release-asset metadata. When the APK itself
is difficult to download directly, its bytes may fall back through
`gh-proxy.com`, `ghfast.top`, and `ghproxy.net`, in that order, but proxy
responses must match the trusted manifest's exact size and SHA-256 digest.
Proxies are never trusted to provide release metadata. Android still requires
the user to approve the signed APK update; the app does not perform silent
installation.

The app is designed for real server-side CCB projects, not a demo-only flow.
It connects to the server-wide mobile gateway, lists mounted CCB projects,
renders agent transcripts, sends pane-native text input, opens terminal views,
and supports image/document upload and download through the authenticated
gateway.

Selected-agent headers also expose the active Provider/model/thinking state.
The Provider sheet uses capability-driven model and effort choices, separates
configured/pending state from the active native session, and displays native
session usage plus optional account quota. These semantics are directly
aligned with pinned open-source Paseo Provider contracts; see
[`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md).

### Native OMP conversation turns

OMP conversations are projected from the live Collab snapshot and events into
stable user-anchored turns. One reply bubble contains the latest assistant answer;
visible reasoning, intermediate messages, tool calls and results remain in an
ordered, initially collapsed execution process. Tool calls retain their native
call IDs. Missing outcomes and unfinished tools are not presented as success.

`app/lib/native_session/native_session.dart` exports the reusable v1 turn/block
model and `NativeTurnBody`. This module has no CCB project, job, terminal or
localization dependency: hosts supply their Markdown renderer and labels, so a
future Nozeli mobile client can consume the same content contract. CCB stores
the optional `native_turn` projection with its conversation snapshots.

Only Linux servers enable the new OMP Collab route: a private loopback relay
and owner-bound local bridge attach to the existing TUI. Linux verifies socket
ownership through `/proc`. macOS retains the original OMP terminal-input and
native-session-history route; it does not start the Collab supervisor or expose
the new live model controls. Other Providers retain their existing routes.

The mobile app selects Collab only when the server advertises
`provider_control.runtime_source=collab`; an OMP provider name alone is not enough.
Existing Linux OMP processes must be restarted normally to load the supervisor.
Unavailable Linux Collab produces an explicit disconnected banner, never a silent
terminal/history fallback. Collab refresh is limited to the selected foreground
agent and pauses while the app is backgrounded.

OMP model/effort choices come from the running agent's native model registry.
The Provider sheet switches that same session through a restricted native
companion, confirms host readback, and does not edit project configuration or
restart the agent. Busy sessions, desktop drafts and stale session/model
revisions reject changes; observable mutation races report an unknown outcome.

Linux OMP accepts consecutive mobile sends into its persisted, session-fenced FIFO.
Queued user messages remain visible after refresh/restart, with explicit Queued,
unconfirmed, sent or failed delivery state; held inputs show Needs review and are
never silently resent into another session. Queue acceptance does not reset the
running turn's status. Exact native entry association preserves the original
idempotency-key identity without matching message text. Long queued bodies share
the same bounded inline/original-reader budget as native history.
Upstream Collab v3 does not provide request-level prompt admission receipts;
unconfirmed delivery is retained until exact native evidence is available.
Session/generation fences and retained idempotency keys prevent cross-session
delivery and duplicate handoffs. Terminal shortcuts remain
available only in Open Terminal; interactive approvals and remote session
branching are not implemented by this connection.

Validation commands (installed OMP 18.11 sources and the Android SDK required):

```sh
bun test test/omp_collab_runtime.test.mjs test/omp_model_companion.test.mjs
PYTHONPATH=lib .venv/bin/python test/omp_collab_tui_smoke.py
bun test/omp_collab_native_smoke.mjs /absolute/path/to/pi-coding-agent
.venv/bin/python test/omp_mobile_emulator_backend.py --upstream /absolute/path/to/pi-coding-agent --run
```

For the actual encrypted CCB Relay large-history lane, add `--relay` to the
Android harness command. It starts an isolated TLS Relay plus host connector,
checks history above 2 MiB and an oversized single turn, and verifies original
content UTF-8 byte offsets and SHA-256 without increasing the 512 KiB Relay cap.

OMP inline pages use a conservative 192 KiB serialized budget. Oversized turns
show a preview and explicit Read original action; originals are returned in
versioned UTF-8-safe pages of at most 32 KiB. Changed session/generation/content
invalidates the read cursor instead of mixing snapshots. Both the server repair
and the updated app are required; installing the APK alone does not fix an old
server's oversized responses.

`CCB_MOBILE_TEST_BUILD=1 flutter build apk --debug` uses the separate
`io.ccb.mobile.ccb_mobile.relaytest` application ID so repair test builds do not
replace the production installation or its paired-device data.

The repair was exercised on Android API 35 over the real TLS CCB Relay:
196 history items across four pages; a 987,082-byte original reconstructed
across 31 pages with exact SHA-256; send/live/tools/model/stale-fence/reconnect
and background-pause checks passed without increasing the Relay cap.
Evidence: `/home/bfly/.local/state/ccb-relay-fix-test/relay-run8`.



The Android harness uses production Flutter workspace/model controls, authenticated
HTTP gateway, encrypted Collab guest, real upstream CollabHost and SessionManager.
Its model output/catalog scenarios are deterministic fixtures, not paid-provider
quality evidence. The actual TUI smoke separately exercises real native model and
effort APIs without submitting any prompt. Android API 35 verification covered
two live-to-durable turns, folded tool results, model readback without restart,
stale revisions, explicit failure, disconnect/reconnect, generation fencing and
background refresh suspension. The final ancestry/cross-launch-key revision was
rechecked with 853 Flutter tests passing (one skipped), clean static analysis,
175 gateway/OMP Python tests, 13 Bun protocol/model tests, the actual native TUI
model smoke and the Android journey. Local final evidence is retained at
`/home/bfly/.local/state/ccb-omp-final-android` (three submitted fixture prompts,
one model change, zero terminal calls and zero external model calls).

The harness disables Bun's implicit package installation so an isolated HOME
cannot silently download a newer native dependency instead of the pinned release.
The current-project paid-model run separately exposed omitted metadata ancestors;
the companion now exports bounded ID/parent-ID links only, preserving original
turn ownership without exposing the omitted record contents. New OMP mobile
submissions use cross-launch unique keys; explicit retries retain the same key.



### LAN pairing and recovery

For a LAN QR/code, connect the phone and computer to the same trusted Wi-Fi,
wired LAN, or phone hotspot. Avoid guest/client-isolated Wi-Fi, and allow local
network access through any active VPN. Android shows an advisory before a LAN
claim when no local-network transport is visible; **Continue anyway** remains
available because the phone itself may be providing the hotspot.

If a paired LAN route becomes unreachable, the app keeps the profile and shows
a persistent network notice with **Retry** and **Diagnostics**. Check the
same-network, VPN, guest-isolation, and firewall conditions first. If the
computer received a new LAN address, rerun `ccb update mobile` on the computer
and scan the new code.

## Plan Tree

- [Mobile tmux control plan](docs/plantree/plans/mobile-tmux-control/README.md)
- [Remote access roadmap](docs/plantree/plans/mobile-tmux-control/topics/remote-access-roadmap.md)
- [Native Flutter blueprint](docs/plantree/plans/mobile-tmux-control/topics/native-flutter-ccb-blueprint.md)

## Project Layout

```text
app/                 Flutter mobile app
docs/plantree/       Planning tree and design decisions
tools/               Emulator, gateway, and acceptance helpers
```

iOS/iPadOS remain source-supported targets, but v8.6.13 release validation is
Android-focused.
