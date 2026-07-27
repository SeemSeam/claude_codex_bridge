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
2. Result intent:
   - `--silence`: publish/execute task; success result not needed. Failures,
     blockers, risks, or required next actions still surface.
   - `--compact`: result wanted, but only distilled
     findings/status/risks/blockers/next actions.
   - `+ --artifact-reply`: consultation/analysis/report where full text should
     be preserved.
   - plain `ask`: short question or short handoff where inline text is enough.
   - `--chain`: active CCB parent job + child result required to finish.
     Combine with `--compact` or `--artifact-reply` as needed. Submit, then
     stop for continuation.
3. Request fidelity:
   - `+ --artifact-request`: exact transient input
     (logs/output/diffs/copied contents/config/JSON/YAML/table/structured text).
     Prefer repo paths when the target can read files directly.
   - `--artifact-io`: request and reply both need artifacts.

## Guardrails

- Do not probe `--chain`; if unsure there is an active parent job, use plain
  `ask`.
- If CCB says `ask --chain requires an active parent job`, retry once with
  plain `ask` for user-requested delegation.
- `--chain` and `--silence` usually conflict; avoid mixing unless explicit.
- Avoid `--silence --artifact-reply`; silence means no caller result needed; artifact-reply preserves one.
- Artifact flags are orthogonal to `--chain`, `--silence`, and `--compact`.
  They preserve content, not dependency shape.
- Automatic spill for text over 4 KiB is a fallback, not the primary rule.
- `--artifact-*` modes are CCB/daemon managed; targets do not write artifact reply files.
- Plain nested `ask` from an active CCB task is rejected; use `--chain` or `--silence`.
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
- Do not manually append output-policy text; `ask` injects reply guidance.

Use no flags or insert selected flags before `"$TARGET"`:

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
