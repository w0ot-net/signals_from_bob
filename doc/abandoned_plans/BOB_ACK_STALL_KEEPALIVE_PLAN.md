# Bob Ack Stall Retransmit + Keepalive Suppression Plan

Status: abandoned

## Summary
Reduce tunnel wedges when Alice keeps polling but Bob's cumulative ACK stops
advancing by forcing data retransmits on ACK stalls and tightening keepalive
suppression for pending-data edge cases.

## Goals
- Force a data retransmit when Bob sees a wall-clock ACK stall even if the
  normal cooldown/ack_progress gates would skip it.
- Preserve the existing segments-first behavior; only change keepalive
  selection when channels report pending data but no segments fit.
- Add targeted logging to explain why a retransmit or pong suppression happened.

## Non-Goals
- Change MTU clamping policy or resolver selection.
- Modify Alice's poll cadence or DNS transport behavior.
- Add or run automated tests.

## Affected Components
- `sfb/tunnel/bob_tunnel.py`
- `sfb/reliability/send_window.py`
- `sfb/reliability/stats.py`
- `sfb/config.py`
- `doc/architecture/ASYMMETRY.md`

## Plan
1. Add ACK-stall detection in the Bob send window.
   - Track wall-clock time since `last_cum_ack` advanced.
   - Add a config threshold (seconds) for forcing a retransmit on stall.
2. Force retransmit of the oldest unacked data segment on stall.
   - Override `retransmit_skip` when the stall threshold is exceeded.
   - Log a dedicated event with the stalled ACK, age, and segment info.
3. Tighten keepalive suppression for pending-data edge cases.
   - Use `_collect_segments(..., return_pending=True)` to get both segments and
     `pending_data`.
   - Keep the current behavior when `segments` is non-empty.
   - If `pending_data` is True but `segments` is empty, suppress keepalive and
     log the condition (this should be rare under normal payload caps).
4. Update docs for the asymmetry rules.
   - Document the Bob wall-clock stall retransmit behavior.
   - Note the keepalive suppression rule when channels have pending data.
5. Manual verification.
   - Confirm logs show forced retransmit when ACK stalls.
   - Confirm keepalive-only responses stop during pending data.

## Testing
- Do not run tests.

## Abandon Notes
- Abandoned after review; the stall symptoms point to response-cap blocked
  retransmits rather than keepalive suppression or ACK-stall overrides.
