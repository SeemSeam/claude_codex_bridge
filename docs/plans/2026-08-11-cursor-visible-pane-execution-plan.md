# Cursor Visible Pane Execution Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Execute CCB Cursor jobs in the named interactive Cursor tmux pane, wait safely when that pane is busy, and complete jobs from Cursor's top-level transcript without changing any other provider.

**Architecture:** Keep the existing native Cursor subprocess adapter as an opt-in rollback path. Add a Cursor-only pane adapter that loads the existing `.cursor-session`, defers delivery while the current-session transcript has an unfinished turn, sends one anchored prompt to the bound pane, and incrementally observes only top-level Cursor transcript JSONL files. Bind completion to the exact `CCB_REQ_ID` and require a matching `turn_ended` record.

**Tech Stack:** Python 3.11+, pytest, CCB provider adapter interfaces, tmux terminal backend, Cursor Agent transcript JSONL.

---

## Task 1: Build the Cursor transcript reader

**Files:**

- Create: `test/test_cursor_transcript.py`
- Create: `lib/provider_backends/cursor/transcript.py`

**Step 1: Write failing discovery and incremental-read tests**

Add tests that construct a managed Cursor home with:

- a top-level transcript at `.cursor/projects/<workspace>/agent-transcripts/<session>/<session>.jsonl`;
- a nested `subagents/*.jsonl` file that must be excluded;
- a partial final JSON line that must remain unread until its newline arrives;
- a second top-level transcript created after the initial offset snapshot.

Assert that `capture_cursor_transcript_offsets()` records only complete top-level files and `read_new_cursor_transcript_records()` returns ordered `(path, record)` pairs without advancing past a partial line.

**Step 2: Run the new tests and confirm RED**

Run: `PYTHONPATH=lib pytest -q test/test_cursor_transcript.py`

Expected: collection fails because `provider_backends.cursor.transcript` does not exist.

**Step 3: Implement the minimal reader**

Implement:

- `cursor_transcript_root(cursor_home)`;
- `iter_top_level_cursor_transcripts(cursor_home)` using the exact two-level session layout, never `rglob()`;
- `capture_cursor_transcript_offsets(cursor_home)`;
- `read_new_cursor_transcript_records(cursor_home, offsets)` with byte offsets, newline framing, malformed-record tolerance, and deterministic mtime/path order;
- content helpers that extract text from `message.content` lists while ignoring tool records.

**Step 4: Run the reader tests and confirm GREEN**

Run: `PYTHONPATH=lib pytest -q test/test_cursor_transcript.py`

Expected: all tests pass.

**Step 5: Commit**

```bash
git add test/test_cursor_transcript.py lib/provider_backends/cursor/transcript.py
git commit -m "feat(cursor): read visible session transcripts"
```

## Task 2: Model current-session busy and idle state

**Files:**

- Modify: `test/test_cursor_transcript.py`
- Modify: `lib/provider_backends/cursor/transcript.py`

**Step 1: Write failing turn-state tests**

Cover these cases:

- no transcript is idle;
- a current-session user record without a later `turn_ended` is busy;
- `turn_ended: success` and `turn_ended: error` both make the pane idle for the next turn;
- an incomplete legacy transcript older than the current `.cursor-session` binding is ignored;
- malformed and subagent records cannot make the parent pane busy.

The session-file modification time is the lower bound for "current session" so a pre-existing legacy transcript without terminal records cannot block forever.

**Step 2: Run the focused tests and confirm RED**

Run: `PYTHONPATH=lib pytest -q test/test_cursor_transcript.py -k 'turn_state or busy or legacy'`

Expected: failures because current-session turn-state classification is missing.

**Step 3: Implement minimal classification**

Add a small immutable turn-state result and `cursor_pane_turn_state(cursor_home, session_started_mtime_ns=...)`. Inspect only the newest eligible top-level transcript. Treat the transcript as busy only when its latest user turn has no later `turn_ended`. Unknown records are ignored.

**Step 4: Run the transcript suite and confirm GREEN**

Run: `PYTHONPATH=lib pytest -q test/test_cursor_transcript.py`

Expected: all tests pass.

**Step 5: Commit**

```bash
git add test/test_cursor_transcript.py lib/provider_backends/cursor/transcript.py
git commit -m "feat(cursor): classify visible pane turn state"
```

## Task 3: Select visible pane execution by default

**Files:**

- Create: `test/test_cursor_provider.py`
- Modify: `lib/provider_backends/cursor/execution.py`

**Step 1: Write failing adapter-selection tests**

Assert:

- with `CCB_CURSOR_EXECUTION_MODE` unset, `build_execution_adapter()` returns `CursorPaneExecutionAdapter`;
- `CCB_CURSOR_EXECUTION_MODE=headless` returns the existing `NativeCliSubprocessAdapter`;
- unexpected values fail closed with a clear `ValueError` instead of silently changing modes;
- `_build_command()` and `_build_env()` remain available for rollback compatibility.

**Step 2: Run the selection tests and confirm RED**

Run: `PYTHONPATH=lib pytest -q test/test_cursor_provider.py -k execution_adapter`

Expected: the default still returns the headless adapter.

**Step 3: Add a named rollback builder and mode switch**

Refactor the current function into `build_headless_execution_adapter()`. Make `build_execution_adapter()` choose the pane adapter by default and headless only for the explicit environment value. Keep imports lazy enough to avoid a circular dependency.

**Step 4: Run selection and existing rollback tests**

Run: `PYTHONPATH=lib pytest -q test/test_cursor_provider.py test/test_native_cli_provider_execution.py -k 'cursor or execution_adapter'`

Expected: all selected tests pass.

**Step 5: Commit**

```bash
git add test/test_cursor_provider.py lib/provider_backends/cursor/execution.py
git commit -m "feat(cursor): default jobs to visible pane"
```

## Task 4: Send once and complete from the anchored transcript

**Files:**

- Modify: `test/test_cursor_provider.py`
- Create: `lib/provider_backends/cursor/pane_execution.py`

**Step 1: Write failing ready-pane lifecycle tests**

Use fake session/backend objects and temporary transcript files. Cover:

- the exact named session and pane are resolved;
- a ready pane receives exactly one prompt containing `CCB_REQ_ID`;
- offsets are captured before delivery;
- records in a stale transcript and a subagent transcript are ignored;
- the adapter binds only to the new top-level transcript containing the exact anchor;
- assistant text produces progress but not terminal completion by itself;
- `turn_ended: success` emits `ANCHOR_SEEN`, `ASSISTANT_FINAL`, and `TURN_BOUNDARY`, returning the observed reply;
- `turn_ended: error` terminates as failed/incomplete with a provider-specific reason;
- `reply_delivery` completes immediately after visible dispatch;
- `no_wrap` sends the raw body unchanged.

**Step 2: Run lifecycle tests and confirm RED**

Run: `PYTHONPATH=lib pytest -q test/test_cursor_provider.py -k 'pane or transcript or reply_delivery'`

Expected: failures because `CursorPaneExecutionAdapter` is not implemented.

**Step 3: Implement the minimal pane adapter**

Mirror CCB's existing active-pane contract:

- call `prepare_active_start()` and `ensure_active_pane_alive()`;
- resolve `cursor_home` from the managed session;
- wrap ordinary prompts with `wrap_native_prompt()` and the exact request anchor;
- snapshot transcript offsets before `send_prompt_to_runtime_target()`;
- persist `matched_transcript_path`, `anchor_seen`, `reply_buffer`, `prompt_sent`, and sequence state;
- parse only new transcript records and ignore all records before the matched user anchor;
- complete only on a later `turn_ended` from that same transcript;
- use `clean_native_reply()` before returning assistant text;
- interrupt only the bound pane on cancellation after delivery.

Restore diagnostics must state that interrupted in-flight jobs require resubmission.

**Step 4: Run lifecycle tests and confirm GREEN**

Run: `PYTHONPATH=lib pytest -q test/test_cursor_provider.py`

Expected: all tests pass.

**Step 5: Commit**

```bash
git add test/test_cursor_provider.py lib/provider_backends/cursor/pane_execution.py
git commit -m "feat(cursor): execute jobs in visible pane"
```

## Task 5: Defer safely while the Cursor pane is busy

**Files:**

- Modify: `test/test_cursor_provider.py`
- Modify: `lib/provider_backends/cursor/pane_execution.py`

**Step 1: Write failing deferred-delivery tests**

Cover:

- `start()` accepts a job but sends nothing when the current-session transcript is busy;
- repeated busy polls do not send;
- after a new `turn_ended`, the next poll snapshots offsets and dispatches exactly once;
- the wait timer is distinct from the execution timer;
- `CCB_CURSOR_READY_TIMEOUT_S` and `CCB_CURSOR_RUN_TIMEOUT_S` accept positive finite values and fall back safely otherwise;
- readiness timeout terminates without sending;
- run timeout after anchor/reply evidence terminates without resending;
- a dead pane fails promptly;
- cancellation before delivery does not send `Ctrl-C`, while cancellation after delivery interrupts the exact pane.

**Step 2: Run the deferred tests and confirm RED**

Run: `PYTHONPATH=lib pytest -q test/test_cursor_provider.py -k 'busy or defer or timeout or cancel'`

Expected: failures because deferred dispatch and separate timeout handling are incomplete.

**Step 3: Implement deferred dispatch**

Store the pending prompt when busy. On each poll, re-check the current-session turn state. When it becomes idle, capture fresh offsets immediately before a single send and reset `started_at` for the run timeout. Preserve `prompt_sent=False` through readiness timeout and never infer success from elapsed quiet time.

**Step 4: Run Cursor tests and confirm GREEN**

Run: `PYTHONPATH=lib pytest -q test/test_cursor_provider.py test/test_cursor_transcript.py`

Expected: all tests pass.

**Step 5: Commit**

```bash
git add test/test_cursor_provider.py lib/provider_backends/cursor/pane_execution.py
git commit -m "feat(cursor): wait for busy panes before dispatch"
```

## Task 6: Regression verification and authenticated three-Cursor smoke test

**Files:**

- Modify if needed: `docs/plans/2026-08-11-cursor-visible-pane-execution-design.md`
- Create if useful: `scripts/smoke_cursor_visible_panes.sh`

**Step 1: Run formatting and focused provider suites**

Run:

```bash
git diff --check
PYTHONPATH=lib pytest -q test/test_cursor_provider.py test/test_cursor_transcript.py test/test_native_cli_providers.py test/test_native_cli_provider_execution.py test/test_v2_provider_core_registry.py test/test_v2_runtime_launch.py -k 'cursor or native_cli'
```

Expected: no whitespace errors and all selected tests pass.

**Step 2: Run the broader provider regression suite**

Run:

```bash
PYTHONPATH=lib pytest -q test/test_grok_provider.py test/test_pi_pane_execution.py test/test_v2_provider_core_registry.py test/test_v2_runtime_launch.py
```

Expected: all tests pass; no non-Cursor adapter behavior changes.

**Step 3: Run an authenticated source-tree smoke test**

Use a temporary project with three named Cursor agents and distinct `startup_args --model` values. Launch CCB from this worktree, submit concurrent anchored requests to two non-controller agents, and verify all of the following before changing the installed release:

- both target tmux panes visibly show their full execution;
- neither request creates a headless `agent --print` job process;
- each returned reply comes from the transcript containing its exact request anchor;
- a deliberately busy pane queues the next request and dispatches it exactly once after idle;
- each pane's launch command retains its configured model argument.

If credentials or provider availability block the live smoke test, do not install globally; report the exact blocker and preserve the tested branch.

**Step 4: Install the verified source build and validate the user's project**

After the smoke test passes, install from this worktree using the repository's documented installer, restart the user's CCB project, run `ccb doctor`, and submit one short request to each configured Cursor agent. Confirm visible pane activity and correct anchored replies.

**Step 5: Final verification and commit**

Run:

```bash
git status --short
git log --oneline --decorate -6
```

Commit only any test/support documentation created during smoke testing. Do not push to an upstream repository without an explicitly configured writable fork.
