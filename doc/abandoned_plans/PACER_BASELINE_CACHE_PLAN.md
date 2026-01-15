# Pacer Gate Merge Plan

Status: abandoned

## Summary
Reduce per-tick pacing overhead by moving pacer gate decisions into
`AdaptivePacer` and removing `PacerGateController`, avoiding repeated baseline
computations and cross-module indirection.

## Goals
- Eliminate `PacerGateController` by folding its `check_send` logic into
  `AdaptivePacer`.
- Preserve pacing behavior and decision outputs with identical inputs.
- Keep Python 2.7/3 compatibility and standard library usage.

## Non-Goals
- Change pacing algorithms, thresholds, or send-window policy.
- Alter logging cadence or content.
- Add or run automated tests.

## Affected Components
- `sfb/pacing.py`
- `sfb/reliability/pacer_gate.py`
- `sfb/reliability/__init__.py`
- `sfb/tunnel/alice_tunnel.py`

## Plan
1. Move `check_send` logic into `AdaptivePacer`.
   - Introduce a new method (for example, `check_send_gate`) on
     `AdaptivePacer` that accepts the same inputs used today by
     `PacerGateController.check_send` and returns the same decision dict.
   - Keep helper logic (baseline target, window distance checks) within
     `pacing.py` so it can be reused without cross-module calls.

2. Remove `PacerGateController`.
   - Delete `sfb/reliability/pacer_gate.py` and the export from
     `sfb/reliability/__init__.py`.
   - Update all call sites in the same change (breaking change) to call the
     new `AdaptivePacer` method directly.

3. Update tunnel call sites and preserve logging fields.
   - Replace `self._pacer_gate.check_send(...)` in
     `sfb/tunnel/alice_tunnel.py` with the new pacer method.
   - Ensure the decision dict uses the same keys so logging and metrics remain
     unchanged.

4. Verify behavior parity in pacing decisions.
   - Confirm the same inputs produce the same block/allow decisions.
   - Keep all logging fields unchanged.

## Testing
- Do not run tests.

## Abandon Notes
- Abandoned after review; moving gating into `AdaptivePacer` increases coupling
  with `SendWindow` and does not deliver the baseline caching/perf gains
  claimed, while risking ordering/logging changes.
