# Mobile device project authorization — Issue #393

Status: ready-check; authorization policy awaiting owner selection.
Date: 2026-10-15
Issue: https://github.com/SeemSeam/claude_codex_bridge/issues/393

## Confirmed behavior

A temporary loopback HTTP gateway with two registered temporary projects accepts
an upload into project B using a device claimed for project A with only
`file_upload`: HTTP 201, bytes present in B. The same-project control returns 201.
No existing gateway, real project files, or paired-device records were modified.

`MobileGatewayPairingStore.authenticate_device` checks token and operation scopes,
but does not compare the stored `project_id` against the request target.
`MobileGatewayService._authenticate` and `_authenticate_any_scope` do not carry a
target project. State-changing routes therefore cannot distinguish project-bound
and host-wide devices.

However, cross-project host use is intentional: the existing
`test_agent_file_routes_use_registry_project_id` expressly verifies cross-project
upload/download. The CLI has separate current-project and server-registry gateway
constructors; the latter pairs using a host identity. A blanket equality check
would break legitimate host-wide Mobile use.

## Proposed boundary

- Project pairing issues an explicit project-bound grant.
- Host/server-registry pairing explicitly issues a host-wide grant; it retains
  multi-project operation scopes rather than bypassing scope checks.
- Carry the grant from pairing records through device records and public metadata.
  Never accept a broader grant from the remote claim request.
- Every project-scoped read/write and terminal-open path checks both grant and
  operation scope before project/client/filesystem side effects.
- Review host-only endpoints, notification filtering/presence and terminal-token
  use separately so they cannot bypass a project-bound grant.
- Decide compatibility for old host-device records and reusable handoffs explicitly.
  Do not infer host-wide privileges from a matching name or silently upgrade old
  project records. Re-pairing may be required for ambiguous historical records.

The owner has been asked to select this boundary or project-only pairing for all
clients. Formal implementation is pending that answer. No runtime fix is claimed.

## Verification gates

- Real HTTP: project A token uploads to A (201), B is denied (403), no B file or
  backend action; explicitly host-wide token can access A and B.
- Direct service and HTTP: messages, focus, lifecycle, terminal-open, provider
  controls, conversation/history, files, and related project APIs deny cross-project
  use consistently; test alternate-scope upload paths too.
- Revoked/invalid token and insufficient-scope checks continue to fail closed.
- Legacy records and reusable-code rotation/reuse follow the selected migration
  policy; terminal WebSocket and Relay admission preserve the same grant.
- Run relevant gateway, terminal, notification, Relay and CLI suites. Real Android
  multi-project UI acceptance remains separate from backend HTTP verification.

## Evidence so far

Temporary reproduction script: `/var/tmp/ccb-issue393-repro.py` (not committed).
Both same-project and cross-project uploads returned 201 with matching disk bytes.
Existing file HTTP and multi-project registry tests: **2 passed, 125 deselected**.
Default `/tmp` pytest setup first failed with disk quota exceeded; rerun used a
fresh `/var/tmp/ccb-393-pytest-20261015` base and disabled pytest cache.

## Reassessment: host pairing contract (2026-10-15)

Owner clarified that cross-project use is allowed and requested analysis before
adding project restrictions. Source inspection confirms the normal host gateway
is already host-wide by behavior: `prepare_server_mobile_gateway` passes
`resolved_host_id` as the service `project_id`, uses `mobile_host_state_dir`, and
sets `mode=loopback_server_registry`. Its summary exposes both `host_id` and
`project_id` with the same host identity. Install and the managed host-service
entrypoints use this constructor. The project-local `ccb mobile serve` constructor
instead supplies only the current-project registry by default.

Thus `project_id` on a device is not presently a declared per-project ACL; on the
host path it is the pairing host identity under a legacy field name. The existing
cross-project test is contract evidence, not proof that intended restrictions
failed. The isolated reproduction proves cross-project behavior, but alone does
not establish an authorization vulnerability under this trust model. It injects
a two-project registry into a service and directly creates a project-labelled
pairing record; it does not reproduce a normal host-pairing identity.

Revised direction: preserve host-wide pairing and multi-project use. First document
and test the host/gateway trust boundary and operation scopes; explicit metadata
may clarify pairing authority without narrowing existing grants. Do not implement
project-level ACLs or require re-pairing merely based on the misleading field name.
Any optional project-restricted devices would be a distinct product capability.
The earlier proposed boundary remains a proposal, not an accepted implementation
requirement. No runtime code has been changed.
