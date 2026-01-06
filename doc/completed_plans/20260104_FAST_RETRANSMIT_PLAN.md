# Fast Retransmit Plan

## Summary
Add a fast retransmit path on Alice that targets the missing cumulative ACK
hole when SACK progress is observed and the window is stalled, without waiting
for full RTO silence.

## Goals
- Reduce throughput valleys caused by a single missing packet blocking the
  cumulative ACK while later packets are SACKed.
- Keep behavior consistent with asymmetry rules: Alice drives retransmit
  decisions; Bob remains opportunistic.
- Avoid aggressive retransmit loops by applying per-seq limits and pruning.
- Allow keepalive retransmits to preserve Alice polling when a keepalive is
  the missing sequence.

## Non-Goals
- Changing Bob retransmit logic or transport semantics.
- Altering MTU negotiation or SACK bitmap size.
- Replacing adaptive pacing or poll pacing.

## Affected Components
- sfb/tunnel/alice_tunnel.py (fast retransmit trigger and logging)
- sfb/tunnel/base_tunnel.py (track last seen SACK and SACK progress)
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
2. Track last seen SACK and SACK progress in `BaseTunnel._process_incoming_packet`
   so Alice can confirm SACK activity while the cumulative ACK is unchanged.
3. After the RTO scan in `AliceTunnel.tick`, detect a SACK hole when the window
   distance is exceeded using `last_cum_ack` from `_send_window_distance_info()`
   and `SendWindow.get_unacked_info(last_cum_ack)`.
4. If the missing seq is still unacked and its age exceeds
   `rto_sec * min_age_ratio`, attempt a fast retransmit:
   - Require SACK progress observed with the cumulative ACK unchanged.
   - Respect `_can_send_retransmit()` and retransmit budget.
   - Track per-seq fast retransmits to avoid repeated sends
     (`max_per_seq` cap, prune counts when seq leaves the window).
   - Allow keepalive retransmits and use
     `_send_retransmit(..., reason='fast_retransmit')` so logging stays
     consistent with existing retransmit events.
5. Update logging docs to mention `reason=fast_retransmit` on
   `tunnel.retransmit`.
6. Add unit tests covering:
   - Fast retransmit fires when ack_silence < RTO but a SACK hole is old.
   - Fast retransmit does not fire when missing_age < threshold or when
     max_per_seq is reached.

## Validation
- Run unit tests that exercise Alice retransmit logic
  (do not run E2E tests; user will run them).
- Inspect `tunnel.retransmit` logs for `reason=fast_retransmit` and confirm
  cumulative ACK advances sooner during stalled windows.

## Execution Notes
- Implemented SACK progress tracking in `BaseTunnel._process_incoming_packet`.
- Added fast retransmit gating and per-seq cap/pruning in `AliceTunnel.tick`.
- Allowed keepalive fast retransmits and wired config/CLI flags.
- Updated logging docs and added unit tests for fast retransmit behavior.
- Tests not run (per instructions).
