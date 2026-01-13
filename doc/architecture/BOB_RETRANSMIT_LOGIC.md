# Bob Retransmit Logic

This document captures the complete retransmission behavior on Bob (server
side). It covers handshake resends, opportunistic data retransmits, cooldown
and gating rules, window enforcement overrides, packet rebuild/encryption, and
the logging and stats side effects that accompany retransmits.

## Scope And Entry Points

Primary implementation locations:
- `sfb/tunnel/bob_tunnel.py`: request handling, opportunistic retransmit
  decision, cooldown gates, response building, retransmit send path.
- `sfb/reliability/send_window.py`: unacked tracking, oldest selection, cached
  segments/body, retransmit counters.
- `sfb/tunnel/base_tunnel.py`: ACK/SACK processing, recv window state, packet
  rebuild with fresh ack/sack, send window distance guard.
- `sfb/config.py`: Bob retransmit cooldown configuration and poll EWMA params.
- `sfb/crypto.py`: per-packet encryption key derivation (retransmit stability).
- `doc/architecture/ASYMMETRY.md` and `doc/architecture/PROTOCOL.md`: asymmetry and retransmit rules.

Bob is opportunity-driven: he can only transmit in response to an incoming poll
from Alice. There are no timer-driven retransmits on Bob.

## Time Source And Units

- All timing uses `time_provider.now()` (monotonic seconds).
- Bob captures a single time per request and threads it through send-window
  time reads/writes (send_time, cooldown age, debug snapshots).
- Retransmit cooldown comparisons use seconds.
- There is no RTT estimator or RTO on Bob.
- Unclamped/custom time sources are test-only; a warning is emitted if an
  unclamped source is used while tunnels are active.

## Handshake Resends (Pre-Data)

Bob does not keep handshake packets in the send window, so there is no timer-
driven handshake retransmit. The handshake resend behavior is:
- On every received SYN, Bob immediately replies with SYN+ACK.
- If Alice retries SYN (because her timer expired), Bob sends another SYN+ACK.
- If Alice's final ACK is lost, Bob stays CONNECTING but accepts a data packet
  as an implicit ACK and moves to CONNECTED, then sends a normal response.

No retransmit counters are updated for handshake SYN+ACK responses because they
do not go through the send window.

## Request Handling Order (Where Retransmit Happens)

Retransmit logic runs only when Bob handles a valid request:

1. `handle_request()` updates poll EWMA timing for cooldown calculations.
2. Incoming packet is decoded. If decode fails, Bob returns without responding,
   so no retransmit decision is made on that request.
3. `_process_incoming_packet()` updates ACK/SACK state and prunes acked packets
   from the send window.
4. `_send_response()` decides whether to retransmit or send new data.

Bob never sends outside this request path. At most one response packet is sent
per poll, and retransmit takes priority when selected.

## Retransmit Candidate Selection (Oldest Unacked)

Bob considers a single retransmit candidate per poll:
- `_send_window.get_oldest_unacked_first_info()` returns the unacked packet
  with the oldest initial send time (`first_send_time`). This is not
  necessarily the smallest sequence number.
- Selection uses `first_send_time`, which is set on first send and does not
  change on retransmit, so the original oldest packet stays prioritized.
- `first_send_time` is required; a missing value is a fatal send-window
  inconsistency.
- The returned info includes `(seq, segments, flags, encrypted_body,
  send_time, retransmit_count, first_send_time)`.

If there are no unacked packets, no retransmit is attempted.

## Retransmit Cooldown Gate

Bob suppresses opportunistic retransmits to avoid spamming responses when Alice
polls rapidly.

### Poll EWMA Update

Each request updates a poll interval EWMA used to derive the cooldown:
- `interval = now - last_request_time`.
- On the first request, `_last_request_time` is set and no EWMA is computed.
- Subsequent requests update `_poll_interval_ewma`:
  `ewma = alpha * interval + (1 - alpha) * ewma`.
- `alpha` is `config.tunnel_bob_poll_ewma_alpha`.

This update runs before packet decode, so even malformed requests influence the
EWMA and cooldown timing.

### Cooldown Computation

Cooldown is computed in `_retransmit_cooldown()`:
- Start with `config.tunnel_bob_retransmit_min_interval`.
- If `poll_ewma` exists and `config.tunnel_bob_retransmit_poll_factor > 0`,
  then `cooldown = max(cooldown, poll_ewma * factor)`.
- If `poll_ewma` exists and `max_in_flight > 0`, then
  `cooldown = max(cooldown, poll_ewma * max_in_flight)` to allow one window's
  worth of polls before retransmitting the oldest packet.
- If `config.tunnel_bob_retransmit_max_interval` is set and > 0, clamp:
  `cooldown = min(cooldown, max_interval)`.

### Skip Conditions

Given the oldest unacked packet:
- `age = now - send_time` (send_time is required).

The retransmit is skipped (and only logged) if:
- `age` is set and `age < cooldown` (cooldown gate).

This gate uses a strict `<` comparison. Missing `send_time` is a fatal
send-window inconsistency and results in tunnel shutdown.

When a retransmit is skipped for these reasons, Bob may still send new data or
keepalive packets in the same response.

## Retransmit Send Path (Packet Build And Encryption)

Retransmits are sent via `_send_retransmit_response()`:
- Rebuilds the packet with the original `seq` and stored `flags`, but with
  fresh `ack` and `sack` from the current recv window:
  `packet = _rebuild_packet(seq, segments, flags=flags)`.
- Uses cached `encrypted_body` if available (stored on first send).
- If cached body is missing, re-encodes segments and re-encrypts using the
  original `seq` and outbound direction.
  - RC4 derives a per-packet key from `(seq, direction)`, so retransmits are
    deterministic even when re-encrypted.
- Encodes `header + encrypted_body` into `response_data`.

### Response Payload Cap (Invariant)

For DNS responders, `response_payload_cap` is fixed to the invariant cap and
validated on every request. The DNS transport logs and raises a fatal
transport error if a per-request cap falls below the fixed cap or the
transport send MTU, so retransmits are never blocked by per-request caps.

### Side Effects On Successful Retransmit

On success:
- `send_window.mark_retransmit(seq, now)`:
  - Increments per-packet `retransmit_count`
  - Updates `send_time` to `now` (resets cooldown age)
  - Increments global retransmit stats
- `_packets_sent` and `_bytes_sent` are incremented.
- Events are logged:
  - `tunnel.retransmit` (includes `seq`, flags, ack/sack, bytes, and reason)
  - `tunnel.packet_send`
- The responder is called with the rebuilt packet.

Bob does not use transport send permits or rate limiting on retransmits.

## Window Enforcement Overrides (Bypass Cooldown)

After the initial opportunistic retransmit attempt (and any cooldown skip),
Bob enforces window limits. These checks can trigger retransmits even when the
cooldown gate would have skipped:

### Send Window Full

If `send_window.can_send` is False:
- Bob logs `tunnel.send_window_full` and `tunnel.send_blocked`.
- If an unacked packet exists, he retransmits it with:
  - `context='window_full'`
  - `reason='window_full'` in `tunnel.retransmit`
- Cooldown gate is not applied here.

### Send Window Distance Exceeded

If `_send_window_distance_exceeded()` returns True:
- Uses `seq_diff(next_seq, last_cum_ack)` to compute distance with wraparound.
- Distance limit is `max_in_flight` and is capped at 256.
- When exceeded, Bob logs `tunnel.send_window_distance` and `tunnel.send_blocked`.
- He retransmits the oldest unacked packet with:
  - `context='window_distance'`
  - `reason='window_distance'` in `tunnel.retransmit`
- Cooldown gate is not applied here either.

If there are no unacked packets in these cases, Bob returns without sending.

## Interaction With New Sends And Keepalive

Retransmit priority:
- If an opportunistic retransmit is sent, `_send_response()` returns immediately
  and no new data is sent in that poll.
- If retransmit is skipped by cooldown, Bob may send new data.

New sends and keepalive packets are still tracked in the send window, which
means they can later be retransmitted:
- Keepalive packets (FLAG_KEEPALIVE, no segments) are stored and eligible.
- Flags are preserved on retransmit because they are stored in the send window.

Keepalive responses are suppressed when any channel has pending data:
- Pending data is sent as segments; keepalive is only sent when idle.
- With invariant response caps, per-request caps do not block data or
  retransmits.

## Instrumentation Events (Bob)

Key structured events emitted during opportunistic retransmits:
- `tunnel.retransmit_skip`: cooldown skips with age, cooldown, poll EWMA, and
  unacked context.
- `tunnel.retransmit`: retransmit send details (seq, ack/sack, flags, bytes,
  previous send age/count, first-send age).
- `tunnel.ack_detail`: ACK/SACK processing detail and window snapshots.
- `tunnel.reliability_state`: structured snapshot of send/recv window and
  counters when drops or gating occur.

## ACK/SACK Effects On Retransmit Eligibility

Bob processes ACK/SACK on every valid request:
- `send_window.process_ack_with_progress(ack, sack)` removes acked packets from
  the unacked set (cumulative ACK and SACK) while updating ACK tracking.
- Once removed, a packet is no longer eligible for retransmit.
- ACK advances update `send_window.last_cum_ack_time`, but Bob does not use
  ACK-progress timing for retransmit gating.

RTT samples are computed for first-transmission packets in `send_window`, but
Bob does not use them for retransmit decisions.

## Potential Enhancements

- ACK regression guard is implemented in `SendWindow`: cumulative ACK tracking
  only updates when ACK advances in sequence space (or when unset). This
  prevents stale polls from skewing the window-distance check, which matters
  with pipelined polls.

## Sequence Number And Window Semantics

Retransmits reuse the original sequence number:
- No new sequence number is allocated.
- The number of unacked packets does not increase.
- The send window capacity remains bounded by `max_in_flight` (<= 256).

`send_time` is updated only by `mark_retransmit()`, so cooldown age is always
relative to the most recent transmission of that packet.
`first_send_time` is set on the initial send and stays fixed, so oldest
selection reflects the original send order.

## Logging And Stats Summary

Relevant events emitted by Bob retransmit logic:
- `tunnel.retransmit`: emitted for each retransmit; includes `seq`, `seg_count`,
  and optional `reason` (`window_full`, `window_distance`).
- `tunnel.retransmit_skip`: emitted when a retransmit is skipped due to
  cooldown.
- `tunnel.packet_send`: emitted for every retransmit send.

Stats:
- `send_window.mark_retransmit()` increments per-packet and global retransmit
  counters and updates reliability stats when enabled.

## Config Knobs (Bob Retransmit)

Key configuration fields that affect Bob retransmit behavior:
- `tunnel_bob_retransmit_min_interval`: baseline cooldown in seconds.
- `tunnel_bob_retransmit_max_interval`: upper bound on cooldown (seconds).
- `tunnel_bob_retransmit_poll_factor`: multiplier for poll EWMA cooldown.
- `tunnel_bob_poll_ewma_alpha`: EWMA alpha for poll interval smoothing.
- `tunnel_bob_poll_interval` / `tunnel_bob_poll_interval_bg`: poll timeouts
  that shape the observed request cadence.
- `max_in_flight` and negotiated window size (cap retransmit backlog and set
  the poll-window cooldown floor).

No Bob-side RTT or RTO configuration exists; retransmits are opportunity-driven.
