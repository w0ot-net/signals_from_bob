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
1) **Baseline with new pacing summaries**
   - Use `tunnel.pacer_summary` at 1s cadence to capture:
     send/recv rates, pending depth, inflight, window-distance stalls,
     retransmit deltas.
   - Define target metrics: high send_rate, low retransmit deltas,
     low window_distance stalls, low transport_headroom blocks.

2) **Rework pacing target selection**
   - Adjust `AdaptivePacer.target_inflight()` to *cap* by feedback when it
     is lower than base, not only when higher.
   - Introduce a stable/unstable gate (EWMA stability or min sample count)
     before applying feedback-based reductions to avoid oscillation.
   - Keep `min_inflight` guardrails and allow slow upward probe when loss
     remains low.

3) **Integrate stall signals into pacing**
   - Add a `pacer.on_blocked(reason, now)` hook in Alice:
     - `window_distance` stall with small `unacked` reduces target quickly.
     - `transport_headroom` or `send_window_full` reduces target modestly.
   - Reset probe state after stalls, similar to retransmit resets.

4) **Add minimal, non-spammy instrumentation**
   - Extend `tunnel.pacer_summary` to include:
     `blocked_window_distance`, `blocked_transport_headroom`,
     `gap_retransmit_delta`.
   - Add a low-frequency `tunnel.pacer_adjust` event only when target is
     decreased by feedback or stall signals.

5) **Tests**
   - Update `tests/test_pacing.py`:
     - Feedback lower than base reduces target (after stability gate).
     - Stall signals reduce target and reset probe.
     - Target increases slowly on stable ack rates with low retransmits.

6) **Validation runs**
   - Run ICMP SOCKS diag with and without the new pacer logic.
   - Compare throughput, `tunnel.send_blocked` reasons, and retransmit deltas.

## Affected Components
- `sfb/tunnel/pacing.py`
- `sfb/tunnel/alice_tunnel.py`
- `sfb/reliability/send_window.py` (for stall signal inputs)
- `sfb/config.py`
- `sfb/log_profiles.py`
- `tests/test_pacing.py`
- `doc/bugs/slow_icmp_socks_throughput.md`
