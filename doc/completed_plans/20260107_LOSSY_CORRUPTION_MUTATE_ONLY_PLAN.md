# Lossy Corruption Mutate-Only Plan

Status: completed

## Goal

Remove the `corrupt_mode` switch so corruption always mutates packet bytes
instead of dropping packets.

## Non-Goals

- Add new CLI flags or change loss/dup/reorder behavior.
- Change tunnel reliability or transport MTU negotiation.
- Modify tests under ./tests (blocked by repo instructions).

## Affected Components

- sfb/transport/lossy.py
- doc/architecture/LOSSY_TRANSPORT.md
- doc/plans/CORRUPTION_SIMULATION_FLAGS_PLAN.md
- tests/test_lossy_transport.py (references corrupt_mode; left untouched)

## Design Notes

- `NetworkImpairment` no longer accepts `corrupt_mode`; corruption always
  mutates data using the existing byte-flip logic.
- Dropping corrupted packets is removed; use `loss_rate` for loss simulation.
- This is a breaking change for external callers that pass `corrupt_mode`.

## Implementation Steps

1. Remove `corrupt_mode` from `NetworkImpairment` args, validation, and state.
2. Simplify lossy send/recv paths to mutate whenever a corruption decision is
   selected, with no drop branch.
3. Update lossy transport docs (and the corruption CLI plan) to describe
   mutate-only corruption and direct loss users to `loss_rate`.

## Validation

- Manual smoke check: use a small in-memory transport run with non-zero
  `corrupt_rate` and confirm packets are delivered but altered.
- Do not run tests/e2e/.

## Execution Notes

- Removed `corrupt_mode` from `NetworkImpairment` and lossy send/recv logic.
- Documented mutate-only corruption in lossy transport docs and corruption plan.
- Validation not run (not requested).
