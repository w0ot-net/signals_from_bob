# Tunnel Knob Consolidation Plan

Status: completed

## Summary
Consolidate tunnel timing, pacing, and growth configuration to reduce the
number of overlapping knobs. Use a single keepalive-based time scale, drop
redundant poll and pacing controls, and simplify Bob retransmit and Alice window
growth settings while preserving the asymmetry model.

## Goals
- Replace three timeout knobs with one base interval and derived timeouts.
- Remove redundant poll and pacing knobs while keeping predictable limits.
- Simplify Bob retransmit cooldown controls to one derivation path.
- Keep window growth configurable with one clear parameter.
- Update docs and CLI to match the new configuration surface.

## Non-Goals
- Retune pacing or retransmit algorithms beyond knob removal.
- Change protocol roles or asymmetry semantics.
- Add or run automated tests.

## Affected Components
- `sfb/config.py`
- `sfb/cli.py`
- `sfb/tunnel/alice_tunnel.py`
- `sfb/tunnel/bob_tunnel.py`
- `sfb/reliability/pacing.py`
- `sfb/reliability/pacer_logging.py`
- `doc/architecture/TUNNEL.md`
- `doc/architecture/ALICE_RETRANSMIT_LOGIC.md`
- `doc/architecture/BOB_RETRANSMIT_LOGIC.md`
- `doc/architecture/BOB_RETRANSMIT_COOLDOWN.md`
- `doc/architecture/ASYMMETRY.md`
- `doc/architecture/ICMP_TRANSPORT.md`
- `doc/architecture/TLS_TRANSPORT.md`

## Plan
1. Consolidate keepalive and timeouts.
   - Keep `tunnel_keepalive_interval` as the single time base.
   - Derive `tunnel_no_response_timeout` and `tunnel_idle_timeout` internally
     as fixed multiples of `tunnel_keepalive_interval` (constants, not config).
   - Remove `tunnel_no_response_timeout` and `tunnel_idle_timeout` from config
     defaults, validation, CLI flags, and docs; log derived values at init.

2. Simplify poll interval configuration.
   - Remove `tunnel_poll_max_interval` and set the max poll interval to
     `tunnel_keepalive_interval` inside poll pacing calculations.
   - Keep `tunnel_poll_min_interval` and `tunnel_poll_rtt_ratio` as the primary
     controls for pacing shape.
   - Update CLI flags and doc tables to reflect the removal.

3. Pick one pacing system.
   - Treat adaptive pacing as the single send-rate control; remove
     `tunnel_send_rate` and `tunnel_send_burst` gating and CLI flags.
   - When adaptive pacing is disabled, allow only window/distance gating
     (no static rate limiter).
   - Remove references to rate/burst in logs and docs.

4. Unify Bob retransmit cooldown derivation.
   - Keep EWMA-based derivation (`tunnel_bob_retransmit_poll_factor` and
     `tunnel_bob_poll_ewma_alpha`).
   - Remove `tunnel_bob_retransmit_min_interval` and
     `tunnel_bob_retransmit_max_interval`; replace with fixed internal
     floor/cap derived from keepalive or poll cadence.
   - Update retransmit docs to describe the new derivation.

5. Simplify window growth controls.
   - Standardize on linear growth and keep only `tunnel_window_growth_step`.
   - Derive the growth interval from RTT or poll cadence (fallback to
     `tunnel_keepalive_interval` when RTT is unknown).
   - Remove `tunnel_window_growth_mode` and `tunnel_window_growth_interval`
     from config, CLI, and docs.

6. Cleanup and documentation.
   - Remove all deleted knobs from config fields and CLI wiring.
   - Update architecture and transport docs to match the new knob set.
   - Note breaking changes in the plan execution notes.

## Testing
- Do not run tests.

## Execution Notes
- Removed config/CLI knobs: `tunnel_idle_timeout`, `tunnel_no_response_timeout`,
  `tunnel_send_rate`, `tunnel_window_growth_mode`,
  `tunnel_window_growth_interval`, `tunnel_bob_retransmit_max_interval`.
- Alice/Bob timeouts now derive from `tunnel_keepalive_interval * 60`.
- Bob retransmit cooldown is capped at `tunnel_keepalive_interval * 3`.
- Window growth interval derives from RTT or poll cadence (keepalive fallback).
