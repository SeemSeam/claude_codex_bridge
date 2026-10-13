# PR #385 Merged-Source Verification

Date: 2026-10-13
Role: evidence
Status: merged and verified locally; pending the next release

## Source And Scope

- PR: [#385](https://github.com/SeemSeam/claude_codex_bridge/pull/385).
- Reviewed PR head: `c88de463afa3c566b988c2c690244d88e2bfdb73`.
- Tested merged main: `55d3303bffe00562a2e681646db729f575cda3b3`.
- Host: Linux, Python 3.14.4; Bun executes the actual generated extension.
- Verification used an isolated worktree, without replacing the installed
  runtime or changing external provider settings/authentication.

The fix preserves request attribution when image processing transforms the
prompt body while retaining its request/dispatch headers. Cancellation sends
Escape only for the exact active Pi request. The digest path, legacy persisted
jobs, raw `no_wrap` inputs and OMP behavior remain covered.
[Contract](../topics/pi-transformed-input.md).

## Verification

The following commands used the existing qualification interpreter at
`/home/bfly/.local/share/codex-dual/.venv/bin/python`, with Bun on PATH.

Core regressions: **54 passed, no skips**.

```sh
python -m pytest -q \
  test/test_pi_dispatch_binding.py test/test_pi_pane_execution.py \
  test/test_pi_omp_control_input.py test/test_omp_pane_execution.py --tb=short
```

Compatibility and integration regressions: **509 passed, 5 skipped**.

```sh
python -m pytest -q \
  test/test_pi_omp_completion.py test/test_pi_session_history.py \
  test/test_pi_interactive_resume.py test/test_pi_package_snapshot.py \
  test/test_omp_provider.py test/test_omp_session_history.py \
  test/test_omp_composer_bridge_recovery.py test/test_native_cli_providers.py \
  test/test_provider_execution_active_resume.py \
  test/test_provider_execution_service_runtime.py \
  test/test_provider_execution_common.py test/test_provider_execution_fake_runtime.py \
  test/test_execution_phase.py test/test_v2_ccbd_dispatcher.py \
  test/test_input_draft_guard.py test/test_input_draft_fifo.py \
  test/test_v2_runtime_launch.py test/test_provider_continuity_native.py --tb=short
```

The five skips are two opt-in real OMP history/resume cases and three opt-in
real Codex/Pi/OMP continuity cases. Bun dispatch replay, transformed image
completion, nonce reuse/foreign identities, restored completion and scoped
cancellation ran without skips. Pi/OMP Python compilation also passed.

The trusted-base platform isolation checker from trusted base `636afa2f2` passed against
merged main as a shared-provider change; the fix adds no platform ownership or
reverse-dependency changes. Documentation links and `git diff --check` passed.

## Integration And Release Status

The working branch was an ancestor of merged main. Existing local edits were
compared with upstream before synchronization so older draft copies would not
overwrite newer merged fixes. Original tracked edits and overlapping untracked
files are preserved in a local recovery snapshot; unrelated Mobile planning
edits and untracked files remain outside this integration commit.

The code is queued for the next version under the owner's instruction.
An authenticated Pi model roundtrip with transformed image input remains
unqualified by this verification. It must not be described as passing live
acceptance. Version metadata, tags and publication are deferred to the next
release preparation.
