# Config UI Design

Date: 2026-07-10

## Goal

Add an optional browser-based editor for CCB project configuration without
changing config authority. The UI is a convenience layer over `.ccb/ccb.config`;
it is not a persistent service and not a runtime control plane.

## Command Shape

Landed first command:

```bash
ccb config ui
```

Possible later flags:

```bash
ccb config ui --user
ccb config ui --no-open
ccb config ui --port 0
```

The landed editor defaults to the current project and serves the accepted
panel. The Full TOML drawer and V1/V2 visual canvas share the active project
draft; visual changes are rendered to validated TOML by the backend.

## Runtime Shape

- CLI starts a short-lived local HTTP server.
- Bind only to `127.0.0.1`.
- Generate a random token and include it in the URL.
- Open the browser automatically when possible.
- Print the URL as fallback.
- Server exits after an idle timeout or explicit close.

Landed runtime details:

- the URL carries a random launch token;
- requests without that token receive HTTP `403`;
- `GET /api/session` exposes project root, config path, and config existence;
- `GET /api/capabilities` exposes provider/model suggestions and separates
  model discoverability from writable CCB model shortcuts;
- `GET /api/theme` exposes the selected and effective user-level CCB appearance
  preference; token-guarded `POST /api/theme` is the only user-preference
  mutation exposed by the panel;
- current mode is explicitly reported as `editor`.

The page embeds the existing Android 48px launcher icon as a data URI for its
favicon, touch icon, and compact header branding. A byte-equality unit test
keeps the embedded data synchronized with the mobile source asset. The icon
therefore remains available for direct-file viewing and does not depend on the
short-lived loopback server.

## Language Behavior

- Follow the browser language at first open; currently supported UI languages
  are English and Simplified Chinese.
- Allow an explicit `lang=en|zh` URL choice and update it when the in-page
  language selector changes so refresh preserves the current choice.
- Translate the document title, ARIA labels, static controls, dynamic status
  text, model capability diagnostics, and drawer content from one shared
  dictionary.
- Keep CCB command names, provider/model ids, config keys, paths, and role ids
  literal.
- Every `data-i18n` key must exist in both language dictionaries; browser smoke
  tests enforce this invariant.

## UI Sections

Left navigation:

1. Project
2. Windows
3. Agents
4. Tools
5. Sidebar
6. Workspace
7. Model And API
8. Provider Advanced
9. Runtime
10. Preview And Apply

Default visible sections should prioritize Project, Windows, Agents, Tools, and
Sidebar. Workspace and later sections are advanced.

Current release surface:

- keep the configuration/editor and activation-review controls;
- keep bounded Agent history scan and cleanup;
- temporarily delist the read-only Agent communication-flow observer. It is
  absent from the shipped DOM and has no animation, trace drawer, or event
  handlers in the current panel.

The sticky header also exposes **Appearance** independently from project config.
Choices are `system`, `dark`, `light`, `solarized`, `tokyo`, `gruvbox`, and
`rose-pine`. `system` follows browser appearance in the panel and OS appearance
in CCB runtime consumers. Saving Appearance writes only the global CCB
`theme.json`; it never inserts presentation state into `.ccb/ccb.config`.

## API Sketch

```text
GET  /api/session
GET  /api/capabilities
GET  /api/theme
GET  /api/config
GET  /api/profile?name=NAME
POST /api/validate
POST /api/render
POST /api/apply
POST /api/reload
POST /api/profile
POST /api/theme
```

The browser keeps the draft in memory. The source of truth remains the file
written by apply. Config reads return a SHA-256 revision; writes require that
exact revision so a second UI session or external editor cannot be overwritten
silently. Request bodies are bounded to 1 MiB.

`POST /api/apply` supports save-only and hot-reload modes. Both validate first,
create a timestamped adjacent backup when the file exists, and atomically write
the project config. Hot reload then delegates to the existing mounted-daemon
dry-run and reload endpoints; it does not mutate lifecycle or runtime files
itself. A blocked dry-run leaves the valid desired config saved and reports the
runtime mismatch explicitly.

Selected Agent deletion follows the same authority boundary. The visual editor
removes one Agent leaf from the draft and collapses the released binary split
by promoting its sibling subtree. Removing the sole leaf of a Window removes
that Window only when another Window remains; the final project Pane cannot be
deleted. Agent history and provider state are not deleted. Hot reload delegates
the resulting `remove_agent` plan to ccbd, and busy or outstanding work remains
in drain until the Agent can be unloaded safely. After unload, the affected
Window is reflowed from its target `user_layout`, so promoting a vertical or
horizontal sibling subtree in the editor produces the same live tmux topology
without respawning surviving providers.

`POST /api/render` accepts the structured V1/V2 document produced by the
visual editor, validates it, and renders deterministic TOML on the server. The
browser does not implement its own TOML writer. Profile endpoints are confined
to `.ccb/config-profiles/*.toml`, validate before writing, and never activate a
profile implicitly.

## Provider And Model Capabilities

Model choices must not be a static frontend-only enum. The runtime capability
response uses this precedence:

1. the newest safe project-managed or environment-selected provider model
   cache, or a bounded provider CLI catalog command;
2. current official fallback suggestions maintained in CCB;
3. free-form input only where CCB has a validated provider model shortcut.

Each provider entry reports model suggestions, their source, whether CCB can
compile `model` into provider startup arguments, and whether the current config
schema can write thinking effort. These are separate capabilities.

Role Catalog entries similarly carry a V2 selection policy. V1/V2 Agent
overlays offer general-purpose Role Packs plus `agentroles.ccb_self`; workflow
roles whose logical id starts with `ccb_` are V3-only choices and are omitted
from the V1/V2 datalist. The backend still exposes the complete safe catalog
for the V3 surface. If a V1/V2 config already contains a filtered role, the
editor preserves that exact current value without offering it for new
selection.

The first catalog covers:

- Codex GPT-5.6 variants found in the local cache and GPT-5.5;
- current Claude Fable, Opus, Sonnet, and Haiku model ids plus stable aliases;
- current Gemini Flash, Flash-Lite, and Pro suggestions;
- installed OpenCode and MiMo model catalogs;
- DeepSeek V4 Pro and V4 Flash as planning-visible entries only.

For Codex, project-managed
`.ccb/agents/*/provider-state/codex/home/models_cache.json` files participate in
selection because a managed agent may run a newer Codex client/account catalog
than the user's global `~/.codex` cache. Only safe model metadata is read; auth
or credential files are not part of discovery.

DeepSeek V4 Pro/Flash are writable through the Deep Code CLI contract. CCB maps
the model to `DEEPCODE_MODEL`, maps API overrides to `DEEPCODE_API_KEY` and
`DEEPCODE_BASE_URL`, and maps thinking to the documented enabled/effort
environment controls.

V1/V2 write static thinking only where CCB has a tested provider compiler:
Codex and DeepSeek. Codex options follow the selected model's installed catalog
metadata; DeepSeek V4 options are `off`, `high`, and `max`. Unsupported
providers remain disabled.

Current 2026-07-10 acceptance matrix:

| Provider model | Writable thinking values | Capability authority |
|---|---|---|
| `gpt-5.5` | `low`, `medium`, `high`, `xhigh` | installed Codex model cache |
| `gpt-5.6-sol` | `low`, `medium`, `high`, `xhigh`, `max`, `ultra` | installed Codex model cache |
| `gpt-5.6-terra` | `low`, `medium`, `high`, `xhigh`, `max`, `ultra` | installed Codex model cache |
| `gpt-5.6-luna` | `low`, `medium`, `high`, `xhigh`, `max` | installed Codex model cache |
| `deepseek-v4-pro` | `off`, `high`, `max` | DeepSeek API plus Deep Code CLI contract |
| `deepseek-v4-flash` | `off`, `high`, `max` | DeepSeek API plus Deep Code CLI contract |

Codex rows are discovered at UI launch and may change with the installed
client/account catalog. The table records acceptance evidence, not a permanent
hard-coded Codex model policy.

## Validation And Apply

Before writing:

- run the same config loader validation used by `ccb config validate`;
- compare the editor revision with the current target file;
- render a unified candidate-to-active diff and require explicit confirmation;
- warn when likely secret fields are present.

Apply writes only after validation passes and the user confirms.

After writing:

- run validation again;
- show the active source kind and target path;
- run daemon `reload --dry-run` before hot reload;
- apply only when the daemon reports the plan as future-safe.

Landed visual mapping:

- recursive binary window layout trees and Rich leaves;
- selected Agent deletion with sibling promotion, Window collapse, and undo;
- V2 named-window management and V1 single-window reuse;
- topology-owned provider/workspace fields plus agent overlays;
- sidebar topology/view fields and multiline tips;
- active/profile draft synchronization.

Phase 2 is complete. The visual renderer, profile drafts, Full TOML fallback,
validation, digest conflict gate, candidate diff confirmation, atomic write,
backup, and delegated daemon reload path have real browser acceptance evidence.

## Boundaries

The UI must not:

- edit memory files;
- install roles or tools;
- write provider-state;
- start, stop, restart, clear, or kill project runtime;
- implement reload itself instead of delegating to the mounted daemon;
- open a remote listener;
- become a long-running daemon.

The one deliberate exception to the project-file-only boundary is the
Appearance control. It may write `$XDG_CONFIG_HOME/ccb/theme.json` only through
the shared theme service. It must not mutate user terminal dotfiles, provider
state, role stores, or another global preference.
