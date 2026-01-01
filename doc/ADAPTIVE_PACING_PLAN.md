# Adaptive Pacing Plan (Alice)

## Goal
Improve ICMP/SOCKS throughput by replacing fixed send pacing with an adaptive
algorithm that:
- Uses RTT EWMA to pace Alice's sends
- Targets a configurable inflight ratio
- Clamps to min(transport.max_pending, tunnel_max_in_flight)
- Preserves "poll immediately after real data" behavior

This plan applies to Alice only. Bob remains opportunistic, per
`doc/ASYMMETRY.md`.

## Constraints
- Python 2.7/3 compatible; standard library only.
- Must support Linux and Windows (ICMP remains Linux-only).
- Preserve asymmetric MTU negotiation and cap MTU <= 1400.
- Keepalive pongs are suppressed when any channel has pending data.
- Alice initiates; Bob responds to polls only.

## Current Behavior (Problem Statement)
- Alice bursts until the send window hits 64 in-flight, then stalls.
- Channel send buffers remain full, so the pump busy-loops under backpressure.
- Throughput starts high and decays to a low steady rate.

## Proposed Architecture

### New Component: AdaptivePacer (Alice)
Add a pacing helper (e.g., `sfb/tunnel/pacing.py`) that:
- Tracks RTT EWMA (use existing `RttEstimator`, add a read-only `srtt_ms`).
- Computes a target inflight count:
  - `cap = min(transport.max_pending, config.tunnel_max_in_flight)`
  - `target = clamp(cap * target_inflight_ratio, min_inflight, cap)`
- Enforces pacing by gating new sends when:
  - `send_window.unacked_count >= target`
  - or time since last send is below `rtt / target` (optional fine-grain)

### Integration Points
- `AliceTunnel._can_send_new()`:
  - Add `pacer.can_send(now, unacked_count)` check before send.
  - Keep existing transport and window checks.
- `AliceTunnel._handle_response()`:
  - Feed RTT samples into pacer for EWMA and pacing updates.
  - Detect ack progress to allow "fast refill" after real data.
- `AliceTunnel._send_new_packet()`:
  - Inform pacer on actual sends (for pacing interval calculations).

### Preserve "Poll Immediately After Real Data"
- If Bob sends real data, allow a short burst to refill toward target inflight.
- After the burst, revert to pacing based on RTT EWMA.
- Keep existing keepalive logic and pong suppression intact.

## Configuration Additions (Alice)
Add config fields (names TBD, defaults conservative):
- `tunnel_adaptive_pacing_enabled` (bool, default False)
- `tunnel_pace_target_inflight_ratio` (float, default 0.7)
- `tunnel_pace_min_inflight` (int, default 1)
- `tunnel_pace_max_inflight` (int, default None => use cap)
- `tunnel_pace_fast_start` (bool, default True)
- `tunnel_pace_rtt_floor_ms` (float, default 5.0) to avoid divide by zero

Expose via CLI overrides, similar to other tunnel knobs.

## Control Loop Details (Phase 1)
- Start with static target inflight ratio and RTT EWMA.
- Pacer permits new send when:
  - `unacked_count < target`
  - and `now - last_send >= rtt / target` (optional if we want spacing)
- Update RTT EWMA from `RttEstimator` samples (first TX only).

## Control Loop Enhancements (Phase 2)
- Add ack-rate EWMA (bytes or packets per second).
- Estimate pipe size: `pipe = ack_rate * rtt`.
- Set `target = clamp(pipe * inflight_gain, min_inflight, cap)`.
- Adjust gain based on observed `tunnel.send_blocked` ratio.

## Logging
Add a pacing-specific log event set (ex: `tunnel.pacer_state`) with:
- srtt_ms, target_inflight, unacked_count, cap, rate_limit
- fast_start active flag
Create a log profile that enables these events with minimal noise.

## Testing Plan
- Unit tests for `AdaptivePacer`:
  - target clamping with cap/min/max
  - pacing interval behavior at different RTTs
  - fast-start burst then steady-state
- Alice tunnel tests:
  - pacer gating is honored (no send when at target)
  - RTT updates feed into pacer state

## Rollout Steps
1) Add config fields, defaults, and CLI plumbing (off by default).
2) Add `AdaptivePacer` and hook into Alice tunnel with logs.
3) Run ICMP SOCKS diag with pacing enabled (MTU <= 1400).
4) Compare throughput and send_blocked counts vs baseline.
5) Tune defaults based on logs and update documentation.
