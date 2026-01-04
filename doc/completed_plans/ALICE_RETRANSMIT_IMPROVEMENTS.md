# Alice Retransmit Improvements

This document proposes improvements to Alice retransmit behavior to reduce
RTO blowups, smooth recovery, and isolate keepalive effects while preserving
asymmetry constraints (Alice polls, Bob only responds).

## Goals

- Reduce RTO inflation during loss recovery without weakening loss detection.
- Avoid retransmit bursts that stall later retries.
- Keep keepalive traffic from skewing data timing.
- Preserve the asymmetric poll/response model and packet-count timeouts.
- Keep changes compatible with existing protocol wire format.

## Observed Issues

From the current behavior in `doc/ALICE_RETRANSMIT_LOGIC.md`:

- RTO backoff is applied on every retransmit and can occur multiple times per
  tick.
- Retransmit selection happens once per tick using a single RTO snapshot, which
  can produce a burst of retransmits followed by an overly large RTO.
- Keepalive-only packets can produce RTT samples and trigger backoff, which can
  slow data recovery after idle or loss of keepalive traffic.
- Handshake backoff affects the same estimator used for data, which can carry
  an inflated RTO into the first data phase.

## Proposed Improvements

### 1) Backoff Policy: Only on RTO Timeouts, At Most Once Per Tick

Change:
- Apply `RttEstimator.backoff()` only for RTO-driven retransmits.
- Guard backoff so it happens at most once per tick (or once per tick per
  tunnel) even if multiple packets are retransmitted.

Why:
- Backoff should reflect actual timeouts, not early retries.
- Multiple backoffs in a single tick can multiply the RTO too aggressively.

Notes:
- Track a simple `backoff_epoch` counter in `AliceTunnel` to ensure a single
  backoff per tick.

### 2) Retransmit Scheduling: Limit Burst Size

Change:
- Cap RTO retransmits per tick to a small number (for example 1-2), or cap by
  bytes, and carry remaining candidates into subsequent ticks.
- Prefer the oldest `send_time` first rather than send order when selecting
  retransmits.

Why:
- Reduces bursty retransmission that inflates RTO and rate limiting.
- Prioritizing the oldest packet aligns better with timeout semantics.

Notes:
- A small cap respects the asymmetric model by letting polls drive ACK timing.
- If ordering by `send_time` is expensive, keep a simple list and select the
  oldest due packet each tick.

### 3) Keepalive Isolation: Do Not Affect Data RTT Or Backoff

Change:
- Exclude keepalive-only packets from RTT sampling.
- Do not apply RTO backoff for retransmits of keepalive-only packets.
- Consider not retransmitting keepalives at all; send a fresh keepalive/poll
  instead of reusing the old sequence number.

Why:
- Keepalive loss should not slow data recovery.
- Poll cadence already bounds liveness detection in the asymmetric model.

Notes:
- If RTT tracking for keepalives is still useful for diagnostics, record it in
  a separate stat without feeding the main estimator.

### 4) Handshake RTO Separation

Change:
- Use a separate RTO estimator for the handshake, or reset the data estimator
  to `protocol_initial_rto_ms` when the handshake completes.

Why:
- Prevents a choppy handshake from delaying first data retransmits.

Notes:
- This is internal behavior only; it does not alter wire format.

### 5) Add Explicit Metrics For Skipped Retransmits

Change:
- Track counters for "retransmit skipped due to rate limiter" and "skipped due
  to transport permit" to distinguish congestion from transport saturation.

Why:
- Makes it easier to tune pacing and diagnose stalls under load.

Notes:
- Metrics only; no protocol behavior change.

## Compatibility And Risk

- All changes are internal to Alice and do not change the wire format.
- Backoff and scheduling changes may alter retransmit timing; this is expected
  and desired to avoid RTO explosions.
- Keepalive isolation should not affect liveness because Alice polls anyway.

## Validation Ideas

- Simulate loss with a fixed poll cadence and confirm RTO does not grow by more
  than one backoff step per tick.
- Verify that keepalive-only loss does not increase data RTO.
- Measure recovery time for single-loss and multi-loss SACK cases.
- Ensure packet-count timeout behavior is unchanged.

## Implementation Plan

### Affected Components

- `sfb/tunnel/alice_tunnel.py`: backoff gating, retransmit cap, keepalive resend
  policy, handshake estimator reset.
- `sfb/reliability/send_window.py`: retransmit selection ordering, keepalive
  metadata, RTT sampling eligibility.
- `sfb/reliability/rtt.py`: optional handshake estimator or reset helper.
- `sfb/tunnel/base_tunnel.py`: ACK processing hooks for keepalive RTT filtering.
- `sfb/reliability/stats.py` (or equivalent): add skipped-retransmit counters.
- `sfb/config.py`: optional knobs for retransmit caps or keepalive sampling.

### Plan Steps

1. Decide final policy values: retransmit cap per tick, keepalive treatment,
   and whether to reset or split RTO estimator after handshake.
2. Update `SendWindow.get_retransmits()` to select by oldest `send_time` and
   return a bounded list; plumb keepalive metadata to callers.
3. Update `AliceTunnel` retransmit loop to apply backoff only for RTO-driven
   retransmits and at most once per tick; apply cap and keepalive rules.
4. Exclude keepalive-only packets from RTT sampling, or route those samples to
   a separate stat that does not affect `RttEstimator`.
5. Add metrics for retransmits skipped due to rate limiting or transport
   permits; update logging as needed for visibility.
6. Add or update unit tests around retransmit ordering, backoff gating, and
   keepalive RTT handling (do not run E2E tests here).

## Execution Notes

- Implemented per-tick retransmit budget and single backoff per tick for RTO
  retransmits.
- Dropped keepalive-only RTO candidates, excluded keepalive RTT sampling, and
  reset the RTT estimator after handshake completion.
- Added retransmit skip metrics, and updated unit tests plus retransmit logic
  docs.
