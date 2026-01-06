# Adaptive Pacing Plan (Alice)

## Goal
Improve throughput by replacing fixed send pacing with an adaptive
algorithm that:
- Targets a configurable inflight ratio
- Clamps to the negotiated send window (max_in_flight cap)
- Preserves "poll immediately after real data" via existing poll decisions
- Keepalive polls are not delayed by pacing when due

This plan applies to Alice only. Bob remains opportunistic, per
`doc/ASYMMETRY.md`.

## Constraints
- Python 2.7/3 compatible; standard library only.
- Must support Linux and Windows (ICMP remains Linux-only).
- Preserve asymmetric MTU negotiation; use the configured MTU per transport.
- Keepalive-only responses are suppressed when any channel has pending data.
- Alice initiates; Bob responds to polls only.

## Current Behavior (Problem Statement)
- Alice bursts until the send window hits 64 in-flight, then stalls.
- Channel send buffers remain full, so the pump busy-loops under backpressure.
- Throughput starts high and decays to a low steady rate.

## Proposed Architecture

### New Component: AdaptivePacer (Alice)
Add a pacing helper (e.g., `sfb/tunnel/pacing.py`) that:
- Computes a target inflight count:
  - `cap = send_window._max_in_flight`
  - `target = clamp(int(cap * target_inflight_ratio), min_inflight, max_inflight or cap)`
- Enforces pacing by gating new sends when:
  - `send_window.unacked_count >= target`
- Treat existing `tunnel_send_rate` as a hard ceiling (pacer can only reduce).

### Reliability Updates (Support)
- `RttEstimator`: add read-only `srtt_ms` to expose the EWMA in milliseconds.
- `SendWindow.process_ack`: return acked packet count alongside RTT samples,
  counting all newly acked packets (including retransmits) so Phase 2 can
  compute ack-rate EWMA without re-walking internal state.

### Integration Points
- `AliceTunnel._can_send_new()`:
  - Add `pacer.can_send(unacked_count, cap)` check before send.
  - Keep existing transport and window checks.
  - Bypass pacing for keepalive-only polls once the keepalive interval is due.
- `AliceTunnel._handle_response()`:
  - Track if any response in the tick had real data.
- `AliceTunnel._send_new_packet()`:
- Retransmits do not update pacer state.

### Preserve "Poll Immediately After Real Data"
- If Bob sends real data, allow immediate polls to refill toward target inflight.
- Do not accelerate keepalive-only traffic; keep keepalive interval behavior.
- Keepalive polls are sent on schedule even if pacing would otherwise block.

## Configuration Additions (Alice)
Add config fields (names TBD, defaults conservative):
- `tunnel_adaptive_pacing_enabled` (bool, default False)
- `tunnel_pace_target_inflight_ratio` (float, default 0.7)
- `tunnel_pace_min_inflight` (int, default 1)
- `tunnel_pace_max_inflight` (int, default None => use cap, validated 1-64 when set)
- `tunnel_send_rate` remains a hard ceiling when adaptive pacing is enabled.

Expose via CLI overrides, similar to other tunnel knobs.

## Control Loop Details (Phase 1)
- Start with static target inflight ratio and RTT EWMA.
- Pacer permits new send when:
  - `unacked_count < target`
- Treat keepalive-only polls as exempt from pacing gates once the keepalive
  interval has elapsed; pacing still applies to real data sends.

## Control Loop Enhancements (Phase 2)
- Add ack-rate EWMA (packets per second).
- Source acked packet counts from `SendWindow.process_ack` (count any newly
  acked packet, including those acking retransmits; still only use first-TX
  acks for RTT samples).
- Estimate pipe size: `pipe = ack_rate * rtt_sec`.
- Set `target = clamp(pipe * inflight_gain, min_inflight, cap)`.
- Adjust gain based on observed `tunnel.send_blocked` ratio.

## Logging
Add a pacing-specific log event set (ex: `tunnel.pacer_state`) with:
- target_inflight, unacked_count, cap, rate_limit
Create a log profile that enables these events with minimal noise.

## Testing Plan
- Unit tests for `AdaptivePacer`:
  - target clamping with cap/min/max
  - hard ceiling interaction with `tunnel_send_rate`
- Alice tunnel tests:
  - pacer gating is honored (no send when at target)
  - RTT updates feed into pacer state

## Rollout Steps
1) Add config fields, defaults, and CLI plumbing (off by default).
2) Add `AdaptivePacer` and hook into Alice tunnel with logs.
3) Run ICMP SOCKS diag with pacing enabled (use configured MTU).
4) Compare throughput and send_blocked counts vs baseline.
5) Tune defaults based on logs and update documentation.

## Execution Notes
- Completed prior to this change per user report; no additional execution steps
  were run as part of this move.
