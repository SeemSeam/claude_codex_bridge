# Cursor Visible Pane Execution Design

## Goal

Route CCB jobs for the Cursor provider through each managed interactive Cursor pane so the full execution is visible and directly controllable, while preserving the current behavior of every non-Cursor provider.

## Problem

CCB currently mounts an interactive Cursor pane but executes incoming jobs in a separate `agent --print --output-format stream-json` subprocess. The subprocess writes output to completion artifacts, so the managed pane remains idle. The subprocess command also does not carry the agent's configured `startup_args`, which means a per-agent `--model` selection is not guaranteed for the job.

The desired topology contains multiple named Cursor agents with different models. A job sent to one of those agents must run in that agent's existing pane and session, not in an unrelated background process.

## Approaches Considered

### 1. Pane injection with transcript-backed completion

Send the anchored CCB request to the managed tmux pane. Observe the isolated Cursor transcript directory, identify the transcript containing the exact request anchor, collect assistant text, and finish only when a matching `turn_ended` record appears.

This is the selected approach. It gives complete pane visibility, preserves the configured interactive model, and supports direct takeover without changing non-Cursor providers.

### 2. Pre-created chat IDs with `agent create-chat` and `--resume`

Create a Cursor chat before launching the pane and bind the pane to that chat ID. This provides an explicit transcript identifier but adds provider-side session creation to every launch and relies on a less mature command path. It also changes existing session startup and recovery behavior unnecessarily.

### 3. Headless execution with a pane log mirror

Keep the current subprocess and mirror its structured output into a terminal pane. This does not provide a real interactive session, cannot support clean takeover, and still leaves model selection split between the visible pane and the headless job.

## Architecture

The change is confined to `lib/provider_backends/cursor/`:

- `execution.py` selects pane-backed execution by default and retains the current headless adapter as an explicit rollback path.
- `pane_execution.py` owns CCB submission, idle waiting, tmux delivery, polling, completion, and cancellation for Cursor panes.
- `transcript.py` discovers and incrementally reads Cursor transcript JSONL files under the managed Cursor home.

No Claude, Codex, Gemini, Grok, shared dispatcher, or generic native CLI execution behavior changes. The existing Cursor launcher remains responsible for starting the interactive pane with the configured `startup_args`, including `--model`.

Set `CCB_CURSOR_EXECUTION_MODE=headless` to select the previous subprocess behavior if a future Cursor CLI release changes its transcript contract.

## Data Flow

1. CCB accepts a job for a named Cursor agent.
2. The Cursor pane adapter loads the exact `.cursor-session` binding and validates the pane and workspace.
3. The adapter inspects both the most recently active top-level transcript and the visible Cursor pane state in the agent's isolated runtime.
4. If the transcript contains an unfinished turn, or the pane reports `Working`/`ctrl+c to stop`, the job remains accepted but unsent. A job that observed an active pane must see a new top-level `turn_ended` and then a stable idle interval before delivery. This covers Cursor versions that do not flush the user transcript until the active turn ends.
5. Once idle, the adapter snapshots transcript offsets and sends a prompt containing the unique `CCB_REQ_ID` anchor to the exact tmux pane.
6. Until the unique anchor appears, polling scans complete top-level transcripts and excludes files that already contained that anchor before dispatch. After binding, polling becomes incremental. This handles Cursor replacing the previous terminal record when appending a new turn. Subagent transcript directories are excluded.
7. Assistant text after the anchored user message is accumulated while the Cursor TUI shows the live reasoning state and tool activity.
8. A following `turn_ended` with `status=success` completes the CCB job. `status=error` fails it. No terminal event means the job remains active.

## Concurrency and Manual Use

CCB continues to serialize jobs per named agent. A job arriving while the target pane is in a manual turn waits rather than becoming a Cursor follow-up/steering message or corrupting the user's input. Transcript evidence is authoritative for the transition out of an observed active turn; pane text is used as an early busy/not-ready signal.

Waiting and execution have bounded timeouts. A dead pane, missing session, malformed transcript, or wait timeout fails closed with a specific diagnostic reason. The adapter never retries or duplicates a prompt after it has observed the request anchor in a transcript.

Manual input during an active CCB-owned turn remains a user takeover action and may steer that turn, matching ordinary Cursor behavior.

## Transcript Contract

The observed Cursor transcript format uses:

- `{role: "user", message: {content: [...]}}`
- `{role: "assistant", message: {content: [...]}}`
- `{type: "turn_ended", status: "success" | "error"}`

For a continuing session, Cursor may remove the prior trailing `turn_ended` before writing the next user record, then write a new terminal record at the end of the combined transcript. The pre-anchor scanner therefore cannot rely on a monotonic old EOF offset.

The parser treats unknown records as ignorable and malformed JSON as incomplete while the file may still be growing. It does not infer completion from pane text, elapsed time, or the presence of an assistant message alone.

Only assistant text is returned as the CCB reply. Tool calls remain visible in the Cursor pane and transcript but are not copied into the caller's reply.

## Compatibility

- Three-Cursor configurations gain visible, model-pinned pane execution.
- Mixed or three-independent-provider configurations keep their current adapters and behavior.
- Existing CCB configuration remains valid.
- The headless rollback mode preserves the previous Cursor execution path.
- Interrupted in-flight Cursor jobs remain resubmit-required because CCB cannot prove safe prompt ownership across daemon restart.

## Testing

Automated tests will cover:

- Cursor selects pane execution by default and headless execution only through the rollback switch.
- A ready pane receives exactly one anchored prompt.
- A busy pane waits and sends only after a terminal event makes it idle.
- An active pane whose user record has not yet been flushed still waits for new terminal evidence.
- Replacing the prior terminal record does not hide the new request anchor.
- The adapter binds only to the transcript containing the exact request anchor.
- Subagent and stale transcript records do not complete the parent job.
- Assistant text is accumulated and returned on `turn_ended: success`.
- `turn_ended: error`, pane death, malformed evidence, and timeout fail closed.
- Cancellation interrupts only the exact bound pane after the prompt has been sent.
- Existing non-Cursor provider and native CLI tests remain unchanged and pass.

A final authenticated smoke test will mount three Cursor agents with different models, submit concurrent jobs to Sol and Grok, confirm that both panes visibly execute, and verify that the returned replies match the corresponding anchored transcripts.

## Rollout

Development and tests run in an isolated source worktree. The existing release installation is not modified until automated tests pass and a local three-Cursor smoke test succeeds. Installation then uses the source checkout so future changes are version-controlled and reversible.
