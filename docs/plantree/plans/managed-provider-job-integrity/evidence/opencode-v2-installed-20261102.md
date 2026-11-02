# OpenCode v2 native transport local qualification

Date: 2026-11-02. User authorized implementation, local installation verification,
then commit. This is a supplemental slice after `d78fd31bd`; no push or release.

## Source and protocol authority

Qualified against real OpenCode 2.2.4, upstream revision
`45898d0f8569225eb1ddb4ee13f8581b34dff311`. Protocol/session/message schemas and
core execution/message projection define the native input-to-idle boundary.
Native assistant completion timestamps end individual steps, not entire tasks.
The pinned executable lives under `~/.local/share/ccb-previews/opencode-v2.2.4/`;
the existing OpenCode executable was not replaced.

Implemented exact service/session/workspace and prompt binding, one-shot native
queued admission, bounded paginated timeline reads, explicit native idle
terminalization, explicit password environment references, and exact pending
input cancellation. Admission uncertainty never causes replay. Native polling
ignores TUI liveness; no native task is cancelled through terminal keys.

## Verification results

- Final source regression: **359 passed, 2 skipped**. Includes OpenCode v1/v2,
  OMP, native launchers, projection and execution service tests. Existing skips
  remain environment-gated. Tests cover lost admission receipts, foreign inputs,
  malformed metadata, cursor cycles, multi-step tools, empty/failed/interrupted
  outcomes, continuous network failure timing and cancellation races.
- Final installed payload regression: **223 passed, 2 skipped**. Run from the
  installed `app/test`, importing installed `app/lib`; pytest came from the
  development venv. Eight changed production files match source byte for byte.
- Real installed v2 smoke passed: isolated HOME/XDG, explicit fixture password,
  loopback native server and local OpenAI-compatible model fixture. Real CCB
  start/poll observed native admission and held streaming without premature
  completion, then completed `LOCAL_V2_OK` only after native idle. A second
  request queued behind the stream was cancelled by exact input ID and removed
  from the real native inbox. No paid model or real Provider auth was used.
- Real installed v1 smoke passed: exact session/workspace and native structured
  messages against isolated OpenCode 1.19.5; no model request.
- Installed `ccb --version`, CLI v2 server/session flags and `git diff --check`
  passed. No business prompt was replayed or running agent restarted.

Real model smoke covers a text stream and queued cancellation. Multi-step tool
completion, malformed timelines and error paths are covered by contract tests,
not claimed as real model/tool integration coverage.

## Installed artifact

Prefix: `~/.local/share/ccb-previews/opencode-v2-native-20261102/app`.
Entrypoint: `~/.local/share/ccb-previews/opencode-v2-native-20261102/bin/ccb`.

Built with `scripts/build_linux_release.py --allow-dirty --git-ref v8.7.20`,
then installed with the artifact's `install.sh install`. Existing version/tag
identity is retained as the preview's base anchor (`a89847333`); the artifact
contains the current dirty working snapshot, including the v1/OMP prerequisite
and this v2 slice. It is not a new stable release or a clean tagged build.
An initial build using HEAD was rejected by the existing version/tag collision
gate; no gate or version metadata was changed.

Artifact SHA256:
`39164314bb90cba2feb13a8c93bb3318bceecc82ea182d10e736890eb0caed65`.

Installer environment was whitelisted with separate HOME/XDG/Codex roots,
install/bin prefixes and managed Python environment. Role/Droid provisioning
and watchdog were disabled; required Mobile Relay dependencies were installed.
The artifact includes pre-existing updater/Mobile working changes, which are
excluded from this slice's commit. The normal stable CCB entrypoint remains
unchanged.

## Limits and rollout

Opt-in only: `CCB_OPENCODE_API_VERSION=2`, explicit loopback API origin, exact
`--session ses_...`, and optional explicit password environment reference.
Unsupported versions/bindings fail before sending a task. Existing mounted
agents and Mobile discovery are not automatically migrated.

Upstream DELETE returns 204 even if the queued input was consumed concurrently.
CCB checks native projection after deletion and reports detected races. It does
not treat 204 as evidence that active work stopped. Atomic expected-input-ID
active interruption is unavailable in 2.2.x; active cancellation reports this
limit and native work may continue. Restart-time execution observation restore
also remains unsupported. Neither limitation permits automatic replay.
