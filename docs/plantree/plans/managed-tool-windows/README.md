# Managed Tool Windows Plan

Date: 2026-05-30

## Purpose

Plan first-class CCB-managed non-agent windows such as a default `neovim`
window. A tool window is visible in the sidebar as a single window row and is
managed by the project tmux namespace, but it is not an agent and must not
participate in ask routing, provider runtime, health monitoring, completion
tracking, or Comms.

This plan exists separately from the agent hot-reload plan because the core
model is a new topology primitive. Reload is one integration path, not the
authority for the feature.

## File Map

- [v8.7.1 preparation](evidence/release-871-validation-20260921.md): platform
  test scope, package checks and GitHub version-display discrepancy audit.
- [System-default file opening](topics/rich-system-file-open.md): shared safe/rich
  mouse and Enter behavior, installed configuration and native Yazi checks.
- [roadmap.md](roadmap.md): current implementation sequence and gates.
- [open-questions.md](open-questions.md): unresolved questions only.
- [topics/config-and-topology-contract.md](topics/config-and-topology-contract.md):
  proposed `tool_windows` config shape, validation, identity, and user-facing
  semantics.
- [topics/namespace-sidebar-reload-design.md](topics/namespace-sidebar-reload-design.md):
  namespace materialization, sidebar/project-view payloads, and explicit reload
  behavior for tool windows.
- [topics/neovim-lazyvim-provisioning.md](topics/neovim-lazyvim-provisioning.md):
  install/update provisioning for a CCB-managed Neovim and LazyVim profile,
  including tmux compatibility.
- [topics/neovim-system-optimization.md](topics/neovim-system-optimization.md):
  second-phase plan for making the managed Neovim profile useful and
  diagnosable across Linux, macOS, and WSL, including folders, Markdown,
  images, clipboard, and opener behavior.
- [topics/rich-terminal-workbench-profile.md](topics/rich-terminal-workbench-profile.md):
  optional recommended WezTerm/Yazi/LazyVim workbench profile with safe and
  rich media tiers for Markdown, PDF, image, and video preview.
- [topics/ui-theme-preference.md](topics/ui-theme-preference.md):
  global `ccb theme` preference model for CCB tmux/sidebar colors, with
  optional CCB-owned rich WezTerm synchronization.
- [history/neovim-local-plugin-lab-2026-06-13.md](history/neovim-local-plugin-lab-2026-06-13.md):
  isolated local Linux/tmux plugin lab for folder, Markdown, image, parser,
  browser-preview, opener, and clipboard capability checks.
- [history/neovim-enhancement-slice-2026-06-13.md](history/neovim-enhancement-slice-2026-06-13.md):
  landed Linux/tmux implementation slice for parser runtimepath preservation,
  read-only doctor capabilities, Snacks folder defaults, guarded Markdown, and
  Treesitter no-auto-install policy.
- [history/neovim-open-fallback-slice-2026-06-14.md](history/neovim-open-fallback-slice-2026-06-14.md):
  landed conservative OS-integration slice for external open/reveal commands,
  WSL mounted-drive diagnostics, and Linux/tmux source-wrapper validation.
- [history/workbench-bundle-slice-2026-06-15.md](history/workbench-bundle-slice-2026-06-15.md):
  landed first CCB-owned workbench bundle slice for WezTerm/Yazi/LazyVim and
  Markdown/PDF/video helpers, with isolated profiles, manifest, lifecycle
  commands, and Linux/tmux source-wrapper validation.
- [history/rich-layout-alias-slice-2026-06-15.md](history/rich-layout-alias-slice-2026-06-15.md):
  landed `rich` as a reserved layout alias that can be mounted in `[windows]`
  like provider panes while remaining outside agent communication/runtime.
- [history/rich-update-entry-slice-2026-06-15.md](history/rich-update-entry-slice-2026-06-15.md):
  landed `ccb update rich` as the single rich install/update entry, removed
  the `rich-install` alias, and removed standalone public Neovim tool routes.
- [topics/test-matrix.md](topics/test-matrix.md): automatic and manual tests,
  including `test_ccb2` validation.
- [decisions/001-tool-windows-are-not-agents.md](decisions/001-tool-windows-are-not-agents.md):
  decision record for keeping tool windows out of agent/provider runtime.
- [decisions/002-isolated-managed-neovim-profile.md](decisions/002-isolated-managed-neovim-profile.md):
  decision record for installing Neovim/LazyVim into CCB-owned isolated paths.
- [decisions/003-neovim-enhancement-defaults.md](decisions/003-neovim-enhancement-defaults.md):
  decision record for capability-gated Neovim folder, Markdown, image,
  browser-preview, clipboard, and plugin-pinning defaults.
- [decisions/004-optional-rich-terminal-workbench.md](decisions/004-optional-rich-terminal-workbench.md):
  decision record for treating the rich terminal workbench as an optional
  recommended profile rather than a hard CCB dependency.
- [decisions/005-rich-owns-neovim.md](decisions/005-rich-owns-neovim.md):
  decision record for moving Neovim/LazyVim out of the normal CCB tool surface
  and into the optional rich bundle.

## Related Sources

- [../../../ccb-config-layout-contract.md](../../../ccb-config-layout-contract.md)
- [../../../ccbd-startup-supervision-contract.md](../../../ccbd-startup-supervision-contract.md)
- [../ccbd-agent-hot-reload/README.md](../ccbd-agent-hot-reload/README.md)
- [../ccbd-agent-hot-reload/topics/non-disruptive-hot-load-design.md](../ccbd-agent-hot-reload/topics/non-disruptive-hot-load-design.md)
- [../sidebar-provider-activity/README.md](../sidebar-provider-activity/README.md)
- [../../baseline/runtime-flows.md](../../baseline/runtime-flows.md)
- [../../baseline/storage-and-state.md](../../baseline/storage-and-state.md)

## Scope

In scope:

- A config-level `tool_windows` concept for windows that run a command such as
  `nvim`.
- Cold-start materialization of managed tool windows.
- Project view and sidebar rendering as one window row with no child agent row.
- Explicit `ccb reload` add/remove behavior for idle managed tool windows.
- Project/session-scoped tmux identity and UI settings.
- A single optional rich workbench lifecycle. Normal `ccb install` and
  `ccb update` must not install or refresh Neovim/LazyVim; rich provisioning is
  requested explicitly through the rich command surface such as
  `ccb update rich`.
- tmux compatibility settings applied at CCB session/window scope, not through
  user-global tmux config edits.
- Tests proving tool windows do not become agents or provider runtime records.

Out of scope for the first slice:

- Tool windows participating in `ask`, Comms, provider status, or completion.
- Arbitrary tool pane layouts inside one tool window.
- Background config watching.
- Automatic replacement of a running tool command after its command changes.
- Treating a tool window as a terminal multiplexer workspace independent of
  CCB ownership.
- Mutating a user's existing `~/.config/nvim`, Neovim data/cache/state
  directories, or global tmux configuration.
