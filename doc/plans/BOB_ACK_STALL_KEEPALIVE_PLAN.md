# Bob Ack Stall Retransmit + Keepalive Suppression Plan

Status: draft

## Summary
Reduce tunnel wedges when Alice keeps polling but Bob's cumulative ACK stops
advancing by forcing data retransmits on ACK stalls and suppressing keepalive
pongs when any channel has pending data.

## Goals
- Force a data retransmit when Bob sees a wall-clock ACK stall even if the
  normal cooldown/ack_progress gates would skip it.
- Prefer sending data/control over keepalive-only responses whenever any
  channel has pending data.
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
- `sfb/channel/channel_manager.py`
- `doc/architecture/ASYMMETRY.md`

## Plan
1. Add ACK-stall detection in the Bob send window.
   - Track wall-clock time since `last_cum_ack` advanced.
   - Add a config threshold (seconds) for forcing a retransmit on stall.
2. Force retransmit of the oldest unacked data segment on stall.
   - Override `retransmit_skip` when the stall threshold is exceeded.
   - Log a dedicated event with the stalled ACK, age, and segment info.
3. Suppress keepalive pongs when any channel has pending data.
   - In Bob's response selection, treat pending data/control as higher priority
     than keepalive-only responses.
   - Ensure a keepalive response is only sent when all channels are idle and
     there are no retransmit candidates.
4. Update docs for the asymmetry rules.
   - Document the Bob wall-clock stall retransmit behavior.
   - Note the keepalive suppression rule when channels have pending data.
5. Manual verification.
   - Confirm logs show forced retransmit when ACK stalls.
   - Confirm keepalive-only responses stop during pending data.

## Testing
- Do not run tests.
