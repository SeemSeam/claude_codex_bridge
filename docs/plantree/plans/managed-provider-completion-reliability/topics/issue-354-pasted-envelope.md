# Issue 354: Claude pasted request envelopes

Date: 2026-10-05
Mode: status-update. Isolated candidate implemented; native/Hook/polling verification passed.

## Scope and cause

Claude 2.1.283 has a feature-gated native pasted_content serializer. Polling
and Stop-hook transcript attribution both require CCB_REQ_ID at the beginning
and reject the resulting envelope. Missing hook events also disable the exact
orphan-hook fallback. Do not change timeout policy or provider settings.

## Candidate

Use one dependency-light Claude-specific envelope helper from both consumers.
Accept exactly one complete envelope, identical case-sensitive opening/closing
IDs and surrounding whitespace. Allow nonempty IDs without quotes, angle
brackets or whitespace; current four-hex IDs are observation, not a stable API.
Reject nested/sibling wrappers and any non-whitespace outside. Preserve the
original anchored request-ID checks, parent-chain traversal, queue activation,
subagent and tool-result exclusions. Leave unwrapped text unchanged.

## Acceptance and verification

1. Real native PTY paste in a disposable test project; record actual version,
   input, native transcript, answer and whether wrapper was generated naturally.
2. Reproduce both failures before repair using the issue's exact envelope shape.
3. Verify normal/invalid input, UUID IDs, whitespace, foreign IDs, nested and
   sibling wrappers, sidechains, tool results, intermediate attachments, queued
   activation, empty replies and historical-turn fencing.
4. Execute real finish-hook entrypoint and polling state machine, then focused
   Claude/hooks suites and broader completion/execution/dispatcher regression.
5. Live success requires an available endpoint. Forced/synthetic envelopes must
   never be reported as naturally occurring client output.

## Isolation and rollout

Candidate worktree: /var/tmp/ccb-issue354-fix-20261005, base 18bf0af07 (v8.7.5
publication evidence). Test root: /home/bfly/yunwei/test_ccb2/issue354-20261005.
Existing source/work environments remain separate. No external config/auth
repair, feature-flag changes, release, push or issue reply is part of this task.
Rollback is removal of this small source patch before deployment; runtime
validation must use a newly launched test daemon to avoid stale loaded code.

## Evidence

[Real-client verification and regression results](../evidence/issue-354-live-20261005.md):
three accepted native PTY/remote-model cases, dual actual Stop hooks, unmodified
native-log replays, 735 passing automated tests and one opt-in skip. New 61-case
regression: baseline 33 failures; candidate all pass. Candidate not deployed. Full mounted ask/mailbox/callback smoke remains a pre-deployment gate.

## PR #403 follow-up (2026-11-09)

Mode: status-update. The user authorized reverting PR #403's replacement of
the already implemented shared parser from `0eebd8fea`. The revert restores
`unwrap_claude_pasted_prompt` in polling, keeping it consistent with Stop-hook
attribution. Existing compatibility and ambiguity tests are retained. The six
ID-format failures and one malformed-tag failure were regression fixtures;
they do not establish failures in current native four-hex client output.
See [revert verification](../evidence/pr403-revert-20261109.md). Historical
candidate/deployment status above describes the original 2026-10-05 probe.
