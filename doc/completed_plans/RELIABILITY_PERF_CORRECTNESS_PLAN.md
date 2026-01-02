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
- `RecvWindow` must drop incoming out-of-order packets when its buffer is full
  (spec requirement). Ensure we keep this behavior and add tests to prevent
  accidental eviction of buffered packets.
- Missing tests for the above failure modes.

## Constraints
- Python 2.7/3 compatible; standard library only.
- Must support Windows and Linux (ICMP transport remains Linux-only).
- Preserve asymmetry rules in `doc/ASYMMETRY.md`.
- Bob's silence timeout uses monotonic time (align with `doc/ASYMMETRY.md`).
- Do not run E2E tests under `tests/e2e/`.
- `max_in_flight` may rise as high as 512; keep any scans or selection bounded
  by `max_in_flight`, and avoid unbounded queues.

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
     map yet; seq reuse cannot overlap while `max_in_flight << 2^16` (planned
     512). Document this assumption. If `max_in_flight` ever approaches the
     sequence space or seq reuse becomes possible, add a generation tag or
     monotonic send id to disambiguate ACK/SACK processing.
   - On send, insert into the ordered dict. On cumulative ACK, pop from the
     front while `seq_lt(entry.seq, ack)`; on SACK ACK, delete `seq` if present.
   - Do not reorder on retransmit; keep insertion order for cumulative ACK
     removal, and avoid double-removal when popping from the front.
   - Update send timestamps only in `mark_retransmit()` after a successful send
     (do not update during selection or before rate limiter checks).
   - For Bob opportunistic retransmit, select the unacked packet with the
     oldest `send_time` (scan the ordered dict; bounded by `max_in_flight`,
     including a future 512 cap).
     This avoids cooldown stalls caused by recently retransmitted head-of-order.
   - For Alice `get_retransmits()`, collect candidates without mutating the
     ordered dict; no timestamp updates during selection; no tombstones remain.
   - Update any internal references/tests that assumed `_send_order` exists or
     mutated `_unacked` directly.
2. Audit monotonic timing usage (no churn).
   - `sfb/time_provider.py` already provides monotonic timing; do a focused audit
     for any new `time.time()` usage in runtime code.
   - Current review did not find stragglers in affected components; only update
     if new wall-clock interval math is discovered.
3. Keep `RecvWindow` buffer-full behavior spec-compliant.
   - Ensure duplicates/already-buffered seqs are rejected before buffer-full
     handling to avoid stats churn.
   - When buffer is full, drop the incoming out-of-order packet; do not evict
     buffered packets (SACKed data must never be discarded).
   - Record buffer pressure via `on_recv_buffer_full()` when dropping due to
     capacity.
   - Keep the existing `SACK_BITS` window check in place.
4. Add targeted unit tests.
   - `SendWindow`: SACK-only progress with a missing cumulative ACK should
     leave only the missing seq in the ordered dict and keep retransmit scans
     bounded.
   - `SendWindow`: Bob opportunistic selection uses oldest `send_time`, and
     `mark_retransmit()` updates timestamps only after a successful send.
   - `RecvWindow`: verify buffer-full drops incoming out-of-order packets and
     preserves existing buffered seqs; duplicates remain ignored.
   - Keepalive: verify pongs are suppressed while any channel has pending data
     (extend existing coverage only if gaps remain).
   - ACK wrap: cumulative ACK pop logic remains correct across seq wrap,
     including a send-order sequence that crosses wrap.
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
- Recv buffer never evicts buffered packets; full buffers drop incoming
  out-of-order packets and record buffer pressure.
- Cumulative ACK processing remains correct after retransmits.
- New unit tests cover the new behavior and pass.
- Keepalive pongs remain suppressed while any channel has pending data.

## Execution Notes
- Updated `SendWindow` to use an `OrderedDict`, select Bob retransmits by oldest
  `send_time`, and keep cumulative ACK removal ordered without tombstones.
- Added reliability and recv-window tests for SACK-only cleanup, wraparound ACK
  handling, send-time selection, and buffer-full drops.
- Ran `python3 -m unittest tests.test_reliability tests.test_tunnel`.
