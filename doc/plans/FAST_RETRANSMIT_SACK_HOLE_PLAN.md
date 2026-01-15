# Fast Retransmit SACK Hole Plan

## Goal
Trigger fast retransmit earlier when a SACK hole is confirmed and the missing
packet is old enough, without requiring the send window to hit the distance
cap. Optionally gate on a small buffered threshold to avoid firing on trivial
holes.

## Non-goals
- Change transport behavior or polling cadence.
- Alter pacer math or window growth logic.
- Add compatibility shims for old configs.

## Affected Components
- sfb/reliability/fast_retransmit.py
- sfb/reliability/send_window.py
- sfb/tunnel/alice_tunnel.py
- sfb/config.py (only if adding a tunable threshold)
- sfb/cli.py (only if exposing a new tunable)

## Plan
1) Expose a minimal hole state helper on SendWindow that returns:
   - last_cum_ack, missing_in_unacked, missing_age
   - distance, unacked, buffered (distance - unacked)
   This should reuse existing distance_info + distance_details to avoid new
   state.
2) Update FastRetransmitController.select_candidate to:
   - require ack_silence < rto_sec and sack_progress_ready
   - require missing_in_unacked and missing_age >= min_age
   - drop the distance_exceeded requirement
   - optionally require buffered >= MIN_BUFFERED (small constant or tunable)
3) Keep existing max_per_seq and min_age_ratio behavior intact to limit
   bursty retransmits.
4) Ensure the reason remains "fast_retransmit" so existing logs continue to
   track the change without new log events.

## Validation
- In logs, verify fast retransmits occur before window_distance stalls.
- Confirm buffered peaks drop and the sawtooth amplitude reduces.
- Check retransmit_count per seq remains within max_per_seq bounds.

## Risks
- Earlier retransmit could increase duplicate traffic under reordering.
- A too-low buffered threshold may trigger on benign gaps; keep the threshold
  small and bounded by min_age_ratio and max_per_seq.
