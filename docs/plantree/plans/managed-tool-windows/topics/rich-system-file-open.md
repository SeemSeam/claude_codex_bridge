# Rich system-default file opening

Date: 2026-09-21
Mode: execute-ready / status-update
Status: implemented locally; macOS/WSL routing included in local v8.7.1 preparation

The owner requested one consistent CCB configuration: clicking a file or pressing
Enter opens it with the system default application. This replaces Yazi's default
left-click-only selection and text-file `$EDITOR` opening inside managed profiles.

## Behavior and implementation

- Both safe and rich profiles use the same local-file opening rule. Linux prefers
  `gio open` when installed, otherwise `xdg-open`; macOS uses `open`. The Linux
  shell loop preserves each selected pathname and opens all selected files.
  The generated `ccb-file-open` helper detects WSL by kernel or session markers
  and requires `wslview` from `wslu` for Windows associations. Missing bridge
  tooling fails explicitly, without opening in a Linux GUI instead. Windows
  interop and a registered default application are host prerequisites.
- Enter invokes the generated synchronous `ccb-open` plugin: directories enter
  inside Yazi, files use the configured opener. Left and right mouse presses
  open the exact clicked URL; mouse-up and middle-click do not open files.
- Nonlocal URLs retain Yazi's existing rules, including download handling.
- `_write_yazi_config()` generates `yazi.toml`, `keymap.toml`, `init.lua`, and
  `plugins/ccb-open.yazi/main.lua` and `bin/ccb-file-open`; the bundle completeness check requires all.
  `ccb update rich` regenerates them. Restart the file-browser process to load
  them; changing the files does not alter an already-running Yazi instance.
- Only CCB-owned profiles are modified. User Yazi configuration, system MIME
  associations, external Provider configuration and Windows implementation are
  outside this change.

## Verification and local application

Command: `CCB_TEST_REAL_YAZI=1 PYTHONPATH=lib python -m pytest -q
test/test_cli_tools_workbench.py test/test_workbench_yazi_open.py --tb=short`
with the repository venv on PATH. Original Linux slice: **27 passed**;
macOS/WSL routing extension: **34 passed**.

Native Yazi 26.5.6 in isolated tmux servers verifies both profiles: real mouse
escape events, Enter, exact filenames containing spaces/quotes/shell punctuation,
and mouse/Enter directory navigation without launching an application for folders.
Only the desktop opener is replaced by a recording executable. Separate checks
exercise GIO preference, xdg-open fallback and multiple selected filenames.
Platform stubs also verify macOS argument grouping, WSL1/WSL2 kernel detection,
WSL environment detection, Unicode and mounted-drive path preservation, missing
bridge failure and no duplicate retry after opener failure. No actual macOS/WSL
desktop qualification is claimed.

This host's actual `xdg-open` attempted missing `kfmclient` and returned a false
success. Actual `gio open` accepted a temporary text-file request without error;
desktop-window rendering was not independently confirmed. macOS has not been
tested on hardware. Desktop association/session availability remains an OS
prerequisite; SSH does not imply opening files on the SSH client's computer.

The existing managed profiles were backed up under
`/var/tmp/ccb-yazi-config-backup-vajJo2` before targeted regeneration. No full
bundle reinstall, daemon restart, commit or publication was performed.
