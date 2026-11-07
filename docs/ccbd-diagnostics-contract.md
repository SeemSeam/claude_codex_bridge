# CCBD Diagnostics Contract

## 1. Purpose

This document defines the non-drifting diagnostics contract for project-scoped startup/shutdown reports, backend logs, and support-bundle export in `ccb_source`.

It is the authoritative design anchor for:

- `.ccb/ccbd/startup-report.json`
- `.ccb/ccbd/shutdown-report.json`
- `.ccb/ccbd/state.json`
- `.ccb/ccbd/start-policy.json`
- `.ccb/ccbd/lifecycle.jsonl`
- `.ccb/ccbd/heartbeats/<subject-kind>/*.json`
- `.ccb/ccbd/maintenance-heartbeat/`
- `.ccb/ccbd/reload-drain.json`
- `.ccb/ccbd/artifacts/text/`
- project-scoped backend log retention under `.ccb/ccbd/`
- `ccb doctor`
- `ccb doctor ps`
- `ccb doctor logs <agent>`
- `ccb doctor --bundle`

The repo-local memory file [AGENTS.md](/home/bfly/yunwei/ccb_source/AGENTS.md) must point to this document instead of duplicating the rules.

## 2. Goals

Diagnostics must let another user reproduce backend state and failure context without interactive shell access to the original machine.

That means the diagnostics surface must answer at least:

- what the project anchor was
- which config was active
- whether the backend was mounted, restarted, recovered, or shut down
- which agents were expected, mounted, degraded, or stopped
- what the daemon and keeper most recently logged
- which authority files and event streams existed at the time of export

## 3. Hard Contract

### 3.0 Windows x64 release surface

`ccb doctor` includes a `windows_x64_release_surface` record loaded from the
installed release root. It is diagnostics, not runtime authority. The record
must expose `release_install_entry`, `source_install_allowed`,
`source_install_entry`, `update_entry`, `managed_python_status`,
`native_helper_status`, and an actionable `next_action`. Missing, malformed,
wrong-host, wrong-architecture, or WOW64 evidence fails closed to a blocked
surface; it must not enable a Windows download or update route.

### 3.1 Project Scope

- Diagnostics are scoped to one `.ccb` anchor.
- All project diagnostics records must live under that anchor's logical `.ccb/ccbd/`, even when the physical runtime state root is relocated on WSL mounted-drive projects.
- Runtime-root marker and reference files are part of the project diagnostics evidence chain and must be mapped back into the logical `.ccb` archive path in support bundles.
- Project-local provider state under `.ccb/agents/<agent>/provider-state/` is diagnostics evidence and should be exported when it is relevant to session isolation or binding analysis.
- Diagnostics export must never merge multiple project anchors into one bundle.

### 3.2 Startup Report

Path:

- `.ccb/ccbd/startup-report.json`

The latest startup report must capture the most recent startup-related transaction, including:

- `trigger`
  - at minimum `daemon_boot` or `start_command`
- `status`
  - at minimum `ok` or `failed`
- `generated_at`
- `daemon_generation`
- optional `daemon_started`
  - whether the foreground `ccb` command had to start a new daemon
- optional `startup_run_id`
  - a `start_<uuid>` correlation identity generated once per foreground start
    command and echoed by the start RPC; legacy daemon-boot and old-client
    records may omit it
- `requested_agents`
- `desired_agents`
- `actions_taken`
- `agent_results`
  - each result may include `duration_ms`, `provider_prepare_ms`,
    `provider_prepare_count`, and a machine-readable `binding_reject_reason`
  - result `health` reflects the final runtime authority after restore
    bookkeeping, not the earlier binding-classification intent; both
    `healthy` and `restored` are successful runtime health values
  - each result may also include a non-negative `timings_ms` map with the
    accurately observed `prepare_launch_context`, `build_start_cmd`,
    `tmux_respawn`, `pane_identity`, `session_write`,
    `provider_post_launch`, `binding_resolve`, `pane_and_runtime_facts`,
    `authority_commit`, `restore_bookkeeping`, and `unattributed` boundaries
  - current-source start results persist every named Agent timing key; a stage
    that did not execute is `0.0`, while legacy records may omit keys
  - `prepare_launch_context` spans runtime-directory/session-id preparation,
    provider `prepare_runtime`, run-cwd resolution, and the optional provider
    `prepare_launch_context` hook; it is not limited to the provider hook
- `timings_ms`
  - non-negative stage durations, including `namespace_ensure`,
    `context_and_layout_plan`, `tmux_namespace_runtime`,
    `agent_prepare_and_classify`, `tmux_layout`, `active_panes_and_cmd`,
    `agent_runtime_commit`, cumulative `agent_runtime_*` substage totals,
    `tmux_cleanup`, `flow_total`, and `supervisor_total` when those stages were
    reached
- optional `readiness_timeline`
  - diagnostics schema `1` correlates one `trace_id`, `startup_run_id`, keeper
    startup id, expected/actual daemon generation, attach mode, Agent scopes,
    RPC-accept offset, and T0-T6 point records on one host monotonic origin
  - persisted point records contain only relative milliseconds, status,
    provenance source, and an optional Agent-name scope; the absolute
    `origin_monotonic_ns` and exact keeper-acceptance performance-counter value
    are transient inputs and must never be persisted
  - `timeline_complete=true` requires a matching positive daemon generation,
    an observed RPC-accept offset, complete point/status/duration shape,
    exact T4 requested-Agent and T6 desired-Agent scopes, and monotonic
    `T2 <= RPC <= T3 <= T4 <= T6` ordering
  - source-test `--no-attach` records T5 as
    `not_applicable_no_attach`; it must not synthesize foreground attach or
    first-frame latency
  - a cold T1 is exact only when the diagnostics checkpoint sampled immediately
    after keeper's durable `phase=starting` commit matches startup id,
    generation, current daemon lease identity, and timeline ordering.  It uses
    status `reached` and source `keeper_lifecycle_starting_committed`.  Without
    that proof, the compatible-daemon observation remains
    `observed_upper_bound` at T2; an already-mounted start uses
    `not_required_already_mounted`
  - daemon boot ping evidence must expose lifecycle `startup_stage` together
    with serving PID, daemon instance, lease generation, in-memory startup
    generation, and accepted startup id.  `starting/runtime_bootstrap` is
    bounded progress after a successful self-ping and mounted lease; it is not
    a completed T2.  Only `phase=mounted/startup_stage=mounted` with matching
    durable and serving identity is caller-ready.  Final lifecycle persistence
    and opening the normal-RPC gate are dispatch-atomic: a ping that arrives in
    the persistence window waits, and a failed publication (including an error
    after file replacement) returns a stopping/unavailable error rather than a
    mounted readiness payload
- optional `operation_counts`
  - a non-negative integer map collected only while the current daemon-boot or
    start-command request owns the startup collector

  - `tmux_backend_command_attempt_count`, `tmux_backend_command_count`,
    `tmux_backend_subprocess_spawn_count`,
    `tracked_startup_subprocess_spawn_attempt_count`, and
    `tracked_startup_subprocess_spawn_count` cover calls routed through
    `TmuxBackend._tmux_run`; `tracked_startup_subprocess_*` is deliberately not
    a process-wide subprocess total
  - `atomic_durable_write_attempt_count`, `atomic_durable_write_count`,
    `atomic_durable_write_byte_count`, and
    `atomic_durable_write_skip_count` cover the shared `storage.atomic` path;
    direct file writes, copies, and JSONL appends are outside this counter
  - `provider_prepare_attempt_count`, `provider_prepare_count`, and
    `provider_prepare_atomic_write_*` cover the explicit provider preparation
    boundary and atomic writes inside it; these are not projection-file totals
  - `orphan_cleanup_pass_count`, `orphan_cleanup_skip_count`,
    `orphan_cleanup_socket_scan_count`, and the owned/orphan/killed pane counts
    cover the explicit startup orphan-cleanup pass
  - `startup_report_write_attempt_count` covers the enclosing report write
    attempt; the report's own atomic write is intentionally excluded from
    `atomic_durable_write_*` in that same payload to avoid recursive rewrites

Daemon-boot latest-report publication is lifecycle-fenced.  A child may replace
the latest report only while its keeper startup id and generation are still the
current transaction and its lifecycle phase agrees with the reported terminal
status.  A superseded child's late failure must not overwrite a newer
generation's successful report.  The report's `inspection.startup_id` and
`daemon_generation` provide the durable correlation for daemon-boot records;
foreground `start_command` records continue to use `startup_run_id`.

- `inspection`
- `socket_placement`
  - at minimum preferred/effective socket paths plus root kind and fallback reason for both `ccbd` and project tmux socket selection
- optional `failure_reason`

Rules:

- daemon boot must write a startup report
- foreground `start` must overwrite it with the more specific `start_command` report
- the daemon/control-plane request lane is the sole startup-report writer for
  `start_command`; the foreground CLI must not load and rewrite the latest
  report after its RPC because a later start may already own that path
- a new start client must require its RPC response `startup_run_id`, persisted
  report `startup_run_id`, and foreground observation `startup_run_id` to
  match before treating timing evidence as the same transaction; a missing
  response identity is tolerated only for compatibility with an older daemon
- accepted binding reuse must report `provider_prepare_count=0`; launch and
  relaunch through the normal managed start path must report exactly one
  provider preparation pass
- accepted binding reuse must preserve an existing successful `healthy` or
  `restored` runtime health value. A no-op start must not toggle restored
  provenance to healthy or create an authority write solely for that toggle
- timing fields are diagnostics only, must not affect lifecycle authority, and
  malformed/non-finite persisted values must be ignored by readers
- readiness tracing is diagnostics only and first-observation-wins.  A stale,
  malformed, future-origin, generation-less, duplicate, or out-of-order trace
  must not change startup authority; benchmark readers reject it rather than
  repairing or reordering the evidence
- the structural readiness gate must count cold T1 upper bounds, exact keeper
  checkpoints, and warm not-required records separately.  Every cold timeline
  needs an exact checkpoint to satisfy the Goal's formal-readiness gate; an
  upper bound may prove correlation but cannot satisfy that gate
- operation counters are diagnostics only, must not affect startup behavior or
  authority, and malformed, fractional, boolean, or negative persisted values
  must be ignored by readers
- the request-scoped collector uses context-local propagation. It does not
  automatically follow work submitted to a new thread; future concurrent
  startup work must explicitly propagate or pass the collector before its
  counters can be considered complete
- until narrower request-scoped hooks exist, no startup report may claim a
  global subprocess total, process-snapshot total, helper-spawn total, or
  provider projection file/byte total by inferring those values from Agent
  launches or provider preparation
- per-Agent timing fields are additive rather than nested totals; their sum
  must not exceed `duration_ms`, and setup or compatibility paths without an
  accurate finer hook are counted only as `unattributed`
- when an Agent launch fails after its identity is known, the failed startup
  report must retain one structured Agent result with `action=failed`, provider,
  original failure reason, completed additive timings, and duration; diagnostic
  propagation must not replace or wrap the original startup exception
- `ping('ccbd')` and `doctor` must surface the latest startup timing map,
  request-scoped operation-count map, per-Agent timing maps, and aggregate
  provider preparation count when a startup report is available
- non-interactive foreground output must expose a stable JSON-safe CLI timing
  map covering `cli_pre_rpc`, `daemon_ensure`, `start_rpc`, `cli_post_rpc`, and
  `cli_total`; synchronous sidebar refresh, layout status, and maintenance
  heartbeat work belongs to `cli_post_rpc`, while render and attach latency is
  outside that map and may be reported as externally unattributed wall time
- source-benchmark process bootstrap timings are a separate optional surface;
  they must not be inserted into the stable CLI phase map.  When the source
  benchmark enables them, foreground output includes a random
  `startup_process_trace_id` plus
  `startup_process_bootstrap_timings_ms` with the non-overlapping stages
  `popen_begin_to_ccb_test_entry`, `ccb_test_entry_to_pre_exec`,
  `ccb_test_pre_exec_to_ccb_py_entry`, `ccb_py_entry_to_main`, and
  `ccb_py_main_to_cli_start`
- process bootstrap tracing is accepted only through the guarded source-test
  wrapper.  Absolute monotonic timestamps are consumed before the normal CLI
  import fan-out and must not reach output, reports, daemon/provider child
  environments, or benchmark artifacts.  A malformed or forged trace must not
  alter product startup; the benchmark instead rejects a profiled sample that
  lacks the complete durations or whose emitted trace id differs from the id
  retained by its `Popen` owner
- startup report write failure must not replace the original startup error with a diagnostics-only error
- when project tmux preparation fails, `failure_reason` must preserve the user-facing startup failure plus tmux command context, the effective tmux socket path, socket path byte length when known, and original tmux stderr/stdout detail when available

#### External startup benchmark artifacts

The source-only startup benchmark writes its raw evidence to an explicitly
owned external test fixture, not into the live project's `.ccb` authority.  A
round may contain `run.json`, an immutable startup-report snapshot (or the
explicitly named unchanged S0 sentinel snapshot),
`scenario-construction.before.json`, `scenario-construction.ready.json`, the
current/final `scenario-construction.json`, and `resource-profile.json`; final
cleanup may additionally produce `cleanup-resource-audit.json`.

Rules:

- before any scenario constructor can mutate runtime state, the benchmark must
  atomically publish and durably directory-sync the immutable `before` phase.
  After the constructor it publishes an immutable `ready` phase whose
  predecessor is the `before` SHA256.  The terminal manifest retains both
  immutable references and names the `ready` SHA256 as its predecessor.  A
  start-spawn exception therefore leaves a non-passing `ready` record rather
  than silently losing the attempted round
- the terminal scenario reference in `run.json` binds benchmark id, round
  ordinal, CLI scenario, scenario id, variant, instrumentation arm, artifact
  path, validation status, and SHA256.  Summary construction reopens the final
  and both immutable phase artifacts, verifies their digests and predecessor
  chain, and checks the binding against the corresponding run.  Missing,
  swapped, tampered, pass-with-reasons, or orphan attempted-round artifacts
  fail the scenario gate
- scenario identity is a stable double-read of lifecycle, lease, namespace,
  and configured runtime authority.  Mixed record types, project ids, config
  signatures, or daemon generations fail closed.  Raw Agent names, PIDs,
  provider prompts, runtime records, and paths are not persisted; identity
  equality uses HMAC digests under a fresh non-exported benchmark key, while
  only aggregate record/live-process counts and sanitized authority state are
  written
- the implemented scenario constructors are S0 CLI-only hot path, S1 warm
  attach, S3 deterministic mixed recovery, S4 official full-cold reset, and
  S5a preflight-pristine cold.  S0 first uses the ordinary cold prime to create
  one healthy mounted fixture, then freezes daemon, namespace, generation,
  every configured runtime identity, and the full startup-report file identity.
  Its measured command is exactly `ccb_test --print-version`: it emits no
  startup id, consumes no startup process trace, and performs no RPC.  Its
  resource sampler must observe exactly one newly created command-process
  identity across its snapshots.  That count is a sampled lower bound, not an
  event-complete proof that no process lived entirely between snapshots; the
  no-subprocess boundary instead rests on the wrapper `exec`, the early version
  return in the CLI entrypoint, and (on Linux) a separate process-syscall trace.
  Every before/ready/after/final audit must match the one frozen baseline, and
  even a same-bytes report rewrite fails because inode/ctime/mtime identity is
  part of the sentinel proof.  The old report is evidence of preserved state
  only and must never be attributed to the measured command; readiness T1-T6
  and supervisor/Agent statistics are explicitly not applicable.  S1/S3 prime
  and S4 publish
  their `before` phase before invoking official `ccb_test kill`; their ready
  state requires consistent stopped/unmounted authority, a non-attachable
  namespace, zero active runtime records, and a clean bounded full-discovery
  process audit.  S5a additionally requires absent ccbd/Agent state and an
  empty isolated source home.  Ordinary S1 rounds require the same daemon,
  namespace, generation, and live configured runtime identities before and
  after the command.  Cold rounds require mounted/live authority afterward and
  newly created or changed daemon, namespace, generation, and Agent runtime
  identities; a relabelled same-generation daemon is not a cold sample
- S3 is available only for an explicitly owned external fixture with at least
  two configured deterministic provider stubs and serial launch authority.  A
  caller-supplied global or Provider-specific `STUB_LAUNCH_*` control makes
  preflight fail; Agent selection, delay, barrier, cancel, failure, probe path,
  and run identity for this scenario are harness-owned.  A
  source-test-only failure latch is projected through the already validated
  provider `*_START_CMD`: the selected target launch is held alive until the
  official single-Agent `ccb_test restart` RPC has returned, then released to
  fail.  This prevents the restart handler's own recovery checks from consuming
  the foreground compensation launch.  The ready phase requires exactly that
  pseudonymous target slot dead, every peer slot live and unchanged, and the
  same daemon/generation/namespace.  The measured start must produce exactly
  one target `relaunched` result with `pane_dead` and one provider prepare,
  attach every peer with zero prepare, preserve namespace and peer identities,
  and advance the target probe sequence from selected failure match 2N to
  non-selected recovery match 2N+1.  Any automatic supervision recovery event
  after the frozen cursor, peer mutation, project-wide topology rebuild, or
  probe mismatch fails closed
- each S3 round copies the raw launch-probe snapshot into its access-restricted
  `0700` run directory as a `0600` file and binds its SHA256 from the final
  manifest.  Agent names and PIDs may exist only in that raw external snapshot;
  scenario manifests retain HMAC slot/identity values and aggregate counters
- each usable startup-transaction resource profile must bind the benchmark
  coordinates, stdout `startup_run_id`, persisted startup-report
  `startup_run_id`, and startup report digest.  S0 is the sole no-transaction
  exception: its profile binds benchmark coordinates, profile id, command
  output hash, frozen authority token, and identical before/after sentinel
  identity while both startup ids remain absent.  A profile supplied for S0
  must observe exactly one newly created process identity across sampled
  snapshots.  This count has the process-sampling lower-bound semantics stated
  below and cannot by itself exclude a process whose complete lifetime falls
  between snapshots.  Any identity, digest, or observed-process-count mismatch
  is measurement-integrity failure rather than a resource zero
- for a profiled source startup, `run.json` separately records the correlated
  process trace id and duration map.  If `B` is the sum of the five process
  bootstrap durations, `C` is `cli_total`, and `W` is the foreground command
  wall, then `post_cli_residual = W - B - C`; external named attribution is
  `B + C`.  `external_minus_cli_total = W - C` remains available for
  compatibility but includes both bootstrap and post-CLI residual.  A residual
  below the bounded floating-point tolerance is measurement-integrity failure
- the CLI timing partition must fit inside `cli_total`, and the synchronous
  post-RPC subpartition must fit inside `cli_post_rpc`; readiness milestones
  must fit inside the independently measured foreground command wall
- the resource sampler's command wall and the benchmark runner's outer wall
  are independent observations.  Their signed difference is retained as
  `sampler_and_runner_overhead_ms`; a materially negative value rejects the
  profile rather than being clamped to zero
- profile, spawn, command-exit, and profile-end timestamps must be monotonic,
  and every retained sample must lie inside the profile window.  Reversed
  timestamps or out-of-window samples make the profile unavailable
- Linux resource sampling reads `/proc` directly and identifies a process
  instance by PID plus `/proc/<pid>/stat` start ticks; it must not persist argv,
  cwd, environment values, provider prompts, or raw procfs text
- CPU ticks and process I/O derived from periodic process snapshots are
  labelled sampled lower bounds; command-child rusage, sampled aggregate peak
  RSS, process-count peak, capability gaps, scan overhead, and scheduling
  misses remain separate fields
- `/proc/<pid>/io` counters are monotonic only within the same PID/start-ticks
  identity.  During one profile window the sampler may keep at most 256
  read-only, close-on-exec `/proc/<pid>/io` handles.  Each handle is opened
  relative to a no-follow proc directory handle and accepted only after a stat
  read from that same directory proves the expected PID/start-ticks identity;
  later samples use a bounded offset-zero read.  This prevents PID reuse from
  rebinding a handle and lets a handle opened while the process was alive read
  the real final counters while that process is a zombie, when a fresh procfs
  open would fail.  During targeted command-window discovery, handle
  acquisition happens immediately after the stat identity read and before
  slower cmdline/executable/cwd classification, so classification work does not
  recreate the same exit race.  Handles are never inherited or persisted and
  are closed on success, timeout, and exception paths.  Limit, open, read, reuse, prime, and
  identity-mismatch counts are aggregate diagnostics only
- the sampler aggregation keeps a last-valid I/O value per identity and field:
  a transient unavailable read followed by a valid non-regressing value, or a
  valid final read from an already-open identity handle, recovers the full
  sampled delta without an additional I/O observation.  Missing baseline
  values, terminal values that the stable handle cannot read, identities that
  never produce a valid value, and counter regressions remain unresolved and
  force `process_io_partial`.  Raw unavailable events, recovered identity-field
  gap sequences, unresolved gaps/identities, and counter regressions are
  reported separately.  Unresolved identity-field gaps are additionally
  partitioned into mutually exclusive baseline, terminal, never-valid, and
  regression counts without persisting process identity or raw procfs text;
  the partition counts sum to the unresolved-gap count.  The formal gate is
  driven by unresolved gaps, not by a recovered transient event.  Process I/O
  remains a sampled lower bound because a process can still be created and
  fully exit between samples
- the pre-spawn full-discovery baseline is outside the measured command wall;
  sampling during the command follows the foreground root, current runtime
  authority, previously observed process identities, and their descendants so
  a full-machine scan is not added to every sampling interval
- repeated benchmark rounds keep two identity sets: the next round is seeded
  only with process identities still present in the prior terminal snapshot,
  while the final cleanup audit retains the cumulative observed identities.
  Dead foreground identities must not accumulate in active sampling work;
  each persisted sample reports only an aggregate unavailable-I/O event count.
  Within each targeted snapshot, the foreground root and its discovered
  descendants are read before persistent project peers.  Stable identity
  handles remove the observed live-to-zombie stat/I/O tear without increasing
  the number of I/O observations; a first-seen zombie or a process wholly
  between samples remains explicitly outside that recovery guarantee
- unavailable or partial resource telemetry cannot turn a successful startup
  into a successful formal performance claim, and it must not replace an
  original startup timeout, exit error, or report-validation failure
- foreground sampler execution owns and reaps its direct `Popen`; timeout
  handling is bounded terminate/kill/reap logic so descendants retaining an
  inherited pipe cannot make the benchmark wait unboundedly.  Project runtime
  cleanup remains the separate official control-plane cleanup step
- final cleanup evidence is separate from any startup run.  After the official
  control-plane cleanup reports `unmounted`/`stopped`, the benchmark requires
  two bounded consecutive full-discovery snapshots with no known or
  project-attributed process before reporting the cleanup resource audit as
  `clean`
- benchmark summaries must keep `formal_claim_allowed=false` until every
  independent sample-count, readiness, attribution, instrumentation-overhead,
  scenario, provider, fault, and platform gate is satisfied; a passing
  resource-correlation gate alone is only smoke evidence
- platforms without usable `/proc` must report resource capability as
  unavailable.  Missing fields are not coerced to zero and do not authorize a
  Linux-only performance conclusion on that platform
- instrumentation overhead A/B is a dedicated warm-only benchmark mode.  It
  writes a seeded balanced-ABBA `benchmark-plan.json` before the first runtime
  mutation, primes once, and runs adjacent control/instrumented arms against
  the same generation and frozen reuse identity
- both A/B arms measure spawn-to-exit wall with the same direct-`Popen`, bounded
  timeout boundary.  The control arm disables `/proc` sampling, process trace,
  and readiness trace by design, but must still pass native run-id, startup
  report, generation/config, warm-reuse, zero-Provider-prepare, and authority
  drift checks
- a control arm that emits trace/readiness/resource evidence, or an
  instrumented arm missing any of those correlated surfaces, is invalid; failed
  pairs remain in the artifact and are never retried or replaced
- summary wall/stage/resource/readiness distributions must not mix arms.
  Resource/readiness/attribution gates use only the instrumented arm; control
  trust and paired `instrumented - control` deltas are separate.  Formal
  overhead pass requires `3 + 20` pairs, zero invalid pairs, paired p50 and the
  deterministic bootstrap 95% CI upper bound within `max(10 ms, 2% of control
  p50)`

### 3.3 Shutdown Report

Path:

- `.ccb/ccbd/shutdown-report.json`

The latest shutdown report must capture the most recent shutdown-related transaction, including:

- `trigger`
  - at minimum `shutdown`, `stop_all`, `kill`, or `kill_fallback`
- `status`
- `generated_at`
- `forced`
- `stopped_agents`
- `actions_taken`
- `cleanup_summaries`
- `inspection_after`
- optional `failure_reason`

Rules:

- normal server-side stop/shutdown must write a shutdown report
- CLI fallback kill must also write a shutdown report
- the final persisted shutdown report must reflect post-shutdown state, not an intermediate pre-unmount snapshot
- remote `ccb kill` must finalize lifecycle state before recording the final shutdown report, so `inspection_after` reflects `phase=unmounted` / `desired_state=stopped` rather than a transient `stopping` state

### 3.4 Backend Logs

Project backend logs must remain under `.ccb/ccbd/`:

- `ccbd.stdout.log`
- `ccbd.stderr.log`
- `keeper.stdout.log`
- `keeper.stderr.log`

Rules:

- daemon and keeper must append logs to stable file paths
- diagnostics readers must treat these as evidence, not authority
- large logs may be tailed during export, but the manifest must explicitly mark truncation
- provider runtime `pane-crash-*.reason.json` records are paired diagnostics
  for their matching `pane-crash-*.log`; cleanup must remove the reason record
  when retention removes the matching crash log
- crash capture enforces online per-runtime retention of the newest 50
  `pane-crash-*.log` files and matching reason sidecars; retention is not
  deferred until a later `ccb cleanup`
- pane history is cleared after crash capture and before respawn so a recovered
  pane does not repeatedly reclassify stale terminal history as a new crash

### 3.5 Namespace State And Lifecycle

Paths:

- `.ccb/ccbd/state.json`
- `.ccb/ccbd/start-policy.json`
- `.ccb/ccbd/lifecycle.jsonl`
- `.ccb/ccbd/heartbeats/<subject-kind>/*.json`
- `.ccb/ccbd/maintenance-heartbeat/schedule.json`
- `.ccb/ccbd/maintenance-heartbeat/status.json`
- `.ccb/ccbd/maintenance-heartbeat/runner.json`
- `.ccb/ccbd/maintenance-heartbeat/lock.json`
- `.ccb/ccbd/maintenance-heartbeat/activations.jsonl`
- `.ccb/ccbd/reload-drain.json`

Rules:

- `state.json` records the latest persisted project tmux namespace facts
- `start-policy.json` records the persisted project recovery startup policy, including inherited `auto_permission` and forced recovery-restore semantics
- `lifecycle.jsonl` records namespace creation/destruction and later runtime lifecycle events
- `heartbeats/<subject-kind>/*.json` records non-lease heartbeat state for long-lived supervised subjects such as running jobs; these files are diagnostics/evidence, not backend ownership authority
- `maintenance-heartbeat/` records CCB maintenance heartbeat schedule, status,
  runner, lock, and activation evidence. It is a project-scoped maintenance
  namespace, not daemon lease heartbeat authority and not subject/job heartbeat
  evidence.
  - `schedule.json` records the next maintenance heartbeat time and reason
    when a heartbeat tick or later schedule command updates cadence.
  - `status.json` records the latest maintenance heartbeat status summary from
    `ccb maintenance tick`, including `last_tick_status`, `last_tick_at`,
    `last_ok_at`, `unknown_streak`, `source_kind`, `recommended_action`,
    `next_heartbeat_after_s`, `needs_user`, last activation fields, a bounded
    `summary`, and bounded `evidence`.
  - `runner.json` records best-effort schedule consumer diagnostics, including
    `runner_id`, `pid`, `state`, `source`, `started_at`, `last_seen_at`,
    `last_wake_at`, `last_tick_at`, `last_tick_status`,
    `observed_next_run_at`, `sleep_until`, and `exit_reason`. It is diagnostic
    state for the project-scoped helper and is not daemon lifecycle authority.
  - `lock.json` records best-effort heartbeat operation lock metadata. The
    lock is independent from keeper, lease, and daemon lifecycle locks.
  - `activations.jsonl` records `ActivationIntent` dispatch outcomes such as
    `submitted`, `suppressed`, `blocked`, or `failed`. These records are
    diagnostics/audit evidence for CCB-originated silent asks and are not
    mailbox, job, or daemon authority.
  - malformed or missing files must be visible to `ccb maintenance status`
    without crashing the read path.
  - status readers may report `missing` or `corrupt`; they must not repair,
    rewrite, or migrate these files as a side effect.
  - `ccb maintenance tick` may write only `status.json`, `schedule.json`,
    `lock.json`, and `activations.jsonl` in this namespace. The project-scoped
    schedule consumer may write only `runner.json` and invoke the same one-shot
    tick path when the schedule is due. When non-healthy evidence requires
    semantic supervision, the tick may submit one silent ask to the configured
    assessor through the mounted daemon dispatcher, then exit. Neither tick nor
    runner may write provider state, run repairs, or mutate daemon lifecycle
    authority.
- `reload-drain.json` records bounded pending unload/replace drain state when
  explicit reload state machinery is invoked; it is not lifecycle, lease,
  runtime authority, or a config-watch trigger
- `reload-handoff.json` records a short-lived explicit reload handoff while
  plain `ccb reload` submits a non-dry-run request and, after daemon-side plan
  acceptance, while additive apply with a changed config signature is in
  progress; it lets keeper distinguish a bounded
  old-signature-to-target-signature transition from daemon drift. It is cleared
  after apply, is not lifecycle/lease/runtime/namespace authority, is not a
  config-watch trigger, and stale or mismatched records fail closed.
- `artifacts/text/` stores oversized CCB agent-to-agent message and reply text. Request bodies, terminal replies, notices, and callback continuations larger than 4 KiB are written there as UTF-8 text artifacts; ledgers store only the short preview plus artifact path, byte count, and sha256 metadata. These artifacts are diagnostics/evidence and transport support, not scheduling authority.
- running-job heartbeat observations stay in diagnostics/events and must not be emitted as caller-visible mailbox replies; by default they do not terminalize running `ask` jobs while provider/runtime evidence remains active, and any `heartbeat_timeout` terminalization must be explicit opt-in or health-gated behavior
- daemon lease heartbeat, subject/job heartbeat, and maintenance heartbeat
  schedule/status/activation state must remain separate concepts and separate
  files
- `doctor` and bundle export must include these records when present
- `ping('ccbd')` and `doctor` should surface start-policy summary fields when available
- `ping('<agent>')` diagnostics must surface runtime `reconcile_state`,
  `restart_count`, `recovery_failure_count`, `last_reconcile_at`, and
  `last_failure_reason`. A terminal
  provider-auth block must report health `provider-auth-revoked`,
  `reconcile_state=blocked`, and the actionable login/remount reason rather
  than collapsing to generic `pane-dead` or `stale`
- provisional recovery must report `health=recovering` and
  `reconcile_state=probing`; exhausted automatic recovery must report
  `health=recovery-circuit-open`, `reconcile_state=blocked`, the consecutive
  failure count, and the explicit restart/remount action
- `ping('ccbd')` and `doctor` must surface namespace summary fields such as epoch, tmux socket path, session name, and latest lifecycle event when available
- `ping('ccbd')` and `doctor` must surface current socket placement diagnostics, including preferred/effective socket path, root kind, fallback reason, and filesystem hint when known
- `ping('ccbd')` must remain available during the post-self-ping
  `runtime_bootstrap` stage so startup waiters can distinguish live progress
  from a dead listener; non-ping bootstrap rejections are availability state,
  not evidence of a mounted generation or permission to retry after a request
  was sent.  This diagnostic exception ends fail-closed when final publication
  fails or serving stops; shutdown flag cleanup must not reopen ping or normal
  RPC dispatch
- `ping('ccbd')` and `doctor` should surface lightweight control-plane metrics when available, including handler latency, heartbeat wall duration, heartbeat step duration, heartbeat runtime-store write count, project-view cache/response/build/tmux counts, process RSS/FD/thread counts, service-graph version/created-at/retained-count metadata, pending maintenance ticks, and reload timing fields when a reload feature exists; until old-graph in-flight retention is implemented, `service_graph_retained_count` means published graph count, not RCU-style old graph retention; these metrics are diagnostics only and must not add config watchers, tmux mutations, or heavy steady-state scans
- `ccb layout status --json` may include best-effort tmux observation
  metadata for each observed pane, including `pane_index`, `pane_left`,
  `pane_top`, `pane_width`, and `pane_height`. These fields are diagnostics
  evidence for layout validation and smoke tests, not workflow authority;
  sidebar/tool panes may appear in observed runtime pane counts and must be
  distinguished from configured agent panes by CCB identity metadata.
- `project_view` must surface active reload drain state when
  `.ccb/ccbd/reload-drain.json` contains non-terminal records. The view should
  expose a top-level `reload_drains` summary and mark affected agent rows with
  `reload_drain` plus `dispatch_blocked_by_reload_drain=true`. Project-view
  cache reuse must account for the drain file revision so sidebar state does
  not remain stale after a busy unload is recorded or retired.
- `project_view` must expose terminal provider-auth blocks as failed activity
  reason `provider_auth_revoked`, runtime health `provider-auth-revoked`, and
  `runtime_failure_reason`; sidebar clients should render a concise
  `[login]` action instead of a generic stale indicator, while the diagnostics
  reason retains the complete login-and-remount instruction
- `ccbd` heartbeat may automatically retry a ready active unload drain by
  reusing the same guarded additive reload transaction after dispatcher
  completion polling shows the removed agent has no outstanding work. This is
  limited to current `remove_agent` plans for active unload drain records; it
  must not auto-apply replace or arbitrary layout changes. If the disk config
  no longer requests that removal, the stale ready drain is retired so it stops
  blocking dispatch.
- `ccb reload --dry-run` / `project_reload_config(dry_run=true)` is a diagnostics-grade planning surface: it validates the current `.ccb/ccb.config`, returns old/new config signatures, plan class, no-mutation `safe_to_apply=false`, future classification safety, operations, optional drain intent suggestions for unload/replace, active reload drain status when present, reasons, warnings, and errors, and updates reload timing fields without mutating tmux/runtime/lifecycle/service graph
- `ccb reload` / `project_reload_config(dry_run=false)` is an explicit guarded apply surface: `no_change` returns `status=noop` without graph publish, and only `view_only_change`, `maintenance_change`, append-only `add_agent`, `add_window`, idle `remove_agent`, idle same-slot `replace_agent`, `add_tool_window`, and `remove_tool_window` may publish. `maintenance_change` publishes a new service graph/config signature without tmux namespace mutation, runtime mount/unload, or agent pane restart. Idle same-slot `replace_agent` reuses the existing managed pane as namespace evidence, stops only the target runtime authority/helper, respawns the new provider in that pane, and reports `replaced_agents`; a busy replace must persist a bounded replace drain and stop before namespace/runtime/publish mutation. Non-additive `move_agent`, unsupported tool changes, non-same-slot replacement, and arbitrary `layout_change` must stop before graph publish and report structured diagnostics. Successful apply diagnostics must include stage, graph versions, publish flags, keeper handoff safety, project-view cache invalidation state, and active reload drain status. Failure diagnostics must preserve stage-specific namespace/runtime residue and active reload drain status while leaving the old published graph/config visible.
- heartbeat wall duration may include lifecycle wrapper work outside the named step map; project-view build duration is updated only on cache misses, while project-view response duration is updated on cache hits and misses
- process RSS, virtual memory, FD count, and thread count are best-effort process metrics; platforms without Linux `/proc` support may report `None`
- `doctor` must also surface preferred/effective socket path byte lengths and an equivalent isolated-config `tmux -f /dev/null -S <effective-socket> start-server` command when a project tmux socket path is known, so macOS and WSL socket pathname failures can be diagnosed from one report
- malformed namespace diagnostics must surface as diagnostics errors, not silently disappear
- supervision diagnostics must preserve mount-attempt distinctions:
  - `mount_started` details should include `mount_attempt_id` when present
  - superseded finalize paths should remain visible as `mount_superseded`
    instead of collapsing into missing history

#### ProjectView and queue execution-phase diagnostics

ProjectView Comms records and structured queue agent records may expose the
optional schema-v1 fields `execution_phase`, `execution_phase_reason`, and
`execution_evidence`. The stable phase vocabulary is `queued`, `injecting`,
`executing`, `provider_idle_pending_terminal`, `reply_queued`,
`reply_delivering`, `orphaned`, `terminal`, and `unknown`.

These fields are a read-only correlated projection. A confident non-terminal
request phase requires exact job, attempt, inbound event, mailbox head/active,
lease, completion snapshot, agent, and current provider identity joins. A
correlated reply record and reply-delivery job determine the reply phases.
Terminal job/completion authority wins over lagging mailbox or lease cleanup;
missing, stale, contradictory, or mismatched evidence produces `unknown`.
Missing provider identity evidence uses `provider_identity_unavailable`;
evidence that the identity is not current uses `provider_identity_mismatch`.
`execution_evidence` contains only the compact identity and state facts used by
the resolver and is diagnostics evidence, not scheduling authority.

ProjectView and queue reads must not poll providers, capture extra state solely
for this projection, mutate authority, or trigger recovery. In particular,
`orphaned` reports bounded current-session provider-idle evidence without
automatically cancelling, retrying, restarting, resending, or terminalizing the
job. Older producers may omit the fields, and CLI, Rust sidebar, and mobile
clients must fall back to existing mailbox/job/business status fields.

R8 narrows `orphaned` for the Issue260 active-inbound condition. An exact
Claude idle prompt after the bound request first produces
`provider_idle_pending_terminal`; it becomes `orphaned` only when the same
job/attempt/inbound/mailbox/lease/completion anchor and current provider
session/reference, pane, workspace, and runtime generations are observed idle
again after at least 30 seconds. Job progress, terminal evidence, active
provider work, missing or changed lineage, pane/session/generation rotation,
and ProjectView service restart reset the in-memory observation. The earlier
job-age threshold only makes the first observation eligible, and cached
ProjectView responses do not advance the window.

A confirmed Comms row adds `active_inbound_diagnostic` with condition
`orphaned_active_inbound`, exact job/attempt/inbound/mailbox/lease/provider
evidence, job age and last progress, observation start/elapsed/required
seconds, `recommended_action=explicit_comms_recover`, an exact recover target,
and `automatic_action=none`. Maintenance concern evidence preserves the same
record; trace merges it only for jobs in the resolved lineage; doctor lists
the current records; CLI and sidebar consumers render them without rederiving
the condition. Reading any of these surfaces must not call recovery or mutate
job, attempt, inbound, mailbox, lease, completion, reply, or provider runtime
authority. Explicit `comms recover` remains a separate operator action that
revalidates authority when invoked.

### 3.6 Doctor Read Path

`ccb doctor` is the best-effort project diagnostics read path.

Rules:

- it must summarize current backend inspection plus latest persisted reports
- `doctor ps` and `doctor logs <agent>` are converged diagnostics subviews of
  the same diagnostics surface
- if top-level `ps` and `logs` are retained, they must remain compatibility
  entrypoints over the same diagnostics meaning rather than drifting into a
  second independent diagnostics surface
- it should surface current mailbox summary authority for configured agents when
  present, including at minimum summary version/source/freshness plus head and
  queue facts needed to diagnose summary-vs-ledger drift
- it should also surface mailbox summary consistency status for configured
  agents by comparing persisted summary authority against a diagnostics-grade
  ledger projection without mutating mailbox artifacts
- missing, unreadable, or drifted mailbox summaries must remain visible in
  doctor output as explicit consistency mismatch/error state rather than being
  silently repaired during the read path
- agent binding diagnostics must include both `tmux_socket_name` and `tmux_socket_path` when known so project-scoped namespace bugs can be diagnosed from logs alone
- startup failure diagnostics must retain chained cause detail in CLI output and in `ccbd_startup_last_failure_reason` when the backend recorded it
- Codex agent diagnostics should surface managed in-pane session-switch state
  when `.ccb/agents/<agent>/provider-runtime/codex/session-switch.json`
  exists, including state, reason, commit status, and candidate session
  identity
- `doctor storage` must surface the effective `shared_cache_root`,
  `shared_cache_root_usable`, and shared-cache status/reason, so WSL relocation
  and future provider cache sharing decisions are diagnosable from the same
  storage view. When the reason is `wsl_drvfs_requires_runtime_relocation`, the
  root must be reported as unavailable instead of pointing at an unsafe drvfs
  path. The current disabled reason code is
  `wsl_drvfs_requires_runtime_relocation`; relocated WSL projects should report
  shared cache as enabled.
- it must not crash only because one diagnostics artifact is missing or malformed
- malformed diagnostics files must surface as diagnostics errors, not silent omission

### 3.7 Support Bundle Export

Command:

- `ccb doctor --bundle`

Default output location:

- `.ccb/ccbd/support/<bundle-id>.tar.gz`

The support bundle must include:

- a manifest
- a generated doctor snapshot
- current project config from `.ccb/ccb.config`
- latest lifecycle reports
- backend authority files such as lease, keeper, shutdown intent, and namespace state when present
- backend recovery policy authority such as `start-policy.json` when present
- persisted non-lease heartbeat state under `.ccb/ccbd/heartbeats/` when present
- maintenance heartbeat schedule/status/runner/lock/activation files under
  `.ccb/ccbd/maintenance-heartbeat/` when present
- oversized CCB text artifacts under `.ccb/ccbd/artifacts/text/` when referenced by recent message/reply records
- recent backend event streams such as supervision, namespace lifecycle, and cleanup history
- backend stdout/stderr logs
- per-agent runtime authority and recent agent/provider logs
- non-secret project-local provider-state evidence such as managed Codex homes, session roots, session logs, and config overlays
- a generated storage classification snapshot at
  `generated/storage-summary.json`
- relevant external session files when discoverable from runtime authority

Rules:

- bundle export must be best-effort and continue when some files are missing or malformed
- manifest rows must include original source path, archive path, inclusion status, and truncation status
- bundle export must not require the backend to be healthy
- bundle export must be project-local and deterministic enough for support usage
- provider-state export must exclude credential material such as copied auth
  tokens and provider-managed credential files like `auth.json` or
  `oauth_creds.json`; Gemini projected auth artifacts such as `.env` and
  `google_accounts.json` must also be excluded
- provider-state export must use the storage classification model from
  [docs/ccb-provider-state-storage-boundary-plan.md](/home/bfly/yunwei/ccb_source/docs/ccb-provider-state-storage-boundary-plan.md)
  to exclude `SECRET`, `REBUILDABLE_CACHE`, and
  `STARTUP_AUTHORITY_BUNDLE` payload files from the archive while preserving
  their path/class/size metadata in `generated/storage-summary.json`
- if storage classification fails, provider-state export must still apply a
  conservative path hard-filter for known cache/startup-bundle directories such
  as Codex `.tmp/plugins/`, Claude `.local/share/claude/versions/`, and
  Gemini/npm rebuildable cache paths; classification failure must not turn
  excluded payloads into archive entries
- provider-state export must not follow symlinks while walking provider-state
  trees
- Codex managed-home violations must remain visible as diagnostics evidence; bundle export must not hide them by silently replacing the managed reader source with global `~/.codex/sessions`

### 3.8 Keeper Child Reaping

The keeper may directly spawn `ccbd`, but it must reap exited direct children.

Rule:

- a crashed or killed `ccbd` process must not remain visible as an unreaped zombie just because keeper is still alive

## 4. Operational Workflow

Recommended support workflow:

1. reproduce the issue in the project anchor
2. run `ccb doctor`
3. run `ccb doctor --bundle`
4. send the generated tarball

The bundle is the transport unit. The reports inside it are the authoritative timeline.

## 5. Update Discipline

- If startup or shutdown reporting changes, update this document in the same patch.
- If `doctor` or bundle contents change materially, update this document in the same patch.
- Use [docs/ccbd-manual-test-issue-log.md](/home/bfly/yunwei/ccb_source/docs/ccbd-manual-test-issue-log.md) for concrete incidents and repro findings.
