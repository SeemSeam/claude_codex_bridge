# Pi Transformed-Input Binding

Date: 2026-10-12

## Observed Failure

A Pi 1.1.0 image editor replaced an image path in a CCB ask with `[Image #1]`
before the lifecycle extension received the input. Attachment metadata also
changed the prompt. The submitted text therefore differed from the dispatch
SHA256 while its first-line `CCB_REQ_ID` remained intact.

The lifecycle sidecar contained tools, turn boundaries, and `agent_settled`,
but their `req_id` was empty. CCB ignored those unbound events and kept the job
running, blocking subsequent asks. The extension instance was unchanged:
this reproduction does not require a lost event stream or stale restore.

Cancelling that stale job also sent Ctrl-C, Escape, and Ctrl-U to the pane even
after Pi had settled. Those unscoped keys can affect the editor or exit the
provider, exposing the next queued delivery to a failed send.

## Binding Contract

- Newly wrapped Pi asks carry a random 32-hex `CCB_DISPATCH_ID` on the second
  line. The same value is persisted in submission state and the dispatch log.
- The extension accepts an exact prompt digest, preserving existing and raw
  `no_wrap` behavior, or the exact first-line request id and second-line
  dispatch id. Both paths still require matching actor, launch session, live
  extension instance, schema, and an unconsumed dispatch record.
- Body transformations may preserve the two headers. The request id alone,
  a wrong nonce, a consumed nonce, or a foreign actor/session/instance cannot
  bind the transformed prompt.
- An anchored input that cannot bind at `before_agent_start` emits an explicit
  unmatched `request_start`. CCB terminalizes it as incomplete with
  `pi_request_binding_invalid`, rather than attributing an unrelated reply or
  remaining running indefinitely.
- `agent_settled` remains the sole successful Pi terminal boundary. Neither a
  visible final reply nor `turn_end` substitutes for it.
- Restore preserves the dispatch header for unsent prompts and the exact
  native instance for sent prompts. Existing persisted jobs remain compatible
  with digest matching; no completed or unbound legacy work is resubmitted.

## Cancellation Contract

Pi pane cancellation reads the lifecycle sidecar and sends Escape only for a
sent job whose request id is currently active on the exact extension instance.
It leaves idle, human, superseded, foreign-instance, partially written, and
unsent states untouched. The existing OMP and legacy Pi headless paths retain
their cancellation behavior. Queue release remains owned by the dispatcher.

## Verification

- [Extension/adapter replay](../../../../../test/test_pi_dispatch_binding.py)
  covers image transformations, exact legacy input, explicit legacy binding
  failure, foreign identities, nonce replay, restored completion, `no_wrap`,
  and unsent nonce persistence.
- [Pane execution tests](../../../../../test/test_pi_pane_execution.py)
  cover exact-active interruption, untouched unrelated turns, and a settled
  unbound job followed by a successful new delivery.
- Existing Pi/OMP lifecycle, native launch, execution restore, and dispatcher
  cancellation tests remain the compatibility gates.

The change writes only CCB-owned completion/dispatch state. It changes neither
external Pi settings nor authentication, model choice, or plugin files.
