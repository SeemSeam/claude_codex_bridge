# Native provider local installation qualification

Date: 2026-11-02. User authorized local installation testing, followed by a
commit only after verification passes. No push or release was authorized.

## Installed artifact and isolation

Built the dirty working tree using `scripts/build_linux_release.py --allow-dirty`
and installed the resulting Linux x86_64 preview with its `install.sh install`.
The artifact preserves version 8.7.20 and labels itself preview, not a new
published release. Its base revision is `a89847333`.

- Local test prefix: `~/.local/share/ccb-previews/native-ingress-20261102/app`.
- Executable: `~/.local/share/ccb-previews/native-ingress-20261102/bin/ccb`.
- Separate `home`, `bin`, managed Python venv and temporary Provider projects.
- Artifact SHA256:
  `adcf00774ac4cf32be72e99b6222260bab54a6a2db5e8d74faa83cacc7c12305`.
- Installer entrypoint smoke and installed `ccb --version` passed and identify
  the installed preview prefix. Eleven changed production files match the
  source byte for byte, including both new native transport modules.
- Normal `~/.local/bin/ccb` still selects the existing stable installation;
  no live project daemon or agent was restarted, no business task replayed.
- Installer ran with a whitelist environment and isolated HOME/XDG/Codex roots.
  Role provisioning and Droid registration were disabled. Required Mobile Relay
  Python dependencies were installed into the preview venv. An initial run
  that deliberately skipped those dependencies failed on missing cryptography;
  enabling the required dependency provisioning fixed the install without a
  product-code change.

## Acceptance results

- Source provider/storage/composer/launcher/projection/packaging regression:
  **498 passed, 2 skipped**. Skips remain environment-gated existing tests.
- Installed payload regression, run from the installed `app/test` with its
  installed `lib`: **148 passed**. This includes native OpenCode binding,
  WAL sidecar preservation, unsupported-schema errors, OMP task delivery,
  ambiguity/rejection handling, draft protection, and the earlier updater tests.
  Pytest itself came from the developer venv; product imports came from the
  installation. The artifact includes earlier updater/Mobile working changes;
  those changes are excluded from this provider repair commit.
- Installed OMP integration:
  `test/omp_reply_native_smoke.py --app-root <prefix>/app --native-task` passed
  using actual OMP 18.13.3 and Bun 1.4.4. The model fixture is local only.
  Native task admission starts a real streamed tool turn without terminal
  submission; two busy reply admissions reach durable native receipts; exactly
  one anchored request completes; duplicate messages, supersession and draft
  loss are absent. Completed in approximately 1.9 seconds.
- Installed OpenCode integration:
  `test/opencode_native_smoke.py --app-root <prefix>/app` passed using actual
  OpenCode 1.19.5 and a disposable loopback service. Session/workspace binding
  and structured native messages were validated without a model call.
- `git diff --check` passed.

## Limits and follow-up

This qualifies the installed local preview and source repair, not a stable
rollout. OMP extensions in existing processes remain unchanged until a guarded
idle-agent refresh. OpenCode v2.2.4 causal/completion mapping remains unsupported;
the reported SQL schema now fails explicitly before task dispatch. The v1 API
adapter is opt-in and requires an exact existing session binding. Legacy WAL
snapshots add temporary disk I/O and can be discarded during concurrent writes.
