# ccb-py-mailbox

PyO3 extension module that exposes the Rust `ccb-mailbox` and `ccb-message-bureau`
crates to Python with minimal model wrapping.

## Submodules

- `ccb_py_mailbox.mailbox_kernel` — `MailboxKernelService` and mailbox enums.
- `ccb_py_mailbox.message_bureau` — `MessageBureauFacade`, `MessageBureauControlService`,
  and message-bureau enums.

Models are passed as plain Python `dict`s (serialized via JSON) wherever possible.

## Build (development)

With `maturin` installed:

```bash
maturin develop
```

Without `maturin`, build the cdylib manually:

```bash
cargo build -p ccb-py-mailbox
cp target/debug/libccb_py_mailbox.so \
   ccb_py_mailbox$(python3 -c 'import sysconfig; print(sysconfig.get_config_var("EXT_SUFFIX"))')
PYTHONPATH=. python3 tests/smoke.py
```

## Test

```bash
cargo test -p ccb-py-mailbox -- --test-threads=1
PYTHONPATH=. python3 tests/smoke.py
```

## Integration into CCB

Replace `from mailbox_kernel import ...` and `from message_bureau import ...`
imports with the `ccb_py_mailbox.mailbox_kernel` and `ccb_py_mailbox.message_bureau`
submodules. Constructors take a root path string; a thin Python shim can adapt
existing `app.paths` call sites for production use.
