# Alice Retransmit Logic

This document captures the full retransmission behavior on Alice (client side),
including handshake retries, data retransmits, fast retransmit/recovery, and
all gating and side effects that influence when retransmits happen.

## Scope And Entry Points

Primary implementation locations:
- `sfb/tunnel/alice_tunnel.py`: handshake retries, tick loop, RTO scan,
  fast retransmit, fast recovery, send path.
- `sfb/reliability/send_window.py`: unacked tracking, retransmit selection,
  RTT sample eligibility, retransmit counters.
- `sfb/reliability/rtt.py`: RTO estimator and exponential backoff.
- `sfb/tunnel/base_tunnel.py`: packet rebuild with fresh ACK/SACK,
  ACK/SACK processing, recv window.
- `sfb/tunnel/pacing.py`: pacing feedback and probe reset on retransmit.
- `sfb/transport/transport_base.py`: send permits and rate limiter.
- `sfb/config.py` and `sfb/protocol/constants.py`: default values and limits.

Context: per `doc/ASYMMETRY.md`, Alice is timer-driven and initiates transport
polls; Bob only responds to polls. Alice retransmits based on timers and RTT.

## Time Source And Units

- All protocol timing uses `time_provider.now()` (monotonic seconds).
- RTT samples are computed in milliseconds: `(now - send_time) * 1000`.
- RTO comparisons use seconds via `RttEstimator.rto_sec`.

## Handshake Retransmits

Handshake retransmission is independent of the reliability send window.

### SYN retransmit (connect)

`AliceTunnel.connect()`:
- Builds a SYN packet (seq=ISN, flags=FLAG_SYN).
- Sends it via `transport.reserve_send()` and `transport.send()`.
- Waits for SYN+ACK with timeout `min(rto_sec, remaining_timeout)`.
- On timeout, decode failure, or unexpected response:
  - `RttEstimator.backoff()` doubles the RTO (clamped).
  - Loop retries until `tunnel_connect_timeout` expires.
- If the transport cannot reserve a send permit, Alice logs
  `tunnel.send_blocked`, sleeps `min(rto_sec, timeout/10)`, and retries.
- After each attempt (successful or not), Alice sleeps
  `min(rto_sec, timeout/10)` before retrying, unless the loop exits.

No RTT samples are taken during handshake; the estimator starts at
`protocol_initial_rto_ms` and only backs off until data ACK samples arrive.

### Final ACK retransmit (_complete_handshake)

`AliceTunnel._complete_handshake()`:
- Uses `_send_window._next_seq` for the ACK sequence number but does not
  store the ACK in the send window.
- Sends the ACK repeatedly until any valid response is received.
- Each attempt waits up to `min(rto_sec, remaining_timeout)` for a response.
- On timeout or decode failure, calls `RttEstimator.backoff()` and retries.
- If the transport cannot reserve a send permit, Alice sleeps for
  `min(rto_sec, remaining)` and retries.

If handshake ACK retries exceed the remaining timeout, `TunnelError` is raised.

## Tick Loop Ordering (Data Path)

Each `tick()` in `AliceTunnel` executes in this order:
1. Drain available responses (`transport.recv()` non-blocking).
2. If no responses and transport pending is high, wait up to 50ms for a response.
3. Reset packet-count timeout if any valid response was decoded.
4. Check packet-count timeout (disconnect if exceeded).
5. Scan RTO retransmits and send them.
6. Send new packets or keepalive polls if allowed.

Retransmit scanning happens before new sends, so expired packets get priority.
Tick cadence is controlled by `tunnel_tick_sleep` (in `run()` and the
background loop), so retransmits occur on the next tick after an RTO expires,
not necessarily immediately at the expiration time.
The "pending is high" check uses `pending >= int(cap * 0.75)`, where `cap`
is the transport's `max_in_flight` if available, otherwise the send window cap.

## RTO-Based Retransmits

### Selection

`SendWindow.get_retransmits(rto_sec, now)` returns a list of all unacked packets
whose `now - send_time >= rto_sec`. Details:
- Uses the same `now` for all comparisons in a tick.
- Iterates in initial send order (`OrderedDict` order), not by send_time.
- Does not mutate send_time; only `mark_retransmit()` updates send_time.
- Includes keepalive-only packets and control-only packets (no segment filter).

### Sending

For each candidate:
- `AliceTunnel._can_send_retransmit()` checks only the rate limiter.
  - If rate-limited, the loop breaks (no further retransmits this tick).
- `_send_retransmit()` performs the send:
  - Rebuilds the packet with the original `seq` and `flags`, but fresh
    `ack/sack` from the current receive window.
  - Reuses cached `encrypted_body` if available; otherwise re-encodes and
    encrypts the segments with the original sequence number.
  - Requires a transport send permit; if unavailable, retransmit is skipped.
  - Calls `SendWindow.mark_retransmit()` on success:
    - Increments per-packet `retransmit_count`.
    - Updates `send_time` to `now` (restarts the RTO timer).
    - Increments global retransmit stats.
  - Calls `AdaptivePacer.on_retransmit()` (probe reset).
  - Calls `RttEstimator.backoff()` (global RTO doubling).
  - Increments `_packets_sent`, `_bytes_sent`, and `_packets_since_response`.
  - Logs `tunnel.retransmit` and `tunnel.packet_send`.
  - The `tunnel.retransmit` reason is `rto` for timer-driven retransmits.

If the rate limiter denies `consume()` or no send permit is available, the
retransmit is skipped (no backoff, no send_time update).

### Window Semantics

Retransmits reuse an existing sequence number and do not consume a new window
slot. Retransmissions are allowed even when the send window is full.

## RTT And RTO Estimation (Alice Only)

`RttEstimator` details (`sfb/reliability/rtt.py`):
- Initial RTO: `protocol_initial_rto_ms` (default 1000ms).
- EWMA update:
  - First sample: `srtt = sample` (no smoothing).
  - Subsequent: `srtt = 0.875 * srtt + 0.125 * sample`.
  - `rto = clamp(srtt * 2, min_rto_ms, max_rto_ms)`.
- Backoff:
  - `backoff()` doubles the current RTO (clamped).
  - Called on each retransmit and on handshake timeouts/invalid responses.
- Global estimator:
  - One estimator per Alice tunnel, not per packet.
  - Any backoff affects all outstanding retransmit decisions.

## ACK/SACK Processing And RTT Samples

`BaseTunnel._process_incoming_packet()`:
- Updates `_last_cum_ack` when `packet.ack` changes.
- Calls `SendWindow.process_ack(ack, sack, now)`:
  - Cumulative ACKs remove all `seq < ack` in send order.
  - SACK ACKs remove `ack + offset` for each set bit (offset 1..256).

RTT sampling (`SendWindow._ack_seq`):
- RTT sample is recorded only if `retransmit_count == 0` (Karn's rule).
- Sample value: `(now - send_time) * 1000` milliseconds.
- Samples are collected for both cumulative ACKs and SACK ACKs.

Effects on retransmit logic:
- RTT samples feed `RttEstimator.add_sample()` and reset backoff.
- ACK progress sets `_ack_progressed` and `_last_ack_progress_time`.
- `data_acked_count` (only packets with segments) drives pacing feedback.
- Keepalive packets (no segments) can update RTT but do not drive pacing.

## Fast Retransmit And Fast Recovery

### Fast Retransmit Trigger

`AliceTunnel._maybe_fast_retransmit()` triggers when:
- Incoming packet has `sack != 0`, and
- The packet at `seq == packet.ack` is still unacked (gap at the cumulative ACK).

Behavior:
- Retransmits that `seq` immediately (reason `fast_gap`).
- Only once per distinct `ack` value; repeated packets with the same `ack`
  are ignored until `sack == 0` resets the guard.
- Uses the same `_send_retransmit()` path and rate/permit gating.

### Fast Recovery Mode

`AliceTunnel._update_fast_recovery()`:
- Activates when a SACK gap exists (same condition as fast retransmit).
- While active, `_can_send_new()` blocks new data sends unless
  `allow_fast_recovery` is True.
- Tick behavior while active:
  - Data-path sends are control-only (no data segments).
  - Keepalive polls are still allowed to drive ACKs.

Clears fast recovery when:
- `unacked_count == 0`, or
- `packet.sack == 0`, or
- `packet.ack` differs from the stored fast-recovery ACK.

## Polling And Keepalive Effects On Retransmit Timing

Alice controls when Bob can ACK by polling. Poll cadence affects when ACKs
arrive, which in turn drives RTT samples and retransmit timing.

`AliceTunnel._poll_decision()`:
- If the last response had only keepalive and grace polls remain:
  - Poll immediately (no keepalive flag), decrement grace.
- If grace expired:
  - Poll only when `now - last_send_time >= keepalive_interval`,
    and use FLAG_KEEPALIVE.
- If Alice saw real data or has pending data acks:
  - Poll immediately (no keepalive flag).
- Otherwise:
  - Poll at `keepalive_interval` using FLAG_KEEPALIVE.

Keepalive specifics:
- Keepalive packets have FLAG_KEEPALIVE and zero segments.
- They still use sequence numbers, are tracked in the send window, and can
  produce RTT samples (if not retransmitted).
- Keepalive responses are suppressed when any channel has pending data; real
  data replaces keepalives.

## Rate Limiting, Pacing, And Transport Gating

### Rate Limiter (Config.tunnel_send_rate / tunnel_send_burst)

- New sends: `_can_send_new()` uses `RateLimiter.can_send()`, and
  `_send_new_packet()` uses `RateLimiter.consume()`.
- Retransmits: `_can_send_retransmit()` uses `can_send()`, and
  `_send_retransmit()` uses `consume()`.
- If `consume()` fails, the send/retransmit is skipped and logged.
- When `tunnel_send_rate <= 0`, the limiter is disabled (always allows).

### Adaptive Pacing

- Applies only to new data sends (not keepalive, not retransmits).
- `on_ack()` uses `data_acked_count` and `srtt_ms` to adjust target inflight.
- `on_retransmit()` resets probe growth (reduces aggressiveness).
- Keepalive polls bypass pacing checks.

### Transport Capacity And Headroom

All sends require a transport `SendPermit`:
- `transport.reserve_send()` may return None when in-flight capacity is full.
- `_reserve_transport_permit()` also enforces headroom by checking
  `pending_count()` vs `max_in_flight`, and can reject sends if near the limit.
  - Headroom is `max(2, max_in_flight // 16)` (unless max_in_flight <= headroom).
  - Uses `permit.pending_before` when provided, otherwise calls `pending_count()`.
  - If the transport does not expose `pending_count`/`max_in_flight`, headroom
    checks are skipped.

If no permit is available, retransmit is skipped (no backoff, no send_time
update).

## Packet-Count Timeout (Failure Detection)

Alice tracks `_packets_since_response`:
- Incremented on every successful send (new or retransmit).
- Reset to 0 on any valid decoded response (regardless of ACK progress).
- Invalid or undecodable responses do not reset the counter.
  - Keepalive-only and ack-only responses still count as valid responses.

If `_packets_since_response >= tunnel_timeout_packets`, Alice closes the
connection and logs `tunnel.timeout_packets`. Retransmissions stop once closed.

## Logging And Stats

Key retransmit-related events:
- `tunnel.retransmit`: emitted on each retransmit (reason `rto` or `fast_gap`).
- `tunnel.send_blocked`: emitted when rate-limited or transport-blocked.
- `tunnel.packet_send` and `tunnel.packet_recv`: all sends/receives.
- `tunnel.ack`: ACK/SACK processing details.
- `tunnel.timeout_packets`: packet-count timeout triggered.

If `tunnel_stats_enabled`:
- `ReliabilityStats.retransmit_packets` increments per retransmit.
- `ReliabilityStats.rtt_samples` increments on valid RTT samples.

## Configuration Knobs And Defaults

Retransmit-related settings in `Config`:
- `protocol_initial_rto_ms` (default 1000)
- `protocol_min_rto_ms` (default 500)
- `protocol_max_rto_ms` (default 10000)
- `tunnel_timeout_packets` (default 257)
- `tunnel_keepalive_interval` (default 1.0)
- `tunnel_pong_grace_polls` (default 5)
- `tunnel_send_rate` (default 0.0, unlimited)
- `tunnel_send_burst` (default None, equals rate)
- `tunnel_adaptive_pacing_enabled` (default True)
- `tunnel_pace_rtt_floor_ms` (default 5.0)
- `non_blocking_poll_timeout` (default 0.0001)
- `tunnel_initial_window` (default 1)
- `max_in_flight` (default 128)
- `tunnel_window_growth_*` (affects in-flight capacity and pacing target)

Protocol limits:
- `SACK_BITS` and `MAX_IN_FLIGHT` are 256 (SACK coverage and window cap).
- `SEQ_MAX` is 0xFFFF (16-bit sequence space).

## Invariants And Edge Cases

- Retransmits always reuse the original sequence number and flags.
- Retransmits always rebuild headers with fresh ACK/SACK.
- Cached encrypted bodies are reused to keep ciphertext stable (RC4 derives
  keys from seq+direction) and to avoid re-encryption cost.
- `SendWindow.get_retransmits()` is non-mutating; only `mark_retransmit()`
  updates send_time and retransmit_count.
- RTO backoff is global; repeated retransmits without new RTT samples can
  push the RTO to the max clamp.
- Fast retransmit is triggered by SACK gaps, not duplicate ACK counts.
