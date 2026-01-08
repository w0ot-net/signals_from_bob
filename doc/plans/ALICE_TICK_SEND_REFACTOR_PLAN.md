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

## Plan

1. Extract a receive phase helper (ex: `_drain_transport_responses(now)`):
   - Encapsulate the non-blocking recv loop plus the pending-count threshold
     wait.
   - Return `(received_any, received_valid, last_resp_kind)`.
2. Extract a response-state updater (ex: `_update_response_state(...)`):
   - Clear `_has_pending_data_acks` when `data_unacked_count()` hits zero.
   - Preserve `_last_was_pong_only` and `_pong_grace_remaining` handling.
3. Extract a send phase helper (ex: `_send_pending_or_poll(...)`):
   - Consolidate the duplicated segment collection and keepalive paths.
   - Preserve the priority order: pending data/control sends before polls.
   - Keep the exact window-full keepalive drop logic.
   - Return `pacing_blocked` and `sent_any` so `tick()` can handle sleeps
     without recomputing state.
4. Replace the `pending_mode_set` closure with a small method or a local helper
   outside the loop so the main loop stays linear and readable.
5. Restructure `tick()` to be a high-level sequence of helper calls with the
   same state updates and exits as today.

## Testing

- Do not run tests here. The user can run tests with python3 if needed.
