# Reliability Performance and Correctness Plan

## Goal
Fix reliability-layer performance degradation and timing hazards while
preserving protocol behavior.

## Issues
- `SendWindow` keeps SACK-acked seqs in `_send_order`, so the deque grows
  without bound when cumulative ACK stalls; `get_retransmits()` then scans an
  ever-growing list.
- RTT/retransmit timing must be monotonic; wall-clock jumps can cause negative
  RTTs or missed/early retransmits if `time_provider.now()` is not used.
- `RecvWindow` drops new out-of-order packets when its buffer is full, even if
  the new packet is closer to `ack` than buffered ones, which can extend
  head-of-line blocking.
- Missing tests for the above failure modes.

## Constraints
- Python 2.7/3 compatible; standard library only.
- Must support Windows and Linux (ICMP transport remains Linux-only).
- Preserve asymmetry rules in `doc/ASYMMETRY.md`.
- Bob's silence timeout uses monotonic time (align with `doc/ASYMMETRY.md`).
- Do not run E2E tests under `tests/e2e/`.

## Plan
1. Fix `SendWindow` send-order tracking.
   - Replace `_unacked` + `_send_order` with a single `OrderedDict` keyed by
     a composite (seq, generation) -> `_UnackedPacket`, ordered by original
     send to avoid collisions if seq wraps while packets are in flight.
   - On send, insert into the ordered dict; on cumulative ACK, pop from the
     front while `seq_lt(entry.seq, ack)`; on SACK ACK, delete by key if present.
   - Do not reorder on retransmit; keep insertion order for cumulative ACK
     removal, and avoid double-removal when popping from the front.
   - Update send timestamps only in `mark_retransmit()` after a successful send
     (do not update during selection or before rate limiter checks).
   - For Bob opportunistic retransmit, choose the unacked packet with the
     oldest `send_time` (scan the ordered dict; bounded by `MAX_IN_FLIGHT`).
     This is a behavior change from "send order"; add a targeted test.
   - For Alice `get_retransmits()`, collect candidates without mutating the
     ordered dict; no timestamp updates during selection; no tombstones remain.
   - Update any internal references/tests that assumed `_send_order` exists.
   - Track per-seq generation in the send window to map ACK/SACK seqs back to
     their composite key (e.g., maintain seq -> generation for active entries).
2. Use a shared monotonic clock for protocol timing.
   - Use `sfb/time_provider.py` (shared with
     `doc/MONOTONIC_TIME_PROVIDER_PLAN.md`) with `now()`:
     - Python 3: `time.monotonic()`.
     - Python 2: `time.clock()` on Windows; otherwise `time.time()` with a
       last-value clamp to prevent backwards jumps (guarded by a small lock).
       Forward jumps are acceptable and will advance timers.
   - Switch protocol codepaths that compare timestamps to use
     `time_provider.now()` (send timestamps, ACK progress timers, retransmit
     timing, keepalive/poll scheduling, Bob idle timeout, channel timeouts,
     transport pending timeouts, and module pacing).
   - Decide and document time source for rate limiting/token buckets used in
     tunnel pacing; prefer `time_provider.now()` for tunnel-level pacing and pass
     it into limiter calls when available.
   - Thread the chosen time provider into rate limiter update calls to avoid
     mixed wall-clock/monotonic use.
   - Ensure all reliability timestamps are sourced from `time_provider.now()`
     consistently; avoid mixing wall-clock `time.time()` with monotonic values.
   - Audit all `time.time()` usage across runtime code and tests; route interval
     math through `time_provider.now()` and keep wall time limited to logging
     or user-facing timestamps via `time_provider.wall_time()`.
   - Add a test hook for the monotonic source (module-level indirection via
     `time_provider`) so tests can drive time without external deps.
3. Improve `RecvWindow` buffer behavior under pressure.
   - Check for duplicates/already-buffered seqs before running the buffer-full
     eviction logic to avoid evicting useful packets for duplicates.
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
   - `SendWindow`: Bob opportunistic selection uses oldest `send_time`, and
     `mark_retransmit()` updates timestamps only after a successful send.
   - `RecvWindow`: verify eviction keeps the nearest offsets, drops the
     farthest when full, and uses the tie-break rule deterministically with
     duplicates/out-of-order arrivals.
   - `time_provider.now()`: ensure non-decreasing outputs with a controllable
     time source (no external dependencies) and restore the default source.
   - Keepalive: verify pongs are suppressed while any channel has pending data.
   - ACK wrap: cumulative ACK pop logic remains correct across seq wrap.
   - Run `python3 -m unittest tests.test_reliability` (no E2E tests).

## Acceptance Criteria
- `_send_order` tombstones no longer accumulate after SACK-only ACK progress.
- Retransmit scanning cost is bounded by `MAX_IN_FLIGHT`.
- Timing is stable across backward wall-clock adjustments; forward jumps may
  still advance timers on Python 2.
- Bob's silence timeout uses monotonic time.
- Recv buffer keeps nearest-to-ack packets under pressure.
- Cumulative ACK processing remains correct after retransmits.
- New unit tests cover the new behavior and pass.
- Keepalive pongs remain suppressed while any channel has pending data.
