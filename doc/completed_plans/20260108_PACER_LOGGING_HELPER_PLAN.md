# Pacer Logging Helper Plan

Status: draft

## Goal
- Move pacer target/state/summary log field assembly out of
  `AliceTunnel` into a reliability-side helper to reduce tunnel size and keep
  logging mechanics centralized.
- Preserve existing log event names, messages, and field keys.

## Non-Goals
- Change pacing behavior, thresholds, or feedback logic.
- Modify reliability statistics collection or transport pacing rules.
- Run tests here.

## Affected Components
- sfb/tunnel/alice_tunnel.py
- sfb/reliability/pacing.py
- sfb/reliability/send_window.py
- sfb/reliability/__init__.py
- sfb/reliability/pacer_logging.py (new)

## Plan
1. Inventory the current pacer logging responsibilities in
   `sfb/tunnel/alice_tunnel.py`.
   - `_maybe_log_pacer_target_change`, `_log_pacer_adjust`,
     `_maybe_log_pacer_adjust`, `_log_pacer_state`, `_maybe_log_pacer_summary`.
   - Document required inputs and the exact field keys they emit.
2. Add a reliability-side helper (e.g., `PacerLoggingHelper`) in
   `sfb/reliability/pacer_logging.py`.
   - Own the summary counters (`target_sum`, `target_count`,
     `blocked_counts`) and last-summary snapshots used for deltas.
   - Expose methods that return field dicts for target/state/summary logs and
     update internal counters at the same points as today.
3. Update `AliceTunnel` to use the helper.
   - Replace manual field assembly with helper calls while keeping the
     `log_event` calls in `AliceTunnel`.
   - Pass in dynamic inputs (send/recv counters, transport pending/max,
     `send_window` distances, reliability stats snapshots) as explicit args.
   - Remove redundant state variables in `AliceTunnel` once the helper owns
     them.
4. Export the helper from `sfb/reliability/__init__.py` if needed for use by
   tunnel code.
5. Confirm parity by comparing old/new log field keys and ensuring deltas,
   averages, and summary reset timing match.

## Testing
- Do not run tests here. The user can run python3 tests if needed.

## Execution Notes
- Added `PacerLoggingHelper` in reliability and routed pacer target/state/
  summary logging through it.
- Kept log event names/fields intact; tests not run (not requested).
