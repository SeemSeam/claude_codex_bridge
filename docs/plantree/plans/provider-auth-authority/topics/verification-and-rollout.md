# Verification And Rollout

Date: 2026-08-04

Role: Acceptance and migration plan

Status: Planning

Read when: preparing implementation, tests, upgrade behavior, or release notes.

## Acceptance Dimensions

The feature is accepted only when all four isolation dimensions pass:

1. Storage isolation: managed writes cannot reach external paths/keyrings.
2. Process isolation: all Provider processes select the resolved private roots
   and one credential authority.
3. Server-side isolation: managed refresh/logout/revoke cannot invalidate
   external or another Agent's credential.
4. Lifecycle isolation: config changes, clear, kill, cleanup, and restart touch
   only owned managed state.

Passing the first two does not imply the third.

## Deterministic Test Matrix

### Authority And Precedence

- Explicit `key/url` suppresses inherited auth/API/route state.
- Credential, route, account-selection, and non-auth config precedence is
  tested independently before composite validation.
- Compatible mixed authority, such as Agent-private credentials with an
  explicit Provider-qualified endpoint, remains representable.
- Incompatible cross-dimension combinations fail before materialization.
- Advanced explicit API env produces the same dimensional authority result.
- Explicit failure never falls back to ambient external credentials.
- `inherit_auth=false` preserves an independently Agent-owned login but not an
  old inherited snapshot.
- Source removal removes a source-owned snapshot on next stopped launch.
- External login, logout, account switch, static credential replacement, and
  inherited API/route changes synchronize on the next stopped restart.
- Restart synchronization does not overwrite `ccb_explicit` or `agent_private`
  authority.
- A transient source read error is distinct from authoritative logout: it
  blocks stale launch without deleting external or Agent-owned state.
- An account/authority change fences incompatible Provider-session resume.
- Managed process environment cannot become inheritance source.
- A derived credential follows external logout only when its recorded Provider
  source-dependency semantics require it.

### External Immutability

- Simulated refresh, local logout, config change, kill, and cleanup leave source
  files, modes, timestamps, keyrings, and environment unchanged.
- Destination symlinks/hard links/junctions are rejected or detached without
  traversing the source.
- External keyring operations are read-only.
- No cleanup command invokes a Provider remote logout path.

### OAuth Server Semantics

Use a local fake OAuth server supporting:

- access-token expiration;
- rotating refresh tokens;
- refresh-token reuse rejection;
- per-credential revocation;
- account-wide revocation;
- concurrent refresh contention;
- token exchange producing an independent child credential.

Required cases:

- two Agents cannot receive writable copies of one rotating lineage;
- a status-only external login remains unchanged;
- external OAuth state changes are re-observed after restart without cloning
  the new rotating refresh token;
- independent derived credentials refresh separately;
- independent children remain valid across source logout when Provider
  semantics say so, while source-dependent children are locally deactivated;
- Agent A local cleanup does not revoke Agent B or external authority;
- unknown revoke scope disables remote logout;
- visible/headless concurrency honors the one-writer rule.

### Provider Storage

- macOS Keychain tests distinguish service-name isolation from remote-token
  identity and never use real secrets.
- Issue #319 adds deterministic coverage for source re-login refreshing an
  existing Agent-derived service, preserving an Agent-private refresh when the
  source is unchanged, refusing to follow a symlinked CCB-owned projection, and
  failing closed when the private service cannot be inspected. The current
  Linux host cannot provide real macOS Keychain qualification, so that remains
  a platform-owner gate after the `v8.6.10` publication.
- Linux Secret Service, Windows credential stores, and file-backed providers
  follow the same capability decision.
- Provider-specific tests assert the exact root and credential-store switches
  for visible and headless paths.

### Diagnostics And Secrecy

- Reports expose authority mode, class, ownership, and action without secrets.
- Correlation evidence cannot be reused as a stable cross-project token
  identifier.
- Config validation errors do not echo key/token values.
- Bundles and logs exclude credential files and keyring payloads.
- Literal secrets never appear in persisted `start_cmd`, session/launch intent,
  launch context, runtime/helper records, pane crash evidence, or bundles.
- Legacy secret-bearing session commands are redacted or excluded before
  diagnostic staging.

### Authority Transaction And Writer Lease

- Probe classification and projection consume one immutable or
  generation-checked source snapshot.
- Source rotation between probe and projection fails without copying the newly
  observed value under stale classification.
- A prepared authority generation and writer lease are durable before spawn.
- Binding or activation failure after spawn terminates the new Provider writer.
- Visible and headless refresh-capable processes cannot acquire incompatible
  concurrent writer leases.
- Daemon recovery reconciles lease generation and exact live process identity;
  unknown or stale writers cannot accept work.

## Provider Qualification

For each Provider, record:

- exact CLI version and platform;
- official documentation/source evidence for storage, refresh, and logout;
- supported independent-login or token-exchange path;
- visible/headless concurrency behavior;
- unit/fake-server results;
- isolated source-runtime results;
- unsupported or qualified modes.

Real-provider tests must not log out or rotate a normal user's external account.
Any test requiring real remote revocation needs a dedicated disposable account,
explicit authorization, and cleanup evidence.

## Migration From Existing Copies

Migration must be stopped-process-safe and non-destructive:

1. Inventory configured Agents and current authority provenance without reading
   or printing secrets.
2. Detect likely unsafe rotating-OAuth inheritance and mark the Agent
   `reauth_required` or `explicit_api_required`.
3. Do not rewrite or delete credentials under a running Provider.
4. Do not call remote logout or modify the external source.
5. After the Agent stops, quarantine or remove only CCB-owned inherited copies.
6. Preserve independently Agent-owned login state when provenance proves it.
7. Require a private Agent login or explicit CCB API authority before restart.
8. Verify external CLI/IDE login remains unchanged using status-only checks.
9. Re-run external source resolution at the final stopped restart and reject
   stale inherited authority if source state changed during migration.

The enforcement timing remains an open question; see
[open-questions.md](../open-questions.md).

## Rollout Sequence

1. Land contracts, capability schema, and diagnostics with no behavior change.
2. Warn on duplicate/unknown rotating credential authority.
3. Add safe Agent-private login and explicit config workflows.
4. Enforce fail-closed behavior for new Agents.
5. Migrate existing stopped Agents with explicit operator action.
6. Enforce the same rule for existing Agents after the announced compatibility
   window, if that window is accepted.
7. Publish Provider qualification and unsupported-mode tables.

## Rollback

Rollback may restore a prior CCB binary, but must never:

- recreate symlinks to external credential stores;
- restore deleted managed copies into external state;
- call remote logout/revoke;
- overwrite an independently Agent-owned login with a source snapshot;
- hide a known rotating-token sharing risk.

Before release, rollback needs a fixture proving old managed state remains
recoverable without weakening the external no-write boundary.
