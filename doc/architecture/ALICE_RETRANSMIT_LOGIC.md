# Alice Retransmit Logic

This document captures the full retransmission behavior on Alice (client side),
including handshake retries, data retransmits, and all gating and side effects
that influence when retransmits happen.

## Scope And Entry Points

Primary implementation locations:
- `sfb/tunnel/alice_tunnel.py`: handshake retries, tick loop, RTO scan,
  send path.
- `sfb/reliability/send_window.py`: unacked tracking, retransmit selection,
  RTT sample eligibility, retransmit counters.
- `sfb/reliability/rtt.py`: RTO estimator and exponential backoff.
- `sfb/tunnel/base_tunnel.py`: packet rebuild with fresh ACK/SACK,
  ACK/SACK processing, recv window.
- `sfb/reliability/pacing.py`: pacing feedback and probe reset on retransmit.
- `sfb/transport/transport_base.py`: send permits and rate limiter.
- `sfb/config.py` and `sfb/protocol/constants.py`: default values and limits.

Context: per `doc/architecture/ASYMMETRY.md`, Alice is timer-driven and initiates transport
polls; Bob only responds to polls. Alice retransmits based on timers, RTT,
and SACK-driven fast retransmit for missing ACK holes.

## Time Source And Units

- All protocol timing uses `time_provider.now()` (monotonic seconds).
- Alice captures a single tick time per `tick()` and threads it through all
  send-window time reads/writes (send_time, ack silence, debug snapshots).
- RTT samples are computed in milliseconds: `(now - send_time) * 1000`.
- RTO comparisons use seconds via `RttEstimator.rto_sec`.
- Unclamped/custom time sources are not allowed; `set_time_source()` always
  clamps and rejects `clamp=False`.

## Handshake Retransmits

Handshake retransmission is independent of the reliability send window.

### SYN retransmit (connect)

`AliceTunnel.connect()`:
- Builds a SYN packet (seq=ISN, flags=FLAG_SYN).
- Sends it via `transport.reserve_send()` and `transport.send()`.
- Waits for SYN+ACK with timeout `min(rto_sec, remaining_timeout)`.
- On timeout or decode failure:
  - `RttEstimator.backoff()` doubles the RTO (clamped).
  - Loop retries until `tunnel_connect_timeout` expires (no extra sleep beyond
    the recv timeout).
- On unexpected response or other errors, Alice backs off and then sleeps
  `min(rto_sec, timeout/10)` before retrying.
- If the transport cannot reserve a send permit, Alice logs
  `tunnel.send_blocked`, sleeps `min(rto_sec, timeout/10)`, and retries.

No RTT samples are taken during handshake; the estimator starts at
`protocol_initial_rto_ms`, may back off on handshake timeouts, and is reset
after the handshake completes.

### Final ACK retransmit (_complete_handshake)

`AliceTunnel._complete_handshake()`:
- Uses `_send_window._next_seq` for the ACK sequence number but does not
  store the ACK in the send window.
- Sends the ACK repeatedly until any valid response is received.
- Each attempt waits up to `min(rto_sec, remaining_timeout)` for a response.
- On timeout or decode failure, calls `RttEstimator.backoff()` and retries.
- If the transport cannot reserve a send permit, Alice sleeps for
  `min(rto_sec, remaining)` and retries.

If handshake ACK retries exceed the remaining timeout (or other errors occur),
`AliceTunnel` logs `tunnel.ack_send_failed`, keeps the tunnel connected, resets
RTO, and still queues negotiation; the error is not propagated.

## Tick Loop Ordering (Data Path)

Each `tick()` in `AliceTunnel` executes in this order:
1. Drain available responses (`transport.recv()` non-blocking).
2. If no responses and transport pending is high, wait up to 50ms for a response.
3. Update `_last_recv_time` when any valid response is decoded.
4. Check the no-response timeout (wall-clock silence, disconnect if exceeded).
5. Scan RTO retransmits and send them if the cumulative ACK has not advanced
   within the current RTO window.
6. Attempt fast retransmit for a SACK hole if the window distance is exceeded.
7. Send new packets or keepalive polls if allowed.

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
- Orders by oldest `send_time` (oldest first), not initial send order.
- Does not mutate send_time; only `mark_retransmit()` updates send_time.
- Includes keepalive-only packets and control-only packets (no segment filter).
- Retransmit scanning is skipped if responses arrived within the current RTO
  window (`ack_silence < rto_sec` based on the last cumulative ACK time).

### Sending

For each candidate:
- `AliceTunnel._can_send_retransmit()` checks the per-tick retransmit budget
  and the rate limiter.
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
  - Increments `_packets_sent` and `_bytes_sent`.
  - Logs `tunnel.retransmit` and `tunnel.packet_send`.
  - The `tunnel.retransmit` reason is `rto` for data retransmits and
    `rto_keepalive` for keepalive retransmits. Fast retransmit uses reason
    `fast_retransmit`.

If the rate limiter denies `consume()` or no send permit is available, the
retransmit is skipped (no backoff, no send_time update).
Keepalive-only RTO candidates are retransmitted (reason `rto_keepalive`).
Keepalive drops only happen when the send window is full and a new keepalive
poll needs to make room.

### Window Semantics

Retransmits reuse an existing sequence number and do not consume a new window
slot. Retransmissions are allowed even when the send window is full.

## Fast Retransmit (SACK Hole)

Fast retransmit is a targeted resend of the missing cumulative ACK hole when
SACK progress is observed and the window distance is stalled. It runs after
the RTO scan in each tick.

Trigger conditions:
- `ack_silence < rto_sec` (cumulative ACK recently advanced).
- SACK progress observed while the cumulative ACK is unchanged.
- Send window distance exceeded; the missing sequence is `last_cum_ack`.
- Missing seq is still unacked and its age exceeds
  `rto_sec * tunnel_fast_retransmit_min_age_ratio`.
- Per-seq fast retransmit count is below `tunnel_fast_retransmit_max_per_seq`.

Send behavior:
- Uses `_send_retransmit(..., reason='fast_retransmit')`.
- Respects retransmit budget and rate limiter.
- Fast retransmit counts are pruned when a seq leaves the unacked set.
- Keepalive holes may be fast retransmitted; keepalives are also eligible for
  RTO retransmit (reason `rto_keepalive`).

## RTT And RTO Estimation (Alice Only)

`RttEstimator` details (`sfb/reliability/rtt.py`):
- Initial RTO: `protocol_initial_rto_ms` (default 1000ms).
- EWMA update:
  - First sample: `srtt = sample` (no smoothing).
  - Subsequent: `srtt = 0.875 * srtt + 0.125 * sample`.
  - `rto = clamp(srtt * 2, min_rto_ms, max_rto_ms)`.
- Backoff:
  - `backoff()` doubles the current RTO (clamped).
  - Called on RTO-driven retransmits (at most once per tick) and on handshake
    timeouts/invalid responses.
- Global estimator:
  - One estimator per Alice tunnel, not per packet.
  - Any backoff affects all outstanding retransmit decisions.

## ACK/SACK Processing And RTT Samples

`BaseTunnel._process_incoming_packet()`:
- Calls `SendWindow.process_ack_with_progress(ack, sack, now)` to update
  cumulative ACK tracking, SACK progress, and ACK/SACK cleanup in one step.
- ACK/SACK cleanup matches `SendWindow.process_ack(ack, sack, now)`:
  - Cumulative ACKs remove all `seq < ack` in send order.
  - SACK ACKs remove `ack + offset` for each set bit (offset 1..256).

RTT sampling (`SendWindow._ack_seq`):
- RTT sample is recorded only if `retransmit_count == 0` (Karn's rule).
- Sample value: `(now - send_time) * 1000` milliseconds.
- Samples are collected for both cumulative ACKs and SACK ACKs.
  - RTT samples are only taken when the response carries `HAS_SEGMENTS`.
  - When sampling is enabled, any newly acked first-TX packet can contribute
    except `KEEPALIVE`.

Effects on retransmit logic:
- RTT samples feed `RttEstimator.add_sample()` and reset backoff.
- ACK progress updates `SendWindow.last_ack_progress_time` and sets
  `_ack_progressed` for window-growth gating.
- `data_acked_count` (only packets with segments) drives pacing feedback.
- `KEEPALIVE` packets never contribute RTT samples or pacing feedback because
  pacing uses `data_acked_count`.

## Polling And Keepalive Effects On Retransmit Timing

Alice controls when Bob can ACK by polling. Poll cadence affects when ACKs
arrive, which in turn drives RTT samples and retransmit timing.

`AliceTunnel._poll_decision()`:
- If the last response had only keepalive and grace polls remain:
  - Poll immediately, decrement grace.
- If grace expired:
  - Poll only when `now - last_send_time >= keepalive_interval`.
- If Alice saw real data or has pending data acks:
  - Poll immediately.
- Otherwise:
  - Poll at `keepalive_interval`.

Keepalive specifics:
- Alice's empty polls carry `FLAG_KEEPALIVE`, including grace polls and
  ACK-progress polls. Bob's empty responses use `FLAG_KEEPALIVE` when idle.
- Empty packets still use sequence numbers and are tracked in the send window.
  RTT sampling is response-gated; `KEEPALIVE` packets are excluded.
- If an empty keepalive poll is ready and the window is full, the oldest
  keepalive is dropped so a replacement keepalive poll can be sent.
- Bob suppresses keepalive responses when any channel has pending data; he
  responds with data instead.

## Instrumentation Events (Alice)

Key structured events emitted during retransmit/ACK handling:
- `tunnel.retransmit_scan`: RTO scan summary (candidate count, RTO, ACK silence).
- `tunnel.retransmit_skip`: includes reasons like `ack_silence`.
- `tunnel.retransmit`: retransmit send details (seq, flags, ack/sack, bytes,
  reason, previous send age/count). Reasons include `rto`, `rto_keepalive`,
  and `fast_retransmit`.
- `tunnel.keepalive_drop`: keepalive drops (`window_full`).
- `tunnel.ack_detail`: ACK processing detail (acked counts, RTT samples, send
  and recv window snapshots).
- `tunnel.reliability_state`: structured snapshot of send/recv window, RTT,
  and counters after key gating decisions.

## Rate Limiting, Pacing, And Transport Gating

### Rate Limiter (Config.tunnel_send_rate)

- New sends: `_can_send_new()` uses `RateLimiter.can_send()`, and
  `_send_new_packet()` uses `RateLimiter.consume()`.
- Retransmits: `_can_send_retransmit()` uses `can_send()`, and
  `_send_retransmit()` uses `consume()`.
- If `consume()` fails, the send/retransmit is skipped and logged.
- Burst capacity derives from `tunnel_send_rate` when enabled.
- When `tunnel_send_rate <= 0`, the limiter is disabled (always allows).

### Adaptive Pacing

- Applies only to new data sends (not keepalive, not retransmits).
- `on_ack()` uses `data_acked_count` and `srtt_ms` to adjust target inflight.
- `on_retransmit()` resets probe growth (reduces aggressiveness).
- Keepalive polls bypass pacing checks.

### Transport Capacity

All sends require a transport `SendPermit`:
- `transport.reserve_send()` may return None when in-flight capacity is full.
- `_reserve_transport_permit()` wraps `reserve_send()` and logs transport blocks.

If no permit is available, retransmit is skipped (no backoff, no send_time
update).

## No-Response Timeout (Failure Detection)

Alice tracks time since the last valid response:
- `_last_recv_time` updates on any valid decoded response.
- Invalid or undecodable responses do not reset the timer.
  - Keepalive-only responses still count as valid responses.

If `now - _last_recv_time >= tunnel_no_response_timeout`, Alice closes the
connection and logs `tunnel.timeout_no_response`. Retransmissions stop once closed.

## Logging And Stats

Key retransmit-related events:
- `tunnel.retransmit`: emitted on each retransmit (reasons `rto`,
  `rto_keepalive`, `fast_retransmit`).
- `tunnel.send_blocked`: emitted when rate-limited or transport-blocked.
- `tunnel.packet_send` and `tunnel.packet_recv`: all sends/receives.
- `tunnel.ack`: ACK/SACK processing details.
- `tunnel.timeout_no_response`: no-response timeout triggered.

If stats are enabled (verbose logging, `-v`):
- `ReliabilityStats.retransmit_packets` increments per retransmit.
- `ReliabilityStats.rtt_samples` increments on valid RTT samples.
- `ReliabilityStats.retransmit_skipped_rate_limit` and
  `ReliabilityStats.retransmit_skipped_transport` track retransmit skips.

## Configuration Knobs And Defaults

Retransmit-related settings in `Config`:
- `protocol_initial_rto_ms` (default 1000)
- `protocol_min_rto_ms` (default 500)
- `protocol_max_rto_ms` (default 10000)
- `tunnel_no_response_timeout` (default 60.0)
- `tunnel_retransmit_cap` (default 2)
- `tunnel_fast_retransmit_enabled` (default True)
- `tunnel_fast_retransmit_min_age_ratio` (default 0.25)
- `tunnel_fast_retransmit_max_per_seq` (default 2)
- `tunnel_keepalive_interval` (default 1.0)
- Pong grace polls (derived as `2 * proposed_window` at init)
- `tunnel_send_rate` (default 0.0, unlimited; burst derives from rate)
- `tunnel_adaptive_pacing_enabled` (default True)
- `tunnel_pace_rtt_floor_ms` (default 5.0)
- `tunnel_poll_min_interval` (default 0.0)
- Poll pacing max interval derives from `tunnel_keepalive_interval`.
- `tunnel_poll_rtt_ratio` (default 0.75)
- `non_blocking_poll_timeout` (default 0.0001)
- `tunnel_initial_window` (default 1)
- `max_in_flight` (default 256)
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
- Alice can fast retransmit a single missing sequence when SACK progress is
  observed; there is no fast recovery.
