# Pacing And Window Bookkeeping Plan

Status: draft

## Goal

Reduce per-tick and per-packet overhead in pacing and window bookkeeping by
cutting recomputation and threading `now` through hot paths.

## Non-Goals

- Change pacing, window, or reliability behavior.
- Add new CLI flags or protocol features.
- Modify tests under ./tests.

## Affected Components

- sfb/reliability/pacing.py
- sfb/reliability/recv_window.py
- sfb/reliability/send_window.py
- sfb/protocol/packet.py
- sfb/tunnel/alice_tunnel.py
- sfb/tunnel/bob_tunnel.py
- sfb/tunnel/base_tunnel.py

## Design Notes

- Hot spots are in `sfb/reliability/pacing.py` around `_baseline_target()` and
  `state_fields()`, `sfb/reliability/recv_window.py` `sack`, and
  `sfb/reliability/send_window.py` `_ack_sack()`.
- Pacing: consolidate baseline/target math so `target_inflight()` and
  `state_fields()` can share one computation per tick when both are used.
- Pass `now` deeper: capture `now` once per tick in tunnel loops and thread it
  through pacing and send window helpers to avoid repeated
  `time_provider.now()` calls.
- RecvWindow SACK: cache the bitmap and recompute only when the buffer or
  expected sequence advances; keep wraparound correctness by rebuilding when
  `ack` changes.
- SendWindow SACK ACK: iterate set bits in the SACK bitmap rather than scanning
  all `SACK_BITS` every time.
- seq_diff micro-opt: use a local reference in hot loops; only add a new helper
  in `sfb/protocol/packet.py` if profiling shows it is still a top cost.

## Implementation Steps

## Phase 1: Pacer And Now Threading

1. Add a pacing helper that returns baseline/target state (base, feedback,
   baseline, blocked, target, modes) and accepts `now`; refactor
   `target_inflight()` and `state_fields()` to reuse it.
2. Update Alice/Bob tunnel call sites to compute `now` once per tick and pass
   it into pacing and send window methods that currently default to
   `time_provider.now()`.

## Phase 2: Window Bookkeeping Hot Paths

1. Implement a cached SACK bitmap in `RecvWindow` with a dirty flag; set dirty
   on buffer changes and `ack` movement, recompute only when needed.
2. Replace the `for offset in range(1, SACK_BITS + 1)` loop in
   `SendWindow._ack_sack()` with a set-bit iteration to skip zero bits.
3. Profile again; if `seq_diff()` is still hot, add a minimal helper or local
   binding and recheck.

## Validation

- Use python3 with the existing profiling helpers or a local run to compare
  pacing/window CPU before and after; no tests/e2e/.
- Confirm pacing targets and ACK/SACK behavior stay unchanged via logs.
