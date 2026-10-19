# Native Windows maintenance

These tools belong exclusively to the native Windows lane. Linux/macOS entrypoints
and shared update dispatch are unchanged. Python 3.10+, Herdr and WezTerm remain
prerequisites; this does not promote Windows from beta to stable support.

## Configuration protection

The Windows installer and uninstaller read Claude settings as strict UTF-8 JSON.
Invalid JSON, encoding or permission shapes stop before installation/uninstallation
changes. Existing content is never replaced with an empty fallback. JSON writes
use depth 100, UTF-8 without BOM and same-directory atomic replacement, keeping an
exact `settings.json.ccb-backup-<id>` beside the original. Concurrent edits detected
between read and write abort the write. Backups can contain credentials: keep them
private. This does not add a lock protocol to external editors.

## Terminal checks

`ccb windows-terminal-check --json` inspects the current WezTerm process ancestor.
`--wezterm C:\Tools\WezTerm\wezterm.exe` explicitly checks another installation.
Interactive native startup checks before starting/reconnecting agents. Old,
mismatched or unverifiable signed pairs block attachment with a repair message.
An unidentified host prints a warning; it is not reported as tested or repaired.
Headless control calls do not inspect the UI host.

The matching Microsoft-signed ConPTY 1.24.2607.10001 pair is the locally qualified
pair. Newer signed matching pairs are reported separately, without claiming E2E
coverage. To prepare an isolated terminal copy, use `tools/prepare_wezterm.ps1`
as described in [native-reliability.md](native-reliability.md). Point the shortcut
to that copy and reopen the terminal. Restore its old target to roll back the
terminal change. The checker does not replace files in a running terminal.

## Update, rollback and interrupted-switch recovery

Use an **external Python** and a working directory outside the installation.
Stop all CCB projects using that installation first. Maintenance refuses active
processes, project/source roots, reparse points and concurrent maintenance.
It does not kill agents automatically.

Copy the standalone tool outside the installation once so it remains usable if
an update is interrupted while the installation directory is being switched:

```powershell
Copy-Item 'C:\Tools\CCB\platforms\windows\tools\manage_install.py' "$env:TEMP\ccb-manage-install.py"
Set-Location $env:TEMP
python .\ccb-manage-install.py update --install-root C:\Tools\CCB
python .\ccb-manage-install.py status --install-root C:\Tools\CCB
python .\ccb-manage-install.py rollback --install-root C:\Tools\CCB
# Only after an interrupted switch:
python .\ccb-manage-install.py recover --install-root C:\Tools\CCB
```

`update` downloads the official stable Windows ZIP and its SHA256 asset; use
`--release v8.7.10` to select a tag, or `--archive <zip> --sha256 <digest>` for an
offline, explicitly verified archive. The archive must contain the isolated native
entrypoint. Existing pre-8.7.8 native installations can be upgraded and restored.
Running the downloaded/new installer with `install -SourceArchive ...
-ExpectedSha256 ... -InstallPrefix ...` also routes an existing release installation
through this transaction. Fresh installs retain the normal installer flow.

Maintenance stages on the same volume, validates the manifest, checks dependencies
and runs the actual EXE version/help both before and after the directory switch.
The existing `.venv`, local `config` directory and non-runtime user files are
preserved; external user profiles, skills, projects, PATH and shortcuts are not
rewritten. An absent venv requires dependencies in the external Python. Dependency
installation is not performed by this updater. Owned CMD/BAT fallbacks forward to
their native EXE so old entrypoint paths cannot bypass Windows isolation.

A post-switch failure restores the prior installation automatically. A killed
process leaves a journal for explicit `recover`. The latest successful switch is
available to `rollback`; rollback preserves configuration edits made since the
update. Old backups and failed candidates are retained in the sibling
`.CCB.ccb-maintenance` directory (name follows the install directory). They are not
automatically pruned. Check disk space before updating a large runtime.

`ccb update` retains the existing shared diagnostic behavior; this standalone
Windows maintenance entrypoint avoids changing shared CLI ownership.
