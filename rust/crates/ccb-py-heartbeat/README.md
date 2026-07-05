# ccb-py-heartbeat

PyO3 extension module that exposes the Rust `ccb-heartbeat` crate to Python.

This is a drop-in replacement for Python `lib/heartbeat/`:
- `HeartbeatAction`
- `HeartbeatPolicy`
- `HeartbeatState`
- `HeartbeatDecision`
- `evaluate_heartbeat(...)`
- `HeartbeatStateStore`

## Build (development)

With `maturin` installed:

```bash
maturin develop
```

Without `maturin`, build the cdylib manually:

```bash
cargo build -p ccb-py-heartbeat
cp target/debug/libccb_py_heartbeat.so \
   ccb_py_heartbeat$(python3 -c 'import sysconfig; print(sysconfig.get_config_var("EXT_SUFFIX"))')
PYTHONPATH=. python3 tests/smoke.py
```

## Test

```bash
cargo test -p ccb-py-heartbeat -- --test-threads=1
PYTHONPATH=. python3 tests/smoke.py
```

## Integration into CCB

Replace `from heartbeat import ...` with `from ccb_py_heartbeat import ...`.
The store constructor currently takes a root path string instead of a
`PathLayout` object; a thin Python shim can adapt the existing `app.paths`
call sites for production use.
