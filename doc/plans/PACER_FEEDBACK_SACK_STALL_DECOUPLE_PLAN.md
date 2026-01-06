# Pacer Feedback Decouple During SACK Stalls Plan

Status: draft

## Goal
Prevent pacer feedback from collapsing `target_inflight` while Alice is
blocked by `send_window_distance` (SACK hole stalls), so recovery can proceed
without shrinking the inflight target.

## Non-Goals
- Change retransmit policy or fast retransmit thresholds.
- Modify poll pacing or transport rate limits.
- Alter SACK bitmap size or window negotiation behavior.

## Affected Components
- `sfb/reliability/pacing.py` (AdaptivePacer feedback handling)
- `sfb/tunnel/alice_tunnel.py` (send-window distance detection, pacer hooks)
- `sfb/reliability/send_window.py` (distance details used for stall detection)
- `doc/bugs/slow_icmp_socks_throughput.md` (log review updates)
- `tests/test_pacing.py` (unit tests for new pacer behavior)

## Plan
1) Define a stall-aware gating signal for pacing.
   - In Alice, when `send_window_distance` is exceeded, classify the stall
     as a SACK-hole condition (e.g., `missing_in_unacked` true, `buffered`
     high, `unacked` low).
   - Use existing `distance_details()` fields to avoid new protocol changes.
2) Freeze feedback updates while the SACK-hole stall is active.
   - Add a pacer method to pause feedback updates for a short interval or
     until the stall clears.
   - Preserve the current `ack_rate_ewma` and `ack_samples` while frozen,
     so `target_inflight` does not drop.
3) Resume feedback smoothly after the stall clears.
   - On the first non-stalled ACK progress, unfreeze feedback and allow
     EWMA updates again.
   - Avoid a sudden jump by keeping the last EWMA and probe state.
4) Add targeted logs and unit tests.
   - Log a pacer state or adjust event when feedback is frozen/resumed.
   - Extend `tests/test_pacing.py` to cover the freeze/unfreeze behavior.
5) Validate with new logs (user-run).
   - Compare `target_inflight` during stalls before/after; confirm it no
     longer drops to low teens while `send_window_distance` is active.

## Risks / Considerations
- Freezing feedback too long could overrun weak links; keep the freeze
  tied strictly to the stall condition.
- If the stall is not SACK-hole related, this may mask a real throughput
  limit; keep the detection narrow and log when engaged.

## Validation
- Unit tests for pacer freeze/unfreeze behavior.
- User-run ICMP+SOCKS logs show fewer `pacer` blocks during SACK stalls and
  reduced `target_inflight` collapse while maintaining stability.
