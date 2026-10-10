# Real Provider Roundtrip Qualification — 2026-10-10

Source candidate: `e9789d5ecd1bf2535b2f6789603d24ba9c13c57e` (PR #376).
See [identity repair evidence](codex-ask-caller-identity-20261010.md) for the defect,
launcher boundary, regression suites and native filtered-environment probes.

Native clients: Codex 0.161.0, OMP 18.8.3, Claude Code 2.1.289, Pi 1.1.0.
These additional runs used real model services, not deterministic responses.

| Flow | Outcome | Evidence |
| --- | --- | --- |
| OMP → Codex → OMP, run 1 | Passed | `job_afdcd8701d56`, from=omp, 8.20 s reply / 14.16 s return consumed |
| OMP → Codex → OMP, run 2 | Passed | `job_ccd9eafa28ab`, from=omp, 7.18 s reply / 13.16 s return consumed |
| Claude Sonnet 4.6 → Codex → Claude | Passed | `job_388976040ab1`, reply `rep_f73f12716052`, from=claude, 4.80 s reply / 9.57 s consumed |
| Pi → Codex → Pi | Blocked before ask | zai-coding-cn/glm-5.3 returned 429/code 1113, insufficient balance/resources |
| Simple request to Claude | Delivered; business failure | `job_494b66f74900`, model unavailable; completed execution is not task acceptance |
| Simple request to Pi | Delivered; business failure | `job_99c0c8ca81d0`, failed/pi_run_error, insufficient resources |

Every passing flow used one native bare ask, one attempt, the exact expected
marker and `42`, a consumed task_reply and empty pending queues. Native callers
ended their sending turn after accepted submission and verified the result in
the later return turn. No extra chain/silence/from override was needed.

Claude used test-project startup_args `--model claude-sonnet-4-6` after default
claude-sonnet-5-5 and an existing Kimi-K3 route were rejected. Only the disposable
test project changed model. External Provider state and the working project
configuration remained unchanged. Pi normal service qualification is pending.

Evidence receipts are retained locally under
`/var/tmp/ccb-provider-roundtrip-evidence-20261010/REPORT.md`, including
`real-a0c1248d/omp-verified.json`, `real-c2a98ea4/summary.json`, and
`real-63865878/summary.json`, trace, pane and queue receipts. Harness path and
premature-consumption checks were corrected; failed harness attempts are not
counted as passes. Final probes preserved existing API environment opaquely.
All disposable projects exited through their control plane (cleanup exit 0).
The working project was not restarted and its inspected queues were empty.
