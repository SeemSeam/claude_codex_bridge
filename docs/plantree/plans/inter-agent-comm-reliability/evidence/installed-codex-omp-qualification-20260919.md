# Installed Codex / OMP Qualification

Date: 2026-09-19
Role: evidence
Status: real FIFO passed for both providers; Codex empty notice passed; OMP empty terminal qualification open
Related: [roadmap](../roadmap.md), [shared gate](ask-back-shared-turn-end-gate-20260919.md)

## Installed Artifact And Isolation

Owner requested installation and real Codex/OMP tests in an independent
project. Installed the current working-tree snapshot with `install.sh install`
into `/var/tmp/ccb-real-fifo-ukpp4c/installed`. Installer HOME and bin were
isolated under `/var/tmp/ccb-real-fifo-ukpp4c/install-home`; no default global
CCB wrapper was replaced. This is a locally modified snapshot retaining
8.6.18 metadata, not a newly published official release.

External test project: `/var/tmp/ccb-real-fifo-ukpp4c/project`.
Configuration: `cmd; cx:codex, om:omp`. Each provider uses project-owned
managed state through existing CCB authority preparation. Test CLI calls
remove the parent agent's caller/session environment. Real external user
state is an inheritance input, not a new login destination.

Installed and source `dispatcher_runtime/polling_service.py` SHA256 match:
`76572fd693e25d667bb332a7bcb5af814978f3ce459cea06ec4ca3a4a7f6e39d`.
The test project's reported daemon/panes bind to its own tmux socket. No
work-environment daemon was restarted. Installed Python dependencies and Rust
helpers were prepared by the installer; the initial isolated PATH lacked
Rust and was corrected before the successful installation.

Codex pane: CLI 0.154.0, gpt-5.6-sol high. OMP pane: DeepSeek V4 Flash.
Both real providers returned exact SMOKE_OK markers before qualification.

## Chronological Ask / Back FIFO

The external controller submitted source work with the destination agent as
caller. Its real result generated an ordinary reply_delivery A. The target
was instructed to execute `/bin/sleep 25` while processing A, then return
A_PROCESSED. While A ran, controller admitted an ordinary ask X, then another
source result B. Provider outputs were A_PROCESSED, X_PROCESSED, B_PROCESSED.

During A, both targets independently showed A running, X queued, and B pending
without a started delivery job. Durable job journal RUNNING timestamps verify
the following ordering (UTC):

| Target | A delivery | A ends | X starts | X ends | B starts |
| --- | --- | --- | --- | --- | --- |
| Codex cx | job_949f9dce63df | 01:56:17.379716 | 01:56:18.438661 | 01:56:25.006391 | 01:56:26.067737 |
| OMP om | job_bdbdd1a7827e | 01:57:12.254285 | 01:57:13.337907 | 01:57:15.992616 | 01:57:17.054340 |

Codex X/B: job_734483c5698e / job_630dd1ae4428.
OMP X/B: job_333d38478a2b / job_9602a91e8fb8.
Both A deliveries have one attempt and zero downstream replies in `ccb trace`;
processing a back did not generate a recursive return loop.

The first controller run incorrectly asserted only `accepted` for queued X;
real daemon state was `queued`. This was a probe assertion error, not an
overlap. After allowing both pre-execution states and draining that run, the
full two-direction campaign completed with CAMPAIGN_PASS.

## Empty Results

Codex job `job_0dfd6b1e5dee` was explicitly asked to end with no visible body.
It emitted a real empty terminal result, finalized as
INCOMPLETE/task_complete_empty_reply, and produced exactly one
`notice=true, notice_kind=empty_result` ReplyRecord. Notice body advises the
caller to inspect status/history and retrieve the existing result. Trace
shows one attempt, retry index 0; later inspection found no additional attempt.

OMP job `job_731f01028256` was given the same empty-body instruction. Native
events recorded empty assistant text with stop_reason=stop, but agent_end
reported `will_continue=true` (01:57:42.979 and 01:57:46.010 UTC), with no
agent_settled event for this request during observation. CCB therefore kept
the job running and did not fabricate a final empty-result notice. There was
one CCB attempt; native Provider continuations must not be counted as daemon
automatic resubmission. This case does **not** qualify OMP empty terminal
handling: further investigation of the native continuation boundary remains.

The controller cancelled only that test job at 02:00:19.142213 UTC through
`ccb ask cancel`; trace confirms CANCELLED with the normal cancellation notice.
Afterwards both test agents were idle and both queues were empty. Their
managed test runtime and evidence were retained for inspection.

## Reproduction And Limits

Local controller and artifacts under `/var/tmp/ccb-real-fifo-ukpp4c/`:
`run.py`, `install.log`, `smoke.py`, `campaign.py`, `campaign-evidence.json`,
`empty_probe.py`, `verify_records.py`, `empty-probe-evidence.json`, plus the
test project's `.ccb/agents/*/jobs.jsonl` and `.ccb/ccbd` lineage stores.
ReplyRecords are joined via attempt_id, not job_id. The empty probe snapshot
records OMP as running before cleanup; its later reply is the cancellation.

No fake provider was used for these cases. No Claude, model-switch,
provider-crash, simultaneous mutual-ask, or real restart recovery campaign
was performed in this round. No commit, push, or release publication occurred.
Temporary artifacts can expire; the task IDs and results above preserve the
acceptance scope. Installation is isolated, not promotion of the shared
work-environment installation.
