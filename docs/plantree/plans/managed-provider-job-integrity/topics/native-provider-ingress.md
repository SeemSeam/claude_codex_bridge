# Native provider ingress and observation (2026-11-02)

Mode: execute-ready for OMP ingress and safe OpenCode observation; native
OpenCode API compatibility must be capability/shape checked before use.

## Evidence and decisions

- Nozeli executor → frontdesk job `job_ffd523fd1fe7` remained accepted until
  cancelled. Native OMP composer was empty, but the cursor heuristic returned
  `omp_editor_not_focused`. Do not replay the cancelled business request.
- OMP 18.13.3 exposes sendMessage, getEditorText, isIdle, hasPendingMessages.
  Native sendMessage returns void: a return is neither persistence nor business
  completion. Use separate ccb-task durable intent/receipt identity, preserve
  dispatch hash binding, and finish only through existing completion events.
- Issue #401 reports external SQLite shm truncation and OpenCode SIGBUS.
  Read-only URI alone is insufficient; CCB must never open foreign WAL shared
  memory through a writable fallback.
- Issue #402 reports session_v2/session_message/session_part replacing the
  legacy schema. Unknown schema must fail explicitly rather than mean no reply.
- OpenCode native SDK surfaces differ at v1.19.5 and v2.2.4. A native adapter
  must bind directory/session and validate structured messages/parts; never
  infer completion from idle alone or rename database tables speculatively.

## Acceptance and rollout

Native OMP must ignore cursor location, reject busy/pending/draft admission,
prevent duplicate or ambiguous replay, and retain request completion binding.
Legacy OMP retains its guarded terminal path. Test socket bridge and execution
integration with no paid inference. Do not restart busy mounted providers.
OpenCode tests must cover WAL sidecar preservation and explicit unsupported
schema errors, plus native session/parent/completion attribution.
All generated bridges/config remain CCB-owned. No external provider changes,
push, issue mutation, restart or release are part of this slice. The later
user instruction authorizes a commit after successful local installation tests.

## Implemented and verified (local working tree)

- `omp/task_delivery.py` selects capability-advertised task ingress, appends
  dispatch binding before admission, and never falls back after ambiguous
  native I/O. Native owner rejects busy, queued-message, model-change and draft
  races before intent creation. Dedicated `ccb-task` entries and ingress keys
  preserve duplicate protection separately from `ccb-reply`.
- Native composer observation removes cursor dependence only when native task
  ingress is advertised. Legacy transport retains its existing screen gate.
  Corrected native pending-message API to `hasPendingMessages`.
- OpenCode uses private main+WAL query copies instead of foreign SQLite opens;
  missing tables/columns raise before dispatch. Added opt-in loopback v1 API
  adapter preserving exact session/workspace/parent/completion binding.
- OpenCode v2 full completion support remains open: v2.2.4 has no legacy
  assistant parentID; mapping requires its native causal/idle message protocol.
  This patch prevents silent hangs for the reported schema, not full v2 support.

Evidence:

- 382 focused provider/storage/composer tests passed, 2 skipped before the final
  additional two ambiguity/rejection cases. Follow-up execution/launcher/memory
  suites: 97 passed; strengthened native request-marker suite: 6 passed.
- Actual OMP 18.13.3, Bun 1.4.4, isolated PTY + local streamed model fixture:
  native task ingress, held real tool turn, two reply admissions, native JSONL
  receipts, no duplicate/supersession, original anchored completion and intact
  unsubmitted draft all passed (`test/omp_reply_native_smoke.py --native-task`).
- Actual OpenCode 1.19.5 isolated loopback service: session creation and native
  exact-session/directory/messages reads passed without a model request.
- User subsequently authorized local installation qualification and a commit
  after passing tests. [Installed acceptance evidence](../evidence/native-provider-installed-20261102.md)
  records 498 source tests and 148 installed tests passing, plus both actual
  Provider smokes. Stable installed rollout and full OpenCode v2 remain open.
  No mounted-agent restart, cancelled task replay, push or release occurred.
