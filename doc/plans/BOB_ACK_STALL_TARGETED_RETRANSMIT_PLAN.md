# Bob Ack Stall Targeted Retransmit Plan

Status: draft

## Summary
When Bob's cumulative ACK stalls and Alice continues polling with SACK progress,
force a targeted retransmit of the missing seq (the stalled cumulative ACK)
instead of always retransmitting the oldest-by-send-time packet. This is meant
to clear single-packet holes like the DNS SOCKS stall at seq 595 without
changing normal opportunistic retransmit behavior.

## Goals
- Retransmit the missing seq when ACK is stalled and SACK indicates progress
  beyond the gap.
- Keep the default "oldest-by-send-time" opportunistic retransmit for all
  non-stall cases.
- Add logging to explain stall detection and retransmit selection.

## Non-Goals
- Retransmit "oldest by sequence number" in all cases.
- Change Alice logic, poll cadence, or DNS transport behavior.
- Add or run automated tests.

## Affected Components
- `sfb/tunnel/bob_tunnel.py`
- `sfb/reliability/send_window.py`
- `sfb/config.py`
- `doc/architecture/ASYMMETRY.md`

## Plan
1. Add a stall-aware retransmit decision for Bob.
   - Detect when cumulative ACK has not advanced for a configurable duration.
   - Require SACK progress or ack-miss activity to confirm a hole exists.
   - Pick `missing_seq = last_cum_ack` and retransmit it if still unacked.
2. Rate-limit stall retransmits per sequence number.
   - Add a Bob-side limit (max per seq or minimum interval per seq) so the
     forced retransmit does not spam during long stalls.
   - Fall back to existing retransmit selection if the missing seq is not
     in the send window.
3. Add targeted logging.
   - Log a distinct event when a stall retransmit fires, including stall age,
     missing seq, send age, and retransmit count.
   - Log a skip reason when a stall is detected but cannot be retried.
4. Update asymmetry documentation.
   - Document that Bob uses wall-clock stall detection to retransmit the
     missing seq when Alice keeps polling and SACK progresses.
5. Manual verification (log review only).
   - Confirm the missing seq is retransmitted during ACK stalls and that
     normal opportunistic retransmits remain unchanged.

## Testing
- Do not run tests.
