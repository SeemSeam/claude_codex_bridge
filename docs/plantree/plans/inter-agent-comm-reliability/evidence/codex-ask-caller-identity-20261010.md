# Codex ask caller identity under restrictive shell inheritance

Date: 2026-10-10

Status: local source repair on `fix/codex-ask-identity-20261010`, based on
`origin/main` at `721be82903a3a2584bde4f455c4ee96fa4d652c1`.
Owner authorized repair, real verification and commit. Publication and an
upgrade/restart of the existing work-environment agents are separate actions.
The repair is isolated in `/var/tmp/ccb-codex-ask-identity-20261010`; unrelated
changes in the owner's main checkout are preserved.

## Root cause

The original Demo task `job_ddf848b1c245` completed normally, but its message
`msg_e3eaa713cb04` recorded `from=user`. The reply was saved as
`rep_df6c872d129e`; no agent4 return event was generated because `user` is a
non-mailbox actor.

The native Codex parent process had CCB caller identity, while its tool shell
lost it under the inherited `shell_environment_policy.inherit = "core"`.
Agents shared the project workspace, so `resolve_ask_sender` had no unique
workspace fallback and resolved to `user`. This is a launcher/environment
compatibility gap, not a Demo execution/completion failure.

The sender, caller environment and Codex home-config blobs are unchanged
between CCB v8.7.6 and v8.7.8. Native CLI 0.159.2 and 0.161.0 both reproduced
the identity loss under `core`. The old successful request proves identity
previously reached a tool shell; there is no old config snapshot establishing
when the inheritance policy changed. Do not attribute the incident to a
specific release without that evidence.

Prior diagnosis artifacts: `/var/tmp/ccb-demo-comm-20261009/diagnosis.md`,
`ccb-version-comparison.json` and `native-env-comparison.json`.

## Repair and authority boundaries

The Codex launcher adds launch-only `-c shell_environment_policy.set.KEY=VALUE`
leaf overrides for six routing fields: `CCB_CALLER_ACTOR`,
`CCB_CALLER_RUNTIME_DIR`, `CCB_SESSION_ID`, `CCB_CALLER_PROJECT_ROOT`,
`CCB_CALLER_PROJECT_ID` and `CODEX_RUNTIME_DIR`.

The overrides follow user startup arguments and precede terminal resume/fork
arguments, so current identity replaces stale launch values. Both the native
CLI/fallback and the separately spawned managed app-server receive them;
server tools cannot rely on overrides supplied only to the remote UI.

The change does not write external Provider config/authentication, change
inherit/include_only/exclude, replace unrelated set entries, or project the
full provider environment. PATH, API credentials and previous task ids are
outside the projection. Explicit `include_only` remains authoritative: users
who configure that additional filter must allow the routing fields for agent
asks. This repair does not widen their allowlist.

## Regression verification

Both suites passed on the final source candidate:

```sh
uv run --with pytest --with cryptography --with aiohttp --with watchdog \
  pytest -q test/test_v2_runtime_launch.py test/test_codex_start_cmd_parsing.py \
  test/test_codex_app_server_followup.py test/test_v2_ask_service.py \
  test/test_provider_profiles.py
```

Result: **437 passed in 30.80s**.

```sh
uv run --with pytest --with cryptography --with aiohttp --with watchdog \
  pytest -q test/test_codex*.py \
  test/test_v2_message_bureau_dispatcher_integration.py \
  test/test_runtime_env_control_plane.py test/test_unified_message_fifo.py \
  test/test_reply_delivery*.py test/test_input_draft*.py
```

Result: **533 passed in 20.72s**. Suites overlap; these are not 970 distinct tests.
Added coverage exercises two caller names, local/fresh/resume/fork/remote
launches, server identity projection, stale startup identity, quoted Unicode
paths and the exclusion of unrelated environment fields. Existing tests cover
sender fallback, wrong-project runtime identity, provider inheritance,
permission/resume compatibility and command templates. `git diff --check`
passes. The change stays within the Codex launcher, regression tests and
verification documents.

## Native tool execution

`/var/tmp/ccb-codex-ask-identity-20261010-probe.py` drives the actual Codex
0.161.0 CLI using a deterministic loopback Responses endpoint and synthetic
API key. The fixture receives actual `custom_tool_call_output`, rather than
assuming that a requested tool call executed.

Four final cases pass: baseline/fixed with `core`, and baseline/fixed with
`none`. Baselines retain the deliberately stale set actor and lose runtime
and session identity. Fixed shells contain the current actor/runtime/session,
retain an unrelated `KEPT_SETTING`, and omit the excluded synthetic private
sentinel. The source config remains byte-for-byte unchanged. Custom excludes
include `CCB_*`; the explicit set overrides still take effect. The runtime
path contains both quotes and Unicode.

Artifacts: `/var/tmp/ccb-codex-identity-evidence-20261010/native-*/result.json`.
These cases qualify real CLI/tool behavior, not remote model-service behavior.

## Real model and mailbox roundtrip

`/var/tmp/ccb-codex-identity-real-probe.py` starts a disposable candidate ccbd
project with actual Codex 0.161.0 and actual OMP 18.8.3 Demo. Both use their
inherited real model services. The test intentionally covers real inherited
Provider configuration; it does not read or report authentication material.
The Codex shell retains `inherit=core`. A native human startup prompt starts
its first turn; the test does not manufacture an active CCB parent job or
invent a chain dependency.

The first complete run, project `real-e8537e14`, passes:

- Actual Codex tool output shows `caller`, its own runtime directory and
  launch session id.
- One bare `command ask demo` through a quoted heredoc; no explicit sender,
  chain or silence. Native session evidence counts exactly one ask tool call.
- Task `job_5c4755f7cf2c` records `from=caller`, one completed Demo attempt,
  and reply `rep_0ea1dc2d6e54` containing `CCB_REAL_IDENTITY_2a8ec276` and
  `17 + 25 = 42`.
- Caller receives `CCB_REPLY`, finishes with
  `REAL_REPLY_VERIFIED CCB_REAL_IDENTITY_2a8ec276`, and its `task_reply` event
  is `consumed`. Both queues and pending-reply counts drain to zero.
- Managed app-server is enabled; test-owned runtime is stopped through
  `ccb kill` after acceptance.

A final qualification rerun uses an explicitly allowed external test root
(`CCB_SOURCE_ALLOWED_ROOTS`) instead of the diagnostics-only override. Project
`real-c8feff6d` passes the same gates: job `job_f6318c6717b9`, reply
`rep_6226ae6fd4e9`, marker `CCB_REAL_IDENTITY_4069b49c`, current caller identity,
exactly one native ask tool call, correct calculation, consumed return event,
empty queues and successful test-project cleanup.

Each successful project's `results.json`, `caller-screen.txt`,
`cli-trace-<job>.log` and `cli-queue---detail-all.log` are retained under
`/var/tmp/ccb-codex-identity-evidence-20261010/`.

Earlier harness attempts are not acceptance evidence: one used an unallowed
source-test root; an attempted fixture setup inherited the actual Codex source
instead; one used the wrong capture method; another checked reply consumption
before the returned turn ended. Their test-owned projects were cleaned up.
The final harness waits for both native verification and consumed lineage.

## Activation

The new projection is applied when CCB constructs a Codex launch. An already
running Codex/app-server retains its original config until a controlled
restart/update. No existing Agent was restarted, no installed release was
replaced, and no version/release metadata is changed by this commit.
