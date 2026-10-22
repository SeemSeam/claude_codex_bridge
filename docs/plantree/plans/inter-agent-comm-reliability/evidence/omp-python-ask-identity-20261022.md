# OMP Python ask caller identity

Date: 2026-10-22

Status: repaired and verified in local source; owner authorized a local commit.
No deployment, task replay, Agent restart, or publication was performed.

## Diagnosis

The observed Frontdesk submits used OMP's Python `eval` tool and
`subprocess.run(['ask', '--compact', '--artifact-reply', 'executor'], ...)`.
OMP 18.12.5 filters Python subprocess environments to an allowlist: `PI_*`
survives, while `CCB_*` and `CODEX_*` do not. The actual Frontdesk process
retained its CCB caller fields; its Python kernel did not.

The original caller resolver checked CCB/runtime/session identity and workspace
ownership only. A shared project workspace cannot uniquely identify an Agent,
so these submissions silently became `from_actor=user`. The completion was
stored, but `user` has no Agent mailbox to receive a return event.

Authoritative trace evidence:

- `job_753b5074bf8b`: sender `user`, completed at
  `2026-10-22T04:12:21.746597Z`, reply `rep_c7165650a697`.
- `job_3fca8db3672c`: sender `user`, completed at
  `2026-10-22T04:28:47.689875Z`, reply `rep_291bf3ee55de`.
- Both terminal replies were read in full from their stored text artifacts;
  both jobs have exact `omp_run_stop` completion. No Frontdesk return event
  exists for either. Current queues and pending replies were empty.
- Bounded pane captures showed Executor's finished review and an idle
  Frontdesk that had only acknowledged submission.

Failure fingerprint:
`omp-python-filter:ccb-caller-identity-dropped:ask-sender-user:no-mailbox-return`.

An earlier return-processing job, `job_f12cda30e971`, had a distinct
`omp_request_superseded` / `unmanaged_agent_turn` outcome. This identity repair
does not claim to fix human-turn supersession or every historical interruption.

## Repair boundary

[Caller resolution](../../../../../lib/cli/ask_sender.py) now recovers the
sender from the retained `PI_CODING_AGENT_DIR` only when its canonical path
matches the current effective runtime state's
`agents/<configured-agent>/provider-state/omp/home/.omp/agent` and that Agent
is configured with provider `omp`. Relocated runtime roots are supported.

Explicit sender and existing runtime/actor/session identities retain their
precedence. An invalid supplied runtime path cannot be overridden by this
fallback. Foreign projects, unmanaged homes, unknown Agents, other providers,
subdirectories and symlinks escaping the managed tree do not infer an Agent.
No Provider configuration, authentication, environment allowlist or native
installation is modified. Existing persisted jobs are not relabeled or replayed.

## Verification

Before the fix, the new regression selection produced **3 failed, 11 passed**:
both ordinary/relocated sender cases became `user`, and the integrated
ask/completion failed to route back to Frontdesk.

Related source regressions:

```sh
.venv/bin/python -m pytest -q \
  test/test_v2_ask_service.py \
  test/test_v2_message_bureau_dispatcher_integration.py \
  test/test_ask_cli.py test/test_v2_cli_context.py test/test_v2_phase2_ask.py \
  test/test_ask_internal_paths.py test/test_omp_provider.py \
  test/test_omp_pane_execution.py test/test_reply_delivery_start_completion.py \
  test/test_reply_delivery_polling_gate.py
```

Final result: **196 passed**. The integrated test uses the actual CLI sender
resolver, submission service, dispatcher, completion/artifact persistence and
mailbox stores with a synthetic completion; the result artifact is complete
and exactly one `TASK_REPLY` is queued for Frontdesk.

A separate isolated probe imported installed OMP 18.12.5's real `filterEnv`,
filtered a synthetic caller environment, then launched a Python subprocess
using the repaired source resolver. CCB identity fields were absent,
`PI_CODING_AGENT_DIR` remained, and the sender resolved to `frontdesk`.
Zero model requests and zero daemon starts; all files were temporary fixtures.
This verifies native environment compatibility, not a live model roundtrip.

`git diff --check` passes. Existing unrelated work is preserved. Public release
qualification and activation in existing sessions remain separate work.
