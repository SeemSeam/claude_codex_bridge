# Native Windows release lane

This directory owns the native Windows installer, x64 launcher, release
packaging, Herdr integration documentation, and Windows-only tooling.

Linux and macOS release builders remain under `scripts/` and must not import
this directory. Shared runtime contracts may call implementations under
`lib/platforms/windows/`, but concrete Windows code must not be added to the
Unix installers or Unix release workflows.

The Windows beta artifact is `ccb-windows-x86_64.zip`, attached to the stable
CCB GitHub Release. It contains native PE launchers
for `ccb`, `ask`, `autonew`, and `ctx-transfer`, plus the Python runtime source
tree. Python 3.10+, WezTerm, Git Bash, and Herdr remain explicit prerequisites
for this support tier; it is not presented as a fully self-contained installer
or as stable Windows support.

See [native maintenance](docs/maintenance.md) for protected configuration writes,
terminal compatibility checks, verified updates, rollback and interrupted-update
recovery using the Windows-owned tools.
