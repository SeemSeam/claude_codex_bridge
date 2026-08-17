---
name: ccb-clear
description: Clear one or more CCB managed agent contexts with `ccb clear` without deleting project state.
metadata:
  short-description: Clear CCB context
---

# CCB Clear

Run one matching command and report its output:

```bash
command ccb clear
command ccb clear "$AGENT"
command ccb clear agent1 agent2
```

For a DSH target, CCB rotates the managed native DSH session binding through
the control plane; DSH has no `/clear` command and CCB does not type into its
host/log pane. Old native session logs remain available. Busy or queued agents
are blocked. Do not substitute restart, kill, file deletion, or polling.
