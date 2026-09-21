# Installed Draft Guard Qualification

Date: 2026-09-20
Role: evidence
Status: installed real-project normal-path qualification passed; explicit Codex crash-recovery limit retained
Related: [contract](../topics/input-draft-delivery-guard.md), [initial v1 tests](input-draft-guard-v1-20260920.md)

## Installation and real project

Installed a working-tree snapshot with `install.sh install` into
`/var/tmp/ccb-installed-draft-eGwpkj/installed`, with isolated installer HOME/bin.
The installer completed successfully, including managed Python and runtime
helpers. This is modified 8.6.19 source, not an official new release. The shared
work-environment wrapper/daemon was not replaced.

Real project: `/var/tmp/ccb-installed-draft-eGwpkj/project`, configured as
`cmd; cx:codex, om:omp, cl:claude`. Its own CCB daemon and tmux namespace ran all
tests, with standard one-way managed provider authority inheritance. No mock
model/endpoint was used; no external provider authentication was edited.

| Agent | Native CLI | Actual model shown |
| --- | --- | --- |
| cx | Codex 0.155.1 | gpt-5.6-sol high |
| om | OMP 18.2.6 | DeepSeek V4 Flash |
| cl | Claude Code 2.1.278 | Kimi-K3 |

All three used tools to create `qualification-<agent>.txt` in this real project
and returned the exact expected marker. File contents were checked independently.

## Faults found and repaired

1. The original full-screen `esc to interrupt` substring veto mistook ordinary
   historical replies for busy state. Real Codex and Claude jobs completed with
   that exact reply, but their next jobs stayed ACCEPTED. Codex also mistook
   draft words `Select Model`, `NORMAL` and `-- INSERT --` for UI state.
2. Real Claude tool execution can display an animated flower and elapsed time
   without the interrupt hint. The old observer then treated its composer as
   empty and sent a waiting CCB task into Claude's native queue during a manually
   initiated turn. This was a failed qualification case, not a passing FIFO test.

The corrected parser scopes status inspection outside the current composer,
recognizes Codex native busy rows, recognizes Claude's animated busy row even
without the hint, excludes native queued-message ghost prompts from emptiness,
and inspects editor-mode words only in the footer. Model-picker rejection still
passes using layout boundaries rather than a draft substring blacklist.

Twelve real installed screen captures were added as regression fixtures in
`test/fixtures/composer/installed-20260920.json`; an additional test covers the
Claude native queue hint between turns. Source and installed corrected parser
SHA256 both equal:
`78fd4eff34375e3538d706a704883447e587b59af5b975fea77a6398268aa4d0`.
The isolated daemon was restarted to load the correction.

Post-fix: both historical-phrase follow-up jobs completed without manually
changing the screen. All three manually initiated real `/bin/sleep 20` turns
held their CCB requests unclaimed while busy, then the requests completed.

## Verified scenarios

- Initial real 180-second multi-line draft test: release at 180.77 s (Codex),
  182.30 s (OMP), 182.29 s (Claude). Each agent's second ask remained queued until
  the first completed; journal timestamps establish no overlap.
- Model picker and tmux copy mode: all three blocked while open and automatically
  resumed after Escape/copy-mode exit, with successful real model responses.
- Narrow window: all three delivered successfully after resizing the project
  window to 120 columns (provider panes about 62 columns), then restoring size.
- Real back-before-ask and early manual draft release passed for all three.
  Back job IDs: cx `job_549b6ddce752`, om `job_2485090f838c`, cl
  `job_325b4d213331`. Each `ccb trace` reports one attempt, retry index zero,
  and zero downstream replies. Job journals verify the next ask starts only
  after that back's processing turn ends.
- Final corrected-installation 180-second run included `NORMAL Select Model
  esc to interrupt` plus two synthetic draft lines. All three recognized this
  as nonempty input, held both jobs before the deadline, then cleared and
  completed them in order: cx release 181.58 s, om 182.04 s, cl 182.93 s.
  First jobs: `job_11227caeecff`, `job_3509bccafed3`, `job_7dceb3e66b35`.
  Second jobs: `job_f4618c3e115d`, `job_80e62c2748e1`, `job_1f6b939c9300`.
  Every composer was empty afterwards and the queue snapshot had no active
  jobs, queued jobs or pending replies.

No synthetic draft marker appeared in submitted provider session transcripts.
Codex does retain cancelled draft text in its own `history.jsonl`; that native
editor history must not be confused with a message sent to the model.

## Daemon restart boundary

A SIGTERM was sent only to the exact test daemon after verifying its project
and executable in `/proc/<pid>/cmdline`. The keeper restarted it. OMP and Claude
preserved their nonempty drafts and queued jobs, and completed after manual
early clearing, with no extra task attempt.

Codex's remote app-server session could not reconnect following this injected
daemon failure. The native pane explicitly showed `app-server session could
not be restored`, retained the draft, and the guard correctly stayed unknown.
`ccb restart cx` was blocked by the existing queue-depth restart gate.
Recovery was explicitly tested: cancel the never-sent queued job, restart cx,
and resubmit its original body. The replacement completed. Original job
`job_b4aef4b33e51` is CANCELLED; replacement `job_1bf3a3835d6b` is COMPLETED.
This does **not** qualify automatic Codex recovery across daemon termination;
it is a provider/control-plane recovery limit, not an empty-composer false veto.

One rapid keyword-probe loop also hit Claude's native double-Ctrl-C exit
shortcut. That probe was corrected to space clear actions; production timed
clearing remains once per 180-second wait, never a rapid repeat. No production
clear policy was relaxed to bypass that native behavior.

## Regression and retained artifacts

469 tests passed in 16.37 s across the same 23 relevant files listed in
[v1 evidence](input-draft-guard-v1-20260920.md#deterministic-verification).
Compileall and `git diff --check` passed.

Artifacts under `/var/tmp/ccb-installed-draft-eGwpkj/`: `install.log`, `run.py`,
`qualification.py`, `qualification.jsonl`, `screens.jsonl`, `busy_capture.py`,
`busy_verify.py`, `narrow_verify.py`, `restart_guard.py`, `restart_finish.py`,
`codex_disconnect_recover.py`, and the project's durable jobs/attempts/replies.
The initial failed probes remain in the record; PASS events identify corrected
acceptance cases. `runtime-hashes.json` verifies all 16 changed runtime files
against the installed copy. No commit, push, release, or shared-environment
upgrade occurred. The isolated installation/project and evidence are retained;
the test project's background processes were stopped after the empty-queue check.
