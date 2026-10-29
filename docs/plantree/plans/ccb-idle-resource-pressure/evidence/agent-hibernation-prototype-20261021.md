# Independent Codex Hibernation Prototype Evidence

Date: 2026-10-21
Authority: isolated source prototype and real local Provider test, not release.
Base: `8034c32f9`; branch: `prototype/agent-hibernation-20261021`.
Source worktree: `/var/tmp/ccb-agent-hibernation-20261021`.
External project: `/home/bfly/yunwei/test_ccb2/hibernation-20261021`.
Harness: `dev_tools/agent_hibernation_probe.py` in that worktree.
Tests: `test/test_agent_hibernation_probe.py` in that worktree.

## Results

- Real Codex 0.163.0, dedicated Agent `sleeper`, native session
  `01a15e02-7815-7fd3-a6c7-5f50033c4039`.
- Initial task `job_68b31daf4086` stored a unique conversation marker.
- Three hibernations and wakes succeeded; recorded wake readiness was 5.892s,
  5.908s and 6.124s. This is a three-cycle sample, not a p95 latency claim.
- The first post-sleep ask (`job_dbe429a24851`) returned the exact marker without
  tools or reading files. Native session ID remained unchanged.
- Two sequentially submitted requests (`job_6ce198260295`,
  `job_53e5bfd198a4`) were accepted in 5.24ms / 5.10ms, processed in order, and
  caused one wake. Replies were `FIRST_WAKE_OK` then the original marker.
- Attempting sleep with accepted/active work was rejected. A literal unsent
  draft inserted via the test daemon fixture also blocked sleep, without clearing
  the draft. The test explicitly cleared its own fixture afterward.
- Test policy with a 3-second idle threshold hibernated the Agent. Repeated
  ping/queue/status reads did not wake it. It remained asleep across 32 seconds,
  covering the normal 30-second full health/supervision interval.
- Test daemon detach/restart retained sleeping state. Explicit wake followed by
  `job_c33541d490b4` again recovered the initial marker in the same native session.
- Every tracked pre-sleep PID/start-time identity was gone before release success
  was reported. One measured Provider subtree contained 6 processes:
  RSS 453384 KiB / PSS 179611 KiB before sleep; placeholder RSS 8068 KiB /
  PSS 1685 KiB afterward. Shared-page RSS is not additive physical memory; PSS
  is the better proportional comparison. ccbd/sidebar/shared services are excluded.
  No numerical CPU-saving claim or whole-host memory claim is made.
- Narrow tests: **45 passed** across `test_agent_hibernation_probe.py`,
  `test_ccb_restart.py`, `test_v2_ccbd_supervision_loop.py`.

## Mechanism and Limitations

This harness runs an isolated ccbd and adds test RPCs. Hibernation persists intent,
keeps Agent identity and pane as a lightweight placeholder, stops the old pane
program via the CCB terminal backend, and suppresses recovery for intentional
sleep. Wake uses the existing single-Agent restart implementation with
`restore=True`; it waits for an empty composer and verifies the same native session
before letting queued jobs dispatch. Existing request submit stays quick.

It is a feasibility prototype, not production implementation: hooks are scoped to
one test daemon, one Linux pane-backed Codex Agent and one loaded service graph.
It does not support graph reload, general manual restart/start while asleep,
Mobile/side-panel interactive wake, broadcast targeting, external runtimes,
multi-Agent wake limits, full process-tree/background-tool admission, or complete
failure cleanup. It conservatively refuses attached tmux clients; a production
interaction lease can allow more useful desktop behavior. Some persisted callback
or queue failures still need stronger fail-closed observation under one atomic
transition boundary. Native tool activity is partly guarded by composer evidence;
that does not qualify all arbitrary background jobs.

Queued result return and callback-chain wake were not independently exercised.
Tests here cover the restart contract and selected blockers, not all providers or
platforms. Production readiness needs dedicated state/generation authority,
startup/reload/recovery integration, race/cancellation/expiry tests, native-session
preflight repeated at wake, partial-start cleanup and user-visible failure state.

## Isolation and Cleanup

Normal installed CCB and active working-project Agents were not restarted or
updated. The source wrapper preflight confirmed an external allowed test root.
Only the dedicated test Agent was stopped/restarted. Normal workbench auto-launch
was disabled for subsequent test startup with `CCB_RICH_AUTO_START=0`.

The dedicated test daemon and Agent were shut down through CCB after acceptance;
its transcript and redacted evidence remain for review. No Provider auth/config
was edited and no global installation or publication was performed.

Raw receipts are local review artifacts:
`/var/tmp/ccb-hibernation-context-result.json`,
`/var/tmp/ccb-hibernation-roundtrip-result.json`,
`/var/tmp/ccb-hibernation-auto-result.json`,
`/var/tmp/ccb-hibernation-daemon-restart-result.json`.
