# Pacer Baseline Cache Plan

Status: draft

## Summary
Reduce per-tick pacing overhead by caching baseline target computations keyed
by (cap, srtt_ms, unacked) so repeated calls in a tick reuse the same result.

## Goals
- Cut repeated `pacing._baseline_target` work during `pacer_gate.check_send`.
- Preserve pacing behavior and decision outputs.
- Keep Python 2.7/3 compatibility and standard library usage.

## Non-Goals
- Change pacing algorithms, thresholds, or send-window policy.
- Alter logging cadence or content.
- Add or run automated tests.

## Affected Components
- `sfb/pacing.py`
- `sfb/pacer_gate.py`

## Plan
1. Add a small cache in `pacing.py` for baseline target results.
   - Store the last `(cap, srtt_ms, unacked)` inputs and computed target.
   - Keep the cache minimal (single-entry) to avoid churn and complexity.

2. Use the cached baseline in `pacer_gate.check_send`.
   - Thread the inputs through the same call path as today and replace direct
     baseline calls with the cached helper.
   - Ensure cache is bypassed when any input is None or invalid.

3. Verify behavior parity in pacing decisions.
   - Confirm no logic changes beyond avoiding recomputation.
   - Keep all logging fields unchanged.

## Testing
- Do not run tests.
