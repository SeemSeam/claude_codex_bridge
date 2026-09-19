# OMP exact conversation resume

Date: 2026-09-19
Status: Committed in 4b966363b; included in the v8.6.19 release candidate

User authorized OMP context restoration aligned with other providers. Restart
must preserve the exact observed native conversation, not merely relaunch a pane.
Scope: OMP launcher/session binding and extension observations, regression tests.
Do not modify installed provider state, credentials or unrelated FIFO changes.

Resolve native identity from the current launch's owner-bound extension events,
then validate transcript header, agent/project/workdir and managed session path.
Persist selected identity in the next CCB session payload. Use OMP's supported
`--resume <path>`; respect restore-disabled and explicit session flags. Never
select the newest transcript by mtime. Invalid or absent evidence yields an
explicit fresh-session reason, never a false exact-resume claim.

Cover ordinary relaunch and session.start_cmd recovery, unmanaged interactive
turns, session switching, stale launch events, missing/corrupt/outside-root
transcripts, shell quoting and explicit overrides. Preserve OMP completion and
headless contracts. Validate focused OMP/Pi tests plus an isolated real OMP
resume without model calls; current demo is not a test fixture.

## Landed implementation and verification

OMP launcher now resolves the old launch observation before creating a new
sidecar and injects a quoted `--resume <native-path>` only when restore is enabled
and the user has not supplied session-selection flags. Session records separate
CCB launch IDs from selected native conversation IDs. Automatic pane recovery's
`start_cmd` also resolves current native evidence from the restart template.
The OMP extension records native identity on input, session switch and turn end,
including unmanaged interactive conversations; no Pi adapter edits were needed.

Validation on 2026-09-19: 236 tests passed (226 across OMP session history, provider,
pane execution, Pi history/execution, native CLI registry/execution/completion
and ccbd startup layout, plus 10 single-agent restart tests). This includes an opt-in real OMP 18.2.1 RPC test with an
isolated home, dummy loopback-only model and no model invocation: exact native ID,
pre-existing history message and extension-reported native path all matched.
Run with `CCB_OMP_NATIVE_SMOKE=/path/to/omp PYTHONPATH=lib .venv/bin/python -m pytest
-q test/test_omp_session_history.py`; without the env var the real-CLI case skips.

Limits: read the last 8 MiB of the current launch event sidecar; absent or invalid
identity yields an explicit fresh reason instead of guessing another history.
Previously lost bindings are not reconstructed by mtime. This change does not
repair the earlier OMP UI hang or retroactively select demo's pre-restart session.
Real CCB-managed pane replacement with this candidate is not yet qualified;
the real CLI test proves native history loading and extension interoperability.

Next: review/install qualification and controlled CCB restart acceptance.
No production runtime files, Provider credentials or current panes were changed.
