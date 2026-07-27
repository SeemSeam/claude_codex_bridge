---
name: ccb-clear
description: Clear CCB managed agent conversation context with `ccb clear`. Use when the user writes `$ccb-clear`, `$ccb_clear`, or asks to clear/reset one or more CCB agent contexts without restarting or deleting project state.
metadata:
  short-description: Clear CCB agent context
---

# CCB Clear

Use this skill to clear provider conversation context for mounted CCB agents.

In the CCB source checkout, this skill is only for clearing the active work-environment CCB collaboration with the installed release `ccb`. It is not a source validation workflow. For testing current source changes, use `/home/bfly/yunwei/ccb_source/ccb_test` from the dedicated external test project `/home/bfly/yunwei/test_ccb2` instead. Other external test projects require explicit `CCB_TEST_ROOTS` or `CCB_SOURCE_ALLOWED_ROOTS`.

Commands:

```bash
command ccb clear
```

```bash
command ccb clear "$AGENT"
```

```bash
command ccb clear agent1 agent2
```

Rules:

- `ccb clear` targets all configured mounted agents.
- `ccb clear <agent...>` targets only the named agents.
- This sends provider-native `/clear` to each target pane.
- It does not delete `.ccb` state, workspaces, auth, sessions, logs, or memory files.
- Do not use `ccb kill`, `ccb -n`, or restart commands unless the user explicitly asks for process/runtime reset.
- After running the command, report the command output. Do not poll or wait.
