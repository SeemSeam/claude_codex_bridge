# Mounted Agent Hibernation and On-Demand Wake

Date: 2026-10-21
Mode: isolated prototype. Status: Codex feasibility verified; production runtime unchanged.

## Owner Goal

Long-unused mounted Agents should release Provider CPU/memory, remain discoverable
and addressable, and restore their conversation when used again. This applies to
ordinary CCB projects, independently of the proposed WezTerm migration.

## Current Source Findings

- `ccbd/supervision/loop.py` mounts missing configured runtimes and recovers
  failed ones. `loop_runtime.py` derives desired state as mounted/stopped;
  intentional sleeping needs explicit recognition throughout reconciliation.
- Dynamic-agent `hide`/`park` changes visibility/dispatch policy. `unload` removes
  the Agent from the effective runtime graph. These do not provide a transparent,
  named sleeping target with automatic message-triggered wake.
- `ccbd/handlers/project_restart.py::_restart_busy_gate` already checks runtime
  state, active jobs, queued messages, pending replies, inbound delivery and
  callback blockers. Reuse the concepts, but missing/failed safety observations
  must block hibernation rather than imply idle.
- Provider-specific session restore paths exist, e.g. Codex session bindings,
  Claude restore targets, and Pi exact-session binding. Their existence does not
  prove safe sleep/wake for every Provider or native task.
- `runtime_specs.py` declares idle-timeout environment names; inspection found no
  general mounted-Agent hibernation implementation consuming those names in lib.
  Do not advertise a working switch based on that declaration.

## Proposed Runtime Contract

Keep logical Agent configuration and identity separate from process residency:
`resident -> quiescing -> hibernated -> waking -> resident`, plus explicit wake
failure. Job busy/idle remains a separate concern. These are proposed concepts,
not current config keys or CLI commands.

At hibernation, retain Agent/provider/workspace identity, selected native session
and validated restore evidence, CCB lineage, configuration generation and enough
UI history to explain state. Stop only CCB-owned per-Agent Provider processes,
bridges and eligible descendants; preserve shared services with other users.
Retain a lightweight pane placeholder where needed, and the ccbd/socket/queue
service. Never clear conversations, change external Provider auth/config, remove
an Agent from the configured graph, or use raw pane death as hibernation authority.

This is process exit plus native-session resume, not a process-memory snapshot.
Unpersisted drafts, foreground tool state and arbitrary background processes are
not automatically recoverable. Unsupported or uncertain cases stay resident.

## Eligibility and Wake

Hibernate only after an opt-in timeout and an atomic final safety check:
no executing/queued job, return delivery, unacknowledged inbound, waiting child
callback, native Provider turn/tool activity, draft/modal, interaction lease,
recovery/reload/restart or unresolved process ownership. Externally attached
Providers are excluded. Unknown evidence defers the attempt. A quiet screen,
unchanged transcript, or CCB idle state alone is insufficient.

Use monotonic business-activity age, not runtime heartbeat/last_seen timestamps.
Passive sidebar refresh, Mobile polling, project listing and history viewing
must neither keep an Agent awake nor wake a sleeping one. Explicit interactive
entry/terminal attachment can wake and hold a bounded interaction lease.

Accepted ask, result return or explicit interactive use requests one deduplicated
wake. Queue authority persists before startup; submit remains quick, jobs retain
FIFO, and delivery waits for verified readiness and correct session binding.
New arrivals cancel quiescing or trigger one wake after a committed stop. Serialize
wake/hibernate/restart/reload through per-Agent generation/transition control.
Provider execution timing starts at the appropriate ready/dispatch boundary;
absolute request expiry and cancellation still apply during startup.

Wake failure reports a reason and bounded retries/backoff. Never silently start a
blank conversation, drop an accepted job, or permanently retry a crashing Agent.
After daemon restart, persisted hibernation must survive supervision and UI reads.

## Proposed Rollout and Verification

1. Inventory per-Agent owned process trees, RSS/PSS, idle CPU and child services;
   validate exact-session restore on an isolated Codex test Agent first. No
   projected resource-saving numbers are acceptance evidence.
2. Implement manual sleep/wake and visible state before opt-in auto-timeout.
   Candidate policy: 30 minutes, per-Agent override and keep-resident exemption;
   these defaults remain unapproved. Use a reduced timeout only in test projects.
3. Add ask/result/interactive wake and race/failure tests; cover Agent sleeping
   during concurrent arrivals, daemon restart, cancelled wake and queued expiry.
4. Real Provider acceptance: known conversation fact before/after sleep; actual
   process-tree release; ask/back exactly-once delivery; chained callbacks; drafts
   and native tools block sleep; Mobile polling does not undo savings.
5. Expand to Claude/Pi/OMP only after individual native-session and process-tree
   evidence. Account for in-flight OMP changes separately; do not mix this analysis
   with unrelated dirty implementation files.

Measure idle process-tree PSS/RSS/CPU and CCB residual cost, sleep eligibility
reasons, wake latency (cold/warm and p95), queue latency and restore outcomes.
Keep resource and correctness evidence together. Never promise zero memory/CPU:
the routing daemon and optional placeholder must remain available.

## Remaining Decisions

Choose initial Providers and timeout policy, exact resume compatibility criteria,
interactive lease behavior, wake timeout/error semantics and limits on simultaneous
cold starts across many sleeping Agents. Implementing this feature is separate
from this requested feasibility analysis.

## Restart-Based Isolated Prototype (2026-10-21)

Owner authorized writing the plan and independent testing, explicitly asking
whether restart-style recovery can be reused. Answer: yes, the isolated harness
calls `restart_project_agent_panes_in_place` for one Agent and forces native
`restore=True`. Queue/mailbox handling remains CCB-owned; sleep is a separate
intentional state recognized by the test daemon's supervision hook.

Implementation is deliberately in `dev_tools/agent_hibernation_probe.py`, outside
production runtime imports. The one-Agent Linux/Codex experiment proves process
release, exact native-session continuation, queued ask wake and persistence.
[Independent test evidence](../evidence/agent-hibernation-prototype-20261021.md)
records results and production gaps. The dedicated test project is stopped.

No default timeout/config schema, published command, Mobile wake integration,
Windows support, or general Provider hibernation feature is claimed. Production
implementation still needs explicit transition authority, startup/reload hooks,
request/maintenance synchronization, interaction leases, complete failure cleanup,
resource samples over a longer interval, and per-Provider acceptance.
