# Managed Tool Windows Roadmap

Date: 2026-05-30

## Done

- Unified safe/rich system-default file opening and mouse/Enter folder navigation
  (2026-09-21); [implementation and verification](topics/rich-system-file-open.md).
- Confirmed current explicit `[windows]` topology is agent-only: every window
  value must contain provider-declared agent leaves and `WindowSpec` requires at
  least one `agent_name`.
- Confirmed the current sidebar already renders a window row independently of
  child agent rows, so a window payload with `agents: []` can support the
  desired single-row visual model once project view emits the right window
  shape.
- Confirmed the current namespace materializer creates managed windows,
  sidebars, and agent panes from `ProjectConfig.windows`; tool windows need a
  separate materialization path so they do not create provider runtime
  authority.
- Recorded the decision that managed tool windows are not agents and must not
  be represented as fake providers.
- Recorded the decision that CCB-managed Neovim/LazyVim must be isolated from
  the user's existing Neovim home and global tmux config.
- Defined the first-class `tool_windows` config/topology model in
  [topics/config-and-topology-contract.md](topics/config-and-topology-contract.md).
- Implemented explicit project-view/sidebar shape for tool windows: one window
  row, no child agent row.
- Implemented cold-start and explicit reload add/remove namespace behavior for
  managed tool windows without provider runtime authority.
- Added `ccb tools doctor/install/update neovim` provisioning with isolated
  `ccb-nvim` wrapper/profile, official Neovim tarball fallback, checksum
  verification, and install/update soft mode.
- Added first-slice CCB tmux compatibility policy for managed tool windows:
  focus events and low escape-time are applied through CCB-owned tmux backend
  policy rather than user-global tmux config.
- Completed the automatic matrix for config loading, project view/sidebar,
  namespace materialization, reload dry-run/apply, Neovim provisioning, Rust
  sidebar parsing/rendering, and plan-tree link consistency.
- Completed an isolated Linux/tmux Neovim plugin lab for folder opening,
  Markdown rendering, image handling, browser preview, parser runtime paths,
  opener, and clipboard capability checks. Results are recorded in
  [history/neovim-local-plugin-lab-2026-06-13.md](history/neovim-local-plugin-lab-2026-06-13.md).
- Recorded managed Neovim enhancement defaults in
  [decisions/003-neovim-enhancement-defaults.md](decisions/003-neovim-enhancement-defaults.md).
- Recorded the rich terminal workbench decision: WezTerm/Yazi/LazyVim can be a
  recommended optional CCB-owned bundle, but CCB defaults remain safe and
  capability-gated. The bundle owns generated config and can be installed,
  enabled, launched, disabled, and uninstalled as one product unit. See
  [decisions/004-optional-rich-terminal-workbench.md](decisions/004-optional-rich-terminal-workbench.md)
  and [topics/rich-terminal-workbench-profile.md](topics/rich-terminal-workbench-profile.md).
- Landed the first managed Neovim enhancement slice for Linux/tmux:
  parser runtimepath preservation, read-only capability diagnostics, Snacks
  folder defaults with watcher disabled, guarded `render-markdown.nvim`, and
  no implicit Treesitter parser downloads. Evidence is recorded in
  [history/neovim-enhancement-slice-2026-06-13.md](history/neovim-enhancement-slice-2026-06-13.md).
- Landed the managed Neovim open/fallback slice:
  generated `ccb-open.lua`, `CCBOpenCurrent`, `CCBOpenUnderCursor`,
  `CCBOpenImage`, `CCBRevealCurrent`, conservative external opener selection,
  direct image-file external fallback, and WSL mounted-drive diagnostics. The
  same slice also added a managed `string.buffer` fallback needed by Snacks
  picker on the current test host. Evidence is recorded in
  [history/neovim-open-fallback-slice-2026-06-14.md](history/neovim-open-fallback-slice-2026-06-14.md).
- Landed the first rich workbench bundle slice:
  `ccb tools doctor/install/update/enable/launch/disable/uninstall workbench`,
  CCB-owned Yazi safe/rich profiles, Markdown/PDF/video preview helpers,
  generated WezTerm config, JSON manifest, and source-wrapper validation from
  `/home/bfly/yunwei/test_ccb2`. Evidence is recorded in
  [history/workbench-bundle-slice-2026-06-15.md](history/workbench-bundle-slice-2026-06-15.md).
- Added a compact rich-only Yazi layout that hides the parent-directory column
  and retains the current-directory and preview columns at a `4:3` proportion;
  the safe profile keeps Yazi's default three-column layout.
- Superseded the early `ccb rich-install` setup command. The product entry for
  installing/updating the rich workbench is now `ccb update rich`.
- Landed the `rich` layout alias slice:
  `rich` can be used directly in `[windows]` layouts as a non-communicating
  tool pane/page backed by a rich-forced `ccb-workbench files` command, while
  remaining outside `config.agents`, ask targets, and provider runtime
  authority. Evidence is
  recorded in
  [history/rich-layout-alias-slice-2026-06-15.md](history/rich-layout-alias-slice-2026-06-15.md).
- Sealed the rich WezTerm visual profile inside the generated bundle config:
  family-only font fallback stack, compact font/geometry defaults, quiet
  workbench theme, isolated `--config-file` launch, `--always-new-process`,
  rich/tool active border coloring, and heavy tmux pane split lines. Design
  contract is recorded in
  [topics/rich-terminal-workbench-profile.md](topics/rich-terminal-workbench-profile.md);
  landed evidence is recorded in
  [history/workbench-bundle-slice-2026-06-15.md](history/workbench-bundle-slice-2026-06-15.md).
- Recorded the product boundary that Neovim/LazyVim is now owned by the rich
  bundle and is no longer installed or updated by normal CCB. See
  [decisions/005-rich-owns-neovim.md](decisions/005-rich-owns-neovim.md).
- Landed `ccb update rich` as the single rich install/update entry:
  `rich-install` is removed, standalone public `ccb tools ... neovim` routes
  now reject with guidance, normal install/update no longer provisions Neovim,
  and `ccb rich` requires the rich bundle to be installed/enabled first.
  Evidence is recorded in
  [history/rich-update-entry-slice-2026-06-15.md](history/rich-update-entry-slice-2026-06-15.md).
- Landed binary-first rich dependency hardening:
  `ccb update rich` downloads CCB-owned Yazi/ya release binaries where
  possible, validates them before use, prefers Linux musl builds to avoid
  glibc drift, falls back to platform package managers for non-bundled
  dependencies, and can launch Windows-native `wezterm.exe` from WSL while
  keeping rich tools inside the current Linux distro. Evidence is recorded in
  [history/rich-binary-dependency-slice-2026-06-15.md](history/rich-binary-dependency-slice-2026-06-15.md).
- Landed the global `ccb theme` preference slice:
  `ccb theme [light|dark|+|-]` writes user-level
  `$XDG_CONFIG_HOME/ccb/theme.json`, ordinary terminals only receive CCB
  tmux/sidebar color changes, and CCB-owned rich WezTerm follows the same
  preference through generated config reload. Design and validation evidence
  are recorded in [topics/ui-theme-preference.md](topics/ui-theme-preference.md).
- Hardened CCB Rich WezTerm launch isolation on 2026-07-30: GUI launchers now
  start in a new process session with all standard streams detached from the
  invoking shell, so runtime GUI diagnostics and terminal job-control signals
  cannot pollute or stop the parent `cmd` job. Linux Wayland provisioning also
  creates a CCB-owned XCursor overlay that supplies the missing `hand` cursor
  while preserving the selected user theme and leaving global cursor files
  untouched. `ccb tools doctor workbench` reports the compatibility asset and
  overlay root.
- Verified the launch/cursor hardening with 218 related unit/integration tests
  and an external source-under-test project at
  `/home/bfly/yunwei/test_ccb2/workbench-cursor-detach-cMWaDI`: the launched
  probe had `sid == pid`, no TTY on stdin/stdout/stderr, the generated
  `default/cursors/hand` overlay was present, and the new-window command kept
  `--always-new-process --no-auto-connect`.

## In Progress

- Continue hardening the rich terminal workbench after the lifecycle boundary:
  direct GUI WezTerm validation across more hosts, degraded-banner behavior,
  richer launch/close records, and macOS/WSL manual validation.

## Next

1. Continue the rich terminal workbench implementation from
   [topics/rich-terminal-workbench-profile.md](topics/rich-terminal-workbench-profile.md):
   direct GUI WezTerm validation across more hosts, richer launch/close
   records, degraded-banner behavior for `rich`, and richer diagnostics for
   binary/package fallback decisions.
2. Add macOS, WSL home, and WSL mounted-drive manual checks before enabling
   rich media defaults beyond Linux/tmux.

## Deferred

- Multiple panes inside a tool window.
- Tool command replacement or restart policy.
- Background config watching.
- Tool-specific sidebar activity/status beyond focus/liveness.
- Cross-project/global tool definitions.
- Bundling every Neovim release binary directly inside the CCB release tarballs
  instead of provisioning from a versioned manifest.
