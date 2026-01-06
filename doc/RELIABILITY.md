# Reliability Layer

This layer provides reliable, ordered delivery of packets over unreliable
transports. It sits above the transport abstraction and below the channel
muxer. It is independent of the underlying transport (DNS, ICMP, etc).

---

## Goals

- Deliver packets in order.
- Detect loss and retransmit.
- Tolerate packet reordering and duplication.

---

## Sequence Numbers and ACKs

- `seq` is a 16-bit sequence number for each packet sent.
- `ack` is the next expected sequence number from the peer.
- `sack` is a 256-bit bitmap of packets received beyond `ack`.

Sequence numbers are 16-bit and wrap from 65535 to 0. All comparisons use
modular arithmetic.

ACK behavior:
- When a packet is received and accepted, update `ack` to the highest contiguous
  sequence number plus 1.
- If a packet is received out of order, set the corresponding `sack` bit.
- Duplicates are ignored.

---

## Receive Path

1. Parse the header, then decrypt the body (if crypto is enabled).
2. Decode segments and validate flags. Decode failures are logged and dropped.
   Invalid content-flag packets are treated as protocol violations and close the tunnel.
3. If the packet is a duplicate or outside the SACK window, drop it.
4. If in order, deliver to the next layer and advance `ack`.
5. If out of order, buffer it and set the SACK bit.
6. When gaps are filled, release buffered packets in order.

### Buffer Limits

The out-of-order buffer is bounded by the local max_in_flight proposal
(min(config.max_in_flight, 256)) and is not resized during window negotiation.
Because the negotiated send window is clamped to each side's proposal, the
receiver's buffer capacity always matches or exceeds the sender's window.
Buffer overflow should not occur under normal operation.

If a packet arrives that would exceed the buffer (e.g., due to implementation
mismatch or misbehaving peer), drop the incoming packet silently. Do not drop
buffered packets, as this would create unrecoverable gaps. The sender will
retransmit dropped packets once they reach the RTO (Alice may also fast
retransmit earlier when SACK indicates a hole), and SACK ensures already acked
packets are skipped during retransmit scans.

---

## Send Path

- Maintain a transmit queue of unacked packets.
- The sender may have up to `max_in_flight` unacked packets outstanding (see
  Window Negotiation in PROTOCOL.md). Maximum value is 256.
- On ACK or SACK, remove acknowledged packets from the queue.
- Send window tracking records cumulative ACK progress (value/time and last
  progress time) for retransmit gating and diagnostics.
- Retransmission is asymmetric: Alice retransmits on RTO (and may fast
  retransmit earlier when SACK progress indicates a missing cumulative ACK
  hole); Bob retransmits opportunistically when polled and cooldown allows.
  Retransmits reuse an existing sequence number and do not create new
  outstanding slots.

---

## RTT and Retransmission (Alice only)

RTT estimation uses an exponentially weighted moving average:

```
srtt = 0.875 * srtt + 0.125 * sample
rto = srtt * 2
rto = clamp(rto, 500ms, 10s)
```

**Initialization:** Before the first RTT sample, use rto = 1000ms. After the
first sample, set srtt = sample directly (no smoothing on first measurement).
These values are configurable via protocol_initial_rto_ms,
protocol_min_rto_ms, and protocol_max_rto_ms (defaults 1000/500/10000ms).

**Karn's Algorithm:** Do not sample RTT from retransmitted packets. The ack
could be for the original or the retransmit, making the sample ambiguous.

RTT samples are only taken when the response carries `HAS_SEGMENTS`.
`WANTS_POLL` and `KEEPALIVE` responses do not produce RTT samples or reset
backoff.
When sampling is enabled (`HAS_SEGMENTS` response), RTT samples are taken from
newly acked first-transmission packets; `KEEPALIVE` packets are excluded, but
`WANTS_POLL` packets can be sampled if they are acked by a `HAS_SEGMENTS`
response.

**RTO Backoff:** Double the RTO on each consecutive retransmit (up to 10s max).
Reset after receiving a valid RTT sample.

Bob does not track RTT; his effective round-trip is dominated by Alice's
polling interval.

### Retransmit Window Rules

Retransmits reuse an existing in-flight sequence number. The total number of
unacked packets remains capped at max_in_flight, ensuring the SACK bitmap
always covers the entire outstanding set. Retransmits are always allowed even
when the window is full, avoiding deadlock during loss recovery. Retransmits
are still limited by the transport in-flight cap (max_in_flight), which
bounds how many polls can be outstanding.

---

## Flow Control (Natural Throttling)

There is no explicit receive window. Flow control is implicit:

- **Bob is throttled by Alice's query rate.** Bob can only send one packet per
  query. If Alice slows her polling, Bob's throughput decreases automatically.
- **Alice is throttled by max_in_flight.** She cannot have more than
  max_in_flight packets outstanding. If she sends faster than Bob can ack,
  she blocks until acks arrive.

If either side's processing slows, acks are delayed, the sender's window fills,
and the sender naturally pauses.

---

## Keepalives and Poll Hints

`HAS_SEGMENTS` packets carry data segments. `WANTS_POLL` packets are empty
responses that request another poll soon. `KEEPALIVE` packets are empty,
idle responses. All three participate in seq/ack like any other packet.

Alice sends keepalive polls only when no channel has data to transmit. Bob
responds with `KEEPALIVE` only when no channel has data to transmit; if data
is queued but nothing fits, he responds with `WANTS_POLL` instead.

Handshake packets must not set content flags. `WANTS_POLL` and `KEEPALIVE`
must contain zero segments; `HAS_SEGMENTS` must contain at least one segment.
Violations are protocol errors.

---

## Notes

- This layer does not provide integrity; malformed packets are dropped.
- Only one connection is active at a time, so no session identifiers are used.
- Reliability stats counters are collected only when verbose logging (`-v`) is enabled.
