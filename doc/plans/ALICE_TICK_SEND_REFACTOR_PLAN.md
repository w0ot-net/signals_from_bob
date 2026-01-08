# Alice Tick Send Refactor Plan

Status: draft

## Goal

Reduce duplication in Alice's tick send loop by extracting small helpers while
preserving behavior, performance, and readability.

## Non-Goals

- Change polling, keepalive, window/pacer gating, or transport permit semantics.
- Alter protocol framing, asymmetry rules, or reliability behavior.
- Run tests here.

## Affected Components

- sfb/tunnel/alice_tunnel.py

## Design Notes

- Preserve the phase ordering: receive -> timeout check -> retransmit ->
  send/poll -> window growth -> pacing sleep.
- Keep keepalive suppression when any control/data pending exists.
- Avoid extra allocations or attribute lookups on the hot path by passing
  explicit arguments and returning small tuples.
- Keep transport permit reservation/release centralized to avoid leaks.

## Plan (Three Phases)

### Phase 1: Receive/response extraction and reuse

1. Add `_drain_transport_responses(now)` inside `AliceTunnel`:
   - Move the non-blocking recv loop plus the pending-count threshold wait
     into a helper so the two recv paths share one implementation.
   - Keep the exact `self._transport.recv` timeouts (`non_blocking_poll_timeout`
     and the `0.05` threshold wait) and the `_handle_response` call flow.
   - Return a tuple `(received_any, received_valid, last_resp_kind)` so the
     caller does not recompute state.
2. Add `_update_response_state(received_valid, last_resp_kind)`:
   - Move the "clear pending data acks" check and the pong/grace updates into
     one helper.
   - Preserve the exact semantics: only clear `_has_pending_data_acks` when
     `data_unacked_count()` hits zero and only reset `_pong_grace_remaining`
     when `last_resp_kind == 'has_segments'`.
3. Update `tick()` to call the new helpers:
   - Replace the inline recv loops with one call to
     `_drain_transport_responses(now)`.
   - Replace the inline response-state block with `_update_response_state(...)`.

Code reduction in Phase 1:
- Eliminates the duplicated `valid, resp_kind = _handle_response(...)` block
  used once in the main recv loop and again in the pending-threshold recv.
- Removes the repeated `received_any/received_valid/last_resp_kind` updates
  scattered across two loops and places them in a single helper.
- Shrinks the top of `tick()` by turning ~two recv loops and a response-state
  block into two method calls.

### Phase 2: Consolidate send/poll paths into one helper

1. Add `_send_pending_or_poll(...)` that encapsulates the `while True` send loop:
   - Inputs: `now`, `serial_window`, `send_payload_limit`, and the
     `pending_mode_set` callable (or a small method).
   - Localize the `control_only`, `break_on_empty`, and `pending_mode` setup.
   - Return a tuple `(pacing_blocked, sent_any)` so `tick()` can decide on
     pacing/idle sleeps without re-deriving state.
2. Inside `_send_pending_or_poll(...)`, factor out the repeated segment-send
   sequence into `_try_send_segments(...)`:
   - Steps inside helper: reserve permit, compute payload cap, collect segments,
     send packet, or release permit.
   - Use a `control_only` flag and preserve `has_data_pending`/`notify_send_pending`
     behavior by passing it through to `_reserve_transport_permit`.
3. Inside `_send_pending_or_poll(...)`, factor out the repeated keepalive path
   into `_send_keepalive_or_break(...)`:
   - Keep the exact window-full branch: drop oldest keepalive, log, then send
     a keepalive or release permit and break.
   - Reuse the same keepalive send call for both "window_full" and "no segments"
     cases so the packet construction path is shared.
4. Keep the poll decision sequence unchanged:
   - Evaluate `_poll_decision(now)` only after pending sends are drained.
   - Preserve the `pending_mode_set` checks that suppress poll sends when
     control/data is pending.

Code reduction in Phase 2:
- Collapses two nearly identical blocks that each do
  `reserve permit -> payload cap -> collect segments -> send or release`.
- Reuses one keepalive send helper instead of repeating the keepalive logic in
  both the window-full and empty-segments branches.
- Removes repeated `self._collect_segments(...)` calls that are currently
  duplicated for pending sends and poll sends.

### Phase 3: Simplify `tick()` orchestration and local helpers

1. Replace the inline `pending_mode_set` closure with a small method or a
   pre-bound local function outside the loop:
   - Use a method like `_pending_mode_set(mode, pending_event, control_event, data_event)`
     or a simple local function built once per `tick()` call.
2. Rework `tick()` to read as a linear sequence:
   - `recv` phase (Phase 1 helpers) -> timeout check -> retransmit scan ->
     send/poll phase (Phase 2 helper) -> window growth -> pacing/idle sleep.
   - Keep all state updates (`_retransmit_budget`, `_tick_epoch`,
     `_tick_sleep_hint`, `_last_was_pong_only`, `_pong_grace_remaining`) in the
     same order they occur today.
3. Make sleep decisions use helper return values:
   - Use the `(pacing_blocked, sent_any)` return to decide whether to call
     `_sleep_for_poll_pacing` and whether to apply the idle sleep.
   - Keep existing conditions so behavior is unchanged.

Code reduction in Phase 3:
- Removes the nested `while True` send loop body from `tick()` and replaces it
  with a single helper call that owns the loop.
- Eliminates the inline closure and reduces per-iteration local variables,
  which makes `tick()` shorter and easier to scan.
- Consolidates the top-level `tick()` flow into a small sequence of helper
  invocations, cutting the method size while keeping the same logic.

## Testing

- Do not run tests here. The user can run tests with python3 if needed.
