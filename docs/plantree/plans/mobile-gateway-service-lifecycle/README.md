# Mobile Gateway Service Lifecycle Plan

Date: 2026-07-01

## Purpose

Make CCB Mobile gateway startup idempotent and host-owned. Re-running
`ccb update mobile` should refresh or replace the single CCB-managed background
mobile gateway instead of failing because the previous gateway still owns
`127.0.0.1:8787`.

The gateway must remain loopback-only. Public exposure through Tailscale Serve,
Cloudflare Tunnel, or another route provider remains a separate route layer.

## File Map

- [topics/project-authorization.md](topics/project-authorization.md): Issue #393
  reproduction, project/host authorization boundary, and pending policy decision.
- [roadmap.md](roadmap.md): implementation phases and release gate.
- [topics/unique-background-service.md](topics/unique-background-service.md):
  observed issue, target lifecycle contract, state files, replacement flow,
  command changes, and validation plan.

## Related Sources

- [../../../mobile-cloudflare-alpha.md](../../../mobile-cloudflare-alpha.md)
- [../../../mobile-cloudflare-alpha.zh.md](../../../mobile-cloudflare-alpha.zh.md)
- [../../baseline/runtime-flows.md](../../baseline/runtime-flows.md)
- [../../baseline/storage-and-state.md](../../baseline/storage-and-state.md)

## Scope

In scope:

- A host-wide CCB-owned mobile gateway service manager.
- Exactly one CCB-managed server-wide mobile gateway per host state directory.
- Idempotent `ccb update mobile` behavior that stops/replaces the previous
  managed gateway and waits for the new one to become healthy.
- Stale pid/state cleanup.
- Clear refusal when `127.0.0.1:8787` is occupied by a non-CCB process.
- Tests for replacement, stale state, external port occupancy, and lock
  behavior.

Out of scope for the first slice:

- Managing Tailscale Serve or Cloudflare Tunnel processes.
- Killing unknown processes that happen to use the same port.
- Changing CCB Mobile API routes or pairing/token semantics.
- Replacing project-scoped `ccb mobile serve` as a foreground debugging command.
