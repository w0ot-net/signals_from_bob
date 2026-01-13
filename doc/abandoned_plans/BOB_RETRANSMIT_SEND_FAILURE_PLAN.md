# Bob Retransmit Send Failure Plan

Status: abandoned

## Summary
Ensure Bob only updates retransmit counters and cooldown timestamps after a
successful retransmit response send. Avoid skew when a responder send fails
(oversize or transport error), matching Alice's retransmit accounting.

## Goals
- Move retransmit accounting (mark_retransmit, reliability stats) to after a
  successful responder send.
- Ensure `tunnel.retransmit` and reliability snapshots only log on successful
  retransmit sends.
- Preserve existing retransmit selection, cooldown, and window override
  behavior.

## Non-Goals
- Change retransmit candidate selection, cooldown derivation, or window
  enforcement.
- Alter responder error handling semantics beyond avoiding retransmit
  accounting on failed sends.
- Add or run automated tests.

## Affected Components
- `sfb/tunnel/bob_tunnel.py`

## Plan
1. Reorder retransmit bookkeeping in `_send_retransmit_response`.
   - Move `send_window.mark_retransmit` and the `tunnel.retransmit`/reliability
     logs to after `_send_response_packet` completes successfully.
   - Compute previous send age/retransmit counts before the send to preserve
     existing log fields.
2. Keep responder failure behavior consistent.
   - Allow `_respond` exceptions to propagate as today.
   - Ensure no retransmit counters or cooldown timestamps are mutated on a
     failed send.

## Testing
- Do not run tests.

## Abandon Notes
- Abandoned per request.
