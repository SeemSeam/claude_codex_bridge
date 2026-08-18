# CCB Mobile

Native Android/iOS/iPadOS remote controller for server-side CCB tmux projects.

This directory is the authoritative CCB Mobile source inside the CCB monorepo.
The legacy standalone `ccb_mobile` repository is retired as an implementation
surface and now exists only for migration/runtime compatibility notes.

CCB and provider CLIs run on the server; the mobile app is a controller for
project discovery, agent switching, terminal access, Markdown reading,
notifications, local/Tailnet gateway access, and file transfer.

## Android Alpha Release

CCB Mobile v8.6.10 is published as an Android APK:

- [Download ccb-mobile-v8.6.10.apk](https://github.com/SeemSeam/claude_codex_bridge/releases/download/v8.6.10/ccb-mobile-v8.6.10.apk)
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

iOS/iPadOS remain source-supported targets, but v8.6.10 release validation is
Android-focused.
