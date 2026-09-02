# Magic Context Shared Storage

## Feature Overview

CCB isolates each managed Pi, OMP, and OpenCode process under its own provider
home and XDG data directory. Magic Context uses one shared SQLite database and
per-project identifiers inside that database, so its storage directory must not
follow CCB's per-agent provider isolation.

CCB injects `MAGIC_CONTEXT_STORAGE_DIR` into supported Magic Context hosts so
all managed processes resolve the same database directory.

## Supported Flow

- Pi, OMP, and OpenCode pane launches receive `MAGIC_CONTEXT_STORAGE_DIR`.
- Pi and OMP headless launches receive the same variable.
- An explicit absolute `MAGIC_CONTEXT_STORAGE_DIR` from the user takes highest
  precedence and is forwarded unchanged.
- Relative explicit paths are rejected because the Magic Context contract
  requires an absolute complete storage directory.
- Other providers do not receive this variable from the integration helper.

When the user does not provide an override, CCB derives a shared default from
the source user's platform data directory:

- `XDG_DATA_HOME/cortexkit/magic-context` when `XDG_DATA_HOME` is set.
- `%LOCALAPPDATA%/cortexkit/magic-context` on Windows, with the source user's
  `AppData/Local` directory as fallback.
- `~/Library/Application Support/cortexkit/magic-context` on macOS.
- `~/.local/share/cortexkit/magic-context` on Linux and other Unix platforms.

## Backend Design

`lib/provider_core/caller_env.py` owns provider filtering, explicit override
validation, and platform-aware default resolution. The native CLI launcher and
execution adapter inject the resolved value into pane and headless processes.
OpenCode's dedicated launcher uses the same resolver.

`lib/runtime_env/control_plane.py` allows the explicit environment variable to
survive keeper and ccbd process boundaries before provider launch.

## Persistence Contract

CCB does not create or migrate the Magic Context database. It supplies one
absolute directory shared by supported hosts. Magic Context owns `context.db`
and isolates project data using its internal project identifier.

## Verification Status

Focused tests cover explicit overrides, relative-path rejection, control-plane
propagation, pane and headless injection, XDG defaults, and Linux, macOS, and
Windows fallback paths.

Verified with:

```text
env -u MAGIC_CONTEXT_STORAGE_DIR pytest -q \
  test/test_magic_context_provider_env.py \
  test/test_runtime_env_control_plane.py
```

Result: `30 passed`.

`python -m py_compile lib/provider_core/caller_env.py` and `git diff --check`
also pass.

## Known Limitations

All processes sharing a directory must use Magic Context versions with a
compatible database schema. CCB does not coordinate Magic Context migrations.
