# Adaptive Pacing Feedback Plan (Alice)

## Goal
Make Alice's pacing target responsive to delivery throughput and RTT so the
target inflight count adapts to the observed pipe instead of staying fixed.

## Constraints
- Python 2.7/3 compatible; standard library only.
- Must support Linux and Windows (ICMP remains Linux-only).
- Preserve asymmetric MTU negotiation; use configured MTU per transport.
- Keepalive pongs suppressed when any channel has pending data.
- Asymmetry rules apply: Alice initiates, Bob responds to polls only.
- Alice uses RTT-based retransmit; Bob retransmits opportunistically.
- No time-based pacing gate.

## Current Behavior
- Adaptive pacing uses a static inflight target derived from ratio/min/max.
- There is no feedback loop from ACK throughput or RTT.

## Proposed Architecture

### New Feedback Signals (Alice only)
- `ack_rate_ewma`: smoothed packets/sec based on ACK progress (bounded by Alice polling cadence).
- `srtt_ms`: from `RttEstimator` (existing).

### Target Computation
1) Compute cap: `cap = send_window._max_in_flight`.
2) Compute base target: `base = clamp(int(cap * ratio), min_inflight, max_inflight or cap)`.
3) If feedback is available (`ack_rate_ewma` set and `srtt_ms` not None):
   - `rtt_sec = max(srtt_ms, rtt_floor_ms) / 1000.0`
   - `pipe = ack_rate_ewma * rtt_sec`
   - `feedback = clamp(int(pipe * feedback_gain), min_inflight, max_inflight or cap)`
   - Use `feedback` as the target.
4) If feedback is not available, use `base`.

### Feedback Update
- Only update when `data_acked_count > 0` (ignore keepalive-only responses).
- On ack progress, call `pacer.on_ack(data_acked_count, now)` to update
  `ack_rate_ewma`.
- Use `data_acked_count` as newly acked packets that carried data or non-keepalive
  control, not total ACKs.
- Compute instantaneous rate as `data_acked_count / dt`, where
  `dt = now - last_ack_time`.
- If `last_ack_time` is None, set it to `now` and return without update.
- If `ack_rate_ewma` is None, seed it to `rate` and set `last_ack_time = now`.
- Use EWMA: `ack_rate_ewma = (1 - alpha) * ack_rate_ewma + alpha * rate`.
- If `dt <= 0`, set `last_ack_time = now` and skip update.
- If `dt > ack_idle_reset_sec`, reset `ack_rate_ewma` and `last_ack_time` to None
  (fallback to base).

### Pacing Gate
- `pacer.can_send(unacked_count, cap, srtt_ms)` blocks only when
  `unacked_count >= target`.
- Keep keepalive-only polls exempt once due (existing behavior).

## Configuration Additions
- `tunnel_pace_feedback_gain` (float, default 1.25, > 0)
- `tunnel_pace_ack_ewma_alpha` (float, default 0.2, 0 < alpha <= 1)
- `tunnel_pace_rtt_floor_ms` (float, default 5.0, > 0)
- `tunnel_pace_ack_idle_reset_sec` (float, default 2.0, > 0)

Expose via CLI under client pacing args. Validate in Config.

## Integration Points
- `AdaptivePacer`:
  - add state: `ack_rate_ewma`, `last_ack_time`, config knobs.
  - `on_ack(acked_count, now)` updates EWMA.
  - `target_inflight(cap, srtt_ms=None)` uses feedback when available.
  - `can_send(unacked_count, cap, srtt_ms=None)` uses computed target.
- `AliceTunnel._handle_response()`:
- after `_process_incoming_packet`, call `pacer.on_ack(data_acked_count, now)` when
  `data_acked_count > 0`.
- `AliceTunnel._can_send_new()`:
  - pass `srtt_ms=self._rtt.srtt_ms` into `pacer.can_send(...)`.
- Logging (`tunnel.pacer_state`):
  - add `srtt_ms`, `ack_rate_ewma`, `base_target`, `feedback_target`, `target_mode`.

## Testing Plan
- Unit tests for `AdaptivePacer`:
  - EWMA update behavior (initial, steady updates, alpha bounds).
  - Idle reset behavior (dt > ack_idle_reset_sec).
  - Target uses feedback when available and falls back to base otherwise.
  - Target clamps against min/max/cap.
  - No feedback until `srtt_ms` is available.
  - Keepalive-only acks do not update EWMA.
  - `data_acked_count == 0` does not update EWMA.
- Alice tunnel tests:
  - pacer gating honors feedback target.
  - ack updates use `acked_count` from `process_ack`.

## Rollout Steps
1) Add config fields + CLI wiring + validation.
2) Implement `AdaptivePacer` feedback state and target selection.
3) Wire into Alice tunnel and add logging fields.
4) Update unit tests and any tuning docs.
5) Run unit tests (exclude E2E tests per project rules).

## Risks and Mitigations
- Risk: feedback target shrinks too much on transient stalls.
  - Mitigation: min_inflight clamp and idle reset fallback.
- Risk: overshoot with bursty ACKs.
  - Mitigation: EWMA smoothing and gain defaults.

## Execution Notes
- Implemented ACK-rate feedback in `sfb/tunnel/pacing.py` with EWMA and RTT-based target selection.
- Wired data-only ACK accounting through `sfb/reliability/send_window.py` and
  `sfb/tunnel/base_tunnel.py`, feeding `pacer.on_ack` from Alice.
- Added config and CLI knobs for feedback tuning; logging now includes srtt and
  feedback targets.
- Updated unit tests for pacing and ACK accounting; refreshed ICMP transport doc.
- Tests: `python3 -m unittest tests.test_pacing tests.test_reliability tests.test_reliability_sim tests.test_tunnel`
