# Config Designer UI Roadmap

Date: 2026-07-13

## Done

- Confirmed current config authority is complete replacement by source layer:
  built-in default, then user config, then project config.
- Confirmed current `ccb-config` skill already prefers `version = 2`
  `[windows]` topology.
- Cleaned the inherited `ccb-config` skill scope so it is config-only, shows a
  numbered option menu, and treats workflow memory as a separate follow-up.
- Reorganized the config option menu into Basic, Agent Advanced, Workspace
  Advanced, Provider Startup Advanced, Runtime Advanced, and Output groups.
- Kept Rich as the only built-in non-agent pane exposed by the control panel;
  removed editor-tool choices are not generated.
- Added language-following rules so `ccb-config` presents menus, questions, and
  explanations in the user's language while keeping CCB syntax literal.
- Accepted the single-authority config writing rule in
  [decisions/002-config-single-authority.md](decisions/002-config-single-authority.md):
  `[windows]` owns agent presence, provider, default `inplace`/`git-worktree`
  workspace mode, ordering, and window grouping; `[agents.<name>]` is overlay
  only.
- Updated generated role binding behavior so custom local Role Pack bindings no
  longer write redundant overlay `provider`.
- Added `ccb config validate` style warnings for redundant provider,
  redundant default workspace mode, overriding `inplace`/`git-worktree`
  workspace mode, and stale `[agents.<name>]` overlays.
- Updated `ccb_self`'s built-in `ccb-config` guidance to use Role Pack
  shorthand or role-only overlays and to treat style warnings as cleanup before
  reload.
- Landed the first `ccb config ui` runtime shell:
  - loopback-only `127.0.0.1` listener;
  - random per-launch URL token;
  - `--no-open` and `--port` controls;
  - bounded idle lifetime;
  - project-scoped session metadata;
  - the accepted V1/V2/V3 control-panel prototype as the first served page.
- Replaced the sidebar header restart icon with a settings icon. Clicking `⚙`
  launches project-scoped `ccb config ui`; deliberate pane restart remains on
  keyboard `r`, and project kill remains `×` / `Q`.
- Added token-guarded `GET /api/capabilities` model discovery for the panel:
  - Codex uses its local safe model cache and exposes only GPT-5.6 and GPT-5.5
    entries;
  - Claude and Gemini use current official model suggestions;
  - OpenCode and MiMo use their installed CLI model catalogs when available;
  - DeepSeek V4 Pro and V4 Flash use the tested Deep Code model/API runtime
    mapping.
- Enabled V1/V2 static thinking for Codex and DeepSeek. Codex levels come from
  the selected installed model catalog entry; DeepSeek V4 Pro/Flash expose
  `off`, `high`, and `max` and compile through Deep Code runtime overrides.
- Embedded the existing 48px mobile launcher icon as a byte-identical data URI
  for the browser favicon, Apple touch icon, and compact page-header brand icon.
  This remains visible when the prototype is opened directly or the temporary
  HTTP server is unavailable.
- Expanded English/Chinese language coverage across the static editor, V3
  preview, runtime observer, session maintenance, dynamic drawers, model
  capability messages, document title, and accessibility labels. Browser
  language is the default; `lang` in the URL and the page selector can override
  it for the current UI session.
- Connected the local panel to the active project config through guarded APIs:
  - `GET /api/config` returns the current UTF-8 text and SHA-256 revision;
  - `POST /api/validate` uses the same compact/rich/hybrid parser and schema
    validator as CLI config loading;
  - `POST /api/apply` validates, checks the expected revision, writes a dated
    backup, atomically replaces `.ccb/ccb.config`, and can delegate dry-run plus
    reload to the mounted daemon;
  - `POST /api/reload` runs a dry-run only for the exact saved revision.
- Replaced the fake Full TOML preview with a real active-config editor. Validate,
  Save Active Config, Reload Dry-run, and Hot Reload now display backend results
  instead of changing demo badges locally.
- Replaced the initial draft-only visual controls with the structured renderer
  path below; the Full TOML drawer remains the advanced fallback.
- Connected the V1/V2 visual editor to one structured draft and backend TOML
  renderer:
  - recursive 50/50 left-right and top-bottom split trees with undo;
  - V2 window add, rename, delete, selection, and entry-window choice;
  - shared V1 single-window canvas and inspector;
  - Agent/Rich pane conversion with installed Rich capability gating;
  - agent name, provider, workspace, RolePack, model, key/URL, startup args,
    and workspace-group fields;
  - sidebar mode, position, width, section heights, and multiline tips.
- Added project config profiles under `.ccb/config-profiles/*.toml`. The page can
  list, load, and save inactive profiles; activation still goes through the
  digest-guarded active-config path.
- Added normalization safety: visual edits ask before removing TOML comments;
  compact configs containing the unsupported `cmd` pane stay Full-TOML-only.
- Fixed the core project-config renderer so Rich tool leaves are preserved
  instead of being indexed as configured agents.
- Added selected Agent deletion to the V1/V2 visual editor. Deletion promotes
  the sibling subtree to collapse the binary split, removes stale Agent
  overlays, supports undo, removes a sole-leaf Window only when another Window
  remains, and delegates activation to the guarded `remove_agent` reload path.
- Fixed explicit multi-window config rendering so Agent overlays are indexed
  from every Window rather than only the entry Window. Model, thinking, Role,
  API, and other `[agents.<name>]` overrides now survive the visual editor's
  asynchronous render round trip for non-entry-window Agents.
- Connected the RolePack field to the authoritative Role Catalog exposed by
  `role_catalog_status()`, preserving currently configured ids and exposing
  installed, available, updateable, and source-missing roles without leaking
  local source paths or digests to the browser.
- Scoped the V1/V2 RolePack selector to general-purpose roles. Catalog roles
  whose logical id starts with `ccb_` are hidden from new V1/V2 selection,
  except `agentroles.ccb_self`; the complete catalog remains available to the
  V3 surface, and an already configured filtered role remains visible as the
  current value so opening the editor does not rewrite it.

## Validation Evidence

Date: 2026-07-13

- 2026-09-06 V1/V2 Role Catalog filtering: Config UI suite `40 passed`;
  Python compilation, embedded JavaScript syntax, and `git diff --check`
  passed. A live catalog audit marked every `ccb_*` role V2-unselectable except
  `agentroles.ccb_self`, while retaining all general-purpose roles.
- Focused Config UI/parser/phase2 plus full config-loader suites: `112 passed`.
- Rust Sidebar suite: `74 passed`.
- External config-validation matrix:
  `/home/bfly/yunwei/test_ccb2/config_ui_provider_model_matrix_20260710`.
  Provider/model shortcuts validated. DeepSeek V4 Pro/Flash model mapping and
  static Codex/DeepSeek thinking were subsequently promoted from expected
  failures to writable validated controls.
- Real source-wrapper loopback smoke served `/api/capabilities`; headless Chrome
  rendered the project-managed Codex cache, including GPT-5.6 entries not
  present in the older system Codex cache.
- Real Chrome CDP input testing used mouse and keyboard events to switch to
  Chinese, open and close drawers, select Claude, pause message animation,
  validate, run reload dry-run, and confirm a V2-to-V1 version draft. It also
  verified the decoded mobile icon, complete translation-key coverage, and
  desktop/mobile horizontal-fit constraints.
- Browser screenshots:
  `/home/bfly/yunwei/test_ccb2/config_ui_provider_model_matrix_20260710/config-ui-click-desktop.png`
  and `config-ui-click-mobile.png`.
- Compacted the communication observer to a maximum 500px square that also
  respects `58dvh`; its event list shares the same height ceiling. A 1366x768
  notebook-viewport assertion keeps the complete observer panel inside the
  visible screen. Evidence:
  `/home/bfly/yunwei/test_ccb2/config_ui_provider_model_matrix_20260710/config-ui-observer-compact.png`.
- Real mounted-project apply acceptance:
  `/home/bfly/yunwei/test_ccb2/config_ui_apply_acceptance_20260710`.
  A headless Chrome session loaded the active config, edited sidebar
  `tips_height`, validated it, and clicked Hot Reload. The daemon published a
  `view_only_change`; a follow-up CLI dry-run returned `no_change`. The same run
  also confirmed that sidebar width is classified as blocked `layout_change`
  rather than bypassing the daemon gate. Screenshot:
  `config-ui-hot-reload-pass.png`.
- Extended browser acceptance in the same external project covered visual
  split/undo, agent fields, API and advanced overlays, window add/undo, Profile
  save/load, V1/V2 shared-tree switching, Rich conversion/undo, sidebar editing,
  an `add_agent` hot reload, and an `add_window` hot reload. Follow-up CLI
  dry-runs returned `no_change`, and the mounted graph contained all generated
  fake-provider agents. Evidence:
  `config-ui-visual-hot-reload-pass.png` and
  `config-ui-window-hot-reload-pass.png`.
- Active writes now open a candidate-to-active unified diff, require explicit
  confirmation, and repeat the expected-digest check before mutation. Browser
  acceptance exercised this gate for `add_agent`, `add_window`, and
  `view_only_change`; each ended with a CLI `no_change` dry-run.
- Static thinking and DeepSeek mapping acceptance:
  `/home/bfly/yunwei/test_ccb2/config_ui_thinking_acceptance_20260710`.
  Real Chrome interactions saved `gpt-5.5/xhigh`, then switched the same pane
  to `deepseek-v4-pro/max` with DeepSeek API overrides. Both writes passed
  validation and diff confirmation. A source-runtime probe reloaded the TOML,
  materialized the provider profile, and verified the generated Deep Code
  model/thinking/API environment. Screenshot:
  `config-ui-thinking-deepseek-pass.png`.
- Focused static config/UI/provider/store, provider-profile, loop/dynamic, and
  reload-planner suites: `324 passed` after the thinking compiler landed. A
  model/thinking change is classified as `replace_agent`, so hot reload uses
  the existing guarded drain-and-respawn path rather than mutating a live
  provider process in place.
- JavaScript syntax, Python compile, and `git diff --check` passed.
- Real selected-Agent removal acceptance:
  `/home/bfly/yunwei/test_ccb2/config-ui-agent-delete-20260713`. A mounted
  authenticated Codex + Claude project was edited through real headless Chrome:
  Claude was selected, deleted, restored with Undo, deleted again, confirmed in
  the config diff, and hot reloaded as `remove_agent`. Claude pane `%2` stopped;
  Codex retained pane `%1` and PID `415429`; the Window expanded Codex into the
  released area while preserving the 20% Sidebar. Follow-up reload dry-run was
  `no_change`. Desktop/mobile screenshots have no horizontal overflow.
- Nested target-topology acceptance:
  `/home/bfly/yunwei/test_ccb2/config-ui-agent-delete-nested-20260713` started
  authenticated Codex, Claude, and Grok as `Codex ; (Claude, Grok)`. Browser
  deletion of outer Codex published `remove_agent`; Claude pane `%2` and Grok
  pane `%3` retained their process identities and expanded into a full-width
  vertical stack matching `claude1:claude, grok1:grok`. Follow-up dry-run was
  `no_change`.
- Final Config UI/config-loader/reload/remove/reflow suite: `196 passed`.
- Role Catalog payload and dynamic RolePack option regression passed in the
  focused run; the broader run was environment-blocked by the pre-existing
  missing `cryptography` dependency in the qualification Python.
- 2026-07-15 multi-window overlay regression: reproduced the reset with the
  live five-Window project shape, then verified model/thinking retention for
  eight Codex Agents across the first three Windows in real headless Chrome.
  Config UI plus V2 config-loader suites passed `132 passed`; an external
  `/home/bfly/yunwei/ccb_source/ccb_test config ui --no-open --port 0` probe
  also preserved the non-entry Agent overlay through `GET /api/config`.

## In Progress

- Complete the remaining Phase 3 sidebar launch-failure URL presentation and
  keep the V3 activation path gated until its schema and lifecycle contract
  land.

## Next

1. Dogfood the cleaned `ccb-config` skill on a representative config migration.
2. Extract a supported config field registry so parser validation, docs,
   `ccb_self` skill guidance, UI metadata, and formatter behavior cannot drift.
3. Design and implement `ccb config format` or `ccb config normalize --write`
   for safe cleanup of redundant provider/default-workspace fields and stale
   overlays.
4. Complete sidebar entry diagnostics:
   - show the fallback URL/status in the sidebar when browser open fails;
   - preserve keyboard `r` as the non-prominent pane restart action.

## Deferred

- Remote/shared configuration UI.
- Full drag-and-drop layout designer.
- Import/export of reusable team presets.
- Provider credential vault integration.
- Editing project workflow memory from the config UI.

## Phase Gates

Phase 1 is complete when:

- `ccb-config` skill can list supported config knobs clearly.
- The skill writes only `.ccb/ccb.config` or an explicitly requested
  `~/.ccb/ccb.config`; workflow memory remains a separate follow-up.
- Generated topology uses only supported agent leaves and the Rich non-agent
  pane, and validates with the current loader.

Phase 2 is complete when:

- `ccb config ui` opens a local browser editor on `127.0.0.1`.
- The editor can load, preview, validate, and apply `.ccb/ccb.config`.
- Apply shows a diff and validation result before writing.

Current status: active TOML loading, visual V1/V2 serialization, Profile
save/load, validation, guarded writing, backups, and daemon dry-run/reload are
complete. Candidate-to-active unified diff review and explicit confirmation are
also enforced before writes. Phase 2 is complete.

Phase 3 is complete when:

- The sidebar shows a config icon without adding text buttons.
- Clicking the icon launches `ccb config ui` or displays a fallback URL.
- Header kill remains available and keyboard `r` still performs deliberate
  pane restart without exposing restart as a header icon.

Current status: icon launch and kill/restart separation are complete; fallback
URL display remains open, so Phase 3 is not complete.
