# Poll Pacing Helper Plan

Status: draft

## Goal

Extract the poll pacing interval/target computation from
`AliceTunnel._poll_pacing_interval` into a small helper under
`sfb/reliability` so `AliceTunnel` only schedules sleeps and sends.

## Non-Goals

- Change poll pacing behavior, thresholds, or logging semantics.
- Alter keepalive suppression or send scheduling order.
- Add or run tests.

## Affected Components

- sfb/tunnel/alice_tunnel.py
- sfb/reliability/pacing.py
- sfb/reliability/__init__.py

## Design Notes

- The helper must be pure policy: accept explicit inputs and return computed
  values without touching tunnel state.
- Preserve the current math and clamping logic, including RTT floor and
  keepalive interval caps.
- Keep logging (`_maybe_log_poll_pace`) in `AliceTunnel` so it can add
  transport-specific fields (pending count) without new dependencies.
- Return `(interval, target_inflight, srtt_ms)` or `(interval, target_inflight)`
  and let `AliceTunnel` decide what to log and when to store the rounded value.

## Plan

1. Add a helper in `sfb/reliability/pacing.py`:
   - Example: `compute_poll_pacing_interval(srtt_ms, keepalive_interval,
     rtt_floor_ms, poll_rtt_ratio, min_interval, max_interval,
     target_inflight)`.
   - Implement the exact logic currently in `AliceTunnel._poll_pacing_interval`:
     - Use `keepalive_interval` when `srtt_ms` is None or invalid.
     - Apply `rtt_floor_ms` to clamp the RTT.
     - Compute `interval = (srtt_sec * poll_rtt_ratio) / target_inflight`.
     - Clamp `interval` to `[min_interval, max_interval]` and
       `max_interval <= keepalive_interval`.
   - Return `(interval, target_inflight)`.

2. Update `AliceTunnel._poll_pacing_interval` to call the helper:
   - Keep `_poll_pacing_cap()` and `_poll_pacing_target_inflight()` in
     `AliceTunnel` so transport caps and pacer policy remain local.
   - Replace the inline math with a single helper call and keep
     `_maybe_log_poll_pace(interval, target_inflight, srtt_ms)` intact.
   - Preserve the early return when poll pacing is disabled.

3. Export the helper from `sfb/reliability/__init__.py`:
   - Add it to the public imports and `__all__` for consistent access.

## Validation

- No automated tests here. Manual inspection to confirm the helper preserves
  the existing interval math and clamping behavior.
