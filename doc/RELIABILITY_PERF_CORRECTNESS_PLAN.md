# Reliability Performance and Correctness Plan

## Goal
Fix reliability-layer performance degradation and timing hazards while
preserving protocol behavior.

## Issues
- `SendWindow` keeps SACK-acked seqs in `_send_order`, so the deque grows
  without bound when cumulative ACK stalls; `get_retransmits()` then scans an
  ever-growing list.
- Bob opportunistic retransmit only considers the send-order head; if that
  packet was just retransmitted, cooldown blocks retransmits even when older
  packets are eligible.
- Timing is already routed through `time_provider.now()`, but we should confirm
  no new wall-clock usages slipped in.
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

## Affected Components
- `sfb/reliability/send_window.py`
- `sfb/reliability/recv_window.py`
- `sfb/tunnel/bob_tunnel.py`
- `tests/test_reliability.py`
- `tests/test_tunnel.py`
- `tests/test_channel.py` (only if keepalive suppression coverage needs updates)

## Plan
1. Fix `SendWindow` send-order tracking.
   - Replace `_unacked` + `_send_order` with a single `OrderedDict` keyed by
     `seq -> _UnackedPacket`, ordered by original send. Do not add a generation
     map; seq reuse cannot overlap with `MAX_IN_FLIGHT=64`, and ACK/SACK carry
     only seqs.
   - On send, insert into the ordered dict. On cumulative ACK, pop from the
     front while `seq_lt(entry.seq, ack)`; on SACK ACK, delete `seq` if present.
   - Do not reorder on retransmit; keep insertion order for cumulative ACK
     removal, and avoid double-removal when popping from the front.
   - Update send timestamps only in `mark_retransmit()` after a successful send
     (do not update during selection or before rate limiter checks).
   - For Bob opportunistic retransmit, select the unacked packet with the
     oldest `send_time` (scan the ordered dict; bounded by `MAX_IN_FLIGHT`).
     This avoids cooldown stalls caused by recently retransmitted head-of-order.
   - For Alice `get_retransmits()`, collect candidates without mutating the
     ordered dict; no timestamp updates during selection; no tombstones remain.
   - Update any internal references/tests that assumed `_send_order` exists or
     mutated `_unacked` directly.
2. Audit monotonic timing usage (no churn).
   - `sfb/time_provider.py` already provides monotonic timing; do a focused audit
     for any new `time.time()` usage in runtime code.
   - If any stragglers exist, route interval math through `time_provider.now()`
     and reserve `time_provider.wall_time()` for logging/user-facing timestamps.
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
   - Keepalive: verify pongs are suppressed while any channel has pending data
     (extend existing coverage only if gaps remain).
   - ACK wrap: cumulative ACK pop logic remains correct across seq wrap.
   - Run `python3 -m unittest tests.test_reliability tests.test_tunnel`
     (no E2E tests).

## Acceptance Criteria
- `_send_order` tombstones no longer accumulate after SACK-only ACK progress;
  SACK ACKs remove entries from the ordered dict.
- Retransmit scanning cost is bounded by `MAX_IN_FLIGHT`.
- Bob opportunistic retransmit uses oldest `send_time` rather than send order.
- Timing continues to use `time_provider.now()`; no new wall-clock interval math
  in runtime code.
- Bob's silence timeout uses monotonic time.
- Recv buffer keeps nearest-to-ack packets under pressure.
- Cumulative ACK processing remains correct after retransmits.
- New unit tests cover the new behavior and pass.
- Keepalive pongs remain suppressed while any channel has pending data.
