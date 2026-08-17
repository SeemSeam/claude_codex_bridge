---
name: ask
description: Send a request to another CCB agent with `ask`.
metadata:
  short-description: Ask CCB agent
---

# Ask

Use CCB `ask` when the user requests delegation or project memory assigns a
concrete task to another mounted agent.

```bash
command ask "$TARGET" <<'EOF'
$MESSAGE
EOF
```

Use `--chain` only when the current inbound task cannot finish without that
exact child result. Use `--silence` only for independent work whose successful
result is not needed. After submitting, stop: do not poll with `watch`, `pend`,
`ping`, or `ask get`. CCB routes an inbound task's terminal result through the
existing lineage, so do not open another ask merely to report completion.

Never use `--silence` as an active-job correction. Use `ccb followup` only when
the target advertises exact active-turn injection; otherwise cancel and
resubmit the complete corrected task.
