# Codex / Claude Composer Deep Probe

Date: 2026-09-20
Role: evidence and implementation-readiness assessment
Status: native UI probes passed within the tested defaults; guard not implemented
Related: [input guard](../topics/input-draft-delivery-guard.md), [initial probe](composer-native-probe-20260920.md)

## Scope and isolation

Owner explicitly excludes the case of typing the exact Codex placeholder as
real input. Do not make that collision a first-version implementation blocker.
The task authorizes analysis and extensive isolated experiments, not production
code changes or release.

Used installed Codex 0.155.1 and Claude Code 2.1.278 in the disposable root
`/var/tmp/ccb-composer-probe-U4zRvY`, on its dedicated `tmux.sock`. Fresh provider
homes and dummy credentials; no existing working panes or external auth changed.
Codex used `--no-alt-screen`, default editor bindings and a loopback endpoint
on closed port 9. Claude basic tests used `--bare`; suggestion tests used normal
interactive mode, explicit prompt-suggestion enablement and a local mock
Anthropic server at `127.0.0.1:18793`. All model responses in the latter were
synthetic. This exercises the real installed UI, not a real model service.
Normal Claude's initial first-run connectivity check failed with HTTP 403;
the disposable home's onboarding flag was then set to exercise the local mock.

Artifacts retained at that root:
`deep_probe.py`, `extra_probe.py`, `suggestion_probe.py`, `theme_probe.py`,
`mock_claude.py`, `validate_captures.py`, `deep-captures.jsonl` (plain screen,
ANSI screen and cursor), `mock-requests.jsonl` (synthetic request payloads).
Final harness uses exact tmux session targets (`=name:`) and distinct paste
buffers. Earlier Ctrl-L experiments and an ambiguous-window-target exploratory
run are retained in raw captures but superseded by the corrected final runs.

## Input and clear matrix

Both providers were exercised with single-line text, three-line text, blank
first line with text below, trailing blank lines, spaces only, newlines only,
Chinese/emoji, wrapped lines, 60-line bracketed paste, slash autocomplete,
mention input, and narrow 45-column / 16-row windows.

The final default-editor matrix contains 12 nonempty-to-clear cases per
provider. A single Ctrl-C restored the empty composer in all 24 cases; the
validator checked the captured empty state, removal of draft markers and a
still-live process. This qualifies these idle default-editor observations only.
Ctrl-C is not a context-free clear command: it can interrupt a live turn or
exit an already-empty CLI. Do not use it without a fresh idle/nonempty check,
and never repeat automatically after a successful clear.

Claude Ctrl-L did not clear the draft, despite the packaged keymap action being
named `chat:clearInput`. Earlier Ctrl-U experiments also did not clear complete
multiline input. Neither is an acceptable inferred whole-composer clear.

## Codex findings

- Normal empty composer consistently shows `Ask Codex to do anything`; any
  tested text/space/newline removes it. A blank first line does not imply empty:
  text can be on a following line.
- Long paste renders `[Pasted Content 1559 chars]`. A valid local PNG path pasted
  into the composer attaches an image and renders `[Image #1]`; Ctrl-C removed
  the attachment and restored the empty placeholder.
- The composer moves vertically and can occupy nearly the whole viewport.
  Inspecting the last physical row or just the arrow row is insufficient.
- `/model` opens a selection list using the same `›` glyph as the composer.
  A glyph anywhere in the capture is not an editable-composer identity.
- A submitted request to the unavailable loopback endpoint displayed `Working`
  and the empty `Ask Codex to do anything` composer simultaneously. The probe
  was stopped with Escape. Empty input does not prove provider idleness.

Exact-version upstream source corroborates the UI:
[chatwidget.rs](https://github.com/openai/codex/blob/rust-v0.155.1/codex-rs/tui/src/chatwidget.rs)
defines normal `PLACEHOLDER` and a distinct `Ask a follow-up question` side
placeholder. [chat_composer.rs](https://github.com/openai/codex/blob/rust-v0.155.1/codex-rs/tui/src/bottom_pane/chat_composer.rs)
renders the normal placeholder when the text buffer is empty and not in shell
mode; input-disabled views have separate placeholder handling. Semantic
`is_empty()` additionally checks attachments. `clear_for_ctrl_c()` flushes
pending paste input, clears text/attachments, and records the old draft in
provider history. These are internal Rust methods, not an exposed CCB editor API.

For first-version normal main-composer support, a known placeholder in the
identified current composer is usable under the owner's exclusion. Also
exclude attachments, overlays, disabled/side-agent views and actual busy state.
Unknown layouts must not be classified from an arbitrary matching history line.

## Claude dynamic suggestion findings

Normal fresh Claude showed an example placeholder (`Try "write a test for
<filepath>"`), unlike the bare session's blank composer. After sufficient mock
conversation turns, native suggestion generation requested `[SUGGESTION MODE:
...]` and displayed the mock answer `Run the tests` as ghost text.

Verified sequence:

1. Suggestion is visible, cursor at the start, actual draft not accepted.
2. Bracketed paste of `CCB_PROBE_NEW_MESSAGE` replaces the suggestion.
3. Enter sends that new message; the captured API request contains no suggestion
   text in the final user content. This is the CLI transport probe, not CCB FIFO.
4. A new suggestion appears. Tab accepts it into the draft.
5. Ctrl-A moves to the start: plain text and cursor now match the earlier ghost,
   but ANSI attributes distinguish them.
6. Ctrl-C clears the accepted draft; the suggestion reappears immediately.

Representative current-composer ANSI text:

```text
ghost:       ❯ \x1b[7mR\x1b[0;2mun the tests\x1b[0m
accepted:    ❯ \x1b[7mR\x1b[0mun the tests
after clear: ❯ \x1b[7mR\x1b[0;2mun the tests\x1b[0m
```

The cursor cell is inverse video; the remaining ghost text is dim (SGR 2).
This behavior passed in default dark, light and dark-ANSI themes. Match semantic
attributes, not a fixed RGB color or suggestion string. A whole-row “all dim”
test would fail because of the cursor cell. Style by itself elsewhere in the
screen is not an empty-input signal.

The packaged JS corroborates separate `promptSuggestion` state, suppression
while input is present/responding, and a Tab acceptance transition. No public
live-editor read/clear API comparable to OMP's was established in this audit.
A direct Enter probe on the reappeared ghost did not add a request; this probe
does not establish Enter acceptance across all suggestion modes.

Clear confirmation must accept a recognized unaccepted ghost/example
placeholder as empty; waiting for literally no visible text would falsely
report a failed clear or hold the queue indefinitely. Accepted suggestions,
multiline continuation text and folded paste markers count as nonempty.

## Existing CCB helpers and minimal next design

Feeding the captured screens to current helpers returned:

| Helper and capture | Result |
| --- | --- |
| Codex `looks_ready`: single draft | true |
| Codex `looks_ready`: model picker | true |
| Codex `looks_ready`: Working plus empty composer | true |
| Claude `looks_ready`: blank first line, text below | true |
| Claude `looks_ready`: model picker | false |

These helpers are readiness heuristics, not qualified input or idleness gates.
Keep the existing chronological FIFO and attributed provider turn-end authority.
After those permit the head, inspect the exact bound pane's current composer.
Use richer internal `capture-pane -e` plus cursor/geometry for Claude; public
`ccb screen` may remain plain text. Normalize provider observations to
`empty/nonempty/unknown` for the fixed 180-second guard. Only fresh nonempty
plus idle permits one clear attempt followed by the same inspector's readback.

## Verification and remaining limits

`python3 /var/tmp/ccb-composer-probe-U4zRvY/validate_captures.py` passed:
24 clear/readback cases; three-theme ghost/accepted/cleared distinctions;
native Claude paste/send excludes ghost; Codex image and busy-composer probes.
No production source was changed, and no 180-second CCB guard was enabled.

Still unqualified: custom keybindings/Vim, arbitrary custom themes, Claude image
attachments, full permission/error overlay matrix, scrollback/copy mode,
invisible-only draft precision, restart/cancellation integration, user-started
turn races and capture-to-paste/Enter races. Unknown cases must not authorize
destructive clearing. The requested fixed timer does not solve those races or
reset on ongoing user edits. This evidence supports a narrow implementation,
not an all-version universal screen parser or release acceptance.
