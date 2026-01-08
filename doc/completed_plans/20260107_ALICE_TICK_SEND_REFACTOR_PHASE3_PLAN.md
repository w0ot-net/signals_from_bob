# Alice Tick Send Refactor Phase 3 Plan

Status: completed

## Goal

Make `tick()` a linear, high-level sequence of helper calls and remove small
inline helpers and local state that are no longer needed after Phases 1 and 2,
shrinking the method and improving scanability.

## Non-Goals

- Change polling, keepalive, window/pacer gating, or transport permit behavior.
- Alter protocol framing, asymmetry rules, or reliability behavior.
- Run tests here.

## Affected Components

- sfb/tunnel/alice_tunnel.py

## Current Duplication/Noise (What We Will Remove)

- The `pending_mode_set` closure inside `tick()` adds a nested helper with
  multiple branches and keeps event lookups mixed into the main flow.
- The `tick()` body currently interleaves recv, send, and sleep logic with
  local helper definitions and long inline loops, making the method larger
  than necessary.

## Detailed Plan

1. Replace the inline `pending_mode_set` closure:
   - Create a small method such as `_pending_mode_set(mode, pending_event, control_event, data_event)`.
   - Alternatively, create a single pre-bound local function at the top of
     `tick()` that captures the three events once and reuses them.
   - Keep the exact mapping of `mode` values:
     - `control` -> `control_send_event.is_set()`
     - `data` -> `data_send_event.is_set()`
     - default -> `pending_event.is_set()`

2. Restructure `tick()` into a linear sequence:
   - `recv` phase: call `_drain_transport_responses(now)` and
     `_update_response_state(...)` from Phase 1.
   - timeout check: keep the no-response timeout block in the same place.
   - retransmit scan: keep the RTO/fast-retransmit flow unchanged.
   - send/poll phase: call `_send_pending_or_poll(...)` from Phase 2.
   - window growth: keep `_maybe_request_window(now)` in the same position.
   - pacing/idle sleep: preserve all existing conditions, but use the helper
     return values from Phase 2 to avoid recomputing send outcomes.

3. Use helper return values to reduce inline logic:
   - Use the `(pacing_blocked, sent_any)` tuple from `_send_pending_or_poll(...)`
     to decide whether to call `_sleep_for_poll_pacing(...)`.
   - Use the same tuple plus `received_any` to drive the idle sleep decision
     without duplicating checks.

## How This Decreases the Amount of Code

- Removing the inline closure eliminates a small block of conditional logic
  from `tick()` and avoids repeating event checks in-line.
- After Phase 2, the large send loop is gone; Phase 3 then removes the remaining
  inline helper code that surrounds it, leaving `tick()` as a short list of
  helper calls.
- The sleep decision logic uses helper return values instead of re-deriving
  state, which removes repeated conditional checks and shortens the tail end
  of `tick()`.

## Testing

- Do not run tests here. The user can run tests with python3 if needed.

## Execution Notes

- Added `_pending_mode_set` to remove the inline `pending_mode_set` closure and
  reused it inside `_send_pending_or_poll` in `sfb/tunnel/alice_tunnel.py`.
- Kept `tick()` as a linear sequence of helper calls while using the existing
  `(pacing_blocked, sent_any)` return values for sleep decisions.
