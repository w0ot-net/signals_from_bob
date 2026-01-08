# Pacing And Window Bookkeeping Phase 2 Plan

Status: abandoned

## Goal

Reduce per-packet overhead in receive/send window bookkeeping by optimizing
SACK bitmap handling and SACK ACK processing.

## Non-Goals

- Change pacing behavior or tunnel logic (Phase 1).
- Add new CLI flags or protocol features.
- Modify tests under ./tests.

## Affected Components

- sfb/reliability/recv_window.py
- sfb/reliability/send_window.py
- sfb/protocol/packet.py (optional micro-opt)

## Design Notes

- RecvWindow SACK: cache the bitmap and recompute only when the buffer or
  expected sequence advances; keep wraparound correctness by rebuilding when
  `ack` changes.
- SendWindow SACK ACK: iterate set bits in the SACK bitmap rather than scanning
  all `SACK_BITS` every time.
- seq_diff micro-opt: use a local reference in hot loops; only add a new helper
  in `sfb/protocol/packet.py` if profiling still shows it hot after SACK changes.

## Implementation Steps

1. Implement a cached SACK bitmap in `RecvWindow` with a dirty flag; set dirty
   on buffer changes and `ack` movement, recompute only when needed.
2. Replace the `for offset in range(1, SACK_BITS + 1)` loop in
   `SendWindow._ack_sack()` with a set-bit iteration to skip zero bits.
3. Profile again; if `seq_diff()` is still hot, add a minimal helper or local
   binding and recheck.

## Validation

- Manual run with python3 and existing profiling helpers to compare window CPU
  before/after (no tests/e2e/).
- Confirm ACK/SACK behavior is unchanged via logs.

## Abandonment notes

- 2026-01-07: Abandoned per request; no implementation work recorded.
