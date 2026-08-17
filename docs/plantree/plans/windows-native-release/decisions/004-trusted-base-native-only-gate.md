# Decision 004: Trusted-base native-only Windows gate

Date: 2026-08-17
Status: accepted

## Context

The first Windows PR gate classified Windows intent from paths, diff markers,
and commit subjects, then executed the checker from the PR checkout. It also
treated release notes and synchronized version files as harmless metadata.
Those choices left two avoidable failure modes: a functional Windows PR could
change global release/client metadata, and a PR could weaken the checker or
workflow that judged the same change.

## Decision

Windows-scoped PRs are limited to the approved native Windows ownership
surface, Windows-only tests and planning material. Root README files,
`CHANGELOG.md`, `VERSION`, release notes, npm metadata, Mobile, Linux/macOS
builders, shared runtime modules, and generic tests are protected paths even
when the change is documentation or version synchronization. Global release
identity changes must use a separately reviewed release PR.

The isolation workflow runs on `pull_request_target` with read-only
permissions. It checks out the base revision as trusted policy, checks out the
PR head only as data, imports the base history into that checkout, and executes
the checker from the base revision. The PR cannot weaken the policy used to
evaluate itself. Branch protection must require this check, and workflow/policy
changes must receive their own governance review.

The reverse-dependency scan remains fail-closed. Existing shared imports of
Windows implementation modules are a debt ceiling; new imports, including
`from platforms import windows`, fail the gate. Windows API/platform markers
in added or removed lines also make a shared-file diff Windows-scoped.

## Consequences

- Windows implementation changes stay under `lib/platforms/windows/` and
  `platforms/windows/` instead of changing Linux/macOS client behavior.
- Windows version/release work is intentionally split from functional Windows
  PRs, so release coordination is explicit rather than an implicit exception.
- The first installation of this gate must land before later policy-hardening
  PRs can rely on the trusted-base workflow.
- Physical removal of the existing reverse dependencies remains a separate
  facade/lazy-loading effort; the gate prevents their expansion.
