# Preemptive Fast Retransmit Plan

## Context
Recent client logs show most window-distance stalls happen right after a
fast retransmit of the cumulative ACK hole. That suggests the hole is being
repaired only after we are already at the distance cap, forcing a stall
while we wait for the retransmitted seq to be ACKed.

## Goals
- Repair SACK holes earlier so we avoid reaching the window-distance cap.
- Preserve existing asymmetry rules and Alice-only timer-driven behavior.
- Keep retransmit budgeting and rate limits intact.
- Maintain Python 2.7/3 compatibility and standard-library-only code.

## Non-Goals
- Changing Bob behavior or poll cadence.
- Altering RTO/backoff logic or the ACK/SACK format.
- Disabling the existing fast-retransmit guardrails.

## Affected Components
- `sfb/tunnel/alice_tunnel.py` (fast retransmit decision logic)
- `sfb/config.py` (new preemptive threshold knob)
- `sfb/cli.py` (expose new knob)
- `doc/ALICE_RETRANSMIT_LOGIC.md` (document updated fast retransmit behavior)
- `doc/RELIABILITY.md` or `doc/TUNNEL.md` (if they describe the gating rules)
- `tests/` (unit coverage for the preemptive trigger)

## Proposed Changes
1. Add a preemptive threshold config:
   - `tunnel_fast_retransmit_preemptive_ratio` (float, default 0.75).
   - Valid range: `0 < ratio <= 1`. `ratio == 1.0` preserves current behavior.
2. Extend `_maybe_fast_retransmit()` to allow an early trigger:
   - Require SACK progress readiness and `ack_silence < rto_sec` as today.
   - Compute `distance` and `distance_limit` from `distance_info`.
   - If `distance_exceeded` is true, keep current behavior.
   - If not exceeded, allow preemptive send when:
     - `distance >= ratio * distance_limit`
     - `missing_seq` is still unacked
     - `missing_age >= rto_sec * tunnel_fast_retransmit_min_age_ratio`
3. Logging updates:
   - Keep `tunnel.retransmit` event, but set `reason` to
     `fast_retransmit_preemptive` on the new path.
   - Add fields to the `tunnel.send_window_distance` debug details as needed
     to correlate preemptive decisions (ratio, distance, limit).
4. Documentation updates:
   - Note the preemptive trigger and the new config knob.

## Detailed Steps
1. Add the new config field and CLI flag, with validation and help text.
2. Update Alice fast retransmit logic to compute the preemptive gate using
   the existing distance calculation.
3. Ensure the preemptive path respects:
   - Retransmit budget and rate limiter.
   - Per-seq fast retransmit cap.
   - ACK silence gating.
4. Update logging and docs to reflect the new decision path.

## Test Plan
- Unit test: preemptive trigger fires when distance is near the cap and
  SACK progress is ready.
- Unit test: preemptive trigger does not fire when ratio is 1.0 or when
  missing_age is below the min-age threshold.
- Unit test: retransmit count cap prevents repeated preemptive sends.

## Risks and Mitigations
- Risk: extra retransmits on transient reorder or latency.
  Mitigation: keep min-age gate, per-seq cap, and rate limiter.
- Risk: interaction with pacing adjustments after blocked sends.
  Mitigation: leave pacing logic unchanged; only adjust retransmit timing.

## Success Criteria
- Reduction in `tunnel.send_window_distance` events following
  `tunnel.retransmit` in client logs.
- Fewer window-distance stalls without increased timeouts or error rates.

## Abandonment notes
- 2025-09-19: Abandoned per request; no implementation work recorded.
