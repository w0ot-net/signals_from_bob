# Alice Poll Pacing Plan

## Summary
Introduce time-based poll pacing for Alice so polling is spread across the RTT
instead of bursty send-and-drain cycles. The goal is steadier throughput while
keeping overall capacity high.

## Goals
- Reduce burst-and-idle oscillation in Alice polling.
- Maintain or improve peak throughput by keeping inflight near target.
- Preserve asymmetry rules and keepalive suppression semantics.

## Affected Components
- `sfb/tunnel/alice_tunnel.py` (poll scheduling and send loop)
- `sfb/tunnel/pacing.py` (target inflight helpers for poll pacing)
- `sfb/config.py` (poll pacing knobs and validation)
- `sfb/cli.py` (expose new config flags)
- `doc/TUNNEL.md` (poll pacing behavior)
- `doc/ASYMMETRY.md` (polling rate implications)
- `doc/LOGGING.md` (poll pacing log events)

## Plan
1. Add poll pacing config options:
   - `tunnel_poll_pacing_enabled` (bool, default on)
   - `tunnel_poll_min_interval` (seconds, lower bound)
   - `tunnel_poll_max_interval` (seconds, upper bound)
   - `tunnel_poll_rtt_ratio` (fraction of RTT to distribute a target inflight)
   - Validate ranges in `Config.validate()`.
2. Compute a pacing interval in Alice:
   - Derive `target_inflight` from the pacer target, clamped by send window and
     transport max inflight.
   - Use SRRT when available (floor at `tunnel_pace_rtt_floor_ms`), otherwise
     fall back to `tunnel_keepalive_interval`.
   - `interval = clamp(srtt_sec * tunnel_poll_rtt_ratio / max(target_inflight, 1),
     tunnel_poll_min_interval, tunnel_poll_max_interval)`.
3. Gate poll sends with `next_poll_time`:
   - Track `self._next_poll_time` in `AliceTunnel`.
   - Only allow a poll send when `now >= next_poll_time`.
   - After each send that consumes a poll slot (data or keepalive), set
     `next_poll_time = now + interval`.
   - Optional: add a small catch-up budget if we fall behind (cap per tick).
4. Apply pacing alongside existing `_poll_decision` logic:
   - Keep keepalive/pong grace behavior, but space grace polls over time rather
     than issuing immediate bursts.
   - Ensure keepalive pongs remain suppressed when any channel has pending data.
5. Add lightweight logging:
   - Emit a `tunnel.poll_pace` event when the interval changes, with
     `interval`, `target_inflight`, `pending`, and `srtt_ms`.
   - Optionally add pacing block counters in the pacer summary.
6. Update docs to describe the new poll pacing logic and configuration.

## Success Criteria
- `tunnel.pacer_summary` shows steadier send/recv rates (fewer large swings).
- Pending count stays closer to target inflight with less sawtooth behavior.
- Real sessions show improved throughput with less choppy delivery.
