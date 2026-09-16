# PR350 Local Integration

Date: 2026-09-16
Role: evidence
Status: local integration and qualification; not merged to remote main
Authority: [Core PR requirements](../test-and-release-gates.md#core-pr-maintenance-requirements)

## Scope

The owner accepted local integration followed by repair and testing. Branch
`integrate/pr350-auth-fixes` merges PR350 at `8b6c8bd06638f0c55fe20496b7021d0e33311058`
onto main `5784daee4`. PR349 is not merged. Its explicit private database
targeting is carried into Claude's credential operations.

## Corrected Review Finding

The previous recommendation to add `-t 2147483647 -u` was incorrect. Apple's
[security tool implementation](https://github.com/apple-oss-distributions/Security/blob/main/SecurityTool/macOS/keychain_set_settings.c)
initializes `lockOnSleep` and `useLockInterval` to false. `-l` enables sleep
locking; `-u` enables interval locking, subject to the final INT_MAX handling.
The existing no-option call disables both settings. Retain it and unlock an
existing database on every provisioning pass. The author also reported a native
temporary-Keychain check in
[the latest reply](https://github.com/SeemSeam/claude_codex_bridge/pull/350#issuecomment-5690420665).
That author's result is not a native test performed by this Linux host.

## Local Repairs

- Claude find/add/delete explicitly target the private database as well as
  using the private HOME. Write timeout diagnostics suppress credential argv.
- AGY source Keychain read failures and private Keychain provisioning failures
  block launch instead of silently selecting potentially stale file auth.
- An owner-private, non-secret AGY projection manifest records copied file
  paths and whether the private Keychain item was inherited. Confirmed absence
  removes only recorded projections; unmarked private files/items survive.
- Auth file reads complete before file projection updates. Invalid provenance
  blocks cleanup. Atomic replacement detaches destination aliases.
- A missing auth-mode marker does not authorize adopting an existing Keychain
  item as independent login. Switching back from independent auth to inheritance
  requires explicit authority resolution and preserves the private login.

## Verification

- Initial PR350 focused baseline: 368 passed.
- Local repaired focused suites: 368 passed.
- Added deterministic boundary regressions: 19 passed.
- Full Linux suite: in progress. The first attempt exhausted `/tmp` inodes;
  it was stopped and restarted with an isolated `/var/tmp` temporary root.

## Qualification Limits

This host cannot execute native macOS sleep/wake, Security.framework, or GUI
prompt acceptance. Keep that gate open before promoting to main. Synthetic
Keychain tests prove command/storage boundaries, not remote OAuth independence.
Provider-specific rotating-token refresh and revoke qualification remains open;
this change must not be described as solving that broader authority problem.
No real user login, logout, refresh-token rotation, or remote revocation is used
as a test. Existing unrelated `.zai/` files are excluded.
