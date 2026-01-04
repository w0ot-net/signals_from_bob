# Fast Retransmit Plan

## Summary
Add a fast retransmit path on Alice that targets the missing cumulative ACK
hole when SACK is advancing but the window is stalled, without waiting for
full RTO silence.

## Goals
- Reduce throughput valleys caused by a single missing packet blocking the
  cumulative ACK while later packets are SACKed.
- Keep behavior consistent with asymmetry rules: Alice drives retransmit
  decisions; Bob remains opportunistic.
- Avoid aggressive retransmit loops by applying per-seq cooldown and limits.

## Non-Goals
- Changing Bob retransmit logic or transport semantics.
- Altering MTU negotiation or SACK bitmap size.
- Replacing adaptive pacing or poll pacing.

## Affected Components
- sfb/tunnel/alice_tunnel.py (fast retransmit trigger and logging)
- sfb/reliability/send_window.py (use get_unacked_info for missing seq details)
- sfb/config.py (fast retransmit tuning knobs)
- sfb/cli.py (optional flags for new knobs)
- doc/LOGGING.md (document fast retransmit reason/event)
- tests/test_alice_tunnel.py (unit coverage for fast retransmit trigger)

## Plan
1. Add config knobs for fast retransmit:
   - `tunnel_fast_retransmit_enabled` (bool, default True)
   - `tunnel_fast_retransmit_min_age_ratio` (float, default 0.5 of RTO)
   - `tunnel_fast_retransmit_max_per_seq` (int, default 1)
2. In `AliceTunnel._can_send_new` window-distance block path, detect a SACK
   hole using `last_cum_ack` from `_send_window_distance_info()` and
   `SendWindow.get_unacked_info(last_cum_ack)`.
3. If the missing seq is still unacked and its age exceeds
   `rto_sec * min_age_ratio`, attempt a fast retransmit:
   - Respect `_can_send_retransmit()` and retransmit budget.
   - Track per-seq fast retransmits to avoid repeated sends
     (`max_per_seq` cap, optionally a time-based cooldown).
   - Use `_send_retransmit(..., reason='fast_retransmit')` so logging stays
     consistent with existing retransmit events.
4. Update logging docs to mention `reason=fast_retransmit` on
   `tunnel.retransmit`.
5. Add unit tests covering:
   - Fast retransmit fires when ack_silence < RTO but a SACK hole is old.
   - Fast retransmit does not fire when missing_age < threshold or when
     max_per_seq is reached.

## Validation
- Run unit tests that exercise Alice retransmit logic
  (do not run E2E tests; user will run them).
- Inspect `tunnel.retransmit` logs for `reason=fast_retransmit` and confirm
  cumulative ACK advances sooner during stalled windows.
