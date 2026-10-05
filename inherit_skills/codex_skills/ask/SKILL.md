---
name: ask
description: Send a request to a CCB agent with `ask`.
metadata:
  short-description: Ask agent
---

Use this skill when the user asks you to delegate with CCB, or when project
memory says to use CCB `ask` for collaboration.

## Decision Card

Before every ask, decide:

1. Need delegation? If no, answer directly.
2. Dependency gate:
   - Default: do not use `--chain`.
   - Use `--chain` only when the current active CCB task cannot finish until
     this exact child result arrives. Then stop for continuation.
   - Communication tests, batch sends, notifications, and independent work do
     not become chain dependencies merely because replies are requested.
3. Result intent:
   - `--silence`: publish/execute task; success result not needed. Failures,
     blockers, risks, or required next actions still surface.
   - `--compact`: result wanted, but only distilled
     findings/status/risks/blockers/next actions.
   - `+ --artifact-reply`: consultation/analysis/report where full text should
     be preserved.
   - plain `ask`: short question or short handoff where inline text is enough.
4. Request fidelity:
   - `+ --artifact-request`: exact transient input
     (logs/output/diffs/copied contents/config/JSON/YAML/table/structured text).
     Prefer repo paths when the target can read files directly.
   - `--artifact-io`: request and reply both need artifacts.

## Guardrails

- Do not probe `--chain`; if unsure there is an active parent job, use plain
  `ask`.
- If CCB says `ask --chain requires an active parent job`, retry once with
  plain `ask` for user-requested delegation.
- Never add `--chain` merely to make a rejected plain ask succeed. If the work
  is independent and no success result is needed, use `--silence`; otherwise
  report the routing limitation instead of inventing a dependency.
- `--chain` and `--silence` usually conflict; avoid mixing unless explicit.
- Avoid `--silence --artifact-reply`; silence means no caller result needed; artifact-reply preserves one.
- Artifact flags are orthogonal to `--chain`, `--silence`, and `--compact`.
  They preserve content, not dependency shape.
- Automatic spill for text over 4 KiB is a fallback, not the primary rule.
- `--artifact-*` modes are CCB/daemon managed; targets do not write artifact reply files.
- Plain nested `ask` from an active CCB task is rejected. Use `--chain` only
  for a real child dependency; use `--silence` for independent no-result work.
- In `A --silence -> B`, B still runs an active job. B-to-C depends on whether B needs C's result.
- In task chains, each needed-result hop uses `--chain`; CCB then propagates continuations.
- Finish an inbound CCB task in its current turn.
- If the original caller is a registered CCB agent, CCB routes that turn's
  terminal result through the existing lineage; do not open a new `ask` to
  report completion to the original caller.
- Direct CLI submitters read terminal results from control output such as
  `watch` or `trace`.
- If the current task is a CCB result-chain continuation, answer the current task
  directly with the final result. Do not use `ask`, `--chain`, or
  `--silence` to send that final result to the original caller; CCB routes the
  continuation completion upstream.
- `--silence` is not an active-job correction channel. Use
  `ccb followup <active_job_id> --message "<correction>"` only when the target
  provider supports exact active-turn injection; only `injected` is success.
  On `rejected`, `too_late`, or `terminal`, cancel and resubmit the complete
  corrected task instead of queueing a correction as ordinary work.
- A `completed` CCB job means provider execution ended normally; it does not by
  itself prove business acceptance.
- `ask get`, `pend`, `watch`, and `ping` are diagnostics-only commands for
  explicit debugging requests, not normal ask workflow tools.
- Do not manually append output-policy text; stable reply policy comes from managed CCB memory, and `ask` adds only requested compact/silent mode metadata.

When `CCB_PINNED_CLI` is set by the controller, invoke `"$CCB_PINNED_CLI" ask`
with the same flags and input below instead of resolving `ask` or `ccb` from
PATH. Use that absolute entrypoint for other CCB operations too. Login shells
may otherwise select a different installed version. Do not use the outer
controller startup wrapper: it would clear the managed caller identity.

Otherwise, use no flags or insert selected flags before `"$TARGET"`:

```bash
command ask "$TARGET" <<'EOF'
$MESSAGE
EOF
```

```bash
command ask --chain --artifact-reply "$TARGET" <<'EOF'
$MESSAGE
EOF
```

After submit, stop. Do not wait for a reply, do not run `ask get` / `pend` /
`ping` / `watch`, and do not poll. For `--chain`, report only submitted.
