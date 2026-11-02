# OpenCode v2 native task support

Mode: locally qualified (2026-11-02). User authorized implementation after the
v1/OMP slice `d78fd31bd`; prior local-install-before-commit sequencing remains.

## Scope and source authority

Pinned OpenCode v2.2.4 (`45898d0f8569225eb1ddb4ee13f8581b34dff311`):
`packages/protocol/src/groups/{session,message}.ts`,
`packages/core/src/session/{session,execution,message-updater}.ts`.

Use `/api/info`, exact `/api/session/{id}`, paginated ordered native messages,
and `/api/session/{id}/prompt`. No provider SQL opens, table renaming, invented
parentID, auth discovery, global service restart or provider replacement.
The pinned v2 executable is installed only in a CCB preview directory.

## Binding and completion decisions

- Opt-in version 2 API origin and exact native session/workspace binding.
  Managed launch preserves matching explicit --server/--session inputs; missing
  binding or unsupported service version fails before sending any task.
- Native task submission supplies a fresh message ID and CCB job metadata plus
  the normal request marker; queue delivery avoids steering existing work.
  A response is an admission receipt, never completion. Lost responses are
  observed by exact ID, never replayed or injected through the terminal.
- Native timeline order is the v2 authority. Observe only the exact admitted
  input and its subsequent records up to the first native idle. Another user,
  synthetic, shell or other task-changing input in that interval is ambiguous
  and must fail closed. Do not synthesize a v1 assistant parent.
- Assistant time.completed / tool-calls alone cannot finish a job. Require an
  explicit native idle outcome, final visible assistant text and no unresolved
  tool state. Failed/interrupted/empty outcomes terminate explicitly.
- Paginate with bounded budgets; validate envelopes, IDs, unique messages,
  session/workspace identity and cursors. Truncation/malformed pages cannot
  authorize completion. Preserve v1 and legacy behavior.

## Acceptance and rollout

Tests cover v1 regression, exact native admission, lost receipt/no replay,
foreign/queued input, multi-step tools, failed/interrupted/empty terminal,
streaming previews, pagination and incompatible service envelopes. Run actual
v2 isolated service tests and locally installed CCB acceptance before commit.
No busy production restart or business prompt replay; no push/release.


## Landed scope and remaining limits

Implemented the explicit 2.2.x reader, one-shot native task admission, native
idle completion and launcher binding. Authentication uses only an explicit
environment-variable reference. Native polling is independent of TUI liveness.
HTTP failures are bounded by continuous failure time and never cause replay.
Queued cancellation handles native 204 responses and detects raced projection;
active exact-ID interruption and restart-time observation restore remain
unsupported. This slice does not automatically migrate existing agents or
change Mobile discovery.

Acceptance and the installed preview are recorded in
[local qualification evidence](../evidence/opencode-v2-installed-20261102.md).
The task remains opt-in; stable rollout, push and publication are separate work.
