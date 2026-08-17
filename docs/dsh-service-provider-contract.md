# DeepSeek Harness Service Provider Contract

## 1. Purpose

This document is the authoritative CCB contract for the official DeepSeek
Harness provider key `dsh`.  The existing `deepseek` provider remains the
community Deep Code CLI (`deepcode`) and is a separate integration.

The inspected upstream baseline is:

- repository: `https://github.com/deepseek-ai/deepseek-harness`
- npm package: `@deepseek-ai/dsh`
- executable: `dsh`
- inspected version: `0.1.0-rc.6`
- inspected source commit: `47f943859bef60e4160492346772ded9b24f765a`
- Node requirement: `^22.19 || >=24`

DeepSeek Harness is Developer Preview software.  CCB must fail closed if a
later DSH build no longer provides the carrier or event fields required by
this contract.

The npm package does install a `dsh` bootstrap executable and also ships a
one-shot headless profile.  Neither fact makes DSH a conversational terminal
CLI for CCB purposes: the headless task path creates a fresh persisted Agent
per invocation and cannot provide Codex-like mounted-session continuity by
itself.  CCB therefore uses the structured Web session carrier for native
session reuse, history recovery, approvals, cancellation, and compaction.

## 2. Runtime Boundary

DSH is a service-backed harness, not an interactive terminal provider.  CCB
starts:

```text
dsh web --host 127.0.0.1 --port 0
```

and discovers the selected loopback endpoint from the exact DSH readiness
line.  Unary requests use HTTP POST and native event delivery uses the DSH Web
event WebSocket.

The current POSIX CCB launcher may keep the host wrapper inside the Agent's
managed tmux pane because the existing mounted-runtime lifecycle is
pane-backed.  That pane is only a process-lifecycle and log carrier:

- CCB never submits a DSH prompt with pane input;
- pane capture never supplies a DSH reply;
- pane quietness is never DSH completion evidence;
- host, wrapper, or pane process exit is never successful completion evidence;
- `ccb clear` and `ccb compact` do not send pane keys for DSH.

A future service-process supervisor may replace the pane carrier without
changing the DSH request, session, or completion contract.  Such a change must
first provide equivalent project ownership, liveness, restart, shutdown,
diagnostic, and zero-orphan guarantees.  Merely setting an Agent's
`runtime_mode` to `headless` is not a service supervisor and must not be
advertised as one.

The DSH host binds only to loopback.  CCB rejects non-loopback, credentialed,
path-bearing, query-bearing, or otherwise ambiguous endpoint records and
bypasses proxy environment variables for local RPC.

## 3. Managed State And Authority

Each CCB Agent owns one isolated managed `DSH_HOME`.  The launcher projects
only allowlisted inputs into that home:

- `.credentials.yaml` and `.env` when auth/API inheritance allows them;
- `settings.yaml` when config inheritance is enabled;
- optional user skills, Role skills, and required CCB control skills under
  `skills/`;
- the generated CCB memory bundle at `AGENTS.md`.

Projection is one-way.  CCB does not copy a user's native session store,
caches, or arbitrary DSH home tree, and managed DSH writes must not flow back
to the user's home. `DSH_AGENTS_HOME` points to a separate empty managed
compatibility root, so DSH neither leaks the user's ambient `~/.agents` tree
nor scans the managed `DSH_HOME/skills` root twice.

The Agent session binding records the exact CCB project, Agent, workspace,
managed home, host endpoint-state path, native DSH session id, context
generation, model selection, reasoning effort, permission mode, and
credential/API authority fingerprint.  Restore may reuse a native session id
only when all of those identity boundaries still match.

## 4. Request Submission

For CCB job `J`:

1. CCB opens the DSH event WebSocket before prompt submission.
2. CCB creates or joins the exact managed DSH session.
3. Model/provider/reasoning selection is applied with the native session RPC
   when configured.
4. CCB submits `session.prompt` with RPC id `J`.
5. The prompt retains the leading `CCB_REQ_ID: J` correlation line even for a
   no-wrap CCB request.

The RPC id is the primary native request identity.  The prompt line is an
independent correlation fence and must not replace native event identity.

## 5. Native Completion Authority

A DSH job succeeds only when one session and one native turn provide every
item below:

1. a committed `user/message` has `source.kind == "user"` and
   `source.rpcId == J`;
2. its text begins with the exact `CCB_REQ_ID: J` line;
3. the message binds to the currently open `turn/start.turn`;
4. a committed append-form `assistant/message` for the same turn contains a
   non-empty assistant-visible text reply;
5. `turn/end` for the same turn has `reason.kind == "completed"`.

Reasoning blocks, tool content, another RPC, another session, another turn,
replacement/provisional projections, process exit, pane output, and elapsed
quiet time cannot satisfy this gate.

The native terminal kinds are `completed`, `aborted`, `blocked`, `error`,
`max-tokens`, and `interrupted`.  Only `completed` can succeed, and it still
requires the exact anchor and non-empty reply.  Every other terminal kind,
missing terminal, empty reply, malformed event, missing outcome, timeout, or
transport failure becomes failed or incomplete evidence; it never becomes a
successful CCB completion.

## 6. Restore And Cancellation

The execution adapter persists the exact DSH session id, RPC id, request
record, endpoint-state path, and append-only observation artifact.  After a
ccbd restart it may reconnect an observer to that same request and page
`session.history` backward with `beforeSeq` until the exact RPC is found.

Restore never reposts the prompt.  It reconstructs an already terminal turn,
continues observing an open exact turn, or fails closed when the binding or RPC
cannot be proven current.

CCB cancellation sends native `session.cancel` for the bound session when the
host is reachable and terminates the local observer.  Cancellation is not
successful answer completion.

## 7. Context And Interaction Controls

- DSH has native `/compact`.  `ccb compact <agent>` invokes it through the
  typed `commands/execute` Web Remote endpoint, requires a paired native
  command id plus `result.kind == success`, and never sends it as a model
  prompt or types into the host pane.
- DSH has no native `/clear`.  `ccb clear <agent>` rotates the managed native
  session id and context generation without deleting old DSH logs, CCB jobs,
  workspaces, credentials, or project memory.
- A DSH-only clear or compact request does not construct or require a tmux
  backend. Mixed-provider requests initialize terminal control only for the
  interactive provider targets that need it.
- Both operations use the existing CCB outstanding-work gate and must not
  mutate a busy or queued Agent context.
- With auto-permission enabled, a bound DSH approval may be answered only as
  `allowed-once`.  Otherwise it is rejected and surfaced as blocked.
- DSH question requests are never guessed or automatically answered.  CCB
  returns the native `/api/respond` client-response error with
  `code == "cancelled"`, sends `session.cancel`, and reports the turn as
  requiring interaction.

## 8. Platform Boundary

The first qualified carrier is the existing POSIX/tmux lifecycle on Linux,
macOS, and WSL-compatible environments.  DSH remains outside the explicit
Herdr/Windows-native provider allow-list until an actual Windows lifecycle,
carrier, shutdown, and no-residue test passes.  Upstream Windows branches are
not CCB integration evidence.

No DSH change may weaken the independent Linux, macOS, WSL, or Windows runtime
paths.  Unsupported platform/backend combinations fail closed.

## 9. Acceptance Evidence

Required acceptance includes:

- provider registry, command, session, model, reasoning, API, update, home,
  skill, config UI, and storage classification tests;
- launch tests proving the host command contains no prompt submission;
- reducer and execution tests for exact request/turn binding, every native
  failure terminal, empty replies, missing native anchors, and process exit
  without `turn/end`;
- observer-only restore tests proving the prompt is not reposted;
- clear/compact tests proving DSH uses its API/session control path rather than
  pane input;
- an isolated official-host probe from the external CCB test project.  Without
  credentials, a durable native request followed by `turn/end(error)` is valid
  fail-closed transport evidence, not an authenticated answer-success claim.

The 2026-08-14 qualification used official `@deepseek-ai/dsh@0.1.0-rc.6`
from `/home/bfly/yunwei/test_ccb2`.  The focused cross-module suite passed 451
tests.  The external source-runtime probe passed diagnosis, mount, exact
session restart, keyless native-error rejection, session rotation, and native
compact; the final project kill left no DSH wrapper, child, observer, socket,
or mounted lifecycle residue.  This evidence does not claim an authenticated
answer because no user-owned DeepSeek credential was supplied to that probe.
