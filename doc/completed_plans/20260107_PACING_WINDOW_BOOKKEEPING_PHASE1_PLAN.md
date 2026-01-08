# Pacing And Window Bookkeeping Phase 1 Plan

Status: completed

## Goal

Reduce per-tick overhead in pacing by sharing baseline/target computations and
threading `now` through pacing and send window call sites in tunnel loops.

## Non-Goals

- Change pacing behavior or targets.
- Implement SACK bitmap caching or SACK ACK scan changes (Phase 2).
- Modify tests under ./tests.

## Affected Components

- sfb/reliability/pacing.py
- sfb/tunnel/alice_tunnel.py
- sfb/tunnel/bob_tunnel.py
- sfb/tunnel/base_tunnel.py

## Design Notes

- Add a pacing helper that returns baseline/target state (base, feedback,
  baseline, blocked, target, modes) and accepts `now`.
- Refactor `target_inflight()` and `state_fields()` to reuse the shared helper
  instead of recomputing the same values per tick.
- Capture `now` once per tunnel tick and pass it into pacer and send window
  helpers that accept `now` to avoid repeated `time_provider.now()` calls.

## Implementation Steps

1. Add a pacing helper in `AdaptivePacer` that returns baseline/target state
   and accepts `now`; refactor `target_inflight()` and `state_fields()` to use
   it.
2. Update Alice/Bob tunnel call sites to compute `now` once per tick and pass
   it into pacer and send window helpers that currently default to
   `time_provider.now()`.
3. Remove redundant pacer state recomputation in logging paths by reusing the
   shared helper output where available.

## Validation

- Manual run with python3 and existing profiling helpers to compare pacer CPU
  before/after (no tests/e2e/).
- Confirm pacing targets and block reasons are unchanged via logs.

## Execution Notes

- Added `_PacerState` plus `_target_state()` and `target_state()` helpers to
  consolidate pacer target computations; `target_inflight()` and
  `state_fields()` now reuse the shared helper.
- Reused precomputed pacer state in Alice send gating/logging to avoid
  duplicate target calculations during `_check_send_pacer()`.
- Threaded `now` through pacer state/logging call sites while keeping feedback
  floor timing anchored to the last ACK time for behavior parity.
- Tests not run (per instructions).
