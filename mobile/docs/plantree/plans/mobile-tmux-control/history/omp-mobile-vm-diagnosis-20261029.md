# OMP Mobile Android VM diagnosis — 2026-10-29

Status: testing completed; reported production incident not fully reproduced.

## Environment and scope

- Android emulator `emulator-5580`, API 35 x86_64.
- Bun 1.4.4; installed OMP 18.13.3; source-built debug Mobile client.
- Read-only live-project gateway on a separate loopback endpoint, with an
  isolated test pairing store. Production Relay host generation 13 was not
  replaced and production device credentials were not accessed.
- Existing `ccb update` work remains separate from this diagnosis.

## Observations

- Live `demo` and `agent3` native Collab snapshots report connected/ready;
  native history page requests succeed without history errors.
- Android can open the real project's `demo` workspace and native model list.
  The workspace shows Idle and an enabled composer, with no conversation.
- Both current OMP launch bindings record
  `fresh_no_current_session_observation`. The native sessions are new and
  contain only two initial records. Thus the empty live conversation is
  consistent with the native session; this is not evidence of a history
  download failure. Old transcript recovery was not attempted.
- Direct live-project viewing does not qualify the user's official Relay
  connection or a paid model round trip.

## Android encrypted Relay regression

Command (after sourcing `mobile/tools/mobile_toolchain_env.sh`):

```sh
.venv/bin/python test/omp_mobile_emulator_backend.py \
  --upstream /home/bfly/.bun/install/cache/@oh-my-pi/pi-coding-agent@18.13.3@@@1 \
  --relay --run --device emulator-5580 \
  --evidence /tmp/ccb-omp-vm-20261029/relay-smoke
```

Passed: `All tests passed!` (68-second Android test, excluding build/install).
Uses real upstream native Collab, production gateway/repository, isolated TLS
Relay and deterministic native fixture events; no paid inference.

- History: 14 pages / 198 items, 29 content pages with semantic hash checks.
- Sends, live/intermediate/durable turns, native model controls, errors,
  reconnect, stale generation rejection, progress/status, and history branch
  changes pass the existing integration assertions.
- FIFO: six prompts total, maximum one active prompt, zero active at finish;
  three consecutive busy-session inputs dispatch in order.

Local screenshots and proof JSON are under the evidence directory above.
The emulator was reinstalled with the normal debug client connected to the
live project after the fixture test; the separate diagnostic gateway remains
running for follow-up. No production gateway refresh or agent restart occurred.

## Remaining incident boundary

The reported symptom (disconnect, send failure, or missing history/replies) is
not yet specified. The isolated Relay pass must not be reported as proof that
the user's official Relay/device path is fixed. Continue from the exact
reported symptom and production route before changing runtime code.
