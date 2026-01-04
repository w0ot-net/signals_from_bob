# Pacing Target Inflight Plan

## Goal
Make Alice's pacer continuously converge on a target_inflight that maximizes
throughput while minimizing retransmits and window/transport blocking.

## Background
Recent logs show:
- `feedback_target` is lower than `base_target`, but `target_inflight`
  remains capped by base, keeping inflight high.
- `window_distance` stalls occur with `unacked` near 1 while `distance`
  is maxed, which blocks new sends behind a single missing seq.
- `transport_headroom` blockages indicate pending saturation at the
  transport layer.

The pacer should actively respond to these signals and reduce inflight when
feedback indicates a smaller pipe.

## Constraints
- Python 2.7/3 compatible; standard library only.
- Keep asymmetry rules: Alice initiates, Bob responds.
- Avoid spammy logging; summarize at low cadence.

## Plan
1) **Baseline with pacing summaries**
   - Use `tunnel.pacer_summary` at 1s cadence (log profile or config) to capture:
     send/recv rates, pending depth, inflight, window-distance stalls,
     retransmit deltas.
   - Define target metrics: high send_rate, low retransmit deltas,
     low window_distance stalls, low transport_headroom blocks.

2) **Rework pacing target selection**
   - Restore intended behavior: feedback can move target_inflight up or down;
     use feedback as a cap when lower and as a floor when higher.
   - Introduce a stable/unstable gate (EWMA stability or min sample count)
     before applying feedback-based reductions to avoid oscillation.
   - Keep `min_inflight` guardrails and allow slow upward probe when loss
     remains low.
   - No new config flags; behavior stays under adaptive pacing.

3) **Integrate stall signals into pacing**
   - Add a `pacer.on_blocked(reason, now)` hook in Alice:
     - `window_distance` stall with small `unacked` reduces target quickly.
     - `transport_headroom` or `window_full` reduces target modestly.
   - Reset probe state after stalls, similar to retransmit resets.

4) **Add minimal, non-spammy instrumentation**
   - Extend `tunnel.pacer_summary` to include:
     `blocked_window_distance`, `blocked_transport_headroom`,
     `blocked_window_full`.
   - Add a low-frequency `tunnel.pacer_adjust` event only when target is
     decreased by feedback or stall signals.
   - Include `tunnel.pacer_adjust` in pacing-focused log profiles.

5) **Tests**
   - Update `tests/test_pacing.py`:
     - Feedback lower than base reduces target (after stability gate).
     - Feedback-lower cases set `target_mode` to feedback.
     - Stall signals reduce target and reset probe.
     - Target increases slowly on stable ack rates with low retransmits.

6) **Validation runs**
   - Run ICMP SOCKS diag with and without adaptive pacing (or compare to
     a previous build).
   - Compare throughput, `tunnel.send_blocked` reasons, and retransmit deltas.

## Affected Components
- `sfb/tunnel/pacing.py`
- `sfb/tunnel/alice_tunnel.py`
- `sfb/log_profiles.py`
- `tests/test_pacing.py`
