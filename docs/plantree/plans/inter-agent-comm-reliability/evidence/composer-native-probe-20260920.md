# Real Composer Probe

Date: 2026-09-20
Role: evidence
Related: [input guard plan](../topics/input-draft-delivery-guard.md)
Status: isolated UI and OMP editor API probe passed; no delivery qualification

## Isolation

Test root: `/var/tmp/ccb-composer-probe-U4zRvY`.
Dedicated tmux socket and sessions, new provider homes, sanitized environment,
dummy API keys, API endpoints on unavailable loopback port 9. No external
credentials copied, no task submitted and no existing working panes edited.
Versions: Codex 0.155.1, OMP 18.2.6, Claude Code 2.1.278.
Temporary harness `probe.py` and OMP `editor-probe.ts` remain at the test root.

## Observations

| Provider | Empty composer | Typed multiline draft |
| --- | --- | --- |
| Codex | `› Ask Codex to do anything` | `› DRAFT_PROBE_123`, then indented `SECOND_LINE` |
| OMP default Status Band layout | `╰─` with no following text | `╰─ DRAFT_PROBE_123`, then indented `SECOND_LINE` |
| Claude bare fresh session | `❯` with blank content between horizontal rules | `❯ DRAFT_PROBE_123`, then indented `SECOND_LINE` |

Composer position was not the last physical terminal row: these fresh sessions
had blank rows below the input/footer. OMP onboarding offered eight composer
shapes and separate glyph modes, so `╰─` is layout-specific, not universal.
Claude dynamic suggestions were NOT reproduced: fresh bare session, no model
turn. This does not establish behavior with suggestion features enabled.

Codex ambiguity reproduced: type the exact literal `Ask Codex to do anything`
and move cursor to the beginning with Ctrl-A. Plain capture and cursor (x=2,
y=10) match the empty placeholder. `capture-pane -e` distinguishes this version:

```text
typed:       '\x1b[1m›\x1b[0m Ask Codex to do anything'
placeholder: '\x1b[1m›\x1b[0m \x1b[2mAsk Codex to do anything\x1b[0m'
```

Thus neither plain-text equality nor cursor position alone proves emptiness.
SGR dim is a useful version-specific observation, not a universal UI guarantee.
`ccb screen` strips ANSI by design; its current text cannot convey this distinction.

Ctrl-U was not whole-composer clear: at the beginning of the second line in
Codex and Claude it removed the line break, leaving joined draft content.
OMP Ctrl-A/Ctrl-K on the second line cleared that line while leaving the first.
Generic key combinations cannot be declared full clear without readback.

## OMP Native API

Installed extension contract exposes `ctx.ui.getEditorText()` and
`ctx.ui.setEditorText(text)` in
`@oh-my-pi/pi-coding-agent/src/extensibility/extensions/types.ts`.
`modes/controllers/extension-ui-controller.ts` binds these to the actual editor.
An explicit isolated extension ran in a real interactive session, checked
`ctx.hasUI`, set a two-line disposable draft, read it, cleared it and read again:

```json
{"phase":"initial","text":""}
{"phase":"multiline","text":"DRAFT_PROBE_123\nSECOND_LINE"}
{"phase":"cleared","text":""}
```

Raw evidence: `editor-api.jsonl` at the test root. This verifies the native API
in interactive mode, not a CCB polling/IPC integration. Noninteractive UI stubs
can return empty strings; a future adapter must require the live UI binding.
Prefer native boolean/length observations without exposing drafts to CCB logs.

## Consequences

- OMP can use its native editor API instead of parsing a border. Integration
  still needs a scoped bridge to the existing managed extension and FIFO guard.
- Codex screen-based observation should retain ANSI attributes and identify
  the full current composer; fixed placeholder text and cursor alone are unsafe.
- Claude needs normal-session suggestion and multiline/attachment probes before
  a screen-based empty rule is accepted.
- No production code or guard was enabled. 180-second wait, live-turn safety,
  cancellation/restart and send-race behavior remain separate acceptance gates.
