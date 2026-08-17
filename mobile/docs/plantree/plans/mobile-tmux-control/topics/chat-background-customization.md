# Chat Background Customization

Status: Implemented and accepted on Android Emulator.

## Goal

Let a user choose, replace, or remove a local image used as the full-screen
workspace background. The preference belongs to the phone and must survive app
restarts.

## Boundaries

- The image is copied into the app's private documents directory and is never
  uploaded to CCB, the Gateway, or Relay.
- The background spans the complete project workspace, including its header,
  project home/list, bubble chat, Agent Terminal, and the standalone host
  Terminal route. Pairing/settings and diagnostics remain control surfaces.
- Selection accepts PNG, JPEG, GIF, WebP, and BMP images up to 20 MiB.
- Replacing or removing a background deletes managed predecessor files.
- Missing, damaged, or unsupported files fail back to the normal theme surface
  without blocking project or conversation loading.
- A theme-aware scrim remains above the image so session boundaries and the
  space between opaque bubbles remain readable in light and dark themes.
- Chat and project-list content surfaces use a persisted adjustable opacity;
  the default is `0.62`. Controls such as the header, composer, settings, and
  diagnostics remain opaque for reliable interaction and contrast.
- Terminal views use a stronger dark scrim and a transparent xterm base so the
  image remains visible without sacrificing ANSI text contrast.

## UX

The existing Settings surface gains a `Workspace background` section next to
Theme. It provides an image preview, one choose/replace command, and a delete
icon. There is no separate background screen and no visible filesystem path.

## Acceptance

- Selecting an image updates an already-open chat without reconnecting.
- The selected image is visible behind the project home/list and chat bubbles,
  not only in the margins around an opaque chat scaffold.
- Adjusting content opacity updates the open workspace and survives restart.
- The selected image is restored after rebuilding/restarting the app.
- Removing the image immediately restores the standard chat surface.
- Chat bubbles, scrolling, expansion, composer behavior, and working rings
  remain unchanged.
- Agent and host Terminal modes render the same background with a
  terminal-specific readability scrim.
- Widget coverage verifies choose/restore/remove, full-workspace placement,
  and transparent terminal rendering; Android Emulator evidence verifies the
  real system picker plus chat and terminal rendering.

## Evidence

- Flutter suite: 785 passed, 1 skipped; `flutter analyze` passed.
- Release APK validation was completed on Android Emulator; the published
  `v8.6.7` asset carries version `8.6.7+8060007`.
- Emulator `emulator-5554` exercised Android DocumentsUI selection, settings
  preview, process restart persistence, full-screen dedicated-project chat,
  Agent Terminal, standalone host Terminal, and removal back to the standard
  surface.
- Owner-local evidence is outside the source tree at
  `/home/bfly/.cache/ccb-workspace-background-20260816`.
