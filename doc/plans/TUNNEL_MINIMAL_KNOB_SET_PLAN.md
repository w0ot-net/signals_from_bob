# Tunnel Minimal Knob Set Plan

Status: draft

## Summary
Define a minimal tunnel knob set by removing redundant configuration pairs and
deriving their values from the remaining knobs. Update code, CLI, scripts, and
docs so the external configuration surface matches the derived behavior.

## Goals
- Remove redundant knobs and replace them with explicit derivations.
- Keep Alice/Bob asymmetry semantics intact.
- Keep logging sufficient to surface derived values.
- Align CLI flags and helper scripts with the new knob set.

## Non-Goals
- Retune pacing or retransmit algorithms beyond derived defaults.
- Add compatibility shims or deprecated alias flags.
- Add or run tests.

## Affected Components
- `sfb/config.py`
- `sfb/cli.py`
- `sfb/tunnel/alice_tunnel.py`
- `sfb/tunnel/bob_tunnel.py`
- `sfb/reliability/pacing.py`
- `sfb/transport/transport_base.py`
- `scripts/icmp_socks_diag.py`
- `scripts/icmp_socks_scp_test.py`
- `doc/architecture/TUNNEL.md`
- `doc/architecture/ALICE_RETRANSMIT_LOGIC.md`
- `doc/architecture/BOB_RETRANSMIT_LOGIC.md`
- `doc/architecture/ICMP_TRANSPORT.md`
- `doc/architecture/ASYMMETRY.md`
- `doc/bugs/slow_icmp_socks_throughput.md`
- `README.md` (if CLI flags are documented)

## Proposed Minimal Knob Set
### Keep
- Timeouts: `tunnel_keepalive_interval`, `tunnel_no_response_timeout`,
  `tunnel_idle_timeout`, `tunnel_connect_timeout`
- Windowing: `tunnel_initial_window`, `max_in_flight`,
  `tunnel_window_growth_enabled`, `tunnel_window_growth_mode`,
  `tunnel_window_growth_step`, `tunnel_window_growth_interval`
- Retransmit (Alice): `tunnel_retransmit_cap`, `tunnel_fast_retransmit_enabled`,
  `tunnel_fast_retransmit_min_age_ratio`, `tunnel_fast_retransmit_max_per_seq`
- Retransmit (Bob): `tunnel_bob_poll_interval`, `tunnel_bob_poll_ewma_alpha`,
  `tunnel_bob_retransmit_poll_factor`, `tunnel_bob_retransmit_max_interval`
- Pacing: `tunnel_send_rate`, `tunnel_adaptive_pacing_enabled`,
  `tunnel_pace_target_inflight_ratio`, `tunnel_pace_min_inflight`,
  `tunnel_pace_max_inflight`, `tunnel_pace_feedback_gain`,
  `tunnel_pace_ack_ewma_alpha`, `tunnel_pace_rtt_floor_ms`,
  `tunnel_pace_ack_idle_reset_sec`
- Poll pacing: `tunnel_poll_pacing_enabled`, `tunnel_poll_min_interval`,
  `tunnel_poll_rtt_ratio`
- Misc: `tunnel_tick_sleep`, `tunnel_bg_stop_timeout`,
  `tunnel_connect_poll_interval`, `non_blocking_poll_timeout`,
  `tunnel_pacer_summary_interval`, `stats_enabled`

### Remove/Derive
- `tunnel_poll_max_interval`: derive as `tunnel_keepalive_interval` inside poll
  pacing clamp.
- `tunnel_pong_grace_polls`: derive as `2 * proposed_window` at init (same
  minimum currently enforced).
- `tunnel_send_burst`: derive as `tunnel_send_rate` capacity when rate > 0,
  otherwise disable rate limiting.
- `tunnel_bob_poll_interval_bg`: derive as `tunnel_bob_poll_interval * 0.1`,
  clamped to `[non_blocking_poll_timeout, tunnel_bob_poll_interval]`.
- `tunnel_bob_retransmit_min_interval`: drop and compute cooldown as
  `max(poll_ewma * tunnel_bob_retransmit_poll_factor, poll_ewma * send_window_max)`
  then clamp to
  `tunnel_bob_retransmit_max_interval`.

## Plan
1. Confirm call sites and doc references for the removed knobs and adjust the
   derivation rules if any transport/module requires a tighter bound.
2. Implement derived values in the runtime code paths (Alice poll pacing, Bob
   retransmit cooldown, background poll interval, pong grace).
3. Remove deleted knobs from config defaults, validation, CLI flags, and helper
   scripts.
4. Update docs and bug notes to reflect the new knob set and derivations.
5. Add or adjust init logging to surface derived values for traceability.

## Testing
- Do not run tests.
