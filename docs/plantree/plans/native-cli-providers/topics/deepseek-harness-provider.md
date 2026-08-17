# DeepSeek Harness Provider (`dsh`)

Date: 2026-08-14

## Decision

CCB integrates the official DeepSeek Harness as a new provider key, `dsh`.
The existing `deepseek` key remains the community Deep Code CLI (`deepcode`)
and is not renamed or repurposed.

DSH is treated as a service-backed harness, not as an interactive terminal
CLI.  The managed runtime starts the official Web profile on loopback and CCB
communicates through its public HTTP and WebSocket carrier.  A tmux pane may
host the process under the current CCB lifecycle, but pane input, pane text,
quietness, and process exit are never reply or completion authority.

The packaged `dsh` executable is a profile/bootstrap surface.  Its one-shot
headless task profile creates a fresh persisted Agent per invocation, so CCB
does not use that route as a substitute for mounted conversation continuity.
The Web session carrier is the native surface that supports stable session
identity, history recovery, interaction control, and `/compact`.

## Verified Upstream Baseline

- Repository: `https://github.com/deepseek-ai/deepseek-harness`
- npm package: `@deepseek-ai/dsh`
- executable: `dsh`
- inspected version: `0.1.0-rc.6`
- inspected source commit: `47f943859bef60e4160492346772ded9b24f765a`
- runtime requirement: Node `^22.19 || >=24`
- managed host command: `dsh web --host 127.0.0.1 --port 0`
- home override: `DSH_HOME` (default `~/.dsh`)
- credentials: `DEEPSEEK_API_KEY`, optional `DEEPSEEK_BASE_URL`, and
  `$DSH_HOME/.credentials.yaml`
- settings: `$DSH_HOME/settings.yaml`
- user skills: `$DSH_HOME/skills`
- user instructions: `$DSH_HOME/AGENTS.md`
- persisted conversations: append-only DSH session storage under
  `$DSH_HOME`
- native context command: `/compact`

The upstream package is Developer Preview software.  CCB therefore validates
the host description and event protocol at runtime and fails closed when the
required carrier or event fields are absent.

## Session And Context Contract

Each CCB agent owns an isolated managed `$DSH_HOME` and a stable native DSH
session id in its `.dsh-session` binding.  A normal provider restart reuses
that exact id and `session.create` resumes the persisted conversation.  A
fresh start or `ccb clear <agent>` rotates to a new DSH session id without
deleting the old DSH log, credentials, settings, skills, CCB jobs, or project
memory.

`ccb compact <agent>` invokes the official `/compact` command through
the typed `commands/execute` Web Remote endpoint; it is not a model prompt and
does not type into the host pane.

Only controlled one-way projection is allowed:

- auth: `.credentials.yaml` and `.env` when `inherit_auth` is enabled;
- config: `settings.yaml` when `inherit_config` is enabled;
- skills: optional user DSH skills, mounted Role skills, and required CCB
  control skills;
- memory: the managed project/Role memory bundle at `$DSH_HOME/AGENTS.md`.

Native session stores, caches, generated profiles, and arbitrary DSH trees are
not copied from the user's source home.

## Exact Native Completion Gate

For CCB job `J`, the bridge uses `J` as the DSH `session.prompt` RPC id and
also keeps the normal leading `CCB_REQ_ID: J` prompt anchor.  Success requires
all of the following evidence from one DSH session and one native turn:

1. the event downlink is open before submission;
2. a durable `user/message` has `source.kind == "user"` and
   `source.rpcId == J`;
3. that message is bound to the currently open `turn/start.turn`;
4. a committed, non-empty `assistant/message` exists for that same turn;
5. `turn/end` for that turn has `reason.kind == "completed"`.

`aborted`, `blocked`, `error`, `max-tokens`, `interrupted`, an empty final
assistant message, a missing RPC anchor, a malformed event, stream loss,
timeout, host exit, or pane quietness never becomes success.

The bridge opens `/api/events.mux` as a downlink-only WebSocket and uses HTTP
POST for unary methods.  On ccbd restore it scans `session.history` backwards
by complete-message pages using `beforeSeq`; an already completed exact RPC is
reconstructed, an open exact turn is observed without reposting, and an absent
RPC fails closed.  No restore path silently duplicates the prompt.

## Permission And Interaction Boundary

When CCB auto-permission is active, an exact DSH `approval/requested` frame for
the current session may be answered `allowed-once` through `/api/respond`.
Without auto-permission it is rejected and reported as an interactive approval
block.  Native user-question requests are not guessed or auto-answered; CCB
cancels the turn and reports the unsupported interactive wait.

## Platform Boundary

The upstream DSH source includes Linux, macOS, and Windows branches, but this
first CCB adapter is qualified on the existing POSIX/tmux lifecycle only.  It
is intentionally absent from the explicit Herdr/Windows-native allow-list
until a real native Windows carrier test passes.  This preserves Windows,
WSL, Linux, and macOS runtime independence rather than treating upstream
portability as CCB integration evidence.

## Acceptance

- provider catalog, command default, runtime/client specs, session binding,
  launch, config parsing, model/thinking shortcuts, update discovery, and
  storage classification tests;
- host wrapper endpoint publication and stale-state tests;
- bridge reducer tests for exact RPC/turn binding, stale and cross-turn event
  rejection, every native terminal reason, empty replies, reconnect/history
  recovery, approval behavior, cancellation, and malformed protocol;
- clear rotation and API-backed compact handler tests;
- isolated source-runtime mount from `/home/bfly/yunwei/test_ccb2` using the
  absolute source `ccb_test` wrapper;
- an authenticated real answer is reported only when user-owned DeepSeek
  credentials are available; a keyless native `turn/end(error)` probe is valid
  transport and fail-closed completion evidence, not an answer-success claim.

## Verification Evidence

On 2026-08-14, the external source project
`/home/bfly/yunwei/test_ccb2/dsh_ccb_source_smoke_20260814` passed source-wrapper
diagnosis, mount, exact-session restart, keyless ask failure, clear rotation,
and native compact checks with official `@deepseek-ai/dsh@0.1.0-rc.6`:

- restart replaced the host instance token and wrapper/child processes while
  preserving the exact native session id;
- the keyless request durably matched its CCB job id and ended with native
  `turn/end(error)`; CCB returned `failed/dsh_native_turn_failed` with exact
  confidence and no reply;
- clear changed the native session id and advanced the context generation;
- compact produced paired `command/run(name=compact)` and
  `command/done(kind=success)` events with the same command id.
- the focused DSH and cross-module regression selection passed 451 tests;
- final project shutdown left zero DSH wrapper, child, observer, socket, or
  mounted lifecycle residue.

This proves the mounted service transport and fail-closed terminal path. It
does not claim authenticated answer success.
