---
name: ccb-diagnose
description: Diagnose a named CCB managed agent using runtime, job-lineage, and provider evidence.
metadata:
  short-description: Diagnose CCB agent
---

# CCB Diagnose

Diagnose exactly one current mounted agent. Start with CCB authority:

```bash
command ccb ping "$AGENT"
command ccb ps
command ccb queue --detail "$AGENT"
command ccb pend --inbox --detail "$AGENT"
```

Use `ccb trace` for a current lineage id and `ccb doctor logs` for provider/API
evidence. For DSH, the pane is only the managed host process/log surface: pane
text is not prompt, reply, or completion authority. Native authority is the
exact DSH session/RPC event history and `turn/end` reason recorded by CCB.

Do not read credentials, mutate tmux directly, restart all agents, or submit a
GitHub issue without showing a redacted proposal and receiving explicit
authorization. Apply only a bounded supported repair, then re-check the same
runtime and lineage evidence.
