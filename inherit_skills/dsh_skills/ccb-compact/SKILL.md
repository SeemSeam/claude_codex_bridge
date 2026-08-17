---
name: ccb-compact
description: Compact one or more CCB managed agent contexts with `ccb compact` without restarting or deleting state.
metadata:
  short-description: Compact CCB context
---

# CCB Compact

Run one matching command and report its output:

```bash
command ccb compact
command ccb compact "$AGENT"
command ccb compact agent1 agent2
```

For a DSH target, CCB invokes native `/compact` through DSH's structured Web
command endpoint and waits for the native result; it never sends the command
to the model or types pane input. Busy or queued agents are blocked. Do not
substitute clear, restart, kill, or polling.
