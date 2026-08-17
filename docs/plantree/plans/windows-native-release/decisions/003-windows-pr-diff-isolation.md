# Decision 003: Windows PR diff isolation

Date: 2026-08-17
Status: accepted

## Decision

A Windows-scoped functional PR must keep implementation changes inside
`lib/platforms/windows/` and Windows release/tooling changes inside
`platforms/windows/` or the dedicated Windows workflow and entrypoints.
Windows-specific tests and Windows Plan Tree material may accompany that PR.

The PR gate determines Windows scope from the actual changed paths, Windows or
Herdr markers in added/removed lines, and commit subjects. Once Windows scope
is detected, changes to shared runtime modules, generic tests, Linux/macOS
builders or workflows, npm, Mobile, and other non-Windows clients fail closed.
A cross-platform behavior change must be designed, reviewed, and landed as a
separate cross-platform PR rather than hidden inside a Windows fix.

Existing imports from shared code into `platforms.windows` are architectural
debt. The current inventory is an allowlist ceiling: removing an edge is
allowed, but adding an importer or imported Windows module is rejected.

## Consequences

- A platform check inside a shared file does not make that file Windows-owned.
- A commit whose paths look generic can still be Windows-scoped when its diff
  or subject identifies Windows intent.
- Release notes and synchronized version identity are metadata, but functional
  Windows and non-Windows changes still require separate PRs.
- Corrective rollback of already mixed code lands before activation of this
  gate; the policy has no broad bypass for future remediation claims.
- Existing reverse dependencies remain a residual risk until a later plan
  replaces them with platform-neutral facades and platform-gated loading.

## Supersession

Decision 004 supersedes the release-metadata exception and adds trusted-base
execution for the gate. The ownership and reverse-dependency boundaries in
this decision remain the historical baseline.
