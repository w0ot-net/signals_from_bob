# Reliability Performance and Correctness Plan

## Goal
Fix reliability-layer performance degradation and timing hazards while
preserving protocol behavior.

## Issues
- `SendWindow` keeps SACK-acked seqs in `_send_order`, so the deque grows
  without bound when cumulative ACK stalls; `get_retransmits()` then scans an
  ever-growing list.
- RTT/retransmit timing uses `time.time()`, so clock jumps can cause negative
  RTTs or missed/early retransmits.
- `RecvWindow` drops new out-of-order packets when its buffer is full, even if
  the new packet is closer to `ack` than buffered ones, which can extend
  head-of-line blocking.
- Missing tests for the above failure modes.

## Constraints
- Python 2.7/3 compatible; standard library only.
- Must support Windows and Linux (ICMP transport remains Linux-only).
- Preserve asymmetry rules in `doc/ASYMMETRY.md`.
- Bob's wall-clock silence timeout remains wall-clock (do not convert to monotonic).
- Do not run E2E tests under `tests/e2e/`.

## Plan
1. Fix `SendWindow` send-order tracking.
   - Replace `_unacked` + `_send_order` with a single `OrderedDict` keyed by
     seq -> `_UnackedPacket`, ordered by original send.
   - On send, insert into the ordered dict; on cumulative ACK, pop from the
     front while `seq_lt(seq, ack)`; on SACK ACK, delete by key if present.
   - Do not reorder on retransmit; update timestamps in place so cumulative ACK
     removal stays correct.
   - For Bob opportunistic retransmit, choose the unacked packet with the
     oldest `send_time` (scan the ordered dict; bounded by `MAX_IN_FLIGHT`).
   - For Alice `get_retransmits()`, collect candidates without mutating the
     ordered dict; update timestamps after selection; no tombstones remain.
   - Update any internal references/tests that assumed `_send_order` exists.
2. Use a monotonic clock for reliability timers.
   - Add `sfb/time_utils.py` (or extend `sfb/compat.py`) with
     `monotonic_time()`:
     - Python 3: `time.monotonic()`.
     - Python 2: `time.time()` with a last-value clamp to prevent backwards
       jumps (guarded by a small lock).
   - Switch reliability/tunnel codepaths that compare timestamps to use the
     monotonic helper (send timestamps, ACK progress timers, retransmit timing,
     keepalive/poll scheduling), excluding Bob's wall-clock silence timeout.
   - Ensure all reliability timestamps are sourced from `monotonic_time()`
     consistently; avoid mixing wall-clock `time.time()` with monotonic values.
   - Audit all `time.time()` usage and limit changes to reliability/tunnel:
     - `sfb/reliability/send_window.py` for send/retransmit timestamps.
     - `sfb/tunnel/base_tunnel.py` for ACK progress timers.
     - `sfb/tunnel/alice_tunnel.py` for handshake/poll scheduling loops.
     - `sfb/tunnel/bob_tunnel.py` for poll EWMA/retransmit scheduling, but keep
       `_check_idle_timeout()` on wall-clock time.
   - Keep wall-clock time only for logging/user-facing timestamps and Bob idle.
   - Add a test hook for the monotonic source (module-level indirection with a
     default time provider) so tests can drive time without external deps.
3. Improve `RecvWindow` buffer behavior under pressure.
   - When buffer is full, compute wrap-safe distance from `ack` for the
     incoming packet. If it is closer to `ack` than the farthest buffered
     packet, evict the farthest and accept the new one; otherwise drop the new
     packet (use existing seq compare/distance helpers).
   - Define a deterministic tie-breaker for equal distance (e.g., keep the
     existing packet and drop the new one to avoid churn).
   - When evicting to accept a closer packet, still record buffer pressure
     via `on_recv_buffer_full()` and acceptance via `on_recv_buffered()`.
   - Keep the existing `SACK_BITS` window check in place.
4. Add targeted unit tests.
   - `SendWindow`: SACK-only progress with a missing cumulative ACK should
     leave only the missing seq in the ordered dict and keep retransmit scans
     bounded.
   - `RecvWindow`: verify eviction keeps the nearest offsets, drops the
     farthest when full, and uses the tie-break rule deterministically with
     duplicates/out-of-order arrivals.
   - `monotonic_time()`: ensure non-decreasing outputs with a controllable
     time source (no external dependencies) and restore the default source.
   - Run `python3 -m unittest tests.test_reliability` (no E2E tests).

## Acceptance Criteria
- `_send_order` tombstones no longer accumulate after SACK-only ACK progress.
- Retransmit scanning cost is bounded by `MAX_IN_FLIGHT`.
- Timing is stable across wall-clock adjustments.
- Bob's wall-clock silence timeout remains wall-clock.
- Recv buffer keeps nearest-to-ack packets under pressure.
- Cumulative ACK processing remains correct after retransmits.
- New unit tests cover the new behavior and pass.
- Keepalive pongs remain suppressed while any channel has pending data.
