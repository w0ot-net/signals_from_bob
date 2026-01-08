# Alice Tick Send Refactor Phase 1 Plan

Status: draft

## Goal

Extract the receive/response handling in `tick()` into dedicated helpers so the
recv logic and response-state updates are written once and reused, reducing
repeated code while keeping behavior and performance the same.

## Non-Goals

- Change polling, keepalive, window/pacer gating, or transport permit behavior.
- Alter protocol framing, asymmetry rules, or reliability behavior.
- Run tests here.

## Affected Components

- sfb/tunnel/alice_tunnel.py

## Current Duplication (What We Will Remove)

- Two recv paths both call `_handle_response(data, now)` and repeat the same
  `received_any`, `received_valid`, and `last_resp_kind` updates.
- The response-state updates (clear `_has_pending_data_acks`, set
  `_last_was_pong_only`, update `_pong_grace_remaining`) are inline and tightly
  coupled to the recv loops, making the recv logic longer than it needs to be.

## Detailed Plan

1. Add `_drain_transport_responses(now)` to `AliceTunnel`:
   - Move the main non-blocking recv loop from `tick()` into the helper.
   - Inside the helper, keep the exact call ordering and timeouts:
     - First loop: `self._transport.recv(timeout=self._config.non_blocking_poll_timeout)`.
     - Second loop: only if no responses were seen and pending count is above
       threshold; use `self._transport.recv(timeout=0.05)`.
   - Keep the same `hasattr(self._transport, 'pending_count')` and
     `max_in_flight` fallback logic when computing the pending threshold.
   - Keep the exact `_handle_response(data, now)` call and update the same
     three locals: `received_any`, `received_valid`, `last_resp_kind`.
   - Return `(received_any, received_valid, last_resp_kind)`.

2. Add `_update_response_state(received_valid, last_resp_kind)`:
   - Move the response-state updates into this helper:
     - If `received_valid` and `data_unacked_count() == 0`, clear
       `_has_pending_data_acks`.
     - If `last_resp_kind` is set, update `_last_was_pong_only` based on
       `last_resp_kind == 'keepalive'`.
     - If `last_resp_kind == 'has_segments'`, reset `_pong_grace_remaining`.
   - Do not change the predicates or the order of updates.

3. Update `tick()` to use the helpers:
   - Replace the inline recv loops with
     `(received_any, received_valid, last_resp_kind) = _drain_transport_responses(now)`.
   - Replace the inline response-state block with
     `_update_response_state(received_valid, last_resp_kind)`.
   - Keep the timeout and retransmit logic exactly where it is today.

## How This Decreases the Amount of Code

- The two recv paths currently duplicate the same `_handle_response` and
  bookkeeping block. Moving those into `_drain_transport_responses()` removes
  one full copy of that logic from `tick()`.
- The response-state update block is no longer embedded in the middle of the
  recv logic. Turning it into `_update_response_state()` removes that inline
  block from `tick()` entirely.
- Net result: `tick()` loses the repeated recv-handling lines and keeps only
  one call site for each helper, shrinking the method body without changing
  behavior.

## Testing

- Do not run tests here. The user can run tests with python3 if needed.
