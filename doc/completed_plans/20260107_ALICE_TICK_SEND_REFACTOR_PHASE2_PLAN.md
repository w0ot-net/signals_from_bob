# Alice Tick Send Refactor Phase 2 Plan

Status: completed

## Goal

Collapse the duplicated send/poll logic inside `tick()` into a single helper
that owns the send loop and reuses one segment-send path and one keepalive
send path, reducing code size while keeping semantics unchanged.

## Non-Goals

- Change polling, keepalive, window/pacer gating, or transport permit behavior.
- Alter protocol framing, asymmetry rules, or reliability behavior.
- Run tests here.

## Affected Components

- sfb/tunnel/alice_tunnel.py

## Current Duplication (What We Will Remove)

- The code path for pending control/data sends and the code path for poll
  sends each repeat the sequence:
  - Reserve permit -> compute payload cap -> collect segments -> send or
    release permit.
- The keepalive send path is repeated in two places:
  - Window-full keepalive drop/replace path.
  - No-segments keepalive path after poll decision.

## Detailed Plan

1. Add `_send_pending_or_poll(...)` to `AliceTunnel`:
   - Inputs:
     - `now`, `serial_window`, `send_payload_limit`, and a helper to check
       pending mode (see Phase 3).
     - Any precomputed send events or flags needed to avoid extra lookups.
   - Responsibilities:
     - Own the existing `while True` loop that sends pending segments and
       performs poll keepalives.
     - Preserve the ordering: pending control/data sends first, poll sends
       only when no pending mode is active.
     - Return `(pacing_blocked, sent_any)` so `tick()` can keep its pacing and
       idle sleep decisions without recomputing state.

2. Add `_try_send_segments(...)` used by `_send_pending_or_poll(...)`:
   - Inputs:
     - `now`, `send_payload_limit`, `control_only`, `has_data_pending`, and
       optionally a pre-reserved permit.
   - Steps:
     - Reserve a permit via `_reserve_transport_permit(now, has_data_pending=...)`.
     - Determine payload cap with `self._transport.payload_cap_for_send(permit)`.
     - Collect segments with `_collect_segments(...)` using the same arguments
       as today.
     - If segments exist, send using `_send_new_packet(...)` and return True.
     - If no segments, release the permit and return False.
   - Keep `_has_pending_data_acks` updates where they occur today when a data
     packet is sent.

3. Add `_send_keepalive_or_break(...)` used by `_send_pending_or_poll(...)`:
   - Inputs:
     - `now`, `permit`, `consume_pong_grace`, `window_full`.
   - Steps:
     - If `window_full` is true, perform `drop_oldest_keepalive(...)` with the
       same logging and break behavior when nothing can be dropped.
     - Otherwise, send a keepalive using `_send_new_packet([], now, flags=FLAG_KEEPALIVE, permit=permit)`.
     - Decrement `_pong_grace_remaining` when `consume_pong_grace` is true,
       matching current behavior.
   - This keeps the keepalive code path identical while centralizing it.

4. Replace the inline loop in `tick()`:
   - Remove the large `while True` body from `tick()` and replace it with a
     call to `_send_pending_or_poll(...)`.
   - Pass in `serial_window`, `send_payload_limit`, and the pre-bound pending
     check helper so the hot path is not slowed by extra attribute lookups.

## How This Decreases the Amount of Code

- The repeated "reserve permit -> payload cap -> collect segments -> send or
  release" sequence currently appears twice. Moving it into `_try_send_segments`
  removes one full copy of that sequence from `tick()`.
- The keepalive send logic currently exists in two different branches. Moving
  it into `_send_keepalive_or_break` eliminates the duplicated keepalive send
  block, leaving only one implementation.
- The largest reduction comes from removing the large `while True` block from
  `tick()` and replacing it with a single helper call. The helper is shorter
  than the duplicated logic it replaces because it reuses `_try_send_segments`
  and `_send_keepalive_or_break`.

## Testing

- Do not run tests here. The user can run tests with python3 if needed.

## Execution Notes

- Added `_try_send_segments` and `_send_keepalive_or_break` helpers in
  `sfb/tunnel/alice_tunnel.py` to consolidate the segment send and keepalive
  paths without changing permit handling.
- Updated `_send_pending_or_poll` to use the helpers and return
  `(pacing_blocked, sent_any)` so `tick()` can decide on sleeps without
  rechecking packet counters.
- Updated `tick()` to consume the new tuple return and keep existing sleep
  behavior.
