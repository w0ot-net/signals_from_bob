# Bob Ack Stall Targeted Retransmit Plan

Status: completed

## Summary
Keep Bob's retransmit selection pinned to the initial send order by tracking a
`first_send_time` per packet and using it for "oldest" selection. This keeps a
missing seq (like 595) prioritized across retransmits without changing
cooldown/ack gating or Alice behavior.

## Goals
- Ensure the oldest unacked packet is selected by initial send time, so a
  missing seq remains highest priority even after retransmits.
- Preserve Bob's existing cooldown and ack-silence gating (age still based on
  the last send time).
- Add logging to explain initial-send ordering when a retransmit is chosen.

## Non-Goals
- Add explicit stall detection or targeted retransmit logic.
- Change Alice logic, poll cadence, or DNS transport behavior.
- Change retransmit cooldown or ack-silence gating semantics.
- Add or run automated tests.

## Affected Components
- `sfb/tunnel/bob_tunnel.py`
- `sfb/reliability/send_window.py`
- `doc/architecture/ASYMMETRY.md`

## Plan
1. Track initial send time in the send window.
   - Add `first_send_time` to `_UnackedPacket`, set once in `send()`.
   - Leave `send_time` updates unchanged in `mark_retransmit()`.
2. Add a Bob-only "oldest by initial send" selector.
   - Implement `get_oldest_unacked_first_info()` in `SendWindow` using a simple
     scan over unacked packets ordered by `first_send_time`.
   - Keep existing `get_oldest_unacked_info()` behavior unchanged.
3. Use initial-send ordering for Bob retransmits.
   - Swap `bob_tunnel._select_response_action()` to use the new selector.
   - Keep cooldown/ack-silence gates tied to `send_time` (age since last send).
4. Add targeted logging.
   - Include initial-send age or timestamp when a retransmit is selected to
     explain why a seq stays oldest across retries.
5. Update asymmetry documentation.
   - Note that Bob's opportunistic retransmit selection is ordered by initial
     send time, while cooldown uses time since last send.
6. Manual verification (log review only).
   - Confirm seq 595 remains the oldest selected across retransmits and that
     gating still prevents per-poll spam.

## Testing
- Do not run tests.

## Execution Notes
- Added first-send tracking in the send window and a Bob-only oldest selector
  based on initial send time.
- Switched Bob retransmit selection to initial-send ordering while keeping
  cooldown gating tied to last-send age, and logged first-send age on
  retransmits.
- Updated asymmetry and Bob retransmit documentation to reflect the new
  ordering and logging.
