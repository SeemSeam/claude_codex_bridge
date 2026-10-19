# Native Windows open reliability

This change is self-contained in Windows-owned paths and has no prerequisite
shared-code change. The native EXE runs `platforms/windows/ccb.py`, which reuses
the existing source guard, platform detection and metadata. Windows-owned CLI
composition preserves the shared early-command ordering and calls existing
parsing, config validation and approval helpers. Only start/open dispatch and
foreground attachment are implemented here; no global handlers are patched.
Contract tests compare early-command behavior with the unchanged shared entrypoint.
Direct invocation
of the repository-root `ccb.py` keeps the legacy shared route. Use the native EXE
or `python platforms/windows/ccb.py` for this Windows behavior.

`ccb herdr open` serializes check/start per project. If the daemon PID is alive,
opening reconnects rather than sending another start/restore RPC. A busy socket
is not evidence that another startup is safe. An explicit management operation
is required to restart or reconfigure an existing runtime.

Cold opens use the daemon's project-derived session name. A different
`--herdr-session` is rejected before starting any server: the shared daemon
does not support assigning an arbitrary session name. Reopens use the session
recorded by the live daemon and reject conflicting explicit names. Reopening
does not recreate a missing Herdr server behind a still-running daemon.

Interactive attachment inherits the invoking console and lasts until detach;
it does not spawn a second hidden UI or impose a five-second lifetime. A failed
attachment raises an error. `--wait-ready` requires a live namespace ping and
returns a nonzero status on timeout.
The Herdr adapter uses a separate foreground subprocess runner, so background
control wrappers cannot hide its console. Shared subprocess defaults are unchanged.

The native EXE defaults `CCB_STARTUP_TRANSACTION_TIMEOUT_S` to 180 seconds when
not already configured, allowing initial plugin projection to finish. This does
not make projection faster. `platforms/windows/start.ps1` wraps the EXE and keeps
startup errors visible. Pass `-NoPause` for automation and `-NoAttach` for a
headless open. Background control subprocesses remain hidden.

Before respawning in an existing pane, the adapter requires its foreground PID
to equal its original shell PID. A model process or missing evidence fails
closed. This is a boundary guard, not atomic process replacement in Herdr.

## Native acceptance runner

`platforms/windows/tools/e2e_local.py` operates on an explicitly supplied,
disposable project and real provider processes. Existing projects must carry
the `.ccb-native-e2e` marker created by its initial setup. Never mark a normal
work project as disposable. The `lifecycle` stage stops the entire test project.
Unmarked nonempty directories are rejected even when they have no `ccb.config`.
Failure checks use a unique directory within the disposable project, stop their
own Herdr server and allow a bounded delay for Windows to release its directory
handle before cleanup. Test GUIs also close when a check raises an exception.

Run with Python and the runtime dependencies installed; `config` additionally
needs Python Playwright and a local Edge browser. Example parameters:

```powershell
python platforms/windows/tools/e2e_local.py --project 'C:\ccb-tests\中文 workspace' --output 'C:\ccb-tests\results' --herdr 'C:\tools\Herdr\herdr.exe' --sh 'C:\Program Files\Git\bin\sh.exe' --stage cold
```

Then run stages `services`, `ui` (also pass `--wezterm`), `config`, `jobs`,
`lifecycle`, and `failures`, sequentially. Confirm the provider's first-use
project-trust prompt in this disposable workspace before running UI and jobs.
`jobs` submits short requests to the configured providers and may incur usage.
It reuses saved submission receipts on a rerun rather than resubmitting jobs.

Results append to `report.json`; a stage returns nonzero for any failing check.
The upstream Herdr single-agent restart is currently deferred, so `lifecycle`
checks that it explicitly reports unsupported and preserves agent identity;
this does not count as successful single-agent restart. Full stop/start is
tested separately.
Private config UI launch logs contain a short-lived loopback token; do not
publish raw logs. Screenshots and result JSON do not need that URL.

Unicode paste and terminal key injection do not validate every OS IME or
candidate-selection workflow. Unit tests, real process tests, and provider
roundtrips are separate evidence, not interchangeable claims.

## Windows terminal host compatibility

The 20240203 WezTerm bundle's ConPTY/OpenConsole pair leaked mouse-control
payloads into provider prompts in the local Windows E2E test. Changing only
Herdr's input backend or disabling WezTerm Win32 input did not fix that test.
Using the matching Microsoft-signed ConPTY 1.24.2607.10001 pair already shipped
with Herdr 0.9.3 passed the same workload. This is a local compatibility fix,
not an official WezTerm release or a guarantee for all terminal versions.

`tools/prepare_wezterm.ps1 -WezTermRoot <original> -HerdrConptyRoot <herdr/conpty>
-Destination <new-directory>` creates an isolated x64 copy. It checks the
component signatures and matching versions, rejects an existing destination,
and writes a SHA256 manifest. Point the desktop shortcut to the new copy and
reopen the UI; an already running terminal retains its old console host.
The original terminal and CCB provider sessions can be retained for rollback.

Run `tools/e2e_input.py` with the same `--project`, `--output`, `--herdr`, `--sh`,
and `--wezterm` arguments as the existing runner. It uses the real PowerShell /
CCB / Herdr / WezTerm chain, checks both Claude and Codex for 30 seconds of idle
input, mouse protocols, focus/resize/mouse messages, Unicode paste, punctuation,
arrow/backspace editing, and session identity preservation. Window messages are
scoped to the disposable GUI; it neither types into other windows nor submits
model requests. A finite idle check cannot establish absence of every sporadic
input problem. OS IME composition remains a separate manual acceptance check.
