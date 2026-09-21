# Input Draft Guard v1 Verification

Date: 2026-09-20
Role: evidence
Status: source and isolated native-editor verification passed; not deployed
Related: [contract](../topics/input-draft-delivery-guard.md), [roadmap](../roadmap.md)

## Implemented boundary

OMP, Codex and Claude share a monotonic 180-second guard after existing FIFO
eligibility/turn-end checks, before claim, with a second check at the actual
sender. Unknown/busy/modal states never authorize clear/send. A failed clear
is not repeated automatically; delayed empty readback releases the same job.
Same-job deferred submissions preserve FIFO. Waiting time does not count as
provider execution or progress timeout, and restore starts a fresh timer.

Codex uses a narrow deferred-send split; Claude bypasses blind pre-send clear
and ready-timeout fallback and disables speculative second Enter. OMP uses a
native editor bridge with actor/launch/runtime/session identity and default
Status Band focus check. Pre-claim session resolution is read-only.

## Deterministic verification

Final result: **456 passed in 16.69 s**, 23 files. Command prefix:

```sh
PYTHONPATH=lib uv run --with pytest --with pytest-asyncio --no-project python -m pytest -q
```

Files (all under `test/`):

```text
test_input_draft_guard.py test_input_draft_fifo.py
test_unified_message_fifo.py test_reply_delivery_polling_gate.py
test_reply_delivery_start_completion.py test_omp_provider.py
test_omp_pane_execution.py test_pi_pane_execution.py test_pi_omp_completion.py
test_claude_execution_runtime_start.py test_claude_execution_polling.py
test_claude_queued_prompt_activation.py test_claude_activation_enter_retry.py
test_codex_execution_polling.py test_provider_execution_service_runtime.py
test_provider_execution_active_resume.py test_v2_completion_tracker.py
test_v2_ccbd_dispatcher.py test_v2_message_bureau_dispatcher_integration.py
test_terminal_runtime_tmux.py test_terminal_runtime_tmux_input.py
test_terminal_runtime_tmux_send.py test_active_job_followup.py
```

Coverage includes 35 captured native ANSI fixtures, ghost versus accepted draft,
fake-clock boundary/early empty/unknown/rebind, one failed-clear attempt,
ask-before-back and back-before-ask pre-claim ordering, cancellation and new
head timer, no terminal I/O under dispatcher transition lock, final sender
deferral, persistence and all three provider resume paths, timeout exclusion,
tracker restart, OMP dispatch-after-guard and ambiguous-send failure, read-only
binding lookup, modal focus and disabled Claude activation retry.

Two existing transcript-only dispatcher fixtures now explicitly stub pre-claim
binding resolution as unprotected, matching their fake non-tmux backends and
absent session files. Production missing bindings still block protected paths.
The FIFO fixture accepts either ACCEPTED or QUEUED while unclaimed; neither is
RUNNING. No regression assertions were removed.

`git diff --check` and compileall of affected runtime modules passed.

## Actual 180-second native experiment

Dedicated project/state and tmux socket:
`/var/tmp/ccb-draft-v1-UOGS5v/`. Real native versions: Codex 0.155.1,
Claude 2.1.278, OMP 18.2.6. Fake credentials and loopback-only failing endpoints
prevented model calls; no working user pane or external auth/config was changed.

`experiment.py run` used production DraftGuard, DraftTarget and TmuxBackend,
with the generated OMP completion extension. It pasted a two-line synthetic
draft into each native composer, observed the actual 180-second wait, cleared,
confirmed empty, and sent `CCB_DRAFT_V1_DELIVERY no tools` through normal transport.
This was not a shortened/fake-clock terminal experiment.

| Provider | Empty confirmed (seconds) | Send finished (seconds) |
| --- | ---: | ---: |
| Codex | 180.10 | 180.67 |
| OMP | 180.10 loop timestamp | 181.30 |
| Claude | 181.80 | 182.35 |

Elapsed stamps share the loop start; transport operations are sequential.
Claude's first readback was not yet rendered, so a later poll confirmed empty;
there was no second clear. All three final native screens showed only the new
message, not the synthetic draft. Records: `results.jsonl` in the directory.

After the final OMP focus/recovery change, `focus_recovery.py` additionally
restarted the disposable OMP process using the current generated extension,
verified recovery of the killed process's stale socket, multiline native
clear/readback, model-picker `unknown`, and empty after Escape. Records:
`focus-recovery.json`. This uses the updated production focus check.

Earlier 24 idle Codex/Claude clear/readback cases and live Claude ghost tests
remain in [deep probe evidence](codex-claude-composer-deep-probe-20260920.md).
OMP API provenance is in [native probe evidence](composer-native-probe-20260920.md).

## Limits and handoff

This verifies source integration with deterministic dispatcher tests and real
native editor behavior; it is not a full installed ccbd/model-completion campaign.
OMP requires the newly launched extension and currently qualifies only default
Status Band focus. Custom keybindings, Vim and arbitrary attachment/modal/UI
variants are not generally qualified. Capture/clear/paste/Enter remains
non-atomic against simultaneous human input, including the existing Enter delay.

No commit, install into working user sessions, push or release was performed.
The disposable tmux server was shut down after testing; test files remain for
inspection. Deploying requires an updated daemon and newly launched OMP bridge.
