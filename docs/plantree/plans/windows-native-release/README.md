# Native Windows Release Plan

Date: 2026-08-12

## Goal

Ship and maintain the CCB `v8.6` stable release line through the existing
release products while attaching an isolated, testable native Windows x64
artifact without claiming stable Windows support or mixing Windows code into
Unix/npm ownership.

## Current target

- Version/tag: `v8.6.8`
- Status: the published release remains immutable. Local corrective rollback
  `7d74e92a8`, isolation gate `23d62228f`, and strict-gate hardening
  `702870c1b` remove and guard against Windows/Herdr changes that cross shared
  Linux/macOS ownership; the Windows artifact remains beta.
- Artifact: `ccb-windows-x86_64.zip`
- Installer: root `install.ps1`, implemented by
  `platforms/windows/installer/install.ps1`
- Runtime source ownership: `lib/platforms/windows/`
- Release tooling ownership: `platforms/windows/`

## File map

- [roadmap.md](roadmap.md): gates and current status.
- [topics/v8.6.1.md](topics/v8.6.1.md): current stable patch release and
  verification record.
- [topics/v8.6.0.md](topics/v8.6.0.md): stable release implementation and
  verification record.
- [topics/v8.6.0-beta.3.md](topics/v8.6.0-beta.3.md): published Windows beta
  evidence used by the stable candidate.
- [topics/v8.6.0-beta.2.md](topics/v8.6.0-beta.2.md): immutable failed-candidate
  record.
- [topics/v8.6.0-beta.1.md](topics/v8.6.0-beta.1.md): immutable failed-candidate
  record.
- [decisions/001-isolated-windows-prerelease.md](decisions/001-isolated-windows-prerelease.md):
  frozen isolation and publication boundaries.
- [decisions/002-stable-ccb-with-windows-beta-asset.md](decisions/002-stable-ccb-with-windows-beta-asset.md):
  stable tag and Windows support-tier boundary.
- [decisions/003-windows-pr-diff-isolation.md](decisions/003-windows-pr-diff-isolation.md):
  fail-closed changed-file and reverse-dependency boundary for Windows PRs.
- [decisions/004-trusted-base-native-only-gate.md](decisions/004-trusted-base-native-only-gate.md):
  trusted-base execution and strict native-only ownership for future Windows
  PRs.
- [evidence/v8.6.8-windows-pr-isolation-audit.md](evidence/v8.6.8-windows-pr-isolation-audit.md):
  commit-level findings, rollback scope, verification, and residual risks.

## Acceptance boundary

The CCB tag is stable, but the Windows artifact remains beta. Publication
requires the normal stable Linux, macOS, npm, Sidebar, and Android gates plus a
native Windows x64 build, checksum, archive install, and `ccb.exe` smoke test.
Real WezTerm/Herdr and provider behavior remains a post-publication Windows
qualification gate.
